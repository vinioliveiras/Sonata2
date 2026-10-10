"""Vini: with the window buttons on the right, Settings itself (and Files)
kept them on the left, the custom colours didn't reach Sonata's open
windows, and GTK 3 apps / Chrome's GTK mode kept the theme's buttons (the
close one cut at the edge). Open windows follow now; GTK 3 gets Sonata's
rules."""
import os
import tempfile
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import adwstyle, config  # noqa: E402
from sonata2.ui import theme, window as W  # noqa: E402


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        p.start()
        self.addCleanup(p.stop)
        theme._buttons_seen = theme._buttons_sig()

    def change(self, **kw):
        config.update("appearance", **kw)
        theme._buttons_changed()                  # (what appearance.json's monitor calls)


def lights_in(w):
    out, stack = [], [w]
    while stack:
        x = stack.pop()
        if "traffic" in x.get_css_classes():
            out.append(x)
        c = x.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return out


class TitlebarLiveTest(Base):
    def test_moves_both_ways_with_end_widget(self):
        end = Gtk.Label(label="end")
        h = W.titlebar(Gtk.Window(), "x", end=end)
        bar = h.bar
        self.assertIsNotNone(bar.get_start_widget())
        self.change(buttons_side="right")
        self.assertIsNone(bar.get_start_widget())
        both = bar.get_end_widget()
        self.assertIs(both.get_first_child(), end)
        self.assertIn("traffic", both.get_last_child().get_css_classes())
        self.change(buttons_side="left")
        self.assertIn("traffic", bar.get_start_widget().get_css_classes())
        self.assertIs(bar.get_end_widget(), end)
        self.assertEqual(len(lights_in(bar)), 1)

    def test_colours_reach_open_windows(self):
        calls = []
        with mock.patch("sonata2.trafficlights.css_rules", lambda dark: calls.append(dark) or ""):
            self.change(buttons_style="graphite")
        self.assertTrue(calls)                    # the look's rules were asked again


class SettingsLiveTest(Base):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def test_settings_moves_its_own_buttons(self):
        from sonata2.settings import app as S
        win = S.Settings(None)
        win.present()
        win.select("appearance", from_sidebar=True)
        settle(300)
        self.assertIsNotNone(win._side_lights)
        with mock.patch.object(S, "apply_buttons_side", lambda: None), \
                mock.patch.object(S.system, "run_async", lambda *a, **k: None):
            win._set_buttons_side("right")
        theme._buttons_changed()                  # (the file monitor's turn: nothing moves twice)
        self.assertIsNone(win._side_lights)
        tv = win.pages[S.section_of("appearance")]
        self.assertIsNotNone(tv.lights)
        self.assertIs(tv.lights.get_parent() is not None, True)
        self.change(buttons_side="left")
        self.assertIsNotNone(win._side_lights)
        self.assertIsNone(tv.lights)
        win.destroy()


class FilesLiveTest(Base):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def test_files_follows(self):
        from sonata2.files import window as FW
        app = Adw.Application(application_id="org.test.FilesLive")
        app.register(None)
        w = FW.FilesWindow(app) if hasattr(FW, "FilesWindow") else None
        if w is None:
            self.skipTest("no FilesWindow")
        self.assertIs(w._lights.get_parent(), w._lights_slot)
        self.change(buttons_side="right")
        self.assertIs(w._lights.get_parent(), w._toolbar_bar)
        self.assertEqual(w._lights_slot.get_first_child(), None)
        self.change(buttons_side="left")
        self.assertIs(w._lights.get_parent(), w._lights_slot)
        w.destroy()


class Gtk3Test(unittest.TestCase):
    def test_rules(self):
        with mock.patch.object(adwstyle, "tokens_frame", return_value={"buttons_side": "right", "radius": 10}):
            css = adwstyle.css3("/x")
        self.assertIn("headerbar > box.right { padding-right:", css)
        self.assertIn('url("file:///x/sonata-tl-close.svg")', css)
        self.assertIn("button.titlebutton.close:hover", css)
        with mock.patch.object(adwstyle, "tokens_frame", return_value={"buttons_side": "left", "radius": 10}):
            self.assertIn("headerbar > box.left { padding-left:", adwstyle.css3("/x"))

    def test_linked_in_gtk3(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "gtk-3.0", "gtk.css")
            os.makedirs(os.path.dirname(path))
            open(path, "w").write("window { color: red; }\n")
            adwstyle.link(path, "/run/x/gtk3.css")
            text = open(path).read()
            self.assertTrue(text.startswith(adwstyle.BEGIN))
            self.assertIn('@import url("file:///run/x/gtk3.css");', text)
            self.assertIn("window { color: red; }", text)
            adwstyle.link(path, "/run/x/gtk3.css")
            self.assertEqual(open(path).read().count("@import"), 1)

    def test_written_and_linked_at_login(self):
        src = open(os.path.join(os.path.dirname(adwstyle.__file__), "adwstyle.py")).read()
        self.assertIn("link(user_css3(), css_path3())", src)
        tsrc = open(os.path.join(os.path.dirname(adwstyle.__file__), "titlebars.py")).read()
        self.assertIn("adwstyle.link(adwstyle.user_css3(), adwstyle.css_path3())", tsrc)


if __name__ == "__main__":
    unittest.main()
