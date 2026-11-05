//! Each device keeps its own volume (backend/devicevolume.py; Vini: "I keep
//! the headset's mic at the maximum and it keeps resetting"): the half that
//! puts it back. The "remember" half -- a volume set from Sonata written
//! under the device in use, in devicevolumes.json -- is sonata-backend's
//! devicevolume.rs (system::set_volume calls it).
//!
//! - restore(): a device that comes back (plugged in again, a Bluetooth
//!   headset reconnecting or switching to its headset profile, which makes
//!   a new microphone) gets its volume again, after WirePlumber has put its
//!   own back (audiofollow's "new" events).
//! - keep(): Settings > Sound > "Don't let apps change the volume"
//!   (sounds.json lock_volumes): a volume nobody set from Sonata goes back
//!   at once (audiofollow's "change" events). Sonata's own changes are told
//!   apart by sonata_backend::devicevolume::recently_set (the remember half
//!   knows when it was last called, in this same process: the menu bar's).

use serde_json::{json, Map, Value};
use std::time::Duration;

pub const NAME: &str = sonata_backend::devicevolume::NAME;
pub const KINDS: [&str; 2] = ["sink", "source"];
/// after WirePlumber has restored its own
pub const RESTORE_AFTER: Duration = Duration::from_millis(1500);
/// a change this soon after Sonata set one is Sonata's
pub const LOCK_GRACE_S: f64 = 2.0;

/// How a device's volume is set: (kind, device name, percent).
pub type SetVolume<'a> = &'a dyn Fn(&str, &str, i64);
/// A node's (name, percent) from (kind, index).
pub type NodeState<'a> = &'a dyn Fn(&str, i64) -> Option<(String, i64)>;

fn pactl_set(kind: &str, name: &str, pct: i64) {
    sonata_backend::devicevolume::pactl(&[&format!("set-{kind}-volume"), name, &format!("{pct}%")]);
}

/// devicevolumes.json: {kind: {device name: percent}}, both kinds there.
pub fn saved() -> Map<String, Value> {
    let data = sonata_config::load(NAME, &json!({"sink": {}, "source": {}}));
    KINDS.iter().map(|k| (k.to_string(), Value::Object(data.get(*k).and_then(Value::as_object).cloned().unwrap_or_default()))).collect()
}

fn volume_of(data: &Map<String, Value>, kind: &str, name: &str) -> Option<i64> {
    let v = data.get(kind)?.get(name)?;
    v.as_i64().or_else(|| v.as_f64().map(|f| f as i64))
}

/// The volume remembered for that device, None if none.
pub fn wanted(kind: &str, name: &str) -> Option<i64> {
    if !sonata_backend::devicevolume::usable(name) {
        return None;
    }
    volume_of(&saved(), kind, name)
}

/// Blocking (a worker thread): the device's volume set again after `wait`,
/// if one was remembered. True: set.
pub fn restore(kind: &str, name: &str, wait: Duration, set_volume: Option<SetVolume>) -> bool {
    let Some(pct) = wanted(kind, name) else { return false };
    std::thread::sleep(wait);
    set_volume.unwrap_or(&pactl_set)(kind, name, pct);
    true
}

/// audiofollow's "new" events: restore() in its own thread (it waits for
/// WirePlumber to restore its own first), only when there is something to
/// put back.
pub fn restore_soon(kind: &str, name: &str) {
    if wanted(kind, name).is_some() {
        let (kind, name) = (kind.to_string(), name.to_string());
        std::thread::spawn(move || restore(&kind, &name, RESTORE_AFTER, None));
    }
}

// -- "Don't let apps change the volume" ----------------------------------------------------
pub fn locked() -> bool {
    crate::py::truthy(sonata_config::load("sounds", &sonata_backend::sounds::defaults()).get("lock_volumes"))
}

/// (name, percent) of sink/source #index -- its loudest channel -- in
/// `pactl -f json list sinks|sources`, else None.
pub fn node_volume_of(json_out: &str, index: i64) -> Option<(String, i64)> {
    let nodes: Value = serde_json::from_str(json_out).ok()?;
    let n = nodes.as_array()?.iter().find(|n| n.get("index").and_then(Value::as_i64) == Some(index))?;
    let pcts: Vec<i64> = n
        .get("volume")
        .and_then(Value::as_object)
        .map(|chs| chs.values().filter_map(|ch| ch.get("value_percent").and_then(Value::as_str).and_then(|p| p.trim_end_matches('%').parse().ok())).collect())
        .unwrap_or_default();
    let max = pcts.into_iter().max()?;
    Some((n.get("name").and_then(Value::as_str).unwrap_or("").to_string(), max))
}

pub fn node_volume(kind: &str, index: i64) -> Option<(String, i64)> {
    node_volume_of(&sonata_backend::devicevolume::pactl(&["-f", "json", "list", &format!("{kind}s")]), index)
}

/// Blocking: sink/source #index changed. With the lock on, a volume nobody
/// set from Sonata goes back to the device's own (its first volume seen
/// counts as its own when none was set yet). True: put back. `recent`:
/// Sonata set this kind just now (recently_set); `state`: the node's
/// (name, percent).
pub fn keep(kind: &str, index: i64, recent: &dyn Fn(&str) -> bool, state: NodeState, set_volume: SetVolume) -> bool {
    if !KINDS.contains(&kind) || !locked() || recent(kind) {
        return false; // (recent: Sonata's own change)
    }
    let Some((name, pct)) = state(kind, index) else { return false };
    if !sonata_backend::devicevolume::usable(&name) {
        return false;
    }
    let Some(want) = wanted(kind, &name) else {
        // first seen: this is its volume
        let mut data = saved();
        if let Some(Value::Object(m)) = data.get_mut(kind) {
            m.insert(name, json!(pct));
        }
        let _ = sonata_config::save(NAME, &data);
        return false;
    };
    if want == pct {
        return false;
    }
    set_volume(kind, &name, want);
    true
}

