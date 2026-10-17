"""Vini felt Sonata got slower. What was found, kept from coming back:

- every sound stream's events (sink-input) read the volume again, many a second;
- no DDC/CI display found was asked again on every Control Center opening
  (`ddcutil detect`: seconds);
- quit-on-close listed every window on every title change for an app whose
  process it never found;
- an app_id matched read and parsed its .desktop file again each time
  (~60 us, many times per window event).

    python3 -m unittest tests.test_perf_regressions
"""
import unittest
from unittest import mock

from sonata2.backend import system as S
from sonata2.shell import quitonclose as Q


class AudioEventsTest(unittest.TestCase):
    def test_only_devices(self):
        self.assertTrue(S._AUDIO_EVENT.search("Event 'change' on sink #56"))
        self.assertTrue(S._AUDIO_EVENT.search("Event 'new' on source #3"))
        self.assertFalse(S._AUDIO_EVENT.search("Event 'change' on sink-input #812"))
        self.assertFalse(S._AUDIO_EVENT.search("Event 'change' on source-output #9"))


class DdcDetectTest(unittest.TestCase):
    def test_none_found_is_kept_too(self):
        with mock.patch.dict(S._detected, {"at": 0.0, "displays": []}), \
                mock.patch.object(S, "_run", return_value=(0, "Invalid display\n")) as run:
            self.assertEqual(S.ddc_displays(), [])
            self.assertEqual(S.ddc_displays(), [])
        run.assert_called_once()


class QuitOnCloseRetryTest(unittest.TestCase):
    def test_unknown_process_asked_again_only_now_and_then(self):
        class Mgr:
            toplevels, listeners = [], []
        m = Mgr()
        m.toplevels = [type("T", (), {"app_id": "flatpak.app"})()]
        clock = [100.0]
        views = mock.Mock(return_value=[])
        with mock.patch.object(Q.QuitOnClose, "key_of", staticmethod(lambda a: a)), \
                mock.patch.object(Q.quitapps, "app_pids", return_value=set()):
            q = Q.QuitOnClose(m, views=views, enabled=lambda: True, later=lambda ms, fn: None,
                              now=lambda: clock[0])
            clock[0] += Q.RETRY_S                                 # (its first moments: asked freely)
            for _ in range(20):                                   # title changes
                q.changed()
            self.assertEqual(views.call_count, 2)
            clock[0] += Q.RETRY_S
            q.changed()
            self.assertEqual(views.call_count, 3)


class MatchAppIdTest(unittest.TestCase):
    def test_a_match_is_kept_a_while(self):
        from sonata2 import apps
        apps.refresh()
        with mock.patch.object(apps, "_match", return_value="org.gnome.Calculator") as match:
            for _ in range(50):
                self.assertEqual(apps.match_app_id("gnome-calculator"), "org.gnome.Calculator")
            match.assert_called_once()
            with mock.patch.object(apps.time, "monotonic", return_value=apps.time.monotonic() + apps.HIT_KEEP_S + 1):
                apps.match_app_id("gnome-calculator")
            self.assertEqual(match.call_count, 2)                         # asked again after a while
            apps.refresh()                                                # apps installed / removed
            apps.match_app_id("gnome-calculator")
            self.assertEqual(match.call_count, 3)
        apps.refresh()


class MixerLearnTest(unittest.TestCase):
    def test_learns_seldom_and_never_a_new_streams_start(self):
        """A playing app's 'change' events read every stream (pactl) each
        LEARN_MS; a new stream's own start level stays unlearned (FRESH_S)."""
        from sonata2.backend import mixer as M
        self.assertGreaterEqual(M.LEARN_MS, 5000)
        self.assertGreater(M.FRESH_S * 1000, M.LEARN_MS)


