//! Panels: popovers with the menu material that hold custom content (menu
//! bar extras like Wi-Fi/Sound/Battery, Control Center, calendars),
//! sonata2/ui/panel.py, its CSS panel.py's own.
//!
//! ```no_run
//! # use gtk::prelude::*;
//! # let (anchor, content) = (gtk::Button::new(), gtk::Box::new(gtk::Orientation::Vertical, 0));
//! use sonata_ui::panel;
//! let pop = panel::popup(&anchor, &content, gtk::PositionType::Bottom);
//! panel::header("Wi-Fi", None);
//! panel::module(&[], 8);                               // Control Center rounded block
//! let t = panel::toggle("bluetooth-active-symbolic", "Bluetooth", true, |_on| {}, "");
//! ```
//!
//! Open panels count with the menus (menu::any_open: auto-hiding
//! surfaces stay put) and a panel that couldn't show is closed again.

use gtk::{glib, prelude::*};
use std::cell::{Cell, RefCell};
use std::rc::Rc;

pub(crate) fn register_css() {
    crate::css::register_module("ui/panel.py");
}

/// How a panel opens (panel.popup's keywords).
#[derive(Clone, Debug)]
pub struct Options {
    pub position: gtk::PositionType,
    pub gap: i32,
    /// left-aligned below the anchor (macOS menus hang from the title's
    /// left edge) instead of GTK's centring
    pub align_start: bool,
    /// a fixed content width (menu bar panels): long names ellipsize
    /// instead of stretching the panel (fixed.rs)
    pub width: Option<i32>,
    /// a close dot at its top corner, the windows' own (panels that are
    /// forms and sheets, like dialogs -- Vini; not the menu bar's or small
    /// tool panels)
    pub close: bool,
    /// not dropped when it closes, to be shown again with reopen()
    /// (Control Center: a new panel around the same content cost ~100 ms
    /// of GTK work before its first frame)
    pub keep: bool,
}

impl Default for Options {
    fn default() -> Self {
        Options { position: gtk::PositionType::Bottom, gap: 4, align_start: false, width: None, close: false, keep: false }
    }
}

/// A panel around `child`, anchored to `anchor`; it goes when it closes.
pub fn popup(anchor: &impl IsA<gtk::Widget>, child: &impl IsA<gtk::Widget>, position: gtk::PositionType) -> gtk::Popover {
    popup_with(anchor, child, Options { position, ..Default::default() })
}

/// [`popup`] with every option.
pub fn popup_with(anchor: &impl IsA<gtk::Widget>, child: &impl IsA<gtk::Widget>, opts: Options) -> gtk::Popover {
    let anchor = anchor.upcast_ref::<gtk::Widget>();
    let pop = gtk::Popover::builder().has_arrow(false).position(opts.position).build();
    pop.add_css_class("sonata-panel");
    let mut child: gtk::Widget = fit_screen(child.upcast_ref(), anchor);
    if let Some(w) = opts.width {
        child = crate::fixed::FixedWidth::new(&child, w).upcast();
    }
    // (one of its own already, or a Cancel / Close / Quit button: none added -- Vini)
    if opts.close && !crate::window::has_window_buttons(&child) && !crate::window::has_dismiss_button(&child) {
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let p = pop.downgrade();
        col.append(&crate::window::close_button(move || {
            if let Some(p) = p.upgrade() {
                p.popdown();
            }
        }));
        col.append(&child);
        child = col.upcast();
    }
    pop.set_child(Some(&child));
    let gap = opts.gap;
    let (x, y) = match opts.position {
        gtk::PositionType::Top => (0, -gap),
        gtk::PositionType::Bottom => (0, gap),
        gtk::PositionType::Left => (-gap, 0),
        _ => (gap, 0),
    };
    pop.set_offset(x, y);
    pop.set_parent(anchor);
    if opts.align_start {
        align_to_start(&pop, anchor, gap);
    }
    KEPT.with(|k| {
        if opts.keep {
            k.borrow_mut().push(pop.downgrade());
        }
    });
    if opts.keep {
        // with its anchor (GTK wants no child left)
        let p = pop.downgrade();
        anchor.connect_destroy(move |_| {
            if let Some(p) = p.upgrade().filter(|p| p.parent().is_some()) {
                p.unparent();
            }
        });
    }
    show(&pop)
}

thread_local! {
    /// panels made with keep: shown again by reopen(), never dropped on close
    static KEPT: RefCell<Vec<glib::WeakRef<gtk::Popover>>> = const { RefCell::new(Vec::new()) };
}

fn kept(pop: &gtk::Popover) -> bool {
    KEPT.with(|k| {
        let mut k = k.borrow_mut();
        k.retain(|w| w.upgrade().is_some());
        k.iter().any(|w| w.upgrade().as_ref() == Some(pop))
    })
}

