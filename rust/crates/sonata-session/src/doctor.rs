//! `doctor` (sonata2/doctor.py): is this computer ready to run Sonata as
//! its desktop?
//!
//! Checks libraries, Wayfire and its plugins, the login-screen entry, the
//! helper programs each feature uses, the lock screen's PAM service, the GPU
//! setup and the keyboard layout; prints what's missing with the command that
//! fixes it, and saves the report to doctor.txt in the logs' folder (plus
//! the last session's errors, for bug reports). `doctor windows`: stylecheck.rs.
//!
//! The text is plain English, as the Python doctor's: it goes into bug reports.

use crate::logs;
use crate::stylecheck;
use std::ffi::CStr;
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Status(pub &'static str);

pub const OK: Status = Status("ok");
pub const WARN: Status = Status("warn");
pub const FAIL: Status = Status("FAIL");
pub const COLOR_END: &str = "\x1b[0m";

impl Status {
    pub fn color(self) -> &'static str {
        match self.0 {
            "ok" => "\x1b[32m",
            "warn" => "\x1b[33m",
            _ => "\x1b[31m",
        }
    }
}

/// (status, what, detail, fix)
#[derive(Default)]
pub struct Report {
    pub rows: Vec<(Status, String, String, String)>,
}

impl Report {
    pub fn add(&mut self, status: Status, what: impl Into<String>, detail: impl Into<String>, fix: impl Into<String>) {
        self.rows.push((status, what.into(), detail.into(), fix.into()));
    }

    pub fn text(&self, color: bool) -> String {
        let mut out = Vec::new();
        for (status, what, detail, fix) in &self.rows {
            let tag = if color { format!("{}{:>4}{COLOR_END}", status.color(), status.0) } else { format!("{:>4}", status.0) };
            out.push(format!("[{tag}] {what}{}", if detail.is_empty() { String::new() } else { format!(" -- {detail}") }));
            if !fix.is_empty() {
                out.push(format!("       fix: {fix}"));
            }
        }
        let n_fail = self.rows.iter().filter(|r| r.0 == FAIL).count();
        let n_warn = self.rows.iter().filter(|r| r.0 == WARN).count();
        out.push(String::new());
        out.push(if n_fail == 0 { "Ready to log in to Sonata.".into() } else { format!("{n_fail} problem(s) to fix before logging in.") });
        if n_warn > 0 {
            out.push(format!("{n_warn} optional thing(s) missing: those features stay off."));
        }
        out.join("\n")
    }

    pub fn failed(&self) -> bool {
        self.rows.iter().any(|r| r.0 == FAIL)
    }
}

fn run(cmd: &[&str]) -> String {
    sonata_backend::run::run(cmd, 5).1.trim().to_string()
}

fn which(name: &str) -> Option<PathBuf> {
    sonata_backend::run::which(name)
}

fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).unwrap_or_default()
}

fn read(path: &Path) -> String {
    std::fs::read(path).map(|b| String::from_utf8_lossy(&b).trim().to_string()).unwrap_or_default()
}

fn family() -> &'static str {
    sonata_backend::system::package_family()
}

/// The first line `<key> = <value>` (`^key\s*=\s*(.+)$`), its value.
fn ini_line<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    text.lines().find_map(|l| l.strip_prefix(key).and_then(|r| r.trim_start().strip_prefix('=')).map(str::trim).filter(|v| !v.is_empty()))
}

// optional programs: (command, what it's for, package on Arch / Debian / Fedora)
// (None: not in that family's own repositories -- never in the one-command fix)
pub const TOOLS: [(&str, &str, Option<&str>, &str, &str); 20] = [
    ("nmcli", "Wi-Fi and network", Some("networkmanager"), "network-manager", "NetworkManager"),
    ("wpctl", "sound", Some("wireplumber"), "wireplumber", "wireplumber"),
    ("pactl", "headphones / speakers ports", Some("libpulse"), "pulseaudio-utils", "pulseaudio-utils"),
    ("bluetoothctl", "Bluetooth", Some("bluez-utils"), "bluez", "bluez"),
    ("brightnessctl", "brightness keys (fallback)", Some("brightnessctl"), "brightnessctl", "brightnessctl"),
    ("ddcutil", "external monitors' brightness (DDC/CI)", Some("ddcutil"), "ddcutil", "ddcutil"),
    ("wlr-randr", "Settings > Displays", Some("wlr-randr"), "wlr-randr", "wlr-randr"),
    ("powerprofilesctl", "energy modes / Low Power Mode", Some("power-profiles-daemon"), "power-profiles-daemon", "power-profiles-daemon"),
    ("grim", "screenshots, Dock window previews", Some("grim"), "grim", "grim"),
    ("slurp", "screenshot of an area", Some("slurp"), "slurp", "slurp"),
    ("wf-recorder", "screen recording", Some("wf-recorder"), "wf-recorder", "wf-recorder"),
    ("wl-screenrec", "screen recording without the drawing palette (AUR: wl-screenrec)", None, "wl-screenrec", "wl-screenrec"),
    ("wl-copy", "clipboard from the shell", Some("wl-clipboard"), "wl-clipboard", "wl-clipboard"),
    ("wlsunset", "Night Shift", Some("wlsunset"), "wlsunset", "wlsunset"),
    ("swayidle", "lock after the display sleeps", Some("swayidle"), "swayidle", "swayidle"),
    ("wtype", "typing emoji from the Character Viewer", Some("wtype"), "wtype", "wtype"),
    ("ffmpegthumbnailer", "video thumbnails in Files", Some("ffmpegthumbnailer"), "ffmpegthumbnailer", "ffmpegthumbnailer"),
    ("ffmpeg", "Trim and Crop in Videos", Some("ffmpeg"), "ffmpeg", "ffmpeg-free"),
    ("bwrap", "Open in Sandbox", Some("bubblewrap"), "bubblewrap", "bubblewrap"),
    ("Xwayland", "X11 apps (Steam, older apps)", Some("xorg-xwayland"), "xwayland", "xorg-x11-server-Xwayland"),
];
pub const POLKIT_AGENTS: [&str; 11] = [
    "/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1",
    "/usr/libexec/polkit-gnome-authentication-agent-1",
    "/usr/lib/x86_64-linux-gnu/polkit-gnome-authentication-agent-1",
    "/usr/lib/polkit-kde-authentication-agent-1",
    "/usr/libexec/polkit-kde-authentication-agent-1",
    "/usr/lib/mate-polkit/polkit-mate-authentication-agent-1",
    "/usr/libexec/polkit-mate-authentication-agent-1",
    "/usr/bin/lxqt-policykit-agent",
    "/usr/lib/xfce-polkit/xfce-polkit",
    "/usr/libexec/xfce-polkit",
    "/usr/lib/hyprpolkitagent/hyprpolkitagent",
];

