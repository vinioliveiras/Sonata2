//! Task Manager (sonata2/activity/): processes, performance, app history,
//! startup apps, users, details and services, from /proc and /sys.

pub mod manage;
pub mod pages;
pub mod performance;
pub mod rows;
pub mod widgets;
pub mod window;
#[cfg(test)]
mod window_tests;

use gtk::prelude::*;

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.activity"
}

/// `sonata2-rs activity`: one window (opening it again brings it forward).
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
            sonata_ui::css::register_module("activity/widgets.py");
        }
        window::present(app);
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}
