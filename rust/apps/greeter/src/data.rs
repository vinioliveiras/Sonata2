//! What the login screen knows before drawing anything (greeter.py's data
//! part, and monitors.py's login_main / login_off_after): the sessions it
//! offers, its own state, the wallpapers and displays its users shared,
//! and the displays' modes through wlr-randr.

use serde_json::{json, Map, Value};
use sonata_shell::logins;
use std::path::{Path, PathBuf};

pub const STATE: &str = "/var/cache/sonata-greeter/state.json";
/// waits after wrong passwords, per user
pub const WAITS: &str = "/var/cache/sonata-greeter/password-waits.json";
/// per user, writable by them (install.sh --greeter): what their session shares
pub const WALLPAPERS: &str = "/var/lib/sonata-greeter";
/// "Turn display off after" when nobody shared theirs (displaysleep.py)
pub const OFF_AFTER_DEFAULT: i64 = 600;

/// a user's picture, its button's padding, the gap
pub const USER_TILE: i32 = 96 + 2 * 8 + 18;

/// (users per row, picture size) for n users on a display screen_w wide.
pub fn user_grid(n: usize, screen_w: i32) -> (usize, i32) {
    let per_row = ((screen_w as f64 * 0.8) as i32 / USER_TILE).max(0) as usize;
    let per_row = n.max(1).min(per_row).max(1);
    let rows = n.max(1).div_ceil(per_row);
    (per_row, if rows <= 2 { 96 } else { 72 })
}

#[derive(Clone, Debug, PartialEq)]
pub struct Session {
    /// the file name without ".desktop"
    pub key: String,
    pub name: String,
    pub cmd: Vec<String>,
    /// DesktopNames, ':'-separated (XDG_CURRENT_DESKTOP)
    pub desktops: String,
}

/// The Wayland sessions installed: Sonata first, then by name. (Which
/// files count is logins::session_keys' rule; this reads what the login
/// needs from each.)
pub fn sessions_in(dirs: &[&str]) -> Vec<Session> {
    let mut out = Vec::new();
    for key in logins::session_keys(dirs) {
        let Some(file) = dirs.iter().map(|d| Path::new(d).join(format!("{key}.desktop"))).find(|p| p.is_file()) else { continue };
        let kf = glib::KeyFile::new();
        if kf.load_from_file(&file, glib::KeyFileFlags::NONE).is_err() {
            continue;
        }
        let Ok(name) = kf.locale_string("Desktop Entry", "Name", None) else { continue };
        let Ok(exec) = kf.string("Desktop Entry", "Exec") else { continue };
        let Ok(argv) = glib::shell_parse_argv(exec.as_str()) else { continue };
        // field codes (%f, %U...) mean nothing to a session
        let cmd = argv.iter().map(|a| a.to_string_lossy().into_owned()).filter(|a| !(a.chars().count() == 2 && a.starts_with('%'))).collect();
        let desktops = kf.string("Desktop Entry", "DesktopNames").map(|s| s.trim_matches(';').replace(';', ":")).unwrap_or_default();
        out.push(Session { key, name: name.into(), cmd, desktops });
    }
    out.sort_by_key(|s| (s.key != "sonata", s.name.to_lowercase()));
    out
}

pub fn sessions() -> Vec<Session> {
    sessions_in(&logins::SESSION_DIRS)
}

pub fn load_state(path: &Path) -> Map<String, Value> {
    std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default()
}

pub fn save_state(path: &Path, state: &Map<String, Value>) {
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let text = sonata_config::to_python_json(&Value::Object(state.clone()));
    let _ = sonata_config::atomic_write(path, text.as_bytes(), false);
}

/// The session `user` picked last time, else the first.
pub fn chosen_session<'a>(state: &Map<String, Value>, sessions: &'a [Session], user: Option<&str>) -> Option<&'a Session> {
    let key = user.and_then(|u| state.get("sessions")?.get(u)?.as_str());
    sessions.iter().find(|s| Some(s.key.as_str()) == key).or(sessions.first())
}

pub fn remember_session(state: &mut Map<String, Value>, user: &str, key: &str) {
    let entry = state.entry("sessions").or_insert_with(|| json!({}));
    if !entry.is_object() {
        *entry = json!({});
    }
    entry.as_object_mut().expect("an object").insert(user.into(), json!(key));
}

