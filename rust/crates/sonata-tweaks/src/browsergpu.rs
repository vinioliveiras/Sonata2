//! Vulkan and WebGPU, per app (sonata2/browsergpu.py; Vini: a toggle for
//! each app). Right-click an app in the Dock or Launchpad > Options, or
//! Settings > Apps > the app > Graphics.
//!
//! What the toggles do depends on the app (kind):
//!
//!   chromium  Chrome, Chromium, Brave, Edge, Vivaldi, Thorium. Their Arch
//!             launcher reads a flag file: Sonata's block there holds
//!             --ozone-platform=x11 (Chrome's Vulkan draws nothing in a
//!             Wayland window) and --enable-unsafe-webgpu, and Vulkan joins
//!             --enable-features. The file is the state. Flatpak browsers:
//!             the same flags at launch.
//!   webapp    Sonata's web apps: the same flags in their command, and the
//!             browser's own program past its launcher (Vini: WhatsApp
//!             showed "unsupported command-line flag: --enable-unsafe-webgpu").
//!   electron  Discord, VS Code...: the same flags at launch.
//!   mozilla   Firefox, LibreWolf, Floorp: WebGPU only (dom.webgpu.enabled
//!             in its profiles' user.js).
//!   wine      Steam, Lutris, Heroic, Bottles, Wine: Vulkan (DXVK) on by
//!             default; off, Windows games draw with WineD3D (OpenGL).
//!   other     Vulkan only: GTK 4 and Qt 6 apps draw with it.
//!
//! WebGPU on Linux Chromium needs Vulkan: WebGPU on turns Vulkan on with it.

use serde_json::{json, Map, Value};
use sonata_i18n::tr;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

const NAME: &str = "browsergpu";
pub const MARK: &str = "# Sonata: Vulkan / WebGPU (the app's right-click menu > Options)";
/// ours, and the old global switch's
pub const MARKS: [&str; 2] = [MARK, "# Sonata: WebGPU for browser games (Settings > Displays > Games)"];
pub const X11: &str = "--ozone-platform=x11";
pub const WEBGPU: &str = "--enable-unsafe-webgpu";
pub const FLAGS: [&str; 2] = [X11, WEBGPU];
pub const FEATURE: &str = "Vulkan";
pub const OWN: &str = "--enable-features=Vulkan";

/// the program -> its launcher's flag file
pub const CHROMIUM_BINS: [(&str, &str); 11] = [
    ("google-chrome", "chrome-flags.conf"),
    ("google-chrome-stable", "chrome-flags.conf"),
    ("google-chrome-beta", "chrome-beta-flags.conf"),
    ("google-chrome-unstable", "chrome-dev-flags.conf"),
    ("chromium", "chromium-flags.conf"),
    ("brave", "brave-flags.conf"),
    ("brave-browser", "brave-flags.conf"),
    ("vivaldi", "vivaldi-stable.conf"),
    ("vivaldi-stable", "vivaldi-stable.conf"),
    ("thorium-browser", "thorium-flags.conf"),
    ("microsoft-edge-stable", "microsoft-edge-stable-flags.conf"),
];
pub const CHROMIUM_FLATPAKS: [&str; 7] = ["com.google.Chrome", "com.google.ChromeDev", "org.chromium.Chromium", "com.brave.Browser", "com.microsoft.Edge", "com.vivaldi.Vivaldi", "io.github.ungoogled_software.ungoogled_chromium"];
pub const MOZILLA: [(&str, &[&str]); 7] = [
    ("firefox", &["~/.mozilla/firefox", "~/.config/mozilla/firefox"]),
    ("firefox-esr", &["~/.mozilla/firefox"]),
    ("librewolf", &["~/.librewolf"]),
    ("floorp", &["~/.floorp"]),
    ("org.mozilla.firefox", &["~/.var/app/org.mozilla.firefox/.mozilla/firefox"]),
    ("io.gitlab.librewolf-community", &["~/.var/app/io.gitlab.librewolf-community/.librewolf"]),
    ("one.ablaze.floorp", &["~/.var/app/one.ablaze.floorp/.floorp"]),
];
pub const WINE: [&str; 16] = ["steam", "steam-native", "steam-runtime", "com.valvesoftware.Steam", "lutris", "net.lutris.Lutris", "heroic", "com.heroicgameslauncher.hgl", "bottles", "com.usebottles.bottles", "faugus-launcher", "io.github.Faugus.faugus-launcher", "wine", "wine64", "proton", "umu-run"];
pub const FLATPAK_DIRS: [&str; 2] = ["/var/lib/flatpak/app", "~/.local/share/flatpak/app"];
pub const WINED3D_ENV: [(&str, &str); 2] = [("PROTON_USE_WINED3D", "1"), ("WINEDLLOVERRIDES", "d3d9,d3d10core,d3d11,dxgi=b")];
pub const VULKAN_ENV: [(&str, &str); 2] = [("GSK_RENDERER", "vulkan"), ("QSG_RHI_BACKEND", "vulkan")];
pub const MOZ_KEY: &str = "dom.webgpu.enabled";
pub const MOZ_LINE: &str = "user_pref(\"dom.webgpu.enabled\", true);  // Sonata WebGPU (Allow WebGPU)";
/// launchers that are programs, not scripts: (their browser, their flag file)
pub const KNOWN_LAUNCHERS: [(&str, (&str, &str)); 1] = [("chromium", ("/usr/lib/chromium/chromium", "chromium-flags.conf"))];

