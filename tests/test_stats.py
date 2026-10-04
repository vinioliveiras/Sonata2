"""CPU, GPU, memory, network and FPS for the menu bar and Control Center
(Vini): off by default; read only while shown; FPS from Sonata's Wayfire
plugin (commits of the app in front per second)."""
import os
import pathlib
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config  # noqa: E402
from sonata2.backend import stats as S  # noqa: E402

Adw.init()


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeRoot:
    def __init__(self):
        self.root = tempfile.mkdtemp()
        self.proc = os.path.join(self.root, "proc")
        self.sys = os.path.join(self.root, "sys")
        os.makedirs(os.path.join(self.proc, "net"))
        os.makedirs(os.path.join(self.sys, "class/drm/card1/device"))

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w") as f:
            f.write(text)


NETDEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: {lo} 10 0 0 0 0 0 0 {lo} 10 0 0 0 0 0 0
  wlan0: {rx} 100 0 0 0 0 0 0 {tx} 50 0 0 0 0 0 0
"""


class SamplerTest(unittest.TestCase):
    def setUp(self):
        self.f = FakeRoot()
        self.s = S.Sampler(self.f.proc, self.f.sys, ipc=mock.Mock())

    def test_cpu_percent_between_two_reads(self):
        self.f.write("proc/stat", "cpu  100 0 100 800 0 0 0 0\n")
        self.assertEqual(self.s.cpu(), 0.0)                      # first read: nothing to compare yet
        self.f.write("proc/stat", "cpu  200 0 150 850 0 0 0 0\n")  # 150 busy of 200
        self.assertEqual(self.s.cpu(), 75.0)

    def test_memory_used_and_total(self):
        self.f.write("proc/meminfo", "MemTotal: 1000 kB\nMemAvailable: 250 kB\nMemFree: 100 kB\n")
        self.assertEqual(self.s.ram(), (750 * 1024, 1000 * 1024))

    def test_network_rate_without_loopback(self):
        self.f.write("proc/net/dev", NETDEV.format(lo=999999, rx=1000, tx=500))
        self.assertEqual(self.s.net(1.0), (0.0, 0.0))
        self.f.write("proc/net/dev", NETDEV.format(lo=99999999, rx=3048, tx=1524))
        self.assertEqual(self.s.net(2.0), (1024.0, 512.0))         # loopback traffic never counts

    def card(self, n, driver):
        d = os.path.join(self.f.sys, f"class/drm/card{n}/device")
        os.makedirs(d, exist_ok=True)
        drv = os.path.join(self.f.root, "drivers", driver)
        os.makedirs(drv, exist_ok=True)
        os.symlink(drv, os.path.join(d, "driver"))
        return d

    def no_nvidia_tool(self):
        return mock.patch.object(S.procfs.NvidiaUsage, "__init__",
                                 lambda nv, sys="": setattr(nv, "tool", None) or setattr(nv, "devices", []))

    def test_gpu_from_amdgpu_busy_percent(self):
        self.card(1, "amdgpu")
        self.f.write("sys/class/drm/card1/device/gpu_busy_percent", "37\n")
        with self.no_nvidia_tool():
            self.assertEqual(self.s.gpus(), {"amd": 37.0})
            self.assertEqual(self.s.gpu(), 37.0)

    def test_no_gpu_reading(self):
        with self.no_nvidia_tool():
            self.assertIsNone(self.s.gpu())

    def test_each_card_on_its_own(self):
        """Vini: with two cards, one figure each (AMD from sysfs, NVIDIA from nvidia-smi)."""
        self.card(0, "amdgpu")
        self.card(1, "nvidia")
        os.makedirs(os.path.join(self.f.sys, "class/drm/card1-HDMI-A-1"))       # a connector: no card
        self.f.write("sys/class/drm/card0/device/gpu_busy_percent", "12\n")
        self.assertEqual([k for k, _m, _c in S.gpu_cards(self.f.sys)], ["amd", "nvidia"])
        nv = mock.Mock(tool="/usr/bin/nvidia-smi", devices=["x"])
        nv.awake.return_value = True
        self.s._nvidia = nv
        self.s._nv_list = [64.0]
        with mock.patch.object(self.s, "_nvidia_refresh"):
            self.assertEqual(self.s.gpus(), {"amd": 12.0, "nvidia": 64.0})
        nv.awake.return_value = False                              # asleep: never woken to ask
        with mock.patch.object(self.s, "_nvidia_refresh") as refresh:
            self.assertEqual(self.s.gpus(), {"amd": 12.0, "nvidia": None})
        refresh.assert_not_called()

    def test_video_memory_of_each_card(self):
        """Vini: VRAM per card -- AMD from sysfs, NVIDIA from the same nvidia-smi
        call as its busy %, none while it sleeps; Intel's iGPU has none."""
        self.card(0, "amdgpu")
        self.card(1, "nvidia")
        self.card(2, "i915")
        self.f.write("sys/class/drm/card0/device/mem_info_vram_used", str(256 << 20))
        self.f.write("sys/class/drm/card0/device/mem_info_vram_total", str(512 << 20))
        cards = S.gpu_cards(self.f.sys)
        self.assertEqual(S.vram_kinds(cards), {"vram_amd": "amd", "vram_nvidia": "nvidia"})
        self.assertEqual(S.vram_kinds(cards[1:]), {"vram": "nvidia"})                 # one card: "vram"
        self.assertIn("vram_nvidia", S.kinds(cards, cpu_temp=""))
        self.assertEqual(S.parse_nvidia("45, 3072, 8188, 61\n"), ([45.0], [(3072 << 20, 8188 << 20)], [61.0]))
        self.assertEqual(S.parse_nvidia("[N/A], 1, 2, 3\n"), ([], [], []))
        self.s._nv_mem = [(3 << 30, 8 << 30)]
        self.assertEqual(self.s.vrams(), {"amd": (256 << 20, 512 << 20), "nvidia": (3 << 30, 8 << 30)})
        self.s._nv_mem = []                                        # asleep
        self.assertIsNone(self.s.vrams()["nvidia"])
        r = S.Reading(vrams={"nvidia": (int(3.2 * (1 << 30)), 8 << 30), "amd": None})
        with mock.patch.object(S, "VRAM_KINDS", {"vram_nvidia": "nvidia", "vram_amd": "amd"}), \
                mock.patch.object(S, "VRAM_MAKERS_BY_KIND", {"vram_nvidia": "NVIDIA", "vram_amd": "AMD"}):
            self.assertEqual(S.text("vram_nvidia", r), "NVIDIA VRAM 3.2 GB")
            self.assertEqual(S.text("vram_amd", r), "AMD VRAM –")
        self.assertEqual(S.vram_text(r, "nvidia"), "3.2 / 8.0 GB")
        self.assertAlmostEqual(S.vram_pct(r, "nvidia"), 40.0, places=3)

    def test_temperatures(self):
        """Vini: temperatures like CPU/GPU use -- the CPU's chip sensor
        (k10temp Tctl, not a single core), AMD's hwmon, NVIDIA from nvidia-smi."""
        hw = os.path.join(self.f.sys, "class/hwmon")
        for n, name, files in ((0, "acpitz", {"temp1_input": "30000"}),
                               (1, "k10temp", {"temp1_input": "61500", "temp1_label": "Tctl",
                                               "temp3_input": "50000", "temp3_label": "Tccd1"})):
            d = os.path.join(hw, f"hwmon{n}")
            os.makedirs(d)
            self.f.write(f"sys/class/hwmon/hwmon{n}/name", name + "\n")
            for k, v in files.items():
                self.f.write(f"sys/class/hwmon/hwmon{n}/{k}", v + "\n")
        self.assertEqual(S.cpu_temp_path(self.f.sys), os.path.join(hw, "hwmon1", "temp1_input"))
        self.assertEqual(self.s.cpu_temp(), 61.5)
        self.assertIsNone(S.Sampler(self.f.proc, os.path.join(self.f.root, "none")).cpu_temp())   # no sensor
        d = self.card(0, "amdgpu")
        os.makedirs(os.path.join(d, "hwmon/hwmon5"))
        self.f.write("sys/class/drm/card0/device/hwmon/hwmon5/temp1_input", "48000\n")
        self.card(1, "nvidia")
        self.s._nv_temp = [55.0]
        self.assertEqual(self.s.temps(), {"amd": 48.0, "nvidia": 55.0})
        self.s._nv_temp = []                                       # asleep: never woken
        self.assertIsNone(self.s.temps()["nvidia"])
        r = S.Reading(cpu_temp=61.5, temps={"nvidia": 55.0, "amd": None})
        self.assertEqual(S.text("temp_cpu", r), "CPU 62°C")
        with mock.patch.object(S, "TEMP_KINDS", {"temp_nvidia": "nvidia", "temp_amd": "amd"}), \
                mock.patch.object(S, "TEMP_MAKERS_BY_KIND", {"temp_nvidia": "NVIDIA", "temp_amd": "AMD"}):
            self.assertEqual(S.text("temp_nvidia", r), "NVIDIA 55°C")
            self.assertEqual(S.text("temp_amd", r), "AMD –")

    def test_fps_states(self):
        ipc = self.s._ipc
        ipc.call.return_value = {"result": "ok", "fps": 143, "ready": True, "app-id": "steam_app_1", "fullscreen": True}
        self.assertEqual(self.s.fps(), (143, "ok"))
        ipc.call.return_value = {"result": "ok", "fps": 20, "ready": False, "app-id": "x"}
        self.assertEqual(self.s.fps(), (None, "starting"))         # less than a second counted
        ipc.call.return_value = {"result": "ok", "fps": 0, "ready": False, "app-id": ""}
        self.assertEqual(self.s.fps(), (None, "no-app"))
        ipc.call.return_value = {"error": "No such method found!"}
        self.assertEqual(self.s.fps(), (None, "no-plugin"))        # plugin not rebuilt: said, not faked
        ipc.call.return_value = None
        self.assertEqual(self.s.fps()[1], "no-plugin")


