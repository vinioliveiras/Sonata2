//! Wayfire options Sonata changes (sonata2/wfconfig.py): keyboard,
//! trackpad, displays... Sonata's overrides file (kept apart from the
//! installed wayfire.ini; tools/wayfire-config.sh merges it in at login)
//! and, while a session runs, the resolved copy Wayfire reads and reloads
//! live.

use std::os::fd::AsRawFd;
use std::path::PathBuf;
use std::sync::Mutex;

static LOCK: Mutex<()> = Mutex::new(());

fn runtime() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

fn cfg_dir() -> PathBuf {
    sonata_config::config_home().join("sonata2")
}

/// Where Settings writes: the overrides, and the running session's copy.
pub fn write_files() -> Vec<PathBuf> {
    let mut files = vec![cfg_dir().join("wayfire-overrides.ini")];
    let run = runtime().join("sonata2-wayfire.ini");
    if run.exists() {
        files.push(run);
    }
    files
}

pub fn read_files() -> Vec<PathBuf> {
    let mut f = vec![cfg_dir().join("wayfire.ini")];
    f.extend(write_files());
    f
}

/// One writer at a time: threads and Sonata's processes (a flock beside the session copy).
fn locked<R>(f: impl FnOnce() -> R) -> R {
    let _g = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    let lock = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false).open(runtime().join("sonata2-wayfire.lock"));
    if let Ok(l) = &lock {
        unsafe {
            libc::flock(l.as_raw_fd(), libc::LOCK_EX);
        }
    }
    f()
}

pub fn get(section: &str, key: &str, default: &str) -> String {
    for path in read_files().iter().rev() {
        let Ok(text) = std::fs::read_to_string(path) else { continue };
        let mut cur: Option<&str> = None;
        for line in text.lines() {
            let s = line.trim();
            if s.starts_with('[') && s.ends_with(']') {
                cur = Some(&s[1..s.len() - 1]);
            } else if cur == Some(section) && s.contains('=') && !s.starts_with('#') {
                let (k, v) = s.split_once('=').unwrap_or_default();
                if k.trim() == key {
                    return v.trim().to_string();
                }
            }
        }
    }
    default.to_string()
}

/// The screenshot shortcuts are Wayfire's "always" bindings (Vini: they did
/// nothing in Mission Control): the command plugin refuses its binding_
/// keys while a plugin holding the input (scale, expo) is open, its
/// always_binding_ ones run regardless. Their command_ keys stay.
pub const ALWAYS_BINDINGS: [&str; 4] = ["screenshot", "screenshot_area", "capture", "capture_print"];

/// The name a key has now: binding_screenshot (a config kept from before)
/// is always_binding_screenshot.
pub fn current_key(section: &str, key: &str) -> String {
    match key.strip_prefix("binding_") {
        Some(name) if section == "command" && ALWAYS_BINDINGS.contains(&name) => format!("always_{key}"),
        _ => key.to_string(),
    }
}

/// The name a kept config gives `key` (always_binding_screenshot ->
/// binding_screenshot), if it has one.
fn old_key<'a>(section: &str, key: &'a str) -> Option<&'a str> {
    let name = key.strip_prefix("always_binding_")?;
    (section == "command" && ALWAYS_BINDINGS.contains(&name)).then(|| &key["always_".len()..])
}

/// `text` with `key = value` set in [section] (None removes it); comments
/// and other lines stay as they are.
pub fn set_in(text: &str, section: &str, key: &str, value: Option<&str>) -> String {
    let old = old_key(section, key); // never both names: Wayfire would run the shortcut twice
    let lines: Vec<String> = text.split_inclusive('\n').map(String::from).collect();
    let mut out: Vec<String> = Vec::new();
    let (mut cur, mut done, mut sec_end): (Option<String>, bool, Option<usize>) = (None, false, None);
    for line in &lines {
        let s = py_strip(line);
        if s.starts_with('[') && s.ends_with(']') {
            if cur.as_deref() == Some(section) && !done {
                sec_end = Some(out.len());
            }
            cur = Some(s[1..s.len() - 1].to_string());
        } else if cur.as_deref() == Some(section) && s.contains('=') && !s.starts_with('#') && s.split_once('=').map(|x| py_strip(x.0)) == Some(key) {
            if let Some(v) = value {
                out.push(format!("{key} = {v}\n"));
            }
            done = true;
            continue;
        } else if old.is_some() && cur.as_deref() == Some(section) && s.contains('=') && !s.starts_with('#') && s.split_once('=').map(|x| py_strip(x.0)) == old {
            continue;
        }
        out.push(line.clone());
    }
    if !done {
        if let Some(v) = value {
            if let Some(last) = out.last_mut() {
                if !last.ends_with('\n') {
                    last.push('\n'); // never glue the new key onto that line
                }
            }
            if cur.as_deref() == Some(section) {
                sec_end = Some(out.len());
            }
            match sec_end {
                Some(mut at) => {
                    while at > 0 && py_strip(&out[at - 1]).is_empty() {
                        at -= 1; // before the blank line
                    }
                    out.insert(at, format!("{key} = {v}\n"));
                }
                None => {
                    out.push(format!("\n[{section}]\n"));
                    out.push(format!("{key} = {v}\n"));
                }
            }
        }
    }
    out.concat()
}

fn set_files(section: &str, key: &str, value: Option<&str>, files: &[PathBuf]) -> bool {
    let mut ok = false;
    for path in files {
        let text = std::fs::read_to_string(path).unwrap_or_default();
        let new = set_in(&text, section, key, value);
        if new == text {
            ok = true; // already so: no write, so Wayfire doesn't reload
            continue;
        }
        // whole new file renamed over the old: Wayfire never reloads a half-written one
        if sonata_config::atomic_write(path, new.as_bytes(), false).is_ok() {
            ok = true;
        }
    }
    ok
}

