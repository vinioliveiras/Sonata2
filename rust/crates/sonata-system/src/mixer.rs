//! Volume per app (backend/mixer.py; the mixer macOS never had): every app
//! playing sound has its own level, kept for the next time it plays. Shown
//! in the menu bar's Sound menu and Control Center, under Output.
//!
//! PipeWire's pulse layer (pactl): each app's playback is a "sink input".
//! Levels are saved per app (mixer.json "volumes": key -> percent, "muted":
//! key -> bool), the key being the app's id, else its program, else its
//! name; MixerService (menu bar process) puts the saved level back on every
//! new stream of that app, so a level set once stays even after the app
//! quits. Sonata's own short sounds (pw-play, paplay...) and event sounds
//! are left out of the list (system_sound).

use serde_json::{json, Map, Value};
use sonata_backend::system::AudioDevice;
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, HashSet};
use std::rc::Rc;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

pub const NAME: &str = "mixer";
/// players Sonata starts for its own sounds (sounds.py, the alarm): never listed
pub const OWN_PLAYERS: [&str; 6] = ["pw-play", "paplay", "canberra-gtk-play", "ffplay", "pw-cat", "aplay"];
pub const SKIP_ROLES: [&str; 3] = ["event", "notification", "a11y"];
pub const MAX_PERCENT: i64 = 100;
/// an app that sets its own level right after its stream starts (Spotify:
/// its saved level went back every time -- Vini) gets ours again at these
pub const RECHECK_MS: [u64; 5] = [120, 300, 700, 2000, 4000];
/// ...and only then: afterwards, a level the app sets is the user's (learned)
pub const FRESH_S: f64 = 8.0;
/// changes made outside Sonata (Spotify's own slider) saved this long
/// after: a playing app sends 'change' events all the time, and each learn
/// reads every stream (less than FRESH_S: a new stream's own start level is
/// never learned)
pub const LEARN_MS: u64 = 5000;
/// a stream often comes from a helper process (WebKitWebProcess, wine...)
pub const PARENTS: usize = 6;
/// equalizer::PREFIX
pub const EQ_PREFIX: &str = "sonata-eq";

pub fn defaults() -> Value {
    json!({"volumes": {}, "muted": {}})
}

/// One stream playing sound.
#[derive(Clone, Debug, PartialEq)]
pub struct Stream {
    pub index: i64,
    /// what the level is saved under
    pub key: String,
    /// shown
    pub name: String,
    /// icon name ("" = none known)
    pub icon: String,
    /// percent (the loudest channel)
    pub volume: i32,
    pub muted: bool,
    /// paused
    pub corked: bool,
    /// the process playing it (application.process.id)
    pub pid: i64,
    /// the output it plays on (pactl's sink index)
    pub sink: i64,
}

/// How pactl is run (tests pass their own): (exit code, stdout).
pub type Run<'a> = &'a dyn Fn(&[&str]) -> (i32, String);

fn pactl(args: &[&str]) -> (i32, String) {
    sonata_backend::run::run(args, 4)
}

/// A property as text (pactl gives strings; a number shown as Python would).
fn prop(props: &Value, k: &str) -> String {
    match props.get(k) {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

fn percent(volume: Option<&Value>) -> i32 {
    let vals: Vec<i32> = volume
        .and_then(Value::as_object)
        .map(|m| {
            m.values()
                .filter_map(|ch| {
                    let p = match ch.get("value_percent") {
                        Some(Value::String(s)) => s.clone(),
                        Some(v @ Value::Number(_)) => v.to_string(),
                        _ => return None,
                    };
                    p.trim_end_matches('%').trim().parse::<f64>().ok().map(|f| f.round_ties_even() as i32)
                })
                .collect()
        })
        .unwrap_or_default();
    vals.into_iter().max().unwrap_or(100)
}

/// A sound of the system, not an app's (Vini: the volume feedback flashed a
/// "pw-play" row): Sonata's own (its players -- named by the binary or,
/// without one, by the application name -- or marked "Sonata"), and any
/// event / notification / accessibility sound.
pub fn system_sound(props: &Value) -> bool {
    let binary = prop(props, "application.process.binary").to_lowercase();
    let name = prop(props, "application.name").to_lowercase();
    let mut role = prop(props, "media.role");
    if role.is_empty() {
        role = prop(props, "media.category");
    }
    let role = role.to_lowercase();
    // "Sonata" exactly (sounds.py marks its sounds so): a web app the user
    // named "Sonata …", or "Sonatype", is an app with its own row
    OWN_PLAYERS.contains(&binary.as_str()) || OWN_PLAYERS.contains(&name.as_str()) || name == "sonata" || name == "sonata2" || SKIP_ROLES.contains(&role.as_str()) || !prop(props, "event.id").is_empty()
}

pub fn app_key(props: &Value) -> String {
    ["application.id", "pipewire.access.portal.app_id", "application.process.binary", "application.name"].iter().map(|k| prop(props, k).trim().to_string()).find(|v| !v.is_empty()).map(|v| v.to_lowercase()).unwrap_or_default()
}

fn webapp_re() -> &'static regex::Regex {
    static RE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"(?:/webapps/([\w.-]+)/chromium\b|\bsonata2 webapp ([\w.-]+)\b)").unwrap())
}

