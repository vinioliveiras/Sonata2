"""Vini: "I'd like to be able to customise the order of the window buttons
close, minimize and zoom." Settings > Appearance > Button order keeps it in
appearance.json ("buttons_order", from the window's edge inwards; mirrored
on the right), and every place that draws window buttons follows it."""
import json
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import config, wfconfig  # noqa: E402
from sonata2.ui import theme, tokens  # noqa: E402
from sonata2.ui import window as W  # noqa: E402

DEFAULT = ("close", "minimize", "maximize")
MINE = ("maximize", "close", "minimize")          # zoom at the edge, then close, then minimize


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def dots(box):
    """The dots' names, left to right."""
    back = {v: k for k, v in W.DOT_CLASSES.items()}
    out, c = [], box.get_first_child()
    while c is not None:
        out.append(next(back[k] for k in c.get_css_classes() if k in back))
        c = c.get_next_sibling()
    return out


class Base(unittest.TestCase):
    def setUp(self):
        home = tempfile.mkdtemp()
        p = mock.patch.object(config, "CONFIG_DIR", os.path.join(home, "sonata2"))
        p.start()
        self.addCleanup(p.stop)
        e = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": home})      # (tokens read on their own: prefs.py)
        e.start()
        self.addCleanup(e.stop)
        theme._buttons_seen = theme._buttons_sig()

    def change(self, **kw):
        config.update("appearance", **kw)
        theme._buttons_changed()                  # (what appearance.json's monitor calls)


class StoredOrderTest(Base):
    def test_default_is_macos(self):
        self.assertEqual(tokens.user_buttons_order(), DEFAULT)
        self.assertEqual(tokens.frame()["buttons"], DEFAULT)

    def test_a_bad_stored_order_is_the_default(self):
        """A hand-edited or old appearance.json never gives a window two close
        buttons, or none."""
        for bad in (None, "close,minimize,maximize", ["close", "minimize"], ["close", "close", "minimize"],
                    ["close", "minimize", "maximize", "close"], ["close", "minimize", "restore"], [1, 2, 3], {}):
            self.assertEqual(tokens.buttons_order(bad), DEFAULT, bad)
            config.update("appearance", buttons_order=bad)
            self.assertEqual(tokens.user_buttons_order(), DEFAULT, bad)
            self.assertEqual(W.buttons_order(), DEFAULT, bad)

    def test_a_good_order_is_kept(self):
        config.update("appearance", buttons_order=list(MINE))
        self.assertEqual(tokens.user_buttons_order(), MINE)
        self.assertEqual(tokens.frame()["buttons"], MINE)


