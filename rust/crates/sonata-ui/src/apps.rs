//! Installed apps (sonata2/apps.py), the parts Files, Launchpad and the
//! Dock share: the applications folders, a scan that reads them directly
//! (GIO's own list only notices folders it watched since the process
//! started), lookup by desktop id.
//!
//! apps.py also changed GIO for the whole process (it patched
//! DesktopAppInfo): names without packaging noise, entries that aren't apps
//! here hidden, and every launch through Sonata's own path (the app lock,
//! a new Flatpak's review, the GPU, its own scope...). Rust can't patch
//! GIO, so those are functions here every place calls instead of GIO's:
//! [`name`] / [`display_name`], [`should_show`], [`launch`] /
//! [`launch_uris`], [`launch_default_for_uri`]. The full launch path needs
//! crates that depend on this one: the binary hands it in once at start
//! ([`set_launcher`]).

use gtk::gio;
use gtk::glib;
use gtk::prelude::*;
use std::collections::BTreeMap;
use std::path::PathBuf;

// -- app names without packaging noise --------------------------------------------------
// "Spotify (Launcher)", "Discord (Flatpak)", "Firefox (Wayland)": macOS shows
// just the app's name.
fn noise() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| {
        regex::Regex::new(
            r"(?i)\s*[\(\[](?:launcher|flatpak|snap|appimage|wayland|x11|xwayland|native|official|unofficial|web ?app|electron|bin|git|stable)[\)\]]\s*$|\s+-\s+(?:launcher|flatpak|snap)$",
        )
        .expect("a valid pattern")
    })
}

/// "Spotify (Launcher)" -> "Spotify" (apps.clean_name); never empty.
pub fn clean_name(name: &str) -> String {
    if name.is_empty() {
        return String::new();
    }
    let out = noise().replace(name, "").trim().to_string();
    if out.is_empty() { name.to_string() } else { out }
}

/// The app's name, cleaned (what apps.py made DesktopAppInfo.get_name give).
pub fn name(info: impl AsRef<gio::AppInfo>) -> String {
    clean_name(&info.as_ref().name())
}

/// The app's display name, cleaned (DesktopAppInfo.get_display_name).
pub fn display_name(info: impl AsRef<gio::AppInfo>) -> String {
    clean_name(&info.as_ref().display_name())
}

/// Entries that aren't apps here: a service's web page Sonata has its own
/// place for (Vini: "Manage Printing" -- CUPS' localhost:631, refused when
/// the service isn't running; printers are in Settings > Printers & Scanners).
pub const NOT_APPS: [&str; 1] = ["cups.desktop"];

/// Shown in Launchpad, Spotlight, Open With... (should_show, as apps.py
/// made it: never one of NOT_APPS).
pub fn should_show(info: impl AsRef<gio::AppInfo>) -> bool {
    let info = info.as_ref();
    info.should_show() && !NOT_APPS.contains(&info.id().as_deref().unwrap_or(""))
}

// -- desktop ids ------------------------------------------------------------------------------
/// The mark a login item carries in place of a desktop id.
const DESKTOP_ID: &str = "sonata-desktop-id";

/// An app made from a file's contents (a login item: autostart reads its
/// keyfile) has no desktop id. Its entry's file name ("steam.desktop")
/// stands in for one, so it gets what its app gets from the Dock -- the
/// GPU, Steam's FPS limit (autostart.py's info.sonata_desktop_id).
pub fn set_desktop_id(info: &impl IsA<gio::AppInfo>, id: &str) {
    // SAFETY: the mark is only ever set and read as a String, here
    unsafe { info.as_ref().set_data(DESKTOP_ID, id.to_string()) };
}

/// The app's desktop id ("steam.desktop"): GIO's, else the one
/// [`set_desktop_id`] gave it; empty without either.
pub fn desktop_id(info: &impl IsA<gio::AppInfo>) -> String {
    let info = info.as_ref();
    if let Some(id) = info.id().filter(|i| !i.is_empty()) {
        return id.to_string();
    }
    // SAFETY: as above, only ever a String
    unsafe { info.data::<String>(DESKTOP_ID) }.map(|p| unsafe { p.as_ref() }.clone()).unwrap_or_default()
}

// -- launching ------------------------------------------------------------------------------
/// The display's launch context when there is one: GTK gives the app an
/// xdg-activation token (seat, serial, the surface clicked), so Wayfire
/// lets it come to the front. A plain AppLaunchContext gives none: an app
/// already running (Files, Chrome, Steam) was asked to show itself without
/// one and stayed behind.
pub fn launch_context() -> gio::AppLaunchContext {
    if gtk::is_initialized_main_thread() {
        if let Some(d) = gtk::gdk::Display::default() {
            return d.app_launch_context().upcast();
        }
    }
    gio::AppLaunchContext::new()
}

/// Sonata's launch path: (app, files, context) -> launched.
pub type Launcher = fn(&gio::AppInfo, &[gio::File], Option<&gio::AppLaunchContext>) -> Result<(), glib::Error>;

static LAUNCHER: std::sync::OnceLock<Launcher> = std::sync::OnceLock::new();

/// The full launch path (the binary's: app lock, review, limits, GPU,
/// scope), once per process; until then [`plain_launch`].
pub fn set_launcher(f: Launcher) {
    let _ = LAUNCHER.set(f);
}

