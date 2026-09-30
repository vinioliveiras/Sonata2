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


class ScreenRecordingRegressions(unittest.TestCase):
    """The screen recorder "didn't work well": with two displays wf-recorder
    asked on a terminal which one and quit; 180 Hz recordings were heavy
    and choppy; some players couldn't open them; stopping froze the menu bar."""

    def test_command(self):
        from sonata2.shell import capture as C
        cmd = C.recorder_command("/tmp/x.mp4", None, "HDMI-A-1")
        self.assertEqual(cmd[cmd.index("-o") + 1], "HDMI-A-1")        # one display, never a prompt
        self.assertEqual(cmd[cmd.index("-r") + 1], "60")
        self.assertEqual(cmd[cmd.index("-x") + 1], "yuv420p")
        area = C.recorder_command("/tmp/x.mp4", "10,10 200x100", "HDMI-A-1")
        self.assertIn("-g", area)
        self.assertNotIn("-o", area)                                  # an area needs no display

    def test_stop_does_not_block(self):
        import subprocess
        import time
        from sonata2.shell import capture as C

        class Bar:
            recording = None
            def set_recording(self, on): self.recording = on
        cap = C.Capture(None, Bar())
        cap.recorder = subprocess.Popen(["sh", "-c", "trap '' INT; sleep 3"])   # slow to finish
        cap.rec_path = None
        t0 = time.monotonic()
        cap.stop_recording()
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertIsNone(cap.recorder)
        self.assertFalse(cap.bar.recording)
        settle(3500)

    def test_sound(self):
        import stat
        from sonata2.shell import capture as C
        self.assertTrue(C.recorder_command("/tmp/x.mp4", audio="dev")[-1] == "--audio=dev")
        self.assertFalse(any(a.startswith("--audio") for a in C.recorder_command("/tmp/x.mp4")))
        bin_dir = tempfile.mkdtemp()                   # a pactl that answers like PipeWire's
        fake = os.path.join(bin_dir, "pactl")
        with open(fake, "w") as f:
            f.write('#!/bin/sh\n[ "$1" = get-default-sink ] && echo alsa_output.hdmi || echo alsa_input.mic\n')
        os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
        old = os.environ["PATH"]
        os.environ["PATH"] = bin_dir + os.pathsep + old
        try:
            self.assertEqual(C.audio_device("system"), "alsa_output.hdmi.monitor")    # what the speakers play
            self.assertEqual(C.audio_device("mic"), "alsa_input.mic")
            self.assertIsNone(C.audio_device("none"))
        finally:
            os.environ["PATH"] = old

    def test_menu_bar_shows_the_time(self):
        from sonata2.shell import topbar as T
        from gi.repository import Gtk
        bar_cls = next(c for c in vars(T).values() if isinstance(c, type) and hasattr(c, "_rec_tick"))
        bar = bar_cls.__new__(bar_cls)
        box = Gtk.Box()
        box.append(Gtk.Image())
        box.append(Gtk.Label(label=""))
        bar.rec_stop = Gtk.Button(child=box)
        bar._rec_src = 0
        bar.set_recording(True)
        bar._rec_t0 -= 65 * 1_000_000
        bar._rec_tick()
        self.assertEqual(box.get_last_child().get_label(), "1:05")
        bar.set_recording(False)
        self.assertEqual(bar._rec_src, 0)


