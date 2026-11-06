//! Keys pressed for the app in front (wl/vkeyboard.py,
//! virtual-keyboard-unstable-v1): here the pickers' Ctrl+V.
//!
//! Not `wtype`: it makes up its own keymap and gives the first key it types
//! keycode 1 -- Esc to Wayfire and to web pages, which read keycodes (the
//! Instagram web app saw Ctrl+Esc and pasted nothing: Vini). Here the keys
//! go out with their real keycodes (evdev) and a plain US keymap: Wayfire
//! and the app read the same key.
//!
//! The workspace has no Wayland client library (and the Python one's
//! pywayland has no Rust twin in it), so the few requests this needs are
//! written on the wire by hand, on a short connection of its own. Belongs
//! in sonata-backend once the menu bar's New Tab (apptabs.py) needs it too.

use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

/// evdev keycodes (linux/input-event-codes.h)
pub fn code(key: &str) -> Option<u32> {
    let named = [
        ("Escape", 1), ("BackSpace", 14), ("Tab", 15), ("Return", 28), ("space", 57), ("Delete", 111),
        ("Home", 102), ("Up", 103), ("Page_Up", 104), ("Left", 105), ("Right", 106), ("End", 107), ("Down", 108),
        ("Page_Down", 109), ("minus", 12), ("equal", 13), ("bracketleft", 26), ("bracketright", 27),
    ];
    if let Some((_, c)) = named.iter().find(|(k, _)| *k == key) {
        return Some(*c);
    }
    if let Some(n) = key.strip_prefix('F').and_then(|n| n.parse::<u32>().ok()).filter(|n| (1..=10).contains(n)) {
        return Some(58 + n);
    }
    let mut chars = key.chars();
    let (Some(c), None) = (chars.next(), chars.next()) else { return None };
    for (row, first) in [("1234567890", 2), ("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)] {
        if let Some(i) = row.find(c) {
            return Some(first + i as u32);
        }
    }
    None
}

/// modifier: (its key, its bit in the US keymap's modifier mask)
fn modifier(name: &str) -> Option<(u32, u32)> {
    match name {
        "shift" => Some((42, 1)),
        "ctrl" => Some((29, 4)),
        "alt" => Some((56, 8)),
        "super" => Some((125, 64)),
        _ => None,
    }
}

pub const KEYMAP: &str = "xkb_keymap {\n  xkb_keycodes { include \"evdev+aliases(qwerty)\" };\n  \
xkb_types { include \"complete\" };\n  xkb_compat { include \"complete\" };\n  \
xkb_symbols { include \"pc+us+inet(evdev)\" };\n};\n";
const PRESSED: u32 = 1;
const RELEASED: u32 = 0;
const MANAGER: &str = "zwp_virtual_keyboard_manager_v1";

fn ms() -> u32 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: a valid timespec to fill
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
    (ts.tv_sec as u64 * 1000 + ts.tv_nsec as u64 / 1_000_000) as u32
}

