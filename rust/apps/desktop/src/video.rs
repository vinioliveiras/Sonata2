//! The desktop's video wallpaper (videowallpaper.py: Vini: videos as
//! wallpapers, in a loop, silent, playing only while the desktop has the
//! focus, paused under any app, and light on the machine).
//!
//! - **One decoder per video.** Every display showing the same video
//!   shares one gtk::MediaFile (GTK's own GStreamer media backend, loaded
//!   by GTK as a module: this crate links no GStreamer); a picture per
//!   display only draws it. The last display to let go of it frees it.
//! - **Plays only on the desktop**: focus.rs says when.
//! - **Silent unless asked, and looped by hand** (the player's own loop
//!   seeks back, and a big file's seek could leave it with no picture).
//! - **Players let go of once settled**: paused first, freed once the
//!   pause has gone through (one dropped while its thread still held it
//!   was never freed -- Vini: changing the wallpaper several times froze it).
//!
//! GTK's player sends "invalidate-contents" from its streaming thread at
//! times (measured in Python): every handler on a player only carries
//! thread-safe references and hands the work to the main loop.

use crate::focus::{self, DesktopFocus};
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;

const SETTLE_MS: u64 = 500; // after the pause, before the player is freed
const SETTLE_MAX_MS: u64 = 5000; // one that never opens: freed anyway
const TRIM_AFTER_MS: u64 = 1500; // GStreamer's threads let go of the frames a moment later
const WATCH_MS: u64 = 2000; // how often a playing video is checked
const STALL_MS: i64 = 8000; // playing, but no new frame for this long: started again
const RETRY_MAX: u32 = 3; // in a row, with no frame in between: then once every RETRY_LATER_MS
const RETRY_LATER_MS: i64 = 60000;
const OPEN_MAX_MS: i64 = 45000; // still opening after this long: started again (GStreamer at boot is slow)

/// `f` on the main loop: at once from the main thread, else queued there.
fn on_main(f: impl FnOnce() + Send + 'static) {
    glib::MainContext::default().invoke(f);
}

// -- GStreamer's audio sinks ----------------------------------------------------------------

thread_local! {
    /// the audio sinks' own ranks, before they were turned off
    static SINK_RANKS: RefCell<HashMap<&'static str, u32>> = RefCell::default();
}

/// The audio sinks' ranks in this process's GStreamer, once it's running
/// (the environment is read only when it starts): off for a silent
/// wallpaper, back to their own for one with sound. GStreamer is GTK's
/// (its media module): reached through the loaded library, never loaded here.
fn audio_sinks(on: bool) {
    type IsInit = unsafe extern "C" fn() -> i32;
    type RegGet = unsafe extern "C" fn() -> *mut libc::c_void;
    type Lookup = unsafe extern "C" fn(*mut libc::c_void, *const libc::c_char) -> *mut libc::c_void;
    type GetRank = unsafe extern "C" fn(*mut libc::c_void) -> u32;
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
        let syms = [sym(c"gst_is_initialized"), sym(c"gst_registry_get"), sym(c"gst_registry_lookup_feature"), sym(c"gst_plugin_feature_get_rank"), sym(c"gst_plugin_feature_set_rank"), sym(c"gst_object_unref")];
        if syms.iter().all(|s| !s.is_null()) {
            let is_init: IsInit = std::mem::transmute(syms[0]);
            if is_init() != 0 {
                let (reg_get, lookup, get_rank, set_rank, unref): (RegGet, Lookup, GetRank, SetRank, Unref) = (std::mem::transmute(syms[1]), std::mem::transmute(syms[2]), std::mem::transmute(syms[3]), std::mem::transmute(syms[4]), std::mem::transmute(syms[5]));
                let reg = reg_get();
                for name in focus::AUDIO_SINKS {
                    let Ok(c) = std::ffi::CString::new(name) else { continue };
                    let f = lookup(reg, c.as_ptr());
                    if f.is_null() {
                        continue;
                    }
                    let own = SINK_RANKS.with(|r| {
                        *r.borrow_mut().entry(name).or_insert_with(|| match get_rank(f) {
                            0 => focus::default_rank(name),
                            n => n,
                        })
                    });
                    set_rank(f, if on { own } else { 0 });
                    unref(f);
                }
            }
        }
        libc::dlclose(h);
    }
}

