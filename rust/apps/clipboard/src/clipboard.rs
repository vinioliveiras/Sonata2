//! Clipboard history (shell/clipboard.py; a Linux extra macOS doesn't have).
//!
//! Watches the Wayland clipboard with `wl-paste --watch` (wl-clipboard,
//! wlr-data-control), keeps the last copies -- text and images -- in memory
//! only (never on disk), and puts a chosen one back with `wl-copy`.

use gtk::prelude::*;
use gtk::{gdk, gdk_pixbuf};
use sonata_i18n::tr;
use std::cell::RefCell;
use std::rc::{Rc, Weak};

pub const KEEP: usize = 15;
/// larger copies (a whole file...) are not kept
pub const MAX_BYTES: usize = 64 * 1024;
/// PNG bytes
pub const MAX_IMAGE: usize = 16 * 1024 * 1024;
/// images are big: only the latest few
pub const KEEP_IMAGES: usize = 5;
/// for each copy: "I" + the PNG in base64, or "T" + the text; then a NUL
pub const WATCH: &str = "cat >/dev/null; t=$(wl-paste --list-types 2>/dev/null); case \"$t\" in \
*image/png*) printf I; wl-paste --no-newline --type image/png 2>/dev/null | base64 -w0 ;; \
*) printf T; wl-paste --no-newline --type text 2>/dev/null ;; esac; printf '\\0'";

/// An image copy. Reads as its description ("Image · 800×600") wherever a
/// text copy is shown; `png` holds the picture, texture() a thumbnail.
pub struct ClipImage {
    pub png: Vec<u8>,
    pub size: (u32, u32),
    pub key: String,
    texture: RefCell<Option<gdk::Texture>>,
}

impl ClipImage {
    pub fn new(png: Vec<u8>) -> Self {
        let mut size = (0, 0);
        if png.len() >= 24 && png[..8] == *b"\x89PNG\r\n\x1a\n" {
            let be = |r: &[u8]| u32::from_be_bytes([r[0], r[1], r[2], r[3]]);
            size = (be(&png[16..20]), be(&png[20..24]));
        }
        let key = glib::compute_checksum_for_data(glib::ChecksumType::Sha1, &png).map(|s| s.to_string()).unwrap_or_default();
        Self { png, size, key, texture: RefCell::default() }
    }

    pub fn label(&self) -> String {
        let (w, h) = self.size;
        if w == 0 {
            return tr!("Image");
        }
        sonata_i18n::fmt(&tr!("Image · {w}×{h}"), &[("w", &w.to_string()), ("h", &h.to_string())])
    }

    /// A thumbnail at most max_w x max_h (made once, then kept).
    pub fn texture(&self, max_w: i32, max_h: i32) -> Option<gdk::Texture> {
        if self.texture.borrow().is_none() {
            let loader = gdk_pixbuf::PixbufLoader::with_type("png").ok()?;
            let (w, h) = self.size;
            if w > 0 && h > 0 {
                let s = 1f64.min(max_w as f64 / w as f64).min(max_h as f64 / h as f64);
                loader.set_size(((w as f64 * s).round() as i32).max(1), ((h as f64 * s).round() as i32).max(1));
            }
            let written = loader.write(&self.png);
            let closed = loader.close();
            if written.is_err() || closed.is_err() {
                return None;
            }
            let pixbuf = loader.pixbuf()?;
            #[allow(deprecated)] // (GTK 4.12: no GdkTexture from a scaled PNG otherwise)
            let tex = gdk::Texture::for_pixbuf(&pixbuf);
            self.texture.replace(Some(tex));
        }
        self.texture.borrow().clone()
    }
}

/// One copy: text or a picture.
#[derive(Clone)]
pub enum ClipItem {
    Text(String),
    Image(Rc<ClipImage>),
}

impl ClipItem {
    /// What a row says: the text, or the image's description.
    pub fn label(&self) -> String {
        match self {
            Self::Text(t) => t.clone(),
            Self::Image(i) => i.label(),
        }
    }
}

/// Equal text is the same copy, equal bytes the same picture; a text is
/// never the same as an image.
impl PartialEq for ClipItem {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Text(a), Self::Text(b)) => a == b,
            (Self::Image(a), Self::Image(b)) => a.key == b.key,
            _ => false,
        }
    }
}

impl std::fmt::Debug for ClipItem {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Self::Text(t) => write!(f, "Text({t:?})"),
            Self::Image(i) => write!(f, "Image({}x{})", i.size.0, i.size.1),
        }
    }
}

/// What the watch script prints, cut into copies: each one is "T" + text
/// or "I" + base64 PNG, then a NUL; the reads cut them anywhere.
#[derive(Default)]
pub struct Stream {
    buf: Vec<u8>,
}

