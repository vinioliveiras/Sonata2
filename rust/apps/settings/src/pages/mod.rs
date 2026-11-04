//! The page registry: every part of Settings (app.py's `_page_<part>`
//! methods), one module per page group. A section (sections.rs) is built
//! from its parts in order, each part's groups added to one
//! `adw::PreferencesPage` under the section's title.
//!
//! # Writing a page (the brief)
//!
//! * One function per part, **named `page_<part>`** (the search index is
//!   read from that name at build time, build.rs):
//!   `pub fn page_wifi(ctx: &Ctx) -> Vec<adw::PreferencesGroup>`, listed in
//!   `build()` below. Helpers a page uses go in the same file (plain `fn`s:
//!   their tr! strings are found through the page that calls them; a fn
//!   whose name starts with `page_` is never followed).
//! * Strings: `sonata_i18n::{tr, trn, trp, n_, fmt}` with the SAME English
//!   text as the Python source (both catalogs already have it).
//! * Rows (crate::kit): `group(title, desc)`, `switch_row(title, on,
//!   on_change, subtitle)`, `combo_row(title, vec![(value, label)],
//!   &selected, on_change, subtitle)` (any `T: Clone + PartialEq`),
//!   `slider_row(title, value, lower, upper, on_change, subtitle, ends,
//!   default) -> (row, scale)`, `loading_row(title)`, `install_row(title,
//!   subtitle, command, Install{ready, on_ready, label, confirm}) -> (row,
//!   button)`, `badge`, `search_entry(placeholder, inline)`,
//!   `show_quietly(&switch_row, on)` / `show_quietly_combo(&combo, &value)`
//!   / `quietly(&row, || ...)` (Python's `row.quiet`: shown, not written
//!   back), `combo_value::<T>(&combo)`, `clear_group`, `nearest`,
//!   `clock_format`, `speed`, `minimize_animation`, `apply_buttons_side`,
//!   `is_admin`, `login_items` / `set_login_item`, `usb_warning()` /
//!   `usb_confirm()`, `BACKGROUNDS`, `ACCENTS`, `SLIDER_W`,
//!   `WALL_PREVIEW_PX`, and `run_async(work, done)` (system.run_async:
//!   work in a thread, done on the main loop).
//! * The window, `ctx: &Ctx` (= `Rc<Settings>`, window.rs). In closures
//!   hold `Rc::downgrade(ctx)` (a `Weak`), never the Rc: the window owns
//!   the page. What Python's `self.` gave:
//!   - `ctx.window` (an `adw::ApplicationWindow`), `ctx.parent()` (alerts'
//!     parent), `ctx.toast(text)`;
//!   - `ctx.select(id)` (a section or a part's old id, "appicons/<did>"),
//!     `ctx.current()`, `ctx.pane(id)` (its `main` page, header `hb`,
//!     `title`), `ctx.push_detail(title, &groups)` / `ctx.pop_detail(None)`,
//!     `ctx.rebuild_page(id)` / `ctx.reload_page(id)` (built again now);
//!   - `ctx.save(name, key, json!(v))` (config.update), `ctx.save_live(name,
//!     key, v)` (a slider: throttled, flushed on close), `ctx.flush_live()`;
//!   - `ctx.latest(key, work)` (Python `_latest`: one at a time per key, the
//!     newest waiting), `ctx.async_rows(&grp, work, fill)` ("Loading…" row);
//!   - `ctx.ask_reset(title, body, then)`, `ctx.ask_restart(kind, what)`
//!     ("sonata" / "session" / "system");
//!   - `ctx.keep(key, Rc<T>)` / `ctx.kept::<T>(key)`: a page's object kept
//!     for the window's life (Python `self.apps_page = ...`);
//!     `ctx.on_focus(part, |id| ...)`: open one item when "part/<id>" is
//!     selected; `ctx.on_close(|| ...)`: run when the window closes;
//!     `ctx.refresh_card()` (the sidebar's picture, after Users changes it).
//! * Backends: sonata_backend::system (Wi-Fi, sound, displays, power...),
//!   sonata_backend::wfconfig (Wayfire options), sonata_tweaks::*,
//!   sonata_config, sonata_desktop. backend/* still missing from those
//!   (mixer, bluez, users, updates, locales, permstore...) is in
//!   sonata-system, behind this crate's `system` feature
//!   (`#[cfg(feature = "system")]`; without it the page keeps its placeholder).
//! * The battery section followed power changes in Python
//!   (`power.watch(... reload_page("battery"))`): the battery page sets that
//!   up itself.
//! * CSS: app.py's blocks are registered by the window (kit::register_css);
//!   a page module with its own (css.json: "settings/<file>.py") registers it
//!   once with `sonata_ui::css::register_module`.
//! * Tests: pure logic as `#[test]`s in the page's file (no window, no
//!   system state); a ported Python bug test keeps its name and docstring.