/// A panel made with `keep` shown again, as a new one would open: its
/// opening animation (CSS plays an animation once: its name alternates).
pub fn reopen(pop: &gtk::Popover) -> gtk::Popover {
    if !pop.get_visible() {
        if pop.has_css_class("reopen") {
            pop.remove_css_class("reopen");
        } else {
            pop.add_css_class("reopen");
        }
        show(pop);
    }
    pop.clone()
}

/// Each opening: focus, a click outside closes it, no hover yet.
fn show(pop: &gtk::Popover) -> gtk::Popover {
    let Some(anchor) = pop.parent() else { return pop.clone() };

    // GTK 4: a widget that can't take focus keeps its descendants from
    // taking it too, and a popover is a child of its anchor -- a text field
    // in a panel hung from a toolbar button (can_focus=false) couldn't be
    // typed in. The chain may take focus while the panel is open (a click
    // still doesn't focus the button: focus_on_click).
    let unlocked: Rc<RefCell<Vec<(gtk::Widget, bool)>>> = Rc::default();
    let mut w = Some(anchor.clone());
    while let Some(x) = w {
        if x.is::<gtk::Root>() {
            break;
        }
        if !x.can_focus() {
            x.set_can_focus(true);
            unlocked.borrow_mut().push((x.clone(), x.gets_focus_on_click()));
            x.set_focus_on_click(false);
        }
        w = x.parent();
    }

    // a click anywhere else in the window closes the panel. GTK's autohide
    // relies on the compositor's popup grab, which can be lost on Wayland (a
    // keyring prompt while saving the Assistant's key): the panel then
    // stayed open over everything (Vini).
    let outside = anchor.root().and_downcast::<gtk::Window>().map(|root| {
        let g = gtk::GestureClick::builder().button(0).propagation_phase(gtk::PropagationPhase::Capture).build();
        let (p, r) = (pop.downgrade(), root.downgrade());
        g.connect_pressed(move |g, _, _, _| {
            // a press on the window itself (not on the panel, or a pop-up
            // button's list inside it: their own surfaces)
            let (Some(p), Some(r)) = (p.upgrade(), r.upgrade()) else { return };
            let surface = g.current_event().and_then(|e| e.surface());
            if p.get_visible() && surface.is_some() && surface == r.surface() {
                p.popdown();
            }
        });
        root.add_controller(g.clone());
        g
    });

    let handler: Rc<Cell<Option<glib::SignalHandlerId>>> = Rc::default();
    let h = handler.clone();
    let id = pop.connect_closed(move |p| {
        if let Some(id) = h.take() {
            p.disconnect(id); // (once: a kept panel opens again)
        }
        if let Some(g) = outside.as_ref() {
            if let Some(w) = g.widget() {
                w.remove_controller(g);
            }
        }
        for (w, foc) in unlocked.borrow_mut().drain(..) {
            w.set_can_focus(false);
            w.set_focus_on_click(foc);
        }
        if !kept(p) {
            let p = p.clone();
            glib::idle_add_local_once(move || p.unparent());
        }
    });
    handler.set(Some(id));
    hold_hover(pop, 2.0);
    crate::menu::track(pop); // open: auto-hiding surfaces stay put
    pop.popup();
    pop.clone()
}

/// A panel never reaches past its display: the menu bar above it, its own
/// padding and a margin below (a popover taller than the screen is cut off).
pub const SCREEN_MARGIN: i32 = 48;

/// The tallest a panel's content may be on a display this tall.
pub fn max_height(screen_h: i32) -> i32 {
    (screen_h - SCREEN_MARGIN).max(200)
}

/// `child` in a scroller that is exactly its size, until the display is
/// too short for it (a long Wi-Fi list, many audio apps on a 720 px
/// laptop): then it scrolls instead of running off the screen.
pub fn fit_screen(child: &gtk::Widget, anchor: &gtk::Widget) -> gtk::Widget {
    let Some((_, h)) = crate::window::screen_size_of(anchor) else { return child.clone() };
    gtk::ScrolledWindow::builder()
        .child(child)
        .hscrollbar_policy(gtk::PolicyType::Never)
        .propagate_natural_width(true)
        .propagate_natural_height(true)
        .max_content_height(max_height(h))
        .css_classes(["panel-scroller"])
        .build()
        .upcast()
}

