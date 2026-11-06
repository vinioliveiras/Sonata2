//! Preview's thumbnail sidebar (sonata2/preview/sidebar.py; macOS Preview:
//! View > Thumbnails, ⌥⌘2). A lazy ListView of the pictures; only the rows
//! on screen ask for a thumbnail. Thumbnails are made off the main loop by
//! one worker, newest request first (the rows just scrolled to), and kept
//! in a small in-memory LRU shared by every window.

use crate::imageload;
use gtk::{gdk, glib, pango, prelude::*};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

/// px: the longest side made (sharp on 2x screens at the row's size)
const THUMB: i32 = 160;
/// thumbnails kept (~12 MB at most)
const CACHE_MAX: usize = 160;
/// the thumbnail's box in a row
const ROW_W: i32 = 104;
const ROW_H: i32 = 78;
pub const SIDEBAR_W: i32 = 148;

type Key = (String, i128);
type Back = Rc<dyn Fn(&str, Option<gdk::Texture>)>;

fn key(path: &str) -> Key {
    let mtime = std::fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|d| d.as_nanos() as i128)
        .unwrap_or(0);
    (path.to_string(), mtime)
}

/// One worker thread making thumbnails; the cache and the callbacks live
/// on the main thread.
#[derive(Default)]
struct Loader {
    cache: Vec<(Key, Option<gdk::Texture>)>,
    waiting: HashMap<Key, Vec<(u64, Back)>>,
    pending: Arc<Mutex<Vec<Key>>>,
    busy: Arc<AtomicBool>,
    next_id: u64,
}

thread_local! {
    static LOADER: RefCell<Loader> = RefCell::default();
}

/// A cached thumbnail, or None: then back(path, texture) comes later.
/// The id lets a row that left the screen forget its request.
fn get(path: &str, back: Back) -> (Option<gdk::Texture>, u64) {
    let k = key(path);
    LOADER.with(|l| {
        let mut l = l.borrow_mut();
        if let Some(i) = l.cache.iter().position(|c| c.0 == k) {
            let entry = l.cache.remove(i);
            let tex = entry.1.clone();
            l.cache.push(entry);
            return (tex, 0);
        }
        l.next_id += 1;
        let id = l.next_id;
        let pending = l.pending.clone();
        let mut p = pending.lock().unwrap_or_else(|e| e.into_inner());
        if let Some(ws) = l.waiting.get_mut(&k) {
            ws.push((id, back));
            // asked again: to the front of the queue
            p.retain(|x| *x != k);
            p.push(k);
            return (None, id);
        }
        l.waiting.insert(k.clone(), vec![(id, back)]);
        p.push(k);
        drop(p);
        let busy = l.busy.clone();
        drop(l);
        kick(pending, busy);
        (None, id)
    })
}

/// A row left the screen: its thumbnail isn't needed any more.
fn forget(path: &str, id: u64) {
    let k = key(path);
    LOADER.with(|l| {
        let mut l = l.borrow_mut();
        let empty = match l.waiting.get_mut(&k) {
            Some(ws) => {
                ws.retain(|w| w.0 != id);
                ws.is_empty()
            }
            None => false,
        };
        if empty {
            l.waiting.remove(&k);
            l.pending.lock().unwrap_or_else(|e| e.into_inner()).retain(|x| *x != k);
        }
    });
}

fn kick(pending: Arc<Mutex<Vec<Key>>>, busy: Arc<AtomicBool>) {
    if busy.swap(true, Ordering::SeqCst) {
        return;
    }
    std::thread::spawn(move || loop {
        let k = {
            let mut p = pending.lock().unwrap_or_else(|e| e.into_inner());
            match p.pop() {
                Some(k) => k,
                None => {
                    busy.store(false, Ordering::SeqCst);
                    // asked for while the worker was stopping
                    let again = !p.is_empty();
                    drop(p);
                    if again && !busy.swap(true, Ordering::SeqCst) {
                        continue;
                    }
                    return;
                }
            }
        };
        let px = imageload::pixbuf_at_scale(&k.0, THUMB).map(|pb| imageload::pixbuf_rgba(&pb));
        glib::MainContext::default().invoke(move || deliver(k, px));
    });
}

