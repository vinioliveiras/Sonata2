//! Sonata's app windows (sonata2/ui/window.py): the standard frame, glass,
//! the traffic lights (and a dialog's close dot), the unified glass
//! toolbar, and the size each window remembers (winsize.py). The CSS is
//! window.py's own (css.json); the dots' size and place are tokens
//! (tokens::traffic_tokens), so the user's button size and window radius
//! move them with the rest of the theme.
//!
//! Button colours (Settings > Appearance) are sonata-tweaks' pictures:
//! `sonata_tweaks::trafficlights::style_windows()` registers them in a
//! process that shows traffic lights.

use gtk::{glib, graphene, prelude::*, subclass::prelude::*};
use serde_json::{json, Value};
use sonata_i18n::{n_, tr};
use std::cell::Cell;
use std::rc::Rc;
use std::time::Duration;

pub(crate) fn register_css() {
    crate::css::register_module("ui/window.py");
    // a new button size / window radius: the dots' reach measured again
    crate::theme::on_change(forget_reach);
    crate::theme::on_buttons_change(forget_reach);
}

// -- traffic lights ----------------------------------------------------------------------------
/// "left" (macOS, the default) or "right": where windows keep their buttons.
pub fn buttons_side() -> String {
    crate::frame::buttons_side(&crate::frame::appearance())
}

/// The buttons from the window's edge inwards (Settings > Appearance >
/// Button order): close, minimize, maximize unless the user moved them.
pub fn buttons_order() -> Vec<String> {
    crate::frame::buttons_order(crate::frame::appearance().get("buttons_order"))
}

/// Where a small x that closes something goes (a window's picture in the
/// Dock's previews, a desktop in Mission Control): the side the user keeps
/// window buttons on (Vini: "my buttons are all on the left").
pub fn close_align() -> gtk::Align {
    if buttons_side() == "right" { gtk::Align::End } else { gtk::Align::Start }
}

/// The green button: maximize / restore.
pub fn toggle_zoom(win: &impl IsA<gtk::Window>) {
    if win.is_maximized() {
        win.unmaximize();
    } else {
        win.maximize();
    }
}

/// A button's name -> its dot's CSS class.
pub fn dot_class(name: &str) -> &'static str {
    match name {
        "close" => "tl-close",
        "minimize" => "tl-min",
        _ => "tl-zoom",
    }
}

// Bigger targets, same look (Vini: the 12 px dots were hard to hit). Each dot
// takes the clicks of the room around it -- Fitts' law, like macOS: half the
// gap to its neighbours (no dead space between them), up to the window's top
// edge, out to its side edge for the outermost one, and half a gap below.
// Maximized, that's the screen's corner: throw the pointer there and click.
// Only the hit test grows (contains): layout and drawing don't move.
/// px: a dot this much further from the edge than designed still reaches it.
pub const EDGE_SLACK: f64 = 8.0;

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Reach {
    pub top: f64,
    pub edge: f64,
    pub gap: f64,
    pub below: f64,
}

/// The room around a dot of this frame: top, edge (to the window's side),
/// gap (to a neighbour, each side), below.
pub fn reach_of(f: &crate::frame::Frame) -> Reach {
    let half = f.dot as f64 / 2.0;
    let gap = f.dot_gap as f64 / 2.0;
    Reach { top: (f.dot_top - half).max(0.0), edge: (f.dot_left - half).max(0.0), gap, below: gap }
}

thread_local! {
    static REACH: Cell<Option<Reach>> = const { Cell::new(None) };
}

fn reach() -> Reach {
    REACH.with(|r| {
        r.get().unwrap_or_else(|| {
            let v = reach_of(&crate::frame::frame());
            r.set(Some(v));
            v
        })
    })
}

fn forget_reach() {
    REACH.with(|r| r.set(None));
}

/// Whether (x, y), in a dot's own coordinates, hits it: the dot of
/// `w` x `h`, `outer` the side facing the window's edge, `edges` how far
/// the window's content edges really are (left, top, right).
pub fn light_contains(r: Reach, outer: Option<&str>, w: f64, h: f64, edges: Option<(f64, f64, f64)>, x: f64, y: f64) -> bool {
    let mut left = if outer == Some("left") { r.edge } else { r.gap };
    let mut right = if outer == Some("right") { r.edge } else { r.gap };
    let mut top = r.top;
    if let Some((ex, ey, er)) = edges {
        // out to the real edge, if it's a little further
        if (0.0..=EDGE_SLACK).contains(&(ey - top)) {
            top = ey;
        }
        if outer == Some("left") && (0.0..=EDGE_SLACK).contains(&(ex - left)) {
            left = ex;
        }
        if outer == Some("right") && (0.0..=EDGE_SLACK).contains(&(er - right)) {
            right = er;
        }
    }
    -left <= x && x < w + right && -top <= y && y < h + r.below
}

