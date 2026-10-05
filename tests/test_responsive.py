"""Responsiveness: every surface fits small laptops (1280x720, 1366x768) and
adapts to big displays where macOS does (xvfb-run -a python3 -m unittest
tests.test_responsive). Sizes are checked as numbers, or measured with
Gtk.Widget.measure(); the display size is mocked where it matters, so the
virtual screen's own size doesn't change the result."""
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.ui import panel as P, window as W  # noqa: E402

V, H = Gtk.Orientation.VERTICAL, Gtk.Orientation.HORIZONTAL
SMALL = [(1280, 720), (1366, 768)]
BIG = [(1920, 1080), (2560, 1440), (3840, 2160)]


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def _app(name):
    app = Adw.Application(application_id=f"io.github.vinioliveiras.sonata2.test.resp.{name}")
    app.register(None)
    return app


def work_area(screen):
    return screen[0] - W.WORK_MARGIN_W, screen[1] - W.WORK_MARGIN_H


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()


# -- app windows -----------------------------------------------------------------------------------
class WindowSizeTests(Base):
    def test_fit_size_shrinks_to_the_work_area_never_grows(self):
        for screen in SMALL:
            w, h = W.fit_size(1120, 720, screen)
            self.assertLessEqual((w, h), work_area(screen))
            self.assertLessEqual(h, screen[1] - 24 - 72)        # under the menu bar, above the Dock
        for screen in BIG:
            self.assertEqual(W.fit_size(1120, 720, screen), (1120, 720))
        self.assertEqual(W.fit_size(400, 300, (1280, 720)), (400, 300))
        self.assertEqual(W.fit_size(1120, 720, None), (1120, 720))

    def test_screen_size_reads_the_display(self):
        disp = Gdk.Display.get_default()
        mons = disp.get_monitors()
        sizes = [(m.get_geometry().width, m.get_geometry().height)
                 for m in (mons.get_item(i) for i in range(mons.get_n_items()))]
        self.assertEqual(W.screen_size(), min(sizes, key=lambda s: s[0] * s[1]))
        win = Gtk.Window()
        win.present()
        settle()
        self.assertIn(W.screen_size(win), sizes)                # a shown widget: its own display
        win.destroy()

    def _windows(self):
        from sonata2.activity.window import TaskManagerWindow
        from sonata2.assistant.window import AssistantWindow
        from sonata2.calendar.window import CalendarWindow
        from sonata2.camera.window import CameraWindow
        from sonata2.music.window import MusicWindow
        from sonata2.notes.window import NotesWindow
        from sonata2.settings.app import Settings
        app = _app("windows")
        return [lambda: Settings(app), lambda: NotesWindow(app), lambda: AssistantWindow(app),
                lambda: CalendarWindow(app, folder=tempfile.mkdtemp()), lambda: TaskManagerWindow(app),
                lambda: MusicWindow(app, scan=False, mpris=False), lambda: CameraWindow(app)]

    def test_app_windows_open_inside_a_small_laptop(self):
        """Regression: Music / Calendar (720 px tall), Camera, Task Manager,
        Assistant, Notes, Settings opened taller than a 1280x720 display."""
        for screen in SMALL:
            with mock.patch.object(W, "screen_size", return_value=screen):
                for make in self._windows():
                    win = make()
                    w, h = win.get_default_size()
                    self.assertLessEqual(w, work_area(screen)[0], type(win).__name__)
                    self.assertLessEqual(h, work_area(screen)[1], type(win).__name__)
                    win.destroy()

    def test_app_windows_keep_their_size_on_big_displays(self):
        with mock.patch.object(W, "screen_size", return_value=(1920, 1080)):
            from sonata2.music.window import MusicWindow
            win = MusicWindow(_app("bigmusic"), scan=False, mpris=False)
            self.assertEqual(tuple(win.get_default_size()), (1120, 720))
            win.destroy()

    def test_textedit_restores_a_big_window_inside_a_small_display(self):
        """Regression: a size saved on a 4K reopened bigger than the laptop."""
        from sonata2.textedit import window as TE
        win = Gtk.Window()
        win.docs = []
        with mock.patch.object(W, "screen_size", return_value=(1366, 768)):
            TE.TextEditWindow._restore(win, {"width": 2400, "height": 1400})
        self.assertLessEqual(tuple(win.get_default_size()), work_area((1366, 768)))
        win.destroy()

    def test_files_can_be_narrow(self):
        """Regression: Files' list columns set its minimum width (~1040 px),
        whatever the view: it couldn't be half a 1920 display or fit 1024."""
        from sonata2.files.window import FilesWindow
        win = FilesWindow(_app("files"))
        self.assertLessEqual(win.measure(H, -1)[0], 700)
        win.destroy()

    def test_videos_and_preview_size_from_the_display_not_monitor_zero(self):
        import inspect
        from sonata2.preview import window as PW
        from sonata2.videos import window as VW
        for mod in (PW, VW):
            src = inspect.getsource(mod)
            self.assertNotIn("get_monitors().get_item(0)", src, mod.__name__)
            self.assertIn("ui.window.screen_size()", src, mod.__name__)


