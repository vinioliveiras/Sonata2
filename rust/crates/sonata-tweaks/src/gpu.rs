//! Apps on the discrete graphics card (sonata2/gpu.py; laptops with two GPUs).
//!
//! Some apps (GitHub Desktop, games, 3D tools) don't start, or run badly,
//! on the integrated GPU. Each app keeps an option (right-click it in the
//! Dock or Launchpad -> "Use High-Performance Graphics"); apps whose entry
//! asks for it (PrefersNonDefaultGPU=true) get it on their own, and Smart
//! Graphics Switching gives games and creative apps the high-performance
//! card, everyday apps the GPU that draws the screens.
//!
//! Which card is the high-performance one: scan_cards() (switcheroo-
//! control's "Discrete" when it says, else each card's driver, address and
//! memory). Its environment: NVIDIA's PRIME render offload variables, or
//! DRI_PRIME with the card's address.

use gio::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::tr;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::OnceLock;

pub const NAME: &str = "gpu";
pub type Env = Vec<(String, String)>;

/// desktop ids (without .desktop) the user put on the discrete GPU, or took off it
pub fn defaults() -> Value {
    json!({"discrete": [], "integrated": [],
           // Smart Graphics Switching (Vini; on by default)
           "smart": true,
           "everyday_integrated": false,      // (before Smart Graphics Switching; no longer read)
           "light_effects": true,
           // Settings > Displays > Graphics: a card's DRI_PRIME tag, "" = Sonata decides
           "games_gpu": "", "apps_gpu": ""})
}

fn cfg() -> Map<String, Value> {
    crate::util::load(NAME, defaults())
}

pub const NVIDIA_ENV: [(&str, &str); 4] = [
    ("__NV_PRIME_RENDER_OFFLOAD", "1"),
    ("__GLX_VENDOR_LIBRARY_NAME", "nvidia"),
    ("__VK_LAYER_NV_optimus", "NVIDIA_only"),
    ("__EGL_VENDOR_LIBRARY_FILENAMES", "/usr/share/glvnd/egl_vendor.d/10_nvidia.json"),
];
pub const NVIDIA_DRIVERS: [&str; 2] = ["nvidia", "nouveau"];
pub const INTEGRATED: [&str; 4] = ["amdgpu", "i915", "xe", "radeon"];
pub const MESA_EGL: &str = "/usr/share/glvnd/egl_vendor.d/50_mesa.json";

fn env(pairs: &[(&str, &str)]) -> Env {
    pairs.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
}

fn read_text(path: &Path) -> String {
    std::fs::read_to_string(path).map(|s| s.trim().to_string()).unwrap_or_default()
}

fn switcheroo() -> Option<HashMap<String, glib::Variant>> {
    use glib::variant::ToVariant;
    let bus = gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE).ok()?;
    let r = bus
        .call_sync(Some("net.hadess.SwitcherooControl"), "/net/hadess/SwitcherooControl", "org.freedesktop.DBus.Properties", "GetAll", Some(&("net.hadess.SwitcherooControl",).to_variant()), None, gio::DBusCallFlags::NONE, 800, gio::Cancellable::NONE)
        .ok()?;
    r.child_value(0).get::<HashMap<String, glib::Variant>>()
}

fn gpus(props: &HashMap<String, glib::Variant>) -> Vec<HashMap<String, glib::Variant>> {
    props.get("GPUs").and_then(|v| unbox(v).get::<Vec<HashMap<String, glib::Variant>>>()).unwrap_or_default()
}

fn unbox(v: &glib::Variant) -> glib::Variant {
    if v.is_type(glib::VariantTy::VARIANT) { v.as_variant().unwrap_or_else(|| v.clone()) } else { v.clone() }
}

fn gpu_env(g: &HashMap<String, glib::Variant>) -> HashMap<String, String> {
    let e: Vec<String> = g.get("Environment").and_then(|v| unbox(v).get()).unwrap_or_default();
    e.chunks(2).filter(|c| c.len() == 2).map(|c| (c[0].clone(), c[1].clone())).collect()
}

/// Driver names of the display GPUs (/sys/class/drm/cardN).
pub fn card_drivers() -> Vec<String> {
    let mut out = Vec::new();
    let Ok(rd) = std::fs::read_dir("/sys/class/drm") else { return out };
    let mut cards: Vec<PathBuf> = rd.flatten().map(|e| e.path()).filter(|p| p.file_name().map(|n| n.to_string_lossy()).is_some_and(|n| n.len() == 5 && n.starts_with("card") && n[4..].chars().all(|c| c.is_ascii_digit()))).collect();
    cards.sort();
    for c in cards {
        if let Ok(drv) = std::fs::canonicalize(c.join("device").join("driver")) {
            if drv.is_dir() {
                out.push(drv.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default());
            }
        }
    }
    out
}

