//! The desktop's wallpaper (shell/wallpaper.py): a layer-shell background
//! surface per display showing the picture set in Sonata's desktop
//! settings (picture-uri / picture-uri-dark in system.json), updated live,
//! with the desktop's icons on top. A video plays there too (video.rs):
//! its poster shows first, the video fades in over it once it has a frame.
//!
//! `sonata2-rs wallpaper [--preview]`. On trial (or with --preview) it is a
//! plain window: the Python wallpaper stays the desktop's background, and
//! the icons' spots are written to the trial's own desktop.json.

use crate::desktop::Desktop;
use crate::video::{self, DisplayCover, VideoWallpaper};
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, gio, glib};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.wallpaper"
    } else {
        "io.github.vinioliveiras.sonata2.wallpaper"
    }
}

/// The picture's size scaled to just cover w x h (never enlarged).
pub fn cover_size(img_w: i32, img_h: i32, w: i32, h: i32) -> (i32, i32) {
    if img_w <= 0 || img_h <= 0 || w <= 0 || h <= 0 {
        return (img_w, img_h);
    }
    let f = (w as f64 / img_w as f64).max(h as f64 / img_h as f64).min(1.0);
    (((img_w as f64 * f).round_ties_even() as i32).max(1), ((img_h as f64 * f).round_ties_even() as i32).max(1))
}

/// A picture decoded off the main loop, made a texture on it.
pub struct Decoded {
    bytes: glib::Bytes,
    w: i32,
    h: i32,
    stride: usize,
    alpha: bool,
}

/// The picture decoded at the display's size: a 4K photo kept whole held
/// ~40 MB of graphics memory per copy (Vini: Sonata's wallpaper process had
/// 228 MB on the card while a game needed it all). None when unreadable.
pub fn decode(path: &std::path::Path, w: i32, h: i32) -> Option<Decoded> {
    let (_, iw, ih) = gtk::gdk_pixbuf::Pixbuf::file_info(path)?;
    let (tw, th) = cover_size(iw, ih, w, h);
    let pb = gtk::gdk_pixbuf::Pixbuf::from_file_at_scale(path, tw, th, true).ok()?;
    Some(Decoded { bytes: pb.read_pixel_bytes(), w: pb.width(), h: pb.height(), stride: pb.rowstride() as usize, alpha: pb.has_alpha() })
}

impl Decoded {
    pub fn texture(&self) -> gdk::Texture {
        let fmt = if self.alpha { gdk::MemoryFormat::R8g8b8a8 } else { gdk::MemoryFormat::R8g8b8 };
        gdk::MemoryTexture::new(self.w, self.h, fmt, &self.bytes, self.stride).upcast()
    }
}

const GREETER_FOLDER: &str = "/var/lib/sonata-greeter";
const VIDEO_NAME: &str = "wallpaper-video"; // beside "wallpaper" (the picture, or the video's poster)

thread_local! {
    static SHARED: RefCell<Option<(String, Option<String>)>> = const { RefCell::new(None) };
}

/// The login screen (greeter.py) shows this user's wallpaper: copied where
/// the greeter can read it (install.sh --greeter makes the folder, owned
/// by this user). Nothing to do without that folder. A video: its poster
/// as the picture and the video beside it; a picture removes the video
/// left from before.
fn share_with_login_screen(f: &gio::File, video: Option<&str>) {
    let folder = std::path::Path::new(GREETER_FOLDER).join(glib::user_name());
    let key = (f.uri().to_string(), video.map(String::from));
    let Ok(c) = std::ffi::CString::new(folder.as_os_str().as_encoded_bytes()) else { return };
    // SAFETY: access() only reads the path
    let writable = unsafe { libc::access(c.as_ptr(), libc::W_OK) } == 0;
    if !writable || SHARED.with(|s| s.borrow().as_ref() == Some(&key)) {
        return;
    }
    SHARED.with(|s| s.replace(Some(key)));
    let copy = |src: &gio::File, dest: std::path::PathBuf| {
        src.copy_async(&gio::File::for_path(dest), gio::FileCopyFlags::OVERWRITE, glib::Priority::LOW, gio::Cancellable::NONE, None, |_| {});
    };
    copy(f, folder.join("wallpaper"));
    let vdest = folder.join(VIDEO_NAME);
    match video {
        Some(v) => copy(&gio::File::for_path(v), vdest),
        None => {
            let _ = std::fs::remove_file(vdest);
        }
    }
}

