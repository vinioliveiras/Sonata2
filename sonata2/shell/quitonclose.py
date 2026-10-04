"""Settings > Desktop & Windows > "Quit apps when their last window closes"
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

from gi.repository import GLib

from . import quitapps

GRACE_MS = 1500


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
                 enabled=wanted, table=proc_table, later=GLib.timeout_add):
        self.manager, self.views, self.kill, self.alive = manager, views, kill, alive
        self.enabled, self.table, self.later = enabled, table, later
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
        if new:                                         # (not on every title change: one IPC call per new app)
            views = self.views()
            for k in new:
                self.pids[k] = quitapps.app_pids(views, key=k) or self.pids.get(k, set())
        for k in gone:
            self.later(GRACE_MS, lambda k=k: self.maybe_quit(k) and False)
        self.open = now

    def maybe_quit(self, key: str) -> bool:
        if key in self._keys():                         # a window came back
            return False
        pids = self.pids.pop(key, set())
        if not self.enabled():
            return False
        windows = {v.get("pid") for v in self.views() if v.get("type") == "toplevel"}
        table = self.table()
        targets = []
        for pid in pids:
            if not self.alive(pid) or pid in windows:
                continue
            tree = subtree(app_root(pid, table), table)
            if windows & set(tree):
                return False                            # a game it started still shows
            targets += [p for p in tree if p not in targets]
        quit_any = False
        for pid in targets:
            if quitapps.OWN in table.get(pid, (0, "", ""))[2]:
                continue
            try:
                self.kill(pid, signal.SIGTERM)
                quit_any = True
            except OSError:
                pass
        return quit_any
