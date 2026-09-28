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
