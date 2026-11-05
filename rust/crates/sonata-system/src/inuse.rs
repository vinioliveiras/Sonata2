//! Microphone and camera in use (backend/inuse.py; macOS: the orange and
//! green dots).
//!
//! Microphone: the recording streams (`pactl list source-outputs`) that
//! record a real input, not a monitor of the speakers (a screen recording's
//! system sound) nor a level meter; looked at again on every `pactl
//! subscribe` source-output event, nothing in between.
//! Camera: a process with a /dev/video* device open (PipeWire holds it
//! while an app records through it). Looked for (every /proc/*/fd of this
//! user's processes, in a thread) only when a camera node is opened or
//! closed -- inotify on the /dev/video* nodes, new ones followed through
//! /dev -- instead of every 4 s (that scan was the menu bar's biggest idle
//! cost). Without inotify: every CAMERA_S seconds. Never while paused (the
//! screen locked, a fullscreen game in front: the caller's `paused`): a
//! look once that ends (resume()).

use serde_json::Value;
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::os::fd::RawFd;
use std::path::Path;
use std::rc::Rc;
use std::time::Duration;

pub const CAMERA_S: u32 = 4;
/// a burst of opens/closes (an app probing every node): one look
pub const SETTLE_MS: u64 = 300;
/// a node event while paused: looked at again (the pause may end unseen)
pub const PAUSED_RETRY_S: u32 = 30;
pub const METER_NAMES: [&str; 3] = ["peak detect", "level meter", "peak"];
/// hold the device for the app actually recording
pub const CAMERA_HOLDERS: [&str; 2] = ["pipewire", "wireplumber"];

fn truthy(v: Option<&Value>) -> bool {
    matches!(v, Some(Value::Bool(true))) || v.and_then(Value::as_str) == Some("true")
}

/// App names recording a real input, from `pactl -f json list source-outputs`.
pub fn mic_users(outputs: &[Value]) -> Vec<String> {
    let mut names: Vec<String> = Vec::new();
    for o in outputs {
        let empty = Value::Null;
        let props = o.get("properties").unwrap_or(&empty);
        let s = |k: &str| props.get(k).and_then(Value::as_str).unwrap_or("").to_string();
        let media = s("media.name").to_lowercase();
        if METER_NAMES.iter().any(|m| media.contains(m)) || truthy(props.get("stream.monitor")) {
            continue;
        }
        let source = [o.get("source_name").and_then(Value::as_str).map(String::from), Some(s("target.object")), Some(s("node.target"))].into_iter().flatten().find(|x| !x.is_empty()).unwrap_or_default();
        if source.ends_with(".monitor") || truthy(props.get("stream.capture.sink")) {
            continue; // the speakers' sound (screen recording), not the mic
        }
        let name = [s("application.name"), s("application.process.binary")].into_iter().find(|n| !n.is_empty()).unwrap_or_else(|| tr!("An app"));
        if !names.contains(&name) {
            names.push(name);
        }
    }
    names
}

fn pactl_json(args: &[&str]) -> Vec<Value> {
    let mut cmd = vec!["pactl", "-f", "json", "list"];
    cmd.extend_from_slice(args);
    let (rc, out) = sonata_backend::run::run(&cmd, 3);
    if rc == 0 { serde_json::from_str(&out).unwrap_or_default() } else { vec![] }
}

/// Who records now (blocking).
pub fn read_mic() -> Vec<String> {
    let mut outs = pactl_json(&["source-outputs"]);
    if outs.is_empty() {
        return vec![];
    }
    // {source index: name} -- to tell a monitor source from a microphone
    let names: Vec<(String, String)> = pactl_json(&["sources", "short"]).iter().map(|s| (s.get("index").map(|i| i.to_string()).unwrap_or_default(), s.get("name").and_then(Value::as_str).unwrap_or("").to_string())).collect();
    for o in outs.iter_mut() {
        if o.get("source_name").is_none() {
            let src = o.get("source").map(|s| s.to_string()).unwrap_or_default();
            if let Some((_, n)) = names.iter().find(|(i, _)| *i == src) {
                o["source_name"] = Value::String(n.clone());
            }
        }
    }
    mic_users(&outs)
}

fn comm(proc: &Path, pid: &str) -> String {
    std::fs::read_to_string(proc.join(pid).join("comm")).map(|s| s.trim().to_string()).unwrap_or_default()
}