// -- libraries -----------------------------------------------------------------------------------
/// A shared library by its soname (already loaded or not), dlopen'ed and
/// kept: the doctor exits soon after.
fn open_lib(names: &[&CStr]) -> Option<*mut libc::c_void> {
    names.iter().map(|n| unsafe { libc::dlopen(n.as_ptr(), libc::RTLD_LAZY | libc::RTLD_LOCAL) }).find(|h| !h.is_null())
}

fn sym(lib: *mut libc::c_void, name: &CStr) -> Option<*mut libc::c_void> {
    let p = unsafe { libc::dlsym(lib, name.as_ptr()) };
    (!p.is_null()).then_some(p)
}

/// The library's version through its own get_{major,minor,micro}_version().
fn lib_version(lib: *mut libc::c_void, fns: [&CStr; 3]) -> Option<(u32, u32, u32)> {
    let get = |n: &CStr| -> Option<u32> {
        let f: extern "C" fn() -> libc::c_uint = unsafe { std::mem::transmute(sym(lib, n)?) };
        Some(f())
    };
    Some((get(fns[0])?, get(fns[1])?, get(fns[2])?))
}

/// What the Rust build needs where the Python doctor checked Python, its
/// PyGObject typelibs, pywayland and pycairo: the shared libraries
/// themselves, at the versions sonata2-rs is built for (Cargo.toml: GTK
/// 4.12, libadwaita 1.5) -- looked up the way the dynamic linker does, so a
/// library this binary links is reported whether it loaded or not.
pub fn check_libraries(r: &mut Report) {
    let libs: [(&str, &[&CStr], [&CStr; 3], (u32, u32), &str); 3] = [
        ("Gtk", &[c"libgtk-4.so.1"], [c"gtk_get_major_version", c"gtk_get_minor_version", c"gtk_get_micro_version"], (4, 12), "gtk4"),
        ("Adw", &[c"libadwaita-1.so.0"], [c"adw_get_major_version", c"adw_get_minor_version", c"adw_get_micro_version"], (1, 5), "libadwaita"),
        ("Gtk4LayerShell", &[c"libgtk4-layer-shell.so.0"], [c"gtk_layer_get_major_version", c"gtk_layer_get_minor_version", c"gtk_layer_get_micro_version"], (1, 0), "gtk4-layer-shell"),
    ];
    let mut layer = None;
    for (ns, names, fns, min, pkg) in libs {
        let Some(lib) = open_lib(names) else {
            r.add(FAIL, format!("{ns} missing"), "", format!("install {pkg}"));
            continue;
        };
        if ns == "Gtk4LayerShell" {
            layer = Some(lib);
        }
        match lib_version(lib, fns) {
            Some((a, b, c)) if (a, b) < min => r.add(FAIL, format!("{ns} {a}.{b}.{c}"), format!("Sonata needs {}.{}+", min.0, min.1), format!("install {pkg}")),
            Some((a, b, c)) => r.add(OK, format!("{ns} {a}.{b}.{c}"), "", ""),
            None => r.add(OK, ns, "", ""),
        }
    }
    // the lock screen's ext-session-lock: in gtk4-layer-shell itself since 1.1
    if let Some(lib) = layer {
        match sym(lib, c"gtk_session_lock_is_supported") {
            Some(_) => r.add(OK, "Gtk4SessionLock", "", ""),
            None => r.add(WARN, "Gtk4SessionLock missing", "", "install gtk4-layer-shell (1.1+, lock screen)"),
        }
    }
}

// -- Wayfire -------------------------------------------------------------------------------------
fn own_wayfire() -> PathBuf {
    home().join(".local/opt/sonata-wayfire")
}

/// Wayfire 0.12 built by install.sh where the distro's is older
/// (tools/build-wayfire-stack.sh): the session runs that one.
pub fn standalone(own: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    own.join("standalone").is_file() && std::fs::metadata(own.join("bin/wayfire")).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
}

