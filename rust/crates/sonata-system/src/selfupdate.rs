//! Sonata's own updates, from its GitHub releases (backend/selfupdate.py).
//!
//! latest_release() asks GitHub; newer(tag, current) says whether it is an
//! update; `sonata2 self-update [--check] [TAG]` (main()) downloads that
//! release, runs its install.sh and restarts Sonata. Settings > About >
//! Software Update lists it with the system's updates. A clone updates with
//! `git pull` instead (the code runs from it).
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

/// The running Sonata's version (sonata2/__init__.py's __version__: the
/// Python Sonata is what is installed and updated).
pub fn version() -> String {
    let init = install_root().join("sonata2").join("__init__.py");
    std::fs::read_to_string(init)
        .ok()
        .and_then(|t| t.lines().find_map(|l| l.strip_prefix("__version__ = ").map(|r| r.split('#').next().unwrap_or("").trim().trim_matches('"').to_string())))
        .unwrap_or_else(|| "0.0.0".into())
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
/// The folder holding the sonata2 package (a clone, or <prefix>/share/sonata2):
/// the one whose catalogs this build reads.
pub fn install_root() -> PathBuf {
    let loc = sonata_i18n::locale_dir();
    let loc = loc.canonicalize().unwrap_or(loc);
    loc.parent().and_then(Path::parent).map(Path::to_path_buf).unwrap_or_default()
}

pub fn is_clone(root: &Path) -> bool {
    root.join(".git").is_dir()
}

/// How to start the Python Sonata again (__main__.self_argv).
pub fn self_argv() -> Vec<String> {
    if let Some(l) = std::env::var_os("SONATA2_LAUNCHER").filter(|l| Path::new(l).exists()) {
        return vec![l.to_string_lossy().into_owned()];
    }
    vec!["env".into(), format!("PYTHONPATH={}", install_root().display()), "python3".into(), "-m".into(), "sonata2".into()]
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

/// A .tar.gz into `dest`: only plain files and folders that stay inside it
/// (no links, no "../"), as tarfile's "data" filter.
pub fn extract(archive: &Path, dest: &Path) -> std::io::Result<()> {
    let mut data = Vec::new();
    flate2::read::GzDecoder::new(std::fs::File::open(archive)?).read_to_end(&mut data)?;
    let (mut off, mut long_name): (usize, Option<String>) = (0, None);
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
        let mut name = cstr(&h[0..100]);
        if &h[257..262] == b"ustar" {
            let prefix = cstr(&h[345..500]);
            if !prefix.is_empty() {
                name = format!("{prefix}/{name}");
            }
        }
        if let Some(n) = long_name.take() {
            name = n;
        }
        match kind {
            // GNU long name, pax header (path=)
            b'L' => long_name = Some(cstr(&data[body..end])),
            b'x' => {
                let text = String::from_utf8_lossy(&data[body..end]);
                long_name = text.lines().find_map(|l| l.split_once(' ').and_then(|(_, kv)| kv.strip_prefix("path=")).map(String::from));
            }
            b'0' | 0 | b'5' => {
                if let Some(rel) = safe(&name) {
                    let target = dest.join(rel);
                    if kind == b'5' {
                        std::fs::create_dir_all(&target)?;
                    } else {
                        if let Some(p) = target.parent() {
                            std::fs::create_dir_all(p)?;
                        }
                        std::fs::write(&target, &data[body..end])?;
                        use std::os::unix::fs::PermissionsExt;
                        let mode = octal(&h[100..108]) as u32 & 0o755;
                        let _ = std::fs::set_permissions(&target, std::fs::Permissions::from_mode(mode | 0o600));
                    }
                }
            }
            _ => {} // links, devices, global pax headers: left out
        }
        off = body + size.div_ceil(512) * 512;
    }
    Ok(())
}

/// The release's source in the cache; returns the folder with install.sh.
pub fn download(rel: &Release, say: &dyn Fn(&str)) -> std::io::Result<PathBuf> {
    let dest = cache().join(&rel.tag);
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

/// `sonata2 self-update [--check] [TAG]`.
pub fn main(args: &[String]) -> i32 {
    let current = version();
    if args.first().map(String::as_str) == Some("--check") {
        if let Some(rel) = available(10) {
            println!("{}", check_line(&current, &rel));
        }
        return 0;
    }
    let Some(mut rel) = latest_release(10) else {
        println!("Sonata: couldn't reach GitHub (offline?)");
        return 1;
    };
    let tag = args.first().cloned().unwrap_or_else(|| rel.tag.clone());
    if tag != rel.tag {
        rel.tarball = format!("https://api.github.com/repos/{REPO}/tarball/{tag}");
        rel.tag = tag.clone();
    }
    if args.is_empty() && !newer(&tag, &current) {
        println!("Sonata {current} is up to date.");
        return 0;
    }
    let root = install_root();
    if is_clone(&root) {
        println!("This Sonata runs from a git clone: update it with git pull.");
        return 2;
    }
    let folder = match download(&rel, &|t| println!("{t}")) {
        Ok(f) => f,
        Err(e) => {
            println!("Sonata: {e}");
            return 1;
        }
    };
    println!("Installing Sonata {tag}…");
    let code = std::process::Command::new("bash").arg(folder.join("install.sh")).args(install_args(&root.to_string_lossy(), greeter_installed())).current_dir(&folder).status().map(|s| s.code().unwrap_or(1)).unwrap_or(1);
    if code != 0 {
        println!("The installer stopped (exit {code}). Sonata wasn't changed.");
        return code;
    }
    let _ = sonata_config::update(NAME, &json!({"notified": tag}));
    let argv = self_argv();
    let _ = std::process::Command::new(&argv[0]).args(&argv[1..]).arg("restart").status();
    println!("Sonata {tag} is installed.");
    0
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
        assert_eq!(install_args("/home/v/.local/share/sonata2", false), ["--yes", "--no-greeter"]);
        assert_eq!(install_args("/usr/local/share/sonata2", true), ["--yes", "--system"]);
        let argv = vec!["sonata2".to_string()];
        let cmd = terminal_command_for("v0.2.0", "/home/v/GitHub/sonata2", true, &argv);
        assert!(cmd.contains("git -C /home/v/GitHub/sonata2 pull --ff-only") && cmd.contains("restart"));
        assert!(terminal_command_for("v0.2.0", "/x", false, &argv).ends_with("self-update v0.2.0"));
        // Review: the GitHub tag went unquoted into a shell line
        assert!(terminal_command_for("v1;touch /tmp/x", "/x", false, &argv).ends_with("self-update 'v1;touch /tmp/x'"));
        assert_eq!(terminal_command_for("", "/home/v/my clone'x", true, &argv), "git -C '/home/v/my clone'\"'\"'x' pull --ff-only && sonata2 restart");
    }

    fn tarball(dir: &Path, members: &[(&str, &[u8])]) -> PathBuf {
        let mut tar = Vec::new();
        for (name, data) in members {
            let mut h = [0u8; 512];
            h[..name.len()].copy_from_slice(name.as_bytes());
            h[100..107].copy_from_slice(b"0000644");
            h[124..135].copy_from_slice(format!("{:011o}", data.len()).as_bytes());
            h[156] = b'0';
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

    #[test]
    fn extracts_the_release_and_never_writes_outside() {
        let dir = tempfile::tempdir().unwrap();
        let tb = tarball(dir.path(), &[("sonata2-abc/install.sh", b"#!/bin/sh\n"), ("sonata2-abc/sonata2/__init__.py", b""), ("../../evil.sh", b"x"), ("/abs.sh", b"x")]);
        let dest = dir.path().join("a").join("b");
        std::fs::create_dir_all(&dest).unwrap();
        extract(&tb, &dest).unwrap();
        assert!(dest.join("sonata2-abc/install.sh").exists());
        assert!(dest.join("sonata2-abc/sonata2/__init__.py").exists());
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