mod light_imp {
    use super::*;

    #[derive(Default)]
    pub struct Light {
        /// which side faces the window's edge ("left" / "right"), if any
        pub outer: Cell<Option<&'static str>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Light {
        const NAME: &'static str = "SonataTrafficLight";
        type Type = super::Light;
        type ParentType = gtk::Button;
    }

    impl ObjectImpl for Light {}
    impl ButtonImpl for Light {}

    impl WidgetImpl for Light {
        fn contains(&self, x: f64, y: f64) -> bool {
            let obj = self.obj();
            light_contains(reach(), self.outer.get(), obj.width() as f64, obj.height() as f64, obj.to_edges(), x, y)
        }
    }
}

glib::wrapper! {
    /// A traffic light whose clickable area reaches past its dot.
    pub struct Light(ObjectSubclass<light_imp::Light>)
        @extends gtk::Button, gtk::Widget,
        @implements gtk::Accessible, gtk::Actionable, gtk::Buildable, gtk::ConstraintTarget;
}

impl Light {
    fn new(outer: Option<&'static str>, css: &str, tip: Option<&str>) -> Self {
        let b: Self = glib::Object::builder()
            .property("valign", gtk::Align::Center)
            .property("focus-on-click", false)
            .property("can-focus", false)
            .build();
        b.add_css_class(css);
        b.set_tooltip_text(tip);
        b.imp().outer.set(outer);
        b
    }

    /// How far the window's content edges really are (left, top, right):
    /// a header bar's padding put Settings' dots a pixel further in than
    /// the tokens say, and the very corner missed (Vini: on both sides).
    fn to_edges(&self) -> Option<(f64, f64, f64)> {
        let content = self.root()?.downcast::<gtk::Window>().ok()?.child()?;
        let p = self.compute_point(&content, &graphene::Point::new(0.0, 0.0))?;
        let (x, y) = (p.x() as f64, p.y() as f64);
        Some((x, y, content.width() as f64 - (x + self.width() as f64)))
    }
}

/// Close / minimize / zoom as macOS-style coloured dots. `on_zoom` None
/// greys the green one out (fixed-size windows, like System Settings). In
/// the order Settings > Appearance > Button order says, from the window's
/// edge; on the right, mirrored: the first one at the window's edge.
pub fn traffic_lights(on_close: impl Fn() + 'static, on_minimize: impl Fn() + 'static, on_zoom: Option<Box<dyn Fn()>>) -> gtk::Box {
    let bx = gtk::Box::builder().css_classes(["traffic"]).valign(gtk::Align::Start).build();
    let zoom = on_zoom.is_some();
    type Call = Option<Rc<dyn Fn()>>;
    let mut calls: Vec<(&str, Call)> =
        vec![("close", Some(Rc::new(on_close))), ("minimize", Some(Rc::new(on_minimize))), ("maximize", on_zoom.map(Rc::from))];
    let tip = |n: &str| match n {
        "close" => Some(tr!("Close")),
        "minimize" => Some(tr!("Minimize")),
        _ => zoom.then(|| tr!("Zoom")),
    };
    let mut dots: Vec<(String, Call)> =
        buttons_order().into_iter().map(|n| { let cb = calls.iter_mut().find(|c| c.0 == n).and_then(|c| c.1.take()); (n, cb) }).collect();
    let right = buttons_side() == "right";
    if right {
        dots.reverse();
        bx.add_css_class("traffic-right");
    }
    let last = dots.len().saturating_sub(1);
    for (i, (name, cb)) in dots.into_iter().enumerate() {
        // the first one, at the window's edge
        let outer = match (right, i) {
            (true, i) if i == last => Some("right"),
            (false, 0) => Some("left"),
            _ => None,
        };
        let b = Light::new(outer, dot_class(&name), tip(&name).as_deref());
        match cb {
            Some(f) => {
                b.connect_clicked(move |_| f());
            }
            None => {
                b.add_css_class("tl-disabled");
                b.set_can_target(false);
            }
        }
        bx.append(&b);
    }
    bx
}