/// `<dir>/<pattern's first part>*/<rest>` for the two globs the doctor uses.
fn glob_lib_wayfire(own: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let Ok(rd) = std::fs::read_dir(own) else { return out };
    let mut libs: Vec<PathBuf> = rd.flatten().filter(|e| e.file_name().to_string_lossy().starts_with("lib")).map(|e| e.path()).collect();
    libs.sort();
    for l in &libs {
        // lib*/wayfire
        if l.join("wayfire").is_dir() {
            out.push(l.join("wayfire"));
        }
    }
    for l in libs.iter().filter(|l| l.file_name().is_some_and(|n| n == "lib")) {
        // lib/*/wayfire
        let mut subs: Vec<PathBuf> = std::fs::read_dir(l).map(|rd| rd.flatten().map(|e| e.path().join("wayfire")).filter(|p| p.is_dir()).collect()).unwrap_or_default();
        subs.sort();
        out.extend(subs);
    }
    out
}

pub fn plugin_dirs(home: &Path, own: &Path, pkg_plugindir: &str) -> Vec<PathBuf> {
    let user = home.join(".local/share/wayfire/plugin-manager/install/lib/wayfire");
    if standalone(own) {
        // (the system's plugins are for its older Wayfire)
        let mut dirs = vec![user];
        dirs.extend(glob_lib_wayfire(own).into_iter().filter(|p| p.parent().and_then(|p| p.parent()) == Some(own)));
        return dirs.into_iter().filter(|d| d.is_dir()).collect();
    }
    let mut dirs: Vec<PathBuf> = [pkg_plugindir, "/usr/lib/wayfire", "/usr/lib64/wayfire", "/usr/local/lib/wayfire", "/usr/lib/x86_64-linux-gnu/wayfire"].iter().filter(|d| !d.is_empty()).map(PathBuf::from).collect();
    // what install.sh builds for you: pixdecor and Sonata's plugins, Wayfire with Sonata's fix
    // (a fresh install warned "pixdecor missing" right after building it)
    dirs.push(user);
    dirs.extend(glob_lib_wayfire(&home.join(".local/opt/sonata-wayfire")));
    dirs.into_iter().filter(|d| d.is_dir()).collect()
}

fn major_minor(version: &str) -> Option<(u32, u32)> {
    let (a, rest) = version.split_once('.')?;
    let b: String = rest.chars().take_while(char::is_ascii_digit).collect();
    Some((a.parse().ok()?, b.parse().ok()?))
}

pub fn check_wayfire(r: &mut Report, sonata_home: Option<&Path>) {
    if which("wayfire").is_none() {
        r.add(FAIL, "Wayfire missing", "", "install wayfire (0.9+)");
        return;
    }
    let own = own_wayfire();
    if standalone(&own) {
        let exe = own.join("bin/wayfire").display().to_string();
        let v = run(&[&exe, "--version"]);
        r.add(OK, format!("Wayfire {} (built for Sonata; the system's is older)", v.lines().next().unwrap_or("?")), "", "");
    } else {
        let out = run(&["wayfire", "--version"]);
        let version = out.lines().next().unwrap_or("?");
        r.add(OK, format!("Wayfire {version}"), "", "");
        if major_minor(version).is_some_and(|v| v < (0, 11)) {
            r.add(WARN, "Wayfire older than 0.11: no Sonata title bars, round corners or window effects", "", "./install.sh   (builds Wayfire 0.12 in its own folder)");
        }
    }
    let dirs = plugin_dirs(&home(), &own, &run(&["pkg-config", "--variable=plugindir", "wayfire"]));
    let have: std::collections::BTreeSet<String> = dirs
        .iter()
        .flat_map(|d| std::fs::read_dir(d).into_iter().flatten().flatten())
        .filter_map(|e| e.file_name().to_str().and_then(|n| n.strip_prefix("lib")).and_then(|n| n.strip_suffix(".so")).map(String::from))
        .collect();
    let cfg = sonata_home.map(|h| read(&h.join("config/wayfire.ini"))).unwrap_or_default();
    let wanted: Vec<&str> = ini_line(&cfg, "plugins").map(|v| v.split_whitespace().collect()).unwrap_or_default();
    let missing: Vec<&str> = wanted.into_iter().filter(|p| !have.contains(*p) && !(*p == "decoration" && have.contains("pixdecor"))).collect();
    if !have.is_empty() && !missing.is_empty() {
        // left out of the session's config (tools/wayfire-config.sh): those features are off
        r.add(WARN, format!("this Wayfire lacks: {} (left out)", missing.join(" ")), "", "a newer Wayfire (or wayfire-plugins-extra) has them");
    } else if !have.is_empty() {
        r.add(OK, "Wayfire plugins", "", "");
    }
    let built = home().join(".local/share/wayfire/plugin-manager/install/lib/wayfire");
    let corners = built.join("libsonata-corners.so").exists();
    r.add(if corners { OK } else { WARN }, "sonata-corners (rounded corners for Chrome, Spotify...)", "", if corners { "" } else { "./install.sh   (needs meson, ninja and Wayfire's headers)" });
    let pix = have.contains("pixdecor");
    r.add(if pix { OK } else { WARN }, "pixdecor (Sonata title bars for terminals / X11 apps)", "", if pix { "" } else { "tools/build-pixdecor.sh" });
    let fixed = built.join("libpixdecor.so").exists();
    r.add(if fixed { OK } else { WARN }, "pixdecor with Sonata's long-title fix", "", if fixed { "" } else { "tools/build-pixdecor.sh   (then log out and back in)" });
}

