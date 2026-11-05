//! An item's exported menu (com.canonical.dbusmenu): layout on demand,
//! clicks as Events.

use crate::{strip_mnemonic, MENU_IFACE, TIMEOUT};
use gio::prelude::*;
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap};
use std::rc::{Rc, Weak};
use std::time::Duration;

/// A GetLayout node: (id, properties, children).
#[derive(Clone, Debug, Default)]
pub struct Node {
    pub id: i32,
    pub props: BTreeMap<String, glib::Variant>,
    pub children: Vec<Node>,
}

impl Node {
    /// From a `(ia{sv}av)` variant (or a `v` holding one).
    pub fn from_variant(v: &glib::Variant) -> Option<Node> {
        let v = if v.type_().as_str() == "v" { v.as_variant()? } else { v.clone() };
        if v.type_().as_str() != "(ia{sv}av)" {
            return None;
        }
        let mut props = BTreeMap::new();
        for e in v.child_value(1).iter() {
            if let (Some(k), Some(val)) = (e.child_value(0).str().map(String::from), e.child_value(1).as_variant()) {
                props.insert(k, val);
            }
        }
        let children = v.child_value(2).iter().filter_map(|c| Node::from_variant(&c)).collect();
        Some(Node { id: v.child_value(0).get::<i32>()?, props, children })
    }

    fn str(&self, key: &str) -> Option<String> {
        self.props.get(key).and_then(|v| v.str().map(String::from))
    }

    fn flag(&self, key: &str) -> bool {
        self.props.get(key).and_then(|v| v.get::<bool>()).unwrap_or(true)
    }

    /// Without its ids: what the menu shows.
    fn shape(&self, out: &mut String) {
        out.push('[');
        for c in &self.children {
            out.push('(');
            for (k, v) in &c.props {
                out.push_str(&format!("{k}={};", v.print(true)));
            }
            c.shape(out);
            out.push(')');
        }
        out.push(']');
    }

    /// Every item's id, in the order shape() walks them.
    fn ids(&self, out: &mut Vec<i32>) {
        for c in &self.children {
            out.push(c.id);
            c.ids(out);
        }
    }

    fn empty_submenus(&self, out: &mut Vec<i32>) {
        for c in &self.children {
            if c.str("children-display").as_deref() == Some("submenu") && c.children.is_empty() {
                out.push(c.id);
            } else if !c.children.is_empty() {
                c.empty_submenus(out);
            }
        }
    }
}

fn shape(n: &Node) -> String {
    let mut s = String::new();
    n.shape(&mut s);
    s
}

fn ids(n: &Node) -> Vec<i32> {
    let mut v = Vec::new();
    n.ids(&mut v);
    v
}

/// A menu row as the layout describes it (sonata_ui::menu::Item-to-be).
#[derive(Clone, Debug, PartialEq)]
pub enum Entry {
    Item { id: i32, label: String, enabled: bool, checked: Option<bool> },
    Submenu { id: i32, label: String, enabled: bool, sections: Vec<Vec<Entry>> },
}

impl Entry {
    pub fn label(&self) -> &str {
        match self {
            Entry::Item { label, .. } | Entry::Submenu { label, .. } => label,
        }
    }

    pub fn enabled(&self) -> bool {
        match self {
            Entry::Item { enabled, .. } | Entry::Submenu { enabled, .. } => *enabled,
        }
    }

    /// A disabled row with this text ("No Items").
    pub fn disabled(label: &str) -> Entry {
        Entry::Item { id: 0, label: label.to_string(), enabled: false, checked: None }
    }
}

