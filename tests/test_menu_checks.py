"""Menus with checkmark rows (Vini): every row's text starts at the same
line as the separators, and the check comes after the text.
Run: xvfb-run -a python3 -m unittest tests.test_menu_checks"""
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from sonata2.ui import menu, theme  # noqa: E402


def _walk(w, name, out):
    w = w.get_first_child()
    while w is not None:
        if w.get_css_name() == name:
            out.append(w)
        _walk(w, name, out)
        w = w.get_next_sibling()
    return out


def _label(btn):
    return next(c for c in _walk(btn, "label", []) if c.get_label())


class MenuChecksTest(unittest.TestCase):
    def test_text_aligned_and_check_after_it(self):
        if not Gtk.init_check():
            self.skipTest("no display")
        theme.setup()
        win = Gtk.Window(default_width=400, default_height=300)
        b = Gtk.Button(label="x")
        win.set_child(b)
        win.present()
        I = menu.Item
        pop = menu.popup(b, [[I("Open", lambda: 0), I("Keep in Dock", lambda s: 0, checked=True),
                              I("Open at Login", lambda s: 0, checked=False)]])
        ctx = GLib.MainContext.default()
        for _ in range(60):
            ctx.iteration(False)
        rows = _walk(pop, "modelbutton", [])
        self.assertEqual(len(rows), 3)
        xs = []
        for r in rows:
            ok, p = _label(r).compute_point(r, __import__("gi.repository.Graphene", fromlist=["Point"]).Point())
            xs.append(round(p.x))
        self.assertEqual(len(set(xs)), 1, xs)                  # the same left edge
        checks = [getattr(r, "_sonata_check", None) for r in rows]
        self.assertIsNone(checks[0])                           # a plain row: no check
        self.assertEqual(checks[1].get_opacity(), 1)           # on
        self.assertEqual(checks[2].get_opacity(), 0)           # off
        self.assertIs(rows[1].get_last_child(), checks[1])     # after the text
        pop.popdown()
        win.destroy()
