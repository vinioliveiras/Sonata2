//! Instant replay (sonata2/shell/replay.py; Vini: "record the last 30
//! seconds", Windows' Record That).
//!
//! While it's on, a recorder keeps the most recent seconds of the game's
//! display in memory, nothing written until a clip is asked for (SIGUSR1):
//!
//! - wl-screenrec (--history): SIGUSR1 writes those seconds to its file,
//!   SIGINT a moment later finishes it, and a new buffer starts at once;
//! - gpu-screen-recorder (-r), when there's no wl-screenrec (Vini: Instant
//!   Replay couldn't be turned on -- wl-screenrec is only in the AUR, while
//!   gpu-screen-recorder is in Arch's and CachyOS' own repositories):
//!   SIGUSR1 writes the clip into its folder and it keeps recording.
//!
//! The clip goes to the recordings folder as "Replay <date>.mp4" (with its
//! thumbnail and notification, like a recording). The GPU encoder does the
//! work (a few % of a core); off by default, and it stops when the Game
//! Bar's switch is turned off or the process hosting it quits.

use crate::capture::{self, audio_device, focused_output, load_cfg, movies_dir, running, signal};
use gtk::glib;
use std::cell::{Cell, RefCell};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::rc::{Rc, Weak};
use std::time::Duration;

pub const SECONDS: i64 = 30;
/// after SIGUSR1, before SIGINT: the buffer is written out
pub const FLUSH_MS: u64 = 600;
pub const WAIT_MS: u64 = 100;
/// gpu-screen-recorder's clip: given up on if it isn't written by then
pub const GSR_SAVE_S: u64 = 15;
/// ...and kept once its size hasn't changed for this many looks (WAIT_MS apart)
pub const GSR_STILL: u32 = 5;
pub const GSR_FPS: u32 = 60;

/// The recorder replays are kept with: "wl-screenrec", "gpu-screen-recorder" or None.
pub fn backend() -> Option<&'static str> {
    ["wl-screenrec", "gpu-screen-recorder"].into_iter().find(|n| sonata_backend::run::which(n).is_some())
}

fn nice() -> Vec<String> {
    if sonata_backend::run::which("nice").is_some() {
        vec!["nice".into(), "-n".into(), "10".into()]
    } else {
        Vec::new()
    }
}

/// gpu-screen-recorder keeping the last `seconds` in memory; each SIGUSR1
/// writes them as an .mp4 into `folder`.
pub fn gsr_command(folder: &str, output: Option<&str>, seconds: i64, audio: Option<&str>) -> Vec<String> {
    let mut cmd = nice();
    cmd.extend(
        ["gpu-screen-recorder", "-w", output.filter(|o| !o.is_empty()).unwrap_or("screen"), "-f", &GSR_FPS.to_string(), "-r", &seconds.to_string(), "-c", "mp4", "-o", folder]
            .iter()
            .map(|s| s.to_string()),
    );
    if let Some(a) = audio.filter(|a| !a.is_empty()) {
        cmd.extend(["-a".into(), a.into()]);
    }
    cmd
}

pub fn command(path: &str, output: Option<&str>, seconds: i64, audio: Option<&str>) -> Vec<String> {
    let mut cmd = nice();
    cmd.extend(["wl-screenrec", "--history", &seconds.to_string(), "-f", path].iter().map(|s| s.to_string()));
    if let Some(o) = output.filter(|o| !o.is_empty()) {
        cmd.extend(["-o".into(), o.into()]);
    }
    if let Some(a) = audio.filter(|a| !a.is_empty()) {
        cmd.extend(["--audio".into(), "--audio-device".into(), a.into()]);
    }
    cmd
}

/// The buffer's file gone (or gpu-screen-recorder's folder: its clips not
/// saved, then the folder itself).
pub fn clear(path: &Path) {
    if path.is_dir() {
        for f in clips(path) {
            let _ = std::fs::remove_file(path.join(f));
        }
        let _ = std::fs::remove_dir(path);
    } else {
        let _ = std::fs::remove_file(path);
    }
}