/// A player, with no sound unless asked. The ranks are set only while
/// it's made (GStreamer starts then): apps opened from the desktop inherit
/// this process's environment and keep theirs.
fn new_player(path: &str, with_sound: bool) -> gtk::MediaFile {
    audio_sinks(with_sound);
    if with_sound {
        return gtk::MediaFile::for_filename(path);
    }
    const RANK: &str = "GST_PLUGIN_FEATURE_RANK";
    let before = std::env::var(RANK).ok();
    std::env::set_var(RANK, focus::silent_ranks(before.as_deref().unwrap_or("")));
    let media = gtk::MediaFile::for_filename(path);
    match before {
        Some(b) => std::env::set_var(RANK, b),
        None => std::env::remove_var(RANK),
    }
    audio_sinks(false); // (GStreamer started just now: off there too)
    media
}

thread_local! {
    /// held until they're freed
    static SETTLING: RefCell<Vec<gtk::MediaFile>> = RefCell::default();
}

/// Pause now, free once paused and prepared (or failed).
fn let_go(media: gtk::MediaFile) {
    media.pause();
    SETTLING.with(|s| s.borrow_mut().push(media.clone()));
    let waited = Cell::new(0);
    glib::timeout_add_local(std::time::Duration::from_millis(SETTLE_MS), move || {
        waited.set(waited.get() + SETTLE_MS);
        if !(media.is_prepared() || media.error().is_some()) && waited.get() < SETTLE_MAX_MS {
            media.pause(); // (prepared meanwhile: GTK starts what was asked)
            return glib::ControlFlow::Continue;
        }
        media.pause();
        // the decoder and its frames go now, on the main thread: GTK's
        // GStreamer sink shares the window's GL context (freed from a thread
        // of its own, the desktop stopped answering the mouse -- Vini)
        media.clear();
        SETTLING.with(|s| s.borrow_mut().retain(|m| m != &media));
        glib::timeout_add_local_once(std::time::Duration::from_millis(TRIM_AFTER_MS), trim);
        glib::ControlFlow::Break
    });
}

/// Give the freed frames back to the system: glibc keeps freed memory for
/// the process, and a video's frames (33 MB each at 4K) left the
/// wallpaper hundreds of MB bigger after a few changes.
fn trim() {
    #[cfg(all(target_os = "linux", target_env = "gnu"))]
    // SAFETY: malloc_trim only gives free heap pages back
    unsafe {
        libc::malloc_trim(0);
    }
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
        const NAME: &'static str = "SonataCoverPaintable";
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

    #[derive(Default)]
    pub struct DisplayCover {
        pub video: RefCell<Option<Rc<VideoWallpaper>>>,
        pub display: RefCell<String>,
        pub frozen: RefCell<Option<gdk::Paintable>>,
        pub relay: RefCell<Option<glib::SignalHandlerId>>,
        pub listener: Cell<u64>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for DisplayCover {
        const NAME: &'static str = "SonataDisplayCover";
        type Type = super::DisplayCover;
        type Interfaces = (gdk::Paintable,);
    }

    impl ObjectImpl for DisplayCover {}

    impl PaintableImpl for DisplayCover {
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
            if let Some(f) = self.frozen.borrow().as_ref() {
                return f.clone();
            }
            match self.video.borrow().as_ref() {
                Some(v) => v.cover.current_image(),
                None => gdk::Paintable::new_empty(0, 0),
            }
        }
        fn snapshot(&self, snapshot: &gdk::Snapshot, width: f64, height: f64) {
            let Some(snap) = snapshot.downcast_ref::<gtk::Snapshot>() else { return };
            match (self.frozen.borrow().as_ref(), self.video.borrow().as_ref()) {
                (Some(frozen), _) => {
                    snap.push_clip(&gtk::graphene::Rect::new(0.0, 0.0, width as f32, height as f32));
                    draw(snap, frozen, width, height, Some(frozen));
                    snap.pop();
                }
                (None, Some(v)) => v.cover.snapshot(snapshot, width, height),
                _ => {}
            }
        }
    }
}

glib::wrapper! {
    /// The video drawn to fill whatever it's given, like a wallpaper:
    /// scaled to cover, the overflow cut off, centred (Vini: it didn't fill
    /// the screen). Done here, not by the picture's fit: the size a video
    /// reports differs between GTK versions and GStreamer sinks. No size of
    /// its own: the picture gives it the screen. The player under it can
    /// change: a frame kept from the old one fades out over the new one's.
    pub struct CoverPaintable(ObjectSubclass<imp::CoverPaintable>) @implements gdk::Paintable;
}

