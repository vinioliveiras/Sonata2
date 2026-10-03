"""Settings > Menu Bar: "Automatically hide and show the menu bar" (Vini:
the menu bar hides like the Dock does). It slides up out of sight when the
pointer leaves it, comes back down when the pointer reaches the top edge,
stays while one of its menus is open, and gives windows the top of the
screen while it is on."""
import os
import tempfile
import types
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.shell import topbar as T  # noqa: E402

Adw.init()


def settle(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class Host(Gtk.Window):
    """The menu bar window's auto-hide half, without its services."""
    _init_autohide = T.TopBarWindow._init_autohide
    _apply_autohide = T.TopBarWindow._apply_autohide
    _pointer = T.TopBarWindow._pointer
    _timed = T.TopBarWindow._timed
    _slide = T.TopBarWindow._slide

    def __init__(self, autohide):
        super().__init__()
        self.set_child(Gtk.Box(width_request=200, height_request=T.BAR_H))
        self.bar = types.SimpleNamespace(cfg={"autohide": autohide})
        self.inputs = []
        self._update_input = lambda: self.inputs.append(self._hidden)


class AutoHideTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(T.layer, "set_exclusive")
        self.exclusive = p.start()
        self.addCleanup(p.stop)
        self.hosts = []

    def tearDown(self):
        for h in self.hosts:
            h.destroy()
        ui.menu.OPEN.clear()

    def host(self, autohide):
        h = Host(autohide)
        h.present()
        h._init_autohide()
        self.hosts.append(h)
        settle(30)
        return h

    def test_starts_hidden_and_gives_windows_the_top(self):
        h = self.host(True)
        self.assertTrue(h._hidden)
        self.assertEqual(h._intro_offset, float(T.BAR_H))       # drawn above the screen
        self.exclusive.assert_called_with(h, 0)

    def test_off_by_default(self):
        self.assertFalse(T.DEFAULTS["autohide"])
        h = self.host(False)
        self.assertFalse(h._hidden)
        h._pointer(False)
        settle(500)
        self.assertFalse(h._hidden)                               # stays without the setting

    def test_slides_out_on_leave_and_back_at_the_edge(self):
        h = self.host(True)
        h._pointer(True)                                          # pointer reaches the top edge
        settle(T.REVEAL_MS + T.HIDE_MS + 150)
        self.assertFalse(h._hidden)
        self.assertAlmostEqual(h._intro_offset, 0.0, places=3)
        self.assertEqual(h.inputs[-1], False)                    # the whole bar takes the pointer again
        h._pointer(False)
        settle(400 + T.HIDE_MS + 150)
        self.assertTrue(h._hidden)
        self.assertAlmostEqual(h._intro_offset, float(T.BAR_H), places=3)
        self.assertEqual(h.inputs[-1], True)                     # only the edge strip

    def test_stays_while_a_menu_is_open(self):
        h = self.host(True)
        h._slide(False)
        settle(T.HIDE_MS + 100)
        ui.menu.OPEN.add(object())
        h._pointer(False)
        settle(600)
        self.assertFalse(h._hidden)

    def test_turning_it_off_brings_it_back(self):
        h = self.host(True)
        h.bar.cfg["autohide"] = False
        h._apply_autohide()
        settle(T.HIDE_MS + 150)
        self.assertFalse(h._hidden)
        self.assertAlmostEqual(h._intro_offset, 0.0, places=3)
        self.exclusive.assert_called_with(h, T.BAR_H)            # windows stop under the bar again

    def test_opening_a_menu_reveals_it(self):
        h = self.host(True)
        bar = T.Bar.__new__(T.Bar)
        bar.reveal = h.bar.reveal
        btn = Gtk.Button()
        T.Bar._open(bar, btn, lambda b: None)
        settle(T.HIDE_MS + 100)
        self.assertFalse(h._hidden)


class AnimationTest(unittest.TestCase):
    def test_slide_animates_and_an_interrupted_one_stops_its_stats(self):
        """The slide is an Adw animation; reversing mid-way stops the first one's FrameStats."""
        made = []
        real = ui.transition.FrameStats

        class Rec(real):
            def __init__(s, *a):
                super().__init__(*a)
                made.append(s)
        with mock.patch.object(T.layer, "set_exclusive"), mock.patch.object(ui.transition, "FrameStats", Rec):
            h = Host(True)
            h.present()
            h._init_autohide()
            h._slide(False)
            settle(40)
            mid = h._intro_offset
            h._slide(True)
            settle(T.HIDE_MS + 200)
        self.assertTrue(0 < mid < T.BAR_H)                        # moving, not a jump
        self.assertEqual(len(made), 2)
        self.assertTrue(all(s.tick is None for s in made))
        h.destroy()


class SettingsRowTest(unittest.TestCase):
    def test_menu_bar_page_has_the_switch(self):
        tmp = tempfile.mkdtemp()
        with mock.patch.object(T.config, "CONFIG_DIR", tmp):
            from sonata2.settings import app as S
            win = S.Settings.__new__(S.Settings)
            saved = []
            win._save = lambda name, key, value: saved.append((name, key, value))
            groups = S.Settings._page_menubar(win)
            rows = []

            def walk(w):
                if isinstance(w, Adw.SwitchRow):
                    rows.append(w)
                c = w.get_first_child()
                while c is not None:
                    walk(c)
                    c = c.get_next_sibling()
            for g in groups:
                walk(g)
            row = next(r for r in rows if r.get_title() == "Automatically hide and show the menu bar")
            row.set_active(True)
        self.assertIn(("topbar", "autohide", True), saved)


if __name__ == "__main__":
    unittest.main()
