//! Waits after wrong passwords (sonata2/throttle.py; macOS: "Try again in
//! 1 minute"), for the login and lock screens. Kept in a small JSON file
//! (the Python screens' own: one count per user, whichever screen asks),
//! so restarting the screen or the computer doesn't reset it. The first
//! FREE_TRIES wrong passwords cost nothing; then each one waits longer.

use serde_json::{json, Map, Value};
use sonata_i18n::{tr, trn};
use std::path::PathBuf;

pub const FREE_TRIES: i64 = 4;
/// 5th, 6th, 7th, 8th+ wrong password
pub const WAITS: [i64; 4] = [60, 5 * 60, 15 * 60, 60 * 60];

pub struct Throttle {
    pub path: PathBuf,
    pub clock: fn() -> f64,
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// $XDG_STATE_HOME/sonata2/password-waits.json (a restart doesn't reset it)
pub fn default_path() -> PathBuf {
    let home = std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default();
    std::env::var_os("XDG_STATE_HOME").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| home.join(".local").join("state")).join("sonata2").join("password-waits.json")
}

impl Throttle {
    pub fn new(path: PathBuf) -> Self {
        Self { path, clock: now }
    }

    fn load(&self) -> Map<String, Value> {
        std::fs::read_to_string(&self.path).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default()
    }

    fn save(&self, data: &Map<String, Value>) {
        use std::os::unix::fs::PermissionsExt;
        let Some(dir) = self.path.parent() else { return };
        if std::fs::create_dir_all(dir).is_err() {
            return;
        }
        let tmp = PathBuf::from(format!("{}.{}.tmp", self.path.display(), std::process::id()));
        let text = sonata_config::to_python_json(&Value::Object(data.clone()));
        if std::fs::write(&tmp, text).is_ok() && std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600)).is_ok() {
            let _ = std::fs::rename(&tmp, &self.path);
        }
        let _ = std::fs::remove_file(&tmp);
    }

    /// Seconds still to wait (0: may try).
    pub fn wait_left(&self, user: &str) -> i64 {
        let until = self.load().get(user).and_then(|e| e.get("until")).and_then(Value::as_f64).unwrap_or(0.0);
        ((until - (self.clock)() + 0.999) as i64).max(0)
    }

    /// A wrong password; returns the wait it starts.
    pub fn failed(&self, user: &str) -> i64 {
        let mut data = self.load();
        let n = data.get(user).and_then(|e| e.get("failures")).and_then(Value::as_i64).unwrap_or(0) + 1;
        let wait = if n <= FREE_TRIES { 0 } else { WAITS[((n - FREE_TRIES).min(WAITS.len() as i64) - 1) as usize] };
        let until = if wait > 0 { json!((self.clock)() + wait as f64) } else { json!(0) };
        data.insert(user.to_string(), json!({"failures": n, "until": until}));
        self.save(&data);
        wait
    }

    /// The right password: the count starts over.
    pub fn succeeded(&self, user: &str) {
        let mut data = self.load();
        if data.shift_remove(user).is_some() {
            self.save(&data);
        }
    }
}

/// "Try again in 4:59" (minutes:seconds), "Try again in 59 seconds".
pub fn describe(seconds: i64) -> String {
    if seconds >= 60 {
        return sonata_i18n::fmt(&tr!("Try again in {time}"), &[("time", &format!("{}:{:02}", seconds / 60, seconds % 60))]);
    }
    sonata_i18n::fmt(&trn!("Try again in {n} second", "Try again in {n} seconds", seconds), &[("n", &seconds.to_string())])
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static T: AtomicU64 = AtomicU64::new(1000);
    fn clock() -> f64 {
        T.load(Ordering::Relaxed) as f64
    }

    #[test]
    fn waits_grow_after_the_free_tries_and_reset_on_success() {
        let d = tempfile::tempdir().unwrap();
        let t = Throttle { path: d.path().join("w.json"), clock };
        for _ in 0..FREE_TRIES {
            assert_eq!(t.failed("vini"), 0);
        }
        assert_eq!(t.wait_left("vini"), 0);
        assert_eq!(t.failed("vini"), 60);
        assert_eq!(t.wait_left("vini"), 60);
        T.fetch_add(30, Ordering::Relaxed);
        assert_eq!(t.wait_left("vini"), 30);
        assert_eq!(t.wait_left("ana"), 0); // per user
        for want in [300, 900, 3600, 3600] {
            assert_eq!(t.failed("vini"), want);
        }
        t.succeeded("vini");
        assert_eq!(t.wait_left("vini"), 0);
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&t.path).unwrap().permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn the_python_file_reads_the_same() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("w.json");
        std::fs::write(&p, r#"{"vini": {"failures": 5, "until": 1090.5}}"#).unwrap();
        T.store(1000, Ordering::Relaxed);
        assert_eq!(Throttle { path: p, clock }.wait_left("vini"), 91);
    }

    #[test]
    fn describing_the_wait() {
        sonata_i18n::use_language("en");
        assert_eq!(describe(299), "Try again in 4:59");
        assert_eq!(describe(1), "Try again in 1 second");
        assert_eq!(describe(59), "Try again in 59 seconds");
    }
}
