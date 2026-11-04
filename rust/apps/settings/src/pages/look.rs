//! Appearance, Dock, Desktop & Windows, Menu Bar, Control Center, Apps (the grid, Hidden & Protected Apps).
//!
//! What app.py kept on `self` between a page's rows and its timers (a
//! slider still saving, the Button order row, the Dock's folder rows, the
//! hidden apps' lock) is one [`Look`], kept by the window.
//!
//! Not ported: Settings' own window buttons moving with Window buttons /
//! Button order (`_place_lights`: the Rust window has none of its own yet).

#[path = "look_color.rs"]
mod look_color;

use crate::kit::{self, combo_row, group, show_quietly_combo, slider_row, switch_row};
use crate::window::{Ctx, Settings};
use adw::prelude::*;
use gtk::{gio, glib};
use look_color::ColorDot;
use serde_json::{json, Map, Value};
use sonata_backend::wfconfig;
use sonata_desktop::names;
use sonata_i18n::{fmt, gettext, n_, tr};
use sonata_tweaks::{flatpak_theme, frame, prefs_write, steamtheme, titlebars, trafficlights};
use std::cell::{Cell, RefCell};
use std::collections::BTreeSet;
use std::rc::{Rc, Weak};
use std::time::Duration;

const IFACE: &str = "org.gnome.desktop.interface";
const WM: &str = "org.gnome.desktop.wm.preferences";
const DBL: &str = "action-double-click-titlebar";

// -- the settings' defaults (icons.APPEARANCE_DEFAULTS, dock.DEFAULTS...) --------------------------
pub fn appearance_defaults() -> Value {
    json!({"flatpak_theme": true, "icon_theme": "Sonata", "theme": "mac", "accent": "blue", "reduce_transparency": false,
           "renderer": "gl", "system_titlebars": true, "menu_logo": "distro", "menu_text": "", "glass_titlebars": false,
           "screen_corners": true, "radius": {}, "glass": {}, "background": {}, "buttons_side": "left",
           "buttons_order": ["close", "minimize", "maximize"], "button_size": 12, "buttons_style": "color",
           "buttons_colors": {}, "steam_theme": true, "always_show_tabs": true})
}

fn appearance() -> Map<String, Value> {
    sonata_config::load("appearance", &appearance_defaults())
}

/// what Reset Appearance puts back (Vini: the theme's defaults); light/dark is
/// the system's setting and stays
pub const APPEARANCE_RESET: [&str; 15] = ["accent", "background", "theme", "icon_theme", "flatpak_theme", "system_titlebars", "glass_titlebars", "glass", "radius", "button_size", "buttons_side", "buttons_order", "buttons_style", "buttons_colors", "always_show_tabs"];

/// shell/dock.py DEFAULTS (the Dock's own: sonata_dock::dock::defaults, a test keeps them equal)
pub fn dock_defaults() -> Value {
    json!({"pinned": null, "icon_size": 48, "edge_gap": 4, "window_gap": 6, "glass": true,
           "indicators": true, "bounce": true, "minimize_effect": "genie", "click_minimizes": true,
           "magnification": false, "magnified_size": 80, "position": "bottom",
           "autohide": false, "autohide_delay_ms": 300, "show_recents": true,
           "recent": [], "stacks": null, "all_displays": false, "folders": {},
           "quit_on_close": true})
}

const DOCK_MIN: f64 = 16.0;
const DOCK_MAX: f64 = 128.0;

fn dock_cfg() -> Map<String, Value> {
    sonata_config::load("dock", &dock_defaults())
}

/// what Reset Dock puts back: options, never what's in the Dock
/// (pinned apps, folders, stacks, recents) nor the default browser
pub const DOCK_RESET: [&str; 11] = ["icon_size", "magnification", "magnified_size", "position", "autohide", "all_displays", "show_recents", "click_minimizes", "bounce", "indicators", "edge_gap"];

/// topbar_prefs.DEFAULTS: a show_ / _style key per performance figure of this machine.
pub fn topbar_defaults(kinds: &[String]) -> Value {
    let mut d = json!({"battery_percent": false, "clock_format": "%a %-d %b  %H:%M", "show_bluetooth": true,
                       "show_sound": false, "show_now_playing": false, "show_tray": true,
                       "show_wifi": true, "show_battery": true, "show_spotlight": true, "show_input": true,
                       "show_cpu": false, "show_gpu": false, "show_ram": false, "show_net": false, "show_fps": false,
                       "cpu_style": "text", "gpu_style": "text", "ram_style": "text", "net_style": "text",
                       "fps_style": "text", "autohide": false, "tall": true});
    let o = d.as_object_mut().expect("an object");
    for k in kinds {
        o.entry(format!("show_{k}")).or_insert(json!(false));
        o.entry(format!("{k}_style")).or_insert(json!("text"));
    }
    d
}

fn b(m: &Map<String, Value>, k: &str, default: bool) -> bool {
    m.get(k).and_then(Value::as_bool).unwrap_or(default)
}

fn s(m: &Map<String, Value>, k: &str, default: &str) -> String {
    m.get(k).and_then(Value::as_str).unwrap_or(default).to_string()
}

fn f(m: &Map<String, Value>, k: &str, default: f64) -> f64 {
    m.get(k).and_then(Value::as_f64).unwrap_or(default)
}

fn dark() -> bool {
    adw::StyleManager::default().is_dark()
}

fn labels(opts: &[(&str, &str)]) -> Vec<(String, String)> {
    opts.iter().map(|(v, l)| (v.to_string(), gettext(l))).collect()
}

/// system.set_dark_mode: Sonata and libadwaita / GTK 4 apps (color-scheme),
/// GTK 3 apps (the theme's name), Flatpak apps (gtkstyle.update). (Not in
/// sonata-backend yet.)
fn set_dark_mode(on: bool) {
    prefs_write::set(IFACE, "color-scheme", if on { "prefer-dark" } else { "default" });
    prefs_write::set(IFACE, "gtk-theme", if on { "Sonata-Dark" } else { "Sonata-Light" });
    flatpak_theme::apply();
}

// -- what the pages keep between rows and timers --------------------------------------------------
#[derive(Default)]
struct Look {
    bg_src: RefCell<Option<glib::SourceId>>,
    button_size_src: RefCell<Option<glib::SourceId>>,
    button_size_pending: Cell<i64>,
    button_custom_row: glib::WeakRef<adw::ActionRow>,
    order_slot: glib::WeakRef<gtk::Box>,
    order_dots: glib::WeakRef<gtk::Box>,
    order_default: glib::WeakRef<gtk::Button>,
    radius_src: RefCell<Option<glib::SourceId>>,
    radius_pending: RefCell<Map<String, Value>>,
    glass_src: RefCell<Option<glib::SourceId>>,
    glass_wf_src: RefCell<Option<glib::SourceId>>,
    glass_pending: RefCell<Map<String, Value>>,
    glass_sliders: RefCell<Vec<(&'static str, glib::WeakRef<adw::ActionRow>)>>,
    dock_folders: glib::WeakRef<adw::PreferencesGroup>,
    folder_rows: RefCell<Vec<adw::ActionRow>>,
    hidden_lock: glib::WeakRef<adw::PreferencesGroup>,
    hidden_groups: RefCell<Vec<adw::PreferencesGroup>>,
}

fn look(ctx: &Ctx) -> Rc<Look> {
    ctx.kept::<Look>("look").unwrap_or_else(|| {
        let l = Rc::new(Look::default());
        ctx.keep("look", l.clone());
        l
    })
}

fn cancel(slot: &RefCell<Option<glib::SourceId>>) {
    if let Some(id) = slot.borrow_mut().take() {
        id.remove();
    }
}

fn later(slot: &RefCell<Option<glib::SourceId>>, ms: u64, f: impl FnOnce() + 'static) {
    cancel(slot);
    *slot.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(ms), f));
}

/// Run with the window, if it is still open.
fn with(w: &Weak<Settings>, f: impl FnOnce(&Ctx)) {
    if let Some(ctx) = w.upgrade() {
        f(&ctx);
    }
}

/// A section's last row: everything on it back to the defaults (asked first).
fn reset_group(title: &str, subtitle: &str, ask: impl Fn() + 'static) -> adw::PreferencesGroup {
    let g = group("", "");
    let row = adw::ActionRow::builder().title(title).subtitle(subtitle).use_markup(false).build();
    let rb = sonata_ui::controls::push_button(&tr!("Reset\u{2026}"), ask, "destructive");
    rb.set_valign(gtk::Align::Center);
    row.add_suffix(&rb);
    g.add(&row);
    g
}

fn push_button(label: &str, f: impl Fn() + 'static) -> gtk::Button {
    let b = sonata_ui::controls::push_button(label, f, "");
    b.set_valign(gtk::Align::Center);
    b
}

