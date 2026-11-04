//! Ask before apps use things (sonata2/appperms.py; Vini): Settings > Apps
//! > "Ask before apps use the camera, microphone, network…", on by default.
//!
//! On: a newly installed Flatpak app, the first time it opens, shows what
//! it wants of the microphone, network and home folder (checked; uncheck
//! to refuse -- Flatpak overrides); the camera and location are asked when
//! the app first uses them (Sonata's Access portal). Off: apps get all of
//! it without asking. Apps from the distro's packages aren't sandboxed:
//! nothing to ask. Apps already installed when this came are taken as
//! reviewed.
//!
//! What a Flatpak app wants and the overrides themselves are
//! backend/appmanage.py's (not ported yet): review_gate takes them from
//! the caller.

use adw::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::{fmt, tr};
use std::rc::Rc;

pub const NAME: &str = "appperms";
/// asked at the first open (overrides)
pub const ASKED: [&str; 3] = ["microphone", "network", "files"];

fn load() -> Map<String, Value> {
    crate::util::load(NAME, json!({"ask": true, "reviewed": null, "limits": {}}))
}

pub fn asking() -> bool {
    load().get("ask").map(|v| crate::util::truthy(Some(v))).unwrap_or(true)
}

pub fn set_asking(on: bool) {
    let _ = sonata_config::update(NAME, &json!({"ask": on}));
}

fn did(app_id: &str) -> &str {
    app_id.strip_suffix(".desktop").unwrap_or(app_id)
}

/// What a packaged app may not use (Settings > Apps > the app): camera,
/// microphone, network, files -- enforced when Sonata opens it.
pub fn limits(app_id: &str) -> Vec<String> {
    crate::util::strings(load().get("limits").and_then(|l| l.get(did(app_id))))
}

pub fn set_limit(app_id: &str, key: &str, refused: bool) {
    let d = did(app_id).to_string();
    let mut all = load().get("limits").and_then(Value::as_object).cloned().unwrap_or_default();
    let mut keep: Vec<String> = limits(&d).into_iter().filter(|k| k != key).collect();
    if refused {
        keep.push(key.to_string());
    }
    if keep.is_empty() {
        all.remove(&d);
    } else {
        all.insert(d, json!(keep));
    }
    let _ = sonata_config::update(NAME, &json!({"limits": all}));
}

/// The Flatpak id of an app ("" for a packaged one).
pub fn flatpak_id(info: &gio::AppInfo) -> String {
    sonata_desktop::sandbox::flatpak_id(info)
}

pub fn installed_flatpaks() -> Vec<String> {
    let mut out: Vec<String> = gio::AppInfo::all().iter().map(flatpak_id).filter(|f| !f.is_empty()).collect();
    out.sort();
    out.dedup();
    out
}

/// The Flatpak apps seen; the first time, what's there already counts as seen.
pub fn reviewed_with(installed: impl FnOnce() -> Vec<String>) -> Vec<String> {
    match load().get("reviewed") {
        Some(Value::Array(a)) => a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
        _ => {
            let got = installed();
            let _ = sonata_config::update(NAME, &json!({"reviewed": got}));
            got
        }
    }
}

pub fn reviewed() -> Vec<String> {
    reviewed_with(installed_flatpaks)
}

pub fn mark_reviewed(fid: &str) {
    let mut got = reviewed();
    if !got.iter().any(|f| f == fid) {
        got.push(fid.to_string());
        let _ = sonata_config::update(NAME, &json!({"reviewed": got}));
    }
}

/// The first open of Flatpak app `fid` ("" for a packaged one): true when
/// it should be asked about now (each app once, whatever the answer).
pub fn first_open(fid: &str, installed: impl FnOnce() -> Vec<String>) -> bool {
    if fid.is_empty() || reviewed_with(installed).iter().any(|f| f == fid) {
        return false;
    }
    mark_reviewed(fid); // asked once, whatever the answer
    asking()
}

/// What the review panel asks of `permissions` ([(key, title, on)], appmanage.permissions).
pub fn wanted(permissions: &[(String, String, bool)]) -> Vec<(String, String)> {
    permissions.iter().filter(|(k, _, on)| *on && ASKED.contains(&k.as_str())).map(|(k, t, _)| (k.clone(), t.clone())).collect()
}

/// apps.py's launch wrapper, after the app lock. true: the review panel is
/// up (launch runs after it) -- the caller does nothing more; false: launch
/// now. `permissions(fid)`: appmanage.permissions; `refuse(fid, key)`:
/// appmanage.set_permission(owner, key, false), run off the main loop.
pub fn review_gate(
    info: &gio::AppInfo,
    permissions: impl FnOnce(&str) -> Vec<(String, String, bool)>,
    refuse: impl Fn(&str, &str) + Send + Sync + 'static,
    launch: impl Fn() + 'static,
) -> bool {
    let fid = flatpak_id(info);
    if !first_open(&fid, installed_flatpaks) {
        return false;
    }
    let wants = wanted(&permissions(&fid));
    if wants.is_empty() || gtk::gdk::Display::default().is_none() {
        return false;
    }
    ask(info, &fid, &wants, refuse, launch);
    true
}

