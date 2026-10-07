"""Settings > Desktop & Windows > "Quit apps when their last window closes" (on by default)
(Vini: closing Steam with its window's X left it running in the background).

When an app's last window goes and none comes back within GRACE_MS (Steam
swaps its sign-in window for the main one), the whole app gets SIGTERM --
the polite quit (quitapps.py): the window's process, the app it belongs to
(up to what launched it: Sonata, systemd) and everything under that. Only
the window's process came back: Steam restarted its web helper, and its
window with it (Vini). An app with a window of another app under it (a game
Steam started) is left alone, and so is Sonata itself.

    QuitOnClose(manager)          # manager: wl.toplevels.ToplevelManager"""
import os
import signal
import time

from gi.repository import GLib

from . import quitapps

GRACE_MS = 1500
KILL_MS = 3000           # then whatever is left of it goes (Vini: "just kill the process")
CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _cache(*parts) -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2", *parts)


def norm(did: str) -> str:
    did = did or ""
    return did[:-8] if did.endswith(".desktop") else did


def mark_launch(desktop_id: str) -> None:
    """Sonata opened this app (apps.py, any Sonata process): a quit pending
    for it is called off -- opened again right after closing it, the app
    was quit while it came back (Vini: apps didn't open)."""
    if not desktop_id:
        return
    try:
        os.makedirs(_cache("launched"), exist_ok=True)
        with open(_cache("launched", norm(desktop_id).replace("/", "_")), "w") as f:
            f.write(str(time.time()))
    except OSError:
        pass


def launched_at(key: str) -> float:
    try:
        return os.path.getmtime(_cache("launched", norm(key).replace("/", "_")))
    except OSError:
        return 0.0


def log(text: str) -> None:
    """~/.cache/sonata2/quit.log: what was quit and why not (small, rotated)."""
    from .. import logs
    path = logs.path("quit.log")
    try:
        if os.path.getsize(path) > 200_000:
            os.replace(path, path + ".old")
    except OSError:
        pass
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {text}\n")
    except OSError:
        pass


_btime = []


def _boot_time() -> int:
    """When the system booted (read once: it never changes)."""
    if not _btime:
        with open("/proc/stat", encoding="utf-8") as f:
            _btime.append(next(int(ln.split()[1]) for ln in f if ln.startswith("btime ")))
    return _btime[0]


