//! A video wallpaper under the lock and login screens' blur
//! (videowallpaper.py's VideoWallpaper with an AwakeGate, silent; Vini:
//! the lock and login screens were black with one). One player for every
//! display; its picture is a [`CoverPaintable`] the backdrop draws blurred,
//! the wallpaper's poster until the first frame.
//!
//! - **Silent, always**, whatever Settings says for the desktop: the audio
//!   sinks are off while the player is made (GStreamer reads its ranks once).
//! - **Paused while the display is dark** (the session's dark marker,
//!   lockdisplay), like the desktop's under an app.
//! - **Looped by hand**, the last frame fading into the first (the player's
//!   own loop seeks back, and a big file's seek could leave it with no
//!   picture); one that stalls or fails is started again on a new player,
//!   the last frame on screen meanwhile.
//!
//! The desktop's own video wallpaper (rust/apps/desktop) adds what only it
//! needs: sound, playing only while the desktop has the focus, a still
//! picture per display. GTK's player sends some signals from its
//! streaming thread: every handler on a player only carries thread-safe
//! references and hands the work to the main loop.

use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::path::Path;
use std::rc::{Rc, Weak};
use std::time::Duration;

const SETTLE_MS: u64 = 500; // after the pause, before the player is freed
const SETTLE_MAX_MS: u64 = 5000; // one that never opens: freed anyway
const TRIM_AFTER_MS: u64 = 1500; // GStreamer's threads let go of the frames a moment later
const WATCH_MS: u64 = 2000; // how often a playing video is checked
const STALL_MS: i64 = 8000; // playing, but no new frame for this long: started again
const RETRY_MAX: u32 = 3; // in a row, with no frame in between: then once every RETRY_LATER_MS
const RETRY_LATER_MS: i64 = 60000;
const OPEN_MAX_MS: i64 = 45000; // still opening after this long: started again (GStreamer at boot is slow)
const FADE_MS: f64 = 700.0; // the end of the video fading into its start

/// GStreamer's audio sinks: rank 0 = never picked, so autoaudiosink falls
/// back to a fake sink and nothing reaches the speakers.
pub const AUDIO_SINKS: [&str; 8] = ["pulsesink", "pipewiresink", "alsasink", "jackaudiosink", "osssink", "oss4sink", "openalsink", "sdlaudiosink"];

/// GST_PLUGIN_FEATURE_RANK with every audio sink off (what was set stays).
pub fn silent_ranks(existing: &str) -> String {
    let mut parts: Vec<String> = existing.split(',').filter(|p| !p.is_empty()).map(String::from).collect();
    let have: Vec<String> = parts.iter().map(|p| p.split(':').next().unwrap_or("").to_string()).collect();
    parts.extend(AUDIO_SINKS.iter().filter(|s| !have.iter().any(|h| h == *s)).map(|s| format!("{s}:0")));
    parts.join(",")
}

/// (w, h) to draw something of aspect `ratio` (else its size) at so it
/// covers width x height, keeping its shape; nothing known: the box itself.
pub fn cover(ratio: f64, size: (f64, f64), width: f64, height: f64) -> (f64, f64) {
    let ratio = if ratio > 0.0 { ratio } else if size.0 > 0.0 && size.1 > 0.0 { size.0 / size.1 } else { 0.0 };
    if ratio <= 0.0 || width <= 0.0 || height <= 0.0 {
        return (width, height);
    }
    if width / height > ratio {
        (width, width / ratio) // the box is wider: as wide as it, taller than it
    } else {
        (height * ratio, height)
    }
}

