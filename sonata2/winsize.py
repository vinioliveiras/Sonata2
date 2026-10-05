"""Every Sonata app's window opens at the size it last had (macOS).

windows.json: {key: {"width", "height", "maximized"}} -- one key per app
("files", "notes", "webapp-<id>"...); the size is the window's unmaximized
one, so a window closed maximized opens maximized and un-maximizes to it.
No GTK here: web apps in Chrome save their size from a process without it
(ui.window.remember_size is the GTK side)."""
import json
import os

from . import config

NAME = "windows"
MIN = (240, 160)          # smaller: a glitch, not a size someone chose


def saved(key: str):
    """{"width", "height", "maximized"} for `key`, or None."""
    try:
        with open(os.path.join(config.CONFIG_DIR, NAME + ".json"), encoding="utf-8") as f:
            st = json.load(f).get(key)
        w, h = int(st["width"]), int(st["height"])
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None
    if w < MIN[0] or h < MIN[1]:
        return None
    return {"width": w, "height": h, "maximized": bool(st.get("maximized"))}


def save(key: str, width: int, height: int, maximized: bool = False) -> bool:
    """Kept for next time (not written when unchanged)."""
    if width < MIN[0] or height < MIN[1]:
        return False
    st = {"width": int(width), "height": int(height), "maximized": bool(maximized)}
    if saved(key) == st:
        return False
    config.update(NAME, **{key: st})
    return True


def size_or(key: str, w: int, h: int) -> tuple:
    """The saved (width, height), else (w, h)."""
    st = saved(key)
    return (st["width"], st["height"]) if st else (int(w), int(h))
