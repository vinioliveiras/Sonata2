//! Window previews in the Dock (shell/dock_preview.py; Vini: like
//! Windows' taskbar, in Sonata's look): hovering an app shows every window
//! it has open (minimized ones too) above its icon, live; click one to
//! bring it forward.
//!
//! Wayland gives no client pictures of other windows. Sonata's Wayfire
//! plugin draws a window by itself, even covered or on another desktop
//! (`sonata/window-shot`): the popover opens at once with the windows'
//! names, their pictures arrive from a thread, and are drawn again every
//! REFRESH_MS while it's open. Without that plugin, the older way: a
//! picture taken with grim when a window gets the focus (it's on top then).

use crate::dock::{Dock, Tile, SHADOW};
use gtk::prelude::*;
use gtk::{gdk, glib, graphene, pango};
use serde_json::{json, Value};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::time::Duration;

/// small (Vini): a window is at most PIC_W x PIC_H -- the size of Mission
/// Control's desktop pictures
pub const PIC_W: i32 = 128;
pub const PIC_H: i32 = 80;
/// (crisp at 2x scale)
pub const SHOT_W: i32 = 2 * PIC_W;
/// the app's icon standing in until a picture comes
pub const ICON_PX: i32 = 32;
pub const MAX_WINDOWS: usize = 6;
/// the pointer rests on the icon this long first (Vini: not at once)
pub const HOVER_MS: u64 = 1000;
/// pictures drawn again while the previews are open: live, cheaply
pub const REFRESH_MS: u64 = 1500;
/// let the window finish drawing / animating
pub const CAPTURE_DELAY_MS: u64 = 700;

/// Sonata's plugin answers sonata/window-shot (an error about the request
/// is still an answer); Wayfire without it: no such method.
pub fn has_window_shot(reply: &Value) -> bool {
    let err = reply.get("error").map(|e| e.as_str().map(String::from).unwrap_or_else(|| e.to_string())).unwrap_or_default().to_lowercase();
    !(err.contains("method") && (err.contains("no such") || err.contains("not found") || err.contains("unknown")))
}

#[derive(Clone, Debug, PartialEq)]
pub struct Win {
    pub id: i64,
    pub title: String,
    pub minimized: bool,
    pub last: i64,
}

/// A Dock app's open windows from Wayfire's list, the last used first.
pub fn windows_of(views: &Value, key: &str) -> Vec<Win> {
    let mut out: Vec<Win> = views
        .as_array()
        .into_iter()
        .flatten()
        .filter(|v| v.get("type").and_then(Value::as_str) == Some("toplevel") && v.get("mapped").and_then(Value::as_bool).unwrap_or(true) && matches!(v.get("role").and_then(Value::as_str), None | Some("toplevel")))
        .filter(|v| {
            let aid = v.get("app-id").and_then(Value::as_str).unwrap_or("");
            sonata_desktop::appmatch::match_app_id(aid).unwrap_or_else(|| aid.to_string()) == key
        })
        .filter_map(|v| {
            Some(Win {
                id: v.get("id")?.as_i64()?,
                title: v.get("title").and_then(Value::as_str).unwrap_or("").to_string(),
                minimized: v.get("minimized").and_then(Value::as_bool).unwrap_or(false),
                last: v.get("last-focus-timestamp").and_then(Value::as_i64).unwrap_or(0),
            })
        })
        .collect();
    out.sort_by_key(|w| -w.last);
    out.truncate(MAX_WINDOWS);
    out
}

#[derive(Default)]
struct Shot {
    tex: Option<gdk::Texture>,
    app: String,
    minimized: bool,
}

/// view id -> its picture and what it is, kept up to date.
pub struct WindowShots {
    shots: RefCell<HashMap<i64, Shot>>,
    /// sonata/window-shot answers (None: not asked yet)
    compositor: Rc<Cell<Option<bool>>>,
    grim: bool,
    pub enabled: bool,
    pending: RefCell<Option<glib::SourceId>>,
    grabbing: Rc<Cell<bool>>,
}

thread_local! {
    static SHOTS: RefCell<Option<Rc<WindowShots>>> = const { RefCell::new(None) };
}

pub fn shots() -> Rc<WindowShots> {
    SHOTS.with(|s| s.borrow_mut().get_or_insert_with(WindowShots::start).clone())
}

