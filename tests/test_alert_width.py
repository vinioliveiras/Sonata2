"""The test run found every alert segfaulting with libadwaita 1.5.0
(Ubuntu 24.04's): set_content_width on an Adw.AlertDialog. The fixed
width is set only from libadwaita 1.6."""
import unittest
from unittest import mock

import gi

gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from sonata2.ui import dialog  # noqa: E402


class AlertWidthTest(unittest.TestCase):
    def test_guard_follows_the_version(self):
        self.assertEqual(dialog._CONTENT_WIDTH_OK,
                         (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 6))

    def test_no_content_width_on_old_libadwaita(self):
        if not dialog._MODERN:
            self.skipTest("no AlertDialog here")
        fake = mock.MagicMock()
        with mock.patch.object(dialog, "_CONTENT_WIDTH_OK", False), \
                mock.patch.object(dialog.Adw, "AlertDialog", return_value=fake):
            try:
                dialog.alert("Heading", "Body", [("ok", "OK", "default")])
            except Exception:
                pass                                   # (presenting needs a window: not what's checked)
        fake.set_content_width.assert_not_called()
        with mock.patch.object(dialog, "_CONTENT_WIDTH_OK", True), \
                mock.patch.object(dialog.Adw, "AlertDialog", return_value=fake):
            try:
                dialog.alert("Heading", "Body", [("ok", "OK", "default")])
            except Exception:
                pass
        fake.set_content_width.assert_called_once_with(dialog.ALERT_WIDTH)


if __name__ == "__main__":
    unittest.main()
