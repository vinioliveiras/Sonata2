//! Sonata title bars for every app (sonata2/titlebars.py; Settings >
//! Appearance, on by default).
//!
//! Wayfire asks apps for server-side title bars, drawn by pixdecor like
//! the rest of Sonata. Sonata forces its title bar on every app that has a
//! way to ask for it, at every login (Vini: "everything forced to Sonata's
//! bars"). Apps with no such setting keep their own -- forcing one over
//! theirs would show two title bars.
//!
//! - Every Chromium-based browser (Flatpak ones too), found by its profile
//!   folder: "Use system title bar and borders", GTK mode for the colours
//!   (set the first time). Skipped while the browser runs (it would write
//!   its own value back on quit): next login.
//! - VS Code / VSCodium / Code - OSS / Insiders (Flatpak too):
//!   "window.titleBarStyle": "native" at every login; off: "custom".
//! - Firefox, Thunderbird, LibreWolf, Floorp (every profile): user.js
//!   browser.tabs.inTitlebar = 0 (tabs under the system title bar).
//! - Obsidian: obsidian.json "frame": "native". Vesktop: "titleBar": "system".
//!
//! Not ported yet: GTK 4 / libadwaita apps' style (adwstyle.py): the
//! Python session still writes it.

use crate::frame;
use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

pub const VESKTOP: [&str; 2] = ["~/.config/vesktop/settings.json", "~/.var/app/dev.vencord.Vesktop/config/vesktop/settings.json"];
pub const OBSIDIAN: [&str; 2] = ["~/.config/obsidian/obsidian.json", "~/.var/app/md.obsidian.Obsidian/config/obsidian/obsidian.json"];
pub const CODE: [&str; 4] = ["Code", "Code - OSS", "VSCodium", "Code - Insiders"];
/// Flatpak builds keep their settings in their own sandbox
pub const CODE_FLATPAK: [&str; 3] = ["~/.var/app/com.visualstudio.code/config/Code", "~/.var/app/com.vscodium.codium/config/VSCodium", "~/.var/app/com.visualstudio.code.insiders/config/Code - Insiders"];
pub const MOZILLA: [&str; 6] = ["~/.mozilla/firefox", "~/.config/mozilla/firefox", "~/.thunderbird", "~/.librewolf", "~/.floorp", "~/.var/app/org.mozilla.firefox/.mozilla/firefox"];
/// Chromium's "Appearance > Mode": 0 Classic, 1 GTK, 2 Qt
pub const GTK_MODE: i64 = 1;
pub const QT_MODE: i64 = 2;
pub const MOZ_LINE: &str = "user_pref(\"browser.tabs.inTitlebar\", 0);  // Sonata title bars (Settings > Appearance)";
/// always: Firefox's Open/Save dialogs through the portal (Sonata's panels)
pub const MOZ_PORTAL: &str = "user_pref(\"widget.use-xdg-desktop-portal.file-picker\", 1);  // Sonata Open/Save panels";
/// the launchers' flag files (Arch packages read them; others ignore them)
pub const CHROMIUM_FLAGS: [&str; 8] = ["chrome-flags.conf", "chrome-beta-flags.conf", "chrome-dev-flags.conf", "chromium-flags.conf", "brave-flags.conf", "vivaldi-stable.conf", "thorium-flags.conf", "microsoft-edge-stable-flags.conf"];
pub const GTK3_FLAG: &str = "--gtk-version=3";

pub fn enabled() -> bool {
    crate::util::truthy(crate::util::load("appearance", json!({"system_titlebars": true})).get("system_titlebars"))
}

