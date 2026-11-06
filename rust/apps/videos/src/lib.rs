//! Videos (videos/window.py; macOS QuickTime Player): one window per movie.
//!
//! The window takes the movie's shape (fitted to 70 % of the screen) and
//! the movie fills it, letterboxed on black. A rounded glass control bar
//! floats near the bottom (volume, back 10 s, play/pause, forward 10 s,
//! elapsed / remaining time, a scrubber, Trim, Crop, full screen); it fades
//! out after 2.5 s without motion while the movie plays (the pointer hides
//! too); paused, it stays. Trim and Crop save a new file beside the
//! original (ffmpeg) and open it. Each file's position is remembered.
//!
//! Songs (QuickTime plays them too): a square window with the album cover
//! (or a note), the controls always shown, no full screen; title "Artist —
//! Title". Movies go through gtk::MediaFile; songs through GStreamer's
//! classic playbin (playbin.rs).
//!
//! Keys: Space or K play/pause, ← / → 5 s, J / L 10 s, ↑ / ↓ volume, M
//! mute, F or ⌘F full screen, Esc leaves it, ⌘O open, ⌘T trim, ⌘W close.
//!
//! Its own binary (sonata2-rs-videos): only it needs GStreamer. ⌘O opens
//! Files' own Open panel (sonata_files::chooser), as the Python Videos does.

pub mod cropbar;
pub mod edit;
pub mod playbin;
pub mod trimbar;

use gtk::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Duration;

const CONFIG: &str = "videos";
/// remembered files (oldest forgotten first)
const MAX_POSITIONS: usize = 200;
/// s: closer than this to the start or the end, start over
const RESUME_MIN: f64 = 10.0;
/// ms without motion before the controls fade (playing)
const HIDE_AFTER: u64 = 2500;
/// px: a song's window (square, the cover's shape)
const AUDIO_SIZE: i32 = 420;
/// room for the picture above the controls at the smallest window
const MIN_PICTURE_H: i32 = 120;
/// top, sides, bottom (the crop bar's room), px
const CROP_ROOM: (i32, i32, i32) = (16, 16, 84);
pub const MIME_TYPES: [&str; 15] = ["video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo", "video/avi", "video/quicktime", "video/mpeg", "video/ogg", "video/x-ogm+ogg", "video/x-flv", "video/3gpp", "video/3gpp2", "video/x-m4v", "video/mp2t", "video/x-ms-wmv"];
pub const AUDIO_TYPES: [&str; 16] = ["audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/vorbis", "audio/opus", "audio/x-opus+ogg", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/x-aac", "audio/wav", "audio/x-wav", "audio/vnd.wave"];

pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.videos"
}

fn defaults() -> Value {
    json!({"positions": {}, "volume": 1.0, "muted": false})
}

// -- pure logic (tested) ----------------------------------------------------------------------
/// QuickTime's clock: 0:05, 12:34, 1:02:03; remaining time gets a minus
/// (and rounds up: never "-0:00" too early).
pub fn fmt_time(seconds: f64, remaining: bool) -> String {
    let s = (seconds + if remaining { 0.999 } else { 0.0 }).max(0.0) as i64;
    let (h, m, sec) = (s / 3600, (s % 3600) / 60, s % 60);
    let text = if h > 0 { format!("{h}:{m:02}:{sec:02}") } else { format!("{m}:{sec:02}") };
    if remaining { format!("-{text}") } else { text }
}

/// A window of the movie's shape that fits (max_w, max_h), never larger
/// than the movie itself.
pub fn fit_size(w: i32, h: i32, max_w: f64, max_h: f64) -> (i32, i32) {
    let (w, h) = (w.max(1) as f64, h.max(1) as f64);
    let s = 1.0f64.min(max_w / w).min(max_h / h);
    (((w * s) as i32).max(320), ((h * s) as i32).max(180))
}

/// A number as window.py reads one: `float(v or 0)` -- a number, true
/// (1), a numeric string ("42.5"); null, "" or false are 0; anything that
/// doesn't read (Python's ValueError) is `bad`.
pub fn py_float(v: Option<&Value>, bad: f64) -> f64 {
    match v {
        None | Some(Value::Null) => 0.0,
        Some(Value::Bool(b)) => f64::from(u8::from(*b)),
        Some(Value::Number(n)) => n.as_f64().unwrap_or(bad),
        Some(Value::String(s)) if s.is_empty() => 0.0,
        Some(Value::String(s)) => s.trim().parse::<f64>().ok().filter(|f| f.is_finite()).unwrap_or(bad),
        Some(_) => bad,
    }
}

/// Python's truth (window.py's bool(cfg.get("muted"))).
pub fn py_truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// Where the movie was left (seconds), 0 for none.
pub fn position_in(cfg: &Map<String, Value>, path: &str) -> f64 {
    match cfg.get("positions") {
        Some(Value::Object(p)) => py_float(p.get(path), 0.0),
        _ => 0.0,
    }
}

/// The volume and mute switch saved last (window.py's reading: a volume
/// that doesn't read is 0, never past 0..1).
pub fn volume_of(cfg: &Map<String, Value>) -> (f64, bool) {
    let v = match cfg.get("volume") {
        None => 1.0,
        some => py_float(some, 0.0),
    };
    (if v.is_nan() { 0.0 } else { v.clamp(0.0, 1.0) }, py_truthy(cfg.get("muted")))
}

/// The positions after leaving `path` at `position`: forgotten near the
/// start or the end; the latest last, the oldest dropped.
pub fn positions_after(cfg: &Map<String, Value>, path: &str, position: f64, duration: f64) -> Map<String, Value> {
    let mut pos = cfg.get("positions").and_then(Value::as_object).cloned().unwrap_or_default();
    pos.shift_remove(path);
    if position >= RESUME_MIN && (duration <= 0.0 || duration - position > RESUME_MIN) {
        pos.insert(path.into(), json!((position * 10.0).round_ties_even() / 10.0));
    }
    while pos.len() > MAX_POSITIONS {
        let first = pos.keys().next().cloned().unwrap_or_default();
        pos.shift_remove(&first);
    }
    pos
}

pub fn volume_icon(volume: f64, muted: bool) -> String {
    if muted || volume <= 0.0 {
        return "sonata-volume-muted-symbolic".into();
    }
    format!("sonata-volume-{}-symbolic", if volume < 0.34 { 1 } else if volume < 0.67 { 2 } else { 3 })
}

/// A song (by its name, as Files guesses it): MP3, FLAC, Ogg, M4A...
pub fn is_audio(path: &Path) -> bool {
    let (kind, _) = gio::content_type_guess(Some(path), &[]);
    gio::content_type_get_mime_type(&kind).is_some_and(|m| m.starts_with("audio/"))
}

/// "Artist — Title", or the title, or the file name.
pub fn song_title(title: &str, artist: &str, path: &Path) -> String {
    let t = if title.is_empty() { path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default() } else { title.to_string() };
    if artist.is_empty() { t } else { format!("{artist} — {t}") }
}

/// (width, height) of the smallest display: whatever is sized from it
/// fits whichever display the window opens on.
fn smallest_display() -> Option<(i32, i32)> {
    let d = gtk::gdk::Display::default()?;
    let list = d.monitors();
    (0..list.n_items()).filter_map(|i| list.item(i).and_downcast::<gtk::gdk::Monitor>()).map(|m| (m.geometry().width(), m.geometry().height())).min_by_key(|(w, h)| w * h)
}

// -- window -----------------------------------------------------------------------------------
struct Video {
    win: gtk::ApplicationWindow,
    path: RefCell<PathBuf>,
    stream: RefCell<Option<gtk::MediaStream>>,
    song: RefCell<Option<playbin::PlaybinStream>>,
    handlers: RefCell<Vec<glib::SignalHandlerId>>,
    sized: Cell<bool>,
    hide_src: RefCell<Option<glib::SourceId>>,
    vol_src: RefCell<Option<glib::SourceId>>,
    over_hud: Cell<bool>,
    last_xy: Cell<Option<(f64, f64)>>,
    syncing: Cell<bool>,
    shown_secs: Cell<(i64, i64)>,
    audio: Cell<bool>,
    trim_bar: RefCell<Option<Rc<trimbar::TrimBar>>>,
    crop_bar: RefCell<Option<Rc<cropbar::CropBar>>>,
    export: RefCell<Option<edit::Export>>,
    volume: Cell<f64>,
    muted: Cell<bool>,
    max: (f64, f64),
    picture: gtk::Picture,
    overlay: gtk::Overlay,
    sizers: RefCell<Vec<gtk::Widget>>,
    art: gtk::Box,
    message: gtk::Box,
    msg_title: gtk::Label,
    reason: gtk::Label,
    hud: gtk::Box,
    mute_btn: gtk::Button,
    volume_scale: gtk::Scale,
    play_btn: gtk::Button,
    full_btn: gtk::Button,
    trim_btn: gtk::Button,
    crop_btn: gtk::Button,
    elapsed: gtk::Label,
    remaining: gtk::Label,
    scrubber: gtk::Scale,
}

