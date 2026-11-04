//! `doctor windows` (sonata2/stylecheck.py): are the window styles Sonata
//! puts on other apps still in place? (Vini: "a way to monitor them all,
//! to see they work").
//!
//! 1. The settings Sonata writes into apps so they use its look
//!    (titlebars.py, adwstyle.py, steamtheme.py -- still the Python
//!    Sonata's, which writes them at login): Chromium browsers' GTK mode and
//!    flags file, Firefox's user.js, VS Code's title bar style, Vesktop's,
//!    Claude's environment, the libadwaita style, Steam's patched interface.
//!    An app's update or its own settings can undo one.
//! 2. Every window open now (Wayfire IPC): who draws its title bar -- Sonata
//!    (Wayfire's decoration) or the app itself, and if so whether that frame
//!    is one Sonata styles -- and whether its corners are rounded
//!    (sonata-corners, "sonata/rounded").
//!
//! Rows are (status, what, detail): ok / warn / FAIL. The menu bar's half
//! (a notification once a problem is new) keeps `problems` / `new_problems`
//! here for when the menu bar is ported.

use crate::doctor::{Status, COLOR_END, FAIL, OK, WARN};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

/// browsers whose own frame Sonata styles (GTK mode: its traffic lights), by app_id
pub const CHROMIUM_IDS: [&str; 7] = ["google-chrome", "chromium", "brave-browser", "microsoft-edge", "vivaldi", "opera", "thorium"];

// titlebars.py's tables
pub const CHROMIUM_FLAGS: [&str; 8] = [
    "chrome-flags.conf", "chrome-beta-flags.conf", "chrome-dev-flags.conf", "chromium-flags.conf", "brave-flags.conf", "vivaldi-stable.conf",
    "thorium-flags.conf", "microsoft-edge-stable-flags.conf",
];
pub const MOZILLA: [&str; 6] = ["~/.mozilla/firefox", "~/.config/mozilla/firefox", "~/.thunderbird", "~/.librewolf", "~/.floorp", "~/.var/app/org.mozilla.firefox/.mozilla/firefox"];
pub const MOZ_LINE: &str = "user_pref(\"browser.tabs.inTitlebar\", 0);  // Sonata title bars (Settings > Appearance)";
pub const VESKTOP: [&str; 2] = ["~/.config/vesktop/settings.json", "~/.var/app/dev.vencord.Vesktop/config/vesktop/settings.json"];
const CODE: [&str; 4] = ["Code", "Code - OSS", "VSCodium", "Code - Insiders"];
const CODE_FLATPAK: [&str; 3] = ["~/.var/app/com.visualstudio.code/config/Code", "~/.var/app/com.vscodium.codium/config/VSCodium", "~/.var/app/com.visualstudio.code.insiders/config/Code - Insiders"];
// steamtheme.py's
const STEAM_ROOTS: [(&str, &str); 2] = [("default", "~/.steam/steam"), ("flatpak", "~/.var/app/com.valvesoftware.Steam/.steam/steam")];
const PATCH_FILES: [&str; 3] = ["library.css", "gamerecording.css", "gamenotes.css"];
const STEAM_HEADER: &str = "/* Sonata 2 */";
const SKIN_DIR: &str = "sonata";

/// A row, or a heading (`detail` empty and `status` None).
#[derive(Clone, Debug, PartialEq)]
pub enum Row {
    Heading(String),
    Check(Status, String, String),
}

fn check(s: Status, what: impl Into<String>, detail: impl Into<String>) -> Row {
    Row::Check(s, what.into(), detail.into())
}

fn read(path: &Path) -> String {
    std::fs::read(path).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default()
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default()
}

fn expand(p: &str) -> PathBuf {
    match p.strip_prefix("~/") {
        Some(rest) => home().join(rest),
        None => PathBuf::from(p),
    }
}

fn listdirs(path: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(path).map(|rd| rd.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect()).unwrap_or_default();
    v.sort();
    v
}

