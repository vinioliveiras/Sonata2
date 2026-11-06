//! Mission Control, the app switcher and Spaces (sonata2/shell/mission.py,
//! switcher.py, spaces.py, apptabs.py, frost.py, preview.py).
//!
//! In the Python Sonata these live in the menu bar's process: the backdrop
//! and the desktops bar follow Wayfire's events, the switcher is the menu
//! bar's "switcher" action (Alt+Tab -> gdbus). Here they are a library the
//! Rust menu bar can host (`MissionBackdrop::new`, `Switcher::new`), and two
//! entry points of their own:
//!
//!   sonata2-rs switcher [next|prev] [--preview]
//!   sonata2-rs mission [--preview]
//!
//! `--preview`: a normal window with the desktops bar over a sample of
//! desktops kept in memory -- it never asks the real Wayfire nor writes its
//! config.

pub mod apptabs;
pub mod frost;
pub mod mission;
pub mod preview;
pub mod spaces;
pub mod switcher;

use gtk::prelude::*;
use serde_json::{json, Value};
use std::cell::RefCell;
use std::io::Write;
use std::os::fd::AsRawFd;
use std::os::unix::net::UnixStream;
use std::rc::Rc;

/// Something that answers Wayfire's IPC calls: Wayfire itself, or a fake
/// one (tests, previews).
pub trait Ipc {
    /// One request; None without an answer.
    fn call(&self, method: &str, data: Value) -> Option<Value>;
}

/// The running Wayfire ($WAYFIRE_SOCKET).
#[derive(Clone, Copy, Debug, Default)]
pub struct Wayfire;

impl Ipc for Wayfire {
    fn call(&self, method: &str, data: Value) -> Option<Value> {
        sonata_backend::wfipc::call(method, data)
    }
}

/// A Wayfire call off the main loop (each is a socket round trip).
pub fn call_bg(work: impl FnOnce() + Send + 'static) {
    let _ = std::thread::Builder::new().name("mission-ipc".into()).spawn(work);
}

/// The shell's own surfaces, never apps (__main__.SHELL_IDS), Python's and
/// the Rust build's. (The Dock keeps the same list: dock/toplevels.rs.)
pub fn shell_surface(app_id: &str) -> bool {
    const SHELL: [&str; 13] = ["dock", "autostart", "wallpaper", "launchpad", "topbar", "gallery", "lock", "spotlight", "greeter", "welcome", "portal", "setup", "polkit"];
    ["io.github.vinioliveiras.sonata2.", "io.github.vinioliveiras.sonata2rs."].iter().any(|p| app_id.strip_prefix(p).is_some_and(|rest| SHELL.contains(&rest)))
}

/// A glass surface's "solid" class in step with the setting (theme.glass_class).
pub fn glass_class(widget: &impl IsA<gtk::Widget>) {
    let weak = widget.upcast_ref::<gtk::Widget>().downgrade();
    let sync = move || {
        if let Some(w) = weak.upgrade() {
            if sonata_ui::glass() { w.remove_css_class("solid") } else { w.add_css_class("solid") }
        }
    };
    sync();
    sonata_ui::on_change(sync);
}

/// Wayfire's event stream, followed on the main loop (wfipc.WayfireIPC.watch;
/// sonata-backend's wfipc has no watch yet). Dropped: no more events.
pub struct Watch {
    _stream: UnixStream,
    source: Option<glib::SourceId>,
}

impl Drop for Watch {
    fn drop(&mut self) {
        if let Some(s) = self.source.take() {
            sonata_ui::cancel(s); // (gone already when Wayfire hung up: Break)
        }
    }
}

