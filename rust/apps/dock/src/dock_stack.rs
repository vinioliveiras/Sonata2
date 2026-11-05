//! Dock stacks (shell/dock_stack.py, macOS): a folder in the Dock, right
//! of the divider.
//!
//! Click: a panel with the folder's items (Grid or List view); click an
//! item to open it, drag it out to copy/move it, "Open in Files" at the
//! bottom. Right-click: Sort by, Display as (Folder / Stack = newest item's
//! icon), View content as, Remove from Dock, Open "<folder>". A folder
//! dropped on the Dock becomes a stack. Default: Downloads. A package, an
//! AppImage, an archive or a script clicked in it is installed, run or
//! extracted, like a double-click in Files (packages.rs).

use crate::dock::{self, Dock, Tile, TileFn};
use crate::dock_menu;
use gtk::prelude::*;
use gtk::{gio, glib, pango};
use serde_json::{json, Map, Value};
use sonata_i18n::{n_, tr};
use sonata_ui::menu::Item;
use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::{Rc, Weak};
use std::time::Duration;

/// shown as the folder's icon by default (Vini: Downloads showed its
/// newest photo); "stack" (the first item's picture) stays in its menu
pub const STACK_DEFAULTS: [(&str, &str); 3] = [("display", "folder"), ("view", "grid"), ("sort", "added")];
/// dock.json "stacks_version": 2 = the folder icon made the default
pub const STACKS_VERSION: i64 = 2;
pub const SORTS: [(&str, &str); 4] = [("name", n_!("Name")), ("added", n_!("Date Added")), ("modified", n_!("Date Modified")), ("kind", n_!("Kind"))];
pub const MAX_ITEMS: usize = 60;
pub const GRID_COLS: usize = 5;
pub const GRID_ICON: i32 = 48;
/// monitor events coalesced before the folder is listed again: a download
/// writing fires them all the time (at 300 ms the folder was listed ~3
/// times a second until it finished)
pub const REFRESH_MS: u64 = 1000;
/// a drive unplugged: its folders leave this long after
pub const MOUNT_MS: u64 = 300;

/// A folder event that can change the stack (not a write in progress:
/// a download sends CHANGED many times a second).
pub fn stack_event(ev: gio::FileMonitorEvent) -> bool {
    ev != gio::FileMonitorEvent::Changed
}

fn downloads() -> PathBuf {
    glib::user_special_dir(glib::UserDirectory::Downloads).unwrap_or_else(|| glib::home_dir().join("Downloads"))
}

/// The folder isn't there any more: deleted, moved away, or on a drive
/// that was unplugged (Vini: it stayed in the Dock; a pen drive's too).
pub fn gone(path: &str) -> bool {
    !path.is_empty() && !std::path::Path::new(path).is_dir()
}

/// A stack's spec with every choice filled.
pub fn with_defaults(spec: &Map<String, Value>) -> Map<String, Value> {
    let mut out: Map<String, Value> = STACK_DEFAULTS.iter().map(|(k, v)| (k.to_string(), json!(v))).collect();
    for (k, v) in spec {
        out.insert(k.clone(), v.clone());
    }
    out
}

