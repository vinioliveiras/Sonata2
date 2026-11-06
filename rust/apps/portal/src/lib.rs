//! Sonata's portal backend (sonata2/portal.py): every app that asks for an
//! Open or Save dialog through xdg-desktop-portal gets Sonata's panels
//! (sonata_files::chooser), every app reads Sonata's appearance through its
//! Settings (Dark Mode, fonts, accent colour... live), and the camera and
//! location portals ask through its Access dialog.
//!
//! `sonata2-rs portal` owns org.freedesktop.impl.portal.desktop.sonata on
//! the session bus and stays up for the session. Never on trial: the Python
//! portal owns the name then (`sonata2-rs chooser open|save|folder` tries
//! the panels instead).
//!
//! FileChooser: OpenFile, SaveFile, SaveFiles (handle, app_id,
//! parent_window, title, options -> response, results); response 0 done,
//! 1 cancelled, 2 other. Each call exports org.freedesktop.impl.portal.Request
//! at `handle` (Close).

use gtk::prelude::*;
use sonata_config::prefs;
use sonata_files::chooser::{Chooser, Filter, Mode, Options};
use sonata_i18n::tr;
use std::cell::RefCell;
use std::rc::Rc;

pub const BUS_NAME: &str = "org.freedesktop.impl.portal.desktop.sonata";
pub const PATH: &str = "/org/freedesktop/portal/desktop";
pub const APPEARANCE: &str = "org.freedesktop.appearance";
const APP_ID: &str = "io.github.vinioliveiras.sonata2.portal";

const CHOOSER_METHOD: &str = r#"
    <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
    <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
    <arg type="a{sv}" name="options" direction="in"/>
    <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>"#;

fn chooser_xml() -> String {
    format!(
        r#"<node><interface name="org.freedesktop.impl.portal.FileChooser">
  <method name="OpenFile">{m}</method><method name="SaveFile">{m}</method><method name="SaveFiles">{m}</method>
  <property name="version" type="u" access="read"/>
</interface></node>"#,
        m = CHOOSER_METHOD
    )
}

const SETTINGS_XML: &str = r#"<node><interface name="org.freedesktop.impl.portal.Settings">
  <method name="ReadAll">
    <arg type="as" name="namespaces" direction="in"/><arg type="a{sa{sv}}" name="value" direction="out"/>
  </method>
  <method name="Read">
    <arg type="s" name="namespace" direction="in"/><arg type="s" name="key" direction="in"/>
    <arg type="v" name="value" direction="out"/>
  </method>
  <signal name="SettingChanged">
    <arg type="s" name="namespace"/><arg type="s" name="key"/><arg type="v" name="value"/>
  </signal>
  <property name="version" type="u" access="read"/>
</interface></node>"#;

const ACCESS_XML: &str = r#"<node><interface name="org.freedesktop.impl.portal.Access">
  <method name="AccessDialog">
    <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
    <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
    <arg type="s" name="subtitle" direction="in"/><arg type="s" name="body" direction="in"/>
    <arg type="a{sv}" name="options" direction="in"/>
    <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
  </method>
</interface></node>"#;

const REQUEST_XML: &str = r#"<node><interface name="org.freedesktop.impl.portal.Request"><method name="Close"/></interface></node>"#;

pub type Values = Vec<(String, Vec<(String, glib::Variant)>)>;

/// {namespace: {key: value}}: Sonata's settings (prefs), plus
/// org.freedesktop.appearance (color-scheme 1 dark / 2 light, accent, contrast).
pub fn settings_values() -> Values {
    let data = prefs::load(); // system.json read once, not once per key
    let mut out: Values = prefs::keys(&data)
        .into_iter()
        .map(|(schema, keys)| {
            let kv = keys
                .into_iter()
                .map(|k| {
                    let v = match prefs::typed(&schema, &k, &data) {
                        prefs::Typed::B(b) => b.to_variant(),
                        prefs::Typed::I(i) => i.to_variant(),
                        prefs::Typed::D(d) => d.to_variant(),
                        prefs::Typed::S(s) => s.to_variant(),
                    };
                    (k, v)
                })
                .collect();
            (schema, kv)
        })
        .collect();
    let dark = prefs::get(prefs::I, "color-scheme", "", &data) == "prefer-dark";
    let look = sonata_config::load("appearance", &serde_json::json!({"accent": "blue", "reduce_transparency": false}));
    let accent = look.get("accent").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).unwrap_or("blue").to_string();
    let reduce = truthy(look.get("reduce_transparency"));
    out.push((
        APPEARANCE.into(),
        vec![
            ("color-scheme".into(), (if dark { 1u32 } else { 2u32 }).to_variant()),
            ("accent-color".into(), accent_rgb(&accent).to_variant()),
            ("contrast".into(), (reduce as u32).to_variant()),
        ],
    ));
    out
}

