"""Vini: the picture rose on the login screen, then dropped back to its old
place on the "loading the desktop" screen right after logging in. The
welcome screen's column is the login column (same rows, same lift)."""
import os
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def avatar_y(win):
    """Top of the biggest picture-sized widget (the avatar) in the window."""
    best = None
    stack = [win]
    while stack:
        w = stack.pop()
        if w.get_width() >= 100 and abs(w.get_width() - w.get_height()) <= 2 and w.get_width() <= 120 \
                and w.get_mapped():
            ok, b = w.compute_bounds(win)
            if ok and (best is None or b.get_y() < best):
                best = b.get_y()
        c = w.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return best


class WelcomeMatchesLoginTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def check(self, users, sessions):
        from sonata2.shell import greeter as G, welcome as W
        from sonata2.shell.loginui import lift
        monitor = Gdk.Display.get_default().get_monitors().get_item(0)
        with mock.patch.dict(os.environ, {"SONATA_GREETER_FAKE": "1"}), \
                mock.patch.object(G, "users", lambda: users), mock.patch.object(G, "sessions", lambda: sessions), \
                mock.patch.object(G, "load_state", lambda: {}), \
                mock.patch.object(G.Greeter, "_restore_modes", lambda self: None), \
                mock.patch.object(G.Greeter, "_sleep", lambda self, s: None), \
                mock.patch.object(Gtk.Window, "fullscreen_on_monitor",       # (no window manager here)
                                  lambda self, m: self.set_size_request(1920, 1080)):
            g = G.Greeter(None)
            settle(600)
            g.slot.set_visible_child_name("progress")          # the password accepted: the spinner turns
            settle(1500)                                        # (the column rises in: measured once it stops)
            login = avatar_y(g.windows[0])
            w = Gtk.Window(decorated=False)
            over = Gtk.Overlay()
            over.set_child(Gtk.Box())
            over.add_overlay(lift(W.login_column(), monitor))
            w.set_child(over)
            w.set_size_request(1920, 1080)
            w.present()
            settle(1500)
            welcome = avatar_y(w)
            for x in g.windows + [w]:
                x.destroy()
        self.assertIsNotNone(login)
        self.assertGreater(login, 100)
        self.assertAlmostEqual(welcome, login, delta=2)

    def test_one_user(self):
        from sonata2.shell import greeter as G
        self.check([G.User("vini", "Vini")], [])

    def test_with_the_links_row(self):
        from sonata2.shell import greeter as G
        s = [G.Session("a", "Sonata", "x", "Sonata"), G.Session("b", "Plasma", "y", "KDE")]
        with mock.patch.object(G, "sessions", lambda: s):
            self.check([G.User("vini", "Vini")], s)


if __name__ == "__main__":
    unittest.main()
