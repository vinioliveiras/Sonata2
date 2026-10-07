"""Each device keeps its own volume (Vini: "I keep the headset's mic at the
maximum and it keeps resetting"). A volume set from Sonata -- the menu
bar, Control Center, the volume keys, Settings > Sound -- is remembered
for the device in use; when that device comes back (plugged in again, a
Bluetooth headset reconnecting or switching to its headset profile, which
makes a new microphone), Sonata sets it again, after WirePlumber has
put its own back.

Only what you set from Sonata is remembered: an app turning the
microphone down by itself (automatic gain in a call) isn't saved, nor
fought.

    remember("source", 100)          # system.set_volume does it
    restore("source", "bluez_input.XX")   # audiofollow's "new" events
"""
import threading
import time

from .. import config

NAME = "devicevolumes"
KINDS = ("sink", "source")
SAVE_AFTER_S = 0.8                   # a slider dragged: one write when it stops
RESTORE_AFTER_S = 1.5                # after WirePlumber has restored its own
MAX_DEVICES = 40                     # the most recently set ones

_pending = {}                        # kind -> percent not written yet
_timer = None
_lock = threading.Lock()


def _pactl(*args) -> str:
    from .audiofollow import _pactl as run
    return run(*args)


def saved() -> dict:
    data = config.load(NAME, {"sink": {}, "source": {}})
    return {k: dict(data.get(k) or {}) for k in KINDS}


def remember(kind: str, percent: int) -> None:
    """The default `kind`'s volume was set to `percent` (written a moment
    later, once, with the device's name)."""
    global _timer
    if kind not in KINDS:
        return
    with _lock:
        _pending[kind] = max(0, min(100, int(percent)))
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(SAVE_AFTER_S, flush)
        _timer.daemon = True
        _timer.start()


def flush(default_name=None) -> None:
    """Write what's pending under each default device's name."""
    global _timer
    with _lock:
        pending, _timer = dict(_pending), None
        _pending.clear()
    if not pending:
        return
    get = default_name or (lambda kind: _pactl("get-default-" + kind).strip())
    data = saved()
    changed = False
    for kind, pct in pending.items():
        name = get(kind)
        if not usable(name):
            continue
        devices = data[kind]
        devices.pop(name, None)                   # most recent last
        devices[name] = pct
        while len(devices) > MAX_DEVICES:
            devices.pop(next(iter(devices)))
        changed = True
    if changed:
        config.save(NAME, data)


def usable(name: str) -> bool:
    return bool(name) and not name.endswith(".monitor") and not name.startswith("sonata-eq")


def wanted(kind: str, name: str):
    """The volume remembered for that device, None if none."""
    return saved().get(kind, {}).get(name) if usable(name) else None


def restore(kind: str, name: str, wait: float = RESTORE_AFTER_S, set_volume=None) -> bool:
    """Blocking (a worker thread): the device's volume set again, if one
    was remembered."""
    pct = wanted(kind, name)
    if pct is None:
        return False
    if wait:
        time.sleep(wait)
    (set_volume or (lambda k, n, p: _pactl(f"set-{k}-volume", n, f"{p}%")))(kind, name, pct)
    return True