/// Only the close dot (Sonata's dialogs: Vini), in the look and on the
/// side the user picked for windows (Settings > Appearance), like theirs.
pub fn close_button(on_close: impl Fn() + 'static) -> gtk::Box {
    let right = buttons_side() == "right";
    let bx = gtk::Box::builder()
        .css_classes(["traffic", "traffic-close-only"])
        .valign(gtk::Align::Start)
        .halign(if right { gtk::Align::End } else { gtk::Align::Start })
        .build();
    if right {
        bx.add_css_class("traffic-right");
    }
    let b = Light::new(Some(if right { "right" } else { "left" }), "tl-close", Some(&tr!("Close")));
    b.connect_clicked(move |_| on_close());
    bx.append(&b);
    bx
}

fn any_widget(widget: &gtk::Widget, test: &dyn Fn(&gtk::Widget) -> bool) -> bool {
    let mut stack = vec![widget.clone()];
    while let Some(w) = stack.pop() {
        if test(&w) {
            return true;
        }
        let mut c = w.first_child();
        while let Some(ch) = c {
            c = ch.next_sibling();
            stack.push(ch);
        }
    }
    false
}

/// True when `widget` already holds window buttons (a dialog or panel
/// with its own): close_button() isn't added a second time.
pub fn has_window_buttons(widget: &impl IsA<gtk::Widget>) -> bool {
    // (Button order's picture of them isn't a window's own)
    any_widget(widget.upcast_ref(), &|w| w.has_css_class("traffic") && !w.has_css_class("traffic-order"))
}

/// A button that already closes without doing anything (Vini: a close dot
/// next to Cancel or Quit makes no sense -- nor next to OK, Done, Later, Not
/// Now or Don't Allow: Restart's "Later / Restart" alert had one).
pub const DISMISS_IDS: [&str; 7] = ["cancel", "close", "quit", "ok", "done", "later", "deny"];
/// (an English label too: some alerts' buttons come untranslated, from GIO)
pub const DISMISS_LABELS: [&str; 9] =
    [n_!("Cancel"), n_!("Close"), n_!("Quit"), n_!("OK"), n_!("Done"), n_!("Later"), n_!("Not Now"), n_!("Don't Allow"), n_!("Don't Open")];

fn plain(text: &str) -> String {
    let t = text.replace('_', "").replace('\u{2019}', "'");
    t.trim().trim_end_matches(['…', '.']).trim().to_lowercase()
}

/// Cancel, Close, Quit, OK, Done, Later, Not Now, Don't Allow... (in
/// Sonata's language or English): the button leaves without doing anything.
pub fn dismiss_label(text: &str) -> bool {
    let t = plain(text);
    !t.is_empty() && DISMISS_LABELS.iter().any(|l| plain(l) == t || plain(&sonata_i18n::gettext(l)) == t)
}

/// True when `widget` holds a Cancel / Close / Quit button: no close
/// dot is added to it.
pub fn has_dismiss_button(widget: &impl IsA<gtk::Widget>) -> bool {
    any_widget(widget.upcast_ref(), &|w| w.downcast_ref::<gtk::Button>().and_then(|b| b.label()).is_some_and(|l| dismiss_label(&l)))
}

/// Give an app window Sonata's standard frame (call once, any time).
pub fn standard(win: &(impl IsA<gtk::Window> + IsA<gtk::Widget>)) {
    let w = win.upcast_ref::<gtk::Widget>();
    w.add_css_class("sonata-window");
    w.set_overflow(gtk::Overflow::Hidden); // children clipped to the rounded corners
}

/// Present a window that was hidden instead of closed. GTK remembers a
/// minimize as "minimize when shown" and Wayland never tells it the Dock
/// brought the window back, so a hidden window came back minimized.
pub fn show_again(win: &(impl IsA<gtk::Window> + IsA<gtk::Widget>)) {
    if !win.upcast_ref::<gtk::Widget>().is_visible() {
        win.unminimize();
    }
    win.present();
}