impl Stream {
    pub fn feed(&mut self, chunk: &[u8]) -> Vec<ClipItem> {
        let mut out = Vec::new();
        // only the new bytes can hold a new NUL
        let mut scan = self.buf.len();
        self.buf.extend_from_slice(chunk);
        let mut start = 0;
        while let Some(i) = self.buf[scan..].iter().position(|&b| b == 0) {
            let end = scan + i;
            let item = &self.buf[start..end];
            let (kind, data) = item.split_at(item.len().min(1));
            if kind == b"I" && !data.is_empty() {
                let png = std::str::from_utf8(data).map(glib::base64_decode).unwrap_or_default();
                if !png.is_empty() && png.len() <= MAX_IMAGE {
                    out.push(ClipItem::Image(Rc::new(ClipImage::new(png))));
                }
            } else if kind == b"T" && !data.is_empty() && data.len() <= MAX_BYTES {
                out.push(ClipItem::Text(String::from_utf8_lossy(data).into_owned()));
            }
            start = end + 1;
            scan = start;
        }
        self.buf.drain(..start);
        if self.buf.len() > MAX_IMAGE * 2 {
            self.buf.clear();
        }
        out
    }
}

type Listener = Rc<dyn Fn()>;

pub struct History {
    /// newest first
    items: RefCell<Vec<ClipItem>>,
    listeners: RefCell<Vec<Listener>>,
    pub available: bool,
    proc: RefCell<Option<gio::Subprocess>>,
}

fn which(cmd: &str) -> bool {
    glib::find_program_in_path(cmd).is_some()
}

impl History {
    /// `watch`: follow the clipboard (wl-paste --watch) from now on.
    pub fn new(watch: bool) -> Rc<Self> {
        let h = Rc::new(Self { items: RefCell::default(), listeners: RefCell::default(), available: which("wl-paste") && which("wl-copy"), proc: RefCell::default() });
        if watch && h.available {
            h.read(&["wl-paste", "--watch", "sh", "-c", WATCH], true);
        }
        h
    }

    /// A history that never looks at the clipboard (tests).
    pub fn unwatched() -> Rc<Self> {
        Rc::new(Self { items: RefCell::default(), listeners: RefCell::default(), available: false, proc: RefCell::default() })
    }

    /// What is on the clipboard now, added as a copy (once, without the
    /// watcher: what the panel reads when nothing watches).
    pub fn snapshot(self: &Rc<Self>) {
        if self.available {
            self.read(&["sh", "-c", WATCH.trim_start_matches("cat >/dev/null; ")], false);
        }
    }

    pub fn items(&self) -> Vec<ClipItem> {
        self.items.borrow().clone()
    }

    pub fn is_empty(&self) -> bool {
        self.items.borrow().is_empty()
    }

    pub fn connect(&self, f: impl Fn() + 'static) {
        self.listeners.borrow_mut().push(Rc::new(f));
    }

    fn changed(&self) {
        let listeners = self.listeners.borrow().clone();
        for cb in listeners {
            cb();
        }
    }

    /// The script's output read on the main loop, a copy at a time.
    fn read(self: &Rc<Self>, argv: &[&str], keep: bool) {
        let argv: Vec<&std::ffi::OsStr> = argv.iter().map(std::ffi::OsStr::new).collect();
        let Ok(p) = gio::Subprocess::newv(&argv, gio::SubprocessFlags::STDOUT_PIPE | gio::SubprocessFlags::STDERR_SILENCE) else { return };
        let Some(out) = p.stdout_pipe() else { return };
        read_more(out, Stream::default(), Rc::downgrade(self));
        if keep {
            self.proc.replace(Some(p));
        }
    }

    pub fn add(&self, item: ClipItem) {
        if let ClipItem::Text(t) = &item {
            if t.trim().is_empty() {
                return;
            }
        }
        {
            let mut items = self.items.borrow_mut();
            items.retain(|t| *t != item);
            items.insert(0, item);
            items.truncate(KEEP);
            let mut images = 0;
            items.retain(|t| {
                if matches!(t, ClipItem::Image(_)) {
                    images += 1;
                    return images <= KEEP_IMAGES;
                }
                true
            });
        }
        self.changed();
    }

    /// Put a copy back on the clipboard (wl-copy, in a session of its own:
    /// it stays to serve the clipboard after this process).
    pub fn copy(&self, item: &ClipItem) {
        use std::io::Write;
        use std::os::unix::process::CommandExt;
        let (args, data): (&[&str], Vec<u8>) = match item {
            ClipItem::Image(i) => (&["--type", "image/png"], i.png.clone()),
            ClipItem::Text(t) => (&[], t.clone().into_bytes()),
        };
        let mut cmd = std::process::Command::new("wl-copy");
        cmd.args(args).stdin(std::process::Stdio::piped()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null());
        // SAFETY: setsid() is async-signal-safe (start_new_session=True)
        unsafe {
            cmd.pre_exec(|| {
                libc::setsid();
                Ok(())
            });
        }
        let Ok(mut child) = cmd.spawn() else { return };
        // written, then reaped, off the main loop: a large picture fills the pipe
        std::thread::spawn(move || {
            if let Some(mut stdin) = child.stdin.take() {
                let _ = stdin.write_all(&data);
            }
            let _ = child.wait();
        });
    }

    pub fn clear(&self) {
        self.items.borrow_mut().clear();
        self.changed();
    }