/// Set `key = value` in [section]; Wayfire applies it at once.
pub fn set(section: &str, key: &str, value: &str) -> bool {
    locked(|| set_files(section, key, Some(value), &write_files()))
}

/// For this session only (the resolved copy, never the overrides); None removes the key.
pub fn runtime_set(section: &str, key: &str, value: Option<&str>) -> bool {
    let run = runtime().join("sonata2-wayfire.ini");
    if !run.exists() {
        return false;
    }
    locked(|| set_files(section, key, value, &[run]))
}

/// [section] and its keys out of the session's Wayfire config(s).
pub fn remove_section(section: &str) -> bool {
    locked(|| {
        let mut ok = false;
        for path in write_files() {
            let Ok(text) = std::fs::read_to_string(&path) else { continue };
            let lines: Vec<&str> = text.split_inclusive('\n').collect();
            let mut out: Vec<&str> = Vec::new();
            let mut skip = false;
            for line in &lines {
                let s = line.trim();
                if s.starts_with('[') && s.ends_with(']') {
                    skip = &s[1..s.len() - 1] == section;
                }
                if !skip {
                    out.push(line);
                }
            }
            while out.len() > 1 && out[out.len() - 1].trim().is_empty() && out[out.len() - 2].trim().is_empty() {
                out.pop();
            }
            if out == lines {
                ok = true;
                continue;
            }
            ok |= sonata_config::atomic_write(&path, out.concat().as_bytes(), false).is_ok();
        }
        ok
    })
}

// -- the login's steps (tools/wayfire-config.sh) -------------------------------------------
// wayfire-config.sh builds the session's copy at every login; its two steps
// that were Python run here (`sonata2-rs wayfire-config`). They give the
// same bytes the Python gave for the same files (the golden test's recorded
// cases): Python's whitespace, line splitting and rounding are copied, not
// Rust's own.

/// str.isspace(): Rust's whitespace plus the separators \x1c-\x1f, without
/// the bidi marks U+200E/U+200F.
fn py_space(c: char) -> bool {
    matches!(c, '\x1c'..='\x1f') || (c.is_whitespace() && !matches!(c, '\u{200e}' | '\u{200f}'))
}

/// str.strip()
pub fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// What a file opened in Python's text mode reads: "\r\n" and a lone "\r" are "\n".
pub fn universal_newlines(s: &str) -> String {
    s.replace("\r\n", "\n").replace('\r', "\n")
}

/// str.splitlines(keepends=True): every line break Python knows.
fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        let end = match c {
            '\r' if it.peek().map(|p| p.1) == Some('\n') => {
                it.next();
                i + 2
            }
            '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}' => i + c.len_utf8(),
            _ => continue,
        };
        out.push(&s[start..end]);
        start = end;
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// The buttons' place: always from the frame (the user's side), so a value
/// Settings saved earlier never wins at login (wfconfig.BUTTON_KEYS).
pub const BUTTON_KEYS: [&str; 6] = ["button_layout", "button_order", "left_button_spacing", "left_button_x_offset", "right_button_spacing", "right_button_x_offset"];

// tokens.FRAME's values the title bars Wayfire draws take (sonata-ui's
// tokens.json "frame"; a test keeps them equal). Here, not from sonata-ui:
// this crate has no GTK, and the login step must not start any.
const FRAME_RADIUS: i64 = 10;
const FRAME_DOT: i64 = 12;
const FRAME_DOT_GAP: i64 = 8;
const FRAME_DOT_LEFT: f64 = 13.0;
const FRAME_BUTTONS: [&str; 3] = ["close", "minimize", "maximize"];
const FRAME_SHADOW_RADIUS: i64 = 3;
const FRAME_SHADOW_COLOR: &str = "#0000001f";
const FRAME_TITLE_ALIGN: &str = "center";
const FRAME_TITLE_FONT: &str = "Inter Variable Semi-Bold 10";
const FRAME_FALLBACK_TITLE_H: i64 = 28;
const FRAME_FALLBACK_FONT: &str = "Inter Semi-Bold";
const RADIUS_RANGE: (i64, i64) = (0, 24);
const BUTTON_SIZE_RANGE: (i64, i64) = (10, 20);

/// tokens.frame() as the login reads it: the window radius, the buttons'
/// side, order and size from appearance.json.
#[derive(Clone, Debug, PartialEq)]
pub struct LoginFrame {
    pub radius: i64,
    pub buttons_side: String,
    /// from the window's edge inwards
    pub buttons: Vec<String>,
    pub dot: i64,
    pub dot_gap: i64,
    pub dot_left: f64,
}

