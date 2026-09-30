"""Game controllers driving the desktop (sonata2/gamepad): reading events,
the button map, the pauses that keep games safe.
Run: python3 -m unittest tests.test_gamepad"""
import os
import struct
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()

from gi.repository import GLib  # noqa: E402

from sonata2 import gamemode  # noqa: E402
from sonata2.gamepad import evdev as E, service as S  # noqa: E402


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class FakePointer:
    ok = True

    def __init__(self):
        self.log = []

    def move(self, dx, dy): self.log.append(("move", dx, dy))
    def button(self, b, p): self.log.append(("button", b, p))
    def scroll(self, dx, dy): self.log.append(("scroll", dx, dy))
    def close(self): pass


class GamepadTest(unittest.TestCase):
    def setUp(self):
        self.keys = []
        self._key = S.key
        S.key = lambda name, mods=(): self.keys.append(name)
        self._steam = S.steam_running
        S.steam_running = lambda: False
        gamemode.Watcher._write(False)
        self.g = S.Gamepads(None, None)
        self.g.cfg["enabled"] = True
        self.g.vp = FakePointer()

    def tearDown(self):
        S.key, S.steam_running = self._key, self._steam
        gamemode.Watcher._write(False)

    def press(self, code, value=1):
        self.g._event(type("P", (), {"path": "p", "norm": lambda s, c, v: v})(), E.EV_KEY, code, value)

    def test_dead_zone_and_curve(self):
        self.assertEqual(S.curve(0.1), 0.0)
        self.assertGreater(S.curve(1.0), 0.99)
        self.assertLess(S.curve(-0.6), 0)

    def test_buttons(self):
        self.press(E.BTN_SOUTH, 1)
        self.press(E.BTN_SOUTH, 0)
        self.press(E.BTN_NORTH, 1)
        self.press(E.BTN_EAST, 1)
        self.press(E.BTN_DPAD_UP, 1)
        self.assertEqual(self.g.vp.log[:3], [("button", S.BTN_LEFT, True), ("button", S.BTN_LEFT, False),
                                             ("button", S.BTN_RIGHT, True)])
        self.assertEqual(self.keys, ["Escape", "Up"])

    def test_paused_in_fullscreen_games_and_with_steam(self):
        gamemode.Watcher._write(True)                  # a fullscreen window has the focus
        self.press(E.BTN_SOUTH, 1)
        self.assertEqual(self.g.vp.log, [])
        gamemode.Watcher._write(False)
        self.g._steam = True
        self.press(E.BTN_EAST, 1)
        self.assertEqual(self.keys, [])

    def test_off_by_default_and_ignores_buttons_when_off(self):
        self.assertFalse(S.DEFAULTS["enabled"])
        self.g.cfg["enabled"] = False
        self.press(E.BTN_SOUTH, 1)
        self.press(E.BTN_EAST, 1)
        self.assertEqual((self.g.vp.log, self.keys), ([], []))

    def test_quick_guide_presses_toggle(self):
        self.g._notify = lambda _t: None
        self.g.cfg["enabled"] = False
        for _ in range(S.TOGGLE_TAPS):
            self.press(E.BTN_MODE, 1)
            self.press(E.BTN_MODE, 0)
        self.assertTrue(self.g.cfg["enabled"])
        self.assertTrue(S.config.load("gamepad", S.DEFAULTS)["enabled"])     # saved
        settle(int(S.TAP_GAP_S * 1000) + 150)
        self.assertEqual(self.keys, [])            # a burst is not Mission Control
        for _ in range(S.TOGGLE_TAPS):
            self.press(E.BTN_MODE, 1)
        self.assertFalse(self.g.cfg["enabled"])

    def test_xbox_button_over_bluetooth_toggles_too(self):
        """The shortcut did nothing: over Bluetooth the Xbox button comes as
        KEY_HOMEPAGE from a "Consumer Control" device, not BTN_MODE."""
        self.assertEqual(S.TOGGLE_TAPS, 5)
        self.g._notify = lambda _t: None
        self.g.cfg["enabled"] = False
        for _ in range(5):
            self.press(E.KEY_HOMEPAGE, 1)
            self.press(E.KEY_HOMEPAGE, 0)
        self.assertTrue(self.g.cfg["enabled"])

    def test_one_guide_press_is_mission_control(self):
        self.press(E.BTN_MODE, 1)
        self.press(E.BTN_MODE, 0)
        self.assertEqual(self.keys, [])            # waits: more presses may follow
        settle(int(S.TAP_GAP_S * 1000) + 150)
        self.assertEqual(self.keys, ["F3"])

    def test_steam_pause_can_be_turned_off(self):
        self.g._steam = True
        self.assertTrue(self.g.paused)
        self.g.cfg["pause_steam"] = False
        self.assertFalse(self.g.paused)

    def test_switcher_driven_by_the_controller(self):
        calls = []

        class Panel:
            def has_css_class(self, _c): return False

        class Sw:
            panel = Panel()
            def get_visible(self): return True
            def _switch(self): calls.append("switch")
            def _close(self): calls.append("close")
            def step(self, d): calls.append(d)
        self.g.switcher = lambda: Sw()
        self.press(E.BTN_DPAD_RIGHT, 1)
        self.press(E.BTN_SOUTH, 1)
        self.press(E.BTN_EAST, 1)
        self.assertEqual(calls, [1, "switch", "close"])
        self.assertEqual(self.g.vp.log, [])

    def test_stick_moves_the_pointer(self):
        self.g.axes[("p", E.ABS_X)] = 1.0
        self.g._tick_soon()
        settle(120)
        self.g.axes[("p", E.ABS_X)] = 0.0
        settle(40)
        moves = [e for e in self.g.vp.log if e[0] == "move"]
        self.assertTrue(moves and all(m[1] > 0 for m in moves))
        self.assertEqual(self.g._tick_src, 0)          # stopped once the stick is back

    def test_reads_events_from_the_device(self):
        path = os.path.join(tempfile.mkdtemp(), "event0")
        os.mkfifo(path)
        got = []
        pad = E.Gamepad(path, lambda p, t, c, v: got.append((t, c, v)))
        w = os.open(path, os.O_WRONLY)
        os.write(w, struct.pack("llHHi", 0, 0, E.EV_KEY, E.BTN_SOUTH, 1) +
                 struct.pack("llHHi", 0, 0, 0, 0, 0) +                   # SYN: ignored
                 struct.pack("llHHi", 0, 0, E.EV_ABS, E.ABS_X, 32767))
        settle(100)
        self.assertEqual(got, [(E.EV_KEY, E.BTN_SOUTH, 1), (E.EV_ABS, E.ABS_X, 32767)])
        self.assertAlmostEqual(pad.norm(E.ABS_X, 32767), 1.0, places=3)
        os.close(w)
        pad.close()


if __name__ == "__main__":
    unittest.main()