pub fn note() -> String {
    tr!("Takes effect the next time it opens.")
}

fn pairs(p: &[(&str, &str)]) -> Vec<(String, String)> {
    p.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect()
}

fn cfg() -> Map<String, Value> {
    crate::util::load(NAME, json!({"vulkan": {}, "webgpu": {}, "appended": {}}))
}

fn obj(m: &Map<String, Value>, k: &str) -> Map<String, Value> {
    m.get(k).and_then(Value::as_object).cloned().unwrap_or_default()
}

// -- the flag files ------------------------------------------------------------------------------
/// Sonata's own lines for these choices (Vulkan goes in --enable-features).
pub fn flags(vulkan: bool, webgpu: bool) -> Vec<String> {
    let mut out = Vec::new();
    if vulkan || webgpu {
        out.push(X11.to_string());
    }
    if webgpu {
        out.push(WEBGPU.to_string());
    }
    out
}

fn feature_names(line: &str) -> Vec<String> {
    line.trim().split_once('=').map(|(_, v)| v).unwrap_or("").split(',').filter(|n| !n.is_empty()).map(String::from).collect()
}

/// (lines, appended): the flag file with Sonata's block set to `flags`
/// ([] = taken out; Vulkan joins --enable-features with any). appended:
/// Vulkan was added to the user's own --enable-features line (no comment
/// can mark it: the launchers pass a line's every word to the browser).
pub fn edit(lines: &[String], flags: &[String], appended: bool) -> (Vec<String>, bool) {
    let mut appended = appended;
    let mut out: Vec<String> = Vec::new();
    let mut i = 0;
    while i < lines.len() {
        // our block out (added again below)
        if MARKS.contains(&lines[i].trim()) {
            i += 1;
            while i < lines.len() && (FLAGS.contains(&lines[i].trim()) || lines[i].trim() == OWN) {
                i += 1;
            }
            continue;
        }
        out.push(lines[i].clone());
        i += 1;
    }
    let user = out.iter().position(|l| l.trim().starts_with("--enable-features="));
    if let (true, Some(u), true) = (appended, user, flags.is_empty()) {
        // Vulkan out of the user's line again
        let names: Vec<String> = feature_names(&out[u]).into_iter().filter(|n| n != FEATURE).collect();
        out[u] = format!("--enable-features={}", names.join(","));
        appended = false;
    }
    if !flags.is_empty() {
        match user {
            None => {
                out.push(MARK.into());
                out.push(OWN.into());
                out.extend(flags.iter().cloned());
            }
            Some(u) => {
                let mut names = feature_names(&out[u]);
                if !names.iter().any(|n| n == FEATURE) {
                    names.push(FEATURE.into());
                    out[u] = format!("--enable-features={}", names.join(","));
                    appended = true;
                }
                out.push(MARK.into());
                out.extend(flags.iter().cloned());
            }
        }
    }
    (out, appended)
}

