"""Game Bar (shell/gamebar.py, Super+G). Vini: Windows' Win+G with the Big
Sur look -- a bar over the dimmed game with a toggle per widget; Esc, Super+G
or a click on the dimmed game closes it; the widgets shown are remembered;
nothing ticks while it's closed.
Run: xvfb-run -a python3 -m unittest tests.test_gamebar"""
import configparser
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from sonata2 import config, shortcuts, ui  # noqa: E402
from sonata2.shell import gamebar as G  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_APP = {}


def _app():
    if "a" not in _APP:
        ui.setup()
        app = Gtk.Application(application_id="io.github.vinioliveiras.sonata2.test.gamebar")
        app.register(None)
        _APP["a"] = app
    return _APP["a"]


def _spin(ms: int) -> None:
    loop = GLib.MainLoop()
    GLib.timeout_add(ms, loop.quit)
    loop.run()


def _key(win, keyval, state=0):
    keys = next(c for c in list(win.observe_controllers()) if isinstance(c, Gtk.EventControllerKey))
    return keys.emit("key-pressed", keyval, 0, state)


def _click(win, at):
    click = next(c for c in list(win.observe_controllers()) if isinstance(c, Gtk.GestureClick))
    with mock.patch.object(win, "pick", return_value=at):
        click.emit("pressed", 1, 5, 5)


class GameBarTests(unittest.TestCase):
    def setUp(self):
        config.save(G.CONFIG, dict(G.DEFAULTS))
        self.bar = G.GameBar(_app())

    def tearDown(self):
        self.bar.destroy()

    def _closed(self):
        _spin(G._close_ms() + 60)
        return not self.bar.get_visible()

    def test_super_g_toggles_it(self):
        self.bar.toggle()
        self.assertTrue(self.bar.is_open())
        self.bar.toggle()
        self.assertFalse(self.bar.is_open())
        self.assertTrue(self._closed())

    def test_esc_closes(self):
        self.bar.open()
        self.assertTrue(_key(self.bar, Gdk.KEY_Escape))
        self.assertTrue(self._closed())

    def test_super_g_inside_closes(self):
        """The overlay holds the keyboard while open: Super+G reaches it, not Wayfire."""
        self.bar.open()
        self.assertTrue(_key(self.bar, Gdk.KEY_g, Gdk.ModifierType.SUPER_MASK))
        self.assertTrue(self._closed())

    def test_click_on_dimmed_game_closes_but_not_on_bar_or_widget(self):
        self.bar.open()
        _click(self.bar, self.bar.bar)
        _click(self.bar, self.bar.widgets["capture"])
        self.assertTrue(self.bar.is_open())
        _click(self.bar, self.bar.scrim)
        self.assertTrue(self._closed())

    def test_reopen_while_closing_stays_open(self):
        """Super+G twice fast: the pending hide must not close the reopened bar."""
        self.bar.open()
        self.bar.close_bar()
        self.bar.open()
        _spin(G._close_ms() + 60)
        self.assertTrue(self.bar.is_open())

    def test_no_clock_tick_while_closed(self):
        self.bar.open()
        self.assertTrue(self.bar._clock_src)
        self.bar.close_bar()
        self.assertEqual(self.bar._clock_src, 0)

    def test_widget_toggle_lights_its_button_and_is_remembered(self):
        self.bar.open()
        btn = self.bar.toggles["audio"]
        self.assertTrue(btn.has_css_class("on"))
        self.bar.toggle_widget("audio")
        self.assertFalse(btn.has_css_class("on"))
        _spin(G._close_ms() + 60)
        self.assertFalse(self.bar.widgets["audio"].get_visible())
        self.assertNotIn("audio", config.load(G.CONFIG, G.DEFAULTS)["widgets"])
        again = G.GameBar(_app())
        try:
            self.assertFalse(again.widgets["audio"].get_visible())
            self.assertFalse(again.toggles["audio"].has_css_class("on"))
        finally:
            again.destroy()
        self.bar.toggle_widget("audio")
        self.assertTrue(btn.has_css_class("on"))
        self.assertTrue(self.bar.widgets["audio"].get_visible())

    def test_widget_quickly_toggled_back_stays(self):
        """Off and on again within the out animation: it must not vanish."""
        self.bar.open()
        self.bar.toggle_widget("capture")
        self.bar.toggle_widget("capture")
        _spin(G._close_ms() + 60)
        self.assertTrue(self.bar.widgets["capture"].get_visible())

    def test_front_app_without_menu_bar(self):
        self.bar.open()
        self.assertTrue(self.bar.app_name.get_label())