/// The .mp4 files in gpu-screen-recorder's folder.
pub fn clips(folder: &Path) -> Vec<String> {
    let Ok(rd) = std::fs::read_dir(folder) else { return Vec::new() };
    rd.filter_map(|e| e.ok()).map(|e| e.file_name().to_string_lossy().to_string()).filter(|f| f.ends_with(".mp4")).collect()
}

/// A written clip moved to the recordings as "Replay <date>.mp4" (out of
/// gpu-screen-recorder's own folder: `nested`). None: nothing was written.
pub fn keep(path: &Path, nested: bool) -> Option<PathBuf> {
    if !std::fs::metadata(path).is_ok_and(|m| m.len() > 0) {
        return None;
    }
    let mut d = path.parent()?.to_path_buf();
    if nested {
        d = d.parent()?.to_path_buf(); // out of its folder, into the recordings
    }
    let out = d.join(capture::name_now("Replay", "mp4"));
    std::fs::rename(path, &out).ok()?;
    Some(out)
}

/// gpu-screen-recorder writes the clip in the background: kept once it's
/// there and stopped growing (it keeps recording meanwhile).
#[derive(Debug)]
pub struct GsrWatch {
    left: i64,
    size: i64,
    still: u32,
}

#[derive(Debug, PartialEq)]
pub enum Look {
    Wait,
    Keep,
    GiveUp,
}

impl Default for GsrWatch {
    fn default() -> Self {
        GsrWatch { left: (GSR_SAVE_S * 1000 / WAIT_MS) as i64, size: -1, still: 0 }
    }
}

impl GsrWatch {
    /// One look: the new clip's size (None: no new clip yet).
    pub fn look(&mut self, size: Option<u64>) -> Look {
        self.left -= 1;
        if let Some(size) = size {
            let size = size as i64;
            // (one look the same was a pause in its writing, and the clip came out cut)
            self.still = if size > 0 && size == self.size { self.still + 1 } else { 0 };
            if self.still >= GSR_STILL {
                return Look::Keep;
            }
            self.size = size;
        }
        if self.left <= 0 {
            Look::GiveUp
        } else {
            Look::Wait
        }
    }
}

pub struct Replay {
    proc: RefCell<Option<Child>>,
    output: RefCell<Option<String>>,
    path: RefCell<Option<PathBuf>>,
    backend: Cell<Option<&'static str>>,
    /// fn(): started, stopped or a clip saved
    listeners: RefCell<Vec<Rc<dyn Fn()>>>,
    saving: Cell<bool>,
    /// a clip saved (Capture: its thumbnail and notification)
    on_saved: Box<dyn Fn(PathBuf)>,
    me: Weak<Replay>,
}

impl Replay {
    pub fn new(on_saved: Box<dyn Fn(PathBuf)>) -> Rc<Self> {
        Rc::new_cyclic(|me| Replay {
            proc: RefCell::new(None),
            output: RefCell::new(None),
            path: RefCell::new(None),
            backend: Cell::new(None),
            listeners: RefCell::default(),
            saving: Cell::new(false),
            on_saved,
            me: me.clone(),
        })
    }

    pub fn available() -> bool {
        backend().is_some()
    }

    pub fn running(&self) -> bool {
        self.proc.borrow_mut().as_mut().is_some_and(running)
    }

    pub fn saving(&self) -> bool {
        self.saving.get()
    }

    pub fn add_listener(&self, f: Rc<dyn Fn()>) {
        self.listeners.borrow_mut().push(f);
    }

    pub fn remove_listener(&self, f: &Rc<dyn Fn()>) {
        self.listeners.borrow_mut().retain(|g| !Rc::ptr_eq(g, f));
    }

    fn changed(&self) {
        let fns: Vec<_> = self.listeners.borrow().clone();
        for f in fns {
            f();
        }
    }

    fn buffer_path() -> PathBuf {
        movies_dir(&load_cfg()).join(format!(".replay-{}.mp4", std::process::id()))
    }

