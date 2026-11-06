//! App folders in the Dock (shell/dock_folder.py): like Launchpad's
//! folders, kept in the Dock.
//!
//! A folder is a pinned key "folder:<id>"; its name and apps live in the
//! Dock's config, cfg["folders"][id] = {"name": ..., "apps": [desktop ids]}
//! (the same shape as a Launchpad folder: {"folder": name, "apps": [...]}).
//! Its icon is a rounded plate with up to nine of its apps in a 3 x 3 grid.
//! Click: a panel with its apps opens from the icon (zoom + fade); click an
//! app to open it. An app's menu has "Add to New Folder" and "Move to
//! <folder>"; a folder's menu has "Ungroup" and "Remove from Dock". A
//! folder left with one app turns back into that app.
//!
//! Locked folders ("Lock Folder"): the icon shows blank tiles and a lock,
//! nothing of what's inside; opening it (or unlocking it, or ungrouping it)
//! asks for the login password every time. Apps can still be dropped in.
//!
//! Launchpad uses FolderIcon, LockBadge (lock_badge) and draw_lock_badge.

use crate::dock::{self, folder_apps, folder_locked, folder_name, Dock, Tile};
use crate::dock_menu;
use adw::prelude::*;
use gtk::{cairo, gdk, gio, glib, graphene, gsk, pango};
use serde_json::{Map, Value};
use sonata_i18n::tr;
use sonata_ui::menu::Item;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};
use std::time::Duration;

pub const PREFIX: &str = "folder:";
/// mini icons per row on the folder's icon
pub const GRID: usize = 3;
/// apps per row in the open folder
pub const PANEL_COLS: usize = 4;
pub const PANEL_ICON: i32 = 56;
pub const OPEN_MS: u64 = 220;
pub const CLOSE_MS: u64 = 140;
/// the app frame's sheen (icons.PLATE_SHEEN: top, bottom)
const PLATE_SHEEN: (&str, &str) = ("rgba(255,255,255,0.10)", "rgba(0,0,0,0.075)");

// -- the model -----------------------------------------------------------------------------------

pub fn is_folder(key: &str) -> bool {
    key.starts_with(PREFIX)
}

pub fn folder_id(key: &str) -> &str {
    key.strip_prefix(PREFIX).unwrap_or(key)
}

pub fn new_id(folders: &Map<String, Value>) -> String {
    let mut n = 1;
    while folders.contains_key(&n.to_string()) {
        n += 1;
    }
    n.to_string()
}

fn categories(key: &str) -> Vec<String> {
    let text = sonata_ui::apps::lookup(key).and_then(|i| i.categories()).map(|s| s.to_string()).unwrap_or_default();
    text.split(';').filter(|c| !c.is_empty()).map(String::from).collect()
}

/// Launchpad's rule: the apps' shared category, else the first's.
pub fn default_name(keys: &[String]) -> String {
    let a = keys.first().map(|k| categories(k)).unwrap_or_default();
    let b = keys.get(1).map(|k| categories(k)).unwrap_or_else(|| a.clone());
    sonata_desktop::launchpad_model::folder_name(&a, &b)
}

/// The same folder in Launchpad's shape.
pub fn as_launchpad(folder: &Map<String, Value>) -> Value {
    serde_json::json!({"folder": folder_name(folder), "apps": folder_apps(folder)})
}

/// Where the first n (<= 9) mini icons go on a folder icon of `size`:
/// [(x, y, side)], row by row, inside a padded 3 x 3 grid -- inside the
/// app-sized frame; a round one: inside the square that fits the circle.
pub fn mini_rects(size: f64, n: usize, shape: &str) -> Vec<(f64, f64, f64)> {
    let inset = sonata_ui::icons::PLATE_INSET;
    let extra = if shape != "circle" { 0.08 } else { (1.0 - 2.0 * inset) * (1.0 - 0.7071) / 2.0 + 0.02 };
    let pad = size * (inset + extra);
    let cell = (size - 2.0 * pad) / GRID as f64;
    let side = cell * 0.84;
    let off = (cell - side) / 2.0;
    (0..n.min(GRID * GRID)).map(|i| (pad + (i % GRID) as f64 * cell + off, pad + (i / GRID) as f64 * cell + off, side)).collect()
}

fn rgba(spec: &str) -> gdk::RGBA {
    gdk::RGBA::parse(spec).unwrap_or(gdk::RGBA::BLACK)
}

fn set_source(cr: &cairo::Context, c: &gdk::RGBA) {
    cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
}

// -- the icon ----------------------------------------------------------------------------------

mod imp {
    use super::*;
    use gtk::subclass::prelude::*;

