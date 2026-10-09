"""When Sonata's background work steps back: the screen is locked (nobody
sees the menu bar) or a fullscreen app -- a game -- has the focus (it gets
the machine to itself; Vini: games must keep their full performance).

Periodic jobs ask before each run and catch up when it ends:

    quiet.paused()            # locked or fullscreen in front
    quiet.locked()            # the lock screen holds the session (only that)
    quiet.watch(cb)           # cb(paused) when it changes; returns a handle to cancel()

Both states are files other processes write (idlelock.marker(),
gamemode.STATE), so it works the same in every Sonata process. The file
monitors are made once per process, whatever the number of watchers."""
import os

_listeners = []
_mons = []
_last = {"v": None}


def locked() -> bool:
    from .shell import idlelock
    return idlelock.is_locked()


def fullscreen() -> bool:
    from . import gamemode
    return gamemode.active()


def paused() -> bool:
    return locked() or fullscreen()


class _Handle:
    def __init__(self, cb):
        self.cb = cb

    def cancel(self) -> None:
        if self in _listeners:
            _listeners.remove(self)


def _changed(*_a) -> None:
    v = paused()
    if v == _last["v"]:
        return
    _last["v"] = v
    for h in list(_listeners):
        h.cb(v)


def _start() -> None:
    if _mons:
        return
    from gi.repository import Gio
    from . import gamemode
    from .shell import idlelock
    _last["v"] = paused()
    for path in (gamemode.STATE, idlelock.marker()):
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            mon = Gio.File.new_for_path(path).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except Exception:
            continue
        mon.connect("changed", _changed)
        _mons.append(mon)


def watch(cb) -> _Handle:
    """cb(paused) on the main loop whenever paused() changes."""
    _start()
    h = _Handle(cb)
    _listeners.append(h)
    return h
