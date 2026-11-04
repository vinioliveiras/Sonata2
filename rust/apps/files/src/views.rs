//! The Files views (files/views.py; Finder's View menu): Icons, List,
//! Columns. Each shows a ListModel of FileInfo (folder.rs) and calls
//! on_open(info) on double-click / Return; all are virtualised. They share
//! drag and drop (from anywhere on an item; spring-loaded folders), the
//! item under the pointer, select by name / typing, and Finder's in-place
//! rename. Also the tag dots (tags.py's look).

use crate::folder::{self, is_dir, sort_key};
use gtk::prelude::*;
use gtk::{gdk, pango};
use sonata_i18n::{fmt, n_, tr};
use std::cell::{Cell, RefCell};
use std::cmp::Ordering;
use std::collections::HashMap;
use std::rc::{Rc, Weak};

/// spring-loaded folders: hover time before a folder opens
const SPRING_MS: u64 = 900;
const ICON_SIZE: i32 = 64;
const CELL_W: i32 = 96;
const MAX_COLUMNS: u32 = 64;
const COLUMN_W: i32 = 230;
const NAME_W: i32 = 320;
pub const DIR_SIZE: &str = "sonata::dir-size";

/// What the views ask of their window for drag and drop.
pub trait Dnd {
    fn files_for_drag(&self, info: &gio::FileInfo) -> Vec<gio::File>;
    fn drop_files(&self, files: Vec<gio::File>, dest: gio::File, copy: bool) -> bool;
    fn spring_open(&self, info: &gio::FileInfo);
    fn is_dragged(&self, f: &gio::File) -> bool;
    fn drag_started(&self, files: Option<Vec<gio::File>>);
}

pub type OnOpen = Rc<dyn Fn(&gio::FileInfo)>;
pub type OnCommit = Rc<dyn Fn(&gio::FileInfo, &str)>;

// -- tag dots (tags.py) -----------------------------------------------------------------------
fn tags_css() {
    thread_local!(static DONE: Cell<bool> = const { Cell::new(false) });
    if DONE.with(|d| d.replace(true)) {
        return;
    }
    sonata_ui::css::register_module("files/tags.py");
    let rules: String = crate::tags::COLORS.iter().map(|(n, c)| format!(".fs-tag-{} {{ background: {c}; }}\n", n.to_lowercase())).collect();
    sonata_ui::theme::register("files-tag-colors", &rules);
}

pub fn tag_dot(name: &str, size: i32) -> gtk::Box {
    tags_css();
    let d = gtk::Box::builder().css_classes(["fs-tag", &format!("fs-tag-{}", name.to_lowercase())]).valign(gtk::Align::Center).halign(gtk::Align::Center).build();
    d.set_size_request(size, size);
    d
}

/// The colours next to a name. Empty, it takes no room -- unless
/// `reserve` (the icons' grid: the row is always there).
pub fn dots_box(reserve: bool) -> gtk::Box {
    tags_css();
    let b = gtk::Box::builder().css_classes(["fs-tags"]).valign(gtk::Align::Center).visible(reserve).build();
    if reserve {
        b.set_size_request(-1, 8);
    }
    unsafe { b.set_data("reserve", reserve) };
    b
}

/// Put an item's colours in a dots_box().
pub fn show_dots(b: &gtk::Box, tags: &[String]) {
    let names: Vec<&str> = crate::tags::colors(tags).iter().map(|c| c.0).collect();
    let key = names.join(",");
    unsafe {
        if b.data::<String>("names").map(|p| p.as_ref() == &key).unwrap_or(false) {
            return;
        }
        b.set_data("names", key);
    }
    while let Some(c) = b.first_child() {
        b.remove(&c);
    }
    for n in &names {
        b.append(&tag_dot(n, 8));
    }
    let reserve = unsafe { b.data::<bool>("reserve").map(|p| *p.as_ref()).unwrap_or(false) };
    b.set_visible(!names.is_empty() || reserve);
}

/// The context menu's row of colours (Finder): a click adds the tag to
/// every selected item, or removes it when they all have it already.
/// states: tag -> "all"/"some"/"none"; pick(tag, on).
pub fn tags_menu_row(states: &HashMap<String, &'static str>, pick: Rc<dyn Fn(&str, bool)>) -> gtk::Box {
    tags_css();
    let row = gtk::Box::builder().spacing(2).css_classes(["fs-tag-row"]).halign(gtk::Align::Start).build();
    for (name, _) in crate::tags::COLORS {
        let st = states.get(name).copied().unwrap_or("none");
        let tip = if st == "all" { tr!("Remove “{tag}” tag") } else { tr!("Add “{tag}” tag") };
        let b = gtk::Button::builder().css_classes(["fs-tag-pick"]).can_focus(false).tooltip_text(fmt(&tip, &[("tag", &sonata_i18n::gettext(name))])).build();
        let d = tag_dot(name, 14);
        if st != "none" {
            d.append(&gtk::Image::builder().icon_name(if st == "all" { "object-select-symbolic" } else { "list-remove-symbolic" }).hexpand(true).halign(gtk::Align::Center).build());
        }
        b.set_child(Some(&d));
        let p = pick.clone();
        b.connect_clicked(move |b| {
            if let Some(pop) = b.ancestor(gtk::Popover::static_type()).and_downcast::<gtk::Popover>() {
                pop.popdown();
            }
            p(name, st != "all");
        });
        row.append(&b);
    }
    row
}

// -- formatting (Finder style) ----------------------------------------------------------------
thread_local! {
    static KINDS: RefCell<HashMap<String, String>> = RefCell::default();
    static LAUNCHERS: RefCell<HashMap<String, (i64, Option<gio::Icon>, Option<String>)>> = RefCell::default();
}

/// The real type; empty files get the type their name suggests.
pub fn content_type(info: &gio::FileInfo) -> String {
    let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
    if ct == "application/x-zerosize" {
        let (g, _) = gio::content_type_guess(Some(info.display_name().as_str()), &[]);
        if !g.is_empty() {
            return g.to_string();
        }
    }
    ct
}

pub fn kind(info: &gio::FileInfo) -> String {
    if is_dir(info) {
        return tr!("Folder");
    }
    let ct = content_type(info);
    KINDS.with(|k| {
        k.borrow_mut()
            .entry(ct.clone())
            .or_insert_with(|| {
                let d = if ct.is_empty() { tr!("Document") } else { gio::content_type_get_description(&ct).to_string() };
                let mut c = d.chars();
                c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
            })
            .clone()
    })
}

/// A folder's counted size, or None (not counted / not asked).
pub fn dir_size(info: &gio::FileInfo) -> Option<u64> {
    info.attribute_string(DIR_SIZE).and_then(|v| v.parse().ok())
}

pub fn size(info: &gio::FileInfo) -> String {
    if is_dir(info) {
        return dir_size(info).map(sonata_ui::fmt::size).unwrap_or_else(|| "--".into());
    }
    sonata_ui::fmt::size(info.size().max(0) as u64)
}

/// Count a folder's size in the background (Calculate all sizes);
/// on_done(info) on the main loop.
pub fn count_dir(info: &gio::FileInfo, on_done: impl FnOnce(&gio::FileInfo) + 'static) {
    let Some(path) = folder::file_of(info).and_then(|f| f.path()) else { return };
    if info.has_attribute(DIR_SIZE) {
        return;
    }
    info.set_attribute_string(DIR_SIZE, ""); // asked: not again
    let info = info.clone();
    glib::spawn_future_local(async move {
        let total = gio::spawn_blocking(move || walk_size(&path)).await.unwrap_or(0);
        info.set_attribute_string(DIR_SIZE, &total.to_string());
        on_done(&info);
    });
}

