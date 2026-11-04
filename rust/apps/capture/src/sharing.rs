//! Screen sharing in the menu bar (sonata2/shell/sharing.py; macOS: the
//! screen-sharing pill).
//!
//! A browser sharing the screen opens a bar of its own ("<site> is sharing
//! your screen.", no app id): the toplevel list keeps it out of the Dock
//! and Alt+Tab (its share bars). While one is open, a pill over the middle
//! of the menu bar names the site; a click brings the browser's bar to the
//! front, its stop button ends the sharing -- by force (Vini: Chrome kept
//! sharing):
//!
//!   1. the browser's bar is closed: for Chrome that is its own "Stop sharing";
//!   2. the sharing streams are removed from PipeWire (stop_sharing);
//!   3. still sharing STOP_CHECK_MS later: the portal's capture service
//!      starts again, which ends every share (restart_portal).

use crate::capture::{stop_button, BarPill};
use crate::livedraw;
use gtk::{glib, pango, prelude::*};
use serde_json::Value;
use sonata_i18n::tr;
use std::rc::Rc;
use std::time::Duration;

pub const STOP_CHECK_MS: u64 = 2500;

/// The share bars' titles (wl/toplevels.py's SHARE_BAR_TEXT; the Dock's
/// toplevels.rs has the same list, but this crate can't depend on the Dock).
pub const SHARE_BAR_TEXT: [&str; 3] = [" is sharing ", " está compartilhando ", " compartilhando "];

/// "b.siobud.com is sharing your screen." -> "b.siobud.com".
pub fn site_of(title: &str) -> String {
    SHARE_BAR_TEXT.iter().find(|t| title.contains(*t)).and_then(|t| title.split_once(t)).map(|(site, _)| site.trim().to_string()).unwrap_or_default()
}

/// PipeWire ids of the screen-sharing streams (xdg-desktop-portal-wlr's "xdpw-stream-…").
pub fn cast_nodes(dump: &Value) -> Vec<i64> {
    let Some(objs) = dump.as_array() else { return Vec::new() };
    objs.iter()
        .filter(|o| o.get("type").and_then(Value::as_str).is_some_and(|t| t.ends_with(":Node")))
        .filter(|o| o.pointer("/info/props/node.name").and_then(Value::as_str).is_some_and(|n| n.starts_with("xdpw-stream")))
        .filter_map(|o| o.get("id").and_then(Value::as_i64))
        .collect()
}

type Runner<'a> = &'a dyn Fn(&[&str]) -> i32;

/// The portal's capture service starts again: every share ends.
pub fn restart_portal_with(run: Runner) -> bool {
    let ok = run(&["systemctl", "--user", "restart", "xdg-desktop-portal-wlr"]) == 0;
    println!("sonata2-sharing: portal restarted: {ok}");
    ok
}

/// Ends every screen share: its PipeWire stream goes away (the app sees
/// the share end, as with its own Stop button); if that can't be done, the
/// portal's capture service starts again (also ending them).
pub fn stop_sharing_with(has: &dyn Fn(&str) -> bool, dump: &dyn Fn() -> Option<String>, run: Runner) -> bool {
    let mut ok = false;
    if has("pw-dump") && has("pw-cli") {
        if let Some(v) = dump().and_then(|s| serde_json::from_str::<Value>(if s.trim().is_empty() { "[]" } else { &s }).ok()) {
            for node in cast_nodes(&v) {
                ok = run(&["pw-cli", "destroy", &node.to_string()]) == 0 || ok;
            }
        }
    }
    println!("sonata2-sharing: streams removed: {ok}");
    ok || restart_portal_with(run)
}

fn real_run(cmd: &[&str]) -> i32 {
    sonata_backend::run::run(cmd, 10).0
}

pub fn restart_portal() -> bool {
    restart_portal_with(&real_run)
}

pub fn stop_sharing() -> bool {
    let has = |n: &str| sonata_backend::run::which(n).is_some();
    let dump = || {
        let (code, out) = sonata_backend::run::run(&["pw-dump"], 5);
        (code == 0).then_some(out)
    };
    stop_sharing_with(&has, &dump, &real_run)
}

/// What the pill needs from the toplevel list (the menu bar's): the
/// browsers' share bars open now, closing them, bringing them to the front.
/// The host calls [`SharingControl::update`] whenever they change.
pub trait ShareBars {
    /// the share bars' titles ("<site> is sharing your screen.")
    fn titles(&self) -> Vec<String>;
    /// closes each one (Chrome: closing its bar is its Stop)
    fn close_all(&self);
    /// brings each one to the front
    fn front(&self);
}

/// The sites, each once, in order; else "Sharing Screen".
pub fn pill_text(titles: &[String]) -> String {
    let mut sites: Vec<String> = Vec::new();
    for s in titles.iter().map(|t| site_of(t)).filter(|s| !s.is_empty()) {
        if !sites.contains(&s) {
            sites.push(s);
        }
    }
    if sites.is_empty() {
        tr!("Sharing Screen")
    } else {
        sites.join(", ")
    }
}

pub struct SharingControl {
    pub pill: BarPill,
    pub label: gtk::Label,
    manager: Rc<dyn ShareBars>,
    app: gtk::Application,
}

