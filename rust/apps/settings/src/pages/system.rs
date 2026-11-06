//! Date & Time, Default Apps, Users & Groups, Notifications, Security & Privacy, Accessibility, About (with Sonata Update and Software Update), Language & Region.
//!
//! The larger pages live in their own files next to this one (each with
//! its `page_<part>`, so the search index finds their strings):
//! system_users.rs, system_updates.rs, system_privacy.rs, system_about.rs.

use crate::kit::{combo_row, group, run_async, show_quietly, show_quietly_combo, switch_row};
use crate::window::Ctx;
use adw::prelude::*;
use serde_json::{json, Map, Value};
use sonata_backend::system;
use sonata_i18n::{gettext, tr};
use sonata_tweaks::prefs_write;
use std::cell::RefCell;
use std::rc::Rc;

#[cfg(feature = "system")]
#[path = "system_users.rs"]
mod users;

#[cfg(feature = "system")]
#[path = "system_updates.rs"]
mod updates;

#[cfg(feature = "system")]
#[path = "system_privacy.rs"]
mod privacy;

#[path = "system_about.rs"]
mod about;

/// What Settings' USB protection "Set Up…" runs as root (USBGuard allowing
/// every device while unlocked): install.sh runs the same.
#[cfg(feature = "system")]
pub fn usb_setup_script() -> String {
    privacy::usb_setup()
}

/// Read a row back later from its own on_change (it is made after the
/// closure): a weak slot, so the row doesn't keep itself alive.
type Slot<T> = Rc<RefCell<glib::WeakRef<T>>>;

fn slot<T: glib::object::ObjectType>() -> Slot<T> {
    Rc::new(RefCell::new(glib::WeakRef::new()))
}

// -- Date & Time ---------------------------------------------------------------------------------
/// The menu bar's own reading of its clock format (topbar_prefs.rs, an app
/// crate Settings doesn't link): `%` + an optional flag + one of `letters`.
fn has_directive(fmt: &str, letters: &[char]) -> bool {
    let c: Vec<char> = fmt.chars().collect();
    (0..c.len()).any(|i| {
        if c[i] != '%' {
            return false;
        }
        let mut j = i + 1;
        if c.get(j).is_some_and(|f| "-_0^#".contains(*f)) {
            j += 1;
        }
        c.get(j).is_some_and(|l| letters.contains(l))
    })
}

/// The clock shows the date (%-d, %d, %e) -- not "%d" only (Vini).
pub fn shows_date(fmt: &str) -> bool {
    has_directive(fmt, &['d', 'e'])
}

/// The clock is 24-hour (%H, %-H, %k).
pub fn is_24h(fmt: &str) -> bool {
    has_directive(fmt, &['H', 'k'])
}

/// topbar_prefs.DEFAULTS' clock format.
pub const CLOCK_DEFAULT: &str = "%a %-d %b  %H:%M";

fn clock_now() -> String {
    sonata_config::load("topbar", &json!({"clock_format": CLOCK_DEFAULT})).get("clock_format").and_then(Value::as_str).unwrap_or(CLOCK_DEFAULT).to_string()
}