    pub struct FolderIcon {
        pub keys: RefCell<Vec<String>>,
        pub gicons: RefCell<Vec<Option<gio::Icon>>>,
        pub size: Cell<i32>,
        pub locked: Cell<bool>,
        pub on_scrim: Cell<bool>,
        pub pop: Cell<f64>,
        pub paint: RefCell<HashMap<(usize, i32, bool), gdk::Paintable>>,
        pub nodes: RefCell<HashMap<(i32, bool, bool, Vec<String>, String), gsk::RenderNode>>,
    }

    impl Default for FolderIcon {
        fn default() -> Self {
            FolderIcon {
                keys: RefCell::default(),
                gicons: RefCell::default(),
                size: Cell::new(48),
                locked: Cell::new(false),
                on_scrim: Cell::new(false),
                pop: Cell::new(1.0),
                paint: RefCell::default(),
                nodes: RefCell::default(),
            }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for FolderIcon {
        const NAME: &'static str = "SonataDockFolderIcon";
        type Type = super::FolderIcon;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for FolderIcon {}

    impl WidgetImpl for FolderIcon {
        fn measure(&self, _o: gtk::Orientation, _for: i32) -> (i32, i32, i32, i32) {
            let s = self.size.get();
            (s, s, -1, -1)
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            self.obj().paint_cached(snap);
        }
    }
}

glib::wrapper! {
    /// A rounded translucent plate with the folder's first nine apps (sized
    /// like a DockIcon, so magnification and fitting work the same).
    pub struct FolderIcon(ObjectSubclass<imp::FolderIcon>) @extends gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

use gtk::subclass::prelude::ObjectSubclassIsExt;

impl FolderIcon {
    /// on_scrim: over Launchpad's dimmed desktop (light and dark alike).
    pub fn new(keys: &[String], size: i32, locked: bool, on_scrim: bool, css: &[&str]) -> Self {
        let mut classes = vec!["dock-icon".to_string(), "dock-folder-icon".to_string()];
        classes.extend(css.iter().map(|c| c.to_string()));
        let w: Self = glib::Object::builder().property("css-classes", classes).build();
        w.imp().size.set(size);
        w.imp().locked.set(locked);
        w.imp().on_scrim.set(on_scrim);
        w.set_apps(keys);
        w
    }

    pub fn keys(&self) -> Vec<String> {
        self.imp().keys.borrow().clone()
    }

    pub fn is_locked(&self) -> bool {
        self.imp().locked.get()
    }

    pub fn set_locked(&self, on: bool) {
        self.imp().locked.set(on);
        self.imp().nodes.borrow_mut().clear();
        self.queue_draw();
    }

    pub fn set_apps(&self, keys: &[String]) {
        self.imp().keys.replace(keys.to_vec());
        let gicons = keys.iter().take(GRID * GRID).map(|k| sonata_ui::apps::lookup(k).map(|i| sonata_ui::icons::app_icon(i.upcast_ref()))).collect();
        self.imp().gicons.replace(gicons);
        self.imp().paint.borrow_mut().clear();
        self.imp().nodes.borrow_mut().clear();
        self.queue_draw();
    }

    pub fn set_size(&self, size: f64) {
        let size = size.round() as i32;
        if size != self.imp().size.get() {
            self.imp().size.set(size);
            self.queue_resize();
        }
    }

    /// Drawn at this scale (popping out of an undone folder).
    pub fn set_pop(&self, v: f64) {
        self.imp().pop.set(v);
        self.queue_draw();
    }

    fn mini(&self, i: usize, gicon: &gio::Icon, px: i32) -> Option<gdk::Paintable> {
        let key = (i, px, sonata_ui::is_dark());
        if let Some(p) = self.imp().paint.borrow().get(&key) {
            return Some(p.clone());
        }
        let p = sonata_ui::icons::paintable(self, gicon, px)?;
        let mut cache = self.imp().paint.borrow_mut();
        if cache.len() > 40 {
            cache.clear();
        }
        cache.insert(key, p.clone());
        Some(p)
    }

    /// The icon is drawn once per 8 px size step and kept as a render
    /// node; magnification (a new size every frame) only scales that node.
    fn paint_cached(&self, snap: &gtk::Snapshot) {
        let s = self.imp().size.get();
        if s <= 0 {
            return;
        }
        let b = ((s + 7) / 8 * 8).max(8);
        let key = (b, sonata_ui::is_dark(), self.imp().locked.get(), self.keys(), sonata_ui::icons::frame_shape());
        let cached = self.imp().nodes.borrow().get(&key).cloned();
        let node = match cached {
            Some(n) => n,
            None => {
                let sub = gtk::Snapshot::new();
                self.draw(&sub, b as f64);
                let Some(n) = sub.to_node() else { return };
                let mut nodes = self.imp().nodes.borrow_mut();
                if nodes.len() > 6 {
                    nodes.clear();
                }
                nodes.insert(key, n.clone());
                n
            }
        };
        snap.save();
        let pop = self.imp().pop.get();
        if pop != 1.0 {
            let c = s as f32 / 2.0;
            snap.translate(&graphene::Point::new(c, c));
            snap.scale(pop as f32, pop as f32);
            snap.translate(&graphene::Point::new(-c, -c));
        }
        if b != s {
            snap.scale(s as f32 / b as f32, s as f32 / b as f32);
        }
        snap.append_node(&node);
        snap.restore();
    }

    /// The icon as a picture (the drag icon: a folder has no gicon, and
    /// without one GTK showed the drag's text by the pointer).
    pub fn texture(&self, size: i32) -> Option<gdk::Texture> {
        let renderer = self.native()?.renderer()?;
        let snap = gtk::Snapshot::new();
        self.draw(&snap, size as f64);
        let node = snap.to_node()?;
        Some(renderer.render_texture(&node, Some(&graphene::Rect::new(0.0, 0.0, size as f32, size as f32))))
    }

    /// Exactly an app's frame (icons' plate): the same shape, inset,
    /// shadow, sheen and edge -- only see-through (Vini: they must match).
    fn draw(&self, snap: &gtk::Snapshot, s: f64) {
        let inset = s * sonata_ui::icons::PLATE_INSET;
        let p = s - 2.0 * inset;
        let shape = sonata_ui::icons::frame_shape(); // Settings > App Icons: folders follow apps
        let dark = sonata_ui::is_dark() || self.imp().on_scrim.get();
        let rr = gsk::RoundedRect::from_rect(graphene::Rect::new(inset as f32, inset as f32, p as f32, p as f32), (p * if shape == "circle" { 0.5 } else { 0.3 }) as f32);
        snap.append_outset_shadow(&rr, &rgba("rgba(0,0,0,0.22)"), 0.0, (s * 0.012) as f32, 0.0, (s * 0.02) as f32);
        let fill = if self.imp().on_scrim.get() {
            "rgba(255,255,255,0.22)" // Launchpad: its tile_on_scrim, a bit stronger
        } else if dark {
            "rgba(120,120,128,0.55)"
        } else {
            "rgba(255,255,255,0.62)"
        };
        let cr = snap.append_cairo(&graphene::Rect::new(0.0, 0.0, s as f32, s as f32));
        sonata_ui::icons::shape_path(&cr, &shape, inset, inset, p, p);
        set_source(&cr, &rgba(fill));
        let _ = cr.fill_preserve();
        let grad = cairo::LinearGradient::new(0.0, inset, 0.0, s - inset);
        for (off, spec) in [(0.0, PLATE_SHEEN.0), (1.0, PLATE_SHEEN.1)] {
            let c = rgba(spec);
            grad.add_color_stop_rgba(off, c.red() as f64, c.green() as f64, c.blue() as f64, c.alpha() as f64);
        }
        let _ = cr.set_source(&grad);
        let _ = cr.fill_preserve();
        cr.set_line_width((s / 256.0).max(0.5));
        set_source(&cr, &rgba("rgba(0,0,0,0.10)"));
        let _ = cr.stroke();
        let n = self.imp().keys.borrow().len();
        if self.imp().locked.get() {
            draw_locked(&cr, s, dark, n, &shape);
            return;
        }
        drop(cr);
        let gicons = self.imp().gicons.borrow().clone();
        for (i, ((x, y, side), gicon)) in mini_rects(s, gicons.len(), &shape).into_iter().zip(gicons.iter()).enumerate() {
            let Some(gicon) = gicon else { continue };
            let px = (side.round() as i32).max(8);
            if let Some(pic) = self.mini(i, gicon, px) {
                snap.save();
                snap.translate(&graphene::Point::new(x as f32, y as f32));
                pic.snapshot(snap, side, side);
                snap.restore();
            }
        }
    }
}

/// Blank tiles (how many apps, not which) and a padlock over them.
fn draw_locked(cr: &cairo::Context, s: f64, dark: bool, n: usize, shape: &str) {
    set_source(cr, &rgba(if dark { "rgba(255,255,255,0.16)" } else { "rgba(0,0,0,0.08)" }));
    for (x, y, side) in mini_rects(s, n, shape) {
        rounded_path(cr, x, y, side, side, side * 0.225);
        let _ = cr.fill();
    }
    draw_padlock(cr, s, &rgba(if dark { "rgba(255,255,255,0.92)" } else { "rgba(40,40,46,0.85)" }), s * 0.055);
}

fn rounded_path(cr: &cairo::Context, x: f64, y: f64, w: f64, h: f64, r: f64) {
    use std::f64::consts::{FRAC_PI_2, PI};
    cr.new_sub_path();
    cr.arc(x + w - r, y + r, r, -FRAC_PI_2, 0.0);
    cr.arc(x + w - r, y + h - r, r, 0.0, FRAC_PI_2);
    cr.arc(x + r, y + h - r, r, FRAC_PI_2, PI);
    cr.arc(x + r, y + r, r, PI, 1.5 * PI);
    cr.close_path();
}

/// A padlock centred on an s x s icon: the body (filled) and the shackle
/// (stroked `line` wide).
pub fn draw_padlock(cr: &cairo::Context, s: f64, ink: &gdk::RGBA, line: f64) {
    let (bw, bh) = (s * 0.34, s * 0.26);
    let (bx, by) = ((s - bw) / 2.0, s * 0.47);
    set_source(cr, ink);
    rounded_path(cr, bx, by, bw, bh, s * 0.05);
    let _ = cr.fill();
    let r = bw * 0.30;
    let (cx, top) = (s / 2.0, by - r * 1.15);
    cr.new_path();
    cr.move_to(cx - r, by);
    cr.line_to(cx - r, top + r);
    cr.arc(cx, top + r, r, std::f64::consts::PI, 2.0 * std::f64::consts::PI);
    cr.line_to(cx + r, by);
    cr.set_line_width(line);
    let _ = cr.stroke();
}

/// A locked app (applock.py): a small dark disc with a padlock at the
/// icon's bottom right (s: the icon's size). The Dock and Launchpad.
pub fn draw_lock_badge(snap: &gtk::Snapshot, s: f64) {
    let d = s * 0.38;
    let (x, y) = (s - d * 0.9, s - d * 0.9);
    let rr = gsk::RoundedRect::from_rect(graphene::Rect::new(x as f32, y as f32, d as f32, d as f32), (d / 2.0) as f32);
    snap.append_outset_shadow(&rr, &rgba("rgba(0,0,0,0.30)"), 0.0, 1.0, 0.0, 2.0);
    snap.push_rounded_clip(&rr);
    snap.append_color(&rgba("rgba(36,36,40,0.94)"), &graphene::Rect::new(x as f32, y as f32, d as f32, d as f32));
    snap.pop();
    let w = (d * 0.04) as f32;
    let edge = rgba("rgba(255,255,255,0.22)");
    snap.append_border(&rr, &[w; 4], &[edge; 4]);
    let cr = snap.append_cairo(&graphene::Rect::new(x as f32, y as f32, d as f32, (d * 1.04) as f32));
    cr.translate(x, y + d * 0.04);
    draw_padlock(&cr, d, &rgba("rgba(255,255,255,0.95)"), d * 0.075);
}

/// draw_lock_badge over an icon of `size` px (an overlay; never takes clicks).
pub fn lock_badge(size: i32) -> gtk::DrawingArea {
    let area = gtk::DrawingArea::builder().can_target(false).halign(gtk::Align::Center).valign(gtk::Align::Start).content_width(size).content_height(size).build();
    area.set_draw_func(move |_, cr, _, _| {
        // (the same drawing as draw_lock_badge, on cairo)
        let s = size as f64;
        let d = s * 0.38;
        let (x, y) = (s - d * 0.9, s - d * 0.9);
        cr.arc(x + d / 2.0, y + d / 2.0, d / 2.0, 0.0, std::f64::consts::TAU);
        set_source(cr, &rgba("rgba(36,36,40,0.94)"));
        let _ = cr.fill_preserve();
        cr.set_line_width(d * 0.04);
        set_source(cr, &rgba("rgba(255,255,255,0.22)"));
        let _ = cr.stroke();
        cr.translate(x, y + d * 0.04);
        draw_padlock(cr, d, &rgba("rgba(255,255,255,0.95)"), d * 0.075);
    });
    area
}

// -- the open folder -------------------------------------------------------------------------------

/// The folder's panel over its icon.
pub struct Panel {
    pub pop: gtk::Popover,
    view: gtk::Box,
    flow: RefCell<Option<gtk::FlowBox>>,
    sig: (String, Vec<String>),
    title: gtk::Label,
    editable: Cell<bool>,
    rename: Cell<bool>,
    dock: Weak<Dock>,
    tile: Weak<Tile>,
    dots: RefCell<Vec<(gtk::Box, String)>>,
    dragging_out: Cell<bool>,
}

fn signature(f: &Map<String, Value>) -> (String, Vec<String>) {
    (folder_name(f), folder_apps(f))
}

/// The folder's apps in a panel over its icon, zooming in from it (a
/// locked folder asks for the password first -- ask_password; `then`: ask,
/// then run it instead of showing the apps). An unlocked folder's panel is
/// built once and opened again as it is while its apps don't change.
/// None while the password is asked.
pub fn open_panel(dock: &Dock, tile: &Rc<Tile>, then: Option<Rc<dyn Fn()>>, rename: bool, passed: bool) -> Option<Rc<Panel>> {
    tile.label.popdown();
    let key = tile.key()?;
    let folder = dock.folder(&key)?;
    if (folder_locked(&folder) || then.is_some()) && !passed {
        ask_password(dock, tile, then, rename);
        return None;
    }
    let reuse = then.is_none() && !folder_locked(&folder);
    let cached = tile.folder_pop.borrow_mut().take();
    if let Some(cached) = cached {
        if reuse && cached.sig == signature(&folder) && !cached.pop.get_visible() {
            replay(&cached);
            if rename {
                edit_title(&cached);
            }
            tile.folder_pop.replace(Some(cached.clone()));
            return Some(cached);
        }
        if !cached.pop.get_visible() {
            cached.pop.unparent();
        }
    }
    let pop = gtk::Popover::builder().css_classes(["dock-folder-panel"]).has_arrow(false).position(dock.away).build();
    let (ox, oy) = match dock.away {
        gtk::PositionType::Left => (-8, 0),
        gtk::PositionType::Right => (8, 0),
        _ => (0, -8),
    };
    pop.set_offset(ox, oy);
    let view = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["dock-folder-view"]).build();
    // the name, centred: click it to rename (Vini) -- once the apps show.
    // Typed in an alert of its own: the Dock's panel never got the keys
    let title = gtk::Label::builder()
        .label(folder_name(&folder))
        .css_classes(["dock-folder-title"])
        .halign(gtk::Align::Center)
        .xalign(0.5)
        .justify(gtk::Justification::Center)
        .max_width_chars(28)
        .ellipsize(pango::EllipsizeMode::End)
        .tooltip_text(tr!("Rename"))
        .build();
    view.append(&title);
    pop.set_child(Some(&view));
    pop.set_parent(&tile.button);
    let panel = Rc::new(Panel {
        pop: pop.clone(),
        view,
        flow: RefCell::default(),
        sig: signature(&folder),
        title: title.clone(),
        editable: Cell::new(false),
        rename: Cell::new(rename),
        dock: dock.weak(),
        tile: Rc::downgrade(tile),
        dots: RefCell::default(),
        dragging_out: Cell::new(false),
    });
    let click = gtk::GestureClick::new();
    let p = Rc::downgrade(&panel);
    click.connect_released(move |_, _, _, _| {
        if let Some(p) = p.upgrade() {
            if p.editable.get() {
                if let (Some(d), Some(t)) = (p.dock.upgrade(), p.tile.upgrade()) {
                    ask_rename(&d, &t, Some(&p));
                }
            }
        }
    });
    title.add_controller(click);
    if reuse {
        tile.folder_pop.replace(Some(panel.clone())); // kept (unparented with the tile)
    } else {
        // a locked folder's, after its password: never kept
        pop.connect_closed(|p| {
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        });
    }
    dock::menu_opened(&pop); // keeps an auto-hiding Dock visible
    // the keyboard while it's open (the Dock takes none): Esc closes it
    let t = Rc::downgrade(tile);
    pop.connect_closed(move |_| {
        if let Some(t) = t.upgrade() {
            keyboard(&t, false);
        }
    });
    apps_view(dock, tile, &panel);
    keyboard(tile, true);
    pop.popup();
    Some(panel)
}

fn keyboard(tile: &Tile, on: bool) {
    if let Some(win) = tile.button.root().and_downcast::<gtk::Window>() {
        sonata_shell::layer::set_keyboard(&win, on);
    }
}

/// Open a kept panel with its zoom-in again (the class comes back on the
/// next frame, so the animation starts over; hidden until then).
fn replay(p: &Rc<Panel>) {
    if let Some(t) = p.tile.upgrade() {
        keyboard(&t, true);
    }
    for c in ["closing", "dock-folder-view"] {
        p.view.remove_css_class(c);
    }
    p.view.set_opacity(0.0);
    dock::menu_opened(&p.pop);
    p.pop.popup();
    let v = p.view.clone();
    glib::idle_add_local_once(move || {
        v.set_opacity(1.0);
        v.add_css_class("dock-folder-view");
    });
}

/// Password first, in the sheet every login-password prompt shares (a
/// window of its own: in the Dock's panel the field could be clicked but
/// never typed in -- Vini); right: the apps (or then(), e.g. unlock / ungroup).
pub fn ask_password(dock: &Dock, tile: &Rc<Tile>, then: Option<Rc<dyn Fn()>>, rename: bool) {
    let Some(folder) = tile.key().and_then(|k| dock.folder(&k)) else { return };
    let title = sonata_i18n::fmt(&tr!("“{name}” is locked"), &[("name", &folder_name(&folder))]);
    let text = if then.is_none() { tr!("Enter your password to open this folder") } else { tr!("Enter your password to change this folder") };
    let ok = if then.is_none() { tr!("Open") } else { tr!("OK") };
    let (w, t) = (dock.weak(), Rc::downgrade(tile));
    let app = dock.app.borrow().clone();
    sonata_shell::authsheet::ask(&title, &text, Some(&ok), app.as_ref(), None, move |_pw| {
        let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) else { return };
        if t.key().and_then(|k| d.folder(&k)).is_none() {
            return; // gone meanwhile
        }
        match &then {
            Some(f) => f(),
            None => {
                open_panel(&d, &t, None, rename, true);
            }
        }
    }, || {});
}

fn apps_view(dock: &Dock, tile: &Rc<Tile>, p: &Rc<Panel>) {
    let Some(folder) = tile.key().and_then(|k| dock.folder(&k)) else { return };
    let keys: Vec<String> = folder_apps(&folder).into_iter().filter(|k| sonata_ui::apps::lookup(k).is_some()).collect();
    let cols = keys.len().clamp(1, PANEL_COLS) as u32;
    let flow = gtk::FlowBox::builder().max_children_per_line(cols).min_children_per_line(cols).selection_mode(gtk::SelectionMode::None).homogeneous(true).column_spacing(2).row_spacing(2).build();
    for k in &keys {
        flow.append(&app_button(dock, tile, k, p));
    }
    let rows = keys.len().div_ceil(cols as usize) as i32;
    let scroll = gtk::ScrolledWindow::builder()
        .hscrollbar_policy(gtk::PolicyType::Never)
        .propagate_natural_width(true)
        .propagate_natural_height(rows <= 4)
        .min_content_height(rows.min(4) * 100)
        .max_content_height(4 * 112)
        .child(&flow)
        .build();
    p.view.append(&scroll);
    p.flow.replace(Some(flow));
    p.editable.set(true);
    p.title.add_css_class("editable");
    if p.rename.replace(false) {
        edit_title(p);
    }
}

/// "Rename…" for a locked folder: asked once its apps show.
fn edit_title(p: &Rc<Panel>) {
    let p = Rc::downgrade(p);
    glib::idle_add_local_once(move || {
        let Some(p) = p.upgrade() else { return };
        if p.pop.get_visible() && p.editable.get() {
            if let (Some(d), Some(t)) = (p.dock.upgrade(), p.tile.upgrade()) {
                ask_rename(&d, &t, Some(&p));
            }
        }
    });
}

/// The folder's new name (ui.dialog.ask_text); the panel closes first.
pub fn ask_rename(dock: &Dock, tile: &Rc<Tile>, p: Option<&Rc<Panel>>) {
    let Some(key) = tile.key() else { return };
    let Some(f) = dock.folder(&key) else { return };
    let w = dock.weak();
    let ask = move || {
        let (w, key) = (w.clone(), key.clone());
        sonata_ui::dialog::ask_text(&tr!("Rename Folder"), &folder_name(&f), &tr!("Rename"), move |name| {
            if let Some(d) = w.upgrade() {
                d.rename_folder(&key, &name);
            }
        }, "", None);
    };
    match p.filter(|p| p.pop.get_visible()) {
        Some(p) => close_panel(p, Some(Box::new(ask))),
        None => ask(),
    }
}

/// Fade the panel out, then close it (then(): after it's gone).
pub fn close_panel(p: &Rc<Panel>, then: Option<Box<dyn Fn()>>) {
    if p.view.has_css_class("closing") {
        return;
    }
    p.view.add_css_class("closing");
    let pop = p.pop.clone();
    glib::timeout_add_local_once(Duration::from_millis(CLOSE_MS), move || {
        pop.popdown();
        if let Some(f) = then {
            f();
        }
    });
}

fn app_button(dock: &Dock, tile: &Rc<Tile>, key: &str, p: &Rc<Panel>) -> gtk::Button {
    let info = sonata_ui::apps::lookup(key).expect("listed apps exist");
    let b = gtk::Button::builder().css_classes(["dock-folder-app"]).build();
    let col = gtk::Box::new(gtk::Orientation::Vertical, 4);
    let img = gtk::Image::builder().pixel_size(PANEL_ICON).build();
    sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(info.upcast_ref()));
    col.append(&img);
    col.append(&gtk::Label::builder().label(sonata_ui::apps::display_name(info)).wrap(true).lines(2).justify(gtk::Justification::Center).ellipsize(pango::EllipsizeMode::End).max_width_chars(12).width_chars(12).build());
    // open: the Dock's running dot under its name (Vini: the app stays in its folder)
    let dot = gtk::Box::builder().css_classes(["dock-dot", "on"]).halign(gtk::Align::Center).visible(dock.windows.borrow().get(key).is_some_and(|w| !w.is_empty())).build();
    col.append(&dot);
    p.dots.borrow_mut().push((dot, key.to_string()));
    b.set_child(Some(&col));
    let open = {
        let (w, t, pw, key) = (dock.weak(), Rc::downgrade(tile), Rc::downgrade(p), key.to_string());
        move || {
            let (Some(p), w, t, key) = (pw.upgrade(), w.clone(), t.clone(), key.clone()) else { return };
            close_panel(&p, Some(Box::new(move || {
                if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                    d.open_app(&key, Some(&t));
                }
            })));
        }
    };
    let open = Rc::new(open);
    let o = open.clone();
    b.connect_clicked(move |_| o());
    let menu = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
    let (w, t, pw, k) = (dock.weak(), Rc::downgrade(tile), Rc::downgrade(p), key.to_string());
    menu.connect_pressed(move |g, _, x, y| {
        let Some(widget) = g.widget() else { return };
        let o = open.clone();
        let (w, t, pw, k) = (w.clone(), t.clone(), pw.clone(), k.clone());
        dock_menu::popup_at(&widget, vec![vec![
            Item::new(&tr!("Open"), move || o()),
            Item::new(&tr!("Remove from Folder"), move || {
                if let Some(p) = pw.upgrade() {
                    p.pop.popdown();
                }
                if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                    if let Some(fk) = t.key() {
                        d.remove_from_folder(&fk, &k, true);
                    }
                }
            }),
        ]], (x, y));
    });
    b.add_controller(menu);
    // dragged out of the panel: out of the folder, into the Dock (Vini)
    let src = gtk::DragSource::new();
    src.set_actions(gdk::DragAction::MOVE);
    let k = key.to_string();
    src.connect_prepare(move |_, _, _| Some(gdk::ContentProvider::for_value(&k.to_value())));
    let (w, t, pw, k) = (dock.weak(), Rc::downgrade(tile), Rc::downgrade(p), key.to_string());
    src.connect_drag_begin(move |_, drag| {
        if let (Some(d), Some(t), Some(p)) = (w.upgrade(), t.upgrade(), pw.upgrade()) {
            drag_out(&d, &t, &k, &p, drag);
        }
    });
    src.connect_drag_cancel(|_, _, _| true); // no snap back into a closed panel
    let (w, t, pw, k) = (dock.weak(), Rc::downgrade(tile), Rc::downgrade(p), key.to_string());
    src.connect_drag_end(move |_, _, deleted| {
        if crate::dock::debug_on() {
            eprintln!("dock: panel drag end (deleted {deleted}): dock {}, folder tile {}, panel {}", w.upgrade().is_some(), t.upgrade().is_some(), pw.upgrade().is_some());
        }
        if let (Some(d), Some(t), Some(p)) = (w.upgrade(), t.upgrade(), pw.upgrade()) {
            if p.dragging_out.get() {
                hold_open(&p, false); // closed now, the drag over
            }
            if let Some(fk) = t.key() {
                d.folder_app_drag_end(&fk, &k);
            }
        }
    });
    b.add_controller(src);
    b
}

