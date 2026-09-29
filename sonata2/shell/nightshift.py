"""Night Shift (macOS): warmer colours at night, through wlsunset (wlr
gamma control; the colours reset when it exits, so the menu bar process
keeps it running and restarts it when nightshift.json changes).

Settings: schedule off | sunset (Sunset to Sunrise, from the time zone's
coordinates in zone1970.tab: no location service) | custom (from/to);
"manual" = Turn On Until Tomorrow (constant warmth until 07:00 local);
warmth 0..100 (Less Warm .. More Warm)."""
import datetime
import os
import shutil
import signal
import subprocess

from .. import config

DEFAULTS = {"schedule": "off", "from": "22:00", "to": "07:00", "warmth": 50, "manual_until": ""}
DAY_K = 6500
ZONE_TAB = "/usr/share/zoneinfo/zone1970.tab"


def temperature(warmth: int) -> int:
    """0 -> 5500 K (Less Warm), 100 -> 2700 K (More Warm)."""
    return int(5500 - (max(0, min(100, warmth)) / 100) * 2800)


def _dms(s: str, deg_digits: int) -> float:
    sign = -1 if s[0] == "-" else 1
    s = s[1:]
    d, m, sec = int(s[:deg_digits]), int(s[deg_digits:deg_digits + 2]), int(s[deg_digits + 2:] or 0)
    return sign * (d + m / 60 + sec / 3600)


def tz_location(tz: str = "") -> tuple:
    """(lat, lon) of the time zone's principal city, or None."""
    tz = tz or _local_tz()
    try:
        with open(ZONE_TAB) as f:
            for line in f:
                if line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 3 and parts[2].strip() == tz:
                    c = parts[1]
                    cut = max(c.rfind("+"), c.rfind("-"))
                    lat, lon = c[:cut], c[cut:]
                    return _dms(lat, 2), _dms(lon, 3)
    except OSError:
        pass
    return None


def _local_tz() -> str:
    tz = os.environ.get("TZ", "").lstrip(":")
    if tz:
        return tz
    try:
        link = os.readlink("/etc/localtime")
        return link.split("zoneinfo/", 1)[1]
    except (OSError, IndexError):
        return ""


def manual_active(cfg: dict) -> bool:
    until = cfg.get("manual_until") or ""
    try:
        return datetime.datetime.now() < datetime.datetime.fromisoformat(until)
    except ValueError:
        return False


def until_tomorrow() -> str:
    """macOS: Turn On Until Tomorrow = until 7 AM."""
    now = datetime.datetime.now()
    end = now.replace(hour=7, minute=0, second=0, microsecond=0)
    if end <= now:
        end += datetime.timedelta(days=1)
    return end.isoformat(timespec="minutes")


def command(cfg: dict) -> list:
    """The wlsunset command for these settings, or [] (Night Shift off)."""
    warm = temperature(int(cfg.get("warmth", 50)))
    base = ["wlsunset", "-t", str(warm)]
    if manual_active(cfg):            # constant: both ends warm
        return base + ["-T", str(warm + 1)]
    sched = cfg.get("schedule")
    if sched == "sunset":
        loc = tz_location()
        if loc:
            return base + ["-T", str(DAY_K), "-l", f"{loc[0]:.2f}", "-L", f"{loc[1]:.2f}"]
        sched = "custom"                # unknown time zone: 22:00-07:00
    if sched == "custom":
        # wlsunset: -S sunrise (day starts), -s sunset (night starts)
        return base + ["-T", str(DAY_K), "-S", cfg.get("to", "07:00"), "-s", cfg.get("from", "22:00"), "-d", "1800"]
    return []


class NightShift:
    """Keeps wlsunset in step with nightshift.json (menu bar process)."""

    def __init__(self):
        self.proc = None
        self.cmd = None
        from gi.repository import GLib
        self._pending = 0
        self._mon = config.watch("nightshift", self._later)
        self.apply()
        GLib.timeout_add_seconds(60, lambda: (self.apply(), True)[1])   # "until tomorrow" expiring

    def _later(self, *_a) -> None:
        """Debounced: the warmth slider saves on every step."""
        from gi.repository import GLib
        if self._pending:
            GLib.source_remove(self._pending)

        def run():
            self._pending = 0
            self.apply()
            return False
        self._pending = GLib.timeout_add(400, run)

    @property
    def available(self) -> bool:
        return shutil.which("wlsunset") is not None

    def apply(self) -> None:
        cmd = command(config.load("nightshift", DEFAULTS)) if self.available else []
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


def set_manual(on: bool) -> None:
    cfg = config.load("nightshift", DEFAULTS)
    cfg["manual_until"] = until_tomorrow() if on else ""
    config.save("nightshift", cfg)


def is_on() -> bool:
    """On now (manual, or inside the schedule) -- for the Control Center button."""
    return manual_active(config.load("nightshift", DEFAULTS))