fn spec_str(spec: &Map<String, Value>, k: &str) -> String {
    spec.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

pub fn default_stacks() -> Vec<Value> {
    let d = downloads();
    if d.is_dir() {
        let mut m = with_defaults(&Map::new());
        m.insert("path".into(), json!(d.to_string_lossy()));
        vec![Value::Object(m)]
    } else {
        Vec::new()
    }
}

/// One item of the folder, as the sort and the icon need it.
#[derive(Clone, Debug)]
pub struct Entry {
    pub name: String,
    pub display: String,
    pub content_type: String,
    pub is_dir: bool,
    pub modified: f64,
    pub added: f64,
    pub thumb: Option<String>,
    /// the file's own icon (standard::icon, as text: Entry crosses threads)
    /// -- Pictures, Music and custom folder icons, not a plain folder
    pub icon: Option<String>,
}

/// The order of a stack's items (pure: tests).
pub fn sort_entries(entries: &mut [Entry], sort: &str) {
    match sort {
        "name" => entries.sort_by_key(|e| e.display.to_lowercase()),
        "kind" => entries.sort_by_key(|e| (e.content_type.clone(), e.display.to_lowercase())),
        "modified" => entries.sort_by(|a, b| b.modified.total_cmp(&a.modified)),
        _ => entries.sort_by(|a, b| b.added.total_cmp(&a.added)),
    }
}

/// The folder's visible items, sorted (gio only: safe off the main loop).
pub fn items(path: &str, sort: &str) -> Vec<Entry> {
    let folder = gio::File::for_path(path);
    let attrs = "standard::name,standard::display-name,standard::icon,standard::content-type,standard::is-hidden,standard::type,time::*,thumbnail::path";
    let Ok(en) = folder.enumerate_children(attrs, gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) else { return Vec::new() };
    let t = |info: &gio::FileInfo, attr: &str| -> f64 {
        if !info.has_attribute(attr) {
            return 0.0;
        }
        // seconds + microseconds (files made in the same second)
        let usec = format!("{attr}-usec");
        info.attribute_uint64(attr) as f64 + if info.has_attribute(&usec) { info.attribute_uint32(&usec) as f64 / 1e6 } else { 0.0 }
    };
    let mut out = Vec::new();
    while let Ok(Some(info)) = en.next_file(gio::Cancellable::NONE) {
        let name = info.name().to_string_lossy().to_string();
        if info.is_hidden() || name.starts_with('.') {
            continue;
        }
        out.push(Entry {
            display: info.display_name().to_string(),
            name,
            content_type: info.content_type().map(|c| c.to_string()).unwrap_or_default(),
            is_dir: info.file_type() == gio::FileType::Directory,
            modified: t(&info, "time::modified"),
            added: t(&info, "time::created").max(t(&info, "time::changed")),
            thumb: info.attribute_byte_string("thumbnail::path").map(|s| s.to_string()),
            icon: info.icon().and_then(|i| IconExt::to_string(&i)).map(|s| s.to_string()),
        });
    }
    sort_entries(&mut out, sort);
    out.truncate(MAX_ITEMS);
    out
}

fn entry_icon(e: &Entry) -> gio::Icon {
    if let Some(t) = e.thumb.as_ref().filter(|t| std::path::Path::new(t).exists()) {
        return gio::FileIcon::new(&gio::File::for_path(t)).upcast();
    }
    if let Some(i) = e.icon.as_deref().and_then(|s| gio::Icon::for_string(s).ok()) {
        return i;
    }
    if e.is_dir {
        return gio::ThemedIcon::new("folder").upcast();
    }
    if e.content_type.is_empty() {
        gio::ThemedIcon::new("text-x-generic").upcast()
    } else {
        gio::content_type_get_icon(&e.content_type)
    }
}

/// The stack's icon: its first item's (display "stack"), else the folder's.
fn stack_icon(spec: &Map<String, Value>, first: Option<&Entry>) -> gio::Icon {
    if spec_str(spec, "display") == "stack" {
        if let Some(e) = first {
            return entry_icon(e);
        }
    }
    let path = spec_str(spec, "path");
    if PathBuf::from(&path) == downloads() {
        gio::ThemedIcon::from_names(&["folder-download", "folder"]).upcast()
    } else {
        gio::ThemedIcon::new("folder").upcast()
    }
}

/// Only a different icon: set_gicon drops the tile's rendered icon and
/// repaints the (blurred) Dock -- every refresh of a downloading folder did.
fn set_icon(tile: &Tile, icon: gio::Icon) {
    if tile.gicon.borrow().as_ref().is_some_and(|old| old.equal(Some(&icon))) {
        return;
    }
    tile.set_gicon(icon);
}

/// Folders open in Sonata's Files, files in their default app (packages,
/// archives and scripts: installed / run / extracted).
pub fn open(file: &gio::File) {
    if file.query_file_type(gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) == gio::FileType::Directory {
        dock_menu::open_folder(&file.uri());
        return;
    }
    if file.path().is_some_and(|p| sonata_files::packages::open_path(&p, None)) {
        return; // install / run / extract
    }
    let _ = sonata_ui::apps::launch_default_for_uri(&file.uri(), None); // (the display's context: to the front)
}

fn drag_source(widget: &impl IsA<gtk::Widget>, file: gio::File) {
    let src = gtk::DragSource::new();
    src.set_actions(gtk::gdk::DragAction::COPY | gtk::gdk::DragAction::MOVE);
    // a plain uri-list: every receiver takes it
    src.connect_prepare(move |_, _, _| Some(sonata_files::ops::file_content(std::slice::from_ref(&file), false, false)));
    widget.add_controller(src);
}

/// A stack tile's own state: its folder's monitor and the pending listing.
#[derive(Default)]
pub struct TileState {
    monitor: RefCell<Option<gio::FileMonitor>>,
    src: RefCell<Option<glib::SourceId>>,
    busy: Cell<bool>,
    again: Cell<bool>,
}

/// The stack tiles of a Dock (between the divider and the Trash).
pub struct StackRow {
    me: Weak<StackRow>,
    dock: Weak<Dock>,
    tiles: RefCell<Vec<Rc<Tile>>>,
    volumes: RefCell<Option<(gio::VolumeMonitor, glib::SignalHandlerId)>>,
}

impl StackRow {
    pub fn new(dock: Weak<Dock>) -> Rc<StackRow> {
        // (made inside the Dock: the drives are watched once it loads)
        Rc::new_cyclic(|me| StackRow { me: me.clone(), dock, tiles: RefCell::default(), volumes: RefCell::default() })
    }

    fn dock(&self) -> Option<Rc<Dock>> {
        self.dock.upgrade()
    }

    /// A drive unplugged: its folders leave the Dock (inotify may say
    /// nothing when the mount goes away).
    fn drives(&self) {
        if self.volumes.borrow().is_some() {
            return;
        }
        let vm = gio::VolumeMonitor::get();
        let w = self.me.clone();
        let id = vm.connect_mount_removed(move |_, _| {
            let w = w.clone();
            glib::timeout_add_local_once(Duration::from_millis(MOUNT_MS), move || {
                if let Some(r) = w.upgrade() {
                    r.drop_gone();
                }
            });
        });
        self.volumes.replace(Some((vm, id)));
    }

    pub fn drop_gone(&self) {
        let gone_tiles: Vec<Rc<Tile>> = self.tiles.borrow().iter().filter(|t| gone(&spec_str(&t.spec.borrow(), "path"))).cloned().collect();
        for t in gone_tiles {
            self.remove(&t, true);
        }
    }

    pub fn tiles(&self) -> Vec<Rc<Tile>> {
        self.tiles.borrow().clone()
    }

    pub fn load(&self) {
        let Some(dock) = self.dock() else { return };
        self.drives();
        let specs: Vec<Map<String, Value>> = dock.cfg.borrow().get("stacks").and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_object().cloned()).collect()).unwrap_or_default();
        let version = dock.cfg.borrow().get("stacks_version").and_then(Value::as_i64).unwrap_or(1);
        let migrate = version < STACKS_VERSION;
        let specs: Vec<Map<String, Value>> = if migrate {
            // saved with the old default: the folder icon now
            dock.cfg.borrow_mut().insert("stacks_version".into(), json!(STACKS_VERSION));
            specs.into_iter().map(|mut s| {
                s.insert("display".into(), json!("folder"));
                s
            }).collect()
        } else {
            specs
        };
        let kept: Vec<&Map<String, Value>> = specs.iter().filter(|s| !gone(&spec_str(s, "path"))).collect();
        for spec in &kept {
            self.add_tile(with_defaults(spec));
        }
        if migrate || kept.len() != specs.len() {
            self.save(); // (deleted while the Dock wasn't running)
        }
    }

    fn save(&self) {
        let Some(dock) = self.dock() else { return };
        let specs: Vec<Value> = self.tiles.borrow().iter().map(|t| Value::Object(t.spec.borrow().clone())).collect();
        dock.cfg.borrow_mut().insert("stacks".into(), Value::Array(specs));
        dock.save_cfg(); // the chosen icon size, not the shrunk-to-fit one
    }

    pub fn add(&self, path: &str) {
        if !std::path::Path::new(path).is_dir() || self.tiles.borrow().iter().any(|t| spec_str(&t.spec.borrow(), "path") == path) {
            return;
        }
        let mut spec = with_defaults(&Map::new());
        spec.insert("path".into(), json!(path));
        let tile = self.add_tile(spec);
        self.save();
        if let Some(d) = self.dock() {
            d.open_up(&tile); // its place opens, then it fades in
        }
    }

    pub fn remove(&self, tile: &Rc<Tile>, save: bool) {
        self.unwatch(tile);
        self.tiles.borrow_mut().retain(|t| !Rc::ptr_eq(t, tile));
        tile.label.pop.unparent();
        if let Some(d) = self.dock() {
            d.close_up(&tile.widget(), false); // its place closes up, like an app's
        }
        if save {
            self.save();
        }
    }

    /// Settings > Dock changed the folders (added back, removed): the Dock
    /// follows, nothing written back (the file is already so).
    pub fn sync(&self, specs: &[Value]) {
        let want: Vec<Map<String, Value>> = specs.iter().filter_map(Value::as_object).filter(|s| !spec_str(s, "path").is_empty() && !gone(&spec_str(s, "path"))).map(with_defaults).collect();
        let paths: Vec<String> = want.iter().map(|s| spec_str(s, "path")).collect();
        let drop: Vec<Rc<Tile>> = self.tiles.borrow().iter().filter(|t| !paths.contains(&spec_str(&t.spec.borrow(), "path"))).cloned().collect();
        for t in drop {
            self.remove(&t, false);
        }
        let have: Vec<String> = self.tiles.borrow().iter().map(|t| spec_str(&t.spec.borrow(), "path")).collect();
        for spec in want {
            if !have.contains(&spec_str(&spec, "path")) {
                let t = self.add_tile(spec);
                if let Some(d) = self.dock() {
                    d.open_up(&t); // its place opens, then it fades in
                }
            }
        }
        if let Some(d) = self.dock() {
            let specs: Vec<Value> = self.tiles.borrow().iter().map(|t| Value::Object(t.spec.borrow().clone())).collect();
            d.cfg.borrow_mut().insert("stacks".into(), Value::Array(specs));
        }
    }

    fn add_tile(&self, spec: Map<String, Value>) -> Rc<Tile> {
        let dock = self.dock().expect("a stack belongs to a Dock");
        let path = spec_str(&spec, "path");
        let name = std::path::Path::new(path.trim_end_matches('/')).file_name().map(|n| n.to_string_lossy().to_string()).unwrap_or_else(|| path.clone());
        let w = self.me.clone();
        let click: TileFn = Rc::new(move |t| {
            if let Some(r) = w.upgrade() {
                r.open_panel(t);
            }
        });
        let w = self.me.clone();
        let menu: TileFn = Rc::new(move |t| {
            if let Some(r) = w.upgrade() {
                stack_menu(&r, t);
            }
        });
        let tile = Tile::new(&dock, &name, Some(gio::ThemedIcon::new("folder").upcast()), click, None, Some(menu), None);
        tile.spec.replace(spec);
        let prev: gtk::Widget = self.tiles.borrow().last().map(|t| t.widget()).unwrap_or_else(|| dock.sep.clone().upcast());
        dock.plate.insert_child_after(&tile.button, Some(&prev));
        self.tiles.borrow_mut().push(tile.clone());
        self.refresh_icon(&tile);
        if let Ok(mon) = gio::File::for_path(&path).monitor_directory(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE) {
            let (w, t) = (self.me.clone(), Rc::downgrade(&tile));
            mon.connect_changed(move |_, _, _, ev| {
                if let (true, Some(r), Some(t)) = (stack_event(ev), w.upgrade(), t.upgrade()) {
                    r.refresh_later(&t);
                }
            });
            tile.stack.monitor.replace(Some(mon));
        }
        tile
    }

    /// A burst of changes (a download writing) re-lists the folder once.
    fn refresh_later(&self, tile: &Rc<Tile>) {
        if tile.stack.src.borrow().is_some() {
            return;
        }
        let (w, t) = (self.me.clone(), Rc::downgrade(tile));
        let src = glib::timeout_add_local_once(Duration::from_millis(REFRESH_MS), move || {
            let (Some(r), Some(t)) = (w.upgrade(), t.upgrade()) else { return };
            t.stack.src.replace(None);
            let mine = r.tiles.borrow().iter().any(|x| Rc::ptr_eq(x, &t));
            if mine && gone(&spec_str(&t.spec.borrow(), "path")) {
                r.remove(&t, true); // the folder was deleted (or put in the Trash)
                return;
            }
            r.refresh_icon_bg(&t);
        });
        tile.stack.src.replace(Some(src));
    }

    fn unwatch(&self, tile: &Tile) {
        if let Some(m) = tile.stack.monitor.borrow_mut().take() {
            m.cancel();
        }
        if let Some(s) = tile.stack.src.borrow_mut().take() {
            s.remove();
        }
    }

    /// The Dock is replaced: stop watching every stack's folder.
    pub fn detach(&self) {
        for t in self.tiles.borrow().iter() {
            self.unwatch(t);
        }
        if let Some((vm, id)) = self.volumes.borrow_mut().take() {
            vm.disconnect(id);
        }
    }

    pub fn refresh_icon(&self, tile: &Tile) {
        let spec = tile.spec.borrow().clone();
        let first = if spec_str(&spec, "display") == "stack" { items(&spec_str(&spec, "path"), &spec_str(&spec, "sort")).into_iter().next() } else { None };
        set_icon(tile, stack_icon(&spec, first.as_ref()));
    }

    /// As refresh_icon, the folder listed in a thread (a big Downloads
    /// folder: every item stat-ed). One listing at a time per tile;
    /// changes meanwhile list it once more.
    fn refresh_icon_bg(&self, tile: &Rc<Tile>) {
        if tile.stack.busy.get() {
            tile.stack.again.set(true);
            return;
        }
        tile.stack.busy.set(true);
        tile.stack.again.set(false);
        let spec = tile.spec.borrow().clone();
        let (path, sort, display) = (spec_str(&spec, "path"), spec_str(&spec, "sort"), spec_str(&spec, "display"));
        let (tx, rx) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let first = if display == "stack" { items(&path, &sort).into_iter().next() } else { None };
            let _ = tx.send(first);
        });
        let (w, t) = (self.me.clone(), Rc::downgrade(tile));
        glib::timeout_add_local(Duration::from_millis(30), move || {
            let first = match rx.try_recv() {
                Ok(f) => f,
                Err(std::sync::mpsc::TryRecvError::Empty) => return glib::ControlFlow::Continue,
                Err(_) => None,
            };
            let (Some(r), Some(t)) = (w.upgrade(), t.upgrade()) else { return glib::ControlFlow::Break };
            t.stack.busy.set(false);
            let mine = r.tiles.borrow().iter().any(|x| Rc::ptr_eq(x, &t));
            if mine && *t.spec.borrow() == spec {
                // (not removed / changed meanwhile)
                set_icon(&t, stack_icon(&spec, first.as_ref()));
            }
            if t.stack.again.get() && mine {
                t.stack.again.set(false);
                r.refresh_later(&t);
            }
            glib::ControlFlow::Break
        });
    }

    pub fn set_spec(&self, tile: &Tile, key: &str, value: &str) {
        tile.spec.borrow_mut().insert(key.into(), json!(value));
        self.save();
        self.refresh_icon(tile);
    }

    // -- the panel ---------------------------------------------------------------------------

    pub fn open_panel(&self, tile: &Rc<Tile>) -> Option<gtk::Popover> {
        let dock = self.dock()?;
        tile.label.popdown();
        let spec = tile.spec.borrow().clone();
        let path = spec_str(&spec, "path");
        let pop = gtk::Popover::builder().css_classes(["stack-panel"]).has_arrow(false).position(dock.away).build();
        let (ox, oy) = match dock.away {
            gtk::PositionType::Left => (-8, 0),
            gtk::PositionType::Right => (8, 0),
            _ => (0, -8),
        };
        pop.set_offset(ox, oy);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&gtk::Label::builder().label(tile.name()).xalign(0.0).css_classes(["stack-title"]).build());
        let found = items(&path, &spec_str(&spec, "sort"));
        if found.is_empty() {
            col.append(&gtk::Label::builder().label(tr!("No items")).css_classes(["stack-empty"]).build());
        } else if spec_str(&spec, "view") == "list" {
            col.append(&self.list(&path, &found, &pop));
        } else {
            col.append(&self.grid(&path, &found, &pop));
        }
        let footer = gtk::Box::builder().css_classes(["stack-footer"]).halign(gtk::Align::End).build();
        let (p, pp) = (pop.clone(), path.clone());
        footer.append(&sonata_ui::controls::push_button(&tr!("Open in Files"), move || {
            open(&gio::File::for_path(&pp));
            p.popdown();
        }, ""));
        col.append(&footer);
        pop.set_child(Some(&col));
        pop.set_parent(&tile.button);
        pop.connect_closed(|p| {
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        });
        dock::menu_opened(&pop); // keeps an auto-hiding Dock visible
        pop.popup();
        Some(pop)
    }

    fn grid(&self, path: &str, found: &[Entry], pop: &gtk::Popover) -> gtk::Widget {
        let flow = gtk::FlowBox::builder().max_children_per_line(GRID_COLS as u32).min_children_per_line(GRID_COLS.min(found.len()) as u32).selection_mode(gtk::SelectionMode::None).homogeneous(true).column_spacing(2).row_spacing(2).build();
        for e in found {
            let file = gio::File::for_path(std::path::Path::new(path).join(&e.name));
            let b = gtk::Button::builder().css_classes(["stack-item"]).build();
            let col = gtk::Box::new(gtk::Orientation::Vertical, 4);
            col.append(&gtk::Image::builder().gicon(&entry_icon(e)).pixel_size(GRID_ICON).build());
            // broken anywhere (WORD_CHAR): a long name with no spaces (a
            // Downloads photo, an .iso) widened its column -- the panel ran
            // across the screen
            col.append(&gtk::Label::builder().label(&e.display).wrap(true).wrap_mode(pango::WrapMode::WordChar).lines(2).justify(gtk::Justification::Center).ellipsize(pango::EllipsizeMode::Middle).max_width_chars(12).width_chars(12).build());
            b.set_child(Some(&col));
            let (f, p) = (file.clone(), pop.clone());
            b.connect_clicked(move |_| {
                open(&f);
                p.popdown();
            });
            drag_source(&b, file);
            flow.append(&b);
        }
        let rows = found.len().div_ceil(GRID_COLS) as i32;
        gtk::ScrolledWindow::builder()
            .hscrollbar_policy(gtk::PolicyType::Never)
            .propagate_natural_width(true)
            .propagate_natural_height(rows <= 3)
            .min_content_height(rows.min(3) * 96)
            .max_content_height(3 * 110)
            .child(&flow)
            .build()
            .upcast()
    }

    fn list(&self, path: &str, found: &[Entry], pop: &gtk::Popover) -> gtk::Widget {
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        for e in found {
            let file = gio::File::for_path(std::path::Path::new(path).join(&e.name));
            let b = gtk::Button::builder().css_classes(["stack-row"]).build();
            let row = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            row.append(&gtk::Image::builder().gicon(&entry_icon(e)).pixel_size(16).build());
            row.append(&gtk::Label::builder().label(&e.display).xalign(0.0).hexpand(true).ellipsize(pango::EllipsizeMode::Middle).max_width_chars(36).build());
            if e.is_dir {
                row.append(&gtk::Image::builder().icon_name("go-next-symbolic").pixel_size(12).build());
            }
            b.set_child(Some(&row));
            let (f, p) = (file.clone(), pop.clone());
            b.connect_clicked(move |_| {
                open(&f);
                p.popdown();
            });
            drag_source(&b, file);
            col.append(&b);
        }
        gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_width(true).propagate_natural_height(true).max_content_height(420).child(&col).build().upcast()
    }
}