fn base_name(p: &Path) -> String {
    p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

/// Python's truthiness of a settings value.
pub(crate) fn truthy(v: Option<&Value>) -> bool {
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// An Appearance switch that is on by default (system_titlebars, steam_theme).
fn appearance_on(key: &str) -> bool {
    truthy(sonata_config::load("appearance", &json!({ key: true })).get(key))
}

/// Every Chromium-based browser's data folder (titlebars.chromium_roots):
/// a "Local State" file next to Default/, one or two levels under
/// ~/.config (Flatpak browsers under ~/.var/app too).
pub fn chromium_roots(cfg: &Path) -> Vec<PathBuf> {
    let bases = std::iter::once(cfg.to_path_buf()).chain(listdirs(&home().join(".var/app")).into_iter().map(|p| p.join("config")));
    let mut out = Vec::new();
    for base in bases {
        for d in listdirs(&base) {
            for cand in std::iter::once(d.clone()).chain(listdirs(&d)) {
                if cand.join("Local State").is_file() && cand.join("Default").is_dir() {
                    out.push(cand);
                }
            }
        }
    }
    out
}

fn chromium_installed(flags_file: &str) -> bool {
    let base = flags_file.replace("-flags.conf", "").replace(".conf", "");
    [base.clone(), format!("{base}-stable"), format!("google-{base}"), format!("google-{base}-stable")].iter().any(|b| sonata_backend::run::which(b).is_some())
}

fn code_dirs(cfg: &Path) -> Vec<PathBuf> {
    CODE.iter().map(|a| cfg.join(a)).chain(CODE_FLATPAK.iter().map(|p| expand(p))).filter(|d| d.is_dir()).collect()
}

/// `"window.titleBarStyle": "native"` on a line that isn't commented out.
pub fn code_native(settings: &str) -> bool {
    settings.lines().any(|l| {
        let Some(i) = l.find("\"window.titleBarStyle\"") else { return false };
        if l[..i].contains("//") {
            return false;
        }
        let rest = l[i + "\"window.titleBarStyle\"".len()..].trim_start();
        rest.strip_prefix(':').is_some_and(|r| r.trim_start().starts_with("\"native\""))
    })
}

// -- 1. the settings Sonata writes into apps -------------------------------------------------
pub fn config_rows() -> Vec<Row> {
    config_rows_in(&sonata_config::config_home(), appearance_on("system_titlebars"), steam_rows())
}

pub fn config_rows_in(cfg: &Path, enabled: bool, steam: Vec<Row>) -> Vec<Row> {
    if !enabled {
        return vec![check(OK, "Sonata title bars", "off in Settings > Appearance: apps keep their own")];
    }
    let mut rows = Vec::new();
    for base in chromium_roots(cfg) {
        let name = base_name(&base);
        let mut profs: Vec<String> = std::fs::read_dir(&base).map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|p| p == "Default" || p.starts_with("Profile ")).collect()).unwrap_or_default();
        profs.sort();
        for prof in profs {
            let text = read(&base.join(&prof).join("Preferences"));
            let Ok(prefs) = serde_json::from_str::<Value>(if text.is_empty() { "{}" } else { &text }) else { continue };
            let frame = prefs.get("browser").and_then(|b| b.get("custom_chrome_frame"));
            if frame == Some(&Value::Bool(false)) {
                rows.push(check(OK, format!("{name} ({prof})"), "Sonata's title bar above the tabs"));
            } else {
                rows.push(check(WARN, format!("{name} ({prof})"), "its own frame: Sonata's title bar is set at the next login (while the browser is closed)"));
            }
        }
    }
    for flags in CHROMIUM_FLAGS {
        let path = cfg.join(flags);
        if path.exists() || chromium_installed(flags) {
            let ok = read(&path).lines().any(|l| l.trim().starts_with("--gtk-version"));
            rows.push(check(if ok { OK } else { WARN }, flags, if ok { "GTK 3 window buttons" } else { "no --gtk-version: plain buttons in GTK mode; written at the next login" }));
        }
    }
    for base in MOZILLA {
        let base = expand(base);
        let Ok(rd) = std::fs::read_dir(&base) else { continue };
        let mut profs: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|p| base.join(p).join("prefs.js").is_file()).collect();
        profs.sort();
        for p in profs {
            let ok = read(&base.join(&p).join("user.js")).contains(MOZ_LINE);
            rows.push(check(if ok { OK } else { WARN }, format!("{} {p}", base_name(&base)), if ok { "tabs under Sonata's title bar" } else { "user.js line missing: written at the next login" }));
        }
    }
    for d in code_dirs(cfg) {
        // Sonata's title bar, always (titlebars.code_titlebar)
        let ok = code_native(&read(&d.join("User").join("settings.json")));
        rows.push(check(if ok { OK } else { WARN }, base_name(&d), if ok { "Sonata's title bar" } else { "its own title bar: Sonata's is set at the next login" }));
    }
    for path in VESKTOP {
        let path = expand(path);
        if path.is_file() {
            let text = read(&path);
            let ok = serde_json::from_str::<Value>(if text.is_empty() { "{}" } else { &text }).ok().is_some_and(|v| v.get("titleBar") == Some(&json!("system")));
            rows.push(check(if ok { OK } else { FAIL }, "Vesktop", if ok { "system title bar" } else { "its own title bar" }));
        }
    }
    if let Some(launcher) = sonata_backend::run::which("claude-desktop") {
        // only the community package (claude-desktop-bin / -extra) reads the
        // variable; Anthropic's official build draws its own title bar (Vini)
        let real = std::fs::canonicalize(&launcher).unwrap_or(launcher);
        if !read(&real).contains("CLAUDE_NATIVE_TITLEBAR") {
            rows.push(check(WARN, "Claude Desktop", "the official build: its own title bar (no setting for Sonata's)"));
        } else {
            let ok = std::env::var("CLAUDE_NATIVE_TITLEBAR").as_deref() == Ok("1");
            rows.push(check(if ok { OK } else { WARN }, "Claude Desktop", if ok { "native title bar" } else { "CLAUDE_NATIVE_TITLEBAR not set in this session (tools/session-env.sh)" }));
        }
    }
    // adwstyle.py: $XDG_RUNTIME_DIR/sonata2/adw/libadwaita.css, imported by ~/.config/gtk-4.0/gtk.css
    let runtime = std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })));
    let css = runtime.join("sonata2").join("adw").join("libadwaita.css");
    let user = read(&cfg.join("gtk-4.0").join("gtk.css"));
    let ok = css.is_file() && user.contains(&format!("file://{}", css.display()));
    rows.push(check(if ok { OK } else { FAIL }, "GTK 4 / libadwaita apps", if ok { "Sonata's traffic lights and title bar" } else { "style not linked from ~/.config/gtk-4.0/gtk.css (next login)" }));
    rows.extend(steam);
    rows
}

