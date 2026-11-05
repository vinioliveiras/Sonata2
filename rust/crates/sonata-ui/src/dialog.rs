//! Alerts (macOS Big Sur alert: bold heading, body, buttons at the bottom),
//! sonata2/ui/dialog.py, its CSS dialog.py's own.
//!
//! Default button (Return): the response styled "default"; otherwise the
//! first non-destructive one. Escape: "cancel", else the first
//! non-destructive one -- never a destructive one (both can be chosen on
//! purpose: [`AlertOptions`]). A checkbox under the text: [`alert_check`].
//! A close dot at its top corner where no button already backs out
//! ([`needs_close`]).
//!
//! The login password (dialog.ask_password) is shell/authsheet's sheet,
//! not this crate's.

use adw::prelude::*;
use std::cell::RefCell;
use std::rc::Rc;

pub(crate) fn register_css() {
    crate::css::register_module("ui/dialog.py");
}

/// The alert's own width, fixed (the wrapped text can't make it shake).
const ALERT_WIDTH: i32 = 372;

/// Long unbroken words (file names) get break chances every few letters:
/// without them the wrapped heading couldn't settle on a width and shook.
pub fn breakable(text: &str) -> String {
    let mut out = String::new();
    let mut run: Vec<char> = Vec::new();
    let flush = |run: &mut Vec<char>, out: &mut String| {
        if run.len() >= 18 {
            let parts: Vec<String> = run.chunks(8).map(|c| c.iter().collect()).collect();
            out.push_str(&parts.join("\u{200b}"));
        } else {
            out.extend(run.iter());
        }
        run.clear();
    };
    for c in text.chars() {
        if c.is_whitespace() {
            flush(&mut run, &mut out);
            out.push(c);
        } else {
            run.push(c);
        }
    }
    flush(&mut run, &mut out);
    out
}

/// responses: (id, label, style "" / "default" / "destructive").
pub fn alert<F: Fn(&str) + 'static>(
    heading: &str,
    body: &str,
    responses: &[(&str, String, &str)],
    on_response: F,
    parent: Option<&gtk::Widget>,
) -> adw::AlertDialog {
    alert_full(heading, body, responses, None, move |rid, _| on_response(rid), parent)
}

/// An alert with a checkbox under its text (Finder's "Apply to All");
/// on_response(response id, ticked).
pub fn alert_check<F: Fn(&str, bool) + 'static>(
    heading: &str,
    body: &str,
    responses: &[(&str, String, &str)],
    check: &str,
    checked: bool,
    on_response: F,
    parent: Option<&gtk::Widget>,
) -> adw::AlertDialog {
    alert_full(heading, body, responses, Some((check, checked)), on_response, parent)
}

/// What an alert may be given beyond its responses (dialog.alert's keywords).
#[derive(Clone, Debug, Default)]
pub struct AlertOptions {
    /// a checkbox under the text (Finder's "Apply to All"), ticked at first or not
    pub check: Option<(String, bool)>,
    /// the response Escape gives (default: "cancel", else the first
    /// non-destructive one; it needn't be one of the responses)
    pub close: Option<String>,
    /// the one Return gives -- chosen on purpose, it may be destructive
    /// (macOS' Empty Trash: Return empties, Vini); otherwise never a destructive one
    pub default: Option<String>,
}

/// An alert with every option; on_response(response id, ticked).
pub fn alert_with<F: Fn(&str, bool) + 'static>(
    heading: &str,
    body: &str,
    responses: &[(&str, String, &str)],
    opts: AlertOptions,
    on_response: F,
    parent: Option<&gtk::Widget>,
) -> adw::AlertDialog {
    alert_opts(heading, body, responses, &opts, on_response, parent)
}

fn alert_full<F: Fn(&str, bool) + 'static>(
    heading: &str,
    body: &str,
    responses: &[(&str, String, &str)],
    check: Option<(&str, bool)>,
    on_response: F,
    parent: Option<&gtk::Widget>,
) -> adw::AlertDialog {
    let opts = AlertOptions { check: check.map(|(l, on)| (l.to_string(), on)), ..Default::default() };
    alert_opts(heading, body, responses, &opts, on_response, parent)
}

