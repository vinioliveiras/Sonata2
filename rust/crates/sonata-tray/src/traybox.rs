//! One menu bar's tray icons (placed left of the status items).

use crate::dbusmenu::{DBusMenu, Entry, Open};
use crate::host::{host, Host, ListenerId};
use crate::icon::MonoIcon;
use crate::item::TrayItem;
use gtk::prelude::*;
use gtk::{gdk, gio, glib, graphene};
use sonata_i18n::tr;
use sonata_ui::menu::{Item, Options};
use std::cell::{Cell, RefCell};
use std::collections::HashSet;
use std::rc::{Rc, Weak};
use std::time::Duration;

/// The desktop's double-click time (GTK's setting), kept snappy.
pub fn double_click_ms() -> u32 {
    let ms = gtk::Settings::default().map(|s| s.gtk_double_click_time()).filter(|m| *m > 0).unwrap_or(400);
    ms.clamp(150, 400) as u32
}

/// A left click's action waits the double-click time: a second click is a
/// double-click (the app comes forward) -- straight away, the first
/// click's menu took the second (Steam: a menu item was picked) and a
/// toggling Activate hid the window again (Spotify) (Vini).
#[derive(Default)]
pub(crate) struct Clicks {
    single: RefCell<Option<glib::SourceId>>,
    skip: Cell<bool>,
}

impl Clicks {
    /// A left click ("clicked"): `run` once the double-click time is over,
    /// unless a second press comes.
    pub(crate) fn left(self: &Rc<Self>, ms: u32, run: impl FnOnce() + 'static) {
        if self.skip.replace(false) {
            return; // the double-click's second release
        }
        if let Some(id) = self.single.take() {
            id.remove();
        }
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(ms as u64), move || {
            if let Some(c) = w.upgrade() {
                c.single.take();
            }
            run();
        });
        self.single.replace(Some(id));
    }

    /// The second press of a double-click: the first click's action never runs.
    pub(crate) fn double(&self) {
        if let Some(id) = self.single.take() {
            id.remove();
        }
        self.skip.set(true);
    }
}

struct Inner {
    widget: gtk::Box,
    host: Rc<Host>,
    shown: Cell<bool>,
    buttons: RefCell<Vec<(String, gtk::Button, MonoIcon)>>,
    listener: Cell<Option<ListenerId>>,
    clicks: Rc<Clicks>,
    menus: RefCell<Vec<Weak<DBusMenu>>>, // menus opened from here (a double-click closes them)
}

/// One menu bar's tray icons. Cheap to clone (one box).
#[derive(Clone)]
pub struct TrayBox(Rc<Inner>);

fn search_paths() -> &'static std::sync::Mutex<HashSet<String>> {
    static P: std::sync::OnceLock<std::sync::Mutex<HashSet<String>>> = std::sync::OnceLock::new();
    P.get_or_init(Default::default)
}

/// The icon theme knows `name`; an app's own theme-structured folder
/// (hicolor/...) is added to its search path first when needed.
fn theme_has(display: &gdk::Display, name: &str, theme_path: &str) -> bool {
    let theme = gtk::IconTheme::for_display(display);
    if !theme.has_icon(name) && !theme_path.is_empty() && std::path::Path::new(theme_path).is_dir() {
        let new = search_paths().lock().map(|mut s| s.insert(theme_path.to_string())).unwrap_or(false);
        if new {
            theme.add_search_path(theme_path);
        }
    }
    theme.has_icon(name)
}

impl TrayBox {
    /// The tray of a menu bar, on the process's [`host()`]; `shown` is the
    /// bar's "show_tray" setting.
    pub fn new(shown: bool) -> Self {
        Self::with_host(shown, host())
    }

    pub fn with_host(shown: bool, host: Rc<Host>) -> Self {
        let widget = gtk::Box::builder().css_classes(["tray-box"]).valign(gtk::Align::Center).build();
        let t = TrayBox(Rc::new(Inner {
            widget,
            host,
            shown: Cell::new(shown),
            buttons: RefCell::default(),
            listener: Cell::new(None),
            clicks: Rc::default(),
            menus: RefCell::default(),
        }));
        let w = Rc::downgrade(&t.0);
        let id = t.0.host.connect_changed(move |item| {
            if let Some(i) = w.upgrade() {
                TrayBox(i).changed(item);
            }
        });
        t.0.listener.set(Some(id));
        t.changed(None);
        t
    }