/// The shell's variables apps must never get: GTK_THEME breaks libadwaita
/// apps; the shell's ADW_DISABLE_PORTAL would keep apps from following
/// Dark Mode.
pub const NOT_HANDED_ON: [&str; 2] = ["GTK_THEME", "ADW_DISABLE_PORTAL"];

/// What every launch does at least: the display's context (the app may
/// come to the front) without the shell's own variables.
pub fn plain_launch(info: &gio::AppInfo, files: &[gio::File], ctx: Option<&gio::AppLaunchContext>) -> Result<(), glib::Error> {
    let ctx = ctx.cloned().unwrap_or_else(launch_context);
    for n in NOT_HANDED_ON {
        if std::env::var_os(n).is_some() {
            ctx.unsetenv(n);
        }
    }
    info.launch(files, Some(&ctx))
}

/// Open an app as Sonata does (apps.py's launch): every place that starts
/// an app calls this, not AppInfo::launch.
pub fn launch(info: impl AsRef<gio::AppInfo>, files: &[gio::File], ctx: Option<&gio::AppLaunchContext>) -> Result<(), glib::Error> {
    match LAUNCHER.get() {
        Some(f) => f(info.as_ref(), files, ctx),
        None => plain_launch(info.as_ref(), files, ctx),
    }
}

/// [`launch`] with URIs (AppInfo::launch_uris).
pub fn launch_uris(info: impl AsRef<gio::AppInfo>, uris: &[&str], ctx: Option<&gio::AppLaunchContext>) -> Result<(), glib::Error> {
    let files: Vec<gio::File> = uris.iter().map(|u| gio::File::for_uri(u)).collect();
    launch(info, &files, ctx)
}

/// AppInfo::launch_default_for_uri with the display's context (apps.py
/// patched it the same: the app opened may come to the front).
pub fn launch_default_for_uri(uri: &str, ctx: Option<&gio::AppLaunchContext>) -> Result<(), glib::Error> {
    let ctx = ctx.cloned().unwrap_or_else(launch_context);
    gio::AppInfo::launch_default_for_uri(uri, Some(&ctx))
}

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
mod name_tests {
    use super::*;

    fn entry(text: &str) -> gio::DesktopAppInfo {
        let kf = glib::KeyFile::new();
        kf.load_from_data(text, glib::KeyFileFlags::NONE).unwrap();
        gio::DesktopAppInfo::from_keyfile(&kf).expect("an entry")
    }

    /// apps.clean_name: macOS shows the app's name, not its packaging.
    #[test]
    fn test_app_names_showed_packaging_noise() {
        for (raw, clean) in [("Spotify (Launcher)", "Spotify"), ("Discord (Flatpak)", "Discord"), ("Firefox [Wayland]", "Firefox"),
                             ("Code - OSS", "Code - OSS"), ("Steam - Launcher", "Steam"), ("Web App (web app)", "Web App"),
                             ("(Flatpak)", "(Flatpak)"), ("", ""), ("Bin Packer", "Bin Packer")] {
            assert_eq!(clean_name(raw), clean, "{raw}");
        }
        let info = entry("[Desktop Entry]\nType=Application\nName=Spotify (Launcher)\nExec=true\n");
        assert_eq!(name(&info), "Spotify");
        assert_eq!(display_name(&info), "Spotify");
    }

    /// Vini: "Manage Printing" (CUPS' web page, refused when the service
    /// isn't running) was in the Rust session's Apps menu -- apps.py's
    /// NOT_APPS hid it from every list in Python.
    #[test]
    fn test_manage_printing_showed_in_apps() {
        let dir = tempfile::tempdir().unwrap();
        let p = dir.path().join("cups.desktop");
        std::fs::write(&p, "[Desktop Entry]\nType=Application\nName=Manage Printing\nExec=true\n").unwrap();
        let cups = gio::DesktopAppInfo::from_filename(&p).unwrap();
        assert!(cups.should_show(), "GIO itself shows it");
        assert!(!should_show(&cups));
        let p = dir.path().join("x.desktop");
        std::fs::write(&p, "[Desktop Entry]\nType=Application\nName=X\nExec=true\n").unwrap();
        assert!(should_show(&gio::DesktopAppInfo::from_filename(&p).unwrap()));
    }

    fn fake(_: &gio::AppInfo, files: &[gio::File], _: Option<&gio::AppLaunchContext>) -> Result<(), glib::Error> {
        Err(glib::Error::new(gio::IOErrorEnum::Cancelled, &format!("sonata's path: {}", files.len())))
    }

    /// Vini's Steam opened on the integrated GPU in the Rust session (and
    /// apps skipped the app lock's and a new Flatpak's questions): GIO's
    /// own launch was called, never apps.py's path. Every launch goes
    /// through the one the binary sets.
    #[test]
    fn test_launches_skipped_sonatas_launch_path() {
        set_launcher(fake);
        let info = entry("[Desktop Entry]\nType=Application\nName=X\nExec=true\n");
        let err = launch_uris(&info, &["file:///tmp/a"], None).unwrap_err();
        assert_eq!(err.message(), "sonata's path: 1");
    }
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
