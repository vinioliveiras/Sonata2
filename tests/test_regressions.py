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

    def test_no_stretch_on_first_light_dark_switch(self):
        """Still stretched after the transition fix: the theme cross-fade put
        the Dock in an overlay on the first switch and its old picture (FILL)
        stretched while the Dock re-laid out."""
        from sonata2.shell import dock
        src = pathlib.Path(dock.__file__).read_text()
        body = src[src.index("class DockWindow"):]
        self.assertIn("self.sonata_no_fade = True", body[:body.index("self.cfg = cfg") + 20])
        theme_src = pathlib.Path(ui.theme.__file__).read_text()
        self.assertIn("halign=Gtk.Align.START, valign=Gtk.Align.START)", theme_src)

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


class MenuRegressions(unittest.TestCase):
    def test_dropdown_options_have_dividers(self):
        """Settings' pop-up lists had no hairline between the options."""
        src = pathlib.Path(ui.menu.__file__).read_text()
        self.assertIn("popover.menu listview > row:not(:first-child)", src)
        self.assertIn("background-image: linear-gradient(%(separator)s", src)


class CalendarWidgetRegressions(unittest.TestCase):
    def test_menu_bar_calendar_shows_events_and_opens_calendar(self):
        """The menu bar's calendar showed no events and didn't open Calendar."""
        import datetime as dt
        from sonata2.calendar import ics, model
        from sonata2.shell import notifications as N
        folder = tempfile.mkdtemp()
        real = N._calendar_folder
        N._calendar_folder = lambda: folder
        try:
            st = model.Store(folder)
            st.load()
            start = dt.datetime.now() + dt.timedelta(hours=1)
            st.put(ics.Event(uid="x", summary="Dentist", start=start, end=start + dt.timedelta(hours=1),
                             calendar=st.calendars[0].id))
            settle(300)
            events = N._upcoming()
            self.assertEqual([o.event.summary for o, _c in events], ["Dentist"])
            opened = []
            month = N._month(events, opened.append)
            up = N._up_next(events)
            texts = []

            def walk(w):
                if isinstance(w, Gtk.Label):
                    texts.append(w.get_label())
                c = w.get_first_child()
                while c:
                    walk(c)
                    c = c.get_next_sibling()
            walk(up)
            self.assertIn("Dentist", texts)
        finally:
            N._calendar_folder = real
        src = pathlib.Path(N.__file__).read_text()
        self.assertIn('"sonata-date:"', src)
        cal_src = pathlib.Path(__file__).parent.parent.joinpath("sonata2", "calendar", "window.py").read_text()
        self.assertIn('startswith("sonata-date:")', cal_src)

    def test_play_pause_icons_line_up(self):
        """Music's pause icon sat higher: at 22 px GTK took MacTahoe's
        24 px variant, drawn differently; Sonata's own scalable copies win."""
        from sonata2 import icons
        for n in ("media-playback-pause-symbolic", "media-playback-start-symbolic"):
            self.assertTrue(os.path.exists(os.path.join(icons.ICONS_DIR, "Sonata", "actions", "symbolic", n + ".svg")))


class EqualizerRegressions(unittest.TestCase):
    def test_stale_chain_processes_are_stopped(self):
        """Chrome/Spotify ignored the equalizer: chain processes left by a
        restarted menu bar kept their old curve, and streams linked to them."""
        import subprocess
        import sys
        import time
        from sonata2.backend import equalizer
        real = equalizer.RUN_CONF
        equalizer.RUN_CONF = "import time; time.sleep(30)"        # the fake chain's "-c" argument
        try:
            proc = subprocess.Popen(["pipewire", "-c", equalizer.RUN_CONF], executable=sys.executable)
            time.sleep(0.3)
            equalizer._kill_stale_chains()
            self.assertIsNotNone(proc.wait(timeout=3))
        finally:
            equalizer.RUN_CONF = real

    def test_gains_reach_every_copy_of_a_filter(self):
        src = pathlib.Path(__file__).parent.parent.joinpath("sonata2", "backend", "equalizer.py").read_text()
        self.assertIn("for nid in nids:", src)