// -- the install ---------------------------------------------------------------------------------
pub fn check_install(r: &mut Report) {
    let bin = which("sonata-session").unwrap_or_else(|| home().join(".local/bin/sonata-session"));
    let have = bin.exists();
    r.add(if have { OK } else { FAIL }, "sonata-session launcher", "", if have { "" } else { "./install.sh --dev   (from the repo)" });
    let entry = Path::new("/usr/share/wayland-sessions/sonata.desktop");
    let try_exec = ini_line(&read(entry), "TryExec").unwrap_or("").to_string();
    if !entry.exists() {
        r.add(FAIL, "\"Sonata\" on the login screen", "", "./install.sh --dev");
    } else if try_exec.starts_with("/home/") || (!try_exec.is_empty() && !Path::new(&try_exec).exists()) {
        // the login screen checks TryExec as its own user: a private home is invisible to it
        r.add(FAIL, "\"Sonata\" is hidden on the login screen", format!("it can't reach {try_exec}"), "./install.sh --dev   (installs /usr/local/bin/sonata-login)");
    } else {
        r.add(OK, "\"Sonata\" on the login screen", "", "");
    }
    for conf in ["/etc/gdm/custom.conf", "/etc/gdm3/custom.conf", "/etc/gdm3/daemon.conf"] {
        let text = read(Path::new(conf));
        let off = text.lines().any(|l| l.trim_start().strip_prefix("WaylandEnable").and_then(|r| r.trim_start().strip_prefix('=')).is_some_and(|v| v.trim_start().starts_with("false")));
        if off {
            r.add(FAIL, "GDM runs without Wayland: it hides Wayland sessions like Sonata", conf, format!("remove WaylandEnable=false from {conf}, then restart"));
        }
    }
    let share = home().join(".local/share/sonata2");
    if share.is_symlink() {
        r.add(OK, "dev install", std::fs::canonicalize(&share).unwrap_or(share).display().to_string(), "");
    } else if share.is_dir() {
        r.add(OK, "installed", share.display().to_string(), ""); // (a dev install links the repo: ./install.sh --dev)
    }
    let cfg = home().join(".config/sonata2/wayfire.ini.new");
    if cfg.exists() {
        r.add(WARN, "session config edited by you; a newer default is next to it", cfg.display().to_string(), "");
    }
    let dm = ["sddm", "gdm", "lightdm", "greetd", "ly", "plasmalogin"].into_iter().find(|d| run(&["systemctl", "is-active", d]) == "active").unwrap_or("");
    r.add(
        if dm.is_empty() { WARN } else { OK },
        format!("login screen: {}", if dm.is_empty() { "unknown" } else { dm }),
        if ["sddm", "gdm", "lightdm", "plasmalogin", ""].contains(&dm) { "" } else { "make sure it lists wayland-sessions" },
        "",
    );
}

// -- keyring.py's checks -------------------------------------------------------------------------
/// The lines pam_gnome_keyring needs: unlock with the login password,
/// start the daemon, and follow password changes.
pub const PAM_LINES: [(&str, &str); 3] = [
    ("auth", "auth       optional     pam_gnome_keyring.so"),
    ("session", "session    optional     pam_gnome_keyring.so auto_start"),
    ("password", "password   optional     pam_gnome_keyring.so"),
];

/// greetd's PAM file with pam_gnome_keyring after each kind's last line
/// (unchanged when already there).
pub fn pam_text(text: &str) -> String {
    let mut lines: Vec<String> = text.lines().map(String::from).collect();
    let kind_of = |l: &str| l.split_whitespace().next().map(String::from);
    for (kind, line) in PAM_LINES {
        if lines.iter().any(|l| kind_of(l).as_deref() == Some(kind) && l.contains("pam_gnome_keyring")) {
            continue;
        }
        let last = lines.iter().rposition(|l| kind_of(l).as_deref() == Some(kind)).map(|i| i as isize).unwrap_or(lines.len() as isize - 1);
        lines.insert((last + 1) as usize, line.to_string());
    }
    lines.join("\n") + "\n"
}

pub fn pam_ready(path: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(path) else { return false };
    let norm = if text.ends_with('\n') { text.clone() } else { format!("{text}\n") };
    pam_text(&text) == norm
}

/// One value of a KeePassXC-style INI ([section] key=value, keys as written).
fn ini_get(text: &str, section: &str, key: &str) -> Option<String> {
    let mut cur = "";
    for l in text.lines() {
        let s = l.trim();
        if s.starts_with('[') && s.ends_with(']') {
            cur = &s[1..s.len() - 1];
        } else if cur == section {
            if let Some((k, v)) = s.split_once('=') {
                if k.trim() == key {
                    return Some(v.trim().to_string());
                }
            }
        }
    }
    None
}

/// KeePassXC was the Secret Service with a database in use (Sonata's
/// keyring before): its secrets live there until moved.
pub fn keepassxc_in_use(ini: &str) -> bool {
    let on = ini_get(ini, "FdoSecrets", "Enabled").unwrap_or_default().to_lowercase() == "true";
    let dbs = ini_get(ini, "General", "LastOpenedDatabases").filter(|v| !v.is_empty()).or_else(|| ini_get(ini, "General", "LastActiveDatabase")).unwrap_or_default();
    on && !dbs.trim().is_empty()
}

/// "gnome" or "keepassxc" (your choice, else what is already in use).
pub fn keyring_backend() -> String {
    let chosen = sonata_config::load("keyring", &serde_json::json!({"backend": ""})).get("backend").and_then(|v| v.as_str()).unwrap_or("").to_string();
    if chosen == "gnome" || chosen == "keepassxc" {
        return chosen;
    }
    let kp = which("keepassxc").is_some();
    if kp && keepassxc_in_use(&read(&home().join(".config/keepassxc/keepassxc.ini"))) {
        return "keepassxc".into();
    }
    if which("gnome-keyring-daemon").is_some() || !kp { "gnome".into() } else { "keepassxc".into() }
}

// -- helper programs -----------------------------------------------------------------------------
pub fn check_tools(r: &mut Report) {
    check_tools_with(r, family(), &|c| which(c).is_some(), &|p| Path::new(p).exists());
}

