//! The menu bar's content (topbar.py's Bar): the Sonata menu, the active
//! app and its menus on the left; tray icons, performance figures and the
//! menu extras (Now Playing, input source, Sound, Battery, Bluetooth,
//! Wi-Fi, Spotlight, Control Center, the clock) on the right; the
//! microphone / camera dot over its right margin.
//!
//! Wi-Fi, battery and volume are read by one poller for every display's
//! bar ([`crate::statuspoll`]: NetworkManager's, UPower's and `pactl
//! subscribe`'s events, polling as the fallback), Bluetooth by one BlueZ
//! watch. Not here (see seams.rs for what's passed in): the clipboard
//! history (its own crate); the logo is logo.rs's.

use crate::kit::{self, run_async};
use crate::logic;
use crate::seams::{Services, Toplevel};
use crate::statuspoll::StatusPoll;
use crate::{about, cc::ControlCenter, menubar_size, statsui, topbar_prefs};
use gtk::{gdk, gio, glib, graphene, prelude::*, subclass::prelude::*};
use serde_json::{json, Map, Value};
use sonata_backend::system;
use sonata_i18n::{fmt, tr};
use adw::prelude::AlertDialogExt;
use sonata_ui::menu::{self, Item};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::time::Duration;

/// The menu bar's panels (Control Center, Wi-Fi, clock...) open this far below it.
pub const PANEL_GAP: i32 = 2;
/// A menu closes before its Minimize / Hide happens.
pub const MENU_SETTLE_MS: u64 = 150;
/// Fixed content width of the menu bar's panels: a long network, device
/// or song name ellipsizes, never widens a panel.
pub const STATUS_W: i32 = 280;
/// The bold app name in the menu bar, then "…".
pub const APP_NAME_CHARS: i32 = 24;
/// The Sonata menu's logo (under the 16 px status icons; Vini's call).
pub const LOGO_PX: i32 = 12;
pub const POLL_S: u64 = 10;
/// Wi-Fi / battery as signals: a look now and then only.
pub const PUSHED_POLL_S: u64 = 120;
/// Keyboards plugged in / out: looked at this often (a thread).
pub const KEYBOARDS_S: u32 = 15;
/// The bar's right margin, where the in-use dot sits.
pub const INUSE_ROOM: i32 = 8;

pub fn bar_h() -> i32 {
    menubar_size::height()
}

pub fn item_h(bar_h: i32) -> i32 {
    if bar_h <= 24 { bar_h - 2 } else { bar_h - 6 }
}

thread_local! {
    static CSS: Cell<bool> = const { Cell::new(false) };
    static BARS: RefCell<Vec<Weak<Bar>>> = RefCell::default();
    static BT: RefCell<Option<Rc<Bluetooth>>> = RefCell::default();
    /// The microphone / camera watch, one for every display's bar.
    static INUSE: RefCell<Option<sonata_system::inuse::Watcher>> = RefCell::default();
}

/// topbar.py's and controlcenter.py's CSS, filled once per process.
pub fn register_css() {
    if CSS.with(|c| c.replace(true)) {
        return;
    }
    let h = bar_h();
    sonata_ui::css::register_module_with("shell/topbar.py", &[("bar_h", h.to_string()), ("item_h", item_h(h).to_string())]);
    sonata_ui::css::register_module("shell/controlcenter.py");
    // Edit Controls' jiggle and its x (ui/edit.py, Launchpad's look): never
    // registered in the menu bar's process, the modules stood still and the
    // x was a plain button
    sonata_ui::css::register_module(EDIT_CSS);
}

/// ui/edit.py's templates: Control Center's edit mode.
pub(crate) const EDIT_CSS: &str = "ui/edit.py";

pub(crate) fn bars() -> Vec<Rc<Bar>> {
    BARS.with(|b| b.borrow().iter().filter_map(Weak::upgrade).collect())
}

// -- the material: paints the bar, and slides it (intro, auto-hide) ------------------------

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct Material {
        pub backdrop: RefCell<Option<gdk::Texture>>,
        pub offset: Cell<f64>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Material {
        const NAME: &'static str = "SonataTopbarMaterial";
        type Type = super::Material;
        type ParentType = gtk::Widget;

        fn class_init(klass: &mut Self::Class) {
            klass.set_layout_manager_type::<gtk::BinLayout>();
        }
    }

    impl ObjectImpl for Material {
        fn dispose(&self) {
            while let Some(c) = self.obj().first_child() {
                c.unparent();
            }
        }
    }

    impl WidgetImpl for Material {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let w = self.obj();
            let off = self.offset.get();
            if off > 0.0 {
                snap.save();
                snap.translate(&graphene::Point::new(0.0, -off as f32));
            }
            let (width, height) = (w.width() as f32, w.height() as f32);
            if width > 0.0 && height > 0.0 {
                let rect = graphene::Rect::new(0.0, 0.0, width, height);
                if let Some(tex) = self.backdrop.borrow().as_ref() {
                    // preview: stands in for the compositor's blur (bar area only)
                    snap.push_clip(&rect);
                    snap.append_texture(tex, &graphene::Rect::new(0.0, 0.0, tex.width() as f32, tex.height() as f32));
                    snap.pop();
                }
                // Big Sur: translucent material, no bottom line
                snap.append_color(&sonata_ui::rgba("bar_material"), &rect);
            }
            self.parent_snapshot(snap);
            if off > 0.0 {
                snap.restore();
            }
        }
    }
}

glib::wrapper! {
    /// The bar painted with the menu bar material (ui/glass.py), its
    /// drawing moved up by `offset` while it slides (the surface stays:
    /// a surface moved off-screen gets no frames and would never come back).
    pub struct Material(ObjectSubclass<imp::Material>) @extends gtk::Widget;
}

impl Material {
    fn new(child: &impl IsA<gtk::Widget>) -> Self {
        let m: Self = glib::Object::new();
        child.set_parent(&m);
        m
    }

    pub fn set_offset(&self, v: f64) {
        self.imp().offset.set(v);
        self.queue_draw();
    }

    pub fn offset(&self) -> f64 {
        self.imp().offset.get()
    }

    pub fn set_backdrop(&self, t: Option<gdk::Texture>) {
        self.imp().backdrop.replace(t);
        self.queue_draw();
    }
}

// -- shared services ------------------------------------------------------------------------

/// Battery / power events (backend/power.py's watch): plugging in,
/// charging, level and Low Power Mode changes are pushed, so the icon
/// updates at once. False when the system bus isn't reachable (polled then).
pub(crate) fn power_watch() -> bool {
    let Ok(bus) = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) else { return false };
    let src: Rc<Cell<Option<glib::SourceId>>> = Rc::default();
    for (name, path) in [
        ("org.freedesktop.UPower", "/org/freedesktop/UPower"),
        ("org.freedesktop.UPower", "/org/freedesktop/UPower/devices/DisplayDevice"),
        ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
        ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"),
    ] {
        let src = src.clone();
        // (kept for the process: the bus holds the subscription)
        #[allow(deprecated)]
        bus.signal_subscribe(Some(name), Some("org.freedesktop.DBus.Properties"), Some("PropertiesChanged"), Some(path), None, gio::DBusSignalFlags::NONE, move |_, _, _, _, _, _| {
            if let Some(old) = src.take() {
                src.set(Some(old)); // a burst of property changes -> one update
                return;
            }
            let s = src.clone();
            src.set(Some(glib::timeout_add_local_once(Duration::from_millis(150), move || {
                s.set(None);
                if let Some(p) = StatusPoll::current() {
                    p.battery();
                }
            })));
        });
    }
    true
}

/// The BlueZ adapter, watched once for all bars (async proxy: a slow
/// bluetoothd never stalls the menu bar).
struct Bluetooth {
    proxy: RefCell<Option<gio::DBusProxy>>,
}

impl Bluetooth {
    fn shared() -> Rc<Bluetooth> {
        BT.with(|b| {
            b.borrow_mut()
                .get_or_insert_with(|| {
                    let me = Rc::new(Bluetooth { proxy: RefCell::default() });
                    let (w1, w2) = (Rc::downgrade(&me), Rc::downgrade(&me));
                    let id = gio::bus_watch_name(
                        gio::BusType::System,
                        "org.bluez",
                        gio::BusNameWatcherFlags::NONE,
                        move |conn, _, _| {
                            let w = w1.clone();
                            gio::DBusProxy::new(&conn, gio::DBusProxyFlags::NONE, None, Some("org.bluez"), "/org/bluez/hci0", "org.bluez.Adapter1", gio::Cancellable::NONE, move |res| {
                                let Some(me) = w.upgrade() else { return };
                                if let Ok(p) = &res {
                                    p.connect_local("g-properties-changed", false, |_| {
                                        Bluetooth::changed();
                                        None
                                    });
                                }
                                me.proxy.replace(res.ok());
                                Bluetooth::changed();
                            });
                        },
                        move |_, _| {
                            if let Some(me) = w2.upgrade() {
                                me.proxy.replace(None);
                            }
                            Bluetooth::changed();
                        },
                    );
                    std::mem::forget(id); // watched for the process
                    me
                })
                .clone()
        })
    }

    fn changed() {
        for b in bars() {
            b.bt_update();
        }
    }

    fn powered(&self) -> Option<bool> {
        self.proxy.borrow().as_ref().and_then(|p| p.cached_property("Powered")).and_then(|v| v.get::<bool>())
    }
}

type Builder = fn(&Rc<Bar>, &gtk::Button) -> Option<gtk::Popover>;

/// One display's menu bar.
pub struct Bar {
    pub root: Material,
    pub center: gtk::CenterBox,
    pub cfg: RefCell<Map<String, Value>>,
    pub services: Services,
    pub items: RefCell<Vec<gtk::Button>>,
    pub logo: gtk::Button,
    pub app_btn: gtk::Button,
    pub file_btn: gtk::Button,
    pub go_btn: gtk::Button,
    pub win_btn: gtk::Button,
    pub tray: sonata_tray::TrayBox,
    stats_box: gtk::Box,
    stat_items: RefCell<Vec<gtk::Button>>,
    stats_shown: RefCell<Option<Vec<(String, String)>>>,
    pub players: crate::mpris::Players,
    players_id: Cell<u32>,
    pub nowplaying: gtk::Button,
    /// The input source controls: (button, its keyboards' layout -- None:
    /// Input Sources --, those keyboards); the first is always there.
    pub inputs: RefCell<Vec<(gtk::Button, Option<String>, Vec<String>)>>,
    input_groups: RefCell<Option<Vec<(Option<String>, Vec<String>)>>>,
    pub sound: gtk::Button,
    pub battery: gtk::Button,
    pub battery_pct: gtk::Label,
    pub bt: gtk::Button,
    pub wifi: gtk::Button,
    pub spotlight: gtk::Button,
    pub cc: gtk::Button,
    pub clock: gtk::Button,
    pub inuse_btn: gtk::Button,
    mic_dot: gtk::Box,
    cam_dot: gtk::Box,
    pub alive: Cell<bool>,
    /// The main display's notifications (the clock opens Notification Center).
    pub notifications: RefCell<Option<Rc<sonata_notifications::Notifications>>>,
    /// The display this bar is on (Control Center's brightness follows it).
    pub monitor: RefCell<Option<gdk::Monitor>>,
    pub control_center: RefCell<Option<Rc<ControlCenter>>>,
    cc_panel: RefCell<Option<gtk::Popover>>,
    /// Menus open now: an auto-hidden bar stays while one is.
    pub open_menus: Cell<u32>,
    /// Auto-hide's hooks (window.rs): settings changed; a hidden bar comes
    /// down for its menu; a menu closed.
    pub on_autohide: RefCell<Option<Rc<dyn Fn()>>>,
    pub reveal: RefCell<Option<Rc<dyn Fn()>>>,
    pub on_menu_closed: RefCell<Option<Rc<dyn Fn()>>>,
    clock_day: Cell<(i32, i32)>,
    /// Most recently used apps (the app switcher's order).
    pub mru: RefCell<Vec<String>>,
    windows_id: Cell<Option<u64>>,
    icons_src: RefCell<Option<glib::SourceId>>,
    icons_sig: RefCell<Option<IconsSig>>,
    /// The window whose app the bar shows (its id), kept while the focus
    /// is on a surface of ours (keeps_front_app): the menus act on it.
    front: Cell<Option<i64>>,
    theme_src: RefCell<Option<glib::SourceId>>,
    cfg_mon: RefCell<Vec<gio::FileMonitor>>,
}

