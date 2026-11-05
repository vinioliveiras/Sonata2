//! Files sidebar (files/sidebar.py; Finder's source list): Favorites
//! (Recents, Applications, Desktop, Documents, Downloads, home, GTK
//! bookmarks), Locations (Computer, mounted drives with Eject on their icon
//! and a capacity meter, disks not mounted yet, Network, Trash) and Tags. Live:
//! rebuilt when drives come and go or pins change.
//!
//! Pin a folder: drag it between two Favorites -- a line shows where it
//! goes -- or onto the Favorites heading; a pinned one dragged there moves.
//! Right-click a pinned folder: Remove from Sidebar. Pins are GTK's
//! bookmarks (shared with file dialogs). A folder place dragged out goes on
//! the Dock as a stack; it is offered as a link only, so a place can't copy
//! or move a whole folder (Home) by mistake.

use crate::folder;
use gtk::prelude::*;
use gtk::{gdk, pango};
use sonata_i18n::{fmt, n_, tr};
use std::cell::{Cell, RefCell};
use std::path::PathBuf;
use std::rc::{Rc, Weak};

/// px at a row's top/bottom where a drop pins instead of moving
const EDGE: f64 = 7.0;
const EJECT_FADE_MS: u32 = 160;

pub fn bookmarks_path() -> PathBuf {
    glib::user_config_dir().join("gtk-3.0").join("bookmarks")
}