pub fn check_tools_with(r: &mut Report, fam: &str, have: &dyn Fn(&str) -> bool, exists: &dyn Fn(&str) -> bool) {
    let mut missing: Vec<String> = Vec::new();
    for (cmd, what, arch, debian, rpm) in TOOLS {
        if have(cmd) {
            r.add(OK, format!("{cmd} ({what})"), "", "");
        } else {
            r.add(WARN, format!("{cmd} missing"), what, "");
            let pkg = match fam {
                "debian" => Some(debian),
                "rpm" => Some(rpm),
                _ => arch,
            };
            if let Some(p) = pkg {
                missing.push(p.to_string());
            }
        }
    }
    // Sonata's own password prompt (sonata2-rs polkit) opens polkit's agent
    // library at run time (the Python one asked for its typelib)
    let own = [c"libpolkit-agent-1.so.0", c"libpolkit-agent-1.so"].iter().any(|n| open_lib(&[n]).is_some());
    let agent = if own { Some("Sonata's own".to_string()) } else { POLKIT_AGENTS.iter().find(|a| exists(a)).map(|a| a.to_string()) };
    let fix = match fam {
        "arch" => "sudo pacman -S polkit",
        "debian" => "sudo apt install libpolkit-agent-1-0",
        "rpm" => "sudo dnf install polkit-libs",
        _ => "install polkit's agent library (libpolkit-agent-1)",
    };
    r.add(if agent.is_some() { OK } else { FAIL }, "polkit agent (password prompts: users, printers, updates)", agent.clone().unwrap_or_default(), if agent.is_some() { "" } else { fix });
    let portals: Vec<&str> = ["xdg-desktop-portal", "xdg-desktop-portal-wlr"].into_iter().filter(|p| ["/usr/lib", "/usr/libexec", "/usr/lib/x86_64-linux-gnu"].iter().any(|d| exists(&format!("{d}/{p}")))).collect();
    let lack: Vec<&str> = ["xdg-desktop-portal", "xdg-desktop-portal-wlr"].into_iter().filter(|p| !portals.contains(p)).collect();
    r.add(if lack.is_empty() { OK } else { WARN }, "portals (file dialogs, screen sharing)", if lack.is_empty() { String::new() } else { format!("{} missing", lack.join(" ")) }, "");
    missing.extend(lack.iter().map(|s| s.to_string()));
    if keyring_backend() == "keepassxc" {
        r.add(WARN, "saved passwords: KeePassXC (asks for its own password at every login)", "", "Settings > Security & Privacy > Use Login Password…");
    } else {
        let gk = have("gnome-keyring-daemon");
        let fix = match fam {
            "arch" => "sudo pacman -S gnome-keyring libsecret",
            "debian" => "sudo apt install gnome-keyring",
            "rpm" => "sudo dnf install gnome-keyring",
            _ => "install gnome-keyring",
        };
        r.add(if gk { OK } else { WARN }, "saved passwords: login keyring (Chrome, VS Code, Wi-Fi)", "", if gk { "" } else { fix });
        if gk && exists("/etc/pam.d/greetd") {
            let ok = pam_ready(Path::new("/etc/pam.d/greetd"));
            r.add(if ok { OK } else { WARN }, "the login unlocks the keyring (no password prompt)", "", if ok { "" } else { "./install.sh --greeter (or: sudo sonata2 keyring pam)" });
        }
    }
    if !missing.is_empty() {
        let pm = match fam {
            "arch" => "sudo pacman -S --needed",
            "debian" => "sudo apt install",
            "rpm" => "sudo dnf install",
            _ => "install",
        };
        let mut seen = Vec::new();
        for m in missing {
            if !seen.contains(&m) {
                seen.push(m);
            }
        }
        r.add(WARN, "all optional packages in one go", "", format!("{pm} {}", seen.join(" ")));
    }
}

// -- the lock screen -----------------------------------------------------------------------------
/// pam.py's services, first match wins (sonata-shell's pam.rs looks the same way).
pub const PAM_SERVICES: [&str; 5] = ["sonata-lock", "system-local-login", "login", "system-auth", "other"];

pub fn pam_service(exists: &dyn Fn(&Path) -> bool) -> &'static str {
    PAM_SERVICES.into_iter().find(|s| ["/etc/pam.d", "/usr/lib/pam.d"].iter().any(|d| exists(&Path::new(d).join(s)))).unwrap_or("login")
}

pub fn check_lock_with(r: &mut Report, exists: &dyn Fn(&Path) -> bool) {
    let svc = pam_service(exists);
    // the same places the lock screen looks (distros may ship PAM files in /usr/lib/pam.d)
    let found = ["/etc/pam.d", "/usr/lib/pam.d"].iter().any(|d| exists(&Path::new(d).join(svc)));
    r.add(if found { OK } else { FAIL }, format!("lock screen PAM service: {svc}"), "", "");
}

pub fn check_lock(r: &mut Report) {
    check_lock_with(r, &|p| p.exists());
}

// -- the GPU -------------------------------------------------------------------------------------
const INTEGRATED: [&str; 4] = ["amdgpu", "i915", "xe", "radeon"];

/// gpu.py: while this file exists, Wayfire draws with the GPU the displays
/// are wired to (tools/sonata-session) instead of wlroots' choice.
fn compositor_on_display_gpu() -> bool {
    sonata_config::config_home().join("sonata2").join("compositor-display-gpu").exists()
}

