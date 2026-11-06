//! Context menus (macOS Big Sur menus), sonata2/ui/menu.py: a GtkPopoverMenu
//! from sections of items, its CSS menu.py's own. Items can carry a check
//! (shown after the text, at the right edge, as menu.py does) or a submenu
//! (opening to the side), or a small x at the right edge that does
//! something else (`Item::on_close`: the Dock closes that window), or be
//! a widget of their own (`Item::widget`: Files' tag colours).
//! Options::passthrough: a click elsewhere in the window closes the menu
//! and does its own thing too (Files' right-click).
//!
//! ```no_run
//! # let widget = gtk::Label::new(None);
//! use sonata_ui::menu::{popup, Item, Options};
//! popup(&widget, vec![vec![Item::new("Delete Alarm", || println!("gone"))]], Options::at(10.0, 4.0));
//! ```

use gtk::{gio, glib, prelude::*};
use std::rc::Rc;

pub(crate) fn register_css() {
    crate::css::register_module("ui/menu.py");
}

pub struct Item {
    pub label: String,
    action: Option<Rc<dyn Fn()>>,
    toggle: Option<Rc<dyn Fn(bool)>>,
    checked: Option<bool>,
    pub enabled: bool,
    submenu: Vec<Vec<Item>>,
    on_close: Option<Rc<dyn Fn()>>,
    widget: Option<Box<dyn FnOnce(&gtk::PopoverMenu) -> gtk::Widget>>,
}

/// A row's text is at most this many characters wide (~320 px at 13 px),
/// then ends in "…" (menu.py's LABEL_CHARS: a Chrome window titled with a
/// whole YouTube video name made the Window menu 830 px wide).
pub const LABEL_CHARS: i32 = 40;

impl Item {
    /// A plain item.
    pub fn new<F: Fn() + 'static>(label: &str, action: F) -> Self {
        Item { label: label.to_string(), action: Some(Rc::new(action)), toggle: None, checked: None, enabled: true, submenu: Vec::new(), on_close: None, widget: None }
    }

    /// A checkmark item; `action` gets the new state.
    pub fn check<F: Fn(bool) + 'static>(label: &str, checked: bool, action: F) -> Self {
        Item { label: label.to_string(), action: None, toggle: Some(Rc::new(action)), checked: Some(checked), enabled: true, submenu: Vec::new(), on_close: None, widget: None }
    }

    /// An item opening a submenu (sections of items) to the side.
    pub fn submenu(label: &str, sections: Vec<Vec<Item>>) -> Self {
        Item { label: label.to_string(), action: None, toggle: None, checked: None, enabled: true, submenu: sections, on_close: None, widget: None }
    }

    /// A small x at the right of a plain item's row: `f` runs and the row
    /// goes, the menu stays open (the Dock: close that window).
    pub fn on_close<F: Fn() + 'static>(mut self, f: F) -> Self {
        self.on_close = Some(Rc::new(f));
        self
    }

    /// A row that is a widget of its own (menu.py's Item(widget=)): Files'
    /// row of tag colours. `make` gets the menu, to close it from a click;
    /// buttons in it want `one_click`, or they take two clicks.
    pub fn widget<F: FnOnce(&gtk::PopoverMenu) -> gtk::Widget + 'static>(label: &str, make: F) -> Self {
        Item { label: label.to_string(), action: None, toggle: None, checked: None, enabled: true, submenu: Vec::new(), on_close: None, widget: Some(Box::new(make)) }
    }

    pub fn enabled(mut self, on: bool) -> Self {
        self.enabled = on;
        self
    }
}

/// Where the menu opens: above / below its anchor, or at a point in it
/// (a context menu, its top-left corner there, as macOS).
pub struct Options {
    pub position: gtk::PositionType,
    pub at: Option<(f64, f64)>,
    pub glass: bool,
    /// (menus inside an app window) a click elsewhere in the window closes
    /// the menu *and* does its own thing -- selects, opens, right-clicks
    /// another file -- instead of only closing the menu
    pub passthrough: bool,
    /// false: on a surface that got no click of its own (a menu for a
    /// window in Mission Control), which closes it itself
    pub autohide: bool,
}

