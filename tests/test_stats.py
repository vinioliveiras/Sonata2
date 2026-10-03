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

    def test_gpu_from_amdgpu_busy_percent(self):
        self.f.write("sys/class/drm/card1/device/gpu_busy_percent", "37\n")
        with mock.patch.object(S.procfs.NvidiaUsage, "__init__", lambda nv, sys="": setattr(nv, "tool", None)
                               or setattr(nv, "devices", [])):
            self.assertEqual(self.s.gpu(), 37.0)

    def test_no_gpu_reading(self):
        with mock.patch.object(S.procfs.NvidiaUsage, "__init__", lambda nv, sys="": setattr(nv, "tool", None)
                               or setattr(nv, "devices", [])):
            self.assertIsNone(self.s.gpu())

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
