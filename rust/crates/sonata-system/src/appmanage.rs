//! Settings > Apps: one app's size, its data, and what it may use
//! (backend/appmanage.py).
//!
//! Flatpak apps (sandboxed): the portals' own permission store (permstore:
//! camera, location, background, notifications) and Flatpak's overrides
//! (network, microphone, home folder) -- the ones `flatpak override --user`
//! writes. Apps from the distro's packages run unsandboxed: Sonata keeps
//! from them what it can when it opens them (appperms.json's limits,
//! enforced by sandbox::restricted_command), and GeoClue's own per-app
//! setting for the location.

use crate::uninstall::Owner;
use gio::prelude::*;
use serde_json::{json, Value};
use sonata_i18n::{gettext, n_};
use std::path::{Path, PathBuf};

/// How a permission is kept: the portals' store (asked when used), or a
/// Flatpak override (context key, value).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum How {
    Store,
    Override(&'static str, &'static str),
}

pub const PERMISSIONS: [(&str, &str, How); 7] = [
    ("camera", n_!("Camera"), How::Store),
    // (one socket for both: no microphone means no sound either -- said so)
    ("microphone", n_!("Sound & Microphone"), How::Override("sockets", "pulseaudio")),
    ("network", n_!("Network"), How::Override("shared", "network")),
    ("location", n_!("Location"), How::Store),
    ("background", n_!("Running in the Background"), How::Store),
    ("notifications", n_!("Notifications"), How::Store),
    ("files", n_!("Home Folder"), How::Override("filesystems", "home")),
];
/// either gives the whole home folder
pub const HOME_ALIASES: [&str; 2] = ["home", "host"];
/// Apps from packages: what Sonata can keep from them when it opens them
pub const NATIVE: [(&str, &str); 4] = [("camera", n_!("Camera")), ("microphone", n_!("Sound & Microphone")), ("network", n_!("Network")), ("files", n_!("Home Folder"))];

fn run(cmd: &[&str]) -> String {
    let (rc, out) = crate::run_c(cmd, 20);
    if rc == 0 { out } else { String::new() }
}

// -- appperms.py's limits (what a packaged app may not use) ------------------------------------
pub const APPPERMS: &str = "appperms";

fn did(app_id: &str) -> &str {
    app_id.strip_suffix(".desktop").unwrap_or(app_id)
}

/// What a packaged app may not use: camera, microphone, network, files.
pub fn limits(app_id: &str) -> Vec<String> {
    let data = sonata_config::load(APPPERMS, &json!({"ask": true, "reviewed": null, "limits": {}}));
    data.get("limits").and_then(|l| l.get(did(app_id))).and_then(Value::as_array).map(|a| a.iter().filter_map(|k| k.as_str().map(String::from)).collect()).unwrap_or_default()
}

pub fn set_limit(app_id: &str, key: &str, refused: bool) {
    let d = did(app_id);
    let data = sonata_config::load(APPPERMS, &json!({"ask": true, "reviewed": null, "limits": {}}));
    let mut all = data.get("limits").and_then(Value::as_object).cloned().unwrap_or_default();
    let mut keep: Vec<String> = limits(d).into_iter().filter(|k| k != key).collect();
    if refused {
        keep.push(key.into());
    }
    if keep.is_empty() {
        all.remove(d);
    } else {
        all.insert(d.into(), json!(keep));
    }
    let _ = sonata_config::update(APPPERMS, &json!({"limits": all}));
}

/// [Context] of `flatpak info --show-permissions`: {key: [values]}.
pub fn context(text: &str) -> Vec<(String, Vec<String>)> {
    let kf = glib::KeyFile::new();
    if kf.load_from_data(text, glib::KeyFileFlags::NONE).is_err() {
        return vec![];
    }
    let Ok(keys) = kf.keys("Context") else { return vec![] };
    keys.iter().map(|k| (k.to_string(), kf.string("Context", k).map(|v| v.split(';').filter(|x| !x.is_empty()).map(String::from).collect()).unwrap_or_default())).collect()
}

fn has(ctx: &[(String, Vec<String>)], key: &str, value: &str) -> bool {
    let vals = ctx.iter().find(|(k, _)| k == key).map(|(_, v)| v.as_slice()).unwrap_or(&[]);
    if key == "filesystems" {
        return vals.iter().any(|v| HOME_ALIASES.contains(&v.split(':').next().unwrap_or("")));
    }
    vals.iter().any(|v| v == value)
}

