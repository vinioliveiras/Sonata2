//! Alerts (macOS Big Sur alert: bold heading, body, buttons at the bottom),
//! sonata2/ui/dialog.py, its CSS dialog.py's own.
//!
//! Default button (Return): the response styled "default"; otherwise the
//! first non-destructive one. Escape: "cancel", else the first
//! non-destructive one -- never a destructive one. Not ported yet: the
//! checkbox, the close dot.

use adw::prelude::*;

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
    let dlg = adw::AlertDialog::new(Some(&breakable(heading)), Some(&breakable(body)));
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
    if let Some(d) = default {
        dlg.set_default_response(Some(d));
    }
    let close = responses.iter().find(|r| r.0 == "cancel").or_else(|| responses.iter().find(|r| r.2 != "destructive"));
    if let Some(c) = close {
        dlg.set_close_response(c.0);
    }
    dlg.connect_response(None, move |_, rid| on_response(rid));
    // always its own window, so the compositor's blur shows through its
    // glass -- still in front of its window and modal to it
    let owner = parent.and_then(|p| p.root());
    dlg.present(None::<&gtk::Widget>);
    if let Some(root) = dlg.root() {
        let own = owner.as_ref().is_none_or(|o| o.upcast_ref::<gtk::Widget>() != root.upcast_ref::<gtk::Widget>());
        if own {
            dlg.add_css_class("glass");
            root.add_css_class("sonata-glass-window");
            if let (Ok(win), Some(Ok(o))) = (root.clone().downcast::<gtk::Window>(), owner.map(|o| o.downcast::<gtk::Window>())) {
                win.set_transient_for(Some(&o));
                win.set_modal(true);
            }
            if let Ok(win) = root.downcast::<gtk::Window>() {
                // a fixed-size window made the compositor and GTK disagree
                // on its size every frame: the alert wobbled sideways
                win.set_resizable(true);
            }
        }
    }
    dlg
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
    use super::breakable;

    #[test]
    fn long_names_get_break_chances() {
        assert_eq!(breakable("short words stay"), "short words stay");
        assert_eq!(breakable("“04iuiyBZ61YPzdVS4GfRYKM-19.webp” can't"), "“04iuiyB\u{200b}Z61YPzdV\u{200b}S4GfRYKM\u{200b}-19.webp\u{200b}” can't");
    }
}
