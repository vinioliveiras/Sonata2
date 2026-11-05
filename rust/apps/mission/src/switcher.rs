//! App switcher (shell/switcher.py; macOS Cmd+Tab): Alt+Tab shows the open
//! apps, most recently used first, as big icons on a glass panel in the
//! middle of the screen; Tab / Shift+Tab (or the arrows) move, releasing Alt
//! switches to the app (all its windows come forward, minimized ones
//! restored), Q quits it, Escape cancels. An app with several windows has
//! one icon per window (Vini, like Windows), side by side, the window's
//! title under the selected one; picking one brings that window alone
//! forward. One press still goes to the previous app.
//!
//! The Python one lives in the menu bar process (it already tracks the
//! windows) and reads them from wlr-foreign-toplevel. Here the windows come
//! through `Windows`: `IpcWindows` asks Wayfire's IPC (list-views, with
//! each window's last focus, so the most recently used order needs no
//! menu bar); the menu bar can pass its own and its `mru`.

use crate::{shell_surface, Ipc, Wayfire};
use adw::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, graphene, gsk};
use serde_json::{json, Value};
use std::cell::{Cell, RefCell};
use std::rc::Rc;
use std::time::Duration;

pub const ICON: i32 = 96;
/// while open: is Alt / Super still held?
pub const MODS_POLL_MS: u64 = 60;
/// never got the keyboard by then (a drag holds it): switch at once
pub const NO_KEYBOARD_MS: i64 = 500;
/// (after opening: the keyboard's state arrives with the focus)
pub const MODS_GRACE_MS: i64 = 120;
/// the selection glides from app to app
pub const SLIDE_MS: u32 = 150;
pub const ICON_MIN: i32 = 32;
/// what the panel adds around its icons (.sw-panel margins + padding, .sw-item padding)
pub const CHROME_W: i32 = 2 * 40 + 2 * 14;
pub const ITEM_PAD: i32 = 2 * 8 + 4;

/// The icons' size for n apps: 96 px, smaller when that many don't fit
/// the display's width (macOS shrinks them too), never under 32.
pub fn icon_size(n: usize, screen_w: i32) -> i32 {
    if n == 0 || screen_w == 0 {
        return ICON;
    }
    let fit = (screen_w - CHROME_W - 16).div_euclid(n as i32) - ITEM_PAD;
    fit.clamp(ICON_MIN, ICON)
}

// -- the windows ----------------------------------------------------------------------------

/// One app window, as the switcher needs it.
#[derive(Clone, Debug, PartialEq)]
pub struct Win {
    pub id: i64,
    pub app_id: String,
    pub title: String,
    pub minimized: bool,
    /// when it last had the focus; 0: never seen
    pub focused_at: i64,
}

impl Win {
    /// A Wayfire view (list-views), None for what isn't an app's window
    /// (panels, popups, the shell's own surfaces).
    pub fn from_view(v: &Value) -> Option<Win> {
        let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        if v.get("type").and_then(Value::as_str).is_some_and(|t| t != "toplevel")
            || v.get("role").and_then(Value::as_str).is_some_and(|r| r != "toplevel")
            || v.get("mapped") == Some(&Value::Bool(false))
            || shell_surface(&s("app-id"))
        {
            return None;
        }
        Some(Win {
            id: v.get("id")?.as_i64()?,
            app_id: s("app-id"),
            title: s("title"),
            minimized: v.get("minimized").and_then(Value::as_bool).unwrap_or(false),
            focused_at: v.get("last-focus-timestamp").and_then(Value::as_i64).unwrap_or(0),
        })
    }
}

/// Where the switcher's windows come from and what it does to them.
pub trait Windows {
    fn windows(&self) -> Vec<Win>;
    /// Bring these forward in this order (the last one on top, focused),
    /// minimized ones restored.
    fn activate(&self, wins: &[Win]);
    fn close(&self, wins: &[Win]);
}

/// The windows from Wayfire's IPC.
pub struct IpcWindows<I: Ipc + Send + Clone + 'static = Wayfire>(pub I);

