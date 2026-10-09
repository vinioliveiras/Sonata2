"""Graphics memory watch (NVIDIA): when the card's memory is almost full,
Sonata logs who is using it (~/.cache/sonata2/gpu-memory.log) and warns
once -- a full card took the whole session down (Vini: WhatsApp's web app
loading a video; Wayfire aborts when it can't get a buffer).

Only while the card is already awake (NvidiaUsage.awake): asking a
sleeping laptop GPU would wake it. The nvidia-smi calls run in a thread.

    VramWatch(notify=gpu.notify).start()
    parse_usage("1234, 8188") -> (1234, 8188)
    parse_processes(nvidia_smi_text) -> [(pid, name, MiB)] biggest first
"""
import os
import re
import subprocess
import threading
import time

from . import logs

EVERY_S = 15
WARN = 0.85                  # warn at 85 % ...
REARM = 0.70                 # ... and again only after it went back under 70 %
LOG = "gpu-memory.log"

# |    0   N/A  N/A    12345      G   /usr/bin/spider        1650MiB |
_PROC = re.compile(r"^\|\s+\d+\s+\S+\s+\S+\s+(\d+)\s+[A-Z+]+\s+(.+?)\s+(\d+)\s*MiB\s+\|")


def parse_usage(text: str):
    """(used MiB, total MiB) from `--query-gpu=memory.used,memory.total
    --format=csv,noheader,nounits` (the first card), None when unreadable."""
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and all(p.isdigit() for p in parts) and int(parts[1]) > 0:
            return int(parts[0]), int(parts[1])
    return None


def parse_processes(text: str) -> list:
    """[(pid, name, MiB)] from plain `nvidia-smi`'s process table, biggest first."""
    out = []
    for line in text.splitlines():
        m = _PROC.match(line)
        if m:
            out.append((int(m.group(1)), os.path.basename(m.group(2).split()[0]), int(m.group(3))))
    return sorted(out, key=lambda p: -p[2])


# -- one nvidia-smi reading per process ------------------------------------------------------
# The menu bar's figures (backend/stats.py, every second while shown) and
# this watch (every EVERY_S) asked nvidia-smi separately -- each run costs
# tens of ms of CPU and wakes the driver. Both now read one query, kept for
# a moment: whoever comes second within NV_FRESH_S gets the same answer.
NV_QUERY = ["--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
            "--format=csv,noheader,nounits"]
USAGE_ARGS = ["--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]
NV_FRESH_S = 0.9
_nv = {"t": None, "tool": None, "out": ""}
_nv_lock = threading.Lock()


def nvidia_read(tool: str, max_age: float = NV_FRESH_S, clock=time.monotonic) -> str:
    """nvidia-smi's NV_QUERY rows (one per card), from cache when younger
    than max_age. Blocking: call it off the main loop."""
    with _nv_lock:
        now = clock()
        if _nv["tool"] == tool and _nv["t"] is not None and now - _nv["t"] < max_age:
            return _nv["out"]
        try:
            out = subprocess.run([tool] + NV_QUERY, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        _nv.update(t=clock(), tool=tool, out=out)
        return out


def usage_rows(query_out: str) -> str:
    """NV_QUERY rows -> "used, total" rows (what parse_usage reads)."""
    rows = []
    for line in query_out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 3:
            rows.append(f"{parts[1]}, {parts[2]}")
    return "\n".join(rows)


def app_name(pid: int, fallback: str) -> str:
    """A readable name: the process' own name (comm) when the table cut it."""
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip() or fallback
    except OSError:
        return fallback


def log_path() -> str:
    from . import logs
    return logs.path(LOG)


class VramWatch:
    def __init__(self, notify=None, usage=None, run=None):
        if usage is None:
            from .activity.procfs import NvidiaUsage
            usage = NvidiaUsage()
        self.usage = usage                     # its card list, nvidia-smi path and awake()
        self.notify = notify
        self.run = run or self._run_tool
        self.warned = False
        self._busy = False
        self.listeners = []                    # callback(used MiB, total MiB) on the main loop, every look

    def start(self) -> None:
        if not self.usage.tool or not self.usage.devices:
            return
        from gi.repository import GLib
        GLib.timeout_add_seconds(EVERY_S, self._tick)

    def _tick(self) -> bool:
        # (paused only while locked: in a fullscreen game this reading is what
        #  turns blur and animations off when the game fills the card --
        #  gamemode.LightEffects)
        from . import quiet
        if not self._busy and not quiet.locked() and self.usage.awake():
            self._busy = True
            threading.Thread(target=self._check_safe, daemon=True).start()
        return True

    def _check_safe(self) -> None:
        try:
            self.check()
        finally:
            self._busy = False

    def _run_tool(self, args) -> str:
        if args == USAGE_ARGS:                 # the shared reading (stats.py may have just made it)
            return usage_rows(nvidia_read(self.usage.tool, max_age=EVERY_S / 3))
        try:
            return subprocess.run([self.usage.tool] + args, capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    def check(self):
        """One look; returns (used, total, procs) when it's nearly full, else None."""
        got = parse_usage(self.run(USAGE_ARGS))
        if not got:
            return None
        used, total = got
        if self.listeners:
            from gi.repository import GLib
            GLib.idle_add(lambda: [cb(used, total) for cb in list(self.listeners)] and False)
        if used < total * REARM:
            self.warned = False
        if used < total * WARN:
            return None
        procs = [(pid, app_name(pid, name), mib) for pid, name, mib in parse_processes(self.run([]))]
        self._log(used, total, procs)
        if not self.warned and self.notify:
            self.warned = True
            body = "Closing apps you don't need keeps Sonata from stopping."
            if procs:
                pid, name, mib = procs[0]
                body = f"{name} is using {mib / 1024:.1f} GB. " + body
            from gi.repository import GLib
            GLib.idle_add(lambda: self.notify("Graphics memory almost full", body) and False)
        return used, total, procs

    def _log(self, used, total, procs) -> None:
        path = log_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {used}/{total} MiB: "
                        + ", ".join(f"{n} ({pid}) {m} MiB" for pid, n, m in procs[:8]) + "\n")
            logs.trim(path)
        except OSError:
            pass
