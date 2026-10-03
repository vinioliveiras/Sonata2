"""Double-clicking a background app's icon in the menu bar opens that app
(Vini): its window comes forward, else the app is launched; an icon whose
app isn't known gets its own Activate."""
import os
import tempfile
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.shell import tray as T  # noqa: E402


class AppOfPidTest(unittest.TestCase):
    def test_from_the_executable_or_command(self):
        root = tempfile.mkdtemp()
        d = os.path.join(root, "42")
        os.makedirs(d)
        os.symlink("/opt/discord/Discord", os.path.join(d, "exe"))
        with open(os.path.join(d, "cmdline"), "wb") as f:
            f.write(b"/opt/discord/Discord\0--type=renderer\0")
        with open(os.path.join(d, "comm"), "w") as f:
            f.write("Discord\n")
        with mock.patch("sonata2.apps.match_app_id", side_effect=lambda n: "discord" if n == "Discord" else None):
            self.assertEqual(T.app_of_pid(42, root), "discord")
        with mock.patch("sonata2.apps.match_app_id", return_value=None):
            self.assertIsNone(T.app_of_pid(42, root))
            self.assertIsNone(T.app_of_pid(7, root))              # gone: nothing, no error


class OpenAppTest(unittest.TestCase):
    def item(self, did):
        it = mock.Mock(tooltip="Steam")
        it.app.side_effect = lambda done: done(did)
        return it

    def open(self, item, forward, info=None):
        box = types.SimpleNamespace(_point=lambda b, *a: (5, 6))
        with mock.patch("sonata2.shell.notifications.bring_forward", return_value=forward) as bf, \
                mock.patch("sonata2.apps.lookup", return_value=info) as lk:
            T.TrayBox.open_app(box, Gtk.Button(), item)
        return bf, lk

    def test_running_app_comes_forward(self):
        it = self.item("steam")
        bf, lk = self.open(it, True)
        bf.assert_called_once_with("steam", "Steam")
        lk.assert_not_called()
        it.activate.assert_not_called()

    def test_app_without_window_is_launched(self):
        it, info = self.item("steam"), mock.Mock()
        self.open(it, False, info)
        info.launch.assert_called_once()
        it.activate.assert_not_called()

    def test_unknown_app_gets_activate(self):
        it = self.item(None)
        self.open(it, False)
        it.activate.assert_called_once_with(5, 6)

    def test_double_click_only(self):
        """A single left click keeps its Activate / menu; the second press opens the app."""
        calls = []
        box = types.SimpleNamespace(open_app=lambda b, i: calls.append("open"),
                                    show_menu=lambda *a: calls.append("menu"), _single=0)
        gest = mock.Mock()
        gest.get_current_button.return_value = 1
        T.TrayBox._press(box, gest, Gtk.Button(), 0, 0, mock.Mock(), 1)
        self.assertEqual(calls, [])
        T.TrayBox._press(box, gest, Gtk.Button(), 0, 0, mock.Mock(), 2)
        self.assertEqual(calls, ["open"])
        gest.set_state.assert_called_with(Gtk.EventSequenceState.CLAIMED)   # no second Activate


    def test_first_click_waits_for_a_second(self):
        """Vini: double-clicking Steam's icon picked an item of the menu the
        first click had opened; Spotify's window came and went (its Activate
        toggles). The single click now waits the double-click time."""
        from gi.repository import GLib
        calls = []
        box = types.SimpleNamespace(open_app=lambda b, i: calls.append("open"),
                                    _click=lambda b, i: calls.append("single"), _single=0)
        box._single_click = lambda b, i: T.TrayBox._single_click(box, b, i)
        gest = mock.Mock()
        gest.get_current_button.return_value = 1
        btn, item = Gtk.Button(), mock.Mock()

        def settle(ms):
            end = GLib.get_monotonic_time() + ms * 1000
            while GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
        # double-click: press, release (clicked), press #2, release (clicked)
        T.TrayBox._press(box, gest, btn, 0, 0, item, 1)
        T.TrayBox._left(box, btn, item)
        T.TrayBox._press(box, gest, btn, 0, 0, item, 2)
        T.TrayBox._left(box, btn, item)
        settle(T.double_click_ms() + 100)
        self.assertEqual(calls, ["open"])                 # no Activate, no menu
        # a single click: its action, once the double-click time is over
        T.TrayBox._left(box, btn, item)
        self.assertEqual(calls, ["open"])
        settle(T.double_click_ms() + 100)
        self.assertEqual(calls, ["open", "single"])


if __name__ == "__main__":
    unittest.main()