use crate::window::Ctx;

pub mod apps;
pub mod input;
pub mod look;
pub mod network;
pub mod sound_displays;
pub mod system;

/// The groups of one part (None: no such part).
pub fn build(part: &str, ctx: &Ctx) -> Option<Vec<adw::PreferencesGroup>> {
    Some(match part {
        "wifi" => network::page_wifi(ctx),
        "network" => network::page_network(ctx),
        "bluetooth" => network::page_bluetooth(ctx),
        "printers" => network::page_printers(ctx),
        "sharing" => network::page_sharing(ctx),
        "sound" => sound_displays::page_sound(ctx),
        "displays" => sound_displays::page_displays(ctx),
        "wallpaper" => sound_displays::page_wallpaper(ctx),
        "battery" => sound_displays::page_battery(ctx),
        "keyboard" => input::page_keyboard(ctx),
        "shortcuts" => input::page_shortcuts(ctx),
        "trackpad" => input::page_trackpad(ctx),
        "mouse" => input::page_mouse(ctx),
        "gamepad" => input::page_gamepad(ctx),
        "appearance" => look::page_appearance(ctx),
        "dock" => look::page_dock(ctx),
        "desktop" => look::page_desktop(ctx),
        "menubar" => look::page_menubar(ctx),
        "controlcenter" => look::page_controlcenter(ctx),
        "launchpad" => look::page_launchpad(ctx),
        "hidden" => look::page_hidden(ctx),
        "apps" => apps::page_apps(ctx),
        "appicons" => apps::page_appicons(ctx),
        "datetime" => system::page_datetime(ctx),
        "defaults" => system::page_defaults(ctx),
        "users" => system::page_users(ctx),
        "notifications" => system::page_notifications(ctx),
        "updates" => system::page_updates(ctx),
        "sonataupdate" => system::page_sonataupdate(ctx),
        "privacy" => system::page_privacy(ctx),
        "accessibility" => system::page_accessibility(ctx),
        "about" => system::page_about(ctx),
        "language" => system::page_language(ctx),
        _ => return None,
    })
}

/// A part not ported yet: one row saying so (internal, not translated:
/// it goes once every page is ported).
pub fn placeholder(part: &str) -> Vec<adw::PreferencesGroup> {
    use adw::prelude::*;
    let g = adw::PreferencesGroup::new();
    g.add(&adw::ActionRow::builder().title("Coming soon").subtitle(part).use_markup(false).build());
    vec![g]
}

#[cfg(test)]
mod tests {
    use crate::sections::{parts_of, SECTIONS};

    /// Every section's parts have a builder (the match above), checked
    /// without a window: the names in the source.
    #[test]
    fn every_part_has_a_builder() {
        let src = include_str!("mod.rs");
        for (sid, ..) in SECTIONS {
            for part in parts_of(sid) {
                assert!(src.contains(&format!("\"{part}\" => ")), "{part}");
            }
        }
    }
}