    pub fn start(&self, output: Option<String>) -> bool {
        if self.running() || !Self::available() {
            return self.running();
        }
        let cfg = load_cfg();
        let output = output.or_else(focused_output);
        *self.output.borrow_mut() = output.clone();
        let backend = backend();
        self.backend.set(backend);
        let seconds = cfg.get("replay_s").and_then(|v| v.as_i64()).unwrap_or(SECONDS);
        let audio = if capture::text(&cfg, "audio", "system") != "none" { audio_device("system") } else { None };
        let (path, cmd) = if backend == Some("gpu-screen-recorder") {
            // its clips land in a folder of its own, moved out as they're done
            let buf = Self::buffer_path();
            let folder = buf.with_file_name(buf.file_stem().unwrap_or_default());
            if std::fs::create_dir_all(&folder).is_err() {
                return false;
            }
            let cmd = gsr_command(&folder.display().to_string(), output.as_deref(), seconds, audio.as_deref());
            (folder, cmd)
        } else {
            let path = Self::buffer_path();
            let cmd = command(&path.display().to_string(), output.as_deref(), seconds, audio.as_deref());
            (path, cmd)
        };
        *self.path.borrow_mut() = Some(path);
        let child = Command::new(&cmd[0]).args(&cmd[1..]).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().ok();
        *self.proc.borrow_mut() = child;
        self.changed();
        self.running()
    }

    pub fn stop(&self) {
        let proc = self.proc.borrow_mut().take();
        if let Some(mut p) = proc {
            if running(&mut p) {
                signal(&p, libc::SIGTERM);
                let end = std::time::Instant::now() + Duration::from_secs(3);
                while running(&mut p) && std::time::Instant::now() < end {
                    std::thread::sleep(Duration::from_millis(20));
                }
                if running(&mut p) {
                    let _ = p.kill();
                    let _ = p.wait();
                }
            }
        }
        if let Some(path) = self.path.borrow().as_ref() {
            clear(path);
        }
        self.changed();
    }

    /// The last seconds as a clip; the buffer starts again. False: not running.
    pub fn save(&self) -> bool {
        if !self.running() || self.saving.get() {
            return false;
        }
        self.saving.set(true);
        let (path, output) = (self.path.borrow().clone().unwrap_or_default(), self.output.borrow().clone());
        let gsr = self.backend.get() == Some("gpu-screen-recorder");
        let before = if gsr { clips(&path) } else { Vec::new() };
        let sent = self.proc.borrow().as_ref().is_some_and(|p| signal(p, libc::SIGUSR1));
        if !sent {
            self.saving.set(false);
            return false;
        }
        if gsr {
            self.gsr_wait(path, before);
            return true;
        }
        let me = self.me.clone();
        glib::timeout_add_local_once(Duration::from_millis(FLUSH_MS), move || {
            let Some(r) = me.upgrade() else { return };
            if let Some(p) = r.proc.borrow().as_ref() {
                signal(p, libc::SIGINT);
            }
            let me = r.me.clone();
            glib::timeout_add_local(Duration::from_millis(WAIT_MS), move || {
                let Some(r) = me.upgrade() else { return glib::ControlFlow::Break };
                // (stopped meanwhile: its buffer is gone, nothing to keep)
                let alive = r.proc.borrow_mut().as_mut().map(running);
                if alive == Some(true) {
                    return glib::ControlFlow::Continue;
                }
                r.saving.set(false);
                if alive.is_none() {
                    r.changed();
                    return glib::ControlFlow::Break;
                }
                r.proc.borrow_mut().take();
                let clip = keep(&path, false);
                r.start(output.clone()); // a new buffer at once
                if let Some(c) = clip {
                    (r.on_saved)(c);
                }
                r.changed();
                glib::ControlFlow::Break
            });
        });
        true
    }

