"""Vini: sharing the screen from Chrome put an odd app (no icon) in the Dock:
Chrome's "<site> is sharing your screen." bar, a window with no app id. It
stays out of the Dock and Alt+Tab; a pill over the menu bar shows the sharing."""
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.wl import toplevels as T  # noqa: E402


class FakeHandle:
    def __init__(self):
        self.dispatcher = {}

    def destroy(self):
        pass


def manager():
    m = T.ToplevelManager.__new__(T.ToplevelManager)
    m.toplevels, m.share_bars, m.listeners, m._ignore = [], [], [], {"shell"}
    return m


def open_window(m, app_id, title):
    h = FakeHandle()
    m._on_toplevel(None, h)
    h.dispatcher["app_id"](h, app_id)
    h.dispatcher["title"](h, title)
    h.dispatcher["done"](h)
    return h


class ShareBarTest(unittest.TestCase):
    def test_is_share_bar(self):
        self.assertTrue(T.is_share_bar("", "b.siobud.com is sharing your screen."))
        self.assertTrue(T.is_share_bar("", "meet.google.com está compartilhando sua tela."))
        self.assertTrue(T.is_share_bar("unknown", "b.siobud.com is sharing your screen."))   # foreign-toplevel
        self.assertFalse(T.is_share_bar("google-chrome", "b.siobud.com is sharing your screen."))
        self.assertFalse(T.is_share_bar("", "Untitled"))

    def test_kept_out_of_the_windows(self):
        m = manager()
        open_window(m, "google-chrome", "Broadcast Box - Google Chrome")
        bar = open_window(m, "unknown", "b.siobud.com is sharing your screen.")
        self.assertEqual([t.app_id for t in m.toplevels], ["google-chrome"])
        self.assertEqual(len(m.share_bars), 1)
        bar.dispatcher["closed"](bar)
        self.assertEqual(m.share_bars, [])

    def test_site(self):
        from sonata2.shell.sharing import site_of
        self.assertEqual(site_of("b.siobud.com is sharing your screen."), "b.siobud.com")
        self.assertEqual(site_of("x"), "")


class PillTest(unittest.TestCase):
    def test_pill_follows_the_bars(self):
        from sonata2.shell.sharing import SharingControl
        app = Gtk.Application(application_id="io.github.test.sharepill")
        app.register(None)
        m = manager()
        pill = SharingControl(app, m)
        self.assertFalse(pill.get_visible())
        open_window(m, "", "b.siobud.com is sharing your screen.")
        self.assertTrue(pill.get_visible())
        self.assertEqual(pill.label.get_label(), "b.siobud.com")
        m.share_bars.clear()
        m._notify()
        self.assertFalse(pill.get_visible())


class StopSharingTest(unittest.TestCase):
    """Vini: a button on the pill to stop sharing."""

    def test_cast_nodes(self):
        from sonata2.shell.sharing import cast_nodes
        dump = [{"id": 40, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "xdpw-stream-a1B2c3"}}},
                {"id": 41, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "alsa_input.x"}}},
                {"id": 42, "type": "PipeWire:Interface:Port", "info": {"props": {"node.name": "xdpw-stream-x"}}}]
        self.assertEqual(cast_nodes(dump), [40])
        self.assertEqual(cast_nodes([]), [])

    def test_stop_destroys_the_stream(self):
        import json
        import subprocess
        from unittest import mock
        from sonata2.shell import sharing
        runs = []

        def run(cmd, **_k):
            runs.append(cmd)
            out = json.dumps([{"id": 40, "type": "PipeWire:Interface:Node",
                               "info": {"props": {"node.name": "xdpw-stream-q"}}}]) if cmd[0] == "pw-dump" else ""
            return subprocess.CompletedProcess(cmd, 0, out, "")
        with mock.patch("shutil.which", return_value="/usr/bin/x"), mock.patch("subprocess.run", side_effect=run):
            self.assertTrue(sharing.stop_sharing())
        self.assertIn(["pw-cli", "destroy", "40"], runs)
        self.assertFalse(any("systemctl" in c for c in runs))

    def test_fallback_restarts_the_portal(self):
        import subprocess
        from unittest import mock
        from sonata2.shell import sharing
        runs = []
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("subprocess.run", side_effect=lambda cmd, **_k: (runs.append(cmd),
                                                                           subprocess.CompletedProcess(cmd, 0))[1]):
            self.assertTrue(sharing.stop_sharing())
        self.assertEqual(runs, [["systemctl", "--user", "restart", "xdg-desktop-portal-wlr"]])

    def test_pill_has_the_button(self):
        from sonata2.shell.sharing import SharingControl
        app = Gtk.Application(application_id="io.github.test.sharestop")
        app.register(None)
        pill = SharingControl(app, manager())
        buttons, w = [], pill.box.get_first_child()
        while w is not None:
            if isinstance(w, Gtk.Button):
                buttons.append(w)
            w = w.get_next_sibling()
        self.assertEqual([b.get_tooltip_text() for b in buttons], ["Stop Sharing"])


if __name__ == "__main__":
    unittest.main()
