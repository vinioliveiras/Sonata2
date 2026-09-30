"""The camera itself, with GStreamer (no widgets here: camera/window.py).

    cam = Camera(on_error=lambda text: ...)
    cams = devices()                      # [Cam(name, device)] webcams, built-in first
    cam.start(cams[0], mirror=True)       # cam.paintable: show it in a Gtk.Picture
    cam.set_mirror(False)
    cam.photo("/path/Photo.jpg")          # the frame on screen, as JPEG
    ext = cam.start_recording("/path/Video")   # with the microphone; ".mp4" / ".webm" added
    cam.stop_recording(done=lambda path: ...)

Pipeline: camera -> decodebin (webcams often send MJPEG) -> videoconvert ->
videoflip (mirror) -> tee -> sink; while recording a second branch hangs
off the tee: H.264 (the GPU's encoder first: NVIDIA, VA-API, then x264 /
OpenH264) + AAC from the microphone in MP4, or VP8 + Opus in WebM. The sink is GStreamer's GTK 4 sink when it is
installed (gst-plugin-gtk4: the frames go to the GPU, no copies); else
an appsink whose frames become GdkTextures here. A photo is the sink's
last frame, encoded to JPEG. Cameras come from GStreamer's device monitor
(V4L2 webcams; PipeWire's camera nodes when V4L2 shows none)."""
from dataclasses import dataclass

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstVideo", "1.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gst, GstVideo  # noqa: E402

Gst.init(None)

# a webcam's usual best: 1280x720 (MJPEG or raw), else whatever it has
PREFERRED = ("image/jpeg,width=1280,height=720;video/x-raw,width=1280,height=720;"
             "image/jpeg,width=[640,1920];video/x-raw,width=[640,1920];image/jpeg;video/x-raw")


@dataclass
class Cam:
    name: str
    device: object = None             # Gst.Device (None: a test source)
    api: str = ""


def devices() -> list:
    """The cameras, V4L2 ones first (each once, by name)."""
    mon = Gst.DeviceMonitor()
    mon.add_filter("Video/Source", None)
    found = []
    if mon.start():
        for dev in mon.get_devices() or []:
            props = dev.get_properties()
            api = (props.get_string("device.api") if props else "") or ""
            found.append(Cam(dev.get_display_name() or "Camera", dev, api))
        mon.stop()
    v4l2 = [c for c in found if c.api == "v4l2"]
    pick = v4l2 or found
    seen, out = set(), []
    for c in pick:
        if c.name not in seen:
            seen.add(c.name)
            out.append(c)
    return out


