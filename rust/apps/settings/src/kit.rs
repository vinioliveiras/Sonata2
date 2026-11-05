//! The rows every Settings page is built from (app.py's module-level
//! helpers): groups, switch / combo / slider rows, a loading row, an
//! install row, the sidebar badges, the search field look, and the small
//! pure helpers (nearest, clock_format, speed...).
//!
//! The "quiet" flag Python set on a row (`row.quiet = True`) is the row's
//! `st-quiet` data here: while it is set its on_change doesn't run, so a
//! state read back from the system is shown without being written back.

use adw::prelude::*;
use gtk::{gio, glib};
use sonata_i18n::tr;
use std::cell::Cell;
use std::rc::Rc;

/// Every slider row's slider: same width, same leading edge (macOS).
pub const SLIDER_W: i32 = 240;
pub const INSTALL_POLL_S: u32 = 2;
pub const INSTALL_WAIT_S: u32 = 15 * 60;
/// Displays' wallpaper preview: decoded at most this wide/tall (180 px high, HiDPI).
pub const WALL_PREVIEW_PX: i32 = 1280;

/// tokens.BACKGROUNDS (not exported to tokens.json yet): Appearance's
/// background dots, (name msgid, colour) per mode.
pub const BACKGROUNDS: [(&str, [(&str, &str); 8]); 2] = [
    ("light", [(sonata_i18n::n_!("Default"), "#ececec"), (sonata_i18n::n_!("Paper"), "#f5f3ee"), (sonata_i18n::n_!("Sand"), "#efe8dc"), (sonata_i18n::n_!("Mist"), "#e4e9f0"), (sonata_i18n::n_!("Mint"), "#e3eee7"), (sonata_i18n::n_!("Rose"), "#f1e5e8"), (sonata_i18n::n_!("Lavender"), "#e9e5f2"), (sonata_i18n::n_!("Butter"), "#f2eedb")]),
    ("dark", [(sonata_i18n::n_!("Default"), "#18181a"), (sonata_i18n::n_!("Black"), "#0b0b0c"), (sonata_i18n::n_!("Graphite"), "#26262a"), (sonata_i18n::n_!("Navy"), "#121826"), (sonata_i18n::n_!("Forest"), "#121c16"), (sonata_i18n::n_!("Plum"), "#1d1420"), (sonata_i18n::n_!("Espresso"), "#1f1915"), (sonata_i18n::n_!("Teal"), "#0f1e1f")]),
];

/// tokens.ACCENTS' swatches (light, dark): Appearance's accent dots.
pub const ACCENTS: [(&str, &str, &str); 8] = [
    ("blue", "#007aff", "#0a84ff"),
    ("purple", "#953d96", "#a550a7"),
    ("pink", "#f74f9e", "#f74f9e"),
    ("red", "#e0383e", "#ff453a"),
    ("orange", "#f7821b", "#ff9f0a"),
    ("yellow", "#fcb827", "#ffd60a"),
    ("green", "#62ba46", "#32d74b"),
    ("graphite", "#8c8c8c", "#98989d"),
];

/// Settings' CSS: app.py's own blocks (css.json) and the colour classes
/// the exporter leaves to Rust (`_ACCENT_CSS + ...`, the backgrounds).
pub fn register_css() {
    sonata_ui::css::register_module("settings/app.py");
    let accents: String = ACCENTS.iter().map(|(n, light, _)| format!(".st-accent.{n} {{ background: {light}; }}\n")).collect();
    sonata_ui::register("settings-accent-colours", &accents);
    let mut bgs = String::new();
    for (mode, opts) in BACKGROUNDS {
        for (i, (_, c)) in opts.iter().enumerate() {
            bgs.push_str(&format!(".st-bg.bg-{mode}-{i} {{ background: {c}; }}\n"));
        }
    }
    sonata_ui::register("settings-backgrounds", &bgs);
}

// -- quiet rows ----------------------------------------------------------------------------------
fn is_quiet(o: &impl IsA<glib::Object>) -> bool {
    // SAFETY: "st-quiet" only ever holds a bool (set below)
    unsafe { o.as_ref().data::<bool>("st-quiet").is_some_and(|p| *p.as_ref()) }
}

fn set_quiet(o: &impl IsA<glib::Object>, on: bool) {
    // SAFETY: as above
    unsafe { o.as_ref().set_data("st-quiet", on) }
}

/// Run `f` with `row` quiet: what it changes on the row is not written back.
pub fn quietly(row: &impl IsA<glib::Object>, f: impl FnOnce()) {
    set_quiet(row, true);
    f();
    set_quiet(row, false);
}