class DownloadsStackTest(unittest.TestCase):
    def test_a_download_being_written_is_not_listed_again(self):
        """The Dock's Downloads stack listed the whole folder (a stat per
        file) every second while a download was being written."""
        import os
        import tempfile
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gio, GLib
        from sonata2.shell import dock_stack as D
        folder = tempfile.mkdtemp()
        mon = Gio.File.new_for_path(folder).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        seen, kept = [], []
        mon.connect("changed", lambda _m, _f, _o, ev: (seen.append(ev), D.stack_event(ev) and kept.append(ev)))

        def spin(ms=300):
            ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + ms * 1000
            while GLib.get_monotonic_time() < end:
                ctx.iteration(False)
        with open(os.path.join(folder, "video.mp4"), "wb") as f:
            spin()
            for _ in range(20):
                f.write(b"x" * 4096)
                f.flush()
                spin(20)
        spin()
        writes = seen.count(Gio.FileMonitorEvent.CHANGED)
        self.assertGreaterEqual(writes, 1)       # (the download's writes: Gio sends one every ~0.8 s)
        self.assertNotIn(Gio.FileMonitorEvent.CHANGED, kept)
        self.assertIn(Gio.FileMonitorEvent.CREATED, kept)                 # it came
        self.assertIn(Gio.FileMonitorEvent.CHANGES_DONE_HINT, kept)       # and is done
        mon.cancel()


class DockJsonTest(unittest.TestCase):
    def test_only_the_glass_switch_restyles(self):
        """Every dock.json write (an icon pinned, the Dock resized) re-read the
        appearance in every Sonata process; only its old "glass" key is theirs."""
        from sonata2.ui import theme as T
        cfg = {"glass": True, "pinned": ["a"]}
        with mock.patch.object(T, "_dock_glass", side_effect=lambda: cfg.get("glass")), \
                mock.patch.object(T, "_appearance_changed") as restyle, \
                mock.patch.object(T, "_dock_glass_seen", True):
            for n in range(5):
                cfg["pinned"].append(str(n))
                T._dock_changed()
            restyle.assert_not_called()
            cfg["glass"] = False
            T._dock_changed()
            restyle.assert_called_once()


class LaunchpadOpensFastTest(unittest.TestCase):
    """Vini: open the Apps menu faster. `sonata2 launchpad` (Super, F4, the
    Dock's icon) started a whole Sonata process -- GTK, its UI -- only to
    tell the resident Launchpad: ~0.5 s before it began to open."""

    def test_cli_tells_the_resident_launchpad_first(self):
        import sys
        import sonata2.__main__ as M
        with mock.patch.object(sys, "argv", ["sonata2", "launchpad"]), \
                mock.patch.object(M, "tell_launchpad", return_value=True) as tell, \
                mock.patch.object(M, "run", create=True) as heavy:
            self.assertEqual(M.main(), 0)
        tell.assert_called_once_with("toggle")
        heavy.assert_not_called()
        src = open(M.__file__).read()
        self.assertIn('toggle = _Gio.SimpleAction.new("toggle", None)', src)

    def test_dock_tells_it_over_dbus(self):
        import types
        from sonata2.shell import dock as D
        tile = types.SimpleNamespace(key="sonata2-launchpad", info=mock.Mock())
        fake = types.SimpleNamespace(_starting={})
        with mock.patch.object(D, "launchpad_action") as act:
            D.Dock.launch(fake, tile)
        self.assertEqual(act.call_args[0][0], "toggle")
        tile.info.launch.assert_not_called()                              # no process
        failed = act.call_args[1]["failed"]                               # not running: launched
        seen = []
        fake.launch = lambda t: seen.append(getattr(t, "spawn", False))
        failed()
        self.assertEqual(seen, [True])                                    # the usual way, this once
        self.assertFalse(tile.spawn)


class ControlCenterWarmTest(unittest.TestCase):
    def test_built_once_unseen_after_login(self):
        """Its first opening took ~2.5x the others: built once, unseen, and
        kept by the menu bar (the login warm-up asking again builds nothing)."""
        import types
        from sonata2.shell import topbar as T
        bar = types.SimpleNamespace()
        with mock.patch.object(T, "ControlCenter") as cc:
            T.prewarm_control_center(bar)
            T.prewarm_control_center(bar)
        cc.assert_called_once_with(bar)
        self.assertIs(bar.control_center, cc.return_value)
        with mock.patch.object(T, "ControlCenter", side_effect=RuntimeError("x")):
            T.prewarm_control_center(types.SimpleNamespace())            # never in the way
        self.assertIn("prewarm_control_center(self.bar)", open(T.__file__).read())
        import sonata2.__main__ as M
        self.assertNotIn("ControlCenter(win.bar)", open(M.__file__).read())


