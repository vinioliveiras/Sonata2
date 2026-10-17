"""Menu bar > Window with a very long window title (Vini: Google Chrome's
"(3) GOODBYE FREEDOM! - Google is going to LOCK DOWN Android. - YouTube -
Google Chrome" made the whole menu about 830 px wide). A long row ends in
"…" at ui.menu.LABEL_CHARS, short rows keep their width, the check stays at
the right edge with every other row's, and the whole text is the tooltip.
Run: xvfb-run -a python3 -m unittest tests.test_menu_long_window_title
(SONATA_SHOT=/path.png also saves a picture of the menu.)"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Graphene, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.wl import toplevels as TL  # noqa: E402

from tests.test_window_menu import manager  # noqa: E402

LONG = "(3) GOODBYE FREEDOM! - Google is going to LOCK DOWN Android. - YouTube - Google Chrome"


def settle(n=120):
    ctx = GLib.MainContext.default()
    for _ in range(n):
        ctx.iteration(False)


def walk(w, name, out):
    w = w.get_first_child()
    while w is not None:
        if w.get_css_name() == name:
            out.append(w)
        walk(w, name, out)
        w = w.get_next_sibling()
    return out


def text_label(row):
    return next(c for c in walk(row, "label", []) if c.get_parent() is row)


def right_edge(w, to):
    ok, p = w.compute_point(to, Graphene.Point().init(w.get_width(), 0))
    return round(p.x)


def shot(pop, path):
    """The menu as it is drawn, into a PNG."""
    paint = Gtk.WidgetPaintable.new(pop)
    w, h = pop.get_width(), pop.get_height()
    snap = Gtk.Snapshot()
    paint.snapshot(snap, w, h)
    node = snap.to_node()
    tex = pop.get_native().get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    tex.save_to_png(path)


class LongWindowTitleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not Gtk.init_check():
            raise unittest.SkipTest("no display")
        Adw.init()
        ui.setup()

    def test_long_window_title_ends_in_ellipsis(self):
        from sonata2.shell import topbar as T
        bar = T.Bar(None)
        bar.manager = manager([], ("google-chrome", LONG, {TL.ACTIVATED}),
                              ("google-chrome", "New Tab - Google Chrome", set()))
        win = Gtk.Window(default_width=1200, default_height=700)
        btn = Gtk.Button(label="Window", halign=Gtk.Align.START, valign=Gtk.Align.START)
        win.set_child(btn)
        win.present()
        settle()
        pop = bar._window_menu(btn)                  # the real menu, as the menu bar opens it
        settle(300)
        try:
            rows = {r.get_property("text"): r for r in walk(pop, "modelbutton", [])}
            long_label = text_label(rows[LONG])
            # at most LABEL_CHARS wide (GTK's character: the wider of a letter and a digit), cut with "…"
            m = long_label.get_pango_context().get_metrics(None, None)
            char = max(m.get_approximate_char_width(), m.get_approximate_digit_width()) / 1024
            cap = ui.menu.LABEL_CHARS * char
            self.assertLessEqual(long_label.get_width(), cap + 1)
            self.assertTrue(long_label.get_layout().is_ellipsized())
            self.assertLess(pop.get_width(), 480)     # it was ~830 px
            # short rows keep their whole text and their own width
            for name in ("Minimize", "Bring All to Front", "New Tab - Google Chrome"):
                lbl = text_label(rows[name])
                self.assertFalse(lbl.get_layout().is_ellipsized(), name)
                self.assertLess(lbl.get_width(), cap, name)
            # the check is still last, at the same right edge as the other rows
            check = rows[LONG]._sonata_check
            self.assertEqual(check.get_opacity(), 1)
            self.assertIs(rows[LONG].get_last_child(), check)
            other = rows["New Tab - Google Chrome"]._sonata_check
            self.assertEqual(right_edge(check, pop), right_edge(other, pop))
            self.assertEqual(right_edge(check, pop), right_edge(rows["Minimize"], pop))
            # a cut row shows its whole text on hover
            self.assertTrue(long_label.get_has_tooltip())
            if os.environ.get("SONATA_SHOT"):
                shot(pop, os.environ["SONATA_SHOT"])
        finally:
            pop.popdown()
            settle()
            win.destroy()

    def test_menu_without_long_rows_is_unchanged(self):
        """Short menus keep their own width (no row is cut)."""
        win = Gtk.Window(default_width=600, default_height=400)
        btn = Gtk.Button(label="x", halign=Gtk.Align.START, valign=Gtk.Align.START)
        win.set_child(btn)
        win.present()
        settle()
        I = ui.menu.Item
        pop = ui.menu.popup(btn, [[I("Open", lambda: 0), I("Keep in Dock", lambda s: 0, checked=True)]])
        settle(300)
        try:
            for r in walk(pop, "modelbutton", []):
                self.assertFalse(text_label(r).get_layout().is_ellipsized())
        finally:
            pop.popdown()
            settle()
            win.destroy()


if __name__ == "__main__":
    unittest.main()
