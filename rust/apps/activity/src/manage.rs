//! Task Manager's page data that isn't a /proc counter
//! (activity/manage.py): grouping processes into apps (Processes page),
//! per-user totals (Users), cumulative app usage (App history), login items
//! (Startup apps) and systemd services (Services). No GTK: the window calls
//! these off the main loop where they block.

use serde_json::{json, Map, Value};
use sonata_backend::procfs::{Proc, Snapshot};
use sonata_i18n::n_;
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};

// -- Processes: apps / background / system ----------------------------------------------------
/// ({app key: [pids]}, background pids, system pids).
pub type Groups = (BTreeMap<String, Vec<i64>>, Vec<i64>, Vec<i64>);

/// Windows Task Manager's groups. app_of(proc) -> the desktop app a
/// process belongs to. Apps: the user's processes of a desktop app whose
/// parent isn't the same app, merged by key, each with every process below
/// it (helpers, renderers...). System processes: kernel threads and
/// processes of system accounts (uid < 1000, not the user's).
/// Background: the rest. An app's pids start with its top processes.
pub fn group_processes(procs: &BTreeMap<i64, Proc>, app_of: impl Fn(&Proc) -> Option<String>, my_uid: i64) -> Groups {
    let mut children: HashMap<i64, Vec<i64>> = HashMap::new();
    for p in procs.values() {
        children.entry(p.ppid).or_default().push(p.pid);
    }
    let keys: HashMap<i64, Option<String>> = procs.iter().map(|(pid, p)| (*pid, if p.uid == my_uid && !p.exe.is_empty() { app_of(p) } else { None })).collect();
    let mut apps: BTreeMap<String, Vec<i64>> = BTreeMap::new();
    let mut owned: HashSet<i64> = HashSet::new();
    for (pid, p) in procs {
        let Some(key) = keys[pid].clone() else { continue };
        if owned.contains(pid) {
            continue;
        }
        if procs.get(&p.ppid).is_some_and(|parent| keys.get(&parent.pid).cloned().flatten().as_ref() == Some(&key)) {
            continue; // a child of the same app: under its top process
        }
        let members = apps.entry(key).or_default();
        let mut stack = vec![*pid];
        while let Some(cur) = stack.pop() {
            // the whole tree below it
            if !owned.insert(cur) {
                continue;
            }
            members.push(cur);
            let mut kids = children.get(&cur).cloned().unwrap_or_default();
            kids.sort_unstable_by(|a, b| b.cmp(a));
            stack.extend(kids);
        }
    }
    let (mut background, mut system) = (Vec::new(), Vec::new());
    for (pid, p) in procs {
        if owned.contains(pid) {
            continue;
        }
        if p.exe.is_empty() || (p.uid < 1000 && p.uid != my_uid) {
            system.push(*pid);
        } else {
            background.push(*pid);
        }
    }
    (apps, background, system)
}

// -- Users ------------------------------------------------------------------------------------
#[derive(Clone, Debug, Default, PartialEq)]
pub struct UserTotals {
    pub cpu: f64,
    pub mem: u64,
    pub procs: i64,
    pub uid: i64,
    pub disk: f64,
}

/// CPU (% of one core), memory (RSS), disk and process count per user.
pub fn per_user(procs: &BTreeMap<i64, Proc>) -> BTreeMap<String, UserTotals> {
    let mut out: BTreeMap<String, UserTotals> = BTreeMap::new();
    for p in procs.values() {
        let name = if p.user.is_empty() { p.uid.to_string() } else { p.user.clone() };
        let u = out.entry(name).or_insert_with(|| UserTotals { uid: p.uid, ..Default::default() });
        u.cpu += p.cpu;
        u.mem += p.rss;
        u.procs += 1;
        u.disk += p.read_ps + p.write_ps;
    }
    out
}