fn item(bx: &gtk::Box, text: Option<&str>, icon: Option<&str>, css: &str) -> gtk::Button {
    let b = gtk::Button::builder().css_classes(["topbar-item"]).can_focus(false).valign(gtk::Align::Center).build();
    if !css.is_empty() {
        b.add_css_class(css);
    }
    let content = gtk::Box::builder().valign(gtk::Align::Center).build();
    if let Some(i) = icon {
        content.append(&gtk::Image::from_icon_name(i));
    }
    if let Some(t) = text {
        content.append(&gtk::Label::new(Some(t)));
    }
    b.set_child(Some(&content));
    bx.append(&b);
    b
}

fn label_of(btn: &gtk::Button) -> Option<gtk::Label> {
    btn.child()?.last_child()?.downcast::<gtk::Label>().ok()
}

fn image_of(btn: &gtk::Button) -> Option<gtk::Image> {
    let mut c = btn.child()?.first_child();
    while let Some(w) = c {
        if let Ok(i) = w.clone().downcast::<gtk::Image>() {
            return Some(i);
        }
        c = w.next_sibling();
    }
    None
}

pub fn set_text(btn: &gtk::Button, text: &str) {
    if let Some(l) = label_of(btn) {
        l.set_label(text);
    }
}

pub fn text_of(btn: &gtk::Button) -> String {
    label_of(btn).map(|l| l.label().to_string()).unwrap_or_default()
}

pub fn icon_of(btn: &gtk::Button) -> Option<String> {
    image_of(btn).and_then(|i| i.icon_name()).map(|s| s.to_string())
}

fn set_icon(btn: &gtk::Button, name: &str) {
    if let Some(i) = image_of(btn) {
        if i.icon_name().as_deref() != Some(name) {
            i.set_icon_name(Some(name));
        }
    }
}

/// (the app's key, its windows) of the focused window, or, with none
/// focused, of the window `front` the bar still shows -- marked activated
/// so the menus act on it. (None, []) when neither.
pub fn shown_from(tops: &[Toplevel], front: Option<i64>) -> (Option<String>, Vec<Toplevel>) {
    let key_of = |t: &Toplevel| sonata_desktop::appmatch::match_app_id(&t.app_id).unwrap_or_else(|| t.app_id.clone());
    let act = tops.iter().find(|t| t.activated).or_else(|| front.and_then(|id| tops.iter().find(|t| t.id == id)));
    let Some(act) = act else { return (None, vec![]) };
    let (key, id) = (key_of(act), act.id);
    let wins = tops.iter().filter(|t| key_of(t) == key).cloned().map(|mut t| {
        t.activated = t.id == id;
        t
    }).collect();
    (Some(key), wins)
}

/// Whether the app name and menus stay as they are when no window is
/// focused. Wayfire's IPC reports none as soon as a surface of ours takes
/// the keyboard (a menu bar menu, and after it closes the focus may stay
/// nowhere), where the Python bar read wlr-foreign-toplevel, which keeps
/// the last window activated -- Vini: Chrome in front, a click on a menu
/// turned them into Files'. Files' only for the desktop itself, or when no
/// window is left to be in front.
/// `focus`: Wayfire's focused view ("type", "app-id"), None when nothing.
pub fn keeps_front_app(focused: Option<&str>, menus_open: u32, focus: Option<&serde_json::Value>, windows_shown: usize) -> bool {
    if focused.is_some() {
        return false; // a window in front: follow it
    }
    if menus_open > 0 {
        return true;
    }
    match focus {
        Some(v) => {
            let app = v.get("app-id").and_then(|a| a.as_str()).unwrap_or("");
            let kind = v.get("type").and_then(|t| t.as_str()).unwrap_or("");
            // the desktop (the wallpaper's layer) has it: Files'
            !(kind == "background" || app.contains("sonata2-wallpaper")) && windows_shown > 0
        }
        None => windows_shown > 0,
    }
}

/// What redraws the icon items when it changes (icons_soon): the window in
/// front, how many there are, how many minimized and maximized. Vini: Wi-Fi,
/// Search and Control Center went dark after minimizing or maximizing an
/// app, or switching windows -- none of which changed the front app or the
/// count, the only two looked at.
pub type IconsSig = (Option<i64>, usize, usize, usize);

pub fn icons_signature(tops: &[crate::seams::Toplevel]) -> IconsSig {
    (tops.iter().find(|t| t.activated).map(|t| t.id), tops.len(), tops.iter().filter(|t| t.minimized).count(), tops.iter().filter(|t| t.maximized).count())
}

/// Look the item's icon up again and draw it anew. Vini: the Control
/// Center icon vanished after its panel and an app window came and went,
/// and only came back when clicked -- a stale picture of it was kept.
fn refresh_icon(btn: &gtk::Button) {
    if let Some(i) = image_of(btn) {
        if let Some(n) = i.icon_name() {
            i.set_icon_name(None);
            i.set_icon_name(Some(&n));
        }
    }
}

/// An appearance change has settled this long after its last frame.
pub const THEME_SETTLE_MS: u64 = 150;

/// Every widget under `root` drawn anew, every named icon looked up again
/// and the logo's picture made again (ui/theme.py's refresh_tree). GTK
/// keeps a widget's last picture until that widget itself asks to redraw.
pub fn refresh_tree(root: &gtk::Widget) {
    let mut stack = vec![root.clone()];
    while let Some(w) = stack.pop() {
        if let Some(img) = w.downcast_ref::<gtk::Image>() {
            if img.storage_type() == gtk::ImageType::IconName {
                if let Some(n) = img.icon_name() {
                    img.set_icon_name(None); // (GTK keeps the picture for the same name)
                    img.set_icon_name(Some(&n));
                }
            }
        } else if let Some(g) = w.downcast_ref::<crate::logo::LogoGlyph>() {
            g.refresh(true);
        }
        w.queue_draw();
        let mut c = w.first_child();
        while let Some(x) = c {
            c = x.next_sibling();
            stack.push(x);
        }
    }
}

/// Once the menu has gone: its closing hands the keyboard back to the
/// window, which must not undo a Minimize / Hide.
fn after_menu(f: impl FnOnce() + 'static) {
    glib::timeout_add_local_once(Duration::from_millis(MENU_SETTLE_MS), f);
}

fn menu_at(btn: &gtk::Button, sections: Vec<Vec<Item>>) -> Option<gtk::Popover> {
    let pop = menu::popup(btn, sections, menu::Options::below());
    pop.set_offset(0, PANEL_GAP);
    kit::align_to_start(pop.upcast_ref(), btn.upcast_ref(), 2);
    Some(pop.upcast())
}

/// A status item's panel (Wi-Fi, Bluetooth, battery...), centred under its
/// icon as on macOS (Vini, after trying the left edge: better centred) --
/// GTK's own placement, there from the first frame.
fn status_panel(btn: &gtk::Button, col: &gtk::Box) -> gtk::Popover {
    kit::panel(btn, col, PANEL_GAP, Some(STATUS_W), STATUS_ALIGN_START, false)
}

/// Status panels centred under their icon (Vini; the app menus hang from
/// their left edge).
pub(crate) const STATUS_ALIGN_START: bool = false;

impl Bar {
    pub fn new(services: Services) -> Rc<Bar> {
        register_css();
        let cfg = topbar_prefs::load();
        let flag = |k: &str| cfg.get(k).and_then(Value::as_bool).unwrap_or(true);
        let left = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        let logo = item(&left, None, None, "icon");
        // the distro logo, a shape, a symbol or words (Settings > Appearance)
        let glyph = crate::logo::LogoGlyph::new(LOGO_PX);
        if let Some(c) = logo.child().and_downcast::<gtk::Box>() {
            c.append(&glyph);
        }
        let app_btn = item(&left, Some(&tr!("Files")), None, "app");
        // a very long app name ("GNU Image Manipulation Program", a raw app id)
        // ellipsizes: with tray and stats on a 1280 px display the bar overflowed
        if let Some(l) = label_of(&app_btn) {
            l.set_ellipsize(gtk::pango::EllipsizeMode::End);
            l.set_max_width_chars(APP_NAME_CHARS);
        }
        // on the desktop (nothing focused) the menus are Files' own, like Finder's
        let file_btn = item(&left, Some(&tr!("File")), None, "");
        let go_btn = item(&left, Some(&tr!("Go")), None, "");
        let win_btn = item(&left, Some(&tr!("Window")), None, "");
        left.set_margin_start(8); // no CSS padding: the bar is painted over the whole allocation

        let right = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        // background apps' status icons (StatusNotifierItem), left of the extras
        let tray = sonata_tray::TrayBox::new(flag("show_tray"));
        right.append(tray.widget());
        // CPU, GPU, memory, network, FPS (Settings > Menu Bar; off by default)
        let stats_box = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        right.append(&stats_box);
        let players = crate::mpris::players();
        let nowplaying = item(&right, None, Some("sonata-now-playing-symbolic"), "icon");
        let input_btn = item(&right, Some(""), None, "");
        input_btn.add_css_class("input-src");
        let sound = item(&right, None, Some("sonata-volume-3-symbolic"), "icon");
        let battery = item(&right, None, Some("sonata-battery-100-symbolic"), "icon");
        battery.add_css_class("battery");
        let battery_pct = gtk::Label::builder().css_classes(["percent"]).build();
        if let Some(c) = battery.child().and_downcast::<gtk::Box>() {
            c.prepend(&battery_pct); // "87% [battery]" like Big Sur
        }
        let bt = item(&right, None, Some("sonata-bluetooth-symbolic"), "icon");
        bt.set_visible(false);
        let wifi = item(&right, None, Some("sonata-wifi-3-symbolic"), "icon");
        let spotlight = item(&right, None, Some("sonata-search-symbolic"), "icon");
        spotlight.set_visible(flag("show_spotlight"));
        let cc = item(&right, None, Some("sonata-control-center-symbolic"), "icon");
        let clock = item(&right, Some(""), None, "clock");
        right.set_margin_end(INUSE_ROOM);
        // microphone / camera in use (macOS Tahoe: the orange or green dot
        // right of the date). Over the menu bar's right margin, never in the
        // row: it comes and goes without moving a single item (Vini)
        let inuse_btn = gtk::Button::builder().css_classes(["inuse-dots"]).can_focus(false).halign(gtk::Align::End).valign(gtk::Align::Center).visible(false).build();
        let dots = gtk::Box::builder().valign(gtk::Align::Center).build();
        let mic_dot = gtk::Box::builder().css_classes(["inuse-dot", "mic"]).valign(gtk::Align::Center).build();
        let cam_dot = gtk::Box::builder().css_classes(["inuse-dot", "cam"]).valign(gtk::Align::Center).build();
        dots.append(&cam_dot);
        dots.append(&mic_dot);
        inuse_btn.set_child(Some(&dots));
        let end = gtk::Overlay::builder().child(&right).build();
        end.add_overlay(&inuse_btn); // (an overlay: not measured, the row keeps its size)

        let center = gtk::CenterBox::builder().css_classes(["topbar"]).hexpand(true).build();
        center.set_start_widget(Some(&left));
        center.set_end_widget(Some(&end));
        let root = Material::new(&center);
        root.set_hexpand(true);

        let items = vec![logo.clone(), app_btn.clone(), file_btn.clone(), go_btn.clone(), win_btn.clone(), nowplaying.clone(), input_btn.clone(), sound.clone(), battery.clone(), bt.clone(), wifi.clone(), spotlight.clone(), cc.clone(), clock.clone()];
        let bar = Rc::new(Bar {
            root,
            center,
            cfg: RefCell::new(cfg),
            services,
            items: RefCell::new(items),
            logo,
            app_btn,
            file_btn,
            go_btn,
            win_btn,
            tray,
            stats_box,
            stat_items: RefCell::default(),
            stats_shown: RefCell::default(),
            players,
            players_id: Cell::new(0),
            nowplaying,
            inputs: RefCell::new(vec![(input_btn, None, Vec::new())]),
            input_groups: RefCell::default(),
            sound,
            battery,
            battery_pct,
            bt,
            wifi,
            spotlight,
            cc,
            clock,
            inuse_btn,
            mic_dot,
            cam_dot,
            alive: Cell::new(true),
            notifications: RefCell::default(),
            monitor: RefCell::default(),
            control_center: RefCell::default(),
            cc_panel: RefCell::default(),
            open_menus: Cell::new(0),
            on_autohide: RefCell::default(),
            reveal: RefCell::default(),
            on_menu_closed: RefCell::default(),
            clock_day: Cell::new((0, 0)),
            mru: RefCell::default(),
            windows_id: Cell::new(None),
            icons_src: RefCell::default(),
            icons_sig: RefCell::default(),
            front: Cell::new(None),
            theme_src: RefCell::default(),
            cfg_mon: RefCell::default(),
        });
        bar.wire(&end);
        BARS.with(|b| b.borrow_mut().push(Rc::downgrade(&bar)));
        bar.build_stats();
        bar.update_input();
        bar.extras_visibility();
        bar.tick_clock();
        bar.active_changed();
        bar.bt_update();
        bar.poll();
        bar.watch_inuse();
        bar
    }