impl<I: Ipc + Send + Clone + 'static> Windows for IpcWindows<I> {
    fn windows(&self) -> Vec<Win> {
        let got = self.0.call("window-rules/list-views", json!({}));
        got.as_ref().and_then(Value::as_array).map(|a| a.iter().filter_map(Win::from_view).collect()).unwrap_or_default()
    }

    fn activate(&self, wins: &[Win]) {
        let (ipc, wins) = (self.0.clone(), wins.to_vec());
        // (one thread, in order: the last one ends on top)
        crate::call_bg(move || {
            for w in wins {
                if w.minimized {
                    ipc.call("wm-actions/set-minimized", json!({"view_id": w.id, "state": false}));
                }
                ipc.call("window-rules/focus-view", json!({"id": w.id}));
            }
        });
    }

    fn close(&self, wins: &[Win]) {
        let (ipc, wins) = (self.0.clone(), wins.to_vec());
        crate::call_bg(move || {
            for w in wins {
                ipc.call("window-rules/close-view", json!({"id": w.id}));
            }
        });
    }
}

// -- what is shown and picked (no GTK: tests) -----------------------------------------------

/// The open apps, in the order shown.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Model {
    /// the app key of each entry
    pub keys: Vec<String>,
    /// the entry's window; None: the whole app (it has one window)
    pub wins: Vec<Option<Win>>,
    /// each app's windows
    pub groups: Vec<(String, Vec<Win>)>,
    pub index: usize,
}

impl Model {
    /// The windows grouped by app (`key_of`: the app a window belongs to),
    /// `mru` first (app keys, most recent first), then the others, the one
    /// used last first.
    pub fn build(windows: &[Win], mru: &[String], key_of: impl Fn(&Win) -> String) -> Model {
        let mut groups: Vec<(String, Vec<Win>)> = Vec::new();
        for w in windows {
            let key = key_of(w);
            match groups.iter_mut().find(|(k, _)| *k == key) {
                Some((_, ws)) => ws.push(w.clone()),
                None => groups.push((key, vec![w.clone()])),
            }
        }
        let last = |ws: &[Win]| ws.iter().map(|w| w.focused_at).max().unwrap_or(0);
        let mut rest: Vec<&(String, Vec<Win>)> = groups.iter().filter(|(k, _)| !mru.contains(k)).collect();
        rest.sort_by_key(|(_, ws)| std::cmp::Reverse(last(ws)));
        let order: Vec<String> = mru.iter().filter(|k| groups.iter().any(|(g, _)| g == *k)).cloned().chain(rest.into_iter().map(|(k, _)| k.clone())).collect();
        let (keys, wins) = entries(&order, &groups);
        Model { keys, wins, groups, index: 0 }
    }

    pub fn group(&self, key: &str) -> &[Win] {
        self.groups.iter().find(|(k, _)| k == key).map(|(_, ws)| ws.as_slice()).unwrap_or(&[])
    }

    /// One press. Opening, the previous app is preselected (past the
    /// windows of the app in front).
    pub fn step(&mut self, mut direction: i32, opening: bool) {
        if opening {
            let first = self.keys.first().and_then(|k0| self.keys.iter().position(|k| k != k0));
            self.index = first.map_or(0, |f| f.wrapping_sub(1));
            direction = if first.is_some() || self.keys.len() > 1 { 1 } else { 0 };
        }
        if !self.keys.is_empty() {
            let n = self.keys.len() as i64;
            self.index = ((self.index as i64 + direction as i64).rem_euclid(n)) as usize;
        }
    }

    /// What comes forward, in order: that window alone, or all the app's
    /// windows (minimized ones only when there are no others), the one used
    /// last activated last (on top, focused).
    pub fn picked(&self) -> Vec<Win> {
        let Some(key) = self.keys.get(self.index) else { return Vec::new() };
        if let Some(Some(one)) = self.wins.get(self.index) {
            return vec![one.clone()];
        }
        let ws = self.group(key);
        let mut shown: Vec<Win> = ws.iter().filter(|w| !w.minimized).cloned().collect();
        if shown.is_empty() {
            shown = ws.to_vec();
        }
        shown.sort_by_key(|w| w.focused_at);
        shown
    }
}

