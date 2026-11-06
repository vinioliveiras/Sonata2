//! Sonata's own updates, from its GitHub releases (backend/selfupdate.py).
//!
//! latest_release() asks GitHub; newer(tag, current) says whether it is an
//! update; `sonata2 self-update [--check] [TAG]` (main()) downloads that
//! release, runs its install.sh -- which builds Sonata with cargo and
//! installs it over this one, carrying settings and data over -- and
//! restarts Sonata. Settings > About > Software Update lists it with the
//! system's updates. A clone updates with `git pull` instead.
//!
//! A release is Sonata's source (GitHub's tarball, no prebuilt binaries):
//! cargo is needed to update. install.sh checks for it before it changes
//! anything, and a build that fails leaves the installed Sonata as it was.
//! The build is kept between updates ($XDG_CACHE_HOME/sonata2/rust-target):
//! each release is a new folder, and building all of it from nothing every
//! time takes many minutes.
//!
//! The download goes through curl (no HTTP stack in Sonata's Rust build);
//! the archive is read here, keeping only plain files and folders inside
//! the destination.

use serde_json::{json, Value};
use std::io::Read;
use std::path::{Path, PathBuf};

pub const REPO: &str = "vinioliveiras/sonata2";
pub const NAME: &str = "selfupdate";

pub fn api() -> String {
    format!("https://api.github.com/repos/{REPO}/releases?per_page=10")
}

pub fn defaults() -> Value {
    json!({"notified": "", "last_check": 0})
}

/// Sonata's version (the VERSION file this build was made from: the
/// binary is the install, so they are the same).
pub fn version() -> String {
    sonata_config::version().to_string()
}

/// 'v0.2.0-beta.1' -> (0, 2, 0, 2, 1): numbers, then the pre-release kind
/// and its number (a final release sorts after its pre-releases).
pub fn parse_version(tag: &str) -> (u64, u64, u64, u64, u64) {
    let t = tag.trim().trim_start_matches(['v', 'V']);
    let (main, pre) = t.split_once('-').unwrap_or((t, ""));
    static NUM: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let num = NUM.get_or_init(|| regex::Regex::new(r"\d+").unwrap());
    let mut n: Vec<u64> = num.find_iter(main).take(3).map(|m| m.as_str().parse().unwrap_or(0)).collect();
    n.resize(3, 0);
    if pre.is_empty() {
        return (n[0], n[1], n[2], 4, 0);
    }
    static PRE: std::sync::OnceLock<regex::Regex> = std::sync::OnceLock::new();
    let m = PRE.get_or_init(|| regex::Regex::new(r"^([a-zA-Z]+)\.?(\d*)").unwrap()).captures(pre);
    let kind = m.as_ref().map(|m| match m[1].to_lowercase().as_str() {
        "alpha" | "a" => 1,
        "beta" | "b" => 2,
        "rc" => 3,
        _ => 0, // dev, or unknown
    });
    let nr = m.as_ref().and_then(|m| m[2].parse().ok()).unwrap_or(0);
    (n[0], n[1], n[2], kind.unwrap_or(0), nr)
}

pub fn newer(tag: &str, current: &str) -> bool {
    parse_version(tag) > parse_version(current)
}

#[derive(Clone, Debug, PartialEq)]
pub struct Release {
    pub tag: String,
    pub name: String,
    pub body: String,
    pub url: String,
    pub tarball: String,
}

/// The newest published release (drafts left out; pre-releases count:
/// Sonata is in alpha).
pub fn pick(releases: &Value) -> Option<Release> {
    let best = releases.as_array()?.iter().filter(|r| !r.get("draft").and_then(Value::as_bool).unwrap_or(false) && r.get("tag_name").and_then(Value::as_str).is_some_and(|t| !t.is_empty())).fold(None::<&Value>, |best, r| {
        let tag = |x: &Value| parse_version(x["tag_name"].as_str().unwrap_or(""));
        match best {
            Some(b) if tag(r) <= tag(b) => Some(b),
            _ => Some(r),
        }
    })?;
    let s = |k: &str| best.get(k).and_then(Value::as_str).filter(|v| !v.is_empty()).map(String::from);
    let tag = s("tag_name")?;
    Some(Release {
        name: s("name").unwrap_or_else(|| tag.clone()),
        body: s("body").unwrap_or_default(),
        url: s("html_url").unwrap_or_else(|| format!("https://github.com/{REPO}/releases")),
        tarball: s("tarball_url").unwrap_or_else(|| format!("https://api.github.com/repos/{REPO}/tarball/{tag}")),
        tag,
    })
}

