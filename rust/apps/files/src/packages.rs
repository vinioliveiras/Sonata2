//! Linux app packages and archives, opened the way people expect
//! (files/packages.py): double-click an installer (.pkg.tar.zst, .deb,
//! .rpm, .flatpak(ref), .snap) to install it, an AppImage to run it, an
//! archive (.tar.gz, .zip, .rar, .7z...) to extract it next to itself
//! (macOS Archive Utility). The same actions are in the right-click menus
//! (Files, desktop), and a shell script's Run (a double-click on a .sh runs
//! it too, in a terminal).
//!
//! Installs run in a terminal window that shows what the package manager
//! does (dependencies, conflicts); the password is asked first by Sonata's
//! own prompt (pkexec), not inside the terminal. A package for another
//! distro family isn't forced in; an alert says what this system installs
//! instead.
//!
//! Tar archives are expanded by bsdtar (libarchive) or GNU tar, not
//! Python's tarfile: both refuse members that would land outside the
//! folder (absolute paths, "..").

use gio::prelude::*;
use sonata_i18n::{fmt, n_, tr};
use sonata_system::selfupdate::quote;
use sonata_ui::menu::Item;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::Arc;

/// suffix -> package format (longest suffixes first)
const FORMATS: [(&str, &str); 8] = [(".pkg.tar.zst", "arch"), (".pkg.tar.xz", "arch"), (".deb", "debian"), (".rpm", "rpm"), (".flatpakref", "flatpakref"), (".flatpak", "flatpak"), (".snap", "snap"), (".appimage", "appimage")];
const ARCHIVES: [&str; 11] = [".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tar", ".zip", ".rar", ".7z"];
/// RAR and 7-Zip (Vini): through the first tool there is -- 7-Zip, unrar,
/// or libarchive's bsdtar (installed with pacman itself on Arch / CachyOS:
/// RAR 4 and 5 work with nothing more installed)
fn tools_for(ext: &str) -> &'static [&'static str] {
    match ext {
        "rar" => &["7z", "7zz", "unrar", "bsdtar"],
        "7z" => &["7z", "7zz", "bsdtar"],
        _ => &[],
    }
}

pub const FAMILY_NAMES: [(&str, &str); 3] = [("arch", n_!("Arch Linux (.pkg.tar.zst)")), ("debian", n_!("Debian and Ubuntu (.deb)")), ("rpm", n_!("Fedora, openSUSE and RHEL (.rpm)"))];

fn family_name(k: &str) -> Option<&'static str> {
    FAMILY_NAMES.iter().find(|(f, _)| *f == k).map(|(_, n)| *n)
}

fn base_lower(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().to_lowercase()).unwrap_or_default()
}

/// "arch", "debian", "rpm", "flatpak", "flatpakref", "snap", "appimage",
/// "archive" or None.
pub fn kind(path: &Path) -> Option<&'static str> {
    let low = base_lower(path);
    if let Some((_, k)) = FORMATS.iter().find(|(s, _)| low.ends_with(s)) {
        return Some(k);
    }
    ARCHIVES.iter().any(|s| low.ends_with(s)).then_some("archive")
}

/// What's installed, asked by name: tests give their own.
pub type Which = Arc<dyn Fn(&str) -> Option<PathBuf> + Send + Sync>;

fn system_which() -> Which {
    Arc::new(sonata_backend::run::which)
}

/// How an install becomes root: pkexec, so Sonata's own password prompt
/// asks first (Vini: sudo asked inside the terminal and the install could
/// fail there); sudo where there's no pkexec.
fn root(has: &dyn Fn(&str) -> bool) -> &'static str {
    if has("pkexec") { "pkexec" } else { "sudo" }
}

/// Convert a .deb into an Arch package with debtap, then install it.
fn debtap_command(path: &Path, root: &str) -> String {
    let d = path.parent().map(|d| d.to_string_lossy().into_owned()).filter(|d| !d.is_empty()).unwrap_or_else(|| ".".into());
    format!("cd {} && debtap -Q {} && {root} pacman -U \"$(ls -t -- *.pkg.tar.zst | head -n 1)\"", quote(&d), quote(&path.to_string_lossy()))
}

fn aur_helper(has: &dyn Fn(&str) -> bool) -> Option<&'static str> {
    ["paru", "yay"].into_iter().find(|h| has(h))
}

/// Shell command that installs `path` on a system of `family` with these
/// tools, or None.
pub fn install_command_with(path: &Path, family: &str, has: &dyn Fn(&str) -> bool) -> Option<String> {
    let k = kind(path)?;
    let q = quote(&path.to_string_lossy());
    let r = root(has);
    if k == "debian" && family == "arch" && has("debtap") {
        return Some(debtap_command(path, r)); // .deb on Arch: converted first
    }
    if matches!(k, "arch" | "debian" | "rpm") && k != family {
        return None;
    }
    match k {
        "arch" => Some(format!("{r} pacman -U {q}")),
        "debian" => Some(format!("{r} apt install {q}")),
        "rpm" => Some(if has("dnf") { format!("{r} dnf install {q}") } else { format!("{r} zypper install {q}") }),
        "flatpakref" if has("flatpak") => Some(format!("flatpak install --user {q}")),
        "flatpak" if has("flatpak") => Some(format!("flatpak install --user --bundle {q}")),
        "snap" if has("snap") => Some(format!("{r} snap install --dangerous {q}")),
        _ => None,
    }
}

fn has_tool(t: &str) -> bool {
    sonata_backend::run::which(t).is_some()
}

pub fn install_command(path: &Path) -> Option<String> {
    install_command_with(path, sonata_backend::system::package_family(), &has_tool)
}

/// What Install does with a package here.
#[derive(Debug, PartialEq)]
pub enum Plan {
    /// this command, in a terminal
    Run(String),
    /// a .deb on Arch without debtap: offer to get it (through this AUR
    /// helper) and convert
    OfferDebtap(String),
    /// it can't be installed here: why
    Explain(String),
}

