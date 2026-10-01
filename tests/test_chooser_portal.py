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
        with mock.patch("subprocess.run", side_effect=lambda a, **k: runs.append(a)), \
                mock.patch("subprocess.Popen"), mock.patch("time.sleep"), \
                mock.patch.object(m, "_reload_wayfire_config"):
            m.restart([])
        self.assertIn(["pkill", "-f", "--", r"sonata2 portal( |$)"], runs)
        runs.clear()
        with mock.patch("subprocess.run", side_effect=lambda a, **k: runs.append(a)), \
                mock.patch("subprocess.Popen"), mock.patch("time.sleep"):
            m.restart(["dock"])                               # just one part: the panels stay
        self.assertNotIn(["pkill", "-f", "--", r"sonata2 portal( |$)"], runs)
        with open("install.sh", encoding="utf-8") as f:
            self.assertIn('pkill -f -- "sonata2 portal( |$)"', f.read())


if __name__ == "__main__":
    unittest.main()