    /// The mic / camera dots follow backend/inuse.py's watcher (one for
    /// the process, made by the first bar). Vini's Rust session: the dot
    /// never showed -- set_inuse was there, nothing called it.
    fn watch_inuse(self: &Rc<Self>) {
        let w = INUSE.with(|i| {
            i.borrow_mut()
                .get_or_insert_with(|| {
                    let paused = self.services.quiet_paused.clone();
                    let w = sonata_system::inuse::Watcher::new(move || paused.as_ref().map_or_else(crate::quiet::paused, |p| p()));
                    connect_inuse(&w);
                    w
                })
                .clone()
        });
        let st = w.state();
        self.set_inuse(&st.camera, &st.mic);
    }

    fn connect_item(self: &Rc<Self>, btn: &gtk::Button, build: Builder) {
        let w = Rc::downgrade(self);
        btn.connect_clicked(move |b| {
            if let Some(bar) = w.upgrade() {
                bar.open(b, build);
            }
        });
    }

    fn wire(self: &Rc<Self>, end: &gtk::Overlay) {
        self.connect_item(&self.logo.clone(), |b, btn| b.sonata_menu(btn));
        self.connect_item(&self.app_btn.clone(), |b, btn| b.app_menu(btn));
        self.connect_item(&self.file_btn.clone(), |b, btn| b.file_menu(btn));
        self.connect_item(&self.go_btn.clone(), |b, btn| b.files_go_menu(btn));
        self.connect_item(&self.win_btn.clone(), |b, btn| b.window_menu(btn));
        self.connect_item(&self.nowplaying.clone(), |b, btn| b.nowplaying_panel(btn));
        let input = self.inputs.borrow()[0].0.clone();
        self.connect_item(&input, |b, btn| b.input_panel(btn));
        self.connect_item(&self.sound.clone(), |b, btn| b.sound_panel(btn));
        self.connect_item(&self.battery.clone(), |b, btn| b.battery_panel(btn));
        self.connect_item(&self.bt.clone(), |b, btn| b.bluetooth_panel(btn));
        self.connect_item(&self.wifi.clone(), |b, btn| b.wifi_panel(btn));
        self.connect_item(&self.spotlight.clone(), |_, btn| {
            btn.remove_css_class("open");
            kit::spawn_self(&["spotlight"]); // Big Sur's magnifier
            None
        });
        self.connect_item(&self.cc.clone(), |b, btn| b.control_center_panel(btn));
        self.connect_item(&self.clock.clone(), |b, btn| b.calendar(btn));
        let w = Rc::downgrade(self);
        self.inuse_btn.connect_clicked(move |_| {
            if let Some(b) = w.upgrade() {
                let cc = b.cc.clone();
                b.open(&cc, |b, btn| b.control_center_panel(btn));
            }
        });
        // an auto-hidden bar stays down while a tray icon's menu is open
        let w = Rc::downgrade(self);
        self.tray.connect_menu(move |open| {
            let Some(b) = w.upgrade() else { return };
            if open {
                b.open_menus.set(b.open_menus.get() + 1);
            } else {
                b.open_menus.set(b.open_menus.get().saturating_sub(1));
                if let Some(f) = b.on_menu_closed.borrow().clone() {
                    f();
                }
            }
        });
        let w = Rc::downgrade(self);
        end.connect_get_child_position(move |ov, child| w.upgrade().and_then(|b| b.inuse_place(ov, child)));
        let w = Rc::downgrade(self);
        if let Some(l) = label_of(&self.clock) {
            l.connect_map(move |_| {
                if let Some(b) = w.upgrade() {
                    b.clock_room();
                }
            });
        }
        let w = Rc::downgrade(self);
        self.players_id.set(self.players.connect(move || {
            if let Some(b) = w.upgrade() {
                b.extras_visibility();
            }
        }));
        let w = Rc::downgrade(self);
        // the bar's material blends with the appearance; every item looked up
        // again once it settled (an icon kept its Light Mode look in Dark Mode)
        sonata_ui::on_change(move || {
            if let Some(b) = w.upgrade().filter(|b| b.alive.get()) {
                b.root.queue_draw(); // the material (blends during a cross-fade)
                b.theme_settled();
            }
        });
        let w = Rc::downgrade(self);
        self.cfg_mon.replace(sonata_config::watch("topbar", move || {
            if let Some(b) = w.upgrade() {
                b.config_changed();
            }
        }));
        if let Some(win) = self.services.windows.clone() {
            let w = Rc::downgrade(self);
            self.windows_id.set(Some(win.add_listener(Rc::new(move || {
                if let Some(b) = w.upgrade() {
                    b.active_changed();
                }
            }))));
        }
        let w = Rc::downgrade(self);
        glib::timeout_add_seconds_local(KEYBOARDS_S, move || match w.upgrade() {
            Some(b) if b.alive.get() => {
                b.read_keyboards();
                glib::ControlFlow::Continue
            }
            _ => glib::ControlFlow::Break,
        });
    }

    /// Its display was unplugged: no more polling or listening (the shared
    /// watchers stay for the other bars).
    pub fn stop(&self) {
        self.alive.set(false);
        self.tray.stop();
        BARS.with(|b| b.borrow_mut().retain(|x| x.upgrade().is_some_and(|x| !std::ptr::eq(&*x, self))));
        self.players.disconnect(self.players_id.get());
        if let (Some(win), Some(id)) = (self.services.windows.as_ref(), self.windows_id.take()) {
            win.remove_listener(id);
        }
        for m in self.cfg_mon.take() {
            m.cancel();
        }
    }

    pub fn cfg_bool(&self, key: &str, default: bool) -> bool {
        self.cfg.borrow().get(key).and_then(Value::as_bool).unwrap_or(default)
    }

    fn cfg_str(&self, key: &str, default: &str) -> String {
        self.cfg.borrow().get(key).and_then(Value::as_str).unwrap_or(default).to_string()
    }

    /// The camera / microphone in use now (backend/inuse.py's Watcher, a
    /// seam: whoever watches calls this).
    pub fn set_inuse(self: &Rc<Self>, camera: &[String], mic: &[String]) {
        let (cam, mic_on, tip) = logic::inuse(camera, mic);
        self.cam_dot.set_visible(cam);
        self.mic_dot.set_visible(mic_on);
        self.inuse_btn.set_visible(cam || mic_on);
        self.inuse_btn.set_tooltip_text(if tip.is_empty() { None } else { Some(&tip) });
        self.icons_soon();
    }

    /// The dot halfway between the end of the date's text and the screen's
    /// edge (Vini), centred on the bar's height.
    fn inuse_place(&self, ov: &gtk::Overlay, child: &gtk::Widget) -> Option<gdk::Rectangle> {
        if child != self.inuse_btn.upcast_ref::<gtk::Widget>() {
            return None;
        }
        let (_, nat) = child.preferred_size();
        let (w, h) = (nat.width(), nat.height());
        let end = label_of(&self.clock).and_then(|l| l.compute_bounds(ov)).map(|b| (b.x() + b.width()) as f64).unwrap_or((ov.width() - INUSE_ROOM) as f64);
        let x = (end + (ov.width() as f64 - end - w as f64) / 2.0).round() as i32;
        Some(gdk::Rectangle::new(x, (ov.height() - h) / 2, w, h))
    }

    fn extras_visibility(&self) {
        self.nowplaying.set_visible(self.cfg_bool("show_now_playing", false) && self.players.active());
        self.sound.set_visible(self.cfg_bool("show_sound", false));
    }

    /// Settings changed topbar.json: apply live.
    fn config_changed(self: &Rc<Self>) {
        self.cfg.replace(topbar_prefs::load());
        self.battery_pct.set_visible(self.cfg_bool("battery_percent", false) && self.battery.get_visible());
        self.bt_update();
        self.extras_visibility();
        self.tray.set_shown(self.cfg_bool("show_tray", true));
        self.spotlight.set_visible(self.cfg_bool("show_spotlight", true));
        self.build_stats();
        self.update_input();
        self.poll(); // Wi-Fi / battery shown or not, at once
        if let Some(f) = self.on_autohide.borrow().clone() {
            f();
        }
        set_text(&self.clock, &logic::clock_text(&now(), &self.cfg_str("clock_format", "%a %H:%M")));
        self.clock_room();
    }

    /// The clock keeps one width all day (Vini: no nudge when the minute
    /// changes, and no gap for dates wider than today's): as wide as its
    /// widest text today in this format, the text against the right edge.
    fn clock_room(&self) {
        let Some(lbl) = label_of(&self.clock) else { return };
        let t = now();
        self.clock_day.set((t.year(), t.day_of_year()));
        let shown = lbl.label();
        lbl.set_size_request(-1, -1);
        let mut best = 0;
        for text in logic::clock_texts(&t, &self.cfg_str("clock_format", "%a %H:%M")) {
            lbl.set_label(&text);
            best = best.max(lbl.measure(gtk::Orientation::Horizontal, -1).1);
        }
        lbl.set_label(&shown);
        lbl.set_size_request(best, -1);
        lbl.set_xalign(1.0);
    }