fn parent(pid: i64, proc: &str) -> i64 {
    std::fs::read(format!("{proc}/{pid}/stat"))
        .ok()
        .and_then(|b| {
            let s = String::from_utf8_lossy(&b).into_owned();
            s.rsplit_once(')').and_then(|(_, rest)| rest.split_whitespace().nth(1).and_then(|p| p.parse().ok()))
        })
        .unwrap_or(0)
}

/// The Sonata web app (its id) whose browser or window plays a stream, from
/// the process or its parents: a Chromium web app's profile folder
/// (--user-data-dir=.../webapps/<id>/chromium), a WebKit one's
/// `sonata2 webapp <id>`. Vini: web apps' sound showed as Chrome's.
pub fn webapp_of(pid: i64, proc: &str) -> Option<String> {
    let mut p = pid;
    for _ in 0..PARENTS {
        if p <= 1 {
            return None;
        }
        let raw = std::fs::read(format!("{proc}/{p}/cmdline")).ok()?;
        let cmd = String::from_utf8_lossy(&raw).replace('\0', " ");
        if let Some(m) = webapp_re().captures(&cmd) {
            return m.get(1).or(m.get(2)).map(|g| g.as_str().to_string());
        }
        p = parent(p, proc);
    }
    None
}

/// webapps.py's entry for a web app ({"name", "url"}).
pub fn webapp_entry(wid: &str) -> Option<Map<String, Value>> {
    sonata_config::load("webapps", &json!({"apps": {}})).get("apps")?.get(wid)?.as_object().cloned()
}

/// webapps.desktop_id
pub fn webapp_desktop_id(wid: &str) -> String {
    format!("sonata2-webapp-{wid}")
}

/// `pactl -f json list sink-inputs` -> the apps' streams (Sonata's own
/// sounds and event sounds left out), web apps found through /proc.
pub fn parse(text: &str) -> Vec<Stream> {
    parse_with(text, Some(&|pid| webapp_of(pid, "/proc")), &webapp_entry)
}

/// parse() with how a web app is found (None: never) and read. A web app's
/// stream is its own (its desktop id as key, its name), not its browser's.
pub fn parse_with(text: &str, webapp: Option<&dyn Fn(i64) -> Option<String>>, entry: &dyn Fn(&str) -> Option<Map<String, Value>>) -> Vec<Stream> {
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(if text.is_empty() { "[]" } else { text }) else { return vec![] };
    let mut out = Vec::new();
    for it in &items {
        let empty = json!({});
        let props = it.get("properties").filter(|p| p.is_object()).unwrap_or(&empty);
        if system_sound(props) {
            continue;
        }
        let binary = prop(props, "application.process.binary").to_lowercase();
        let mut key = app_key(props);
        if key.is_empty() {
            continue;
        }
        let mut name = prop(props, "application.name");
        if name.is_empty() {
            name = if binary.is_empty() { key.clone() } else { binary.clone() };
        }
        let pid_s = prop(props, "application.process.id");
        let pid = if !pid_s.is_empty() && pid_s.bytes().all(|b| b.is_ascii_digit()) { pid_s.parse().unwrap_or(0) } else { 0 };
        let mut icon = prop(props, "application.icon_name");
        if let Some(wid) = webapp.filter(|_| pid != 0).and_then(|f| f(pid)) {
            let e = entry(&wid).unwrap_or_default();
            key = webapp_desktop_id(&wid);
            if let Some(n) = e.get("name").and_then(Value::as_str).filter(|n| !n.is_empty()) {
                name = n.to_string();
            }
            icon = String::new();
        }
        out.push(Stream {
            index: it.get("index").and_then(Value::as_i64).unwrap_or(-1),
            key,
            name,
            icon,
            volume: percent(it.get("volume")),
            muted: it.get("mute").and_then(Value::as_bool).unwrap_or(false),
            corked: it.get("corked").and_then(Value::as_bool).unwrap_or(false),
            pid,
            sink: it.get("sink").and_then(Value::as_i64).filter(|s| *s >= 0).unwrap_or(-1),
        });
    }
    out
}

