"""Lock App (Vini): a locked app asks for the login password when it's
opened from anywhere in Sonata (every launch goes through apps.py's
wrapper); right -> it opens, wrong -> asked again, nothing opens. Login
items and apps reopened after a crash don't ask. The Dock and Launchpad
menus lock / unlock (unlocking asks too) and the icons show a padlock."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from sonata2 import applock, apps, config  # noqa: E402
from sonata2.ui import dialog  # noqa: E402


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeInfo:
    def __init__(self, app_id="spotify.desktop"):
        self.app_id, self.launched = app_id, 0

    def get_id(self):
        return self.app_id

    def get_display_name(self):
        return "Spotify"


class ModelTest(unittest.TestCase):
    def setUp(self):
        config.save(applock.NAME, {})

    def test_lock_and_unlock(self):
        self.assertFalse(applock.locked("spotify.desktop"))
        applock.set_locked("spotify.desktop", True)
        self.assertTrue(applock.locked("spotify.desktop"))
        self.assertTrue(applock.locked("spotify"))                # either form of the id
        applock.set_locked("spotify", False)
        self.assertFalse(applock.locked("spotify.desktop"))
        self.assertFalse(applock.locked(None))

    def test_bad_file_is_nothing_locked(self):
        config.save(applock.NAME, {"apps": "spotify.desktop"})
        self.assertFalse(applock.locked("spotify.desktop"))


class GateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        config.save(applock.NAME, {"apps": ["spotify.desktop"]})
        self.info, self.ran = FakeInfo(), []

    def gate(self, ok):
        asked = []

        def ask(heading, body, on_ok, *a, **k):
            asked.append(heading)
            if ok:
                on_ok()
        with mock.patch.object(dialog, "ask_password", ask):
            took = applock.gate(self.info, lambda: self.ran.append(applock._trusted))
        return took, asked

    def test_locked_asks_then_opens(self):
        took, asked = self.gate(ok=True)
        self.assertTrue(took)
        self.assertEqual(asked, ["“Spotify” is locked"])
        self.assertEqual(self.ran, [1])             # opened inside trusted(): never asked twice

    def test_wrong_password_never_opens(self):
        took, _ = self.gate(ok=False)
        self.assertTrue(took)
        self.assertEqual(self.ran, [])

    def test_unlocked_and_trusted_dont_ask(self):
        self.info.app_id = "other.desktop"
        self.assertEqual(self.gate(ok=True), (False, []))
        self.info.app_id = "spotify.desktop"
        with applock.trusted():
            self.assertEqual(self.gate(ok=True), (False, []))

    def test_every_launch_goes_through_it(self):
        """apps.py's wrapper (Dock, Launchpad, Spotlight...) asks first."""
        app = Gio.DesktopAppInfo.new_from_keyfile(_keyfile())
        with mock.patch.object(applock, "gate", return_value=True) as gate:
            self.assertTrue(app.launch([], None))   # (true: nothing really runs)
        gate.assert_called_once()

    def test_login_items_and_reopen_are_trusted(self):
        import inspect
        from sonata2 import autostart, open_apps
        self.assertIn("applock.trusted()", inspect.getsource(autostart))
        self.assertIn("applock.trusted()", inspect.getsource(open_apps.reopen))


def _keyfile():
    kf = GLib.KeyFile()
    data = "[Desktop Entry]\nType=Application\nName=X\nExec=true\n"
    kf.load_from_data(data, len(data), GLib.KeyFileFlags.NONE)
    return kf


class PasswordAlertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def sheet(self, on_ok, results):
        from sonata2.shell import authsheet
        with mock.patch.object(authsheet.layer, "overlay_fullscreen", return_value=True):
            d = dialog.ask_password("“X” is locked", "", on_ok)
        self.addCleanup(d.close)
        self.answers = mock.patch("sonata2.pam.check_async", lambda pw, done: done(next(results)))
        self.answers.start()
        self.addCleanup(self.answers.stop)
        return d

    def test_enter_checks_without_closing(self):
        """Every login-password prompt is the password sheet (Vini: like the
        updates' one); a wrong password asks again in it."""
        from sonata2.shell import authsheet
        opened = []
        d = self.sheet(lambda: opened.append(1), iter([False, True]))
        self.assertIsInstance(d, authsheet.PasswordSheet)
        d.entry.set_text("wrong")
        d.entry.emit("activate")
        self.assertEqual(d.error.get_label(), "Wrong password")
        self.assertTrue(d.error.get_visible())
        self.assertEqual(d.entry.get_text(), "")
        self.assertFalse(d.finished)
        self.assertEqual(opened, [])
        d.entry.set_text("right")
        d.entry.emit("activate")
        self.assertEqual(opened, [1])
        self.assertTrue(d.finished)

    def test_ok_button_checks_and_cancel_closes(self):
        opened = []
        d = self.sheet(lambda: opened.append(1), iter([True]))
        d.entry.set_text("right")
        d.ok.emit("clicked")
        self.assertEqual(opened, [1])
        d = self.sheet(lambda: opened.append(2), iter([True]))
        d.cancel()
        self.assertTrue(d.finished)
        self.assertEqual(opened, [1])


class IconsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def test_dock_icon_draws_padlock(self):
        from sonata2.shell import dock as D
        icon = D.DockIcon(Gio.ThemedIcon.new("application-x-executable"), 48)
        icon.set_locked(True)
        snap = Gtk.Snapshot()
        with mock.patch("sonata2.shell.dock_folder.draw_lock_badge") as draw:
            icon.do_snapshot(snap)
        draw.assert_called_once_with(snap, 48)

    def test_badge_draws(self):
        from sonata2.shell import dock_folder as F
        snap = Gtk.Snapshot()
        F.draw_lock_badge(snap, 64)
        self.assertIsNotNone(snap.to_node())

    def test_menu_items(self):
        from sonata2.shell import dock_menu
        config.save(applock.NAME, {})
        info = FakeInfo()
        item = dock_menu.lock_item(info)
        self.assertEqual(item.label, "Lock App")
        item.on_activate()
        self.assertTrue(applock.locked("spotify.desktop"))
        around = []
        item = dock_menu.lock_item(info, lambda fn: around.append(fn))
        self.assertEqual(item.label, "Unlock App…")
        with mock.patch.object(applock, "ask_unlock") as ask:
            item.on_activate()
            ask.assert_not_called()                 # Launchpad closes first
            around[0]()
        ask.assert_called_once_with(info)


if __name__ == "__main__":
    unittest.main()
