"""Vini: Return in "Empty Trash?" cancelled. macOS: Return empties (the
default button), Escape cancels. Other alerts keep Return off their
destructive choice unless it is asked for."""
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ.setdefault("XDG_DATA_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from sonata2 import ui  # noqa: E402


class EmptyTrashReturnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def test_alert_default(self):
        R = [("cancel", "Cancel", ""), ("empty", "Empty Trash", "destructive")]

        def made(**_kw):
            d = mock.MagicMock()
            d.get_root.return_value = None
            return d
        with mock.patch.object(ui.dialog, "_MODERN", True), mock.patch.object(Adw, "AlertDialog", side_effect=made):
            d = ui.dialog.alert("Empty?", "", R, default="empty")
            d.set_default_response.assert_called_once_with("empty")
            d.set_close_response.assert_called_once_with("cancel")
            d = ui.dialog.alert("Discard?", "", [("discard", "Don't Save", "destructive"), ("cancel", "Cancel", "")])
            d.set_default_response.assert_called_once_with("cancel")   # never destructive unless asked

    def test_dock_and_files_empty_on_return(self):
        from sonata2.shell import dock_menu
        with mock.patch.object(ui.dialog, "alert") as alert:
            dock_menu.confirm_empty_trash()
        self.assertEqual(alert.call_args.kwargs.get("default"), "empty")
        import inspect
        from sonata2.files import window as FW
        self.assertIn('default="empty"', inspect.getsource(FW.FilesWindow.empty_trash))


if __name__ == "__main__":
    unittest.main()
