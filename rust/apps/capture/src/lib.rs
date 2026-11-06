//! Screenshots and screen recording (sonata2/shell/capture.py, livedraw.py,
//! replay.py, sharepicker.py, sharing.py).
//!
//! In Python these live in the menu bar process. Here they are a library
//! the Rust menu bar hosts ([`capture::Capture`], [`capture::install_actions`],
//! [`sharing::SharingControl`]), plus two entry points for `sonata2-rs`:
//!
//! - `sonata2-rs capture [toolbar|screen|area|edit|display|window|rec-display|
//!   rec-window|rec-area|stop|draw|replay-save|quit]` ([`run_capture`]): a
//!   resident host for Capture of its own (the menu bar's process hosts
//!   Capture in the session: host.rs in sonata2-rs).
//! - `sonata2-rs share-picker` ([`run_share_picker`]): the ScreenCast
//!   portal's chooser (stdin lines in, the chosen one out; install.sh points
//!   xdg-desktop-portal-wlr's config at it). To try it:
//!   `printf 'Monitor: eDP-1\n' | sonata2-rs share-picker`.

pub mod capture;
pub mod livedraw;
pub mod replay;
pub mod sharepicker;
pub mod sharing;

use gtk::{gio, glib, prelude::*};
use std::cell::RefCell;
use std::rc::Rc;

pub use sharepicker::run_share_picker;

pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.capture"
}

/// What `sonata2-rs capture <what>` asks the running host.
fn request(cap: &Rc<capture::Capture>, app: &gtk::Application, what: &str) {
    match what {
        "draw" => livedraw::get(app).toggle(None),
        "replay-save" => {
            let rp = cap.replay();
            if rp.running() {
                rp.save();
            }
        }
        "quit" => app.quit(),
        _ => capture::dispatch(cap, what),
    }
}

/// `sonata2-rs capture [WHAT]` (default: the toolbar): single instance and
/// resident, like the menu bar that hosts Capture in Python -- a recording,
/// the thumbnail and the instant replay outlive the command. Running it
/// again hands WHAT to the running one.
pub fn run_capture(args: &[String]) -> glib::ExitCode {
    sonata_shell::layer::ensure_preload();
    let app = gtk::Application::builder().application_id(app_id()).flags(gio::ApplicationFlags::HANDLES_COMMAND_LINE).build();
    type Host = Option<(Rc<capture::Capture>, gio::ApplicationHoldGuard)>;
    let state: Rc<RefCell<Host>> = Rc::default();
    let s = state.clone();
    app.connect_startup(move |app| {
        sonata_ui::setup();
        let cap = capture::Capture::new(app);
        capture::install_actions(app, &cap);
        s.replace(Some((cap, app.hold())));
    });
    let s = state.clone();
    app.connect_command_line(move |app, cl| {
        let argv: Vec<String> = cl.arguments().iter().map(|a| a.to_string_lossy().to_string()).collect();
        // [program, what]
        let what = argv.get(1).cloned().unwrap_or_else(|| "toolbar".into());
        if let Some((cap, _)) = s.borrow().as_ref() {
            request(cap, app, &what);
        }
        0
    });
    let s = state;
    app.connect_shutdown(move |_| {
        s.borrow_mut().take();
    });
    let mut argv = vec![std::env::args().next().unwrap_or_default()];
    argv.extend(args.iter().cloned());
    app.run_with_args(&argv)
}
