"""The camera itself, with GStreamer (no widgets here: camera/window.py).

    cam = Camera(on_error=lambda text: ...)
    cams = devices()                      # [Cam(name, device)] webcams, built-in first
    cam.start(cams[0], mirror=True)       # cam.paintable: show it in a Gtk.Picture
    cam.set_mirror(False)
    cam.photo("/path/Photo.jpg")          # the frame on screen, as JPEG

Pipeline: camera -> decodebin (webcams often send MJPEG) -> videoconvert ->
videoflip (mirror) -> sink. The sink is GStreamer's GTK 4 sink when it is
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
        self.pipeline = self.sink = self.flip = None
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
        conv2 = Gst.ElementFactory.make("videoconvert")
        self.sink, gtk = self._make_sink()
        for e in (src, caps, dec, conv, self.flip, conv2, self.sink):
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
        self.flip.link(conv2)
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

    def _bus_error(self, _bus, msg):
        err, _debug = msg.parse_error()
        self.stop()
        busy = "busy" in (err.message or "").lower() or "resource" in (err.message or "").lower()
        self._error("The camera is in use by another app" if busy else f"The camera stopped ({err.message})")

    def _error(self, text):
        if self.on_error:
            self.on_error(text)


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
