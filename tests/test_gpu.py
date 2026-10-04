"""Discrete GPU launches: an app marked for it starts with the GPU's
environment (python3 -m unittest tests.test_gpu)."""
import os
import tempfile
import time
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2 import apps, config, gpu  # noqa: E402

config.CONFIG_DIR = os.path.join(os.environ["XDG_CONFIG_HOME"], "sonata2")


class GpuTest(unittest.TestCase):
    @staticmethod
    def _sys(*cards):
        """A fake /sys: cards = (pci address, driver, VRAM bytes)."""
        root = tempfile.mkdtemp()
        drm = os.path.join(root, "class/drm")
        os.makedirs(drm)
        for i, (addr, drv, vram) in enumerate(cards):
            dev = os.path.join(root, "devices", addr)
            os.makedirs(dev)
            os.makedirs(os.path.join(root, "drivers", drv), exist_ok=True)
            os.symlink(os.path.join(root, "drivers", drv), os.path.join(dev, "driver"))
            if vram:
                with open(os.path.join(dev, "mem_info_vram_total"), "w") as f:
                    f.write(str(vram))
            os.makedirs(os.path.join(drm, f"card{i}"))
            os.symlink(dev, os.path.join(drm, f"card{i}", "device"))
            os.makedirs(os.path.join(drm, f"card{i}-HDMI-A-1"))       # a connector: skipped
        return root

    def _best(self, *cards, told=None):
        cs = gpu.scan_cards(self._sys(*cards), told={} if told is None else told)
        self.assertEqual(len(cs), len(cards))
        return gpu.best_discrete(cs)

    def test_high_performance_card(self):
        """MUX in dGPU mode: switcheroo's non-default GPU was the integrated
        one and Steam's games went there (Vini). Each machine kind:"""
        G = 1 << 30
        apu, nv = ("0000:36:00.0", "amdgpu", 512 << 20), ("0000:01:00.0", "nvidia", 0)
        self.assertEqual(self._best(apu, nv).driver, "nvidia")                      # AMD APU + NVIDIA
        self.assertEqual(self._best(apu, ("0000:03:00.0", "amdgpu", 8 * G)).addr, "0000:03:00.0")  # APU + Radeon
        igpu = ("0000:00:02.0", "i915", 0)
        self.assertEqual(self._best(igpu, ("0000:03:00.0", "amdgpu", 8 * G)).driver, "amdgpu")   # Intel + Radeon
        self.assertEqual(self._best(igpu, ("0000:04:00.0", "xe", 0)).addr, "0000:04:00.0")       # Intel + Arc
        self.assertEqual(self._best(igpu, nv).driver, "nvidia")                                  # Intel + NVIDIA
        self.assertIsNone(self._best(apu))                                                       # one APU
        # switcheroo's "Discrete" wins over the guess
        self.assertEqual(self._best(apu, ("0000:03:00.0", "amdgpu", 256 << 20),
                                    told={"pci-0000_36_00_0": False, "pci-0000_03_00_0": True}).addr,
                         "0000:03:00.0")

    def test_env_for_card(self):
        from unittest import mock
        nv = gpu.Card("pci-0000_01_00_0", "0000:01:00.0", "nvidia", True, 0)
        amd = gpu.Card("pci-0000_36_00_0", "0000:36:00.0", "amdgpu", False, 0)
        with mock.patch.object(gpu, "_card_list", [nv, amd]):
            self.assertEqual(gpu.env_for(nv)["__NV_PRIME_RENDER_OFFLOAD"], "1")
            self.assertNotIn("DRI_PRIME", gpu.env_for(nv))
            self.assertEqual(gpu.env_for(amd)["DRI_PRIME"], "pci-0000_36_00_0")
            # Settings can force either kind of app onto a card
            gpu.set_card("games", amd.tag)
            self.assertEqual(gpu.high_performance(), amd)
            gpu.set_card("games", "")
            self.assertEqual(gpu.high_performance(), nv)
            self.assertIsNone(gpu.everyday())
            gpu.set_card("apps", nv.tag)
            self.assertEqual(gpu.everyday(), nv)
            gpu.set_card("apps", "")

    def test_settings_card_rows(self):
        """Settings > Displays > Graphics: two pickers, Automatic named after Sonata's pick."""
        from unittest import mock
        import gi
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from sonata2.settings import app as settings
        nv = gpu.Card("pci-0000_01_00_0", "0000:01:00.0", "nvidia", True, 0)
        amd = gpu.Card("pci-0000_36_00_0", "0000:36:00.0", "amdgpu", False, 0)
        g = settings.group("Graphics")
        with mock.patch.object(gpu, "_card_list", [nv, amd]), \
                mock.patch.object(gpu, "card_name", lambda c: "RTX" if c is nv else "680M"):
            settings.Settings._gpu_card_rows(g, gpu)
            rows = []
            w = g.get_first_child()
            stack = [w]
            while stack:
                w = stack.pop()
                if w is None:
                    continue
                if isinstance(w, Adw.ComboRow):
                    rows.append(w)
                stack += [w.get_first_child(), w.get_next_sibling()]
            self.assertEqual(sorted(r.get_title() for r in rows), ["Graphics for Apps", "Graphics for Games"])
            games = next(r for r in rows if r.get_title() == "Graphics for Games")
            self.assertEqual(games.get_model().get_string(0), "Automatic (RTX)")
            games.set_selected(2)                                  # the 680M, forced
            self.assertEqual(gpu.high_performance(), amd)
            games.set_selected(0)
            self.assertEqual(gpu.high_performance(), nv)

    def test_launch_env(self):
        d = tempfile.mkdtemp()
        out = os.path.join(d, "env.txt")
        path = os.path.join(d, "gputest.desktop")
        with open(path, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=T\nExec=sh -c 'env > {out}'\n")
        info = apps.DesktopAppInfo.new_from_filename(path)
        gpu._env = {"SONATA_TEST_GPU": "dGPU"}
        self.assertFalse(gpu.wants_discrete(info))
        gpu.set_discrete(info, True)
        self.assertTrue(gpu.wants_discrete(info))
        info.launch([], None)
        for _ in range(50):
            if os.path.exists(out) and os.path.getsize(out):
                break
            time.sleep(0.1)
        with open(out) as f:
            self.assertIn("SONATA_TEST_GPU=dGPU", f.read())
        gpu.set_discrete(info, False)
        self.assertFalse(gpu.wants_discrete(info))


if __name__ == "__main__":
    unittest.main()


class MenuNameTest(unittest.TestCase):
    def test_plain_name(self):
        """Vini: "Use Discrete Graphics" didn't say it meant the stronger
        (NVIDIA) card."""
        from unittest import mock
        Item = mock.Mock()
        info = mock.Mock()
        info.get_id.return_value = "x.desktop"
        info.has_key.return_value = False
        with mock.patch.object(gpu, "has_dual_gpu", return_value=True):
            gpu.menu_items(info, Item)
        self.assertIn("Use High-Performance Graphics", [c[0][0] for c in Item.call_args_list])


class NotifyOnceTest(unittest.TestCase):
    """Vini: the same notice four times at login -- notify() waited for the
    reply of a server living in the same process, timed out, and the notice
    was sent again."""

    def test_sent_without_waiting(self):
        from unittest import mock
        bus = mock.Mock()
        owned = mock.Mock()
        owned.unpack.return_value = (True,)
        bus.call_sync.return_value = owned
        with mock.patch("gi.repository.Gio.bus_get_sync", return_value=bus):
            self.assertTrue(gpu.notify("a", "b"))
        self.assertEqual(bus.call_sync.call_args[0][3], "NameHasOwner")       # only that waits
        self.assertEqual(bus.call.call_args[0][3], "Notify")                  # sent, not awaited

    def test_no_server_yet(self):
        from unittest import mock
        bus = mock.Mock()
        owned = mock.Mock()
        owned.unpack.return_value = (False,)
        bus.call_sync.return_value = owned
        with mock.patch("gi.repository.Gio.bus_get_sync", return_value=bus):
            self.assertFalse(gpu.notify("a", "b"))
        bus.call.assert_not_called()