fn first_line(path: &Path) -> String {
    read(path).lines().next().unwrap_or("").trim().to_string()
}

/// Every Steam interface file Sonata patches is patched (an update puts the originals back).
pub fn steam_installed(root: &Path) -> bool {
    let css = root.join("steamui").join("css");
    let files: Vec<&str> = PATCH_FILES.iter().copied().filter(|n| css.join(n).is_file()).collect();
    !files.is_empty() && root.join("steamui").join(SKIN_DIR).join("window.css").is_file() && files.iter().all(|n| first_line(&css.join(n)) == STEAM_HEADER)
}

pub fn steam_rows() -> Vec<Row> {
    let on = appearance_on("steam_theme");
    let mut rows = Vec::new();
    for (name, root) in STEAM_ROOTS {
        let r = expand(root);
        let r = std::fs::canonicalize(&r).unwrap_or(r);
        if !r.join("steamui").join("css").is_dir() {
            continue;
        }
        if !on {
            rows.push(check(OK, format!("Steam ({name})"), "Sonata look off in Settings"));
        } else if steam_installed(&r) {
            rows.push(check(OK, format!("Steam ({name})"), "Sonata's window buttons"));
        } else {
            rows.push(check(FAIL, format!("Steam ({name})"), "Steam's own buttons (an update undid it): put back within a minute, or at the next login"));
        }
    }
    rows
}

// -- 2. the windows open now ------------------------------------------------------------------
fn uses_lib(pid: Option<i64>, lib: &str) -> bool {
    pid.is_some_and(|p| p > 1 && read(Path::new(&format!("/proc/{p}/maps"))).contains(lib))
}

fn own_frame_apps() -> BTreeSet<String> {
    let raw = sonata_backend::wfconfig::get("sonata-corners", "own_frame_apps", "");
    raw.split(|c: char| c.is_whitespace() || c == ',').filter(|w| !w.is_empty()).map(str::to_lowercase).collect()
}

