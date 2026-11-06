//! Finder's path bar (files/pathbar.py) -- Windows' address bar too: one
//! button per folder from your home (or the disk's top), at the bottom of
//! the window. A click opens that folder; files dropped on a folder go
//! into it. A click on its empty part types an address; right-click: Copy
//! Path. And Go to Folder's resolve().

use crate::folder;
use gtk::prelude::*;
use gtk::{gdk, pango};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::rc::{Rc, Weak};

/// [(uri, name, icon)] from the top of the path to `uri`: from your home
/// when it's inside it, else from "/". Places without a path: themselves.
pub fn crumbs(uri: &str, home: Option<&Path>) -> Vec<(String, String, &'static str)> {
    let home = home.map(Path::to_path_buf).unwrap_or_else(glib::home_dir);
    let f = gio::File::for_uri(uri);
    let Some(path) = f.path() else { return vec![(uri.into(), folder::display_name(uri), "folder-symbolic")] };
    let path = normalize(&path);
    let (top, top_name, top_icon) = if path.starts_with(&home) {
        (home.clone(), home.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| home.display().to_string()), "user-home-symbolic")
    } else {
        (PathBuf::from("/"), tr!("Computer"), "drive-harddisk-symbolic")
    };
    let mut out = vec![(gio::File::for_path(&top).uri().to_string(), top_name, top_icon)];
    if let Ok(rel) = path.strip_prefix(&top) {
        let mut cur = top.clone();
        for part in rel.components() {
            cur.push(part);
            out.push((gio::File::for_path(&cur).uri().to_string(), part.as_os_str().to_string_lossy().into_owned(), "folder-symbolic"));
        }
    }
    out
}

fn normalize(p: &Path) -> PathBuf {
    let mut out = PathBuf::new();
    for c in p.components() {
        match c {
            std::path::Component::ParentDir => {
                out.pop();
            }
            std::path::Component::CurDir => {}
            c => out.push(c),
        }
    }
    out
}

