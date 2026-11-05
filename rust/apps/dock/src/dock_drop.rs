//! Dropping files onto the Dock (shell/dock_drop.py, macOS behaviour):
//!
//! - files on an app icon: open them with that app (running or not). The
//!   icon darkens while hovered only if the app can open all of them (MIME
//!   types). Held there a moment, an open app's windows come forward --
//!   minimized ones too -- to drop the item into one (spring-loading).
//! - files on the Trash: move them to the Trash; an app: uninstall it (asked first).
//! - an application (.desktop file) anywhere on the Dock: pin it at that spot.
//! - a folder anywhere on the Dock: add it as a stack.

use crate::dock::{self, Dock, Tile};
use gtk::prelude::*;
use gtk::{gdk, gio, glib, graphene};
use sonata_desktop::launchpad_model::{decode_folder, Folder, FOLDER_SCHEME};
use std::cell::RefCell;
use std::path::PathBuf;
use std::rc::Rc;
use std::time::Duration;

pub const HOVER: &str = "drop-hover";
/// hold a dragged item on an open app's icon: its windows come forward
pub const SPRING_MS: u64 = 700;
pub const URI_LIST: &str = "text/uri-list";

/// A dropped item: a file, or a URI kept as it came (a Launchpad folder,
/// "sonata2-folder:..." -- gio hands unknown schemes to GVfs, which may
/// rewrite them).
#[derive(Clone, Debug)]
pub enum DropItem {
    File(gio::File),
    Raw(String),
}

impl DropItem {
    pub fn from_uri(uri: &str) -> Self {
        if uri.starts_with(FOLDER_SCHEME) {
            DropItem::Raw(uri.to_string())
        } else {
            DropItem::File(gio::File::for_uri(uri))
        }
    }

    pub fn path(&self) -> Option<PathBuf> {
        match self {
            DropItem::File(f) => f.path(),
            DropItem::Raw(_) => None,
        }
    }

    pub fn uri(&self) -> String {
        match self {
            DropItem::File(f) => f.uri().to_string(),
            DropItem::Raw(u) => u.clone(),
        }
    }

    pub fn file(&self) -> Option<&gio::File> {
        match self {
            DropItem::File(f) => Some(f),
            DropItem::Raw(_) => None,
        }
    }
}

pub fn is_app(f: &DropItem) -> bool {
    f.path().is_some_and(|p| p.to_string_lossy().ends_with(".desktop"))
}

/// An installed app's own entry (in an applications folder) -- not a
/// shortcut to it on the Desktop or in a folder, which only goes to the
/// Trash (Vini: deleting a shortcut asked to uninstall the app).
pub fn installed(f: &DropItem) -> bool {
    is_app(f) && f.path().and_then(|p| p.parent().map(|d| d.file_name().is_some_and(|n| n == "applications"))).unwrap_or(false)
}

/// Launchpad folders in the drop (launchpad_model.encode_folder).
pub fn folders(items: &[DropItem]) -> Vec<Folder> {
    items.iter().filter_map(|f| decode_folder(&f.uri())).collect()
}

/// True if `info` declares support for every file's content type.
pub fn can_open(info: Option<&gio::DesktopAppInfo>, items: &[DropItem]) -> bool {
    let Some(info) = info else { return false };
    if items.is_empty() {
        return false;
    }
    let supported = info.supported_types();
    items.iter().all(|f| {
        let Some(file) = f.file() else { return false };
        let Ok(q) = file.query_info("standard::content-type", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) else { return false };
        let ctype = q.content_type().map(|c| c.to_string()).unwrap_or_default();
        supported.iter().any(|t| gio::content_type_is_a(&ctype, t))
    })
}

fn user_apps() -> PathBuf {
    glib::user_data_dir().join("applications")
}

/// Desktop id for a dropped .desktop file; files outside the application
/// folders are copied to ~/.local/share/applications first.
pub fn app_id_for(f: &DropItem) -> Option<String> {
    let path = f.path()?;
    let name = path.file_name()?.to_string_lossy().to_string();
    let did = name.strip_suffix(".desktop")?.to_string();
    if sonata_ui::apps::lookup(&did).is_some() {
        return Some(did);
    }
    let _ = std::fs::create_dir_all(user_apps());
    std::fs::copy(&path, user_apps().join(&name)).ok()?;
    sonata_desktop::appmatch::refresh();
    sonata_ui::apps::scan();
    sonata_ui::apps::lookup(&did).map(|_| did)
}

