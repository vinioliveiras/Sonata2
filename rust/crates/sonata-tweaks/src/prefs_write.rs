//! prefs.py's writing half: a desktop setting changed (system.json, then
//! copied to GSettings for the apps that read it directly), the wallpaper
//! set and started again, and `session-env` (every login). The reading
//! half is sonata_config::prefs.
//!
//! system.json is the desktop's own setting (`~/.config/sonata2/system.json`),
//! written under the same `.system.lock` as Python's config.update.

use gio::prelude::*;
use serde_json::{Map, Value};
use sonata_config::prefs::{self, BG, BOOLS, DOUBLES, I, INTS, WM};
use std::os::fd::AsRawFd;
use std::path::PathBuf;

const NAME: &str = "system";
/// quick changes: one fresh wallpaper, for the last one
pub const RELOAD_AFTER_MS: u64 = 400;

fn folder() -> PathBuf {
    sonata_config::config_home().join(sonata_config::NAME)
}

/// Sonata's defaults, with the button layout the user's frame gives.
pub fn defaults() -> Vec<(String, String)> {
    let layout = crate::frame::button_layout(&crate::frame::frame());
    prefs::defaults().into_iter().map(|(k, v)| if k == format!("{WM}/button-layout") { (k, layout.clone()) } else { (k, v) }).collect()
}

/// gsettings' printed form -> the plain value ("'x'" -> x, "uint32 30" -> 30).
pub fn plain(value: &str) -> String {
    let mut v = value.trim();
    for prefix in ["uint32 ", "int32 ", "@as "] {
        if let Some(rest) = v.strip_prefix(prefix) {
            v = rest;
        }
    }
    v.trim_matches('\'').to_string()
}

/// The value GNOME's database had (first run: nothing chosen is lost).
fn from_gsettings(schema: &str, key: &str) -> Option<String> {
    if cfg!(test) {
        return None; // never the real session's settings
    }
    let (rc, out) = sonata_backend::run::run(&["gsettings", "get", schema, key], 3);
    (rc == 0).then(|| plain(&out))
}

/// prefs.get: stored, else Sonata's default, else GNOME's old value, else `default`.
pub fn get_in(schema: &str, key: &str, default: Option<&str>, data: &Map<String, Value>) -> Option<String> {
    let k = format!("{schema}/{key}");
    if let Some(v) = data.get(&k) {
        return Some(match v {
            Value::String(s) => s.clone(),
            other => sonata_config::to_python_json(other),
        });
    }
    if let Some((_, v)) = defaults().into_iter().find(|(d, _)| *d == k) {
        return Some(v);
    }
    from_gsettings(schema, key).or_else(|| default.map(String::from))
}

pub fn get(schema: &str, key: &str) -> String {
    get_in(schema, key, None, &prefs::load()).unwrap_or_default()
}

fn update(values: &[(String, String)]) -> std::io::Result<()> {
    let dir = folder();
    std::fs::create_dir_all(&dir)?;
    let lock = std::fs::OpenOptions::new().write(true).create(true).truncate(false).open(dir.join(format!(".{NAME}.lock")))?;
    unsafe {
        libc::flock(lock.as_raw_fd(), libc::LOCK_EX);
    }
    let path = prefs::path();
    let mut data = prefs::load();
    for (k, v) in values {
        data.insert(k.clone(), Value::String(v.clone()));
    }
    sonata_config::atomic_write(&path, sonata_config::to_python_json(&Value::Object(data)).as_bytes(), false)
}

/// What `gsettings set` is given for a value.
pub fn gvariant_text(key: &str, value: &str) -> String {
    if key == "old-files-age" {
        format!("uint32 {value}")
    } else if BOOLS.contains(&key) || INTS.contains(&key) || DOUBLES.contains(&key) {
        value.to_string()
    } else {
        format!("'{}'", value.replace('\'', "\\'"))
    }
}

/// Copy to GSettings when its schemas exist (apps reading them directly).
fn mirror(schema: &str, key: &str, value: &str) {
    if cfg!(test) {
        return;
    }
    sonata_backend::run::spawn(&["gsettings", "set", schema, key, &gvariant_text(key, value)]);
}

/// prefs.set: stored, and mirrored to GSettings.
pub fn set(schema: &str, key: &str, value: &str) -> bool {
    let value = plain(value);
    let _ = update(&[(format!("{schema}/{key}"), value.clone())]);
    mirror(schema, key, &value);
    true
}

/// A picture that's there and can be shown.
pub fn usable_picture(uri: &str) -> bool {
    sonata_shell::wallpaper::usable_picture(uri)
}

