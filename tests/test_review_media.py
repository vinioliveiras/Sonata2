"""Review tests for the media area: Task Manager's /proc parsing and pages
data (sonata2/activity),
Camera helpers (sonata2/camera) and game controllers (sonata2/gamepad).
Headless and fast; everything on temp dirs and fakes.
Run: xvfb-run -a python3 -m unittest tests.test_review_media -v"""
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

_TMP = tempfile.mkdtemp()
for _k in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR"):
    os.environ[_k] = os.path.join(_TMP, _k.lower())
    os.makedirs(os.environ[_k], exist_ok=True)
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, GObject  # noqa: E402

from sonata2 import gamemode  # noqa: E402
from sonata2.activity import manage, procfs  # noqa: E402
from sonata2.activity.procfs import CLK_TCK, Sampler  # noqa: E402
from sonata2.gamepad import evdev as E, service as S  # noqa: E402


def _write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


def _stat_line(pid, comm, ppid=1, ticks=0, start=100):
    rest = ["S", ppid, 0, 0, 0, -1, 0, 0, 0, 0, 0, ticks, 0, 0, 0, 20, 0, 1, 0, start, 0, 10]
    return f"{pid} ({comm}) " + " ".join(str(x) for x in rest) + " 0 0 0\n"


def _proc(pid, comm="p", uid=1000, ppid=1, exe="p", user="me"):
    return procfs.Proc(pid=pid, name=comm, comm=comm, cmdline=exe, exe=exe, uid=uid, user=user, ppid=ppid,
                       threads=1, rss=0, ticks=0)


# == Task Manager ==============================================================================
class ProcParsingTest(unittest.TestCase):
    def test_stat_name_with_parens_and_garbage(self):
        """A process name holding ") (" must not shift the numeric fields; junk gives None, not a crash."""
        st = procfs.parse_stat(_stat_line(42, "a) (b", ppid=7, ticks=33, start=500))
        self.assertEqual((st["comm"], st["ppid"], st["ticks"], st["start"]), ("a) (b", 7, 33, 500))
        self.assertIsNone(procfs.parse_stat(""))
        self.assertIsNone(procfs.parse_stat("12 (short) S 1 2"))

    def test_diskstats_guess_skips_partitions_and_virtual_devices(self):
        """Without /sys/block, partitions, loop, zram, dm and md devices must not count the same I/O twice."""
        rows = []
        for name in ("sda", "sda1", "mmcblk0", "mmcblk0p1", "loop3", "zram0", "dm-0", "md127", "nvme0n1",
                     "nvme0n1p2"):
            rows.append(f" 8 0 {name} 1 0 2 0 3 0 4 0 0 5 0")
        disks = procfs.parse_disks("\n".join(rows))
        self.assertEqual(sorted(disks), ["mmcblk0", "nvme0n1", "sda"])
        self.assertEqual(disks["sda"]["read_bytes"], 2 * procfs.SECTOR)
        self.assertEqual(disks["sda"]["io_ms"], 5)
        self.assertEqual(procfs.parse_diskstats("\n".join(rows))["writes"], 9)

    def test_cpuinfo_without_topology(self):
        """ARM / VM cpuinfo (no core id, model in "Hardware"): cores fall back to logical processors."""
        text = "processor\t: 0\nBogoMIPS\t: 50\n\nprocessor\t: 1\nBogoMIPS\t: 50\n\nHardware\t: Some SoC\n"
        info = procfs.parse_cpuinfo(text)
        self.assertEqual((info["model"], info["logical"], info["cores"], info["sockets"]), ("Some SoC", 2, 2, 1))
        self.assertEqual(procfs.parse_cpuinfo("")["model"], "Processor")

    def test_battery_charging_and_charge_counters(self):
        """Charging shows time to full; charge_*/current_* (µAh) batteries work like energy_* ones; no battery: None."""
        root = tempfile.mkdtemp(dir=_TMP)
        self.assertIsNone(procfs.battery_info(root))
        b = os.path.join(root, "class/power_supply/BAT1")
        for n, v in (("type", "Battery"), ("status", "Charging"), ("charge_now", "2000000"),
                     ("charge_full", "4000000"), ("current_now", "-1000000")):
            _write(os.path.join(b, n), v + "\n")
        info = procfs.battery_info(root)
        self.assertEqual(info["percent"], 50)                  # computed: no capacity file
        self.assertEqual(info["seconds"], 7200)                # 2 Ah left to fill at 1 A
        self.assertTrue(info["on_ac"])

    def test_formats_edges(self):
        """Byte and bit formatting at unit edges; negative bytes show a dash."""
        from sonata2.activity import pages
        self.assertEqual(procfs.fmt_bytes(-1), "–")
        self.assertEqual(procfs.fmt_bytes(1023), "1023 bytes")
        self.assertEqual(procfs.fmt_bytes(5 * 1024 ** 5), "5120.00 TB")
        self.assertEqual(pages.fmt_bits(50), "0 Kbps")
        self.assertEqual(pages.fmt_bits(2_500_000), "2.5 Mbps")
        self.assertEqual(pages.fmt_bits(3e9), "3.00 Gbps")
        self.assertEqual(procfs.fmt_duration(None), "Calculating…")
        self.assertEqual(procfs.fmt_duration(3 * 3600 + 5 * 60 + 59), "3:05")

    def test_cpu_time_never_shows_60_seconds(self):
        """CPU time just under a minute boundary must roll over, not print "1:60.00"."""
        self.assertEqual(procfs.fmt_cpu_time(119.996), "2:00.00")
        self.assertEqual(procfs.fmt_cpu_time(59.999), "1:00.00")
        self.assertEqual(procfs.fmt_cpu_time(3599.999), "1:00:00.00")


class SamplerEdgeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(dir=_TMP)
        self.proc, self.sys = os.path.join(self.root, "proc"), os.path.join(self.root, "sys")
        os.makedirs(os.path.join(self.sys, "block"))
        self.now = 1000.0
        _write(os.path.join(self.proc, "stat"), "cpu  1 0 1 10 0 0 0 0\nbtime 1700000000\n")
        _write(os.path.join(self.proc, "uptime"), "5000.0 1.0\n")

    def tearDown(self):
        shutil.rmtree(self.root)

    def net(self, rx, tx):
        _write(os.path.join(self.proc, "net/dev"),
               "h1\nh2\n    lo: 999999 1 0 0 0 0 0 0 999999 1 0 0 0 0 0 0\n"
               f"  eth0: {rx} 10 0 0 0 0 0 0 {tx} 10 0 0 0 0 0 0\n")

    def test_reused_pid_gets_no_cpu_spike(self):
        """A new process reusing a dead one's PID (other start time) must not inherit its CPU ticks."""
        s = Sampler(proc=self.proc, sys=self.sys, clock=lambda: self.now)
        _write(os.path.join(self.proc, "77/stat"), _stat_line(77, "old", ticks=50, start=100))
        s.sample()
        self.now += 1
        _write(os.path.join(self.proc, "77/stat"), _stat_line(77, "new", ticks=10 * CLK_TCK, start=4000))
        snap = s.sample()
        self.assertEqual(snap.procs[77].name, "new")
        self.assertEqual(snap.procs[77].cpu, 0.0)
        self.now += 1
        _write(os.path.join(self.proc, "77/stat"), _stat_line(77, "new", ticks=10 * CLK_TCK + CLK_TCK // 2,
                                                             start=4000))
        self.assertAlmostEqual(s.sample().procs[77].cpu, 50.0, delta=1)

    def test_network_counter_reset_and_loopback(self):
        """An interface whose counters restart never shows negative speed; the loopback isn't network traffic."""
        s = Sampler(proc=self.proc, sys=self.sys, clock=lambda: self.now)
        self.net(10_000, 5_000)
        s.sample()
        self.now += 2
        self.net(30_000, 6_000)
        snap = s.sample()
        self.assertEqual(snap.net["rx_bytes_ps"], 10_000)
        self.assertEqual(snap.net["rx_bytes"], 30_000)          # no lo bytes in the total
        self.now += 2
        self.net(100, 100)                                      # interface went down and up
        snap = s.sample()
        self.assertEqual((snap.net["rx_bytes_ps"], snap.net["tx_bytes_ps"]), (0.0, 0.0))


