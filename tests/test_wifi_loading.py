"""Settings > Wi-Fi: a spinner while the networks are read (Vini: a delay
with nothing shown)."""
import unittest
from types import SimpleNamespace
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from sonata2.settings import app as S  # noqa: E402
from sonata2.ui import progress  # noqa: E402


class WifiLoadingTest(unittest.TestCase):
    def test_spinner_until_the_list_comes(self):
        Adw.init()
        win = S.Settings.__new__(S.Settings)
        jobs = []
        with mock.patch.object(S.system, "run_async", side_effect=lambda work, done, *a: jobs.append(done)):
            groups = win._page_wifi()
        nets = groups[1]
        self.assertTrue(win._wifi_spin.get_visible())
        self.assertEqual(len(win._wifi_rows), 1)
        loading = win._wifi_rows[0]
        self.assertIn("Looking", loading.get_title())
        self.assertTrue(any(isinstance(w, progress.Spinner) for w in _all(loading)))
        net = SimpleNamespace(ssid="Casa", connected=True, signal=80, secure=True)
        jobs[-1]((True, [net]))
        self.assertFalse(win._wifi_spin.get_visible())
        self.assertEqual([r.get_title() for r in win._wifi_rows], ["Casa"])
        self.assertIsNotNone(nets)


def _all(w):
    out, stack = [], [w]
    while stack:
        x = stack.pop()
        out.append(x)
        c = x.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return out


if __name__ == "__main__":
    unittest.main()