fn is_dir(f: &DropItem) -> bool {
    f.file().is_some_and(|f| f.query_file_type(gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) == gio::FileType::Directory)
}

pub fn only_dirs(items: &[DropItem]) -> bool {
    !items.is_empty() && folders(items).is_empty() && items.iter().all(|f| f.path().is_some() && is_dir(f))
}

/// What a target's handlers see: the files read so far, and the drop.
#[derive(Default)]
struct State {
    drop: Option<gdk::Drop>,
    files: Option<Vec<DropItem>>,
}

/// The dragged files as a plain uri list. (A Gdk.FileList target lets GTK
/// pick the portal's file-transfer format first, which fails to convert
/// without the document portal: drags from Files were refused.)
fn read_uris(drop: &gdk::Drop, done: impl FnOnce(Option<Vec<DropItem>>) + 'static) {
    drop.read_async(&[URI_LIST], glib::Priority::DEFAULT, gio::Cancellable::NONE, move |res| {
        let Ok((stream, _)) = res else {
            done(None);
            return;
        };
        stream.read_bytes_async(1 << 20, glib::Priority::DEFAULT, gio::Cancellable::NONE, move |res| {
            let Ok(bytes) = res else {
                done(None);
                return;
            };
            let text = String::from_utf8_lossy(&bytes).to_string();
            done(Some(text.lines().map(str::trim).filter(|l| !l.is_empty() && !l.starts_with('#')).map(DropItem::from_uri).collect()));
        });
    });
}

/// The action to answer with: `wanted` (COPY) when the drag allows it,
/// else LINK -- a place dragged from Files' sidebar is offered as a link
/// only, so nothing else ever copies or moves a whole folder.
fn action(drop: &gdk::Drop, wanted: gdk::DragAction, link: bool) -> gdk::DragAction {
    if wanted.is_empty() {
        return wanted;
    }
    let allowed = drop.actions();
    if allowed.intersects(wanted) {
        return wanted;
    }
    if link && allowed.contains(gdk::DragAction::LINK) {
        gdk::DragAction::LINK
    } else {
        gdk::DragAction::empty()
    }
}

type Motion = Rc<dyn Fn(&[DropItem], f64, f64) -> gdk::DragAction>;
type Dropped = Rc<dyn Fn(Vec<DropItem>, f64, f64) -> bool>;

/// A file drop target that reads text/uri-list itself (see read_uris).
/// link: also takes a link-only drag (the plate: folders become stacks);
/// the icons and the Trash never do (the Trash would bin the folder).
fn target(on_motion: Motion, on_drop: Dropped, on_leave: Rc<dyn Fn()>, link: bool) -> gtk::DropTargetAsync {
    let mut actions = gdk::DragAction::COPY | gdk::DragAction::MOVE;
    if link {
        actions |= gdk::DragAction::LINK;
    }
    let t = gtk::DropTargetAsync::new(Some(gdk::ContentFormats::new(&[URI_LIST])), actions);
    let st: Rc<RefCell<State>> = Rc::default();
    t.connect_accept(move |_, drop| drop.formats().contain_mime_type(URI_LIST) && drop.actions().intersects(actions));
    let s = st.clone();
    t.connect_drag_enter(move |_, drop, _, _| {
        s.replace(State { drop: Some(drop.clone()), files: None });
        let (s2, d2) = (s.clone(), drop.clone());
        read_uris(drop, move |files| {
            let mut st = s2.borrow_mut();
            if st.drop.as_ref() == Some(&d2) {
                st.files = Some(files.unwrap_or_default());
            }
        });
        action(drop, gdk::DragAction::COPY, link) // decided on the next motion, once read
    });
    let s = st.clone();
    t.connect_drag_motion(move |_, drop, x, y| {
        let files = s.borrow().files.clone();
        match files {
            None => action(drop, gdk::DragAction::COPY, link),
            Some(f) => action(drop, on_motion(&f, x, y), link),
        }
    });
    let s = st.clone();
    t.connect_drag_leave(move |_, _| {
        on_leave();
        s.replace(State::default());
    });
    t.connect_drop(move |_, drop, x, y| {
        let (s2, d2, on_drop) = (st.clone(), drop.clone(), on_drop.clone());
        let finish = move |files: Option<Vec<DropItem>>| {
            let files = files.unwrap_or_default();
            let ok = !files.is_empty() && on_drop(files, x, y);
            d2.finish(if ok { action(&d2, gdk::DragAction::COPY, link) } else { gdk::DragAction::empty() });
            s2.replace(State::default());
        };
        let files = st.borrow().files.clone();
        match files {
            Some(f) => finish(Some(f)),
            None => read_uris(drop, finish),
        }
        true
    });
    t
}