    /// The box to place in the menu bar (hidden while it has no icon).
    pub fn widget(&self) -> &gtk::Box {
        &self.0.widget
    }

    /// Stop following the host (the menu bar goes away).
    pub fn stop(&self) {
        if let Some(id) = self.0.listener.take() {
            self.0.host.disconnect(id);
        }
    }

    /// Show or hide the icons (the "show_tray" setting).
    pub fn set_shown(&self, on: bool) {
        self.0.shown.set(on);
        self.changed(None);
    }

    /// The items with a button now, in order (tests, the menu bar's layout).
    pub fn keys(&self) -> Vec<String> {
        self.0.buttons.borrow().iter().map(|(k, _, _)| k.clone()).collect()
    }

    pub fn button(&self, key: &str) -> Option<gtk::Button> {
        self.0.buttons.borrow().iter().find(|(k, _, _)| k == key).map(|(_, b, _)| b.clone())
    }

    fn changed(&self, item: Option<&Rc<TrayItem>>) {
        let me = &self.0;
        let items: Vec<Rc<TrayItem>> = if me.shown.get() { me.host.items().into_iter().filter(|i| i.visible()).collect() } else { Vec::new() };
        let keys: Vec<&str> = items.iter().map(|i| i.key()).collect();
        me.buttons.borrow_mut().retain(|(k, b, _)| {
            let keep = keys.contains(&k.as_str());
            if !keep {
                me.widget.remove(b);
            }
            keep
        });
        let mut prev: Option<gtk::Widget> = None;
        for it in &items {
            let found = me.buttons.borrow().iter().find(|(k, _, _)| k == it.key()).map(|(_, b, i)| (b.clone(), i.clone()));
            let new = found.is_none();
            let (b, icon) = match found {
                Some((b, icon)) => {
                    if b.prev_sibling() != prev {
                        me.widget.reorder_child_after(&b, prev.as_ref());
                    }
                    (b, icon)
                }
                None => {
                    let (b, icon) = self.make_button(it.key());
                    me.widget.insert_child_after(&b, prev.as_ref());
                    me.buttons.borrow_mut().push((it.key().to_string(), b.clone(), icon.clone()));
                    (b, icon)
                }
            };
            if item.is_none() || item.is_some_and(|i| Rc::ptr_eq(i, it)) || new {
                self.update(&b, &icon, it); // only what changed gets a new icon
            }
            prev = Some(b.upcast());
        }
        me.widget.set_visible(!items.is_empty());
    }

    fn update(&self, b: &gtk::Button, icon: &MonoIcon, item: &TrayItem) {
        let scale = self.0.widget.scale_factor().max(1);
        let display = self.0.widget.display();
        let src = item.icon(scale, &|name, path| theme_has(&display, name, path));
        icon.set_icon(src.as_ref(), item.icon_serial(), scale);
        let tip = item.tooltip();
        b.set_tooltip_text((!tip.is_empty()).then_some(tip.as_str()));
    }

    fn make_button(&self, key: &str) -> (gtk::Button, MonoIcon) {
        let b = gtk::Button::builder().css_classes(["topbar-item", "icon", "tray-item"]).can_focus(false).valign(gtk::Align::Center).build();
        let inner = gtk::Box::builder().valign(gtk::Align::Center).build();
        let icon = MonoIcon::new();
        inner.append(&icon);
        b.set_child(Some(&inner));
        let w = Rc::downgrade(&self.0);
        let k = key.to_string();
        b.connect_clicked(move |btn| {
            if let Some(i) = w.upgrade() {
                TrayBox(i).left(btn, &k);
            }
        });
        let g = gtk::GestureClick::builder().button(0).build();
        let (w, k, bw) = (Rc::downgrade(&self.0), key.to_string(), b.downgrade());
        g.connect_pressed(move |gest, n, x, y| {
            if let (Some(i), Some(btn)) = (w.upgrade(), bw.upgrade()) {
                TrayBox(i).press(gest, &btn, x, y, &k, n);
            }
        });
        b.add_controller(g);
        let sc = gtk::EventControllerScroll::new(gtk::EventControllerScrollFlags::BOTH_AXES | gtk::EventControllerScrollFlags::DISCRETE);
        let (w, k) = (Rc::downgrade(&self.0), key.to_string());
        sc.connect_scroll(move |_, dx, dy| {
            let item = w.upgrade().and_then(|i| i.host.item(&k));
            glib::Propagation::from(item.is_some_and(|it| {
                scroll(&it, dx, dy);
                true
            }))
        });
        b.add_controller(sc);
        (b, icon)
    }