type Me = Rc<Video>;

fn call(cell: &Rc<RefCell<Option<std::rc::Weak<Video>>>>, f: fn(&Me)) -> impl Fn() + 'static {
    let c = cell.clone();
    move || {
        if let Some(me) = c.borrow().as_ref().and_then(|w| w.upgrade()) {
            f(&me);
        }
    }
}

impl Video {
    fn new(app: &gtk::Application, path: &Path) -> Me {
        if glib::application_name().is_none() {
            glib::set_application_name("Videos");
        }
        let win = gtk::ApplicationWindow::builder().application(app).css_classes(["sonata-videos"]).build();
        sonata_ui::window::standard(&win);
        let cfg = sonata_config::load(CONFIG, &defaults());
        let (volume, muted) = volume_of(&cfg);
        let cell: Rc<RefCell<Option<std::rc::Weak<Video>>>> = Rc::default();

        let picture = gtk::Picture::builder().content_fit(gtk::ContentFit::Contain).can_shrink(true).hexpand(true).vexpand(true).build();
        let handle = gtk::WindowHandle::builder().child(&picture).build(); // drag the movie: the window moves
        let overlay = gtk::Overlay::builder().child(&handle).css_classes(["vd-canvas"]).build();
        let art = gtk::Box::builder().css_classes(["vd-art"]).visible(false).can_target(false).build(); // a song without a cover
        art.append(&gtk::Image::builder().icon_name("audio-x-generic-symbolic").hexpand(true).vexpand(true).build());
        overlay.add_overlay(&art);
        let message = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).css_classes(["vd-message"]).halign(gtk::Align::Center).valign(gtk::Align::Center).visible(false).can_target(false).build();
        let msg_title = gtk::Label::builder().label(tr!("This video can't be played").as_str()).css_classes(["vd-msg-title"]).build();
        let reason = gtk::Label::builder().wrap(true).justify(gtk::Justification::Center).max_width_chars(60).css_classes(["vd-msg-body"]).build();
        message.append(&msg_title);
        message.append(&reason);
        overlay.add_overlay(&message);

