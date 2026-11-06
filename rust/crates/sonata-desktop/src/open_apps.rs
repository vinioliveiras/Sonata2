//! The apps that were open (sonata2/open_apps.py), for "Reopen Apps" after
//! a crash, and to reopen them at the next login after Restart / Shut Down
//! / Log Out (Vini; macOS' "Reopen windows when logging back in").
//!
//! The Dock notes the running apps (desktop ids) a few seconds after they
//! change, in ~/.cache/sonata2/open-apps.json. After a crash (the session's
//! crash note) the first note of the new session first keeps the old list
//! as open-apps.before-crash.json; Feedbacker offers to reopen those.
//!
//! Restart / Shut Down / Log Out with the box ticked keep the list as
//! open-apps.reopen.json before the apps quit; the next session's Dock
//! reopens them (reopen_after_login), leaving out the apps already open by
//! then ("Open at Login" ones).
//!
//! The same files as the Python Sonata's (~/.cache/sonata2): one session,
//! one list, whichever Dock or Feedbacker reads it.
//!
//! ```text
//! note(&ids)                         // the Dock (debounced)
//! before_crash() -> ids              // Feedbacker
//! reopen(&ids, None)
//! save_for_login(&ids) / drop_for_login()
//! reopen_after_login(&running, None) // the Dock, once per session
//! ```

use gio::prelude::*;
use serde_json::{json, Value};
use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;

pub const FILE: &str = "open-apps.json";
pub const BEFORE: &str = "open-apps.before-crash.json";
/// For the next login (Restart / Shut Down / Log Out).
pub const REOPEN: &str = "open-apps.reopen.json";
/// The box's last choice (ticked, like macOS): session.json "reopen".
pub const CONFIG: &str = "session";
pub const DELAY_MS: u64 = 3000;
/// Not "apps you had open".
pub const SKIP: [&str; 2] = ["sonata2-launchpad", "io.github.vinioliveiras.sonata2.feedback"];
/// The session's crash note (tools/sonata-session: "<epoch> <exit code>").
pub const CRASH: &str = "last-crash";

/// What starts one app (default: Sonata's launch path, `sonata_ui::apps::launch`).
pub type Launcher = Rc<dyn Fn(&gio::DesktopAppInfo)>;

thread_local! {
    static PENDING: RefCell<(Option<glib::SourceId>, Vec<String>)> = const { RefCell::new((None, Vec::new())) };
}

pub fn dir() -> PathBuf {
    let cache = std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| glib::home_dir().join(".cache"));
    cache.join("sonata2")
}

/// The ids in one of the lists ([] when missing or damaged).
pub fn read(name: &str) -> Vec<String> {
    match std::fs::read(dir().join(name)).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
        Some(Value::Object(m)) => match m.get("apps") {
            Some(Value::Array(a)) => a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
            _ => Vec::new(),
        },
        _ => Vec::new(),
    }
}

/// Dock and Feedbacker both write here: a unique temp; a cache, no fsync.
pub fn write(name: &str, ids: &[String]) -> std::io::Result<()> {
    sonata_config::atomic_write(&dir().join(name), json!({"apps": ids}).to_string().as_bytes(), false)
}

/// The last session's crash time (epoch seconds), if it crashed.
pub fn crash_time() -> Option<i64> {
    let text = std::fs::read_to_string(sonata_session::logs::session_log_dir().join(CRASH)).ok()?;
    let mut parts = text.split_whitespace();
    let when = parts.next()?.parse::<i64>().ok()?;
    parts.next()?.parse::<i64>().ok()?;
    Some(when)
}

fn mtime(p: &Path) -> Option<f64> {
    let m = std::fs::metadata(p).ok()?.modified().ok()?;
    Some(m.duration_since(std::time::UNIX_EPOCH).ok()?.as_secs_f64())
}

/// The last session crashed and its list wasn't kept yet -> it becomes the
/// "before crash" list (the Dock's first note, or Feedbacker -- whichever
/// comes first; by the files' times, so only once).
pub fn rotate_after_crash() {
    rotate_after(crash_time());
}

fn rotate_after(crash: Option<i64>) {
    let Some(when) = crash else { return };
    let (src, dst) = (dir().join(FILE), dir().join(BEFORE));
    let Some(src_t) = mtime(&src) else { return };
    let kept = mtime(&dst).is_some_and(|d| d >= src_t);
    if src_t <= when as f64 + 5.0 && !kept {
        let _ = std::fs::rename(&src, &dst);
    }
}

