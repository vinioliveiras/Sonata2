//! Notifications, macOS Big Sur style (sonata2/shell/notifications.py).
//!
//! - A freedesktop notification server (org.freedesktop.Notifications on
//!   the session bus) in the menu bar process: apps' notify-send /
//!   libnotify / portal notifications arrive here. If another daemon
//!   (mako, dunst...) already owns the name, Sonata leaves it alone.
//! - Banners: glass cards at the top right, under the menu bar; slide in,
//!   stay 5 s (critical ones until closed), click = default action, hover
//!   shows the close button.
//! - Notification Center (click the clock): the notifications, newest
//!   first, over the Today widgets (a calendar).
//! - Do Not Disturb (Control Center; notifications.json "dnd"): no banners
//!   at all (critical ones too: Chromium/Electron apps mark ordinary
//!   messages critical), the ones on screen go away when it's turned on;
//!   notifications still collect in the Notification Center.
//!
//! The menu bar embeds it:
//!
//! ```text
//! let nc = Notifications::new(&app);        // once, in the main display's menu bar
//! nc.toggle_center(Some(&monitor));         // the clock
//! nc.dnd() / nc.set_dnd(on)                 // Control Center's toggle
//! nc.connect_dnd_changed(|on| ...)          // ... kept in step with Settings
//! nc.connect_changed(|| ...)                // the list changed
//! nc.server()                               // the list, close / invoke / clear
//! ```
//!
//! Trial: while the Python menu bar runs (`sonata_config::side_by_side()`)
//! it is the session's notification server, so the Rust one never takes
//! the name, never writes the Dock's badges file or the kept list. It
//! still serves the interface at /org/freedesktop/Notifications on its
//! own connection: `sonata2-rs notifications --preview` shows sample
//! banners, `--center` opens Notification Center too, and any
//! notification can be sent to it with
//!
//! ```text
//! gdbus call --session --dest io.github.vinioliveiras.sonata2rs.notifications \
//!   --object-path /org/freedesktop/Notifications \
//!   --method org.freedesktop.Notifications.Notify Firefox 0 '' Hi 'Hello there' '[]' '{}' -1
//! ```

pub mod banners;
pub mod card;
pub mod center;
pub mod model;
pub mod server;
pub mod today;

pub use model::Note;
pub use server::{Presenter, Server};

use gtk::prelude::*;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

/// What the server shows through: the banners, and Notification Center
/// (made at the first opening, or by the prewarm a moment after start).
struct Surfaces {
    app: gtk::Application,
    server: std::rc::Weak<Server>,
    banners: Rc<banners::Banners>,
    center: RefCell<Option<Rc<center::Center>>>,
    /// Notification Center's glass (the menu bar's: sonata-mission's frost)
    frost: RefCell<Option<center::FrostFn>>,
}

impl Surfaces {
    fn center(&self) -> Option<Rc<center::Center>> {
        if let Some(c) = self.center.borrow().as_ref() {
            return Some(c.clone());
        }
        let server = self.server.upgrade()?;
        let c = center::Center::new(&self.app, &server, self.frost.borrow().clone());
        self.center.replace(Some(c.clone()));
        Some(c)
    }
}

impl Presenter for Surfaces {
    fn show_banner(&self, n: &Note) {
        self.banners.show(n);
    }
    fn hide_banner(&self, id: u32) {
        self.banners.hide(id, true);
    }
    fn hide_all_banners(&self) {
        self.banners.hide_all(true);
    }
    fn center_open(&self) -> bool {
        self.center.borrow().as_ref().is_some_and(|c| c.is_open())
    }
}

/// Server + banners + Notification Center; one per menu bar process.
pub struct Notifications {
    server: Rc<Server>,
    surfaces: Rc<Surfaces>,
}

thread_local! {
    static CSS: Cell<bool> = const { Cell::new(false) };
}

fn register_css() {
    if !CSS.with(|c| c.replace(true)) {
        sonata_ui::css::register_module_with("shell/notifications.py", &[("nc_slide", model::NC_SLIDE_PX.to_string())]);
    }
}

impl Notifications {
    /// The session's notification server -- or, on trial, the same without
    /// the bus name (see the crate's doc).
    pub fn new(app: &gtk::Application) -> Rc<Self> {
        Self::with_mode(app, true)
    }