/// [(key, title, on)]: a Flatpak app's; a packaged app's (given its id) the
/// ones Sonata enforces when it opens it; [] for Sonata's own.
pub fn permissions(o: Option<&Owner>, app_id: Option<&str>) -> Vec<(&'static str, String, bool)> {
    permissions_with(o, app_id, &|cmd| run(cmd), &crate::permstore::lookup, crate::location::available())
}

pub fn permissions_with(o: Option<&Owner>, app_id: Option<&str>, run: &dyn Fn(&[&str]) -> String, lookup: &dyn Fn(&str, &str) -> std::collections::BTreeMap<String, Vec<String>>, geoclue: bool) -> Vec<(&'static str, String, bool)> {
    let Some(o) = o else { return vec![] };
    if o.kind != "flatpak" {
        let Some(id) = app_id else { return vec![] };
        let refused = limits(id);
        let mut out: Vec<(&'static str, String, bool)> = NATIVE.iter().map(|(k, t)| (*k, gettext(t), !refused.iter().any(|r| r == k))).collect();
        if geoclue {
            // GeoClue's own per-app setting (location.rs)
            out.push(("location", gettext("Location"), crate::location::allowed(id)));
        }
        return out;
    }
    let ctx = context(&run(&["flatpak", "info", "--show-permissions", &o.name]));
    PERMISSIONS
        .iter()
        .map(|(key, title, how)| {
            let on = match how {
                How::Store => {
                    let (_, _, table, ident, yes) = crate::permstore::KINDS.iter().find(|k| k.0 == *key).copied().unwrap();
                    // never asked yet: it asks first -- shown on (allowed until refused)
                    lookup(table, ident).get(&o.name).map(|p| crate::permstore::allowed(p, yes)).unwrap_or(true)
                }
                How::Override(ctx_key, value) => has(&ctx, ctx_key, value),
            };
            (*key, gettext(title), on)
        })
        .collect()
}

pub fn override_args(key: &str, on: bool) -> Vec<String> {
    let Some(How::Override(ctx, value)) = PERMISSIONS.iter().find(|p| p.0 == key).map(|p| p.2) else { return vec![] };
    if ctx == "filesystems" && !on {
        return HOME_ALIASES.iter().map(|v| format!("--nofilesystem={v}")).collect();
    }
    let flag = match (ctx, on) {
        ("sockets", true) => "socket",
        ("shared", true) => "share",
        ("filesystems", true) => "filesystem",
        ("sockets", false) => "nosocket",
        ("shared", false) => "unshare",
        _ => "nofilesystem",
    };
    vec![format!("--{flag}={value}")]
}

/// Blocking (the location's pkexec, flatpak override).
pub fn set_permission(o: Option<&Owner>, key: &str, on: bool, app_id: Option<&str>) -> bool {
    let Some(o) = o else { return false };
    if o.kind != "flatpak" {
        let Some(id) = app_id else { return false };
        if key == "location" {
            return crate::location::set_app(id, on);
        }
        set_limit(id, key, !on);
        return true;
    }
    let Some(how) = PERMISSIONS.iter().find(|p| p.0 == key).map(|p| p.2) else { return false };
    if how == How::Store {
        return crate::permstore::set_allowed(key, &o.name, on);
    }
    let mut cmd = vec!["flatpak".to_string(), "override".into(), "--user".into()];
    cmd.extend(override_args(key, on));
    cmd.push(o.name.clone());
    let argv: Vec<&str> = cmd.iter().map(String::as_str).collect();
    sonata_backend::run::ok(&argv, 20)
}

/// "312.4 MB" / "1,2 GiB" / "12345" (bytes) -> bytes.
pub fn human_size(text: &str) -> u64 {
    let t = text.replace(',', ".").replace('\u{a0}', " ");
    let parts: Vec<&str> = t.split_whitespace().collect();
    let Some(n) = parts.first().and_then(|p| p.parse::<f64>().ok()) else { return 0 };
    let unit = parts.get(1).copied().unwrap_or("B").to_lowercase();
    let unit = unit.trim_end_matches('b').trim_end_matches('i');
    match "_kmgt".find(unit).filter(|_| ["k", "m", "g", "t"].contains(&unit)) {
        Some(i) => (n * 1024f64.powi(i as i32)) as u64,
        None => n as u64,
    }
}

/// The installed app's size in bytes (None: unknown). Blocking.
pub fn app_size(o: Option<&Owner>) -> Option<u64> {
    app_size_with(o, &|cmd| run(cmd))
}

pub fn app_size_with(o: Option<&Owner>, run: &dyn Fn(&[&str]) -> String) -> Option<u64> {
    let o = o?;
    let field = |out: String, label: &str| out.lines().find(|l| l.trim_start().starts_with(label)).and_then(|l| l.split_once(':')).map(|(_, v)| human_size(v));
    if o.kind == "flatpak" {
        return field(run(&["flatpak", "info", &o.name]), "Installed:");
    }
    let digits = |s: String| s.trim().parse::<u64>().ok();
    match o.manager.as_str() {
        "pacman" => field(run(&["pacman", "-Qi", &o.name]), "Installed Size"),
        "apt" => digits(run(&["dpkg-query", "-W", "-f", "${Installed-Size}", &o.name])).map(|k| k * 1024),
        "dnf" | "zypper" => digits(run(&["rpm", "-q", "--qf", "%{SIZE}", &o.name])),
        _ => None,
    }
}

/// What an app's folders are usually called: its id and its last part, its
/// command's name (org.gnome.Calculator, calculator, gnome-calculator).
pub fn names(app_id: &str, executable: &str, o: Option<&Owner>) -> Vec<String> {
    let d = did(app_id);
    let mut names = vec![d.to_string(), d.rsplit('.').next().unwrap_or("").to_string(), Path::new(executable).file_name().map(|f| f.to_string_lossy().into_owned()).unwrap_or_default()];
    if let Some(o) = o.filter(|o| o.kind == "package") {
        names.push(o.name.clone());
    }
    let mut out: Vec<String> = names.into_iter().filter(|n| n.len() >= 3 && !["env", "sh", "bash", "flatpak", "python3", "python", "sonata2"].contains(&n.as_str())).flat_map(|n| [n.to_lowercase(), n]).collect();
    out.sort();
    out.dedup();
    out
}

/// The app's own folders: Flatpak's ~/.var/app/<id>, else the ones named
/// like it in ~/.config, ~/.local/share, ~/.local/state, ~/.cache. Sonata's
/// own apps: none (their settings are Sonata's); a web app: its login and
/// storage.
pub fn data_dirs(info: &gio::AppInfo, o: Option<&Owner>) -> Vec<PathBuf> {
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    let exe = info.executable().to_string_lossy().into_owned();
    let home = glib::home_dir();
    let state = std::env::var_os("XDG_STATE_HOME").filter(|s| !s.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".local/state"));
    let bases = [glib::user_config_dir(), glib::user_data_dir(), glib::user_cache_dir(), state];
    data_dirs_in(&id, &exe, o, &home, &bases)
}

