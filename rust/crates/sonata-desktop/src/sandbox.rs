//! Open in Sandbox (sonata2/sandbox.py; Vini): an app with an empty home
//! folder of its own -- none of your files, sign-ins or settings, and
//! nothing it does is kept: the folder is deleted when the app quits.
//! bubblewrap (bwrap) mounts a temporary folder over your home for the app
//! alone; a Flatpak app gets one over its ~/.var/app/<id> and no access to
//! your home.
//!
//! The Dock shows an orange "S" on the app while a sandboxed copy runs
//! (running()). An app that is already open may hand the request to its
//! open window (one-window apps); quit it first.
//!
//! ```text
//! available()            // bwrap installed
//! open_app(&info, &[])   // starts it; false when it can't
//! ```

use gio::prelude::*;
use sonata_i18n::tr;
use std::cell::RefCell;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;

thread_local! {
    // desktop id -> number of sandboxed copies running
    static RUNNING: RefCell<HashMap<String, u32>> = RefCell::new(HashMap::new());
    // fn() when that changes (the Dock's badges)
    static LISTENERS: RefCell<Vec<Rc<dyn Fn()>>> = RefCell::new(Vec::new());
}

pub fn available() -> bool {
    glib::find_program_in_path("bwrap").is_some()
}

/// A sandboxed copy of the app runs ("spotify", without .desktop).
pub fn running(app_id: &str) -> bool {
    RUNNING.with(|r| r.borrow().get(app_id).copied().unwrap_or(0) > 0)
}

/// listener() whenever running() changes (sandbox.listeners.append).
pub fn add_listener(listener: impl Fn() + 'static) {
    LISTENERS.with(|l| l.borrow_mut().push(Rc::new(listener)));
}

fn changed() {
    let all: Vec<Rc<dyn Fn()>> = LISTENERS.with(|l| l.borrow().clone());
    for f in all {
        f();
    }
}

fn count(app_id: &str, delta: i32) {
    RUNNING.with(|r| {
        let mut r = r.borrow_mut();
        let n = r.entry(app_id.to_string()).or_insert(0);
        *n = (*n as i64 + delta as i64).max(0) as u32;
    });
    changed();
}

/// The Flatpak app id behind a desktop file's path (appperms.flatpak_id /
/// uninstall._flatpak_id), "" for other apps.
pub fn flatpak_id_of(path: &str) -> String {
    if path.contains("/flatpak/exports/") && path.ends_with(".desktop") {
        let base = path.rsplit('/').next().unwrap_or("");
        return base[..base.len() - ".desktop".len()].to_string();
    }
    let parts: Vec<&str> = path.split('/').collect();
    for i in 0..parts.len().saturating_sub(2) {
        if parts[i] == "flatpak" && parts[i + 1] == "app" {
            return parts[i + 2].to_string();
        }
    }
    String::new()
}

/// An app's Flatpak id ("" when it isn't one): by its desktop file, or
/// where that file really is.
pub fn flatpak_id(info: &gio::AppInfo) -> String {
    let Some(path) = info.downcast_ref::<gio::DesktopAppInfo>().and_then(|d| d.filename()) else { return String::new() };
    let id = flatpak_id_of(&path.to_string_lossy());
    if !id.is_empty() {
        return id;
    }
    std::fs::canonicalize(&path).map(|p| flatpak_id_of(&p.to_string_lossy())).unwrap_or_default()
}

fn commandline(info: &gio::AppInfo) -> String {
    info.commandline().map(|c| c.to_string_lossy().into_owned()).unwrap_or_default()
}

fn s(v: &str) -> String {
    v.to_string()
}

/// argv: the app inside bwrap, `home` (empty, temporary) as its home. From
/// its Exec line and Flatpak id ("" for none); real_home: the user's home.
pub fn command_for(commandline: &str, flatpak: &str, real_home: &Path, home: &Path) -> Vec<String> {
    let (real, home) = (real_home.display().to_string(), home.display().to_string());
    if !flatpak.is_empty() {
        let data = real_home.join(".var/app").join(flatpak);
        let _ = std::fs::create_dir_all(&data); // (the mount needs it there; Flatpak makes it anyway)
        return [s("bwrap"), s("--dev-bind"), s("/"), s("/"), s("--bind"), home, data.display().to_string(), s("flatpak"), s("run"), s("--nofilesystem=home"), s("--nofilesystem=host"), s(flatpak)].into();
    }
    let argv = exec_args(commandline, &[]);
    if argv.is_empty() {
        return argv;
    }
    // (/tmp stays: X11 apps reach Xwayland through /tmp/.X11-unix)
    let mut out = vec![s("bwrap"), s("--dev-bind"), s("/"), s("/"), s("--bind"), home, real.clone(), s("--setenv"), s("HOME"), real.clone(), s("--chdir"), real, s("--")];
    out.extend(argv);
    out
}