glib::wrapper! {
    /// One display's picture of a video every display shares: playing while
    /// the video plays there (DesktopFocus::playing_on), else its last
    /// frame, still -- the display with windows paused, the one without
    /// playing on (Vini). A still one doesn't redraw at all.
    pub struct DisplayCover(ObjectSubclass<imp::DisplayCover>) @implements gdk::Paintable;
}

/// `paintable` drawn to cover width x height, centred; its shape from
/// `ratio_from` (a still frame), else the player's own, with the frame's
/// shortfall made up.
fn draw(snap: &gtk::Snapshot, paintable: &gdk::Paintable, width: f64, height: f64, ratio_from: Option<&gdk::Paintable>) {
    let r = ratio_from.unwrap_or(paintable);
    let (sw, sh) = focus::cover(r.intrinsic_aspect_ratio(), (r.intrinsic_width() as f64, r.intrinsic_height() as f64), width, height);
    if sw <= 0.0 {
        return;
    }
    snap.save();
    snap.translate(&gtk::graphene::Point::new(((width - sw) / 2.0) as f32, ((height - sh) / 2.0) as f32));
    let (fx, fy) = if ratio_from.is_none() {
        let img = paintable.current_image();
        focus::frame_shortfall(paintable.intrinsic_aspect_ratio(), paintable.intrinsic_height() as f64, img.intrinsic_width() as f64, img.intrinsic_height() as f64)
    } else {
        (1.0, 1.0)
    };
    paintable.snapshot(snap, sw / fx, sh / fy);
    snap.restore();
}

impl CoverPaintable {
    pub fn new(media: Option<&gtk::MediaFile>) -> Self {
        let c: Self = glib::Object::new();
        c.set_media(media);
        c
    }

    pub fn set_media(&self, media: Option<&gtk::MediaFile>) {
        self.release();
        let imp = self.imp();
        imp.media.replace(media.cloned());
        if let Some(m) = media {
            let w = glib::SendWeakRef::from(self.downgrade());
            let w2 = w.clone();
            imp.ids.replace(vec![
                m.connect_invalidate_contents(move |_| {
                    let w = w.clone();
                    on_main(move || {
                        if let Some(c) = w.upgrade() {
                            c.invalidate_contents();
                        }
                    })
                }),
                m.connect_invalidate_size(move |_| {
                    let w = w2.clone();
                    on_main(move || {
                        if let Some(c) = w.upgrade() {
                            c.invalidate_contents();
                        }
                    })
                }),
            ]);
        }
        self.invalidate_contents();
    }

    pub fn media(&self) -> Option<gtk::MediaFile> {
        self.imp().media.borrow().clone()
    }

    pub fn release(&self) {
        let imp = self.imp();
        if let Some(m) = imp.media.borrow().as_ref() {
            for id in imp.ids.take() {
                m.disconnect(id);
            }
        }
        imp.ids.replace(Vec::new());
    }

    /// A frame to draw (the lock / login backdrop keeps its still until then).
    pub fn has_frame(&self) -> bool {
        self.imp().media.borrow().as_ref().is_some_and(|m| m.intrinsic_width() > 0 && has_image(m))
    }