@unittest.skipUnless(__import__("os").environ.get("DISPLAY"), "needs a display (xvfb-run)")
class ControlCenterKeptTest(unittest.TestCase):
    """Product-wide check: every Control Center opening built it again, in a
    new panel (~35 ms of Python, ~125 ms to its first frame). It is kept, in
    its panel, and only brought up to date as it opens (~25 ms)."""

    def setUp(self):
        import tempfile
        import types
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
        from sonata2 import config, ui
        from sonata2.shell import topbar as T
        Adw.init()
        ui.setup()
        self.T, self.Gtk = T, Gtk
        for p in (mock.patch.object(T.system, "run_async"), mock.patch.object(T, "cached"),
                  mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()),
                  mock.patch.dict(T._LEVELS, clear=True)):
            p.start()
            self.addCleanup(p.stop)
        self.nc = types.SimpleNamespace(dnd=False, set_dnd=lambda on: None)
        self.bar = types.SimpleNamespace(
            _poll=lambda: None, _set_volume=lambda v: None, notifications=self.nc, monitor=None,
            get_native=lambda: None, wifi_column=lambda close: Gtk.Label(label="Networks"),
            bluetooth_column=lambda close: Gtk.Label(label="Devices"))
        self.win = Gtk.Window()
        self.btn = Gtk.Button(label="cc")
        self.win.set_child(self.btn)
        self.win.present()
        self.addCleanup(self.win.destroy)
        _spin(150)

    def open(self):
        pop = self.T.Bar._control_center(self.bar, self.btn)
        _spin(120)
        return pop

    def close(self, pop):
        pop.popdown()
        _spin(120)

    def test_not_built_again(self):
        real = self.T.ControlCenter
        with mock.patch.object(self.T, "ControlCenter", side_effect=real) as made:
            pops = []
            for _ in range(4):
                pops.append(self.open())
                self.close(pops[-1])
        self.assertEqual(made.call_count, 1)
        self.assertEqual(len({id(p) for p in pops}), 1)                  # the same panel
        self.assertIs(pops[0].get_parent(), self.btn)                     # kept, not dropped
        # its opening animation plays each time (CSS plays one name once)
        classes = []
        for _ in range(2):
            classes.append(self.open().has_css_class("reopen"))
            self.close(pops[0])
        self.assertEqual(sorted(classes), [False, True])

    def test_opens_with_current_states(self):
        from sonata2.shell import controlcenter as C
        from sonata2 import config
        config.save("controlcenter", {"modules": list(C.DEFAULT_ORDER)})
        pop = self.open()
        cc = self.bar.control_center
        cc.show_details("wifi")                                           # left open...
        cc.grid.set_editing(True)
        self.close(pop)
        self.nc.dnd = True                                                # ...states changed elsewhere
        self.T._LEVELS.update(vol=17, mic=83)
        self.open()
        self.assertIs(self.bar.control_center, cc)
        self.assertEqual(cc.pages.get_visible_child_name(), "main")
        self.assertIsNone(cc.pages.get_child_by_name("details"))
        self.assertFalse(cc.grid.editing)
        self.assertTrue(cc.dnd.button.has_css_class("on"))
        self.assertEqual(round(cc.vol.get_value()), 17)
        self.assertEqual(round(cc.mic.get_value()), 83)

    def test_a_layout_changed_elsewhere_builds_a_new_one(self):
        from sonata2.shell import controlcenter as C
        from sonata2 import config
        config.save("controlcenter", {"modules": list(C.DEFAULT_ORDER)})
        self.close(self.open())
        first = self.bar.control_center
        config.save("controlcenter", {"modules": ["dnd", "sound"]})        # (Settings)
        pop = self.open()
        self.assertIsNot(self.bar.control_center, first)
        self.assertEqual(self.bar.control_center.grid.order, ["dnd", "sound"])
        self.assertIs(self.bar.control_center.get_ancestor(self.Gtk.Popover), pop)
        self.close(pop)

    def test_menu_bar_item_closes_once_per_opening(self):
        """Bar._open's "closed" handler doesn't pile up on the kept panel."""
        import types
        closed = []
        bar = types.SimpleNamespace(reveal=None, _refresh_icon=lambda b: closed.append(1),
                                    logo=None, _icons_soon=lambda: None)
        for _ in range(3):
            self.T.Bar._open(bar, self.btn, lambda b: self.open())
            self.close(self.bar._cc_panel)
        self.assertEqual(len(closed), 3)


