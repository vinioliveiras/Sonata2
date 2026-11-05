//! One CSS provider per process, filled from the tokens for the current
//! appearance and refilled when it changes (sonata2/ui/theme.py). Light /
//! Dark comes from Sonata's own settings (system.json, as prefs.py keeps
//! it), the accent from appearance.json; both are followed live.
//!
//! Not ported yet (docs/RUST_PORT.md): the user's radii and backgrounds,
//! the cross-fade on a change, the window buttons. Components that need
//! them come later.

use crate::glass as parts;
use crate::tokens::{self, Values};
use serde_json::json;
use std::cell::RefCell;

const PRIORITY: u32 = gtk::STYLE_PROVIDER_PRIORITY_USER + 10;

#[derive(Default)]
struct State {
    templates: Vec<(String, String, Values)>,
    provider: Option<gtk::CssProvider>,
    last_css: String,
    monitors: Vec<gtk::gio::FileMonitor>,
    forced: bool,
    listeners: Vec<Box<dyn Fn()>>,
}

thread_local! {
    static STATE: RefCell<State> = RefCell::new(State::default());
}

/// Add a component's CSS (`%(token)s` placeholders). Registering the same
/// `key` again replaces it; the same CSS again is nothing to do.
pub fn register(key: &str, template: &str) {
    register_with(key, template, Values::new());
}

/// [`register`] with extra placeholders for this template only.
pub fn register_with(key: &str, template: &str, local: Values) {
    let changed = STATE.with(|s| {
        let mut s = s.borrow_mut();
        if let Some(t) = s.templates.iter_mut().find(|t| t.0 == key) {
            if t.1 == template && t.2 == local {
                return false;
            }
            t.1 = template.to_string();
            t.2 = local;
        } else {
            s.templates.push((key.to_string(), template.to_string(), local));
        }
        s.provider.is_some()
    });
    if changed {
        load();
    }
}

pub fn is_dark() -> bool {
    adw::StyleManager::default().is_dark()
}

fn accent() -> String {
    let a = sonata_config::load("appearance", &json!({"accent": "blue"}));
    a.get("accent").and_then(|v| v.as_str()).unwrap_or("blue").to_string()
}

fn appearance_flag(key: &str) -> bool {
    sonata_config::stored("appearance").get(key).and_then(|v| v.as_bool()).unwrap_or(false)
}

/// True when the compositor blurs Sonata windows (Sonata session or the
/// dev session: SONATA_GLASS=1) and Reduce transparency is off. Elsewhere
/// materials fall back to their solid tokens.
pub fn glass() -> bool {
    std::env::var("SONATA_GLASS").as_deref() == Ok("1") && !appearance_flag("reduce_transparency")
}

/// The current tokens (for code that needs a value, e.g. drawing), plus
/// the materials resolved for this compositor (theme.values).
pub fn values() -> Values {
    let dark = is_dark();
    let mut v = tokens::palette(dark);
    v.extend(tokens::accent_tokens(&accent(), dark));
    let parts = parts::settings();
    let ok = glass();
    for (token, item) in [("sidebar_material", "windows"), ("panel_material", "menus"), ("dock_material", "dock"),
                          ("bar_material", "menubar"), ("launchpad_material", "launchpad")] {
        let m = parts::material(item, &v, ok, &parts);
        v.insert(token.into(), m);
    }
    let windows = parts.iter().find(|(i, _)| *i == "windows").map(|(_, p)| *p);
    if let Some(w) = windows.filter(|w| ok && w.on && appearance_flag("glass_titlebars")) {
        // title bars and toolbars: the glass again
        for k in ["titlebar_glass", "titlebar_glass_inactive"] {
            if let (Some(a), Some(c)) = (w.alpha, v.get(k).cloned()) {
                v.insert(k.into(), parts::with_alpha(&c, a));
            }
        }
        for (bg, glass_k) in [("titlebar_bg", "titlebar_glass"), ("titlebar_bg_inactive", "titlebar_glass_inactive")] {
            if let Some(c) = v.get(glass_k).cloned() {
                v.insert(bg.into(), c);
            }
        }
    }
    v
}

/// Call `callback` after every restyle (appearance or accent changed).
pub fn on_change<F: Fn() + 'static>(callback: F) {
    STATE.with(|s| s.borrow_mut().listeners.push(Box::new(callback)));
}

