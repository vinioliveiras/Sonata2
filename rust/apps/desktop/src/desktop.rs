//! The desktop (macOS Big Sur): the files of ~/Desktop as icons on the
//! wallpaper, in a grid that starts at the top right and fills columns
//! downwards (grid.rs). Drag icons anywhere (they snap to the grid and stay
//! there), drop files from Files or other apps to move them to the Desktop,
//! drag icons out to Files, the Dock or the Trash. Double-click opens,
//! right-click menus like Finder's, rubber-band selection, Delete / Return
//! / Space / Ctrl+A / Ctrl+C / Ctrl+V. Reuses Files' folder model, icons,
//! thumbnails and operations.
//!
//! Every display has a desktop (macOS): icons start on the main one; one
//! dragged onto another display, a folder made or a file dropped there,
//! stays there. With that display unplugged it shows on the main one.

use crate::grid::{self, Cell as Spot, Grid, CELL_W, ICON};
use gtk::prelude::*;
use gtk::{gdk, gio, glib};
use serde_json::{json, Map, Value};
use sonata_files::{folder, ops, quicklook};
use sonata_i18n::{fmt, tr, trn};
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::rc::{Rc, Weak};

/// ~/Desktop (the XDG desktop folder); created when missing, like macOS.
/// Sonata's debug logging on (logs.verbose: SONATA_DEBUG=1 or the
/// debug-logging flag): what the desktop sees goes to wallpaper.log.
fn verbose() -> bool {
    std::env::var("SONATA_DEBUG").as_deref() == Ok("1") || glib::user_config_dir().join("sonata2").join("debug-logging").exists()
}

pub fn desktop_dir() -> gio::File {
    let home = glib::home_dir();
    let real = |p: &std::path::Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let p = glib::user_special_dir(glib::UserDirectory::Desktop).filter(|p| real(p) != real(&home)).unwrap_or_else(|| home.join("Desktop"));
    let _ = std::fs::create_dir_all(&p);
    gio::File::for_path(p)
}

/// Connectors of the displays plugged in now.
fn connected() -> HashSet<String> {
    sonata_shell::layer::monitors().iter().map(sonata_shell::layer::connector).collect()
}

/// A picture a wallpaper can be made of (prefs.is_picture).
fn is_picture(ct: &str) -> bool {
    ct.starts_with("image/") && !ct.contains("svg") && !ct.contains("icon")
}

