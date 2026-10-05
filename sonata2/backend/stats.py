"""Live performance figures for the menu bar and Control Center (Vini):
CPU, GPU and memory use, network speed and the FPS of the app in front.
Nothing is read while nobody shows them: watchers subscribe, the reading
stops with the last one. Reads run off the main loop (system.run_async).

    stats = Stats.shared()
    stats.subscribe(callback)        # callback(Reading) about every second
    stats.unsubscribe(callback)

Reading fields: cpu %, gpu % (the busiest card; None: no card reports it),
gpus {card key: %} (each card, Vini: "with two, an option for each"),
vrams {card key: (used, total) bytes or None} (each card's video memory), ram
used/total bytes, down/up bytes per second, fps (None: no reading; see
fps_state).

KINDS: what can be shown -- cpu, gpu (one card) or gpu_<card> for each
card ("gpu_amd", "gpu_nvidia"), vram or vram_<card> (cards that report their
video memory: NVIDIA, AMD), temp_cpu and temp_gpu or temp_<card>
(temperatures, °C), ram, net, fps."""
import glob
import os
import threading
from dataclasses import dataclass, field
from typing import Optional

from ..activity import procfs

INTERVAL_MS = 1000
HISTORY = 30                       # samples kept for the little graphs


def gpu_cards(sys: str = "/sys") -> list:
    """[(key, maker, cardN)] of the graphics cards, in card order: key
    "amd", "nvidia", "intel" ("amd2" for a second card of one maker)."""
    out, seen = [], {}
    for card in sorted(glob.glob(os.path.join(sys, "class/drm/card[0-9]*"))):
        name = os.path.basename(card)
        if not name[4:].isdigit():
            continue                                    # a connector (card1-HDMI-A-1)
        drv = os.path.basename(os.path.realpath(os.path.join(card, "device", "driver")))
        maker = procfs.CARD_NAMES.get(drv)
        if not maker:
            continue
        base = maker.lower()
        seen[base] = seen.get(base, 0) + 1
        out.append((base if seen[base] == 1 else f"{base}{seen[base]}", maker, name))
    return out


VRAM_MAKERS = ("NVIDIA", "AMD")        # Intel's integrated GPU has no memory of its own to show


def vram_kinds(cards=None) -> dict:
    """{kind: card key} of the cards whose video memory can be shown: "vram"
    for one, "vram_<card>" each when there are two or more."""
    cards = gpu_cards() if cards is None else cards
    keys = [key for key, maker, _c in cards if maker in VRAM_MAKERS]
    return {"vram": keys[0]} if len(keys) == 1 else {"vram_" + k: k for k in keys}


TEMP_MAKERS = ("NVIDIA", "AMD")        # cards whose temperature can be read (Intel's iGPU: the CPU's)
CPU_SENSORS = ("k10temp", "zenpower", "coretemp", "cpu_thermal")
CPU_LABELS = ("Tctl", "Tdie", "Package id 0")          # the whole chip, before a single core


def cpu_temp_path(sys: str = "/sys") -> str:
    """The CPU's temperature file (hwmon), "" when there is none."""
    for hw in sorted(glob.glob(os.path.join(sys, "class/hwmon/hwmon*"))):
        if procfs._read(os.path.join(hw, "name")).strip() not in CPU_SENSORS:
            continue
        inputs = sorted(glob.glob(os.path.join(hw, "temp*_input")))
        labels = {procfs._read(i.replace("_input", "_label")).strip(): i for i in inputs}
        for want in CPU_LABELS:
            if want in labels:
                return labels[want]
        if inputs:
            return inputs[0]
    return ""


def temp_kinds(cards=None) -> dict:
    """{kind: card key} of the cards whose temperature can be shown:
    "temp_gpu" for one, "temp_<card>" each when there are two or more."""
    cards = gpu_cards() if cards is None else cards
    keys = [key for key, maker, _c in cards if maker in TEMP_MAKERS]
    return {"temp_gpu": keys[0]} if len(keys) == 1 else {"temp_" + k: k for k in keys}


