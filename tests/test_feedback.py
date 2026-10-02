"""Feedbacker (python3 -m unittest tests.test_feedback)."""
import os
import tempfile
import unittest
import urllib.parse
import zipfile
from unittest import mock

from sonata2 import __version__
from sonata2.feedback import report


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        home = self.tmp.name
        self.env = mock.patch.dict(os.environ, {"HOME": home, "XDG_CACHE_HOME": os.path.join(home, ".cache"),
                                                "XDG_CONFIG_HOME": os.path.join(home, ".config")})
        self.env.start()
        logs = os.path.join(home, ".cache", "sonata2")
        os.makedirs(logs)
        for name in ("session.log", "dock.old.log", "doctor.txt", "preview.png"):
            with open(os.path.join(logs, name), "w") as f:
                f.write(name)

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_report_saved_in_home_folder(self):
        path = report.create("Dock froze", "It stopped.", doctor_text="checks\n", now=0)
        self.assertEqual(os.path.dirname(path), os.path.join(self.tmp.name, "Sonata Reports"))
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            self.assertIn("Dock froze", z.read("description.txt").decode())
            self.assertIn(__version__, z.read("system.txt").decode())
            self.assertEqual(z.read("doctor.txt").decode(), "checks\n")
        self.assertIn("logs/session.log", names)
        self.assertIn("logs/dock.old.log", names)
        self.assertNotIn("logs/preview.png", names)         # logs only
        self.assertNotIn("logs/doctor.txt", names)          # the fresh one is at the top
        self.assertFalse(any(n.endswith(".part") for n in os.listdir(os.path.dirname(path))))

    def test_issue_link(self):
        url = report.issue_url("Crash", "Steps", "Sonata Report x.zip")
        self.assertTrue(url.startswith(report.ISSUES + "?"))
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(q["title"], ["Crash"])
        self.assertIn("Sonata Report x.zip", q["body"][0])
        self.assertIn(__version__, q["body"][0])
        long = report.issue_url("Crash", "word " * 5000)
        self.assertLessEqual(len(long), report.URL_MAX)

    def test_monitoring_is_detailed_logging(self):
        with mock.patch("sonata2.logs.dev_install", return_value=False), \
                mock.patch.dict(os.environ, {"SONATA_DEBUG": ""}):
            report.set_monitoring(False)
            self.assertFalse(report.monitoring())
            report.set_monitoring(True)
            self.assertTrue(report.monitoring())
            report.set_monitoring(False)
            self.assertFalse(report.monitoring())


class CrashTest(unittest.TestCase):
    """After a crash, the next login opens Feedbacker with a report already saved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HOME": self.tmp.name,
                                                "XDG_CACHE_HOME": os.path.join(self.tmp.name, ".cache")})
        self.env.start()
        os.makedirs(report.log_dir())

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def mark(self, text):
        with open(os.path.join(report.log_dir(), report.CRASH), "w") as f:
            f.write(text)

    def test_note_read_and_cleared(self):
        self.assertEqual(report.crash(), {})
        self.mark("1790000000 134\n")
        self.assertEqual(report.crash(), {"time": 1790000000, "code": 134})
        self.assertIn("SIGABRT", report.crash_text(report.crash()))
        path = report.create("t", "d", doctor_text="", now=0)
        with zipfile.ZipFile(path) as z:
            self.assertIn("Crash: Sonata quit unexpectedly", z.read("system.txt").decode())
        report.clear_crash()
        self.assertEqual(report.crash(), {})
        self.mark("garbage")
        self.assertEqual(report.crash(), {})

    def test_session_script_notes_only_crashes(self):
        """tools/sonata-session's record_crash: log-out (0) and shutdown signals are not crashes."""
        import subprocess
        script = os.path.join(os.path.dirname(__file__), "..", "tools", "sonata-session")
        body = open(script).read()
        fn = body[body.index("record_crash() {"):body.index("\n}\n", body.index("kernel_log() {")) + 3]   # + kernel_log
        for code, crashed in ((0, False), (143, False), (129, False), (130, False), (134, True), (1, True)):
            mark = os.path.join(report.log_dir(), report.CRASH)
            if os.path.exists(mark):
                os.remove(mark)
            subprocess.run(["bash", "-c", f'logs="{report.log_dir()}"\n{fn}\nrecord_crash {code} 0'],
                           env={**os.environ, "PATH": "/usr/bin:/bin"}, check=True)
            self.assertEqual(os.path.exists(mark), crashed, code)

    def test_autostart_opens_feedbacker(self):
        from sonata2 import autostart
        self.mark("1790000000 139")
        with mock.patch("subprocess.Popen") as popen, mock.patch("subprocess.run") as run, \
                mock.patch.object(autostart, "entries", return_value=[]), \
                mock.patch("sonata2.titlebars.apply"), mock.patch("sonata2.keyring.start"), \
                mock.patch("sonata2.flatpak_theme.apply"), mock.patch("sonata2.gtkstyle.reset_env"):
            run.return_value.returncode = 0
            autostart.run()
        cmds = [" ".join(c.args[0]) for c in popen.call_args_list]
        self.assertTrue(any("feedback" in c for c in cmds), cmds)