fn load() {
    let (templates, provider) = STATE.with(|s| {
        let s = s.borrow();
        (s.templates.clone(), s.provider.clone())
    });
    let Some(provider) = provider else { return };
    let vals = values();
    let css: Vec<String> = templates
        .iter()
        .map(|(_, t, local)| {
            let mut v = vals.clone();
            v.extend(local.iter().map(|(k, x)| (k.clone(), x.clone())));
            tokens::fill(t, &v)
        })
        .collect();
    let css = tokens::scale_durations(&css.join("\n"));
    let same = STATE.with(|s| {
        let mut s = s.borrow_mut();
        let same = s.last_css == css;
        s.last_css = css.clone();
        same
    });
    if same {
        return; // nothing changed: no re-parse, no redraw everywhere
    }
    provider.load_from_string(&css);
    // (listeners run outside the borrow: they may register CSS themselves)
    let listeners = STATE.with(|s| std::mem::take(&mut s.borrow_mut().listeners));
    for l in &listeners {
        l();
    }
    STATE.with(|s| {
        let mut s = s.borrow_mut();
        let added = std::mem::take(&mut s.listeners);
        s.listeners = listeners;
        s.listeners.extend(added);
    });
}

fn color_scheme_dark() -> bool {
    sonata_config::stored("system")
        .get("org.gnome.desktop.interface/color-scheme")
        .and_then(|v| v.as_str())
        == Some("prefer-dark")
}

fn follow_color_scheme() {
    if STATE.with(|s| s.borrow().forced) {
        return;
    }
    let scheme = if color_scheme_dark() { adw::ColorScheme::ForceDark } else { adw::ColorScheme::ForceLight };
    let sm = adw::StyleManager::default();
    if sm.color_scheme() != scheme {
        sm.set_color_scheme(scheme);
    }
}

/// A window made without the application (About This Computer, a
/// dialog's own window) is named after the program on Wayland, and every
/// component is the one `sonata2-rs`: the Dock took About This Computer
/// for the Assistant (Vini), the first entry running `sonata2-rs`. Each
/// process is its component's app id instead, as __main__.py does.
pub(crate) fn name_windows() {
    use gtk::{gio, glib, prelude::*};
    if let Some(id) = gio::Application::default().and_then(|a| a.application_id()) {
        glib::set_prgname(Some(id.as_str()));
    }
}

/// Install the provider once per process (idempotent), after the
/// application started (a display exists).
pub fn setup() {
    if STATE.with(|s| s.borrow().provider.is_some()) {
        return;
    }
    let Some(display) = gtk::gdk::Display::default() else {
        eprintln!("sonata-ui: no display");
        return;
    };
    name_windows();
    crate::icons::setup(); // Sonata's own icon theme, before any widget (as ui.setup does)
    let provider = gtk::CssProvider::new();
    gtk::style_context_add_provider_for_display(&display, &provider, PRIORITY);
    STATE.with(|s| s.borrow_mut().provider = Some(provider));
    crate::window::register_css();
    crate::controls::register_css();
    crate::menu::register_css();
    crate::dialog::register_css();
    crate::panel::register_css();
    adw::StyleManager::default().connect_dark_notify(|_| load());
    let mut monitors = sonata_config::watch("appearance", load);
    monitors.extend(sonata_config::watch("dock", load)); // its old "glass" switch (glass.rs)
    monitors.extend(sonata_config::watch("system", follow_color_scheme));
    STATE.with(|s| s.borrow_mut().monitors = monitors);
    follow_color_scheme();
    load();
}

/// "light" / "dark" for this process (previews); anything else follows Sonata's setting.
pub fn force_appearance(appearance: &str) {
    let forced = appearance == "dark" || appearance == "light";
    STATE.with(|s| s.borrow_mut().forced = forced);
    let sm = adw::StyleManager::default();
    match appearance {
        "dark" => sm.set_color_scheme(adw::ColorScheme::ForceDark),
        "light" => sm.set_color_scheme(adw::ColorScheme::ForceLight),
        _ => follow_color_scheme(),
    }
}

/// A colour token as a GdkRGBA (for drawing in a snapshot or with cairo).
pub fn rgba(token: &str) -> gtk::gdk::RGBA {
    values()
        .get(token)
        .and_then(|c| gtk::gdk::RGBA::parse(c.as_str()).ok())
        .unwrap_or(gtk::gdk::RGBA::BLACK)
}

#[cfg(test)]
mod name_tests {
    use gtk::{gio, glib, prelude::*};

    /// Vini: About This Computer opened as the Assistant in the Dock -- a
    /// window without the application was named `sonata2-rs`, which the
    /// Dock matched to the Assistant's entry. It is named after the
    /// component's app id now.
    #[test]
    fn test_about_opened_as_assistant() {
        let app = gio::Application::new(Some("io.github.vinioliveiras.sonata2.topbar"), gio::ApplicationFlags::NON_UNIQUE);
        app.set_default();
        glib::set_prgname(Some("sonata2-rs"));
        super::name_windows();
        assert_eq!(glib::prgname().as_deref(), Some("io.github.vinioliveiras.sonata2.topbar"));
    }
}
