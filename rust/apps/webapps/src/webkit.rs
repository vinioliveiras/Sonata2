//! The few WebKitGTK 6 calls the web app window makes, the library opened
//! at run time. There is no webkit6 crate in the workspace, and linking
//! WebKit into sonata2-rs would make every part of it -- the lock screen
//! too -- need WebKit to start (docs/RUST_PORT.md, risks: libpam is opened
//! the same way). Objects are plain GObjects: properties and signals go
//! through glib's own dynamic calls, only the methods below are looked up.

use glib::prelude::*;
use glib::translate::*;
use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::sync::OnceLock;

type Obj = *mut glib::gobject_ffi::GObject;
type Ptr = *mut c_void;

const LIBS: [&CStr; 2] = [c"libwebkitgtk-6.0.so.4", c"libwebkitgtk-6.0.so"];

fn lib() -> Option<usize> {
    static LIB: OnceLock<usize> = OnceLock::new();
    let l = *LIB.get_or_init(|| {
        LIBS.iter()
            // SAFETY: dlopen with a valid C string; the library stays loaded
            .map(|n| unsafe { libc::dlopen(n.as_ptr(), libc::RTLD_NOW | libc::RTLD_GLOBAL) } as usize)
            .find(|h| *h != 0)
            .unwrap_or(0)
    });
    (l != 0).then_some(l)
}

/// The address of `name` in WebKit.
fn sym(name: &CStr) -> Option<Ptr> {
    // SAFETY: dlsym on a loaded library
    let f = unsafe { libc::dlsym(lib()? as Ptr, name.as_ptr()) };
    (!f.is_null()).then_some(f)
}

/// Call WebKit's `name` as an `extern "C" fn` of type `$t` (None: missing).
macro_rules! call {
    ($name:literal as $t:ty, $($arg:expr),*) => {{
        match sym($name) {
            // SAFETY: $t is the function's signature in WebKitGTK 6's headers
            Some(f) => Some(unsafe { std::mem::transmute::<Ptr, $t>(f)($($arg),*) }),
            None => None,
        }
    }};
}

/// WebKitGTK 6 is there to open.
pub fn available() -> bool {
    sym(c"webkit_web_view_get_type").is_some()
}

fn gtype(getter: &CStr) -> Option<glib::Type> {
    let f = sym(getter)?;
    // SAFETY: a *_get_type function
    let t = unsafe { std::mem::transmute::<Ptr, extern "C" fn() -> glib::ffi::GType>(f)() };
    // SAFETY: a registered type
    Some(unsafe { from_glib(t) })
}

fn obj(p: Ptr) -> glib::Object {
    // SAFETY: a GObject owned elsewhere (transfer none)
    unsafe { from_glib_none(p as Obj) }
}

fn ptr(o: &impl IsA<glib::Object>) -> Obj {
    o.as_ref().as_ptr()
}

fn cstr(s: &str) -> CString {
    CString::new(s.replace('\0', "")).unwrap_or_default()
}

/// WebKitNetworkSession: its own data and cache folders (each web app its own login).
pub fn network_session(data: &str, cache: &str, cookies: &str) -> Option<glib::Object> {
    let (d, c) = (cstr(data), cstr(cache));
    let s = call!(c"webkit_network_session_new" as extern "C" fn(*const c_char, *const c_char) -> Obj, d.as_ptr(), c.as_ptr())?;
    if s.is_null() {
        return None;
    }
    // SAFETY: the new session (transfer full)
    let session: glib::Object = unsafe { from_glib_full(s) };
    if let Some(cm) = call!(c"webkit_network_session_get_cookie_manager" as extern "C" fn(Obj) -> Ptr, s) {
        let path = cstr(cookies);
        // WEBKIT_COOKIE_PERSISTENT_STORAGE_SQLITE
        call!(c"webkit_cookie_manager_set_persistent_storage" as extern "C" fn(Ptr, *const c_char, c_int), cm, path.as_ptr(), 1);
    }
    if let Some(m) = call!(c"webkit_network_session_get_website_data_manager" as extern "C" fn(Obj) -> Ptr, s) {
        call!(c"webkit_website_data_manager_set_favicons_enabled" as extern "C" fn(Ptr, glib::ffi::gboolean), m, 1);
    }
    Some(session)
}

/// A WebKitUserContentManager with `scripts` on every frame of every page, at the start.
pub fn content_manager(scripts: &[String]) -> Option<glib::Object> {
    let m = call!(c"webkit_user_content_manager_new" as extern "C" fn() -> Obj,)?;
    // SAFETY: the new manager (transfer full)
    let manager: glib::Object = unsafe { from_glib_full(m) };
    for s in scripts {
        let src = cstr(s);
        // WEBKIT_USER_CONTENT_INJECT_ALL_FRAMES, WEBKIT_USER_SCRIPT_INJECT_AT_DOCUMENT_START
        let script = call!(c"webkit_user_script_new" as extern "C" fn(*const c_char, c_int, c_int, Ptr, Ptr) -> Ptr, src.as_ptr(), 0, 0, std::ptr::null_mut(), std::ptr::null_mut());
        if let Some(script) = script.filter(|p| !p.is_null()) {
            call!(c"webkit_user_content_manager_add_script" as extern "C" fn(Obj, Ptr), m, script);
            call!(c"webkit_user_script_unref" as extern "C" fn(Ptr), script);
        }
    }
    Some(manager)
}

/// A WebKitWebView (a GtkWidget) on this session and content manager.
pub fn web_view(session: &glib::Object, content: &glib::Object) -> Option<gtk::Widget> {
    let t = gtype(c"webkit_web_view_get_type")?;
    let view = glib::Object::builder_with_type(t).property("network-session", session).property("user-content-manager", content).build();
    view.downcast::<gtk::Widget>().ok()
}