fn installed(id: &str) -> bool {
    sonata_ui::apps::lookup(id).is_some()
}

/// Real apps (a desktop entry), Sonata's shell left out, in order, once.
pub fn wanted(ids: &[String]) -> Vec<String> {
    wanted_with(ids, &installed)
}

fn wanted_with(ids: &[String], known: &dyn Fn(&str) -> bool) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for i in ids {
        if i.is_empty() || SKIP.contains(&i.as_str()) || out.contains(i) || !known(i) {
            continue;
        }
        out.push(i.clone());
    }
    out
}

pub fn save_now(ids: &[String]) {
    save_now_with(ids, crash_time(), &installed);
}

fn save_now_with(ids: &[String], crash: Option<i64>, known: &dyn Fn(&str) -> bool) {
    rotate_after(crash);
    let ids = wanted_with(ids, known);
    if ids != read(FILE) {
        let _ = write(FILE, &ids);
    }
}

/// The Dock's running apps changed: saved a little later (a burst of
/// windows, one write).
pub fn note(ids: &[String]) {
    PENDING.with(|p| {
        let mut p = p.borrow_mut();
        p.1 = ids.to_vec();
        if p.0.is_none() {
            p.0 = Some(glib::timeout_add_local_once(std::time::Duration::from_millis(DELAY_MS), || {
                let ids = PENDING.with(|p| {
                    let mut p = p.borrow_mut();
                    p.0 = None;
                    std::mem::take(&mut p.1)
                });
                save_now(&ids);
            }));
        }
    });
}

/// The apps open before the last crash (Feedbacker's "Reopen Apps").
pub fn before_crash() -> Vec<String> {
    rotate_after_crash();
    wanted(&read(BEFORE))
}

/// The confirmation's box: reopen the apps at the next login.
pub fn reopen_wanted() -> bool {
    sonata_config::load(CONFIG, &json!({"reopen": true})).get("reopen").is_none_or(crate::badges::truthy)
}

pub fn set_reopen_wanted(on: bool) {
    if let Err(e) = sonata_config::update(CONFIG, &json!({"reopen": on})) {
        eprintln!("sonata2: open apps: {e}");
    }
}

/// The apps open now: the Dock's last note, then any window it didn't have
/// yet (a note is a few seconds late). toplevels: the windows' app ids.
pub fn current(toplevels: &[String]) -> Vec<String> {
    let mut ids = read(FILE);
    for a in toplevels {
        let key = crate::appmatch::match_app_id(a).unwrap_or_else(|| a.clone());
        if !key.is_empty() && !ids.contains(&key) {
            ids.push(key);
        }
    }
    wanted(&ids)
}

pub fn save_for_login(ids: &[String]) {
    let _ = write(REOPEN, &wanted(ids));
}

pub fn drop_for_login() {
    let _ = std::fs::remove_file(dir().join(REOPEN));
}

pub fn for_login() -> Vec<String> {
    wanted(&read(REOPEN))
}

/// "firefox.desktop" and "firefox" are the same app.
pub fn bare(desktop_id: &str) -> &str {
    desktop_id.strip_suffix(".desktop").unwrap_or(desktop_id)
}

fn kf_bool(kf: &glib::KeyFile, key: &str) -> Option<bool> {
    kf.boolean("Desktop Entry", key).ok()
}

fn kf_list(kf: &glib::KeyFile, key: &str) -> Vec<String> {
    kf.string_list("Desktop Entry", key).map(|l| l.iter().map(|s| s.to_string()).collect()).unwrap_or_default()
}

/// An autostart entry that starts in a Sonata session (autostart._wanted).
fn autostart_wanted(kf: &glib::KeyFile) -> bool {
    const DESKTOP: &str = "Sonata";
    if kf_bool(kf, "Hidden") == Some(true) || kf_bool(kf, "X-GNOME-Autostart-enabled") == Some(false) {
        return false;
    }
    let only = kf_list(kf, "OnlyShowIn");
    if !only.is_empty() && !only.iter().any(|d| d == DESKTOP) {
        return false;
    }
    if kf_list(kf, "NotShowIn").iter().any(|d| d == DESKTOP) {
        return false;
    }
    match kf.string("Desktop Entry", "TryExec") {
        Ok(t) if !t.is_empty() => glib::find_program_in_path(t.as_str()).is_some(),
        _ => true,
    }
}

