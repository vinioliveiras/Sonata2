//! Steam in Sonata's window layout (sonata2/steamtheme.py; Vini: "keep
//! Steam's own theme, but fit Sonata's window layout"). Steam draws its own
//! title bar and window buttons (its interface is a web page), so the
//! window manager can't frame it. Steam loads that interface from CSS
//! files in its own folder: Sonata adds a small stylesheet of its own to
//! them -- Steam's colours and layout stay as they are, only the window
//! buttons become Sonata's round traffic lights, on Sonata's side.
//!
//! How Steam's files are patched (the way Adwaita-for-Steam does it, MIT):
//! the file Steam loads (library.css...) is renamed X.original.css, and a
//! new X.css imports it and then Sonata's stylesheet, padded with spaces to
//! the original's size. Steam updates put its files back: Sonata looks
//! again at login and whenever Steam's interface folder changes (Watch).
//! An earlier Sonata installed the whole Adwaita-for-Steam skin: it is
//! taken out the first time this runs.

use crate::frame::{self, Frame};
use base64::Engine;
use serde_json::json;
use std::os::fd::AsRawFd;
use std::path::{Path, PathBuf};

pub const KEY: &str = "steam_theme";
pub const STEAM_ROOTS: [(&str, &str); 2] = [("default", "~/.steam/steam"), ("flatpak", "~/.var/app/com.valvesoftware.Steam/.steam/steam")];
/// login/dialogs, main windows, notes
pub const PATCH_FILES: [&str; 3] = ["library.css", "gamerecording.css", "gamenotes.css"];
pub const HEADER: &str = "/* Sonata 2 */";
/// steamui/sonata/window.css
pub const SKIN_DIR: &str = "sonata";
/// the skin an earlier Sonata installed
pub const ADWAITA_HEADER: &str = "/* Adwaita-for-Steam */";
pub const TL: [&str; 4] = ["close", "minimize", "maximize", "restore"];
// Steam's main window: its top bar (with the Steam / View / Friends... menu).
// Class names from Steam's current interface (as Adwaita-for-Steam 4.4 uses them).
const TOPBAR: &str = "div._3Z7VQ1IMk4E3HsHvrkLNgo";
const MENUBAR: &str = "div._3s0lkohH8wU2do0K1il28Y";
/// the top bar's room for Steam's own window buttons
const CONTROLS_SLOT: &str = "div.qP17eBPXkfezFfexZ4hC3";
/// the top bar's Big Picture button (GamepadUIToggle)
const BIG_PICTURE: &str = "div._3LKQ3S_yqrebeNLF6aeiog";

pub fn enabled() -> bool {
    crate::util::truthy(crate::util::load("appearance", json!({KEY: true})).get(KEY))
}

/// {name: steam root} of the Steam installs here (with their interface files).
pub fn targets() -> Vec<(&'static str, PathBuf)> {
    STEAM_ROOTS
        .iter()
        .filter_map(|(name, root)| {
            let p = crate::util::expand(root);
            let r = std::fs::canonicalize(&p).unwrap_or(p);
            r.join("steamui/css").is_dir().then_some((*name, r))
        })
        .collect()
}

fn first_line(path: &Path) -> String {
    std::fs::read(path).map(|b| String::from_utf8_lossy(&b).lines().next().unwrap_or("").trim().to_string()).unwrap_or_default()
}

/// Every Steam interface file Sonata patches is patched (an update puts the originals back).
pub fn installed(root: &Path) -> bool {
    let css = root.join("steamui/css");
    let files: Vec<&str> = PATCH_FILES.iter().copied().filter(|n| css.join(n).is_file()).collect();
    !files.is_empty() && root.join("steamui").join(SKIN_DIR).join("window.css").is_file() && files.iter().all(|n| first_line(&css.join(n)) == HEADER)
}

fn data_uri(path: &Path) -> String {
    let b = std::fs::read(path).unwrap_or_default();
    format!("url('data:image/svg+xml;base64,{}')", base64::engine::general_purpose::STANDARD.encode(b))
}

fn py_round(v: f64) -> i64 {
    v.round_ties_even() as i64
}

