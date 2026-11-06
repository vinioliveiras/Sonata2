//! `autostart` (sonata2/autostart.py): XDG autostart for the Sonata session
//! (Wayfire doesn't run it), once at session start.
//!
//! Starts the apps in ~/.config/autostart and $XDG_CONFIG_DIRS/autostart
//! ("Open at Login" in the Dock writes there), following the spec: user
//! entries override system ones with the same file name; Hidden=true,
//! OnlyShowIn/NotShowIn (desktop "Sonata") and TryExec are honoured.
//! Before them, what a login sets up: the Setup Assistant on the first
//! login, Feedbacker after a crash, the password prompt (polkit) and Files
//! kept running.
//!
//! The steps that are still Python modules -- Sonata's title bars on other
//! apps (titlebars.py), the cedilla compose rules, the GTK style reset and
//! the Flatpak theme, KeePassXC -- run through the Python Sonata while it
//! is installed, in one short process, before any app starts (as before).

use crate::{keep, procs};
use gio::prelude::*;
use std::path::PathBuf;
use std::process::{Command, Stdio};

pub const DESKTOP: &str = "Sonata";

/// Highest priority first: the user's folder, then the system's.
pub fn dirs() -> Vec<PathBuf> {
    let cfg = sonata_config::config_home();
    let sys = std::env::var("XDG_CONFIG_DIRS").ok().filter(|v| !v.is_empty()).unwrap_or_else(|| "/etc/xdg".into());
    std::iter::once(cfg).chain(sys.split(':').filter(|d| !d.is_empty()).map(PathBuf::from)).map(|d| d.join("autostart")).collect()
}

fn wanted(kf: &glib::KeyFile) -> bool {
    const G: &str = "Desktop Entry";
    if kf.boolean(G, "Hidden").unwrap_or(false) || kf.boolean(G, "X-GNOME-Autostart-enabled").ok() == Some(false) {
        return false;
    }
    if let Ok(only) = kf.string_list(G, "OnlyShowIn") {
        if !only.is_empty() && !only.iter().any(|d| d == DESKTOP) {
            return false;
        }
    }
    if kf.string_list(G, "NotShowIn").is_ok_and(|l| l.iter().any(|d| d == DESKTOP)) {
        return false;
    }
    if let Ok(t) = kf.string(G, "TryExec") {
        if !t.is_empty() && glib::find_program_in_path(t.as_str()).is_none() {
            return false;
        }
    }
    true
}

/// (file name, app) of the entries to start from these folders.
pub fn entries_in(dirs: &[PathBuf]) -> Vec<(String, gio::DesktopAppInfo)> {
    let mut seen = std::collections::HashSet::new();
    let mut out = Vec::new();
    for d in dirs {
        let Ok(rd) = std::fs::read_dir(d) else { continue };
        let mut names: Vec<String> = rd.flatten().filter_map(|e| e.file_name().into_string().ok()).collect();
        names.sort();
        for name in names {
            if !name.ends_with(".desktop") || !seen.insert(name.clone()) {
                continue; // a user entry hides the system one
            }
            let kf = glib::KeyFile::new();
            if kf.load_from_file(d.join(&name), glib::KeyFileFlags::NONE).is_err() || !wanted(&kf) {
                continue;
            }
            if let Some(info) = gio::DesktopAppInfo::from_keyfile(&kf) {
                out.push((name, info));
            }
        }
    }
    out
}

pub fn entries() -> Vec<(String, gio::DesktopAppInfo)> {
    entries_in(&dirs())
}

/// The last session's crash note (feedback/report.py's crash(): "<epoch> <exit code>").
pub fn crashed(note: &str) -> bool {
    let mut p = note.split_whitespace();
    matches!((p.next().map(str::parse::<i64>), p.next().map(str::parse::<i64>)), (Some(Ok(_)), Some(Ok(_))))
}

/// Started and left to run on its own (GLib.spawn_async).
fn spawn(argv: &[String], what: &str) {
    // reaped when it ends (GLib.spawn_async did): a zombie otherwise
    match Command::new(&argv[0]).args(&argv[1..]).stdin(Stdio::null()).spawn() {
        Ok(c) => sonata_backend::run::reap(c),
        Err(e) => println!("sonata2-autostart: {what}: {e}"),
    }
}

fn me() -> String {
    std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "sonata2-rs".into())
}

/// gio's own launch: what `autostart` uses when nothing better is given.
pub fn launch_plain(_name: &str, info: &gio::DesktopAppInfo) -> Result<(), glib::Error> {
    info.launch(&[], None::<&gio::AppLaunchContext>)
}

/// `autostart`: everything a login starts, the login items through gio.
pub fn autostart(_args: &[String]) -> i32 {
    run(&launch_plain);
    0
}