// == Appearance ======================================================================================
/// Big Sur "Accent colour": a row of colour dots (live everywhere).
fn accent_row(ctx: &Ctx) -> adw::ActionRow {
    let row = adw::ActionRow::builder().title(tr!("Accent colour")).subtitle(tr!("Buttons, selections, switches and menus")).build();
    let bx = gtk::Box::builder().valign(gtk::Align::Center).build();
    let cur = s(&appearance(), "accent", "blue");
    let custom_now = sonata_ui::tokens::custom_accent(&cur);
    let accent_hex = custom_now.clone().unwrap_or_else(|| kit::ACCENTS.iter().find(|a| a.0 == cur).unwrap_or(&kit::ACCENTS[0]).1.to_string());
    let custom = ColorDot::new(&accent_hex, None);
    if custom_now.is_some() {
        custom.button.add_css_class("selected");
    }
    let buttons: Rc<RefCell<Vec<(&'static str, gtk::Button)>>> = Rc::default();
    let pick: Rc<dyn Fn(&str)> = {
        let (buttons, custom, w) = (buttons.clone(), custom.button.clone(), Rc::downgrade(ctx));
        Rc::new(move |name: &str| {
            for (n, b) in buttons.borrow().iter() {
                if *n == name { b.add_css_class("selected") } else { b.remove_css_class("selected") }
            }
            if sonata_ui::tokens::custom_accent(name).is_some() { custom.add_css_class("selected") } else { custom.remove_css_class("selected") }
            with(&w, |ctx| ctx.save("appearance", "accent", json!(name)));
        })
    };
    for (name, ..) in kit::ACCENTS {
        let mut cap = name.to_string();
        cap[..1].make_ascii_uppercase();
        let b = gtk::Button::builder().css_classes(["st-accent", name]).tooltip_text(gettext(&cap)).valign(gtk::Align::Center).build();
        if name == cur {
            b.add_css_class("selected");
        }
        let pick = pick.clone();
        b.connect_clicked(move |_| pick(name));
        buttons.borrow_mut().push((name, b.clone()));
        bx.append(&b);
    }
    // any colour (Vini): Sonata's own colour picker; the dot shows the colour picked
    let dot = custom.clone();
    custom.connect_pick(None, move |hexc| {
        dot.set_color(hexc);
        pick(hexc);
    });
    bx.append(&custom.button);
    row.add_suffix(&bx);
    row
}

/// Background colour of one appearance (Vini), like the accent colour:
/// a few to pick from -- the first is the theme's own -- and any colour
/// (Sonata's colour picker). Live everywhere: Sonata's windows, the title
/// bars Wayfire draws and other GTK apps.
fn background_row(ctx: &Ctx, mode: &'static str) -> adw::ActionRow {
    let title = if mode == "light" { tr!("Light background") } else { tr!("Dark background") };
    let sub = if mode == "light" { tr!("Windows, sidebars, menus and title bars in Light Mode") } else { tr!("Windows, sidebars, menus and title bars in Dark Mode") };
    let row = adw::ActionRow::builder().title(title.as_str()).subtitle(sub).build();
    let bx = gtk::Box::builder().valign(gtk::Align::Center).build();
    let opts = kit::BACKGROUNDS.iter().find(|(m, _)| *m == mode).map(|(_, o)| *o).unwrap_or(kit::BACKGROUNDS[0].1);
    let default = opts[0].1;
    let stored = appearance().get("background").and_then(|b| b.get(mode)).and_then(Value::as_str).map(str::to_string).unwrap_or_default();
    let cur = sonata_ui::tokens::custom_accent(&stored).unwrap_or_else(|| default.to_string());
    let custom = ColorDot::new(&cur, None);
    if !opts.iter().any(|(_, c)| *c == cur) {
        custom.button.add_css_class("selected");
    }
    let buttons: Rc<RefCell<Vec<(&'static str, gtk::Button)>>> = Rc::default();
    let pick: Rc<dyn Fn(&str)> = {
        let (buttons, custom, w, lk) = (buttons.clone(), custom.button.clone(), Rc::downgrade(ctx), look(ctx));
        Rc::new(move |color: &str| {
            let color = sonata_ui::tokens::custom_accent(color).unwrap_or_else(|| default.to_string());
            for (c, b) in buttons.borrow().iter() {
                if *c == color { b.add_css_class("selected") } else { b.remove_css_class("selected") }
            }
            if buttons.borrow().iter().any(|(c, _)| *c == color) { custom.remove_css_class("selected") } else { custom.add_css_class("selected") }
            let mut bg = appearance().get("background").and_then(Value::as_object).cloned().unwrap_or_default();
            if color == default {
                bg.remove(mode); // the theme's own again
            } else {
                bg.insert(mode.into(), json!(color));
            }
            with(&w, |ctx| ctx.save("appearance", "background", Value::Object(bg)));
            // the title bars Wayfire draws and other GTK apps: once the clicks
            // stop (each run rewrites their style files; quick clicks raced)
            let lk2 = lk.clone();
            later(&lk.bg_src, 400, move || {
                lk2.bg_src.borrow_mut().take();
                let d = dark();
                kit::run_async(move || {
                    titlebars::apply_colors(d);
                    titlebars::apply(None);
                }, |_| {});
            });
        })
    };
    for (i, (name, color)) in opts.iter().enumerate() {
        let b = gtk::Button::builder().tooltip_text(gettext(name)).valign(gtk::Align::Center).build();
        for c in ["st-accent".to_string(), "st-bg".to_string(), format!("bg-{mode}-{i}")] {
            b.add_css_class(&c);
        }
        if *color == cur {
            b.add_css_class("selected");
        }
        let (pick, color) = (pick.clone(), *color);
        b.connect_clicked(move |_| pick(color));
        buttons.borrow_mut().push((color, b.clone()));
        bx.append(&b);
    }
    let dot = custom.clone();
    custom.connect_pick(Some(title), move |hexc| {
        dot.set_color(hexc);
        pick(hexc);
    });
    bx.append(&custom.button);
    row.add_suffix(&bx);
    row
}

/// The icon themes installed (a folder with an index.theme), and Sonata's.
fn icon_themes() -> Vec<String> {
    let mut bases = glib::system_data_dirs();
    bases.push(glib::user_data_dir());
    let mut out: BTreeSet<String> = BTreeSet::from(["Sonata".to_string()]);
    for base in bases {
        let Ok(rd) = std::fs::read_dir(base.join("icons")) else { continue };
        for e in rd.flatten() {
            if e.path().join("index.theme").exists() {
                out.insert(e.file_name().to_string_lossy().into_owned());
            }
        }
    }
    out.into_iter().collect()
}

/// launchpad_window.STYLES: Apps opens full screen or as a panel.
const APPS_STYLES: [(&str, &str); 2] = [("fullscreen", n_!("Full Screen")), ("window", names::APPS_MENU)];

pub const BUTTON_STYLES: [(&str, &str); 4] = [("color", n_!("Colourful")), ("graphite", n_!("Graphite")), ("mono", n_!("Black & White")), ("custom", n_!("Custom"))];

/// Everything about how Sonata looks (macOS Appearance): light/dark and
/// accent, style and icons, title bars, glass, corners.
pub fn page_appearance(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let w = Rc::downgrade(ctx);
    let g = group(&tr!("Appearance"), "");
    let scheme = prefs_write::get(IFACE, "color-scheme");
    g.add(&combo_row(&tr!("Appearance"), vec![("default".to_string(), tr!("Light")), ("prefer-dark".to_string(), tr!("Dark"))], &(if scheme == "prefer-dark" { "prefer-dark" } else { "default" }).to_string(), |v: &String| {
        let on = v == "prefer-dark";
        kit::run_async(move || set_dark_mode(on), |_| {});
    }, &tr!("Linux setting: every app follows it")));
    g.add(&accent_row(ctx));
    g.add(&background_row(ctx, "light"));
    g.add(&background_row(ctx, "dark"));

    let st = group(&tr!("Style"), "");
    let app = appearance();
    let style_row: Rc<RefCell<Option<adw::ComboRow>>> = Rc::default();
    let row = {
        let (w, style_row) = (w.clone(), style_row.clone());
        combo_row(&tr!("Style"), vec![("mac".to_string(), "Sonata".to_string()), ("windows".to_string(), tr!("Windows 11 (coming later)"))], &s(&app, "theme", "mac"), move |v: &String| {
            with(&w, |ctx| ctx.save("appearance", "theme", json!("mac")));
            if v != "mac" {
                // not available yet: the pop-up goes back to what's in use
                let style_row = style_row.clone();
                glib::idle_add_local_once(move || {
                    if let Some(r) = style_row.borrow().as_ref() {
                        show_quietly_combo(r, &"mac".to_string());
                    }
                });
            }
        }, "")
    };
    *style_row.borrow_mut() = Some(row.clone());
    st.add(&row);
    // Vini: with the rest of the look
    let apps = gettext(names::APPS);
    let lw = sonata_config::load("launcher", &json!({"style": "fullscreen"}));
    let lw_style = Some(s(&lw, "style", "fullscreen")).filter(|v| APPS_STYLES.iter().any(|(k, _)| k == v)).unwrap_or_else(|| "fullscreen".into());
    st.add(&combo_row(&fmt(&tr!("{apps} style"), &[("apps", &apps)]), labels(&APPS_STYLES), &lw_style, |v: &String| {
        let _ = sonata_config::update("launcher", &json!({"style": v}));
    }, &fmt(&tr!("Full Screen or {apps_menu}: a panel by category"), &[("apps_menu", &gettext(names::APPS_MENU))])));
    let themes: Vec<(String, String)> = icon_themes().into_iter().map(|t| (t.clone(), t)).collect();
    let w2 = w.clone();
    st.add(&combo_row(&tr!("Icons"), themes, &s(&app, "icon_theme", "Sonata"), move |v: &String| {
        with(&w2, |ctx| {
            ctx.save("appearance", "icon_theme", json!(v));
            ctx.ask_restart("sonata", &tr!("The new icons"));
        });
    }, ""));
    let gen = adw::ActionRow::builder().title(tr!("App icons made by Sonata")).subtitle(tr!("Apps without Sonata artwork get their icon on the standard frame, saved on disk")).build();
    let w2 = w.clone();
    gen.add_suffix(&push_button(&tr!("Regenerate"), move || {
        sonata_ui::icons::clear_generated();
        sonata_backend::system::restart_sonata(&[]);
        with(&w2, |ctx| ctx.toast(&tr!("Making the app icons again…")));
    }));
    st.add(&gen);
    if sonata_backend::run::which("flatpak").is_some() {
        let w2 = w.clone();
        st.add(&switch_row(&tr!("Sonata style for Flatpak apps"), flatpak_theme::enabled(), move |on| {
            with(&w2, |ctx| {
                ctx.save("appearance", "flatpak_theme", json!(on));
                kit::run_async(move || if on { flatpak_theme::apply(); } else { flatpak_theme::remove() }, |_| {});
                ctx.toast(&tr!("Flatpak apps pick it up when they open again"));
            });
        }, &tr!("Also changes Flatpak apps in other desktops' sessions while on")));
    }

    let bars = group(&tr!("Title Bars"), "");
    let w2 = w.clone();
    bars.add(&combo_row(&tr!("Window buttons"), vec![("left".to_string(), tr!("Left")), ("right".to_string(), tr!("Right"))], &s(&app, "buttons_side", "left"), move |v: &String| {
        with(&w2, |ctx| set_buttons_side(ctx, v));
    }, &tr!("Where close, minimize and zoom sit on every window")));
    bars.add(&button_order_row(ctx));
    button_colour_rows(ctx, &bars, &app);
    let (lo, hi) = frame::BUTTON_SIZE_RANGE;
    let w2 = w.clone();
    let (size_row, _) = slider_row(&tr!("Button size"), frame::user_button_size() as f64, lo as f64, hi as f64, move |v| {
        with(&w2, |ctx| set_button_size(ctx, v));
    }, &tr!("Close, minimize and zoom on every window"), Some((&tr!("Small"), &tr!("Large"))), Some(frame::DOT as f64));
    bars.add(&size_row);
    let w2 = w.clone();
    bars.add(&switch_row(&tr!("Sonata title bars for all apps"), b(&app, "system_titlebars", true), move |on| {
        with(&w2, |ctx| {
            ctx.save("appearance", "system_titlebars", json!(on));
            kit::run_async(move || titlebars::apply(Some(on)), |_| {});
            ctx.toast(&tr!("Apps pick it up when they open again"));
        });
    }, &tr!("Chrome, VS Code and others use Sonata's title bar instead of their own")));
    let w2 = w.clone();
    bars.add(&switch_row(&tr!("Always show the tab bar"), b(&app, "always_show_tabs", true), move |on| {
        with(&w2, |ctx| ctx.save("appearance", "always_show_tabs", json!(on)));
    }, &tr!("Terminal, Files and TextEdit show their tabs and + even with one tab")));
    let w2 = w.clone();
    bars.add(&switch_row(&tr!("Sonata look for Steam"), b(&app, "steam_theme", true), move |on| {
        with(&w2, |ctx| set_steam_theme(ctx, on));
    }, &tr!("Steam keeps its own look, with Sonata's round window buttons and corners")));
    let w2 = w.clone();
    bars.add(&switch_row(&tr!("Glass title bars"), b(&app, "glass_titlebars", false), move |on| {
        with(&w2, |ctx| set_glass_titlebars(ctx, on));
    }, &tr!("See-through, blurred title bars on every window, GNOME apps too (experimental; heavier on the graphics card)")));
    let w2 = w.clone();
    let reset = reset_group(&tr!("Reset Appearance"), &tr!("Accent colour, style, icons, title bars, glass and corners back to the theme's defaults"), move || {
        with(&w2, ask_reset_appearance);
    });
    vec![g, st, bars, glass_group(ctx), corners_group(ctx), reset]
}

/// Settings > Appearance > Sonata look for Steam (steamtheme).
fn set_steam_theme(ctx: &Ctx, on: bool) {
    ctx.save("appearance", "steam_theme", json!(on));
    kit::run_async(move || if on { steamtheme::apply() } else { steamtheme::remove() }, |_| {});
    ctx.toast(&tr!("Steam shows it the next time it opens"));
}

/// Title bars of every window: the glass, or opaque (the default).
fn set_glass_titlebars(ctx: &Ctx, on: bool) {
    ctx.save("appearance", "glass_titlebars", json!(on));
    let d = dark();
    kit::run_async(move || {
        titlebars::apply(None); // GNOME apps (adwstyle)
        titlebars::apply_colors(d); // the bars Wayfire draws
    }, |_| {});
    ctx.toast(&tr!("Apps pick it up when they open again"));
}

/// Settings > Appearance > Button Colours (Vini): colourful, graphite,
/// black & white (black on light, white on dark) or a colour per button.
fn button_colour_rows(ctx: &Ctx, bars: &adw::PreferencesGroup, app: &Map<String, Value>) {
    let look_now = s(app, "buttons_style", "color");
    let w = Rc::downgrade(ctx);
    let shown = if trafficlights::STYLES.contains(&look_now.as_str()) { look_now.clone() } else { "color".into() };
    bars.add(&combo_row(&tr!("Button colours"), labels(&BUTTON_STYLES), &shown, move |v: &String| {
        with(&w, |ctx| set_button_style(ctx, v));
    }, &tr!("Close, minimize and zoom on every window")));
    let custom = adw::ActionRow::builder().title(tr!("Custom colours")).subtitle(tr!("Close, minimize, zoom")).visible(look_now == "custom").build();
    let bx = gtk::Box::builder().spacing(8).valign(gtk::Align::Center).build();
    let now = trafficlights::custom();
    for (name, tip) in [("close", tr!("Close")), ("minimize", tr!("Minimize")), ("maximize", tr!("Zoom"))] {
        let color = now.iter().find(|(n, _)| n == name).map(|(_, c)| c.clone()).unwrap_or_else(|| frame::tl_color(name).to_string());
        let dot = ColorDot::new(&color, Some(&tip));
        let (d2, w) = (dot.clone(), Rc::downgrade(ctx));
        dot.connect_pick(Some(fmt(&tr!("{name} Button"), &[("name", &tip)])), move |hexc| {
            d2.set_color(hexc);
            let mut cols = appearance().get("buttons_colors").and_then(Value::as_object).cloned().unwrap_or_default();
            cols.insert(name.into(), json!(hexc));
            with(&w, |ctx| ctx.save("appearance", "buttons_colors", Value::Object(cols)));
            let d = dark();
            kit::run_async(move || trafficlights::apply(d), |_| {});
        });
        bx.append(&dot.button);
    }
    custom.add_suffix(&bx);
    bars.add(&custom);
    look(ctx).button_custom_row.set(Some(&custom));
}

/// Saved a moment after the slider stops (like the corners): Sonata's
/// windows restyle; the pictures are drawn at that size and every place
/// told (pixdecor's title bars, GTK apps, Steam) with their new room.
fn set_button_size(ctx: &Ctx, value: f64) {
    let lk = look(ctx);
    lk.button_size_pending.set(value.round_ties_even() as i64);
    let (lk2, w) = (lk.clone(), Rc::downgrade(ctx));
    later(&lk.button_size_src, 250, move || {
        lk2.button_size_src.borrow_mut().take();
        with(&w, |ctx| ctx.save("appearance", "button_size", json!(lk2.button_size_pending.get())));
        let d = dark();
        kit::run_async(move || {
            trafficlights::apply(d); // the pictures at that size, pixdecor's too
            kit::apply_buttons_side(); // their room and spacing, GTK apps
        }, |_| {});
    });
}

fn set_button_style(ctx: &Ctx, style: &str) {
    ctx.save("appearance", "buttons_style", json!(style));
    if let Some(r) = look(ctx).button_custom_row.upgrade() {
        r.set_visible(style == "custom");
    }
    let d = dark();
    kit::run_async(move || trafficlights::apply(d), |_| {});
    ctx.toast(&tr!("Other apps pick it up when they open again"));
}

/// Settings > Appearance > Window buttons: left (macOS) or right. The
/// title bars Wayfire draws change at once; GTK apps follow GNOME's
/// button-layout; Sonata's apps place theirs when their windows open.
fn set_buttons_side(ctx: &Ctx, side: &str) {
    ctx.save("appearance", "buttons_side", json!(side));
    fill_button_order(ctx); // the order's picture, mirrored too
    kit::run_async(kit::apply_buttons_side, |_| {});
    ctx.toast(&tr!("Other open windows pick it up when they open again"));
}

/// Settings > Appearance > Button order (Vini): the three buttons as
/// windows show them, dragged to another place; Use Default puts back
/// macOS's (close, minimize, zoom from the edge).
fn button_order_row(ctx: &Ctx) -> adw::ActionRow {
    let row = adw::ActionRow::builder().title(tr!("Button order")).subtitle(tr!("Drag the buttons to change their order on every window")).build();
    let slot = gtk::Box::builder().valign(gtk::Align::Center).margin_end(6).build();
    row.add_suffix(&slot);
    let w = Rc::downgrade(ctx);
    let default = push_button(&tr!("Use Default"), move || {
        with(&w, |ctx| set_buttons_order(ctx, frame::BUTTONS.iter().map(|s| s.to_string()).collect()));
    });
    row.add_suffix(&default);
    let lk = look(ctx);
    lk.order_slot.set(Some(&slot));
    lk.order_dots.set(None);
    lk.order_default.set(Some(&default));
    fill_button_order(ctx);
    row
}

/// The Button order row's dots as they are now (its order and side).
fn fill_button_order(ctx: &Ctx) {
    let lk = look(ctx);
    let Some(slot) = lk.order_slot.upgrade() else { return };
    if let Some(d) = lk.order_dots.upgrade() {
        slot.remove(&d);
    }
    let f = frame::frame();
    let w = Rc::downgrade(ctx);
    let dots = order_lights(&f.buttons, f.buttons_side == "right", Rc::new(move |order| {
        with(&w, |ctx| set_buttons_order(ctx, order));
    }));
    slot.append(&dots);
    lk.order_dots.set(Some(&dots));
    if let Some(d) = lk.order_default.upgrade() {
        d.set_sensitive(f.buttons != frame::BUTTONS.iter().map(|s| s.to_string()).collect::<Vec<_>>());
    }
}

/// Button order: saved (from the window's edge inwards), then like
/// Window buttons -- the title bars Wayfire draws, GTK apps'
/// button-layout; open Sonata windows follow appearance.json.
fn set_buttons_order(ctx: &Ctx, order: Vec<String>) {
    let order = frame::buttons_order(Some(&json!(order)));
    ctx.save("appearance", "buttons_order", json!(order));
    // (after the drop: the dots that were dragged are still in their signal)
    let w = Rc::downgrade(ctx);
    glib::idle_add_local_once(move || with(&w, fill_button_order));
    kit::run_async(kit::apply_buttons_side, |_| {});
    ctx.toast(&tr!("Other open windows pick it up when they open again"));
}

/// `name` moved to the `index`-th place of the row as drawn (left to
/// right); the new order from the window's edge inwards, None if nothing
/// changed. `right`: the row is drawn mirrored.
pub fn moved_order(order: &[String], right: bool, name: &str, index: usize) -> Option<Vec<String>> {
    let screen: Vec<String> = if right { order.iter().rev().cloned().collect() } else { order.to_vec() };
    if !screen.iter().any(|n| n == name) {
        return None;
    }
    let mut names: Vec<String> = screen.into_iter().filter(|n| n != name).collect();
    names.insert(index.min(names.len()), name.to_string());
    let new: Vec<String> = if right { names.into_iter().rev().collect() } else { names };
    (new != order).then_some(new)
}

/// ui.window.order_lights: the three window buttons to reorder, shown as
/// windows show them -- `order` from the edge inwards, mirrored when
/// `right` -- and dragged to another place in the row. Only a picture of
/// the buttons: they close, minimize and zoom nothing.
fn order_lights(order: &[String], right: bool, on_change: Rc<dyn Fn(Vec<String>)>) -> gtk::Box {
    let order = order.to_vec();
    let screen: Vec<String> = if right { order.iter().rev().cloned().collect() } else { order.clone() };
    let bx = gtk::Box::builder().css_classes(["traffic", "traffic-order"]).valign(gtk::Align::Center).halign(gtk::Align::Center).build();
    for name in &screen {
        let (css, tip) = match name.as_str() {
            "close" => ("tl-close", tr!("Close")),
            "minimize" => ("tl-min", tr!("Minimize")),
            _ => ("tl-zoom", tr!("Zoom")),
        };
        let b = gtk::Button::builder().css_classes([css]).tooltip_text(tip).valign(gtk::Align::Center).focus_on_click(false).can_focus(false).build();
        b.set_cursor_from_name(Some("grab"));
        let src = gtk::DragSource::builder().actions(gtk::gdk::DragAction::MOVE).build();
        let n = name.clone();
        src.connect_prepare(move |_, _, _| Some(gtk::gdk::ContentProvider::for_value(&n.to_value())));
        let bw = b.downgrade();
        src.connect_drag_begin(move |s, _| {
            if let Some(w) = bw.upgrade() {
                s.set_icon(Some(&gtk::WidgetPaintable::new(Some(&w))), w.width() / 2, w.height() / 2);
            }
        });
        b.add_controller(src);
        bx.append(&b);
    }
    let target = gtk::DropTarget::new(glib::Type::STRING, gtk::gdk::DragAction::MOVE);
    let bw = bx.downgrade();
    target.connect_drop(move |_, value, x, _| {
        let (Ok(name), Some(bx)) = (value.get::<String>(), bw.upgrade()) else { return false };
        let Some(at) = screen.iter().position(|n| *n == name) else { return false };
        // where it goes: after every other dot whose centre is left of it
        let mut i = 0;
        let mut c = bx.first_child();
        while let Some(ch) = c {
            if ch.compute_bounds(&bx).is_some_and(|r| (r.x() + r.width() / 2.0) as f64 <= x) {
                i += 1;
            }
            c = ch.next_sibling();
        }
        if at < i {
            i -= 1; // (counted itself among the dots on its left)
        }
        if let Some(new) = moved_order(&order, right, &name, i) {
            on_change(new);
        }
        true
    });
    bx.add_controller(target);
    bx
}

fn ask_reset_appearance(ctx: &Ctx) {
    let w = Rc::downgrade(ctx);
    ctx.ask_reset(&tr!("Reset Appearance?"), &tr!("The accent and background colours, style, icons, title bars, glass and corners go back to the theme's defaults."), move || {
        with(&w, reset_appearance);
    });
}

fn reset_appearance(ctx: &Ctx) {
    let lk = look(ctx);
    for src in [&lk.glass_src, &lk.glass_wf_src, &lk.radius_src, &lk.button_size_src] {
        cancel(src); // a slider still saving
    }
    lk.glass_pending.borrow_mut().clear();
    lk.radius_pending.borrow_mut().clear();
    let old = appearance();
    let defaults = appearance_defaults();
    for k in APPEARANCE_RESET {
        ctx.save("appearance", k, defaults[k].clone());
    }
    let d = dark();
    let flatpak = old.get("flatpak_theme") != defaults.get("flatpak_theme");
    let buttons = s(&old, "buttons_style", "color") != "color" || old.get("button_size").and_then(Value::as_f64).unwrap_or(frame::DOT as f64) != frame::DOT as f64;
    kit::run_async(move || {
        wayfire_corners();
        kit::apply_buttons_side(); // the buttons' spacing and room, GTK apps, GNOME apps' corners
        titlebars::apply(Some(true));
        titlebars::apply_colors(d);
        if flatpak {
            flatpak_theme::apply();
        }
        if buttons {
            trafficlights::apply(d); // Steam's and GTK apps' buttons too
        }
    }, |_| {});
    ctx.rebuild_page("appearance");
    if old.get("icon_theme") != defaults.get("icon_theme") {
        ctx.ask_restart("sonata", &tr!("Sonata's icons"));
    } else {
        ctx.toast(&tr!("Appearance reset"));
    }
}

// -- glass, per part (ui/glass.py) ------------------------------------------------------------------
/// shown translated (glass.TITLES / SUBTITLES)
const GLASS_TITLES: [(&str, &str, &str); 5] = [
    ("dock", n_!("Dock"), ""),
    ("menubar", n_!("Menu bar"), ""),
    ("menus", n_!("Menus and panels"), n_!("Menu bar menus, Control Center, Wi-Fi, notifications panels")),
    ("windows", n_!("Windows"), n_!("Sidebars, and title bars when Glass title bars is on")),
    ("launchpad", n_!("Apps Menu"), n_!("The apps panel when Apps > Style is Apps Menu")),
];
/// most see-through: clear glass (Vini)
pub const ALPHA_RANGE: (f64, f64) = (0.0, 0.97);
pub const BLUR_DEFAULT: i64 = 50;
/// while a slider moves: saved this often (every Sonata surface follows live)
const GLASS_LIVE_MS: u64 = 120;
/// Wayfire's part (blur rule, strength, threshold): once it stops
const GLASS_WAYFIRE_MS: u64 = 300;

pub fn clamp_alpha(a: f64) -> f64 {
    (a.clamp(ALPHA_RANGE.0, ALPHA_RANGE.1) * 1000.0).round_ties_even() / 1000.0
}

pub fn alpha_to_slider(alpha: f64) -> f64 {
    let (lo, hi) = ALPHA_RANGE;
    ((hi - alpha.clamp(lo, hi)) / (hi - lo) * 1000.0).round_ties_even() / 10.0
}

pub fn slider_to_alpha(v: f64) -> f64 {
    let (lo, hi) = ALPHA_RANGE;
    clamp_alpha(hi - (hi - lo) * v / 100.0)
}

/// glass.css_alpha: a theme colour's own alpha ('rgba(...)'; else opaque).
pub fn css_alpha(css: &str) -> f64 {
    let css = css.trim();
    css.strip_prefix("rgba(").and_then(|r| r.strip_suffix(')')).and_then(|r| r.split(',').nth(3)).and_then(|a| a.trim().parse().ok()).unwrap_or(1.0)
}

/// The theme's glass colour of a part (glass.MATERIALS' first).
fn glass_token(item: &str) -> &'static str {
    match item {
        "menubar" => "bar_bg",
        "windows" => "window_glass",
        _ => "glass_tint",
    }
}

/// The glass setting saved after a slider moved or a switch flipped:
/// `pending` ({part: {"on", "alpha"}}, "blur") over what's stored (`raw`),
/// a part's "on" read as it is now (`on_now`) when the file doesn't say.
pub fn merge_glass(raw: &Map<String, Value>, on_now: &dyn Fn(&str) -> bool, pending: &Map<String, Value>) -> Map<String, Value> {
    let mut raw = raw.clone();
    for (k, v) in pending {
        if k == "blur" {
            raw.insert("blur".into(), v.clone());
            continue;
        }
        let mut part = Map::new();
        part.insert("on".into(), json!(on_now(k)));
        if let Some(stored) = raw.get(k).and_then(Value::as_object) {
            part.extend(stored.clone());
        }
        if let Some(v) = v.as_object() {
            part.extend(v.clone());
        }
        raw.insert(k.clone(), Value::Object(part));
    }
    raw
}

fn glass_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let cur = sonata_ui::glass::settings();
    let vals = sonata_ui::theme::values();
    let app = appearance();
    let reduce = b(&app, "reduce_transparency", false);
    let desc = tr!("Frosted, see-through backgrounds. Off: solid.") + &(if reduce { " ".to_string() + &tr!("Accessibility > Reduce transparency is on: everything is solid now.") } else { " ".into() });
    let g = group(&tr!("Glass & Transparency"), &desc);
    let lk = look(ctx);
    lk.glass_sliders.borrow_mut().clear();
    for (item, title, sub) in GLASS_TITLES {
        let part = cur.iter().find(|(i, _)| *i == item).map(|(_, p)| *p).unwrap_or(sonata_ui::glass::Part { on: true, alpha: None });
        let theme_alpha = css_alpha(vals.get(glass_token(item)).map(String::as_str).unwrap_or(""));
        let w = Rc::downgrade(ctx);
        let sw = switch_row(&gettext(title), part.on, move |on| with(&w, |ctx| set_glass(ctx, item, Some(on), None, None)), &(if sub.is_empty() { String::new() } else { gettext(sub) }));
        let w = Rc::downgrade(ctx);
        let (sl, _) = slider_row(&tr!("Transparency"), alpha_to_slider(part.alpha.unwrap_or(theme_alpha)), 0.0, 100.0, move |v| {
            with(&w, |ctx| set_glass(ctx, item, None, Some(slider_to_alpha(v)), None));
        }, "", Some((&tr!("Less"), &tr!("More"))), Some(alpha_to_slider(theme_alpha)));
        sl.add_css_class("st-sub-row");
        sl.set_sensitive(part.on);
        lk.glass_sliders.borrow_mut().push((item, sl.downgrade()));
        g.add(&sw);
        g.add(&sl);
    }
    let blur = app.get("glass").and_then(|g| g.get("blur")).and_then(Value::as_f64).map(|b| b.clamp(0.0, 100.0).trunc() as i64).unwrap_or(BLUR_DEFAULT);
    let w = Rc::downgrade(ctx);
    let (row, _) = slider_row(&tr!("Blur strength"), blur as f64, 0.0, 100.0, move |v| {
        with(&w, |ctx| set_glass(ctx, "blur", None, None, Some(v)));
    }, &tr!("How frosted every glass part is (one for all: Wayfire's blur)"), Some((&tr!("Light"), &tr!("Strong"))), Some(BLUR_DEFAULT as f64));
    g.add(&row);
    g
}