fn alert_opts<F: Fn(&str, bool) + 'static>(
    heading: &str,
    body: &str,
    responses: &[(&str, String, &str)],
    opts: &AlertOptions,
    on_response: F,
    parent: Option<&gtk::Widget>,
) -> adw::AlertDialog {
    let dlg = adw::AlertDialog::new(Some(&breakable(heading)), Some(&breakable(body)));
    let tick = opts.check.as_ref().map(|(label, on)| {
        let b = gtk::CheckButton::builder().label(label).halign(gtk::Align::Center).active(*on).build();
        dlg.set_extra_child(Some(&b));
        b
    });
    dlg.add_css_class("sonata-alert");
    if (adw::major_version(), adw::minor_version()) >= (1, 6) {
        dlg.set_content_width(ALERT_WIDTH); // (segfaults with libadwaita 1.5.0)
    }
    let mut default: Option<&str> = None;
    for (rid, text, style) in responses {
        dlg.add_response(rid, text);
        match *style {
            "destructive" => dlg.set_response_appearance(rid, adw::ResponseAppearance::Destructive),
            "default" => dlg.set_response_appearance(rid, adw::ResponseAppearance::Suggested),
            _ => {}
        }
        if *style != "destructive" && (default.is_none() || *style == "default") {
            default = Some(rid);
        }
    }
    if let Some(d) = opts.default.as_deref().or(default) {
        dlg.set_default_response(Some(d));
    }
    // Escape = "cancel", else the first non-destructive response: never a
    // destructive one (TextEdit's "Don't Save" comes first and must not win)
    let close = opts.close.as_deref().or_else(|| {
        responses.iter().find(|r| r.0 == "cancel").or_else(|| responses.iter().find(|r| r.2 != "destructive")).map(|r| r.0)
    });
    if let Some(c) = close {
        dlg.set_close_response(c);
    }
    dlg.connect_response(None, move |_, rid| on_response(rid, tick.as_ref().is_some_and(|t| t.is_active())));
    // always its own window, so the compositor's blur shows through its
    // glass -- still in front of its window and modal to it
    let owner = parent.and_then(|p| p.root());
    dlg.present(None::<&gtk::Widget>);
    if let Some(root) = dlg.root() {
        let own = owner.as_ref().is_none_or(|o| o.upcast_ref::<gtk::Widget>() != root.upcast_ref::<gtk::Widget>());
        if own {
            dlg.add_css_class("glass");
            root.add_css_class("sonata-glass-window");
            if let (Ok(win), Some(Ok(o))) = (root.clone().downcast::<gtk::Window>(), owner.clone().map(|o| o.downcast::<gtk::Window>())) {
                win.set_transient_for(Some(&o));
                win.set_modal(true);
            }
            if let Ok(win) = root.clone().downcast::<gtk::Window>() {
                // a fixed-size window made the compositor and GTK disagree
                // on its size every frame: the alert wobbled sideways
                win.set_resizable(true);
            }
            if let (Some(o), Ok(win)) = (owner.and_then(|o| o.downcast::<gtk::Window>().ok()), root.downcast::<gtk::Window>()) {
                raise_when_owner_shows(&o, &win);
            }
        }
    }
    let ids: Vec<&str> = responses.iter().map(|r| r.0).collect();
    let labels: Vec<&str> = responses.iter().map(|r| r.1.as_str()).collect();
    if needs_close(&ids, &labels) {
        add_close(&dlg); // (its content exists once presented; none next to Cancel: Vini)
    }
    dlg
}

/// Answers that back out of an alert as well: Revert (a display change),
/// Stop (a copy that met a conflict).
pub const BACK_OUT_IDS: [&str; 2] = ["revert", "stop"];

/// A close dot only where no button leaves things as they are (Vini:
/// "Later / Restart" had one for nothing). One button alone (OK, Unlock...)
/// already closes it, so it never needs one either. `ids` and `labels`:
/// the responses' ids and texts.
pub fn needs_close(ids: &[&str], labels: &[&str]) -> bool {
    use crate::window::{dismiss_label, DISMISS_IDS};
    if ids.len() <= 1 {
        return false;
    }
    !ids.iter().any(|r| DISMISS_IDS.contains(r) || BACK_OUT_IDS.contains(r)) && !labels.iter().any(|t| dismiss_label(t))
}

/// Every Sonata dialog gets a close dot at its top corner (Vini), the
/// windows' own button: their look, colours and side. A click closes it
/// like Escape (its close response). Put at the top of the dialog's own
/// content (the box under its window handle): nothing is moved around.
fn add_close(dlg: &adw::AlertDialog) {
    if crate::window::has_window_buttons(dlg) {
        return; // (one of its own already: Vini)
    }
    let mut queue = std::collections::VecDeque::from([dlg.clone().upcast::<gtk::Widget>()]);
    while let Some(w) = queue.pop_front() {
        if let Some(bx) = w.downcast_ref::<gtk::WindowHandle>().and_then(|h| h.child()).and_downcast::<gtk::Box>() {
            let d = dlg.downgrade();
            bx.prepend(&crate::window::close_button(move || {
                if let Some(d) = d.upgrade() {
                    close(&d);
                }
            }));
            dlg.add_css_class("has-close");
            return;
        }
        let mut c = w.first_child();
        while let Some(ch) = c {
            c = ch.next_sibling();
            queue.push_back(ch);
        }
    }
}

