//! Graphic equalizer per audio output (backend/equalizer.py; Settings >
//! Sound).
//!
//! Ten bands (32 Hz .. 16 kHz, +/-12 dB). Each output -- a port of a card
//! ("Speakers", "Headphones") or a device of its own -- keeps its own curve
//! in equalizer.json, keyed like system::AudioDevice.key ("sink|port"), and
//! the curve follows the output in use.
//!
//! How: a PipeWire filter-chain per sink, as WirePlumber "smart filters"
//! (filter.smart, WirePlumber >= 0.5): WirePlumber puts the filter in front
//! of its sink by itself, the sink stays the default output and its volume
//! the hardware one. The chains run in their own `pipewire -c` process
//! (started by the menu bar's services); band gains change live with
//! `pw-cli set-param`, no restart, no audio gap.

use serde_json::{json, Map, Value};
use sonata_i18n::n_;
use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::rc::Rc;
use std::time::Duration;

pub const BANDS: [u32; 10] = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
pub const LABELS: [&str; 10] = ["32", "64", "125", "250", "500", "1K", "2K", "4K", "8K", "16K"];
pub const MAX_DB: f64 = 12.0;
pub const FLAT: [f64; 10] = [0.0; 10];
/// macOS Music's presets (the ones people know), dB per band; names
/// translated where shown
pub const PRESETS: [(&str, [f64; 10]); 20] = [
    (n_!("Flat"), FLAT),
    (n_!("Acoustic"), [5.0, 5.0, 4.0, 1.0, 2.0, 2.0, 3.5, 4.0, 3.5, 2.0]),
    (n_!("Bass Booster"), [5.5, 4.5, 3.5, 2.5, 1.5, 0.0, 0.0, 0.0, 0.0, 0.0]),
    (n_!("Bass Reducer"), [-5.5, -4.5, -3.5, -2.5, -1.5, 0.0, 0.0, 0.0, 0.0, 0.0]),
    (n_!("Classical"), [5.0, 4.0, 3.5, 3.0, -1.5, -1.5, 0.0, 2.0, 3.0, 4.0]),
    (n_!("Dance"), [3.5, 6.5, 5.0, 0.0, 2.0, 3.5, 5.0, 4.5, 3.5, 0.0]),
    (n_!("Electronic"), [4.5, 4.0, 1.5, 0.0, -2.0, 2.0, 1.0, 1.5, 4.0, 5.0]),
    (n_!("Hip-Hop"), [5.0, 4.5, 1.5, 3.0, -1.0, -1.0, 1.5, -0.5, 2.0, 3.0]),
    (n_!("Jazz"), [4.0, 3.0, 1.5, 2.0, -1.5, -1.5, 0.0, 1.5, 3.0, 3.5]),
    (n_!("Loudness"), [6.0, 4.0, 0.0, 0.0, -2.0, 0.0, -1.0, -5.0, 5.0, 1.0]),
    (n_!("Lounge"), [-3.0, -1.5, -0.5, 1.5, 4.0, 2.5, 0.0, -1.5, 2.0, 1.0]),
    (n_!("Piano"), [3.0, 2.0, 0.0, 2.5, 3.0, 1.5, 3.5, 4.5, 3.0, 3.5]),
    (n_!("Pop"), [-1.5, -1.0, 0.0, 2.0, 4.0, 4.0, 2.0, 0.0, -1.0, -1.5]),
    (n_!("R&B"), [2.5, 7.0, 5.5, 1.5, -2.5, -1.5, 2.5, 3.0, 3.0, 4.0]),
    (n_!("Rock"), [5.0, 4.0, 3.0, 1.5, -0.5, -1.0, 0.5, 2.5, 3.5, 4.5]),
    (n_!("Small Speakers"), [5.5, 4.0, 3.5, 2.5, 1.0, 0.0, -1.0, -2.0, -3.0, -4.0]),
    (n_!("Spoken Word"), [-3.5, -0.5, 0.0, 0.5, 3.5, 4.5, 4.5, 4.0, 2.0, 0.0]),
    (n_!("Treble Booster"), [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 2.5, 3.5, 4.5, 5.5]),
    (n_!("Treble Reducer"), [0.0, 0.0, 0.0, 0.0, 0.0, -1.0, -2.5, -3.5, -4.5, -5.5]),
    (n_!("Vocal Booster"), [-1.5, -3.0, -3.0, 1.5, 3.5, 3.5, 3.0, 1.5, 0.0, -1.5]),
];