/// Show a state read back from the system on a switch row without writing
/// it back (its on_change doesn't run).
pub fn show_quietly(row: &adw::SwitchRow, on: bool) {
    quietly(row, || row.set_active(on));
}

/// show_quietly for a combo_row: `value` selected if it is one of its options.
pub fn show_quietly_combo<T: PartialEq + 'static>(row: &adw::ComboRow, value: &T) {
    if let Some(i) = combo_values::<T>(row).and_then(|v| v.iter().position(|x| x == value)) {
        quietly(row, || row.set_selected(i as u32));
    }
}

fn combo_values<T: 'static>(row: &adw::ComboRow) -> Option<Rc<Vec<T>>> {
    // SAFETY: "st-values" is set once, by combo_row::<T>, and read with the same T
    unsafe { row.data::<Rc<Vec<T>>>("st-values").map(|p| p.as_ref().clone()) }
}

/// The value selected in a combo_row (its options' values).
pub fn combo_value<T: Clone + 'static>(row: &adw::ComboRow) -> Option<T> {
    combo_values::<T>(row).and_then(|v| v.get(row.selected() as usize).cloned())
}

// -- rows -----------------------------------------------------------------------------------------
/// Settings' search field: the sidebar's look everywhere (Vini) -- a
/// rounded grey field with the magnifier. inline: inside a section.
pub fn search_entry(placeholder: Option<&str>, inline: bool) -> gtk::SearchEntry {
    let e = gtk::SearchEntry::builder().placeholder_text(placeholder.map(str::to_string).unwrap_or_else(|| tr!("Search"))).build();
    for c in search_classes(inline) {
        e.add_css_class(c);
    }
    e
}

/// The search field's style classes (inline: inside a section).
pub fn search_classes(inline: bool) -> &'static [&'static str] {
    if inline { &["st-search", "inline"] } else { &["st-search"] }
}

/// A sidebar badge: the section's symbol on its colour.
pub fn badge(icon: &str, color: &str, big: bool) -> gtk::Box {
    let img = gtk::Image::builder().icon_name(icon).pixel_size(if big { 28 } else { 16 }).build();
    let b = gtk::Box::builder().valign(gtk::Align::Center).halign(gtk::Align::Center).build();
    b.add_css_class("st-badge");
    b.add_css_class(color);
    if big {
        b.add_css_class("big");
    }
    b.append(&img);
    b
}

/// A group of rows. Titles and descriptions are markup: "Point & Click"
/// would come out blank, so they are escaped here.
pub fn group(title: &str, description: &str) -> adw::PreferencesGroup {
    adw::PreferencesGroup::builder().title(glib::markup_escape_text(title)).description(glib::markup_escape_text(description)).build()
}

/// A switch row; on_change(on) when the user flips it (not when shown quietly).
/// Plain text: app, device and network names may hold "&" or "<".
pub fn switch_row(title: &str, active: bool, on_change: impl Fn(bool) + 'static, subtitle: &str) -> adw::SwitchRow {
    let row = adw::SwitchRow::builder().title(title).subtitle(subtitle).active(active).use_markup(false).build();
    row.connect_active_notify(move |r| {
        if !is_quiet(r) {
            on_change(r.is_active());
        }
    });
    row
}

/// A pop-up row of options [(value, label)]; on_change(&value) when picked.
/// An unknown `selected` shows the first option.
pub fn combo_row<T: Clone + PartialEq + 'static>(title: &str, options: Vec<(T, String)>, selected: &T, on_change: impl Fn(&T) + 'static, subtitle: &str) -> adw::ComboRow {
    let labels: Vec<&str> = options.iter().map(|o| o.1.as_str()).collect();
    let row = adw::ComboRow::builder().title(title).subtitle(subtitle).use_markup(false).model(&gtk::StringList::new(&labels)).build();
    let values: Rc<Vec<T>> = Rc::new(options.into_iter().map(|o| o.0).collect());
    row.set_selected(values.iter().position(|v| v == selected).unwrap_or(0) as u32);
    // SAFETY: read back only through combo_values::<T>
    unsafe { row.set_data("st-values", values.clone()) };
    row.connect_selected_notify(move |r| {
        if !is_quiet(r) {
            if let Some(v) = values.get(r.selected() as usize) {
                on_change(v);
            }
        }
    });
    row
}

