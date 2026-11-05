//! Game controllers read straight from /dev/input (gamepad/evdev.py: no
//! evdev library, the kernel's ioctls by hand).
//!
//! The Linux gamepad layout (kernel docs, gamepad.rst): face buttons
//! BTN_SOUTH (A / cross) .. , shoulders BTN_TL/TR, sticks ABS_X/Y and
//! ABS_RX/RY, triggers ABS_Z/RZ (or ABS_BRAKE/GAS), d-pad ABS_HAT0X/Y or
//! BTN_DPAD_*. Controllers are readable by the logged-in user (logind's
//! "uaccess" for ID_INPUT_JOYSTICK devices). Devices are only listened to:
//! games keep receiving them -- except while the Game Bar is open over a
//! game ([`Gamepad::grab`]), so navigating it doesn't also play the game.
//!
//! ```ignore
//! let pads = find_gamepads(DEV_INPUT);              // ["/dev/input/event17", ...]
//! let pad = Gamepad::open(path, on_event)?;         // on_event(path, Some((type, code, value))); None: gone
//! pad.norm(ABS_X, raw);                             // -> -1.0 .. 1.0 (triggers 0 .. 1)
//! ```

use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::io;
use std::os::fd::RawFd;
use std::rc::{Rc, Weak};

pub const DEV_INPUT: &str = "/dev/input";

pub const EV_KEY: u16 = 0x01;
pub const EV_ABS: u16 = 0x03;
/// struct input_event on 64-bit: two longs (the time), type, code, value.
pub const EVENT_SIZE: usize = 24;

// buttons
pub const BTN_SOUTH: u16 = 0x130;
pub const BTN_EAST: u16 = 0x131;
pub const BTN_NORTH: u16 = 0x133;
pub const BTN_WEST: u16 = 0x134;
pub const BTN_TL: u16 = 0x136;
pub const BTN_TR: u16 = 0x137;
pub const BTN_TL2: u16 = 0x138;
pub const BTN_TR2: u16 = 0x139;
pub const BTN_SELECT: u16 = 0x13a;
pub const BTN_START: u16 = 0x13b;
pub const BTN_MODE: u16 = 0x13c;
pub const BTN_THUMBL: u16 = 0x13d;
pub const BTN_THUMBR: u16 = 0x13e;
pub const BTN_DPAD_UP: u16 = 0x220;
pub const BTN_DPAD_DOWN: u16 = 0x221;
pub const BTN_DPAD_LEFT: u16 = 0x222;
pub const BTN_DPAD_RIGHT: u16 = 0x223;
pub const BTN_GAMEPAD: u16 = BTN_SOUTH;
/// The Xbox button over Bluetooth (a "Consumer Control" device of its own).
pub const KEY_HOMEPAGE: u16 = 0xac;
// axes
pub const ABS_X: u16 = 0x00;
pub const ABS_Y: u16 = 0x01;
pub const ABS_Z: u16 = 0x02;
pub const ABS_RX: u16 = 0x03;
pub const ABS_RY: u16 = 0x04;
pub const ABS_RZ: u16 = 0x05;
pub const ABS_GAS: u16 = 0x09;
pub const ABS_BRAKE: u16 = 0x0a;
pub const ABS_HAT0X: u16 = 0x10;
pub const ABS_HAT0Y: u16 = 0x11;
pub const TRIGGERS: [u16; 4] = [ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE];

/// One event as the service gets it: (type, code, value).
pub type Event = (u16, u16, i32);
/// on_event(path, Some(event)); None once the controller is gone.
pub type OnEvent = Rc<dyn Fn(&str, Option<Event>)>;

fn ioc_read(nr: u32, size: usize) -> libc::c_ulong {
    (2 << 30) | ((size as libc::c_ulong) << 16) | ((b'E' as libc::c_ulong) << 8) | nr as libc::c_ulong
}

/// _IOW('E', 0x90, int)
const EVIOCGRAB: libc::c_ulong = (1 << 30) | (4 << 16) | ((b'E' as libc::c_ulong) << 8) | 0x90;

fn open_fd(path: &str) -> io::Result<RawFd> {
    let c = std::ffi::CString::new(path).map_err(|_| io::Error::from(io::ErrorKind::InvalidInput))?;
    // SAFETY: a NUL-terminated path
    let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_NONBLOCK | libc::O_CLOEXEC) };
    if fd < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(fd)
    }
}