/// The panel's dots follow the apps' windows.
pub fn show_running(dock: &Dock, p: &Panel) {
    let w = dock.windows.borrow();
    for (dot, key) in p.dots.borrow().iter() {
        dot.set_visible(w.get(key).is_some_and(|x| !x.is_empty()));
    }
}

/// The panel holds the drag's source: closing it now ended the drag at
/// once on Wayland -- its surface went, so the drag did (Vini). It only goes
/// invisible and lets the pointer through until the drag ends.
fn drag_out(dock: &Dock, tile: &Rc<Tile>, key: &str, p: &Rc<Panel>, drag: &gdk::Drag) {
    hold_open(p, true);
    let fk = tile.key().unwrap_or_default();
    if !dock.folder_app_drag_begin(&fk, key, drag) {
        hold_open(p, false);
        drag.drop_done(false);
    }
}

/// While an app is dragged out of it: invisible, clicks and drags through
/// -- its surface takes no pointer at all (an empty input region).
fn hold_open(p: &Panel, on: bool) {
    p.dragging_out.set(on);
    p.pop.set_opacity(if on { 0.0 } else { 1.0 });
    if let Some(c) = p.pop.child() {
        c.set_can_target(!on);
    }
    if let Some(surface) = p.pop.native().and_then(|n| n.surface()) {
        let region = if on { cairo::Region::create() } else { cairo::Region::create_rectangle(&cairo::RectangleInt::new(0, 0, 1 << 15, 1 << 15)) };
        surface.set_input_region(&region);
    }
    if !on {
        p.pop.popdown();
    }
}

