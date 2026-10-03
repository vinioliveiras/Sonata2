"""One `pactl subscribe` reader for Sonata's audio watchers (Settings' Sound
page, the menu bar's mixer and equalizer).

Lines arrive through linereader on the GTK main loop (no buffered
readline inside an fd watch, which could block or miss lines kept in the
buffer); if pactl dies (PipeWire restarted, pipewire-pulse crashed) it is
started again, waiting longer each time it keeps dying."""
import shutil
import time

from gi.repository import Gio, GLib

from .linereader import read_lines

RESPAWN_MS = (1000, 30000)          # first wait, longest wait
STABLE_S = 30                       # ran this long: the next death waits the shortest again


class PactlWatch:
    """on_line(text) for every `pactl subscribe` line. stop() (or kill(),
    the name of the Popen it replaces) ends it for good."""

    def __init__(self, on_line):
        self.on_line = on_line
        self.proc = None
        self.cancel = None
        self.stopped = False
        self._wait = RESPAWN_MS[0]
        self._src = 0
        self._started = 0.0

    def start(self) -> bool:
        if self.stopped:
            return False
        try:
            self.proc = Gio.Subprocess.new(["pactl", "subscribe"],
                                           Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            self.proc = None
            return False
        self._started = time.monotonic()
        self.cancel = Gio.Cancellable()
        cancel = self.cancel
        read_lines(self.proc.get_stdout_pipe(), cancel, self.on_line,
                   lambda: None if cancel.is_cancelled() else self._respawn())     # pactl ended
        return True

    def _respawn(self) -> None:
        self._end_proc()
        if self.stopped or self._src:
            return
        if time.monotonic() - self._started >= STABLE_S:
            self._wait = RESPAWN_MS[0]

        def again():
            self._src = 0
            if not self.start():
                self._respawn()
            return False
        self._src = GLib.timeout_add(self._wait, again)
        self._wait = min(self._wait * 2, RESPAWN_MS[1])

    def _end_proc(self) -> None:
        if self.cancel:
            self.cancel.cancel()
            self.cancel = None
        if self.proc:
            self.proc.force_exit()
            self.proc.wait_async(None, None)          # reaped without blocking: no zombie
            self.proc = None

    def stop(self) -> None:
        self.stopped = True
        if self._src:
            GLib.source_remove(self._src)
            self._src = 0
        self._end_proc()

    kill = terminate = stop


def watch(on_line):
    """A running PactlWatch, None without pactl."""
    if not shutil.which("pactl"):
        return None
    w = PactlWatch(on_line)
    return w if w.start() else None
