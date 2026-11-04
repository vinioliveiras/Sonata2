//! The welcome screen right after logging in (`sonata2-rs welcome`, first
//! in the session's autostart; only while intro::pending()) --
//! shell/welcome.py.
//!
//! The login screen's look -- blurred wallpaper, picture, name -- with a
//! spinner while the shell comes up (the wallpaper, menu bar, Dock and
//! Launchpad own their D-Bus names once running). Then it fades out and
//! the Dock and menu bar slide in (intro::finish()). Gives up waiting
//! after a few seconds.

use adw::prelude::*;
use sonata_i18n::tr;
use sonata_shell::{intro, layer, loginui};
use std::cell::RefCell;
use std::rc::Rc;
use std::time::{Duration, Instant};

/// the shell's parts the screen waits for (the Python shell's names: they
/// aren't ported, and once they are, the Rust ones take the same names)
pub const WAIT_FOR: [&str; 4] = ["wallpaper", "topbar", "dock", "launchpad"];
/// shown at least / at most
pub const MIN_S: f64 = 1.2;
pub const MAX_S: f64 = 9.0;
const FADE_MS: f64 = 520.0;

pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.welcome"
    } else {
        "io.github.vinioliveiras.sonata2.welcome"
    }
}

/// Leave now: everything is up (and it showed long enough), or it waited too long.
pub fn time_to_leave(ready: usize, total: usize, elapsed_s: f64) -> bool {
    (ready >= total && elapsed_s >= MIN_S) || elapsed_s >= MAX_S
}

/// The login screen's column as it was when the password was accepted --
/// picture, name, the field's slot turning (the spinner), the links and
/// hint rows -- so the picture stays exactly where it was (Vini: it rose on
/// the login screen and dropped back down here). The rows below the
/// spinner are kept but unseen.
fn login_column() -> gtk::Box {
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(10).halign(gtk::Align::Center).valign(gtk::Align::Center).css_classes(["gr-fade-in"]).build();
    col.append(&loginui::avatar(108, None, None, None));
    col.append(&gtk::Label::builder().label(loginui::display_name().as_str()).css_classes(["lk-name"]).build());
    let slot = gtk::Stack::builder().halign(gtk::Align::Center).build(); // as tall as the password field
    slot.add_named(&loginui::password_field(None), Some("field"));
    let spin = sonata_ui::progress::Spinner::new(true);
    spin.add_css_class("gr-spinner");
    spin.set_halign(gtk::Align::Center);
    spin.set_valign(gtk::Align::Center);
    slot.add_named(&spin, Some("progress"));
    slot.set_visible_child_name("progress"); // macOS: a spinner while the desktop loads
    col.append(&slot);
    let links = gtk::Box::builder().spacing(4).halign(gtk::Align::Center).opacity(0.0).can_target(false).build();
    if sonata_shell::logins::users().len() > 1 || sonata_shell::logins::sessions().len() > 1 {
        // the login screen had a link row there
        links.append(&gtk::Button::builder().label(tr!("Other Users").as_str()).css_classes(["gr-link"]).can_focus(false).build());
    }
    let hint = gtk::Label::builder().css_classes(["lk-hint"]).opacity(0.0).build();
    loginui::balanced(&col, &[links.upcast(), hint.upcast()]);
    col
}

struct Welcome {
    app: adw::Application,
    names: Vec<String>,
    started: Instant,
    windows: RefCell<Vec<gtk::Window>>,
    ready: RefCell<Vec<String>>,
    bus: Option<gio::DBusConnection>,
}