pub fn data_dirs_in(app_id: &str, executable: &str, o: Option<&Owner>, home: &Path, bases: &[PathBuf]) -> Vec<PathBuf> {
    let d = did(app_id);
    if let Some(wid) = d.strip_prefix("sonata2-webapp-") {
        let p = sonata_config::data_dir("webapps").join(wid);
        return if p.is_dir() { vec![p] } else { vec![] };
    }
    if crate::uninstall::PROTECTED.iter().any(|p| app_id.starts_with(p)) {
        return vec![];
    }
    if let Some(o) = o.filter(|o| o.kind == "flatpak") {
        let p = home.join(".var/app").join(&o.name);
        return if p.is_dir() { vec![p] } else { vec![] };
    }
    let mut out: Vec<PathBuf> = Vec::new();
    for base in bases {
        for n in names(app_id, executable, o) {
            let p = base.join(n);
            if p.is_dir() && !p.is_symlink() && !out.contains(&p) {
                out.push(p);
            }
        }
    }
    out
}

/// Bytes in these folders (links not followed). Blocking.
pub fn folder_size(paths: &[PathBuf]) -> u64 {
    fn walk(p: &Path) -> u64 {
        let Ok(dir) = std::fs::read_dir(p) else { return 0 };
        dir.flatten()
            .map(|e| match e.file_type() {
                Ok(t) if t.is_dir() => walk(&e.path()),
                _ => e.path().symlink_metadata().map(|m| m.len()).unwrap_or(0),
            })
            .sum()
    }
    paths.iter().map(|p| walk(p)).sum()
}