/// A switch saves at once. A moving slider saves every GLASS_LIVE_MS
/// (a live preview: it used to wait for the slider to stop, so dragging
/// seemed to do nothing); Wayfire's settings follow once it stops.
fn set_glass(ctx: &Ctx, item: &'static str, on: Option<bool>, alpha: Option<f64>, blur: Option<f64>) {
    let lk = look(ctx);
    {
        let mut pend = lk.glass_pending.borrow_mut();
        if item == "blur" {
            pend.insert("blur".into(), json!(blur.unwrap_or(BLUR_DEFAULT as f64).round_ties_even() as i64));
        } else {
            let part = pend.entry(item).or_insert_with(|| json!({}));
            if let Some(p) = part.as_object_mut() {
                if let Some(on) = on {
                    p.insert("on".into(), json!(on));
                }
                if let Some(a) = alpha {
                    p.insert("alpha".into(), json!(a));
                }
            }
        }
    }
    if let Some(on) = on {
        if let Some(row) = lk.glass_sliders.borrow().iter().find(|(i, _)| *i == item).and_then(|(_, r)| r.upgrade()) {
            row.set_sensitive(on);
        }
    }
    let save = {
        let (lk, w) = (lk.clone(), Rc::downgrade(ctx));
        move || {
            lk.glass_src.borrow_mut().take();
            let pending = std::mem::take(&mut *lk.glass_pending.borrow_mut());
            if pending.is_empty() {
                return;
            }
            let raw = appearance().get("glass").and_then(Value::as_object).cloned().unwrap_or_default();
            let cur = sonata_ui::glass::settings();
            let on_now = |k: &str| cur.iter().find(|(i, _)| *i == k).is_none_or(|(_, p)| p.on);
            let merged = merge_glass(&raw, &on_now, &pending);
            with(&w, |ctx| ctx.save("appearance", "glass", Value::Object(merged)));
        }
    };
    let wayfire = {
        let lk = lk.clone();
        move || {
            lk.glass_wf_src.borrow_mut().take();
            let d = dark();
            kit::run_async(move || titlebars::apply_colors(d), |_| {});
        }
    };
    cancel(&lk.glass_wf_src);
    if on.is_some() {
        cancel(&lk.glass_src);
        save();
        wayfire();
        return;
    }
    if lk.glass_src.borrow().is_none() {
        // throttle, not debounce: live while dragging
        *lk.glass_src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(GLASS_LIVE_MS), save));
    }
    *lk.glass_wf_src.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(GLASS_WAYFIRE_MS), wayfire));
}