    fn tick_clock(self: &Rc<Self>) {
        let t = now();
        set_text(&self.clock, &logic::clock_text(&t, &self.cfg_str("clock_format", "%a %H:%M")));
        let day = self.clock_day.get();
        if day != (0, 0) && day != (t.year(), t.day_of_year()) {
            self.clock_room(); // a new day: its own width
        }
        if self.alive.get() {
            let w = Rc::downgrade(self);
            glib::timeout_add_seconds_local_once((60 - t.second()).max(1) as u32, move || {
                if let Some(b) = w.upgrade() {
                    b.tick_clock();
                }
            });
        }
    }

    /// The performance items the settings ask for, in a fixed order.
    fn build_stats(self: &Rc<Self>) {
        let want: Vec<(String, String)> = statsui::kinds().iter().filter(|k| self.cfg_bool(&format!("show_{k}"), false)).map(|k| (k.clone(), self.cfg_str(&format!("{k}_style"), "text"))).collect();
        if self.stats_shown.borrow().as_ref() == Some(&want) {
            return;
        }
        self.stats_shown.replace(Some(want.clone()));
        for b in self.stat_items.take() {
            self.stats_box.remove(&b);
            self.items.borrow_mut().retain(|x| *x != b);
        }
        for (kind, style) in want {
            let b = item(&self.stats_box, None, None, "stat");
            b.set_child(Some(&statsui::menu_item(&kind, &style)));
            b.set_tooltip_text(Some(&statsui::title(&kind)));
            // a performance item: Task Manager (no menu of its own)
            b.connect_clicked(|_| kit::spawn_self(&["activity"]));
            self.items.borrow_mut().push(b.clone());
            self.stat_items.borrow_mut().push(b);
        }
    }

    fn open(self: &Rc<Self>, btn: &gtk::Button, build: Builder) {
        if let Some(r) = self.reveal.borrow().clone() {
            r(); // a hidden bar comes down for its menu
        }
        btn.add_css_class("open");
        let Some(pop) = build(self, btn) else {
            btn.remove_css_class("open");
            return;
        };
        self.open_menus.set(self.open_menus.get() + 1);
        let (w, b) = (Rc::downgrade(self), btn.clone());
        let handler: Rc<RefCell<Option<glib::SignalHandlerId>>> = Rc::default();
        let h = handler.clone();
        *handler.borrow_mut() = Some(pop.connect_closed(move |p| {
            if let Some(id) = h.take() {
                p.disconnect(id); // (once: Control Center's panel opens again)
            }
            b.remove_css_class("open");
            refresh_icon(&b); // never left without its icon (Vini)
            if let Some(bar) = w.upgrade() {
                if b == bar.logo {
                    bar.refresh_logo(true);
                }
                bar.open_menus.set(bar.open_menus.get().saturating_sub(1));
                // the other items too: closing the Sound menu turned the
                // Control Center icon dark in Light Mode (Vini)
                bar.icons_soon();
                if let Some(f) = bar.on_menu_closed.borrow().clone() {
                    f();
                }
            }
        }));
    }

    /// Open the N-th item (screenshots: --menu N).
    pub fn open_menu(&self, index: usize) {
        let b = self.items.borrow().get(index).cloned();
        if let Some(b) = b {
            b.emit_clicked();
        }
    }

    /// The icon items drawn anew once things settle. Vini: the Control
    /// Center icon went blank after closing Chrome and came back only when
    /// clicked -- GTK kept a stale picture of it.
    fn icons_soon(self: &Rc<Self>) {
        if self.icons_src.borrow().is_some() {
            return;
        }
        let w = Rc::downgrade(self);
        self.icons_src.replace(Some(glib::timeout_add_local_once(Duration::from_millis(120), move || {
            let Some(b) = w.upgrade() else { return };
            b.icons_src.replace(None);
            if b.alive.get() {
                for i in [&b.cc, &b.wifi, &b.spotlight] {
                    refresh_icon(i);
                }
                b.refresh_logo(false); // (focus changes are frequent: a redraw only)
            }
        })));
    }

    /// Once an appearance change has settled, every item drawn anew and
    /// every named icon looked up again (ui.theme.settled + refresh_tree):
    /// Vini, an icon kept its Light Mode look in Dark Mode until clicked --
    /// only three of them (and not the logo, the tray, the battery) were.
    fn theme_settled(self: &Rc<Self>) {
        if let Some(s) = self.theme_src.take() {
            sonata_ui::cancel(s); // (on_change comes on every frame of a cross-fade)
        }
        let w = Rc::downgrade(self);
        self.theme_src.replace(Some(glib::timeout_add_local_once(Duration::from_millis(THEME_SETTLE_MS), move || {
            if let Some(b) = w.upgrade() {
                b.theme_src.replace(None);
                if b.alive.get() {
                    refresh_tree(b.root.upcast_ref());
                }
            }
        })));
    }

    /// The Sonata menu's logo.
    pub fn logo_glyph(&self) -> Option<crate::logo::LogoGlyph> {
        self.logo.child()?.last_child()?.downcast().ok()
    }

    /// The Sonata menu's logo drawn anew (Vini: it went blank); reload: its
    /// picture made again too (not on every focus change).
    fn refresh_logo(&self, reload: bool) {
        if let Some(g) = self.logo_glyph() {
            g.refresh(reload);
        }
    }

    // -- the active app ---------------------------------------------------------------------
    /// (the focused app's key, its windows); (None, []) on the desktop.
    pub fn active(&self) -> (Option<String>, Vec<Toplevel>) {
        let Some(win) = self.services.windows.as_ref() else { return (None, vec![]) };
        let tops = win.toplevels();
        let Some(act) = tops.iter().find(|t| t.activated) else { return (None, vec![]) };
        let key_of = |t: &Toplevel| sonata_desktop::appmatch::match_app_id(&t.app_id).unwrap_or_else(|| t.app_id.clone());
        let key = key_of(act);
        let wins = tops.iter().filter(|t| key_of(t) == key).cloned().collect();
        (Some(key), wins)
    }

    /// The app the bar shows and its windows, the one in front marked
    /// activated -- what the app, File and Window menus act on. Asking only
    /// for the focused window, a menu opened from the bar found none (the
    /// bar has the focus) and acted on Files: Vini, About in Chrome's menu
    /// showed Files' About.
    pub fn shown(&self) -> (Option<String>, Vec<Toplevel>) {
        let tops = self.services.windows.as_ref().map(|w| w.toplevels()).unwrap_or_default();
        shown_from(&tops, self.front.get())
    }

    /// App name + menus follow the focused app; on the desktop (nothing
    /// focused) they are Files' -- File, Go, Window -- like Finder's.
    fn active_changed(self: &Rc<Self>) {
        let tops = self.services.windows.as_ref().map(|w| w.toplevels()).unwrap_or_default();
        let sig = icons_signature(&tops);
        // the front window, the number of windows or their minimized /
        // maximized states changed -- not a title (a browser's changes many
        // times a second)
        if self.icons_sig.borrow().as_ref() != Some(&sig) {
            self.icons_sig.replace(Some(sig));
            self.icons_soon();
        }
        let (key, wins) = self.active();
        let focus = if key.is_none() { sonata_backend::wfipc::call("window-rules/get-focused-view", serde_json::json!({})).and_then(|v| v.get("info").cloned()).filter(|v| !v.is_null()) } else { None };
        let shown = tops.iter().filter(|t| !t.minimized).count();
        if keeps_front_app(key.as_deref(), self.open_menus.get(), focus.as_ref(), shown) {
            return; // a menu of ours took the focus: the app in front is still Chrome's...
        }
        self.front.set(tops.iter().find(|t| t.activated).map(|t| t.id));
        self.go_btn.set_visible(key.is_none());
        match key {
            Some(key) => {
                let mut mru = self.mru.borrow_mut();
                mru.retain(|k| *k != key);
                mru.insert(0, key.clone());
                drop(mru);
                let name = match sonata_ui::apps::lookup(&key) {
                    Some(info) => sonata_ui::apps::display_name(&info),
                    None => {
                        // no desktop entry: named as in the Dock (windowapps)
                        let titles: Vec<(&str, bool)> = wins.iter().map(|t| (t.title.as_str(), t.activated)).collect();
                        sonata_desktop::windowapps::describe(&key, &sonata_desktop::windowapps::window_title(&titles)).0
                    }
                };
                set_text(&self.app_btn, &name);
            }
            None => set_text(&self.app_btn, &tr!("Files")),
        }
    }

