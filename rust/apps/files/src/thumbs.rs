//! Thumbnails for images and videos (files/thumbs.py; Finder shows the
//! picture instead of the file icon), and Windows programs' own icons.
//!
//! Uses the freedesktop thumbnail cache (~/.cache/thumbnails/large, shared
//! with GNOME/KDE apps): an up-to-date thumbnail is loaded; otherwise one is
//! made -- images with GdkPixbuf (Preview's loader: WebP, camera RAW...),
//! videos with ffmpegthumbnailer or ffmpeg -- and saved there with
//! Thumb::URI / Thumb::MTime. Work runs on 2 background threads, only for
//! items being shown; results are kept in a small in-memory cache.

use gtk::prelude::*;
use gtk::gdk;
use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Mutex, OnceLock};

/// "large" thumbnails
const SIZE: i32 = 256;
const MAX_IMAGE_BYTES: i64 = 150 * 1000 * 1000;
/// textures kept in RAM, at most...
const MEMORY: usize = 400;
/// ...and this many bytes of pixels (a folder's worth of 256 px still fits)
const MEMORY_BYTES: usize = 32 << 20;
const NONE_KEEP: usize = 20000;

type Key = (String, u64, u32, i64);
type Callback = Box<dyn FnOnce(&gdk::Texture)>;

fn cache_dir() -> PathBuf {
    glib::user_cache_dir().join("thumbnails/large")
}

fn fail_dir() -> PathBuf {
    glib::user_cache_dir().join("thumbnails/fail/sonata2")
}

pub fn wanted(content_type: &str) -> bool {
    content_type.starts_with("image/") || content_type.starts_with("video/") || crate::peicon::is_program(content_type)
}

struct Job {
    key: Key,
    uri: String,
    path: PathBuf,
    mtime: u64,
    ct: String,
    size: i64,
    existing: Option<String>,
}

#[derive(Default)]
struct State {
    mem: VecDeque<(Key, gdk::Texture)>,
    none: VecDeque<Key>,
    pending: HashMap<Key, Vec<Callback>>,
}

thread_local! {
    static STATE: RefCell<State> = RefCell::default();
}

static POOL: OnceLock<Mutex<mpsc::Sender<Job>>> = OnceLock::new();
static DONE: OnceLock<Mutex<mpsc::Receiver<(Key, Option<PathBuf>)>>> = OnceLock::new();

/// The two workers (started once); results are picked up on the main loop.
fn pool() -> mpsc::Sender<Job> {
    POOL.get_or_init(|| {
        let (tx, rx) = mpsc::channel::<Job>();
        let (dtx, drx) = mpsc::channel();
        let rx = std::sync::Arc::new(Mutex::new(rx));
        for _ in 0..2 {
            let (rx, dtx) = (rx.clone(), dtx.clone());
            std::thread::spawn(move || loop {
                let job = match rx.lock().unwrap().recv() {
                    Ok(j) => j,
                    Err(_) => return,
                };
                let thumb = work(&job);
                let _ = dtx.send((job.key, thumb));
            });
        }
        let _ = DONE.set(Mutex::new(drx));
        glib::timeout_add_local(std::time::Duration::from_millis(40), || {
            let got: Vec<(Key, Option<PathBuf>)> = DONE.get().map(|r| r.lock().unwrap().try_iter().collect()).unwrap_or_default();
            for (k, p) in got {
                deliver(k, p.and_then(|p| gdk::Texture::from_filename(p).ok()));
            }
            glib::ControlFlow::Continue
        });
        Mutex::new(tx)
    })
    .lock()
    .unwrap()
    .clone()
}

/// callback(texture) on the main loop when a thumbnail is ready (never
/// called when there is none).
pub fn request(info: &gio::FileInfo, file: &gio::File, callback: impl FnOnce(&gdk::Texture) + 'static) {
    let Some(path) = file.path() else { return };
    let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
    if !wanted(&ct) {
        return;
    }
    let mtime = info.attribute_uint64("time::modified");
    // an edit makes a new key: its seconds, microseconds and size (two edits
    // in the same second kept the old picture)
    let key: Key = (path.display().to_string(), mtime, info.attribute_uint32("time::modified-usec"), info.size());
    let mut cb: Option<Callback> = Some(Box::new(callback));
    let queue = STATE.with(|s| {
        let mut s = s.borrow_mut();
        if let Some(i) = s.mem.iter().position(|(k, _)| *k == key) {
            let e = s.mem.remove(i).unwrap();
            (cb.take().unwrap())(&e.1);
            s.mem.push_back(e);
            return false;
        }
        if s.none.contains(&key) {
            return false;
        }
        if let Some(list) = s.pending.get_mut(&key) {
            list.push(cb.take().unwrap());
            return false;
        }
        s.pending.insert(key.clone(), vec![cb.take().unwrap()]);
        true
    });
    if queue {
        let existing = info.attribute_byte_string("thumbnail::path").map(|s| s.to_string());
        let _ = pool().send(Job { key, uri: file.uri().to_string(), path, mtime, ct, size: info.size(), existing });
    }
}

