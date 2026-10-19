"""Settings > Keyboard fits a narrow window
(dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_settings_keyboard_width).

Regression: a shortcut with two key combos showed them side by side
("Ctrl+Super+→ or Alt+Super+→"): Keyboard needed 542 px, the one section
wider than the rest, and pushed a narrow Settings window wider. Each
alternative now has its own line."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import GLib, Gtk  # noqa: E402


def settle(ms=500):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class KeyboardWidthTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        ui.setup()

    def test_two_key_combos_dont_widen_the_keyboard_section(self):
        from sonata2.settings import app as S
        win = S.Settings(None)
        win.present()
        settle(1200)
        widths = {}
        for sid in [s[0] for s in S.SECTIONS]:
            win.select(sid)
            settle(300)
            page = win.pages.get(sid)
            if page is not None:
                widths[sid] = page.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        others = max(w for sid, w in widths.items() if sid != "keyboard")
        self.assertLessEqual(widths["keyboard"], others, widths)
        win.destroy()

    def test_each_alternative_on_its_own_line(self):
        from sonata2.settings import shortcuts_page as P
        box = P._keys("<ctrl> <super> KEY_RIGHT | <alt> <super> KEY_RIGHT")
        self.assertEqual(box.get_orientation(), Gtk.Orientation.VERTICAL)
        lines = []
        c = box.get_first_child()
        while c is not None:
            lines.append(c)
            c = c.get_next_sibling()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(ln.get_halign() == Gtk.Align.END for ln in lines))


if __name__ == "__main__":
    unittest.main()