# -- menu bar panels ---------------------------------------------------------------------------------
class PanelTests(Base):
    def _anchor(self):
        win = Gtk.Window()
        btn = Gtk.Button(label="x")
        win.set_child(btn)
        win.present()
        settle()
        self.addCleanup(win.destroy)
        return btn

    def _rows(self, n):
        col = P.column()
        for i in range(n):
            col.append(P.row(None, f"Network {i}", on_click=lambda: None))
        return col

    def test_max_height(self):
        self.assertEqual(P.max_height(720), 720 - P.SCREEN_MARGIN)
        self.assertGreaterEqual(P.max_height(100), 200)

    def test_long_panel_scrolls_instead_of_leaving_the_screen(self):
        """Regression: a long Wi-Fi list / Sound panel with many apps grew
        taller than a 720 px display, cut off at the bottom."""
        btn = self._anchor()
        with mock.patch.object(W, "screen_size", return_value=(1280, 720)):
            pop = P.popup(btn, self._rows(60), width=280)
        settle()
        h = pop.get_child().measure(V, 280)[1]
        self.assertLessEqual(h, P.max_height(720))
        self.assertEqual(pop.get_child().measure(H, -1)[0], 280)     # the fixed width stays
        pop.popdown()
        settle()

    def test_short_panel_keeps_its_natural_size(self):
        btn = self._anchor()
        col = self._rows(3)
        natural = col.measure(V, 280)[1]
        with mock.patch.object(W, "screen_size", return_value=(1280, 720)):
            pop = P.popup(btn, col, width=280)
        settle()
        self.assertEqual(pop.get_child().measure(V, 280)[1], natural)
        pop.popdown()
        settle()

    def test_control_center_width_and_height_follow_the_display(self):
        from sonata2.shell import controlcenter as CCL, topbar as T
        self.assertEqual(CCL.width_for(1280), CCL.WIDTH_MIN)
        self.assertEqual(CCL.width_for(1366), CCL.WIDTH_MIN)
        self.assertEqual(CCL.width_for(3840), CCL.WIDTH_MAX)
        self.assertEqual(CCL.width_for(0), CCL.WIDTH_MIN)
        widths = [CCL.width_for(w) for w, _h in SMALL + BIG]
        self.assertEqual(widths, sorted(widths))
        for _w, h in SMALL + BIG:
            mon = types.SimpleNamespace(get_geometry=lambda h=h: types.SimpleNamespace(width=_w, height=h))
            stub = types.SimpleNamespace(_monitor=lambda mon=mon: mon)
            self.assertLessEqual(T.ControlCenter._max_height(stub), h - T.BAR_H)

    def test_menu_bar_app_name_ellipsizes(self):
        """Regression: a long app name (GIMP's) with the tray and stats could
        push the clock off a 1280 px menu bar."""
        from gi.repository import Pango
        from sonata2.shell import topbar as T
        bar = T.Bar(None)
        bar._set_text(bar.app_btn, "GNU Image Manipulation Program " * 4)
        label = bar.app_btn.get_child().get_last_child()
        self.assertEqual(label.get_ellipsize(), Pango.EllipsizeMode.END)
        self.assertLess(bar.get_start_widget().measure(H, -1)[1], 600)


