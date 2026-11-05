//! A virtual mouse (gamepad/vpointer.py: wlr-virtual-pointer-unstable-v1,
//! which Wayfire offers) on a Wayland connection of its own: only requests
//! are sent, nothing has to be dispatched.
//!
//! The workspace has no Wayland client library (the Python one used
//! pywayland), so the few requests this needs are written on the wire by
//! hand, as the clipboard's vkeyboard.rs does for the virtual keyboard.
//!
//! ```ignore
//! let vp = VirtualPointer::connect();   // vp.ok() is false without the protocol
//! vp.move_by(dx, dy); vp.button(BTN_LEFT, true); vp.scroll(dx, dy);
//! ```

use std::io::{self, Read, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

pub const BTN_LEFT: u32 = 0x110;
pub const BTN_RIGHT: u32 = 0x111;
pub const BTN_MIDDLE: u32 = 0x112;
const MANAGER: &str = "zwlr_virtual_pointer_manager_v1";

// zwlr_virtual_pointer_v1's requests
const MOTION: u16 = 0;
const BUTTON: u16 = 2;
const AXIS: u16 = 3;
const FRAME: u16 = 4;
const AXIS_SOURCE: u16 = 5;
const DESTROY: u16 = 8;
const CONTINUOUS: u32 = 2; // wl_pointer.axis_source.continuous
const VERTICAL: u32 = 0;
const HORIZONTAL: u32 = 1;

fn ms() -> u32 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: a valid timespec to fill
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    (ts.tv_sec as u64 * 1000 + ts.tv_nsec as u64 / 1_000_000) as u32
}

fn socket_path() -> Option<PathBuf> {
    let name = std::env::var("WAYLAND_DISPLAY").ok().filter(|s| !s.is_empty())?;
    if name.starts_with('/') {
        return Some(name.into());
    }
    Some(PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR")?).join(name))
}

#[derive(Clone, Copy)]
enum Arg<'a> {
    Uint(u32),
    /// wl_fixed: 24.8 fixed point
    Fixed(f64),
    Str(&'a str),
}

/// A Wayland message: object, opcode, then the arguments (native endian,
/// strings with their NUL, padded to 4 bytes).
fn message(obj: u32, op: u16, args: &[Arg]) -> Vec<u8> {
    let mut body = Vec::new();
    for a in args {
        match *a {
            Arg::Uint(v) => body.extend(v.to_ne_bytes()),
            Arg::Fixed(v) => body.extend(((v * 256.0).round() as i32).to_ne_bytes()),
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

/// wl_registry.global's arguments: name, interface, version.
fn global(body: &[u8]) -> Option<(u32, String, u32)> {
    let word = |i: usize| body.get(i..i + 4).map(|b| u32::from_ne_bytes([b[0], b[1], b[2], b[3]]));
    let name = word(0)?;
    let len = word(4)? as usize;
    let iface = body.get(8..8 + len.checked_sub(1)?)?;
    let version = word(8 + len.div_ceil(4) * 4)?;
    Some((name, String::from_utf8_lossy(iface).into_owned(), version))
}

/// What a virtual mouse does (a fake one in tests).
pub trait Pointer {
    fn ok(&self) -> bool;
    fn move_by(&mut self, dx: i32, dy: i32);
    fn button(&mut self, button: u32, pressed: bool);
    /// Smooth scrolling in pixels (positive dy: down).
    fn scroll(&mut self, dx: f64, dy: f64);
    fn close(&mut self);
}

pub struct VirtualPointer {
    s: Option<UnixStream>,
    next: u32,
    pointer: u32,
    inbuf: Vec<u8>,
}

impl VirtualPointer {
    /// Its own connection to the compositor; ok() false without Wayland or
    /// without the protocol (no virtual mouse then).
    pub fn connect() -> VirtualPointer {
        let mut vp = VirtualPointer { s: None, next: 1, pointer: 0, inbuf: Vec::new() };
        let Some(stream) = socket_path().and_then(|p| UnixStream::connect(p).ok()) else {
            eprintln!("sonata2-gamepad: no virtual pointer (no Wayland display)");
            return vp;
        };
        let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
        let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));
        vp.s = Some(stream);
        match vp.setup() {
            Ok(true) => {}
            Ok(false) => vp.s = None, // a compositor without the protocol
            Err(e) => {
                eprintln!("sonata2-gamepad: no virtual pointer ({e})");
                vp.s = None;
            }
        }
        vp
    }

    #[cfg(test)]
    fn on(stream: UnixStream) -> io::Result<VirtualPointer> {
        let mut vp = VirtualPointer { s: Some(stream), next: 1, pointer: 0, inbuf: Vec::new() };
        if !vp.setup()? {
            vp.s = None;
        }
        Ok(vp)
    }

    fn id(&mut self) -> u32 {
        self.next += 1;
        self.next
    }

    fn send(&mut self, obj: u32, op: u16, args: &[Arg]) -> io::Result<()> {
        let data = message(obj, op, args);
        match &self.s {
            Some(s) => (&*s).write_all(&data),
            None => Err(io::Error::from(io::ErrorKind::NotConnected)),
        }
    }

    /// wl_display.sync and wait for its callback; other events go to
    /// `event`; wl_display.error fails.
    fn roundtrip(&mut self, event: &mut dyn FnMut(u32, u16, &[u8])) -> io::Result<()> {
        let cb = self.id();
        self.send(1, 0, &[Arg::Uint(cb)])?;
        let mut chunk = [0u8; 4096];
        loop {
            let (msgs, used) = messages(&self.inbuf);
            self.inbuf.drain(..used);
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
            let Some(s) = &self.s else { return Err(io::Error::from(io::ErrorKind::NotConnected)) };
            let n = (&*s).read(&mut chunk)?;
            if n == 0 {
                return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "the compositor hung up"));
            }
            self.inbuf.extend_from_slice(&chunk[..n]);
        }
    }

    /// The registry, the manager and a seat, then the pointer. False: no
    /// manager here.
    fn setup(&mut self) -> io::Result<bool> {
        let registry = self.id();
        self.send(1, 1, &[Arg::Uint(registry)])?; // wl_display.get_registry
        let (mut mgr, mut seat) = (None, None);
        self.roundtrip(&mut |obj, op, body| {
            if obj == registry && op == 0 {
                match global(body) {
                    Some((name, iface, version)) if iface == MANAGER => mgr = Some((name, version)),
                    Some((name, iface, _)) if iface == "wl_seat" && seat.is_none() => seat = Some(name),
                    _ => {}
                }
            }
        })?;
        let Some((mgr_name, version)) = mgr else { return Ok(false) };
        let mgr = self.id();
        self.send(registry, 0, &[Arg::Uint(mgr_name), Arg::Str(MANAGER), Arg::Uint(version.min(1)), Arg::Uint(mgr)])?;
        // no seat: null, the compositor picks its own
        let seat = match seat {
            Some(name) => {
                let id = self.id();
                self.send(registry, 0, &[Arg::Uint(name), Arg::Str("wl_seat"), Arg::Uint(1), Arg::Uint(id)])?;
                id
            }
            None => 0,
        };
        let pointer = self.id();
        self.send(mgr, 0, &[Arg::Uint(seat), Arg::Uint(pointer)])?; // create_virtual_pointer
        self.roundtrip(&mut |_, _, _| {})?;
        self.pointer = pointer;
        Ok(true)
    }

    /// Requests, then a frame; a connection that fails is given up (the
    /// next use makes a new one: the service checks ok()).
    fn requests(&mut self, reqs: &[(u16, &[Arg])]) {
        let p = self.pointer;
        let sent = reqs.iter().try_for_each(|(op, args)| self.send(p, *op, args)).and_then(|_| self.send(p, FRAME, &[]));
        if sent.is_err() {
            self.s = None;
        }
    }
}

