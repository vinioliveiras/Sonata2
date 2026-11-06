//! Where windows minimize to: wlr-foreign-toplevel-management's
//! set_rectangle (the part of wl/toplevels.py the Dock still needed).
//!
//! The Dock's window list comes from Wayfire's IPC (toplevels.rs), but the
//! IPC has no way to say "this window minimizes into that icon": only the
//! foreign-toplevel protocol does, and its set_rectangle names one of the
//! Dock's own wl_surfaces -- so it must be sent on GTK's Wayland
//! connection, where that surface lives. A connection of our own (as
//! clipboard's vkeyboard.rs writes) can't name it. So, as the Python Dock
//! does with pywayland: the manager is bound on GTK's wl_display through
//! libwayland-client itself (already loaded by GTK, its symbols looked up
//! at run time), with the two interfaces written out by hand below. Events
//! arrive on the default queue, which GTK dispatches on the main loop: no
//! thread, socket or fd watch of our own.
//!
//! Each handle keeps its app id and the displays it is on (output_enter /
//! output_leave, GTK's own wl_output objects); the Dock aims the windows of
//! each app at its icon on their display (dock.rs, update_rectangles).
//! Nothing else is ever requested: the IPC stays the one that minimizes,
//! activates and closes.

use gtk::prelude::*;
use gtk::{gdk, glib};
use std::cell::RefCell;
use std::collections::HashMap;
use std::ffi::{c_char, c_int, c_void, CStr};
use std::rc::Rc;

#[repr(C)]
struct Message {
    name: *const c_char,
    signature: *const c_char,
    types: *const *const Interface,
}

#[repr(C)]
struct Interface {
    name: *const c_char,
    version: c_int,
    method_count: c_int,
    methods: *const Message,
    event_count: c_int,
    events: *const Message,
}

/// union wl_argument: 8 bytes, the pointer or the 32-bit value.
#[repr(C)]
#[derive(Clone, Copy)]
union Arg {
    i: i32,
    u: u32,
    s: *const c_char,
    o: *mut c_void,
    a: *const Array,
}

#[repr(C)]
struct Array {
    size: usize,
    alloc: usize,
    data: *mut c_void,
}

type Dispatcher = unsafe extern "C" fn(*const c_void, *mut c_void, u32, *const Message, *mut Arg) -> c_int;

type Marshal = unsafe extern "C" fn(*mut c_void, u32, *const Interface, u32, u32, *mut Arg) -> *mut c_void;
type AddDispatcher = unsafe extern "C" fn(*mut c_void, Dispatcher, *const c_void, *mut c_void) -> c_int;
type ProxyU32 = unsafe extern "C" fn(*mut c_void) -> u32;
type ProxyInt = unsafe extern "C" fn(*mut c_void) -> c_int;
type GdkToWl = unsafe extern "C" fn(*mut c_void) -> *mut c_void;

/// libwayland-client and GDK's Wayland calls, found in the process.
struct Lib {
    marshal: Marshal,
    add_dispatcher: AddDispatcher,
    get_version: ProxyU32,
    flush: ProxyInt,
    registry_iface: *const Interface,
    gdk_display: GdkToWl,
    gdk_surface: GdkToWl,
    gdk_output: GdkToWl,
}

fn sym(name: &str) -> Option<*mut c_void> {
    let c = std::ffi::CString::new(name).ok()?;
    // SAFETY: looking a name up among the loaded libraries
    let p = unsafe { libc::dlsym(libc::RTLD_DEFAULT, c.as_ptr()) };
    (!p.is_null()).then_some(p)
}

