"""Vini (Files vs Finder / Windows): no path bar, no way to type where to go.
A path bar at the bottom (View > Show/Hide Path Bar, Ctrl+Alt+P) and Go to
Folder (Ctrl+Shift+G, Ctrl+L)."""
import inspect
import os
import tempfile
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio  # noqa: E402

from sonata2.files import pathbar as P  # noqa: E402


class PathBarTest(unittest.TestCase):
    def test_crumbs_from_home(self):
        c = P.crumbs(Gio.File.new_for_path("/home/vini/Documents/Work").get_uri(), home="/home/vini")
        self.assertEqual([n for _u, n, _i in c], ["vini", "Documents", "Work"])
        self.assertEqual(c[0][2], "user-home-symbolic")
        self.assertTrue(c[-1][0].endswith("/home/vini/Documents/Work"))

    def test_crumbs_outside_home(self):
        c = P.crumbs("file:///etc/X11", home="/home/vini")
        self.assertEqual([n for _u, n, _i in c], ["Computer", "etc", "X11"])
        self.assertEqual(len(P.crumbs("trash:///", home="/home/vini")), 1)

    def test_bar(self):
        b = P.PathBar(lambda u: None)
        b.set_uri(Gio.File.new_for_path(os.path.expanduser("~")).get_uri())
        self.assertIsNotNone(b.box.get_first_child())

    def test_right_click_copy_path(self):
        """Vini: right-click the path bar > Copy Path -- the folder under the
        pointer, or the one shown on the bar's empty part."""
        from unittest import mock
        from gi.repository import GLib, Gtk
        from sonata2 import ui
        d = tempfile.mkdtemp()
        sub = os.path.join(d, "Docs")
        os.mkdir(sub)
        win = Gtk.Window()
        b = P.PathBar(lambda u: None)
        win.set_child(b)
        win.present()
        b.set_uri(Gio.File.new_for_path(sub).get_uri())
        crumbs = []
        c = b.box.get_first_child()
        while c is not None:
            if getattr(c, "crumb_uri", None):
                crumbs.append(c)
            c = c.get_next_sibling()
        menus = []
        g = Gtk.GestureClick()
        with mock.patch.object(ui.menu, "popup", lambda w, sections, **kw: menus.append(sections)):
            with mock.patch.object(b, "pick", return_value=crumbs[-2].get_child()):      # the parent folder
                b._menu(g, 1, 5, 5)
            with mock.patch.object(b, "pick", return_value=b):                           # the empty part
                b._menu(g, 1, 300, 5)
        self.assertEqual([m[0][0].label for m in menus], ["Copy Path", "Copy Path"])
        got = []
        for m in menus:
            m[0][0].on_activate()
            b.get_clipboard().read_text_async(None, lambda cb, r: got.append(cb.read_text_finish(r)))
            ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + 1_000_000
            n = len(got)
            while len(got) == n and GLib.get_monotonic_time() < end:
                ctx.iteration(False)
        self.assertEqual(got, [d, sub])
        win.destroy()

    def test_resolve(self):
        d = tempfile.mkdtemp()
        open(os.path.join(d, "f.txt"), "w").close()
        self.assertEqual(P.resolve(d), (Gio.File.new_for_path(d).get_uri(), None))
        self.assertEqual(P.resolve(os.path.join(d, "f.txt")), (Gio.File.new_for_path(d).get_uri(), "f.txt"))
        self.assertEqual(P.resolve("f.txt", Gio.File.new_for_path(d).get_uri())[1], "f.txt")   # relative
        self.assertIsNotNone(P.resolve("~"))
        self.assertIsNone(P.resolve(os.path.join(d, "nope")))
        self.assertIsNone(P.resolve(""))

    def test_wired(self):
        from sonata2.files import window
        src = inspect.getsource(window)
        for k in ('"<Control><Shift>g"', '"<Control>l", self.edit_address', '"<Control><Alt>p"', "Show Path Bar", "self.pathbar.set_uri("):
            self.assertIn(k, src)
        self.assertTrue(window.DEFAULTS["path_bar"])


class AddressTest(unittest.TestCase):
    """Vini: Files had no way to type the address. Ctrl+L or a click on the
    path bar's empty part turns it into a field; Return goes there."""

    def test_edit_then_go(self):
        bar = P.PathBar(lambda u: None)
        d = tempfile.mkdtemp()
        bar.set_uri(Gio.File.new_for_path(d).get_uri())
        went = []
        bar.edit(d, went.append)
        self.assertIs(bar.stack.get_visible_child(), bar.entry)
        bar.entry.set_text("/tmp")
        bar.entry.emit("activate")
        self.assertEqual(went, ["/tmp"])
        self.assertIsNone(bar.entry)
        self.assertIs(bar.stack.get_visible_child(), bar.box)  # the folders are back

    def test_address_field_focus_ring_not_cut_off(self):
        """Vini: the address field's focus ring was cut off at every edge --
        the bar is 24 px high and scrolls sideways, and the 3 px ring was
        drawn around the field. In the path bar it is drawn inside it."""
        import re
        from sonata2.ui import theme
        bar = P.PathBar(lambda u: None)
        bar.edit("/tmp", lambda v: None)
        entry = bar.entry
        self.assertTrue(entry.has_css_class("sonata-field"))
        up = entry.get_parent()
        while up is not None and not up.has_css_class("fs-pathbar"):
            up = up.get_parent()
        self.assertIsNotNone(up)                                 # the rule below reaches it
        css = theme._templates["files-pathbar"][0]
        rule = re.search(r"\.fs-pathbar entry\.sonata-field:focus-within\s*\{([^}]*)\}", css)
        self.assertIsNotNone(rule)                               # (more specific than every field's ring)
        shadow = re.search(r"box-shadow:([^;]*);", rule.group(1)).group(1)
        rings = [s.strip() for s in re.split(r",(?![^(]*\))", shadow)]
        self.assertTrue(rings and all(r.startswith("inset") for r in rings), rings)

    def test_hidden_files_wiring(self):
        from sonata2.files import window
        self.assertFalse(window.DEFAULTS["show_hidden"])         # hidden by default (Vini)
        src = inspect.getsource(window.FilesWindow)
        self.assertIn("folderprefs.remember(tab.uri, hidden=on)", src)
        self.assertIn('"Show Hidden Files"', src)


if __name__ == "__main__":
    unittest.main()