/// Ask GitHub (blocking: run it in a thread). None when it can't.
pub fn latest_release(timeout_s: u64) -> Option<Release> {
    let ua = format!("User-Agent: sonata2/{}", version());
    let (rc, out) = crate::run_full(&["curl", "-fsSL", "--max-time", &timeout_s.to_string(), "-H", "Accept: application/vnd.github+json", "-H", &ua, &api()], None, &[], timeout_s + 2);
    if rc != 0 {
        return None;
    }
    pick(&serde_json::from_str(&out).ok()?)
}

/// The newer release, None when up to date or unknown.
pub fn available(timeout_s: u64) -> Option<Release> {
    latest_release(timeout_s).filter(|r| newer(&r.tag, &version()))
}

// -- where Sonata runs from -------------------------------------------------------------------
/// Sonata's folder: the one install.sh puts next to this binary
/// (`<prefix>/share/sonata2-rs`; a --dev install's is a link to the clone,
/// followed), else the clone this build was made from.
pub fn install_root() -> PathBuf {
    let installed = std::env::current_exe().ok().and_then(|e| Some(e.parent()?.parent()?.join("share").join("sonata2-rs")));
    install_root_from(installed.as_deref(), &Path::new(env!("CARGO_MANIFEST_DIR")).join("../../.."))
}

pub fn install_root_from(installed: Option<&Path>, clone: &Path) -> PathBuf {
    let real = |p: &Path| p.canonicalize().unwrap_or_else(|_| p.to_path_buf());
    match installed.filter(|p| p.join("tools").is_dir()) {
        Some(p) => real(p),
        None => real(clone),
    }
}

pub fn is_clone(root: &Path) -> bool {
    root.join(".git").exists()
}

/// How to start Sonata again: this binary.
pub fn self_argv() -> Vec<String> {
    vec![std::env::current_exe().map(|p| p.display().to_string()).unwrap_or_else(|_| "sonata2".into())]
}

/// install.sh's options for an update: the same kind of install, no
/// questions, the login screen as it is (kept up to date when it's Sonata's).
pub fn install_args(root: &str, greeter: bool) -> Vec<&'static str> {
    let mut args = vec!["--yes"];
    if root.starts_with("/usr/") {
        args.push("--system");
    }
    if !greeter {
        args.push("--no-greeter"); // never set up one the user didn't choose
    }
    args
}

fn greeter_installed() -> bool {
    Path::new("/usr/local/bin/sonata-greeter").exists()
}

/// Python's shlex.quote
pub fn quote(s: &str) -> String {
    if !s.is_empty() && s.chars().all(|c| c.is_ascii_alphanumeric() || "@%+=:,./-_".contains(c)) {
        return s.into();
    }
    format!("'{}'", s.replace('\'', "'\"'\"'"))
}

/// What Software Update runs in a terminal (asks for sudo there).
pub fn terminal_command(tag: &str) -> String {
    let root = install_root();
    terminal_command_for(tag, &root.to_string_lossy(), is_clone(&root), &self_argv())
}

pub fn terminal_command_for(tag: &str, root: &str, clone: bool, argv: &[String]) -> String {
    let me: Vec<String> = argv.iter().map(|a| quote(a)).collect();
    let me = me.join(" ");
    if clone {
        return format!("git -C {} pull --ff-only && {me} restart", quote(root));
    }
    // a git tag may hold ; $ ` -- one quoted word, never shell code
    if tag.is_empty() { format!("{me} self-update") } else { format!("{me} self-update {}", quote(tag)) }
}

// -- updating ---------------------------------------------------------------------------------
pub fn cache() -> PathBuf {
    let base = std::env::var_os("XDG_CACHE_HOME").filter(|d| !d.is_empty()).map(PathBuf::from).unwrap_or_else(|| glib::home_dir().join(".cache"));
    let d = base.join("sonata2").join("updates");
    let _ = std::fs::create_dir_all(&d);
    d
}

fn bad(msg: &str) -> std::io::Error {
    std::io::Error::other(msg.to_string())
}

/// A relative path that stays inside the destination (no "/", no "..").
fn safe(name: &str) -> Option<PathBuf> {
    let p = Path::new(name);
    p.components().all(|c| matches!(c, std::path::Component::Normal(_) | std::path::Component::CurDir)).then(|| p.to_path_buf()).filter(|p| p.components().count() > 0)
}