pub fn has_dual_gpu() -> bool {
    static DUAL: OnceLock<bool> = OnceLock::new();
    *DUAL.get_or_init(|| {
        let sw = switcheroo().and_then(|p| p.get("HasDualGpu").and_then(|v| unbox(v).get::<bool>())).unwrap_or(false);
        sw || card_drivers().len() > 1
    })
}

// -- which card is which --------------------------------------------------------------------------
// Not "the NVIDIA one" nor switcheroo's non-default GPU: with a MUX in dGPU
// mode that is the integrated one, and Steam's games went there (Vini). Each
// card is judged by itself, so an APU + Radeon, Intel + Arc or Intel + NVIDIA
// machine gets it right too; Settings > Displays > Graphics can force either.
#[derive(Clone, Debug, PartialEq)]
pub struct Card {
    /// DRI_PRIME's "pci-0000_01_00_0"
    pub tag: String,
    pub addr: String,
    pub driver: String,
    pub discrete: bool,
    pub vram: u64,
}

fn is_nvidia(driver: &str) -> bool {
    NVIDIA_DRIVERS.iter().any(|d| driver.starts_with(d))
}

/// {DRI_PRIME tag: discrete} from switcheroo-control (versions with "Discrete").
fn switcheroo_discrete() -> HashMap<String, bool> {
    let Some(props) = switcheroo() else { return HashMap::new() };
    let mut out = HashMap::new();
    for g in gpus(&props) {
        let tag = gpu_env(&g).get("DRI_PRIME").cloned().unwrap_or_default();
        if let (false, Some(d)) = (tag.is_empty(), g.get("Discrete").and_then(|v| unbox(v).get::<bool>())) {
            out.insert(tag, d);
        }
    }
    out
}

fn read_int(path: &Path) -> u64 {
    let t = read_text(path);
    let t = if t.is_empty() { "0" } else { &t };
    match t.strip_prefix("0x") {
        Some(h) => u64::from_str_radix(h, 16).unwrap_or(0),
        None => t.parse().unwrap_or(0),
    }
}

/// The GPUs, in card order. told: switcheroo's {tag: discrete}, which wins.
/// Otherwise: NVIDIA is discrete; Intel's integrated GPU is always 00:02.0
/// (Arc is elsewhere); an AMD card is discrete beside an Intel iGPU, not
/// beside NVIDIA, and of two AMD cards the APU has the smaller memory.
pub fn scan_cards(sys: &Path, told: Option<&HashMap<String, bool>>) -> Vec<Card> {
    let drm = sys.join("class/drm");
    let mut names: Vec<String> = std::fs::read_dir(&drm).map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect()).unwrap_or_default();
    names.sort();
    let mut raw: Vec<(String, String, u64)> = Vec::new();
    for n in names {
        let Some(num) = n.strip_prefix("card") else { continue };
        if num.is_empty() || !num.chars().all(|c| c.is_ascii_digit()) {
            continue; // a connector (card1-HDMI-A-1)
        }
        let Ok(dev) = std::fs::canonicalize(drm.join(&n).join("device")) else { continue };
        let Ok(drv) = std::fs::canonicalize(dev.join("driver")) else { continue };
        if !drv.is_dir() {
            continue;
        }
        let name = |p: &Path| p.file_name().map(|x| x.to_string_lossy().into_owned()).unwrap_or_default();
        raw.push((name(&dev), name(&drv), read_int(&dev.join("mem_info_vram_total"))));
    }
    let owned;
    let told = match told {
        Some(t) => t,
        None => {
            owned = switcheroo_discrete();
            &owned
        }
    };
    let amd = |d: &str| d == "amdgpu" || d == "radeon";
    let intel = |d: &str| d == "i915" || d == "xe";
    raw.iter()
        .map(|(addr, drv, vram)| {
            let tag = format!("pci-{}", addr.replace([':', '.'], "_"));
            let disc = if let Some(t) = told.get(&tag) {
                *t
            } else if is_nvidia(drv) {
                true
            } else if intel(drv) {
                !addr.ends_with(":00:02.0")
            } else if amd(drv) {
                let others: Vec<u64> = raw.iter().filter(|(a, d, _)| a != addr && amd(d)).map(|(_, _, v)| *v).collect();
                if let Some(m) = others.iter().max() {
                    vram > m
                } else {
                    raw.iter().any(|(a, d, _)| intel(d) && a.ends_with(":00:02.0"))
                }
            } else {
                false
            };
            Card { tag, addr: addr.clone(), driver: drv.clone(), discrete: disc, vram: *vram }
        })
        .collect()
}

