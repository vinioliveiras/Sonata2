"""Vini (Files): Go > Recents showed an error (behind the window), Go >
Connect to Server did nothing and New Terminal at Folder did nothing.
python3 -m unittest tests.test_files_go_places (xvfb)"""
import os
import stat
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.files import folder  # noqa: E402


def spin(cond, ms=4000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


class CanonicalTest(unittest.TestCase):
    def test_gvfs_spelling_is_our_place_again(self):
        """With gvfs, a GFile turns "sonata:recents" into "sonata:///recents"."""
        C = folder.canonical
        self.assertEqual(C("sonata:///recents"), folder.RECENTS)
        self.assertEqual(C("sonata://recents"), folder.RECENTS)
        self.assertEqual(C("sonata:///connect"), folder.CONNECT)
        self.assertEqual(C("sonata:///tag/My%20Tag"), "sonata:tag/My Tag")
        self.assertIn(C("sonata:///applications"), folder.VIRTUAL)
        for same in (folder.RECENTS, "file:///tmp", "smb://nas/x", None):
            self.assertEqual(C(same), same)

    def test_round_trip_through_a_gfile(self):
        """Whatever VFS is in use, the place a launched Files gets back is ours."""
        for place in (folder.RECENTS, folder.CONNECT, "sonata:tag/Red"):
            self.assertEqual(folder.canonical(Gio.File.new_for_commandline_arg(place).get_uri()), place)


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.filesgotest")
        cls.app.register(None)

    def make(self, uri):
        from sonata2.files.window import FilesWindow
        w = FilesWindow(self.app, uri)
        self.addCleanup(w.destroy)
        return w

    def test_recents_from_the_menu_bar_opens(self):
        with mock.patch.object(ui.dialog, "alert") as alert:
            w = self.make("sonata:///recents")
            w.present()
            self.assertTrue(spin(lambda: w.folder.uri == folder.RECENTS))
            w.go("sonata:///recents")                       # the same place: no new history step
            self.assertEqual(w.history, [folder.RECENTS])
        alert.assert_not_called()

    def test_connect_to_server_from_the_menu_bar(self):
        from sonata2.files.window import FilesWindow
        with mock.patch.object(FilesWindow, "connect_to_server") as connect:
            w = self.make("sonata:///connect")
            self.assertEqual(w.history[0], Gio.File.new_for_path(GLib.get_home_dir()).get_uri())
            w.present()
            self.assertTrue(spin(lambda: connect.call_count == 1))
            spin(lambda: False, 200)
            self.assertEqual(connect.call_count, 1)

    def test_terminal_opens_at_the_folder(self):
        from sonata2.files import window
        d = tempfile.mkdtemp()
        w = self.make(Gio.File.new_for_path(d).get_uri())
        with mock.patch.object(window.GLib, "spawn_async") as spawn:
            w._terminal(Gio.File.new_for_path(d))
        argv = spawn.call_args.args[0]
        self.assertEqual(spawn.call_args.kwargs["working_directory"], d)
        self.assertEqual(argv[-2:], ["terminal", d])         # Sonata's own Terminal first

    def test_terminal_fallbacks_and_none(self):
        from sonata2.files import window
        bin_dir, d = tempfile.mkdtemp(), tempfile.mkdtemp()
        kitty = os.path.join(bin_dir, "kitty")
        with open(kitty, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(kitty, stat.S_IRWXU)
        with mock.patch.object(window, "_sonata_terminal_ok", return_value=False), \
                mock.patch.dict(os.environ, {"PATH": bin_dir, "TERMINAL": ""}):
            self.assertEqual(window.terminal_commands(d), [[kitty, "--directory", d]])
        with mock.patch.object(window, "_sonata_terminal_ok", return_value=False), \
                mock.patch.dict(os.environ, {"PATH": tempfile.mkdtemp(), "TERMINAL": ""}), \
                mock.patch.object(ui.dialog, "alert") as alert:
            w = self.make(Gio.File.new_for_path(d).get_uri())
            w._terminal(Gio.File.new_for_path(d))
        alert.assert_called_once()                            # says why, over its window
        self.assertIs(alert.call_args.kwargs["parent"], w)


class AlertAboveItsWindowTest(unittest.TestCase):
    def test_raised_once_the_owner_shows(self):
        """An alert raised while its window was still opening stayed behind it."""
        from sonata2.ui import dialog

        class Owner(Gtk.Window):
            active = False

            def is_active(self):
                return self.active
        owner, alert = Owner(), Gtk.Window()
        alert.set_visible(True)
        with mock.patch.object(alert, "present") as present:
            dialog._raise_when_owner_shows(owner, alert)
            owner.notify("is-active")                         # not active yet: nothing
            present.assert_not_called()
            owner.active = True
            owner.notify("is-active")
            present.assert_called_once()
            owner.notify("is-active")                         # once only
            present.assert_called_once()

    def test_files_alerts_have_their_window(self):
        """Every Files alert names its window (none floats free, behind it)."""
        import re
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sonata2", "files")
        for name in sorted(os.listdir(root)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as f:
                src = f.read()
            for m in re.finditer(r"ui\.dialog\.alert\(", src):
                depth, i = 1, m.end()
                while depth:
                    depth += {"(": 1, ")": -1}.get(src[i], 0)
                    i += 1
                self.assertRegex(src[m.end():i], r"parent=(?!None)", f"{name}: {src[m.start():m.start() + 70]}")


if __name__ == "__main__":
    unittest.main()