    /// A point of the button (default: the middle of its bottom edge), in
    /// its window.
    fn point(btn: &gtk::Button, at: Option<(f64, f64)>) -> (i32, i32) {
        let (x, y) = at.unwrap_or((btn.width() as f64 / 2.0, btn.height() as f64));
        let pt = btn.root().and_then(|root| btn.compute_point(&root, &graphene::Point::new(x as f32, y as f32)));
        pt.map(|p| (p.x() as i32, p.y() as i32)).unwrap_or((0, 0))
    }

    fn left(&self, btn: &gtk::Button, key: &str) {
        let (w, b, k) = (Rc::downgrade(&self.0), btn.downgrade(), key.to_string());
        self.0.clicks.left(double_click_ms(), move || {
            if let (Some(i), Some(btn)) = (w.upgrade(), b.upgrade()) {
                TrayBox(i).click(&btn, &k);
            }
        });
    }

    fn click(&self, btn: &gtk::Button, key: &str) {
        let Some(item) = self.0.host.item(key) else { return };
        if item.item_is_menu() {
            return self.show_menu(btn, &item);
        }
        // an item without Activate (libappindicator) answers with an error: its menu
        let (x, y) = Self::point(btn, None);
        let (w, b, k) = (Rc::downgrade(&self.0), btn.downgrade(), key.to_string());
        item.activate(
            x,
            y,
            Some(Box::new(move || {
                if let (Some(i), Some(btn)) = (w.upgrade(), b.upgrade()) {
                    let t = TrayBox(i);
                    if let Some(item) = t.0.host.item(&k) {
                        t.show_menu(&btn, &item);
                    }
                }
            })),
        );
    }

    fn press(&self, gest: &gtk::GestureClick, btn: &gtk::Button, x: f64, y: f64, key: &str, n_press: i32) {
        let Some(item) = self.0.host.item(key) else { return };
        match gest.current_button() {
            1 if n_press == 2 => {
                gest.set_state(gtk::EventSequenceState::Claimed);
                self.0.clicks.double();
                self.open_app(btn, &item);
            }
            3 => {
                gest.set_state(gtk::EventSequenceState::Claimed);
                self.show_menu(btn, &item);
            }
            2 => {
                gest.set_state(gtk::EventSequenceState::Claimed);
                let (px, py) = Self::point(btn, Some((x, y)));
                item.secondary_activate(px, py);
            }
            _ => {}
        }
    }

    /// Double-click (Vini): the app behind the icon comes forward -- its
    /// window restored and focused, else the app opened (a running
    /// single-instance app shows its window); unknown app: Activate.
    pub fn open_app(&self, btn: &gtk::Button, item: &Rc<TrayItem>) {
        // the first click's menu goes away
        for m in self.0.menus.take().iter().filter_map(|m| m.upgrade()) {
            if let Some(open) = m.open.take() {
                (open.redraw)(Vec::new()); // (an empty redraw closes it: see show_menu)
            }
        }
        let (x, y) = Self::point(btn, None);
        let tooltip = item.tooltip();
        let it = Rc::downgrade(item);
        item.app(move |did| {
            if let Some(d) = did.as_deref() {
                if crate::bring_forward(d, &tooltip) {
                    return;
                }
                if let Some(info) = sonata_ui::apps::lookup(d) {
                    if info.launch(&[], None::<&gio::AppLaunchContext>).is_ok() {
                        return;
                    }
                }
            }
            if let Some(it) = it.upgrade() {
                it.activate(x, y, None);
            }
        });
    }

    /// The item's dbusmenu as a glass menu hanging from its icon; no
    /// exported menu: the item's own ContextMenu.
    pub fn show_menu(&self, btn: &gtk::Button, item: &Rc<TrayItem>) {
        let Some(menu) = item.menu() else {
            let (x, y) = Self::point(btn, None);
            return item.context_menu(x, y);
        };
        btn.add_css_class("open");
        self.0.menus.borrow_mut().retain(|m| m.strong_count() > 0);
        self.0.menus.borrow_mut().push(Rc::downgrade(&menu));
        let (b, mw) = (btn.downgrade(), Rc::downgrade(&menu));
        menu.fetch(move |lay| {
            let (Some(btn), Some(menu)) = (b.upgrade(), mw.upgrade()) else { return };
            let Some(lay) = lay.filter(|_| btn.is_mapped()) else {
                btn.remove_css_class("open");
                return;
            };
            let mut sections = menu.sections(&lay);
            if sections.is_empty() {
                sections = vec![vec![Entry::disabled(&tr!("No Items"))]];
            }
            show(&menu, &btn, sections);
            menu.event(0, "opened");
        });
    }
}

