"""Security & Privacy > "Require password after the display turns off":
the menu bar process keeps swayidle running with the matching timeout
(ext-idle-notify; Wayfire's idle plugin only blanks the display) and
locks before sleep. On by default: the display off, then the lock
screen (macOS's "Require password immediately").

The same swayidle turns the keyboard's backlight off when the display
turns off and back to its level when you come back (brightnessctl saves
and restores it; logind lets it write the LED without root). RGB devices
(USB keyboards, mice: their light isn't the system's) go dark too through
OpenRGB when it is installed: the current look saved as a profile, all
off, the profile loaded back on input."""
import glob
import os
import shutil
import shlex
import signal
import subprocess
import sys
import time

from .. import config

# lock_after: seconds after the display turns off (0 = immediately), -1 = never.
# On by default (Vini): locked when the display turns off and before sleep, like macOS
DEFAULTS = {"lock_after": 0, "lock_before_sleep": True, "usb_protection": True,
            "rgb_dark": False}       # RGB lights (OpenRGB) dark too: chosen in Settings (rgblights.py)
# swayidle -w waits for its command: `sonata2 lock` itself only quits on
# unlock, so every idle timeout / before-sleep that came meanwhile waited in
# line and locked again right after each unlock (Vini: the password 3 times
# on waking). lock-wait starts the lock on its own and returns once locked.
LOCK = "sonata2 lock-wait"
LEDS = "/sys/class/leds"
KBD = "*::kbd_backlight"
# The lights' level before Sonata turned them off, in a file that outlives a
# restart. Two "off"s in a row (the idle timeout and the lock screen both go
# dark) must not save the dark level over the real one, and a restart while
# dark must not leave them off (Vini: the keyboard stayed unlit -- brightnessctl's
# own save kept 0 the second time, and was lost on restart).
_STATE = shlex.quote(os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                  "sonata2", "lights-before-dark"))
KBD_OFF = (f"b=$(brightnessctl -d '{KBD}' g 2>/dev/null); "
           f"if [ \"${{b:-0}}\" -gt 0 ] 2>/dev/null; then mkdir -p \"$(dirname {_STATE})\"; "
           f"echo \"$b\" > {_STATE}; brightnessctl -q -d '{KBD}' set 0; fi")
KBD_ON = (f"if [ -s {_STATE} ]; then brightnessctl -q -d '{KBD}' set \"$(cat {_STATE})\"; "
          f"rm -f {_STATE}; fi")
# the panel's backlight while the lock screen is dark (lockdisplay.dim): the same
# once-saved level, in a file of its own
_BL_STATE = shlex.quote(os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                     "sonata2", "backlight-before-dark"))
BL_OFF = (f"b=$(brightnessctl -c backlight g 2>/dev/null); "
          f"if [ \"${{b:-0}}\" -gt 0 ] 2>/dev/null; then mkdir -p \"$(dirname {_BL_STATE})\"; "
          f"echo \"$b\" > {_BL_STATE}; brightnessctl -q -c backlight set 0; fi")
BL_ON = (f"if [ -s {_BL_STATE} ]; then brightnessctl -q -c backlight set \"$(cat {_BL_STATE})\"; "
         f"rm -f {_BL_STATE}; fi")

RGB_PROFILE = "sonata-idle"
# OpenRGB through rgblights.py: it never keeps dark colours to put back
# (Vini: the laptop keyboard was saved black, and stayed black)
_PKG = shlex.quote(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_PY = shlex.quote(sys.executable or "python3")
RGB_OFF = f"PYTHONPATH={_PKG} {_PY} -m sonata2.rgblights off"
RGB_ON = f"PYTHONPATH={_PKG} {_PY} -m sonata2.rgblights on"
_LIGHTS_LOCK = shlex.quote(os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                        "sonata2", "lights.lock"))


def serial(cmd: str) -> str:
    """One lights command at a time, whoever runs it (the lock screen, the
    idle timer, swayidle): OpenRGB takes seconds to save the colours, and a
    wake-up meanwhile ran "back on" before there was anything to put back
    -- then "off" finished, and the keyboard stayed dark (Vini)."""
    if not shutil.which("flock"):
        return cmd
    return f"mkdir -p \"$(dirname {_LIGHTS_LOCK})\" && flock {_LIGHTS_LOCK} sh -c {shlex.quote(cmd)}"


def marker() -> str:
    """Present while the lock screen holds the session (its pid inside)."""
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-locked")


def mark_locked(on: bool) -> None:
    try:
        if on:
            with open(marker(), "w") as f:
                f.write(str(os.getpid()))
        else:
            os.unlink(marker())
    except OSError:
        pass