pub fn install_plan(path: &Path, family: &str, has: &dyn Fn(&str) -> bool) -> Plan {
    if let Some(cmd) = install_command_with(path, family, has) {
        return Plan::Run(cmd);
    }
    let k = kind(path).unwrap_or("");
    if k == "debian" && family == "arch" {
        if let Some(helper) = aur_helper(has) {
            // debtap (AUR) converts .deb packages for Arch; offer to get it
            let r = root(has);
            return Plan::OfferDebtap(format!("{helper} -S debtap && {r} debtap -u && {}", debtap_command(path, r)));
        }
    }
    if let Some(fam) = family_name(k) {
        let mut body = fmt(&tr!("This is a package for {family}."), &[("family", &sonata_i18n::gettext(fam))]);
        if let Some(here) = family_name(family) {
            body.push(' ');
            body.push_str(&fmt(&tr!("This computer installs packages for {family}."), &[("family", &sonata_i18n::gettext(here))]));
        }
        body.push(' ');
        body.push_str(&tr!("Look for a version of the app made for this system, a Flatpak or an AppImage."));
        return Plan::Explain(body);
    }
    let tool = match k {
        "flatpak" | "flatpakref" => Some("Flatpak"),
        "snap" => Some("Snap"),
        _ => None,
    };
    Plan::Explain(match tool {
        Some(t) => fmt(&tr!("{tool} isn't installed on this computer."), &[("tool", t)]),
        None => tr!("Its installer isn't installed on this computer."),
    })
}

fn name_of(path: &Path) -> String {
    path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default()
}

fn ok_alert(heading: &str, body: &str, parent: Option<&gtk::Widget>) {
    sonata_ui::dialog::alert(heading, body, &[("ok", "OK".into(), "default")], |_| {}, parent);
}

/// Double-click: install / run / extract, a shell script run. False = none
/// of those (the caller opens it the usual way).
pub fn open_path(path: &Path, parent: Option<&gtk::Widget>) -> bool {
    match kind(path) {
        None => {
            // a shell script (.sh / .bash / .zsh) runs, in a terminal that
            // stays open: its output and whatever it asks (Vini: double-click runs it)
            if script_suffix(path).is_some() && is_script(path) {
                run_script(path, parent, true);
                return true;
            }
            false
        }
        Some("appimage") => {
            run_appimage(path, parent);
            true
        }
        Some("archive") => {
            extract(path, parent, None);
            true
        }
        Some(_) => {
            install(path, parent);
            true
        }
    }
}

pub fn install(path: &Path, parent: Option<&gtk::Widget>) {
    let name = name_of(path);
    match install_plan(path, sonata_backend::system::package_family(), &has_tool) {
        Plan::Run(cmd) => {
            if !sonata_backend::system::run_in_terminal(&cmd) {
                ok_alert(&tr!("No terminal app was found."), &format!("{}\n{cmd}", tr!("Install it with:")), parent);
            }
        }
        Plan::OfferDebtap(cmd) => {
            sonata_ui::dialog::alert(
                &fmt(&tr!("“{name}” is a package for Debian and Ubuntu."), &[("name", &name)]),
                &tr!("It can be converted into an Arch package with debtap, which isn't installed yet. Converted packages usually work, but a version made for this system, a Flatpak or an AppImage is safer."),
                &[("cancel", tr!("Cancel"), ""), ("debtap", tr!("Install debtap and Convert"), "default")],
                move |r| {
                    if r == "debtap" {
                        sonata_backend::system::run_in_terminal(&cmd);
                    }
                },
                parent,
            );
        }
        Plan::Explain(body) => ok_alert(&fmt(&tr!("“{name}” can’t be installed."), &[("name", &name)]), &body, parent),
    }
}

// -- AppImages ------------------------------------------------------------------------------------

/// Run an AppImage; the first time (per file) only after the user confirms
/// (Gatekeeper's "downloaded from the Internet" question). Installing it is
/// appimage.rs.
pub fn run_appimage(path: &Path, parent: Option<&gtk::Widget>) {
    if crate::appimage::trusted(path) {
        launch_appimage(path, parent);
        return;
    }
    let (p, w) = (path.to_path_buf(), parent.map(|w| w.downgrade()));
    sonata_ui::dialog::alert(
        &fmt(&tr!("Are you sure you want to open “{name}”?"), &[("name", &name_of(path))]),
        &tr!("This app will run with your permissions and can access your files. Only open apps from developers you trust."),
        &[("cancel", tr!("Cancel"), ""), ("open", tr!("Open"), "default")],
        move |rid| {
            if rid == "open" {
                crate::appimage::trust(&p);
                launch_appimage(&p, w.as_ref().and_then(|w| w.upgrade()).as_ref());
            }
        },
        parent,
    );
}

/// Started in its own folder; GLib reaps it when it ends.
fn spawn_in(argv: &[&std::ffi::OsStr], folder: Option<&Path>) -> Result<(), glib::Error> {
    let l = gio::SubprocessLauncher::new(gio::SubprocessFlags::NONE);
    if let Some(d) = folder.filter(|d| !d.as_os_str().is_empty()) {
        l.set_cwd(d);
    }
    l.spawn(argv).map(|_| ())
}

fn launch_appimage(path: &Path, parent: Option<&gtk::Widget>) {
    use std::os::unix::fs::PermissionsExt;
    let res = (|| -> Result<(), String> {
        let mode = std::fs::metadata(path).map_err(|e| e.to_string())?.permissions().mode();
        if mode & 0o100 == 0 {
            // make it executable, like "chmod +x"
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode | 0o111)).map_err(|e| e.to_string())?;
        }
        spawn_in(&[path.as_os_str()], path.parent()).map_err(|e| e.message().to_string())
    })();
    if let Err(e) = res {
        ok_alert(&fmt(&tr!("“{name}” can’t be opened."), &[("name", &name_of(path))]), &e, parent);
    }
}

// -- archives -------------------------------------------------------------------------------------

/// A folder named after the archive, next to it ("a", then "a 2"...):
/// never into an existing one.
pub fn extract_dir(path: &Path) -> PathBuf {
    let mut base = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let low = base.to_lowercase();
    if let Some(s) = ARCHIVES.iter().find(|s| low.ends_with(*s)) {
        base.truncate(base.len() - s.len());
    }
    let folder = path.parent().unwrap_or(Path::new(""));
    let mut target = folder.join(&base);
    let mut n = 1;
    while std::fs::symlink_metadata(&target).is_ok() {
        n += 1;
        target = folder.join(format!("{base} {n}"));
    }
    target
}

