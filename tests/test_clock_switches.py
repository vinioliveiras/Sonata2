"""Vini: Settings > Date & Time's switches were off with the date showing --
"Show the date" looked for "%d", the default format has "%-d"."""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

from sonata2.shell import topbar as T  # noqa: E402
from sonata2.settings.app import clock_format  # noqa: E402


class ClockSwitchesTest(unittest.TestCase):
    def test_default_shows_date_in_24_hours(self):
        self.assertTrue(T.shows_date(T.DEFAULTS["clock_format"]))
        self.assertTrue(T.is_24h(T.DEFAULTS["clock_format"]))

    def test_every_format_settings_writes_reads_back(self):
        for date in (True, False):
            for h24 in (True, False):
                fmt = clock_format(date, h24)
                self.assertEqual((T.shows_date(fmt), T.is_24h(fmt)), (date, h24), fmt)

    def test_other_spellings(self):
        self.assertTrue(T.shows_date("%e %b %k:%M"))
        self.assertTrue(T.is_24h("%e %b %k:%M"))
        self.assertFalse(T.shows_date("%a %-I:%M %p"))
        self.assertFalse(T.is_24h("%a %-I:%M %p"))
        self.assertFalse(T.shows_date(""))

    def test_settings_uses_them(self):
        import inspect
        from sonata2.settings import app
        src = inspect.getsource(app.Settings._page_datetime)
        self.assertIn('switch_row("Show the date", T.shows_date(fmt)', src)
        self.assertNotIn('"%d" in', src)


if __name__ == "__main__":
    unittest.main()
