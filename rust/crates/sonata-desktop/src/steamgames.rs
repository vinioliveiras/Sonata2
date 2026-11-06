//! Steam games in the Dock (sonata2/steamgames.py): their windows have the
//! app_id "steam_app_<id>" and no .desktop entry (unless the user made a
//! shortcut), so the Dock showed a generic icon named "steam_app_1234".
//! Here: the game's name (its appmanifest in a Steam library) and icon (a
//! shortcut's hicolor icon, else Steam's own copy in
//! appcache/librarycache), and how to start it again.
//!
//! ```text
//! appid("steam_app_3219630")    // Some("3219630")
//! name("3219630")               // Some("Halloween: The Game")
//! icon_path("3219630")          // a square picture, or None
//! launch("3219630")
//! shown("steam_app_3219630", t) // (name, gio::Icon): the Dock and the app switcher
//! ```

use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::{Duration, Instant};

pub const STEAM_DIRS: [&str; 3] = ["~/.local/share/Steam", "~/.steam/steam", "~/.var/app/com.valvesoftware.Steam/.local/share/Steam"];
/// A game not found yet (installing, library mounted later) is looked up again.
pub const UNKNOWN_RETRY: Duration = Duration::from_secs(60);
/// shown() is asked on every window event of the game (the Dock's sync, the
/// app switcher) and each time looked through Steam's icon folders: the icon
/// is looked at again after this (a shortcut made meanwhile: its bigger icon).
pub const ICON_RETRY: Duration = Duration::from_secs(60);

// aid -> (name or None, when looked up)
type Names = HashMap<String, (Option<String>, Instant)>;
static NAMES: Mutex<Option<Names>> = Mutex::new(None);

thread_local! {
    static ICONS: RefCell<HashMap<String, (gio::Icon, Instant)>> = RefCell::new(HashMap::new());
}

/// "3219630" for "steam_app_3219630", None for other app_ids.
pub fn appid(app_id: &str) -> Option<String> {
    let id = app_id.strip_prefix("steam_app_")?;
    (!id.is_empty() && id.chars().all(|c| c.is_ascii_digit())).then(|| id.to_string())
}

fn home() -> PathBuf {
    glib::home_dir()
}

fn expand(d: &str) -> PathBuf {
    match d.strip_prefix("~/") {
        Some(rest) => home().join(rest),
        None => PathBuf::from(d),
    }
}

/// The Steam folders that exist (each once, links followed).
pub fn roots() -> Vec<PathBuf> {
    roots_of(&STEAM_DIRS.map(expand))
}

fn roots_of(dirs: &[PathBuf]) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    for d in dirs {
        let p = std::fs::canonicalize(d).unwrap_or_else(|_| d.clone());
        if p.is_dir() && !out.contains(&p) {
            out.push(p);
        }
    }
    out
}

fn path_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r#""path"\s+"([^"]+)""#).unwrap())
}

fn name_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r#""name"\s+"([^"]+)""#).unwrap())
}

/// steamapps folders of every Steam library (libraryfolders.vdf).
pub fn libraries() -> Vec<PathBuf> {
    libraries_in(&roots())
}

fn libraries_in(roots: &[PathBuf]) -> Vec<PathBuf> {
    let mut libs: Vec<PathBuf> = Vec::new();
    for root in roots {
        for vdf in [root.join("steamapps/libraryfolders.vdf"), root.join("config/libraryfolders.vdf")] {
            let Ok(bytes) = std::fs::read(&vdf) else { continue };
            let text = String::from_utf8_lossy(&bytes);
            let mut paths = vec![root.display().to_string()];
            paths.extend(path_re().captures_iter(&text).map(|c| c[1].to_string()));
            for p in paths {
                let sa = PathBuf::from(p.replace("\\\\", "\\")).join("steamapps");
                if sa.is_dir() && !libs.contains(&sa) {
                    libs.push(sa);
                }
            }
        }
        let sa = root.join("steamapps");
        if sa.is_dir() && !libs.contains(&sa) {
            libs.push(sa);
        }
    }
    libs
}

