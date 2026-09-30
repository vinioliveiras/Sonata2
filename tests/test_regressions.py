"""Regression tests: one per bug Vini reported and we fixed, so they don't
come back. Each test names the bug it guards. Add one with every fix.

Run: xvfb-run -a python3 -m unittest tests.test_regressions
(a session bus helps the Launchpad tests: dbus-run-session -- xvfb-run ...)"""
import os
import pathlib
import re
import tempfile
import unittest

_home = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = os.path.join(_home, ".config")
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from sonata2 import config, ui  # noqa: E402

Adw.init()
ui.setup()


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    ctx = GLib.MainContext.default()
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


def rule(css: str, selector: str) -> str:
    """The body of the first CSS rule whose selector list contains `selector`."""
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        if selector in m.group(1):
            return m.group(2)
    return ""


def render(widget) -> tuple:
    """The widget drawn to RGBA bytes: (bytes, width, height, stride)."""
    w, h = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h)
    tex = widget.get_native().get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
    d = Gdk.TextureDownloader.new(tex)
    d.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = d.download_bytes()
    return data.get_data(), tex.get_width(), tex.get_height(), stride


class DockRegressions(unittest.TestCase):
    def test_tiles_do_not_animate_padding(self):
        """The Dock stretched sideways on the first Light/Dark switch:
        Adwaita's buttons transition "all" (padding included)."""
        from sonata2.shell import dock
        self.assertIn("transition: none", rule(dock.CSS, ".dock-tile, .dock-tile:hover"))

    def test_bounce_has_no_per_keyframe_curves(self):
        """GTK ignores animation-timing-function inside keyframes (warnings,
        linear bounce): the arc is sampled instead."""
        from sonata2.shell import dock
        for name in ("dock-bounce-up", "dock-bounce-left", "dock-bounce-right"):
            body = dock.CSS[dock.CSS.index("@keyframes " + name):]
            body = body[:body.index("} }") + 3]
            self.assertNotIn("animation-timing-function", body)

    def test_menu_minimize_then_maximize(self):
        """Dock menu: "Hide" became Minimize; Maximize when all windows are minimized."""
        from sonata2.shell import dock_menu
        got = {}
        real = ui.menu.popup
        ui.menu.popup = lambda _w, sections, **_k: got.setdefault("s", sections)
        calls = []

        class T:
            def __init__(self, m):
                self.minimized, self.title = m, "w"

        class Mgr:
            def activate(self, t): calls.append("activate")
            def set_maximized(self, t, on): calls.append(("max", on))
            def minimize(self, t): calls.append("min")
            def close(self, t): pass

        class Tile:
            info, name = None, "App"
            label = type("L", (), {"popdown": lambda self: None})()

        try:
            for minimized, want in ((False, "Minimize"), (True, "Maximize")):
                got.clear()
                dock = type("D", (), {"windows": {"k": [T(minimized)]}, "cfg": {"pinned": []},
                                      "manager": Mgr(), "away": Gtk.PositionType.TOP})()
                dock_menu.app_menu(dock, "k", Tile())
                labels = [i.label for sec in got["s"] for i in sec]
                self.assertIn(want, labels)
                self.assertNotIn("Hide", labels)
            item = next(i for sec in got["s"] for i in sec if i.label == "Maximize")
            item.on_activate()
            self.assertIn(("max", True), calls)
        finally:
            ui.menu.popup = real


class WindowRegressions(unittest.TestCase):
    def test_sidebar_divider_is_opaque(self):
        """A 1 px see-through gap between sidebar and content showed the
        wallpaper (Task Manager, Music: paneds with their own css_classes)."""
        win = Gtk.Window(css_classes=["sonata-unified"])
        ui.window.standard(win)
        paned = Gtk.Paned(start_child=Gtk.Box(width_request=100), end_child=Gtk.Box(hexpand=True),
                          css_classes=["some-app-paned"])       # replaces GTK's .horizontal class
        paned.set_position(100)
        win.set_child(paned)
        win.set_default_size(300, 120)
        win.present()
        settle(400)
        sep = paned.get_start_child().get_next_sibling()
        ok, p = sep.compute_point(win, Graphene.Point().init(0, 0))
        self.assertTrue(ok)
        px, w, h, stride = render(win)
        scale = w / win.get_width()
        x, y = int((p.x + sep.get_width() / 2) * scale), int(h / 2)
        self.assertEqual(px[y * stride + x * 4 + 3], 255, "divider pixel is see-through")
        win.destroy()

    def test_spinner_is_small_and_centred(self):
        """Disk Manager's loading spinner filled the whole page, off-centre."""
        s = ui.progress.spinner(size=32)
        self.assertEqual(s.get_halign(), Gtk.Align.CENTER)
        self.assertEqual(s.get_valign(), Gtk.Align.CENTER)
        self.assertEqual(s.get_size_request(), (32, 32))

    def test_window_glass_darker_than_dock_glass(self):
        """Title bars/toolbars/sidebars use their own, darker glass."""
        for dark in (False, True):
            pal = ui.tokens.palette(dark, "mac")
            self.assertEqual(pal["titlebar_bg"], pal["window_glass"])
            lum = lambda c: sum(int(v) for v in re.findall(r"[\d.]+", c)[:3])      # noqa: E731
            self.assertLess(lum(pal["window_glass"]), lum(pal["glass_tint"]))


