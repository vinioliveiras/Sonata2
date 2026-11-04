//! Finder search (files/search.py): every file under a folder whose name
//! contains all the typed words (case- and accent-insensitive), found by a
//! background thread and delivered in batches so the first hits show at
//! once. Hidden folders and files are skipped (like Spotlight). New
//! searches cancel old ones. Criteria too: a kind, when it was last
//! modified, and the words in the file's contents (text files up to
//! CONTENT_MAX).

use crate::folder::{self, ATTRS};
use gio::prelude::*;
use sonata_i18n::n_;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{mpsc, Arc};

pub const LIMIT: usize = 2000;
const BATCH: usize = 50;
const SKIP: [&str; 5] = ["node_modules", "__pycache__", ".git", "proc", "sys"];
pub const CONTENT_MAX: u64 = 4 << 20;
pub const KINDS: [(&str, &str); 7] = [("any", n_!("Any Kind")), ("folder", n_!("Folders")), ("document", n_!("Documents")), ("image", n_!("Pictures")), ("video", n_!("Movies")), ("audio", n_!("Music")), ("archive", n_!("Archives"))];
pub const DATES: [(&str, &str, i64); 5] = [("any", n_!("Any Time"), 0), ("today", n_!("Today"), 0), ("week", n_!("Last 7 Days"), 7), ("month", n_!("Last 30 Days"), 30), ("year", n_!("This Year"), 0)];
const DOC_TYPES: [&str; 8] = ["text/", "application/pdf", "application/msword", "application/vnd.", "application/rtf", "application/x-tex", "application/json", "application/xml"];
const ARCHIVES: [&str; 9] = [".zip", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".zst", ".7z", ".rar"];

pub fn kind_of(name: &str, is_dir: bool) -> &'static str {
    if is_dir {
        return "folder";
    }
    let low = name.to_lowercase();
    if ARCHIVES.iter().any(|a| low.ends_with(a)) {
        return "archive";
    }
    // by the name only, as Python's mimetypes did
    let (ct, uncertain) = gio::content_type_guess(Some(name), &[]);
    let mt = if uncertain { String::new() } else { gio::content_type_get_mime_type(&ct).map(|m| m.to_string()).unwrap_or_default() };
    for k in ["image", "video", "audio"] {
        if mt.starts_with(&format!("{k}/")) {
            return k;
        }
    }
    if DOC_TYPES.iter().any(|d| mt.starts_with(d)) {
        return "document";
    }
    "other"
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// The oldest modification time `date_key` keeps (0: any).
pub fn since(date_key: &str, now_s: Option<f64>) -> f64 {
    let t = now_s.unwrap_or_else(now);
    let local = glib::DateTime::from_unix_local(t as i64).ok();
    let start = |y: i32, m: i32, d: i32| glib::DateTime::from_local(y, m, d, 0, 0, 0.0).map(|d| d.to_unix() as f64).unwrap_or(0.0);
    match (date_key, local) {
        ("today", Some(l)) => start(l.year(), l.month(), l.day_of_month()),
        ("year", Some(l)) => start(l.year(), 1, 1),
        _ => DATES.iter().find(|d| d.0 == date_key).map(|d| d.2).filter(|d| *d > 0).map_or(0.0, |d| t - d as f64 * 86400.0),
    }
}

/// Case and accents folded away ("Éclair" -> "eclair").
pub fn fold(s: &str) -> String {
    glib::normalize(s.to_lowercase(), glib::NormalizeMode::All).chars().filter(|c| !is_combining(*c)).collect()
}

fn is_combining(c: char) -> bool {
    matches!(c as u32, 0x0300..=0x036F | 0x1AB0..=0x1AFF | 0x1DC0..=0x1DFF | 0x20D0..=0x20FF | 0xFE20..=0xFE2F)
}

/// All the words in a text file's contents (binary and big files: no).
pub fn contents_match(path: &Path, words: &[String]) -> bool {
    if std::fs::metadata(path).map(|m| m.len()).unwrap_or(u64::MAX) > CONTENT_MAX {
        return false;
    }
    let Ok(data) = std::fs::read(path) else { return false };
    if data[..data.len().min(4096)].contains(&0) {
        return false; // binary
    }
    let text = fold(&String::from_utf8_lossy(&data));
    words.iter().all(|w| text.contains(w.as_str()))
}

#[derive(Default)]
pub struct Search {
    token: Option<Arc<AtomicBool>>,
}

pub struct Criteria {
    pub kind: String,
    pub date: String,
    pub contents: bool,
}