    fn gsr_wait(&self, folder: PathBuf, before: Vec<String>) {
        let mut watch = GsrWatch::default();
        let me = self.me.clone();
        glib::timeout_add_local(Duration::from_millis(WAIT_MS), move || {
            let Some(r) = me.upgrade() else { return glib::ControlFlow::Break };
            let mut new: Vec<String> = clips(&folder).into_iter().filter(|f| !before.contains(f)).collect();
            new.sort();
            let file = new.last().map(|f| folder.join(f));
            let size = file.as_ref().map(|f| std::fs::metadata(f).map(|m| m.len()).unwrap_or(0));
            match watch.look(size) {
                Look::Wait => glib::ControlFlow::Continue,
                Look::Keep => {
                    r.saving.set(false);
                    if let Some(c) = file.and_then(|f| keep(&f, true)) {
                        (r.on_saved)(c);
                    }
                    r.changed();
                    glib::ControlFlow::Break
                }
                Look::GiveUp => {
                    r.saving.set(false); // never written: the switch works again
                    r.changed();
                    glib::ControlFlow::Break
                }
            }
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tail(cmd: &[String], from: &str) -> Vec<String> {
        cmd[cmd.iter().position(|a| a == from).unwrap()..].to_vec()
    }

    #[test]
    fn test_command_keeps_the_last_seconds() {
        let cmd = command("/v/.replay.mp4", Some("DP-1"), 30, Some("sink.monitor"));
        assert_eq!(tail(&cmd, "wl-screenrec"), ["wl-screenrec", "--history", "30", "-f", "/v/.replay.mp4", "-o", "DP-1", "--audio", "--audio-device", "sink.monitor"]);
    }

    #[test]
    fn test_gsr_command_keeps_the_last_seconds() {
        let cmd = gsr_command("/v/.replay", Some("DP-1"), 30, Some("sink.monitor"));
        assert_eq!(tail(&cmd, "gpu-screen-recorder"), ["gpu-screen-recorder", "-w", "DP-1", "-f", "60", "-r", "30", "-c", "mp4", "-o", "/v/.replay", "-a", "sink.monitor"]);
        assert_eq!(tail(&gsr_command("/v/.replay", None, 30, None), "-w")[..2], ["-w", "screen"]);
    }

    /// A clip was taken as done the first time its size looked the same
    /// twice (100 ms): a pause in gpu-screen-recorder's writing and the
    /// Replay came out cut. It must stay the same size GSR_STILL looks.
    #[test]
    fn test_gsr_clip_kept_while_still_being_written() {
        let mut w = GsrWatch::default();
        assert_eq!(w.look(None), Look::Wait);
        for _ in 0..3 {
            assert_eq!(w.look(Some(4)), Look::Wait); // the same size two, three looks
        }
        // it was still writing
        let mut last = Look::Wait;
        for _ in 0..GSR_STILL + 1 {
            last = w.look(Some(9));
        }
        assert_eq!(last, Look::Keep);
        // never written: given up in the end
        let mut w = GsrWatch::default();
        let n = (GSR_SAVE_S * 1000 / WAIT_MS) as usize;
        let looks: Vec<Look> = (0..n).map(|_| w.look(None)).collect();
        assert_eq!(looks.last(), Some(&Look::GiveUp));
        assert!(looks[..n - 1].iter().all(|l| *l == Look::Wait));
    }

    /// Vini: the Game Bar's Instant Replay couldn't be turned on without
    /// wl-screenrec; gpu-screen-recorder's clip comes out of its folder
    /// into the recordings, and the folder goes when it stops.
    #[test]
    fn test_instant_replay_could_not_be_turned_on_without_wl_screenrec() {
        let d = tempfile::tempdir().unwrap();
        let folder = d.path().join(".replay-1");
        std::fs::create_dir(&folder).unwrap();
        let clip = folder.join("Replay_2026-11-04_13-00-00.mp4");
        std::fs::write(&clip, b"clip").unwrap();
        let kept = keep(&clip, true).unwrap();
        assert_eq!(kept.parent(), Some(d.path())); // out of its folder
        assert!(kept.file_name().unwrap().to_string_lossy().starts_with("Replay "));
        std::fs::write(folder.join("left.mp4"), b"x").unwrap();
        clear(&folder);
        assert!(!folder.exists());
        // wl-screenrec's buffer: a file beside the recordings
        let buf = d.path().join(".replay-2.mp4");
        assert_eq!(keep(&buf, false), None); // nothing written
        std::fs::write(&buf, b"clip").unwrap();
        assert_eq!(keep(&buf, false).unwrap().parent(), Some(d.path()));
    }
}
