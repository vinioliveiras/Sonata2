"""User settings, stored as JSON in $XDG_CONFIG_HOME/sonata2/<name>.json.

Missing keys fall back to the defaults passed by the caller, so a config file
written by an older version keeps working."""
import json
import os

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


def save(name: str, data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    path = os.path.join(CONFIG_DIR, name + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)   # atomic: never leaves a half-written file


def update(name: str, **values) -> None:
    """Change some keys of <name>.json, keeping every other key as stored."""
    try:
        with open(os.path.join(CONFIG_DIR, name + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data.update(values)
    save(name, data)


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