    // -- the Sonata menu ----------------------------------------------------------------------
    fn sonata_menu(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let user = glib::real_name().to_string_lossy().into_owned();
        let user = if user.is_empty() || user == "Unknown" { glib::user_name().to_string_lossy().into_owned() } else { user };
        let (c1, c2, c3) = (Rc::downgrade(self), Rc::downgrade(self), Rc::downgrade(self));
        let confirm = |w: Weak<Bar>, kind: &'static str| move || {
            if let Some(b) = w.upgrade() {
                b.confirm(kind);
            }
        };
        menu_at(btn, vec![
            vec![Item::new(&tr!("About This Computer"), || about::AboutWindow::open())],
            vec![Item::new(&tr!("Settings…"), || kit::open_settings(""))],
            vec![Item::submenu(&tr!("Recent Items"), recent_items(&self.services))],
            // reload the shell; apps stay open
            vec![Item::new(&tr!("Restart Sonata"), || system::restart_sonata(&[]))],
            vec![Item::new(&tr!("Sleep"), || kit::in_thread(|| system::power_action("sleep"))), Item::new(&tr!("Restart…"), confirm(c1, "restart")), Item::new(&tr!("Shut Down…"), confirm(c2, "shutdown"))],
            vec![Item::new(&tr!("Lock Screen"), || kit::in_thread(|| system::power_action("lock"))), Item::new(&fmt(&tr!("Log Out {user}…"), &[("user", &user)]), confirm(c3, "logout"))],
        ])
    }

    fn confirm(self: &Rc<Self>, kind: &'static str) {
        let (text, verb) = match kind {
            "restart" => (tr!("Are you sure you want to restart your computer now?"), tr!("Restart")),
            "shutdown" => (tr!("Are you sure you want to shut down your computer now?"), tr!("Shut Down")),
            _ => (tr!("Are you sure you want to quit all applications and log out now?"), tr!("Log Out")),
        };
        let end = self.services.end_session.clone();
        // macOS: "Reopen windows when logging back in" -- the apps open now
        // come back at the next login (Vini), ticked as last time
        sonata_ui::dialog::alert_check(&text, "", &[("cancel", tr!("Cancel"), ""), (kind, verb, "default")], &tr!("Reopen windows when logging back in"), sonata_desktop::open_apps::reopen_wanted(), move |r, reopen| {
            sonata_desktop::open_apps::set_reopen_wanted(reopen); // the box as left, next time (macOS)
            if r == kind {
                // the apps quit first (Chrome kept asking to restore its tabs)
                match &end {
                    Some(f) => f(kind, reopen),
                    None => kit::in_thread(move || system::power_action(kind)),
                }
            }
        }, None);
    }

    // -- the app's menus ----------------------------------------------------------------------
    fn open_folder(&self, uri: &str) {
        match &self.services.open_folder {
            Some(f) => f(uri),
            None => {
                let l = kit::python_launcher();
                sonata_backend::run::spawn(&[&l, "files", uri]);
            }
        }
    }

    fn app_menu(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let (key, wins) = self.shown();
        let info = sonata_ui::apps::lookup(key.as_deref().unwrap_or("io.github.vinioliveiras.sonata2.files")); // desktop: Files, like Finder
        let name = info.as_ref().map(sonata_ui::apps::display_name).or(key.clone()).unwrap_or_else(|| tr!("Files"));
        let win = self.services.windows.clone();
        let all = win.as_ref().map(|w| w.toplevels()).unwrap_or_default();
        let others: Vec<Toplevel> = all.iter().filter(|t| !wins.iter().any(|x| x.id == t.id)).cloned().collect();
        let (w1, w2, w3, w4) = (win.clone(), win.clone(), win.clone(), self.services.quit_app.clone());
        let (hide, hide_others, show, quit) = (wins.clone(), others.clone(), all.clone(), wins.clone());
        let (info2, name2, key2) = (info.clone(), name.clone(), key.clone().unwrap_or_default());
        menu_at(btn, vec![
            vec![Item::new(&fmt(&tr!("About {app}"), &[("app", &name)]), move || about::about_app(info2.as_ref(), &name2)).enabled(info.is_some())],
            vec![
                Item::new(&fmt(&tr!("Hide {app}"), &[("app", &name)]), move || {
                    let (w, h) = (w1.clone(), hide.clone());
                    after_menu(move || h.iter().for_each(|t| w.iter().for_each(|w| w.minimize(t))));
                })
                .enabled(!wins.is_empty()),
                Item::new(&tr!("Hide Others"), move || {
                    let (w, h) = (w2.clone(), hide_others.clone());
                    after_menu(move || h.iter().for_each(|t| w.iter().for_each(|w| w.minimize(t))));
                })
                .enabled(!others.is_empty()),
                Item::new(&tr!("Show All"), move || show.iter().for_each(|t| w3.iter().for_each(|w| w.unminimize(t)))).enabled(win.is_some()),
            ],
            vec![Item::new(&fmt(&tr!("Quit {app}"), &[("app", &name)]), move || match &w4 {
                Some(f) => f(&quit, &key2),
                None => quit.iter().for_each(|t| win.iter().for_each(|w| w.close(t))),
            })
            .enabled(!wins.is_empty())],
        ])
    }

    /// File: the app's windows (apptabs: the app's own shortcuts); on the desktop, Files' own.
    fn file_menu(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let (key, wins) = self.shown();
        let Some(key) = key else { return self.files_file_menu(btn) };
        let act = wins.iter().find(|t| t.activated).cloned();
        let app_id = act.as_ref().map_or(key, |t| t.app_id.clone());
        let tabs = self.services.apptabs.clone();
        let has_new = tabs.as_ref().is_some_and(|t| t.has(&app_id, "new_window"));
        let win = self.services.windows.clone();
        let close = act.clone();
        menu_at(btn, vec![
            vec![Item::new(&tr!("New Window"), move || {
                let (t, a) = (tabs.clone(), app_id.clone());
                after_menu(move || t.iter().for_each(|t| t.press(&a, "new_window")));
            })
            .enabled(has_new)],
            vec![Item::new(&tr!("Close Window"), move || close.iter().for_each(|t| win.iter().for_each(|w| w.close(t)))).enabled(act.is_some())],
        ])
    }

    fn files_file_menu(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let home = gio::File::for_path(glib::home_dir()).uri().to_string();
        let desktop = sonata_backend::userdirs::special(glib::UserDirectory::Desktop, false).map(|p| gio::File::for_path(p).uri().to_string());
        let (b1, b2, b3) = (Rc::downgrade(self), Rc::downgrade(self), Rc::downgrade(self));
        let new_folder = self.services.new_desktop_folder.clone();
        let has_new = new_folder.is_some();
        menu_at(btn, vec![
            vec![
                Item::new(&tr!("New Files Window"), move || b1.upgrade().iter().for_each(|b| b.open_folder(&home))),
                Item::new(&tr!("New Folder"), move || new_folder.iter().for_each(|f| f())).enabled(has_new),
            ],
            vec![Item::new(&tr!("Open Desktop in Files"), move || {
                if let (Some(b), Some(d)) = (b2.upgrade(), desktop.as_ref()) {
                    b.open_folder(d);
                }
            })],
            vec![Item::new(&tr!("Empty Trash…"), move || b3.upgrade().iter().for_each(|b| b.open_folder("trash:///")))],
        ])
    }

    fn files_go_menu(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let place = |d: glib::UserDirectory| sonata_backend::userdirs::special(d, false).map(|p| gio::File::for_path(p).uri().to_string());
        let home = Some(gio::File::for_path(glib::home_dir()).uri().to_string());
        let rows = [
            (tr!("Documents"), place(glib::UserDirectory::Documents)),
            (tr!("Desktop"), place(glib::UserDirectory::Desktop)),
            (tr!("Downloads"), place(glib::UserDirectory::Downloads)),
            (tr!("Home"), home),
            (tr!("Pictures"), place(glib::UserDirectory::Pictures)),
            (tr!("Music"), place(glib::UserDirectory::Music)),
        ];
        let go = |w: Weak<Bar>, uri: String| move || w.upgrade().iter().for_each(|b| b.open_folder(&uri));
        let places: Vec<Item> = rows.into_iter().map(|(name, uri)| Item::new(&name, go(Rc::downgrade(self), uri.clone().unwrap_or_default())).enabled(uri.is_some())).collect();
        menu_at(btn, vec![
            vec![Item::new(&tr!("Recents"), go(Rc::downgrade(self), "sonata:recents".into()))],
            places,
            vec![Item::new(&tr!("Computer"), go(Rc::downgrade(self), "file:///".into())), Item::new(&tr!("Trash"), go(Rc::downgrade(self), "trash:///".into()))],
            vec![Item::new(&tr!("Connect to Server…"), go(Rc::downgrade(self), "sonata:connect".into()))],
        ])
    }

    fn window_menu(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let (_key, wins) = self.shown();
        let act = wins.iter().find(|t| t.activated).cloned();
        let win = self.services.windows.clone();
        let (w1, w2, a1, a2) = (win.clone(), win.clone(), act.clone(), act.clone());
        let mut sections = vec![vec![
            Item::new(&tr!("Minimize"), move || {
                let (w, a) = (w1.clone(), a1.clone());
                after_menu(move || {
                    if let (Some(w), Some(a)) = (w, a) {
                        w.minimize(&a);
                    }
                });
            })
            .enabled(act.is_some()),
            Item::new(&tr!("Zoom"), move || {
                if let (Some(w), Some(a)) = (w2.as_ref(), a2.as_ref()) {
                    w.set_maximized(a, !a.maximized);
                }
            })
            .enabled(act.is_some()),
        ]];
        // the app's tabs (apptabs; Vini: here)
        if let (Some(a), Some(tabs)) = (act.as_ref(), self.services.apptabs.clone()) {
            if tabs.kind(&a.app_id).is_some() {
                let tab = |label: String, action: &'static str| {
                    let (t, id) = (tabs.clone(), a.app_id.clone());
                    Item::new(&label, move || {
                        let (t, id) = (t.clone(), id.clone());
                        after_menu(move || t.press(&id, action));
                    })
                };
                sections.push(vec![tab(tr!("New Tab"), "new_tab"), tab(tr!("Show Previous Tab"), "prev_tab"), tab(tr!("Show Next Tab"), "next_tab"), tab(tr!("Close Tab"), "close_tab")]);
            }
        }
        if !wins.is_empty() {
            let list = wins
                .iter()
                .map(|t| {
                    let (w, t2) = (win.clone(), t.clone());
                    let title = if t.title.is_empty() { tr!("Untitled") } else { t.title.clone() };
                    Item::check(&title, act.as_ref().is_some_and(|a| a.id == t.id), move |_| w.iter().for_each(|w| w.activate(&t2)))
                })
                .collect();
            sections.push(list);
        }
        let all = wins.clone();
        sections.push(vec![Item::new(&tr!("Bring All to Front"), move || all.iter().for_each(|t| win.iter().for_each(|w| w.activate(t)))).enabled(!wins.is_empty())]);
        menu_at(btn, sections)
    }

    /// Big Sur: the clock opens Notification Center (a calendar without one).
    fn calendar(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let nc = self.notifications.borrow().clone();
        if let Some(nc) = nc {
            nc.toggle_center(self.monitor.borrow().as_ref());
            return None;
        }
        Some(status_panel(btn, &kit::column(&[gtk::Calendar::new().upcast_ref()])))
    }

    // -- status ---------------------------------------------------------------------------------
    pub fn poll(&self) {
        if self.alive.get() {
            StatusPoll::shared(self.services.quiet_paused.clone()).poll(true, true);
        }
    }

    pub fn poll_soon(&self) {
        glib::timeout_add_local_once(Duration::from_millis(600), || {
            if let Some(b) = bars().first() {
                b.poll();
            }
        });
    }

    fn poll_battery(&self) {
        StatusPoll::shared(self.services.quiet_paused.clone()).battery();
    }

    pub fn wifi_state(&self, res: &logic::WifiStatus) {
        match logic::wifi_icon(res) {
            None => self.wifi.set_visible(false),
            Some(name) => {
                self.wifi.set_visible(self.cfg_bool("show_wifi", true));
                set_icon(&self.wifi, &name);
            }
        }
    }

    pub fn battery_state(&self, res: &(Option<i64>, String, bool, Option<String>)) {
        let (pct, status, ac, profile) = res;
        self.battery.set_visible((pct.is_some() || !status.is_empty()) && self.cfg_bool("show_battery", true));
        let Some(p) = pct else {
            if !status.is_empty() {
                set_icon(&self.battery, "sonata-battery-missing-symbolic"); // battery, no reading
            }
            return;
        };
        set_icon(&self.battery, &logic::battery_icon(Some(*p), status, *ac, profile.as_deref()));
        self.battery_pct.set_label(&format!("{p}%"));
        self.battery_pct.set_visible(self.cfg_bool("battery_percent", false));
    }

    pub fn sound_state(&self, res: Option<(i64, bool)>) {
        self.sound.set_visible(res.is_some() && self.cfg_bool("show_sound", false));
        if let Some((vol, muted)) = res {
            set_icon(&self.sound, &logic::sound_icon(vol, muted));
        }
    }

    pub fn set_volume(&self, v: f64) {
        run_async(move || system::set_volume(Some(v as i64), Some(false)), |_| {
            if let Some(b) = bars().first() {
                b.poll();
            }
        });
    }

    // -- menus of the extras --------------------------------------------------------------------
    /// "Wi-Fi Preferences…" etc. at the bottom of a menu (Big Sur).
    fn prefs_row(pop: &gtk::Popover, title: &str, page: &'static str) -> gtk::Widget {
        let p = pop.downgrade();
        sonata_ui::panel::row(None, title, None, Some(Box::new(move || {
            if let Some(p) = p.upgrade() {
                p.popdown();
            }
            kit::open_settings(page);
        })))
        .0
    }

    fn wifi_panel(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let held: Rc<RefCell<Option<gtk::Popover>>> = Rc::default();
        let h = held.clone();
        let col = self.wifi_column(Rc::new(move || h.borrow().iter().for_each(|p| p.popdown())));
        let pop = status_panel(btn, &col);
        held.replace(Some(pop.clone()));
        Some(pop)
    }

    /// Switch, the joined network, other networks (round signal badges,
    /// lock), Wi-Fi Preferences… -- in the menu bar's Wi-Fi menu, and in
    /// Control Center when its Wi-Fi row is clicked. close(): the panel goes.
    pub fn wifi_column(self: &Rc<Self>, close: Rc<dyn Fn()>) -> gtk::Box {
        let on = sonata_ui::controls::switch(false);
        let col = kit::column(&[sonata_ui::panel::header("Wi-Fi", Some(on.upcast_ref())).upcast_ref()]);
        let cur_box = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let nets = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&sonata_ui::panel::separator());
        col.append(&cur_box);
        col.append(&sonata_ui::panel::section_title(&tr!("Other Networks")));
        nets.append(&sonata_ui::panel::row(None, &tr!("Searching…"), None, None).0);
        col.append(&nets);
        col.append(&sonata_ui::panel::separator());
        let c = close.clone();
        col.append(&sonata_ui::panel::row(None, &tr!("Wi-Fi Preferences…"), None, Some(Box::new(move || {
            c();
            kit::open_settings("wifi");
        })))
        .0);
        let w = Rc::downgrade(self);
        let net_row = move |n: &system::WifiNetwork| -> gtk::Widget {
            let trailing = gtk::Box::new(gtk::Orientation::Horizontal, 4);
            if n.secure {
                trailing.append(&gtk::Image::builder().icon_name("system-lock-screen-symbolic").pixel_size(12).css_classes(["dim-label"]).build());
            }
            let (w, c, n2) = (w.clone(), close.clone(), n.clone());
            let (row, _) = sonata_ui::panel::row(None, &n.ssid, Some(trailing.upcast_ref()), Some(Box::new(move || {
                c();
                if let Some(b) = w.upgrade() {
                    b.join(&n2);
                }
            })));
            let content = row.downcast_ref::<gtk::Button>().and_then(|b| b.child()).and_downcast::<gtk::Box>().or_else(|| row.clone().downcast::<gtk::Box>().ok());
            if let Some(content) = content {
                content.prepend(&badge(&format!("sonata-wifi-{}-symbolic", logic::signal_level(n.signal)), n.connected));
            }
            row
        };
        let (cb, nb, sw) = (cur_box.clone(), nets.clone(), on.clone());
        kit::cached("wifi", || (system::wifi_enabled(), system::wifi_scan(false)), move |(enabled, networks): &(bool, Vec<system::WifiNetwork>)| {
            sw.set_active(*enabled);
            kit::clear(&cb);
            kit::clear(&nb);
            let (joined, others) = logic::split_networks(networks);
            if let Some(j) = joined {
                cb.append(&sonata_ui::panel::section_title(&tr!("Known Network")));
                cb.append(&net_row(j));
            }
            for n in &others {
                nb.append(&net_row(n));
            }
            if others.is_empty() {
                nb.append(&sonata_ui::panel::row(None, &if *enabled { tr!("No networks") } else { tr!("Wi-Fi: Off") }, None, None).0);
            }
        }, true);
        on.connect_state_set(|_, st| {
            run_async(move || system::set_wifi_enabled(st), |_| bars().iter().for_each(|b| b.poll()));
            glib::Propagation::Proceed
        });
        col
    }

    fn join(&self, net: &system::WifiNetwork) {
        if net.connected {
            return;
        }
        let ssid = net.ssid.clone();
        if !net.secure {
            run_async(move || system::wifi_connect(&ssid, ""), |_| bars().iter().for_each(|b| b.poll()));
            return;
        }
        let entry = gtk::PasswordEntry::builder().show_peek_icon(true).hexpand(true).build();
        let e = entry.clone();
        let dlg = sonata_ui::dialog::alert(&fmt(&tr!("The Wi-Fi network “{ssid}” requires a password."), &[("ssid", &net.ssid)]), "", &[("cancel", tr!("Cancel"), ""), ("join", tr!("Join"), "default")], move |r| {
            if r == "join" {
                let (ssid, pw) = (ssid.clone(), e.text().to_string());
                run_async(move || system::wifi_connect(&ssid, &pw), |_| bars().iter().for_each(|b| b.poll()));
            }
        }, None);
        dlg.set_extra_child(Some(&entry));
    }

    // -- Bluetooth (BlueZ over D-Bus: the icon follows the adapter, no polling) ------------------
    fn bt_powered(&self) -> Option<bool> {
        Bluetooth::shared().powered()
    }

    fn bt_update(&self) {
        let powered = self.bt_powered();
        self.bt.set_visible(powered.is_some() && self.cfg_bool("show_bluetooth", true));
        set_icon(&self.bt, if powered == Some(true) { "sonata-bluetooth-symbolic" } else { "sonata-bluetooth-off-symbolic" });
    }

    fn bluetooth_panel(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let held: Rc<RefCell<Option<gtk::Popover>>> = Rc::default();
        let h = held.clone();
        let col = self.bluetooth_column(Rc::new(move || h.borrow().iter().for_each(|p| p.popdown())));
        let pop = status_panel(btn, &col);
        held.replace(Some(pop.clone()));
        Some(pop)
    }

    /// Switch, Devices (paired, connected ones highlighted; click to
    /// connect/disconnect), Bluetooth Preferences… -- in the menu bar's
    /// menu and in Control Center. close(): the panel goes.
    pub fn bluetooth_column(self: &Rc<Self>, close: Rc<dyn Fn()>) -> gtk::Box {
        let on = sonata_ui::controls::switch(self.bt_powered() == Some(true));
        let col = kit::column(&[sonata_ui::panel::header("Bluetooth", Some(on.upcast_ref())).upcast_ref()]);
        col.append(&sonata_ui::panel::separator());
        col.append(&sonata_ui::panel::section_title(&tr!("Devices")));
        let devs = gtk::Box::new(gtk::Orientation::Vertical, 0);
        devs.append(&sonata_ui::panel::row(None, &tr!("Searching…"), None, None).0);
        col.append(&devs);
        col.append(&sonata_ui::panel::separator());
        let c = close.clone();
        col.append(&sonata_ui::panel::row(None, &tr!("Bluetooth Preferences…"), None, Some(Box::new(move || {
            c();
            kit::open_settings("bluetooth");
        })))
        .0);
        let (d, w) = (devs.clone(), Rc::downgrade(self));
        kit::cached("bluetooth", system::bluetooth_devices, move |list: &Vec<system::BtDevice>| {
            kit::clear(&d);
            let paired: Vec<&system::BtDevice> = list.iter().filter(|x| x.paired).collect();
            for dev in paired.iter().take(12) {
                let (c, mac, conn) = (close.clone(), dev.mac.clone(), dev.connected);
                let (row, _) = sonata_ui::panel::row(None, &dev.name, None, Some(Box::new(move || {
                    c();
                    let mac = mac.clone();
                    kit::in_thread(move || {
                        system::bluetooth_connect_result(&mac, !conn);
                    });
                })));
                if let Some(content) = row.downcast_ref::<gtk::Button>().and_then(|b| b.child()).and_downcast::<gtk::Box>() {
                    content.prepend(&badge(logic::bt_icon(&dev.name), dev.connected));
                }
                d.append(&row);
            }
            if paired.is_empty() {
                let powered = w.upgrade().and_then(|b| b.bt_powered()) == Some(true);
                d.append(&sonata_ui::panel::row(None, &if powered { tr!("No devices") } else { tr!("Bluetooth: Off") }, None, None).0);
            }
        }, true);
        on.connect_state_set(|_, st| {
            kit::in_thread(move || {
                system::set_bluetooth(st);
            });
            glib::Propagation::Proceed
        });
        col
    }

    fn battery_panel(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let pct_lbl = gtk::Label::builder().css_classes(["dim-label"]).build();
        let (src_row, src_lbl) = sonata_ui::panel::row(None, &tr!("Power Source: …"), None, None);
        let percent_sw = sonata_ui::controls::switch(self.cfg_bool("battery_percent", false));
        let w = Rc::downgrade(self);
        percent_sw.connect_state_set(move |_, on| {
            if let Some(b) = w.upgrade() {
                b.toggle_percent(on);
            }
            glib::Propagation::Proceed
        });
        // Energy Mode (power-profiles-daemon), macOS Ventura: a checkmark on the current one
        let modes = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let mode_box = gtk::Box::builder().orientation(gtk::Orientation::Vertical).visible(false).build();
        mode_box.append(&sonata_ui::panel::section_title(&tr!("Energy Mode")));
        mode_box.append(&modes);
        mode_box.append(&sonata_ui::panel::separator());
        let col = kit::column(&[
            sonata_ui::panel::header(&tr!("Battery"), Some(pct_lbl.upcast_ref())).upcast_ref(),
            &src_row,
            sonata_ui::panel::separator().upcast_ref(),
            mode_box.upcast_ref(),
            &sonata_ui::panel::row(None, &tr!("Show Percentage"), Some(percent_sw.upcast_ref()), None).0,
            sonata_ui::panel::separator().upcast_ref(),
        ]);
        let pop = status_panel(btn, &col);
        col.append(&Self::prefs_row(&pop, &tr!("Battery Preferences…"), "battery"));
        let (p, boosted, remember) = (pop.downgrade(), self.services.power_boosted.clone(), self.services.remember_profile.clone());
        let pick = Rc::new(move |key: &'static str| {
            if let Some(p) = p.upgrade() {
                p.popdown();
            }
            if let Some((is, set)) = boosted.as_ref() {
                if is() {
                    // raised for a full-screen game (gamemode.PowerBoost)
                    if key == "balanced" {
                        return; // already Automatic
                    }
                    set(false); // the user's own pick: kept after the game
                }
            }
            if let Some(r) = remember.as_ref() {
                r(key); // (back to it when the adapter is plugged in again)
            }
            run_async(move || system::set_power_profile(key), |_| {
                if let Some(b) = bars().first() {
                    b.poll_battery();
                }
            });
        });
        let boosted = self.services.power_boosted.clone();
        run_async(system::power_profile_fast, move |current| {
            let Some(mut current) = current else { return }; // no power-profiles-daemon: no section
            if current == "performance" && boosted.as_ref().is_some_and(|(is, _)| is()) {
                current = "balanced".into(); // Automatic, running a game at full speed
            }
            for (key, label) in system::POWER_PROFILES {
                let pk = pick.clone();
                let (row, _) = sonata_ui::panel::row(Some("object-select-symbolic"), &sonata_i18n::gettext(label), None, Some(Box::new(move || pk(key))));
                if let Some(img) = row.downcast_ref::<gtk::Button>().and_then(|b| b.child()).and_then(|c| c.first_child()) {
                    img.set_opacity(if key == current { 1.0 } else { 0.0 }); // checkmark column (menus)
                }
                modes.append(&row);
            }
            mode_box.set_visible(true);
        });
        run_async(|| (system::battery(system::POWER_SUPPLY), system::on_ac(system::POWER_SUPPLY)), move |((pct, status), ac)| {
            pct_lbl.set_label(&pct.map(|p| format!("{p}%")).unwrap_or_default());
            src_lbl.set_label(&logic::power_source(ac, &status));
        });
        Some(pop)
    }

    fn toggle_percent(&self, on: bool) {
        self.cfg.borrow_mut().insert("battery_percent".into(), json!(on));
        if let Err(e) = sonata_config::update("topbar", &json!({"battery_percent": on})) {
            eprintln!("sonata2-topbar: topbar.json: {e}");
        }
        self.battery_pct.set_visible(on);
    }

    /// Big Sur sound menu: slider, the apps' volumes, Sound Preferences…
    /// The output is picked from the slider's button, as in Control Center.
    fn sound_panel(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let w = Rc::downgrade(self);
        let out_btn = crate::controls::round_button("sonata-audio-output-symbolic", &tr!("Output"), |b| device_menu(b, &tr!("Output"), false));
        let (bx, slider) = crate::controls::slider_with_icon(|v| crate::controls::speaker_icon(v).to_string(), 0.0, move |v| w.upgrade().iter().for_each(|b| b.set_volume(v)), true, Some(&out_btn));
        crate::controls::volume_feedback(&slider);
        let holder = gtk::Box::builder().css_classes(["panel-header"]).build();
        holder.append(&bx);
        let col = kit::column(&[sonata_ui::panel::header(&tr!("Sound"), None).upcast_ref(), holder.upcast_ref(), sonata_ui::panel::separator().upcast_ref()]);
        let mixer = self.services.mixer.clone().filter(|m| (m.available)());
        let mut listening = None;
        if let Some(m) = mixer.as_ref() {
            // every app's own volume, kept (Vini); laid out as Control Center's mixer
            let apps = Rc::new(crate::mixer_ui::AppMixer::new(m.apply.clone(), m.pick.clone(), None));
            col.append(&sonata_ui::panel::section_title(&tr!("Applications")));
            col.append(apps.widget());
            col.append(&sonata_ui::panel::separator());
            let (a, streams) = (apps.clone(), m.streams.clone());
            run_async(move || streams(), move |s| a.set_streams(&s));
            if let Some(listen) = m.listen.as_ref() {
                let a = apps.clone();
                listening = Some(listen(Rc::new(move |s| a.set_streams(s))));
            }
        }
        let pop = status_panel(btn, &col);
        if let (Some(id), Some(un)) = (listening, mixer.as_ref().and_then(|m| m.unlisten.clone())) {
            pop.connect_closed(move |_| un(id)); // apps start / stop playing while it's open
        }
        col.append(&Self::prefs_row(&pop, &tr!("Sound Preferences…"), "sound"));
        run_async(system::volume, move |v| {
            if let Some((v, _)) = v {
                slider.show_value(v as f64);
            }
        });
        Some(pop)
    }

    fn nowplaying_panel(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        Some(status_panel(btn, &kit::column(&[now_playing_module(&self.players, true).upcast_ref()])))
    }

    // -- input sources ----------------------------------------------------------------------------
    /// The layout in use, always (Vini: like macOS' input menu, even with
    /// one layout). Keyboards with a layout of their own (Settings >
    /// Keyboard) get a control each, one per layout: Vini's laptop types
    /// Portuguese, his HyperX US.
    pub fn update_input(self: &Rc<Self>) {
        self.update_input_badges();
        self.read_keyboards();
    }

    fn update_input_badges(&self) {
        let lays = system::keyboard_layouts();
        let show = self.cfg_bool("show_input", true);
        for (i, (b, layout, _)) in self.inputs.borrow().iter().enumerate() {
            match layout {
                None if i == 0 => {
                    b.set_visible(!lays.is_empty() && show);
                    if let Some(first) = lays.first() {
                        set_text(b, &logic::layout_badge(first));
                        let more = if lays.len() > 1 { format!("  {}", tr!("(Ctrl+Space: the previous one)")) } else { String::new() };
                        b.set_tooltip_text(Some(&(logic::layout_name(first) + &more)));
                    }
                }
                _ => b.set_visible(show),
            }
        }
    }

    /// The keyboards connected and their layouts, read in a thread (Wayfire's IPC).
    fn read_keyboards(self: &Rc<Self>) {
        let w = Rc::downgrade(self);
        run_async(
            || {
                let kbs = system::keyboards();
                let own = kbs.iter().map(|n| (n.clone(), system::keyboard_device_layout(n))).collect();
                logic::keyboard_groups(&kbs, &own)
            },
            move |groups| {
                if let Some(b) = w.upgrade() {
                    b.show_keyboards(groups);
                }
            },
        );
    }

    /// One control per group of keyboards (the first control, the others
    /// right after it); none of this while every keyboard follows Input Sources.
    pub fn show_keyboards(self: &Rc<Self>, groups: Vec<(Option<String>, Vec<String>)>) {
        if !self.alive.get() || self.input_groups.borrow().as_ref() == Some(&groups) {
            return;
        }
        self.input_groups.replace(Some(groups.clone()));
        let groups = if groups.is_empty() || (groups.len() == 1 && groups[0].0.is_none()) { vec![(None, vec![])] } else { groups };
        let first = self.inputs.borrow()[0].0.clone();
        for (b, _, _) in self.inputs.borrow_mut().drain(1..) {
            if let Some(p) = b.parent().and_downcast::<gtk::Box>() {
                p.remove(&b);
            }
            self.items.borrow_mut().retain(|x| *x != b);
        }
        let parent = first.parent().and_downcast::<gtk::Box>();
        let mut prev = first.clone();
        for (i, (layout, kbs)) in groups.into_iter().enumerate() {
            let b = if i == 0 {
                first.clone()
            } else {
                let Some(p) = parent.as_ref() else { break };
                let b = item(p, Some(""), None, "input-src");
                p.reorder_child_after(&b, Some(&prev));
                self.connect_item(&b, |bar, btn| bar.input_panel(btn));
                self.items.borrow_mut().push(b.clone());
                b
            };
            if let Some(l) = &layout {
                set_text(&b, &logic::layout_badge(l));
                b.set_tooltip_text(Some(&format!("{}\n{}", logic::layout_name(l), kbs.join("\n"))));
            }
            if i == 0 {
                let mut inputs = self.inputs.borrow_mut();
                inputs[0].1 = layout;
                inputs[0].2 = kbs;
            } else {
                self.inputs.borrow_mut().push((b.clone(), layout, kbs));
            }
            prev = b;
        }
        self.update_input_badges();
    }

    fn input_panel(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let group = self.inputs.borrow().iter().find(|(b, _, _)| b == btn).map(|(_, l, k)| (l.clone(), k.clone()));
        if let Some((Some(layout), kbs)) = group {
            return self.keyboard_panel(btn, &layout, kbs);
        }
        let lays = system::keyboard_layouts();
        let col = kit::column(&[]);
        let pop = status_panel(btn, &col);
        for (i, l) in lays.iter().enumerate() {
            let p = pop.downgrade();
            let (row, _) = sonata_ui::panel::row(Some("object-select-symbolic"), &logic::layout_name(l), None, Some(Box::new(move || {
                p.upgrade().iter().for_each(|p| p.popdown());
                use_layout(i);
            })));
            check_column(&row, i == 0);
            col.append(&row);
        }
        col.append(&sonata_ui::panel::separator());
        let title = if lays.len() < 2 { tr!("Add Input Source…") } else { tr!("Open Keyboard Preferences…") };
        col.append(&Self::prefs_row(&pop, &title, "keyboard"));
        Some(pop)
    }

    /// These keyboards' layout: one of the Input Sources (or the one they
    /// have); a click sets it for them only.
    fn keyboard_panel(self: &Rc<Self>, btn: &gtk::Button, layout: &str, kbs: Vec<String>) -> Option<gtk::Popover> {
        let mut lays = system::keyboard_layouts();
        if !lays.iter().any(|l| l == layout) {
            lays.insert(0, layout.to_string());
        }
        let col = kit::column(&[]);
        let pop = status_panel(btn, &col);
        for kb in &kbs {
            // whose layout this is
            col.append(&gtk::Label::builder().label(kb).xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::End).css_classes(["panel-module-title", "dim-label"]).build());
        }
        for l in &lays {
            let (p, kbs, l2) = (pop.downgrade(), kbs.clone(), l.clone());
            let (row, _) = sonata_ui::panel::row(Some("object-select-symbolic"), &logic::layout_name(l), None, Some(Box::new(move || {
                p.upgrade().iter().for_each(|p| p.popdown());
                let (kbs, l) = (kbs.clone(), l2.clone());
                run_async(move || kbs.iter().for_each(|k| {
                    system::set_keyboard_device_layout(k, &l);
                }), |_| {
                    for b in bars() {
                        b.input_groups.replace(None); // read again: the badges follow
                        b.update_input();
                    }
                });
            })));
            check_column(&row, l == layout);
            col.append(&row);
        }
        col.append(&sonata_ui::panel::separator());
        col.append(&Self::prefs_row(&pop, &tr!("Open Keyboard Preferences…"), "keyboard"));
        Some(pop)
    }

    /// Ctrl+Space (macOS): back to the layout used before this one.
    pub fn next_layout(&self) {
        if system::keyboard_layouts().len() > 1 {
            use_layout(1);
        }
    }

    // -- Control Center ---------------------------------------------------------------------------
    /// One Control Center per menu bar, kept in its panel: each opening
    /// brings its states up to date (refresh) -- a new one, in a new panel,
    /// took ~35 ms and ~100 ms more of GTK work before its first frame. A
    /// new one only when it no longer fits (its layout changed in Settings,
    /// another display or size).
    fn control_center_panel(self: &Rc<Self>, btn: &gtk::Button) -> Option<gtk::Popover> {
        let cc = self.control_center.borrow().clone();
        let pop = self.cc_panel.borrow().clone();
        let cc = match cc {
            Some(cc) if cc.refresh() => {
                if let Some(p) = pop.as_ref() {
                    if p.parent().as_ref() == Some(btn.upcast_ref()) && cc.root.ancestor(gtk::Popover::static_type()).as_ref() == Some(p.upcast_ref()) {
                        kit::reopen(p);
                        return Some(p.clone());
                    }
                }
                cc
            }
            _ => {
                let cc = ControlCenter::new(self, false);
                self.control_center.replace(Some(cc.clone()));
                cc
            }
        };
        detach(cc.root.upcast_ref());
        if let Some(p) = pop {
            // (another item opened it, or a new one)
            if p.get_visible() {
                p.popdown();
            }
            if p.parent().is_some() {
                p.unparent();
            }
        }
        let pop = kit::panel(btn, &cc.root, PANEL_GAP, Some(cc.width), false, true);
        pop.add_css_class("instant"); // no grow-in: there at once
        pop.add_css_class("cc-panel"); // its border as wide as the modules' gaps (controlcenter.py)
        self.cc_panel.replace(Some(pop.clone()));
        Some(pop)
    }

    /// The menu bar's Control Center made unseen: its first opening only
    /// brings it up to date. Once is enough.
    pub fn prewarm_control_center(self: &Rc<Self>) {
        if self.control_center.borrow().is_none() {
            self.control_center.replace(Some(ControlCenter::new(self, true)));
        }
    }
}