impl Options {
    pub fn at(x: f64, y: f64) -> Self {
        Options { position: gtk::PositionType::Bottom, at: Some((x, y)), glass: true, passthrough: false, autohide: true }
    }

    pub fn below() -> Self {
        Options { position: gtk::PositionType::Bottom, at: None, glass: true, passthrough: false, autohide: true }
    }

    pub fn above() -> Self {
        Options { position: gtk::PositionType::Top, at: None, glass: true, passthrough: false, autohide: true }
    }

    pub fn passthrough(mut self) -> Self {
        self.passthrough = true;
        self
    }

    pub fn no_autohide(mut self) -> Self {
        self.autohide = false;
        self
    }
}

// -- menus and panels open now (ui.menu.OPEN): auto-hiding surfaces stay put -------------------
thread_local! {
    static OPEN: std::cell::RefCell<Vec<glib::WeakRef<gtk::Popover>>> = const { std::cell::RefCell::new(Vec::new()) };
}

/// A menu or panel of this process is open (the menu bar and the Dock
/// don't hide under it).
pub fn any_open() -> bool {
    OPEN.with(|o| {
        let mut o = o.borrow_mut();
        o.retain(|w| w.upgrade().is_some_and(|p| p.get_visible()));
        !o.is_empty()
    })
}

thread_local! {
    static ON_CLOSED: std::cell::RefCell<Vec<(u64, Rc<dyn Fn()>)>> = const { std::cell::RefCell::new(Vec::new()) };
}

/// `callback()` after any menu or panel of this process closes (an
/// auto-hiding Dock or menu bar may hide again); the id for [`off_closed`].
pub fn on_closed(callback: impl Fn() + 'static) -> u64 {
    thread_local!(static NEXT: std::cell::Cell<u64> = const { std::cell::Cell::new(1) });
    let id = NEXT.with(|n| n.replace(n.get() + 1));
    ON_CLOSED.with(|l| l.borrow_mut().push((id, Rc::new(callback))));
    id
}

pub fn off_closed(id: u64) {
    ON_CLOSED.with(|l| l.borrow_mut().retain(|(i, _)| *i != id));
}

/// Count `pop` as open until it closes (panel.rs's panels too: a kept
/// panel each time it opens), and close it again when it couldn't show.
pub fn track(pop: &impl IsA<gtk::Popover>) {
    let pop = pop.as_ref();
    OPEN.with(|o| {
        let mut o = o.borrow_mut();
        if !o.iter().any(|w| w.upgrade().as_ref() == Some(pop)) {
            o.push(pop.downgrade());
        }
    });
    if unsafe { pop.data::<bool>("sonata-tracked") }.is_none() {
        unsafe { pop.set_data("sonata-tracked", true) };
        pop.connect_closed(|p| {
            let p = p.clone();
            OPEN.with(|o| o.borrow_mut().retain(|w| w.upgrade().is_some_and(|x| x != p)));
            // (outside the borrow: a callback may open another menu)
            let cbs: Vec<Rc<dyn Fn()>> = ON_CLOSED.with(|l| l.borrow().iter().map(|(_, c)| c.clone()).collect());
            for cb in cbs {
                cb();
            }
        });
    }
    watch_shown(pop);
}

/// How long a menu has to show before it is taken for one that can't.
pub const SHOWN_WITHIN_MS: u64 = 400;

/// A menu or panel that never shows (GTK: "Tried to map a grabbing popup
/// with a non-top most parent" -- opened over another one) is closed
/// again: it held the menu bar's clicks while invisible (Vini: the menu
/// bar stopped responding).
pub fn watch_shown(pop: &impl IsA<gtk::Popover>) {
    let w = pop.as_ref().downgrade();
    glib::timeout_add_local_once(std::time::Duration::from_millis(SHOWN_WITHIN_MS), move || {
        if let Some(p) = w.upgrade() {
            if p.get_visible() && !p.is_mapped() {
                eprintln!("sonata2: a menu that couldn't show was closed");
                p.popdown();
            }
        }
    });
}

