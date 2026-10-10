"""The first dock.json / appearance.json write after start compares with
the look the process started with (Vini: opening an unpinned app made the
Dock write its recents, every Sonata window cross-faded and the menu bar's
Control Center icon went blank)."""
import unittest
from unittest import mock

from sonata2.ui import theme


class FirstWriteTest(unittest.TestCase):
    def test_unchanged_look_no_fade(self):
        with mock.patch.object(theme, "_radii_seen", None), mock.patch.object(theme, "_glass_bars_on", None), \
                mock.patch.object(theme, "_accent_name", None), mock.patch.object(theme, "_glass_seen", None), \
                mock.patch.object(theme, "_load") as load, mock.patch.object(theme, "_transparency_changed"):
            theme._remember_appearance()
            theme._appearance_changed()                   # dock.json written, nothing changed
            load.assert_not_called()

    def test_setup_remembers(self):
        import inspect
        src = inspect.getsource(theme.setup)
        self.assertLess(src.index('config.watch("dock"'), src.index("_remember_appearance()"))


class FilesUriTest(unittest.TestCase):
    def test_run_files_canonical(self):
        import inspect
        from sonata2 import __main__ as M
        src = inspect.getsource(M.run_files)
        self.assertLess(src.index("folder.canonical(uri)"), src.index('uri == "sonata:connect"'))


if __name__ == "__main__":
    unittest.main()
