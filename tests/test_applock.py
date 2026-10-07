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

    def test_enter_checks_without_closing(self):
        opened = []
        results = iter([False, True])
        with mock.patch("sonata2.pam.check_async", lambda pw, done: done(next(results))), \
                mock.patch.object(dialog, "alert") as alert:
            dlg = alert.return_value
            d = dialog.ask_password("“X” is locked", "", lambda: opened.append(1))
            d.entry.set_text("wrong")
            d.entry.emit("activate")
            self.assertEqual(d.hint.get_label(), "Wrong password")
            self.assertTrue(d.hint.get_visible())
            self.assertEqual(d.entry.get_text(), "")
            self.assertEqual(opened, [])
            d.entry.set_text("right")
            d.entry.emit("activate")
        self.assertEqual(opened, [1])
        dlg.close.assert_called_once()

    def test_button_wrong_asks_again(self):
        with mock.patch("sonata2.pam.check_async", lambda pw, done: done(False)), \
                mock.patch.object(dialog, "alert") as alert:
            dialog.ask_password("“X” is locked", "", lambda: None)
            entry = alert.return_value.entry
            on_response = alert.call_args[0][3]
            with mock.patch.object(dialog, "ask_password") as again:
                entry.set_text("wrong")
                on_response("ok")
            self.assertTrue(again.call_args.kwargs["wrong"])


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