/// [(uri, label)] of GTK's bookmarks file.
pub fn read_bookmarks() -> Vec<(String, String)> {
    std::fs::read_to_string(bookmarks_path())
        .map(|t| {
            t.lines()
                .filter_map(|l| {
                    let l = l.trim();
                    let (u, label) = l.split_once(' ').unwrap_or((l, ""));
                    (!u.is_empty()).then(|| (u.to_string(), label.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

pub fn write_bookmarks(marks: &[(String, String)]) {
    let text: String = marks.iter().map(|(u, l)| format!("{}\n", format!("{u} {l}").trim_end())).collect();
    let _ = sonata_config::atomic_write(&bookmarks_path(), text.as_bytes(), true);
}

thread_local! {
    /// live sidebars of this process: rebuilt when pins change
    static SIDEBARS: RefCell<Vec<Weak<Sidebar>>> = const { RefCell::new(Vec::new()) };
}

fn changed() {
    let all: Vec<Rc<Sidebar>> = SIDEBARS.with(|s| s.borrow().iter().filter_map(Weak::upgrade).collect());
    for sb in all {
        sb.rebuild();
    }
}

/// Add folders to Favorites, before the pinned `before` (else at the end);
/// one pinned already moves there (its label kept).
pub fn pin(uris: &[String], before: Option<&str>) {
    if before.is_some_and(|b| uris.iter().any(|u| u == b)) {
        return; // dropped next to itself: stays
    }
    let marks = read_bookmarks();
    let label = |u: &str| marks.iter().find(|m| m.0 == u).map(|m| m.1.clone()).unwrap_or_default();
    let mut rest: Vec<(String, String)> = marks.iter().filter(|(u, _)| !uris.contains(u)).cloned().collect();
    let mut new: Vec<(String, String)> = Vec::new();
    for u in uris {
        if !new.iter().any(|n| n.0 == *u) {
            new.push((u.clone(), label(u)));
        }
    }
    let at = before.and_then(|b| rest.iter().position(|(u, _)| u == b)).unwrap_or(rest.len());
    rest.splice(at..at, new);
    write_bookmarks(&rest);
    changed();
}

/// A pinned folder renamed: the pin follows it (and keeps its place).
pub fn moved(old: &str, new: &str) {
    let marks = read_bookmarks();
    if marks.iter().any(|(u, _)| u == old) {
        write_bookmarks(&marks.into_iter().map(|(u, l)| (if u == old { new.to_string() } else { u }, l)).collect::<Vec<_>>());
        changed();
    }
}

pub fn unpin(uri: &str) {
    write_bookmarks(&read_bookmarks().into_iter().filter(|(u, _)| u != uri).collect::<Vec<_>>());
    changed();
}

/// The local folder behind a place, else None: Recents, Network, Trash,
/// tags and drives not mounted can't be dragged out.
pub fn place_folder(uri: &str) -> Option<gio::File> {
    if !uri.starts_with("file://") || folder::is_virtual(uri) {
        return None;
    }
    let f = gio::File::for_uri(uri);
    f.path().filter(|p| p.is_dir()).map(|_| f)
}

fn favorites() -> Vec<(String, String, String)> {
    let mut out = vec![(tr!("Recents"), "document-open-recent-symbolic".to_string(), folder::RECENTS.to_string()), (tr!("Applications"), "view-app-grid-symbolic".into(), folder::APPS.into())];
    for (kind, title, icon) in [(glib::UserDirectory::Desktop, tr!("Desktop"), "user-desktop-symbolic"), (glib::UserDirectory::Documents, tr!("Documents"), "folder-documents-symbolic"), (glib::UserDirectory::Downloads, tr!("Downloads"), "folder-download-symbolic")] {
        let uri = folder::xdg_uri(kind);
        if !uri.is_empty() {
            out.push((title, icon.into(), uri));
        }
    }
    out.push((glib::user_name().to_string_lossy().into_owned(), "user-home-symbolic".into(), gio::File::for_path(glib::home_dir()).uri().to_string()));
    for (uri, label) in read_bookmarks() {
        if out.iter().any(|o| o.2 == uri) {
            continue;
        }
        let f = gio::File::for_uri(&uri);
        let name = if label.is_empty() { f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_else(|| uri.clone()) } else { label };
        out.push((name, "folder-symbolic".into(), uri));
    }
    out
}

/// The sidebar's links to its window.
#[derive(Default)]
pub struct Hooks {
    pub on_open: Option<Box<dyn Fn(&str)>>,
    /// (files, folder, copy) -> taken
    pub on_drop: Option<Box<dyn Fn(Vec<gio::File>, gio::File, bool) -> bool>>,
    pub on_tag: Option<Box<dyn Fn(Vec<PathBuf>, &str)>>,
    pub on_info: Option<Box<dyn Fn(&str)>>,
    pub on_new_tab: Option<Box<dyn Fn(&str)>>,
}

struct Row {
    row: gtk::ListBoxRow,
    uri: String,
    favorite: bool,
    pinned: bool,
    meter: Option<(gtk::ProgressBar, gtk::Label)>,
    volume: Option<gio::Volume>,
}

pub struct Sidebar {
    pub root: gtk::Box,
    pub list: gtk::ListBox,
    rows: RefCell<Vec<Row>>,
    quiet: Cell<bool>,
    current: RefCell<Option<String>>,
    pub hooks: RefCell<Hooks>,
    volumes: gio::VolumeMonitor,
    _marks: Option<gio::FileMonitor>,
    rebuild_src: RefCell<Option<glib::SourceId>>,
    me: Weak<Sidebar>,
}

impl Sidebar {
    pub fn new(top: &impl IsA<gtk::Widget>) -> Rc<Self> {
        sonata_ui::css::register_module("files/sidebar.py");
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["fs-sidebar", "sonata-sidebar"]).build();
        root.append(&gtk::WindowHandle::builder().child(top).css_classes(["fs-sidebar-top"]).build());
        let list = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).build();
        root.append(&gtk::ScrolledWindow::builder().child(&list).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build());
        let _ = std::fs::create_dir_all(bookmarks_path().parent().unwrap()); // (a monitor needs the folder)
        let marks = gio::File::for_path(bookmarks_path()).monitor_file(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE).ok();
        let sb = Rc::new_cyclic(|me| Sidebar { root, list, rows: RefCell::default(), quiet: Cell::new(false), current: RefCell::default(), hooks: RefCell::default(), volumes: gio::VolumeMonitor::get(), _marks: marks, rebuild_src: RefCell::default(), me: me.clone() });
        let w = sb.me.clone();
        sb.list.connect_row_activated(move |_, r| {
            if let Some(s) = w.upgrade() {
                s.activated(r);
            }
        });
        for sig in ["mount-added", "mount-removed", "mount-changed", "volume-added", "volume-removed"] {
            let w = sb.me.clone();
            sb.volumes.connect_local(sig, false, move |_| {
                if let Some(s) = w.upgrade() {
                    s.rebuild_soon();
                }
                None
            });
        }
        if let Some(m) = &sb._marks {
            let w = sb.me.clone();
            m.connect_changed(move |_, _, _, _| {
                if let Some(s) = w.upgrade() {
                    s.rebuild_soon(); // other apps' pins
                }
            });
        }
        SIDEBARS.with(|s| s.borrow_mut().push(sb.me.clone()));
        sb.rebuild();
        sb
    }

    fn rebuild_soon(&self) {
        if self.rebuild_src.borrow().is_some() {
            return;
        }
        let w = self.me.clone();
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis(150), move || {
            if let Some(s) = w.upgrade() {
                s.rebuild_src.replace(None);
                s.rebuild();
            }
        });
        self.rebuild_src.replace(Some(id));
    }

    pub fn rebuild(&self) {
        self.list.remove_all();
        self.rows.borrow_mut().clear();
        let head = self.head(n_!("Favorites"));
        self.pin_target(&head);
        let pinned: Vec<String> = read_bookmarks().into_iter().map(|m| m.0).collect();
        for (title, icon, uri) in favorites() {
            let p = pinned.contains(&uri);
            self.place(&title, Some(&icon), None, &uri, None, false, true, p, None);
        }
        self.head(n_!("Locations"));
        self.place(&tr!("Computer"), Some("drive-harddisk-symbolic"), None, "file:///", None, true, false, false, None);
        for mount in self.volumes.mounts() {
            if mount.is_shadowed() {
                continue;
            }
            let uri = mount.root().uri().to_string();
            if self.rows.borrow().iter().any(|r| r.uri == uri) {
                continue;
            }
            let ejectable = mount.can_eject() || mount.can_unmount();
            self.place(&mount.name(), Some("drive-removable-media-symbolic"), Some(mount.symbolic_icon()), &uri, ejectable.then_some(mount.clone()), true, false, false, None);
        }
        // disks not mounted yet (still mounting, or one that couldn't be): a click mounts it
        for vol in self.volumes.volumes() {
            if vol.get_mount().is_some() || !vol.can_mount() {
                continue;
            }
            let id = vol.uuid().map(|u| u.to_string()).or_else(|| vol.identifier("unix-device").map(|u| u.to_string())).unwrap_or_else(|| vol.name().to_string());
            let uri = format!("volume:{id}");
            if self.rows.borrow().iter().any(|r| r.uri == uri) {
                continue;
            }
            self.place(&vol.name(), Some("drive-harddisk-symbolic"), Some(vol.symbolic_icon()), &uri, None, true, false, false, None);
            if let Some(r) = self.rows.borrow_mut().last_mut() {
                r.volume = Some(vol);
            }
        }
        if crate::server::can_browse_network() {
            // Finder's Network: the computers and shares nearby
            self.place(&tr!("Network"), Some("network-workgroup-symbolic"), None, crate::server::NETWORK, None, false, false, false, None);
        }
        self.place(&tr!("Trash"), Some("user-trash-symbolic"), None, "trash:///", None, false, false, false, None);
        self.head(n_!("Tags"));
        for (name, _) in crate::tags::COLORS {
            self.place(&sonata_i18n::gettext(name), None, None, &crate::tags::uri(name), None, false, false, false, Some(name));
        }
        let cur = self.current.borrow().clone();
        if let Some(c) = cur {
            self.select(&c);
        }
    }

    /// A heading: `text` in English, shown translated.
    fn head(&self, text: &str) -> gtk::ListBoxRow {
        let row = gtk::ListBoxRow::builder().selectable(false).activatable(false).css_classes(["fs-head"]).build();
        row.set_child(Some(&gtk::Label::builder().label(sonata_i18n::gettext(text)).xalign(0.0).margin_start(2).build()));
        self.list.append(&row);
        row
    }

    fn folders(files: &[gio::File]) -> Vec<String> {
        files.iter().filter(|f| f.query_file_type(gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) == gio::FileType::Directory).map(|f| f.uri().to_string()).collect()
    }

    /// A drop target that only pins (the Favorites heading: at the end).
    fn pin_target(&self, row: &gtk::ListBoxRow) {
        let tgt = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE | gdk::DragAction::LINK);
        let r = row.downgrade();
        tgt.connect_enter(move |t, _, _| {
            if let Some(r) = r.upgrade() {
                r.add_css_class("pin-after");
            }
            pin_action(t)
        });
        tgt.connect_motion(|t, _, _| pin_action(t));
        let r = row.downgrade();
        tgt.connect_leave(move |_| {
            if let Some(r) = r.upgrade() {
                r.remove_css_class("pin-after");
            }
        });
        let r = row.downgrade();
        tgt.connect_drop(move |_, v, _, _| {
            if let Some(r) = r.upgrade() {
                r.remove_css_class("pin-after");
            }
            if let Ok(l) = v.get::<gdk::FileList>() {
                pin(&Self::folders(&l.files()), None);
            }
            true
        });
        row.add_controller(tgt);
    }

    /// The pinned uri a new pin goes before, for a drop at `zone` of `row`.
    fn next_pinned(&self, uri: &str, zone: &str) -> Option<String> {
        let rows = self.rows.borrow();
        let favs: Vec<&Row> = rows.iter().filter(|r| r.favorite).collect();
        let i = favs.iter().position(|r| r.uri == uri)? + usize::from(zone == "after");
        favs[i..].iter().find(|r| r.pinned).map(|r| r.uri.clone())
    }

    #[allow(clippy::too_many_arguments)]
    fn place(&self, title: &str, icon: Option<&str>, gicon: Option<gio::Icon>, uri: &str, mount: Option<gio::Mount>, disk: bool, favorite: bool, pinned: bool, tag: Option<&str>) {
        let row = gtk::ListBoxRow::new();
        let bx = gtk::Box::new(gtk::Orientation::Horizontal, 7);
        let img: gtk::Widget = match tag {
            Some(t) => {
                let d = crate::views::tag_dot(t, 10);
                d.set_margin_start(3); // 16 px wide: lined up with the icons above
                d.set_margin_end(3);
                d.upcast()
            }
            None => {
                let i = gtk::Image::builder().pixel_size(16).css_classes(["fs-place"]).build();
                match &gicon {
                    Some(g) => i.set_from_gicon(g),
                    None => i.set_icon_name(icon),
                }
                i.upcast()
            }
        };
        let img = match &mount {
            Some(m) if disk => self.eject_icon(img, m.clone()), // the disk's icon turns into Eject on hover
            _ => img,
        };
        bx.append(&img);
        let name = gtk::Label::builder().label(title).xalign(0.0).hexpand(true).ellipsize(pango::EllipsizeMode::End).css_classes(["fs-place"]).build();
        let mut meter = None;
        if disk {
            // name and free space on one line, the meter under them
            row.add_css_class("fs-disk");
            let grid = gtk::Grid::builder().column_spacing(6).row_spacing(3).hexpand(true).valign(gtk::Align::Center).build();
            let m = sonata_ui::progress::meter(0.0);
            m.set_hexpand(true);
            m.set_valign(gtk::Align::Center);
            let free = gtk::Label::builder().css_classes(["fs-free"]).xalign(1.0).valign(gtk::Align::Baseline).build();
            name.set_valign(gtk::Align::Baseline);
            grid.attach(&name, 0, 0, 1, 1);
            grid.attach(&free, 1, 0, 1, 1);
            grid.attach(&m, 0, 1, 2, 1);
            bx.append(&grid);
            let menu = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
            let (w, u, t, mo) = (self.me.clone(), uri.to_string(), title.to_string(), mount.clone());
            menu.connect_pressed(move |g, _, x, y| {
                if let (Some(s), Some(r)) = (w.upgrade(), g.widget()) {
                    s.disk_menu(&r, &u, &t, mo.clone(), x, y);
                }
            });
            row.add_controller(menu);
            meter = Some((m, free));
        } else {
            bx.append(&name);
        }
        row.set_child(Some(&bx));
        if let Some(t) = tag {
            self.tag_target(&row, t);
        } else if !folder::is_virtual(uri) || favorite {
            self.drop_target(&row, uri, favorite);
        }
        if tag.is_none() {
            place_drag(&row, uri);
        }
        if pinned {
            let menu = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
            let u = uri.to_string();
            menu.connect_pressed(move |g, _, x, y| {
                let Some(r) = g.widget() else { return };
                let u = u.clone();
                sonata_ui::menu::popup(&r, vec![vec![sonata_ui::menu::Item::new(&tr!("Remove from Sidebar"), move || unpin(&u))]], sonata_ui::menu::Options::at(x, y).passthrough());
            });
            row.add_controller(menu);
        }
        self.list.append(&row);
        if let Some((m, f)) = &meter {
            read_space(uri, m, f, &row);
        }
        self.rows.borrow_mut().push(Row { row, uri: uri.into(), favorite, pinned, meter, volume: None });
    }

    /// Files dropped on a place move/copy there; on a Favorite's top or
    /// bottom edge, folders get pinned there instead (a line shows it).
    fn drop_target(&self, row: &gtk::ListBoxRow, uri: &str, favorite: bool) {
        let tgt = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE | gdk::DragAction::LINK);
        let into_ok = !folder::is_virtual(uri);
        let show = {
            let r = row.downgrade();
            move |zone: Option<&str>| {
                let Some(r) = r.upgrade() else { return };
                for c in ["drop-target", "pin-before", "pin-after"] {
                    r.remove_css_class(c);
                }
                match zone {
                    Some("into") if into_ok => r.add_css_class("drop-target"),
                    Some(z @ ("before" | "after")) => r.add_css_class(&format!("pin-{z}")),
                    _ => {}
                }
            }
        };
        let zone_at = {
            let r = row.downgrade();
            move |y: f64| -> &'static str {
                if !favorite {
                    return "into";
                }
                let h = r.upgrade().map(|r| r.height() as f64).unwrap_or(0.0);
                if y < EDGE {
                    "before"
                } else if y > h - EDGE {
                    "after"
                } else {
                    "into"
                }
            }
        };
        let motion = {
            let (show, zone_at) = (show.clone(), zone_at.clone());
            move |t: &gtk::DropTarget, y: f64| -> gdk::DragAction {
                let zone = zone_at(y);
                if zone == "into" && link_only(t) {
                    show(None); // a place dragged onto another: not moved into it
                    return gdk::DragAction::empty();
                }
                show(Some(zone));
                if zone == "into" {
                    return if into_ok { gdk::DragAction::MOVE } else { gdk::DragAction::empty() };
                }
                pin_action(t)
            }
        };
        let m = motion.clone();
        tgt.connect_enter(move |t, _, y| m(t, y));
        tgt.connect_motion(move |t, _, y| motion(t, y));
        let s = show.clone();
        tgt.connect_leave(move |_| s(None));
        let (w, u) = (self.me.clone(), uri.to_string());
        tgt.connect_drop(move |t, v, _, y| {
            let zone = zone_at(y);
            show(None);
            if zone == "into" && link_only(t) {
                return false;
            }
            let (Some(s), Ok(list)) = (w.upgrade(), v.get::<gdk::FileList>()) else { return false };
            let files = list.files();
            if zone != "into" {
                let before = s.next_pinned(&u, zone);
                pin(&Self::folders(&files), before.as_deref());
                return true;
            }
            if !into_ok {
                return false;
            }
            let copy = t.current_event_state().contains(gdk::ModifierType::CONTROL_MASK);
            let hooks = s.hooks.borrow();
            hooks.on_drop.as_ref().is_some_and(|d| d(files, gio::File::for_uri(&u), copy))
        });
        row.add_controller(tgt);
    }

    /// Files dropped on a tag get it (Finder; COPY: the files stay where they are).
    fn tag_target(&self, row: &gtk::ListBoxRow, tag: &str) {
        let tgt = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE | gdk::DragAction::LINK);
        let r = row.downgrade();
        tgt.connect_enter(move |_, _, _| {
            if let Some(r) = r.upgrade() {
                r.add_css_class("drop-target");
            }
            gdk::DragAction::COPY
        });
        tgt.connect_motion(|_, _, _| gdk::DragAction::COPY);
        let r = row.downgrade();
        tgt.connect_leave(move |_| {
            if let Some(r) = r.upgrade() {
                r.remove_css_class("drop-target");
            }
        });
        let (w, r, tag) = (self.me.clone(), row.downgrade(), tag.to_string());
        tgt.connect_drop(move |_, v, _, _| {
            if let Some(r) = r.upgrade() {
                r.remove_css_class("drop-target");
            }
            let (Some(s), Ok(list)) = (w.upgrade(), v.get::<gdk::FileList>()) else { return false };
            let paths: Vec<PathBuf> = list.files().iter().filter_map(|f| f.path()).collect();
            let hooks = s.hooks.borrow();
            match (&hooks.on_tag, paths.is_empty()) {
                (Some(t), false) => {
                    t(paths, &tag);
                    true
                }
                _ => false,
            }
        });
        row.add_controller(tgt);
    }

    pub fn refresh_space(&self) {
        for r in self.rows.borrow().iter() {
            if let Some((m, f)) = &r.meter {
                read_space(&r.uri, m, f, &r.row);
            }
        }
    }

    fn activated(&self, row: &gtk::ListBoxRow) {
        if self.quiet.get() {
            return;
        }
        let (uri, vol) = match self.rows.borrow().iter().find(|r| r.row == *row) {
            Some(r) => (r.uri.clone(), r.volume.clone()),
            None => return,
        };
        if let Some(vol) = vol {
            // not mounted yet: mount, then open it
            let op = crate::mountop::new(self.window().as_ref());
            let w = self.me.clone();
            vol.mount(gio::MountMountFlags::NONE, Some(&op), gio::Cancellable::NONE, glib::clone!(#[strong] vol, move |res| {
                let Some(s) = w.upgrade() else { return };
                match res {
                    Ok(()) => s.open_volume(&vol),
                    Err(e) => match vol.identifier("unix-device") {
                        // a Windows disk left dirty: read-only
                        Some(dev) if crate::rodisk::try_readonly(&dev, e.message()) => s.open_readonly(&vol, &dev, e.message()),
                        _ => s.cant_open(&vol, e.message()),
                    },
                }
            }));
            return;
        }
        if let Some(open) = self.hooks.borrow().on_open.as_ref() {
            open(&uri);
        }
    }

    fn window(&self) -> Option<gtk::Window> {
        self.root.root().and_downcast::<gtk::Window>()
    }

    /// Open the disk just mounted -- this one, not the first disk with a
    /// mount (that opened another disk). Its mount may show up a moment
    /// after the mount finished: then it opens when it does.
    fn open_volume(&self, vol: &gio::Volume) {
        if let Some(m) = vol.get_mount() {
            if let Some(open) = self.hooks.borrow().on_open.as_ref() {
                open(&m.root().uri());
            }
            return;
        }
        let w = self.me.clone();
        let id = std::rc::Rc::new(std::cell::RefCell::new(None::<glib::SignalHandlerId>));
        let i = id.clone();
        let h = vol.connect_changed(move |v| {
            if v.get_mount().is_some() {
                if let Some(h) = i.borrow_mut().take() {
                    v.disconnect(h);
                }
                if let Some(s) = w.upgrade() {
                    s.open_volume(v);
                }
            }
        });
        id.replace(Some(h));
    }

    fn cant_open(&self, vol: &gio::Volume, why: &str) {
        sonata_ui::dialog::alert(&fmt(&tr!("“{name}” couldn't be opened."), &[("name", &vol.name())]), why, &[("ok", "OK".into(), "default")], |_| {}, Some(self.root.upcast_ref()));
    }

    /// Opened read-only (rodisk.rs), and said why -- once, when it opens.
    fn open_readonly(&self, vol: &gio::Volume, device: &str, why: &str) {
        let (w, vol, why) = (self.me.clone(), vol.clone(), why.to_string());
        crate::rodisk::mount_readonly(device, move |res| {
            let Some(s) = w.upgrade() else { return };
            let path = match res {
                Ok(p) => p,
                Err(e) => return s.cant_open(&vol, if e.is_empty() { &why } else { &e }),
            };
            let uri = vol.get_mount().map(|m| m.root().uri().to_string()).or_else(|| (!path.is_empty()).then(|| gio::File::for_path(&path).uri().to_string()));
            if let (Some(u), Some(open)) = (uri, s.hooks.borrow().on_open.as_ref()) {
                open(&u);
            }
            sonata_ui::dialog::alert(&fmt(&tr!("“{name}” is read-only."), &[("name", &vol.name())]), &sonata_i18n::gettext(crate::rodisk::NOTE), &[("ok", "OK".into(), "default")], |_| {}, Some(s.root.upcast_ref()));
        });
    }

    /// Highlight the place showing `uri` (none if it isn't one).
    pub fn select(&self, uri: &str) {
        self.current.replace(Some(uri.into()));
        let row = self.rows.borrow().iter().find(|r| r.uri == uri).map(|r| r.row.clone());
        self.quiet.set(true);
        match row {
            Some(r) => self.list.select_row(Some(&r)),
            None => self.list.unselect_all(),
        }
        self.quiet.set(false);
    }

    /// The disk's icon, crossfading to Eject while the pointer is on it; a
    /// click on it then ejects.
    fn eject_icon(&self, img: gtk::Widget, mount: gio::Mount) -> gtk::Widget {
        let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(EJECT_FADE_MS).valign(gtk::Align::Center).css_classes(["fs-eject-icon"]).tooltip_text(tr!("Eject")).build();
        stack.add_named(&img, Some("disk"));
        stack.add_named(&gtk::Image::builder().icon_name("media-eject-symbolic").pixel_size(16).css_classes(["fs-place"]).build(), Some("eject"));
        let hover = gtk::EventControllerMotion::new();
        let s = stack.downgrade();
        hover.connect_enter(move |_, _, _| {
            if let Some(s) = s.upgrade() {
                s.set_visible_child_name("eject");
            }
        });
        let s = stack.downgrade();
        hover.connect_leave(move |_| {
            if let Some(s) = s.upgrade() {
                s.set_visible_child_name("disk");
            }
        });
        stack.add_controller(hover);
        let click = gtk::GestureClick::builder().button(gdk::BUTTON_PRIMARY).build();
        let w = self.me.clone();
        click.connect_pressed(move |g, _, _, _| {
            g.set_state(gtk::EventSequenceState::Claimed); // ejecting, not opening the disk
            if let Some(s) = w.upgrade() {
                s.eject(&mount);
            }
        });
        stack.add_controller(click);
        stack.upcast()
    }

    /// A disk's menu: open it in a new tab, Get Info, Eject, Disk Utility.
    fn disk_menu(&self, row: &gtk::Widget, uri: &str, title: &str, mount: Option<gio::Mount>, x: f64, y: f64) {
        use sonata_ui::menu::Item;
        let (w1, w2, w3) = (self.me.clone(), self.me.clone(), self.me.clone());
        let (u1, u2) = (uri.to_string(), uri.to_string());
        let mut first = Vec::new();
        // a window with tabs only (the Open / Save panels have none)
        if self.hooks.borrow().on_new_tab.is_some() {
            first.push(Item::new(&tr!("Open in New Tab"), move || {
                if let Some(s) = w1.upgrade() {
                    if let Some(f) = s.hooks.borrow().on_new_tab.as_ref() {
                        f(&u1);
                    }
                }
            }));
        }
        first.push(Item::new(&tr!("Get Info"), move || {
                if let Some(s) = w2.upgrade() {
                    if let Some(f) = s.hooks.borrow().on_info.as_ref() {
                        f(&u2);
                    }
                }
            }));
        let mut sections = vec![first];
        if let Some(m) = mount {
            let verb = if m.can_eject() { tr!("Eject “{name}”") } else { tr!("Unmount “{name}”") };
            sections.push(vec![Item::new(&fmt(&verb, &[("name", title)]), move || {
                if let Some(s) = w3.upgrade() {
                    s.eject(&m);
                }
            })]);
        }
        sections.push(vec![Item::new(&tr!("Open Disk Utility"), || {
            if let Some(info) = sonata_ui::apps::lookup("io.github.vinioliveiras.sonata2.diskutil") {
                let _ = info.launch(&[], None::<&gio::AppLaunchContext>);
            }
        })]);
        sonata_ui::menu::popup(row, sections, sonata_ui::menu::Options::at(x, y).passthrough());
    }

    fn eject(&self, mount: &gio::Mount) {
        let op = crate::mountop::new(self.window().as_ref());
        let (w, name) = (self.me.clone(), mount.name().to_string());
        let done = move |res: Result<(), glib::Error>| {
            if let (Err(e), Some(s)) = (res, w.upgrade()) {
                sonata_ui::dialog::alert(&fmt(&tr!("The disk “{name}” wasn't ejected."), &[("name", &name)]), e.message(), &[("ok", "OK".into(), "default")], |_| {}, Some(s.root.upcast_ref()));
            }
        };
        if mount.can_eject() {
            mount.eject_with_operation(gio::MountUnmountFlags::NONE, Some(&op), gio::Cancellable::NONE, done);
        } else {
            mount.unmount_with_operation(gio::MountUnmountFlags::NONE, Some(&op), gio::Cancellable::NONE, done);
        }
    }
}

