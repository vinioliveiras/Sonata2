"""Layout tests (Vini: "super importante"): every surface, really laid out by
GTK, checked for what a video of a bug shows -- something cut off or sticking
out, two things drawn over each other, a panel that grows a band, an item
that jumps when you only renamed it. Light and dark, small and big displays.

    PYTHONPATH=.:tests xvfb-run -a python3 -m unittest test_layout
    SONATA_LAYOUT_SHOTS=/tmp/shots ...   # also saves a PNG of each surface

The checks live in tests/layoutcheck.py; a failure names the widget and by
how many pixels it's off."""
import os
import tempfile
import types
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402

import layoutcheck as LC  # noqa: E402



def themes():
    """(name, dark) -- each test runs in both."""
    return (("light", False), ("dark", True))


def set_dark(dark: bool) -> None:
    Adw.StyleManager.get_default().set_color_scheme(
        Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    LC.settle(50)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def tearDown(self):
        set_dark(False)

    def clean(self, root, what, allow=()):
        problems = LC.problems(root, allow)
        self.assertEqual(problems, [], f"{what}:\n  " + "\n  ".join(problems))

    def show(self, child, width=-1, height=-1):
        win = Gtk.Window(child=child, default_width=width, default_height=height)
        win.present()
        self.addCleanup(win.destroy)
        LC.settle(250)
        return win


# -- Control Center -----------------------------------------------------------------------------------
class ControlCenterLayout(Base):
    """Vini: opening Add Controls left an empty band on the left (long names
    widened the panel); adding CPU and GPU once made it huge."""

    def setUp(self):
        from sonata2.shell import topbar as T
        self.T = T
        self.bar = types.SimpleNamespace(
            _poll=lambda: None, _set_volume=lambda v: None, notifications=None, monitor=None,
            get_native=lambda: None, wifi_column=lambda close: Gtk.Label(label="Networks"),
            bluetooth_column=lambda close: Gtk.Label(label="Devices"))
        for p in (mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"),
                  mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())):
            p.start()
            self.addCleanup(p.stop)

    def panel(self, order):
        from sonata2.shell import controlcenter as C
        config.save("controlcenter", {"modules": order})
        cc = self.T.ControlCenter(self.bar)
        win = self.show(cc)
        return cc, win

    def same_width(self, cc, win, what):
        self.assertEqual(cc.get_width(), cc.width, what)
        self.assertLessEqual(win.get_width(), cc.width + 1, f"{what}: a band beside the panel")

    def test_every_module_fits_its_place(self):
        from sonata2.shell import controlcenter as C
        for theme, dark in themes():
            set_dark(dark)
            for name, order in (("default", list(C.DEFAULT_ORDER)), ("all", list(C.CATALOG))):
                cc, win = self.panel(order)
                what = f"Control Center {name} ({theme})"
                self.same_width(cc, win, what)
                self.clean(cc, what)
                for m, (col, row, w, h) in C.pack(cc.grid.order).items():
                    slot = cc.modules[m].get_parent()
                    self.assertEqual(slot.get_height(), C.span_height(h), f"{what}: {m} isn't {h} rows")
                LC.shot(cc, f"controlcenter-{name}-{theme}")
                win.destroy()

    def test_edit_mode_and_add_controls_keep_the_width(self):
        from sonata2.shell import controlcenter as C
        long = {m: (f"Video Memory Temperature (NVIDIA GeForce RTX) {m}", C.CATALOG[m][1])
                for m in C.CATALOG if m.startswith("stat_")}
        for theme, dark in themes():
            set_dark(dark)
            with mock.patch.dict(C.CATALOG, long):
                cc, win = self.panel(list(C.DEFAULT_ORDER))
                cc.grid.set_editing(True)
                LC.settle(100)
                self.same_width(cc, win, f"edit mode ({theme})")
                self.clean(cc, f"edit mode ({theme})")
                cc.add_btn.emit("clicked")
                LC.settle(400)                                           # the list slides in
                what = f"Add Controls with long names ({theme})"
                self.same_width(cc, win, what)
                self.clean(cc, what)
                widths = {b.get_width() for b in LC.children(cc.add_list)}   # two even columns
                self.assertEqual(len(widths), 1, what)
                self.assertGreater(widths.pop(), cc.add_list.get_width() / 2 - 16, what)
                LC.shot(cc, f"controlcenter-add-{theme}")
                win.destroy()

    def test_wifi_details_keep_the_width(self):
        from sonata2.shell import controlcenter as C
        cc, win = self.panel(list(C.DEFAULT_ORDER))
        cc.show_details("wifi")
        LC.settle(400)
        self.same_width(cc, win, "Wi-Fi details")
        self.clean(cc, "Wi-Fi details")


