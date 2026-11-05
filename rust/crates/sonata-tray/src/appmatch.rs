//! The app behind a tray icon, and bringing it forward: apps.py's
//! match_app_id (what the tray needs of it), notifications.py's
//! window_of / bring_forward, and Steam's links.

use gio::prelude::*;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::Path;
use std::time::Duration;

// a wrapper's name ends in these: "discord-wayland" is Discord
const WRAPPER_SUFFIXES: &[&str] = &["-launcher", "-bin", "-wrapper", "-desktop", "-stable", "-wayland", "-x11"];

/// Lower-cased app_id candidates -> desktop id (without .desktop), from
/// (desktop id, StartupWMClass, executable) of each entry, the ones meant
/// to be shown first. Exact names first (id, StartupWMClass, last
/// reverse-DNS part, executable); then the same without a wrapper suffix --
/// only where no entry claims that name exactly.
pub(crate) fn index_of(entries: &[(String, String, String)]) -> HashMap<String, String> {
    let mut idx: HashMap<String, String> = HashMap::new();
    let mut loose = Vec::new();
    for (did, wm, exe) in entries {
        let last = did.rsplit('.').next().unwrap_or(did);
        let mut keys = vec![did.as_str(), last];
        if !wm.is_empty() {
            keys.insert(0, wm);
        }
        if !exe.is_empty() {
            keys.push(exe);
        }
        for k in keys {
            let k = k.to_lowercase();
            idx.entry(k.clone()).or_insert_with(|| did.clone());
            for suf in WRAPPER_SUFFIXES {
                if k.ends_with(suf) && k.len() > suf.len() {
                    loose.push((k[..k.len() - suf.len()].to_string(), did.clone()));
                }
            }
        }
    }
    for (k, did) in loose {
        idx.entry(k).or_insert(did);
    }
    idx
}

fn build_index() -> HashMap<String, String> {
    let mut infos = gio::AppInfo::all();
    // entries meant to be shown claim their names first (a hidden copy with
    // the same window class must not win: apps.py)
    infos.sort_by_key(|i| !sonata_ui::apps::should_show(i));
    let mut entries = Vec::new();
    for info in infos {
        let Some(did) = info.id().and_then(|d| d.strip_suffix(".desktop").map(String::from)) else { continue };
        let (mut wm, mut exe) = (String::new(), String::new());
        if let Some(d) = info.downcast_ref::<gio::DesktopAppInfo>() {
            wm = d.startup_wm_class().map(|s| s.to_string()).unwrap_or_default();
            let exec = d.string("Exec").map(|s| s.to_string()).unwrap_or_default();
            exe = exec.split(' ').next().unwrap_or("").rsplit('/').next().unwrap_or("").to_string();
        }
        entries.push((did, wm, exe));
    }
    index_of(&entries)
}

/// Desktop id for an app id or program name (exact, StartupWMClass, last
/// reverse-DNS part, executable), or None when no .desktop matches.
/// (apps.py's web-app ids aren't known here: a tray icon is never one.)
pub fn match_app_id(app_id: &str) -> Option<String> {
    if app_id.is_empty() {
        return None;
    }
    if sonata_ui::apps::lookup(app_id).is_some() {
        return Some(app_id.strip_suffix(".desktop").unwrap_or(app_id).to_string());
    }
    let idx = build_index();
    let a = app_id.to_lowercase();
    let last = a.rsplit('.').next().unwrap_or(&a).to_string();
    idx.get(&a).or_else(|| idx.get(&last)).cloned().filter(|d| sonata_ui::apps::lookup(d).is_some())
}

/// Desktop id of the program running as `pid` (its executable's and
/// command's names, as match_app_id knows them), else None.
pub fn app_of_pid(pid: u32, proc: &Path) -> Option<String> {
    app_of_pid_with(pid, proc, &match_app_id)
}

pub(crate) fn app_of_pid_with(pid: u32, proc: &Path, matcher: &dyn Fn(&str) -> Option<String>) -> Option<String> {
    let dir = proc.join(pid.to_string());
    let base = |p: &str| p.rsplit('/').next().unwrap_or("").to_string();
    let mut names = Vec::new();
    if let Ok(exe) = std::fs::read_link(dir.join("exe")) {
        names.push(base(&exe.to_string_lossy()));
    }
    if let Ok(cmd) = std::fs::read(dir.join("cmdline")) {
        let arg0 = cmd.split(|b| *b == 0).next().unwrap_or(&[]);
        names.push(base(&String::from_utf8_lossy(arg0)));
    }
    if let Ok(comm) = std::fs::read_to_string(dir.join("comm")) {
        names.push(comm.trim().to_string());
    }
    names.iter().filter(|n| !n.is_empty()).find_map(|n| matcher(n))
}

