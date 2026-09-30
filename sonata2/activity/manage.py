"""Task Manager's page data that isn't a /proc counter: grouping processes
into apps (Processes page), per-user totals (Users), cumulative app usage
(App history), login items (Startup apps) and systemd services (Services).
No GTK here: the window calls these off the main loop where they block.

    apps, background, system = group_processes(procs, app_of, my_uid)
    per_user(procs)                         # {user: {"cpu", "mem", "procs", "uid"}}
    History().add(snapshot, apps) / .rows()  # CPU time and disk per app since "since"
    startup_entries() / set_startup_enabled(entry, on)
    list_services("user") / service_action("ssh.service", "restart", "system")"""
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple


# -- Processes: apps / background / system ----------------------------------------------------
def group_processes(procs: dict, app_of: Callable, my_uid: int) -> Tuple[dict, list, list]:
    """Windows Task Manager's groups.

    app_of(proc) -> app key or None (the desktop app a process belongs to).
    Apps: the user's processes of a desktop app whose parent isn't the same
    app, merged by key, each with every process below it (helpers,
    renderers...). System processes: kernel threads and processes of
    system accounts (uid < 1000, not the user's). Background: the rest.
    Returns ({key: [pids]}, [pids], [pids]); an app's pids start with its
    top processes."""
    children: Dict[int, list] = {}
    for p in procs.values():
        children.setdefault(p.ppid, []).append(p.pid)
    keys = {pid: (app_of(p) if p.uid == my_uid and p.exe else None) for pid, p in procs.items()}
    apps: Dict[str, list] = {}
    owned = set()
    for pid in sorted(procs):
        key = keys[pid]
        if key is None or pid in owned:
            continue
        parent = procs.get(procs[pid].ppid)
        if parent is not None and keys.get(parent.pid) == key:
            continue                                   # a child of the same app: under its top process
        members = apps.setdefault(key, [])
        stack = [pid]
        while stack:                                   # the whole tree below it
            cur = stack.pop()
            if cur in owned:
                continue
            owned.add(cur)
            members.append(cur)
            stack.extend(sorted(children.get(cur, ()), reverse=True))
    background, system = [], []
    for pid, p in sorted(procs.items()):
        if pid in owned:
            continue
        if not p.exe or (p.uid < 1000 and p.uid != my_uid):
            system.append(pid)
        else:
            background.append(pid)
    return apps, background, system


# -- Users ------------------------------------------------------------------------------------
def per_user(procs: dict) -> Dict[str, dict]:
    """CPU (% of one core), memory (RSS) and process count per user."""
    out: Dict[str, dict] = {}
    for p in procs.values():
        u = out.setdefault(p.user or str(p.uid), {"cpu": 0.0, "mem": 0, "procs": 0, "uid": p.uid,
                                                  "disk": 0.0})
        u["cpu"] += p.cpu
        u["mem"] += p.rss
        u["procs"] += 1
        u["disk"] += p.read_ps + p.write_ps
    return out


# -- App history ------------------------------------------------------------------------------
class History:
    """CPU time and disk traffic per app, added up from the samples
    (Windows' App history). Stored as a dict (config "activity-history")."""

    def __init__(self, data: Optional[dict] = None):
        data = data or {}
        self.since = data.get("since") or time.time()
        self.apps: Dict[str, dict] = {k: dict(v) for k, v in (data.get("apps") or {}).items()}

    def add(self, snap, apps: Dict[str, list], names: Dict[str, str]) -> None:
        """One sample: each app's processes used cpu% of a core for
        snap.interval seconds and moved read/write bytes per second."""
        dt = snap.interval
        if dt <= 0:
            return
        for key, pids in apps.items():
            cpu = disk = 0.0
            for pid in pids:
                p = snap.procs.get(pid)
                if p is not None:
                    cpu += p.cpu / 100 * dt
                    disk += (p.read_ps + p.write_ps) * dt
            a = self.apps.setdefault(key, {"name": names.get(key, key), "cpu_time": 0.0, "disk": 0.0})
            a["name"] = names.get(key, a["name"])
            a["cpu_time"] += cpu
            a["disk"] += disk
            a["last"] = time.time()

    def clear(self) -> None:
        self.apps, self.since = {}, time.time()

    def to_dict(self) -> dict:
        return {"since": self.since, "apps": self.apps}


# -- Startup apps -----------------------------------------------------------------------------
@dataclass
class StartupEntry:
    file: str                 # file name (the entry's id: a user copy overrides the system one)
    path: str                 # where it was read
    name: str
    comment: str
    command: str
    icon: str
    enabled: bool
    user: bool                # the file lives in the user's autostart folder


def _user_dir() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "autostart")


def _system_dirs() -> list:
    dirs = (os.environ.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":")
    return [os.path.join(d, "autostart") for d in dirs if d]


def _desktop_section(text: str) -> dict:
    """[Desktop Entry] keys of a .desktop file (untranslated values)."""
    out, inside = {}, False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            inside = s == "[Desktop Entry]"
            continue
        if inside and "=" in s and not s.startswith("#"):
            k, _, v = s.partition("=")
            out.setdefault(k.strip(), v.strip())
    return out


def _true(v: Optional[str]) -> bool:
    return (v or "").strip().lower() == "true"


