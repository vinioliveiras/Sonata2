"""The display turns off soon after the screen is locked (macOS), and
comes back on any key or move (Vini: locked, the screen stayed on).

While locked:
- Wayfire's idle timeout is LOCKED_DPMS_S (this session only: the copy of
  wayfire.ini Wayfire reads), and a full-screen app (a video, a game left
  open under the lock) no longer keeps the display on;
- with wlopm installed, the lock screen also turns the displays off itself
  after LOCKED_DPMS_S without input -- an app holding the display awake
  (idle inhibit) can't keep it on behind the lock;
- the keyboard's backlight (and RGB devices) go dark then too, whatever
  keeps the session awake, and come back on any input.
The values from before are kept in $XDG_RUNTIME_DIR and put back on unlock
(or by the menu bar when it starts, after a lock screen that crashed)."""
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
    try:
        prev = int(saved().get("idle/dpms_timeout") or 600)
    except ValueError:
        prev = 600
    # sooner, never later than you chose (a shorter one stays); "never" too: locked, it goes dark
    wfconfig.runtime_set("idle", "dpms_timeout", prev if 0 < prev < LOCKED_DPMS_S else LOCKED_DPMS_S)
    wfconfig.runtime_set("idle", "disable_on_fullscreen", False)


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
    """The keyboard's backlight (and RGB devices, with OpenRGB) off / back
    as they were -- with the displays (Vini: the keyboard stayed lit with
    the screen locked, an app in the background keeping the session awake)."""
    from . import idlelock
    cmds = []
    if idlelock.keyboard_light():
        cmds.append(idlelock.KBD_ON if on else idlelock.KBD_OFF)
    if idlelock.rgb_lights():
        cmds.append(idlelock.RGB_ON if on else idlelock.RGB_OFF)
    for c in cmds:
        try:
            subprocess.Popen(["sh", "-c", c], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