/// The flags in Sonata's block of a flag file ([] = none).
pub fn block(lines: &[String]) -> Vec<String> {
    let (mut out, mut inside) = (Vec::new(), false);
    for l in lines {
        let s = l.trim();
        if MARKS.contains(&s) {
            inside = true;
        } else if inside && FLAGS.contains(&s) {
            out.push(s.to_string());
        } else if inside && s != OWN {
            inside = false;
        }
    }
    out
}

fn cfg_dir(dir: Option<&Path>) -> PathBuf {
    dir.map(Path::to_path_buf).unwrap_or_else(sonata_config::config_home)
}

pub fn write_flag_file(name: &str, flags: &[String], dir: Option<&Path>) {
    let path = cfg_dir(dir).join(name);
    let lines = crate::util::read_lines(&path);
    let mut done = obj(&cfg(), "appended");
    let was = crate::util::truthy(done.get(name));
    let (new, appended) = edit(&lines, flags, was);
    if new != lines && (path.exists() || !flags.is_empty()) {
        crate::util::write(&path, &(new.join("\n") + "\n"));
    }
    if appended != was {
        done.insert(name.into(), Value::Bool(appended));
        let _ = sonata_config::update(NAME, &json!({"appended": done}));
    }
}

// -- Firefox ------------------------------------------------------------------------------------
pub fn mozilla_profiles(bases: &[&str]) -> Vec<PathBuf> {
    let mut out = Vec::new();
    for base in bases {
        let base = crate::util::expand(base);
        let Ok(rd) = std::fs::read_dir(&base) else { continue };
        let mut names: Vec<_> = rd.flatten().map(|e| e.path()).collect();
        names.sort();
        out.extend(names.into_iter().filter(|p| p.join("prefs.js").is_file()));
    }
    out
}

pub fn mozilla_on(profiles: &[PathBuf]) -> bool {
    profiles.iter().any(|p| crate::util::read_lines(&p.join("user.js")).iter().any(|l| l.contains("Sonata WebGPU")))
}

pub fn apply_mozilla(on: bool, profiles: &[PathBuf]) {
    for prof in profiles {
        let user = prof.join("user.js");
        let lines = crate::util::read_lines(&user);
        let mut new: Vec<String> = lines.iter().filter(|l| !l.contains("Sonata WebGPU")).cloned().collect();
        if on {
            new.push(MOZ_LINE.into());
        }
        if new != lines {
            crate::util::write(&user, &(new.join("\n") + if new.is_empty() { "" } else { "\n" }));
        }
        if on || prof.join("lock").symlink_metadata().is_ok() {
            continue; // running: it writes prefs.js back on quit
        }
        let prefs = prof.join("prefs.js");
        let plines = crate::util::read_lines(&prefs);
        let kept: Vec<String> = plines.iter().filter(|l| !l.contains(&format!("\"{MOZ_KEY}\""))).cloned().collect();
        if kept != plines {
            crate::util::write(&prefs, &(kept.join("\n") + "\n"));
        }
    }
}

// -- which app is which --------------------------------------------------------------------------
/// What browsergpu needs of an app: its key (desktop id without ".desktop") and Exec line.
#[derive(Clone, Debug, PartialEq)]
pub struct App {
    pub key: String,
    pub commandline: String,
}

impl App {
    pub fn new(key: &str, commandline: &str) -> Self {
        App { key: key.strip_suffix(".desktop").unwrap_or(key).to_string(), commandline: commandline.to_string() }
    }