def startup_entries(user_dir: str = None, system_dirs: list = None) -> List[StartupEntry]:
    """The login items: ~/.config/autostart over $XDG_CONFIG_DIRS/autostart
    (same file name: the user's wins), sorted by name."""
    user_dir = user_dir or _user_dir()
    seen, out = set(), []
    for d in [user_dir] + list(system_dirs if system_dirs is not None else _system_dirs()):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for fn in names:
            if not fn.endswith(".desktop") or fn in seen:
                continue
            seen.add(fn)
            path = os.path.join(d, fn)
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    kv = _desktop_section(f.read())
            except OSError:
                continue
            enabled = not _true(kv.get("Hidden")) and kv.get("X-GNOME-Autostart-enabled", "true").lower() != "false"
            out.append(StartupEntry(file=fn, path=path, name=kv.get("Name") or fn[:-8], comment=kv.get("Comment", ""),
                                    command=kv.get("Exec", ""), icon=kv.get("Icon", ""), enabled=enabled,
                                    user=d == user_dir))
    out.sort(key=lambda e: e.name.casefold())
    return out


def _set_keys(text: str, values: dict) -> str:
    """Set (value) or remove (None) keys of the [Desktop Entry] section."""
    lines = text.splitlines()
    out, inside, done = [], False, set()
    for line in lines:
        s = line.strip()
        if s.startswith("["):
            if inside:                                  # end of the section: add what's missing
                out.extend(f"{k}={v}" for k, v in values.items() if v is not None and k not in done)
                done.update(values)
            inside = s == "[Desktop Entry]"
            out.append(line)
            continue
        if inside and "=" in s and not s.startswith("#"):
            k = s.partition("=")[0].strip()
            if k in values:
                if values[k] is not None and k not in done:
                    out.append(f"{k}={values[k]}")
                done.add(k)
                continue
        out.append(line)
    if inside:
        out.extend(f"{k}={v}" for k, v in values.items() if v is not None and k not in done)
    return "\n".join(out) + "\n"


def set_startup_enabled(entry: StartupEntry, on: bool, user_dir: str = None) -> str:
    """Enable/disable a login item the XDG way: a copy in the user's
    autostart folder gets Hidden=true (off) or loses it (on); a system file
    is never touched. Returns the path written."""
    user_dir = user_dir or _user_dir()
    os.makedirs(user_dir, exist_ok=True)
    target = os.path.join(user_dir, entry.file)
    src = target if os.path.exists(target) else entry.path
    with open(src, encoding="utf-8", errors="replace") as f:
        text = f.read()
    values = {"Hidden": None if on else "true"}
    if on and _desktop_section(text).get("X-GNOME-Autostart-enabled", "").lower() == "false":
        values["X-GNOME-Autostart-enabled"] = "true"
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(_set_keys(text, values))
    os.replace(tmp, target)
    entry.enabled, entry.path, entry.user = on, target, True
    return target


# -- Services ---------------------------------------------------------------------------------
@dataclass
class Service:
    unit: str
    description: str
    load: str
    active: str               # active / inactive / failed / activating...
    sub: str                  # running / exited / dead...
    scope: str                # "user" / "system"

    @property
    def status(self) -> str:
        if self.active == "active":
            return "Running" if self.sub == "running" else "Active"
        if self.active == "failed":
            return "Failed"
        if self.active in ("activating", "deactivating", "reloading"):
            return self.active.capitalize()
        return "Stopped"


def parse_units_json(text: str, scope: str) -> List[Service]:
    out = []
    for u in json.loads(text or "[]"):
        if u.get("unit", "").endswith(".service"):
            out.append(Service(u["unit"], u.get("description", ""), u.get("load", ""), u.get("active", ""),
                               u.get("sub", ""), scope))
    return out


def parse_units_text(text: str, scope: str) -> List[Service]:
    """`systemctl list-units --plain --no-legend` lines: UNIT LOAD ACTIVE SUB DESCRIPTION."""
    out = []
    for line in text.splitlines():
        parts = line.lstrip("●*× ").split(None, 4)
        if len(parts) >= 4 and parts[0].endswith(".service"):
            out.append(Service(parts[0], parts[4] if len(parts) > 4 else "", parts[1], parts[2], parts[3], scope))
    return out


def _systemctl(scope: str) -> List[str]:
    return ["systemctl"] + (["--user"] if scope == "user" else [])


def list_services(scope: str, run=None) -> Optional[List[Service]]:
    """Every service unit of one scope; None when systemctl can't answer
    (no systemd, no bus). run(cmd) -> (returncode, stdout) for tests."""
    run = run or _run
    base = _systemctl(scope) + ["list-units", "--type=service", "--all", "--no-pager", "--plain"]
    code, out = run(base + ["--output=json"])
    if code == 0 and out.lstrip().startswith("["):
        try:
            return parse_units_json(out, scope)
        except ValueError:
            pass
    code, out = run(base + ["--no-legend"])
    return parse_units_text(out, scope) if code == 0 else None


def service_action(unit: str, action: str, scope: str, run=None) -> Tuple[bool, str]:
    """start / stop / restart. System units ask through polkit (systemd's
    own); if that is refused and pkexec exists, pkexec tries once more."""
    assert action in ("start", "stop", "restart")
    run = run or _run
    code, out = run(_systemctl(scope) + [action, unit])
    if code != 0 and scope == "system" and shutil.which("pkexec"):
        code, out = run(["pkexec", "systemctl", action, unit])
    return code == 0, out.strip()


def _run(cmd: list, timeout: int = 20) -> Tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)
    return p.returncode, p.stdout if p.returncode == 0 else (p.stderr or p.stdout)