fn shared_file(root: &Path, name: &str, file: &str) -> Option<PathBuf> {
    let p = root.join(name).join(file);
    p.is_file().then_some(p)
}

/// The picture a user's Sonata session shared (wallpaper.py).
pub fn user_wallpaper_in(root: &Path, name: &str) -> Option<PathBuf> {
    shared_file(root, name, "wallpaper")
}

/// Their video wallpaper, shared beside the picture.
pub fn user_video_in(root: &Path, name: &str) -> Option<PathBuf> {
    shared_file(root, name, "wallpaper-video")
}

/// `key` from what the first of `users` shared (monitors.share_with_login_screen).
fn login_shared(root: &Path, users: &[String], key: &str, ok: impl Fn(&Value) -> bool) -> Option<Value> {
    users.iter().find_map(|name| {
        let text = std::fs::read_to_string(root.join(name).join("displays.json")).ok()?;
        let v = serde_json::from_str::<Value>(&text).ok()?.get(key)?.clone();
        ok(&v).then_some(v)
    })
}

/// The main display a user chose (the first of `users` that has one).
pub fn login_main_in(root: &Path, users: &[String]) -> String {
    login_shared(root, users, "main", |v| v.as_str().is_some_and(|s| !s.is_empty())).and_then(|v| v.as_str().map(String::from)).unwrap_or_default()
}

/// Seconds before the login screen goes dark: what the user chose in
/// Settings (<= 0 never), else Sonata's default.
pub fn login_off_after_in(root: &Path, users: &[String]) -> i64 {
    // (a number, never true / false)
    login_shared(root, users, "off_after", |v| v.is_i64()).and_then(|v| v.as_i64()).unwrap_or(OFF_AFTER_DEFAULT)
}

// -- displays (wlr-randr: Wayfire's output management) --------------------------------------------
/// (width, height, refresh Hz)
pub type Mode = (i64, i64, f64);

#[derive(Clone, Debug, PartialEq)]
pub struct Output {
    pub name: String,
    pub description: String,
    pub modes: Vec<Mode>,
    pub current: Option<Mode>,
}

/// The enabled displays in `wlr-randr --json`'s answer.
pub fn parse_outputs(text: &str) -> Vec<Output> {
    let Ok(Value::Array(list)) = serde_json::from_str::<Value>(text) else { return Vec::new() };
    let mode = |m: &Value| -> Option<Mode> { Some((m.get("width")?.as_i64()?, m.get("height")?.as_i64()?, m.get("refresh")?.as_f64()?)) };
    list.iter()
        .filter(|o| o.get("enabled").and_then(Value::as_bool).unwrap_or(true))
        .map(|o| {
            let modes = o.get("modes").and_then(Value::as_array).cloned().unwrap_or_default();
            let name = o.get("name").and_then(Value::as_str).unwrap_or("").to_string();
            let description = o.get("description").and_then(Value::as_str).filter(|s| !s.is_empty()).map(String::from).unwrap_or_else(|| name.clone());
            let current = modes.iter().filter(|m| m.get("current").and_then(Value::as_bool).unwrap_or(false)).find_map(mode);
            Output { name, description, modes: modes.iter().filter_map(mode).collect(), current }
        })
        .collect()
}

pub fn outputs() -> Vec<Output> {
    let (rc, out) = sonata_backend::run::run(&["wlr-randr", "--json"], 4);
    if rc != 0 {
        return Vec::new();
    }
    parse_outputs(&out)
}

/// One entry per resolution, at its highest refresh rate; biggest first.
pub fn best_modes(modes: &[Mode]) -> Vec<Mode> {
    let mut best: Vec<Mode> = Vec::new();
    for &(w, h, hz) in modes {
        match best.iter_mut().find(|m| (m.0, m.1) == (w, h)) {
            Some(m) if hz > m.2 => m.2 = hz,
            Some(_) => {}
            None => best.push((w, h, hz)),
        }
    }
    best.sort_by(|a, b| (b.0 * b.1).cmp(&(a.0 * a.1)).then(b.2.total_cmp(&a.2)));
    best
}

fn round3(hz: f64) -> f64 {
    (hz * 1000.0).round() / 1000.0
}

