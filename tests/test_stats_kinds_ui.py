"""A menu bar figure subscribes only to its own kind, so Stats reads only
that (nvidia-smi, sensors... only when shown -- performance review)."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.backend import stats as S  # noqa: E402
from sonata2.shell import statsui  # noqa: E402


class KindsTest(unittest.TestCase):
    def test_menu_item_asks_only_its_kind(self):
        Gtk.init()
        st = mock.Mock()
        with mock.patch.object(S.Stats, "shared", return_value=st):
            box = statsui.menu_item("cpu", "text")
            box._live._on(True)
            st.subscribe.assert_called_once()
            self.assertEqual(st.subscribe.call_args.kwargs.get("kinds"), ["cpu"])
            box._live._on(False)
            st.unsubscribe.assert_called_once()

    def test_wanted_is_the_union(self):
        st = S.Stats.__new__(S.Stats)
        st._kinds = {1: frozenset({"cpu"}), 2: frozenset({"ram"})}
        self.assertEqual(st.wanted(), {"cpu", "ram"})
        st._kinds[3] = None
        self.assertIsNone(st.wanted())


if __name__ == "__main__":
    unittest.main()