// -- whose stream: a game's (Steam), or an app's window --------------------------------------
type Owner = Option<(&'static str, String)>;
static OWNERS: Mutex<Option<HashMap<i64, Owner>>> = Mutex::new(None);

fn steam_app(pid: i64, proc: &str) -> Option<String> {
    let raw = std::fs::read(format!("{proc}/{pid}/environ")).ok()?;
    let env: HashMap<&[u8], &[u8]> = raw.split(|b| *b == 0).filter_map(|e| e.iter().position(|b| *b == b'=').map(|i| (&e[..i], &e[i + 1..]))).collect();
    let aid = env.get(&b"SteamAppId"[..]).filter(|v| !v.is_empty()).or(env.get(&b"SteamGameId"[..])).map(|v| String::from_utf8_lossy(v).into_owned()).unwrap_or_default();
    (!aid.is_empty() && aid.bytes().all(|b| b.is_ascii_digit()) && aid != "0").then_some(aid)
}

pub fn forget_owners() {
    *OWNERS.lock().unwrap_or_else(|e| e.into_inner()) = None;
}

/// ("steam", appid) or ("app", window app_id) for the process playing a
/// stream (or one of its parents), None when unknown. Vini: the sound
/// mixer showed no icon for a Steam game. `views`: Wayfire's list-views
/// (None: asked here).
pub fn whose(pid: i64, views: Option<&[Value]>, proc: &str) -> Owner {
    if pid <= 0 {
        return None;
    }
    if let Some(found) = OWNERS.lock().unwrap_or_else(|e| e.into_inner()).as_ref().and_then(|m| m.get(&pid)) {
        return found.clone();
    }
    let asked;
    let views = match views {
        Some(v) => Some(v),
        None => {
            asked = sonata_backend::wfipc::call("window-rules/list-views", json!({})).and_then(|v| v.as_array().cloned());
            asked.as_deref()
        }
    };
    let by_pid: HashMap<i64, String> = views.unwrap_or(&[]).iter().filter_map(|v| Some((v.get("pid")?.as_i64()?, v.get("app-id")?.as_str().filter(|a| !a.is_empty())?.to_string()))).collect();
    let (mut found, mut p) = (None, pid);
    for _ in 0..PARENTS {
        if p <= 1 {
            break;
        }
        if let Some(aid) = steam_app(p, proc) {
            found = Some(("steam", aid));
            break;
        }
        if let Some(a) = by_pid.get(&p) {
            found = Some(("app", a.clone()));
            break;
        }
        p = parent(p, proc);
    }
    if found.is_some() || views.is_some() {
        // (IPC down: asked again next time)
        OWNERS.lock().unwrap_or_else(|e| e.into_inner()).get_or_insert_with(HashMap::new).insert(pid, found.clone());
    }
    found
}

/// One entry per app (a browser plays several streams): the first stream
/// stands for them; set_level() changes all of an app's streams.
pub fn group(streams: &[Stream]) -> Vec<Stream> {
    let mut seen = HashSet::new();
    streams.iter().filter(|s| seen.insert(s.key.clone())).cloned().collect()
}

pub fn available() -> bool {
    sonata_backend::run::which("pactl").is_some()
}

pub fn streams() -> Vec<Stream> {
    streams_with(&pactl)
}

pub fn streams_with(run: Run) -> Vec<Stream> {
    if !available() {
        return vec![];
    }
    let (rc, out) = run(&["pactl", "-f", "json", "list", "sink-inputs"]);
    if rc == 0 { parse(&out) } else { vec![] }
}

// -- saved levels ------------------------------------------------------------------------------
/// mixer.json: the levels and mutes saved per app.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Saved {
    pub volumes: Map<String, Value>,
    pub muted: Map<String, Value>,
}