/// (x, y) <= 1: how much smaller the decoded frame (iw x ih) is than the
/// video says it is (ratio, video height). GTK's GStreamer picture draws
/// its frame at frame size / video size of what it's asked, from the
/// left: a frame a little narrower (hardware decoders) left a black band
/// down the right of the screen. Asked that much larger, it fills the box.
pub fn frame_shortfall(ratio: f64, vh: f64, iw: f64, ih: f64) -> (f64, f64) {
    if !(ratio > 0.0 && vh > 0.0 && iw > 0.0 && ih > 0.0) {
        return (1.0, 1.0);
    }
    let (fx, fy) = (iw / (ratio * vh), ih / vh); // (the video's own width: its height x its shape)
    // only a small shortfall is that band; anything else isn't this
    let keep = |f: f64| if (0.8..1.0).contains(&f) { f } else { 1.0 };
    (keep(fx), keep(fy))
}

/// The old frame's opacity `ms` into the fade (ease in-out).
pub fn fade_alpha(ms: f64) -> f64 {
    let t = ms / FADE_MS;
    if t < 1.0 { (1.0 - t * t * (3.0 - 2.0 * t)).max(0.0) } else { 0.0 }
}

/// `f` on the main loop: at once from the main thread, else queued there.
fn on_main(f: impl FnOnce() + Send + 'static) {
    glib::MainContext::default().invoke(f);
}

/// The audio sinks off in this process's GStreamer, once it's running
/// (the environment is read only when it starts). GStreamer is GTK's (its
/// media module): reached through the loaded library, never loaded here.
fn audio_sinks_off() {
    type IsInit = unsafe extern "C" fn() -> i32;
    type RegGet = unsafe extern "C" fn() -> *mut libc::c_void;
    type Lookup = unsafe extern "C" fn(*mut libc::c_void, *const libc::c_char) -> *mut libc::c_void;
    type SetRank = unsafe extern "C" fn(*mut libc::c_void, u32);
    type Unref = unsafe extern "C" fn(*mut libc::c_void);
    // SAFETY: the symbols are GStreamer 1.x's public C API, with these
    // signatures; RTLD_NOLOAD: only a library already in the process
    unsafe {
        let h = libc::dlopen(c"libgstreamer-1.0.so.0".as_ptr(), libc::RTLD_NOW | libc::RTLD_NOLOAD);
        if h.is_null() {
            return;
        }
        let sym = |n: &std::ffi::CStr| libc::dlsym(h, n.as_ptr());
        let syms = [sym(c"gst_is_initialized"), sym(c"gst_registry_get"), sym(c"gst_registry_lookup_feature"), sym(c"gst_plugin_feature_set_rank"), sym(c"gst_object_unref")];
        if syms.iter().all(|s| !s.is_null()) {
            let is_init = std::mem::transmute::<*mut libc::c_void, IsInit>(syms[0]);
            if is_init() != 0 {
                let (reg_get, lookup, set_rank, unref): (RegGet, Lookup, SetRank, Unref) = (std::mem::transmute::<*mut libc::c_void, RegGet>(syms[1]), std::mem::transmute::<*mut libc::c_void, Lookup>(syms[2]), std::mem::transmute::<*mut libc::c_void, SetRank>(syms[3]), std::mem::transmute::<*mut libc::c_void, Unref>(syms[4]));
                let reg = reg_get();
                for name in AUDIO_SINKS {
                    let Ok(c) = std::ffi::CString::new(name) else { continue };
                    let f = lookup(reg, c.as_ptr());
                    if !f.is_null() {
                        set_rank(f, 0);
                        unref(f);
                    }
                }
            }
        }
        libc::dlclose(h);
    }
}

/// A player with no sound: the ranks are set only while it's made
/// (GStreamer starts then), so nothing this process starts later inherits them.
fn new_player(path: &str) -> gtk::MediaFile {
    audio_sinks_off();
    const RANK: &str = "GST_PLUGIN_FEATURE_RANK";
    let before = std::env::var(RANK).ok();
    std::env::set_var(RANK, silent_ranks(before.as_deref().unwrap_or("")));
    let media = gtk::MediaFile::for_filename(path);
    match before {
        Some(b) => std::env::set_var(RANK, b),
        None => std::env::remove_var(RANK),
    }
    audio_sinks_off(); // (GStreamer started just now: off there too)
    media.set_muted(true);
    media.set_volume(0.0);
    media
}