/// Python's bool() of a setting (portal.py and appperms.py read them so):
/// a 1 or "yes" written by an older Sonata or by hand counts as on.
fn truthy(v: Option<&serde_json::Value>) -> bool {
    use serde_json::Value;
    match v {
        None | Some(Value::Null) => false,
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    }
}

/// The accent as the portal spec wants it (sRGB 0..1), from Sonata's tokens.
fn accent_rgb(name: &str) -> (f64, f64, f64) {
    let hex = sonata_ui::tokens::accent_tokens(name, false).get("accent").cloned().or_else(|| sonata_ui::tokens::palette(false).get("accent").cloned()).unwrap_or_default();
    hex_rgb(&hex).unwrap_or((0.0, 0.48, 1.0))
}

fn hex_rgb(hex: &str) -> Option<(f64, f64, f64)> {
    let h = hex.strip_prefix('#').filter(|h| h.len() >= 6)?;
    let c = |i: usize| u8::from_str_radix(&h[i..i + 2], 16).ok().map(|v| v as f64 / 255.0);
    Some((c(0)?, c(2)?, c(4)?))
}

/// Portal namespace patterns: exact, or a trailing "*".
pub fn ns_matches(ns: &str, patterns: &[String]) -> bool {
    if patterns.is_empty() || patterns == [""] {
        return true;
    }
    patterns.iter().any(|p| ns == p || p.strip_suffix('*').is_some_and(|pre| ns.starts_with(pre)))
}

/// ay (NUL-terminated file name bytes) -> path text.
fn bytes_path(v: Option<Vec<u8>>) -> String {
    let raw = v.unwrap_or_default();
    let end = raw.iter().rposition(|b| *b != 0).map_or(0, |i| i + 1);
    String::from_utf8_lossy(&raw[..end]).into_owned()
}

/// A panel's arguments, from the portal's call.
#[derive(Debug, Clone, PartialEq)]
pub struct Args {
    pub mode: Mode,
    pub title: String,
    pub accept_label: String,
    pub multiple: bool,
    pub filters: Vec<Filter>,
    pub current_filter: usize,
    pub folder: Option<String>,
    pub name: String,
    pub files: Vec<String>,
}

/// Portal options -> the panel's arguments (pure: tested without D-Bus).
pub fn parse(method: &str, title: &str, opts: &glib::VariantDict) -> Args {
    let get = |k: &str| opts.lookup_value(k, None);
    let mut filters: Vec<Filter> = get("filters").and_then(|v| v.get()).unwrap_or_default();
    let mut index = 0;
    if let Some((name, rules)) = get("current_filter").and_then(|v| v.get::<Filter>()) {
        match filters.iter().position(|f| f.0 == name) {
            Some(i) => index = i,
            None => filters.insert(0, (name, rules)), // a filter that isn't in the list: add it
        }
    }
    let mut folder = bytes_path(get("current_folder").and_then(|v| v.get()));
    let mut name: String = get("current_name").and_then(|v| v.get()).unwrap_or_default();
    let current_file = bytes_path(get("current_file").and_then(|v| v.get()));
    if !current_file.is_empty() {
        let p = std::path::Path::new(&current_file);
        if folder.is_empty() {
            folder = p.parent().map(|d| d.display().to_string()).unwrap_or_default();
        }
        if name.is_empty() {
            name = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        }
    }
    let flag = |k: &str| get(k).and_then(|v| v.get::<bool>()).unwrap_or(false);
    let mode = match method {
        "OpenFile" if flag("directory") => Mode::Folder,
        "OpenFile" => Mode::Open,
        "SaveFiles" => Mode::Folder,
        _ => Mode::Save,
    };
    let files: Vec<Vec<u8>> = get("files").and_then(|v| v.get()).unwrap_or_default();
    Args {
        mode,
        title: title.into(),
        accept_label: get("accept_label").and_then(|v| v.get()).unwrap_or_default(),
        multiple: flag("multiple") && method == "OpenFile",
        filters,
        current_filter: index,
        folder: (!folder.is_empty()).then(|| gio::File::for_path(&folder).uri().to_string()),
        name,
        files: files.into_iter().map(|f| bytes_path(Some(f))).collect(),
    }
}

