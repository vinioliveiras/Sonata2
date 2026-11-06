//! Settings > Keyboard Shortcuts (shortcuts_page.py): every shortcut
//! Sonata sets up (macOS- and Windows-style ones side by side), changed by
//! pressing the new keys.
//!
//! A row shows the current shortcuts as key caps; clicking it records a new
//! one (it replaces them), and its "…" menu adds another one, turns it off
//! or puts Sonata's back. While a shortcut is being recorded the desktop's
//! own shortcuts are held back (keyboard-shortcuts-inhibit), so Super+D can
//! be pressed without showing the desktop. The logic: sonata_tweaks::shortcuts.

use crate::window::{Ctx, Settings};
use adw::prelude::*;
use gtk::{gdk, glib};
use sonata_i18n::{fmt, gettext, tr};
use sonata_tweaks::shortcuts::{self as sc, Shortcut, GROUPS, MODS, SHORTCUTS};
use sonata_ui::menu::{Item, Options};
use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::{Rc, Weak};

/// shortcuts.py's MOD_LABEL: a modifier as it is shown while recording.
fn mod_label(m: &str) -> &'static str {
    match m {
        "<ctrl>" => "Ctrl",
        "<alt>" => "Alt",
        "<shift>" => "Shift",
        _ => "Super",
    }
}

/// The shortcuts as key caps, each alternative on its own line ("or"
/// before it), right-aligned: side by side, "Ctrl+Super+→ or Alt+Super+→"
/// made Keyboard the one section wider than a narrow Settings window.
fn keys(binding: &str) -> gtk::Box {
    let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(4).valign(gtk::Align::Center).css_classes(["sc-keys"]).build();
    let combos = sc::split(binding);
    if combos.is_empty() {
        bx.append(&gtk::Label::builder().label(tr!("Off")).css_classes(["sc-text"]).halign(gtk::Align::End).build());
    }
    for (i, combo) in combos.iter().enumerate() {
        let line = gtk::Box::builder().spacing(6).halign(gtk::Align::End).build();
        if i > 0 {
            line.append(&gtk::Label::builder().label(tr!("or")).css_classes(["sc-or"]).build());
        }
        // a gesture's words wrap: on one line ("Swipe up with 3 fingers",
        // longer in Portuguese) they made the row wider than a narrow window
        match sc::accelerator(combo) {
            Some(accel) => line.append(&gtk::ShortcutLabel::builder().accelerator(accel).valign(gtk::Align::Center).build()),
            None => line.append(&gtk::Label::builder().label(sc::describe(combo)).css_classes(["sc-text"]).wrap(true).justify(gtk::Justification::Right).xalign(1.0).build()),
        }
        bx.append(&line);
    }
    bx
}

/// The row's title breaks between words only. Adw breaks it anywhere: in
/// a narrow window the key caps took the room and "Overview" came out one
/// letter per line ("V-is-ão G-e-ral").
fn whole_words(row: &impl IsA<gtk::Widget>) {
    let mut stack: Vec<gtk::Widget> = vec![row.clone().upcast()];
    while let Some(w) = stack.pop() {
        if let Some(l) = w.downcast_ref::<gtk::Label>().filter(|l| l.has_css_class("title")) {
            l.set_wrap_mode(gtk::pango::WrapMode::Word);
            return;
        }
        let mut c = w.first_child();
        while let Some(ch) = c {
            c = ch.next_sibling();
            stack.push(ch);
        }
    }
}

struct Row {
    holder: gtk::Box,
}

pub struct ShortcutsPage {
    win: Weak<Settings>,
    me: Weak<ShortcutsPage>,
    rows: RefCell<HashMap<&'static str, Row>>,
}

impl ShortcutsPage {
    pub fn new(ctx: &Ctx) -> Rc<Self> {
        sonata_ui::css::register_module("settings/shortcuts_page.py");
        Rc::new_cyclic(|me| ShortcutsPage { win: Rc::downgrade(ctx), me: me.clone(), rows: RefCell::new(HashMap::new()) })
    }

    pub fn groups(&self) -> Vec<adw::PreferencesGroup> {
        let top = adw::PreferencesGroup::builder().description(tr!("Click a shortcut to change it, then press the new keys. Super- and Ctrl-style shortcuts both work.")).build();
        let mut out = vec![top];
        for name in GROUPS {
            let g = adw::PreferencesGroup::builder().title(gettext(name)).build();
            for s in SHORTCUTS.iter().filter(|s| s.group == name) {
                g.add(&self.row(s));
            }
            out.push(g);
        }
        let reset = adw::PreferencesGroup::new();
        let me = self.me.clone();
        let btn = sonata_ui::controls::push_button(&tr!("Restore Defaults"), move || {
            if let Some(p) = me.upgrade() {
                p.reset_all();
            }
        }, "");
        btn.set_halign(gtk::Align::Start);
        reset.add(&btn);
        out.push(reset);
        out
    }

    // -- rows ----------------------------------------------------------------------------------------
    fn row(&self, s: &'static Shortcut) -> adw::ActionRow {
        let row = adw::ActionRow::builder().title(glib::markup_escape_text(&gettext(s.title))).activatable(true).build();
        let me = self.me.clone();
        row.connect_activated(move |_| {
            if let Some(p) = me.upgrade() {
                p.record(s, false);
            }
        });
        whole_words(&row);
        let holder = gtk::Box::builder().valign(gtk::Align::Center).build(); // the key caps, before the "…" button
        row.add_suffix(&holder);
        let more = gtk::Button::builder().icon_name("view-more-symbolic").valign(gtk::Align::Center).css_classes(["flat"]).tooltip_text(tr!("More")).build();
        let me = self.me.clone();
        more.connect_clicked(move |b| {
            if let Some(p) = me.upgrade() {
                p.menu(b, s);
            }
        });
        row.add_suffix(&more);
        self.rows.borrow_mut().insert(s.id, Row { holder });
        self.fill(s);
        row
    }