/// argv: the app inside bwrap, `home` (empty, temporary) as its home.
pub fn command(info: &gio::AppInfo, home: &Path) -> Vec<String> {
    command_for(&commandline(info), &flatpak_id(info), &glib::home_dir(), home)
}

/// What restricted_command hides, found on this computer.
#[derive(Clone, Debug, Default)]
pub struct Devices {
    /// /dev/video*, /dev/media*
    pub cameras: Vec<String>,
    /// $XDG_RUNTIME_DIR/pipewire-* (not the .lock files)
    pub pipewire: Vec<String>,
    /// $XDG_RUNTIME_DIR/pulse, when it is there
    pub pulse: Option<String>,
    /// /dev/snd is there
    pub snd: bool,
}

fn listed(dir: &Path, prefix: &str) -> Vec<String> {
    let mut out: Vec<String> = std::fs::read_dir(dir)
        .map(|rd| rd.flatten().filter(|e| e.file_name().to_string_lossy().starts_with(prefix)).map(|e| e.path().display().to_string()).collect())
        .unwrap_or_default();
    out.sort();
    out
}

impl Devices {
    pub fn found() -> Self {
        let mut cameras = listed(Path::new("/dev"), "video");
        cameras.extend(listed(Path::new("/dev"), "media"));
        cameras.sort();
        let rt = glib::user_runtime_dir();
        let pipewire = listed(&rt, "pipewire-").into_iter().filter(|p| !p.ends_with(".lock")).collect();
        let pulse = rt.join("pulse");
        Devices { cameras, pipewire, pulse: pulse.is_dir().then(|| pulse.display().to_string()), snd: Path::new("/dev/snd").is_dir() }
    }
}

/// restricted_command from what it needs: the Exec line, what is refused,
/// the files to open, what is on the computer, the user's home and the
/// app's own home (for "files").
pub fn restricted_command_for(commandline: &str, refused: &[&str], uris: &[String], dev: &Devices, home: &Path, own_home: &Path) -> Vec<String> {
    let argv = exec_args(commandline, uris);
    if argv.is_empty() {
        return argv;
    }
    let mut out = vec![s("bwrap"), s("--dev-bind"), s("/"), s("/")];
    if refused.contains(&"network") {
        out.push(s("--unshare-net"));
    }
    if refused.contains(&"camera") {
        for d in &dev.cameras {
            out.extend([s("--ro-bind"), s("/dev/null"), d.clone()]);
        }
    }
    if refused.contains(&"microphone") {
        for sock in &dev.pipewire {
            out.extend([s("--ro-bind"), s("/dev/null"), sock.clone()]);
        }
        if let Some(p) = &dev.pulse {
            out.extend([s("--tmpfs"), p.clone()]);
        }
        if dev.snd {
            out.extend([s("--tmpfs"), s("/dev/snd")]);
        }
    }
    if refused.contains(&"files") {
        out.extend([s("--bind"), own_home.display().to_string(), home.display().to_string()]);
    }
    out.push(s("--"));
    out.extend(argv);
    out
}

/// argv: a packaged app kept from what it may not use (appperms.limits):
/// network (its own empty network), camera (/dev/video*, /dev/media*
/// hidden), microphone (and sound: the PipeWire / PulseAudio sockets and
/// /dev/snd hidden), files (a home of its own, kept between opens, in
/// Sonata's data folder, sonata2-data/app-homes/<app> -- never the
/// install, which ./install.sh replaces).
pub fn restricted_command(info: &gio::AppInfo, refused: &[&str], uris: &[String]) -> Vec<String> {
    let id = info.id().map(|s| s.to_string()).filter(|s| !s.is_empty()).unwrap_or_else(|| "app".into());
    let own = sonata_config::data_dir("app-homes").join(id.strip_suffix(".desktop").unwrap_or(&id));
    if refused.contains(&"files") {
        use std::os::unix::fs::DirBuilderExt;
        let _ = std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&own);
    }
    restricted_command_for(&commandline(info), refused, uris, &Devices::found(), &glib::home_dir(), &own)
}

fn spawn(argv: &[String], launch_env: &[(String, String)]) -> Result<gio::Subprocess, glib::Error> {
    let launcher = gio::SubprocessLauncher::new(gio::SubprocessFlags::NONE);
    for (k, v) in launch_env {
        launcher.setenv(k, v, true);
    }
    let args: Vec<&std::ffi::OsStr> = argv.iter().map(|a| std::ffi::OsStr::new(a.as_str())).collect();
    launcher.spawn(&args)
}

