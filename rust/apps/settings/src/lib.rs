//! Settings (sonata2/settings/): the macOS System Settings layout -- a
//! sidebar with coloured badges, one pane per section. The frame is
//! window.rs (with sections.rs, kit.rs, search.rs); the sections' pages
//! are pages/ (their brief: pages/mod.rs).
//!
//! `sonata2-rs settings [--page ID] [--dark|--light]`.

pub mod applist;
pub mod appicons_page;
#[cfg(feature = "system")]
pub mod apps_page;
pub mod arrange;
pub mod kit;
#[cfg(feature = "system")]
pub mod language_page;
pub mod pages;
pub mod search;
pub mod sections;
pub mod shortcuts_page;
pub mod window;

pub use window::{Ctx, Settings};

use gtk::prelude::*;
use gtk::{gio, glib};
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

/// Appearance (was General)
pub const DEFAULT_PAGE: &str = "appearance";
/// A closed Settings stays (hidden) this long: opening it again is instant.
pub const LINGER_S: u32 = 600;

/// The app id: on trial its own (`sonata2rs`), so both can be open.
pub fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.settings"
    } else {
        "io.github.vinioliveiras.sonata2.settings"
    }
}

/// sonata2-settings.desktop's text (shown in Apps as "Settings": Vini).
pub fn settings_desktop_entry(command: &str) -> String {
    format!(
        "[Desktop Entry]\nType=Application\nName=Settings\nComment=Sonata and system settings\nIcon=preferences-system\nCategories=Settings;System;\nStartupWMClass={}\nExec={command} settings\n",
        app_id()
    )
}

fn page_arg(args: &[String]) -> Option<String> {
    let i = args.iter().position(|a| a == "--page")?;
    args.get(i + 1).cloned()
}

#[derive(Default)]
struct State {
    win: Option<Ctx>,
    linger: Option<glib::SourceId>,
    page: Option<String>,
}

/// One Settings window. Closing hides it and the app lingers for ten
/// minutes (the hidden window keeps it running): opening Settings again is
/// instant. It opens on Appearance, unless asked for a pane (--page: the
/// menu bar's "Wi-Fi Preferences…").
fn present(app: &adw::Application, state: &Rc<RefCell<State>>) {
    let page = state.borrow_mut().page.take();
    let existing = state.borrow().win.clone();
    let win = match existing {
        None => {
            let win = Settings::new(app, page.as_deref().unwrap_or(DEFAULT_PAGE));
            let st = Rc::downgrade(state);
            win.window.connect_close_request(move |w| {
                w.set_visible(false);
                let Some(st) = st.upgrade() else { return glib::Propagation::Proceed };
                if let Some(src) = st.borrow_mut().linger.take() {
                    src.remove();
                }
                let st2 = Rc::downgrade(&st);
                let src = glib::timeout_add_seconds_local_once(LINGER_S, move || {
                    let Some(st) = st2.upgrade() else { return };
                    st.borrow_mut().linger = None;
                    let win = st.borrow().win.clone();
                    if let Some(win) = win.filter(|w| !w.window.is_visible()) {
                        // still closed: now really quit
                        st.borrow_mut().win = None;
                        win.window.destroy();
                    }
                });
                st.borrow_mut().linger = Some(src);
                glib::Propagation::Stop
            });
            state.borrow_mut().win = Some(win.clone());
            win
        }
        Some(win) => {
            if let Some(p) = page.as_deref().filter(|p| !p.is_empty()) {
                win.select(p);
            } else if !win.window.is_visible() {
                // opened again after closing: Appearance, not the last pane
                win.select(DEFAULT_PAGE);
            }
            win
        }
    };
    sonata_ui::window::show_again(&win.window); // it may have been minimized before it was closed
}

/// `sonata2-rs settings [--page ID] [--dark|--light]`.
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(app_id()).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let state = Rc::new(RefCell::new(State { page: page_arg(args), ..Default::default() }));
    let ready = std::cell::Cell::new(false);
    let setup = move || {
        if !ready.replace(true) {
            sonata_ui::setup();
            if let Some(f) = force {
                sonata_ui::force_appearance(f);
            }
        }
    };
    let setup = Rc::new(setup);
    let (st, s) = (state.clone(), setup.clone());
    app.connect_activate(move |app| {
        s();
        present(app, &st);
    });
    // a Settings already running (it lingers after closing) only gets
    // "activate" from a second launch, not its --page: the page goes over
    // as an action ("Wi-Fi Preferences…" opens Wi-Fi, not the last pane)
    let show_page = gio::SimpleAction::new("show-page", Some(glib::VariantTy::STRING));
    let (st, s, a) = (state.clone(), setup.clone(), app.downgrade());
    show_page.connect_activate(move |_, param| {
        st.borrow_mut().page = param.and_then(|p| p.get::<String>());
        if let Some(app) = a.upgrade() {
            s();
            present(&app, &st);
        }
    });
    app.add_action(&show_page);
    // "New Window" (the Dock's menu, middle-click): a second Settings window
    let new_window = gio::SimpleAction::new("new-window", None);
    let (st, s, a) = (state.clone(), setup.clone(), app.downgrade());
    new_window.connect_activate(move |_, _| {
        let Some(app) = a.upgrade() else { return };
        s();
        if st.borrow().win.is_none() {
            present(&app, &st); // the first one, set up as usual
            return;
        }
        let w = Settings::new(&app, DEFAULT_PAGE);
        w.window.present();
        // kept by the app (the window holds it); its own close destroys it
        let keep = w.clone();
        w.window.connect_destroy(move |_| {
            let _ = &keep;
        });
    });
    app.add_action(&new_window);
    if app.register(gio::Cancellable::NONE).is_ok() && app.is_remote() {
        app.activate_action("show-page", Some(&state.borrow().page.clone().unwrap_or_default().to_variant()));
        // (give D-Bus the call before the process ends)
        let ctx = glib::MainContext::default();
        let until = std::time::Instant::now() + Duration::from_millis(200);
        while std::time::Instant::now() < until {
            ctx.iteration(false);
        }
        return glib::ExitCode::SUCCESS;
    }
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
pub(crate) mod tests {
    /// Tests that set XDG_CONFIG_HOME take turns (one process environment).
    pub static CONFIG: std::sync::Mutex<()> = std::sync::Mutex::new(());

    #[test]
    fn page_argument() {
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(super::page_arg(&a(&["--page", "wifi"])), Some("wifi".into()));
        assert_eq!(super::page_arg(&a(&["--dark"])), None);
        assert!(super::settings_desktop_entry("sonata2-rs").contains("Exec=sonata2-rs settings\n"));
    }
}
