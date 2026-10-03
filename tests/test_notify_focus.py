"""Clicking a notification brings its app forward (Vini): the app's most
recently used window, matched by desktop id or, without one, by name."""
import unittest
from unittest import mock

from sonata2.shell import notifications as N


def view(vid, aid, ts, kind="toplevel"):
    return {"id": vid, "app-id": aid, "last-focus-timestamp": ts, "type": kind}


class WindowOfTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(N.apps, "match_app_id", side_effect=lambda a: {"Firefox": "firefox"}.get(a))
        p.start(); self.addCleanup(p.stop)

    def test_latest_window_of_app(self):
        views = [view(1, "org.gnome.Nautilus", 5), view(2, "Firefox", 3), view(3, "firefox", 9),
                 view(4, "firefox", 99, kind="background")]
        self.assertEqual(N.window_of(views, "firefox.desktop", "Firefox"), 3)

    def test_matches_through_app_id_lookup(self):
        self.assertEqual(N.window_of([view(7, "Firefox", 1)], "firefox", ""), 7)

    def test_by_name_without_desktop(self):
        info = mock.Mock(); info.get_name.return_value = "Firefox"
        with mock.patch.object(N.apps, "lookup", return_value=info):
            self.assertEqual(N.window_of([view(7, "Firefox", 1)], "", "firefox"), 7)

    def test_none_open(self):
        self.assertIsNone(N.window_of([view(1, "other", 1)], "firefox", "Firefox"))
        self.assertIsNone(N.window_of(None, "firefox", "Firefox"))


class InvokeTest(unittest.TestCase):
    def server(self):
        s = N.Notifications.__new__(N.Notifications)
        s._conn = None
        s.close = mock.Mock()
        return s

    def test_focuses_open_app(self):
        n = mock.Mock(actions=[], desktop="firefox", app="Firefox", id=4)
        with mock.patch.object(N, "bring_forward", return_value=True) as bf, \
             mock.patch.object(N.apps, "lookup") as lk:
            self.server().invoke(n)
        bf.assert_called_once_with("firefox", "Firefox")
        lk.assert_not_called()

    def test_opens_closed_app(self):
        n = mock.Mock(actions=[], desktop="firefox", app="Firefox", id=4)
        with mock.patch.object(N, "bring_forward", return_value=False), \
             mock.patch.object(N.apps, "lookup") as lk:
            self.server().invoke(n)
        lk.return_value.launch.assert_called_once()

    def test_app_default_action_wins_over_launch(self):
        n = mock.Mock(actions=[("default", "Open")], desktop="firefox", app="Firefox", id=4)
        with mock.patch.object(N, "bring_forward", return_value=False), \
             mock.patch.object(N.apps, "lookup") as lk:
            self.server().invoke(n)
        lk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