/// A long row ends in "…" at LABEL_CHARS and hovering it shows the whole
/// text. GTK makes the rows' labels itself (nested submenus' too, already
/// there under the row that opens them), so they are capped here, before
/// the menu first shows at its full width.
fn cap_labels(w: &gtk::Widget) {
    let mut c = w.first_child();
    while let Some(x) = c {
        if let Some(l) = x.downcast_ref::<gtk::Label>() {
            let in_row = l.parent().is_some_and(|p| p.css_name() == "modelbutton");
            if in_row && !l.has_css_class("accelerator") && l.ellipsize() == gtk::pango::EllipsizeMode::None {
                l.set_ellipsize(gtk::pango::EllipsizeMode::End);
                l.set_max_width_chars(LABEL_CHARS);
                // only a cut row gets a tooltip: a short one would repeat itself
                l.set_has_tooltip(true);
                l.connect_query_tooltip(|l, _, _, _, tip| {
                    if l.layout().is_ellipsized() {
                        tip.set_text(Some(&l.label()));
                        return true;
                    }
                    false
                });
            }
        }
        cap_labels(&x);
        c = x.next_sibling();
    }
}

fn popovers(w: &gtk::Widget, out: &mut Vec<gtk::Popover>) {
    if let Some(p) = w.downcast_ref::<gtk::Popover>() {
        out.push(p.clone());
    }
    let mut c = w.first_child();
    while let Some(x) = c {
        popovers(&x, out);
        c = x.next_sibling();
    }
}

/// GTK keeps the last hovered row selected after the pointer leaves the
/// menu; macOS clears it. Nested submenus are descendants of `pop`, so
/// moving into one isn't a leave.
fn hover_only(pop: &gtk::PopoverMenu) {
    let mut all = Vec::new();
    popovers(pop.upcast_ref(), &mut all);
    for p in all {
        let m = gtk::EventControllerMotion::new();
        let (a, b) = (p.downgrade(), p.downgrade());
        m.connect_leave(move |_| {
            if let Some(p) = a.upgrade() {
                p.add_css_class("pointer-out");
            }
        });
        m.connect_enter(move |_, _, _| {
            if let Some(p) = b.upgrade() {
                p.remove_css_class("pointer-out");
            }
        });
        p.add_controller(m);
    }
    let keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
    let w = pop.downgrade();
    keys.connect_key_pressed(move |_, _, _, _| {
        if let Some(pop) = w.upgrade() {
            let mut all = Vec::new();
            popovers(pop.upcast_ref(), &mut all);
            for q in all {
                q.remove_css_class("pointer-out");
            }
        }
        glib::Propagation::Proceed
    });
    pop.add_controller(keys);
}

fn scrolled(w: &gtk::Widget, out: &mut Vec<gtk::ScrolledWindow>) {
    let mut c = w.first_child();
    while let Some(x) = c {
        if let Some(sw) = x.downcast_ref::<gtk::ScrolledWindow>() {
            out.push(sw.clone());
        }
        scrolled(&x, out);
        c = x.next_sibling();
    }
}

/// macOS menus never scroll: every item stays visible. The compositor may
/// shrink a popup that doesn't fit below its anchor (GTK then adds a
/// scrollbar); the menu is moved up by the missing height and shown again
/// at full size.
fn no_scroll(pop: &gtk::PopoverMenu) {
    let slides = Rc::new(std::cell::Cell::new(0));
    let check = {
        let w = pop.downgrade();
        Rc::new(move || {
            let Some(pop) = w.upgrade() else { return };
            let mut sws = Vec::new();
            scrolled(pop.upcast_ref(), &mut sws);
            let missing = sws
                .iter()
                .filter(|sw| sw.is_mapped())
                .map(|sw| {
                    let adj = sw.vadjustment();
                    adj.upper() - adj.page_size()
                })
                .filter(|m| *m > 1.0)
                .fold(0.0_f64, f64::max) as i32;
            if missing > 0 && slides.get() < 3 {
                slides.set(slides.get() + 1);
                let (x, y) = pop.offset();
                pop.set_offset(x, y - missing);
                pop.present();
            }
        })
    };
    let c = check.clone();
    pop.connect_map(move |_| {
        let c = c.clone();
        glib::timeout_add_local_once(std::time::Duration::from_millis(30), move || c());
    });
    let mut sws = Vec::new();
    scrolled(pop.upcast_ref(), &mut sws);
    for sw in sws {
        let c = check.clone();
        sw.vadjustment().connect_changed(move |_| {
            let c = c.clone();
            glib::idle_add_local_once(move || c());
        });
    }
}

