"""Control Center: the whole Do Not Disturb square toggles it, not only its
round button (Vini); nothing while the controls are being edited."""
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402


class DndSquareTest(unittest.TestCase):
    def build(self):
        Gtk.init()
        seen = []
        t = ui.panel.toggle("weather-clear-night-symbolic", "Do Not Disturb", False, seen.append)
        area = ui.panel.click_anywhere(ui.panel.module(t), t)
        win = Gtk.Window()
        win.set_child(area)
        win.present()
        for _ in range(20):
            from gi.repository import GLib
            GLib.MainContext.default().iteration(False)
        return area, t, seen, win

    def test_click_on_the_title_toggles(self):
        area, t, seen, win = self.build()
        title = t.get_last_child()                       # the texts box
        pt = title.translate_coordinates(area, 2, 2)
        self.assertIsNotNone(pt)
        x, y = pt[-2], pt[-1]
        area.toggle_click.emit("released", 1, x, y)
        self.assertEqual(seen, [True])
        self.assertTrue(t.button.has_css_class("on"))
        win.destroy()

    def test_not_while_editing(self):
        area, t, seen, win = self.build()
        win.editing = True
        area.toggle_click.emit("released", 1, 1, 1)
        self.assertEqual(seen, [])
        win.destroy()

    def test_dnd_module_uses_it(self):
        import inspect
        from sonata2.shell import topbar
        src = inspect.getsource(topbar)
        self.assertIn("ui.panel.click_anywhere(ui.panel.module(dnd_toggle), dnd_toggle)", src)


if __name__ == "__main__":
    unittest.main()