        // the control bar
        let hud = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).css_classes(["vd-hud"]).halign(gtk::Align::Center).valign(gtk::Align::End).margin_bottom(22).margin_start(16).margin_end(16).build();
        hud.set_size_request(440, -1);
        let button = |icon: &str, tip: &str, f: Box<dyn Fn()>, css: &[&str]| {
            let b = gtk::Button::builder().icon_name(icon).tooltip_text(tip).can_focus(false).css_classes(css.to_vec()).build();
            b.connect_clicked(move |_| f());
            b
        };
        let top = gtk::CenterBox::new();
        let vol = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        let mute_btn = button(&volume_icon(volume, muted), &tr!("Mute"), Box::new(call(&cell, |m| m.toggle_mute())), &["vd-small"]);
        vol.append(&mute_btn);
        let c = cell.clone();
        let volume_scale = sonata_ui::controls::slider(volume * 100.0, move |v| {
            if let Some(m) = c.borrow().as_ref().and_then(|w| w.upgrade()) {
                m.volume_changed(v);
            }
        }, 0.0, 100.0, None);
        volume_scale.add_css_class("vd-volume");
        volume_scale.set_hexpand(false);
        volume_scale.set_can_focus(false); // arrows stay the movie's keys
        volume_scale.set_valign(gtk::Align::Center);
        vol.append(&volume_scale);
        top.set_start_widget(Some(&vol));
        let transport = gtk::Box::builder().spacing(10).valign(gtk::Align::Center).build();
        transport.append(&button("media-seek-backward-symbolic", &tr!("Back 10 Seconds"), Box::new(call(&cell, |m| m.skip(-10.0))), &[]));
        let play_btn = button("media-playback-start-symbolic", &tr!("Play"), Box::new(call(&cell, |m| m.toggle_play())), &["vd-play"]);
        transport.append(&play_btn);
        transport.append(&button("media-seek-forward-symbolic", &tr!("Forward 10 Seconds"), Box::new(call(&cell, |m| m.skip(10.0))), &[]));
        top.set_center_widget(Some(&transport));
        let full_btn = button("view-fullscreen-symbolic", &tr!("Enter Full Screen"), Box::new(call(&cell, |m| m.toggle_fullscreen())), &["vd-small"]);
        full_btn.set_valign(gtk::Align::Center);
        // Trim and Crop in sight (Vini: only in the right-click menu, they
        // weren't found), beside full screen; a song has no Crop
        let trim_btn = button("edit-cut-symbolic", &tr!("Trim…"), Box::new(call(&cell, |m| m.start_trim())), &["vd-small"]);
        let crop_btn = button("image-crop-symbolic", &tr!("Crop…"), Box::new(call(&cell, |m| m.start_crop())), &["vd-small"]);
        let end = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        for b in [&trim_btn, &crop_btn, &full_btn] {
            end.append(b);
        }
        top.set_end_widget(Some(&end));
        hud.append(&top);
        let bottom = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        let elapsed = gtk::Label::builder().label("0:00").xalign(0.0).css_classes(["vd-time"]).build();
        let remaining = gtk::Label::builder().label("-0:00").xalign(1.0).css_classes(["vd-time"]).build();
        let c = cell.clone();
        let scrubber = sonata_ui::controls::slider(0.0, move |v| {
            if let Some(m) = c.borrow().as_ref().and_then(|w| w.upgrade()) {
                m.scrubbed(v);
            }
        }, 0.0, 1000.0, None);
        scrubber.set_valign(gtk::Align::Center);
        scrubber.set_can_focus(false);
        bottom.append(&elapsed);
        bottom.append(&scrubber);
        bottom.append(&remaining);
        hud.append(&bottom);
        overlay.add_overlay(&hud);
        win.set_child(Some(&overlay));

        // 70 % of the display (the smallest one until shown: not just the first)
        let max = smallest_display().map(|(w, h)| (w as f64 * 0.7, h as f64 * 0.7)).unwrap_or((1100.0, 700.0));
        let (dw, dh) = fit_size(1920, 1080, max.0, max.1);
        win.set_default_size(dw, dh);
        let me = Rc::new(Self {
            win, path: RefCell::new(path.to_path_buf()), stream: RefCell::default(), song: RefCell::default(), handlers: RefCell::default(),
            sized: Cell::new(false), hide_src: RefCell::default(), vol_src: RefCell::default(), over_hud: Cell::new(false), last_xy: Cell::new(None),
            syncing: Cell::new(false), shown_secs: Cell::new((-1, -1)), audio: Cell::new(false), trim_bar: RefCell::default(), crop_bar: RefCell::default(),
            export: RefCell::default(), volume: Cell::new(volume), muted: Cell::new(muted), max, picture, overlay, sizers: RefCell::default(), art, message,
            msg_title, reason, hud, mute_btn, volume_scale, play_btn, full_btn, trim_btn, crop_btn, elapsed, remaining, scrubber,
        });
        cell.replace(Some(Rc::downgrade(&me)));
        let hud: gtk::Widget = me.hud.clone().upcast();
        me.add_sizer(&hud); // the window's smallest size
        me.input();
        let w = Rc::downgrade(&me);
        me.win.connect_close_request(move |_| {
            if let Some(m) = w.upgrade() {
                m.close_request();
            }
            glib::Propagation::Proceed
        });
        let w = Rc::downgrade(&me);
        me.win.connect_fullscreened_notify(move |_| {
            if let Some(m) = w.upgrade() {
                m.show_hud();
            }
        });
        let w = Rc::downgrade(&me);
        me.hud.connect_visible_notify(move |_| {
            if let Some(m) = w.upgrade() {
                m.fit_controls();
            }
        });
        me.open(path);
        me
    }

    // -- the window's smallest size ---------------------------------------------------------------
    /// An overlay's own children don't size it: the controls -- and the
    /// trim / crop bars in their place -- set the window's smallest size
    /// (Vini: a song's window cut its volume and time row).
    fn add_sizer(self: &Me, w: &gtk::Widget) {
        self.sizers.borrow_mut().push(w.clone());
        self.fit_controls();
    }

    fn remove_sizer(&self, w: &gtk::Widget) {
        self.sizers.borrow_mut().retain(|x| x != w);
        self.fit_controls();
    }

    /// (Their own visible flag -- get_visible, as window.py reads it; not
    /// is_visible, which is false for anything in a window not shown yet:
    /// the window opened with no smallest size at all.)
    fn fit_controls(&self) {
        let (mut w, mut h) = (0, 0);
        for c in self.sizers.borrow().iter().filter(|c| c.get_visible() && c.parent().as_ref() == Some(self.overlay.upcast_ref())) {
            w = w.max(c.measure(gtk::Orientation::Horizontal, -1).0 + c.margin_start() + c.margin_end());
            h = h.max(c.measure(gtk::Orientation::Vertical, -1).0 + c.margin_top() + c.margin_bottom() + MIN_PICTURE_H);
        }
        self.overlay.set_size_request(if w > 0 { w } else { -1 }, if h > 0 { h } else { -1 });
    }

    // -- file ----------------------------------------------------------------------------------
    fn open(self: &Me, path: &Path) {
        if self.trim_bar.borrow().is_some() {
            self.cancel_trim();
        }
        if self.crop_bar.borrow().is_some() {
            self.cancel_crop();
        }
        self.release();
        self.path.replace(path.to_path_buf());
        self.trim_btn.set_sensitive(false);
        self.crop_btn.set_sensitive(false);
        self.sized.set(false);
        self.win.set_title(Some(&path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()));
        self.message.set_visible(false);
        self.hud.set_visible(true);
        let audio = is_audio(path);
        self.set_audio(audio);
        let factory = FACTORY.with(|f| f.borrow().clone());
        let stream: gtk::MediaStream = if let Some(make) = factory {
            match make(path) {
                Ok(s) => s,
                Err(e) => return self.fail(&e),
            }
        } else if audio && playbin::available() {
            match playbin::PlaybinStream::new(path) {
                Ok(s) => {
                    self.song.replace(Some(s.clone()));
                    s.upcast()
                }
                Err(e) => return self.fail(&e), // never crash on a broken backend
            }
        } else {
            gtk::MediaFile::for_filename(path).upcast()
        };
        stream.set_volume(self.volume.get());
        stream.set_muted(self.muted.get());
        let mut hs = Vec::new();
        for prop in ["prepared", "error", "playing", "ended", "timestamp", "duration"] {
            let w = Rc::downgrade(self);
            hs.push(stream.connect_notify_local(Some(prop), move |_, p| {
                let Some(m) = w.upgrade() else { return };
                match p.name() {
                    "prepared" => m.prepared(),
                    "error" => m.errored(),
                    "playing" => m.playing_changed(),
                    "ended" => m.ended(),
                    _ => m.tick(),
                }
            }));
        }
        self.handlers.replace(hs);
        if !audio {
            self.picture.set_paintable(Some(&stream)); // a song shows its cover instead
        }
        self.stream.replace(Some(stream.clone()));
        if path.exists() {
            gtk::RecentManager::default().add_item(&gio::File::for_path(path).uri());
        }
        if stream.error().is_some() {
            self.errored();
        } else if stream.is_prepared() {
            self.prepared();
        }
        self.playing_changed();
        self.show_hud();
    }

    fn stream(&self) -> Option<gtk::MediaStream> {
        self.stream.borrow().clone()
    }

    fn secs(&self, us: i64) -> f64 {
        us as f64 / 1e6
    }

    fn playable(&self) -> bool {
        self.stream().is_some_and(|s| s.error().is_none() && s.is_prepared() && s.duration() > 0 && s.is_seekable()) && self.path.borrow().exists()
    }

    fn can_edit(&self) -> bool {
        edit::available() && self.playable()
    }

    /// Trim / Crop offered: the movie can be edited -- or only FFmpeg is
    /// missing (then they say so, instead of sitting greyed out).
    fn editable(&self) -> bool {
        self.can_edit() || (!edit::available() && self.playable())
    }

    fn editing(&self) -> bool {
        self.trim_bar.borrow().is_some() || self.crop_bar.borrow().is_some()
    }

    /// No FFmpeg: an alert says what to install (true: nothing to do).
    fn needs_ffmpeg(&self) -> bool {
        if edit::available() {
            return false;
        }
        sonata_ui::dialog::alert(&tr!("Trim and Crop need FFmpeg"), &tr!("Install the “ffmpeg” package, then try again."), &[("ok", tr!("OK"), "default")], |_| {}, Some(self.win.upcast_ref()));
        true
    }

    fn edit_buttons(&self) {
        let on = self.editable() && !self.editing();
        self.trim_btn.set_sensitive(on);
        self.crop_btn.set_sensitive(on && !self.audio.get());
    }

    /// A song: a square window, its cover (read off the main loop), the
    /// controls always there, no full screen.
    fn set_audio(self: &Me, audio: bool) {
        self.audio.set(audio);
        if audio { self.win.add_css_class("vd-audio") } else { self.win.remove_css_class("vd-audio") }
        self.full_btn.set_visible(!audio);
        self.crop_btn.set_visible(!audio);
        self.edit_buttons();
        self.msg_title.set_label(&if audio { tr!("This song can't be played") } else { tr!("This video can't be played") });
        self.art.set_visible(audio);
        if !audio {
            return;
        }
        self.picture.set_paintable(None::<&gtk::gdk::Paintable>); // (the last song's cover)
        if self.win.is_fullscreen() {
            self.toggle_fullscreen();
        }
        self.sized.set(true);
        let side = AUDIO_SIZE.max(self.overlay.measure(gtk::Orientation::Horizontal, -1).0); // whole controls
        self.win.set_default_size(side, side);
        let path = self.path.borrow().clone();
        let me = self.clone();
        glib::spawn_future_local(async move {
            let p = path.clone();
            let Ok(tags) = gio::spawn_blocking(move || playbin::read_tags(&p)).await else { return };
            if *me.path.borrow() != path {
                return; // another file since
            }
            me.win.set_title(Some(&song_title(&tags.title, &tags.artist, &path)));
            if let Some(cover) = tags.cover {
                if let Ok(tex) = gtk::gdk::Texture::from_bytes(&glib::Bytes::from_owned(cover)) {
                    me.picture.set_paintable(Some(&tex));
                    me.picture.set_content_fit(gtk::ContentFit::Cover);
                    me.art.set_visible(false);
                } // (a broken picture: the note stays)
            }
        });
    }

    /// Let go of the current stream (remembering where it was).
    fn release(&self) {
        let Some(s) = self.stream.take() else { return };
        self.remember(&s);
        for h in self.handlers.take() {
            s.disconnect(h);
        }
        s.pause();
        if let Some(f) = s.downcast_ref::<gtk::MediaFile>() {
            f.clear();
        }
        if let Some(song) = self.song.take() {
            song.close(); // a song's playbin
        }
        self.picture.set_paintable(None::<&gtk::gdk::Paintable>);
        self.picture.set_content_fit(gtk::ContentFit::Contain);
    }

    fn remember(&self, s: &gtk::MediaStream) {
        if s.error().is_some() || !s.is_prepared() {
            return;
        }
        let cfg = sonata_config::load(CONFIG, &defaults());
        let pos = positions_after(&cfg, &self.path.borrow().to_string_lossy(), self.secs(s.timestamp()), self.secs(s.duration()));
        let _ = sonata_config::update(CONFIG, &json!({"positions": pos}));
    }

    fn fail(&self, reason: &str) {
        self.reason.set_label(reason);
        self.message.set_visible(true);
        self.hud.set_visible(false);
        self.overlay.set_cursor(None);
    }

    fn errored(&self) {
        let Some(err) = self.stream().and_then(|s| s.error()) else { return };
        let mut msg = err.message().to_string();
        if msg.contains("media module") || msg.to_lowercase().contains("backend") {
            msg = tr!("No media backend is installed (GTK's GStreamer module and its plugins).");
        }
        self.fail(&msg);
    }

    fn prepared(&self) {
        let Some(s) = self.stream().filter(|s| s.is_prepared() && s.error().is_none()) else { return };
        let paintable: &gtk::gdk::Paintable = s.upcast_ref();
        let (w, h) = (paintable.intrinsic_width(), paintable.intrinsic_height());
        if !self.sized.get() && w > 0 && h > 0 && !(self.win.is_fullscreen() || self.win.is_maximized()) {
            self.sized.set(true);
            let (dw, dh) = fit_size(w, h, self.max.0, self.max.1);
            self.win.set_default_size(dw, dh);
        }
        let resume = position_in(&sonata_config::load(CONFIG, &defaults()), &self.path.borrow().to_string_lossy());
        if resume > 0.0 && s.is_seekable() && (s.duration() <= 0 || resume < self.secs(s.duration())) {
            s.seek((resume * 1e6) as i64);
        }
        self.edit_buttons();
        self.tick();
    }

    // -- trim ---------------------------------------------------------------------------------
    fn start_trim(self: &Me) {
        if self.needs_ffmpeg() || self.editing() || !self.can_edit() {
            return;
        }
        let Some(s) = self.stream() else { return };
        s.pause();
        let dur = self.secs(s.duration());
        let w = Rc::downgrade(self);
        let seek: Rc<dyn Fn(f64)> = Rc::new(move |t| {
            if let Some(m) = w.upgrade() {
                m.trim_seek(t);
            }
        });
        let (w1, w2) = (Rc::downgrade(self), Rc::downgrade(self));
        let bar = trimbar::TrimBar::new(dur, 12, seek, move || {
            if let Some(m) = w1.upgrade() {
                m.cancel_trim();
            }
        }, move |a, b| {
            if let Some(m) = w2.upgrade() {
                m.do_trim(a, b);
            }
        });
        bar.set_position(self.secs(s.timestamp()));
        self.hud.set_visible(false);
        self.overlay.add_overlay(&bar.root);
        self.add_sizer(bar.root.upcast_ref()); // (in the controls' place: they size the window too)
        self.overlay.set_cursor(None);
        self.trim_bar.replace(Some(bar.clone()));
        if !self.audio.get() {
            // a song has no frames: a plain strip
            let (b1, b2) = (Rc::downgrade(&bar), Rc::downgrade(&bar));
            let me = Rc::downgrade(self);
            edit::thumbnails(&self.path.borrow(), dur, bar.thumb_count(), 48, move |i, png| {
                if let (Some(b), Ok(tex)) = (b1.upgrade(), gtk::gdk::Texture::from_bytes(&glib::Bytes::from_owned(png))) {
                    b.set_thumbnail(i, tex);
                }
            }, move || {
                // stop once this bar is gone
                let cur = me.upgrade().and_then(|m| m.trim_bar.borrow().clone());
                !matches!((cur, b2.upgrade()), (Some(c), Some(b)) if Rc::ptr_eq(&c, &b))
            });
        }
    }

    fn trim_seek(&self, t: f64) {
        if let Some(s) = self.stream().filter(|s| s.is_seekable()) {
            s.seek((t * 1e6) as i64); // the movie shows the frame at the handle (QuickTime)
        }
        if let Some(b) = self.trim_bar.borrow().as_ref() {
            b.set_position(t);
        }
    }

    fn cancel_trim(self: &Me) {
        if let Some(e) = self.export.take() {
            e.cancel();
        }
        if let Some(b) = self.trim_bar.take() {
            self.overlay.remove_overlay(&b.root);
            self.remove_sizer(b.root.upcast_ref());
        }
        self.hud.set_visible(!self.message.get_visible());
        self.show_hud();
        self.edit_buttons();
    }

    fn do_trim(self: &Me, start: f64, end: f64) {
        let Some(bar) = self.trim_bar.borrow().clone() else { return };
        if self.export.borrow().is_some() {
            return;
        }
        let s = self.stream();
        let dur = s.as_ref().map(|s| self.secs(s.duration())).unwrap_or(end);
        if start <= 0.01 && end >= dur - 0.01 {
            self.cancel_trim(); // nothing cut off: nothing to save
            return;
        }
        if let Some(s) = s {
            s.pause();
        }
        let path = self.path.borrow().clone();
        let out = edit::output_path(&path, &tr!("Trimmed"), self.audio.get());
        bar.exporting(Some(0.0));
        let (b, me, o) = (Rc::downgrade(&bar), Rc::downgrade(self), out.clone());
        let job = edit::Export::start(edit::trim_command(&path, &out, start, end, self.audio.get()), end - start, {
            let b = b.clone();
            move |p| {
                if let Some(b) = b.upgrade() {
                    b.exporting(Some(p));
                }
            }
        }, move |ok, message| {
            let Some(m) = me.upgrade() else { return };
            m.export.take();
            let still = matches!((m.trim_bar.borrow().clone(), b.upgrade()), (Some(c), Some(b)) if Rc::ptr_eq(&c, &b));
            if !still {
                return;
            }
            if ok {
                m.cancel_trim();
                m.open(&o); // the trimmed movie, like QuickTime's document
            } else if let Some(b) = b.upgrade() {
                b.exporting(None);
                b.times.set_label(&if message.is_empty() { tr!("Couldn't trim") } else { sonata_i18n::fmt(&tr!("Couldn't trim: {error}"), &[("error", &message)]) });
            }
        });
        self.export.replace(Some(job));
    }

    // -- crop ---------------------------------------------------------------------------------
    fn start_crop(self: &Me) {
        if self.needs_ffmpeg() || self.editing() || self.audio.get() || !self.can_edit() {
            return;
        }
        let Some(s) = self.stream() else { return };
        let p: &gtk::gdk::Paintable = s.upcast_ref();
        let (iw, ih) = (p.intrinsic_width(), p.intrinsic_height());
        if iw <= 0 || ih <= 0 {
            return;
        }
        s.pause();
        let frame = cropbar::CropFrame::new(iw as i64, ih as i64);
        let (w1, w2) = (Rc::downgrade(self), Rc::downgrade(self));
        let bar = cropbar::CropBar::new(frame, move || {
            if let Some(m) = w1.upgrade() {
                m.cancel_crop();
            }
        }, move |r| {
            if let Some(m) = w2.upgrade() {
                m.do_crop(r);
            }
        });
        // the movie moves up out of the bar's way, the frame with it: every corner can be reached
        self.crop_room(Some(bar.frame.upcast_ref()), true);
        self.hud.set_visible(false);
        self.overlay.add_overlay(&bar.frame);
        self.overlay.add_overlay(&bar.root);
        self.add_sizer(bar.root.upcast_ref());
        self.overlay.set_cursor(None);
        self.crop_bar.replace(Some(bar));
    }

    fn crop_room(&self, frame: Option<&gtk::Widget>, on: bool) {
        let (t, side, b) = if on { CROP_ROOM } else { (0, 0, 0) };
        let pic: gtk::Widget = self.picture.clone().upcast();
        for w in std::iter::once(&pic).chain(frame) {
            w.set_margin_top(t);
            w.set_margin_bottom(b);
            w.set_margin_start(side);
            w.set_margin_end(side);
        }
    }

    fn cancel_crop(self: &Me) {
        if let Some(e) = self.export.take() {
            e.cancel();
        }
        if let Some(b) = self.crop_bar.take() {
            self.overlay.remove_overlay(&b.frame);
            self.overlay.remove_overlay(&b.root);
            self.remove_sizer(b.root.upcast_ref());
        }
        self.crop_room(None, false);
        self.hud.set_visible(!self.message.get_visible());
        self.show_hud();
        self.edit_buttons();
    }

    fn do_crop(self: &Me, rect: (i64, i64, i64, i64)) {
        let Some(bar) = self.crop_bar.borrow().clone() else { return };
        if self.export.borrow().is_some() {
            return;
        }
        let (iw, ih) = bar.frame.size();
        if rect == (0, 0, iw, ih) {
            self.cancel_crop(); // the whole picture: nothing to save
            return;
        }
        let path = self.path.borrow().clone();
        let out = edit::output_path(&path, &tr!("Cropped"), false);
        bar.exporting(Some(0.0));
        let dur = self.stream().map(|s| self.secs(s.duration())).unwrap_or(0.0);
        let (b, me, o) = (Rc::downgrade(&bar), Rc::downgrade(self), out.clone());
        let job = edit::Export::start(edit::crop_command(&path, &out, rect.0, rect.1, rect.2, rect.3), dur, {
            let b = b.clone();
            move |p| {
                if let Some(b) = b.upgrade() {
                    b.exporting(Some(p));
                }
            }
        }, move |ok, message| {
            let Some(m) = me.upgrade() else { return };
            m.export.take();
            let still = matches!((m.crop_bar.borrow().clone(), b.upgrade()), (Some(c), Some(b)) if Rc::ptr_eq(&c, &b));
            if !still {
                return;
            }
            if ok {
                m.cancel_crop();
                m.open(&o);
            } else if let Some(b) = b.upgrade() {
                b.exporting(None);
                b.size.set_label(&if message.is_empty() { tr!("Couldn't crop") } else { sonata_i18n::fmt(&tr!("Couldn't crop: {error}"), &[("error", &message)]) });
            }
        });
        self.export.replace(Some(job));
    }

    // -- playback ------------------------------------------------------------------------------
    fn toggle_play(&self) {
        let Some(s) = self.stream().filter(|s| s.error().is_none()) else { return };
        if s.is_playing() {
            s.pause();
        } else {
            if s.is_ended() {
                s.seek(0);
            }
            s.play();
        }
    }

    fn skip(self: &Me, seconds: f64) {
        let now = self.stream().map(|s| self.secs(s.timestamp())).unwrap_or(0.0);
        self.seek_to(now + seconds);
    }

    fn seek_to(self: &Me, seconds: f64) {
        let Some(s) = self.stream().filter(|s| s.is_seekable()) else { return };
        let dur = self.secs(s.duration());
        let t = if dur > 0.0 { seconds.min(dur) } else { seconds }.max(0.0);
        s.seek((t * 1e6) as i64);
        self.show_hud();
    }

    fn set_volume(&self, v: f64) {
        self.volume.set(v.clamp(0.0, 1.0));
        if self.volume.get() > 0.0 {
            self.muted.set(false);
        }
        self.apply_volume();
        self.syncing.set(true);
        self.volume_scale.set_value(self.volume.get() * 100.0);
        self.syncing.set(false);
    }

    fn volume_changed(&self, v: f64) {
        if !self.syncing.get() {
            self.volume.set(v / 100.0);
            self.muted.set(false);
            self.apply_volume();
        }
    }

    fn toggle_mute(&self) {
        self.muted.set(!self.muted.get());
        self.apply_volume();
    }

    fn apply_volume(&self) {
        if let Some(s) = self.stream() {
            s.set_volume(self.volume.get());
            s.set_muted(self.muted.get());
        }
        self.mute_btn.set_icon_name(&volume_icon(self.volume.get(), self.muted.get()));
        self.mute_btn.set_tooltip_text(Some(&if self.muted.get() { tr!("Unmute") } else { tr!("Mute") }));
        // saved once the slider rests: a drag would rewrite the file every step
        if let Some(id) = self.vol_src.take() {
            sonata_ui::cancel(id);
        }
        let (v, m) = (self.volume.get(), self.muted.get());
        let me = self.win.downgrade();
        let id = glib::timeout_add_local_once(Duration::from_millis(600), move || {
            let _ = me; // (the window may be gone: the setting is still saved)
            save_volume(v, m);
        });
        self.vol_src.replace(Some(id));
    }

    fn toggle_loop(&self) {
        if let Some(s) = self.stream() {
            s.set_loop(!s.is_loop());
        }
    }

    fn playing_changed(self: &Me) {
        let playing = self.stream().is_some_and(|s| s.is_playing());
        self.play_btn.set_icon_name(if playing { "media-playback-pause-symbolic" } else { "media-playback-start-symbolic" });
        self.play_btn.set_tooltip_text(Some(&if playing { tr!("Pause") } else { tr!("Play") }));
        if playing { self.arm_hide() } else { self.show_hud() }
    }

    fn ended(self: &Me) {
        if self.stream().is_some_and(|s| s.is_ended()) {
            self.show_hud();
        }
    }

    fn scrubbed(self: &Me, v: f64) {
        let Some(s) = self.stream() else { return };
        if self.syncing.get() || s.duration() <= 0 {
            return;
        }
        self.seek_to(v / 1000.0 * self.secs(s.duration()));
    }

    /// Clock and scrubber follow the stream (only while they show).
    fn tick(&self) {
        let Some(s) = self.stream() else { return };
        if let Some(b) = self.trim_bar.borrow().as_ref() {
            let ts = self.secs(s.timestamp());
            b.set_position(ts);
            if s.is_playing() && ts >= b.strip.end() {
                s.pause(); // trimming: plays the part kept only
            }
            return;
        }
        if self.hud.has_css_class("hidden") {
            return;
        }
        let (ts, dur) = (self.secs(s.timestamp()), self.secs(s.duration()));
        let secs = (ts as i64, ((dur - ts).max(0.0) + 0.999) as i64);
        if secs != self.shown_secs.get() {
            self.shown_secs.set(secs);
            self.elapsed.set_label(&fmt_time(ts, false));
            self.remaining.set_label(&fmt_time((dur - ts).max(0.0), true));
        }
        self.scrubber.set_sensitive(dur > 0.0 && s.is_seekable());
        if dur > 0.0 {
            let v = ts / dur * 1000.0;
            if (self.scrubber.value() - v).abs() >= 0.5 {
                self.syncing.set(true);
                self.scrubber.set_value(v);
                self.syncing.set(false);
            }
        }
    }

    // -- controls fading -----------------------------------------------------------------------
    fn show_hud(self: &Me) {
        if self.hud.has_css_class("hidden") {
            self.hud.remove_css_class("hidden");
            self.hud.set_can_target(true);
            self.shown_secs.set((-1, -1));
            self.tick();
        }
        self.overlay.set_cursor(None);
        self.arm_hide();
    }

    fn arm_hide(self: &Me) {
        if let Some(id) = self.hide_src.take() {
            sonata_ui::cancel(id);
        }
        if self.stream().is_some_and(|s| s.is_playing()) && !self.audio.get() {
            // (a song: always there)
            let w = Rc::downgrade(self);
            let id = glib::timeout_add_local_once(Duration::from_millis(HIDE_AFTER), move || {
                if let Some(m) = w.upgrade() {
                    m.hide_src.take();
                    m.hide_now();
                }
            });
            self.hide_src.replace(Some(id));
        }
    }

    fn hide_now(&self) {
        if self.over_hud.get() || self.audio.get() || self.editing() || !self.stream().is_some_and(|s| s.is_playing()) {
            return;
        }
        self.hud.add_css_class("hidden");
        self.hud.set_can_target(false);
        self.overlay.set_cursor_from_name(Some("none"));
    }

    fn toggle_fullscreen(&self) {
        if self.audio.get() && !self.win.is_fullscreen() {
            return; // a song: no full screen (QuickTime)
        }
        let full = !self.win.is_fullscreen();
        if full { self.win.fullscreen() } else { self.win.unfullscreen() }
        self.full_btn.set_icon_name(if full { "view-restore-symbolic" } else { "view-fullscreen-symbolic" });
        self.full_btn.set_tooltip_text(Some(&if full { tr!("Exit Full Screen") } else { tr!("Enter Full Screen") }));
    }

    // -- input ---------------------------------------------------------------------------------
    fn input(self: &Me) {
        let keys = gtk::EventControllerKey::new();
        let w = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, k, _, state| match w.upgrade() {
            Some(m) if m.key(k, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.win.add_controller(keys);
        let motion = gtk::EventControllerMotion::new();
        let w = Rc::downgrade(self);
        motion.connect_motion(move |_, x, y| {
            let Some(m) = w.upgrade() else { return };
            if m.last_xy.get().is_none_or(|(lx, ly)| (x - lx).abs() + (y - ly).abs() > 1.0) {
                m.last_xy.set(Some((x, y)));
                m.show_hud();
            }
        });
        let w = Rc::downgrade(self);
        motion.connect_leave(move |_| {
            let Some(m) = w.upgrade() else { return };
            m.last_xy.set(None);
            if m.stream().is_some_and(|s| s.is_playing()) && !m.win.is_fullscreen() {
                m.hide_now(); // the pointer left the movie: controls go (QuickTime)
            }
        });
        self.overlay.add_controller(motion);
        let hud_motion = gtk::EventControllerMotion::new();
        let w = Rc::downgrade(self);
        hud_motion.connect_enter(move |_, _, _| {
            if let Some(m) = w.upgrade() {
                m.over_hud.set(true);
            }
        });
        let w = Rc::downgrade(self);
        hud_motion.connect_leave(move |_| {
            if let Some(m) = w.upgrade() {
                m.over_hud.set(false);
                m.arm_hide();
            }
        });
        self.hud.add_controller(hud_motion);
        let click = gtk::GestureClick::new();
        click.set_propagation_phase(gtk::PropagationPhase::Capture);
        let w = Rc::downgrade(self);
        click.connect_pressed(move |g, n, _, _| {
            if n == 2 {
                // the movie, not the window handle: full screen
                g.set_state(gtk::EventSequenceState::Claimed);
                if let Some(m) = w.upgrade() {
                    m.toggle_fullscreen();
                }
            }
        });
        if let Some(handle) = self.picture.parent() {
            handle.add_controller(click);
        }
        // before the movie's window handle (capture): its own right-click
        // (the window menu) took the press, and the menu never showed (Vini)
        let menu = gtk::GestureClick::builder().button(gtk::gdk::BUTTON_SECONDARY).build();
        menu.set_propagation_phase(gtk::PropagationPhase::Capture);
        let w = Rc::downgrade(self);
        menu.connect_pressed(move |g, _, x, y| {
            g.set_state(gtk::EventSequenceState::Claimed);
            if let Some(m) = w.upgrade() {
                m.context_menu(x, y);
            }
        });
        self.overlay.add_controller(menu);
    }

    fn context_menu(self: &Me, x: f64, y: f64) {
        use sonata_ui::menu::{popup, Item, Options};
        let s = self.stream();
        let ok = s.as_ref().is_some_and(|s| s.error().is_none());
        let playing = ok && s.as_ref().is_some_and(|s| s.is_playing());
        let looping = ok && s.as_ref().is_some_and(|s| s.is_loop());
        let w = Rc::downgrade(self);
        let on = move |f: fn(&Me)| {
            let w = w.clone();
            move || {
                if let Some(m) = w.upgrade() {
                    f(&m);
                }
            }
        };
        let (l, mu) = (on(|m| m.toggle_loop()), on(|m| m.toggle_mute()));
        let edit = self.editable() && !self.editing();
        popup(&self.overlay, vec![
            vec![Item::new(&if playing { tr!("Pause") } else { tr!("Play") }, on(|m| m.toggle_play())).enabled(ok)],
            vec![Item::check(&tr!("Loop"), looping, move |_| l()).enabled(ok), Item::check(&tr!("Mute"), self.muted.get(), move |_| mu()).enabled(ok)],
            vec![Item::new(&if self.win.is_fullscreen() { tr!("Exit Full Screen") } else { tr!("Enter Full Screen") }, on(|m| m.toggle_fullscreen())).enabled(!self.audio.get())],
            vec![Item::new(&tr!("Trim…"), on(|m| m.start_trim())).enabled(edit), Item::new(&tr!("Crop…"), on(|m| m.start_crop())).enabled(edit && !self.audio.get())],
            vec![Item::new(&tr!("Show in Files"), on(|m| reveal(&m.path.borrow()))), Item::new(&tr!("Open…"), on(|m| choose(&m.win.application().expect("an app"), Some(&m.win))))],
        ], Options::at(x, y));
    }

    fn key(self: &Me, keyval: gtk::gdk::Key, state: gtk::gdk::ModifierType) -> bool {
        use gtk::gdk::{Key, ModifierType as M};
        let cmd = state.intersects(M::CONTROL_MASK | M::SUPER_MASK);
        let k = keyval.to_lower();
        if self.editing() && !cmd && [Key::Return, Key::KP_Enter, Key::Escape].contains(&k) {
            let crop = self.crop_bar.borrow().clone();
            let trim = self.trim_bar.borrow().clone();
            if let Some(c) = crop {
                if k == Key::Escape { self.cancel_crop() } else { self.do_crop(c.frame.rect()) }
            } else if k == Key::Escape {
                self.cancel_trim();
            } else if let Some(t) = trim {
                self.do_trim(t.strip.start(), t.strip.end());
            }
            return true;
        }
        if cmd {
            match k {
                Key::w => self.win.close(),
                Key::o => choose(&self.win.application().expect("an app"), Some(&self.win)),
                Key::f => self.toggle_fullscreen(),
                Key::t => self.start_trim(),
                _ => return false,
            }
            return true;
        }
        match k {
            Key::space | Key::k => self.toggle_play(),
            Key::Left => self.skip(-5.0),
            Key::Right => self.skip(5.0),
            Key::j => self.skip(-10.0),
            Key::l => self.skip(10.0),
            Key::Up => self.set_volume(self.volume.get() + 0.1),
            Key::Down => self.set_volume(self.volume.get() - 0.1),
            Key::m => self.toggle_mute(),
            Key::f => self.toggle_fullscreen(),
            Key::Escape => {
                if self.win.is_fullscreen() {
                    self.toggle_fullscreen();
                }
                return true;
            }
            _ => return false,
        }
        self.show_hud();
        true
    }

    fn close_request(&self) {
        if let Some(e) = self.export.take() {
            e.cancel(); // closed while saving: stopped, nothing half-made left
        }
        if let Some(id) = self.hide_src.take() {
            sonata_ui::cancel(id);
        }
        if let Some(id) = self.vol_src.take() {
            sonata_ui::cancel(id); // closed right after a change: still saved
            save_volume(self.volume.get(), self.muted.get());
        }
        self.release();
    }
}

fn save_volume(volume: f64, muted: bool) {
    let _ = sonata_config::update(CONFIG, &json!({"volume": (volume * 1000.0).round_ties_even() / 1000.0, "muted": muted}));
}

/// Files shows the file (FileManager1), else its folder opens.
fn reveal(path: &Path) {
    let uri = gio::File::for_path(path).uri().to_string();
    let folder = path.parent().map(Path::to_path_buf).unwrap_or_default();
    let fallback = move || {
        let _ = sonata_ui::apps::launch_default_for_uri(&gio::File::for_path(&folder).uri(), None);
    };
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return fallback() };
    bus.call(Some("org.freedesktop.FileManager1"), "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1", "ShowItems", Some(&(vec![uri], "").to_variant()), None, gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE, move |r| {
        if r.is_err() {
            fallback();
        }
    });
}

