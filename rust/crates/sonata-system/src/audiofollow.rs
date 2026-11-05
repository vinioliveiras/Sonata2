//! New headphones and headsets are used right away (backend/audiofollow.py;
//! Vini, like macOS): a USB or Bluetooth device that appears becomes the
//! default output -- and its microphone the default input. When it goes,
//! the device used before comes back.
//!
//! Watched with `pactl subscribe` in the menu bar's process; on/off:
//! sounds.json "follow_new_devices" (Settings > Sound).

use serde_json::Value;
use std::collections::HashMap;
use std::sync::{Arc, Mutex};

pub const EXTERNAL_BUSES: [&str; 2] = ["bluetooth", "usb"];

fn event_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"Event '(new|remove|change)' on (sink|source) #(\d+)").unwrap())
}

fn pactl(args: &[&str]) -> String {
    sonata_backend::devicevolume::pactl(args)
}

/// (name, properties) of sink/source #index, else None.
pub fn node_info(kind: &str, index: i64) -> Option<(String, Value)> {
    let nodes: Value = serde_json::from_str(&pactl(&["-f", "json", "list", &format!("{kind}s")])).ok()?;
    let n = nodes.as_array()?.iter().find(|n| n.get("index").and_then(Value::as_i64) == Some(index))?;
    Some((n.get("name").and_then(Value::as_str).unwrap_or("").to_string(), n.get("properties").cloned().unwrap_or(Value::Null)))
}

/// A device you plug in or pair (USB, Bluetooth) -- not the laptop's own
/// card, a monitor of an output, or Sonata's equalizer.
pub fn external(name: &str, props: &Value) -> bool {
    if name.is_empty() || name.ends_with(".monitor") || name.starts_with("sonata-eq") {
        return false;
    }
    let get = |k: &str| props.get(k).and_then(Value::as_str).unwrap_or("").to_lowercase();
    EXTERNAL_BUSES.contains(&get("device.bus").as_str()) || get("device.api") == "bluez5" || name.starts_with("bluez_")
}

type InfoFn = Box<dyn Fn(&str, i64) -> Option<(String, Value)> + Send + Sync>;
type GetFn = Box<dyn Fn(&str) -> String + Send + Sync>;
type SetFn = Box<dyn Fn(&str, &str) + Send + Sync>;
type OnFn = Box<dyn Fn() -> bool + Send + Sync>;
type NameHook = Box<dyn Fn(&str, &str) + Send + Sync>;
type IndexHook = Box<dyn Fn(&str, i64) + Send + Sync>;

pub struct AudioFollow {
    pub info: InfoFn,
    pub get_default: GetFn,
    pub set_default: SetFn,
    pub enabled: OnFn,
    /// a device that came back: its own volume again (devicevolume's restore)
    pub restore: NameHook,
    /// a device changed: an app turned it down? (devicevolume's keep)
    pub keep: IndexHook,
    /// (kind, index) -> (name, the default before it)
    pub ours: HashMap<(String, i64), (String, String)>,
}

/// sounds.json's switch.
pub fn enabled() -> bool {
    sonata_config::load("sounds", &sonata_backend::sounds::defaults()).get("follow_new_devices").and_then(Value::as_bool).unwrap_or(true)
}

impl Default for AudioFollow {
    fn default() -> Self {
        Self {
            info: Box::new(node_info),
            get_default: Box::new(|kind| pactl(&[&format!("get-default-{kind}")]).trim().to_string()),
            set_default: Box::new(|kind, name| {
                pactl(&[&format!("set-default-{kind}"), name]);
            }),
            enabled: Box::new(enabled),
            // its own volume again, in a thread of its own (it waits for
            // WirePlumber to restore its own first)
            restore: Box::new(crate::devicevolume::restore_soon),
            // "Don't let apps change the volume" (devicevolume.rs)
            keep: Box::new(|kind, index| {
                crate::devicevolume::keep_now(kind, index);
            }),
            ours: HashMap::new(),
        }
    }
}

impl AudioFollow {
    pub fn event(&mut self, line: &str) {
        let Some(m) = event_re().captures(line) else { return };
        let (what, kind) = (&m[1], &m[2]);
        let Ok(index) = m[3].parse::<i64>() else { return };
        match what {
            "change" => (self.keep)(kind, index),
            "new" => {
                self.added(kind, index);
                if let Some((name, _)) = (self.info)(kind, index) {
                    (self.restore)(kind, &name);
                }
            }
            _ => self.removed(kind, index),
        }
    }