/// The presets, in order (names in English: translate where shown).
pub fn presets() -> impl Iterator<Item = (&'static str, [f64; 10])> {
    PRESETS.iter().copied()
}

/// node names; system.rs hides them from the output lists
pub const PREFIX: &str = "sonata-eq";

pub fn defaults() -> Value {
    json!({"outputs": {}})
}

/// pipewire -c file of the chains
pub fn run_conf() -> PathBuf {
    glib::user_runtime_dir().join("sonata2-equalizer.conf")
}

/// the chain process's own output (errors loading the chain, WirePlumber links)
pub fn log_path() -> PathBuf {
    sonata_session::logs::log_dir().join("equalizer.log")
}

/// Routine lines: detailed logs only; errors always.
fn log(text: &str, error: bool) {
    if error || sonata_session::logs::verbose() {
        eprintln!("equalizer: {text}");
    }
}

// -- settings (Settings app) ----------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct Curve {
    pub on: bool,
    pub preset: String,
    pub gains: Vec<f64>,
}

impl Curve {
    fn to_json(&self) -> Value {
        json!({"on": self.on, "preset": self.preset, "gains": self.gains})
    }
}

fn outputs() -> Map<String, Value> {
    sonata_config::load("equalizer", &defaults()).get("outputs").and_then(Value::as_object).cloned().unwrap_or_default()
}

fn curve_of(out: Option<&Value>) -> Curve {
    let empty = json!({});
    let out = out.filter(|o| o.is_object()).unwrap_or(&empty);
    // float() and bool() as curve() reads them: "1.5" is a gain, 1 is on
    let mut gains: Vec<f64> = out.get("gains").and_then(Value::as_array).filter(|a| !a.is_empty()).map(|a| a.iter().map(|g| crate::py::float(g).unwrap_or(0.0)).collect()).unwrap_or_else(|| FLAT.to_vec());
    gains.truncate(BANDS.len());
    gains.resize(BANDS.len(), 0.0);
    Curve { on: crate::py::truthy(out.get("on")), preset: out.get("preset").and_then(Value::as_str).unwrap_or("Flat").to_string(), gains }
}

pub fn curve(key: &str) -> Curve {
    curve_of(outputs().get(key))
}

pub fn set_curve(key: &str, on: Option<bool>, preset: Option<&str>, gains: Option<&[f64]>) {
    let mut cfg = sonata_config::load("equalizer", &defaults());
    let mut cur = curve(key);
    if let Some(o) = on {
        cur.on = o;
    }
    if let Some(p) = preset {
        cur.preset = p.to_string();
        if let Some((_, g)) = presets().find(|(n, _)| *n == p) {
            cur.gains = g.to_vec();
        }
    }
    if let Some(g) = gains {
        cur.gains = g.iter().map(|x| crate::py::round1(*x).clamp(-MAX_DB, MAX_DB)).collect();
        cur.preset = presets().find(|(_, p)| p[..] == cur.gains[..]).map(|(n, _)| n.to_string()).unwrap_or_else(|| "Custom".into());
    }
    let mut outs = cfg.get("outputs").and_then(Value::as_object).cloned().unwrap_or_default();
    outs.insert(key.into(), cur.to_json());
    cfg.insert("outputs".into(), Value::Object(outs));
    let _ = sonata_config::save("equalizer", &cfg);
}

pub fn available() -> bool {
    ["pipewire", "pw-cli", "pactl"].iter().all(|t| sonata_backend::run::which(t).is_some())
}

// -- the filter chains ------------------------------------------------------------------------
pub fn slug(sink: &str) -> String {
    crate::mixer::slug(sink)
}

/// Python's repr of a float ("32.0", "1.1")
fn pyf(x: f64) -> String {
    if x.fract() == 0.0 { format!("{x:.1}") } else { format!("{x}") }
}

