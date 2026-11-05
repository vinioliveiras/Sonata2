//! Game Bar (Super+G; shell/gamebar.py; Vini: Windows' Xbox Game Bar, drawn
//! the Big Sur way).
//!
//! Over the game, a full-screen overlay: the screen dimmed like Overview's
//! (mission.DIM; no blur -- Vini: a full-screen blur over a game cost too
//! much), a bar at the top -- the app in front, a toggle per widget, the
//! clock -- and the widgets themselves as Control Center-style glass panels.
//! Super+G again, Esc or a click on the dimmed screen closes it; the game
//! keeps running underneath.
//!
//! Controllers (Vini): while it's open the controller drives it (the menu
//! bar's gamepad service hands the presses to [`GameBar::pad`]) -- D-pad /
//! left stick move the focus, A presses, B closes, LB / RB jump between the
//! bar and the widgets. The keyboard's arrows and Tab move the same focus.
//!
//! Glass (Vini, the "fake" glass, at no cost per frame): opening, the Game
//! Bar first has the game's display pictured and blurred once (frost.py,
//! through [`Host::frost`]), and each panel draws its slice of it under
//! itself. No picture (no grim, too slow): the panels stay solid.
//!
//! Widgets: Now Playing, Control Center's controls, Audio (output and
//! microphone, every app's volume, what a recording takes), Capture
//! (Screenshot, Record / Stop with the time, Instant Replay, Save Clip) and
//! Performance (Control Center's FPS Limit and graphs). They move by their
//! title (dragged; a double-click puts one back) and the pin keeps one over
//! the game once the Game Bar has gone (a small click-through window each).
//!
//! It lives in the menu bar's process (it has the capture, the stats, the
//! mixer, the players and the window list): what it needs from there comes
//! through [`Host`], whose every hook may say "not here" -- the Game Bar
//! then shows what it can on its own (`sonata2-rs gamebar --preview`).
//! Nothing runs while it's closed: the clock ticks only while it's on screen.

use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, graphene, gsk};
use serde_json::{json, Map, Value};
use sonata_i18n::{gettext, n_, tr};
use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::rc::Rc;
use std::time::Duration;

pub const NAMESPACE: &str = "sonata2-gamebar";
pub const PIN_NAMESPACE: &str = "sonata2-gamebar-pin";
/// mission.DIM: the same dark layer as Overview (Win+Tab)
pub const DIM: f64 = 0.45;
/// Vini: the Game Bar opens and closes at once, as fast as it can -- no
/// animations -- and already in glass, never turning to glass in front of
/// you: it shows the moment the game's picture is ready (~25 ms), solid
/// only if the picture is later than this
pub const FROST_WAIT_MS: u64 = 100;
/// the states read once it's on screen
pub const READ_AFTER_MS: u64 = 120;
pub const CONFIG: &str = "gamebar";

/// The widgets, in the bar's order -- the same left-to-right order as on
/// the screen, so the toggles, the arrows and LB / RB follow them: (id,
/// title, icon, (halign, valign)). Vini: three along the bottom -- Audio at
/// the left, Capture in the middle, Performance at the right -- Now Playing
/// at the top left, Control Center's controls at the top right.
pub const WIDGETS: [(&str, &str, &str, (gtk::Align, gtk::Align)); 5] = [
    ("nowplaying", n_!("Now Playing"), "sonata-now-playing-symbolic", (gtk::Align::Start, gtk::Align::Start)),
    ("controls", n_!("Control Center"), "sonata-control-center-symbolic", (gtk::Align::End, gtk::Align::Start)),
    ("audio", n_!("Audio"), "audio-volume-high-symbolic", (gtk::Align::Start, gtk::Align::End)),
    ("capture", n_!("Capture"), "camera-photo-symbolic", (gtk::Align::Center, gtk::Align::End)),
    ("performance", n_!("Performance"), "speedometer-symbolic", (gtk::Align::End, gtk::Align::End)),
];
/// the Performance widget's graphs under Control Center's FPS Limit (which
/// has the game's frame rate and frame times): every one Control Center has
/// -- not FPS again; past PERF_MAX_H they scroll inside the widget
pub const PERF_SKIP: [&str; 1] = ["fps"];
pub const PERF_MAX_H: i32 = 440;
/// the D-pad's left / right on a slider
pub const SLIDER_STEP: f64 = 5.0;
/// the apps' volumes: more scroll inside the widget
pub const MIXER_MAX_H: i32 = 200;
/// the bar below the screen's top edge
pub const BAR_TOP: i32 = 14;
/// widgets start under the bar
pub const WIDGET_TOP: i32 = 76;
pub const WIDGET_SIDE: i32 = 24;
pub const WIDGET_W: i32 = 340;
/// the content's width inside a widget (its padding and the body's): long
/// text wraps or ellipsizes, never widens a widget
pub const WIDGET_INNER: i32 = WIDGET_W - 2 * 6 - 2 * 4;
/// two graphs a row, the Grid's 6 px between
pub const PERF_CELL: (i32, i32) = ((WIDGET_INNER - 6) / 2, 92);
/// the panels' tint over the glass (menu_bg at this alpha)
pub const GLASS_TINT: f32 = 0.55;

fn widget_ids() -> Vec<String> {
    WIDGETS.iter().map(|w| w.0.to_string()).collect()
}

pub fn defaults() -> Value {
    json!({"widgets": widget_ids(), "positions": {}, "pinned": [], "known": widget_ids()})
}

pub fn register_css() {
    sonata_ui::css::register_module("ui/panel.py");
    sonata_ui::css::register_module_with("shell/gamebar.py", &[("dim", DIM.to_string())]);
}

// -- what the menu bar lends it ------------------------------------------------------------------
/// The menu bar's capture (shell/capture.py and replay.py).
pub trait Capture {
    fn recording(&self) -> bool;
    /// when the recording started (glib::monotonic_time, µs); 0: unknown
    fn recording_since_us(&self) -> i64;
    /// on `output` (the game's display), never waiting for the timer
    fn screenshot(&self, output: Option<&str>);
    /// on `output`, never waiting for the timer
    fn record(&self, output: Option<&str>);
    fn stop_recording(&self);
    /// gpu-screen-recorder or wl-screenrec there
    fn replay_available(&self) -> bool;
    fn replay_running(&self) -> bool;
    fn replay_start(&self, output: Option<&str>);
    fn replay_stop(&self);
    fn replay_save(&self);
}

/// Control Center's own controls (topbar.QuickControls), made for the Game Bar.
pub struct QuickParts {
    /// Wi-Fi, Bluetooth and Airplane Mode, in their module
    pub radios: gtk::Widget,
    /// Do Not Disturb, in its module (a click anywhere on it toggles)
    pub dnd: gtk::Widget,
    /// the display's brightness
    pub display: gtk::Widget,
}

/// The menu bar's side. Every hook has a "not here" default.
pub trait Host {
    /// the app in front: (app id, its window's title)
    fn front_app(&self) -> Option<(String, String)> {
        None
    }
    fn capture(&self) -> Option<Rc<dyn Capture>> {
        None
    }
    /// The Sound menu's mixer (mixer_ui.AppMixer), its rows reachable with
    /// the controller (focusable sliders and mute buttons).
    fn app_mixer(&self) -> Option<gtk::Widget> {
        None
    }
    /// The apps' streams read again and followed (on), or no longer (off):
    /// only while the Audio widget is on screen.
    fn mixer_listen(&self, _on: bool) {}
    /// open_displays: Settings > Displays, once the Game Bar has gone
    fn quick_controls(&self, _open_displays: Rc<dyn Fn()>) -> Option<QuickParts> {
        None
    }
    /// The quick controls read again; `output`: the game's display (its brightness).
    fn read_controls(&self, _output: Option<&str>) {}
    /// Control Center's Now Playing, following the player only while on screen.
    fn now_playing(&self) -> Option<gtk::Widget> {
        None
    }
    /// Control Center's graph module for `kind` (statsui.module).
    fn stats_module(&self, _kind: &str) -> Option<gtk::Widget> {
        None
    }
    /// What this machine can show (statsui.kinds()).
    fn stats_kinds(&self) -> Vec<String> {
        crate::perf::kinds().to_vec()
    }
    /// The recording pill's pen (livedraw.pen_button) in the bar's look
    /// ("gb-toggle", 16 px, focusable). start(go): the Game Bar closes,
    /// then go() starts drawing on the game's display.
    fn pen_button(&self, _start: PenStart) -> Option<gtk::Widget> {
        None
    }
    /// The controllers drive the Game Bar (on) or the game again (off).
    fn grab_gamepads(&self, _on: bool) {}
    /// A slider the Game Bar doesn't know (Control Center's capsule ones)
    /// moved by `delta`; false: not one of those.
    fn step_slider(&self, _slider: &gtk::Widget, _delta: f64) -> bool {
        false
    }
    /// frost.capture: the display `output` pictured and blurred, done(texture)
    /// on the main loop (None if it couldn't be); renderer_of() is the
    /// renderer to blur with. False: no picture can be taken (no grim).
    fn frost(&self, _output: Option<&str>, _done: Box<dyn FnOnce(Option<gdk::Texture>)>, _renderer_of: Box<dyn Fn() -> Option<gsk::Renderer>>) -> bool {
        false
    }
}