fn close_fd(fd: RawFd) {
    // SAFETY: an fd this module opened, closed once
    unsafe { libc::close(fd) };
}

fn bits(fd: RawFd, ev_type: u16, count: usize) -> Vec<u8> {
    let mut buf = vec![0u8; count.div_ceil(8)];
    // SAFETY: EVIOCGBIT writes at most buf.len() bytes (the size is in the request);
    // on a file that isn't a device it fails and the buffer stays zero
    unsafe { libc::ioctl(fd, ioc_read(0x20 + ev_type as u32, buf.len()) as _, buf.as_mut_ptr()) };
    buf
}

pub fn has(bits: &[u8], code: u16) -> bool {
    let code = code as usize;
    bits.get(code / 8).is_some_and(|b| b & (1 << (code % 8)) != 0)
}

fn name_of(fd: RawFd) -> String {
    let mut buf = vec![0u8; 256];
    // SAFETY: EVIOCGNAME writes at most buf.len() bytes
    let n = unsafe { libc::ioctl(fd, ioc_read(0x06, buf.len()) as _, buf.as_mut_ptr()) };
    if n < 0 {
        return String::new();
    }
    let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    String::from_utf8_lossy(&buf[..end]).into_owned()
}

/// A device with the gamepad's south button and two stick axes.
pub fn is_gamepad(path: &str) -> bool {
    let Ok(fd) = open_fd(path) else { return false };
    let (keys, axes) = (bits(fd, EV_KEY, 0x300), bits(fd, EV_ABS, 0x40));
    close_fd(fd);
    has(&keys, BTN_GAMEPAD) && has(&axes, ABS_X) && has(&axes, ABS_Y)
}

/// The event* nodes of `folder`, sorted as Python sorts names.
fn events(folder: &str) -> Vec<String> {
    let Ok(rd) = std::fs::read_dir(folder) else { return Vec::new() };
    let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| n.starts_with("event")).collect();
    names.sort();
    names.into_iter().map(|n| format!("{}/{n}", folder.trim_end_matches('/'))).collect()
}

pub fn find_gamepads(folder: &str) -> Vec<String> {
    events(folder).into_iter().filter(|p| is_gamepad(p)).collect()
}

/// Extra devices of a controller that carry its Xbox / home button: over
/// Bluetooth, Xbox controllers send it from "<name> Consumer Control" as
/// KEY_HOMEPAGE instead of BTN_MODE on the gamepad itself.
pub fn find_guide_devices(pad_names: &[String], folder: &str) -> Vec<String> {
    let mut found = Vec::new();
    for path in events(folder) {
        let Ok(fd) = open_fd(&path) else { continue };
        let name = name_of(fd);
        if !pad_names.contains(&name)
            && pad_names.iter().any(|p| !p.is_empty() && name.starts_with(p.as_str()))
            && has(&bits(fd, EV_KEY, 0x300), KEY_HOMEPAGE)
        {
            found.push(path);
        }
        close_fd(fd);
    }
    found
}

pub fn gamepad_name(path: &str) -> String {
    let Ok(fd) = open_fd(path) else { return String::new() };
    let name = name_of(fd);
    close_fd(fd);
    name
}

/// -1 .. 1 for a stick (0 .. 1 for a trigger) from a raw value in the
/// device's range (lo, hi); the usual 16-bit range when it gave none.
pub fn norm_in(range: Option<(i32, i32)>, code: u16, raw: f64) -> f64 {
    let (lo, hi) = range.map(|(l, h)| (l as f64, h as f64)).unwrap_or((-32768.0, 32767.0));
    if TRIGGERS.contains(&code) {
        return ((raw - lo) / (hi - lo)).clamp(0.0, 1.0);
    }
    let mid = (lo + hi) / 2.0;
    ((raw - mid) / ((hi - lo) / 2.0)).clamp(-1.0, 1.0)
}

/// The EV_KEY / EV_ABS events in what one read() gave (SYN and the rest
/// are left out; a torn event at the end too).
pub fn parse(data: &[u8]) -> Vec<Event> {
    data.chunks_exact(EVENT_SIZE)
        .filter_map(|e| {
            let typ = u16::from_ne_bytes([e[16], e[17]]);
            let code = u16::from_ne_bytes([e[18], e[19]]);
            let value = i32::from_ne_bytes([e[20], e[21], e[22], e[23]]);
            [EV_KEY, EV_ABS].contains(&typ).then_some((typ, code, value))
        })
        .collect()
}