class ManageEdgeTest(unittest.TestCase):
    def test_units_text_and_status(self):
        """systemctl's plain listing (failed units have a ● marker) parses; status words map like Windows."""
        text = ("● bad.service loaded failed failed The Broken One\n"
                "  ok.service  loaded active running OK daemon with spaces\n"
                "  sock.socket loaded active listening Not a service\n")
        got = manage.parse_units_text(text, "system")
        self.assertEqual([(s.unit, s.status, s.description) for s in got],
                         [("bad.service", "Failed", "The Broken One"),
                          ("ok.service", "Running", "OK daemon with spaces")])
        st = manage.Service("x", "", "", "active", "exited", "user")
        self.assertEqual(st.status, "Active")
        self.assertEqual(manage.Service("x", "", "", "reloading", "", "user").status, "Reloading")
        self.assertEqual(manage.Service("x", "", "", "inactive", "dead", "user").status, "Stopped")

    def test_service_action_pkexec_only_for_system(self):
        """A refused system unit retries through pkexec; a user unit never escalates."""
        calls = []

        def run(cmd):
            calls.append(cmd)
            return (0, "") if cmd[0] == "pkexec" else (1, "denied")
        with mock.patch.object(manage.shutil, "which", return_value="/usr/bin/pkexec"):
            self.assertEqual(manage.service_action("a.service", "restart", "system", run=run), (True, ""))
            self.assertEqual(calls[-1], ["pkexec", "systemctl", "restart", "a.service"])
            calls.clear()
            ok, msg = manage.service_action("b.service", "stop", "user", run=run)
        self.assertEqual((ok, msg), (False, "denied"))
        self.assertEqual(calls, [["systemctl", "--user", "stop", "b.service"]])

    def test_set_keys_idempotent_and_section_scoped(self):
        """Disabling twice writes one Hidden=true; enabling removes it only from [Desktop Entry]."""
        text = "[Desktop Entry]\nName=A\nExec=a\n\n[Desktop Action x]\nHidden=true\nExec=b\n"
        off = manage._set_keys(manage._set_keys(text, {"Hidden": "true"}), {"Hidden": "true"})
        self.assertEqual(off.count("Hidden=true"), 2)            # its own + the action's
        self.assertTrue(manage._true(manage._desktop_section(off).get("Hidden")))
        on = manage._set_keys(off, {"Hidden": None})
        self.assertNotIn("Hidden", manage._desktop_section(on))
        self.assertIn("[Desktop Action x]\nHidden=true", on)

    def test_group_processes_kinds(self):
        """Kernel threads and system accounts are System; another user's and helper processes are Background;
        an app's children (even of another kind) stay under the app."""
        procs = {1: _proc(1, "systemd", uid=0, ppid=0, exe="systemd"), 2: _proc(2, "kthreadd", uid=0, exe=""),
                 10: _proc(10, "firefox", ppid=1, exe="firefox"), 11: _proc(11, "helper", ppid=10, exe="helper"),
                 20: _proc(20, "bash", uid=1001, exe="bash"), 21: _proc(21, "daemon", exe="daemon")}
        apps, background, system = manage.group_processes(
            procs, lambda p: "firefox" if p.exe == "firefox" else None, 1000)
        self.assertEqual(apps, {"firefox": [10, 11]})
        self.assertEqual(background, [20, 21])
        self.assertEqual(system, [1, 2])

    def test_sync_store_keeps_rows_in_place(self):
        """The Processes list keeps surviving rows (no reorder flicker): gone ones go, new ones are appended."""
        from sonata2.activity.pages import sync_store
        objs = [GObject.Object() for _ in range(4)]
        store = Gio.ListStore(item_type=GObject.Object)
        store.splice(0, 0, objs[:3])
        sync_store(store, [objs[2], objs[3], objs[0]])
        self.assertEqual([store.get_item(i) for i in range(store.get_n_items())], [objs[0], objs[2], objs[3]])