thread_local! {
    /// held until they're freed
    static SETTLING: RefCell<Vec<gtk::MediaFile>> = RefCell::default();
    /// the open videos, for handlers coming back to the main loop
    static OPEN: RefCell<HashMap<String, Weak<LockVideo>>> = RefCell::default();
}

/// Pause now, free once paused and prepared (or failed) -- on the main
/// thread: GTK's GStreamer sink shares the window's GL context.
fn let_go(media: gtk::MediaFile) {
    media.pause();
    SETTLING.with(|s| s.borrow_mut().push(media.clone()));
    let waited = Cell::new(0);
    glib::timeout_add_local(Duration::from_millis(SETTLE_MS), move || {
        waited.set(waited.get() + SETTLE_MS);
        if !(media.is_prepared() || media.error().is_some()) && waited.get() < SETTLE_MAX_MS {
            media.pause(); // (prepared meanwhile: GTK starts what was asked)
            return glib::ControlFlow::Continue;
        }
        media.pause();
        media.clear();
        SETTLING.with(|s| s.borrow_mut().retain(|m| m != &media));
        glib::timeout_add_local_once(Duration::from_millis(TRIM_AFTER_MS), trim);
        glib::ControlFlow::Break
    });
}

/// The freed frames back to the system (glibc keeps freed memory; a 4K
/// video's frames are 33 MB each).
fn trim() {
    #[cfg(all(target_os = "linux", target_env = "gnu"))]
    // SAFETY: malloc_trim only gives free heap pages back
    unsafe {
        libc::malloc_trim(0);
    }
}

/// The player has a real frame (GTK gives an empty paintable before).
fn has_image(m: &gtk::MediaFile) -> bool {
    let img = m.current_image();
    img.intrinsic_width() > 0 || img.is::<gdk::Texture>()
}

fn frame_of(m: &gtk::MediaFile) -> Option<gdk::Paintable> {
    has_image(m).then(|| m.current_image())
}

// -- CoverPaintable -------------------------------------------------------------------------

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct CoverPaintable {
        pub media: RefCell<Option<gtk::MediaFile>>,
        pub ids: RefCell<Vec<glib::SignalHandlerId>>,
        /// (the old player's last frame, start µs)
        pub fade: RefCell<Option<(gdk::Paintable, i64)>>,
        pub fade_src: RefCell<Option<glib::SourceId>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for CoverPaintable {
        const NAME: &'static str = "SonataLockCoverPaintable";
        type Type = super::CoverPaintable;
        type Interfaces = (gdk::Paintable,);
    }

    impl ObjectImpl for CoverPaintable {}

    impl PaintableImpl for CoverPaintable {
        fn intrinsic_width(&self) -> i32 {
            0
        }
        fn intrinsic_height(&self) -> i32 {
            0
        }
        fn intrinsic_aspect_ratio(&self) -> f64 {
            0.0
        }
        fn flags(&self) -> gdk::PaintableFlags {
            gdk::PaintableFlags::empty()
        }
        fn current_image(&self) -> gdk::Paintable {
            match self.media.borrow().as_ref() {
                Some(m) => m.current_image(),
                None => gdk::Paintable::new_empty(0, 0),
            }
        }
        fn snapshot(&self, snapshot: &gdk::Snapshot, width: f64, height: f64) {
            let Some(snap) = snapshot.downcast_ref::<gtk::Snapshot>() else { return };
            snap.push_clip(&gtk::graphene::Rect::new(0.0, 0.0, width as f32, height as f32));
            if let Some(m) = self.media.borrow().as_ref() {
                draw(snap, m.upcast_ref(), width, height, None);
            }
            let alpha = self.obj().fade_alpha();
            if alpha > 0.0 {
                if let Some((frame, _)) = self.fade.borrow().as_ref() {
                    snap.push_opacity(alpha);
                    draw(snap, frame, width, height, Some(frame));
                    snap.pop();
                }
            }
            snap.pop();
        }
    }
}