/// The pen's start(go): the Game Bar closes, then go() starts drawing.
pub type PenStart = Rc<dyn Fn(Box<dyn FnOnce()>)>;

/// No menu bar (a preview, tests): what the Game Bar can do alone.
pub struct NoHost;
impl Host for NoHost {}

// -- the logic, apart from GTK --------------------------------------------------------------------
fn strings(v: Option<&Value>) -> Option<Vec<String>> {
    v?.as_array().map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
}

/// A widget added by an update (Now Playing) shows the first time (Vini
/// added it, then it was off): the saved list predates it. `raw`: what was
/// saved, no defaults. True when `cfg` changed (to be saved).
pub fn new_widgets_shown(raw: &Map<String, Value>, cfg: &mut Map<String, Value>) -> bool {
    let known = strings(raw.get("known")).unwrap_or_else(|| {
        // saved before "known" existed: the first three
        if raw.get("widgets").is_some_and(|w| !w.is_null()) { ["audio", "capture", "performance"].map(String::from).to_vec() } else { widget_ids() }
    });
    let new: Vec<String> = widget_ids().into_iter().filter(|w| !known.contains(w)).collect();
    if new.is_empty() {
        return false;
    }
    let mut shown = strings(cfg.get("widgets")).unwrap_or_default();
    for w in new {
        if !shown.contains(&w) {
            shown.push(w);
        }
    }
    cfg.insert("widgets".into(), json!(shown));
    cfg.insert("known".into(), json!(widget_ids()));
    true
}

/// The widgets shown once `wid` is turned on or off -- in the bar's order.
pub fn toggled(shown: &[String], wid: &str, on: bool) -> Vec<String> {
    if on {
        widget_ids().into_iter().filter(|x| x == wid || shown.contains(x)).collect()
    } else {
        shown.iter().filter(|x| *x != wid).cloned().collect()
    }
}

/// The pinned widgets once `wid`'s pin is pressed (unknown ones dropped).
pub fn pin_toggled(pinned: &[String], wid: &str) -> Vec<String> {
    let known: Vec<String> = pinned.iter().filter(|w| WIDGETS.iter().any(|x| x.0 == w.as_str())).cloned().collect();
    if known.iter().any(|w| w == wid) {
        known.into_iter().filter(|w| w != wid).collect()
    } else {
        let mut k = known;
        k.push(wid.to_string());
        k
    }
}

/// Where a widget was moved to, None: its own corner.
pub fn position(cfg: &Map<String, Value>, wid: &str) -> Option<(f64, f64)> {
    let p = cfg.get("positions")?.get(wid)?.as_array()?;
    match p.as_slice() {
        [x, y] => Some((x.as_f64()?, y.as_f64()?)),
        _ => None,
    }
}

/// The Performance widget's graphs: all of Control Center's, in its order,
/// FPS apart (the FPS Limit above shows it).
pub fn perf_kinds(available: &[String]) -> Vec<String> {
    available.iter().filter(|k| !PERF_SKIP.contains(&k.as_str())).cloned().collect()
}

/// Record's label while recording: "Stop · 1:05".
pub fn stop_label(secs: i64) -> String {
    format!("{} · {}:{:02}", tr!("Stop"), secs / 60, secs % 60)
}

/// The openings, each with its own picture: a picture or a deadline that
/// comes for an opening that was closed or replaced never shows anything.
#[derive(Default, Debug)]
pub struct Gate {
    frost_gen: u64,
    opening_gen: Option<u64>,
}

impl Gate {
    /// A new opening: its number.
    pub fn begin(&mut self) -> u64 {
        self.frost_gen += 1;
        self.opening_gen = Some(self.frost_gen);
        self.frost_gen
    }

    pub fn current(&self, gen: u64) -> bool {
        gen == self.frost_gen
    }

    /// Asked to open, waiting (at most FROST_WAIT_MS) for its picture.
    pub fn opening(&self, visible: bool) -> bool {
        self.opening_gen == Some(self.frost_gen) && !visible
    }

    /// Closed before it showed: it never shows.
    pub fn cancel(&mut self) {
        self.frost_gen += 1;
    }
}

// -- the glass ----------------------------------------------------------------------------------
/// The game's picture, blurred, and how much of it shows.
#[derive(Default)]
pub struct Frost {
    pub tex: Option<gdk::Texture>,
    pub alpha: f32,
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct GlassBox {
        pub frost: RefCell<Option<Rc<RefCell<Frost>>>>,
        pub root: glib::WeakRef<gtk::Widget>,
        pub radius_token: RefCell<String>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for GlassBox {
        const NAME: &'static str = "SonataRsGameBarGlass";
        type Type = super::GlassBox;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for GlassBox {}

    impl WidgetImpl for GlassBox {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            self.obj().glass(snap);
            self.parent_snapshot(snap);
        }
    }

    impl BoxImpl for GlassBox {}
}

glib::wrapper! {
    /// A panel (the bar, a widget) drawing the Game Bar's glass under
    /// itself: its slice of the blurred picture, then its tint, inside its
    /// rounded shape. Each panel draws its own (Vini: the Game Bar was slow
    /// -- one glass layer over the whole screen was drawn again whole
    /// whenever a panel changed); a panel moved draws again.
    pub struct GlassBox(ObjectSubclass<imp::GlassBox>) @extends gtk::Box, gtk::Widget, @implements gtk::Orientable;
}

fn px(token: &str) -> f32 {
    sonata_ui::values().get(token).and_then(|v| v.trim_end_matches("px").parse().ok()).unwrap_or(12.0)
}

impl GlassBox {
    pub fn new(orientation: gtk::Orientation, spacing: i32, radius_token: &str, frost: &Rc<RefCell<Frost>>, root: &impl IsA<gtk::Widget>) -> Self {
        let b: Self = glib::Object::builder().property("orientation", orientation).property("spacing", spacing).build();
        b.imp().frost.replace(Some(frost.clone()));
        b.imp().root.set(Some(root.upcast_ref()));
        b.imp().radius_token.replace(radius_token.to_string());
        glass_class(b.upcast_ref());
        b
    }