/// What the portal answers for the chosen uris (a{sv}).
pub fn results(args: &Args, uris: &[String], index: usize) -> glib::Variant {
    let mut uris = uris.to_vec();
    if args.mode == Mode::Folder && !args.files.is_empty() && !uris.is_empty() {
        // SaveFiles: one uri per file in the folder
        let base = gio::File::for_uri(&uris[0]);
        uris = args.files.iter().map(|f| base.child(std::path::Path::new(f).file_name().unwrap_or_default()).uri().to_string()).collect();
    }
    let d = glib::VariantDict::new(None);
    d.insert_value("uris", &uris.to_variant());
    if let Some(f) = args.filters.get(index) {
        d.insert_value("current_filter", &f.to_variant());
    }
    d.insert_value("writable", &true.to_variant());
    d.end()
}

fn answer(inv: gio::DBusMethodInvocation, response: u32, results: glib::Variant) {
    inv.return_value(Some(&glib::Variant::tuple_from_iter([response.to_variant(), results])));
}

fn empty() -> glib::Variant {
    glib::VariantDict::new(None).end()
}

/// The Access portal (camera, location...): 0 granted, 1 refused. Not
/// asking (Settings > Apps): granted at once.
fn access(title: &str, subtitle: &str, body: &str, opts: &glib::VariantDict, done: impl Fn(u32) + 'static) {
    let asking = sonata_config::load("appperms", &serde_json::json!({"ask": true})).get("ask").is_none_or(|v| truthy(Some(v)));
    if !asking {
        done(0);
        return;
    }
    let s = |k: &str| opts.lookup_value(k, None).and_then(|v| v.get::<String>()).filter(|s| !s.is_empty());
    let grant = s("grant_label").unwrap_or_else(|| tr!("Allow"));
    let deny = s("deny_label").unwrap_or_else(|| tr!("Don't Allow"));
    let text: Vec<&str> = [subtitle, body].into_iter().filter(|t| !t.is_empty()).collect();
    let text = if text.is_empty() { tr!("You can change this later in Settings › Installed Apps.") } else { text.join("\n") };
    let heading = if title.is_empty() { tr!("Allow access?") } else { title.to_string() };
    sonata_ui::dialog::alert(&heading, &text, &[("deny", deny, ""), ("grant", grant, "default")], move |rid| done(if rid == "grant" { 0 } else { 1 }), None);
}

/// The window that asked for a panel (the request's parent_window, as the
/// portal spec writes it).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Parent {
    /// "wayland:<handle>": a toplevel the app exported with xdg-foreign
    Wayland(String),
    /// "x11:<xid>", the window id in hex
    X11(u64),
    /// "" (no window), or something we can't read
    None,
}

pub fn parent_of(parent_window: &str) -> Parent {
    match parent_window.split_once(':') {
        Some(("wayland", h)) if !h.is_empty() => Parent::Wayland(h.to_string()),
        Some(("x11", xid)) => {
            let hex = xid.trim_start_matches("0x").trim_start_matches("0X");
            u64::from_str_radix(hex, 16).ok().filter(|x| *x != 0).map_or(Parent::None, Parent::X11)
        }
        _ => Parent::None,
    }
}