/// One open controller, as the service sees it (a fake one in tests).
pub trait Pad {
    fn name(&self) -> String;
    /// EVIOCGRAB: the controller's events come only here (on) or to
    /// everyone again (off). False: the kernel refused (another grab).
    fn grab(&self, on: bool) -> bool;
    fn norm(&self, code: u16, raw: i32) -> f64;
    /// Stop listening; on_event(path, None) follows.
    fn close(&self);
}

/// One open controller; events come on the GLib main loop.
pub struct Gamepad {
    path: String,
    name: String,
    fd: Cell<Option<RawFd>>,
    ranges: HashMap<u16, (i32, i32)>,
    watch: RefCell<Option<glib::SourceId>>,
    on_event: OnEvent,
}

impl Gamepad {
    pub fn open(path: &str, on_event: OnEvent) -> io::Result<Rc<Gamepad>> {
        let fd = open_fd(path)?;
        let mut ranges = HashMap::new();
        for code in [ABS_X, ABS_Y, ABS_RX, ABS_RY, ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y] {
            let mut info = [0i32; 6]; // struct input_absinfo: value, minimum, maximum, fuzz, flat, resolution
            // SAFETY: EVIOCGABS fills 24 bytes, the size given in the request
            let n = unsafe { libc::ioctl(fd, ioc_read(0x40 + code as u32, 24) as _, info.as_mut_ptr()) };
            if n >= 0 && info[2] > info[1] {
                ranges.insert(code, (info[1], info[2]));
            }
        }
        let pad = Rc::new(Gamepad { path: path.to_string(), name: name_of(fd), fd: Cell::new(Some(fd)), ranges, watch: RefCell::new(None), on_event });
        let weak: Weak<Gamepad> = Rc::downgrade(&pad);
        let id = glib::source::unix_fd_add_local(fd, glib::IOCondition::IN | glib::IOCondition::HUP | glib::IOCondition::ERR, move |_, cond| {
            let Some(pad) = weak.upgrade() else { return glib::ControlFlow::Break };
            pad.readable(cond)
        });
        pad.watch.replace(Some(id));
        Ok(pad)
    }

    pub fn path(&self) -> &str {
        &self.path
    }

    fn readable(&self, cond: glib::IOCondition) -> glib::ControlFlow {
        let gone = || {
            self.watch.take(); // this source ends by returning Break: never removed twice
            self.close();
            glib::ControlFlow::Break
        };
        if cond.intersects(glib::IOCondition::HUP | glib::IOCondition::ERR) {
            return gone();
        }
        let Some(fd) = self.fd.get() else { return glib::ControlFlow::Break };
        let mut buf = [0u8; EVENT_SIZE * 64];
        // SAFETY: reads at most buf.len() bytes into buf
        let n = unsafe { libc::read(fd, buf.as_mut_ptr() as *mut libc::c_void, buf.len()) };
        if n < 0 {
            let err = io::Error::last_os_error();
            if matches!(err.raw_os_error(), Some(libc::EAGAIN) | Some(libc::EINTR)) {
                return glib::ControlFlow::Continue;
            }
            return gone(); // unplugged
        }
        for ev in parse(&buf[..n as usize]) {
            (self.on_event)(&self.path, Some(ev));
        }
        glib::ControlFlow::Continue
    }
}

impl Pad for Gamepad {
    fn name(&self) -> String {
        self.name.clone()
    }

    fn grab(&self, on: bool) -> bool {
        let Some(fd) = self.fd.get() else { return false };
        // SAFETY: EVIOCGRAB takes its int by value
        unsafe { libc::ioctl(fd, EVIOCGRAB as _, if on { 1 as libc::c_int } else { 0 }) >= 0 }
    }

    fn norm(&self, code: u16, raw: i32) -> f64 {
        norm_in(self.ranges.get(&code).copied(), code, raw as f64)
    }

    fn close(&self) {
        if let Some(id) = self.watch.take() {
            id.remove();
        }
        let Some(fd) = self.fd.take() else { return };
        close_fd(fd);
        (self.on_event)(&self.path, None); // gone
    }
}

