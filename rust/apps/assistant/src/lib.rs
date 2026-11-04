//! Assistant (sonata2/assistant/): chat with Claude. Its own binary
//! (sonata2-rs-assistant), so libsoup and libsecret are needed only to run it.

pub mod api;
pub mod keystore;
pub mod markdown;
pub mod store;
pub mod tools;
pub mod usage;
pub mod window;

use gtk::prelude::*;

/// The app id: on trial its own (`sonata2rs`), so both Assistants can be open.
pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.assistant"
    } else {
        "io.github.vinioliveiras.sonata2.assistant"
    }
}

/// `sonata2-rs assistant`: one window (opening it again brings it forward).
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = gtk::Application::builder().application_id(app_id()).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let ready = std::cell::Cell::new(false);
    app.connect_activate(move |app| {
        if !ready.replace(true) {
            sonata_ui::setup();
            if let Some(f) = force {
                sonata_ui::force_appearance(f);
            }
            sonata_ui::css::register_module("assistant/window.py");
        }
        window::present(app);
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}