/// A layout -> menu sections; separators start a new section,
/// children-display=submenu nodes become submenus. `labels` gets each
/// item's label by id.
pub fn layout_sections(layout: &Node, labels: &mut HashMap<i32, String>) -> Vec<Vec<Entry>> {
    let (mut sections, mut cur) = (Vec::new(), Vec::new());
    for child in &layout.children {
        if !child.flag("visible") {
            continue;
        }
        if child.str("type").as_deref() == Some("separator") {
            if !cur.is_empty() {
                sections.push(std::mem::take(&mut cur));
            }
            continue;
        }
        let label = strip_mnemonic(&child.str("label").unwrap_or_default());
        labels.insert(child.id, label.clone());
        let enabled = child.flag("enabled");
        if child.str("children-display").as_deref() == Some("submenu") || !child.children.is_empty() {
            let mut sub = layout_sections(child, labels);
            if sub.is_empty() {
                sub = vec![vec![Entry::disabled("")]];
            }
            cur.push(Entry::Submenu { id: child.id, label, enabled, sections: sub });
            continue;
        }
        let checked = match child.str("toggle-type").as_deref() {
            Some("checkmark") | Some("radio") => Some(child.props.get("toggle-state").and_then(|v| v.get::<i32>()).unwrap_or(0) == 1),
            _ => None,
        };
        cur.push(Entry::Item { id: child.id, label, enabled, checked });
    }
    if !cur.is_empty() {
        sections.push(cur);
    }
    sections
}

/// Steam's tray menu ignores the clicks it gets (Vini: Library, Store...
/// opened nothing -- whatever the Event looked like): its items are opened
/// with Steam's own steam:// links instead. Labels in English and Portuguese.
pub const STEAM_LINKS: &[(&[&str], &str)] = &[
    (&["library", "biblioteca"], "steam://open/games"),
    (&["store", "loja"], "steam://store"),
    (&["community", "comunidade"], "steam://url/CommunityHome"),
    (&["friends", "amigos"], "steam://open/friends"),
    (&["settings", "configura", "preferenc"], "steam://open/settings"),
    (&["big picture"], "steam://open/bigpicture"),
    (&["servers", "servidores"], "steam://open/servers"),
    (&["news", "notícias", "noticias"], "steam://open/news"),
    (&["screenshots", "capturas"], "steam://open/screenshots"),
    (&["downloads"], "steam://open/downloads"),
    (&["exit", "sair"], "-shutdown"),
];

/// The steam:// link (or -shutdown) for a Steam tray menu item, None if unknown.
pub fn steam_link(label: &str) -> Option<&'static str> {
    let low = label.to_lowercase();
    STEAM_LINKS.iter().find(|(words, _)| words.iter().any(|w| low.contains(w))).map(|(_, l)| *l)
}