# -- menu bar -----------------------------------------------------------------------------------------
class MenuBarLayout(Base):
    def test_fits_and_lines_up_on_small_and_big_displays(self):
        from sonata2.shell import topbar as T
        for theme, dark in themes():
            set_dark(dark)
            for width in (1280, 1920, 2560):
                bar = T.Bar(None)
                bar.set_size_request(width, T.BAR_H)
                win = self.show(Gtk.ScrolledWindow(child=bar, hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                                   vscrollbar_policy=Gtk.PolicyType.NEVER), 1200, T.BAR_H)
                bar._set_text(bar.app_btn, "GNU Image Manipulation Program")     # a long app name
                LC.settle(100)
                what = f"menu bar {width} ({theme})"
                self.assertEqual((bar.get_width(), bar.get_height()), (width, T.BAR_H), what)
                self.clean(bar, what)
                cut = [f"{LC.name(w)} is cut" for w in LC.walk(bar) if isinstance(w, Gtk.Label)
                       and w.get_layout().is_ellipsized() and not w.is_ancestor(bar.app_btn)]
                self.assertEqual(cut, [], f"{what}: only the app name may shorten")
                for w in LC.walk(bar):                                # everything on one middle line
                    if w.has_css_class("topbar-item"):
                        x, y, _w, h = LC.rect(w, bar)
                        self.assertAlmostEqual(y + h / 2, T.BAR_H / 2, delta=1, msg=f"{what}: {LC.name(w)}")
                LC.shot(bar, f"menubar-{width}-{theme}")
                win.destroy()


# -- Dock ---------------------------------------------------------------------------------------------
class DockLayout(Base):
    def test_tiles_even_and_inside(self):
        from sonata2.shell import dock as D
        cfg = D.load_config()
        D.load_css(cfg)
        if len(cfg["pinned"]) < 3:
            self.skipTest("needs at least 3 installed default apps")
        for theme, dark in themes():
            set_dark(dark)
            dock = D.Dock(cfg)
            win = self.show(dock)
            what = f"Dock ({theme})"
            self.clean(dock, what)
            tiles = dock.app_tiles()
            self.assertEqual(len({(t.get_width(), t.get_height()) for t in tiles}), 1, f"{what}: tiles differ")
            xs = [LC.rect(t, dock)[0] for t in tiles]
            gaps = {round(b - a) for a, b in zip(xs, xs[1:])}
            self.assertEqual(len(gaps), 1, f"{what}: uneven spacing {gaps}")
            ys = {round(LC.rect(t, dock)[1]) for t in tiles}
            self.assertEqual(len(ys), 1, f"{what}: tiles not on one line")
            LC.shot(dock, f"dock-{theme}")
            win.destroy()


# -- Launchpad ----------------------------------------------------------------------------------------
class LaunchpadLayout(Base):
    def test_grid_cells_even_long_names_shorten(self):
        from sonata2.shell import launchpad as L
        from test_launchpad_window import FakeInfo
        apps = {f"app{i}": FakeInfo(("A Very Long Application Name Indeed " if i % 3 == 0 else "App ") + str(i),
                                    "Office;") for i in range(40)}
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()), \
                mock.patch.object(L, "installed_apps", return_value=apps):
            for theme, dark in themes():
                set_dark(dark)
                pad = L.Launchpad(None)
                pad._dock_above = lambda *_a: None
                pad.set_default_size(1400, 900)
                pad.open_launchpad()
                self.addCleanup(pad.destroy)
                root = pad.get_child()
                for _ in range(20):                                   # the grid sized (icons come in late)
                    LC.settle(100)
                    sizes = {(w.get_width(), w.get_height()) for w in LC.walk(root) if w.has_css_class("lp-item")}
                    if len(sizes) == 1:
                        break
                what = f"Launchpad ({theme})"
                self.clean(root, what, ("lp-more-dots",))           # the dots are nudged up to look centred
                tiles = [w for w in LC.walk(root) if w.has_css_class("lp-item")
                         and 0 <= LC.rect(w, root)[0] < root.get_width()]          # the page shown
                self.assertGreater(len(tiles), 20, what)
                sizes = {(t.get_width(), t.get_height()) for t in tiles}      # (the selection box: one size)
                self.assertEqual(len(sizes), 1, f"{what}: tiles differ {sorted(sizes)}")
                rows = {}
                for t in tiles:
                    x, y, _w, _h = LC.rect(t, root)
                    rows.setdefault(round(y), []).append(round(x))
                columns = {tuple(sorted(xs)) for xs in rows.values() if len(xs) == max(map(len, rows.values()))}
                self.assertEqual(len(columns), 1, f"{what}: full rows not in the same columns")
                LC.shot(root, f"launchpad-{theme}")
                pad.destroy()