class StatsTest(unittest.TestCase):
    def test_reads_only_while_someone_watches(self):
        st = S.Stats(sampler=mock.Mock())
        st.read = mock.Mock(return_value=S.Reading(cpu=10))
        with mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb, *a: cb(fn(*a))):
            got = []
            cb = got.append
            st.subscribe(cb)
            self.assertTrue(st._timer)
            self.assertEqual(got[-1].cpu, 10)
            st.unsubscribe(cb)
            self.assertFalse(st._timer)                            # nobody looks: no more reads
            n = st.read.call_count
            settle(1200)
            self.assertEqual(st.read.call_count, n)

    def test_history_kept_short(self):
        st = S.Stats(sampler=mock.Mock())
        for i in range(S.HISTORY + 10):
            st._deliver(S.Reading(cpu=i))
        self.assertEqual(len(st.history["cpu"]), S.HISTORY)
        self.assertEqual(st.history["cpu"][-1], S.HISTORY + 9)


class KindsTest(unittest.TestCase):
    def test_one_gpu_option_or_one_per_card(self):
        self.assertEqual(S.kinds([("amd", "AMD", "card0")], cpu_temp=""),
                         ("cpu", "gpu", "vram", "temp_gpu", "ram", "net", "fps"))
        self.assertEqual(S.kinds([("intel", "Intel", "card0")], cpu_temp="x"),
                         ("cpu", "gpu", "temp_cpu", "ram", "net", "fps"))
        self.assertEqual(S.kinds([], cpu_temp=""), ("cpu", "gpu", "ram", "net", "fps"))
        self.assertEqual(S.kinds([("amd", "AMD", "card0"), ("nvidia", "NVIDIA", "card1")], cpu_temp="x"),
                         ("cpu", "gpu_amd", "gpu_nvidia", "vram_amd", "vram_nvidia", "temp_cpu", "temp_amd",
                          "temp_nvidia", "ram", "net", "fps"))

    def test_card_text_and_history(self):
        with mock.patch.dict(S.GPU_MAKERS, {"gpu_nvidia": "NVIDIA"}):
            r = S.Reading(gpus={"nvidia": 64.4, "amd": None})
            self.assertEqual(S.text("gpu_nvidia", r), "NVIDIA 64%")
            self.assertEqual(S.text("gpu_amd", r), "AMD –")
        st = S.Stats(sampler=mock.Mock())
        st._deliver(S.Reading(gpus={"nvidia": 50.0}))
        self.assertEqual(st.history["gpu_nvidia"], [50.0])