pub fn cards() -> &'static [Card] {
    static CARDS: OnceLock<Vec<Card>> = OnceLock::new();
    CARDS.get_or_init(|| scan_cards(Path::new("/sys"), None))
}

fn by_tag<'a>(cs: &'a [Card], tag: &str) -> Option<&'a Card> {
    if tag.is_empty() { None } else { cs.iter().find(|c| c.tag == tag) }
}

/// The most capable discrete card (NVIDIA first, then the most memory).
pub fn best_discrete(cs: &[Card]) -> Option<&Card> {
    cs.iter().filter(|c| c.discrete).max_by_key(|c| (is_nvidia(&c.driver), c.vram))
}

fn str_of(m: &Map<String, Value>, k: &str) -> String {
    m.get(k).and_then(Value::as_str).unwrap_or("").to_string()
}

/// The card games and creative apps get: the user's (Settings) or the best discrete one.
pub fn high_performance_in(cs: &[Card]) -> Option<&Card> {
    by_tag(cs, &str_of(&cfg(), "games_gpu")).or_else(|| best_discrete(cs))
}

pub fn high_performance() -> Option<&'static Card> {
    high_performance_in(cards())
}

/// The card the user picked for everyday apps (Settings), None = Sonata decides.
pub fn everyday_in(cs: &[Card]) -> Option<&Card> {
    by_tag(cs, &str_of(&cfg(), "apps_gpu"))
}

pub fn everyday() -> Option<&'static Card> {
    everyday_in(cards())
}

/// kind: "games" or "apps"; tag "" = automatic.
pub fn set_card(kind: &str, tag: &str) {
    let mut m = Map::new();
    m.insert(format!("{kind}_gpu"), Value::String(tag.into()));
    let _ = sonata_config::update(NAME, &Value::Object(m));
}

/// NVIDIA's offload variables, or DRI_PRIME with the card's own address
/// (two AMD cards told apart); a Mesa card beside NVIDIA also needs Mesa's
/// GL/EGL/Vulkan, or NVIDIA's libraries answer anyway.
pub fn env_for_in(card: &Card, cs: &[Card]) -> Env {
    if is_nvidia(&card.driver) {
        let mut e = env(&NVIDIA_ENV);
        if !Path::new(NVIDIA_ENV[3].1).exists() {
            e.retain(|(k, _)| k != "__EGL_VENDOR_LIBRARY_FILENAMES");
        }
        return e;
    }
    let mut e = if cs.iter().any(|c| is_nvidia(&c.driver)) { integrated_env() } else { Vec::new() };
    e.push(("DRI_PRIME".into(), card.tag.clone()));
    e
}

pub fn env_for(card: &Card) -> Env {
    env_for_in(card, cards())
}

/// The environment that puts an app on the high-performance card.
pub fn discrete_env() -> Env {
    match high_performance() {
        Some(c) => env_for(c),
        None => env(&[("DRI_PRIME", "1")]),
    }
}

/// "NVIDIA GeForce RTX 4060 ..." from lspci (Settings only).
pub fn card_name(card: &Card) -> String {
    let (rc, out) = sonata_backend::run::run(&["lspci", "-mm", "-s", &card.addr], 2);
    if rc == 0 {
        let f: Vec<&str> = out.split('"').skip(1).step_by(2).collect();
        if f.len() >= 3 {
            return sonata_backend::system::gpu_name(f[1], f[2]);
        }
    }
    let brand = match card.driver.as_str() {
        "nvidia" | "nouveau" => "NVIDIA".to_string(),
        "amdgpu" | "radeon" => "AMD".to_string(),
        "i915" | "xe" => "Intel".to_string(),
        d => d.to_string(),
    };
    format!("{brand} graphics")
}

/// The app's desktop id without ".desktop".
pub fn app_key(info: &gio::AppInfo) -> String {
    crate::util::key_of(info)
}

fn listed(cfg: &Map<String, Value>, list: &str, key: &str) -> bool {
    crate::util::strings(cfg.get(list)).iter().any(|k| k == key)
}

fn prefers_non_default(info: &gio::AppInfo) -> bool {
    info.downcast_ref::<gio::DesktopAppInfo>().is_some_and(|d| d.has_key("PrefersNonDefaultGPU") && d.boolean("PrefersNonDefaultGPU"))
}

fn categories(info: &gio::AppInfo) -> String {
    info.downcast_ref::<gio::DesktopAppInfo>().and_then(|d| d.categories()).map(|s| s.to_string()).unwrap_or_default()
}