/// No hover highlight until the pointer moves inside the panel (the
/// "fresh" class): a row that only seemed under the pointer as the panel
/// opened (or as its rows were filled in) lit up, then went dark.
pub fn hold_hover(pop: &gtk::Popover, slack: f64) {
    pop.add_css_class("fresh");
    HOVER.with(|h| {
        let mut h = h.borrow_mut();
        h.retain(|(w, _)| w.upgrade().is_some());
        if let Some((_, seen)) = h.iter().find(|(w, _)| w.upgrade().as_ref() == Some(pop)) {
            seen.set(None); // a kept panel shown again
            return;
        }
        let seen: Rc<Cell<Option<(f64, f64)>>> = Rc::default();
        h.push((pop.downgrade(), seen.clone()));
        let motion = gtk::EventControllerMotion::new();
        let s = seen.clone();
        motion.connect_enter(move |_, x, y| s.set(Some((x, y))));
        let p = pop.downgrade();
        motion.connect_motion(move |_, x, y| match seen.get() {
            None => seen.set(Some((x, y))), // the first report: where GTK thinks it is
            Some((x0, y0)) if (x - x0).abs() + (y - y0).abs() > slack => {
                if let Some(p) = p.upgrade() {
                    p.remove_css_class("fresh");
                }
            }
            _ => {}
        });
        pop.add_controller(motion);
    });
}

type Seen = (glib::WeakRef<gtk::Popover>, Rc<Cell<Option<(f64, f64)>>>);

thread_local! {
    static HOVER: RefCell<Vec<Seen>> = const { RefCell::new(Vec::new()) };
}

thread_local! {
    /// a panel's width around its content (padding, border, shadow room),
    /// as the last one shown had it
    static FRAME: std::cell::Cell<i32> = const { std::cell::Cell::new(0) };
}

/// Left-align a popover below `anchor` (macOS menus hang from the title's
/// left edge) instead of GTK's centring.
pub fn align_to_start(pop: &gtk::Popover, anchor: &gtk::Widget, gap: i32) {
    // placed before it shows: set only once it was on screen, it opened
    // centred and jumped to the left edge (Vini: "thrown to the right")
    // (a popover measures 0 before it's realized: its content's width, plus
    // the frame the last panel had around its content)
    let content = pop.child().map_or(0, |c| c.measure(gtk::Orientation::Horizontal, -1).1);
    if content > 0 && anchor.width() > 0 {
        let natural = content + FRAME.with(|f| f.get());
        pop.set_offset((natural - anchor.width()) / 2, gap);
    }
    let a = anchor.downgrade();
    pop.connect_map(move |p| {
        let (p, a) = (p.downgrade(), a.clone());
        glib::idle_add_local_once(move || {
            let (Some(p), Some(a)) = (p.upgrade(), a.upgrade()) else { return };
            let pw = p.width();
            if let Some(c) = p.child().filter(|_| pw > 0) {
                FRAME.with(|f| f.set((pw - c.width()).max(0)));
            }
            if pw > 0 {
                p.set_offset((pw - a.width()) / 2, gap);
            }
        });
    });
}

pub fn column(children: &[&gtk::Widget], spacing: i32) -> gtk::Box {
    let b = gtk::Box::new(gtk::Orientation::Vertical, spacing);
    for c in children {
        b.append(*c);
    }
    b
}

pub fn header(title: &str, trailing: Option<&gtk::Widget>) -> gtk::Box {
    let b = gtk::Box::builder().css_classes(["panel-header"]).build();
    b.append(&gtk::Label::builder().label(title).xalign(0.0).hexpand(true).build());
    if let Some(t) = trailing {
        b.append(t);
    }
    b
}

pub fn section_title(text: &str) -> gtk::Label {
    gtk::Label::builder().label(text).xalign(0.0).css_classes(["panel-section"]).build()
}

pub fn separator() -> gtk::Box {
    let b = gtk::Box::new(gtk::Orientation::Horizontal, 0);
    b.add_css_class("panel-sep");
    b
}

/// A menu-like row: an icon, a label and a trailing widget; with
/// `on_click`, a button. Returns the row and its label.
pub fn row(icon: Option<&str>, text: &str, trailing: Option<&gtk::Widget>, on_click: Option<Box<dyn Fn()>>) -> (gtk::Widget, gtk::Label) {
    let content = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    if let Some(i) = icon {
        let img = gtk::Image::from_icon_name(i);
        img.set_pixel_size(16);
        content.append(&img);
    }
    let label = gtk::Label::builder().label(text).xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(30).build();
    content.append(&label);
    if let Some(t) = trailing {
        content.append(t);
    }
    match on_click {
        None => {
            content.add_css_class("panel-row");
            content.add_css_class("static");
            (content.upcast(), label)
        }
        Some(f) => {
            let b = gtk::Button::builder().can_focus(false).child(&content).build();
            b.add_css_class("panel-row");
            b.connect_clicked(move |_| f());
            (b.upcast(), label)
        }
    }
}

/// A Control Center rounded block around `children`.
pub fn module(children: &[&gtk::Widget], spacing: i32) -> gtk::Box {
    let b = column(children, spacing);
    b.add_css_class("panel-module");
    b
}