/// Why an archive wasn't expanded.
#[derive(Debug, PartialEq)]
pub enum ExtractError {
    /// protected: ask for its password (wrong: a wrong one was given)
    Password { wrong: bool },
    Failed(String),
}

const PASSWORD_WORDS: [&str; 3] = ["password", "passphrase", "encrypted"];

fn ext_of(path: &Path) -> String {
    path.extension().map(|e| e.to_string_lossy().to_lowercase()).unwrap_or_default()
}

/// argv that extracts a .rar / .7z into `target` (None: no tool for it).
/// With no password, a tool never stops to ask for one: it fails.
pub fn tool_command(path: &Path, target: &Path, password: Option<&str>, which: &Which) -> Option<Vec<String>> {
    let (p, t) = (path.to_string_lossy().into_owned(), target.to_string_lossy().into_owned());
    for tool in tools_for(&ext_of(path)) {
        let Some(exe) = which(tool) else { continue };
        let exe = exe.to_string_lossy().into_owned();
        let pw = password.unwrap_or("");
        return Some(match *tool {
            "7z" | "7zz" => vec![exe, "x".into(), "-y".into(), "-bso0".into(), "-bsp0".into(), format!("-p{pw}"), format!("-o{t}"), "--".into(), p],
            "unrar" => vec![exe, "x".into(), "-o+".into(), "-y".into(), format!("-p{}", if pw.is_empty() { "-" } else { pw }), "--".into(), p, format!("{t}/")],
            _ => {
                // bsdtar: never writes outside target
                let mut v = vec![exe, "-xf".into(), p];
                if let Some(pw) = password.filter(|s| !s.is_empty()) {
                    v.extend(["--passphrase".into(), pw.to_string()]);
                }
                v.extend(["-C".into(), t]);
                v
            }
        });
    }
    None
}

/// (exit code, stdout + stderr); no stdin: nothing waits for a password
/// typed in a terminal.
fn run_argv(argv: &[String]) -> Result<(i32, String), String> {
    let out = std::process::Command::new(&argv[0]).args(&argv[1..]).stdin(std::process::Stdio::null()).output().map_err(|e| e.to_string())?;
    let mut msg = String::from_utf8_lossy(&out.stderr).trim().to_string();
    if msg.is_empty() {
        msg = String::from_utf8_lossy(&out.stdout).trim().to_string();
    }
    Ok((out.status.code().unwrap_or(1), msg))
}

/// A tool's failure: a password it wants, or its last line.
fn tool_failed(argv: &[String], msg: &str, password: Option<&str>) -> ExtractError {
    let low = msg.to_lowercase();
    if PASSWORD_WORDS.iter().any(|w| low.contains(w)) {
        return ExtractError::Password { wrong: password.is_some_and(|p| !p.is_empty()) };
    }
    let tool = Path::new(&argv[0]).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    ExtractError::Failed(msg.lines().last().map(str::to_string).unwrap_or_else(|| format!("{tool} failed")))
}

fn run_tool(path: &Path, target: &Path, password: Option<&str>, which: &Which) -> Result<(), ExtractError> {
    let Some(argv) = tool_command(path, target, password, which) else {
        return Err(ExtractError::Failed(tr!("Expanding this kind of archive needs 7-Zip (the 7zip package) or bsdtar (libarchive).")));
    };
    match run_argv(&argv).map_err(ExtractError::Failed)? {
        (0, _) => Ok(()),
        (_, msg) => Err(tool_failed(&argv, &msg, password)),
    }
}

/// A tarball (any compression): bsdtar, else GNU tar.
fn untar(path: &Path, target: &Path, which: &Which) -> Result<(), ExtractError> {
    let (p, t) = (path.to_string_lossy().into_owned(), target.to_string_lossy().into_owned());
    let argv: Vec<String> = if let Some(b) = which("bsdtar") {
        vec![b.to_string_lossy().into_owned(), "-xf".into(), p, "-C".into(), t]
    } else if let Some(g) = which("tar") {
        let mut v = vec![g.to_string_lossy().into_owned()];
        if base_lower(path).ends_with(".tar.zst") {
            v.push("--zstd".into());
        }
        v.extend(["-xf".into(), p, "-C".into(), t]);
        v
    } else {
        return Err(ExtractError::Failed("tar".into()));
    };
    match run_argv(&argv).map_err(ExtractError::Failed)? {
        (0, _) => Ok(()),
        (_, msg) => Err(ExtractError::Failed(msg.lines().last().map(str::to_string).unwrap_or_else(|| "tar failed".into()))),
    }
}

/// A .zip: names that would land outside the folder are skipped, links
/// written as plain files (zipfile's way); ZipCrypto passwords.
fn unzip(path: &Path, target: &Path, password: Option<&str>) -> Result<(), ExtractError> {
    let fail = |e: &dyn std::fmt::Display| ExtractError::Failed(e.to_string());
    let f = std::fs::File::open(path).map_err(|e| fail(&e))?;
    let mut z = zip::ZipArchive::new(f).map_err(|e| fail(&e))?;
    let pw = password.filter(|p| !p.is_empty());
    for i in 0..z.len() {
        let entry = match pw {
            Some(p) => z.by_index_decrypt(i, p.as_bytes()),
            None => z.by_index(i),
        };
        let mut entry = match entry {
            Ok(e) => e,
            Err(zip::result::ZipError::UnsupportedArchive(m)) if m == zip::result::ZipError::PASSWORD_REQUIRED => return Err(ExtractError::Password { wrong: false }),
            Err(zip::result::ZipError::InvalidPassword) => return Err(ExtractError::Password { wrong: true }),
            Err(e) => return Err(fail(&e)),
        };
        let Some(rel) = entry.enclosed_name() else { continue };
        let out = target.join(rel);
        if entry.is_dir() {
            std::fs::create_dir_all(&out).map_err(|e| fail(&e))?;
            continue;
        }
        if let Some(d) = out.parent() {
            std::fs::create_dir_all(d).map_err(|e| fail(&e))?;
        }
        let encrypted = entry.encrypted();
        let mut data = Vec::new();
        if let Err(e) = entry.read_to_end(&mut data) {
            // ZipCrypto lets about one wrong password in 256 through its
            // check: the checksum then fails
            if encrypted && pw.is_some() {
                return Err(ExtractError::Password { wrong: true });
            }
            return Err(fail(&e));
        }
        std::fs::write(&out, data).map_err(|e| fail(&e))?;
    }
    Ok(())
}

