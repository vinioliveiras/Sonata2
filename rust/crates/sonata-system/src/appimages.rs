//! Installing an AppImage (appimages.py; Vini: Install in its right-click,
//! Files and the desktop), the way an app is dragged into Applications on
//! macOS: the file is copied into ~/Applications and made executable, and a
//! launcher is written for it with the name, icon and categories the
//! AppImage carries -- it's then in the Apps Menu, Launchpad and Spotlight,
//! and can be kept in the Dock. Uninstalling it (Dock, Launchpad, Settings >
//! Apps) moves both the launcher and the AppImage to the Trash (uninstall.rs:
//! a "shortcut" whose launcher names its file in X-Sonata-AppImage).
//!
//! ```ignore
//! let res = appimages::install(Path::new("/home/me/Downloads/App-1.0.AppImage"))?;  // blocking: off the main loop
//! ```

use std::collections::HashMap;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

pub const APPS_DIR: &str = "Applications";
pub const KEY: &str = "X-Sonata-AppImage";
pub const EXTRACT_TIMEOUT_S: u64 = 30;

/// Where install() puts things (the tests point them at a temporary folder).
#[derive(Clone, Debug)]
pub struct Dirs {
    /// ~/Applications: the AppImages themselves
    pub apps: PathBuf,
    /// ~/.local/share/applications: their launchers
    pub launchers: PathBuf,
    /// their icons, next to Sonata's data
    pub icons: PathBuf,
}

impl Default for Dirs {
    fn default() -> Self {
        let data = glib::user_data_dir();
        Dirs { apps: glib::home_dir().join(APPS_DIR), launchers: data.join("applications"), icons: data.join("sonata2-data").join("appimages") }
    }
}

/// What install() did.
#[derive(Clone, Debug, PartialEq)]
pub struct Installed {
    /// the AppImage's new path
    pub appimage: PathBuf,
    /// its launcher
    pub desktop: PathBuf,
    pub name: String,
}

/// A launcher id from a name: "Some App 2" -> "some-app-2".
pub fn slug(name: &str) -> String {
    let re = regex::Regex::new(r"[^a-z0-9]+").unwrap();
    let s = re.replace_all(&name.to_lowercase(), "-").trim_matches('-').to_string();
    if s.is_empty() { "app".into() } else { s }
}

/// The AppImage's own runtime extracts what matches `pattern` (type 2:
/// --appimage-extract <pattern>) into `into`/squashfs-root; that folder.
fn extract(appimage: &Path, pattern: &str, into: &Path) -> PathBuf {
    let child = Command::new(appimage)
        .args(["--appimage-extract", pattern])
        .current_dir(into)
        .env("APPIMAGE_EXTRACT_AND_RUN", "0")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
    if let Ok(mut child) = child {
        // a runtime that hangs must not keep the install waiting forever
        let deadline = Instant::now() + Duration::from_secs(EXTRACT_TIMEOUT_S);
        loop {
            match child.try_wait() {
                Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
                Ok(None) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break;
                }
                _ => break,
            }
        }
    }
    into.join("squashfs-root")
}

/// The [Desktop Entry] of a .desktop file, read as leniently as Python's
/// configparser (strict=False: a repeated key, the later one wins).
fn parse_entry(text: &str) -> Option<HashMap<String, String>> {
    let mut out = None::<HashMap<String, String>>;
    let mut inside = false;
    for line in text.lines() {
        let t = line.trim();
        if t.starts_with('[') && t.ends_with(']') {
            inside = t == "[Desktop Entry]";
            if inside && out.is_none() {
                out = Some(HashMap::new());
            }
            continue;
        }
        if !inside || t.is_empty() || t.starts_with('#') || t.starts_with(';') {
            continue;
        }
        let cut = match (t.find('='), t.find(':')) {
            (Some(a), Some(b)) => a.min(b),
            (Some(a), None) | (None, Some(a)) => a,
            _ => continue,
        };
        if let Some(m) = out.as_mut() {
            m.insert(t[..cut].trim().to_string(), t[cut + 1..].trim().to_string());
        }
    }
    out
}