/// Makes a file's stream instead of media_file's choice (window.py's
/// `stream_factory`: tests).
type Factory = Rc<dyn Fn(&Path) -> Result<gtk::MediaStream, String>>;

thread_local! {
    static WINDOWS: RefCell<Vec<Me>> = RefCell::default();
    static FACTORY: RefCell<Option<Factory>> = RefCell::default();
}

/// Files' Open panel: movies and songs.
fn choose(app: &gtk::Application, parent: Option<&gtk::ApplicationWindow>) {
    use sonata_files::chooser::{Chooser, Mode, Options, MIME};
    let a = app.clone();
    let opts = Options {
        mode: Mode::Open,
        title: tr!("Open"),
        accept_label: String::new(),
        multiple: true,
        filters: vec![(tr!("Movies & Songs"), vec![(MIME, "video/*".into()), (MIME, "audio/*".into())])],
        current_filter: 0,
        folder: None,
        name: String::new(),
    };
    let dlg = Chooser::new(app, opts, Box::new(move |uris, _| open_paths(&a, &uris.unwrap_or_default())));
    if let Some(p) = parent {
        dlg.window().set_transient_for(Some(p));
        dlg.window().set_modal(true);
    }
    dlg.present();
}

fn open_paths(app: &gtk::Application, paths: &[String]) {
    WINDOWS.with(|w| w.borrow_mut().retain(|v| v.win.application().is_some()));
    for p in paths {
        let Some(path) = gio::File::for_commandline_arg(p).path() else { continue };
        let same = WINDOWS.with(|w| w.borrow().iter().find(|v| *v.path.borrow() == path).cloned());
        let v = same.unwrap_or_else(|| {
            let v = Video::new(app, &path);
            WINDOWS.with(|w| w.borrow_mut().push(v.clone()));
            v
        });
        v.win.present();
    }
    if paths.is_empty() {
        let first = WINDOWS.with(|w| w.borrow().first().cloned());
        match first {
            Some(v) => v.win.present(),
            None if app.windows().is_empty() => choose(app, None),
            None => {}
        }
    }
}

