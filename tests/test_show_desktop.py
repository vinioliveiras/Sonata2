"""Show Desktop, Sonata's own (shell/dock.py toggle_show_desktop). Vini:
after Super+D, a click on one app brought every app back.
Run: python3 -m unittest tests.test_show_desktop"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from sonata2.shell import dock as D  # noqa: E402


class T:
    def __init__(self, name):
        self.name, self.minimized = name, False


class Manager:
    def __init__(self, *names):
        self.toplevels = [T(n) for n in names]
        self.order = []

    def minimize(self, t):
        t.minimized = True

    def activate(self, t):
        t.minimized = False
        self.order.append(t.name)


class ShowDesktopTest(unittest.TestCase):
    def setUp(self):
        D._DESK["hidden"] = []

    def test_click_brings_back_only_that_app(self):
        m = Manager("files", "chrome", "spotify")
        self.assertEqual(D.toggle_show_desktop(m), "hid")
        self.assertTrue(all(t.minimized for t in m.toplevels))
        m.activate(m.toplevels[1])                                   # a click on Chrome's icon
        self.assertEqual([t.minimized for t in m.toplevels], [True, False, True])   # only Chrome

    def test_again_brings_them_back(self):
        m = Manager("files", "chrome")
        D.toggle_show_desktop(m)
        self.assertEqual(D.toggle_show_desktop(m), "restored")
        self.assertFalse(any(t.minimized for t in m.toplevels))
        self.assertEqual(m.order, ["chrome", "files"])               # the front one last: in front again

    def test_with_one_back_again_hides_it_too(self):
        m = Manager("files", "chrome")
        D.toggle_show_desktop(m)
        m.activate(m.toplevels[0])
        self.assertEqual(D.toggle_show_desktop(m), "hid")            # something showing: everything goes
        self.assertTrue(all(t.minimized for t in m.toplevels))
        self.assertEqual(D.toggle_show_desktop(m), "restored")       # and both come back
        self.assertFalse(any(t.minimized for t in m.toplevels))

    def test_minimized_before_stay_minimized(self):
        m = Manager("files", "chrome")
        m.toplevels[0].minimized = True                              # minimized by you, before
        D.toggle_show_desktop(m)
        D.toggle_show_desktop(m)
        self.assertEqual([t.minimized for t in m.toplevels], [True, False])

    def test_wired_to_super_d(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(D.__file__)))
        ini = open(os.path.join(root, "config", "wayfire.ini")).read()
        self.assertIn("binding_showdesktop = <super> KEY_D", ini)
        self.assertIn("command_showdesktop = sonata2 show-desktop", ini)
        self.assertIn("toggle_showdesktop = none", ini)              # Wayfire's own off
        from sonata2 import shortcuts
        src = open(shortcuts.__file__).read()
        self.assertIn('Shortcut("desktop", "Show Desktop", "Windows", "command", "binding_showdesktop")', src)
        main = open(os.path.join(root, "sonata2", "__main__.py")).read()
        self.assertIn('Gio.SimpleAction.new("show-desktop", None)', main)


if __name__ == "__main__":
    unittest.main()


class BringHereTest(unittest.TestCase):
    """Vini: restoring from the Dock on another display brings the window
    there (and the animation comes out of that Dock's icon)."""

    def test_minimized_windows_move_to_the_clicked_display(self):
        from unittest import mock

        class IPC:
            calls = []

            def call(self, method, data=None):
                IPC.calls.append((method, data))
                if method == "window-rules/list-outputs":
                    return [{"id": 1, "name": "HDMI-A-1"}, {"id": 2, "name": "eDP-1"}]
                if method == "window-rules/list-views":
                    return [{"id": 10, "app-id": "chrome", "title": "Doc", "type": "toplevel",
                             "output-name": "HDMI-A-1", "minimized": True},
                            {"id": 11, "app-id": "chrome", "title": "Here", "type": "toplevel",
                             "output-name": "eDP-1", "minimized": True},
                            {"id": 12, "app-id": "files", "title": "Doc", "type": "toplevel",
                             "output-name": "HDMI-A-1", "minimized": True}]
                return {"result": "ok"}
        t1, t2 = mock.Mock(app_id="chrome", title="Doc"), mock.Mock(app_id="chrome", title="Here")
        d = mock.Mock()
        mon = mock.Mock()
        mon.get_connector.return_value = "eDP-1"
        d._monitor_of.return_value = mon
        with mock.patch("sonata2.wl.wfipc.WayfireIPC", IPC):
            self.assertTrue(D.Dock._bring_here(d, [t1, t2]))
        moves = [c for c in IPC.calls if c[0] == "window-rules/configure-view"]
        self.assertEqual(moves, [("window-rules/configure-view", {"id": 10, "output_id": 2})])   # only the other one

    def test_click_uses_it_when_restoring(self):
        src = open(D.__file__).read()
        body = src[src.index("    def _clicked(self"):src.index("    def launch_feedback")]
        self.assertIn("if not shown and len(_DOCKS) > 1 and self._bring_here(wins):", body)
        # Vini: the window came, but the animation still played toward the other
        # display -- aimed at this icon directly, before it comes back
        i = body.index("self._bring_here(wins)")
        self.assertLess(body.index("self._aim_at(tile, wins)", i), body.index("BRING_MS", i))

    def test_aim_at_sets_this_docks_icon(self):
        from unittest import mock
        d = mock.Mock()
        ok_b = mock.Mock()
        ok_b.get_x.return_value, ok_b.get_y.return_value = 300, 10
        ok_b.get_width.return_value, ok_b.get_height.return_value = 64, 64
        tile = mock.Mock()
        tile.compute_bounds.return_value = (True, ok_b)
        geo = mock.Mock(x=0, y=0)
        d._monitor_of.return_value.get_geometry.return_value = geo
        t = mock.Mock()
        D.Dock._aim_at(d, tile, [t])
        surface = d.get_native.return_value.get_surface.return_value
        d.manager.set_rectangle.assert_called_once_with(t, surface, 300, 10, 64, 64)


class MinimizeAimTest(unittest.TestCase):
    """Vini: on the second display, minimizing from the Dock still flew to
    the first display's icon -- the clicked Dock is magnified, so its own
    update waited until after the minimize."""

    def test_click_aims_at_the_clicked_icon_first(self):
        src = open(D.__file__).read()
        body = src[src.index("    def _clicked(self"):src.index("    def launch_feedback")]
        i = body.index('self.cfg.get("click_minimizes", True)')
        self.assertLess(body.index("self._aim_at(tile, self._here(wins))", i),
                        body.index("self.manager.minimize(t)", i))
        self.assertIn("if d is not self:", body[i:])

    def test_here_keeps_this_displays_windows(self):
        from unittest import mock
        t1, t2 = mock.Mock(app_id="chrome", title="A"), mock.Mock(app_id="chrome", title="B")
        d = mock.Mock()
        d._windows_here.return_value = ({("chrome", "B")}, {("chrome", "A"), ("chrome", "B")})
        with mock.patch.object(D, "_DOCKS", [object(), object()]):
            self.assertEqual(D.Dock._here(d, [t1, t2]), [t2])
        d._windows_here.return_value = (set(), None)                  # Wayfire didn't answer
        with mock.patch.object(D, "_DOCKS", [object(), object()]):
            self.assertEqual(D.Dock._here(d, [t1, t2]), [t1, t2])
        with mock.patch.object(D, "_DOCKS", [object()]):              # one Dock
            self.assertEqual(D.Dock._here(d, [t1]), [t1])


class DockDisplayTest(unittest.TestCase):
    """Vini: windows on the external display flew into the laptop's Dock (any
    minimize) -- the laptop's Dock asked GTK which display it was on and got
    the other one. It uses the display its window was put on."""

    def test_the_display_it_was_put_on(self):
        from unittest import mock
        d = mock.Mock()
        mine = mock.Mock()
        mine.is_valid.return_value = True
        d.get_root.return_value.shown_on = mine
        self.assertIs(D.Dock._monitor_of(d, mock.Mock()), mine)
        d.get_display.return_value.get_monitor_at_surface.assert_not_called()

    def test_no_gtk_guess_left(self):
        src = open(D.__file__).read()
        body = src[src.index("class Dock(Gtk.Box)"):src.index("class DockWindow")]
        self.assertEqual(body.count("get_monitor_at_surface("), 1)          # only the fallback
        self.assertIn("self.shown_on = m", src)
        self.assertIn("self.shown_on = monitor", src)


class DockDotsTest(unittest.TestCase):
    """Vini: up to two dots, then a small + for more windows."""

    def test_two_dots_then_plus(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, Gtk
        if not Gtk.init_check():
            self.skipTest("no display")
        from unittest import mock
        dock = mock.Mock(edge="bottom", cfg={"icon_size": 48}, away=Gtk.PositionType.TOP)
        with mock.patch.object(D.ui.label, "HoverLabel"):
            t = D.DockTile(dock, "x", Gio.ThemedIcon.new("folder"), lambda *_: 0)
        on = lambda: [d.has_css_class("on") for d in t.dots]
        t.set_running(1)
        self.assertEqual((on(), t.plus.get_visible()), ([True, False], False))
        t.set_running(2)
        self.assertEqual((on(), t.plus.get_visible()), ([True, True], False))
        t.set_running(5)
        self.assertEqual((on(), t.plus.get_visible()), ([True, True], True))
        t.set_running(0)
        self.assertFalse(t.plus.get_visible())