pub fn run_restricted(info: &gio::AppInfo, refused: &[&str], uris: &[String], launch_env: &[(String, String)]) -> bool {
    let argv = restricted_command(info, refused, uris);
    if argv.is_empty() || !available() {
        return false;
    }
    match spawn(&argv, launch_env) {
        Ok(_) => true,
        Err(e) => {
            eprintln!("sonata2: restricted launch: {}", e.message());
            false
        }
    }
}

const FIELD_CODES: &str = "fFuUdDnNickvm";

/// An Exec line with its field codes (%U, %f, --uri=%U...) filled with
/// `uris` (file paths for %f/%F), or out when there are none.
pub fn exec_args(line: &str, uris: &[String]) -> Vec<String> {
    let Ok(words) = glib::shell_parse_argv(line) else { return Vec::new() };
    let mut out = Vec::new();
    for w in words {
        let a = w.to_string_lossy().into_owned();
        if ["%U", "%F", "%u", "%f"].contains(&a.as_str()) && !uris.is_empty() {
            let files: Vec<String> = uris
                .iter()
                .map(|u| if a.eq_ignore_ascii_case("%f") { gio::File::for_uri(u).path().map(|p| p.display().to_string()).unwrap_or_else(|| u.clone()) } else { u.clone() })
                .collect();
            if a == "%U" || a == "%F" {
                out.extend(files);
            } else {
                out.extend(files.into_iter().take(1));
            }
            continue;
        }
        // the field codes out (re.sub), then "%%" -> "%"
        let mut b = String::new();
        let mut chars = a.chars().peekable();
        while let Some(c) = chars.next() {
            if c == '%' && chars.peek().is_some_and(|n| FIELD_CODES.contains(*n)) {
                chars.next();
                continue;
            }
            b.push(c);
        }
        let b = b.replace("%%", "%");
        if !b.is_empty() && !(b != a && b.ends_with('=')) {
            out.push(b);
        }
    }
    out
}

/// Start the app in a sandbox; false when it can't.
pub fn open_app(info: &gio::AppInfo, launch_env: &[(String, String)]) -> bool {
    if !available() {
        return false;
    }
    let base = glib::user_runtime_dir();
    let base = if base.as_os_str().is_empty() { glib::tmp_dir() } else { base };
    // 0700: the app's alone
    let Some(home) = glib::mkdtemp(base.join("sonata-sandbox-XXXXXX")) else { return false };
    open_in(info, &home, &command(info, &home), launch_env)
}

fn open_in(info: &gio::AppInfo, home: &Path, argv: &[String], launch_env: &[(String, String)]) -> bool {
    if argv.is_empty() {
        let _ = std::fs::remove_dir_all(home);
        return false;
    }
    let proc = match spawn(argv, launch_env) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("sonata2: sandbox: {}", e.message());
            let _ = std::fs::remove_dir_all(home);
            return false;
        }
    };
    let id = info.id().map(|s| s.to_string()).unwrap_or_default();
    let app_id = id.strip_suffix(".desktop").unwrap_or(&id).to_string();
    count(&app_id, 1);
    let home: PathBuf = home.to_path_buf();
    proc.wait_async(gio::Cancellable::NONE, move |_res| {
        let _ = std::fs::remove_dir_all(&home); // nothing kept
        count(&app_id, -1);
    });
    true
}