/// wants_discrete for an app's key, whether its entry asks for the
/// discrete GPU, and its Categories.
pub fn wants_discrete_key(key: &str, prefers: bool, cats: &str) -> bool {
    let cfg = cfg();
    if listed(&cfg, "integrated", key) {
        return false; // unchecked: the user's choice wins
    }
    if listed(&cfg, "discrete", key) || prefers {
        return true;
    }
    smart() && heavy_key(key, cats) && has_dual_gpu() // a game or a creative app
}

pub fn wants_discrete(info: &gio::AppInfo) -> bool {
    wants_discrete_key(&app_key(info), prefers_non_default(info), &categories(info))
}

/// Smart Graphics Switching (Settings > Displays > Graphics), on by default.
pub fn smart() -> bool {
    crate::util::truthy(cfg().get("smart"))
}

pub fn set_smart(on: bool) {
    let _ = sonata_config::update(NAME, &json!({"smart": on}));
}

/// The user picked this app's GPU himself: Smart Graphics Switching leaves it alone.
pub fn chosen_key(key: &str) -> bool {
    let cfg = cfg();
    listed(&cfg, "discrete", key) || listed(&cfg, "integrated", key)
}

pub fn chosen(info: &gio::AppInfo) -> bool {
    chosen_key(&app_key(info))
}

/// On: Sonata picks this app's GPU again (its own choice forgotten). Off:
/// the GPU it gets now becomes its choice.
pub fn set_smart_for(info: &gio::AppInfo, on: bool) {
    if on {
        let cfg = cfg();
        let key = app_key(info);
        let keep = |l: &str| crate::util::strings(cfg.get(l)).into_iter().filter(|k| *k != key).collect::<Vec<_>>();
        let _ = sonata_config::update(NAME, &json!({"discrete": keep("discrete"), "integrated": keep("integrated")}));
    } else {
        set_discrete(info, wants_discrete(info));
    }
}

/// The user's choice for this app, kept either way (Steam asks for the
/// discrete GPU in its entry: unchecking it used to come back checked).
pub fn set_discrete_key(key: &str, on: bool) {
    let cfg = cfg();
    let mut discrete: Vec<String> = crate::util::strings(cfg.get("discrete")).into_iter().filter(|k| k != key).collect();
    let mut integrated: Vec<String> = crate::util::strings(cfg.get("integrated")).into_iter().filter(|k| k != key).collect();
    if on { &mut discrete } else { &mut integrated }.push(key.to_string());
    let _ = sonata_config::update(NAME, &json!({"discrete": discrete, "integrated": integrated}));
}

pub fn set_discrete(info: &gio::AppInfo, on: bool) {
    set_discrete_key(&app_key(info), on)
}

/// While this file exists, Wayfire draws with the GPU the displays are
/// wired to (tools/sonata-session) instead of wlroots' choice.
pub fn display_gpu_flag() -> PathBuf {
    sonata_config::config_home().join("sonata2").join("compositor-display-gpu")
}

pub fn compositor_on_display_gpu() -> bool {
    display_gpu_flag().exists()
}

/// Settings > Displays > Graphics (from the next login). Off by default:
/// on NVIDIA it crashed the session (its GBM refused buffers).
pub fn set_compositor_on_display_gpu(on: bool) {
    let path = display_gpu_flag();
    if on {
        let _ = std::fs::remove_file(crash_marker()); // turned on again: the old crash is history
        if let Some(d) = path.parent() {
            let _ = std::fs::create_dir_all(d);
        }
        let _ = std::fs::write(&path, "Sonata draws with the displays' GPU while this file exists.\n");
    } else if path.exists() {
        let _ = std::fs::remove_file(&path);
    }
}

/// An urgent notification from "Sonata" (false when no server is there yet: tried again later).
pub fn notify(summary: &str, body: &str, icon: &str) -> bool {
    sonata_desktop::notify::send(summary, body, "Sonata", icon, "", true)
}

/// Left by tools/sonata-session when Wayfire couldn't start on the chosen
/// GPU and fell back to wlroots' own choice (the last resort).
pub fn fallback_marker() -> PathBuf {
    crate::util::cache_home().join("sonata2").join("gpu-start-failed")
}

/// Once: Sonata is drawing with another graphics card this session.
/// True when there was nothing to say or it was said.
pub fn fallback_notice() -> bool {
    let path = fallback_marker();
    if !path.exists() {
        return true;
    }
    if !notify(
        &tr!("Sonata started with the other graphics card"),
        &tr!("It couldn't start on the card your displays use, so it is drawing with the other one for this session. Log out and back in to try again; Feedbacker has the logs."),
        "video-display",
    ) {
        return false;
    }
    let _ = std::fs::remove_file(&path);
    true
}

