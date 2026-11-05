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
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.videos"
    } else {
        "io.github.vinioliveiras.sonata2.videos"
    }
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

/// Where the movie was left (seconds), 0 for none.
pub fn position_in(cfg: &Map<String, Value>, path: &str) -> f64 {
    cfg.get("positions").and_then(|p| p.get(path)).and_then(Value::as_f64).unwrap_or(0.0)
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
        let volume = cfg.get("volume").and_then(Value::as_f64).unwrap_or(1.0).clamp(0.0, 1.0);
        let muted = cfg.get("muted").and_then(Value::as_bool).unwrap_or(false);
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

    fn fit_controls(&self) {
        let (mut w, mut h) = (0, 0);
        for c in self.sizers.borrow().iter().filter(|c| c.is_visible() && c.parent().as_ref() == Some(self.overlay.upcast_ref())) {
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
        let stream: gtk::MediaStream = if audio && playbin::available() {
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
        self.hud.set_visible(!self.message.is_visible());
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
        self.hud.set_visible(!self.message.is_visible());
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
            id.remove();
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
            id.remove();
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
            id.remove();
        }
        if let Some(id) = self.vol_src.take() {
            id.remove(); // closed right after a change: still saved
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
        let _ = gio::AppInfo::launch_default_for_uri(&gio::File::for_path(&folder).uri(), gio::AppLaunchContext::NONE);
    };
    let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return fallback() };
    bus.call(Some("org.freedesktop.FileManager1"), "/org/freedesktop/FileManager1", "org.freedesktop.FileManager1", "ShowItems", Some(&(vec![uri], "").to_variant()), None, gio::DBusCallFlags::NONE, 3000, gio::Cancellable::NONE, move |r| {
        if r.is_err() {
            fallback();
        }
    });
}

thread_local! {
    static WINDOWS: RefCell<Vec<Me>> = RefCell::default();
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