/// Expand `path` into `target` (made here), blocking. A failure leaves no
/// half-extracted folder behind.
pub fn extract_into(path: &Path, target: &Path, password: Option<&str>, which: &Which) -> Result<(), ExtractError> {
    std::fs::create_dir(target).map_err(|e| ExtractError::Failed(e.to_string()))?;
    let low = base_lower(path);
    let res = if low.ends_with(".rar") || low.ends_with(".7z") {
        run_tool(path, target, password, which)
    } else if low.ends_with(".zip") {
        unzip(path, target, password)
    } else {
        untar(path, target, which)
    };
    if res.is_err() {
        let _ = std::fs::remove_dir_all(target);
    }
    res
}

/// Finder's Archive Utility: the archive's password, typed in a secure
/// field; on_done(password) unless cancelled.
pub fn ask_password(path: &Path, parent: Option<&gtk::Widget>, wrong: bool, on_done: impl Fn(String) + 'static) -> adw::AlertDialog {
    use adw::prelude::*;
    let field = sonata_ui::controls::secret_field("", &tr!("Password"));
    field.set_activates_default(true);
    let name = name_of(path);
    let heading = if wrong { tr!("The password is incorrect.") } else { fmt(&tr!("“{name}” is protected by a password."), &[("name", &name)]) };
    let body = if wrong { fmt(&tr!("Enter the password for “{name}”."), &[("name", &name)]) } else { tr!("Enter its password to expand it.") };
    let f = field.clone();
    let dlg = sonata_ui::dialog::alert(&heading, &body, &[("cancel", tr!("Cancel"), ""), ("ok", tr!("Expand"), "default")], move |rid| {
        let pw = f.text().to_string();
        if rid == "ok" && !pw.is_empty() {
            on_done(pw);
        }
    }, parent);
    dlg.set_extra_child(Some(&field));
    glib::idle_add_local_once(move || {
        field.grab_focus();
    });
    dlg
}

type Asker = Rc<dyn Fn(&Path, bool, Box<dyn Fn(String)>)>;
type Failer = Rc<dyn Fn(&Path, String)>;
type Done = Option<Rc<dyn Fn(PathBuf)>>;

/// Into a folder named after the archive, next to it (in a thread). A
/// protected archive asks for its password (and again when it's wrong);
/// done(folder) once it's there.
pub fn extract(path: &Path, parent: Option<&gtk::Widget>, done: Done) {
    let w = parent.map(|p| p.downgrade());
    let ask: Asker = Rc::new(move |p, wrong, then| {
        let parent = w.as_ref().and_then(|w| w.upgrade());
        ask_password(p, parent.as_ref(), wrong, then);
    });
    let w = parent.map(|p| p.downgrade());
    let fail: Failer = Rc::new(move |p, err| {
        let parent = w.as_ref().and_then(|w| w.upgrade());
        ok_alert(&fmt(&tr!("Unable to expand “{name}”."), &[("name", &name_of(p))]), &err, parent.as_ref());
    });
    extract_with(path.to_path_buf(), None, system_which(), ask, fail, done);
}

fn extract_with(path: PathBuf, password: Option<String>, which: Which, ask: Asker, fail: Failer, done: Done) {
    let target = extract_dir(&path);
    let (p, t, pw, wh) = (path.clone(), target.clone(), password.clone(), which.clone());
    sonata_system::run_async(move || extract_into(&p, &t, pw.as_deref(), &wh), move |res| match res {
        Ok(()) => {
            if let Some(d) = &done {
                d(target);
            }
        }
        Err(ExtractError::Password { wrong }) => {
            let (p, a) = (path.clone(), ask.clone());
            ask(&path, wrong, Box::new(move |pw| extract_with(p.clone(), Some(pw), which.clone(), a.clone(), fail.clone(), done.clone())));
        }
        Err(ExtractError::Failed(e)) => fail(&path, e),
    });
}

// -- shell scripts: Run (Vini) ----------------------------------------------------------------------

fn script_suffix(path: &Path) -> Option<&'static str> {
    match ext_of(path).as_str() {
        "sh" | "bash" => Some("bash"),
        "zsh" => Some("zsh"),
        _ => None,
    }
}

fn executable(path: &Path) -> bool {
    std::ffi::CString::new(path.as_os_str().as_encoded_bytes()).is_ok_and(|c| unsafe { libc::access(c.as_ptr(), libc::X_OK) } == 0)
}

/// A script to run: .sh / .bash / .zsh, or an executable file starting with "#!".
pub fn is_script(path: &Path) -> bool {
    if path.as_os_str().is_empty() || !path.is_file() {
        return false;
    }
    script_suffix(path).is_some() || (executable(path) && shebang(path).is_some())
}

/// The "#!" line's interpreter and its arguments, or None.
fn shebang(path: &Path) -> Option<Vec<String>> {
    let mut f = std::fs::File::open(path).ok()?;
    let mut buf = [0u8; 256];
    let n = f.read(&mut buf).ok()?;
    let line = &buf[..n];
    let line = &line[..line.iter().position(|b| *b == b'\n').map(|i| i + 1).unwrap_or(n)];
    let rest = line.strip_prefix(b"#!")?;
    let words: Vec<String> = String::from_utf8_lossy(rest).split_whitespace().map(str::to_string).collect();
    (!words.is_empty()).then_some(words)
}

/// How to run it: itself when executable, else through its "#!" line's
/// interpreter (or the shell its suffix names) -- no chmod +x needed.
pub fn script_argv(path: &Path) -> Vec<String> {
    let p = path.to_string_lossy().into_owned();
    if executable(path) {
        return vec![p];
    }
    let mut v = shebang(path).unwrap_or_else(|| vec![script_suffix(path).unwrap_or("sh").to_string()]);
    v.push(p);
    v
}

fn folder_of(path: &Path) -> String {
    path.parent().map(|d| d.to_string_lossy().into_owned()).filter(|d| !d.is_empty()).unwrap_or_else(|| ".".into())
}

/// What the terminal runs: the script, in its own folder.
pub fn script_command(path: &Path) -> String {
    let argv: Vec<String> = script_argv(path).iter().map(|a| quote(a)).collect();
    format!("cd {} && {}", quote(&folder_of(path)), argv.join(" "))
}