/// Names of the processes with a camera device open (PipeWire last: it
/// holds it for an app). Blocking.
pub fn camera_users(proc: &Path) -> Vec<String> {
    use std::os::unix::fs::MetadataExt;
    let uid = unsafe { libc::getuid() };
    let mut found: Vec<String> = Vec::new();
    let Ok(dir) = std::fs::read_dir(proc) else { return found };
    for e in dir.flatten() {
        let pid = e.file_name().to_string_lossy().into_owned();
        if !pid.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        if std::fs::metadata(e.path()).map(|m| m.uid() != uid).unwrap_or(true) {
            continue;
        }
        let Ok(fds) = std::fs::read_dir(e.path().join("fd")) else { continue };
        if fds.flatten().any(|fd| std::fs::read_link(fd.path()).is_ok_and(|t| t.to_string_lossy().starts_with("/dev/video"))) {
            let c = comm(proc, &pid);
            let name = if c.is_empty() { pid.clone() } else { c };
            if !found.contains(&name) {
                found.push(name);
            }
        }
    }
    let apps: Vec<String> = found.iter().filter(|n| !CAMERA_HOLDERS.contains(&n.as_str())).cloned().collect();
    if !apps.is_empty() {
        apps
    } else if found.is_empty() {
        vec![]
    } else {
        vec![tr!("An app")]
    }
}

fn dev_names(dev: &Path) -> Vec<String> {
    std::fs::read_dir(dev).map(|d| d.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect()).unwrap_or_default()
}

fn any_camera(dev: &Path) -> bool {
    dev_names(dev).iter().any(|n| n.starts_with("video"))
}

/// inotify on every /dev/video* node (open and close: a camera taken or let
/// go) and on /dev for nodes coming and going; on_event() for each. `ok`
/// false when inotify can't be used (then the caller polls).
pub struct VideoNodes {
    fd: RawFd,
    src: Option<glib::SourceId>,
    pub ok: bool,
}

const WATCH_NODE: u32 = libc::IN_OPEN | libc::IN_CLOSE_WRITE | libc::IN_CLOSE_NOWRITE;

fn add_watch(fd: RawFd, path: &Path, mask: u32) -> i32 {
    let Ok(c) = std::ffi::CString::new(path.as_os_str().as_encoded_bytes()) else { return -1 };
    unsafe { libc::inotify_add_watch(fd, c.as_ptr(), mask) }
}

impl VideoNodes {
    pub fn new(dev: &Path, on_event: impl Fn() + 'static) -> Self {
        let fd = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
        if fd < 0 {
            return Self { fd: -1, src: None, ok: false };
        }
        let dev_wd = add_watch(fd, dev, libc::IN_CREATE | libc::IN_DELETE | libc::IN_MOVED_TO);
        if dev_wd < 0 {
            unsafe { libc::close(fd) };
            return Self { fd: -1, src: None, ok: false };
        }
        for n in dev_names(dev).iter().filter(|n| n.starts_with("video")) {
            add_watch(fd, &dev.join(n), WATCH_NODE);
        }
        let dev = dev.to_path_buf();
        let src = glib::unix_fd_add_local(fd, glib::IOCondition::IN, move |fd, _| {
            let mut fire = false;
            let mut buf = [0u8; 4096];
            loop {
                let n = unsafe { libc::read(fd, buf.as_mut_ptr().cast(), buf.len()) };
                if n <= 0 {
                    break;
                }
                let n = n as usize;
                let mut off = 0;
                let head = std::mem::size_of::<libc::inotify_event>();
                while off + head <= n {
                    let ev: libc::inotify_event = unsafe { std::ptr::read_unaligned(buf[off..].as_ptr().cast()) };
                    let len = ev.len as usize;
                    let raw = &buf[off + head..(off + head + len).min(n)];
                    let name = String::from_utf8_lossy(raw.split(|b| *b == 0).next().unwrap_or(&[])).into_owned();
                    off += head + len;
                    if ev.wd == dev_wd {
                        if !name.starts_with("video") {
                            continue; // another device (a USB stick...): not ours
                        }
                        if ev.mask & (libc::IN_CREATE | libc::IN_MOVED_TO) != 0 {
                            add_watch(fd, &dev.join(&name), WATCH_NODE); // a camera plugged in
                        }
                    }
                    fire = true;
                }
            }
            if fire {
                on_event();
            }
            glib::ControlFlow::Continue
        });
        Self { fd, src: Some(src), ok: true }
    }

    pub fn close(&mut self) {
        if let Some(s) = self.src.take() {
            sonata_ui::cancel(s);
        }
        if self.fd >= 0 {
            unsafe { libc::close(self.fd) };
            self.fd = -1;
        }
        self.ok = false;
    }
}

impl Drop for VideoNodes {
    fn drop(&mut self) {
        self.close();
    }
}

/// {mic: app names, camera: app names}
#[derive(Clone, Debug, Default, PartialEq)]
pub struct State {
    pub mic: Vec<String>,
    pub camera: Vec<String>,
}