/// What Go to Folder opens for `text`: (folder uri, name to select), or
/// None when there's nothing there.
pub fn resolve(text: &str, here: Option<&str>) -> Option<(String, Option<String>)> {
    let text = text.trim();
    if text.is_empty() {
        return None;
    }
    let f = if text.contains("://") || text.starts_with("trash:") || text.starts_with("sonata:") {
        gio::File::for_uri(text)
    } else {
        let p = match text.strip_prefix('~') {
            Some(rest) if rest.is_empty() || rest.starts_with('/') => glib::home_dir().join(rest.trim_start_matches('/')),
            _ => PathBuf::from(text),
        };
        let p = if p.is_absolute() { p } else { here.and_then(|h| gio::File::for_uri(h).path()).unwrap_or_else(glib::home_dir).join(p) };
        gio::File::for_path(normalize(&p))
    };
    let info = f.query_info("standard::type", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok()?;
    if info.file_type() == gio::FileType::Directory {
        return Some((f.uri().to_string(), None));
    }
    let parent = f.parent()?;
    Some((parent.uri().to_string(), f.basename().map(|b| b.to_string_lossy().into_owned())))
}

pub type Drop = Rc<dyn Fn(Vec<gio::File>, gio::File, bool) -> bool>;

pub struct PathBar {
    pub root: gtk::ScrolledWindow,
    stack: gtk::Stack,
    bx: gtk::Box,
    pub uri: RefCell<Option<String>>,
    entry: RefCell<Option<gtk::Entry>>,
    crumb_uris: RefCell<Vec<(gtk::Button, String)>>,
    go: Rc<dyn Fn(&str)>,
    drop: Option<Drop>,
    /// set by the window: starts edit() with the folder's path
    pub on_edit: RefCell<Option<Box<dyn Fn()>>>,
    me: Weak<PathBar>,
}

impl PathBar {
    pub fn new(go: impl Fn(&str) + 'static, drop: Option<Drop>) -> Rc<Self> {
        sonata_ui::css::register_module("files/pathbar.py");
        let root = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::External).vscrollbar_policy(gtk::PolicyType::Never).css_classes(["fs-pathbar"]).build();
        let bx = gtk::Box::builder().valign(gtk::Align::Center).build();
        let stack = gtk::Stack::builder().hhomogeneous(false).vhomogeneous(true).build();
        stack.add_named(&bx, Some("path"));
        root.set_child(Some(&stack));
        let pb = Rc::new_cyclic(|me| PathBar { root, stack, bx, uri: RefCell::default(), entry: RefCell::default(), crumb_uris: RefCell::default(), go: Rc::new(go), drop, on_edit: RefCell::default(), me: me.clone() });
        // a click on the bar's empty part: type the address (Windows' address bar)
        let click = gtk::GestureClick::new();
        let w = pb.me.clone();
        click.connect_released(move |_, _, _, _| {
            if let Some(p) = w.upgrade() {
                if let Some(f) = p.on_edit.borrow().as_ref() {
                    f();
                }
            }
        });
        pb.root.add_controller(click);
        // right-click: Copy Path -- of the folder clicked, or the one shown
        let menu = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
        let w = pb.me.clone();
        menu.connect_pressed(move |g, _, x, y| {
            let Some(p) = w.upgrade() else { return };
            let Some(uri) = p.uri_at(x, y) else { return };
            if p.entry.borrow().is_some() {
                return; // typing an address: the field's own menu
            }
            g.set_state(gtk::EventSequenceState::Claimed);
            let r = p.root.clone();
            sonata_ui::menu::popup(&p.root, vec![vec![sonata_ui::menu::Item::new(&tr!("Copy Path"), move || crate::actions::copy_paths(&r, &[gio::File::for_uri(&uri)]))]], sonata_ui::menu::Options::at(x, y));
        });
        pb.root.add_controller(menu);
        pb
    }

    /// The folder under (x, y): a crumb's, else the one shown.
    pub fn uri_at(&self, x: f64, y: f64) -> Option<String> {
        let mut w = self.root.pick(x, y, gtk::PickFlags::DEFAULT);
        while let Some(cur) = w {
            if let Some((_, u)) = self.crumb_uris.borrow().iter().find(|(b, _)| b.upcast_ref::<gtk::Widget>() == &cur) {
                return Some(u.clone());
            }
            if cur == *self.root.upcast_ref::<gtk::Widget>() {
                break;
            }
            w = cur.parent();
        }
        self.uri.borrow().clone()
    }

    /// The bar becomes a field with `text` (selected): Return calls
    /// on_go(text), Escape or a click elsewhere puts the folders back.
    pub fn edit(&self, text: &str, on_go: impl Fn(&str) + 'static) {
        if let Some(e) = self.entry.borrow().as_ref() {
            e.grab_focus();
            return;
        }
        let entry = sonata_ui::controls::text_field(text, "");
        entry.set_hexpand(true);
        self.entry.replace(Some(entry.clone()));
        self.stack.add_named(&entry, Some("edit"));
        self.stack.set_visible_child(&entry);
        let done = Rc::new(Cell::new(false));
        let on_go = Rc::new(on_go);
        let finish = {
            let (w, e, done) = (self.me.clone(), entry.downgrade(), done.clone());
            Rc::new(move |go: bool| {
                if done.replace(true) {
                    return;
                }
                let (Some(p), Some(entry)) = (w.upgrade(), e.upgrade()) else { return };
                let value = entry.text().trim().to_string();
                p.entry.replace(None);
                p.stack.set_visible_child(&p.bx);
                let (s, en) = (p.stack.downgrade(), entry.downgrade());
                glib::idle_add_local_once(move || {
                    if let (Some(s), Some(en)) = (s.upgrade(), en.upgrade()) {
                        if en.parent().as_ref() == Some(s.upcast_ref()) {
                            s.remove(&en);
                        }
                    }
                });
                p.scroll_end();
                if go && !value.is_empty() {
                    on_go(&value);
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
            glib::idle_add_local_once(move || f(false));
        });
        entry.add_controller(focus);
        glib::idle_add_local_once(move || {
            entry.grab_focus();
            entry.select_region(0, -1);
        });
    }

    pub fn set_uri(&self, uri: &str) {
        if uri.is_empty() || self.uri.borrow().as_deref() == Some(uri) {
            return;
        }
        self.uri.replace(Some(uri.into()));
        while let Some(c) = self.bx.first_child() {
            self.bx.remove(&c);
        }
        self.crumb_uris.borrow_mut().clear();
        let items = crumbs(uri, None);
        let n = items.len();
        for (i, (u, name, icon)) in items.into_iter().enumerate() {
            if i > 0 {
                self.bx.append(&gtk::Label::builder().label("›").css_classes(["fs-path-sep"]).build());
            }
            let b = gtk::Button::builder().focus_on_click(false).can_focus(false).build();
            let row = gtk::Box::new(gtk::Orientation::Horizontal, 4);
            row.append(&gtk::Image::from_icon_name(icon));
            row.append(&gtk::Label::builder().label(&name).ellipsize(pango::EllipsizeMode::Middle).max_width_chars(28).build());
            b.set_child(Some(&row));
            if i == n - 1 {
                b.add_css_class("current");
            }
            let (go, u2) = (self.go.clone(), u.clone());
            b.connect_clicked(move |_| go(&u2));
            if let Some(d) = self.drop.clone() {
                drop_target(&b, &u, d);
            }
            self.bx.append(&b);
            self.crumb_uris.borrow_mut().push((b, u));
        }
        self.scroll_end();
    }

    fn scroll_end(&self) {
        let adj = self.root.hadjustment();
        glib::idle_add_local_once(move || adj.set_value(adj.upper()));
    }
}

/// Files dropped on a folder button go into it (Ctrl: copy).
pub fn drop_target(w: &impl IsA<gtk::Widget>, uri: &str, drop: Drop) {
    let t = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE);
    let w1 = w.as_ref().downgrade();
    t.connect_enter(move |_, _, _| {
        if let Some(w) = w1.upgrade() {
            w.add_css_class("drop");
        }
        gdk::DragAction::MOVE
    });
    let w2 = w.as_ref().downgrade();
    t.connect_leave(move |_| {
        if let Some(w) = w2.upgrade() {
            w.remove_css_class("drop");
        }
    });
    let (w3, uri) = (w.as_ref().downgrade(), uri.to_string());
    t.connect_drop(move |t, v, _, _| {
        if let Some(w) = w3.upgrade() {
            w.remove_css_class("drop");
        }
        let Ok(list) = v.get::<gdk::FileList>() else { return false };
        let copy = t.current_event_state().contains(gdk::ModifierType::CONTROL_MASK);
        drop(list.files(), gio::File::for_uri(&uri), copy)
    });
    w.add_controller(t);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crumbs_from_home_and_outside() {
        let home = Path::new("/home/me");
        let c = crumbs("file:///home/me/Documents/x", Some(home));
        assert_eq!(c.iter().map(|x| x.1.as_str()).collect::<Vec<_>>(), ["me", "Documents", "x"]);
        assert_eq!(c[0].2, "user-home-symbolic");
        sonata_i18n::use_language("en");
        let c = crumbs("file:///etc/ssh", Some(home));
        assert_eq!(c.iter().map(|x| x.1.as_str()).collect::<Vec<_>>(), ["Computer", "etc", "ssh"]);
        assert_eq!(c[2].0, "file:///etc/ssh");
    }

    #[test]
    fn resolve_folders_and_files() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir(d.path().join("sub")).unwrap();
        std::fs::write(d.path().join("sub/a.txt"), "").unwrap();
        let here = gio::File::for_path(d.path()).uri().to_string();
        assert_eq!(resolve("sub", Some(&here)), Some((gio::File::for_path(d.path().join("sub")).uri().to_string(), None)));
        assert_eq!(resolve("sub/a.txt", Some(&here)).unwrap().1.as_deref(), Some("a.txt"));
        assert_eq!(resolve("nothing-here", Some(&here)), None);
        assert_eq!(resolve("  ", None), None);
    }
}