    /// `frame` (the old player's last picture) over the new one, fading out.
    pub fn fade_from(&self, frame: Option<gdk::Paintable>) {
        let Some(frame) = frame else { return };
        let imp = self.imp();
        imp.fade.replace(Some((frame, glib::monotonic_time())));
        if imp.fade_src.borrow().is_none() {
            let w = self.downgrade();
            let src = glib::timeout_add_local(std::time::Duration::from_millis(16), move || {
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

    pub fn fade_alpha(&self) -> f64 {
        match self.imp().fade.borrow().as_ref() {
            Some((_, start)) => focus::fade_alpha((glib::monotonic_time() - start) as f64 / 1000.0),
            None => 0.0,
        }
    }
}

/// The player has a real frame (GTK gives an empty paintable before).
fn has_image(m: &gtk::MediaFile) -> bool {
    let img = m.current_image();
    img.intrinsic_width() > 0 || img.is::<gdk::Texture>()
}

/// The frame the player shows, kept (for the fade, a still display).
fn frame_of(m: &gtk::MediaFile) -> Option<gdk::Paintable> {
    has_image(m).then(|| m.current_image())
}

impl DisplayCover {
    pub fn new(video: &Rc<VideoWallpaper>, display: &str) -> Self {
        let c: Self = glib::Object::new();
        let imp = c.imp();
        imp.video.replace(Some(video.clone()));
        imp.display.replace(display.to_string());
        let w = c.downgrade();
        imp.relay.replace(Some(video.cover.connect_invalidate_contents(move |_| {
            if let Some(c) = w.upgrade() {
                if c.imp().frozen.borrow().is_none() {
                    c.invalidate_contents();
                }
            }
        })));
        let w = c.downgrade();
        imp.listener.set(video.focus.add_display_listener(Rc::new(move || {
            if let Some(c) = w.upgrade() {
                c.update();
            }
        })));
        c.update();
        c
    }

    fn update(&self) {
        let imp = self.imp();
        let Some(v) = imp.video.borrow().clone() else { return };
        if v.focus.playing_on(&imp.display.borrow()) {
            if imp.frozen.take().is_some() {
                self.invalidate_contents();
            }
        } else if imp.frozen.borrow().is_none() {
            let img = v.media.borrow().as_ref().and_then(frame_of);
            imp.frozen.replace(img);
        }
    }

    pub fn is_frozen(&self) -> bool {
        self.imp().frozen.borrow().is_some()
    }

    pub fn release(&self) {
        let imp = self.imp();
        if let Some(v) = imp.video.take() {
            if let Some(id) = imp.relay.take() {
                v.cover.disconnect(id);
            }
            v.focus.remove_listener(imp.listener.get());
        }
    }
}

// -- VideoWallpaper -------------------------------------------------------------------------

/// One video, playing for every display that shows it. Looped by hand: at
/// the end the last frame fades into the first (Vini: a long video went
/// black at some point after its end). A player that stalls or fails is
/// started again on a new one; the last frame stays on screen meanwhile,
/// never black.
pub struct VideoWallpaper {
    pub path: String,
    users: Cell<u32>,
    pub media: RefCell<Option<gtk::MediaFile>>,
    pub cover: CoverPaintable,
    pub focus: Rc<DesktopFocus>,
    listener: Cell<u64>,
    ids: RefCell<Vec<glib::SignalHandlerId>>,
    /// (player, handler) starting while the old one shows
    next: RefCell<Option<(gtk::MediaFile, glib::SignalHandlerId)>>,
    retries: Cell<u32>,
    last_retry: Cell<i64>,
    /// (timestamp, when) of the last frame that moved
    seen: Cell<(Option<i64>, i64)>,
    watch: RefCell<Option<glib::SourceId>>,
    sound: Cell<(bool, f64)>,
    sound_mon: RefCell<Vec<gio::FileMonitor>>,
}

thread_local! {
    static OPEN: RefCell<HashMap<String, Rc<VideoWallpaper>>> = RefCell::default();
}

/// The video at `path`, shared with every display already showing it.
pub fn acquire(path: &str) -> Rc<VideoWallpaper> {
    let v = OPEN.with(|o| o.borrow().get(path).filter(|v| v.media.borrow().is_some()).cloned()).unwrap_or_else(|| {
        let v = VideoWallpaper::new(path, DesktopFocus::get());
        OPEN.with(|o| o.borrow_mut().insert(path.to_string(), v.clone()));
        v
    });
    v.users.set(v.users.get() + 1);
    v
}

pub fn release(v: &Rc<VideoWallpaper>) {
    v.users.set(v.users.get().saturating_sub(1));
    if v.users.get() == 0 {
        v.close();
        OPEN.with(|o| {
            let mut o = o.borrow_mut();
            if o.get(&v.path).is_some_and(|x| Rc::ptr_eq(x, v)) {
                o.remove(&v.path);
            }
        });
    }
}

/// The open video for `path` (handlers coming back to the main loop).
fn with_video(path: &str, f: impl FnOnce(&Rc<VideoWallpaper>)) {
    if let Some(v) = OPEN.with(|o| o.borrow().get(path).cloned()) {
        f(&v);
    }
}

impl VideoWallpaper {
    fn new(path: &str, focus: Rc<DesktopFocus>) -> Rc<Self> {
        let v = Rc::new(VideoWallpaper {
            path: path.to_string(),
            users: Cell::new(0),
            media: RefCell::default(),
            cover: CoverPaintable::new(None),
            focus: focus.clone(),
            listener: Cell::new(0),
            ids: RefCell::default(),
            next: RefCell::default(),
            retries: Cell::new(0),
            last_retry: Cell::new(0),
            seen: Cell::new((None, glib::monotonic_time())),
            watch: RefCell::default(),
            sound: Cell::new(focus::sound()),
            sound_mon: RefCell::default(),
        });
        let w = Rc::downgrade(&v);
        v.listener.set(focus.add_listener(Rc::new(move |on| {
            if let Some(v) = w.upgrade() {
                v.allowed(on);
            }
        })));
        let w = Rc::downgrade(&v);
        v.watch.replace(Some(glib::timeout_add_local(std::time::Duration::from_millis(WATCH_MS), move || match w.upgrade() {
            Some(v) => v.check(),
            None => glib::ControlFlow::Break,
        })));
        let w = Rc::downgrade(&v);
        v.sound_mon.replace(sonata_config::watch("wallpaper", move || {
            if let Some(v) = w.upgrade() {
                v.sound_changed();
            }
        }));
        let m = v.open();
        v.use_player(&m);
        v.allowed(focus.active());
        v
    }

    fn open(&self) -> gtk::MediaFile {
        let media = new_player(&self.path, self.sound.get().0);
        media.set_loop(false); // (looped here: loop_end)
        self.silence(&media);
        media
    }

    /// Settings: sound on / off needs a player made the other way (the
    /// audio output is picked when it starts): swapped with the same fade;
    /// a new volume applies at once.
    fn sound_changed(self: &Rc<Self>) {
        let (was, now) = (self.sound.get(), focus::sound());
        self.sound.set(now);
        let Some(m) = self.media.borrow().clone() else { return };
        if was == now {
            return;
        }
        if was.0 != now.0 {
            self.again();
        } else {
            self.silence(&m);
        }
    }

    /// `media` becomes the one shown and played.
    fn use_player(&self, media: &gtk::MediaFile) {
        self.media.replace(Some(media.clone()));
        self.cover.set_media(Some(media));
        let path = self.path.clone();
        let notify = |prop: &'static str, what: fn(&Rc<VideoWallpaper>, &gtk::MediaFile)| {
            let (path, me) = (path.clone(), glib::SendWeakRef::from(media.downgrade()));
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
                    eprintln!("sonata2-rs wallpaper: can't play {}: {}", v.path, e.message());
                }
            }),
            notify("prepared", |v, m| {
                if v.media.borrow().as_ref() == Some(m) {
                    v.silence(m);
                    if m.is_prepared() {
                        v.allowed(v.focus.active()); // (a reused player: play once the new file is open)
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

    /// The end: the last frame fades into the start, on the same player
    /// (seeked back). A new player per loop meant freeing one at every end
    /// (Vini: at the end it went back to the first frame and stopped, the
    /// desktop not answering the mouse); a new one is only for a player
    /// that stalls or fails (again).
    fn loop_end(&self) {
        let Some(m) = self.media.borrow().clone() else { return };
        if self.next.borrow().is_some() {
            return;
        }
        self.cover.fade_from(frame_of(&m));
        m.seek(0);
        if self.focus.active() {
            m.play();
            self.silence(&m);
        }
        self.seen.set((None, glib::monotonic_time()));
    }

    /// From the start, on a new player; the last frame fades into its first
    /// once that one has a picture.
    fn again(self: &Rc<Self>) {
        if self.media.borrow().is_none() || self.next.borrow().is_some() {
            return;
        }
        let nxt = self.open();
        let (path, me) = (self.path.clone(), glib::SendWeakRef::from(nxt.downgrade()));
        let id = nxt.connect_invalidate_contents(move |_| {
            let (path, me) = (path.clone(), me.clone());
            on_main(move || {
                let Some(nxt) = me.upgrade() else { return };
                with_video(&path, |v| v.framed(&nxt));
            })
        });
        if self.focus.active() {
            nxt.play();
        } else {
            nxt.pause(); // (paused, it still shows its first frame)
        }
        self.silence(&nxt);
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
        self.allowed(self.focus.active());
    }

    /// A video that should be playing: one left paused is played (Vini:
    /// paused with no window open); one still opening is waited for (at
    /// boot GStreamer can take long); one that fails or shows nothing new
    /// is started again: a few times in a row, then once a minute, never
    /// given up on.
    fn check(self: &Rc<Self>) -> glib::ControlFlow {
        let Some(m) = self.media.borrow().clone() else { return glib::ControlFlow::Break };
        let now = glib::monotonic_time();
        if !self.focus.active() || self.next.borrow().is_some() {
            self.seen.set((None, now));
            return glib::ControlFlow::Continue;
        }
        let err = m.error().is_some();
        if !err && m.is_prepared() && !m.is_playing() && !m.is_ended() {
            m.play(); // should play, but was left paused
            self.silence(&m);
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
        eprintln!("sonata2-rs wallpaper: {} {}, starting it again", self.path, if err { "failed" } else { "stalled" });
        self.again();
        glib::ControlFlow::Continue
    }

    /// No sound unless Settings turns it on (Vini: it played with sound),
    /// else at its volume. Set again once the player is up and at every
    /// play: GStreamer's player can come up with its own volume, and GTK
    /// only passes a change on -- so the volume is moved and set back.
    fn silence(&self, m: &gtk::MediaFile) {
        let (on, vol) = self.sound.get();
        m.set_muted(!on);
        m.set_volume(if vol < 1.0 { (vol + 0.01).min(1.0) } else { 0.99 });
        m.set_volume(if on { vol } else { 0.0 });
    }

    fn allowed(&self, on: bool) {
        let Some(m) = self.media.borrow().clone() else { return };
        if on && m.error().is_none() {
            if m.is_ended() {
                self.loop_end(); // (it ended while paused at its last frame)
            }
            m.play();
            self.silence(&m);
        } else {
            m.pause(); // (the frame on screen stays: no black desktop)
            if !self.focus.covered.borrow().is_empty() {
                self.save_frame(); // Overview: its desktops show this frame
            }
        }
        self.seen.set((None, glib::monotonic_time()));
    }

    /// The frame on screen, small, as a picture in the runtime folder
    /// (FRAME_FILE): Overview's desktops show the video where it paused,
    /// not its poster (Vini). Copied now, shrunk and written on a thread.
    fn save_frame(&self) {
        let Some(m) = self.media.borrow().clone() else { return };
        let Ok(tex) = m.current_image().downcast::<gdk::Texture>() else { return };
        let mut dl = gdk::TextureDownloader::new(&tex);
        dl.set_format(gdk::MemoryFormat::R8g8b8);
        let (data, stride) = dl.download_bytes();
        let (w, h) = (tex.width(), tex.height());
        let path = focus::runtime_dir().join(focus::FRAME_FILE);
        std::thread::Builder::new()
            .name("wallpaper-frame".into())
            .spawn(move || {
                let pb = gtk::gdk_pixbuf::Pixbuf::from_bytes(&data, gtk::gdk_pixbuf::Colorspace::Rgb, false, 8, w, h, stride as i32);
                let k = (focus::FRAME_PX as f64 / w.max(h) as f64).min(1.0);
                let pb = if k < 1.0 { pb.scale_simple(((w as f64 * k) as i32).max(1), ((h as f64 * k) as i32).max(1), gtk::gdk_pixbuf::InterpType::Bilinear).unwrap_or(pb) } else { pb };
                let tmp = path.with_extension("jpg.tmp");
                let r = pb.savev(&tmp, "jpeg", &[("quality", "85")]).map_err(|e| e.to_string()).and_then(|_| std::fs::rename(&tmp, &path).map_err(|e| e.to_string())); // (whole: Overview may be reading it)
                if let Err(e) = r {
                    eprintln!("sonata2-rs wallpaper: frame for Overview: {e}");
                }
            })
            .ok();
    }

    fn close(&self) {
        self.focus.remove_listener(self.listener.get());
        if let Some(s) = self.watch.take() {
            sonata_ui::cancel(s);
        }
        for m in self.sound_mon.take() {
            m.cancel();
        }
        if let Some((nxt, id)) = self.next.take() {
            Self::drop_player(nxt, vec![id]);
        }
        if let Some(m) = self.media.take() {
            self.cover.release();
            Self::drop_player(m, self.ids.take());
        }
    }
}