/// The close dot: what Escape does -- its close response, then it goes.
pub fn close(dlg: &adw::AlertDialog) {
    let rid = dlg.close_response();
    dlg.emit_by_name::<()>("response", &[&rid.as_str()]);
    if dlg.root().is_some() {
        dlg.force_close();
    }
}

/// An alert raised while its window is still opening (Files: a folder
/// that fails to load as the window appears) reached the screen first,
/// and the window then opened on top of it: the error hid behind it
/// (Vini). Once the owner window becomes active, present the alert again
/// so the compositor stacks it above its parent.
fn raise_when_owner_shows(owner: &gtk::Window, win: &gtk::Window) {
    if owner.is_active() {
        return;
    }
    let id: Rc<RefCell<Option<glib::SignalHandlerId>>> = Rc::default();
    let (i, w) = (id.clone(), win.downgrade());
    let handler = owner.connect_is_active_notify(move |owner| {
        if !owner.is_active() {
            return;
        }
        if let Some(h) = i.borrow_mut().take() {
            owner.disconnect(h);
        }
        if let Some(w) = w.upgrade().filter(|w| w.is_visible()) {
            w.present();
        }
    });
    id.replace(Some(handler));
    let o = owner.downgrade();
    win.connect_destroy(move |_| {
        if let (Some(o), Some(h)) = (o.upgrade(), id.borrow_mut().take()) {
            o.disconnect(h);
        }
    });
}

/// One line to type (a new name...): an alert with an entry, the text
/// selected, `ok` off while it's empty. on_done(text) with the trimmed
/// text, only when confirmed.
pub fn ask_text<F: Fn(String) + 'static>(heading: &str, text: &str, ok: &str, on_done: F, body: &str, parent: Option<&gtk::Widget>) -> adw::AlertDialog {
    let entry = crate::controls::text_field(text, "");
    entry.set_hexpand(true);
    entry.set_activates_default(true);
    let e = entry.clone();
    let dlg = alert(heading, body, &[("cancel", sonata_i18n::tr!("Cancel"), ""), ("ok", ok.to_string(), "default")], move |rid| {
        let value = e.text().trim().to_string();
        if rid == "ok" && !value.is_empty() {
            on_done(value);
        }
    }, parent);
    dlg.set_extra_child(Some(&entry));
    let d = dlg.clone();
    entry.connect_changed(move |e| d.set_response_enabled("ok", !e.text().trim().is_empty()));
    dlg.set_response_enabled("ok", !text.trim().is_empty()); // opened empty: off already
    glib::idle_add_local_once(move || {
        entry.grab_focus();
        entry.select_region(0, -1);
    });
    dlg
}

#[cfg(test)]
mod tests {
    use super::{breakable, needs_close};

    /// Vini: "Restart the computer now?" (Later / Restart) showed a close
    /// dot though Later already closes it. Same for OK-only, Done, Don't
    /// Allow, Revert (display settings) and Stop (copy conflicts).
    #[test]
    fn test_no_close_dot_on_restart_later_alert() {
        for (ids, labels) in [
            (vec!["later", "now"], vec!["Later", "Restart"]),
            (vec!["ok"], vec!["OK"]),
            (vec!["only"], vec!["Unlock"]),
            (vec!["done", "more"], vec!["Done", "Show More"]),
            (vec!["deny", "allow"], vec!["Don\u{2019}t Allow", "Allow"]),
            (vec!["revert", "keep"], vec!["Revert", "Keep"]),
            (vec!["keep", "stop", "replace"], vec!["Keep Both", "Stop", "Replace"]),
        ] {
            assert!(!needs_close(&ids, &labels), "{ids:?}");
        }
        assert!(needs_close(&["a", "b"], &["Unmount Anyway", "Eject"]));
    }

    /// Vini: a close dot beside Cancel or Quit makes no sense.
    #[test]
    fn test_none_next_to_cancel_or_quit() {
        assert!(!needs_close(&["cancel", "create"], &["Cancel", "Save"]));
        assert!(!needs_close(&["no", "yes"], &["Quit", "Keep Open"]));
        assert!(!needs_close(&["x"], &["Close"]));
    }

    #[test]
    fn long_names_get_break_chances() {
        assert_eq!(breakable("short words stay"), "short words stay");
        assert_eq!(breakable("“04iuiyBZ61YPzdVS4GfRYKM-19.webp” can't"), "“04iuiyB\u{200b}Z61YPzdV\u{200b}S4GfRYKM\u{200b}-19.webp\u{200b}” can't");
    }
}
