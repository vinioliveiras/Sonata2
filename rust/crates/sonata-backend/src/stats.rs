//! Live performance figures for the menu bar and Control Center
//! (backend/stats.py): CPU, GPU and memory use, network speed and the FPS
//! of the app in front. Nothing is read while nobody shows them: watchers
//! subscribe, the reading stops with the last one. Reads run off the main
//! loop.
//!
//! KINDS: what can be shown -- cpu, gpu (one card) or gpu_<card> for each
//! card ("gpu_amd", "gpu_nvidia"), vram or vram_<card> (cards that report
//! their video memory: NVIDIA, AMD), temp_cpu and temp_gpu or temp_<card>
//! (temperatures, °C), ram, net, fps.

use crate::procfs::{self, read};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

pub const INTERVAL_MS: u64 = 1000;
/// samples kept for the little graphs
pub const HISTORY: usize = 30;
/// Intel's integrated GPU has no memory of its own to show
const VRAM_MAKERS: [&str; 2] = ["NVIDIA", "AMD"];
/// cards whose temperature can be read (Intel's iGPU: the CPU's)
const TEMP_MAKERS: [&str; 2] = ["NVIDIA", "AMD"];
const CPU_SENSORS: [&str; 4] = ["k10temp", "zenpower", "coretemp", "cpu_thermal"];
/// the whole chip, before a single core
const CPU_LABELS: [&str; 3] = ["Tctl", "Tdie", "Package id 0"];

#[derive(Clone, Debug, PartialEq)]
pub struct Card {
    /// "amd", "nvidia", "intel" ("amd2" for a second card of one maker)
    pub key: String,
    pub maker: String,
    /// cardN
    pub card: String,
}

/// The graphics cards, in card order.
pub fn gpu_cards(sys: &Path) -> Vec<Card> {
    let mut out = Vec::new();
    let mut seen: HashMap<String, usize> = HashMap::new();
    let mut cards: Vec<PathBuf> = std::fs::read_dir(sys.join("class/drm")).into_iter().flatten().flatten().map(|e| e.path()).collect();
    cards.sort();
    for card in cards {
        let name = card.file_name().unwrap().to_string_lossy().into_owned();
        if !(name.starts_with("card") && name[4..].bytes().all(|b| b.is_ascii_digit()) && name.len() > 4) {
            continue; // a connector (card1-HDMI-A-1)
        }
        let drv = std::fs::canonicalize(card.join("device/driver")).ok().and_then(|p| p.file_name().map(|n| n.to_string_lossy().into_owned())).unwrap_or_default();
        let Some(maker) = procfs::card_name(&drv) else { continue };
        let base = maker.to_lowercase();
        let n = seen.entry(base.clone()).or_insert(0);
        *n += 1;
        out.push(Card { key: if *n == 1 { base } else { format!("{base}{n}") }, maker: maker.into(), card: name });
    }
    out
}

/// {kind: card key} of the cards whose video memory can be shown: "vram"
/// for one, "vram_<card>" each when there are two or more.
pub fn vram_kinds(cards: &[Card]) -> Kinds {
    let keys: Vec<&Card> = cards.iter().filter(|c| VRAM_MAKERS.contains(&c.maker.as_str())).collect();
    if keys.len() == 1 {
        return vec![("vram".to_string(), keys[0].key.clone())];
    }
    keys.iter().map(|c| (format!("vram_{}", c.key), c.key.clone())).collect()
}

/// [(kind, card key)] in the cards' order.
pub type Kinds = Vec<(String, String)>;

/// The card key of a kind.
pub fn key_of<'a>(kinds: &'a Kinds, kind: &str) -> Option<&'a String> {
    kinds.iter().find(|(k, _)| k == kind).map(|(_, v)| v)
}