fn before_slot(dock: &Dock, slot: Option<usize>) -> Option<Rc<Tile>> {
    let tiles = dock.app_tiles();
    slot.and_then(|s| tiles.get(s).cloned())
}

pub fn attach_app(dock: &Rc<Dock>, tile: &Rc<Tile>) {
    let spring: Rc<RefCell<Option<glib::SourceId>>> = Rc::default();
    let spring_cancel = {
        let spring = spring.clone();
        move || {
            if let Some(s) = spring.borrow_mut().take() {
                sonata_ui::cancel(s);
            }
        }
    };
    let (w, t, sp) = (dock.weak(), Rc::downgrade(tile), spring.clone());
    let motion: Motion = Rc::new(move |files, x, y| {
        let (Some(dock), Some(tile)) = (w.upgrade(), t.upgrade()) else { return gdk::DragAction::empty() };
        if !files.is_empty() && (files.iter().all(is_app) || !folders(files).is_empty()) {
            tile.button.remove_css_class(HOVER);
            if let Some(p) = tile.button.compute_point(&dock.plate, &graphene::Point::new(x as f32, y as f32)) {
                dock.show_drop_gap(p.x() as f64, p.y() as f64); // the icons part where it will land
            }
            return gdk::DragAction::COPY; // pin, handled on drop
        }
        let ok = can_open(tile.info.as_ref(), files);
        if ok {
            tile.button.add_css_class(HOVER);
        } else {
            tile.button.remove_css_class(HOVER);
        }
        if !ok && only_dirs(files) {
            dock.show_stack_gap(); // folders this app can't open: kept as a stack
            return gdk::DragAction::COPY;
        }
        if ok {
            dock.hide_drop_gap_soon();
        }
        // spring-loading (macOS): held over the icon of an open app, its
        // windows come forward -- minimized ones too -- to drop into one
        let key = tile.key().unwrap_or_default();
        if !files.is_empty() && dock.windows.borrow().contains_key(&key) && sp.borrow().is_none() {
            let (w, sp2) = (dock.weak(), sp.clone());
            let src = glib::timeout_add_local_once(Duration::from_millis(SPRING_MS), move || {
                sp2.replace(None);
                if let Some(d) = w.upgrade() {
                    if let Some(m) = &d.manager {
                        for t in d.wins(&key) {
                            m.activate(&t);
                        }
                    }
                }
            });
            sp.replace(Some(src));
        }
        if ok {
            gdk::DragAction::COPY
        } else {
            gdk::DragAction::empty()
        }
    });
    let (w, t, sc) = (dock.weak(), Rc::downgrade(tile), spring_cancel.clone());
    let dropped: Dropped = Rc::new(move |files, _x, _y| {
        sc();
        let (Some(dock), Some(tile)) = (w.upgrade(), t.upgrade()) else { return false };
        tile.button.remove_css_class(HOVER);
        let slot = dock.hide_drop_gap();
        let before = before_slot(&dock, slot).unwrap_or_else(|| tile.clone());
        let fs = folders(&files);
        if !fs.is_empty() {
            return add_folders(&dock, &fs, Some(&before), None);
        }
        if files.iter().all(is_app) {
            return pin_files(&dock, &files, Some(&before), None);
        }
        if !can_open(tile.info.as_ref(), &files) {
            return add_stacks(&dock, &files);
        }
        let Some(info) = tile.info.clone() else { return false };
        let gfiles: Vec<gio::File> = files.iter().filter_map(|f| f.file().cloned()).collect();
        if let Err(e) = dock::launch_info(&info, &gfiles, Some(dock::launch_context(&tile.button))) {
            println!("sonata2-dock: cannot open files with {}: {}", info.id().unwrap_or_default(), e.message());
            return false;
        }
        if !dock.windows.borrow().contains_key(&tile.key().unwrap_or_default()) {
            dock.launch_feedback(&tile);
        }
        true
    });
    let (w, t) = (dock.weak(), Rc::downgrade(tile));
    let leave: Rc<dyn Fn()> = Rc::new(move || {
        spring_cancel();
        if let Some(t) = t.upgrade() {
            t.button.remove_css_class(HOVER);
        }
        if let Some(d) = w.upgrade() {
            d.hide_drop_gap_soon();
        }
    });
    tile.button.add_controller(target(motion, dropped, leave, true));
}

