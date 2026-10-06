"""Clock's stopwatch and timer (macOS Clock: Stopwatch, Timers). No GTK
here: the Clock window starts and stops them, the menu bar process
(shell/alarmservice.py) rings the timer, so both keep going with Clock
closed (~/.local/share/sonata2-data/clock/timers.json).

Times are wall-clock seconds (time.time()): a running stopwatch or timer
is its start / end, so nothing has to tick while nobody looks.

    stopwatch = {"running", "since" (when it last started), "before"
                 (seconds counted before that), "laps": [seconds, ...]}
    timer     = {"duration", "label", "sound", "ends" (running: when it
                 rings), "left" (paused: seconds left), "state":
                 "idle" | "running" | "paused"}
"""
import json
import os
import time

from . import alarms as A

FILE = "timers.json"
PRESETS = (60, 300, 600, 900, 1800, 3600)      # 1, 5, 10, 15, 30 min, 1 h
SOUND = "chimes"


def path() -> str:
    from .. import userdata
    return os.path.join(userdata.folder("clock"), FILE)


def new_stopwatch() -> dict:
    return {"running": False, "since": 0.0, "before": 0.0, "laps": []}


def new_timer(duration: float = 300, label: str = "", sound: str = SOUND) -> dict:
    return {"duration": float(duration), "label": label, "sound": sound, "ends": 0.0, "left": 0.0,
            "state": "idle"}


def _num(v, default=0.0) -> float:
    try:
        return max(0.0, float(v))
    except (TypeError, ValueError):
        return default


def load() -> tuple:
    """(stopwatch, timer), cleaned: a damaged file reads as new ones."""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    sw, tm = data.get("stopwatch"), data.get("timer")
    sw = dict(new_stopwatch(), **sw) if isinstance(sw, dict) else new_stopwatch()
    sw["running"] = bool(sw["running"])
    sw["since"], sw["before"] = _num(sw["since"]), _num(sw["before"])
    sw["laps"] = [_num(x) for x in sw["laps"]] if isinstance(sw["laps"], list) else []
    tm = dict(new_timer(), **tm) if isinstance(tm, dict) else new_timer()
    tm["duration"] = _num(tm["duration"], 300) or 300
    tm["ends"], tm["left"] = _num(tm["ends"]), _num(tm["left"])
    tm["label"] = str(tm.get("label") or "")
    tm["sound"] = tm["sound"] if tm.get("sound") in A.SOUNDS else SOUND
    tm["state"] = tm["state"] if tm.get("state") in ("idle", "running", "paused") else "idle"
    return sw, tm


def save(sw: dict, tm: dict) -> None:
    from ..config import atomic_write
    atomic_write(path(), json.dumps({"stopwatch": sw, "timer": tm}, indent=1).encode("utf-8"))


# -- stopwatch -----------------------------------------------------------------------------
def elapsed(sw: dict, now: float = None) -> float:
    now = time.time() if now is None else now
    return sw["before"] + (max(0.0, now - sw["since"]) if sw["running"] else 0.0)


def sw_start(sw: dict, now: float = None) -> dict:
    if not sw["running"]:
        sw.update(running=True, since=time.time() if now is None else now)
    return sw


def sw_stop(sw: dict, now: float = None) -> dict:
    if sw["running"]:
        sw.update(before=elapsed(sw, now), running=False, since=0.0)
    return sw


def sw_lap(sw: dict, now: float = None) -> dict:
    """A lap: the time since the last one (laps are kept oldest first)."""
    if sw["running"]:
        sw["laps"].append(elapsed(sw, now) - sum(sw["laps"]))
    return sw


def sw_reset(sw: dict) -> dict:
    sw.clear()
    sw.update(new_stopwatch())
    return sw


def current_lap(sw: dict, now: float = None) -> float:
    return elapsed(sw, now) - sum(sw["laps"])


def best_worst(laps: list) -> tuple:
    """Indexes of the fastest and slowest finished laps (None, None under
    two laps: nothing to compare, like macOS)."""
    if len(laps) < 2:
        return None, None
    return laps.index(min(laps)), laps.index(max(laps))


def stopwatch_text(seconds: float) -> str:
    """01:23,45 -- minutes, seconds, hundredths (hours in front past one)."""
    cs = int(seconds * 100)
    h, rest = divmod(cs, 360000)
    m, rest = divmod(rest, 6000)
    s, c = divmod(rest, 100)
    return (f"{h}:" if h else "") + f"{m:02d}:{s:02d},{c:02d}"


# -- timer --------------------------------------------------------------------------------------
def left(tm: dict, now: float = None) -> float:
    now = time.time() if now is None else now
    if tm["state"] == "running":
        return max(0.0, tm["ends"] - now)
    if tm["state"] == "paused":
        return tm["left"]
    return tm["duration"]


def tm_start(tm: dict, duration: float = None, label: str = None, now: float = None) -> dict:
    now = time.time() if now is None else now
    if duration is not None:
        tm["duration"] = float(duration)
    if label is not None:
        tm["label"] = label
    tm.update(state="running", ends=now + tm["duration"], left=0.0)
    return tm


def tm_pause(tm: dict, now: float = None) -> dict:
    if tm["state"] == "running":
        tm.update(state="paused", left=left(tm, now), ends=0.0)
    return tm


def tm_resume(tm: dict, now: float = None) -> dict:
    if tm["state"] == "paused":
        tm.update(state="running", ends=(time.time() if now is None else now) + tm["left"], left=0.0)
    return tm


def tm_cancel(tm: dict) -> dict:
    tm.update(state="idle", ends=0.0, left=0.0)
    return tm


def due(tm: dict, now: float = None) -> bool:
    return tm["state"] == "running" and left(tm, now) <= 0


def progress(tm: dict, now: float = None) -> float:
    """What's left of the timer, 1 (all) -> 0 (rings)."""
    return min(1.0, left(tm, now) / tm["duration"]) if tm["duration"] > 0 else 0.0


def timer_text(seconds: float) -> str:
    """06:32, or 1:06:32 past an hour; rounded up (macOS: 00:01 until it rings)."""
    s = int(-(-seconds // 1))
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def duration_text(seconds: float) -> str:
    """5 min, 1 h, 1 h 30 min, 45 s."""
    s = int(seconds)
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    parts = ([f"{h} h"] if h else []) + ([f"{m} min"] if m else []) + ([f"{s} s"] if s and not h else [])
    return " ".join(parts) or "0 s"
