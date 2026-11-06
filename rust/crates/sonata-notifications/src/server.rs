//! The freedesktop notification server (org.freedesktop.Notifications):
//! apps' notify-send / libnotify / portal notifications arrive here. No
//! widget in it: what shows them (banners, Notification Center) plugs in
//! as a [`Presenter`], so the tests run it on a private bus with no
//! display.

use crate::model::{self, Core, Env, Hints, Note, Outcome, Request, SystemEnv, CLOSED_BY_APP, DISMISSED};
use glib::variant::ToVariant;
use serde_json::json;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};

pub const BUS_NAME: &str = "org.freedesktop.Notifications";
pub const PATH: &str = "/org/freedesktop/Notifications";
pub const IFACE: &str = "org.freedesktop.Notifications";

pub const XML: &str = r#"
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="Notify">
      <arg type="s" name="app_name" direction="in"/><arg type="u" name="replaces_id" direction="in"/>
      <arg type="s" name="app_icon" direction="in"/><arg type="s" name="summary" direction="in"/>
      <arg type="s" name="body" direction="in"/><arg type="as" name="actions" direction="in"/>
      <arg type="a{sv}" name="hints" direction="in"/><arg type="i" name="expire_timeout" direction="in"/>
      <arg type="u" name="id" direction="out"/>
    </method>
    <method name="CloseNotification"><arg type="u" name="id" direction="in"/></method>
    <method name="GetCapabilities"><arg type="as" name="caps" direction="out"/></method>
    <method name="GetServerInformation">
      <arg type="s" name="name" direction="out"/><arg type="s" name="vendor" direction="out"/>
      <arg type="s" name="version" direction="out"/><arg type="s" name="spec_version" direction="out"/>
    </method>
    <signal name="NotificationClosed"><arg type="u" name="id"/><arg type="u" name="reason"/></signal>
    <signal name="ActionInvoked"><arg type="u" name="id"/><arg type="s" name="action_key"/></signal>
  </interface>
</node>"#;

pub const CAPABILITIES: [&str; 5] = ["body", "body-markup", "actions", "icon-static", "persistence"];

/// What shows the notifications: the banners and Notification Center
/// (lib.rs), or a test's recorder.
pub trait Presenter {
    fn show_banner(&self, note: &Note);
    fn hide_banner(&self, id: u32);
    fn hide_all_banners(&self);
    /// Notification Center is on screen (no banners then: they'd be in it)
    fn center_open(&self) -> bool;
}

type Listener = Rc<dyn Fn()>;
type DndListener = Rc<dyn Fn(bool)>;

pub struct Server {
    core: RefCell<Core>,
    env: Box<dyn Env>,
    /// the session's real server: publishes the Dock's badges and keeps the
    /// list for a restart. Off in a --preview.
    serving: bool,
    conn: RefCell<Option<gio::DBusConnection>>,
    registration: RefCell<Option<gio::RegistrationId>>,
    owner: RefCell<Option<gio::OwnerId>>,
    owns_name: Cell<bool>,
    senders: RefCell<HashMap<String, Option<(String, String)>>>,
    presenter: RefCell<Option<Rc<dyn Presenter>>>,
    listeners: RefCell<Vec<Listener>>,
    dnd_listeners: RefCell<Vec<DndListener>>,
    monitors: RefCell<Vec<gio::FileMonitor>>,
}

impl Server {
    /// The server, with the list a restarted menu bar had (when serving).
    pub fn new(serving: bool) -> Rc<Self> {
        Self::with_env(Box::new(SystemEnv), serving)
    }

    pub fn with_env(env: Box<dyn Env>, serving: bool) -> Rc<Self> {
        let mut core = Core::default();
        if serving {
            // a menu bar started again (a crash, `sonata2 restart`) picks up the list
            // it had: the Dock's badges came back only with the next notification (Vini)
            core.notes = model::load_history();
            core.next = core.notes.iter().map(|n| n.id).max().unwrap_or(0) + 1;
        }
        let s = Rc::new(Server {
            core: RefCell::new(core),
            env,
            serving,
            conn: RefCell::default(),
            registration: RefCell::default(),
            owner: RefCell::default(),
            owns_name: Cell::new(false),
            senders: RefCell::default(),
            presenter: RefCell::default(),
            listeners: RefCell::default(),
            dnd_listeners: RefCell::default(),
            monitors: RefCell::default(),
        });
        if serving {
            s.publish_badges();
        }
        s
    }