    fn glass(&self, snap: &gtk::Snapshot) {
        let imp = self.imp();
        let Some(frost) = imp.frost.borrow().clone() else { return };
        let f = frost.borrow();
        let (Some(tex), Some(root)) = (f.tex.as_ref(), imp.root.upgrade()) else { return };
        if f.alpha <= 0.0 || self.parent().is_none() {
            return;
        }
        // (a snapshot's origin is the content box, inside the padding: the
        // whole panel is its border box, here and in the Game Bar)
        let (Some(b), Some(own)) = (self.compute_bounds(&root), self.compute_bounds(self)) else { return };
        let rr = gsk::RoundedRect::from_rect(own, px(&imp.radius_token.borrow()));
        snap.push_rounded_clip(&rr);
        snap.push_opacity(f.alpha as f64);
        snap.append_texture(tex, &graphene::Rect::new(own.x() - b.x(), own.y() - b.y(), root.width() as f32, root.height() as f32));
        let mut tint = sonata_ui::rgba("menu_bg");
        tint.set_alpha(GLASS_TINT);
        snap.append_color(&tint, &own);
        snap.pop();
        snap.pop();
    }
}

/// theme.glass_class: a glass surface's "solid" class in step with the setting.
fn glass_class(w: &gtk::Widget) {
    let sync = |w: &gtk::Widget| {
        if sonata_ui::glass() {
            w.remove_css_class("solid");
        } else {
            w.add_css_class("solid");
        }
    };
    sync(w);
    let weak = w.downgrade();
    sonata_ui::on_change(move || {
        if let Some(w) = weak.upgrade() {
            sync(&w);
        }
    });
}

/// ui.panel.module: a Control Center module's box.
fn panel_module(children: &[&gtk::Widget], spacing: i32) -> gtk::Box {
    let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(spacing).css_classes(["panel-module"]).build();
    for c in children {
        b.append(*c);
    }
    b
}

// -- a widget -----------------------------------------------------------------------------------
/// One Game Bar widget: a glass panel with a header (icon, title, pin) and
/// `body`, the widget's own content.
pub struct Widget {
    pub wid: String,
    pub frame: GlassBox,
    pub head: gtk::Box,
    pub pin_btn: gtk::Button,
    pub body: gtk::Box,
    empty: gtk::Label,
    pub align: (gtk::Align, gtk::Align),
}

impl Widget {
    fn new(wid: &str, frost: &Rc<RefCell<Frost>>, root: &gtk::Overlay) -> Self {
        let (_, title, icon, align) = *WIDGETS.iter().find(|w| w.0 == wid).expect("a known widget");
        let frame = GlassBox::new(gtk::Orientation::Vertical, 0, "r_dialog", frost, root);
        frame.add_css_class("gb-widget");
        frame.set_size_request(WIDGET_W, -1);
        let pin_btn = gtk::Button::builder().icon_name("view-pin-symbolic").css_classes(["gb-pin"]).focus_on_click(false).tooltip_text(tr!("Pin Over the Game")).valign(gtk::Align::Center).build();
        let head = sonata_ui::panel::header(&gettext(title), Some(pin_btn.upcast_ref()));
        head.prepend(&gtk::Image::builder().icon_name(icon).pixel_size(16).margin_end(6).build());
        head.set_cursor_from_name(Some("grab"));
        frame.append(&head);
        let body = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).css_classes(["gb-widget-body"]).build();
        let empty = gtk::Label::builder().label(tr!("Coming soon")).css_classes(["gb-widget-empty"]).build();
        body.append(&empty);
        frame.append(&body);
        let w = Self { wid: wid.into(), frame, head, pin_btn, body, empty, align };
        w.place(None);
        w
    }

    /// At (x, y) in the Game Bar (moved there), or None: its own corner.
    pub fn place(&self, pos: Option<(f64, f64)>) {
        let f = &self.frame;
        match pos {
            None => {
                f.set_halign(self.align.0);
                f.set_valign(self.align.1);
                f.set_margin_start(WIDGET_SIDE);
                f.set_margin_end(WIDGET_SIDE);
                f.set_margin_top(WIDGET_TOP);
                f.set_margin_bottom(WIDGET_SIDE);
            }
            Some((x, y)) => {
                f.set_halign(gtk::Align::Start);
                f.set_valign(gtk::Align::Start);
                f.set_margin_start(x as i32);
                f.set_margin_top(y as i32);
                f.set_margin_end(0);
                f.set_margin_bottom(0);
            }
        }
        f.queue_draw(); // its slice of the glass, where it is now
    }

    /// No margins (in its own pinned window).
    fn bare(&self) {
        let f = &self.frame;
        for m in [gtk::Widget::set_margin_start, gtk::Widget::set_margin_end, gtk::Widget::set_margin_top, gtk::Widget::set_margin_bottom] {
            m(f.upcast_ref(), 0);
        }
        f.set_halign(gtk::Align::Fill);
        f.set_valign(gtk::Align::Fill);
    }

    /// The widget's own content in place of "Coming soon".
    pub fn fill(&self, child: &impl IsA<gtk::Widget>) {
        if self.empty.parent().as_ref() == Some(self.body.upcast_ref()) {
            self.body.remove(&self.empty);
        }
        self.body.append(&sonata_ui::fixed::FixedWidth::new(child, WIDGET_INNER));
    }

    fn shown(&self) -> bool {
        self.frame.get_visible() && !self.frame.has_css_class("hiding")
    }
}

/// A pinned widget over the game once the Game Bar has gone: a small layer
/// surface at the widget's place, clicks passing through it.
fn pin_window(app: Option<gtk::Application>, w: &Widget, x: f64, y: f64, monitor: Option<gdk::Monitor>, layer: bool) -> gtk::Window {
    use gtk4_layer_shell::{Edge, KeyboardMode, Layer, LayerShell};
    let win = gtk::Window::builder().title(tr!("Game Bar")).decorated(false).resizable(false).css_classes(["sonata-gamebar-pin"]).build();
    win.set_application(app.as_ref());
    w.bare();
    win.set_child(Some(&w.frame));
    if layer {
        win.init_layer_shell();
        win.set_namespace(Some(PIN_NAMESPACE));
        win.set_layer(Layer::Overlay);
        win.set_anchor(Edge::Top, true);
        win.set_anchor(Edge::Left, true);
        LayerShell::set_margin(&win, Edge::Top, y as i32);
        LayerShell::set_margin(&win, Edge::Left, x as i32);
        win.set_exclusive_zone(-1);
        win.set_keyboard_mode(KeyboardMode::None);
        if let Some(m) = monitor {
            win.set_monitor(Some(&m));
        }
    }
    win.connect_map(|w| sonata_shell::layer::set_input_region(w, &[])); // the game keeps the clicks
    win
}

// -- the controller's stops ------------------------------------------------------------------------
fn is_module_slider(w: &gtk::Widget) -> bool {
    w.has_css_class("sonata-module-slider")
}

/// The slider `w` is (or is in): a Range, or one of Control Center's capsule ones.
fn slider_of(w: Option<gtk::Widget>) -> Option<gtk::Widget> {
    let w = w?;
    let mut cur = Some(w);
    while let Some(c) = cur {
        if c.is::<gtk::Range>() || is_module_slider(&c) {
            return Some(c);
        }
        cur = c.parent();
    }
    None
}

/// What the controller can stop on inside `root`, in reading order.
fn focusables(root: &gtk::Widget) -> Vec<gtk::Widget> {
    let mut found = Vec::new();
    let mut stack = vec![root.clone()];
    while !stack.is_empty() {
        let w = stack.remove(0);
        let stop = w.is::<gtk::Button>() || w.is::<gtk::Range>() || is_module_slider(&w);
        if &w != root && stop && w.get_visible() && w.get_sensitive() && !w.has_css_class("gb-pin") {
            found.push(w);
            continue;
        }
        let mut kids = Vec::new();
        let mut c = w.first_child();
        while let Some(k) = c {
            c = k.next_sibling();
            kids.push(k);
        }
        stack.splice(0..0, kids);
    }
    found
}

/// A volume slider with its symbol (topbar._slider_with_icon's look):
/// `on_change` only when the user moves it, never when show() puts a level.
struct Level {
    scale: gtk::Scale,
    quiet: Rc<Cell<bool>>,
}

impl Level {
    fn new(icon: fn(f64) -> &'static str, on_change: impl Fn(f64) + 'static) -> (gtk::Widget, Self) {
        let quiet: Rc<Cell<bool>> = Rc::default();
        let q = quiet.clone();
        let img = gtk::Image::builder().icon_name(icon(50.0)).css_classes(["cc-slider-icon"]).build();
        let im = img.clone();
        let scale = sonata_ui::controls::slider(
            50.0,
            move |v| {
                im.set_icon_name(Some(icon(v)));
                if !q.get() {
                    on_change(v);
                }
            },
            0.0,
            100.0,
            None,
        );
        scale.set_focusable(true); // the controller: left / right change it
        scale.set_can_focus(true);
        let row = gtk::Box::builder().spacing(8).css_classes(["cc-slider-box"]).hexpand(true).build();
        row.append(&img);
        row.append(&scale);
        (row.upcast(), Self { scale, quiet })
    }

    fn show(&self, v: f64) {
        self.quiet.set(true);
        self.scale.set_value(v);
        self.quiet.set(false);
    }
}

/// Big Sur speaker: slashed at 0, then one to three waves.
fn speaker_icon(v: f64) -> &'static str {
    if v <= 0.0 {
        "audio-volume-muted-symbolic"
    } else if v < 34.0 {
        "audio-volume-low-symbolic"
    } else if v < 67.0 {
        "audio-volume-medium-symbolic"
    } else {
        "audio-volume-high-symbolic"
    }
}

fn mic_icon(v: f64) -> &'static str {
    if v <= 0.0 { "microphone-disabled-symbolic" } else { "audio-input-microphone-symbolic" }
}

