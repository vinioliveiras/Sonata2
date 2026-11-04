//! Apps (installed apps: apps_page.rs) and App Icons (appicons_page.rs).

use crate::window::Ctx;

/// Installed Apps: every app, click one for its own page.
pub fn page_apps(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    {
        let page = crate::apps_page::AppsPage::new(ctx);
        // what apps may use lives here, with the apps (Vini) -- not in Security & Privacy
        let mut groups = super::system::permissions_group(ctx);
        groups.extend(page.groups());
        let p = page.clone();
        ctx.on_focus("apps", move |did| p.focus(did));
        ctx.keep("apps_page", page);
        groups
    }
    #[cfg(not(feature = "system"))]
    {
        let _ = ctx;
        super::placeholder("apps")
    }
}

/// App Icons: the frame's shape and every app's icon.
pub fn page_appicons(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let page = crate::appicons_page::AppIconsPage::new(ctx);
    let groups = page.groups();
    let p = page.clone();
    ctx.on_focus("appicons", move |did| p.focus(did));
    ctx.keep("appicons_page", page);
    groups
}