/// gpu.render_gpu: the driver of the GPU Wayfire draws with -- the first
/// of WLR_DRM_DEVICES, else the boot GPU, as wlroots does; "" when unknown.
fn render_gpu() -> String {
    let dev = std::env::var("WLR_DRM_DEVICES").unwrap_or_default();
    let dev = dev.split(':').next().unwrap_or("");
    let card = if !dev.is_empty() {
        let real = std::fs::canonicalize(dev).unwrap_or_else(|_| PathBuf::from(dev));
        Some(Path::new("/sys/class/drm").join(real.file_name().unwrap_or_default()))
    } else {
        let mut cards: Vec<PathBuf> = std::fs::read_dir("/sys/class/drm")
            .map(|rd| rd.flatten().filter(|e| e.file_name().to_str().is_some_and(|n| n.len() == 5 && n.starts_with("card") && n.as_bytes()[4].is_ascii_digit())).map(|e| e.path()).collect())
            .unwrap_or_default();
        cards.sort();
        cards.into_iter().find(|c| read(&c.join("device/boot_vga")) == "1")
    };
    let Some(card) = card else { return String::new() };
    match std::fs::canonicalize(card.join("device/driver")) {
        Ok(drv) if drv.is_dir() => drv.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default(),
        _ => String::new(),
    }
}

pub fn check_gpu(r: &mut Report) {
    let mut drm: Vec<String> = std::fs::read_dir("/sys/class/drm")
        .map(|rd| rd.flatten().filter_map(|e| e.file_name().to_str().map(String::from)).filter(|n| n.len() > 4 && n.starts_with("card") && n[4..].chars().all(|c| c.is_ascii_digit())).collect())
        .unwrap_or_default();
    drm.sort();
    let drivers: Vec<String> = drm.iter().filter_map(|c| std::fs::read_link(format!("/sys/class/drm/{c}/device/driver")).ok()).filter_map(|p| p.file_name().map(|n| n.to_string_lossy().into_owned())).collect();
    r.add(OK, format!("GPUs: {}", if drivers.is_empty() { "?".to_string() } else { drivers.join(", ") }), "", "");
    if !drivers.iter().any(|d| d == "nvidia") {
        return;
    }
    let ms = read(Path::new("/sys/module/nvidia_drm/parameters/modeset"));
    r.add(if ms == "Y" { OK } else { FAIL }, "NVIDIA kernel modesetting", format!("nvidia_drm.modeset={}", if ms.is_empty() { "?" } else { &ms }), if ms == "Y" { "" } else { "add nvidia_drm.modeset=1 to the kernel command line" });
    let fb = read(Path::new("/sys/module/nvidia_drm/parameters/fbdev"));
    if !fb.is_empty() {
        r.add(if fb == "Y" { OK } else { WARN }, "NVIDIA framebuffer device", format!("nvidia_drm.fbdev={fb}"), if fb == "Y" { "" } else { "add nvidia_drm.fbdev=1 to the kernel command line (steadier Wayland sessions)" });
    }
    let ver = read(Path::new("/sys/module/nvidia/version"));
    if !ver.is_empty() {
        let major: u32 = ver.split('.').next().filter(|s| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit())).and_then(|s| s.parse().ok()).unwrap_or(0);
        r.add(if major >= 555 { OK } else { WARN }, "NVIDIA driver", ver.clone(), if major >= 555 { "" } else { "update to 555 or newer (explicit sync for Wayland)" });
    }
    if !["/usr/lib/gbm/nvidia-drm_gbm.so", "/usr/lib64/gbm/nvidia-drm_gbm.so", "/usr/lib/x86_64-linux-gnu/gbm/nvidia-drm_gbm.so"].iter().any(|p| Path::new(p).exists()) {
        r.add(WARN, "NVIDIA GBM backend", "nvidia-drm_gbm.so not found", "install the NVIDIA driver's GBM/EGL packages (egl-gbm)");
    }
    if drivers.len() > 1 {
        let render = render_gpu();
        if compositor_on_display_gpu() {
            r.add(WARN, "hybrid graphics: Wayfire draws with the displays' GPU (Settings > Displays > Graphics); on NVIDIA that can end the session -- Sonata turns it off if it does", "", "");
        } else if !render.is_empty() && !INTEGRATED.contains(&render.as_str()) {
            r.add(OK, "hybrid graphics: the screens are on the NVIDIA card (MUX in dGPU mode), Wayfire and every app draw with it", "", "");
        } else {
            r.add(OK, "hybrid graphics: Wayfire draws on the integrated GPU; games still use the NVIDIA card (prime-run / Steam)", "", "");
        }
    }
}

// -- the keyboard --------------------------------------------------------------------------------
fn field<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    let i = text.find(key)?;
    text[i + key.len()..].split_whitespace().next()
}

pub fn check_keyboard_from(r: &mut Report, localectl: &str) {
    match field(localectl, "X11 Layout:") {
        Some(lay) => {
            let var = field(localectl, "X11 Variant:").map(|v| format!(" ({v})")).unwrap_or_default();
            r.add(OK, format!("keyboard layout from the system: {lay}{var}"), "the Sonata session uses it unless you pick one in Settings > Keyboard", "");
        }
        None => r.add(WARN, "no system keyboard layout (localectl)", "Sonata starts with US; pick yours in Settings > Keyboard", ""),
    }
}

pub fn check_keyboard(r: &mut Report) {
    check_keyboard_from(r, &run(&["localectl", "status"]));
}

// -- the report ----------------------------------------------------------------------------------
const ERROR_MARKS: [&str; 6] = ["Traceback", "Error", "CRITICAL", "EE ", "cannot", "failed"];
const SESSION_LOGS: [&str; 7] = ["login.log", "session.log", "topbar.log", "dock.log", "launchpad.log", "wallpaper.log", "spotlight.log"];

