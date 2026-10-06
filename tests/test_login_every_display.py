"""Vini: after sleep, the lock screen showed no name or password field. The
field lived on one display only; the display left and came back (sleep, a
cable), and the copy made for it had none. Now every display -- the ones
there at the start and the ones that come later -- has the whole login,
and typing on any display types in all of them (lock and login screens)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from sonata2.shell.loginui import Mirror, shake  # noqa: E402


def app(name):
    a = Adw.Application(application_id=f"io.github.vinioliveiras.sonata2.test.every.{name}")
    a.register(None)
    return a


class MirrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def test_fields_share_text_and_calls_reach_all(self):
        a, b = Gtk.PasswordEntry(), Gtk.PasswordEntry()
        m = Mirror(a, b)
        b.set_text("secret")
        self.assertEqual(a.get_text(), "secret")
        m.set_sensitive(False)
        self.assertFalse(a.get_sensitive() or b.get_sensitive())
        shake(m)
        self.assertEqual((a.get_text(), b.get_text()), ("", ""))
        self.assertTrue(a.get_sensitive() and b.get_sensitive())
        m.remove(a)
        b.set_text("x")
        self.assertEqual(a.get_text(), "")                  # a gone display isn't touched

    def test_empty_mirror_is_harmless(self):
        m = Mirror()
        self.assertFalse(m)
        m.add_css_class("gr-leave")                          # nothing, no error


class LockEveryDisplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def lock(self):
        from sonata2.shell import lock as L
        ls = L.LockScreen.__new__(L.LockScreen)
        ls.app, ls.lock, ls.texture, ls.windows = app("lock"), mock.Mock(), None, []
        self.addCleanup(lambda: [w.destroy() for w in list(ls.windows)])
        return ls

    def test_every_display_and_a_returning_one_have_the_field(self):
        ls = self.lock()
        mon = Gdk.Display.get_default().get_monitors().get_item(0)
        w1 = ls._window(mon, primary=True)
        w2 = ls._window(mon)                                 # a second display
        self.assertEqual(len(ls.entry.items), 2)
        self.assertEqual(len(ls.power.items), 2)
        for w, e in zip((w1, w2), ls.entry.items):
            self.assertIs(e.get_root(), w)
        ls.entry.items[1].set_text("pw")
        self.assertEqual(ls.entry.get_text(), "pw")
        # the main display goes away (sleep, cable) and comes back as a new one
        parts = {"column": ls.column.items[0], "entry": ls.entry.items[0], "spinner": ls.spinner.items[0],
                 "slot": ls.slot.items[0], "hint": ls.hint.items[0], "power": ls.power.items[0]}
        ls._gone(w1, parts)
        self.assertEqual(len(ls.entry.items), 1)
        w3 = ls._window(mon)
        self.assertEqual(len(ls.entry.items), 2)
        self.assertIs(ls.entry.items[1].get_root(), w3)
        self.assertEqual(ls.entry.items[1].get_text(), "pw")  # what was typed stays
        ls.guard = mock.Mock()
        with mock.patch.object(GLib, "timeout_add", lambda ms, fn: None):
            ls._done(True)
        self.assertTrue(all(c.has_css_class("gr-leave") for c in ls.column))

    def test_displays_added_later_get_the_whole_login(self):
        import inspect
        from sonata2.shell import greeter, lock
        for mod in (lock, greeter):
            src = inspect.getsource(mod)
            self.assertIn('"items-changed"', src, mod.__name__)
            self.assertIn('"invalidate"', src, mod.__name__)
        self.assertNotIn("if primary:\n            over.add_overlay(self._login())", inspect.getsource(lock))


class GreeterEveryDisplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def test_each_display_has_the_login_and_follows_the_user(self):
        from sonata2.shell import greeter as G
        d = tempfile.mkdtemp()
        for name, value in (("STATE", os.path.join(d, "state.json")), ("WAITS", os.path.join(d, "waits.json")),
                            ("users", lambda: [G.User("vini", "Vini"), G.User("ana", "Ana")]),
                            ("sessions", lambda: [G.Session("sonata", "Sonata", ["sonata"], "Sonata")])):
            p = mock.patch.object(G, name, value)
            p.start()
            self.addCleanup(p.stop)
        g = G.Greeter(app("greeter"))
        self.addCleanup(lambda: [w.destroy() for w in list(g.windows)])
        mon = Gdk.Display.get_default().get_monitors().get_item(0)
        g._window(mon)                                       # a second display
        self.assertEqual(len(g.center.items), 2)
        g._pick(g.users[0])
        self.assertEqual(len(g.entry.items), 2)
        self.assertEqual({e.get_root() for e in g.entry}, set(g.windows))
        g.entry.items[0].set_text("pw")
        self.assertEqual(g.entry.items[1].get_text(), "pw")


if __name__ == "__main__":
    unittest.main()