fn graph(gains: &[f64]) -> String {
    let (mut nodes, mut links) = (Vec::new(), Vec::new());
    for (i, f) in BANDS.iter().enumerate() {
        let label = if i == 0 { "bq_lowshelf" } else if i == BANDS.len() - 1 { "bq_highshelf" } else { "bq_peaking" };
        let q = if label != "bq_peaking" { 0.7 } else { 1.1 };
        nodes.push(format!(r#"{{ type = builtin name = b{i} label = {label} control = {{ "Freq" = {} "Q" = {} "Gain" = {:.1} }} }}"#, pyf(*f as f64), pyf(q), gains[i]));
        if i > 0 {
            links.push(format!(r#"{{ output = "b{}:Out" input = "b{i}:In" }}"#, i - 1));
        }
    }
    format!("{{ nodes = [ {} ] links = [ {} ] }}", nodes.join(" "), links.join(" "))
}

/// pipewire -c file: one smart filter chain per sink name (-> its gains).
pub fn conf_text(sinks: &BTreeMap<String, Vec<f64>>) -> String {
    let mods: Vec<String> = sinks
        .iter()
        .map(|(sink, gains)| {
            let s = slug(sink);
            format!(
                "  {{ name = libpipewire-module-filter-chain
    args = {{
      node.description = \"Equalizer\"
      media.name = \"Equalizer\"
      filter.graph = {}
      audio.channels = 2
      audio.position = [ FL FR ]
      capture.props = {{
        node.name = \"{PREFIX}.{s}\"
        media.class = Audio/Sink
        filter.smart = true
        filter.smart.name = \"{PREFIX}.{s}\"
        filter.smart.target = {{ node.name = \"{sink}\" }}
      }}
      playback.props = {{
        node.name = \"{PREFIX}-out.{s}\"
        node.passive = true
      }}
    }}
  }}",
                graph(gains)
            )
        })
        .collect();
    format!(
        "context.properties = {{ log.level = 2 }}\ncontext.spa-libs = {{ audio.convert.* = audioconvert/libspa-audioconvert support.* = support/libspa-support }}\ncontext.modules = [\n  {{ name = libpipewire-module-rt flags = [ ifexists nofail ] }}\n  {{ name = libpipewire-module-protocol-native }}\n  {{ name = libpipewire-module-client-node }}\n  {{ name = libpipewire-module-adapter }}\n{}\n]\n",
        mods.join("\n")
    )
}

fn run(cmd: &[&str], timeout: u64) -> (i32, String) {
    let (rc, out) = sonata_backend::run::run(cmd, timeout);
    if rc != 0 {
        let head: String = out.trim().chars().take(300).collect();
        log(&format!("{} -> {rc} {head}", cmd[..cmd.len().min(3)].join(" ")), true);
    }
    (rc, out)
}

/// (real outputs: (sink name, active port), pactl indexes of our own filter sinks)
pub fn sink_state_of(json_out: &str) -> (Vec<(String, String)>, Vec<i64>) {
    let nodes: Vec<Value> = serde_json::from_str(json_out).unwrap_or_default();
    let name = |n: &Value| n.get("name").and_then(Value::as_str).unwrap_or("").to_string();
    let real = nodes.iter().filter(|n| !name(n).is_empty() && !name(n).starts_with(PREFIX)).map(|n| (name(n), n.get("active_port").and_then(Value::as_str).unwrap_or("").to_string())).collect();
    let own = nodes.iter().filter(|n| name(n).starts_with(PREFIX)).filter_map(|n| n.get("index").and_then(Value::as_i64)).collect();
    (real, own)
}

type State = (Vec<(String, String)>, Vec<i64>, BTreeMap<String, Curve>);

/// Worker thread: the outputs, our filter sinks and every output's curve.
fn read_state() -> State {
    let (rc, out) = run(&["pactl", "-f", "json", "list", "sinks"], 4);
    let (sinks, own) = sink_state_of(if rc == 0 { &out } else { "[]" });
    let all = outputs();
    let curves = sinks.iter().map(|(s, p)| (format!("{s}|{p}"), curve_of(all.get(&format!("{s}|{p}"))))).collect();
    (sinks, own, curves)
}

fn sink_event_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"Event '(\w+)' on sink #(\d+)").unwrap())
}

/// A 'change' on one of our own filter sinks: every pw-cli set-param makes
/// one, and syncing on it ran pactl + pw-dump + pw-cli again, a loop in the
/// menu bar process. (new/remove still sync: a chain process that died
/// must be noticed.)
pub fn own_event(text: &str, own: &[i64]) -> bool {
    sink_event_re().captures(text).is_some_and(|m| &m[1] == "change" && m[2].parse::<i64>().is_ok_and(|i| own.contains(&i)))
}

/// The chains a plan needs, sorted (sink names whose output's equalizer is on).
pub fn wanted(sinks: &[(String, String)], curves: &BTreeMap<String, Curve>) -> BTreeMap<String, Vec<f64>> {
    sinks.iter().filter_map(|(s, p)| curves.get(&format!("{s}|{p}")).filter(|c| c.on).map(|c| (s.clone(), c.gains.clone()))).collect()
}

#[derive(Default)]
struct Inner {
    proc: RefCell<Option<Child>>,
    /// sink names the running process has
    chains: RefCell<Vec<String>>,
    events: RefCell<Option<crate::pactl_watch::Handle>>,
    src: RefCell<Option<glib::SourceId>>,
    links_src: RefCell<Option<glib::SourceId>>,
    cfg_mon: RefCell<Vec<gio::FileMonitor>>,
    /// pactl indexes of our filter sinks (their events are ours)
    own: RefCell<Vec<i64>>,
    /// sink name -> gains the chains should have
    plan: RefCell<BTreeMap<String, Vec<f64>>>,
    /// (chain pid, plan) last set with pw-cli: not again
    applied: RefCell<Option<(u32, BTreeMap<String, Vec<f64>>)>>,
    busy: Cell<bool>,
    again: Cell<bool>,
    applying: Cell<bool>,
    apply_again: Cell<bool>,
    /// how gains reach the filters (None: set_gains; tests count them)
    gains_fn: RefCell<Option<GainsFn>>,
}

type GainsFn = std::sync::Arc<dyn Fn(&BTreeMap<String, Vec<f64>>) -> bool + Send + Sync>;

/// The service: keeps a chain in front of every sink with an output whose
/// equalizer is on, and each chain on the active port's curve.
#[derive(Clone, Default)]
pub struct Equalizer(Rc<Inner>);

impl Equalizer {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn start(&self) {
        if !available() {
            return;
        }
        // a menu bar that restarted left its chains running
        kill_stale_chains(&run_conf());
        let w = Rc::downgrade(&self.0);
        *self.0.cfg_mon.borrow_mut() = sonata_config::watch("equalizer", move || {
            if let Some(i) = w.upgrade() {
                Equalizer(i).sync_soon();
            }
        });
        let w = Rc::downgrade(&self.0);
        // pactl subscribe: a sink appeared/changed (port, plugged headphones)
        *self.0.events.borrow_mut() = crate::pactl_watch::watch(move |text| {
            let Some(i) = w.upgrade() else { return };
            let me = Equalizer(i);
            if own_event(text, &me.0.own.borrow()) {
                return; // set-param on our filter: no sync (it fed itself)
            }
            if text.contains(" sink ") || text.contains(" server ") || text.contains(" card ") { // (pactl: "on server #-1")
                me.sync_soon();
            } else if text.contains("'new' on sink-input") && !me.0.chains.borrow().is_empty() {
                me.links_soon(); // an app started playing: does it go through us?
            }
        });
        self.sync();
    }

    fn links_soon(&self) {
        if self.0.links_src.borrow().is_some() {
            return;
        }
        let w = Rc::downgrade(&self.0);
        *self.0.links_src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(1500), move || {
            if let Some(i) = w.upgrade() {
                i.links_src.borrow_mut().take();
            }
            crate::run_async(log_links, |_| {});
        }));
    }

    pub fn sync_soon(&self) {
        if let Some(s) = self.0.src.borrow_mut().take() {
            sonata_ui::cancel(s);
        }
        let w = Rc::downgrade(&self.0);
        *self.0.src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(250), move || {
            if let Some(i) = w.upgrade() {
                i.src.borrow_mut().take();
                Equalizer(i).sync();
            }
        }));
    }

    /// pactl and the config read in a thread (it ran on the menu bar's main
    /// loop for every sink event); one at a time, the last one wins.
    pub fn sync(&self) {
        if self.0.busy.get() {
            self.0.again.set(true);
            return;
        }
        self.0.busy.set(true);
        let w = Rc::downgrade(&self.0);
        crate::run_async(read_state, move |state| {
            let Some(i) = w.upgrade() else { return };
            let me = Equalizer(i);
            me.0.busy.set(false);
            me.decide(state);
            if me.0.again.replace(false) {
                me.sync();
            }
        });
    }

    fn chain_running(&self) -> Option<u32> {
        let mut p = self.0.proc.borrow_mut();
        let child = p.as_mut()?;
        matches!(child.try_wait(), Ok(None)).then(|| child.id())
    }

    fn decide(&self, (sinks, own, curves): State) {
        *self.0.own.borrow_mut() = own;
        let plan = wanted(&sinks, &curves);
        let want: Vec<String> = plan.keys().cloned().collect();
        log(&format!("outputs {:?} on: {want:?}", sinks.iter().map(|(s, p)| format!("{s}|{p}")).collect::<Vec<_>>()), false);
        *self.0.plan.borrow_mut() = plan.clone();
        if want != *self.0.chains.borrow() || (!want.is_empty() && self.chain_running().is_none()) {
            self.restart(&plan);
            let w = Rc::downgrade(&self.0);
            // nodes need a moment
            glib::timeout_add_local_once(Duration::from_millis(700), move || {
                if let Some(i) = w.upgrade() {
                    Equalizer(i).apply();
                }
            });
            glib::timeout_add_local_once(Duration::from_millis(3000), || crate::run_async(log_links, |_| {}));
            return;
        }
        self.apply();
    }

    fn restart(&self, sinks: &BTreeMap<String, Vec<f64>>) {
        if let Some(mut p) = self.0.proc.borrow_mut().take() {
            if matches!(p.try_wait(), Ok(None)) {
                unsafe { libc::kill(p.id() as i32, libc::SIGTERM) };
                let deadline = std::time::Instant::now() + Duration::from_secs(2);
                while matches!(p.try_wait(), Ok(None)) && std::time::Instant::now() < deadline {
                    std::thread::sleep(Duration::from_millis(20));
                }
                let _ = p.kill();
                let _ = p.wait();
            }
        }
        *self.0.chains.borrow_mut() = sinks.keys().cloned().collect();
        *self.0.applied.borrow_mut() = None;
        kill_stale_chains(&run_conf());
        if sinks.is_empty() {
            return;
        }
        let conf = run_conf();
        if std::fs::write(&conf, conf_text(sinks)).is_err() {
            log("can't write the chain's config", true);
            return;
        }
        let lp = log_path();
        if let Some(d) = lp.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let out = std::fs::File::create(&lp).ok();
        let err = out.as_ref().and_then(|f| f.try_clone().ok());
        let mut cmd = Command::new("pipewire");
        cmd.arg("-c").arg(&conf).stdin(Stdio::null());
        cmd.stdout(out.map(Stdio::from).unwrap_or(Stdio::null())).stderr(err.map(Stdio::from).unwrap_or(Stdio::null()));
        // its own session (start_new_session): it outlives nothing of ours by accident
        use std::os::unix::process::CommandExt;
        unsafe {
            cmd.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        match cmd.spawn() {
            Ok(c) => {
                log(&format!("chain process {} for {:?}", c.id(), sinks.keys().collect::<Vec<_>>()), false);
                *self.0.proc.borrow_mut() = Some(c);
            }
            Err(e) => log(&format!("can't start pipewire: {e}"), true),
        }
    }

    /// Gains of the plan onto the running filters (pw-dump + pw-cli, in a
    /// thread). Nothing when no equalizer is on, or when the same plan was
    /// already set on the same chain process.
    fn apply(&self) {
        let plan = self.0.plan.borrow().clone();
        let Some(pid) = self.chain_running() else { return };
        let key = (pid, plan.clone());
        if plan.is_empty() || self.0.applied.borrow().as_ref() == Some(&key) {
            return;
        }
        if self.0.applying.get() {
            self.0.apply_again.set(true);
            return;
        }
        self.0.applying.set(true);
        let w = Rc::downgrade(&self.0);
        let gains = self.0.gains_fn.borrow().clone();
        crate::run_async(
            move || gains.map_or_else(|| set_gains(&plan), |f| f(&plan)),
            move |ok| {
                let Some(i) = w.upgrade() else { return };
                i.applying.set(false);
                if ok {
                    *i.applied.borrow_mut() = Some(key);
                }
                if i.apply_again.replace(false) {
                    Equalizer(i).apply();
                }
            },
        );
    }

    pub fn stop(&self) {
        self.restart(&BTreeMap::new());
        if let Some(h) = self.0.events.borrow_mut().take() {
            h.stop();
        }
    }
}