    /// Follow notifications.json (Settings' Do Not Disturb and per-app switches).
    pub fn watch_config(self: &Rc<Self>) {
        let w = Rc::downgrade(self);
        let mons = sonata_config::watch(model::CONFIG, move || {
            if let Some(s) = w.upgrade() {
                s.config_changed();
            }
        });
        self.monitors.replace(mons);
    }

    pub fn config_changed(&self) {
        let was = self.dnd();
        self.core.borrow_mut().cfg = model::load_config();
        let on = self.dnd();
        if on {
            // turned on in Settings: banners on screen go too
            self.hide_all_banners();
        }
        if on != was {
            self.emit_dnd(on);
        }
    }

    pub fn set_presenter(&self, p: Option<Rc<dyn Presenter>>) {
        self.presenter.replace(p);
    }

    fn presenter(&self) -> Option<Rc<dyn Presenter>> {
        self.presenter.borrow().clone()
    }

    // -- the state --------------------------------------------------------------------------

    /// The list, newest last.
    pub fn notes(&self) -> Vec<Note> {
        self.core.borrow().notes.clone()
    }

    pub fn note(&self, id: u32) -> Option<Note> {
        self.core.borrow().note(id).cloned()
    }

    /// The ids handed out so far are below this.
    pub fn next_id(&self) -> u32 {
        self.core.borrow().next
    }

    pub fn dnd(&self) -> bool {
        self.core.borrow().dnd()
    }

    /// Do Not Disturb (Control Center): no banners at all (critical ones
    /// too: Chromium/Electron apps mark ordinary messages critical), the
    /// ones on screen go away; notifications still collect in the list.
    pub fn set_dnd(&self, on: bool) {
        let was = self.dnd();
        {
            let mut core = self.core.borrow_mut();
            core.cfg = model::load_config(); // what Settings wrote is kept
            core.cfg.insert("dnd".into(), json!(on));
        }
        let _ = sonata_config::update(model::CONFIG, &json!({"dnd": on}));
        if on {
            self.hide_all_banners();
        }
        if on != was {
            self.emit_dnd(on);
        }
    }