# == Camera ====================================================================================
class CameraLogicTest(unittest.TestCase):
    def test_encoder_choice(self):
        """H.264/MP4 (GPU encoder first) when muxer and parser exist, else VP8/WebM, else None."""
        from sonata2.camera import engine

        def with_elements(names):
            fake = SimpleNamespace(ElementFactory=SimpleNamespace(find=lambda n: n in names))
            with mock.patch.object(engine, "Gst", fake):
                return engine.pick_encoders()
        self.assertEqual(with_elements({"x264enc", "vah264enc", "mp4mux", "h264parse", "voaacenc"}),
                         ("vah264enc", "h264parse", "mp4mux", "voaacenc", ".mp4"))
        self.assertEqual(with_elements({"x264enc", "vp8enc", "webmmux"}),             # no mp4mux
                         ("vp8enc", None, "webmmux", None, ".webm"))
        self.assertIsNone(with_elements({"x264enc"}))

    def test_recent_photos_survives_dangling_symlink(self):
        """A broken link (or a file deleted meanwhile) in the Camera folder must not crash the window."""
        from sonata2.camera import window as W
        folder = tempfile.mkdtemp(dir=_TMP)
        _write(os.path.join(folder, "a.jpg"), b"x", "wb")
        os.symlink(os.path.join(folder, "missing.jpg"), os.path.join(folder, "b.jpg"))
        self.assertEqual(W.recent_photos(folder), [os.path.join(folder, "a.jpg")])


# == Game controllers ==========================================================================
class _Pointer:
    ok = True

    def __init__(self):
        self.log = []

    def move(self, dx, dy): self.log.append(("move", dx, dy))
    def button(self, b, p): self.log.append(("button", b, p))
    def scroll(self, dx, dy): self.log.append(("scroll", dx, dy))
    def close(self): pass


