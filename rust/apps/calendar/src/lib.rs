//! Calendar (sonata2/calendar/): local calendars, one window.

pub mod alerts;
pub mod ics;
pub mod model;
pub mod views;
pub mod window;

use gtk::prelude::*;

/// The app id (its D-Bus name and Wayland app id).
pub fn app_id() -> &'static str {
    "io.github.vinioliveiras.sonata2.calendar"
}

/// Checkbox and dot colours of the palette (tokens where they exist): the
/// computed part of window.py's CSS, which the exporter leaves to us.
pub fn palette_css() -> String {
    model::PALETTE
        .iter()
        .map(|(cid, _, r)| {
            let c = if r.starts_with('#') { r.to_string() } else { format!("%({r})s") };
            format!(
                "checkbutton.cal-check.c-{cid} check {{ border-color: {c}; }}\n\
                 checkbutton.cal-check.c-{cid} check:checked {{ background-color: {c}; border-color: {c}; }}\n\
                 .cal-dot.c-{cid} {{ background-color: {c}; }}\n"
            )
        })
        .collect()
}

/// `sonata2-rs calendar [FILE.ics | sonata-date:YYYY-MM-DD ...]`.
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = gtk::Application::builder().application_id(app_id()).flags(gio::ApplicationFlags::HANDLES_OPEN).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let ready = std::rc::Rc::new(std::cell::Cell::new(false));
    let start = move || {
        if !ready.replace(true) {
            if glib::application_name().is_none() {
                glib::set_application_name("Calendar");
            }
            sonata_ui::setup();
            if let Some(f) = force {
                sonata_ui::force_appearance(f);
            }
            sonata_ui::css::register_module("calendar/window.py");
            sonata_ui::theme::register("calendar-palette", &palette_css());
        }
    };
    let s = start.clone();
    app.connect_open(move |app, files, _| {
        s();
        // a date the menu bar asks for comes as a "sonata-date:" URI
        let args: Vec<String> = files.iter().map(|f| match f.path() {
            Some(p) => p.display().to_string(),
            None => f.uri().to_string(),
        }).collect();
        window::open(app, &args);
    });
    app.connect_activate(move |app| {
        start();
        window::open(app, &[]);
    });
    let mut argv = vec![std::env::args().next().unwrap_or_default()];
    argv.extend(args.iter().filter(|a| !a.starts_with("--")).cloned());
    app.run_with_args(&argv)
}

#[cfg(test)]
mod tests {
    #[test]
    fn palette_css_like_python() {
        let css = super::palette_css();
        assert!(css.contains("checkbutton.cal-check.c-red check { border-color: %(sys_red)s; }"));
        assert!(css.contains(".cal-dot.c-brown { background-color: #a2845e; }"));
        assert_eq!(css.lines().count(), 21);
    }
}
