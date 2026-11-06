//! Folder contents for the views (files/folder.py): one ListStore of
//! FileInfo, kept sorted (Finder order: names compared like Finder,
//! numbers numerically, folders first) and live (directory monitor).
//!
//! Loading is async and batched; the store is filled with one splice so a
//! 10k-item folder is one model update. Each FileInfo carries its File in
//! the "sonata::file" attribute (Recents mixes folders).

use gio::prelude::*;
use gtk::prelude::*;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};

pub const ATTRS: &str = "standard::name,standard::display-name,standard::icon,standard::symbolic-icon,standard::type,standard::is-symlink,standard::is-hidden,standard::is-backup,standard::content-type,standard::size,standard::target-uri,time::modified,time::modified-usec,time::created,time::access,access::can-write,thumbnail::path,thumbnail::failed,xattr::xdg.tags";
pub const RECENTS: &str = "sonata:recents";
/// the installed apps, like Finder's Applications folder
pub const APPS: &str = "sonata:applications";
/// the menu bar's Go > Connect to Server (not a place: the dialog)
pub const CONNECT: &str = "sonata:connect";
const BATCH: i32 = 500;
/// a package removes several files: one reload
const APPS_RELOAD_MS: u64 = 400;
pub const MENU_NAME_MAX: usize = 30;

/// The places that aren't folders: Recents, Applications and the
/// sidebar's Tags (sonata:tag/<name>).
pub fn is_virtual(uri: &str) -> bool {
    uri == RECENTS || uri == APPS || uri.starts_with(crate::tags::PREFIX)
}

/// Our sonata: places as Files names them. Handed to another process they
/// pass through a GFile, and with gvfs "sonata:recents" comes back as
/// "sonata:///recents" -- no longer Recents.
pub fn canonical(uri: &str) -> String {
    match uri.strip_prefix("sonata:/") {
        Some(rest) => format!("sonata:{}", glib::Uri::unescape_string(rest.trim_start_matches('/'), None::<&str>).map(|s| s.to_string()).unwrap_or_else(|| rest.trim_start_matches('/').to_string())),
        None => uri.to_string(),
    }
}

pub fn file_of(info: &gio::FileInfo) -> Option<gio::File> {
    info.attribute_object("sonata::file").and_downcast::<gio::File>()
}

pub fn is_dir(info: &gio::FileInfo) -> bool {
    matches!(info.file_type(), gio::FileType::Directory | gio::FileType::Mountable)
}

/// GLib's file-name collation key (numbers numerically, like Finder).
pub fn collate_key(s: &str) -> String {
    let Ok(c) = std::ffi::CString::new(s) else { return s.into() };
    unsafe {
        let p = glib::ffi::g_utf8_collate_key_for_filename(c.as_ptr(), -1);
        let r = std::ffi::CStr::from_ptr(p).to_string_lossy().into_owned();
        glib::ffi::g_free(p.cast());
        r
    }
}

/// Name order, folders first (Finder's "Keep folders on top"); cached on
/// the info ("sonata::key") for the views' sorters.
pub fn sort_key(info: &gio::FileInfo) -> String {
    if let Some(k) = info.attribute_string("sonata::key") {
        return k.to_string();
    }
    let name = info.display_name();
    // case-insensitive in every locale, like Finder; \u{1} before the name
    // (it breaks ties) as a NUL can't be in a GLib attribute string
    let k = format!("{}{}\u{1}{}", if is_dir(info) { "0" } else { "1" }, collate_key(&name.to_lowercase()), name);
    info.set_attribute_string("sonata::key", &k);
    k
}

/// A long name cut for a menu, its extension kept: "A very long video
/// na….mp4" -- the right-click menu no longer grows with the name.
pub fn short_name(name: &str, limit: usize) -> String {
    let chars: Vec<char> = name.chars().collect();
    if chars.len() <= limit {
        return name.into();
    }
    let ext = match name.rfind('.') {
        Some(i) if i > 0 && (1..=6).contains(&name[i + 1..].chars().count()) => name[i..].to_string(),
        _ => String::new(),
    };
    let stem: Vec<char> = chars[..chars.len() - ext.chars().count()].to_vec();
    let keep = limit.saturating_sub(ext.chars().count() + 1).max(1);
    format!("{}…{ext}", stem.iter().take(keep).collect::<String>().trim_end())
}