/// (app keys, windows): one entry per app, or per window for an app with
/// several (its last used first); window None: the whole app.
pub fn entries(order: &[String], groups: &[(String, Vec<Win>)]) -> (Vec<String>, Vec<Option<Win>>) {
    let (mut keys, mut wins) = (Vec::new(), Vec::new());
    for key in order {
        let Some((_, ws)) = groups.iter().find(|(k, _)| k == key) else { continue };
        if ws.len() > 1 {
            let mut sorted = ws.clone();
            sorted.sort_by_key(|w| std::cmp::Reverse(w.focused_at));
            for w in sorted {
                keys.push(key.clone());
                wins.push(Some(w));
            }
        } else {
            keys.push(key.clone());
            wins.push(None);
        }
    }
    (keys, wins)
}

/// What the check of the held keys decides.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Mods {
    /// closed meanwhile: stop checking
    Stop,
    Keep,
    /// as letting go of Alt would have
    Switch,
}

// Vini: sometimes the switcher stayed open until an app was picked -- a
// quick Alt+Tab let go of Alt before this window had the keyboard, so its
// release never arrived. While open, the held keys are checked: no Alt /
// Super any more (and the window has the keyboard, so the state is real)
// switches, like letting go would have.
pub fn check_mods(open: bool, active: bool, waited_ms: i64, held: Option<bool>) -> Mods {
    if !open {
        return Mods::Stop;
    }
    if !active && waited_ms >= NO_KEYBOARD_MS {
        // Vini: Alt+Tab while dragging a video to WhatsApp left the switcher
        // open for good -- the drag keeps the keyboard, so Alt's release and
        // the held keys never reach it. The app picked comes forward (the
        // drag goes on and can be dropped there).
        return Mods::Switch;
    }
    if !active || waited_ms < MODS_GRACE_MS {
        return Mods::Keep;
    }
    if held == Some(false) { Mods::Switch } else { Mods::Keep }
}

// -- the panel ------------------------------------------------------------------------------

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct SwitcherPanel {
        /// the selected app
        pub widget: RefCell<Option<gtk::Widget>>,
        /// where the glide started (x, y, w, h)
        pub start: Cell<Option<[f32; 4]>>,
        /// glide progress
        pub t: Cell<f64>,
        pub anim: RefCell<Option<adw::TimedAnimation>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for SwitcherPanel {
        const NAME: &'static str = "SonataRsSwitcherPanel";
        type Type = super::SwitcherPanel;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for SwitcherPanel {}
    impl BoxImpl for SwitcherPanel {}

    impl WidgetImpl for SwitcherPanel {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            // the panel's own background/border (CSS), then the selection, then the icons
            let obj = self.obj();
            if let Some([x, y, w, h]) = obj.drawn_rect() {
                let rect = graphene::Rect::new(x, y, w, h);
                snap.push_rounded_clip(&gsk::RoundedRect::from_rect(rect, 14.0));
                let c = sonata_ui::rgba("label");
                snap.append_color(&gdk::RGBA::new(c.red(), c.green(), c.blue(), 0.16), &rect);
                snap.pop();
            }
            let mut child = obj.first_child();
            while let Some(c) = child {
                obj.snapshot_child(&c, snap);
                child = c.next_sibling();
            }
        }
    }
}

glib::wrapper! {
    /// The row of apps with the selection drawn behind them, gliding to the
    /// selected app instead of jumping. The selection is measured on the
    /// app when drawing (never before the layout exists: that drew a small
    /// dot in the corner).
    pub struct SwitcherPanel(ObjectSubclass<imp::SwitcherPanel>) @extends gtk::Box, gtk::Widget, @implements gtk::Orientable;
}

impl Default for SwitcherPanel {
    fn default() -> Self {
        let p: Self = glib::Object::builder().property("spacing", 4).property("css-classes", vec!["sw-panel".to_string()]).build();
        p.imp().t.set(1.0);
        p
    }
}

impl SwitcherPanel {
    /// The next select() lands without a glide.
    pub fn reset(&self) {
        self.imp().widget.replace(None);
        self.imp().start.set(None);
        self.imp().t.set(1.0);
    }

    pub fn selected(&self) -> Option<gtk::Widget> {
        self.imp().widget.borrow().clone()
    }