impl Saved {
    pub fn volume(&self, key: &str) -> Option<i64> {
        self.volumes.get(key).and_then(|v| v.as_i64().or(v.as_f64().map(|f| f as i64)))
    }
    pub fn mute(&self, key: &str) -> Option<bool> {
        self.muted.get(key).and_then(Value::as_bool)
    }
}

pub fn saved() -> Saved {
    let data = sonata_config::load(NAME, &defaults());
    let obj = |k: &str| data.get(k).and_then(Value::as_object).cloned().unwrap_or_default();
    Saved { volumes: obj("volumes"), muted: obj("muted") }
}

fn clamp(percent: i64) -> i64 {
    percent.clamp(0, MAX_PERCENT)
}

pub fn remember(key: &str, percent: Option<i64>, muted: Option<bool>) {
    let mut data = saved();
    if let Some(p) = percent {
        data.volumes.insert(key.into(), json!(clamp(p)));
    }
    if let Some(m) = muted {
        data.muted.insert(key.into(), json!(m));
    }
    let mut out = Map::new();
    out.insert("volumes".into(), Value::Object(data.volumes));
    out.insert("muted".into(), Value::Object(data.muted));
    let _ = sonata_config::save(NAME, &out);
}

/// {key: (percent, muted)} of apps whose level changed outside Sonata
/// (Spotify's own slider: Sonata kept putting back its old level at every
/// start -- Vini). Apps with several streams at different levels are left.
pub fn learn(ss: &[Stream], data: &Saved, skip: &HashSet<i64>) -> Vec<(String, (i32, bool))> {
    let mut by: Vec<(String, Vec<&Stream>)> = Vec::new();
    for s in ss {
        if skip.contains(&s.index) || s.key.is_empty() {
            continue;
        }
        match by.iter_mut().find(|(k, _)| *k == s.key) {
            Some((_, g)) => g.push(s),
            None => by.push((s.key.clone(), vec![s])),
        }
    }
    let mut out = Vec::new();
    for (key, g) in by {
        let levels: HashSet<(i32, bool)> = g.iter().map(|s| (s.volume, s.muted)).collect();
        if levels.len() != 1 {
            continue;
        }
        let s = g[0];
        if data.volume(&key) != Some(s.volume as i64) || data.mute(&key).unwrap_or(false) != s.muted {
            out.push((key, (s.volume, s.muted)));
        }
    }
    out
}

/// Every stream of app `key` at `percent` (and/or muted), saved for next time.
pub fn set_level(key: &str, percent: Option<i64>, muted: Option<bool>, all_streams: Option<&[Stream]>) -> bool {
    set_level_with(&pactl, key, percent, muted, all_streams)
}

pub fn set_level_with(run: Run, key: &str, percent: Option<i64>, muted: Option<bool>, all_streams: Option<&[Stream]>) -> bool {
    remember(key, percent, muted);
    let read;
    let all = match all_streams {
        Some(a) => a,
        None => {
            read = streams_with(run);
            &read
        }
    };
    let mut ok = true;
    for s in all.iter().filter(|s| s.key == key) {
        let idx = s.index.to_string();
        if let Some(p) = percent {
            ok = run(&["pactl", "set-sink-input-volume", &idx, &format!("{}%", clamp(p))]).0 == 0 && ok;
        }
        if let Some(m) = muted {
            ok = run(&["pactl", "set-sink-input-mute", &idx, if m { "1" } else { "0" }]).0 == 0 && ok;
        }
    }
    ok
}

// -- which output an app plays on --------------------------------------------------------------
#[derive(Clone, Debug, PartialEq)]
pub struct Output {
    /// pactl's sink index (Stream.sink)
    pub index: i64,
    /// the sink's node name (what move-sink-input takes)
    pub name: String,
    /// shown: as in the Sound menu's Output list
    pub label: String,
}