/// Keep the panel on top of the app that asked, as its dialog
/// ("wayland:<handle>": GDK imports the handle through zxdg_importer and
/// makes it the panel's parent). Nothing when that isn't possible: an X11
/// app (Xwayland) has no Wayland surface our panel could name, and on
/// another GDK backend there is no handle to import -- bring_to_front still
/// keeps the panel above it then.
pub fn attach_to_parent(win: &impl IsA<gtk::Window>, parent_window: &str) {
    let Parent::Wayland(handle) = parent_of(parent_window) else { return };
    win.as_ref().connect_realize(move |w| {
        set_transient_for_exported(w, &handle);
    });
}

/// gdk_wayland_toplevel_set_transient_for_exported, looked up at run time:
/// the gtk crate has no GdkWayland binding, and GTK only has the call when
/// built with its Wayland backend. GDK keeps the import and applies it when
/// the panel's xdg_toplevel is made (so realize, before it maps, is early
/// enough).
fn set_transient_for_exported(win: &gtk::Window, handle: &str) -> bool {
    use std::ffi::{c_char, c_void};
    type SetExported = unsafe extern "C" fn(*mut c_void, *const c_char) -> glib::ffi::gboolean;
    let Some(surface) = win.surface() else { return false };
    let is_wayland = glib::Type::from_name("GdkWaylandToplevel").is_some_and(|t| surface.type_().is_a(t));
    let Ok(h) = std::ffi::CString::new(handle) else { return false };
    if !is_wayland {
        return false;
    }
    // SAFETY: looking a name up among the libraries already loaded (GTK)
    let f = unsafe { libc::dlsym(libc::RTLD_DEFAULT, c"gdk_wayland_toplevel_set_transient_for_exported".as_ptr()) };
    if f.is_null() {
        return false;
    }
    // SAFETY: the symbol has this C signature (gdkwayland/gdkwaylandtoplevel.h),
    // and the surface was just checked to be a GdkWaylandToplevel
    unsafe {
        let f = std::mem::transmute::<*mut c_void, SetExported>(f);
        f(surface.as_ptr() as *mut c_void, h.as_ptr()) != 0
    }
}

/// The Open / Save panel always comes up above the app that asked: Wayfire
/// keeps it on top and focuses it (IPC: our window, found by this process
/// and its title). Asked again a few times while Wayfire hasn't mapped it.
pub fn bring_to_front(win: &impl IsA<gtk::Window>) {
    fn attempt(title: String, left: u32) {
        let pid = std::process::id() as u64;
        let views = sonata_backend::wfipc::call("window-rules/list-views", serde_json::json!({}));
        let list = views.as_ref().and_then(|v| v.as_array()).cloned().unwrap_or_default();
        let mine: Vec<&serde_json::Value> = list
            .iter()
            .filter(|v| v.get("pid").and_then(|p| p.as_u64()) == Some(pid) && v.get("type").and_then(|t| t.as_str()).is_none_or(|t| t == "toplevel") && v.get("mapped").and_then(|m| m.as_bool()).unwrap_or(true))
            .collect();
        let exact: Vec<&&serde_json::Value> = mine.iter().filter(|v| v.get("title").and_then(|t| t.as_str()) == Some(title.as_str())).collect();
        let view = exact.last().map(|v| **v).or_else(|| mine.last().copied());
        match view.and_then(|v| v.get("id")).cloned() {
            Some(id) => {
                sonata_backend::wfipc::call("wm-actions/set-always-on-top", serde_json::json!({"view_id": id, "state": true}));
                sonata_backend::wfipc::call("window-rules/focus-view", serde_json::json!({"id": id}));
            }
            None if left > 0 && views.is_some() => {
                glib::timeout_add_local_once(std::time::Duration::from_millis(80), move || attempt(title, left - 1));
            }
            None => {}
        }
    }
    win.as_ref().connect_map(|w| {
        let title = w.title().map(|t| t.to_string()).unwrap_or_default();
        glib::idle_add_local_once(move || attempt(title, 10));
    });
}

pub struct Portal {
    conn: gio::DBusConnection,
    last: RefCell<Values>,
    _monitors: RefCell<Vec<gio::FileMonitor>>,
}

