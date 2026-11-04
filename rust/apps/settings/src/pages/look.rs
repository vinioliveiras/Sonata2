//! Appearance, Dock, Desktop & Windows, Menu Bar, Control Center, Apps (the grid, Hidden & Protected Apps).

use crate::window::Ctx;

// TODO port _page_appearance
pub fn page_appearance(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("appearance")
}

// TODO port _page_dock
pub fn page_dock(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("dock")
}

// TODO port _page_desktop
pub fn page_desktop(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("desktop")
}

// TODO port _page_menubar
pub fn page_menubar(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("menubar")
}

// TODO port _page_controlcenter
pub fn page_controlcenter(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("controlcenter")
}

// TODO port _page_launchpad
pub fn page_launchpad(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("launchpad")
}

// TODO port _page_hidden
pub fn page_hidden(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("hidden")
}
