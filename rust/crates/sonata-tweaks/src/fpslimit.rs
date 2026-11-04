//! Limit games' frame rate (sonata2/fpslimit.py; Vini: Control Center > FPS
//! Limit, Settings > Displays; off by default) -- through frame-pacer
//! (github.com/endjynn/frame-pacer, MIT): a Vulkan layer for native Vulkan,
//! DXVK and vkd3d-proton games that reads ~/.config/frame-pacer/frame-pacer.conf
//! about once a second, so a new limit reaches a running game within a second.
//!
//! Only the top of frame-pacer's file is Sonata's (global_fps_limit, hud): a
//! game's own [section] written by hand stays. The layer only loads in what
//! Sonata starts with ENABLE_FRAME_PACER=1 -- game launchers: browsers and
//! other apps drawing with Vulkan are never limited.

use serde_json::{json, Map, Value};
use std::path::{Path, PathBuf};

const NAME: &str = "fpslimit";
pub const CHOICES: [&str; 6] = ["off", "30", "60", "90", "120", "max"];
pub const REPO: &str = "endjynn/frame-pacer";
pub const LAUNCHERS: [&str; 12] = [
    "steam", "steam-native", "steam-runtime", "com.valvesoftware.Steam", "faugus-launcher", "io.github.Faugus.faugus-launcher", "net.lutris.Lutris", "lutris", "heroic", "com.heroicgameslauncher.hgl", "com.usebottles.bottles", "bottles",
];

fn defaults() -> Value {
    // refresh: the display's Hz, for "max"
    json!({"limit": "off", "hud": false, "refresh": 0})
}

pub fn conf_path() -> PathBuf {
    sonata_config::config_home().join("frame-pacer").join("frame-pacer.conf")
}

/// frame-pacer's layer in ~/.local.
pub fn installed() -> bool {
    let home = crate::util::home();
    let d = home.join(".local/share/vulkan/implicit_layer.d");
    let any = std::fs::read_dir(d).map(|rd| rd.flatten().any(|e| {
        let n = e.file_name().to_string_lossy().into_owned();
        n.contains("frame_pacer") || n.contains("frame-pacer")
    }));
    any.unwrap_or(false) && home.join(".local/lib/frame-pacer").is_dir()
}

pub fn settings() -> Map<String, Value> {
    crate::util::load(NAME, defaults())
}

/// "off", or a number of frames per second ("max": the display's rate).
pub fn get() -> String {
    let s = settings();
    let v = match s.get("limit") {
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
        None => "off".into(),
    };
    if CHOICES.contains(&v.as_str()) { v } else { "off".into() }
}

/// What frame-pacer gets: "off" or 1..999 ("max": the display's rate, rounded).
pub fn resolve(limit: &str, refresh_hz: Option<f64>) -> String {
    if limit == "max" {
        return match refresh_hz.filter(|h| *h != 0.0) {
            Some(hz) => (hz.round_ties_even() as i64).clamp(1, 999).to_string(),
            None => "off".into(),
        };
    }
    match limit.parse::<i64>() {
        Ok(n) if limit.chars().all(|c| c.is_ascii_digit()) && (1..=999).contains(&n) => limit.to_string(),
        _ => "off".into(),
    }
}

/// frame-pacer.conf with Sonata's two keys at the top; the game sections
/// (from the first "[" line) and any other top lines stay as they were.
pub fn conf_text(old: &str, limit: &str, hud: bool) -> String {
    let lines: Vec<&str> = old.lines().collect();
    let first = lines.iter().position(|l| l.trim_start().starts_with('[')).unwrap_or(lines.len());
    let ours = regex::Regex::new(r"^\s*(global_fps_limit|hud)\s*=").unwrap();
    let keep: Vec<&str> = lines[..first].iter().copied().filter(|l| !l.trim().is_empty() && !l.contains("Sonata") && !ours.is_match(l)).collect();
    let mut out: Vec<String> = vec!["# Sonata: Control Center > FPS Limit (these two lines)".into(), format!("global_fps_limit = {limit}"), format!("hud = {}", if hud { "on" } else { "off" })];
    out.extend(keep.iter().map(|s| s.to_string()));
    let sections = &lines[first..];
    if !sections.is_empty() {
        out.push(String::new());
        out.extend(sections.iter().map(|s| s.to_string()));
    }
    out.join("\n") + "\n"
}

