//! Idle from the keyboard and mouse only, and the displays on / off -- a
//! tiny Wayland client of its own, run on the GLib loop (wl/idlewatch.py).
//!
//! ```ignore
//! let w = IdleWatch::connect();                     // w.ok() false without a compositor
//! w.watch(120, Rc::new(on_idle), Rc::new(on_back)); // 120 s without input
//! w.displays(false, None);                          // every output off (and true: back on)
//! ```
//!
//! Why (Vini: the session never locked; Chrome kept it awake for hours):
//! the usual idle notification waits while any app asks to keep the
//! session awake (idle inhibitors). ext-idle-notify-v1 version 2 has an
//! *input* idle notification that ignores them -- Sonata decides what may
//! keep it awake (only media playing). Displays go off with
//! wlr-output-power-management (what wlopm uses), so no app keeps them lit.
//!
//! The workspace has no Wayland client library, so the few messages this
//! needs are written on the wire by hand, as vkeyboard.rs and vpointer.rs
//! do: the object id, then the size (high 16 bits) and opcode (low 16
//! bits), then the arguments -- uints, and strings as their length (with
//! the NUL) and the bytes padded to 4.
//!
//! Every method takes `&self`, so the session's `IdleWatch` trait
//! (sonata-sessionui's idlepolicy) is a few lines of delegation.