// -- a box that says its size (GtkFixed allocates through its layout
//    manager: it has no size to follow of its own) ------------------------------------------

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SizeBin {
        pub on_size: RefCell<Option<Box<dyn Fn(i32, i32)>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SizeBin {
        const NAME: &'static str = "SonataWallpaperBin";
        type Type = super::SizeBin;
        type ParentType = gtk::Widget;

        fn class_init(klass: &mut Self::Class) {
            klass.set_layout_manager_type::<gtk::BinLayout>();
        }
    }

    impl ObjectImpl for SizeBin {
        fn dispose(&self) {
            while let Some(c) = self.obj().first_child() {
                c.unparent();
            }
        }
    }

    impl WidgetImpl for SizeBin {
        fn size_allocate(&self, width: i32, height: i32, baseline: i32) {
            self.parent_size_allocate(width, height, baseline);
            if let Some(f) = self.on_size.borrow().as_ref() {
                f(width, height);
            }
        }
    }
}

glib::wrapper! {
    pub struct SizeBin(ObjectSubclass<imp::SizeBin>) @extends gtk::Widget;
}

// -- the window ------------------------------------------------------------------------------

/// One per display, each with its desktop (icons start on the main one).
pub struct WallpaperWindow {
    pub window: gtk::ApplicationWindow,
    pics: [gtk::Picture; 2],
    stack: gtk::Stack,
    uri: RefCell<Option<String>>,
    video: RefCell<Option<Rc<VideoWallpaper>>>,
    cover: RefCell<Option<DisplayCover>>,
    frame_id: RefCell<Option<glib::SignalHandlerId>>,
    monitor: Option<gdk::Monitor>,
    loaded_for: Cell<(i32, i32)>,
    pub desktop: Option<Rc<Desktop>>,
    /// the main display's (in the session): its picture is the login screen's
    share: bool,
    prefs_mon: RefCell<Vec<gio::FileMonitor>>,
    dark_id: RefCell<Option<glib::SignalHandlerId>>,
    me: Weak<WallpaperWindow>,
}

impl WallpaperWindow {
    /// layer: a background surface (else a plain window: the preview).
    pub fn new(app: &gtk::Application, monitor: Option<&gdk::Monitor>, desktop: bool, main: bool, layer: bool) -> Rc<Self> {
        let window = gtk::ApplicationWindow::builder().application(app).title("Wallpaper").css_classes(["sonata-wallpaper"]).decorated(!layer).resizable(true).build();
        if !layer {
            window.set_default_size(1280, 800);
        }
        // two pictures in a cross-fading stack: a new wallpaper (or the dark
        // variant on Dark Mode) fades in instead of cutting
        let pics = [0, 1].map(|_| gtk::Picture::builder().content_fit(gtk::ContentFit::Cover).hexpand(true).vexpand(true).can_target(false).build());
        let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(450).can_target(false).build();
        for (i, p) in pics.iter().enumerate() {
            stack.add_named(p, Some(&i.to_string()));
        }
        // after a cross-fade the hidden picture lets go of its texture
        stack.connect_transition_running_notify(|st| {
            if !st.is_transition_running() {
                let shown = st.visible_child();
                let mut c = st.first_child();
                while let Some(w) = c {
                    if Some(&w) != shown.as_ref() {
                        if let Some(p) = w.downcast_ref::<gtk::Picture>() {
                            p.set_paintable(None::<&gdk::Paintable>);
                        }
                    }
                    c = w.next_sibling();
                }
            }
        });
        let over = gtk::Overlay::new();
        over.set_child(Some(&stack));
        let screen = monitor.map(sonata_shell::layer::connector).unwrap_or_default();
        let desk = desktop.then(|| Desktop::new(&screen, main));
        if let Some(d) = &desk {
            over.add_overlay(&d.fixed); // the icons of ~/Desktop on top of the picture
        }
        let bin: SizeBin = glib::Object::new();
        over.set_parent(&bin);
        window.set_child(Some(&bin));
        if layer {
            sonata_shell::layer::background(window.upcast_ref(), "sonata2-wallpaper", desktop);
            if let Some(m) = monitor {
                sonata_shell::layer::set_monitor(window.upcast_ref(), m);
            }
        }
        let w = Rc::new_cyclic(|me| WallpaperWindow {
            window,
            pics,
            stack,
            uri: RefCell::default(),
            video: RefCell::default(),
            cover: RefCell::default(),
            frame_id: RefCell::default(),
            monitor: monitor.cloned(),
            loaded_for: Cell::new((0, 0)),
            desktop: desk,
            share: layer && main,
            prefs_mon: RefCell::default(),
            dark_id: RefCell::default(),
            me: me.clone(),
        });
        let me = w.me.clone();
        bin.imp().on_size.replace(Some(Box::new(move |width, height| {
            let Some(w) = me.upgrade() else { return };
            if let Some(d) = &w.desktop {
                d.resized(width, height);
            }
            let (px, was) = (w.pixels(), w.loaded_for.get());
            if px.0 > was.0 || px.1 > was.1 {
                // a bigger display: sharper copy
                let me = w.me.clone();
                glib::idle_add_local_once(move || {
                    if let Some(w) = me.upgrade() {
                        w.update(true);
                    }
                });
            }
        })));
        let me = w.me.clone();
        w.prefs_mon.replace(sonata_config::watch("system", move || {
            if let Some(w) = me.upgrade() {
                w.update(false);
            }
        }));
        let me = w.me.clone();
        w.dark_id.replace(Some(adw::StyleManager::default().connect_dark_notify(move |_| {
            if let Some(w) = me.upgrade() {
                w.update(false);
            }
        })));
        w.update(false);
        w
    }