class SettingsFollowUpRegressions(unittest.TestCase):
    def test_wifi_password_never_on_a_command_line(self):
        """wifi_connect passed the password to nmcli (visible in `ps`)."""
        from sonata2.backend import system
        calls = []
        real_run, real_dbus = system._run, system._wifi_connect_dbus
        system._run = lambda cmd, timeout=0: (calls.append(cmd), (0, ""))[1]
        system._wifi_connect_dbus = lambda ssid, pw: (True, "")
        try:
            system.wifi_connect("Home", "hunter22")
            system.wifi_connect("Cafe")
        finally:
            system._run, system._wifi_connect_dbus = real_run, real_dbus
        self.assertFalse(any("hunter22" in " ".join(c) for c in calls))
        self.assertEqual(calls, [["nmcli", "device", "wifi", "connect", "Cafe"]])

    def test_old_trash_items_are_removed(self):
        """"Remove items from the Trash after 30 days" did nothing in Sonata."""
        import datetime as dt
        from sonata2 import trash_cleanup
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "info"))
        os.makedirs(os.path.join(root, "files", "olddir"))
        now = dt.datetime(2026, 10, 1, 12, 0)
        for name, age in (("old.txt", 40), ("olddir", 31), ("new.txt", 3)):
            p = os.path.join(root, "files", name)
            if not os.path.exists(p):
                open(p, "w").close()
            with open(os.path.join(root, "info", name + ".trashinfo"), "w") as f:
                f.write(f"[Trash Info]\nPath=/x/{name}\nDeletionDate={(now - dt.timedelta(days=age)).isoformat()}\n")
        self.assertEqual(trash_cleanup.purge(30, root, now), 2)
        self.assertEqual(sorted(os.listdir(os.path.join(root, "files"))), ["new.txt"])
        self.assertEqual(os.listdir(os.path.join(root, "info")), ["new.txt.trashinfo"])

    def test_time_settings_go_back_when_they_fail(self):
        src = pathlib.Path(__file__).parent.parent.joinpath("sonata2", "settings", "app.py").read_text()
        self.assertIn('show_quietly(ntp_row, state["ntp"])', src)
        self.assertIn('show_quietly(row, zstate["zone"])', src)