pub fn write_conf(limit: &str, hud: bool) {
    let path = conf_path();
    let old = std::fs::read_to_string(&path).unwrap_or_default();
    let new = conf_text(&old, limit, hud);
    if new != old {
        crate::util::write(&path, &new);
    }
}

/// The limit ("off", "30".."120", "max"), the display's refresh rate for
/// "max" (None: the one kept), the overlay (None: as it was).
pub fn set(limit: &str, refresh_hz: Option<f64>, hud: Option<bool>) {
    let cfg = settings();
    let limit = if CHOICES.contains(&limit) { limit } else { "off" };
    let hud = hud.unwrap_or_else(|| crate::util::truthy(cfg.get("hud")));
    let refresh = refresh_hz.filter(|h| *h != 0.0).or_else(|| cfg.get("refresh").and_then(Value::as_f64)).unwrap_or(0.0);
    let _ = sonata_config::update(NAME, &json!({"limit": limit, "hud": hud, "refresh": refresh}));
    write_conf(&resolve(limit, Some(refresh)), hud);
}

/// ENABLE_FRAME_PACER=1 for game launchers once frame-pacer is here.
pub fn env_for_key(key: &str, installed: bool) -> Vec<(String, String)> {
    if LAUNCHERS.contains(&key) && installed {
        vec![("ENABLE_FRAME_PACER".into(), "1".into())]
    } else {
        Vec::new()
    }
}

pub fn env_for(info: &gio::AppInfo) -> Vec<(String, String)> {
    let key = crate::gpu::app_key(info);
    if LAUNCHERS.contains(&key.as_str()) { env_for_key(&key, installed()) } else { Vec::new() }
}

// -- install (a release: prebuilt, no compiler, no sudo) --------------------------------------
fn is_asset(name: &str) -> bool {
    name.starts_with("frame-pacer-") && name.ends_with("-linux-x86_64-multilib.tar.xz")
}

/// (tarball url, sha256 url) of the newest release with a Linux x86_64 archive.
pub fn latest_asset(releases: &Value) -> Option<(String, String)> {
    for rel in releases.as_array()? {
        let assets: Vec<(String, String)> = rel.get("assets").and_then(Value::as_array).map(|a| a.iter().map(|x| (x["name"].as_str().unwrap_or("").to_string(), x["browser_download_url"].as_str().unwrap_or("").to_string())).collect()).unwrap_or_default();
        for (name, url) in &assets {
            if is_asset(name) {
                if let Some((_, sum)) = assets.iter().find(|(n, _)| *n == format!("{name}.sha256")) {
                    return Some((url.clone(), sum.clone()));
                }
            }
        }
    }
    None
}

fn fetch(url: &str, out: &Path, timeout: u64) -> Result<(), String> {
    let o = out.display().to_string();
    let (rc, err) = sonata_backend::run::run(&["curl", "-fsSL", "-A", "sonata2", "-H", "Accept: application/vnd.github+json", "-o", &o, url], timeout);
    match rc {
        0 => Ok(()),
        127 => Err("curl is not installed".into()),
        _ => Err(err.trim().to_string()),
    }
}

/// Download, verify and install frame-pacer (blocking); "" or why it failed.
pub fn install_blocking() -> String {
    let tmp = match tempfile_dir() {
        Ok(t) => t,
        Err(e) => return e,
    };
    let r = install_in(&tmp);
    let _ = std::fs::remove_dir_all(&tmp);
    if let Err(e) = r {
        return e;
    }
    let cfg = settings();
    write_conf(&resolve(&get(), cfg.get("refresh").and_then(Value::as_f64)), crate::util::truthy(cfg.get("hud")));
    String::new()
}

fn tempfile_dir() -> Result<PathBuf, String> {
    let base = std::env::temp_dir();
    for i in 0..100 {
        let d = base.join(format!("sonata-frame-pacer-{}-{i}", std::process::id()));
        if std::fs::create_dir(&d).is_ok() {
            return Ok(d);
        }
    }
    Err("no temporary folder".into())
}

