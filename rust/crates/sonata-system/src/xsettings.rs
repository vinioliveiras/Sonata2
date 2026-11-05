//! XSETTINGS for X11 apps (xsettings.py): Sonata's look for everything that
//! runs through XWayland (Vini: Electron apps' menu bars light in Dark
//! Mode).
//!
//! A GTK app on X11 -- Electron and Chromium in X11 mode, GTK 2/3 apps,
//! Wine, Java... -- doesn't read GSettings or the settings portal: it asks
//! the X server's XSETTINGS manager for its theme, icons, cursor and fonts
//! (on GNOME, gnome-settings-daemon is that manager). Sonata's session had
//! none, so those apps fell back to GTK's defaults: Adwaita (light), its
//! icons and fonts, whatever Dark Mode said. This is Sonata's manager: it
//! owns the display's _XSETTINGS_S0 selection and publishes Sonata's
//! settings (system.json), again whenever they change (Dark Mode switches
//! them live).
//!
//! Started by Wayfire each time XWayland starts (core/xwayland_startup_script,
//! tools/wayfire-config.sh); it ends with that X server.
//!
//! ```ignore
//! xsettings::run()                                   // DISPLAY: the XWayland display
//! encode(&[("Net/ThemeName", V::S("Sonata-Dark".into()))], serial)   // the property's bytes
//! ```
//!
//! libX11 is opened at run time (dlopen), as the Python one did through
//! ctypes: nothing to install or link, and a session without XWayland never
//! loads it.

use sonata_config::prefs::{self, I, WM};
use std::ffi::{c_char, c_int, c_long, c_uint, c_ulong, c_void, CString};

/// An XSETTINGS value: INT32 or a string.
#[derive(Clone, Debug, PartialEq)]
pub enum V {
    I(i32),
    S(String),
}