class BluetoothRegressions(unittest.TestCase):
    def test_failed_connection_is_not_reported_as_done(self):
        """Bluetooth "lied": bluetoothctl's one-shot connect exits 0 even when
        it fails, and Settings said "Done"."""
        from sonata2.backend import bluez, system
        real_av, real_run = bluez.available, system._run
        bluez.available = lambda: False
        system._run = lambda cmd, timeout=0: (0, "Attempting to connect to AA:BB\nFailed to connect: "
                                                 "org.bluez.Error.Failed br-connection-page-timeout")
        try:
            ok, msg = system.bluetooth_connect_result("AA:BB", True)
        finally:
            bluez.available, system._run = real_av, real_run
        self.assertFalse(ok)
        self.assertIn("Failed", msg)

    def test_forget_and_pair_again_offered(self):
        """Saved devices can be forgotten, or forgotten and paired again."""
        from sonata2.backend import bluez, system
        real_av, real_run = bluez.available, system._run
        bluez.available = lambda: False
        system._run = lambda cmd, timeout=0: (0, "[DEL] Device AA:BB X\nDevice has been removed")
        try:
            self.assertTrue(system.bluetooth_forget("AA:BB")[0])
        finally:
            bluez.available, system._run = real_av, real_run
        src = pathlib.Path(__file__).parent.parent.joinpath("sonata2", "settings", "app.py").read_text()
        self.assertIn("Forget This Device…", src)
        self.assertIn("system.bluetooth_pair_again", src)

    def test_connection_that_drops_is_not_success(self):
        """The saved Xbox controller "connected" and dropped a second later;
        Sonata reported it connected."""
        from sonata2.backend import bluez
        import time as _t
        t0 = _t.monotonic()
        state = {"Connected": True, "ServicesResolved": True, "Icon": "input-gaming"}
        real_prop, real_stay = bluez._prop, bluez.STAY_S

        def prop(_bus, _path, name):
            if name in ("Connected", "ServicesResolved") and _t.monotonic() - t0 > 0.5:
                return False                      # dropped after half a second
            return state[name]
        bluez._prop, bluez.STAY_S = prop, 1.0
        try:
            ok, msg = bluez._settled(None, "/x")
        finally:
            bluez._prop, bluez.STAY_S = real_prop, real_stay
        self.assertFalse(ok)
        self.assertIn("xpadneo", msg)

    def test_pairing_error_but_connected_is_success(self):
        """"Pairing failed" was shown while the Xbox controller connected."""
        from sonata2.backend import bluez

        class Bus:
            def call_sync(self, _n, _p, _i, method, *_a):
                if method == "Pair":
                    raise GLib.Error.new_literal(GLib.quark_from_string("x"),
                                                 "org.bluez.Error.AuthenticationFailed", 1)
        props = {"Paired": False, "Trusted": True, "Connected": True, "ServicesResolved": True}
        real = (bluez._bus, bluez._device_path, bluez._prop, bluez.STAY_S)
        bluez._bus, bluez._device_path = Bus, lambda _b, _m: "/dev"
        bluez._prop, bluez.STAY_S = (lambda _b, _p, n: props[n]), 0.3
        try:
            ok, msg = bluez.connect("AA")
        finally:
            bluez._bus, bluez._device_path, bluez._prop, bluez.STAY_S = real
        self.assertTrue(ok, msg)

    def test_pairing_agent_answers_confirmations(self):
        """Still "Pairing failed": with no agent BlueZ can't confirm the
        pairing; Sonata's agent says yes to confirmations, no to PIN entry."""
        from sonata2.backend import bluez
        seen = []

        class Inv:
            def return_value(self, v): seen.append("ok")
            def return_dbus_error(self, name, msg): seen.append(name)
        for m in ("RequestConfirmation", "RequestAuthorization", "AuthorizeService", "RequestPinCode"):
            bluez._agent_call(None, "", "", "", m, None, Inv())
        self.assertEqual(seen, ["ok", "ok", "ok", "org.bluez.Error.Rejected"])
        Gio.DBusNodeInfo.new_for_xml(bluez.AGENT_XML)            # valid introspection
        src = pathlib.Path(__file__).parent.parent.joinpath("sonata2", "settings", "app.py").read_text()
        self.assertIn("bluez.register_agent()", src)

    def test_bluez_errors_read_as_sentences(self):
        from sonata2.backend import bluez
        e = GLib.Error.new_literal(GLib.quark_from_string("x"), "org.bluez.Error.Failed: br-connection-page-timeout", 1)
        self.assertIn("didn't answer", bluez._message(e))


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


def rows_of(widget, cls, out=None) -> list:
    """Every `cls` widget under `widget` (built pages, mapped or not)."""
    out = [] if out is None else out
    c = widget.get_first_child()
    while c is not None:
        if isinstance(c, cls):
            out.append(c)
        rows_of(c, cls, out)
        c = c.get_next_sibling()
    return out


class patched:
    """Swap attributes of a module for the duration of a with-block."""
    def __init__(self, mod, **values):
        self.mod, self.values, self.old = mod, values, {}

    def __enter__(self):
        for k, v in self.values.items():
            self.old[k] = getattr(self.mod, k)
            setattr(self.mod, k, v)

    def __exit__(self, *_a):
        for k, v in self.old.items():
            setattr(self.mod, k, v)