/// The AppImage's own launcher (its top-level .desktop file): Name, Icon,
/// Categories, Comment, StartupWMClass (empty if it has none).
pub fn read_entry(root: &Path) -> HashMap<String, String> {
    let mut files: Vec<PathBuf> = std::fs::read_dir(root)
        .map(|rd| rd.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "desktop")).collect())
        .unwrap_or_default();
    files.sort();
    for p in files {
        let Ok(text) = std::fs::read_to_string(&p) else { continue };
        if let Some(e) = parse_entry(&text) {
            return e;
        }
    }
    HashMap::new()
}

fn nonempty_file(p: &Path) -> bool {
    std::fs::metadata(p).is_ok_and(|m| m.is_file() && m.len() > 0)
}

/// Its icon copied next to Sonata's data (.DirIcon, or the file its
/// launcher's Icon= names); the path, or None.
fn icon(appimage: &Path, root: &Path, work: &Path, entry: &HashMap<String, String>, did: &str, icons: &Path) -> Option<PathBuf> {
    std::fs::create_dir_all(icons).ok()?;
    let mut candidates = Vec::new();
    let dir_icon = root.join(".DirIcon");
    if let Ok(target) = std::fs::read_link(&dir_icon) {
        // usually a link to the real file: that one too
        let target = target.to_string_lossy().to_string();
        extract(appimage, &target, work);
        candidates.push(root.join(&target));
    }
    candidates.push(dir_icon);
    if let Some(name) = entry.get("Icon").filter(|n| !n.is_empty()) {
        for ext in [".png", ".svg", ".xpm"] {
            extract(appimage, &format!("{name}{ext}"), work);
            candidates.push(root.join(format!("{name}{ext}")));
        }
    }
    for c in candidates {
        if !nonempty_file(&c) {
            continue;
        }
        let head: Vec<u8> = std::fs::read(&c).ok()?.into_iter().take(256).collect();
        let svg = head.windows(4).any(|w| w == b"<svg") || c.to_string_lossy().ends_with(".svg");
        let dest = icons.join(format!("{did}{}", if svg { ".svg" } else { ".png" }));
        std::fs::copy(&c, &dest).ok()?;
        return Some(dest);
    }
    None
}

