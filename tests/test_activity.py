"""Task Manager (component "activity"): /proc and /sys parsing on a fake
tree, rates from two samples, the pages' data (app grouping, users, app
history, startup apps, services), sorting, filtering and the window
(xvfb-run python3 -m unittest tests.test_activity)."""
import os
import shutil
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

from sonata2.activity import manage, procfs  # noqa: E402
from sonata2.activity.procfs import CLK_TCK, PAGE_SIZE, Sampler  # noqa: E402

MEMINFO = """MemTotal:       8000000 kB
MemFree:        1000000 kB
MemAvailable:   6000000 kB
Buffers:         100000 kB
Cached:         2000000 kB
SwapTotal:      1000000 kB
SwapFree:        750000 kB
AnonPages:      1500000 kB
KernelStack:      10000 kB
PageTables:       20000 kB
SUnreclaim:       30000 kB
HugePages_Total:      0
"""

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: {lo} 10 0 0 0 0 0 0 {lo} 10 0 0 0 0 0 0
  eth0: {rx} 100 0 0 0 0 0 0 {tx} 50 0 0 0 0 0 0
"""


def stat_line(pid, comm, ppid=1, ticks=(0, 0), threads=1, start=100, rss_pages=0):
    utime, stime = ticks
    rest = ["S", ppid, 0, 0, 0, -1, 0, 0, 0, 0, 0, utime, stime, 0, 0, 20, 0, threads, 0, start, 0, rss_pages]
    return f"{pid} ({comm}) " + " ".join(str(x) for x in rest) + " 0 0 0\n"


class FakeSystem:
    """A /proc and /sys tree in a temp dir, and a clock the test moves."""

    def __init__(self):
        self.root = tempfile.mkdtemp()
        self.proc = os.path.join(self.root, "proc")
        self.sys = os.path.join(self.root, "sys")
        self.now = 1000.0
        for d in ("net", ):
            os.makedirs(os.path.join(self.proc, d))
        for b in ("sda", "nvme0n1", "loop0"):
            os.makedirs(os.path.join(self.sys, "block", b))
        self.write("block/sda/size", "1953525168\n", self.sys)                  # 1 TB
        self.write("block/sda/queue/rotational", "1\n", self.sys)
        self.write("block/nvme0n1/device/model", "Fast SSD\n", self.sys)
        self.write("cpuinfo", "".join(f"processor\t: {i}\nmodel name\t: Test CPU 9000\nphysical id\t: 0\n"
                                      f"core id\t\t: {i // 2}\ncpu MHz\t\t: 1000.0\n\n" for i in range(4)))
        for i, f in enumerate((2000000, 3000000)):
            self.write(f"devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq", f"{f}\n", self.sys)
        self.write("devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "3500000\n", self.sys)
        self.write("uptime", "5000.00 4000.00\n")
        self.write("meminfo", MEMINFO)
        self.cpu((100, 0, 50, 800, 50))
        self.disk(0, 0)
        self.net(0, 0)

    def write(self, rel, text, base=None):
        path = os.path.join(base or self.proc, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)

    def cpu(self, v):
        user, nice, system, idle, iowait = v
        self.write("stat", f"cpu  {user} {nice} {system} {idle} {iowait} 0 0 0 0 0\n"
                           f"cpu0 {user} {nice} {system} {idle} {iowait} 0 0 0 0 0\nbtime 1700000000\n")

    def disk(self, sectors_read, sectors_written, reads=0, writes=0, io_ms=0):
        rows = []
        for name in ("sda", "sda1", "nvme0n1", "nvme0n1p1", "loop0"):
            k = 1 if name in ("sda", "nvme0n1") else 7         # partitions/loop must not count
            rows.append(f"   8 0 {name} {reads * k} 0 {sectors_read * k} 0 {writes * k} 0 {sectors_written * k} "
                        f"0 0 {io_ms * k} 0")
        self.write("diskstats", "\n".join(rows) + "\n")

    def net(self, rx, tx, lo=999):
        self.write("net/dev", NET_DEV.format(rx=rx, tx=tx, lo=lo))

    def process(self, pid, comm, cmdline=None, ticks=(0, 0), threads=1, rss_pages=10, start=100, io=None, swap_kb=0):
        d = str(pid)
        self.write(f"{d}/stat", stat_line(pid, comm, ticks=ticks, threads=threads, start=start, rss_pages=rss_pages))
        self.write(f"{d}/cmdline", "\0".join(cmdline) + "\0" if cmdline else "")
        if io is not None:
            self.write(f"{d}/io", f"rchar: 1\nwchar: 2\nread_bytes: {io[0]}\nwrite_bytes: {io[1]}\n")
        self.write(f"{d}/status", f"Name:\t{comm}\nVmRSS:\t{rss_pages * 4} kB\nVmSwap:\t{swap_kb} kB\n")

    def kill(self, pid):
        shutil.rmtree(os.path.join(self.proc, str(pid)))

    def battery(self):
        base = "class/power_supply"
        self.write(f"{base}/BAT0/type", "Battery\n", self.sys)
        self.write(f"{base}/BAT0/capacity", "80\n", self.sys)
        self.write(f"{base}/BAT0/status", "Discharging\n", self.sys)
        self.write(f"{base}/BAT0/energy_now", "40000000\n", self.sys)
        self.write(f"{base}/BAT0/energy_full", "50000000\n", self.sys)
        self.write(f"{base}/BAT0/power_now", "10000000\n", self.sys)
        self.write(f"{base}/AC/type", "Mains\n", self.sys)
        self.write(f"{base}/AC/online", "0\n", self.sys)

    def sampler(self):
        return Sampler(proc=self.proc, sys=self.sys, clock=lambda: self.now)


class ParsingTest(unittest.TestCase):
    def setUp(self):
        self.fs = FakeSystem()
        self.fs.process(1, "init", ["/sbin/init"], ticks=(10, 5), threads=1)
        self.fs.process(2, "kthreadd")                                           # a kernel thread
        self.fs.process(300, "(my) weird proc", ["/usr/bin/weird"], ticks=(0, 0), threads=4, rss_pages=1000,
                        io=(4096, 8192), swap_kb=512)
        self.fs.process(400, "gnome-text-edit", ["/usr/bin/gnome-text-editor", "--gapplication-service"],
                        ticks=(200, 100), threads=7, rss_pages=500)

    def tearDown(self):
        shutil.rmtree(self.fs.root)

    def test_cpu_percent_from_two_samples(self):
        s = self.fs.sampler()
        first = s.sample()
        self.assertEqual(first.cpu["idle"], 100.0)                  # no rates from one sample
        self.assertEqual(first.procs[400].cpu, 0.0)
        # +100 user, +50 system, +250 idle(+iowait) jiffies, 2 s later; pid 300 used 1 s of CPU
        self.fs.cpu((150, 50, 100, 1000, 100))
        self.fs.process(300, "(my) weird proc", ["/usr/bin/weird"], ticks=(CLK_TCK // 2, CLK_TCK // 2), threads=4,
                        rss_pages=1000)
        self.fs.now += 2
        snap = s.sample()
        self.assertAlmostEqual(snap.cpu["user"], 25.0)
        self.assertAlmostEqual(snap.cpu["system"], 12.5)
        self.assertAlmostEqual(snap.cpu["idle"], 62.5)
        self.assertAlmostEqual(snap.procs[300].cpu, 50.0)               # 1 s of CPU in 2 s: 50 % of a core
        self.assertEqual(snap.procs[1].cpu, 0.0)
        self.assertAlmostEqual(snap.procs[400].cpu_time, 300 / CLK_TCK)
        self.assertAlmostEqual(snap.interval, 2.0)

    def test_cpu_line(self):
        a = procfs.parse_cpu_line("cpu  10 0 10 80 0 0 0 0 0 0")
        b = procfs.parse_cpu_line("cpu  20 0 20 160 0 0 0 0 0 0")
        self.assertEqual(procfs.cpu_percent(a, b), {"user": 10.0, "system": 10.0, "idle": 80.0})
        self.assertEqual(procfs.cpu_percent(b, b)["idle"], 100.0)

    def test_memory_totals(self):
        m = self.fs.sampler().sample().memory
        self.assertEqual(m["total"], 8000000 * 1024)
        self.assertEqual(m["used"], 2000000 * 1024)                     # total - available
        self.assertEqual(m["cached"], 2100000 * 1024)                   # cached + buffers
        self.assertEqual(m["swap"], 250000 * 1024)
        self.assertEqual(m["app"], 1500000 * 1024)
        self.assertEqual(m["wired"], 60000 * 1024)
        self.assertAlmostEqual(m["pressure"], 0.25)

    def test_process_list(self):
        procs = self.fs.sampler().processes({"io", "swap"})
        self.assertEqual(sorted(procs), [1, 2, 300, 400])
        weird = procs[300]
        self.assertEqual(weird.name, "(my) weird proc")                 # parentheses in the name
        self.assertEqual(weird.threads, 4)
        self.assertEqual(weird.rss, 1000 * PAGE_SIZE)
        self.assertEqual((weird.read_bytes, weird.write_bytes), (4096, 8192))
        self.assertEqual(weird.swap, 512 * 1024)
        self.assertEqual(weird.cmdline, "/usr/bin/weird")
        self.assertEqual(weird.uid, os.getuid())
        self.assertTrue(weird.user)
        self.assertEqual(procs[2].cmdline, "[kthreadd]")                  # kernel threads have no command line
        self.assertEqual(procs[2].exe, "")
        self.assertEqual(procs[2].read_bytes, -1)                         # /proc/<pid>/io not readable
        # comm is cut at 15 characters: the executable's name is shown instead
        self.assertEqual(procs[400].name, "gnome-text-editor")
        self.assertEqual(procs[1].started, 1700000000 + 100 / CLK_TCK)
        no_extras = self.fs.sampler().processes(want=())
        self.assertEqual(no_extras[300].read_bytes, -1)
        self.assertEqual(no_extras[300].swap, 0)

    def test_disk_and_network_rates(self):
        s = self.fs.sampler()
        s.sample()
        self.fs.disk(2048, 4096, reads=10, writes=20, io_ms=500)
        self.fs.net(10000, 4000, lo=5000)
        self.fs.now += 2
        snap = s.sample()
        # sda + nvme0n1 only (partitions and loop devices would count twice)
        self.assertEqual(snap.disk["read_bytes"], 2 * 2048 * 512)
        self.assertEqual(snap.disk["writes"], 40)
        self.assertEqual(snap.disk["read_bytes_ps"], 2048 * 512)
        self.assertEqual(snap.disk["writes_ps"], 20)
        self.assertEqual(snap.net["rx_bytes"], 10000)                     # the loopback isn't network traffic
        self.assertEqual(snap.net["rx_bytes_ps"], 5000)
        self.assertEqual(snap.net["tx_bytes_ps"], 2000)
        self.assertEqual(snap.interfaces["lo"]["rx_bytes"], 5000)
        self.assertEqual(sorted(snap.interfaces), ["eth0", "lo"])
        # per disk: rates, active time (500 ms busy in 2 s), capacity, model, kind
        self.assertEqual(sorted(snap.disks), ["nvme0n1", "sda"])
        sda = snap.disks["sda"]
        self.assertEqual(sda["read_bytes_ps"], 2048 * 512 / 2)
        self.assertAlmostEqual(sda["active"], 25.0)
        self.assertEqual(sda["capacity"], 1953525168 * 512)
        self.assertEqual((sda["kind"], snap.disks["nvme0n1"]["kind"]), ("HDD", "SSD"))
        self.assertEqual(snap.disks["nvme0n1"]["model"], "Fast SSD")

    def test_process_io_rates_and_state(self):
        s = self.fs.sampler()
        s.sample()
        self.fs.process(300, "(my) weird proc", ["/usr/bin/weird"], io=(4096 + 2000, 8192 + 4000))
        with open(os.path.join(self.fs.proc, "300/stat")) as f:
            text = f.read()
        self.fs.write("300/stat", text.replace(") S ", ") T "))
        self.fs.now += 2
        p = s.sample().procs[300]
        self.assertEqual((p.read_ps, p.write_ps), (1000, 2000))
        self.assertEqual(p.status, "Suspended")
        self.assertEqual(s.sample().procs[1].status, "")

    def test_cpu_info_speed_gpu(self):
        s = self.fs.sampler()
        info = s.cpu_info()
        self.assertEqual((info["model"], info["logical"], info["cores"], info["sockets"]), ("Test CPU 9000", 4, 2, 1))
        self.assertEqual(info["base_mhz"], 3500)
        self.assertEqual(s.cpu_speed(), 2500)                             # the cores' average
        self.assertEqual(s.gpus(), {})
        self.fs.write("class/drm/card0/device/gpu_busy_percent", "42\n", self.fs.sys)
        self.assertEqual(s.sample().gpus, {"card0": 42.0})
        self.assertGreater(s.sample().uptime, 0)

    def test_diskstats_guess_without_sys_block(self):
        text = "8 0 sda 1 0 10 0 1 0 20 0\n8 1 sda1 1 0 10 0 1 0 20 0\n7 0 loop0 5 0 50 0 5 0 50 0\n"
        d = procfs.parse_diskstats(text)
        self.assertEqual((d["read_bytes"], d["write_bytes"]), (10 * 512, 20 * 512))

    def test_battery(self):
        self.assertIsNone(self.fs.sampler().sample().battery)
        self.fs.battery()
        b = procfs.battery_info(self.fs.sys)
        self.assertEqual(b["percent"], 80)
        self.assertEqual(b["seconds"], 4 * 3600)                          # 40 Wh at 10 W
        self.assertFalse(b["on_ac"])

    def test_sorting_and_filtering(self):
        procs = list(self.fs.sampler().processes().values())
        self.assertEqual([p.pid for p in procfs.sort_procs(procs, "threads", descending=True)], [400, 300, 1, 2])
        self.assertEqual([p.name for p in procfs.sort_procs(procs, "name")][:2], ["(my) weird proc",
                                                                                   "gnome-text-editor"])
        self.assertEqual([p.pid for p in procs if procfs.matches(p, "WEIRD")], [300])
        self.assertEqual([p.pid for p in procs if procfs.matches(p, "40")], [400])
        self.assertTrue(all(procfs.matches(p, "", os.getuid()) for p in procs))
        self.assertFalse(any(procfs.matches(p, "", os.getuid() + 1) for p in procs))

    def test_formats(self):
        self.assertEqual(procfs.fmt_bytes(512), "512 bytes")
        self.assertEqual(procfs.fmt_bytes(1536), "1.5 KB")
        self.assertEqual(procfs.fmt_bytes(345 * 1024 ** 2), "345.0 MB")
        self.assertEqual(procfs.fmt_bytes(1.5 * 1024 ** 3), "1.50 GB")
        self.assertEqual(procfs.fmt_bytes(-1), "–")
        self.assertEqual(procfs.fmt_cpu_time(12.345), "12.35")
        self.assertEqual(procfs.fmt_cpu_time(187.21), "3:07.21")
        self.assertEqual(procfs.fmt_cpu_time(3723.45), "1:02:03.45")
        self.assertEqual(procfs.fmt_duration(3 * 3600 + 7 * 60), "3:07")


def fake_proc(pid, ppid, exe, uid=None, cpu=0.0, rss=0, read_ps=0.0, write_ps=0.0, user="me"):
    return procfs.Proc(pid=pid, name=exe or f"k{pid}", comm=exe or f"k{pid}", cmdline=exe, exe=exe,
                       uid=1000 if uid is None else uid, user=user, ppid=ppid, threads=1, rss=rss, ticks=0,
                       cpu=cpu, read_ps=read_ps, write_ps=write_ps)


class GpuColumnTest(unittest.TestCase):
    """Processes > GPU (Vini): each process's share of its busiest GPU
    engine, from the kernel's DRM fdinfo (amdgpu, Intel...) or nvidia-smi."""

    FDINFO = ("pos:\t0\nflags:\t02100002\ndrm-driver:\tamdgpu\ndrm-pdev:\t0000:05:00.0\n"
              "drm-client-id:\t42\ndrm-engine-gfx:\t{gfx} ns\ndrm-engine-compute:\t0 ns\n"
              "drm-engine-capacity-gfx:\t1\n")

    def test_parse(self):
        key, eng = procfs.parse_drm_fdinfo(self.FDINFO.format(gfx=123))
        self.assertEqual(key, ("0000:05:00.0", "42"))
        self.assertEqual(eng, {"gfx": 123, "compute": 0})
        self.assertIsNone(procfs.parse_drm_fdinfo("pos: 0\nflags: 1\n"))

    def test_busy_share_per_process(self):
        root = tempfile.mkdtemp()
        proc = os.path.join(root, "proc")
        d = os.path.join(proc, "300")
        os.makedirs(os.path.join(d, "fd"))
        os.makedirs(os.path.join(d, "fdinfo"))
        os.symlink("/dev/dri/renderD128", os.path.join(d, "fd", "7"))
        os.symlink("/dev/dri/renderD128", os.path.join(d, "fd", "8"))       # a dup: the same client
        os.symlink("/home/x/file", os.path.join(d, "fd", "3"))
        info = os.path.join(d, "fdinfo")
        smp = Sampler.__new__(Sampler)
        smp.proc, smp.sys = proc, os.path.join(root, "sys")
        smp._gpu_fds, smp._gpu_scan, smp._prev_gpu = {}, {}, {}
        smp._gpu_cards, smp._pdev_names = {}, {}
        smp.nvidia = procfs.NvidiaUsage(smp.sys)
        mk = lambda: {300: procfs.Proc(pid=300, name="game", comm="game", cmdline="", exe="game", uid=0,  # noqa: E731
                                       user="", ppid=1, threads=1, rss=0, ticks=0, start_ticks=5)}
        for fd in ("7", "8"):
            with open(os.path.join(info, fd), "w") as f:
                f.write(self.FDINFO.format(gfx=1_000_000_000))
        first = mk()
        smp.gpu_usage(first, 0.0, 0.0)                                       # first sample: no rate yet
        self.assertEqual(first[300].gpu_on, "AMD")         # which card (Vini): from the fdinfo's driver
        for fd in ("7", "8"):
            with open(os.path.join(info, fd), "w") as f:
                f.write(self.FDINFO.format(gfx=1_400_000_000))
        procs = mk()
        smp.gpu_usage(procs, 1.0, 1.0)
        self.assertAlmostEqual(procs[300].gpu, 40.0, places=3)               # 0.4 s busy in 1 s, counted once

    def test_nvidia_pmon(self):
        nv = procfs.NvidiaUsage("/nonexistent")
        out = ("# gpu         pid   type     sm    mem    enc    dec    jpg    ofa    command\n"
               "# Idx           #    C/G      %      %      %      %      %      %    name\n"
               "    0       4242     G     57     12      -      -      -      -    game.exe\n"
               "    0       1111     G      -      -      -      -      -      -    Xwayland\n")
        from unittest import mock
        nv.tool = "nvidia-smi"
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout=out)):
            nv._run()
        self.assertEqual(nv.pids, {4242: 57.0})