/// Clicks elsewhere in `root` close the menu and still do their own thing.
fn pass_clicks(pop: &gtk::PopoverMenu, root: Option<gtk::Root>) {
    let Some(root) = root.and_downcast::<gtk::Window>() else { return };
    pop.set_autohide(false); // no grab: clicks reach the window
    let click = gtk::GestureClick::builder().button(0).propagation_phase(gtk::PropagationPhase::Capture).build();
    let (p, r) = (pop.downgrade(), root.downgrade());
    click.connect_pressed(move |g, _, _, _| {
        // popovers are children of the window in the widget tree, so clicks
        // on the menu itself pass here too: only the window's own surface counts
        let (Some(pop), Some(root)) = (p.upgrade(), r.upgrade()) else { return };
        if g.current_event().and_then(|e| e.surface()) == root.surface() {
            pop.popdown(); // not claimed: the click goes on
        }
    });
    let keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
    let p = pop.downgrade();
    keys.connect_key_pressed(move |_, k, _, _| {
        if k == gtk::gdk::Key::Escape {
            if let Some(pop) = p.upgrade() {
                pop.popdown();
            }
            return glib::Propagation::Stop;
        }
        glib::Propagation::Proceed
    });
    root.add_controller(click.clone());
    root.add_controller(keys.clone());
    // another window took over (a click in another app): close. The pointer
    // moving onto the menu can briefly take focus from the window on some
    // compositors -- that must not close it: checked a moment later, and
    // only when the pointer isn't on the menu
    let inside = Rc::new(std::cell::Cell::new(false));
    let m = gtk::EventControllerMotion::new();
    let (i1, i2) = (inside.clone(), inside.clone());
    m.connect_enter(move |_, _, _| i1.set(true));
    m.connect_leave(move |_| i2.set(false));
    pop.add_controller(m);
    let p = pop.downgrade();
    let active = root.connect_is_active_notify(move |w| {
        if w.is_active() {
            return;
        }
        let (p, w, inside) = (p.clone(), w.downgrade(), inside.clone());
        glib::timeout_add_local_once(std::time::Duration::from_millis(250), move || {
            if let (Some(pop), Some(w)) = (p.upgrade(), w.upgrade()) {
                if !w.is_active() && !inside.get() && pop.get_visible() {
                    pop.popdown();
                }
            }
        });
    });
    let handler = std::cell::RefCell::new(Some(active));
    let r = root.downgrade();
    pop.connect_closed(move |_| {
        if let Some(root) = r.upgrade() {
            root.remove_controller(&click);
            root.remove_controller(&keys);
            if let Some(h) = handler.borrow_mut().take() {
                root.disconnect(h);
            }
        }
    });
}

/// Rows the model only names (`custom`): made as widgets once the
/// popover exists.
type Customs = Vec<(String, Item)>;