fn octal(field: &[u8]) -> u64 {
    let s = String::from_utf8_lossy(field);
    u64::from_str_radix(s.trim_matches(|c: char| c == '\0' || c == ' '), 8).unwrap_or(0)
}

fn cstr(field: &[u8]) -> String {
    String::from_utf8_lossy(field.split(|b| *b == 0).next().unwrap_or(&[])).into_owned()
}

/// `path` with "." and ".." taken out and every symbolic link already on
/// disk followed (os.path.realpath, non-strict: what doesn't exist yet is
/// kept as written). An archive's links are checked against where they
/// really lead: "x/../.." through a link x reaches what the words don't say.
pub fn realpath(path: &Path) -> PathBuf {
    fn walk(path: &Path, depth: u32) -> PathBuf {
        let mut out = PathBuf::from("/");
        for c in path.components() {
            match c {
                std::path::Component::Normal(n) => {
                    let next = out.join(n);
                    match std::fs::read_link(&next) {
                        Ok(t) if depth < 40 => out = walk(&out.join(t), depth + 1),
                        _ => out = next,
                    }
                }
                std::path::Component::ParentDir => {
                    out.pop();
                }
                _ => {}
            }
        }
        out
    }
    let abs = if path.is_absolute() { path.to_path_buf() } else { std::env::current_dir().unwrap_or_default().join(path) };
    walk(&abs, 0)
}

fn inside(path: &Path, base: &Path) -> bool {
    path.starts_with(base)
}

/// A .tar.gz into `dest`: only what stays inside it, as tarfile's "data"
/// filter (the Python Sonata extracts with it) -- plain files and folders,
/// and links whose target is inside too (no absolute ones). The links
/// matter: the icon themes are ~16,000 of them (one artwork, many names),
/// and with links left out an update installed them without most icons.
/// Devices, fifos and anything leading outside are left out.
pub fn extract(archive: &Path, dest: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut data = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(archive)?).read_to_end(&mut data)?;
    let base = realpath(dest);
    let (mut off, mut long_name, mut long_link): (usize, Option<String>, Option<String>) = (0, None, None);
    while off + 512 <= data.len() {
        let h = &data[off..off + 512];
        if h.iter().all(|b| *b == 0) {
            break;
        }
        let size = octal(&h[124..136]) as usize;
        let kind = h[156];
        let body = off + 512;
        let end = body + size;
        if end > data.len() {
            return Err(bad("a truncated archive"));
        }
        off = body + size.div_ceil(512) * 512;
        let mut name = cstr(&h[0..100]);
        if &h[257..262] == b"ustar" {
            let prefix = cstr(&h[345..500]);
            if !prefix.is_empty() {
                name = format!("{prefix}/{name}");
            }
        }
        let mut link = cstr(&h[157..257]);
        if matches!(kind, b'L' | b'K' | b'x') {
            // GNU long name / long link name, pax header (path=, linkpath=):
            // they name the next member
            match kind {
                b'L' => long_name = Some(cstr(&data[body..end])),
                b'K' => long_link = Some(cstr(&data[body..end])),
                _ => {
                    let text = String::from_utf8_lossy(&data[body..end]);
                    for l in text.lines() {
                        let kv = l.split_once(' ').map_or("", |(_, kv)| kv);
                        if let Some(v) = kv.strip_prefix("path=") {
                            long_name = Some(v.to_string());
                        } else if let Some(v) = kv.strip_prefix("linkpath=") {
                            long_link = Some(v.to_string());
                        }
                    }
                }
            }
            continue;
        }
        if let Some(n) = long_name.take() {
            name = n;
        }
        if let Some(l) = long_link.take() {
            link = l;
        }
        if !matches!(kind, b'0' | 0 | b'5' | b'1' | b'2') {
            continue; // devices, fifos, global pax headers: left out
        }
        let Some(rel) = safe(&name) else { continue };
        let target = dest.join(&rel);
        let parent = target.parent().unwrap_or(dest).to_path_buf();
        // through a link extracted before, the folder may lead elsewhere
        if !inside(&realpath(&parent), &base) {
            continue;
        }
        if kind == b'5' {
            if inside(&realpath(&target), &base) {
                std::fs::create_dir_all(&target)?;
            }
            continue;
        }
        std::fs::create_dir_all(&parent)?;
        if std::fs::symlink_metadata(&target).is_ok_and(|m| !m.is_dir()) {
            std::fs::remove_file(&target)?; // a later member replaces it, never written through
        }
        match kind {
            b'2' => {
                let to = Path::new(&link);
                if link.is_empty() || to.is_absolute() || !inside(&realpath(&realpath(&parent).join(to)), &base) {
                    continue;
                }
                std::os::unix::fs::symlink(to, &target)?;
            }
            b'1' => {
                // a hard link names another member: its content, copied
                let Some(from) = safe(&link).map(|l| dest.join(l)) else { continue };
                if !inside(&realpath(&from), &base) || !from.is_file() {
                    continue;
                }
                std::fs::copy(&from, &target)?;
            }
            _ => {
                std::fs::write(&target, &data[body..end])?;
                let mode = octal(&h[100..108]) as u32 & 0o755;
                let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode(mode | 0o600));
            }
        }
    }
    Ok(())
}

