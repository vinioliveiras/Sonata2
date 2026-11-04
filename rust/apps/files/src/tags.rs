//! Finder tags (files/tags.py): Red, Orange, Yellow, Green, Blue, Purple,
//! Gray on files and folders. Kept in the file's own extended attribute
//! "user.xdg.tags" (the freedesktop name KDE's Dolphin uses too,
//! comma-separated), so a tag travels with the file. Tags another app set
//! that aren't one of the seven colours are kept as they were.
//!
//! The sidebar's lists come from an index of what Files tagged (Spotlight's
//! job on a Mac): ~/.local/share/sonata/files-tags.json, {path: [tags]} --
//! the same file as the Python Files'; moves and renames update it, and an
//! item whose tag is gone drops out when the list is read.

use gio::prelude::*;
use serde_json::{Map, Value};
use sonata_i18n::n_;
use std::ffi::CString;
use std::os::unix::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

pub const XATTR: &str = "user.xdg.tags";
/// how Gio's enumerations hand it over (in folder::ATTRS)
pub const GIO_ATTR: &str = "xattr::xdg.tags";
pub const PREFIX: &str = "sonata:tag/";
/// stored as is; shown through gettext
pub const COLORS: [(&str, &str); 7] = [(n_!("Red"), "#ff3b30"), (n_!("Orange"), "#ff9500"), (n_!("Yellow"), "#ffcc00"), (n_!("Green"), "#34c759"), (n_!("Blue"), "#007aff"), (n_!("Purple"), "#af52de"), (n_!("Gray"), "#8e8e93")];
static LOCK: Mutex<()> = Mutex::new(());

pub fn index_path() -> PathBuf {
    glib::user_data_dir().join("sonata").join("files-tags.json")
}

/// The tags in an attribute's text: "Red,Work" -> ["Red", "Work"].
pub fn parse(text: &str) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for t in text.split(',').map(str::trim) {
        if !t.is_empty() && !out.iter().any(|o| o == t) {
            out.push(t.to_string());
        }
    }
    out
}

/// Gio hands xattrs over escaped: non-ASCII bytes (and backslash) as \xNN.
fn unescape(s: &str) -> Vec<u8> {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'\\' && i + 3 < b.len() + 0 && b.get(i + 1) == Some(&b'x') {
            if let Some(v) = std::str::from_utf8(&b[i + 2..i + 4]).ok().and_then(|h| u8::from_str_radix(h, 16).ok()) {
                out.push(v);
                i += 4;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    out
}

/// The tags of a listed item (a FileInfo queried with folder::ATTRS).
pub fn of_info(info: &gio::FileInfo) -> Vec<String> {
    match info.attribute_string(GIO_ATTR) {
        Some(s) if !s.is_empty() => parse(&String::from_utf8_lossy(&unescape(&s))),
        _ => Vec::new(),
    }
}

/// The colour tags among `tags`, in Finder's order: [(name, hex)].
pub fn colors(tags: &[String]) -> Vec<(&'static str, &'static str)> {
    COLORS.iter().filter(|(n, _)| tags.iter().any(|t| t == n)).copied().collect()
}

fn c_path(path: &Path) -> Option<CString> {
    CString::new(path.as_os_str().as_bytes()).ok()
}

pub fn read(path: &Path) -> Vec<String> {
    let (Some(p), Ok(name)) = (c_path(path), CString::new(XATTR)) else { return Vec::new() };
    let mut buf = vec![0u8; 4096];
    let n = unsafe { libc::lgetxattr(p.as_ptr(), name.as_ptr(), buf.as_mut_ptr().cast(), buf.len()) };
    if n <= 0 {
        return Vec::new();
    }
    parse(&String::from_utf8_lossy(&buf[..n as usize]))
}

/// Set a file's tags (none: the attribute goes). An error when the disk
/// can't keep them (FAT, some network shares).
pub fn write(path: &Path, tags: &[String]) -> std::io::Result<()> {
    let tags = parse(&tags.join(","));
    let p = c_path(path).ok_or_else(|| std::io::Error::from(std::io::ErrorKind::InvalidInput))?;
    let name = CString::new(XATTR).unwrap();
    if !tags.is_empty() {
        let v = tags.join(",");
        if unsafe { libc::lsetxattr(p.as_ptr(), name.as_ptr(), v.as_ptr().cast(), v.len(), 0) } != 0 {
            return Err(std::io::Error::last_os_error());
        }
    } else if unsafe { libc::lremovexattr(p.as_ptr(), name.as_ptr()) } != 0 {
        let e = std::io::Error::last_os_error();
        if !matches!(e.raw_os_error(), Some(libc::ENODATA) | Some(libc::ENOTSUP)) {
            return Err(e); // ENODATA: had none
        }
    }
    index_set(path, &tags);
    Ok(())
}

/// "all", "some" or "none" of `paths` have `tag` (the menu's dots).
pub fn state(paths: &[PathBuf], tag: &str) -> &'static str {
    let have: Vec<bool> = paths.iter().map(|p| read(p).iter().any(|t| t == tag)).collect();
    if !have.is_empty() && have.iter().all(|h| *h) {
        "all"
    } else if have.iter().any(|h| *h) {
        "some"
    } else {
        "none"
    }
}

/// Add `tag` to (on) or remove it from each file; the ones that failed.
pub fn toggle(paths: &[PathBuf], tag: &str, on: bool) -> Vec<(PathBuf, std::io::Error)> {
    let mut failed = Vec::new();
    for p in paths {
        let cur = read(p);
        let new: Vec<String> = if on {
            if cur.iter().any(|t| t == tag) { cur.clone() } else { [cur.clone(), vec![tag.to_string()]].concat() }
        } else {
            cur.iter().filter(|t| *t != tag).cloned().collect()
        };
        if new == cur {
            continue;
        }
        if let Err(e) = write(p, &new) {
            failed.push((p.clone(), e));
        }
    }
    failed
}

// -- the sidebar's lists ----------------------------------------------------------------------
pub fn uri(tag: &str) -> String {
    format!("{PREFIX}{tag}")
}

/// The tag a sidebar location lists ("" when it isn't one).
pub fn tag_of(location: &str) -> &str {
    location.strip_prefix(PREFIX).unwrap_or("")
}

fn load() -> Map<String, Value> {
    std::fs::read_to_string(index_path()).ok().and_then(|t| serde_json::from_str::<Value>(&t).ok()).and_then(|v| v.as_object().cloned()).unwrap_or_default()
}

fn save(data: &Map<String, Value>) {
    let _ = sonata_config::atomic_write(&index_path(), serde_json::to_string(data).unwrap_or_default().as_bytes(), true);
}

fn as_tags(v: &Value) -> Vec<String> {
    v.as_array().map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect()).unwrap_or_default()
}