fn value(text: &str) -> String {
    text.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// A path as an Exec= argument (the desktop entry spec's quoting).
pub fn quote(path: &str) -> String {
    if path.chars().any(|c| c.is_whitespace() || "\"'\\$`".contains(c)) {
        let mut out = String::from("\"");
        for c in path.chars() {
            if "\"`$\\".contains(c) {
                out.push('\\');
            }
            out.push(c);
        }
        out.push('"');
        return out;
    }
    path.to_string()
}

pub fn launcher_text(appimage: &str, entry: &HashMap<String, String>, icon: Option<&str>, fallback_name: &str) -> String {
    let name = value(entry.get("Name").filter(|n| !n.is_empty()).map_or(fallback_name, String::as_str));
    let mut lines = vec![
        "[Desktop Entry]".to_string(),
        "Type=Application".into(),
        format!("Name={name}"),
        format!("Exec={} %U", quote(appimage)),
        format!("TryExec={appimage}"),
        format!("Icon={}", icon.unwrap_or("application-x-executable")),
    ];
    for key in ["Comment", "Categories", "StartupWMClass", "MimeType", "Keywords"] {
        if let Some(v) = entry.get(key).filter(|v| !v.is_empty()) {
            lines.push(format!("{key}={}", value(v)));
        }
    }
    if entry.get("Categories").is_none_or(|v| v.is_empty()) {
        lines.push("Categories=Utility;".into());
    }
    lines.push("Terminal=false".into());
    lines.push(format!("{KEY}={appimage}"));
    lines.join("\n") + "\n"
}

/// `path`, or "name 2.ext", "name 3.ext"... if it's taken.
fn free(path: &Path) -> PathBuf {
    if !path.exists() {
        return path.to_path_buf();
    }
    let s = path.to_string_lossy().to_string();
    let file = path.file_name().map(|f| f.to_string_lossy().to_string()).unwrap_or_default();
    // os.path.splitext: the last dot of the file name, not a leading one
    let (base, ext) = match file.rfind('.').filter(|&i| i > 0 && !file[..i].chars().all(|c| c == '.')) {
        Some(i) => (&s[..s.len() - (file.len() - i)], &file[i..]),
        None => (s.as_str(), ""),
    };
    let mut n = 2;
    while Path::new(&format!("{base} {n}{ext}")).exists() {
        n += 1;
    }
    PathBuf::from(format!("{base} {n}{ext}"))
}

/// The name from the file's when it carries no launcher: architecture and
/// version cut ("Cool App-1.2.3-x86_64.AppImage" -> "Cool App").
pub fn fallback_name(file_name: &str) -> String {
    let stem = Path::new(file_name).file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let re = regex::Regex::new(r"(?i)[-_ ]?(x86_64|aarch64|amd64|linux|\d+(\.\d+)+)+").unwrap();
    let cut = re.replace_all(&stem, "").to_string();
    if cut.is_empty() { file_name.to_string() } else { cut }
}

/// Copy `source` into ~/Applications, executable, and write its launcher.
/// Err when the copy or the launcher can't be written. Blocking.
pub fn install(source: &Path) -> std::io::Result<Installed> {
    install_in(source, &Dirs::default())
}

pub fn install_in(source: &Path, dirs: &Dirs) -> std::io::Result<Installed> {
    std::fs::create_dir_all(&dirs.apps)?;
    let src = std::fs::canonicalize(source)?;
    let inside = src.parent().map(Path::to_path_buf) == std::fs::canonicalize(&dirs.apps).ok();
    let dest = if inside { src.clone() } else { free(&dirs.apps.join(src.file_name().unwrap_or_default())) };
    if !inside {
        std::fs::copy(&src, &dest)?;
    }
    let mut perm = std::fs::metadata(&dest)?.permissions();
    perm.set_mode(perm.mode() | 0o111);
    std::fs::set_permissions(&dest, perm)?;
    let work = tempfile::Builder::new().prefix("sonata2-appimage-").tempdir()?;
    let root = extract(&dest, "*.desktop", work.path());
    let entry = read_entry(&root);
    extract(&dest, ".DirIcon", work.path());
    let file_name = dest.file_name().map(|f| f.to_string_lossy().to_string()).unwrap_or_default();
    let fallback = fallback_name(&file_name);
    let name = entry.get("Name").filter(|n| !n.is_empty()).cloned().unwrap_or_else(|| fallback.clone());
    std::fs::create_dir_all(&dirs.launchers)?;
    let desktop = free(&dirs.launchers.join(format!("appimage-{}.desktop", slug(&name))));
    let did = desktop.file_stem().map(|s| s.to_string_lossy().to_string()).unwrap_or_default();
    let icon = icon(&dest, &root, work.path(), &entry, &did, &dirs.icons);
    let text = launcher_text(&dest.to_string_lossy(), &entry, icon.as_deref().map(|p| p.to_str().unwrap_or_default()), &fallback);
    std::fs::write(&desktop, text)?;
    Ok(Installed { appimage: dest, desktop, name })
}

/// The AppImage an installed launcher runs (X-Sonata-AppImage), or None.
pub fn file_of(desktop_path: &Path) -> Option<PathBuf> {
    let text = std::fs::read_to_string(desktop_path).ok()?;
    let path = text.lines().find_map(|l| l.strip_prefix(&format!("{KEY}=")))?.trim();
    let p = PathBuf::from(path);
    p.is_file().then_some(p)
}

#[cfg(test)]
mod tests {
    //! tests/test_appimages.py (Vini: an AppImage could only be run from
    //! Files, not installed).
    use super::*;

    // a stand-in AppImage: its runtime's --appimage-extract <pattern> writes
    // squashfs-root/<files> where it's run
    const FAKE: &str = r#"#!/bin/sh
[ "$1" = "--appimage-extract" ] || exit 1
mkdir -p squashfs-root
case "$2" in
  "*.desktop") printf '[Desktop Entry]\nType=Application\nName=Cool App\nIcon=cool\nCategories=Graphics;\nStartupWMClass=CoolApp\nExec=AppRun %%U\n' > squashfs-root/cool.desktop ;;
  ".DirIcon") ln -sf cool.png squashfs-root/.DirIcon ;;
  "cool.png") printf '\211PNG fake' > squashfs-root/cool.png ;;