/// audiofollow's "change" events: keep() as the menu bar runs it. (It once
/// waited for a recent-check seam nobody filled, and the lock never did
/// anything in the Rust session.)
pub fn keep_now(kind: &str, index: i64) -> bool {
    keep(kind, index, &sonata_backend::devicevolume::recently_set, &node_volume, &pactl_set)
}

#[cfg(test)]
mod tests {
    //! tests/test_devicevolume.py's restore and lock halves.
    use super::*;
    use std::cell::RefCell;

    fn setup(lock: bool) {
        let _ = sonata_config::save("sounds", json!({"lock_volumes": lock}).as_object().unwrap());
        let _ = sonata_config::save(NAME, json!({"sink": {}, "source": {"bluez_input.AA": 100}}).as_object().unwrap());
    }

    /// Vini: the headset's mic at the maximum kept resetting -- a device
    /// that comes back gets its own volume again.
    #[test]
    fn test_restored_when_it_comes_back() {
        let _l = crate::test_lock();
        setup(false);
        let calls = RefCell::new(vec![]);
        let set = |k: &str, n: &str, p: i64| calls.borrow_mut().push((k.to_string(), n.to_string(), p));
        assert!(restore("source", "bluez_input.AA", Duration::ZERO, Some(&set)));
        assert_eq!(*calls.borrow(), [("source".to_string(), "bluez_input.AA".to_string(), 100)]);
        assert!(!restore("source", "other", Duration::ZERO, Some(&set)));
        assert_eq!(wanted("source", "x.monitor"), None);
    }

    struct Lock {
        calls: RefCell<Vec<(String, String, i64)>>,
    }

    impl Lock {
        fn keep(&self, pct: i64, name: &str, recent: bool) -> bool {
            let set = |k: &str, n: &str, p: i64| self.calls.borrow_mut().push((k.into(), n.into(), p));
            keep("source", 5, &|_| recent, &|_, _| Some((name.to_string(), pct)), &set)
        }
    }

    /// Settings > Sound > "Don't let apps change the volume" did nothing in
    /// the Rust session: an app turning the mic down stayed down.
    #[test]
    fn test_an_app_turning_it_down_is_put_back() {
        let _l = crate::test_lock();
        setup(true);
        let t = Lock { calls: RefCell::default() };
        assert!(t.keep(62, "bluez_input.AA", false));
        assert_eq!(*t.calls.borrow(), [("source".to_string(), "bluez_input.AA".to_string(), 100)]);
        assert!(!t.keep(100, "bluez_input.AA", false)); // our own put-back: nothing more
    }

    #[test]
    fn test_off_by_default_and_when_off() {
        let _l = crate::test_lock();
        assert_eq!(sonata_backend::sounds::defaults()["lock_volumes"], json!(false));
        setup(false);
        let t = Lock { calls: RefCell::default() };
        assert!(!t.keep(10, "bluez_input.AA", false));
        assert!(t.calls.borrow().is_empty());
    }

    #[test]
    fn test_sonata_own_changes_are_not_fought() {
        let _l = crate::test_lock();
        setup(true);
        let t = Lock { calls: RefCell::default() };
        assert!(!t.keep(50, "bluez_input.AA", true));
        assert!(t.calls.borrow().is_empty());
    }

    /// "Don't let apps change the volume" on, an app turned the microphone
    /// down and it stayed down in the Rust session: keep_now() waited for
    /// a recent-check nobody set and put nothing back. Now Sonata's own
    /// change (remember()) is the only one left alone.
    #[test]
    fn test_lock_never_put_back_an_app_change() {
        let _l = crate::test_lock();
        setup(true);
        let recent = |k: &str| sonata_backend::devicevolume::recently_set(k);
        let state = |_: &str, _: i64| Some(("bluez_input.AA".to_string(), 30));
        let set = RefCell::new(Vec::new());
        let set_volume = |k: &str, n: &str, p: i64| set.borrow_mut().push((k.to_string(), n.to_string(), p));
        // the sink was Sonata's just now; the source wasn't
        sonata_backend::devicevolume::remember("sink", 40);
        assert!(keep("source", 5, &recent, &state, &set_volume));
        assert_eq!(set.borrow().last().unwrap().2, 100);
        assert!(!keep("sink", 5, &recent, &state, &set_volume));
        assert_eq!(set.borrow().len(), 1);
    }

    #[test]
    fn test_first_seen_volume_is_its_own() {
        let _l = crate::test_lock();
        setup(true);
        let t = Lock { calls: RefCell::default() };
        assert!(!t.keep(70, "alsa_input.laptop", false));
        assert_eq!(wanted("source", "alsa_input.laptop"), Some(70));
        assert_eq!(wanted("source", "bluez_input.AA"), Some(100)); // the others kept
        assert!(t.keep(30, "alsa_input.laptop", false));
        assert_eq!(t.calls.borrow().last().unwrap().2, 70);
        setup(false);
    }

    #[test]
    fn node_volume_loudest_channel() {
        let out = r#"[{"index": 5, "name": "bluez_input.AA", "volume": {"front-left": {"value_percent": "62%"}, "front-right": {"value_percent": "64%"}}}, {"index": 6, "name": "x", "volume": {}}]"#;
        assert_eq!(node_volume_of(out, 5), Some(("bluez_input.AA".into(), 64)));
        assert_eq!(node_volume_of(out, 6), None);
        assert_eq!(node_volume_of("junk", 5), None);
    }
}