/// The volume feedback sound when the slider is let go (Settings > Sound).
fn volume_feedback(s: &gtk::Scale) {
    let click = gtk::GestureClick::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
    let play = || {
        gio::spawn_blocking(|| sonata_backend::sounds::play("volume"));
    };
    click.connect_released(move |_, _, _, _| play());
    click.connect_unpaired_release(move |_, _, _, _, _| play());
    s.add_controller(click);
}

// -- the Game Bar ---------------------------------------------------------------------------------
struct CaptureUi {
    shot: gtk::Button,
    rec: gtk::Button,
    rec_label: gtk::Label,
    replay: gtk::Button,
    clip: gtk::Button,
}

#[derive(Default)]
struct Drag {
    wid: Option<String>,
    xy: (f64, f64),
    wh: (f64, f64),
    now: Option<(i32, i32)>,
}

pub struct GameBar {
    pub win: gtk::Window,
    pub root: gtk::Overlay,
    pub scrim: gtk::Box,
    pub bar: GlassBox,
    pub app_icon: gtk::Image,
    pub app_name: gtk::Label,
    pub clock: gtk::Label,
    /// the widgets' toggles, in the bar's order
    pub toggles: Vec<(String, gtk::Button)>,
    pub widgets: Vec<(String, Rc<Widget>)>,
    pub fps_limit: RefCell<Option<Rc<crate::fpsmodule::FpsModule>>>,
    host: Rc<dyn Host>,
    cfg: RefCell<Map<String, Value>>,
    frost: Rc<RefCell<Frost>>,
    gate: RefCell<Gate>,
    clock_src: RefCell<Option<glib::SourceId>>,
    hide_src: RefCell<Option<glib::SourceId>>,
    read_src: RefCell<Option<glib::SourceId>>,
    /// wid -> its window while the Game Bar is closed
    pins: RefCell<BTreeMap<String, gtk::Window>>,
    vol: RefCell<Option<(Level, Level)>>,
    has_mixer: Cell<bool>,
    sound_choice: RefCell<Option<sonata_ui::controls::Segmented>>,
    has_controls: Cell<bool>,
    cap: RefCell<Option<CaptureUi>>,
    drag: RefCell<Drag>,
    /// on a layer surface (else a plain window: a preview)
    layer: bool,
}

fn remove_source(cell: &RefCell<Option<glib::SourceId>>) {
    if let Some(id) = cell.take() {
        sonata_ui::cancel(id);
    }
}

/// The game's display (the one with the focus), asked of Wayfire.
fn focused_output() -> Option<String> {
    sonata_backend::wfipc::focused_output()
}

impl GameBar {
    /// Over the game, on a layer surface (a plain window without one).
    pub fn new(app: &gtk::Application, host: Rc<dyn Host>) -> Rc<Self> {
        Self::build(app, host, sonata_shell::layer::available())
    }

    /// In a plain window, for a look (no layer shell, no glass, pins as windows).
    pub fn preview(app: &gtk::Application, host: Rc<dyn Host>) -> Rc<Self> {
        Self::build(app, host, false)
    }