pub fn page_datetime(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let auto = group("", "");
    let clock = group(&tr!("Clock"), "");
    let zone = group(&tr!("Time Zone"), "");
    let (w, a, z) = (Rc::downgrade(ctx), auto.clone(), zone.clone());
    run_async(|| (system::ntp(), system::timezone(), system::timezones()), move |(on, cur, zones)| {
        a.set_visible(on.is_some());
        if let Some(on) = on {
            let state = Rc::new(std::cell::Cell::new(on));
            let row_slot: Slot<adw::SwitchRow> = slot();
            let (w, st, rs) = (w.clone(), state.clone(), row_slot.clone());
            let row = switch_row(&tr!("Set date and time automatically"), on, move |v| {
                let (w, st, rs) = (w.clone(), st.clone(), rs.clone());
                run_async(move || system::set_ntp(v), move |ok| {
                    if ok {
                        st.set(v);
                    } else {
                        // back to what the system still has
                        if let Some(s) = w.upgrade() {
                            s.toast(&tr!("Couldn't change the setting"));
                        }
                        if let Some(r) = rs.borrow().upgrade() {
                            show_quietly(&r, st.get());
                        }
                    }
                });
            }, "");
            row_slot.borrow().set(Some(&row));
            a.add(&row);
        }
        if zones.is_empty() {
            z.add(&adw::ActionRow::builder().title(tr!("Time zone")).subtitle(&cur).build());
            return;
        }
        let zstate = Rc::new(RefCell::new(cur.clone()));
        let row_slot: Slot<adw::ComboRow> = slot();
        let (zs, rs) = (zstate.clone(), row_slot.clone());
        let options: Vec<(String, String)> = zones.iter().map(|z| (z.clone(), z.replace('_', " "))).collect();
        let row = combo_row(&tr!("Time zone"), options, &cur, move |zn: &String| {
            let (w, zs, rs, zn) = (w.clone(), zs.clone(), rs.clone(), zn.clone());
            let want = zn.clone();
            run_async(move || system::set_timezone(&want), move |ok| {
                let Some(s) = w.upgrade() else { return };
                if ok {
                    s.toast(&sonata_i18n::fmt(&tr!("Time zone: {zone}"), &[("zone", &zn)]));
                    *zs.borrow_mut() = zn;
                } else {
                    s.toast(&tr!("Couldn't change the time zone"));
                    if let Some(r) = rs.borrow().upgrade() {
                        show_quietly_combo(&r, &*zs.borrow());
                    }
                }
            });
        }, "");
        row.set_enable_search(true);
        row_slot.borrow().set(Some(&row));
        z.add(&row);
    });
    let fmt = clock_now();
    let set_clock = |w: std::rc::Weak<crate::window::Settings>, date: Option<bool>, h24: Option<bool>| {
        let Some(s) = w.upgrade() else { return };
        let cur = clock_now(); // now, not when the page was built
        s.save("topbar", "clock_format", json!(crate::kit::clock_format(date.unwrap_or_else(|| shows_date(&cur)), h24.unwrap_or_else(|| is_24h(&cur)))));
    };
    let w = Rc::downgrade(ctx);
    clock.add(&switch_row(&tr!("Use a 24-hour clock"), is_24h(&fmt), move |on| set_clock(w.clone(), None, Some(on)), ""));
    let w = Rc::downgrade(ctx);
    clock.add(&switch_row(&tr!("Show the date"), shows_date(&fmt), move |on| set_clock(w.clone(), Some(on), None), ""));
    vec![auto, zone, clock]
}

// -- Default Apps --------------------------------------------------------------------------------
/// Which app opens each kind of file and link (Vini: one place for all).
pub fn page_defaults(_ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    use sonata_tweaks::defaultapps as da;
    let apps = group(&tr!("Default Apps"), "");
    for k in da::KINDS.iter() {
        let options = da::candidates(k.id);
        let Some(first) = options.first().map(|o| o.0.clone()) else { continue };
        let kid = k.id;
        let row = combo_row(&gettext(k.title), options, &first, move |v: &String| {
            let v = v.clone();
            run_async(move || da::set_default(kid, &v), |_| ());
        }, "");
        row.add_prefix(&gtk::Image::from_icon_name(k.icon));
        apps.add(&row);
        if k.id == "web" {
            // xdg-settings is a slow shell script: read it off the main loop
            row.set_sensitive(false);
            let r = row.downgrade();
            run_async(system::default_browser, move |cur| {
                if let Some(r) = r.upgrade() {
                    show_quietly_combo(&r, &cur);
                    r.set_sensitive(true);
                }
            });
        } else {
            show_quietly_combo(&row, &da::current(k.id));
        }
    }
    let hint = group("", &tr!("Apps can also be chosen for one file: right-click it in Files, then Open With."));
    vec![apps, hint]
}

// -- Users & Groups ------------------------------------------------------------------------------
pub fn page_users(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    {
        users::page_users(ctx)
    }
    #[cfg(not(feature = "system"))]
    {
        let _ = ctx;
        super::placeholder("users")
    }
}

// -- Notifications -------------------------------------------------------------------------------
/// shell/notifications.py's DEFAULTS and APP_DEFAULTS (per app: key = its
/// desktop entry or name).
fn notif_defaults() -> Value {
    json!({"dnd": false, "badges": true, "apps": {}})
}

fn app_defaults() -> Map<String, Value> {
    json!({"name": "", "allow": true, "style": "banners", "center": true, "sound": true, "badge": true}).as_object().cloned().unwrap_or_default()
}