/// The menus' "Open in Sandbox": said why when it can't.
pub fn ask_open(info: &gio::AppInfo) {
    if !available() {
        sonata_ui::dialog::alert(&tr!("Open in Sandbox needs bubblewrap"), &tr!("Install the bubblewrap package (./install.sh installs it), then try again."), &[("ok", tr!("OK"), "default")], |_| {}, None);
        return;
    }
    let i = info.clone();
    let go = move || {
        if !open_app(&i, &[]) {
            let heading = sonata_i18n::fmt(&tr!("“{name}” couldn't open in a sandbox"), &[("name", &i.display_name())]);
            sonata_ui::dialog::alert(&heading, "", &[("ok", tr!("OK"), "default")], |_| {}, None);
        }
    };
    // a locked app asks its password here too (this launch skips the usual launch path)
    let go = Rc::new(go);
    let g = go.clone();
    if !crate::applock::gate(info, move || g()) {
        go();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_native_gets_an_empty_home() {
        let home = PathBuf::from("/home/v");
        let argv = command_for("spotify --uri=%U", "", &home, Path::new("/run/user/1/sonata-sandbox-x"));
        assert_eq!(argv[..2], ["bwrap", "--dev-bind"]);
        let i = argv.iter().position(|a| a == "--bind").unwrap();
        assert_eq!(argv[i + 1..i + 3], ["/run/user/1/sonata-sandbox-x", "/home/v"]); // over the real home
        let dd = argv.iter().position(|a| a == "--").unwrap();
        assert_eq!(argv[dd + 1..], ["spotify"]); // %U: no file
        assert!(!argv.iter().any(|a| a == "--tmpfs")); // X11 socket in /tmp stays
    }

    #[test]
    fn test_flatpak_gets_an_empty_var_app() {
        let d = tempfile::tempdir().unwrap();
        let path = "/var/lib/flatpak/exports/share/applications/com.spotify.Client.desktop";
        assert_eq!(flatpak_id_of(path), "com.spotify.Client");
        assert_eq!(flatpak_id_of("/var/lib/flatpak/app/org.x.Y/current/active/export/share/applications/y.desktop"), "org.x.Y");
        assert_eq!(flatpak_id_of("/usr/share/applications/spotify.desktop"), "");
        let argv = command_for("spotify", "com.spotify.Client", d.path(), Path::new("/tmp/x"));
        assert!(argv.contains(&d.path().join(".var/app/com.spotify.Client").display().to_string()));
        assert_eq!(argv[argv.len() - 5..], ["flatpak", "run", "--nofilesystem=home", "--nofilesystem=host", "com.spotify.Client"]);
    }

    /// Settings > Apps > a packaged app: what it may not use, kept out when Sonata opens it.
    #[test]
    fn test_argv() {
        let dev = Devices { cameras: vec!["/dev/video0".into()], pipewire: vec!["/run/user/1/pipewire-0".into()], pulse: None, snd: false };
        let argv = restricted_command_for("spotify --uri=%U", &["network", "camera", "microphone", "files"], &["file:///tmp/a.mp3".into()], &dev, Path::new("/home/v"), Path::new("/d/app-homes/spotify"));
        assert!(argv.iter().any(|a| a == "--unshare-net"));
        assert!(argv.windows(3).any(|w| w == ["--ro-bind", "/dev/null", "/dev/video0"]));
        assert!(argv.iter().any(|a| a == "/run/user/1/pipewire-0"));
        let i = argv.iter().position(|a| a == "/home/v").unwrap();
        assert!(argv[i - 1].ends_with("app-homes/spotify")); // a home of its own, kept
        let dd = argv.iter().position(|a| a == "--").unwrap();
        assert_eq!(argv[dd + 1..], ["spotify"]); // (only a bare %U takes the files, as sandbox.py)
        let cam = restricted_command_for("spotify", &["camera"], &[], &dev, Path::new("/home/v"), Path::new("/x"));
        assert!(!cam.iter().any(|a| a == "--unshare-net"));
    }

    #[test]
    fn test_exec_fills_files() {
        assert_eq!(exec_args("vlc %U", &["file:///a.mp4".into()]), ["vlc", "file:///a.mp4"]);
        assert_eq!(exec_args("gimp %f", &["file:///tmp/x.png".into()]), ["gimp", "/tmp/x.png"]);
        assert_eq!(exec_args("gimp %f", &[]), ["gimp"]);
        assert_eq!(exec_args("app --uri=%u --x=100%%", &[]), ["app", "--x=100%"]);
        assert_eq!(exec_args("sh -c 'echo hi'", &[]), ["sh", "-c", "echo hi"]);
        assert!(exec_args("", &[]).is_empty());
        assert!(exec_args("broken 'quote", &[]).is_empty());
    }

    #[test]
    fn test_runs_and_cleans_up() {
        let ctx = glib::MainContext::default();
        let _g = ctx.acquire().unwrap();
        let seen: Rc<RefCell<Vec<bool>>> = Rc::default();
        let s2 = seen.clone();
        add_listener(move || s2.borrow_mut().push(running("")));
        let kf = glib::KeyFile::new();
        kf.load_from_data("[Desktop Entry]\nType=Application\nName=Spotify\nExec=true\n", glib::KeyFileFlags::NONE).unwrap();
        let info: gio::AppInfo = gio::DesktopAppInfo::from_keyfile(&kf).unwrap().upcast();
        // (made from a key file it has no id: counted as "")
        let d = tempfile::tempdir().unwrap();
        let home = d.path().join("home");
        std::fs::create_dir(&home).unwrap();
        std::fs::write(home.join("secret"), "").unwrap();
        assert!(open_in(&info, &home, &["sh".into(), "-c".into(), "exit 0".into()], &[]));
        assert!(running(""));
        let end = std::time::Instant::now() + std::time::Duration::from_secs(10);
        while running("") && std::time::Instant::now() < end {
            ctx.iteration(true);
        }
        LISTENERS.with(|l| l.borrow_mut().clear());
        assert!(!running(""));
        assert!(!home.exists()); // nothing kept
        assert_eq!(*seen.borrow(), [true, false]);
    }
}