/// Steam's window buttons as Sonata's: round traffic lights (the same
/// pictures as every window's), in the user's order from the window's
/// edge inwards, on the user's side, at Sonata's place. Nothing else in
/// Steam changes.
pub fn window_css_for(f: &Frame, pics: &Path) -> String {
    let left = f.buttons_side == "left";
    let (dot, gap) = (f.dot, f.dot_gap);
    // (whole px; a rounder corner gives halves)
    let (edge, top) = (py_round(f.dot_left - dot as f64 / 2.0), py_round(f.dot_top - dot as f64 / 2.0));
    // in the row, left to right: on the right side close is at the edge (Windows' order);
    // on the left, close first (macOS). Only the main window's buttons move to
    // the user's side; Steam's other windows (Friends, Settings) keep them on
    // the right, so there they always use the right side's order (Vini: close
    // first on the right was confusing).
    let n = f.buttons.len() as i64;
    let mut left_order: Vec<(String, i64)> = f.buttons.iter().enumerate().map(|(i, b)| (b.clone(), i as i64)).collect();
    let zoom = left_order.iter().find(|(b, _)| b == "maximize").map(|(_, i)| *i).unwrap_or(2);
    left_order.push(("restore".into(), zoom));
    let right_of = |name: &str| left_order.iter().find(|(b, _)| b == name).map(|(_, i)| n - 1 - i).unwrap_or(0);
    let sel = "body.DesktopUI .title-bar-actions.window-controls, html.client_chat_frame .title-bar-actions.window-controls";
    let mut out = vec![
        HEADER.to_string(),
        format!("/* window buttons: {} */", frame::button_layout(f)),
        format!(":is({sel}) {{ display: flex !important; flex-direction: row !important; align-items: center !important; gap: {gap}px !important; -webkit-app-region: no-drag !important; }}"),
        format!(":is({sel}) .title-area-icon {{ width: {dot}px !important; height: {dot}px !important; min-width: {dot}px !important; min-height: {dot}px !important; max-width: {dot}px !important; padding: 0 !important; margin: 0 !important; border: none !important; border-radius: 50% !important; box-shadow: none !important; background: no-repeat center / {dot}px {dot}px transparent !important; }}"),
        format!(":is({sel}) .title-area-icon > * {{ display: none !important; }}"),
    ];
    for name in TL {
        let (plain, hover) = (pics.join(format!("sonata-tl-{name}.svg")), pics.join(format!("sonata-tl-{name}-hover.svg")));
        if !(plain.is_file() && hover.is_file()) {
            continue;
        }
        let b = format!(".title-area-icon.{name}Button");
        out.push(format!(":is({sel}) {b} {{ order: {} !important; background-image: {} !important; }}", right_of(name), data_uri(&plain)));
        out.push(format!(":is({sel}):hover {b} {{ background-image: {} !important; }}", data_uri(&hover)));
    }
    // every window: the buttons at Sonata's place (its distance from the top and
    // the side edge), out of Steam's layout -- in Friends / Settings they sat
    // wherever Steam's own (bigger) buttons had been, against the corner (Vini)
    let width = 3 * dot + 2 * gap;
    out.push(format!(":is({sel}) {{ position: fixed !important; top: {top}px !important; right: {edge}px !important; left: auto !important; bottom: auto !important; height: {dot}px !important; margin: 0 !important; padding: 0 !important; z-index: 1000 !important; }}"));
    // the main window: on the user's side; the room Steam kept for its own buttons
    // (after the profile) goes, so its top bar reaches the edge (Vini)
    let main = format!("body.DesktopUI:has({TOPBAR})");
    if left {
        out.push(format!("{main} .title-bar-actions.window-controls {{ left: {edge}px !important; right: auto !important; }}"));
    }
    let slot = if left { 8 } else { edge + width + 12 };
    out.push(format!("{main} {TOPBAR} {CONTROLS_SLOT} {{ flex: 0 0 {slot}px !important; width: {slot}px !important; min-width: 0 !important; }}"));
    out.push(format!("body.DesktopUI {TOPBAR} {BIG_PICTURE} {{ display: none !important; }}")); // Vini: not used
    if left {
        for (name, i) in &left_order {
            out.push(format!("{main} .title-bar-actions.window-controls .title-area-icon.{name}Button {{ order: {i} !important; }}"));
        }
        out.push(format!("{main} {TOPBAR} {MENUBAR} {{ margin-left: {}px !important; }}", edge + width + 12));
    }
    out.join("\n") + "\n"
}

/// Settings' button colours (Steam is dark).
pub fn window_css() -> String {
    window_css_for(&frame::frame(), &crate::trafficlights::folder(true))
}

// -- Steam's files --------------------------------------------------------------------------------
fn patch_text(name: &str) -> String {
    format!("{HEADER}\n@import url(\"{}.original.css\");\n@import url(\"../{SKIN_DIR}/window.css\");\n", &name[..name.len() - 4])
}

fn original(css: &Path, n: &str) -> PathBuf {
    css.join(format!("{}.original.css", &n[..n.len() - 4]))
}