/// The error lines of the last session's logs, `n` per log at most. The
/// session and the Python parts log in the Python Sonata's folder; on trial
/// this build's parts in their own (logs.rs): both are read.
pub fn last_session_errors(n: usize) -> String {
    let mut dirs = vec![logs::session_log_dir()];
    let own = logs::log_dir();
    if !dirs.contains(&own) {
        dirs.push(own);
    }
    let mut out = Vec::new();
    for name in SESSION_LOGS {
        for (i, dir) in dirs.iter().enumerate() {
            let Ok(raw) = std::fs::read(dir.join(name)) else { continue };
            let text = String::from_utf8_lossy(&raw);
            let bad: Vec<&str> = text.lines().filter(|l| ERROR_MARKS.iter().any(|m| l.contains(m))).collect();
            if !bad.is_empty() {
                out.push(if i == 0 { format!("--- {name} ---") } else { format!("--- {name} (Rust) ---") });
                out.extend(bad[bad.len().saturating_sub(n)..].iter().map(|s| s.to_string()));
            }
        }
    }
    out.join("\n")
}

fn panic_text(e: &(dyn std::any::Any + Send)) -> String {
    e.downcast_ref::<&str>().map(|s| s.to_string()).or_else(|| e.downcast_ref::<String>().cloned()).unwrap_or_default()
}

/// Every check (a report, never fatal: one that fails says so and the rest go on).
pub fn build() -> Report {
    let home = crate::home::sonata_home();
    let mut r = Report::default();
    let checks: [&dyn Fn(&mut Report); 7] = [&check_libraries, &|x| check_wayfire(x, home.as_deref()), &check_install, &check_tools, &check_lock, &check_gpu, &check_keyboard];
    for check in checks {
        let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| check(&mut r)));
        if let Err(e) = res {
            r.add(WARN, format!("check failed: {}", panic_text(&*e)), "", "");
        }
    }
    r
}

/// What doctor.txt holds: the checks and the last session's errors (also
/// for Feedbacker's reports).
pub fn report_text(r: &Report) -> String {
    let errors = last_session_errors(25);
    format!("{}{}\n", r.text(false), if errors.is_empty() { String::new() } else { format!("\n\nLast session:\n{errors}") })
}

