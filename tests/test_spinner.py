"""Vini: after a system update the lock screen's spinner was gone.
Gtk.Spinner draws the icon theme's "process-working-symbolic", which
adwaita-icon-theme 51 dropped. Sonata draws its own (ui.progress.Spinner)
and nothing uses Gtk.Spinner any more."""
import pathlib
import re
import unittest
import unittest.mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2.ui import progress  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


class SpinnerTest(unittest.TestCase):
    def test_no_icon_theme_spinner(self):
        used = [str(p.relative_to(ROOT)) for p in (ROOT / "sonata2").rglob("*.py")
                if re.search(r"Gtk\.Spinner\(", p.read_text())]
        self.assertEqual(used, [])

    def test_lock_and_greeter_use_it(self):
        for rel in ("shell/lock.py", "shell/greeter.py", "shell/welcome.py"):
            self.assertIn("Spinner(css_classes=[\"gr-spinner\"]", (ROOT / "sonata2" / rel).read_text(), rel)

    def _drawn(self, sp):
        win = Gtk.Window()
        win.set_child(sp)
        win.present()
        ctx = GLib.MainContext.default()
        for _ in range(50):
            ctx.iteration(False)
        snap = Gtk.Snapshot()
        sp.do_snapshot(snap)
        node = snap.to_node()
        win.destroy()
        return node

    def test_draws_without_any_icon(self):
        sp = progress.spinner(size=22)
        self.assertTrue(sp.get_spinning())
        self.assertTrue(sp.get_state_flags() & Gtk.StateFlags.CHECKED)
        self.assertIsNotNone(self._drawn(sp))

    def test_stopped_draws_nothing_and_ticks_no_more(self):
        sp = progress.spinner(size=22)
        sp.stop()
        self.assertFalse(sp.get_state_flags() & Gtk.StateFlags.CHECKED)
        self.assertIsNone(self._drawn(sp))
        self.assertEqual(sp._tick, 0)
        sp.start()
        self.assertTrue(sp.get_spinning())

    def test_turns_on_a_timer_not_the_frame_clock(self):
        """Memory/power review: a tick callback kept the frame clock (and the
        compositor) at the display's refresh rate for 12 redraws a second."""
        sp = progress.spinner(size=22)
        with unittest.mock.patch.object(Gtk.Widget, "add_tick_callback") as tick:
            win = Gtk.Window()
            win.set_child(sp)
            win.present()
            ctx = GLib.MainContext.default()
            steps = set()
            end = GLib.get_monotonic_time() + 400_000
            while GLib.get_monotonic_time() < end:
                ctx.iteration(False)
                steps.add(sp.step)
            tick.assert_not_called()
        self.assertTrue(sp._tick)
        self.assertGreaterEqual(len(steps), 3)                  # it turns (~5 spokes in 0.4 s)
        win.destroy()
        ctx.iteration(False)
        self.assertEqual(sp._tick, 0)                           # unmapped: the timer is gone

    def test_never_bigger_than_its_size(self):
        """Vini: the login screen's spinner came out huge -- stretched to the
        room it was given. It draws at its own size, centred."""
        sp = progress.Spinner(spinning=True, width_request=16, height_request=16,
                              halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
        win = Gtk.Window(default_width=300, default_height=300)
        win.set_child(sp)
        win.present()
        ctx = GLib.MainContext.default()
        for _ in range(50):
            ctx.iteration(False)
        self.assertGreater(sp.get_width(), 100)                 # given far more room
        snap = Gtk.Snapshot()
        sp.do_snapshot(snap)
        b = snap.to_node().get_bounds()
        self.assertLessEqual(max(b.get_width(), b.get_height()), 17)
        win.destroy()

    def test_login_spinner_is_small(self):
        css = (ROOT / "sonata2" / "shell" / "loginui.py").read_text()
        line = next(l for l in css.splitlines() if l.startswith("spinner.gr-spinner"))
        self.assertIn("min-width: 16px", line)

    def test_css_node_is_spinner(self):
        # the existing "spinner.gr-spinner" / "spinner.sonata-spinner" CSS still styles it
        self.assertEqual(progress.Spinner().get_css_name(), "spinner")


if __name__ == "__main__":
    unittest.main()