/// {XSETTINGS name: value} from Sonata's settings, sorted by name. `get`:
/// (schema, key) -> the stored value or Sonata's default ("" if neither).
pub fn values_from(get: impl Fn(&str, &str) -> String) -> Vec<(String, V)> {
    let or = |v: String, d: &str| if v.is_empty() { d.to_string() } else { v };
    let dark = get(I, "color-scheme") == "prefer-dark";
    let theme = or(get(I, "gtk-theme"), if dark { "Sonata-Dark" } else { "Sonata-Light" });
    let scale = or(get(I, "text-scaling-factor"), "1.0").parse::<f64>().unwrap_or(1.0);
    let cursor = or(get(I, "cursor-size"), "24").parse::<i32>().unwrap_or(24);
    let aa = or(get(I, "font-antialiasing"), "grayscale");
    let hinting = or(get(I, "font-hinting"), "slight");
    let hint_style = match hinting.as_str() {
        "none" => "hintnone",
        "medium" => "hintmedium",
        "full" => "hintfull",
        _ => "hintslight",
    };
    let s = |v: String| V::S(v);
    let mut out = vec![
        ("Net/ThemeName", s(theme)),
        ("Net/IconThemeName", s(or(get(I, "icon-theme"), "Sonata"))),
        ("Gtk/CursorThemeName", s(or(get(I, "cursor-theme"), "Sonata-Cursors"))),
        ("Gtk/CursorThemeSize", V::I(cursor)),
        ("Gtk/FontName", s(or(get(I, "font-name"), "Inter 10"))),
        ("Gtk/MonospaceFontName", s(or(get(I, "monospace-font-name"), "Monospace 10"))),
        ("Gtk/DecorationLayout", s(or(get(WM, "button-layout"), "close,minimize,maximize:"))),
        ("Gtk/EnableAnimations", V::I((get(I, "enable-animations") != "false") as i32)),
        ("Gtk/ApplicationPreferDarkTheme", V::I(dark as i32)),
        ("Gtk/PrimaryButtonWarpsSlider", V::I(1)),
        ("Net/EnableEventSounds", V::I(0)),
        ("Net/CursorBlink", V::I((get(I, "cursor-blink") != "false") as i32)),
        ("Xft/Antialias", V::I((aa != "none") as i32)),
        ("Xft/RGBA", s(if aa == "rgba" { "rgb" } else { "none" }.into())),
        ("Xft/Hinting", V::I((hinting != "none") as i32)),
        ("Xft/HintStyle", s(hint_style.into())),
        // (Python's round(): halves to even)
        ("Xft/DPI", V::I((96.0 * 1024.0 * scale).round_ties_even() as i32)),
    ]
    .into_iter()
    .map(|(k, v)| (k.to_string(), v))
    .collect::<Vec<_>>();
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

/// values_from Sonata's settings as they are now.
pub fn values() -> Vec<(String, V)> {
    let data = prefs::load();
    values_from(|schema, key| prefs::get(schema, key, "", &data))
}

pub fn pad(n: usize) -> usize {
    (4 - n % 4) % 4
}

/// The _XSETTINGS_SETTINGS property (the XSETTINGS spec, little endian):
/// byte order, serial, count, then each setting -- type, name, the serial
/// it last changed in, value (int: INT32; string: length + bytes). Settings
/// in name order.
pub fn encode(settings: &[(String, V)], serial: u32) -> Vec<u8> {
    let mut sorted: Vec<&(String, V)> = settings.iter().collect();
    sorted.sort_by(|a, b| a.0.cmp(&b.0));
    let mut out = vec![0u8, 0, 0, 0];
    out.extend(serial.to_le_bytes());
    out.extend((sorted.len() as u32).to_le_bytes());
    for (name, value) in sorted {
        let raw = name.as_bytes();
        out.push(if matches!(value, V::I(_)) { 0 } else { 1 });
        out.push(0);
        out.extend((raw.len() as u16).to_le_bytes());
        out.extend(raw);
        out.extend(std::iter::repeat_n(0u8, pad(raw.len())));
        out.extend(serial.to_le_bytes());
        match value {
            V::I(i) => out.extend(i.to_le_bytes()),
            V::S(s) => {
                let data = s.as_bytes();
                out.extend((data.len() as u32).to_le_bytes());
                out.extend(data);
                out.extend(std::iter::repeat_n(0u8, pad(data.len())));
            }
        }
    }
    out
}

// -- the manager (libX11 opened at run time) -------------------------------------------------
#[repr(C)]
#[derive(Clone, Copy)]
struct ClientMessage {
    type_: c_int,
    serial: c_ulong,
    send_event: c_int,
    display: *mut c_void,
    window: c_ulong,
    message_type: c_ulong,
    format: c_int,
    data: [c_long; 5],
}

#[repr(C)]
union XEvent {
    type_: c_int,
    xclient: ClientMessage,
    pad: [c_long; 24],
}

const CLIENT_MESSAGE: c_int = 33;
const STRUCTURE_NOTIFY_MASK: c_long = 1 << 17;
const PROP_MODE_REPLACE: c_int = 0;
const CURRENT_TIME: c_ulong = 0;

type Dpy = *mut c_void;

struct X11 {
    open_display: unsafe extern "C" fn(*const c_char) -> Dpy,
    default_root_window: unsafe extern "C" fn(Dpy) -> c_ulong,
    default_screen: unsafe extern "C" fn(Dpy) -> c_int,
    intern_atom: unsafe extern "C" fn(Dpy, *const c_char, c_int) -> c_ulong,
    create_simple_window: unsafe extern "C" fn(Dpy, c_ulong, c_int, c_int, c_uint, c_uint, c_uint, c_ulong, c_ulong) -> c_ulong,
    set_selection_owner: unsafe extern "C" fn(Dpy, c_ulong, c_ulong, c_ulong) -> c_int,
    get_selection_owner: unsafe extern "C" fn(Dpy, c_ulong) -> c_ulong,
    change_property: unsafe extern "C" fn(Dpy, c_ulong, c_ulong, c_ulong, c_int, c_int, *const u8, c_int) -> c_int,
    send_event: unsafe extern "C" fn(Dpy, c_ulong, c_int, c_long, *mut XEvent) -> c_int,
    flush: unsafe extern "C" fn(Dpy) -> c_int,
    connection_number: unsafe extern "C" fn(Dpy) -> c_int,
    pending: unsafe extern "C" fn(Dpy) -> c_int,
    next_event: unsafe extern "C" fn(Dpy, *mut XEvent) -> c_int,
}

impl X11 {
    // each symbol takes its field's type: the struct above is the annotation
    #[allow(clippy::missing_transmute_annotations)]
    fn load() -> Option<X11> {
        unsafe {
            let lib = libc::dlopen(c"libX11.so.6".as_ptr(), libc::RTLD_NOW | libc::RTLD_LOCAL);
            if lib.is_null() {
                return None;
            }
            macro_rules! sym {
                ($name:literal) => {{
                    let p = libc::dlsym(lib, concat!($name, "\0").as_ptr() as *const c_char);
                    if p.is_null() {
                        return None;
                    }
                    std::mem::transmute::<*mut c_void, _>(p)
                }};
            }
            Some(X11 {
                open_display: sym!("XOpenDisplay"),
                default_root_window: sym!("XDefaultRootWindow"),
                default_screen: sym!("XDefaultScreen"),
                intern_atom: sym!("XInternAtom"),
                create_simple_window: sym!("XCreateSimpleWindow"),
                set_selection_owner: sym!("XSetSelectionOwner"),
                get_selection_owner: sym!("XGetSelectionOwner"),
                change_property: sym!("XChangeProperty"),
                send_event: sym!("XSendEvent"),
                flush: sym!("XFlush"),
                connection_number: sym!("XConnectionNumber"),
                pending: sym!("XPending"),
                next_event: sym!("XNextEvent"),
            })
        }
    }
}

pub struct Manager {
    x: X11,
    dpy: Dpy,
    root: c_ulong,
    selection: c_ulong,
    prop: c_ulong,
    manager: c_ulong,
    win: c_ulong,
    serial: u32,
    published: Option<Vec<(String, V)>>,
}

impl Manager {
    /// The display `display` names (None: $DISPLAY).
    pub fn new(display: Option<&str>) -> Result<Manager, String> {
        let x = X11::load().ok_or("can't load libX11")?;
        let name = display.map(|d| CString::new(d).unwrap_or_default());
        unsafe {
            let dpy = (x.open_display)(name.as_ref().map_or(std::ptr::null(), |n| n.as_ptr()));
            if dpy.is_null() {
                return Err("can't open the X display".into());
            }
            let root = (x.default_root_window)(dpy);
            let screen = (x.default_screen)(dpy);
            let atom = |n: &str| (x.intern_atom)(dpy, CString::new(n).unwrap().as_ptr(), 0);
            let selection = atom(&format!("_XSETTINGS_S{screen}"));
            let prop = atom("_XSETTINGS_SETTINGS");
            let manager = atom("MANAGER");
            let win = (x.create_simple_window)(dpy, root, -1, -1, 1, 1, 0, 0, 0);
            Ok(Manager { x, dpy, root, selection, prop, manager, win, serial: 0, published: None })
        }
    }

    /// Become the display's XSETTINGS manager and tell its clients.
    pub fn claim(&self) -> bool {
        unsafe {
            (self.x.set_selection_owner)(self.dpy, self.selection, self.win, CURRENT_TIME);
            if (self.x.get_selection_owner)(self.dpy, self.selection) != self.win {
                return false;
            }
            let mut ev = XEvent { pad: [0; 24] };
            ev.xclient = ClientMessage {
                type_: CLIENT_MESSAGE,
                serial: 0,
                send_event: 0,
                display: std::ptr::null_mut(),
                window: self.root,
                message_type: self.manager,
                format: 32,
                data: [CURRENT_TIME as c_long, self.selection as c_long, self.win as c_long, 0, 0],
            };
            (self.x.send_event)(self.dpy, self.root, 0, STRUCTURE_NOTIFY_MASK, &mut ev);
            (self.x.flush)(self.dpy);
        }
        true
    }

    /// The settings on the manager's window (clients watch it): only when
    /// they changed, with a new serial.
    pub fn publish(&mut self, settings: Vec<(String, V)>) {
        if self.published.as_ref() == Some(&settings) {
            return;
        }
        self.serial += 1;
        let data = encode(&settings, self.serial);
        unsafe {
            (self.x.change_property)(self.dpy, self.win, self.prop, self.prop, 8, PROP_MODE_REPLACE, data.as_ptr(), data.len() as c_int);
            (self.x.flush)(self.dpy);
        }
        self.published = Some(settings);
    }

    pub fn fileno(&self) -> i32 {
        unsafe { (self.x.connection_number)(self.dpy) }
    }

    /// Read what the server sends (nothing to act on), so it never piles up.
    pub fn drain(&self) {
        let mut ev = XEvent { pad: [0; 24] };
        unsafe {
            while (self.x.pending)(self.dpy) > 0 {
                (self.x.next_event)(self.dpy, &mut ev);
            }
        }
    }
}

/// `sonata2-rs xsettings`: the manager until its X server goes. Exit code.
pub fn run() -> i32 {
    use std::cell::RefCell;
    use std::rc::Rc;
    let mut m = match Manager::new(None) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("sonata2 xsettings: {e}");
            return 1;
        }
    };
    m.publish(values());
    if !m.claim() {
        eprintln!("sonata2 xsettings: another XSETTINGS manager runs on this display");
        return 0;
    }
    let fd = m.fileno();
    let m = Rc::new(RefCell::new(m));
    let main_loop = glib::MainLoop::new(None, false);
    let mm = m.clone();
    let _mon = sonata_config::watch("system", move || mm.borrow_mut().publish(values())); // Dark Mode, theme, fonts: live
    let (ml, mm) = (main_loop.clone(), m.clone());
    glib::unix_fd_add_local(fd, glib::IOCondition::IN | glib::IOCondition::HUP | glib::IOCondition::ERR, move |_fd, cond| {
        if cond.intersects(glib::IOCondition::HUP | glib::IOCondition::ERR) {
            ml.quit(); // XWayland is gone: so is its manager
            return glib::ControlFlow::Break;
        }
        mm.borrow().drain();
        glib::ControlFlow::Continue
    });
    main_loop.run();
    0
}