/// The watcher's answers to every display's bar; a camera opened while
/// locked or under a game is looked at once that ends.
pub(crate) fn connect_inuse(w: &sonata_system::inuse::Watcher) {
    w.add_listener(|st| {
        for b in bars() {
            b.set_inuse(&st.camera, &st.mic);
        }
    });
    let w2 = w.clone();
    crate::quiet::watch(move |paused| {
        if !paused {
            w2.resume();
        }
    });
}

#[cfg(test)]
pub(crate) fn detach_for_tests(widget: &gtk::Widget) {
    detach(widget);
}

/// Out of the panel it was shown in (closed, or closing), to be shown in a new one.
fn detach(widget: &gtk::Widget) {
    let Some(parent) = widget.parent() else { return };
    if let Some(old) = widget.ancestor(gtk::Popover::static_type()).and_downcast::<gtk::Popover>() {
        if old.get_visible() {
            old.popdown();
        }
    }
    if let Some(b) = parent.downcast_ref::<gtk::Box>() {
        b.remove(widget);
    } else if let Some(v) = parent.downcast_ref::<gtk::Viewport>() {
        v.set_child(None::<&gtk::Widget>); // (the panel's scroller: panel::fit_screen)
    } else if let Some(s) = parent.downcast_ref::<gtk::ScrolledWindow>() {
        s.set_child(None::<&gtk::Widget>);
    } else {
        widget.unparent(); // ui::fixed::FixedWidth (set_parent)
    }
}