    fn rect(&self, widget: Option<&gtk::Widget>) -> Option<[f32; 4]> {
        let w = widget?;
        if w.parent().as_ref() != Some(self.upcast_ref()) {
            return None;
        }
        let b = w.compute_bounds(self)?;
        (b.width() >= 2.0 && b.height() >= 2.0).then(|| [b.x(), b.y(), b.width(), b.height()])
    }

    fn drawn_rect(&self) -> Option<[f32; 4]> {
        let to = self.rect(self.imp().widget.borrow().as_ref())?;
        let t = self.imp().t.get();
        match self.imp().start.get() {
            Some(from) if t < 1.0 => Some(std::array::from_fn(|i| from[i] + (to[i] - from[i]) * t as f32)),
            _ => Some(to),
        }
    }

    pub fn select(&self, widget: &impl IsA<gtk::Widget>, animate: bool) {
        let now = if animate { self.drawn_rect() } else { None };
        let imp = self.imp();
        imp.widget.replace(Some(widget.clone().upcast()));
        if let Some(a) = imp.anim.borrow().as_ref() {
            a.pause();
        }
        let Some(now) = now else {
            imp.start.set(None);
            imp.t.set(1.0);
            self.queue_draw();
            return;
        };
        imp.start.set(Some(now));
        imp.t.set(0.0);
        let weak = self.downgrade();
        let target = adw::CallbackAnimationTarget::new(move |v| {
            if let Some(p) = weak.upgrade() {
                p.imp().t.set(v);
                p.queue_draw();
            }
        });
        let anim = adw::TimedAnimation::new(self, 0.0, 1.0, SLIDE_MS, target);
        anim.set_easing(adw::Easing::EaseOutCubic);
        anim.play();
        imp.anim.replace(Some(anim));
    }
}

