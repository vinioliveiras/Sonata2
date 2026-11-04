//! One registered StatusNotifierItem: its properties, icon and actions.

use crate::dbusmenu::DBusMenu;
use crate::host::Host;
use crate::icon::pick_pixmap;
use crate::{split_service, ICON_PX, ITEM_IFACE, PROPS, TIMEOUT};
use gio::prelude::*;
use std::cell::{Cell, RefCell};
use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::rc::{Rc, Weak};
use std::time::Duration;

/// The item's properties as the app sent them (each the variant's inner value).
pub(crate) type Props = HashMap<String, glib::Variant>;

// the properties each StatusNotifierItem signal announces: only those are
// read again (a tooltip or title update re-read everything, pixmaps
// included, and dropped the icon textures -- apps tick their tooltip often)
const SIGNAL_PROPS: &[(&str, &[&str])] = &[
    ("NewTitle", &["Title"]),
    ("NewToolTip", &["ToolTip"]),
    ("NewStatus", &["Status"]),
    ("NewIcon", &["IconName", "IconPixmap"]),
    ("NewAttentionIcon", &["AttentionIconName", "AttentionIconPixmap"]),
    ("NewOverlayIcon", &["OverlayIconName", "OverlayIconPixmap"]),
    ("NewIconThemePath", &["IconThemePath"]),
    ("NewMenu", &["Menu"]),
];
const ICON_PROPS: &[&str] = &["IconName", "IconPixmap", "AttentionIconName", "AttentionIconPixmap", "IconThemePath", "Status"];

pub(crate) fn signal_props(signal: &str) -> Option<&'static [&'static str]> {
    SIGNAL_PROPS.iter().find(|(s, _)| *s == signal).map(|(_, p)| *p)
}

fn same(a: Option<&glib::Variant>, b: Option<&glib::Variant>) -> bool {
    match (a, b) {
        (Some(a), Some(b)) => a.type_() == b.type_() && a == b,
        (None, None) => true,
        _ => false,
    }
}

/// The properties to read again since the last read: some by name, or all
/// of them (an unknown signal, or one before the first read).
#[derive(Default, Debug)]
pub(crate) struct Pending {
    want: BTreeSet<String>,
    full: bool,
}

impl Pending {
    pub(crate) fn note(&mut self, signal: &str, ready: bool) {
        match signal_props(signal) {
            Some(props) if ready => self.want.extend(props.iter().map(|p| p.to_string())),
            _ => self.full = true,
        }
    }

    /// The names to read, None for everything (GetAll).
    pub(crate) fn take(&mut self) -> Option<Vec<String>> {
        let want = std::mem::take(&mut self.want);
        let full = std::mem::take(&mut self.full);
        (!full && !want.is_empty()).then(|| want.into_iter().collect())
    }
}

/// Apply properties read one by one (None: not set by this app) ->
/// (anything changed, the icon changed).
pub(crate) fn merge(props: &mut Props, got: Vec<(String, Option<glib::Variant>)>) -> (bool, bool) {
    let (mut changed, mut icon) = (false, false);
    for (k, v) in got {
        if same(props.get(&k), v.as_ref()) {
            continue;
        }
        changed = true;
        icon = icon || ICON_PROPS.contains(&k.as_str());
        match v {
            Some(v) => props.insert(k, v),
            None => props.remove(&k),
        };
    }
    (changed, icon)
}

fn icon_changed(old: &Props, new: &Props) -> bool {
    ICON_PROPS.iter().any(|k| !same(old.get(*k), new.get(*k)))
}

/// An IconPixmap entry: ARGB32 in network byte order (A, R, G, B in memory).
#[derive(Clone, Debug)]
pub struct Pixmap {
    pub width: i32,
    pub height: i32,
    pub argb: glib::Bytes,
}

/// What an item's icon is drawn from.
#[derive(Clone, Debug)]
pub enum IconSource {
    /// A name in the icon theme.
    Name(String),
    /// A picture file (an absolute IconName, or one in IconThemePath).
    File(PathBuf),
    /// The pixmap closest to the size drawn.
    Pixmap(Pixmap),
}

/// The pixmaps of an `a(iiay)` IconPixmap.
pub(crate) fn pixmaps(v: &glib::Variant) -> Vec<Pixmap> {
    if v.type_().as_str() != "a(iiay)" {
        return Vec::new();
    }
    v.iter()
        .map(|c| Pixmap {
            width: c.child_value(0).get::<i32>().unwrap_or(0),
            height: c.child_value(1).get::<i32>().unwrap_or(0),
            argb: c.child_value(2).data_as_bytes(),
        })
        .collect()
}