    /// Destroyed (display unplugged, main display changed): let go of the
    /// app-wide StyleManager and the prefs monitor, or they keep this
    /// window and its decoded pictures (~33 MB at 4K) alive.
    pub fn destroy(&self) {
        if let Some(id) = self.dark_id.take() {
            adw::StyleManager::default().disconnect(id);
        }
        for m in self.prefs_mon.take() {
            m.cancel();
        }
        self.uri.replace(Some(String::from("\0gone"))); // a decode still running is dropped when it lands
        self.drop_video(true);
        for p in &self.pics {
            p.set_paintable(None::<&gdk::Paintable>);
        }
        if let Some(d) = &self.desktop {
            d.release();
        }
        self.window.destroy();
    }

    /// Let go of the video; `now` false: once the next picture has faded in
    /// over it (freed at once, it would fade out from an empty picture).
    fn drop_video(&self, now: bool) {
        let Some(v) = self.video.take() else { return };
        if let Some(c) = self.cover.take() {
            c.release();
        }
        if let Some(id) = self.frame_id.take() {
            v.cover.disconnect(id);
        }
        if now {
            video::release(&v);
        } else {
            glib::timeout_add_local_once(std::time::Duration::from_millis(self.stack.transition_duration() as u64 + 150), move || video::release(&v));
        }
    }

    /// This display's picture of the video: paused here while a window is
    /// on this display, playing on the others (Settings > Wallpaper).
    fn display_cover(&self) -> Option<gdk::Paintable> {
        let v = self.video.borrow().clone()?;
        if self.cover.borrow().is_none() {
            let screen = self.monitor.as_ref().map(sonata_shell::layer::connector).unwrap_or_default();
            self.cover.replace(Some(DisplayCover::new(&v, &screen)));
        }
        self.cover.borrow().clone().map(|c| c.upcast())
    }

    /// This display's size in device pixels.
    fn pixels(&self) -> (i32, i32) {
        let mon = self.monitor.clone().or_else(|| sonata_shell::layer::monitors().into_iter().next());
        match mon {
            Some(m) => {
                let (g, s) = (m.geometry(), m.scale_factor() as f64); // (the fractional scale needs GTK 4.14)
                ((g.width() as f64 * s) as i32, (g.height() as f64 * s) as i32)
            }
            None => (3840, 2160),
        }
    }

    fn show(&self, uri: &str, paint: Option<gdk::Paintable>) {
        if self.uri.borrow().as_deref() != Some(uri) {
            return; // another picture was chosen meanwhile
        }
        let nxt = if self.stack.visible_child().as_ref() == Some(self.pics[0].upcast_ref()) { &self.pics[1] } else { &self.pics[0] };
        nxt.set_paintable(paint.as_ref());
        self.stack.set_visible_child(nxt);
    }