/// Wayfire's answer from a thread, back on the main loop.
fn in_thread<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static, done: impl FnOnce(T) + 'static) {
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let _ = tx.send(work());
    });
    let done = RefCell::new(Some(done));
    glib::timeout_add_local(Duration::from_millis(25), move || match rx.try_recv() {
        Ok(v) => {
            if let Some(f) = done.borrow_mut().take() {
                f(v);
            }
            glib::ControlFlow::Break
        }
        Err(std::sync::mpsc::TryRecvError::Empty) => glib::ControlFlow::Continue,
        Err(_) => glib::ControlFlow::Break,
    });
}

impl WindowShots {
    fn start() -> Rc<WindowShots> {
        let m = crate::toplevels::manager();
        let s = Rc::new(WindowShots {
            shots: RefCell::default(),
            compositor: Rc::new(Cell::new(None)),
            grim: glib::find_program_in_path("grim").is_some(),
            enabled: m.available,
            pending: RefCell::default(),
            grabbing: Rc::new(Cell::new(false)),
        });
        if s.enabled {
            let w = Rc::downgrade(&s);
            m.add_event_listener(Rc::new(move |msg| {
                if let Some(s) = w.upgrade() {
                    s.event(msg);
                }
            }));
            // asked once now: until a preview opened it was unknown, and
            // every focus change took a grim picture even with the plugin there
            let c = s.compositor.clone();
            in_thread(|| sonata_backend::wfipc::call("sonata/window-shot", json!({"id": 0, "path": ""})), move |r| {
                if let Some(r) = r {
                    c.set(Some(has_window_shot(&r)));
                }
            });
        }
        s
    }

    /// The window's picture now, from a thread; done(texture) on the main
    /// loop. Nothing without the plugin (the grim pictures stand in).
    pub fn snapshot(self: &Rc<Self>, vid: i64, done: impl Fn(gdk::Texture) + 'static) {
        if self.compositor.get() == Some(false) {
            return;
        }
        let comp = self.compositor.clone();
        let w = Rc::downgrade(self);
        in_thread(
            move || {
                let path = glib::user_runtime_dir().join(format!("sonata2rs-shot-{}-{vid}", std::process::id()));
                let r = sonata_backend::wfipc::call("sonata/window-shot", json!({"id": vid, "width": SHOT_W, "path": path.to_string_lossy()}))?;
                if r.get("result").and_then(Value::as_str) != Some("ok") {
                    return Some(Err(has_window_shot(&r)));
                }
                let data = std::fs::read(&path).ok()?;
                let _ = std::fs::remove_file(&path);
                let (w, h) = (r.get("width")?.as_i64()?, r.get("height")?.as_i64()?);
                (w > 0 && h > 0 && data.len() as i64 == w * h * 4).then_some(Ok((data, w, h)))
            },
            move |got| match got {
                Some(Ok((data, wd, ht))) => {
                    comp.set(Some(true));
                    let tex: gdk::Texture = gdk::MemoryTexture::new(wd as i32, ht as i32, gdk::MemoryFormat::R8g8b8a8Premultiplied, &glib::Bytes::from_owned(data), (wd * 4) as usize).upcast();
                    if let Some(s) = w.upgrade() {
                        s.shots.borrow_mut().entry(vid).or_default().tex = Some(tex.clone());
                    }
                    done(tex);
                }
                Some(Err(false)) => comp.set(Some(false)), // no such method: an older plugin
                _ => {}
            },
        );
    }

    /// A Dock app's open windows (with the pictures known), the last used first.
    pub fn open_windows(&self, key: &str) -> Vec<(Win, Option<gdk::Texture>)> {
        let views = sonata_backend::wfipc::call("window-rules/list-views", json!({})).unwrap_or(Value::Null);
        let shots = self.shots.borrow();
        windows_of(&views, key).into_iter().map(|w| {
            let tex = shots.get(&w.id).and_then(|s| s.tex.clone());
            (w, tex)
        }).collect()
    }