class GameBarLayoutTests(unittest.TestCase):
    def test_widgets_along_the_bottom(self):
        """Vini: Audio bottom left, Capture bottom centre (right of Audio),
        Performance bottom right -- the bar's toggles in that same order."""
        A = Gtk.Align
        self.assertEqual({k: v[2] for k, v in G.WIDGETS.items()},
                         {"audio": (A.START, A.END), "capture": (A.CENTER, A.END), "performance": (A.END, A.END)})
        self.assertEqual(list(G.WIDGETS), ["audio", "capture", "performance"])
        bar = G.GameBar(_app())
        try:
            bar.open()
            _spin(300)
            x = {k: w.compute_bounds(bar)[1].get_x() for k, w in bar.widgets.items()}
            y = {k: w.compute_bounds(bar)[1].get_y() for k, w in bar.widgets.items()}
            self.assertLess(x["audio"], x["capture"])
            self.assertLess(x["capture"], x["performance"])
            self.assertEqual(len(set(round(v) for v in y.values())), 1)          # one row
            self.assertGreater(y["audio"], bar.get_height() / 2)                 # at the bottom
            self.assertEqual(list(bar.toggles), list(G.WIDGETS))
        finally:
            bar.destroy()


class GameBarDrawTests(unittest.TestCase):
    """Vini: the recording pill's drawing (pen, shapes, text...) in the Game
    Bar too -- the same pen (livedraw.pen_button), in the bar's look."""

    def setUp(self):
        from sonata2.shell import livedraw
        self.L = livedraw
        self.d = mock.Mock(on=False, listeners=[])
        self.p = mock.patch.object(livedraw, "get", return_value=self.d)
        self.p.start()
        self.o = mock.patch("sonata2.shell.capture.focused_output", return_value="DP-1")
        self.o.start()
        config.save(G.CONFIG, dict(G.DEFAULTS))
        self.bar = G.GameBar(_app())
        self.bar.open()

    def tearDown(self):
        self.bar.destroy()
        self.p.stop()
        self.o.stop()

    def test_pen_on_the_bar_is_the_pills_pen(self):
        self.assertIn("gb-toggle", self.bar.pen.get_css_classes())
        self.assertTrue(self.bar.pen.is_ancestor(self.bar.bar))
        self.assertIn(self.bar.pen, G._focusables(self.bar.bar))           # the controller reaches it
        self.assertIn("Super+Shift+D", self.bar.pen.get_tooltip_text())

    def test_pen_closes_the_bar_then_draws_on_the_games_display(self):
        self.bar.pen.emit("clicked")
        self.assertFalse(self.bar.is_open())
        self.d.set_on.assert_not_called()                                   # not over the Game Bar
        _spin(G._close_ms() + 120)
        self.d.set_on.assert_called_once_with(True, "DP-1")

    def test_pen_again_stops_drawing_and_keeps_the_bar(self):
        self.d.on = True
        self.bar.pen.emit("clicked")
        self.d.toggle.assert_called_once()
        self.assertTrue(self.bar.is_open())

    def test_pen_lights_while_drawing(self):
        for fn in list(self.d.listeners):
            fn(True)
        self.assertTrue(self.bar.pen.has_css_class("on"))


class GameBarWiringTests(unittest.TestCase):
    def test_super_g_bound_in_wayfire(self):
        ini = configparser.ConfigParser(interpolation=None, strict=False)
        ini.read(os.path.join(REPO, "config", "wayfire.ini"))
        self.assertEqual(ini["command"]["binding_gamebar"].strip(), "<super> KEY_G")
        self.assertIn("ActivateAction gamebar", ini["command"]["command_gamebar"])

    def test_listed_in_keyboard_shortcuts(self):
        self.assertIn("gamebar", [s.id for s in shortcuts.SHORTCUTS])


if __name__ == "__main__":
    unittest.main()