/// Chain processes (`pipewire -c <conf>`) no Equalizer of this process
/// started: left by a menu bar that crashed or restarted (they run in their
/// own session). Streams linked to such a copy kept its old curve -- Chrome
/// and Spotify didn't follow the equalizer.
pub fn kill_stale_chains(conf: &std::path::Path) {
    let me = std::process::id();
    let Ok(dir) = std::fs::read_dir("/proc") else { return };
    for e in dir.flatten() {
        let Some(pid) = e.file_name().to_str().and_then(|s| s.parse::<u32>().ok()) else { continue };
        if pid == me {
            continue;
        }
        let Ok(raw) = std::fs::read(e.path().join("cmdline")) else { continue };
        let args: Vec<&[u8]> = raw.split(|b| *b == 0).collect();
        let base = args.first().map(|a| a.rsplit(|b| *b == b'/').next().unwrap_or(a)).unwrap_or(b"");
        if args.len() >= 3 && base == b"pipewire" && args[1] == b"-c" && args[2] == conf.as_os_str().as_encoded_bytes() && unsafe { libc::kill(pid as i32, libc::SIGTERM) } == 0 {
            log(&format!("stopped a stale chain process {pid}"), false);
        }
    }
}

/// Worker thread: plan's gains onto every copy of each sink's filter;
/// false when a filter node isn't there (yet), so the next sync tries again.
fn set_gains(plan: &BTreeMap<String, Vec<f64>>) -> bool {
    let ids = node_ids();
    let mut ok = true;
    for (sink, gains) in plan {
        let Some(nids) = ids.get(&format!("{PREFIX}.{}", slug(sink))) else {
            log(&format!("no filter node for {sink}"), false);
            ok = false;
            continue;
        };
        let params = pw_params(gains);
        // every copy (there should be one)
        for nid in nids {
            run(&["pw-cli", "set-param", &nid.to_string(), "Props", &params], 4);
        }
    }
    ok
}

