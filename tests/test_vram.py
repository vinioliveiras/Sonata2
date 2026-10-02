"""Graphics memory watch (sonata2/vram.py). Vini: WhatsApp's web app filled
the NVIDIA card's memory and the session went down with no word.
Run: python3 -m unittest tests.test_vram"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from sonata2 import vram  # noqa: E402

TABLE = """\
+-----------------------------------------------------------------------------------------+
| Processes:                                                                              |
|  GPU   GI   CI              PID   Type   Process name                        GPU Memory |
|=========================================================================================|
|    0   N/A  N/A            2150      G   wayfire                                 310MiB |
|    0   N/A  N/A           77879      G   /usr/bin/spider                        6900MiB |
|    0   N/A  N/A           68907    C+G   ...opt/google/chrome/chrome             420MiB |
+-----------------------------------------------------------------------------------------+
"""


class FakeUsage:
    tool, devices = "/usr/bin/nvidia-smi", ["/sys/x"]

    def awake(self):
        return True


def settle(ms=50):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class VramTest(unittest.TestCase):
    def watch(self, used):
        self.sent = []
        out = {True: f"{used}, 8188\n", False: TABLE}
        return vram.VramWatch(notify=lambda s, b: self.sent.append((s, b)) or True, usage=FakeUsage(),
                              run=lambda args: out[bool(args)])

    def test_parsing(self):
        self.assertEqual(vram.parse_usage("7100, 8188\n"), (7100, 8188))
        self.assertIsNone(vram.parse_usage("[N/A], [N/A]"))
        procs = vram.parse_processes(TABLE)
        self.assertEqual([p[0] for p in procs], [77879, 68907, 2150])        # biggest first
        self.assertEqual(procs[0][1:], ("spider", 6900))

    def test_quiet_below_the_line(self):
        w = self.watch(4000)
        self.assertIsNone(w.check())
        settle()
        self.assertEqual(self.sent, [])

    def test_warns_once_names_the_app_and_logs(self):
        w = self.watch(7600)
        with mock.patch.object(vram, "app_name", side_effect=lambda pid, n: n):
            self.assertIsNotNone(w.check())
            w.check()
        settle()
        self.assertEqual(len(self.sent), 1)                                  # once, not every 15 s
        self.assertIn("spider is using 6.7 GB", self.sent[0][1])
        with open(vram.log_path(), encoding="utf-8") as f:
            self.assertIn("spider (77879) 6900 MiB", f.read())

    def test_warns_again_after_it_went_down(self):
        w = self.watch(7600)
        with mock.patch.object(vram, "app_name", side_effect=lambda pid, n: n):
            w.check()
            w.run = lambda args: "3000, 8188" if args else TABLE
            w.check()
            w.run = lambda args: "7600, 8188" if args else TABLE
            w.check()
        settle()
        self.assertEqual(len(self.sent), 2)

    def test_never_wakes_a_sleeping_card(self):
        w = self.watch(7600)
        w.usage.awake = lambda: False
        with mock.patch("threading.Thread") as t:
            w._tick()
        t.assert_not_called()

    def test_started_by_the_menu_bar(self):
        src = open(os.path.join(os.path.dirname(vram.__file__), "shell", "topbar.py")).read()
        self.assertIn("VramWatch(notify=gpu.notify)", src)


if __name__ == "__main__":
    unittest.main()