/// A named icon: an absolute path, a file in the app's own theme folder,
/// else a theme name `has_icon(name, theme_path)` knows (it may add the
/// folder to the theme's search path first).
pub(crate) fn named_icon(name: &str, theme_path: &str, has_icon: &dyn Fn(&str, &str) -> bool) -> Option<IconSource> {
    if name.starts_with('/') {
        return Path::new(name).is_file().then(|| IconSource::File(name.into()));
    }
    if !theme_path.is_empty() {
        for sub in ["", "hicolor/16x16/apps", "hicolor/22x22/apps", "hicolor/24x24/apps", "hicolor/32x32/apps", "hicolor/48x48/apps", "hicolor/scalable/apps"] {
            for ext in [".png", ".svg"] {
                let f = Path::new(theme_path).join(sub).join(format!("{name}{ext}"));
                if f.is_file() {
                    return Some(IconSource::File(f));
                }
            }
        }
    }
    has_icon(name, theme_path).then(|| IconSource::Name(name.to_string()))
}

fn str_of(props: &Props, key: &str) -> String {
    props.get(key).and_then(|v| v.str().map(String::from)).unwrap_or_default()
}

/// The icon to draw at `scale` from these properties.
pub(crate) fn icon_of(props: &Props, scale: i32, has_icon: &dyn Fn(&str, &str) -> bool) -> Option<IconSource> {
    let attention = str_of(props, "Status") == "NeedsAttention";
    let order: &[&str] = if attention { &["Attention", ""] } else { &[""] };
    for pre in order {
        let name = str_of(props, &format!("{pre}IconName"));
        let theme_path = str_of(props, "IconThemePath");
        if !name.is_empty() {
            if let Some(out) = named_icon(&name, &theme_path, has_icon) {
                return Some(out);
            }
        }
        if let Some(v) = props.get(&format!("{pre}IconPixmap")) {
            let all = pixmaps(v);
            let sizes: Vec<(i32, i32, usize)> = all.iter().map(|p| (p.width, p.height, p.argb.len())).collect();
            if let Some(i) = pick_pixmap(&sizes, ICON_PX * scale) {
                return Some(IconSource::Pixmap(all[i].clone()));
            }
        }
    }
    None
}

/// ToolTip descriptions may carry (HTML-ish) markup: plain text.
pub(crate) fn plain(markup: &str) -> String {
    if let Ok((_, text, _)) = gtk::pango::parse_markup(markup, '\0') {
        return text.to_string();
    }
    let mut out = String::new();
    let mut inside = false;
    for c in markup.chars() {
        match c {
            '<' => inside = true,
            '>' if inside => inside = false,
            _ if !inside => out.push(c),
            _ => {}
        }
    }
    out
}

/// The tooltip: ToolTip's title (else Title), then its description.
pub(crate) fn tooltip_of(props: &Props) -> String {
    let tip = props.get("ToolTip").filter(|t| t.type_().as_str() == "(sa(iiay)ss)");
    let part = |i: usize| tip.and_then(|t| t.child_value(i).str().map(String::from)).unwrap_or_default();
    let mut title = part(2);
    if title.is_empty() {
        title = str_of(props, "Title");
    }
    let mut body = part(3);
    if body.contains('<') {
        body = plain(&body);
    }
    [title, body].into_iter().filter(|t| !t.is_empty()).collect::<Vec<_>>().join("\n")
}

pub struct TrayItem {
    host: Weak<Host>,
    conn: gio::DBusConnection,
    key: String,
    name: String,
    path: String,
    props: RefCell<Props>,
    ready: Cell<bool>,
    menu: RefCell<Option<Rc<DBusMenu>>>,
    icon_serial: Cell<u64>,
    refresh_id: RefCell<Option<glib::SourceId>>,
    pending: RefCell<Pending>,
    sub: RefCell<Option<gio::SignalSubscriptionId>>,
    me: Weak<TrayItem>,
}

impl TrayItem {
    pub(crate) fn new(host: Weak<Host>, conn: &gio::DBusConnection, key: &str) -> Rc<TrayItem> {
        let (name, path) = split_service(key, "");
        let item = Rc::new_cyclic(|me: &Weak<TrayItem>| TrayItem {
            host,
            conn: conn.clone(),
            key: key.to_string(),
            name,
            path,
            props: RefCell::default(),
            ready: Cell::new(false),
            menu: RefCell::new(None),
            icon_serial: Cell::new(0),
            refresh_id: RefCell::new(None),
            pending: RefCell::default(),
            sub: RefCell::new(None),
            me: me.clone(),
        });
        let w = Rc::downgrade(&item);
        let sub = conn.signal_subscribe(Some(&item.name), Some(ITEM_IFACE), None, Some(&item.path), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, signal, _| {
            if let Some(it) = w.upgrade() {
                it.on_signal(signal);
            }
        });
        item.sub.replace(Some(sub));
        item.refresh();
        item
    }