/// A Control Center toggle: its round button, title and caption.
#[derive(Clone)]
pub struct Toggle {
    pub root: gtk::Box,
    pub button: gtk::Button,
    pub caption: gtk::Label,
}

/// Control Center style: round icon toggle + title (+ caption).
pub fn toggle(icon: &str, title: &str, on: bool, on_change: impl Fn(bool) + 'static, caption: &str) -> Toggle {
    let button = gtk::Button::builder().icon_name(icon).css_classes(["panel-toggle"]).valign(gtk::Align::Center).can_focus(false).build();
    if on {
        button.add_css_class("on");
    }
    button.connect_clicked(move |b| {
        let now = !b.has_css_class("on");
        if now {
            b.add_css_class("on");
        } else {
            b.remove_css_class("on");
        }
        on_change(now);
    });
    let root = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    root.append(&button);
    let texts = gtk::Box::builder().orientation(gtk::Orientation::Vertical).valign(gtk::Align::Center).build();
    texts.append(&gtk::Label::builder().label(title).xalign(0.0).css_classes(["panel-module-title"]).build());
    let caption = gtk::Label::builder()
        .label(caption)
        .xalign(0.0)
        .css_classes(["panel-caption"])
        .visible(!caption.is_empty())
        .ellipsize(gtk::pango::EllipsizeMode::End)
        .max_width_chars(16)
        .build();
    texts.append(&caption);
    root.append(&texts);
    Toggle { root, button, caption }
}

/// Update a toggle() without calling its callback.
pub fn set_toggle(t: &Toggle, on: bool, caption: Option<&str>) {
    if on {
        t.button.add_css_class("on");
    } else {
        t.button.remove_css_class("on");
    }
    if let Some(c) = caption {
        t.caption.set_label(c);
        t.caption.set_visible(!c.is_empty());
    }
}

/// The whole `area` (a Control Center module) toggles `t`, not only its
/// round button (Vini: Do Not Disturb's whole square). A click on the
/// button itself is the button's; none while the grid is being edited (an
/// ancestor in edit mode: the "jiggle" class).
pub fn click_anywhere(area: &impl IsA<gtk::Widget>, t: &Toggle) -> gtk::GestureClick {
    let g = gtk::GestureClick::builder().button(gtk::gdk::BUTTON_PRIMARY).build();
    let btn = t.button.downgrade();
    g.connect_released(move |g, _n, x, y| {
        let (Some(btn), Some(area)) = (btn.upgrade(), g.widget()) else { return };
        if let Some(w) = area.pick(x, y, gtk::PickFlags::DEFAULT) {
            if w == *btn.upcast_ref::<gtk::Widget>() || w.is_ancestor(&btn) {
                return;
            }
        }
        let mut p = area.parent();
        while let Some(x) = p {
            if x.has_css_class("jiggle") {
                return; // Edit Controls: the modules don't react
            }
            p = x.parent();
        }
        btn.emit_clicked();
    });
    area.add_controller(g.clone());
    g
}

#[cfg(test)]
mod tests {
    use super::max_height;

    /// A long Wi-Fi list on a 720 px laptop ran off the screen: a panel's
    /// content is never taller than the display minus the menu bar's room.
    #[test]
    fn test_panel_never_taller_than_the_display() {
        assert_eq!(max_height(720), 672);
        assert_eq!(max_height(100), 200); // never a sliver
    }
}

#[cfg(test)]
mod align_tests {
    use gtk::prelude::*;

    /// Vini: a menu bar panel opened centred under its icon, then jumped to
    /// hang from the icon's left edge. Its offset is set before it shows.
    #[test]
    fn test_panel_jumped_to_the_left_edge_after_opening() {
        if (gtk::is_initialized() && !gtk::is_initialized_main_thread()) || gtk::init().is_err() {
            return; // (no display, or GTK is another test thread's)
        }
        let win = gtk::Window::new();
        let anchor = gtk::Button::builder().label("W").width_request(30).halign(gtk::Align::Start).build();
        let row = gtk::Box::builder().width_request(400).build();
        row.append(&anchor);
        win.set_child(Some(&row));
        win.present();
        let ctx = gtk::glib::MainContext::default();
        while anchor.width() == 0 && ctx.iteration(false) {}
        while ctx.pending() {
            ctx.iteration(false);
        }
        if anchor.width() == 0 {
            return; // (not laid out on this display)
        }
        let pop = gtk::Popover::builder().has_arrow(false).build();
        pop.set_child(Some(&gtk::Box::builder().width_request(280).height_request(40).build()));
        pop.set_parent(&anchor);
        super::align_to_start(&pop, anchor.upcast_ref(), 2);
        let (x, y) = pop.offset();
        assert!(x > 100, "offset {x} before showing: it would open centred, then jump");
        assert_eq!(y, 2);
        pop.unparent();
        win.destroy();
    }
}