class MenuBarIconsTest(unittest.TestCase):
    """The menu bar's icons were drawn anew on every window change, a
    browser's title changes included (many a second)."""

    def test_only_when_the_front_app_or_the_windows_change(self):
        import types
        from sonata2.shell import topbar as T
        win = lambda app, on=False: types.SimpleNamespace(app_id=app, activated=on, title="")   # noqa: E731
        chrome, files = win("chrome", True), win("files")
        bar = types.SimpleNamespace(manager=types.SimpleNamespace(toplevels=[chrome, files]), _icons_soon=mock.Mock())
        redraw = lambda: T.Bar._icons_if_changed(bar)                                         # noqa: E731
        redraw()
        for n in range(20):
            chrome.title = f"({n}) Inbox"                                 # titles only
            redraw()
        self.assertEqual(bar._icons_soon.call_count, 1)
        chrome.activated, files.activated = False, True                   # another app in front
        redraw()
        bar.manager.toplevels.append(win("term"))                         # a window opened
        redraw()
        bar.manager.toplevels.pop()                                       # and closed (Vini: icons went blank)
        redraw()
        self.assertEqual(bar._icons_soon.call_count, 4)


def _spin(ms):
    from gi.repository import GLib
    ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        ctx.iteration(False)


class StallWatchIdleTest(unittest.TestCase):
    """Product-wide check (Oct 2026): every Sonata process -- Dock, menu bar,
    wallpaper, Launchpad, Spotlight, each app -- woke up once a second all
    session long only to stat() the flag of a diagnostics tool (60 of the
    idle Dock's 60 wakeups a minute)."""

    def test_no_thread_until_the_flag_comes(self):
        import os
        import tempfile
        import threading
        from sonata2 import stallwatch as SW
        alive = lambda: [t for t in threading.enumerate() if t.name == "stallwatch" and t.is_alive()]   # noqa: E731
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tempfile.mkdtemp()}), \
                mock.patch.object(SW, "_started", []), mock.patch.object(SW, "_probe"):
            SW.start("test")
            _spin(200)
            self.assertEqual(alive(), [])                                 # off: no thread, no timer
            open(SW.flag_path(), "w").close()                             # tools/closelag.sh
            for _ in range(30):
                _spin(100)
                if alive():
                    break
            self.assertEqual(len(alive()), 1)
            os.unlink(SW.flag_path())
            for _ in range(30):
                _spin(100)
                if not alive():
                    break
            self.assertEqual(alive(), [])                                 # off again: gone
        src = open(SW.__file__).read()
        self.assertNotIn("time.sleep(1)", src)