pub fn attach_trash(dock: &Rc<Dock>, tile: &Rc<Tile>) {
    let (w, t) = (dock.weak(), Rc::downgrade(tile));
    let motion: Motion = Rc::new(move |files, _, _| {
        let ok = !files.is_empty() && folders(files).is_empty(); // a Launchpad folder isn't trashed
        if let Some(t) = t.upgrade() {
            if ok {
                t.button.add_css_class(HOVER);
            } else {
                t.button.remove_css_class(HOVER);
            }
        }
        if let Some(d) = w.upgrade() {
            d.hide_drop_gap(); // over the Trash: no gap left open between icons
        }
        if ok {
            gdk::DragAction::MOVE
        } else {
            gdk::DragAction::empty()
        }
    });
    let (w, t) = (dock.weak(), Rc::downgrade(tile));
    let dropped: Dropped = Rc::new(move |dropped, _, _| {
        if let Some(t) = t.upgrade() {
            t.button.remove_css_class(HOVER);
        }
        let Some(dock) = w.upgrade() else { return false };
        dock.hide_drop_gap();
        if !folders(&dropped).is_empty() {
            return false;
        }
        if dropped.iter().all(installed) {
            // an app (from Launchpad, Files): uninstall it
            if let Some(d) = dock.drag.borrow_mut().as_mut() {
                d.dropped = true; // a Dock icon: it stays until the uninstall is done
            }
            uninstall_apps(&dock, &dropped);
            return true;
        }
        // shortcuts (and files) with them: only those trashed -- an installed
        // app's own entry never is (Sonata's own apps among them)
        let mut moved = false;
        for f in dropped.iter().filter(|f| !installed(f)) {
            let Some(file) = f.file() else { continue };
            match file.trash(gio::Cancellable::NONE) {
                Ok(()) => moved = true,
                Err(e) => println!("sonata2-dock: cannot move {} to the Trash: {}", file.uri(), e.message()),
            }
        }
        if moved {
            sonata_backend::sounds::play("trash"); // no sound when nothing could be moved
        }
        moved
    });
    let t = Rc::downgrade(tile);
    let leave: Rc<dyn Fn()> = Rc::new(move || {
        if let Some(t) = t.upgrade() {
            t.button.remove_css_class(HOVER);
        }
    });
    tile.button.add_controller(target(motion, dropped, leave, false));
}

/// Apps dropped on the Trash: asked, then uninstalled (macOS). Launchpad
/// closes first so the question isn't hidden under it.
fn uninstall_apps(dock: &Rc<Dock>, files: &[DropItem]) {
    dock::close_launchpad();
    for f in files {
        let Some(path) = f.path() else { continue };
        let did = path.file_name().map(|n| n.to_string_lossy().trim_end_matches(".desktop").to_string()).unwrap_or_default();
        let info = sonata_ui::apps::lookup(&did).or_else(|| gio::DesktopAppInfo::from_filename(&path));
        if let Some(info) = info {
            let w = dock.weak();
            crate::uninstall_ui::ask(&info, None, Some(Rc::new(move |ok| {
                if let (true, Some(d)) = (ok, w.upgrade()) {
                    d.set_pinned(&did, false);
                }
            })));
        }
    }
}

/// Folders dropped on the Dock: kept as stacks, next to the others.
pub fn add_stacks(dock: &Dock, files: &[DropItem]) -> bool {
    dock.hide_drop_gap();
    if !only_dirs(files) {
        return false;
    }
    for f in files {
        if let Some(p) = f.path() {
            dock.stacks.add(&p.to_string_lossy());
        }
    }
    true
}