// -- corners (tokens.user_radii) ---------------------------------------------------------------------
/// (key, the theme's, (lowest, highest))
pub const RADII: [(&str, i64, (i64, i64)); 3] = [("window", frame::RADIUS, (0, 24)), ("dock", 18, (0, 32)), ("menu", 7, (0, 16))];

/// The radii in effect: appearance.json's, else the theme's (the window's:
/// FRAME's while not picked).
pub fn user_radii(app: &Map<String, Value>) -> Vec<(&'static str, i64)> {
    let got = app.get("radius").and_then(Value::as_object);
    RADII.iter().map(|(k, def, (lo, hi))| (*k, got.and_then(|g| g.get(*k)).and_then(Value::as_f64).map(|v| (v.round_ties_even() as i64).clamp(*lo, *hi)).unwrap_or(*def))).collect()
}

/// The corners of the title bars Wayfire draws, and the buttons' room from
/// them (frame.corner_extra): wfconfig.frame_options' corner keys.
fn wayfire_corners() {
    let f = frame::frame();
    let inset = f.dot_left - f.dot as f64 / 2.0;
    let offset = if f.buttons_side == "left" { inset.round_ties_even() } else { -inset.round_ties_even() };
    wfconfig::set("pixdecor", "rounded_corner_radius", &f.radius.to_string());
    wfconfig::set("sonata-corners", "radius", &f.radius.to_string());
    wfconfig::set("pixdecor", &format!("{}_button_x_offset", f.buttons_side), &format!("{}", offset as i64));
}

/// Corner radii: one slider per kind; a double-click resets it.
fn corners_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let g = group(&tr!("Corners"), &tr!("How round Sonata's windows, Dock and menus are (not the screen's corners)."));
    let cur = user_radii(&appearance());
    for ((key, title, sub), (_, def, (lo, hi))) in [("window", tr!("Windows"), tr!("Their corners and title bars, other apps' too")), ("dock", "Dock".to_string(), String::new()), ("menu", tr!("Menus and panels"), tr!("Menus, the menu bar's panels, notifications, alerts"))].into_iter().zip(RADII) {
        let now = cur.iter().find(|(k, _)| *k == key).map(|(_, v)| *v).unwrap_or(def);
        let w = Rc::downgrade(ctx);
        let (row, _) = slider_row(&title, now as f64, lo as f64, hi as f64, move |v| with(&w, |ctx| set_radius(ctx, key, v)), &sub, Some((&tr!("Square"), &tr!("Round"))), Some(def as f64));
        g.add(&row);
    }
    g
}