def started_at(pid: int) -> float:
    """When pid started (epoch seconds), 0 unknown."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            ticks = int(f.read().rsplit(")", 1)[1].split()[19])
        return _boot_time() + ticks / CLK
    except (OSError, ValueError, IndexError, StopIteration):
        return 0.0


def wanted() -> bool:
    from .. import config
    from . import dock
    return bool(config.load("dock", dock.DEFAULTS).get("quit_on_close"))


# what launched an app, never part of it
BOUNDARY = ("systemd", "wayfire", "dbus-daemon", "dbus-broker", "xdg-desktop-portal", "greetd", "login",
            "bash", "fish", "zsh", "sh", "dash", "tmux", "screen")   # a terminal's shell (an app's own script has its name)


def proc_table() -> dict:
    """{pid: (ppid, comm, cmdline)} of every process (from /proc)."""
    out = {}
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/stat", encoding="utf-8", errors="replace") as f:
                st = f.read()
            comm = st[st.index("(") + 1:st.rindex(")")]
            ppid = int(st.rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{d}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except (OSError, ValueError, IndexError):
            continue
        out[int(d)] = (ppid, comm, cmd)
    return out


def app_root(pid: int, table: dict) -> int:
    """The topmost process of pid's app: its ancestors up to (not including)
    what launched it -- Sonata, systemd, the compositor -- or init."""
    seen = 0
    while seen < 64:
        ppid = table.get(pid, (0, "", ""))[0]
        if ppid <= 1 or ppid not in table:
            return pid
        _p, comm, cmd = table[ppid]
        if quitapps.OWN in cmd or comm in BOUNDARY or comm.startswith("systemd"):
            return pid
        pid, seen = ppid, seen + 1
    return pid


def subtree(root: int, table: dict) -> list:
    """root and every process under it, root first."""
    kids = {}
    for pid, (ppid, _c, _cmd) in table.items():
        kids.setdefault(ppid, []).append(pid)
    out, todo = [], [root]
    while todo:
        p = todo.pop(0)
        if p in out:
            continue
        out.append(p)
        todo += kids.get(p, [])
    return out


class QuitOnClose:
    def __init__(self, manager, views=quitapps._views, kill=os.kill, alive=quitapps._alive,
                 enabled=wanted, table=proc_table, later=GLib.timeout_add, launched=launched_at,
                 started=started_at, now=time.time):
        self.manager, self.views, self.kill, self.alive = manager, views, kill, alive
        self.enabled, self.table, self.later = enabled, table, later
        self.launched, self.started, self.now = launched, started, now
        self.closed_at = {}
        self.open = set()                 # apps (keys) with a window now
        self.pids = {}                    # key -> its processes, read while it had a window
        manager.listeners.append(self.changed)
        self.changed()

    @staticmethod
    def key_of(app_id: str) -> str:
        from .. import apps
        return apps.match_app_id(app_id or "") or (app_id or "")

    def _keys(self) -> set:
        return {self.key_of(t.app_id) for t in self.manager.toplevels
                if t.app_id and quitapps.OWN not in t.app_id}

    def changed(self) -> None:
        now = self._keys()
        new, gone = now - self.open, self.open - now
        # new apps, and open ones whose window the compositor hadn't listed yet
        # (the Claude app kept running: no process was known for it)
        need = new | {k for k in now if not self.pids.get(k)}
        if need:                                        # (not on every title change)
            views = self.views()
            for k in need:
                self.pids[k] = quitapps.app_pids(views, key=k) or self.pids.get(k, set())
        for k in gone:
            self.closed_at[k] = self.now()
            self.later(GRACE_MS, lambda k=k: self.maybe_quit(k) and False)
        self.open = now

    def maybe_quit(self, key: str) -> bool:
        if key in self._keys():                         # a window came back
            return False
        pids = self.pids.pop(key, set())
        closed = self.closed_at.pop(key, self.now())
        if not self.enabled():
            return False
        if self.launched(key) >= closed:                # opened again meanwhile
            log(f"{key}: opened again, kept")
            return False
        if not pids:
            log(f"{key}: no process known, nothing quit")
            return False
        windows = {v.get("pid") for v in self.views() if v.get("type") == "toplevel"}
        table = self.table()
        targets, roots = [], []
        for pid in pids:
            if not self.alive(pid) or pid in windows:
                continue
            root = app_root(pid, table)
            tree = subtree(root, table)
            if windows & set(tree):
                log(f"{key}: a window under it (a game it started), kept")
                return False
            if any(self.started(p) > closed for p in tree):
                log(f"{key}: a process started after it closed (opened again), kept")
                return False
            targets += [p for p in tree if p not in targets]
            if root not in roots:
                roots.append(root)
        targets = [p for p in targets if quitapps.OWN not in table.get(p, (0, "", ""))[2]]
        # SIGTERM to the app itself (a launcher script: the program it runs),
        # which closes its own helpers: SIGTERM to every helper at once left
        # Claude's main process alive without its GPU process, and it couldn't
        # be opened again until killed by hand (Vini)
        first = list(roots)
        for r in roots:
            if table.get(r, (0, "", ""))[1].endswith(".sh") or table.get(r, (0, "", ""))[1] in BOUNDARY:
                first += [p for p, (pp, _c, _m) in table.items() if pp == r and p not in first]
        first = [p for p in first if p in targets]
        log(f"{key}: quit {first} (then all of {targets} after {KILL_MS} ms)")
        quit_any = False
        for pid in first:
            try:
                self.kill(pid, signal.SIGTERM)
                quit_any = True
            except OSError:
                pass

        def finish():
            # every process read once (Vini: closing apps lagged -- the Dock read all
            # of /proc again for each of the app's processes, on its main loop)
            now = self.table()
            left = [p for p in targets if self.alive(p) and now.get(p, (0, "", ""))[2] ==
                    table.get(p, (0, "", ""))[2]]          # the same process, not a reused pid
            if left:
                log(f"{key}: killed {left}")
            for pid in left:
                try:
                    self.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
            return False
        self.later(KILL_MS, finish)
        return quit_any