    fn build(app: &gtk::Application, host: Rc<dyn Host>, layer: bool) -> Rc<Self> {
        register_css();
        let win = gtk::Window::builder().application(app).title(tr!("Game Bar")).decorated(false).css_classes(["sonata-gamebar"]).build();
        let mut cfg = sonata_config::load(CONFIG, &defaults());
        let raw = sonata_config::load(CONFIG, &json!({"widgets": null, "known": null})); // what was saved, no defaults
        if new_widgets_shown(&raw, &mut cfg) {
            let _ = sonata_config::update(CONFIG, &json!({"widgets": cfg["widgets"], "known": cfg["known"]}));
        }
        let root = gtk::Overlay::builder().css_classes(["gb-root"]).build();
        let scrim = gtk::Box::builder().css_classes(["gb-scrim"]).hexpand(true).vexpand(true).build();
        root.set_child(Some(&scrim));
        let frost: Rc<RefCell<Frost>> = Rc::default();
        let bar = GlassBox::new(gtk::Orientation::Horizontal, 2, "r_plate", &frost, &root);
        bar.add_css_class("gb-bar");
        bar.set_halign(gtk::Align::Center);
        bar.set_valign(gtk::Align::Start);
        bar.set_margin_top(BAR_TOP);
        let appbox = gtk::Box::builder().css_classes(["gb-app"]).spacing(8).valign(gtk::Align::Center).build();
        let app_icon = gtk::Image::builder().pixel_size(22).build();
        let app_name = gtk::Label::builder().css_classes(["gb-app-name"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(24).build();
        appbox.append(&app_icon);
        appbox.append(&app_name);
        bar.append(&appbox);
        let sep = || gtk::Box::builder().css_classes(["gb-sep"]).build();
        bar.append(&sep());
        let toggles: Vec<(String, gtk::Button)> = WIDGETS
            .iter()
            .map(|(wid, title, icon, _)| {
                let b = gtk::Button::builder().icon_name(*icon).css_classes(["gb-toggle"]).focus_on_click(false).tooltip_text(gettext(title)).valign(gtk::Align::Center).build();
                bar.append(&b);
                (wid.to_string(), b)
            })
            .collect();
        bar.append(&sep());
        root.add_overlay(&bar);
        let clock = gtk::Label::builder().css_classes(["gb-clock"]).valign(gtk::Align::Center).build();
        let shown = strings(cfg.get("widgets")).unwrap_or_default();
        let widgets: Vec<(String, Rc<Widget>)> = WIDGETS
            .iter()
            .map(|(wid, ..)| {
                let w = Rc::new(Widget::new(wid, &frost, &root));
                w.frame.set_visible(shown.iter().any(|s| s == wid));
                root.add_overlay(&w.frame);
                (wid.to_string(), w)
            })
            .collect();
        let me = Rc::new(Self {
            win,
            root,
            scrim,
            bar,
            app_icon,
            app_name,
            clock,
            toggles,
            widgets,
            fps_limit: RefCell::default(),
            host,
            cfg: RefCell::new(cfg),
            frost,
            gate: RefCell::default(),
            clock_src: RefCell::default(),
            hide_src: RefCell::default(),
            read_src: RefCell::default(),
            pins: RefCell::default(),
            vol: RefCell::default(),
            has_mixer: Cell::new(false),
            sound_choice: RefCell::default(),
            has_controls: Cell::new(false),
            cap: RefCell::default(),
            drag: RefCell::default(),
            layer,
        });
        // the pen, then the clock, at the bar's end
        let w = Rc::downgrade(&me);
        let start: PenStart = Rc::new(move |go| {
            // drawing starts once the Game Bar has gone (the marks go on the game)
            if let Some(me) = w.upgrade() {
                me.close_bar();
            }
            glib::timeout_add_local_once(Duration::from_millis(20), go);
        });
        if let Some(pen) = me.host.pen_button(start) {
            me.bar.append(&pen);
            me.bar.append(&sep());
        }
        me.bar.append(&me.clock);
        for (wid, b) in &me.toggles {
            let (w, wid) = (Rc::downgrade(&me), wid.clone());
            b.connect_clicked(move |_| {
                if let Some(me) = w.upgrade() {
                    me.toggle_widget(&wid);
                }
            });
        }
        me.widget("capture").fill(&me.capture_actions());
        me.widget("audio").fill(&me.audio_controls());
        me.widget("performance").fill(&me.performance());
        if let Some(np) = me.host.now_playing() {
            me.widget("nowplaying").fill(&np);
        }
        if let Some(c) = me.controls() {
            me.widget("controls").fill(&c);
        }
        for (wid, w) in &me.widgets {
            w.place(position(&me.cfg.borrow(), wid));
            let (weak, k) = (Rc::downgrade(&me), wid.clone());
            w.pin_btn.connect_clicked(move |_| {
                if let Some(me) = weak.upgrade() {
                    me.toggle_pin(&k);
                }
            });
            // a double-click on its title puts it back (the dragging itself is drag_widgets')
            let click = gtk::GestureClick::new();
            let (weak, k) = (Rc::downgrade(&me), wid.clone());
            click.connect_pressed(move |_, n, _, _| {
                if let (2, Some(me)) = (n, weak.upgrade()) {
                    me.reset_position(&k);
                }
            });
            w.head.add_controller(click);
        }
        me.drag_widgets();
        me.sync_pins();
        me.sync_toggles();
        me.win.set_child(Some(&me.root));

        let keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
        let w = Rc::downgrade(&me);
        keys.connect_key_pressed(move |_, key, _, state| match w.upgrade() {
            Some(me) if key == gdk::Key::Escape || (matches!(key, gdk::Key::g | gdk::Key::G) && state.contains(gdk::ModifierType::SUPER_MASK)) => {
                me.close_bar(); // Super+G again (the keyboard is ours while open)
                glib::Propagation::Stop
            }
            _ => glib::Propagation::Proceed,
        });
        me.win.add_controller(keys);
        let click = gtk::GestureClick::builder().button(0).build();
        let w = Rc::downgrade(&me);
        click.connect_pressed(move |_, _, x, y| {
            // a click on the dimmed game (not on the bar or a widget) closes it
            let Some(me) = w.upgrade() else { return };
            let hit = me.win.pick(x, y, gtk::PickFlags::DEFAULT);
            if hit.as_ref().is_none_or(|h| h == me.scrim.upcast_ref::<gtk::Widget>() || h == me.root.upcast_ref::<gtk::Widget>()) {
                me.close_bar();
            }
        });
        me.win.add_controller(click);
        if !(layer && sonata_shell::layer::overlay_fullscreen(&me.win, NAMESPACE)) {
            me.win.set_default_size(1280, 720); // no layer shell (preview): a plain window
        }
        me
    }

    pub fn widget(&self, wid: &str) -> &Rc<Widget> {
        &self.widgets.iter().find(|(k, _)| k == wid).expect("a known widget").1
    }

    fn sync_toggles(&self) {
        for (wid, b) in &self.toggles {
            if self.widget(wid).frame.get_visible() {
                b.add_css_class("on");
            } else {
                b.remove_css_class("on");
            }
        }
    }

    /// The app in front (the game) beside the toggles.
    fn front_app(&self) {
        match self.host.front_app() {
            Some((key, title)) if !key.is_empty() => {
                let info = sonata_desktop::appmatch::match_app_id(&key).and_then(|d| sonata_ui::apps::lookup(&d));
                match info {
                    Some(info) => {
                        self.app_icon.set_from_gicon(&sonata_ui::icons::app_icon(info.upcast_ref()));
                        self.app_name.set_label(&info.name());
                    }
                    None => {
                        // no desktop entry -- a Steam game ("steam_app_3219630", Vini) or
                        // a Windows program: named and drawn as the Dock and the menu bar do
                        let (name, gicon, _dynamic) = sonata_desktop::windowapps::describe(&key, &title);
                        self.app_icon.set_from_gicon(&gicon);
                        self.app_name.set_label(if name.is_empty() { &key } else { &name });
                    }
                }
            }
            _ => {
                self.app_icon.set_icon_name(Some("input-gaming-symbolic"));
                self.app_name.set_label(&tr!("Game Bar"));
            }
        }
    }

    // -- moving and pinning ----------------------------------------------------------------------
    /// Widgets dragged by their title. The gesture sits on the Game Bar,
    /// which doesn't move: on the title itself its offsets were measured
    /// from a title that moved with them, and the widget shook under the
    /// pointer (Vini). Buttons on the title keep their clicks.
    fn drag_widgets(self: &Rc<Self>) {
        let drag = gtk::GestureDrag::new();
        let w = Rc::downgrade(self);
        drag.connect_drag_begin(move |g, x, y| {
            let Some(me) = w.upgrade() else { return };
            let target = me.root.pick(x, y, gtk::PickFlags::DEFAULT);
            let found = target.as_ref().and_then(|t| me.widgets.iter().find(|(_, wd)| t == wd.head.upcast_ref::<gtk::Widget>() || t.is_ancestor(&wd.head)));
            let on_button = target.as_ref().is_some_and(|t| t.is::<gtk::Button>() || t.ancestor(gtk::Button::static_type()).is_some());
            let Some((wid, wd)) = found.filter(|_| !on_button) else {
                g.set_state(gtk::EventSequenceState::Denied);
                return;
            };
            let b = wd.frame.compute_bounds(&me.root);
            me.drag.replace(Drag {
                wid: Some(wid.clone()),
                xy: b.map_or((0.0, 0.0), |b| (b.x() as f64, b.y() as f64)),
                wh: b.map_or((WIDGET_W as f64, 100.0), |b| (b.width() as f64, b.height() as f64)),
                now: None,
            });
            g.set_state(gtk::EventSequenceState::Claimed);
            wd.frame.add_css_class("dragging");
        });
        let w = Rc::downgrade(self);
        drag.connect_drag_update(move |_, dx, dy| {
            let Some(me) = w.upgrade() else { return };
            let mut d = me.drag.borrow_mut();
            let Some(wid) = d.wid.clone() else { return };
            let (ww, hh) = (me.root.width() as f64, me.root.height() as f64);
            let x = (d.xy.0 + dx).min(ww - d.wh.0).max(0.0);
            let y = (d.xy.1 + dy).min(hh - d.wh.1).max(0.0);
            d.now = Some((x as i32, y as i32));
            drop(d);
            me.widget(&wid).place(Some((x, y)));
        });
        let w = Rc::downgrade(self);
        drag.connect_drag_end(move |_, _, _| {
            let Some(me) = w.upgrade() else { return };
            let d = me.drag.take();
            if let Some(wid) = d.wid {
                me.widget(&wid).frame.remove_css_class("dragging");
                if let Some(xy) = d.now {
                    me.save_pos(&wid, Some(xy));
                }
            }
        });
        self.root.add_controller(drag);
    }

    fn save_pos(&self, wid: &str, xy: Option<(i32, i32)>) {
        let mut cfg = self.cfg.borrow_mut();
        let mut pos = cfg.get("positions").and_then(Value::as_object).cloned().unwrap_or_default();
        match xy {
            None => {
                pos.remove(wid);
            }
            Some((x, y)) => {
                pos.insert(wid.into(), json!([x, y]));
            }
        }
        cfg.insert("positions".into(), Value::Object(pos.clone()));
        let _ = sonata_config::update(CONFIG, &json!({ "positions": pos }));
    }

    /// A double-click on its title: back to its own corner.
    pub fn reset_position(&self, wid: &str) {
        self.save_pos(wid, None);
        self.widget(wid).place(None);
    }

    pub fn toggle_pin(&self, wid: &str) {
        let pinned = pin_toggled(&strings(self.cfg.borrow().get("pinned")).unwrap_or_default(), wid);
        self.cfg.borrow_mut().insert("pinned".into(), json!(pinned));
        let _ = sonata_config::update(CONFIG, &json!({ "pinned": pinned }));
        self.sync_pins();
    }

    fn pinned(&self) -> Vec<String> {
        strings(self.cfg.borrow().get("pinned")).unwrap_or_default()
    }

    fn sync_pins(&self) {
        let pinned = self.pinned();
        for (wid, w) in &self.widgets {
            let on = pinned.contains(wid);
            if on {
                w.pin_btn.add_css_class("on");
            } else {
                w.pin_btn.remove_css_class("on");
            }
            w.pin_btn.set_tooltip_text(Some(&if on { tr!("Unpin") } else { tr!("Pin Over the Game") }));
        }
    }

    /// The Game Bar has gone: its pinned widgets stay over the game.
    fn show_pins(self: &Rc<Self>) {
        let mon = self.win.surface().and_then(|s| WidgetExt::display(&self.win).monitor_at_surface(&s));
        for wid in self.pinned() {
            let Some((_, w)) = self.widgets.iter().find(|(k, _)| *k == wid) else { continue };
            if !w.frame.get_visible() || self.pins.borrow().contains_key(&wid) {
                continue;
            }
            let (x, y) = w.frame.compute_bounds(&self.root).map_or((0.0, 0.0), |b| (b.x() as f64, b.y() as f64));
            self.root.remove_overlay(&w.frame);
            let win = pin_window(self.win.application(), w, x, y, mon.clone(), self.layer);
            win.present();
            self.pins.borrow_mut().insert(wid, win);
        }
        if !self.pins.borrow().is_empty() && self.clock_src.borrow().is_none() {
            self.start_clock(); // a pinned Capture shows its recording's time
        }
    }

    fn hide_pins(&self) {
        let pins = std::mem::take(&mut *self.pins.borrow_mut());
        for (wid, win) in pins {
            win.set_child(None::<&gtk::Widget>);
            win.destroy();
            let w = self.widget(&wid);
            self.root.add_overlay(&w.frame);
            w.place(position(&self.cfg.borrow(), &wid));
        }
    }

    pub fn toggle_widget(&self, wid: &str) {
        let w = self.widget(wid).clone();
        let b = &self.toggles.iter().find(|(k, _)| k == wid).expect("a known widget").1;
        let shown_now = strings(self.cfg.borrow().get("widgets")).unwrap_or_default();
        let on = !w.shown();
        if !on {
            w.frame.add_css_class("hiding"); // out, then gone
            b.remove_css_class("on");
            let ww = w.clone();
            glib::timeout_add_local_once(Duration::ZERO, move || {
                if ww.frame.has_css_class("hiding") {
                    ww.frame.remove_css_class("hiding");
                    ww.frame.set_visible(false);
                }
            });
        } else {
            w.frame.remove_css_class("hiding");
            w.frame.set_visible(true);
            b.add_css_class("on");
        }
        let shown = toggled(&shown_now, wid, on);
        self.cfg.borrow_mut().insert("widgets".into(), json!(shown));
        let _ = sonata_config::update(CONFIG, &json!({ "widgets": shown })); // the same widgets next time
    }

    // -- open / close ----------------------------------------------------------------------------
    pub fn is_open(&self) -> bool {
        self.win.is_visible() && !self.root.has_css_class("closing")
    }

    /// Asked to open, waiting (at most FROST_WAIT_MS) for its picture.
    pub fn opening(&self) -> bool {
        self.gate.borrow().opening(self.win.is_visible())
    }

    pub fn toggle(self: &Rc<Self>) {
        // pressed again while it waits for its picture: that's a close (it
        // opened anyway, the second press taken as another opening)
        if self.is_open() || self.opening() {
            self.close_bar();
        } else {
            self.open();
        }
    }

    fn start_clock(self: &Rc<Self>) {
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_seconds_local(1, move || match w.upgrade() {
            Some(me) if me.tick() => glib::ControlFlow::Continue,
            Some(me) => {
                me.clock_src.take();
                glib::ControlFlow::Break
            }
            None => glib::ControlFlow::Break,
        });
        self.clock_src.replace(Some(id));
    }

    pub fn open(self: &Rc<Self>) {
        remove_source(&self.hide_src);
        self.root.remove_css_class("closing");
        // the levels and radios read once it's on screen: their reads (pactl,
        // nmcli, rfkill, DDC/CI) started with it and slowed its opening (Vini)
        remove_source(&self.read_src);
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(READ_AFTER_MS), move || {
            if let Some(me) = w.upgrade() {
                me.read_src.take();
                if me.is_open() {
                    me.refresh_audio();
                    me.refresh_controls();
                }
            }
        });
        self.read_src.replace(Some(id));
        self.front_app();
        self.tick();
        if self.clock_src.borrow().is_none() {
            self.start_clock();
        }
        let w = Rc::downgrade(self);
        self.frost_then(Rc::new(move || {
            if let Some(me) = w.upgrade() {
                me.show();
            }
        }));
    }

    fn show(&self) {
        // back in the Game Bar (as it shows: a pending opening closed again
        // leaves them over the game)
        self.hide_pins();
        self.win.set_visible(false);
        self.win.present();
        if let Some((_, first)) = self.toggles.first() {
            first.grab_focus(); // where the controller / arrows start
        }
        self.host.grab_gamepads(true);
    }

    /// The game's picture first, blurred, then show(): the Game Bar opens
    /// already in glass (Vini: it opened solid and turned to glass a moment
    /// later). At most FROST_WAIT_MS: later than that it opens solid and
    /// stays so this time (no glass coming on in front of you); the Game
    /// Bar isn't on screen yet, so it's never in the picture.
    fn frost_then(self: &Rc<Self>, show: Rc<dyn Fn()>) {
        let gen = self.gate.borrow_mut().begin();
        self.set_glass(None, 0.0);
        self.root.remove_css_class("frosted");
        if !self.layer {
            show();
            return;
        }
        let shown = Rc::new(Cell::new(false));
        let w = Rc::downgrade(self);
        let once: Rc<dyn Fn()> = {
            let (shown, show, w) = (shown.clone(), show.clone(), w.clone());
            Rc::new(move || {
                if let Some(me) = w.upgrade() {
                    if !shown.get() && me.gate.borrow().current(gen) {
                        shown.set(true);
                        show();
                    }
                }
            })
        };
        let o = once.clone();
        let done = Box::new(move |tex: Option<gdk::Texture>| {
            // this opening's picture, before it showed: in glass from its first frame
            if let (Some(tex), Some(me)) = (tex, w.upgrade()) {
                if me.gate.borrow().current(gen) && !shown.get() {
                    me.set_glass(Some(tex), 1.0);
                    me.root.add_css_class("frosted");
                }
            }
            o();
        });
        let win = self.win.clone();
        let renderer_of = Box::new(move || {
            if !win.is_realized() {
                WidgetExt::realize(&win); // blurred with its renderer (not on screen yet)
            }
            win.renderer()
        });
        if !self.host.frost(focused_output().as_deref(), done, renderer_of) {
            show();
            return;
        }
        glib::timeout_add_local_once(Duration::from_millis(FROST_WAIT_MS), move || once());
    }

    fn set_glass(&self, tex: Option<gdk::Texture>, alpha: f32) {
        self.frost.replace(Frost { tex, alpha });
        self.bar.queue_draw();
        for (_, w) in &self.widgets {
            w.frame.queue_draw(); // each panel's own area only
        }
    }

    pub fn close_bar(self: &Rc<Self>) {
        if self.opening() {
            // not on screen yet: it never shows
            self.gate.borrow_mut().cancel();
            remove_source(&self.read_src);
            remove_source(&self.clock_src);
            return;
        }
        if !self.win.is_visible() || self.root.has_css_class("closing") {
            return;
        }
        self.root.add_css_class("closing");
        self.host.grab_gamepads(false); // the game gets its controller back at once
        remove_source(&self.clock_src);
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::ZERO, move || {
            let Some(me) = w.upgrade() else { return };
            me.hide_src.take();
            me.show_pins(); // (measured where they are, before hiding)
            if me.has_mixer.get() {
                me.host.mixer_listen(me.pins.borrow().contains_key("audio")); // still on screen pinned: still listening
            }
            me.win.set_visible(false);
            me.root.remove_css_class("closing");
        });
        self.hide_src.replace(Some(id));
    }

