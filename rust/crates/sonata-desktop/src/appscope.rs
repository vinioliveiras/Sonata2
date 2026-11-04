//! Every app opened from Sonata in a systemd scope of its own, under
//! sonata-apps.slice, which systemd-oomd watches (sonata2/appscope.py;
//! macOS: an app that runs the memory out is closed, the computer goes
//! on). Apps together never take the memory kept for Sonata and the
//! system (reserve_mb).
//!
//! GNOME does the same (app-gnome-<id>-<pid>.scope): the launched process
//! is moved into a transient scope over D-Bus right after it starts.
//! Nothing is capped in normal use; when the whole system's memory is
//! under pressure (or swap is nearly full), systemd-oomd kills the app that
//! is causing it, not the session. Web apps make their own scope in the
//! same slice.
//!
//! The slice is written to $XDG_RUNTIME_DIR/systemd/user (gone at logout,
//! nothing left behind) -- the same file the Python Sonata writes.

use gio::prelude::*;
use glib::variant::ToVariant;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const SLICE: &str = "sonata-apps.slice";
/// Memory kept for Sonata and the system: apps together never take it (the
/// menu bar, the Dock and Wayfire stay responsive while an app runs away).
pub const RESERVE_MIN_MB: i64 = 1536;
pub const RESERVE_MAX_MB: i64 = 4096;
pub const RESERVE_SHARE: f64 = 0.10;
/// The kernel's own OOM killer, if it ever comes to it: apps before Sonata.
pub const APP_OOM_SCORE: i32 = 200;
/// They make their own scope (web apps).
pub const OWN_SCOPE: [&str; 1] = ["io.github.vinioliveiras.sonata2.webapp."];
/// An app's processes besides its launcher's name (Steam's own runs from ~/.local/share/Steam).
pub const EXTRA_PROCESSES: [(&str, &[&str]); 1] = [("steam", &["steam", "steamwebhelper", "steam.sh"])];

pub fn reserve_mb(total_mb: i64) -> i64 {
    (RESERVE_MAX_MB as f64).min((RESERVE_MIN_MB as f64).max(total_mb as f64 * RESERVE_SHARE)) as i64
}

/// MemTotal in MB (webapps._meminfo; 0 when unknown).
pub fn mem_total_mb() -> i64 {
    let text = std::fs::read_to_string("/proc/meminfo").unwrap_or_default();
    text.lines().find_map(|l| l.strip_prefix("MemTotal:")?.split_whitespace().next()?.parse::<i64>().ok()).map_or(0, |kb| kb / 1024)
}

/// The slice's unit file; total_mb None: this computer's memory.
pub fn slice_text(total_mb: Option<i64>) -> String {
    let total_mb = total_mb.unwrap_or_else(mem_total_mb);
    let mut limits = String::new();
    if total_mb > 2 * RESERVE_MIN_MB {
        let top = total_mb - reserve_mb(total_mb);
        // slowed down first, then the cap
        limits = format!("MemoryHigh={}M\nMemoryMax={top}M\n", top - 512);
    }
    format!("[Unit]\nDescription=Apps opened from Sonata\n\n[Slice]\n{limits}ManagedOOMMemoryPressure=kill\nManagedOOMMemoryPressureLimit=50%\nManagedOOMMemoryPressureDurationSec=10s\nManagedOOMSwap=kill\n")
}

/// systemd's unit-name escaping ("-" too: it separates the name's parts).
pub fn escape(app_id: &str) -> String {
    let app_id = if app_id.is_empty() { "app" } else { app_id };
    let mut out = String::new();
    for c in app_id.chars() {
        if c.is_ascii_alphanumeric() || ":_.".contains(c) {
            out.push(c);
        } else {
            let mut buf = [0u8; 4];
            for b in c.encode_utf8(&mut buf).bytes() {
                out.push_str(&format!("\\x{b:02x}"));
            }
        }
    }
    out
}

/// app-sonata2-<id>-<pid>.scope (the XDG name: app-<launcher>-<id>-<random>).
pub fn unit_name(app_id: &str, pid: i64) -> String {
    let app_id = app_id.strip_suffix(".desktop").unwrap_or(app_id);
    format!("app-sonata2-{}-{pid}.scope", escape(app_id))
}