impl Drop for Gamepad {
    fn drop(&mut self) {
        if let Some(id) = self.watch.take() {
            id.remove();
        }
        if let Some(fd) = self.fd.take() {
            close_fd(fd);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{main_loop_lock, settle};
    use std::io::Write;

    fn event(typ: u16, code: u16, value: i32) -> Vec<u8> {
        let mut b = vec![0u8; 16]; // the time: two longs
        b.extend(typ.to_ne_bytes());
        b.extend(code.to_ne_bytes());
        b.extend(value.to_ne_bytes());
        b
    }

    /// Read from a FIFO standing in for /dev/input/eventN: the ioctls fail
    /// on it, the events still come (never a real device in tests).
    #[test]
    fn test_reads_events_from_the_device() {
        let _lock = main_loop_lock();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("event0");
        let c = std::ffi::CString::new(path.to_str().unwrap()).unwrap();
        // SAFETY: a NUL-terminated path
        assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
        let got: Rc<RefCell<Vec<Option<Event>>>> = Rc::default();
        let g = got.clone();
        let pad = Gamepad::open(path.to_str().unwrap(), Rc::new(move |_p: &str, ev| g.borrow_mut().push(ev))).unwrap();
        let mut w = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        let mut data = event(EV_KEY, BTN_SOUTH, 1);
        data.extend(event(0, 0, 0)); // SYN: ignored
        data.extend(event(EV_ABS, ABS_X, 32767));
        w.write_all(&data).unwrap();
        settle(100);
        assert_eq!(*got.borrow(), [Some((EV_KEY, BTN_SOUTH, 1)), Some((EV_ABS, ABS_X, 32767))]);
        assert!((pad.norm(ABS_X, 32767) - 1.0).abs() < 1e-3);
        drop(w);
        pad.close();
        assert_eq!(got.borrow().last(), Some(&None)); // gone
        pad.close(); // twice: once
        assert_eq!(got.borrow().iter().filter(|e| e.is_none()).count(), 1);
    }

    /// The writer hanging up (a controller unplugged) ends the pad by itself.
    #[test]
    fn test_unplugged_controller_is_gone() {
        let _lock = main_loop_lock();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("event1");
        let c = std::ffi::CString::new(path.to_str().unwrap()).unwrap();
        // SAFETY: a NUL-terminated path
        assert_eq!(unsafe { libc::mkfifo(c.as_ptr(), 0o600) }, 0);
        let got: Rc<RefCell<Vec<Option<Event>>>> = Rc::default();
        let g = got.clone();
        let pad = Gamepad::open(path.to_str().unwrap(), Rc::new(move |_p: &str, ev| g.borrow_mut().push(ev))).unwrap();
        let w = std::fs::OpenOptions::new().write(true).open(&path).unwrap();
        settle(30);
        drop(w);
        settle(100);
        assert_eq!(*got.borrow(), [None]);
        assert!(!pad.grab(true)); // closed: nothing to grab
    }

    /// Axis ranges from the device: 0..255 sticks centre at 0, triggers map
    /// to 0..1 and clamp.
    #[test]
    fn test_norm_with_device_ranges() {
        let r = Some((0, 255));
        assert!(norm_in(r, ABS_X, 127.5).abs() < 1e-9);
        assert_eq!((norm_in(r, ABS_X, 0.0), norm_in(r, ABS_X, 400.0)), (-1.0, 1.0));
        assert_eq!((norm_in(r, ABS_Z, 255.0), norm_in(r, ABS_Z, -9.0)), (1.0, 0.0));
        assert!((norm_in(None, ABS_Y, 32767.0) - 1.0).abs() < 1e-3); // default range
    }

    /// Regular files named event* (or an unreadable folder) are never taken
    /// for controllers.
    #[test]
    fn test_detection_on_non_devices() {
        let d = tempfile::tempdir().unwrap();
        std::fs::write(d.path().join("event0"), "junk").unwrap();
        std::fs::write(d.path().join("mouse0"), "junk").unwrap();
        let dir = d.path().to_str().unwrap();
        assert!(find_gamepads(dir).is_empty());
        assert!(find_guide_devices(&["Pad".into()], dir).is_empty());
        assert!(find_gamepads(&format!("{dir}/missing")).is_empty());
        assert_eq!(gamepad_name(&format!("{dir}/event0")), "");
    }

    #[test]
    fn test_only_keys_and_axes_whole_events() {
        let mut data = event(EV_KEY, BTN_EAST, 0);
        data.extend(event(0x04, 4, 9)); // EV_MSC
        data.extend(&event(EV_ABS, ABS_HAT0X, -1)[..10]); // torn
        assert_eq!(parse(&data), [(EV_KEY, BTN_EAST, 0)]);
    }
}