#[cfg(test)]
mod tests {
    //! tests/test_xsettings.py (Vini: Electron apps' menu bars were light in
    //! Dark Mode -- no XSETTINGS manager in Sonata's session).
    use super::*;

    /// The property back into [(name, value)] (the XSETTINGS spec).
    fn decode(data: &[u8]) -> Vec<(String, V)> {
        let u32_at = |o: usize| u32::from_le_bytes(data[o..o + 4].try_into().unwrap());
        let n = u32_at(8) as usize;
        let (mut off, mut out) = (12, Vec::new());
        for _ in 0..n {
            let kind = data[off];
            let ln = u16::from_le_bytes([data[off + 2], data[off + 3]]) as usize;
            off += 4;
            let name = String::from_utf8(data[off..off + ln].to_vec()).unwrap();
            off += ln + pad(ln) + 4; // (the setting's last-change serial)
            if kind == 0 {
                out.push((name, V::I(i32::from_le_bytes(data[off..off + 4].try_into().unwrap()))));
                off += 4;
            } else {
                let vl = u32_at(off) as usize;
                off += 4;
                out.push((name, V::S(String::from_utf8(data[off..off + vl].to_vec()).unwrap())));
                off += vl + pad(vl);
            }
        }
        out
    }

    #[test]
    fn test_round_trip() {
        let s: Vec<(String, V)> = vec![
            ("Gtk/CursorThemeSize".into(), V::I(24)),
            ("Net/ThemeName".into(), V::S("Sonata-Dark".into())),
            ("Net/X".into(), V::S("abc".into())),
            ("Xft/DPI".into(), V::I(98304)),
        ];
        let data = encode(&s, 7);
        assert_eq!(data[0], 0); // little endian
        assert_eq!(u32::from_le_bytes(data[4..8].try_into().unwrap()), 7);
        assert_eq!(decode(&data), s);
        assert_eq!(data.len() % 4, 0);
    }