class CaptureTargetsTests(unittest.TestCase):
    """Capture / record a display or a window; folders per kind; encoders."""

    def test_window_boxes(self):
        from sonata2.shell import capture as C
        outs = [{"id": 1, "name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
                {"id": 2, "name": "eDP-1", "geometry": {"x": 1920, "y": 0, "width": 1920, "height": 1080}}]
        v = lambda **k: {"role": "toplevel", "mapped": True, "layer": "workspace", "output-id": 1,
                         "geometry": {"x": 100, "y": 50, "width": 800, "height": 600}, **k}
        views = [v(**{"last-focus-timestamp": 1}),
                 v(**{"output-id": 2, "last-focus-timestamp": 5}),               # on the second display
                 v(geometry={"x": 2000, "y": 50, "width": 800, "height": 600}),  # another workspace
                 v(minimized=True), v(role="desktop-environment")]
        self.assertEqual(C.window_boxes(views, outs), ["2020,50 800x600", "100,50 800x600"])

    def test_display_and_window_come_from_a_list(self):
        """Record a display / a window: pick it from a list (was: click on the screen)."""
        from sonata2.shell import capture as C
        outs = [{"id": 1, "name": "HDMI-A-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}},
                {"id": 2, "name": "eDP-1", "geometry": {"x": 1920, "y": 0, "width": 1920, "height": 1080}}]
        views = [{"role": "toplevel", "mapped": True, "layer": "workspace", "output-id": 2, "title": "Notes",
                  "app-id": "x", "geometry": {"x": 10, "y": 20, "width": 300, "height": 200}}]
        ipc = type("I", (), {"call": lambda s, m, d=None: views})()
        old = C.outputs, C._ipc
        C.outputs, C._ipc = (lambda: outs), (lambda: ipc)
        cap = C.Capture(None, None)
        picked, done = [], []
        cap._pick = lambda title, items, action, then: picked.append((title, items, action, then))
        cap._record = lambda geo, cfg, output=None: done.append(("rec", geo, output))
        cap._shoot = lambda geo, cfg, output=None: done.append(("shot", geo, output))
        try:
            cap._run("rec-display", {})
            title, items, action, then = picked[-1]
            self.assertEqual([i["value"] for i in items], ["HDMI-A-1", "eDP-1"])
            self.assertEqual(action, "Record")
            then("eDP-1")
            self.assertEqual(done[-1], ("rec", None, "eDP-1"))
            cap._run("window", {})
            title, items, action, then = picked[-1]
            self.assertEqual((items[0]["name"], items[0]["value"]), ("Notes", "1930,20 300x200"))
            then(items[0]["value"])
            self.assertEqual(done[-1], ("shot", "1930,20 300x200", None))
        finally:
            C.outputs, C._ipc = old

    def test_folders_per_kind(self):
        from sonata2.shell import capture as C
        self.assertEqual(C.DEFAULTS["shots_to"], "pictures")
        self.assertEqual(C.DEFAULTS["movies_to"], "videos")
        other = tempfile.mkdtemp()
        self.assertEqual(C.shots_dir({"shots_to": "other", "shots_dir": other}), other)
        self.assertEqual(C.movies_dir({"movies_to": "other", "movies_dir": os.path.join(other, "new")}),
                         os.path.join(other, "new"))                                # made when missing

    def test_encoder_order_and_fallback(self):
        from sonata2.shell import capture as C
        order = C.encoders()
        self.assertEqual(order[-1], "x264")                       # the CPU one always works, last
        self.assertEqual(C.encoders("x264")[0], "x264")           # the one that worked first
        cmd = C.recorder_command("/tmp/x.mp4", output="eDP-1", encoder="nvenc")
        self.assertIn("h264_nvenc", cmd)
        if cmd[0] == "nice":
            self.assertEqual(cmd[3], "wf-recorder")                  # the game gets the CPU first

        class Bar:
            notifications = None
            def set_recording(self, on): pass
        cap = C.Capture(None, Bar())
        cap.rec_path = os.path.join(tempfile.mkdtemp(), "r.mp4")
        cap._rec = {"geo": None, "output": "eDP-1", "audio": None, "encoders": ["x264"], "encoder": "nvenc"}
        tried = []
        cap._spawn = lambda: (tried.append(cap._rec["encoders"].pop(0)), True)[1]
        dead = type("P", (), {"poll": lambda s: 1})()
        cap.recorder = dead
        cap._check_started(dead)                                  # NVENC quit at once: x264 next
        self.assertEqual(tried, ["x264"])

    def test_recording_control(self):
        from sonata2.shell import capture as C
        stopped = []
        owner = type("O", (), {"stop_recording": lambda s: stopped.append(1)})()
        pill = C.RecordingControl(None, owner)
        pill.start(None, True, "mic")
        self.assertTrue(pill.get_visible())
        self.assertEqual(pill.sound.get_icon_name(), "audio-input-microphone-symbolic")
        pill._t0 -= 75 * 1_000_000
        pill._tick()
        self.assertEqual(pill.time.get_label(), "1:15")
        stop = pill.get_child().get_last_child()
        stop.emit("clicked")
        self.assertEqual(stopped, [1])
        pill.stop()
        self.assertFalse(pill.get_visible())


class ShowAgainRegressions(unittest.TestCase):
    """Settings opened minimized: minimized once (yellow button), then closed
    (it only hides and lingers); GTK re-applied the minimize when shown."""

    def test_hidden_window_is_unminimized_before_present(self):
        from sonata2.ui.window import show_again
        calls = []

        class W:
            visible = False
            def get_visible(self): return self.visible
            def unminimize(self): calls.append("unminimize")
            def present(self): calls.append("present")
        show_again(W())
        self.assertEqual(calls, ["unminimize", "present"])
        calls.clear()
        w = W()
        w.visible = True                    # on screen: nothing to forget, never un-minimize by surprise
        show_again(w)
        self.assertEqual(calls, ["present"])

    def test_lingering_windows_use_it(self):
        src = pathlib.Path(__file__).resolve().parent.parent.joinpath("sonata2", "__main__.py").read_text()
        body = src[src.index("def run_settings"):src.index("def run_files")]
        self.assertIn("show_again(win)", body)
        self.assertIn('show_again(state["calc"])', src)


class FixedWidthRegressions(unittest.TestCase):
    """Notification Center (and the Calendar widget under it) grew wider
    with a long Chrome notification: an ellipsized label still asks for its
    whole text as natural width. Shell surfaces now have one fixed width."""

    def _natural(self, widget):
        return widget.measure(Gtk.Orientation.HORIZONTAL, -1)[:2]

    def test_fixed_width_ignores_long_content(self):
        from gi.repository import Pango
        from sonata2.ui.fixed import FixedWidth
        long = Gtk.Label(label="A very long title " * 40, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.assertGreater(self._natural(long)[1], 1000)          # what used to stretch the panel
        self.assertEqual(self._natural(FixedWidth(long, 344)), (344, 344))

    def test_never_below_the_child_minimum(self):
        """Control Center needed 328 px in its 320 px panel: GTK warned
        ("Trying to measure ... for width of 320, but it needs at least 328")
        and clipped it. The panel takes the child's minimum then, still fixed."""
        from sonata2.ui.fixed import FixedWidth
        wide = Gtk.Box(width_request=328)
        self.assertEqual(self._natural(FixedWidth(wide, 320)), (328, 328))
        self.assertEqual(self._natural(FixedWidth(Gtk.Box(width_request=100), 320)), (320, 320))

    def test_notification_card_width(self):
        from sonata2.shell import notifications as N
        fake = type("F", (), {"_icon": lambda s, img, n: None, "invoke": lambda s, *a: None,
                              "close": lambda s, *a: None})()
        widths = []
        for title, body in (("Hi", "short"), ("Google Chrome — " + "x" * 300, "y " * 400)):
            n = N.Note(1, "chrome", "", title, body, [("open", "Open in a new window " * 5)])
            widths.append(self._natural(N.Notifications.card(fake, n))[1])
        self.assertEqual(widths[0], widths[1])

    def test_shell_surfaces_use_it(self):
        root = pathlib.Path(__file__).resolve().parent.parent / "sonata2"
        for rel, needle in (("shell/notifications.py", "FixedWidth(box, BANNER_W)"),
                            ("shell/notifications.py", "FixedWidth(cal,"),
                            ("shell/spotlight.py", "FixedWidth(panel, WIDTH"),
                            ("shell/clip_picker.py", "FixedWidth(panel, WIDTH)"),
                            ("shell/topbar.py", "width=CC_W"), ("ui/panel.py", "FixedWidth(child, width)")):
            self.assertIn(needle, (root / rel).read_text(), rel)
        topbar = (root / "shell/topbar.py").read_text()
        self.assertEqual(topbar.count("gap=PANEL_GAP)"), 0)       # every menu bar panel has a width


class BufferFailureRegressions(unittest.TestCase):
    """The session crashed to the login screen: NVIDIA refused a window's
    buffer ("gbm_bo_create failed: Invalid argument", "Failed to allocate
    auxilliary buffer! Size 876x448") and Wayfire rendered into nothing.
    sonata-corners allocates first and, when that fails, draws the window's
    children directly (no rounded corners for that frame) instead."""

    def test_allocation_checked_before_rendering(self):
        cpp = (pathlib.Path(__file__).resolve().parent.parent / "wayfire-plugin" / "src" /
               "sonata-corners.cpp").read_text()
        sched = cpp[cpp.index("void schedule_instructions("):cpp.index("void render(const wf::scene::render_instruction_t")]
        self.assertIn("inner_content.allocate(", sched)
        self.assertIn("buffer_reallocation_result_t::FAILED", sched)
        self.assertIn("ch->schedule_instructions(instructions, target, damage)", sched)
        self.assertIn("self->cached_damage |= bbox", sched)       # a new buffer is drawn in full
        self.assertLess(sched.index("FAILED"), sched.index("instructions.push_back"))


class MusicTitleAndSeamRegressions(unittest.TestCase):
    """Music showed the song twice (title bar + LCD), and the seam fix under
    the title bar altered the top of the LCD's text (a 12-row band)."""

    def test_music_title_stays_music(self):
        src = (pathlib.Path(__file__).resolve().parent.parent / "sonata2" / "music" / "window.py").read_text()
        self.assertNotIn('— Music"', src)

    def test_seam_band_clears_toolbar_content(self):
        root = pathlib.Path(__file__).resolve().parent.parent
        cpp = (root / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()
        band = float(re.search(r"const float SEAM_BAND = ([0-9.]+);", cpp).group(1))
        self.assertNotIn("seam + 12.0", cpp)
        css = (root / "sonata2" / "ui" / "window.py").read_text()
        pad_top = int(re.search(r"\.sonata-toolbar \{ min-height: 34px; padding: (\d+)px", css).group(1))
        music = (root / "sonata2" / "music" / "window.py").read_text()
        lcd_margin = int(re.search(r"\.mu-lcd \{ min-height: 40px; margin: (\d+)px", music).group(1))
        self.assertGreaterEqual(pad_top + lcd_margin, band)         # the LCD starts below the band
        self.assertGreater(band, 5)                                # still covers the maximized overlap

    def test_overlap_rows_take_the_title_bar_colour(self):
        """Dark stubs at both ends of that seam, maximized: pixdecor paints
        its button area twice there. Those rows are the title bar's colour
        (glass only; opaque app content stays)."""
        cpp = (pathlib.Path(__file__).resolve().parent.parent / "wayfire-plugin" / "src" /
               "sonata-corners.cpp").read_text()
        self.assertIn("float overlap = square_top > 0.5 ? 5.0 : 1.0;", cpp)
        self.assertRegex(cpp, r"fill\.a > 0\.0 && p\.y >= seam && p\.y < seam \+ overlap")
        self.assertIn("c = fill;", cpp)

    def test_maximized_stubs_below_the_overlap(self):
        """Still dark stubs maximized, 4 rows under the overlap (seam+5..8),
        61 px left / 9 px right: pixdecor's 4 px border shift. They take the
        client's column just below; the diagnostic log that found them (once
        per render instance: it flooded session.log) is gone."""
        cpp = (pathlib.Path(__file__).resolve().parent.parent / "wayfire-plugin" / "src" /
               "sonata-corners.cpp").read_text()
        rows = float(re.search(r"const float STUB_ROWS = ([0-9.]+);", cpp).group(1))
        left = float(re.search(r"const float STUB_LEFT = ([0-9.]+);", cpp).group(1))
        right = float(re.search(r"const float STUB_RIGHT = ([0-9.]+);", cpp).group(1))
        self.assertGreaterEqual(rows, 4)
        self.assertGreaterEqual(left, 61)
        self.assertGreaterEqual(right, 9)
        self.assertLess(left, 100)                                  # the toolbar's controls stay
        self.assertIn("square_top > 0.5 && seam >= 0.0 && p.y >= seam + overlap", cpp)
        self.assertNotIn("sonata-corners: seam ", cpp)


class UnifiedToolbarSeamRegressions(unittest.TestCase):
    """A dark band under the title bar of maximized glass windows (Music):
    pixdecor's title bar reaches 5 px under a maximized window (1 px
    otherwise) and the toolbar's glass was painted there too. Those rows of
    the toolbar are now see-through."""

    def _top_alpha(self, maximized):
        win = Gtk.Window()
        ui.window.standard(win)
        bar = ui.window.glass_toolbar(win, start=(("media-playback-start-symbolic", "Play", lambda: None),))
        win.set_child(bar)
        win.set_default_size(300, 60)
        if maximized:
            win.add_css_class("maximized")
        win.present()
        settle(400)
        w, h = win.get_width(), win.get_height()
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(bar).snapshot(snap, w, h)
        tex = win.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
        dl = Gdk.TextureDownloader.new(tex)
        dl.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = dl.download_bytes()
        data = data.get_data()
        win.destroy()
        return [data[y * stride + 150 * 4 + 3] for y in range(8)]

    def test_rows_under_the_title_bar_are_see_through(self):
        floating, maximized = self._top_alpha(False), self._top_alpha(True)
        self.assertEqual(floating[0], 0)
        self.assertTrue(all(a > 0 for a in floating[1:]))
        self.assertEqual(maximized[:5], [0] * 5)
        self.assertTrue(all(a > 0 for a in maximized[5:]))


class DiscreteGpuRegressions(unittest.TestCase):
    """Steam's "Use Discrete Graphics" came back checked after unchecking it:
    its desktop entry asks for the discrete GPU (PrefersNonDefaultGPU)."""

    def test_unchecking_an_app_that_asks_for_it_sticks(self):
        from sonata2 import gpu

        class Info:
            def get_id(self): return "steam.desktop"
            def has_key(self, k): return k == "PrefersNonDefaultGPU"
            def get_boolean(self, _k): return True
        steam = Info()
        gpu.config.save(gpu.NAME, dict(gpu.DEFAULTS))
        self.assertTrue(gpu.wants_discrete(steam))              # its entry asks for it
        gpu.set_discrete(steam, False)
        self.assertFalse(gpu.wants_discrete(steam))             # the user's choice wins
        gpu.set_discrete(steam, True)
        self.assertTrue(gpu.wants_discrete(steam))
        self.assertNotIn("steam", gpu.config.load(gpu.NAME, gpu.DEFAULTS)["integrated"])


class ThemeFadeFocusRegressions(unittest.TestCase):
    """Turning Translucent glass on/off made Settings jump to the next section:
    the theme cross-fade moved the window content, the focused sidebar row
    lost focus and the list selected the following one."""

    def test_look_change_keeps_the_section(self):
        from sonata2 import config, ui
        from sonata2.settings import app as st
        ui.setup()
        w = st.Settings(None, "dock")
        w.present()
        settle(400)
        w.rows["dock"].grab_focus()
        before = config.load("dock", {"glass": True}).get("glass", True)
        w._save("dock", "glass", not before)
        settle(900)
        self.assertEqual(w.current, "dock")
        self.assertEqual(w.listbox.get_selected_row().sid, "dock")
        self.assertNotIn("menubar", w.pages)
        w.destroy()


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