/// The whole Adwaita-for-Steam skin an earlier Sonata installed: Steam's own files back.
fn drop_adwaita(root: &Path) {
    let css = root.join("steamui/css");
    for n in PATCH_FILES {
        let (p, orig) = (css.join(n), original(&css, n));
        if first_line(&p) == ADWAITA_HEADER && orig.is_file() {
            let _ = std::fs::rename(&orig, &p);
        }
    }
    let _ = std::fs::remove_dir_all(root.join("steamui/adwaita"));
}

pub fn patch_root(root: &Path, css_text: &str) -> bool {
    let css = root.join("steamui/css");
    drop_adwaita(root);
    let skin = root.join("steamui").join(SKIN_DIR);
    let _ = std::fs::create_dir_all(&skin);
    let path = skin.join("window.css");
    if std::fs::read_to_string(&path).ok().as_deref() != Some(css_text) {
        let _ = std::fs::write(&path, css_text);
    }
    let mut ok = true;
    for n in PATCH_FILES {
        let (p, orig) = (css.join(n), original(&css, n));
        if !p.is_file() || first_line(&p) == HEADER {
            continue;
        }
        let text = patch_text(n).into_bytes();
        let size = std::fs::metadata(&p).map(|m| m.len() as usize).unwrap_or(0);
        if text.len() > size {
            ok = false; // (Steam reads a file of the original's size)
            continue;
        }
        if std::fs::rename(&p, &orig).is_err() {
            ok = false;
            continue;
        }
        let mut padded = text;
        padded.resize(size, b' ');
        let _ = std::fs::write(&p, padded);
    }
    ok
}

/// One patcher at a time, across processes (Settings and the menu bar
/// both patch): two at once could move the patch over X.original.css and
/// lose Steam's own file.
fn locked<R>(f: impl FnOnce() -> R) -> R {
    let d = crate::util::cache_home().join("sonata2");
    let _ = std::fs::create_dir_all(&d);
    let lock = std::fs::OpenOptions::new().write(true).create(true).truncate(true).open(d.join("steam-theme.lock"));
    if let Ok(l) = &lock {
        unsafe {
            libc::flock(l.as_raw_fd(), libc::LOCK_EX);
        }
    }
    f()
}

/// Sonata's window buttons in every Steam here (cheap: only what's missing is written).
pub fn apply() -> bool {
    let found = targets();
    if found.is_empty() {
        return false;
    }
    let text = window_css();
    locked(|| found.iter().map(|(_, root)| patch_root(root, &text)).fold(true, |a, b| a && b))
}

/// On, and undone (a Steam update) or out of date: apply again.
pub fn ensure() -> bool {
    enabled() && apply()
}

pub fn remove_root(root: &Path) {
    let css = root.join("steamui/css");
    drop_adwaita(root);
    for n in PATCH_FILES {
        let (p, orig) = (css.join(n), original(&css, n));
        if first_line(&p) == HEADER && orig.is_file() {
            let _ = std::fs::rename(&orig, &p);
        }
    }
    let _ = std::fs::remove_dir_all(root.join("steamui").join(SKIN_DIR));
}

/// Steam's own files back (and an earlier Adwaita skin out).
pub fn remove() -> bool {
    locked(|| targets().iter().for_each(|(_, r)| remove_root(r)));
    true
}

/// The menu bar's side: at login, and after Steam rewrites its interface
/// files (an update), the buttons are put back -- quietly, in a thread.
/// Also when the window buttons' side changes (appearance.json).
pub struct Watch {
    _mons: Vec<gio::FileMonitor>,
    _cfg: Vec<gio::FileMonitor>,
}

pub const SETTLE_S: u32 = 60;