fn build(sections: Vec<Vec<Item>>, group: &gio::SimpleActionGroup, prefix: &str, customs: &mut Customs) -> gio::Menu {
    let model = gio::Menu::new();
    for (s_i, section) in sections.into_iter().enumerate() {
        let part = gio::Menu::new();
        for (i_i, item) in section.into_iter().enumerate() {
            let name = format!("{prefix}{s_i}_{i_i}");
            if !item.submenu.is_empty() {
                let sub = build(item.submenu, group, &format!("{name}_"), customs);
                let mi = gio::MenuItem::new_submenu(Some(&item.label), &sub);
                if !item.enabled {
                    // a disabled submenu: its row points at an action that is off
                    let off = gio::SimpleAction::new(&name, None);
                    off.set_enabled(false);
                    group.add_action(&off);
                    mi.set_attribute_value("submenu-action", Some(&format!("m.{name}").to_variant()));
                }
                part.append_item(&mi);
                continue;
            }
            if item.on_close.is_some() || item.widget.is_some() {
                // a row with its own x, or a custom row: a widget of its own
                // in the menu, its place named in the model (added without
                // one, GTK dropped it)
                let mi = gio::MenuItem::new(None, None);
                mi.set_attribute_value("custom", Some(&name.to_variant()));
                part.append_item(&mi);
                customs.push((name, item));
                continue;
            }
            let action = match item.checked {
                None => {
                    let a = gio::SimpleAction::new(&name, None);
                    if let Some(f) = item.action.clone() {
                        a.connect_activate(move |_, _| f());
                    }
                    a
                }
                Some(on) => {
                    let a = gio::SimpleAction::new_stateful(&name, None, &on.to_variant());
                    if let Some(f) = item.toggle.clone() {
                        a.connect_activate(move |a, _| {
                            let now = a.state().and_then(|s| s.get::<bool>()).unwrap_or(false);
                            f(!now);
                        });
                    }
                    a
                }
            };
            action.set_enabled(item.enabled);
            group.add_action(&action);
            part.append(Some(&item.label), Some(&format!("m.{name}")));
        }
        model.append_section(None, &part);
    }
    model
}

/// A menu row with a small x on the right: the label does the item's
/// action (and closes the menu); the x runs on_close and removes the row.
fn closable_row(pop: &gtk::PopoverMenu, item: Item) -> gtk::Widget {
    let row = gtk::Box::builder().css_classes(["sonata-menu-row"]).hexpand(true).build();
    let label = gtk::Label::builder().label(&item.label).xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(LABEL_CHARS).build();
    let main = gtk::Button::builder().child(&label).css_classes(["sonata-menu-row-label"]).hexpand(true).can_focus(false).sensitive(item.enabled).build();
    let (p, act) = (pop.downgrade(), item.action.clone());
    one_click(&main, move || {
        if let Some(p) = p.upgrade() {
            p.popdown();
        }
        if let Some(f) = &act {
            f();
        }
    });
    let x = gtk::Button::builder().icon_name("window-close-symbolic").css_classes(["sonata-menu-x"]).valign(gtk::Align::Center).tooltip_text(sonata_i18n::tr!("Close")).can_focus(false).build();
    let (r, close) = (row.downgrade(), item.on_close.clone());
    one_click(&x, move || {
        if let Some(f) = &close {
            f();
        }
        if let Some(r) = r.upgrade() {
            r.set_visible(false);
        }
    });
    row.append(&main);
    row.append(&x);
    row.upcast()
}

/// Inside a PopoverMenu the menu's own gestures see the press first, so a
/// plain "clicked" needed a second click: take the click in the capture
/// phase and act on release over the button.
pub fn one_click(button: &gtk::Button, action: impl Fn() + 'static) {
    let g = gtk::GestureClick::builder().button(1).propagation_phase(gtk::PropagationPhase::Capture).build();
    g.connect_pressed(|g, _, _, _| {
        g.set_state(gtk::EventSequenceState::Claimed);
    });
    let b = button.downgrade();
    g.connect_released(move |_, _, x, y| {
        if b.upgrade().is_some_and(|b| b.get_sensitive() && b.contains(x, y)) {
            action();
        }
    });
    button.add_controller(g);
}

/// Checkmark rows: the text starts where every other row's does and the
/// check follows it at the right edge (Vini). GTK's own indicator (hidden
/// by CSS) still carries the state; the image mirrors it.
fn checks_after_text(w: &gtk::Widget) {
    let mut c = w.first_child();
    while let Some(child) = c {
        if child.css_name() == "modelbutton" {
            check_row(&child);
            // (an indicator GTK only makes when the row shows: watched
            // once per row, not once per opening)
            if unsafe { child.data::<bool>("sonata-check-watch") }.is_none() {
                unsafe { child.set_data("sonata-check-watch", true) };
                let row = child.downgrade();
                child.connect_map(move |_| {
                    let row = row.clone();
                    glib::idle_add_local_once(move || {
                        if let Some(r) = row.upgrade() {
                            check_row(&r);
                        }
                    });
                });
            }
        }
        checks_after_text(&child); // also a nested submenu, a popover under its row
        c = child.next_sibling();
    }
}

