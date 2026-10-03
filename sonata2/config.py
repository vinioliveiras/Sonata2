"""User settings, stored as JSON in $XDG_CONFIG_HOME/sonata2/<name>.json.

Missing keys fall back to the defaults passed by the caller, so a config file
written by an older version keeps working."""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sonata2")


def load(name: str, defaults: dict) -> dict:
    data = dict(defaults)
    try:
        with open(os.path.join(CONFIG_DIR, name + ".json"), encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            data.update({k: v for k, v in stored.items() if k in defaults})
    except (OSError, ValueError):
        pass
    return data


def atomic_write(path: str, data: bytes, fsync: bool = True) -> None:
    """Replace `path` with `data` in one step: a unique temp file in the same
    folder (several processes write the same files), fsync, then rename --
    never a half-written file, never another writer's temp."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            if fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def _locked(name: str):
    """One writer at a time for <name>.json across Sonata's processes."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(os.path.join(CONFIG_DIR, "." + name + ".lock"), "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX)
        except OSError:
            pass
        yield


def _save(name: str, data: dict) -> None:
    atomic_write(os.path.join(CONFIG_DIR, name + ".json"), json.dumps(data, indent=2).encode("utf-8"),
                 fsync=False)


def save(name: str, data: dict) -> None:
    with _locked(name):
        _save(name, data)


def update(name: str, **values) -> None:
    """Change some keys of <name>.json, keeping every other key as stored
    (read, change and write under one lock: no lost update)."""
    with _locked(name):
        try:
            with open(os.path.join(CONFIG_DIR, name + ".json"), encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data.update(values)
        _save(name, data)


def watch(name: str, callback):
    """Call callback() when <name>.json changes on disk (e.g. from the Settings
    app). Returns the monitor -- keep a reference to it."""
    from gi.repository import Gio
    os.makedirs(CONFIG_DIR, exist_ok=True)
    mon = Gio.File.new_for_path(os.path.join(CONFIG_DIR, name + ".json")).monitor_file(
        Gio.FileMonitorFlags.WATCH_MOVES, None)

    def changed(_m, _f, _o, event):
        if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED,
                     Gio.FileMonitorEvent.RENAMED, Gio.FileMonitorEvent.MOVED_IN):
            callback()
    mon.connect("changed", changed)
    return mon