/// pw-cli's Props for a curve.
pub fn pw_params(gains: &[f64]) -> String {
    let p: Vec<String> = gains.iter().enumerate().map(|(i, g)| format!("\"b{i}:Gain\" {g:.1}")).collect();
    format!("{{ params = [ {} ] }}", p.join(" "))
}

/// node.name -> ids of our filter nodes (pw-dump); normally one each.
pub fn node_ids_of(dump: &str) -> BTreeMap<String, Vec<i64>> {
    let objs: Vec<Value> = serde_json::from_str(dump).unwrap_or_default();
    let mut ids: BTreeMap<String, Vec<i64>> = BTreeMap::new();
    for o in &objs {
        let name = o.pointer("/info/props/node.name").and_then(Value::as_str).unwrap_or("");
        if o.get("type").and_then(Value::as_str).unwrap_or("").ends_with("Node") && name.starts_with(&format!("{PREFIX}.")) {
            if let Some(id) = o.get("id").and_then(Value::as_i64) {
                ids.entry(name.to_string()).or_default().push(id);
            }
        }
    }
    ids
}

fn node_ids() -> BTreeMap<String, Vec<i64>> {
    let (rc, out) = run(&["pw-dump", "-N"], 5);
    if rc == 0 { node_ids_of(&out) } else { BTreeMap::new() }
}

