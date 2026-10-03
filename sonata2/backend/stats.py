"""Live performance figures for the menu bar and Control Center (Vini):
CPU, GPU and memory use, network speed and the FPS of the app in front.
Nothing is read while nobody shows them: watchers subscribe, the reading
stops with the last one. Reads run off the main loop (system.run_async).

    stats = Stats.shared()
    stats.subscribe(callback)        # callback(Reading) about every second
    stats.unsubscribe(callback)

Reading fields: cpu %, gpu % (the busiest card; None: no card reports it),
gpus {card key: %} (each card, Vini: "with two, an option for each"), ram
used/total bytes, down/up bytes per second, fps (None: no reading; see
fps_state).

KINDS: what can be shown -- cpu, gpu (one card) or gpu_<card> for each
card ("gpu_amd", "gpu_nvidia"), ram, net, fps."""
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


def kinds(cards=None) -> tuple:
    """The figures that can be shown: one GPU, or one per card when there are two or more."""
    cards = gpu_cards() if cards is None else cards
    gpus = ["gpu"] if len(cards) < 2 else ["gpu_" + key for key, _m, _c in cards]
    return ("cpu", *gpus, "ram", "net", "fps")


CARDS = gpu_cards()
KINDS = kinds(CARDS)
GPU_MAKERS = {"gpu_" + key: maker for key, maker, _c in CARDS}     # "gpu_nvidia" -> "NVIDIA"


@dataclass
class Reading:
    cpu: float = 0.0
    gpu: Optional[float] = None
    gpus: dict = field(default_factory=dict)        # card key -> % (None: no reading)
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
            self._nv_pct, self._nv_list = None, []
        out, nth = {}, 0
        for key, maker, card in self._cards:
            if maker == "NVIDIA":
                out[key] = self._nv_list[nth] if awake and nth < len(self._nv_list) else None
                nth += 1
            else:
                v = procfs._read(os.path.join(self.sys, "class/drm", card, "device/gpu_busy_percent")).strip()
                out[key] = float(v) if v.isdigit() else None
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
                out = subprocess.run([self._nvidia.tool, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=3).stdout
                nums = [float(x) for x in out.split() if x.replace(".", "").isdigit()]
                self._nv_list = nums
                self._nv_pct = max(nums) if nums else None
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
        return Reading(cpu=s.cpu(), gpu=s.gpu(each), gpus=each, ram_used=used, ram_total=total, down=down,
                       up=up, fps=fps, fps_state=state)

    def _deliver(self, r: Optional[Reading]) -> None:
        self._busy = False
        if r is None:
            return
        for k, v in (("cpu", r.cpu), ("gpu", r.gpu), ("ram", r.ram_pct), ("down", r.down), ("up", r.up),
                     ("fps", r.fps), *(("gpu_" + key, pct) for key, pct in r.gpus.items())):
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
    if kind == "ram":
        return f"RAM {r.ram_pct:.0f}%"
    if kind == "net":
        return f"↓ {rate(r.down)}  ↑ {rate(r.up)}"
    if kind == "fps":
        return "– FPS" if r.fps is None else f"{r.fps} FPS"
    return ""


FPS_HINTS = {"no-plugin": "Rebuild Sonata's Wayfire plugin (install.sh) to read FPS",
             "no-app": "No app in front", "starting": "Counting…", "ok": "", "off": ""}
