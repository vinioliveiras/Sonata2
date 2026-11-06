//! The screen goes dark soon after it is locked, and comes back on any key
//! or move (shell/lockdisplay.py, with the bits of idlelock.py,
//! blackout.py and displaysleep.py it uses).
//!
//! Dark, not off: the lock screen fades to black and the panel's backlight
//! goes to zero (and the keyboard's lights). Turning the display itself off
//! and on again failed on Vini's laptop (NVIDIA refused the mode set) and
//! the screen came back only by closing and opening the lid. So while
//! locked Wayfire's own display timeout is off too; your setting is put
//! back on unlock (kept in $XDG_RUNTIME_DIR; the menu bar also puts it back
//! after a lock screen that crashed).
//!
//! Every file here is the Python Sonata's: one session, one lock state.

use serde_json::{json, Map, Value};
use sonata_backend::wfconfig;
use std::path::PathBuf;

pub const LOCKED_DPMS_S: i64 = 30;
const KEYS: [(&str, &str); 2] = [("idle", "dpms_timeout"), ("idle", "disable_on_fullscreen")];
/// "Turn display off after", seconds (displaysleep.py)
pub const DISPLAY_DEFAULT_S: i64 = 600;

fn runtime() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp"))
}

fn cache() -> PathBuf {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    std::env::var_os("XDG_CACHE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".cache")).join("sonata2")
}

fn state() -> PathBuf {
    runtime().join("sonata2-lock-display.json")
}

/// Present while the lock screen holds the session (its pid inside).
pub fn locked_marker() -> PathBuf {
    runtime().join("sonata2-locked")
}

/// `sonata2 lock-wait` returns once this is there; the menu bar reads it.
pub fn mark_locked(on: bool) {
    if on {
        let _ = std::fs::write(locked_marker(), std::process::id().to_string());
    } else {
        let _ = std::fs::remove_file(locked_marker());
    }
}

/// {"idle/dpms_timeout": "600", ...} from before the lock, {} when not locked.
pub fn saved() -> Map<String, Value> {
    std::fs::read_to_string(state()).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default()
}

pub fn locked() {
    if saved().is_empty() {
        // (a second lock keeps the first one's values)
        let values: Map<String, Value> = KEYS.iter().map(|(s, k)| (format!("{s}/{k}"), json!(wfconfig::get(s, k, "")))).collect();
        if std::fs::write(state(), sonata_config::to_python_json(&Value::Object(values))).is_err() {
            return;
        }
    }
    // the lock screen darkens it itself (dim()): Wayfire never powers it off meanwhile
    wfconfig::runtime_set("idle", "dpms_timeout", Some("-1"));
}

pub fn unlocked() {
    let old = saved();
    if old.is_empty() {
        return;
    }
    for (s, k) in KEYS {
        let v = old.get(&format!("{s}/{k}")).and_then(Value::as_str).filter(|v| !v.is_empty());
        wfconfig::runtime_set(s, k, v); // (none set before: Wayfire's default)
    }
    let _ = std::fs::remove_file(state());
}

/// Seconds without input before the display goes dark; <= 0 never. The
/// first time: what Wayfire's timeout was set to (displaysleep.seconds).
pub fn display_seconds() -> i64 {
    let cfg = sonata_config::load("display", &json!({"off_after": null}));
    match cfg.get("off_after") {
        Some(Value::Null) | None => {}
        Some(v) => return v.as_i64().or_else(|| v.as_str().and_then(|s| s.parse().ok())).unwrap_or(DISPLAY_DEFAULT_S),
    }
    let raw = saved().get("idle/dpms_timeout").and_then(Value::as_str).filter(|s| !s.is_empty()).map(String::from).unwrap_or_else(|| wfconfig::get("idle", "dpms_timeout", &DISPLAY_DEFAULT_S.to_string()));
    let old = raw.trim().parse::<i64>().unwrap_or(DISPLAY_DEFAULT_S);
    let old = if old == 0 { DISPLAY_DEFAULT_S } else { old };
    let _ = sonata_config::update("display", &json!({"off_after": old}));
    old
}

/// How long the locked screen stays lit: LOCKED_DPMS_S, or your own
/// shorter time; None when you chose that the display never turns off.
pub fn dark_seconds_for(display_s: i64) -> Option<i64> {
    (display_s > 0).then(|| display_s.min(LOCKED_DPMS_S))
}

pub fn dark_seconds() -> Option<i64> {
    dark_seconds_for(display_seconds())
}

/// Present while the session is dark from idle (the lock screen's video waits for it to go).
pub fn dark_marker() -> PathBuf {
    runtime().join("sonata2-idle-dark")
}

/// The session went dark while idle / came back: the lock screen that
/// follows starts dark too.
pub fn mark_dark(on: bool) {
    if on {
        let _ = std::fs::write(dark_marker(), "");
    } else {
        let _ = std::fs::remove_file(dark_marker());
    }
}

pub fn was_dark() -> bool {
    dark_marker().exists()
}

/// shlex.quote: the same commands the Python Sonata runs, byte for byte.
pub fn quote(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        return s.to_string();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

fn qpath(name: &str) -> String {
    quote(&cache().join(name).display().to_string())
}

const KBD: &str = "*::kbd_backlight";
const KBD_LIST: &str = "brightnessctl -l -m -c leds 2>/dev/null | cut -d, -f1 | grep 'kbd_backlight'";

/// Every keyboard's light off, each level saved in a file of its own.
pub fn kbd_off() -> String {
    let dir = qpath("keyboards-before-dark");
    format!("for d in $({KBD_LIST}); do b=$(brightnessctl -d \"$d\" g 2>/dev/null); if [ \"${{b:-0}}\" -gt 0 ] 2>/dev/null; then mkdir -p {dir}; echo \"$b\" > {dir}/\"$d\"; brightnessctl -q -d \"$d\" set 0; fi; done")
}

pub fn kbd_on() -> String {
    let (dir, state) = (qpath("keyboards-before-dark"), qpath("lights-before-dark"));
    format!("for f in {dir}/*; do [ -s \"$f\" ] || continue; brightnessctl -q -d \"$(basename \"$f\")\" set \"$(cat \"$f\")\"; rm -f \"$f\"; done; if [ -s {state} ]; then brightnessctl -q -d '{KBD}' set \"$(cat {state})\"; rm -f {state}; fi")
}

pub fn bl_off() -> String {
    let st = qpath("backlight-before-dark");
    format!("b=$(brightnessctl -c backlight g 2>/dev/null); if [ \"${{b:-0}}\" -gt 0 ] 2>/dev/null; then mkdir -p \"$(dirname {st})\"; echo \"$b\" > {st}; brightnessctl -q -c backlight set 0; fi")
}

pub fn bl_on() -> String {
    let st = qpath("backlight-before-dark");
    format!("if [ -s {st} ]; then brightnessctl -q -c backlight set \"$(cat {st})\"; rm -f {st}; fi")
}

/// One lights command at a time, whoever runs it (the lock screen, the
/// idle timer, swayidle): a wake-up while "off" still ran put "back on"
/// before there was anything to put back.
pub fn serial(cmd: &str) -> String {
    if sonata_backend::run::which("flock").is_none() {
        return cmd.to_string();
    }
    let lock = qpath("lights.lock");
    format!("mkdir -p \"$(dirname {lock})\" && flock {lock} sh -c {}", quote(cmd))
}

/// Runs on its own: it outlives the lock screen, which quits right after unlocking.
fn detached(cmd: &str) {
    use std::os::unix::process::CommandExt;
    if let Ok(c) = std::process::Command::new("sh").args(["-c", cmd]).stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null()).process_group(0).spawn() {
        sonata_backend::run::reap(c); // (a zombie in this process otherwise)
    }
}

/// A built-in panel whose backlight can go to zero (a laptop).
pub fn has_backlight() -> bool {
    std::fs::read_dir("/sys/class/backlight").is_ok_and(|mut d| d.next().is_some()) && sonata_backend::run::which("brightnessctl").is_some()
}

fn keyboard_light() -> bool {
    let leds = std::fs::read_dir("/sys/class/leds").map(|d| d.flatten().any(|e| e.file_name().to_string_lossy().ends_with("::kbd_backlight"))).unwrap_or(false);
    leds && sonata_backend::run::which("brightnessctl").is_some()
}

/// The built-in panel's backlight to zero / back to its level.
pub fn dim(dark: bool) {
    if sonata_backend::run::which("brightnessctl").is_some() {
        detached(&serial(&if dark { bl_off() } else { bl_on() }));
    }
}

/// The keyboard's backlight off / back as it was.
pub fn lights(on: bool) {
    if keyboard_light() {
        detached(&serial(&if on { kbd_on() } else { kbd_off() }));
    }
}

/// The pointer off / back, by the compositor (sonata-corners): a still
/// pointer stayed in the middle of the dark screen (Vini).
pub fn pointer(hidden: bool) {
    sonata_backend::wfipc::call("sonata/cursor", json!({"hidden": hidden}));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quoted_like_shlex() {
        assert_eq!(quote("/home/v/.cache/sonata2/x"), "/home/v/.cache/sonata2/x");
        assert_eq!(quote("/home/my user/x"), "'/home/my user/x'");
        assert_eq!(quote("it's"), "'it'\"'\"'s'");
        assert_eq!(quote(""), "''");
    }

    #[test]
    fn the_locked_screen_stays_lit_at_most_30_s() {
        assert_eq!(dark_seconds_for(600), Some(30));
        assert_eq!(dark_seconds_for(10), Some(10));
        assert_eq!(dark_seconds_for(0), None); // never: it stays lit
        assert_eq!(dark_seconds_for(-1), None);
    }

    /// The commands the Python Sonata's idlelock ran (recorded from it, with
    /// its cache folder as {CACHE}): a lock that darkened the screen with
    /// one Sonata must light it again with the other.
    #[test]
    fn the_same_shell_commands_as_python() {
        let cache = cache().display().to_string();
        let want = [r#"b=$(brightnessctl -c backlight g 2>/dev/null); if [ "${b:-0}" -gt 0 ] 2>/dev/null; then mkdir -p "$(dirname {CACHE}/backlight-before-dark)"; echo "$b" > {CACHE}/backlight-before-dark; brightnessctl -q -c backlight set 0; fi"#, r#"if [ -s {CACHE}/backlight-before-dark ]; then brightnessctl -q -c backlight set "$(cat {CACHE}/backlight-before-dark)"; rm -f {CACHE}/backlight-before-dark; fi"#, r#"for d in $(brightnessctl -l -m -c leds 2>/dev/null | cut -d, -f1 | grep 'kbd_backlight'); do b=$(brightnessctl -d "$d" g 2>/dev/null); if [ "${b:-0}" -gt 0 ] 2>/dev/null; then mkdir -p {CACHE}/keyboards-before-dark; echo "$b" > {CACHE}/keyboards-before-dark/"$d"; brightnessctl -q -d "$d" set 0; fi; done"#, r#"for f in {CACHE}/keyboards-before-dark/*; do [ -s "$f" ] || continue; brightnessctl -q -d "$(basename "$f")" set "$(cat "$f")"; rm -f "$f"; done; if [ -s {CACHE}/lights-before-dark ]; then brightnessctl -q -d '*::kbd_backlight' set "$(cat {CACHE}/lights-before-dark)"; rm -f {CACHE}/lights-before-dark; fi"#].map(|t| t.replace("{CACHE}", &cache));
        assert_eq!([bl_off(), bl_on(), kbd_off(), kbd_on()], want);
    }
}