# -- Settings -----------------------------------------------------------------------------------------
class SettingsLayout(Base):
    def test_every_page_fits_without_resizing_the_window(self):
        from sonata2.settings import app as S
        for theme, dark in themes():
            set_dark(dark)
            win = S.Settings(None)
            win.present()
            self.addCleanup(win.destroy)
            LC.settle(300)
            size = (win.get_width(), win.get_height())
            for sid, *_ in S.SECTIONS:
                win.select(sid)
                LC.settle(200)
                what = f"Settings > {sid} ({theme})"
                self.assertEqual((win.get_width(), win.get_height()), size, f"{what}: the window resized")
                self.clean(win, what)
                LC.shot(win, f"settings-{sid}-{theme}")
            win.destroy()


# -- lock screen --------------------------------------------------------------------------------------
class LockLayout(Base):
    def test_login_column_centred_on_every_display_size(self):
        from gi.repository import Gdk
        from sonata2.shell import lock as L
        from test_login_every_display import app
        ls = L.LockScreen.__new__(L.LockScreen)
        ls.app, ls.lock, ls.texture, ls.windows = app("layout"), mock.Mock(), None, []
        self.addCleanup(lambda: [w.destroy() for w in list(ls.windows)])
        mon = Gdk.Display.get_default().get_monitors().get_item(0)
        for size in ((1024, 600), (1280, 720), (1280, 1024)):     # (the virtual screen is 1280 wide)
            w = ls._window(mon, primary=not ls.windows)
            w.set_visible(False)
            w.set_default_size(*size)
            w.present()
            LC.settle(400)
            what = f"lock screen {size}"
            self.assertEqual((w.get_width(), w.get_height()), size, what)
            self.clean(w, what)
            for part in (ls.column.items[-1], ls.power.items[-1]):
                x, _y, pw, _ph = LC.rect(part, w)
                self.assertAlmostEqual(x + pw / 2, size[0] / 2, delta=1, msg=f"{what}: {LC.name(part)} off centre")
            LC.shot(w, f"lock-{size[0]}x{size[1]}")


# -- desktop ------------------------------------------------------------------------------------------
class DesktopLayout(Base):
    """Vini: a new folder moved when renamed; icons are cells of one size."""

    def setUp(self):
        from gi.repository import Gio
        from sonata2.shell import desktop as D
        self.D = D
        self.dir = tempfile.mkdtemp()
        for n in ("a.txt", "Quarterly report with a really long file name.pdf", "photos"):
            open(os.path.join(self.dir, n), "w").close()
        for p in (mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()),
                  mock.patch.object(D, "desktop_dir", return_value=Gio.File.new_for_path(self.dir)),
                  mock.patch.object(D, "_connected", return_value={"eDP-1"})):
            p.start()
            self.addCleanup(p.stop)

    def test_icons_are_even_cells_and_renaming_moves_nothing(self):
        for theme, dark in themes():
            set_dark(dark)
            desk = self.D.Desktop(screen="eDP-1", main=True)
            desk.resized(1920, 1080)
            win = self.show(desk, 1920, 1080)
            LC.settle(400)
            what = f"desktop ({theme})"
            items = list(desk.items.values())
            self.assertEqual(len(items), 3, what)
            self.assertEqual(len({(i.get_width(), i.get_height()) for i in items}), 1, f"{what}: cells differ")
            for item in items:
                self.clean(item, what)
            before = {n: LC.rect(i, desk) for n, i in desk.items.items()}
            with mock.patch("sonata2.shell.layer.take_keyboard"):
                desk.select([desk.items["a.txt"]])
                desk.rename_selection()
                LC.settle(200)
                entry = desk.items["a.txt"].lbl.get_next_sibling()
                self.assertIsInstance(entry, Gtk.Entry, what)
                entry.set_text("A much longer name than before, typed in.txt")
                LC.settle(150)
                self.clean(desk.items["a.txt"], f"{what}: renaming")
            after = {n: LC.rect(i, desk) for n, i in desk.items.items()}
            self.assertEqual(after, before, f"{what}: renaming moved icons")
            LC.shot(desk, f"desktop-{theme}")
            win.destroy()
