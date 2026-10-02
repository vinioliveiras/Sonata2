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
    pid: int = 0          # the process playing it (application.process.id)


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
                          muted=bool(it.get("mute")), corked=bool(it.get("corked")),
                          pid=int(props.get("application.process.id") or 0)
                          if str(props.get("application.process.id") or "").isdigit() else 0))
    return out


# -- whose stream: a game's (Steam), or an app's window ---------------------------------------------
_OWNERS = {}
PARENTS = 6            # a stream often comes from a helper process (WebKitWebProcess, wine...)


def _parent(pid: int, proc: str = "/proc") -> int:
    try:
        with open(f"{proc}/{pid}/stat", encoding="utf-8", errors="replace") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return 0


def _steam_app(pid: int, proc: str = "/proc"):
    try:
        with open(f"{proc}/{pid}/environ", "rb") as f:
            env = dict(e.split(b"=", 1) for e in f.read().split(b"\0") if b"=" in e)
    except OSError:
        return None
    aid = (env.get(b"SteamAppId") or env.get(b"SteamGameId") or b"").decode(errors="replace")
    return aid if aid.isdigit() and aid != "0" else None


def whose(pid: int, views=None, proc: str = "/proc"):
    """("steam", appid) or ("app", window app_id) for the process playing a
    stream (or one of its parents), None when unknown. Vini: the sound mixer
    showed no icon for a Steam game. `views`: Wayfire's list-views."""
    if pid <= 0:
        return None
    if pid in _OWNERS:
        return _OWNERS[pid]
    if views is None:
        try:
            from ..wl.wfipc import WayfireIPC
            views = WayfireIPC().call("window-rules/list-views")
        except Exception:
            views = None
    by_pid = {v.get("pid"): v.get("app-id") for v in views or [] if isinstance(v, dict) and v.get("app-id")}
    found, p = None, pid
    for _ in range(PARENTS):
        if p <= 1:
            break
        aid = _steam_app(p, proc)
        if aid:
            found = ("steam", aid)
            break
        if p in by_pid:
            found = ("app", by_pid[p])
            break
        p = _parent(p, proc)
    if found or views is not None:          # (IPC down: asked again next time)
        _OWNERS[pid] = found
    return found


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
        """A burst of stream events: restore new streams' levels; tell an
        open Sound menu. With neither, nothing runs (a playing app sends
        events all the time: no pactl process for each)."""
        self._src = 0
        new, self._new = self._new, set()
        if not new and not self.listeners:
            return False

        def work():
            data = saved()
            ss = streams()
            changed = [restore(s, data) for s in ss if s.index in new]     # every one (no short-circuit)
            if any(changed):
                ss = streams()                  # the menu shows the levels just put back
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