fn install_in(tmp: &Path) -> Result<(), String> {
    let rels = tmp.join("releases.json");
    fetch(&format!("https://api.github.com/repos/{REPO}/releases"), &rels, 60)?;
    let releases: Value = std::fs::read(&rels).ok().and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
    let (tar_url, sum_url) = latest_asset(&releases).ok_or("no Linux release found")?;
    let (tarball, sumfile) = (tmp.join("fp.tar.xz"), tmp.join("fp.sha256"));
    fetch(&tar_url, &tarball, 300)?;
    fetch(&sum_url, &sumfile, 60)?;
    let want = std::fs::read_to_string(&sumfile).unwrap_or_default().split_whitespace().next().unwrap_or("").to_lowercase();
    let data = std::fs::read(&tarball).map_err(|e| e.to_string())?;
    let got = glib::compute_checksum_for_data(glib::ChecksumType::Sha256, &data).map(|s| s.to_string()).unwrap_or_default();
    if got != want {
        return Err("the download didn't match its checksum".into());
    }
    let out = tmp.join("x");
    std::fs::create_dir_all(&out).map_err(|e| e.to_string())?;
    let (t, o) = (tarball.display().to_string(), out.display().to_string());
    let (rc, err) = sonata_backend::run::run(&["tar", "--no-same-owner", "--no-same-permissions", "-xJf", &t, "-C", &o], 120);
    if rc != 0 {
        return Err(err.trim().to_string());
    }
    let dir = std::fs::read_dir(&out).map_err(|e| e.to_string())?.flatten().map(|e| e.path()).find(|p| p.join("install.sh").is_file()).ok_or("the release has no installer")?;
    let out = std::process::Command::new("sh").arg("./install.sh").current_dir(&dir).stdin(std::process::Stdio::null()).output().map_err(|e| e.to_string())?;
    if !out.status.success() {
        let text = if out.stderr.is_empty() { out.stdout } else { out.stderr };
        let text = String::from_utf8_lossy(&text).trim().to_string();
        let tail: String = text.chars().rev().take(300).collect::<Vec<_>>().into_iter().rev().collect();
        return Err(if tail.is_empty() { "the installer failed".into() } else { tail });
    }
    Ok(())
}

/// The latest release, into ~/.local (a thread; no sudo); done("" or why) on the main loop.
pub fn install<F: FnOnce(String) + 'static>(done: F) {
    let ctx = glib::MainContext::default();
    ctx.spawn_local(async move {
        let r = gio::spawn_blocking(install_blocking).await.unwrap_or_else(|_| "the installer failed".into());
        done(r);
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn set_writes_frame_pacer_conf_keeping_game_sections() {
        let old = "global_fps_limit = 45\nhud = on\n\n[My game]\nexecutable = \"Game.exe\"\nfps_limit = 30\n";
        let text = conf_text(old, &resolve("60", None), false);
        assert!(text.contains("global_fps_limit = 60\nhud = off\n"));
        assert!(!text.contains("= 45"));
        assert!(text.contains("[My game]\nexecutable = \"Game.exe\"\nfps_limit = 30"));
        let text = conf_text(&text, &resolve("max", Some(143.9)), false);
        assert!(text.contains("global_fps_limit = 144"));
        let text = conf_text(&text, &resolve("off", None), true);
        assert!(text.contains("global_fps_limit = off\nhud = on"));
        assert_eq!(text.matches("global_fps_limit").count(), 1);
        assert_eq!(resolve("max", None), "off");
        assert_eq!(resolve("1000", None), "off");
    }

    #[test]
    fn set_and_get_through_the_files() {
        let _l = crate::util::test_lock();
        set("60", None, None);
        assert_eq!(get(), "60");
        assert!(std::fs::read_to_string(conf_path()).unwrap().contains("global_fps_limit = 60\nhud = off"));
        set("nonsense", None, Some(true));
        assert_eq!(get(), "off");
    }

    #[test]
    fn only_game_launchers_get_the_layer() {
        assert_eq!(env_for_key("steam", true), [("ENABLE_FRAME_PACER".to_string(), "1".to_string())]);
        assert!(env_for_key("google-chrome", true).is_empty());
        assert!(env_for_key("steam", false).is_empty());
        assert!(env_for_key("", true).is_empty());
    }

    #[test]
    fn release_asset() {
        let rels = json!([{"assets": [{"name": "src.tar.gz", "browser_download_url": "x"}]},
                          {"assets": [{"name": "frame-pacer-0.1.0-linux-x86_64-multilib.tar.xz", "browser_download_url": "T"},
                                      {"name": "frame-pacer-0.1.0-linux-x86_64-multilib.tar.xz.sha256", "browser_download_url": "S"}]}]);
        assert_eq!(latest_asset(&rels), Some(("T".into(), "S".into())));
        assert_eq!(latest_asset(&json!([])), None);
    }
}