    pub fn added(&mut self, kind: &str, index: i64) {
        if !(self.enabled)() {
            return;
        }
        let Some((name, props)) = (self.info)(kind, index) else { return };
        if !external(&name, &props) {
            return;
        }
        let before = (self.get_default)(kind);
        if before == name {
            return;
        }
        self.ours.insert((kind.into(), index), (name.clone(), before));
        (self.set_default)(kind, &name);
    }

    pub fn removed(&mut self, kind: &str, index: i64) {
        let Some((name, before)) = self.ours.remove(&(kind.to_string(), index)) else { return };
        // one of ours used this one as its "before": it goes back further
        for ((k, _), (_, prev)) in self.ours.iter_mut() {
            if k == kind && *prev == name {
                *prev = before.clone();
            }
        }
        // only if it was in use (you may have chosen another meanwhile)
        if !before.is_empty() && (self.enabled)() {
            let cur = (self.get_default)(kind);
            if cur == name || cur.is_empty() {
                (self.set_default)(kind, &before); // (gone too: WirePlumber picks one itself)
            }
        }
    }
}

/// Events from the shared `pactl subscribe` reader; None without pactl.
/// Stop the returned handle to end it.
pub fn start() -> Option<crate::pactl_watch::Handle> {
    let follow = Arc::new(Mutex::new(AudioFollow::default()));
    crate::pactl_watch::watch(move |text| {
        if event_re().is_match(text) {
            let (f, line) = (follow.clone(), text.to_string());
            // pactl calls off the main loop, events one at a time, in order
            crate::run_async(move || f.lock().unwrap_or_else(|e| e.into_inner()).event(&line), |_| {});
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    type Nodes = Arc<Mutex<HashMap<(String, i64), (String, Value)>>>;
    type Defaults = Arc<Mutex<HashMap<String, String>>>;

    struct T {
        f: AudioFollow,
        nodes: Nodes,
        defaults: Defaults,
        on: Arc<Mutex<bool>>,
    }

    fn setup() -> T {
        let defaults: Defaults = Arc::new(Mutex::new(HashMap::from([("sink".into(), "alsa_output.pci.analog-stereo".into()), ("source".into(), "alsa_input.pci.analog-stereo".into())])));
        let nodes: Nodes = Arc::default();
        let on = Arc::new(Mutex::new(true));
        let (n, d1, d2, o) = (nodes.clone(), defaults.clone(), defaults.clone(), on.clone());
        let f = AudioFollow {
            info: Box::new(move |k, i| n.lock().unwrap().get(&(k.to_string(), i)).cloned()),
            get_default: Box::new(move |k| d1.lock().unwrap()[k].clone()),
            set_default: Box::new(move |k, name| {
                d2.lock().unwrap().insert(k.into(), name.into());
            }),
            enabled: Box::new(move || *o.lock().unwrap()),
            ..Default::default()
        };
        T { f, nodes, defaults, on }
    }

    impl T {
        fn plug(&mut self, kind: &str, i: i64, name: &str, props: Value) {
            self.nodes.lock().unwrap().insert((kind.into(), i), (name.into(), props));
            self.f.event(&format!("Event 'new' on {kind} #{i}"));
        }
        fn get(&self, kind: &str) -> String {
            self.defaults.lock().unwrap()[kind].clone()
        }
    }

    /// Vini: headphones and their microphone (Bluetooth too) become the
    /// output and input as soon as they're connected; when they go, the
    /// device used before comes back.
    #[test]
    fn bluetooth_headset_output_and_mic() {
        let mut t = setup();
        t.plug("sink", 80, "bluez_output.AA_BB.1", json!({"device.api": "bluez5"}));
        t.plug("source", 81, "bluez_input.AA_BB.0", json!({"device.api": "bluez5"}));
        assert_eq!((t.get("sink"), t.get("source")), ("bluez_output.AA_BB.1".into(), "bluez_input.AA_BB.0".into()));
        t.f.event("Event 'remove' on sink #80");
        t.f.event("Event 'remove' on source #81");
        assert_eq!((t.get("sink"), t.get("source")), ("alsa_output.pci.analog-stereo".into(), "alsa_input.pci.analog-stereo".into()));
    }

    #[test]
    fn usb_headset_and_what_is_left_alone() {
        let mut t = setup();
        t.plug("sink", 90, "alsa_output.usb-Logi_H390.analog-stereo", json!({"device.bus": "usb"}));
        assert_eq!(t.get("sink"), "alsa_output.usb-Logi_H390.analog-stereo");
        t.plug("source", 91, "alsa_output.usb-Logi_H390.analog-stereo.monitor", json!({"device.bus": "usb"}));
        t.plug("sink", 92, "alsa_output.pci-0000_01_00.1.hdmi-stereo", json!({"device.bus": "pci"}));
        t.plug("sink", 93, "sonata-eq-sink", json!({"device.bus": "usb"}));
        assert_eq!(t.get("sink"), "alsa_output.usb-Logi_H390.analog-stereo");
        assert_eq!(t.get("source"), "alsa_input.pci.analog-stereo");
        t.f.event("Event 'change' on sink #90"); // other events: nothing
        t.f.event("Event 'remove' on sink #92"); // not ours: nothing
        assert_eq!(t.get("sink"), "alsa_output.usb-Logi_H390.analog-stereo");
    }

    /// Each device keeps its own volume: a device that comes back was
    /// never given it again in the Rust session (the hooks did nothing).
    /// "new" restores it, even with following off; "change" asks keep().
    #[test]
    fn test_device_volume_never_restored() {
        let seen: Arc<Mutex<Vec<String>>> = Arc::default();
        let (s1, s2) = (seen.clone(), seen.clone());
        let mut f = AudioFollow {
            info: Box::new(|_, _| Some(("bluez_input.AA".into(), json!({"device.bus": "bluetooth"})))),
            get_default: Box::new(|_| String::new()),
            set_default: Box::new(|_, _| {}),
            enabled: Box::new(|| false),
            restore: Box::new(move |k, n| s1.lock().unwrap().push(format!("restore {k} {n}"))),
            keep: Box::new(move |k, i| s2.lock().unwrap().push(format!("keep {k} {i}"))),
            ours: HashMap::new(),
        };
        f.event("Event 'new' on source #77");
        f.event("Event 'change' on source #5");
        assert_eq!(*seen.lock().unwrap(), ["restore source bluez_input.AA", "keep source 5"]);
        // the real ones are wired by default
        let d = AudioFollow::default();
        (d.restore)("source", "nothing-remembered");
        (d.keep)("source", -1);
    }

    #[test]
    fn two_in_a_row_go_back_one_at_a_time() {
        let mut t = setup();
        t.plug("sink", 1, "bluez_output.A", json!({"device.api": "bluez5"}));
        t.plug("sink", 2, "alsa_output.usb-X", json!({"device.bus": "usb"}));
        t.f.event("Event 'remove' on sink #2");
        assert_eq!(t.get("sink"), "bluez_output.A");
        t.f.event("Event 'remove' on sink #1");
        assert_eq!(t.get("sink"), "alsa_output.pci.analog-stereo");
    }

    #[test]
    fn first_one_removed_first() {
        let mut t = setup();
        t.plug("sink", 1, "bluez_output.A", json!({"device.api": "bluez5"}));
        t.plug("sink", 2, "alsa_output.usb-X", json!({"device.bus": "usb"}));
        t.f.event("Event 'remove' on sink #1"); // not in use: nothing changes
        assert_eq!(t.get("sink"), "alsa_output.usb-X");
        t.f.event("Event 'remove' on sink #2");
        assert_eq!(t.get("sink"), "alsa_output.pci.analog-stereo");
    }

    #[test]
    fn your_own_choice_is_kept_and_off_in_settings() {
        let mut t = setup();
        t.plug("sink", 1, "bluez_output.A", json!({"device.api": "bluez5"}));
        t.defaults.lock().unwrap().insert("sink".into(), "alsa_output.pci.hdmi".into()); // chosen meanwhile
        t.f.event("Event 'remove' on sink #1");
        assert_eq!(t.get("sink"), "alsa_output.pci.hdmi");
        let mut t = setup();
        *t.on.lock().unwrap() = false;
        t.plug("sink", 80, "bluez_output.AA", json!({"device.api": "bluez5"}));
        assert_eq!(t.get("sink"), "alsa_output.pci.analog-stereo");
        assert_eq!(sonata_backend::sounds::defaults()["follow_new_devices"], json!(true));
    }
}