/// The option on (or off) in every app that has one.
pub fn apply(on: Option<bool>) {
    let on = on.unwrap_or_else(enabled);
    let cfg = sonata_config::config_home();
    for base in chromium_roots(&cfg) {
        if base.join("SingletonLock").symlink_metadata().is_ok() {
            continue; // running: it would write its own value back on quit
        }
        let Ok(rd) = std::fs::read_dir(&base) else { continue };
        for p in rd.flatten() {
            let n = p.file_name().to_string_lossy().into_owned();
            if n == "Default" || n.starts_with("Profile ") {
                browser_pref(&base.join(&n).join("Preferences"), on);
            }
        }
    }
    chromium_gtk3(&cfg);
    for base in MOZILLA {
        let Ok(rd) = std::fs::read_dir(crate::util::expand(base)) else { continue };
        for prof in rd.flatten().map(|e| e.path()) {
            if prof.join("prefs.js").is_file() {
                mozilla_userjs(&prof.join("user.js"), on);
            }
        }
    }
    code_titlebar(on, Some(&cfg));
    for p in OBSIDIAN {
        obsidian_setting(&crate::util::expand(p), on);
    }
    for p in VESKTOP {
        vesktop_setting(&crate::util::expand(p), on);
    }
}

fn installed(flags_file: &str) -> bool {
    let base = flags_file.replace("-flags.conf", "").replace(".conf", "");
    [base.clone(), format!("{base}-stable"), format!("google-{base}"), format!("google-{base}-stable")].iter().any(|b| sonata_backend::run::which(b).is_some())
}

/// Chromium draws its GTK-mode window buttons right only through GTK 3
/// (Sonata's traffic lights); with GTK 4 they come out as plain glyphs, cut
/// at the edge. Asked for in each browser's flags file (existing flags kept).
pub fn chromium_gtk3(cfg: &Path) {
    for name in CHROMIUM_FLAGS {
        let path = cfg.join(name);
        let lines = match std::fs::read_to_string(&path) {
            Ok(t) => t.lines().map(String::from).collect::<Vec<_>>(),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                if !installed(name) {
                    continue; // only for browsers that are there
                }
                Vec::new()
            }
            Err(_) => continue,
        };
        if lines.iter().any(|l| l.trim().starts_with("--gtk-version")) {
            continue; // the user's own choice
        }
        let mut all = lines;
        all.push("# Sonata: its traffic lights in GTK mode".into());
        all.push(GTK3_FLAG.into());
        crate::util::write(&path, &(all.join("\n") + "\n"));
    }
}

fn listdirs(path: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = std::fs::read_dir(path).map(|rd| rd.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect()).unwrap_or_default();
    out.sort();
    out
}

/// Every Chromium-based browser's data folder, found by what they all
/// have: a "Local State" file next to Default/ (one or two levels under
/// ~/.config; Flatpak browsers under ~/.var/app too). Electron apps keep no
/// Default/ profile, so they are left alone.
pub fn chromium_roots(cfg: &Path) -> Vec<PathBuf> {
    let mut bases = vec![cfg.to_path_buf()];
    bases.extend(listdirs(&crate::util::home().join(".var/app")).into_iter().map(|p| p.join("config")));
    let mut out = Vec::new();
    for base in bases {
        for d in listdirs(&base) {
            let mut cands = vec![d.clone()];
            cands.extend(listdirs(&d));
            for c in cands {
                if c.join("Local State").is_file() && c.join("Default").is_dir() {
                    out.push(c);
                }
            }
        }
    }
    out
}

/// Sonata's title bar above the tabs while the option is on (Chromium's
/// "Use system title bar and borders"), every login; its own frame when
/// off. A profile that never chose an Appearance > Mode gets GTK.
pub fn browser_pref(path: &Path, on: bool) {
    let Some(Value::Object(mut prefs)) = std::fs::read(path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) else { return };
    let mut changed = false;
    {
        let ext = prefs.entry("extensions").or_insert_with(|| json!({}));
        if let Some(theme) = ext.as_object_mut().map(|e| e.entry("theme").or_insert_with(|| json!({}))).and_then(Value::as_object_mut) {
            // never chosen: GTK, like Chrome on a Mac. Qt mode hangs Chromium in
            // this session: back to GTK, or the browser would hang at every start
            let mode = theme.get("system_theme").and_then(Value::as_i64);
            if theme.get("system_theme").is_none_or(Value::is_null) || mode == Some(QT_MODE) {
                theme.insert("system_theme".into(), json!(GTK_MODE));
                changed = true;
            }
        }
    }
    let own_frame = !on;
    if let Some(browser) = prefs.entry("browser").or_insert_with(|| json!({})).as_object_mut() {
        if browser.get("custom_chrome_frame").and_then(Value::as_bool) != Some(own_frame) {
            browser.insert("custom_chrome_frame".into(), json!(own_frame));
            changed = true;
        }
    }
    if changed {
        crate::util::write(path, &serde_json::to_string(&Value::Object(prefs)).unwrap_or_default());
    }
}