class SettingsAuditRegressions(unittest.TestCase):
    """Settings audit (options that didn't work / looked off)."""

    @classmethod
    def setUpClass(cls):
        from sonata2.settings import app as st
        cls.st = st

    def window(self, start="about"):
        w = self.st.Settings(None, start)
        w.present()
        settle(150)
        return w

    def switch(self, w, sid, title):
        return next(r for r in rows_of(w.pages[sid], Adw.SwitchRow) if r.get_title() == title)

    def test_group_titles_with_ampersand_show(self):
        """Trackpad's "Point & Click" / "Scroll & Zoom" titles were blank
        (group titles are markup)."""
        self.assertEqual(self.st.group("Point & Click").get_title(), "Point &amp; Click")

    def test_row_titles_are_plain_text(self):
        """Names with "&" or "<" (apps, Wi-Fi networks, Bluetooth devices)
        broke the row titles (markup)."""
        self.assertFalse(self.st.switch_row("A & B", True, lambda _v: None).get_use_markup())
        self.assertFalse(self.st.combo_row("A & B", [(1, "x")], 1, lambda _v: None).get_use_markup())

    def test_wifi_state_read_back_does_not_switch_the_radio(self):
        """Opening Wi-Fi ran `nmcli radio wifi on` again (the switch showing
        the state it read called its own handler)."""
        S = self.st.system
        calls = []
        with patched(S, wifi_enabled=lambda: True, wifi_scan=lambda _r=False: [],
                     set_wifi_enabled=lambda on: calls.append(on) or True):
            w = self.window("wifi")
            settle(400)
            self.assertTrue(w._wifi_switch.get_active())
            self.assertEqual(calls, [])
            w.destroy()

    def test_clock_switches_use_the_current_format(self):
        """Date & Time: turning the date off, then 12-hour, brought the date
        back (the 24-hour switch used the format from when the page opened)."""
        config.save("topbar", {"clock_format": "%a %-d %b  %H:%M"})
        w = self.window("datetime")
        w.select("datetime")
        settle(150)
        self.switch(w, "datetime", "Show the date").set_active(False)
        self.switch(w, "datetime", "Use a 24-hour clock").set_active(False)
        self.assertEqual(config.load("topbar", {"clock_format": ""})["clock_format"], "%a %-I:%M %p")
        w.destroy()

    def test_menubar_clock_shows_12_hour_weekday_format(self):
        """Menu Bar's Clock showed "Mon 28 Sep 21:41" for the format Date &
        Time makes with the date off and 12-hour (it wasn't in the list)."""
        config.save("topbar", {"clock_format": "%a %-I:%M %p"})
        w = self.window("menubar")
        combo = next(r for r in rows_of(w.pages["menubar"], Adw.ComboRow) if r.get_title() == "Clock")
        self.assertEqual(combo.values[combo.get_selected()], "%a %-I:%M %p")
        w.destroy()

    def test_section_rebuilt_when_its_settings_changed_elsewhere(self):
        """Switches kept an old state after Control Center / the menu bar /
        another section changed the same setting (pages were cached)."""
        import time
        config.save("dock", {"glass": True})
        w = self.window("dock")
        w.select("dock")
        settle(100)
        self.assertTrue(self.switch(w, "dock", "Translucent glass").get_active())
        w.select("about")
        settle(100)
        time.sleep(0.02)
        config.update("dock", glass=False)            # e.g. General's own "Translucent glass"
        w.select("dock")
        settle(100)
        self.assertFalse(self.switch(w, "dock", "Translucent glass").get_active())
        w.destroy()

    def test_slider_writes_are_coalesced(self):
        """Dragging a slider started a thread (wpctl / Wayfire write) per
        step, out of order: the last value could lose."""
        import threading
        import time
        done, lock = [], threading.Lock()

        def slow(v):
            time.sleep(0.05)
            with lock:
                done.append(v)
        w = self.window()
        for v in range(30):
            w._latest("k", slow, v)
        settle(600)
        self.assertLessEqual(len(done), 3)
        self.assertEqual(done[-1], 29)
        w.destroy()

    def test_wayfire_writes_do_not_lose_each_other(self):
        """Writes from several threads (Reduce motion's two keys, title bar
        colours, sliders) dropped each other's change in wayfire.ini."""
        import threading
        from sonata2 import wfconfig
        path = os.path.join(_home, "wf-audit.ini")
        with patched(wfconfig, _wayfire_files=lambda: [path], _wayfire_read_files=lambda: [path]):
            ts = [threading.Thread(target=wfconfig.wayfire_set, args=("audit", f"k{i}", i)) for i in range(40)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            for i in range(40):
                self.assertEqual(wfconfig.wayfire_get("audit", f"k{i}"), str(i))

    def test_slider_rows_line_up(self):
        """Sliders started wherever their title ended (they filled the row):
        now one width, so both edges line up across rows."""
        a = self.st.slider_row("Size", 50, 0, 100, lambda _v: None)
        b = self.st.slider_row("Key Repeat", 50, 0, 100, lambda _v: None, ends=("Slow", "Fast"))
        for r in (a, b):
            self.assertEqual(r.slider.get_size_request()[0], self.st.SLIDER_W)
            self.assertFalse(r.slider.compute_expand(Gtk.Orientation.HORIZONTAL))
        self.assertFalse(b.slider.get_parent().compute_expand(Gtk.Orientation.HORIZONTAL))

    def test_empty_groups_hidden(self):
        """Sound showed an empty "Output" heading without devices, Bluetooth
        an empty "My Devices" without an adapter, Date & Time a blank gap."""
        S = self.st.system
        with patched(S, volume=lambda: None, bluetooth_state=lambda: None, ntp=lambda: None,
                     timezone=lambda: "UTC", timezones=lambda: []):
            w = self.window()
            for sid in ("sound", "bluetooth", "datetime"):
                w.select(sid)
            settle(500)
            groups = {g.get_title(): g for sid in ("sound", "bluetooth")
                      for g in rows_of(w.pages[sid], Adw.PreferencesGroup)}
            self.assertFalse(groups["Output"].get_visible())
            self.assertFalse(groups["My Devices"].get_visible())
            first = rows_of(w.pages["datetime"], Adw.PreferencesGroup)[0]
            self.assertFalse(first.get_visible())
            w.destroy()

    def test_bluetooth_does_not_claim_discoverable(self):
        """The Bluetooth switch said "discoverable while Settings is open";
        nothing made it so."""
        src = pathlib.Path(self.st.__file__).read_text()
        self.assertNotIn("discoverable while", src)

    def test_slow_reads_off_the_main_loop(self):
        """General (xdg-settings default browser) and Sharing (hostnamectl)
        blocked the window while building; title bars were written on the
        main loop too."""
        import threading
        S = self.st.system
        seen = []

        def browser():
            seen.append(threading.current_thread() is threading.main_thread())
            return ""

        def name():
            seen.append(threading.current_thread() is threading.main_thread())
            return "demo"
        with patched(S, default_browser=browser, computer_name=name):
            w = self.window()
            w.select("appearance")
            w.select("sharing")
            settle(400)
            w.destroy()
        self.assertTrue(seen)
        self.assertNotIn(True, seen)
        src = pathlib.Path(self.st.__file__).read_text()
        self.assertNotIn('fromlist=["apply"]).apply(on)', src)

    def test_display_scale_shows_nearest(self):
        """A 1.75 scale showed "100 %" (not in the list)."""
        opts = [(1.0, "100 %"), (1.25, "125 %"), (1.5, "150 %"), (2.0, "200 %")]
        self.assertEqual(self.st.nearest(opts, 1.8), 2.0)
        self.assertEqual(self.st.nearest(opts, 1.3), 1.25)

    def test_about_values_and_wallpaper_frame(self):
        """About's values were caption-size (tiny next to their titles); an
        empty wallpaper preview was an invisible 180 px gap."""
        src = pathlib.Path(self.st.__file__).read_text()
        self.assertIn("background", rule(src, ".st-wall"))
        self.assertIn('css_classes=["st-value"]', src)

    def test_clear_recent_items_is_a_push_button(self):
        """"Clear Recent Items" was a whole row with a broom glyph (Adwaita
        look); macOS uses a push button."""
        w = self.window("privacy")
        w.select("privacy")
        settle(100)
        labels = [b.get_label() for b in rows_of(w.pages["privacy"], Gtk.Button) if "sonata-button" in b.get_css_classes()]
        self.assertIn("Clear", labels)
        w.destroy()

if __name__ == "__main__":
    unittest.main()
