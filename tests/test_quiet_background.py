"""Sonata's background work steps back (Vini: games must keep their full
performance; the menu bar process should cost next to nothing at idle):
the shared pause gate (quiet.py), one status poll for every display's bar,
no Wi-Fi rescans, event-driven camera check, one nvidia-smi reading, only
the figures shown, no minute timers, the menu-close hook leak, no libvte
in the menu bar, tray tooltips without re-reading icons.
Run: PYTHONPATH=.:tests xvfb-run -a python3.12 -m unittest test_quiet_background"""
import os
import sys
import tempfile
import types
import unittest
from unittest import mock

os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import gamemode, quiet, ui, vram  # noqa: E402
from sonata2.backend import inuse, stats, system  # noqa: E402
from sonata2.shell import idlelock, nightshift, topbar, tray  # noqa: E402

Adw.init()


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def sync_run_async(fn, callback=None, *args):
    res = fn(*args)
    if callback:
        callback(res)


def set_full(on):
    with open(gamemode.STATE, "w") as f:
        f.write("1" if on else "0")


class GateTest(unittest.TestCase):
    def tearDown(self):
        set_full(False)
        idlelock.mark_locked(False)

    def test_paused_when_locked_or_fullscreen_and_watch_tells(self):
        set_full(False)
        idlelock.mark_locked(False)
        seen = []
        h = quiet.watch(seen.append)
        self.assertFalse(quiet.paused())
        idlelock.mark_locked(True)                 # this process' pid: a live lock screen
        settle(300)
        self.assertTrue(quiet.paused() and quiet.locked())
        idlelock.mark_locked(False)
        settle(300)
        set_full(True)
        settle(300)
        self.assertTrue(quiet.paused())
        self.assertFalse(quiet.locked())
        set_full(False)
        settle(300)
        h.cancel()
        self.assertEqual(seen, [True, False, True, False])


class FakeBar:
    def __init__(self, sound=False):
        self.cfg = {"show_sound": sound}
        self.got = []
        self._wifi_state = lambda r: self.got.append(("wifi", r))
        self._battery_state = lambda r: self.got.append(("battery", r))
        self._sound_state = lambda r: self.got.append(("sound", r))


class StatusPollTest(unittest.TestCase):
    def poll(self, bars, paused=False, audio=None):
        calls = []
        with mock.patch.object(topbar, "_BARS", bars), \
                mock.patch.object(topbar.GLib, "timeout_add_seconds"), \
                mock.patch.object(quiet, "paused", return_value=paused), \
                mock.patch.object(system, "run_async", side_effect=sync_run_async), \
                mock.patch.object(system, "wifi_status", side_effect=lambda: calls.append("wifi") or (True, True, ("Home", 80, False))), \
                mock.patch.object(system, "battery", side_effect=lambda: calls.append("battery") or (50, "Discharging")), \
                mock.patch.object(system, "on_ac", return_value=False), \
                mock.patch.object(system, "power_profile_fast", return_value="balanced"), \
                mock.patch.object(system, "volume", side_effect=lambda: calls.append("volume") or (40, False)), \
                mock.patch.object(system, "watch_audio", return_value=audio) as wa:
            p = topbar._StatusPoll()
            p.poll()
            p.poll() if audio else None
            p._gate.cancel()
        return calls, wa

    def test_one_read_for_every_bar(self):
        """Two displays: one nmcli/battery read per tick, both bars updated (was one set per bar)."""
        a, b = FakeBar(), FakeBar()
        calls, _wa = self.poll([a, b])
        self.assertEqual(calls, ["wifi", "battery"])                  # no volume: Sound not shown
        for bar in (a, b):
            self.assertIn(("wifi", (True, True, ("Home", 80, False))), bar.got)
            self.assertIn(("sound", None), bar.got)

    def test_nothing_while_paused(self):
        calls, _wa = self.poll([FakeBar(sound=True)], paused=True)
        self.assertEqual(calls, [])

    def test_volume_from_events_when_sound_shown(self):
        audio = mock.Mock()
        calls, wa = self.poll([FakeBar(sound=True)], audio=audio)
        wa.assert_called_once()
        self.assertEqual(calls.count("volume"), 1)                  # second tick: no poll, events only
        calls, _wa = self.poll([FakeBar(sound=True)], audio=None)   # no pactl: polled
        self.assertEqual(calls.count("volume"), 1)

    def test_bar_poll_goes_through_the_shared_poller(self):
        src = open(topbar.__file__).read()
        self.assertNotIn("GLib.timeout_add_seconds(POLL_S, lambda: (self.alive and self._poll()", src)


