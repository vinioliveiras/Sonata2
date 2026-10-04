"""Settings > Desktop & Windows > "Quit apps when their last window closes"
(Vini: closing Steam with its window's X left it running in the background).

When an app's last window goes and none comes back within GRACE_MS (Steam
swaps its sign-in window for the main one), the app's processes get SIGTERM
-- the polite quit (quitapps.py). A process with a window of another app
under it (a game Steam started) is left alone, and so is Sonata itself.

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


def ancestors(pid: int) -> set:
    """pid's parents up to init (from /proc)."""
    out, seen = set(), 0
    while pid > 1 and seen < 64:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
                pid = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
        out.add(pid)
        seen += 1
    return out


class QuitOnClose:
    def __init__(self, manager, views=quitapps._views, kill=os.kill, alive=quitapps._alive,
                 enabled=wanted, parents=ancestors, later=GLib.timeout_add):
        self.manager, self.views, self.kill, self.alive = manager, views, kill, alive
        self.enabled, self.parents, self.later = enabled, parents, later
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
        quit_any = False
        for pid in pids:
            if not self.alive(pid) or pid in windows:
                continue
            if any(pid in self.parents(w) for w in windows if isinstance(w, int)):
                continue                                # a game it started still shows
            try:
                self.kill(pid, signal.SIGTERM)
                quit_any = True
            except OSError:
                pass
        return quit_any