use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::io::{self, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::rc::{Rc, Weak};
use std::time::Duration;

pub const NOTIFIER: &str = "ext_idle_notifier_v1";
pub const POWER: &str = "zwlr_output_power_manager_v1";

// ext_idle_notifier_v1's requests
const GET_IDLE_NOTIFICATION: u16 = 1;
const GET_INPUT_IDLE_NOTIFICATION: u16 = 2; // (version 2)
// ext_idle_notification_v1: destroy; events idled / resumed
const NOTIFICATION_DESTROY: u16 = 0;
const IDLED: u16 = 0;
// zwlr_output_power_manager_v1.get_output_power, zwlr_output_power_v1.set_mode
const GET_OUTPUT_POWER: u16 = 0;
const SET_MODE: u16 = 0;
// wl_output.name (version 4)
const OUTPUT_NAME: u16 = 4;

type Callback = Rc<dyn Fn()>;

fn socket_path(display: Option<&str>) -> PathBuf {
    let name = display.map(String::from).or_else(|| std::env::var("WAYLAND_DISPLAY").ok().filter(|s| !s.is_empty())).unwrap_or_else(|| "wayland-0".into());
    if name.starts_with('/') {
        return name.into();
    }
    let run = std::env::var_os("XDG_RUNTIME_DIR").filter(|v| !v.is_empty()).map(PathBuf::from).unwrap_or_else(|| "/tmp".into());
    run.join(name)
}

#[derive(Clone, Copy)]
enum Arg<'a> {
    Uint(u32),
    Str(&'a str),
}

/// A Wayland message: object, opcode, then the arguments (native endian,
/// strings with their NUL, padded to 4 bytes).
fn message(obj: u32, op: u16, args: &[Arg]) -> Vec<u8> {
    let mut body = Vec::new();
    for a in args {
        match *a {
            Arg::Uint(v) => body.extend(v.to_ne_bytes()),
            Arg::Str(s) => {
                body.extend((s.len() as u32 + 1).to_ne_bytes());
                body.extend(s.as_bytes());
                body.push(0);
                while body.len() % 4 != 0 {
                    body.push(0);
                }
            }
        }
    }
    let mut out = obj.to_ne_bytes().to_vec();
    out.extend(((((body.len() + 8) as u32) << 16) | op as u32).to_ne_bytes());
    out.extend(body);
    out
}

/// The complete messages at the start of `buf`: (object, opcode, body),
/// and how many bytes they took.
fn messages(buf: &[u8]) -> (Vec<(u32, u16, Vec<u8>)>, usize) {
    let mut out = Vec::new();
    let mut at = 0;
    while buf.len() - at >= 8 {
        let word = |i: usize| u32::from_ne_bytes([buf[i], buf[i + 1], buf[i + 2], buf[i + 3]]);
        let (obj, head) = (word(at), word(at + 4));
        let size = (head >> 16) as usize;
        if size < 8 || buf.len() - at < size {
            break;
        }
        out.push((obj, (head & 0xffff) as u16, buf[at + 8..at + size].to_vec()));
        at += size;
    }
    (out, at)
}

fn word(body: &[u8], i: usize) -> Option<u32> {
    body.get(i..i + 4).map(|b| u32::from_ne_bytes([b[0], b[1], b[2], b[3]]))
}

/// A string argument at the start of `body`.
fn string(body: &[u8]) -> Option<String> {
    let len = word(body, 0)? as usize;
    let bytes = body.get(4..4 + len)?;
    Some(String::from_utf8_lossy(bytes).trim_end_matches('\0').to_string())
}

/// wl_registry.global's arguments: name, interface, version.
fn global(body: &[u8]) -> Option<(u32, String, u32)> {
    let name = word(body, 0)?;
    let len = word(body, 4)? as usize;
    let iface = body.get(8..8 + len.checked_sub(1)?)?;
    let version = word(body, 8 + len.div_ceil(4) * 4)?;
    Some((name, String::from_utf8_lossy(iface).into_owned(), version))
}

#[derive(Default)]
struct Inner {
    stream: RefCell<Option<UnixStream>>,
    next: Cell<u32>,
    inbuf: RefCell<Vec<u8>>,
    registry: Cell<u32>,
    seat: Cell<u32>,
    notifier: Cell<u32>,
    input_idle: Cell<bool>,
    power: Cell<u32>,
    /// output object ids
    outputs: RefCell<Vec<u32>>,
    /// output object id -> "eDP-1", "HDMI-A-1" (wl_output v4)
    output_names: RefCell<HashMap<u32, String>>,
    /// output id -> its output power object
    powers: RefCell<HashMap<u32, u32>>,
    /// notification id -> (on_idle, on_back)
    watches: RefCell<HashMap<u32, (Callback, Callback)>>,
    src: RefCell<Option<glib::SourceId>>,
}

impl Drop for Inner {
    fn drop(&mut self) {
        if let Some(src) = self.src.take() {
            sonata_ui::cancel(src);
        }
    }
}

/// The compositor's idle watch (ext-idle-notify-v1) and the displays'
/// power (wlr-output-power-management), on a connection of its own. A
/// clone is the same connection.
#[derive(Clone)]
pub struct IdleWatch(Rc<Inner>);

impl IdleWatch {
    /// $WAYLAND_DISPLAY's compositor; ok() is false without one, or
    /// without ext-idle-notify-v1.
    pub fn connect() -> IdleWatch {
        Self::connect_to(&socket_path(None))
    }

    /// The compositor listening at `socket` (a test's own).
    pub fn connect_to(socket: &Path) -> IdleWatch {
        match UnixStream::connect(socket) {
            Ok(s) => Self::on(s),
            Err(_) => IdleWatch(Rc::default()),
        }
    }

    /// Over an open connection: the globals asked for, then read on the GLib loop.
    pub fn on(stream: UnixStream) -> IdleWatch {
        let w = Self::open(stream);
        w.listen();
        w
    }

    /// The globals asked for; events read by readable() (the GLib loop's, or a test's).
    fn open(stream: UnixStream) -> IdleWatch {
        let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
        let w = IdleWatch(Rc::default());
        w.0.next.set(1); // 1 is wl_display
        w.0.stream.replace(Some(stream));
        match w.setup() {
            Ok(true) => {
                if let Some(s) = w.0.stream.borrow().as_ref() {
                    let _ = s.set_nonblocking(true);
                }
            }
            Ok(false) => w.hang_up(), // a compositor without the protocol
            Err(e) => {
                eprintln!("sonata2-rs: idle watch: {e}");
                w.hang_up();
            }
        }
        w
    }

    // -- wire ---------------------------------------------------------------------------------
    fn id(&self) -> u32 {
        self.0.next.set(self.0.next.get() + 1);
        self.0.next.get()
    }

    fn send(&self, obj: u32, op: u16, args: &[Arg]) -> io::Result<()> {
        let data = message(obj, op, args);
        match self.0.stream.borrow().as_ref() {
            Some(s) => (&*s).write_all(&data),
            None => Err(io::Error::from(io::ErrorKind::NotConnected)),
        }
    }

    /// A request that fails gives the connection up (ok() turns false).
    fn request(&self, obj: u32, op: u16, args: &[Arg]) -> bool {
        if self.send(obj, op, args).is_err() {
            self.hang_up();
            return false;
        }
        true
    }

    fn bind(&self, name: u32, interface: &str, version: u32) -> io::Result<u32> {
        let id = self.id();
        // wl_registry.bind (untyped new_id: interface and version first)
        self.send(self.0.registry.get(), 0, &[Arg::Uint(name), Arg::Str(interface), Arg::Uint(version), Arg::Uint(id)])?;
        Ok(id)
    }

    /// wl_display.sync and wait for its callback; other events go to
    /// `event`; wl_display.error fails.
    fn roundtrip(&self, event: &mut dyn FnMut(u32, u16, &[u8])) -> io::Result<()> {
        let cb = self.id();
        self.send(1, 0, &[Arg::Uint(cb)])?;
        let mut chunk = [0u8; 4096];
        loop {
            let msgs = {
                let mut buf = self.0.inbuf.borrow_mut();
                let (msgs, used) = messages(&buf);
                buf.drain(..used);
                msgs
            };
            let mut done = false;
            for (obj, op, body) in msgs {
                if obj == 1 && op == 0 {
                    return Err(io::Error::other("the compositor refused a request"));
                } else if obj == cb && op == 0 {
                    done = true;
                } else {
                    event(obj, op, &body);
                }
            }
            if done {
                return Ok(());
            }
            let n = match self.0.stream.borrow().as_ref() {
                Some(s) => (&*s).read(&mut chunk)?,
                None => return Err(io::Error::from(io::ErrorKind::NotConnected)),
            };
            if n == 0 {
                return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "the compositor hung up"));
            }
            self.0.inbuf.borrow_mut().extend_from_slice(&chunk[..n]);
        }
    }

    /// The registry, a seat and the notifier (false: no idle notifier
    /// here), the power manager and the outputs with their names.
    fn setup(&self) -> io::Result<bool> {
        let registry = self.id();
        self.0.registry.set(registry);
        self.send(1, 1, &[Arg::Uint(registry)])?; // wl_display.get_registry
        let mut globals: HashMap<String, Vec<(u32, u32)>> = HashMap::new();
        self.roundtrip(&mut |obj, op, body| {
            if obj == registry && op == 0 {
                if let Some((name, iface, version)) = global(body) {
                    globals.entry(iface).or_default().push((name, version));
                }
            }
        })?;
        let (Some(seat), Some(notif)) = (globals.get("wl_seat").and_then(|v| v.first()), globals.get(NOTIFIER).and_then(|v| v.first())) else {
            return Ok(false);
        };
        self.0.seat.set(self.bind(seat.0, "wl_seat", 1)?);
        let version = notif.1.min(2);
        self.0.notifier.set(self.bind(notif.0, NOTIFIER, version)?);
        self.0.input_idle.set(version >= 2);
        if let Some(p) = globals.get(POWER).and_then(|v| v.first()) {
            self.0.power.set(self.bind(p.0, POWER, 1)?);
        }
        for (name, version) in globals.get("wl_output").cloned().unwrap_or_default() {
            let id = self.bind(name, "wl_output", version.min(4))?; // v4: its name
            self.0.outputs.borrow_mut().push(id);
        }
        let mut names = HashMap::new();
        self.roundtrip(&mut |obj, op, body| {
            if op == OUTPUT_NAME && self.0.outputs.borrow().contains(&obj) {
                if let Some(n) = string(body) {
                    names.insert(obj, n);
                }
            }
        })?;
        self.0.output_names.replace(names);
        Ok(true)
    }

    /// Events from now on come through the GLib loop.
    fn listen(&self) {
        let Some(fd) = self.0.stream.borrow().as_ref().map(|s| s.as_raw_fd()) else { return };
        let weak: Weak<Inner> = Rc::downgrade(&self.0);
        let src = glib::unix_fd_add_local(fd, glib::IOCondition::IN | glib::IOCondition::HUP | glib::IOCondition::ERR, move |_, _| {
            let Some(inner) = weak.upgrade() else { return glib::ControlFlow::Break };
            let w = IdleWatch(inner);
            if w.readable() {
                return glib::ControlFlow::Continue;
            }
            w.0.src.take(); // (it ends with this Break)
            w.hang_up();
            glib::ControlFlow::Break
        });
        self.0.src.replace(Some(src));
    }

    /// What arrived, read and handled; false once the compositor is gone.
    fn readable(&self) -> bool {
        let mut chunk = [0u8; 4096];
        loop {
            let r = match self.0.stream.borrow().as_ref() {
                Some(s) => (&*s).read(&mut chunk),
                None => return false,
            };
            match r {
                Ok(0) => return false,
                Ok(n) => self.0.inbuf.borrow_mut().extend_from_slice(&chunk[..n]),
                Err(e) if e.kind() == io::ErrorKind::WouldBlock => break,
                Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
                Err(_) => return false,
            }
        }
        let msgs = {
            let mut buf = self.0.inbuf.borrow_mut();
            let (msgs, used) = messages(&buf);
            buf.drain(..used);
            msgs
        };
        for (obj, op, body) in msgs {
            self.event(obj, op, &body);
        }
        self.ok()
    }

    fn event(&self, obj: u32, op: u16, body: &[u8]) {
        if obj == 1 {
            if op == 0 {
                eprintln!("sonata2-rs: idle watch: the compositor refused a request");
                self.hang_up();
            }
            return; // (delete_id: nothing kept for it)
        }
        if op == OUTPUT_NAME && self.0.outputs.borrow().contains(&obj) {
            if let Some(n) = string(body) {
                self.0.output_names.borrow_mut().insert(obj, n);
            }
            return;
        }
        // (cloned out first: a callback may watch, unwatch or close)
        let cbs = self.0.watches.borrow().get(&obj).cloned();
        if let Some((on_idle, on_back)) = cbs {
            if op == IDLED {
                on_idle();
            } else {
                on_back();
            }
        }
    }

    fn hang_up(&self) {
        if let Some(src) = self.0.src.take() {
            sonata_ui::cancel(src);
        }
        self.0.stream.replace(None);
        self.0.watches.borrow_mut().clear();
    }

    // -- what Sonata uses ---------------------------------------------------------------------
    /// Connected to a compositor with an idle notifier.
    pub fn ok(&self) -> bool {
        self.0.stream.borrow().is_some()
    }

    /// The compositor has get_input_idle_notification (version 2): app
    /// inhibitors ignored.
    pub fn input_idle(&self) -> bool {
        self.ok() && self.0.input_idle.get()
    }

    /// The displays' connectors ("eDP-1", "HDMI-A-1"), the ones known by name.
    pub fn output_names(&self) -> Vec<String> {
        let names = self.0.output_names.borrow();
        self.0.outputs.borrow().iter().filter_map(|o| names.get(o).cloned()).collect()
    }

    /// on_idle() after `seconds` without input (app inhibitors ignored
    /// when the compositor can), on_back() at the next input. An id for
    /// unwatch(); 0 when there's nothing to watch with.
    pub fn watch(&self, seconds: u32, on_idle: Rc<dyn Fn()>, on_back: Rc<dyn Fn()>) -> u32 {
        self.watch_ms(seconds.saturating_mul(1000), on_idle, on_back)
    }

    /// watch() in milliseconds.
    pub fn watch_ms(&self, ms: u32, on_idle: Rc<dyn Fn()>, on_back: Rc<dyn Fn()>) -> u32 {
        if !self.ok() {
            return 0;
        }
        let id = self.id();
        let op = if self.0.input_idle.get() { GET_INPUT_IDLE_NOTIFICATION } else { GET_IDLE_NOTIFICATION };
        if !self.request(self.0.notifier.get(), op, &[Arg::Uint(id), Arg::Uint(ms), Arg::Uint(self.0.seat.get())]) {
            return 0;
        }
        self.0.watches.borrow_mut().insert(id, (on_idle, on_back));
        id
    }

    pub fn unwatch(&self, id: u32) {
        if self.ok() && self.0.watches.borrow_mut().remove(&id).is_some() {
            self.request(id, NOTIFICATION_DESTROY, &[]);
        }
    }

    /// The compositor can power the displays off and on.
    pub fn can_power(&self) -> bool {
        self.ok() && self.0.power.get() != 0 && !self.0.outputs.borrow().is_empty()
    }

    /// Every display on / off (wlr-output-power-management); `only`: just
    /// the displays it picks by name ("HDMI-A-1").
    pub fn displays(&self, on: bool, only: Option<&dyn Fn(&str) -> bool>) -> bool {
        if !self.can_power() {
            return false;
        }
        let outputs = self.0.outputs.borrow().clone();
        for out in outputs {
            if let Some(only) = only {
                let name = self.0.output_names.borrow().get(&out).cloned().unwrap_or_default();
                if !only(&name) {
                    continue;
                }
            }
            let known = self.0.powers.borrow().get(&out).copied();
            let p = match known {
                Some(p) => p,
                None => {
                    let p = self.id();
                    if !self.request(self.0.power.get(), GET_OUTPUT_POWER, &[Arg::Uint(p), Arg::Uint(out)]) {
                        return false;
                    }
                    self.0.powers.borrow_mut().insert(out, p);
                    p
                }
            };
            if !self.request(p, SET_MODE, &[Arg::Uint(on as u32)]) {
                return false;
            }
        }
        true
    }

    /// The panel's backlight (a laptop) or every display off, and back
    /// (idlelock.displays_dark): the session's darkening, the lock
    /// screen's and the login screen's alike.
    pub fn displays_dark(&self, on: bool) {
        displays_dark_with(&|power, only| self.displays(power, only), on, crate::lockdisplay::has_backlight(), &crate::lockdisplay::dim);
    }

    /// The connection closed: nothing more is watched (the compositor
    /// drops what this client asked for).
    pub fn close(&self) {
        self.hang_up();
    }
}

