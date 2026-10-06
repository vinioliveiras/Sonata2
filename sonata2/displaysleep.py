"""When the display goes dark (Settings > Battery > "Turn display off after").

Sonata darkens it itself (shell/idlepolicy.py: black and the backlight at
zero on a laptop, the keyboard's lights too; any key or move brings it
back). Wayfire's own display timeout stays off (idle/dpms_timeout = -1):
it powered the panel off, and on Vini's laptop (NVIDIA) it never came back
-- "Atomic commit failed: Permission denied" -- until the lid was closed
and opened. The lock screen had been moved off it already; this was the
last place it was still used.

    seconds()          # the user's choice (<= 0: never), 600 by default
    set_seconds(300)
    compositor_off()   # Wayfire's timeout off (at login and on every change)
"""
from . import config, wfconfig

NAME = "display"
DEFAULT = 600


def seconds() -> int:
    """Seconds without input before the display goes dark; <= 0 never. The
    first time: what Wayfire's timeout was set to (Settings wrote it there)."""
    cfg = config.load(NAME, {"off_after": None})
    if cfg["off_after"] is not None:
        try:
            return int(cfg["off_after"])
        except (TypeError, ValueError):
            return DEFAULT
    from .shell import lockdisplay                # (locked now: Wayfire's value from before the lock)
    try:
        old = int(lockdisplay.saved().get("idle/dpms_timeout") or
                  wfconfig.wayfire_get("idle", "dpms_timeout", str(DEFAULT)) or DEFAULT)
    except ValueError:
        old = DEFAULT
    old = old if old != 0 else DEFAULT
    config.update(NAME, off_after=old)
    return old


def set_seconds(value: int) -> None:
    config.update(NAME, off_after=int(value))
    compositor_off()
    try:
        from .shell import monitors
        monitors.share_with_login_screen()       # the login screen goes dark after the same time
    except Exception:
        pass


def compositor_off() -> bool:
    """Wayfire never powers the display off itself; True when it had to be changed."""
    seconds()                                    # (the old value kept first)
    if (wfconfig.wayfire_get("idle", "dpms_timeout", "") or "").strip() == "-1":
        return False
    wfconfig.wayfire_set("idle", "dpms_timeout", -1)
    return True