impl Portal {
    pub fn new(app: &gtk::Application) -> Option<Rc<Self>> {
        let conn = app.dbus_connection()?;
        let p = Rc::new(Portal { conn: conn.clone(), last: RefCell::new(settings_values()), _monitors: RefCell::default() });
        let iface = |xml: &str, name: &str| gio::DBusNodeInfo::for_xml(xml).ok().and_then(|n| n.lookup_interface(name));
        // FileChooser
        let fc = iface(&chooser_xml(), "org.freedesktop.impl.portal.FileChooser")?;
        let a = app.downgrade();
        let c2 = conn.clone();
        conn.register_object(PATH, &fc)
            .method_call(move |_, _, _, _, method, params, inv| {
                let Some(app) = a.upgrade() else { return answer(inv, 2, empty()) };
                open_panel(&app, &c2, method, &params, inv);
            })
            .property(|_, _, _, _, prop| (prop == "version").then(|| 4u32.to_variant()).unwrap_or_else(|| 0u32.to_variant()))
            .build()
            .ok()?;
        // Settings (Dark Mode, fonts, accent... for every app): lives as long as the session
        let st = iface(SETTINGS_XML, "org.freedesktop.impl.portal.Settings")?;
        let w = Rc::downgrade(&p);
        conn.register_object(PATH, &st)
            .method_call(move |_, _, _, _, method, params, inv| {
                let Some(p) = w.upgrade() else { return };
                p.settings_call(method, &params, inv);
            })
            .property(|_, _, _, _, _| 2u32.to_variant())
            .build()
            .ok()?;
        let ac = iface(ACCESS_XML, "org.freedesktop.impl.portal.Access")?;
        conn.register_object(PATH, &ac)
            .method_call(move |_, _, _, _, _, params, inv| {
                let s = |i: usize| params.child_value(i).get::<String>().unwrap_or_default();
                let opts = glib::VariantDict::new(Some(&params.child_value(6)));
                let inv = RefCell::new(Some(inv));
                // never leave the app waiting: one answer, whatever happens
                access(&s(3), &s(4), &s(5), &opts, move |r| {
                    if let Some(i) = inv.take() {
                        answer(i, r, empty());
                    }
                });
            })
            .build()
            .ok()?;
        let w1 = Rc::downgrade(&p);
        let changed = move || {
            if let Some(p) = w1.upgrade() {
                p.settings_changed();
            }
        };
        let mut mons: Vec<gio::FileMonitor> = sonata_config::watch_path(&prefs::path(), changed.clone()).into_iter().collect();
        mons.extend(sonata_config::watch("appearance", changed));
        p._monitors.replace(mons);
        let a = app.downgrade();
        std::mem::forget(gio::bus_own_name_on_connection(&conn, BUS_NAME, gio::BusNameOwnerFlags::REPLACE, |_, _| {}, move |_, _| {
            if let Some(a) = a.upgrade() {
                a.quit();
            }
        }));
        std::mem::forget(app.hold());
        Some(p)
    }

    fn settings_call(&self, method: &str, params: &glib::Variant, inv: gio::DBusMethodInvocation) {
        // the values kept up to date by the watchers: rebuilding them here
        // meant ~30 file reads on every Read call, many per app start
        let vals = self.last.borrow();
        if method == "ReadAll" {
            let patterns: Vec<String> = params.child_value(0).get().unwrap_or_default();
            let map: std::collections::HashMap<String, std::collections::HashMap<String, glib::Variant>> =
                vals.iter().filter(|(ns, _)| ns_matches(ns, &patterns)).map(|(ns, kv)| (ns.clone(), kv.iter().cloned().collect())).collect();
            inv.return_value(Some(&glib::Variant::tuple_from_iter([map.to_variant()])));
            return;
        }
        let ns: String = params.child_value(0).get().unwrap_or_default();
        let key: String = params.child_value(1).get().unwrap_or_default();
        match vals.iter().find(|(n, _)| *n == ns).and_then(|(_, kv)| kv.iter().find(|(k, _)| *k == key)) {
            Some((_, v)) => inv.return_value(Some(&glib::Variant::tuple_from_iter([glib::Variant::from_variant(v)]))),
            None => inv.return_dbus_error("org.freedesktop.portal.Error.NotFound", &format!("{ns} {key}")),
        }
    }