/// `callback(event)` for each of `events`; None without Wayfire.
pub fn watch_events(events: &[&str], callback: impl Fn(&Value) + 'static) -> Option<Watch> {
    let mut s = UnixStream::connect(sonata_backend::wfipc::socket()?).ok()?;
    s.set_read_timeout(Some(std::time::Duration::from_secs(2))).ok()?;
    s.write_all(&sonata_backend::wfipc::frame("window-rules/events/watch", &json!({"events": events}))).ok()?;
    sonata_backend::wfipc::read_message(&mut s)?; // {"result": "ok"}
    let reader = RefCell::new(s.try_clone().ok()?);
    let source = glib::unix_fd_add_local(s.as_raw_fd(), glib::IOCondition::IN | glib::IOCondition::HUP, move |_, cond| {
        if cond.contains(glib::IOCondition::HUP) {
            return glib::ControlFlow::Break; // Wayfire went away
        }
        let Some(msg) = sonata_backend::wfipc::read_message(&mut *reader.borrow_mut()) else { return glib::ControlFlow::Break };
        if msg.get("event").is_some() {
            callback(&msg);
        }
        glib::ControlFlow::Continue
    });
    Some(Watch { _stream: s, source: Some(source) })
}

fn app_id(component: &str) -> String {
    let base = "io.github.vinioliveiras.sonata2";
    format!("{base}.{component}")
}

/// Windows to show in the switcher's preview when there is no Wayfire.
struct SampleWindows;

impl switcher::Windows for SampleWindows {
    fn windows(&self) -> Vec<switcher::Win> {
        let w = |id, app: &str, title: &str, at| switcher::Win { id, app_id: app.into(), title: title.into(), minimized: false, focused_at: at };
        vec![w(1, "org.gnome.Nautilus", "Home", 40), w(2, "firefox", "Sonata 2 - GitHub", 30), w(3, "firefox", "Mozilla Firefox", 10), w(4, "org.gnome.Calculator", "Calculator", 20)]
    }

    fn activate(&self, wins: &[switcher::Win]) {
        println!("sonata2-rs switcher: would bring forward {:?}", wins.iter().map(|w| &w.title).collect::<Vec<_>>());
    }

    fn close(&self, wins: &[switcher::Win]) {
        println!("sonata2-rs switcher: would close {:?}", wins.iter().map(|w| &w.title).collect::<Vec<_>>());
    }
}

/// `sonata2-rs switcher [next|prev] [--preview]`: the app switcher, resident
/// once started; each call moves it one app (Wayfire's Alt+Tab binding can
/// run this, or activate the "switcher" action with "next" / "prev" over
/// D-Bus). With Mission Control open,
/// its highlight moves instead. --preview: a normal window (no layer
/// surface, no keyboard grab), sample windows when there is no Wayfire.
pub fn run_switcher(args: &[String]) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview");
    if !preview {
        sonata_shell::layer::ensure_preload();
    }
    let app = gtk::Application::builder().application_id(app_id("switcher")).flags(gio::ApplicationFlags::HANDLES_COMMAND_LINE).build();
    let state: Rc<RefCell<Option<Rc<switcher::Switcher>>>> = Rc::default();
    let hold: Rc<RefCell<Option<gio::ApplicationHoldGuard>>> = Rc::default();
    let step = {
        let (state, hold) = (state.clone(), hold.clone());
        move |app: &gtk::Application, back: bool| {
            if !preview && switcher::mission_control_step(&Wayfire, back) {
                return; // Mission Control open: its highlight moved instead
            }
            let sw = state.borrow().clone();
            let sw = sw.unwrap_or_else(|| {
                sonata_ui::setup();
                sonata_ui::css::register_module("shell/switcher.py");
                let windows: Rc<dyn switcher::Windows> = if preview && !sonata_backend::wfipc::available() { Rc::new(SampleWindows) } else { Rc::new(switcher::IpcWindows(Wayfire)) };
                let sw = switcher::Switcher::new(app, windows, !preview);
                hold.replace(Some(app.hold()));
                state.replace(Some(sw.clone()));
                sw
            });
            sw.step(if back { -1 } else { 1 });
        }
    };
    let action = gio::SimpleAction::new("switcher", Some(glib::VariantTy::STRING));
    let (a, s) = (app.downgrade(), step.clone());
    action.connect_activate(move |_, param| {
        if let Some(app) = a.upgrade() {
            s(&app, param.and_then(|p| p.str()) == Some("prev"));
        }
    });
    app.add_action(&action);
    app.connect_command_line(move |app, cl| {
        let back = cl.arguments().iter().skip(1).any(|a| a.to_str() == Some("prev"));
        step(app, back);
        0
    });
    let argv: Vec<String> = std::iter::once("sonata2-rs switcher".to_string()).chain(args.iter().cloned()).collect();
    app.run_with_args(&argv)
}