/// The desktop picture for Light / Dark: the chosen one, else Sonata's
/// "Mountains" (Vini: a friend's first login showed the bare gradient --
/// the picture taken over from his old desktop couldn't be shown).
pub fn wallpaper_uri(dark: bool) -> String {
    let mut uri = get(BG, if dark { "picture-uri-dark" } else { "picture-uri" });
    if uri.is_empty() {
        uri = get(BG, "picture-uri");
    }
    if usable_picture(&uri) {
        uri
    } else {
        let (l, d) = crate::wallpapers::default_uris();
        if dark { d } else { l }
    }
}

/// The wallpaper as a picture (a video's poster: call it off the UI thread
/// the first time), Sonata's default when the video has no frame to give.
pub fn wallpaper_still_uri(dark: bool) -> String {
    let uri = wallpaper_uri(dark);
    let Some(path) = gio::File::for_uri(&uri).path() else { return uri };
    if !crate::videowall::is_video(&path.display().to_string()) {
        return uri;
    }
    match crate::videowall::poster(&path) {
        Some(still) => format!("file://{}", still.display()),
        None => {
            let (l, d) = crate::wallpapers::default_uris();
            if dark { d } else { l }
        }
    }
}

/// Finder's "Set Desktop Picture" (and Settings): the picture (or video)
/// for Light and Dark (`dark`: another one for Dark). The desktop's
/// wallpaper is then started again, fresh (Vini: after a video wallpaper,
/// a second change didn't show until Sonata restarted).
pub fn set_wallpaper(uri: &str, dark: Option<&str>) {
    let dark = dark.filter(|d| !d.is_empty()).unwrap_or(uri);
    if get(BG, "picture-uri") == uri && get(BG, "picture-uri-dark") == dark {
        return;
    }
    set(BG, "picture-uri", uri);
    set(BG, "picture-uri-dark", dark);
    reload_wallpaper();
}

thread_local! {
    static RELOAD: std::cell::RefCell<Option<glib::SourceId>> = const { std::cell::RefCell::new(None) };
}

/// The desktop's wallpaper started again (`sonata2 restart wallpaper`: the
/// new one comes up under the old one, no black flash), once changes
/// settle. Without a main loop (a script): at once.
pub fn reload_wallpaper() {
    let looping = glib::MainContext::default().is_owner() && gio::Application::default().is_some();
    if !looping {
        sonata_backend::system::restart_sonata(&["wallpaper"]);
        return;
    }
    RELOAD.with(|r| {
        if let Some(src) = r.borrow_mut().take() {
            sonata_ui::cancel(src);
        }
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis(RELOAD_AFTER_MS), || {
            RELOAD.with(|r| r.borrow_mut().take());
            sonata_backend::system::restart_sonata(&["wallpaper"]);
        });
        *r.borrow_mut() = Some(id);
    });
}

pub fn is_picture(content_type: &str) -> bool {
    content_type.starts_with("image/") && !content_type.contains("svg") && !content_type.contains("icon")
}

/// A video a wallpaper can be made of.
pub fn is_video(content_type: &str) -> bool {
    crate::videowall::is_video(content_type)
}

fn fonts() -> Vec<String> {
    let (rc, out) = sonata_backend::run::run(&["fc-list", ":", "family"], 10);
    if rc != 0 {
        return Vec::new();
    }
    out.lines().flat_map(|l| l.split(',')).map(|f| f.trim().to_string()).collect()
}

/// The settings to change at login for the fonts installed: [(schema, key, value)].
pub fn font_changes(fams: &[String], data: &Map<String, Value>) -> Vec<(&'static str, &'static str, String)> {
    let g = |s: &str, k: &str| get_in(s, k, None, data).unwrap_or_default();
    let mut out = Vec::new();
    // UI font: SF Pro when installed (its licence forbids shipping it), else the bundled Inter;
    // only replaces Sonata's own defaults, never a font the user picked (macOS: 13 px = 10 pt)
    let ui = ["SF Pro Text", "SF Pro", "Inter Variable", "Inter"].into_iter().find(|f| fams.iter().any(|x| x == f));
    let ours = ["SF Pro Text 10", "SF Pro 10", "Inter Variable 10", "Inter 10", "Cantarell 11", "Adwaita Sans 11", ""];
    let font = g(I, "font-name");
    if let Some(ui) = ui {
        if ours.contains(&font.as_str()) && font != format!("{ui} 10") {
            out.push((I, "font-name", format!("{ui} 10")));
            out.push((I, "document-font-name", format!("{ui} 10")));
            out.push((WM, "titlebar-font", format!("{ui} Bold 10")));
        }
    }
    let mono = if fams.iter().any(|f| f == "SF Mono") { "SF Mono 10" } else { "Monospace 10" };
    let cur = g(I, "monospace-font-name");
    if ["Monospace 10", "Source Code Pro 10", "Adwaita Mono 11", "SF Mono 10", ""].contains(&cur.as_str()) && cur != mono {
        out.push((I, "monospace-font-name", mono.to_string()));
    }
    out
}

