//! Preview (macOS Preview, images), ported from `sonata2/preview/window.py`:
//! a window per picture, or one window for the pictures opened together,
//! with their thumbnails in a sidebar.
//!
//! The picture fits the window; ⌘+ / ⌘- / ⌘0 zoom in steps that glide, ⌘9
//! shows it at actual size. Ctrl+wheel and a pinch zoom around the pointer,
//! two-finger scroll or dragging pans a zoomed picture, a double-click goes
//! to actual size and back to fit. ← / → go through the other pictures, ⌥⌘2
//! shows the thumbnails, ⌘I the Info panel, ⌘⇧F plays a slideshow. ⌘R / ⌘L
//! rotate, ⌘K crops, Adjust Color / Resize… / Export As… are on the toolbar
//! and the context menu; ⌘Z undoes, ⌘S saves, ⌘⇧S saves as. Space or ⌘F fill
//! the screen, ⌘W closes (asking about unsaved edits).

use crate::canvas::Canvas;
use crate::edit::{self, Edits, Op, ADJUSTMENTS, EXPORT_FORMATS};
use crate::imageload::{self, RAW_EXTS, RAW_TYPES};
use crate::info::{self, InfoPanel};
use crate::markup::{MarkupBar, MarkupLayer};
use crate::marks;
use crate::sidebar::Thumbnails;
use gtk::gdk::{self, Key, ModifierType};
use gtk::{cairo, gio, glib, prelude::*};
use serde_json::json;
use sonata_i18n::{fmt, tr};
use sonata_ui::{controls, dialog, menu};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::time::Duration;