class GamepadEdgeTest(unittest.TestCase):
    def setUp(self):
        self.keys = []
        patches = [mock.patch.object(S, "key", lambda name, mods=(): self.keys.append(name)),
                   mock.patch.object(S, "steam_running", lambda: False),
                   mock.patch.object(S.E, "find_gamepads", lambda *a: []),          # never real devices
                   mock.patch.object(S.E, "find_guide_devices", lambda *a: [])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        gamemode.Watcher._write(False)
        self.addCleanup(gamemode.Watcher._write, False)
        self.g = S.Gamepads(None, None)
        self.g.cfg["enabled"] = True
        self.g._notify = lambda _t: None
        self.g.vp = _Pointer()
        self.pad = SimpleNamespace(path="p", norm=lambda c, v: v)

    def ev(self, typ, code, value):
        self.g._event(self.pad, typ, code, value)

    def test_hat_dpad_and_key_repeat(self):
        """A hat d-pad sends one arrow per push (release ignored); held buttons' auto-repeat (2) is ignored."""
        self.ev(E.EV_ABS, E.ABS_HAT0X, -1)
        self.ev(E.EV_ABS, E.ABS_HAT0X, 0)
        self.ev(E.EV_ABS, E.ABS_HAT0Y, 1)
        self.ev(E.EV_KEY, E.BTN_DPAD_UP, 1)
        self.ev(E.EV_KEY, E.BTN_DPAD_UP, 2)
        self.ev(E.EV_KEY, 0x2ff, 1)                             # an unmapped button
        self.assertEqual(self.keys, ["Left", "Down", "Up"])

    def test_button_released_when_paused_mid_drag(self):
        """Holding A (drag) when desktop control pauses must not leave the virtual left button stuck down."""
        self.ev(E.EV_KEY, E.BTN_SOUTH, 1)
        gamemode.Watcher._write(True)                          # a fullscreen game takes the focus
        self.ev(E.EV_KEY, E.BTN_SOUTH, 0)
        self.assertEqual(self.g.vp.log[-1], ("button", S.BTN_LEFT, False))

    def test_norm_with_device_ranges(self):
        """Axis ranges from the device: 0..255 sticks centre at 0, triggers map to 0..1 and clamp."""
        pad = E.Gamepad.__new__(E.Gamepad)
        pad.ranges = {E.ABS_X: (0, 255, 0), E.ABS_Z: (0, 255, 0)}
        self.assertAlmostEqual(pad.norm(E.ABS_X, 127.5), 0.0)
        self.assertEqual((pad.norm(E.ABS_X, 0), pad.norm(E.ABS_X, 400)), (-1.0, 1.0))
        self.assertEqual((pad.norm(E.ABS_Z, 255), pad.norm(E.ABS_Z, -9)), (1.0, 0.0))
        self.assertAlmostEqual(pad.norm(E.ABS_Y, 32767), 1.0, places=3)      # default range

    def test_detection_on_non_devices(self):
        """Regular files named event* (or an unreadable folder) are never taken for controllers."""
        d = tempfile.mkdtemp(dir=_TMP)
        _write(os.path.join(d, "event0"), "junk")
        _write(os.path.join(d, "mouse0"), "junk")
        self.assertEqual(E.find_gamepads(d), [])
        self.assertEqual(E.find_guide_devices({"Pad"}, d), [])
        self.assertEqual(E.find_gamepads(os.path.join(d, "missing")), [])
        self.assertEqual(E.gamepad_name(os.path.join(d, "event0")), "")


# == Fixes (regressions) =======================================================================
def _gtk_app(app_id):
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    from sonata2 import ui
    Adw.init()
    ui.setup()
    app = Adw.Application(application_id=app_id)
    app.register(None)
    return app


def _settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class TaskManagerFixTest(unittest.TestCase):
    def test_password_prompt_not_killed_after_20s(self):
        """System units go through polkit / pkexec: their password dialog gets minutes, not 20 s."""
        seen = []

        def fake_run(cmd, timeout=20):
            seen.append((cmd[0], timeout))
            return 1, "denied"
        with mock.patch.object(manage, "_run", fake_run), \
                mock.patch.object(manage.shutil, "which", return_value="/usr/bin/pkexec"):
            manage.service_action("a.service", "restart", "system")
            self.assertEqual(seen, [("systemctl", manage.AUTH_TIMEOUT), ("pkexec", manage.AUTH_TIMEOUT)])
            self.assertGreaterEqual(manage.AUTH_TIMEOUT, 120)
            seen.clear()
            manage.service_action("b.service", "stop", "user")
        self.assertEqual(seen, [("systemctl", 20)])                     # no password: the short timeout

    def test_gpu_fd_scan_resolves_links_only_when_fds_change(self):
        """The 5 s GPU pass lists each process's fds but readlinks them only when that list changed."""
        root = tempfile.mkdtemp(dir=_TMP)
        d = os.path.join(root, "300")
        os.makedirs(os.path.join(d, "fd"))
        os.symlink("/dev/dri/renderD128", os.path.join(d, "fd", "7"))
        os.symlink("/home/x/file", os.path.join(d, "fd", "3"))
        smp = Sampler.__new__(Sampler)
        smp._gpu_fds, smp._gpu_scan, smp._gpu_cards = {}, {}, {}
        key, calls = (300, 5), []
        real = os.readlink

        def counting(path, *a):
            calls.append(path)
            return real(path, *a)
        with mock.patch.object(procfs.os, "readlink", counting):
            self.assertEqual(smp._drm_fdinfos(key, d, 0.0), [os.path.join(d, "fdinfo", "7")])
            self.assertEqual(len(calls), 2)
            smp._drm_fdinfos(key, d, procfs.FD_RESCAN_S)                 # same fds: no readlink
            self.assertEqual(len(calls), 2)
            os.symlink("/dev/dri/card0", os.path.join(d, "fd", "9"))     # a new fd: resolved again
            got = smp._drm_fdinfos(key, d, 2 * procfs.FD_RESCAN_S)
            self.assertEqual(sorted(got), [os.path.join(d, "fdinfo", n) for n in ("7", "9")])
            calls.clear()
            smp._drm_fdinfos(key, d, 2 * procfs.FD_RESCAN_S + procfs.FD_FULL_S)   # reused numbers: full pass
            self.assertEqual(len(calls), 3)

    def test_gone_gpu_leaves_performance_list(self):
        """A GPU that stops reporting (unplugged) leaves the Performance list after a few samples."""
        from sonata2.activity.window import TaskManagerWindow
        app = _gtk_app("io.test.review.media.gpu")
        w = TaskManagerWindow(app)
        w.icons = {}
        page = w.page_by_id["performance"]
        snap = w.sampler.sample()
        snap.gpus = {"card7": 40.0}
        page.update(snap)
        self.assertIn("gpu:card7", page.resources)
        page.list.select_row(page.rows["gpu:card7"])
        snap.gpus = {}
        page.update(snap)
        self.assertIn("gpu:card7", page.resources)                      # one missed read: kept
        for _ in range(10):
            page.update(snap)
        self.assertNotIn("gpu:card7", page.resources)
        self.assertNotIn("gpu:card7", page.rows)
        self.assertEqual(page.current, "cpu")
        w._alive = False
        w.destroy()

    def test_properties_refresh_skips_readlink(self):
        """The open Properties panel refreshes every sample without reading the exe link again."""
        from sonata2.activity.window import TaskManagerWindow
        row = SimpleNamespace(sv={"pid": os.getpid(), "name": "x", "user": "me", "cpu": 0, "cpu_time": 0,
                                  "threads": 1, "mem": 0}, ppid=1, uid=0, started=0, cmdline="x")
        win = SimpleNamespace(pid_rows={}, exe_path=mock.Mock(side_effect=AssertionError("readlink")))
        self.assertEqual(TaskManagerWindow._props(win, row, path=False)[-1][0], "path")
        win.exe_path = lambda _r: "/usr/bin/x"
        self.assertEqual(TaskManagerWindow._props(win, row)[-1][2], "/usr/bin/x")



class CameraFixTest(unittest.TestCase):
    def test_last_capture_with_dangling_link(self):
        """The newest capture ignores broken links in either folder."""
        from sonata2.camera import window as W
        pics, vids = tempfile.mkdtemp(dir=_TMP), tempfile.mkdtemp(dir=_TMP)
        _write(os.path.join(pics, "a.jpg"), b"x", "wb")
        os.symlink(os.path.join(vids, "gone.mp4"), os.path.join(vids, "z.mp4"))
        with mock.patch.object(W, "photos_dir", lambda: pics), mock.patch.object(W, "videos_dir", lambda: vids):
            self.assertEqual(W.last_capture(), os.path.join(pics, "a.jpg"))

    def test_video_thumbnail_needs_no_temp_file(self):
        """The video's thumbnail is decoded from memory, not a fixed /tmp file another user could own."""
        from sonata2.camera import window as W
        src = os.path.join(os.path.dirname(__file__), "..", "sonata2")
        jpeg = None
        for folder, _d, files in os.walk(src):
            jpeg = next((os.path.join(folder, f) for f in files if f.endswith((".jpg", ".png"))), None)
            if jpeg:
                break
        if jpeg is None:
            from gi.repository import GdkPixbuf
            pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 48)
            pb.fill(0x336699ff)
            data = pb.save_to_bufferv("jpeg", [], [])[1]
        else:
            with open(jpeg, "rb") as f:
                data = f.read()
        fake = SimpleNamespace(cam=SimpleNamespace(frame_jpeg=lambda: data,
                                                   photo=mock.Mock(side_effect=AssertionError("tmp file"))))
        with mock.patch.object(W.GLib, "get_tmp_dir", side_effect=AssertionError("tmp dir")):
            self.assertIsNotNone(W.CameraWindow._frame_texture(fake))
        fake.cam.frame_jpeg = lambda: None
        self.assertIsNone(W.CameraWindow._frame_texture(fake))

    def test_device_probe_off_the_main_loop(self):
        """Looking for cameras (GStreamer's device monitor) runs in a worker thread."""
        from sonata2.camera import window as W
        calls = []
        fake = SimpleNamespace(cam=SimpleNamespace(source=None), _closed=False, cams=[])
        fake._got_cameras = lambda cams: calls.append(("got", cams))
        with mock.patch.object(W, "run_async", lambda fn, cb: calls.append(("async", fn))):
            W.CameraWindow._open_camera(fake)
        self.assertEqual(calls, [("async", W.engine.devices)])
        fake._start_camera = mock.Mock()
        fake._closed = True
        W.CameraWindow._got_cameras(fake, ["c"])                        # closed meanwhile: nothing starts
        fake._start_camera.assert_not_called()