// -- menus -------------------------------------------------------------------------------------

pub fn folder_menu(dock: &Dock, tile: &Rc<Tile>) {
    tile.label.popdown();
    let Some(key) = tile.key() else { return };
    let locked = dock.folder(&key).is_some_and(|f| folder_locked(&f));
    let (w, t) = (dock.weak(), Rc::downgrade(tile));
    let act = move |f: Rc<dyn Fn(&Rc<Dock>, &Rc<Tile>)>| {
        let (w, t) = (w.clone(), t.clone());
        move || {
            if let (Some(d), Some(t)) = (w.upgrade(), t.upgrade()) {
                f(&d, &t);
            }
        }
    };
    let k = key.clone();
    let (lock, ungroup) = if locked {
        let k2 = k.clone();
        let k3 = k.clone();
        (
            Item::new(&tr!("Unlock Folder\u{2026}"), act(Rc::new(move |d, t| {
                let (w, k) = (d.weak(), k2.clone());
                open_panel(d, t, Some(Rc::new(move || {
                    if let Some(d) = w.upgrade() {
                        d.set_folder_locked(&k, false);
                    }
                })), false, false);
            }))),
            Item::new(&tr!("Ungroup\u{2026}"), act(Rc::new(move |d, t| {
                let (w, k) = (d.weak(), k3.clone());
                open_panel(d, t, Some(Rc::new(move || {
                    if let Some(d) = w.upgrade() {
                        d.ungroup(&k);
                    }
                })), false, false);
            }))),
        )
    } else {
        let k2 = k.clone();
        let k3 = k.clone();
        (
            Item::new(&tr!("Lock Folder"), act(Rc::new(move |d, _| d.set_folder_locked(&k2, true)))),
            Item::new(&tr!("Ungroup"), act(Rc::new(move |d, _| d.ungroup(&k3)))),
        )
    };
    let k2 = k.clone();
    dock_menu::popup(&tile.button, dock, vec![
        vec![
            Item::new(&tr!("Open"), act(Rc::new(|d, t| {
                open_panel(d, t, None, false, false);
            }))),
            Item::new(&tr!("Rename\u{2026}"), act(Rc::new(move |d, t| {
                if d.folder(&k2).is_some_and(|f| folder_locked(&f)) {
                    open_panel(d, t, None, true, false);
                } else {
                    ask_rename(d, t, None);
                }
            }))),
        ],
        vec![lock],
        vec![ungroup, Item::new(&tr!("Remove from Dock"), act(Rc::new(|d, t| remove_with_puff(d, t))))],
    ]);
}