/// The outputs an app can be sent to: one per device (its ports are the
/// whole card's, so they aren't offered per app), named as in the Output
/// list (`devices`: system::audio_outputs()) when that list has one entry
/// for it. Devices that list leaves out (unplugged HDMI, the equalizer's
/// filters) are left out here too.
pub fn outputs_from(sinks_json: &str, devices: &[AudioDevice]) -> Vec<Output> {
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(sinks_json) else { return vec![] };
    let mut out = Vec::new();
    for it in &items {
        let name = it.get("name").and_then(Value::as_str).unwrap_or("");
        let shown: Vec<&AudioDevice> = devices.iter().filter(|d| d.key.split('|').next() == Some(name)).collect();
        if name.starts_with(EQ_PREFIX) || shown.is_empty() {
            continue;
        }
        let label = if shown.len() == 1 { shown[0].name.clone() } else { it.get("description").and_then(Value::as_str).filter(|d| !d.is_empty()).unwrap_or(name).to_string() };
        out.push(Output { index: it.get("index").and_then(Value::as_i64).unwrap_or(-1), name: name.into(), label });
    }
    out
}

/// equalizer's slug of a sink name
pub fn slug(sink: &str) -> String {
    sink.chars().map(|c| if c.is_ascii_alphanumeric() || "_.-".contains(c) { c } else { '_' }).collect()
}

/// The output `stream` plays on. With the equalizer on, the stream is
/// linked to the filter in front of the device (node
/// "sonata-eq.<device>"): that is the device's own. None: unknown.
pub fn current_output(stream: &Stream, outs: &[Output], sinks_json: &str) -> Option<Output> {
    if let Some(o) = outs.iter().find(|o| o.index == stream.sink) {
        return Some(o.clone());
    }
    let Ok(Value::Array(items)) = serde_json::from_str::<Value>(sinks_json) else { return None };
    let node = items.iter().find(|it| it.get("index").and_then(Value::as_i64) == Some(stream.sink)).and_then(|it| it.get("name")?.as_str()).unwrap_or("");
    let s = node.strip_prefix(&format!("{EQ_PREFIX}."))?;
    outs.iter().find(|o| slug(&o.name) == s).cloned()
}

/// (outputs, the one `stream` plays on or None): what the per-app output
/// picker lists, read in one go (a background thread's work).
pub fn choices(stream: &Stream) -> (Vec<Output>, Option<Output>) {
    choices_with(&pactl, stream, &sonata_backend::system::audio_outputs)
}

pub fn choices_with(run: Run, stream: &Stream, devices: &dyn Fn() -> Vec<AudioDevice>) -> (Vec<Output>, Option<Output>) {
    let (rc, text) = run(&["pactl", "-f", "json", "list", "sinks"]);
    if rc != 0 {
        return (vec![], None);
    }
    let outs = outputs_from(&text, &devices());
    let cur = current_output(stream, &outs, &text);
    (outs, cur)
}

/// Every stream of app `key` to the output `sink_name` (a browser's tabs
/// together, as with its level). WirePlumber keeps the choice for the app's
/// next streams (its stream-restore), like a level Sonata keeps.
pub fn move_to(key: &str, sink_name: &str, all_streams: Option<&[Stream]>) -> bool {
    move_with(&pactl, key, sink_name, all_streams)
}

pub fn move_with(run: Run, key: &str, sink_name: &str, all_streams: Option<&[Stream]>) -> bool {
    let read;
    let all = match all_streams {
        Some(a) => a,
        None => {
            read = streams_with(run);
            &read
        }
    };
    let mut ok = true;
    for s in all.iter().filter(|s| s.key == key) {
        ok = run(&["pactl", "move-sink-input", &s.index.to_string(), sink_name]).0 == 0 && ok;
    }
    ok
}

/// Put the app's saved level back on this stream. True: changed.
pub fn restore(stream: &Stream, data: Option<&Saved>) -> bool {
    restore_with(&pactl, stream, data)
}