const EXTS: &[&str] = &[".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".ico", ".avif", ".heic", ".heif", ".jxl", ".tga", ".qoi"];
const ZOOMS: [f64; 14] = [0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0];
/// slideshow: seconds per picture
const SLIDE_S: u32 = 3;
/// never smaller (the picture a sliver otherwise)
const MIN_SIZE: (i32, i32) = (420, 300);
const TITLEBAR_H: i32 = 52;
const CLIPBOARD_SHOT: &str = "sonata2-clipboard-shot.png";

fn is_picture(name: &str) -> bool {
    let low = name.to_lowercase();
    EXTS.iter().chain(RAW_EXTS).any(|e| low.ends_with(e))
}

fn base(path: &str) -> String {
    std::path::Path::new(path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

fn stem(path: &str) -> String {
    std::path::Path::new(path).file_stem().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

fn dir_of(path: &str) -> String {
    std::path::Path::new(path).parent().map(|p| p.display().to_string()).unwrap_or_default()
}

/// The pictures in the same folder, in Files' (name) order.
pub fn siblings(path: &str) -> Vec<String> {
    let folder = dir_of(path);
    let Ok(rd) = std::fs::read_dir(&folder) else { return vec![path.to_string()] };
    let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| is_picture(n) && !n.starts_with('.')).collect();
    names.sort_by_cached_key(|n| glib::FilenameCollationKey::from(n.as_str()));
    names.into_iter().map(|n| format!("{folder}/{n}")).collect()
}

/// The texture of a picture, decoded off the main loop.
async fn load_texture(path: String) -> Option<gdk::Texture> {
    gio::spawn_blocking(move || imageload::texture(&path)).await.ok().flatten()
}

/// A picture on the clipboard: through wl-copy (it stays after the app
/// quits), GTK's own clipboard without it.
fn to_clipboard(widget: &gtk::Widget, mime: &str, data: Vec<u8>, done: Option<Box<dyn Fn(bool)>>) {
    let has_wlcopy = std::env::var_os("PATH").is_some_and(|p| std::env::split_paths(&p).any(|d| d.join("wl-copy").is_file()));
    if has_wlcopy {
        let mime = mime.to_string();
        glib::spawn_future_local(async move {
            let ok = gio::spawn_blocking(move || {
                use std::io::Write;
                let child = std::process::Command::new("wl-copy").args(["--type", &mime]).stdin(std::process::Stdio::piped()).spawn();
                child
                    .and_then(|mut c| {
                        c.stdin.take().map(|mut s| s.write_all(&data)).transpose()?;
                        c.wait()
                    })
                    .map(|s| s.success())
                    .unwrap_or(false)
            })
            .await
            .unwrap_or(false);
            if let Some(d) = done {
                d(ok);
            }
        });
        return;
    }
    let provider = gdk::ContentProvider::for_bytes(mime, &glib::Bytes::from_owned(data));
    let ok = widget.clipboard().set_content(Some(&provider)).is_ok();
    if let Some(d) = done {
        d(ok);
    }
}

#[derive(Clone, Copy)]
struct Slides {
    paused: bool,
    sidebar: bool,
    info: bool,
}

pub struct PreviewWindow {
    pub window: gtk::ApplicationWindow,
    pub path: RefCell<Option<String>>,
    texture: RefCell<Option<gdk::Texture>>,
    pub edits: RefCell<Option<Edits>>,
    /// crop mode: the selection (x0, y0, x1, y1) in canvas pixels
    crop: Cell<Option<(f64, f64, f64, f64)>>,
    /// the pictures opened together (their thumbnails show); None: the folder's
    group: RefCell<Option<Vec<String>>>,
    pics: RefCell<Vec<String>>,
    asking: Cell<bool>,
    slides: Cell<Option<Slides>>,
    slide_timer: RefCell<Option<glib::SourceId>>,
    info_serial: Cell<u64>,
    open_serial: Cell<u64>,
    loading: RefCell<Option<String>>,
    saving: Cell<bool>,
    toolbar: gtk::WindowHandle,
    sidebar_rev: gtk::Revealer,
    info_rev: gtk::Revealer,
    pub canvas: Canvas,
    overlay: gtk::Overlay,
    crop_area: gtk::DrawingArea,
    markup_rev: gtk::Revealer,
    markup: RefCell<Option<Rc<MarkupLayer>>>,
    markup_bar: RefCell<Option<Rc<MarkupBar>>>,
    markup_on_open: Cell<bool>,
    from_shot: RefCell<Option<String>>,
    thumbs: Rc<Thumbnails>,
    info: InfoPanel,
    adjust_btn: gtk::Button,
    markup_btn: gtk::Button,
    me: Weak<PreviewWindow>,
}

impl PreviewWindow {
    pub fn new(app: &impl IsA<gtk::Application>, path: &str, paths: Option<Vec<String>>, markup: bool) -> Rc<Self> {
        sonata_ui::css::register_module("preview/window.py"); // the Python Preview's own CSS (css.json)
        if glib::application_name().is_none() {
            glib::set_application_name("Preview"); // Recent documents need it
        }
        let group = paths.filter(|p| p.len() > 1);
        let pw = Rc::new_cyclic(|me: &Weak<PreviewWindow>| {
            let window = gtk::ApplicationWindow::builder().application(app).build();
            window.add_css_class("sonata-preview");
            sonata_ui::window::standard(&window);
            window.set_size_request(MIN_SIZE.0, MIN_SIZE.1);
            let call = |f: fn(&PreviewWindow)| -> Box<dyn Fn()> {
                let w = me.clone();
                Box::new(move || {
                    if let Some(p) = w.upgrade() {
                        f(&p);
                    }
                })
            };
            let (toolbar, bar, tools) = sonata_ui::window::glass_toolbar(
                &window,
                vec![
                    ("info-symbolic", tr!("Show Info"), call(|p| p.toggle_info())),
                    ("zoom-out-symbolic", tr!("Zoom Out"), call(|p| p.step_zoom(-1))),
                    ("zoom-in-symbolic", tr!("Zoom In"), call(|p| p.step_zoom(1))),
                    ("object-rotate-left-symbolic", tr!("Rotate Left"), call(|p| p.rotate(-90))),
                    ("sonata-markup-symbolic", tr!("Markup"), call(|p| p.markup_button())),
                    ("sonata-crop-symbolic", tr!("Crop"), call(|p| p.crop_button())),
                    ("sonata-adjust-symbolic", tr!("Adjust Color"), call(|p| p.adjust_panel())),
                ],
            );
            // the thumbnails' button at the start
            let side_btn = gtk::Button::builder().icon_name("sidebar-show-symbolic").tooltip_text(tr!("Thumbnails")).can_focus(false).build();
            side_btn.add_css_class("tool");
            let f = call(|p| p.toggle_sidebar());
            side_btn.connect_clicked(move |_| f());
            let start = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
            start.append(&side_btn);
            bar.set_start_widget(Some(&start));
            let canvas = Canvas::new();
            // the crop selection is drawn over the picture
            let overlay = gtk::Overlay::builder().child(&canvas).vexpand(true).hexpand(true).build();
            let crop_area = gtk::DrawingArea::builder().visible(false).can_target(true).build();
            overlay.add_overlay(&crop_area);
            let markup_rev = gtk::Revealer::builder().transition_type(gtk::RevealerTransitionType::SlideDown).transition_duration(sonata_ui::tokens::ms(200.0)).reveal_child(false).build();
            let dur = sonata_ui::tokens::ms(250.0);
            let w = me.clone();
            let thumbs = Thumbnails::new(move |p| {
                if let Some(pw) = w.upgrade() {
                    pw.pick(p);
                }
            });
            let sidebar_rev = gtk::Revealer::builder().child(&thumbs.root).transition_type(gtk::RevealerTransitionType::SlideRight).transition_duration(dur).reveal_child(group.is_some()).build();
            let info = InfoPanel::new();
            let info_rev = gtk::Revealer::builder().child(&info.root).transition_type(gtk::RevealerTransitionType::SlideLeft).transition_duration(dur).reveal_child(false).build();
            let body = gtk::Box::builder().vexpand(true).build();
            body.append(&sidebar_rev);
            body.append(&overlay);
            body.append(&info_rev);
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            col.append(&toolbar);
            col.append(&markup_rev);
            col.append(&body);
            window.set_child(Some(&col));
            PreviewWindow {
                window,
                path: RefCell::new(None),
                texture: RefCell::new(None),
                edits: RefCell::new(None),
                crop: Cell::new(None),
                group: RefCell::new(group.clone()),
                pics: RefCell::new(group.clone().unwrap_or_else(|| vec![path.to_string()])),
                asking: Cell::new(false),
                slides: Cell::new(None),
                slide_timer: RefCell::new(None),
                info_serial: Cell::new(0),
                open_serial: Cell::new(0),
                loading: RefCell::new(None),
                saving: Cell::new(false),
                toolbar,
                sidebar_rev,
                info_rev,
                canvas,
                overlay,
                crop_area,
                markup_rev,
                markup: RefCell::new(None),
                markup_bar: RefCell::new(None),
                markup_on_open: Cell::new(markup),
                from_shot: RefCell::new(markup.then(|| path.to_string())),
                thumbs,
                info,
                adjust_btn: tools[6].clone(),
                markup_btn: tools[4].clone(),
                me: me.clone(),
            }
        });
        pw.wire();
        pw.open(path, false);
        if pw.group.borrow().is_none() && pw.path.borrow().is_some() {
            pw.load_siblings();
        }
        pw
    }

    fn wire(self: &Rc<Self>) {
        let w = Rc::downgrade(self);
        self.crop_area.set_draw_func(move |_, cr, _, _| {
            if let Some(p) = w.upgrade() {
                p.draw_crop(cr);
            }
        });
        self.crop_input();
        let keys = gtk::EventControllerKey::new();
        let w = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, keyval, _code, state| match w.upgrade() {
            Some(p) if p.key(keyval, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.window.add_controller(keys);
        let menu_click = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
        let w = Rc::downgrade(self);
        menu_click.connect_pressed(move |g, _n, x, y| {
            g.set_state(gtk::EventSequenceState::Claimed);
            if let Some(p) = w.upgrade() {
                p.context_menu(x, y);
            }
        });
        self.canvas.add_controller(menu_click);
        let w = Rc::downgrade(self);
        self.window.connect_close_request(move |_| match w.upgrade() {
            Some(p) if p.close_request() => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        let w = Rc::downgrade(self);
        self.window.connect_fullscreened_notify(move |win| {
            if let Some(p) = w.upgrade() {
                let full = win.is_fullscreen();
                if !full && p.slides.get().is_some() {
                    p.stop_slideshow(); // left full screen some other way
                }
                p.toolbar.set_visible(!full);
            }
        });
        let w = Rc::downgrade(self);
        self.window.connect_unmap(move |_| {
            if let Some(p) = w.upgrade() {
                p.stop_slideshow();
            }
        });
    }

    fn title_for(&self, name: &str, edited: bool) {
        let t = if edited { fmt(&tr!("{name} — Edited"), &[("name", name)]) } else { name.to_string() };
        self.window.set_title(Some(&t));
    }

    fn cur(&self) -> Option<String> {
        self.path.borrow().clone()
    }

    fn edited(&self) -> bool {
        self.edits.borrow().as_ref().is_some_and(|e| e.edited())
    }

    /// The folder's pictures, listed off the main loop.
    fn load_siblings(&self) {
        let Some(path) = self.cur() else { return };
        let w = self.me.clone();
        glib::spawn_future_local(async move {
            let p2 = path.clone();
            let pics = gio::spawn_blocking(move || siblings(&p2)).await.unwrap_or_default();
            let Some(pw) = w.upgrade() else { return };
            let cur = pw.cur();
            if pics.is_empty() || pw.group.borrow().is_some() || cur.as_deref().map(dir_of) != Some(dir_of(&path)) {
                return;
            }
            let cur = cur.unwrap_or_default();
            let mut pics = pics;
            if !pics.contains(&cur) {
                pics.push(cur.clone());
            }
            *pw.pics.borrow_mut() = pics.clone();
            pw.thumbs.set_paths(&pics, Some(&cur));
        });
    }

    // -- file ------------------------------------------------------------------------------
    pub fn open(&self, path: &str, fade: bool) {
        if self.saving.get() {
            return;
        }
        if self.markup.borrow().is_some() {
            // marks on this picture: they become its edit (asked below)
            self.end_markup(true, None);
            if self.saving.get() {
                return; // a screenshot saving its marks: the other picture after
            }
        }
        if self.edited() {
            // ← / → with unsaved edits: ask first
            let (w, p) = (self.me.clone(), path.to_string());
            self.ask_save(Box::new(move || {
                if let Some(pw) = w.upgrade() {
                    *pw.edits.borrow_mut() = None;
                    pw.open(&p, fade);
                }
            }));
            return;
        }
        self.open_serial.set(self.open_serial.get() + 1);
        if !self.window.is_realized() {
            // the first picture sizes the window
            *self.loading.borrow_mut() = None;
            self.opened(path, imageload::texture(path), fade);
            return;
        }
        // ← / →: decoded off the main loop (a big photo froze the window)
        let serial = self.open_serial.get();
        *self.loading.borrow_mut() = Some(path.to_string());
        let (w, p) = (self.me.clone(), path.to_string());
        glib::spawn_future_local(async move {
            let tex = load_texture(p.clone()).await;
            if let Some(pw) = w.upgrade() {
                if serial == pw.open_serial.get() {
                    *pw.loading.borrow_mut() = None;
                    pw.opened(&p, tex, fade);
                }
            }
        });
    }

    fn opened(&self, path: &str, tex: Option<gdk::Texture>, fade: bool) {
        *self.edits.borrow_mut() = None;
        self.crop.set(None);
        self.crop_area.set_visible(false);
        let known = self.path.borrow().is_none() || self.pics.borrow().iter().any(|p| p == path);
        *self.path.borrow_mut() = Some(path.to_string());
        *self.texture.borrow_mut() = tex.clone();
        let name = base(path);
        self.window.set_title(Some(&name));
        if !known {
            // Save As: a new file, maybe elsewhere
            let grouped = self.group.borrow().is_some();
            if grouped {
                self.pics.borrow_mut().push(path.to_string());
            } else {
                *self.pics.borrow_mut() = vec![path.to_string()];
                self.load_siblings();
            }
        }
        let pics = self.pics.borrow().clone();
        self.thumbs.set_paths(&pics, Some(path));
        let Some(tex) = tex else {
            self.canvas.set_texture(None, false, false);
            self.window.set_default_size(520, 360);
            // once the window is on screen: an alert on a window not shown yet never appeared
            let (w, p) = (self.me.clone(), path.to_string());
            glib::timeout_add_local_once(Duration::from_millis(150), move || {
                let Some(pw) = w.upgrade() else { return };
                let heading = fmt(&tr!("“{name}” couldn't be opened."), &[("name", &base(&p))]);
                let body = tr!("It isn't a picture Preview can read.");
                let others = pw.pics.borrow().len() > 1;
                if pw.cur().as_deref() != Some(&p) || others {
                    // moved on, or others to see: no need to close
                    if pw.cur().as_deref() == Some(&p) {
                        dialog::alert(&heading, &body, &[("ok", tr!("OK"), "default")], |_| {}, Some(pw.window.upcast_ref()));
                    }
                    return;
                }
                let win = pw.window.clone();
                dialog::alert(&heading, &body, &[("ok", tr!("OK"), "default")], move |_| win.close(), Some(pw.window.upcast_ref()));
            });
            return;
        };
        self.canvas.set_texture(Some(tex.clone()), fade, false);
        if self.markup_on_open.replace(false) {
            let w = self.me.clone();
            glib::idle_add_local_once(move || {
                if let Some(pw) = w.upgrade() {
                    if pw.markup.borrow().is_none() {
                        pw.markup_button();
                    }
                }
            });
        }
        gtk::RecentManager::default().add_item(&gio::File::for_path(path).uri());
        if !self.window.is_realized() {
            // the window takes the picture's shape
            let (w, h) = (tex.width() as f64, tex.height() as f64);
            let (max_w, max_h) = gdk::Display::default()
                .and_then(|d| d.monitors().item(0))
                .and_downcast::<gdk::Monitor>()
                .map(|m| (m.geometry().width() as f64 * 0.7, m.geometry().height() as f64 * 0.7))
                .unwrap_or((1100.0, 760.0));
            let side = if self.sidebar_rev.reveals_child() { crate::sidebar::SIDEBAR_W as f64 } else { 0.0 };
            let s = ((max_w - side) / w.max(1.0)).min((max_h - TITLEBAR_H as f64) / h.max(1.0)).min(1.0);
            self.window.set_default_size(((w * s) as i32 + side as i32).max(420), ((h * s) as i32 + TITLEBAR_H).max(300));
        }
        if self.info_rev.reveals_child() {
            self.refresh_info();
        }
    }

    pub fn go(&self, step: i32, fade: bool) {
        let cur = self.loading.borrow().clone().or_else(|| self.cur()); // pressed again while one loads: from that one
        let Some(cur) = cur else { return };
        let pics = self.pics.borrow().clone();
        if let (Some(i), true) = (pics.iter().position(|p| *p == cur), pics.len() > 1) {
            let n = pics.len() as i32;
            self.open(&pics[((i as i32 + step).rem_euclid(n)) as usize], fade);
        }
    }

    /// A thumbnail was clicked.
    fn pick(&self, path: &str) {
        let shown = self.loading.borrow().clone().or_else(|| self.cur());
        if shown.as_deref() != Some(path) {
            self.open(path, false);
            let now = self.loading.borrow().clone().or_else(|| self.cur());
            if now.as_deref() != Some(path) {
                // asked about the edits first: stay
                if let Some(c) = self.cur() {
                    self.thumbs.select(&c);
                }
            }
        }
    }

    // -- sidebar / info ----------------------------------------------------------------------
    pub fn toggle_sidebar(&self) {
        self.sidebar_rev.set_reveal_child(!self.sidebar_rev.reveals_child());
        if self.sidebar_rev.reveals_child() {
            let w = self.me.clone();
            glib::idle_add_local_once(move || {
                if let Some(p) = w.upgrade() {
                    if let Some(c) = p.cur() {
                        p.thumbs.select(&c);
                    }
                }
            });
        }
    }

    pub fn toggle_info(&self) {
        let show = !self.info_rev.reveals_child();
        self.info_rev.set_reveal_child(show);
        if show {
            self.refresh_info();
        }
    }

    fn refresh_info(&self) {
        let Some(path) = self.cur() else { return };
        self.info_serial.set(self.info_serial.get() + 1);
        let serial = self.info_serial.get();
        let size = self.texture.borrow().as_ref().map(|t| (t.width(), t.height()));
        if self.info.path.borrow().as_deref() != Some(path.as_str()) {
            self.info.show(&path, &info::Info { name: base(&path), ..Default::default() });
        }
        let w = self.me.clone();
        glib::spawn_future_local(async move {
            let p = path.clone();
            let Ok(data) = gio::spawn_blocking(move || info::read(&p, size)).await else { return };
            if let Some(pw) = w.upgrade() {
                if serial == pw.info_serial.get() {
                    pw.info.show(&path, &data);
                }
            }
        });
    }

    // -- slideshow -------------------------------------------------------------------------
    /// ⌘⇧F: full screen, a cross-fade to the next picture every 3 s.
    pub fn slideshow(&self) {
        if self.slides.get().is_some() {
            self.stop_slideshow();
            return;
        }
        if self.edited() {
            let w = self.me.clone();
            self.ask_save(Box::new(move || {
                if let Some(p) = w.upgrade() {
                    p.slideshow();
                }
            }));
            return;
        }
        self.end_crop();
        self.slides.set(Some(Slides { paused: false, sidebar: self.sidebar_rev.reveals_child(), info: self.info_rev.reveals_child() }));
        for rev in [&self.sidebar_rev, &self.info_rev] {
            rev.set_transition_duration(0);
            rev.set_reveal_child(false);
        }
        self.window.add_css_class("pv-slideshow");
        self.toolbar.set_visible(false);
        self.canvas.set_zoom(None, None, false);
        self.window.fullscreen();
        self.arm_slides();
    }

    fn arm_slides(&self) {
        let Some(s) = self.slides.get() else { return };
        if let Some(id) = self.slide_timer.borrow_mut().take() {
            id.remove();
        }
        if s.paused {
            return;
        }
        let w = self.me.clone();
        *self.slide_timer.borrow_mut() = Some(glib::timeout_add_seconds_local(SLIDE_S, move || {
            let Some(p) = w.upgrade() else { return glib::ControlFlow::Break };
            if p.slides.get().is_none() {
                return glib::ControlFlow::Break;
            }
            let minimized = p.window.surface().and_downcast::<gdk::Toplevel>().is_some_and(|t| t.state().contains(gdk::ToplevelState::MINIMIZED));
            if p.window.is_mapped() && !minimized {
                p.go(1, true);
            }
            glib::ControlFlow::Continue
        }));
    }

    pub fn stop_slideshow(&self) {
        let Some(s) = self.slides.take() else { return };
        if let Some(id) = self.slide_timer.borrow_mut().take() {
            id.remove();
        }
        self.window.remove_css_class("pv-slideshow");
        self.toolbar.set_visible(true);
        for (rev, shown) in [(&self.sidebar_rev, s.sidebar), (&self.info_rev, s.info)] {
            rev.set_reveal_child(shown);
            rev.set_transition_duration(sonata_ui::tokens::ms(250.0));
        }
        if self.window.is_fullscreen() {
            self.window.unfullscreen();
        }
    }

    fn pause_slides(&self) {
        if let Some(mut s) = self.slides.get() {
            s.paused = !s.paused;
            self.slides.set(Some(s));
            self.arm_slides();
        }
    }

    // -- editing ---------------------------------------------------------------------------
    /// The picture's edits (loaded the first time something is edited).
    fn editor(&self) -> bool {
        if self.saving.get() {
            return false; // edits wait for the save being written
        }
        if self.edits.borrow().is_none() {
            if let Some(p) = self.cur() {
                *self.edits.borrow_mut() = Edits::load(&p);
                if self.edits.borrow().is_none() {
                    dialog::alert(&tr!("This picture can't be edited."), &tr!("Preview can only view it."), &[("ok", tr!("OK"), "default")], |_| {}, Some(self.window.upcast_ref()));
                }
            }
        }
        self.edits.borrow().is_some()
    }

    fn show_edits(&self) {
        let Some((tex, edited)) = self.edits.borrow().as_ref().map(|e| (e.texture(), e.edited())) else { return };
        *self.texture.borrow_mut() = Some(tex.clone());
        self.canvas.set_texture(Some(tex), false, true);
        self.title_for(&base(&self.cur().unwrap_or_default()), edited);
    }

    pub fn edit(&self, op: Op) {
        if self.saving.get() || !self.editor() {
            return; // (the save renders these edits right now)
        }
        if let Some(e) = self.edits.borrow_mut().as_mut() {
            e.push(op);
        }
        self.show_edits();
    }

    pub fn rotate(&self, degrees: i32) {
        self.edit(Op::Rotate(degrees.rem_euclid(360)));
    }

    pub fn undo(&self) {
        if self.saving.get() {
            return;
        }
        let undone = self.edits.borrow_mut().as_mut().is_some_and(|e| e.undo());
        if undone {
            self.show_edits();
        }
    }

    pub fn revert(&self) {
        if self.saving.get() {
            return;
        }
        if let Some(e) = self.edits.borrow_mut().as_mut() {
            e.revert();
        }
        self.show_edits();
    }

    // markup: draw on the picture; Done bakes the marks in as one edit
    pub fn markup_button(&self) {
        if self.markup.borrow().is_some() {
            self.end_markup(true, None);
            return;
        }
        if !self.editor() {
            return;
        }
        if self.crop_area.is_visible() {
            self.end_crop();
        }
        self.set_zoom(None, None, false); // the whole picture
        let (c1, w) = (self.canvas.clone(), self.me.clone());
        let layer = MarkupLayer::new(move || c1.image_rect(), move || w.upgrade().and_then(|p| p.texture.borrow().clone()));
        self.overlay.add_overlay(&layer.area);
        let (d, c, cp) = (self.me.clone(), self.me.clone(), self.me.clone());
        let bar = MarkupBar::new(
            layer.clone(),
            Box::new(move || {
                if let Some(p) = d.upgrade() {
                    p.end_markup(true, None);
                }
            }),
            Box::new(move || {
                if let Some(p) = c.upgrade() {
                    p.end_markup(false, None);
                }
            }),
            Some(Box::new(move |done| {
                if let Some(p) = cp.upgrade() {
                    p.copy_marked(Some(done));
                }
            })),
        );
        bar.root.add_css_class("docked"); // opaque, like the toolbar above it
        self.markup_rev.set_child(Some(&bar.root));
        self.markup_rev.set_reveal_child(true);
        self.markup_btn.add_css_class("on");
        layer.area.grab_focus();
        *self.markup.borrow_mut() = Some(layer);
        *self.markup_bar.borrow_mut() = Some(bar);
    }

    pub fn end_markup(&self, keep: bool, then: Option<Box<dyn Fn()>>) {
        let layer = self.markup.borrow_mut().take();
        let Some(layer) = layer else {
            if let Some(t) = then {
                t();
            }
            return;
        };
        self.markup_bar.borrow_mut().take();
        self.overlay.remove_overlay(&layer.area);
        self.markup_rev.set_reveal_child(false);
        self.markup_btn.remove_css_class("on");
        let items = layer.items.borrow().clone();
        if keep && !items.is_empty() {
            self.edit(Op::Markup(items));
            let shot = self.from_shot.borrow().clone();
            if shot.is_some() && shot == self.cur() {
                // a screenshot: Done saves it, like macOS
                let w = self.me.clone();
                self.save(Some(Box::new(move || {
                    if let Some(p) = w.upgrade() {
                        p.shot_to_clipboard();
                    }
                    if let Some(t) = &then {
                        t();
                    }
                })));
                return;
            }
        }
        if let Some(t) = then {
            t();
        }
    }

    /// The full-size picture with its edits and these marks, as PNG bytes.
    fn rendered_png(ed: &Edits, items: &[marks::Mark]) -> Option<Vec<u8>> {
        let mut im = ed.render(&ed.full);
        if !items.is_empty() {
            let (w, h) = im.dimensions();
            let mut data = im.into_raw();
            marks::apply(&mut data, w, h, items);
            im = image::RgbaImage::from_raw(w, h, data)?;
        }
        edit::encode(&im, true, "png", 92, None, None).ok()
    }

    /// Markup's Copy: the picture as it is now -- its edits and the marks
    /// not Done yet -- on the clipboard (PNG); Markup stays open.
    fn copy_marked(&self, done: Option<Box<dyn Fn(bool)>>) {
        if !self.editor() {
            if let Some(d) = done {
                d(false);
            }
            return;
        }
        let items = self.markup.borrow().as_ref().map(|l| l.items.borrow().clone()).unwrap_or_default();
        let png = self.edits.borrow().as_ref().and_then(|ed| Self::rendered_png(ed, &items));
        match png {
            Some(png) => to_clipboard(self.window.upcast_ref(), "image/png", png, done),
            None => {
                if let Some(d) = done {
                    d(false);
                }
            }
        }
    }

    /// Right-click Copy: the picture as shown. Unedited: the file itself (no
    /// re-encoding); with edits or marks: rendered at full size.
    pub fn copy_picture(&self) {
        let marked = self.markup.borrow().as_ref().is_some_and(|l| !l.items.borrow().is_empty());
        if self.edited() || marked {
            self.copy_marked(None);
            return;
        }
        let Some(path) = self.cur() else { return };
        let (ct, _) = gio::content_type_guess(Some(&path), &[]);
        let as_is = ["image/png", "image/jpeg", "image/gif", "image/webp"];
        if as_is.contains(&ct.as_str()) {
            if let Ok(data) = std::fs::read(&path) {
                to_clipboard(self.window.upcast_ref(), &ct, data, None);
                return;
            }
        }
        if let Some(t) = self.texture.borrow().as_ref() {
            to_clipboard(self.window.upcast_ref(), "image/png", t.save_to_png_bytes().to_vec(), None);
        }
    }

    /// A screenshot that went to the clipboard (only, or also): the marked one goes there too.
    fn shot_to_clipboard(&self) {
        let Some(path) = self.cur() else { return };
        let cfg = sonata_config::load("capture", &json!({"shots_copy": false}));
        let on_clip = base(&path) == CLIPBOARD_SHOT || cfg.get("shots_copy").and_then(|v| v.as_bool()).unwrap_or(false);
        if on_clip {
            if let Ok(data) = std::fs::read(&path) {
                to_clipboard(self.window.upcast_ref(), "image/png", data, None);
            }
        }
    }

    // crop: drag a selection over the picture, then Crop (or Return)
    pub fn crop_button(&self) {
        if self.crop_area.is_visible() {
            if self.crop.get().is_some() && self.crop_fraction().is_some() {
                self.apply_crop();
            } else {
                self.end_crop();
            }
            return;
        }
        if !self.editor() {
            return;
        }
        self.set_zoom(None, None, false); // crop on the whole picture
        self.crop.set(None);
        self.crop_area.set_visible(true);
        self.crop_area.queue_draw();
    }

    fn crop_fraction(&self) -> Option<[f64; 4]> {
        let (x, y, w, h) = self.canvas.image_rect()?;
        let (x0, y0, x1, y1) = self.crop.get()?;
        let (fx0, fx1) = { let (a, b) = ((x0 - x) / w, (x1 - x) / w); (a.min(b), a.max(b)) };
        let (fy0, fy1) = { let (a, b) = ((y0 - y) / h, (y1 - y) / h); (a.min(b), a.max(b)) };
        let (fx0, fy0, fx1, fy1) = (fx0.max(0.0), fy0.max(0.0), fx1.min(1.0), fy1.min(1.0));
        if fx1 - fx0 < 0.01 || fy1 - fy0 < 0.01 {
            return None;
        }
        Some([fx0, fy0, fx1, fy1])
    }

    fn apply_crop(&self) {
        let frac = self.crop_fraction();
        self.end_crop();
        if let Some(f) = frac {
            self.edit(Op::Crop(f));
        }
    }

    fn end_crop(&self) {
        self.crop.set(None);
        self.crop_area.set_visible(false);
    }

    fn crop_input(self: &Rc<Self>) {
        let drag = gtk::GestureDrag::new();
        let state: Rc<Cell<(f64, f64, Option<(f64, f64, f64, f64)>)>> = Rc::new(Cell::new((0.0, 0.0, None)));
        let (w, st) = (self.me.clone(), state.clone());
        drag.connect_drag_begin(move |_, x, y| {
            let Some(p) = w.upgrade() else { return };
            let c = p.crop.get();
            let inside = c.is_some_and(|c| c.0.min(c.2) < x && x < c.0.max(c.2) && c.1.min(c.3) < y && y < c.1.max(c.3));
            st.set((x, y, if inside { c } else { None }));
            if !inside {
                p.crop.set(Some((x, y, x, y)));
            }
        });
        let (w, st) = (self.me.clone(), state);
        drag.connect_drag_update(move |_, dx, dy| {
            let Some(p) = w.upgrade() else { return };
            let (x, y, moving) = st.get();
            p.crop.set(Some(match moving {
                Some((x0, y0, x1, y1)) => (x0 + dx, y0 + dy, x1 + dx, y1 + dy),
                None => (x, y, x + dx, y + dy),
            }));
            p.crop_area.queue_draw();
        });
        self.crop_area.add_controller(drag);
        self.crop_area.set_cursor_from_name(Some("crosshair"));
    }

    fn draw_crop(&self, cr: &cairo::Context) {
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.45); // the picture outside the selection dims
        if let Some((x, y, w, h)) = self.canvas.image_rect() {
            cr.rectangle(x, y, w, h);
        }
        if let Some((x0, y0, x1, y1)) = self.crop.get() {
            cr.rectangle(x0.min(x1), y0.min(y1), (x1 - x0).abs(), (y1 - y0).abs());
            cr.set_fill_rule(cairo::FillRule::EvenOdd); // a hole where the selection is
        }
        let _ = cr.fill();
        if let Some((x0, y0, x1, y1)) = self.crop.get() {
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.95);
            cr.set_line_width(1.5);
            cr.rectangle(x0.min(x1) + 0.5, y0.min(y1) + 0.5, (x1 - x0).abs(), (y1 - y0).abs());
            let _ = cr.stroke();
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.35); // thirds, like Photos/Preview
            cr.set_line_width(1.0);
            for i in [1.0, 2.0] {
                let xx = x0.min(x1) + (x1 - x0).abs() * i / 3.0;
                let yy = y0.min(y1) + (y1 - y0).abs() * i / 3.0;
                cr.move_to(xx, y0.min(y1));
                cr.line_to(xx, y0.max(y1));
                cr.move_to(x0.min(x1), yy);
                cr.line_to(x0.max(x1), yy);
            }
            let _ = cr.stroke();
        }
    }

    // adjust color: sliders in a panel under the toolbar button
    pub fn adjust_panel(&self) {
        if !self.editor() {
            return;
        }
        let col = gtk::Box::new(gtk::Orientation::Vertical, 4);
        col.add_css_class("pv-adjust");
        col.set_size_request(260, -1);
        let pending = Rc::new(Cell::new(false));
        let mut sliders = Vec::new();
        for (i, (_key, title)) in ADJUSTMENTS.iter().enumerate() {
            let l = gtk::Label::builder().label(sonata_i18n::gettext(title)).xalign(0.0).build();
            l.add_css_class("pv-adj-name");
            col.append(&l);
            let value = self.edits.borrow().as_ref().map(|e| e.adjust[i]).unwrap_or(0.0);
            let (w, pend) = (self.me.clone(), pending.clone());
            let sl = controls::slider(value * 100.0, move |v| {
                let Some(p) = w.upgrade() else { return };
                if let Some(e) = p.edits.borrow_mut().as_mut() {
                    e.adjust[i] = v / 100.0;
                }
                // a drag sends many values: one redraw per frame
                if !pend.replace(true) {
                    let (w, pend) = (w.clone(), pend.clone());
                    glib::idle_add_local_once(move || {
                        pend.set(false);
                        if let Some(p) = w.upgrade() {
                            p.show_edits();
                        }
                    });
                }
            }, -100.0, 100.0, Some(0.0));
            col.append(&sl);
            sliders.push(sl);
        }
        let reset = gtk::Button::builder().label(tr!("Reset All")).halign(gtk::Align::End).margin_top(6).build();
        reset.add_css_class("sonata-button");
        let w = self.me.clone();
        reset.connect_clicked(move |_| {
            for sl in &sliders {
                sl.set_value(0.0);
            }
            if let Some(p) = w.upgrade() {
                if let Some(e) = p.edits.borrow_mut().as_mut() {
                    e.adjust = [0.0; 5];
                }
                p.show_edits();
            }
        });
        col.append(&reset);
        sonata_ui::panel::popup(&self.adjust_btn, &col, gtk::PositionType::Bottom);
    }

    // Resize… (macOS: Tools > Adjust Size…): a small sheet under the toolbar
    pub fn resize_sheet(&self) {
        if !self.editor() {
            return;
        }
        let Some((w0, h0)) = self.edits.borrow().as_ref().map(|e| e.size()) else { return };
        let (w0, h0) = (w0 as f64, h0 as f64);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 8);
        col.add_css_class("pv-sheet");
        let t = gtk::Label::builder().label(tr!("Resize")).xalign(0.0).build();
        t.add_css_class("pv-sheet-title");
        col.append(&t);
        let grid = gtk::Grid::builder().column_spacing(8).row_spacing(6).build();
        let spin = |value: f64, upper: f64| {
            let s = gtk::SpinButton::with_range(1.0, upper, 1.0);
            s.set_value(value);
            s.set_width_chars(6);
            s
        };
        let (width, height, percent) = (spin(w0, 100000.0), spin(h0, 100000.0), spin(100.0, 1000.0));
        for (i, (name, sb, unit)) in [(tr!("Width:"), &width, tr!("pixels")), (tr!("Height:"), &height, tr!("pixels")), (tr!("Scale:"), &percent, tr!("percent"))].into_iter().enumerate() {
            grid.attach(&gtk::Label::builder().label(name).xalign(1.0).build(), 0, i as i32, 1, 1);
            grid.attach(sb, 1, i as i32, 1, 1);
            let u = gtk::Label::builder().label(unit).xalign(0.0).build();
            u.add_css_class("pv-sheet-dim");
            grid.attach(&u, 2, i as i32, 1, 1);
        }
        col.append(&grid);
        let keep = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        let ratio = controls::switch(true);
        keep.append(&ratio);
        keep.append(&gtk::Label::builder().label(tr!("Scale proportionally")).xalign(0.0).build());
        col.append(&keep);
        let result = gtk::Label::builder().xalign(0.0).build();
        result.add_css_class("pv-sheet-dim");
        col.append(&result);
        let busy = Rc::new(Cell::new(false));
        let sync = {
            let (width, height, percent, ratio, result, busy) = (width.clone(), height.clone(), percent.clone(), ratio.clone(), result.clone(), busy.clone());
            Rc::new(move |src: Option<&str>| {
                if busy.replace(true) {
                    return;
                }
                match src {
                    Some("percent") => {
                        width.set_value((w0 * percent.value() / 100.0).round());
                        height.set_value((h0 * percent.value() / 100.0).round());
                    }
                    Some(which) if ratio.is_active() => {
                        if which == "width" {
                            height.set_value((width.value() * h0 / w0).round().max(1.0));
                        } else {
                            width.set_value((height.value() * w0 / h0).round().max(1.0));
                        }
                        percent.set_value((width.value() / w0 * 100.0).round());
                    }
                    _ => {}
                }
                result.set_label(&fmt(&tr!("Resulting size: {w} × {h} pixels"), &[("w", &(width.value() as i64).to_string()), ("h", &(height.value() as i64).to_string())]));
                busy.set(false);
            })
        };
        for (sb, name) in [(&width, "width"), (&height, "height"), (&percent, "percent")] {
            let s = sync.clone();
            sb.connect_value_changed(move |_| s(Some(name)));
        }
        let s = sync.clone();
        ratio.connect_active_notify(move |r| {
            if r.is_active() {
                s(Some("width"));
            }
        });
        sync(None);
        let buttons = gtk::Box::builder().spacing(8).halign(gtk::Align::End).margin_top(4).build();
        let pop_cell: Rc<RefCell<Option<gtk::Popover>>> = Rc::default();
        let pc = pop_cell.clone();
        buttons.append(&controls::push_button(&tr!("Cancel"), move || {
            if let Some(p) = pc.borrow().as_ref() {
                p.popdown();
            }
        }, ""));
        let (pc, me) = (pop_cell.clone(), self.me.clone());
        buttons.append(&controls::push_button(&tr!("OK"), move || {
            if let Some(p) = me.upgrade() {
                p.resize_to(width.value() as u32, height.value() as u32);
            }
            if let Some(p) = pc.borrow().as_ref() {
                p.popdown();
            }
        }, "default"));
        col.append(&buttons);
        *pop_cell.borrow_mut() = Some(sonata_ui::panel::popup(&self.toolbar, &col, gtk::PositionType::Bottom));
    }

    fn resize_to(&self, w: u32, h: u32) {
        if !self.editor() {
            return;
        }
        let changed = self.edits.borrow_mut().as_mut().is_some_and(|e| {
            let differs = (w, h) != e.size();
            if differs {
                e.resize_to(w, h);
            }
            differs
        });
        if changed {
            self.show_edits();
        }
    }

    // Export As…: the format (and quality) first, then where
    pub fn export_sheet(&self) {
        let Some(path) = self.cur() else { return };
        let fmt_now = edit::format_for(&path);
        let current = EXPORT_FORMATS.iter().position(|f| Some(f.1) == fmt_now).unwrap_or(0) as u32;
        let col = gtk::Box::new(gtk::Orientation::Vertical, 8);
        col.add_css_class("pv-sheet");
        col.set_size_request(260, -1);
        let t = gtk::Label::builder().label(tr!("Export As")).xalign(0.0).build();
        t.add_css_class("pv-sheet-title");
        col.append(&t);
        let row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        row.append(&gtk::Label::builder().label(tr!("Format:")).xalign(1.0).build());
        let names: Vec<String> = EXPORT_FORMATS.iter().map(|f| f.0.to_string()).collect();
        let qrow = gtk::Box::new(gtk::Orientation::Vertical, 2);
        let qr = qrow.clone();
        let fmt_dd = controls::popup_button(&names, current, move |i| qr.set_visible(EXPORT_FORMATS[i as usize].3));
        fmt_dd.set_hexpand(true);
        row.append(&fmt_dd);
        col.append(&row);
        let qlabel = gtk::Label::builder().xalign(0.0).build();
        let quality = Rc::new(Cell::new(85u32));
        let (q, ql) = (quality.clone(), qlabel.clone());
        let set_q = move |v: f64| {
            q.set(v as u32);
            ql.set_label(&fmt(&tr!("Quality: {q} %"), &[("q", &(v as u32).to_string())]));
        };
        set_q(85.0);
        qrow.append(&qlabel);
        qrow.append(&controls::slider(85.0, set_q, 10.0, 100.0, Some(85.0)));
        qrow.set_visible(EXPORT_FORMATS[current as usize].3);
        col.append(&qrow);
        let pop_cell: Rc<RefCell<Option<gtk::Popover>>> = Rc::default();
        let buttons = gtk::Box::builder().spacing(8).halign(gtk::Align::End).margin_top(4).build();
        let pc = pop_cell.clone();
        buttons.append(&controls::push_button(&tr!("Cancel"), move || {
            if let Some(p) = pc.borrow().as_ref() {
                p.popdown();
            }
        }, ""));
        let (pc, me, dd) = (pop_cell.clone(), self.me.clone(), fmt_dd.clone());
        buttons.append(&controls::push_button(&tr!("Save…"), move || {
            let (_n, f, ext, _q) = EXPORT_FORMATS[dd.selected() as usize];
            if let Some(p) = pc.borrow().as_ref() {
                p.popdown();
            }
            if let Some(p) = me.upgrade() {
                p.export_as(f, ext, quality.get());
            }
        }, "default"));
        col.append(&buttons);
        *pop_cell.borrow_mut() = Some(sonata_ui::panel::popup(&self.toolbar, &col, gtk::PositionType::Bottom));
    }

    /// A copy of the (edited) picture in another format; the window stays on this one.
    fn export_as(&self, fmt_name: &'static str, ext: &'static str, quality: u32) {
        let Some(path) = self.cur() else { return };
        if self.edits.borrow().is_none() && Edits::load(&path).is_none() {
            dialog::alert(&tr!("This picture can't be exported."), &tr!("Preview can only view it."), &[("ok", tr!("OK"), "default")], |_| {}, Some(self.window.upcast_ref()));
            return;
        }
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let f = gtk::FileFilter::new();
        f.set_name(Some(&tr!("Images")));
        f.add_pattern(&format!("*{ext}"));
        filters.append(&f);
        let dlg = gtk::FileDialog::builder()
            .title(tr!("Export As"))
            .modal(true)
            .initial_folder(&gio::File::for_path(dir_of(&path)))
            .initial_name(format!("{}{ext}", stem(&path)))
            .filters(&filters)
            .build();
        let w = self.me.clone();
        dlg.save(Some(&self.window), gio::Cancellable::NONE, move |res| {
            let (Ok(file), Some(p)) = (res, w.upgrade()) else { return };
            let Some(chosen) = file.path().map(|p| p.display().to_string()) else { return };
            let ok_ext: &[&str] = match ext {
                ".jpg" => &[".jpg", ".jpeg"],
                ".tiff" => &[".tiff", ".tif"],
                e => std::slice::from_ref(match e { ".png" => &".png", ".webp" => &".webp", _ => &".png" }),
            };
            let final_ = if ok_ext.contains(&edit::ext_of(&chosen).as_str()) { chosen.clone() } else { format!("{chosen}{ext}") };
            let w2 = p.me.clone();
            p.confirm_replace(&chosen, &final_, Box::new(move |target: &str| {
                let Some(p) = w2.upgrade() else { return };
                let fresh;
                let res = {
                    let e = p.edits.borrow();
                    match e.as_ref() {
                        Some(ed) => ed.write(target, Some(fmt_name), quality),
                        None => {
                            fresh = Edits::load(&p.cur().unwrap_or_default());
                            match fresh.as_ref() {
                                Some(ed) => ed.write(target, Some(fmt_name), quality),
                                None => Err(tr!("Preview can only view it.")),
                            }
                        }
                    }
                };
                if let Err(e) = res {
                    dialog::alert(&tr!("The picture couldn't be exported."), &e, &[("ok", tr!("OK"), "default")], |_| {}, Some(p.window.upcast_ref()));
                }
            }));
        });
    }

    // saving
    pub fn save(&self, then: Option<Box<dyn Fn()>>) {
        if !self.edited() {
            if let Some(t) = then {
                t();
            }
            return;
        }
        let Some(path) = self.cur() else { return };
        if !edit::writable(&path) {
            self.save_as(then);
            return;
        }
        let w = self.me.clone();
        let p2 = path.clone();
        self.save_async(path, Box::new(move || {
            if let Some(p) = w.upgrade() {
                p.show_edits();
                p.thumbs.refresh(&p2); // its thumbnail is made again (new mtime)
            }
            if let Some(t) = &then {
                t();
            }
        }));
    }

    /// Render and write the full-size picture off the main loop; edits and
    /// ← / → wait meanwhile. done() on success; an alert on failure.
    fn save_async(&self, path: String, done: Box<dyn Fn()>) {
        if self.saving.get() {
            return;
        }
        let Some(mut ed) = self.edits.borrow_mut().take() else { return };
        self.saving.set(true);
        let w = self.me.clone();
        glib::spawn_future_local(async move {
            let (ed, res) = gio::spawn_blocking(move || {
                let r = ed.save(&path);
                (ed, r)
            })
            .await
            .unwrap_or_else(|_| panic!("the save worker never panics"));
            let Some(p) = w.upgrade() else { return };
            *p.edits.borrow_mut() = Some(ed);
            p.saving.set(false);
            match res {
                Ok(()) => done(),
                Err(e) => {
                    dialog::alert(&tr!("The picture couldn't be saved."), &e, &[("ok", tr!("OK"), "default")], |_| {}, Some(p.window.upcast_ref()));
                }
            }
        });
    }

    /// Save As / Export add the extension after the Save panel checked the
    /// name: a file with the final name is asked about here.
    fn confirm_replace(&self, chosen: &str, final_: &str, write: Box<dyn Fn(&str)>) {
        if final_ == chosen || std::fs::symlink_metadata(final_).is_err() {
            write(final_);
            return;
        }
        let target = final_.to_string();
        dialog::alert(
            &fmt(&tr!("“{name}” already exists. Do you want to replace it?"), &[("name", &base(final_))]),
            &tr!("A file with the same name already exists. Replacing it will overwrite its contents."),
            &[("cancel", tr!("Cancel"), ""), ("replace", tr!("Replace"), "destructive")],
            move |rid| {
                if rid == "replace" {
                    write(&target);
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    pub fn save_as(&self, then: Option<Box<dyn Fn()>>) {
        if self.edits.borrow().is_none() && !self.editor() {
            return;
        }
        let Some(path) = self.cur() else { return };
        let ext = if edit::writable(&path) { std::path::Path::new(&path).extension().map(|e| format!(".{}", e.to_string_lossy())).unwrap_or_default() } else { ".jpg".into() };
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let f = gtk::FileFilter::new();
        f.set_name(Some(&tr!("Images")));
        for p in ["*.png", "*.jpg", "*.jpeg", "*.webp"] {
            f.add_pattern(p);
        }
        filters.append(&f);
        let dlg = gtk::FileDialog::builder()
            .title(tr!("Save As"))
            .modal(true)
            .initial_folder(&gio::File::for_path(dir_of(&path)))
            .initial_name(format!("{}{ext}", stem(&path)))
            .filters(&filters)
            .build();
        let w = self.me.clone();
        let then = Rc::new(then);
        dlg.save(Some(&self.window), gio::Cancellable::NONE, move |res| {
            let (Ok(file), Some(p)) = (res, w.upgrade()) else { return };
            let Some(target) = file.path().map(|p| p.display().to_string()) else { return };
            let ok = [".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"];
            let final_ = if ok.contains(&edit::ext_of(&target).as_str()) { target.clone() } else { format!("{target}.png") };
            let (w2, then) = (p.me.clone(), then.clone());
            p.confirm_replace(&target, &final_, Box::new(move |t: &str| {
                let Some(p) = w2.upgrade() else { return };
                let (w3, t2, then) = (p.me.clone(), t.to_string(), then.clone());
                p.save_async(t.to_string(), Box::new(move || {
                    if let Some(p) = w3.upgrade() {
                        *p.edits.borrow_mut() = None;
                        p.open(&t2, false);
                    }
                    if let Some(f) = then.as_ref() {
                        f();
                    }
                }));
            }));
        });
    }

    /// The "keep the changes?" alert, once at a time.
    fn ask_save(&self, then: Box<dyn Fn()>) {
        if self.asking.replace(true) {
            return;
        }
        let name = base(&self.cur().unwrap_or_default());
        let w = self.me.clone();
        let then = Rc::new(then);
        dialog::alert(
            &fmt(&tr!("Do you want to keep the changes you made to “{name}”?"), &[("name", &name)]),
            &tr!("Your changes will be lost if you don't save them."),
            &[("discard", tr!("Don't Save"), "destructive"), ("cancel", tr!("Cancel"), ""), ("save", tr!("Save"), "default")],
            move |rid| {
                let Some(p) = w.upgrade() else { return };
                p.asking.set(false);
                match rid {
                    "save" => {
                        let t = then.clone();
                        p.save(Some(Box::new(move || t())));
                    }
                    "discard" => {
                        if let Some(e) = p.edits.borrow_mut().as_mut() {
                            e.revert();
                        }
                        then();
                    }
                    _ => {
                        if let Some(c) = p.cur() {
                            p.thumbs.select(&c);
                        }
                    }
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    /// True: the close waits (marks or edits to deal with first).
    fn close_request(&self) -> bool {
        let marked = self.markup.borrow().as_ref().is_some_and(|l| !l.items.borrow().is_empty());
        if marked {
            // marks not Done yet: they count as an edit (a screenshot saves them,
            // as Done would; another picture asks) -- never lost silently
            let shot = self.from_shot.borrow().clone();
            let is_shot = shot.is_some() && shot == self.cur();
            let win = self.window.clone();
            self.end_markup(true, if is_shot { Some(Box::new(move || win.destroy())) } else { None });
            if is_shot {
                return true;
            }
        }
        if self.edited() {
            let win = self.window.clone();
            self.ask_save(Box::new(move || win.destroy()));
            return true;
        }
        self.stop_slideshow();
        false
    }

    // -- view ------------------------------------------------------------------------------
    pub fn set_zoom(&self, z: Option<f64>, anchor: Option<(f64, f64)>, animate: bool) {
        let z = z.map(|z| z.clamp(ZOOMS[0], ZOOMS[ZOOMS.len() - 1]));
        self.canvas.set_zoom(z, anchor, animate);
    }

    pub fn step_zoom(&self, step: i32) {
        let cur = self.canvas.target().or_else(|| self.canvas.zoom()).unwrap_or_else(|| self.canvas.fit_scale());
        let next = if step > 0 {
            ZOOMS.iter().copied().find(|z| *z > cur + 1e-3).unwrap_or(ZOOMS[ZOOMS.len() - 1])
        } else {
            ZOOMS.iter().copied().rev().find(|z| *z < cur - 1e-3).unwrap_or(ZOOMS[0])
        };
        self.set_zoom(Some(next), None, true);
    }

    fn toggle_fullscreen(&self) {
        if self.slides.get().is_some() {
            self.stop_slideshow();
            return;
        }
        let full = !self.window.is_fullscreen();
        if full {
            self.window.fullscreen();
        } else {
            self.window.unfullscreen();
        }
        self.toolbar.set_visible(!full);
    }

    // -- input -----------------------------------------------------------------------------
    fn context_menu(&self, x: f64, y: f64) {
        let uri = self.cur().map(|p| gio::File::for_path(p).uri().to_string());
        let edited = self.edited();
        let has_ops = self.edits.borrow().as_ref().is_some_and(|e| !e.ops.is_empty());
        let item = |label: String, f: fn(&PreviewWindow)| {
            let w = self.me.clone();
            menu::Item::new(&label, move || {
                if let Some(p) = w.upgrade() {
                    f(&p);
                }
            })
        };
        let check = |label: String, on: bool, f: fn(&PreviewWindow)| {
            let w = self.me.clone();
            menu::Item::check(&label, on, move |_| {
                if let Some(p) = w.upgrade() {
                    f(&p);
                }
            })
        };
        let w = self.me.clone();
        let actual = menu::Item::new(&tr!("Actual Size"), move || {
            if let Some(p) = w.upgrade() {
                p.set_zoom(Some(1.0), Some((x, y)), true);
            }
        });
        let wallpaper = menu::Item::new(&tr!("Set Desktop Picture"), {
            let uri = uri.clone().unwrap_or_default();
            move || {
                if let Err(e) = sonata_config::set_wallpaper(&uri) {
                    eprintln!("sonata2-rs preview: wallpaper: {e}");
                }
            }
        })
        .enabled(uri.is_some());
        menu::popup(
            &self.canvas,
            vec![
                vec![item(tr!("Copy"), |p| p.copy_picture()).enabled(self.texture.borrow().is_some())],
                vec![
                    item(tr!("Zoom In"), |p| p.step_zoom(1)),
                    item(tr!("Zoom Out"), |p| p.step_zoom(-1)),
                    item(tr!("Zoom to Fit"), |p| p.set_zoom(None, None, true)),
                    actual,
                ],
                vec![
                    check(tr!("Thumbnails"), self.sidebar_rev.reveals_child(), |p| p.toggle_sidebar()),
                    check(tr!("Show Info"), self.info_rev.reveals_child(), |p| p.toggle_info()),
                    item(tr!("Slideshow"), |p| p.slideshow()),
                ],
                vec![
                    item(tr!("Rotate Left"), |p| p.rotate(-90)),
                    item(tr!("Rotate Right"), |p| p.rotate(90)),
                    item(tr!("Flip Horizontal"), |p| p.edit(Op::Flip('h'))),
                    item(tr!("Flip Vertical"), |p| p.edit(Op::Flip('v'))),
                ],
                vec![item(tr!("Crop"), |p| p.crop_button()), item(tr!("Adjust Color…"), |p| p.adjust_panel()), item(tr!("Resize…"), |p| p.resize_sheet())],
                vec![
                    item(tr!("Undo"), |p| p.undo()).enabled(has_ops),
                    item(tr!("Revert"), |p| p.revert()).enabled(edited),
                    item(tr!("Save"), |p| p.save(None)).enabled(edited),
                    item(tr!("Save As…"), |p| p.save_as(None)),
                    item(tr!("Export As…"), |p| p.export_sheet()),
                ],
                vec![wallpaper],
            ],
            menu::Options::at(x, y),
        );
    }

    fn key(&self, keyval: Key, state: ModifierType) -> bool {
        let cmd = state.intersects(ModifierType::CONTROL_MASK | ModifierType::SUPER_MASK);
        let shift = state.contains(ModifierType::SHIFT_MASK);
        let alt = state.contains(ModifierType::ALT_MASK);
        let k = keyval.to_lower();
        let layer = self.markup.borrow().clone();
        if let Some(lay) = layer {
            // Markup: its own keys, no navigation
            match k {
                Key::z if cmd => {
                    if shift {
                        lay.redo();
                    } else {
                        lay.undo();
                    }
                }
                Key::y if cmd => {
                    lay.redo();
                }
                Key::Delete | Key::BackSpace => lay.delete_selected(),
                Key::Escape if lay.sel.get().is_some() => lay.deselect(),
                Key::Escape => self.end_markup(true, None),
                Key::Return | Key::KP_Enter if lay.sel.get().is_none() => self.end_markup(true, None),
                Key::w if cmd => self.window.close(),
                Key::s if cmd => {
                    let w = self.me.clone();
                    self.end_markup(true, Some(Box::new(move || {
                        if let Some(p) = w.upgrade() {
                            p.save(None);
                        }
                    })));
                }
                _ => return false,
            }
            return true;
        }
        if self.slides.get().is_some() && !cmd {
            // slideshow keys
            match keyval {
                Key::Left | Key::Up => {
                    self.go(-1, true);
                    self.arm_slides();
                }
                Key::Right | Key::Down => {
                    self.go(1, true);
                    self.arm_slides();
                }
                Key::space => self.pause_slides(),
                Key::Escape => self.stop_slideshow(),
                _ => return false,
            }
            return true;
        }
        if cmd {
            match k {
                Key::s if shift => self.save_as(None),
                Key::f if shift => self.slideshow(),
                _ if alt && matches!(keyval, Key::_2 | Key::at | Key::KP_2 | Key::twosuperior) => self.toggle_sidebar(),
                Key::plus | Key::equal => self.step_zoom(1),
                Key::minus => self.step_zoom(-1),
                Key::_0 => self.set_zoom(None, None, true),
                Key::_9 => self.set_zoom(Some(1.0), None, true),
                Key::r => self.rotate(90),
                Key::l => self.rotate(-90),
                Key::w => self.window.close(),
                Key::f => self.toggle_fullscreen(),
                Key::z => self.undo(),
                Key::s => self.save(None),
                Key::k => self.crop_button(),
                Key::i => self.toggle_info(),
                _ => return false,
            }
            return true;
        }
        if self.crop_area.is_visible() && matches!(keyval, Key::Return | Key::KP_Enter | Key::Escape) {
            if keyval == Key::Escape {
                self.end_crop();
            } else {
                self.apply_crop();
            }
            return true;
        }
        match keyval {
            Key::Left | Key::Up => self.go(-1, false),
            Key::Right | Key::Down => self.go(1, false),
            Key::space => self.toggle_fullscreen(),
            Key::Escape if self.window.is_fullscreen() => self.toggle_fullscreen(),
            _ => return false,
        }
        true
    }
}

thread_local! {
    static WINDOWS: RefCell<Vec<Rc<PreviewWindow>>> = RefCell::default();
}

fn keep(w: Rc<PreviewWindow>) -> Rc<PreviewWindow> {
    WINDOWS.with(|ws| ws.borrow_mut().push(w.clone()));
    let win = w.window.clone();
    w.window.connect_destroy(move |_| WINDOWS.with(|ws| ws.borrow_mut().retain(|x| x.window != win)));
    w
}

fn window_for(path: &str) -> Option<Rc<PreviewWindow>> {
    WINDOWS.with(|ws| ws.borrow().iter().find(|w| w.cur().as_deref() == Some(path)).cloned())
}

/// A screenshot's thumbnail clicked: the picture in Markup.
pub fn open_markup(app: &impl IsA<gtk::Application>, path: &str) {
    if let Some(same) = window_for(path) {
        if !same.edited() && same.markup.borrow().is_none() {
            // a new screenshot under the same name (the clipboard's): the new picture
            *same.from_shot.borrow_mut() = Some(path.to_string());
            same.markup_on_open.set(true);
            same.window.present();
            *same.edits.borrow_mut() = None;
            same.opened(path, imageload::texture(path), false);
            same.thumbs.refresh(path);
            return;
        }
        same.window.present();
        if same.markup.borrow().is_none() {
            same.markup_button();
        }
        return;
    }
    keep(PreviewWindow::new(app, path, None, true)).window.present();
}

/// One window per picture; several pictures opened together share one
/// window with their thumbnails in the sidebar.
pub fn open_paths(app: &impl IsA<gtk::Application>, files: &[gio::File]) {
    let paths: Vec<String> = files.iter().filter_map(|f| f.path().map(|p| p.display().to_string())).collect();
    if paths.len() > 1 {
        keep(PreviewWindow::new(app, &paths[0], Some(paths.clone()), false)).window.present();
    } else if let Some(p) = paths.first() {
        match window_for(p) {
            Some(w) => w.window.present(),
            None => keep(PreviewWindow::new(app, p, None, false)).window.present(),
        }
    }
    if files.is_empty() && WINDOWS.with(|ws| ws.borrow().is_empty()) {
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let f = gtk::FileFilter::new();
        f.set_name(Some(&tr!("Images")));
        f.add_mime_type("image/*");
        filters.append(&f);
        let dlg = gtk::FileDialog::builder().title(tr!("Open")).modal(true).filters(&filters).build();
        let app = app.upcast_ref::<gtk::Application>().clone();
        // the app stays while the panel is up
        let hold = app.hold();
        dlg.open_multiple(None::<&gtk::Window>, gio::Cancellable::NONE, move |res| {
            let _hold = hold;
            if let Ok(list) = res {
                let files: Vec<gio::File> = (0..list.n_items()).filter_map(|i| list.item(i).and_downcast::<gio::File>()).collect();
                if !files.is_empty() {
                    open_paths(&app, &files);
                }
            }
        });
    }
}

/// Run Preview: pictures open in the running one; `--markup PATH` opens a
/// screenshot straight into Markup.
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(crate::app_id()).flags(gio::ApplicationFlags::HANDLES_OPEN).build();
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
        }
    };
    let act = gio::SimpleAction::new("markup", Some(glib::VariantTy::STRING));
    let (a, s) = (app.clone(), start.clone());
    act.connect_activate(move |_, param| {
        s();
        if let Some(p) = param.and_then(|v| v.get::<String>()) {
            open_markup(&a, &p);
        }
    });
    app.add_action(&act);
    let s = start.clone();
    app.connect_open(move |app, files, _| {
        s();
        open_paths(app, files);
    });
    let files: Vec<String> = args.iter().filter(|a| !a.starts_with("--")).cloned().collect();
    let markup = args.iter().any(|a| a == "--markup");
    if markup {
        if let Some(p) = files.first() {
            let path = gio::File::for_commandline_arg(p).path().map(|p| p.display().to_string()).unwrap_or_default();
            if app.register(gio::Cancellable::NONE).is_ok() && app.is_remote() {
                app.activate_action("markup", Some(&path.to_variant()));
                return glib::ExitCode::SUCCESS;
            }
            let s = start.clone();
            app.connect_activate(move |app| {
                s();
                open_markup(app, &path);
            });
            return app.run_with_args(&[std::env::args().next().unwrap_or_default()]);
        }
    }
    let s = start.clone();
    app.connect_activate(move |app| {
        s();
        open_paths(app, &[]);
    });
    let mut argv = vec![std::env::args().next().unwrap_or_default()];
    argv.extend(files);
    app.run_with_args(&argv)
}

/// The MimeType line of Preview's desktop entry.
pub fn mime_types() -> String {
    let base = "image/png;image/jpeg;image/gif;image/webp;image/bmp;image/tiff;image/svg+xml;image/x-icon;image/avif;image/heic;image/heif;image/jxl;";
    format!("{base}{}", RAW_TYPES.iter().map(|t| format!("{t};")).collect::<String>())
}
