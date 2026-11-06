//! The mouse pointer (sonata2/cursors.py; Settings > Mouse & Trackpad >
//! Pointer, Vini): Sonata's own pointers by default, any other pointer
//! theme installed, one added from a downloaded file (a .tar.gz / .tar.xz /
//! .zip from gnome-look and the like, or a folder), its size, and back to
//! Sonata's.
//!
//! Where it shows: Wayfire draws the pointer ([input] cursor_theme / size,
//! applied at once); GTK and the apps that read the desktop's settings
//! follow prefs (cursor-theme / cursor-size); other apps read
//! XCURSOR_THEME / XCURSOR_SIZE at login (prefs_write::session_env).

use crate::prefs_write;
use sonata_config::prefs::I;
use std::path::{Path, PathBuf};

pub const DEFAULT: &str = "Sonata-Cursors";
pub const DEFAULT_SIZE: i64 = 24;
/// the sizes pointer themes are drawn at (others snap to one)
pub const SIZES: [i64; 4] = [24, 32, 48, 64];
pub const ARCHIVES: [&str; 8] = [".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar", ".zip"];

/// The file held no pointer theme.
#[derive(Debug, Clone, PartialEq)]
pub struct NotATheme(pub String);

impl std::fmt::Display for NotATheme {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}

pub fn bundled() -> PathBuf {
    sonata_config::data_path("icons")
}

/// Where added pointers go: ~/.local/share/icons (every toolkit looks there).
pub fn user_dir() -> PathBuf {
    crate::util::data_home().join("icons")
}

/// The folders pointer themes are looked up in, in XCURSOR_PATH's order.
pub fn search_path() -> Vec<PathBuf> {
    let mut dirs = vec![bundled(), user_dir(), crate::util::home().join(".icons")];
    let data_dirs = std::env::var("XDG_DATA_DIRS").ok().filter(|v| !v.is_empty()).unwrap_or_else(|| "/usr/local/share:/usr/share".into());
    dirs.extend(data_dirs.split(':').filter(|d| !d.is_empty()).map(|d| Path::new(d).join("icons")));
    dirs.push("/usr/share/icons".into());
    dirs.push("/usr/share/pixmaps".into());
    let mut out: Vec<PathBuf> = Vec::new();
    for d in dirs {
        if !out.contains(&d) {
            out.push(d);
        }
    }
    out
}

/// A pointer theme: a folder with a cursors/ folder holding the pointer.
pub fn is_theme(folder: &Path) -> bool {
    let cur = folder.join("cursors");
    cur.is_dir() && ["default", "left_ptr", "arrow"].iter().any(|n| cur.join(n).exists())
}

/// index.theme's Name, else the folder's name.
fn label(folder: &Path, name: &str) -> String {
    let text = std::fs::read(folder.join("index.theme")).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
    for line in text.lines() {
        if let Some((k, v)) = line.split_once('=') {
            if k.trim() == "Name" && !v.trim().is_empty() {
                return v.trim().to_string();
            }
        }
    }
    name.to_string()
}

/// [(name, label)] of every pointer theme found, Sonata's first, then by label.
pub fn themes() -> Vec<(String, String)> {
    let mut found: Vec<(String, String)> = Vec::new();
    for d in search_path() {
        let Ok(rd) = std::fs::read_dir(&d) else { continue };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if !found.iter().any(|(n, _)| *n == name) && is_theme(&e.path()) {
                let l = label(&e.path(), &name);
                found.push((name, l));
            }
        }
    }
    let own = found.iter().find(|(n, _)| n == DEFAULT).map(|(_, l)| l.clone()).unwrap_or_else(|| "Sonata".into());
    let mut rest: Vec<(String, String)> = found.into_iter().filter(|(n, _)| n != DEFAULT).collect();
    rest.sort_by_key(|(_, l)| l.to_lowercase());
    let mut out = vec![(DEFAULT.to_string(), own)];
    out.extend(rest);
    out
}

pub fn current() -> String {
    Some(prefs_write::get(I, "cursor-theme")).filter(|s| !s.is_empty()).unwrap_or_else(|| DEFAULT.into())
}

pub fn size() -> i64 {
    prefs_write::get(I, "cursor-size").trim().parse().unwrap_or(DEFAULT_SIZE)
}

/// The pointer theme and / or size, everywhere it can change now.
pub fn apply(name: Option<&str>, px: Option<i64>) {
    if let Some(n) = name {
        prefs_write::set(I, "cursor-theme", n);
        sonata_backend::wfconfig::set("input", "cursor_theme", n);
    }
    if let Some(px) = px {
        prefs_write::set(I, "cursor-size", &px.to_string());
        sonata_backend::wfconfig::set("input", "cursor_size", &px.to_string());
    }
}