/// Run a script in its own folder: in a terminal window that stays open
/// (its output, and whatever it asks), or in the background.
pub fn run_script(path: &Path, parent: Option<&gtk::Widget>, terminal: bool) {
    if terminal {
        let cmd = script_command(path);
        if !sonata_backend::system::run_in_terminal(&cmd) {
            ok_alert(&tr!("No terminal app was found."), &format!("{}\n{cmd}", tr!("Run it with:")), parent);
        }
        return;
    }
    let argv = script_argv(path);
    let args: Vec<&std::ffi::OsStr> = argv.iter().map(std::ffi::OsStr::new).collect();
    if let Err(e) = spawn_in(&args, path.parent()) {
        ok_alert(&fmt(&tr!("“{name}” can’t be run."), &[("name", &name_of(path))]), e.message(), parent);
    }
}

// -- the menus ------------------------------------------------------------------------------------

/// A right-click entry for a package, archive or script.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Action {
    RunInTerminal,
    Run,
    RunAppImage,
    InstallAppImage,
    Extract,
    Install,
}

impl Action {
    pub fn label(self) -> String {
        match self {
            Action::RunInTerminal => tr!("Run in Terminal"),
            Action::Run | Action::RunAppImage => tr!("Run"),
            Action::InstallAppImage | Action::Install => tr!("Install"),
            Action::Extract => tr!("Extract Here"),
        }
    }

    pub fn act(self, path: &Path, parent: Option<&gtk::Widget>) {
        match self {
            Action::RunInTerminal => run_script(path, parent, true),
            Action::Run => run_script(path, parent, false),
            Action::RunAppImage => run_appimage(path, parent),
            Action::InstallAppImage => crate::appimage::install(path, parent),
            Action::Extract => extract(path, parent, None),
            Action::Install => install(path, parent), // explains when it can't
        }
    }
}

/// The entries for a package / archive / script ([] for other files).
pub fn actions(path: &Path) -> Vec<Action> {
    match kind(path) {
        None if is_script(path) => vec![Action::RunInTerminal, Action::Run],
        None => vec![],
        Some("appimage") => vec![Action::RunAppImage, Action::InstallAppImage],
        Some("archive") => vec![Action::Extract],
        Some(_) => vec![Action::Install],
    }
}