/// The windows Wayfire lists, judged; `rounded`: the view ids sonata-corners
/// rounds (None: unknown -- an older build without "sonata/rounded").
pub fn window_rows_from(views: &[Value], rounded: Option<&BTreeSet<i64>>, own: &BTreeSet<String>) -> Vec<Row> {
    let mut rows = Vec::new();
    for v in views {
        if v.get("type").and_then(Value::as_str) != Some("toplevel") || !truthy(v.get("mapped")) {
            continue;
        }
        let app = v.get("app-id").and_then(Value::as_str).filter(|a| !a.is_empty()).unwrap_or("?");
        if app.contains("sonata2") {
            continue; // Sonata's own: drawn by Sonata itself
        }
        let title: String = v.get("title").and_then(Value::as_str).unwrap_or("").chars().take(40).collect();
        let what = format!("{app} -- {title}");
        if truthy(v.get("fullscreen")) {
            rows.push(check(OK, what, "full screen (no frame)"));
            continue;
        }
        let num = |o: Option<&Value>, k: &str| o.and_then(|o| o.get(k)).and_then(Value::as_f64).unwrap_or(0.0);
        let (g, b) = (v.get("geometry"), v.get("base-geometry"));
        let sonata_bar = num(g, "height") - num(b, "height") > 1.0 || num(g, "width") - num(b, "width") > 1.0;
        let pid = v.get("pid").and_then(Value::as_i64);
        let low = app.to_lowercase();
        let (frame, ok) = if sonata_bar {
            ("Sonata's title bar", true)
        } else if own.contains(&low) {
            ("its own frame, styled by Sonata", true)
        } else if CHROMIUM_IDS.iter().any(|c| low.starts_with(c)) || app.starts_with("chrome-") {
            // (chrome-<site>-Default: a Chromium web app -- Sonata's own, or a browser's)
            ("browser frame (GTK mode: Sonata's traffic lights)", true)
        } else if uses_lib(pid, "libadwaita-1") {
            ("libadwaita frame, Sonata's style", true)
        } else if uses_lib(pid, "libgtk-4") {
            ("GTK 4 frame, Sonata's style", true)
        } else {
            ("draws its own title bar: not Sonata's look", false)
        };
        let id = v.get("id").and_then(Value::as_i64);
        let rounds = rounded.is_none_or(|r| id.is_some_and(|i| r.contains(&i)));
        let expect_round = sonata_bar || own.contains(&low);
        if expect_round && !rounds {
            rows.push(check(FAIL, what, format!("{frame}; corners NOT rounded (sonata-corners not built or not loaded?)")));
        } else {
            let tail = if expect_round && rounded.is_some() { "; rounded corners" } else { "" };
            rows.push(check(if ok { OK } else { WARN }, what, format!("{frame}{tail}")));
        }
    }
    if rounded.is_none() {
        rows.push(check(WARN, "Rounded corners", "unknown: this sonata-corners build has no \"sonata/rounded\" (./install.sh rebuilds it)"));
    }
    rows
}

pub fn window_rows() -> Vec<Row> {
    use sonata_backend::wfipc;
    if !wfipc::available() {
        return vec![check(WARN, "Windows", "not in a Wayfire session: nothing to look at")];
    }
    let views = wfipc::call("window-rules/list-views", json!({})).and_then(|v| v.as_array().cloned()).unwrap_or_default();
    let rounded: Option<BTreeSet<i64>> = wfipc::call("sonata/rounded", json!({})).and_then(|r| r.get("rounded").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_i64).collect()));
    window_rows_from(&views, rounded.as_ref(), &own_frame_apps())
}

// -- the report ---------------------------------------------------------------------------------
pub fn build() -> Vec<Row> {
    let mut rows = vec![Row::Heading("Apps Sonata styles".into())];
    rows.extend(config_rows());
    rows.push(Row::Heading("Windows open now".into()));
    rows.extend(window_rows());
    rows
}

pub fn text(rows: &[Row], color: bool) -> String {
    let mut out: Vec<String> = Vec::new();
    for row in rows {
        match row {
            Row::Heading(h) => out.push(format!("{}{h}", if out.is_empty() { "" } else { "\n" })),
            Row::Check(status, what, detail) => {
                let tag = if color { format!("{}{:>4}{COLOR_END}", status.color(), status.0) } else { format!("{:>4}", status.0) };
                out.push(format!("[{tag}] {what}{}", if detail.is_empty() { String::new() } else { format!(" -- {detail}") }));
            }
        }
    }
    let bad = rows.iter().filter(|r| matches!(r, Row::Check(s, ..) if *s != OK)).count();
    out.push(String::new());
    out.push(if bad == 0 { "Every style is in place.".into() } else { format!("{bad} to look at.") });
    out.join("\n")
}

