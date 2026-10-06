"""Vini: the keyboard layout shown at the top of the screen, several layouts
and a way to switch between them, like macOS: the input menu is always there
(it showed only with two or more layouts), Ctrl+Space goes back to the one
used before."""
import configparser
import os
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from sonata2.shell import topbar as T  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def fake_bar(layouts, show=True):
    bar = types.SimpleNamespace(cfg={"show_input": show}, shown=None, text=None, tip=None)
    bar._layouts = lambda: list(layouts)
    bar.input_btn = types.SimpleNamespace(set_visible=lambda v: setattr(bar, "shown", v),
                                          set_tooltip_text=lambda t: setattr(bar, "tip", t))
    bar._set_text = lambda _b, t: setattr(bar, "text", t)
    return bar


class InputSourcesTest(unittest.TestCase):
    def test_shown_with_one_layout(self):
        bar = fake_bar(["br(abnt2)"])
        T.Bar._update_input(bar)
        self.assertTrue(bar.shown)
        self.assertEqual(bar.text, "BR")
        bar = fake_bar(["us"], show=False)                      # turned off in Settings
        T.Bar._update_input(bar)
        self.assertFalse(bar.shown)

    def test_settings_toggle_on_by_default(self):
        """Vini: a toggle in Settings to show it or not, on by default."""
        import inspect
        from sonata2.settings import app
        self.assertTrue(T.DEFAULTS["show_input"])
        self.assertIn('("show_input", "Keyboard layout"', inspect.getsource(app))
        self.assertIn("self._update_input()", inspect.getsource(T.Bar._config_changed))   # live

    def test_badges(self):
        self.assertEqual([T.layout_badge(x) for x in ("br(abnt2)", "us(intl)", "pt", "")], ["BR", "US", "PT", "?"])

    def test_ctrl_space_goes_back_to_the_previous_layout(self):
        used = []
        bar = fake_bar(["br", "us"])
        bar._use_layout = used.append
        T.Bar.next_layout(bar)
        self.assertEqual(used, [1])
        bar = fake_bar(["br"])
        bar._use_layout = used.append
        T.Bar.next_layout(bar)                                  # one layout: nothing to switch
        self.assertEqual(used, [1])

    def test_switching_reorders_and_every_bar_follows(self):
        saved, updated = {}, []
        bar = fake_bar(["br", "us(intl)", "pt"])
        bar._update_input = lambda: updated.append("a")
        other = types.SimpleNamespace(_update_input=lambda: updated.append("b"))
        with mock.patch.object(T.system, "wayfire_set", lambda s, k, v: saved.__setitem__(k, v) or True), \
                mock.patch.object(T.system, "run_async", lambda fn, cb: cb(fn())), \
                mock.patch.object(T, "_BARS", [bar, other]):
            T.Bar._use_layout(bar, 1)
        self.assertEqual(saved, {"xkb_layout": "us,br,pt", "xkb_variant": "intl,,"})
        self.assertEqual(sorted(updated), ["a", "b"])

    def test_shortcut_wired(self):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.read(os.path.join(ROOT, "config", "wayfire.ini"))
        cmd = [s for s in cp.sections() if cp.has_option(s, "binding_input")]
        self.assertTrue(cmd)
        self.assertEqual(cp.get(cmd[0], "binding_input"), "<ctrl> KEY_SPACE")
        self.assertIn("input-next", cp.get(cmd[0], "command_input"))
        main = open(os.path.join(ROOT, "sonata2", "__main__.py")).read()
        self.assertIn('Gio.SimpleAction.new("input-next", None)', main)
        from sonata2 import shortcuts
        self.assertIn("binding_input", open(shortcuts.__file__).read())


if __name__ == "__main__":
    unittest.main()
