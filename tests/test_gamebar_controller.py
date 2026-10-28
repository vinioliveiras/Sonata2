"""Game Bar with a controller (Vini): the Xbox / PS button opens it -- in a
game and with desktop control off too -- and while open the controller
drives it: D-pad / left stick move the focus, A presses, B or Guide closes,
LB / RB jump between the bar and the widgets. The controllers are grabbed
while it's open (the game doesn't get the presses) and given back on close.
Run: xvfb-run -a python3 -m unittest tests.test_gamebar_controller"""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ.setdefault("XDG_RUNTIME_DIR", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2 import config, gamemode, ui  # noqa: E402
from sonata2.gamepad import evdev as E, service as S  # noqa: E402
from sonata2.shell import gamebar as G  # noqa: E402


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class FakePad:
    path, name = "p", "Xbox Wireless Controller"

    def __init__(self):
        self.grabs = []

    def norm(self, _c, v):
        return v

    def grab(self, on):
        self.grabs.append(on)
        return True


class FakeBar:
    def __init__(self):
        self.open, self.got = False, []

    def is_open(self):
        return self.open

    def pad(self, name):
        self.got.append(name)


class FakeApp:
    def __init__(self):
        self.actions = []

    def activate_action(self, name, param):
        self.actions.append(name)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self._steam = S.steam_running
        S.steam_running = lambda: False
        self.keys = []
        self._key = S.key
        S.key = lambda name, mods=(): self.keys.append(name)
        self.app, self.bar = FakeApp(), FakeBar()
        self.g = S.Gamepads(self.app, None, lambda: self.bar)
        self.pad = FakePad()
        self.g.pads = {"p": self.pad}

    def tearDown(self):
        S.steam_running, S.key = self._steam, self._key
        gamemode.Watcher._write(False)
        self.g._nav(None)

    def ev(self, typ, code, value):
        self.g._event(self.pad, typ, code, value)

    def test_guide_opens_game_bar_with_desktop_control_off(self):
        self.assertFalse(self.g.cfg["enabled"])                # off by default
        self.ev(E.EV_KEY, E.BTN_MODE, 1)
        self.assertEqual(self.app.actions, ["gamebar"])

    def test_guide_opens_game_bar_in_a_fullscreen_game(self):
        gamemode.Watcher._write(True)
        self.g.cfg["enabled"] = True
        self.assertTrue(self.g.paused)
        self.ev(E.EV_KEY, E.KEY_HOMEPAGE, 1)                   # Xbox over Bluetooth
        self.assertEqual(self.app.actions, ["gamebar"])

    def test_guide_setting_off_gives_overview_back(self):
        self.g.cfg.update(guide_gamebar=False, enabled=True)
        self.ev(E.EV_KEY, E.BTN_MODE, 1)
        self.assertEqual(self.app.actions, [])
        self.assertEqual(self.keys, ["F3"])

    def test_open_bar_gets_the_buttons_not_the_desktop(self):
        self.g.cfg["enabled"] = True
        self.bar.open = True
        for code in (E.BTN_SOUTH, E.BTN_EAST, E.BTN_TR, E.BTN_TL, E.BTN_DPAD_DOWN, E.BTN_MODE):
            self.ev(E.EV_KEY, code, 1)
            self.ev(E.EV_KEY, code, 0)                         # releases are not presses
        self.ev(E.EV_ABS, E.ABS_HAT0X, 1)
        self.assertEqual(self.bar.got, ["primary", "back", "switch_next", "switch_prev", "down", "guide", "right"])
        self.assertEqual(self.keys, [])                        # no Esc / arrows to the game
        self.assertEqual(self.app.actions, [])                 # no app switcher, no second open

    def test_left_stick_steps_and_repeats(self):
        self.bar.open = True
        self.ev(E.EV_ABS, E.ABS_X, 0.9)
        self.assertEqual(self.bar.got, ["right"])
        settle(S.NAV_FIRST_MS + S.NAV_REPEAT_MS + 60)
        self.assertGreaterEqual(self.bar.got.count("right"), 2)
        self.ev(E.EV_ABS, E.ABS_X, 0.0)
        n = len(self.bar.got)
        settle(S.NAV_REPEAT_MS * 3)
        self.assertEqual(len(self.bar.got), n)                 # let go: it stops
        self.ev(E.EV_ABS, E.ABS_Y, -0.8)
        self.assertEqual(self.bar.got[-1], "up")

    def test_stick_doesnt_move_pointer_while_open(self):
        self.g.cfg["enabled"] = True
        self.bar.open = True
        self.ev(E.EV_ABS, E.ABS_X, 0.9)
        self.assertEqual(self.g._tick_src, 0)

    def test_grab_and_release(self):
        self.g.grab(True)
        self.g.grab(True)                                      # once
        self.g.grab(False)
        self.assertEqual(self.pad.grabs, [True, False])

    def test_pad_plugged_while_grabbed_is_grabbed(self):
        self.g.grab(True)
        made = []

        class NewPad(FakePad):
            def __init__(self, path, _cb):
                super().__init__()
                self.path = path
                made.append(self)
        old_find, old_cls, old_guides = E.find_gamepads, E.Gamepad, E.find_guide_devices
        E.find_gamepads, E.Gamepad, E.find_guide_devices = (lambda: ["/dev/input/event99"]), NewPad, (lambda n: [])
        try:
            self.g.scan()
        finally:
            E.find_gamepads, E.Gamepad, E.find_guide_devices = old_find, old_cls, old_guides
        self.assertEqual(made[0].grabs, [True])


_APP = {}


def _app():
    if "a" not in _APP:
        ui.setup()
        a = Gtk.Application(application_id="io.github.vinioliveiras.sonata2.test.gbpad")
        a.register(None)
        _APP["a"] = a
    return _APP["a"]


class BarTests(unittest.TestCase):
    def setUp(self):
        config.save(G.CONFIG, dict(G.DEFAULTS))
        self.grabs = []
        top = type("Top", (), {})()
        top.gamepads = type("P", (), {"grab": lambda _s, on: self.grabs.append(on)})()
        self.bar = G.GameBar(_app(), top)
        self.bar.open()
        settle(200)

    def tearDown(self):
        self.bar.destroy()

    def test_open_grabs_and_close_releases(self):
        self.assertEqual(self.grabs, [True])
        self.bar.pad("back")
        self.assertEqual(self.grabs, [True, False])
        settle(G._close_ms() + 80)
        self.assertFalse(self.bar.get_visible())

    def test_guide_closes(self):
        self.bar.pad("guide")
        self.assertFalse(self.bar.is_open())

    def test_focus_starts_on_first_toggle_and_moves(self):
        t = list(self.bar.toggles.values())
        self.assertIs(self.bar.get_focus(), t[0])
        self.bar.pad("right")
        self.assertIs(self.bar.get_focus(), t[1])
        self.assertTrue(self.bar.get_focus_visible())         # the ring shows

    def test_a_presses_the_focused_toggle(self):
        t = next(iter(self.bar.toggles.values()))          # where the focus starts
        self.assertTrue(t.has_css_class("on"))
        self.bar.pad("primary")
        settle(400)                                            # GTK shows the press, then clicks
        self.assertFalse(t.has_css_class("on"))

    def test_lb_rb_jump_between_bar_and_widgets(self):
        """LB / RB: the bar, then each widget with something to press, in
        the screen's order (Audio, Capture...), and round again."""
        zones = self.bar._zones()
        self.assertIs(zones[0], self.bar.bar)
        self.assertIn(self.bar.widgets["audio"], zones)
        self.assertIn(self.bar.widgets["capture"], zones)
        seen = []
        for _i in range(len(zones)):
            self.bar.pad("switch_next")
            seen.append(next(z for z in zones if self.bar._inside(z)))
        self.assertEqual(seen, zones[1:] + zones[:1])            # every zone once, back to the bar
        self.assertIs(self.bar.get_focus(), next(iter(self.bar.toggles.values())))
        self.bar.pad("switch_prev")
        self.assertTrue(self.bar._inside(zones[-1]))

    def test_dpad_left_right_change_a_slider(self):
        """On a slider (Audio's volumes) the D-pad's left / right move it."""
        vol = self.bar.vol
        vol.grab_focus()
        before = vol.get_value()
        self.bar.pad("right")
        self.assertAlmostEqual(vol.get_value(), min(100, before + 5))
        self.assertIs(self.bar.get_focus(), vol)                   # still there

    def test_ignored_when_closed(self):
        self.bar.close_bar()
        self.bar.pad("primary")
        self.assertTrue(self.bar.toggles["capture"].has_css_class("on"))


if __name__ == "__main__":
    unittest.main()