impl Lib {
    fn load() -> Option<Lib> {
        // GTK has it loaded on Wayland; opened again it is the same library
        // (RTLD_NOLOAD: never a copy of our own)
        let so = c"libwayland-client.so.0";
        // SAFETY: dlopen with NOLOAD only returns a library already there
        unsafe { libc::dlopen(so.as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL | libc::RTLD_NOLOAD) };
        // SAFETY: each symbol has the C signature declared for it (wayland-client.h, gdkwayland.h)
        unsafe {
            Some(Lib {
                marshal: std::mem::transmute::<*mut c_void, Marshal>(sym("wl_proxy_marshal_array_flags")?),
                add_dispatcher: std::mem::transmute::<*mut c_void, AddDispatcher>(sym("wl_proxy_add_dispatcher")?),
                get_version: std::mem::transmute::<*mut c_void, ProxyU32>(sym("wl_proxy_get_version")?),
                flush: std::mem::transmute::<*mut c_void, ProxyInt>(sym("wl_display_flush")?),
                registry_iface: sym("wl_registry_interface")? as *const Interface,
                gdk_display: std::mem::transmute::<*mut c_void, GdkToWl>(sym("gdk_wayland_display_get_wl_display")?),
                gdk_surface: std::mem::transmute::<*mut c_void, GdkToWl>(sym("gdk_wayland_surface_get_wl_surface")?),
                gdk_output: std::mem::transmute::<*mut c_void, GdkToWl>(sym("gdk_wayland_monitor_get_wl_output")?),
            })
        }
    }
}

// -- the two interfaces (wlr-foreign-toplevel-management-unstable-v1.xml) -----------------------

pub const MANAGER_NAME: &str = "zwlr_foreign_toplevel_manager_v1";
const VERSION: u32 = 3;
// zwlr_foreign_toplevel_handle_v1's events, in the XML's order
const EV_TITLE: u32 = 0;
const EV_APP_ID: u32 = 1;
const EV_OUTPUT_ENTER: u32 = 2;
const EV_OUTPUT_LEAVE: u32 = 3;
const EV_DONE: u32 = 5;
const EV_CLOSED: u32 = 6;
// its requests
const REQ_SET_RECTANGLE: u32 = 6;
const REQ_DESTROY: u32 = 7;
const MARSHAL_DESTROY: u32 = 1;

struct Ifaces {
    manager: Interface,
    handle: Interface,
}

const NULL: *const Interface = std::ptr::null();

/// Built once and leaked: libwayland keeps pointing at them.
fn ifaces() -> &'static Ifaces {
    thread_local! {
        static IFACES: &'static Ifaces = build_ifaces();
    }
    IFACES.with(|i| *i)
}

fn build_ifaces() -> &'static Ifaces {
    let leak = |v: Vec<*const Interface>| -> *const *const Interface { Box::leak(v.into_boxed_slice()).as_ptr() };
    let msg = |name: &'static CStr, sig: &'static CStr, types: *const *const Interface| Message { name: name.as_ptr(), signature: sig.as_ptr(), types };
    let me: &'static mut Ifaces = Box::leak(Box::new(Ifaces {
        manager: Interface { name: c"zwlr_foreign_toplevel_manager_v1".as_ptr(), version: VERSION as c_int, method_count: 0, methods: std::ptr::null(), event_count: 0, events: std::ptr::null() },
        handle: Interface { name: c"zwlr_foreign_toplevel_handle_v1".as_ptr(), version: VERSION as c_int, method_count: 0, methods: std::ptr::null(), event_count: 0, events: std::ptr::null() },
    }));
    let handle: *const Interface = &me.handle;
    // (object arguments typed NULL: libwayland then takes any object)
    let none = leak(vec![NULL; 5]);
    let one_handle = leak(vec![handle]);
    let manager_requests = Box::leak(vec![msg(c"stop", c"", none)].into_boxed_slice());
    let manager_events = Box::leak(vec![msg(c"toplevel", c"n", one_handle), msg(c"finished", c"", none)].into_boxed_slice());
    let handle_requests = Box::leak(
        vec![
            msg(c"set_maximized", c"", none),
            msg(c"unset_maximized", c"", none),
            msg(c"set_minimized", c"", none),
            msg(c"unset_minimized", c"", none),
            msg(c"activate", c"o", none),
            msg(c"close", c"", none),
            msg(c"set_rectangle", c"oiiii", none),
            msg(c"destroy", c"", none),
            msg(c"set_fullscreen", c"2?o", none),
            msg(c"unset_fullscreen", c"2", none),
        ]
        .into_boxed_slice(),
    );
    let handle_events = Box::leak(
        vec![
            msg(c"title", c"s", none),
            msg(c"app_id", c"s", none),
            msg(c"output_enter", c"o", none),
            msg(c"output_leave", c"o", none),
            msg(c"state", c"a", none),
            msg(c"done", c"", none),
            msg(c"closed", c"", none),
            msg(c"parent", c"3?o", one_handle),
        ]
        .into_boxed_slice(),
    );
    me.manager.method_count = manager_requests.len() as c_int;
    me.manager.methods = manager_requests.as_ptr();
    me.manager.event_count = manager_events.len() as c_int;
    me.manager.events = manager_events.as_ptr();
    me.handle.method_count = handle_requests.len() as c_int;
    me.handle.methods = handle_requests.as_ptr();
    me.handle.event_count = handle_events.len() as c_int;
    me.handle.events = handle_events.as_ptr();
    me
}