    pub fn of(info: &gio::AppInfo) -> Self {
        use gio::prelude::*;
        App { key: crate::util::key_of(info), commandline: info.commandline().map(|c| c.to_string_lossy().into_owned()).unwrap_or_default() }
    }
}

/// (program name, flatpak) from its Exec line: ("google-chrome-stable",
/// false), ("com.discordapp.Discord", true).
pub fn program(commandline: &str) -> (String, bool) {
    let Some(words) = crate::util::shell_split(commandline) else { return (String::new(), false) };
    let assign = regex::Regex::new(r"^\w+=").unwrap();
    let base = |w: &str| Path::new(w).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let mut words: Vec<String> = words.into_iter().filter(|w| !assign.is_match(w)).collect();
    if words.first().is_some_and(|w| base(w) == "env") {
        words = words[1..].iter().filter(|w| !w.starts_with('-')).cloned().collect();
    }
    let Some(first) = words.first() else { return (String::new(), false) };
    if base(first) == "flatpak" {
        if let Some(run) = words.iter().position(|w| w == "run") {
            let rest: Vec<&String> = words[run + 1..].iter().filter(|w| !w.starts_with('-')).collect();
            return (rest.first().map(|s| s.to_string()).unwrap_or_default(), true);
        }
    }
    (base(first), false)
}

fn which(name: &str) -> PathBuf {
    let p = sonata_backend::run::which(name).unwrap_or_else(|| PathBuf::from(name));
    std::fs::canonicalize(&p).unwrap_or(p)
}

fn asar_under(dir: &Path, depth: usize) -> bool {
    if dir.join("resources").join("app.asar").exists() {
        return true;
    }
    depth > 0 && std::fs::read_dir(dir).map(|rd| rd.flatten().any(|e| e.path().is_dir() && asar_under(&e.path(), depth - 1))).unwrap_or(false)
}

/// An Electron app: resources/app.asar next to its program, or a launcher
/// script that starts electron.
pub fn electron(name: &str, flatpak: bool) -> bool {
    if flatpak {
        return FLATPAK_DIRS.iter().any(|b| asar_under(&crate::util::expand(b).join(name).join("current/active/files"), 3));
    }
    let path = which(name);
    if path.parent().is_some_and(|d| d.join("resources").join("app.asar").exists()) {
        return true;
    }
    let mut head = vec![0u8; 4096];
    let n = std::fs::File::open(&path).and_then(|mut f| std::io::Read::read(&mut f, &mut head)).unwrap_or(0);
    let head = &head[..n];
    head.starts_with(b"#!") && String::from_utf8_lossy(head).to_lowercase().contains("electron")
}

fn kinds() -> &'static Mutex<HashMap<String, &'static str>> {
    static K: std::sync::OnceLock<Mutex<HashMap<String, &'static str>>> = std::sync::OnceLock::new();
    K.get_or_init(Default::default)
}

/// The kind, with `is_electron` deciding Electron (tests pass a fake).
pub fn kind_with(app: &App, is_electron: &dyn Fn(&str, bool) -> bool) -> &'static str {
    let (name, flatpak) = program(&app.commandline);
    if sonata_desktop::badges::is_webapp(&app.key) {
        "webapp"
    } else if CHROMIUM_BINS.iter().any(|(b, _)| *b == name) || CHROMIUM_FLATPAKS.contains(&name.as_str()) {
        "chromium"
    } else if MOZILLA.iter().any(|(m, _)| *m == name) {
        "mozilla"
    } else if WINE.contains(&app.key.as_str()) || WINE.contains(&name.as_str()) {
        "wine"
    } else if !name.is_empty() && is_electron(&name, flatpak) {
        "electron"
    } else {
        "other"
    }
}

pub fn kind(app: &App) -> &'static str {
    if let Some(k) = kinds().lock().unwrap_or_else(|e| e.into_inner()).get(&app.key) {
        return k;
    }
    let k = kind_with(app, &electron);
    kinds().lock().unwrap_or_else(|e| e.into_inner()).insert(app.key.clone(), k);
    k
}