// -- App history ------------------------------------------------------------------------------
fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct AppUse {
    pub name: String,
    pub cpu_time: f64,
    pub disk: f64,
    /// when it was last sampled (written only once it has been, as manage.py does)
    pub last: Option<f64>,
    /// the entry's other keys, written back as they were (manage.py keeps dict(v))
    pub extra: Map<String, Value>,
}

/// CPU time and disk traffic per app, added up from the samples (Windows'
/// App history). Stored as config "activity-history".
#[derive(Clone, Debug, PartialEq)]
pub struct History {
    pub since: f64,
    pub apps: BTreeMap<String, AppUse>,
}

impl History {
    pub fn from_value(data: &Map<String, Value>) -> Self {
        let since = data.get("since").and_then(Value::as_f64).filter(|s| *s != 0.0).unwrap_or_else(now);
        let apps = data
            .get("apps")
            .and_then(Value::as_object)
            .map(|m| {
                m.iter()
                    .map(|(k, v)| {
                        let f = |n: &str| v.get(n).and_then(Value::as_f64).unwrap_or(0.0);
                        let mut extra = v.as_object().cloned().unwrap_or_default();
                        for known in ["name", "cpu_time", "disk", "last"] {
                            extra.remove(known);
                        }
                        (k.clone(), AppUse { name: v.get("name").and_then(Value::as_str).unwrap_or(k).to_string(), cpu_time: f("cpu_time"), disk: f("disk"), last: v.get("last").and_then(Value::as_f64), extra })
                    })
                    .collect()
            })
            .unwrap_or_default();
        Self { since, apps }
    }

    /// One sample: each app's processes used cpu% of a core for
    /// snap.interval seconds and moved read/write bytes per second.
    pub fn add(&mut self, snap: &Snapshot, apps: &BTreeMap<String, Vec<i64>>, names: &HashMap<String, String>) {
        let dt = snap.interval;
        if dt <= 0.0 {
            return;
        }
        for (key, pids) in apps {
            let (mut cpu, mut disk) = (0.0, 0.0);
            for p in pids.iter().filter_map(|pid| snap.procs.get(pid)) {
                cpu += p.cpu / 100.0 * dt;
                disk += (p.read_ps + p.write_ps) * dt;
            }
            let a = self.apps.entry(key.clone()).or_insert_with(|| AppUse { name: names.get(key).cloned().unwrap_or_else(|| key.clone()), ..Default::default() });
            if let Some(n) = names.get(key) {
                a.name = n.clone();
            }
            a.cpu_time += cpu;
            a.disk += disk;
            a.last = Some(now());
        }
    }

    pub fn clear(&mut self) {
        self.apps.clear();
        self.since = now();
    }

    pub fn to_value(&self) -> Value {
        let apps: Map<String, Value> = self
            .apps
            .iter()
            .map(|(k, a)| {
                let mut m = a.extra.clone();
                m.insert("name".into(), json!(a.name));
                m.insert("cpu_time".into(), json!(a.cpu_time));
                m.insert("disk".into(), json!(a.disk));
                if let Some(last) = a.last {
                    m.insert("last".into(), json!(last));
                }
                (k.clone(), Value::Object(m))
            })
            .collect();
        json!({"since": self.since, "apps": apps})
    }
}

// -- Startup apps -----------------------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct StartupEntry {
    /// file name (the entry's id: a user copy overrides the system one)
    pub file: String,
    /// where it was read
    pub path: PathBuf,
    pub name: String,
    pub comment: String,
    pub command: String,
    pub icon: String,
    pub enabled: bool,
    /// the file lives in the user's autostart folder
    pub user: bool,
}

fn user_dir() -> PathBuf {
    sonata_config::config_home().join("autostart")
}

fn system_dirs() -> Vec<PathBuf> {
    std::env::var("XDG_CONFIG_DIRS").ok().filter(|v| !v.is_empty()).unwrap_or_else(|| "/etc/xdg".into()).split(':').filter(|d| !d.is_empty()).map(|d| Path::new(d).join("autostart")).collect()
}

