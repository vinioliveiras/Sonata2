"""Microphone and camera in use (macOS: the orange and green dots).

Microphone: the recording streams (`pactl list source-outputs`) that record
a real input, not a monitor of the speakers (a screen recording's system
sound) nor a level meter; looked at again on every `pactl subscribe`
source-output event, nothing in between.
Camera: a process with a /dev/video* device open (PipeWire holds it while
an app records through it; it lets it go a few seconds after). Looked at
every CAMERA_S seconds in a thread, only this user's processes.

Watcher.state = {"mic": [app names], "camera": [app names]}; listeners()
are called when it changes."""
import json
import os
import shutil
import subprocess

from gi.repository import GLib

CAMERA_S = 4
METER_NAMES = ("peak detect", "level meter", "peak")
CAMERA_HOLDERS = ("pipewire", "wireplumber")        # hold the device for the app actually recording


def mic_users(outputs) -> list:
    """App names recording a real input, from `pactl -f json list source-outputs`."""
    names = []
    for o in outputs or []:
        props = o.get("properties") or {}
        media = (props.get("media.name") or "").lower()
        if any(m in media for m in METER_NAMES) or props.get("stream.monitor") in ("true", True):
            continue
        source = str(o.get("source_name") or props.get("target.object") or props.get("node.target") or "")
        if source.endswith(".monitor") or props.get("stream.capture.sink") in ("true", True):
            continue                                 # the speakers' sound (screen recording), not the mic
        name = props.get("application.name") or props.get("application.process.binary") or "An app"
        if name not in names:
            names.append(name)
    return names


def _source_outputs() -> list:
    try:
        r = subprocess.run(["pactl", "-f", "json", "list", "source-outputs"], capture_output=True, text=True,
                           timeout=3)
        return json.loads(r.stdout or "[]") if r.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def _source_names() -> dict:
    """{source index: name} -- to tell a monitor source from a microphone."""
    try:
        r = subprocess.run(["pactl", "-f", "json", "list", "sources", "short"], capture_output=True, text=True,
                           timeout=3)
        return {str(s.get("index")): s.get("name", "") for s in json.loads(r.stdout or "[]")}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def read_mic() -> list:
    outs = _source_outputs()
    if not outs:
        return []
    names = _source_names()
    for o in outs:
        if "source_name" not in o and str(o.get("source")) in names:
            o["source_name"] = names[str(o.get("source"))]
    return mic_users(outs)


def _comm(pid: str, proc: str = "/proc") -> str:
    try:
        with open(os.path.join(proc, pid, "comm")) as f:
            return f.read().strip()
    except OSError:
        return ""


def camera_users(proc: str = "/proc") -> list:
    """Names of the processes with a camera device open (PipeWire last: it holds it for an app)."""
    uid = os.getuid()
    found = []
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return found
    for pid in pids:
        fd_dir = os.path.join(proc, pid, "fd")
        try:
            if os.stat(os.path.join(proc, pid)).st_uid != uid:
                continue
            fds = os.listdir(fd_dir)
        except OSError:
            continue
        for fd in fds:
            try:
                target = os.readlink(os.path.join(fd_dir, fd))
            except OSError:
                continue
            if target.startswith("/dev/video"):
                name = _comm(pid, proc) or pid
                if name not in found:
                    found.append(name)
                break
    apps = [n for n in found if n not in CAMERA_HOLDERS]
    return apps or (["An app"] if found else [])


class Watcher:
    def __init__(self):
        self.state = {"mic": [], "camera": []}
        self.listeners = []
        self._mic_busy = self._cam_busy = False
        self.proc = None
        if shutil.which("pactl"):
            from . import pactl_watch
            self.proc = pactl_watch.watch(self._line)
            self._read_mic()
        GLib.timeout_add_seconds(CAMERA_S, self._tick_camera)

    def _set(self, kind, names):
        if names != self.state[kind]:
            self.state = dict(self.state, **{kind: names})
            for cb in list(self.listeners):
                cb()

    def _line(self, text) -> None:
        if "source-output" in text:
            self._read_mic()

    def _read_mic(self) -> None:
        if self._mic_busy:
            return
        self._mic_busy = True
        from . import system

        def done(names):
            self._mic_busy = False
            self._set("mic", names or [])
        system.run_async(read_mic, done)

    def _tick_camera(self) -> bool:
        if self._cam_busy:
            return True
        if not any(n.startswith("video") for n in _dev_names()):
            self._set("camera", [])                  # no camera at all: nothing to look for
            return True
        self._cam_busy = True
        from . import system

        def done(names):
            self._cam_busy = False
            self._set("camera", names or [])
        system.run_async(camera_users, done)
        return True


def _dev_names() -> list:
    try:
        return os.listdir("/dev")
    except OSError:
        return []