fn scroll(item: &TrayItem, dx: f64, dy: f64) {
    let steps = |d: f64| {
        let r = d.round() as i32;
        if r != 0 {
            r
        } else if d > 0.0 {
            1
        } else {
            -1
        }
    };
    if dy != 0.0 {
        item.scroll(steps(dy), "vertical");
    }
    if dx != 0.0 {
        item.scroll(steps(dx), "horizontal");
    }
}

fn ui_items(sections: Vec<Vec<Entry>>, menu: &Weak<DBusMenu>) -> Vec<Vec<Item>> {
    let click = |id: i32| {
        let m = menu.clone();
        move || {
            if let Some(m) = m.upgrade() {
                m.event(id, "clicked");
            }
        }
    };
    sections
        .into_iter()
        .map(|s| {
            s.into_iter()
                .map(|e| match e {
                    Entry::Item { id, label, enabled, checked: None } => Item::new(&label, click(id)).enabled(enabled),
                    Entry::Item { id, label, enabled, checked: Some(on) } => {
                        let f = click(id);
                        Item::check(&label, on, move |_| f()).enabled(enabled)
                    }
                    Entry::Submenu { label, enabled, sections, .. } => Item::submenu(&label, ui_items(sections, menu)).enabled(enabled),
                })
                .collect()
        })
        .collect()
}

/// macOS menus hang from the icon's left edge, not centred under it
/// (ui/panel.py align_to_start).
fn align_to_start(pop: &gtk::PopoverMenu, anchor: &gtk::Button, gap: i32) {
    let a = anchor.downgrade();
    pop.connect_map(move |p| {
        let (p, a) = (p.clone(), a.clone());
        glib::idle_add_local_once(move || {
            let (pw, Some(a)) = (p.width(), a.upgrade()) else { return };
            if pw > 0 {
                p.set_offset((pw - a.width()) / 2, gap);
            }
        });
    });
}

/// The menu shown from `btn`; it stays the menu's `open` one until it
/// closes. A redraw while open (the app changed its menu) shows a new
/// menu in its place: sonata_ui's menus can't take new rows once built.
fn show(menu: &Rc<DBusMenu>, btn: &gtk::Button, sections: Vec<Vec<Entry>>) {
    let mw = Rc::downgrade(menu);
    let pop = sonata_ui::menu::popup(btn, ui_items(sections, &mw), Options::below());
    pop.set_offset(0, 2);
    align_to_start(&pop, btn, 2);
    let replaced = Rc::new(Cell::new(false));
    let token: Rc<Open> = {
        let (p1, p2, b, r, m) = (pop.downgrade(), pop.downgrade(), btn.downgrade(), replaced.clone(), mw.clone());
        Rc::new(Open {
            visible: Box::new(move || p1.upgrade().is_some_and(|p| p.is_visible())),
            redraw: Box::new(move |sections| {
                let (Some(pop), Some(btn), Some(menu)) = (p2.upgrade(), b.upgrade(), m.upgrade()) else { return };
                if !sections.is_empty() {
                    r.set(true);
                    show(&menu, &btn, sections);
                }
                pop.popdown();
            }),
        })
    };
    menu.open.replace(Some(token.clone()));
    let (b, m, t) = (btn.downgrade(), mw, Rc::downgrade(&token));
    pop.connect_closed(move |_| {
        if replaced.get() {
            return; // its rows live on in the menu shown in its place
        }
        if let Some(b) = b.upgrade() {
            b.remove_css_class("open");
        }
        if let Some(menu) = m.upgrade() {
            let mine = menu.open.borrow().as_ref().is_some_and(|o| t.upgrade().is_some_and(|t| Rc::ptr_eq(o, &t)));
            if mine {
                menu.open.replace(None);
            }
            menu.event(0, "closed");
        }
    });
}