fn walk_size(p: &std::path::Path) -> u64 {
    let mut total = 0;
    let mut stack = vec![p.to_path_buf()];
    while let Some(d) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&d) else { continue };
        for e in rd.flatten() {
            let Ok(m) = e.path().symlink_metadata() else { continue };
            if m.is_dir() {
                stack.push(e.path());
            } else {
                total += m.len();
            }
        }
    }
    total
}

/// "Today at 14:32", "Yesterday at 09:10", "28 Sep 2026 at 10:00".
pub fn date(info: &gio::FileInfo, attr: &str) -> String {
    let secs = info.attribute_uint64(attr);
    let Some(dt) = (secs > 0).then(|| glib::DateTime::from_unix_local(secs as i64).ok()).flatten() else { return "--".into() };
    let Ok(now) = glib::DateTime::now_local() else { return "--".into() };
    // whole days between the two dates (midnights; rounded: a DST day has 23 or 25 hours)
    let day = |d: &glib::DateTime| glib::DateTime::from_local(d.year(), d.month(), d.day_of_month(), 0, 0, 0.0).map(|m| (m.to_unix() as f64 / 86400.0).round() as i64).unwrap_or(0);
    let t = dt.format("%H:%M").map(|s| s.to_string()).unwrap_or_default();
    match day(&now) - day(&dt) {
        0 => fmt(&tr!("Today at {time}"), &[("time", &t)]),
        1 => fmt(&tr!("Yesterday at {time}"), &[("time", &t)]),
        _ => dt.format(&tr!("%-d %b %Y at %H:%M")).map(|s| s.to_string()).unwrap_or_default(),
    }
}

/// An app shortcut (.desktop): (its app's icon, name), cached by mtime.
fn launcher(info: &gio::FileInfo) -> (Option<gio::Icon>, Option<String>) {
    if !info.name().to_string_lossy().ends_with(".desktop") {
        return (None, None);
    }
    let Some(path) = folder::file_of(info).and_then(|f| f.path()) else { return (None, None) };
    let stamp = info.modification_date_time().map(|d| d.to_unix()).unwrap_or(0);
    let key = path.display().to_string();
    if let Some((s, i, n)) = LAUNCHERS.with(|l| l.borrow().get(&key).cloned()) {
        if s == stamp {
            return (i, n);
        }
    }
    let app = gio::DesktopAppInfo::from_filename(&path);
    let got = (app.as_ref().map(|a| sonata_ui::icons::app_icon(a.upcast_ref())), app.as_ref().map(|a| a.display_name().to_string()));
    LAUNCHERS.with(|l| l.borrow_mut().insert(key, (stamp, got.0.clone(), got.1.clone())));
    got
}

/// The name shown: an app shortcut shows its app's name, everything else its file name.
pub fn label(info: &gio::FileInfo) -> String {
    launcher(info).1.unwrap_or_else(|| info.display_name().to_string())
}

fn hidden(info: &gio::FileInfo) -> bool {
    info.is_hidden() || info.is_backup()
}

/// The item's icon. small (16 px rows): drawn from the 32 px artwork, like
/// Finder's full-colour small icons. Pictures and videos: their thumbnail
/// once ready (recycled rows: only if the image still shows this item).
pub fn set_icon(image: &gtk::Image, info: &gio::FileInfo, small: bool) {
    let mut gicon = info.icon();
    if let Some(a) = launcher(info).0 {
        gicon = Some(a);
    } else if info.content_type().as_deref() == Some("application/x-zerosize") {
        gicon = Some(gio::content_type_get_icon(&content_type(info)));
    }
    let gicon = gicon.unwrap_or_else(|| gio::ThemedIcon::new("text-x-generic").upcast());
    if small {
        image.set_paintable(sonata_ui::icons::paintable(image, &gicon, 32).as_ref());
    } else {
        sonata_ui::icons::set_image(image, &gicon);
    }
    unsafe { image.set_data("thumb-for", info.clone()) };
    let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
    if crate::thumbs::wanted(&ct) {
        if let Some(f) = folder::file_of(info) {
            let (img, inf) = (image.downgrade(), info.clone());
            crate::thumbs::request(info, &f, move |tex| {
                if let Some(img) = img.upgrade() {
                    let still = unsafe { img.data::<gio::FileInfo>("thumb-for").map(|p| p.as_ref() == &inf).unwrap_or(false) };
                    if still {
                        img.set_paintable(Some(tex));
                    }
                }
            });
        }
    }
}

/// The icon with a shortcut's arrow over its corner (hidden; show_badge).
fn icon_with_badge(image: &gtk::Image) -> (gtk::Overlay, gtk::Image) {
    let over = gtk::Overlay::builder().child(image).halign(image.halign()).build();
    let badge = gtk::Image::builder().icon_name("sonata-shortcut-arrow-symbolic").css_classes(["fs-link-badge"]).halign(gtk::Align::Start).valign(gtk::Align::End).visible(false).can_target(false).build();
    over.add_overlay(&badge);
    (over, badge)
}

// -- sorts --------------------------------------------------------------------------------------
fn u64_of(info: &gio::FileInfo, attr: &str) -> u64 {
    info.attribute_uint64(attr)
}

fn size_key(info: &gio::FileInfo) -> i64 {
    if is_dir(info) { dir_size(info).map_or(-1, |n| n as i64) } else { info.size() }
}

/// Sort By and the list's headers: English title -> compare, ascending.
pub const SORTS: [&str; 6] = [n_!("Name"), n_!("Kind"), n_!("Date Modified"), n_!("Size"), n_!("Date Created"), n_!("Date Last Opened")];

pub fn compare(title: &str, a: &gio::FileInfo, b: &gio::FileInfo) -> Ordering {
    match title {
        "Kind" => kind(a).to_lowercase().cmp(&kind(b).to_lowercase()).then_with(|| sort_key(a).cmp(&sort_key(b))),
        "Date Modified" => u64_of(a, "time::modified").cmp(&u64_of(b, "time::modified")),
        "Size" => size_key(a).cmp(&size_key(b)),
        "Date Created" => u64_of(a, "time::created").cmp(&u64_of(b, "time::created")),
        "Date Last Opened" => u64_of(a, "time::access").cmp(&u64_of(b, "time::access")),
        _ => sort_key(a).cmp(&sort_key(b)),
    }
}

/// Folders before files whatever the sort and its direction.
pub fn folders_first(a: &gio::FileInfo, b: &gio::FileInfo) -> Ordering {
    (!is_dir(a)).cmp(&!is_dir(b))
}

/// the list's columns: (title, default width, shown at first); Name always shows
pub const LIST_COLUMNS: [(&str, i32, bool); 5] = [("Date Modified", 190, true), ("Date Created", 190, false), ("Date Last Opened", 190, false), ("Size", 90, true), ("Kind", 160, true)];
/// Sort By's direction for each (Finder: newest and biggest first)
pub const SORT_BY: [(&str, bool); 4] = [("Name", false), ("Kind", false), ("Date Modified", true), ("Size", true)];

fn info_of(o: &glib::Object) -> Option<gio::FileInfo> {
    o.clone().downcast::<gio::FileInfo>().ok()
}

// -- cells ----------------------------------------------------------------------------------------
/// One cell's parts, kept on its box.
struct Parts {
    img: gtk::Image,
    badge: Option<gtk::Image>,
    lbl: gtk::Label,
    tags: gtk::Box,
    /// icons: the name sits over a kept two-line space (rename in place over it)
    over: Option<gtk::Overlay>,
    chev: Option<gtk::Image>,
    info: RefCell<Option<gio::FileInfo>>,
    spring: RefCell<Option<glib::SourceId>>,
}

fn parts(w: &impl IsA<glib::Object>) -> Option<Rc<Parts>> {
    unsafe { w.data::<Rc<Parts>>("fs-cell").map(|p| p.as_ref().clone()) }
}

fn set_parts(w: &impl IsA<glib::Object>, p: Rc<Parts>) {
    unsafe { w.set_data("fs-cell", p) };
}

