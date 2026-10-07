"""Vini: after a system update the lock screen's spinner was gone.
Gtk.Spinner draws the icon theme's "process-working-symbolic", which
adwaita-icon-theme 51 dropped. Sonata draws its own (ui.progress.Spinner)
and nothing uses Gtk.Spinner any more."""
import pathlib
import re
import unittest

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

    def test_css_node_is_spinner(self):
        # the existing "spinner.gr-spinner" / "spinner.sonata-spinner" CSS still styles it
        self.assertEqual(progress.Spinner().get_css_name(), "spinner")


if __name__ == "__main__":
    unittest.main()