pub fn main() -> i32 {
    let rows = build();
    let tty = unsafe { libc::isatty(1) } == 1;
    println!("{}", text(&rows, tty));
    i32::from(rows.iter().any(|r| matches!(r, Row::Check(s, ..) if *s == FAIL)))
}

// -- the menu bar: a notification when something broke ------------------------------------------
/// The app (or setting) a row is about: a window's title left out -- it
/// changes (a browser tab, an unread count) and made the same problem new.
fn subject(what: &str) -> &str {
    what.split(" -- ").next().unwrap_or(what)
}

/// What to tell: FAILs, and windows drawing their own title bar (one line per app).
pub fn problems(rows: &[Row]) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for r in rows {
        let Row::Check(s, what, detail) = r else { continue };
        let p = if *s == FAIL {
            format!("{}{}", subject(what), if what.contains(" -- ") { " (corners)" } else { "" })
        } else if *s == WARN && detail.contains("draws its own title bar") {
            format!("{} (own title bar)", subject(what))
        } else {
            continue;
        };
        if !out.contains(&p) {
            out.push(p);
        }
    }
    out
}

/// Problems not told before (remembered in ~/.cache/sonata2/stylecheck.json);
/// one that's fixed is forgotten, so it's told again if it comes back.
pub fn new_problems(rows: &[Row], seen_path: &Path) -> Vec<String> {
    let seen: BTreeSet<String> = serde_json::from_str::<Vec<String>>(&read(seen_path)).unwrap_or_default().into_iter().collect();
    let now = problems(rows);
    let new: Vec<String> = now.iter().filter(|p| !seen.contains(*p)).cloned().collect();
    // forgotten only once it's seen fixed: an app that is simply closed now
    // keeps its problem (each reopening told it again)
    let present: BTreeSet<&str> = rows.iter().filter_map(|r| if let Row::Check(_, w, _) = r { Some(subject(w)) } else { None }).collect();
    let mut keep: BTreeSet<String> = seen.into_iter().filter(|p| !present.contains(p.split(" (").next().unwrap_or(p))).collect();
    keep.extend(now);
    if let Some(d) = seen_path.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let _ = std::fs::write(seen_path, serde_json::to_string(&keep.into_iter().collect::<Vec<_>>()).unwrap_or_default());
    new
}

#[cfg(test)]
mod tests {
    use super::*;

    fn view(i: i64, app: &str, sonata_bar: bool) -> Value {
        json!({"id": i, "app-id": app, "title": "t", "type": "toplevel", "mapped": true, "pid": -1,
               "geometry": {"x": 0, "y": 0, "width": 800, "height": 600},
               "base-geometry": {"x": 0, "y": 0, "width": 800, "height": if sonata_bar { 562 } else { 600 }},
               "fullscreen": false})
    }

    fn rows(views: &[Value], rounded: Option<&[i64]>) -> std::collections::BTreeMap<String, (Status, String)> {
        let own: BTreeSet<String> = ["steam".to_string()].into();
        let rounded: Option<BTreeSet<i64>> = rounded.map(|r| r.iter().copied().collect());
        window_rows_from(views, rounded.as_ref(), &own)
            .into_iter()
            .filter_map(|r| if let Row::Check(s, w, d) = r { Some((subject(&w).to_string(), (s, d))) } else { None })
            .collect()
    }

    #[test]
    fn test_frames_and_corners() {
        let mut mpv = view(6, "mpv", true);
        mpv["mapped"] = json!(false);
        let r = rows(
            &[view(1, "code", true), view(2, "steam", false), view(3, "google-chrome", false), view(4, "Spotify", false), view(5, "io.github.vinioliveiras.sonata2.files", true), mpv],
            Some(&[1, 2]),
        );
        assert_eq!(r["code"].0, OK);
        assert!(r["code"].1.contains("Sonata's title bar"));
        assert_eq!(r["steam"].0, OK); // own frame Sonata styles, rounded
        assert_eq!(r["google-chrome"].0, OK);
        assert_eq!(r["Spotify"].0, WARN); // its own title bar
        assert!(!r.contains_key("io.github.vinioliveiras.sonata2.files")); // Sonata's own
        assert!(!r.contains_key("mpv")); // not shown
    }