pub fn slice_path() -> PathBuf {
    let run = std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(glib::user_runtime_dir);
    run.join("systemd").join("user").join(SLICE)
}

fn bus() -> Option<gio::DBusConnection> {
    gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE).ok()
}

fn manager(bus: &gio::DBusConnection, method: &str, params: Option<&glib::Variant>) {
    bus.call(Some("org.freedesktop.systemd1"), "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager", method, params, None, gio::DBusCallFlags::NONE, 5000, gio::Cancellable::NONE, |_| {});
}

/// Write the slice when it differs: Some(true) written, Some(false) the
/// same already, None it couldn't be written.
pub fn write_slice(path: &Path, text: &str) -> Option<bool> {
    if std::fs::read_to_string(path).is_ok_and(|t| t == text) {
        return Some(false);
    }
    std::fs::create_dir_all(path.parent()?).ok()?;
    let tmp = PathBuf::from(format!("{}.tmp", path.display()));
    std::fs::write(&tmp, text).ok()?;
    std::fs::rename(&tmp, path).ok()?;
    Some(true)
}

/// The slice in place (once per session: rewritten only when it changed).
pub fn ensure_slice() -> bool {
    match write_slice(&slice_path(), &slice_text(None)) {
        None => false,
        Some(false) => true,
        Some(true) => {
            if let Some(bus) = bus() {
                manager(&bus, "Reload", None);
            }
            true
        }
    }
}

// the newest scope of each app opened from Sonata: {desktop id: unit name}
// (the Dock stops one whose app never showed a window -- stuck while starting)
static LAUNCHED: Mutex<Option<HashMap<String, String>>> = Mutex::new(None);

/// The newest scope of an app opened from Sonata (appscope.launched.get).
pub fn launched(app_id: &str) -> Option<String> {
    LAUNCHED.lock().unwrap_or_else(|e| e.into_inner()).as_ref()?.get(app_id).cloned()
}

/// Forget it, returning it (appscope.launched.pop).
pub fn take_launched(app_id: &str) -> Option<String> {
    LAUNCHED.lock().unwrap_or_else(|e| e.into_inner()).as_mut()?.remove(app_id)
}

fn set_launched(app_id: &str, unit: String) {
    LAUNCHED.lock().unwrap_or_else(|e| e.into_inner()).get_or_insert_with(HashMap::new).insert(app_id.to_string(), unit);
}

/// Stop the app's newest scope (every process of that launch). False: none known.
pub fn stop(app_id: &str) -> bool {
    let unit = take_launched(app_id);
    let (Some(unit), Some(bus)) = (unit, bus()) else { return false };
    manager(&bus, "StopUnit", Some(&(unit.as_str(), "replace").to_variant()));
    true
}

/// The process names (/proc/<pid>/comm, 15 characters) an app runs as: its
/// command's program, and the known extra ones (Steam's). From its Exec
/// line and desktop id.
pub fn process_names_of(commandline: &str, desktop_id: &str) -> HashSet<String> {
    let mut names = HashSet::new();
    let argv = crate::sandbox::exec_args(commandline, &[]);
    if let Some(first) = argv.first() {
        let prog = first.rsplit('/').next().unwrap_or("");
        if !["env", "sh", "bash", "flatpak", "python3", "python"].contains(&prog) {
            names.insert(prog.chars().take(15).collect());
        }
    }
    let key = desktop_id.strip_suffix(".desktop").unwrap_or(desktop_id);
    for (k, extra) in EXTRA_PROCESSES {
        if k == key {
            names.extend(extra.iter().map(|n| n.chars().take(15).collect::<String>()));
        }
    }
    names
}

/// process_names_of an app.
pub fn process_names(info: Option<&gio::AppInfo>) -> HashSet<String> {
    let cmd = info.and_then(|i| i.commandline()).map(|c| c.to_string_lossy().into_owned()).unwrap_or_default();
    let id = info.and_then(|i| i.id()).map(|s| s.to_string()).unwrap_or_default();
    process_names_of(&cmd, &id)
}