/// Sonata's pointer at its normal size.
pub fn reset() {
    apply(Some(DEFAULT), Some(DEFAULT_SIZE));
}

/// Anything in an unpacked download that isn't a plain file, a folder or
/// a link staying inside the theme goes (a downloaded archive is never
/// trusted: links out, fifos, devices).
fn sanitize(root: &Path) {
    let Ok(real_root) = std::fs::canonicalize(root) else { return };
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        let Ok(rd) = std::fs::read_dir(&dir) else { continue };
        for e in rd.flatten() {
            let p = e.path();
            let Ok(meta) = std::fs::symlink_metadata(&p) else { continue };
            let ft = meta.file_type();
            if ft.is_symlink() {
                // pointer themes link one name to another: inside the theme only
                let inside = std::fs::canonicalize(&p).is_ok_and(|t| t.starts_with(&real_root) && t != real_root);
                if !inside {
                    let _ = std::fs::remove_file(&p);
                }
            } else if ft.is_dir() {
                stack.push(p);
            } else if !ft.is_file() {
                let _ = std::fs::remove_file(&p);
            }
        }
    }
}

fn unpack(path: &Path, dest: &Path) -> Result<(), String> {
    if path.to_string_lossy().to_lowercase().ends_with(".zip") {
        let f = std::fs::File::open(path).map_err(|e| e.to_string())?;
        let mut z = zip::ZipArchive::new(f).map_err(|e| e.to_string())?;
        for i in 0..z.len() {
            let mut entry = z.by_index(i).map_err(|e| e.to_string())?;
            let Some(rel) = entry.enclosed_name() else { continue }; // ../ and absolute names stay out
            let target = dest.join(rel);
            if entry.is_dir() {
                std::fs::create_dir_all(&target).map_err(|e| e.to_string())?;
            } else if entry.unix_mode().is_none_or(|m| m & 0o170000 != 0o120000) {
                if let Some(p) = target.parent() {
                    std::fs::create_dir_all(p).map_err(|e| e.to_string())?;
                }
                let mut out = std::fs::File::create(&target).map_err(|e| e.to_string())?;
                std::io::copy(&mut entry, &mut out).map_err(|e| e.to_string())?;
            }
        }
    } else {
        // GNU tar skips members with ".." (and fails at the end: what it
        // did write is kept, as Python's filter keeps the safe members) and
        // strips leading "/"; what it writes is checked again below
        let out = std::process::Command::new("tar")
            .args(["--no-same-owner", "--no-same-permissions", "-xf"])
            .arg(path)
            .arg("-C")
            .arg(dest)
            .env("LC_ALL", "C")
            .stdin(std::process::Stdio::null())
            .output()
            .map_err(|e| e.to_string())?;
        let wrote = std::fs::read_dir(dest).is_ok_and(|mut rd| rd.next().is_some());
        if !out.status.success() && !wrote {
            return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
        }
    }
    sanitize(dest);
    Ok(())
}

/// The pointer themes in an unpacked folder (the top, or a few levels down).
fn found_in(folder: &Path) -> Vec<PathBuf> {
    fn walk(dir: &Path, depth: usize, out: &mut Vec<PathBuf>) {
        if is_theme(dir) {
            out.push(dir.to_path_buf()); // a theme's own folders aren't themes
            return;
        }
        if depth >= 3 {
            return;
        }
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        let mut subs: Vec<PathBuf> = rd.flatten().map(|e| e.path()).filter(|p| p.is_dir() && !p.is_symlink()).collect();
        subs.sort();
        for s in subs {
            walk(&s, depth + 1, out);
        }
    }
    let mut out = Vec::new();
    walk(folder, 0, &mut out);
    out
}

fn copy_tree(src: &Path, dst: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for e in std::fs::read_dir(src)?.flatten() {
        let (s, d) = (e.path(), dst.join(e.file_name()));
        let ft = std::fs::symlink_metadata(&s)?.file_type();
        if ft.is_symlink() {
            std::os::unix::fs::symlink(std::fs::read_link(&s)?, &d)?;
        } else if ft.is_dir() {
            copy_tree(&s, &d)?;
        } else {
            std::fs::copy(&s, &d)?;
        }
    }
    Ok(())
}