/// The FileInfo of the item at (x, y) of a view's widget, or None.
pub fn info_under(widget: &gtk::Widget, x: f64, y: f64) -> Option<gio::FileInfo> {
    let mut w = widget.pick(x, y, gtk::PickFlags::DEFAULT);
    while let Some(cur) = w {
        if cur == *widget {
            return None;
        }
        if let Some(i) = parts(&cur).and_then(|p| p.info.borrow().clone()) {
            return Some(i);
        }
        if let Some(i) = unsafe { cur.data::<gio::FileInfo>("fs-info").map(|p| p.as_ref().clone()) } {
            return Some(i);
        }
        if ["child", "row", "cell"].contains(&cur.css_name().as_str()) {
            if let Some(i) = find_info(&cur, 4) {
                return Some(i);
            }
        }
        w = cur.parent();
    }
    None
}

fn find_info(w: &gtk::Widget, depth: u32) -> Option<gio::FileInfo> {
    if let Some(i) = parts(w).and_then(|p| p.info.borrow().clone()) {
        return Some(i);
    }
    if depth == 0 {
        return None;
    }
    let mut c = w.first_child();
    while let Some(ch) = c {
        if let Some(i) = find_info(&ch, depth - 1) {
            return Some(i);
        }
        c = ch.next_sibling();
    }
    None
}

/// The common view behaviour (views.py's _Cells).
pub struct Cells {
    /// FileInfo -> the cell box showing it
    pub cells: RefCell<HashMap<gio::FileInfo, gtk::Widget>>,
    pub dnd: RefCell<Option<Weak<dyn Dnd>>>,
    drag_info: RefCell<Option<gio::FileInfo>>,
}

impl Cells {
    fn new() -> Rc<Self> {
        Rc::new(Self { cells: RefCell::default(), dnd: RefCell::default(), drag_info: RefCell::default() })
    }

    fn dnd(&self) -> Option<Rc<dyn Dnd>> {
        self.dnd.borrow().as_ref().and_then(Weak::upgrade)
    }

    fn track(&self, b: &gtk::Widget, info: &gio::FileInfo) {
        if let Some(p) = parts(b) {
            p.info.replace(Some(info.clone()));
        }
        self.cells.borrow_mut().insert(info.clone(), b.clone());
    }

    fn untrack(&self, b: &gtk::Widget) {
        let Some(p) = parts(b) else { return };
        if let Some(info) = p.info.take() {
            let mut cells = self.cells.borrow_mut();
            if cells.get(&info) == Some(b) {
                cells.remove(&info);
            }
        }
    }

    /// Folders accept drops, and open when a drag hovers on them (spring-loaded).
    fn dnd_cell(self: &Rc<Self>, b: &gtk::Widget) {
        let tgt = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE);
        let bw = b.downgrade();
        tgt.connect_accept(move |_, _| bw.upgrade().and_then(|b| parts(&b)).and_then(|p| p.info.borrow().clone()).is_some_and(|i| is_dir(&i)));
        let (cw, bw) = (Rc::downgrade(self), b.downgrade());
        tgt.connect_enter(move |_, _, _| {
            let (Some(c), Some(b)) = (cw.upgrade(), bw.upgrade()) else { return gdk::DragAction::empty() };
            c.drag_enter(&b)
        });
        let bw = b.downgrade();
        tgt.connect_leave(move |_| {
            if let Some(b) = bw.upgrade() {
                drag_leave(&b);
            }
        });
        let (cw, bw) = (Rc::downgrade(self), b.downgrade());
        tgt.connect_drop(move |t, v, _, _| {
            let (Some(c), Some(b)) = (cw.upgrade(), bw.upgrade()) else { return false };
            drag_leave(&b);
            let info = parts(&b).and_then(|p| p.info.borrow().clone());
            let (Some(info), Some(dnd), Ok(list)) = (info.filter(is_dir), c.dnd(), v.get::<gdk::FileList>()) else { return false };
            let copy = t.current_event_state().contains(gdk::ModifierType::CONTROL_MASK);
            folder::file_of(&info).is_some_and(|f| dnd.drop_files(list.files(), f, copy))
        });
        b.add_controller(tgt);
    }

    fn drag_enter(&self, b: &gtk::Widget) -> gdk::DragAction {
        let Some(p) = parts(b) else { return gdk::DragAction::empty() };
        let Some(info) = p.info.borrow().clone().filter(is_dir) else { return gdk::DragAction::empty() };
        let dnd = self.dnd();
        if dnd.as_ref().zip(folder::file_of(&info)).is_some_and(|(d, f)| d.is_dragged(&f)) {
            return gdk::DragAction::empty(); // the dragged folder itself: no target, no spring
        }
        b.add_css_class("drop-target");
        let d = self.dnd.borrow().clone();
        let src = glib::timeout_add_local_once(std::time::Duration::from_millis(SPRING_MS), move || {
            if let Some(d) = d.as_ref().and_then(Weak::upgrade) {
                d.spring_open(&info);
            }
        });
        if let Some(old) = p.spring.replace(Some(src)) {
            old.remove();
        }
        gdk::DragAction::MOVE
    }

    /// Pressing on an item and moving drags the item (with the selection
    /// it belongs to) -- anywhere on it. Only a press on empty space starts
    /// the rubber band. One drag source for the whole list (capture).
    fn dnd_list(self: &Rc<Self>, widget: &gtk::Widget, rubberband: bool) {
        let src = gtk::DragSource::builder().actions(gdk::DragAction::COPY | gdk::DragAction::MOVE).propagation_phase(gtk::PropagationPhase::Capture).build();
        let (cw, ww) = (Rc::downgrade(self), widget.downgrade());
        src.connect_prepare(move |_, x, y| {
            let (c, w) = (cw.upgrade()?, ww.upgrade()?);
            let picked = w.pick(x, y, gtk::PickFlags::DEFAULT);
            if picked.as_ref().is_some_and(|p| p.is::<gtk::Text>() || p.ancestor(gtk::Text::static_type()).is_some()) {
                return None; // selecting text in the rename field
            }
            let info = info_under(&w, x, y)?;
            let dnd = c.dnd()?;
            let files = dnd.files_for_drag(&info);
            c.drag_info.replace(Some(info));
            dnd.drag_started(Some(files.clone()));
            Some(crate::ops::file_content(&files, false, true))
        });
        let (cw, ww) = (Rc::downgrade(self), widget.downgrade());
        src.connect_drag_begin(move |s, _| {
            let (Some(c), Some(w)) = (cw.upgrade(), ww.upgrade()) else { return };
            let Some(info) = c.drag_info.borrow().clone() else { return };
            // the file icon follows the pointer
            let paint: Option<gdk::Paintable> = info.attribute_byte_string("thumbnail::path").and_then(|t| gdk::Texture::from_filename(t.as_str()).ok()).map(|t| t.upcast()).or_else(|| sonata_ui::icons::paintable(&w, &info.icon().unwrap_or_else(|| gio::ThemedIcon::new("text-x-generic").upcast()), 64));
            if let Some(p) = paint {
                s.set_icon(Some(&p), 32, 32);
            }
        });
        let cw = Rc::downgrade(self);
        src.connect_drag_end(move |_, _, _| {
            if let Some(c) = cw.upgrade() {
                c.drag_info.replace(None);
                if let Some(d) = c.dnd() {
                    d.drag_started(None);
                }
            }
        });
        widget.add_controller(src);
        if rubberband {
            let press = gtk::GestureClick::builder().button(0).propagation_phase(gtk::PropagationPhase::Capture).build();
            let ww = widget.downgrade();
            press.connect_pressed(move |_, _, x, y| {
                let Some(w) = ww.upgrade() else { return };
                let on_item = info_under(&w, x, y).is_some();
                if let Some(g) = w.downcast_ref::<gtk::GridView>() {
                    g.set_enable_rubberband(!on_item);
                } else if let Some(c) = w.downcast_ref::<gtk::ColumnView>() {
                    c.set_enable_rubberband(!on_item);
                }
            });
            widget.add_controller(press);
        }
    }

    /// Edit the name in place: the label becomes a field with the name
    /// (without extension) selected; Return / a click elsewhere commits,
    /// Escape cancels.
    fn begin_rename(&self, info: &gio::FileInfo, on_commit: OnCommit) -> bool {
        let Some(b) = self.cells.borrow().get(info).cloned() else { return false };
        let Some(p) = parts(&b) else { return false };
        inline_rename(&b, &p, info, on_commit);
        true
    }
}