/// The launcher's flag file of a (non-Flatpak) Chromium browser.
pub fn flag_file(app: &App) -> Option<&'static str> {
    let (name, flatpak) = program(&app.commandline);
    if flatpak { None } else { CHROMIUM_BINS.iter().find(|(b, _)| *b == name).map(|(_, f)| *f) }
}

/// What the choices are read with: the folder of the flag files, where
/// Firefox's profiles are, and how an app's kind is told.
pub struct Ctx<'a> {
    pub cfg_dir: Option<&'a Path>,
    pub mozilla: &'a dyn Fn(&str) -> Vec<String>,
    pub kind: &'a dyn Fn(&App) -> &'static str,
}

fn mozilla_bases(name: &str) -> Vec<String> {
    MOZILLA.iter().find(|(m, _)| *m == name).map(|(_, b)| b.iter().map(|s| s.to_string()).collect()).unwrap_or_default()
}

impl Default for Ctx<'_> {
    fn default() -> Self {
        Ctx { cfg_dir: None, mozilla: &mozilla_bases, kind: &kind }
    }
}

impl Ctx<'_> {
    fn profiles(&self, app: &App) -> Vec<PathBuf> {
        let bases = (self.mozilla)(&program(&app.commandline).0);
        mozilla_profiles(&bases.iter().map(String::as_str).collect::<Vec<_>>())
    }

    fn file_lines(&self, app: &App) -> Vec<String> {
        flag_file(app).map(|f| crate::util::read_lines(&cfg_dir(self.cfg_dir).join(f))).unwrap_or_default()
    }

    /// (vulkan, webgpu): what the app can have.
    pub fn supports(&self, app: &App) -> (bool, bool) {
        let k = (self.kind)(app);
        (k != "mozilla", matches!(k, "chromium" | "webapp" | "electron" | "mozilla"))
    }

    pub fn webgpu(&self, app: &App) -> bool {
        let k = (self.kind)(app);
        if k == "chromium" && flag_file(app).is_some() {
            return block(&self.file_lines(app)).iter().any(|f| f == WEBGPU);
        }
        if k == "mozilla" {
            return mozilla_on(&self.profiles(app));
        }
        matches!(k, "webapp" | "electron" | "chromium") && choice("webgpu", &app.key).unwrap_or(false)
    }

    pub fn vulkan(&self, app: &App) -> bool {
        let k = (self.kind)(app);
        if k == "mozilla" {
            return false;
        }
        if k == "chromium" && flag_file(app).is_some() {
            return block(&self.file_lines(app)).iter().any(|f| f == X11);
        }
        let c = choice("vulkan", &app.key).unwrap_or(k == "wine"); // DXVK: Proton's own default
        c || self.webgpu(app)
    }

    /// WebGPU on keeps Vulkan on (Chromium's WebGPU draws with it).
    pub fn vulkan_forced(&self, app: &App) -> bool {
        matches!((self.kind)(app), "chromium" | "webapp" | "electron") && self.webgpu(app)
    }

    fn set(&self, what: &str, app: &App, on: bool) {
        let k = (self.kind)(app);
        if let (true, Some(file)) = (k == "chromium", flag_file(app)) {
            let vk = if what == "vulkan" { on } else { self.vulkan(app) };
            let wg = if what == "webgpu" { on } else { self.webgpu(app) };
            write_flag_file(file, &flags(vk, wg), self.cfg_dir);
            return;
        }
        if k == "mozilla" {
            if what == "webgpu" {
                apply_mozilla(on, &self.profiles(app));
            }
            return;
        }
        let mut choices = obj(&cfg(), what);
        choices.insert(app.key.clone(), Value::Bool(on));
        let mut m = Map::new();
        m.insert(what.into(), Value::Object(choices));
        let _ = sonata_config::update(NAME, &Value::Object(m));
    }

    pub fn set_vulkan(&self, app: &App, on: bool) {
        self.set("vulkan", app, on)
    }

    pub fn set_webgpu(&self, app: &App, on: bool) {
        self.set("webgpu", app, on)
    }

    /// Flags for Electron apps and Flatpak browsers (gpu::extra_args).
    pub fn launch_args(&self, app: &App) -> Vec<String> {
        let k = (self.kind)(app);
        if k == "electron" || (k == "chromium" && flag_file(app).is_none()) {
            let (vk, wg) = (self.vulkan(app), self.webgpu(app));
            let mut out = flags(vk, wg);
            if vk || wg {
                out.push(OWN.into());
            }
            return out;
        }
        Vec::new()
    }

    pub fn launch_env(&self, app: &App) -> Vec<(String, String)> {
        match (self.kind)(app) {
            "wine" if !self.vulkan(app) => pairs(&WINED3D_ENV),
            "other" if self.vulkan(app) => pairs(&VULKAN_ENV),
            _ => Vec::new(),
        }
    }

    /// The start of a web app's command: with Sonata's block in the
    /// browser's flag file, the browser's own program -- past the launcher,
    /// which would add that block -- with the user's own flags from the
    /// file. Unknown launchers: as they are.
    pub fn web_app_browser(&self, browser: &str) -> Vec<String> {
        let base = Path::new(browser).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let (target, name) = match KNOWN_LAUNCHERS.iter().find(|(b, _)| *b == base) {
            Some((_, (t, n))) => (t.to_string(), n.to_string()),
            None => {
                let mut head = vec![0u8; 8192];
                let Ok(n) = std::fs::File::open(browser).and_then(|mut f| std::io::Read::read(&mut f, &mut head)) else { return vec![browser.into()] };
                let head = &head[..n];
                if !head.starts_with(b"#!") {
                    return vec![browser.into()];
                }
                let text = String::from_utf8_lossy(head);
                let m = regex::Regex::new(r"([\w.+-]+\.conf)\b").unwrap();
                let x = regex::Regex::new(r#"(?m)^\s*exec\s+["']?(/[^\s"']+)"#).unwrap();
                match (m.captures(&text), x.captures(&text)) {
                    (Some(m), Some(x)) => (x[1].to_string(), m[1].to_string()),
                    _ => return vec![browser.into()],
                }
            }
        };
        let lines = crate::util::read_lines(&cfg_dir(self.cfg_dir).join(&name));
        let runnable = std::fs::metadata(&target).is_ok_and(|m| m.is_file() && std::os::unix::fs::PermissionsExt::mode(&m.permissions()) & 0o111 != 0);
        if block(&lines).is_empty() || !runnable {
            return vec![browser.into()];
        }
        let done = obj(&cfg(), "appended");
        let (kept, _) = edit(&lines, &[], crate::util::truthy(done.get(&name)));
        let mut out = vec![target];
        for l in kept {
            if !l.trim().is_empty() && !l.trim_start().starts_with('#') {
                out.extend(crate::util::shell_split(&l).unwrap_or_else(|| l.split_whitespace().map(String::from).collect()));
            }
        }
        out
    }

    /// The browser and the web app's own Vulkan/WebGPU flags; without them
    /// a Wayland window without Vulkan even when the browser's file asks
    /// for them (an unknown launcher adds that file).
    pub fn web_app_command(&self, app: &str, browser: &str) -> Vec<String> {
        let info = App::new(&format!("{}{app}", sonata_desktop::badges::WEBAPP_DESKTOP_PREFIX), "");
        let argv = self.web_app_browser(browser);
        let (vk, wg) = (self.vulkan(&info), self.webgpu(&info));
        if vk || wg {
            let mut all = argv;
            all.extend(flags(vk, wg));
            return features(&all, FEATURE);
        }
        let file = CHROMIUM_BINS.iter().find(|(b, _)| Path::new(browser).file_name().is_some_and(|n| n == *b)).map(|(_, f)| *f).unwrap_or("-");
        if argv == [browser] && !block(&crate::util::read_lines(&cfg_dir(self.cfg_dir).join(file))).is_empty() {
            return vec![browser.into(), "--ozone-platform=wayland".into(), "--disable-features=Vulkan".into()];
        }
        argv
    }
}