/// Saved a moment after the slider stops (each save re-styles every Sonata
/// surface); the title bars Wayfire draws and GNOME apps' follow.
fn set_radius(ctx: &Ctx, key: &str, value: f64) {
    let lk = look(ctx);
    lk.radius_pending.borrow_mut().insert(key.into(), json!(value.round_ties_even() as i64));
    let (lk2, w) = (lk.clone(), Rc::downgrade(ctx));
    later(&lk.radius_src, 250, move || {
        lk2.radius_src.borrow_mut().take();
        let mut radii: Map<String, Value> = user_radii(&appearance()).into_iter().map(|(k, v)| (k.to_string(), json!(v))).collect();
        radii.extend(std::mem::take(&mut *lk2.radius_pending.borrow_mut()));
        with(&w, |ctx| ctx.save("appearance", "radius", Value::Object(radii)));
        kit::run_async(|| {
            wayfire_corners();
            titlebars::apply(None); // GNOME apps' corners (adwstyle)
        }, |_| {});
    });
}

// == Dock ============================================================================================
pub fn page_dock(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let cfg = dock_cfg();
    let def = dock_defaults();
    let w = Rc::downgrade(ctx);
    let sw = |key: &'static str| {
        let w = w.clone();
        move |on: bool| with(&w, |ctx| ctx.save("dock", key, json!(on)))
    };
    let live = |key: &'static str| {
        let w = w.clone();
        move |v: f64| with(&w, |ctx| ctx.save_live("dock", key, json!(v as i64)))
    };
    let size = group("Dock", "");
    size.add(&slider_row(&tr!("Size"), f(&cfg, "icon_size", 48.0), DOCK_MIN, DOCK_MAX, live("icon_size"), "", None, def["icon_size"].as_f64()).0);
    size.add(&switch_row(&tr!("Magnification"), b(&cfg, "magnification", false), sw("magnification"), ""));
    size.add(&slider_row(&tr!("Magnified size"), f(&cfg, "magnified_size", 80.0), DOCK_MIN, DOCK_MAX, live("magnified_size"), "", None, def["magnified_size"].as_f64()).0);
    let w2 = w.clone();
    size.add(&combo_row(&tr!("Position on screen"), vec![("left".to_string(), tr!("Left")), ("bottom".to_string(), tr!("Bottom")), ("right".to_string(), tr!("Right"))], &s(&cfg, "position", "bottom"), move |v: &String| {
        with(&w2, |ctx| ctx.save("dock", "position", json!(v)));
    }, ""));
    let behave = group("", "");
    behave.add(&switch_row(&tr!("Automatically hide and show the Dock"), b(&cfg, "autohide", false), sw("autohide"), ""));
    behave.add(&switch_row(&tr!("Show the Dock on every display"), b(&cfg, "all_displays", false), sw("all_displays"), &tr!("Otherwise only on the main display (Displays)")));
    behave.add(&switch_row(&tr!("Show suggested and recent apps in Dock"), b(&cfg, "show_recents", true), sw("show_recents"), ""));
    behave.add(&switch_row(&tr!("Click an app in front to minimize it"), b(&cfg, "click_minimizes", true), sw("click_minimizes"), ""));
    behave.add(&switch_row(&tr!("Animate opening applications"), b(&cfg, "bounce", true), sw("bounce"), ""));
    behave.add(&switch_row(&tr!("Show indicators for open applications"), b(&cfg, "indicators", true), sw("indicators"), ""));
    let lookg = group(&tr!("Look"), "");
    // (its glass: Appearance > Glass & Transparency)
    // and from zoomed windows
    lookg.add(&slider_row(&tr!("Distance from the screen edge"), f(&cfg, "edge_gap", 4.0), 0.0, 24.0, live("edge_gap"), "", None, def["edge_gap"].as_f64()).0);
    let folders = group(&tr!("Folders in the Dock"), &tr!("Right of the divider, next to the Trash"));
    let lk = look(ctx);
    lk.dock_folders.set(Some(&folders));
    lk.folder_rows.borrow_mut().clear();
    fill_dock_folders(ctx);
    let w2 = w.clone();
    let reset = reset_group(&tr!("Reset Dock"), &tr!("The Dock's options back to the defaults; your apps and folders in the Dock stay"), move || {
        with(&w2, |ctx| {
            let w = Rc::downgrade(ctx);
            ctx.ask_reset(&tr!("Reset Dock?"), &tr!("The Dock's options go back to the defaults. The apps and folders in your Dock stay."), move || with(&w, reset_dock));
        });
    });
    vec![size, behave, lookg, folders, reset]
}

fn reset_dock(ctx: &Ctx) {
    let def = dock_defaults();
    for k in DOCK_RESET {
        ctx.save("dock", k, def[k].clone());
    }
    ctx.rebuild_page("dock");
    ctx.toast(&tr!("Dock reset"));
}

// -- Dock > folders (stacks): a folder removed from the Dock comes back here (Vini) ------------------
fn downloads() -> std::path::PathBuf {
    glib::user_special_dir(glib::UserDirectory::Downloads).unwrap_or_else(|| glib::home_dir().join("Downloads"))
}

/// dock_stack.STACK_DEFAULTS with `path`.
pub fn stack_spec(path: &str) -> Value {
    json!({"display": "folder", "view": "grid", "sort": "added", "path": path})
}

/// The Dock's folders: as stored (those with a path), or -- never set --
/// Downloads (dock_stack.default_stacks).
pub fn dock_stacks_from(stored: Option<&Value>, downloads: &std::path::Path) -> Vec<Value> {
    match stored.and_then(Value::as_array) {
        Some(list) => list.iter().filter(|s| s.get("path").and_then(Value::as_str).is_some_and(|p| !p.is_empty())).cloned().collect(),
        None if downloads.is_dir() => vec![stack_spec(&downloads.to_string_lossy())],
        None => Vec::new(),
    }
}

fn dock_stacks() -> Vec<Value> {
    dock_stacks_from(dock_cfg().get("stacks"), &downloads())
}

fn path_of(spec: &Value) -> String {
    spec.get("path").and_then(Value::as_str).unwrap_or("").to_string()
}

/// The folders with `path` added (once; a folder that's there).
pub fn with_folder(stacks: &[Value], path: &str) -> Option<Vec<Value>> {
    if !std::path::Path::new(path).is_dir() || stacks.iter().any(|s| path_of(s) == path) {
        return None;
    }
    let mut out = stacks.to_vec();
    out.push(stack_spec(path));
    Some(out)
}

fn set_dock_stacks(ctx: &Ctx, stacks: Vec<Value>) {
    ctx.save("dock", "stacks", Value::Array(stacks));
    fill_dock_folders(ctx);
}

fn add_dock_folder(ctx: &Ctx, path: &str) {
    if let Some(stacks) = with_folder(&dock_stacks(), path) {
        set_dock_stacks(ctx, stacks);
    }
}

fn fill_dock_folders(ctx: &Ctx) {
    let lk = look(ctx);
    let Some(g) = lk.dock_folders.upgrade() else { return };
    for row in lk.folder_rows.borrow_mut().drain(..) {
        g.remove(&row);
    }
    let stacks = dock_stacks();
    let add_row = |row: adw::ActionRow| {
        g.add(&row);
        lk.folder_rows.borrow_mut().push(row);
    };
    for spec in &stacks {
        let path = path_of(spec);
        let title = std::path::Path::new(path.trim_end_matches('/')).file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| path.clone());
        let row = adw::ActionRow::builder().title(title).subtitle(path.as_str()).use_markup(false).build();
        row.add_prefix(&gtk::Image::from_icon_name("folder-symbolic"));
        let w = Rc::downgrade(ctx);
        row.add_suffix(&push_button(&tr!("Remove"), move || {
            let p = path.clone();
            with(&w, |ctx| set_dock_stacks(ctx, dock_stacks().into_iter().filter(|s| path_of(s) != p).collect()));
        }));
        add_row(row);
    }
    let down = downloads();
    let down_s = down.to_string_lossy().into_owned();
    if down.is_dir() && stacks.iter().all(|s| path_of(s) != down_s) {
        let row = adw::ActionRow::builder().title(tr!("Downloads")).subtitle(tr!("Not in the Dock")).use_markup(false).build();
        row.add_prefix(&gtk::Image::from_icon_name("folder-download-symbolic"));
        let w = Rc::downgrade(ctx);
        row.add_suffix(&push_button(&tr!("Add to Dock"), move || with(&w, |ctx| add_dock_folder(ctx, &down_s))));
        add_row(row);
    }
    let row = adw::ActionRow::builder().title(tr!("Another folder")).subtitle(tr!("Its items one click away, like Downloads")).use_markup(false).build();
    let w = Rc::downgrade(ctx);
    row.add_suffix(&push_button(&tr!("Add Folder\u{2026}"), move || with(&w, pick_dock_folder)));
    add_row(row);
}

fn pick_dock_folder(ctx: &Ctx) {
    let dlg = gtk::FileDialog::builder().title(tr!("Add a Folder to the Dock")).modal(true).build();
    let w = Rc::downgrade(ctx);
    dlg.select_folder(Some(&ctx.window), None::<&gio::Cancellable>, move |res| {
        let Some(path) = res.ok().and_then(|f| f.path()) else { return };
        with(&w, |ctx| add_dock_folder(ctx, &path.to_string_lossy()));
    });
}

// == Desktop & Windows ===============================================================================
/// shell/desktop.SORTS: the desktop's Sort By (and Settings)
const DESKTOP_SORTS: [(&str, &str); 4] = [("none", n_!("None")), ("name", n_!("Name")), ("kind", n_!("Kind")), ("date", n_!("Date Modified"))];