class Camera:
    def __init__(self, on_error=None, source=None):
        self.on_error = on_error              # (text) when the camera stops working
        self.source = source                  # () -> Gst.Element: tests (videotestsrc)
        self.pipeline = self.sink = self.flip = self.tee = None
        self.rec = None                       # the recording branch while recording
        self.audio_source = None              # () -> Gst.Element: tests (audiotestsrc)
        self.paintable = None                 # what the window shows
        self._texture = None
        self._pending = False
        self.mirror = True

    # -- running --------------------------------------------------------------------------------
    def start(self, cam: Cam = None, mirror: bool = True) -> bool:
        self.stop()
        self.mirror = mirror
        src = self.source() if self.source else (cam.device.create_element(None) if cam and cam.device else None)
        if src is None:
            self._error("No camera")
            return False
        p = Gst.Pipeline.new("camera")
        caps = Gst.ElementFactory.make("capsfilter")
        caps.set_property("caps", Gst.Caps.from_string(PREFERRED))
        dec = Gst.ElementFactory.make("decodebin")
        conv = Gst.ElementFactory.make("videoconvert")
        self.flip = Gst.ElementFactory.make("videoflip")
        self.tee = Gst.ElementFactory.make("tee")
        self.tee.set_property("allow-not-linked", True)
        q = Gst.ElementFactory.make("queue")
        q.set_property("leaky", 2)                          # the preview never waits for the recording
        q.set_property("max-size-buffers", 2)
        conv2 = Gst.ElementFactory.make("videoconvert")
        self.sink, gtk = self._make_sink()
        for e in (src, caps, dec, conv, self.flip, self.tee, q, conv2, self.sink):
            p.add(e)
        if self.source:                       # a test source: no decoding needed
            caps.set_property("caps", Gst.Caps.from_string("video/x-raw,width=640,height=360"))
            src.link(caps)
            caps.link(conv)
            p.remove(dec)
        else:
            src.link(caps)
            caps.link(dec)
            dec.connect("pad-added", lambda _d, pad: pad.link(conv.get_static_pad("sink")))
        conv.link(self.flip)
        self.flip.link(self.tee)
        self.tee.link(q)
        q.link(conv2)
        if gtk:
            conv2.link(self.sink)
        else:
            conv2.link_filtered(self.sink, Gst.Caps.from_string("video/x-raw,format=RGBA"))
        self.set_mirror(mirror)
        bus = p.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._bus_error)
        self.pipeline = p
        if p.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._error("The camera couldn't start (it may be in use by another app)")
            self.stop()
            return False
        return True

    def _make_sink(self):
        gtk = Gst.ElementFactory.make("gtk4paintablesink")
        if gtk is not None:
            self.paintable = gtk.get_property("paintable")
            return gtk, True
        sink = Gst.ElementFactory.make("appsink")
        sink.set_property("emit-signals", True)
        sink.set_property("max-buffers", 1)
        sink.set_property("drop", True)
        sink.set_property("sync", False)
        sink.connect("new-sample", self._new_sample)
        self.paintable = Frames()
        return sink, False

    def _new_sample(self, sink):
        """(appsink, streaming thread) the newest frame to the window; a
        frame arriving while the last one isn't shown yet is dropped."""
        sample = sink.emit("pull-sample")
        if sample is None or self._pending:
            return Gst.FlowReturn.OK
        info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
        buf = sample.get_buffer()
        ok, m = buf.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.OK
        data = GLib.Bytes.new(m.data)
        buf.unmap(m)
        self._pending = True

        def show():
            self._pending = False
            if self.paintable is not None and isinstance(self.paintable, Frames):
                self.paintable.set_texture(Gdk.MemoryTexture.new(info.width, info.height,
                                                                 Gdk.MemoryFormat.R8G8B8A8, data, info.stride[0]))
            return False
        GLib.idle_add(show)
        return Gst.FlowReturn.OK

    def set_mirror(self, on: bool) -> None:
        self.mirror = on
        if self.flip is not None:
            self.flip.set_property("video-direction", GstVideo.VideoOrientationMethod.HORIZ if on
                                   else GstVideo.VideoOrientationMethod.IDENTITY)

    def stop(self) -> None:
        if self.rec is not None:
            self._drop_recording()
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline.get_bus().remove_signal_watch()
        self.pipeline = self.sink = self.flip = None

    @property
    def running(self) -> bool:
        return self.pipeline is not None

    # -- photos ---------------------------------------------------------------------------------
    def photo(self, path: str) -> bool:
        """Save the frame on screen (mirrored as shown) as a JPEG."""
        if self.sink is None:
            return False
        sample = self.sink.get_property("last-sample")
        if sample is None:
            return False
        try:
            jpeg = GstVideo.video_convert_sample(sample, Gst.Caps.from_string("image/jpeg"), 3 * Gst.SECOND)
        except GLib.Error:
            return False
        buf = jpeg.get_buffer()
        ok, m = buf.map(Gst.MapFlags.READ)
        if not ok:
            return False
        try:
            with open(path, "wb") as f:
                f.write(m.data)
        except OSError:
            return False
        finally:
            buf.unmap(m)
        return True

    # -- videos ---------------------------------------------------------------------------------
    @property
    def recording(self) -> bool:
        return self.rec is not None

    def start_recording(self, base: str, sound: bool = True):
        """Record what is on screen (plus the microphone) to base + extension;
        returns the file's path, or None."""
        if self.pipeline is None or self.rec is not None:
            return None
        enc = pick_encoders()
        if enc is None:
            self._error("No video encoder is installed (GStreamer: x264 or openh264, or VP8)")
            return None
        venc_name, parse_name, mux_name, aenc_name, ext = enc
        path = base + ext
        bin_ = Gst.Bin.new("recording")
        q = Gst.ElementFactory.make("queue")
        conv = Gst.ElementFactory.make("videoconvert")
        rate = Gst.ElementFactory.make("videorate")                 # a steady 30 fps file
        rate.set_property("skip-to-first", True)      # from the recording's first frame, not the camera's
        capsf = Gst.ElementFactory.make("capsfilter")
        capsf.set_property("caps", Gst.Caps.from_string("video/x-raw,framerate=30/1"))
        venc = make_encoder(venc_name)
        mux = Gst.ElementFactory.make(mux_name)
        sink = Gst.ElementFactory.make("filesink")
        sink.set_property("location", path)
        chain = [q, conv, rate, capsf, venc] + ([Gst.ElementFactory.make(parse_name)] if parse_name else [])
        for e in chain + [mux, sink]:
            bin_.add(e)
        for a, b in zip(chain, chain[1:]):
            a.link(b)
        chain[-1].link(mux)
        mux.link(sink)
        asrc = None
        if sound and aenc_name:
            asrc = self.audio_source() if self.audio_source else Gst.ElementFactory.make("autoaudiosrc")
            aq = Gst.ElementFactory.make("queue")
            aconv = Gst.ElementFactory.make("audioconvert")
            ares = Gst.ElementFactory.make("audioresample")
            aenc = Gst.ElementFactory.make(aenc_name)
            if None in (asrc, aq, aconv, ares, aenc):
                asrc = None
            else:
                for e in (asrc, aq, aconv, ares, aenc):
                    bin_.add(e)
                asrc.link(aq)
                aq.link(aconv)
                aconv.link(ares)
                ares.link(aenc)
                aenc.link(mux)
        bin_.add_pad(Gst.GhostPad.new("sink", q.get_static_pad("sink")))
        self.pipeline.add(bin_)
        tee_pad = self.tee.request_pad_simple("src_%u")
        tee_pad.link(bin_.get_static_pad("sink"))
        bin_.sync_state_with_parent()
        self.rec = {"bin": bin_, "tee_pad": tee_pad, "sink": sink, "audio": asrc, "path": path}
        return path

    def stop_recording(self, done=None) -> None:
        """Finish the file (EOS through the branch), then drop the branch;
        done(path) on the main loop once the file is complete."""
        rec = self.rec
        if rec is None:
            return
        self.rec = None
        finished = {"once": False}

        def eos_probe(_pad, info):
            if info.get_event().type == Gst.EventType.EOS:
                GLib.idle_add(finish)
            return Gst.PadProbeReturn.OK

        def finish():
            if finished["once"]:
                return False
            finished["once"] = True
            self._remove_branch(rec)
            if done:
                done(rec["path"])
            return False
        rec["sink"].get_static_pad("sink").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, eos_probe)

        def unlink(pad, _info):
            pad.unlink(rec["bin"].get_static_pad("sink"))
            rec["bin"].get_static_pad("sink").send_event(Gst.Event.new_eos())
            if rec["audio"] is not None:
                rec["audio"].send_event(Gst.Event.new_eos())
            return Gst.PadProbeReturn.REMOVE
        rec["tee_pad"].add_probe(Gst.PadProbeType.IDLE, unlink)
        GLib.timeout_add(5000, finish)                       # never wait forever for a stuck encoder

    def _remove_branch(self, rec):
        rec["bin"].set_state(Gst.State.NULL)
        if self.pipeline is not None and rec["bin"].get_parent() is self.pipeline:
            self.pipeline.remove(rec["bin"])
        if self.tee is not None:
            self.tee.release_request_pad(rec["tee_pad"])

    def _drop_recording(self):
        """(closing) stop at once; the file may miss its ending."""
        rec, self.rec = self.rec, None
        rec["bin"].get_static_pad("sink").send_event(Gst.Event.new_eos())
        self._remove_branch(rec)

    def _bus_error(self, _bus, msg):
        err, _debug = msg.parse_error()
        self.stop()
        busy = "busy" in (err.message or "").lower() or "resource" in (err.message or "").lower()
        self._error("The camera is in use by another app" if busy else f"The camera stopped ({err.message})")

    def _error(self, text):
        if self.on_error:
            self.on_error(text)