/// A display's mode as the state file keeps it: [w, h, Hz to 3 places].
pub fn mode_json(m: Mode) -> Value {
    json!([m.0, m.1, round3(m.2)])
}

/// The mode a display should come up in: the one saved for it, else its
/// resolution's highest refresh rate. None: leave it as it is.
pub fn wanted_mode(o: &Output, saved: Option<&Value>) -> Option<Mode> {
    let want = saved.and_then(Value::as_array).and_then(|a| Some((a.first()?.as_i64()?, a.get(1)?.as_i64()?, a.get(2)?.as_f64()?)));
    let mode = match want.and_then(|(w, h, hz)| o.modes.iter().find(|m| (m.0, m.1) == (w, h) && round3(m.2) == hz)) {
        Some(m) => Some(*m),
        None => {
            let same: Vec<Mode> = o.modes.iter().filter(|m| o.current.is_some_and(|c| (m.0, m.1) == (c.0, c.1))).copied().collect();
            let pool = if same.is_empty() { &o.modes } else { &same };
            pool.iter().copied().reduce(|a, b| if b.2 > a.2 { b } else { a })
        }
    };
    mode.filter(|m| Some(*m) != o.current)
}

pub fn set_mode(name: &str, m: Mode) -> bool {
    let spec = format!("{}x{}@{:.3}Hz", m.0, m.1, m.2);
    sonata_backend::run::run(&["wlr-randr", "--output", name, "--mode", &spec], 6).0 == 0
}