def is_locked() -> bool:
    try:
        with open(marker()) as f:
            pid = int(f.read().strip() or 0)
        os.kill(pid, 0)                              # a lock screen that crashed left it behind
        return pid > 0
    except (OSError, ValueError):
        return False


def lock_and_wait(argv: list, timeout: float = 5.0) -> int:
    """`sonata2 lock-wait`: start the lock screen detached and return once it
    holds the session (before-sleep: the screen is locked before the machine
    sleeps), or at once when it already does."""
    if is_locked():
        return 0
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except OSError:
        return 1
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if is_locked():
            return 0
        time.sleep(0.05)
    return 1


def rgb_lights() -> bool:
    """RGB lights go dark too: OpenRGB installed and chosen in Settings
    (off by default -- the laptop's own keyboard goes dark by its backlight)."""
    return bool(shutil.which("openrgb")) and bool(config.load("security", DEFAULTS).get("rgb_dark"))


def keyboard_light() -> bool:
    """A keyboard backlight brightnessctl can dim (asus::kbd_backlight...)."""
    return bool(glob.glob(f"{LEDS}/{KBD}")) and bool(shutil.which("brightnessctl"))


def command(cfg: dict, dpms: int, kbd: bool = False, rgb: bool = False) -> list:
    args = []
    if kbd and dpms > 0:                          # with the display: off, then back on any input
        args += ["timeout", str(dpms), serial(KBD_OFF), "resume", serial(KBD_ON)]
    if rgb and dpms > 0:                          # (in the background: OpenRGB takes a few seconds)
        args += ["timeout", str(dpms), f"sh -c {shlex.quote(serial(RGB_OFF))} &", "resume",
                 f"sh -c {shlex.quote(serial(RGB_ON))} &"]
    after = int(cfg.get("lock_after", -1))
    if after >= 0:
        base = dpms if dpms > 0 else 600          # display never sleeps: count from 10 min idle
        args += ["timeout", str(max(5, base + after)), LOCK]
    if cfg.get("lock_before_sleep"):
        args += ["before-sleep", LOCK]
    return ["swayidle", "-w"] + args if args else []


class IdleLock:
    def __init__(self):
        from gi.repository import GLib
        self.proc, self.cmd = None, None
        from .. import displaysleep
        displaysleep.compositor_off()           # Sonata darkens the display itself (displaysleep.py)
        self.policy = self._policy()            # input idle: only media keeps it awake (idlepolicy.py)
        self._mon = config.watch("security", self.apply)
        if not is_locked():                     # a lock screen that crashed: the display's timeout back
            from . import lockdisplay
            lockdisplay.unlocked()
            lockdisplay.lights(True)            # and the lights, left dark by a restart while locked
            lockdisplay.dim(False)              # and the panel's backlight
        self.apply()
        GLib.timeout_add_seconds(60, lambda: (self.apply(), True)[1])   # dpms timeout changed in Settings

    def _policy(self):
        """Sonata's own idle handling when the compositor tells input idle
        apart from apps keeping the session awake (else None: swayidle)."""
        try:
            from ..wl.idlewatch import IdleWatch
            w = IdleWatch()
        except Exception:
            return None
        if not (w.ok and w.input_idle):
            w.close()
            return None
        from . import idlepolicy, lockdisplay

        def dark(on):
            lockdisplay.lights(not on)              # the keyboard's light (and RGB) with the displays
            from . import blackout                  # black, no pointer (Vini: it only dimmed)
            blackout.show() if on else blackout.hide()
            if lockdisplay.has_backlight():
                # a laptop: the backlight to zero, the display stays on -- powering
                # it off and on again failed on Vini's (NVIDIA: the screen came
                # back only by closing and opening the lid)
                lockdisplay.dim(on)
            else:
                w.displays(not on)

        def lock():
            try:
                subprocess.Popen(["sh", "-c", LOCK], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                pass
        return idlepolicy.IdlePolicy(w, dark, lock)

    def apply(self, *_a) -> None:
        from .. import displaysleep
        dpms = displaysleep.seconds()           # Sonata's own: Wayfire's timeout stays off
        cfg = config.load("security", DEFAULTS)
        if self.policy is not None:             # timeouts here; swayidle only locks before sleep
            self.policy.apply(dpms, int(cfg.get("lock_after", -1)))
            cmd = (["swayidle", "-w", "before-sleep", LOCK]
                   if cfg.get("lock_before_sleep") and shutil.which("swayidle") else [])
        else:
            cmd = command(cfg, dpms, keyboard_light(), rgb_lights()) if shutil.which("swayidle") else []
        if cmd == self.cmd and (not cmd or (self.proc and self.proc.poll() is None)):
            return
        self.stop()
        self.cmd = cmd
        if cmd:
            try:
                self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
            except OSError:
                self.proc = None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