/// The id of the app's most recently used window in Wayfire's list-views,
/// None when it has none. `desktop` is its desktop id; without one, the
/// app is found by its name.
pub fn window_of(views: &Value, desktop: &str, app_name: &str) -> Option<i64> {
    let name_of = |did: &str| sonata_ui::apps::lookup(did).map(sonata_ui::apps::name);
    window_of_with(views, desktop, app_name, &match_app_id, &name_of)
}

pub(crate) fn window_of_with(views: &Value, desktop: &str, app_name: &str, matcher: &dyn Fn(&str) -> Option<String>, name_of: &dyn Fn(&str) -> Option<String>) -> Option<i64> {
    let d = desktop.to_lowercase();
    let ds: Vec<String> = [d.clone(), d.strip_suffix(".desktop").unwrap_or(&d).to_string()].into_iter().filter(|s| !s.is_empty()).collect();
    let name = app_name.to_lowercase();
    let mut best: Option<&Value> = None;
    for v in views.as_array().map(|a| a.as_slice()).unwrap_or(&[]) {
        if v.get("type").and_then(|t| t.as_str()).unwrap_or("toplevel") != "toplevel" {
            continue;
        }
        let Some(aid) = v.get("app-id").and_then(|a| a.as_str()).filter(|a| !a.is_empty()) else { continue };
        let mine = if !ds.is_empty() {
            ds.contains(&aid.to_lowercase()) || matcher(aid).is_some_and(|m| ds.contains(&m.to_lowercase()))
        } else if !name.is_empty() {
            matcher(aid).and_then(|did| name_of(&did)).is_some_and(|n| n.to_lowercase() == name)
        } else {
            false
        };
        let ts = |v: &Value| v.get("last-focus-timestamp").and_then(|t| t.as_f64()).unwrap_or(0.0);
        if mine && best.is_none_or(|b| ts(v) > ts(b)) {
            best = Some(v);
        }
    }
    best.and_then(|b| b.get("id")).and_then(|i| i.as_i64())
}

/// Restore and focus the app's window; false when it has none open (or
/// no Wayfire).
pub fn bring_forward(desktop: &str, app_name: &str) -> bool {
    let Some(views) = sonata_backend::wfipc::call("window-rules/list-views", json!({})) else { return false };
    let Some(id) = window_of(&views, desktop, app_name) else { return false };
    sonata_backend::wfipc::call("wm-actions/set-minimized", json!({"view_id": id, "state": false}));
    sonata_backend::wfipc::call("window-rules/focus-view", json!({"id": id}));
    true
}

/// Open a steam:// link (or -shutdown) with Steam; false without Steam.
pub(crate) fn run_steam(link: &str) -> bool {
    let Some(steam) = glib::find_program_in_path("steam") else { return false };
    if !spawn_detached(&steam, link) {
        return false;
    }
    if link != "-shutdown" {
        front_steam();
    }
    true
}

/// `program arg`, on its own (its own process group, no terminal output),
/// reaped when it ends.
pub(crate) fn spawn_detached(program: &Path, arg: &str) -> bool {
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    let child = Command::new(program).arg(arg).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0).spawn();
    match child {
        Ok(mut c) => {
            std::thread::spawn(move || {
                let _ = c.wait();
            });
            true
        }
        Err(_) => false,
    }
}

// A steam:// link is handed to the running Steam with no xdg-activation token:
// its window stayed minimized / behind (Vini). Steam's window is brought
// forward once it shows (it may first have to map, coming from the tray).
const FRONT_EVERY_MS: u64 = 300;
const FRONT_TRIES: u32 = 20;

fn front_steam() {
    let mut left = FRONT_TRIES;
    glib::timeout_add_local(Duration::from_millis(FRONT_EVERY_MS), move || {
        left -= 1;
        if bring_forward("steam", "Steam") || left == 0 {
            glib::ControlFlow::Break
        } else {
            glib::ControlFlow::Continue
        }
    });
}