/// The name under the selected icon. Vini: a long one made its tile wider
/// than the others -- it asks for no width of its own (one character), so
/// the tile stays the icon's size and a longer name ends in "...".
pub fn name_label() -> gtk::Label {
    gtk::Label::builder().css_classes(["sw-name"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(1).build()
}

// -- the window -----------------------------------------------------------------------------

pub struct Switcher {
    pub win: gtk::Window,
    panel: SwitcherPanel,
    name: gtk::Label,
    /// each entry's tile and name
    items: RefCell<Vec<(gtk::Box, String)>>,
    model: RefCell<Model>,
    windows: Rc<dyn Windows>,
    /// app keys, most recent first (the menu bar's); empty: by each window's last focus
    pub mru: RefCell<Vec<String>>,
    /// where the pointer was when the switcher opened
    origin: Cell<Option<(f64, f64)>>,
    opened_at: Cell<i64>,
    mods_src: RefCell<Option<glib::SourceId>>,
    /// a plain window (--preview): no held keys to follow, nothing grabs the keyboard
    layered: bool,
}

impl Switcher {
    /// `layered` false: a plain window (previews, no layer-shell).
    pub fn new(app: &gtk::Application, windows: Rc<dyn Windows>, layered: bool) -> Rc<Switcher> {
        let win = gtk::Window::builder().application(app).title("App Switcher").decorated(false).resizable(false).build();
        win.add_css_class("sonata-switcher");
        let panel = SwitcherPanel::default();
        crate::glass_class(&panel);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&panel);
        win.set_child(Some(&col));
        let layered = layered && sonata_shell::layer::available();
        if layered {
            use gtk4_layer_shell::{KeyboardMode, Layer, LayerShell};
            win.init_layer_shell();
            win.set_namespace(Some("sonata2-switcher"));
            win.set_layer(Layer::Overlay);
            win.set_keyboard_mode(KeyboardMode::Exclusive);
            win.set_exclusive_zone(-1);
        }
        let me = Rc::new(Switcher {
            win,
            panel,
            name: name_label(),
            items: RefCell::default(),
            model: RefCell::default(),
            windows,
            mru: RefCell::default(),
            origin: Cell::new(None),
            opened_at: Cell::new(0),
            mods_src: RefCell::default(),
            layered,
        });
        let keys = gtk::EventControllerKey::new();
        let weak = Rc::downgrade(&me);
        keys.connect_key_pressed(move |_, key, _, state| {
            if let Some(me) = weak.upgrade() {
                me.key(key, state);
            }
            glib::Propagation::Stop
        });
        let weak = Rc::downgrade(&me);
        keys.connect_key_released(move |_, key, _, _| {
            // Super+Tab (macOS Cmd+Tab) and Alt+Tab (Windows): letting go switches
            use gdk::Key;
            if [Key::Super_L, Key::Super_R, Key::Meta_L, Key::Meta_R, Key::Alt_L, Key::Alt_R].contains(&key) {
                if let Some(me) = weak.upgrade() {
                    me.switch();
                }
            }
        });
        me.win.add_controller(keys);
        // the mouse too (macOS): moving over an app selects it, a click switches
        let motion = gtk::EventControllerMotion::new();
        let weak = Rc::downgrade(&me);
        motion.connect_motion(move |_, x, y| {
            if let Some(me) = weak.upgrade() {
                me.pointer(x, y);
            }
        });
        me.panel.add_controller(motion);
        let click = gtk::GestureClick::new();
        let weak = Rc::downgrade(&me);
        click.connect_released(move |_, _, x, y| {
            if let Some(me) = weak.upgrade() {
                if let Some(i) = me.item_at(x, y) {
                    me.model.borrow_mut().index = i;
                    me.switch();
                }
            }
        });
        me.panel.add_controller(click);
        me
    }

    fn closing(&self) -> bool {
        self.panel.has_css_class("closing")
    }

    /// Driven by the Alt+Tab binding: 1 the next app, -1 the one before.
    pub fn step(self: &Rc<Self>, direction: i32) {
        let opening = !self.win.is_visible() || self.closing();
        if opening {
            self.open();
        }
        self.model.borrow_mut().step(direction, opening);
        if !self.model.borrow().keys.is_empty() {
            self.mark(true);
        }
    }

    fn open(self: &Rc<Self>) {
        let mru = self.mru.borrow().clone();
        let model = Model::build(&self.windows.windows(), &mru, |w| sonata_desktop::appmatch::match_app_id(&w.app_id).unwrap_or_else(|| w.app_id.clone()));
        while let Some(c) = self.panel.first_child() {
            self.panel.remove(&c);
        }
        // (the smallest display: the compositor picks the focused one, unknown here)
        let screen_w = sonata_shell::layer::monitors().iter().map(|m| m.geometry().width()).min().unwrap_or(0);
        let size = icon_size(model.keys.len(), screen_w);
        let mut items = Vec::new();
        for (i, key) in model.keys.iter().enumerate() {
            let tile = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["sw-item"]).build();
            let img = gtk::Image::builder().pixel_size(size).build();
            let mut name = match sonata_ui::apps::lookup(key) {
                Some(info) => {
                    sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(info.upcast_ref()));
                    info.display_name().to_string()
                }
                None => {
                    // no desktop entry (a Steam game, a Windows program, any app): named as in the Dock
                    let titles: Vec<(&str, bool)> = model.group(key).iter().map(|w| (w.title.as_str(), false)).collect();
                    let (name, icon, _) = sonata_desktop::windowapps::describe(key, &sonata_desktop::windowapps::window_title(&titles));
                    sonata_ui::icons::set_image(&img, &icon);
                    name
                }
            };
            if let Some(Some(w)) = model.wins.get(i) {
                if !w.title.is_empty() {
                    name = w.title.clone(); // one of its windows: which one
                }
            }
            tile.append(&img);
            self.panel.append(&tile);
            items.push((tile, name));
        }
        let empty = model.keys.is_empty();
        self.items.replace(items);
        self.model.replace(model);
        if empty {
            return;
        }
        self.origin.set(None);
        self.panel.reset(); // opens on the selection, no glide
        self.panel.remove_css_class("closing");
        self.panel.remove_css_class("opening");
        self.win.present();
        self.panel.add_css_class("opening");
        self.opened_at.set(glib::monotonic_time());
        if self.layered && self.mods_src.borrow().is_none() {
            let weak = Rc::downgrade(self);
            let id = glib::timeout_add_local(Duration::from_millis(MODS_POLL_MS), move || {
                let Some(me) = weak.upgrade() else { return glib::ControlFlow::Break };
                let waited = (glib::monotonic_time() - me.opened_at.get()) / 1000;
                let open = me.win.is_visible() && !me.closing();
                match check_mods(open, me.win.is_active(), waited, me.held()) {
                    Mods::Keep => glib::ControlFlow::Continue,
                    m => {
                        me.mods_src.replace(None);
                        if m == Mods::Switch {
                            me.switch();
                        }
                        glib::ControlFlow::Break
                    }
                }
            });
            self.mods_src.replace(Some(id));
        }
    }

    /// Alt, Super or Meta still held; None: can't tell.
    fn held(&self) -> Option<bool> {
        let kb = WidgetExt::display(&self.win).default_seat()?.keyboard()?;
        let held = gdk::ModifierType::SUPER_MASK | gdk::ModifierType::ALT_MASK | gdk::ModifierType::META_MASK;
        Some(kb.modifier_state().intersects(held))
    }

    fn mark(&self, animate: bool) {
        let items = self.items.borrow();
        for (tile, _) in items.iter() {
            // the name sits under the selected icon (macOS)
            if tile.last_child().as_ref() == Some(self.name.upcast_ref()) {
                tile.remove(&self.name);
            }
        }
        let index = self.model.borrow().index;
        if let Some((tile, name)) = items.get(index) {
            self.name.set_label(name);
            tile.append(&self.name);
            // after the layout that moved the name: then glide there
            self.panel.select(tile, animate && self.panel.selected().is_some());
        }
    }

    // -- the mouse ------------------------------------------------------------------------
    fn item_at(&self, x: f64, y: f64) -> Option<usize> {
        let p = graphene::Point::new(x as f32, y as f32);
        self.items.borrow().iter().position(|(tile, _)| tile.compute_bounds(&self.panel).is_some_and(|r| r.contains_point(&p)))
    }

    /// Hovering selects -- once the pointer really moves: a pointer that
    /// happens to rest where the switcher opens doesn't steal the selection.
    fn pointer(&self, x: f64, y: f64) {
        let Some((ox, oy)) = self.origin.get() else {
            self.origin.set(Some((x, y)));
            return;
        };
        if (x - ox).abs() + (y - oy).abs() < 4.0 {
            return;
        }
        if let Some(i) = self.item_at(x, y) {
            if i != self.model.borrow().index {
                self.model.borrow_mut().index = i;
                self.mark(true);
            }
        }
    }

    // -- keys while open ------------------------------------------------------------------
    fn key(self: &Rc<Self>, key: gdk::Key, state: gdk::ModifierType) {
        use gdk::Key;
        let shift = state.contains(gdk::ModifierType::SHIFT_MASK);
        if key == Key::Tab || key == Key::ISO_Left_Tab {
            self.step(if shift || key == Key::ISO_Left_Tab { -1 } else { 1 });
        } else if key == Key::Right || key == Key::Left {
            self.step(if key == Key::Right { 1 } else { -1 });
        } else if key == Key::Escape {
            self.close();
        } else if key == Key::q || key == Key::Q {
            let m = self.model.borrow().clone();
            if let Some(k) = m.keys.get(m.index) {
                self.windows.close(m.group(k));
                self.close();
            }
        } else if key == Key::Return {
            self.switch();
        }
    }

    /// The app (or window) picked comes forward; the switcher closes.
    pub fn switch(&self) {
        if self.win.is_visible() {
            let picked = self.model.borrow().picked();
            if !picked.is_empty() {
                self.windows.activate(&picked);
            }
        }
        self.close();
    }

    /// A quick fade, then gone.
    pub fn close(&self) {
        if !self.win.is_visible() {
            return;
        }
        self.panel.remove_css_class("opening");
        self.panel.add_css_class("closing");
        let (win, panel) = (self.win.downgrade(), self.panel.downgrade());
        glib::timeout_add_local_once(Duration::from_millis(sonata_ui::tokens::ms(130.0) as u64), move || {
            if let (Some(w), Some(p)) = (win.upgrade(), panel.upgrade()) {
                if w.is_visible() && p.has_css_class("closing") {
                    w.set_visible(false);
                }
            }
        });
    }

    pub fn is_open(&self) -> bool {
        self.win.is_visible() && !self.closing()
    }
}