pub fn restore_with(run: Run, stream: &Stream, data: Option<&Saved>) -> bool {
    let own;
    let data = match data {
        Some(d) => d,
        None => {
            own = saved();
            &own
        }
    };
    let idx = stream.index.to_string();
    let mut changed = false;
    if let Some(want) = data.volume(&stream.key).filter(|w| *w != stream.volume as i64) {
        changed = run(&["pactl", "set-sink-input-volume", &idx, &format!("{want}%")]).0 == 0;
    }
    if let Some(m) = data.mute(&stream.key).filter(|m| *m != stream.muted) {
        changed = run(&["pactl", "set-sink-input-mute", &idx, if m { "1" } else { "0" }]).0 == 0 || changed;
    }
    changed
}

/// `pactl subscribe`: "Event 'new' on sink-input #42" -> 42.
pub fn new_stream_index(line: &str) -> Option<i64> {
    if !line.contains("'new' on sink-input #") {
        return None;
    }
    line.rsplit('#').next()?.trim().parse().ok()
}

// -- the service (menu bar process) ------------------------------------------------------------
type ReadFn = Arc<dyn Fn() -> Vec<Stream> + Send + Sync>;
type PactlFn = Arc<dyn Fn(&[&str]) -> (i32, String) + Send + Sync>;
pub type Listener = Rc<dyn Fn(&[Stream])>;

struct Inner {
    listeners: RefCell<Vec<(u64, Listener)>>,
    next_id: Cell<u64>,
    proc: RefCell<Option<crate::pactl_watch::Handle>>,
    src: RefCell<Option<glib::SourceId>>,
    new: RefCell<HashSet<i64>>,
    /// new stream index -> when it appeared (its level is ours then)
    fresh: RefCell<HashMap<i64, Instant>>,
    learn_src: RefCell<Option<glib::SourceId>>,
    /// sink-input index -> (volume, muted) at the last learn
    levels: Arc<Mutex<HashMap<i64, (i32, bool)>>>,
    read: ReadFn,
    run: PactlFn,
}

/// Menu bar process: every new app stream gets its app's saved level;
/// listeners (an open Sound menu) hear about streams coming and going.
#[derive(Clone)]
pub struct MixerService(Rc<Inner>);

impl MixerService {
    pub fn new() -> Self {
        let svc = Self::with(Arc::new(streams), Arc::new(pactl));
        if !available() {
            return svc;
        }
        let w = Rc::downgrade(&svc.0);
        // the shared `pactl subscribe` reader
        let handle = crate::pactl_watch::watch(move |t| {
            if let Some(i) = w.upgrade() {
                MixerService(i).line(t);
            }
        });
        if handle.is_none() {
            return svc;
        }
        *svc.0.proc.borrow_mut() = handle;
        // what was playing before we started
        let (read, run) = (svc.0.read.clone(), svc.0.run.clone());
        glib::idle_add_local_once(move || {
            crate::run_async(
                move || {
                    for s in read() {
                        restore_with(&*run, &s, None);
                    }
                },
                |_| {},
            )
        });
        svc
    }

    /// A service reading streams and running pactl through these (tests).
    pub fn with(read: ReadFn, run: PactlFn) -> Self {
        Self(Rc::new(Inner {
            listeners: RefCell::default(),
            next_id: Cell::new(0),
            proc: RefCell::default(),
            src: RefCell::default(),
            new: RefCell::default(),
            fresh: RefCell::default(),
            learn_src: RefCell::default(),
            levels: Arc::default(),
            read,
            run,
        }))
    }

    pub fn running(&self) -> bool {
        self.0.proc.borrow().is_some()
    }

    /// listener(streams) after streams came, went or changed; returns its id.
    pub fn add_listener(&self, f: impl Fn(&[Stream]) + 'static) -> u64 {
        let id = self.0.next_id.get() + 1;
        self.0.next_id.set(id);
        self.0.listeners.borrow_mut().push((id, Rc::new(f)));
        id
    }

    pub fn remove_listener(&self, id: u64) {
        self.0.listeners.borrow_mut().retain(|(i, _)| *i != id);
    }