/// Install the pointer themes in a downloaded file or a folder into
/// ~/.local/share/icons; returns their names. A theme of the same name is
/// replaced (the newer download). NotATheme when there is none.
pub fn add(path: &str) -> Result<Vec<String>, NotATheme> {
    let path = crate::util::expand(path);
    let tmp = tempfile_dir().map_err(NotATheme)?;
    let r = add_from(&path, &tmp);
    let _ = std::fs::remove_dir_all(&tmp);
    r
}

fn tempfile_dir() -> Result<PathBuf, String> {
    let base = std::env::temp_dir();
    for i in 0..100 {
        let d = base.join(format!("sonata-cursors-{}-{i}", std::process::id()));
        if std::fs::create_dir(&d).is_ok() {
            return Ok(d);
        }
    }
    Err("no temporary folder".into())
}

fn add_from(path: &Path, tmp: &Path) -> Result<Vec<String>, NotATheme> {
    let shown = path.display().to_string();
    let low = shown.to_lowercase();
    let src = if path.is_dir() {
        path.to_path_buf()
    } else if ARCHIVES.iter().any(|a| low.ends_with(a)) {
        unpack(path, tmp).map_err(NotATheme)?;
        tmp.to_path_buf()
    } else {
        return Err(NotATheme(shown));
    };
    let found = found_in(&src);
    if found.is_empty() {
        return Err(NotATheme(shown));
    }
    std::fs::create_dir_all(user_dir()).map_err(|e| NotATheme(e.to_string()))?;
    let mut names = Vec::new();
    for folder in found {
        let mut name = folder.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        if folder == src && src == tmp {
            let base = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            name = Some(base.split('.').next().unwrap_or("").to_string()).filter(|n| !n.is_empty()).unwrap_or_else(|| "Pointer".into());
        }
        if name == DEFAULT {
            name = format!("{DEFAULT}-custom"); // never over Sonata's own
        }
        let dest = user_dir().join(&name);
        if std::fs::canonicalize(&folder).ok() == std::fs::canonicalize(&dest).ok() && dest.exists() {
            names.push(name); // already there (picked from ~/.local/share/icons)
            continue;
        }
        if dest.symlink_metadata().is_ok() {
            let _ = if dest.is_dir() && !dest.is_symlink() { std::fs::remove_dir_all(&dest) } else { std::fs::remove_file(&dest) };
        }
        copy_tree(&folder, &dest).map_err(|e| NotATheme(e.to_string()))?;
        names.push(name);
    }
    Ok(names)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn theme_files(top: &str) -> Vec<(String, Vec<u8>)> {
        vec![(format!("{top}/index.theme"), format!("[Icon Theme]\nName={top}\n").into_bytes()), (format!("{top}/cursors/default"), b"XCUR".to_vec())]
    }

    /// A tar (ustar) written by hand: names the tar command would refuse to put in.
    fn tar(path: &Path, files: &[(String, Vec<u8>)], links: &[(&str, &str)]) {
        fn header(name: &str, size: usize, kind: u8, link: &str) -> Vec<u8> {
            let mut h = vec![0u8; 512];
            h[..name.len()].copy_from_slice(name.as_bytes());
            h[100..107].copy_from_slice(b"0000644");
            h[108..115].copy_from_slice(b"0001750");
            h[116..123].copy_from_slice(b"0001750");
            h[124..135].copy_from_slice(format!("{size:011o}").as_bytes());
            h[136..147].copy_from_slice(b"00000000000");
            h[156] = kind;
            h[157..157 + link.len()].copy_from_slice(link.as_bytes());
            h[257..263].copy_from_slice(b"ustar\0");
            h[263..265].copy_from_slice(b"00");
            h[148..156].copy_from_slice(b"        ");
            let sum: u32 = h.iter().map(|b| *b as u32).sum();
            h[148..155].copy_from_slice(format!("{sum:06o}\0").as_bytes());
            h
        }
        let mut out = Vec::new();
        for (name, data) in files {
            out.extend(header(name, data.len(), b'0', ""));
            out.extend(data);
            out.extend(vec![0u8; (512 - data.len() % 512) % 512]);
        }
        for (name, to) in links {
            out.extend(header(name, 0, b'2', to));
        }
        out.extend(vec![0u8; 1024]);
        fs::write(path, out).unwrap();
    }

    #[test]
    fn sonatas_pointer_first_then_installed_themes() {
        let _l = crate::util::test_lock();
        let z = user_dir().join("Zeta");
        fs::create_dir_all(z.join("cursors")).unwrap();
        fs::write(z.join("cursors/default"), "x").unwrap();
        fs::write(z.join("index.theme"), "[Icon Theme]\nName=Zeta Pointers\n").unwrap();
        fs::create_dir_all(user_dir().join("IconsOnly/48x48")).unwrap(); // an icon theme: not a pointer
        let all = themes();
        assert_eq!(all[0].0, DEFAULT);
        assert!(all.contains(&("Zeta".into(), "Zeta Pointers".into())));
        assert!(!all.iter().any(|(n, _)| n == "IconsOnly"));
    }

    #[test]
    fn apply_and_reset_reach_wayfire_and_the_apps() {
        let _l = crate::util::test_lock();
        apply(Some("Zeta"), Some(48));
        assert_eq!((current().as_str(), size()), ("Zeta", 48));
        assert_eq!(sonata_backend::wfconfig::get("input", "cursor_theme", ""), "Zeta");
        assert_eq!(sonata_backend::wfconfig::get("input", "cursor_size", ""), "48");
        // the login hands the pointer to every app (Qt, X11, games kept Sonata's)
        assert!(prefs_write::env_text(&sonata_config::prefs::load()).contains("export XCURSOR_THEME=Zeta XCURSOR_SIZE=48"));
        apply(None, Some(32)); // size only keeps the theme
        assert_eq!((current().as_str(), size()), ("Zeta", 32));
        reset();
        assert_eq!((current().as_str(), size()), (DEFAULT, DEFAULT_SIZE));
        assert_eq!(sonata_backend::wfconfig::get("input", "cursor_theme", ""), DEFAULT);
    }

    #[test]
    fn add_a_downloaded_tar_a_zip_and_a_folder() {
        let _l = crate::util::test_lock();
        let t = tempfile::tempdir().unwrap();
        let path = t.path().join("Bibata.tar.gz");
        tar(&path, &theme_files("Bibata-Modern"), &[]);
        assert_eq!(add(&path.display().to_string()).unwrap(), ["Bibata-Modern"]);
        assert!(is_theme(&user_dir().join("Bibata-Modern")));
        assert!(themes().iter().any(|(n, _)| n == "Bibata-Modern"));
        // a zip with two themes
        let zp = t.path().join("pack.zip");
        {
            let mut z = zip::ZipWriter::new(fs::File::create(&zp).unwrap());
            for (name, data) in theme_files("pack/Light").into_iter().chain(theme_files("pack/Dark")) {
                z.start_file(name, zip::write::SimpleFileOptions::default()).unwrap();
                std::io::Write::write_all(&mut z, &data).unwrap();
            }
            z.finish().unwrap();
        }
        let mut got = add(&zp.display().to_string()).unwrap();
        got.sort();
        assert_eq!(got, ["Dark", "Light"]);
        // a folder
        for (name, data) in theme_files("Mine") {
            let p = t.path().join(name);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(p, data).unwrap();
        }
        assert_eq!(add(&t.path().join("Mine").display().to_string()).unwrap(), ["Mine"]);
        // never over Sonata's own
        let same = t.path().join("same.tar");
        tar(&same, &theme_files(DEFAULT), &[]);
        assert_eq!(add(&same.display().to_string()).unwrap(), [format!("{DEFAULT}-custom")]);
    }

    #[test]
    fn a_file_without_pointers_says_so() {
        let t = tempfile::tempdir().unwrap();
        let path = t.path().join("icons.tar");
        tar(&path, &[("Icons/index.theme".into(), b"[Icon Theme]\n".to_vec())], &[]);
        assert!(add(&path.display().to_string()).is_err());
        fs::write(t.path().join("notes.txt"), "x").unwrap();
        assert!(add(&t.path().join("notes.txt").display().to_string()).is_err());
    }

    /// An archive is untrusted: ../ paths and links out of the theme are left out.
    #[test]
    fn a_downloaded_archive_never_writes_outside() {
        let _l = crate::util::test_lock();
        let t = tempfile::tempdir().unwrap();
        let inner = t.path().join("a/b");
        fs::create_dir_all(&inner).unwrap();
        let path = inner.join("evil.tar");
        let mut files = theme_files("Evil");
        files.push(("../../escaped.txt".into(), b"x".to_vec()));
        tar(&path, &files, &[("Evil/cursors/pointer", "/etc/passwd")]);
        assert_eq!(add(&path.display().to_string()).unwrap(), ["Evil"]);
        assert!(!t.path().join("escaped.txt").exists() && !t.path().join("a/escaped.txt").exists());
        assert!(fs::symlink_metadata(user_dir().join("Evil/cursors/pointer")).is_err());
    }
}