def kinds(cards=None, cpu_temp=None) -> tuple:
    """The figures that can be shown: one GPU, or one per card when there are two or more."""
    cards = gpu_cards() if cards is None else cards
    gpus = ["gpu"] if len(cards) < 2 else ["gpu_" + key for key, _m, _c in cards]
    temps = (["temp_cpu"] if (cpu_temp_path() if cpu_temp is None else cpu_temp) else []) + list(temp_kinds(cards))
    return ("cpu", *gpus, *vram_kinds(cards), *temps, "ram", "net", "fps")


CARDS = gpu_cards()
KINDS = kinds(CARDS)
GPU_MAKERS = {"gpu_" + key: maker for key, maker, _c in CARDS}     # "gpu_nvidia" -> "NVIDIA"
VRAM_KINDS = vram_kinds(CARDS)                                      # "vram_nvidia" -> "nvidia"
VRAM_MAKERS_BY_KIND = {k: dict((c[0], c[1]) for c in CARDS)[key] for k, key in VRAM_KINDS.items()}
TEMP_KINDS = temp_kinds(CARDS)                                      # "temp_nvidia" -> "nvidia"
TEMP_MAKERS_BY_KIND = {k: dict((c[0], c[1]) for c in CARDS)[key] for k, key in TEMP_KINDS.items()}


@dataclass
class Reading:
    cpu: float = 0.0
    gpu: Optional[float] = None
    gpus: dict = field(default_factory=dict)        # card key -> % (None: no reading)
    vrams: dict = field(default_factory=dict)       # card key -> (used, total) bytes (None: no reading)
    cpu_temp: Optional[float] = None                # °C
    temps: dict = field(default_factory=dict)       # card key -> °C (None: no reading)
    top: dict = field(default_factory=dict)         # "cpu" / "gpu" / "gpu_<card>" -> [(name, %)], busiest first
    ram_used: int = 0
    ram_total: int = 0
    down: float = 0.0
    up: float = 0.0
    fps: Optional[int] = None
    fps_state: str = "off"         # "ok", "starting", "no-app", "no-plugin"
    history: dict = field(default_factory=dict)    # name -> [values], oldest first

    @property
    def ram_pct(self) -> float:
        return 100.0 * self.ram_used / self.ram_total if self.ram_total else 0.0