fn work(j: &Job) -> Option<PathBuf> {
    // GIO's thumbnail::path names the cached file even when it is out of
    // date: used only when it is this version's
    if let Some(e) = j.existing.as_deref().filter(|e| valid(Path::new(e), j.mtime, Some(j.size))) {
        return Some(e.into());
    }
    if let Some(c) = cached(&j.uri, j.mtime, Some(j.size)) {
        return Some(c);
    }
    if failed(&j.uri, j.mtime) {
        return None;
    }
    make(j)
}

fn bytes_of(t: &gdk::Texture) -> usize {
    (t.width() * t.height() * 4) as usize
}

fn deliver(key: Key, tex: Option<gdk::Texture>) {
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        let cbs = s.pending.remove(&key).unwrap_or_default();
        let Some(tex) = tex else {
            s.none.push_back(key);
            while s.none.len() > NONE_KEEP {
                s.none.pop_front();
            }
            return;
        };
        s.mem.push_back((key, tex.clone()));
        let mut used: usize = s.mem.iter().map(|(_, t)| bytes_of(t)).sum();
        while s.mem.len() > 1 && (s.mem.len() > MEMORY || used > MEMORY_BYTES) {
            if let Some((_, old)) = s.mem.pop_front() {
                used -= bytes_of(&old);
            }
        }
        drop(s);
        for cb in cbs {
            cb(&tex);
        }
    });
}

fn name(uri: &str) -> String {
    format!("{}.png", glib::compute_checksum_for_string(glib::ChecksumType::Md5, uri).unwrap_or_default())
}

/// A PNG's tEXt entries, read from its chunks before the picture: no
/// decoding (it was decoded once to check it, then again to show it).
pub fn png_text(p: &Path) -> HashMap<String, String> {
    use std::io::{Read, Seek, SeekFrom};
    let mut out = HashMap::new();
    let Ok(mut f) = std::fs::File::open(p) else { return out };
    let mut magic = [0u8; 8];
    if f.read_exact(&mut magic).is_err() || &magic != b"\x89PNG\r\n\x1a\n" {
        return out;
    }
    loop {
        let mut head = [0u8; 8];
        if f.read_exact(&mut head).is_err() {
            break;
        }
        let n = u32::from_be_bytes([head[0], head[1], head[2], head[3]]) as usize;
        let kind = &head[4..];
        if kind == b"IDAT" || kind == b"IEND" || n > 1 << 20 {
            break; // (text comes before the picture in thumbnails)
        }
        let mut data = vec![0u8; n];
        if f.read_exact(&mut data).is_err() || f.seek(SeekFrom::Current(4)).is_err() {
            break; // (and its CRC)
        }
        if kind == b"tEXt" {
            if let Some(i) = data.iter().position(|b| *b == 0) {
                let latin = |b: &[u8]| b.iter().map(|c| *c as char).collect::<String>();
                out.insert(latin(&data[..i]), latin(&data[i + 1..]));
            }
        }
    }
    out
}

/// A cached thumbnail of this version of the file: its Thumb::MTime, and
/// its Thumb::Size when it has one (an edit within the same second).
pub fn valid(p: &Path, mtime: u64, size: Option<i64>) -> bool {
    if !p.exists() {
        return false;
    }
    let t = png_text(p);
    if t.get("Thumb::MTime") != Some(&mtime.to_string()) {
        return false;
    }
    match (size, t.get("Thumb::Size")) {
        (Some(s), Some(k)) => *k == s.to_string(),
        _ => true,
    }
}

fn cached(uri: &str, mtime: u64, size: Option<i64>) -> Option<PathBuf> {
    let p = cache_dir().join(name(uri));
    valid(&p, mtime, size).then_some(p)
}