fn drag_leave(b: &gtk::Widget) {
    b.remove_css_class("drop-target");
    if let Some(p) = parts(b) {
        if let Some(s) = p.spring.take() {
            s.remove();
        }
    }
}

fn inline_rename(b: &gtk::Widget, p: &Rc<Parts>, info: &gio::FileInfo, on_commit: OnCommit) {
    let name = info.display_name().to_string();
    let entry = gtk::Entry::builder().text(&name).css_classes(["fs-rename"]).build();
    match &p.over {
        // icons: the field over the name's (always two-line) place -- the
        // cell and the grid keep their size
        Some(over) => {
            entry.set_halign(gtk::Align::Fill);
            entry.set_valign(gtk::Align::Start);
            entry.set_width_chars(1);
            entry.set_max_width_chars(1);
            p.lbl.set_opacity(0.0);
            over.add_overlay(&entry);
        }
        None => {
            entry.set_hexpand(true);
            entry.set_width_chars((name.chars().count() as i32 + 2).clamp(8, 24));
            p.lbl.set_visible(false);
            if let Some(bx) = b.downcast_ref::<gtk::Box>() {
                bx.insert_child_after(&entry, Some(&p.lbl));
            }
        }
    }
    let done = Rc::new(Cell::new(false));
    let finish = {
        let (e, p, b, info, name, done) = (entry.downgrade(), p.clone(), b.downgrade(), info.clone(), name.clone(), done.clone());
        Rc::new(move |commit: bool| {
            if done.replace(true) {
                return;
            }
            let Some(entry) = e.upgrade() else { return };
            let new = entry.text().trim().to_string();
            match (&p.over, b.upgrade()) {
                (Some(over), _) => over.remove_overlay(&entry),
                (None, Some(b)) => {
                    if let Some(bx) = b.downcast_ref::<gtk::Box>() {
                        bx.remove(&entry);
                    }
                }
                _ => {}
            }
            p.lbl.set_visible(true);
            p.lbl.set_opacity(1.0);
            if commit && !new.is_empty() && new != name {
                on_commit(&info, &new);
            }
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
    let (s, e) = crate::ops::rename_selection(&name, is_dir(info));
    entry.select_region(s, e);
}

fn fold(text: &str) -> String {
    crate::search::fold(text)
}

fn selected_of(sel: &impl IsA<gtk::SelectionModel>, model: &impl IsA<gio::ListModel>) -> Vec<gio::FileInfo> {
    let bits = sel.selection();
    (0..bits.size()).filter_map(|i| model.item(bits.nth(i as u32)).and_then(|o| info_of(&o))).collect()
}

fn position_of(model: &impl IsA<gio::ListModel>, name: &str) -> Option<u32> {
    (0..model.n_items()).find(|i| model.item(*i).and_then(|o| info_of(&o)).is_some_and(|f| f.name().to_string_lossy() == name))
}

/// What the window does with any view.
pub trait View {
    fn widget(&self) -> gtk::Widget;
    fn cells(&self) -> Rc<Cells>;
    fn model(&self) -> gio::ListModel;
    fn selection(&self) -> gtk::SelectionModel;
    fn scroll_to(&self, pos: u32);
    fn selected(&self) -> Vec<gio::FileInfo> {
        selected_of(&self.selection(), &self.model())
    }
    fn select_all(&self) {
        self.selection().select_all();
    }
    fn unselect_all(&self) {
        self.selection().unselect_all();
    }
    fn focus(&self) {
        self.widget().grab_focus();
    }
    fn scroll_top(&self) {
        if self.model().n_items() > 0 {
            self.scroll_to(0);
        }
    }
    fn info_at(&self, x: f64, y: f64) -> Option<gio::FileInfo> {
        info_under(&self.widget(), x, y)
    }
    fn select_name(&self, name: &str) -> bool {
        match position_of(&self.model(), name) {
            Some(p) => {
                self.selection().select_item(p, true);
                self.scroll_to(p);
                true
            }
            None => false,
        }
    }
    /// Type to select: the first item, in view order, whose name starts with
    /// `prefix` (case- and accent-insensitive).
    fn select_prefix(&self, prefix: &str) -> bool {
        let key = fold(prefix);
        let m = self.model();
        for i in 0..m.n_items() {
            if m.item(i).and_then(|o| info_of(&o)).is_some_and(|f| fold(&f.display_name()).starts_with(&key)) {
                self.selection().select_item(i, true);
                self.scroll_to(i);
                return true;
            }
        }
        false
    }
    /// Right-click on an unselected item selects just it (Finder).
    fn ensure_selected(&self, info: &gio::FileInfo) {
        let m = self.model();
        if let Some(i) = (0..m.n_items()).find(|i| m.item(*i).and_then(|o| info_of(&o)).as_ref() == Some(info)) {
            if !self.selection().is_selected(i) {
                self.selection().select_item(i, true);
            }
        }
    }
    fn begin_rename(&self, info: &gio::FileInfo, on_commit: OnCommit) {
        if self.cells().begin_rename(info, on_commit.clone()) {
            return;
        }
        // not drawn yet: scroll to it, then rename
        if let Some(p) = position_of(&self.model(), &info.name().to_string_lossy()) {
            self.scroll_to(p);
            let (c, i) = (self.cells(), info.clone());
            glib::timeout_add_local_once(std::time::Duration::from_millis(80), move || {
                c.begin_rename(&i, on_commit);
            });
        }
    }
    /// Off: the view lets go of the folder's items while the window shows
    /// another view (GTK filled cells for all three on every change).
    fn show_items(&self, on: bool);
    fn sort_state(&self) -> (String, bool) {
        ("Name".into(), false)
    }
    fn set_sort(&self, _title: &str, _descending: bool) {}
    fn set_dnd(&self, d: Weak<dyn Dnd>) {
        self.cells().dnd.replace(Some(d));
    }
}

// -- Icons ------------------------------------------------------------------------------------
/// Icons in a grid, sorted by name unless View > Sort By says otherwise.
pub struct IconsView {
    pub grid: gtk::GridView,
    pub sorted: gtk::SortListModel,
    pub selection: gtk::MultiSelection,
    source: gio::ListModel,
    cells: Rc<Cells>,
    sort: Rc<RefCell<(String, bool)>>,
    sorter: gtk::CustomSorter,
}

impl IconsView {
    pub fn new(model: &gio::ListModel, on_open: OnOpen) -> Rc<Self> {
        sonata_ui::css::register_module("files/views.py");
        let sort: Rc<RefCell<(String, bool)>> = Rc::new(RefCell::new(("Name".into(), false)));
        let s = sort.clone();
        let sorter = gtk::CustomSorter::new(move |a, b| {
            let (Some(a), Some(b)) = (info_of(a), info_of(b)) else { return gtk::Ordering::Equal };
            let (title, desc) = s.borrow().clone();
            let c = compare(&title, &a, &b);
            folders_first(&a, &b).then(if desc { c.reverse() } else { c }).into()
        });
        let sorted = gtk::SortListModel::new(Some(model.clone()), Some(sorter.clone()));
        let selection = gtk::MultiSelection::new(Some(sorted.clone()));
        let cells = Cells::new();
        let f = gtk::SignalListItemFactory::new();
        let c = cells.clone();
        f.connect_setup(move |_, item| {
            let item = item.downcast_ref::<gtk::ListItem>().unwrap();
            let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).halign(gtk::Align::Center).build();
            b.set_size_request(CELL_W, -1);
            let img = gtk::Image::builder().pixel_size(ICON_SIZE).css_classes(["fs-icon"]).halign(gtk::Align::Center).build();
            let lbl = gtk::Label::builder().wrap(true).wrap_mode(pango::WrapMode::WordChar).lines(2).max_width_chars(12).ellipsize(pango::EllipsizeMode::Middle).justify(gtk::Justification::Center).halign(gtk::Align::Center).valign(gtk::Align::Start).css_classes(["fs-name"]).build();
            // every cell the same height (Finder's grid): two lines of name are
            // kept (an invisible two-line label sets it) and the tag dots' row
            // too, so a rename or a tag never moves the grid
            let space = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).can_target(false).build();
            space.append(&gtk::Label::builder().label("X\nX").css_classes(["fs-name"]).opacity(0.0).build());
            space.append(&dots_box(true));
            let name = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).valign(gtk::Align::Start).build();
            let tags = dots_box(false);
            tags.set_halign(gtk::Align::Center);
            name.append(&lbl);
            name.append(&tags);
            let over = gtk::Overlay::builder().child(&space).build();
            over.add_overlay(&name);
            let (io, badge) = icon_with_badge(&img);
            b.append(&io);
            b.append(&over);
            set_parts(&b, Rc::new(Parts { img, badge: Some(badge), lbl, tags, over: Some(over), chev: None, info: RefCell::default(), spring: RefCell::default() }));
            c.dnd_cell(b.upcast_ref());
            item.set_child(Some(&b));
        });
        let c = cells.clone();
        f.connect_bind(move |_, item| bind_cell(&c, item, false, None));
        let c = cells.clone();
        f.connect_unbind(move |_, item| {
            if let Some(b) = item.downcast_ref::<gtk::ListItem>().and_then(|i| i.child()) {
                c.untrack(&b);
            }
        });
        let grid = gtk::GridView::builder().model(&selection).factory(&f).max_columns(MAX_COLUMNS).min_columns(1).enable_rubberband(true).css_classes(["fs-icons"]).build();
        // GTK keeps max_columns x 32 cells made (2,049 for a big folder): the
        // most the view's width can hold is enough
        grid.connect_hadjustment_notify(|g| {
            if let Some(adj) = g.hadjustment() {
                let gw = g.downgrade();
                adj.connect_changed(move |a| {
                    if let Some(g) = gw.upgrade() {
                        let w = a.page_size();
                        if w > 0.0 {
                            let n = ((w as i32 / CELL_W + 1) as u32).clamp(1, MAX_COLUMNS);
                            if n != g.max_columns() {
                                g.set_max_columns(n);
                            }
                        }
                    }
                });
            }
        });
        cells.dnd_list(grid.upcast_ref(), true);
        let s2 = sorted.clone();
        grid.connect_activate(move |_, pos| {
            if let Some(i) = s2.item(pos).and_then(|o| info_of(&o)) {
                on_open(&i);
            }
        });
        Rc::new(Self { grid, sorted, selection, source: model.clone(), cells, sort, sorter })
    }
}