// -- the state ----------------------------------------------------------------------------------

/// A window as foreign-toplevel knows it.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Handle {
    /// the proxy's address: its id while it lives
    pub id: usize,
    pub app_id: String,
    pub title: String,
    /// GTK's wl_output objects it is on (output_of names a display's)
    pub outputs: Vec<usize>,
}

#[derive(Default)]
struct State {
    display: usize,
    handles: HashMap<usize, Handle>,
    listeners: Vec<Rc<dyn Fn()>>,
    tell_src: Option<glib::SourceId>,
}

thread_local! {
    static LIB: Option<Lib> = Lib::load();
    static STATE: RefCell<Option<State>> = const { RefCell::new(None) };
}

fn with_lib<T>(f: impl FnOnce(&Lib) -> T) -> Option<T> {
    LIB.with(|l| l.as_ref().map(f))
}

/// GDK's Wayland backend's types (a toplevel's surface is a subclass).
fn is_a(t: glib::Type, name: &str) -> bool {
    glib::Type::from_name(name).is_some_and(|w| t.is_a(w))
}

/// Bind the manager on GTK's connection (once per process). False: not a
/// Wayland session, or no libwayland-client 1.20+ / foreign-toplevel.
pub fn start() -> bool {
    if STATE.with(|s| s.borrow().is_some()) {
        return true;
    }
    let Some(display) = gdk::Display::default() else { return false };
    if !is_a(display.type_(), "GdkWaylandDisplay") {
        return false;
    }
    let ok = with_lib(|lib| {
        // SAFETY: a GdkWaylandDisplay (checked above) and GDK's own call for it
        let wl = unsafe { (lib.gdk_display)(display.as_ptr() as *mut c_void) };
        if wl.is_null() {
            return false;
        }
        // wl_display.get_registry (opcode 1): a registry of our own on GTK's connection
        let mut args = [Arg { o: std::ptr::null_mut() }];
        // SAFETY: the display proxy, wl_registry's interface, one new_id argument
        let registry = unsafe { (lib.marshal)(wl, 1, lib.registry_iface, (lib.get_version)(wl), 0, args.as_mut_ptr()) };
        if registry.is_null() {
            return false;
        }
        // SAFETY: our dispatcher for the proxy we just made
        unsafe { (lib.add_dispatcher)(registry, on_registry, std::ptr::null(), std::ptr::null_mut()) };
        STATE.with(|s| s.replace(Some(State { display: wl as usize, ..State::default() })));
        // SAFETY: GTK's display
        unsafe { (lib.flush)(wl) };
        true
    });
    ok.unwrap_or(false)
}

pub fn active() -> bool {
    STATE.with(|s| s.borrow().is_some())
}

/// The windows now.
pub fn handles() -> Vec<Handle> {
    STATE.with(|s| s.borrow().as_ref().map(|s| s.handles.values().cloned().collect()).unwrap_or_default())
}

/// Told (once per burst) when windows come, go, or change app or display.
pub fn add_listener(f: Rc<dyn Fn()>) {
    STATE.with(|s| {
        if let Some(s) = s.borrow_mut().as_mut() {
            s.listeners.push(f);
        }
    });
}