esac
"#;

    fn setup(script: &str) -> (tempfile::TempDir, Dirs, PathBuf) {
        let home = tempfile::tempdir().unwrap();
        let dirs = Dirs { apps: home.path().join("Applications"), launchers: home.path().join("applications"), icons: home.path().join("icons") };
        let src = home.path().join("Downloads").join("Cool App-1.2.3-x86_64.AppImage");
        std::fs::create_dir_all(src.parent().unwrap()).unwrap();
        std::fs::write(&src, script).unwrap();
        std::fs::set_permissions(&src, std::fs::Permissions::from_mode(0o644)).unwrap(); // downloaded: not executable yet
        (home, dirs, src)
    }

    /// What the user saw: an AppImage could be run, never installed.
    #[test]
    fn test_appimage_could_not_be_installed() {
        let (_h, dirs, src) = setup(FAKE);
        let res = install_in(&src, &dirs).unwrap();
        assert_eq!(res.appimage.parent().unwrap(), std::fs::canonicalize(&dirs.apps).unwrap());
        assert!(std::fs::metadata(&res.appimage).unwrap().permissions().mode() & 0o100 != 0);
        assert!(src.is_file()); // the download stays where it was
        assert_eq!(res.name, "Cool App");
        assert_eq!(res.desktop.file_name().unwrap(), "appimage-cool-app.desktop");
        let text = std::fs::read_to_string(&res.desktop).unwrap();
        assert!(text.contains("Name=Cool App\n"));
        assert!(text.contains("Categories=Graphics;\n"));
        assert!(text.contains("StartupWMClass=CoolApp\n"));
        assert!(text.contains(&format!("Exec=\"{}\" %U\n", res.appimage.display()))); // (a space in its name: quoted)
        let icon = text.lines().find_map(|l| l.strip_prefix("Icon=")).unwrap();
        assert!(Path::new(icon).is_file() && icon.ends_with(".png"), "{icon}");
        assert_eq!(file_of(&res.desktop), Some(res.appimage.clone())); // uninstall finds the file
    }

    #[test]
    fn test_installed_twice_keeps_both() {
        let (_h, dirs, src) = setup(FAKE);
        let a = install_in(&src, &dirs).unwrap();
        let b = install_in(&src, &dirs).unwrap();
        assert_ne!(a.appimage, b.appimage);
        assert_ne!(a.desktop, b.desktop);
        assert!(b.appimage.to_string_lossy().ends_with("Cool App-1.2.3-x86_64 2.AppImage"));
    }

    #[test]
    fn test_no_launcher_inside() {
        let (_h, dirs, src) = setup("#!/bin/sh\nmkdir -p squashfs-root\n");
        let res = install_in(&src, &dirs).unwrap();
        assert_eq!(res.name, "Cool App"); // from the file's name, version cut
        let text = std::fs::read_to_string(&res.desktop).unwrap();
        assert!(text.contains("Icon=application-x-executable\n"));
        assert!(text.contains("Categories=Utility;\n"));
    }

    #[test]
    fn slugs_and_quoting() {
        assert_eq!(slug("Some App 2"), "some-app-2");
        assert_eq!(slug("!!"), "app");
        assert_eq!(quote("/a/b"), "/a/b");
        assert_eq!(quote("/a b/$x"), "\"/a b/\\$x\"");
    }
}