/// A row with a slider (fixed width: the trailing edges line up). ends:
/// small labels under the slider's ends ("Slow", "Fast": macOS). default:
/// a double-click on the slider resets it to this value.
pub fn slider_row(title: &str, value: f64, lower: f64, upper: f64, on_change: impl Fn(f64) + 'static, subtitle: &str, ends: Option<(&str, &str)>, default: Option<f64>) -> (adw::ActionRow, gtk::Scale) {
    let row = adw::ActionRow::builder().title(title).subtitle(subtitle).use_markup(false).build();
    let s = sonata_ui::controls::slider(value, on_change, lower, upper, default);
    s.set_size_request(SLIDER_W, -1);
    s.set_hexpand(false); // fixed width: the trailing edge lines up with the other controls
    s.set_valign(gtk::Align::Center);
    if let Some((a, b)) = ends {
        // hexpand off: the labels' hexpand would widen it past SLIDER_W
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).valign(gtk::Align::Center).margin_top(4).margin_bottom(4).hexpand(false).build();
        col.append(&s);
        let labels = gtk::Box::new(gtk::Orientation::Horizontal, 0);
        labels.append(&gtk::Label::builder().label(a).css_classes(["st-caption"]).hexpand(true).xalign(0.0).build());
        labels.append(&gtk::Label::builder().label(b).css_classes(["st-caption"]).xalign(1.0).build());
        col.append(&labels);
        row.add_suffix(&col);
    } else {
        row.add_suffix(&s);
    }
    (row, s)
}

/// A row saying what is being read, with a spinner (Vini: Wi-Fi, an
/// app's permissions -- lists that take a moment).
pub fn loading_row(title: &str) -> adw::ActionRow {
    let row = adw::ActionRow::builder().title(title).use_markup(false).build();
    let sp = sonata_ui::progress::Spinner::new(true);
    sp.set_valign(gtk::Align::Center);
    row.add_prefix(&sp);
    row
}

/// install_row's options.
#[derive(Default)]
pub struct Install {
    /// with on_ready: the page refreshes itself once it's installed
    pub ready: Option<Rc<dyn Fn() -> bool>>,
    pub on_ready: Option<Rc<dyn Fn()>>,
    /// the button's label (default "Install…")
    pub label: Option<String>,
    /// (heading, body, button) asked first
    pub confirm: Option<(String, String, String)>,
}

/// A row for something Settings needs installed (Vini): its button opens
/// Terminal with the command running (the password asked there). command
/// None (unknown package manager): the subtitle alone. Returns the row and
/// its button.
pub fn install_row(title: &str, subtitle: &str, command: Option<&str>, opts: Install) -> (adw::ActionRow, Option<gtk::Button>) {
    let row = adw::ActionRow::builder().title(title).subtitle(subtitle).use_markup(false).build();
    row.set_subtitle_lines(0);
    let Some(command) = command.filter(|c| !c.is_empty()).map(str::to_string) else { return (row, None) };
    let btn = gtk::Button::with_label(&opts.label.clone().unwrap_or_else(|| tr!("Install…")));
    btn.add_css_class("sonata-button");
    btn.set_valign(gtk::Align::Center);
    if !command.contains('\n') {
        btn.set_tooltip_text(Some(&command));
    }
    row.add_suffix(&btn);
    let opts = Rc::new(opts);
    let run = {
        let (row, opts) = (row.downgrade(), opts.clone());
        Rc::new(move || {
            if !sonata_backend::system::run_in_terminal(&command) {
                return;
            }
            let (Some(ready), Some(on_ready)) = (opts.ready.clone(), opts.on_ready.clone()) else { return };
            let (row, waited) = (row.clone(), Cell::new(0u32));
            glib::timeout_add_seconds_local(INSTALL_POLL_S, move || {
                waited.set(waited.get() + INSTALL_POLL_S);
                let gone = row.upgrade().is_none_or(|r| r.root().is_none());
                if gone || waited.get() > INSTALL_WAIT_S {
                    return glib::ControlFlow::Break;
                }
                if !ready() {
                    return glib::ControlFlow::Continue;
                }
                on_ready();
                glib::ControlFlow::Break
            });
        })
    };
    let weak = row.downgrade();
    btn.connect_clicked(move |_| {
        let Some((heading, body, ok)) = opts.confirm.clone() else {
            run();
            return;
        };
        let run = run.clone();
        let parent = weak.upgrade().and_then(|r| r.root()).map(|r| r.upcast::<gtk::Widget>());
        sonata_ui::dialog::alert(&heading, &body, &[("cancel", tr!("Cancel"), ""), ("go", ok, "suggested")], move |rid| {
            if rid == "go" {
                run();
            }
        }, parent.as_ref());
    });
    (row, Some(btn))
}