/// The user's own processes (not `me`) under `proc` whose comm is one of `names`.
pub fn matching_pids(proc: &Path, names: &HashSet<String>, me: i32, uid: u32) -> Vec<i32> {
    use std::os::unix::fs::MetadataExt;
    let Ok(rd) = std::fs::read_dir(proc) else { return Vec::new() };
    let mut out = Vec::new();
    for e in rd.flatten() {
        let Some(pid) = e.file_name().to_str().and_then(|s| s.parse::<i32>().ok()) else { continue };
        if pid == me || std::fs::metadata(e.path()).map(|m| m.uid()).ok() != Some(uid) {
            continue;
        }
        let Ok(comm) = std::fs::read_to_string(e.path().join("comm")) else { continue };
        if names.contains(comm.trim()) {
            out.push(pid);
        }
    }
    out
}

/// Force the app closed (Vini: before opening it again when it didn't
/// open): every process of its launch's scope, and the user's own
/// processes running as the app's program (one left by an earlier launch,
/// outside the scope -- Steam's). SIGKILL. Returns how many processes
/// were signalled (the scope counts as one).
pub fn kill(app_id: &str, info: Option<&gio::AppInfo>, proc: &Path) -> usize {
    let mut n = 0;
    if let (Some(unit), Some(bus)) = (take_launched(app_id), bus()) {
        manager(&bus, "KillUnit", Some(&(unit.as_str(), "all", libc::SIGKILL).to_variant()));
        n += 1;
    }
    let names = process_names(info);
    if names.is_empty() {
        return n;
    }
    let (me, uid) = (std::process::id() as i32, unsafe { libc::getuid() });
    for pid in matching_pids(proc, &names, me, uid) {
        if unsafe { libc::kill(pid, libc::SIGKILL) } == 0 {
            n += 1;
        }
    }
    n
}

/// The launched app's process into its scope (asynchronous; appscope.move).
pub fn move_to_scope(pid: i64, app_id: &str) -> bool {
    if pid <= 0 || OWN_SCOPE.iter().any(|p| app_id.starts_with(p)) {
        return false;
    }
    let unit = unit_name(app_id, pid);
    set_launched(app_id, unit.clone());
    let Some(bus) = bus() else { return false };
    let props: Vec<(String, glib::Variant)> = vec![
        ("PIDs".into(), vec![pid as u32].to_variant()),
        ("Slice".into(), SLICE.to_variant()),
        ("CollectMode".into(), "inactive-or-failed".to_variant()),
        ("Description".into(), format!("{app_id} (opened from Sonata)").to_variant()),
    ];
    let aux = glib::Variant::array_from_iter_with_type(glib::VariantTy::new("(sa(sv))").unwrap(), std::iter::empty::<glib::Variant>());
    let params = glib::Variant::tuple_from_iter([unit.to_variant(), "fail".to_variant(), props.to_variant(), aux]);
    manager(&bus, "StartTransientUnit", Some(&params));
    raise_oom_score(pid, APP_OOM_SCORE);
    true
}

/// Apps go before Sonata if the kernel has to kill (raising it needs no rights).
pub fn raise_oom_score(pid: i64, score: i32) -> bool {
    raise_oom_score_at(Path::new(&format!("/proc/{pid}/oom_score_adj")), score)
}

fn raise_oom_score_at(path: &Path, score: i32) -> bool {
    let Ok(text) = std::fs::read_to_string(path) else { return false };
    let t = text.trim();
    let Ok(now) = (if t.is_empty() { Ok(0) } else { t.parse::<i32>() }) else { return false };
    now >= score || std::fs::write(path, score.to_string()).is_ok()
}

/// Send the pending scope moves now (a short-lived launcher -- the login
/// items' -- would exit before they leave).
pub fn flush() {
    if let Some(bus) = bus() {
        let _ = bus.flush_sync(gio::Cancellable::NONE);
    }
}