/// The same with the login items started by `launch` -- for Sonata's own
/// launch (an app's GPU and environment, its scope, a locked app trusted
/// since you just logged in: applock.py, appscope.py, gpu.py), which lives
/// in a crate above this one. Returns how many started.
pub fn run(launch: &dyn Fn(&str, &gio::DesktopAppInfo) -> Result<(), glib::Error>) -> usize {
    run_with(launch, &|| {})
}

/// `autostart` with the setup steps (title bars, compose, GTK style,
/// keyring) given by `steps` and the login items opened by `launch`: they
/// live in crates above this one, so `sonata2-rs` passes them in. `launch` is
/// Sonata's own launch path: gio's alone gave a login item no GPU, no FPS
/// limit (Steam at login) and no scope of its own.
pub fn autostart_with(launch: &dyn Fn(&str, &gio::DesktopAppInfo) -> Result<(), glib::Error>, steps: &dyn Fn()) -> i32 {
    run_with(launch, steps);
    0
}

pub fn run_with(launch: &dyn Fn(&str, &gio::DesktopAppInfo) -> Result<(), glib::Error>, steps: &dyn Fn()) -> usize {
    steps();
    // first login: the Setup Assistant
    let setup = sonata_config::load("setup", &serde_json::json!({"done": false}));
    if !crate::stylecheck::truthy(setup.get("done")) {
        spawn(&keep::command_for(&["setup".into()]), "setup");
    }
    // the last session crashed: Feedbacker, its report already saved -- a
    // moment after the Dock and menu bar are up
    if crashed(&std::fs::read_to_string(crate::logs::session_log_dir().join("last-crash")).unwrap_or_default()) {
        let mut argv: Vec<String> = ["sh", "-c", "sleep 3; exec \"$@\"", "sh"].map(String::from).to_vec();
        argv.extend(keep::command_for(&["feedback".into()]));
        let mut c = Command::new(&argv[0]);
        c.args(&argv[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
        use std::os::unix::process::CommandExt;
        unsafe {
            c.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        match c.spawn() {
            Ok(c) => sonata_backend::run::reap(c),
            Err(e) => println!("sonata2-autostart: crash report: {e}"),
        }
    }
    let running = procs::running();
    // Sonata's password prompt (polkit agent), kept alive -- by this build:
    // the prompt is a ported component
    if procs::pids(&running, |a| procs::is_kept(a, "polkit")).is_empty() {
        spawn(&[me(), "keep".into(), "polkit".into()], "polkit");
    }
    // Files resident (like Finder): windows open at once
    if procs::pids(&running, |a| procs::is_component(a, "files")).is_empty() {
        spawn(&keep::command_for(&["files".into(), "--background".into()]), "files");
    }
    let mut started = 0;
    for (name, info) in entries() {
        match launch(&name, &info) {
            Ok(()) => started += 1,
            Err(e) => println!("sonata2-autostart: {name}: {}", e.message()),
        }
    }
    started
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn entry(d: &Path, name: &str, extra: &str) {
        std::fs::create_dir_all(d).unwrap();
        std::fs::write(d.join(name), format!("[Desktop Entry]\nType=Application\nName={name}\nExec=true\n{extra}")).unwrap();
    }

    /// tests/test_autostart.py's test_rules.
    #[test]
    fn test_rules() {
        let home = tempfile::tempdir().unwrap();
        let (user, system) = (home.path().join("cfg/autostart"), home.path().join("sys/autostart"));
        entry(&user, "a.desktop", "");
        entry(&user, "hidden.desktop", "Hidden=true\n");
        entry(&user, "gnome.desktop", "OnlyShowIn=GNOME;\n");
        entry(&user, "sonata.desktop", "OnlyShowIn=Sonata;\n");
        entry(&user, "notsonata.desktop", "NotShowIn=Sonata;\n");
        entry(&user, "disabled.desktop", "X-GNOME-Autostart-enabled=false\n");
        entry(&user, "tryexec.desktop", "TryExec=definitely-not-installed-xyz\n");
        entry(&system, "hidden.desktop", ""); // the user's Hidden=true wins over it
        entry(&system, "sys.desktop", "");
        let mut names: Vec<String> = entries_in(&[user, system]).into_iter().map(|(n, _)| n).collect();
        names.sort();
        assert_eq!(names, ["a.desktop", "sonata.desktop", "sys.desktop"]);
    }

    #[test]
    fn dirs_follow_xdg() {
        let _env = crate::tests::ENV.lock().unwrap_or_else(|e| e.into_inner());
        std::env::set_var("XDG_CONFIG_DIRS", "/a:/b");
        let d = dirs();
        std::env::remove_var("XDG_CONFIG_DIRS");
        assert_eq!(d[1..], [PathBuf::from("/a/autostart"), PathBuf::from("/b/autostart")]);
        assert!(d[0].ends_with("autostart"));
        assert_eq!(dirs()[1], PathBuf::from("/etc/xdg/autostart"));
    }

    #[test]
    fn crash_note() {
        assert!(crashed("1759457770 134\n"));
        assert!(!crashed(""));
        assert!(!crashed("garbage 1"));
    }
}
