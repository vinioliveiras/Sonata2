//! Camera (camera/window.py; the layout of the iPhone's Camera): black all
//! round, the picture in the middle, the mode under it (VIDEO / PHOTO, the
//! chosen one in yellow) and one row of controls: the last photo or video
//! on the left, the shutter in the middle (a white ring; red inside for
//! video, a red rounded square while recording), the camera switch on the
//! right. Along the top: flash (the screen lights up white for the photo),
//! the timer (3 s), the options (camera, mirror); while recording, the
//! time in a red pill.
//!
//! Photos go to Pictures/Camera, videos (with the microphone) to
//! Videos/Camera. The thumbnail opens the last one; right-click: Open, Show
//! in Files, Move to Trash. Keys: Space or Return take the photo / start or
//! stop the video, Esc cancels the timer, ← / → switch the mode.
//!
//! Its own binary (sonata2-rs-camera): only it needs GStreamer.

pub mod engine;

use gtk::prelude::*;
use serde_json::{json, Value};
use sonata_backend::userdirs::{self, UserDirectory};
use sonata_i18n::{n_, tr};
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::time::Duration;

const CONFIG: &str = "camera";
pub const MODES: [(&str, &str); 2] = [("video", n_!("VIDEO")), ("photo", n_!("PHOTO"))];
const COUNT_FROM: u32 = 3;
/// the screen is white this long before the photo (it lights the face)
const FLASH_LEAD_MS: u64 = 180;
const THUMB: i32 = 44;
const MEDIA: [&str; 5] = [".jpg", ".jpeg", ".png", ".mp4", ".webm"];

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.camera"
    } else {
        "io.github.vinioliveiras.sonata2.camera"
    }
}

fn defaults() -> Value {
    json!({"camera": "", "mirror": true, "countdown": false, "flash": false, "mode": "photo"})
}

// -- pure logic (tested) ----------------------------------------------------------------------
fn folder(kind: UserDirectory) -> PathBuf {
    userdirs::special(kind, true).unwrap_or_else(glib::home_dir).join("Camera")
}

pub fn photos_dir() -> PathBuf {
    folder(UserDirectory::Pictures)
}

pub fn videos_dir() -> PathBuf {
    folder(UserDirectory::Videos)
}

/// "Photo 2026-10-03 at 04.16.10.jpg"
pub fn photo_name(now: &glib::DateTime, prefix: &str, ext: &str) -> String {
    let base = now.format(&format!("{prefix} %Y-%m-%d at %H.%M.%S")).map(|s| s.to_string()).unwrap_or_else(|_| prefix.to_string());
    if ext.is_empty() { base } else { format!("{base}.{ext}") }
}

/// name in folder, "name 2.jpg" and so on when it exists (two photos a second).
pub fn unique(folder: &Path, name: &str) -> PathBuf {
    let (base, ext) = match name.rfind('.') {
        Some(i) if i > 0 => (&name[..i], &name[i..]),
        _ => (name, ""),
    };
    let mut path = folder.join(name);
    let mut n = 2;
    while path.exists() {
        path = folder.join(format!("{base} {n}{ext}"));
        n += 1;
    }
    path
}

/// Photos and videos, newest first.
pub fn recent_photos(folder: &Path, limit: usize) -> Vec<PathBuf> {
    let Ok(rd) = std::fs::read_dir(folder) else { return Vec::new() };
    let mut dated: Vec<(std::time::SystemTime, PathBuf)> = rd
        .flatten()
        .filter(|e| { let n = e.file_name().to_string_lossy().to_lowercase(); MEDIA.iter().any(|m| n.ends_with(m)) })
        // a dangling link, or deleted meanwhile: skipped, never a crash
        .filter_map(|e| std::fs::metadata(e.path()).and_then(|m| m.modified()).ok().map(|t| (t, e.path())))
        .collect();
    dated.sort_by(|a, b| b.0.cmp(&a.0));
    dated.into_iter().take(limit).map(|(_, p)| p).collect()
}