/// What WirePlumber linked to the chains (the log shows whether the sound
/// really goes through the equalizer).
fn log_links() {
    if !sonata_session::logs::verbose() {
        return;
    }
    let (_rc, ver) = run(&["wireplumber", "--version"], 4);
    log(&format!("wireplumber {}", ver.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(80).collect::<String>()), false);
    let (_rc, out) = run(&["pw-link", "-l"], 4);
    let (mut lines, mut keep) = (Vec::new(), 0);
    for ln in out.lines() {
        if ln.contains(PREFIX) {
            keep = 3;
        }
        if keep > 0 {
            lines.push(ln.trim().to_string());
            keep -= 1;
        }
    }
    log(&format!("links: {}", if lines.is_empty() { "none".into() } else { lines.join(" / ") }), false);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn curves_saved_per_output_and_presets_named() {
        let _l = crate::test_lock();
        let _ = sonata_config::save("equalizer", &Map::new());
        assert_eq!(curve("a|b"), Curve { on: false, preset: "Flat".into(), gains: FLAT.to_vec() });
        set_curve("a|b", Some(true), Some("Rock"), None);
        let c = curve("a|b");
        assert!(c.on);
        assert_eq!((c.preset.as_str(), c.gains[0]), ("Rock", 5.0));
        set_curve("a|b", None, None, Some(&[20.0, -20.0, 0.04, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]));
        let c = curve("a|b");
        assert_eq!((c.preset.as_str(), c.gains[..3].to_vec()), ("Custom", vec![12.0, -12.0, 0.0]));
        set_curve("a|b", None, None, Some(&PRESETS[2].1));
        assert_eq!(curve("a|b").preset, "Bass Booster");
        assert_eq!(presets().count(), 20);
    }

    /// A band dragged to 0.35 dB was saved as 0.4 where the Python saves
    /// 0.3 (round(x, 1) works on the float's exact value: 0.3499...), so
    /// the two builds named a curve differently.
    #[test]
    fn test_gain_rounded_unlike_python() {
        let _l = crate::test_lock();
        let _ = sonata_config::save("equalizer", &Map::new());
        set_curve("a|b", None, None, Some(&[0.35, -0.05, 0.15, 1.45, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]));
        assert_eq!(curve("a|b").gains[..4], [0.3, -0.1, 0.1, 1.4]);
    }

    /// equalizer.json written by hand or by an older Sonata: gains as text
    /// and "on" as 1 read as the Python reads them (float(), bool()).
    #[test]
    fn test_curve_written_as_text_read_as_flat() {
        let _l = crate::test_lock();
        let cfg = json!({"outputs": {"a|b": {"on": 1, "preset": "Custom", "gains": ["1.5", 2, "x"]}}});
        let _ = sonata_config::save("equalizer", cfg.as_object().unwrap());
        let c = curve("a|b");
        assert!(c.on);
        assert_eq!(c.gains[..4], [1.5, 2.0, 0.0, 0.0]);
        let _ = sonata_config::save("equalizer", &Map::new());
    }

    #[test]
    fn chain_config() {
        let mut sinks = BTreeMap::new();
        sinks.insert("alsa_output.pci-0000:00".to_string(), vec![1.5; 10]);
        let t = conf_text(&sinks);
        assert!(t.contains(r#"node.name = "sonata-eq.alsa_output.pci-0000_00""#));
        assert!(t.contains(r#"filter.smart.target = { node.name = "alsa_output.pci-0000:00" }"#));
        assert!(t.contains(r#"{ type = builtin name = b0 label = bq_lowshelf control = { "Freq" = 32.0 "Q" = 0.7 "Gain" = 1.5 } }"#));
        assert!(t.contains(r#"label = bq_peaking control = { "Freq" = 64.0 "Q" = 1.1 "Gain" = 1.5 }"#));
        assert!(t.contains(r#"{ output = "b0:Out" input = "b1:In" }"#));
        assert_eq!(pw_params(&[1.0, -2.25]), r#"{ params = [ "b0:Gain" 1.0 "b1:Gain" -2.2 ] }"#);
    }

    /// Our own filter's 'change' events (every set-param makes one) never
    /// start a sync: it fed itself, a loop in the menu bar process.
    #[test]
    fn own_events_ignored_and_state_read() {
        let (real, own) = sink_state_of(r#"[{"index": 3, "name": "alsa_output.pci", "active_port": "speaker"}, {"index": 9, "name": "sonata-eq.alsa_output.pci"}]"#);
        assert_eq!(real, [("alsa_output.pci".to_string(), "speaker".to_string())]);
        assert_eq!(own, [9]);
        assert!(own_event("Event 'change' on sink #9", &own));
        assert!(!own_event("Event 'new' on sink #9", &own));
        assert!(!own_event("Event 'change' on sink #3", &own));
        let mut curves = BTreeMap::new();
        curves.insert("alsa_output.pci|speaker".to_string(), Curve { on: true, preset: "Rock".into(), gains: vec![1.0; 10] });
        assert_eq!(wanted(&real, &curves).keys().collect::<Vec<_>>(), ["alsa_output.pci"]);
        curves.get_mut("alsa_output.pci|speaker").unwrap().on = false;
        assert!(wanted(&real, &curves).is_empty());
    }

    /// Gains reach every copy of a filter.
    #[test]
    fn node_ids_every_copy() {
        let dump = r#"[{"id": 40, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "sonata-eq.x"}}},
                      {"id": 41, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "sonata-eq.x"}}},
                      {"id": 42, "type": "PipeWire:Interface:Node", "info": {"props": {"node.name": "sonata-eq-out.x"}}},
                      {"id": 43, "type": "PipeWire:Interface:Link", "info": {"props": {"node.name": "sonata-eq.x"}}}]"#;
        assert_eq!(node_ids_of(dump).get("sonata-eq.x"), Some(&vec![40, 41]));
        assert_eq!(node_ids_of(dump).len(), 1);
    }

    /// tests/test_audio_watch_cpu.py: the same curve on the same chain
    /// process isn't set again (each set-param woke the menu bar), a moved
    /// one is; with every equalizer off nothing runs at all.
    #[test]
    fn same_gains_are_not_set_again_and_nothing_runs_when_off() {
        let calls = crate::on_main(|| {
            let eq = Equalizer::new();
            let count = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
            let c = count.clone();
            *eq.0.gains_fn.borrow_mut() = Some(std::sync::Arc::new(move |_| {
                c.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                true
            }));
            // a chain process already running for this output
            *eq.0.proc.borrow_mut() = Some(Command::new("sleep").arg("30").spawn().unwrap());
            *eq.0.chains.borrow_mut() = vec!["alsa_out".into()];
            let sinks = vec![("alsa_out".to_string(), "spk".to_string())];
            let curve = |g: f64, on: bool| BTreeMap::from([("alsa_out|spk".to_string(), Curve { on, preset: "Rock".into(), gains: vec![g; 10] })]);
            let mut seen = vec![];
            for _ in 0..3 {
                eq.decide((sinks.clone(), vec![], curve(1.0, true)));
                crate::spin(50);
            }
            seen.push(count.load(std::sync::atomic::Ordering::SeqCst));
            eq.decide((sinks.clone(), vec![], curve(2.0, true))); // moved in Settings: set again
            crate::spin(50);
            seen.push(count.load(std::sync::atomic::Ordering::SeqCst));
            if let Some(mut p) = eq.0.proc.borrow_mut().take() {
                let _ = p.kill();
                let _ = p.wait();
            }
            // every equalizer off, no chain: nothing is set
            let off = Equalizer::new();
            *off.0.gains_fn.borrow_mut() = eq.0.gains_fn.borrow().clone();
            off.decide((sinks, vec![], curve(1.0, false)));
            crate::spin(50);
            seen.push(count.load(std::sync::atomic::Ordering::SeqCst));
            seen
        });
        assert_eq!(calls, [1, 2, 2]);
    }

    /// Chrome/Spotify ignored the equalizer: chain processes left by a
    /// restarted menu bar kept their old curve, and streams linked to them.
    #[test]
    fn stale_chain_processes_are_stopped() {
        use std::os::unix::process::CommandExt;
        // a fake chain: argv "pipewire -c <conf>" (sh runs the conf as its script)
        let conf = "sleep 30; echo";
        let mut p = Command::new("sh").arg0("pipewire").args(["-c", conf]).spawn().unwrap();
        std::thread::sleep(Duration::from_millis(200));
        kill_stale_chains(std::path::Path::new(conf));
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        while matches!(p.try_wait(), Ok(None)) && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        let ended = !matches!(p.try_wait(), Ok(None));
        let _ = p.kill();
        let _ = p.wait();
        assert!(ended);
    }
}
