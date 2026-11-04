//! Installed apps (sonata2/apps.py), the parts Files, Launchpad and the
//! Dock share: the applications folders, a scan that reads them directly
//! (GIO's own list only notices folders it watched since the process
//! started), lookup by desktop id.

use gtk::gio;
use gtk::glib;
use std::collections::BTreeMap;
use std::path::PathBuf;

/// Every applications folder, highest priority first: the user's, the
/// XDG_DATA_DIRS ones, and Flatpak's exports even when the session's
/// XDG_DATA_DIRS lacks them (a login screen doesn't run /etc/profile.d).
pub fn app_dirs() -> Vec<PathBuf> {
    let mut data = vec![glib::user_data_dir()];
    data.extend(glib::system_data_dirs());
    data.push(glib::user_data_dir().join("flatpak/exports/share"));
    data.push(PathBuf::from("/var/lib/flatpak/exports/share"));
    let mut out: Vec<PathBuf> = Vec::new();
    for d in data {
        let p = d.join("applications");
        if !out.contains(&p) {
            out.push(p);
        }
    }
    out
}

fn walk(dir: &std::path::Path, base: &std::path::Path, out: &mut BTreeMap<String, Option<gio::DesktopAppInfo>>) {
    let Ok(rd) = std::fs::read_dir(dir) else { return };
    let mut entries: Vec<std::fs::DirEntry> = rd.flatten().collect();
    entries.sort_by_key(|e| e.file_name());
    for e in entries {
        let p = e.path();
        if p.is_dir() {
            walk(&p, base, out);
            continue;
        }
        let name = e.file_name().to_string_lossy().into_owned();
        if !name.ends_with(".desktop") {
            continue;
        }
        let did = p.strip_prefix(base).map(|r| r.to_string_lossy().replace('/', "-")).unwrap_or(name);
        if out.contains_key(&did) {
            continue; // a higher-priority folder has it
        }
        // Hidden=true: the entry is removed (a user's copy hiding a system
        // one, as GIO reads it) -- kept as None, so the system copy stays
        // hidden too
        let info = gio::DesktopAppInfo::from_filename(&p).filter(|i| !i.is_hidden());
        out.insert(did, info);
    }
}

/// desktop id -> DesktopAppInfo, read from the folders themselves.
pub fn scan() -> BTreeMap<String, gio::DesktopAppInfo> {
    let mut out = BTreeMap::new();
    for d in app_dirs() {
        walk(&d, &d, &mut out);
    }
    out.into_iter().filter_map(|(k, v)| v.map(|v| (k, v))).collect()
}

/// The DesktopAppInfo for 'firefox' or 'firefox.desktop'.
pub fn lookup(desktop_id: &str) -> Option<gio::DesktopAppInfo> {
    let id = if desktop_id.ends_with(".desktop") { desktop_id.to_string() } else { format!("{desktop_id}.desktop") };
    gio::DesktopAppInfo::new(&id).or_else(|| scan().remove(&id)) // installed after GIO's first look
}

/// The .desktop file behind an app.
pub fn app_filename(info: &gio::DesktopAppInfo) -> Option<PathBuf> {
    info.filename()
}