fn socket_path() -> Option<PathBuf> {
    let name = std::env::var("WAYLAND_DISPLAY").ok().filter(|s| !s.is_empty()).unwrap_or_else(|| "wayland-0".into());
    if name.starts_with('/') {
        return Some(name.into());
    }
    Some(PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR")?).join(name))
}

/// Press and release `key` with `mods` held. One short Wayland connection.
/// False: no virtual keyboard here.
pub fn press(key: &str, mods: &[&str]) -> bool {
    if code(key).is_none() || mods.iter().any(|m| modifier(m).is_none()) {
        return false;
    }
    let Some(stream) = socket_path().and_then(|p| UnixStream::connect(p).ok()) else {
        eprintln!("sonata2: no virtual keyboard (no Wayland display)");
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
    match press_on(&stream, key, mods) {
        Ok(found) => found,
        Err(e) => {
            eprintln!("sonata2: virtual keyboard: {e}");
            false
        }
    }
}

enum Arg<'a> {
    Uint(u32),
    Str(&'a str),
}

/// A Wayland message: object, opcode, then the arguments (native endian,
/// strings with their NUL, padded to 4 bytes).
fn message(obj: u32, op: u16, args: &[Arg]) -> Vec<u8> {
    let mut body = Vec::new();
    for a in args {
        match a {
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

/// wl_registry.global's arguments: name, interface, version.
fn global(body: &[u8]) -> Option<(u32, String)> {
    let word = |i: usize| body.get(i..i + 4).map(|b| u32::from_ne_bytes([b[0], b[1], b[2], b[3]]));
    let name = word(0)?;
    let len = word(4)? as usize;
    let iface = body.get(8..8 + len.checked_sub(1)?)?;
    Some((name, String::from_utf8_lossy(iface).into_owned()))
}

struct Conn<'a> {
    s: &'a UnixStream,
    next: u32,
    inbuf: Vec<u8>,
}

impl Conn<'_> {
    fn id(&mut self) -> u32 {
        self.next += 1;
        self.next
    }

    fn send(&mut self, obj: u32, op: u16, args: &[Arg]) -> io::Result<()> {
        self.s.write_all(&message(obj, op, args))
    }

    /// A request carrying a file descriptor (SCM_RIGHTS beside its bytes).
    fn send_fd(&mut self, obj: u32, op: u16, args: &[Arg], fd: RawFd) -> io::Result<()> {
        let data = message(obj, op, args);
        let space = unsafe { libc::CMSG_SPACE(std::mem::size_of::<RawFd>() as u32) } as usize;
        let mut cbuf = vec![0u64; space.div_ceil(8)];
        // SAFETY: the iovec and control buffer outlive the call; the header
        // is filled through the CMSG_* macros inside the buffer's size
        let n = unsafe {
            let mut iov = libc::iovec { iov_base: data.as_ptr() as *mut libc::c_void, iov_len: data.len() };
            let mut msg: libc::msghdr = std::mem::zeroed();
            msg.msg_iov = &mut iov;
            msg.msg_iovlen = 1;
            msg.msg_control = cbuf.as_mut_ptr() as *mut libc::c_void;
            msg.msg_controllen = space as _;
            let cmsg = libc::CMSG_FIRSTHDR(&msg);
            (*cmsg).cmsg_level = libc::SOL_SOCKET;
            (*cmsg).cmsg_type = libc::SCM_RIGHTS;
            (*cmsg).cmsg_len = libc::CMSG_LEN(std::mem::size_of::<RawFd>() as u32) as _;
            std::ptr::write_unaligned(libc::CMSG_DATA(cmsg) as *mut RawFd, fd);
            libc::sendmsg(self.s.as_raw_fd(), &msg, libc::MSG_NOSIGNAL)
        };
        if n < 0 {
            return Err(io::Error::last_os_error());
        }
        if n as usize != data.len() {
            return Err(io::Error::new(io::ErrorKind::WriteZero, "short write"));
        }
        Ok(())
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
            let n = (&*self.s).read(&mut chunk)?;
            if n == 0 {
                return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "the compositor hung up"));
            }
            self.inbuf.extend_from_slice(&chunk[..n]);
        }
    }
}