fn now() -> glib::DateTime {
    glib::DateTime::now_local().unwrap_or_else(|_| glib::DateTime::now_utc().expect("a clock"))
}

/// A row's leading checkmark shown or not (the checkmark column of menus).
fn check_column(row: &gtk::Widget, on: bool) {
    if let Some(img) = row.downcast_ref::<gtk::Button>().and_then(|b| b.child()).and_then(|c| c.first_child()) {
        img.set_opacity(if on { 1.0 } else { 0.0 });
    }
}

/// The round badge of a network / device row.
fn badge(icon: &str, on: bool) -> gtk::Box {
    let b = gtk::Box::builder().css_classes(["wifi-badge"]).valign(gtk::Align::Center).halign(gtk::Align::Start).hexpand(false).build();
    if on {
        b.add_css_class("on");
    }
    b.append(&gtk::Image::builder().icon_name(icon).pixel_size(14).halign(gtk::Align::Center).valign(gtk::Align::Center).build());
    b
}

/// Wayland has no "switch layout" request: the chosen one becomes the
/// first of the list, which Wayfire applies at once.
fn use_layout(i: usize) {
    let lays = logic::use_layout(system::keyboard_layouts(), i);
    run_async(move || system::set_keyboard_layouts(&lays), |_| {
        for b in bars() {
            b.update_input();
        }
    });
}