pub fn display_name(uri: &str) -> String {
    if uri == RECENTS {
        return tr!("Recents");
    }
    if uri == APPS {
        return tr!("Applications");
    }
    if let Some(t) = uri.strip_prefix(crate::tags::PREFIX) {
        return sonata_i18n::gettext(t); // a colour tag
    }
    if uri.trim_end_matches('/') == "trash:" {
        return tr!("Trash");
    }
    let f = gio::File::for_uri(uri);
    if f.path().as_deref() == Some(glib::home_dir().as_path()) {
        return glib::user_name().to_string_lossy().into_owned();
    }
    if f.path().as_deref() == Some(std::path::Path::new("/")) {
        return tr!("Computer");
    }
    match f.query_info("standard::display-name", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) {
        Ok(i) => i.display_name().to_string(),
        Err(_) => f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_else(|| uri.into()),
    }
}

/// The URI of an XDG user folder, "" when unset/home. Through userdirs
/// (userdirs.py), as Python's: without user-dirs.dirs (a session that never
/// ran xdg-user-dirs-update) GLib answers nothing or the home folder, and
/// the sidebar lost Desktop, Documents and Downloads.
pub fn xdg_uri(kind: glib::UserDirectory) -> String {
    sonata_backend::userdirs::special(kind, false).map(|p| gio::File::for_path(p).uri().to_string()).unwrap_or_default()
}