    #[test]
    fn test_corners_missing_is_a_failure() {
        let r = rows(&[view(1, "code", true), view(2, "steam", false)], Some(&[]));
        assert_eq!(r["code"].0, FAIL);
        assert_eq!(r["steam"].0, FAIL);
    }

    /// Review: Sonata's Chromium web apps (chrome-<site>-Default) were
    /// reported as drawing their own title bar.
    #[test]
    fn test_chromium_web_apps_are_styled() {
        let r = rows(&[view(1, "chrome-mail.google.com__mail_u_0_-Default", false)], Some(&[]));
        assert_eq!(r["chrome-mail.google.com__mail_u_0_-Default"].0, OK);
    }

    #[test]
    fn test_old_plugin_build() {
        let rows = window_rows_from(&[view(1, "code", true)], None, &BTreeSet::new());
        assert!(matches!(&rows[0], Row::Check(s, ..) if *s == OK)); // can't tell: not a failure
        assert!(matches!(rows.last().unwrap(), Row::Check(_, w, _) if w == "Rounded corners"));
    }

    #[test]
    fn test_told_once_and_again_when_it_comes_back() {
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join("seen.json");
        let bad = vec![check(FAIL, "VS Code", "x"), check(WARN, "Spotify -- Spotify", "draws its own title bar: not ...")];
        assert_eq!(new_problems(&bad, &path), ["VS Code", "Spotify (own title bar)"]);
        assert!(new_problems(&bad, &path).is_empty()); // not twice
        let fixed = vec![check(OK, "VS Code", "x"), check(OK, "Spotify -- Spotify", "y")];
        assert!(new_problems(&fixed, &path).is_empty()); // fixed: forgotten
        assert_eq!(new_problems(&bad[..1], &path), ["VS Code"]); // back: told again
    }

    /// Review: a window's title (a browser tab, an unread count) was part of
    /// the problem's name -- each change told it again; and a closed app's
    /// problem was forgotten, so each reopening told it again.
    #[test]
    fn test_a_new_title_or_a_closed_window_is_not_new() {
        let d = tempfile::tempdir().unwrap();
        let path = d.path().join("seen.json");
        let a = vec![check(FAIL, "code -- main.py - VS Code", "corners NOT rounded")];
        let b = vec![check(FAIL, "code -- other.py - VS Code", "corners NOT rounded")];
        assert_eq!(new_problems(&a, &path), ["code (corners)"]);
        assert!(new_problems(&b, &path).is_empty()); // another title: the same problem
        assert!(new_problems(&[], &path).is_empty()); // closed: still remembered
        assert!(new_problems(&a, &path).is_empty()); // reopened: not told again
    }

    /// Vini: Sonata always forces its title bar on VS Code: "native" is
    /// OK, its own bar ("custom") is reported (set again at the next login).
    #[test]
    fn test_vscode_on_sonatas_bar() {
        let d = tempfile::tempdir().unwrap();
        let cfg = d.path().join(".config");
        std::fs::create_dir_all(cfg.join("Code/User")).unwrap();
        for (style, want) in [("native", OK), ("custom", WARN)] {
            std::fs::write(cfg.join("Code/User/settings.json"), format!("{{\n    \"window.titleBarStyle\": \"{style}\"\n}}")).unwrap();
            let rows = config_rows_in(&cfg, true, Vec::new());
            let code = rows.iter().find_map(|r| if let Row::Check(s, w, _) = r { (w == "Code").then_some(*s) } else { None });
            assert_eq!(code, Some(want), "{style}");
        }
        assert!(!code_native("// \"window.titleBarStyle\": \"native\""));
        let off = config_rows_in(&cfg, false, Vec::new());
        assert_eq!(off.len(), 1);
    }

    #[test]
    fn report_text_counts_what_to_look_at() {
        let rows = vec![Row::Heading("Apps Sonata styles".into()), check(OK, "a", ""), Row::Heading("Windows open now".into()), check(WARN, "b", "c")];
        let t = text(&rows, false);
        assert!(t.starts_with("Apps Sonata styles\n[  ok] a\n\nWindows open now\n[warn] b -- c\n"));
        assert!(t.ends_with("\n1 to look at."));
        assert!(text(&rows[..2], false).ends_with("Every style is in place."));
    }
}