/// `sonata2-rs mission [--preview]`: Mission Control's dark backdrop, the
/// desktops bar and the windows' close button, following Wayfire.
pub fn run_mission(args: &[String]) -> glib::ExitCode {
    let preview = args.iter().any(|a| a == "--preview");
    if !preview {
        sonata_shell::layer::ensure_preload();
    }
    let app = gtk::Application::builder().application_id(app_id(if preview { "mission-preview" } else { "mission" })).build();
    let keep: Rc<RefCell<Vec<Box<dyn std::any::Any>>>> = Rc::default();
    app.connect_activate(move |app| {
        if !keep.borrow().is_empty() {
            return;
        }
        sonata_ui::setup();
        sonata_ui::css::register_module("shell/mission.py");
        sonata_ui::css::register_module("shell/spaces.py");
        if preview {
            keep.borrow_mut().push(Box::new(preview_mission(app)));
        } else {
            keep.borrow_mut().push(Box::new(mission::MissionBackdrop::new(app)));
            keep.borrow_mut().push(Box::new(app.hold()));
        }
    });
    app.run_with_args(&["sonata2-rs mission"])
}

/// The desktops bar over the sample wallpaper, darkened as Mission Control
/// darkens it, with three sample desktops.
fn preview_mission(app: &gtk::Application) -> (Rc<preview::PreviewWindow>, Rc<spaces::SpacesBar>) {
    sonata_ui::register("mission-preview", &format!(".mc-preview-dim {{ background: rgba(0, 0, 0, {}); }}", mission::DIM));
    let dim = gtk::Box::builder().css_classes(["mc-preview-dim"]).hexpand(true).vexpand(true).build();
    let size = preview::preview_size((1280, 800));
    let win = preview::PreviewWindow::new(app, Some(dim.upcast_ref()), size, None);
    let wall = sonata_shell::loginui::wallpaper_texture(mission::backdrop_size(&[(size.0, size.1, 1)])).or_else(|| preview::wallpaper(size.0, size.1, sonata_ui::is_dark()).map(|w| w.0));
    let sample = mission::SampleDesktops::new();
    let counter = sample.clone();
    let bar = spaces::SpacesBar::new(app, None, "PREVIEW-1", wall, sample, Rc::new(move |n| counter.set_count(n)), Some((win.win.clone().upcast(), win.over.clone())));
    win.win.present();
    bar.show_bar();
    (win, bar)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    type Respond = Box<dyn Fn(&str, &Value) -> Option<Value>>;

    /// A Wayfire that records what it's asked and answers from `respond`.
    pub struct FakeIpc {
        calls: RefCell<Vec<(String, Value)>>,
        respond: Respond,
    }

    impl FakeIpc {
        pub fn new(respond: impl Fn(&str, &Value) -> Option<Value> + 'static) -> FakeIpc {
            FakeIpc { calls: RefCell::default(), respond: Box::new(respond) }
        }

        /// The same answer to everything (null: none).
        pub fn answering(v: Value) -> FakeIpc {
            FakeIpc::new(move |_, _| (!v.is_null()).then(|| v.clone()))
        }

        pub fn calls(&self) -> Vec<(String, Value)> {
            self.calls.borrow().clone()
        }
    }

    impl Ipc for FakeIpc {
        fn call(&self, method: &str, data: Value) -> Option<Value> {
            let got = (self.respond)(method, &data);
            self.calls.borrow_mut().push((method.to_string(), data));
            got
        }
    }

    #[test]
    fn shell_surfaces_are_not_apps() {
        assert!(shell_surface("io.github.vinioliveiras.sonata2.dock"));
        assert!(shell_surface("io.github.vinioliveiras.sonata2rs.topbar"));
        assert!(!shell_surface("io.github.vinioliveiras.sonata2.files"));
        assert!(!shell_surface("firefox"));
    }

    #[test]
    fn no_wayfire_no_watch() {
        std::env::remove_var("WAYFIRE_SOCKET");
        assert!(watch_events(&mission::EVENTS, |_| {}).is_none());
    }
}