/// Desktop & Windows: how windows behave, the desktop's icons.
pub fn page_desktop(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let cfg = dock_cfg();
    let w = Rc::downgrade(ctx);
    let wins = group(&tr!("Windows"), "");
    let live = wfconfig::get("sonata-resize", "live", "true");
    let live = (if live.is_empty() { "true".into() } else { live }).to_lowercase() != "false";
    wins.add(&switch_row(&tr!("Show window contents while resizing"), live, |on| {
        kit::run_async(move || wfconfig::set("sonata-resize", "live", if on { "true" } else { "false" }), |_| {});
    }, &tr!("Off: only the window's background follows the pointer, and its contents fade in when you let go -- smoother with heavy apps")));
    let w2 = w.clone();
    wins.add(&switch_row(&tr!("Quit apps when their last window closes"), b(&cfg, "quit_on_close", true), move |on| {
        with(&w2, |ctx| ctx.save("dock", "quit_on_close", json!(on)));
    }, &tr!("Closing an app's last window quits it, also Steam, Discord and others that keep running in the background")));
    let w2 = w.clone();
    wins.add(&combo_row(&tr!("Minimize windows using"), vec![("genie".to_string(), tr!("Genie effect")), ("scale".to_string(), tr!("Scale effect"))], &s(&cfg, "minimize_effect", "genie"), move |v: &String| {
        with(&w2, |ctx| ctx.save("dock", "minimize_effect", json!(v)));
        let anim = kit::minimize_animation(v);
        kit::run_async(move || wfconfig::set("animate", "minimize_animation", anim), |_| {});
    }, ""));
    let dbl = prefs_write::get(WM, DBL);
    let dbl = if ["toggle-maximize", "minimize", "none"].contains(&dbl.as_str()) { dbl } else { "toggle-maximize".into() };
    wins.add(&combo_row(&tr!("Double-click a window's title bar to"), vec![("toggle-maximize".to_string(), tr!("Zoom")), ("minimize".to_string(), tr!("Minimize")), ("none".to_string(), tr!("Do Nothing"))], &dbl, |v: &String| {
        // GTK's title bars (the setting) and the top edge of every window
        // (sonata-resize: a double-click there used to resize it a little)
        prefs_write::set(WM, DBL, v);
        let v = v.clone();
        kit::run_async(move || wfconfig::set("sonata-resize", "double_click", &v), |_| {});
    }, ""));
    let desk = group(&tr!("Desktop"), "");
    let sort = s(&sonata_config::load("desktop", &json!({"positions": {}, "sort": "none"})), "sort", "none");
    let w2 = w.clone();
    desk.add(&combo_row(&tr!("Sort icons by"), labels(&DESKTOP_SORTS), &sort, move |v: &String| {
        with(&w2, |ctx| ctx.save("desktop", "sort", json!(v)));
    }, &tr!("None: icons stay where you put them")));
    let reset = reset_group(&tr!("Reset Desktop & Windows"), &tr!("Window options back to the defaults; your desktop icons stay where they are"), move || {
        with(&w, |ctx| {
            let w = Rc::downgrade(ctx);
            ctx.ask_reset(&tr!("Reset Desktop & Windows?"), &tr!("Window options go back to the defaults. Your desktop icons stay where they are."), move || with(&w, reset_desktop));
        });
    });
    vec![wins, desk, reset]
}

/// What Reset Desktop & Windows writes to Wayfire: the minimize effect,
/// and the windows' top edge, which follows the title bar (it kept
/// minimizing after a reset), and live resizing.
pub fn desktop_reset_options() -> Vec<(&'static str, &'static str, String)> {
    let effect = dock_defaults()["minimize_effect"].as_str().unwrap_or("genie").to_string();
    vec![
        ("animate", "minimize_animation", kit::minimize_animation(&effect).to_string()),
        ("sonata-resize", "double_click", "toggle-maximize".into()),
        ("sonata-resize", "live", "true".into()),
    ]
}

fn reset_desktop(ctx: &Ctx) {
    let def = dock_defaults();
    ctx.save("dock", "minimize_effect", def["minimize_effect"].clone());
    ctx.save("dock", "quit_on_close", def["quit_on_close"].clone());
    kit::run_async(|| {
        prefs_write::set(WM, DBL, "toggle-maximize");
        for (sec, k, v) in desktop_reset_options() {
            wfconfig::set(sec, k, &v);
        }
    }, |_| {});
    ctx.rebuild_page("desktop");
    ctx.toast(&tr!("Desktop & Windows reset"));
}

// == Menu Bar ========================================================================================
/// This machine's performance figures (stats.KINDS: cpu, gpu or gpu_<card>
/// for each card, vram..., temp_cpu, temp_gpu..., ram, net, fps).
fn stat_kinds() -> (Vec<sonata_backend::stats::Card>, Vec<String>) {
    let sys = std::path::Path::new("/sys");
    let cards = sonata_backend::stats::gpu_cards(sys);
    let kinds = sonata_backend::stats::kinds(&cards, &sonata_backend::stats::cpu_temp_path(sys));
    (cards, kinds)
}

/// A figure's name, translated (statsui.TITLES; a card's with its maker).
pub fn stat_title(cards: &[sonata_backend::stats::Card], kind: &str) -> String {
    use sonata_backend::stats as S;
    let maker_of = |key: &str| cards.iter().find(|c| c.key == key).map(|c| c.maker.clone()).unwrap_or_default();
    let maker = match kind.strip_prefix("gpu_") {
        Some(key) if cards.len() >= 2 => maker_of(key),
        _ => {
            let (vk, tk) = (S::vram_kinds(cards), S::temp_kinds(cards));
            S::key_of(&vk, kind).or_else(|| S::key_of(&tk, kind)).map(|k| maker_of(k)).unwrap_or_default()
        }
    };
    match kind {
        "cpu" => tr!("CPU"),
        "gpu" => tr!("GPU"),
        "ram" => tr!("Memory"),
        "net" => tr!("Network"),
        "fps" => tr!("FPS"),
        "temp_cpu" => tr!("CPU Temperature"),
        "temp_gpu" => tr!("GPU Temperature"),
        "vram" => tr!("Video Memory"),
        k if k.starts_with("gpu_") => fmt(&tr!("GPU ({maker})"), &[("maker", &maker)]),
        k if k.starts_with("temp_") => fmt(&tr!("GPU Temperature ({maker})"), &[("maker", &maker)]),
        k if k.starts_with("vram_") => fmt(&tr!("Video Memory ({maker})"), &[("maker", &maker)]),
        _ => kind.to_string(),
    }
}

/// ui/logo.py: the menu bar logo's choices [(value, label msgid)].
const LOGOS: [(&str, &str); 20] = [
    ("distro", n_!("Distribution logo")), ("distro-colour", n_!("Distribution logo, in colour")), ("sonata", "Sonata"),
    ("shape:circle", n_!("Circle")), ("shape:square", n_!("Square")), ("shape:triangle", n_!("Triangle")),
    ("shape:diamond", n_!("Diamond")), ("shape:hexagon", n_!("Hexagon")), ("shape:star", n_!("Star")), ("shape:ring", n_!("Ring")),
    ("icon:starred-symbolic", n_!("Star (outline)")), ("icon:emblem-favorite-symbolic", n_!("Heart")),
    ("icon:weather-clear-symbolic", n_!("Sun")), ("icon:weather-clear-night-symbolic", n_!("Moon")),
    ("icon:audio-x-generic-symbolic", n_!("Music")), ("icon:applications-games-symbolic", n_!("Game controller")),
    ("icon:utilities-terminal-symbolic", n_!("Terminal")), ("icon:face-smile-symbolic", n_!("Smile")),
    ("text:sonata", n_!("Text: Sonata")), ("text:user", n_!("Text: your name")),
];
const LOGO_CUSTOM: (&str, &str) = ("text:custom", n_!("Text: custom…"));
/// characters of custom text (logo.TEXT_MAX)
const LOGO_TEXT_MAX: i32 = 24;

/// The menu bar's height is set when it starts: it starts again (~1 s).
fn set_tall_menubar(ctx: &Ctx, on: bool) {
    ctx.save("topbar", "tall", json!(on));
    kit::run_async(|| sonata_backend::system::restart_sonata(&["topbar"]), |_| {});
}

pub fn page_menubar(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let (cards, kinds) = stat_kinds();
    let defaults = topbar_defaults(&kinds);
    let cfg = sonata_config::load("topbar", &defaults);
    let w = Rc::downgrade(ctx);
    let g = group(&tr!("Menu Bar"), "");
    let w2 = w.clone();
    g.add(&switch_row(&tr!("Automatically hide and show the menu bar"), b(&cfg, "autohide", false), move |on| {
        with(&w2, |ctx| ctx.save("topbar", "autohide", json!(on)));
    }, &tr!("It slides back down when the pointer reaches the top of the screen")));
    let w2 = w.clone();
    g.add(&switch_row(&tr!("Taller menu bar"), b(&cfg, "tall", true), move |on| with(&w2, |ctx| set_tall_menubar(ctx, on)), &tr!("Like the menu bar of the newest MacBooks")));
    let clocks = vec![
        ("%a %-d %b  %H:%M".to_string(), tr!("Mon 28 Sep  21:41")),
        ("%a %H:%M".to_string(), tr!("Mon 21:41")),
        ("%a %-d %b  %-I:%M %p".to_string(), tr!("Mon 28 Sep  9:41 PM")),
        ("%a %-I:%M %p".to_string(), tr!("Mon 9:41 PM")),
        ("%H:%M".to_string(), "21:41".to_string()),
    ];
    let w2 = w.clone();
    g.add(&combo_row(&tr!("Clock"), clocks, &s(&cfg, "clock_format", "%a %-d %b  %H:%M"), move |v: &String| {
        with(&w2, |ctx| ctx.save("topbar", "clock_format", json!(v)));
    }, ""));
    // every status item, in the menu bar's order (Control Center and the clock always stay)
    let items = group(&tr!("Show in Menu Bar"), "");
    for (key, title, sub) in [
        ("show_tray", tr!("Background apps"), tr!("Status icons of apps running in the background (Discord, Steam…)")),
        ("show_now_playing", tr!("Now Playing"), tr!("While something plays")),
        ("show_input", tr!("Keyboard layout"), tr!("The layout in use (BR, US…); Ctrl+Space: the previous one")),
        ("show_sound", tr!("Sound"), tr!("Volume and outputs are always in Control Center")),
        ("show_battery", tr!("Battery"), String::new()),
        ("battery_percent", tr!("Battery percentage"), String::new()),
        ("show_bluetooth", "Bluetooth".to_string(), String::new()),
        ("show_wifi", "Wi-Fi".to_string(), String::new()),
        ("show_spotlight", gettext(names::SEARCH), String::new()),
    ] {
        let w2 = w.clone();
        items.add(&switch_row(&title, b(&cfg, key, defaults[key].as_bool().unwrap_or(false)), move |on| {
            with(&w2, |ctx| ctx.save("topbar", key, json!(on)));
        }, &sub));
    }
    // CPU, GPU, memory, network, FPS: off, as text or as a small graph (off by default)
    let perf = group(&tr!("Performance in Menu Bar"), "");
    for kind in &kinds {
        let now = if b(&cfg, &format!("show_{kind}"), false) { s(&cfg, &format!("{kind}_style"), "text") } else { "off".into() };
        let (w2, k) = (w.clone(), kind.clone());
        perf.add(&combo_row(&stat_title(&cards, kind), vec![("off".to_string(), tr!("Off")), ("text".to_string(), tr!("Text")), ("graph".to_string(), tr!("Graph"))], &now, move |v: &String| {
            with(&w2, |ctx| set_stat(ctx, &k, v));
        }, &(if kind == "fps" { tr!("Frames per second of the app in front") } else { String::new() })));
    }
    let app = appearance();
    // always there, usable only for "Text: custom…": showing / hiding it
    // moved the rows under it, and a click meant for the next row landed
    // on it (it turned "Sonata title bars for all apps" off once)
    let menu_logo = s(&app, "menu_logo", "distro");
    let text_row = adw::EntryRow::builder().title(tr!("Menu bar text")).text(s(&app, "menu_text", "")).use_markup(false).sensitive(menu_logo == LOGO_CUSTOM.0).show_apply_button(true).build();
    if text_row.find_property("max-length").is_some() {
        text_row.set_property("max-length", LOGO_TEXT_MAX); // libadwaita 1.6+
    }
    let w2 = w.clone();
    text_row.connect_apply(move |r| {
        let t = r.text().trim().to_string();
        with(&w2, |ctx| ctx.save("appearance", "menu_text", json!(t)));
    });
    let logo = group(&tr!("Logo"), "");
    let mut logos = labels(&LOGOS);
    logos.push((LOGO_CUSTOM.0.to_string(), gettext(LOGO_CUSTOM.1)));
    let (w2, tr2) = (w.clone(), text_row.downgrade());
    logo.add(&combo_row(&tr!("Menu bar logo"), logos, &menu_logo, move |v: &String| {
        with(&w2, |ctx| ctx.save("appearance", "menu_logo", json!(v)));
        if let Some(r) = tr2.upgrade() {
            r.set_sensitive(v == LOGO_CUSTOM.0);
        }
    }, &tr!("The menu at the left end of the menu bar")));
    logo.add(&text_row); // text:custom: your words (emoji drawn in one colour)
    let reset = reset_group(&tr!("Reset Menu Bar"), &tr!("The menu bar's options and logo back to the defaults"), move || {
        with(&w, |ctx| {
            let w = Rc::downgrade(ctx);
            ctx.ask_reset(&tr!("Reset Menu Bar?"), &tr!("The menu bar's options and logo go back to the defaults."), move || with(&w, reset_menubar));
        });
    });
    vec![g, items, perf, logo, reset]
}

