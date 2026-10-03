"""Review tests for the media area: Task Manager's /proc parsing and pages
data (sonata2/activity), Music's tags / library / queue / MPRIS (sonata2/music),
Camera helpers (sonata2/camera) and game controllers (sonata2/gamepad).
Headless and fast; everything on temp dirs and fakes.
Run: xvfb-run -a python3 -m unittest tests.test_review_media -v"""
import os
import shutil
import struct
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
from sonata2.music import library as lib, mpris, tags  # noqa: E402
from sonata2.music.queue import REPEAT_ALL, REPEAT_OFF, Queue  # noqa: E402


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

    @unittest.expectedFailure
    def test_cpu_time_never_shows_60_seconds(self):
        """CPU time just under a minute boundary must roll over, not print "1:60.00"."""
        # BUG: procfs.fmt_cpu_time rounds the seconds after divmod: 119.996 -> "1:60.00", 59.999 -> "60.00"
        self.assertEqual(procfs.fmt_cpu_time(119.996), "2:00.00")


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


# == Music =====================================================================================
def _atom(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _mp4(title="Mp4 Song", track=5, seconds=12, cover=b"\x89PNGxx"):
    mvhd = _atom(b"mvhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 1000, seconds * 1000) + b"\x00" * 80)
    data = lambda v: _atom(b"data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + v)  # noqa: E731
    ilst = _atom(b"ilst", _atom(b"\xa9nam", data(title.encode())) + _atom(b"\xa9ART", data(b"Mp4 Artist"))
                 + _atom(b"trkn", data(struct.pack(">HHHH", 0, track, 10, 0))) + _atom(b"gnre", data(b"\x00\x0a"))
                 + _atom(b"covr", data(cover)))
    moov = _atom(b"moov", mvhd + _atom(b"udta", _atom(b"meta", b"\x00\x00\x00\x00" + ilst)))
    return _atom(b"ftyp", b"M4A \x00\x00\x00\x00") + _atom(b"mdat", b"\x00" * 64) + moov


class TagEdgeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_TMP)

    def path(self, name, data: bytes):
        p = os.path.join(self.dir, name)
        _write(p, data, "wb")
        return p

    def test_mp4_ilst_and_duration(self):
        """M4A tags (title, artist, track, numeric genre, cover) and mvhd duration with moov after mdat."""
        t = tags.read(self.path("a.m4a", _mp4()))
        self.assertEqual((t["title"], t["artist"], t["track"], t["genre"]), ("Mp4 Song", "Mp4 Artist", 5, "Metal"))
        self.assertAlmostEqual(t["duration"], 12.0)
        self.assertEqual(t["cover"], (b"\x89PNGxx", "image/png"))

    def test_wav_duration(self):
        """WAV length = data size / byte rate (from the fmt chunk)."""
        fmt = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
        body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 176400 * 3)
        p = self.path("01 Intro.wav", b"RIFF" + struct.pack("<I", len(body)) + body)
        t = tags.read(p)
        self.assertAlmostEqual(t["duration"], 3.0)
        self.assertEqual((t["title"], t["track"]), ("Intro", 1))

    def test_mp3_xing_vbr_and_id3v1(self):
        """A VBR MP3 without ID3v2 takes its length from the Xing frame count and its tags from ID3v1."""
        frame = b"\xff\xfb\x90\x64" + b"\x00" * 32 + b"Xing" + struct.pack(">II", 1, 1000) + b"\x00" * 400
        v1 = (b"TAG" + b"V1 Title".ljust(30, b"\0") + b"V1 Artist".ljust(30, b"\0") + b"V1 Album".ljust(30, b"\0")
              + b"1999" + b"\0" * 28 + b"\x00\x07" + bytes([17]))
        t = tags.read(self.path("x.mp3", frame + b"\x00" * 5000 + v1))
        self.assertAlmostEqual(t["duration"], 1000 * 1152 / 44100, places=2)
        self.assertEqual((t["title"], t["artist"], t["album"], t["year"], t["track"], t["genre"]),
                         ("V1 Title", "V1 Artist", "V1 Album", 1999, 7, "Rock"))

    def test_number_genre_and_filename_helpers(self):
        """'3/12', dates, ID3 numeric genres and disc-track file names parse; years in names aren't tracks."""
        self.assertEqual((tags._num("3/12"), tags._num("2004-05-01"), tags._num(None), tags._num("x")),
                         (3, 2004, 0, 0))
        self.assertEqual((tags._genre("(9)"), tags._genre("(9)Death Thrash"), tags._genre("13"), tags._genre("Indie")),
                         ("Metal", "Death Thrash", "Pop", "Indie"))
        self.assertEqual(tags._genre("(250)"), "")
        self.assertEqual(tags.from_filename("/m/1-05 Song Name.flac"), {"track": 5, "title": "Song Name"})
        self.assertEqual(tags.from_filename("/m/1984.mp3"), {"track": 0, "title": "1984"})
        self.assertEqual(tags.from_filename("/m/2001 - A Space Odyssey.mp3")["track"], 0)


class LibraryEdgeTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(dir=_TMP)
        self.root = os.path.join(self.base, "Music")
        os.makedirs(self.root)
        self.art = os.path.join(self.base, "art")

    def test_hidden_and_non_audio_skipped_and_folder_fallback(self):
        """Hidden files/folders and non-audio are ignored; untagged songs take Artist/Album from the folders
        and the folder's cover.jpg."""
        album = os.path.join(self.root, "The Band", "First Album")
        _write(os.path.join(album, "02 Second.mp3"), b"\x00" * 64, "wb")
        _write(os.path.join(album, "cover.jpg"), b"\xff\xd8\xff", "wb")
        _write(os.path.join(album, ".03 hidden.mp3"), b"\x00", "wb")
        _write(os.path.join(album, "notes.txt"), "x")
        _write(os.path.join(self.root, ".trash", "x.mp3"), b"\x00", "wb")
        got = lib.scan(self.root, {}, self.art)
        self.assertEqual(list(got), [os.path.join(album, "02 Second.mp3")])
        t = next(iter(got.values()))
        self.assertEqual((t["artist"], t["album"], t["title"], t["track"]), ("The Band", "First Album", "Second", 2))
        self.assertEqual(t["art"], os.path.join(album, "cover.jpg"))

    def test_album_key_compilation_by_folder(self):
        """Without an album artist, same-titled albums in different folders stay apart; same folder groups."""
        a = {"path": "/m/x/Greatest Hits/1.mp3", "album": "Greatest Hits", "artist": "X"}
        b = {"path": "/m/y/Greatest Hits/1.mp3", "album": "Greatest Hits", "artist": "Y"}
        c = {"path": "/m/x/Greatest Hits/2.mp3", "album": "greatest hits", "artist": "X"}
        self.assertNotEqual(lib.album_key(a), lib.album_key(b))
        self.assertEqual(lib.album_key(a), lib.album_key(c))
        albums = lib.group_albums([a, b, c])
        self.assertEqual(sorted(len(x["tracks"]) for x in albums), [1, 2])

    @unittest.expectedFailure
    def test_symlink_loop_scans_each_song_once(self):
        """A symlink pointing back up the Music folder must not list every song dozens of times."""
        # BUG: library.scan uses os.walk(followlinks=True) with no visited-directory check: a link to the
        # parent yields ~40 copies (until ELOOP); two such links make the scan exponential (hangs).
        _write(os.path.join(self.root, "a.mp3"), b"\x00" * 64, "wb")
        os.symlink(self.root, os.path.join(self.root, "loop"))
        self.assertEqual(len(lib.scan(self.root, {}, self.art)), 1)


class QueueEdgeTest(unittest.TestCase):
    def test_empty_queue_is_safe(self):
        """Next / Previous / jump / shuffle on an empty queue do nothing and never raise."""
        q = Queue()
        self.assertIsNone(q.next())
        self.assertIsNone(q.previous())
        q.jump(3)
        q.set_shuffle(True)
        q.set([])
        self.assertEqual((q.current, q.pos, q.upcoming()), (None, -1, []))

    def test_shuffle_off_returns_to_queued_order(self):
        """Turning shuffle off keeps the current song and continues in the queued order after it."""
        import random
        q = Queue(random.Random(3))
        q.set(list("abcdef"), 0)
        q.set_shuffle(True)
        q.next()
        cur = q.current
        q.set_shuffle(False)
        self.assertEqual(q.current, cur)
        self.assertEqual(q.upcoming(), list("abcdef")[list("abcdef").index(cur) + 1:])

    def test_repeat_all_shuffle_wrap_avoids_same_song(self):
        """At the end of a shuffled repeat-all queue the reshuffle never plays the last song again first."""
        import random
        for seed in range(20):
            q = Queue(random.Random(seed))
            q.repeat = REPEAT_ALL
            q.set(list("abcd"), 0)
            q.set_shuffle(True)
            for _ in range(3):
                q.next()
            last = q.current
            self.assertNotEqual(q.next(), last)
            self.assertEqual(sorted(q.items[i] for i in q.order), list("abcd"))

    def test_upcoming_wraps_with_repeat_all(self):
        """Up Next under repeat-all lists the rest, then the start of the queue (not the current song)."""
        q = Queue()
        q.set(list("abcd"), 2)
        self.assertEqual(q.upcoming(), ["d"])
        q.repeat = REPEAT_ALL
        self.assertEqual(q.upcoming(), ["d", "a", "b"])
        q.repeat = REPEAT_OFF
        q.jump(1)
        self.assertIsNone(q.next())                              # end of the queue, repeat off


class _Invocation:
    def __init__(self):
        self.result = self.error = None

    def return_value(self, v):
        self.result = ("ok", v)

    def return_dbus_error(self, name, msg):
        self.error = (name, msg)


class MprisServerTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        track = {"path": "/m/a.mp3", "title": "A"}
        c = SimpleNamespace(player=SimpleNamespace(position=30.0, status="Playing", volume=0.5),
                            queue=SimpleNamespace(repeat="all", shuffle=True, __len__=lambda: 1),
                            current_track=lambda: track, can_next=lambda: True)
        for name in ("seek_to", "set_volume", "set_shuffle", "set_repeat", "next", "previous", "toggle", "play",
                     "pause", "stop", "raise_window", "quit", "open_uris"):
            setattr(c, name, (lambda n: lambda *a: self.calls.append((n,) + a))(name))
        self.srv = mpris.Server.__new__(mpris.Server)          # no session bus in tests
        self.srv.c, self.srv.bus, self.srv._ids, self.srv._owner = c, None, [], 0

    def call(self, method, sig="()", args=()):
        inv = _Invocation()
        self.srv._call(None, None, None, None, method, GLib.Variant(sig, args), inv)
        return inv

    def test_seek_setposition_and_unknown(self):
        """Seek is relative; SetPosition with a stale track id is ignored; unknown methods get a D-Bus error."""
        self.call("Seek", "(x)", (-10_000_000,))
        self.call("SetPosition", "(ox)", ("/stale/track", 5_000_000))
        self.call("SetPosition", "(ox)", (mpris.track_id("/m/a.mp3"), 5_000_000))
        self.assertEqual(self.calls, [("seek_to", 20.0), ("seek_to", 5.0)])
        inv = self.call("Bogus")
        self.assertEqual(inv.error[0], "org.freedesktop.DBus.Error.UnknownMethod")
        self.assertIsNone(inv.result)
        self.assertEqual(self.call("PlayPause").result, ("ok", None))

    def test_set_properties(self):
        """LoopStatus/Shuffle/Volume writes from the menu bar reach the controller with Music's names."""
        self.srv._set(None, None, None, mpris.PLAYER_IFACE, "LoopStatus", GLib.Variant("s", "Track"))
        self.srv._set(None, None, None, mpris.PLAYER_IFACE, "LoopStatus", GLib.Variant("s", "Weird"))
        self.srv._set(None, None, None, mpris.PLAYER_IFACE, "Shuffle", GLib.Variant("b", False))
        self.srv._set(None, None, None, mpris.PLAYER_IFACE, "Volume", GLib.Variant("d", 0.25))
        self.assertEqual(self.calls, [("set_repeat", "one"), ("set_repeat", "off"), ("set_shuffle", False),
                                      ("set_volume", 0.25)])
        self.assertEqual(self.srv._get(None, None, None, mpris.PLAYER_IFACE, "LoopStatus").unpack(), "Playlist")
        self.srv.changed()                                     # no bus yet: a no-op, never raises


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

    @unittest.expectedFailure
    def test_recent_photos_survives_dangling_symlink(self):
        """A broken link (or a file deleted meanwhile) in the Camera folder must not crash the window."""
        # BUG: camera/window.py recent_photos sorts with os.path.getmtime, which raises FileNotFoundError;
        # CameraWindow.__init__ calls last_capture() -> the Camera app fails to open.
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

    @unittest.expectedFailure
    def test_button_released_when_paused_mid_drag(self):
        """Holding A (drag) when desktop control pauses must not leave the virtual left button stuck down."""
        # BUG: service._event drops every event while paused, including the release of a button pressed before
        # (also on unplug / turning control off): the compositor keeps BTN_LEFT held.
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


if __name__ == "__main__":
    unittest.main()
