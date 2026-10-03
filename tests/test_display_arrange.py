"""Settings > Displays: choose the main display and arrange the displays
(Vini): drag a display (it snaps edge to edge and glides into place), drag
the menu bar to another display to make it the main one."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2.backend import system  # noqa: E402
from sonata2.settings import arrange as A  # noqa: E402

Adw.init()

WLR = '''eDP-1 "BOE 0x0BC9 (eDP-1)"
  Enabled: yes
  Modes:
    1920x1080 px, 144.003006 Hz (preferred, current)
    1920x1080 px, 60.000000 Hz
  Position: 0,180
  Transform: normal
  Scale: 1.250000
HDMI-A-1 "LG Electronics LG ULTRAGEAR 0x00066A07 (HDMI-A-1)"
  Enabled: yes
  Modes:
    1920x1080 px, 180.000000 Hz (current)
  Position: 1536,0
  Transform: 90
  Scale: 1.000000
'''


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class ParseTest(unittest.TestCase):
    def test_position_enabled_rotation_and_logical_size(self):
        with mock.patch.object(system, "_run", return_value=(0, WLR)):
            edp, hdmi = system.displays()
        self.assertEqual((edp.x, edp.y, edp.enabled), (0, 180, True))
        self.assertEqual(edp.size, (1536, 864))                 # the mode over its scale
        self.assertEqual(hdmi.size, (1080, 1920))               # turned 90°
        self.assertEqual((hdmi.x, hdmi.y), (1536, 0))

    def test_positions_go_to_wayfire(self):
        with mock.patch.object(system, "wayfire_set", return_value=True) as ws:
            self.assertTrue(system.set_display_positions({"eDP-1": (0, 0), "HDMI-A-1": (1920, 0)}))
        ws.assert_any_call("output:HDMI-A-1", "position", "1920,0")


class SnapTest(unittest.TestCase):
    R = {"a": (0, 0, 1920, 1080), "b": (1920, 0, 1920, 1080)}

    def test_dropped_on_the_left_lands_edge_to_edge(self):
        self.assertEqual(A.snap(self.R, "b", -1700, 40), (-1920, 40))

    def test_dropped_overlapping_moves_out_to_the_nearest_edge(self):
        x, y = A.snap(self.R, "b", 1000, 900)
        self.assertEqual((x, y), (1000, 1080))                  # under it, not over it

    def test_keeps_a_shared_edge(self):
        self.assertEqual(A.snap(self.R, "b", 1990, 600), (1920, 600))      # slid along the edge
        r = {"a": (0, 0, 1920, 1080), "b": (2500, 0, 1920, 1080)}
        self.assertEqual(A.snap(r, "b", 1950, 1060), (1920, 1080 - A.MIN_OVERLAP))   # touching by MIN_OVERLAP

    def test_never_overlaps_a_third(self):
        r = dict(self.R, c=(0, 1080, 1920, 1080))
        x, y = A.snap(r, "b", 10, 1000)
        bx = (x, x + 1920)
        for n in ("a", "c"):
            ox, oy, ow, oh = r[n]
            self.assertFalse(min(bx[1], ox + ow) - max(bx[0], ox) > 0 and min(y + 1080, oy + oh) - max(y, oy) > 0)

    def test_normalize(self):
        self.assertEqual(A.normalize({"a": (0, 0, 10, 10), "b": (-10, 5, 10, 10)}),
                         {"a": (10, 0, 10, 10), "b": (0, 5, 10, 10)})


class ArrangementTest(unittest.TestCase):
    def make(self):
        with mock.patch.object(system, "_run", return_value=(0, WLR)):
            ds = system.displays()
        moved, mains = [], []
        art = A.Arrangement(ds, "eDP-1", moved.append, mains.append)
        win = Gtk.Window(default_width=500)
        win.set_child(art)
        win.present()
        settle(100)
        self.addCleanup(win.destroy)
        return art, moved, mains

    def widget_center(self, art, name):
        x, y, w, h = art._widget_rect(name)
        return x + w / 2, y + h / 2

    def test_drag_a_display_to_the_other_side(self):
        art, moved, _ = self.make()
        cx, cy = self.widget_center(art, "HDMI-A-1")
        art._begin(None, cx, cy)
        s = art.drag["fit"][0]
        art._update(None, -(1536 + 1080) * s, 0)                 # to the left of the laptop
        art._end(None, 0, 0)
        self.assertEqual(len(moved), 1)
        pos = moved[0]
        self.assertEqual(pos["HDMI-A-1"][0], 0)                  # the layout starts at 0,0 again
        self.assertEqual(pos["eDP-1"][0], 1080)                  # the laptop now right of it
        self.assertTrue(art.shown)                               # gliding into place
        settle(400)
        self.assertEqual(art.shown, {})

    def test_drag_the_menu_bar_to_choose_the_main_display(self):
        art, moved, mains = self.make()
        x, y, w, h = art._widget_rect("eDP-1")
        self.assertEqual(art.hit(x + w / 2, y + 2), ("bar", "eDP-1"))
        art._begin(None, x + w / 2, y + 2)
        tx, ty = self.widget_center(art, "HDMI-A-1")
        art._update(None, tx - (x + w / 2), ty - (y + 2))
        art._end(None, 0, 0)
        self.assertEqual(mains, ["HDMI-A-1"])
        self.assertEqual(art.main, "HDMI-A-1")
        self.assertEqual(moved, [])                              # nothing moved

    def test_a_click_moves_nothing(self):
        art, moved, mains = self.make()
        cx, cy = self.widget_center(art, "HDMI-A-1")
        art._begin(None, cx, cy)
        art._end(None, 0, 0)
        self.assertEqual((moved, mains), ([], []))


if __name__ == "__main__":
    unittest.main()


class SettingsPageTest(unittest.TestCase):
    def test_displays_page_has_arrange_and_main(self):
        import tempfile
        from sonata2 import config
        from sonata2.settings import app as S
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()), \
                mock.patch.object(system, "_run", return_value=(0, WLR)), \
                mock.patch.object(system, "brightness", return_value=None), \
                mock.patch.object(system, "display_mode_setting", return_value="highrr"), \
                mock.patch.object(system, "run_async", side_effect=lambda fn, cb, *a: cb(fn(*a)) if cb else fn(*a)), \
                mock.patch.object(A, "wallpaper_thumb", return_value=None), \
                mock.patch.object(system, "set_display_positions") as setpos:
            win = S.Settings.__new__(S.Settings)
            win._latest = lambda *a: None
            S.Settings._page_displays(win)
            art = win.arrangement
            self.assertEqual(set(art.rects), {"eDP-1", "HDMI-A-1"})
            art.on_main("HDMI-A-1")
            self.assertEqual(config.load("displays", {"main": ""})["main"], "HDMI-A-1")
            art.on_move({"eDP-1": (0, 0), "HDMI-A-1": (1536, 0)})
            setpos.assert_called_once_with({"eDP-1": (0, 0), "HDMI-A-1": (1536, 0)})


class LoginScreenMainTest(unittest.TestCase):
    """Vini: with the Acer chosen as the main display, the login still came up on
    the laptop -- the login screen runs before the session and can't read
    ~/.config: the session shares the choice with it, like the wallpaper."""

    def test_shared_and_read_back(self):
        import os
        import tempfile
        from sonata2 import config
        from sonata2.shell import monitors as M
        root = tempfile.mkdtemp()
        user = GLib.get_user_name()
        os.makedirs(os.path.join(root, user))
        with mock.patch.object(M, "GREETER", root), mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()):
            config.save("displays", {"main": "HDMI-A-1"})
            M.share_with_login_screen()
            self.assertEqual(M.login_main(["nobody", user]), "HDMI-A-1")
            self.assertEqual(M.login_main(["nobody"]), "")
            hdmi, edp = mock.Mock(), mock.Mock()
            hdmi.get_connector.return_value, edp.get_connector.return_value = "HDMI-A-1", "eDP-1"
            with mock.patch.object(M, "_list", return_value=[edp, hdmi]):
                self.assertIs(M.main(M.login_main([user])), hdmi)

    def test_no_folder_no_error(self):
        from sonata2.shell import monitors as M
        with mock.patch.object(M, "GREETER", "/nonexistent"):
            M.share_with_login_screen()                           # nothing to do, nothing raised