class IconRegressions(unittest.TestCase):
    def test_trash_and_launchpad_follow_appearance(self):
        from sonata2 import icons
        real = icons._dark
        try:
            for dark, trash, lp in ((False, "user-trash", "sonata-launchpad-light"),
                                    (True, "user-trash-dark", "sonata-launchpad")):
                icons._dark = lambda d=dark: d
                self.assertEqual(icons.for_appearance(Gio.ThemedIcon.new("user-trash")).get_names()[0], trash)
                self.assertEqual(icons.for_appearance(Gio.ThemedIcon.new("sonata-launchpad")).get_names()[0], lp)
        finally:
            icons._dark = real
        base = os.path.join(icons.ICONS_DIR, "Sonata")
        for f in ("places/scalable/user-trash-dark.svg", "places/scalable/user-trash-full-dark.svg",
                  "apps/scalable/sonata-launchpad-light.svg", "actions/symbolic/sonata-screenshot-symbolic.svg"):
            self.assertTrue(os.path.exists(os.path.join(base, f)), f)

    def test_music_icon_is_the_pink_one(self):
        from sonata2.music import window
        self.assertIn("Icon=gnome-music", pathlib.Path(window.__file__).read_text())

    def test_tray_icons_are_one_tone(self):
        """Menu bar tray icons must never be coloured: a two-tone icon becomes
        a silhouette with its light details cut out."""
        from sonata2.shell import tray
        n = 16
        buf = bytearray()
        for y in range(n):
            for x in range(n):
                inner = 5 <= x < 11 and 5 <= y < 11
                buf += bytes((255, 255, 255, 255) if inner else (88, 101, 242, 255))
        tex = Gdk.MemoryTexture.new(n, n, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(buf)), n * 4)
        out = tray._silhouette(tex)
        d = Gdk.TextureDownloader.new(out)
        d.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = d.download_bytes()
        px = data.get_data()
        self.assertEqual(px[8 * stride + 8 * 4 + 3], 0)          # the light centre: a hole
        self.assertGreater(px[1 * stride + 1 * 4 + 3], 200)      # the body: solid


class MusicRegressions(unittest.TestCase):
    def test_plays_through_classic_playbin(self):
        """Music crashed on MP3s: GTK's media backend uses playbin3, whose
        decodebin3 aborts ("assertion failed: (collection)")."""
        from sonata2.music import player
        Gst = player._gst()
        if Gst is None:
            self.skipTest("no GStreamer typelib here")
        path = os.path.join(_home, "silence.wav")
        import struct
        with open(path, "wb") as f:               # 0.2 s of silence
            data = b"\0" * 8820
            f.write(b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " +
                    struct.pack("<IHHIIHH", 16, 1, 1, 22050, 44100, 2, 16) + b"data" +
                    struct.pack("<I", len(data)) + data)
        p = player.Player()
        p.set_volume(0)
        self.assertTrue(p.load(path, play=False))
        self.assertIsInstance(p.stream, player.GstStream)
        self.assertEqual(p.stream.bin.get_factory().get_name(), "playbin")
        p.stop()

    def test_songs_and_videos_open_in_sonata_apps(self):
        """Opening an MP3 from Files did nothing: no default app for audio."""
        env = pathlib.Path(__file__).parent.parent.joinpath("tools", "session-env.sh").read_text()
        self.assertIn("audio/mpeg", env)
        self.assertIn("sonata2.music.desktop", env)
        self.assertIn("sonata2.videos.desktop", env)

    def test_volume_icons_have_room(self):
        """The small volume icon sat against the LCD."""
        src = pathlib.Path(__file__).parent.parent.joinpath("sonata2", "music", "window.py").read_text()
        self.assertRegex(src, r"vol = Gtk.Box\(spacing=6, valign=Gtk.Align.CENTER, margin_start=\d+")


class ButtonRegressions(unittest.TestCase):
    def test_push_buttons_are_macos_style(self):
        """The Save panel's buttons (and others) used Adwaita's look (bold,
        big pills) instead of Sonata's macOS push buttons."""
        root = pathlib.Path(__file__).parent.parent / "sonata2"
        for f in root.rglob("*.py"):
            if f.parent.name == "ui":
                continue
            src = f.read_text()
            self.assertNotIn("suggested-action", src, f"{f}: use sonata-button + default")
            self.assertNotIn("destructive-action", src, f"{f}: use sonata-button + destructive")
        chooser = (root / "files" / "chooser.py").read_text()
        self.assertEqual(chooser.count('"sonata-button"'), 3)


