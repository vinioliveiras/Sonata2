"""The screen goes dark soon after it is locked (macOS), and comes back on
any key or move (Vini: locked, the screen stayed on).

Dark, not off: the lock screen fades to black and the panel's backlight
goes to zero (and the keyboard's lights). Turning the display itself off
and on again (DPMS) failed on Vini's laptop (the NVIDIA driver refused the
mode set: "Atomic commit failed: Permission denied") and the screen came
back only by closing and opening the lid. So while locked Wayfire's own
display timeout is off too; your setting is put back on unlock (kept in
$XDG_RUNTIME_DIR, also put back by the menu bar after a lock screen that
crashed). Set to never turn off: it stays lit."""
import json
import os
import shutil
import subprocess

from .. import wfconfig

LOCKED_DPMS_S = 30
KEYS = (("idle", "dpms_timeout"), ("idle", "disable_on_fullscreen"))


def _state() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-lock-display.json")


def saved() -> dict:
    """{"idle/dpms_timeout": "600", ...} from before the lock, {} when not locked."""
    try:
        with open(_state(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def locked() -> None:
    if not saved():                               # (a second lock keeps the first one's values)
        try:
            with open(_state(), "w", encoding="utf-8") as f:
                json.dump({f"{s}/{k}": wfconfig.wayfire_get(s, k) for s, k in KEYS}, f)
        except OSError:
            return
    # the lock screen darkens it itself (dim()): Wayfire never powers it off meanwhile
    wfconfig.runtime_set("idle", "dpms_timeout", -1)


def dark_seconds():
    """How long the locked screen stays lit: LOCKED_DPMS_S, or your own
    shorter time; None when you chose that the display never turns off
    (Vini: set to never, it went dark behind the lock anyway)."""
    from .. import displaysleep
    prev = displaysleep.seconds()
    if prev <= 0:
        return None
    return min(prev, LOCKED_DPMS_S)


def unlocked() -> None:
    old = saved()
    if not old:
        return
    for s, k in KEYS:
        wfconfig.runtime_set(s, k, old.get(f"{s}/{k}") or None)     # (none set before: Wayfire's default)
    try:
        os.unlink(_state())
    except OSError:
        pass


def has_backlight() -> bool:
    """A built-in panel whose backlight can go to zero (a laptop)."""
    import glob
    return bool(glob.glob("/sys/class/backlight/*")) and bool(shutil.which("brightnessctl"))


def dim(dark: bool) -> None:
    """The built-in panel's backlight to zero / back to its level (in the
    background, after any lights command before it)."""
    from . import idlelock
    if not shutil.which("brightnessctl"):
        return
    try:
        subprocess.Popen(["sh", "-c", idlelock.serial(idlelock.BL_OFF if dark else idlelock.BL_ON)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        pass


def displays(on: bool) -> bool:
    """All displays on / off (wlr-output-power-management, wlopm); False
    when wlopm isn't installed."""
    if not shutil.which("wlopm"):
        return False
    try:
        subprocess.Popen(["wlopm", "--on" if on else "--off", "*"], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def lights(on: bool) -> None:
    """The keyboard's backlight off / back
    as they were -- with the displays (Vini: the keyboard stayed lit with
    the screen locked, an app in the background keeping the session awake).
    Each runs on its own (it outlives the lock screen, which quits right after
    unlocking) and waits for the one before it (idlelock.serial)."""
    from . import idlelock
    cmds = []
    if idlelock.keyboard_light():
        cmds.append(idlelock.KBD_ON if on else idlelock.KBD_OFF)
    for c in cmds:
        try:
            subprocess.Popen(["sh", "-c", idlelock.serial(c)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