/// user.js is read at every start (and overrides prefs.js): our one marked
/// line in or out.
pub fn mozilla_userjs(path: &Path, on: bool) {
    let lines = crate::util::read_lines(path);
    let mut new: Vec<String> = lines.iter().filter(|l| !l.contains("Sonata title bars") && !l.contains("Sonata Open/Save panels")).cloned().collect();
    if on {
        new.push(MOZ_LINE.into());
    }
    new.push(MOZ_PORTAL.into());
    if new != lines {
        crate::util::write(path, &(new.join("\n") + "\n"));
    }
}

/// The config folders of the VS Code family that has run here.
pub fn code_dirs(cfg: Option<&Path>) -> Vec<PathBuf> {
    let cfg = cfg.map(Path::to_path_buf).unwrap_or_else(|| crate::util::home().join(".config"));
    CODE.iter().map(|a| cfg.join(a)).chain(CODE_FLATPAK.iter().map(|p| crate::util::expand(p))).filter(|d| d.is_dir()).collect()
}

/// VS Code and its family: Sonata's title bar ("native") while the option
/// is on -- set every time, whatever was chosen since -- their own
/// ("custom") when it's off. Only where one has run (its folder exists).
pub fn code_titlebar(on: bool, cfg: Option<&Path>) {
    for d in code_dirs(cfg) {
        code_setting(&d.join("User/settings.json"), if on { "native" } else { "custom" });
    }
}

/// Where the live "window.titleBarStyle" value is: (start, end) of its
/// quoted value -- never a commented-out "// ..." line's.
fn live_value(text: &str) -> Option<(usize, usize)> {
    let key = "\"window.titleBarStyle\"";
    let mut line_start = 0;
    for line in text.split_inclusive('\n') {
        if let Some(k) = line.find(key) {
            if !line[..k].contains("//") {
                let after = line_start + k + key.len();
                let rest = &text[after..];
                let colon = rest.len() - rest.trim_start().len();
                if rest[colon..].starts_with(':') {
                    let r2 = &rest[colon + 1..];
                    let q = colon + 1 + (r2.len() - r2.trim_start().len());
                    if rest[q..].starts_with('"') {
                        if let Some(end) = rest[q + 1..].find('"') {
                            return Some((after + q, after + q + 1 + end + 1));
                        }
                    }
                }
            }
        }
        line_start += line.len();
    }
    None
}

/// VS Code's settings.json allows comments (JSONC): edit the text.
pub fn code_setting(path: &Path, style: &str) {
    let text = std::fs::read_to_string(path).unwrap_or_else(|_| "{\n}\n".into());
    let new = if let Some((a, b)) = live_value(&text) {
        format!("{}\"{style}\"{}", &text[..a], &text[b..])
    } else {
        let Some(brace) = text.find('{') else { return };
        let rest = &text[brace + 1..];
        let comma = if !rest.trim().is_empty() && !rest.trim().starts_with('}') { "," } else { "" };
        format!("{}\n    \"window.titleBarStyle\": \"{style}\"{comma}{rest}", &text[..brace + 1])
    };
    if new != text {
        if let Some(d) = path.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        crate::util::write(path, &new);
    }
}

fn json_object(path: &Path) -> Option<Map<String, Value>> {
    match std::fs::read(path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(Value::Object(m)) => Some(m),
        _ => None,
    }
}

/// Obsidian: "frame" -- "native" is the window manager's title bar,
/// "hidden" its own. Only for an Obsidian that has run once.
pub fn obsidian_setting(path: &Path, on: bool) {
    let Some(data) = json_object(path) else { return };
    let mut new = data.clone();
    new.insert("frame".into(), json!(if on { "native" } else { "hidden" }));
    if new != data {
        crate::util::write(path, &sonata_config::to_python_json(&Value::Object(new)));
    }
}