/// The walk itself (a thread): paths found, sent in batches; true at the end when cut at LIMIT.
fn walk(root: PathBuf, words: Vec<String>, c: Criteria, oldest: f64, stop: Arc<AtomicBool>, tx: mpsc::Sender<Result<Vec<PathBuf>, bool>>) {
    let (mut found, mut batch) = (0, Vec::new());
    let mut stack = vec![root];
    'outer: while let Some(d) = stack.pop() {
        if stop.load(Ordering::SeqCst) {
            return;
        }
        let Ok(rd) = std::fs::read_dir(&d) else { continue };
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if name.starts_with('.') || name.ends_with('~') {
                continue;
            }
            let ft = e.file_type().ok();
            let is_dir = ft.is_some_and(|t| t.is_dir());
            if is_dir && !SKIP.contains(&name.as_str()) {
                stack.push(e.path());
            }
            if c.kind != "any" && kind_of(&name, is_dir) != c.kind {
                continue;
            }
            if oldest > 0.0 {
                use std::os::unix::fs::MetadataExt;
                match std::fs::symlink_metadata(e.path()) {
                    Ok(m) if (m.mtime() as f64) >= oldest => {}
                    _ => continue,
                }
            }
            let n = fold(&name);
            let hit = words.iter().all(|w| n.contains(w.as_str())) || (c.contents && !is_dir && ft.is_some_and(|t| t.is_file()) && contents_match(&e.path(), &words));
            if hit {
                batch.push(e.path());
                found += 1;
                if batch.len() >= BATCH {
                    let _ = tx.send(Ok(std::mem::take(&mut batch)));
                }
                if found >= LIMIT {
                    break 'outer;
                }
            }
        }
    }
    if !batch.is_empty() {
        let _ = tx.send(Ok(batch));
    }
    let _ = tx.send(Err(found >= LIMIT));
}

impl Search {
    pub fn cancel(&mut self) {
        if let Some(t) = self.token.take() {
            t.store(true, Ordering::SeqCst);
        }
    }

    /// on_batch(items) and on_done(truncated) run on the main loop.
    pub fn start(&mut self, root: &str, query: &str, on_batch: impl Fn(Vec<gio::FileInfo>) + 'static, on_done: impl FnOnce(bool) + 'static, c: Criteria) {
        self.cancel();
        let words: Vec<String> = fold(query).split_whitespace().map(str::to_string).collect();
        if words.is_empty() {
            return;
        }
        let oldest = since(&c.date, None);
        let stop = Arc::new(AtomicBool::new(false));
        self.token = Some(stop.clone());
        let (tx, rx) = mpsc::channel();
        let (s2, root) = (stop.clone(), PathBuf::from(root));
        std::thread::spawn(move || walk(root, words, c, oldest, s2, tx));
        let mut on_done = Some(on_done);
        glib::timeout_add_local(std::time::Duration::from_millis(30), move || {
            for msg in rx.try_iter() {
                if stop.load(Ordering::SeqCst) {
                    return glib::ControlFlow::Break;
                }
                match msg {
                    Ok(paths) => {
                        // a FileInfo can't cross threads: read here
                        let items: Vec<gio::FileInfo> = paths
                            .iter()
                            .filter_map(|p| {
                                let f = gio::File::for_path(p);
                                let info = f.query_info(ATTRS, gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok()?;
                                info.set_attribute_object("sonata::file", &f);
                                folder::sort_key(&info);
                                Some(info)
                            })
                            .collect();
                        on_batch(items);
                    }
                    Err(truncated) => {
                        if let Some(d) = on_done.take() {
                            d(truncated);
                        }
                        return glib::ControlFlow::Break;
                    }
                }
            }
            glib::ControlFlow::Continue
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kinds_folding_and_dates() {
        assert_eq!(kind_of("a.jpg", false), "image");
        assert_eq!(kind_of("a.tar.gz", false), "archive");
        assert_eq!(kind_of("x", true), "folder");
        assert_eq!(kind_of("notes.txt", false), "document");
        assert_eq!(fold("Éclair Ção"), "eclair cao");
        assert_eq!(since("any", Some(1e9)), 0.0);
        assert_eq!(since("week", Some(1e9)), 1e9 - 7.0 * 86400.0);
        assert!(since("today", Some(1e9)) <= 1e9 && since("today", Some(1e9)) > 1e9 - 86400.0);
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("a.txt"), "Olá Mundo").unwrap();
        std::fs::write(d.path().join("b.bin"), b"ola\0mundo").unwrap();
        assert!(contents_match(&d.path().join("a.txt"), &["ola".into(), "mundo".into()]));
        assert!(!contents_match(&d.path().join("b.bin"), &["ola".into()]));
    }

    #[test]
    fn walk_finds_names_skips_hidden() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("sub/.hidden")).unwrap();
        std::fs::write(d.path().join("sub/Relatório final.txt"), "").unwrap();
        std::fs::write(d.path().join("sub/.hidden/relatorio.txt"), "").unwrap();
        let (tx, rx) = mpsc::channel();
        walk(d.path().into(), vec!["relatorio".into()], Criteria { kind: "any".into(), date: "any".into(), contents: false }, 0.0, Arc::default(), tx);
        let found: Vec<PathBuf> = rx.iter().filter_map(|m| m.ok()).flatten().collect();
        assert_eq!(found, [d.path().join("sub/Relatório final.txt")]);
    }
}