/// Apps "Open at Login" starts anyway: not opened a second time. Their
/// entries' file names (autostart.entries: only entries GIO takes for an
/// app -- a link, or one whose program isn't installed, starts nothing).
pub fn autostarted() -> Vec<String> {
    let mut dirs = vec![glib::user_config_dir().join("autostart")];
    dirs.extend(glib::system_config_dirs().into_iter().map(|d| d.join("autostart")));
    autostarted_in(&dirs)
}

fn autostarted_in(dirs: &[PathBuf]) -> Vec<String> {
    let mut seen: Vec<String> = Vec::new();
    let mut out = Vec::new();
    for d in dirs {
        let Ok(rd) = std::fs::read_dir(d) else { continue };
        let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        for name in names {
            if !name.ends_with(".desktop") || seen.contains(&name) {
                continue;
            }
            seen.push(name.clone()); // a user entry hides the system one
            let kf = glib::KeyFile::new();
            if kf.load_from_file(d.join(&name), glib::KeyFileFlags::NONE).is_ok() && autostart_wanted(&kf) && gio::DesktopAppInfo::from_keyfile(&kf).is_some() {
                out.push(name);
            }
        }
    }
    out
}

/// The ids of the last session's list, without the ones open already or
/// started at login.
fn left_to_open(ids: Vec<String>, running: &[String], autostarted: &[String]) -> Vec<String> {
    let skip: Vec<&str> = running.iter().chain(autostarted).filter(|i| !i.is_empty()).map(|i| bare(i)).collect();
    ids.into_iter().filter(|i| !skip.contains(&bare(i))).collect()
}

/// The new session's apps from the last one (Restart / Shut Down / Log Out
/// with the box ticked), once: the list is used up. Apps open already (or
/// that Open at Login starts) are left alone.
pub fn reopen_after_login(running: &[String], launch: Option<Launcher>) -> usize {
    let ids = for_login();
    drop_for_login();
    let ids = left_to_open(ids, running, &autostarted());
    if ids.is_empty() {
        return 0;
    }
    reopen(&ids, launch)
}

/// The apps' names to show (their ids when not installed).
pub fn names(ids: &[String]) -> Vec<String> {
    ids.iter().map(|i| sonata_ui::apps::lookup(i).map(|a| sonata_ui::apps::display_name(&a)).unwrap_or_else(|| i.clone())).collect()
}

/// Starts them, a little apart (not all at once at login); the list is
/// then used up. Returns how many were started.
pub fn reopen(ids: &[String], launch: Option<Launcher>) -> usize {
    let infos: Vec<gio::DesktopAppInfo> = ids.iter().filter_map(|i| sonata_ui::apps::lookup(i)).collect();
    let launch: Launcher = launch.unwrap_or_else(|| {
        Rc::new(|info: &gio::DesktopAppInfo| {
            if let Err(e) = sonata_ui::apps::launch(info, &[], None) {
                eprintln!("sonata2: reopen {}: {e}", info.id().unwrap_or_default());
            }
        })
    });
    for (n, info) in infos.iter().enumerate() {
        let (info, launch) = (info.clone(), launch.clone());
        glib::timeout_add_local_once(std::time::Duration::from_millis(250 * n as u64), move || {
            // reopened as they were: a locked app doesn't ask
            crate::applock::trusted(|| launch(&info));
        });
    }
    let _ = std::fs::remove_file(dir().join(BEFORE));
    infos.len()
}

#[cfg(test)]
mod tests {
    use super::*;

    const KNOWN: [&str; 4] = ["firefox", "spotify", "sonata2-launchpad", "org.gnome.Calculator"];

    fn known(i: &str) -> bool {
        KNOWN.contains(&bare(i))
    }

    fn ids(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    fn clean() {
        for n in [FILE, BEFORE, REOPEN] {
            let _ = std::fs::remove_file(dir().join(n));
        }
    }

    fn age(name: &str, secs: u64) {
        let t = std::time::SystemTime::now() - std::time::Duration::from_secs(secs);
        std::fs::File::options().write(true).open(dir().join(name)).unwrap().set_modified(t).unwrap();
    }

    fn now() -> i64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs() as i64
    }

