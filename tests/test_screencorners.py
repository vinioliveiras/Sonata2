"""Rounded screen corners (xvfb-run python3 -m unittest tests.test_screencorners)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Graphene, Gtk  # noqa: E402

from sonata2 import config, icons, ui  # noqa: E402
from sonata2.shell import screencorners as C  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class CornersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.setup()

    def test_none_over_a_full_screen_game(self):
        """Vini: frame drops and a laggy mouse in a full-screen game -- the
        corners kept Wayfire composing every frame. None on that display."""
        sc = C.ScreenCorners.__new__(C.ScreenCorners)
        sc.wins, sc.full_on = [], None
        a, b = mock.Mock(connector="HDMI-A-1"), mock.Mock(connector="eDP-1")
        sc.wins = [a, b]
        sc.fullscreen_on("HDMI-A-1")
        a.set_visible.assert_called_with(False)
        b.set_visible.assert_called_with(True)
        sc.fullscreen_on(None)
        a.set_visible.assert_called_with(True)
        src = open(os.path.join(os.path.dirname(__file__), "..", "sonata2", "shell", "topbar.py")).read()
        self.assertIn("c.fullscreen_on(f.output if full else None)", src)
        gm = open(os.path.join(os.path.dirname(__file__), "..", "sonata2", "gamemode.py")).read()
        self.assertIn('output = front.get("output-name") if full else None', gm)

    def test_on_by_default(self):
        self.assertTrue(icons.APPEARANCE_DEFAULTS["screen_corners"])
        config.save("appearance", {"theme": "mac"})
        self.assertTrue(C.enabled())
        self.assertNotIn("sonata2", C.NAMESPACE)          # Wayfire's blur rule must not blur behind them

    def test_corners_black_outside_the_arc(self):
        win = Gtk.Window(decorated=False)
        over = Gtk.Overlay()
        bg = Gtk.DrawingArea(hexpand=True, vexpand=True)
        bg.set_draw_func(lambda _a, cr, w, h: (cr.set_source_rgb(1, 1, 1), cr.paint()))
        over.set_child(bg)
        over.add_overlay(C.CornersOverlay(20))
        win.set_child(over)
        win.set_default_size(200, 120)
        win.present()
        settle()
        W, H = over.get_width(), over.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(over).snapshot(snap, W, H)
        tex = win.get_native().get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, W, H))
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(tex.save_to_png_bytes().get_data())).convert("RGB")
        W, H = im.size
        for x, y in ((0, 0), (W - 1, 0), (0, H - 1), (W - 1, H - 1), (2, 3), (W - 3, H - 2)):
            self.assertLess(sum(im.getpixel((x, y))), 30, (x, y))          # the corners: black
        for x, y in ((25, 25), (W - 25, 25), (W // 2, 0), (0, H // 2), (14, 14), (W - 15, H - 15)):
            self.assertEqual(im.getpixel((x, y)), (255, 255, 255), (x, y))  # inside the arc: untouched
        win.destroy()

    def test_lock_and_login_screens_get_them(self):
        base = os.path.dirname(C.__file__)
        for name in ("lock.py", "greeter.py"):
            self.assertIn("CornersOverlay()", open(os.path.join(base, name)).read(), name)

    def test_follows_the_setting(self):
        made = []
        fake = mock.Mock()
        fake.each = lambda create, destroy: type("S", (), {"rebuild": lambda s: made.append("rebuild")})()
        config.save("appearance", {"screen_corners": True})
        sc = C.ScreenCorners.__new__(C.ScreenCorners)
        sc.app, sc.surfaces, sc.monitors = None, None, fake
        sc.apply()
        config.save("appearance", {"screen_corners": False})
        sc.apply()
        self.assertEqual(made, ["rebuild"])
        self.assertEqual(sc._create(None), [])                              # off: no surfaces


class SettingsPlaceTest(unittest.TestCase):
    def test_in_displays(self):
        """Vini: the switch lives in Settings > Displays."""
        from sonata2.settings import app as S
        src = open(S.__file__).read()
        disp = src[src.index("    def _page_displays"):src.index("    def _page_battery")]
        self.assertIn('"Rounded screen corners"', disp)
        self.assertEqual(src.count('"Rounded screen corners"'), 1)


if __name__ == "__main__":
    unittest.main()