    fn event(self: &Rc<Self>, msg: &Value) {
        let view = msg.get("view").unwrap_or(&Value::Null);
        let Some(vid) = view.get("id").and_then(Value::as_i64) else { return };
        let ev = msg.get("event").and_then(Value::as_str).unwrap_or("");
        if !["view-focused", "view-minimized", "view-unmapped", "view-title-changed"].contains(&ev) {
            return;
        }
        if ev == "view-unmapped" {
            self.shots.borrow_mut().remove(&vid);
            return;
        }
        let minimized = view.get("minimized").and_then(Value::as_bool).unwrap_or(false);
        {
            let mut shots = self.shots.borrow_mut();
            let e = shots.entry(vid).or_default();
            e.app = view.get("app-id").and_then(Value::as_str).unwrap_or("").to_string();
            e.minimized = minimized;
        }
        // (the plugin draws them itself)
        let toplevel = view.get("type").and_then(Value::as_str).unwrap_or("toplevel") == "toplevel";
        if ev == "view-focused" && toplevel && !minimized && self.grim && self.compositor.get() == Some(false) {
            if let Some(p) = self.pending.borrow_mut().take() {
                p.remove();
            }
            let w = Rc::downgrade(self);
            let src = glib::timeout_add_local_once(Duration::from_millis(CAPTURE_DELAY_MS), move || {
                if let Some(s) = w.upgrade() {
                    s.pending.replace(None);
                    s.capture(vid);
                }
            });
            self.pending.replace(Some(src));
        }
    }

    fn capture(self: &Rc<Self>, vid: i64) {
        if self.grabbing.get() {
            return; // (the next focus change takes it)
        }
        let info = sonata_backend::wfipc::call("window-rules/view-info", json!({"id": vid}));
        let view = info.as_ref().and_then(|i| i.get("info")).cloned().unwrap_or(Value::Null);
        let g = view.get("geometry").cloned().unwrap_or(Value::Null);
        let num = |k: &str| g.get(k).and_then(Value::as_f64).unwrap_or(0.0);
        if view.is_null() || view.get("minimized").and_then(Value::as_bool).unwrap_or(false) || !view.get("activated").and_then(Value::as_bool).unwrap_or(true) || num("width") < 40.0 {
            return;
        }
        // (whole pixels: Wayfire 0.12 reports 500.0 -- grim refused "500.0,241.5 920.0x560.0")
        let region = format!("{},{} {}x{}", num("x").round(), num("y").round(), num("width").round(), num("height").round());
        let scale = (SHOT_W as f64 / num("width")).min(1.0);
        self.grabbing.set(true);
        let (grab, w) = (self.grabbing.clone(), Rc::downgrade(self));
        in_thread(
            move || {
                std::process::Command::new("grim").args(["-g", &region, "-s", &format!("{scale:.3}"), "-t", "png", "-"]).output().ok().filter(|o| o.status.success() && !o.stdout.is_empty()).map(|o| o.stdout)
            },
            move |png| {
                grab.set(false);
                let (Some(png), Some(s)) = (png, w.upgrade()) else { return };
                if let Ok(tex) = gdk::Texture::from_bytes(&glib::Bytes::from_owned(png)) {
                    if let Some(e) = s.shots.borrow_mut().get_mut(&vid) {
                        e.tex = Some(tex);
                    }
                }
            },
        );
    }
}

pub fn close_window(vid: i64) {
    crate::toplevels::call_bg(move || {
        sonata_backend::wfipc::call("window-rules/close-view", json!({"id": vid}));
    });
}

pub fn restore(vid: i64) {
    crate::toplevels::call_bg(move || {
        sonata_backend::wfipc::call("wm-actions/set-minimized", json!({"view_id": vid, "state": false}));
        sonata_backend::wfipc::call("window-rules/focus-view", json!({"id": vid}));
    });
}

mod imp {
    use super::*;
    use gtk::subclass::prelude::*;