/// `doctor` / `doctor windows`.
pub fn doctor(args: &[String]) -> i32 {
    if args.first().map(String::as_str) == Some("windows") {
        return stylecheck::main();
    }
    let r = build();
    let tty = unsafe { libc::isatty(1) } == 1;
    println!("{}", r.text(tty));
    // the logs' folder of this build (logs.rs): ~/.cache/sonata2-rs with Logs on, on trial
    let d = logs::log_dir();
    let _ = std::fs::create_dir_all(&d);
    let _ = std::fs::write(d.join("doctor.txt"), report_text(&r));
    println!("\nSaved to {}/doctor.txt", d.display());
    i32::from(r.failed())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The summary says how many problems and optional gaps there are,
    /// with each fix under its line.
    #[test]
    fn test_report_counts_problems_and_shows_fixes() {
        let mut r = Report::default();
        r.add(OK, "Python", "", "");
        r.add(FAIL, "Wayfire missing", "", "install wayfire");
        r.add(WARN, "grim missing", "screenshots", "");
        let t = r.text(false);
        assert!(t.contains("[FAIL] Wayfire missing"));
        assert!(t.contains("       fix: install wayfire"));
        assert!(t.contains("[warn] grim missing -- screenshots"));
        assert!(t.contains("[  ok] Python"));
        assert!(t.contains("1 problem(s) to fix before logging in."));
        assert!(t.contains("1 optional thing(s) missing"));
        let mut ok = Report::default();
        ok.add(OK, "all", "", "");
        assert!(ok.text(false).contains("Ready to log in to Sonata."));
        assert!(ok.text(true).contains("\x1b[32m  ok\x1b[0m"));
    }

    /// Bug reports get the error lines of each log, newest last, capped;
    /// read from $XDG_CACHE_HOME/sonata2, where the session's parts log
    /// with Logs on (test_last_session_errors_keeps_only_error_lines,
    /// test_errors_read_from_where_keep_writes_logs).
    #[test]
    fn test_last_session_errors_keeps_only_error_lines() {
        let _env = crate::tests::ENV.lock().unwrap_or_else(|e| e.into_inner());
        let cache = tempfile::tempdir().unwrap();
        let dir = cache.path().join("sonata2");
        std::fs::create_dir_all(&dir).unwrap();
        let lines: Vec<String> = (0..30).map(|i| format!("Error {i}")).chain(["all good".to_string()]).collect();
        std::fs::write(dir.join("dock.log"), lines.join("\n")).unwrap();
        std::env::set_var("XDG_CACHE_HOME", cache.path());
        std::env::set_var("SONATA_DEBUG", "1");
        let out = last_session_errors(5);
        std::env::remove_var("SONATA_DEBUG");
        std::env::remove_var("XDG_CACHE_HOME");
        let want: Vec<String> = std::iter::once("--- dock.log ---".to_string()).chain((25..30).map(|i| format!("Error {i}"))).collect();
        assert_eq!(out.lines().collect::<Vec<_>>(), want);
    }

    /// A PAM service in /usr/lib/pam.d (the lock screen accepts it) is OK for the doctor too.
    #[test]
    fn test_lock_service_in_usr_lib_is_not_a_failure() {
        let mut r = Report::default();
        check_lock_with(&mut r, &|p| p == Path::new("/usr/lib/pam.d/system-local-login"));
        assert_eq!(r.rows[0].0, OK);
        assert_eq!(r.rows[0].1, "lock screen PAM service: system-local-login");
        let mut r = Report::default();
        check_lock_with(&mut r, &|_| false);
        assert_eq!(r.rows[0].0, FAIL);
    }

    /// A fresh Arch install's doctor offered "sudo pacman -S wl-screenrec":
    /// it's only in the AUR.
    #[test]
    fn test_aur_only_tools_stay_out_of_the_pacman_fix() {
        let mut r = Report::default();
        check_tools_with(&mut r, "arch", &|c| c != "wl-screenrec" && c != "grim", &|_| true);
        let text = r.text(false);
        assert!(text.contains("wl-screenrec missing"));
        let fix = text.lines().find(|l| l.contains("fix: sudo pacman -S --needed")).unwrap();
        assert!(!fix.contains("wl-screenrec"), "{fix}");
        assert!(fix.contains(" grim"));
        let mut r = Report::default();
        check_tools_with(&mut r, "debian", &|c| c != "wl-screenrec", &|_| true);
        assert!(r.text(false).contains("fix: sudo apt install wl-screenrec"));
    }

    #[test]
    fn test_finds_the_plugins_install_built() {
        let home = tempfile::tempdir().unwrap();
        let user = home.path().join(".local/share/wayfire/plugin-manager/install/lib/wayfire");
        let own = home.path().join(".local/opt/sonata-wayfire/lib/wayfire");
        let multi = home.path().join(".local/opt/sonata-wayfire/lib/x86_64-linux-gnu/wayfire");
        for d in [&user, &own, &multi] {
            std::fs::create_dir_all(d).unwrap();
        }
        let dirs = plugin_dirs(home.path(), &home.path().join(".local/opt/sonata-wayfire"), "");
        assert!(dirs.contains(&user)); // pixdecor, sonata-corners: built there
        assert!(dirs.contains(&own));
        assert!(dirs.contains(&multi));
    }

    /// tests/test_wayfire_stack.py's test_doctor_reports_it: Wayfire built
    /// for Sonata (standalone) -- its own plugins, never the system's.
    #[test]
    fn test_doctor_reports_it() {
        use std::os::unix::fs::PermissionsExt;
        let home = tempfile::tempdir().unwrap();
        let own = home.path().join("own");
        std::fs::create_dir_all(own.join("bin")).unwrap();
        std::fs::create_dir_all(own.join("lib/wayfire")).unwrap();
        let exe = own.join("bin/wayfire");
        std::fs::write(&exe, "#!/bin/sh\necho '0.12.0-d181484c (Oct  6 2026) branch HEAD wlroots-0.20.2'\n").unwrap();
        std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
        std::fs::write(own.join("standalone"), "wlroots-0.20.2 wayfire-d181484c").unwrap();
        assert!(standalone(&own));
        let dirs = plugin_dirs(home.path(), &own, "/usr/lib/wayfire");
        assert!(dirs.contains(&own.join("lib/wayfire")));
        assert!(!dirs.contains(&PathBuf::from("/usr/lib/wayfire")));
        assert_eq!(major_minor("0.10.1"), Some((0, 10)));
        assert_eq!(major_minor("?"), None);
    }

    #[test]
    fn test_doctor_reports_nvidia() {
        let mut r = Report::default();
        check_gpu(&mut r); // never panics without NVIDIA
        assert!(r.rows[0].1.starts_with("GPUs: "));
    }

    /// No greetd PAM file: not ready (doctor suggests the fix), no crash;
    /// a Debian-style file (@include lines only) gets the three lines once.
    #[test]
    fn test_pam_ready_on_missing_file() {
        let d = tempfile::tempdir().unwrap();
        assert!(!pam_ready(&d.path().join("nope")));
        let text = "#%PAM-1.0\n@include common-auth\n@include common-account\n@include common-session\n";
        let new = pam_text(text);
        for (_, line) in PAM_LINES {
            assert_eq!(new.matches(line).count(), 1);
        }
        assert_eq!(pam_text(&new), new);
        std::fs::write(d.path().join("greetd"), &new).unwrap();
        assert!(pam_ready(&d.path().join("greetd")));
        let arch = "auth include login\nsession include login\n";
        assert_eq!(pam_text(arch).lines().nth(1), Some(PAM_LINES[0].1)); // after its kind's last line
    }

    #[test]
    fn keepassxc_in_use_needs_the_service_and_a_database() {
        assert!(keepassxc_in_use("[General]\nLastOpenedDatabases=/home/v/p.kdbx\n[FdoSecrets]\nEnabled=true\n"));
        assert!(!keepassxc_in_use("[General]\nLastOpenedDatabases=\n[FdoSecrets]\nEnabled=true\n"));
        assert!(!keepassxc_in_use("[General]\nLastActiveDatabase=/x.kdbx\n"));
    }

    #[test]
    fn keyboard_from_localectl() {
        let mut r = Report::default();
        check_keyboard_from(&mut r, "   System Locale: LANG=pt_BR.UTF-8\n       X11 Layout: br\n      X11 Variant: abnt2\n");
        assert_eq!(r.rows[0].1, "keyboard layout from the system: br (abnt2)");
        let mut r = Report::default();
        check_keyboard_from(&mut r, "");
        assert_eq!(r.rows[0].0, WARN);
    }

    #[test]
    fn libraries_are_looked_up_without_crashing() {
        let mut r = Report::default();
        check_libraries(&mut r);
        assert!(r.rows.iter().any(|x| x.1.starts_with("Gtk")));
    }
}
