"""Playback for Music: GStreamer's classic playbin when the Gst typelib is
there (gir1.2-gstreamer / gstreamer's introspection data), else
Gtk.MediaFile (GTK's media backend). Not GTK's backend first: it plays
through GstPlay's playbin3, whose decodebin3 aborts the whole app on some
MP3s ("mq_slot_handle_stream_start: assertion failed: (collection)").
One stream at a time.

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


def _gst():
    """The Gst module, initialised, with a playbin; None without them."""
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        return Gst if Gst.ElementFactory.find("playbin") else None
    except (ValueError, ImportError):
        return None


def backend_available() -> bool:
    """False when there's neither GStreamer's playbin nor a GTK media module."""
    if _gst() is not None:
        return True
    probe = Gtk.MediaFile.new()
    return type(probe).__name__ != "GtkNoMediaFile"


class GstStream:
    """playbin with Gtk.MediaFile's calls (what Player uses of it)."""

    def __init__(self, Gst, path: str, owner):
        self.Gst, self.owner = Gst, owner
        self.bin = Gst.ElementFactory.make("playbin", None)
        self.bin.set_property("uri", Gio.File.new_for_path(path).get_uri())
        video = Gst.ElementFactory.make("fakesink", None)      # cover art "video": dropped
        if video is not None:
            self.bin.set_property("video-sink", video)
        self._playing = self._ended = False
        self._duration = 0
        self._tick_src = 0
        bus = self.bin.get_bus()
        bus.add_signal_watch()
        self._bus_id = bus.connect("message", self._message)

    def get_playing(self) -> bool:
        return self._playing

    def get_ended(self) -> bool:
        return self._ended

    def get_timestamp(self) -> int:
        ok, pos = self.bin.query_position(self.Gst.Format.TIME)
        return pos // 1000 if ok and pos > 0 else 0

    def get_duration(self) -> int:
        return self._duration

    def is_seekable(self) -> bool:
        return self._duration > 0

    def play(self) -> None:
        self._ended = False
        self.bin.set_state(self.Gst.State.PLAYING)
        self._set_playing(True)

    def pause(self) -> None:
        self.bin.set_state(self.Gst.State.PAUSED)
        self._set_playing(False)

    def seek(self, usec: int) -> None:
        self._ended = False
        self.bin.seek_simple(self.Gst.Format.TIME, self.Gst.SeekFlags.FLUSH | self.Gst.SeekFlags.KEY_UNIT,
                             usec * 1000)

    def set_volume(self, v: float) -> None:
        self.bin.set_property("volume", v)

    def close(self) -> None:
        self._set_playing(False)
        bus = self.bin.get_bus()
        bus.disconnect(self._bus_id)
        bus.remove_signal_watch()
        self.bin.set_state(self.Gst.State.NULL)

    def _set_playing(self, on: bool) -> None:
        if on == self._playing:
            return
        self._playing = on
        from gi.repository import GLib
        if on and not self._tick_src:
            self._tick_src = GLib.timeout_add(250, self._tick)
        elif not on and self._tick_src:
            GLib.source_remove(self._tick_src)
            self._tick_src = 0
        self.owner._emit(self.owner.on_state)

    def _tick(self) -> bool:
        if not self._duration:
            self._query_duration()
        self.owner._tick(self, None)
        return True

    def _query_duration(self) -> None:
        ok, dur = self.bin.query_duration(self.Gst.Format.TIME)
        if ok and dur > 0 and dur // 1000 != self._duration:
            self._duration = dur // 1000
            self.owner._emit(self.owner.on_duration, self._duration / 1e6)

    def _message(self, _bus, msg) -> None:
        T = self.Gst.MessageType
        if msg.type == T.EOS:
            self._ended = True
            self._set_playing(False)
            self.owner._emit(self.owner.on_ended)
        elif msg.type == T.ERROR:
            err, _dbg = msg.parse_error()
            self._set_playing(False)
            self.owner._emit(self.owner.on_error, err.message)
        elif msg.type in (T.DURATION_CHANGED, T.ASYNC_DONE):
            self._query_duration()


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
        Gst = _gst()
        if Gst is not None:
            s = GstStream(Gst, path, self)
            s.set_volume(self.volume)
            self.stream = s
            self._last_sec = -1
            if play:
                s.play()
            self._emit(self.on_state)
            return True
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
            if isinstance(self.stream, GstStream):
                self.stream.close()
            else:
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
