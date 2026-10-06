"""Songs in Videos: a Gtk.MediaStream over GStreamer's classic playbin.

Not GTK's own media backend for audio: it plays through playbin3, whose
decodebin3 aborts the whole app on some MP3s ("mq_slot_handle_stream_start:
assertion failed: (collection)") -- what the removed Music app learnt.
As a Gtk.MediaStream, the window uses it exactly like a movie (notify::
playing / timestamp / ended / error, volume, mute, loop).

    stream = PlaybinStream(path)     # None-safe: available() first
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

TICK_MS = 250


def gst():
    """The Gst module, initialised, with a playbin; None without them."""
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        if not Gst.is_initialized():
            Gst.init(None)
        return Gst if Gst.ElementFactory.find("playbin") else None
    except (ValueError, ImportError):
        return None


def available() -> bool:
    return gst() is not None


class PlaybinStream(Gtk.MediaStream):
    def __init__(self, path: str):
        super().__init__()
        Gst = self.Gst = gst()
        if Gst is None:
            raise RuntimeError("GStreamer's playbin is missing (gstreamer and its base plugins).")
        self.bin = Gst.ElementFactory.make("playbin", None)
        self.bin.set_property("uri", Gio.File.new_for_path(path).get_uri())
        sink = Gst.ElementFactory.make("fakesink", None)          # a song's cover "video": dropped
        if sink is not None:
            self.bin.set_property("video-sink", sink)
        self._tick_src = 0
        self._bus = self.bin.get_bus()
        self._bus.add_signal_watch()
        self._bus_id = self._bus.connect("message", self._message)
        self.bin.set_state(Gst.State.PAUSED)                       # prerolls: duration, prepared

    # -- Gtk.MediaStream ------------------------------------------------------------------------
    def do_play(self) -> bool:
        self.bin.set_state(self.Gst.State.PLAYING)
        if not self._tick_src:
            self._tick_src = GLib.timeout_add(TICK_MS, self._tick)
        return True

    def do_pause(self) -> None:
        self.bin.set_state(self.Gst.State.PAUSED)
        self._stop_tick()

    def do_seek(self, timestamp: int) -> None:
        ok = self.bin.seek_simple(self.Gst.Format.TIME,
                                  self.Gst.SeekFlags.FLUSH | self.Gst.SeekFlags.ACCURATE, timestamp * 1000)
        if ok:
            self.seek_success()
            self.update(timestamp)
        else:
            self.seek_failed()

    def do_update_audio(self, muted: bool, volume: float) -> None:
        self.bin.set_property("mute", muted)
        self.bin.set_property("volume", volume)

    # -- own ------------------------------------------------------------------------------------
    def close(self) -> None:
        """Let go of the pipeline (the window does, on close or another file)."""
        self._stop_tick()
        if self._bus_id:
            self._bus.disconnect(self._bus_id)
            self._bus.remove_signal_watch()
            self._bus_id = 0
        self.bin.set_state(self.Gst.State.NULL)

    def _stop_tick(self) -> None:
        if self._tick_src:
            GLib.source_remove(self._tick_src)
            self._tick_src = 0

    def _tick(self) -> bool:
        ok, pos = self.bin.query_position(self.Gst.Format.TIME)
        if ok and pos >= 0:
            self.update(pos // 1000)
        return True

    def _duration(self) -> int:
        ok, dur = self.bin.query_duration(self.Gst.Format.TIME)
        return dur // 1000 if ok and dur > 0 else 0

    def _message(self, _bus, msg) -> None:
        T = self.Gst.MessageType
        if msg.type == T.ASYNC_DONE and not self.is_prepared():
            dur = self._duration()
            self.stream_prepared(True, False, dur > 0, dur)
        elif msg.type == T.EOS:
            if self.get_loop():
                self.do_seek(0)
                return
            self._stop_tick()
            self.update(self.get_duration())
            self.stream_ended()
        elif msg.type == T.ERROR:
            err, _dbg = msg.parse_error()
            self._stop_tick()
            self.gerror(err)