    #[test]
    fn test_only_real_apps_once() {
        let _l = crate::test_lock();
        clean();
        save_now_with(&ids(&["firefox", "?", "sonata2-launchpad", "firefox", "spotify"]), None, &known);
        assert_eq!(read(FILE), ["firefox", "spotify"]);
    }

    #[test]
    fn test_after_a_crash_the_old_list_is_offered() {
        let _l = crate::test_lock();
        clean();
        save_now_with(&ids(&["firefox", "spotify"]), None, &known);
        age(FILE, 60);
        let crash = Some(now() - 30);
        save_now_with(&ids(&["org.gnome.Calculator"]), crash, &known); // the new session's Dock: keeps the old one first
        assert_eq!(wanted_with(&read(BEFORE), &known), ["firefox", "spotify"]);
        assert_eq!(read(FILE), ["org.gnome.Calculator"]);
        save_now_with(&ids(&["firefox"]), crash, &known); // later notes never replace it
        assert_eq!(read(BEFORE), ["firefox", "spotify"]);
    }

    #[test]
    fn test_no_crash_nothing_offered() {
        let _l = crate::test_lock();
        clean();
        save_now_with(&ids(&["firefox"]), None, &known);
        rotate_after(None);
        assert!(read(BEFORE).is_empty());
    }

    #[test]
    fn test_ticked_by_default_and_remembered() {
        let _l = crate::test_lock();
        let _ = sonata_config::save(CONFIG, &serde_json::Map::new());
        assert!(reopen_wanted());
        set_reopen_wanted(false);
        assert!(!reopen_wanted());
        set_reopen_wanted(true);
    }

    #[test]
    fn test_next_login_opens_them_once_but_not_open_ones() {
        let left = left_to_open(ids(&["firefox", "spotify", "discord"]), &ids(&["spotify"]), &ids(&["discord.desktop"]));
        assert_eq!(left, ["firefox"]);
    }

    /// The Python Dock asked each login item its desktop id, which an
    /// entry read from its file doesn't have: an app both reopened and
    /// Open at Login was opened twice.
    #[test]
    fn test_login_items_were_opened_twice() {
        let d = tempfile::tempdir().unwrap();
        let (user, system) = (d.path().join("user"), d.path().join("system"));
        std::fs::create_dir_all(&user).unwrap();
        std::fs::create_dir_all(&system).unwrap();
        std::fs::write(user.join("discord.desktop"), "[Desktop Entry]\nType=Application\nName=Discord\nExec=true\n").unwrap();
        std::fs::write(user.join("off.desktop"), "[Desktop Entry]\nType=Application\nName=Off\nExec=off\nHidden=true\n").unwrap();
        std::fs::write(system.join("off.desktop"), "[Desktop Entry]\nType=Application\nName=Off\nExec=off\n").unwrap(); // hidden by the user's
        std::fs::write(system.join("gnome-only.desktop"), "[Desktop Entry]\nType=Application\nName=G\nExec=g\nOnlyShowIn=GNOME;\n").unwrap();
        let auto = autostarted_in(&[user, system]);
        assert_eq!(auto, ["discord.desktop"]);
        assert_eq!(left_to_open(ids(&["discord", "firefox"]), &[], &auto), ["firefox"]);
    }

    /// An autostart entry GIO doesn't take for an app (a link, or one whose
    /// program isn't installed) counted as an app started at login: a
    /// same-named app of the last session wasn't reopened
    /// (autostart.entries keeps only what GIO reads as an app).
    #[test]
    fn test_non_app_login_entry_kept_an_app_closed() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("firefox.desktop"), "[Desktop Entry]\nType=Link\nName=Firefox\nURL=https://example.org\n").unwrap();
        std::fs::write(d.path().join("gone.desktop"), "[Desktop Entry]\nType=Application\nName=N\nExec=no-such-program-anywhere\n").unwrap();
        assert!(autostarted_in(&[d.path().to_path_buf()]).is_empty());
    }

    #[test]
    fn test_bare() {
        assert_eq!(bare("firefox.desktop"), "firefox");
        assert_eq!(bare("firefox"), "firefox");
    }
}