    pub(crate) fn close(&self) {
        if let Some(s) = self.sub.take() {
            self.conn.signal_unsubscribe(s);
        }
        if let Some(id) = self.refresh_id.take() {
            id.remove();
        }
        if let Some(m) = self.menu.take() {
            m.close();
        }
    }

    fn closed(&self) -> bool {
        self.sub.borrow().is_none()
    }

    /// "busname/path", as the watcher lists it.
    pub fn key(&self) -> &str {
        &self.key
    }

    /// The app's bus name.
    pub fn bus_name(&self) -> &str {
        &self.name
    }

    /// Its properties have been read once (until then it isn't shown).
    pub fn ready(&self) -> bool {
        self.ready.get()
    }

    /// A property's value (the variant's content), None when not set.
    pub fn prop(&self, name: &str) -> Option<glib::Variant> {
        self.props.borrow().get(name).cloned()
    }

    pub fn id(&self) -> String {
        let id = str_of(&self.props.borrow(), "Id");
        if id.is_empty() {
            self.name.clone()
        } else {
            id
        }
    }

    pub fn status(&self) -> String {
        let s = str_of(&self.props.borrow(), "Status");
        if s.is_empty() {
            "Active".into()
        } else {
            s
        }
    }

    /// Shown in the menu bar: read, and not Passive.
    pub fn visible(&self) -> bool {
        self.ready.get() && self.status() != "Passive"
    }

    pub fn item_is_menu(&self) -> bool {
        self.props.borrow().get("ItemIsMenu").and_then(|v| v.get::<bool>()).unwrap_or(false)
    }

    pub fn tooltip(&self) -> String {
        tooltip_of(&self.props.borrow())
    }

    /// Bumped whenever the icon may look different: an unchanged serial
    /// means the drawn icon can stay (no new silhouette).
    pub fn icon_serial(&self) -> u64 {
        self.icon_serial.get()
    }

    /// The icon to draw at `scale`; `has_icon(name, theme_path)` says
    /// whether the icon theme has a name. None: no icon of its own.
    pub fn icon(&self, scale: i32, has_icon: &dyn Fn(&str, &str) -> bool) -> Option<IconSource> {
        icon_of(&self.props.borrow(), scale, has_icon)
    }

    /// Its exported menu (com.canonical.dbusmenu), if any.
    pub fn menu(&self) -> Option<Rc<DBusMenu>> {
        self.menu.borrow().clone()
    }

    // -- properties --------------------------------------------------------
    fn on_signal(&self, signal: &str) {
        // NewIcon/NewStatus/NewTitle/NewToolTip... come in bursts: one read
        self.pending.borrow_mut().note(signal, self.ready.get());
        if self.refresh_id.borrow().is_none() {
            let w = self.me.clone();
            let id = glib::timeout_add_local_once(Duration::from_millis(60), move || {
                if let Some(it) = w.upgrade() {
                    it.refresh_id.take();
                    it.refresh_now();
                }
            });
            self.refresh_id.replace(Some(id));
        }
    }

    fn refresh_now(&self) {
        let want = self.pending.borrow_mut().take();
        match want {
            None => self.refresh(),
            Some(names) => self.fetch(names),
        }
    }

    /// Read just these properties, then apply them together.
    fn fetch(&self, names: Vec<String>) {
        let got: Rc<RefCell<Vec<(String, Option<glib::Variant>)>>> = Rc::default();
        let left = Rc::new(Cell::new(names.len()));
        for name in names {
            let (w, got, left) = (self.me.clone(), got.clone(), left.clone());
            let args = (ITEM_IFACE, name.as_str()).to_variant();
            self.conn.call(Some(&self.name), &self.path, PROPS, "Get", Some(&args), Some(glib::VariantTy::new("(v)").unwrap()), gio::DBusCallFlags::NONE, TIMEOUT, None::<&gio::Cancellable>, move |r| {
                // an error: not set by this app
                let v = r.ok().and_then(|r| r.child_value(0).as_variant());
                got.borrow_mut().push((name, v));
                left.set(left.get() - 1);
                if left.get() == 0 {
                    if let Some(it) = w.upgrade() {
                        it.apply(got.take());
                    }
                }
            });
        }
    }