/// Into the Trash (put back from there); returns the ones that failed.
pub fn clear_data(paths: &[PathBuf]) -> Vec<PathBuf> {
    paths.iter().filter(|p| gio::File::for_path(p).trash(gio::Cancellable::NONE).is_err()).cloned().collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    const SHOW: &str = "[Context]\nshared=ipc;network;\nsockets=x11;wayland;\nfilesystems=xdg-download;home:ro;\n";

    fn flatpak() -> Owner {
        Owner::new("flatpak", "org.test.App", "flatpak-user")
    }

    #[test]
    fn flatpak_permissions() {
        let lookup = |_t: &str, ident: &str| -> BTreeMap<String, Vec<String>> { if ident == "camera" { BTreeMap::from([("org.test.App".to_string(), vec!["no".to_string()])]) } else { BTreeMap::new() } };
        let got: Vec<(&str, bool)> = permissions_with(Some(&flatpak()), None, &|_| SHOW.to_string(), &lookup, false).into_iter().map(|(k, _, on)| (k, on)).collect();
        assert_eq!(got, [("camera", false), ("microphone", false), ("network", true), ("location", true), ("background", true), ("notifications", true), ("files", true)]);
        assert!(permissions(None, None).is_empty());
        assert!(!set_permission(None, "network", false, None));
    }

    #[test]
    fn override_args_and_sizes() {
        assert_eq!(override_args("network", false), ["--unshare=network"]);
        assert_eq!(override_args("microphone", true), ["--socket=pulseaudio"]);
        assert_eq!(override_args("files", false), ["--nofilesystem=home", "--nofilesystem=host"]);
        assert_eq!(human_size("312.4 MB"), (312.4 * 1024f64.powi(2)) as u64);
        assert_eq!(human_size("1,5 GiB"), (1.5 * 1024f64.powi(3)) as u64);
        assert_eq!(human_size("12 KiB"), 12 * 1024);
        assert_eq!(human_size(""), 0);
        let pkg = Owner::new("package", "testapp", "pacman");
        assert_eq!(app_size_with(Some(&pkg), &|_| "Name : testapp\nInstalled Size  : 2.00 MiB\n".into()), Some(2 * 1024 * 1024));
    }

    /// A packaged app gets the switches Sonata enforces, and the location's
    /// when GeoClue is there.
    #[test]
    fn packaged_app_permissions() {
        let _l = crate::test_lock();
        let _ = sonata_config::save(APPPERMS, &Default::default());
        let pkg = Owner::new("package", "gnome-maps", "pacman");
        let none = |_: &str, _: &str| BTreeMap::new();
        set_limit("org.gnome.Maps.desktop", "network", true);
        let got = permissions_with(Some(&pkg), Some("org.gnome.Maps.desktop"), &|_| String::new(), &none, true);
        assert!(got.iter().any(|(k, _, _)| *k == "location"));
        assert!(got.iter().any(|(k, _, on)| *k == "network" && !on));
        assert!(!permissions_with(Some(&pkg), Some("org.gnome.Maps.desktop"), &|_| String::new(), &none, false).iter().any(|(k, _, _)| *k == "location"));
        set_limit("org.gnome.Maps", "network", false);
        assert!(limits("org.gnome.Maps.desktop").is_empty());
    }

    #[test]
    fn data_dirs_and_size() {
        let home = tempfile::tempdir().unwrap();
        let (cfg, cache) = (home.path().join(".config"), home.path().join(".cache"));
        for d in [cfg.join("testapp"), cache.join("org.test.App"), cfg.join("other")] {
            std::fs::create_dir_all(d).unwrap();
        }
        std::fs::write(cfg.join("testapp/a"), "x".repeat(10)).unwrap();
        let pkg = Owner::new("package", "testapp", "pacman");
        let dirs = data_dirs_in("org.test.App.desktop", "/usr/bin/testapp", Some(&pkg), home.path(), &[cfg.clone(), cache.clone()]);
        assert_eq!(dirs, [cfg.join("testapp"), cache.join("org.test.App")]);
        assert_eq!(folder_size(&dirs), 10);
        assert!(data_dirs_in("io.github.vinioliveiras.sonata2.files.desktop", "", None, home.path(), &[cfg]).is_empty());
    }
}