    pub fn line(&self, text: &str) {
        if !text.contains("on sink-input") {
            return;
        }
        let me = &self.0;
        let idx = new_stream_index(text);
        if let Some(i) = idx {
            me.new.borrow_mut().insert(i);
            me.fresh.borrow_mut().insert(i, Instant::now());
        } else if text.contains("'change' on sink-input") && me.learn_src.borrow().is_none() {
            let w = Rc::downgrade(me);
            *me.learn_src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(LEARN_MS), move || {
                if let Some(i) = w.upgrade() {
                    i.learn_src.borrow_mut().take();
                    MixerService(i).learn_now();
                }
            }));
        }
        if idx.is_some() {
            // a new stream: no waiting for the burst to end
            if let Some(src) = me.src.borrow_mut().take() {
                src.remove();
            }
        }
        if me.src.borrow().is_none() {
            // a burst: one read
            let w = Rc::downgrade(me);
            let ms = if idx.is_some() { 0 } else { 150 };
            *me.src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(ms), move || {
                if let Some(i) = w.upgrade() {
                    i.src.borrow_mut().take();
                    MixerService(i).changed();
                }
            }));
        }
    }

    /// A burst of stream events: restore new streams' levels; tell an open
    /// Sound menu. With neither, nothing runs (a playing app sends events
    /// all the time: no pactl process for each).
    pub fn changed(&self) {
        let me = &self.0;
        let new = std::mem::take(&mut *me.new.borrow_mut());
        if new.is_empty() && me.listeners.borrow().is_empty() {
            return;
        }
        let (read, run, n) = (me.read.clone(), me.run.clone(), new.clone());
        let w = Rc::downgrade(me);
        crate::run_async(
            move || {
                let data = saved();
                let ss = read();
                // every one (no short-circuit)
                let changed: Vec<bool> = ss.iter().filter(|s| n.contains(&s.index)).map(|s| restore_with(&*run, s, Some(&data))).collect();
                // the menu shows the levels just put back
                if changed.iter().any(|c| *c) { read() } else { ss }
            },
            move |ss| {
                if let Some(i) = w.upgrade() {
                    let all: Vec<Listener> = i.listeners.borrow().iter().map(|(_, f)| f.clone()).collect();
                    for f in all {
                        f(&ss);
                    }
                }
            },
        );
        if !new.is_empty() {
            for ms in RECHECK_MS {
                let (read, run, n) = (me.read.clone(), me.run.clone(), new.clone());
                glib::timeout_add_local_once(Duration::from_millis(ms), move || {
                    // the saved level again on these new streams, if the app changed it
                    crate::run_async(
                        move || {
                            let data = saved();
                            for s in read().iter().filter(|s| n.contains(&s.index)) {
                                restore_with(&*run, s, Some(&data));
                            }
                        },
                        |_| {},
                    );
                });
            }
        }
    }

    /// Levels changed outside Sonata become the apps' saved levels.
    pub fn learn_now(&self) {
        let me = &self.0;
        me.fresh.borrow_mut().retain(|_, t| t.elapsed().as_secs_f64() < FRESH_S);
        let skip: HashSet<i64> = me.fresh.borrow().keys().copied().collect();
        let (read, levels) = (me.read.clone(), me.levels.clone());
        crate::run_async(move || learn_step(&read(), &levels, &skip, &saved).into_iter().for_each(|(k, (p, m))| remember(&k, Some(p as i64), Some(m))), |_| {});
    }

    pub fn stop(&self) {
        if let Some(h) = self.0.proc.borrow_mut().take() {
            h.stop();
        }
    }
}

impl Default for MixerService {
    fn default() -> Self {
        Self::new()
    }
}

/// One learn (a worker thread's): a playing app sends 'change' events all
/// the time (title, corked, latency) with no level change: only a stream
/// whose volume or mute differs from the last read reads the config and
/// learns. `levels` is kept between learns (one at a time: its lock).
pub fn learn_step(ss: &[Stream], levels: &Mutex<HashMap<i64, (i32, bool)>>, skip: &HashSet<i64>, data: &dyn Fn() -> Saved) -> Vec<(String, (i32, bool))> {
    let mut last = levels.lock().unwrap_or_else(|e| e.into_inner());
    let now: HashMap<i64, (i32, bool)> = ss.iter().map(|s| (s.index, (s.volume, s.muted))).collect();
    let same = ss.iter().all(|s| last.get(&s.index) == now.get(&s.index));
    *last = now;
    if same {
        return vec![];
    }
    learn(ss, &data(), skip)
}

#[cfg(test)]
mod tests;