fn mtime(p: &Path) -> std::time::SystemTime {
    std::fs::metadata(p).and_then(|m| m.modified()).unwrap_or(std::time::UNIX_EPOCH)
}

/// The newest photo or video of both folders.
pub fn last_capture() -> Option<PathBuf> {
    recent_photos(&photos_dir(), 1).into_iter().chain(recent_photos(&videos_dir(), 1)).max_by_key(|p| mtime(p))
}

/// The middle square of a picture, size x size (the thumbnail, iOS).
fn square_texture(pb: &gtk::gdk_pixbuf::Pixbuf, size: i32) -> Option<gtk::gdk::Texture> {
    let (w, h) = (pb.width(), pb.height());
    let side = w.min(h);
    let sq = pb.new_subpixbuf((w - side) / 2, (h - side) / 2, side, side);
    let scaled = sq.scale_simple(size, size, gtk::gdk_pixbuf::InterpType::Bilinear)?;
    #[allow(deprecated)]
    Some(gtk::gdk::Texture::for_pixbuf(&scaled))
}

/// "00:01:05"
pub fn fmt_time(seconds: i64) -> String {
    let s = seconds.max(0);
    format!("{:02}:{:02}:{:02}", s / 3600, (s % 3600) / 60, s % 60)
}

fn is_video(p: &Path) -> bool {
    let n = p.to_string_lossy().to_lowercase();
    n.ends_with(".mp4") || n.ends_with(".webm")
}

// -- the window ------------------------------------------------------------------------------------
struct CameraWin {
    win: gtk::ApplicationWindow,
    cam: Rc<engine::Camera>,
    cfg: RefCell<serde_json::Map<String, Value>>,
    cams: RefCell<Vec<engine::Cam>>,
    mode: RefCell<String>,
    last_photo: RefCell<Option<PathBuf>>,
    count_src: RefCell<Option<glib::SourceId>>,
    rec_src: RefCell<Option<glib::SourceId>>,
    rec_t0: Cell<i64>,
    closed: Cell<bool>,
    flash_btn: gtk::Button,
    timer_btn: gtk::Button,
    rec_time: gtk::Label,
    picture: gtk::Picture,
    count: gtk::Label,
    msg: gtk::Box,
    msg_title: gtk::Label,
    msg_hint: gtk::Label,
    flash: gtk::Box,
    blink: gtk::Box,
    mode_btns: Vec<(String, gtk::Button)>,
    last_btn: gtk::Button,
    last_pic: gtk::Picture,
    shutter: gtk::Button,
    switch_btn: gtk::Button,
}