fn query(f: &gio::File) -> Option<gio::FileInfo> {
    let info = f.query_info(ATTRS, gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok()?;
    info.set_attribute_object("sonata::file", f);
    Some(info)
}

type Loaded = Box<dyn Fn(&str)>;
type Failed = Box<dyn Fn(&str, &glib::Error)>;

/// `store` holds the current folder's items. load(uri) replaces them;
/// on_loaded(uri) / on_error(uri, error) report the outcome.
pub struct Folder {
    pub store: gio::ListStore,
    pub uri: RefCell<Option<String>>,
    pub show_hidden: Cell<bool>,
    on_loaded: Loaded,
    on_error: Failed,
    on_started: Option<Loaded>,
    /// sort keys, parallel to store (empty: not name-sorted, Recents)
    keys: RefCell<Vec<String>>,
    /// file name -> its sort key (find an item by bisect, not a scan)
    key_of: RefCell<HashMap<String, String>>,
    cancel: RefCell<Option<gio::Cancellable>>,
    monitor: RefCell<Option<gio::FileMonitor>>,
    watched: RefCell<Option<gio::File>>,
    /// Applications: the apps' folders (an app uninstalled leaves the list)
    app_monitors: RefCell<Vec<gio::FileMonitor>>,
    app_reload: RefCell<Option<glib::SourceId>>,
    me: Weak<Folder>,
}

impl Folder {
    pub fn new(on_loaded: impl Fn(&str) + 'static, on_error: impl Fn(&str, &glib::Error) + 'static, on_started: Option<Loaded>) -> Rc<Self> {
        Rc::new_cyclic(|me| Folder {
            store: gio::ListStore::new::<gio::FileInfo>(),
            uri: RefCell::default(),
            show_hidden: Cell::new(false),
            on_loaded: Box::new(on_loaded),
            on_error: Box::new(on_error),
            on_started,
            keys: RefCell::default(),
            key_of: RefCell::default(),
            cancel: RefCell::default(),
            monitor: RefCell::default(),
            watched: RefCell::default(),
            app_monitors: RefCell::default(),
            app_reload: RefCell::default(),
            me: me.clone(),
        })
    }

    pub fn load(&self, uri: &str) {
        if let Some(c) = self.cancel.take() {
            c.cancel();
        }
        let cancel = gio::Cancellable::new();
        self.cancel.replace(Some(cancel.clone()));
        if let Some(s) = &self.on_started {
            s(uri);
        }
        if let Some(m) = self.monitor.take() {
            m.cancel();
        }
        if uri != APPS {
            self.unwatch_apps();
        }
        let uri = uri.to_string();
        let me = self.me.clone();
        if uri == RECENTS {
            // recently used files (GTK's recent list), newest first
            let mut entries: Vec<gtk::RecentInfo> = gtk::RecentManager::default().items().into_iter().filter(|i| i.is_local()).collect();
            entries.sort_by_key(|e| std::cmp::Reverse(e.modified().to_unix()));
            let uris: Vec<String> = entries.iter().take(100).map(|e| e.uri().to_string()).collect();
            self.load_with(uri, cancel, move || uris.iter().map(|u| gio::File::for_uri(u)).filter(|f| f.query_exists(gio::Cancellable::NONE)).collect(), true);
            return;
        }
        if uri == APPS {
            self.load_apps(uri, cancel);
            return;
        }
        if is_virtual(&uri) {
            // a sidebar tag: every item that has it
            let tag = crate::tags::tag_of(&uri).to_string();
            self.load_with(uri, cancel, move || crate::tags::tagged(&tag).iter().map(gio::File::for_path).collect(), false);
            return;
        }
        let d = gio::File::for_uri(&uri);
        glib::spawn_future_local(async move {
            let mut items: Vec<gio::FileInfo> = Vec::new();
            let res: Result<(), glib::Error> = async {
                let en = d.enumerate_children_future(ATTRS, gio::FileQueryInfoFlags::NONE, glib::Priority::DEFAULT).await?;
                loop {
                    if cancel.is_cancelled() {
                        return Ok(());
                    }
                    let batch = en.next_files_future(BATCH, glib::Priority::DEFAULT).await?;
                    if batch.is_empty() {
                        break;
                    }
                    let Some(f) = me.upgrade() else { return Ok(()) };
                    for info in batch {
                        if f.visible(&info) {
                            info.set_attribute_object("sonata::file", &d.child(info.name()));
                            items.push(info);
                        }
                    }
                }
                let _ = en.close_future(glib::Priority::LOW).await;
                Ok(())
            }
            .await;
            let Some(f) = me.upgrade() else { return };
            if cancel.is_cancelled() {
                return;
            }
            match res {
                Ok(()) => {
                    f.fill(&uri, items, false);
                    f.watch(&d);
                }
                Err(e) => (f.on_error)(&uri, &e),
            }
        });
    }

    /// Items gathered off the main loop (work), then shown.
    fn load_with(&self, uri: String, cancel: gio::Cancellable, work: impl FnOnce() -> Vec<gio::File> + Send + 'static, keep_order: bool) {
        let me = self.me.clone();
        glib::spawn_future_local(async move {
            // found off the main loop; a FileInfo can't cross threads, so read here
            let files = gio::spawn_blocking(work).await.unwrap_or_default();
            let items: Vec<gio::FileInfo> = files.iter().filter_map(query).collect();
            let Some(f) = me.upgrade() else { return };
            if cancel.is_cancelled() {
                return;
            }
            f.fill(&uri, items, keep_order);
            if keep_order {
                f.keys.borrow_mut().clear(); // not name-sorted: live inserts go last
            }
        });
    }

    /// Installed apps (the ones Launchpad shows) as their .desktop files,
    /// named and drawn like the app: open = launch, drag = to the Dock.
    fn load_apps(&self, uri: String, cancel: gio::Cancellable) {
        let mut out = Vec::new();
        for info in sonata_ui::apps::scan().into_values() {
            let Some(path) = sonata_ui::apps::app_filename(&info) else { continue };
            if !sonata_ui::apps::should_show(&info) {
                continue;
            }
            let f = gio::File::for_path(&path);
            let Some(fi) = query(&f) else { continue };
            fi.set_display_name(&sonata_ui::apps::display_name(&info));
            fi.set_attribute_object("sonata::app", &info);
            // the icon Sonata draws for the app everywhere (the one chosen in
            // Settings > App Icons, on its plate), as Python's icons.app_icon
            fi.set_icon(&sonata_ui::icons::app_icon(info.upcast_ref()));
            out.push(fi);
        }
        if !cancel.is_cancelled() {
            self.fill(&uri, out, false);
        }
        self.watch_apps();
    }

    /// An app uninstalled (dragged from here to the Trash) stayed listed:
    /// every applications folder is watched while this shows.
    fn watch_apps(&self) {
        if !self.app_monitors.borrow().is_empty() {
            return;
        }
        for d in sonata_ui::apps::app_dirs() {
            let Ok(m) = gio::File::for_path(&d).monitor_directory(gio::FileMonitorFlags::WATCH_MOVES, gio::Cancellable::NONE) else { continue };
            let me = self.me.clone();
            m.connect_changed(move |_, f, _, _| {
                let Some(fo) = me.upgrade() else { return };
                if !f.basename().is_some_and(|b| b.to_string_lossy().ends_with(".desktop")) || fo.uri.borrow().as_deref() != Some(APPS) {
                    return;
                }
                if let Some(id) = fo.app_reload.take() {
                    sonata_ui::cancel(id);
                }
                let me = fo.me.clone();
                let id = glib::timeout_add_local_once(std::time::Duration::from_millis(APPS_RELOAD_MS), move || {
                    if let Some(fo) = me.upgrade() {
                        fo.app_reload.replace(None);
                        if fo.uri.borrow().as_deref() == Some(APPS) {
                            fo.load(APPS);
                        }
                    }
                });
                fo.app_reload.replace(Some(id));
            });
            self.app_monitors.borrow_mut().push(m);
        }
    }

    fn unwatch_apps(&self) {
        for m in self.app_monitors.take() {
            m.cancel();
        }
        if let Some(id) = self.app_reload.take() {
            sonata_ui::cancel(id);
        }
    }

    /// Stop loading and watching (a column that was closed).
    pub fn cancel(&self) {
        if let Some(c) = self.cancel.borrow().as_ref() {
            c.cancel();
        }
        if let Some(m) = self.monitor.take() {
            m.cancel();
        }
    }

    pub fn reload(&self) {
        let uri = self.uri.borrow().clone();
        if let Some(u) = uri {
            self.load(&u);
        }
    }

    fn visible(&self, info: &gio::FileInfo) -> bool {
        self.show_hidden.get() || !(info.is_hidden() || info.is_backup())
    }

    fn fill(&self, uri: &str, mut items: Vec<gio::FileInfo>, keep_order: bool) {
        if !keep_order {
            items.sort_by_cached_key(sort_key);
        }
        let keys: Vec<String> = items.iter().map(sort_key).collect();
        self.key_of.replace(items.iter().zip(&keys).map(|(i, k)| (i.name().to_string_lossy().into_owned(), k.clone())).collect());
        self.keys.replace(keys);
        self.uri.replace(Some(uri.into()));
        self.store.splice(0, self.store.n_items(), &items);
        (self.on_loaded)(uri);
    }

    // -- live updates -------------------------------------------------------------------------
    fn watch(&self, d: &gio::File) {
        let Ok(m) = d.monitor_directory(gio::FileMonitorFlags::WATCH_MOVES, gio::Cancellable::NONE) else { return };
        self.watched.replace(Some(d.clone()));
        let me = self.me.clone();
        m.connect_changed(move |_, f, other, event| {
            if let Some(fo) = me.upgrade() {
                fo.changed(f, other, event);
            }
        });
        self.monitor.replace(Some(m));
    }

    /// `f` is an item of the folder shown -- not the folder itself, whose own
    /// changes the monitor reports too (the Trash listed inside itself).
    fn inside(&self, f: Option<&gio::File>) -> bool {
        let Some(f) = f else { return false };
        let w = self.watched.borrow();
        let Some(d) = w.as_ref() else { return true };
        !f.equal(d) && f.parent().is_some_and(|p| p.equal(d))
    }

    fn changed(&self, f: &gio::File, other: Option<&gio::File>, event: gio::FileMonitorEvent) {
        use gio::FileMonitorEvent as E;
        if !self.inside(Some(f)) && !(event == E::Renamed && self.inside(other)) {
            return;
        }
        let name = |f: &gio::File| f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
        match event {
            E::Deleted | E::MovedOut => self.remove(&name(f)),
            E::Created | E::MovedIn => self.add(f, false),
            E::Renamed => {
                if self.inside(Some(f)) {
                    self.remove(&name(f));
                }
                if let Some(o) = other.filter(|o| self.inside(Some(o))) {
                    self.add(o, false);
                }
            }
            E::ChangesDoneHint | E::AttributeChanged => self.add(f, true),
            _ => {}
        }
    }

    /// O(log n) while the store is name-sorted (a big copy sends one event
    /// per file: a linear scan each made it O(n²)).
    pub fn index(&self, name: &str) -> Option<u32> {
        let n = self.store.n_items();
        let keys = self.keys.borrow();
        let item_name = |i: u32| self.store.item(i).and_downcast::<gio::FileInfo>().map(|f| f.name().to_string_lossy().into_owned());
        if !keys.is_empty() && keys.len() as u32 == n {
            let k = self.key_of.borrow().get(name).cloned()?;
            let mut i = keys.partition_point(|x| *x < k) as u32;
            while i < n && keys[i as usize] == k {
                if item_name(i).as_deref() == Some(name) {
                    return Some(i);
                }
                i += 1;
            }
            return None;
        }
        (0..n).find(|i| item_name(*i).as_deref() == Some(name)) // Recents: not name-sorted
    }

    fn remove(&self, name: &str) {
        if let Some(i) = self.index(name) {
            self.store.remove(i);
            let mut keys = self.keys.borrow_mut();
            if !keys.is_empty() {
                keys.remove(i as usize);
            }
            self.key_of.borrow_mut().remove(name);
        }
    }

    fn add(&self, f: &gio::File, replace: bool) {
        let uri = self.uri.borrow().clone();
        let (me, f) = (self.me.clone(), f.clone());
        glib::spawn_future_local(async move {
            let Ok(info) = f.query_info_future(ATTRS, gio::FileQueryInfoFlags::NONE, glib::Priority::DEFAULT).await else { return };
            let Some(fo) = me.upgrade() else { return };
            if *fo.uri.borrow() != uri {
                return;
            }
            let name = info.name().to_string_lossy().into_owned();
            if fo.index(&name).is_some() {
                if !replace {
                    return;
                }
                fo.remove(&name);
            }
            if !fo.visible(&info) {
                return;
            }
            info.set_attribute_object("sonata::file", &f);
            let k = sort_key(&info);
            let at = fo.keys.borrow().partition_point(|x| *x <= k);
            let sorted = !fo.keys.borrow().is_empty() || fo.store.n_items() == 0;
            if sorted {
                fo.keys.borrow_mut().insert(at, k.clone());
                fo.key_of.borrow_mut().insert(name, k);
                fo.store.insert(at as u32, &info);
            } else {
                fo.store.append(&info);
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn places_and_names() {
        assert_eq!(canonical("sonata:///recents"), "sonata:recents");
        assert_eq!(canonical("sonata:/tag/Red"), "sonata:tag/Red");
        assert_eq!(canonical("file:///x"), "file:///x");
        assert!(is_virtual(RECENTS) && is_virtual(&crate::tags::uri("Red")) && !is_virtual("file:///"));
        assert_eq!(short_name("A very long video name that goes on.mp4", 20), "A very long vid….mp4");
        assert_eq!(short_name("short.txt", 30), "short.txt");
        sonata_i18n::use_language("en");
        assert_eq!(display_name(&crate::tags::uri("Red")), "Red");
    }

    /// Without ~/.config/user-dirs.dirs the sidebar showed no Desktop,
    /// Documents or Downloads (GLib knew none), though the folders were there.
    #[test]
    fn test_user_folders_missing_without_user_dirs_file() {
        let home = glib::home_dir();
        // only in the isolated test home: never makes folders in a real one
        if !home.starts_with(std::env::temp_dir()) || glib::user_config_dir().join("user-dirs.dirs").exists() {
            return;
        }
        let _ = std::fs::create_dir(home.join("Downloads"));
        assert_eq!(xdg_uri(glib::UserDirectory::Downloads), gio::File::for_path(home.join("Downloads")).uri());
    }

    #[test]
    fn folders_first_and_live() {
        let d = tempfile::tempdir().unwrap();
        for n in ["b.txt", "A.txt", "10.txt", "9.txt", ".hidden"] {
            std::fs::write(d.path().join(n), "").unwrap();
        }
        std::fs::create_dir(d.path().join("zdir")).unwrap();
        // its own main context: the directory monitor and the loading
        // future left on the default one fired on the next test's thread
        // (glib's thread guard aborted the whole run in mountop's test)
        let ctx = glib::MainContext::new();
        ctx.with_thread_default(|| live_listing(&ctx, d.path())).unwrap();
    }

    fn live_listing(ctx: &glib::MainContext, dir: &std::path::Path) {
        let d = dir;
        let loaded = Rc::new(Cell::new(false));
        let l = loaded.clone();
        let f = Folder::new(move |_| l.set(true), |_, e| panic!("{e}"), None);
        f.load(&gio::File::for_path(d).uri());
        let spin = |c: &dyn Fn() -> bool| {
            let end = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while !c() && std::time::Instant::now() < end {
                ctx.iteration(false);
            }
        };
        spin(&|| loaded.get());
        let names = |f: &Folder| (0..f.store.n_items()).map(|i| f.store.item(i).and_downcast::<gio::FileInfo>().unwrap().name().to_string_lossy().into_owned()).collect::<Vec<_>>();
        assert_eq!(names(&f), ["zdir", "9.txt", "10.txt", "A.txt", "b.txt"]); // folders first, numbers numerically
        assert_eq!(f.index("A.txt"), Some(3));
        std::fs::write(d.join("c.txt"), "").unwrap();
        spin(&|| f.store.n_items() == 6);
        assert_eq!(names(&f).last().map(String::as_str), Some("c.txt"));
        std::fs::remove_file(d.join("9.txt")).unwrap();
        spin(&|| f.store.n_items() == 5);
        assert!(!names(&f).contains(&"9.txt".to_string()));
        f.cancel();
    }
}