fn choice(what: &str, key: &str) -> Option<bool> {
    obj(&cfg(), what).get(key).map(|v| crate::util::truthy(Some(v)))
}

/// Chrome keeps only the last --enable-features: `extra` joins theirs.
pub fn features(flags: &[String], extra: &str) -> Vec<String> {
    let mut out = flags.to_vec();
    match out.iter().position(|f| f.starts_with("--enable-features=")) {
        None => out.push(format!("--enable-features={extra}")),
        Some(at) => {
            let mut names = feature_names(&out[at]);
            if !names.iter().any(|n| n == extra) {
                names.push(extra.into());
            }
            out[at] = format!("--enable-features={}", names.join(","));
        }
    }
    out
}

// -- the public API on Gio.AppInfo (gpu, the Dock, Launchpad, Settings) ---------------------------
pub fn supports(info: &gio::AppInfo) -> (bool, bool) {
    Ctx::default().supports(&App::of(info))
}
pub fn vulkan(info: &gio::AppInfo) -> bool {
    Ctx::default().vulkan(&App::of(info))
}
pub fn webgpu(info: &gio::AppInfo) -> bool {
    Ctx::default().webgpu(&App::of(info))
}
pub fn vulkan_forced(info: &gio::AppInfo) -> bool {
    Ctx::default().vulkan_forced(&App::of(info))
}
pub fn set_vulkan(info: &gio::AppInfo, on: bool) {
    Ctx::default().set_vulkan(&App::of(info), on)
}
pub fn set_webgpu(info: &gio::AppInfo, on: bool) {
    Ctx::default().set_webgpu(&App::of(info), on)
}
pub fn launch_args(info: &gio::AppInfo) -> Vec<String> {
    Ctx::default().launch_args(&App::of(info))
}
pub fn launch_env(info: &gio::AppInfo) -> Vec<(String, String)> {
    Ctx::default().launch_env(&App::of(info))
}
pub fn web_app_command(app: &str, browser: &str) -> Vec<String> {
    Ctx::default().web_app_command(app, browser)
}

