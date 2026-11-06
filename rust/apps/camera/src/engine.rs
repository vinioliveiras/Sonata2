//! The camera itself, with GStreamer (camera/engine.py; no widgets here).
//!
//! Pipeline: camera -> decodebin (webcams often send MJPEG) -> videoconvert
//! -> videoflip (mirror) -> tee -> sink; while recording a second branch
//! hangs off the tee: H.264 (the GPU's encoder first: NVIDIA, VA-API, then
//! x264 / OpenH264) + AAC from the microphone in MP4, or VP8 + Opus in
//! WebM. The sink is GStreamer's GTK 4 sink when installed (the frames go
//! to the GPU, no copies); else an appsink whose frames become textures
//! here. A photo is the sink's last frame, encoded to JPEG. Cameras come
//! from GStreamer's device monitor (V4L2 webcams first).

use gst::prelude::*;
use gstreamer::prelude::DeviceExt as _;
use gstreamer as gst;
use gstreamer_video as gst_video;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use sonata_i18n::{n_, tr};
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// a webcam's usual best: 1280x720 (MJPEG or raw), else whatever it has
const PREFERRED: &str = "image/jpeg,width=1280,height=720;video/x-raw,width=1280,height=720;image/jpeg,width=[640,1920];video/x-raw,width=[640,1920];image/jpeg;video/x-raw";
/// (video encoder, parser, muxer, audio encoder, extension), best first
const H264: [&str; 5] = ["nvh264enc", "vah264enc", "vaapih264enc", "x264enc", "openh264enc"];
const AAC: [&str; 3] = ["avenc_aac", "fdkaacenc", "voaacenc"];
/// live-video settings per encoder (as gst-launch would write them); bitrates in each one's own unit
const ENCODER_SETTINGS: [(&str, &[(&str, &str)]); 6] = [
    ("x264enc", &[("speed-preset", "veryfast"), ("tune", "zerolatency"), ("bitrate", "8000")]), // kbit/s
    ("openh264enc", &[("bitrate", "8000000")]),                                                // bit/s
    ("nvh264enc", &[("bitrate", "8000"), ("preset", "low-latency-hq")]),                       // kbit/s
    ("vah264enc", &[("bitrate", "8000")]),
    ("vaapih264enc", &[("bitrate", "8000")]),
    ("vp8enc", &[("deadline", "1"), ("cpu-used", "8"), ("target-bitrate", "8000000")]),
];

pub fn init() -> bool {
    gst::init().is_ok()
}

/// A camera: its name and GStreamer's device.
#[derive(Clone, Debug)]
pub struct Cam {
    pub name: String,
    pub device: Option<gst::Device>,
    pub api: String,
}

/// V4L2 cameras when there are any, else whatever the monitor found
/// (PipeWire's nodes); each name once.
pub fn pick_cams(found: Vec<Cam>) -> Vec<Cam> {
    let v4l2: Vec<Cam> = found.iter().filter(|c| c.api == "v4l2").cloned().collect();
    let pick = if v4l2.is_empty() { found } else { v4l2 };
    let mut seen: Vec<String> = Vec::new();
    pick.into_iter().filter(|c| if seen.contains(&c.name) { false } else { seen.push(c.name.clone()); true }).collect()
}

/// The cameras, V4L2 ones first (each once, by name). Slow (USB,
/// PipeWire): call it off the main loop.
pub fn devices() -> Vec<Cam> {
    let mon = gst::DeviceMonitor::new();
    mon.add_filter(Some("Video/Source"), None);
    let mut found = Vec::new();
    if mon.start().is_ok() {
        for dev in mon.devices() {
            let api = dev.properties().and_then(|p| p.get::<String>("device.api").ok()).unwrap_or_default();
            let name = Some(dev.display_name().to_string()).filter(|n| !n.is_empty()).unwrap_or_else(|| tr!("Camera"));
            found.push(Cam { name, device: Some(dev), api });
        }
        mon.stop();
    }
    pick_cams(found)
}