class MenuBarNoNmcliPollTest(unittest.TestCase):
    """Product-wide check: the menu bar ran nmcli every 10 s, all day (up to
    three runs a tick with Wi-Fi on), and woke a thread for the battery. With
    NetworkManager and UPower on the bus their signals say when to look."""

    def make(self, net=True, upower=True):
        from sonata2.shell import topbar as T
        self.T = T
        self.reads, self.timers, self.later = [], [], []
        patches = [mock.patch.object(T, "_BARS", []),
                   mock.patch.object(T.GLib, "timeout_add_seconds", side_effect=lambda s, fn: self.timers.append(s)),
                   mock.patch.object(T.GLib, "timeout_add",
                                     side_effect=lambda ms, fn: self.later.append((ms, fn)) or 7),
                   mock.patch.object(T.system, "run_async",
                                     side_effect=lambda fn, cb=None, *a: (self.reads.append(fn), cb and cb(None))),
                   mock.patch.object(T.system, "watch_network", return_value=(lambda: None) if net else None),
                   mock.patch.object(T.system, "system_service", return_value=upower)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        poll = T._StatusPoll()
        self.addCleanup(poll._gate.cancel)
        return poll

    def wifi_reads(self):
        return sum(1 for fn in self.reads if fn is self.T.system.wifi_status)

    def test_signals_not_a_poll(self):
        p = self.make()
        self.assertEqual(self.timers, [self.T.PUSHED_POLL_S])             # a look every 2 min, not 10 s
        p._tick()
        self.assertEqual(self.wifi_reads(), 0)
        self.assertEqual(len(self.reads), 0)                              # battery: UPower's signals
        for _ in range(30):                                               # a scan: a signal per network
            p._net_changed()
        self.assertEqual(len(self.later), 1)                              # one read for the burst
        self.assertGreaterEqual(self.later[0][0], self.T.NET_SETTLE_MS)
        self.later[0][1]()
        self.assertEqual(self.wifi_reads(), 1)
        p._looked -= self.T.PUSHED_POLL_S                                 # now and then: everything
        p._tick()
        self.assertEqual(self.wifi_reads(), 2)

    def test_without_network_manager_it_polls_as_before(self):
        p = self.make(net=False, upower=False)
        self.assertEqual(self.timers, [self.T.POLL_S])
        p._tick()
        p._tick()
        self.assertEqual(self.wifi_reads(), 2)
        self.assertEqual(self.timers[-1], self.T.POLL_S)


@unittest.skipUnless(__import__("os").environ.get("DISPLAY"), "needs a display (xvfb-run)")
class WindowSizeNotEveryFrameTest(unittest.TestCase):
    """Product-wide check: Disk Utility ran ~58 Python callbacks a second
    while idle -- every app window looked at its size on every frame its
    surface was laid out, and a turning spinner lays it out every frame."""

    def test_an_animation_is_not_a_resize(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from sonata2.ui import window as W
        calls = []
        real = W.follow_size
        with mock.patch.object(W, "follow_size", side_effect=lambda w: calls.append(1) or real(w)), \
                mock.patch("sonata2.winsize.saved", return_value=None), mock.patch("sonata2.winsize.save"):
            win = Gtk.Window()
            W.remember_size(win, "perf-test", 300, 200)
            win.set_child(Gtk.Spinner(spinning=True))
            win.present()
            _spin(600)
            before = len(calls)
            _spin(1500)                                                   # ~90 frames of the spinner
            self.assertLessEqual(len(calls) - before, 2)
            win.set_default_size(420, 260)                                # a real resize still counts
            _spin(600)
            self.assertGreater(len(calls), before)
            win.destroy()


class SameCssNotParsedAgainTest(unittest.TestCase):
    """Product-wide check: Files took ~10.5 s to list a folder of 5,000
    files -- each cell made (tags.dots_box) registered the tags' CSS again,
    and every register parsed Sonata's whole stylesheet again (~2 ms each)."""

    def test_registering_the_same_css_twice_loads_once(self):
        from sonata2.ui import theme as T
        with mock.patch.object(T, "_provider", object()), mock.patch.object(T, "_load") as load, \
                mock.patch.dict(T._templates):
            for _ in range(50):
                T.register(".perf-x { color: %(accent)s; }", key="perf-x")
            self.assertEqual(load.call_count, 1)
            T.register(".perf-x { color: %(label)s; }", key="perf-x")     # a real change still applies
            self.assertEqual(load.call_count, 2)
            T.register(".perf-x { color: %(label)s; }", key="perf-x", size=3)
            self.assertEqual(load.call_count, 3)

    def test_files_cells_register_nothing_new(self):
        from sonata2.files import tags
        from sonata2.ui import theme as T
        tags._css()
        with mock.patch.object(T, "_provider", object()), mock.patch.object(T, "_load") as load:
            for _ in range(100):
                tags.dots_box(reserve=True)
        load.assert_not_called()


@unittest.skipUnless(__import__("os").environ.get("DISPLAY"), "needs a display (xvfb-run)")
class DisplaysWallpaperPreviewTest(unittest.TestCase):
    """Product-wide check: Settings > Displays took ~230 ms to open -- ~160
    of them decoding the 4K wallpaper on the main thread for a 180 px
    preview, which then held ~40 MB."""

    def test_decoded_in_a_thread_at_the_previews_size(self):
        import os
        import tempfile
        os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
        Adw.init()
        from sonata2 import ui
        from sonata2.settings import app as S
        ui.setup()
        with mock.patch.object(Gtk.Picture, "set_file", side_effect=AssertionError("decoded on the main loop")):
            w = S.Settings(None, "displays")
        try:
            w.present()

            def walk(x):
                yield x
                c = x.get_first_child()
                while c is not None:
                    yield from walk(c)
                    c = c.get_next_sibling()
            pic = next(p for p in walk(w.pages["displays"])
                       if isinstance(p, Gtk.Picture) and p.has_css_class("st-wall"))
            for _ in range(40):
                _spin(100)
                if pic.get_paintable() is not None:
                    break
            tex = pic.get_paintable()
            self.assertIsNotNone(tex)                                     # (the default wallpaper)
            self.assertLessEqual(max(tex.get_width(), tex.get_height()), S.WALL_PREVIEW_PX)
        finally:
            w.destroy()


@unittest.skipUnless(__import__("os").environ.get("DISPLAY"), "needs a display (xvfb-run)")
class FilesBigFolderTest(unittest.TestCase):
    """Product-wide check: Files took ~2.5 s to list 5,000 files -- the
    icons grid made 2,049 cells (GTK keeps max_columns x 32 of them; it was
    64), and the two views not shown made, sorted and filled theirs too.
    A ".png" that isn't one was asked for again on every listing."""

    N = 600

    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from sonata2 import ui
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.perfbigfolder")
        cls.app.register(None)

    def setUp(self):
        import os
        import tempfile
        from sonata2 import config
        from sonata2.files import thumbs
        self.d = tempfile.mkdtemp()
        self.big = os.path.join(self.d, "big")
        os.mkdir(self.big)
        for i in range(self.N):
            with open(os.path.join(self.big, f"f{i:04}.png" if i % 2 else f"f{i:04}.txt"), "w") as f:
                f.write("x")                                       # (no picture in the ".png"s)
        for p in (mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()),
                  mock.patch.object(thumbs, "CACHE_DIR", os.path.join(self.d, "large")),
                  mock.patch.object(thumbs, "FAIL_DIR", os.path.join(self.d, "fail")),
                  mock.patch.dict(thumbs._none, clear=True), mock.patch.dict(thumbs._mem, clear=True)):
            p.start()
            self.addCleanup(p.stop)

    def window(self):
        from gi.repository import Gio
        from sonata2.files.window import FilesWindow
        w = FilesWindow(self.app, Gio.File.new_for_path(self.d).get_uri())
        w.set_default_size(1000, 700)
        w.present()
        self.addCleanup(w.destroy)
        _spin(400)
        return w

    def go_big(self, w):
        from gi.repository import Gio
        w.go(Gio.File.new_for_path(self.big).get_uri(), fade=False)
        for _ in range(50):
            _spin(100)
            if w.tab.folder.store.get_n_items() == self.N:
                break
        _spin(300)

    def test_cells_only_for_the_view_shown(self):
        from sonata2.files import views as V
        made = {}

        def count(name, real):
            def f(*a):
                made[name] = made.get(name, 0) + 1
                return real(*a)
            return f
        with mock.patch.object(V.IconsView, "_setup", count("icons", V.IconsView._setup)), \
                mock.patch.object(V.ListView, "_setup_name", count("list", V.ListView._setup_name)), \
                mock.patch.object(V._Column, "_setup", count("columns", V._Column._setup)):
            w = self.window()
            w.set_view("icons", save=False)
            made.clear()
            self.go_big(w)
            grid = w.tab.views["icons"].widget
            self.assertLess(made.get("icons", 0), 2049 // 2)               # not 64 x 32 + 1
            self.assertEqual(made.get("list", 0), 0)                         # hidden: no cells
            self.assertEqual(made.get("columns", 0), 0)
            # as many columns as before: the limit is never what stops them
            xs = {round(c.get_parent().compute_point(grid, __import__("gi").repository.Graphene.Point().init(0, 0))[1].x)
                  for c in w.tab.views["icons"]._cells.values() if c.get_parent() is not None and c.get_mapped()}
            self.assertGreater(len(xs), 1)
            self.assertGreater(grid.get_max_columns(), len(xs))
            # the list, once shown: every item, nothing selected, at the top
            w.set_view("list", save=False)
            _spin(300)
            lv = w.tab.views["list"]
            self.assertEqual(lv.model.get_n_items(), self.N)
            self.assertGreater(made.get("list", 0), 0)
            self.assertEqual(lv.selected(), [])
            self.assertEqual(w.tab.views["icons"].model.get_n_items(), self.N)   # the one left: kept
            w.go(__import__("gi").repository.Gio.File.new_for_path(self.d).get_uri(), fade=False)
            _spin(300)                                   # (the default view there: icons, given back)
            w.set_view("icons", save=False)
            _spin(200)
            self.assertEqual(w.tab.views["icons"].model.get_n_items(), w.tab.filtered.get_n_items())

    def test_a_file_without_a_picture_is_asked_once(self):
        from sonata2.files import thumbs
        real = thumbs._pool.submit
        with mock.patch.object(thumbs._pool, "submit", side_effect=real) as sub:
            w = self.window()
            w.set_view("icons", save=False)
            self.go_big(w)
            _spin(1500)                                                      # (the threads answer)
            first = sub.call_count
            self.assertGreater(first, 0)
            from gi.repository import Gio
            w.go(Gio.File.new_for_path(self.d).get_uri(), fade=False)
            _spin(400)
            self.go_big(w)
            _spin(500)
        self.assertEqual(sub.call_count, first)                             # none asked again
        self.assertEqual(len(thumbs._mem), 0)                               # nor kept with the pictures


@unittest.skipUnless(__import__("os").environ.get("DISPLAY"), "needs a display (xvfb-run)")
class SettingsFirstVisitImportsTest(unittest.TestCase):
    """Product-wide check: Settings > Displays imported Files' packages module
    (tarfile, zipfile, Files' UI: ~36 ms) only to name the package manager;
    Menu Bar and Date & Time imported the whole menu bar for its settings."""

    def test_pages_import_neither(self):
        import os
        import subprocess
        import sys
        import tempfile
        code = (
            "import sys, gi\n"
            "gi.require_version('Gtk', '4.0'); gi.require_version('Adw', '1')\n"
            "from gi.repository import Adw\n"
            "Adw.init()\n"
            "from sonata2 import ui\n"
            "ui.setup()\n"
            "from sonata2.settings import app as S\n"
            "w = S.Settings(None, 'displays')\n"
            "for sid in ('displays', 'menubar', 'datetime', 'keyboard'):\n"
            "    w.select(sid)\n"
            "print(sorted(m for m in ('sonata2.files.packages', 'sonata2.shell.topbar') if m in sys.modules))\n")
        env = dict(os.environ, XDG_CONFIG_HOME=tempfile.mkdtemp(), XDG_CACHE_HOME=tempfile.mkdtemp())
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(out.stdout.strip().splitlines()[-1:], ["[]"], out.stderr[-2000:])

    def test_install_command_needs_no_files_module(self):
        from sonata2.backend import system
        with mock.patch.object(system, "package_family", return_value="arch"):
            self.assertEqual(system.install_command("wlsunset"), "sudo pacman -S --needed wlsunset")
        from sonata2.files import packages
        self.assertIs(packages.family, system.package_family)          # one answer for both


class ShortcutDefaultsReadOnceTest(unittest.TestCase):
    """Settings > Keyboard read Sonata's wayfire.ini once per shortcut (29)."""

    def test_one_read_for_all(self):
        import builtins
        from sonata2 import shortcuts as SC
        real = builtins.open
        reads = []

        def counting(path, *a, **k):
            if str(path).endswith("wayfire.ini"):
                reads.append(path)
            return real(path, *a, **k)
        with mock.patch.dict(SC._parsed, clear=True), mock.patch("builtins.open", counting):
            values = [SC.default(s) for s in SC.SHORTCUTS]
        self.assertEqual(len(reads), 1)
        self.assertTrue(all(values))


class EnvReportOffTheMainLoopTest(unittest.TestCase):
    """Product-wide check: the menu bar's env report ran ten `tool --version`
    on its main loop right after it started (~60-220 ms frozen as it slid in)."""

    def test_in_a_thread_with_the_logo_read_before(self):
        import os
        import tempfile
        import sonata2.__main__ as M
        src = open(M.__file__).read()
        self.assertIn("threading.Thread(target=_write_env_report, args=(_logo_line(),)", src)
        self.assertNotIn("GLib.idle_add(lambda: (_write_env_report(), False)[1])", src)
        home = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"HOME": home}), \
                mock.patch("sonata2.icons.distro_logo", side_effect=AssertionError("GTK off the main thread")), \
                mock.patch("subprocess.run", side_effect=OSError):
            M._write_env_report("logo /x.svg")                            # (as the thread runs it)
        with open(os.path.join(home, ".cache", "sonata2", "env.txt")) as f:
            self.assertIn("logo /x.svg", f.read())


if __name__ == "__main__":
    unittest.main()