/// Remove the rows added to a group (they live in its inner list box).
pub fn clear_group(grp: &adw::PreferencesGroup) {
    let mut rows = Vec::new();
    let mut stack: Vec<gtk::Widget> = vec![grp.clone().upcast()];
    while let Some(w) = stack.pop() {
        let mut c = w.first_child();
        while let Some(ch) = c {
            if ch.is::<gtk::ListBox>() {
                let mut r = ch.first_child();
                while let Some(row) = r {
                    r = row.next_sibling();
                    rows.push(row);
                }
            } else {
                stack.push(ch.clone());
            }
            c = ch.next_sibling();
        }
    }
    for r in rows {
        grp.remove(&r);
    }
}

// -- pure helpers ----------------------------------------------------------------------------------
/// The option value closest to a number read from the system (1.75 -> 2.0).
pub fn nearest(options: &[f64], value: f64) -> f64 {
    options.iter().copied().fold(None, |best: Option<f64>, v| match best {
        Some(b) if (b - value).abs() <= (v - value).abs() => Some(b),
        _ => Some(v),
    }).unwrap_or(value)
}

/// The menu bar clock format for Date & Time's two switches.
pub fn clock_format(date: bool, h24: bool) -> String {
    format!("{}{}", if date { "%a %-d %b  " } else { "%a " }, if h24 { "%H:%M" } else { "%-I:%M %p" })
}

/// Wayfire speed (-1..1) <-> slider (0..100).
pub fn speed(v: &str) -> f64 {
    v.trim().parse::<f64>().map(|f| (f + 1.0) * 50.0).unwrap_or(50.0)
}

/// Settings' minimize effect -> Wayfire's animate/minimize_animation.
pub fn minimize_animation(effect: &str) -> &'static str {
    if effect == "genie" {
        "squeezimize"
    } else {
        "zoom"
    }
}

/// Vini: warn about USBGuard's own default before anyone turns it on.
pub fn usb_warning() -> String {
    tr!("Careful: USBGuard on its own blocks every USB device it has no rule for -- your keyboard and mouse too.")
}

pub fn usb_confirm() -> String {
    tr!("USBGuard on its own blocks every USB device it has no rule for, your keyboard and mouse included. Set Up allows every device while the screen is unlocked and blocks only the ones plugged in while it's locked; devices already plugged in keep working.\n\nTerminal opens and asks for your password. Don't turn USBGuard on yourself in another way.")
}

/// The window buttons' side and order (appearance.json) for everything
/// drawn outside Sonata's own windows: Wayfire's title bars (pixdecor,
/// decoration), GNOME's button-layout (GTK 3/4 apps), libadwaita apps'
/// style. (wfconfig.frame_options' button keys, which aren't ported on
/// their own yet.)
pub fn apply_buttons_side() {
    use sonata_tweaks::frame;
    let f = frame::frame();
    let side = f.buttons_side.as_str();
    let inset = f.dot_left - f.dot as f64 / 2.0;
    // pixdecor adds x_offset on both sides: on the right it's negative,
    // or the rounded corner cuts the close button
    let offset = if side == "left" { inset.round_ties_even() } else { -inset.round_ties_even() };
    let names = &f.buttons;
    let wf = sonata_backend::wfconfig::set;
    wf("pixdecor", "button_layout", &frame::button_layout(&f));
    wf("pixdecor", &format!("{side}_button_spacing"), &f.dot_gap.to_string());
    wf("pixdecor", &format!("{side}_button_x_offset"), &format!("{}", offset as i64));
    // Wayfire's own bar keeps its buttons on the right: the outermost one at the edge
    let order: Vec<String> = if side == "right" { names.iter().rev().cloned().collect() } else { names[1..].iter().chain(names[..1].iter()).cloned().collect() };
    wf("decoration", "button_order", &order.join(" "));
    sonata_tweaks::prefs_write::set("org.gnome.desktop.wm.preferences", "button-layout", &frame::button_layout(&f));
    sonata_tweaks::titlebars::apply(None);
}

// -- groups and login items -------------------------------------------------------------------------
fn group_members(name: &str) -> Option<Vec<String>> {
    let c = std::ffi::CString::new(name).ok()?;
    // SAFETY: getgrnam's static result is read at once, on the main thread
    unsafe {
        let g = libc::getgrnam(c.as_ptr());
        if g.is_null() {
            return None;
        }
        let mut out = Vec::new();
        let mut p = (*g).gr_mem;
        while !p.is_null() && !(*p).is_null() {
            out.push(std::ffi::CStr::from_ptr(*p).to_string_lossy().into_owned());
            p = p.add(1);
        }
        Some(out)
    }
}