class GamepadFixTest(GamepadEdgeTest):
    def test_one_plug_one_scan(self):
        """The burst of /dev/input changes one plug makes is debounced into a single rescan."""
        with mock.patch.object(self.g, "scan") as scan:
            for _ in range(6):
                self.g._scan_soon()
            _settle(800)
        self.assertEqual(scan.call_count, 1)

    def test_button_released_on_unplug_and_disable(self):
        """Unplugging or turning desktop control off mid-drag lets go of the virtual button."""
        self.ev(E.EV_KEY, E.BTN_SOUTH, 1)
        self.g._event(self.pad, None, 0, 0)                             # unplugged
        self.assertEqual(self.g.vp.log[-1], ("button", S.BTN_LEFT, False))
        self.ev(E.EV_KEY, E.BTN_NORTH, 1)
        with mock.patch.object(S.config, "save"):
            self.g.set_enabled(False)
        self.assertEqual(self.g.vp.log[-1], ("button", S.BTN_RIGHT, False))
        n = len(self.g.vp.log)
        self.g.set_enabled(False) if False else self.g._release_held()  # nothing held: no stray release
        self.assertEqual(len(self.g.vp.log), n)


def _fake_stream(_path):
    """A 90 s movie that plays nothing (as in test_videos)."""
    from gi.repository import Gtk

    class Fake(Gtk.MediaStream):
        def do_play(self):
            return True

        def do_pause(self):
            pass

        def do_seek(self, ts):
            self.seek_success()
            self.update(ts)
    s = Fake()
    s.stream_prepared(True, True, True, 90_000_000)
    return s