/// APP_DEFAULTS with an app's stored values over them.
pub fn app_settings(stored: Option<&Value>) -> Map<String, Value> {
    let mut a = app_defaults();
    if let Some(Value::Object(m)) = stored {
        for (k, v) in m {
            a.insert(k.clone(), v.clone());
        }
    }
    a
}

/// A stored value as Python's `if` reads it.
pub(crate) fn truthy(v: Option<&Value>) -> bool {
    match v {
        Some(Value::Bool(b)) => *b,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
        _ => false,
    }
}

fn flag(a: &Map<String, Value>, key: &str) -> bool {
    truthy(a.get(key))
}

/// An app's row subtitle: how its notifications show.
pub fn notif_summary(a: &Map<String, Value>) -> String {
    if !flag(a, "allow") {
        return tr!("Off");
    }
    if a.get("style").and_then(Value::as_str) == Some("banners") {
        tr!("Banners")
    } else if flag(a, "center") {
        tr!("Notification Center only")
    } else {
        tr!("None")
    }
}

/// Big Sur Notifications: Do Not Disturb, then every app that has
/// notified (allow, alert style, Notification Center).
pub fn page_notifications(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let cfg = sonata_config::load("notifications", &notif_defaults());
    let dnd = group(&tr!("Do Not Disturb"), &tr!("Banners stay hidden; notifications still collect in the Notification Center."));
    let w = Rc::downgrade(ctx);
    dnd.add(&switch_row(&tr!("Do Not Disturb"), flag(&cfg, "dnd"), move |on| {
        if let Some(s) = w.upgrade() {
            s.save("notifications", "dnd", json!(on));
        }
    }, ""));
    let w = Rc::downgrade(ctx);
    dnd.add(&switch_row(&tr!("Badge App Icons"), cfg.get("badges").map(|v| truthy(Some(v))).unwrap_or(true), move |on| {
        if let Some(s) = w.upgrade() {
            s.save("notifications", "badges", json!(on));
        }
    }, &tr!("A red count on an app's icon in the Dock")));
    let lst = group(&tr!("Application Notifications"), "");

    // one field of one app, written now (the rest of the file as stored)
    fn set_app(key: &str, field: &str, value: Value, row: Option<&adw::ExpanderRow>) {
        let c = sonata_config::load("notifications", &notif_defaults());
        let mut apps = c.get("apps").and_then(Value::as_object).cloned().unwrap_or_default();
        let mut a = app_settings(apps.get(key));
        a.insert(field.into(), value);
        apps.insert(key.into(), Value::Object(a.clone()));
        let _ = sonata_config::update("notifications", &json!({"apps": apps}));
        if let Some(r) = row {
            r.set_subtitle(&notif_summary(&a));
        }
    }
    let mut entries: Vec<(String, Value)> = cfg.get("apps").and_then(Value::as_object).map(|m| m.iter().map(|(k, v)| (k.clone(), v.clone())).collect()).unwrap_or_default();
    entries.sort_by_key(|(k, a)| a.get("name").and_then(Value::as_str).filter(|n| !n.is_empty()).unwrap_or(k).to_lowercase());
    for (key, stored) in &entries {
        let a = app_settings(Some(stored));
        let info = sonata_ui::apps::lookup(key);
        let name = match &info {
            Some(i) => i.display_name().to_string(),
            None => a.get("name").and_then(Value::as_str).filter(|n| !n.is_empty()).unwrap_or(key).to_string(),
        };
        let row = adw::ExpanderRow::builder().title(&name).subtitle(notif_summary(&a)).use_markup(false).build();
        let img = gtk::Image::builder().pixel_size(32).build();
        match &info {
            Some(i) => sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(i.upcast_ref())),
            None => img.set_icon_name(Some("application-x-executable")),
        }
        row.add_prefix(&img);
        let (k, r) = (key.clone(), row.downgrade());
        row.add_row(&switch_row(&tr!("Allow Notifications"), flag(&a, "allow"), move |on| set_app(&k, "allow", json!(on), r.upgrade().as_ref()), ""));
        let (k, r) = (key.clone(), row.downgrade());
        let style = a.get("style").and_then(Value::as_str).unwrap_or("banners").to_string();
        row.add_row(&combo_row(&tr!("Alert style"), vec![("banners".to_string(), tr!("Banners")), ("none".to_string(), tr!("None"))], &style, move |v: &String| set_app(&k, "style", json!(v), r.upgrade().as_ref()), &tr!("Banners appear at the top right and go away automatically")));
        let (k, r) = (key.clone(), row.downgrade());
        row.add_row(&switch_row(&tr!("Show in Notification Center"), flag(&a, "center"), move |on| set_app(&k, "center", json!(on), r.upgrade().as_ref()), ""));
        let k = key.clone();
        row.add_row(&switch_row(&tr!("Badge App Icon"), flag(&a, "badge"), move |on| set_app(&k, "badge", json!(on), None), ""));
        lst.add(&row);
    }
    if entries.is_empty() {
        lst.add(&adw::ActionRow::builder().title(tr!("No notifications yet")).subtitle(tr!("Apps appear here after they send their first notification")).build());
    }
    vec![dnd, lst]
}