struct Inner {
    state: RefCell<State>,
    listeners: RefCell<Vec<Rc<dyn Fn(&State)>>>,
    mic_busy: Cell<bool>,
    cam_busy: Cell<bool>,
    proc: RefCell<Option<crate::pactl_watch::Handle>>,
    nodes: RefCell<Option<VideoNodes>>,
    soon_src: RefCell<Option<glib::SourceId>>,
    retry_src: RefCell<Option<glib::SourceId>>,
    /// a node event came while paused
    dirty: Cell<bool>,
    paused: Box<dyn Fn() -> bool>,
    read_mic: std::sync::Arc<dyn Fn() -> Vec<String> + Send + Sync>,
}

/// Main thread. listener(state) when it changes.
#[derive(Clone)]
pub struct Watcher(Rc<Inner>);

impl Watcher {
    /// `paused`: screen locked or a fullscreen game in front (quiet.py).
    pub fn new(paused: impl Fn() -> bool + 'static) -> Self {
        let w = Self::bare(paused, std::sync::Arc::new(read_mic));
        if sonata_backend::run::which("pactl").is_some() {
            let weak = Rc::downgrade(&w.0);
            *w.0.proc.borrow_mut() = crate::pactl_watch::watch(move |t| {
                if let Some(i) = weak.upgrade() {
                    Watcher(i).line(t);
                }
            });
            w.read_mic_now();
        }
        let weak = Rc::downgrade(&w.0);
        let nodes = VideoNodes::new(Path::new("/dev"), move || {
            if let Some(i) = weak.upgrade() {
                Watcher(i).camera_soon();
            }
        });
        let ok = nodes.ok;
        *w.0.nodes.borrow_mut() = Some(nodes);
        if ok {
            w.tick_camera(); // once: an app already recording at start
        } else {
            let weak = Rc::downgrade(&w.0);
            glib::timeout_add_seconds_local(CAMERA_S, move || match weak.upgrade() {
                Some(i) => {
                    Watcher(i).tick_camera();
                    glib::ControlFlow::Continue
                }
                None => glib::ControlFlow::Break,
            });
        }
        w
    }

    /// A watcher reading nothing by itself (tests).
    pub fn bare(paused: impl Fn() -> bool + 'static, read_mic: std::sync::Arc<dyn Fn() -> Vec<String> + Send + Sync>) -> Self {
        Self(Rc::new(Inner { state: RefCell::default(), listeners: RefCell::default(), mic_busy: Cell::new(false), cam_busy: Cell::new(false), proc: RefCell::default(), nodes: RefCell::default(), soon_src: RefCell::default(), retry_src: RefCell::default(), dirty: Cell::new(false), paused: Box::new(paused), read_mic }))
    }

    pub fn state(&self) -> State {
        self.0.state.borrow().clone()
    }

    pub fn add_listener(&self, f: impl Fn(&State) + 'static) {
        self.0.listeners.borrow_mut().push(Rc::new(f));
    }

    /// The pause ended: a camera event that came meanwhile is looked at.
    pub fn resume(&self) {
        if self.0.dirty.get() {
            self.tick_camera();
        }
    }

    fn set(&self, mic: Option<Vec<String>>, camera: Option<Vec<String>>) {
        let mut st = self.0.state.borrow().clone();
        if let Some(m) = mic {
            st.mic = m;
        }
        if let Some(c) = camera {
            st.camera = c;
        }
        if st == *self.0.state.borrow() {
            return;
        }
        *self.0.state.borrow_mut() = st.clone();
        let all: Vec<_> = self.0.listeners.borrow().clone();
        for f in all {
            f(&st);
        }
    }

    pub fn line(&self, text: &str) {
        if text.contains("source-output") {
            self.read_mic_now();
        }
    }

    fn read_mic_now(&self) {
        if self.0.mic_busy.replace(true) {
            return;
        }
        let w = Rc::downgrade(&self.0);
        let read = self.0.read_mic.clone();
        crate::run_async(move || read(), move |names| {
            if let Some(i) = w.upgrade() {
                i.mic_busy.set(false);
                Watcher(i).set(Some(names), None);
            }
        });
    }

