"""Instant replay (Vini: "record the last 30 seconds", Windows' Record That).

While it's on, wl-screenrec keeps the most recent seconds of the game's
display in memory (its --history mode: nothing is written until SIGUSR1).
Save Clip: SIGUSR1 writes those seconds to the file, SIGINT a moment later
finishes it, the clip goes to the recordings folder as "Replay <date>.mp4"
(with its thumbnail and notification, like a recording) and a new buffer
starts at once. The GPU encoder does the work (a few % of a core); off by
default, and it stops when the Game Bar's switch is turned off or Sonata's
menu bar quits.

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


def command(path: str, output, seconds: int = SECONDS, audio=None) -> list:
    cmd = (["nice", "-n", "10"] if shutil.which("nice") else []) + \
        ["wl-screenrec", "--history", str(seconds), "-f", path]
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
        self.listeners = []                 # fn(): started, stopped or a clip saved
        self._saving = False

    @staticmethod
    def available() -> bool:
        return bool(shutil.which("wl-screenrec"))

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
        self.path = self._buffer_path()
        audio = audio_device("system") if cfg.get("audio", "system") != "none" else None
        try:
            self.proc = subprocess.Popen(command(self.path, self.output, int(cfg.get("replay_s", SECONDS)), audio),
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
            try:
                os.remove(self.path)
            except OSError:
                pass
        self._changed()

    def save(self) -> bool:
        """The last seconds as a clip; the buffer starts again. False: not running."""
        if not self.running or self._saving:
            return False
        self._saving = True
        proc, path, output = self.proc, self.path, self.output
        try:
            proc.send_signal(signal.SIGUSR1)
        except OSError:
            self._saving = False
            return False

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

    def _keep(self, path: str):
        if not path or not os.path.exists(path) or os.path.getsize(path) == 0:
            return None
        from .capture import _name
        out = os.path.join(os.path.dirname(path), _name("Replay", "mp4"))
        try:
            os.replace(path, out)
        except OSError:
            return None
        return out
