"""Memory management for the apps (macOS: memory pressure, then "Your
system has run out of application memory").

Every app opened from Sonata has its own scope under sonata-apps.slice
(appscope). Under memory pressure, step by step and quietly first:

1. apps in the background (no window on screen) give memory back: the
   kernel reclaims part of their scope (memory.reclaim -- the least
   recently used first); they come back a bit slower, nothing is lost;
2. systemd-oomd closes the app causing it if the pressure stays high
   (sonata-apps.slice); the apps together never take the memory kept for
   Sonata (MemoryMax, appscope.reserve_mb);
3. only if the memory is still almost gone and the system still stalls
   after both: the alert, to choose an app to quit (needs_alert).

No GTK here (memorywatch.py is the menu bar side)."""
import os
import re

PRESSURE_SHARE = 0.15        # less free than this (or the PSI below): background apps give memory back
PRESSURE_PSI = 10.0          # % of time some task stalled on memory (avg10)
CRITICAL_SHARE = 0.05        # the alert: less free than this (and at least CRITICAL_MIN_MB)...
CRITICAL_MIN_MB = 512
CRITICAL_PSI = 10.0          # ...and every task stalled this much (full avg10)
RECLAIM_SHARE = 0.25         # of a background app's memory per pass
RECLAIM_MAX_MB = 1024
RECLAIM_EVERY_S = 30         # per app


def slice_dir(uid: int = None) -> str:
    uid = os.getuid() if uid is None else uid
    return f"/sys/fs/cgroup/user.slice/user-{uid}.slice/user@{uid}.service/sonata-apps.slice"


def meminfo(path: str = "/proc/meminfo") -> dict:
    """{"total", "available"} in MB."""
    out = {}
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.partition(":")
                if k == "MemTotal":
                    out["total"] = int(v.split()[0]) // 1024
                elif k == "MemAvailable":
                    out["available"] = int(v.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def psi(path: str = "/proc/pressure/memory") -> dict:
    """{"some", "full"}: avg10 of the memory pressure (0 when unknown)."""
    out = {"some": 0.0, "full": 0.0}
    try:
        with open(path) as f:
            for line in f:
                kind = line.split(" ", 1)[0]
                m = re.search(r"avg10=([0-9.]+)", line)
                if kind in out and m:
                    out[kind] = float(m.group(1))
    except OSError:
        pass
    return out


def level(total_mb: int, available_mb: int, pressure: dict) -> str:
    """"ok", "pressure" (background apps give memory back) or "critical" (the alert's condition)."""
    if not total_mb:
        return "ok"
    if (available_mb < max(total_mb * CRITICAL_SHARE, CRITICAL_MIN_MB)
            and pressure.get("full", 0.0) >= CRITICAL_PSI):
        return "critical"
    if available_mb < total_mb * PRESSURE_SHARE or pressure.get("some", 0.0) >= PRESSURE_PSI:
        return "pressure"
    return "ok"


def _read_int(path: str) -> int:
    try:
        with open(path) as f:
            return int(f.read().strip() or 0)
    except (OSError, ValueError):
        return 0


def scopes(root: str = None) -> list:
    """[{"path", "unit", "memory" (bytes), "pids"}] of the apps' scopes."""
    root = root or slice_dir()
    out = []
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return out
    for name in names:
        path = os.path.join(root, name)
        if not name.endswith(".scope") or not os.path.isdir(path):
            continue
        try:
            with open(os.path.join(path, "cgroup.procs")) as f:
                pids = [int(p) for p in f.read().split()]
        except (OSError, ValueError):
            pids = []
        if pids:
            out.append({"path": path, "unit": name, "memory": _read_int(os.path.join(path, "memory.current")),
                        "pids": pids})
    return out


def on_screen(views) -> dict:
    """{pid: last focus} of the windows that are on screen (mapped, not minimized)."""
    out = {}
    for v in views or []:
        if (isinstance(v, dict) and v.get("role", "toplevel") == "toplevel" and v.get("mapped", True)
                and not v.get("minimized") and v.get("pid", 0) > 1):
            out[v["pid"]] = max(out.get(v["pid"], 0), v.get("last-focus-timestamp", 0))
    return out


def background(all_scopes, views) -> list:
    """The scopes with no window on screen, the biggest first."""
    shown = on_screen(views)
    return sorted((s for s in all_scopes if not any(p in shown for p in s["pids"])),
                  key=lambda s: -s["memory"])


def reclaim_amount(memory_bytes: int) -> int:
    return int(min(memory_bytes * RECLAIM_SHARE, RECLAIM_MAX_MB * 1024 * 1024))


def reclaim(path: str, amount: int) -> bool:
    """Ask the kernel to take `amount` bytes back from this scope (memory.reclaim).
    It may get less (EAGAIN): that's fine."""
    if amount < 16 * 1024 * 1024:
        return False
    try:
        with open(os.path.join(path, "memory.reclaim"), "w") as f:
            f.write(str(amount))
        return True
    except OSError:
        return False


def kill(path: str) -> bool:
    """Force Quit: every process of the app's scope at once (cgroup.kill)."""
    try:
        with open(os.path.join(path, "cgroup.kill"), "w") as f:
            f.write("1")
        return True
    except OSError:
        return False


def _unescape(text: str) -> str:
    return re.sub(r"\\x([0-9a-f]{2})", lambda m: chr(int(m.group(1), 16)), text)


def app_of(scope: dict):
    """(desktop id or None, web app id or None) of a scope."""
    m = re.match(r"app-sonata2-(.+)-\d+\.scope$", scope["unit"])
    if m:
        return _unescape(m.group(1)), None
    for pid in scope["pids"][:3]:                     # a web app's own scope: `sonata2 webapp <id>`
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().decode(errors="replace").split("\0")
        except OSError:
            continue
        for i, a in enumerate(args[:-1]):
            if a == "webapp" and i > 0 and args[i - 1] == "sonata2":
                return None, args[i + 1]
    return None, None