    fn camera_soon(&self) {
        if self.0.soon_src.borrow().is_some() {
            return;
        }
        let w = Rc::downgrade(&self.0);
        *self.0.soon_src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(SETTLE_MS), move || {
            if let Some(i) = w.upgrade() {
                i.soon_src.borrow_mut().take();
                Watcher(i).tick_camera();
            }
        }));
    }

    fn tick_camera(&self) {
        let me = &self.0;
        if (me.paused)() {
            // locked / a game in front: looked at once that ends
            me.dirty.set(true);
            if me.retry_src.borrow().is_none() {
                let w = Rc::downgrade(me);
                *me.retry_src.borrow_mut() = Some(glib::timeout_add_seconds_local_once(PAUSED_RETRY_S, move || {
                    if let Some(i) = w.upgrade() {
                        i.retry_src.borrow_mut().take();
                        Watcher(i).resume();
                    }
                }));
            }
            return;
        }
        me.dirty.set(false);
        if me.cam_busy.get() {
            self.camera_soon(); // an event during a look: once more after it
            return;
        }
        if !any_camera(Path::new("/dev")) {
            self.set(None, Some(vec![])); // no camera at all: nothing to look for
            return;
        }
        me.cam_busy.set(true);
        let w = Rc::downgrade(me);
        crate::run_async(|| camera_users(Path::new("/proc")), move |names| {
            if let Some(i) = w.upgrade() {
                i.cam_busy.set(false);
                Watcher(i).set(None, Some(names));
            }
        });
    }

    pub fn stop(&self) {
        if let Some(h) = self.0.proc.borrow_mut().take() {
            h.stop();
        }
        if let Some(mut n) = self.0.nodes.borrow_mut().take() {
            n.close();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    #[test]
    fn real_inputs_only() {
        let outs = [
            json!({"source_name": "alsa_input.pci-0000.analog-stereo", "properties": {"application.name": "Discord"}}),
            json!({"source_name": "alsa_output.pci-0000.analog-stereo.monitor", "properties": {"application.name": "wf-recorder"}}), // screen recording sound
            json!({"source_name": "alsa_input.x", "properties": {"application.name": "Settings", "media.name": "Peak detect"}}), // a level meter
            json!({"source_name": "alsa_input.x", "properties": {"application.name": "Discord"}}),
        ];
        assert_eq!(mic_users(&outs), ["Discord"]);
        assert!(mic_users(&[]).is_empty());
    }

    #[test]
    fn camera_holders() {
        let root = tempfile::tempdir().unwrap();
        let proc = |pid: u32, comm: &str, target: &str| {
            let d = root.path().join(pid.to_string()).join("fd");
            std::fs::create_dir_all(&d).unwrap();
            std::os::unix::fs::symlink(target, d.join("3")).unwrap();
            std::fs::write(root.path().join(pid.to_string()).join("comm"), format!("{comm}\n")).unwrap();
        };
        proc(10, "pipewire", "/dev/video0");
        proc(11, "bash", "/dev/null");
        assert_eq!(camera_users(root.path()), [tr!("An app")]); // only PipeWire: an app records through it
        proc(12, "obs", "/dev/video2");
        assert_eq!(camera_users(root.path()), ["obs"]);
        assert!(camera_users(tempfile::tempdir().unwrap().path()).is_empty());
    }

    /// The menu bar's biggest idle cost: the microphone is read on
    /// source-output events only.
    #[test]
    fn watcher_reads_on_events_only() {
        let n = crate::on_main(move || {
            let reads = Arc::new(AtomicUsize::new(0));
            let r = reads.clone();
            let w = Watcher::bare(|| false, Arc::new(move || {
                r.fetch_add(1, Ordering::SeqCst);
                vec!["Discord".into()]
            }));
            let seen: Rc<RefCell<Vec<State>>> = Rc::default();
            let s = seen.clone();
            w.add_listener(move |st| s.borrow_mut().push(st.clone()));
            w.line("Event 'change' on sink-input #3");
            crate::spin(50);
            assert_eq!(reads.load(Ordering::SeqCst), 0);
            w.line("Event 'new' on source-output #7");
            crate::spin(100);
            assert_eq!(seen.borrow().last().unwrap().mic, ["Discord"]);
            reads.load(Ordering::SeqCst)
        });
        assert_eq!(n, 1);
    }

    /// inotify: opening a camera node is seen at once.
    #[test]
    fn video_nodes_follow_opens_and_new_nodes() {
        let got = crate::on_main(move || {
            let dev = tempfile::tempdir().unwrap();
            std::fs::write(dev.path().join("video0"), "").unwrap();
            let hits = Rc::new(Cell::new(0));
            let h = hits.clone();
            let nodes = VideoNodes::new(dev.path(), move || h.set(h.get() + 1));
            assert!(nodes.ok);
            let _ = std::fs::File::open(dev.path().join("video0"));
            crate::spin(100);
            let after_open = hits.get();
            std::fs::write(dev.path().join("sdb"), "").unwrap(); // not a camera
            crate::spin(100);
            let after_other = hits.get();
            std::fs::write(dev.path().join("video1"), "").unwrap(); // plugged in
            crate::spin(100);
            let _ = std::fs::File::open(dev.path().join("video1"));
            crate::spin(100);
            (after_open, after_other, hits.get())
        });
        assert!(got.0 >= 1);
        assert_eq!(got.1, got.0);
        assert!(got.2 >= got.1 + 2, "{got:?}");
    }
}