impl Welcome {
    fn new(app: &adw::Application) -> Rc<Self> {
        let w = Rc::new(Self {
            app: app.clone(),
            names: WAIT_FOR.iter().map(|c| format!("io.github.vinioliveiras.sonata2.{c}")).collect(),
            started: Instant::now(),
            windows: RefCell::default(),
            ready: RefCell::default(),
            // no session bus: time alone decides
            bus: gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE).ok(),
        });
        let tex = loginui::wallpaper_texture(0);
        let mons = layer::monitors();
        if mons.is_empty() {
            w.window(None, tex.clone(), true);
        }
        for (i, m) in mons.iter().enumerate() {
            w.window(Some(m), tex.clone(), i == 0);
        }
        let weak = Rc::downgrade(&w);
        glib::timeout_add_local(Duration::from_millis(120), move || match weak.upgrade() {
            Some(w) => w.poll(),
            None => glib::ControlFlow::Break,
        });
        w
    }

    fn window(&self, monitor: Option<&gtk::gdk::Monitor>, tex: Option<gtk::gdk::Texture>, primary: bool) {
        let win = gtk::Window::builder().application(&self.app).decorated(false).css_classes(["sonata-lock"]).build();
        let over = gtk::Overlay::new();
        over.set_child(Some(&loginui::Backdrop::new(tex, 0.18, 64.0)));
        if primary {
            let col = login_column();
            loginui::lift(&col, monitor);
            over.add_overlay(&col);
        }
        win.set_child(Some(&over));
        if layer::overlay_fullscreen(&win, "sonata2-welcome") {
            if let Some(m) = monitor {
                layer::set_monitor(&win, m);
            }
            layer::set_keyboard(&win, false);
        } else if let Some(m) = monitor {
            win.fullscreen_on_monitor(m);
        } else {
            win.fullscreen();
        }
        win.present();
        self.windows.borrow_mut().push(win);
    }

    fn has_owner(&self, name: &str) -> bool {
        let Some(bus) = &self.bus else { return false };
        bus.call_sync(Some("org.freedesktop.DBus"), "/org/freedesktop/DBus", "org.freedesktop.DBus", "NameHasOwner", Some(&(name,).to_variant()), Some(glib::VariantTy::new("(b)").expect("a type")), gio::DBusCallFlags::NONE, 500, gio::Cancellable::NONE)
            .ok()
            .and_then(|v| v.get::<(bool,)>())
            .is_some_and(|v| v.0)
    }

    fn poll(self: &Rc<Self>) -> glib::ControlFlow {
        for name in &self.names {
            if !self.ready.borrow().contains(name) && self.has_owner(name) {
                self.ready.borrow_mut().push(name.clone());
            }
        }
        if time_to_leave(self.ready.borrow().len(), self.names.len(), self.started.elapsed().as_secs_f64()) {
            let me = self.clone();
            // the first frames of the shell are drawn by then
            glib::timeout_add_local_once(Duration::from_millis(250), move || me.leave());
            return glib::ControlFlow::Break;
        }
        glib::ControlFlow::Continue
    }

    fn leave(self: &Rc<Self>) {
        intro::finish(); // the Dock and menu bar slide in now
        let start = Instant::now();
        for win in self.windows.borrow().iter() {
            let me = self.clone();
            win.add_tick_callback(move |win, _clock| {
                let t = (start.elapsed().as_secs_f64() * 1000.0 / FADE_MS).min(1.0);
                win.set_opacity(1.0 - t * t);
                if t < 1.0 {
                    return glib::ControlFlow::Continue;
                }
                win.set_visible(false);
                if me.windows.borrow().iter().all(|w| !w.is_visible()) {
                    me.app.quit();
                }
                glib::ControlFlow::Break
            });
        }
    }
}

/// `sonata2-rs welcome [--preview]`: right after login, covers the display
/// until the shell is up. A restart of Sonata has no intro: nothing shows.
pub fn run(args: &[String]) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview");
    if !preview {
        layer::ensure_preload();
    }
    let app = adw::Application::builder().application_id(app_id()).build();
    let state: Rc<RefCell<Option<Rc<Welcome>>>> = Rc::default();
    let hold: RefCell<Option<gio::ApplicationHoldGuard>> = RefCell::default();
    app.connect_activate(move |app| {
        if state.borrow().is_some() || (!intro::pending() && !preview) {
            return;
        }
        sonata_ui::setup();
        loginui::register_css();
        hold.replace(Some(app.hold()));
        state.replace(Some(Welcome::new(app)));
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn leaves_once_the_shell_is_up_or_too_late() {
        assert!(!time_to_leave(4, 4, 0.5)); // up at once: still shown a moment
        assert!(time_to_leave(4, 4, 1.3));
        assert!(!time_to_leave(3, 4, 5.0));
        assert!(time_to_leave(0, 4, 9.0)); // never waits longer
    }
}