    /// Tell apps what changed (they switch Dark Mode, fonts... live).
    fn settings_changed(&self) {
        let new = settings_values();
        {
            let old = self.last.borrow();
            for (ns, kv) in &new {
                for (key, v) in kv {
                    let before = old.iter().find(|(n, _)| n == ns).and_then(|(_, o)| o.iter().find(|(k, _)| k == key)).map(|(_, v)| v);
                    if before != Some(v) {
                        let _ = self.conn.emit_signal(None, PATH, "org.freedesktop.impl.portal.Settings", "SettingChanged", Some(&(ns.as_str(), key.as_str(), glib::Variant::from_variant(v)).to_variant()));
                    }
                }
            }
        }
        self.last.replace(new);
    }
}

fn open_panel(app: &gtk::Application, conn: &gio::DBusConnection, method: &str, params: &glib::Variant, inv: gio::DBusMethodInvocation) {
    let handle: String = params.child_value(0).get::<glib::variant::ObjectPath>().map(|o| o.as_str().to_string()).unwrap_or_default();
    let parent: String = params.child_value(2).get().unwrap_or_default();
    let title: String = params.child_value(3).get().unwrap_or_default();
    let opts = glib::VariantDict::new(Some(&params.child_value(4)));
    let args = parse(method, &title, &opts);
    let reg: Rc<RefCell<Option<gio::RegistrationId>>> = Rc::default();
    let (r, c) = (reg.clone(), conn.clone());
    let a2 = args.clone();
    let ch = Chooser::new(
        app,
        Options { mode: args.mode, title: args.title.clone(), accept_label: args.accept_label.clone(), multiple: args.multiple, filters: args.filters.clone(), current_filter: args.current_filter, folder: args.folder.clone(), name: args.name.clone() },
        Box::new(move |uris, index| {
            if let Some(id) = r.take() {
                let _ = c.unregister_object(id);
            }
            match uris {
                None => answer(inv, 1, empty()),
                Some(u) => answer(inv, 0, results(&a2, &u, index)),
            }
        }),
    );
    attach_to_parent(ch.window(), &parent);
    bring_to_front(ch.window()); // above the asking app, always
    if let Some(node) = gio::DBusNodeInfo::for_xml(REQUEST_XML).ok().and_then(|n| n.lookup_interface("org.freedesktop.impl.portal.Request")) {
        let w = Rc::downgrade(&ch);
        let id = conn
            .register_object(&handle, &node)
            .method_call(move |_, _, _, _, method, _, inv| {
                inv.return_value(None);
                if method == "Close" {
                    if let Some(c) = w.upgrade() {
                        c.finish(None);
                    }
                }
            })
            .build();
        reg.replace(id.ok());
    }
    ch.present();
}