    /// `f()` whenever the list changes.
    pub fn connect_changed<F: Fn() + 'static>(&self, f: F) {
        self.listeners.borrow_mut().push(Rc::new(f));
    }

    /// `f(on)` when Do Not Disturb is turned on or off, here or in Settings.
    pub fn connect_dnd_changed<F: Fn(bool) + 'static>(&self, f: F) {
        self.dnd_listeners.borrow_mut().push(Rc::new(f));
    }

    fn emit_dnd(&self, on: bool) {
        let ls: Vec<DndListener> = self.dnd_listeners.borrow().clone();
        for f in ls {
            f(on);
        }
    }

    fn publish_badges(&self) {
        let notes: Vec<sonata_desktop::badges::Note> =
            self.core.borrow().notes.iter().map(|n| sonata_desktop::badges::Note { id: n.id as i64, desktop: n.desktop.clone(), app: n.app.clone() }).collect();
        sonata_desktop::badges::publish(&notes, std::process::id() as i64, None);
    }

    fn changed(&self) {
        if self.serving {
            self.publish_badges(); // the Dock's badges (badges.rs)
            model::save_history(&self.core.borrow().notes);
        }
        let ls: Vec<Listener> = self.listeners.borrow().clone();
        for f in ls {
            f();
        }
    }

    // -- what happens to a notification ----------------------------------------------------

    /// A notification (Notify): its id.
    pub fn notify(&self, r: Request) -> u32 {
        let out: Outcome = self.core.borrow_mut().notify(r, self.env.as_ref(), model::now());
        if out.listed {
            self.changed();
        }
        if let Some(n) = out.banner {
            if let Some(p) = self.presenter() {
                if !p.center_open() {
                    p.show_banner(&n);
                }
            }
        }
        out.id
    }

    /// reason: 1 expired, 2 dismissed, 3 closed by the app. A web app
    /// taking its notification back (WhatsApp does as its window is
    /// focused) only hides the banner: it stays in Notification Center
    /// until it is read there -- clicked or dismissed (Vini).
    pub fn close(&self, id: u32, reason: u32) {
        if let Some(p) = self.presenter() {
            p.hide_banner(id);
        }
        let kept = reason == CLOSED_BY_APP && self.core.borrow().kept_when_withdrawn(id);
        if !kept {
            let gone = self.core.borrow_mut().remove(id);
            if gone {
                self.changed();
            }
        }
        // (when kept: the app forgets it; ours stays)
        self.emit("NotificationClosed", (id, reason).to_variant());
    }

    /// A click on it (key "default") or one of its buttons.
    pub fn invoke(&self, n: &Note, key: &str) {
        let has_default = n.has_default();
        if key != "default" || has_default {
            self.emit("ActionInvoked", (n.id, key).to_variant());
        }
        if key == "default" {
            // a click on it: its app comes forward -- its window restored and
            // focused when open, else the app opened (Vini). An app that opens
            // itself for the click (its own "default" action) isn't opened twice.
            // A web app (Chrome answers the click itself but can't raise its
            // window on Wayland): its window comes forward, or it opens (Vini).
            let web = sonata_desktop::badges::is_webapp(&n.desktop);
            if !self.env.bring_forward(&n.desktop, &n.app) && (web || !has_default) && !n.desktop.is_empty() {
                self.env.launch(&n.desktop);
            }
        }
        self.close(n.id, DISMISSED);
    }

    /// Clear All.
    pub fn clear(&self) {
        let ids: Vec<u32> = self.notes().iter().map(|n| n.id).collect();
        self.dismiss(&ids);
    }

    /// Several dismissed at once -- Clear All, a group's x: one change to
    /// the list, so Notification Center sees them all leave together (one
    /// by one, a stack turned into a card on its way out).
    pub fn dismiss(&self, ids: &[u32]) {
        if let Some(p) = self.presenter() {
            for id in ids {
                p.hide_banner(*id);
            }
        }
        let mut gone = false;
        for id in ids {
            gone |= self.core.borrow_mut().remove(*id);
        }
        if gone {
            self.changed();
        }
        for id in ids {
            self.emit("NotificationClosed", (*id, DISMISSED).to_variant());
        }
    }

    fn hide_all_banners(&self) {
        if let Some(p) = self.presenter() {
            p.hide_all_banners();
        }
    }

    fn emit(&self, signal: &str, args: glib::Variant) {
        let conn = self.conn.borrow().clone();
        if let Some(c) = conn {
            let _ = c.emit_signal(None, PATH, IFACE, signal, Some(&args));
        }
    }

    // -- D-Bus ------------------------------------------------------------------------------

    /// Serve the interface at /org/freedesktop/Notifications on `conn`
    /// (reachable at the connection's own names; [`own_name`](Self::own_name)
    /// makes it the session's notification server).
    pub fn export(self: &Rc<Self>, conn: &gio::DBusConnection) -> bool {
        let Some(iface) = gio::DBusNodeInfo::for_xml(XML).ok().and_then(|n| n.lookup_interface(IFACE)) else { return false };
        let w: Weak<Server> = Rc::downgrade(self);
        let reg = conn
            .register_object(PATH, &iface)
            .method_call(move |conn, sender, _path, _iface, method, params, inv| {
                let Some(s) = w.upgrade() else { return };
                s.call(&conn, sender, method, &params, inv);
            })
            .build();
        match reg {
            Ok(id) => {
                self.conn.replace(Some(conn.clone()));
                self.registration.replace(Some(id));
                true
            }
            Err(e) => {
                eprintln!("sonata2-rs notifications: {e}");
                false
            }
        }
    }

    /// Take org.freedesktop.Notifications. If another daemon (mako,
    /// dunst...) already owns the name, Sonata leaves it alone.
    pub fn own_name(self: &Rc<Self>, conn: &gio::DBusConnection) {
        let a = Rc::downgrade(self);
        let l = Rc::downgrade(self);
        let id = gio::bus_own_name_on_connection(
            conn,
            BUS_NAME,
            gio::BusNameOwnerFlags::DO_NOT_QUEUE,
            move |_, _| {
                if let Some(s) = a.upgrade() {
                    s.owns_name.set(true);
                }
            },
            move |_, _| {
                if let Some(s) = l.upgrade() {
                    s.owns_name.set(false);
                }
            },
        );
        if let Some(old) = self.owner.replace(Some(id)) {
            gio::bus_unown_name(old);
        }
    }

    /// It is the session's notification server now.
    pub fn owns_name(&self) -> bool {
        self.owns_name.get()
    }

    fn call(&self, conn: &gio::DBusConnection, sender: Option<&str>, method: &str, params: &glib::Variant, inv: gio::DBusMethodInvocation) {
        match method {
            "GetCapabilities" => inv.return_value(Some(&(CAPABILITIES.iter().map(|s| s.to_string()).collect::<Vec<_>>(),).to_variant())),
            "GetServerInformation" => inv.return_value(Some(&("Sonata", "Sonata", "2", "1.2").to_variant())),
            "CloseNotification" => {
                let id = params.child_value(0).get::<u32>().unwrap_or(0);
                self.close(id, CLOSED_BY_APP);
                inv.return_value(None);
            }
            "Notify" => {
                // always answered: apps wait on the reply (freeze)
                let mut r = request_of(params);
                if let Some((desktop, name)) = sender.and_then(|s| self.webapp_sender(conn, s)) {
                    // a web app on Chromium: its own name and icon, not Chrome's
                    if !name.is_empty() {
                        r.app_name = name;
                    }
                    r.hints.desktop_entry = desktop;
                }
                let id = self.notify(r);
                inv.return_value(Some(&(id,).to_variant()));
            }
            _ => inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method),
        }
    }

    /// (desktop id, name) of the Sonata web app whose process sent this,
    /// else None. Once per sender.
    fn webapp_sender(&self, conn: &gio::DBusConnection, sender: &str) -> Option<(String, String)> {
        if let Some(found) = self.senders.borrow().get(sender) {
            return found.clone();
        }
        let pid = conn
            .call_sync(
                Some("org.freedesktop.DBus"),
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetConnectionUnixProcessID",
                Some(&(sender,).to_variant()),
                Some(glib::VariantTy::new("(u)").unwrap()),
                gio::DBusCallFlags::NONE,
                300,
                gio::Cancellable::NONE,
            )
            .ok()
            .and_then(|v| v.get::<(u32,)>())
            .map(|(p,)| p);
        let found = pid.and_then(|p| model::webapp_of_argv(&sonata_desktop::badges::cmdline(p as i64), self.env.as_ref()));
        let mut cache = self.senders.borrow_mut();
        if cache.len() > 200 {
            cache.clear();
        }
        cache.insert(sender.to_string(), found.clone());
        found
    }
}