/// Output/input picker (Big Sur: the list under the Sound module).
pub fn device_menu(btn: &gtk::Button, title: &str, input: bool) {
    let (b, title) = (btn.clone(), title.to_string());
    run_async(move || if input { system::audio_inputs() } else { system::audio_outputs() }, move |devs| {
        let items: Vec<Item> = devs
            .iter()
            .map(|d| {
                let key = d.key.clone();
                Item::check(&d.name, d.default, move |_| {
                    let key = key.clone();
                    kit::in_thread(move || {
                        if input {
                            system::select_input(&key);
                        } else {
                            system::select_output(&key);
                        }
                    });
                })
            })
            .collect();
        let items = if items.is_empty() { vec![Item::new(&tr!("No devices"), || {}).enabled(false)] } else { items };
        let pop = menu::popup(&b, vec![vec![Item::new(&title, || {}).enabled(false)], items], menu::Options::below());
        pop.set_offset(0, 4);
    });
}

/// Recent Items: the Dock's recent apps, then recent documents.
fn recent_items(_s: &Services) -> Vec<Vec<Item>> {
    let dock = sonata_config::load("dock", &json!({"recent": []}));
    let apps: Vec<Item> = dock
        .get("recent")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).take(5).filter_map(sonata_ui::apps::lookup).map(|info| {
            let i = info.clone();
            // Sonata's launch path (the GPU, its scope, the app lock...) and
            // the display's context: it comes to the front
            Item::new(&sonata_ui::apps::display_name(&info), move || {
                let _ = sonata_ui::apps::launch(&i, &[], None);
            })
        }).collect())
        .unwrap_or_default();
    let mut recent = gtk::RecentManager::default().items();
    recent.sort_by_key(|r| -r.modified().to_unix());
    let docs: Vec<Item> = recent
        .iter()
        .take(8)
        .filter(|r| r.exists())
        .map(|r| {
            let uri = r.uri().to_string();
            Item::new(&r.display_name(), move || open_recent(&uri))
        })
        .collect();
    let sections: Vec<Vec<Item>> = [apps, docs].into_iter().filter(|s| !s.is_empty()).collect();
    if sections.is_empty() {
        vec![vec![Item::new(&tr!("No recent items"), || {}).enabled(false)]]
    } else {
        sections
    }
}

/// A recent item: folders in Sonata's Files, files in their app.
fn open_recent(uri: &str) {
    let f = gio::File::for_uri(uri);
    if f.query_file_type(gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) == gio::FileType::Directory {
        if let Some(b) = bars().first() {
            b.open_folder(uri);
        }
        return;
    }
    let _ = sonata_ui::apps::launch_default_for_uri(uri, None); // (the display's context: to the front)
}

// -- Now Playing ------------------------------------------------------------------------------

/// Small and few: (url, size) -> texture, newest last.
const ART_KEEP: usize = 8;

thread_local! {
    static ART: RefCell<Vec<((String, i32), (gdk::Texture, bool))>> = RefCell::default();
}

/// Album art (mpris:artUrl: file:// or http[s]://) into `image`.
fn load_art(url: &str, image: &gtk::Image, size: i32) {
    if url.is_empty() {
        image.set_icon_name(Some("sonata-now-playing-symbolic"));
        image.set_pixel_size(size / 2); // a small note in the empty square
        return;
    }
    let key = (url.to_string(), size);
    let hit = ART.with(|a| {
        let mut a = a.borrow_mut();
        let i = a.iter().position(|(k, _)| *k == key)?;
        let e = a.remove(i);
        let t = e.1.clone();
        a.push(e); // most recently used last
        Some(t)
    });
    if let Some((t, icon)) = hit {
        image.set_pixel_size(art_size(size, icon));
        image.set_paintable(Some(&t));
        return;
    }
    let px = size * 2; // sharp on HiDPI, not a full-size cover in memory
    let (u, img) = (url.to_string(), image.clone());
    // read in a thread, decoded here (a Pixbuf can't cross threads)
    run_async(
        move || gio::File::for_uri(&u).load_contents(gio::Cancellable::NONE).ok().map(|r| r.0.to_vec()),
        move |bytes| {
            let Some(bytes) = bytes else { return };
            let stream = gio::MemoryInputStream::from_bytes(&glib::Bytes::from_owned(bytes));
            let Ok(pb) = gtk::gdk_pixbuf::Pixbuf::from_stream_at_scale(&stream, px, px, true, gio::Cancellable::NONE) else { return };
            #[allow(deprecated)]
            let tex = gdk::Texture::for_pixbuf(&pb);
            let icon = corners_clear(&pb);
            ART.with(|a| {
                let mut a = a.borrow_mut();
                a.push((key, (tex.clone(), icon)));
                while a.len() > ART_KEEP {
                    a.remove(0);
                }
            });
            img.set_pixel_size(art_size(size, icon));
            img.set_paintable(Some(&tex));
        },
    );
}

/// An app's icon handed over as the art (Chrome's for Netflix, YouTube...:
/// round, its corners clear) is drawn smaller in the square, like an icon,
/// not stretched over it as a cover (Vini: the Chrome icon far too big).
pub fn art_size(size: i32, icon: bool) -> i32 {
    if icon { size * 2 / 3 } else { size }
}

/// The picture's four corners see-through: an icon, not a cover.
pub fn corners_clear(pb: &gtk::gdk_pixbuf::Pixbuf) -> bool {
    if !pb.has_alpha() || pb.n_channels() != 4 || pb.width() < 2 || pb.height() < 2 {
        return false;
    }
    let (w, h, stride) = (pb.width() as usize, pb.height() as usize, pb.rowstride() as usize);
    let bytes = pb.read_pixel_bytes();
    let alpha = |x: usize, y: usize| bytes.get(y * stride + x * 4 + 3).copied().unwrap_or(255);
    [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)].iter().all(|&(x, y)| alpha(x, y) < 16)
}

/// Big Sur Now Playing: artwork, title, artist, previous / play-pause /
/// next; follows the player live and says "Not Playing" when there is none.
pub fn now_playing_module(p: &crate::mpris::Players, header: bool) -> gtk::Box {
    let bx = gtk::Box::new(gtk::Orientation::Vertical, 6);
    if header {
        bx.append(&kit::module_title(&tr!("Now Playing")));
    }
    let row = gtk::Box::builder().spacing(10).css_classes(["cc-np"]).build();
    let art_box = gtk::Box::builder().css_classes(["cc-np-art"]).valign(gtk::Align::Center).halign(gtk::Align::Start).overflow(gtk::Overflow::Hidden).hexpand(false).build();
    art_box.set_size_request(36, 36); // one Control Center row (UNIT_H)
    let art = gtk::Image::builder().pixel_size(36).halign(gtk::Align::Center).valign(gtk::Align::Center).hexpand(true).vexpand(true).build();
    art_box.append(&art);
    row.append(&art_box);
    let texts = gtk::Box::builder().orientation(gtk::Orientation::Vertical).hexpand(true).valign(gtk::Align::Center).build();
    // asks for no width of its own (max_width_chars=1): a long title is cut
    // with "…" and never widens Control Center
    let label = |css: &str| gtk::Label::builder().xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(1).hexpand(true).css_classes([css]).build();
    let (title, artist) = (label("cc-np-title"), label("cc-np-artist"));
    texts.append(&title);
    texts.append(&artist);
    row.append(&texts);
    // previous / play-pause / next (macOS shows no "previous" here; Vini wants it)
    let mut btns = Vec::new();
    for (icon, method) in [("media-skip-backward-symbolic", "Previous"), ("media-playback-start-symbolic", "PlayPause"), ("media-skip-forward-symbolic", "Next")] {
        let b = gtk::Button::builder().icon_name(icon).valign(gtk::Align::Center).build();
        let pl = p.clone();
        b.connect_clicked(move |_| pl.call(method));
        row.append(&b);
        btns.push(b);
    }
    let shown_art: Rc<RefCell<Option<String>>> = Rc::default();
    let pl = p.clone();
    let update: Rc<dyn Fn()> = Rc::new(move || {
        let on = pl.active();
        title.set_label(&if on { pl.title() } else { tr!("Not Playing") });
        artist.set_label(&if on { pl.artist() } else { String::new() });
        artist.set_visible(on && !pl.artist().is_empty());
        btns[1].set_icon_name(if pl.playing() { "media-playback-pause-symbolic" } else { "media-playback-start-symbolic" });
        for b in &btns {
            b.set_sensitive(on);
        }
        let url = if on { pl.art() } else { String::new() };
        if shown_art.borrow().as_ref() != Some(&url) {
            shown_art.replace(Some(url.clone()));
            load_art(&url, &art, 36);
        }
    });
    update();
    // on screen only (map / unmap): a hidden window stays realized --
    // Control Center between openings -- and kept following the player
    let id: Rc<Cell<u32>> = Rc::default();
    let (pl, u, i) = (p.clone(), update.clone(), id.clone());
    row.connect_map(move |_| {
        if i.get() == 0 {
            let u2 = u.clone();
            i.set(pl.connect(move || u2()));
            u();
        }
    });
    let (pl, i) = (p.clone(), id);
    row.connect_unmap(move |_| {
        if i.get() != 0 {
            pl.disconnect(i.replace(0));
        }
    });
    bx.append(&row);
    kit::module(&[bx.upcast_ref()], 8)
}

#[cfg(test)]
mod art_tests {
    use super::*;
    use gtk::gdk_pixbuf::{Colorspace, Pixbuf};

    /// Vini: Netflix in Chrome showed Chrome's icon stretched over the whole
    /// Now Playing square -- Chrome hands its round icon over as the art.
    /// Clear corners: drawn smaller, as an icon; a cover keeps the square.
    #[test]
    fn test_chrome_icon_too_big_in_now_playing() {
        let icon = Pixbuf::new(Colorspace::Rgb, true, 8, 64, 64).unwrap();
        icon.fill(0x00000000); // see-through
        icon.new_subpixbuf(8, 8, 48, 48).fill(0x3080ffff); // the round part, opaque
        assert!(corners_clear(&icon));
        assert_eq!(art_size(36, corners_clear(&icon)), 24);
        let cover = Pixbuf::new(Colorspace::Rgb, true, 8, 64, 64).unwrap();
        cover.fill(0x202020ff);
        assert!(!corners_clear(&cover));
        assert_eq!(art_size(36, corners_clear(&cover)), 36);
        let jpeg = Pixbuf::new(Colorspace::Rgb, false, 8, 64, 64).unwrap(); // no alpha at all
        assert!(!corners_clear(&jpeg));
    }
}

