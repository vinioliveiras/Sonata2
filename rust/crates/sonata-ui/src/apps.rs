//! Installed apps (sonata2/apps.py), the parts Files, Launchpad and the
//! Dock share: the applications folders, a scan that reads them directly
//! (GIO's own list only notices folders it watched since the process
//! started), lookup by desktop id.

use gtk::gio;
use gtk::glib;
use gtk::prelude::*;
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

/// The program an app's Exec starts (empty without one), as
/// g_app_info_get_executable gives it. gio's AppInfo::executable() reads
/// that as a path and crashes on the NULL of an entry with no Exec= (a
/// D-Bus activated one): Task Manager closed itself as it opened, and the
/// Dock and Launchpad went down the same way (Vini).
pub fn executable(info: &impl IsA<gio::AppInfo>) -> PathBuf {
    let cmd = info.as_ref().commandline().map(|c| c.to_string_lossy().into_owned()).unwrap_or_default();
    let first = glib::shell_parse_argv(cmd.as_str()).ok().and_then(|a| a.into_iter().next()).map(|a| a.to_string_lossy().into_owned()).unwrap_or_default();
    PathBuf::from(first)
}

#[cfg(test)]
mod exec_tests {
    use super::*;

    /// Vini: Task Manager didn't open in the Rust session -- an app entry
    /// without Exec= crashed it (AppInfo::executable() on its NULL).
    #[test]
    fn test_task_manager_did_not_open() {
        let entry = |text: &str| {
            let kf = glib::KeyFile::new();
            kf.load_from_data(text, glib::KeyFileFlags::NONE).unwrap();
            gio::DesktopAppInfo::from_keyfile(&kf).expect("an entry")
        };
        let info = entry("[Desktop Entry]\nType=Application\nName=D-Bus only\nDBusActivatable=true\n");
        assert_eq!(executable(&info), PathBuf::new());
        let info = entry("[Desktop Entry]\nType=Application\nName=Files\nExec=\"/bin/sh\" -c true %U\n");
        assert_eq!(executable(&info), PathBuf::from("/bin/sh"));
    }
}
