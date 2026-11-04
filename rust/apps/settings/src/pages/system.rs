//! Date & Time, Default Apps, Users & Groups, Notifications, Security & Privacy, Accessibility, About (with Sonata Update and Software Update), Language & Region.

use crate::window::Ctx;

// TODO port _page_datetime
pub fn page_datetime(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("datetime")
}

// TODO port _page_defaults
pub fn page_defaults(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("defaults")
}

// TODO port _page_users
pub fn page_users(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("users")
}

// TODO port _page_notifications
pub fn page_notifications(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("notifications")
}

// TODO port _page_updates
pub fn page_updates(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("updates")
}

// TODO port _page_sonataupdate
pub fn page_sonataupdate(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("sonataupdate")
}

// TODO port _page_privacy
pub fn page_privacy(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("privacy")
}

// TODO port _page_accessibility
pub fn page_accessibility(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("accessibility")
}

// TODO port _page_about
pub fn page_about(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    super::placeholder("about")
}

/// Language & Region (language_page.rs): the system's language and
/// formats, apart from the keyboard.
pub fn page_language(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    {
        let page = crate::language_page::LanguagePage::new(ctx);
        let groups = page.groups();
        ctx.keep("language_page", page);
        groups
    }
    #[cfg(not(feature = "system"))]
    {
        let _ = ctx;
        super::placeholder("language")
    }
}

// TODO port _permissions_group (app.py, with Security & Privacy's helpers):
// what every app may use, shown first in Apps (pages/apps.rs calls it)
#[allow(dead_code)] // (called only with the "system" feature)
pub fn permissions_group(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    Vec::new()
}