    fn fill(&self, s: &Shortcut) {
        let rows = self.rows.borrow();
        let Some(r) = rows.get(s.id) else { return };
        if let Some(old) = r.holder.first_child() {
            r.holder.remove(&old);
        }
        r.holder.append(&keys(&sc::current(s)));
    }

    fn menu(&self, button: &gtk::Button, s: &'static Shortcut) {
        let is_default = sc::split(&sc::current(s)) == sc::split(&sc::default(s));
        let on = !sc::split(&sc::current(s)).is_empty();
        let call = |f: fn(&ShortcutsPage, &'static Shortcut)| {
            let me = self.me.clone();
            move || {
                if let Some(p) = me.upgrade() {
                    f(&p, s);
                }
            }
        };
        sonata_ui::menu::popup(
            button,
            vec![
                vec![Item::new(&tr!("Change Shortcut…"), call(|p, s| p.record(s, false))), Item::new(&tr!("Add Another Shortcut…"), call(|p, s| p.record(s, true)))],
                vec![
                    Item::new(&tr!("Turn Off"), call(|p, s| p.set(s, &[]))).enabled(on),
                    Item::new(&tr!("Use the Default"), call(|p, s| {
                        sc::reset(s);
                        p.fill(s);
                    }))
                    .enabled(!is_default),
                ],
            ],
            Options::below(),
        );
    }

    fn set(&self, s: &Shortcut, combos: &[String]) {
        sc::set_binding(s, combos);
        self.fill(s);
    }

    fn reset_all(&self) {
        let Some(win) = self.win.upgrade() else { return };
        let me = self.me.clone();
        sonata_ui::dialog::alert(
            &tr!("Restore the default shortcuts?"),
            &tr!("Every shortcut you changed goes back to Sonata's."),
            &[("cancel", tr!("Cancel"), ""), ("reset", tr!("Restore"), "destructive")],
            move |rid| {
                let (true, Some(p)) = (rid == "reset", me.upgrade()) else { return };
                for s in SHORTCUTS.iter() {
                    sc::reset(s);
                    p.fill(s);
                }
                if let Some(w) = p.win.upgrade() {
                    w.toast(&tr!("Shortcuts restored"));
                }
            },
            win.parent().as_ref(),
        );
    }

    // -- recording -----------------------------------------------------------------------------------
    pub fn record(&self, s: &'static Shortcut, add: bool) {
        let Some(win) = self.win.upgrade() else { return };
        let shown = gtk::Label::builder().label("…").css_classes(["sc-capture"]).build();
        let dlg = sonata_ui::dialog::alert(&gettext(s.title), &tr!("Press the new shortcut. Esc cancels."), &[("cancel", tr!("Cancel"), "")], |_| {}, win.parent().as_ref());
        dlg.set_extra_child(Some(&shown));
        // Super+D reaches us, not the desktop
        let surface = win.window.surface().and_downcast::<gdk::Toplevel>();
        if let Some(t) = &surface {
            t.inhibit_system_shortcuts(None::<&gdk::Event>);
        }
        dlg.connect_closed(move |_| {
            if let Some(t) = &surface {
                t.restore_system_shortcuts();
            }
        });
        let keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
        let (me, d) = (self.me.clone(), dlg.downgrade());
        keys.connect_key_pressed(move |_, keyval, keycode, state| {
            let m = gdk::ModifierType::from_bits_truncate(state.bits());
            let (ctrl, shift, alt, sup) = (m.contains(gdk::ModifierType::CONTROL_MASK), m.contains(gdk::ModifierType::SHIFT_MASK), m.contains(gdk::ModifierType::ALT_MASK), m.contains(gdk::ModifierType::SUPER_MASK));
            if keyval == gdk::Key::Escape && !(ctrl || shift || alt || sup) {
                return glib::Propagation::Proceed; // the dialog cancels
            }
            let Some(combo) = sc::combo_from_key(keycode, ctrl, shift, alt, sup) else {
                // a modifier so far: show it
                let held: Vec<&str> = MODS.iter().zip([ctrl, alt, shift, sup]).filter(|(_, on)| *on).map(|(m, _)| mod_label(m)).collect();
                shown.set_label(&if held.is_empty() { "…".to_string() } else { held.join("+") });
                return glib::Propagation::Stop;
            };
            if let Some(p) = me.upgrade() {
                p.apply(s, &combo, add);
            }
            if let Some(d) = d.upgrade() {
                d.close();
            }
            glib::Propagation::Stop
        });
        dlg.add_controller(keys);
    }

    /// One combo, one shortcut: taken from another one if it had it.
    fn apply(&self, s: &'static Shortcut, combo: &str, add: bool) {
        if let Some(other) = sc::owner(combo, Some(s)) {
            let keep: Vec<String> = sc::split(&sc::current(other)).into_iter().filter(|c| c != combo).collect();
            sc::set_binding(other, &keep);
            self.fill(other);
            if let Some(w) = self.win.upgrade() {
                w.toast(&fmt(&tr!("{keys} was removed from “{name}”"), &[("keys", &sc::describe(combo)), ("name", &gettext(other.title))]));
            }
        }
        let mut combos = if add { sc::split(&sc::current(s)) } else { Vec::new() };
        if !combos.iter().any(|c| c == combo) {
            combos.push(combo.to_string());
        }
        self.set(s, &combos);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_group_has_shortcuts() {
        for g in GROUPS {
            assert!(SHORTCUTS.iter().any(|s| s.group == g), "{g}");
        }
        assert_eq!(mod_label("<super>"), "Super");
    }
}