/// What the machine can record: (video encoder, parser, muxer, audio encoder, extension).
pub fn pick_encoders_with(has: &dyn Fn(&str) -> bool) -> Option<(&'static str, Option<&'static str>, &'static str, Option<&'static str>, &'static str)> {
    if let Some(venc) = H264.iter().find(|e| has(e)) {
        if has("mp4mux") && has("h264parse") {
            return Some((venc, Some("h264parse"), "mp4mux", AAC.iter().find(|e| has(e)).copied(), ".mp4"));
        }
    }
    if has("vp8enc") && has("webmmux") {
        return Some(("vp8enc", None, "webmmux", has("opusenc").then_some("opusenc"), ".webm"));
    }
    None
}

fn pick_encoders() -> Option<(&'static str, Option<&'static str>, &'static str, Option<&'static str>, &'static str)> {
    pick_encoders_with(&|e| gst::ElementFactory::find(e).is_some())
}

/// A property set from text when this version knows it and the value
/// reads; else left at its default (never a panic).
fn set_if_known(e: &gst::Element, prop: &str, value: &str) {
    use gst::prelude::GstValueExt;
    let Some(pspec) = e.find_property(prop) else { return };
    if let Ok(v) = glib::Value::deserialize(value, pspec.value_type()) {
        e.set_property_from_value(prop, &v);
    }
}

fn make(name: &str) -> Option<gst::Element> {
    gst::ElementFactory::make(name).build().ok()
}