class WifiTest(unittest.TestCase):
    def test_no_rescan_and_no_list_with_the_radio_off(self):
        runs = []

        def run(cmd, timeout=10):
            runs.append(cmd)
            if cmd[:4] == ["nmcli", "-t", "-f", "TYPE,STATE"]:
                return 0, "wifi:connected\nethernet:unavailable\n"
            if cmd[:3] == ["nmcli", "radio", "wifi"]:
                return 0, "disabled\n" if off else "enabled\n"
            return 0, "yes:Home:77\nno:Other:30\n"
        off = False
        with mock.patch.object(system, "_run", side_effect=run):
            self.assertEqual(system.wifi_status(), (True, True, ("Home", 77, False)))
            lists = [c for c in runs if "wifi" in c and "list" in c]
            self.assertTrue(lists and all(c[-2:] == ["--rescan", "no"] for c in lists))
            self.assertEqual(len(runs), 3)
            runs.clear()
            system.wifi_current()
            self.assertIn(["--rescan", "no"], [c[-2:] for c in runs])
            runs.clear()
            off = True
            self.assertEqual(system.wifi_status(), (True, False, ("", 0, False)))
            self.assertEqual(len(runs), 2)                              # radio off: no network list


class VteProbeTest(unittest.TestCase):
    def test_terminal_probe_does_not_load_vte(self):
        sys.modules.pop("gi.repository.Vte", None)
        with mock.patch.object(system.subprocess, "Popen"), mock.patch.object(system, "_spawn"), \
                mock.patch.object(gi, "require_version"):
            system.run_in_terminal("true")
        self.assertNotIn("gi.repository.Vte", sys.modules)


class CameraTest(unittest.TestCase):
    def test_node_open_and_close_are_seen(self):
        dev = tempfile.mkdtemp()
        open(os.path.join(dev, "video0"), "w").close()
        hits = []
        n = inuse.VideoNodes(lambda: hits.append(1), dev=dev)
        self.assertTrue(n.ok)
        open(os.path.join(dev, "null"), "w").close()               # another node: ignored
        settle(100)
        self.assertEqual(hits, [])
        with open(os.path.join(dev, "video0")):
            pass
        settle(100)
        self.assertTrue(hits)
        hits.clear()
        open(os.path.join(dev, "video1"), "w").close()             # a camera plugged in
        settle(100)
        with open(os.path.join(dev, "video1")):
            pass
        settle(100)
        self.assertTrue(hits)
        n.close()

    def test_no_periodic_scan_and_paused_until_resumed(self):
        with mock.patch.object(inuse.shutil, "which", return_value=None), \
                mock.patch.object(inuse.GLib, "timeout_add_seconds") as tas, \
                mock.patch.object(inuse, "camera_users", return_value=["obs"]) as cu, \
                mock.patch.object(inuse, "_dev_names", return_value=["video0"]), \
                mock.patch.object(system, "run_async", side_effect=sync_run_async), \
                mock.patch.object(quiet, "paused", return_value=False):
            w = inuse.Watcher()
            self.assertTrue(w.nodes.ok)
            self.assertFalse(any(c.args[1] == w._poll_camera for c in tas.call_args_list))   # no 4 s scan
            self.assertEqual(w.state["camera"], ["obs"])            # one look at start
            cu.reset_mock()
            with mock.patch.object(quiet, "paused", return_value=True):
                w._tick_camera()                                     # a node event during a game
            cu.assert_not_called()
            self.assertTrue(w._dirty)
            w._gate.cb(False)                                        # the game left full screen
            cu.assert_called_once()
            self.assertFalse(w._dirty)
        w.nodes.close()
        w._gate.cancel()