fn set_stat(ctx: &Ctx, kind: &str, value: &str) {
    if value != "off" {
        ctx.save("topbar", &format!("{kind}_style"), json!(value));
    }
    ctx.save("topbar", &format!("show_{kind}"), json!(value != "off"));
}

/// Reset Menu Bar restarts the menu bar only when its height changes (it
/// is read when the menu bar starts).
pub fn reset_needs_restart(tall_now: &Value, defaults: &Value) -> bool {
    tall_now.as_bool().unwrap_or(true) != defaults["tall"].as_bool().unwrap_or(true)
}

fn reset_menubar(ctx: &Ctx) {
    let (_, kinds) = stat_kinds();
    let defaults = topbar_defaults(&kinds);
    let tall = sonata_config::load("topbar", &defaults).get("tall").cloned().unwrap_or(json!(true));
    for (k, v) in defaults.as_object().into_iter().flatten() {
        ctx.save("topbar", k, v.clone());
    }
    if reset_needs_restart(&tall, &defaults) {
        set_tall_menubar(ctx, defaults["tall"].as_bool().unwrap_or(true));
    }
    let ad = appearance_defaults();
    for k in ["menu_logo", "menu_text"] {
        ctx.save("appearance", k, ad[k].clone());
    }
    ctx.rebuild_page("menubar");
    ctx.toast(&tr!("Menu Bar reset"));
}

// == Control Center ==================================================================================
/// controlcenter.CATALOG (its fixed modules; the performance ones are
/// "stat_<kind>", one per figure of this machine)
const CC_CATALOG: [(&str, &str); 10] = [
    ("connectivity", n_!("Wi-Fi & Bluetooth")), ("dnd", n_!("Do Not Disturb")), ("darkmode", n_!("Dark Mode")),
    ("screenshot", n_!("Screenshot")), ("display", n_!("Display")), ("sound", n_!("Sound")),
    ("nowplaying", n_!("Now Playing")), ("mixer", n_!("Volume Mixer")), ("fpslimit", n_!("FPS Limit")), ("keyboard", n_!("Keyboard")),
];
const CC_DEFAULT_ORDER: [&str; 7] = ["connectivity", "dnd", "darkmode", "screenshot", "display", "sound", "nowplaying"];

/// The modules shown, in order: known ids, each once (controlcenter.load).
pub fn cc_modules(saved: Option<&Value>, known: &[String]) -> Vec<String> {
    let order: Vec<String> = match saved.and_then(Value::as_array) {
        Some(a) => a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
        None => CC_DEFAULT_ORDER.iter().map(|s| s.to_string()).collect(),
    };
    let mut out: Vec<String> = Vec::new();
    for m in order {
        if known.contains(&m) && !out.contains(&m) {
            out.push(m);
        }
    }
    out
}

/// Control Center: which modules it shows (also: hold one in Control
/// Center to move or remove it, Add Controls to add).
pub fn page_controlcenter(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let (cards, kinds) = stat_kinds();
    let controls: Vec<(String, String)> = CC_CATALOG.iter().map(|(id, t)| (id.to_string(), gettext(t))).collect();
    let perf: Vec<(String, String)> = kinds.iter().map(|k| (format!("stat_{k}"), stat_title(&cards, k))).collect();
    let known: Rc<Vec<String>> = Rc::new(controls.iter().chain(perf.iter()).map(|(id, _)| id.clone()).collect());
    let load = |known: &[String]| cc_modules(sonata_config::stored("controlcenter").get("modules"), known);
    let order = load(&known);
    let mut groups = Vec::new();
    for (title, desc, ids) in [(tr!("Controls"), tr!("Hold a module in Control Center to move it."), controls), (tr!("Performance"), tr!("Read only while Control Center is open."), perf)] {
        let g = group(&title, &desc);
        for (mid, name) in ids {
            let (w, known) = (Rc::downgrade(ctx), known.clone());
            let on_now = order.contains(&mid);
            g.add(&switch_row(&name, on_now, move |on| {
                let mut now = load(&known);
                if on && !now.contains(&mid) {
                    now.push(mid.clone());
                } else if !on {
                    now.retain(|m| *m != mid);
                }
                with(&w, |ctx| ctx.save("controlcenter", "modules", json!(now)));
            }, ""));
        }
        groups.push(g);
    }
    let w = Rc::downgrade(ctx);
    groups.push(reset_group(&tr!("Reset Control Center"), &tr!("Its modules and their order back to the defaults"), move || {
        with(&w, |ctx| {
            let w = Rc::downgrade(ctx);
            ctx.ask_reset(&tr!("Reset Control Center?"), &tr!("Its modules and their order go back to the defaults."), move || {
                with(&w, |ctx| {
                    ctx.save("controlcenter", "modules", Value::Null);
                    ctx.rebuild_page("controlcenter");
                    ctx.toast(&tr!("Control Center reset"));
                });
            });
        });
    }));
    groups
}

// == Apps: the grid ==================================================================================
pub fn page_launchpad(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let apps = gettext(names::APPS);
    let g = group(&apps, "");
    let w = Rc::downgrade(ctx);
    let reset = push_button(&tr!("Reset…"), move || {
        with(&w, |ctx| {
            let apps = gettext(names::APPS);
            let w = Rc::downgrade(ctx);
            sonata_ui::dialog::alert(&fmt(&tr!("Reset the {apps} layout?"), &[("apps", &apps)]), &tr!("Folders and your icon order are removed; apps are sorted by name."), &[("cancel", tr!("Cancel"), ""), ("reset", tr!("Reset"), "destructive")], move |r| {
                if r != "reset" {
                    return;
                }
                let hidden = sonata_config::load("launchpad", &json!({"pages": [], "hidden": []})).get("hidden").cloned().unwrap_or(json!([]));
                let mut data = Map::new();
                data.insert("pages".into(), json!([]));
                data.insert("hidden".into(), hidden);
                let _ = sonata_config::save("launchpad", &data);
                with(&w, |ctx| ctx.toast(&fmt(&tr!("{apps} was reset"), &[("apps", &gettext(names::APPS))])));
            }, ctx.parent().as_ref());
        });
    });
    let row = adw::ActionRow::builder().title(tr!("Layout")).subtitle(tr!("Pages, folders and order")).build();
    row.add_suffix(&reset);
    g.add(&row);
    vec![g]
}

// == Hidden & Protected Apps: behind the user's password ==============================================
/// Locked until the user's password (PAM, like the lock screen) is
/// typed; locks again when another section is chosen.
pub fn page_hidden(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let g = group("", &fmt(&tr!("Apps hidden from {apps}, {search} and the Dock. Enter your password to see and edit them."), &[("apps", &gettext(names::APPS)), ("search", &gettext(names::SEARCH))]));
    let row = adw::ActionRow::builder().title(tr!("Password")).build();
    row.add_prefix(&gtk::Image::from_icon_name("system-lock-screen-symbolic"));
    let entry = sonata_ui::controls::secret_field("", "");
    entry.set_valign(gtk::Align::Center);
    entry.set_width_chars(18);
    row.add_suffix(&entry);
    g.add(&row);
    let hint = gtk::Label::builder().label(if sonata_shell::pam::available() { String::new() } else { tr!("PAM is not available: can't check passwords") }).xalign(0.0).css_classes(["dim-label", "caption"]).build();
    g.add(&hint);
    let lk = look(ctx);
    lk.hidden_lock.set(Some(&g));
    lk.hidden_groups.borrow_mut().clear();
    let w = Rc::downgrade(ctx);
    entry.connect_activate(move |e| {
        let pw = e.text().to_string();
        if pw.is_empty() {
            return;
        }
        e.set_sensitive(false);
        let user = glib::user_name().to_string_lossy().into_owned();
        let (e, hint, w) = (e.clone(), hint.clone(), w.clone());
        kit::run_async(move || sonata_shell::pam::authenticate(&user, &pw), move |ok| {
            e.set_sensitive(true);
            if ok {
                with(&w, show_hidden_apps);
            } else {
                hint.set_label(&tr!("Wrong password"));
                e.set_text("");
                e.grab_focus();
            }
        });
    });
    glib::idle_add_local_once(move || {
        entry.grab_focus();
    });
    vec![g]
}

/// desktop id (no ".desktop") -> its app, for the apps a launcher lists.
fn installed_apps() -> Vec<(String, gio::AppInfo)> {
    sonata_ui::apps::scan().into_iter().filter(|(_, i)| i.should_show()).map(|(d, i)| (d.strip_suffix(".desktop").unwrap_or(&d).to_string(), i.upcast())).collect()
}

fn show_hidden_apps(ctx: &Ctx) {
    let Some(pane) = ctx.pane("hidden") else { return };
    let page = pane.main.clone(); // (not an app's, if one is open)
    let lk = look(ctx);
    // the list takes the lock's place: added last, it went under Installed
    // Apps, out of sight, and the unlocked section seemed to show nothing.
    // (Adw.PreferencesPage can't insert: the groups after it go again after.)
    let mut mine: Vec<adw::PreferencesGroup> = lk.hidden_lock.upgrade().into_iter().collect();
    mine.extend(lk.hidden_groups.borrow().iter().cloned());
    let here = mine.iter().find(|g| g.parent().is_some()).cloned();
    let mut after: Vec<adw::PreferencesGroup> = Vec::new();
    let mut sib = here.and_then(|h| h.next_sibling());
    while let Some(s) = sib {
        if let Some(g) = s.downcast_ref::<adw::PreferencesGroup>() {
            if !mine.contains(g) {
                after.push(g.clone());
            }
        }
        sib = s.next_sibling();
    }
    for grp in mine.iter().chain(after.iter()) {
        if grp.parent().is_some() {
            page.remove(grp);
        }
    }
    lk.hidden_groups.borrow_mut().clear();
    let installed = installed_apps();
    let hidden: Vec<String> = sonata_config::load("launchpad", &json!({"pages": [], "hidden": []})).get("hidden").and_then(Value::as_array).into_iter().flatten().filter_map(|v| v.as_str().map(String::from)).filter(|a| installed.iter().any(|(d, _)| d == a)).collect();
    let h = group(&tr!("Hidden apps"), &fmt(&tr!("In the Hidden folder in {apps} (opened with your password)."), &[("apps", &gettext(names::APPS))]));
    for did in &hidden {
        let Some((_, info)) = installed.iter().find(|(d, _)| d == did) else { continue };
        let r = adw::ActionRow::builder().title(info.display_name().as_str()).use_markup(false).build();
        let img = gtk::Image::builder().pixel_size(32).build();
        img.set_from_gicon(&sonata_ui::icons::app_icon(info));
        r.add_prefix(&img);
        let (w, did) = (Rc::downgrade(ctx), did.clone());
        r.add_suffix(&push_button(&tr!("Show"), move || {
            set_hidden(&did, false);
            with(&w, show_hidden_apps);
        }));
        h.add(&r);
    }
    if hidden.is_empty() {
        h.add(&adw::ActionRow::builder().title(tr!("No hidden apps")).build());
    }
    let add = sonata_ui::controls::push_button(&tr!("Hide an App…"), || {}, "");
    add.set_halign(gtk::Align::Start);
    add.set_margin_top(10);
    let (w, hidden2) = (Rc::downgrade(ctx), hidden.clone());
    add.connect_clicked(move |b| with(&w, |ctx| pick_app_to_hide(ctx, b, &hidden2)));
    h.add(&add);
    page.add(&h);
    for grp in &after {
        page.add(grp);
    }
    lk.hidden_groups.borrow_mut().push(h);
}