class TextTest(unittest.TestCase):
    def test_menu_bar_texts(self):
        r = S.Reading(cpu=12.4, gpu=None, ram_used=512, ram_total=1024, down=1536, up=3 * 1024 ** 2, fps=None)
        self.assertEqual(S.text("cpu", r), "CPU 12%")
        self.assertEqual(S.text("gpu", r), "GPU –")
        self.assertEqual(S.text("ram", r), "RAM 50%")
        self.assertEqual(S.text("net", r), "↓ 2 KB/s  ↑ 3.0 MB/s")
        self.assertEqual(S.text("fps", r), "– FPS")
        r.fps = 144
        self.assertEqual(S.text("fps", r), "144 FPS")


class WiringTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)

    def test_off_by_default_everywhere(self):
        from sonata2.shell import controlcenter as C, statsui, topbar as T
        for k in statsui.KINDS:
            self.assertFalse(T.DEFAULTS[f"show_{k}"])
            self.assertIn(f"stat_{k}", C.CATALOG)
            self.assertNotIn(f"stat_{k}", C.DEFAULT_ORDER)            # only through Add Controls
            self.assertIn(f"stat_{k}", C.hidden(C.DEFAULT_ORDER))

    def test_settings_off_text_graph(self):
        from sonata2.settings import app as A
        from sonata2.shell import topbar as T
        win = A.Settings.__new__(A.Settings)
        win._save = lambda name, key, value: config.update(name, **{key: value})
        groups = A.Settings._page_menubar(win)
        rows = {}

        def walk(w):
            if isinstance(w, Adw.ComboRow):
                rows[w.get_title()] = w
            c = w.get_first_child()
            while c is not None:
                walk(c)
                c = c.get_next_sibling()
        for g in groups:
            walk(g)
        row = rows["CPU"]
        self.assertEqual(row.values[row.get_selected()], "off")
        row.set_selected(row.values.index("graph"))
        cfg = config.load("topbar", T.DEFAULTS)
        self.assertTrue(cfg["show_cpu"])
        self.assertEqual(cfg["cpu_style"], "graph")
        row.set_selected(row.values.index("off"))
        self.assertFalse(config.load("topbar", T.DEFAULTS)["show_cpu"])

    def test_widgets_subscribe_only_when_shown(self):
        from sonata2.shell import statsui
        st = S.Stats(sampler=mock.Mock())
        st.read = mock.Mock(return_value=S.Reading(cpu=42, fps=None, fps_state="no-plugin"))
        with mock.patch.object(S.Stats, "_shared", st), \
                mock.patch("sonata2.backend.system.run_async", side_effect=lambda fn, cb, *a: cb(fn(*a))):
            item = statsui.menu_item("cpu", "text")
            mod = statsui.module("fps")
            self.assertEqual(st.listeners, [])                     # built, not shown: nothing read
            win = Gtk.Window()
            box = Gtk.Box()
            box.append(item)
            box.append(mod)
            win.set_child(box)
            win.present()
            settle(100)
            self.assertEqual(len(st.listeners), 2)
            label = item.get_first_child()
            self.assertEqual(label.get_label(), "CPU 42%")
            win.destroy()
            settle(50)
            self.assertEqual(st.listeners, [])

    def test_plugin_answers_sonata_fps(self):
        """The plugin counts commits of the focused view's surface, only while asked."""
        cpp = (pathlib.Path(__file__).resolve().parent.parent / "wayfire-plugin" / "src" /
               "sonata-corners.cpp").read_text()
        self.assertIn('register_method("sonata/fps"', cpp)
        self.assertIn('unregister_method("sonata/fps")', cpp)
        self.assertIn("events.commit", cpp)
        self.assertIn("events.destroy", cpp)                       # never a dangling listener
        self.assertIn("now - last_ask > 5000", cpp)


if __name__ == "__main__":
    unittest.main()