impl SharingControl {
    pub fn new(app: &gtk::Application, manager: Rc<dyn ShareBars>) -> Rc<Self> {
        let pill = BarPill::new(Some(app), &tr!("Screen Sharing"), "sonata2-sharing");
        pill.hbox.add_css_class("share-pill");
        // the site: a click brings the browser's bar
        let what = gtk::Box::new(gtk::Orientation::Horizontal, 6);
        what.append(&gtk::Image::builder().icon_name("video-display-symbolic").css_classes(["share-icon"]).build());
        let label = gtk::Label::builder().ellipsize(pango::EllipsizeMode::End).max_width_chars(28).margin_end(2).build();
        what.append(&label);
        what.set_cursor_from_name(Some("pointer"));
        pill.hbox.append(&what);
        // draw on the shared screen (livedraw.rs)
        pill.hbox.append(&livedraw::pen_button(app, Rc::new(|| None)));
        // Stop Sharing (Vini), like the recording pill's stop button
        let stop = stop_button(&tr!("Stop Sharing"));
        pill.hbox.append(&stop);
        let c = Rc::new(SharingControl { pill, label, manager, app: app.clone() });
        let click = gtk::GestureClick::new();
        let w = Rc::downgrade(&c);
        click.connect_released(move |_, _, _, _| {
            if let Some(c) = w.upgrade() {
                c.manager.front();
            }
        });
        what.add_controller(click);
        let w = Rc::downgrade(&c);
        stop.connect_clicked(move |_| {
            if let Some(c) = w.upgrade() {
                c.stop();
            }
        });
        c.update();
        c
    }

    /// The share bars changed: shown with the sites, or gone.
    pub fn update(&self) {
        let titles = self.manager.titles();
        if !titles.is_empty() {
            self.label.set_label(&pill_text(&titles));
            self.pill.win.set_tooltip_text(Some(&tr!("Sharing your screen. Click for the browser's controls.")));
            if !self.pill.win.is_visible() {
                self.pill.show_on(None);
            }
        } else if self.pill.win.is_visible() {
            self.pill.win.set_visible(false);
            // the sharing ended: the drawing goes too
            livedraw::get(&self.app).stop();
        }
    }

    fn stop(self: &Rc<Self>) {
        println!("sonata2-sharing: stop ({} bars)", self.manager.titles().len());
        self.manager.close_all();
        std::thread::spawn(stop_sharing);
        let w = Rc::downgrade(self);
        glib::timeout_add_local_once(Duration::from_millis(STOP_CHECK_MS), move || {
            if let Some(c) = w.upgrade() {
                c.still_sharing();
            }
        });
    }

    /// Stopped yet? Otherwise the portal starts again (that ends it).
    fn still_sharing(&self) {
        if !self.manager.titles().is_empty() {
            println!("sonata2-sharing: still sharing after Stop: restarting the portal");
            std::thread::spawn(restart_portal);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::cell::RefCell;

    #[test]
    fn test_site() {
        assert_eq!(site_of("b.siobud.com is sharing your screen."), "b.siobud.com");
        assert_eq!(site_of("meet.google.com está compartilhando sua tela."), "meet.google.com");
        assert_eq!(site_of("x"), "");
    }

    #[test]
    fn test_pill_names_each_site_once() {
        let t = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(pill_text(&t(&["a.com is sharing your screen.", "b.org is sharing a window.", "a.com is sharing your screen."])), "a.com, b.org");
    }

    #[test]
    fn test_cast_nodes() {
        let dump = json!([
            {"id": 40, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "xdpw-stream-a1B2c3"}}},
            {"id": 41, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "alsa_input.x"}}},
            {"id": 42, "type": "PipeWire:Interface:Port", "info": {"props": {"node.name": "xdpw-stream-x"}}}
        ]);
        assert_eq!(cast_nodes(&dump), [40]);
        assert!(cast_nodes(&json!([])).is_empty());
    }

    /// Vini: a button on the pill to stop sharing -- the stream is destroyed.
    #[test]
    fn test_stop_destroys_the_stream() {
        let runs: RefCell<Vec<Vec<String>>> = RefCell::default();
        let run = |c: &[&str]| {
            runs.borrow_mut().push(c.iter().map(|s| s.to_string()).collect());
            0
        };
        let dump = || Some(json!([{"id": 40, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "xdpw-stream-q"}}}]).to_string());
        assert!(stop_sharing_with(&|_| true, &dump, &run));
        assert!(runs.borrow().contains(&vec!["pw-cli".to_string(), "destroy".into(), "40".into()]));
        assert!(!runs.borrow().iter().any(|c| c[0] == "systemctl"));
    }

    #[test]
    fn test_fallback_restarts_the_portal() {
        let runs: RefCell<Vec<String>> = RefCell::default();
        let run = |c: &[&str]| {
            runs.borrow_mut().push(c.join(" "));
            0
        };
        assert!(stop_sharing_with(&|_| false, &|| None, &run));
        assert_eq!(*runs.borrow(), ["systemctl --user restart xdg-desktop-portal-wlr"]);
    }
}
