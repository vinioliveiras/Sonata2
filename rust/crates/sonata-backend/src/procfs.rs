//! Processes and system counters read straight from /proc and /sys
//! (activity/procfs.py; no GTK): what Activity Monitor and the menu bar's
//! figures show. The window samples it off the main loop.
//!
//! ```text
//! let mut s = Sampler::new("/proc", "/sys");   // a fake root in tests
//! let snap = s.sample(&["io"]);                // snap.procs, .cpu, .memory, .disks, .interfaces, .gpus, .battery
//! snap.procs[&pid].cpu                         // % of one core since the previous sample
//! s.cpu_info()                                 // model, cores, logical processors, base speed (read once)
//! ```
//!
//! Rates (CPU %, disk and network per second) need two samples: the first
//! sample reports them as 0.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

/// /proc/diskstats counts 512-byte sectors
pub const SECTOR: u64 = 512;

pub fn clk_tck() -> f64 {
    let v = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if v > 0 { v as f64 } else { 100.0 }
}

pub fn page_size() -> u64 {
    let v = unsafe { libc::sysconf(libc::_SC_PAGESIZE) };
    if v > 0 { v as u64 } else { 4096 }
}

pub fn read(path: impl AsRef<Path>) -> String {
    std::fs::read(path).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default()
}

fn is_digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

/// An integer, with an optional minus sign (Python's `t.lstrip("-").isdigit()`).
fn signed(s: &str) -> Option<i64> {
    if is_digits(s.trim_start_matches('-')) { s.parse().ok() } else { None }
}

// -- parsing (one file each; testable on strings) ---------------------------------------------
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct CpuTicks {
    pub user: i64,
    pub system: i64,
    pub idle: i64,
    pub total: i64,
}

/// The "cpu" line of /proc/stat -> jiffies by kind.
pub fn parse_cpu_line(line: &str) -> CpuTicks {
    let mut v: Vec<i64> = line.split_whitespace().skip(1).map(|x| x.parse().unwrap_or(0)).collect();
    v.resize(v.len().max(8), 0);
    let (user, nice, system, idle, iowait, irq, softirq, steal) = (v[0], v[1], v[2], v[3], v[4], v[5], v[6], v[7]);
    CpuTicks { user: user + nice, system: system + irq + softirq + steal, idle: idle + iowait, total: user + nice + system + idle + iowait + irq + softirq + steal }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CpuPct {
    pub user: f64,
    pub system: f64,
    pub idle: f64,
}

impl Default for CpuPct {
    fn default() -> Self {
        Self { user: 0.0, system: 0.0, idle: 100.0 }
    }
}

/// System / User / Idle % of all CPUs between two parse_cpu_line() results.
pub fn cpu_percent(prev: &CpuTicks, cur: &CpuTicks) -> CpuPct {
    let total = cur.total - prev.total;
    if total <= 0 {
        return CpuPct::default();
    }
    let p = |a: i64, b: i64| 100.0 * (a - b).max(0) as f64 / total as f64;
    CpuPct { user: p(cur.user, prev.user), system: p(cur.system, prev.system), idle: p(cur.idle, prev.idle) }
}

/// /proc/meminfo -> bytes by key (MemTotal, MemAvailable, ...).
pub fn parse_meminfo(text: &str) -> HashMap<String, u64> {
    let mut out = HashMap::new();
    for line in text.lines() {
        let (key, rest) = line.split_once(':').unwrap_or((line, ""));
        let parts: Vec<&str> = rest.split_whitespace().collect();
        if let Some(n) = parts.first().filter(|p| is_digits(p)).and_then(|p| p.parse::<u64>().ok()) {
            out.insert(key.trim().to_string(), n * if parts.len() > 1 { 1024 } else { 1 });
        }
    }
    out
}

/// macOS's Memory tab figures (bytes; pressure 0..1).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Memory {
    pub total: u64,
    pub used: u64,
    pub cached: u64,
    pub swap: u64,
    pub swap_total: u64,
    pub app: u64,
    pub wired: u64,
    pub compressed: u64,
    pub available: u64,
    pub pressure: f64,
    pub free: u64,
    pub modified: u64,
    pub committed: u64,
    pub commit_limit: u64,
    pub paged_pool: u64,
    pub non_paged_pool: u64,
}