/// Left by tools/sonata-session when Wayfire crashed on the displays' GPU.
pub fn crash_marker() -> PathBuf {
    crate::util::cache_home().join("sonata2").join("display-gpu-crashed")
}

/// When a crash turned "Draw with the Displays' Graphics Card" off (the
/// marker's time, epoch seconds), None when it didn't.
pub fn crashed_at() -> Option<f64> {
    let m = std::fs::metadata(crash_marker()).ok()?.modified().ok()?;
    m.duration_since(std::time::UNIX_EPOCH).ok().map(|d| d.as_secs_f64())
}

/// Once after such a crash: tell the user the option was turned off. The
/// marker stays (Settings shows why the switch is off) and is only marked
/// as told once the notification really went out -- at login the
/// notification server may not be up yet (Vini: the switch "turned itself
/// off" with no word about it).
pub fn crash_notice() -> bool {
    let path = crash_marker();
    match std::fs::read_to_string(&path) {
        Ok(t) if !t.contains("notified") => {}
        _ => return false,
    }
    if !notify(
        &tr!("Graphics set back to the integrated card"),
        &tr!("The last session ended because the discrete card refused memory. Sonata draws with the integrated card again (Settings > Displays > Graphics); games still use the discrete card."),
        "video-display",
    ) {
        return false; // not told yet: tried again later
    }
    let mtime = std::fs::metadata(&path).and_then(|m| m.modified()).ok();
    if std::fs::write(&path, "notified\n").is_ok() {
        if let (Some(t), Ok(f)) = (mtime, std::fs::File::options().write(true).open(&path)) {
            let _ = f.set_times(std::fs::FileTimes::new().set_accessed(t).set_modified(t)); // keep the crash's time
        }
    }
    true
}

// Electron apps whose native Wayland window never shows on hybrid laptops:
// their GPU process can't import the compositor's buffers (GitHub Desktop
// 3.4, Flatpak: "eglCreateImage failed with 0x3009"). Under Xwayland they open fine.
pub const X11_APPS: [&str; 3] = ["io.github.shiftey.Desktop", "github-desktop", "GitHub Desktop"];
// Steam's own window (CEF) never mapped here when CEF drew on the GPU (Vini,
// NVIDIA under Xwayland; also CachyOS-PKGBUILDS#1376). Drawn by the CPU it
// opens every time; games are not affected.
pub const STEAM_APPS: [&str; 4] = ["steam", "steam-native", "steam-runtime", "com.valvesoftware.Steam"];
pub const STEAM_ARGS: [&str; 1] = ["-cef-disable-gpu"];
// The Flatpak (io.github.shiftey.Desktop) sets ELECTRON_OZONE_PLATFORM_HINT=auto
// itself, over ours, and gets no X11 socket while Wayland is there: forced to
// X11 it said "Missing X server" (Vini). Options for `flatpak run` itself.
pub const FLATPAK_X11: [&str; 2] = ["--socket=x11", "--env=ELECTRON_OZONE_PLATFORM_HINT=x11"];

/// Environment an app needs to open here, besides the GPU's.
pub fn extra_env(info: &gio::AppInfo) -> Env {
    let mut e = crate::browsergpu::launch_env(info); // the app's Vulkan choice
    if X11_APPS.contains(&app_key(info).as_str()) {
        e.push(("ELECTRON_OZONE_PLATFORM_HINT".into(), "x11".into()));
        return e;
    }
    e.extend(crate::fpslimit::env_for(info)); // game launchers: frame-pacer's FPS limit
    e
}

/// Arguments an app needs to open here (added to its Exec line).
pub fn extra_args(info: &gio::AppInfo) -> Vec<String> {
    extra_args_for(&app_key(info), || crate::browsergpu::launch_args(info))
}

fn extra_args_for(key: &str, browser: impl FnOnce() -> Vec<String>) -> Vec<String> {
    if STEAM_APPS.contains(&key) && card_drivers().iter().any(|d| d == "nvidia") {
        return STEAM_ARGS.iter().map(|s| s.to_string()).collect();
    }
    if X11_APPS.contains(&key) {
        return vec!["--ozone-platform=x11".into()]; // wins over the hint its launcher sets
    }
    browser() // Vulkan / WebGPU flags (Electron, Flatpak browsers)
}

/// Options for the launcher (`flatpak run ...`), not the app.
pub fn launcher_args_key(key: &str, commandline: &str) -> Vec<String> {
    if X11_APPS.contains(&key) && format!(" {commandline} ").contains(" run ") && commandline.contains("flatpak") {
        return FLATPAK_X11.iter().map(|s| s.to_string()).collect();
    }
    Vec::new()
}