/// A toolbar button: (icon, tooltip, callback).
pub type Tool = (&'static str, String, Box<dyn Fn()>);

/// A toolbar continuing the glass title bar the compositor draws (macOS
/// unified toolbar), `end` buttons at its end. Returns the handle (to add
/// to the window), the bar (its centre is free) and the buttons.
pub fn glass_toolbar(win: &impl IsA<gtk::Widget>, end: Vec<Tool>) -> (gtk::WindowHandle, gtk::CenterBox, Vec<gtk::Button>) {
    win.upcast_ref::<gtk::Widget>().add_css_class("sonata-unified");
    let bar = gtk::CenterBox::new();
    bar.add_css_class("sonata-toolbar");
    let mut buttons = Vec::new();
    if !end.is_empty() {
        let row = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
        for (icon, tip, cb) in end {
            let b = gtk::Button::builder().icon_name(icon).tooltip_text(tip).can_focus(false).build();
            b.add_css_class("tool");
            b.connect_clicked(move |_| cb());
            row.append(&b);
            buttons.push(b);
        }
        bar.set_end_widget(Some(&row));
    }
    (gtk::WindowHandle::builder().child(&bar).build(), bar, buttons)
}

// -- the size a window opens at (winsize.py: windows.json) ----------------------------------
const MIN: (i64, i64) = (240, 160); // smaller: a glitch, not a size someone chose
const SAVE_SIZE_MS: u64 = 600; // a resize settles, then its size is kept

/// (width, height, maximized) kept for `key`.
pub fn saved_size(key: &str) -> Option<(i32, i32, bool)> {
    let stored = sonata_config::stored("windows");
    let st = stored.get(key)?;
    let num = |k: &str| st.get(k).and_then(|v| v.as_f64().or_else(|| v.as_str()?.parse().ok())).map(|f| f as i64);
    let (w, h) = (num("width")?, num("height")?);
    if w < MIN.0 || h < MIN.1 {
        return None;
    }
    Some((w as i32, h as i32, st.get("maximized").and_then(Value::as_bool).unwrap_or(false)))
}

/// Kept for next time (not written when unchanged).
pub fn save_size(key: &str, width: i32, height: i32, maximized: bool) {
    if (width as i64) < MIN.0 || (height as i64) < MIN.1 || saved_size(key) == Some((width, height, maximized)) {
        return;
    }
    let _ = sonata_config::update("windows", &json!({key: {"width": width, "height": height, "maximized": maximized}}));
}

/// (width, height) in logical px of the display `widget` is shown on.
/// Not shown yet (a window before present()): the smallest display, so
/// whatever is sized from it fits whichever display it opens on. None: no
/// display at all.
pub fn screen_size_of(widget: &impl IsA<gtk::Widget>) -> Option<(i32, i32)> {
    let widget = widget.upcast_ref::<gtk::Widget>();
    let display = widget.display();
    let shown = widget.native().and_then(|n| n.surface()).and_then(|s| display.monitor_at_surface(&s));
    if let Some(m) = shown {
        let g = m.geometry();
        return Some((g.width(), g.height()));
    }
    let monitors = display.monitors();
    (0..monitors.n_items())
        .filter_map(|i| monitors.item(i).and_downcast::<gtk::gdk::Monitor>())
        .map(|m| (m.geometry().width(), m.geometry().height()))
        .min_by_key(|(w, h)| w * h)
}

/// The display's size, for a first size that fits it.
fn screen_size(win: &gtk::Window) -> Option<(i32, i32)> {
    let display = WidgetExt::display(win);
    let monitor = win
        .surface()
        .and_then(|s| display.monitor_at_surface(&s))
        .or_else(|| display.monitors().item(0).and_then(|m| m.downcast::<gtk::gdk::Monitor>().ok()))?;
    let g = monitor.geometry();
    Some((g.width(), g.height()))
}

/// The window opens at the size it last had (first time: w x h, never
/// bigger than the display) and maximized if it was; that size is kept when
/// it closes, and shortly after a resize (a restart ends the app without
/// closing its window).
pub fn remember_size(win: &gtk::Window, key: &str, w: i32, h: i32) {
    let (mut w, mut h, maximized) = saved_size(key).unwrap_or((w, h, false));
    if let Some((sw, sh)) = screen_size(win) {
        w = w.min(sw);
        h = h.min(sh);
    }
    win.set_default_size(w, h);
    if maximized {
        win.maximize();
    }
    let key = key.to_string();
    let keep: Rc<dyn Fn(&gtk::Window)> = Rc::new(move |win: &gtk::Window| {
        if win.is_fullscreen() {
            return;
        }
        let (dw, dh) = if win.is_maximized() || !win.is_realized() {
            win.default_size() // GTK keeps it as the unmaximized size
        } else {
            (win.width(), win.height())
        };
        if dw > 0 && dh > 0 {
            save_size(&key, dw, dh, win.is_maximized());
        }
    });
    let k = keep.clone();
    win.connect_close_request(move |win| {
        k(win);
        glib::Propagation::Proceed
    });
    let pending: Rc<Cell<Option<glib::SourceId>>> = Rc::default();
    let soon: Rc<dyn Fn(&gtk::Window)> = Rc::new(move |win: &gtk::Window| {
        if let Some(id) = pending.take() {
            id.remove();
        }
        let (weak, k, p) = (win.downgrade(), keep.clone(), pending.clone());
        pending.set(Some(glib::timeout_add_local_once(Duration::from_millis(SAVE_SIZE_MS), move || {
            p.set(None);
            if let Some(win) = weak.upgrade().filter(|w| w.is_realized()) {
                k(&win);
            }
        })));
    });
    let s = soon.clone();
    win.connect_default_width_notify(move |w| s(w));
    let s = soon.clone();
    win.connect_default_height_notify(move |w| s(w));
    win.connect_maximized_notify(move |w| soon(w));
}

/// set_default_size() that never opens a window bigger than the display.
pub fn fit_default_size(win: &gtk::Window, w: i32, h: i32) {
    let (sw, sh) = screen_size(win).unwrap_or((w, h));
    win.set_default_size(w.min(sw), h.min(sh));
}

// -- tab bars (Terminal, Files, TextEdit) ---------------------------------------------------------
const TABS_KEY: &str = "always_show_tabs";

/// A window's tab bar shows with more than one tab -- or always, the
/// Settings > Appearance choice (on by default, like Safari's "Always show
/// tab bar").
pub fn tab_bar_shown(count: usize) -> bool {
    count > 1 || sonata_config::load("appearance", &json!({TABS_KEY: true})).get(TABS_KEY).and_then(Value::as_bool).unwrap_or(true)
}

/// Call `refresh` again whenever that choice changes; keep the monitors.
pub fn follow_tab_bar<F: Fn() + 'static>(refresh: F) -> Vec<gtk::gio::FileMonitor> {
    sonata_config::watch("appearance", refresh)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(cfg: serde_json::Value) -> crate::frame::Frame {
        crate::frame::frame_from(cfg.as_object().unwrap())
    }

    /// Vini: the 12 px dots were hard to hit. Each takes the room around
    /// it: up to the window's top edge, out to its side edge for the
    /// outermost one, half the gap to its neighbours.
    #[test]
    fn test_top_left_corner_hits_close() {
        let r = reach_of(&frame(json!({})));
        assert_eq!(r, Reach { top: 8.0, edge: 7.0, gap: 4.0, below: 4.0 });
        let (w, h) = (12.0, 12.0);
        // the window's corner, from the outermost dot (left)
        assert!(light_contains(r, Some("left"), w, h, None, -7.0, -8.0));
        assert!(!light_contains(r, Some("left"), w, h, None, -7.5, 0.0));
        // a middle dot: only half a gap each side
        assert!(!light_contains(r, None, w, h, None, -5.0, 0.0));
        assert!(light_contains(r, None, w, h, None, w + 3.9, h + 3.9));
        // a header bar's padding put the dot a pixel further in: the real edge still reaches it
        assert!(light_contains(r, Some("left"), w, h, Some((8.0, 9.0, 300.0)), -8.0, -9.0));
        // ... but not a dot far from the edge
        assert!(!light_contains(r, Some("left"), w, h, Some((40.0, 9.0, 300.0)), -40.0, 0.0));
        // on the right: out to the right edge
        assert!(light_contains(r, Some("right"), w, h, Some((300.0, 8.0, 7.0)), w + 6.9, 0.0));
    }

    /// A bigger button or a rounder corner moves the reach with the dots.
    #[test]
    fn test_reach_follows_button_size() {
        let r = reach_of(&frame(json!({"button_size": 16, "radius": {"window": 14}})));
        // dot_left 17 - 8: the same room from the edge plus the rounder corner's 2
        assert_eq!((r.edge, r.top), (9.0, 10.0));
    }

    #[test]
    fn test_dismiss_labels() {
        for l in ["Cancel", "_Cancel", "Close…", "Don\u{2019}t Allow", "  OK "] {
            assert!(dismiss_label(l), "{l}");
        }
        assert!(!dismiss_label("Restart"));
        assert!(!dismiss_label(""));
    }
}
