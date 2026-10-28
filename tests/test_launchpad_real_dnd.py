"""Apps Menu with a real drag (X11 + xdotool): an app held over another and
let go makes a folder. Vini: it failed -- a drop target around the grids
heard every motion too and undid the folder's target; calling the handlers
directly never showed it.
Run: xvfb-run -a python3 -m unittest tests.test_launchpad_real_dnd"""
import os
import shutil
import subprocess
import unittest

os.environ["GDK_BACKEND"] = "x11"

from gi.repository import GLib  # noqa: E402

from tests import test_launchpad_window as T  # noqa: E402


@unittest.skipUnless(shutil.which("xdotool") and os.environ.get("DISPLAY"), "needs xdotool and X")
class RealDragTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        T.MenuModeTest.setUpClass()

    def setUp(self):
        self.t = T.MenuModeTest("test_arrow_keys_move_the_selection")
        self.t.setUp()
        self.pad = self.t.pad

    def tearDown(self):
        self.t.tearDown()

    def test_drop_an_app_on_another_makes_a_folder(self):
        pad = self.pad
        pad.set_title("LP-REAL-DND")
        pad.render()
        T.settle(300)
        page = pad.model.pages[0]
        a, b = page[0], page[1]

        def centre(item):
            ok, r = pad._item_widget(item).compute_bounds(pad)
            return int(r.get_x() + r.get_width() / 2), int(r.get_y() + r.get_height() / 2 - 10)
        (ax, ay), (bx, by) = centre(a), centre(b)
        wid = subprocess.run(["xdotool", "search", "--name", "LP-REAL-DND"], capture_output=True,
                             text=True).stdout.split()
        self.assertTrue(wid)
        w = wid[0]
        loop = GLib.MainLoop()
        steps = [(ax + (bx - ax) * i // 10, ay) for i in range(1, 11)] + [(bx + i % 2, by) for i in range(14)]

        def step(i=[0]):
            if i[0] < len(steps):
                x, y = steps[i[0]]
                subprocess.run(["xdotool", "mousemove", "--window", w, str(x), str(y)])
                i[0] += 1
                return True
            subprocess.run(["xdotool", "mouseup", "1"])
            GLib.timeout_add(600, lambda: (loop.quit(), False)[1])
            return False

        def go():
            subprocess.run(["xdotool", "mousemove", "--window", w, str(ax), str(ay), "mousedown", "1"])
            GLib.timeout_add(60, step)
            return False
        GLib.timeout_add(300, go)
        GLib.timeout_add(10000, lambda: (loop.quit(), False)[1])
        loop.run()
        first = pad.model.pages[0][0]
        self.assertIsInstance(first, dict, pad.model.pages[0][:3])
        self.assertEqual(set(first["apps"]), {a, b})


if __name__ == "__main__":
    unittest.main()
