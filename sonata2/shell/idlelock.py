"""Security & Privacy > "Require password after the display turns off":
the menu bar process keeps swayidle running with the matching timeout
(ext-idle-notify; Wayfire's idle plugin only blanks the display) and
locks before sleep. Off by default until the lock screen is proven on
the user's hardware (see ROADMAP open questions).

The same swayidle turns the keyboard's backlight off when the display
turns off and back to its level when you come back (brightnessctl saves
and restores it; logind lets it write the LED without root)."""
import glob
import shutil
import signal
import subprocess

from .. import config, wfconfig

# lock_after: seconds after the display turns off (0 = immediately), -1 = never
DEFAULTS = {"lock_after": -1, "lock_before_sleep": False}
LOCK = "sonata2 lock"
LEDS = "/sys/class/leds"
KBD = "*::kbd_backlight"
KBD_OFF = f"brightnessctl -q -d '{KBD}' -s set 0"
KBD_ON = f"brightnessctl -q -d '{KBD}' -r"


def keyboard_light() -> bool:
    """A keyboard backlight brightnessctl can dim (asus::kbd_backlight...)."""
    return bool(glob.glob(f"{LEDS}/{KBD}")) and bool(shutil.which("brightnessctl"))


def command(cfg: dict, dpms: int, kbd: bool = False) -> list:
    args = []
    if kbd and dpms > 0:                          # with the display: off, then back on any input
        args += ["timeout", str(dpms), KBD_OFF, "resume", KBD_ON]
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
        cmd = (command(config.load("security", DEFAULTS), dpms, keyboard_light())
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