    pub fn update(&self, force: bool) {
        let dark = adw::StyleManager::default().is_dark();
        let uri = sonata_shell::wallpaper::wallpaper_uri(dark);
        if self.uri.borrow().as_deref() == Some(uri.as_str()) && !force {
            return;
        }
        self.uri.replace(Some(uri.clone()));
        let f = (!uri.is_empty()).then(|| gio::File::for_uri(&uri));
        let path = f.as_ref().filter(|f| f.query_exists(gio::Cancellable::NONE)).and_then(|f| f.path());
        let (w, h) = self.pixels();
        self.loaded_for.set((w, h));
        let is_video = path.as_ref().is_some_and(|p| sonata_shell::wallpaper::is_video(&p.display().to_string()));
        let same_video = is_video && self.video.borrow().as_ref().is_some_and(|v| Some(std::path::Path::new(&v.path)) == path.as_deref());
        if !same_video {
            self.drop_video(false); // (a resize keeps the video playing)
        }
        let first = self.pics.iter().all(|p| p.paintable().is_none());
        let Some(path) = path else {
            self.show(&uri, None);
            return;
        };
        if is_video {
            if same_video {
                return; // (already on it)
            }
            let poster = sonata_shell::wallpaper::poster_path(&path);
            if first && poster.is_file() {
                let still = decode(&poster, w, h).map(|d| d.texture().upcast());
                self.poster_then_video(&uri, &path, still, Some(poster)); // at login: no gradient flash
                return;
            }
            // ffmpeg, the first time: off the main loop
            let (me, p) = (self.me.clone(), path.clone());
            let (tx, rx) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                let still = sonata_shell::wallpaper::poster(&p);
                let d = still.as_ref().and_then(|s| decode(s, w, h));
                let _ = tx.send((still, d));
            });
            glib::timeout_add_local(std::time::Duration::from_millis(50), move || match rx.try_recv() {
                Ok((still, d)) => {
                    if let Some(w) = me.upgrade() {
                        w.poster_then_video(&uri, &path, d.map(|d| d.texture().upcast()), still);
                    }
                    glib::ControlFlow::Break
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(_) => glib::ControlFlow::Break,
            });
            return;
        }
        if first {
            // at login: decoded before the window shows (a thread let the
            // gradient behind it flash first -- Vini: "the default wallpaper,
            // then mine")
            self.show(&uri, decode(&path, w, h).map(|d| d.texture().upcast()));
        } else {
            // a change later: decoding a 4K photo takes a moment, off the main loop
            let (me, u, p) = (self.me.clone(), uri.clone(), path.clone());
            let (tx, rx) = std::sync::mpsc::channel();
            std::thread::spawn(move || {
                let _ = tx.send(decode(&p, w, h));
            });
            glib::timeout_add_local(std::time::Duration::from_millis(30), move || match rx.try_recv() {
                Ok(d) => {
                    if let Some(w) = me.upgrade() {
                        w.show(&u, d.map(|d| d.texture().upcast()));
                    }
                    glib::ControlFlow::Break
                }
                Err(std::sync::mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
                Err(_) => glib::ControlFlow::Break,
            });
        }
        if self.share {
            if let Some(f) = &f {
                share_with_login_screen(f, None);
            }
        }
    }

    /// The poster now, the video over it once it has a frame (no black
    /// flash while GStreamer starts).
    fn poster_then_video(&self, uri: &str, path: &std::path::Path, still: Option<gdk::Paintable>, still_file: Option<std::path::PathBuf>) {
        if self.uri.borrow().as_deref() != Some(uri) {
            return;
        }
        if still.is_some() && self.video.borrow().is_none() {
            self.show(uri, still);
        }
        if self.video.borrow().is_none() {
            let v = video::acquire(&path.display().to_string());
            self.video.replace(Some(v.clone()));
            if v.cover.has_frame() {
                self.show(uri, self.display_cover()); // (another display already plays it)
            } else {
                // (the cover relays the player's frames on the main loop)
                let (me, u) = (self.me.clone(), uri.to_string());
                let id = v.cover.connect_invalidate_contents(move |c| {
                    let Some(w) = me.upgrade() else { return };
                    if !c.has_frame() {
                        return;
                    }
                    if let Some(id) = w.frame_id.take() {
                        c.disconnect(id);
                    }
                    w.show(&u, w.display_cover());
                });
                self.frame_id.replace(Some(id));
            }
        }
        if self.share {
            if let Some(s) = still_file {
                share_with_login_screen(&gio::File::for_path(s), Some(&path.display().to_string())); // the poster + the video
            }
        }
    }
}

