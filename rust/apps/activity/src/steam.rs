//! Steam games by their app id (steamgames.py): a game's processes carry
//! SteamAppId; its name comes from its appmanifest in a Steam library and
//! its icon from Steam's own copy (sonata_ui::icons::steam_icon_path).

use std::cell::RefCell;
use std::collections::HashMap;
use std::path::PathBuf;

const STEAM_DIRS: [&str; 3] = [".local/share/Steam", ".steam/steam", ".var/app/com.valvesoftware.Steam/.local/share/Steam"];
/// a game not found yet (installing, library mounted later) is looked up again
const UNKNOWN_RETRY_S: f64 = 60.0;

thread_local! {
    static NAMES: RefCell<HashMap<String, (Option<String>, f64)>> = RefCell::new(HashMap::new());
}

/// "steam_app_3219630" -> "3219630" (None for other app ids).
pub fn appid(key: &str) -> Option<String> {
    key.strip_prefix("steam_app_").filter(|d| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit())).map(str::to_string)
}

fn roots() -> Vec<PathBuf> {
    let home = glib::home_dir();
    let mut out: Vec<PathBuf> = Vec::new();
    for d in STEAM_DIRS {
        if let Ok(p) = std::fs::canonicalize(home.join(d)) {
            if p.is_dir() && !out.contains(&p) {
                out.push(p);
            }
        }
    }
    out
}

/// steamapps folders of every Steam library (libraryfolders.vdf).
pub fn libraries() -> Vec<PathBuf> {
    let re = regex_lite_paths;
    let mut libs: Vec<PathBuf> = Vec::new();
    let mut add = |p: PathBuf| {
        if p.is_dir() && !libs.contains(&p) {
            libs.push(p);
        }
    };
    for root in roots() {
        for vdf in [root.join("steamapps/libraryfolders.vdf"), root.join("config/libraryfolders.vdf")] {
            let Ok(text) = std::fs::read_to_string(&vdf) else { continue };
            add(root.join("steamapps"));
            for p in re(&text) {
                add(PathBuf::from(p.replace("\\\\", "\\")).join("steamapps"));
            }
        }
        add(root.join("steamapps"));
    }
    libs
}

/// The values of `"path" "..."` lines.
fn regex_lite_paths(text: &str) -> Vec<String> {
    quoted_values(text, "path")
}

/// The values of `"key"  "value"` pairs.
fn quoted_values(text: &str, key: &str) -> Vec<String> {
    let needle = format!("\"{key}\"");
    let mut out = Vec::new();
    let mut rest = text;
    while let Some(i) = rest.find(&needle) {
        rest = &rest[i + needle.len()..];
        let t = rest.trim_start();
        if t.len() < rest.len() && t.starts_with('"') {
            if let Some(end) = t[1..].find('"') {
                out.push(t[1..1 + end].to_string());
            }
        }
    }
    out
}

fn now() -> f64 {
    sonata_backend::procfs::monotonic()
}

/// The game's name from its appmanifest.
pub fn name(aid: &str) -> Option<String> {
    if let Some((n, t)) = NAMES.with(|m| m.borrow().get(aid).cloned()) {
        if n.is_some() || now() - t < UNKNOWN_RETRY_S {
            return n;
        }
    }
    let found = libraries().iter().find_map(|lib| std::fs::read_to_string(lib.join(format!("appmanifest_{aid}.acf"))).ok().and_then(|t| quoted_values(&t, "name").into_iter().next()));
    NAMES.with(|m| m.borrow_mut().insert(aid.to_string(), (found.clone(), now())));
    found
}

/// (name, icon) of a Steam game's process; None for other app ids. Not in
/// a library: the fallback name and Steam's icon.
pub fn shown(key: &str, fallback: &str) -> Option<(String, gio::Icon)> {
    use gio::prelude::Cast;
    let aid = appid(key)?;
    let icon: gio::Icon = match sonata_ui::icons::steam_icon_path(&aid) {
        Some(p) => gio::FileIcon::new(&gio::File::for_path(p)).upcast(),
        None => gio::ThemedIcon::new("steam").upcast(),
    };
    let n = name(&aid).or_else(|| Some(fallback.trim().to_string()).filter(|f| !f.is_empty())).unwrap_or_else(|| key.to_string());
    Some((n, icon))
}

#[cfg(test)]
mod tests {
    #[test]
    fn manifests_read() {
        assert_eq!(super::appid("steam_app_3219630").as_deref(), Some("3219630"));
        assert_eq!(super::appid("firefox"), None);
        let acf = "\"AppState\"\n{\n\t\"appid\"\t\t\"10\"\n\t\"name\"\t\t\"Halloween: The Game\"\n}";
        assert_eq!(super::quoted_values(acf, "name"), ["Halloween: The Game"]);
    }
}