impl Drop for Server {
    fn drop(&mut self) {
        if let Some(id) = self.owner.take() {
            gio::bus_unown_name(id);
        }
        if let (Some(c), Some(id)) = (self.conn.take(), self.registration.take()) {
            let _ = c.unregister_object(id);
        }
    }
}

/// A Notify call's arguments; anything malformed reads as empty.
pub fn request_of(params: &glib::Variant) -> Request {
    let s = |i: usize| if params.n_children() > i { params.child_value(i).get::<String>().unwrap_or_default() } else { String::new() };
    let hints = if params.n_children() > 6 { hints_of(&params.child_value(6)) } else { Hints::default() };
    Request {
        app_name: s(0),
        replaces_id: if params.n_children() > 1 { params.child_value(1).get::<u32>().unwrap_or(0) } else { 0 },
        app_icon: s(2),
        summary: s(3),
        body: s(4),
        actions: if params.n_children() > 5 { params.child_value(5).get::<Vec<String>>().unwrap_or_default() } else { Vec::new() },
        hints,
        expire_timeout: if params.n_children() > 7 { params.child_value(7).get::<i32>().unwrap_or(-1) } else { -1 },
    }
}

/// The hints Sonata reads, from Notify's a{sv}.
pub fn hints_of(v: &glib::Variant) -> Hints {
    let d = glib::VariantDict::new(Some(v));
    let string = |k: &str| d.lookup_value(k, None).and_then(|v| v.get::<String>()).unwrap_or_default();
    let urgency = d.lookup_value("urgency", None).and_then(|v| {
        v.get::<u8>()
            .map(i64::from)
            .or_else(|| v.get::<i32>().map(i64::from))
            .or_else(|| v.get::<u32>().map(i64::from))
            .or_else(|| v.get::<i64>())
            .or_else(|| v.get::<i16>().map(i64::from))
            .or_else(|| v.get::<u16>().map(i64::from))
    });
    Hints { urgency, desktop_entry: string("desktop-entry"), image_path: string("image-path") }
}