class DockDefaultTest(unittest.TestCase):
    def test_last_in_dock_and_removable(self):
        from sonata2 import apps
        from sonata2.shell import dock
        self.assertEqual(apps.DEFAULT_SLOTS[-1], ("io.github.vinioliveiras.sonata2.feedback",))
        self.assertNotIn("io.github.vinioliveiras.sonata2.feedback", dock.PERMANENT)


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gi.repository import Adw
        from sonata2 import ui
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.feedback")
        cls.app.register(None)

    def test_note_follows_monitoring(self):
        from sonata2.feedback.window import FeedbackWindow
        with mock.patch.object(report, "monitoring", return_value=False), \
                mock.patch.object(report, "set_monitoring") as setm:
            win = FeedbackWindow(self.app)
            self.assertTrue(win.off_note.get_visible())
            win.monitor_row.set_active(True)
            setm.assert_called_with(True)
            self.assertFalse(win.off_note.get_visible())
            win.destroy()

    def test_one_title_bar(self):
        """The compositor draws the title bar: a second one inside showed two sets of buttons."""
        from sonata2.feedback.window import FeedbackWindow
        win = FeedbackWindow(self.app)
        found, stack = [], [win]
        while stack:
            w = stack.pop()
            found += [w] if w.has_css_class("sonata-titlebar") else []
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        self.assertEqual(found, [])
        win.destroy()

    def test_own_icon(self):
        from sonata2.feedback import window
        with mock.patch("sonata2.apps.write_desktop_file", side_effect=lambda _n, text: text):
            self.assertIn("Icon=sonata-feedback\n", window.feedback_desktop_file("sonata2"))
        icon = os.path.join(os.path.dirname(window.__file__), "..", "data", "icons", "Sonata", "apps", "scalable",
                            "sonata-feedback.svg")
        self.assertTrue(os.path.isfile(icon))

    def test_crash_report_on_opening(self):
        from sonata2.feedback.window import FeedbackWindow
        with mock.patch.object(report, "crash", return_value={"time": 0, "code": 134}), \
                mock.patch.object(report, "create", return_value="/tmp/Sonata Report x.zip") as create, \
                mock.patch.object(report, "clear_crash") as clear, \
                mock.patch("sonata2.ui.dialog.alert") as alert:
            win = FeedbackWindow(self.app)
            from gi.repository import GLib
            end = GLib.get_monotonic_time() + 2_000_000
            while not clear.called and GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
            create.assert_called_once()
            clear.assert_called_once()
            alert.assert_not_called()                          # no monitoring question for this one
            self.assertEqual(win.title_row.get_text(), "Sonata quit unexpectedly")
            self.assertIn("Sonata Report x.zip", win.crash_row.get_subtitle())
            win.destroy()

    def test_monitoring_off_asks_first(self):
        from sonata2.feedback.window import FeedbackWindow
        with mock.patch.object(report, "monitoring", return_value=False), \
                mock.patch("sonata2.ui.dialog.alert") as alert, \
                mock.patch.object(FeedbackWindow, "_create") as create:
            win = FeedbackWindow(self.app)
            win.submit(False)
            alert.assert_called_once()
            create.assert_not_called()
            alert.call_args[0][3]("anyway")
            create.assert_called_once_with(False)
            win.destroy()


if __name__ == "__main__":
    unittest.main()