/// Pinning answers COPY (nothing is copied), or LINK for a place's drag.
fn pin_action(t: &gtk::DropTarget) -> gdk::DragAction {
    if link_only(t) { gdk::DragAction::LINK } else { gdk::DragAction::COPY }
}

/// The drag only links (a place from a sidebar).
fn link_only(t: &gtk::DropTarget) -> bool {
    t.current_drop().is_some_and(|d| !d.actions().intersects(gdk::DragAction::COPY | gdk::DragAction::MOVE))
}

/// A folder place dragged out (onto the Dock: a stack), as a link only.
fn place_drag(row: &gtk::ListBoxRow, uri: &str) {
    if place_folder(uri).is_none() {
        return;
    }
    let src = gtk::DragSource::builder().actions(gdk::DragAction::LINK).build();
    let u = uri.to_string();
    src.connect_prepare(move |_, _, _| place_folder(&u).map(|f| crate::ops::file_content(&[f], false, false)));
    // its icon hanging under the pointer like a file's (sidebar.py _place_drag)
    let (u, r) = (uri.to_string(), row.downgrade());
    src.connect_drag_begin(move |_, drag| {
        let (Some(f), Some(r)) = (place_folder(&u), r.upgrade()) else { return };
        let gicon = f.query_info("standard::icon", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok().and_then(|i| i.icon()).unwrap_or_else(|| gio::ThemedIcon::new("folder").upcast());
        if let Some(p) = sonata_ui::icons::paintable(&r, &gicon, 64) {
            crate::views::hang(drag, &p);
        }
    });
    row.add_controller(src);
}

fn read_space(uri: &str, meter: &gtk::ProgressBar, free: &gtk::Label, row: &gtk::ListBoxRow) {
    let (m, fr, r) = (meter.downgrade(), free.downgrade(), row.downgrade());
    let f = gio::File::for_uri(uri);
    glib::spawn_future_local(async move {
        let res = f.query_filesystem_info_future("filesystem::size,filesystem::free", glib::Priority::LOW).await;
        let (Some(m), Some(fr), Some(r)) = (m.upgrade(), fr.upgrade(), r.upgrade()) else { return };
        let Ok(info) = res else {
            m.set_visible(false);
            return;
        };
        let (total, free) = (info.attribute_uint64("filesystem::size"), info.attribute_uint64("filesystem::free"));
        if total == 0 {
            if let Some(p) = m.parent() {
                p.set_visible(false);
            }
            return;
        }
        sonata_ui::progress::set_meter(&m, (total - free) as f64 / total as f64);
        let size = sonata_ui::fmt::size;
        fr.set_label(&fmt(&tr!("{size} free"), &[("size", &size(free))]));
        r.set_tooltip_text(Some(&format!("{}\n{}", fmt(&tr!("{free} available of {total}"), &[("free", &size(free)), ("total", &size(total))]), fmt(&tr!("{size} used"), &[("size", &size(total - free))]))));
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A click on a disk not mounted yet opened whichever disk was mounted
    /// first, not the one clicked; and its error said “” couldn't be opened
    /// (no name).
    #[test]
    fn test_mounting_a_disk_opened_another_disk() {
        let src = include_str!("sidebar.rs");
        assert!(!src.contains(concat!("volumes().iter().find_map(|v| v", ".get_mount())")));
        assert!(!src.contains(concat!("couldn't be opened.\"), &[(\"name\", \"\")]")));
        assert!(src.contains("Ok(()) => s.open_volume(&vol)"));
    }

    #[test]
    fn pins_move_and_follow_renames() {
        // (XDG_CONFIG_HOME is a temp folder in the test run)
        write_bookmarks(&[("file:///a".into(), "".into()), ("file:///b".into(), "Bee".into())]);
        pin(&["file:///c".into()], Some("file:///b"));
        assert_eq!(read_bookmarks().iter().map(|m| m.0.as_str()).collect::<Vec<_>>(), ["file:///a", "file:///c", "file:///b"]);
        pin(&["file:///b".into()], Some("file:///a")); // a pinned one moves, its label kept
        assert_eq!(read_bookmarks()[0], ("file:///b".to_string(), "Bee".to_string()));
        moved("file:///c", "file:///d");
        assert!(read_bookmarks().iter().any(|m| m.0 == "file:///d"));
        unpin("file:///d");
        assert_eq!(read_bookmarks().len(), 2);
    }
}