# (video encoder, parser, muxer, audio encoder, extension), best first
_H264 = ("nvh264enc", "vah264enc", "vaapih264enc", "x264enc", "openh264enc")
_AAC = ("avenc_aac", "fdkaacenc", "voaacenc")


def pick_encoders():
    has = Gst.ElementFactory.find
    venc = next((e for e in _H264 if has(e)), None)
    if venc and has("mp4mux") and has("h264parse"):
        aenc = next((e for e in _AAC if has(e)), None)
        return venc, "h264parse", "mp4mux", aenc, ".mp4"
    if has("vp8enc") and has("webmmux"):
        return "vp8enc", None, "webmmux", "opusenc" if has("opusenc") else None, ".webm"
    return None


# live-video settings per encoder (as gst-launch would write them); bitrates in each one's own unit
ENCODER_SETTINGS = {
    "x264enc": {"speed-preset": "veryfast", "tune": "zerolatency", "bitrate": "8000"},         # kbit/s
    "openh264enc": {"bitrate": "8000000"},                                                     # bit/s
    "nvh264enc": {"bitrate": "8000", "preset": "low-latency-hq"},                              # kbit/s
    "vah264enc": {"bitrate": "8000"},
    "vaapih264enc": {"bitrate": "8000"},
    "vp8enc": {"deadline": "1", "cpu-used": "8", "target-bitrate": "8000000"},
}


def make_encoder(name):
    e = Gst.ElementFactory.make(name)
    for prop, value in ENCODER_SETTINGS.get(name, {}).items():
        if e.find_property(prop) is not None:
            try:
                Gst.util_set_object_arg(e, prop, value)
            except Exception:                    # a value this version doesn't know: its default
                pass
    return e


class Frames(GObject.Object, Gdk.Paintable):
    """A paintable showing the latest frame (the appsink path)."""
    __gtype_name__ = "SonataCameraFrames"

    def __init__(self):
        super().__init__()
        self.texture = None

    def set_texture(self, tex):
        size_changed = self.texture is None or (tex.get_width(), tex.get_height()) != (
            self.texture.get_width(), self.texture.get_height())
        self.texture = tex
        if size_changed:
            self.invalidate_size()
        self.invalidate_contents()

    def do_snapshot(self, snapshot, width, height):
        if self.texture is not None:
            self.texture.snapshot(snapshot, width, height)

    def do_get_intrinsic_width(self):
        return self.texture.get_width() if self.texture else 0

    def do_get_intrinsic_height(self):
        return self.texture.get_height() if self.texture else 0

    def do_get_flags(self):
        return Gdk.PaintableFlags(0)