fn index_set(path: &Path, tags: &[String]) {
    let _g = LOCK.lock().unwrap();
    let mut data = load();
    let key = path.display().to_string();
    if !tags.is_empty() {
        data.insert(key, Value::from(tags.to_vec()));
    } else if data.remove(&key).is_none() {
        return;
    }
    save(&data);
}

/// Paths of the items with `tag` (checked on the disk; the gone ones leave the index).
pub fn tagged(tag: &str) -> Vec<PathBuf> {
    let _g = LOCK.lock().unwrap();
    let mut data = load();
    let mut out = Vec::new();
    let mut changed = false;
    for path in data.keys().cloned().collect::<Vec<_>>() {
        let p = PathBuf::from(&path);
        let now = if p.symlink_metadata().is_ok() { read(&p) } else { Vec::new() };
        if now != as_tags(&data[&path]) {
            changed = true;
            if now.is_empty() {
                data.remove(&path);
            } else {
                data.insert(path.clone(), Value::from(now.clone()));
            }
        }
        if now.iter().any(|t| t == tag) {
            out.push(p);
        }
    }
    if changed {
        save(&data);
    }
    out
}

/// Files moved or renamed items: [(was, now)] -- the index follows them
/// (and what's inside a moved folder).
pub fn moved(pairs: &[(PathBuf, PathBuf)]) {
    if pairs.is_empty() {
        return;
    }
    let _g = LOCK.lock().unwrap();
    let mut data = load();
    if data.is_empty() {
        return;
    }
    let mut changed = false;
    for (was, now) in pairs {
        let (was, now) = (was.display().to_string(), now.display().to_string());
        let inside = format!("{}/", was.trim_end_matches('/'));
        for path in data.keys().cloned().collect::<Vec<_>>() {
            if path == was || path.starts_with(&inside) {
                let v = data.remove(&path).unwrap();
                data.insert(format!("{now}{}", &path[was.len()..]), v);
                changed = true;
            }
        }
    }
    if changed {
        save(&data);
    }
}

/// The paths of file pairs (moved, renamed).
pub fn moved_files(pairs: &[(gio::File, gio::File)]) {
    let p: Vec<(PathBuf, PathBuf)> = pairs.iter().filter_map(|(a, b)| Some((a.path()?, b.path()?))).collect();
    moved(&p);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_and_colors() {
        assert_eq!(parse("Red, Work,,Red"), ["Red", "Work"]);
        assert!(parse("").is_empty());
        assert_eq!(colors(&["Work".into(), "Blue".into(), "Red".into()]), [("Red", "#ff3b30"), ("Blue", "#007aff")]);
        assert_eq!(unescape("Trabalho \\xc3\\xa9"), "Trabalho é".as_bytes());
        assert_eq!(tag_of(&uri("Red")), "Red");
        assert_eq!(tag_of("file:///"), "");
    }

    /// Needs a disk with user xattrs (tmpfs has them since Linux 6.6); skipped otherwise.
    #[test]
    fn toggle_keeps_other_tags_follows_moves_and_drops_the_gone() {
        let d = tempfile::tempdir().unwrap();
        let (a, b) = (d.path().join("a"), d.path().join("b"));
        std::fs::write(&a, "").unwrap();
        std::fs::write(&b, "").unwrap();
        if write(&a, &["Trabalho é".into()]).is_err() {
            return; // no xattrs here
        }
        assert!(toggle(&[a.clone(), b.clone()], "Red", true).is_empty());
        assert_eq!(read(&a), ["Trabalho é", "Red"]);
        assert_eq!(state(&[a.clone(), b.clone()], "Red"), "all");
        toggle(&[b.clone()], "Red", false);
        assert_eq!(state(&[a.clone(), b.clone()], "Red"), "some");
        assert!(read(&b).is_empty()); // none left: the attribute goes
        assert_eq!(tagged("Red"), [a.clone()]);
        let new = d.path().join("moved");
        std::fs::rename(&a, &new).unwrap();
        moved(&[(a.clone(), new.clone())]);
        assert_eq!(tagged("Red"), [new.clone()]);
        std::fs::remove_file(&new).unwrap();
        assert!(tagged("Red").is_empty());
    }
}