pub fn run(args: &[String]) -> glib::ExitCode {
    let app = gtk::Application::builder().application_id(APP_ID).build();
    let portal: Rc<RefCell<Option<Rc<Portal>>>> = Rc::default();
    app.connect_activate(move |app| {
        if portal.borrow().is_some() {
            return;
        }
        sonata_ui::setup();
        if sonata_config::side_by_side() {
            eprintln!("sonata2-rs portal: on trial the Python portal serves the panels (try `sonata2-rs chooser`)");
            return;
        }
        portal.replace(Portal::new(app));
    });
    let _ = args;
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

/// `sonata2-rs chooser open|save|folder`: an Open / Save panel on its own,
/// what it chose printed (trying the panels without the portal).
pub fn run_chooser(args: &[String]) -> glib::ExitCode {
    let mode = match args.first().map(String::as_str) {
        Some("save") => Mode::Save,
        Some("folder") => Mode::Folder,
        _ => Mode::Open,
    };
    let app = gtk::Application::builder().application_id("io.github.vinioliveiras.sonata2rs.chooser").flags(gio::ApplicationFlags::NON_UNIQUE).build();
    app.connect_activate(move |app| {
        sonata_ui::setup();
        let filters: Vec<Filter> = vec![(tr!("All Files"), vec![(0, "*".into())]), ("Text".into(), vec![(1, "text/plain".into())])];
        let hold = app.hold();
        let ch = Chooser::new(
            app,
            Options { mode, title: String::new(), accept_label: String::new(), multiple: true, filters, current_filter: 0, folder: None, name: "Untitled.txt".into() },
            Box::new(move |uris, i| {
                println!("{uris:?} (filter {i})");
                drop(hold);
            }),
        );
        ch.present();
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn dict(pairs: &[(&str, glib::Variant)]) -> glib::VariantDict {
        let d = glib::VariantDict::new(None);
        for (k, v) in pairs {
            d.insert_value(k, v);
        }
        d
    }

    fn ay(s: &str) -> glib::Variant {
        let mut b = s.as_bytes().to_vec();
        b.push(0);
        b.to_variant()
    }

    #[test]
    fn parse_save_with_current_file_and_unknown_filter() {
        let f: Vec<Filter> = vec![("Text".into(), vec![(0, "*.txt".into())])];
        let cur: Filter = ("Markdown".into(), vec![(0, "*.md".into())]);
        let a = parse("SaveFile", "Save It", &dict(&[("filters", f.to_variant()), ("current_filter", cur.to_variant()), ("current_file", ay("/tmp/x/notes.md"))]));
        assert_eq!(a.mode, Mode::Save);
        assert_eq!(a.filters[0].0, "Markdown"); // a filter that isn't in the list: added first
        assert_eq!(a.current_filter, 0);
        assert_eq!(a.name, "notes.md");
        assert_eq!(a.folder.as_deref(), Some("file:///tmp/x"));
    }

    #[test]
    fn parse_open_folder_and_save_files() {
        let a = parse("OpenFile", "", &dict(&[("directory", true.to_variant()), ("multiple", true.to_variant())]));
        assert_eq!((a.mode, a.multiple), (Mode::Folder, true));
        let files: Vec<Vec<u8>> = vec![b"/a/one.txt\0".to_vec(), b"/b/two.txt\0".to_vec()];
        let a = parse("SaveFiles", "", &dict(&[("files", files.to_variant()), ("multiple", true.to_variant())]));
        assert_eq!((a.mode, a.multiple), (Mode::Folder, false));
        let r = glib::VariantDict::new(Some(&results(&a, &["file:///dest".into()], 0)));
        let uris: Vec<String> = r.lookup_value("uris", None).unwrap().get().unwrap();
        assert_eq!(uris, ["file:///dest/one.txt", "file:///dest/two.txt"]);
    }

    #[test]
    fn test_panel_was_not_attached_to_the_asking_window() {
        // The Rust portal read the request's parent_window and dropped it:
        // the Open / Save panel came up as a window of its own, not as the
        // asking app's dialog (the Python portal attaches it, xdg-foreign).
        assert_eq!(parent_of("wayland:Xk2fPq9"), Parent::Wayland("Xk2fPq9".into()));
        assert_eq!(parent_of("wayland:a:b"), Parent::Wayland("a:b".into())); // only the first ':' splits
        assert_eq!(parent_of("wayland:"), Parent::None);
        assert_eq!(parent_of("x11:3a00007"), Parent::X11(0x3a00007));
        assert_eq!(parent_of("x11:0x1f"), Parent::X11(0x1f));
        assert_eq!(parent_of("x11:zz"), Parent::None);
        assert_eq!(parent_of(""), Parent::None);
        assert_eq!(parent_of("mir:1"), Parent::None);
        // where GTK can start (not on Wayland here): attaching is quietly nothing
        if gtk::init().is_ok() {
            let w = gtk::Window::new();
            attach_to_parent(&w, "wayland:abc");
            WidgetExt::realize(&w);
            assert!(!set_transient_for_exported(&w, "abc"));
            w.destroy();
        }
    }

    #[test]
    fn settings_namespaces_and_appearance() {
        assert!(ns_matches("org.gnome.desktop.interface", &["org.gnome.*".into()]));
        assert!(!ns_matches("org.kde.x", &["org.gnome.*".into()]));
        assert!(ns_matches("anything", &[]));
        let v = settings_values();
        let app = v.iter().find(|(n, _)| n == APPEARANCE).unwrap();
        assert!(app.1.iter().any(|(k, val)| k == "color-scheme" && val.get::<u32>() == Some(2)));
        assert_eq!(hex_rgb("#ff0000"), Some((1.0, 0.0, 0.0)));
    }
}