/// `sonata2-rs videos [FILE...]`: a window per movie or song.
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = gtk::Application::builder().application_id(app_id()).flags(gio::ApplicationFlags::HANDLES_OPEN).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let ready = Rc::new(Cell::new(false));
    let start = move || {
        if !ready.replace(true) {
            sonata_ui::setup();
            if let Some(f) = force {
                sonata_ui::force_appearance(f);
            }
            sonata_ui::css::register_module("videos/window.py");
            sonata_ui::css::register_module("videos/trimbar.py");
        }
    };
    let s = start.clone();
    app.connect_open(move |app, files, _| {
        s();
        let paths: Vec<String> = files.iter().filter_map(|f| f.path()).map(|p| p.display().to_string()).collect();
        open_paths(app, &paths);
    });
    app.connect_activate(move |app| {
        start();
        open_paths(app, &[]);
    });
    // (a closed window lets go of its movie: the list drops it)
    app.connect_window_removed(|_, w| WINDOWS.with(|l| l.borrow_mut().retain(|v| v.win.upcast_ref::<gtk::Window>() != w)));
    let mut argv = vec![std::env::args().next().unwrap_or_default()];
    argv.extend(args.iter().filter(|a| !a.starts_with("--")).cloned());
    app.run_with_args(&argv)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clock_like_quicktime() {
        assert_eq!(fmt_time(0.0, false), "0:00");
        assert_eq!(fmt_time(5.7, false), "0:05");
        assert_eq!(fmt_time(754.0, false), "12:34");
        assert_eq!(fmt_time(3723.0, false), "1:02:03");
        assert_eq!(fmt_time(44.2, true), "-0:45");
        assert_eq!(fmt_time(0.0, true), "-0:00");
        assert_eq!(fmt_time(-3.0, false), "0:00");
    }

    #[test]
    fn windows_fit_the_screen_never_enlarged() {
        assert_eq!(fit_size(1920, 1080, 1344.0, 756.0), (1344, 756));
        assert_eq!(fit_size(640, 480, 1344.0, 756.0), (640, 480));
        let (w, h) = fit_size(1080, 1920, 1344.0, 756.0); // portrait
        assert!(h <= 756 && w < h);
    }

    #[test]
    fn positions_remembered_between_the_ends() {
        let cfg: Map<String, Value> = serde_json::from_value(defaults()).unwrap();
        let mut c = cfg.clone();
        c.insert("positions".into(), Value::Object(positions_after(&c, "/m/a.mp4", 42.3, 600.0)));
        assert_eq!(position_in(&c, "/m/a.mp4"), 42.3);
        c.insert("positions".into(), Value::Object(positions_after(&c, "/m/a.mp4", 3.0, 600.0))); // near the start: forgotten
        assert_eq!(position_in(&c, "/m/a.mp4"), 0.0);
        c.insert("positions".into(), Value::Object(positions_after(&c, "/m/b.mp4", 595.0, 600.0))); // near the end
        assert_eq!(position_in(&c, "/m/b.mp4"), 0.0);
        for i in 0..250 {
            c.insert("positions".into(), Value::Object(positions_after(&c, &format!("/m/{i}.mp4"), 100.0, 600.0)));
        }
        let pos = c["positions"].as_object().unwrap();
        assert_eq!(pos.len(), MAX_POSITIONS);
        assert!(pos.contains_key("/m/249.mp4") && !pos.contains_key("/m/0.mp4")); // the oldest dropped
    }

    /// videos.json as window.py reads it: whatever Python accepted is read
    /// the same (a position or volume written as text, a 1 for muted).
    #[test]
    fn test_settings_python_wrote_read_the_same() {
        let cfg = |v: Value| -> Map<String, Value> { serde_json::from_value(v).unwrap() };
        assert_eq!(position_in(&cfg(json!({"positions": {"/m/a.mp4": "42.5"}})), "/m/a.mp4"), 42.5);
        assert_eq!(position_in(&cfg(json!({"positions": {"/m/a.mp4": 30}})), "/m/a.mp4"), 30.0);
        assert_eq!(position_in(&cfg(json!({"positions": {"/m/a.mp4": null}})), "/m/a.mp4"), 0.0);
        assert_eq!(position_in(&cfg(json!({"positions": {"/m/a.mp4": "soon"}})), "/m/a.mp4"), 0.0);
        assert_eq!(position_in(&cfg(json!({"positions": ["/m/a.mp4"]})), "/m/a.mp4"), 0.0);
        assert_eq!(volume_of(&cfg(json!({"volume": "0.5", "muted": 1}))), (0.5, true));
        assert_eq!(volume_of(&cfg(json!({"volume": null, "muted": "yes"}))), (0.0, true));
        assert_eq!(volume_of(&cfg(json!({"volume": 7, "muted": 0}))), (1.0, false));
        assert_eq!(volume_of(&cfg(json!({}))), (1.0, false));
    }

    mod fake {
        use gtk::prelude::*;
        use gtk::subclass::prelude::*;

        #[derive(Default)]
        pub struct Fake;

        #[glib::object_subclass]
        impl ObjectSubclass for Fake {
            const NAME: &'static str = "SonataRsVideosFakeStream";
            type Type = super::FakeStream;
            type ParentType = gtk::MediaStream;
            type Interfaces = (gtk::gdk::Paintable,);
        }

        impl ObjectImpl for Fake {}

        impl gtk::gdk::subclass::paintable::PaintableImpl for Fake {
            fn snapshot(&self, s: &gtk::gdk::Snapshot, w: f64, h: f64) {
                let s = s.downcast_ref::<gtk::Snapshot>().unwrap();
                s.append_color(&gtk::gdk::RGBA::new(0.2, 0.4, 0.6, 1.0), &gtk::graphene::Rect::new(0.0, 0.0, w as f32, h as f32));
            }

            fn intrinsic_width(&self) -> i32 {
                1920
            }

            fn intrinsic_height(&self) -> i32 {
                800
            }
        }

        impl MediaStreamImpl for Fake {
            fn play(&self) -> bool {
                true
            }

            fn pause(&self) {}

            fn seek(&self, ts: i64) {
                self.obj().seek_success();
                self.obj().update(ts);
            }
        }
    }

    glib::wrapper! {
        /// A 90 s, 1920 x 800 movie that plays nothing (tests/test_videos.py's FakeStream).
        pub struct FakeStream(ObjectSubclass<fake::Fake>) @extends gtk::MediaStream, @implements gtk::gdk::Paintable;
    }

    fn settle(ms: u64) {
        let end = std::time::Instant::now() + Duration::from_millis(ms);
        let ctx = glib::MainContext::default();
        while std::time::Instant::now() < end {
            ctx.iteration(false);
            std::thread::sleep(Duration::from_millis(2));
        }
    }

    fn wait(cond: impl Fn() -> bool, ms: u64) -> bool {
        let end = std::time::Instant::now() + Duration::from_millis(ms);
        let ctx = glib::MainContext::default();
        while !cond() && std::time::Instant::now() < end {
            ctx.iteration(false);
            std::thread::sleep(Duration::from_millis(2));
        }
        cond()
    }

    fn use_fake() {
        FACTORY.with(|f| {
            f.replace(Some(Rc::new(|_: &Path| {
                let s: FakeStream = glib::Object::new();
                s.stream_prepared(true, true, true, 90_000_000);
                Ok(s.upcast())
            })))
        });
    }

    fn ffprobe(args: &[&str], path: &Path) -> String {
        let out = std::process::Command::new("ffprobe").args(["-v", "error"]).args(args).args(["-of", "csv=p=0"]).arg(path).output().unwrap();
        String::from_utf8_lossy(&out.stdout).trim().to_string()
    }

    /// tests/test_videos_trim.py's and test_videos_crop.py's real ones:
    /// ffmpeg trims to the frame and crops to even sizes; a cancelled
    /// export leaves nothing.
    fn real_exports() {
        if sonata_backend::run::which("ffmpeg").is_none() || sonata_backend::run::which("ffprobe").is_none() {
            return;
        }
        let d = tempfile::tempdir().unwrap();
        let src = d.path().join("clip.mp4");
        let ok = std::process::Command::new("ffmpeg").args(["-v", "error", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=4", "-f", "lavfi", "-i", "sine=duration=4", "-shortest", "-c:v", "libx264", "-g", "100"]).arg(&src).status().unwrap();
        assert!(ok.success());
        let out = edit::output_path(&src, "Trimmed", false);
        let result: Rc<RefCell<Option<(bool, String)>>> = Rc::default();
        let r = result.clone();
        let _job = edit::Export::start(edit::trim_command(&src, &out, 1.0, 2.5, false), 1.5, |_| {}, move |ok, m| {
            r.replace(Some((ok, m)));
        });
        assert!(wait(|| result.borrow().is_some(), 20000));
        assert!(result.borrow().as_ref().unwrap().0, "{:?}", result.borrow());
        let len: f64 = ffprobe(&["-show_entries", "format=duration"], &out).parse().unwrap();
        assert!((len - 1.5).abs() < 0.1, "{len}"); // to the frame, not the keyframe
        let len: f64 = ffprobe(&["-show_entries", "format=duration"], &src).parse().unwrap();
        assert!((len - 4.0).abs() < 0.1); // the original untouched
        let frames: Rc<RefCell<Vec<(usize, Vec<u8>)>>> = Rc::default();
        let f = frames.clone();
        edit::thumbnails(&src, 4.0, 3, 48, move |i, png| f.borrow_mut().push((i, png[..4].to_vec())), || false);
        assert!(wait(|| frames.borrow().len() == 3, 20000));
        assert!(frames.borrow().iter().all(|(_, p)| p == b"\x89PNG"));
        // crop: even sizes
        let out = edit::output_path(&src, "Cropped", false);
        result.replace(None);
        let r = result.clone();
        let _job = edit::Export::start(edit::crop_command(&src, &out, 100, 50, 301, 201), 4.0, |_| {}, move |ok, m| {
            r.replace(Some((ok, m)));
        });
        assert!(wait(|| result.borrow().is_some(), 20000));
        assert!(result.borrow().as_ref().unwrap().0);
        assert_eq!(ffprobe(&["-select_streams", "v:0", "-show_entries", "stream=width,height"], &out), "300,200");
        // cancelled: nothing half-made left, no "done"
        let out = d.path().join("x (Trimmed).mp4");
        std::fs::write(&out, "").unwrap();
        let done = Rc::new(Cell::new(false));
        let dn = done.clone();
        let job = edit::Export::start(vec!["sh".into(), "-c".into(), "exec sleep 5".into(), out.display().to_string()], 5.0, |_| {}, move |_, _| dn.set(true));
        settle(150);
        job.cancel();
        settle(600);
        assert!(!out.exists());
        assert!(!done.get());
    }

    /// The Rust Videos opened with no smallest size: its controls were
    /// measured as hidden (is_visible: false in a window not shown yet), so
    /// a shrunk window cut them -- what Vini saw in the Python one before
    /// 7670e43c. With it, tests/test_videos.py's, test_videos_trim.py's and
    /// test_videos_crop.py's window tests, on a fake stream (one test: GTK
    /// lives on one thread).
    #[test]
    fn test_videos_window_opened_with_no_smallest_size() {
        let gtk_ok = !(gtk::is_initialized() && !gtk::is_initialized_main_thread()) && gtk::init().is_ok();
        let ctx = glib::MainContext::default();
        let _owner = if gtk_ok { None } else { ctx.acquire().ok() };
        real_exports();
        if !gtk_ok {
            return; // no display
        }
        sonata_ui::setup();
        sonata_ui::css::register_module("videos/window.py");
        sonata_ui::css::register_module("videos/trimbar.py");
        let app = gtk::Application::builder().application_id("io.test.videos").flags(gio::ApplicationFlags::NON_UNIQUE).build();
        app.register(gio::Cancellable::NONE).expect("registered");
        use_fake();
        let d = tempfile::tempdir().unwrap();
        let movie = d.path().join("movie.mp4");
        std::fs::write(&movie, "").unwrap();
        let song = d.path().join("small.mp3");
        std::fs::write(&song, "").unwrap();

        // test_window_never_smaller_than_its_controls: Vini -- the song /
        // movie window had no smallest size; shrunk, it cut the controls
        // (volume and the time row). Now it can't get narrower than them,
        // nor shorter than them plus some picture.
        for path in [&movie, &song] {
            let v = Video::new(&app, path);
            v.win.set_default_size(120, 80);
            v.win.present();
            settle(300);
            let hud = &v.hud;
            let need_w = hud.measure(gtk::Orientation::Horizontal, -1).0 + hud.margin_start() + hud.margin_end();
            let need_h = hud.measure(gtk::Orientation::Vertical, -1).0 + hud.margin_top() + hud.margin_bottom() + MIN_PICTURE_H;
            assert!(v.overlay.width() >= need_w, "{path:?}");
            assert!(v.overlay.height() >= need_h, "{path:?}");
            let b = hud.compute_bounds(&v.overlay).unwrap();
            assert!(b.x() >= 0.0 && b.x() + b.width() <= v.overlay.width() as f32, "{path:?}"); // the whole HUD inside
            v.win.destroy();
            settle(50);
        }

        // a song's window opens square, its controls whole
        let v = Video::new(&app, &song);
        let (w, h) = v.win.default_size();
        assert_eq!(w, h);
        assert!(w >= v.overlay.measure(gtk::Orientation::Horizontal, -1).0);
        assert!(!v.crop_btn.get_visible() && v.trim_btn.get_visible()); // a song has no Crop
        v.start_crop();
        assert!(v.crop_bar.borrow().is_none()); // not for songs
        v.win.destroy();

        // a stream that can't be made: the reason, no controls
        FACTORY.with(|f| f.replace(Some(Rc::new(|_: &Path| Err("no decoder".to_string())))));
        let v = Video::new(&app, Path::new("/x.mkv"));
        assert!(v.message.get_visible() && !v.hud.get_visible());
        assert_eq!(v.reason.label(), "no decoder");
        v.toggle_play(); // harmless
        v.win.destroy();
        use_fake();

        // playback, and the place it was left
        let path = Path::new("/movies/resume.mp4");
        let cfg = sonata_config::load(CONFIG, &defaults());
        let _ = sonata_config::update(CONFIG, &json!({"positions": positions_after(&cfg, &path.to_string_lossy(), 30.0, 90.0)}));
        let v = Video::new(&app, path);
        v.win.present();
        settle(200);
        let s = v.stream().unwrap();
        assert!((s.timestamp() as f64 / 1e6 - 30.0).abs() < 1e-3); // resumed
        assert_eq!(v.elapsed.label(), "0:30");
        assert_eq!(v.remaining.label(), "-1:00");
        v.toggle_play();
        assert!(s.is_playing());
        assert_eq!(v.play_btn.icon_name().as_deref(), Some("media-playback-pause-symbolic"));
        v.skip(10.0);
        assert!((s.timestamp() as f64 / 1e6 - 40.0).abs() < 1e-3);
        v.skip(-100.0);
        assert_eq!(s.timestamp(), 0);
        v.seek_to(500.0); // clamped to the end
        assert_eq!(s.timestamp(), 90_000_000);
        v.seek_to(55.0);
        v.hide_now(); // playing: controls and pointer hide
        assert!(v.hud.has_css_class("hidden"));
        v.toggle_play(); // paused: controls come back
        assert!(!v.hud.has_css_class("hidden"));
        v.set_volume(0.3);
        assert!((s.volume() - 0.3).abs() < 1e-6);
        v.toggle_mute();
        assert!(s.is_muted());
        v.toggle_loop();
        assert!(s.is_loop());
        v.win.close();
        settle(50);
        let cfg = sonata_config::load(CONFIG, &defaults());
        assert_eq!(position_in(&cfg, &path.to_string_lossy()), 55.0); // remembered on close
        assert!(volume_of(&cfg).1); // muted, saved on close

        // test_right_click_menu_comes_before_the_window_handle: Vini -- Trim
        // and Crop didn't show: the movie is a window handle, whose own
        // right-click (the window menu) took the press before the player's
        // menu. The menu's click comes first (capture phase).
        let v = Video::new(&app, &movie);
        v.win.present();
        settle(200);
        let ctrls = v.overlay.observe_controllers();
        let menu = (0..ctrls.n_items()).filter_map(|i| ctrls.item(i).and_downcast::<gtk::GestureClick>()).find(|g| g.button() == gtk::gdk::BUTTON_SECONDARY).expect("the right-click");
        assert_eq!(menu.propagation_phase(), gtk::PropagationPhase::Capture);
        let handle = v.picture.parent().unwrap();
        assert!(handle.is::<gtk::WindowHandle>() && handle.is_ancestor(&v.overlay)); // captured on the way down to it

        // test_trim_and_crop_buttons_in_the_controls: Vini (option A) --
        // Trim and Crop in sight in the controls, beside full screen
        let top = v.hud.first_child().and_downcast::<gtk::CenterBox>().unwrap();
        let end = top.end_widget().unwrap();
        let mut kids = Vec::new();
        let mut c = end.first_child();
        while let Some(w) = c {
            c = w.next_sibling();
            kids.push(w);
        }
        assert_eq!(kids, [v.trim_btn.clone().upcast::<gtk::Widget>(), v.crop_btn.clone().upcast(), v.full_btn.clone().upcast()]);
        if edit::available() {
            assert!(v.trim_btn.is_sensitive() && v.crop_btn.is_sensitive());
            // the trim bar takes the controls' place
            v.trim_btn.emit_clicked();
            assert!(v.trim_bar.borrow().is_some());
            assert!(!v.hud.get_visible());
            v.cancel_trim();
            assert!(v.trim_bar.borrow().is_none() && v.hud.get_visible());
            v.win.destroy();
            // (the bars are measured in a window shown with them: Broadway
            // lays a window out again only when a browser draws it)
            let v = Video::new(&app, &movie);
            v.start_trim();
            v.win.present();
            settle(300);
            let bar = v.trim_bar.borrow().clone().expect("trimming");
            let strip = &bar.strip;
            assert!(strip.width() > 300, "{}", strip.width());
            strip.set_range(10.0, 40.0);
            assert!(bar.times.label().contains("(0:30)"));
            assert_eq!(strip.pick(strip.x_of(10.0) - 6.0), "start");
            assert_eq!(strip.pick(strip.x_of(40.0) + 6.0), "end");
            assert_eq!(strip.pick(strip.x_of(25.0)), "playhead");
            // the dimming and the playhead leave the theme's shared colours alone
            let black = sonata_ui::rgba("sys_black").to_string();
            v.trim_seek(20.0);
            settle(100);
            assert_eq!(sonata_ui::rgba("sys_black").to_string(), black);
            v.start_crop(); // one at a time
            assert!(v.crop_bar.borrow().is_none());
            v.do_trim(0.0, 90.0); // nothing cut off: nothing saved
            assert!(v.trim_bar.borrow().is_none() && v.export.borrow().is_none());
            assert!(v.hud.get_visible());

            // crop: the frame and its shapes
            v.win.destroy();
            let v = Video::new(&app, &movie);
            v.start_crop();
            assert!(!v.hud.get_visible());
            v.win.present();
            settle(300);
            let bar = v.crop_bar.borrow().clone().expect("cropping");
            assert_eq!(bar.frame.rect(), (0, 0, 1920, 800)); // the whole picture to start
            bar.pick("1:1");
            assert_eq!(bar.frame.rect(), (560, 0, 800, 800));
            assert_eq!(bar.size.label(), "800 × 800");
            // the frame's bottom corners stay above the bar (review: the bar covered them)
            settle(100);
            let (iw, ih) = bar.frame.size();
            let (ox, oy, sc) = edit::video_rect(iw, ih, bar.frame.width() as f64, bar.frame.height() as f64);
            let (_, fy, _, fh) = bar.frame.rect();
            let bottom = oy + (fy + fh) as f64 * sc;
            let bb = bar.root.compute_bounds(&bar.frame).unwrap();
            assert!(bottom < bb.y() as f64, "{bottom} {} {ox}", bb.y());
            v.do_crop((0, 0, 1920, 800)); // the whole picture: nothing saved
            assert!(v.crop_bar.borrow().is_none() && v.export.borrow().is_none());
            assert!(v.hud.get_visible());
            assert_eq!(v.picture.margin_bottom(), 0); // the movie back in place
        }
        v.win.destroy();
        settle(50);
        FACTORY.with(|f| f.replace(None));
    }

    #[test]
    fn volume_icons_and_titles() {
        assert_eq!(volume_icon(0.5, true), "sonata-volume-muted-symbolic");
        assert_eq!(volume_icon(0.1, false), "sonata-volume-1-symbolic");
        assert_eq!(volume_icon(0.5, false), "sonata-volume-2-symbolic");
        assert_eq!(volume_icon(1.0, false), "sonata-volume-3-symbolic");
        assert_eq!(song_title("Song", "Band", Path::new("/m/a.mp3")), "Band — Song");
        assert_eq!(song_title("Song", "", Path::new("/m/a.mp3")), "Song");
        assert_eq!(song_title("", "", Path::new("/m/a.mp3")), "a.mp3");
        assert!(is_audio(Path::new("/m/a.mp3")) && is_audio(Path::new("/m/a.flac")));
        assert!(!is_audio(Path::new("/m/a.mp4")));
    }
}