pub fn launcher_args(info: &gio::AppInfo, commandline: &str) -> Vec<String> {
    launcher_args_key(&app_key(info), commandline)
}

fn joined(words: &[String]) -> String {
    crate::util::shell_join(words).replace("'%U'", "%U").replace("'%u'", "%u")
}

/// `args` right after `flatpak run` (its own options); unchanged otherwise.
pub fn with_launcher_args(commandline: &str, args: &[String]) -> String {
    if args.is_empty() {
        return commandline.to_string();
    }
    let Some(words) = crate::util::shell_split(commandline) else { return commandline.to_string() };
    let Some(run) = words.iter().position(|w| w == "run") else { return commandline.to_string() };
    if !words[..run].iter().any(|w| w.ends_with("flatpak")) {
        return commandline.to_string();
    }
    let mut out = words[..run + 1].to_vec();
    out.extend(args.iter().cloned());
    out.extend(words[run + 1..].iter().cloned());
    joined(&out)
}

/// `args` into an Exec line, before its first field code (%U, Flatpak's
/// @@u) so they go to the app, not to the launcher; else at the end.
pub fn with_args(commandline: &str, args: &[String]) -> String {
    let Some(words) = crate::util::shell_split(commandline) else { return commandline.to_string() };
    let at = words.iter().position(|w| w.starts_with('%') || w.starts_with("@@")).unwrap_or(words.len());
    let mut out = words[..at].to_vec();
    out.extend(args.iter().cloned());
    out.extend(words[at..].iter().cloned());
    joined(&out)
}

// -- everyday apps on the integrated GPU ----------------------------------------------------------
// NVIDIA's driver on Linux doesn't lend the computer's memory to the card when
// it's full (Windows does): a game using most of it left browsers starved and
// crawling (Vini). Apps that aren't games or graphics tools are drawn by the
// integrated GPU, whose memory is the computer's.
pub const HEAVY_CATEGORIES: [&str; 9] = ["Game", "Graphics", "3DGraphics", "RasterGraphics", "VectorGraphics", "Photography", "VideoEditing", "Emulator", "Engineering"];
pub const HEAVY_APPS: [&str; 15] = [
    "steam", "steam-native", "steam-runtime", "com.valvesoftware.Steam", "com.heroicgameslauncher.hgl", "heroic", "net.lutris.Lutris", "lutris", "com.usebottles.bottles", "org.prismlauncher.PrismLauncher", "obs", "com.obsproject.Studio", "org.blender.Blender", "blender", "davinci-resolve",
];

/// A game or a graphics/video tool: it keeps the NVIDIA card.
pub fn heavy_key(key: &str, cats: &str) -> bool {
    HEAVY_APPS.contains(&key) || cats.split(';').any(|c| HEAVY_CATEGORIES.contains(&c))
}

pub fn heavy(info: &gio::AppInfo) -> bool {
    heavy_key(&app_key(info), &categories(info))
}

fn icd_files() -> Vec<String> {
    let mut out: Vec<String> = std::fs::read_dir("/usr/share/vulkan/icd.d")
        .map(|rd| rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| (n.starts_with("radeon_icd") || n.starts_with("intel_icd")) && n.ends_with(".json")).map(|n| format!("/usr/share/vulkan/icd.d/{n}")).collect())
        .unwrap_or_default();
    out.sort();
    out
}

/// Mesa (the integrated GPU) for GL/EGL/Vulkan, [] when there is none to use.
pub fn integrated_env() -> Env {
    let cs = card_drivers();
    if !cs.iter().any(|c| c == "nvidia") || !cs.iter().any(|c| INTEGRATED.contains(&c.as_str())) {
        return Vec::new();
    }
    let mut e = env(&[("__GLX_VENDOR_LIBRARY_NAME", "mesa")]);
    if Path::new(MESA_EGL).exists() {
        e.push(("__EGL_VENDOR_LIBRARY_FILENAMES".into(), MESA_EGL.into()));
    }
    let icds = icd_files();
    if !icds.is_empty() {
        let j = icds.join(":");
        e.push(("VK_DRIVER_FILES".into(), j.clone()));
        e.push(("VK_ICD_FILENAMES".into(), j));
    }
    e
}