pub fn memory_summary(mi: &HashMap<String, u64>) -> Memory {
    let g = |k: &str| mi.get(k).copied().unwrap_or(0);
    let total = g("MemTotal");
    let avail = mi.get("MemAvailable").copied().unwrap_or(g("MemFree") + g("Cached"));
    let swap = g("SwapTotal").saturating_sub(g("SwapFree"));
    let used = total.saturating_sub(avail);
    // macOS pressure: how hard the system works to find free memory. Linux
    // has no single figure; what is not available, raised when swap is busy.
    let mut pressure = if total > 0 { used as f64 / total as f64 } else { 0.0 };
    if g("SwapTotal") > 0 {
        pressure = pressure.max((swap as f64 / g("SwapTotal") as f64).min(1.0));
    }
    Memory {
        total,
        used,
        cached: g("Cached") + g("Buffers"),
        swap,
        swap_total: g("SwapTotal"),
        app: g("AnonPages"),
        wired: g("KernelStack") + g("PageTables") + g("SUnreclaim"),
        compressed: g("Zswap"),
        available: avail,
        pressure: pressure.min(1.0),
        free: g("MemFree"),
        modified: g("Dirty") + g("Writeback"),
        committed: g("Committed_AS"),
        commit_limit: g("CommitLimit"),
        paged_pool: g("SReclaimable"),
        non_paged_pool: g("SUnreclaim"),
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Stat {
    pub comm: String,
    pub state: String,
    pub ppid: i64,
    pub ticks: i64,
    pub threads: i64,
    pub start: i64,
    pub rss: u64,
}

/// /proc/<pid>/stat -> the fields Activity Monitor needs. The name sits in
/// parentheses and may hold spaces or ')' itself: split at the last ')'.
pub fn parse_stat(text: &str) -> Option<Stat> {
    let (a, b) = (text.find('(')?, text.rfind(')')?);
    if b < a {
        return None;
    }
    let rest: Vec<&str> = text.get(b + 2..).unwrap_or("").split_whitespace().collect();
    if rest.len() < 22 {
        return None;
    }
    let n = |i: usize| rest[i].parse::<i64>().ok();
    Some(Stat { comm: text[a + 1..b].to_string(), state: rest[0].to_string(), ppid: n(1)?, ticks: n(11)? + n(12)?, threads: n(17)?, start: n(19)?, rss: n(21)?.max(0) as u64 * page_size() })
}

/// /proc/<pid>/io -> (bytes read, bytes written) from storage.
pub fn parse_io(text: &str) -> Option<(i64, i64)> {
    let mut vals = HashMap::new();
    for line in text.lines() {
        let (k, v) = line.split_once(':').unwrap_or((line, ""));
        vals.insert(k.trim(), v.trim());
    }
    Some((vals.get("read_bytes")?.parse().ok()?, vals.get("write_bytes")?.parse().ok()?))
}

pub fn parse_status_swap(text: &str) -> u64 {
    for line in text.lines() {
        if line.starts_with("VmSwap:") {
            let parts: Vec<&str> = line.split_whitespace().collect();
            return parts.get(1).filter(|p| is_digits(p)).and_then(|p| p.parse::<u64>().ok()).map_or(0, |n| n * 1024);
        }
    }
    0
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Net {
    pub rx_bytes: u64,
    pub rx_packets: u64,
    pub tx_bytes: u64,
    pub tx_packets: u64,
    pub rx_bytes_ps: f64,
    pub rx_packets_ps: f64,
    pub tx_bytes_ps: f64,
    pub tx_packets_ps: f64,
}

/// /proc/net/dev -> {iface: rx/tx bytes and packets}.
pub fn parse_net_dev(text: &str) -> BTreeMap<String, Net> {
    let mut out = BTreeMap::new();
    for line in text.lines().skip(2) {
        let Some((name, rest)) = line.split_once(':') else { continue };
        let v: Vec<u64> = rest.split_whitespace().map(|x| x.parse().unwrap_or(0)).collect();
        if v.len() < 10 {
            continue;
        }
        out.insert(name.trim().to_string(), Net { rx_bytes: v[0], rx_packets: v[1], tx_bytes: v[8], tx_packets: v[9], ..Default::default() });
    }
    out
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Disk {
    pub reads: u64,
    pub writes: u64,
    pub read_bytes: u64,
    pub write_bytes: u64,
    /// time spent doing I/O: the disk's active time
    pub io_ms: u64,
    pub reads_ps: f64,
    pub writes_ps: f64,
    pub read_bytes_ps: f64,
    pub write_bytes_ps: f64,
    pub io_ms_ps: f64,
    pub active: f64,
    pub capacity: u64,
    pub model: String,
    pub kind: String,
}

/// /proc/diskstats per whole disk (partitions, loop and ram devices would
/// count the same I/O twice). whole: the names of /sys/block (None: guess).
pub fn parse_disks(text: &str, whole: Option<&HashSet<String>>) -> BTreeMap<String, Disk> {
    let rows: Vec<Vec<&str>> = text.lines().map(|l| l.split_whitespace().collect::<Vec<_>>()).filter(|r| r.len() >= 10).collect();
    let names: HashSet<&str> = rows.iter().map(|r| r[2]).collect();
    let mut out = BTreeMap::new();
    for r in &rows {
        let name = r[2];
        if ["loop", "ram", "zram", "dm-", "md", "sr"].iter().any(|p| name.starts_with(p)) {
            continue;
        }
        match whole {
            Some(w) if !w.contains(name) => continue,
            None if names.iter().any(|n| *n != name && name.starts_with(n)) => continue, // sda1 of sda
            _ => {}
        }
        let n = |i: usize| r.get(i).and_then(|x| x.parse::<u64>().ok()).unwrap_or(0);
        out.insert(name.to_string(), Disk { reads: n(3), read_bytes: n(5) * SECTOR, writes: n(7), write_bytes: n(9) * SECTOR, io_ms: if r.len() > 12 { n(12) } else { 0 }, ..Default::default() });
    }
    out
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct DiskTotals {
    pub reads: u64,
    pub writes: u64,
    pub read_bytes: u64,
    pub write_bytes: u64,
    pub reads_ps: f64,
    pub writes_ps: f64,
    pub read_bytes_ps: f64,
    pub write_bytes_ps: f64,
}

/// /proc/diskstats summed over the whole disks (see parse_disks).
pub fn parse_diskstats(text: &str, whole: Option<&HashSet<String>>) -> DiskTotals {
    let mut t = DiskTotals::default();
    for d in parse_disks(text, whole).values() {
        t.reads += d.reads;
        t.writes += d.writes;
        t.read_bytes += d.read_bytes;
        t.write_bytes += d.write_bytes;
    }
    t
}

#[derive(Clone, Debug, PartialEq)]
pub struct CpuInfo {
    pub model: String,
    pub logical: usize,
    pub cores: usize,
    pub sockets: usize,
    pub mhz: f64,
    pub base_mhz: f64,
}

/// /proc/cpuinfo -> model, logical processors, cores, sockets, MHz (now).
pub fn parse_cpuinfo(text: &str) -> CpuInfo {
    let (mut model, mut logical) = (String::new(), 0usize);
    let (mut cores, mut sockets) = (HashSet::new(), HashSet::new());
    let mut mhz: Vec<f64> = Vec::new();
    let (mut phys, mut core): (Option<String>, Option<String>) = (None, None);
    for line in text.lines().chain(std::iter::once("")) {
        let (k, v) = line.split_once(':').unwrap_or((line, ""));
        let (k, v) = (k.trim(), v.trim());
        match k {
            "processor" => logical += 1,
            "model name" | "Model" | "Hardware" if model.is_empty() => model = v.to_string(),
            "physical id" => {
                phys = Some(v.to_string());
                sockets.insert(v.to_string());
            }
            "core id" => core = Some(v.to_string()),
            "cpu MHz" => {
                if let Ok(f) = v.parse() {
                    mhz.push(f);
                }
            }
            _ if line.trim().is_empty() => {
                if let Some(c) = core.take() {
                    cores.insert((phys.clone(), c));
                }
                phys = None;
            }
            _ => {}
        }
    }
    let logical1 = if logical > 0 { logical } else { 1 };
    CpuInfo {
        model: if model.is_empty() { sonata_i18n::n_!("Processor").into() } else { model }, // shown through gettext
        logical: logical1,
        cores: if !cores.is_empty() { cores.len() } else { logical1 },
        sockets: sockets.len().max(1),
        mhz: if mhz.is_empty() { 0.0 } else { mhz.iter().sum::<f64>() / mhz.len() as f64 },
        base_mhz: 0.0,
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Battery {
    pub percent: Option<i64>,
    pub status: String,
    /// remaining: to empty when discharging, to full when charging
    pub seconds: Option<i64>,
    pub on_ac: bool,
}

/// First battery of /sys/class/power_supply; None without a battery.
pub fn battery_info(sys_root: &Path) -> Option<Battery> {
    let base = sys_root.join("class/power_supply");
    let mut names: Vec<String> = std::fs::read_dir(&base).ok()?.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
    names.sort();
    let bat = names.iter().find(|n| read(base.join(n).join("type")).trim() == "Battery" || n.starts_with("BAT"))?;
    let b = base.join(bat);
    let num = |name: &str| signed(read(b.join(name)).trim());
    let st = read(b.join("status"));
    let status = if st.trim().is_empty() { "Unknown".to_string() } else { st.trim().to_string() };
    let mut pct = num("capacity");
    let (mut now, mut full, mut rate) = (num("energy_now"), num("energy_full"), num("power_now"));
    if now.is_none() {
        (now, full, rate) = (num("charge_now"), num("charge_full"), num("current_now"));
    }
    if let (None, Some(n), Some(f)) = (pct, now, full.filter(|f| *f != 0)) {
        pct = Some((100.0 * n as f64 / f as f64).round_ties_even() as i64);
    }
    let mut seconds = None;
    if let Some(r) = rate.filter(|r| *r != 0).map(i64::abs) {
        if status == "Discharging" {
            seconds = now.map(|n| (3600.0 * n as f64 / r as f64) as i64);
        } else if status == "Charging" {
            if let (Some(n), Some(f)) = (now, full.filter(|f| *f != 0)) {
                seconds = Some((3600.0 * (f - n).max(0) as f64 / r as f64) as i64);
            }
        }
    }
    let on_ac = names.iter().any(|n| matches!(read(base.join(n).join("type")).trim(), "Mains" | "USB") && read(base.join(n).join("online")).trim() == "1");
    Some(Battery { percent: pct, on_ac: on_ac || status == "Charging" || status == "Full", status, seconds })
}

// -- sampling ---------------------------------------------------------------------------------
/// A process state's name ("" for running / sleeping); English, shown through gettext.
pub fn state_name(state: &str) -> &'static str {
    match state {
        "T" | "t" => sonata_i18n::n_!("Suspended"),
        "Z" => sonata_i18n::n_!("Zombie"),
        "D" => sonata_i18n::n_!("Waiting for disk"),
        _ => "",
    }
}

/// The Steam game a process belongs to: Steam starts every game process
/// (Proton, wine, the game) with SteamAppId set. Read once per process.
pub fn steam_appid(d: &Path) -> String {
    let Ok(env) = std::fs::read(d.join("environ")) else { return String::new() };
    for entry in env.split(|b| *b == 0) {
        for key in [&b"SteamAppId="[..], b"SteamGameId="] {
            if let Some(v) = entry.strip_prefix(key) {
                let v = String::from_utf8_lossy(v);
                if is_digits(&v) && v != "0" {
                    return v.into_owned();
                }
            }
        }
    }
    String::new()
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Proc {
    pub pid: i64,
    pub name: String,
    pub comm: String,
    pub cmdline: String,
    /// argv[0]'s base name ("" for kernel threads)
    pub exe: String,
    pub uid: i64,
    pub user: String,
    pub ppid: i64,
    pub threads: i64,
    pub rss: u64,
    pub ticks: i64,
    /// % of one core since the previous sample
    pub cpu: f64,
    /// seconds
    pub cpu_time: f64,
    /// epoch seconds
    pub started: f64,
    pub swap: u64,
    /// -1: not readable (another user's process)
    pub read_bytes: i64,
    pub write_bytes: i64,
    /// bytes/s since the previous sample
    pub read_ps: f64,
    pub write_ps: f64,
    /// % of one core over the process's life
    pub avg_cpu: f64,
    /// clock ticks after boot (with pid: the process's identity)
    pub start_ticks: i64,
    pub state: String,
    /// % of its busiest GPU engine since the previous sample
    pub gpu: f64,
    /// the Steam game it belongs to (SteamAppId), "" for others
    pub steam: String,
    /// the graphics card(s) it uses: "NVIDIA", "AMD", "AMD, NVIDIA"; "" none
    pub gpu_on: String,
}

impl Proc {
    pub fn status(&self) -> &'static str {
        state_name(&self.state)
    }

    pub fn key(&self) -> (i64, i64) {
        (self.pid, self.start_ticks)
    }
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct GpuInfo {
    pub maker: String,
    pub asleep: bool,
    pub vram: Option<(u64, u64)>,
}

#[derive(Clone, Debug, Default)]
pub struct Snapshot {
    pub procs: BTreeMap<i64, Proc>,
    pub cpu: CpuPct,
    pub threads: i64,
    pub memory: Memory,
    pub disk: DiskTotals,
    pub disks: BTreeMap<String, Disk>,
    pub net: Net,
    pub interfaces: BTreeMap<String, Net>,
    /// card -> busy % (None: no reading)
    pub gpus: BTreeMap<String, Option<f64>>,
    pub gpu_info: BTreeMap<String, GpuInfo>,
    pub battery: Option<Battery>,
    pub cpu_mhz: f64,
    pub uptime: f64,
    pub interval: f64,
}

// -- GPU use per process ----------------------------------------------------------------------
// The kernel's DRM fdinfo (amdgpu, i915, xe, nouveau, msm...): every open
// /dev/dri/* file of a process lists its client and the nanoseconds each
// engine was busy for it. NVIDIA's own driver doesn't fill that in: its
// numbers come from `nvidia-smi pmon` (NvidiaUsage), only while the card is
// awake anyway -- asking would wake a sleeping laptop GPU.
/// a process's fd list is checked this often...
const FD_RESCAN_S: f64 = 5.0;
/// ...its links resolved only when that list changed, or this often (reused fd numbers)
const FD_FULL_S: f64 = 60.0;

/// driver -> the card's maker (Task Manager)
pub fn card_name(driver: &str) -> Option<&'static str> {
    Some(match driver {
        "amdgpu" | "radeon" => "AMD",
        "i915" | "xe" => "Intel",
        "nvidia" | "nvidia-drm" | "nouveau" => "NVIDIA",
        _ => return None,
    })
}

pub type DrmClient = (String, String);

/// (client key, {engine: busy ns}) of one DRM fdinfo, None if it isn't one.
pub fn parse_drm_fdinfo(text: &str) -> Option<(DrmClient, BTreeMap<String, u64>)> {
    let (mut pdev, mut client) = (None, None);
    let mut engines = BTreeMap::new();
    for line in text.lines() {
        let (k, v) = line.split_once(':').unwrap_or((line, ""));
        let v = v.trim();
        if k == "drm-pdev" {
            pdev = Some(v.to_string());
        } else if k == "drm-client-id" {
            client = Some(v.to_string());
        } else if let Some(engine) = k.strip_prefix("drm-engine-").filter(|_| !k.starts_with("drm-engine-capacity")) {
            if let Some(n) = v.split_whitespace().next().filter(|n| is_digits(n)).and_then(|n| n.parse().ok()) {
                engines.insert(engine.to_string(), n);
            }
        }
    }
    if engines.is_empty() {
        return None;
    }
    Some(((pdev.unwrap_or_default(), client?), engines))
}

/// A program on PATH (shutil.which).
pub fn which(name: &str) -> Option<PathBuf> {
    std::env::var_os("PATH").and_then(|p| std::env::split_paths(&p).map(|d| d.join(name)).find(|f| f.is_file() && f.metadata().is_ok_and(|m| m.mode() & 0o111 != 0)))
}

fn realpath_name(p: &Path) -> String {
    std::fs::canonicalize(p).ok().and_then(|r| r.file_name().map(|n| n.to_string_lossy().into_owned())).unwrap_or_default()
}

fn drm_cards(sys: &Path) -> Vec<PathBuf> {
    let mut v: Vec<PathBuf> = std::fs::read_dir(sys.join("class/drm")).into_iter().flatten().flatten().filter(|e| e.file_name().to_str().is_some_and(|n| n.starts_with("card") && is_digits(&n[4..]))).map(|e| e.path()).collect();
    v.sort();
    v
}

/// pid -> SM % from `nvidia-smi pmon` in the background, only while the
/// NVIDIA card is already awake (runtime PM "active").
pub struct NvidiaUsage {
    pub pids: Arc<Mutex<HashMap<i64, f64>>>,
    busy: Arc<AtomicBool>,
    pub tool: Option<PathBuf>,
    pub devices: Vec<PathBuf>,
}

impl NvidiaUsage {
    pub fn new(sys: &Path) -> Self {
        let devices = drm_cards(sys).into_iter().filter(|c| realpath_name(&c.join("device/driver")) == "nvidia").filter_map(|c| std::fs::canonicalize(c.join("device")).ok()).collect();
        Self { pids: Arc::default(), busy: Arc::default(), tool: which("nvidia-smi"), devices }
    }

    pub fn awake(&self) -> bool {
        self.devices.iter().any(|d| matches!(read(d.join("power/runtime_status")).trim(), "active" | ""))
    }

    pub fn refresh(&self) {
        let Some(tool) = self.tool.clone().filter(|_| !self.devices.is_empty()) else { return };
        if self.busy.load(Ordering::SeqCst) {
            return;
        }
        if !self.awake() {
            self.pids.lock().unwrap().clear();
            return;
        }
        self.busy.store(true, Ordering::SeqCst);
        let (pids, busy) = (self.pids.clone(), self.busy.clone());
        std::thread::spawn(move || {
            let out = pmon_output(&tool, PMON_TIMEOUT_S);
            *pids.lock().unwrap() = parse_pmon(&out);
            busy.store(false, Ordering::SeqCst);
        });
    }
}

/// nvidia-smi gets this long (procfs.py's timeout=5): a hung one left
/// `busy` set for good and the per-process GPU column stopped updating.
pub const PMON_TIMEOUT_S: u64 = 5;

/// One `nvidia-smi pmon` sample's text ("" when it fails or hangs).
pub fn pmon_output(tool: &Path, timeout_s: u64) -> String {
    let tool = tool.to_string_lossy();
    crate::run::run(&[&tool, "pmon", "-c", "1", "-s", "u"], timeout_s).1
}

/// `nvidia-smi pmon -s u` -> {pid: SM %}.
pub fn parse_pmon(text: &str) -> HashMap<i64, f64> {
    let mut out: HashMap<i64, f64> = HashMap::new();
    for line in text.lines() {
        let f: Vec<&str> = line.split_whitespace().collect();
        if f.len() > 3 && !line.starts_with('#') && is_digits(f[1]) && is_digits(&f[3].replace('.', "")) {
            if let (Ok(pid), Ok(pct)) = (f[1].parse::<i64>(), f[3].parse::<f64>()) {
                let e = out.entry(pid).or_insert(0.0);
                *e = e.max(pct);
            }
        }
    }
    out
}

fn rate(cur: f64, prev: Option<f64>, dt: f64) -> f64 {
    match prev {
        Some(p) if dt > 0.0 => ((cur - p) / dt).max(0.0),
        _ => 0.0,
    }
}

type Key = (i64, i64);

/// Static facts of a process, read once: (name, comm, cmdline, exe, uid, user, steam).
type StaticInfo = (String, String, String, String, i64, String, String);

/// Reads everything Activity Monitor shows; keeps the previous sample for
/// rates. One sample at a time (the window never overlaps them).
pub struct Sampler {
    pub proc: PathBuf,
    pub sys: PathBuf,
    /// seconds, monotonic (tests move their own)
    pub clock: Box<dyn Fn() -> f64 + Send>,
    statics: HashMap<Key, StaticInfo>,
    users: HashMap<i64, String>,
    prev_ticks: HashMap<Key, i64>,
    prev_io: HashMap<Key, (i64, i64)>,
    prev_cpu: Option<CpuTicks>,
    prev_disks: BTreeMap<String, Disk>,
    prev_disk_tot: Option<DiskTotals>,
    prev_net: Option<BTreeMap<String, Net>>,
    prev_t: Option<f64>,
    disk_static: HashMap<String, (u64, String, String)>,
    cpu_info: Option<CpuInfo>,
    gpu_fds: HashMap<Key, Vec<PathBuf>>,
    gpu_scan: HashMap<Key, (f64, f64, u64)>,
    gpu_cards: HashMap<Key, BTreeSet<String>>,
    pdev_names: HashMap<String, String>,
    prev_gpu: HashMap<DrmClient, BTreeMap<String, u64>>,
    pub nvidia: NvidiaUsage,
    /// made on the first NVIDIA card: nvidia-smi's awake check and cached run shared
    stats: Option<crate::stats::Sampler>,
    pub gpu_info: BTreeMap<String, GpuInfo>,
    pub boot_time: f64,
}

pub fn monotonic() -> f64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9
}

fn epoch() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// A user's name (its number when unknown).
pub fn user_name(uid: i64) -> String {
    let mut buf = vec![0 as libc::c_char; 4096];
    let mut pw: libc::passwd = unsafe { std::mem::zeroed() };
    let mut out: *mut libc::passwd = std::ptr::null_mut();
    let ok = uid >= 0 && unsafe { libc::getpwuid_r(uid as libc::uid_t, &mut pw, buf.as_mut_ptr(), buf.len(), &mut out) } == 0 && !out.is_null();
    if ok {
        unsafe { std::ffi::CStr::from_ptr(pw.pw_name) }.to_string_lossy().into_owned()
    } else {
        uid.to_string()
    }
}

impl Sampler {
    pub fn new(proc: impl Into<PathBuf>, sys: impl Into<PathBuf>) -> Self {
        Self::with_clock(proc, sys, Box::new(monotonic))
    }

    pub fn with_clock(proc: impl Into<PathBuf>, sys: impl Into<PathBuf>, clock: Box<dyn Fn() -> f64 + Send>) -> Self {
        let (proc, sys) = (proc.into(), sys.into());
        let nvidia = NvidiaUsage::new(&sys);
        let mut s = Self {
            proc,
            sys,
            clock,
            statics: HashMap::new(),
            users: HashMap::new(),
            prev_ticks: HashMap::new(),
            prev_io: HashMap::new(),
            prev_cpu: None,
            prev_disks: BTreeMap::new(),
            prev_disk_tot: None,
            prev_net: None,
            prev_t: None,
            disk_static: HashMap::new(),
            cpu_info: None,
            gpu_fds: HashMap::new(),
            gpu_scan: HashMap::new(),
            gpu_cards: HashMap::new(),
            pdev_names: HashMap::new(),
            prev_gpu: HashMap::new(),
            nvidia,
            stats: None,
            gpu_info: BTreeMap::new(),
            boot_time: 0.0,
        };
        s.boot_time = s.read_boot_time();
        s
    }

    fn read_boot_time(&self) -> f64 {
        for line in read(self.proc.join("stat")).lines() {
            if let Some(v) = line.strip_prefix("btime ") {
                if let Ok(t) = v.trim().parse() {
                    return t;
                }
            }
        }
        epoch() - self.uptime()
    }

    pub fn uptime(&self) -> f64 {
        read(self.proc.join("uptime")).split_whitespace().next().and_then(|v| v.parse().ok()).unwrap_or(0.0)
    }

    pub fn user_name(&mut self, uid: i64) -> String {
        self.users.entry(uid).or_insert_with(|| user_name(uid)).clone()
    }

    /// Model, cores, logical processors, sockets, base speed (MHz); read once.
    pub fn cpu_info(&mut self) -> CpuInfo {
        if self.cpu_info.is_none() {
            let mut info = parse_cpuinfo(&read(self.proc.join("cpuinfo")));
            let top = read(self.sys.join("devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq"));
            info.base_mhz = if is_digits(top.trim()) { top.trim().parse::<f64>().unwrap() / 1000.0 } else { info.mhz };
            self.cpu_info = Some(info);
        }
        self.cpu_info.clone().unwrap()
    }

    /// Current speed (MHz): the average of the cores' scaling_cur_freq,
    /// else /proc/cpuinfo's "cpu MHz".
    pub fn cpu_speed(&self) -> f64 {
        let base = self.sys.join("devices/system/cpu");
        let freqs: Vec<f64> = std::fs::read_dir(&base)
            .into_iter()
            .flatten()
            .flatten()
            .filter_map(|e| {
                let n = e.file_name().to_string_lossy().into_owned();
                if !(n.starts_with("cpu") && is_digits(&n[3..])) {
                    return None;
                }
                let v = read(base.join(&n).join("cpufreq/scaling_cur_freq"));
                is_digits(v.trim()).then(|| v.trim().parse::<f64>().unwrap() / 1000.0)
            })
            .collect();
        if !freqs.is_empty() {
            return freqs.iter().sum::<f64>() / freqs.len() as f64;
        }
        parse_cpuinfo(&read(self.proc.join("cpuinfo"))).mhz
    }

    /// card -> busy % (None: no reading right now) for every graphics card.
    /// The NVIDIA driver has no gpu_busy_percent, so reading only that file
    /// hid the dGPU of a Radeon + NVIDIA laptop: NVIDIA's load comes from the
    /// menu bar's stats reader (nvidia-smi, run only while the card is
    /// awake: asking would wake it). Fills self.gpu_info on the way.
    pub fn gpus(&mut self) -> BTreeMap<String, Option<f64>> {
        let mut out = BTreeMap::new();
        let mut info = BTreeMap::new();
        let mut nv: Option<HashMap<String, (Option<f64>, Option<(u64, u64)>)>> = None;
        for card in drm_cards(&self.sys) {
            let n = card.file_name().unwrap().to_string_lossy().into_owned();
            let dev = card.join("device");
            let maker = card_name(&realpath_name(&dev.join("driver")));
            let v = read(dev.join("gpu_busy_percent"));
            let mut busy = is_digits(v.trim()).then(|| v.trim().parse::<f64>().unwrap());
            if busy.is_none() && maker.is_none() {
                continue; // not a GPU we can say anything about
            }
            let asleep = read(dev.join("power/runtime_status")).trim() == "suspended";
            let mut vram = None;
            if maker == Some("NVIDIA") {
                let cards = nv.get_or_insert_with(|| self.nvidia_cards());
                (busy, vram) = cards.get(&n).copied().unwrap_or((None, None));
            } else {
                let (used, total) = (read(dev.join("mem_info_vram_used")), read(dev.join("mem_info_vram_total")));
                if let (Ok(u), Ok(t)) = (used.trim().parse::<u64>(), total.trim().parse::<u64>()) {
                    if t > 0 {
                        vram = Some((u, t));
                    }
                }
            }
            out.insert(n.clone(), busy);
            info.insert(n, GpuInfo { maker: maker.unwrap_or("").to_string(), asleep, vram });
        }
        self.gpu_info = info;
        out
    }

    /// {cardN: (busy %, VRAM)} of the NVIDIA cards, through the menu bar's
    /// reader (stats) so nvidia-smi's awake check and its cached run are
    /// shared, not duplicated; (None, None) while asleep.
    fn nvidia_cards(&mut self) -> HashMap<String, (Option<f64>, Option<(u64, u64)>)> {
        let (proc, sys) = (self.proc.clone(), self.sys.clone());
        let st = self.stats.get_or_insert_with(|| crate::stats::Sampler::new(&proc, &sys));
        let busy = st.gpus(true);
        let vram = st.vrams();
        st.cards().iter().filter(|c| c.maker == "NVIDIA").map(|c| (c.card.clone(), (busy.get(&c.key).copied().flatten(), vram.get(&c.key).copied().flatten()))).collect()
    }

    fn drm_fdinfos(&mut self, key: Key, d: &Path, now: f64) -> Vec<PathBuf> {
        let (listed, resolved, old_hash) = self.gpu_scan.get(&key).copied().unwrap_or((-1e9, -1e9, 0));
        if now - listed >= FD_RESCAN_S {
            let mut fds: Vec<String> = std::fs::read_dir(d.join("fd")).into_iter().flatten().flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect(); // another user's process: none
            fds.sort();
            let fd_hash = {
                use std::hash::{Hash, Hasher};
                let mut h = std::collections::hash_map::DefaultHasher::new();
                fds.hash(&mut h);
                h.finish()
            };
            if fd_hash == old_hash && now - resolved < FD_FULL_S {
                // same fds as last time: no readlink of every fd of every process each pass
                self.gpu_scan.insert(key, (now, resolved, fd_hash));
                return self.gpu_fds.get(&key).cloned().unwrap_or_default();
            }
            let mut paths = Vec::new();
            let mut cards = BTreeSet::new();
            for fd in &fds {
                let Ok(target) = std::fs::read_link(d.join("fd").join(fd)) else { continue };
                let t = target.to_string_lossy();
                if t.starts_with("/dev/dri/") {
                    paths.push(d.join("fdinfo").join(fd));
                } else if t.starts_with("/dev/nvidia") && is_digits(t.get(11..).unwrap_or("")) {
                    cards.insert("NVIDIA".to_string()); // NVIDIA's own driver
                }
            }
            self.gpu_fds.insert(key, paths);
            self.gpu_scan.insert(key, (now, now, fd_hash));
            self.gpu_cards.insert(key, cards);
        }
        self.gpu_fds.get(&key).cloned().unwrap_or_default()
    }

    /// A GPU's maker from its PCI address (the driver bound to it).
    fn pdev_name(&mut self, pdev: &str) -> String {
        if !self.pdev_names.contains_key(pdev) {
            let link = self.sys.join("bus/pci/devices").join(pdev).join("driver");
            let drv = if !pdev.is_empty() && link.exists() { realpath_name(&link) } else { String::new() };
            self.pdev_names.insert(pdev.to_string(), card_name(&drv).map(str::to_string).unwrap_or(drv));
        }
        self.pdev_names[pdev].clone()
    }

    /// Fills each process's .gpu and .gpu_on (see the GPU section above).
    pub fn gpu_usage(&mut self, procs: &mut BTreeMap<i64, Proc>, dt: f64, now: f64) {
        let mut clients: HashMap<DrmClient, BTreeMap<String, u64>> = HashMap::new();
        let mut owner: HashMap<DrmClient, i64> = HashMap::new();
        let pids: Vec<(i64, Key)> = procs.values().map(|p| (p.pid, p.key())).collect();
        for (pid, key) in pids {
            let mut cards: BTreeSet<String> = self.gpu_cards.get(&key).cloned().unwrap_or_default();
            let d = self.proc.join(pid.to_string());
            for path in self.drm_fdinfos(key, &d, now) {
                let text = read(&path);
                let parsed = parse_drm_fdinfo(&text);
                let drv = text.lines().find_map(|l| l.strip_prefix("drm-driver:")).map(|v| v.trim().to_string()).unwrap_or_default();
                if !drv.is_empty() {
                    cards.insert(card_name(&drv).map(str::to_string).unwrap_or(drv));
                } else if let Some(((pdev, _), _)) = parsed.as_ref().filter(|p| !p.0 .0.is_empty()) {
                    let n = self.pdev_name(pdev);
                    cards.insert(n);
                }
                if let Some((client, engines)) = parsed {
                    if !clients.contains_key(&client) {
                        // dup'ed fds share one client
                        owner.insert(client.clone(), pid);
                        clients.insert(client, engines);
                    }
                }
            }
            cards.remove("");
            if let Some(p) = procs.get_mut(&pid) {
                p.gpu_on = cards.into_iter().collect::<Vec<_>>().join(", ");
            }
        }
        for (client, engines) in &clients {
            let Some(prev) = self.prev_gpu.get(client).filter(|_| dt > 0.0) else { continue };
            let busy = engines.iter().map(|(e, ns)| 100.0 * (*ns as f64 - *prev.get(e).unwrap_or(ns) as f64) / (dt * 1e9)).fold(0.0, f64::max);
            if let Some(p) = procs.get_mut(&owner[client]) {
                p.gpu = p.gpu.max(busy).min(100.0);
            }
        }
        self.prev_gpu = clients;
        let live: HashSet<Key> = procs.values().map(Proc::key).collect();
        self.gpu_fds.retain(|k, _| live.contains(k));
        self.gpu_scan.retain(|k, _| live.contains(k));
        self.gpu_cards.retain(|k, _| live.contains(k));
        self.nvidia.refresh();
        let nv = self.nvidia.pids.lock().unwrap().clone();
        for (pid, pct) in nv {
            if let Some(p) = procs.get_mut(&pid) {
                p.gpu = p.gpu.max(pct);
                if !p.gpu_on.contains("NVIDIA") {
                    let mut set: BTreeSet<String> = p.gpu_on.split(", ").filter(|s| !s.is_empty()).map(str::to_string).collect();
                    set.insert("NVIDIA".into());
                    p.gpu_on = set.into_iter().collect::<Vec<_>>().join(", ");
                }
            }
        }
    }

    fn disk_info(&mut self, name: &str) -> (u64, String, String) {
        let sys = self.sys.clone();
        self.disk_static
            .entry(name.to_string())
            .or_insert_with(|| {
                let b = sys.join("block").join(name);
                let size = read(b.join("size"));
                let kind = if read(b.join("queue/rotational")).trim() == "1" { "HDD" } else { "SSD" };
                (size.trim().parse::<u64>().map_or(0, |s| s * SECTOR), read(b.join("device/model")).trim().to_string(), kind.to_string())
            })
            .clone()
    }

    fn static_info(&mut self, pid: i64, d: &Path, st: &Stat) -> StaticInfo {
        let key = (pid, st.start);
        if let Some(i) = self.statics.get(&key) {
            return i.clone();
        }
        let raw = read(d.join("cmdline"));
        let argv: Vec<&str> = raw.split('\0').filter(|a| !a.is_empty()).collect();
        let exe = argv.first().map(|a| a.rsplit('/').next().unwrap_or(a).to_string()).unwrap_or_default();
        let cmdline = if argv.is_empty() { format!("[{}]", st.comm) } else { argv.join(" ") };
        let uid = std::fs::metadata(d).map(|m| m.uid() as i64).unwrap_or(-1);
        let comm = st.comm.clone();
        // comm is cut at 15 characters: the program's own name reads better
        let cut: String = comm.chars().take(15).collect();
        let name = if !exe.is_empty() && comm.chars().count() >= 15 && exe.starts_with(&cut) { exe.clone() } else { comm.clone() };
        let user = if uid >= 0 { self.user_name(uid) } else { String::new() };
        let cmd: String = cmdline.chars().take(4096).collect();
        let info = (name, comm, cmd, exe, uid, user, steam_appid(d));
        self.statics.insert(key, info.clone());
        info
    }

    /// Every process now (rates not computed yet; see sample). want: "io"
    /// reads /proc/<pid>/io, "swap" VmSwap from status.
    pub fn processes(&mut self, want: &[&str]) -> BTreeMap<i64, Proc> {
        let mut procs = BTreeMap::new();
        let Ok(entries) = std::fs::read_dir(&self.proc) else { return procs };
        let tck = clk_tck();
        for e in entries.flatten() {
            let fname = e.file_name().to_string_lossy().into_owned();
            if !is_digits(&fname) {
                continue;
            }
            let pid: i64 = fname.parse().unwrap();
            let d = self.proc.join(&fname);
            let Some(st) = parse_stat(&read(d.join("stat"))) else { continue }; // gone meanwhile
            let (name, comm, cmdline, exe, uid, user, steam) = self.static_info(pid, &d, &st);
            let mut p = Proc { pid, name, comm, cmdline, exe, uid, user, ppid: st.ppid, steam, threads: st.threads, rss: st.rss, ticks: st.ticks, cpu_time: st.ticks as f64 / tck, start_ticks: st.start, started: self.boot_time + st.start as f64 / tck, state: st.state.clone(), read_bytes: -1, write_bytes: -1, ..Default::default() };
            if want.contains(&"io") {
                if let Some((r, w)) = parse_io(&read(d.join("io"))) {
                    p.read_bytes = r;
                    p.write_bytes = w;
                }
            }
            if want.contains(&"swap") {
                p.swap = parse_status_swap(&read(d.join("status")));
            }
            procs.insert(pid, p);
        }
        procs
    }

    pub fn sample(&mut self, want: &[&str]) -> Snapshot {
        let now = (self.clock)();
        let dt = self.prev_t.map_or(0.0, |p| now - p);
        let mut snap = Snapshot { interval: dt, ..Default::default() };
        let mut procs = self.processes(want);
        let uptime = self.uptime();
        snap.uptime = uptime;
        let tck = clk_tck();
        let (mut ticks, mut ios) = (HashMap::new(), HashMap::new());
        for p in procs.values_mut() {
            let key = p.key();
            ticks.insert(key, p.ticks);
            if let (Some(prev), true) = (self.prev_ticks.get(&key), dt > 0.0) {
                p.cpu = (100.0 * (p.ticks - prev) as f64 / tck / dt).max(0.0);
            }
            if p.read_bytes >= 0 {
                ios.insert(key, (p.read_bytes, p.write_bytes));
                if let (Some(pio), true) = (self.prev_io.get(&key), dt > 0.0) {
                    p.read_ps = ((p.read_bytes - pio.0) as f64 / dt).max(0.0);
                    p.write_ps = ((p.write_bytes - pio.1) as f64 / dt).max(0.0);
                }
            }
            let age = uptime - p.start_ticks as f64 / tck;
            p.avg_cpu = if age > 1.0 { 100.0 * p.cpu_time / age } else { 0.0 };
            snap.threads += p.threads;
        }
        // static info of dead processes goes with them
        if self.statics.len() > 2 * ticks.len() + 64 {
            self.statics.retain(|k, _| ticks.contains_key(k));
        }
        self.prev_ticks = ticks;
        self.prev_io = ios;

        let stat = read(self.proc.join("stat"));
        let cpu = stat.lines().next().filter(|l| l.starts_with("cpu ")).map(parse_cpu_line);
        if let (Some(c), Some(p)) = (cpu, self.prev_cpu) {
            snap.cpu = cpu_percent(&p, &c);
        }
        if cpu.is_some() {
            self.prev_cpu = cpu;
        }
        snap.cpu_mhz = self.cpu_speed();
        snap.memory = memory_summary(&parse_meminfo(&read(self.proc.join("meminfo"))));

        let whole: Option<HashSet<String>> = std::fs::read_dir(self.sys.join("block")).ok().map(|r| r.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect());
        let disks = parse_disks(&read(self.proc.join("diskstats")), whole.as_ref());
        let mut tot = DiskTotals::default();
        for (name, v) in &disks {
            let prev = self.prev_disks.get(name);
            let mut row = v.clone();
            let r = |c: u64, p: Option<u64>| rate(c as f64, p.map(|x| x as f64), dt);
            row.reads_ps = r(v.reads, prev.map(|p| p.reads));
            row.writes_ps = r(v.writes, prev.map(|p| p.writes));
            row.read_bytes_ps = r(v.read_bytes, prev.map(|p| p.read_bytes));
            row.write_bytes_ps = r(v.write_bytes, prev.map(|p| p.write_bytes));
            row.io_ms_ps = r(v.io_ms, prev.map(|p| p.io_ms));
            row.active = (row.io_ms_ps / 10.0).min(100.0); // ms busy per s -> %
            (row.capacity, row.model, row.kind) = self.disk_info(name);
            snap.disks.insert(name.clone(), row);
            tot.reads += v.reads;
            tot.writes += v.writes;
            tot.read_bytes += v.read_bytes;
            tot.write_bytes += v.write_bytes;
        }
        let prev_tot = if self.prev_disks.is_empty() { None } else { self.prev_disk_tot };
        let r = |c: u64, p: Option<u64>| rate(c as f64, p.map(|x| x as f64), dt);
        tot.reads_ps = r(tot.reads, prev_tot.map(|p| p.reads));
        tot.writes_ps = r(tot.writes, prev_tot.map(|p| p.writes));
        tot.read_bytes_ps = r(tot.read_bytes, prev_tot.map(|p| p.read_bytes));
        tot.write_bytes_ps = r(tot.write_bytes, prev_tot.map(|p| p.write_bytes));
        snap.disk = tot;
        self.prev_disks = disks;
        self.prev_disk_tot = Some(tot);

        let ifaces = parse_net_dev(&read(self.proc.join("net/dev")));
        let prev = self.prev_net.clone().unwrap_or_default();
        let mut net = Net::default();
        for (name, v) in &ifaces {
            let p = prev.get(name);
            let mut row = *v;
            let r = |c: u64, p: Option<u64>| rate(c as f64, p.map(|x| x as f64), dt);
            row.rx_bytes_ps = r(v.rx_bytes, p.map(|x| x.rx_bytes));
            row.rx_packets_ps = r(v.rx_packets, p.map(|x| x.rx_packets));
            row.tx_bytes_ps = r(v.tx_bytes, p.map(|x| x.tx_bytes));
            row.tx_packets_ps = r(v.tx_packets, p.map(|x| x.tx_packets));
            if name != "lo" {
                // the loopback is no network traffic
                net.rx_bytes += v.rx_bytes;
                net.rx_packets += v.rx_packets;
                net.tx_bytes += v.tx_bytes;
                net.tx_packets += v.tx_packets;
                net.rx_bytes_ps += row.rx_bytes_ps;
                net.rx_packets_ps += row.rx_packets_ps;
                net.tx_bytes_ps += row.tx_bytes_ps;
                net.tx_packets_ps += row.tx_packets_ps;
            }
            snap.interfaces.insert(name.clone(), row);
        }
        snap.net = net;
        self.prev_net = Some(ifaces);

        snap.gpus = self.gpus();
        snap.gpu_info = self.gpu_info.clone();
        self.gpu_usage(&mut procs, dt, now);
        snap.procs = procs;
        snap.battery = battery_info(&self.sys);
        self.prev_t = Some(now);
        snap
    }
}

// -- sorting / filtering ----------------------------------------------------------------------
/// The search field and All / My Processes filter: name, PID or user.
pub fn matches(p: &Proc, text: &str, mine_uid: Option<i64>) -> bool {
    if mine_uid.is_some_and(|u| p.uid != u) {
        return false;
    }
    if text.is_empty() {
        return true;
    }
    let t = text.to_lowercase();
    p.name.to_lowercase().contains(&t) || p.pid.to_string().contains(&t) || p.user.to_lowercase().contains(&t)
}

// -- formats (Activity Monitor's) -------------------------------------------------------------
/// Binary units like Activity Monitor: "512 bytes", "12.3 KB", "345.6 MB", "1.23 GB".
pub fn fmt_bytes(n: f64) -> String {
    if n < 0.0 {
        return "–".into();
    }
    if n < 1024.0 {
        return format!("{} bytes", n as i64);
    }
    for (unit, p, dec) in [("KB", 1, 1), ("MB", 2, 1), ("GB", 3, 2), ("TB", 4, 2)] {
        if n < 1024f64.powi(p + 1) || unit == "TB" {
            return format!("{:.*} {unit}", dec, n / 1024f64.powi(p));
        }
    }
    unreachable!()
}

/// "12.34", "3:07.21", "1:02:03.45".
pub fn fmt_cpu_time(seconds: f64) -> String {
    // round to hundredths first, so 119.996 rolls over to "2:00.00" (not
    // "1:60.00"); through the exact decimal, as Python's round(x, 2): 12.345
    // is a hair above, and times a hundred it would fall a hair below
    let hundredths: f64 = format!("{:.2}", seconds.max(0.0)).parse().unwrap_or(0.0);
    let cs = (hundredths * 100.0).round_ties_even() as i64;
    let (h, rem) = (cs / 360000, cs % 360000);
    let (m, s) = (rem / 6000, (rem % 6000) as f64 / 100.0);
    if h > 0 {
        format!("{h}:{m:02}:{s:05.2}")
    } else if m > 0 {
        format!("{m}:{s:05.2}")
    } else {
        format!("{s:.2}")
    }
}

/// 1,234,567 (Python's f"{n:,}").
pub fn fmt_count(n: f64) -> String {
    let n = n as i64;
    let s = n.abs().to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    if n < 0 { format!("-{out}") } else { out }
}

/// "3:07" (hours:minutes); None: still calculating (the caller shows that).
pub fn fmt_duration(seconds: Option<f64>) -> Option<String> {
    let s = seconds? as i64;
    Some(format!("{}:{:02}", s / 60 / 60, (s / 60) % 60))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicU64;

    const MEMINFO: &str = "MemTotal:       8000000 kB\nMemFree:        1000000 kB\nMemAvailable:   6000000 kB\nBuffers:         100000 kB\nCached:         2000000 kB\nSwapTotal:      1000000 kB\nSwapFree:        750000 kB\nAnonPages:      1500000 kB\nKernelStack:      10000 kB\nPageTables:       20000 kB\nSUnreclaim:       30000 kB\nHugePages_Total:      0\n";

    /// A /proc and /sys tree in a temp dir, and a clock the test moves.
    struct Fake {
        dir: tempfile::TempDir,
        now: Arc<AtomicU64>,
    }

    impl Fake {
        fn new() -> Self {
            let f = Fake { dir: tempfile::tempdir().unwrap(), now: Arc::new(AtomicU64::new(1000_f64.to_bits())) };
            for b in ["sda", "nvme0n1", "loop0"] {
                std::fs::create_dir_all(f.sys().join("block").join(b)).unwrap();
            }
            f.w_sys("block/sda/size", "1953525168\n");
            f.w_sys("block/sda/queue/rotational", "1\n");
            f.w_sys("block/nvme0n1/device/model", "Fast SSD\n");
            let cpuinfo: String = (0..4).map(|i| format!("processor\t: {i}\nmodel name\t: Test CPU 9000\nphysical id\t: 0\ncore id\t\t: {}\ncpu MHz\t\t: 1000.0\n\n", i / 2)).collect();
            f.w("cpuinfo", &cpuinfo);
            for (i, fr) in [2000000, 3000000].iter().enumerate() {
                f.w_sys(&format!("devices/system/cpu/cpu{i}/cpufreq/scaling_cur_freq"), &format!("{fr}\n"));
            }
            f.w_sys("devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "3500000\n");
            f.w("uptime", "5000.00 4000.00\n");
            f.w("meminfo", MEMINFO);
            f.cpu([100, 0, 50, 800, 50]);
            f.disk(0, 0, 0, 0, 0);
            f.net(0, 0, 999);
            f
        }
        fn proc(&self) -> PathBuf {
            self.dir.path().join("proc")
        }
        fn sys(&self) -> PathBuf {
            self.dir.path().join("sys")
        }
        fn put(path: PathBuf, text: &str) {
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(path, text).unwrap();
        }
        fn w(&self, rel: &str, text: &str) {
            Self::put(self.proc().join(rel), text);
        }
        fn w_sys(&self, rel: &str, text: &str) {
            Self::put(self.sys().join(rel), text);
        }
        fn cpu(&self, v: [i64; 5]) {
            let [u, n, s, i, io] = v;
            self.w("stat", &format!("cpu  {u} {n} {s} {i} {io} 0 0 0 0 0\ncpu0 {u} {n} {s} {i} {io} 0 0 0 0 0\nbtime 1700000000\n"));
        }
        fn disk(&self, sr: u64, sw: u64, reads: u64, writes: u64, io_ms: u64) {
            let rows: Vec<String> = ["sda", "sda1", "nvme0n1", "nvme0n1p1", "loop0"]
                .iter()
                .map(|n| {
                    let k = if *n == "sda" || *n == "nvme0n1" { 1 } else { 7 }; // partitions/loop must not count
                    format!("   8 0 {n} {} 0 {} 0 {} 0 {} 0 0 {} 0", reads * k, sr * k, writes * k, sw * k, io_ms * k)
                })
                .collect();
            self.w("diskstats", &(rows.join("\n") + "\n"));
        }
        fn net(&self, rx: u64, tx: u64, lo: u64) {
            self.w("net/dev", &format!("Inter-|   Receive |  Transmit\n face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets\n    lo: {lo} 10 0 0 0 0 0 0 {lo} 10 0 0 0 0 0 0\n  eth0: {rx} 100 0 0 0 0 0 0 {tx} 50 0 0 0 0 0 0\n"));
        }
        #[allow(clippy::too_many_arguments)]
        fn process(&self, pid: i64, comm: &str, cmdline: &[&str], ticks: (i64, i64), threads: i64, rss_pages: i64, io: Option<(i64, i64)>, swap_kb: i64) {
            let rest = ["S".to_string(), "1".into(), "0".into(), "0".into(), "0".into(), "-1".into(), "0".into(), "0".into(), "0".into(), "0".into(), "0".into(), ticks.0.to_string(), ticks.1.to_string(), "0".into(), "0".into(), "20".into(), "0".into(), threads.to_string(), "0".into(), "100".into(), "0".into(), rss_pages.to_string()];
            self.w(&format!("{pid}/stat"), &format!("{pid} ({comm}) {} 0 0 0\n", rest.join(" ")));
            self.w(&format!("{pid}/cmdline"), &if cmdline.is_empty() { String::new() } else { cmdline.join("\0") + "\0" });
            if let Some((r, w)) = io {
                self.w(&format!("{pid}/io"), &format!("rchar: 1\nwchar: 2\nread_bytes: {r}\nwrite_bytes: {w}\n"));
            }
            self.w(&format!("{pid}/status"), &format!("Name:\t{comm}\nVmRSS:\t{} kB\nVmSwap:\t{swap_kb} kB\n", rss_pages * 4));
        }
        fn sampler(&self) -> Sampler {
            let now = self.now.clone();
            Sampler::with_clock(self.proc(), self.sys(), Box::new(move || f64::from_bits(now.load(Ordering::SeqCst))))
        }
        fn advance(&self, s: f64) {
            let t = f64::from_bits(self.now.load(Ordering::SeqCst)) + s;
            self.now.store(t.to_bits(), Ordering::SeqCst);
        }
        fn standard(&self) {
            self.process(1, "init", &["/sbin/init"], (10, 5), 1, 10, None, 0);
            self.process(2, "kthreadd", &[], (0, 0), 1, 10, None, 0); // a kernel thread
            self.process(300, "(my) weird proc", &["/usr/bin/weird"], (0, 0), 4, 1000, Some((4096, 8192)), 512);
            self.process(400, "gnome-text-edit", &["/usr/bin/gnome-text-editor", "--gapplication-service"], (200, 100), 7, 500, None, 0);
        }
    }

    #[test]
    fn cpu_percent_from_two_samples() {
        let fs = Fake::new();
        fs.standard();
        let mut s = fs.sampler();
        let first = s.sample(&["io"]);
        assert_eq!(first.cpu.idle, 100.0); // no rates from one sample
        assert_eq!(first.procs[&400].cpu, 0.0);
        // +100 user, +50 system, +250 idle(+iowait) jiffies, 2 s later; pid 300 used 1 s of CPU
        fs.cpu([150, 50, 100, 1000, 100]);
        let half = (clk_tck() / 2.0) as i64;
        fs.process(300, "(my) weird proc", &["/usr/bin/weird"], (half, half), 4, 1000, None, 0);
        fs.advance(2.0);
        let snap = s.sample(&["io"]);
        assert!((snap.cpu.user - 25.0).abs() < 1e-9);
        assert!((snap.cpu.system - 12.5).abs() < 1e-9);
        assert!((snap.cpu.idle - 62.5).abs() < 1e-9);
        assert!((snap.procs[&300].cpu - 50.0).abs() < 1e-9); // 1 s of CPU in 2 s
        assert_eq!(snap.procs[&1].cpu, 0.0);
        assert!((snap.procs[&400].cpu_time - 300.0 / clk_tck()).abs() < 1e-9);
        assert!((snap.interval - 2.0).abs() < 1e-9);
        let a = parse_cpu_line("cpu  10 0 10 80 0 0 0 0 0 0");
        let b = parse_cpu_line("cpu  20 0 20 160 0 0 0 0 0 0");
        assert_eq!(cpu_percent(&a, &b), CpuPct { user: 10.0, system: 10.0, idle: 80.0 });
        assert_eq!(cpu_percent(&b, &b).idle, 100.0);
    }

    #[test]
    fn memory_totals() {
        let fs = Fake::new();
        let m = fs.sampler().sample(&["io"]).memory;
        assert_eq!(m.total, 8000000 * 1024);
        assert_eq!(m.used, 2000000 * 1024); // total - available
        assert_eq!(m.cached, 2100000 * 1024);
        assert_eq!(m.swap, 250000 * 1024);
        assert_eq!(m.app, 1500000 * 1024);
        assert_eq!(m.wired, 60000 * 1024);
        assert!((m.pressure - 0.25).abs() < 1e-9);
    }

    #[test]
    fn process_list() {
        let fs = Fake::new();
        fs.standard();
        let procs = fs.sampler().processes(&["io", "swap"]);
        assert_eq!(procs.keys().copied().collect::<Vec<_>>(), [1, 2, 300, 400]);
        let weird = &procs[&300];
        assert_eq!(weird.name, "(my) weird proc"); // parentheses in the name
        assert_eq!(weird.threads, 4);
        assert_eq!(weird.rss, 1000 * page_size());
        assert_eq!((weird.read_bytes, weird.write_bytes), (4096, 8192));
        assert_eq!(weird.swap, 512 * 1024);
        assert_eq!(weird.cmdline, "/usr/bin/weird");
        assert_eq!(weird.uid, unsafe { libc::getuid() } as i64);
        assert!(!weird.user.is_empty());
        assert_eq!(procs[&2].cmdline, "[kthreadd]"); // kernel threads have no command line
        assert_eq!(procs[&2].exe, "");
        assert_eq!(procs[&2].read_bytes, -1);
        // comm is cut at 15 characters: the executable's name is shown instead
        assert_eq!(procs[&400].name, "gnome-text-editor");
        assert_eq!(procs[&1].started, 1700000000.0 + 100.0 / clk_tck());
        let bare = fs.sampler().processes(&[]);
        assert_eq!((bare[&300].read_bytes, bare[&300].swap), (-1, 0));
    }

    #[test]
    fn disk_and_network_rates() {
        let fs = Fake::new();
        let mut s = fs.sampler();
        s.sample(&["io"]);
        fs.disk(2048, 4096, 10, 20, 500);
        fs.net(10000, 4000, 5000);
        fs.advance(2.0);
        let snap = s.sample(&["io"]);
        // sda + nvme0n1 only (partitions and loop devices would count twice)
        assert_eq!(snap.disk.read_bytes, 2 * 2048 * 512);
        assert_eq!(snap.disk.writes, 40);
        assert_eq!(snap.disk.read_bytes_ps, (2048 * 512) as f64);
        assert_eq!(snap.disk.writes_ps, 20.0);
        assert_eq!(snap.net.rx_bytes, 10000); // the loopback isn't network traffic
        assert_eq!(snap.net.rx_bytes_ps, 5000.0);
        assert_eq!(snap.net.tx_bytes_ps, 2000.0);
        assert_eq!(snap.interfaces["lo"].rx_bytes, 5000);
        assert_eq!(snap.interfaces.keys().cloned().collect::<Vec<_>>(), ["eth0", "lo"]);
        assert_eq!(snap.disks.keys().cloned().collect::<Vec<_>>(), ["nvme0n1", "sda"]);
        let sda = &snap.disks["sda"];
        assert_eq!(sda.read_bytes_ps, (2048 * 512 / 2) as f64);
        assert!((sda.active - 25.0).abs() < 1e-9); // 500 ms busy in 2 s
        assert_eq!(sda.capacity, 1953525168 * 512);
        assert_eq!((sda.kind.as_str(), snap.disks["nvme0n1"].kind.as_str()), ("HDD", "SSD"));
        assert_eq!(snap.disks["nvme0n1"].model, "Fast SSD");
        let d = parse_diskstats("8 0 sda 1 0 10 0 1 0 20 0\n8 1 sda1 1 0 10 0 1 0 20 0\n7 0 loop0 5 0 50 0 5 0 50 0\n", None);
        assert_eq!((d.read_bytes, d.write_bytes), (10 * 512, 20 * 512)); // guessed without /sys/block
    }

    #[test]
    fn process_io_rates_and_state() {
        let fs = Fake::new();
        fs.standard();
        let mut s = fs.sampler();
        s.sample(&["io"]);
        fs.process(300, "(my) weird proc", &["/usr/bin/weird"], (0, 0), 1, 10, Some((4096 + 2000, 8192 + 4000)), 0);
        let text = std::fs::read_to_string(fs.proc().join("300/stat")).unwrap();
        fs.w("300/stat", &text.replace(") S ", ") T "));
        fs.advance(2.0);
        let snap = s.sample(&["io"]);
        let p = &snap.procs[&300];
        assert_eq!((p.read_ps, p.write_ps), (1000.0, 2000.0));
        assert_eq!(p.status(), "Suspended");
        assert_eq!(snap.procs[&1].status(), "");
    }

    #[test]
    fn cpu_info_speed_gpu_battery() {
        let fs = Fake::new();
        let mut s = fs.sampler();
        let info = s.cpu_info();
        assert_eq!((info.model.as_str(), info.logical, info.cores, info.sockets), ("Test CPU 9000", 4, 2, 1));
        assert_eq!(info.base_mhz, 3500.0);
        assert_eq!(s.cpu_speed(), 2500.0); // the cores' average
        assert!(s.gpus().is_empty());
        fs.w_sys("class/drm/card0/device/gpu_busy_percent", "42\n");
        assert_eq!(s.sample(&[]).gpus, BTreeMap::from([("card0".to_string(), Some(42.0))]));
        assert!(s.sample(&[]).uptime > 0.0);
        assert!(s.sample(&[]).battery.is_none());
        for (f, t) in [("BAT0/type", "Battery"), ("BAT0/capacity", "80"), ("BAT0/status", "Discharging"), ("BAT0/energy_now", "40000000"), ("BAT0/energy_full", "50000000"), ("BAT0/power_now", "10000000"), ("AC/type", "Mains"), ("AC/online", "0")] {
            fs.w_sys(&format!("class/power_supply/{f}"), &format!("{t}\n"));
        }
        let b = battery_info(&fs.sys()).unwrap();
        assert_eq!((b.percent, b.seconds, b.on_ac), (Some(80), Some(4 * 3600), false)); // 40 Wh at 10 W
    }

    #[test]
    fn filtering_and_formats() {
        let fs = Fake::new();
        fs.standard();
        let procs: Vec<Proc> = fs.sampler().processes(&[]).into_values().collect();
        assert_eq!(procs.iter().filter(|p| matches(p, "WEIRD", None)).map(|p| p.pid).collect::<Vec<_>>(), [300]);
        assert_eq!(procs.iter().filter(|p| matches(p, "40", None)).map(|p| p.pid).collect::<Vec<_>>(), [400]);
        let me = unsafe { libc::getuid() } as i64;
        assert!(procs.iter().all(|p| matches(p, "", Some(me))));
        assert!(!procs.iter().any(|p| matches(p, "", Some(me + 1))));
        assert_eq!(fmt_bytes(512.0), "512 bytes");
        assert_eq!(fmt_bytes(1536.0), "1.5 KB");
        assert_eq!(fmt_bytes(345.0 * 1024.0 * 1024.0), "345.0 MB");
        assert_eq!(fmt_bytes(1.5 * 1024f64.powi(3)), "1.50 GB");
        assert_eq!(fmt_bytes(-1.0), "–");
        assert_eq!(fmt_cpu_time(12.345), "12.35");
        assert_eq!(fmt_cpu_time(187.21), "3:07.21");
        assert_eq!(fmt_cpu_time(3723.45), "1:02:03.45");
        assert_eq!(fmt_cpu_time(119.996), "2:00.00");
        assert_eq!(fmt_duration(Some((3 * 3600 + 7 * 60) as f64)).as_deref(), Some("3:07"));
        assert_eq!(fmt_count(1234567.0), "1,234,567");
    }

    const FDINFO: &str = "pos:\t0\nflags:\t02100002\ndrm-driver:\tamdgpu\ndrm-pdev:\t0000:05:00.0\ndrm-client-id:\t42\ndrm-engine-gfx:\t{gfx} ns\ndrm-engine-compute:\t0 ns\ndrm-engine-capacity-gfx:\t1\n";

    #[test]
    fn gpu_share_per_process() {
        let (key, eng) = parse_drm_fdinfo(&FDINFO.replace("{gfx}", "123")).unwrap();
        assert_eq!(key, ("0000:05:00.0".to_string(), "42".to_string()));
        assert_eq!(eng, BTreeMap::from([("gfx".to_string(), 123), ("compute".to_string(), 0)]));
        assert!(parse_drm_fdinfo("pos: 0\nflags: 1\n").is_none());
        let fs = Fake::new();
        let d = fs.proc().join("300");
        std::fs::create_dir_all(d.join("fd")).unwrap();
        std::fs::create_dir_all(d.join("fdinfo")).unwrap();
        std::os::unix::fs::symlink("/dev/dri/renderD128", d.join("fd/7")).unwrap();
        std::os::unix::fs::symlink("/dev/dri/renderD128", d.join("fd/8")).unwrap(); // a dup: the same client
        std::os::unix::fs::symlink("/home/x/file", d.join("fd/3")).unwrap();
        let mut s = fs.sampler();
        let mk = || BTreeMap::from([(300, Proc { pid: 300, name: "game".into(), start_ticks: 5, ..Default::default() })]);
        for fd in ["7", "8"] {
            std::fs::write(d.join("fdinfo").join(fd), FDINFO.replace("{gfx}", "1000000000")).unwrap();
        }
        let mut first = mk();
        s.gpu_usage(&mut first, 0.0, 0.0); // first sample: no rate yet
        assert_eq!(first[&300].gpu_on, "AMD"); // which card: from the fdinfo's driver
        for fd in ["7", "8"] {
            std::fs::write(d.join("fdinfo").join(fd), FDINFO.replace("{gfx}", "1400000000")).unwrap();
        }
        let mut procs = mk();
        s.gpu_usage(&mut procs, 1.0, 1.0);
        assert!((procs[&300].gpu - 40.0).abs() < 1e-3); // 0.4 s busy in 1 s, counted once
        let pm = "# gpu         pid   type     sm    mem    enc    dec    jpg    ofa    command\n# Idx           #    C/G      %      %      %      %      %      %    name\n    0       4242     G     57     12      -      -      -      -    game.exe\n    0       1111     G      -      -      -      -      -      -    Xwayland\n";
        assert_eq!(parse_pmon(pm), HashMap::from([(4242, 57.0)]));
    }

    /// A hung nvidia-smi was waited on forever: `busy` stayed set and
    /// Activity Monitor's per-process GPU numbers stopped updating.
    #[test]
    fn test_hung_nvidia_smi_froze_gpu_column() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let tool = dir.path().join("nvidia-smi");
        std::fs::write(&tool, "#!/bin/sh\nexec sleep 30\n").unwrap();
        std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o755)).unwrap();
        let t = std::time::Instant::now();
        assert!(parse_pmon(&pmon_output(&tool, 1)).is_empty());
        assert!(t.elapsed() < std::time::Duration::from_secs(10));
        assert_eq!(PMON_TIMEOUT_S, 5);
    }
}