glib::wrapper! {
    /// The video drawn to fill whatever it's given, like a wallpaper:
    /// scaled to cover, the overflow cut off, centred. No size of its own:
    /// the backdrop gives it the screen. The player under it can change: a
    /// frame kept from the old one fades out over the new one's.
    pub struct CoverPaintable(ObjectSubclass<imp::CoverPaintable>) @implements gdk::Paintable;
}

/// `paintable` drawn to cover width x height, centred; its shape from
/// `ratio_from` (a still frame), else the player's own, with the frame's
/// shortfall made up.
fn draw(snap: &gtk::Snapshot, paintable: &gdk::Paintable, width: f64, height: f64, ratio_from: Option<&gdk::Paintable>) {
    let r = ratio_from.unwrap_or(paintable);
    let (sw, sh) = cover(r.intrinsic_aspect_ratio(), (r.intrinsic_width() as f64, r.intrinsic_height() as f64), width, height);
    if sw <= 0.0 {
        return;
    }
    snap.save();
    snap.translate(&gtk::graphene::Point::new(((width - sw) / 2.0) as f32, ((height - sh) / 2.0) as f32));
    let (fx, fy) = if ratio_from.is_none() {
        let img = paintable.current_image();
        frame_shortfall(paintable.intrinsic_aspect_ratio(), paintable.intrinsic_height() as f64, img.intrinsic_width() as f64, img.intrinsic_height() as f64)
    } else {
        (1.0, 1.0)
    };
    paintable.snapshot(snap, sw / fx, sh / fy);
    snap.restore();
}

impl CoverPaintable {
    pub fn new() -> Self {
        glib::Object::new()
    }

    fn set_media(&self, media: Option<&gtk::MediaFile>) {
        self.release();
        let imp = self.imp();
        imp.media.replace(media.cloned());
        if let Some(m) = media {
            let relay = |w: glib::SendWeakRef<CoverPaintable>| {
                move |_: &gtk::MediaFile| {
                    let w = w.clone();
                    on_main(move || {
                        if let Some(c) = w.upgrade() {
                            c.invalidate_contents();
                        }
                    })
                }
            };
            let w = glib::SendWeakRef::from(self.downgrade());
            imp.ids.replace(vec![m.connect_invalidate_contents(relay(w.clone())), m.connect_invalidate_size(relay(w))]);
        }
        self.invalidate_contents();
    }

    fn release(&self) {
        let imp = self.imp();
        if let Some(m) = imp.media.borrow().as_ref() {
            for id in imp.ids.take() {
                m.disconnect(id);
            }
        }
        imp.ids.replace(Vec::new());
    }

    /// A frame to draw (the backdrop keeps its still picture until then).
    pub fn has_frame(&self) -> bool {
        self.imp().media.borrow().as_ref().is_some_and(|m| m.intrinsic_width() > 0 && has_image(m))
    }

    /// `frame` (the old player's last picture) over the new one, fading out.
    fn fade_from(&self, frame: Option<gdk::Paintable>) {
        let Some(frame) = frame else { return };
        let imp = self.imp();
        imp.fade.replace(Some((frame, glib::monotonic_time())));
        if imp.fade_src.borrow().is_none() {
            let w = self.downgrade();
            let src = glib::timeout_add_local(Duration::from_millis(16), move || {
                let Some(c) = w.upgrade() else { return glib::ControlFlow::Break };
                c.invalidate_contents();
                if c.fade_alpha() <= 0.0 {
                    c.imp().fade.replace(None);
                    c.imp().fade_src.replace(None);
                    return glib::ControlFlow::Break;
                }
                glib::ControlFlow::Continue
            });
            imp.fade_src.replace(Some(src));
        }
    }