/// The user is in wheel, sudo or admin.
pub fn is_admin() -> bool {
    let me = glib::user_name().to_string_lossy().into_owned();
    ["wheel", "sudo", "admin"].iter().any(|g| group_members(g).is_some_and(|m| m.contains(&me)))
}

const AUTOSTART_DESKTOP: &str = "Sonata";

/// Highest priority first: the user's autostart folder, then the system's.
pub fn autostart_dirs() -> Vec<std::path::PathBuf> {
    let mut d = vec![glib::user_config_dir().join("autostart")];
    d.extend(glib::system_config_dirs().into_iter().map(|p| p.join("autostart")));
    d
}

/// (file name, app name, enabled) for every autostart entry (user and
/// system; a user copy overrides the system one).
pub fn login_items() -> Vec<(String, String, bool)> {
    let (mut seen, mut out) = (std::collections::HashSet::new(), Vec::new());
    for d in autostart_dirs() {
        let Ok(rd) = std::fs::read_dir(&d) else { continue };
        let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).collect();
        names.sort();
        for name in names {
            if !name.ends_with(".desktop") || !seen.insert(name.clone()) {
                continue;
            }
            let kf = glib::KeyFile::new();
            if kf.load_from_file(d.join(&name), glib::KeyFileFlags::NONE).is_err() {
                continue;
            }
            let Ok(title) = kf.locale_string("Desktop Entry", "Name", None) else { continue };
            let hidden = kf.boolean("Desktop Entry", "Hidden").unwrap_or(false) || kf.boolean("Desktop Entry", "X-GNOME-Autostart-enabled").ok() == Some(false);
            if let Ok(only) = kf.string_list("Desktop Entry", "OnlyShowIn") {
                if !only.is_empty() && !only.iter().any(|s| s.as_str() == AUTOSTART_DESKTOP) {
                    continue;
                }
            }
            out.push((name, title.to_string(), !hidden));
        }
    }
    out
}

/// Enable/disable a login item through a user copy (Hidden=true), like GNOME/KDE.
pub fn set_login_item(name: &str, on: bool) {
    let dirs = autostart_dirs();
    let Some(src) = dirs.iter().map(|d| d.join(name)).find(|p| p.exists()) else { return };
    let kf = glib::KeyFile::new();
    if kf.load_from_file(&src, glib::KeyFileFlags::KEEP_TRANSLATIONS).is_err() {
        return;
    }
    kf.set_boolean("Desktop Entry", "Hidden", !on);
    kf.set_boolean("Desktop Entry", "X-GNOME-Autostart-enabled", on);
    let _ = std::fs::create_dir_all(&dirs[0]);
    let _ = kf.save_to_file(dirs[0].join(name));
}

// -- threads -------------------------------------------------------------------------------------
/// system.run_async: `work` in a worker thread, `done(result)` back on the
/// main loop.
pub fn run_async<T: Send + 'static>(work: impl FnOnce() -> T + Send + 'static, done: impl FnOnce(T) + 'static) {
    glib::spawn_future_local(async move {
        if let Ok(r) = gio::spawn_blocking(work).await {
            done(r);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nearest_option() {
        assert_eq!(nearest(&[1.0, 1.25, 1.5, 2.0], 1.75), 1.5); // a tie keeps the first, as min() does
        assert_eq!(nearest(&[1.0, 1.25, 1.5, 2.0], 1.8), 2.0);
        assert_eq!(nearest(&[1.0, 2.0], 0.2), 1.0);
    }

    #[test]
    fn speed_to_slider() {
        assert_eq!(speed("0"), 50.0);
        assert_eq!(speed("-1"), 0.0);
        assert_eq!(speed("0.5"), 75.0);
        assert_eq!(speed("fast"), 50.0);
    }

    /// Vini: Date & Time's switches were off with the date showing -- "Show
    /// the date" looked for "%d", the default format has "%-d".
    #[test]
    fn test_every_format_settings_writes_reads_back() {
        // the menu bar's own reading (topbar.shows_date / is_24h)
        let shows_date = |f: &str| ["%d", "%-d", "%e", "%b", "%B", "%x"].iter().any(|t| f.contains(t));
        let is_24h = |f: &str| ["%H", "%k", "%R", "%T"].iter().any(|t| f.contains(t));
        for date in [true, false] {
            for h24 in [true, false] {
                let f = clock_format(date, h24);
                assert_eq!((shows_date(&f), is_24h(&f)), (date, h24), "{f}");
            }
        }
        assert_eq!(minimize_animation("genie"), "squeezimize");
        assert_eq!(minimize_animation("scale"), "zoom");
    }
}