fn bind_cell(c: &Rc<Cells>, item: &glib::Object, small: bool, mine: Option<bool>) {
    let item = item.downcast_ref::<gtk::ListItem>().unwrap();
    let (Some(info), Some(b)) = (item.item().and_then(|o| info_of(&o)), item.child()) else { return };
    let Some(p) = parts(&b) else { return };
    set_icon(&p.img, &info, small);
    if let Some(badge) = &p.badge {
        badge.set_visible(crate::ops::is_shortcut(&info, folder::file_of(&info).as_ref(), mine));
    }
    p.lbl.set_label(&label(&info));
    show_dots(&p.tags, &crate::tags::of_info(&info));
    if let Some(ch) = &p.chev {
        ch.set_visible(is_dir(&info));
    }
    if hidden(&info) {
        b.add_css_class("fs-hidden");
    } else {
        b.remove_css_class("fs-hidden");
    }
    c.track(&b, &info);
}

impl View for IconsView {
    fn widget(&self) -> gtk::Widget {
        self.grid.clone().upcast()
    }
    fn cells(&self) -> Rc<Cells> {
        self.cells.clone()
    }
    fn model(&self) -> gio::ListModel {
        self.sorted.clone().upcast()
    }
    fn selection(&self) -> gtk::SelectionModel {
        self.selection.clone().upcast()
    }
    fn scroll_to(&self, pos: u32) {
        self.grid.scroll_to(pos, gtk::ListScrollFlags::FOCUS, None);
    }
    fn show_items(&self, on: bool) {
        if self.sorted.model().is_some() != on {
            self.sorted.set_model(if on { Some(&self.source) } else { None });
        }
    }
    fn sort_state(&self) -> (String, bool) {
        self.sort.borrow().clone()
    }
    fn set_sort(&self, title: &str, descending: bool) {
        let title = if SORTS.contains(&title) { title } else { "Name" };
        if *self.sort.borrow() != (title.to_string(), descending) {
            self.sort.replace((title.into(), descending));
            self.sorter.changed(gtk::SorterChange::Different);
        }
    }
}

// -- List -------------------------------------------------------------------------------------
fn column_widths() -> serde_json::Map<String, serde_json::Value> {
    sonata_config::load("files", &serde_json::json!({"list_columns": {}})).get("list_columns").and_then(|v| v.as_object()).cloned().unwrap_or_default()
}

thread_local! {
    static WIDTH_SAVE: RefCell<HashMap<String, glib::SourceId>> = RefCell::default();
}

/// Remembered a moment after the drag (not on every pixel).
fn save_column_width(title: &str, width: i32) {
    if width <= 0 {
        return;
    }
    let t = title.to_string();
    WIDTH_SAVE.with(|w| {
        if let Some(s) = w.borrow_mut().remove(&t) {
            s.remove();
        }
    });
    let t2 = t.clone();
    let id = glib::timeout_add_local_once(std::time::Duration::from_millis(400), move || {
        WIDTH_SAVE.with(|w| w.borrow_mut().remove(&t2));
        let mut cols = column_widths();
        cols.insert(t2.clone(), serde_json::json!(width));
        let _ = sonata_config::update("files", &serde_json::json!({"list_columns": cols}));
    });
    WIDTH_SAVE.with(|w| w.borrow_mut().insert(t, id));
}