    fn prefs(extra: &[(&str, &str)]) -> impl Fn(&str, &str) -> String {
        let mut base: std::collections::HashMap<String, String> = [
            ("color-scheme", "prefer-dark"),
            ("gtk-theme", "Sonata-Dark"),
            ("icon-theme", "Sonata"),
            ("cursor-theme", "Sonata-Cursors"),
            ("cursor-size", "32"),
            ("font-name", "Inter 10"),
            ("text-scaling-factor", "1.25"),
            ("font-antialiasing", "rgba"),
            ("font-hinting", "full"),
            ("button-layout", "close,minimize,maximize:"),
        ]
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        for (k, v) in extra {
            base.insert(k.to_string(), v.to_string());
        }
        move |_schema, key| base.get(key).cloned().unwrap_or_default()
    }

    fn get<'a>(v: &'a [(String, V)], k: &str) -> &'a V {
        &v.iter().find(|(n, _)| n == k).unwrap().1
    }

    #[test]
    fn test_from_sonatas_settings() {
        let v = values_from(prefs(&[]));
        assert_eq!(get(&v, "Net/ThemeName"), &V::S("Sonata-Dark".into()));
        assert_eq!(get(&v, "Gtk/ApplicationPreferDarkTheme"), &V::I(1));
        assert_eq!(get(&v, "Net/IconThemeName"), &V::S("Sonata".into()));
        assert_eq!(get(&v, "Gtk/CursorThemeSize"), &V::I(32));
        assert_eq!(get(&v, "Xft/DPI"), &V::I((96.0 * 1024.0 * 1.25) as i32));
        assert_eq!((get(&v, "Xft/RGBA"), get(&v, "Xft/HintStyle")), (&V::S("rgb".into()), &V::S("hintfull".into())));
        assert_eq!(get(&v, "Gtk/DecorationLayout"), &V::S("close,minimize,maximize:".into()));
        let light = values_from(prefs(&[("color-scheme", "default"), ("gtk-theme", "Sonata-Light")]));
        assert_eq!((get(&light, "Net/ThemeName"), get(&light, "Gtk/ApplicationPreferDarkTheme")), (&V::S("Sonata-Light".into()), &V::I(0)));
    }

    #[test]
    fn defaults_when_nothing_is_set() {
        let v = values_from(|_, _| String::new());
        assert_eq!(get(&v, "Net/ThemeName"), &V::S("Sonata-Light".into()));
        assert_eq!(get(&v, "Xft/DPI"), &V::I(98304));
        assert_eq!(get(&v, "Gtk/EnableAnimations"), &V::I(1));
        assert_eq!(get(&v, "Xft/HintStyle"), &V::S("hintslight".into()));
    }

    /// Every symbol the manager uses is found (a typo would only show at login).
    #[test]
    fn libx11_symbols_resolve() {
        if std::path::Path::new("/usr/lib/libX11.so.6").exists() || std::path::Path::new("/usr/lib/x86_64-linux-gnu/libX11.so.6").exists() {
            assert!(X11::load().is_some());
        }
    }

    /// On an X display of its own (xvfb-run -a cargo test -- --ignored):
    /// the manager owns the selection and publishes only what changed.
    #[test]
    #[ignore]
    fn live_claims_the_selection() {
        if std::env::var("DISPLAY").is_err() {
            return;
        }
        let mut m = Manager::new(None).unwrap();
        m.publish(values_from(|_, _| String::new()));
        assert!(m.claim());
        assert_eq!(m.serial, 1);
        m.publish(values_from(|_, _| String::new())); // the same: no new serial
        assert_eq!(m.serial, 1);
        m.drain();
    }
}