fn pick_app_to_hide(ctx: &Ctx, anchor: &gtk::Button, hidden: &[String]) {
    let bx = gtk::Box::new(gtk::Orientation::Vertical, 6);
    let search = kit::search_entry(None, false);
    bx.append(&search);
    let lb = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::None).css_classes(["boxed-list"]).build();
    let mut apps: Vec<(String, String, gio::AppInfo)> = installed_apps().into_iter().filter(|(d, _)| !hidden.contains(d)).map(|(d, i)| (i.display_name().to_string(), d, i)).collect();
    apps.sort_by_key(|(n, ..)| n.to_lowercase());
    let keys: Rc<Vec<(String, String)>> = Rc::new(apps.iter().map(|(n, d, _)| (n.to_lowercase(), d.clone())).collect());
    for (name, _, info) in &apps {
        let rb = gtk::Box::builder().spacing(8).margin_start(6).margin_end(6).margin_top(4).margin_bottom(4).build();
        let img = gtk::Image::builder().pixel_size(24).build();
        img.set_from_gicon(&sonata_ui::icons::app_icon(info));
        rb.append(&img);
        rb.append(&gtk::Label::builder().label(name.as_str()).xalign(0.0).build());
        lb.append(&gtk::ListBoxRow::builder().child(&rb).build());
    }
    {
        let (keys, search) = (keys.clone(), search.clone());
        lb.set_filter_func(move |r| {
            let q = search.text().to_lowercase();
            keys.get(r.index() as usize).is_none_or(|(n, _)| n.contains(&q))
        });
    }
    let lbw = lb.downgrade();
    search.connect_search_changed(move |_| {
        if let Some(lb) = lbw.upgrade() {
            lb.invalidate_filter();
        }
    });
    bx.append(&gtk::ScrolledWindow::builder().child(&lb).min_content_height(300).min_content_width(280).hscrollbar_policy(gtk::PolicyType::Never).build());
    let pop = sonata_ui::panel::popup(anchor, &bx, gtk::PositionType::Bottom);
    let (w, popw) = (Rc::downgrade(ctx), pop.downgrade());
    lb.connect_row_activated(move |_, r| {
        if let Some(p) = popw.upgrade() {
            p.popdown();
        }
        let Some((_, did)) = keys.get(r.index() as usize) else { return };
        set_hidden(did, true);
        with(&w, show_hidden_apps);
    });
    search.grab_focus();
}

/// Hide (out of Launchpad's pages, the Dock) or show again; Launchpad
/// and the Dock reload their files.
fn set_hidden(did: &str, on: bool) {
    use sonata_desktop::launchpad_model as M;
    let data = sonata_config::load("launchpad", &M::defaults());
    let names = installed_apps().into_iter().map(|(d, i)| (d, i.display_name().to_string())).collect();
    let mut m = M::Model::new(&Value::Object(data), names);
    if on {
        m.hide(did);
        let dock = sonata_config::load("dock", &json!({"pinned": null}));
        if let Some(pinned) = dock.get("pinned").and_then(Value::as_array) {
            if pinned.iter().any(|p| p.as_str() == Some(did)) {
                let kept: Vec<Value> = pinned.iter().filter(|p| p.as_str() != Some(did)).cloned().collect();
                let _ = sonata_config::update("dock", &json!({"pinned": kept}));
            }
        }
    } else if m.hidden.iter().any(|h| h == did) {
        m.hidden.retain(|h| h != did);
        m.reconcile();
    }
    if let Some(o) = m.to_json().as_object() {
        let _ = sonata_config::save("launchpad", o);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The Dock's own defaults and the ones Settings resets to agree.
    #[cfg(feature = "system")]
    #[test]
    fn dock_defaults_match_the_dock() {
        assert_eq!(dock_defaults(), sonata_dock::dock::defaults());
    }

    /// Vini: the slider's "More" end must be really see-through.
    #[test]
    fn test_most_see_through_is_clear() {
        assert_eq!(slider_to_alpha(100.0), 0.0);
    }

    #[test]
    fn test_more_room_to_be_see_through() {
        assert!(alpha_to_slider(0.60) > 0.0);
        assert!(alpha_to_slider(0.60) < 80.0);
    }

    #[test]
    fn test_slider_mapping_round_trips() {
        for a in [0.0, 0.2, 0.45, 0.6, 0.85, 0.97] {
            assert!((slider_to_alpha(alpha_to_slider(a)) - a).abs() < 0.01, "{a}");
        }
    }

    /// Regression: a part's alpha saved, then changed again, raised
    /// (dict() got multiple values for 'alpha') and nothing was saved.
    #[test]
    fn test_slider_moved_twice_saves() {
        let on = |_: &str| true;
        let first = merge_glass(&Map::new(), &on, json!({"menubar": {"alpha": slider_to_alpha(20.0)}}).as_object().unwrap());
        let second = merge_glass(&first, &on, json!({"menubar": {"alpha": slider_to_alpha(80.0)}}).as_object().unwrap());
        assert_eq!(second["menubar"]["alpha"].as_f64(), Some(slider_to_alpha(80.0)));
        assert_eq!(second["menubar"]["on"], json!(true));
        let off = merge_glass(&second, &on, json!({"menubar": {"on": false}, "blur": 80}).as_object().unwrap());
        assert_eq!(off["menubar"]["on"], json!(false));
        assert_eq!(off["menubar"]["alpha"].as_f64(), Some(slider_to_alpha(80.0)));
        assert_eq!(off["blur"], json!(80));
    }

    #[test]
    fn theme_alpha() {
        assert_eq!(css_alpha("rgba(30, 30, 32, 0.62)"), 0.62);
        assert_eq!(css_alpha("#ffffff"), 1.0);
    }

    #[test]
    fn radii_clamped_and_defaulted() {
        let app = json!({"radius": {"dock": 99, "menu": 3.4}});
        assert_eq!(user_radii(app.as_object().unwrap()), vec![("window", frame::RADIUS), ("dock", 32), ("menu", 3)]);
    }

    /// Dragging a dot: the order from the window's edge, on either side.
    #[test]
    fn button_order_drag() {
        let o: Vec<String> = ["close", "minimize", "maximize"].iter().map(|s| s.to_string()).collect();
        assert_eq!(moved_order(&o, false, "close", 2).unwrap(), ["minimize", "maximize", "close"]);
        assert_eq!(moved_order(&o, false, "close", 0), None);
        // on the right the row is mirrored: drawn maximize, minimize, close
        assert_eq!(moved_order(&o, true, "close", 0).unwrap(), ["minimize", "maximize", "close"]);
        assert_eq!(moved_order(&o, false, "stranger", 0), None);
    }

    #[test]
    fn test_never_set_means_the_default() {
        let down = tempfile::tempdir().unwrap();
        let p = down.path().to_string_lossy().into_owned();
        assert_eq!(dock_stacks_from(None, down.path()).iter().map(path_of).collect::<Vec<_>>(), [p.clone()]);
        assert_eq!(dock_stacks_from(Some(&Value::Null), down.path()).len(), 1);
        assert!(dock_stacks_from(Some(&json!([])), down.path()).is_empty()); // removed from the Dock
    }

    #[test]
    fn test_add_and_remove_a_folder() {
        let (down, other) = (tempfile::tempdir().unwrap(), tempfile::tempdir().unwrap());
        let (d, o) = (down.path().to_string_lossy().into_owned(), other.path().to_string_lossy().into_owned());
        let stacks = vec![stack_spec(&d)];
        let two = with_folder(&stacks, &o).unwrap();
        assert!(with_folder(&two, &o).is_none()); // once
        assert_eq!(two.iter().map(path_of).collect::<Vec<_>>(), [d, o]);
        assert!(with_folder(&two, "/no/such/folder").is_none());
    }

    /// Reset Desktop & Windows put the title bar's double-click back to
    /// Zoom but not the windows' top edge (sonata-resize), which kept the
    /// old action.
    #[test]
    fn test_reset_desktop_puts_back_the_top_edge_double_click() {
        let opts = desktop_reset_options();
        assert!(opts.contains(&("sonata-resize", "double_click", "toggle-maximize".to_string())));
        assert!(opts.contains(&("animate", "minimize_animation", "squeezimize".to_string())));
    }

    /// Reset Menu Bar wrote "Taller menu bar" back on but the menu bar
    /// kept its height: it is read when the menu bar starts, and only the
    /// switch restarted it.
    #[test]
    fn test_reset_menubar_brings_back_the_taller_menu_bar() {
        let d = topbar_defaults(&[]);
        assert!(reset_needs_restart(&json!(false), &d));
        assert!(!reset_needs_restart(&json!(true), &d)); // already tall: no restart for nothing
    }

    #[test]
    fn topbar_keys_per_figure() {
        let d = topbar_defaults(&["gpu_nvidia".to_string(), "cpu".to_string()]);
        assert_eq!(d["show_gpu_nvidia"], json!(false));
        assert_eq!(d["gpu_nvidia_style"], json!("text"));
        assert_eq!(d["tall"], json!(true));
    }

    #[test]
    fn control_center_modules() {
        let known: Vec<String> = ["dnd", "sound", "stat_cpu"].iter().map(|s| s.to_string()).collect();
        assert_eq!(cc_modules(None, &known), ["dnd", "sound"]);
        assert_eq!(cc_modules(Some(&json!(["stat_cpu", "x", "dnd", "dnd", 3])), &known), ["stat_cpu", "dnd"]);
    }

    /// Apps style is with the rest of the look (Appearance), not in Apps.
    #[test]
    fn apps_style_in_appearance() {
        let src = include_str!("look.rs");
        let appearance = &src[src.find("pub fn page_appearance").unwrap()..src.find("fn set_steam_theme").unwrap()];
        let launchpad = &src[src.find("pub fn page_launchpad").unwrap()..src.find("pub fn page_hidden").unwrap()];
        assert!(appearance.contains("APPS_STYLES"));
        assert!(!launchpad.contains("APPS_STYLES"));
    }
}