fn make_encoder(name: &str) -> Option<gst::Element> {
    let e = make(name)?;
    for (_, props) in ENCODER_SETTINGS.iter().filter(|(n, _)| *n == name) {
        for (p, v) in props.iter() {
            set_if_known(&e, p, v);
        }
    }
    Some(e)
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct Frames {
        pub texture: RefCell<Option<gtk::gdk::Texture>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Frames {
        const NAME: &'static str = "SonataRsCameraFrames";
        type Type = super::Frames;
        type Interfaces = (gtk::gdk::Paintable,);
    }

    impl ObjectImpl for Frames {}

    impl PaintableImpl for Frames {
        fn snapshot(&self, snapshot: &gtk::gdk::Snapshot, width: f64, height: f64) {
            if let Some(t) = self.texture.borrow().as_ref() {
                t.snapshot(snapshot, width, height);
            }
        }

        fn intrinsic_width(&self) -> i32 {
            self.texture.borrow().as_ref().map(|t| t.width()).unwrap_or(0)
        }

        fn intrinsic_height(&self) -> i32 {
            self.texture.borrow().as_ref().map(|t| t.height()).unwrap_or(0)
        }
    }
}

glib::wrapper! {
    /// A paintable showing the latest frame (the appsink path).
    pub struct Frames(ObjectSubclass<imp::Frames>) @implements gtk::gdk::Paintable;
}

impl Frames {
    fn set_texture(&self, tex: gtk::gdk::Texture) {
        let old = self.imp().texture.replace(Some(tex.clone()));
        if old.is_none_or(|o| (o.width(), o.height()) != (tex.width(), tex.height())) {
            self.invalidate_size();
        }
        self.invalidate_contents();
    }
}

struct Recording {
    bin: gst::Bin,
    tee_pad: gst::Pad,
    sink: gst::Element,
    audio: Option<gst::Element>,
    path: String,
}

#[derive(Default)]
pub struct Camera {
    pipeline: RefCell<Option<gst::Pipeline>>,
    sink: RefCell<Option<gst::Element>>,
    flip: RefCell<Option<gst::Element>>,
    tee: RefCell<Option<gst::Element>>,
    rec: RefCell<Option<Recording>>,
    bus_watch: RefCell<Option<gst::bus::BusWatchGuard>>,
    /// what the window shows
    pub paintable: RefCell<Option<gtk::gdk::Paintable>>,
    on_error: RefCell<Option<Rc<dyn Fn(String)>>>,
    /// tests: the picture (videotestsrc) instead of a camera
    pub source: RefCell<Option<Rc<dyn Fn() -> Option<gst::Element>>>>,
    /// tests: the sound (audiotestsrc) instead of the microphone
    pub audio_source: RefCell<Option<Rc<dyn Fn() -> Option<gst::Element>>>>,
}

impl Camera {
    pub fn new(on_error: impl Fn(String) + 'static) -> Rc<Self> {
        let c = Rc::new(Self::default());
        c.on_error.replace(Some(Rc::new(on_error)));
        c
    }

    fn error(&self, text: String) {
        let f = self.on_error.borrow().clone();
        if let Some(f) = f {
            f(text);
        }
    }

    pub fn running(&self) -> bool {
        self.pipeline.borrow().is_some()
    }

    pub fn recording(&self) -> bool {
        self.rec.borrow().is_some()
    }

    pub fn start(self: &Rc<Self>, cam: Option<&Cam>, mirror: bool) -> bool {
        self.stop();
        let test = self.source.borrow().clone();
        let src = match &test {
            Some(make) => make(),
            None => cam.and_then(|c| c.device.as_ref()).and_then(|d| d.create_element(None).ok()),
        };
        let Some(src) = src else {
            self.error(n_!("No camera").into());
            return false;
        };
        let built = (|| {
            let p = gst::Pipeline::new();
            let caps = gst::ElementFactory::make("capsfilter").property("caps", gst::Caps::from_str(PREFERRED).ok()?).build().ok()?;
            let dec = make("decodebin")?;
            let conv = make("videoconvert")?;
            let flip = make("videoflip")?;
            let tee = gst::ElementFactory::make("tee").property("allow-not-linked", true).build().ok()?;
            let q = gst::ElementFactory::make("queue").property("max-size-buffers", 2u32).build().ok()?;
            q.set_property_from_str("leaky", "downstream"); // the preview never waits for the recording
            let conv2 = make("videoconvert")?;
            let (sink, gtk) = self.make_sink()?;
            p.add_many([&src, &caps, &conv, &flip, &tee, &q, &conv2, &sink]).ok()?;
            if test.is_some() {
                // a test source: no decoding needed
                caps.set_property("caps", gst::Caps::from_str("video/x-raw,width=640,height=360").ok()?);
                src.link(&caps).ok()?;
                caps.link(&conv).ok()?;
            } else {
                p.add(&dec).ok()?;
                src.link(&caps).ok()?;
                caps.link(&dec).ok()?;
                let c = conv.downgrade();
                dec.connect_pad_added(move |_, pad| {
                    if let Some(sinkpad) = c.upgrade().and_then(|c| c.static_pad("sink")) {
                        if !sinkpad.is_linked() {
                            let _ = pad.link(&sinkpad);
                        }
                    }
                });
            }
            gst::Element::link_many([&conv, &flip, &tee, &q, &conv2]).ok()?;
            if gtk {
                conv2.link(&sink).ok()?;
            } else {
                conv2.link_filtered(&sink, &gst::Caps::from_str("video/x-raw,format=RGBA").ok()?).ok()?;
            }
            Some((p, sink, flip, tee))
        })();
        let Some((p, sink, flip, tee)) = built else {
            self.error(n_!("The camera couldn't start (it may be in use by another app)").into());
            return false;
        };
        self.flip.replace(Some(flip));
        self.set_mirror(mirror);
        let weak = Rc::downgrade(self);
        let watch = p.bus().and_then(|bus| {
            bus.add_watch_local(move |_, msg| {
                if let gst::MessageView::Error(err) = msg.view() {
                    if let Some(c) = weak.upgrade() {
                        c.bus_error(&err.error().to_string());
                    }
                }
                glib::ControlFlow::Continue
            })
            .ok()
        });
        self.bus_watch.replace(watch);
        self.sink.replace(Some(sink));
        self.tee.replace(Some(tee));
        self.pipeline.replace(Some(p.clone()));
        if p.set_state(gst::State::Playing).is_err() {
            self.error(n_!("The camera couldn't start (it may be in use by another app)").into());
            self.stop();
            return false;
        }
        true
    }

    fn make_sink(&self) -> Option<(gst::Element, bool)> {
        if let Some(gtk) = make("gtk4paintablesink") {
            let p = gtk.property::<glib::Object>("paintable").downcast::<gtk::gdk::Paintable>().ok();
            self.paintable.replace(p);
            return Some((gtk, true));
        }
        let sink = gst::ElementFactory::make("appsink").property("emit-signals", true).property("max-buffers", 1u32).property("drop", true).property("sync", false).build().ok()?;
        let frames: Frames = glib::Object::new();
        self.paintable.replace(Some(frames.clone().upcast()));
        let target = glib::SendWeakRef::from(frames.downgrade());
        let pending = Arc::new(AtomicBool::new(false));
        // (the streaming thread) the newest frame to the window; one arriving
        // while the last isn't shown yet is dropped
        sink.connect("new-sample", false, move |values| {
            let sink = values[0].get::<gst::Element>().ok()?;
            let sample = sink.emit_by_name::<Option<gst::Sample>>("pull-sample", &[])?;
            if pending.swap(true, Ordering::AcqRel) {
                return Some(gst::FlowReturn::Ok.to_value());
            }
            let info = sample.caps().and_then(|c| gst_video::VideoInfo::from_caps(c).ok());
            let data = sample.buffer().and_then(|b| b.map_readable().ok().map(|m| glib::Bytes::from(m.as_slice())));
            let (Some(info), Some(data)) = (info, data) else {
                pending.store(false, Ordering::Release);
                return Some(gst::FlowReturn::Ok.to_value());
            };
            let (target, pending) = (target.clone(), pending.clone());
            glib::MainContext::default().invoke(move || {
                pending.store(false, Ordering::Release);
                if let Some(f) = target.upgrade() {
                    let tex = gtk::gdk::MemoryTexture::new(info.width() as i32, info.height() as i32, gtk::gdk::MemoryFormat::R8g8b8a8, &data, info.stride()[0] as usize);
                    f.set_texture(tex.upcast());
                }
            });
            Some(gst::FlowReturn::Ok.to_value())
        });
        Some((sink, false))
    }

    pub fn set_mirror(&self, on: bool) {
        if let Some(f) = self.flip.borrow().as_ref() {
            f.set_property_from_str("video-direction", if on { "horiz" } else { "identity" });
        }
    }

    pub fn stop(&self) {
        if self.rec.borrow().is_some() {
            self.drop_recording();
        }
        if let Some(p) = self.pipeline.take() {
            let _ = p.set_state(gst::State::Null);
        }
        self.bus_watch.take();
        self.sink.take();
        self.flip.take();
        self.tee.take();
    }

    fn bus_error(&self, message: &str) {
        self.stop();
        let low = message.to_lowercase();
        if low.contains("busy") || low.contains("resource") {
            self.error(n_!("The camera is in use by another app").into());
        } else {
            self.error(sonata_i18n::fmt(&tr!("The camera stopped ({error})"), &[("error", message)]));
        }
    }

    // -- photos ---------------------------------------------------------------------------------
    /// The frame on screen (mirrored as shown) as JPEG bytes.
    pub fn frame_jpeg(&self) -> Option<Vec<u8>> {
        let sink = self.sink.borrow().clone()?;
        let sample = sink.property::<Option<gst::Sample>>("last-sample")?;
        let jpeg = gst_video::convert_sample(&sample, &gst::Caps::builder("image/jpeg").build(), 3 * gst::ClockTime::SECOND).ok()?;
        let buf = jpeg.buffer()?;
        let m = buf.map_readable().ok()?;
        Some(m.as_slice().to_vec())
    }

    /// Save the frame on screen (mirrored as shown) as a JPEG.
    pub fn photo(&self, path: &std::path::Path) -> bool {
        self.frame_jpeg().is_some_and(|d| std::fs::write(path, d).is_ok())
    }

    // -- videos ---------------------------------------------------------------------------------
    /// Record what is on screen (plus the microphone) to base + extension;
    /// the file's path, or None.
    pub fn start_recording(&self, base: &str) -> Option<String> {
        let (pipeline, tee) = (self.pipeline.borrow().clone()?, self.tee.borrow().clone()?);
        if self.recording() {
            return None;
        }
        let Some((venc_name, parse_name, mux_name, aenc_name, ext)) = pick_encoders() else {
            self.error(n_!("No video encoder is installed (GStreamer: x264 or openh264, or VP8)").into());
            return None;
        };
        let path = format!("{base}{ext}");
        let bin = gst::Bin::new();
        let q = make("queue")?;
        let conv = make("videoconvert")?;
        // a steady 30 fps file, from the recording's first frame (not the camera's)
        let rate = gst::ElementFactory::make("videorate").property("skip-to-first", true).build().ok()?;
        let capsf = gst::ElementFactory::make("capsfilter").property("caps", gst::Caps::from_str("video/x-raw,framerate=30/1").ok()?).build().ok()?;
        let venc = make_encoder(venc_name)?;
        let mux = make(mux_name)?;
        let sink = gst::ElementFactory::make("filesink").property("location", &path).build().ok()?;
        let mut chain = vec![q.clone(), conv, rate, capsf, venc];
        if let Some(p) = parse_name {
            chain.push(make(p)?);
        }
        bin.add_many(chain.iter().chain([&mux, &sink])).ok()?;
        gst::Element::link_many(chain.iter()).ok()?;
        chain.last()?.link(&mux).ok()?;
        mux.link(&sink).ok()?;
        let mut audio = None;
        if let Some(aenc) = aenc_name {
            let asrc = match self.audio_source.borrow().clone() {
                Some(f) => f(),
                None => make("autoaudiosrc"),
            };
            let parts = (asrc, make("queue"), make("audioconvert"), make("audioresample"), make(aenc));
            if let (Some(asrc), Some(aq), Some(aconv), Some(ares), Some(aenc)) = parts {
                if bin.add_many([&asrc, &aq, &aconv, &ares, &aenc]).is_ok() && gst::Element::link_many([&asrc, &aq, &aconv, &ares, &aenc]).is_ok() && aenc.link(&mux).is_ok() {
                    audio = Some(asrc);
                }
            }
        }
        bin.add_pad(&gst::GhostPad::with_target(&q.static_pad("sink")?).ok()?).ok()?;
        pipeline.add(&bin).ok()?;
        let tee_pad = tee.request_pad_simple("src_%u")?;
        tee_pad.link(&bin.static_pad("sink")?).ok()?;
        let _ = bin.sync_state_with_parent();
        self.rec.replace(Some(Recording { bin, tee_pad, sink, audio, path: path.clone() }));
        Some(path)
    }

    /// Finish the file (EOS through the branch), then drop the branch;
    /// done(path) on the main loop once the file is complete.
    pub fn stop_recording(self: &Rc<Self>, done: impl Fn(String) + 'static) {
        let Some(rec) = self.rec.take() else { return };
        let finished = Arc::new(AtomicBool::new(false));
        if let Some(pad) = rec.sink.static_pad("sink") {
            let f = finished.clone();
            pad.add_probe(gst::PadProbeType::EVENT_DOWNSTREAM, move |_, info| {
                if let Some(gst::PadProbeData::Event(e)) = &info.data {
                    if e.type_() == gst::EventType::Eos {
                        f.store(true, Ordering::Release);
                    }
                }
                gst::PadProbeReturn::Ok
            });
        }
        let (bin, audio) = (rec.bin.clone(), rec.audio.clone());
        rec.tee_pad.add_probe(gst::PadProbeType::IDLE, move |pad, _| {
            if let Some(bin_pad) = bin.static_pad("sink") {
                let _ = pad.unlink(&bin_pad);
                bin_pad.send_event(gst::event::Eos::new());
            }
            if let Some(a) = &audio {
                a.send_event(gst::event::Eos::new());
            }
            gst::PadProbeReturn::Remove
        });
        // wait for the file's end on the main loop; never forever for a stuck encoder
        let started = std::time::Instant::now();
        let me = self.clone();
        let rec = RefCell::new(Some(rec));
        glib::timeout_add_local(std::time::Duration::from_millis(50), move || {
            if !finished.load(Ordering::Acquire) && started.elapsed().as_millis() < 5000 {
                return glib::ControlFlow::Continue;
            }
            if let Some(r) = rec.take() {
                me.remove_branch(&r);
                done(r.path);
            }
            glib::ControlFlow::Break
        });
    }

    fn remove_branch(&self, rec: &Recording) {
        let _ = rec.bin.set_state(gst::State::Null);
        if let Some(p) = self.pipeline.borrow().as_ref() {
            if rec.bin.parent().as_ref() == Some(p.upcast_ref::<gst::Object>()) {
                let _ = p.remove(&rec.bin);
            }
        }
        if let Some(t) = self.tee.borrow().as_ref() {
            t.release_request_pad(&rec.tee_pad);
        }
    }

    /// (closing) stop at once; the file may miss its ending.
    fn drop_recording(&self) {
        if let Some(rec) = self.rec.take() {
            if let Some(p) = rec.bin.static_pad("sink") {
                p.send_event(gst::event::Eos::new());
            }
            self.remove_branch(&rec);
        }
    }
}

use std::str::FromStr;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_best_encoders_first() {
        let all = |_: &str| true;
        assert_eq!(pick_encoders_with(&all), Some(("nvh264enc", Some("h264parse"), "mp4mux", Some("avenc_aac"), ".mp4")));
        let soft = |e: &str| ["x264enc", "mp4mux", "h264parse"].contains(&e);
        assert_eq!(pick_encoders_with(&soft), Some(("x264enc", Some("h264parse"), "mp4mux", None, ".mp4")));
        let vp8 = |e: &str| ["vp8enc", "webmmux", "opusenc"].contains(&e);
        assert_eq!(pick_encoders_with(&vp8), Some(("vp8enc", None, "webmmux", Some("opusenc"), ".webm")));
        assert_eq!(pick_encoders_with(&|_| false), None);
    }

    #[test]
    fn webcams_before_pipewire_nodes_each_once() {
        let c = |n: &str, api: &str| Cam { name: n.into(), device: None, api: api.into() };
        let got = pick_cams(vec![c("Integrated", "pipewire"), c("Integrated", "v4l2"), c("Integrated", "v4l2"), c("USB Cam", "v4l2")]);
        assert_eq!(got.iter().map(|c| c.name.as_str()).collect::<Vec<_>>(), ["Integrated", "USB Cam"]);
        assert!(got.iter().all(|c| c.api == "v4l2"));
        let pw = pick_cams(vec![c("PW Cam", "pipewire")]);
        assert_eq!(pw.len(), 1);
    }

    #[test]
    fn unknown_encoder_settings_are_skipped() {
        if !init() {
            return;
        }
        if let Some(e) = make("identity") {
            set_if_known(&e, "no-such-property", "1"); // no panic
            set_if_known(&e, "silent", "not-a-bool"); // a value that doesn't read: its default
            set_if_known(&e, "silent", "false");
            assert!(!e.property::<bool>("silent"));
        }
    }
}