/// The CPU's temperature file (hwmon), "" when there is none.
pub fn cpu_temp_path(sys: &Path) -> String {
    let mut hws: Vec<PathBuf> = std::fs::read_dir(sys.join("class/hwmon")).into_iter().flatten().flatten().map(|e| e.path()).filter(|p| p.file_name().is_some_and(|n| n.to_string_lossy().starts_with("hwmon"))).collect();
    hws.sort();
    for hw in hws {
        if !CPU_SENSORS.contains(&read(hw.join("name")).trim()) {
            continue;
        }
        let mut inputs: Vec<PathBuf> = std::fs::read_dir(&hw).into_iter().flatten().flatten().map(|e| e.path()).filter(|p| p.file_name().is_some_and(|n| {
            let n = n.to_string_lossy();
            n.starts_with("temp") && n.ends_with("_input")
        })).collect();
        inputs.sort();
        let labels: HashMap<String, &PathBuf> = inputs.iter().map(|i| (read(i.to_string_lossy().replace("_input", "_label")).trim().to_string(), i)).collect();
        for want in CPU_LABELS {
            if let Some(p) = labels.get(want) {
                return p.display().to_string();
            }
        }
        if let Some(i) = inputs.first() {
            return i.display().to_string();
        }
    }
    String::new()
}

/// {kind: card key} of the cards whose temperature can be shown:
/// "temp_gpu" for one, "temp_<card>" each when there are two or more.
pub fn temp_kinds(cards: &[Card]) -> Kinds {
    let keys: Vec<&Card> = cards.iter().filter(|c| TEMP_MAKERS.contains(&c.maker.as_str())).collect();
    if keys.len() == 1 {
        return vec![("temp_gpu".to_string(), keys[0].key.clone())];
    }
    keys.iter().map(|c| (format!("temp_{}", c.key), c.key.clone())).collect()
}