/// The wl_output GTK binds for `monitor` (what output_enter names).
pub fn output_of(monitor: &gdk::Monitor) -> Option<usize> {
    if !is_a(monitor.type_(), "GdkWaylandMonitor") {
        return None;
    }
    // SAFETY: a GdkWaylandMonitor (checked above)
    let p = with_lib(|lib| unsafe { (lib.gdk_output)(monitor.as_ptr() as *mut c_void) })?;
    (!p.is_null()).then_some(p as usize)
}

/// Where window `id` minimizes to: (x, y, w, h) in `surface`'s coordinates
/// (0, 0, 0, 0: nowhere, the compositor's plain animation).
pub fn set_rectangle(id: usize, surface: &gdk::Surface, rect: Rect) {
    if !is_a(surface.type_(), "GdkWaylandSurface") {
        return;
    }
    let (live, display) = STATE.with(|s| s.borrow().as_ref().map(|s| (s.handles.contains_key(&id), s.display)).unwrap_or((false, 0)));
    if !live {
        return;
    }
    with_lib(|lib| {
        // SAFETY: a GdkWaylandSurface (checked above)
        let wl_surface = unsafe { (lib.gdk_surface)(surface.as_ptr() as *mut c_void) };
        if wl_surface.is_null() {
            return;
        }
        let (x, y, w, h) = rect;
        let mut args = [Arg { o: wl_surface }, Arg { i: x }, Arg { i: y }, Arg { i: w }, Arg { i: h }];
        // SAFETY: a live handle proxy (still in our list), set_rectangle's arguments
        unsafe {
            let handle = id as *mut c_void;
            (lib.marshal)(handle, REQ_SET_RECTANGLE, std::ptr::null(), (lib.get_version)(handle), 0, args.as_mut_ptr());
            (lib.flush)(display as *mut c_void);
        }
    });
}

/// A burst of events tells the listeners once, on the main loop (never
/// from inside libwayland's dispatch).
fn changed() {
    STATE.with(|s| {
        let mut b = s.borrow_mut();
        let Some(st) = b.as_mut() else { return };
        if st.tell_src.is_some() {
            return;
        }
        st.tell_src = Some(glib::idle_add_local_once(|| {
            let ls = STATE.with(|s| {
                let mut b = s.borrow_mut();
                let st = b.as_mut()?;
                st.tell_src = None;
                Some(st.listeners.clone())
            });
            for f in ls.into_iter().flatten() {
                f();
            }
        }));
    });
}

unsafe fn text(a: Arg) -> String {
    // SAFETY: (caller) a string argument, NULL or NUL-terminated
    unsafe { if a.s.is_null() { String::new() } else { CStr::from_ptr(a.s).to_string_lossy().into_owned() } }
}

/// wl_registry: global(name, interface, version) / global_remove(name).
unsafe extern "C" fn on_registry(_impl: *const c_void, registry: *mut c_void, opcode: u32, _msg: *const Message, args: *mut Arg) -> c_int {
    if opcode != 0 {
        return 0;
    }
    // SAFETY: libwayland passes global's three arguments
    let (name, iface, version) = unsafe { ((*args).u, text(*args.add(1)), (*args.add(2)).u) };
    if iface != MANAGER_NAME {
        return 0;
    }
    with_lib(|lib| {
        let ver = version.min(VERSION);
        let ifs = ifaces();
        // wl_registry.bind(name, interface name, version, new_id)
        let mut a = [Arg { u: name }, Arg { s: ifs.manager.name }, Arg { u: ver }, Arg { o: std::ptr::null_mut() }];
        // SAFETY: our registry proxy, bind's arguments, our manager interface
        let manager = unsafe { (lib.marshal)(registry, 0, &ifs.manager, ver, 0, a.as_mut_ptr()) };
        if !manager.is_null() {
            // SAFETY: our dispatcher for the proxy we just made
            unsafe { (lib.add_dispatcher)(manager, on_manager, std::ptr::null(), std::ptr::null_mut()) };
        }
    });
    0
}

