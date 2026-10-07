"""Locked apps (Vini): "Lock App" in an app's Dock or Launchpad menu, and
opening it asks for the login password first -- only when it opens; a
running app isn't touched. Every launch Sonata makes goes through
apps.py's launch wrapper, which calls gate() first.

Not asked: apps reopened at login (autostart) or after a crash
(open_apps) -- those run inside trusted().

    locked(app_id)            # "spotify.desktop" or "spotify"
    set_locked(app_id, on)
    with trusted(): info.launch([], None)
"""
import contextlib

from . import config

NAME = "applock"
DEFAULTS = {"apps": []}
_trusted = 0


def _id(app_id: str) -> str:
    app_id = app_id or ""
    return app_id if app_id.endswith(".desktop") else app_id + ".desktop"


def locked_apps() -> list:
    apps = config.load(NAME, DEFAULTS).get("apps")
    return [a for a in apps if isinstance(a, str)] if isinstance(apps, list) else []


def locked(app_id: str) -> bool:
    return bool(app_id) and _id(app_id) in locked_apps()


def set_locked(app_id: str, on: bool) -> None:
    keep = [a for a in locked_apps() if a != _id(app_id)]
    if on:
        keep.append(_id(app_id))
    config.save(NAME, {"apps": keep})


@contextlib.contextmanager
def trusted():
    """Launches in here never ask (autostart, reopening after a crash)."""
    global _trusted
    _trusted += 1
    try:
        yield
    finally:
        _trusted -= 1


def gate(info, launch) -> bool:
    """True: `launch` ran or waits for the password (the caller does
    nothing more); False: not locked, the caller launches."""
    app_id = info.get_id() if hasattr(info, "get_id") else None
    if _trusted or not locked(app_id):
        return False
    try:
        from gi.repository import Gdk
        if Gdk.Display.get_default() is None:      # nowhere to ask: never opens unasked
            return True
        from .ui import dialog
    except Exception as e:
        print(f"sonata2: app lock: {e}", flush=True)
        return True
    name = info.get_display_name() if hasattr(info, "get_display_name") else app_id
    def opened():
        with trusted():                            # (the launch comes back through gate)
            launch()
    dialog.ask_password(f"“{name}” is locked", "Enter your password to open it.", opened)
    return True


def ask_unlock(info, done=None) -> None:
    """Unlocking asks the password too (like a locked folder)."""
    from .ui import dialog
    name = info.get_display_name()

    def ok():
        set_locked(info.get_id(), False)
        if done:
            done()
    dialog.ask_password(f"Unlock “{name}”?", "Enter your password to stop asking when it opens.",
                        ok, ok="Unlock")