/// The figures that can be shown: one GPU, or one per card when there are two or more.
pub fn kinds(cards: &[Card], cpu_temp: &str) -> Vec<String> {
    let mut out = vec!["cpu".to_string()];
    if cards.len() < 2 {
        out.push("gpu".into());
    } else {
        out.extend(cards.iter().map(|c| format!("gpu_{}", c.key)));
    }
    out.extend(vram_kinds(cards).into_iter().map(|k| k.0));
    if !cpu_temp.is_empty() {
        out.push("temp_cpu".into());
    }
    out.extend(temp_kinds(cards).into_iter().map(|k| k.0));
    out.extend(["ram", "net", "fps"].map(String::from));
    out
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Reading {
    pub cpu: f64,
    pub gpu: Option<f64>,
    /// card key -> % (None: no reading)
    pub gpus: BTreeMap<String, Option<f64>>,
    /// card key -> (used, total) bytes (None: no reading)
    pub vrams: BTreeMap<String, Option<(u64, u64)>>,
    /// °C
    pub cpu_temp: Option<f64>,
    pub temps: BTreeMap<String, Option<f64>>,
    /// "cpu" / "gpu" / "gpu_<card>" -> [(name, %)], busiest first
    pub top: BTreeMap<String, Vec<(String, f64)>>,
    pub ram_used: u64,
    pub ram_total: u64,
    pub down: f64,
    pub up: f64,
    pub fps: Option<i64>,
    /// "ok", "starting", "no-app", "no-plugin", "off"
    pub fps_state: String,
    /// name -> [values], oldest first
    pub history: BTreeMap<String, Vec<f64>>,
}

impl Reading {
    pub fn ram_pct(&self) -> f64 {
        if self.ram_total > 0 { 100.0 * self.ram_used as f64 / self.ram_total as f64 } else { 0.0 }
    }
}

// -- nvidia-smi, shared -----------------------------------------------------------------------
/// what the menu bar, Activity and the VRAM watch read in one run
pub const NV_QUERY: [&str; 2] = ["--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"];
/// whoever comes second within this many seconds gets the same answer
const NV_FRESH_S: f64 = 0.9;

static NV_CACHE: Mutex<Option<(PathBuf, f64, String)>> = Mutex::new(None);

/// nvidia-smi's NV_QUERY rows (one per card), from cache when younger
/// than NV_FRESH_S. Blocking: call it off the main loop.
pub fn nvidia_read(tool: &Path) -> String {
    let mut c = NV_CACHE.lock().unwrap();
    let now = procfs::monotonic();
    if let Some((t, at, out)) = c.as_ref() {
        if t == tool && now - at < NV_FRESH_S {
            return out.clone();
        }
    }
    let out = std::process::Command::new(tool).args(NV_QUERY).output().map(|o| String::from_utf8_lossy(&o.stdout).into_owned()).unwrap_or_default();
    *c = Some((tool.to_path_buf(), procfs::monotonic(), out.clone()));
    out
}

type NvFigures = (Vec<f64>, Vec<Option<(u64, u64)>>, Vec<Option<f64>>);

/// ([busy %], [(used, total) bytes], [°C]) from nvidia-smi's rows, one per card.
pub fn parse_nvidia(out: &str) -> NvFigures {
    let (mut pcts, mut mems, mut temps) = (Vec::new(), Vec::new(), Vec::new());
    for line in out.lines() {
        let parts: Vec<&str> = line.split(',').map(str::trim).collect();
        let Some(pct) = parts.first().and_then(|p| p.parse::<f64>().ok()) else { continue };
        pcts.push(pct);
        let mem = match (parts.get(1).and_then(|p| p.parse::<u64>().ok()), parts.get(2).and_then(|p| p.parse::<u64>().ok())) {
            (Some(u), Some(t)) if t > 0 => Some((u << 20, t << 20)),
            _ => None,
        };
        mems.push(mem);
        temps.push(parts.get(3).and_then(|p| p.parse::<f64>().ok()));
    }
    (pcts, mems, temps)
}

#[derive(Default)]
struct NvState {
    list: Vec<f64>,
    mem: Vec<Option<(u64, u64)>>,
    temp: Vec<Option<f64>>,
}

/// The figures from /proc and /sys (pure reads; testable on a fake root).
pub struct Sampler {
    pub proc: PathBuf,
    pub sys: PathBuf,
    cpu: Option<procfs::CpuTicks>,
    net: Option<(u64, u64)>,
    nvidia: Option<procfs::NvidiaUsage>,
    nv: Arc<Mutex<NvState>>,
    nv_busy: Arc<AtomicBool>,
    cpu_temp: Option<String>,
    cards: Option<Vec<Card>>,
}

impl Sampler {
    pub fn new(proc: &Path, sys: &Path) -> Self {
        Self { proc: proc.into(), sys: sys.into(), cpu: None, net: None, nvidia: None, nv: Arc::default(), nv_busy: Arc::default(), cpu_temp: None, cards: None }
    }

    pub fn cards(&mut self) -> Vec<Card> {
        let sys = self.sys.clone();
        self.cards.get_or_insert_with(|| gpu_cards(&sys)).clone()
    }

    pub fn forget_cpu(&mut self) {
        self.cpu = None;
    }

    /// Shown again later: its first rate from then, not a spike.
    pub fn forget_net(&mut self) {
        self.net = None;
    }

    pub fn cpu(&mut self) -> f64 {
        let text = read(self.proc.join("stat"));
        let line = text.lines().next().unwrap_or("");
        if !line.starts_with("cpu") {
            return 0.0;
        }
        let cur = procfs::parse_cpu_line(line);
        let prev = self.cpu.replace(cur);
        match prev {
            Some(p) => ((100.0 - procfs::cpu_percent(&p, &cur).idle) * 10.0).round_ties_even() / 10.0,
            None => 0.0,
        }
    }

    pub fn ram(&self) -> (u64, u64) {
        let m = procfs::memory_summary(&procfs::parse_meminfo(&read(self.proc.join("meminfo"))));
        (m.used, m.total)
    }

    pub fn net(&mut self, dt: f64) -> (f64, f64) {
        let rows = procfs::parse_net_dev(&read(self.proc.join("net/dev")));
        let rx: u64 = rows.iter().filter(|(n, _)| *n != "lo").map(|(_, r)| r.rx_bytes).sum();
        let tx: u64 = rows.iter().filter(|(n, _)| *n != "lo").map(|(_, r)| r.tx_bytes).sum();
        let prev = self.net.replace((rx, tx));
        match prev {
            Some(p) if dt > 0.0 => (((rx as f64 - p.0 as f64) / dt).max(0.0), ((tx as f64 - p.1 as f64) / dt).max(0.0)),
            _ => (0.0, 0.0),
        }
    }

    /// {card key: busy %} for every card: amdgpu/Intel from
    /// gpu_busy_percent; NVIDIA through nvidia-smi, only while the card is
    /// awake (asking would wake it), else None. nvidia false: no figure
    /// shown needs the NVIDIA card (no nvidia-smi run).
    pub fn gpus(&mut self, nvidia: bool) -> BTreeMap<String, Option<f64>> {
        let cards = self.cards();
        let sys = self.sys.clone();
        let nv = self.nvidia.get_or_insert_with(|| procfs::NvidiaUsage::new(&sys));
        let awake = nvidia && nv.tool.is_some() && !nv.devices.is_empty() && nv.awake();
        if awake {
            self.nvidia_refresh();
        } else {
            *self.nv.lock().unwrap() = NvState::default();
        }
        let state = self.nv.lock().unwrap();
        let mut out = BTreeMap::new();
        let mut nth = 0;
        for c in &cards {
            if c.maker == "NVIDIA" {
                out.insert(c.key.clone(), if awake { state.list.get(nth).copied() } else { None });
                nth += 1;
            } else {
                let v = read(self.sys.join("class/drm").join(&c.card).join("device/gpu_busy_percent"));
                out.insert(c.key.clone(), v.trim().parse::<u64>().ok().map(|n| n as f64));
            }
        }
        out
    }

    /// {card key: (used, total) bytes} of each card that has its own: AMD
    /// from mem_info_vram_*, NVIDIA from the nvidia-smi read of gpus()
    /// (None while the card sleeps). Call after gpus().
    pub fn vrams(&mut self) -> BTreeMap<String, Option<(u64, u64)>> {
        let cards = self.cards();
        let state = self.nv.lock().unwrap();
        let mut out = BTreeMap::new();
        let mut nth = 0;
        for c in &cards {
            if c.maker == "NVIDIA" {
                out.insert(c.key.clone(), state.mem.get(nth).copied().flatten());
                nth += 1;
            } else if VRAM_MAKERS.contains(&c.maker.as_str()) {
                let dev = self.sys.join("class/drm").join(&c.card).join("device");
                let v = match (read(dev.join("mem_info_vram_used")).trim().parse::<u64>(), read(dev.join("mem_info_vram_total")).trim().parse::<u64>()) {
                    (Ok(u), Ok(t)) if t > 0 => Some((u, t)),
                    _ => None,
                };
                out.insert(c.key.clone(), v);
            }
        }
        out
    }

    /// The CPU's °C (k10temp's Tctl, coretemp's package), None without a sensor.
    pub fn cpu_temp(&mut self) -> Option<f64> {
        let sys = self.sys.clone();
        let path = self.cpu_temp.get_or_insert_with(|| cpu_temp_path(&sys)).clone();
        if path.is_empty() {
            return None;
        }
        read(&path).trim().parse::<i64>().ok().map(|v| v as f64 / 1000.0)
    }

    /// {card key: °C} of each card that reports it: AMD from its hwmon
    /// (edge), NVIDIA from the nvidia-smi read of gpus(). Call after gpus().
    pub fn temps(&mut self) -> BTreeMap<String, Option<f64>> {
        let cards = self.cards();
        let state = self.nv.lock().unwrap();
        let mut out = BTreeMap::new();
        let mut nth = 0;
        for c in &cards {
            if c.maker == "NVIDIA" {
                out.insert(c.key.clone(), state.temp.get(nth).copied().flatten());
                nth += 1;
            } else if TEMP_MAKERS.contains(&c.maker.as_str()) {
                let hw = self.sys.join("class/drm").join(&c.card).join("device/hwmon");
                let mut files: Vec<PathBuf> = std::fs::read_dir(&hw).into_iter().flatten().flatten().map(|e| e.path().join("temp1_input")).filter(|p| p.exists()).collect();
                files.sort();
                out.insert(c.key.clone(), files.first().and_then(|f| read(f).trim().parse::<i64>().ok()).map(|v| v as f64 / 1000.0));
            }
        }
        out
    }

    /// The busiest card.
    pub fn gpu(each: &BTreeMap<String, Option<f64>>) -> Option<f64> {
        each.values().flatten().copied().reduce(f64::max)
    }

    fn nvidia_refresh(&self) {
        let Some(tool) = self.nvidia.as_ref().and_then(|n| n.tool.clone()) else { return };
        if self.nv_busy.swap(true, Ordering::SeqCst) {
            return;
        }
        let (nv, busy) = (self.nv.clone(), self.nv_busy.clone());
        std::thread::spawn(move || {
            let (list, mem, temp) = parse_nvidia(&nvidia_read(&tool)); // shared with the VRAM watch (one run)
            *nv.lock().unwrap() = NvState { list, mem, temp };
            busy.store(false, Ordering::SeqCst);
        });
    }

    /// (fps or None, state) from Sonata's Wayfire plugin (IPC sonata/fps).
    pub fn fps(&self) -> (Option<i64>, &'static str) {
        let Some(r) = crate::wfipc::call("sonata/fps", serde_json::json!({})) else { return (None, "no-plugin") };
        let Some(fps) = r.get("fps") else { return (None, "no-plugin") }; // plugin not rebuilt yet (or no Wayfire)
        if r.get("app-id").and_then(|v| v.as_str()).is_none_or(str::is_empty) {
            return (None, "no-app");
        }
        if !r.get("ready").and_then(|v| v.as_bool()).unwrap_or(false) {
            return (None, "starting");
        }
        (fps.as_f64().map(|f| f as i64), "ok")
    }
}

/// Any of these figures read from an NVIDIA card (nvidia-smi).
pub fn needs_nvidia(cards: &[Card], kinds: &[String]) -> bool {
    let (vk, tk) = (vram_kinds(cards), temp_kinds(cards));
    kinds.iter().any(|k| {
        if k == "gpu" && cards.iter().any(|c| c.maker == "NVIDIA") {
            return true;
        }
        let key = k.strip_prefix("gpu_").map(str::to_string).or_else(|| key_of(&vk, k).cloned()).or_else(|| key_of(&tk, k).cloned());
        key.is_some_and(|key| cards.iter().any(|c| c.key == key && c.maker == "NVIDIA"))
    })
}

pub const TOP_N: usize = 5;

/// [(name, %)] summed per program name (Chrome's many processes are one),
/// busiest first, the n busiest above 0.5 %.
pub fn top_list(rows: impl IntoIterator<Item = (String, f64)>, n: usize) -> Vec<(String, f64)> {
    let mut total: Vec<(String, f64)> = Vec::new();
    for (name, pct) in rows {
        if pct > 0.0 {
            match total.iter_mut().find(|(k, _)| *k == name) {
                Some(e) => e.1 += pct,
                None => total.push((name, pct)),
            }
        }
    }
    total.retain(|(_, v)| *v >= 0.5);
    total.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
    total.truncate(n);
    total
}

/// The busiest programs for CPU and each GPU (Activity's reader). Read
/// only while a module showing them is open.
pub struct TopProcs {
    s: procfs::Sampler,
    prev: HashMap<(i64, i64), i64>,
    t: Option<f64>,
}

impl TopProcs {
    pub fn new() -> Self {
        Self { s: procfs::Sampler::new("/proc", "/sys"), prev: HashMap::new(), t: None }
    }

    pub fn read(&mut self, cards: &[Card]) -> BTreeMap<String, Vec<(String, f64)>> {
        let now = procfs::monotonic();
        let dt = self.t.map_or(0.0, |t| now - t);
        let mut procs = self.s.processes(&[]);
        let tck = procfs::clk_tck();
        let mut ticks = HashMap::new();
        for p in procs.values_mut() {
            ticks.insert(p.key(), p.ticks);
            if let (Some(prev), true) = (self.prev.get(&p.key()), dt > 0.0) {
                p.cpu = (100.0 * (p.ticks - prev) as f64 / tck / dt).max(0.0);
            }
        }
        self.prev = ticks;
        self.t = Some(now);
        self.s.gpu_usage(&mut procs, dt, now);
        let mut out = BTreeMap::new();
        out.insert("cpu".to_string(), top_list(procs.values().map(|p| (p.name.clone(), p.cpu)), TOP_N));
        out.insert("gpu".to_string(), top_list(procs.values().map(|p| (p.name.clone(), p.gpu)), TOP_N));
        for c in cards {
            out.insert(format!("gpu_{}", c.key), top_list(procs.values().filter(|p| p.gpu_on.contains(&c.maker)).map(|p| (p.name.clone(), p.gpu)), TOP_N));
        }
        out
    }
}

impl Default for TopProcs {
    fn default() -> Self {
        Self::new()
    }
}

/// One reading of what `want` asks for (None: everything); `top`: read the
/// busiest programs too. Blocking: run it off the main loop.
pub fn read_once(s: &mut Sampler, top: Option<&mut TopProcs>, want: Option<&HashSet<String>>, dt: f64) -> Reading {
    let has = |ks: &[&str]| want.is_none_or(|w| ks.iter().any(|k| w.contains(*k)));
    let cards = s.cards();
    let gpu_kinds: Vec<String> = want.map(|w| w.iter().filter(|k| *k == "gpu" || ((k.starts_with("gpu_") || k.starts_with("vram") || k.starts_with("temp_")) && *k != "temp_cpu")).cloned().collect()).unwrap_or_default();
    let (ram_used, ram_total) = if has(&["ram"]) { s.ram() } else { (0, 0) };
    let (down, up) = if has(&["net"]) { s.net(dt) } else { (0.0, 0.0) };
    if !has(&["net"]) {
        s.forget_net();
    }
    if !has(&["cpu"]) {
        s.forget_cpu();
    }
    let (fps, fps_state) = if has(&["fps"]) { s.fps() } else { (None, "off") };
    let watching_top = top.is_some();
    let show_cards = want.is_none() || !gpu_kinds.is_empty() || watching_top;
    let each = if show_cards { s.gpus(want.is_none() || watching_top || needs_nvidia(&cards, &gpu_kinds)) } else { BTreeMap::new() };
    let top = top.map(|t| t.read(&cards)).unwrap_or_default();
    Reading {
        cpu: if has(&["cpu"]) { s.cpu() } else { 0.0 },
        gpu: Sampler::gpu(&each),
        vrams: if show_cards { s.vrams() } else { BTreeMap::new() },
        cpu_temp: if has(&["temp_cpu"]) { s.cpu_temp() } else { None },
        temps: if show_cards { s.temps() } else { BTreeMap::new() },
        gpus: each,
        top,
        ram_used,
        ram_total,
        down,
        up,
        fps,
        fps_state: fps_state.into(),
        history: BTreeMap::new(),
    }
}

// -- text -------------------------------------------------------------------------------------
/// Network speed, short: "0 KB/s", "12 KB/s", "1.2 MB/s".
pub fn rate(n: f64) -> String {
    if n < 1024.0 * 1000.0 {
        format!("{:.0} KB/s", n / 1024.0)
    } else if n < 1024f64.powi(3) {
        format!("{:.1} MB/s", n / 1024f64.powi(2))
    } else {
        format!("{:.2} GB/s", n / 1024f64.powi(3))
    }
}

pub fn vram_pct(r: &Reading, key: &str) -> Option<f64> {
    r.vrams.get(key).copied().flatten().map(|(u, t)| 100.0 * u as f64 / t as f64)
}

/// Video memory, short: "0.4", "3.2", "12" (GB).
pub fn gib(n: u64) -> String {
    let g = n as f64 / 1024f64.powi(3);
    if g < 10.0 { format!("{g:.1}") } else { format!("{g:.0}") }
}

/// "3.2 / 8.0 GB", "–" without a reading (an NVIDIA card asleep).
pub fn vram_text(r: &Reading, key: &str) -> String {
    match r.vrams.get(key).copied().flatten() {
        Some((u, t)) => format!("{} / {} GB", gib(u), gib(t)),
        None => "–".into(),
    }
}

/// The menu bar's text for one figure.
pub fn text(cards: &[Card], kind: &str, r: &Reading) -> String {
    let maker_of = |key: &str| cards.iter().find(|c| c.key == key).map(|c| c.maker.clone()).unwrap_or_else(|| key.to_uppercase());
    let pct = |name: &str, v: Option<f64>| v.map_or(format!("{name} –"), |v| format!("{name} {v:.0}%"));
    let (vk, tk) = (vram_kinds(cards), temp_kinds(cards));
    match kind {
        "cpu" => format!("CPU {:.0}%", r.cpu),
        "gpu" => pct("GPU", r.gpu),
        "temp_cpu" => r.cpu_temp.map_or("CPU –".into(), |v| format!("CPU {v:.0}°C")),
        "ram" => format!("RAM {:.0}%", r.ram_pct()),
        "net" => format!("↓ {}  ↑ {}", rate(r.down), rate(r.up)),
        "fps" => r.fps.map_or("– FPS".into(), |f| format!("{f} FPS")),
        k if k.starts_with("gpu_") => pct(&maker_of(&k[4..]), r.gpus.get(&k[4..]).copied().flatten()),
        k if key_of(&vk, k).is_some() => {
            // "VRAM 3.2 GB", "NVIDIA VRAM 3.2 GB"
            let key = key_of(&vk, k).unwrap();
            let name = if k == "vram" { "VRAM".to_string() } else { format!("{} VRAM", maker_of(key)) };
            r.vrams.get(key).copied().flatten().map_or(format!("{name} –"), |(u, _)| format!("{name} {} GB", gib(u)))
        }
        k if key_of(&tk, k).is_some() => {
            // "GPU 55°C", "NVIDIA 55°C"
            let key = key_of(&tk, k).unwrap();
            let name = if k == "temp_gpu" { "GPU".to_string() } else { maker_of(key) };
            r.temps.get(key).copied().flatten().map_or(format!("{name} –"), |v| format!("{name} {v:.0}°C"))
        }
        _ => String::new(),
    }
}

/// Keep the little graphs' history (HISTORY samples per figure).
pub fn remember(history: &mut BTreeMap<String, Vec<f64>>, cards: &[Card], r: &Reading) {
    let mut rows: Vec<(String, Option<f64>)> = vec![("cpu".into(), Some(r.cpu)), ("gpu".into(), r.gpu), ("ram".into(), Some(r.ram_pct())), ("down".into(), Some(r.down)), ("up".into(), Some(r.up)), ("fps".into(), r.fps.map(|f| f as f64))];
    rows.extend(r.gpus.iter().map(|(k, v)| (format!("gpu_{k}"), *v)));
    rows.extend(vram_kinds(cards).into_iter().map(|(kind, key)| (kind, vram_pct(r, &key))));
    rows.push(("temp_cpu".into(), r.cpu_temp));
    rows.extend(temp_kinds(cards).into_iter().map(|(kind, key)| (kind, r.temps.get(&key).copied().flatten())));
    for (k, v) in rows {
        let h = history.entry(k).or_default();
        h.push(v.unwrap_or(0.0));
        if h.len() > HISTORY {
            h.drain(..h.len() - HISTORY);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// class/drm/cardN/device -> a PCI device whose driver link names `driver`.
    fn card(sys: &Path, n: usize, driver: &str) {
        let dev = sys.join(format!("devices/pci/{n}"));
        let drv = sys.join("bus/pci/drivers").join(driver);
        std::fs::create_dir_all(&dev).unwrap();
        std::fs::create_dir_all(&drv).unwrap();
        std::os::unix::fs::symlink(&drv, dev.join("driver")).unwrap();
        let card = sys.join(format!("class/drm/card{n}"));
        std::fs::create_dir_all(&card).unwrap();
        std::os::unix::fs::symlink(&dev, card.join("device")).unwrap();
        std::fs::create_dir_all(sys.join(format!("class/drm/card{n}-HDMI-A-1"))).unwrap(); // a connector
    }

    #[test]
    fn each_card_on_its_own() {
        let d = tempfile::tempdir().unwrap();
        let sys = d.path().join("sys");
        card(&sys, 0, "amdgpu");
        card(&sys, 1, "nvidia");
        card(&sys, 2, "amdgpu");
        let cards = gpu_cards(&sys);
        assert_eq!(cards.iter().map(|c| (c.key.as_str(), c.maker.as_str(), c.card.as_str())).collect::<Vec<_>>(), [("amd", "AMD", "card0"), ("nvidia", "NVIDIA", "card1"), ("amd2", "AMD", "card2")]);
        assert_eq!(kinds(&cards, ""), ["cpu", "gpu_amd", "gpu_nvidia", "gpu_amd2", "vram_amd", "vram_nvidia", "vram_amd2", "temp_amd", "temp_nvidia", "temp_amd2", "ram", "net", "fps"]);
        assert_eq!(kinds(&cards[..1], "/x"), ["cpu", "gpu", "vram", "temp_cpu", "temp_gpu", "ram", "net", "fps"]);
        std::fs::write(sys.join("class/drm/card0/device/gpu_busy_percent"), "37\n").unwrap();
        std::fs::write(sys.join("class/drm/card0/device/mem_info_vram_used"), "1073741824\n").unwrap();
        std::fs::write(sys.join("class/drm/card0/device/mem_info_vram_total"), "4294967296\n").unwrap();
        let mut s = Sampler::new(&d.path().join("proc"), &sys);
        let g = s.gpus(false); // no NVIDIA read asked for
        assert_eq!(g, BTreeMap::from([("amd".into(), Some(37.0)), ("nvidia".into(), None), ("amd2".into(), None)]));
        let r = Reading { vrams: s.vrams(), gpus: g.clone(), gpu: Sampler::gpu(&g), ..Default::default() };
        assert_eq!(vram_text(&r, "amd"), "1.0 / 4.0 GB");
        assert_eq!(text(&cards, "gpu_amd", &r), "AMD 37%");
        assert_eq!(text(&cards, "gpu_nvidia", &r), "NVIDIA –");
        assert_eq!(text(&cards, "vram_amd", &r), "AMD VRAM 1.0 GB");
        assert!(needs_nvidia(&cards, &["vram_nvidia".into()]) && !needs_nvidia(&cards, &["gpu_amd".into()]));
    }

    #[test]
    fn nvidia_rows_and_texts() {
        let (p, m, t) = parse_nvidia("12, 512, 8192, 55\nbroken\n7, 0, 0, x\n");
        assert_eq!(p, [12.0, 7.0]);
        assert_eq!(m, [Some((512 << 20, 8192 << 20)), None]);
        assert_eq!(t, [Some(55.0), None]);
        assert_eq!(rate(12.0 * 1024.0), "12 KB/s");
        assert_eq!(rate(1.5 * 1024.0 * 1024.0), "1.5 MB/s");
        assert_eq!(gib(12 * 1024u64.pow(3)), "12");
        let r = Reading { cpu: 12.4, ram_used: 1, ram_total: 4, down: 2048.0, ..Default::default() };
        assert_eq!(text(&[], "cpu", &r), "CPU 12%");
        assert_eq!(text(&[], "ram", &r), "RAM 25%");
        assert_eq!(text(&[], "net", &r), "↓ 2 KB/s  ↑ 0 KB/s");
        assert_eq!(text(&[], "fps", &r), "– FPS");
        assert_eq!(top_list([("a".into(), 1.0), ("b".into(), 0.2), ("a".into(), 2.0), ("c".into(), 0.4)], 5), [("a".to_string(), 3.0)]);
        let mut h = BTreeMap::new();
        for _ in 0..40 {
            remember(&mut h, &[], &r);
        }
        assert_eq!(h["cpu"].len(), HISTORY);
    }
}