class FakeUsage:
    tool, devices = "/usr/bin/nvidia-smi", ["/sys/x"]

    def awake(self):
        return True


class NvidiaTest(unittest.TestCase):
    def test_one_reading_shared_within_a_moment(self):
        vram._nv.update(t=None, tool=None, out="")
        t = {"now": 100.0}
        with mock.patch.object(vram.subprocess, "run",
                               return_value=types.SimpleNamespace(stdout="45, 3072, 8188, 61\n")) as run:
            a = vram.nvidia_read("/x/nvidia-smi", clock=lambda: t["now"])
            t["now"] += 0.5
            b = vram.nvidia_read("/x/nvidia-smi", clock=lambda: t["now"])
            self.assertEqual(run.call_count, 1)
            t["now"] += 1.0
            vram.nvidia_read("/x/nvidia-smi", clock=lambda: t["now"])
            self.assertEqual(run.call_count, 2)
        self.assertEqual(a, b)
        self.assertEqual(vram.parse_usage(vram.usage_rows(a)), (3072, 8188))

    def test_watch_reads_the_shared_query_and_rests_while_locked(self):
        w = vram.VramWatch(usage=FakeUsage())
        with mock.patch.object(vram, "nvidia_read", return_value="10, 1000, 8000, 50\n") as nr:
            self.assertIsNone(w.check())
        nr.assert_called_once()
        with mock.patch.object(quiet, "locked", return_value=True), mock.patch("threading.Thread") as th:
            w._tick()
        th.assert_not_called()
        with mock.patch.object(quiet, "locked", return_value=False), \
                mock.patch.object(gamemode, "active", return_value=True), mock.patch("threading.Thread") as th:
            w._tick()                                # in a game: still read (LightEffects needs it)
        th.assert_called_once()


class StatsKindsTest(unittest.TestCase):
    def test_only_the_shown_figures_are_read(self):
        s = mock.Mock()
        s.ram.return_value = (1, 2)
        s.net.return_value = (0.0, 0.0)
        s.fps.return_value = (None, "off")
        s.gpu.return_value = None
        st = stats.Stats(sampler=s)
        cb = lambda r: None  # noqa: E731
        with mock.patch("gi.repository.GLib.timeout_add", return_value=1), \
                mock.patch.object(system, "run_async"):
            st.subscribe(cb, kinds=["cpu"])
        st.read()
        s.cpu.assert_called()
        for name in ("gpus", "vrams", "temps", "fps", "net", "cpu_temp"):
            getattr(s, name).assert_not_called()
        st._kinds[id(cb)] = None                      # an old caller: everything
        st.read()
        s.gpus.assert_called_with(nvidia=True)
        st.unsubscribe(cb)

    def test_needs_nvidia(self):
        with mock.patch.object(stats, "CARDS", [("amd", "AMD", "card0"), ("nvidia", "NVIDIA", "card1")]), \
                mock.patch.object(stats, "VRAM_KINDS", {"vram_amd": "amd", "vram_nvidia": "nvidia"}), \
                mock.patch.object(stats, "TEMP_KINDS", {"temp_amd": "amd", "temp_nvidia": "nvidia"}):
            self.assertFalse(stats.needs_nvidia(["gpu_amd", "vram_amd", "temp_amd"]))
            self.assertTrue(stats.needs_nvidia(["vram_nvidia"]))
            self.assertTrue(stats.needs_nvidia(["gpu"]))
        with mock.patch.object(stats, "CARDS", [("amd", "AMD", "card0")]):
            self.assertFalse(stats.needs_nvidia(["gpu"]))

    def test_paused_while_locked(self):
        st = stats.Stats(sampler=mock.Mock())
        st.listeners.append(lambda r: None)
        with mock.patch.object(quiet, "locked", return_value=True), mock.patch.object(system, "run_async") as ra:
            self.assertTrue(st._tick())
        ra.assert_not_called()