/// Vesktop: "titleBar" ("system" = the window manager's; older builds:
/// "customTitleBar" false). Only for a Vesktop that has run once.
pub fn vesktop_setting(path: &Path, on: bool) {
    let Some(data) = json_object(path) else { return };
    let mut new = data.clone();
    new.insert("titleBar".into(), json!(if on { "system" } else { "discord" }));
    new.insert("customTitleBar".into(), json!(!on));
    if new != data {
        crate::util::write(path, &sonata_config::to_python_json_indent(&Value::Object(new), 4));
    }
}

// -- colours of the frames Wayfire draws -----------------------------------------------
// What Wayfire blurs behind: Sonata's own surfaces (Dock, menu bar,
// sidebars) and popups. Other apps' windows no more: their title bars are
// opaque now, so blurring behind them only cost GPU time on every frame
// they changed -- and the blur pass is where the Radeon 680M hung.
// (ui/glass.py's rule, which sonata-ui's glass doesn't have yet.)
const BLUR_ALL: &str = "app_id contains \"sonata2\" | type is \"unmanaged\"";
const BLUR_NONE: &str = "app_id is \"sonata2-blur-nothing\"";
const NEVER_BLUR: &str = "app_id is \"sonata2-mission\" | app_id is \"sonata2-spaces\" | app_id is \"sonata2-wallpaper\" | app_id is \"sonata2-gamebar\" | app_id is \"sonata2-gamebar-pin\" | app_id is \"sonata2-notification-center\"";
const THRESHOLD: f64 = 0.5;
const THRESHOLD_MIN: f64 = 0.36;
const BLUR_DEFAULT: i64 = 50;
const OFFSET_RANGE: (f64, f64) = (1.1, 5.66);
pub const GLASS_BARS: &str = " | type is \"toplevel\"";

fn blur_part(item: &str) -> &'static str {
    match item {
        "dock" => "app_id is \"sonata2-dock\"",
        "menubar" => "app_id is \"sonata2-topbar\"",
        "menus" => "app_id contains \"sonata2-\" | type is \"unmanaged\"",
        "windows" => "app_id contains \"sonata2.\"",
        _ => "app_id is \"sonata2-launchpad-window\"",
    }
}

pub fn without_never(rule: &str) -> String {
    if rule.contains("contains \"sonata2") { format!("({rule}) & !({NEVER_BLUR})") } else { rule.to_string() }
}

/// Never behind Mission Control or the wallpaper.
pub fn blur() -> String {
    without_never(BLUR_ALL)
}

pub fn blur_rule(parts: &[(&str, sonata_ui::glass::Part)]) -> String {
    let mut on: Vec<&str> = parts.iter().filter(|(_, p)| p.on).map(|(i, _)| *i).collect();
    if on.len() == parts.len() {
        return without_never(BLUR_ALL);
    }
    if on.contains(&"menus") {
        // it covers the Dock's, the menu bar's and Launchpad's surfaces too
        on.retain(|i| !matches!(*i, "dock" | "menubar" | "launchpad"));
    }
    let mut rule = on.iter().map(|i| blur_part(i)).collect::<Vec<_>>().join(" | ");
    if on.contains(&"dock") {
        rule += " | type is \"unmanaged\""; // an open Dock folder (a popup) has the Dock's glass
    }
    if rule.is_empty() { BLUR_NONE.into() } else { without_never(&rule) }
}

/// The rule `cur` should become (None: left as it is). Only one of
/// Sonata's ("contains \"sonata2"): Glass all off, or the user's own rule,
/// stay; glass title bars on stay on; nothing while a game has the lighter
/// effects (`light`: gamemode's file), whose rule is put back when it ends.
pub fn refreshed_blur_rule(cur: &str, light: bool, parts: &[(&str, sonata_ui::glass::Part)]) -> Option<String> {
    let cur = cur.trim();
    if light || !cur.contains("contains \"sonata2") {
        return None;
    }
    let want = blur_rule(parts) + if cur.ends_with(GLASS_BARS) { GLASS_BARS } else { "" };
    (cur != want).then_some(want)
}