/// "1920 × 1080  ·  60 Hz" -- the rate rounded as greeter.py's round()
/// does (half to even), so both login screens name a mode alike.
pub fn mode_label(m: Mode) -> String {
    format!("{} \u{00d7} {}  \u{00b7}  {} Hz", m.0, m.1, m.2.round_ties_even() as i64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn many_users_fit_in_rows() {
        // (tests/test_responsive.py)
        assert_eq!(user_grid(3, 1920), (3, 96));
        let (per_row, _px) = user_grid(12, 1280);
        assert!(per_row * USER_TILE as usize <= 1280);
        assert_eq!(user_grid(30, 1280).1, 72);
        assert_eq!(user_grid(0, 1920), (1, 96));
        assert_eq!(user_grid(4, 100).0, 1); // never zero per row
    }

    fn entry(d: &Path, key: &str, name: &str, exec: &str, extra: &str) {
        std::fs::write(d.join(format!("{key}.desktop")), format!("[Desktop Entry]\nName={name}\nExec={exec}\nType=Application\n{extra}")).unwrap();
    }

    #[test]
    fn plain_wayfire_hidden() {
        // Vini: plain Wayfire (no Sonata) showed in the session list after every install
        let d = tempfile::tempdir().unwrap();
        entry(d.path(), "sonata", "Sonata", "/usr/local/bin/sonata-login", "DesktopNames=Sonata;wlroots;\n");
        entry(d.path(), "wayfire", "Wayfire", "wayfire", "");
        entry(d.path(), "wayfire-uwsm", "Wayfire (UWSM)", "uwsm start wayfire", "");
        entry(d.path(), "plasma", "Plasma (Wayland)", "startplasma-wayland %U", "");
        let got = sessions_in(&[d.path().to_str().unwrap()]);
        assert_eq!(got.iter().map(|s| s.key.as_str()).collect::<Vec<_>>(), ["sonata", "plasma"]);
        assert_eq!(got[0].desktops, "Sonata:wlroots");
        assert_eq!(got[1].cmd, ["startplasma-wayland"]); // no field codes
    }

    #[test]
    fn each_users_session_is_remembered() {
        let s = |k: &str| Session { key: k.into(), name: k.into(), cmd: vec![], desktops: String::new() };
        let all = [s("sonata"), s("plasma")];
        let mut state = Map::new();
        assert_eq!(chosen_session(&state, &all, Some("vini")).unwrap().key, "sonata");
        remember_session(&mut state, "vini", "plasma");
        assert_eq!(chosen_session(&state, &all, Some("vini")).unwrap().key, "plasma");
        assert_eq!(chosen_session(&state, &all, Some("ana")).unwrap().key, "sonata");
        assert!(chosen_session(&state, &[], Some("vini")).is_none());
    }

    #[test]
    fn the_state_file_round_trips() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("cache").join("state.json");
        assert!(load_state(&p).is_empty());
        let mut st = Map::new();
        st.insert("user".into(), json!("vini"));
        save_state(&p, &st);
        assert_eq!(load_state(&p).get("user"), Some(&json!("vini")));
        std::fs::write(&p, "not json").unwrap();
        assert!(load_state(&p).is_empty());
    }

    #[test]
    fn the_session_shares_its_display_and_time() {
        // (tests/test_greeter_sleep.py: SharedTimeTest)
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(root.path().join("vini")).unwrap();
        std::fs::write(root.path().join("vini").join("displays.json"), r#"{"main": "HDMI-A-1", "off_after": 300}"#).unwrap();
        let users = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(login_off_after_in(root.path(), &users(&["nobody", "vini"])), 300);
        assert_eq!(login_off_after_in(root.path(), &users(&["nobody"])), OFF_AFTER_DEFAULT);
        assert_eq!(login_main_in(root.path(), &users(&["vini"])), "HDMI-A-1");
        assert_eq!(login_main_in(root.path(), &users(&["nobody"])), "");
        std::fs::write(root.path().join("vini").join("displays.json"), r#"{"off_after": -1}"#).unwrap();
        assert_eq!(login_off_after_in(root.path(), &users(&["vini"])), -1); // never
        std::fs::write(root.path().join("vini").join("displays.json"), r#"{"off_after": true}"#).unwrap();
        assert_eq!(login_off_after_in(root.path(), &users(&["vini"])), OFF_AFTER_DEFAULT); // a bad value
    }

    #[test]
    fn shared_wallpapers() {
        let root = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(root.path().join("vini")).unwrap();
        std::fs::write(root.path().join("vini").join("wallpaper"), b"x").unwrap();
        assert!(user_wallpaper_in(root.path(), "vini").is_some());
        assert!(user_video_in(root.path(), "vini").is_none());
        assert!(user_wallpaper_in(root.path(), "ana").is_none());
    }

    const RANDR: &str = r#"[
      {"name": "eDP-1", "description": "Built-in", "enabled": true, "modes": [
        {"width": 1920, "height": 1080, "refresh": 60.0, "current": true},
        {"width": 1920, "height": 1080, "refresh": 144.002},
        {"width": 1280, "height": 720, "refresh": 60.0},
        {"width": 2560, "height": 1440, "refresh": 59.951}]},
      {"name": "HDMI-A-1", "enabled": false, "modes": []}
    ]"#;

    #[test]
    fn displays_and_their_modes() {
        let outs = parse_outputs(RANDR);
        assert_eq!(outs.len(), 1); // the disabled one left out
        let o = &outs[0];
        assert_eq!((o.name.as_str(), o.description.as_str()), ("eDP-1", "Built-in"));
        assert_eq!(o.current, Some((1920, 1080, 60.0)));
        assert_eq!(best_modes(&o.modes), [(2560, 1440, 59.951), (1920, 1080, 144.002), (1280, 720, 60.0)]);
        assert!(parse_outputs("garbage").is_empty());
        assert_eq!(mode_label((1920, 1080, 59.951)), "1920 \u{00d7} 1080  \u{00b7}  60 Hz");
    }

    /// The Displays menu named a 74.5 Hz mode "75 Hz" where the Python
    /// login screen (round(): half to even) says "74 Hz".
    #[test]
    fn test_refresh_rate_rounded_unlike_python() {
        assert_eq!(mode_label((1920, 1080, 74.5)), "1920 \u{00d7} 1080  \u{00b7}  74 Hz");
        assert_eq!(mode_label((1920, 1080, 75.5)), "1920 \u{00d7} 1080  \u{00b7}  76 Hz");
    }

    #[test]
    fn displays_come_up_at_their_saved_mode_else_the_fastest() {
        let o = &parse_outputs(RANDR)[0];
        assert_eq!(wanted_mode(o, None), Some((1920, 1080, 144.002))); // same resolution, highest Hz
        assert_eq!(wanted_mode(o, Some(&mode_json((2560, 1440, 59.951)))), Some((2560, 1440, 59.951)));
        assert_eq!(wanted_mode(o, Some(&json!([1920, 1080, 60.0]))), None); // already so
        assert_eq!(wanted_mode(o, Some(&json!([800, 600, 60.0]))), Some((1920, 1080, 144.002))); // gone
    }
}