impl CameraWin {
    fn new(app: &gtk::Application) -> Rc<Self> {
        let win = gtk::ApplicationWindow::builder().application(app).title(tr!("Camera").as_str()).css_classes(["sonata-camera"]).build();
        sonata_ui::window::remember_size(win.upcast_ref(), "camera", 820, 700); // (never bigger than the display)
        sonata_ui::window::standard(&win);
        win.set_size_request(460, 480);
        let cfg = sonata_config::load(CONFIG, &defaults());
        let cell: Rc<RefCell<Option<std::rc::Weak<CameraWin>>>> = Rc::default();
        let c = cell.clone();
        let cam = engine::Camera::new(move |text| {
            if let Some(me) = c.borrow().as_ref().and_then(|w| w.upgrade()) {
                me.show_error(&sonata_i18n::gettext(&text), None);
            }
        });
        // top bar
        let bar = gtk::CenterBox::builder().css_classes(["cam-top"]).build();
        let left = gtk::Box::builder().spacing(4).valign(gtk::Align::Center).build();
        let flash_btn = gtk::Button::builder().icon_name("sonata-flash-symbolic").css_classes(["cam-icon"]).tooltip_text(tr!("Flash").as_str()).build();
        left.append(&flash_btn);
        bar.set_start_widget(Some(&left));
        let rec_time = gtk::Label::builder().label(fmt_time(0).as_str()).css_classes(["cam-rec-time"]).valign(gtk::Align::Center).visible(false).build();
        bar.set_center_widget(Some(&rec_time));
        let right = gtk::Box::builder().spacing(4).valign(gtk::Align::Center).build();
        let timer_btn = gtk::Button::builder().icon_name("timer-symbolic").css_classes(["cam-icon"]).tooltip_text(tr!("Timer (3 s)").as_str()).build();
        right.append(&timer_btn);
        let menu_btn = gtk::Button::builder().icon_name("view-more-symbolic").css_classes(["cam-icon"]).tooltip_text(tr!("Options").as_str()).build();
        right.append(&menu_btn);
        bar.set_end_widget(Some(&right));
        // stage
        let stage = gtk::Overlay::builder().css_classes(["cam-stage"]).vexpand(true).hexpand(true).build();
        let picture = gtk::Picture::builder().content_fit(gtk::ContentFit::Contain).can_shrink(true).hexpand(true).vexpand(true).build();
        stage.set_child(Some(&picture));
        let count = gtk::Label::builder().css_classes(["cam-count"]).halign(gtk::Align::Center).valign(gtk::Align::Center).visible(false).can_target(false).build();
        stage.add_overlay(&count);
        let msg = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).halign(gtk::Align::Center).valign(gtk::Align::Center).visible(false).build();
        let msg_title = gtk::Label::builder().css_classes(["cam-message"]).build();
        let msg_hint = gtk::Label::builder().css_classes(["cam-hint"]).wrap(true).justify(gtk::Justification::Center).max_width_chars(48).build();
        msg.append(&msg_title);
        msg.append(&msg_hint);
        stage.add_overlay(&msg);
        let flash = gtk::Box::builder().css_classes(["cam-flash"]).can_target(false).opacity(0.0).build();
        stage.add_overlay(&flash);
        let blink = gtk::Box::builder().css_classes(["cam-blink"]).can_target(false).opacity(0.0).build();
        stage.add_overlay(&blink);
        // bottom
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(10).css_classes(["cam-bottom"]).build();
        let modes = gtk::Box::builder().spacing(6).halign(gtk::Align::Center).build();
        let mode_btns: Vec<(String, gtk::Button)> = MODES.iter().map(|(k, l)| {
            let b = gtk::Button::builder().label(sonata_i18n::gettext(l).as_str()).css_classes(["cam-mode"]).build();
            modes.append(&b);
            (k.to_string(), b)
        }).collect();
        col.append(&modes);
        let row = gtk::CenterBox::builder().margin_start(28).margin_end(28).build();
        let last_btn = gtk::Button::builder().css_classes(["cam-last"]).valign(gtk::Align::Center).tooltip_text(tr!("Open").as_str()).build();
        let last_pic = gtk::Picture::builder().content_fit(gtk::ContentFit::Cover).css_classes(["cam-last-pic"]).overflow(gtk::Overflow::Hidden).can_shrink(true).build();
        last_btn.set_child(Some(&sonata_ui::fixed::FixedWidth::new(&last_pic, THUMB))); // a square whatever the photo's shape
        row.set_start_widget(Some(&last_btn));
        let shutter = gtk::Button::builder().css_classes(["cam-shutter"]).tooltip_text(tr!("Take a Photo").as_str()).halign(gtk::Align::Center).valign(gtk::Align::Center).sensitive(false).build();
        shutter.set_child(Some(&gtk::Box::builder().css_classes(["cam-core"]).halign(gtk::Align::Center).valign(gtk::Align::Center).build()));
        row.set_center_widget(Some(&shutter));
        let switch_btn = gtk::Button::builder().icon_name("camera-switch-symbolic").css_classes(["cam-switch"]).valign(gtk::Align::Center).tooltip_text(tr!("Switch Camera").as_str()).build();
        row.set_end_widget(Some(&switch_btn));
        col.append(&row);
        let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
        root.append(&bar);
        root.append(&stage);
        root.append(&col);
        win.set_child(Some(&root));
        let me = Rc::new(Self {
            win, cam, cfg: RefCell::new(cfg), cams: RefCell::default(), mode: RefCell::new("photo".into()), last_photo: RefCell::default(),
            count_src: RefCell::default(), rec_src: RefCell::default(), rec_t0: Cell::new(0), closed: Cell::new(false),
            flash_btn, timer_btn, rec_time, picture, count, msg, msg_title, msg_hint, flash, blink, mode_btns, last_btn, last_pic, shutter, switch_btn,
        });
        cell.replace(Some(Rc::downgrade(&me)));
        me.wire(&menu_btn);
        me.sync_toggles();
        let mode = me.cfg.borrow().get("mode").and_then(Value::as_str).unwrap_or("photo").to_string();
        me.set_mode(&mode, false);
        me.show_last(last_capture(), None);
        let m = me.clone();
        glib::idle_add_local_once(move || m.open_camera()); // the window first, then the camera
        me
    }

    fn wire(self: &Rc<Self>, menu_btn: &gtk::Button) {
        let w = Rc::downgrade(self);
        let on = move |f: fn(&Rc<CameraWin>)| {
            let w = w.clone();
            move || {
                if let Some(m) = w.upgrade() {
                    f(&m);
                }
            }
        };
        let f = on(|m| m.toggle("flash"));
        self.flash_btn.connect_clicked(move |_| f());
        let f = on(|m| m.toggle("countdown"));
        self.timer_btn.connect_clicked(move |_| f());
        let f = on(|m| m.menu());
        menu_btn.connect_clicked(move |_| f());
        for (k, b) in &self.mode_btns {
            let (w, k) = (Rc::downgrade(self), k.clone());
            b.connect_clicked(move |_| {
                if let Some(m) = w.upgrade() {
                    m.set_mode(&k, true);
                }
            });
        }
        let f = on(|m| {
            let last = m.last_photo.borrow().clone();
            if let Some(p) = last {
                open(&p);
            }
        });
        self.last_btn.connect_clicked(move |_| f());
        let click = gtk::GestureClick::builder().button(3).build();
        let w = Rc::downgrade(self);
        click.connect_pressed(move |_, _, x, y| {
            if let Some(m) = w.upgrade() {
                m.last_menu(x, y);
            }
        });
        self.last_btn.add_controller(click);
        let f = on(|m| m.capture());
        self.shutter.connect_clicked(move |_| f());
        let f = on(|m| m.next_camera());
        self.switch_btn.connect_clicked(move |_| f());
        let keys = gtk::EventControllerKey::new();
        let w = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, k, _, state| match w.upgrade() {
            Some(m) if m.key(k, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.win.add_controller(keys);
        let w = Rc::downgrade(self);
        self.win.connect_close_request(move |_| {
            if let Some(m) = w.upgrade() {
                m.cancel_count();
                m.stop_video();
                m.cam.stop();
                m.closed.set(true);
            }
            glib::Propagation::Proceed
        });
    }

    // -- camera ---------------------------------------------------------------------------------
    fn open_camera(self: &Rc<Self>) {
        // the device probe can take a while (USB, PipeWire): off the main loop
        let me = self.clone();
        glib::spawn_future_local(async move {
            let cams = gio::spawn_blocking(engine::devices).await.unwrap_or_default();
            if me.closed.get() {
                return;
            }
            me.cams.replace(cams);
            me.start_camera();
        });
    }

    fn start_camera(self: &Rc<Self>) {
        let cams = self.cams.borrow().clone();
        if cams.is_empty() {
            self.show_error(&tr!("No Camera"), Some(&tr!("Connect a camera, or check that no other app is using it.")));
            self.switch_btn.set_sensitive(false);
            return;
        }
        self.switch_btn.set_sensitive(cams.len() > 1);
        let want = self.cfg.borrow().get("camera").and_then(Value::as_str).unwrap_or("").to_string();
        let cam = cams.iter().find(|c| c.name == want).or(cams.first()).cloned();
        self.msg.set_visible(false);
        let mirror = self.cfg.borrow().get("mirror").and_then(Value::as_bool).unwrap_or(true);
        if self.cam.start(cam.as_ref(), mirror) {
            self.picture.set_paintable(self.cam.paintable.borrow().as_ref());
            self.shutter.set_sensitive(true);
        }
    }

    /// From the camera: "Camera Unavailable" with the text under; or a title and a hint.
    fn show_error(self: &Rc<Self>, text: &str, hint: Option<&str>) {
        let (title, hint) = match hint {
            None => (tr!("Camera Unavailable"), text.to_string()),
            Some(h) => (text.to_string(), h.to_string()),
        };
        self.msg_title.set_label(&title);
        self.msg_hint.set_label(&hint);
        self.msg.set_visible(true);
        self.shutter.set_sensitive(false);
        self.stop_video();
    }

    fn save(&self) {
        let _ = sonata_config::save(CONFIG, &self.cfg.borrow());
    }

    fn switch(self: &Rc<Self>, name: &str) {
        if self.cam.recording() {
            return;
        }
        self.cfg.borrow_mut().insert("camera".into(), json!(name));
        self.save();
        self.open_camera();
    }

    fn next_camera(self: &Rc<Self>) {
        let names: Vec<String> = self.cams.borrow().iter().map(|c| c.name.clone()).collect();
        if names.len() < 2 {
            return;
        }
        let cur = self.cfg.borrow().get("camera").and_then(Value::as_str).map(String::from).filter(|c| names.contains(c)).unwrap_or_else(|| names[0].clone());
        let i = names.iter().position(|n| *n == cur).unwrap_or(0);
        self.switch(&names[(i + 1) % names.len()]);
    }

    fn flag(&self, key: &str) -> bool {
        self.cfg.borrow().get(key).and_then(Value::as_bool).unwrap_or(defaults()[key].as_bool().unwrap_or(false))
    }

    fn toggle(&self, key: &str) {
        self.set(key, !self.flag(key));
        self.sync_toggles();
    }

    fn sync_toggles(&self) {
        for (btn, key) in [(&self.flash_btn, "flash"), (&self.timer_btn, "countdown")] {
            if self.flag(key) { btn.add_css_class("on") } else { btn.remove_css_class("on") }
        }
    }

    fn set(&self, key: &str, value: bool) {
        self.cfg.borrow_mut().insert(key.into(), json!(value));
        self.save();
        if key == "mirror" {
            self.cam.set_mirror(value);
        }
    }

    fn menu(self: &Rc<Self>) {
        use sonata_ui::menu::{popup, Item, Options};
        let current = self.cfg.borrow().get("camera").and_then(Value::as_str).filter(|s| !s.is_empty()).map(String::from).or_else(|| self.cams.borrow().first().map(|c| c.name.clone())).unwrap_or_default();
        let mut sections = Vec::new();
        let cams: Vec<Item> = self.cams.borrow().iter().map(|c| {
            let (w, n) = (Rc::downgrade(self), c.name.clone());
            Item::check(&c.name, c.name == current, move |_| {
                if let Some(m) = w.upgrade() {
                    m.switch(&n);
                }
            })
        }).collect();
        if !cams.is_empty() {
            sections.push(cams);
        }
        let w = Rc::downgrade(self);
        sections.push(vec![Item::check(&tr!("Mirror Image"), self.flag("mirror"), move |on| {
            if let Some(m) = w.upgrade() {
                m.set("mirror", on);
            }
        })]);
        let anchor = self.timer_btn.parent().unwrap_or_else(|| self.timer_btn.clone().upcast());
        popup(&anchor, sections, Options::below());
    }

    // -- modes ------------------------------------------------------------------------------------
    fn set_mode(&self, mode: &str, save: bool) {
        if self.cam.recording() || self.count_src.borrow().is_some() || !MODES.iter().any(|m| m.0 == mode) {
            return;
        }
        self.mode.replace(mode.into());
        for (k, b) in &self.mode_btns {
            if k == mode { b.add_css_class("selected") } else { b.remove_css_class("selected") }
        }
        if mode == "video" { self.shutter.add_css_class("video") } else { self.shutter.remove_css_class("video") }
        self.shutter.set_tooltip_text(Some(&if mode == "video" { tr!("Record") } else { tr!("Take a Photo") }));
        self.flash_btn.set_visible(mode == "photo");
        if save {
            self.cfg.borrow_mut().insert("mode".into(), json!(mode));
            self.save();
        }
    }

    fn key(self: &Rc<Self>, k: gtk::gdk::Key, state: gtk::gdk::ModifierType) -> bool {
        use gtk::gdk::{Key, ModifierType as M};
        if state.intersects(M::CONTROL_MASK | M::ALT_MASK) {
            return false;
        }
        match k {
            Key::space | Key::Return | Key::KP_Enter => self.capture(),
            Key::Escape if self.count_src.borrow().is_some() => self.cancel_count(),
            Key::Left | Key::Right => {
                let keys: Vec<&str> = MODES.iter().map(|m| m.0).collect();
                let i = keys.iter().position(|x| *x == self.mode.borrow().as_str()).unwrap_or(0) as i32 + if k == Key::Right { 1 } else { -1 };
                self.set_mode(keys[i.clamp(0, keys.len() as i32 - 1) as usize], true);
            }
            _ => return false,
        }
        true
    }

    // -- photos and videos --------------------------------------------------------------------------
    /// The shutter: a photo (after the timer), or start / stop the video.
    fn capture(self: &Rc<Self>) {
        let video = *self.mode.borrow() == "video";
        if video && self.cam.recording() {
            self.stop_video();
            return;
        }
        if self.count_src.borrow().is_some() || !self.cam.running() {
            return;
        }
        let go: Rc<dyn Fn()> = {
            let me = self.clone();
            Rc::new(move || if video { me.start_video() } else { me.photo() })
        };
        if !self.flag("countdown") {
            go();
            return;
        }
        let left = Rc::new(Cell::new(COUNT_FROM));
        self.count.set_label(&COUNT_FROM.to_string());
        self.count.set_visible(true);
        let me = self.clone();
        let id = glib::timeout_add_local(Duration::from_secs(1), move || {
            left.set(left.get() - 1);
            if left.get() == 0 {
                me.count_src.take();
                me.count.set_visible(false);
                go();
                return glib::ControlFlow::Break;
            }
            me.count.set_label(&left.get().to_string());
            glib::ControlFlow::Continue
        });
        self.count_src.replace(Some(id));
    }

    fn cancel_count(&self) {
        if let Some(id) = self.count_src.take() {
            id.remove();
        }
        self.count.set_visible(false);
    }

    fn photo(self: &Rc<Self>) {
        if self.flag("flash") {
            // the screen lights up first, the photo in that light
            self.flash.set_opacity(1.0);
            let me = self.clone();
            glib::timeout_add_local_once(Duration::from_millis(FLASH_LEAD_MS), move || {
                me.snap();
                fade(&me.flash, 300, 1.0);
            });
        } else {
            self.snap();
            self.blink.set_opacity(0.9); // iOS: the picture blinks
            fade(&self.blink, 220, 0.9);
        }
    }

    fn snap(&self) {
        let folder = photos_dir();
        if std::fs::create_dir_all(&folder).is_err() {
            return;
        }
        let now = glib::DateTime::now_local().expect("a clock");
        let path = unique(&folder, &photo_name(&now, "Photo", "jpg"));
        if !self.cam.photo(&path) {
            return;
        }
        sonata_backend::sounds::play("screenshot");
        self.show_last(Some(path), None);
    }

    fn start_video(self: &Rc<Self>) {
        let folder = videos_dir();
        if std::fs::create_dir_all(&folder).is_err() {
            return;
        }
        let now = glib::DateTime::now_local().expect("a clock");
        let base = unique(&folder, &photo_name(&now, "Video", ""));
        if self.cam.start_recording(&base.to_string_lossy()).is_none() {
            return;
        }
        self.shutter.add_css_class("recording");
        self.shutter.set_tooltip_text(Some(&tr!("Stop")));
        for (_, b) in &self.mode_btns {
            b.set_sensitive(false);
        }
        self.switch_btn.set_sensitive(false);
        self.last_btn.set_sensitive(false);
        self.rec_time.set_label(&fmt_time(0));
        self.rec_time.set_visible(true);
        self.rec_t0.set(glib::monotonic_time());
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_local(Duration::from_millis(500), move || {
            let Some(m) = w.upgrade() else { return glib::ControlFlow::Break };
            m.rec_time.set_label(&fmt_time((glib::monotonic_time() - m.rec_t0.get()) / 1_000_000));
            glib::ControlFlow::Continue
        });
        self.rec_src.replace(Some(id));
    }

    fn stop_video(self: &Rc<Self>) {
        if let Some(id) = self.rec_src.take() {
            id.remove();
        }
        self.rec_time.set_visible(false);
        self.shutter.remove_css_class("recording");
        self.shutter.set_tooltip_text(Some(&if *self.mode.borrow() == "video" { tr!("Record") } else { tr!("Take a Photo") }));
        for (_, b) in &self.mode_btns {
            b.set_sensitive(true);
        }
        self.switch_btn.set_sensitive(self.cams.borrow().len() > 1);
        self.last_btn.set_sensitive(true);
        if self.cam.recording() {
            let thumb = self.frame_texture();
            let w = Rc::downgrade(self);
            self.cam.stop_recording(move |path| {
                if let Some(m) = w.upgrade() {
                    m.show_last(Some(PathBuf::from(path)), thumb.clone());
                }
            });
        }
    }

    // -- the last photo / video ----------------------------------------------------------------------
    /// The frame on screen, small (a video's thumbnail).
    fn frame_texture(&self) -> Option<gtk::gdk::Texture> {
        let jpeg = self.cam.frame_jpeg()?; // in memory: no shared temp file
        let loader = gtk::gdk_pixbuf::PixbufLoader::new();
        loader.write(&jpeg).ok()?;
        loader.close().ok()?;
        square_texture(&loader.pixbuf()?, THUMB * 2)
    }

    fn show_last(&self, path: Option<PathBuf>, texture: Option<gtk::gdk::Texture>) {
        let mut tex = texture;
        if let (Some(p), None) = (&path, &tex) {
            if !is_video(p) {
                tex = gtk::gdk_pixbuf::Pixbuf::from_file_at_scale(p, THUMB * 4, THUMB * 4, true).ok().and_then(|pb| square_texture(&pb, THUMB * 2));
            }
        }
        self.last_pic.set_paintable(tex.as_ref());
        self.last_btn.set_opacity(if path.is_some() { 1.0 } else { 0.0 });
        self.last_btn.set_can_target(path.is_some());
        self.last_photo.replace(path);
    }

    fn last_menu(self: &Rc<Self>, x: f64, y: f64) {
        use sonata_ui::menu::{popup, Item, Options};
        let Some(path) = self.last_photo.borrow().clone() else { return };
        let (p1, p2, p3) = (path.clone(), path.clone(), path);
        let w = Rc::downgrade(self);
        popup(&self.last_btn, vec![
            vec![Item::new(&tr!("Open"), move || open(&p1)), Item::new(&tr!("Show in Files"), move || reveal(&p2))],
            vec![Item::new(&tr!("Move to Trash"), move || {
                if gio::File::for_path(&p3).trash(gio::Cancellable::NONE).is_ok() {
                    if let Some(m) = w.upgrade() {
                        m.show_last(last_capture(), None);
                    }
                }
            })],
        ], Options::at(x, y));
    }
}

/// `widget` fades out over `ms` (from `peak`).
fn fade(widget: &gtk::Box, ms: i64, peak: f64) {
    let t0 = glib::monotonic_time();
    let w = widget.downgrade();
    glib::timeout_add_local(Duration::from_millis(16), move || {
        let Some(w) = w.upgrade() else { return glib::ControlFlow::Break };
        let t = (glib::monotonic_time() - t0) as f64 / (ms * 1000) as f64;
        w.set_opacity((1.0 - t).max(0.0).powi(2) * peak);
        if t < 1.0 { glib::ControlFlow::Continue } else { glib::ControlFlow::Break }
    });
}

fn open(path: &Path) {
    let _ = gio::AppInfo::launch_default_for_uri(&gio::File::for_path(path).uri(), gio::AppLaunchContext::NONE);
}

/// Files shows it in its folder (FileManager1), else the folder opens.
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

/// `sonata2-rs camera`: one Camera window (a camera can't be shared between two).
pub fn run(args: &[String]) -> glib::ExitCode {
    if !engine::init() {
        eprintln!("sonata2-rs-camera: GStreamer couldn't start");
        return glib::ExitCode::FAILURE;
    }
    let app = gtk::Application::builder().application_id(app_id()).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let state: Rc<RefCell<Option<Rc<CameraWin>>>> = Rc::default();
    app.connect_activate(move |app| {
        if let Some(c) = state.borrow().as_ref() {
            c.win.present();
            return;
        }
        sonata_ui::setup();
        if let Some(f) = force {
            sonata_ui::force_appearance(f);
        }
        sonata_ui::css::register_module("camera/window.py");
        let c = CameraWin::new(app);
        c.win.present();
        state.replace(Some(c));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_times() {
        let t = glib::DateTime::from_local(2026, 10, 3, 4, 16, 10.0).unwrap();
        assert_eq!(photo_name(&t, "Photo", "jpg"), "Photo 2026-10-03 at 04.16.10.jpg");
        assert_eq!(photo_name(&t, "Video", ""), "Video 2026-10-03 at 04.16.10");
        assert_eq!(fmt_time(0), "00:00:00");
        assert_eq!(fmt_time(3725), "01:02:05");
        assert_eq!(fmt_time(-5), "00:00:00");
    }

    #[test]
    fn two_photos_in_a_second_get_their_own_names() {
        let d = tempfile::tempdir().unwrap();
        assert_eq!(unique(d.path(), "Photo.jpg"), d.path().join("Photo.jpg"));
        std::fs::write(d.path().join("Photo.jpg"), "").unwrap();
        assert_eq!(unique(d.path(), "Photo.jpg"), d.path().join("Photo 2.jpg"));
        std::fs::write(d.path().join("Photo 2.jpg"), "").unwrap();
        assert_eq!(unique(d.path(), "Photo.jpg"), d.path().join("Photo 3.jpg"));
        assert_eq!(unique(d.path(), "Video"), d.path().join("Video"));
    }

    #[test]
    fn recent_photos_newest_first_skipping_broken_links() {
        let d = tempfile::tempdir().unwrap();
        for (i, n) in ["a.jpg", "b.mp4", "c.txt"].iter().enumerate() {
            let p = d.path().join(n);
            std::fs::write(&p, "").unwrap();
            let f = std::fs::File::options().write(true).open(&p).unwrap();
            f.set_modified(std::time::UNIX_EPOCH + Duration::from_secs(1000 + i as u64 * 10)).unwrap();
        }
        std::os::unix::fs::symlink(d.path().join("gone.jpg"), d.path().join("dangling.jpg")).unwrap();
        assert_eq!(recent_photos(d.path(), 10), [d.path().join("b.mp4"), d.path().join("a.jpg")]);
        assert_eq!(recent_photos(d.path(), 1).len(), 1);
        assert!(recent_photos(&d.path().join("none"), 5).is_empty());
    }
}
