//! Each device keeps its own volume (sonata2/backend/devicevolume.py, its
//! "remember" half): a volume set from Sonata is remembered for the device
//! in use (devicevolumes.json), written once a moment after it was set.

use serde_json::{json, Map, Value};
use std::sync::Mutex;
use std::time::Duration;

pub const NAME: &str = "devicevolumes";
const SAVE_AFTER: Duration = Duration::from_millis(800);
const MAX_DEVICES: usize = 40;

struct Pending {
    values: Vec<(String, i64)>,
    serial: u64,
}

static PENDING: Mutex<Pending> = Mutex::new(Pending { values: Vec::new(), serial: 0 });

pub fn pactl(args: &[&str]) -> String {
    let mut cmd = vec!["pactl"];
    cmd.extend_from_slice(args);
    let (rc, out) = crate::run::run(&cmd, 5);
    if rc == 0 { out } else { String::new() }
}

pub fn usable(name: &str) -> bool {
    !name.is_empty() && !name.ends_with(".monitor") && !name.starts_with("sonata-eq")
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
        p.serial
    };
    std::thread::spawn(move || {
        std::thread::sleep(SAVE_AFTER);
        let mine = PENDING.lock().unwrap_or_else(|e| e.into_inner()).serial == serial;
        if mine {
            flush(None);
        }
    });
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
