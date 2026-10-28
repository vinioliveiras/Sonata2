"""Real drags (X11 + xdotool) in the Apps Menu, full-screen Apps and the
Dock: an app held over another makes a folder; an app dragged to another
place is reordered there. Vini: both failed in the Apps Menu -- a drop
target around the grids heard every motion too and answered "the end";
calling the handlers directly never showed it.
Run: xvfb-run -a python3 -m unittest tests.test_launchpad_real_dnd"""
import os
import shutil
import subprocess
import unittest

os.environ["GDK_BACKEND"] = "x11"

from gi.repository import GLib  # noqa: E402

from tests import fake_apps  # noqa: F401,E402
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



def real_drag(title, frm, to, hold=14):
    """Press at frm, move to `to` in steps, wiggle there, let go."""
    wid = subprocess.run(["xdotool", "search", "--name", title], capture_output=True, text=True).stdout.split()
    if not wid:
        return False
    w = wid[0]
    loop = GLib.MainLoop()
    (ax, ay), (tx, ty) = frm, to
    steps = [(ax + (tx - ax) * i // 10, ay + (ty - ay) * i // 10) for i in range(1, 11)] + \
        [(tx + i % 2, ty) for i in range(hold)]

    def step(i=[0]):
        if i[0] < len(steps):
            x, y = steps[i[0]]
            subprocess.run(["xdotool", "mousemove", "--window", w, str(x), str(y)])
            i[0] += 1
            return True
        subprocess.run(["xdotool", "mouseup", "1"])
        GLib.timeout_add(700, lambda: (loop.quit(), False)[1])
        return False

    def go():
        subprocess.run(["xdotool", "mousemove", "--window", w, str(ax), str(ay), "mousedown", "1"])
        GLib.timeout_add(60, step)
        return False
    GLib.timeout_add(300, go)
    GLib.timeout_add(12000, lambda: (loop.quit(), False)[1])
    loop.run()
    return True


def _mid(widget, win, dy=-10):
    ok, r = widget.compute_bounds(win)
    return int(r.get_x() + r.get_width() / 2), int(r.get_y() + r.get_height() / 2 + dy)


@unittest.skipUnless(shutil.which("xdotool") and os.environ.get("DISPLAY"), "needs xdotool and X")
class RealReorderTest(unittest.TestCase):
    """Vini: apps couldn't be reordered by dragging any more."""

    def test_apps_menu(self):
        T.MenuModeTest.setUpClass()
        t = T.MenuModeTest("test_arrow_keys_move_the_selection")
        t.setUp()
        try:
            pad = t.pad
            pad.set_title("LP-REORDER-MENU")
            pad.render()
            T.settle(300)
            a = pad.model.pages[0][0]
            grid = t.menu.pages[0]
            ok, gb = grid.compute_bounds(pad)
            cw, ch = gb.get_width() / grid.cols, gb.get_height() / grid.rows
            self.assertTrue(real_drag("LP-REORDER-MENU", _mid(pad._item_widget(a), pad),
                                      (int(gb.get_x() + cw * 3 + 3), int(gb.get_y() + ch / 2))))
            self.assertEqual(pad.model.pages[0].index(a), 3, pad.model.pages[0][:5])
        finally:
            t.tearDown()

    def test_apps_menu_out_of_a_folder_into_the_empty_room(self):
        """Out of an open folder, let go in the room under the apps: the end."""
        T.MenuModeTest.setUpClass()
        t = T.MenuModeTest("test_arrow_keys_move_the_selection")
        t.setUp()
        try:
            pad, menu = t.pad, t.menu
            pad.set_title("LP-ROOM")
            pad.model.make_folder("calc", "files", "Work")
            pad.save()
            pad.render()
            T.settle(200)
            pad.activate_item(next(x for x in menu.visible_items() if isinstance(x.item, dict)))
            T.settle(300)
            tile = pad.folder_view[2].get_first_child().get_child_at(0, 0)
            item = tile.item
            ok, rb = menu.room.compute_bounds(pad)
            ok, pb = pad.folder_view[0].compute_bounds(pad)
            self.assertGreater(rb.get_height(), 40)                     # there is a room to drop in
            to = (int(rb.get_x() + 20), int(max(rb.get_y() + 10, pb.get_y() + pb.get_height() + 20)))
            self.assertTrue(real_drag("LP-ROOM", _mid(tile, pad), to, 10))
            flat = [i for p in pad.model.pages for i in p]
            self.assertEqual(flat[-1], item)
        finally:
            t.tearDown()

    def test_full_screen_apps(self):
        from tests import test_launchpad as TL
        TL.LaunchpadTest.setUpClass()
        t = TL.LaunchpadTest("test_edge_flips_page")
        t.setUp()
        try:
            pad = t.win
            pad.set_title("LP-REORDER-FULL")
            T.settle(400)
            a = pad.model.pages[0][0]
            grid = pad.carousel.get_nth_page(0)
            ok, gb = grid.compute_bounds(pad)
            cw, ch = gb.get_width() / grid.cols, gb.get_height() / grid.rows
            self.assertTrue(real_drag("LP-REORDER-FULL", _mid(pad._item_widget(a), pad),
                                      (int(gb.get_x() + cw * 3 + 3), int(gb.get_y() + ch / 2))))
            self.assertEqual(pad.model.pages[0].index(a), 3, pad.model.pages[0][:5])
        finally:
            t.win.destroy()

    def test_dock(self):
        from gi.repository import Gtk
        from sonata2 import apps, config
        from sonata2.shell import dock as D
        config.save("dock", {"pinned": apps.default_pins()})          # a fresh Dock: the default apps
        cfg = D.load_config()
        D.load_css(cfg)
        win = Gtk.Window(title="DOCK-REORDER")
        dock = D.Dock(cfg)
        win.set_child(dock)
        win.present()
        T.settle(600)
        try:
            keys = [t.key for t in dock.app_tiles()]
            a, past = keys[1], dock.tiles[keys[4]]
            ok, bt = past.compute_bounds(win)
            frm = _mid(dock.tiles[a], win, 0)
            self.assertTrue(real_drag("DOCK-REORDER", frm, (int(bt.get_x() + bt.get_width() * 0.95), frm[1]), 10))
            after = [t.key for t in dock.app_tiles()]
            self.assertEqual(after.index(a), 4, after)
            self.assertEqual(D.load_config()["pinned"].index(a), keys.index(keys[4]))
        finally:
            win.destroy()

if __name__ == "__main__":
    unittest.main()