/// The cache folder for `tag`: one plain name. A tag comes from GitHub or
/// the command line, and the folder is emptied first -- "../.." there would
/// have emptied a folder outside the cache.
pub fn folder_name(tag: &str) -> String {
    let name: String = tag.chars().map(|c| if c.is_ascii_alphanumeric() || "._-+".contains(c) { c } else { '_' }).collect();
    if name.is_empty() || name.chars().all(|c| c == '.') { format!("_{name}") } else { name }
}

/// The release's source in the cache; returns the folder with install.sh.
pub fn download(rel: &Release, say: &dyn Fn(&str)) -> std::io::Result<PathBuf> {
    download_to(rel, &cache(), say)
}

pub fn download_to(rel: &Release, cache: &Path, say: &dyn Fn(&str)) -> std::io::Result<PathBuf> {
    let dest = cache.join(folder_name(&rel.tag));
    let _ = std::fs::remove_dir_all(&dest);
    std::fs::create_dir_all(&dest)?;
    let archive = PathBuf::from(format!("{}.tar.gz", dest.display()));
    say(&format!("Downloading Sonata {}…", rel.tag));
    let ua = format!("User-Agent: sonata2/{}", version());
    let (rc, _) = crate::run_full(&["curl", "-fsSL", "--max-time", "60", "-H", &ua, "-o", &archive.to_string_lossy(), &rel.tarball], None, &[], 65);
    if rc != 0 {
        return Err(bad("couldn't download the release"));
    }
    let r = extract(&archive, &dest);
    let _ = std::fs::remove_file(&archive);
    r?;
    let top: Vec<PathBuf> = std::fs::read_dir(&dest)?.flatten().map(|e| e.path()).filter(|p| p.is_dir()).collect();
    let folder = if top.len() == 1 { top[0].clone() } else { dest };
    if !folder.join("install.sh").exists() {
        return Err(bad("the release has no install.sh"));
    }
    Ok(folder)
}

/// The line Software Update's list reads (parse_arrow): "Sonata A -> B".
pub fn check_line(current: &str, rel: &Release) -> String {
    format!("Sonata {current} -> {}", rel.tag.trim_start_matches(['v', 'V']))
}

/// The cargo build kept between updates ($XDG_CACHE_HOME/sonata2/rust-target).
pub fn build_cache() -> PathBuf {
    cache().parent().map_or_else(|| cache().join("rust-target"), |c| c.join("rust-target"))
}

/// What an update does, each step a seam (the tests run it all against a
/// local server and record the commands).
pub struct Steps<'a> {
    pub current: String,
    /// Sonata's folder (install_root)
    pub root: PathBuf,
    pub greeter: bool,
    pub latest: &'a dyn Fn() -> Option<Release>,
    pub download: &'a dyn Fn(&Release) -> std::io::Result<PathBuf>,
    /// a command (argv) in a folder: its exit code
    pub run: &'a dyn Fn(&[String], &Path) -> i32,
    /// how to restart Sonata (self_argv + "restart")
    pub restart: Vec<String>,
    /// the cargo build kept between updates
    pub target: PathBuf,
    pub say: &'a dyn Fn(&str),
}

