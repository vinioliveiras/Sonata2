"""Volume per app (the mixer macOS never had): every app playing sound has
its own level, kept for the next time it plays. Shown in the menu bar's
Sound menu ("Show Sound in menu bar"), under Output.

PipeWire's pulse layer (pactl): each app's playback is a "sink input".
Levels are saved per app (mixer.json "volumes": key -> percent, "muted":
key -> bool), the key being the app's id, else its program, else its name;
MixerService (menu bar process) puts the saved level back on every new
stream of that app, so a level set once stays even after the app quits.
Sonata's own short sounds (pw-play, paplay...) and event sounds are left out."""
import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from gi.repository import GLib

from .. import config

NAME = "mixer"
DEFAULTS = {"volumes": {}, "muted": {}}
# players Sonata starts for its own sounds (sounds.py, the alarm): never listed
OWN_PLAYERS = {"pw-play", "paplay", "canberra-gtk-play", "ffplay", "pw-cat", "aplay"}
SKIP_ROLES = {"event", "notification", "a11y"}
MAX_PERCENT = 100


@dataclass
class Stream:
    index: int
    key: str             # what the level is saved under
    name: str            # shown
    icon: str            # icon name ("" = none known)
    volume: int          # percent (the loudest channel)
    muted: bool
    corked: bool = False  # paused


def _percent(volume: dict) -> int:
    vals = []
    for ch in (volume or {}).values():
        p = str((ch or {}).get("value_percent", "")).rstrip("%").strip()
        try:
            vals.append(int(round(float(p))))
        except ValueError:
            pass
    return max(vals) if vals else 100


def app_key(props: dict) -> str:
    for k in ("application.id", "pipewire.access.portal.app_id", "application.process.binary", "application.name"):
        v = (props.get(k) or "").strip()
        if v:
            return v.lower()
    return ""


def parse(text: str) -> List[Stream]:
    """`pactl -f json list sink-inputs` -> the apps' streams (Sonata's own
    sounds and event sounds left out)."""
    try:
        items = json.loads(text or "[]")
    except ValueError:
        return []
    out = []
    for it in items if isinstance(items, list) else []:
        props = it.get("properties") or {}
        binary = (props.get("application.process.binary") or "").lower()
        if binary in OWN_PLAYERS or (props.get("media.role") or "").lower() in SKIP_ROLES:
            continue
        key = app_key(props)
        if not key:
            continue
        name = props.get("application.name") or binary or key
        out.append(Stream(index=int(it.get("index", -1)), key=key, name=name,
                          icon=props.get("application.icon_name") or "", volume=_percent(it.get("volume")),
                          muted=bool(it.get("mute")), corked=bool(it.get("corked"))))
    return out


def group(streams: List[Stream]) -> List[Stream]:
    """One entry per app (a browser plays several streams): the first
    stream stands for them; set_level() changes all of an app's streams."""
    seen, out = set(), []
    for s in streams:
        if s.key not in seen:
            seen.add(s.key)
            out.append(s)
    return out


def _run(args, timeout=4):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def available() -> bool:
    return shutil.which("pactl") is not None


def streams() -> List[Stream]:
    if not available():
        return []
    rc, out = _run(["pactl", "-f", "json", "list", "sink-inputs"])
    return parse(out) if rc == 0 else []


# -- saved levels -------------------------------------------------------------------------
def saved() -> dict:
    """A fresh copy (DEFAULTS' inner dicts must never be filled in)."""
    data = config.load(NAME, DEFAULTS)
    return {"volumes": dict(data.get("volumes") or {}), "muted": dict(data.get("muted") or {})}


def remember(key: str, percent: Optional[int] = None, muted: Optional[bool] = None) -> None:
    data = saved()
    if percent is not None:
        data["volumes"][key] = max(0, min(MAX_PERCENT, int(round(percent))))
    if muted is not None:
        data["muted"][key] = bool(muted)
    config.save(NAME, data)


def set_level(key: str, percent: Optional[int] = None, muted: Optional[bool] = None,
              all_streams: Optional[List[Stream]] = None) -> bool:
    """Every stream of app `key` at `percent` (and/or muted), saved for next time."""
    remember(key, percent, muted)
    ok = True
    for s in (all_streams if all_streams is not None else streams()):
        if s.key != key:
            continue
        if percent is not None:
            ok = _run(["pactl", "set-sink-input-volume", str(s.index),
                       f"{max(0, min(MAX_PERCENT, int(round(percent))))}%"])[0] == 0 and ok
        if muted is not None:
            ok = _run(["pactl", "set-sink-input-mute", str(s.index), "1" if muted else "0"])[0] == 0 and ok
    return ok


def restore(stream: Stream, data: dict = None) -> bool:
    """Put the app's saved level back on this stream. True: changed."""
    data = data or saved()
    changed = False
    want = data["volumes"].get(stream.key)
    if want is not None and want != stream.volume:
        changed = _run(["pactl", "set-sink-input-volume", str(stream.index), f"{want}%"])[0] == 0
    mute = data["muted"].get(stream.key)
    if mute is not None and mute != stream.muted:
        changed = _run(["pactl", "set-sink-input-mute", str(stream.index), "1" if mute else "0"])[0] == 0 or changed
    return changed


def new_stream_index(line: str) -> Optional[int]:
    """`pactl subscribe`: "Event 'new' on sink-input #42" -> 42."""
    if "'new' on sink-input #" not in line:
        return None
    try:
        return int(line.rsplit("#", 1)[1].strip())
    except ValueError:
        return None


class MixerService:
    """Menu bar process: every new app stream gets its app's saved level;
    listeners (an open Sound menu) hear about streams coming and going."""

    def __init__(self):
        self.listeners = []
        self.proc = None
        if not available():
            return
        try:
            self.proc = subprocess.Popen(["pactl", "subscribe"], stdout=subprocess.PIPE, text=True,
                                         stderr=subprocess.DEVNULL)
        except OSError:
            return
        self._src = 0
        self._new = set()
        GLib.io_add_watch(GLib.IOChannel.unix_new(self.proc.stdout.fileno()), GLib.PRIORITY_DEFAULT,
                          GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._line)
        GLib.idle_add(lambda: (self._restore_all(), False)[1])      # what was playing before we started

    def _line(self, _ch, cond) -> bool:
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            return False
        text = self.proc.stdout.readline()
        if not text:
            return False
        if "on sink-input" not in text:
            return True
        idx = new_stream_index(text)
        if idx is not None:
            self._new.add(idx)
        if not self._src:
            self._src = GLib.timeout_add(150, self._changed)        # a burst: one read
        return True

    def _changed(self) -> bool:
        self._src = 0
        new, self._new = self._new, set()

        def work():
            data = saved()
            ss = streams()
            for s in ss:
                if s.index in new:
                    restore(s, data)
            return ss
        from . import system
        system.run_async(work, lambda ss: [cb(ss) for cb in list(self.listeners)])
        return False

    def _restore_all(self) -> None:
        from . import system
        system.run_async(lambda: [restore(s) for s in streams()], None)

    def stop(self) -> None:
        if self.proc:
            self.proc.terminate()