# -- overlays: Spotlight, switcher, share picker, Launchpad ---------------------------------------------
class OverlayTests(Base):
    def test_spotlight_layout(self):
        from sonata2.shell import spotlight as S
        for _w, h in SMALL + BIG:                     # macOS: 22 % down, 400 px of results
            self.assertEqual(S.layout_for(h), (int(h * 0.22), S.RESULTS_H))
        for h in (384, 450, 540):                     # a small laptop at 2x: still on screen
            top, results = S.layout_for(h)
            self.assertLessEqual(top + S.FIELD_H + results, h)
            self.assertGreater(results, 0)

    def test_spotlight_is_placed_again_on_each_display(self):
        """Regression: the margin was computed once (first display): opened
        later on a laptop after a 4K, the results ran off the screen."""
        from sonata2.shell import spotlight as S
        sp = S.Spotlight.__new__(S.Spotlight)
        sp.scroller = Gtk.ScrolledWindow()
        panel = Gtk.Box()
        for h in (2160, 720):
            mon = types.SimpleNamespace(get_geometry=lambda h=h: types.SimpleNamespace(width=1, height=h))
            S.Spotlight._place(sp, panel, mon)
            self.assertEqual(panel.get_margin_top(), S.layout_for(h)[0])

    def test_switcher_icons_fit_the_display(self):
        """Regression: 11+ open apps made the switcher wider than a laptop."""
        from sonata2.shell import switcher as SW
        self.assertEqual(SW.icon_size(5, 1280), SW.ICON)
        self.assertEqual(SW.icon_size(9, 1280), SW.ICON)
        self.assertEqual(SW.icon_size(14, 3840), SW.ICON)
        for n in (10, 12, 16, 20):
            for w, _h in SMALL:
                s = SW.icon_size(n, w)
                self.assertGreaterEqual(s, SW.ICON_MIN)
                self.assertLessEqual(n * (s + SW.ITEM_PAD) + SW.CHROME_W, w)

    def test_share_picker_scrolls_with_many_windows(self):
        """Regression: 12 windows to share pushed Cancel / Share off a 720 px display."""
        from sonata2.shell import sharepicker as SP
        items = [{"name": f"Window {i}", "icon": "window-symbolic", "value": i} for i in range(12)]
        with mock.patch.object(W, "screen_size", return_value=(1280, 720)):
            p = SP.Picker(None, "Pick", "text", items, "Share", lambda v: None)
        self.assertLessEqual(p.scroller.get_max_content_height(), 720 - SP.CHROME_H)
        self.assertLessEqual(p.get_child().measure(V, -1)[1], 720)
        p.destroy()

    def test_launchpad_adapts(self):
        from sonata2 import launchpad_model as M
        from sonata2.shell.launchpad_window import panel_size
        for w, h in SMALL + BIG:
            pw, ph = panel_size(w, h)
            self.assertLessEqual(pw, w)
            self.assertLessEqual(ph, h - 24)
        self.assertEqual(panel_size(1280, 720), (588, 440))
        self.assertEqual(panel_size(3840, 2160), (940, 680))
        cols, rows = M.COLS, M.ROWS
        try:
            M.set_grid(20, 1)
            self.assertEqual((M.COLS, M.ROWS), (8, 3))
            M.set_grid(7, 5)
            self.assertEqual(M.PER_PAGE, 35)
        finally:
            M.set_grid(cols, rows)


# -- login / lock: the password on the main display ------------------------------------------------------
class LoginScreenTests(Base):
    def test_greeter_puts_the_login_on_the_main_display(self):
        """Regression: the column went to monitor 0 even when another one is
        the main display (built-in panel / Settings' choice)."""
        from sonata2.shell import greeter as G, monitors
        first = Gdk.Display.get_default().get_monitors().get_item(0)
        for main, want in ((first, True), (object(), False)):
            seen = []
            with mock.patch.object(G, "users", lambda: [G.User("vini", "Vini")]), \
                    mock.patch.object(G, "sessions", lambda: []), \
                    mock.patch.object(G, "load_state", lambda: {}), \
                    mock.patch.object(G.Greeter, "_restore_modes", lambda self: None), \
                    mock.patch.object(G.Greeter, "_window", lambda self, m, primary: seen.append(primary)), \
                    mock.patch.object(monitors, "main", lambda want=None: main):
                G.Greeter(None)
            self.assertEqual(seen[0], want)


    def test_greeter_users_fit_small_displays(self):
        """Vini: many users ran off a small display -- they wrap into rows that
        fit, with smaller pictures from 3 rows on, and scroll past half the height."""
        from sonata2.shell import greeter as G
        self.assertEqual(G.user_grid(3, 1920), (3, 96))
        per_row, px = G.user_grid(12, 1280)
        self.assertLessEqual(per_row * G.USER_TILE, 1280 * 0.8)
        self.assertEqual(G.user_grid(30, 1280)[1], 72)
        g = G.Greeter.__new__(G.Greeter)
        g.users = [G.User(f"u{i}", f"User {i}") for i in range(12)]
        g._pick = lambda u: None
        with mock.patch.object(ui.window, "screen_size", return_value=(1280, 720)):
            page = g._users_page()
        self.assertEqual(page.grid.get_max_children_per_line(), per_row)
        self.assertLessEqual(page.get_max_content_height(), 720 * 0.6)


if __name__ == "__main__":
    unittest.main()
