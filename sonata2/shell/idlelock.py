"""Security & Privacy > "Require password after the display turns off":
the menu bar process keeps swayidle running with the matching timeout
(ext-idle-notify; Wayfire's idle plugin only blanks the display) and
locks before sleep. Off by default until the lock screen is proven on
the user's hardware (see ROADMAP open questions).

The same swayidle turns the keyboard's backlight off when the display
turns off and back to its level when you come back (brightnessctl saves
and restores it; logind lets it write the LED without root). RGB devices
(USB keyboards, mice: their light isn't the system's) go dark too through
OpenRGB when it is installed: the current look saved as a profile, all
off, the profile loaded back on input."""
import glob
import os
import shutil
import signal
import subprocess
import time

from .. import config, wfconfig

# lock_after: seconds after the display turns off (0 = immediately), -1 = never
DEFAULTS = {"lock_after": -1, "lock_before_sleep": False, "usb_protection": True}
# swayidle -w waits for its command: `sonata2 lock` itself only quits on
# unlock, so every idle timeout / before-sleep that came meanwhile waited in
# line and locked again right after each unlock (Vini: the password 3 times
# on waking). lock-wait starts the lock on its own and returns once locked.
LOCK = "sonata2 lock-wait"
LEDS = "/sys/class/leds"
KBD = "*::kbd_backlight"
KBD_OFF = f"brightnessctl -q -d '{KBD}' -s set 0"
KBD_ON = f"brightnessctl -q -d '{KBD}' -r"


RGB_PROFILE = "sonata-idle"
RGB_OFF = f"openrgb --save-profile {RGB_PROFILE} >/dev/null 2>&1; openrgb --mode off >/dev/null 2>&1"
RGB_ON = f"openrgb --profile {RGB_PROFILE} >/dev/null 2>&1"


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
    """OpenRGB installed: USB keyboards' and mice's RGB can go dark."""
    return bool(shutil.which("openrgb"))


def keyboard_light() -> bool:
    """A keyboard backlight brightnessctl can dim (asus::kbd_backlight...)."""
    return bool(glob.glob(f"{LEDS}/{KBD}")) and bool(shutil.which("brightnessctl"))


def command(cfg: dict, dpms: int, kbd: bool = False, rgb: bool = False) -> list:
    args = []
    if kbd and dpms > 0:                          # with the display: off, then back on any input
        args += ["timeout", str(dpms), KBD_OFF, "resume", KBD_ON]
    if rgb and dpms > 0:                          # (in the background: OpenRGB takes a few seconds)
        args += ["timeout", str(dpms), f"sh -c '{RGB_OFF}' &", "resume", f"sh -c '{RGB_ON}' &"]
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
        self._mon = config.watch("security", self.apply)
        self.apply()
        GLib.timeout_add_seconds(60, lambda: (self.apply(), True)[1])   # dpms timeout changed in Settings

    def apply(self, *_a) -> None:
        try:
            dpms = int(wfconfig.wayfire_get("idle", "dpms_timeout", "600") or 600)
        except ValueError:
            dpms = 600
        cmd = (command(config.load("security", DEFAULTS), dpms, keyboard_light(), rgb_lights())
               if shutil.which("swayidle") else [])
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