pub fn stack_menu(row: &Rc<StackRow>, tile: &Rc<Tile>) {
    let Some(dock) = row.dock() else { return };
    let spec = tile.spec.borrow().clone();
    tile.label.popdown();
    let choice = |key: &'static str, options: &[(&'static str, &'static str)]| -> Vec<Item> {
        let now = spec_str(&spec, key);
        options
            .iter()
            .map(|(k, label)| {
                let (r, t, k) = (Rc::downgrade(row), Rc::downgrade(tile), k.to_string());
                Item::check(&sonata_i18n::gettext(label), now == k, move |on| {
                    if let (true, Some(r), Some(t)) = (on, r.upgrade(), t.upgrade()) {
                        r.set_spec(&t, key, &k);
                    }
                })
            })
            .collect()
    };
    let (r, t) = (Rc::downgrade(row), Rc::downgrade(tile));
    let path = spec_str(&spec, "path");
    dock_menu::popup(&tile.button, &dock, vec![
        vec![
            Item::submenu(&tr!("Sort by"), vec![choice("sort", &SORTS)]),
            Item::submenu(&tr!("Display as"), vec![choice("display", &[("folder", n_!("Folder")), ("stack", n_!("Stack"))])]),
            Item::submenu(&tr!("View content as"), vec![choice("view", &[("grid", n_!("Grid")), ("list", n_!("List"))])]),
        ],
        vec![Item::submenu(&tr!("Options"), vec![vec![Item::new(&tr!("Remove from Dock"), move || {
            if let (Some(r), Some(t)) = (r.upgrade(), t.upgrade()) {
                r.remove(&t, true);
            }
        })]])],
        vec![Item::new(&sonata_i18n::fmt(&tr!("Open \"{name}\""), &[("name", &tile.name())]), move || open(&gio::File::for_path(&path)))],
    ]);
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A package, an archive or a script clicked in a stack is installed,
    /// run or extracted (dock_stack.py's _open), not opened in an app.
    #[test]
    fn stack_opens_packages_the_files_way() {
        let src = include_str!("dock_stack.rs");
        let open = &src[src.find("pub fn open(").unwrap()..];
        assert!(open[..open.find("\n}").unwrap()].contains("sonata_files::packages::open_path("));
    }

    fn e(name: &str, kind: &str, modified: f64, added: f64) -> Entry {
        Entry { name: name.into(), display: name.into(), content_type: kind.into(), is_dir: false, modified, added, thumb: None, icon: None }
    }

    /// Pictures (or a folder with its own icon) in a stack showed as a
    /// plain folder: the file's own icon wasn't asked for.
    #[test]
    fn test_stack_items_lost_their_own_icon() {
        let mut pics = e("Pictures", "inode/directory", 0.0, 0.0);
        pics.is_dir = true;
        pics.icon = Some("folder-pictures".into());
        assert_eq!(IconExt::to_string(&entry_icon(&pics)).unwrap().as_str(), "folder-pictures");
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir(dir.path().join("sub")).unwrap();
        let listed = items(&dir.path().to_string_lossy(), "name");
        assert!(listed[0].icon.is_some(), "standard::icon read");
    }

    #[test]
    fn sorted_as_asked() {
        let mut v = vec![e("b.txt", "text/plain", 3.0, 1.0), e("A.png", "image/png", 1.0, 5.0), e("c.txt", "text/plain", 2.0, 3.0)];
        sort_entries(&mut v, "name");
        assert_eq!(v.iter().map(|x| x.name.as_str()).collect::<Vec<_>>(), ["A.png", "b.txt", "c.txt"]);
        sort_entries(&mut v, "added"); // newest first
        assert_eq!(v[0].name, "A.png");
        sort_entries(&mut v, "modified");
        assert_eq!(v[0].name, "b.txt");
        sort_entries(&mut v, "kind");
        assert_eq!(v[0].name, "A.png");
    }

    /// test_stack_shows_the_folder_icon_by_default (Vini: Downloads showed
    /// its newest photo).
    #[test]
    fn test_stack_shows_the_folder_icon_by_default() {
        let spec = with_defaults(&Map::new());
        assert_eq!(spec_str(&spec, "display"), "folder");
        assert_eq!(spec_str(&spec, "view"), "grid");
        assert_eq!(spec_str(&spec, "sort"), "added");
        let mut own = Map::new();
        own.insert("view".into(), json!("list"));
        assert_eq!(spec_str(&with_defaults(&own), "view"), "list");
    }

    /// test_folder_deleted_while_the_dock_was_off / unplugged drives.
    #[test]
    fn test_gone_folders_leave() {
        let dir = tempfile::tempdir().unwrap();
        assert!(!gone(&dir.path().to_string_lossy()));
        assert!(gone("/no/such/folder/anywhere"));
        assert!(!gone(""));
    }

    #[test]
    fn writes_in_progress_dont_relist() {
        assert!(!stack_event(gio::FileMonitorEvent::Changed));
        assert!(stack_event(gio::FileMonitorEvent::Created));
        assert!(stack_event(gio::FileMonitorEvent::Deleted));
    }

    #[test]
    fn items_listed_newest_first_without_hidden_ones() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::write(dir.path().join("one.txt"), "1").unwrap();
        std::fs::write(dir.path().join(".hidden"), "h").unwrap();
        std::thread::sleep(Duration::from_millis(20));
        std::fs::write(dir.path().join("two.txt"), "2").unwrap();
        let got = items(&dir.path().to_string_lossy(), "added");
        assert_eq!(got.iter().map(|e| e.name.as_str()).collect::<Vec<_>>(), ["two.txt", "one.txt"]);
    }
}