/// Python's truth of a JSON value (`json.load(f) or {}`).
fn truthy(v: &serde_json::Value) -> bool {
    use serde_json::Value;
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// round() then clamped, as tokens.py does (half to even).
fn py_round_clamp(n: f64, (lo, hi): (i64, i64)) -> i64 {
    (n.round_ties_even() as i64).clamp(lo, hi)
}

/// tokens.frame() from appearance.json in `folder`, the way tokens.py reads
/// it -- including its quirks, so the title bars are the Python session's:
/// a radius of `true` is 1 (bool is an int there), and a "radius" that is
/// no object keeps the button size from being read.
pub fn login_frame(folder: &std::path::Path) -> LoginFrame {
    use serde_json::Value;
    let path = folder.join("appearance.json");
    let exists = std::fs::metadata(&path).is_ok();
    // json.load's ValueError (a bad file, bad UTF-8) and OSError: none
    let data: Option<Value> = std::fs::read(&path)
        .ok()
        .and_then(|b| String::from_utf8(b).ok())
        .and_then(|t| serde_json::from_str::<Value>(&t).ok())
        .map(|v| if truthy(&v) { v } else { Value::Object(Default::default()) });
    // (a list or number where the object goes: AttributeError, nothing read)
    let obj = data.as_ref().and_then(Value::as_object);
    let (mut radius, mut button) = (FRAME_RADIUS, None);
    if exists {
        let got = obj.map(|o| o.get("radius").filter(|r| truthy(r)).cloned().unwrap_or(Value::Object(Default::default())));
        if let Some(g) = got.as_ref().and_then(Value::as_object) {
            let num = |v: &Value| match v {
                Value::Bool(b) => Some(*b as i64 as f64),
                Value::Number(n) => n.as_f64(),
                _ => None,
            };
            if let Some(n) = g.get("window").and_then(num) {
                radius = py_round_clamp(n, RADIUS_RANGE);
            }
            // (only when "radius" was read: an AttributeError above skipped it)
            if let Some(n) = obj.and_then(|o| o.get("button_size")).and_then(|b| if b.is_number() { b.as_f64() } else { None }) {
                button = Some(py_round_clamp(n, BUTTON_SIZE_RANGE));
            }
        }
    }
    let side = match obj.and_then(|o| o.get("buttons_side")).and_then(Value::as_str) {
        Some(s @ ("left" | "right")) => s.to_string(),
        _ => "left".to_string(),
    };
    let default: Vec<String> = FRAME_BUTTONS.iter().map(|s| s.to_string()).collect();
    let buttons = match obj.and_then(|o| o.get("buttons_order")).and_then(Value::as_array) {
        Some(a) if a.len() == default.len() => {
            let order: Vec<String> = a.iter().filter_map(|v| v.as_str().map(String::from)).collect();
            let (mut x, mut y) = (order.clone(), default.clone());
            x.sort();
            y.sort();
            if order.len() == default.len() && x == y { order } else { default }
        }
        _ => default,
    };
    let size = if exists { button.unwrap_or(FRAME_DOT) } else { FRAME_DOT };
    // a bigger button grows inwards, a rounder corner moves them in (corner_extra)
    let mv = (((radius - FRAME_RADIUS) as f64) / 2.0).max(0.0) + (size - FRAME_DOT) as f64 / 2.0;
    LoginFrame {
        radius,
        buttons_side: side,
        buttons,
        dot: size,
        dot_gap: ((FRAME_DOT_GAP * size) as f64 / FRAME_DOT as f64).round_ties_even() as i64,
        dot_left: FRAME_DOT_LEFT + mv,
    }
}

/// (section, key, value) for the title bars Wayfire draws (wfconfig.frame_options):
/// the same corners, buttons, title and shadow as Sonata's windows.
pub fn frame_options(f: &LoginFrame) -> Vec<(&'static str, String, String)> {
    let side = f.buttons_side.as_str();
    let names = &f.buttons;
    let inset = f.dot_left - f.dot as f64 / 2.0;
    // pixdecor adds x_offset to the buttons' run on both sides: on the right
    // a positive one put them past the edge, where the corner cut the close
    // button (Vini: WhatsApp, VS Code) -- negative there
    let offset = if side == "left" { inset.round_ties_even() as i64 } else { -(inset.round_ties_even() as i64) };
    let rev: Vec<String> = names.iter().rev().cloned().collect();
    let layout = if side == "left" { format!("{}:", names.join(",")) } else { format!(":{}", rev.join(",")) };
    let align = match FRAME_TITLE_ALIGN {
        "left" => "0",
        "right" => "2",
        _ => "1",
    };
    // Wayfire's own bar keeps its buttons on the right: the outermost one at the edge
    let order = if side == "right" { rev.join(" ") } else { names[1..].iter().chain(&names[..1]).cloned().collect::<Vec<_>>().join(" ") };
    let o = |s: &'static str, k: &str, v: String| (s, k.to_string(), v);
    vec![
        o("pixdecor", "rounded_corner_radius", f.radius.to_string()),
        o("pixdecor", "button_layout", layout),
        o("pixdecor", &format!("{side}_button_spacing"), f.dot_gap.to_string()),
        o("pixdecor", &format!("{side}_button_x_offset"), offset.to_string()),
        o("pixdecor", "title_font", FRAME_TITLE_FONT.into()),
        o("pixdecor", "title_text_align", align.into()),
        o("pixdecor", "shadow_radius", FRAME_SHADOW_RADIUS.to_string()),
        o("pixdecor", "shadow_color", format!("\\{FRAME_SHADOW_COLOR}")),
        o("sonata-corners", "radius", f.radius.to_string()),
        o("decoration", "button_order", order),
        o("decoration", "title_height", FRAME_FALLBACK_TITLE_H.to_string()),
        o("decoration", "title_halign", FRAME_TITLE_ALIGN.into()),
        o("decoration", "font", FRAME_FALLBACK_FONT.into()),
    ]
}

pub const SUPER_TAB: &str = "<super> KEY_TAB";
/// scale's margin before the desktops bar, and the bar's first one
pub const OLD_MARGINS: [&str; 2] = ["28", "56"];
/// scale's room for the desktops bar (spaces.py SCALE_MARGIN)
pub const SCALE_MARGIN: i64 = 134;

/// A wayfire.ini kept from before Super+Tab became Mission Control (Vini:
/// Win+Tab shows the windows, as Task View): Super+Tab / Super+Shift+Tab
/// leave the app switcher (Alt+Tab stays), scale's toggle gets Super+Tab,
/// scale keeps room for the desktops bar (its old 28 px margin, the bar's
/// first 56), and the old default of four desktops becomes one.
pub fn migrate_mission_control(text: &str, margin: i64) -> String {
    let lines = py_splitlines(text);
    let parts = |s: &str| -> (String, String) {
        let (k, v) = s.split_once('=').unwrap_or((s, ""));
        (py_strip(k).to_string(), py_strip(v).to_string())
    };
    // the switcher's Super bindings, and their commands
    let mut drop: Vec<String> = Vec::new();
    let mut sec: Option<&str> = None;
    for line in &lines {
        let s = py_strip(line);
        if s.starts_with('[') && s.ends_with(']') {
            sec = Some(&s[1..s.len() - 1]);
        } else if sec == Some("command") && s.contains('=') {
            // (commented-out bindings count here too, as in wfconfig.py)
            let (key, val) = parts(s);
            if let Some(name) = key.strip_prefix("binding_") {
                if val == SUPER_TAB || val == "<super> <shift> KEY_TAB" {
                    drop.push(name.to_string());
                }
            }
        }
    }
    let mut out = String::with_capacity(text.len());
    let mut sec: Option<&str> = None;
    for line in &lines {
        let s = py_strip(line);
        if s.starts_with('[') && s.ends_with(']') {
            sec = Some(&s[1..s.len() - 1]);
        } else if s.contains('=') && !s.starts_with('#') {
            let (key, val) = parts(s);
            // key.split("_", 1): [0] before the first "_", [-1] after it (or the whole key)
            let (head, tail) = key.split_once('_').unwrap_or((&key, &key));
            if sec == Some("command") && drop.iter().any(|d| d == tail) && (head == "binding" || head == "command") {
                continue;
            }
            if sec == Some("scale") && key == "toggle" && !val.contains(SUPER_TAB) {
                out.push_str(&if val.is_empty() { format!("toggle = {SUPER_TAB}\n") } else { format!("toggle = {SUPER_TAB} | {val}\n") });
                continue;
            } else if sec == Some("scale") && key == "outer_margin" && OLD_MARGINS.contains(&val.as_str()) {
                out.push_str(&format!("outer_margin = {margin}\n"));
                continue;
            } else if sec == Some("core") && key == "vwidth" && val == "4" {
                out.push_str("vwidth = 1\n"); // the old default: one desktop now
                continue;
            }
        }
        out.push_str(line);
    }
    out
}

/// A wayfire.ini or overrides kept from before the screenshot shortcuts
/// were "always" bindings (Vini: they did nothing in Mission Control):
/// binding_screenshot becomes always_binding_screenshot, the user's keys
/// kept. Where both names are there the new one stays and the old goes --
/// one key, or Wayfire would bind it twice.
pub fn migrate_always_bindings(text: &str) -> String {
    let lines = py_splitlines(text);
    let key_of = |s: &str| py_strip(s.split_once('=').map_or(s, |x| x.0)).to_string();
    let mut have: Vec<String> = Vec::new();
    let mut sec: Option<&str> = None;
    for line in &lines {
        let s = py_strip(line);
        if s.starts_with('[') && s.ends_with(']') {
            sec = Some(&s[1..s.len() - 1]);
        } else if sec == Some("command") && s.contains('=') && !s.starts_with('#') {
            have.push(key_of(s));
        }
    }
    let mut out = String::with_capacity(text.len() + 64);
    let mut sec: Option<&str> = None;
    for line in &lines {
        let s = py_strip(line);
        if s.starts_with('[') && s.ends_with(']') {
            sec = Some(&s[1..s.len() - 1]);
        } else if sec == Some("command") && s.contains('=') && !s.starts_with('#') {
            let key = key_of(s);
            let new = current_key("command", &key);
            if new != key {
                if have.contains(&new) {
                    continue;
                }
                have.push(new);
                let at = line.find(&key).unwrap_or(0);
                out.push_str(&line[..at]);
                out.push_str("always_");
                out.push_str(&line[at..]);
                continue;
            }
        }
        out.push_str(line);
    }
    out
}

/// What follows the "=" of every line `^<key> *=` (any section), in order
/// (the regexes wayfire-config.sh's Python step used, `re.M`).
fn line_values<'a>(text: &'a str, key: &'a str) -> impl Iterator<Item = &'a str> {
    text.split('\n').filter_map(move |l| l.strip_prefix(key)?.trim_start_matches(' ').strip_prefix('='))
}

/// tools/wayfire-config.sh's first Python step on the session's copy's
/// text: the window frame, grid's crossfade off with sonata-resize, the
/// Mission Control migration, and the XSETTINGS manager for XWayland
/// (`_here`: Sonata's folder; nothing needs it since the manager is this
/// binary's).
pub fn login_frame_step(text: &str, frame: &LoginFrame, _here: &str) -> String {
    let mut t = text.to_string();
    for (sec, key, val) in frame_options(frame) {
        t = set_in(&t, sec, &key, Some(&val));
    }
    // zoom (maximize / restore): sonata-resize animates the frame; grid's
    // crossfade showed two title bars over each other (Vini)
    if line_values(&t, "plugins").next().is_some_and(|p| p.split(py_space).any(|w| w == "sonata-resize")) {
        t = set_in(&t, "grid", "type", Some("none"));
    }
    // Super+Tab is Mission Control now (a wayfire.ini kept from before had it on the app switcher)
    t = migrate_mission_control(&t, SCALE_MARGIN);
    // the screenshot shortcuts work in Mission Control too: always_binding_ keys
    t = migrate_always_bindings(&t);
    // X11 apps get Sonata's look from its XSETTINGS manager; a script of the user's stays
    // (the Python Sonata's own line -- `env PYTHONPATH=... python3 -m
    // sonata2.xsettings`, kept in an edited wayfire.ini -- is Sonata's, not
    // the user's: it is replaced, there is no Python Sonata to run it)
    let own = line_values(&t, "xwayland_startup_script").any(|v| v.trim_start_matches(' ').chars().next().is_some_and(|c| !py_space(c)) && !is_sonatas_xsettings(v));
    if !own {
        t = set_in(&t, "core", "xwayland_startup_script", Some("sonata2-rs xsettings"));
    }
    t
}

/// The XSETTINGS line Sonata writes, the Python Sonata's included.
fn is_sonatas_xsettings(value: &str) -> bool {
    let v = value.trim();
    v.ends_with("python3 -m sonata2.xsettings") || v == "sonata2-rs xsettings" || v == "sonata2 xsettings"
}

/// tools/wayfire-config.sh's second Python step: the options changed in
/// Settings (wayfire-overrides.ini) on top -- except the buttons' place,
/// set from the side above (an old right offset cut the close button).
pub fn apply_overrides(text: &str, overrides: &str) -> String {
    let mut t = text.to_string();
    let mut sec = String::new();
    for line in universal_newlines(overrides).split_inclusive('\n') {
        let s = py_strip(line);
        if s.starts_with('[') && s.ends_with(']') {
            sec = s[1..s.len() - 1].to_string();
        } else if !sec.is_empty() && s.contains('=') && !s.starts_with('#') {
            let (k, v) = s.split_once('=').unwrap_or_default();
            let k = py_strip(k);
            if (sec == "pixdecor" || sec == "decoration") && BUTTON_KEYS.contains(&k) {
                continue;
            }
            t = set_in(&t, &sec, &current_key(&sec, k), Some(py_strip(v)));
        }
    }
    t
}

/// `step` on the file at `out`, read and written as wfconfig.wayfire_set
/// does (text mode: "\r\n" read as "\n"; no write when nothing changes, so
/// a copy without changes keeps its bytes).
fn rewrite(out: &std::path::Path, step: impl FnOnce(&str) -> String) -> std::io::Result<()> {
    locked(|| {
        let raw = match std::fs::read(out) {
            Ok(b) => String::from_utf8(b).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => String::new(),
            Err(e) => return Err(e),
        };
        let text = universal_newlines(&raw);
        let new = step(&text);
        if new != text {
            sonata_config::atomic_write(out, new.as_bytes(), false)?;
        }
        Ok(())
    })
}

/// The first step on the file `out`, with appearance.json from `folder`.
pub fn frame_step_file(out: &std::path::Path, folder: &std::path::Path, here: &str) -> std::io::Result<()> {
    let frame = login_frame(folder);
    rewrite(out, |t| login_frame_step(t, &frame, here))
}

/// The second step: `overrides` (wayfire-overrides.ini) on the file `out`.
pub fn overrides_step_file(out: &std::path::Path, overrides: &std::path::Path) -> std::io::Result<()> {
    // the screenshot shortcuts' old binding_ keys renamed in the overrides
    // too (Settings reads and writes always_binding_ now)
    rewrite(overrides, migrate_always_bindings)?;
    let o = String::from_utf8(std::fs::read(overrides)?).map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    rewrite(out, |t| apply_overrides(t, &o))
}

/// `sonata2-rs wayfire-config frame <out.ini> <sonata folder>` and
/// `sonata2-rs wayfire-config overrides <out.ini> <overrides.ini>`: the two
/// steps, run where tools/wayfire-config.sh ran each Python one (the system
/// keyboard layout goes in between them).
pub fn login_main(args: &[String]) -> i32 {
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let result = match args.as_slice() {
        ["frame", out, here] => frame_step_file(out.as_ref(), &cfg_dir(), here),
        ["overrides", out, over] => overrides_step_file(out.as_ref(), over.as_ref()),
        _ => {
            eprintln!("usage: sonata2-rs wayfire-config frame <out.ini> <sonata folder> | overrides <out.ini> <overrides.ini>");
            return 2;
        }
    };
    match result {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("sonata2-rs wayfire-config: {e}");
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::set_in;

    #[test]
    fn keys_are_set_in_their_section() {
        let text = "# Sonata\n[input]\nxkb_layout = us\n\n[core]\nvwidth = 1\n";
        assert_eq!(set_in(text, "input", "xkb_layout", Some("br")), "# Sonata\n[input]\nxkb_layout = br\n\n[core]\nvwidth = 1\n");
        // a new key goes before the section's blank line
        assert_eq!(set_in(text, "input", "kb_repeat_rate", Some("40")), "# Sonata\n[input]\nxkb_layout = us\nkb_repeat_rate = 40\n\n[core]\nvwidth = 1\n");
        // a new section at the end
        assert_eq!(set_in("[a]\nx = 1", "b", "y", Some("2")), "[a]\nx = 1\n\n[b]\ny = 2\n");
        // removed
        assert_eq!(set_in(text, "core", "vwidth", None), "# Sonata\n[input]\nxkb_layout = us\n\n[core]\n");
        // already so: the same text (no write)
        assert_eq!(set_in(text, "input", "xkb_layout", Some("us")), text);
    }
}

#[cfg(test)]
mod login_tests {
    use super::*;

    const OLD: &str = "[command]
binding_switcher = <super> KEY_TAB
command_switcher = gdbus call switcher next
binding_switcher_back = <super> <shift> KEY_TAB
command_switcher_back = gdbus call switcher prev
binding_switcher_alt = <alt> KEY_TAB
command_switcher_alt = gdbus call switcher next
binding_term = <super> KEY_T
command_term = terminal

[core]
vwidth = 4
vheight = 1

[scale]
toggle = <ctrl> KEY_UP | KEY_F3 | swipe up 3
outer_margin = 28
";

    fn left() -> LoginFrame {
        login_frame(std::path::Path::new("/nonexistent-sonata-folder"))
    }

    fn frame_with(json: &str) -> LoginFrame {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("appearance.json"), json).unwrap();
        login_frame(d.path())
    }

    /// Vini: Win+Tab still opened the app switcher on a wayfire.ini kept from
    /// before Super+Tab became Mission Control (0.18).
    #[test]
    fn test_kept_config_super_tab_stayed_on_the_switcher() {
        let new = migrate_mission_control(OLD, SCALE_MARGIN);
        assert!(!new.contains("binding_switcher ="));
        assert!(!new.contains("command_switcher ="));
        assert!(!new.contains("binding_switcher_back"));
        assert!(!new.contains("command_switcher_back"));
        assert!(new.contains("binding_switcher_alt = <alt> KEY_TAB\n"), "Alt+Tab stays");
        assert!(new.contains("command_term = terminal\n"));
        assert!(new.contains("toggle = <super> KEY_TAB | <ctrl> KEY_UP | KEY_F3 | swipe up 3\n"));
        assert_eq!(migrate_mission_control(&new, SCALE_MARGIN), new, "once");
        // an empty toggle gets Super+Tab alone
        assert_eq!(migrate_mission_control("[scale]\ntoggle =\n", 134), "[scale]\ntoggle = <super> KEY_TAB\n");
    }

    /// Vini: the desktops bar covered the windows in Mission Control -- a kept
    /// config's scale margin (28, or the bar's first 56) left it no room (0.19).
    #[test]
    fn test_kept_config_desktops_bar_covered_the_windows() {
        assert!(migrate_mission_control(OLD, SCALE_MARGIN).contains("outer_margin = 134\n"));
        let again = OLD.replace("outer_margin = 28", "outer_margin = 56");
        assert!(migrate_mission_control(&again, SCALE_MARGIN).contains("outer_margin = 134\n"));
        let own = OLD.replace("outer_margin = 28", "outer_margin = 40");
        assert!(migrate_mission_control(&own, SCALE_MARGIN).contains("outer_margin = 40\n"), "a margin of the user's stays");
    }

    /// Vini: the screenshot keys did nothing while Mission Control was open
    /// -- always_binding_ keys now (config/wayfire.ini), with their commands.
    #[test]
    fn test_screenshot_keys_dead_in_mission_control() {
        let ini = include_str!("../../../../config/wayfire.ini");
        let keys: Vec<&str> = ini.lines().filter(|l| !l.trim_start().starts_with('#')).filter_map(|l| l.split_once('=')).map(|(k, _)| k.trim()).collect();
        for name in ALWAYS_BINDINGS {
            assert!(keys.contains(&format!("always_binding_{name}").as_str()), "{name}");
            assert!(!keys.contains(&format!("binding_{name}").as_str()), "{name}");
            assert!(keys.contains(&format!("command_{name}").as_str()), "{name}");
        }
        assert!(keys.contains(&"binding_draw"), "not a capture: stays as it was");
        assert_eq!(migrate_always_bindings(ini), ini, "nothing to move");
    }

    const KEPT_SHOTS: &str = "[command]
binding_launchpad = <super> | KEY_F4
# binding_screenshot = <super> KEY_X
binding_screenshot = <super> <shift> KEY_9
command_screenshot = sonata2 screenshot
binding_screenshot_area = <super> <shift> KEY_4
  binding_capture   =   <super> <shift> KEY_5
binding_capture_print = KEY_PRINT
always_binding_capture_print = KEY_SYSRQ
binding_draw = <super> <shift> KEY_D

[vswitch]
binding_screenshot = KEY_LEFT
";

    fn command_keys(text: &str) -> Vec<String> {
        let mut sec = "";
        let mut out = Vec::new();
        for l in text.lines() {
            let s = l.trim();
            if s.starts_with('[') && s.ends_with(']') {
                sec = &s[1..s.len() - 1];
            } else if sec == "command" && !s.starts_with('#') {
                if let Some((k, _)) = s.split_once('=') {
                    out.push(k.trim().to_string());
                }
            }
        }
        out
    }

    /// Vini: a wayfire.ini or overrides kept from before still had the old
    /// binding_ names (dead in Mission Control): renamed, the user's keys
    /// kept, never both names (Wayfire would bind the key twice).
    #[test]
    fn test_kept_config_screenshot_keys_dead_in_mission_control() {
        let new = migrate_always_bindings(KEPT_SHOTS);
        assert!(new.contains("\nalways_binding_screenshot = <super> <shift> KEY_9\n"), "the user's own: {new}");
        assert!(new.contains("\nalways_binding_screenshot_area = <super> <shift> KEY_4\n"));
        assert!(new.contains("\n  always_binding_capture   =   <super> <shift> KEY_5\n"), "the line as it was");
        assert!(new.contains("\nalways_binding_capture_print = KEY_SYSRQ\n") && !new.contains("KEY_PRINT"), "both: the new one stays");
        let keys = command_keys(&new);
        for name in ALWAYS_BINDINGS {
            assert!(!keys.contains(&format!("binding_{name}")), "{name}");
            assert_eq!(keys.iter().filter(|k| **k == format!("always_binding_{name}")).count(), 1, "{name}");
        }
        assert!(new.contains("# binding_screenshot = <super> KEY_X\n"), "comments untouched");
        assert!(new.contains("binding_launchpad = <super> | KEY_F4\n") && new.contains("binding_draw = <super> <shift> KEY_D\n"));
        assert!(new.ends_with("[vswitch]\nbinding_screenshot = KEY_LEFT\n"), "only [command]");
        assert_eq!(migrate_always_bindings(&new), new, "once");
        assert_eq!(migrate_always_bindings("[command]\nbinding_screenshot = KEY_A\nbinding_screenshot = KEY_B\n"), "[command]\nalways_binding_screenshot = KEY_A\n");
    }

    /// A file still holding the old name: Settings' change replaces it, not
    /// a second binding for the same shortcut (Wayfire would run it twice).
    #[test]
    fn test_settings_wrote_screenshot_key_twice() {
        let text = "[command]\nbinding_screenshot = KEY_A\ncommand_screenshot = sonata2 screenshot\n";
        assert_eq!(set_in(text, "command", "always_binding_screenshot", Some("KEY_B")), "[command]\ncommand_screenshot = sonata2 screenshot\nalways_binding_screenshot = KEY_B\n");
        assert_eq!(set_in("[vswitch]\nbinding_screenshot = KEY_A\n", "vswitch", "always_binding_screenshot", Some("KEY_B")), "[vswitch]\nbinding_screenshot = KEY_A\nalways_binding_screenshot = KEY_B\n", "only [command]");
    }

    /// The login's steps on a kept config and overrides with the old names:
    /// the user's key wins under the new name only, and the overrides file
    /// is renamed too (Settings reads always_binding_).
    #[test]
    fn test_kept_overrides_screenshot_key_lost_at_login() {
        let d = tempfile::tempdir().unwrap();
        let (out, over) = (d.path().join("out.ini"), d.path().join("over.ini"));
        let base = include_str!("../../../../config/wayfire.ini").replace("always_binding_", "binding_");
        std::fs::write(&out, base).unwrap();
        std::fs::write(&over, "[command]\nbinding_screenshot = <super> KEY_F12\n").unwrap();
        std::fs::write(&out, login_frame_step(&std::fs::read_to_string(&out).unwrap(), &left(), "/s")).unwrap();
        overrides_step_file(&out, &over).unwrap();
        let text = std::fs::read_to_string(&out).unwrap();
        let keys = command_keys(&text);
        for name in ALWAYS_BINDINGS {
            assert!(!keys.contains(&format!("binding_{name}")), "{name}");
            assert_eq!(keys.iter().filter(|k| **k == format!("always_binding_{name}")).count(), 1, "{name}");
        }
        assert!(text.contains("\nalways_binding_screenshot = <super> KEY_F12\n"));
        assert_eq!(std::fs::read_to_string(&over).unwrap(), "[command]\nalways_binding_screenshot = <super> KEY_F12\n");
    }

    /// Vini: a kept config started with four desktops; one to start with now (0.19).
    #[test]
    fn test_kept_config_started_with_four_desktops() {
        let new = migrate_mission_control(OLD, SCALE_MARGIN);
        assert!(new.contains("vwidth = 1\n") && new.contains("vheight = 1\n"));
        let three = OLD.replace("vwidth = 4", "vwidth = 3");
        assert!(migrate_mission_control(&three, SCALE_MARGIN).contains("vwidth = 3\n"), "only the old default moves");
    }

    /// Vini (WhatsApp, VS Code): with the buttons on the right a value Settings
    /// saved earlier (a positive right x_offset) put the close button past the
    /// edge, where the rounded corner cut it. The side's values win at login.
    #[test]
    fn test_old_right_offset_cut_the_close_button() {
        let f = frame_with(r#"{"buttons_side": "right"}"#);
        let base = login_frame_step("[pixdecor]\n", &f, "/s");
        assert!(base.contains("right_button_x_offset = -7\n"), "{base}");
        let over = "[pixdecor]\nright_button_x_offset = 7\nbutton_layout = close:\ntitle_font = Mine 11\n[decoration]\nbutton_order = close\n";
        let out = apply_overrides(&base, over);
        assert!(out.contains("right_button_x_offset = -7\n") && out.contains("button_layout = :maximize,minimize,close\n"));
        assert!(out.contains("button_order = maximize minimize close\n"), "{out}");
        assert!(out.contains("title_font = Mine 11\n"), "other options of the user's still win");
    }

    /// Vini: maximizing showed two title bars over each other (grid's
    /// crossfade) -- off when sonata-resize animates the frame.
    #[test]
    fn test_maximize_showed_two_title_bars() {
        let with = login_frame_step("[core]\nplugins = grid sonata-resize\n", &left(), "/s");
        assert!(with.contains("[grid]\ntype = none\n"));
        let without = login_frame_step("[core]\nplugins = grid resize\n", &left(), "/s");
        assert!(!without.contains("[grid]"));
    }

    /// Vini: X11 apps' GTK menu bars were light in Dark Mode -- XWayland
    /// starts Sonata's XSETTINGS manager; a script of the user's stays.
    #[test]
    fn test_x11_menu_bars_light_in_dark_mode() {
        let out = login_frame_step("[core]\nplugins = a\n", &left(), "/opt/sonata");
        assert!(out.contains("xwayland_startup_script = sonata2-rs xsettings\n"));
        let own = "[core]\nxwayland_startup_script = my-script\n";
        assert!(login_frame_step(own, &left(), "/s").contains("xwayland_startup_script = my-script\n"));
        // an empty one is no script of the user's
        let empty = login_frame_step("[core]\nxwayland_startup_script =\n", &left(), "/s");
        assert!(empty.contains("xwayland_startup_script = sonata2-rs xsettings\n"));
    }

    /// A wayfire.ini kept from the Python Sonata starts XWayland's XSETTINGS
    /// manager with `python3 -m sonata2.xsettings`: with no Python Sonata
    /// left, X11 apps would lose Sonata's look. That line is Sonata's, not
    /// the user's own script: it becomes the Rust one.
    #[test]
    fn test_python_xsettings_line_kept_after_the_upgrade() {
        let old = "[core]\nxwayland_startup_script = env PYTHONPATH=/home/v/.local/share/sonata2 python3 -m sonata2.xsettings\n";
        let out = login_frame_step(old, &left(), "/s");
        assert!(out.contains("xwayland_startup_script = sonata2-rs xsettings\n") && !out.contains("python3"), "{out}");
    }

    #[test]
    fn frame_options_follow_the_side_order_size_and_radius() {
        let o = |f: &LoginFrame| frame_options(f).into_iter().map(|(s, k, v)| (format!("{s}/{k}"), v)).collect::<std::collections::HashMap<_, _>>();
        let l = o(&left());
        assert_eq!(l["pixdecor/button_layout"], "close,minimize,maximize:");
        assert_eq!(l["pixdecor/left_button_x_offset"], "7");
        assert_eq!(l["pixdecor/shadow_color"], "\\#0000001f");
        assert_eq!(l["decoration/button_order"], "minimize maximize close");
        // radius 11: the inset is 7.5, rounded half to even as Python does
        let r11 = o(&frame_with(r#"{"radius": {"window": 11}}"#));
        assert_eq!((r11["pixdecor/rounded_corner_radius"].as_str(), r11["pixdecor/left_button_x_offset"].as_str()), ("11", "8"));
        let r13 = o(&frame_with(r#"{"radius": {"window": 13}}"#));
        assert_eq!(r13["pixdecor/left_button_x_offset"], "8");
        let big = o(&frame_with(r#"{"button_size": 16, "buttons_side": "right", "buttons_order": ["minimize", "close", "maximize"]}"#));
        assert_eq!((big["pixdecor/right_button_spacing"].as_str(), big["pixdecor/right_button_x_offset"].as_str()), ("11", "-7"));
        assert_eq!(big["pixdecor/button_layout"], ":maximize,close,minimize");
    }

    /// tokens.py's own quirks, kept so both sessions draw the same title bars.
    #[test]
    fn appearance_read_as_tokens_py_reads_it() {
        assert_eq!(frame_with(r#"{"radius": {"window": true}}"#).radius, 1);
        assert_eq!(frame_with(r#"{"radius": 5, "button_size": 16}"#).dot, 12, "no radius object: no size read");
        assert_eq!(frame_with(r#"{"button_size": true}"#).dot, 12);
        assert_eq!(frame_with(r#"[1]"#), left());
        assert_eq!(frame_with("{bad"), left());
        assert_eq!(frame_with(r#"{"buttons_order": ["close", "close", "maximize"]}"#).buttons, ["close", "minimize", "maximize"]);
    }

    /// The constants here are tokens.FRAME's (sonata-ui's tokens.json).
    #[test]
    fn frame_constants_are_the_tokens() {
        let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../sonata-ui/tokens.json")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        let f = &v["frame"];
        assert_eq!(f["radius"], FRAME_RADIUS);
        assert_eq!(f["dot"], FRAME_DOT);
        assert_eq!(f["dot_gap"], FRAME_DOT_GAP);
        assert_eq!(f["dot_left"].as_f64(), Some(FRAME_DOT_LEFT));
        assert_eq!(f["shadow_radius"], FRAME_SHADOW_RADIUS);
        assert_eq!(f["shadow_color"], FRAME_SHADOW_COLOR);
        assert_eq!(f["title_align"], FRAME_TITLE_ALIGN);
        assert_eq!(f["title_font"], FRAME_TITLE_FONT);
        assert_eq!(f["fallback_title_h"], FRAME_FALLBACK_TITLE_H);
        assert_eq!(f["fallback_font"], FRAME_FALLBACK_FONT);
        assert_eq!(f["buttons_side"], "left");
    }

    #[test]
    fn python_text_rules() {
        assert_eq!(py_strip("\x1c a \u{a0}"), "a");
        assert_eq!(py_strip("\u{200e}a"), "\u{200e}a");
        assert_eq!(py_splitlines("a\r\nb\x0bc\u{2028}d"), ["a\r\n", "b\x0b", "c\u{2028}", "d"]);
        assert_eq!(universal_newlines("a\r\nb\rc\n"), "a\nb\nc\n");
    }

    #[test]
    fn overrides_skip_comments_and_nameless_sections() {
        let out = apply_overrides("[input]\nxkb_layout = us\n", "x = 1\n[]\ny = 2\n[input]\n# xkb_layout = de\nxkb_layout = br \r\n");
        assert_eq!(out, "[input]\nxkb_layout = br\n");
    }

    /// The copy is written only when something changes (no Wayfire reload),
    /// and in text mode, as Python wrote it.
    #[test]
    fn rewrite_keeps_an_unchanged_file_and_normalizes_a_changed_one() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("w.ini");
        std::fs::write(&p, "[a]\r\nx = 1\r\n").unwrap();
        rewrite(&p, |t| t.to_string()).unwrap();
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "[a]\r\nx = 1\r\n");
        rewrite(&p, |t| set_in(t, "a", "y", Some("2"))).unwrap();
        assert_eq!(std::fs::read_to_string(&p).unwrap(), "[a]\nx = 1\ny = 2\n");
    }
}