fn failed(uri: &str, mtime: u64) -> bool {
    fail_dir().join(format!("{}.{mtime}", name(uri))).exists()
}

fn mark_failed(uri: &str, mtime: u64) {
    let _ = std::fs::create_dir_all(fail_dir());
    let _ = std::fs::write(fail_dir().join(format!("{}.{mtime}", name(uri))), b"");
}

fn make(j: &Job) -> Option<PathBuf> {
    let pb = if j.ct.starts_with("image/") && j.size <= MAX_IMAGE_BYTES {
        sonata_preview::imageload::pixbuf_at_scale(&j.path.display().to_string(), SIZE)
    } else if j.ct.starts_with("video/") {
        video_frame(&j.path)
    } else if crate::peicon::is_program(&j.ct) {
        crate::peicon::pixbuf(&j.path, SIZE) // a Windows program: its own icon
    } else {
        None
    };
    let Some(pb) = pb else {
        mark_failed(&j.uri, j.mtime);
        return None;
    };
    let dir = cache_dir();
    let _ = std::fs::create_dir_all(&dir);
    let out = dir.join(name(&j.uri));
    let tmp = dir.join(format!("{}.sonata{}.tmp", name(&j.uri), std::process::id()));
    let (mt, sz) = (j.mtime.to_string(), j.size.to_string());
    pb.savev(&tmp, "png", &[("tEXt::Thumb::URI", j.uri.as_str()), ("tEXt::Thumb::MTime", &mt), ("tEXt::Thumb::Size", &sz), ("tEXt::Software", "Sonata Files")]).ok()?;
    std::fs::rename(&tmp, &out).ok()?;
    Some(out)
}

fn video_frame(path: &Path) -> Option<gtk::gdk_pixbuf::Pixbuf> {
    let out = tempfile_path();
    let p = path.display().to_string();
    let o = out.display().to_string();
    let s = SIZE.to_string();
    let vf = format!("scale={SIZE}:{SIZE}:force_original_aspect_ratio=decrease");
    let run = |args: &[&str]| sonata_backend::run::run(args, 20);
    if sonata_backend::procfs::which("ffmpegthumbnailer").is_some() {
        run(&["ffmpegthumbnailer", "-i", &p, "-o", &o, "-s", &s, "-t", "10%"]);
    } else if sonata_backend::procfs::which("ffmpeg").is_some() {
        run(&["ffmpeg", "-v", "quiet", "-y", "-ss", "3", "-i", &p, "-frames:v", "1", "-vf", &vf, &o]);
        if std::fs::metadata(&out).map(|m| m.len()).unwrap_or(0) == 0 {
            // a very short clip: its first frame instead
            run(&["ffmpeg", "-v", "quiet", "-y", "-ss", "0", "-i", &p, "-frames:v", "1", "-vf", &vf, &o]);
        }
    } else {
        return None;
    }
    let pb = if std::fs::metadata(&out).map(|m| m.len()).unwrap_or(0) > 0 { gtk::gdk_pixbuf::Pixbuf::from_file(&out).ok() } else { None };
    let _ = std::fs::remove_file(&out);
    pb
}

fn tempfile_path() -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};
    static N: AtomicU64 = AtomicU64::new(0);
    std::env::temp_dir().join(format!("sonata2-thumb-{}-{}.png", std::process::id(), N.fetch_add(1, Ordering::SeqCst)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cached_thumbnails_checked_by_their_text() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("t.png");
        let pb = gtk::gdk_pixbuf::Pixbuf::new(gtk::gdk_pixbuf::Colorspace::Rgb, false, 8, 2, 2).unwrap();
        pb.savev(&p, "png", &[("tEXt::Thumb::MTime", "42"), ("tEXt::Thumb::Size", "7")]).unwrap();
        assert_eq!(png_text(&p).get("Thumb::MTime").map(String::as_str), Some("42"));
        assert!(valid(&p, 42, Some(7)));
        assert!(!valid(&p, 42, Some(8))); // an edit in the same second
        assert!(!valid(&p, 41, None));
        assert!(wanted("image/png") && wanted("video/mp4") && !wanted("text/plain"));
        assert_eq!(name("file:///a"), format!("{}.png", glib::compute_checksum_for_string(glib::ChecksumType::Md5, "file:///a").unwrap()));
    }
}