/// “X” wants to use: [x] Microphone [x] Network [x] Home Folder -- Open / Don't Open.
pub fn ask(info: &gio::AppInfo, fid: &str, wants: &[(String, String)], refuse: impl Fn(&str, &str) + Send + Sync + 'static, launch: impl Fn() + 'static) -> adw::AlertDialog {
    let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(4).halign(gtk::Align::Center).build();
    let mut checks = Vec::new();
    for (key, title) in wants {
        let c = gtk::CheckButton::builder().label(sonata_i18n::gettext(title)).active(true).build();
        bx.append(&c);
        checks.push((key.clone(), c));
    }
    let (fid, refuse, launch) = (fid.to_string(), std::sync::Arc::new(refuse), Rc::new(launch));
    let answer = move |rid: &str| {
        if rid != "open" {
            return;
        }
        let refused: Vec<String> = checks.iter().filter(|(_, c)| !c.is_active()).map(|(k, _)| k.clone()).collect();
        let (fid, refuse, launch) = (fid.clone(), refuse.clone(), launch.clone());
        // overrides first: the app starts with them
        glib::MainContext::default().spawn_local(async move {
            let _ = gio::spawn_blocking(move || refused.iter().for_each(|k| refuse(&fid, k))).await;
            sonata_desktop::applock::trusted(|| launch()); // its password (if locked) was asked already
        });
    };
    let name = info.display_name().to_string();
    let dlg = sonata_ui::dialog::alert(
        &fmt(&tr!("“{name}” wants to use"), &[("name", &name)]),
        &tr!("Uncheck what it shouldn't use. You can change it later in Settings › Installed Apps."),
        &[("cancel", tr!("Don't Open"), ""), ("open", tr!("Open"), "default")],
        answer,
        None,
    );
    dlg.set_extra_child(Some(&bx));
    dlg
}

/// The Access portal (camera, location...): done(0) granted, done(1)
/// refused. Not asking: granted at once.
pub fn access_answer(title: &str, subtitle: &str, body: &str, grant_label: Option<&str>, deny_label: Option<&str>, done: impl Fn(u32) + 'static) {
    if !asking() {
        done(0);
        return;
    }
    let grant = grant_label.filter(|s| !s.is_empty()).map(String::from).unwrap_or_else(|| tr!("Allow"));
    let deny = deny_label.filter(|s| !s.is_empty()).map(String::from).unwrap_or_else(|| tr!("Don't Allow"));
    let text: Vec<&str> = [subtitle, body].into_iter().filter(|t| !t.is_empty()).collect();
    let text = if text.is_empty() { tr!("You can change this later in Settings › Installed Apps.") } else { text.join("\n") };
    let title = if title.is_empty() { tr!("Allow access?") } else { title.to_string() };
    let dlg = sonata_ui::dialog::alert(&title, &text, &[("deny", deny, ""), ("grant", grant, "default")], move |rid| done(if rid == "grant" { 0 } else { 1 }), None);
    dlg.set_close_response("deny"); // Escape refuses
}

#[cfg(test)]
mod tests {
    use super::*;

    fn reset() {
        let _ = sonata_config::save(NAME, json!({"ask": true, "reviewed": []}).as_object().unwrap());
    }

    #[test]
    fn new_flatpaks_are_asked_once_packages_never() {
        let _l = crate::util::test_lock();
        reset();
        assert!(first_open("com.discordapp.Discord", Vec::new));
        assert!(!first_open("com.discordapp.Discord", Vec::new)); // second open: no question
        assert!(!first_open("", Vec::new)); // packages never asked
        let wants = wanted(&[("camera".into(), "Camera".into(), true), ("microphone".into(), "Microphone".into(), true), ("network".into(), "Network".into(), true), ("files".into(), "Home Folder".into(), false)]);
        assert_eq!(wants, [("microphone".to_string(), "Microphone".to_string()), ("network".into(), "Network".into())]);
        // not asking grants
        reset();
        set_asking(false);
        assert!(!first_open("org.x.App", Vec::new));
        // installed before count as seen
        let _ = sonata_config::save(NAME, json!({"ask": true}).as_object().unwrap());
        assert!(!first_open("com.discordapp.Discord", || vec!["com.discordapp.Discord".into()]));
        assert!(first_open("org.new.App", Vec::new));
        reset();
    }

    #[test]
    fn limits_per_app() {
        let _l = crate::util::test_lock();
        set_limit("org.x.App.desktop", "camera", true);
        set_limit("org.x.App", "network", true);
        assert_eq!(limits("org.x.App"), ["camera", "network"]);
        set_limit("org.x.App", "camera", false);
        set_limit("org.x.App", "network", false);
        assert!(limits("org.x.App").is_empty());
        assert!(!load()["limits"].as_object().unwrap().contains_key("org.x.App"));
    }
}