    /// `serving`: take org.freedesktop.Notifications, publish the Dock's
    /// badges and keep the list for a restart. Without it the interface is
    /// only on the app's own connection.
    pub fn with_mode(app: &gtk::Application, serving: bool) -> Rc<Self> {
        register_css();
        let server = Server::new(serving);
        server.watch_config();
        let surfaces = Rc::new(Surfaces { app: app.clone(), server: Rc::downgrade(&server), banners: banners::Banners::new(app, &server), center: RefCell::default(), frost: RefCell::default() });
        server.set_presenter(Some(surfaces.clone()));
        let conn = app.dbus_connection().or_else(|| gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE).ok());
        if let Some(conn) = conn {
            server.export(&conn);
            if serving {
                server.own_name(&conn);
            }
        }
        let me = Rc::new(Notifications { server, surfaces });
        // Notification Center built once, unseen, at start: its first
        // opening slides in without a hitch
        let w = Rc::downgrade(&me.surfaces);
        glib::timeout_add_local_once(std::time::Duration::from_millis(2500), move || {
            if let Some(s) = w.upgrade() {
                if s.center.borrow().is_none() && sonata_shell::layer::available() {
                    if let Some(c) = s.center() {
                        c.prewarm();
                    }
                }
            }
        });
        me
    }

    /// How Notification Center gets its glass (the display's picture,
    /// blurred once): set before it is first made (2.5 s after start).
    /// Unset: solid panels.
    pub fn set_frost(&self, f: center::FrostFn) {
        self.surfaces.frost.replace(Some(f));
    }

    pub fn server(&self) -> &Rc<Server> {
        &self.server
    }

    pub fn dnd(&self) -> bool {
        self.server.dnd()
    }

    pub fn set_dnd(&self, on: bool) {
        self.server.set_dnd(on);
    }

    /// `f(on)` when Do Not Disturb changes, here or in Settings.
    pub fn connect_dnd_changed<F: Fn(bool) + 'static>(&self, f: F) {
        self.server.connect_dnd_changed(f);
    }

    /// `f()` when the list changes.
    pub fn connect_changed<F: Fn() + 'static>(&self, f: F) {
        self.server.connect_changed(f);
    }

    pub fn center_open(&self) -> bool {
        self.surfaces.center_open()
    }

    /// Open / close Notification Center -- on `monitor`, the display of
    /// the menu bar whose clock was clicked (Vini: every display's clock
    /// opens the same one, not a bare calendar).
    pub fn toggle_center(&self, monitor: Option<&gtk::gdk::Monitor>) {
        let Some(c) = self.surfaces.center() else { return };
        if c.is_open() {
            c.hide_center();
            return;
        }
        // the banners move into the Center
        self.surfaces.banners.hide_all(false);
        c.show_center(monitor);
    }
}

/// Sample notifications for the preview (not translated: they stand for
/// what apps send).
fn samples(server: &Server) {
    use model::{Hints, Request};
    let r = |app: &str, summary: &str, body: &str, actions: &[&str], urgency: i64, desktop: &str| Request {
        app_name: app.into(),
        summary: summary.into(),
        body: body.into(),
        actions: actions.iter().map(|s| s.to_string()).collect(),
        hints: Hints { urgency: Some(urgency), desktop_entry: desktop.into(), image_path: String::new() },
        expire_timeout: -1,
        ..Default::default()
    };
    server.notify(r("Calendar", "Dentist", "Today at 15:00", &[], 1, ""));
    server.notify(r("Messages", "Ana", "Are we still on for <b>tonight</b>?", &["default", "", "reply", "Reply", "later", "Later"], 1, ""));
    server.notify(r("Software Update", "Updates available", "Restart to finish installing the updates.", &["restart", "Restart"], 2, ""));
}

/// `sonata2-rs notifications [--preview] [--center]`: the server and its
/// surfaces on their own (the menu bar normally embeds them). --preview:
/// sample banners; --center: Notification Center open too. On trial it
/// never takes the bus name (see the crate's doc).
pub fn run(args: &[String]) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview");
    let open_center = args.iter().any(|a| a == "--center");
    let id = "io.github.vinioliveiras.sonata2.notifications";
    let app = adw::Application::builder().application_id(id).build();
    let keep: Rc<RefCell<Option<Rc<Notifications>>>> = Rc::default();
    app.connect_activate(move |app| {
        if keep.borrow().is_some() {
            return;
        }
        sonata_ui::setup();
        sonata_ui::icons::setup();
        let nc = Notifications::with_mode(app.upcast_ref(), !preview);
        if preview {
            samples(nc.server());
        }
        if open_center {
            nc.toggle_center(None);
        }
        std::mem::forget(app.hold()); // a service: no window of its own most of the time
        keep.replace(Some(nc));
    });
    app.run_with_args(&[String::from("sonata2-rs")])
}

#[cfg(test)]
mod tests;