/// Alt+Tab while Mission Control is open (Vini: pick a window with it):
/// its highlight moves, and letting go of Alt picks it (Sonata's Wayfire:
/// wayfire-scale-tab.patch). False when it isn't open -- the app switcher
/// then, as ever (its list would open hidden under Mission Control).
/// (__main__.mission_control_step)
pub fn mission_control_step(ipc: &impl Ipc, back: bool) -> bool {
    ipc.call("sonata/scale-cycle", json!({"back": back})).is_some_and(|r| r.get("result").and_then(Value::as_str) == Some("ok"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn win(id: i64, app: &str, title: &str, at: i64) -> Win {
        Win { id, app_id: app.into(), title: title.into(), minimized: false, focused_at: at }
    }

    /// tests/test_switcher_windows.py's setUp: code with two windows, a terminal with one.
    fn model() -> (Model, Win, Win, Win) {
        let (code1, code2, term) = (win(1, "code", "a.py", 30), win(2, "code", "b.py", 10), win(3, "term", "~", 20));
        let m = Model::build(&[code2.clone(), term.clone(), code1.clone()], &["code".into(), "term".into()], |w| w.app_id.clone());
        (m, code1, code2, term)
    }

    #[test]
    fn test_one_entry_per_window_last_used_first() {
        let (m, code1, code2, _) = model();
        assert_eq!(m.keys, ["code", "code", "term"]);
        assert_eq!(m.wins, [Some(code1), Some(code2), None]); // a lone window: the app as before
    }

    #[test]
    fn test_first_press_goes_to_the_previous_app() {
        let (mut m, ..) = model();
        m.step(1, true);
        assert_eq!(m.keys[m.index], "term"); // past the app in front's other window
    }

    #[test]
    fn test_picking_a_window_brings_it_alone() {
        let (mut m, _, code2, term) = model();
        m.index = 1; // code's other window
        assert_eq!(m.picked(), [code2]);
        m.index = 2; // a lone app, as before
        assert_eq!(m.picked(), [term]);
    }

    #[test]
    fn test_only_one_app_with_two_windows() {
        let (code1, code2) = (win(1, "code", "a.py", 30), win(2, "code", "b.py", 10));
        let mut m = Model::build(&[code1, code2.clone()], &["code".into()], |w| w.app_id.clone());
        m.step(1, true);
        assert_eq!(m.wins[m.index], Some(code2)); // the other window
    }

    #[test]
    fn steps_wrap_and_one_app_stays() {
        let (mut m, ..) = model();
        m.step(1, true);
        m.step(1, false);
        assert_eq!(m.index, 0);
        m.step(-1, false);
        assert_eq!(m.index, 2);
        let mut one = Model::build(&[win(9, "x", "", 1)], &[], |w| w.app_id.clone());
        one.step(1, true);
        assert_eq!(one.index, 0);
        let mut none = Model::default();
        none.step(1, false);
        assert_eq!(none.index, 0);
    }

    #[test]
    fn without_a_menu_bar_the_last_used_app_comes_first() {
        let m = Model::build(&[win(1, "a", "", 5), win(2, "b", "", 50), win(3, "c", "", 20)], &[], |w| w.app_id.clone());
        assert_eq!(m.keys, ["b", "c", "a"]);
        let m = Model::build(&[win(1, "a", "", 5), win(2, "b", "", 50)], &["a".into(), "gone".into()], |w| w.app_id.clone());
        assert_eq!(m.keys, ["a", "b"]);
    }

    #[test]
    fn a_whole_app_comes_forward_minimized_only_when_alone() {
        let mut a = win(1, "a", "", 10);
        let mut b = win(2, "a", "", 30);
        let c = win(3, "a", "", 20);
        b.minimized = true;
        let m = Model { keys: vec!["a".into()], wins: vec![None], groups: vec![("a".into(), vec![a.clone(), b.clone(), c.clone()])], index: 0 };
        assert_eq!(m.picked(), [a.clone(), c]); // the last used on top
        a.minimized = true;
        let m = Model { groups: vec![("a".into(), vec![b.clone(), a.clone()])], ..m };
        assert_eq!(m.picked(), [a, b]);
    }

    /// tests/test_switcher_drag.py
    #[test]
    fn test_switches_when_it_never_gets_the_keyboard() {
        assert_eq!(check_mods(true, false, NO_KEYBOARD_MS + 10, Some(true)), Mods::Switch);
    }

    #[test]
    fn test_waits_a_moment_first_and_with_the_keyboard_follows_alt() {
        assert_eq!(check_mods(true, false, 100, Some(true)), Mods::Keep);
        assert_eq!(check_mods(true, true, NO_KEYBOARD_MS + 10, Some(true)), Mods::Keep); // Alt held: stays open
        assert_eq!(check_mods(true, true, MODS_GRACE_MS - 1, Some(false)), Mods::Keep); // (the state not in yet)
        assert_eq!(check_mods(true, true, MODS_GRACE_MS, Some(false)), Mods::Switch);
        assert_eq!(check_mods(true, true, MODS_GRACE_MS, None), Mods::Keep);
        assert_eq!(check_mods(false, true, 0, Some(false)), Mods::Stop);
    }

    /// tests/test_switcher_name_width.py -- Vini: in Alt+Tab, an app with a
    /// long name (or a window with a long title) made its tile wider when
    /// selected; the tiles stopped being the same size.
    #[test]
    fn test_long_app_name_widens_the_selected_tile() {
        if gtk::init().is_err() {
            return; // (no display)
        }
        // the width a tile asks for, its name under it or not (as open / mark build it)
        let width = |size: i32, name: Option<&str>| {
            let tile = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["sw-item"]).build();
            tile.append(&gtk::Image::builder().pixel_size(size).build());
            if let Some(n) = name {
                let l = name_label();
                l.set_label(n);
                tile.append(&l);
            }
            tile.measure(gtk::Orientation::Horizontal, -1).1
        };
        let long = "Visual Studio Code - Insiders -- a very long window title.py";
        for size in [ICON, 64, ICON_MIN] {
            let plain = width(size, None);
            assert_eq!(width(size, Some(long)), plain, "{size}");
            assert_eq!(width(size, Some("Files")), plain, "{size}");
        }
    }

    #[test]
    fn icons_shrink_to_fit() {
        assert_eq!(icon_size(0, 1920), ICON);
        assert_eq!(icon_size(5, 0), ICON);
        assert_eq!(icon_size(5, 1920), ICON);
        assert_eq!(icon_size(20, 1920), (1920 - CHROME_W - 16) / 20 - ITEM_PAD);
        assert_eq!(icon_size(200, 1280), ICON_MIN);
    }

    #[test]
    fn windows_from_wayfire() {
        let v = json!({"id": 4, "app-id": "firefox", "title": "Mozilla", "type": "toplevel", "role": "toplevel", "mapped": true, "minimized": true, "last-focus-timestamp": 77});
        assert_eq!(Win::from_view(&v), Some(Win { id: 4, app_id: "firefox".into(), title: "Mozilla".into(), minimized: true, focused_at: 77 }));
        for (k, val) in [("type", json!("panel")), ("role", json!("desktop-environment")), ("mapped", json!(false)), ("app-id", json!("io.github.vinioliveiras.sonata2rs.dock"))] {
            let mut x = v.clone();
            x[k] = val;
            assert!(Win::from_view(&x).is_none(), "{k}");
        }
    }

    /// tests/test_mission_control.py: AltTabInMissionControlTest.
    #[test]
    fn test_switcher_steps_mission_control_when_open() {
        let ok = crate::tests::FakeIpc::answering(json!({"result": "ok"}));
        assert!(mission_control_step(&ok, false));
        assert!(mission_control_step(&ok, true));
        assert_eq!(ok.calls(), [("sonata/scale-cycle".to_string(), json!({"back": false})), ("sonata/scale-cycle".to_string(), json!({"back": true}))]);
        assert!(!mission_control_step(&crate::tests::FakeIpc::answering(json!({"error": "Mission Control is not open"})), false));
        assert!(!mission_control_step(&crate::tests::FakeIpc::answering(Value::Null), false));
    }
}