/// Name / Date Modified / Size / Kind, sortable by clicking a header;
/// folders always first, in either direction.
pub struct ListView {
    pub view: gtk::ColumnView,
    pub sorted: gtk::SortListModel,
    pub selection: gtk::MultiSelection,
    source: gio::ListModel,
    cells: Rc<Cells>,
    pub columns: Vec<(&'static str, gtk::ColumnViewColumn)>,
    pub count_sizes: Rc<Cell<bool>>,
    /// a header clicked: (title, descending)
    pub on_sort: RefCell<Option<Box<dyn Fn((String, bool))>>>,
    sorting: Cell<bool>,
}

impl ListView {
    pub fn new(model: &gio::ListModel, on_open: OnOpen) -> Rc<Self> {
        sonata_ui::css::register_module("files/views.py");
        let view = gtk::ColumnView::builder().show_column_separators(false).show_row_separators(false).enable_rubberband(true).reorderable(true).css_classes(["fs-list"]).build();
        fill_last(&view);
        // folders first, then the clicked column's order (its direction never moves them)
        let both = gtk::MultiSorter::new();
        both.append(gtk::CustomSorter::new(|a, b| match (info_of(a), info_of(b)) {
            (Some(a), Some(b)) => folders_first(&a, &b).into(),
            _ => gtk::Ordering::Equal,
        }));
        if let Some(s) = view.sorter() {
            both.append(s);
        }
        let sorted = gtk::SortListModel::new(Some(model.clone()), Some(both));
        let selection = gtk::MultiSelection::new(Some(sorted.clone()));
        view.set_model(Some(&selection));
        let cells = Cells::new();
        let count_sizes = Rc::new(Cell::new(false));
        let mut columns = Vec::new();
        let saved = column_widths();
        let mk_col = |title: &'static str, f: &gtk::SignalListItemFactory, width: i32| {
            let col = gtk::ColumnViewColumn::builder().title(sonata_i18n::gettext(title)).factory(f).resizable(true).build();
            col.set_sorter(Some(&gtk::CustomSorter::new(move |a, b| match (info_of(a), info_of(b)) {
                (Some(a), Some(b)) => compare(title, &a, &b).into(),
                _ => gtk::Ordering::Equal,
            })));
            let w = saved.get(title).and_then(|v| v.as_i64()).filter(|w| *w > 0).map(|w| w as i32).unwrap_or(width);
            col.set_fixed_width(w);
            col.connect_fixed_width_notify(move |c| save_column_width(title, c.fixed_width()));
            view.append_column(&col);
            col
        };
        // Name
        let nf = gtk::SignalListItemFactory::new();
        let c = cells.clone();
        nf.connect_setup(move |_, item| {
            let item = item.downcast_ref::<gtk::ListItem>().unwrap();
            let b = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            let img = gtk::Image::builder().pixel_size(16).build();
            let lbl = gtk::Label::builder().xalign(0.0).ellipsize(pango::EllipsizeMode::Middle).hexpand(true).build();
            let tags = dots_box(false);
            b.append(&img);
            b.append(&lbl);
            b.append(&tags);
            set_parts(&b, Rc::new(Parts { img, badge: None, lbl, tags, over: None, chev: None, info: RefCell::default(), spring: RefCell::default() }));
            c.dnd_cell(b.upcast_ref());
            item.set_child(Some(&b));
        });
        let c = cells.clone();
        nf.connect_bind(move |_, item| bind_cell(&c, item, true, None));
        let c = cells.clone();
        nf.connect_unbind(move |_, item| {
            if let Some(b) = item.downcast_ref::<gtk::ListItem>().and_then(|i| i.child()) {
                c.untrack(&b);
            }
        });
        let name = mk_col("Name", &nf, NAME_W);
        let text_factory = |xalign: f32, text: Rc<dyn Fn(&gio::FileInfo, &gtk::Label) -> String>| {
            let f = gtk::SignalListItemFactory::new();
            f.connect_setup(move |_, item| item.downcast_ref::<gtk::ListItem>().unwrap().set_child(Some(&gtk::Label::builder().xalign(xalign).ellipsize(pango::EllipsizeMode::End).css_classes(["fs-dim"]).build())));
            f.connect_bind(move |_, item| {
                let item = item.downcast_ref::<gtk::ListItem>().unwrap();
                let (Some(info), Some(lbl)) = (item.item().and_then(|o| info_of(&o)), item.child().and_downcast::<gtk::Label>()) else { return };
                lbl.set_label(&text(&info, &lbl));
                unsafe { lbl.set_data("fs-info", info) }; // right-click anywhere on the row
            });
            f
        };
        for (title, width, _) in LIST_COLUMNS {
            let f = match title {
                "Size" => {
                    let cs = count_sizes.clone();
                    text_factory(1.0, Rc::new(move |info, lbl| {
                        if cs.get() && is_dir(info) && !info.has_attribute(DIR_SIZE) {
                            let (l, i) = (lbl.downgrade(), info.clone());
                            count_dir(info, move |done| {
                                if let Some(l) = l.upgrade() {
                                    if unsafe { l.data::<gio::FileInfo>("fs-info").map(|p| p.as_ref() == &i).unwrap_or(false) } {
                                        l.set_label(&size(done));
                                    }
                                }
                            });
                        }
                        size(info)
                    }))
                }
                "Kind" => text_factory(0.0, Rc::new(|info, _| kind(info))),
                _ => {
                    let attr = match title {
                        "Date Created" => "time::created",
                        "Date Last Opened" => "time::access",
                        _ => "time::modified",
                    };
                    text_factory(0.0, Rc::new(move |info, _| date(info, attr)))
                }
            };
            columns.push((title, mk_col(title, &f, width)));
        }
        columns.insert(0, ("Name", name.clone()));
        let lv = Rc::new(Self { view, sorted, selection, source: model.clone(), cells, columns, count_sizes, on_sort: RefCell::default(), sorting: Cell::new(false) });
        lv.show_columns(None);
        lv.view.sort_by_column(Some(&name), gtk::SortType::Ascending);
        let w = Rc::downgrade(&lv);
        if let Some(s) = lv.view.sorter() {
            s.connect_changed(move |_, _| {
                if let Some(l) = w.upgrade() {
                    if !l.sorting.get() {
                        if let Some(f) = l.on_sort.borrow().as_ref() {
                            f(l.sort_state());
                        }
                    }
                }
            });
        }
        let s2 = lv.sorted.clone();
        lv.view.connect_activate(move |_, pos| {
            if let Some(i) = s2.item(pos).and_then(|o| info_of(&o)) {
                on_open(&i);
            }
        });
        lv.cells.dnd_list(lv.view.upcast_ref(), true);
        lv
    }

    fn key_of(&self, col: &gtk::ColumnViewColumn) -> &'static str {
        self.columns.iter().find(|(_, c)| c == col).map(|(t, _)| *t).unwrap_or("Name")
    }

    /// Which columns show (None: the defaults); Name always does.
    pub fn show_columns(&self, shown: Option<&[String]>) {
        for (title, _, default) in LIST_COLUMNS {
            if let Some((_, c)) = self.columns.iter().find(|(t, _)| *t == title) {
                c.set_visible(shown.map_or(default, |s| s.iter().any(|x| x == title)));
            }
        }
    }

    pub fn shown_columns(&self) -> Vec<String> {
        LIST_COLUMNS.iter().filter(|(t, _, _)| self.columns.iter().any(|(ct, c)| ct == t && c.is_visible())).map(|(t, _, _)| t.to_string()).collect()
    }
}

impl View for ListView {
    fn widget(&self) -> gtk::Widget {
        self.view.clone().upcast()
    }
    fn cells(&self) -> Rc<Cells> {
        self.cells.clone()
    }
    fn model(&self) -> gio::ListModel {
        self.sorted.clone().upcast()
    }
    fn selection(&self) -> gtk::SelectionModel {
        self.selection.clone().upcast()
    }
    fn scroll_to(&self, pos: u32) {
        self.view.scroll_to(pos, None, gtk::ListScrollFlags::FOCUS, None);
    }
    fn show_items(&self, on: bool) {
        if self.sorted.model().is_some() != on {
            self.sorted.set_model(if on { Some(&self.source) } else { None });
        }
    }
    fn sort_state(&self) -> (String, bool) {
        let Some(s) = self.view.sorter().and_downcast::<gtk::ColumnViewSorter>() else { return ("Name".into(), false) };
        let title = s.primary_sort_column().map(|c| self.key_of(&c)).unwrap_or("Name");
        (title.into(), s.primary_sort_order() == gtk::SortType::Descending)
    }
    /// Sort by a column (an unknown title: Name), not reported as your choice.
    fn set_sort(&self, title: &str, descending: bool) {
        let col = self.columns.iter().find(|(t, _)| *t == title).or(self.columns.first()).map(|c| c.1.clone());
        let Some(col) = col else { return };
        if self.sort_state() == (self.key_of(&col).to_string(), descending) {
            return;
        }
        self.sorting.set(true);
        self.view.sort_by_column(Some(&col), if descending { gtk::SortType::Descending } else { gtk::SortType::Ascending });
        self.sorting.set(false);
    }
}