fn deliver(k: Key, px: Option<(Vec<u8>, u32, u32, bool)>) {
    let tex = px.map(|(d, w, h, _)| imageload::texture_from_rgba(&d, w, h));
    let backs = LOADER.with(|l| {
        let mut l = l.borrow_mut();
        l.cache.push((k.clone(), tex.clone()));
        while l.cache.len() > CACHE_MAX {
            l.cache.remove(0);
        }
        l.waiting.remove(&k).unwrap_or_default()
    });
    for (_, back) in backs {
        back(&k.0, tex.clone());
    }
}

/// One row's widgets and what it shows.
struct Row {
    pic: gtk::Picture,
    name: gtk::Label,
    path: RefCell<Option<String>>,
    req: Cell<u64>,
}

impl Row {
    fn show(&self, tex: Option<&gdk::Texture>) {
        self.pic.set_paintable(tex);
        if let Some(t) = tex {
            // the picture's own shape inside the box, so its shadow hugs it
            let s = (ROW_W as f64 / t.width().max(1) as f64).min(ROW_H as f64 / t.height().max(1) as f64).min(1.0);
            self.pic.set_size_request(((t.width() as f64 * s).round() as i32).max(8), ((t.height() as f64 * s).round() as i32).max(8));
        }
    }
}

/// The sidebar. on_pick(path) when a thumbnail is clicked (or chosen with ↑ ↓).
pub struct Thumbnails {
    pub root: gtk::Box,
    pub paths: RefCell<Vec<String>>,
    model: gtk::StringList,
    selection: gtk::SingleSelection,
    view: gtk::ListView,
    quiet: Cell<bool>,
    rows: RefCell<HashMap<gtk::Box, Rc<Row>>>,
}

