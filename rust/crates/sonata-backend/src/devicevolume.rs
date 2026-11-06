//! Each device keeps its own volume (sonata2/backend/devicevolume.py, its
//! "remember" half): a volume set from Sonata is remembered for the device
//! in use (devicevolumes.json), written once a moment after it was set.
//! recently_set() tells the lock half (sonata-system's devicevolume) that
//! a change was Sonata's own, so "Don't let apps change the volume" never
//! puts back the user's own slider -- even one set from another process
//! (marker()).

use serde_json::{json, Map, Value};
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime};

pub const NAME: &str = "devicevolumes";
const SAVE_AFTER: Duration = Duration::from_millis(800);
const MAX_DEVICES: usize = 40;
/// a change this soon after Sonata set one is Sonata's (LOCK_GRACE_S)
pub const LOCK_GRACE: Duration = Duration::from_secs(2);

struct Pending {
    values: Vec<(String, i64)>,
    serial: u64,
    /// kind -> when Sonata last set it (_set_at)
    set_at: Vec<(String, Instant)>,
}

static PENDING: Mutex<Pending> = Mutex::new(Pending { values: Vec::new(), serial: 0, set_at: Vec::new() });

pub fn pactl(args: &[&str]) -> String {
    let mut cmd = vec!["pactl"];
    cmd.extend_from_slice(args);
    let (rc, out) = crate::run::run(&cmd, 5);
    if rc == 0 { out } else { String::new() }
}

pub fn usable(name: &str) -> bool {
    !name.is_empty() && !name.ends_with(".monitor") && !name.starts_with("sonata-eq")
}

/// $XDG_RUNTIME_DIR/sonata2-volume-set-<kind>, touched by every volume
/// Sonata sets (devicevolume.py's marker(), the same file). The lock runs
/// in the menu bar, but Settings > Sound's sliders and the volume keys set
/// the volume from processes of their own: without it the menu bar only
/// learnt the change was Sonata's once devicevolumes.json was written
/// (0.8 s later), and put the slider back while it was being dragged.
pub fn marker(kind: &str) -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/tmp")).join(format!("sonata2-volume-set-{kind}"))
}

fn mark(kind: &str) {
    if let Ok(f) = std::fs::OpenOptions::new().create(true).append(true).open(marker(kind)) {
        let _ = f.set_modified(SystemTime::now());
    }
}

/// Any Sonata process set this kind's volume less than LOCK_GRACE before
/// `now` (one stat: it runs on every volume event).
pub fn marked_at(kind: &str, now: SystemTime) -> bool {
    let Ok(at) = std::fs::metadata(marker(kind)).and_then(|m| m.modified()) else { return false };
    let age = now.duration_since(at).unwrap_or_else(|e| e.duration()); // either way round: clocks
    age < LOCK_GRACE
}

/// The default `kind`'s volume ("sink" / "source") was set to `percent`.
pub fn remember(kind: &str, percent: i64) {
    if kind != "sink" && kind != "source" {
        return;
    }
    let serial = {
        let mut p = PENDING.lock().unwrap_or_else(|e| e.into_inner());
        p.values.retain(|(k, _)| k != kind);
        p.values.push((kind.to_string(), percent.clamp(0, 100)));
        p.serial += 1;
        p.set_at.retain(|(k, _)| k != kind);
        p.set_at.push((kind.to_string(), Instant::now()));
        p.serial
    };
    mark(kind);
    std::thread::spawn(move || {
        std::thread::sleep(SAVE_AFTER);
        let mine = PENDING.lock().unwrap_or_else(|e| e.into_inner()).serial == serial;
        if mine {
            flush(None);
        }
    });
}

/// Sonata set this kind's volume itself just now: in this process (still
/// waiting to be written, or remember() ran less than LOCK_GRACE ago --
/// devicevolume.py keep()'s grace) or in another one (marked_at). The lock
/// leaves such a change alone.
pub fn recently_set(kind: &str) -> bool {
    recently_set_at(kind, Instant::now()) || marked_at(kind, SystemTime::now())
}

/// This process's half of recently_set().
pub fn recently_set_at(kind: &str, now: Instant) -> bool {
    let p = PENDING.lock().unwrap_or_else(|e| e.into_inner());
    p.values.iter().any(|(k, _)| k == kind) || p.set_at.iter().any(|(k, at)| k == kind && now.saturating_duration_since(*at) < LOCK_GRACE)
}

/// Write what's pending under each default device's name.
pub fn flush(default_name: Option<&dyn Fn(&str) -> String>) {
    let pending = std::mem::take(&mut PENDING.lock().unwrap_or_else(|e| e.into_inner()).values);
    if pending.is_empty() {
        return;
    }
    let mut data = sonata_config::load(NAME, &json!({"sink": {}, "source": {}}));
    let mut changed = false;
    for (kind, pct) in pending {
        let name = match default_name {
            Some(f) => f(&kind),
            None => pactl(&[&format!("get-default-{kind}")]).trim().to_string(),
        };
        if !usable(&name) {
            continue;
        }
        let mut devices: Map<String, Value> = data.get(&kind).and_then(Value::as_object).cloned().unwrap_or_default();
        devices.shift_remove(&name); // most recent last
        devices.insert(name, json!(pct));
        while devices.len() > MAX_DEVICES {
            let first = devices.keys().next().cloned().unwrap_or_default();
            devices.shift_remove(&first);
        }
        data.insert(kind, Value::Object(devices));
        changed = true;
    }
    if changed {
        let _ = sonata_config::save(NAME, &data);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// With Settings > Sound > "Don't let apps change the volume" on, the
    /// lock never switched on in the Rust session: nothing told it which
    /// changes were Sonata's own, so it left every app's change alone (an
    /// app turning the microphone down stayed down).
    #[test]
    fn test_lock_never_knew_sonata_own_change() {
        // (this process's half: the sink's marker is the next test's)
        assert!(!recently_set_at("source", Instant::now()));
        remember("source", 70);
        assert!(recently_set("source")); // pending, then within the grace
        assert!(!recently_set_at("sink", Instant::now())); // only that kind
        flush(Some(&|_: &str| String::new())); // written (no device: nothing saved)
        assert!(recently_set("source"));
        let later = Instant::now() + LOCK_GRACE + Duration::from_millis(10);
        assert!(!recently_set_at("source", later)); // an app's change, later on
    }

    /// With "Don't let apps change the volume" on, dragging Settings >
    /// Sound's slider jumped back: the slider sets the volume in Settings'
    /// process, the lock runs in the menu bar's, and the menu bar only knew
    /// the change was Sonata's once devicevolumes.json was written, 0.8 s
    /// later. Another process's change (the marker alone, nothing known
    /// here) counts as Sonata's at once.
    #[test]
    fn test_settings_slider_dragged_was_put_back() {
        // what Settings' remember() leaves behind, from its own process
        mark("sink");
        assert!(!recently_set_at("sink", Instant::now())); // nothing in this process
        assert!(recently_set("sink"));
        let at = std::fs::metadata(marker("sink")).unwrap().modified().unwrap();
        assert!(!marked_at("sink", at + LOCK_GRACE + Duration::from_millis(10))); // an app's, later on
        assert!(marked_at("sink", at + Duration::from_millis(500)));
    }
}