impl Pointer for VirtualPointer {
    fn ok(&self) -> bool {
        self.s.is_some()
    }

    fn move_by(&mut self, dx: i32, dy: i32) {
        if self.ok() && (dx != 0 || dy != 0) {
            self.requests(&[(MOTION, &[Arg::Uint(ms()), Arg::Fixed(dx as f64), Arg::Fixed(dy as f64)])]);
        }
    }

    fn button(&mut self, button: u32, pressed: bool) {
        if self.ok() {
            self.requests(&[(BUTTON, &[Arg::Uint(ms()), Arg::Uint(button), Arg::Uint(pressed as u32)])]);
        }
    }

    fn scroll(&mut self, dx: f64, dy: f64) {
        if !self.ok() || (dx == 0.0 && dy == 0.0) {
            return;
        }
        let t = ms();
        let (vert, horiz) = ([Arg::Uint(t), Arg::Uint(VERTICAL), Arg::Fixed(dy)], [Arg::Uint(t), Arg::Uint(HORIZONTAL), Arg::Fixed(dx)]);
        let mut reqs: Vec<(u16, &[Arg])> = vec![(AXIS_SOURCE, &[Arg::Uint(CONTINUOUS)])];
        if dy != 0.0 {
            reqs.push((AXIS, &vert));
        }
        if dx != 0.0 {
            reqs.push((AXIS, &horiz));
        }
        self.requests(&reqs);
    }

    fn close(&mut self) {
        if self.ok() {
            let p = self.pointer;
            if self.send(p, DESTROY, &[]).is_ok() {
                let _ = self.roundtrip(&mut |_, _, _| {});
            }
        }
        self.s = None;
    }
}