class ManageTest(unittest.TestCase):
    def test_group_processes(self):
        procs = {p.pid: p for p in (
            fake_proc(1, 0, "systemd", uid=0),
            fake_proc(2, 0, ""),                                   # kernel thread
            fake_proc(10, 1, "firefox"), fake_proc(11, 10, "firefox"), fake_proc(12, 11, "helper"),
            fake_proc(20, 1, "firefox"),                           # a second window process: same app
            fake_proc(30, 1, "syncthing"),                         # the user's daemon: background
            fake_proc(40, 1, "sshd", uid=0),                       # a system account's: system
            fake_proc(50, 1, "gedit", uid=12345))}                  # another user's app: not "Apps"
        apps, background, system_ = manage.group_processes(
            procs, lambda p: {"firefox": "firefox", "gedit": "gedit"}.get(p.exe), 1000)
        self.assertEqual(sorted(apps), ["firefox"])
        self.assertEqual(sorted(apps["firefox"]), [10, 11, 12, 20])       # helpers belong to their app
        self.assertEqual(apps["firefox"][0], 10)
        self.assertEqual(background, [30, 50])
        self.assertEqual(system_, [1, 2, 40])

    def test_per_user(self):
        procs = {1: fake_proc(1, 0, "a", cpu=10, rss=100, user="root", uid=0),
                 2: fake_proc(2, 0, "b", cpu=5, rss=50, user="vini", read_ps=10),
                 3: fake_proc(3, 0, "c", cpu=1, rss=25, user="vini", write_ps=5)}
        u = manage.per_user(procs)
        self.assertEqual(u["vini"]["procs"], 2)
        self.assertEqual(u["vini"]["cpu"], 6)
        self.assertEqual(u["vini"]["mem"], 75)
        self.assertEqual(u["vini"]["disk"], 15)
        self.assertEqual(u["root"]["cpu"], 10)

    def test_history(self):
        snap = procfs.Snapshot(interval=2.0)
        snap.procs = {10: fake_proc(10, 1, "firefox", cpu=50, read_ps=100),
                      11: fake_proc(11, 10, "firefox", cpu=25, write_ps=50)}
        h = manage.History()
        h.add(snap, {"firefox": [10, 11]}, {"firefox": "Firefox"})
        h.add(snap, {"firefox": [10, 11, 99]}, {"firefox": "Firefox"})          # 99 already gone
        a = h.apps["firefox"]
        self.assertEqual(a["name"], "Firefox")
        self.assertAlmostEqual(a["cpu_time"], 2 * (0.75 * 2))                 # 75 % of a core for 2 s, twice
        self.assertAlmostEqual(a["disk"], 2 * 150 * 2)
        again = manage.History(h.to_dict())                                   # persisted and read back
        self.assertEqual(again.apps["firefox"]["cpu_time"], a["cpu_time"])
        self.assertEqual(again.since, h.since)
        again.clear()
        self.assertEqual(again.apps, {})

    def test_startup_entries(self):
        root = tempfile.mkdtemp()
        user, sys1 = os.path.join(root, "user"), os.path.join(root, "sys")
        os.makedirs(user)
        os.makedirs(sys1)

        def desktop(d, fn, name, extra=""):
            with open(os.path.join(d, fn), "w") as f:
                f.write(f"[Desktop Entry]\nType=Application\nName={name}\nExec={name.lower()}\n{extra}"
                        "[Desktop Action x]\nName=Other\n")
        desktop(sys1, "a.desktop", "Alpha")
        desktop(sys1, "b.desktop", "Beta", "X-GNOME-Autostart-enabled=false\n")
        desktop(sys1, "c.desktop", "Gamma")
        desktop(user, "c.desktop", "Gamma Mine", "Hidden=true\n")               # the user's copy wins
        entries = manage.startup_entries(user, [sys1])
        self.assertEqual([(e.name, e.enabled, e.user) for e in entries],
                         [("Alpha", True, False), ("Beta", False, False), ("Gamma Mine", False, True)])
        alpha, beta, gamma = entries
        # disabling a system entry: a user copy with Hidden=true; the system file stays
        path = manage.set_startup_enabled(alpha, False, user)
        self.assertEqual(path, os.path.join(user, "a.desktop"))
        with open(os.path.join(sys1, "a.desktop")) as f:
            self.assertNotIn("Hidden", f.read())
        with open(path) as f:
            text = f.read()
        self.assertIn("Hidden=true", text.split("[Desktop Action x]")[0])
        self.assertIn("[Desktop Action x]", text)
        self.assertFalse(manage.startup_entries(user, [sys1])[0].enabled)
        # enabling removes Hidden (and a false X-GNOME-Autostart-enabled)
        manage.set_startup_enabled(gamma, True, user)
        manage.set_startup_enabled(beta, True, user)
        states = {e.name: e.enabled for e in manage.startup_entries(user, [sys1])}
        self.assertEqual(states, {"Alpha": False, "Beta": True, "Gamma Mine": True})
        shutil.rmtree(root)

    def test_services(self):
        js = ('[{"unit":"ssh.service","load":"loaded","active":"active","sub":"running","description":"SSH"},'
              '{"unit":"dev-sda.device","load":"loaded","active":"active","sub":"plugged","description":"x"},'
              '{"unit":"cups.service","load":"loaded","active":"failed","sub":"failed","description":"CUPS"}]')
        svc = manage.parse_units_json(js, "system")
        self.assertEqual([(s.unit, s.status) for s in svc], [("ssh.service", "Running"), ("cups.service", "Failed")])
        text = ("ssh.service loaded active running OpenBSD Secure Shell server\n"
                "● bad.service not-found inactive dead bad.service\n")
        svc = manage.parse_units_text(text, "user")
        self.assertEqual([(s.unit, s.status, s.description) for s in svc],
                         [("ssh.service", "Running", "OpenBSD Secure Shell server"), ("bad.service", "Stopped",
                                                                                     "bad.service")])
        calls = []

        def run(cmd):
            calls.append(cmd)
            if "--output=json" in cmd:
                return 1, "unknown option"                         # an old systemctl: plain text instead
            if cmd[0] == "pkexec":
                return 0, ""
            if cmd[1:2] == ["restart"]:
                return 1, "Access denied"
            return 0, "ssh.service loaded inactive dead SSH\n"
        listed = manage.list_services("user", run)
        self.assertEqual([s.unit for s in listed], ["ssh.service"])
        self.assertEqual(calls[0][:2], ["systemctl", "--user"])
        ok, _msg = manage.service_action("ssh.service", "restart", "system", run)
        self.assertIn(["systemctl", "restart", "ssh.service"], calls)
        if shutil.which("pkexec"):
            self.assertTrue(ok)
            self.assertEqual(calls[-1], ["pkexec", "systemctl", "restart", "ssh.service"])
        self.assertIsNone(manage.list_services("user", lambda cmd: (1, "no bus")))


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from sonata2 import ui
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.activity")
        cls.app.register(None)

    def setUp(self):
        self.fs = FakeSystem()
        self.fs.process(1, "init", ["/sbin/init"], threads=1, rss_pages=100)
        self.fs.process(10, "busy", ["/usr/bin/busy"], threads=3, rss_pages=300)
        self.fs.process(20, "idle", ["/usr/bin/idle"], threads=2, rss_pages=200)
        self.fs.process(30, "editor", ["/usr/bin/editor"], threads=2, rss_pages=200)
        self.fs.process(31, "editor-helper", ["/usr/bin/editor-helper"], threads=1, rss_pages=50)
        with open(os.path.join(self.fs.proc, "31/stat")) as f:
            self.fs.write("31/stat", f.read().replace(") S 1 ", ") S 30 "))

    def tearDown(self):
        shutil.rmtree(self.fs.root)

    def settle(self, ms=200):
        from gi.repository import GLib
        end = GLib.get_monotonic_time() + ms * 1000
        while GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)

    def window(self):
        from gi.repository import Gio
        from sonata2.activity.window import TaskManagerWindow
        s = self.fs.sampler()
        w = TaskManagerWindow(self.app, sampler=s)            # not shown: the test drives the samples
        w.icons = {"editor": (Gio.ThemedIcon.new("accessories-text-editor"), "Editor")}
        return w, s

    def test_details_sort_select_filter(self):
        from gi.repository import Gtk
        w, s = self.window()
        w.show_page("details")
        page = w.page_by_id["details"]
        w.apply_snapshot(s.sample())

        def names():
            return [page.sorted.get_item(i).sv["name"] for i in range(page.sorted.get_n_items())]
        self.assertEqual(sorted(names()), ["Editor", "busy", "editor-helper", "idle", "init"])   # an app's name
        before = {r.sv["pid"]: r for r in w.rows.values()}
        self.fs.process(20, "idle", ["/usr/bin/idle"], ticks=(CLK_TCK, 0), threads=2)
        self.fs.now += 2
        page.selection.set_selected(names().index("busy"))
        w.apply_snapshot(s.sample())
        self.assertEqual(names()[0], "idle")                                # CPU, highest first
        self.assertIs(w.pid_rows[20], before[20])                          # rows updated in place
        self.assertAlmostEqual(w.pid_rows[20].cpu, 50.0)
        self.assertEqual(page.selected_rows()[0].sv["name"], "busy")        # selection kept across the refresh
        page.view.sort_by_column(page.cols["name"], Gtk.SortType.ASCENDING)
        self.assertEqual(names(), ["busy", "Editor", "editor-helper", "idle", "init"])
        w.search.set_text("ini")
        self.settle(400)                                                   # the search field waits a moment
        self.assertEqual(names(), ["init"])
        w.search.set_text("")
        self.settle(400)
        self.assertEqual(page.selected_rows()[0].sv["name"], "busy")        # back when the filter lets it
        self.fs.kill(10)
        self.fs.now += 2
        w.apply_snapshot(s.sample())
        self.assertNotIn("busy", names())
        self.assertEqual(page.selected_rows(), [])
        self.assertTrue(w.descends(w.pid_rows[31], w.pid_rows[30]))
        self.assertFalse(w.descends(w.pid_rows[30], w.pid_rows[31]))
        w.destroy()

    def test_processes_page_groups(self):
        w, s = self.window()
        w.show_page("processes")
        page = w.page_by_id["processes"]
        w.apply_snapshot(s.sample())
        self.fs.process(31, "editor-helper", ["/usr/bin/editor-helper"], ticks=(CLK_TCK, 0), rss_pages=50)
        with open(os.path.join(self.fs.proc, "31/stat")) as f:
            self.fs.write("31/stat", f.read().replace(") S 1 ", ") S 30 "))
        self.fs.now += 2
        w.apply_snapshot(s.sample())
        self.assertEqual(page.counts, [1, 3, 0])                           # the fake tree's processes are ours
        first = page.sorted.get_item(0)
        node = first.get_item()
        self.assertEqual(node.sv["name"], "Editor (2)")                     # the app, its processes counted
        self.assertAlmostEqual(node.cpu, 50.0)                              # its processes' total
        self.assertEqual(node.mem, 250 * procfs.PAGE_SIZE)
        first.set_expanded(True)
        kids = [page.sorted.get_item(i).get_item().sv["name"] for i in (1, 2)]
        self.assertEqual(kids, ["editor-helper", "Editor"])                 # sorted by CPU inside the app
        self.assertEqual(page.sorted.get_section(0)[1], 3)                 # Apps section: the app + 2 children
        page.selection.set_selected(0)
        self.assertEqual(sorted(r.sv["pid"] for r in page.selected_rows()), [30, 31])   # End task ends them all
        w.search.set_text("helper")
        self.settle(400)
        self.assertEqual(page.sorted.get_item(0).get_item().sv["name"], "Editor (2)")
        self.assertEqual(page.sorted.get_n_items(), 2)                    # the app and its matching process
        w.search.set_text("")
        w.destroy()

    def test_users_history_performance(self):
        self.fs.net(100, 50)
        w, s = self.window()
        w.apply_snapshot(s.sample())
        self.fs.process(30, "editor", ["/usr/bin/editor"], ticks=(CLK_TCK, 0), rss_pages=200)
        self.fs.now += 2
        w.show_page("users")
        w.apply_snapshot(s.sample())
        users = w.page_by_id["users"]
        self.assertEqual(users.store.get_n_items(), 1)
        self.assertEqual(users.store.get_item(0).procs, 5)
        w.show_page("history")
        hist = w.page_by_id["history"]
        self.assertEqual(hist.store.get_item(0).sv["name"], "Editor")
        self.assertAlmostEqual(w.history.apps["editor"]["cpu_time"], 1.0)
        hist.clear()
        self.assertEqual(hist.store.get_n_items(), 0)
        w.show_page("performance")
        perf = w.page_by_id["performance"]
        self.assertEqual(list(perf.resources)[:4], ["cpu", "memory", "disk:nvme0n1", "disk:sda"])
        self.assertIn("net:eth0", perf.resources)
        self.assertNotIn("net:lo", perf.resources)
        perf.list.select_row(perf.rows["memory"])
        self.assertEqual(perf.current, "memory")
        self.assertEqual(perf.stat_labels["In use"].get_label(), "1.9 GB")
        self.assertEqual(len(perf.resources["cpu"].a.values), 1)          # the first sample has no rates
        self.fs.now += 2
        w.apply_snapshot(s.sample())
        self.assertEqual(len(perf.resources["cpu"].a.values), 2)
        w.destroy()

    def test_end_task(self):
        import subprocess
        import time
        from sonata2.activity.window import TaskManagerWindow
        child = subprocess.Popen(["sleep", "30"])
        w = TaskManagerWindow(self.app)
        w.icons = {}
        w.apply_snapshot(w.sampler.sample())
        row = w.pid_rows[child.pid]
        w.end_task([row])
        for _ in range(50):
            if child.poll() is not None:
                break
            time.sleep(0.05)
        self.assertEqual(child.returncode, -15)                             # SIGTERM
        w.destroy()

    def test_window_builds_and_samples(self):
        from sonata2.activity.window import TaskManagerWindow, activity_desktop_file  # noqa: F401
        w = TaskManagerWindow(self.app, sampler=self.fs.sampler())
        w.present()
        self.settle(1500)                                                 # icon index + first samples
        self.assertEqual(w.get_title(), "Task Manager")
        self.assertIn(30, w.pid_rows)
        self.assertTrue(w._timer)
        for p in w.pages:                                                  # every page builds and shows
            w.show_page(p.id)
            self.settle(50)
            self.assertEqual(w.title_lbl.get_label(), p.title)
        self.settle(300)
        w.set_visible(False)                                               # hidden: sampling pauses
        self.settle(100)
        self.assertFalse(w._timer)
        w.destroy()

    def test_gpu_engine_column(self):
        """The GPU Engine column (Vini: mind the column bugs we had): only the
        last column fills, it sorts, and an app lists its processes' cards."""
        from gi.repository import Gtk
        w, s = self.window()
        w.show_page("processes")
        page = w.page_by_id["processes"]
        cols = page.view.get_columns()
        shown = [cols.get_item(i) for i in range(cols.get_n_items()) if cols.get_item(i).get_visible()]
        self.assertEqual(shown[-1].get_title(), "GPU Engine")
        self.assertEqual([c.get_expand() for c in shown], [False] * (len(shown) - 1) + [True])
        self.assertEqual(page.cols["gpu_on"].cid, "gpu_on")
        snap = s.sample()
        for p in snap.procs.values():
            if p.pid == 30:
                p.gpu_on = "NVIDIA"
            if p.pid == 31:
                p.gpu_on = "AMD"
        w.apply_snapshot(snap)
        self.settle()
        node = page.nodes.get("editor")
        self.assertIsNotNone(node)
        self.assertEqual(node.sv["gpu_on"], "AMD, NVIDIA")               # the app: all its processes' cards
        page.view.sort_by_column(page.cols["gpu_on"], Gtk.SortType.ASCENDING)       # strings sort too
        self.settle()


if __name__ == "__main__":
    unittest.main()