/// `sonata2-rs wallpaper [--preview]`: the wallpaper and the desktop's
/// icons on every display (layer-shell background surfaces). --preview, and
/// always on trial: one plain window, next to the Python desktop.
pub fn run_wallpaper(args: &[String]) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview") || sonata_config::side_by_side();
    if !preview {
        sonata_shell::layer::ensure_preload();
    }
    let app = adw::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Walls>>>> = Rc::default();
    app.connect_activate(move |app| {
        if state.borrow().is_some() {
            return;
        }
        sonata_ui::setup();
        sonata_ui::css::register_module("shell/wallpaper.py");
        let walls = Walls::start(app.upcast_ref(), preview);
        state.replace(Some(walls));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

/// A wallpaper window on every display (monitors.each), made again when
/// the main display changes (the icons follow it).
struct Walls {
    app: gtk::Application,
    wins: RefCell<HashMap<String, Rc<WallpaperWindow>>>,
    main: RefCell<String>,
    _hold: gio::ApplicationHoldGuard,
    keep: RefCell<Vec<gio::FileMonitor>>,
}

impl Walls {
    fn start(app: &gtk::Application, preview: bool) -> Rc<Walls> {
        let walls = Rc::new(Walls { app: app.clone(), wins: RefCell::default(), main: RefCell::default(), _hold: app.hold(), keep: RefCell::default() });
        if preview || !sonata_shell::layer::available() {
            // a plain window: just one
            let main = sonata_shell::layer::main_monitor();
            let w = WallpaperWindow::new(app, main.as_ref(), true, true, false);
            w.window.present();
            let a = app.clone();
            w.window.connect_close_request(move |_| {
                a.quit();
                glib::Propagation::Proceed
            });
            walls.wins.borrow_mut().insert(String::new(), w);
            return walls;
        }
        walls.rebuild();
        if let Some(display) = gdk::Display::default() {
            let me = Rc::downgrade(&walls);
            display.monitors().connect_items_changed(move |_, _, _, _| {
                if let Some(w) = me.upgrade() {
                    w.rebuild();
                }
            });
        }
        let me = Rc::downgrade(&walls);
        walls.keep.replace(sonata_config::watch("displays", move || {
            if let Some(w) = me.upgrade() {
                w.rebuild();
            }
        }));
        walls
    }

    /// Windows follow the displays; a new main display: all made again.
    fn rebuild(&self) {
        let mons = sonata_shell::layer::monitors();
        let main = sonata_shell::layer::main_monitor().map(|m| sonata_shell::layer::connector(&m)).unwrap_or_default();
        let main_changed = *self.main.borrow() != main;
        self.main.replace(main.clone());
        let now: Vec<String> = mons.iter().map(sonata_shell::layer::connector).collect();
        let gone: Vec<String> = self.wins.borrow().keys().filter(|k| main_changed || !now.contains(k)).cloned().collect();
        for k in gone {
            if let Some(w) = self.wins.borrow_mut().remove(&k) {
                w.destroy();
            }
        }
        for (m, c) in mons.iter().zip(now) {
            if self.wins.borrow().contains_key(&c) {
                continue;
            }
            let w = WallpaperWindow::new(&self.app, Some(m), true, c == main, true); // a desktop on every display
            w.window.present();
            self.wins.borrow_mut().insert(c, w);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cover_size() {
        assert_eq!(cover_size(3840, 2160, 1920, 1080), (1920, 1080));
        assert_eq!(cover_size(1000, 1000, 1920, 1080), (1000, 1000)); // never enlarged
        assert_eq!(cover_size(4000, 1000, 1920, 1080), (4000, 1000)); // (as wide as needed to cover)
        assert_eq!(cover_size(0, 0, 1920, 1080), (0, 0));
    }

    #[test]
    fn test_texture_at_screen_size() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("big.png");
        let pb = gtk::gdk_pixbuf::Pixbuf::new(gtk::gdk_pixbuf::Colorspace::Rgb, false, 8, 400, 200).unwrap();
        pb.savev(&p, "png", &[]).unwrap();
        let got = decode(&p, 100, 100).unwrap();
        assert_eq!((got.w, got.h), (200, 100));
        assert!(decode(&d.path().join("none.png"), 100, 100).is_none());
    }
}