/// Driver of the GPU Wayfire draws with: the first of WLR_DRM_DEVICES
/// (tools/sonata-session picks it from where the screens are wired), else
/// the boot GPU, as wlroots does; "" when unknown.
pub fn render_gpu() -> String {
    let dev = std::env::var("WLR_DRM_DEVICES").unwrap_or_default();
    let dev = dev.split(':').next().unwrap_or("");
    let card = if !dev.is_empty() {
        let real = std::fs::canonicalize(dev).unwrap_or_else(|_| PathBuf::from(dev));
        Some(Path::new("/sys/class/drm").join(real.file_name().unwrap_or_default()))
    } else {
        let mut cs: Vec<PathBuf> = std::fs::read_dir("/sys/class/drm").map(|rd| rd.flatten().map(|e| e.path()).collect()).unwrap_or_default();
        cs.sort();
        cs.into_iter().filter(|p| p.file_name().map(|n| n.to_string_lossy().into_owned()).is_some_and(|n| n.len() == 5 && n.starts_with("card"))).find(|c| read_text(&c.join("device/boot_vga")) == "1")
    };
    card.and_then(|c| std::fs::canonicalize(c.join("device/driver")).ok()).filter(|d| d.is_dir()).and_then(|d| d.file_name().map(|n| n.to_string_lossy().into_owned())).unwrap_or_default()
}

/// The GPU environment Sonata launches `info` with ([] = the default).
/// With Smart Graphics Switching, everyday apps go to the integrated GPU
/// only while Wayfire draws with it: with the screens on the NVIDIA card,
/// their frames would have to cross cards, and apps drawn through Xwayland
/// (Spotify, CEF) opened empty (Vini).
pub fn launch_env(info: &gio::AppInfo) -> Env {
    if wants_discrete(info) {
        return discrete_env();
    }
    if smart() && !heavy(info) {
        if let Some(picked) = everyday() {
            return env_for(picked); // forced in Settings
        }
        if INTEGRATED.contains(&render_gpu().as_str()) {
            return integrated_env();
        }
    }
    Vec::new()
}

/// One entry of an app's right-click menu.
pub struct MenuItem {
    pub label: String,
    pub checked: bool,
    pub enabled: bool,
    pub toggled: Box<dyn Fn(bool)>,
}