class VideosFixTest(unittest.TestCase):
    def test_volume_saved_once_slider_rests(self):
        """Dragging the volume slider doesn't rewrite the config each step; closing saves at once."""
        from sonata2.videos import window as vw
        app = _gtk_app("io.test.review.media.videos")
        win = vw.VideoWindow(app, "/movies/vol.mp4", stream_factory=_fake_stream)
        win.present()
        _settle(100)
        with mock.patch.object(vw.config, "update") as upd:
            for v in range(10, 60, 5):
                win._volume_changed(v)
            self.assertEqual(upd.call_count, 0)
            _settle(800)
            self.assertEqual(upd.call_count, 1)
            self.assertAlmostEqual(upd.call_args.kwargs["volume"], 0.55)
            win.toggle_mute()
            win.close()                                                 # pending: saved on close
            vols = [c.kwargs for c in upd.call_args_list if "volume" in c.kwargs]
            self.assertEqual(len(vols), 2)
            self.assertTrue(vols[-1]["muted"])
        _settle(50)


# == Animations ================================================================================
class AnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _gtk_app("io.test.review.media.anim")
        from gi.repository import Gtk
        cls.Gtk = Gtk

    def test_task_manager_page_switch_crossfades(self):
        """Switching Task Manager pages crossfades the content and the page's toolbar buttons."""
        from sonata2.activity.window import TaskManagerWindow
        Gtk = self.Gtk
        w = TaskManagerWindow(self.app)
        w.icons = {}
        w.present()
        _settle(300)
        for st in (w.stack, w.page_buttons):
            self.assertEqual(st.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
            self.assertGreater(st.get_transition_duration(), 0)
        w.show_page("performance" if w.page_id != "performance" else "details")
        self.assertTrue(w.stack.get_transition_running())
        w._alive = False
        w.destroy()
        _settle(50)


    def test_videos_hud_fades(self):
        """The video controls fade out / in (CSS opacity transition on the .hidden class)."""
        from sonata2.videos import window as vw
        with open(vw.__file__, encoding="utf-8") as f:
            src = f.read()
        self.assertRegex(src, r"\.vd-hud \{[^}]*transition: opacity")
        self.assertIn(".vd-hud.hidden { opacity: 0; }", src)
        win = vw.VideoWindow(self.app, "/movies/hud.mp4", stream_factory=_fake_stream)
        win.present()
        _settle(100)
        win.stream.play()
        win._hide_now()                                                 # playing: the HUD fades out
        self.assertTrue(win.hud.has_css_class("hidden"))
        win.toggle_fullscreen()                                         # full screen brings it back
        _settle(300)
        win._show_hud()
        self.assertFalse(win.hud.has_css_class("hidden"))
        win.close()
        _settle(50)

    def test_camera_flash_fades(self):
        """The shutter's blink (and the screen flash) fade out frame by frame."""
        from sonata2.camera import window as W
        win = W.CameraWindow(self.app, source=lambda: None)
        ticks = []
        real = GLib.timeout_add

        def spy(ms, fn, *a):
            ticks.append(ms)
            return real(ms, fn, *a)
        win.blink.set_opacity(0.9)
        with mock.patch.object(W.GLib, "timeout_add", spy):
            win._fade(win.blink, 120)
        self.assertEqual(ticks, [16])                                   # ~60 fps steps
        _settle(60)
        mid = win.blink.get_opacity()
        self.assertTrue(0.0 < mid < 0.9, mid)
        _settle(200)
        self.assertEqual(win.blink.get_opacity(), 0.0)
        win._cancel_count()
        win.cam.stop()
        win.destroy()
        _settle(50)


if __name__ == "__main__":
    unittest.main()