fn press_on(s: &UnixStream, key: &str, mods: &[&str]) -> io::Result<bool> {
    let code = code(key).ok_or_else(|| io::Error::other("unknown key"))?;
    let mut c = Conn { s, next: 1, inbuf: Vec::new() };
    let registry = c.id();
    c.send(1, 1, &[Arg::Uint(registry)])?; // wl_display.get_registry
    let (mut mgr, mut seat) = (None, None);
    c.roundtrip(&mut |obj, op, body| {
        if obj == registry && op == 0 {
            match global(body) {
                Some((name, iface)) if iface == MANAGER => mgr = Some(name),
                Some((name, iface)) if iface == "wl_seat" && seat.is_none() => seat = Some(name),
                _ => {}
            }
        }
    })?;
    let (Some(mgr_name), Some(seat_name)) = (mgr, seat) else { return Ok(false) };
    let mgr = c.id();
    c.send(registry, 0, &[Arg::Uint(mgr_name), Arg::Str(MANAGER), Arg::Uint(1), Arg::Uint(mgr)])?;
    let seat = c.id();
    c.send(registry, 0, &[Arg::Uint(seat_name), Arg::Str("wl_seat"), Arg::Uint(1), Arg::Uint(seat)])?;
    let kb = c.id();
    c.send(mgr, 0, &[Arg::Uint(seat), Arg::Uint(kb)])?; // create_virtual_keyboard

    let mut data = KEYMAP.as_bytes().to_vec();
    data.push(0);
    // SAFETY: a fresh memfd, written and closed here
    let fd = unsafe { libc::memfd_create(c"sonata2-keymap".as_ptr(), libc::MFD_CLOEXEC) };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    let sent = (|| {
        let written = unsafe { libc::write(fd, data.as_ptr() as *const libc::c_void, data.len()) };
        if written != data.len() as isize {
            return Err(io::Error::last_os_error());
        }
        c.send_fd(kb, 0, &[Arg::Uint(1), Arg::Uint(data.len() as u32)], fd) // keymap, 1: XKB_V1
    })();
    unsafe { libc::close(fd) };
    sent?;

    let mut mask = 0;
    for m in mods {
        // modifiers down, in order
        let (key, bit) = modifier(m).ok_or_else(|| io::Error::other("unknown modifier"))?;
        mask |= bit;
        c.send(kb, 1, &[Arg::Uint(ms()), Arg::Uint(key), Arg::Uint(PRESSED)])?;
        c.send(kb, 2, &[Arg::Uint(mask), Arg::Uint(0), Arg::Uint(0), Arg::Uint(0)])?;
    }
    c.send(kb, 1, &[Arg::Uint(ms()), Arg::Uint(code), Arg::Uint(PRESSED)])?;
    c.send(kb, 1, &[Arg::Uint(ms()), Arg::Uint(code), Arg::Uint(RELEASED)])?;
    for m in mods.iter().rev() {
        let (key, bit) = modifier(m).ok_or_else(|| io::Error::other("unknown modifier"))?;
        mask &= !bit;
        c.send(kb, 1, &[Arg::Uint(ms()), Arg::Uint(key), Arg::Uint(RELEASED)])?;
        c.send(kb, 2, &[Arg::Uint(mask), Arg::Uint(0), Arg::Uint(0), Arg::Uint(0)])?;
    }
    c.roundtrip(&mut |_, _, _| {})?;
    c.send(kb, 3, &[])?; // destroy
    c.roundtrip(&mut |_, _, _| {})?;
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn word(b: &[u8], i: usize) -> u32 {
        u32::from_ne_bytes([b[i], b[i + 1], b[i + 2], b[i + 3]])
    }

    /// A compositor with a seat and (or not) the virtual keyboard manager:
    /// answers the registry and every sync, and gives back what the
    /// client asked of the keyboard (object 5: registry 2, callback 3,
    /// manager 4... see press_on).
    fn fake_compositor(with_manager: bool, key: &str, mods: &[&str]) -> (bool, Vec<(u16, Vec<u32>)>) {
        let (client, server) = UnixStream::pair().unwrap();
        let handle = std::thread::spawn(move || {
            let mut server = server;
            let (mut buf, mut chunk, mut seen) = (Vec::new(), [0u8; 4096], Vec::new());
            let mut first = true;
            let mut kb = 0;
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
                                globals.push(message(2, 0, &[Arg::Uint(2), Arg::Str(MANAGER), Arg::Uint(1)]));
                            }
                            for g in globals {
                                server.write_all(&g).unwrap();
                            }
                        }
                        server.write_all(&message(cb, 0, &[Arg::Uint(0)])).unwrap();
                    } else if obj == 4 && op == 0 {
                        kb = word(&body, 4);
                    } else if obj == kb && kb != 0 {
                        seen.push((op, body.chunks(4).map(|c| word(c, 0)).collect::<Vec<u32>>()));
                    }
                }
            }
        });
        let found = press_on(&client, key, mods).unwrap();
        drop(client);
        (found, handle.join().unwrap())
    }

    #[test]
    fn instagram_took_no_emoji_from_the_character_viewer() {
        // Vini: in the Instagram web app emoji from the Character Viewer
        // never pasted -- wtype gave its "v" keycode 1, the page saw
        // Ctrl+Esc. Ctrl+V goes out with V's real keycode.
        assert_eq!(code("v"), Some(47)); // KEY_V
        let (found, seen) = fake_compositor(true, "v", &["ctrl"]);
        assert!(found);
        let keys: Vec<(u32, u32)> = seen.iter().filter(|(op, _)| *op == 1).map(|(_, a)| (a[1], a[2])).collect();
        assert_eq!(keys, [(29, PRESSED), (47, PRESSED), (47, RELEASED), (29, RELEASED)]);
        let masks: Vec<u32> = seen.iter().filter(|(op, _)| *op == 2).map(|(_, a)| a[0]).collect();
        assert_eq!(masks, [4, 0]);
        assert_eq!(seen.first().map(|s| s.0), Some(0)); // the keymap first
        assert_eq!(seen.last().map(|s| s.0), Some(3)); // destroyed after
    }

    #[test]
    fn terminals_get_ctrl_shift_v() {
        let (_, seen) = fake_compositor(true, "v", &["ctrl", "shift"]);
        let keys: Vec<u32> = seen.iter().filter(|(op, _)| *op == 1).map(|(_, a)| a[1]).collect();
        assert_eq!(keys, [29, 42, 47, 47, 42, 29]);
    }

    #[test]
    fn no_manager_no_keys() {
        let (found, seen) = fake_compositor(false, "v", &["ctrl"]);
        assert!(!found && seen.is_empty());
    }

    #[test]
    fn keycodes_and_unknown_keys() {
        assert_eq!((code("q"), code("a"), code("z"), code("1"), code("0")), (Some(16), Some(30), Some(44), Some(2), Some(11)));
        assert_eq!((code("F1"), code("F10"), code("Return"), code("F11")), (Some(59), Some(68), Some(28), None));
        assert!(!press("v", &["hyper"]) && !press("ç", &[])); // refused before connecting
    }

    #[test]
    fn strings_are_padded() {
        let m = message(2, 0, &[Arg::Str("wl_seat")]);
        assert_eq!(m.len(), 8 + 4 + 8); // "wl_seat\0" is 8 bytes
        assert_eq!(word(&m, 4) >> 16, m.len() as u32);
        assert_eq!(global(&message(2, 0, &[Arg::Uint(9), Arg::Str("wl_seat"), Arg::Uint(7)])[8..]), Some((9, "wl_seat".into())));
    }
}
