"""Playback for Music through Gtk.MediaFile (GTK's media backend -- the
GStreamer module; no direct GStreamer import). One stream at a time.

Callbacks (set by the window): on_state() when playing/paused/stopped or the
song changes, on_position(seconds) at most once per second of playback,
on_duration(seconds) once known, on_ended() at the end of a song,
on_error(message)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

NO_BACKEND = ("Music can't play audio because GTK's media backend is missing. "
              "Install the GStreamer media module for GTK 4 (gtk4-media-gstreamer) "
              "and the GStreamer plugins, then open Music again.")


def backend_available() -> bool:
    """False when GTK has no media module (GtkNoMediaFile)."""
    probe = Gtk.MediaFile.new()
    return type(probe).__name__ != "GtkNoMediaFile"


class Player:
    def __init__(self):
        self.stream = None
        self.path = None
        self.volume = 1.0
        self.available = backend_available()
        self.on_state = self.on_position = self.on_duration = self.on_ended = self.on_error = None
        self._last_sec = -1

    # state ---------------------------------------------------------------------------------
    @property
    def playing(self) -> bool:
        return bool(self.stream and self.stream.get_playing())

    @property
    def status(self) -> str:
        """MPRIS PlaybackStatus."""
        if self.stream is None:
            return "Stopped"
        return "Playing" if self.stream.get_playing() else "Paused"

    @property
    def position(self) -> float:
        return self.stream.get_timestamp() / 1e6 if self.stream else 0.0

    @property
    def duration(self) -> float:
        return self.stream.get_duration() / 1e6 if self.stream else 0.0

    # control -------------------------------------------------------------------------------
    def load(self, path: str, play: bool = True) -> bool:
        self.stop(notify=False)
        self.path = path
        if not self.available:
            self._emit(self.on_error, NO_BACKEND)
            self._emit(self.on_state)
            return False
        s = Gtk.MediaFile.new_for_file(Gio.File.new_for_path(path))
        s.set_volume(self.volume)
        s.connect("notify::timestamp", self._tick)
        s.connect("notify::duration", lambda st, _p: st.get_duration() > 0 and
                  self._emit(self.on_duration, st.get_duration() / 1e6))
        s.connect("notify::ended", lambda st, _p: st.get_ended() and self._emit(self.on_ended))
        s.connect("notify::playing", lambda *_a: self._emit(self.on_state))
        s.connect("notify::error", self._errored)
        self.stream = s
        self._last_sec = -1
        if play:
            s.play()
        self._emit(self.on_state)
        return True

    def play(self) -> None:
        if self.stream is not None:
            if self.stream.get_ended():
                self.stream.seek(0)
            self.stream.play()

    def pause(self) -> None:
        if self.stream is not None:
            self.stream.pause()

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def stop(self, notify: bool = True) -> None:
        if self.stream is not None:
            self.stream.pause()
            self.stream.set_file(None)
            self.stream = None
        if notify:
            self.path = None
            self._emit(self.on_state)

    def seek(self, seconds: float) -> None:
        if self.stream is not None and self.stream.is_seekable():
            self.stream.seek(int(max(0.0, seconds) * 1e6))
            self._last_sec = -1
            self._tick(self.stream, None)

    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, min(1.0, v))
        if self.stream is not None:
            self.stream.set_volume(self.volume)

    # signals -------------------------------------------------------------------------------
    def _tick(self, s, _p) -> None:
        sec = int(s.get_timestamp() / 1e6)
        if sec != self._last_sec:           # the backend ticks often: the UI once a second
            self._last_sec = sec
            self._emit(self.on_position, s.get_timestamp() / 1e6)

    def _errored(self, s, _p) -> None:
        err = s.get_error()
        if err is not None:
            self._emit(self.on_error, err.message)

    @staticmethod
    def _emit(cb, *args) -> None:
        if cb:
            cb(*args)