class MinuteTimersTest(unittest.TestCase):
    def test_idlelock_reads_settings_only_on_change(self):
        il = idlelock.IdleLock.__new__(idlelock.IdleLock)
        il.proc = mock.Mock()
        il.proc.poll.return_value = None
        with mock.patch.object(il, "apply") as ap:
            il._check_alive()
            ap.assert_not_called()                   # swayidle alive: no config read, no PATH search
            il.proc.poll.return_value = 0
            il._check_alive()
            ap.assert_called_once()
        src = open(idlelock.__file__).read()
        self.assertIn("config.watch(displaysleep.NAME, self.apply)", src)
        self.assertNotIn("timeout_add_seconds(60, lambda: (self.apply(), True)[1])", src)

    def test_nightshift_until_tomorrow_ends_on_its_own_timer(self):
        ns = nightshift.NightShift.__new__(nightshift.NightShift)
        with mock.patch("gi.repository.GLib.timeout_add_seconds", return_value=7) as tas:
            ns._expire_at({"manual_until": nightshift.until_tomorrow()})
            tas.assert_called_once()
            self.assertLessEqual(tas.call_args.args[0], 24 * 3600 + 1)
            self.assertEqual(ns._expiry, 7)
            with mock.patch("gi.repository.GLib.source_remove") as rm:
                ns._expire_at({"manual_until": ""})      # turned off: the timer goes
            rm.assert_called_once_with(7)
        ns.proc, ns._waiting = mock.Mock(), False
        ns.proc.poll.return_value = None
        with mock.patch.object(ns, "apply") as ap:
            ns._check_alive()
        ap.assert_not_called()


class MenuHookLeakTest(unittest.TestCase):
    def test_stop_takes_the_menu_close_hook_back(self):
        """Vini: every display unplugged left its menu bar window alive (held by ui.menu.on_closed)."""
        class Host(Gtk.Window):
            _init_autohide = topbar.TopBarWindow._init_autohide
            _apply_autohide = topbar.TopBarWindow._apply_autohide
            _pointer = topbar.TopBarWindow._pointer

        h = Host()
        h.bar = types.SimpleNamespace(cfg={"autohide": False}, on_stop=[])
        before = len(ui.menu.on_closed)
        with mock.patch.object(topbar.layer, "set_exclusive"):
            h._init_autohide()
        self.assertEqual(len(ui.menu.on_closed), before + 1)
        for cb in h.bar.on_stop:                     # what Bar.stop() runs
            cb()
        self.assertEqual(len(ui.menu.on_closed), before)
        self.assertIn("for cb in self.on_stop:", open(topbar.__file__).read())
        h.destroy()


class TrayTooltipTest(unittest.TestCase):
    def item(self):
        it = tray.TrayItem.__new__(tray.TrayItem)
        it.host = mock.Mock()
        it.conn = mock.Mock()
        it.name, it.path, it.key = ":1.5", "/StatusNotifierItem", ":1.5/StatusNotifierItem"
        it.props = {"Id": "app", "IconName": "app-icon", "ToolTip": ("", [], "Old", "")}
        it.ready, it.menu, it._subs, it._refresh_id = True, None, [1], 0
        it._tex = {1: ("name", "app-icon")}
        it._want, it._full = set(), False
        return it

    def test_tooltip_reads_only_the_tooltip_and_keeps_the_icon(self):
        it = self.item()
        it._signal(None, ":1.5", it.path, tray.ITEM_IFACE, "NewToolTip", None)
        it._refresh_now()
        calls = it.conn.call.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[3], "Get")
        self.assertEqual(calls[0].args[4].unpack(), (tray.ITEM_IFACE, "ToolTip"))
        it._apply({"ToolTip": ("", [], "New", "")})
        self.assertEqual(it._tex, {1: ("name", "app-icon")})       # texture kept
        it.host.changed.assert_called_once()
        it.host.changed.reset_mock()
        it._apply({"ToolTip": ("", [], "New", "")})                 # the same again: no redraw
        it.host.changed.assert_not_called()
        it._apply({"IconName": "other"})
        self.assertEqual(it._tex, {})                               # a new icon: drawn again

    def test_unknown_signal_reads_everything(self):
        it = self.item()
        with mock.patch.object(it, "refresh") as rf:
            it._signal(None, ":1.5", it.path, tray.ITEM_IFACE, "XAyatanaNewLabel", None)
            it._refresh_now()
        rf.assert_called_once()


if __name__ == "__main__":
    unittest.main()
