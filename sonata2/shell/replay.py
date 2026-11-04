"""Instant replay (Vini: "record the last 30 seconds", Windows' Record That).

While it's on, a recorder keeps the most recent seconds of the game's
display in memory, nothing written until a clip is asked for (SIGUSR1):

- wl-screenrec (--history): SIGUSR1 writes those seconds to its file, SIGINT
  a moment later finishes it, and a new buffer starts at once;
- gpu-screen-recorder (-r), when there's no wl-screenrec (Vini: Instant
  Replay couldn't be turned on -- wl-screenrec is only in the AUR, while
  gpu-screen-recorder is in Arch's and CachyOS' own repositories): SIGUSR1
  writes the clip into its folder and it keeps recording.

The clip goes to the recordings folder as "Replay <date>.mp4" (with its
thumbnail and notification, like a recording). The GPU encoder does the work
(a few % of a core); off by default, and it stops when the Game Bar's switch
is turned off or Sonata's menu bar quits.

    r = Replay(capture)            # shell/capture.Capture: folder, thumbnail
    r.available(); r.start(output); r.save(); r.stop(); r.running
"""
import os
import shutil
import signal
import subprocess

from gi.repository import GLib

from .. import config

SECONDS = 30
FLUSH_MS = 600            # after SIGUSR1, before SIGINT: the buffer is written out
WAIT_MS = 100
GSR_SAVE_S = 15           # gpu-screen-recorder's clip: given up on if it isn't written by then
GSR_FPS = 60


def backend():
    """The recorder replays are kept with: "wl-screenrec", "gpu-screen-recorder" or None."""
    for name in ("wl-screenrec", "gpu-screen-recorder"):
        if shutil.which(name):
            return name
    return None


def _nice() -> list:
    return ["nice", "-n", "10"] if shutil.which("nice") else []


def gsr_command(folder: str, output, seconds: int = SECONDS, audio=None) -> list:
    """gpu-screen-recorder keeping the last `seconds` in memory; each SIGUSR1
    writes them as an .mp4 into `folder`."""
    cmd = _nice() + ["gpu-screen-recorder", "-w", output or "screen", "-f", str(GSR_FPS),
                     "-r", str(seconds), "-c", "mp4", "-o", folder]
    if audio:
        cmd += ["-a", audio]
    return cmd


def command(path: str, output, seconds: int = SECONDS, audio=None) -> list:
    cmd = _nice() + ["wl-screenrec", "--history", str(seconds), "-f", path]
    if output:
        cmd += ["-o", output]
    if audio:
        cmd += ["--audio", "--audio-device", audio]
    return cmd


class Replay:
    def __init__(self, capture):
        self.capture = capture
        self.proc = None
        self.output = None
        self.path = None
        self.backend = None
        self.listeners = []                 # fn(): started, stopped or a clip saved
        self._saving = False

    @staticmethod
    def available() -> bool:
        return backend() is not None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _changed(self) -> None:
        for fn in list(self.listeners):
            fn()

    def _buffer_path(self) -> str:
        from .capture import DEFAULTS, movies_dir
        d = movies_dir(config.load("capture", DEFAULTS))
        return os.path.join(d, f".replay-{os.getpid()}.mp4")

    def start(self, output=None) -> bool:
        if self.running or not self.available():
            return self.running
        from .capture import DEFAULTS, audio_device, focused_output
        cfg = config.load("capture", DEFAULTS)
        self.output = output or focused_output()
        self.backend = backend()
        seconds = int(cfg.get("replay_s", SECONDS))
        audio = audio_device("system") if cfg.get("audio", "system") != "none" else None
        if self.backend == "gpu-screen-recorder":
            # its clips land in a folder of its own, moved out as they're done
            self.path = self._buffer_path()[:-len(".mp4")]
            try:
                os.makedirs(self.path, exist_ok=True)
            except OSError:
                return False
            cmd = gsr_command(self.path, self.output, seconds, audio)
        else:
            self.path = self._buffer_path()
            cmd = command(self.path, self.output, seconds, audio)
        try:
            self.proc = subprocess.Popen(cmd,
                                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        except OSError:
            self.proc = None
        self._changed()
        return self.running

    def stop(self) -> None:
        proc, self.proc = self.proc, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        if self.path:
            self._clear(self.path)
        self._changed()

    @staticmethod
    def _clear(path: str) -> None:
        """The buffer's file gone (or gpu-screen-recorder's folder: its clips
        not saved, then the folder itself)."""
        try:
            if os.path.isdir(path):
                for f in os.listdir(path):
                    if f.endswith(".mp4"):
                        os.remove(os.path.join(path, f))
                os.rmdir(path)
            else:
                os.remove(path)
        except OSError:
            pass

    def save(self) -> bool:
        """The last seconds as a clip; the buffer starts again. False: not running."""
        if not self.running or self._saving:
            return False
        self._saving = True
        proc, path, output = self.proc, self.path, self.output
        before = set(self._clips(path)) if self.backend == "gpu-screen-recorder" else set()
        try:
            proc.send_signal(signal.SIGUSR1)
        except OSError:
            self._saving = False
            return False
        if self.backend == "gpu-screen-recorder":
            self._gsr_wait(path, before)
            return True

        def finish():
            try:
                proc.send_signal(signal.SIGINT)
            except OSError:
                pass

            def wait():
                if proc.poll() is None:
                    return True
                self._saving = False
                self.proc = None
                clip = self._keep(path)
                self.start(output)                     # a new buffer at once
                if clip:
                    self.capture._saved(clip)
                self._changed()
                return False
            GLib.timeout_add(WAIT_MS, wait)
            return False
        GLib.timeout_add(FLUSH_MS, finish)
        return True

    @staticmethod
    def _clips(folder: str) -> list:
        try:
            return [f for f in os.listdir(folder) if f.endswith(".mp4")]
        except OSError:
            return []

    def _gsr_wait(self, folder: str, before: set) -> None:
        """gpu-screen-recorder writes the clip in the background: kept once
        it's there and stopped growing (it keeps recording meanwhile)."""
        state = {"left": int(GSR_SAVE_S * 1000 / WAIT_MS), "size": -1}

        def look():
            state["left"] -= 1
            new = [f for f in self._clips(folder) if f not in before]
            if new:
                f = os.path.join(folder, sorted(new)[-1])
                try:
                    size = os.path.getsize(f)
                except OSError:
                    size = 0
                if size > 0 and size == state["size"]:
                    self._saving = False
                    clip = self._keep(f)
                    if clip:
                        self.capture._saved(clip)
                    self._changed()
                    return False
                state["size"] = size
            if state["left"] <= 0:
                self._saving = False                   # never written: the switch works again
                self._changed()
                return False
            return True
        GLib.timeout_add(WAIT_MS, look)

    def _keep(self, path: str):
        if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        from .capture import _name
        d = os.path.dirname(path)
        if self.backend == "gpu-screen-recorder":
            d = os.path.dirname(d)                     # out of its folder, into the recordings
        out = os.path.join(d, _name("Replay", "mp4"))
        try:
            os.replace(path, out)
        except OSError:
            return None
        return out