    /// False: nothing on screen, nothing to tick.
    fn tick(&self) -> bool {
        if !self.is_open() && self.pins.borrow().is_empty() {
            return false;
        }
        if let Ok(now) = glib::DateTime::now_local().and_then(|t| t.format("%H:%M")) {
            self.clock.set_label(&now);
        }
        self.sync_capture();
        true
    }

    /// fn once the Game Bar has gone: the game alone in the picture.
    fn after_close(self: &Rc<Self>, f: impl FnOnce() + 'static) {
        self.close_bar();
        glib::timeout_add_local_once(Duration::from_millis(60), f);
    }

    // -- Audio -----------------------------------------------------------------------------------
    fn audio_controls(self: &Rc<Self>) -> gtk::Box {
        use sonata_backend::system;
        let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).build();
        let (out, vol) = Level::new(speaker_icon, |v| {
            gio::spawn_blocking(move || system::set_volume(Some(v as i64), Some(false)));
        });
        volume_feedback(&vol.scale);
        let (mic_row, mic) = Level::new(mic_icon, |v| {
            gio::spawn_blocking(move || system::set_input_volume(Some(v as i64), Some(false)));
        });
        b.append(&out);
        b.append(&mic_row);
        self.vol.replace(Some((vol, mic)));
        // every app's own volume (Vini): the Sound menu's mixer, the same rows
        if let Some(mixer) = self.host.app_mixer() {
            b.append(&gtk::Label::builder().label(tr!("Applications")).xalign(0.0).css_classes(["gb-section"]).build());
            mixer.add_css_class("cc-mixer"); // Control Center's edges: lined up with the sliders above
            let scroll = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).max_content_height(MIXER_MAX_H).child(&mixer).build();
            b.append(&scroll);
            self.has_mixer.set(true);
        }
        b.append(&gtk::Label::builder().label(tr!("Recordings Take")).xalign(0.0).css_classes(["gb-section"]).build());
        let cur = sound_kind();
        let w = Rc::downgrade(self);
        let seg = sonata_ui::controls::Segmented::new(
            &[("system", tr!("System")), ("mic", tr!("Microphone")), ("both", tr!("Both")), ("none", tr!("None"))],
            Some(&cur),
            move |kind: &str| {
                let _ = sonata_config::update("capture", &json!({ "audio": kind }));
                if let Some(me) = w.upgrade() {
                    if let Some(s) = me.sound_choice.borrow().as_ref() {
                        s.select(kind);
                    }
                }
            },
            false,
        );
        if let Some(both) = seg.buttons.get("both") {
            both.set_tooltip_text(Some(&tr!("System sound and microphone: mixed, and each on its own track")));
        }
        for btn in seg.buttons.values() {
            btn.set_can_focus(true); // the controller reaches them
        }
        b.append(&seg.root);
        self.sound_choice.replace(Some(seg));
        b
    }

    /// The levels as they are now (each opening; changed elsewhere meanwhile).
    fn refresh_audio(self: &Rc<Self>) {
        if self.has_mixer.get() {
            self.host.mixer_listen(true);
        }
        if let Some(s) = self.sound_choice.borrow().as_ref() {
            s.select(&sound_kind());
        }
        let w = Rc::downgrade(self);
        glib::spawn_future_local(async move {
            use sonata_backend::system;
            let (vol, mic) = gio::spawn_blocking(|| (system::volume(), system::input_volume())).await.unwrap_or((None, None));
            let Some(me) = w.upgrade() else { return };
            let levels = me.vol.borrow();
            if let Some((v, m)) = levels.as_ref() {
                for (slider, level) in [(v, vol), (m, mic)] {
                    slider.scale.set_sensitive(level.is_some());
                    if let Some((pct, _muted)) = level {
                        slider.show(pct as f64);
                    }
                }
            }
        });
    }

    // -- Control Center's controls ---------------------------------------------------------------
    /// Wi-Fi, Bluetooth, Airplane Mode, Do Not Disturb and the brightness of
    /// the game's display (Vini) -- Control Center's own, read again at
    /// each opening.
    fn controls(self: &Rc<Self>) -> Option<gtk::Box> {
        let w = Rc::downgrade(self);
        let open_displays: Rc<dyn Fn()> = Rc::new(move || {
            if let Some(me) = w.upgrade() {
                me.after_close(|| crate::open_settings("displays"));
            }
        });
        let q = self.host.quick_controls(open_displays)?;
        self.has_controls.set(true);
        let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).build();
        b.append(&q.radios);
        b.append(&q.dnd);
        let title = gtk::Label::builder().label(tr!("Display")).xalign(0.0).css_classes(["panel-module-title"]).build();
        b.append(&panel_module(&[title.upcast_ref(), &q.display], 6));
        Some(b)
    }

    fn refresh_controls(&self) {
        if self.has_controls.get() {
            self.host.read_controls(focused_output().as_deref()); // the game's display: its brightness
        }
    }

    // -- Performance -----------------------------------------------------------------------------
    fn performance(&self) -> gtk::Box {
        let b = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).css_classes(["gb-perf"]).build();
        // Control Center's FPS Limit, the same module (Vini): Off / 30 / 60 /
        // 90 / 120 / Max, and the game's frame times -- read only while on screen
        let fps = crate::fpsmodule::FpsModule::new();
        for btn in fps.buttons() {
            btn.set_can_focus(true); // the controller reaches them
        }
        b.append(&fps.root);
        self.fps_limit.replace(Some(fps));
        let grid = gtk::Grid::builder().column_spacing(6).row_spacing(6).column_homogeneous(true).build();
        for (i, k) in perf_kinds(&self.host.stats_kinds()).iter().enumerate() {
            // read only while shown
            let m = self.host.stats_module(k).unwrap_or_else(|| crate::perf::module(k).upcast());
            m.set_size_request(PERF_CELL.0, PERF_CELL.1);
            grid.attach(&m, (i % 2) as i32, (i / 2) as i32, 1, 1);
        }
        let scroll = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).max_content_height(PERF_MAX_H).child(&grid).build();
        b.append(&scroll);
        b
    }

    // -- Capture ---------------------------------------------------------------------------------
    fn action(icon: &impl IsA<gtk::Widget>, label: &str, on_click: impl Fn() + 'static) -> (gtk::Button, gtk::Label) {
        let b = gtk::Button::builder().css_classes(["gb-action"]).focus_on_click(false).hexpand(true).build();
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let holder = gtk::Box::builder().css_classes(["gb-action-icon"]).halign(gtk::Align::Center).build();
        icon.set_hexpand(true);
        icon.set_halign(gtk::Align::Center);
        icon.set_valign(gtk::Align::Center);
        holder.append(icon);
        col.append(&holder);
        let l = gtk::Label::builder().label(label).css_classes(["gb-action-label"]).build();
        col.append(&l);
        b.set_child(Some(&col));
        b.connect_clicked(move |_| on_click());
        (b, l)
    }

    fn capture_actions(self: &Rc<Self>) -> gtk::Box {
        let row = gtk::Box::builder().spacing(8).homogeneous(true).css_classes(["gb-actions"]).build();
        let w = Rc::downgrade(self);
        let (shot, _) = Self::action(&gtk::Image::builder().icon_name("camera-photo-symbolic").pixel_size(20).build(), &tr!("Screenshot"), move || {
            if let Some(me) = w.upgrade() {
                me.screenshot();
            }
        });
        let w = Rc::downgrade(self);
        let (rec, rec_label) = Self::action(&gtk::Box::builder().css_classes(["gb-rec-dot"]).build(), &tr!("Record"), move || {
            if let Some(me) = w.upgrade() {
                me.toggle_record();
            }
        });
        row.append(&shot);
        row.append(&rec);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&row);
        let chips = gtk::Box::builder().spacing(6).homogeneous(true).css_classes(["gb-chips"]).build();
        let replay = gtk::Button::builder().label(tr!("Instant Replay")).css_classes(["gb-chip"]).focus_on_click(false).build();
        let w = Rc::downgrade(self);
        replay.connect_clicked(move |_| {
            if let Some(me) = w.upgrade() {
                me.toggle_replay();
            }
        });
        let clip = gtk::Button::builder().label(tr!("Save Last 30 s")).css_classes(["gb-chip"]).focus_on_click(false).tooltip_text(tr!("Save the last 30 seconds (Super+Alt+G)")).build();
        let w = Rc::downgrade(self);
        clip.connect_clicked(move |_| {
            if let Some(me) = w.upgrade() {
                me.save_clip();
            }
        });
        chips.append(&replay);
        chips.append(&clip);
        col.append(&chips);
        self.cap.replace(Some(CaptureUi { shot, rec, rec_label, replay, clip }));
        col
    }

    /// Record or Stop (with the recording's time) as it is now.
    fn sync_capture(&self) {
        let ui = self.cap.borrow();
        let Some(ui) = ui.as_ref() else { return };
        let cap = self.host.capture();
        let rec = cap.as_ref().is_some_and(|c| c.recording());
        if rec {
            ui.rec.add_css_class("recording");
        } else {
            ui.rec.remove_css_class("recording");
        }
        let label = match cap.as_ref().filter(|_| rec) {
            Some(c) => {
                let t0 = c.recording_since_us();
                stop_label(if t0 > 0 { (glib::monotonic_time() - t0) / 1_000_000 } else { 0 })
            }
            None => tr!("Record"),
        };
        ui.rec_label.set_label(&label);
        ui.shot.set_sensitive(cap.is_some());
        ui.rec.set_sensitive(cap.is_some());
        let ok = cap.as_ref().is_some_and(|c| c.replay_available());
        let on = ok && cap.as_ref().is_some_and(|c| c.replay_running());
        ui.replay.set_sensitive(ok);
        let tip = (!ok).then(|| tr!("Instant replay needs gpu-screen-recorder or wl-screenrec"));
        ui.replay.set_tooltip_text(tip.as_deref());
        if on {
            ui.replay.add_css_class("on");
        } else {
            ui.replay.remove_css_class("on");
        }
        ui.clip.set_sensitive(on);
    }

    pub fn screenshot(self: &Rc<Self>) {
        let Some(cap) = self.host.capture() else { return };
        self.after_close(move || cap.screenshot(focused_output().as_deref()));
    }

    /// Instant replay on (the game's display: the one in front) or off.
    pub fn toggle_replay(&self) {
        let Some(cap) = self.host.capture() else { return };
        if cap.replay_running() {
            cap.replay_stop();
        } else {
            cap.replay_start(focused_output().as_deref());
        }
        self.sync_capture();
    }

    /// The last 30 seconds as a clip -- once the Game Bar has gone (it
    /// would be in the last moments).
    pub fn save_clip(self: &Rc<Self>) {
        let Some(cap) = self.host.capture().filter(|c| c.replay_running()) else { return };
        if self.is_open() {
            self.after_close(move || cap.replay_save());
        } else {
            cap.replay_save();
        }
    }

    pub fn toggle_record(self: &Rc<Self>) {
        let Some(cap) = self.host.capture() else { return };
        if cap.recording() {
            cap.stop_recording(); // stop here (the bar stays: what's next)
            self.sync_capture();
            return;
        }
        self.after_close(move || cap.record(focused_output().as_deref()));
    }

    // -- controller ------------------------------------------------------------------------------
    /// A controller press (the gamepad service's action names: up, down,
    /// left, right, primary, back, guide, switch_next, switch_prev).
    pub fn pad(self: &Rc<Self>, name: &str) {
        if !self.is_open() {
            return;
        }
        self.win.set_focus_visible(true); // the ring shows from the first press
        let dir = match name {
            "up" => Some(gtk::DirectionType::Up),
            "down" => Some(gtk::DirectionType::Down),
            "left" => Some(gtk::DirectionType::Left),
            "right" => Some(gtk::DirectionType::Right),
            _ => None,
        };
        let focus = gtk::prelude::GtkWindowExt::focus(&self.win);
        if let Some(dir) = dir {
            let slider = slider_of(focus.clone());
            match (focus, slider) {
                (None, _) => self.focus_zone(0),
                (Some(_), Some(s)) if matches!(name, "left" | "right") => {
                    // a slider: its value
                    let delta = if name == "right" { SLIDER_STEP } else { -SLIDER_STEP };
                    match s.downcast_ref::<gtk::Range>() {
                        Some(r) => r.set_value(r.value() + delta),
                        None => {
                            self.host.step_slider(&s, delta);
                        }
                    }
                }
                _ => {
                    self.win.child_focus(dir);
                }
            }
        } else if name == "primary" {
            if let Some(f) = focus {
                f.activate(); // (a button shows its press, then clicks)
            }
        } else if matches!(name, "back" | "guide") {
            self.close_bar();
        } else if matches!(name, "switch_next" | "switch_prev") {
            let zones = self.zones();
            let here = zones.iter().position(|z| self.inside(z));
            let n = zones.len() as i64;
            let step = if name == "switch_next" { 1 } else { -1 };
            self.focus_zone(here.map_or(0, |h| (h as i64 + step).rem_euclid(n) as usize));
        }
    }

    /// The bar, then each widget shown that has something to press.
    fn zones(&self) -> Vec<gtk::Widget> {
        let mut out = vec![self.bar.clone().upcast::<gtk::Widget>()];
        out.extend(self.widgets.iter().filter(|(_, w)| w.shown() && w.frame.parent().is_some() && !focusables(w.frame.upcast_ref()).is_empty()).map(|(_, w)| w.frame.clone().upcast()));
        out
    }

    fn inside(&self, zone: &gtk::Widget) -> bool {
        gtk::prelude::GtkWindowExt::focus(&self.win).is_some_and(|f| &f == zone || f.is_ancestor(zone))
    }

    fn focus_zone(&self, i: usize) {
        let zones = self.zones();
        if let Some(first) = focusables(&zones[i % zones.len()]).first() {
            first.grab_focus();
        }
    }
}