    /// Stop watching (the process is ending).
    pub fn stop(&self) {
        if let Some(p) = self.proc.take() {
            p.force_exit();
        }
    }
}

fn read_more(out: gio::InputStream, mut stream: Stream, history: Weak<History>) {
    out.clone().read_bytes_async(65536, glib::Priority::DEFAULT, None::<&gio::Cancellable>, move |res| {
        let Ok(bytes) = res else { return };
        if bytes.is_empty() {
            return;
        }
        let Some(h) = history.upgrade() else { return };
        for item in stream.feed(&bytes) {
            h.add(item);
        }
        drop(h);
        read_more(out, stream, history);
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A PNG's signature and IHDR: enough for the size (the picture itself
    /// isn't decoded here).
    pub fn png_bytes(w: u32, h: u32) -> Vec<u8> {
        let mut v = b"\x89PNG\r\n\x1a\n\0\0\0\x0dIHDR".to_vec();
        v.extend(w.to_be_bytes());
        v.extend(h.to_be_bytes());
        v.extend([8, 6, 0, 0, 0]);
        v
    }

    fn text(t: &str) -> ClipItem {
        ClipItem::Text(t.into())
    }

    #[test]
    fn clip_image_size_and_identity() {
        let _e = crate::testenv::env();
        let png = png_bytes(8, 5);
        let a = ClipItem::Image(Rc::new(ClipImage::new(png.clone())));
        let b = ClipItem::Image(Rc::new(ClipImage::new(png)));
        let ClipItem::Image(img) = &a else { unreachable!() };
        assert_eq!(img.size, (8, 5));
        assert_eq!(a.label(), "Image · 8×5");
        assert_eq!(a, b);
        assert_ne!(a, text("Image · 8×5")); // never equal to a text copy
        assert_eq!(ClipImage::new(b"junk".to_vec()).label(), "Image");
    }

    #[test]
    fn thumbnail_fits_the_row() {
        let pixbuf = gdk_pixbuf::Pixbuf::new(gdk_pixbuf::Colorspace::Rgb, true, 8, 16, 10).unwrap();
        let png = pixbuf.save_to_bufferv("png", &[]).unwrap();
        let img = ClipImage::new(png);
        assert_eq!(img.size, (16, 10));
        let tex = img.texture(4, 4).unwrap();
        assert!(tex.width().max(tex.height()) <= 4, "{}x{}", tex.width(), tex.height());
        assert!(ClipImage::new(b"junk".to_vec()).texture(4, 4).is_none());
    }

    #[test]
    fn dedupe_newest_first_and_caps() {
        let h = History::unwatched();
        assert!(!h.available);
        let calls = Rc::new(std::cell::Cell::new(0));
        let c = calls.clone();
        h.connect(move || c.set(c.get() + 1));
        h.add(text("one"));
        h.add(text("two"));
        h.add(text("one"));
        assert_eq!(h.items(), [text("one"), text("two")]);
        h.add(text("   \n")); // whitespace: ignored
        assert_eq!(calls.get(), 3);
        for i in 0..KEEP + 5 {
            h.add(text(&format!("t{i}")));
        }
        assert_eq!(h.items().len(), KEEP);
        assert_eq!(h.items()[0], text(&format!("t{}", KEEP + 4)));
        for i in 0..KEEP_IMAGES + 2 {
            h.add(ClipItem::Image(Rc::new(ClipImage::new(png_bytes(i as u32 + 1, 1)))));
        }
        let imgs: Vec<_> = h.items().into_iter().filter_map(|t| if let ClipItem::Image(i) = t { Some(i) } else { None }).collect();
        assert_eq!(imgs.len(), KEEP_IMAGES);
        assert_eq!(imgs[0].size, (KEEP_IMAGES as u32 + 2, 1)); // newest kept
        h.clear();
        assert!(h.is_empty());
    }

    #[test]
    fn watch_stream_protocol() {
        // wl-paste's output ("T"text / "I"base64 PNG, NUL-separated, cut
        // across reads) becomes copies; oversized text is dropped
        let png = png_bytes(3, 2);
        let mut data = b"Thello\0I".to_vec();
        data.extend(glib::base64_encode(&png).as_bytes());
        data.extend(b"\0T");
        data.extend(vec![b'x'; MAX_BYTES + 1]);
        data.extend(b"\0Tbye\0");
        let h = History::unwatched();
        let mut s = Stream::default();
        for chunk in data.chunks(7) {
            // tiny reads: copies cut mid-way
            for item in s.feed(chunk) {
                h.add(item);
            }
        }
        let items = h.items();
        assert_eq!(items.len(), 3);
        assert_eq!(items[0], text("bye"));
        assert!(matches!(&items[1], ClipItem::Image(i) if i.size == (3, 2)));
        assert_eq!(items[2], text("hello"));
    }

    #[test]
    fn snapshot_reads_without_the_watchers_stdin() {
        // the one-shot read: the same script without the `cat`
        // that eats the copy wl-paste --watch hands it (it would read ours)
        let once = WATCH.trim_start_matches("cat >/dev/null; ");
        assert!(WATCH.starts_with("cat >/dev/null; ") && !once.contains("cat"));
    }
}
