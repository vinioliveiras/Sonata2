//! Keyboard (with Keyboard Shortcuts), Mouse & Trackpad, Game Controllers.

use crate::window::Ctx;

// TODO port _page_keyboard
pub fn page_keyboard(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("keyboard")
}

// TODO port _page_trackpad
pub fn page_trackpad(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("trackpad")
}

// TODO port _page_mouse
pub fn page_mouse(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("mouse")
}

// TODO port _page_gamepad
pub fn page_gamepad(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("gamepad")
}

/// Keyboard Shortcuts (shortcuts_page.rs).
pub fn page_shortcuts(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let page = crate::shortcuts_page::ShortcutsPage::new(ctx);
    let groups = page.groups();
    ctx.keep("shortcuts_page", page);
    groups
}