impl Thumbnails {
    pub fn new<F: Fn(&str) + 'static>(on_pick: F) -> Rc<Self> {
        sonata_ui::css::register_module("preview/sidebar.py");
        let root = gtk::Box::new(gtk::Orientation::Vertical, 0);
        root.add_css_class("pv-sidebar");
        root.add_css_class("sonata-sidebar");
        root.set_size_request(SIDEBAR_W, -1);
        root.set_hexpand(false);
        let model = gtk::StringList::new(&[]);
        let selection = gtk::SingleSelection::builder().model(&model).autoselect(false).can_unselect(false).build();
        let factory = gtk::SignalListItemFactory::new();
        let view = gtk::ListView::builder().model(&selection).factory(&factory).margin_top(6).margin_bottom(6).build();
        view.add_css_class("pv-thumbs");
        root.append(&gtk::ScrolledWindow::builder().child(&view).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build());
        let t = Rc::new(Thumbnails {
            root,
            paths: RefCell::default(),
            model,
            selection,
            view,
            quiet: Cell::new(false),
            rows: RefCell::default(),
        });
        let w = Rc::downgrade(&t);
        factory.connect_setup(move |_, item| {
            let (Some(t), Some(item)) = (w.upgrade(), item.downcast_ref::<gtk::ListItem>()) else { return };
            // rows: the thumbnail over the name
            let b = gtk::Box::new(gtk::Orientation::Vertical, 4);
            let frame = gtk::CenterBox::builder().halign(gtk::Align::Center).build(); // centres the picture in a fixed box
            frame.set_size_request(ROW_W, ROW_H);
            let pic = gtk::Picture::builder().content_fit(gtk::ContentFit::Contain).can_shrink(true).valign(gtk::Align::Center).build();
            pic.add_css_class("pv-thumb");
            frame.set_center_widget(Some(&pic));
            let name = gtk::Label::builder().ellipsize(pango::EllipsizeMode::Middle).max_width_chars(14).justify(gtk::Justification::Center).build();
            name.add_css_class("pv-thumb-name");
            b.append(&frame);
            b.append(&name);
            item.set_child(Some(&b));
            t.rows.borrow_mut().insert(b, Rc::new(Row { pic, name, path: RefCell::new(None), req: Cell::new(0) }));
        });
        let w = Rc::downgrade(&t);
        factory.connect_bind(move |_, item| {
            let (Some(t), Some(item)) = (w.upgrade(), item.downcast_ref::<gtk::ListItem>()) else { return };
            let (Some(b), Some(s)) = (item.child().and_downcast::<gtk::Box>(), item.item().and_downcast::<gtk::StringObject>()) else { return };
            let Some(row) = t.rows.borrow().get(&b).cloned() else { return };
            let path = s.string().to_string();
            *row.path.borrow_mut() = Some(path.clone());
            let base = std::path::Path::new(&path).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            row.name.set_label(&base);
            b.set_tooltip_text(Some(&base));
            row.pic.set_paintable(None::<&gdk::Paintable>);
            row.pic.set_size_request(-1, -1);
            let r = Rc::downgrade(&row);
            let back: Back = Rc::new(move |p: &str, tex: Option<gdk::Texture>| {
                if let Some(row) = r.upgrade() {
                    if row.path.borrow().as_deref() == Some(p) {
                        row.show(tex.as_ref());
                    }
                }
            });
            let (tex, id) = get(&path, back);
            row.req.set(id);
            if let Some(tex) = tex {
                row.show(Some(&tex));
            }
        });
        let w = Rc::downgrade(&t);
        factory.connect_unbind(move |_, item| {
            let (Some(t), Some(item)) = (w.upgrade(), item.downcast_ref::<gtk::ListItem>()) else { return };
            let Some(b) = item.child().and_downcast::<gtk::Box>() else { return };
            let row = t.rows.borrow().get(&b).cloned();
            if let Some(row) = row {
                if let Some(p) = row.path.borrow_mut().take() {
                    forget(&p, row.req.get());
                }
            }
        });
        let w = Rc::downgrade(&t);
        let on_pick = Rc::new(on_pick);
        t.selection.connect_selected_notify(move |sel| {
            let Some(t) = w.upgrade() else { return };
            if t.quiet.get() {
                return;
            }
            let i = sel.selected() as usize;
            let p = t.paths.borrow().get(i).cloned();
            if let Some(p) = p {
                on_pick(&p);
            }
        });
        t
    }

    pub fn set_paths(&self, paths: &[String], current: Option<&str>) {
        if *self.paths.borrow() != paths {
            *self.paths.borrow_mut() = paths.to_vec();
            self.quiet.set(true);
            let strs: Vec<&str> = paths.iter().map(String::as_str).collect();
            self.model.splice(0, self.model.n_items(), &strs);
            self.quiet.set(false);
        }
        if let Some(c) = current {
            self.select(c);
        }
    }

    /// The file changed: its row binds again (a new thumbnail).
    pub fn refresh(&self, path: &str) {
        let i = self.paths.borrow().iter().position(|p| p == path);
        if let Some(i) = i {
            self.quiet.set(true);
            self.model.splice(i as u32, 1, &[path]);
            self.quiet.set(false);
            self.select(path);
        }
    }

    /// Selection follows the picture shown (← →); no on_pick.
    pub fn select(&self, path: &str) {
        let Some(i) = self.paths.borrow().iter().position(|p| p == path) else { return };
        if self.selection.selected() != i as u32 {
            self.quiet.set(true);
            self.selection.set_selected(i as u32);
            self.quiet.set(false);
        }
        if self.root.is_mapped() {
            self.view.scroll_to(i as u32, gtk::ListScrollFlags::NONE, None);
        }
    }
}
