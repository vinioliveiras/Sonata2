//! Token use by the Assistant, counted locally (assistant/usage.py; an
//! API key has no hourly or weekly limits of its own: the limits are
//! yours). usage.json: {"calls": [[epoch seconds, tokens], ...]}, kept 7 days.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};

const HOUR: f64 = 3600.0;
const WEEK: f64 = 7.0 * 86400.0;
/// limit choices
pub const HOURLY: [i64; 6] = [100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000];
pub const WEEKLY: [i64; 6] = [1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000, 100_000_000];

pub struct Usage {
    path: PathBuf,
    pub calls: Vec<(f64, i64)>,
}

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

impl Usage {
    pub fn open(folder: &Path) -> Self {
        let path = folder.join("usage.json");
        let calls = std::fs::read_to_string(&path).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|v| v.get("calls").and_then(Value::as_array).cloned()).unwrap_or_default();
        let calls = calls.iter().filter_map(|c| {
            let a = c.as_array().filter(|a| a.len() == 2)?;
            Some((a[0].as_f64()?, a[1].as_i64()?))
        }).collect();
        Self { path, calls }
    }

    pub fn add(&mut self, tokens: i64, now: f64) {
        if tokens > 0 {
            self.calls.push((now, tokens));
        }
        self.calls.retain(|c| c.0 > now - WEEK);
        let list: Vec<Value> = self.calls.iter().map(|(t, n)| json!([(t * 10.0).round_ties_even() / 10.0, n])).collect();
        if let Some(d) = self.path.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = sonata_config::atomic_write(&self.path, sonata_config::to_python_json_utf8(&json!({"calls": list}), 1).as_bytes(), true);
    }

    fn since(&self, seconds: f64, now: f64) -> i64 {
        self.calls.iter().filter(|c| c.0 > now - seconds).map(|c| c.1).sum()
    }

    pub fn last_hour(&self, now: f64) -> i64 {
        self.since(HOUR, now)
    }

    pub fn last_week(&self, now: f64) -> i64 {
        self.since(WEEK, now)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counted_by_hour_and_week() {
        let d = tempfile::tempdir().unwrap();
        let mut u = Usage::open(d.path());
        u.add(100, 1000.0);
        u.add(50, 1000.0 + 3000.0);
        u.add(0, 1000.0 + 3000.0); // nothing used: nothing added
        assert_eq!(u.last_hour(1000.0 + 3700.0), 50);
        assert_eq!(u.last_week(1000.0 + 3700.0), 150);
        let again = Usage::open(d.path());
        assert_eq!(again.calls.len(), 2);
        let mut later = Usage::open(d.path());
        later.add(1, 1000.0 + 8.0 * 86400.0); // older than a week: dropped
        assert_eq!(later.calls.len(), 1);
    }
}