class Sampler:
    """The figures from /proc and /sys (pure reads; testable on a fake root)."""

    def __init__(self, proc: str = "/proc", sys: str = "/sys", ipc=None):
        self.proc, self.sys = proc, sys
        self._cpu = None
        self._net = None
        self._ipc = ipc
        self._nvidia = None
        self._nv_pct = None
        self._nv_list = []                 # nvidia-smi's figures, one per NVIDIA card in order
        self._nv_mem = []                  # (used, total) bytes, one per NVIDIA card in order
        self._nv_temp = []                 # °C, one per NVIDIA card in order
        self._cpu_temp = None              # its hwmon file ("" none)
        self._nv_busy = False
        self._cards = None

    def cpu(self) -> float:
        line = procfs._read(os.path.join(self.proc, "stat")).split("\n", 1)[0]
        if not line.startswith("cpu"):
            return 0.0
        cur = procfs.parse_cpu_line(line)
        prev, self._cpu = self._cpu, cur
        if prev is None:
            return 0.0
        p = procfs.cpu_percent(prev, cur)
        return round(100.0 - p["idle"], 1)

    def ram(self) -> tuple:
        m = procfs.memory_summary(procfs.parse_meminfo(procfs._read(os.path.join(self.proc, "meminfo"))))
        return int(m["used"]), int(m["total"])

    def net(self, dt: float) -> tuple:
        rows = procfs.parse_net_dev(procfs._read(os.path.join(self.proc, "net/dev")))
        rx = sum(r["rx_bytes"] for n, r in rows.items() if n != "lo")
        tx = sum(r["tx_bytes"] for n, r in rows.items() if n != "lo")
        prev, self._net = self._net, (rx, tx)
        if prev is None or dt <= 0:
            return 0.0, 0.0
        return max(0.0, (rx - prev[0]) / dt), max(0.0, (tx - prev[1]) / dt)

    def gpus(self) -> dict:
        """{card key: busy %} for every card: amdgpu/Intel from gpu_busy_percent;
        NVIDIA through nvidia-smi, only while the card is awake (asking would
        wake it), else None."""
        if self._cards is None:
            self._cards = gpu_cards(self.sys)
        if self._nvidia is None:
            self._nvidia = procfs.NvidiaUsage(self.sys)
        nv = self._nvidia
        awake = bool(nv.tool and nv.devices and nv.awake())
        if awake:
            self._nvidia_refresh()
        else:
            self._nv_pct, self._nv_list, self._nv_mem, self._nv_temp = None, [], [], []
        out, nth = {}, 0
        for key, maker, card in self._cards:
            if maker == "NVIDIA":
                out[key] = self._nv_list[nth] if awake and nth < len(self._nv_list) else None
                nth += 1
            else:
                v = procfs._read(os.path.join(self.sys, "class/drm", card, "device/gpu_busy_percent")).strip()
                out[key] = float(v) if v.isdigit() else None
        return out

    def vrams(self) -> dict:
        """{card key: (used, total) bytes} of each card in VRAM_MAKERS: AMD
        from mem_info_vram_*, NVIDIA from the nvidia-smi read of gpus()
        (None while the card sleeps). Call after gpus()."""
        if self._cards is None:
            self._cards = gpu_cards(self.sys)
        out, nth = {}, 0
        for key, maker, card in self._cards:
            if maker == "NVIDIA":
                out[key] = self._nv_mem[nth] if nth < len(self._nv_mem) else None
                nth += 1
            elif maker in VRAM_MAKERS:
                dev = os.path.join(self.sys, "class/drm", card, "device")
                used = procfs._read(os.path.join(dev, "mem_info_vram_used")).strip()
                total = procfs._read(os.path.join(dev, "mem_info_vram_total")).strip()
                out[key] = (int(used), int(total)) if used.isdigit() and total.isdigit() and int(total) else None
        return out

    def cpu_temp(self):
        """The CPU's °C (k10temp's Tctl, coretemp's package), None without a sensor."""
        if self._cpu_temp is None:
            self._cpu_temp = cpu_temp_path(self.sys)
        v = procfs._read(self._cpu_temp).strip() if self._cpu_temp else ""
        return int(v) / 1000 if v.lstrip("-").isdigit() else None

    def temps(self) -> dict:
        """{card key: °C} of each card in TEMP_MAKERS: AMD from its hwmon
        (edge), NVIDIA from the nvidia-smi read of gpus(). Call after gpus()."""
        if self._cards is None:
            self._cards = gpu_cards(self.sys)
        out, nth = {}, 0
        for key, maker, card in self._cards:
            if maker == "NVIDIA":
                out[key] = self._nv_temp[nth] if nth < len(self._nv_temp) else None
                nth += 1
            elif maker in TEMP_MAKERS:
                files = sorted(glob.glob(os.path.join(self.sys, "class/drm", card, "device/hwmon/hwmon*/temp1_input")))
                v = procfs._read(files[0]).strip() if files else ""
                out[key] = int(v) / 1000 if v.lstrip("-").isdigit() else None
        return out

    def gpu(self, each: dict = None) -> Optional[float]:
        """The busiest card (from gpus())."""
        vals = [v for v in (self.gpus() if each is None else each).values() if v is not None]
        return max(vals) if vals else None

    def _nvidia_refresh(self) -> None:
        if self._nv_busy:
            return
        self._nv_busy = True

        def run():
            import subprocess
            try:
                out = subprocess.run([self._nvidia.tool, "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                                      "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=3).stdout
                self._nv_list, self._nv_mem, self._nv_temp = parse_nvidia(out)
                self._nv_pct = max(self._nv_list) if self._nv_list else None
            except (OSError, ValueError, Exception):
                self._nv_pct = None
            self._nv_busy = False
        threading.Thread(target=run, daemon=True).start()

    def fps(self) -> tuple:
        """(fps or None, state) from Sonata's Wayfire plugin (IPC sonata/fps)."""
        ipc = self._ipc
        if ipc is None:
            try:
                from ..wl.wfipc import WayfireIPC
                ipc = self._ipc = WayfireIPC()
            except Exception:
                return None, "no-plugin"
        r = ipc.call("sonata/fps")
        if not isinstance(r, dict) or "fps" not in r:
            return None, "no-plugin"                 # plugin not rebuilt yet (or no Wayfire)
        if not r.get("app-id"):
            return None, "no-app"
        if not r.get("ready"):
            return None, "starting"
        return int(r["fps"]), "ok"


def parse_nvidia(out: str) -> tuple:
    """([busy %], [(used, total) bytes], [°C]) from nvidia-smi's
    utilization.gpu,memory.used,memory.total (MiB),temperature.gpu rows,
    one per card."""
    pcts, mems, temps = [], [], []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        try:
            pcts.append(float(parts[0]))
        except (ValueError, IndexError):
            continue
        try:
            used, total = int(parts[1]) << 20, int(parts[2]) << 20
            mems.append((used, total) if total else None)
        except (ValueError, IndexError):
            mems.append(None)
        try:
            temps.append(float(parts[3]))
        except (ValueError, IndexError):
            temps.append(None)
    return pcts, mems, temps


TOP_N = 5


def top_list(rows, n: int = TOP_N) -> list:
    """[(name, %)] summed per program name (Chrome's many processes are one),
    busiest first, the n busiest above 0.5 %."""
    total = {}
    for name, pct in rows:
        if pct > 0:
            total[name] = total.get(name, 0.0) + pct
    return sorted(((k, v) for k, v in total.items() if v >= 0.5), key=lambda kv: -kv[1])[:n]


class TopProcs:
    """The busiest programs for CPU and each GPU (Task Manager's reader,
    procfs.Sampler: DRM fdinfo for AMD/Intel, nvidia-smi pmon for NVIDIA
    while it's awake). Read only while a module showing them is open."""

    def __init__(self, sampler=None, clock=None):
        import time
        self.s = sampler or procfs.Sampler()
        self.clock = clock or time.monotonic
        self._prev = {}
        self._t = None

    def read(self) -> dict:
        now = self.clock()
        dt = now - self._t if self._t is not None else 0.0
        procs = self.s.processes(want=())
        ticks = {}
        for p in procs.values():
            key = (p.pid, p.start_ticks)
            ticks[key] = p.ticks
            prev = self._prev.get(key)
            if prev is not None and dt > 0:
                p.cpu = max(0.0, 100.0 * (p.ticks - prev) / procfs.CLK_TCK / dt)
        self._prev, self._t = ticks, now
        try:
            self.s.gpu_usage(procs, dt, now)
        except Exception:
            pass
        out = {"cpu": top_list((p.name, p.cpu) for p in procs.values()),
               "gpu": top_list((p.name, p.gpu) for p in procs.values())}
        for key, maker, _c in CARDS:
            out["gpu_" + key] = top_list((p.name, p.gpu) for p in procs.values() if maker in (p.gpu_on or ""))
        return out


class Stats:
    """One per process: reads while anyone subscribes."""
    _shared = None

    @classmethod
    def shared(cls) -> "Stats":
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def __init__(self, sampler: Sampler = None):
        self.sampler = sampler or Sampler()
        self.listeners = []
        self.history = {k: [] for k in ("cpu", "gpu", "ram", "down", "up", "fps")}      # (+ gpu_<card>)
        self.last = None
        self.top_watchers = 0              # modules showing the busiest programs (TopProcs read only then)
        self._top = None
        self._timer = 0
        self._busy = False
        self._t = None

    def subscribe(self, cb) -> None:
        if cb not in self.listeners:
            self.listeners.append(cb)
        if self.last is not None:
            cb(self.last)
        if not self._timer:
            from gi.repository import GLib
            self._tick()
            self._timer = GLib.timeout_add(INTERVAL_MS, self._tick)

    def unsubscribe(self, cb) -> None:
        if cb in self.listeners:
            self.listeners.remove(cb)
        if not self.listeners and self._timer:
            from gi.repository import GLib
            GLib.source_remove(self._timer)
            self._timer = 0

    def _tick(self) -> bool:
        if not self.listeners:
            self._timer = 0
            return False
        if not self._busy:
            self._busy = True
            from . import system
            system.run_async(self.read, self._deliver)
        return True

    def read(self) -> Reading:
        import time
        now = time.monotonic()
        dt = now - self._t if self._t is not None else 0.0
        self._t = now
        s = self.sampler
        used, total = s.ram()
        down, up = s.net(dt)
        fps, state = s.fps()
        each = s.gpus()
        top = {}
        if self.top_watchers > 0:
            if self._top is None:
                self._top = TopProcs()
            try:
                top = self._top.read()
            except Exception:
                top = {}
        return Reading(cpu=s.cpu(), gpu=s.gpu(each), gpus=each, vrams=s.vrams(), cpu_temp=s.cpu_temp(), temps=s.temps(),
                       top=top, ram_used=used, ram_total=total, down=down,
                       up=up, fps=fps, fps_state=state)

    def _deliver(self, r: Optional[Reading]) -> None:
        self._busy = False
        if r is None:
            return
        for k, v in (("cpu", r.cpu), ("gpu", r.gpu), ("ram", r.ram_pct), ("down", r.down), ("up", r.up),
                     ("fps", r.fps), *(("gpu_" + key, pct) for key, pct in r.gpus.items()),
                     *((kind, vram_pct(r, key)) for kind, key in VRAM_KINDS.items()),
                     ("temp_cpu", r.cpu_temp), *((kind, r.temps.get(key)) for kind, key in TEMP_KINDS.items())):
            h = self.history.setdefault(k, [])
            h.append(v if v is not None else 0.0)
            del h[:-HISTORY]
        r.history = self.history
        self.last = r
        for cb in list(self.listeners):
            cb(r)


# -- text ----------------------------------------------------------------------------------
def rate(n: float) -> str:
    """Network speed, short: "0 KB/s", "12 KB/s", "1.2 MB/s"."""
    if n < 1024 * 1000:
        return f"{n / 1024:.0f} KB/s"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.1f} MB/s"
    return f"{n / 1024 ** 3:.2f} GB/s"