pub fn load_uri(view: &gtk::Widget, uri: &str) {
    let u = cstr(uri);
    call!(c"webkit_web_view_load_uri" as extern "C" fn(Obj, *const c_char), ptr(view), u.as_ptr());
}

pub fn go_back(view: &gtk::Widget) {
    call!(c"webkit_web_view_go_back" as extern "C" fn(Obj), ptr(view));
}

pub fn go_forward(view: &gtk::Widget) {
    call!(c"webkit_web_view_go_forward" as extern "C" fn(Obj), ptr(view));
}

pub fn reload(view: &gtk::Widget) {
    call!(c"webkit_web_view_reload" as extern "C" fn(Obj), ptr(view));
}

pub fn can_go_back(view: &gtk::Widget) -> bool {
    call!(c"webkit_web_view_can_go_back" as extern "C" fn(Obj) -> glib::ffi::gboolean, ptr(view)).unwrap_or(0) != 0
}

pub fn can_go_forward(view: &gtk::Widget) -> bool {
    call!(c"webkit_web_view_can_go_forward" as extern "C" fn(Obj) -> glib::ffi::gboolean, ptr(view)).unwrap_or(0) != 0
}

/// Run `script` in the page, its result unused.
pub fn evaluate_javascript(view: &gtk::Widget, script: &str) {
    let s = cstr(script);
    type F = extern "C" fn(Obj, *const c_char, isize, *const c_char, *const c_char, Ptr, Ptr, Ptr);
    let null = std::ptr::null();
    call!(c"webkit_web_view_evaluate_javascript" as F, ptr(view), s.as_ptr(), -1, null, null, std::ptr::null_mut(), std::ptr::null_mut(), std::ptr::null_mut());
}

/// WEBKIT_EDITING_COMMAND_PASTE and the like.
pub fn execute_editing_command(view: &gtk::Widget, command: &str) {
    let c = cstr(command);
    call!(c"webkit_web_view_execute_editing_command" as extern "C" fn(Obj, *const c_char), ptr(view), c.as_ptr());
}

/// The boxed pointer a signal argument holds (a WebKitNavigationAction).
pub fn boxed(v: &glib::Value) -> Ptr {
    // SAFETY: the value holds a boxed type
    unsafe { glib::gobject_ffi::g_value_get_boxed(v.to_glib_none().0) }
}

/// An enum signal argument's number.
pub fn enum_value(v: &glib::Value) -> i32 {
    // SAFETY: the value holds an enum
    unsafe { glib::gobject_ffi::g_value_get_enum(v.to_glib_none().0) }
}

/// A WebKitNavigationAction's (navigation type, uri). WEBKIT_NAVIGATION_TYPE_LINK_CLICKED is 0.
pub fn navigation_action(action: Ptr) -> (i32, String) {
    if action.is_null() {
        return (-1, String::new());
    }
    let kind = call!(c"webkit_navigation_action_get_navigation_type" as extern "C" fn(Ptr) -> c_int, action).unwrap_or(-1);
    let req = call!(c"webkit_navigation_action_get_request" as extern "C" fn(Ptr) -> Ptr, action).unwrap_or(std::ptr::null_mut());
    let uri = if req.is_null() { None } else { call!(c"webkit_uri_request_get_uri" as extern "C" fn(Ptr) -> *const c_char, req) };
    // SAFETY: a NUL-terminated string owned by the request
    let uri = uri.filter(|p| !p.is_null()).map(|p| unsafe { CStr::from_ptr(p) }.to_string_lossy().into_owned()).unwrap_or_default();
    (kind, uri)
}

/// A navigation policy decision's action.
pub fn decision_action(decision: &glib::Object) -> Ptr {
    call!(c"webkit_navigation_policy_decision_get_navigation_action" as extern "C" fn(Obj) -> Ptr, ptr(decision)).unwrap_or(std::ptr::null_mut())
}

pub fn decision_ignore(decision: &glib::Object) {
    call!(c"webkit_policy_decision_ignore" as extern "C" fn(Obj), ptr(decision));
}

pub fn permission_allow(request: &glib::Object) {
    call!(c"webkit_permission_request_allow" as extern "C" fn(Obj), ptr(request));
}

pub fn permission_deny(request: &glib::Object) {
    call!(c"webkit_permission_request_deny" as extern "C" fn(Obj), ptr(request));
}

/// A WebKitUserMediaPermissionRequest's (camera, microphone).
pub fn media_devices(request: &glib::Object) -> (bool, bool) {
    let v = call!(c"webkit_user_media_permission_is_for_video_device" as extern "C" fn(Obj) -> glib::ffi::gboolean, ptr(request)).unwrap_or(0);
    let a = call!(c"webkit_user_media_permission_is_for_audio_device" as extern "C" fn(Obj) -> glib::ffi::gboolean, ptr(request)).unwrap_or(0);
    (v != 0, a != 0)
}

pub fn set_download_destination(download: &glib::Object, path: &str) {
    let p = cstr(path);
    call!(c"webkit_download_set_destination" as extern "C" fn(Obj, *const c_char), ptr(download), p.as_ptr());
}

/// A GObject from a signal argument.
pub fn object_arg(v: &glib::Value) -> Option<glib::Object> {
    v.get::<glib::Object>().ok().or_else(|| {
        // SAFETY: the value holds an object (a WebKit type unknown to glib-rs' checks)
        let p = unsafe { glib::gobject_ffi::g_value_get_object(v.to_glib_none().0) };
        (!p.is_null()).then(|| obj(p as Ptr))
    })
}