/// The manager: toplevel(new handle) / finished.
unsafe extern "C" fn on_manager(_impl: *const c_void, _manager: *mut c_void, opcode: u32, _msg: *const Message, args: *mut Arg) -> c_int {
    if opcode != 0 {
        return 0;
    }
    // SAFETY: libwayland made the new handle's proxy (the "n" argument)
    let handle = unsafe { (*args).o };
    if handle.is_null() {
        return 0;
    }
    with_lib(|lib| {
        // SAFETY: our dispatcher for the new proxy, before any of its events
        unsafe { (lib.add_dispatcher)(handle, on_handle, std::ptr::null(), std::ptr::null_mut()) };
    });
    STATE.with(|s| {
        if let Some(st) = s.borrow_mut().as_mut() {
            st.handles.insert(handle as usize, Handle { id: handle as usize, ..Handle::default() });
        }
    });
    0
}

/// A window's events: what the Dock needs of them.
unsafe extern "C" fn on_handle(_impl: *const c_void, handle: *mut c_void, opcode: u32, _msg: *const Message, args: *mut Arg) -> c_int {
    let id = handle as usize;
    if opcode == EV_CLOSED {
        STATE.with(|s| {
            if let Some(st) = s.borrow_mut().as_mut() {
                st.handles.remove(&id);
            }
        });
        with_lib(|lib| {
            // SAFETY: the handle's destructor request; the proxy goes with it
            unsafe { (lib.marshal)(handle, REQ_DESTROY, std::ptr::null(), (lib.get_version)(handle), MARSHAL_DESTROY, std::ptr::null_mut()) };
        });
        changed();
        return 0;
    }
    // SAFETY: each event's first argument as its signature says
    let first = |kind: u32| -> Option<Arg> { (matches!(kind, EV_TITLE | EV_APP_ID | EV_OUTPUT_ENTER | EV_OUTPUT_LEAVE)).then(|| unsafe { *args }) };
    let tell = STATE.with(|s| {
        let mut b = s.borrow_mut();
        let h = b.as_mut()?.handles.get_mut(&id)?;
        match opcode {
            // SAFETY: a string argument
            EV_TITLE => h.title = unsafe { text(first(opcode)?) },
            EV_APP_ID => {
                // SAFETY: a string argument
                let a = unsafe { text(first(opcode)?) };
                if a == h.app_id {
                    return None;
                }
                h.app_id = a;
                return Some(());
            }
            EV_OUTPUT_ENTER | EV_OUTPUT_LEAVE => {
                // SAFETY: an object argument
                let o = unsafe { first(opcode)?.o } as usize;
                h.outputs.retain(|x| *x != o);
                if opcode == EV_OUTPUT_ENTER && o != 0 {
                    h.outputs.push(o);
                }
                return Some(());
            }
            EV_DONE => return None,
            _ => {}
        }
        None
    });
    if tell.is_some() {
        changed();
    }
    0
}

/// (x, y, w, h) in the Dock's surface.
pub type Rect = (i32, i32, i32, i32);