class TopbarRegressions(unittest.TestCase):
    def test_now_playing_title_does_not_widen_control_center(self):
        from sonata2.shell import topbar

        class P:
            active, playing, art = True, False, ""
            title = "A very very long song title that goes on and on and on forever"
            artist = "Someone with a long name too"
            listeners = []
            def call(self, _m): pass

        m = topbar.now_playing_module(P())
        short = P()
        short.title, short.artist = "A", "B"
        m2 = topbar.now_playing_module(short)
        self.assertEqual(m.measure(Gtk.Orientation.HORIZONTAL, -1)[1],
                         m2.measure(Gtk.Orientation.HORIZONTAL, -1)[1])


class SwitcherRegressions(unittest.TestCase):
    def test_mouse_hover_selects_and_click_switches(self):
        from sonata2.shell import switcher

        class T:
            def __init__(self, a):
                self.app_id, self.minimized = a, False

        class M:
            def __init__(self):
                self.toplevels = [T("a1"), T("a2"), T("a3")]
                self.act = []
            def activate(self, t): self.act.append(t.app_id)
            def close(self, t): pass

        app = Gtk.Application(application_id="io.test.regress.switcher")
        app.register(None)
        m = M()
        w = switcher.Switcher(app, m, [])
        w.step(1)
        settle(500)
        ok, r = w.items[2].compute_bounds(w.panel)
        cx, cy = r.origin.x + r.size.width / 2, r.origin.y + r.size.height / 2
        w._pointer(None, cx, cy)
        self.assertEqual(w.index, 1)                 # resting pointer: no change
        w._pointer(None, cx + 6, cy)
        self.assertEqual(w.index, 2)
        ok, r = w.items[0].compute_bounds(w.panel)
        w._clicked(None, 1, r.origin.x + 4, r.origin.y + 4)
        self.assertEqual(m.act, ["a1"])
        w.destroy()


class LaunchpadRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2.shell import launchpad
        cls.lp = launchpad
        cls.app = Adw.Application(application_id="io.test.regress.launchpad")
        cls.app.register(None)
        cls.win = launchpad.Launchpad(cls.app)
        cls.win._dock_above = lambda *_a: None
        cls.win.set_default_size(1200, 760)
        cls.win.present()
        cls.win.open_launchpad()
        settle(1200)
        cls.apps = [i for p in cls.win.model.pages for i in p if isinstance(i, str)]
        if len(cls.apps) < 4:
            raise unittest.SkipTest("needs at least 4 installed apps")

    def test_hide_goes_to_hidden_folder_and_off_the_dock(self):
        w, a = self.win, self.apps[0]
        config.save("dock", {**config.load("dock", {}), "pinned": [a]})
        w.hide_app(a)
        self.assertIn(a, w.model.hidden)
        self.assertNotIn(a, w.model.all_apps())
        last = w._pages_with_hidden()[-1][-1]
        self.assertTrue(isinstance(last, dict) and last.get("locked") and a in last["apps"])
        self.assertNotIn(a, config.load("dock", {"pinned": []})["pinned"])

    def test_drop_on_hidden_folder_hides(self):
        w = self.win
        w.hide_app(self.apps[1])
        settle(300)
        grid = w.carousel.get_nth_page(w.carousel.get_n_pages() - 1)
        hidden = [x for x in grid if isinstance(x, self.lp.LaunchItem) and isinstance(x.item, dict)
                  and x.item.get("locked")][0]
        ok, r = hidden.compute_bounds(grid)
        cx, cy = r.origin.x + r.size.width / 2, r.origin.y + r.size.height / 2
        dragged = self.apps[3]
        w._drag = {"item": dragged, "widget": w._item_widget(dragged), "folder": None, "target": None}
        w.drag_over(grid, cx, cy)
        settle(self.lp.FOLDER_HOLD_MS + 200)
        w.drag_drop(grid, cx, cy)
        w._drag = None
        self.assertIn(dragged, w.model.hidden)

    def test_hidden_folder_shows_nothing_until_unlocked(self):
        w = self.win
        w.hide_app(self.apps[2])
        settle(200)
        tile = w._item_widget(w._hidden_item)
        names = []

        def walk(x):
            if isinstance(x, Gtk.Image) and x.get_storage_type() == Gtk.ImageType.GICON:
                names.append(x.get_gicon().to_string())
            c = x.get_first_child()
            while c:
                walk(c)
                c = c.get_next_sibling()
        walk(tile)
        self.assertFalse(any(n for n in names if "executable" not in n and "lock" not in n),
                         "real app icons in the locked folder")
        w._unlocked = False
        w.activate_item(tile)
        self.assertIsNone(w.folder_view[1], "opened without the password")
        w._close_folder()

    def test_hidden_apps_not_in_spotlight(self):
        from sonata2.shell import spotlight
        src = pathlib.Path(spotlight.__file__).read_text()
        self.assertIn('get("hidden"', src)


class SettingsRegressions(unittest.TestCase):
    def test_hidden_section_locks_again(self):
        from sonata2.settings import app as st
        app = Adw.Application(application_id="io.test.regress.settings")
        app.register(None)
        w = st.Settings(app, "hidden")
        w.present()
        settle(400)
        self.assertIn("hidden", w.pages)
        w.select("wifi")
        settle(200)
        self.assertNotIn("hidden", w.pages)
        w.destroy()


if __name__ == "__main__":
    unittest.main()