/// Right-click entries for a package / archive / script; their alerts on
/// `parent`.
pub fn menu_items(path: &Path, parent: Option<&gtk::Widget>) -> Vec<Item> {
    actions(path)
        .into_iter()
        .map(|a| {
            let (p, w) = (path.to_path_buf(), parent.map(|w| w.downgrade()));
            Item::new(&a.label(), move || a.act(&p, w.as_ref().and_then(|w| w.upgrade()).as_ref()))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    const CASES: [(&str, Option<&str>); 20] = [
        ("yay-12.4.2-1-x86_64.pkg.tar.zst", Some("arch")),
        ("old-1-1-any.pkg.tar.xz", Some("arch")),
        ("Discord 0.0.70.deb", Some("debian")),
        ("code_1.95_amd64.DEB", Some("debian")),
        ("google-chrome-stable.x86_64.rpm", Some("rpm")),
        ("com.spotify.Client.flatpakref", Some("flatpakref")),
        ("app.flatpak", Some("flatpak")),
        ("app_1.0.snap", Some("snap")),
        ("Obsidian-1.7.AppImage", Some("appimage")),
        ("tool.appimage", Some("appimage")),
        ("src.tar.gz", Some("archive")),
        ("src.tgz", Some("archive")),
        ("x.tar.xz", Some("archive")),
        ("x.tar.bz2", Some("archive")),
        ("x.tar.zst", Some("archive")),
        ("x.zip", Some("archive")),
        ("x.rar", Some("archive")),
        ("x.7z", Some("archive")),
        ("notes.txt", None),
        ("pkg.tar.zst.sig", None),
    ];

    #[test]
    fn kinds() {
        for (name, k) in CASES {
            assert_eq!(kind(&Path::new("/d").join(name)), k, "{name}");
        }
        assert_eq!(kind(Path::new("x.FLATPAKREF")), Some("flatpakref"));
        assert_eq!(kind(Path::new("a/Fotos.RAR")), Some("archive"));
        assert_eq!(kind(Path::new("")), None);
    }

    #[test]
    fn menu_entries() {
        for (name, k) in CASES {
            let want: Vec<&str> = match k {
                None => vec![],
                Some("appimage") => vec!["Run", "Install"],
                Some("archive") => vec!["Extract Here"],
                _ => vec!["Install"],
            };
            let got: Vec<String> = actions(&Path::new("/d").join(name)).iter().map(|a| a.label()).collect();
            assert_eq!(got, want, "{name}");
        }
    }

    fn cmd(name: &str, fam: &str, tools: &[&str]) -> Option<String> {
        install_command_with(&Path::new("/home/v/Downloads").join(name), fam, &|t| tools.contains(&t))
    }

    #[test]
    fn native_packages() {
        assert_eq!(cmd("yay-1.pkg.tar.zst", "arch", &[]).unwrap(), "sudo pacman -U /home/v/Downloads/yay-1.pkg.tar.zst");
        assert_eq!(cmd("a b.deb", "debian", &[]).unwrap(), "sudo apt install '/home/v/Downloads/a b.deb'");
        assert_eq!(cmd("c.rpm", "rpm", &["dnf"]).unwrap(), "sudo dnf install /home/v/Downloads/c.rpm");
        assert_eq!(cmd("c.rpm", "rpm", &["zypper"]).unwrap(), "sudo zypper install /home/v/Downloads/c.rpm");
    }

    /// Vini: sudo asked inside the terminal, and the install could fail
    /// there. With pkexec, Sonata's own prompt asks first.
    #[test]
    fn password_asked_first() {
        assert_eq!(cmd("yay-1.pkg.tar.zst", "arch", &["pkexec"]).unwrap(), "pkexec pacman -U /home/v/Downloads/yay-1.pkg.tar.zst");
        assert_eq!(cmd("a.deb", "debian", &["pkexec"]).unwrap(), "pkexec apt install /home/v/Downloads/a.deb");
        assert_eq!(cmd("x.snap", "debian", &["snap", "pkexec"]).unwrap(), "pkexec snap install --dangerous /home/v/Downloads/x.snap");
        assert!(cmd("a.deb", "arch", &["debtap", "pkexec"]).unwrap().ends_with("pkexec pacman -U \"$(ls -t -- *.pkg.tar.zst | head -n 1)\""));
    }

    /// Paths with spaces, quotes and shell metacharacters stay one argument
    /// (no shell injection when the terminal runs the command).
    #[test]
    fn quotes_in_names() {
        assert_eq!(cmd("it's (1).pkg.tar.zst", "arch", &[]).unwrap(), "sudo pacman -U '/home/v/Downloads/it'\"'\"'s (1).pkg.tar.zst'");
        let c = install_command_with(Path::new("/tmp/a b;rm -rf ~ $(id)'x.pkg.tar.zst"), "arch", &|_| false).unwrap();
        assert_eq!(c, "sudo pacman -U '/tmp/a b;rm -rf ~ $(id)'\"'\"'x.pkg.tar.zst'");
        let d = debtap_command(Path::new("/tmp/d ir/$(x).deb"), "sudo");
        assert!(d.starts_with("cd '/tmp/d ir' && debtap -Q '/tmp/d ir/$(x).deb' && "));
    }

    #[test]
    fn foreign_packages_refused() {
        assert_eq!(cmd("c.rpm", "arch", &[]), None);
        assert_eq!(cmd("a.pkg.tar.zst", "debian", &[]), None);
        assert_eq!(cmd("a.deb", "arch", &[]), None); // no debtap
        assert!(cmd("a.deb", "arch", &["debtap"]).unwrap().contains("debtap -Q"));
    }

    #[test]
    fn flatpak_snap() {
        assert_eq!(cmd("x.flatpakref", "arch", &["flatpak"]).unwrap(), "flatpak install --user /home/v/Downloads/x.flatpakref");
        assert_eq!(cmd("x.flatpak", "arch", &["flatpak"]).unwrap(), "flatpak install --user --bundle /home/v/Downloads/x.flatpak");
        assert_eq!(cmd("x.flatpak", "arch", &[]), None);
        assert_eq!(cmd("x.snap", "debian", &["snap"]).unwrap(), "sudo snap install --dangerous /home/v/Downloads/x.snap");
    }

    #[test]
    fn install_runs_in_a_terminal_or_explains() {
        let plan = |name: &str, fam: &str, tools: &[&str]| install_plan(&Path::new("/d").join(name), fam, &|t| tools.contains(&t));
        assert_eq!(plan("yay.pkg.tar.zst", "arch", &[]), Plan::Run("sudo pacman -U /d/yay.pkg.tar.zst".into()));
        // a .deb on Arch: debtap offered, through the AUR helper there is
        let Plan::OfferDebtap(c) = plan("discord.deb", "arch", &["paru"]) else { panic!() };
        assert!(c.contains("paru -S debtap") && c.contains("debtap -Q /d/discord.deb"), "{c}");
        for (name, fam, words) in [("c.rpm", "arch", "Fedora"), ("a.deb", "arch", "Debian"), ("x.flatpak", "arch", "Flatpak isn't installed"), ("x.snap", "arch", "Snap isn't installed")] {
            let Plan::Explain(body) = plan(name, fam, &[]) else { panic!("{name}") };
            assert!(body.contains(words), "{name}: {body}");
        }
        let Plan::Explain(body) = plan("c.rpm", "arch", &[]) else { panic!() };
        assert!(body.contains("This computer installs packages for Arch Linux"), "{body}");
    }

    // -- archives ---------------------------------------------------------------------------------

    fn which_of(dir: &Path, names: &[&str]) -> Which {
        let (d, n): (PathBuf, Vec<String>) = (dir.to_path_buf(), names.iter().map(|s| s.to_string()).collect());
        Arc::new(move |t| n.iter().any(|x| x == t).then(|| d.join(t)))
    }

    fn fake_tool(dir: &Path, name: &str, script: &str) {
        let p = dir.join(name);
        std::fs::write(&p, format!("#!/bin/sh\n{script}")).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(0o755)).unwrap();
    }

    #[test]
    fn rar_folder_and_tool_order() {
        let d = tempfile::tempdir().unwrap();
        let rar = d.path().join("Fotos.rar");
        std::fs::write(&rar, "").unwrap();
        assert_eq!(extract_dir(&rar), d.path().join("Fotos"));
        assert_eq!(actions(&rar), [Action::Extract]);
        let t = Path::new("/t");
        let w = |n: &[&str]| which_of(d.path(), n);
        assert!(tool_command(&rar, t, None, &w(&["bsdtar", "unrar"])).unwrap()[0].ends_with("unrar"));
        assert_eq!(tool_command(&rar, t, None, &w(&["bsdtar"])).unwrap()[1..], ["-xf", &rar.to_string_lossy(), "-C", "/t"]);
        let argv = tool_command(&d.path().join("a.7z"), t, None, &w(&["7z", "bsdtar"])).unwrap();
        assert!(argv[0].ends_with("7z") && argv.contains(&"-o/t".to_string()));
        assert_eq!(tool_command(Path::new("a.7z"), t, None, &w(&["unrar"])), None); // unrar can't do 7z
    }

    #[test]
    fn tools_never_wait_for_a_password() {
        let d = tempfile::tempdir().unwrap();
        let rar = d.path().join("Fotos.rar");
        let t = Path::new("/t");
        assert!(tool_command(&rar, t, None, &which_of(d.path(), &["unrar"])).unwrap().contains(&"-p-".to_string()));
        assert!(tool_command(&rar, t, Some("abc"), &which_of(d.path(), &["unrar"])).unwrap().contains(&"-pabc".to_string()));
        assert!(tool_command(&rar, t, None, &which_of(d.path(), &["7z"])).unwrap().contains(&"-p".to_string()));
    }

    #[test]
    fn rar_extracts_and_reports() {
        let (bin, d) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        let rar = d.path().join("Fotos.rar");
        std::fs::write(&rar, "").unwrap();
        let target = d.path().join("Fotos");
        std::fs::create_dir(&target).unwrap();
        let w = which_of(bin.path(), &["bsdtar"]);
        fake_tool(bin.path(), "bsdtar", "touch \"$4/inside.txt\"\n");
        run_tool(&rar, &target, None, &w).unwrap();
        assert!(target.join("inside.txt").exists());
        fake_tool(bin.path(), "bsdtar", "echo \"Encrypted file is unsupported\" >&2; exit 1\n");
        assert_eq!(run_tool(&rar, &target, None, &w), Err(ExtractError::Password { wrong: false }));
        let Err(ExtractError::Failed(m)) = run_tool(&rar, &target, None, &which_of(bin.path(), &[])) else { panic!() };
        assert!(m.contains("7-Zip"));
    }

    /// Vini: a RAR with a password didn't ask for it, only showed an error
    /// (bsdtar says "Passphrase required", not "password").
    #[test]
    fn rar_password_asks_then_retries_with_it() {
        let (bin, d) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        let rar = d.path().join("Fotos.rar");
        std::fs::write(&rar, "").unwrap();
        fake_tool(bin.path(), "bsdtar", "if [ \"$3\" = \"--passphrase\" ] && [ \"$4\" = \"segredo\" ]; then\n  touch \"$6/ok.txt\"; exit 0; fi\nif [ \"$3\" = \"--passphrase\" ]; then echo \"Incorrect passphrase\" >&2; exit 1; fi\necho \"Passphrase required for this entry\" >&2; exit 1\n");
        let ctx = glib::MainContext::new();
        let _own = ctx.acquire().unwrap();
        ctx.with_thread_default(|| {
            let asked: Rc<std::cell::RefCell<Vec<bool>>> = Rc::default();
            let done: Rc<std::cell::RefCell<Option<PathBuf>>> = Rc::default();
            let a = asked.clone();
            let ask: Asker = Rc::new(move |_, wrong, then| {
                a.borrow_mut().push(wrong);
                then(if a.borrow().len() == 1 { "errada".into() } else { "segredo".into() });
            });
            let dn = done.clone();
            extract_with(rar.clone(), None, which_of(bin.path(), &["bsdtar"]), ask, Rc::new(|_, e| panic!("{e}")), Some(Rc::new(move |t| *dn.borrow_mut() = Some(t))));
            let end = std::time::Instant::now() + std::time::Duration::from_secs(5);
            while done.borrow().is_none() && std::time::Instant::now() < end {
                ctx.iteration(false);
            }
            assert_eq!(*asked.borrow(), [false, true]); // asked, then "the password is incorrect"
            assert!(done.borrow().as_ref().unwrap().join("ok.txt").exists());
        })
        .unwrap();
    }

    fn sys() -> Which {
        system_which()
    }

    /// The archive "My Files.<kind>" of inner/a.txt ("hello"), or None
    /// when the tool to make it isn't there.
    fn make(d: &Path, kind: &str, password: Option<&str>) -> Option<PathBuf> {
        let src = d.join("src");
        std::fs::create_dir_all(src.join("inner")).unwrap();
        std::fs::write(src.join("inner").join("a.txt"), "hello").unwrap();
        let out = d.join(format!("My Files.{kind}"));
        let o = out.to_string_lossy().into_owned();
        let ok = if kind == "zip" && password.is_none() {
            let f = std::fs::File::create(&out).unwrap();
            let mut z = zip::ZipWriter::new(f);
            z.start_file("inner/a.txt", zip::write::SimpleFileOptions::default()).unwrap();
            std::io::Write::write_all(&mut z, b"hello").unwrap();
            z.finish().unwrap();
            true
        } else if kind == "zip" || kind == "7z" {
            has_tool("7z") && {
                let mut args = vec!["a".to_string(), "-bso0".into(), "-bsp0".into()];
                if kind == "zip" {
                    args.push("-tzip".into());
                }
                if let Some(p) = password {
                    args.push(format!("-p{p}"));
                }
                args.extend([o, "inner".into()]);
                std::process::Command::new("7z").args(&args).current_dir(&src).status().unwrap().success()
            }
        } else {
            let flag = match kind {
                "tar.gz" => Some("-z"),
                "tar.xz" => Some("-J"),
                "tar.bz2" => Some("-j"),
                "tar.zst" => Some("--zstd"),
                _ => None,
            };
            if kind == "tar.zst" && !has_tool("zstd") {
                return None;
            }
            let mut args: Vec<String> = flag.map(String::from).into_iter().collect();
            args.extend(["-cf".into(), o, "-C".into(), src.to_string_lossy().into_owned(), "inner".into()]);
            has_tool("tar") && std::process::Command::new("tar").args(&args).status().unwrap().success()
        };
        ok.then_some(out)
    }

    #[test]
    fn every_archive() {
        for kind in ["zip", "tar.gz", "tar.xz", "tar.bz2", "tar.zst", "tar", "7z"] {
            let d = tempfile::tempdir().unwrap();
            let Some(arc) = make(d.path(), kind, None) else { continue };
            let target = extract_dir(&arc);
            assert_eq!(target.file_name().unwrap(), "My Files", "{kind}");
            extract_into(&arc, &target, None, &sys()).unwrap_or_else(|e| panic!("{kind}: {e:?}"));
            assert_eq!(std::fs::read_to_string(target.join("inner").join("a.txt")).unwrap(), "hello", "{kind}");
            assert_eq!(extract_dir(&arc).file_name().unwrap(), "My Files 2"); // a second copy beside
        }
    }

    #[test]
    fn password_archives() {
        for kind in ["zip", "7z"] {
            let d = tempfile::tempdir().unwrap();
            let Some(arc) = make(d.path(), kind, Some("s3cret")) else { continue };
            let target = extract_dir(&arc);
            assert_eq!(extract_into(&arc, &target, None, &sys()), Err(ExtractError::Password { wrong: false }), "{kind}");
            assert!(!target.exists(), "{kind}"); // nothing half-made
            assert_eq!(extract_into(&arc, &target, Some("wrong"), &sys()), Err(ExtractError::Password { wrong: true }), "{kind}");
            assert!(!target.exists(), "{kind}");
            extract_into(&arc, &target, Some("s3cret"), &sys()).unwrap();
            assert_eq!(std::fs::read_to_string(target.join("inner").join("a.txt")).unwrap(), "hello", "{kind}");
        }
    }

    /// A broken archive says so and leaves no empty folder behind.
    #[test]
    fn broken_archive_says_so() {
        let d = tempfile::tempdir().unwrap();
        let bad = d.path().join("broken.zip");
        std::fs::write(&bad, "not a zip").unwrap();
        assert!(matches!(extract_into(&bad, &extract_dir(&bad), None, &sys()), Err(ExtractError::Failed(_))));
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 1);
    }

    /// Archive members with "../" never land outside the target folder.
    #[test]
    fn extract_blocks_path_traversal() {
        let d = tempfile::tempdir().unwrap();
        let inner = d.path().join("in");
        std::fs::create_dir(&inner).unwrap();
        let z = inner.join("evil.zip");
        let mut w = zip::ZipWriter::new(std::fs::File::create(&z).unwrap());
        w.start_file("../escaped-zip.txt", zip::write::SimpleFileOptions::default()).unwrap();
        std::io::Write::write_all(&mut w, b"x").unwrap();
        w.finish().unwrap();
        let _ = extract_into(&z, &extract_dir(&z), None, &sys());
        assert!(!d.path().join("escaped-zip.txt").exists() && !inner.join("escaped-zip.txt").exists());
        if has_tool("tar") {
            std::fs::write(d.path().join("payload.txt"), "x").unwrap();
            let t = inner.join("evil.tar");
            // GNU tar keeps "../" with -P
            let ok = std::process::Command::new("tar").args(["-P", "-cf", &t.to_string_lossy(), "-C", &inner.to_string_lossy(), "../payload.txt"]).status().unwrap().success();
            if ok {
                std::fs::remove_file(d.path().join("payload.txt")).unwrap();
                let _ = extract_into(&t, &extract_dir(&t), None, &sys());
                assert!(!d.path().join("payload.txt").exists());
            }
        }
    }

    /// An archive that breaks half-way leaves no half-extracted folder.
    #[test]
    fn extract_failure_removes_partial_folder() {
        if !has_tool("tar") {
            return;
        }
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("one.txt"), "1".repeat(4000)).unwrap();
        std::fs::write(d.path().join("two.txt"), "2".repeat(40000)).unwrap();
        let t = d.path().join("half.tar");
        assert!(std::process::Command::new("tar").args(["-cf", &t.to_string_lossy(), "-C", &d.path().to_string_lossy(), "one.txt", "two.txt"]).status().unwrap().success());
        std::fs::OpenOptions::new().write(true).open(&t).unwrap().set_len(12000).unwrap(); // one.txt complete, two.txt cut
        assert!(matches!(extract_into(&t, &d.path().join("half"), None, &sys()), Err(ExtractError::Failed(_))));
        assert!(!d.path().join("half").exists());
    }

    // -- scripts ----------------------------------------------------------------------------------

    fn file(d: &Path, name: &str, text: &str, mode: u32) -> PathBuf {
        let p = d.join(name);
        std::fs::write(&p, text).unwrap();
        std::fs::set_permissions(&p, std::fs::Permissions::from_mode(mode)).unwrap();
        p
    }

    #[test]
    fn which_files_are_scripts() {
        let d = tempfile::tempdir().unwrap();
        let d = d.path();
        assert!(is_script(&file(d, "a.sh", "echo hi\n", 0o644)));
        assert!(is_script(&file(d, "b.BASH", "echo hi\n", 0o644)));
        assert!(is_script(&file(d, "tool", "#!/usr/bin/env python3\nprint(1)\n", 0o755)));
        assert!(!is_script(&file(d, "notes", "#!/bin/sh\n", 0o644))); // not executable, no suffix
        assert!(!is_script(&file(d, "data.bin", "\x7fELF", 0o755))); // a program, not a script
        assert!(!is_script(&file(d, "readme.txt", "hello", 0o644)));
        assert!(!is_script(d));
        assert!(!is_script(Path::new("")));
    }

    #[test]
    fn how_scripts_run() {
        let d = tempfile::tempdir().unwrap();
        let d = d.path();
        let exe = file(d, "x.sh", "echo\n", 0o755);
        assert_eq!(script_argv(&exe), [exe.to_string_lossy()]); // executable: itself
        let plain = file(d, "y.sh", "echo\n", 0o644);
        assert_eq!(script_argv(&plain), ["bash".to_string(), plain.to_string_lossy().into_owned()]); // no chmod +x needed
        let bang = file(d, "z.sh", "#!/usr/bin/env python3\n", 0o644);
        assert_eq!(script_argv(&bang), ["/usr/bin/env".to_string(), "python3".into(), bang.to_string_lossy().into_owned()]); // its "#!" line
        assert_eq!(script_argv(&file(d, "w.zsh", "echo\n", 0o644))[0], "zsh");
    }

    /// Vini: double-clicking a .sh in Files should run it (it opened in the
    /// text editor). It runs in a terminal, in its own folder; other files
    /// -- an executable Python script too -- are opened as before.
    #[test]
    fn double_click_on_a_sh_did_not_run_it() {
        let d = tempfile::tempdir().unwrap();
        let d = d.path();
        let sh = file(d, "go.sh", "echo hi\n", 0o644);
        let py = file(d, "tool.py", "#!/usr/bin/env python3\nprint(1)\n", 0o755);
        assert!(script_suffix(&sh).is_some() && is_script(&sh));
        assert!(script_suffix(&py).is_none()); // opened in its app, not run
        assert_eq!(actions(&sh), [Action::RunInTerminal, Action::Run]);
        assert_eq!(actions(&file(d, "a.txt", "x", 0o644)), []);
        assert_eq!(script_command(&sh), format!("cd {} && bash {}", quote(&d.to_string_lossy()), quote(&sh.to_string_lossy()))); // in its own folder
    }

    #[test]
    fn script_runs_in_the_background_in_its_folder() {
        let d = tempfile::tempdir().unwrap();
        let out = d.path().join("out.txt");
        let sh = file(d.path(), "w.sh", &format!("pwd > '{}'\n", out.display()), 0o644);
        run_script(&sh, None, false);
        let end = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while std::fs::metadata(&out).map(|m| m.len()).unwrap_or(0) == 0 && std::time::Instant::now() < end {
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        assert_eq!(std::fs::read_to_string(&out).unwrap().trim(), std::fs::canonicalize(d.path()).unwrap().to_string_lossy());
    }

    /// Files, Spotlight and the Dock stacks open packages through here.
    #[test]
    fn opened_from_files() {
        let w = include_str!("window.rs");
        assert!(w.contains("crate::packages::open_path(") && w.contains("crate::packages::menu_items("));
    }
}