/// [Desktop Entry] keys of a .desktop file (untranslated values).
fn desktop_section(text: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let mut inside = false;
    for line in text.lines() {
        let s = line.trim();
        if s.starts_with('[') {
            inside = s == "[Desktop Entry]";
            continue;
        }
        if inside && s.contains('=') && !s.starts_with('#') {
            let (k, v) = s.split_once('=').unwrap();
            out.entry(k.trim().to_string()).or_insert_with(|| v.trim().to_string());
        }
    }
    out
}

/// The login items: ~/.config/autostart over $XDG_CONFIG_DIRS/autostart
/// (same file name: the user's wins), sorted by name.
pub fn startup_entries(user: Option<&Path>, system: Option<&[PathBuf]>) -> Vec<StartupEntry> {
    let user_dir = user.map(Path::to_path_buf).unwrap_or_else(user_dir);
    let mut dirs = vec![user_dir.clone()];
    dirs.extend(system.map(<[PathBuf]>::to_vec).unwrap_or_else(system_dirs));
    let mut seen = HashSet::new();
    let mut out = Vec::new();
    for d in dirs {
        let Ok(rd) = std::fs::read_dir(&d) else { continue };
        let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        for f in names {
            if !f.ends_with(".desktop") || !seen.insert(f.clone()) {
                continue;
            }
            let path = d.join(&f);
            let Ok(bytes) = std::fs::read(&path) else { continue };
            let kv = desktop_section(&String::from_utf8_lossy(&bytes));
            let hidden = kv.get("Hidden").is_some_and(|v| v.trim().eq_ignore_ascii_case("true"));
            let gnome_on = kv.get("X-GNOME-Autostart-enabled").map(|v| v.to_lowercase() != "false").unwrap_or(true);
            let g = |k: &str| kv.get(k).cloned().unwrap_or_default();
            let name = kv.get("Name").filter(|n| !n.is_empty()).cloned().unwrap_or_else(|| f[..f.len() - 8].to_string());
            out.push(StartupEntry { file: f.clone(), path, name, comment: g("Comment"), command: g("Exec"), icon: g("Icon"), enabled: !hidden && gnome_on, user: d == user_dir });
        }
    }
    out.sort_by_key(|e| e.name.to_lowercase());
    out
}

/// Set (Some) or remove (None) keys of the [Desktop Entry] section.
fn set_keys(text: &str, values: &[(&str, Option<&str>)]) -> String {
    let mut out: Vec<String> = Vec::new();
    let mut inside = false;
    let mut done: HashSet<&str> = HashSet::new();
    let missing = |done: &HashSet<&str>| values.iter().filter(|(k, v)| v.is_some() && !done.contains(k)).map(|(k, v)| format!("{k}={}", v.unwrap())).collect::<Vec<_>>();
    for line in text.lines() {
        let s = line.trim();
        if s.starts_with('[') {
            if inside {
                // end of the section: add what's missing
                out.extend(missing(&done));
                done.extend(values.iter().map(|(k, _)| *k));
            }
            inside = s == "[Desktop Entry]";
            out.push(line.to_string());
            continue;
        }
        if inside && s.contains('=') && !s.starts_with('#') {
            let k = s.split_once('=').unwrap().0.trim();
            if let Some((key, v)) = values.iter().find(|(key, _)| *key == k) {
                if let (Some(v), false) = (v, done.contains(key)) {
                    out.push(format!("{key}={v}"));
                }
                done.insert(key);
                continue;
            }
        }
        out.push(line.to_string());
    }
    if inside {
        out.extend(missing(&done));
    }
    out.join("\n") + "\n"
}