/// Move what `context` launches (Gio's "launched": the new process' pid),
/// connected once per context.
pub fn watch(context: &gio::AppLaunchContext, app_id: &str) {
    const MARK: &str = "sonata-scope";
    // SAFETY: the mark is only ever set and read as a bool, here
    if unsafe { context.data::<bool>(MARK) }.is_some() {
        return;
    }
    unsafe { context.set_data(MARK, true) };
    let app_id = app_id.to_string();
    context.connect_launched(move |_ctx, info, platform_data| {
        let pid = platform_data.get::<HashMap<String, glib::Variant>>().and_then(|d| d.get("pid")?.get::<i32>()).unwrap_or(0);
        let id = info.id().map(|s| s.to_string()).filter(|s| !s.is_empty()).unwrap_or_else(|| app_id.clone());
        move_to_scope(pid as i64, &id);
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_text() {
        let t = slice_text(Some(64 * 1024));
        assert!(t.contains("ManagedOOMMemoryPressure=kill"));
        assert!(t.contains("ManagedOOMSwap=kill"));
        let top = 64 * 1024 - RESERVE_MAX_MB; // 10% would be 6.4 GB: capped at 4
        assert!(t.contains(&format!("MemoryMax={top}M")));
        assert!(t.contains(&format!("MemoryHigh={}M", top - 512)));
        assert!(slice_text(Some(8192)).contains(&format!("MemoryMax={}M", 8192 - RESERVE_MIN_MB))); // small: at least 1.5 GB kept
        assert!(!slice_text(Some(0)).contains("MemoryMax")); // unknown: no cap
    }

    #[test]
    fn test_reserve() {
        assert_eq!(reserve_mb(4096), RESERVE_MIN_MB);
        assert_eq!(reserve_mb(20000), 2000);
        assert_eq!(reserve_mb(128000), RESERVE_MAX_MB);
    }

    #[test]
    fn test_written_once() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("systemd/user").join(SLICE);
        assert_eq!(write_slice(&p, "a"), Some(true));
        assert_eq!(write_slice(&p, "a"), Some(false)); // reloaded only when it changed
        assert_eq!(write_slice(&p, "b"), Some(true));
    }

    #[test]
    fn test_unit_name() {
        assert_eq!(unit_name("org.gnome.Nautilus.desktop", 42), "app-sonata2-org.gnome.Nautilus-42.scope");
        assert_eq!(unit_name("my-app", 7), "app-sonata2-my\\x2dapp-7.scope");
        assert_eq!(escape(""), "app");
        assert_eq!(escape("é"), "\\xc3\\xa9");
    }

    #[test]
    fn test_web_apps_make_their_own() {
        assert!(!move_to_scope(5, "io.github.vinioliveiras.sonata2.webapp.abc"));
        assert!(!move_to_scope(0, "steam"));
        assert_eq!(launched("steam"), None);
    }

    #[test]
    fn test_process_names() {
        assert_eq!(process_names_of("/usr/bin/steam %U", "steam.desktop"), HashSet::from(["steam".into(), "steamwebhelper".into(), "steam.sh".into()]));
        assert_eq!(process_names_of("env FOO=1 app", "x"), HashSet::new());
        assert_eq!(process_names_of("averyveryverylongprogramname", "x"), HashSet::from(["averyveryverylo".into()]));
    }

    #[test]
    fn test_matching_pids() {
        let d = tempfile::tempdir().unwrap();
        for (pid, comm) in [("10", "steam\n"), ("11", "firefox\n"), ("12", "steam\n"), ("self", "steam\n")] {
            std::fs::create_dir_all(d.path().join(pid)).unwrap();
            std::fs::write(d.path().join(pid).join("comm"), comm).unwrap();
        }
        let uid = unsafe { libc::getuid() };
        let mut got = matching_pids(d.path(), &HashSet::from(["steam".to_string()]), 12, uid);
        got.sort();
        assert_eq!(got, [10]); // not this process, not another program
        assert!(matching_pids(d.path(), &HashSet::from(["steam".to_string()]), 0, uid + 1).is_empty()); // only the user's own
    }

    #[test]
    fn test_oom_score_only_raised() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("oom_score_adj");
        std::fs::write(&p, "0").unwrap();
        assert!(raise_oom_score_at(&p, APP_OOM_SCORE));
        assert_eq!(std::fs::read_to_string(&p).unwrap(), APP_OOM_SCORE.to_string());
        std::fs::write(&p, "500").unwrap();
        assert!(raise_oom_score_at(&p, APP_OOM_SCORE));
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "500");
        assert!(!raise_oom_score_at(&d.path().join("none"), APP_OOM_SCORE));
    }
}