/// A reply: the call's result, None on an error.
pub(crate) type Reply = Box<dyn FnOnce(Option<glib::Variant>)>;
/// How calls reach the app: (method, args, reply type, reply).
pub(crate) type Transport = Box<dyn Fn(&str, glib::Variant, Option<&'static str>, Option<Reply>)>;

/// The menu while it shows: whether it still does, and how to show other
/// rows in it.
pub(crate) struct Open {
    pub visible: Box<dyn Fn() -> bool>,
    pub redraw: Box<dyn Fn(Vec<Vec<Entry>>)>,
}

pub struct DBusMenu {
    path: String,
    call: Transport,
    #[allow(clippy::type_complexity)]
    pub(crate) run_steam: RefCell<Box<dyn Fn(&str) -> bool>>,
    pub(crate) open: RefCell<Option<Rc<Open>>>,
    steam: Cell<bool>,
    labels: RefCell<HashMap<i32, String>>,
    shown: RefCell<Option<Node>>,         // the layout the open menu was built from
    ids: RefCell<HashMap<i32, i32>>,      // its ids -> the app's current ones (renumbered)
    pending: RefCell<Option<glib::SourceId>>,
    busy: Cell<bool>,
    again: Cell<bool>,
    sub: RefCell<Option<(gio::DBusConnection, gio::SignalSubscriptionId)>>,
    me: Weak<DBusMenu>,
}

impl DBusMenu {
    pub(crate) fn new(conn: &gio::DBusConnection, name: &str, path: &str) -> Rc<DBusMenu> {
        let (c, n, p) = (conn.clone(), name.to_string(), path.to_string());
        let call: Transport = Box::new(move |method, args, rtype, reply| {
            let ty = rtype.and_then(|t| glib::VariantTy::new(t).ok());
            c.call(Some(&n), &p, MENU_IFACE, method, Some(&args), ty, gio::DBusCallFlags::NONE, TIMEOUT, None::<&gio::Cancellable>, move |r| {
                if let Some(cb) = reply {
                    cb(r.ok());
                }
            });
        });
        let m = Self::with_transport(path, call);
        let w = Rc::downgrade(&m);
        let sub = conn.signal_subscribe(Some(name), Some(MENU_IFACE), None, Some(path), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, signal, _| {
            if let Some(m) = w.upgrade() {
                m.on_signal(signal);
            }
        });
        m.sub.replace(Some((conn.clone(), sub)));
        m
    }

    /// A menu whose calls go through `call` (tests: no bus).
    pub(crate) fn with_transport(path: &str, call: Transport) -> Rc<DBusMenu> {
        Rc::new_cyclic(|me| DBusMenu {
            path: path.to_string(),
            call,
            run_steam: RefCell::new(Box::new(crate::appmatch::run_steam)),
            open: RefCell::new(None),
            steam: Cell::new(false),
            labels: RefCell::default(),
            shown: RefCell::new(None),
            ids: RefCell::default(),
            pending: RefCell::new(None),
            busy: Cell::new(false),
            again: Cell::new(false),
            sub: RefCell::new(None),
            me: me.clone(),
        })
    }

    pub fn path(&self) -> &str {
        &self.path
    }

    /// Steam's menu (STEAM_LINKS).
    pub(crate) fn set_steam(&self, on: bool) {
        self.steam.set(on);
    }

    pub(crate) fn close(&self) {
        if let Some((c, s)) = self.sub.take() {
            c.signal_unsubscribe(s);
        }
        if let Some(id) = self.pending.take() {
            sonata_ui::cancel(id);
        }
    }

    fn get_layout(&self, then: impl FnOnce(Option<Node>) + 'static) {
        let args = (0i32, -1i32, Vec::<String>::new()).to_variant();
        (self.call)("GetLayout", args, Some("(u(ia{sv}av))"), Some(Box::new(move |r: Option<glib::Variant>| then(r.and_then(|r| Node::from_variant(&r.child_value(1)))))));
    }

    /// AboutToShow(0), GetLayout(0, -1, []); submenus that come empty
    /// (filled lazily by Qt apps) get their own AboutToShow, then one more
    /// GetLayout. cb(the layout, None on an error).
    pub fn fetch(&self, cb: impl FnOnce(Option<Node>) + 'static) {
        let w = self.me.clone();
        (self.call)("AboutToShow", (0i32,).to_variant(), None, Some(Box::new(move |_| {
            let Some(m) = w.upgrade() else { return };
            let w = m.me.clone();
            m.get_layout(move |lay| {
                let mut empty = Vec::new();
                if let Some(l) = &lay {
                    l.empty_submenus(&mut empty);
                }
                let Some(m) = w.upgrade().filter(|_| !empty.is_empty()) else { return cb(lay) };
                empty.truncate(32);
                let left = Rc::new(Cell::new(empty.len()));
                let cb = Rc::new(RefCell::new(Some(cb)));
                for i in empty {
                    let (left, cb, w) = (left.clone(), cb.clone(), m.me.clone());
                    (m.call)("AboutToShow", (i,).to_variant(), None, Some(Box::new(move |_| {
                        left.set(left.get() - 1);
                        if left.get() == 0 {
                            if let (Some(m), Some(cb)) = (w.upgrade(), cb.take()) {
                                m.get_layout(cb);
                            }
                        }
                    })));
                }
            });
        })));
    }

    /// Tell the app about item `item_id` ("clicked", "opened", "closed").
    pub fn event(&self, item_id: i32, kind: &str) {
        if kind == "clicked" {
            self.open.replace(None); // the menu is closing: no more redraws in it
        }
        if self.steam.get() && kind == "clicked" {
            let label = self.labels.borrow().get(&item_id).cloned().unwrap_or_default();
            if let Some(link) = steam_link(&label) {
                if (self.run_steam.borrow())(link) {
                    return;
                }
            }
        }
        let id = self.ids.borrow().get(&item_id).copied().unwrap_or(item_id);
        let ts = (glib::real_time() / 1000) as u64 as u32;
        let args = glib::Variant::tuple_from_iter([id.to_variant(), kind.to_variant(), glib::Variant::from_variant(&0i32.to_variant()), ts.to_variant()]);
        (self.call)("Event", args, None, None);
    }

    /// The layout's sections; the menu now shows this layout.
    pub fn sections(&self, layout: &Node) -> Vec<Vec<Entry>> {
        let mut labels = HashMap::new();
        let out = layout_sections(layout, &mut labels);
        self.labels.replace(labels);
        self.shown.replace(Some(layout.clone()));
        self.ids.replace(HashMap::new());
        out
    }

    // Vini: Steam's menu did nothing and the menu bar froze after it. Steam
    // (Chromium's dbusmenu) rebuilds its menu -- new ids, LayoutUpdated -- on
    // every AboutToShow / "opened": the redraw sent AboutToShow again, an
    // endless loop that swapped the open menu's rows under the pointer (the
    // click was lost, the menu's grab left behind). Redraws now only re-read
    // the layout (no AboutToShow), one at a time, and an unchanged menu keeps
    // its rows: only its ids are mapped to the new ones.
    pub(crate) fn on_signal(&self, signal: &str) {
        let open = self.open.borrow().is_some();
        if open && matches!(signal, "LayoutUpdated" | "ItemsPropertiesUpdated") && self.pending.borrow().is_none() {
            self.schedule();
        }
    }

    fn schedule(&self) {
        let w = self.me.clone();
        let id = glib::timeout_add_local_once(Duration::from_millis(80), move || {
            if let Some(m) = w.upgrade() {
                m.pending.take();
                m.refetch();
            }
        });
        self.pending.replace(Some(id));
    }

    /// Run a waiting redraw now (tests).
    #[cfg(test)]
    pub(crate) fn flush(&self) {
        if let Some(id) = self.pending.take() {
            sonata_ui::cancel(id);
            self.refetch();
        }
    }

    #[cfg(test)]
    pub(crate) fn has_pending(&self) -> bool {
        self.pending.borrow().is_some()
    }

    #[cfg(test)]
    pub(crate) fn label(&self, id: i32) -> Option<String> {
        self.labels.borrow().get(&id).cloned()
    }

    fn refetch(&self) {
        if self.busy.get() {
            self.again.set(true);
            return;
        }
        self.busy.set(true);
        let w = self.me.clone();
        self.get_layout(move |lay| {
            let Some(m) = w.upgrade() else { return };
            m.busy.set(false);
            m.update_open(lay);
            if m.again.replace(false) && m.open.borrow().is_some() && m.pending.borrow().is_none() {
                m.schedule();
            }
        });
    }

    fn update_open(&self, lay: Option<Node>) {
        let (Some(open), Some(lay)) = (self.open.borrow().clone(), lay) else { return };
        if !(open.visible)() {
            return;
        }
        let same = self.shown.borrow().as_ref().is_some_and(|s| shape(s) == shape(&lay));
        if same {
            let map = ids(self.shown.borrow().as_ref().unwrap()).into_iter().zip(ids(&lay)).collect();
            self.ids.replace(map);
            return;
        }
        let sections = self.sections(&lay);
        (open.redraw)(sections);
    }
}