fn name_in(libs: &[PathBuf], aid: &str) -> Option<String> {
    for lib in libs {
        let Ok(bytes) = std::fs::read(lib.join(format!("appmanifest_{aid}.acf"))) else { continue };
        if let Some(c) = name_re().captures(&String::from_utf8_lossy(&bytes)) {
            return Some(c[1].to_string());
        }
    }
    None
}

/// The game's name from its appmanifest; None when no library has it.
pub fn name(aid: &str) -> Option<String> {
    let mut names = NAMES.lock().unwrap_or_else(|e| e.into_inner());
    let names = names.get_or_insert_with(HashMap::new);
    if let Some((hit, when)) = names.get(aid) {
        if hit.is_some() || when.elapsed() < UNKNOWN_RETRY {
            return hit.clone();
        }
    }
    let found = name_in(&libraries(), aid);
    names.insert(aid.to_string(), (found.clone(), Instant::now()));
    found
}

fn data_home() -> PathBuf {
    std::env::var_os("XDG_DATA_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| home().join(".local/share"))
}

/// The biggest icon Steam keeps for the game: a desktop shortcut's hicolor
/// PNG (up to 256 px), else the library cache's square icon
/// (appcache/librarycache/<id>/<sha1>.jpg, 32 px; older clients:
/// <id>_icon.jpg).
pub fn icon_path(aid: &str) -> Option<PathBuf> {
    icon_path_in(&data_home(), &roots(), aid)
}

fn icon_path_in(data: &Path, roots: &[PathBuf], aid: &str) -> Option<PathBuf> {
    let mut best: Option<(u32, PathBuf)> = None;
    if let Ok(sizes) = std::fs::read_dir(data.join("icons/hicolor")) {
        let mut sizes: Vec<_> = sizes.flatten().collect();
        sizes.sort_by_key(|e| e.file_name());
        for s in sizes {
            let f = s.path().join("apps").join(format!("steam_icon_{aid}.png"));
            if !f.exists() {
                continue;
            }
            // "256x256": its size; any other folder name counts as 0
            let dir = s.file_name().to_string_lossy().into_owned();
            let px = dir.split_once('x').filter(|(w, h)| !w.is_empty() && w.chars().all(|c| c.is_ascii_digit()) && !h.is_empty() && h.chars().all(|c| c.is_ascii_digit())).and_then(|(w, _)| w.parse().ok()).unwrap_or(0);
            if px > best.as_ref().map_or(0, |b| b.0) {
                best = Some((px, f));
            }
        }
    }
    if let Some((_, f)) = best {
        return Some(f);
    }
    for root in roots {
        let cache = root.join("appcache/librarycache");
        if let Ok(rd) = std::fs::read_dir(cache.join(aid)) {
            let mut files: Vec<_> = rd.flatten().map(|e| e.path()).collect();
            files.sort();
            for f in files {
                let n = f.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
                if n.len() == 44 && n.ends_with(".jpg") && n[..40].chars().all(|c| c.is_ascii_digit() || ('a'..='f').contains(&c)) {
                    return Some(f);
                }
            }
        }
        let old = cache.join(format!("{aid}_icon.jpg"));
        if old.exists() {
            return Some(old);
        }
    }
    None
}

/// Start the game through Steam.
pub fn launch(aid: &str) -> bool {
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    let mut cmd = Command::new("steam");
    cmd.arg(format!("steam://rungameid/{aid}")).stdout(Stdio::null()).stderr(Stdio::null());
    // its own session: it outlives the Dock
    unsafe {
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    cmd.spawn().map(sonata_backend::run::reap).is_ok() // (reaped: no zombie in the Dock)
}

/// (name, icon) of a Steam game's window, None for other app_ids -- one
/// answer for the Dock and the app switcher (Vini: Alt+Tab showed
/// "steam_app_3219630" with a generic icon). Not in a library: the window's
/// title (fallback_name) and Steam's icon.
pub fn shown(key: &str, fallback_name: &str) -> Option<(String, gio::Icon)> {
    let aid = appid(key)?;
    let cached = ICONS.with(|c| c.borrow().get(&aid).filter(|(_, when)| when.elapsed() < ICON_RETRY).map(|(i, _)| i.clone()));
    let gicon = cached.unwrap_or_else(|| {
        let made = icon_path(&aid).and_then(|pic| sonata_ui::icons::picture_icon(&pic, "squircle", false, None));
        let gicon = made.unwrap_or_else(|| gio::ThemedIcon::new("steam").into());
        ICONS.with(|c| c.borrow_mut().insert(aid.clone(), (gicon.clone(), Instant::now())));
        gicon
    });
    Some((shown_name(name(&aid), fallback_name, key), gicon))
}

fn shown_name(found: Option<String>, fallback_name: &str, key: &str) -> String {
    found.filter(|n| !n.is_empty()).unwrap_or_else(|| {
        let t = fallback_name.trim();
        if t.is_empty() { key.to_string() } else { t.to_string() }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    struct Lib {
        _tmp: tempfile::TempDir,
        steam: PathBuf,
        data: PathBuf,
        icon: PathBuf,
    }

    fn library() -> Lib {
        let tmp = tempfile::tempdir().unwrap();
        let steam = tmp.path().join("Steam");
        std::fs::create_dir_all(steam.join("steamapps")).unwrap();
        std::fs::write(steam.join("steamapps/libraryfolders.vdf"), format!("\"libraryfolders\"\n{{\n \"0\"\n {{\n  \"path\"  \"{}\"\n }}\n}}\n", steam.display())).unwrap();
        std::fs::write(steam.join("steamapps/appmanifest_3219630.acf"), "\"AppState\"\n{\n \"appid\"  \"3219630\"\n \"name\"  \"Halloween: The Game\"\n}\n").unwrap();
        let cache = steam.join("appcache/librarycache/3219630");
        std::fs::create_dir_all(cache.join("43b33351bd6ed183f94e2322f3701069a5815e90")).unwrap();
        std::fs::write(cache.join("43b33351bd6ed183f94e2322f3701069a5815e90/logo.png"), "").unwrap();
        let icon = cache.join("c324b7ba65f49c1638bbc64005ed34103f3058bd.jpg");
        std::fs::write(&icon, "").unwrap();
        let data = tmp.path().join("data");
        Lib { _tmp: tmp, steam, data, icon }
    }

    #[test]
    fn test_name_and_icon() {
        let lib = library();
        assert_eq!(appid("steam_app_3219630").as_deref(), Some("3219630"));
        assert_eq!(appid("steam"), None);
        assert_eq!(appid("steam_app_default"), None);
        let roots = roots_of(&[lib.steam.clone()]);
        let libs = libraries_in(&roots);
        assert_eq!(libs.len(), 1); // the vdf's own folder once
        assert_eq!(name_in(&libs, "3219630").as_deref(), Some("Halloween: The Game"));
        assert_eq!(name_in(&libs, "999"), None);
        let canon = std::fs::canonicalize(&lib.icon).unwrap();
        assert_eq!(icon_path_in(&lib.data, &roots, "3219630"), Some(canon));
    }

    #[test]
    fn test_shortcut_icon_wins() {
        let lib = library();
        for size in ["32x32", "256x256", "48x48"] {
            let d = lib.data.join("icons/hicolor").join(size).join("apps");
            std::fs::create_dir_all(&d).unwrap();
            std::fs::write(d.join("steam_icon_3219630.png"), "").unwrap();
        }
        let got = icon_path_in(&lib.data, &roots_of(&[lib.steam.clone()]), "3219630").unwrap();
        assert!(got.ends_with("256x256/apps/steam_icon_3219630.png"), "{got:?}");
    }

    /// Vini: Alt+Tab showed "steam_app_3219630" -- a game in no library
    /// is named by its window's title.
    #[test]
    fn test_alt_tab_shows_the_game() {
        assert_eq!(shown_name(Some("Halloween: The Game".into()), "Ravage", "steam_app_3219630"), "Halloween: The Game");
        assert_eq!(shown_name(None, "Ravage  ", "steam_app_999"), "Ravage");
        assert_eq!(shown_name(None, " ", "steam_app_999"), "steam_app_999");
        assert!(shown("firefox", "").is_none());
    }
}
