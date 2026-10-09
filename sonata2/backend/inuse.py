"""Microphone and camera in use (macOS: the orange and green dots).

Microphone: the recording streams (`pactl list source-outputs`) that record
a real input, not a monitor of the speakers (a screen recording's system
sound) nor a level meter; looked at again on every `pactl subscribe`
source-output event, nothing in between.
Camera: a process with a /dev/video* device open (PipeWire holds it while
an app records through it; it lets it go a few seconds after). Looked for
(every /proc/*/fd of this user's processes, in a thread) only when a camera
node is opened or closed -- inotify on the /dev/video* nodes, new ones
followed through /dev -- instead of every 4 s (that scan was the menu
bar's biggest idle cost). Without inotify: every CAMERA_S seconds as
before. Never while the screen is locked or a fullscreen game has the
focus (quiet.py): a look once that ends.

Watcher.state = {"mic": [app names], "camera": [app names]}; listeners()
are called when it changes."""
import ctypes
import json
import os
import shutil
import struct
import subprocess

from gi.repository import GLib

CAMERA_S = 4
SETTLE_MS = 300                 # a burst of opens/closes (an app probing every node): one look
PAUSED_RETRY_S = 30             # a node event while paused: looked at again (the pause may end unseen)
IN_OPEN, IN_CLOSE_WRITE, IN_CLOSE_NOWRITE = 0x20, 0x08, 0x10
IN_CREATE, IN_DELETE, IN_MOVED_TO = 0x100, 0x200, 0x80
IN_NONBLOCK, IN_CLOEXEC = 0o4000, 0o2000000
_EVENT = struct.Struct("iIII")
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


class VideoNodes:
    """inotify on every /dev/video* node (open and close: a camera taken or
    let go) and on /dev for nodes coming and going; on_event() for each.
    ok False when inotify can't be used (then the caller polls)."""

    def __init__(self, on_event, dev: str = "/dev"):
        self.on_event, self.dev = on_event, dev
        self.fd, self.src, self.ok = -1, 0, False
        self._dev_wd = -1
        try:
            self._libc = ctypes.CDLL(None, use_errno=True)
            fd = self._libc.inotify_init1(IN_NONBLOCK | IN_CLOEXEC)
        except (OSError, AttributeError):
            return
        if fd < 0:
            return
        self.fd = fd
        self._dev_wd = self._add(dev, IN_CREATE | IN_DELETE | IN_MOVED_TO)
        if self._dev_wd < 0:
            self.close()
            return
        for n in _dev_names(dev):
            if n.startswith("video"):
                self._add_node(n)
        self.src = GLib.unix_fd_add_full(GLib.PRIORITY_DEFAULT, fd, GLib.IOCondition.IN, self._readable)
        self.ok = True

    def _add(self, path: str, mask: int) -> int:
        return self._libc.inotify_add_watch(self.fd, os.fsencode(path), mask)

    def _add_node(self, name: str) -> None:
        self._add(os.path.join(self.dev, name), IN_OPEN | IN_CLOSE_WRITE | IN_CLOSE_NOWRITE)

    def _readable(self, *_a) -> bool:
        fire = False
        while True:
            try:
                buf = os.read(self.fd, 4096)
            except BlockingIOError:
                break
            except OSError:
                return True
            if not buf:
                break
            off = 0
            while off + _EVENT.size <= len(buf):
                wd, mask, _cookie, ln = _EVENT.unpack_from(buf, off)
                name = buf[off + _EVENT.size: off + _EVENT.size + ln].split(b"\0", 1)[0].decode(errors="replace")
                off += _EVENT.size + ln
                if wd == self._dev_wd:
                    if not name.startswith("video"):
                        continue                         # another device (a USB stick...): not ours
                    if mask & (IN_CREATE | IN_MOVED_TO):
                        self._add_node(name)             # a camera plugged in
                fire = True
        if fire:
            self.on_event()
        return True

    def close(self) -> None:
        if self.src:
            GLib.source_remove(self.src)
            self.src = 0
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1
        self.ok = False


class Watcher:
    def __init__(self):
        self.state = {"mic": [], "camera": []}
        self.listeners = []
        self._mic_busy = self._cam_busy = False
        self.proc = None
        self._soon_src = self._retry_src = 0
        self._dirty = False                          # a node event came while paused
        if shutil.which("pactl"):
            from . import pactl_watch
            self.proc = pactl_watch.watch(self._line)
            self._read_mic()
        from .. import quiet
        self._gate = quiet.watch(lambda paused: paused or (self._dirty and self._tick_camera()))
        self.nodes = VideoNodes(self._camera_soon)
        if self.nodes.ok:
            self._tick_camera()                      # once: an app already recording at start
        else:
            GLib.timeout_add_seconds(CAMERA_S, self._poll_camera)

    def _retry(self) -> bool:
        self._retry_src = 0
        if self._dirty:
            self._tick_camera()
        return False

    def _camera_soon(self) -> None:
        if not self._soon_src:
            self._soon_src = GLib.timeout_add(SETTLE_MS, self._camera_now)

    def _camera_now(self) -> bool:
        self._soon_src = 0
        self._tick_camera()
        return False

    def _poll_camera(self) -> bool:
        self._tick_camera()
        return True

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
        from .. import quiet
        if quiet.paused():                           # locked / a game in front: looked at once that ends
            self._dirty = True
            if not self._retry_src:
                self._retry_src = GLib.timeout_add_seconds(PAUSED_RETRY_S, self._retry)
            return True
        self._dirty = False
        if self._cam_busy:
            self._camera_soon()                      # an event during a look: once more after it
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


def _dev_names(dev: str = "/dev") -> list:
    try:
        return os.listdir(dev)
    except OSError:
        return []