def vram_pct(r: Reading, key: str):
    v = r.vrams.get(key)
    return 100.0 * v[0] / v[1] if v else None


def gib(n: int) -> str:
    """Video memory, short: "0.4", "3.2", "12" (GB)."""
    g = n / 1024 ** 3
    return f"{g:.1f}" if g < 10 else f"{g:.0f}"


def vram_text(r: Reading, key: str) -> str:
    """"3.2 / 8.0 GB", "–" without a reading (an NVIDIA card asleep)."""
    v = r.vrams.get(key)
    return "–" if not v else f"{gib(v[0])} / {gib(v[1])} GB"


def text(kind: str, r: Reading) -> str:
    """The menu bar's text for one figure."""
    if kind == "cpu":
        return f"CPU {r.cpu:.0f}%"
    if kind == "gpu":
        return "GPU –" if r.gpu is None else f"GPU {r.gpu:.0f}%"
    if kind.startswith("gpu_"):                     # one card: its maker
        v = r.gpus.get(kind[4:])
        maker = GPU_MAKERS.get(kind, kind[4:].upper())
        return f"{maker} –" if v is None else f"{maker} {v:.0f}%"
    if kind in VRAM_KINDS:                          # "VRAM 3.2 GB", "NVIDIA VRAM 3.2 GB"
        v = r.vrams.get(VRAM_KINDS[kind])
        name = "VRAM" if kind == "vram" else f"{VRAM_MAKERS_BY_KIND[kind]} VRAM"
        return f"{name} –" if not v else f"{name} {gib(v[0])} GB"
    if kind == "temp_cpu":                          # "CPU 62°C"
        return "CPU –" if r.cpu_temp is None else f"CPU {r.cpu_temp:.0f}°C"
    if kind in TEMP_KINDS:                          # "GPU 55°C", "NVIDIA 55°C"
        v = r.temps.get(TEMP_KINDS[kind])
        name = "GPU" if kind == "temp_gpu" else TEMP_MAKERS_BY_KIND[kind]
        return f"{name} –" if v is None else f"{name} {v:.0f}°C"
    if kind == "ram":
        return f"RAM {r.ram_pct:.0f}%"
    if kind == "net":
        return f"↓ {rate(r.down)}  ↑ {rate(r.up)}"
    if kind == "fps":
        return "– FPS" if r.fps is None else f"{r.fps} FPS"
    return ""


FPS_HINTS = {"no-plugin": "Rebuild Sonata's Wayfire plugin (install.sh) to read FPS",
             "no-app": "No app in front", "starting": "Counting…", "ok": "", "off": ""}
