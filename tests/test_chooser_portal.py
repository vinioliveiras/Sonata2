"""The Open/Save panels (file chooser portal) look exactly like Files and
follow its changes. Run: python3 -m unittest tests.test_chooser_portal"""
import inspect
import unittest
from unittest import mock


class ChooserIsFilesTest(unittest.TestCase):
    def test_a_files_window(self):
        # every Files change reaches the panels: they are Files windows
        from sonata2.files.chooser import ChooserWindow
        from sonata2.files.window import FilesWindow
        self.assertTrue(issubclass(ChooserWindow, FilesWindow))

    def test_glass_reaches_the_dbus_started_portal(self):
        """Vini: the WhatsApp Open panel had no glass -- the portal is started
        by D-Bus without SONATA_GLASS, so its sidebar was solid."""
        from sonata2 import __main__ as m
        self.assertIn("SONATA_GLASS", m.ACTIVATION_ENV)
        src = inspect.getsource(m.main)
        self.assertIn('os.environ.setdefault("SONATA_GLASS", "1")', src)
        with open("tools/sonata-session", encoding="utf-8") as f:
            self.assertIn("QT_QPA_PLATFORMTHEME SONATA_GLASS", f.read())

    def test_restart_renews_the_portal(self):
        """It runs all session and kept the old look after an update."""
        from sonata2 import __main__ as m
        runs = []
        with mock.patch("subprocess.run", side_effect=lambda a, **k: runs.append(a) or mock.Mock(stdout="")), \
                mock.patch("subprocess.Popen"), mock.patch("time.sleep"), \
                mock.patch.object(m, "_reload_wayfire_config"):
            m.restart([])
        self.assertIn(["pkill", "-f", "--", r"sonata2 portal( |$)"], runs)
        runs.clear()
        with mock.patch("subprocess.run", side_effect=lambda a, **k: runs.append(a) or mock.Mock(stdout="")), \
                mock.patch("subprocess.Popen"), mock.patch("time.sleep"):
            m.restart(["dock"])                               # just one part: the panels stay
        self.assertNotIn(["pkill", "-f", "--", r"sonata2 portal( |$)"], runs)
        with open("install.sh", encoding="utf-8") as f:
            self.assertIn('pkill -f -- "sonata2 portal( |$)"', f.read())


class FrontTest(unittest.TestCase):
    """Vini: the Open panel opened under Settings' panel -- it must always
    come up above the app that asked."""

    def test_kept_on_top_and_focused(self):
        import os
        from sonata2 import portal
        calls = []

        class IPC:
            def call(self, method, data=None):
                calls.append((method, data))
                if method == "window-rules/list-views":
                    return [{"id": 3, "pid": 1, "title": "Open", "type": "toplevel"},
                            {"id": 7, "pid": os.getpid(), "title": "Other", "type": "toplevel"},
                            {"id": 9, "pid": os.getpid(), "title": "Choose an Icon", "type": "toplevel"}]
                return None

        class Win:
            def get_title(self):
                return "Choose an Icon"

            def connect(self, _sig, cb):
                self.cb = cb
        w = Win()
        with mock.patch("sonata2.wl.wfipc.WayfireIPC", IPC), \
                mock.patch.object(portal.GLib, "idle_add", side_effect=lambda f: f()):
            portal.bring_to_front(w)
            w.cb(w)                                       # mapped
        self.assertIn(("wm-actions/set-always-on-top", {"view_id": 9, "state": True}), calls)
        self.assertIn(("window-rules/focus-view", {"id": 9}), calls)

    def test_every_panel_does_it(self):
        from sonata2 import portal
        self.assertIn("bring_to_front(win)", inspect.getsource(portal.Portal._call))


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(__import__("os").environ.get("DISPLAY") or __import__("os").environ.get("WAYLAND_DISPLAY"),
                     "needs a display")
class ChooserSizeTest(unittest.TestCase):
    def test_open_panel_closed_reset_files_size(self):
        """Vini: Files windows sometimes opened small again, and the Trash
        (a new Files window from the Dock) opened at another size than
        Files. The Open/Save panel is a Files window: it kept its size under
        "files" too, so closing an app's Open dialog (820 x 520) saved that
        as Files' size. It keeps its own now."""
        import tempfile
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib, Gtk
        from sonata2 import config, ui, winsize
        Adw.init()
        ui.setup()
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()):
            winsize.save("files", 900, 560)
            from sonata2.files.chooser import ChooserWindow
            app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.test.chooser")
            app.register(None)
            win = ChooserWindow(app, mode="open", title="Open")
            self.assertEqual(tuple(win.get_default_size()), (820, 520))   # its own first size
            win.present()
            loop = GLib.MainLoop()
            GLib.timeout_add(400, loop.quit)
            loop.run()
            win.close()
            win.destroy()
            self.assertEqual(winsize.saved("files"), {"width": 900, "height": 560, "maximized": False})
            self.assertEqual((winsize.saved("chooser") or {}).get("width"), 820)
            from sonata2.files.window import FilesWindow
            files = FilesWindow(app, None)
            self.assertEqual(tuple(files.get_default_size()), (900, 560))
            files.destroy()