    fn fade_alpha(&self) -> f64 {
        match self.imp().fade.borrow().as_ref() {
            Some((_, start)) => fade_alpha((glib::monotonic_time() - start) as f64 / 1000.0),
            None => 0.0,
        }
    }
}

impl Default for CoverPaintable {
    fn default() -> Self {
        Self::new()
    }
}

// -- AwakeGate ------------------------------------------------------------------------------

/// When the video may play: while the display is lit (the session's dark
/// marker, lockdisplay::mark_dark), paused once it goes dark.
struct AwakeGate {
    _mon: Option<gio::FileMonitor>,
}

impl AwakeGate {
    fn active() -> bool {
        !crate::lockdisplay::was_dark()
    }

    fn new(changed: impl Fn(bool) + 'static) -> Self {
        let was = Cell::new(Self::active());
        let mon = gio::File::for_path(crate::lockdisplay::dark_marker()).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE).ok();
        if let Some(m) = mon.as_ref() {
            m.connect_changed(move |_, _, _, _| {
                let now = Self::active();
                if now != was.replace(now) {
                    changed(now);
                }
            });
        }
        AwakeGate { _mon: mon }
    }
}

// -- LockVideo ------------------------------------------------------------------------------

/// The video, playing silent for every display of the lock or login screen.
pub struct LockVideo {
    pub path: String,
    pub cover: CoverPaintable,
    media: RefCell<Option<gtk::MediaFile>>,
    ids: RefCell<Vec<glib::SignalHandlerId>>,
    /// (player, handler) starting while the old one shows
    next: RefCell<Option<(gtk::MediaFile, glib::SignalHandlerId)>>,
    retries: Cell<u32>,
    last_retry: Cell<i64>,
    /// (timestamp, when) of the last frame that moved
    seen: Cell<(Option<i64>, i64)>,
    watch: RefCell<Option<glib::SourceId>>,
    gate: RefCell<Option<AwakeGate>>,
}

/// The open video for `path` (handlers coming back to the main loop).
fn with_video(path: &str, f: impl FnOnce(&Rc<LockVideo>)) {
    if let Some(v) = OPEN.with(|o| o.borrow().get(path).and_then(Weak::upgrade)) {
        f(&v);
    }
}

impl LockVideo {
    /// The video at `path`, playing; None when there's no such file.
    pub fn open(path: &Path) -> Option<Rc<Self>> {
        if !path.is_file() {
            return None;
        }
        let path = path.to_string_lossy().into_owned();
        let v = Rc::new(LockVideo {
            path: path.clone(),
            cover: CoverPaintable::new(),
            media: RefCell::default(),
            ids: RefCell::default(),
            next: RefCell::default(),
            retries: Cell::new(0),
            last_retry: Cell::new(0),
            seen: Cell::new((None, glib::monotonic_time())),
            watch: RefCell::default(),
            gate: RefCell::default(),
        });
        OPEN.with(|o| o.borrow_mut().insert(path, Rc::downgrade(&v)));
        let w = Rc::downgrade(&v);
        v.gate.replace(Some(AwakeGate::new(move |on| {
            if let Some(v) = w.upgrade() {
                v.allowed(on);
            }
        })));
        let w = Rc::downgrade(&v);
        v.watch.replace(Some(glib::timeout_add_local(Duration::from_millis(WATCH_MS), move || match w.upgrade() {
            Some(v) => {
                let go = v.check();
                if go == glib::ControlFlow::Break {
                    v.watch.take(); // (ends here: close() never removes it again)
                }
                go
            }
            None => glib::ControlFlow::Break,
        })));
        let m = Self::player(&v.path);
        v.use_player(&m);
        v.allowed(AwakeGate::active());
        Some(v)
    }

    fn player(path: &str) -> gtk::MediaFile {
        let media = new_player(path);
        media.set_loop(false); // (looped here: loop_end)
        media
    }