/// The blur rule Wayfire runs with, brought up to this version's
/// (ui/glass.py refresh_blur_rule): it is stored in Sonata's Wayfire
/// settings when the look changes, so a surface added to NEVER_BLUR (Vini:
/// the Game Bar, still blurred after the update) kept its blur until the
/// look was changed again. True: changed.
///
/// The menu bar calls it once, 2 s after it starts, in a thread (it
/// writes Wayfire's config). Never on trial: the trial build writes no
/// Wayfire settings of the Python Sonata, which refreshes the rule itself.
pub fn refresh_blur_rule() -> bool {
    if sonata_config::side_by_side() {
        return false;
    }
    let cur = sonata_backend::wfconfig::get("blur", "blur_by_default", "");
    match refreshed_blur_rule(&cur, crate::gamemode::light_path().exists(), &sonata_ui::glass::settings()) {
        Some(want) => sonata_backend::wfconfig::set("blur", "blur_by_default", &want),
        None => false,
    }
}

pub fn blur_strength(appearance: &Map<String, Value>) -> i64 {
    appearance.get("glass").and_then(|g| g.get("blur")).and_then(Value::as_f64).map(|b| b.clamp(0.0, 100.0) as i64).unwrap_or(BLUR_DEFAULT)
}

pub fn blur_offset(strength: i64) -> f64 {
    let (lo, hi) = OFFSET_RANGE;
    ((lo + (hi - lo) * strength.clamp(0, 100) as f64 / 100.0) * 100.0).round_ties_even() / 100.0
}

fn css_alpha(css: &str) -> f64 {
    let css = css.trim();
    css.strip_prefix("rgba(").and_then(|r| r.trim_end_matches(')').split(',').nth(3)).and_then(|a| a.trim().parse().ok()).unwrap_or(1.0)
}

fn glass_token(item: &str) -> &'static str {
    match item {
        "menubar" => "bar_bg",
        "windows" => "window_glass",
        _ => "glass_tint",
    }
}

/// Wayfire's alpha_threshold for the alphas chosen: just under the most
/// see-through part that is on, 0.5 otherwise.
pub fn blur_threshold(parts: &[(&str, sonata_ui::glass::Part)], tokens: &sonata_ui::tokens::Values) -> f64 {
    let t = parts.iter().filter(|(_, p)| p.on).map(|(i, p)| p.alpha.unwrap_or_else(|| css_alpha(tokens.get(glass_token(i)).map(String::as_str).unwrap_or("")))).map(|a| a - 0.02).fold(THRESHOLD, f64::min);
    (t.max(THRESHOLD_MIN) * 100.0).round_ties_even() / 100.0
}

fn reduce_transparency(appearance: &Map<String, Value>) -> bool {
    crate::util::truthy(appearance.get("reduce_transparency"))
}

/// Glass title bars wanted and possible (not with Reduce transparency).
pub fn glass_bars() -> bool {
    let a = frame::appearance();
    let windows_on = sonata_ui::glass::settings().iter().any(|(i, p)| *i == "windows" && p.on);
    crate::util::truthy(a.get("glass_titlebars")) && !reduce_transparency(&a) && windows_on
}