class LayoutStringsTest(Base):
    def test_default_strings_unchanged(self):
        f = tokens.frame()
        self.assertEqual(tokens.button_layout(f), "close,minimize,maximize:")
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(f)}
        self.assertEqual(opts[("decoration", "button_order")], "minimize maximize close")
        config.update("appearance", buttons_side="right")
        f = tokens.frame()
        self.assertEqual(tokens.button_layout(f), ":maximize,minimize,close")
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(f)}
        self.assertEqual(opts[("decoration", "button_order")], "maximize minimize close")

    def test_left_in_my_order(self):
        config.update("appearance", buttons_order=list(MINE))
        f = tokens.frame()
        self.assertEqual(tokens.button_layout(f), "maximize,close,minimize:")
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(f)}
        self.assertEqual(opts[("pixdecor", "button_layout")], "maximize,close,minimize:")
        # Wayfire's own bar keeps its buttons on the right: the first one at the edge
        self.assertEqual(opts[("decoration", "button_order")], "close minimize maximize")

    def test_right_in_my_order_mirrored(self):
        config.update("appearance", buttons_order=list(MINE), buttons_side="right")
        f = tokens.frame()
        self.assertEqual(tokens.button_layout(f), ":minimize,close,maximize")       # maximize at the edge
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(f)}
        self.assertEqual(opts[("pixdecor", "button_layout")], ":minimize,close,maximize")
        self.assertEqual(opts[("decoration", "button_order")], "minimize close maximize")

    def test_gtk_apps_button_layout(self):
        """GNOME's button-layout (GTK 3/4 apps; XWayland's Gtk/DecorationLayout
        reads it) -- prefs.py loads tokens.py on its own, without Sonata's
        config module."""
        from sonata2 import prefs
        config.update("appearance", buttons_order=list(MINE))
        self.assertEqual(prefs._frame()["layout"], "maximize,close,minimize:")
        config.update("appearance", buttons_side="right")
        self.assertEqual(prefs._frame()["layout"], ":minimize,close,maximize")

    def test_settings_applies_it_everywhere(self):
        from sonata2.settings import app
        config.update("appearance", buttons_order=list(MINE))
        calls, gs = [], []
        with mock.patch.object(app.system, "wayfire_set", lambda s, k, v: calls.append((s, k, v)) or True), \
                mock.patch("sonata2.prefs.set", lambda sch, k, v: gs.append((k, v))), \
                mock.patch("sonata2.titlebars.apply", lambda *a: None):
            app.apply_buttons_side()
        self.assertIn(("pixdecor", "button_layout", "maximize,close,minimize:"), calls)
        self.assertIn(("decoration", "button_order", "close minimize maximize"), calls)
        self.assertIn(("button-layout", "maximize,close,minimize:"), gs)

    def test_steam_in_my_order(self):
        from sonata2 import steamtheme
        config.update("appearance", buttons_order=list(MINE))
        css = steamtheme.window_css()
        self.assertIn("/* window buttons: maximize,close,minimize: */", css)
        self.assertIn(".title-area-icon.maximizeButton { order: 0 !important; }", css)
        self.assertIn(".title-area-icon.minimizeButton { order: 2 !important; }", css)


class TrafficLightsTest(Base):
    def test_default_order(self):
        self.assertEqual(dots(W.traffic_lights(lambda: 0, lambda: 0, lambda: 0)), list(DEFAULT))

    def test_left_in_my_order(self):
        config.update("appearance", buttons_order=list(MINE))
        box = W.traffic_lights(lambda: 0, lambda: 0, lambda: 0)
        self.assertEqual(dots(box), list(MINE))
        self.assertEqual(box.get_first_child().outer, "left")         # the first one reaches the edge
        self.assertIsNone(box.get_last_child().outer)

    def test_right_in_my_order_mirrored(self):
        config.update("appearance", buttons_order=list(MINE), buttons_side="right")
        box = W.traffic_lights(lambda: 0, lambda: 0, lambda: 0)
        self.assertEqual(dots(box), list(reversed(MINE)))
        self.assertEqual(box.get_last_child().outer, "right")
        self.assertIn("tl-zoom", box.get_last_child().get_css_classes())

    def test_each_dot_still_does_its_job(self):
        """Moving a dot moves what it does with it: the red one closes."""
        config.update("appearance", buttons_order=list(MINE))
        hit = []
        box = W.traffic_lights(lambda: hit.append("close"), lambda: hit.append("minimize"),
                               lambda: hit.append("maximize"))
        c = box.get_first_child()
        while c is not None:
            c.emit("clicked")
            c = c.get_next_sibling()
        self.assertEqual(hit, list(MINE))

    def test_zoom_disabled_wherever_it_is(self):
        config.update("appearance", buttons_order=list(MINE))
        box = W.traffic_lights(lambda: 0, lambda: 0)
        self.assertIn("tl-disabled", box.get_first_child().get_css_classes())

    def test_open_titlebar_follows(self):
        h = W.titlebar(Gtk.Window(), "x")
        self.assertEqual(dots(h.lights), list(DEFAULT))
        self.change(buttons_order=list(MINE))
        self.assertEqual(dots(h.lights), list(MINE))