/// Remove from Dock, in a puff of smoke like an app dragged out (Vini).
pub fn remove_with_puff(dock: &Dock, tile: &Rc<Tile>) {
    dock.poof_at_tile(tile);
    if let Some(k) = tile.key() {
        dock.set_pinned(&k, false);
    }
}

/// For an app's Dock menu: [Add to New Folder, Move to <folder>...]
/// (pinned, movable apps only).
pub fn app_items(dock: &Dock, key: &str) -> Vec<Item> {
    if is_folder(key) || dock::PERMANENT.contains(&key) || !dock.pinned().iter().any(|k| k == key) || sonata_ui::apps::lookup(key).is_none() {
        return Vec::new();
    }
    let (w, k) = (dock.weak(), key.to_string());
    let mut items = vec![Item::new(&tr!("Add to New Folder"), move || {
        if let Some(d) = w.upgrade() {
            d.make_folder(std::slice::from_ref(&k), None, None);
        }
    })];
    for fkey in dock.pinned().into_iter().filter(|k| is_folder(k)) {
        let Some(f) = dock.folder(&fkey) else { continue };
        let (w, k) = (dock.weak(), key.to_string());
        items.push(Item::new(&sonata_i18n::fmt(&tr!("Move to “{name}”"), &[("name", &folder_name(&f))]), move || {
            if let Some(d) = w.upgrade() {
                d.add_to_folder(&fkey, &k);
            }
        }));
    }
    items
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn folder_keys_and_ids() {
        assert!(is_folder("folder:3"));
        assert!(!is_folder("firefox"));
        assert_eq!(folder_id("folder:12"), "12");
        let m = json!({"1": {}, "2": {}, "4": {}});
        assert_eq!(new_id(m.as_object().unwrap()), "3");
        assert_eq!(new_id(&Map::new()), "1");
    }

    /// test_mini_rects: nine minis at most, in rows of three, inside the frame.
    #[test]
    fn test_mini_rects() {
        let r = mini_rects(96.0, 12, "squircle");
        assert_eq!(r.len(), 9);
        assert!(r[1].0 > r[0].0 && (r[1].1 - r[0].1).abs() < 1e-9); // same row
        assert!(r[3].1 > r[0].1 && (r[3].0 - r[0].0).abs() < 1e-9); // next row
        for (x, y, side) in &r {
            assert!(*x > 0.0 && *y > 0.0 && x + side < 96.0 && y + side < 96.0);
        }
        // a round frame keeps them inside its circle: smaller
        let round = mini_rects(96.0, 1, "circle");
        assert!(round[0].2 < r[0].2);
        assert_eq!(mini_rects(96.0, 2, "squircle").len(), 2);
    }

    #[test]
    fn launchpads_shape() {
        let f = json!({"name": "Games", "apps": ["a", "b"], "locked": true});
        assert_eq!(as_launchpad(f.as_object().unwrap()), json!({"folder": "Games", "apps": ["a", "b"]}));
    }
}