/// The displays powered off on a laptop: every one but its own panel
/// (an unknown name: left alone).
pub fn not_the_panel(name: &str) -> bool {
    !name.is_empty() && !sonata_backend::system::is_builtin(Some(name))
}

type Displays<'a> = &'a dyn Fn(bool, Option<&dyn Fn(&str) -> bool>) -> bool;

/// displays_dark with what it uses handed in: `displays` an IdleWatch's,
/// `backlight` a laptop's panel, `dim` its backlight to zero / back.
pub fn displays_dark_with(displays: Displays, on: bool, backlight: bool, dim: &dyn Fn(bool)) {
    if backlight {
        // a laptop: its panel's backlight to zero, the panel stays on --
        // powering it off and on again failed on Vini's (NVIDIA: the screen
        // came back only by closing and opening the lid). Monitors plugged
        // in really go off (Vini: they only went black, the pointer on them)
        dim(on);
        displays(!on, Some(&not_the_panel));
    } else {
        displays(!on, None);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn w(b: &[u8], i: usize) -> u32 {
        word(b, i).unwrap()
    }

    /// What the fake compositor saw of the client: (object, opcode, words).
    type Seen = Vec<(u32, u16, Vec<u32>)>;

    /// A compositor with a seat, the idle notifier (`version`, 0: none),
    /// the power manager and two outputs ("eDP-1", "HDMI-A-1"). It answers
    /// the registry, every sync and the outputs' names, sends `idled` /
    /// `resumed` for the first notification when told to, and gives back
    /// what the client asked.
    struct Fake {
        tell: std::sync::mpsc::Sender<u16>,
        seen: std::thread::JoinHandle<Seen>,
    }

    fn fake(version: u32) -> (UnixStream, Fake) {
        let (client, server) = UnixStream::pair().unwrap();
        let (tell, told) = std::sync::mpsc::channel::<u16>();
        let seen = std::thread::spawn(move || {
            let mut server = server;
            server.set_read_timeout(Some(Duration::from_millis(20))).unwrap();
            let (mut buf, mut chunk, mut seen): (Vec<u8>, [u8; 4096], Seen) = (Vec::new(), [0; 4096], Vec::new());
            let (mut first, mut notifier, mut notification, mut outputs) = (true, 0, 0, Vec::new());
            loop {
                if let Ok(op) = told.try_recv() {
                    server.write_all(&message(notification, op, &[])).unwrap();
                }
                match server.read(&mut chunk) {
                    Ok(0) => return seen,
                    Ok(n) => buf.extend_from_slice(&chunk[..n]),
                    Err(e) if matches!(e.kind(), io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut) => continue,
                    Err(_) => return seen,
                }
                let (msgs, used) = messages(&buf);
                buf.drain(..used);
                for (obj, op, body) in msgs {
                    if obj == 1 && op == 0 {
                        if first {
                            first = false;
                            let mut globals = vec![(1, "wl_seat", 7), (3, POWER, 1), (4, "wl_output", 4), (5, "wl_output", 4)];
                            if version > 0 {
                                globals.push((2, NOTIFIER, version));
                            }
                            for (name, iface, v) in globals {
                                server.write_all(&message(2, 0, &[Arg::Uint(name), Arg::Str(iface), Arg::Uint(v)])).unwrap();
                            }
                        }
                        server.write_all(&message(w(&body, 0), 0, &[Arg::Uint(0)])).unwrap();
                    } else if obj == 2 && op == 0 {
                        let (name, iface, _) = global(&body).unwrap();
                        let at = 8 + (iface.len() + 1).div_ceil(4) * 4;
                        let (version, id) = (w(&body, at), w(&body, at + 4));
                        seen.push((obj, op, vec![name, version, id]));
                        match iface.as_str() {
                            NOTIFIER => notifier = id,
                            "wl_output" => {
                                outputs.push(id);
                                let n = if outputs.len() == 1 { "eDP-1" } else { "HDMI-A-1" };
                                server.write_all(&message(id, OUTPUT_NAME, &[Arg::Str(n)])).unwrap();
                            }
                            _ => {}
                        }
                    } else {
                        if obj == notifier && notification == 0 {
                            notification = w(&body, 0);
                        }
                        seen.push((obj, op, body.chunks(4).map(|c| w(c, 0)).collect()));
                    }
                }
            }
        });
        (client, Fake { tell, seen })
    }

    /// Reads (as the GLib loop would) until `done` or 2 s.
    fn pump(watch: &IdleWatch, done: impl Fn() -> bool) {
        let end = std::time::Instant::now() + Duration::from_secs(2);
        while !done() && std::time::Instant::now() < end {
            watch.readable();
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn input_idle_ignoring_inhibitors_and_back() {
        let (client, fake) = fake(2);
        let watch = IdleWatch::open(client);
        assert!(watch.ok() && watch.input_idle() && watch.can_power());
        assert_eq!(watch.output_names(), ["eDP-1", "HDMI-A-1"]); // names: which are plugged in
        let ev = Rc::new(RefCell::new(Vec::new()));
        let (a, b) = (ev.clone(), ev.clone());
        let id = watch.watch(30, Rc::new(move || a.borrow_mut().push("idle")), Rc::new(move || b.borrow_mut().push("back")));
        assert_ne!(id, 0);
        fake.tell.send(IDLED).unwrap();
        pump(&watch, || !ev.borrow().is_empty());
        fake.tell.send(1).unwrap(); // resumed
        pump(&watch, || ev.borrow().len() > 1);
        assert_eq!(*ev.borrow(), ["idle", "back"]);
        watch.unwatch(id);
        watch.close();
        assert!(!watch.ok());
        let seen = fake.seen.join().unwrap();
        let notifier = seen.iter().find(|s| s.0 == 2 && s.2[0] == 2).map(|s| s.2[2]).unwrap();
        let seat = seen.iter().find(|s| s.0 == 2 && s.2[0] == 1).map(|s| s.2[2]).unwrap();
        assert_eq!(seen.iter().find(|s| s.0 == 2 && s.2[0] == 2).map(|s| s.2[1]), Some(2)); // version 2 asked
        let asked = seen.iter().find(|s| s.0 == notifier).unwrap();
        assert_eq!((asked.1, asked.2[0], asked.2[1], asked.2[2]), (GET_INPUT_IDLE_NOTIFICATION, id, 30_000, seat));
        assert!(seen.iter().any(|s| s.0 == id && s.1 == NOTIFICATION_DESTROY));
    }

    #[test]
    fn an_older_compositor_still_watches_idle() {
        let (client, fake) = fake(1);
        let watch = IdleWatch::open(client);
        assert!(watch.ok() && !watch.input_idle());
        let id = watch.watch_ms(500, Rc::new(|| {}), Rc::new(|| {}));
        watch.close();
        let seen = fake.seen.join().unwrap();
        let notifier = seen.iter().find(|s| s.0 == 2 && s.2[0] == 2).map(|s| s.2[2]).unwrap();
        let asked = seen.iter().find(|s| s.0 == notifier).unwrap();
        assert_eq!((asked.1, asked.2[0], asked.2[1]), (GET_IDLE_NOTIFICATION, id, 500));
    }

    #[test]
    fn displays_off_and_on_only_those_picked() {
        let (client, fake) = fake(2);
        let watch = IdleWatch::open(client);
        assert!(watch.displays(false, Some(&not_the_panel)));
        assert!(watch.displays(true, None));
        watch.close();
        let seen = fake.seen.join().unwrap();
        let power = seen.iter().find(|s| s.0 == 2 && s.2[0] == 3).map(|s| s.2[2]).unwrap();
        let outs: Vec<u32> = seen.iter().filter(|s| s.0 == 2 && (s.2[0] == 4 || s.2[0] == 5)).map(|s| s.2[2]).collect();
        // one power object per output, asked once
        let got: Vec<(u32, u32)> = seen.iter().filter(|s| s.0 == power && s.1 == GET_OUTPUT_POWER).map(|s| (s.2[0], s.2[1])).collect();
        assert_eq!(got.iter().map(|g| g.1).collect::<Vec<_>>(), [outs[1], outs[0]]); // HDMI first (off), then the panel (on)
        let mode = |p: u32| seen.iter().filter(|s| s.0 == p && s.1 == SET_MODE).map(|s| s.2[0]).collect::<Vec<_>>();
        assert_eq!(mode(got[0].0), [0, 1]); // the monitor: off, then on
        assert_eq!(mode(got[1].0), [1]); // the panel: never powered off
    }

    #[test]
    fn no_idle_notifier_no_watch() {
        let (client, fake) = fake(0);
        let watch = IdleWatch::open(client);
        assert!(!watch.ok());
        assert_eq!(watch.watch(10, Rc::new(|| {}), Rc::new(|| {})), 0);
        assert!(!watch.displays(false, None));
        drop(watch);
        fake.seen.join().unwrap();
    }

    #[test]
    fn no_compositor_no_watch() {
        let dir = tempfile::tempdir().unwrap();
        let watch = IdleWatch::connect_to(&dir.path().join("wayland-9"));
        assert!(!watch.ok() && !watch.input_idle() && !watch.can_power());
        watch.close(); // (nothing to close: fine)
    }

    /// Vini: plugged-in monitors only went black, the pointer on them.
    /// The laptop's own panel is never powered off (only its backlight);
    /// every other display is.
    #[test]
    fn test_laptop_dims_its_panel_and_turns_monitors_off() {
        let calls: RefCell<Vec<(bool, Option<[bool; 4]>)>> = RefCell::default();
        let dims: RefCell<Vec<bool>> = RefCell::default();
        let displays = |on: bool, only: Option<&dyn Fn(&str) -> bool>| {
            calls.borrow_mut().push((on, only.map(|f| [f("eDP-1"), f("HDMI-A-1"), f("DP-2"), f("")])));
            true
        };
        displays_dark_with(&displays, true, true, &|d| dims.borrow_mut().push(d));
        displays_dark_with(&displays, false, true, &|d| dims.borrow_mut().push(d));
        assert_eq!(*dims.borrow(), [true, false]);
        // the panel: never; unknown: left alone
        assert_eq!(*calls.borrow(), [(false, Some([false, true, true, false])), (true, Some([false, true, true, false]))]);
    }

    #[test]
    fn test_desktop_still_powers_off() {
        let calls: RefCell<Vec<(bool, bool)>> = RefCell::default();
        let displays = |on: bool, only: Option<&dyn Fn(&str) -> bool>| {
            calls.borrow_mut().push((on, only.is_some()));
            true
        };
        let dimmed = Cell::new(false);
        displays_dark_with(&displays, true, false, &|_| dimmed.set(true));
        displays_dark_with(&displays, false, false, &|_| dimmed.set(true));
        assert_eq!(*calls.borrow(), [(false, false), (true, false)]);
        assert!(!dimmed.get());
    }
}