/// What the session's environment gets from the settings (the last part of session_env).
pub fn env_text(data: &Map<String, Value>) -> String {
    let g = |s: &str, k: &str| get_in(s, k, None, data).unwrap_or_default();
    // other apps take the GTK theme from the settings (portal / Sonata's dconf
    // layer) -- never from a GTK_THEME variable, which breaks libadwaita apps
    let mut out = String::from("unset GTK_THEME\n");
    // the pointer picked in Settings (cursors.rs) for the apps that only read
    // these at start (Qt, X11, Electron, SDL games...)
    let theme = Some(g(I, "cursor-theme")).filter(|t| !t.is_empty()).unwrap_or_else(|| "Sonata-Cursors".into());
    let px = g(I, "cursor-size").trim().parse::<i64>().unwrap_or(24);
    out += &format!("export XCURSOR_THEME={} XCURSOR_SIZE={px}\n", crate::util::shell_quote(&theme));
    out
}

/// `sonata2 prefs session-env` (tools/session-env.sh, every login): fills
/// in Sonata's defaults (first run), picks the UI font, and returns the
/// environment the session needs.
pub fn session_env() -> String {
    let data = prefs::load();
    if data.is_empty() {
        // keep what GNOME had for the few keys people change
        let mut seed = Vec::new();
        for (schema, key) in [(BG, "picture-uri"), (BG, "picture-uri-dark"), (I, "color-scheme")] {
            if let Some(old) = from_gsettings(schema, key).filter(|o| !o.is_empty()) {
                if key == "color-scheme" || usable_picture(&old) {
                    seed.push((format!("{schema}/{key}"), old));
                }
            }
        }
        let _ = update(&seed);
        // apps that read GSettings see the same look
        let mut all = defaults();
        for (k, v) in &seed {
            match all.iter_mut().find(|(d, _)| d == k) {
                Some(slot) => slot.1 = v.clone(),
                None => all.push((k.clone(), v.clone())),
            }
        }
        for (k, v) in all {
            if let Some((schema, key)) = k.rsplit_once('/') {
                if !v.is_empty() {
                    mirror(schema, key, &v);
                }
            }
        }
    }
    for (schema, key, value) in font_changes(&fonts(), &prefs::load()) {
        set(schema, key, &value);
    }
    let theme = if get(I, "color-scheme") == "prefer-dark" { "Sonata-Dark" } else { "Sonata-Light" };
    if get(I, "gtk-theme") != theme {
        set(I, "gtk-theme", theme);
    }
    env_text(&prefs::load())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn plain_and_gvariant_forms() {
        assert_eq!(plain("'Adwaita'"), "Adwaita");
        assert_eq!(plain("uint32 30\n"), "30");
        assert_eq!(gvariant_text("old-files-age", "30"), "uint32 30");
        assert_eq!(gvariant_text("cursor-size", "24"), "24");
        assert_eq!(gvariant_text("font-name", "It's 10"), "'It\\'s 10'");
    }

    #[test]
    fn fonts_only_replace_sonatas_own_defaults() {
        let fams: Vec<String> = ["SF Pro", "Inter", "SF Mono"].iter().map(|s| s.to_string()).collect();
        let ch = font_changes(&fams, &Map::new());
        assert!(ch.contains(&(I, "font-name", "SF Pro 10".into())));
        assert!(ch.contains(&(WM, "titlebar-font", "SF Pro Bold 10".into())));
        assert!(ch.contains(&(I, "monospace-font-name", "SF Mono 10".into())));
        let mine = json!({format!("{I}/font-name"): "Comic Sans 12", format!("{I}/monospace-font-name"): "Hack 10"});
        assert!(font_changes(&fams, mine.as_object().unwrap()).is_empty());
    }

    #[test]
    fn session_env_exports_the_pointer() {
        let d = json!({format!("{I}/cursor-theme"): "Bibata Modern", format!("{I}/cursor-size"): "32"});
        assert_eq!(env_text(d.as_object().unwrap()), "unset GTK_THEME\nexport XCURSOR_THEME='Bibata Modern' XCURSOR_SIZE=32\n");
        assert_eq!(env_text(&Map::new()), "unset GTK_THEME\nexport XCURSOR_THEME=Sonata-Cursors XCURSOR_SIZE=24\n");
    }

    #[test]
    fn pictures_and_videos() {
        assert!(is_picture("image/jpeg") && !is_picture("image/svg+xml") && !is_picture("image/vnd.microsoft.icon"));
        assert!(is_video("video/mp4"));
        assert!(!usable_picture("") && !usable_picture("file:///no/such.jpg"));
    }
}