/// Enable/disable a login item the XDG way: a copy in the user's
/// autostart folder gets Hidden=true (off) or loses it (on); a system file
/// is never touched. The path written.
pub fn set_startup_enabled(entry: &mut StartupEntry, on: bool, user: Option<&Path>) -> std::io::Result<PathBuf> {
    let dir = user.map(Path::to_path_buf).unwrap_or_else(user_dir);
    std::fs::create_dir_all(&dir)?;
    let target = dir.join(&entry.file);
    let src = if target.exists() { target.clone() } else { entry.path.clone() };
    let text = String::from_utf8_lossy(&std::fs::read(&src)?).into_owned();
    let mut values: Vec<(&str, Option<&str>)> = vec![("Hidden", if on { None } else { Some("true") })];
    if on && desktop_section(&text).get("X-GNOME-Autostart-enabled").is_some_and(|v| v.to_lowercase() == "false") {
        values.push(("X-GNOME-Autostart-enabled", Some("true")));
    }
    sonata_config::atomic_write(&target, set_keys(&text, &values).as_bytes(), true)?;
    entry.enabled = on;
    entry.path = target.clone();
    entry.user = true;
    Ok(target)
}

// -- Services ---------------------------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct Service {
    pub unit: String,
    pub description: String,
    pub load: String,
    /// active / inactive / failed / activating...
    pub active: String,
    /// running / exited / dead...
    pub sub: String,
    /// "user" / "system"
    pub scope: String,
}

impl Service {
    /// The state shown (English; shown through gettext).
    pub fn status(&self) -> &'static str {
        match self.active.as_str() {
            "active" if self.sub == "running" => n_!("Running"),
            "active" => n_!("Active"),
            "failed" => n_!("Failed"),
            "activating" => n_!("Activating"),
            "deactivating" => n_!("Deactivating"),
            "reloading" => n_!("Reloading"),
            _ => n_!("Stopped"),
        }
    }
}

pub fn parse_units_json(text: &str, scope: &str) -> Result<Vec<Service>, serde_json::Error> {
    let units: Vec<Value> = serde_json::from_str(if text.trim().is_empty() { "[]" } else { text })?;
    Ok(units
        .iter()
        .filter(|u| u.get("unit").and_then(Value::as_str).is_some_and(|n| n.ends_with(".service")))
        .map(|u| {
            let g = |k: &str| u.get(k).and_then(Value::as_str).unwrap_or("").to_string();
            Service { unit: g("unit"), description: g("description"), load: g("load"), active: g("active"), sub: g("sub"), scope: scope.into() }
        })
        .collect())
}

/// `systemctl list-units --plain --no-legend` lines: UNIT LOAD ACTIVE SUB DESCRIPTION.
pub fn parse_units_text(text: &str, scope: &str) -> Vec<Service> {
    let mut out = Vec::new();
    for line in text.lines() {
        let (fields, rest) = split_ws(line.trim_start_matches(['●', '*', '×', ' ']), 4);
        if fields.len() >= 4 && fields[0].ends_with(".service") {
            out.push(Service { unit: fields[0].into(), description: rest.into(), load: fields[1].into(), active: fields[2].into(), sub: fields[3].into(), scope: scope.into() });
        }
    }
    out
}

/// The first n words, and the rest after them (Python's split(None, n)).
fn split_ws(s: &str, n: usize) -> (Vec<&str>, &str) {
    let mut words = Vec::new();
    let mut rest = s.trim_start();
    while words.len() < n && !rest.is_empty() {
        let end = rest.find(char::is_whitespace).unwrap_or(rest.len());
        words.push(&rest[..end]);
        rest = rest[end..].trim_start();
    }
    (words, rest)
}

fn systemctl(scope: &str) -> Vec<String> {
    let mut v = vec!["systemctl".to_string()];
    if scope == "user" {
        v.push("--user".into());
    }
    v
}

/// s: polkit / pkexec wait on the password dialog; 20 s killed it mid-typing
pub const AUTH_TIMEOUT: u64 = 600;

/// How long a Start / Stop / Restart may take: a system unit goes through
/// polkit, whose password dialog the user may be typing into.
pub fn action_timeout(scope: &str) -> u64 {
    if scope == "system" { AUTH_TIMEOUT } else { 20 }
}