/// Apps dropped between icons get pinned; folders become stacks.
pub fn attach_plate(dock: &Rc<Dock>) {
    let w = dock.weak();
    let motion: Motion = Rc::new(move |files, x, y| {
        let Some(dock) = w.upgrade() else { return gdk::DragAction::empty() };
        let fs = folders(files);
        let apps_only = !files.is_empty() && (files.iter().all(is_app) || !fs.is_empty());
        let pins = dock.pinned();
        let fresh = files.iter().any(|f| !is_app(f) || app_id_for(f).is_none_or(|id| !pins.contains(&id)));
        let dirs = only_dirs(files);
        if apps_only && (fresh || !fs.is_empty()) {
            dock.show_drop_gap(x, y); // the icons part where it will land (not for one already kept)
        } else if dirs {
            dock.show_stack_gap(); // a folder: its place opens by the other folders
        }
        if !files.is_empty() && (apps_only || dirs) {
            gdk::DragAction::COPY
        } else {
            gdk::DragAction::empty()
        }
    });
    let w = dock.weak();
    let dropped: Dropped = Rc::new(move |files, x, y| {
        let Some(dock) = w.upgrade() else { return false };
        let slot = dock.hide_drop_gap();
        let before = before_slot(&dock, slot);
        let fs = folders(&files);
        if !fs.is_empty() {
            return add_folders(&dock, &fs, before.as_ref(), Some((x, y)));
        }
        if only_dirs(&files) {
            return add_stacks(&dock, &files);
        }
        if !files.iter().all(is_app) {
            return false;
        }
        match before {
            Some(b) => pin_files(&dock, &files, Some(&b), None),
            None => pin_files(&dock, &files, None, Some((x, y))),
        }
    });
    let w = dock.weak();
    let leave: Rc<dyn Fn()> = Rc::new(move || {
        if let Some(d) = w.upgrade() {
            d.hide_drop_gap_soon();
        }
    });
    dock.plate.add_controller(target(motion, dropped, leave, true));
}

/// Launchpad folders dropped on the Dock: a Dock folder each, there.
pub fn add_folders(dock: &Dock, fs: &[Folder], before: Option<&Rc<Tile>>, at: Option<(f64, f64)>) -> bool {
    let mut ok = false;
    for f in fs {
        let fkey = dock.add_folder(&f.name, &f.apps, before, at, f.link());
        println!("sonata2-dock: folder dropped: {:?} ({} apps) -> {:?}", f.name, f.apps.len(), fkey);
        ok |= fkey.is_some();
    }
    ok
}

/// Pin dropped .desktop files at the drop position.
pub fn pin_files(dock: &Dock, files: &[DropItem], before: Option<&Rc<Tile>>, at: Option<(f64, f64)>) -> bool {
    let mut ok = false;
    for f in files {
        if let Some(did) = app_id_for(f) {
            dock.pin_at(&did, before, at);
            ok = true;
        }
    }
    ok
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apps_and_their_shortcuts() {
        let entry = DropItem::File(gio::File::for_path("/usr/share/applications/firefox.desktop"));
        let shortcut = DropItem::File(gio::File::for_path("/home/u/Desktop/firefox.desktop"));
        let doc = DropItem::File(gio::File::for_path("/home/u/notes.txt"));
        assert!(is_app(&entry) && installed(&entry));
        // Vini: deleting a shortcut asked to uninstall the app
        assert!(is_app(&shortcut) && !installed(&shortcut));
        assert!(!is_app(&doc));
    }

    /// A Launchpad folder's text survives the drop (gio would rewrite an
    /// unknown scheme): the folder lands on the Dock.
    #[test]
    fn launchpad_folders_come_through() {
        let text = sonata_desktop::launchpad_model::encode_folder("Games", &["a".into(), "b".into()], "abc");
        let items = vec![DropItem::from_uri(&text), DropItem::from_uri("file:///tmp/x")];
        let fs = folders(&items);
        assert_eq!(fs.len(), 1);
        assert_eq!((fs[0].name.as_str(), fs[0].apps.len(), fs[0].link()), ("Games", 2, "abc"));
        assert!(!only_dirs(&items));
    }

    #[test]
    fn folders_dropped_become_stacks() {
        let dir = tempfile::tempdir().unwrap();
        let f = dir.path().join("a.txt");
        std::fs::write(&f, "x").unwrap();
        assert!(only_dirs(&[DropItem::File(gio::File::for_path(dir.path()))]));
        assert!(!only_dirs(&[DropItem::File(gio::File::for_path(&f))]));
        assert!(!only_dirs(&[]));
        assert!(!can_open(None, &[DropItem::File(gio::File::for_path(&f))]));
    }
}