impl Drop for VirtualPointer {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn word(b: &[u8], i: usize) -> u32 {
        u32::from_ne_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]])
    }

    /// A compositor with a seat and (or not) the virtual pointer manager:
    /// answers the registry and every sync, and gives back what the client
    /// asked of the pointer, as (opcode, words).
    fn fake_compositor(with_manager: bool, drive: impl FnOnce(&mut VirtualPointer)) -> (bool, Vec<(u16, Vec<u32>)>) {
        let (client, server) = UnixStream::pair().unwrap();
        let handle = std::thread::spawn(move || {
            let mut server = server;
            let (mut buf, mut chunk, mut seen) = (Vec::new(), [0u8; 4096], Vec::new());
            let (mut first, mut mgr, mut ptr) = (true, 0, 0);
            loop {
                let n = server.read(&mut chunk).unwrap_or(0);
                if n == 0 {
                    return seen;
                }
                buf.extend_from_slice(&chunk[..n]);
                let (msgs, used) = messages(&buf);
                buf.drain(..used);
                for (obj, op, body) in msgs {
                    if obj == 1 && op == 0 {
                        let cb = word(&body, 0);
                        if first {
                            first = false;
                            let mut globals = vec![message(2, 0, &[Arg::Uint(1), Arg::Str("wl_seat"), Arg::Uint(7)])];
                            if with_manager {
                                globals.push(message(2, 0, &[Arg::Uint(2), Arg::Str(MANAGER), Arg::Uint(2)]));
                            }
                            for g in globals {
                                server.write_all(&g).unwrap();
                            }
                        }
                        server.write_all(&message(cb, 0, &[Arg::Uint(0)])).unwrap();
                    } else if obj == 2 && op == 0 && global(&body).is_some_and(|g| g.1 == MANAGER) {
                        // bind: name, interface, version, new id
                        let len = word(&body, 4) as usize;
                        let at = 8 + len.div_ceil(4) * 4;
                        assert_eq!(word(&body, at), 1); // version 1 asked, as the Python one
                        mgr = word(&body, at + 4);
                    } else if obj == mgr && mgr != 0 && op == 0 {
                        assert_ne!(word(&body, 0), 0); // the seat
                        ptr = word(&body, 4);
                    } else if obj == ptr && ptr != 0 {
                        seen.push((op, body.chunks(4).map(|c| word(c, 0)).collect::<Vec<u32>>()));
                    }
                }
            }
        });
        let mut vp = VirtualPointer::on(client).unwrap();
        let ok = vp.ok();
        drive(&mut vp);
        vp.close();
        drop(vp);
        (ok, handle.join().unwrap())
    }

    #[test]
    fn moves_clicks_and_scrolls_on_the_wire() {
        let (ok, seen) = fake_compositor(true, |vp| {
            vp.move_by(3, -2);
            vp.move_by(0, 0); // nothing
            vp.button(BTN_LEFT, true);
            vp.button(BTN_LEFT, false);
            vp.scroll(0.0, 12.5);
        });
        assert!(ok);
        let ops: Vec<u16> = seen.iter().map(|s| s.0).collect();
        assert_eq!(ops, [MOTION, FRAME, BUTTON, FRAME, BUTTON, FRAME, AXIS_SOURCE, AXIS, FRAME, DESTROY]);
        assert_eq!((seen[0].1[1] as i32, seen[0].1[2] as i32), (3 * 256, -2 * 256)); // wl_fixed
        assert_eq!((seen[2].1[1], seen[2].1[2]), (BTN_LEFT, 1));
        assert_eq!(seen[4].1[2], 0);
        assert_eq!(seen[6].1, [CONTINUOUS]);
        assert_eq!((seen[7].1[1], seen[7].1[2]), (VERTICAL, 3200)); // 12.5 px
    }

    #[test]
    fn no_manager_no_pointer() {
        let (ok, seen) = fake_compositor(false, |vp| {
            vp.move_by(5, 5);
            vp.button(BTN_RIGHT, true);
        });
        assert!(!ok && seen.is_empty());
    }

    #[test]
    fn no_wayland_no_pointer() {
        // the test run has no WAYLAND_DISPLAY: never a real compositor
        if std::env::var_os("WAYLAND_DISPLAY").is_none() {
            assert!(!VirtualPointer::connect().ok());
        }
    }

    #[test]
    fn globals_are_read_with_their_version() {
        let m = message(2, 0, &[Arg::Uint(9), Arg::Str("wl_seat"), Arg::Uint(7)]);
        assert_eq!(word(&m, 4) >> 16, m.len() as u32);
        assert_eq!(global(&m[8..]), Some((9, "wl_seat".into(), 7)));
    }
}