fn sound_kind() -> String {
    sonata_config::load("capture", &json!({"audio": "system"})).get("audio").and_then(Value::as_str).unwrap_or("system").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn v(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    /// test_widgets_along_the_bottom (Vini): Audio bottom left, Capture
    /// bottom centre, Performance bottom right -- the bar's toggles in that
    /// same order; Now Playing top left, Control Center top right.
    #[test]
    fn test_widgets_along_the_bottom() {
        use gtk::Align as A;
        let got: Vec<(&str, (A, A))> = WIDGETS.iter().map(|w| (w.0, w.3)).collect();
        assert_eq!(
            got,
            [("nowplaying", (A::Start, A::Start)), ("controls", (A::End, A::Start)), ("audio", (A::Start, A::End)), ("capture", (A::Center, A::End)), ("performance", (A::End, A::End))]
        );
    }

    /// test_widget_toggle_lights_its_button_and_is_remembered: off drops
    /// it, on puts it back in the bar's order.
    #[test]
    fn test_widget_toggle_is_remembered_in_order() {
        let all = widget_ids();
        let off = toggled(&all, "audio", false);
        assert!(!off.contains(&"audio".to_string()));
        assert_eq!(toggled(&off, "audio", true), all);
        assert_eq!(toggled(&v(&["performance"]), "audio", true), v(&["audio", "performance"]));
    }

    /// test_new_widget_shows_after_an_update: a Game Bar saved before Now
    /// Playing existed shows it the first time; a choice made later (turned
    /// off) is kept.
    #[test]
    fn test_new_widget_shows_after_an_update() {
        let raw: Map<String, Value> = json!({"widgets": ["audio", "capture"], "known": null}).as_object().unwrap().clone();
        let mut cfg = json!({"widgets": ["audio", "capture"]}).as_object().unwrap().clone();
        assert!(new_widgets_shown(&raw, &mut cfg));
        assert_eq!(strings(cfg.get("widgets")).unwrap(), v(&["audio", "capture", "nowplaying", "controls"]));
        assert_eq!(strings(cfg.get("known")).unwrap(), widget_ids());
        // turned off afterwards: kept off
        let raw2: Map<String, Value> = json!({"widgets": ["audio"], "known": widget_ids()}).as_object().unwrap().clone();
        let mut cfg2 = raw2.clone();
        assert!(!new_widgets_shown(&raw2, &mut cfg2));
        assert_eq!(strings(cfg2.get("widgets")).unwrap(), v(&["audio"]));
        // never saved: nothing new
        let none: Map<String, Value> = json!({"widgets": null, "known": null}).as_object().unwrap().clone();
        assert!(!new_widgets_shown(&none, &mut Map::new()));
    }

    #[test]
    fn pins_and_positions() {
        assert_eq!(pin_toggled(&[], "audio"), v(&["audio"]));
        assert_eq!(pin_toggled(&v(&["audio", "ghost"]), "audio"), Vec::<String>::new());
        let cfg = json!({"positions": {"audio": [10, 20], "capture": [1], "performance": "x"}}).as_object().unwrap().clone();
        assert_eq!(position(&cfg, "audio"), Some((10.0, 20.0)));
        assert_eq!(position(&cfg, "capture"), None);
        assert_eq!(position(&cfg, "performance"), None);
        assert_eq!(position(&Map::new(), "audio"), None);
    }

    /// test_every_control_center_graph_under_it: every graph but FPS (the
    /// FPS Limit above has it).
    #[test]
    fn test_every_control_center_graph_under_it() {
        assert_eq!(perf_kinds(&v(&["cpu", "gpu", "vram", "ram", "net", "fps"])), v(&["cpu", "gpu", "vram", "ram", "net"]));
    }

    /// test_while_recording_it_is_stop_with_the_time
    #[test]
    fn test_while_recording_it_is_stop_with_the_time() {
        let _env = crate::testenv::env();
        assert_eq!(stop_label(65), "Stop · 1:05");
        assert_eq!(stop_label(0), "Stop · 0:00");
    }

    /// test_super_g_twice_quickly_still_opened_it: pressed again while it
    /// waited for its picture, the second press was taken as another
    /// opening. It's a close now -- the picture or the deadline that come
    /// later show nothing.
    #[test]
    fn test_super_g_twice_quickly_still_opened_it() {
        let mut g = Gate::default();
        assert!(!g.opening(false));
        let gen = g.begin();
        assert!(g.opening(false) && g.current(gen));
        g.cancel(); // Super+G again, before it showed
        assert!(!g.opening(false));
        assert!(!g.current(gen));
    }

    /// test_late_picture_ignored_once_closed_or_reopened: the first
    /// opening's picture, arriving after a second opening, is not this one's.
    #[test]
    fn test_late_picture_ignored_once_closed_or_reopened() {
        let mut g = Gate::default();
        let first = g.begin();
        let second = g.begin();
        assert!(!g.current(first) && g.current(second));
        assert!(!g.opening(true)); // on screen: not "opening"
    }

    #[test]
    fn css_has_its_dim() {
        let blocks = sonata_ui::css::blocks("shell/gamebar.py");
        assert!(blocks.iter().any(|(_, css, _)| css.contains("%(dim)s")));
        assert_eq!(DIM.to_string(), "0.45");
        assert_eq!(WIDGET_INNER, 320);
        assert_eq!(PERF_CELL, (157, 92));
    }
}
