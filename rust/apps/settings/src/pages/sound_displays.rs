//! Sound, Displays (with Wallpaper and Arrange: crate::arrange), Battery.

use crate::window::Ctx;

// TODO port _page_sound
pub fn page_sound(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("sound")
}

// TODO port _page_displays
pub fn page_displays(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("displays")
}

// TODO port _page_wallpaper
pub fn page_wallpaper(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("wallpaper")
}

// TODO port _page_battery
pub fn page_battery(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("battery")
}