/// Only the last visible column expands -- kept so when columns are shown or hidden.
pub fn fill_last(view: &gtk::ColumnView) {
    let update = |cols: &gio::ListModel| {
        let shown: Vec<gtk::ColumnViewColumn> = (0..cols.n_items()).filter_map(|i| cols.item(i).and_downcast::<gtk::ColumnViewColumn>()).filter(|c| c.is_visible()).collect();
        for (i, c) in shown.iter().enumerate() {
            let want = i + 1 == shown.len();
            if c.expands() != want {
                c.set_expand(want);
            }
        }
    };
    let cols = view.columns();
    cols.connect_items_changed(move |cols, pos, _, added| {
        for i in pos..pos + added {
            if let Some(c) = cols.item(i).and_downcast::<gtk::ColumnViewColumn>() {
                let cl = cols.downgrade();
                c.connect_visible_notify(move |_| {
                    if let Some(cl) = cl.upgrade() {
                        update(cl.upcast_ref());
                    }
                });
            }
        }
        update(cols.upcast_ref());
    });
}

// -- Columns ----------------------------------------------------------------------------------
struct Column {
    root: gtk::ScrolledWindow,
    list: gtk::ListView,
    model: gio::ListModel,
    selection: gtk::SingleSelection,
    cells: Rc<Cells>,
    /// its Folder (None: the window's own, the first column)
    owner: Option<Rc<folder::Folder>>,
}

impl Column {
    fn picked(&self) -> Option<gio::FileInfo> {
        self.selection.selected_item().and_then(|o| info_of(&o))
    }
}

/// Finder's column browser: the first column is the window's folder;
/// picking a folder opens its column to the right, picking a file shows a
/// preview column. on_location(uri) reports the deepest open folder.
pub struct ColumnsView {
    pub scroller: gtk::ScrolledWindow,
    bx: gtk::Box,
    columns: RefCell<Vec<Rc<Column>>>,
    preview: RefCell<Option<gtk::Widget>>,
    pub root_uri: RefCell<Option<String>>,
    on_open: OnOpen,
    on_location: Rc<dyn Fn(&str)>,
    show_hidden: Rc<dyn Fn() -> bool>,
    source: gio::ListModel,
    dnd: RefCell<Option<Weak<dyn Dnd>>>,
    me: Weak<ColumnsView>,
}

impl ColumnsView {
    pub fn new(model: &gio::ListModel, on_open: OnOpen, on_location: impl Fn(&str) + 'static, show_hidden: impl Fn() -> bool + 'static) -> Rc<Self> {
        sonata_ui::css::register_module("files/views.py");
        let bx = gtk::Box::builder().css_classes(["fs-columns"]).build();
        let scroller = gtk::ScrolledWindow::builder().child(&bx).vscrollbar_policy(gtk::PolicyType::Never).build();
        let cv = Rc::new_cyclic(|me| ColumnsView { scroller, bx, columns: RefCell::default(), preview: RefCell::default(), root_uri: RefCell::default(), on_open, on_location: Rc::new(on_location), show_hidden: Rc::new(show_hidden), source: model.clone(), dnd: RefCell::default(), me: me.clone() });
        let first = cv.column(model.clone(), None);
        cv.bx.append(&first.root);
        cv.columns.borrow_mut().push(first);
        cv
    }

    fn column(&self, model: gio::ListModel, owner: Option<Rc<folder::Folder>>) -> Rc<Column> {
        let root = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).css_classes(["fs-col"]).build();
        root.set_size_request(COLUMN_W, -1);
        let selection = gtk::SingleSelection::builder().model(&model).autoselect(false).can_unselect(true).build();
        let cells = Cells::new();
        cells.dnd.replace(self.dnd.borrow().clone());
        let f = gtk::SignalListItemFactory::new();
        let c = cells.clone();
        f.connect_setup(move |_, item| {
            let item = item.downcast_ref::<gtk::ListItem>().unwrap();
            let b = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            let img = gtk::Image::builder().pixel_size(16).build();
            let lbl = gtk::Label::builder().xalign(0.0).ellipsize(pango::EllipsizeMode::Middle).hexpand(true).build();
            let chev = gtk::Image::builder().icon_name("go-next-symbolic").css_classes(["fs-chevron"]).build();
            let tags = dots_box(false);
            for w in [img.upcast_ref::<gtk::Widget>(), lbl.upcast_ref(), tags.upcast_ref(), chev.upcast_ref()] {
                b.append(w);
            }
            set_parts(&b, Rc::new(Parts { img, badge: None, lbl, tags, over: None, chev: Some(chev), info: RefCell::default(), spring: RefCell::default() }));
            c.dnd_cell(b.upcast_ref());
            item.set_child(Some(&b));
        });
        let c = cells.clone();
        f.connect_bind(move |_, item| bind_cell(&c, item, true, None));
        let c = cells.clone();
        f.connect_unbind(move |_, item| {
            if let Some(b) = item.downcast_ref::<gtk::ListItem>().and_then(|i| i.child()) {
                c.untrack(&b);
            }
        });
        let list = gtk::ListView::builder().model(&selection).factory(&f).build();
        root.set_child(Some(&list));
        let col = Rc::new(Column { root, list, model, selection, cells, owner });
        let (w, cw) = (self.me.clone(), Rc::downgrade(&col));
        col.selection.connect_selection_changed(move |_, _, _| {
            if let (Some(v), Some(c)) = (w.upgrade(), cw.upgrade()) {
                v.picked(&c);
            }
        });
        let (open, m) = (self.on_open.clone(), col.model.clone());
        col.list.connect_activate(move |_, pos| {
            if let Some(i) = m.item(pos).and_then(|o| info_of(&o)) {
                open(&i);
            }
        });
        let focus = gtk::EventControllerFocus::new();
        let (w, cw) = (self.me.clone(), Rc::downgrade(&col));
        focus.connect_enter(move |_| {
            if let (Some(v), Some(c)) = (w.upgrade(), cw.upgrade()) {
                v.activate_column(&c);
            }
        });
        col.list.add_controller(focus);
        let keys = gtk::EventControllerKey::new();
        let (w, cw) = (self.me.clone(), Rc::downgrade(&col));
        keys.connect_key_pressed(move |_, k, _, _| match (w.upgrade(), cw.upgrade()) {
            (Some(v), Some(c)) if v.key(&c, k) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        col.list.add_controller(keys);
        col.cells.dnd_list(col.list.upcast_ref(), false);
        // on the column's empty space: into its folder
        let bg = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE);
        let (w, cw) = (self.me.clone(), Rc::downgrade(&col));
        bg.connect_drop(move |t, v, _, _| {
            let (Some(view), Some(c)) = (w.upgrade(), cw.upgrade()) else { return false };
            let uri = c.owner.as_ref().and_then(|o| o.uri.borrow().clone()).or_else(|| view.root_uri.borrow().clone());
            let (Some(uri), Some(d), Ok(list)) = (uri, view.dnd.borrow().as_ref().and_then(Weak::upgrade), v.get::<gdk::FileList>()) else { return false };
            d.drop_files(list.files(), gio::File::for_uri(&uri), t.current_event_state().contains(gdk::ModifierType::CONTROL_MASK))
        });
        col.root.add_controller(bg);
        col
    }

    /// The window's folder changed: back to one column.
    pub fn reset(&self, root_uri: &str) {
        self.root_uri.replace(Some(root_uri.into()));
        self.truncate(0);
        if let Some(c) = self.columns.borrow().first() {
            c.selection.unselect_all();
        }
    }

    fn truncate(&self, keep: usize) {
        let gone: Vec<Rc<Column>> = {
            let mut cols = self.columns.borrow_mut();
            if cols.len() > keep + 1 { cols.split_off(keep + 1) } else { Vec::new() }
        };
        for c in gone {
            if let Some(o) = &c.owner {
                o.cancel();
            }
            self.bx.remove(&c.root);
        }
        if let Some(p) = self.preview.take() {
            self.bx.remove(&p);
        }
    }

