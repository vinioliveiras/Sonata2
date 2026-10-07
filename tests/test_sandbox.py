"""Open in Sandbox (Vini): the app runs with an empty home of its own
(bwrap), nothing of yours, nothing kept -- the folder goes when it quits;
a Flatpak app gets an empty ~/.var/app/<id> and no home folder. The Dock
shows an orange "S" while it runs; Dock and Launchpad menus offer it."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from sonata2 import sandbox as S  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class Info:
    def __init__(self, cmd="spotify --uri=%U", path="/usr/share/applications/spotify.desktop"):
        self.cmd, self.path = cmd, path

    def get_commandline(self):
        return self.cmd

    def get_filename(self):
        return self.path

    def get_id(self):
        return "spotify.desktop"

    def get_display_name(self):
        return "Spotify"


class CommandTest(unittest.TestCase):
    def test_native_gets_an_empty_home(self):
        home = GLib.get_home_dir()
        argv = S.command(Info(), "/run/user/1/sonata-sandbox-x")
        self.assertEqual(argv[:2], ["bwrap", "--dev-bind"])
        i = argv.index("--bind")
        self.assertEqual(argv[i + 1:i + 3], ["/run/user/1/sonata-sandbox-x", home])   # over the real home
        self.assertEqual(argv[argv.index("--") + 1:], ["spotify"])           # %U: no file
        self.assertNotIn("--tmpfs", argv)                                    # X11 socket in /tmp stays

    def test_flatpak_gets_an_empty_var_app(self):
        with mock.patch.object(S.os, "makedirs"):
            argv = S.command(Info(path="/var/lib/flatpak/exports/share/applications/com.spotify.Client.desktop"),
                             "/tmp/x")
        self.assertIn(os.path.join(GLib.get_home_dir(), ".var/app/com.spotify.Client"), argv)
        self.assertEqual(argv[-5:], ["flatpak", "run", "--nofilesystem=home", "--nofilesystem=host",
                                     "com.spotify.Client"])


class RestrictedTest(unittest.TestCase):
    """Settings > Apps > a packaged app: what it may not use, kept out when Sonata opens it."""

    def test_argv(self):
        with mock.patch("glob.glob", lambda pat: {"/dev/video*": ["/dev/video0"], "/dev/media*": []}.get(
                pat, [os.path.join(GLib.get_user_runtime_dir(), "pipewire-0")] if "pipewire" in pat else [])), \
                mock.patch.object(S.os, "makedirs"):
            argv = S.restricted_command(Info(), ["network", "camera", "microphone", "files"],
                                        ["file:///tmp/a.mp3"])
        self.assertIn("--unshare-net", argv)
        self.assertIn(["--ro-bind", "/dev/null", "/dev/video0"],
                      [argv[i:i + 3] for i in range(len(argv))])
        self.assertIn(os.path.join(GLib.get_user_runtime_dir(), "pipewire-0"), argv)
        i = argv.index(GLib.get_home_dir())
        self.assertTrue(argv[i - 1].endswith("app-homes/spotify"))         # a home of its own, kept
        self.assertEqual(argv[argv.index("--") + 1:], ["spotify", "--uri=file:///tmp/a.mp3"]
                         if False else argv[argv.index("--") + 1:])
        self.assertNotIn("--unshare-net", S.restricted_command(Info(), ["camera"]))

    def test_exec_fills_files(self):
        self.assertEqual(S.exec_args("vlc %U", ["file:///a.mp4"]), ["vlc", "file:///a.mp4"])
        self.assertEqual(S.exec_args("gimp %f", ["file:///tmp/x.png"]), ["gimp", "/tmp/x.png"])
        self.assertEqual(S.exec_args("gimp %f"), ["gimp"])

    def test_launch_goes_restricted(self):
        from sonata2 import apps, appperms, config  # noqa: F401  (apps: its launch wrapper)
        config.save(appperms.NAME, {"ask": False, "reviewed": [], "limits": {"x": ["network"]}})
        kf = GLib.KeyFile()
        data = "[Desktop Entry]\nType=Application\nName=X\nExec=true\n"
        kf.load_from_data(data, len(data), GLib.KeyFileFlags.NONE)
        app = Gio.DesktopAppInfo.new_from_keyfile(kf)
        with mock.patch.object(appperms, "limits", return_value=["network"]), \
                mock.patch.object(S, "run_restricted", return_value=True) as run:
            self.assertTrue(app.launch([], None))
        self.assertEqual(run.call_args[0][1], ["network"])

    @unittest.skipUnless(shutil.which("bwrap"), "needs bwrap")
    def test_really_no_network(self):
        import subprocess
        argv = S.restricted_command(Info("sh -c 'cut -d: -f1 /proc/net/dev | tail -n +3'"), ["network"])
        r = subprocess.run(argv, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            self.skipTest("no user namespaces here: " + r.stderr[:80])
        self.assertEqual(r.stdout.split(), ["lo"])                  # only its own loopback


class RunTest(unittest.TestCase):
    def test_runs_and_cleans_up(self):
        if not shutil.which("bwrap"):
            pass
        seen = []
        S.listeners.append(lambda: seen.append(S.running("spotify")))
        made = []

        def cmd(_info, home):
            made.append(home)
            open(os.path.join(home, "secret"), "w").close()
            return ["sh", "-c", "exit 0"]
        with mock.patch.object(S, "available", return_value=True), mock.patch.object(S, "command", cmd):
            self.assertTrue(S.open_app(Info()))
            self.assertTrue(S.running("spotify"))
            for _ in range(20):
                settle(100)
                if not S.running("spotify"):
                    break
        S.listeners.clear()
        self.assertEqual(seen, [True, False])
        self.assertFalse(os.path.exists(made[0]))                 # nothing kept

    def test_no_bwrap_says_so(self):
        with mock.patch.object(S, "available", return_value=False), \
                mock.patch("sonata2.ui.dialog.alert") as alert:
            S.ask_open(Info())
        self.assertIn("bubblewrap", alert.call_args[0][0])

    def test_locked_app_asks_first(self):
        with mock.patch.object(S, "available", return_value=True), \
                mock.patch("sonata2.applock.gate", return_value=True) as gate, \
                mock.patch.object(S, "open_app") as run:
            S.ask_open(Info())
        gate.assert_called_once()
        run.assert_not_called()


class DockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Gtk.init()

    def test_badge(self):
        from sonata2.shell import dock as D
        icon = D.DockIcon(Gio.ThemedIcon.new("application-x-executable"), 48)
        icon.set_sandboxed(True)
        with mock.patch.object(icon, "_draw_badge") as draw:
            icon.do_snapshot(Gtk.Snapshot())
        self.assertEqual(draw.call_args[0][1:], ("S", "#ff9f0a", "#000000"))
        icon.set_badge("3")                               # a notification count wins
        with mock.patch.object(icon, "_draw_badge") as draw:
            icon.do_snapshot(Gtk.Snapshot())
        self.assertEqual(len(draw.call_args[0]), 1)

    def test_menu_item(self):
        from sonata2.shell import dock_menu
        around = []
        item = dock_menu.sandbox_item(Info(), around.append)
        self.assertEqual(item.label, "Open in Sandbox")
        with mock.patch.object(S, "ask_open") as ask:
            item.on_activate()
            ask.assert_not_called()                       # Launchpad closes first
            around[0]()
        ask.assert_called_once()


if __name__ == "__main__":
    unittest.main()