/// The right-click menu section (empty on one-GPU machines): "Smart
/// Graphics Switching" (checked while Sonata picks) and "Use
/// High-Performance Graphics" -- greyed out while Sonata picks, showing
/// its choice. To choose yourself, uncheck Smart first.
pub fn menu_items(info: &gio::AppInfo) -> Vec<MenuItem> {
    if !has_dual_gpu() {
        return Vec::new();
    }
    let auto = smart() && !chosen(info);
    let (a, b) = (info.clone(), info.clone());
    let mut items = vec![MenuItem { label: tr!("Use High-Performance Graphics"), checked: wants_discrete(info), enabled: !auto, toggled: Box::new(move |on| set_discrete(&a, on)) }];
    if smart() {
        items.insert(0, MenuItem { label: tr!("Smart Graphics Switching"), checked: auto, enabled: true, toggled: Box::new(move |on| set_smart_for(&b, on)) });
    }
    items
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A fake /sys: cards = (pci address, driver, VRAM bytes).
    fn sys(cards: &[(&str, &str, u64)]) -> tempfile::TempDir {
        let root = tempfile::tempdir().unwrap();
        let r = root.path();
        let drm = r.join("class/drm");
        std::fs::create_dir_all(&drm).unwrap();
        for (i, (addr, drv, vram)) in cards.iter().enumerate() {
            let dev = r.join("devices").join(addr);
            std::fs::create_dir_all(&dev).unwrap();
            std::fs::create_dir_all(r.join("drivers").join(drv)).unwrap();
            std::os::unix::fs::symlink(r.join("drivers").join(drv), dev.join("driver")).unwrap();
            if *vram > 0 {
                std::fs::write(dev.join("mem_info_vram_total"), vram.to_string()).unwrap();
            }
            std::fs::create_dir_all(drm.join(format!("card{i}"))).unwrap();
            std::os::unix::fs::symlink(&dev, drm.join(format!("card{i}")).join("device")).unwrap();
            std::fs::create_dir_all(drm.join(format!("card{i}-HDMI-A-1"))).unwrap(); // a connector: skipped
        }
        root
    }

    fn best(cards: &[(&str, &str, u64)], told: &[(&str, bool)]) -> Option<Card> {
        let s = sys(cards);
        let told: HashMap<String, bool> = told.iter().map(|(k, v)| (k.to_string(), *v)).collect();
        let cs = scan_cards(s.path(), Some(&told));
        assert_eq!(cs.len(), cards.len());
        best_discrete(&cs).cloned()
    }

    /// MUX in dGPU mode: switcheroo's non-default GPU was the integrated
    /// one and Steam's games went there (Vini). Each machine kind:
    #[test]
    fn test_high_performance_card() {
        let g: u64 = 1 << 30;
        let (apu, nv) = (("0000:36:00.0", "amdgpu", 512 << 20), ("0000:01:00.0", "nvidia", 0));
        assert_eq!(best(&[apu, nv], &[]).unwrap().driver, "nvidia");
        assert_eq!(best(&[apu, ("0000:03:00.0", "amdgpu", 8 * g)], &[]).unwrap().addr, "0000:03:00.0");
        let igpu = ("0000:00:02.0", "i915", 0);
        assert_eq!(best(&[igpu, ("0000:03:00.0", "amdgpu", 8 * g)], &[]).unwrap().driver, "amdgpu");
        assert_eq!(best(&[igpu, ("0000:04:00.0", "xe", 0)], &[]).unwrap().addr, "0000:04:00.0");
        assert_eq!(best(&[igpu, nv], &[]).unwrap().driver, "nvidia");
        assert!(best(&[apu], &[]).is_none());
        // switcheroo's "Discrete" wins over the guess
        let told = [("pci-0000_36_00_0", false), ("pci-0000_03_00_0", true)];
        assert_eq!(best(&[apu, ("0000:03:00.0", "amdgpu", 256 << 20)], &told).unwrap().addr, "0000:03:00.0");
    }

    #[test]
    fn test_env_for_card() {
        let _l = crate::util::test_lock();
        let nv = Card { tag: "pci-0000_01_00_0".into(), addr: "0000:01:00.0".into(), driver: "nvidia".into(), discrete: true, vram: 0 };
        let amd = Card { tag: "pci-0000_36_00_0".into(), addr: "0000:36:00.0".into(), driver: "amdgpu".into(), discrete: false, vram: 0 };
        let cs = vec![nv.clone(), amd.clone()];
        let e = env_for_in(&nv, &cs);
        assert!(e.contains(&("__NV_PRIME_RENDER_OFFLOAD".into(), "1".into())));
        assert!(!e.iter().any(|(k, _)| k == "DRI_PRIME"));
        assert!(env_for_in(&amd, &cs).contains(&("DRI_PRIME".into(), "pci-0000_36_00_0".into())));
        // Settings can force either kind of app onto a card
        set_card("games", &amd.tag);
        assert_eq!(high_performance_in(&cs), Some(&amd));
        set_card("games", "");
        assert_eq!(high_performance_in(&cs), Some(&nv));
        assert!(everyday_in(&cs).is_none());
        set_card("apps", &nv.tag);
        assert_eq!(everyday_in(&cs), Some(&nv));
        set_card("apps", "");
    }

    /// Vini: GitHub Desktop (Flatpak) never opened -- its GPU process failed
    /// on Wayland, and our X11 hint was overridden by the Flatpak's own (no X11
    /// socket either: "Missing X server"). flatpak run gets the socket and the
    /// hint, the app --ozone-platform=x11.
    #[test]
    fn test_github_desktop_flatpak_opens_under_xwayland() {
        let key = "io.github.shiftey.Desktop";
        let line = "/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=start-github-desktop --file-forwarding io.github.shiftey.Desktop @@u %U @@";
        let cmd = with_launcher_args(&with_args(line, &extra_args_for(key, Vec::new)), &launcher_args_key(key, line));
        assert!(cmd.starts_with("/usr/bin/flatpak run --socket=x11 --env=ELECTRON_OZONE_PLATFORM_HINT=x11 "), "{cmd}");
        assert!(cmd.contains("io.github.shiftey.Desktop --ozone-platform=x11 @@u %U @@"), "{cmd}");
        assert!(launcher_args_key("org.gnome.Calculator", line).is_empty());
        let x11: Vec<String> = FLATPAK_X11.iter().map(|s| s.to_string()).collect();
        assert_eq!(with_launcher_args("/usr/bin/foo %U", &x11), "/usr/bin/foo %U"); // no flatpak
    }

    #[test]
    fn test_the_users_choice_is_kept_either_way() {
        let _l = crate::util::test_lock();
        let key = "sonata-test-gputest";
        assert!(!wants_discrete_key(key, false, ""));
        set_discrete_key(key, true);
        assert!(wants_discrete_key(key, false, "") && chosen_key(key));
        // Steam asks for the discrete GPU in its entry: unchecking it used to come back checked
        set_discrete_key(key, false);
        assert!(!wants_discrete_key(key, true, "Game;"));
        assert!(heavy_key("x", "Game;Utility;") && heavy_key("steam", "") && !heavy_key("x", "Utility;"));
    }
}
