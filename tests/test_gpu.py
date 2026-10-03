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
