"""Night Shift (macOS): warmer colours at night, through wlsunset (wlr
gamma control; the colours reset when it exits, so the menu bar process
keeps it running and restarts it when nightshift.json changes).

Settings: schedule off | sunset (Sunset to Sunrise, from the time zone's
coordinates in zone1970.tab: no location service) | custom (from/to);
"manual" = Turn On Until Tomorrow (constant warmth until 07:00 local);
warmth 0..100 (Less Warm .. More Warm)."""
import datetime
import math
import os
import shutil
import signal
import subprocess
import threading
import time

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


def _hm(s: str):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def sun_times(lat: float, lon: float, day: datetime.date):
    """(sunrise, sunset) as Unix times for that date (sunrise equation, the
    same model wlsunset uses); infinities during polar night / day."""
    jd = datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp() \
        / 86400 + 2440587.5                       # midnight UTC: n is this day's number
    n = math.ceil(jd - 2451545.0 + 0.0008)
    j = n - lon / 360
    m = math.radians((357.5291 + 0.98560028 * j) % 360)
    c = 1.9148 * math.sin(m) + 0.02 * math.sin(2 * m) + 0.0003 * math.sin(3 * m)
    lam = math.radians((math.degrees(m) + c + 180 + 102.9372) % 360)
    transit = 2451545.0 + j + 0.0053 * math.sin(m) - 0.0069 * math.sin(2 * lam)
    dec = math.asin(math.sin(lam) * math.sin(math.radians(23.4397)))
    phi = math.radians(lat)
    cos_w = (math.sin(math.radians(-0.833)) - math.sin(phi) * math.sin(dec)) / (math.cos(phi) * math.cos(dec))
    if cos_w > 1:                                 # polar night: the sun never rises
        return math.inf, math.inf
    if cos_w < -1:                                # polar day: it never sets
        return -math.inf, math.inf
    w = math.degrees(math.acos(cos_w)) / 360
    unix = lambda jd: (jd - 2440587.5) * 86400  # noqa: E731
    return unix(transit - w), unix(transit + w)


def in_schedule(cfg: dict, now: datetime.datetime = None) -> bool:
    """True while the schedule makes the screen warm (manual mode aside)."""
    now = now or datetime.datetime.now()
    sched = cfg.get("schedule")
    if sched == "sunset":
        loc = tz_location()
        if loc:
            times = sun_times(loc[0], loc[1], now.date())
            t = now.timestamp() if now.tzinfo else time.mktime(now.timetuple())
            return t < times[0] or t >= times[1]
        sched = "custom"                          # same fallback as command()
    if sched != "custom":
        return False
    try:
        start, end, cur = _hm(cfg.get("from", "22:00")), _hm(cfg.get("to", "07:00")), now.hour * 60 + now.minute
    except (ValueError, AttributeError):
        return False
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end              # crosses midnight


class NightShift:
    """Keeps wlsunset in step with nightshift.json (menu bar process)."""

    def __init__(self):
        self.proc = None
        self.cmd = None
        self._gen = 0
        self._waiting = False
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
        if cmd == self.cmd and (not cmd or self._waiting or (self.proc and self.proc.poll() is None)):
            return
        self.cmd = cmd
        self._gen += 1
        if self.stop() and cmd:
            self._waiting = True              # the new one starts once the old one let go of the gamma
            return
        self._start(self._gen)

    def _start(self, gen: int) -> bool:
        if gen != self._gen:                   # settings changed again meanwhile
            return False
        self._waiting = False
        if self.cmd:
            try:
                self.proc = subprocess.Popen(self.cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL)
            except OSError:
                self.proc = None
        return False

    def stop(self) -> bool:
        """SIGTERM wlsunset and reap it off the main loop (it can take up to
        2 s to fade the gamma back). True when one was running."""
        proc, self.proc = self.proc, None
        if not proc or proc.poll() is not None:
            return False
        proc.send_signal(signal.SIGTERM)
        gen = self._gen

        def reap():
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            from gi.repository import GLib
            GLib.idle_add(self._start, gen)
        threading.Thread(target=reap, daemon=True).start()
        return True


def set_manual(on: bool) -> None:
    cfg = config.load("nightshift", DEFAULTS)
    cfg["manual_until"] = until_tomorrow() if on else ""
    config.save("nightshift", cfg)


def is_on() -> bool:
    """On now (manual, or inside the schedule) -- for the Control Center button."""
    cfg = config.load("nightshift", DEFAULTS)
    return manual_active(cfg) or in_schedule(cfg)