/// `sonata2 self-update [TAG]` (all but --check): download the release,
/// check it is one, run its install.sh, update the Rust build, restart.
pub fn update(args: &[String], s: &Steps) -> i32 {
    let Some(mut rel) = (s.latest)() else {
        (s.say)("Sonata: couldn't reach GitHub (offline?)");
        return 1;
    };
    let tag = args.first().cloned().unwrap_or_else(|| rel.tag.clone());
    if tag != rel.tag {
        rel.tarball = format!("https://api.github.com/repos/{REPO}/tarball/{tag}");
        rel.tag = tag.clone();
    }
    if args.is_empty() && !newer(&tag, &s.current) {
        (s.say)(&format!("Sonata {} is up to date.", s.current));
        return 0;
    }
    if is_clone(&s.root) {
        (s.say)("This Sonata runs from a git clone: update it with git pull.");
        return 2;
    }
    let folder = match (s.download)(&rel) {
        Ok(f) => f,
        Err(e) => {
            (s.say)(&format!("Sonata: {e}"));
            return 1;
        }
    };
    (s.say)(&format!("Building and installing Sonata {tag}…"));
    // (install.sh builds with cargo: into the build kept from the last
    //  update, so only what changed is compiled)
    let _ = std::fs::create_dir_all(&s.target);
    let mut argv = vec!["env".to_string(), format!("CARGO_TARGET_DIR={}", s.target.display()), "bash".into(), folder.join("install.sh").to_string_lossy().into_owned()];
    argv.extend(install_args(&s.root.to_string_lossy(), s.greeter).into_iter().map(String::from));
    let code = (s.run)(&argv, &folder);
    if code != 0 {
        (s.say)(&format!("The installer stopped (exit {code}). Sonata wasn't changed."));
        return code;
    }
    let _ = sonata_config::update(NAME, &json!({"notified": tag}));
    (s.run)(&s.restart, &s.root);
    (s.say)(&format!("Sonata {tag} is installed."));
    0
}

fn run_status(argv: &[String], cwd: &Path) -> i32 {
    std::process::Command::new(&argv[0]).args(&argv[1..]).current_dir(cwd).status().map(|s| s.code().unwrap_or(1)).unwrap_or(1)
}