fn check_row(btn: &gtk::Widget) {
    let has_ours = btn.last_child().is_some_and(|l| l.has_css_class("sonata-menu-check"));
    if has_ours {
        return;
    }
    let Some(boxed) = btn.first_child() else { return };
    let mut c = boxed.first_child();
    let mut ind = None;
    while let Some(x) = c {
        if x.css_name() == "check" || x.css_name() == "radio" {
            ind = Some(x.clone());
            break;
        }
        c = x.next_sibling();
    }
    let Some(ind) = ind else { return };
    let img = gtk::Image::from_icon_name("object-select-symbolic");
    img.add_css_class("sonata-menu-check");
    img.set_halign(gtk::Align::End);
    img.set_hexpand(true);
    img.insert_before(btn, None::<&gtk::Widget>); // last: after the text (and its shortcut)
    let sync = {
        let img = img.clone();
        move |ind: &gtk::Widget| img.set_opacity(if ind.state_flags().contains(gtk::StateFlags::CHECKED) { 1.0 } else { 0.0 })
    };
    sync(&ind);
    ind.connect_state_flags_changed(move |i, _| sync(i));
}

/// Show a menu anchored to `widget`; it cleans itself up when closed.
pub fn popup(widget: &impl IsA<gtk::Widget>, sections: Vec<Vec<Item>>, opts: Options) -> gtk::PopoverMenu {
    let group = gio::SimpleActionGroup::new();
    let mut customs = Customs::new();
    let model = build(sections, &group, "i", &mut customs);
    let pop = gtk::PopoverMenu::from_model_full(&model, gtk::PopoverMenuFlags::NESTED);
    // on the menu, not the anchor: two menus on one anchor would clash
    pop.insert_action_group("m", Some(&group));
    for (name, mut item) in customs {
        let row = match item.widget.take() {
            Some(make) => make(&pop),
            None => closable_row(&pop, item),
        };
        pop.add_child(&row, &name);
    }
    pop.set_has_arrow(false);
    if opts.glass {
        pop.add_css_class("glass");
    }
    pop.set_position(opts.position);
    pop.set_offset(0, if opts.position == gtk::PositionType::Top { -6 } else { 6 });
    pop.set_parent(widget);
    if let Some((x, y)) = opts.at {
        pop.set_pointing_to(Some(&gtk::gdk::Rectangle::new(x as i32, y as i32, 1, 1)));
        pop.set_position(gtk::PositionType::Bottom);
        pop.set_halign(gtk::Align::Start);
        pop.set_offset(0, 2);
    }
    pop.connect_closed(|p| {
        let p = p.clone();
        glib::idle_add_local_once(move || p.unparent());
    });
    if opts.passthrough {
        pass_clicks(&pop, widget.as_ref().root());
    }
    no_scroll(&pop);
    hover_only(&pop);
    pop.connect_map(|p| checks_after_text(p.upcast_ref()));
    cap_labels(pop.upcast_ref());
    if !opts.passthrough {
        pop.set_autohide(opts.autohide);
    }
    track(&pop);
    pop.popup();
    pop
}

#[cfg(test)]
mod visible_tests {
    use super::*;

    /// A menu still open whose button sat in a hidden box counted as
    /// closed (is_visible: the popover and every parent), so the menu bar
    /// and the Dock could hide under it. Python asks the menu itself.
    #[test]
    fn test_menu_over_a_hidden_box_counted_as_closed() {
        if !crate::gtk_for_tests() {
            return; // (no display)
        }
        let win = gtk::Window::new();
        let hidden = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        let button = gtk::Button::new();
        hidden.append(&button);
        win.set_child(Some(&hidden));
        win.present();
        let pop = gtk::Popover::new();
        pop.set_parent(&button);
        track(&pop);
        pop.popup();
        hidden.set_visible(false); // (the menu stays up: GTK leaves it to its owner)
        assert!(pop.get_visible());
        assert!(any_open());
        pop.popdown();
        pop.unparent();
        win.destroy();
    }
}