/// cmd -> (return code, output); 127 when the program is missing.
pub fn run(cmd: &[String], timeout_s: u64) -> (i32, String) {
    let args: Vec<&str> = cmd.iter().map(String::as_str).collect();
    sonata_backend::run::run(&args, timeout_s)
}

/// Every service unit of one scope; None when systemctl can't answer (no
/// systemd, no bus). run(cmd) -> (returncode, stdout) for tests.
pub fn list_services(scope: &str, run: &dyn Fn(&[String]) -> (i32, String)) -> Option<Vec<Service>> {
    let mut base = systemctl(scope);
    base.extend(["list-units", "--type=service", "--all", "--no-pager", "--plain"].map(String::from));
    let (code, out) = run(&[base.clone(), vec!["--output=json".into()]].concat());
    if code == 0 && out.trim_start().starts_with('[') {
        if let Ok(s) = parse_units_json(&out, scope) {
            return Some(s);
        }
    }
    let (code, out) = run(&[base, vec!["--no-legend".into()]].concat());
    (code == 0).then(|| parse_units_text(&out, scope))
}

/// start / stop / restart. System units ask through polkit (systemd's
/// own); if that is refused and pkexec exists, pkexec tries once more.
pub fn service_action(unit: &str, action: &str, scope: &str, run: &dyn Fn(&[String]) -> (i32, String)) -> (bool, String) {
    let mut cmd = systemctl(scope);
    cmd.extend([action.to_string(), unit.to_string()]);
    let (mut code, mut out) = run(&cmd);
    if code != 0 && scope == "system" && sonata_backend::procfs::which("pkexec").is_some() {
        (code, out) = run(&["pkexec", "systemctl", action, unit].map(String::from));
    }
    (code == 0, out.trim().to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fake(pid: i64, ppid: i64, exe: &str, uid: i64) -> Proc {
        Proc { pid, ppid, exe: exe.into(), name: if exe.is_empty() { format!("k{pid}") } else { exe.into() }, uid, user: "me".into(), ..Default::default() }
    }

    #[test]
    fn processes_grouped_like_task_manager() {
        let procs: BTreeMap<i64, Proc> = [
            fake(1, 0, "systemd", 0),
            fake(2, 0, "", 1000), // kernel thread
            fake(10, 1, "firefox", 1000),
            fake(11, 10, "firefox", 1000),
            fake(12, 11, "helper", 1000),
            fake(20, 1, "firefox", 1000), // a second window process: same app
            fake(30, 1, "syncthing", 1000), // the user's daemon: background
            fake(40, 1, "sshd", 0), // a system account's: system
            fake(50, 1, "gedit", 12345), // another user's app: not "Apps"
        ]
        .into_iter()
        .map(|p| (p.pid, p))
        .collect();
        let (apps, background, system) = group_processes(&procs, |p| ["firefox", "gedit"].contains(&p.exe.as_str()).then(|| p.exe.clone()), 1000);
        assert_eq!(apps.keys().collect::<Vec<_>>(), ["firefox"]);
        let mut f = apps["firefox"].clone();
        assert_eq!(f[0], 10);
        f.sort();
        assert_eq!(f, [10, 11, 12, 20]); // helpers belong to their app
        assert_eq!(background, [30, 50]);
        assert_eq!(system, [1, 2, 40]);
    }

    #[test]
    fn users_and_history() {
        let mk = |pid, cpu, rss, user: &str, uid, read_ps, write_ps| Proc { pid, cpu, rss, user: user.into(), uid, read_ps, write_ps, ..Default::default() };
        let procs = BTreeMap::from([(1, mk(1, 10.0, 100, "root", 0, 0.0, 0.0)), (2, mk(2, 5.0, 50, "vini", 1000, 10.0, 0.0)), (3, mk(3, 1.0, 25, "vini", 1000, 0.0, 5.0))]);
        let u = per_user(&procs);
        assert_eq!((u["vini"].procs, u["vini"].cpu, u["vini"].mem, u["vini"].disk), (2, 6.0, 75, 15.0));
        assert_eq!(u["root"].cpu, 10.0);
        let snap = Snapshot { interval: 2.0, procs: BTreeMap::from([(10, mk(10, 50.0, 0, "", 0, 100.0, 0.0)), (11, mk(11, 25.0, 0, "", 0, 0.0, 50.0))]), ..Default::default() };
        let mut h = History::from_value(&Map::new());
        let names = HashMap::from([("firefox".to_string(), "Firefox".to_string())]);
        h.add(&snap, &BTreeMap::from([("firefox".to_string(), vec![10, 11])]), &names);
        h.add(&snap, &BTreeMap::from([("firefox".to_string(), vec![10, 11, 99])]), &names); // 99 already gone
        let a = &h.apps["firefox"];
        assert_eq!(a.name, "Firefox");
        assert!((a.cpu_time - 2.0 * 0.75 * 2.0).abs() < 1e-9); // 75 % of a core for 2 s, twice
        assert!((a.disk - 2.0 * 150.0 * 2.0).abs() < 1e-9);
        let again = History::from_value(h.to_value().as_object().unwrap()); // persisted and read back
        assert_eq!((again.apps["firefox"].cpu_time, again.since), (a.cpu_time, h.since));
        let mut again = again;
        again.clear();
        assert!(again.apps.is_empty());
    }

    /// activity-history.json written by the Python Task Manager: an app's
    /// other keys survive the Rust one saving it, and an entry never
    /// sampled gets no "last" (the Rust one added "last": 0 and dropped
    /// the rest).
    #[test]
    fn test_history_file_kept_as_python_wrote_it() {
        let text = r#"{"since": 1700000000.5, "apps": {"firefox": {"name": "Firefox", "cpu_time": 12.5, "disk": 300.0, "pinned": true}}}"#;
        let v: Value = serde_json::from_str(text).unwrap();
        let h = History::from_value(v.as_object().unwrap());
        assert_eq!(h.since, 1700000000.5);
        let out = h.to_value();
        assert_eq!(out["apps"]["firefox"], json!({"name": "Firefox", "cpu_time": 12.5, "disk": 300.0, "pinned": true}));
        let mut h = h;
        let snap = Snapshot { interval: 2.0, procs: BTreeMap::from([(1, Proc { pid: 1, cpu: 50.0, ..Default::default() })]), ..Default::default() };
        h.add(&snap, &BTreeMap::from([("firefox".to_string(), vec![1])]), &HashMap::new());
        let out = h.to_value();
        assert!(out["apps"]["firefox"]["last"].as_f64().is_some_and(|t| t > 0.0));
        assert_eq!(out["apps"]["firefox"]["pinned"], json!(true));
        assert_eq!(out["apps"]["firefox"]["name"], json!("Firefox")); // no new name: the old one stays
    }

    #[test]
    fn startup_items_enabled_the_xdg_way() {
        let d = tempfile::tempdir().unwrap();
        let (user, sys1) = (d.path().join("user"), d.path().join("sys"));
        std::fs::create_dir_all(&user).unwrap();
        std::fs::create_dir_all(&sys1).unwrap();
        let desktop = |dir: &Path, f: &str, name: &str, extra: &str| std::fs::write(dir.join(f), format!("[Desktop Entry]\nType=Application\nName={name}\nExec={}\n{extra}[Desktop Action x]\nName=Other\n", name.to_lowercase())).unwrap();
        desktop(&sys1, "a.desktop", "Alpha", "");
        desktop(&sys1, "b.desktop", "Beta", "X-GNOME-Autostart-enabled=false\n");
        desktop(&sys1, "c.desktop", "Gamma", "");
        desktop(&user, "c.desktop", "Gamma Mine", "Hidden=true\n"); // the user's copy wins
        let sys = [sys1.clone()];
        let entries = startup_entries(Some(&user), Some(&sys));
        assert_eq!(entries.iter().map(|e| (e.name.as_str(), e.enabled, e.user)).collect::<Vec<_>>(), [("Alpha", true, false), ("Beta", false, false), ("Gamma Mine", false, true)]);
        let (mut alpha, mut beta, mut gamma) = (entries[0].clone(), entries[1].clone(), entries[2].clone());
        // disabling a system entry: a user copy with Hidden=true; the system file stays
        let path = set_startup_enabled(&mut alpha, false, Some(&user)).unwrap();
        assert_eq!(path, user.join("a.desktop"));
        assert!(!std::fs::read_to_string(sys1.join("a.desktop")).unwrap().contains("Hidden"));
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.split("[Desktop Action x]").next().unwrap().contains("Hidden=true"));
        assert!(text.contains("[Desktop Action x]"));
        assert!(!startup_entries(Some(&user), Some(&sys))[0].enabled);
        // enabling removes Hidden (and a false X-GNOME-Autostart-enabled)
        set_startup_enabled(&mut gamma, true, Some(&user)).unwrap();
        set_startup_enabled(&mut beta, true, Some(&user)).unwrap();
        let states: HashMap<String, bool> = startup_entries(Some(&user), Some(&sys)).into_iter().map(|e| (e.name, e.enabled)).collect();
        assert_eq!(states, HashMap::from([("Alpha".into(), false), ("Beta".into(), true), ("Gamma Mine".into(), true)]));
    }

    /// systemctl's plain listing (failed units carry a ● marker) parses;
    /// the status words map like Windows'.
    #[test]
    fn units_text_and_status() {
        let text = "● bad.service loaded failed failed The Broken One\n  ok.service  loaded active running OK daemon with spaces\n  sock.socket loaded active listening Not a service\n";
        let got = parse_units_text(text, "system");
        assert_eq!(got.iter().map(|s| (s.unit.as_str(), s.status(), s.description.as_str())).collect::<Vec<_>>(), [("bad.service", "Failed", "The Broken One"), ("ok.service", "Running", "OK daemon with spaces")]);
        let svc = |active: &str, sub: &str| Service { unit: "x".into(), description: String::new(), load: String::new(), active: active.into(), sub: sub.into(), scope: "user".into() };
        assert_eq!(svc("active", "exited").status(), "Active");
        assert_eq!(svc("reloading", "").status(), "Reloading");
        assert_eq!(svc("inactive", "dead").status(), "Stopped");
    }

    /// A refused system unit retries through pkexec; a user unit never escalates.
    #[test]
    fn service_action_pkexec_only_for_system() {
        let calls = std::cell::RefCell::new(Vec::<Vec<String>>::new());
        let run = |cmd: &[String]| {
            calls.borrow_mut().push(cmd.to_vec());
            if cmd[0] == "pkexec" { (0, String::new()) } else { (1, "denied".to_string()) }
        };
        let (ok, msg) = service_action("b.service", "stop", "user", &run);
        assert_eq!((ok, msg.as_str()), (false, "denied"));
        assert_eq!(*calls.borrow(), [["systemctl", "--user", "stop", "b.service"]]);
    }

    /// System units go through polkit / pkexec: their password dialog gets
    /// minutes, not 20 s (it was killed mid-typing).
    #[test]
    fn password_prompt_not_killed_after_20s() {
        assert_eq!(action_timeout("system"), AUTH_TIMEOUT);
        assert!(AUTH_TIMEOUT >= 120);
        assert_eq!(action_timeout("user"), 20); // no password: the short timeout
    }

    /// Disabling twice writes one Hidden=true; enabling removes it only
    /// from [Desktop Entry].
    #[test]
    fn set_keys_idempotent_and_section_scoped() {
        let text = "[Desktop Entry]\nName=A\nExec=a\n\n[Desktop Action x]\nHidden=true\nExec=b\n";
        let off = set_keys(&set_keys(text, &[("Hidden", Some("true"))]), &[("Hidden", Some("true"))]);
        assert_eq!(off.matches("Hidden=true").count(), 2); // its own + the action's
        assert_eq!(desktop_section(&off).get("Hidden").map(String::as_str), Some("true"));
        let on = set_keys(&off, &[("Hidden", None)]);
        assert!(!desktop_section(&on).contains_key("Hidden"));
        assert!(on.contains("[Desktop Action x]\nHidden=true"));
    }

    /// Kernel threads and system accounts are System; another user's and
    /// helper processes are Background; an app's children stay under it.
    #[test]
    fn group_processes_kinds() {
        let p = |pid, ppid, exe: &str, uid| Proc { pid, ppid, exe: exe.into(), name: exe.into(), uid, ..Default::default() };
        let procs: BTreeMap<i64, Proc> = [p(1, 0, "systemd", 0), p(2, 0, "", 0), p(10, 1, "firefox", 1000), p(11, 10, "helper", 1000), p(20, 1, "bash", 1001), p(21, 1, "daemon", 1000)].into_iter().map(|p| (p.pid, p)).collect();
        let (apps, background, system) = group_processes(&procs, |p| (p.exe == "firefox").then(|| "firefox".to_string()), 1000);
        assert_eq!(apps, BTreeMap::from([("firefox".to_string(), vec![10, 11])]));
        assert_eq!(background, [20, 21]);
        assert_eq!(system, [1, 2]);
    }

    #[test]
    fn services() {
        let js = r#"[{"unit":"ssh.service","load":"loaded","active":"active","sub":"running","description":"SSH"},{"unit":"dev-sda.device","load":"loaded","active":"active","sub":"plugged","description":"x"},{"unit":"cups.service","load":"loaded","active":"failed","sub":"failed","description":"CUPS"}]"#;
        let svc = parse_units_json(js, "system").unwrap();
        assert_eq!(svc.iter().map(|s| (s.unit.as_str(), s.status())).collect::<Vec<_>>(), [("ssh.service", "Running"), ("cups.service", "Failed")]);
        let text = "ssh.service loaded active running OpenBSD Secure Shell server\n● bad.service not-found inactive dead bad.service\n";
        let svc = parse_units_text(text, "user");
        assert_eq!(svc.iter().map(|s| (s.unit.as_str(), s.status(), s.description.as_str())).collect::<Vec<_>>(), [("ssh.service", "Running", "OpenBSD Secure Shell server"), ("bad.service", "Stopped", "bad.service")]);
        let calls = std::cell::RefCell::new(Vec::<Vec<String>>::new());
        let run = |cmd: &[String]| {
            calls.borrow_mut().push(cmd.to_vec());
            if cmd.iter().any(|c| c == "--output=json") {
                return (1, "unknown option".to_string()); // an old systemctl: plain text instead
            }
            if cmd[0] == "pkexec" {
                return (0, String::new());
            }
            if cmd.get(1).map(String::as_str) == Some("restart") {
                return (1, "Access denied".into());
            }
            (0, "ssh.service loaded inactive dead SSH\n".into())
        };
        let listed = list_services("user", &run).unwrap();
        assert_eq!(listed.iter().map(|s| s.unit.as_str()).collect::<Vec<_>>(), ["ssh.service"]);
        assert_eq!(calls.borrow()[0][..2], ["systemctl", "--user"]);
        let (ok, _) = service_action("ssh.service", "restart", "system", &run);
        assert!(calls.borrow().iter().any(|c| c == &["systemctl", "restart", "ssh.service"]));
        if sonata_backend::procfs::which("pkexec").is_some() {
            assert!(ok);
            assert_eq!(calls.borrow().last().unwrap(), &["pkexec", "systemctl", "restart", "ssh.service"]);
        }
        assert!(list_services("user", &|_| (1, "no bus".into())).is_none());
    }
}