/// `sonata2 self-update [--check] [TAG]`.
pub fn main(args: &[String]) -> i32 {
    let current = version();
    if args.first().map(String::as_str) == Some("--check") {
        if let Some(rel) = available(10) {
            println!("{}", check_line(&current, &rel));
        }
        return 0;
    }
    let mut restart = self_argv();
    restart.push("restart".into());
    let steps = Steps {
        current,
        root: install_root(),
        greeter: greeter_installed(),
        latest: &|| latest_release(10),
        download: &|rel| download(rel, &|t| println!("{t}")),
        run: &run_status,
        restart,
        target: build_cache(),
        say: &|t| println!("{t}"),
    };
    update(args, &steps)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn order_and_newer() {
        let order = ["v0.1.0-alpha", "0.1.0-alpha.2", "v0.1.0-beta", "v0.1.0-rc.1", "v0.1.0", "v0.1.1", "v0.2.0-alpha", "1.0.0"];
        let mut sorted = order.to_vec();
        sorted.sort_by_key(|t| parse_version(t));
        assert_eq!(sorted, order);
        assert!(newer("v0.2.0", "0.1.0-alpha"));
        assert!(!newer("v0.1.0-alpha", "0.1.0-alpha"));
        assert!(!newer("v0.0.9", "0.1.0-alpha"));
    }

    /// The same version written with v/V, spaces or an explicit .0 must
    /// never be offered as an update (Sonata would ask to update to itself).
    #[test]
    fn same_version_is_not_an_update() {
        for tag in ["v0.19.2-alpha", "V0.19.2-alpha", " v0.19.2-alpha ", "0.19.2-alpha.0", "v0.19.2-Alpha", "0.19.2-a"] {
            assert!(!newer(tag, "0.19.2-alpha"), "{tag}");
        }
        assert!(!newer("v0.19.2", "0.19.2"));
        assert!(!newer("v0.19", "0.19.0"));
    }

    #[test]
    fn pre_releases_climb_and_numbers_compare_as_numbers() {
        assert!(newer("v0.19.2-alpha.2", "0.19.2-alpha"));
        assert!(newer("v0.19.2-alpha2", "0.19.2-alpha.1"));
        assert!(newer("v0.19.2-beta", "0.19.2-alpha.9"));
        assert!(newer("v0.19.2-rc.1", "0.19.2-beta.3"));
        assert!(newer("v0.19.2", "0.19.2-rc.4"));
        assert!(!newer("v0.19.2-beta", "0.19.2"));
        assert!(!newer("v0.19.2-alpha", "0.19.2-beta"));
        assert!(newer("v0.19.10-alpha", "0.19.9-alpha"));
        assert!(newer("v0.20.0-alpha", "0.19.2"));
        assert!(newer("v1.0.0-alpha", "0.99.99"));
        assert!(!newer("v0.9.0", "0.19.2-alpha"));
        for tag in ["", "latest", "nightly"] {
            assert!(!newer(tag, "0.19.2-alpha"), "{tag}");
        }
        assert!(parse_version(&version()) > (0, 0, 0, 0, 0));
        assert!(!newer(&version(), &version()));
    }

    /// The installed build's version read "0.0.0" once (it looked for the
    /// Python package's __init__.py) and every release was offered as an
    /// update: the version is the VERSION file this build was made from.
    #[test]
    fn test_installed_build_offered_every_release() {
        assert_eq!(version(), std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../VERSION")).unwrap().trim());
        assert!(parse_version(&version()) > (0, 0, 0, 0, 0));
    }

    /// Sonata's folder: the installed one (a --dev install's link followed
    /// to the clone, which then updates with git), else the clone.
    #[test]
    fn install_root_is_the_installed_folder_else_the_clone() {
        let d = tempfile::tempdir().unwrap();
        let clone = d.path().join("clone");
        std::fs::create_dir_all(clone.join("tools")).unwrap();
        std::fs::create_dir_all(clone.join(".git")).unwrap();
        let share = d.path().join("share/sonata2-rs");
        assert_eq!(install_root_from(Some(&share), &clone), clone.canonicalize().unwrap());
        std::fs::create_dir_all(share.join("tools")).unwrap();
        assert_eq!(install_root_from(Some(&share), &clone), share.canonicalize().unwrap());
        assert!(!is_clone(&install_root_from(Some(&share), &clone)));
        let dev = d.path().join("dev/sonata2-rs");
        std::fs::create_dir_all(dev.parent().unwrap()).unwrap();
        std::os::unix::fs::symlink(&clone, &dev).unwrap();
        assert!(is_clone(&install_root_from(Some(&dev), &clone)));
    }

    #[test]
    fn pick_the_newest_published() {
        let rels = json!([{"tag_name": "v0.1.0", "name": "One"}, {"tag_name": "v0.3.0", "draft": true},
                          {"tag_name": "v0.2.0-beta", "name": "Beta", "body": "notes", "html_url": "u2"}]);
        let r = pick(&rels).unwrap();
        assert_eq!((r.tag.as_str(), r.name.as_str(), r.body.as_str(), r.url.as_str()), ("v0.2.0-beta", "Beta", "notes", "u2"));
        assert!(r.tarball.contains("tarball/v0.2.0-beta"));
        assert_eq!(pick(&json!([])), None);
        assert_eq!(pick(&json!({"message": "rate limited"})), None);
    }

    #[test]
    fn install_args_and_terminal_commands() {
        assert_eq!(install_args("/home/v/.local/share/sonata2-rs", false), ["--yes", "--no-greeter"]);
        assert_eq!(install_args("/usr/local/share/sonata2-rs", true), ["--yes", "--system"]);
        let argv = vec!["sonata2".to_string()];
        let cmd = terminal_command_for("v0.2.0", "/home/v/GitHub/sonata2", true, &argv);
        assert!(cmd.contains("git -C /home/v/GitHub/sonata2 pull --ff-only") && cmd.contains("restart"));
        assert!(terminal_command_for("v0.2.0", "/x", false, &argv).ends_with("self-update v0.2.0"));
        // Review: the GitHub tag went unquoted into a shell line
        assert!(terminal_command_for("v1;touch /tmp/x", "/x", false, &argv).ends_with("self-update 'v1;touch /tmp/x'"));
        assert_eq!(terminal_command_for("", "/home/v/my clone'x", true, &argv), "git -C '/home/v/my clone'\"'\"'x' pull --ff-only && sonata2 restart");
    }

    fn tarball(dir: &Path, members: &[(&str, &[u8])]) -> PathBuf {
        let m: Vec<(&str, u8, &str, &[u8])> = members.iter().map(|(n, d)| (*n, b'0', "", *d)).collect();
        tarball_of(dir, &m)
    }

    /// (name, kind, link target, content)
    fn tarball_of(dir: &Path, members: &[(&str, u8, &str, &[u8])]) -> PathBuf {
        let mut tar = Vec::new();
        for (name, kind, link, data) in members {
            let mut h = [0u8; 512];
            h[..name.len()].copy_from_slice(name.as_bytes());
            h[100..107].copy_from_slice(if *kind == b'0' { b"0000644" } else { b"0000755" });
            h[124..135].copy_from_slice(format!("{:011o}", data.len()).as_bytes());
            h[156] = *kind;
            h[157..157 + link.len()].copy_from_slice(link.as_bytes());
            h[148..156].copy_from_slice(b"        ");
            let sum: u32 = h.iter().map(|b| *b as u32).sum();
            h[148..155].copy_from_slice(format!("{sum:06o}\0").as_bytes());
            tar.extend_from_slice(&h);
            tar.extend_from_slice(data);
            tar.resize(tar.len().div_ceil(512) * 512, 0);
        }
        tar.extend_from_slice(&[0u8; 1024]);
        let path = dir.join("rel.tar.gz");
        let mut gz = flate2::write::GzEncoder::new(std::fs::File::create(&path).unwrap(), flate2::Compression::default());
        std::io::Write::write_all(&mut gz, &tar).unwrap();
        gz.finish().unwrap();
        path
    }

    /// An update from the Rust build installed Sonata without most of its
    /// icons (and its Rust build linked to them): the icon themes are
    /// ~16,000 symbolic links, and the archive reader left every link out.
    /// Links that stay inside are kept, as the Python's "data" filter does;
    /// those leading out -- also through a link extracted before -- aren't.
    #[test]
    fn test_update_lost_the_icon_links() {
        let dir = tempfile::tempdir().unwrap();
        let tb = tarball_of(dir.path(), &[
            ("s/icons/places/folder.svg", b'0', "", b"<svg/>"),
            ("s/icons/apps", b'5', "", b""),
            ("s/icons/apps/system-file-manager.svg", b'2', "../places/folder.svg", b""),
            ("s/icons/apps/copy.svg", b'1', "s/icons/places/folder.svg", b""),
            ("s/up", b'2', "..", b""),          // the release's own top: inside
            ("s/abs", b'2', "/etc/passwd", b""),
            ("s/out", b'2', "../../evil", b""),
            ("s/sneaky", b'2', "up/..", b""),    // through "up": outside
            ("s/hard", b'1', "../../evil", b""),
            ("s/up/written-through.txt", b'0', "", b"x"), // through a link, still inside
        ]);
        let dest = dir.path().join("a").join("b");
        std::fs::create_dir_all(&dest).unwrap();
        extract(&tb, &dest).unwrap();
        let link = dest.join("s/icons/apps/system-file-manager.svg");
        assert!(std::fs::symlink_metadata(&link).unwrap().file_type().is_symlink());
        assert_eq!(std::fs::read(&link).unwrap(), b"<svg/>");
        assert_eq!(std::fs::read(dest.join("s/icons/apps/copy.svg")).unwrap(), b"<svg/>");
        assert!(std::fs::symlink_metadata(dest.join("s/up")).is_ok());
        for gone in ["s/abs", "s/out", "s/sneaky", "s/hard"] {
            assert!(std::fs::symlink_metadata(dest.join(gone)).is_err(), "{gone}");
        }
        assert!(dest.join("written-through.txt").is_file());
        assert!(!dir.path().join("a/written-through.txt").exists());
    }

    /// A tag given on the command line named the cache folder emptied
    /// before the download: "../.." emptied one outside the cache.
    #[test]
    fn test_tag_emptied_a_folder_outside_the_cache() {
        assert_eq!(folder_name("v0.22.0-alpha"), "v0.22.0-alpha");
        for tag in ["..", "../..", "a/b", ".", "", "v1;rm"] {
            let n = folder_name(tag);
            assert!(!n.contains('/') && n != ".." && n != "." && !n.is_empty() && !n.contains(';'), "{tag} -> {n}");
        }
    }

    /// One HTTP answer from 127.0.0.1 (the tests never reach GitHub).
    fn serve_once(body: Vec<u8>) -> String {
        use std::io::Write;
        let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = l.local_addr().unwrap().port();
        std::thread::spawn(move || {
            if let Ok((mut c, _)) = l.accept() {
                let mut buf = [0u8; 4096];
                let _ = c.read(&mut buf);
                let _ = write!(c, "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", body.len());
                let _ = c.write_all(&body);
            }
        });
        format!("http://127.0.0.1:{port}/tarball/v9.0.0")
    }

    struct Fake {
        dir: tempfile::TempDir,
        ran: std::cell::RefCell<Vec<(Vec<String>, PathBuf)>>,
        said: std::cell::RefCell<Vec<String>>,
    }

    impl Fake {
        fn new() -> Fake {
            Fake { dir: tempfile::tempdir().unwrap(), ran: Default::default(), said: Default::default() }
        }

        fn release(&self) -> Release {
            let src = tempfile::tempdir().unwrap();
            let members: Vec<(&str, u8, &str, &[u8])> = vec![("sonata2-v9/install.sh", b'0', "", b"#!/bin/sh\n"), ("sonata2-v9/data/icons/a.svg", b'0', "", b"<svg/>")];
            let tb = tarball_of(src.path(), &members);
            let url = serve_once(std::fs::read(tb).unwrap());
            Release { tag: "v9.0.0".into(), name: String::new(), body: String::new(), url: String::new(), tarball: url }
        }

        fn update(&self, rel: Release, code_of: &dyn Fn(&str) -> i32) -> i32 {
            let root = self.dir.path().join("share/sonata2-rs");
            std::fs::create_dir_all(root.join("tools")).unwrap();
            let cache = self.dir.path().join("cache");
            let steps = Steps {
                current: "0.1.0-alpha".into(),
                root,
                greeter: false,
                latest: &|| Some(rel.clone()),
                download: &|r| download_to(r, &cache, &|t| self.said.borrow_mut().push(t.into())),
                run: &|argv, cwd| {
                    self.ran.borrow_mut().push((argv.to_vec(), cwd.to_path_buf()));
                    code_of(argv.last().unwrap())
                },
                restart: vec!["sonata2".into(), "restart".into()],
                target: self.dir.path().join("rust-target"),
                say: &|t| self.said.borrow_mut().push(t.into()),
            };
            update(&[], &steps)
        }
    }

    /// An update runs the release's install.sh -- which builds Sonata with
    /// cargo and installs it -- into the build kept between updates (each
    /// release is a new folder: building it all from nothing took many
    /// minutes), then restarts Sonata.
    #[test]
    fn test_self_update_builds_and_installs_the_release() {
        let f = Fake::new();
        assert_eq!(f.update(f.release(), &|_| 0), 0);
        let ran = f.ran.borrow();
        assert_eq!(ran.len(), 2);
        assert_eq!(&ran[0].0[..2], ["env".to_string(), format!("CARGO_TARGET_DIR={}", f.dir.path().join("rust-target").display())]);
        assert_eq!(ran[0].0[2], "bash");
        assert!(ran[0].0[3].ends_with("sonata2-v9/install.sh"));
        assert_eq!(&ran[0].0[4..], ["--yes", "--no-greeter"]);
        assert!(ran[0].1.starts_with(f.dir.path().join("cache/v9.0.0")));
        assert!(f.dir.path().join("rust-target").is_dir());
        assert_eq!(ran[1].0, ["sonata2", "restart"]);
        assert_eq!(f.said.borrow().last().unwrap(), "Sonata v9.0.0 is installed.");
        assert_eq!(sonata_config::load(NAME, &defaults())["notified"], "v9.0.0");
    }

    /// No cargo, or a build that fails: install.sh stops before it changes
    /// anything, and Sonata isn't restarted.
    #[test]
    fn installer_failing_changes_nothing() {
        let f = Fake::new();
        assert_eq!(f.update(f.release(), &|a| if a == "--no-greeter" { 3 } else { 0 }), 3);
        assert_eq!(f.ran.borrow().len(), 1); // no restart
        assert!(f.said.borrow().last().unwrap().contains("Sonata wasn't changed"));
        // up to date: nothing run
        let f = Fake::new();
        let mut rel = f.release();
        rel.tag = "0.1.0-alpha".into();
        assert_eq!(f.update(rel, &|_| 0), 0);
        assert!(f.ran.borrow().is_empty());
    }

    #[test]
    fn extracts_the_release_and_never_writes_outside() {
        let dir = tempfile::tempdir().unwrap();
        let tb = tarball(dir.path(), &[("sonata2-abc/install.sh", b"#!/bin/sh\n"), ("sonata2-abc/VERSION", b""), ("../../evil.sh", b"x"), ("/abs.sh", b"x")]);
        let dest = dir.path().join("a").join("b");
        std::fs::create_dir_all(&dest).unwrap();
        extract(&tb, &dest).unwrap();
        assert!(dest.join("sonata2-abc/install.sh").exists());
        assert!(dest.join("sonata2-abc/VERSION").exists());
        assert!(!dir.path().join("evil.sh").exists() && !dir.path().join("a/evil.sh").exists());
    }

    #[test]
    fn check_line_for_software_update() {
        let rel = Release { tag: "v0.2.0".into(), name: String::new(), body: String::new(), url: String::new(), tarball: String::new() };
        let line = check_line("0.1.0-alpha", &rel);
        let ups = crate::updates::parse_arrow(&line);
        assert_eq!((ups[0].name.as_str(), ups[0].old.as_str(), ups[0].new.as_str()), ("Sonata", "0.1.0-alpha", "0.2.0"));
    }
}