    /// `media` becomes the one shown and played.
    fn use_player(&self, media: &gtk::MediaFile) {
        self.media.replace(Some(media.clone()));
        self.cover.set_media(Some(media));
        let notify = |prop: &'static str, what: fn(&Rc<LockVideo>, &gtk::MediaFile)| {
            let (path, me) = (self.path.clone(), glib::SendWeakRef::from(media.downgrade()));
            media.connect_notify(Some(prop), move |_, _| {
                let (path, me) = (path.clone(), me.clone());
                on_main(move || {
                    if let Some(m) = me.upgrade() {
                        with_video(&path, |v| what(v, &m));
                    }
                })
            })
        };
        self.ids.replace(vec![
            notify("error", |v, m| {
                if let Some(e) = m.error() {
                    eprintln!("sonata2-rs lock: video wallpaper: can't play {}: {}", v.path, e.message());
                }
            }),
            notify("prepared", |v, m| {
                if v.media.borrow().as_ref() == Some(m) {
                    m.set_muted(true);
                    if m.is_prepared() {
                        v.allowed(AwakeGate::active());
                    }
                }
            }),
            notify("ended", |v, m| {
                if m.is_ended() && v.media.borrow().as_ref() == Some(m) {
                    v.loop_end();
                }
            }),
        ]);
        self.seen.set((None, glib::monotonic_time()));
    }

    fn drop_player(media: gtk::MediaFile, ids: Vec<glib::SignalHandlerId>) {
        for id in ids {
            media.disconnect(id);
        }
        let_go(media);
    }

    /// The end: the last frame fades into the start, on the same player.
    fn loop_end(&self) {
        let Some(m) = self.media.borrow().clone() else { return };
        if self.next.borrow().is_some() {
            return;
        }
        self.cover.fade_from(frame_of(&m));
        m.seek(0);
        if AwakeGate::active() {
            m.play();
        }
        self.seen.set((None, glib::monotonic_time()));
    }

    /// From the start, on a new player; the last frame fades into its first
    /// once that one has a picture.
    fn again(&self) {
        if self.media.borrow().is_none() || self.next.borrow().is_some() {
            return;
        }
        let nxt = Self::player(&self.path);
        let (path, me) = (self.path.clone(), glib::SendWeakRef::from(nxt.downgrade()));
        let id = nxt.connect_invalidate_contents(move |_| {
            let (path, me) = (path.clone(), me.clone());
            on_main(move || {
                let Some(nxt) = me.upgrade() else { return };
                with_video(&path, |v| v.framed(&nxt));
            })
        });
        if AwakeGate::active() {
            nxt.play();
        } else {
            nxt.pause(); // (paused, it still shows its first frame)
        }
        self.next.replace(Some((nxt, id)));
    }

    /// The new player has a picture: it takes over.
    fn framed(&self, nxt: &gtk::MediaFile) {
        if self.next.borrow().as_ref().is_none_or(|(n, _)| n != nxt) || !has_image(nxt) {
            return;
        }
        let Some((nxt, id)) = self.next.take() else { return };
        nxt.disconnect(id);
        let old = self.media.borrow().clone();
        let ids = self.ids.take();
        self.cover.fade_from(old.as_ref().and_then(frame_of));
        self.use_player(&nxt);
        if let Some(old) = old {
            Self::drop_player(old, ids);
        }
        self.allowed(AwakeGate::active());
    }

    /// A video that should be playing: one left paused is played; one
    /// still opening is waited for (at boot GStreamer can take long -- the
    /// login screen's stayed paused); one that fails or shows nothing new
    /// is started again: a few times in a row, then once a minute.
    fn check(&self) -> glib::ControlFlow {
        let Some(m) = self.media.borrow().clone() else { return glib::ControlFlow::Break };
        let now = glib::monotonic_time();
        if !AwakeGate::active() || self.next.borrow().is_some() {
            self.seen.set((None, now));
            return glib::ControlFlow::Continue;
        }
        let err = m.error().is_some();
        if !err && m.is_prepared() && !m.is_playing() && !m.is_ended() {
            m.play(); // should play, but was left paused
        }
        let ts = m.timestamp();
        let (last, since) = self.seen.get();
        if !err && Some(ts) != last {
            if last.is_some() {
                self.retries.set(0); // it plays: retries start over
            }
            self.seen.set((Some(ts), now));
            return glib::ControlFlow::Continue;
        }
        let waited = (now - since) / 1000;
        let limit = if m.is_prepared() { STALL_MS } else { OPEN_MAX_MS };
        if !(err || waited >= limit) {
            return glib::ControlFlow::Continue;
        }
        if self.retries.get() >= RETRY_MAX && (now - self.last_retry.get()) / 1000 < RETRY_LATER_MS {
            return glib::ControlFlow::Continue;
        }
        self.retries.set(self.retries.get() + 1);
        self.last_retry.set(now);
        self.seen.set((None, now));
        eprintln!("sonata2-rs lock: video wallpaper: {} {}, starting it again", self.path, if err { "failed" } else { "stalled" });
        self.again();
        glib::ControlFlow::Continue
    }

    fn allowed(&self, on: bool) {
        let Some(m) = self.media.borrow().clone() else { return };
        if on && m.error().is_none() {
            if m.is_ended() {
                self.loop_end(); // (it ended while paused at its last frame)
            }
            m.play();
            m.set_muted(true);
        } else {
            m.pause(); // (the frame on screen stays: never black)
        }
        self.seen.set((None, glib::monotonic_time()));
    }

    /// Stopped and its players let go of (the screen is going away, or
    /// shows another user's wallpaper now).
    pub fn close(&self) {
        self.gate.take();
        if let Some(s) = self.watch.take() {
            sonata_ui::cancel(s);
        }
        if let Some((nxt, id)) = self.next.take() {
            Self::drop_player(nxt, vec![id]);
        }
        if let Some(m) = self.media.take() {
            self.cover.release();
            Self::drop_player(m, self.ids.take());
        }
        OPEN.with(|o| {
            let mut o = o.borrow_mut();
            if o.get(&self.path).is_some_and(|w| std::ptr::eq(w.as_ptr(), self)) {
                o.remove(&self.path);
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn silent_whatever_was_set() {
        let all = silent_ranks("");
        for s in AUDIO_SINKS {
            assert!(all.contains(&format!("{s}:0")));
        }
        let kept = silent_ranks("vah264dec:300,pulsesink:5");
        assert!(kept.starts_with("vah264dec:300,pulsesink:5,"));
        assert!(!kept.contains("pulsesink:0")); // (what was set stays)
    }

    #[test]
    fn the_video_covers_the_screen() {
        assert_eq!(cover(16.0 / 9.0, (0.0, 0.0), 1600.0, 1200.0), (1200.0 * (16.0 / 9.0), 1200.0));
        assert_eq!(cover(0.0, (200.0, 100.0), 100.0, 100.0), (200.0, 100.0));
        assert_eq!(cover(0.0, (0.0, 0.0), 100.0, 50.0), (100.0, 50.0)); // nothing known: the box
        assert_eq!(frame_shortfall(16.0 / 9.0, 1080.0, 1904.0, 1080.0).1, 1.0);
        assert!(frame_shortfall(16.0 / 9.0, 1080.0, 1904.0, 1080.0).0 < 1.0); // the band on the right
        assert_eq!(frame_shortfall(16.0 / 9.0, 1080.0, 960.0, 1080.0), (1.0, 1.0)); // not that
    }

    #[test]
    fn the_loops_fade() {
        assert_eq!(fade_alpha(0.0), 1.0);
        assert!((fade_alpha(350.0) - 0.5).abs() < 1e-9);
        assert_eq!(fade_alpha(700.0), 0.0);
    }

    #[test]
    fn no_file_no_video() {
        assert!(LockVideo::open(Path::new("/nonexistent/wallpaper-video")).is_none());
    }
}