/// Files' window keeps its own; both on one disk: a drop moves.
fn same_disk(a: &gio::File, b: &gio::File) -> bool {
    let fs = |f: &gio::File| f.query_info("id::filesystem", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok().and_then(|i| i.attribute_string("id::filesystem")).map(|s| s.to_string());
    matches!((fs(a), fs(b)), (Some(x), Some(y)) if !x.is_empty() && x == y)
}

/// The app of a .desktop shortcut on the Desktop.
fn launcher(info: &gio::FileInfo) -> Option<gio::DesktopAppInfo> {
    if !info.name().to_string_lossy().ends_with(".desktop") {
        return None;
    }
    folder::file_of(info).and_then(|f| f.path()).and_then(gio::DesktopAppInfo::from_filename)
}

/// A shortcut (a link) whose original is gone.
fn broken(info: &gio::FileInfo, f: &gio::File) -> bool {
    info.has_attribute("standard::is-symlink") && info.is_symlink() && f.path().is_some_and(|p| !p.exists())
}

fn copy_key(t: &impl IsA<gtk::EventController>) -> bool {
    t.current_event_state().contains(gdk::ModifierType::CONTROL_MASK)
}

/// Sonata Settings at a page (topbar.open_settings: the Python Settings).
fn open_settings(page: &str) {
    let launcher = std::env::var("SONATA2_LAUNCHER").unwrap_or_else(|_| "sonata2".into());
    if let Ok(c) = std::process::Command::new(launcher).args(["settings", "--page", page]).spawn() {
        sonata_backend::run::reap(c); // (else a zombie once Settings quits)
    }
}

// -- the icon layer: a gtk::Fixed whose icons glide to a new spot ----------------------------

mod glide_imp {
    use gtk::subclass::prelude::*;

    #[derive(Default)]
    pub struct GlideFixed;

    #[glib::object_subclass]
    impl ObjectSubclass for GlideFixed {
        const NAME: &'static str = "SonataDesktopFixed";
        type Type = super::GlideFixed;
        type ParentType = gtk::Fixed;
    }

    impl ObjectImpl for GlideFixed {}
    impl FixedImpl for GlideFixed {}

    impl WidgetImpl for GlideFixed {
        // each icon drawn at its glide's offset (ui.transition.snapshot_children)
        fn snapshot(&self, snapshot: &gtk::Snapshot) {
            sonata_ui::transition::snapshot_children(&*self.obj(), snapshot);
        }
    }
}

glib::wrapper! {
    /// desktop.py's Desktop draws its children with ui.transition: icons
    /// that already had a spot slide to their new one (Clean Up, Sort By, a
    /// file gone, the Dock moved) instead of jumping there.
    pub struct GlideFixed(ObjectSubclass<glide_imp::GlideFixed>) @extends gtk::Fixed, gtk::Widget;
}

fn icon_layer() -> gtk::Fixed {
    glib::Object::builder::<GlideFixed>().property("focusable", true).property("hexpand", true).property("vexpand", true).build().upcast()
}

fn alert(heading: &str, body: &str) {
    sonata_ui::dialog::alert(heading, body, &[("ok", tr!("OK"), "default")], |_| {}, None);
}

pub struct Item {
    pub widget: gtk::Box,
    img: gtk::Image,
    lbl: gtk::Label,
    over: gtk::Overlay,
    pub info: RefCell<gio::FileInfo>,
    app: Option<gio::DesktopAppInfo>,
    /// drawn at its spot already
    laid: Cell<bool>,
}

impl Item {
    pub fn name(&self) -> String {
        self.info.borrow().name().to_string_lossy().into_owned()
    }

    fn file(&self) -> Option<gio::File> {
        folder::file_of(&self.info.borrow())
    }
}

/// The icon layer over the wallpaper.
pub struct Desktop {
    pub fixed: gtk::Fixed,
    screen: String,
    main: bool,
    dir: gio::File,
    cfg: RefCell<Map<String, Value>>,
    items: RefCell<HashMap<String, Rc<Item>>>,
    selection: RefCell<Vec<Rc<Item>>>,
    drag_names: RefCell<Option<Vec<String>>>,
    drag_from: Cell<Option<Spot>>,
    placed: RefCell<HashMap<String, Spot>>,
    size: Cell<(i32, i32)>,
    margins: Cell<(i32, i32, i32)>,
    top: Cell<i32>,
    folder: Rc<folder::Folder>,
    band: gtk::Box,
    band_origin: Cell<(f64, f64)>,
    mons: RefCell<Vec<gio::FileMonitor>>,
    released: Cell<bool>,
    rename_next: RefCell<Option<String>>,
    ql: RefCell<Option<Rc<quicklook::QuickLook>>>,
    me: Weak<Desktop>,
}

impl Desktop {
    /// screen: the display's connector; main: the main display's desktop.
    pub fn new(screen: &str, main: bool) -> Rc<Desktop> {
        sonata_ui::css::register_module("files/views.py"); // the shortcut arrow, the rename field
        sonata_ui::css::register_module("shell/desktop.py");
        let dir = desktop_dir();
        let d = Rc::new_cyclic(|me: &Weak<Desktop>| {
            let (m1, m2) = (me.clone(), me.clone());
            let folder = folder::Folder::new(
                move |_| {
                    if let Some(d) = m1.upgrade() {
                        d.sync();
                    }
                },
                |_, _| {},
                None,
            );
            folder.store.connect_items_changed(move |_, _, _, _| {
                if let Some(d) = m2.upgrade() {
                    d.sync();
                }
            });
            Desktop {
                fixed: icon_layer(),
                screen: screen.to_string(),
                main,
                dir: dir.clone(),
                cfg: RefCell::new(sonata_config::load("desktop", &grid::defaults())),
                items: RefCell::default(),
                selection: RefCell::default(),
                drag_names: RefCell::default(),
                drag_from: Cell::new(None),
                placed: RefCell::default(),
                size: Cell::new((0, 0)),
                margins: Cell::new(dock_margins()),
                top: Cell::new(grid::top(grid::menubar_height())),
                folder,
                band: gtk::Box::builder().css_classes(["desk-band"]).can_target(false).visible(false).build(),
                band_origin: Cell::new((0.0, 0.0)),
                mons: RefCell::default(),
                released: Cell::new(false),
                rename_next: RefCell::default(),
                ql: RefCell::default(),
                me: me.clone(),
            }
        });
        d.folder.load(&dir.uri());
        d.fixed.put(&d.band, 0.0, 0.0);
        d.controllers();
        d.watch();
        // Memory review (Python): the wallpaper windows are made again on
        // every main-display change; watches never cancelled kept every old
        // desktop -- its icons and thumbnails -- alive. Unrealized: let go
        // (realized again: watch again).
        let w = d.me.clone();
        d.fixed.connect_realize(move |_| {
            if let Some(d) = w.upgrade() {
                d.watch();
            }
        });
        let w = d.me.clone();
        d.fixed.connect_unrealize(move |_| {
            if let Some(d) = w.upgrade() {
                d.release();
            }
        });
        d
    }

    fn grid(&self) -> Grid {
        Grid { size: self.size.get(), margins: self.margins.get(), top: self.top.get() }
    }

    fn tag(&self) -> Option<&str> {
        (!self.main).then_some(self.screen.as_str())
    }

    fn controllers(&self) {
        let f = &self.fixed;
        // background: deselect, rubber band, menu, drops
        let drag = gtk::GestureDrag::new();
        let w = self.me.clone();
        drag.connect_drag_begin(move |g, x, y| {
            if let Some(d) = w.upgrade() {
                d.band_begin(g, x, y);
            }
        });
        let w = self.me.clone();
        drag.connect_drag_update(move |_, dx, dy| {
            if let Some(d) = w.upgrade() {
                d.band_update(dx, dy);
            }
        });
        let w = self.me.clone();
        drag.connect_drag_end(move |_, _, _| {
            if let Some(d) = w.upgrade() {
                d.band.set_visible(false);
            }
        });
        f.add_controller(drag);
        let menu = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
        let w = self.me.clone();
        menu.connect_pressed(move |g, _, x, y| {
            if let Some(d) = w.upgrade() {
                d.background_menu(g, x, y);
            }
        });
        f.add_controller(menu);
        let tgt = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::MOVE | gdk::DragAction::COPY);
        let w = self.me.clone();
        tgt.connect_drop(move |t, v, x, y| {
            let (Some(d), Ok(list)) = (w.upgrade(), v.get::<gdk::FileList>()) else { return false };
            d.dropped(list.files(), x, y, copy_key(t))
        });
        f.add_controller(tgt);
        let keys = gtk::EventControllerKey::new();
        let w = self.me.clone();
        keys.connect_key_pressed(move |_, k, _, state| match w.upgrade() {
            Some(d) if d.key(k, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        f.add_controller(keys);
    }

    // -- watches: held while on screen ----------------------------------------------------------
    fn watch(&self) {
        if self.mons.borrow().is_empty() {
            let mut mons = Vec::new();
            let w = self.me.clone();
            mons.extend(sonata_config::watch("desktop", move || {
                if let Some(d) = w.upgrade() {
                    d.config_changed();
                }
            }));
            let w = self.me.clone();
            mons.extend(sonata_config::watch("dock", move || {
                // the Dock moved / resized
                if let Some(d) = w.upgrade() {
                    let m = dock_margins();
                    if m != d.margins.replace(m) {
                        d.layout();
                    }
                }
            }));
            // the taller / standard menu bar: icons stayed where the old one
            // ended until the session started again
            let w = self.me.clone();
            mons.extend(sonata_config::watch("topbar", move || {
                if let Some(d) = w.upgrade() {
                    let top = grid::top(grid::menubar_height());
                    if top != d.top.replace(top) {
                        d.layout();
                    }
                }
            }));
            self.mons.replace(mons);
        }
        if self.released.replace(false) {
            self.folder.load(&self.dir.uri());
        }
    }

    pub fn release(&self) {
        for m in self.mons.take() {
            m.cancel();
        }
        self.folder.cancel();
        self.released.set(true);
    }

    // -- layout -------------------------------------------------------------------------------
    /// From the window's size (GtkFixed has none of its own to follow).
    pub fn resized(&self, w: i32, h: i32) {
        if (w, h) != self.size.get() {
            self.size.set((w, h));
            self.layout_soon();
        }
    }

    fn layout_soon(&self) {
        let w = self.me.clone();
        glib::idle_add_local_once(move || {
            if let Some(d) = w.upgrade() {
                d.layout();
            }
        });
    }

    fn infos(&self) -> Vec<gio::FileInfo> {
        let s = &self.folder.store;
        (0..s.n_items()).filter_map(|i| s.item(i).and_downcast::<gio::FileInfo>()).collect()
    }

    fn sorted_names(&self) -> Vec<String> {
        let mut infos = self.infos();
        match grid::sort_of(&self.cfg.borrow()).as_str() {
            "kind" => infos.sort_by_cached_key(|i| (!folder::is_dir(i), i.content_type().map(|c| c.to_string()).unwrap_or_default(), folder::sort_key(i))),
            "date" => infos.sort_by_cached_key(|i| std::cmp::Reverse(i.modification_date_time().map(|d| d.to_unix()).unwrap_or(0))),
            _ => {} // "name"/"none": the model's Finder order
        }
        infos.iter().map(|i| i.name().to_string_lossy().into_owned()).collect()
    }

    fn layout(&self) {
        if self.size.get().0 == 0 {
            if verbose() {
                eprintln!("desktop {}: no size yet, no layout", self.screen);
            }
            return;
        }
        let g = self.grid();
        let names: Vec<String> = self.sorted_names().into_iter().filter(|n| self.items.borrow().contains_key(n)).collect();
        let prev = if grid::sort_of(&self.cfg.borrow()) == "none" { self.placed.borrow().clone() } else { HashMap::new() };
        let (placed, unsaved) = grid::layout(&g, &names, &self.cfg.borrow(), &prev);
        if verbose() {
            eprintln!("desktop {}: layout {:?} top {} margins {:?} -> {} placed of {} ({:?})", self.screen, g.size, g.top, g.margins, placed.len(), names.len(), placed.iter().map(|(n, c)| (n.clone(), *c, g.cell_xy(c.0, c.1))).collect::<Vec<_>>());
        }
        if !unsaved.is_empty() {
            let w = self.me.clone();
            glib::idle_add_local_once(move || {
                if let Some(d) = w.upgrade() {
                    d.keep_spots(&unsaved);
                }
            });
        }
        // icons that already had a spot slide to their new one (new ones just
        // appear). Only icons already drawn at a spot: one just added sits at
        // 0,0 until now -- it glided in from the top-left corner (Vini)
        let before = {
            let (old, items) = (self.placed.borrow(), self.items.borrow());
            let laid: Vec<gtk::Widget> = items.iter().filter(|(n, it)| old.contains_key(*n) && it.laid.get()).map(|(_, it)| it.widget.clone().upcast()).collect();
            sonata_ui::transition::glide_record(&laid, self.fixed.upcast_ref())
        };
        for (name, (c, r)) in &placed {
            if let Some(item) = self.items.borrow().get(name) {
                let (x, y) = g.cell_xy(*c, *r);
                self.fixed.move_(&item.widget, x as f64, y as f64);
                item.laid.set(true);
                item.widget.set_opacity(1.0);
            }
        }
        self.placed.replace(placed);
        sonata_ui::transition::glide_play(before, self.fixed.upcast_ref(), sonata_ui::transition::GLIDE_MS);
    }

    fn keep_spots(&self, spots: &HashMap<String, Spot>) {
        let mut cfg = sonata_config::load("desktop", &grid::defaults());
        let shown = |n: &str| self.items.borrow().contains_key(n);
        if grid::keep_spots(&mut cfg, spots, &shown, &self.placed.borrow(), self.tag()) {
            self.save(cfg);
        }
    }

    fn save(&self, cfg: Map<String, Value>) {
        if let Err(e) = sonata_config::save("desktop", &cfg) {
            eprintln!("sonata2-rs wallpaper: desktop.json: {e}");
        }
        // known here at once: the folder being made can be listed before the
        // config watch reads this back (Vini: it showed elsewhere, then glided over)
        self.cfg.replace(cfg);
    }

    /// Is this icon shown on this display's desktop?
    pub fn mine(&self, name: &str) -> bool {
        grid::mine(&self.cfg.borrow(), name, self.main, &self.screen, &connected())
    }

    /// Items follow the folder (live monitor), the ones on this display.
    fn sync(&self) {
        let infos: HashMap<String, gio::FileInfo> = self.infos().into_iter().map(|i| (i.name().to_string_lossy().into_owned(), i)).filter(|(n, _)| self.mine(n)).collect();
        if verbose() {
            eprintln!("desktop {} (main {}): {} in the folder, {} here, size {:?}", self.screen, self.main, self.infos().len(), infos.len(), self.size.get());
        }
        let gone: Vec<String> = self.items.borrow().keys().filter(|n| !infos.contains_key(*n)).cloned().collect();
        for name in gone {
            if let Some(item) = self.items.borrow_mut().remove(&name) {
                self.selection.borrow_mut().retain(|i| !Rc::ptr_eq(i, &item));
                self.fixed.remove(&item.widget);
            }
        }
        let g = self.grid();
        for (name, info) in infos {
            let old = self.items.borrow().get(&name).cloned();
            if let Some(old) = &old {
                if *old.info.borrow() == info {
                    continue;
                }
                if !old.lbl.get_visible() || old.lbl.opacity() == 0.0 {
                    old.info.replace(info); // its name being typed (the field over it): keep the field
                    continue;
                }
            }
            let mut at = (0, 0);
            let laid = old.as_ref().is_some_and(|o| o.laid.get());
            if let Some(old) = &old {
                // the same file, fresh info: same spot
                if let Some(s) = self.placed.borrow().get(&name).filter(|_| laid) {
                    at = g.cell_xy(s.0, s.1);
                }
                self.fixed.remove(&old.widget);
                self.selection.borrow_mut().retain(|i| !Rc::ptr_eq(i, old));
            }
            let item = self.item(&info);
            item.laid.set(laid);
            if !laid {
                let spot = (grid::sort_of(&self.cfg.borrow()) == "none").then(|| grid::spot(&self.cfg.borrow(), &name)).flatten();
                match spot.filter(|p| p.0 >= 0 && p.1 >= 0 && p.0 < g.cols() && p.1 < g.rows()) {
                    Some(p) => at = g.cell_xy(p.0, p.1), // its spot is known: drawn there from the start
                    None => item.widget.set_opacity(0.0), // shown once the layout has given it a spot
                }
            }
            self.fixed.put(&item.widget, at.0 as f64, at.1 as f64);
            self.items.borrow_mut().insert(name.clone(), item);
            if self.rename_next.borrow().as_deref() == Some(name.as_str()) {
                // a new folder: its name field at once
                let w = self.me.clone();
                glib::idle_add_local_once(move || {
                    if let Some(d) = w.upgrade() {
                        d.maybe_rename(&name);
                    }
                });
            }
        }
        self.layout_soon();
    }

    fn config_changed(&self) {
        self.cfg.replace(sonata_config::load("desktop", &grid::defaults()));
        self.sync(); // icons moved to / from another display
    }

    fn save_positions(&self, moved: &HashMap<String, Spot>) {
        let mut cfg = sonata_config::load("desktop", &grid::defaults());
        let base = self.dir.path().unwrap_or_default();
        grid::save_positions(&mut cfg, moved, &self.placed.borrow(), self.tag(), &|k| base.join(k).symlink_metadata().is_ok());
        self.save(cfg);
    }

    // -- the icons --------------------------------------------------------------------------------
    fn item(&self, info: &gio::FileInfo) -> Rc<Item> {
        let widget = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).css_classes(["desk-item"]).width_request(CELL_W).halign(gtk::Align::Center).build();
        let img = gtk::Image::builder().pixel_size(ICON).css_classes(["desk-icon"]).halign(gtk::Align::Center).build();
        let app = launcher(info); // an app shortcut (.desktop): its icon and name
        match &app {
            Some(a) => sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(a.upcast_ref())),
            None => sonata_files::views::set_icon(&img, info, false),
        }
        // the icon with a shortcut's arrow over its corner (Files' views);
        // the Desktop is the person's, wherever it is
        let io = gtk::Overlay::builder().child(&img).halign(gtk::Align::Center).build();
        let badge = gtk::Image::builder().icon_name("sonata-shortcut-arrow-symbolic").css_classes(["fs-link-badge"]).halign(gtk::Align::Start).valign(gtk::Align::End).can_target(false).build();
        badge.set_visible(ops::is_shortcut(info, folder::file_of(info).as_ref(), Some(true)));
        io.add_overlay(&badge);
        widget.append(&io);
        let name = app.as_ref().map(sonata_ui::apps::display_name).unwrap_or_else(|| info.display_name().to_string());
        let lbl = gtk::Label::builder().label(name).wrap(true).wrap_mode(gtk::pango::WrapMode::WordChar).lines(2).ellipsize(gtk::pango::EllipsizeMode::Middle).justify(gtk::Justification::Center).max_width_chars(13).halign(gtk::Align::Center).valign(gtk::Align::Start).build();
        // the name in a kept two-line place, the rename field over it (as in
        // Files): the icon never shifts while or after it's renamed (Vini: it
        // moved a little on Enter -- the field was wider than the name)
        let space = gtk::Label::builder().label("X\nX").lines(2).opacity(0.0).can_target(false).max_width_chars(13).build();
        let over = gtk::Overlay::builder().child(&space).build();
        over.add_overlay(&lbl);
        widget.append(&over);
        let item = Rc::new(Item { widget, img, lbl, over, info: RefCell::new(info.clone()), app, laid: Cell::new(false) });
        self.item_controllers(&item);
        item
    }

    fn item_controllers(&self, item: &Rc<Item>) {
        let click = gtk::GestureClick::builder().button(0).build();
        let (w, it) = (self.me.clone(), Rc::downgrade(item));
        click.connect_pressed(move |g, n, x, y| {
            let (Some(d), Some(item)) = (w.upgrade(), it.upgrade()) else { return };
            let button = g.current_button();
            let state = g.current_event_state();
            let selected = d.selected(&item);
            if button == gdk::BUTTON_SECONDARY {
                if !selected {
                    d.select(vec![item.clone()]);
                }
                d.item_menu(&item, x, y);
            } else if n == 2 {
                d.open_selection();
            } else if state.intersects(gdk::ModifierType::CONTROL_MASK | gdk::ModifierType::SHIFT_MASK) {
                d.toggle(&item);
            } else if !selected {
                d.select(vec![item.clone()]);
            }
            // a plain press stays unclaimed: claiming it here cancelled the
            // drag source on the same icon, so icons couldn't be dragged anywhere
            if button == gdk::BUTTON_SECONDARY || n == 2 {
                g.set_state(gtk::EventSequenceState::Claimed);
            }
        });
        item.widget.add_controller(click);
        let src = gtk::DragSource::builder().actions(gdk::DragAction::MOVE | gdk::DragAction::COPY).build();
        let (w, it) = (self.me.clone(), Rc::downgrade(item));
        src.connect_prepare(move |_, _, _| {
            let (d, item) = (w.upgrade()?, it.upgrade()?);
            Some(d.drag_prepare(&item))
        });
        let (w, it) = (self.me.clone(), Rc::downgrade(item));
        src.connect_drag_begin(move |_, drag| {
            let (Some(d), Some(item)) = (w.upgrade(), it.upgrade()) else { return };
            for i in d.selection.borrow().iter() {
                i.widget.add_css_class("dragging");
            }
            let paint = item.img.paintable().unwrap_or_else(|| gtk::WidgetPaintable::new(Some(&item.img)).upcast());
            hang(drag, &paint);
            d.drag_from.set(d.placed.borrow().get(&item.name()).copied());
        });
        let w = self.me.clone();
        src.connect_drag_end(move |_, _, _| {
            if let Some(d) = w.upgrade() {
                for i in d.items.borrow().values() {
                    i.widget.remove_css_class("dragging");
                }
                d.drag_names.replace(None);
            }
        });
        item.widget.add_controller(src);
        if folder::is_dir(&item.info.borrow()) {
            // folders take drops
            let tgt = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::MOVE | gdk::DragAction::COPY);
            let wd = item.widget.downgrade();
            tgt.connect_enter(move |_, _, _| {
                if let Some(b) = wd.upgrade() {
                    b.add_css_class("drop-target");
                }
                gdk::DragAction::MOVE
            });
            let wd = item.widget.downgrade();
            tgt.connect_leave(move |_| {
                if let Some(b) = wd.upgrade() {
                    b.remove_css_class("drop-target");
                }
            });
            let (w, it) = (self.me.clone(), Rc::downgrade(item));
            tgt.connect_drop(move |t, v, _, _| {
                let (Some(d), Some(item), Ok(list)) = (w.upgrade(), it.upgrade(), v.get::<gdk::FileList>()) else { return false };
                item.widget.remove_css_class("drop-target");
                item.file().is_some_and(|f| d.drop_into(list.files(), &f, copy_key(t)))
            });
            item.widget.add_controller(tgt);
        }
    }

    // -- selection ----------------------------------------------------------------------------
    fn selected(&self, item: &Rc<Item>) -> bool {
        self.selection.borrow().iter().any(|i| Rc::ptr_eq(i, item))
    }

    pub fn select(&self, items: Vec<Rc<Item>>) {
        for it in self.selection.borrow().iter() {
            it.widget.remove_css_class("selected");
        }
        for it in &items {
            it.widget.add_css_class("selected");
        }
        self.selection.replace(items);
        self.fixed.grab_focus();
    }

    fn toggle(&self, item: &Rc<Item>) {
        let was = self.selected(item);
        let mut sel: Vec<Rc<Item>> = self.selection.borrow().iter().filter(|i| !Rc::ptr_eq(i, item)).cloned().collect();
        if !was {
            sel.push(item.clone());
        }
        self.select(sel);
    }

    fn on_background(&self, x: f64, y: f64) -> bool {
        self.fixed.pick(x, y, gtk::PickFlags::DEFAULT).is_none_or(|w| &w == self.fixed.upcast_ref::<gtk::Widget>())
    }

    fn band_begin(&self, g: &gtk::GestureDrag, x: f64, y: f64) {
        if !self.on_background(x, y) {
            g.set_state(gtk::EventSequenceState::Denied);
            return;
        }
        self.select(Vec::new());
        self.band_origin.set((x, y));
    }

    fn band_update(&self, dx: f64, dy: f64) {
        let (x0, y0) = self.band_origin.get();
        let (x, y, w, h) = (x0.min(x0 + dx), y0.min(y0 + dy), dx.abs(), dy.abs());
        if w < 3.0 && h < 3.0 {
            return;
        }
        self.band.set_size_request(w as i32, h as i32);
        self.fixed.move_(&self.band, x, y);
        self.band.set_visible(true);
        let rect = gtk::graphene::Rect::new(x as f32, y as f32, w as f32, h as f32);
        let hit: Vec<Rc<Item>> = self.items.borrow().values().filter(|i| i.widget.compute_bounds(&self.fixed).is_some_and(|b| b.intersection(&rect).is_some())).cloned().collect();
        let same = hit.len() == self.selection.borrow().len() && hit.iter().all(|i| self.selected(i));
        if !same {
            self.select(hit);
        }
    }

    // -- actions ------------------------------------------------------------------------------
    fn selected_files(&self) -> Vec<gio::File> {
        self.selection.borrow().iter().filter_map(|i| i.file()).collect()
    }

    fn window(&self) -> Option<gtk::Window> {
        self.fixed.root().and_downcast::<gtk::Window>()
    }

    pub fn open_selection(&self) {
        let sel = self.selection.borrow().clone();
        for item in &sel {
            let Some(f) = item.file() else { continue };
            let info = item.info.borrow().clone();
            if broken(&info, &f) {
                self.broken_shortcut(item);
            } else if let Some(app) = &item.app {
                // Sonata's launch path, with the display's context: the app
                // comes to the front, on its GPU, in its own scope
                let _ = sonata_ui::apps::launch(app, &[], None);
            } else if folder::is_dir(&info) {
                // (folders: the session's file manager -- Sonata's Files)
                let _ = sonata_ui::apps::launch_default_for_uri(&f.uri(), None);
            } else if quicklook::previewable(&info) && sel.len() == 1 && info.content_type().is_none_or(|ct| gio::AppInfo::default_for_type(&ct, false).is_none()) {
                self.quick_look(); // Quick Look only when no app opens it (like Files)
            } else if !f.path().is_some_and(|p| sonata_files::packages::open_path(&p, Some(self.fixed.upcast_ref()))) {
                // (a package, an AppImage, an archive or a script: installed,
                // run or expanded above, as in Files; the rest: its app)
                let _ = sonata_ui::apps::launch_default_for_uri(&f.uri(), None);
            }
        }
    }

    /// A shortcut whose original is gone says so (macOS' alias alert): it
    /// opened an empty Quick Look, or nothing at all.
    fn broken_shortcut(&self, item: &Rc<Item>) {
        let f = item.file();
        sonata_ui::dialog::alert(
            &fmt(&tr!("The shortcut “{name}” can’t be opened because the original item can’t be found."), &[("name", &item.info.borrow().display_name())]),
            "",
            &[("trash", tr!("Move to Trash"), "destructive"), ("ok", tr!("OK"), "default")],
            move |r| {
                if let (Some(f), "trash") = (&f, r) {
                    ops::trash(vec![f.clone()], None, None);
                }
            },
            None,
        );
    }

    pub fn trash_selection(&self) {
        let files = self.selected_files();
        if !files.is_empty() {
            ops::trash(files, None, None);
        }
    }

    /// The desktop sits under the windows and gets the keyboard only when
    /// clicked: it takes it while a name is typed (Vini: a new folder's name
    /// had to be clicked before typing), then gives it back.
    fn take_keyboard(&self, on: bool) {
        use gtk4_layer_shell::{KeyboardMode, LayerShell};
        let Some(win) = self.window().filter(|w| w.is_layer_window()) else { return };
        win.set_keyboard_mode(if on { KeyboardMode::Exclusive } else { KeyboardMode::OnDemand });
        win.queue_draw(); // (the new mode comes with the next frame)
        let output = win.monitor().and_then(|m| m.connector()).map(|c| c.to_string());
        if let Some(data) = focus_layer_request(on, win.layer(), &win.namespace().unwrap_or_default(), output.as_deref()) {
            // under the windows Wayfire gives "exclusive" no keyboard (only the
            // top layers get it): sonata-corners hands it over once the mode is
            // in (layer.take_keyboard; Vini, in the Rust session: the new
            // folder's name field took no typing until clicked)
            glib::timeout_add_local_once(std::time::Duration::from_millis(FOCUS_AFTER_MS), move || {
                std::thread::spawn(move || {
                    sonata_backend::wfipc::call("sonata/focus-layer", data);
                });
            });
        }
    }

    pub fn rename_selection(&self) {
        let sel = self.selection.borrow().clone();
        let [item] = sel.as_slice() else { return };
        self.take_keyboard(true);
        let w = self.me.clone();
        self.inline_rename(item, move |old, new| {
            if let Some(d) = w.upgrade() {
                d.take_keyboard(false);
                if let Some(new) = new {
                    d.commit_rename(&old, &new);
                }
            }
        });
    }

    fn commit_rename(&self, item: &Rc<Item>, new: &str) {
        let old = item.name();
        let Some(f) = item.file() else { return };
        // the new name keeps the spot before the file is renamed: the folder
        // watch can list it first, and it went to the first free cell (top
        // right) -- the old name's spot was gone by then (Vini). Not over a
        // file that already has the name: the rename fails, and that file's
        // icon had been sent to this one's spot.
        let spot = self.placed.borrow().get(&old).copied();
        let taken = self.dir.path().is_some_and(|b| b.join(new).symlink_metadata().is_ok());
        if let Some(s) = spot_for_new_name(spot, &grid::sort_of(&self.cfg.borrow()), taken) {
            self.save_positions(&HashMap::from([(new.to_string(), s)]));
        }
        let (w, new_name) = (self.me.clone(), new.to_string());
        let n2 = new_name.clone();
        ops::rename(
            &f,
            new,
            move |_| {
                if let Some(d) = w.upgrade() {
                    if let Some(p) = spot.or_else(|| d.placed.borrow().get(&old).copied()) {
                        d.save_positions(&HashMap::from([(new_name, p)]));
                    }
                }
            },
            // a name that can't be used says so (it was silently kept, as in Files)
            move |e| alert(&fmt(&tr!("The name “{name}” can’t be used."), &[("name", &n2)]), e.message()),
        );
    }

    /// The name edited in place, the field over its two-line place (Files'
    /// own is private to its views): Return / a click elsewhere commits,
    /// Escape cancels. done(item, Some(new name) | None).
    fn inline_rename(&self, item: &Rc<Item>, done: impl Fn(Rc<Item>, Option<String>) + 'static) {
        let info = item.info.borrow().clone();
        let name = info.display_name().to_string();
        let entry = gtk::Entry::builder().text(&name).css_classes(["fs-rename"]).halign(gtk::Align::Fill).valign(gtk::Align::Start).width_chars(1).max_width_chars(1).build();
        item.lbl.set_opacity(0.0);
        item.over.add_overlay(&entry);
        let finished = Rc::new(Cell::new(false));
        let finish = {
            let (e, it, finished) = (entry.downgrade(), Rc::downgrade(item), finished.clone());
            Rc::new(move |commit: bool| {
                let (Some(entry), Some(item)) = (e.upgrade(), it.upgrade()) else { return };
                if finished.replace(true) {
                    return;
                }
                let new = entry.text().trim().to_string();
                item.over.remove_overlay(&entry);
                item.lbl.set_opacity(1.0);
                done(item, (commit && !new.is_empty() && new != name).then_some(new));
            })
        };
        let f = finish.clone();
        entry.connect_activate(move |_| f(true));
        let keys = gtk::EventControllerKey::new();
        let f = finish.clone();
        keys.connect_key_pressed(move |_, k, _, _| {
            if k == gdk::Key::Escape {
                f(false);
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        entry.add_controller(keys);
        let focus = gtk::EventControllerFocus::new();
        focus.connect_leave(move |_| {
            let f = finish.clone();
            glib::idle_add_local_once(move || f(true));
        });
        entry.add_controller(focus);
        entry.grab_focus();
        let (s, e) = ops::rename_selection(&info.display_name(), folder::is_dir(&info));
        entry.select_region(s, e);
    }

    pub fn get_info(&self) {
        let Some(win) = self.window() else { return };
        for item in self.selection.borrow().iter().take(5) {
            quicklook::get_info(&win, &item.info.borrow()).present();
        }
    }

    pub fn quick_look(&self) {
        let Some(info) = self.selection.borrow().first().map(|i| i.info.borrow().clone()) else { return };
        let Some(win) = self.window() else { return };
        if self.ql.borrow().is_none() {
            let w = self.me.clone();
            self.ql.replace(Some(quicklook::QuickLook::new(&win, Box::new(move || {
                if let Some(d) = w.upgrade() {
                    d.ql.replace(None);
                }
            }))));
        }
        if let Some(q) = self.ql.borrow().as_ref() {
            q.show_item(&info);
            q.window.present();
        }
    }

    fn parent(&self) -> Option<gtk::Widget> {
        Some(self.fixed.clone().upcast())
    }

    pub fn duplicate_selection(&self) {
        let files = self.selected_files();
        if !files.is_empty() {
            ops::transfer(files, &self.dir, false, self.parent(), None, true);
        }
    }

    /// Shortcuts to the selection, here (Vini, like Windows).
    pub fn shortcut_selection(&self) {
        ops::make_shortcuts(&self.selected_files(), &self.dir);
    }

    pub fn copy_selection(&self) {
        let files = self.selected_files();
        if !files.is_empty() {
            ops::copy_to_clipboard(&self.fixed, &files, false);
        }
    }

    pub fn paste(&self) {
        let w = self.me.clone();
        ops::read_clipboard(&self.fixed, move |files, cut| {
            let Some(d) = w.upgrade() else { return };
            if !files.is_empty() {
                ops::transfer(files, &d.dir, cut, d.parent(), None, false);
                if cut {
                    let _ = d.fixed.clipboard().set_content(None::<&gdk::ContentProvider>);
                }
            } else {
                // a copied picture becomes a file
                ops::paste_image(&d.fixed, &d.dir, None, Some(Box::new(|e: glib::Error| alert(&tr!("The picture couldn’t be pasted."), e.message()))));
            }
        });
    }

    /// A new folder at `at` (the background menu's cell), its name field
    /// open as soon as its icon is there (Vini: the name showed first, then
    /// turned into the field 300 ms later).
    pub fn new_folder(&self, at: Option<Spot>) {
        let (w, w2) = (self.me.clone(), self.me.clone());
        ops::new_folder(
            &self.dir,
            move |child| {
                if let Some(d) = w.upgrade() {
                    d.maybe_rename(&ops::base(&child));
                }
            },
            |_| {},
            Some(Box::new(move |child: &gio::File| {
                // its spot is saved before it exists: it appears where it was
                // asked for (Vini: it showed in the first free cell, top right,
                // and glided over)
                if let Some(d) = w2.upgrade() {
                    let name = ops::base(child);
                    d.rename_next.replace(Some(name.clone()));
                    if let Some(at) = at {
                        d.save_positions(&HashMap::from([(name, at)]));
                    }
                }
            })),
        );
    }

    fn maybe_rename(&self, name: &str) {
        if self.rename_next.borrow().as_deref() == Some(name) {
            let item = self.items.borrow().get(name).cloned();
            if let Some(item) = item {
                self.rename_next.replace(None);
                self.select(vec![item]);
                self.rename_selection();
            }
        }
    }

    /// Snap this display's icons into the grid in name order (Finder's Clean
    /// Up By Name). Icons on the other displays stay there.
    pub fn clean_up(&self) {
        let mut cfg = sonata_config::load("desktop", &grid::defaults());
        let order: Vec<String> = self.infos().iter().map(|i| i.name().to_string_lossy().into_owned()).collect();
        let shown = |n: &str| self.items.borrow().contains_key(n);
        grid::clean_up(&mut cfg, &self.grid(), &order, &shown, self.tag());
        self.save(cfg);
    }

    pub fn set_sort(&self, sort: &str) {
        let mut cfg = sonata_config::load("desktop", &grid::defaults());
        cfg.insert("sort".into(), json!(sort));
        if let Err(e) = sonata_config::save("desktop", &cfg) {
            eprintln!("sonata2-rs wallpaper: desktop.json: {e}");
        }
    }

    // -- menus ----------------------------------------------------------------------------------
    fn item_menu(&self, item: &Rc<Item>, x: f64, y: f64) {
        use sonata_ui::menu::Item as M;
        let n = self.selection.borrow().len();
        let (look, copy) = if n == 1 {
            let name = item.info.borrow().display_name().to_string();
            (fmt(&tr!("Quick Look “{name}”"), &[("name", &name)]), fmt(&tr!("Copy “{name}”"), &[("name", &name)]))
        } else {
            let k = n.to_string();
            (fmt(&trn!("Quick Look {n} Item", "Quick Look {n} Items", n), &[("n", &k)]), fmt(&trn!("Copy {n} Item", "Copy {n} Items", n), &[("n", &k)]))
        };
        // anchored to the desktop, not the icon: the selected icon's styles
        // (blue label...) would otherwise reach into the menu's rows
        let pt = item.widget.compute_point(&self.fixed, &gtk::graphene::Point::new(x as f32, y as f32)).map(|p| (p.x() as f64, p.y() as f64)).unwrap_or((x, y));
        let w = || self.me.clone();
        let act = |f: fn(&Desktop)| {
            let w = w();
            move || {
                if let Some(d) = w.upgrade() {
                    f(&d);
                }
            }
        };
        let mut first = vec![M::new(&tr!("Open"), act(Desktop::open_selection))];
        // a package, an archive or a script: Install / Run / Extract, ahead of Open
        if let (1, None, Some(p)) = (n, &item.app, item.file().and_then(|f| f.path())) {
            first.splice(0..0, sonata_files::packages::menu_items(&p, Some(self.fixed.upcast_ref())));
        }
        let ct = item.info.borrow().content_type().map(|c| c.to_string()).unwrap_or_default();
        if let (1, None, Some(f)) = (n, &item.app, item.file()) {
            let uri = f.uri().to_string();
            let label = if is_picture(&ct) {
                Some(tr!("Set Desktop Picture"))
            } else if sonata_shell::wallpaper::is_video(&ct) {
                Some(tr!("Use Video as Wallpaper")) // a video wallpaper (videowall.py)
            } else {
                None
            };
            if let Some(label) = label {
                first.push(M::new(&label, move || {
                    if let Err(e) = sonata_config::set_wallpaper(&uri) {
                        eprintln!("sonata2-rs wallpaper: {e}");
                    }
                }));
            }
        }
        sonata_ui::menu::popup(
            &self.fixed,
            vec![
                first,
                vec![M::new(&tr!("Move to Trash"), act(Desktop::trash_selection))],
                vec![
                    M::new(&tr!("Get Info"), act(Desktop::get_info)),
                    M::new(&tr!("Rename"), act(Desktop::rename_selection)).enabled(n == 1),
                    M::new(&tr!("Duplicate"), act(Desktop::duplicate_selection)),
                    M::new(&tr!("Create Shortcut"), act(Desktop::shortcut_selection)),
                ],
                vec![M::new(&look, act(Desktop::quick_look))],
                vec![M::new(&copy, act(Desktop::copy_selection))],
            ],
            sonata_ui::menu::Options::at(pt.0, pt.1),
        );
    }

    fn background_menu(&self, g: &gtk::GestureClick, x: f64, y: f64) {
        use sonata_ui::menu::Item as M;
        if !self.on_background(x, y) {
            return;
        }
        g.set_state(gtk::EventSequenceState::Claimed);
        self.select(Vec::new());
        let sort = grid::sort_of(&self.cfg.borrow());
        let cell = self.grid().cell_at(x, y);
        let w = || self.me.clone();
        let wn = w();
        let sorts = grid::SORTS
            .iter()
            .map(|(key, label)| {
                let (w, key) = (w(), *key);
                M::check(&sonata_i18n::gettext(label), sort == key, move |_| {
                    if let Some(d) = w.upgrade() {
                        d.set_sort(key);
                    }
                })
            })
            .collect();
        let (wp, wc) = (w(), w());
        sonata_ui::menu::popup(
            &self.fixed,
            vec![
                // (New Web App…: Apps' menu only, Vini)
                vec![M::new(&tr!("New Folder"), move || {
                    if let Some(d) = wn.upgrade() {
                        d.new_folder(Some(cell));
                    }
                })],
                vec![M::new(&tr!("Paste Item"), move || {
                    if let Some(d) = wp.upgrade() {
                        d.paste();
                    }
                })
                .enabled(ops::clipboard_has_files(&self.fixed))],
                vec![M::new(&tr!("Change Desktop Background…"), || open_settings("wallpaper"))],
                vec![
                    M::new(&tr!("Clean Up"), move || {
                        if let Some(d) = wc.upgrade() {
                            d.clean_up();
                        }
                    }),
                    M::submenu(&tr!("Sort By"), vec![sorts]),
                ],
            ],
            sonata_ui::menu::Options::at(x, y),
        );
    }

    // -- drag and drop --------------------------------------------------------------------------
    fn drag_prepare(&self, item: &Rc<Item>) -> gdk::ContentProvider {
        if !self.selected(item) {
            self.select(vec![item.clone()]);
        }
        self.drag_names.replace(Some(self.selection.borrow().iter().map(|i| i.name()).collect()));
        // file list, uri-list and (one picture) its image
        ops::file_content(&self.selected_files(), false, true)
    }

    fn dropped(&self, files: Vec<gio::File>, x: f64, y: f64, copy: bool) -> bool {
        let g = self.grid();
        let at = g.cell_at(x, y);
        let own = self.drag_names.borrow().clone();
        if let Some(names) = own.filter(|n| !n.is_empty()) {
            // moving our own icons: new spots
            let moved = grid::drop_own(&g, &names, &self.placed.borrow(), self.drag_from.get(), at);
            self.save_positions(&moved);
            return true;
        }
        let here: Vec<&gio::File> = files.iter().filter(|f| f.parent().is_some_and(|p| p.equal(&self.dir))).collect();
        if !here.is_empty() && here.len() == files.len() {
            // icons from another display's desktop: just move them
            let names: Vec<String> = here.iter().map(|f| ops::base(f)).collect();
            let moved = grid::drop_spots(&g, &names, &self.placed.borrow(), at, &|_| false);
            self.save_positions(&moved);
            return true;
        }
        let base = self.dir.path().unwrap_or_default();
        let names: Vec<String> = files.iter().map(|f| f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default()).collect();
        let moved = grid::drop_spots(&g, &names, &self.placed.borrow(), at, &|n| base.join(n).symlink_metadata().is_ok());
        if !moved.is_empty() {
            self.save_positions(&moved);
        }
        self.drop_into(files, &self.dir, copy)
    }

    fn drop_into(&self, files: Vec<gio::File>, dest: &gio::File, copy: bool) -> bool {
        let files = ops::drop_plan(&files, dest); // same folder / into itself: silently nothing
        if files.is_empty() {
            return false;
        }
        let mv = !copy && files.iter().all(|f| same_disk(f, dest));
        ops::transfer(files, dest, mv, self.parent(), None, false);
        true
    }

    // -- keys -------------------------------------------------------------------------------------
    fn key(&self, k: gdk::Key, state: gdk::ModifierType) -> bool {
        use gdk::Key;
        let ctrl = state.contains(gdk::ModifierType::CONTROL_MASK);
        if matches!(k, Key::Delete | Key::KP_Delete) || (ctrl && k == Key::BackSpace) {
            self.trash_selection();
        } else if matches!(k, Key::Return | Key::KP_Enter | Key::F2) {
            self.rename_selection();
        } else if k == Key::space {
            self.quick_look();
        } else if ctrl && matches!(k, Key::a | Key::A) {
            self.select(self.items.borrow().values().cloned().collect());
        } else if ctrl && matches!(k, Key::c | Key::C) {
            self.copy_selection();
        } else if ctrl && matches!(k, Key::v | Key::V) {
            self.paste();
        } else if ctrl && matches!(k, Key::o | Key::O | Key::Down) {
            self.open_selection();
        } else if k == Key::Escape {
            self.select(Vec::new());
        } else {
            return false;
        }
        true
    }
}

/// ui.drag.hang: the dragged item as macOS shows it -- the icon itself, a
/// little translucent, held steady under the pointer (Vini). GTK's own drag
/// icon was the icon fully opaque.
fn hang(drag: &gdk::Drag, paint: &gdk::Paintable) {
    let pic = gtk::Picture::for_paintable(paint);
    pic.set_size_request(ICON, ICON);
    pic.set_can_shrink(true);
    pic.set_opacity(DRAG_OPACITY);
    gtk::DragIcon::for_drag(drag).set_child(Some(&pic));
    drag.set_hotspot(ICON / 2, ICON / 2);
}

/// layer.FOCUS_AFTER_MS: the keyboard asked for once the new mode is in
const FOCUS_AFTER_MS: u64 = 60;

/// What sonata-corners is asked ("sonata/focus-layer") to hand a layer
/// surface the keyboard: only taking it, and only under the windows (the
/// top layers get it from Wayfire itself).
fn focus_layer_request(on: bool, layer: Option<gtk4_layer_shell::Layer>, namespace: &str, output: Option<&str>) -> Option<Value> {
    use gtk4_layer_shell::Layer;
    if !on || !matches!(layer, Some(Layer::Background | Layer::Bottom)) {
        return None;
    }
    let mut data = json!({"namespace": namespace});
    if let Some(o) = output.filter(|o| !o.is_empty()) {
        data["output"] = json!(o);
    }
    Some(data)
}

/// ui/drag.py's HangingIcon.OPACITY
const DRAG_OPACITY: f64 = 0.78;

/// The spot a renamed icon's new name gets before the rename: its own,
/// unsorted, and not when another file has that name (the rename fails).
fn spot_for_new_name(spot: Option<Spot>, sort: &str, taken: bool) -> Option<Spot> {
    spot.filter(|_| sort == "none" && !taken)
}

fn dock_margins() -> (i32, i32, i32) {
    grid::dock_margins(&sonata_config::load("dock", &json!({"icon_size": 48, "edge_gap": 4, "position": "bottom", "autohide": false})))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn info(path: &std::path::Path) -> (gio::FileInfo, gio::File) {
        let f = gio::File::for_path(path);
        (f.query_info("standard::*", gio::FileQueryInfoFlags::NOFOLLOW_SYMLINKS, gio::Cancellable::NONE).unwrap(), f)
    }

    /// Opening a shortcut whose original was deleted showed an empty Quick
    /// Look (or nothing): it says the original is gone.
    #[test]
    fn test_broken_shortcut_says_the_original_is_gone() {
        let d = tempfile::tempdir().unwrap();
        let (orig, link) = (d.path().join("gamma.txt"), d.path().join("gamma.txt - Shortcut"));
        std::fs::write(&orig, "x").unwrap();
        std::os::unix::fs::symlink(&orig, &link).unwrap();
        std::fs::remove_file(&orig).unwrap();
        let (i, f) = info(&link);
        assert!(broken(&i, &f));
    }

    #[test]
    fn test_working_shortcut_still_opens() {
        let d = tempfile::tempdir().unwrap();
        let (orig, link) = (d.path().join("gamma.txt"), d.path().join("gamma.txt - Shortcut"));
        std::fs::write(&orig, "x").unwrap();
        std::os::unix::fs::symlink(&orig, &link).unwrap();
        let (i, f) = info(&link);
        assert!(!broken(&i, &f));
    }

    /// Renaming an icon to a name already on the Desktop failed, and the
    /// other file's icon was sent to the renamed one's spot.
    #[test]
    fn test_renaming_to_a_taken_name_doesnt_move_that_icon() {
        assert_eq!(spot_for_new_name(Some((3, 3)), "none", true), None);
        assert_eq!(spot_for_new_name(Some((3, 3)), "none", false), Some((3, 3)));
        assert_eq!(spot_for_new_name(Some((3, 3)), "kind", false), None);
    }

    /// Vini, in the Rust session: Clean Up, Sort By or a file gone made the
    /// desktop's icons jump to their new spots; the Python desktop slides
    /// them there (ui.transition's glide).
    #[test]
    fn test_icons_jumped_to_their_new_spot() {
        let _l = crate::testenv::lock();
        crate::testenv::on_gtk(icons_glide);
    }

    fn icons_glide() {
        let dir = desktop_dir().path().unwrap();
        // a the oldest, c the newest: by date the order turns round
        for (i, n) in ["a.txt", "b.txt", "c.txt"].iter().enumerate() {
            let f = std::fs::File::create(dir.join(n)).unwrap();
            f.set_modified(std::time::SystemTime::UNIX_EPOCH + std::time::Duration::from_secs(1_000_000 * (i as u64 + 1))).unwrap();
        }
        let d = Desktop::new("", true);
        let win = gtk::Window::builder().default_width(800).default_height(600).child(&d.fixed).build();
        win.present();
        let ctx = glib::MainContext::default();
        let spin = |until: &dyn Fn() -> bool| {
            let t = std::time::Instant::now();
            while !until() && t.elapsed() < std::time::Duration::from_secs(5) {
                ctx.iteration(false);
            }
        };
        d.resized(800, 600);
        spin(&|| d.placed.borrow().len() == 3 && d.fixed.is_mapped() && d.items.borrow().values().all(|i| i.widget.is_mapped()));
        assert_eq!(d.placed.borrow().len(), 3, "the icons were never laid out");
        let gliding = || d.items.borrow().values().any(|i| sonata_ui::transition::glide_offset(i.widget.upcast_ref()) != (0.0, 0.0));
        d.cfg.borrow_mut().insert("sort".into(), json!("date"));
        d.layout();
        spin(&gliding);
        assert!(gliding(), "the icons jumped to their new spot");
        win.destroy();
        for n in ["a.txt", "b.txt", "c.txt"] {
            let _ = std::fs::remove_file(dir.join(n));
        }
    }

    /// Vini, in the Rust session: a new folder's name field on the desktop
    /// took no typing until it was clicked -- the desktop is a background
    /// layer, and Wayfire gives "exclusive" no keyboard there; the Python
    /// desktop asks Sonata's plugin to hand it over.
    #[test]
    fn test_new_folder_name_took_no_typing() {
        use gtk4_layer_shell::Layer;
        assert_eq!(focus_layer_request(true, Some(Layer::Background), "sonata2-wallpaper", Some("DP-1")), Some(json!({"namespace": "sonata2-wallpaper", "output": "DP-1"})));
        assert_eq!(focus_layer_request(true, Some(Layer::Bottom), "x", None), Some(json!({"namespace": "x"})));
        assert_eq!(focus_layer_request(false, Some(Layer::Background), "sonata2-wallpaper", None), None); // giving it back
        assert_eq!(focus_layer_request(true, Some(Layer::Overlay), "x", None), None); // the top layers get it anyway
    }

    #[test]
    fn test_menus_offer_videos() {
        assert!(is_picture("image/png") && !is_picture("image/svg+xml") && !is_picture("image/vnd.microsoft.icon"));
        assert!(sonata_shell::wallpaper::is_video("video/mp4"));
    }
}