class OrderPickerTest(Base):
    def test_shows_the_order_as_windows_do(self):
        self.assertEqual(dots(W.order_lights(MINE, False, lambda o: None)), list(MINE))
        self.assertEqual(dots(W.order_lights(MINE, True, lambda o: None)), list(reversed(MINE)))
        self.assertEqual(dots(W.order_lights(["close"], False, lambda o: None)), list(DEFAULT))     # (validated)

    def test_the_picture_is_not_a_windows_buttons(self):
        """The picker's dots are a picture: a dialog holding it still gets its
        own close button, and nothing counts them as the window's."""
        box = Gtk.Box()
        box.append(W.order_lights(DEFAULT, False, lambda o: None))
        self.assertFalse(W.has_window_buttons(box))
        box.append(W.traffic_lights(lambda: 0, lambda: 0))
        self.assertTrue(W.has_window_buttons(box))

    def test_moving_a_dot(self):
        got = []
        box = W.order_lights(DEFAULT, False, got.append)
        box.move("maximize", 0)
        self.assertEqual(got, [("maximize", "close", "minimize")])
        box.move("close", 0)                         # already there: nothing to save
        self.assertEqual(len(got), 1)

    def test_moving_a_dot_on_the_right(self):
        """On the right the row reads mirrored: the dot dropped at the right
        end is the one at the window's edge."""
        got = []
        box = W.order_lights(DEFAULT, True, got.append)       # maximize, minimize, close
        box.move("maximize", 2)
        self.assertEqual(got, [("maximize", "close", "minimize")])

    def test_drop_by_position(self):
        win = Gtk.Window()
        got = []
        box = W.order_lights(DEFAULT, False, got.append)
        win.set_child(box)
        win.present()
        settle(200)
        last = box.get_last_child()
        ok, b = last.compute_bounds(box)
        self.assertTrue(ok)
        self.assertTrue(box.dropped(None, "close", b.get_x() + b.get_width() - 1, 0))     # past zoom's centre
        self.assertEqual(got[-1], ("minimize", "maximize", "close"))
        self.assertTrue(box.dropped(None, "maximize", 0, 0))                               # at the start
        self.assertEqual(got[-1], ("maximize", "close", "minimize"))
        self.assertFalse(box.dropped(None, "restore", 0, 0))                               # not a button
        win.destroy()


class SettingsTest(Base):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def _open(self):
        from sonata2.settings import app as S
        win = S.Settings(None)
        win.present()
        win.select("appearance", from_sidebar=True)
        settle(300)
        return S, win

    def test_settings_writes_it_and_moves_its_own_buttons(self):
        S, win = self._open()
        self.assertFalse(win.button_order_default.get_sensitive())        # already macOS's
        self.assertEqual(dots(win.button_order_dots), list(DEFAULT))
        with mock.patch.object(S.system, "run_async") as run:
            win.button_order_dots.move("maximize", 0)
        settle(100)
        with open(os.path.join(config.CONFIG_DIR, "appearance.json"), encoding="utf-8") as f:
            a = json.load(f)
        self.assertEqual(a["buttons_order"], list(MINE))
        self.assertIn(S.apply_buttons_side, [c.args[0] for c in run.call_args_list])     # Wayfire, GTK apps
        self.assertEqual(dots(win._side_lights), list(MINE))                             # Settings' own
        self.assertEqual(dots(win.button_order_dots), list(MINE))
        self.assertTrue(win.button_order_default.get_sensitive())
        with mock.patch.object(S.system, "run_async"):
            win.button_order_default.emit("clicked")                                     # Use Default
        settle(100)
        self.assertEqual(tokens.user_buttons_order(), DEFAULT)
        self.assertEqual(dots(win._side_lights), list(DEFAULT))
        win.destroy()

    def test_picture_mirrors_with_the_side(self):
        S, win = self._open()
        config.update("appearance", buttons_order=list(MINE))
        with mock.patch.object(S.system, "run_async"):
            win._set_buttons_side("right")
        self.assertEqual(dots(win.button_order_dots), list(reversed(MINE)))
        tv = win.pages[S.section_of("appearance")]
        self.assertEqual(dots(tv.lights), list(reversed(MINE)))
        win.destroy()

    def test_reset_appearance_restores_the_order(self):
        from sonata2 import icons
        S, win = self._open()
        self.assertIn("buttons_order", S.Settings.APPEARANCE_RESET)
        config.update("appearance", buttons_order=list(MINE))
        with mock.patch.object(S.system, "run_async"):
            win.reset_appearance()
        a = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        self.assertEqual(a["buttons_order"], list(DEFAULT))
        self.assertEqual(tokens.user_buttons_order(), DEFAULT)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
