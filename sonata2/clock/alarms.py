"""Clock's alarms: the list (~/.local/share/sonata2-data/clock/alarms.json)
and when each one goes off next. No GTK here: the Clock window edits the
list, the menu bar process (shell/alarmservice.py) rings them.

alarm = {"id", "hour", "minute", "label", "repeat": [weekday, ...] (0 =
Monday; empty: once), "enabled", "snooze": bool, "sound": SOUNDS key}

A one-time alarm turns itself off once it has gone off (iPhone / macOS)."""
import datetime as dt
import json
import os
import uuid

FILE = "alarms.json"
# the alarm's sound: key -> (name shown, file in data/sounds). Soft ones first;
# Classic is the freedesktop alarm (high and sharp: no longer the default)
SOUNDS = {"morning": ("Morning", "sonata-alarm-morning"), "chimes": ("Chimes", "sonata-alarm-chimes"),
          "harp": ("Harp", "sonata-alarm-harp"), "sunrise": ("Sunrise", "sonata-alarm-sunrise"),
          "pulse": ("Pulse", "sonata-alarm-pulse"), "classic": ("Classic", "alarm-clock-elapsed")}
SOUND = "morning"
SNOOZE_MIN = 9                         # iPhone / macOS
RING_MAX_S = 15 * 60                   # an alarm nobody stops goes quiet after this
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def sound_path(key: str) -> str:
    """The file of an alarm sound (the default one for an unknown key)."""
    name = SOUNDS.get(key, SOUNDS[SOUND])[1]
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sounds",
                        name + ".oga")


def path() -> str:
    from .. import userdata
    return os.path.join(userdata.folder("clock"), FILE)


def new(hour: int = 7, minute: int = 0) -> dict:
    return {"id": uuid.uuid4().hex[:12], "hour": hour, "minute": minute, "label": "Alarm",
            "repeat": [], "enabled": True, "snooze": True, "sound": SOUND}


def _clean(a) -> dict:
    if not isinstance(a, dict):
        return None
    try:
        out = dict(new(), **a)
        out["hour"], out["minute"] = int(out["hour"]) % 24, int(out["minute"]) % 60
        out["repeat"] = sorted({int(d) for d in out.get("repeat") or [] if 0 <= int(d) <= 6})
        out["enabled"], out["snooze"] = bool(out["enabled"]), bool(out["snooze"])
        out["label"] = str(out.get("label") or "")
        out["sound"] = out["sound"] if out.get("sound") in SOUNDS else SOUND
        return out
    except (TypeError, ValueError):
        return None


def load() -> list:
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    items = data.get("alarms") if isinstance(data, dict) else None
    # {} or "alarms": null must read as no alarms (the menu bar's AlarmService loads this)
    alarms = [_clean(a) for a in (items if isinstance(items, list) else [])]
    return sorted((a for a in alarms if a), key=lambda a: (a["hour"], a["minute"]))


def save(alarms: list) -> None:
    from ..config import atomic_write
    # unique temp + rename: the Clock window and the menu bar may write at once
    atomic_write(path(), json.dumps({"alarms": alarms}, indent=1).encode("utf-8"))


def next_time(a: dict, after: dt.datetime) -> dt.datetime:
    """When the alarm goes off next, strictly after `after` (None when off)."""
    if not a.get("enabled"):
        return None
    base = after.replace(second=0, microsecond=0)
    for days in range(8):
        day = (base + dt.timedelta(days=days)).date()
        t = dt.datetime.combine(day, dt.time(a["hour"], a["minute"]))
        if t <= after:
            continue
        if not a["repeat"] or day.weekday() in a["repeat"]:
            return t
    return None


def due(alarms: list, since: dt.datetime, now: dt.datetime) -> list:
    """Alarms going off in (since, now] -- a suspend in between: still rung
    when the computer is back (at most once each)."""
    out = []
    for a in alarms:
        t = next_time(a, since)
        if t is not None and t <= now:
            out.append(a)
    return out


def next_any(alarms: list, after: dt.datetime) -> dt.datetime:
    times = [t for t in (next_time(a, after) for a in alarms) if t]
    return min(times) if times else None


def repeat_text(repeat: list) -> str:
    """"Every day", "Weekdays", "Weekends", "Mon, Wed" -- or "" for once."""
    r = sorted(repeat or [])
    if len(r) == 7:
        return "Every day"
    if r == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if r == [5, 6]:
        return "Weekends"
    return ", ".join(DAYS[d] for d in r)


def time_text(a: dict, h24: bool = True) -> str:
    if h24:
        return f"{a['hour']:02d}:{a['minute']:02d}"
    h = a["hour"] % 12 or 12
    return f"{h}:{a['minute']:02d} {'AM' if a['hour'] < 12 else 'PM'}"