    #[derive(Default)]
    pub struct ShotPic {
        pub size: Cell<(i32, i32)>,
        pub paintable: RefCell<Option<gdk::Paintable>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ShotPic {
        const NAME: &'static str = "SonataDockShotPic";
        type Type = super::ShotPic;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for ShotPic {}

    impl WidgetImpl for ShotPic {
        fn measure(&self, o: gtk::Orientation, _for: i32) -> (i32, i32, i32, i32) {
            let (w, h) = self.size.get();
            let n = if o == gtk::Orientation::Horizontal { w } else { h };
            (n, n, -1, -1)
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            if let Some(p) = self.paintable.borrow().as_ref() {
                p.snapshot(snap, obj.width() as f64, obj.height() as f64);
            }
        }
    }
}

glib::wrapper! {
    /// A picture exactly its size big. A Gtk.Picture asks for its image's
    /// own size (a whole window: ~1000 px wide -- Vini: "a very big frame"),
    /// and a size request is only a minimum.
    pub struct ShotPic(ObjectSubclass<imp::ShotPic>) @extends gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

use gtk::subclass::prelude::ObjectSubclassIsExt;

impl ShotPic {
    fn new() -> Self {
        let p: Self = glib::Object::builder().property("halign", gtk::Align::Center).property("valign", gtk::Align::Center).property("hexpand", true).build();
        p.imp().size.set((ICON_PX, ICON_PX));
        p
    }

    fn set_box(&self, w: i32, h: i32) {
        self.imp().size.set((w, h));
        self.queue_resize();
    }

    fn paintable(&self) -> Option<gdk::Paintable> {
        self.imp().paintable.borrow().clone()
    }

    fn set_paintable(&self, p: Option<&impl IsA<gdk::Paintable>>) {
        self.imp().paintable.replace(p.map(|p| p.clone().upcast()));
        self.queue_draw();
    }
}

/// The windows above a Dock icon -- a small picture and the name; a click
/// brings that window forward (restored when minimized), x closes it.
pub struct PreviewPopover {
    pub pop: gtk::Popover,
    row: gtk::Box,
    pics: RefCell<HashMap<i64, ShotPic>>,
    items: RefCell<HashMap<i64, gtk::Overlay>>,
}

/// Where a small x that closes something goes: the side the user keeps
/// window buttons on (ui/window.close_align; Vini: "my buttons are all on the left").
fn close_align() -> gtk::Align {
    let side = sonata_config::stored("appearance").get("buttons_side").and_then(Value::as_str).map(String::from);
    if side.as_deref() == Some("right") {
        gtk::Align::End
    } else {
        gtk::Align::Start
    }
}

impl PreviewPopover {
    fn new(tile: &Tile, position: gtk::PositionType, windows: &[(Win, Option<gdk::Texture>)]) -> Rc<Self> {
        let pop = gtk::Popover::builder().css_classes(["dock-preview"]).has_arrow(false).autohide(false).position(position).build();
        let row = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        let p = Rc::new(PreviewPopover { pop: pop.clone(), row: row.clone(), pics: RefCell::default(), items: RefCell::default() });
        let icon = icon_of(tile);
        for (w, tex) in windows {
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            if w.minimized {
                col.add_css_class("minimized");
            }
            let slot = gtk::Box::builder().halign(gtk::Align::Center).valign(gtk::Align::Center).build(); // PIC_W x PIC_H, the picture inside
            slot.set_size_request(PIC_W, PIC_H);
            let pic = ShotPic::new();
            slot.append(&pic);
            col.append(&slot);
            let title = if w.title.is_empty() { tile.name() } else { w.title.clone() };
            col.append(&gtk::Label::builder().label(&title).ellipsize(pango::EllipsizeMode::End).max_width_chars(13).build());
            let b = gtk::Button::builder().child(&col).css_classes(["shot"]).can_focus(false).build();
            if !w.title.is_empty() {
                b.set_tooltip_text(Some(&w.title));
            }
            let (pp, vid) = (pop.clone(), w.id);
            b.connect_clicked(move |_| {
                pp.popdown();
                restore(vid);
            });
            let item = gtk::Overlay::builder().css_classes(["shot-item"]).child(&b).build();
            let x = gtk::Button::builder().icon_name("window-close-symbolic").css_classes(["shot-close"]).can_focus(false).halign(close_align()).valign(gtk::Align::Start).tooltip_text(tr!("Close Window")).build();
            let (pw, vid) = (Rc::downgrade(&p), w.id);
            x.connect_clicked(move |_| {
                if let Some(p) = pw.upgrade() {
                    p.close(vid);
                }
            });
            item.add_overlay(&x);
            row.append(&item);
            p.pics.borrow_mut().insert(w.id, pic);
            p.items.borrow_mut().insert(w.id, item);
            p.set_shot(w.id, tex.as_ref(), icon.as_ref());
        }
        pop.set_child(Some(&row));
        pop.set_parent(&tile.button);
        pop.connect_closed(|p| {
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        });
        p
    }

    /// The window's picture, as big as fits PIC_W x PIC_H in its own
    /// shape; none yet: the app's icon, small, in the middle.
    pub fn set_shot(&self, vid: i64, tex: Option<&gdk::Texture>, icon: Option<&gdk::Paintable>) {
        let Some(pic) = self.pics.borrow().get(&vid).cloned() else { return };
        let Some(tex) = tex else {
            if icon.is_none() && pic.paintable().is_some() {
                return; // (keep what's there)
            }
            pic.set_paintable(icon);
            pic.set_box(ICON_PX, ICON_PX);
            pic.remove_css_class("shot-pic");
            return;
        };
        let (w, h) = (tex.width().max(1) as f64, tex.height().max(1) as f64);
        let k = (PIC_W as f64 / w).min(PIC_H as f64 / h);
        pic.set_paintable(Some(tex));
        pic.set_box(((w * k).round() as i32).max(1), ((h * k).round() as i32).max(1));
        pic.add_css_class("shot-pic");
    }

    /// x: that window closes (the app may ask to save first); its preview goes.
    fn close(&self, vid: i64) {
        close_window(vid);
        self.pics.borrow_mut().remove(&vid);
        if let Some(item) = self.items.borrow_mut().remove(&vid) {
            self.row.remove(&item);
        }
        if self.items.borrow().is_empty() {
            self.pop.popdown();
        }
    }

    fn ids(&self) -> Vec<i64> {
        self.pics.borrow().keys().copied().collect()
    }
}

/// The app's icon, standing in for a window's picture until it comes.
fn icon_of(tile: &Tile) -> Option<gdk::Paintable> {
    let theme = gtk::IconTheme::for_display(&tile.button.display());
    let scale = tile.button.scale_factor();
    let p = match tile.gicon.borrow().as_ref() {
        Some(g) => theme.lookup_by_gicon(g, ICON_PX, scale, gtk::TextDirection::None, gtk::IconLookupFlags::empty()),
        None => theme.lookup_icon("application-x-executable", &[], ICON_PX, scale, gtk::TextDirection::None, gtk::IconLookupFlags::empty()),
    };
    Some(p.upcast())
}

#[derive(Default)]
struct HoverState {
    pop: RefCell<Option<Rc<PreviewPopover>>>,
    src: RefCell<Option<glib::SourceId>>,
    inside: Cell<bool>,
    refresh: RefCell<Option<glib::SourceId>>,
    wait: RefCell<Option<glib::SourceId>>,
}

impl HoverState {
    fn stop_refresh(&self) {
        if let Some(r) = self.refresh.borrow_mut().take() {
            r.remove();
        }
    }
}

/// Hovering `tile` shows its app's open windows (pictures, names).
pub fn attach(tile: &Rc<Tile>, dock: &Rc<Dock>) {
    let st: Rc<HoverState> = Rc::default();
    let motion = gtk::EventControllerMotion::new();
    let (s, t, d) = (st.clone(), Rc::downgrade(tile), dock.weak());
    // The pointer came onto the icon: the previews after HOVER_MS, if it stays.
    motion.connect_enter(move |_, _, _| {
        if let Some(src) = s.src.borrow_mut().take() {
            src.remove(); // (back from the previews: they stay)
        }
        if s.pop.borrow().is_some() || s.wait.borrow().is_some() {
            return;
        }
        let (s2, t, d) = (s.clone(), t.clone(), d.clone());
        let src = glib::timeout_add_local_once(Duration::from_millis(HOVER_MS), move || {
            s2.wait.replace(None);
            if let (Some(t), Some(d)) = (t.upgrade(), d.upgrade()) {
                enter(&s2, &t, &d);
            }
        });
        s.wait.replace(Some(src));
    });
    let s = st.clone();
    motion.connect_leave(move |_| {
        if let Some(w) = s.wait.borrow_mut().take() {
            w.remove();
        }
        close_later(&s);
    });
    tile.button.add_controller(motion);
}

fn close_later(s: &Rc<HoverState>) {
    if let Some(src) = s.src.borrow_mut().take() {
        src.remove();
    }
    let s2 = s.clone();
    let src = glib::timeout_add_local_once(Duration::from_millis(300), move || {
        s2.src.replace(None);
        if !s2.inside.get() {
            let pop = s2.pop.borrow_mut().take();
            if let Some(p) = pop {
                p.pop.popdown();
                s2.stop_refresh();
            }
        }
    });
    s.src.replace(Some(src));
}

fn refresh(s: &Rc<HoverState>) {
    let Some(p) = s.pop.borrow().clone() else { return };
    let shots = shots();
    for vid in p.ids() {
        let pw = Rc::downgrade(&p);
        shots.snapshot(vid, move |tex| {
            if let Some(p) = pw.upgrade() {
                p.set_shot(vid, Some(&tex), None);
            }
        });
    }
}

fn enter(s: &Rc<HoverState>, tile: &Rc<Tile>, dock: &Rc<Dock>) {
    let shots = shots();
    let Some(key) = tile.key() else { return };
    if !shots.enabled || !dock.windows.borrow().get(&key).is_some_and(|w| !w.is_empty()) {
        return; // (not running: the name label shows)
    }
    let found = shots.open_windows(&key);
    if found.is_empty() {
        return; // (a single window gets its preview too: Vini)
    }
    if let Some(src) = s.src.borrow_mut().take() {
        src.remove();
    }
    if s.pop.borrow().is_some() {
        return;
    }
    tile.label.popdown();
    let p = PreviewPopover::new(tile, dock.away, &found);
    gap_from_dock(&p.pop, tile, dock);
    let motion = gtk::EventControllerMotion::new();
    let s2 = s.clone();
    motion.connect_enter(move |_, _, _| s2.inside.set(true));
    let s2 = s.clone();
    motion.connect_leave(move |_| {
        s2.inside.set(false);
        close_later(&s2);
    });
    p.pop.add_controller(motion);
    // Closed by itself (a window picked, Esc, a click elsewhere): this
    // icon's previews show again (Vini: "sometimes the hover doesn't show")
    let (s2, pw) = (s.clone(), Rc::downgrade(&p));
    p.pop.connect_closed(move |_| {
        let mine = s2.pop.borrow().as_ref().is_some_and(|x| pw.upgrade().is_some_and(|p| Rc::ptr_eq(x, &p)));
        if mine {
            s2.pop.replace(None);
            s2.inside.set(false);
            s2.stop_refresh();
        }
    });
    s.pop.replace(Some(p.clone()));
    p.pop.popup();
    let t = Rc::downgrade(tile);
    glib::idle_add_local_once(move || {
        if let Some(t) = t.upgrade() {
            t.label.popdown(); // the name label yields
        }
    });
    refresh(s); // pictures now, then live
    let s2 = s.clone();
    let src = glib::timeout_add_local(Duration::from_millis(REFRESH_MS), move || {
        if s2.pop.borrow().is_none() {
            s2.refresh.replace(None);
            return glib::ControlFlow::Break;
        }
        refresh(&s2);
        glib::ControlFlow::Continue
    });
    s.refresh.replace(Some(src));
}

/// The previews stand off the Dock as far as a maximized window does (Vini:
/// they sat against the icon): edge_gap past the Dock's plate, not the icon.
pub fn gap_from_dock(pop: &gtk::Popover, tile: &Tile, dock: &Dock) {
    let Some(win) = tile.button.root() else { return };
    let gap = dock.cfg_int("edge_gap") as i32;
    let Some(pt) = tile.button.compute_point(&win, &graphene::Point::new(0.0, 0.0)) else { return };
    let (w, h) = (tile.button.width(), tile.button.height());
    let (px, py) = (pt.x().round() as i32, pt.y().round() as i32);
    let rect = match dock.edge.as_str() {
        // plate top, in the tile's own coordinates
        "bottom" => gdk::Rectangle::new(0, SHADOW as i32 - py - gap, w, 1),
        // plate's right side
        "left" => gdk::Rectangle::new(win.width() - SHADOW as i32 - px + gap - 1, 0, 1, h),
        // right: the plate's left side
        _ => gdk::Rectangle::new(SHADOW as i32 - px - gap, 0, 1, h),
    };
    pop.set_pointing_to(Some(&rect));
}

#[cfg(test)]
mod tests {
    use super::*;

    /// test_has_window_shot: an error about the request is still an answer.
    #[test]
    fn test_has_window_shot() {
        assert!(has_window_shot(&json!({"result": "ok"})));
        assert!(has_window_shot(&json!({"error": "no view with that id"})));
        assert!(!has_window_shot(&json!({"error": "No such method exists!"})));
        assert!(!has_window_shot(&json!({"error": "unknown method sonata/window-shot"})));
    }

    #[test]
    fn the_last_used_window_first() {
        let views = json!([
            {"id": 1, "app-id": "kitty", "title": "a", "type": "toplevel", "last-focus-timestamp": 10},
            {"id": 2, "app-id": "kitty", "title": "b", "type": "toplevel", "last-focus-timestamp": 30, "minimized": true},
            {"id": 3, "app-id": "kitty", "title": "c", "type": "toplevel", "mapped": false},
            {"id": 4, "app-id": "other", "title": "d", "type": "toplevel"}
        ]);
        let w = windows_of(&views, "kitty");
        assert_eq!(w.iter().map(|w| w.id).collect::<Vec<_>>(), [2, 1]);
        assert!(w[0].minimized);
    }
}
