"""Task Manager's data: processes and system counters read straight from
/proc and /sys (no psutil). Pure Python, no GTK: the window samples it
off the main loop (backend.system.run_async).

    s = Sampler()                 # Sampler(proc="/tmp/fake/proc", sys="/tmp/fake/sys") in tests
    snap = s.sample()             # snap.procs, .cpu, .memory, .disks, .interfaces, .gpus, .battery
    snap.procs[pid].cpu           # % of one core since the previous sample
    snap.cpu                      # {"user": %, "system": %, "idle": %}
    s.cpu_info()                  # model, cores, logical processors, base speed (read once)

Rates (CPU %, disk and network per second) need two samples: the first
sample reports them as 0."""
import glob
import os
import pwd
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

CLK_TCK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
SECTOR = 512                                   # /proc/diskstats counts 512-byte sectors


def _read(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


# -- parsing (one file each; testable on strings) ---------------------------------------------
def parse_cpu_line(line: str) -> Dict[str, int]:
    """The "cpu" line of /proc/stat -> jiffies by kind."""
    v = [int(x) for x in line.split()[1:]] + [0] * 10
    user, nice, system, idle, iowait, irq, softirq, steal = v[:8]
    return {"user": user + nice, "system": system + irq + softirq + steal, "idle": idle + iowait,
            "total": user + nice + system + idle + iowait + irq + softirq + steal}


def cpu_percent(prev: Dict[str, int], cur: Dict[str, int]) -> Dict[str, float]:
    """System / User / Idle % of all CPUs between two parse_cpu_line() results."""
    total = cur["total"] - prev["total"]
    if total <= 0:
        return {"user": 0.0, "system": 0.0, "idle": 100.0}
    return {k: 100.0 * max(0, cur[k] - prev[k]) / total for k in ("user", "system", "idle")}


def parse_meminfo(text: str) -> Dict[str, int]:
    """/proc/meminfo -> bytes by key (MemTotal, MemAvailable, ...)."""
    out = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
    return out


def memory_summary(mi: Dict[str, int]) -> Dict[str, float]:
    """macOS's Memory tab figures from /proc/meminfo (bytes; pressure 0..1)."""
    total = mi.get("MemTotal", 0)
    avail = mi.get("MemAvailable", mi.get("MemFree", 0) + mi.get("Cached", 0))
    cached = mi.get("Cached", 0) + mi.get("Buffers", 0)
    swap = max(0, mi.get("SwapTotal", 0) - mi.get("SwapFree", 0))
    wired = mi.get("KernelStack", 0) + mi.get("PageTables", 0) + mi.get("SUnreclaim", 0)
    used = max(0, total - avail)
    # macOS pressure: how hard the system works to find free memory. Linux
    # has no single figure; what is not available, raised when swap is busy.
    pressure = used / total if total else 0.0
    if mi.get("SwapTotal", 0):
        pressure = max(pressure, min(1.0, swap / mi["SwapTotal"]))
    return {"total": total, "used": used, "cached": cached, "swap": swap, "swap_total": mi.get("SwapTotal", 0),
            "app": mi.get("AnonPages", 0), "wired": wired, "compressed": mi.get("Zswap", 0),
            "available": avail, "pressure": min(1.0, pressure), "free": mi.get("MemFree", 0),
            "modified": mi.get("Dirty", 0) + mi.get("Writeback", 0),
            "committed": mi.get("Committed_AS", 0), "commit_limit": mi.get("CommitLimit", 0),
            "paged_pool": mi.get("SReclaimable", 0), "non_paged_pool": mi.get("SUnreclaim", 0)}


def parse_stat(text: str) -> Optional[dict]:
    """/proc/<pid>/stat -> the fields Activity Monitor needs. The name sits in
    parentheses and may hold spaces or ')' itself: split at the last ')'."""
    a, b = text.find("("), text.rfind(")")
    if a < 0 or b < 0:
        return None
    rest = text[b + 2:].split()
    if len(rest) < 22:
        return None
    return {"comm": text[a + 1:b], "state": rest[0], "ppid": int(rest[1]),
            "ticks": int(rest[11]) + int(rest[12]), "threads": int(rest[17]),
            "start": int(rest[19]), "rss": int(rest[21]) * PAGE_SIZE}


def parse_io(text: str) -> Optional[tuple]:
    """/proc/<pid>/io -> (bytes read, bytes written) from storage."""
    vals = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        vals[k.strip()] = v.strip()
    try:
        return int(vals["read_bytes"]), int(vals["write_bytes"])
    except (KeyError, ValueError):
        return None


def parse_status_swap(text: str) -> int:
    for line in text.splitlines():
        if line.startswith("VmSwap:"):
            parts = line.split()
            return int(parts[1]) * 1024 if len(parts) > 1 and parts[1].isdigit() else 0
    return 0


def parse_net_dev(text: str) -> Dict[str, Dict[str, int]]:
    """/proc/net/dev -> {iface: {rx_bytes, rx_packets, tx_bytes, tx_packets}}."""
    out = {}
    for line in text.splitlines()[2:]:
        name, _, rest = line.partition(":")
        v = rest.split()
        if len(v) < 10:
            continue
        out[name.strip()] = {"rx_bytes": int(v[0]), "rx_packets": int(v[1]),
                             "tx_bytes": int(v[8]), "tx_packets": int(v[9])}
    return out


def _whole_disks(rows, whole):
    names = {r[2] for r in rows}
    for r in rows:
        name = r[2]
        if name.startswith(("loop", "ram", "zram", "dm-", "md", "sr")):
            continue
        if whole is not None:
            if name not in whole:
                continue
        elif any(name != n and name.startswith(n) for n in names):     # sda1 of sda, nvme0n1p1 of nvme0n1
            continue
        yield r


def parse_disks(text: str, whole=None) -> Dict[str, Dict[str, int]]:
    """/proc/diskstats per whole disk (partitions, loop and ram devices
    would count the same I/O twice): reads, writes (operations), read_bytes,
    write_bytes, io_ms (time spent doing I/O: the disk's active time).
    whole: the names of /sys/block (None: guess)."""
    rows = [line.split() for line in text.splitlines()]
    rows = [r for r in rows if len(r) >= 10]
    return {r[2]: {"reads": int(r[3]), "read_bytes": int(r[5]) * SECTOR, "writes": int(r[7]),
                   "write_bytes": int(r[9]) * SECTOR, "io_ms": int(r[12]) if len(r) > 12 else 0}
            for r in _whole_disks(rows, whole)}


def parse_diskstats(text: str, whole=None) -> Dict[str, int]:
    """/proc/diskstats summed over the whole disks (see parse_disks)."""
    tot = {"reads": 0, "writes": 0, "read_bytes": 0, "write_bytes": 0}
    for d in parse_disks(text, whole).values():
        for k in tot:
            tot[k] += d[k]
    return tot


def parse_cpuinfo(text: str) -> dict:
    """/proc/cpuinfo -> model, logical processors, cores, sockets, MHz (now)."""
    model, logical, cores, sockets, mhz = "", 0, set(), set(), []
    phys = core = None
    for line in text.splitlines() + [""]:
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip()
        if k == "processor":
            logical += 1
        elif k in ("model name", "Model", "Hardware") and not model:
            model = v
        elif k == "physical id":
            phys = v
            sockets.add(v)
        elif k == "core id":
            core = v
        elif k == "cpu MHz":
            try:
                mhz.append(float(v))
            except ValueError:
                pass
        elif not line.strip():
            if core is not None:
                cores.add((phys, core))
            phys = core = None
    return {"model": model or "Processor", "logical": logical or 1, "cores": len(cores) or logical or 1,
            "sockets": len(sockets) or 1, "mhz": sum(mhz) / len(mhz) if mhz else 0.0}


def battery_info(sys_root: str = "/sys") -> Optional[dict]:
    """First battery of /sys/class/power_supply: {percent, status, seconds
    (remaining: to empty when discharging, to full when charging; None if
    unknown), on_ac}; None without a battery."""
    base = os.path.join(sys_root, "class", "power_supply")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    bats = [n for n in names if _read(os.path.join(base, n, "type")).strip() == "Battery" or n.startswith("BAT")]
    if not bats:
        return None
    b = os.path.join(base, bats[0])

    def num(name):
        t = _read(os.path.join(b, name)).strip()
        return int(t) if t.lstrip("-").isdigit() else None
    status = _read(os.path.join(b, "status")).strip() or "Unknown"
    pct = num("capacity")
    now, full, rate = num("energy_now"), num("energy_full"), num("power_now")
    if now is None:
        now, full, rate = num("charge_now"), num("charge_full"), num("current_now")
    if pct is None and now is not None and full:
        pct = round(100 * now / full)
    seconds = None
    if rate:
        rate = abs(rate)
        if status == "Discharging" and now is not None:
            seconds = int(3600 * now / rate)
        elif status == "Charging" and now is not None and full:
            seconds = int(3600 * max(0, full - now) / rate)
    on_ac = False
    for n in names:
        kind = _read(os.path.join(base, n, "type")).strip()
        if kind in ("Mains", "USB") and _read(os.path.join(base, n, "online")).strip() == "1":
            on_ac = True
    return {"percent": pct, "status": status, "seconds": seconds, "on_ac": on_ac or status in ("Charging", "Full")}


# -- sampling ---------------------------------------------------------------------------------
STATES = {"T": "Suspended", "t": "Suspended", "Z": "Zombie", "D": "Waiting for disk"}


def steam_appid(d: str) -> str:
    """The Steam game a process belongs to: Steam starts every game process
    (Proton, wine, the game) with SteamAppId set. Read once per process."""
    try:
        with open(os.path.join(d, "environ"), "rb") as f:
            env = f.read()
    except OSError:
        return ""
    for entry in env.split(b"\0"):
        if entry.startswith((b"SteamAppId=", b"SteamGameId=")):
            val = entry.split(b"=", 1)[1].decode(errors="replace")
            if val.isdigit() and val != "0":
                return val
    return ""


@dataclass
class Proc:
    pid: int
    name: str
    comm: str
    cmdline: str
    exe: str                      # argv[0]'s base name ("" for kernel threads)
    uid: int
    user: str
    ppid: int
    threads: int
    rss: int
    ticks: int
    cpu: float = 0.0              # % of one core since the previous sample
    cpu_time: float = 0.0         # seconds
    started: float = 0.0          # epoch seconds
    swap: int = 0
    read_bytes: int = -1          # -1: not readable (another user's process)
    write_bytes: int = -1
    read_ps: float = 0.0          # bytes/s since the previous sample
    write_ps: float = 0.0
    avg_cpu: float = 0.0          # % of one core over the process's life
    start_ticks: int = 0          # clock ticks after boot (with pid: the process's identity)
    state: str = "S"
    gpu: float = 0.0              # % of its busiest GPU engine since the previous sample
    steam: str = ""               # the Steam game it belongs to (SteamAppId), "" for others
    gpu_on: str = ""              # the graphics card(s) it uses: "NVIDIA", "AMD", "AMD, NVIDIA"; "" none

    @property
    def status(self) -> str:
        return STATES.get(self.state, "")


@dataclass
class Snapshot:
    procs: Dict[int, Proc] = field(default_factory=dict)
    cpu: Dict[str, float] = field(default_factory=lambda: {"user": 0.0, "system": 0.0, "idle": 100.0})
    threads: int = 0
    memory: Dict[str, float] = field(default_factory=dict)
    disk: Dict[str, float] = field(default_factory=dict)       # totals + *_ps rates
    disks: Dict[str, Dict[str, float]] = field(default_factory=dict)   # per disk + *_ps, active %, capacity, model
    net: Dict[str, float] = field(default_factory=dict)
    interfaces: Dict[str, Dict[str, float]] = field(default_factory=dict)
    gpus: Dict[str, Optional[float]] = field(default_factory=dict)   # card -> busy % (None: no reading)
    gpu_info: Dict[str, dict] = field(default_factory=dict)   # card -> {maker, asleep, vram}
    battery: Optional[dict] = None
    cpu_mhz: float = 0.0
    uptime: float = 0.0
    interval: float = 0.0


# -- GPU use per process ---------------------------------------------------------------------------
# The kernel's DRM fdinfo (amdgpu, i915, xe, nouveau, msm...): every open
# /dev/dri/* file of a process lists its client and the nanoseconds each
# engine was busy for it. NVIDIA's own driver doesn't fill that in: its
# numbers come from `nvidia-smi pmon` (NvidiaUsage), only while the card is
# awake anyway -- asking would wake a sleeping laptop GPU.
FD_RESCAN_S = 5.0           # a process's fd list is checked this often...
FD_FULL_S = 60.0            # ...its links resolved only when that list changed, or this often (reused fd numbers)
CARD_NAMES = {"amdgpu": "AMD", "radeon": "AMD", "i915": "Intel", "xe": "Intel", "nvidia": "NVIDIA",
              "nvidia-drm": "NVIDIA", "nouveau": "NVIDIA"}      # driver -> the card's maker (Task Manager)                 # which fds of a process are GPU files: looked up again this often


def parse_drm_fdinfo(text: str) -> Optional[tuple]:
    """(client key, {engine: busy ns}) of one DRM fdinfo, None if it isn't one."""
    pdev = client = None
    engines = {}
    for line in text.splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if k == "drm-pdev":
            pdev = v
        elif k == "drm-client-id":
            client = v
        elif k.startswith("drm-engine-") and not k.startswith("drm-engine-capacity"):
            num = v.split()[0] if v else ""
            if num.isdigit():
                engines[k[11:]] = int(num)
    if client is None or not engines:
        return None
    return (pdev or "", client), engines


class NvidiaUsage:
    """pid -> SM % from `nvidia-smi pmon` in the background, only while the
    NVIDIA card is already awake (runtime PM "active")."""

    def __init__(self, sys: str = "/sys"):
        import shutil
        self.sys = sys
        self.pids = {}
        self._busy = False
        self.tool = shutil.which("nvidia-smi")
        self.devices = []
        for card in sorted(glob.glob(os.path.join(sys, "class/drm/card[0-9]*"))):
            drv = os.path.basename(os.path.realpath(os.path.join(card, "device", "driver")))
            if drv == "nvidia":
                self.devices.append(os.path.realpath(os.path.join(card, "device")))

    def awake(self) -> bool:
        return any(_read(os.path.join(d, "power", "runtime_status")).strip() in ("active", "")
                   for d in self.devices)

    def refresh(self) -> None:
        if not self.tool or not self.devices or self._busy:
            return
        if not self.awake():
            self.pids = {}
            return
        self._busy = True
        import threading
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        import subprocess
        out = {}
        try:
            text = subprocess.run([self.tool, "pmon", "-c", "1", "-s", "u"], capture_output=True, text=True,
                                  timeout=5).stdout
            for line in text.splitlines():
                f = line.split()
                if len(f) > 3 and not line.startswith("#") and f[1].isdigit() and f[3].replace(".", "").isdigit():
                    out[int(f[1])] = max(out.get(int(f[1]), 0.0), float(f[3]))
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        self.pids = out
        self._busy = False


def _rates(cur: dict, prev: Optional[dict], dt: float, keys) -> dict:
    out = dict(cur)
    for k in keys:
        out[k + "_ps"] = max(0.0, (cur[k] - prev[k]) / dt) if prev and dt > 0 else 0.0
    return out


class Sampler:
    """Reads everything Task Manager shows; keeps the previous sample for
    rates. One sample at a time (the window never overlaps them)."""

    def __init__(self, proc: str = "/proc", sys: str = "/sys", clock=time.monotonic):
        self.proc, self.sys, self.clock = proc, sys, clock
        self._static = {}                  # (pid, start) -> (name, comm, cmdline, exe, uid, user)
        self._users = {}
        self._prev_ticks = {}              # (pid, start) -> ticks
        self._prev_io = {}                 # (pid, start) -> (read, written)
        self._prev_cpu = None
        self._prev_disks = {}
        self._prev_net = None
        self._prev_t = None
        self._stats = None                 # backend.stats.Sampler, made on the first NVIDIA card
        self.gpu_info = {}                 # card -> {maker, asleep, vram} (gpus())
        self._disk_static = {}             # name -> (capacity, model)
        self._cpu_info = None
        self._prev_disk_tot = None
        self._gpu_fds = {}                 # (pid, start) -> [fdinfo paths of its /dev/dri files]
        self._gpu_scan = {}                # (pid, start) -> (listed at, resolved at, hash of its fd list)
        self._gpu_cards = {}               # (pid, start) -> {card names} its open GPU files belong to
        self._pdev_names = {}              # PCI address -> "AMD" / "NVIDIA" / "Intel"
        self._prev_gpu = {}                # DRM client -> {engine: busy ns}
        self.nvidia = NvidiaUsage(sys)
        self.boot_time = self._boot_time()

    def _boot_time(self) -> float:
        for line in _read(os.path.join(self.proc, "stat")).splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
        return time.time() - self._uptime()

    def _uptime(self) -> float:
        try:
            return float(_read(os.path.join(self.proc, "uptime")).split()[0])
        except (IndexError, ValueError):
            return 0.0

    def user_name(self, uid: int) -> str:
        if uid not in self._users:
            try:
                self._users[uid] = pwd.getpwuid(uid).pw_name
            except (KeyError, OverflowError):
                self._users[uid] = str(uid)
        return self._users[uid]

    def cpu_info(self) -> dict:
        """Model, cores, logical processors, sockets, base speed (MHz); read once."""
        if self._cpu_info is None:
            info = parse_cpuinfo(_read(os.path.join(self.proc, "cpuinfo")))
            top = _read(os.path.join(self.sys, "devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq")).strip()
            info["base_mhz"] = int(top) / 1000 if top.isdigit() else info["mhz"]
            self._cpu_info = info
        return self._cpu_info

    def cpu_speed(self) -> float:
        """Current speed (MHz): the average of the cores' scaling_cur_freq,
        else /proc/cpuinfo's "cpu MHz"."""
        base = os.path.join(self.sys, "devices/system/cpu")
        freqs = []
        try:
            names = os.listdir(base)
        except OSError:
            names = []
        for n in names:
            if n.startswith("cpu") and n[3:].isdigit():
                v = _read(os.path.join(base, n, "cpufreq/scaling_cur_freq")).strip()
                if v.isdigit():
                    freqs.append(int(v) / 1000)
        if freqs:
            return sum(freqs) / len(freqs)
        return parse_cpuinfo(_read(os.path.join(self.proc, "cpuinfo")))["mhz"]

    def gpus(self) -> Dict[str, Optional[float]]:
        """card -> busy % (None: no reading right now) for every graphics card.
        Vini: "only one GPU shows" on his Radeon 680M + NVIDIA laptop -- the
        NVIDIA driver has no gpu_busy_percent, so reading only that file hid
        the dGPU. NVIDIA's load comes from the menu bar's stats reader
        (nvidia-smi, run only while the card is awake: asking would wake it).
        Fills self.gpu_info {card: {"maker", "asleep", "vram"}} on the way."""
        base = os.path.join(self.sys, "class/drm")
        out, info = {}, {}
        self.gpu_info = info
        try:
            names = sorted(os.listdir(base))
        except OSError:
            return out
        nv = None                                          # {cardN: (busy, vram)} from the stats reader
        for n in names:
            if not (n.startswith("card") and n[4:].isdigit()):
                continue                                   # a connector (card1-HDMI-A-1)
            dev = os.path.join(base, n, "device")
            maker = CARD_NAMES.get(os.path.basename(os.path.realpath(os.path.join(dev, "driver"))))
            v = _read(os.path.join(dev, "gpu_busy_percent")).strip()
            busy = float(v) if v.isdigit() else None
            if busy is None and not maker:
                continue                                   # not a GPU we can say anything about
            asleep = _read(os.path.join(dev, "power/runtime_status")).strip() == "suspended"
            vram = None
            if maker == "NVIDIA":
                if nv is None:
                    nv = self._nvidia_cards()
                busy, vram = nv.get(n, (None, None))
            else:
                used = _read(os.path.join(dev, "mem_info_vram_used")).strip()
                total = _read(os.path.join(dev, "mem_info_vram_total")).strip()
                if used.isdigit() and total.isdigit() and int(total):
                    vram = (int(used), int(total))
            out[n] = busy
            info[n] = {"maker": maker or "", "asleep": asleep, "vram": vram}
        return out

    def _nvidia_cards(self) -> dict:
        """{cardN: (busy %, (used, total) VRAM)} of the NVIDIA cards, through
        the menu bar's reader (backend.stats) so nvidia-smi's awake check and
        its cached run are shared, not duplicated; (None, None) while asleep."""
        if self._stats is None:
            from ..backend import stats                    # imports this module: not at the top
            self._stats = stats.Sampler(proc=self.proc, sys=self.sys)
        try:
            busy, vram = self._stats.gpus(), self._stats.vrams()
            cards = self._stats._cards or []
        except Exception:
            return {}
        return {card: (busy.get(key), vram.get(key)) for key, maker, card in cards if maker == "NVIDIA"}

    def _drm_fdinfos(self, key, d: str, now: float) -> list:
        listed, resolved, old_hash = self._gpu_scan.get(key, (-1e9, -1e9, None))
        if now - listed >= FD_RESCAN_S:
            try:
                fds = os.listdir(os.path.join(d, "fd"))
            except OSError:
                fds = []                                   # another user's process
            fd_hash = hash(tuple(sorted(fds)))
            if fd_hash == old_hash and now - resolved < FD_FULL_S:
                # Same fds as last time: no readlink of every fd of every process each pass.
                self._gpu_scan[key] = (now, resolved, fd_hash)
                return self._gpu_fds.get(key, [])
            paths = []
            cards = set()
            for fd in fds:
                try:
                    target = os.readlink(os.path.join(d, "fd", fd))
                except OSError:
                    continue
                if target.startswith("/dev/dri/"):
                    paths.append(os.path.join(d, "fdinfo", fd))
                elif target.startswith("/dev/nvidia") and target[11:].isdigit():   # NVIDIA's own driver
                    cards.add("NVIDIA")
            self._gpu_fds[key], self._gpu_scan[key], self._gpu_cards[key] = paths, (now, now, fd_hash), cards
        return self._gpu_fds.get(key, [])

    def _card_name(self, pdev: str) -> str:
        """A GPU's maker from its PCI address (the driver bound to it)."""
        if pdev not in self._pdev_names:
            link = os.path.join(self.sys, "bus/pci/devices", pdev, "driver")
            drv = os.path.basename(os.path.realpath(link)) if pdev and os.path.exists(link) else ""
            self._pdev_names[pdev] = CARD_NAMES.get(drv, drv)
        return self._pdev_names[pdev]

    def gpu_usage(self, procs: Dict[int, "Proc"], dt: float, now: float) -> None:
        """Fills each process's .gpu (see the GPU section above)."""
        clients, owner = {}, {}
        for p in procs.values():
            key = (p.pid, p.start_ticks)
            cards = set(self._gpu_cards.get(key, ()))
            for path in self._drm_fdinfos(key, os.path.join(self.proc, str(p.pid)), now):
                text = _read(path)
                parsed = parse_drm_fdinfo(text)
                drv = next((ln.split(":", 1)[1].strip() for ln in text.splitlines() if ln.startswith("drm-driver:")),
                           "")
                if drv:
                    cards.add(CARD_NAMES.get(drv, drv))
                elif parsed and parsed[0][0]:
                    cards.add(self._card_name(parsed[0][0]))
                if parsed and parsed[0] not in clients:    # dup'ed fds share one client
                    clients[parsed[0]] = parsed[1]
                    owner[parsed[0]] = p
            p.gpu_on = ", ".join(sorted(cards - {""}))
        for client, engines in clients.items():
            prev = self._prev_gpu.get(client)
            if prev is None or dt <= 0:
                continue
            busy = max((100.0 * (ns - prev.get(e, ns)) / (dt * 1e9) for e, ns in engines.items()), default=0.0)
            p = owner[client]
            p.gpu = min(100.0, max(p.gpu, busy))
        self._prev_gpu = clients
        live = {(p.pid, p.start_ticks) for p in procs.values()}
        self._gpu_fds = {k: v for k, v in self._gpu_fds.items() if k in live}
        self._gpu_scan = {k: v for k, v in self._gpu_scan.items() if k in live}
        self._gpu_cards = {k: v for k, v in self._gpu_cards.items() if k in live}
        self.nvidia.refresh()
        for pid, pct in self.nvidia.pids.items():
            if pid in procs:
                procs[pid].gpu = max(procs[pid].gpu, pct)
                if "NVIDIA" not in procs[pid].gpu_on:
                    procs[pid].gpu_on = ", ".join(sorted(set(filter(None, procs[pid].gpu_on.split(", "))) | {"NVIDIA"}))

    def _disk_info(self, name: str) -> tuple:
        if name not in self._disk_static:
            size = _read(os.path.join(self.sys, "block", name, "size")).strip()
            model = _read(os.path.join(self.sys, "block", name, "device/model")).strip()
            rot = _read(os.path.join(self.sys, "block", name, "queue/rotational")).strip()
            kind = "HDD" if rot == "1" else "SSD"
            self._disk_static[name] = (int(size) * SECTOR if size.isdigit() else 0, model, kind)
        return self._disk_static[name]

    def _static_info(self, pid: int, d: str, st: dict) -> tuple:
        key = (pid, st["start"])
        info = self._static.get(key)
        if info is None:
            raw = _read(os.path.join(d, "cmdline"))
            argv = [a for a in raw.split("\0") if a]
            exe = os.path.basename(argv[0]) if argv else ""
            cmdline = " ".join(argv) if argv else "[" + st["comm"] + "]"
            try:
                uid = os.stat(d).st_uid
            except OSError:
                uid = -1
            comm = st["comm"]
            # comm is cut at 15 characters: the program's own name reads better
            name = exe if exe and len(comm) >= 15 and exe.startswith(comm[:15]) else comm
            info = (name, comm, cmdline[:4096], exe, uid, self.user_name(uid) if uid >= 0 else "",
                    steam_appid(d))
            self._static[key] = info
        return info

    def processes(self, want=("io",)) -> Dict[int, Proc]:
        """Every process now (rates not computed yet; see sample). want:
        "io" reads /proc/<pid>/io, "swap" VmSwap from status."""
        procs = {}
        try:
            entries = os.listdir(self.proc)
        except OSError:
            return procs
        for e in entries:
            if not e.isdigit():
                continue
            pid = int(e)
            d = os.path.join(self.proc, e)
            st = parse_stat(_read(os.path.join(d, "stat")))
            if st is None:
                continue                                   # gone meanwhile
            name, comm, cmdline, exe, uid, user, steam = self._static_info(pid, d, st)
            p = Proc(pid=pid, name=name, comm=comm, cmdline=cmdline, exe=exe, uid=uid, user=user, ppid=st["ppid"],
                     steam=steam,
                     threads=st["threads"], rss=st["rss"], ticks=st["ticks"], cpu_time=st["ticks"] / CLK_TCK,
                     start_ticks=st["start"], started=self.boot_time + st["start"] / CLK_TCK, state=st["state"])
            if "io" in want:
                io = parse_io(_read(os.path.join(d, "io")))
                if io:
                    p.read_bytes, p.write_bytes = io
            if "swap" in want:
                p.swap = parse_status_swap(_read(os.path.join(d, "status")))
            procs[pid] = p
        return procs

    def sample(self, want=("io",)) -> Snapshot:
        now = self.clock()
        dt = (now - self._prev_t) if self._prev_t is not None else 0.0
        snap = Snapshot(interval=dt)
        procs = self.processes(want)
        uptime = snap.uptime = self._uptime()
        ticks, ios = {}, {}
        for p in procs.values():
            key = (p.pid, p.start_ticks)
            ticks[key] = p.ticks
            prev = self._prev_ticks.get(key)
            if prev is not None and dt > 0:
                p.cpu = max(0.0, 100.0 * (p.ticks - prev) / CLK_TCK / dt)
            if p.read_bytes >= 0:
                ios[key] = (p.read_bytes, p.write_bytes)
                pio = self._prev_io.get(key)
                if pio is not None and dt > 0:
                    p.read_ps = max(0.0, (p.read_bytes - pio[0]) / dt)
                    p.write_ps = max(0.0, (p.write_bytes - pio[1]) / dt)
            age = uptime - p.start_ticks / CLK_TCK
            p.avg_cpu = 100.0 * p.cpu_time / age if age > 1 else 0.0
            snap.threads += p.threads
        self._prev_ticks, self._prev_io = ticks, ios
        # static info of dead processes goes with them
        if len(self._static) > 2 * len(ticks) + 64:
            self._static = {k: v for k, v in self._static.items() if k in ticks}
        snap.procs = procs

        lines = _read(os.path.join(self.proc, "stat")).splitlines()
        cpu = parse_cpu_line(lines[0]) if lines and lines[0].startswith("cpu ") else None
        if cpu and self._prev_cpu:
            snap.cpu = cpu_percent(self._prev_cpu, cpu)
        self._prev_cpu = cpu or self._prev_cpu
        snap.cpu_mhz = self.cpu_speed()

        snap.memory = memory_summary(parse_meminfo(_read(os.path.join(self.proc, "meminfo"))))

        try:
            whole = set(os.listdir(os.path.join(self.sys, "block")))
        except OSError:
            whole = None
        disks = parse_disks(_read(os.path.join(self.proc, "diskstats")), whole)
        tot = {"reads": 0, "writes": 0, "read_bytes": 0, "write_bytes": 0}
        for name, v in disks.items():
            prev = self._prev_disks.get(name)
            row = _rates(v, prev, dt, ("reads", "writes", "read_bytes", "write_bytes", "io_ms"))
            row["active"] = min(100.0, row["io_ms_ps"] / 10)            # ms busy per s -> %
            row["capacity"], row["model"], row["kind"] = self._disk_info(name)
            snap.disks[name] = row
            for k in tot:
                tot[k] += v[k]
        snap.disk = _rates(tot, self._prev_disk_tot if self._prev_disks else None, dt, list(tot))
        self._prev_disks, self._prev_disk_tot = disks, tot

        ifaces = parse_net_dev(_read(os.path.join(self.proc, "net", "dev")))
        prev = self._prev_net or {}
        keys = ("rx_bytes", "rx_packets", "tx_bytes", "tx_packets")
        tot = dict.fromkeys(keys, 0)
        for name, v in ifaces.items():
            snap.interfaces[name] = _rates(v, prev.get(name), dt, keys)
            if name != "lo":                                  # the loopback is no network traffic
                for k in keys:
                    tot[k] += v[k]
        snap.net = dict(tot)
        for k in keys:
            snap.net[k + "_ps"] = sum(r[k + "_ps"] for n, r in snap.interfaces.items() if n != "lo")
        self._prev_net = ifaces

        snap.gpus = self.gpus()
        snap.gpu_info = dict(self.gpu_info)
        self.gpu_usage(procs, dt, now)
        snap.battery = battery_info(self.sys)
        self._prev_t = now
        return snap


# -- sorting / filtering (used by the window's models and the tests) ---------------------------
def matches(p, text: str, mine_uid: Optional[int] = None) -> bool:
    """The search field and All / My Processes filter: name, PID or user."""
    if mine_uid is not None and p.uid != mine_uid:
        return False
    if not text:
        return True
    t = text.casefold()
    return t in p.name.casefold() or t in str(p.pid) or t in p.user.casefold()


def sort_procs(procs: List, key: str, descending: bool = False) -> List:
    """Processes by a column (name case-insensitive); equal values keep PID order."""
    by_pid = sorted(procs, key=lambda p: p.pid)
    if key == "name":
        return sorted(by_pid, key=lambda p: p.name.casefold(), reverse=descending)
    return sorted(by_pid, key=lambda p: getattr(p, key), reverse=descending)


# -- formats (Activity Monitor's) ------------------------------------------------------------
def fmt_bytes(n: float) -> str:
    """Binary units like Activity Monitor: "512 bytes", "12.3 KB", "345.6 MB", "1.23 GB"."""
    if n < 0:
        return "–"
    if n < 1024:
        return f"{int(n)} bytes"
    for unit, p, dec in (("KB", 1, 1), ("MB", 2, 1), ("GB", 3, 2), ("TB", 4, 2)):
        if n < 1024 ** (p + 1) or unit == "TB":
            return f"{n / 1024 ** p:.{dec}f} {unit}"


def fmt_cpu_time(seconds: float) -> str:
    """"12.34", "3:07.21", "1:02:03.45"."""
    # Round to hundredths first, so 119.996 rolls over to "2:00.00" (not "1:60.00").
    cs = round(round(max(0.0, seconds), 2) * 100)
    h, rem = divmod(cs, 360000)
    m, s = divmod(rem, 6000)
    s /= 100
    if h:
        return f"{int(h)}:{int(m):02d}:{s:05.2f}"
    if m:
        return f"{int(m)}:{s:05.2f}"
    return f"{s:.2f}"


def fmt_count(n: float) -> str:
    return f"{int(n):,}"


def fmt_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "Calculating…"
    h, m = divmod(int(seconds) // 60, 60)
    return f"{h}:{m:02d}"