// -- Software Update, Sonata Update --------------------------------------------------------------
pub fn page_updates(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    {
        updates::page_updates(ctx)
    }
    #[cfg(not(feature = "system"))]
    {
        let _ = ctx;
        super::placeholder("updates")
    }
}

pub fn page_sonataupdate(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    {
        updates::page_sonataupdate(ctx)
    }
    #[cfg(not(feature = "system"))]
    {
        let _ = ctx;
        super::placeholder("sonataupdate")
    }
}

// -- Security & Privacy --------------------------------------------------------------------------
pub fn page_privacy(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    {
        privacy::page_privacy(ctx)
    }
    #[cfg(not(feature = "system"))]
    {
        let _ = ctx;
        super::placeholder("privacy")
    }
}

// -- Accessibility -------------------------------------------------------------------------------
const IFACE: &str = "org.gnome.desktop.interface";
/// Accessibility's text sizes (text-scaling-factor).
pub const TEXT_SCALES: [f64; 3] = [1.0, 1.15, 1.3];

/// The title bars' colours again (Reduce transparency changed them).
fn apply_titlebars() {
    let dark = sonata_ui::theme::is_dark();
    run_async(move || sonata_tweaks::titlebars::apply_colors(dark), |_| ());
}

/// The cursor size option closest to the one set (`nearest` for whole pixels).
pub fn nearest_size(sizes: &[i64], px: i64) -> i64 {
    sizes.iter().copied().min_by_key(|s| (s - px).abs()).unwrap_or(px)
}

/// Pointer size, the same row as Mouse & Trackpad's (cursors.rs).
fn pointer_size_row(ctx: &Ctx) -> adw::ComboRow {
    use sonata_tweaks::cursors;
    let label = |px: i64| match px {
        24 => tr!("Normal"),
        32 => tr!("Large"),
        48 => tr!("Larger"),
        _ => tr!("Largest"),
    };
    let opts: Vec<(i64, String)> = cursors::SIZES.iter().map(|px| (*px, label(*px))).collect();
    let w = Rc::downgrade(ctx);
    combo_row(&tr!("Pointer size"), opts, &nearest_size(&cursors::SIZES, cursors::size()), move |v: &i64| {
        let (w, v) = (w.clone(), *v);
        run_async(move || cursors::apply(None, Some(v)), move |_| {
            // both places that show the pointer's settings, again
            if let Some(s) = w.upgrade() {
                for sid in ["mouse", "accessibility"] {
                    s.rebuild_page(sid);
                }
            }
        });
    }, "")
}