/// (label, checked, enabled, what toggling does) for the right-click menu.
pub fn menu_items(info: &gio::AppInfo) -> Vec<crate::gpu::MenuItem> {
    let (ctx, app) = (Ctx::default(), App::of(info));
    let (vk_ok, wg_ok) = ctx.supports(&app);
    let mut items = Vec::new();
    if vk_ok {
        let a = info.clone();
        items.push(crate::gpu::MenuItem { label: tr!("Use Vulkan"), checked: ctx.vulkan(&app), enabled: !ctx.vulkan_forced(&app), toggled: Box::new(move |on| set_vulkan(&a, on)) });
    }
    if wg_ok {
        let a = info.clone();
        items.push(crate::gpu::MenuItem { label: tr!("Allow WebGPU"), checked: ctx.webgpu(&app), enabled: true, toggled: Box::new(move |on| set_webgpu(&a, on)) });
    }
    items
}

pub fn vulkan_note(info: &gio::AppInfo) -> String {
    match kind(&App::of(info)) {
        "chromium" => tr!("Draws through XWayland with Vulkan. Restart it after changing."),
        "webapp" | "electron" => tr!("Draws through XWayland with Vulkan."),
        "wine" => tr!("DXVK for Windows games. Off: WineD3D (OpenGL)."),
        _ => tr!("Apps made with GTK 4 or Qt 6 draw with Vulkan; others ignore it."),
    }
}

pub fn webgpu_note(info: &gio::AppInfo) -> String {
    let k = kind(&App::of(info));
    let mut s = tr!("For 3D web games.");
    if k != "mozilla" {
        s += &format!(" {}", tr!("Turns Vulkan on too."));
    }
    if matches!(k, "chromium" | "mozilla") {
        s += &format!(" {}", tr!("Restart it after changing."));
    }
    s
}

#[cfg(test)]
mod tests;