    fn apply(&self, got: Vec<(String, Option<glib::Variant>)>) {
        if self.closed() {
            return; // removed meanwhile
        }
        let (changed, icon) = merge(&mut self.props.borrow_mut(), got);
        if !changed {
            return; // the same tooltip again: nothing to redraw
        }
        if icon {
            self.icon_serial.set(self.icon_serial.get() + 1);
        }
        self.sync_menu();
        self.changed();
    }

    /// Read every property again (GetAll).
    pub fn refresh(&self) {
        let w = self.me.clone();
        let args = (ITEM_IFACE,).to_variant();
        self.conn.call(Some(&self.name), &self.path, PROPS, "GetAll", Some(&args), Some(glib::VariantTy::new("(a{sv})").unwrap()), gio::DBusCallFlags::NONE, TIMEOUT, None::<&gio::Cancellable>, move |r| {
            // an error: gone, or not an SNI -- stays hidden
            if let (Some(it), Ok(r)) = (w.upgrade(), r) {
                it.got(&r.child_value(0));
            }
        });
    }

    fn got(&self, d: &glib::Variant) {
        if self.closed() {
            return; // removed meanwhile
        }
        let mut props = Props::new();
        for e in d.iter() {
            if let (Some(k), Some(v)) = (e.child_value(0).str().map(String::from), e.child_value(1).as_variant()) {
                props.insert(k, v);
            }
        }
        // (the icon itself unchanged: the drawn one is kept)
        if !self.ready.get() || icon_changed(&self.props.borrow(), &props) {
            self.icon_serial.set(self.icon_serial.get() + 1);
        }
        self.props.replace(props);
        self.sync_menu();
        self.ready.set(true);
        self.changed();
    }

    fn changed(&self) {
        if let (Some(h), Some(me)) = (self.host.upgrade(), self.me.upgrade()) {
            h.changed(Some(&me));
        }
    }

    fn sync_menu(&self) {
        let menu = str_of(&self.props.borrow(), "Menu");
        let stale = self.menu.borrow().as_ref().is_some_and(|m| m.path() != menu);
        if stale {
            if let Some(m) = self.menu.take() {
                m.close();
            }
        }
        if self.menu.borrow().is_none() && !menu.is_empty() && menu != "/" {
            self.menu.replace(Some(DBusMenu::new(&self.conn, &self.name, &menu)));
        }
        if let Some(m) = self.menu.borrow().as_ref() {
            m.set_steam(self.id().to_lowercase().contains("steam"));
        }
    }

    // -- actions -----------------------------------------------------------
    fn item_call(&self, method: &str, args: glib::Variant, on_error: Option<Box<dyn FnOnce()>>) {
        self.conn.call(Some(&self.name), &self.path, ITEM_IFACE, method, Some(&args), None, gio::DBusCallFlags::NONE, TIMEOUT, None::<&gio::Cancellable>, move |r| {
            if r.is_err() {
                if let Some(f) = on_error {
                    f();
                }
            }
        });
    }

    /// Activate at (x, y); `fallback` runs when the app answers with an
    /// error (libappindicator items have no Activate).
    pub fn activate(&self, x: i32, y: i32, fallback: Option<Box<dyn FnOnce()>>) {
        self.item_call("Activate", (x, y).to_variant(), fallback);
    }

    pub fn secondary_activate(&self, x: i32, y: i32) {
        self.item_call("SecondaryActivate", (x, y).to_variant(), None);
    }

    pub fn context_menu(&self, x: i32, y: i32) {
        self.item_call("ContextMenu", (x, y).to_variant(), None);
    }

    /// Scroll by `delta` steps; `orientation` "vertical" or "horizontal".
    pub fn scroll(&self, delta: i32, orientation: &str) {
        self.item_call("Scroll", (delta, orientation).to_variant(), None);
    }

    /// done(desktop id or None): the app behind this icon -- from its Id
    /// ("steam", "KeePassXC"), else from the program owning its bus name
    /// (Electron/Chromium items are "chrome_status_icon_1").
    pub fn app(&self, done: impl FnOnce(Option<String>) + 'static) {
        let id = self.id();
        if let Some(did) = crate::match_app_id(&id) {
            return done(Some(did));
        }
        let args = (self.name.as_str(),).to_variant();
        self.conn.call(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "GetConnectionUnixProcessID", Some(&args), None, gio::DBusCallFlags::NONE, 1000, None::<&gio::Cancellable>, move |r| {
            let pid = r.ok().and_then(|r| r.child_value(0).get::<u32>());
            done(pid.and_then(|p| crate::app_of_pid(p, Path::new("/proc"))));
        });
    }
}