pub fn page_accessibility(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let disp = group(&tr!("Display"), "");
    let anim = prefs_write::get(IFACE, "enable-animations");
    disp.add(&switch_row(&tr!("Reduce motion"), anim == "false", |on| {
        prefs_write::set(IFACE, "enable-animations", if on { "false" } else { "true" });
        run_async(|| sonata_backend::wfconfig::set("animate", "open_animation", "fade"), |_| ());
        run_async(|| sonata_backend::wfconfig::set("animate", "close_animation", "fade"), |_| ());
    }, ""));
    let app = sonata_tweaks::frame::appearance();
    let w = Rc::downgrade(ctx);
    disp.add(&switch_row(&tr!("Reduce transparency"), truthy(app.get("reduce_transparency")), move |on| {
        if let Some(s) = w.upgrade() {
            s.save("appearance", "reduce_transparency", json!(on));
            apply_titlebars();
        }
    }, &tr!("Solid sidebars, Dock and title bars instead of glass")));
    let renderer = app.get("renderer").and_then(Value::as_str).unwrap_or("gl").to_string();
    let w = Rc::downgrade(ctx);
    let graphics = vec![("gl".to_string(), tr!("Hardware (OpenGL)")), ("vulkan".to_string(), tr!("Hardware (Vulkan)")), ("software".to_string(), tr!("Software (no GPU)"))];
    disp.add(&combo_row(&tr!("Graphics"), graphics, &renderer, move |v: &String| {
        if let Some(s) = w.upgrade() {
            s.save("appearance", "renderer", json!(v));
            s.ask_restart("sonata", &tr!("The new graphics setting"));
        }
    }, &tr!("How Sonata draws its Dock, menu bar and windows")));
    let scale = prefs_write::get(IFACE, "text-scaling-factor").trim().parse::<f64>().unwrap_or(1.0);
    let sizes = vec![(1.0, tr!("Default")), (1.15, tr!("Large")), (1.3, tr!("Larger"))];
    disp.add(&combo_row(&tr!("Text size"), sizes, &crate::kit::nearest(&TEXT_SCALES, scale), |v: &f64| {
        // "1.0", as Python's str() wrote it
        prefs_write::set(IFACE, "text-scaling-factor", &format!("{v:?}"));
    }, ""));
    let ptr = group(&tr!("Pointer"), "");
    ptr.add(&pointer_size_row(ctx));
    vec![disp, ptr]
}

// -- About ---------------------------------------------------------------------------------------
pub fn page_about(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    about::page_about(ctx)
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

/// macOS' Privacy list, shown first in Apps (pages/apps.rs calls it): what
/// every app may use (system_privacy.rs).
#[allow(dead_code)] // (called only with the "system" feature)
pub fn permissions_group(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    #[cfg(feature = "system")]
    {
        vec![privacy::permissions_group(ctx)]
    }
    #[cfg(not(feature = "system"))]
    {
        let _ = ctx;
        Vec::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_shows_date_in_24_hours() {
        assert!(shows_date(CLOCK_DEFAULT));
        assert!(is_24h(CLOCK_DEFAULT));
    }

    /// Vini: Date & Time's switches were off with the date showing -- "Show
    /// the date" looked for "%d", the default format has "%-d".
    #[test]
    fn test_every_format_settings_writes_reads_back() {
        for date in [true, false] {
            for h24 in [true, false] {
                let f = crate::kit::clock_format(date, h24);
                assert_eq!((shows_date(&f), is_24h(&f)), (date, h24), "{f}");
            }
        }
    }

    #[test]
    fn test_other_spellings() {
        assert!(shows_date("%e %b %k:%M"));
        assert!(is_24h("%e %b %k:%M"));
        assert!(!shows_date("%a %-I:%M %p"));
        assert!(!is_24h("%a %-I:%M %p"));
        assert!(!shows_date(""));
    }

    #[test]
    fn notification_summaries() {
        let mut a = app_settings(None);
        assert_eq!(notif_summary(&a), tr!("Banners"));
        a.insert("style".into(), json!("none"));
        assert_eq!(notif_summary(&a), tr!("Notification Center only"));
        a.insert("center".into(), json!(false));
        assert_eq!(notif_summary(&a), tr!("None"));
        a.insert("allow".into(), json!(false));
        assert_eq!(notif_summary(&a), tr!("Off"));
        // stored values over the defaults, the rest kept
        let b = app_settings(Some(&json!({"name": "Mail", "badge": false})));
        assert_eq!((b["name"].as_str(), b["badge"].as_bool(), b["allow"].as_bool()), (Some("Mail"), Some(false), Some(true)));
    }

    #[test]
    fn pointer_and_text_sizes() {
        assert_eq!(nearest_size(&[24, 32, 48, 64], 30), 32);
        assert_eq!(nearest_size(&[24, 32, 48, 64], 100), 64);
        assert_eq!(crate::kit::nearest(&TEXT_SCALES, 1.25), 1.3);
        assert_eq!(format!("{:?}", 1.0f64), "1.0");
    }
}