impl Watch {
    pub fn new(first_s: u32) -> Self {
        use gio::prelude::*;
        use std::cell::{Cell, RefCell};
        use std::rc::Rc;
        let src: Rc<RefCell<Option<glib::SourceId>>> = Rc::default();
        let busy = Rc::new(Cell::new(false));
        let run = {
            let busy = busy.clone();
            Rc::new(move || {
                if busy.replace(true) {
                    return;
                }
                let busy = busy.clone();
                glib::MainContext::default().spawn_local(async move {
                    let _ = gio::spawn_blocking(ensure).await;
                    busy.set(false);
                });
            })
        };
        let soon = {
            let (src, run) = (src.clone(), run.clone());
            Rc::new(move |seconds: u32| {
                if let Some(s) = src.borrow_mut().take() {
                    sonata_ui::cancel(s);
                }
                let (src2, run) = (src.clone(), run.clone());
                *src.borrow_mut() = Some(glib::timeout_add_seconds_local_once(seconds, move || {
                    src2.borrow_mut().take();
                    run();
                }));
            })
        };
        let mut mons = Vec::new();
        for (_, root) in targets() {
            if let Ok(m) = gio::File::for_path(root.join("steamui/css")).monitor_directory(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE) {
                let soon = soon.clone();
                m.connect_changed(move |_, _, _, _| soon(SETTLE_S));
                mons.push(m);
            }
        }
        let s2 = soon.clone();
        let cfg = sonata_config::watch("appearance", move || s2(5));
        soon(first_s);
        Watch { _mons: mons, _cfg: cfg }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{Map, Value};

    fn steam() -> (tempfile::TempDir, PathBuf, String) {
        let t = tempfile::tempdir().unwrap();
        let root = t.path().join(".steam/steam");
        std::fs::create_dir_all(root.join("steamui/css")).unwrap();
        let original = format!(".x{{color:red}}\n{}{}", "/* steam */\n".repeat(284), ".".repeat(4000 - 14 - 12 * 284));
        for f in ["library", "gamerecording", "gamenotes"] {
            std::fs::write(root.join(format!("steamui/css/{f}.css")), &original).unwrap();
        }
        (t, root, original)
    }

    fn pics() -> PathBuf {
        crate::trafficlights::bundled().0
    }

    #[test]
    fn css_is_only_sonatas_window_buttons() {
        let f = frame::frame_from(&Map::new());
        let css = window_css_for(&f, &pics());
        assert_eq!(css.matches("svg+xml").count(), 8); // close/min/max/restore x plain/hover
        assert!(css.contains("border-radius: 50%")); // round, not Steam's squares
        assert!(css.contains("div._3LKQ3S_yqrebeNLF6aeiog { display: none")); // no Big Picture button
        assert!(!css.contains("--adw-")); // Steam's own colours stay
        assert!(css.contains("left: 7px")); // Sonata's place, on the left
        assert!(css.contains("window-controls) { position: fixed !important; top: 8px !important; right: 7px"));
        assert!(css.contains("div.qP17eBPXkfezFfexZ4hC3 { flex: 0 0 8px"));
        assert!(css.contains("margin-left:"));
        // Friends / Settings keep the buttons on the right: close at the edge (Windows' order)
        assert!(css.contains("closeButton { order: 2 !important; background-image"));
        assert!(css.contains("maximizeButton { order: 0 !important; background-image"));
        assert!(regex::Regex::new(r":has\(div\._3Z7VQ1IMk4E3HsHvrkLNgo\) .*\.closeButton \{ order: 0").unwrap().is_match(&css));
        let mut right = Map::new();
        right.insert("buttons_side".into(), Value::String("right".into()));
        let css = window_css_for(&frame::frame_from(&right), &pics());
        assert!(css.contains("right: 7px") && !css.contains("margin-left:"));
    }

    #[test]
    fn patches_once_and_again_after_a_steam_update() {
        let (_t, root, original) = steam();
        let css_dir = root.join("steamui/css");
        let read = |n: &str| std::fs::read_to_string(css_dir.join(n)).unwrap();
        let text = window_css_for(&frame::frame_from(&Map::new()), &pics());
        assert!(!installed(&root));
        assert!(patch_root(&root, &text));
        assert!(installed(&root));
        let lib = read("library.css");
        assert!(lib.starts_with(HEADER));
        assert_eq!(lib.len(), 4000); // padded to the original's size
        assert!(lib.contains("@import url(\"library.original.css\")"));
        assert_eq!(read("library.original.css"), original);
        patch_root(&root, &text); // again: nothing changes
        assert_eq!(read("library.original.css"), original);
        std::fs::write(css_dir.join("library.css"), &original).unwrap(); // Steam updated itself
        assert!(!installed(&root));
        patch_root(&root, &text);
        assert!(installed(&root));
        remove_root(&root);
        assert_eq!(read("library.css"), original);
        assert!(!css_dir.join("library.original.css").exists());
        assert!(!root.join("steamui").join(SKIN_DIR).is_dir());
    }

    #[test]
    fn earlier_adwaita_skin_is_taken_out() {
        let (_t, root, original) = steam();
        let css = root.join("steamui/css");
        std::fs::create_dir_all(root.join("steamui/adwaita")).unwrap();
        for f in ["library", "gamerecording", "gamenotes"] {
            std::fs::rename(css.join(format!("{f}.css")), css.join(format!("{f}.original.css"))).unwrap();
            std::fs::write(css.join(format!("{f}.css")), format!("{ADWAITA_HEADER}\n@import url('../adwaita/base.css');\n")).unwrap();
        }
        patch_root(&root, "x");
        assert!(!root.join("steamui/adwaita").is_dir());
        assert_eq!(std::fs::read_to_string(css.join("gamerecording.original.css")).unwrap(), original); // Steam's own
        assert!(installed(&root));
    }
}