    fn picked(&self, col: &Rc<Column>) {
        let Some(i) = self.columns.borrow().iter().position(|c| Rc::ptr_eq(c, col)) else { return };
        self.truncate(i);
        self.activate_column(col);
        let here = |v: &Self| if i == 0 { v.root_uri.borrow().clone() } else { v.columns.borrow()[i].owner.as_ref().and_then(|o| o.uri.borrow().clone()) };
        let Some(info) = col.picked() else {
            if let Some(u) = here(self) {
                (self.on_location)(&u);
            }
            return;
        };
        if is_dir(&info) {
            let uri = info.attribute_string("standard::target-uri").map(|s| s.to_string()).filter(|s| !s.is_empty()).or_else(|| folder::file_of(&info).map(|f| f.uri().to_string())).unwrap_or_default();
            let f = folder::Folder::new(|_| {}, |_, _| {}, None);
            f.show_hidden.set((self.show_hidden)());
            f.load(&uri);
            let new = self.column(f.store.clone().upcast(), Some(f));
            self.bx.append(&new.root);
            self.columns.borrow_mut().push(new);
            (self.on_location)(&uri);
        } else {
            let p = preview(&info);
            self.bx.append(&p);
            self.preview.replace(Some(p));
            if let Some(u) = here(self) {
                (self.on_location)(&u);
            }
        }
        let adj = self.scroller.hadjustment();
        glib::idle_add_local_once(move || adj.set_value(adj.upper() - adj.page_size()));
    }

    /// Right: into the open folder's column; Left: back to the parent column.
    fn key(&self, col: &Rc<Column>, k: gdk::Key) -> bool {
        let cols = self.columns.borrow().clone();
        let Some(i) = cols.iter().position(|c| Rc::ptr_eq(c, col)) else { return false };
        if k == gdk::Key::Right && i + 1 < cols.len() {
            let nxt = &cols[i + 1];
            if nxt.model.n_items() > 0 && nxt.selection.selected() == gtk::INVALID_LIST_POSITION {
                nxt.selection.set_selected(0);
            }
            nxt.list.grab_focus();
            return true;
        }
        if k == gdk::Key::Left && i > 0 {
            col.selection.unselect_all();
            cols[i - 1].list.grab_focus();
            return true;
        }
        false
    }

    fn activate_column(&self, col: &Rc<Column>) {
        for c in self.columns.borrow().iter() {
            if Rc::ptr_eq(c, col) {
                c.root.add_css_class("fs-active");
            } else {
                c.root.remove_css_class("fs-active");
            }
        }
    }

    fn active(&self) -> Rc<Column> {
        let cols = self.columns.borrow();
        cols.iter().find(|c| c.root.has_css_class("fs-active")).unwrap_or(&cols[0]).clone()
    }

    /// The folder the active column shows (paste / new folder go there).
    pub fn location(&self) -> Option<String> {
        let a = self.active();
        a.owner.as_ref().and_then(|o| o.uri.borrow().clone()).or_else(|| self.root_uri.borrow().clone())
    }
}

impl View for ColumnsView {
    fn widget(&self) -> gtk::Widget {
        self.scroller.clone().upcast()
    }
    fn cells(&self) -> Rc<Cells> {
        self.active().cells.clone()
    }
    fn model(&self) -> gio::ListModel {
        self.active().model.clone()
    }
    fn selection(&self) -> gtk::SelectionModel {
        self.active().selection.clone().upcast()
    }
    fn scroll_to(&self, pos: u32) {
        self.active().list.scroll_to(pos, gtk::ListScrollFlags::FOCUS, None);
    }
    fn selected(&self) -> Vec<gio::FileInfo> {
        self.active().picked().into_iter().collect()
    }
    fn select_all(&self) {}
    fn unselect_all(&self) {
        if let Some(c) = self.columns.borrow().first() {
            c.selection.unselect_all();
        }
    }
    fn focus(&self) {
        self.active().list.grab_focus();
    }
    fn scroll_top(&self) {}
    fn ensure_selected(&self, info: &gio::FileInfo) {
        let cols = self.columns.borrow().clone();
        for c in cols {
            if c.cells.cells.borrow().contains_key(info) {
                if let Some(i) = (0..c.model.n_items()).find(|i| c.model.item(*i).and_then(|o| info_of(&o)).as_ref() == Some(info)) {
                    if !c.selection.is_selected(i) {
                        c.selection.set_selected(i);
                    }
                }
                self.activate_column(&c);
                return;
            }
        }
    }
    fn begin_rename(&self, info: &gio::FileInfo, on_commit: OnCommit) {
        let cols = self.columns.borrow().clone();
        for c in &cols {
            if c.cells.begin_rename(info, on_commit.clone()) {
                return;
            }
        }
    }
    fn show_items(&self, on: bool) {
        let first = self.columns.borrow()[0].clone();
        if first.selection.model().is_some() != on {
            first.selection.set_model(if on { Some(&self.source) } else { None });
        }
    }
    fn set_dnd(&self, d: Weak<dyn Dnd>) {
        self.dnd.replace(Some(d.clone()));
        for c in self.columns.borrow().iter() {
            c.cells.dnd.replace(Some(d.clone()));
        }
    }
}

/// Finder's preview column: big icon, name, kind + size, dates.
fn preview(info: &gio::FileInfo) -> gtk::Widget {
    let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(4).css_classes(["fs-preview"]).build();
    bx.set_size_request(COLUMN_W + 40, -1);
    let img = gtk::Image::builder().pixel_size(128).margin_bottom(10).build();
    set_icon(&img, info, false);
    bx.append(&img);
    bx.append(&gtk::Label::builder().label(label(info)).wrap(true).wrap_mode(pango::WrapMode::WordChar).justify(gtk::Justification::Center).css_classes(["fs-preview-name"]).build());
    bx.append(&gtk::Label::builder().label(format!("{} – {}", kind(info), size(info))).css_classes(["fs-preview-kind"]).build());
    let grid = gtk::Grid::builder().column_spacing(8).row_spacing(2).margin_top(14).halign(gtk::Align::Center).build();
    grid.attach(&gtk::Label::builder().label(tr!("Modified")).xalign(1.0).css_classes(["fs-preview-key"]).build(), 0, 0, 1, 1);
    grid.attach(&gtk::Label::builder().label(date(info, "time::modified")).xalign(0.0).css_classes(["fs-preview-val"]).build(), 1, 0, 1, 1);
    bx.append(&grid);
    bx.upcast()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn info(name: &str, dir: bool, size: i64) -> gio::FileInfo {
        let i = gio::FileInfo::new();
        i.set_name(name);
        i.set_display_name(name);
        i.set_file_type(if dir { gio::FileType::Directory } else { gio::FileType::Regular });
        i.set_size(size);
        i
    }

    #[test]
    fn folders_first_every_sort() {
        let (d, a, b) = (info("zdir", true, 0), info("a.txt", false, 5), info("b.txt", false, 9));
        let mut v = vec![b.clone(), d.clone(), a.clone()];
        v.sort_by(|x, y| folders_first(x, y).then(compare("Name", x, y)));
        assert_eq!(v.iter().map(|i| i.name().to_string_lossy().into_owned()).collect::<Vec<_>>(), ["zdir", "a.txt", "b.txt"]);
        v.sort_by(|x, y| folders_first(x, y).then(compare("Size", x, y).reverse()));
        assert_eq!(v.iter().map(|i| i.name().to_string_lossy().into_owned()).collect::<Vec<_>>(), ["zdir", "b.txt", "a.txt"]);
        assert_eq!(size(&d), "--");
        assert_eq!(walk_size(std::path::Path::new("/nonexistent")), 0);
    }
}