/// The Wayfire options for the title bars pixdecor (and Wayfire's own
/// decoration) draw in this appearance: [(section, key, value)].
pub fn color_options(dark: bool, glass: bool, appearance: &Map<String, Value>, parts: &[(&'static str, sonata_ui::glass::Part)]) -> Vec<(&'static str, &'static str, String)> {
    let mut t = sonata_ui::tokens::palette(dark);
    let get = |t: &sonata_ui::tokens::Values, k: &str| t.get(k).cloned().unwrap_or_default();
    let windows = parts.iter().find(|(i, _)| *i == "windows").map(|(_, p)| *p).unwrap_or(sonata_ui::glass::Part { on: true, alpha: None });
    if glass {
        for (bar, g) in [("titlebar_bg", "titlebar_glass"), ("titlebar_bg_inactive", "titlebar_glass_inactive")] {
            let v = match windows.alpha {
                Some(a) => sonata_ui::glass::with_alpha(&get(&t, g), a),
                None => get(&t, g),
            };
            t.insert(bar.into(), v);
        }
    }
    let mut out = Vec::new();
    let mut rule = blur_rule(parts); // solid parts: no blur pass behind them
    if glass {
        rule += GLASS_BARS;
    }
    out.push(("blur", "blur_by_default", rule));
    out.push(("blur", "kawase_offset", format!("{}", blur_offset(blur_strength(appearance))))); // one strength for all
    out.push(("blur", "alpha_threshold", format!("{}", blur_threshold(parts, &t)))); // see-through parts stay frosted
    out.push(("sonata-corners", "outline", format!("\\{}", frame::wayfire_color(&get(&t, "window_outline"), false))));
    // the window's background while it's resized without its contents (sonata-resize):
    // the windows' glass when it is on (Vini: frosted like the rest), else solid
    let see_through = windows.on && !reduce_transparency(appearance);
    let fill = if see_through { sonata_ui::glass::material("windows", &t, true, parts) } else { get(&t, "window_bg") };
    out.push(("sonata-resize", "fill", format!("\\{}", frame::wayfire_color(&fill, false))));
    out.push(("sonata-resize", "border", format!("\\{}", frame::wayfire_color(&get(&t, "window_outline"), false))));
    let keys = ["titlebar_bg", "titlebar_bg_inactive", "titlebar_text", "titlebar_text_inactive"];
    for (k, tok) in ["fg_color", "bg_color", "fg_text_color", "bg_text_color"].into_iter().zip(keys) {
        out.push(("pixdecor", k, format!("\\{}", frame::wayfire_color(&get(&t, tok), true))));
    }
    for (k, tok) in ["active_color", "inactive_color", "font_color"].into_iter().zip(keys) {
        out.push(("decoration", k, format!("\\{}", frame::wayfire_color(&get(&t, tok), false)))); // Wayfire's own: straight
    }
    out
}

/// Title bars pixdecor (and Wayfire's own decoration) draw, for the
/// appearance (opaque like GNOME apps' header bars). Wayfire reloads its
/// config live.
pub fn apply_colors(dark: bool) {
    let parts = sonata_ui::glass::settings();
    for (s, k, v) in color_options(dark, glass_bars(), &frame::appearance(), &parts) {
        sonata_backend::wfconfig::set(s, k, &v);
    }
    crate::trafficlights::apply_wayfire(dark); // the buttons' pictures for this look
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn read(p: &Path) -> String {
        fs::read_to_string(p).unwrap()
    }

    /// VS Code settings.json (JSONC) gets the key with a comma before
    /// existing keys, and an existing value is replaced in place.
    #[test]
    fn vscode_setting_inserted_and_replaced() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("Code/User/settings.json");
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(&p, "{\n    // mine\n    \"editor.fontSize\": 14\n}\n").unwrap();
        code_setting(&p, "native");
        assert!(read(&p).contains("\"window.titleBarStyle\": \"native\","));
        assert!(read(&p).contains("\"editor.fontSize\": 14"));
        code_setting(&p, "custom");
        assert_eq!(read(&p).matches("titleBarStyle").count(), 1);
        assert!(read(&p).contains("\"window.titleBarStyle\": \"custom\""));
        // a brand-new settings file stays valid JSON
        let q = d.path().join("new/User/settings.json");
        code_setting(&q, "native");
        assert_eq!(serde_json::from_str::<Value>(&read(&q)).unwrap(), json!({"window.titleBarStyle": "native"}));
        // a commented-out key is not the setting: the live one is added
        let c = d.path().join("settings.json");
        fs::write(&c, "{\n    // \"window.titleBarStyle\": \"custom\",\n    \"a\": 1\n}\n").unwrap();
        code_setting(&c, "native");
        assert_eq!(read(&c).lines().filter(|l| l.contains("titleBarStyle") && !l.contains("//")).count(), 1);
    }

    /// user.js keeps the user's prefs; the title bar line comes and goes,
    /// the portal line stays, and running twice changes nothing.
    #[test]
    fn firefox_userjs_lines_toggle_and_user_lines_stay() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("user.js");
        fs::write(&p, "user_pref(\"a\", 1);\n").unwrap();
        mozilla_userjs(&p, true);
        let on = read(&p);
        assert!(on.contains(MOZ_LINE) && on.contains(MOZ_PORTAL));
        mozilla_userjs(&p, true);
        assert_eq!(read(&p), on);
        mozilla_userjs(&p, false);
        let off = read(&p);
        assert!(!off.contains(MOZ_LINE) && off.starts_with("user_pref(\"a\", 1);") && off.contains(MOZ_PORTAL));
    }

    #[test]
    fn vesktop_and_obsidian_only_when_they_ran_once() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("vesktop/settings.json");
        vesktop_setting(&p, true);
        assert!(!p.exists());
        fs::create_dir_all(p.parent().unwrap()).unwrap();
        fs::write(&p, r#"{"minimizeToTray": true}"#).unwrap();
        vesktop_setting(&p, true);
        assert_eq!(serde_json::from_str::<Value>(&read(&p)).unwrap(), json!({"minimizeToTray": true, "titleBar": "system", "customTitleBar": false}));
        let o = d.path().join("obsidian/obsidian.json");
        obsidian_setting(&o, true);
        assert!(!o.exists());
        fs::create_dir_all(o.parent().unwrap()).unwrap();
        fs::write(&o, r#"{"vaults": {}, "frame": "hidden"}"#).unwrap();
        obsidian_setting(&o, true);
        assert_eq!(serde_json::from_str::<Value>(&read(&o)).unwrap(), json!({"vaults": {}, "frame": "native"}));
        obsidian_setting(&o, false);
        assert_eq!(serde_json::from_str::<Value>(&read(&o)).unwrap()["frame"], "hidden");
    }

    /// Vini: "everything forced to Sonata's bars": Sonata's title bar above
    /// the tabs at every login; Qt mode (hangs Chromium here) becomes GTK;
    /// its own frame when the option is off.
    #[test]
    fn chromium_always_gets_sonatas_title_bar() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("Preferences");
        fs::write(&p, r#"{"extensions": {"theme": {"system_theme": 2}}, "other": 1}"#).unwrap();
        browser_pref(&p, true);
        let prefs: Value = serde_json::from_str(&read(&p)).unwrap();
        assert_eq!(prefs["extensions"]["theme"]["system_theme"], GTK_MODE);
        assert_eq!(prefs["browser"]["custom_chrome_frame"], false);
        assert_eq!(prefs["other"], 1);
        fs::write(&p, r#"{"browser": {"custom_chrome_frame": true}, "extensions": {"theme": {"system_theme": 1, "id": "abc"}}}"#).unwrap();
        browser_pref(&p, true);
        assert_eq!(serde_json::from_str::<Value>(&read(&p)).unwrap()["browser"]["custom_chrome_frame"], false);
        browser_pref(&p, false);
        assert_eq!(serde_json::from_str::<Value>(&read(&p)).unwrap()["browser"]["custom_chrome_frame"], true);
    }

    #[test]
    fn chromium_roots_and_gtk3_flags() {
        let d = tempfile::tempdir().unwrap();
        let chrome = d.path().join("google-chrome");
        fs::create_dir_all(chrome.join("Default")).unwrap();
        fs::write(chrome.join("Local State"), "{}").unwrap();
        fs::create_dir_all(d.path().join("Electron/x")).unwrap();
        assert_eq!(chromium_roots(d.path()), [chrome]);
        fs::write(d.path().join("chrome-flags.conf"), "--x\n").unwrap();
        fs::write(d.path().join("brave-flags.conf"), "--gtk-version=4\n").unwrap();
        chromium_gtk3(d.path());
        assert_eq!(read(&d.path().join("chrome-flags.conf")), "--x\n# Sonata: its traffic lights in GTK mode\n--gtk-version=3\n");
        assert_eq!(read(&d.path().join("brave-flags.conf")), "--gtk-version=4\n"); // the user's own choice
    }

    /// Other apps' windows aren't blurred behind: every window was blurred
    /// on every frame of a move/resize (heavy; the Radeon 680M hung there).
    #[test]
    fn only_sonata_surfaces_are_blurred() {
        assert!(!blur().contains("toplevel") && blur().contains("app_id contains \"sonata2\""));
        let ini = read(&crate::shortcuts::default_ini());
        // (config/wayfire.ini's rule still lacks the Notification Center, which
        // Python's NEVER_BLUR has: only the start is checked here)
        assert!(ini.contains(&format!("blur_by_default = ({BLUR_ALL}) & !(app_id is \"sonata2-mission\"")));
        assert!(ini.contains(&format!("kawase_offset = {}", blur_offset(BLUR_DEFAULT))));
        let parts = sonata_ui::glass::settings_from(&Map::new(), true);
        for dark in [false, true] {
            let opts = color_options(dark, false, &Map::new(), &parts);
            assert_eq!(opts[0], ("blur", "blur_by_default", blur_rule(&parts)));
            assert!(!opts[0].2.contains("type is \"toplevel\""));
        }
        // glass title bars: every window blurred again, see-through bars
        let opts = color_options(true, true, &Map::new(), &parts);
        assert_eq!(opts[0].2, blur_rule(&parts) + GLASS_BARS);
        let glass = frame::wayfire_color(&sonata_ui::tokens::palette(true)["titlebar_glass"], true);
        assert!(opts.contains(&("pixdecor", "fg_color", format!("\\{glass}"))));
    }

    #[test]
    fn blur_rule_per_part() {
        use sonata_ui::glass::Part;
        let p = |on: &[&str]| -> Vec<(&'static str, Part)> { sonata_ui::glass::ITEMS.iter().map(|i| (*i, Part { on: on.contains(i), alpha: None })).collect() };
        assert_eq!(blur_rule(&p(&[])), BLUR_NONE);
        assert_eq!(blur_rule(&p(&["windows"])), format!("(app_id contains \"sonata2.\") & !({NEVER_BLUR})"));
        assert_eq!(blur_rule(&p(&["dock"])), "app_id is \"sonata2-dock\" | type is \"unmanaged\"");
        let t = sonata_ui::tokens::palette(false);
        assert_eq!(blur_threshold(&p(&["dock"]), &t), 0.43); // light glass is under 0.5
        assert_eq!(blur_threshold(&p(&[]), &t), 0.5);
    }

    fn all_on() -> Vec<(&'static str, sonata_ui::glass::Part)> {
        sonata_ui::glass::ITEMS.iter().map(|i| (*i, sonata_ui::glass::Part { on: true, alpha: None })).collect()
    }

    /// Vini: the Game Bar still blurred after the update -- the rule stored
    /// when the look last changed was an older version's.
    #[test]
    fn test_old_blur_rule_updated() {
        let old = "(app_id contains \"sonata2\" | type is \"unmanaged\") & !(app_id is \"sonata2-mission\")";
        let want = refreshed_blur_rule(old, false, &all_on()).unwrap();
        assert!(want.contains("app_id is \"sonata2-gamebar\""));
        assert_eq!(want, blur_rule(&all_on()));
    }

    #[test]
    fn glass_title_bars_kept() {
        let old = "(app_id contains \"sonata2\") & !(app_id is \"sonata2-mission\") | type is \"toplevel\"";
        assert!(refreshed_blur_rule(old, false, &all_on()).unwrap().ends_with(GLASS_BARS));
    }

    #[test]
    fn up_to_date_left_alone() {
        assert_eq!(refreshed_blur_rule(&blur_rule(&all_on()), false, &all_on()), None);
        assert_eq!(refreshed_blur_rule(&(blur_rule(&all_on()) + GLASS_BARS), false, &all_on()), None);
    }

    #[test]
    fn not_while_a_game_has_lighter_effects_nor_a_foreign_rule() {
        assert_eq!(refreshed_blur_rule("(app_id contains \"sonata2\") & !(x)", true, &all_on()), None);
        assert_eq!(refreshed_blur_rule("app_id is \"sonata2-no-blur\"", false, &all_on()), None);
        assert_eq!(refreshed_blur_rule("type is \"toplevel\"", false, &all_on()), None);
    }
}