/// Which handles go where: each window of an app with an icon here, aimed
/// at that icon when it is on this Dock's display. `several`: other Docks
/// (other displays) aim their own windows; a single Dock gives a window on
/// another display no target (Wayfire can't animate across displays).
/// Returns (handle id, rect); rect (0, 0, 0, 0) is "no target".
pub fn plan(handles: &[Handle], mine: Option<usize>, several: bool, icon: &dyn Fn(&str) -> Option<Rect>) -> Vec<(usize, Rect)> {
    let mut out = Vec::new();
    for h in handles {
        let Some(rect) = icon(&h.app_id) else { continue };
        // None: Wayfire hasn't told which display yet
        let here = match (mine, h.outputs.is_empty()) {
            (Some(o), false) => Some(h.outputs.contains(&o)),
            _ => None,
        };
        match (here, several) {
            (Some(true), _) | (None, false) => out.push((h.id, rect)),
            (Some(false), false) => out.push((h.id, (0, 0, 0, 0))),
            // (several Docks: the one on the window's display aims it --
            //  never from here, or the last Dock to write would win)
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn h(id: usize, app: &str, outputs: &[usize]) -> Handle {
        Handle { id, app_id: app.into(), title: String::new(), outputs: outputs.to_vec() }
    }

    /// Windows on the external screen flew into the laptop's Dock (Vini):
    /// with a Dock per display, each aims only the windows on its display.
    #[test]
    fn test_windows_fly_to_the_icon_on_their_display() {
        let hs = [h(1, "kitty", &[10]), h(2, "kitty", &[20]), h(3, "firefox", &[10]), h(4, "unknown-app", &[10])];
        let icon = |a: &str| (a != "unknown-app").then_some((5, 6, 48, 48));
        assert_eq!(plan(&hs, Some(10), true, &icon), vec![(1, (5, 6, 48, 48)), (3, (5, 6, 48, 48))]);
        assert_eq!(plan(&hs, Some(20), true, &icon), vec![(2, (5, 6, 48, 48))]);
    }

    /// A single Dock: a window on another display gets no target (Wayfire
    /// can't animate across displays), one not placed yet aims at the icon.
    #[test]
    fn a_single_dock_aims_every_window_it_can() {
        let hs = [h(1, "kitty", &[10]), h(2, "kitty", &[20]), h(3, "kitty", &[])];
        let icon = |_: &str| Some((1, 2, 3, 4));
        assert_eq!(plan(&hs, Some(10), false, &icon), vec![(1, (1, 2, 3, 4)), (2, (0, 0, 0, 0)), (3, (1, 2, 3, 4))]);
        // several Docks: a window not placed yet is left to the others too
        assert_eq!(plan(&hs[2..], Some(10), true, &icon), vec![]);
    }

    /// The messages libwayland reads match the XML: names, signatures,
    /// the request and event numbers used here.
    #[test]
    fn interfaces_follow_the_protocol() {
        let ifs = ifaces();
        // SAFETY: built by build_ifaces, static
        unsafe {
            let req = |i: u32| CStr::from_ptr((*ifs.handle.methods.add(i as usize)).name).to_str().unwrap();
            let ev = |i: u32| CStr::from_ptr((*ifs.handle.events.add(i as usize)).name).to_str().unwrap();
            assert_eq!(req(REQ_SET_RECTANGLE), "set_rectangle");
            assert_eq!(CStr::from_ptr((*ifs.handle.methods.add(REQ_SET_RECTANGLE as usize)).signature).to_str().unwrap(), "oiiii");
            assert_eq!(req(REQ_DESTROY), "destroy");
            assert_eq!((ev(EV_TITLE), ev(EV_APP_ID), ev(EV_OUTPUT_ENTER), ev(EV_OUTPUT_LEAVE), ev(EV_DONE), ev(EV_CLOSED)), ("title", "app_id", "output_enter", "output_leave", "done", "closed"));
            assert_eq!(ifs.handle.method_count, 10);
            assert_eq!(ifs.handle.event_count, 8);
            assert_eq!(CStr::from_ptr(ifs.manager.name).to_str().unwrap(), MANAGER_NAME);
            assert_eq!(*(*ifs.manager.events).types, &ifs.handle as *const Interface);
        }
        // the protocol's own XML (the one the Python Sonata read)
        let xml = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/protocols/wlr-foreign-toplevel-management-unstable-v1.xml")).unwrap();
        let handle_part = &xml[xml.find("zwlr_foreign_toplevel_handle_v1\" version").unwrap()..];
        let names: Vec<&str> = handle_part.match_indices("<request name=\"").map(|(i, _)| handle_part[i + 15..].split('"').next().unwrap()).collect();
        assert_eq!(names.iter().position(|n| *n == "set_rectangle"), Some(REQ_SET_RECTANGLE as usize));
        assert_eq!(names.iter().position(|n| *n == "destroy"), Some(REQ_DESTROY as usize));
        let events: Vec<&str> = handle_part.match_indices("<event name=\"").map(|(i, _)| handle_part[i + 13..].split('"').next().unwrap()).collect();
        assert_eq!(events, ["title", "app_id", "output_enter", "output_leave", "state", "done", "closed", "parent"]);
    }
}
