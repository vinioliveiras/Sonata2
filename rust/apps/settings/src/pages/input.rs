//! Keyboard (with Keyboard Shortcuts), Mouse & Trackpad, Game Controllers.

use crate::kit::{clear_group, combo_row, group, nearest, run_async, slider_row, speed, switch_row};
use crate::window::Ctx;
use adw::prelude::*;
use gtk::{gio, glib};
use serde_json::{json, Value};
use sonata_backend::{system, wfconfig};
use sonata_i18n::{gettext, n_, tr, trp};
use sonata_tweaks::cursors;
use std::rc::Rc;

// -- [input] -----------------------------------------------------------------------------------
/// A float as Python's str() writes it into the Wayfire config ("0.5",
/// "-1.0"), rounded to two places like the sliders' values were.
fn py_float(x: f64) -> String {
    let x = (x * 100.0).round() / 100.0;
    let x = if x == 0.0 { 0.0 } else { x }; // never "-0.0"
    if x.fract() == 0.0 { format!("{x:.1}") } else { format!("{x}") }
}

fn py_bool(on: bool) -> String {
    (if on { "true" } else { "false" }).to_string()
}

/// The [input:<device>] section names in a Wayfire config's text.
fn sections_in(text: &str, found: &mut Vec<String>) {
    for line in text.lines() {
        let s = line.trim();
        if let Some(name) = s.strip_prefix("[input:").and_then(|r| r.strip_suffix(']')) {
            if !found.iter().any(|f| f == name) {
                found.push(name.to_string());
            }
        }
    }
}

/// system.keyboard_sections (not in sonata_backend yet): the keyboards
/// that have a layout of their own.
fn keyboard_sections() -> Vec<String> {
    let mut found = Vec::new();
    for path in wfconfig::read_files() {
        if let Ok(text) = std::fs::read_to_string(path) {
            sections_in(&text, &mut found);
        }
    }
    found.into_iter().filter(|n| !system::keyboard_device_layout(n).is_empty()).collect()
}

const PER_KEYBOARD_KEYS: [&str; 5] = ["kb_repeat_rate", "kb_repeat_delay", "xkb_options", "xkb_model", "xkb_rules"];

/// system.sync_keyboard_option: a keyboard option changed in Settings, the
/// keyboards with a layout of their own get it too (Wayfire reads only
/// their section).
fn sync_keyboard_option(key: &str, value: &str) {
    if PER_KEYBOARD_KEYS.contains(&key) {
        for n in keyboard_sections() {
            wfconfig::set(&format!("input:{n}"), key, value);
        }
    }
}

/// An [input] option, written off the main loop (the newest value of a
/// dragged slider wins).
fn wf(ctx: &Ctx, key: &'static str, value: String) {
    // (keyboards with a layout of their own: theirs too)
    ctx.latest(&format!("input/{key}"), move || {
        wfconfig::set("input", key, &value);
        sync_keyboard_option(key, &value);
    });
}

fn xkb_name(lay: &str) -> String {
    system::XKB_LAYOUTS.iter().find(|(k, _)| *k == lay).map_or_else(|| lay.to_string(), |(_, n)| gettext(n))
}

// -- Keyboard ------------------------------------------------------------------------------------
pub fn page_keyboard(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let get = wfconfig::get;
    let rep = group("", "");
    let rate: i64 = get("input", "kb_repeat_rate", "40").trim().parse().unwrap_or(40);
    let delay: i64 = get("input", "kb_repeat_delay", "400").trim().parse().unwrap_or(400);
    let w = Rc::downgrade(ctx);
    rep.add(&slider_row(&tr!("Key Repeat"), rate as f64, 2.0, 80.0, move |v| {
        if let Some(c) = w.upgrade() {
            wf(&c, "kb_repeat_rate", (v as i64).to_string());
        }
    }, "", Some((&tr!("Slow"), &tr!("Fast"))), Some(40.0)).0);
    // Delay Until Repeat: Long (left) .. Short (right), like macOS
    let w = Rc::downgrade(ctx);
    rep.add(&slider_row(&tr!("Delay Until Repeat"), (1150 - delay) as f64, 150.0, 1000.0, move |v| {
        if let Some(c) = w.upgrade() {
            wf(&c, "kb_repeat_delay", ((1150.0 - v) as i64).to_string());
        }
    }, "", Some((&tr!("Long"), &tr!("Short"))), Some(750.0)).0);

    let src = group(&tr!("Input Sources"), &tr!("The first one is in use; switch from the input menu in the menu bar, or with Ctrl+Space (the one used before)."));
    let lays = system::keyboard_layouts();
    for (i, lay) in lays.iter().enumerate() {
        let r = adw::ActionRow::builder().title(xkb_name(lay)).subtitle(if i == 0 { tr!("In use") } else { String::new() }).use_markup(false).build();
        let rm = gtk::Button::builder().icon_name("list-remove-symbolic").valign(gtk::Align::Center).css_classes(["flat"]).tooltip_text(tr!("Remove")).sensitive(lays.len() > 1).build();
        let (w, lay) = (Rc::downgrade(ctx), lay.clone());
        rm.connect_clicked(move |_| {
            if let Some(c) = w.upgrade() {
                set_layouts(&c, system::keyboard_layouts().into_iter().filter(|x| *x != lay).collect());
            }
        });
        r.add_suffix(&rm);
        src.add(&r);
    }
    // (a short translation: the pop-up's value is cut past ~20 letters)
    let mut add_opts = vec![(String::new(), trp!("input source combo", "Add Input Source…"))];
    add_opts.extend(system::XKB_LAYOUTS.iter().filter(|(k, _)| !lays.iter().any(|l| l == k)).map(|(k, n)| (k.to_string(), gettext(n))));
    let w = Rc::downgrade(ctx);
    src.add(&combo_row(&tr!("Add"), add_opts, &String::new(), move |v: &String| {
        if let (false, Some(c)) = (v.is_empty(), w.upgrade()) {
            let mut l = system::keyboard_layouts();
            l.push(v.clone());
            set_layouts(&c, l);
        }
    }, ""));
    src.add(&adw::EntryRow::builder().title(tr!("Type here to test")).build());
    vec![rep, src, keyboards_group(ctx)]
}

/// Keyboard Shortcuts (shortcuts_page.rs).
pub fn page_shortcuts(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let page = crate::shortcuts_page::ShortcutsPage::new(ctx);
    let groups = page.groups();
    ctx.keep("shortcuts_page", page);
    groups
}

fn is_builtin_keyboard(name: &str) -> bool {
    name.contains("AT Translated") || name.contains("i8042")
}

/// A layout per keyboard (Vini: a U.S. International USB keyboard on a
/// Portuguese laptop): each connected keyboard follows Input Sources, or
/// has one of its own.
fn keyboards_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let grp = group(&tr!("Keyboards"), &tr!("Each keyboard can have a layout of its own; the others follow Input Sources."));
    grp.add(&adw::ActionRow::builder().title(tr!("Looking for keyboards…")).build());
    let (g, w) = (grp.clone(), Rc::downgrade(ctx));
    run_async(|| {
        let kbds = system::keyboards();
        let own: Vec<String> = kbds.iter().map(|n| system::keyboard_device_layout(n)).collect();
        (kbds, own)
    }, move |(kbds, own)| {
        clear_group(&g);
        if kbds.is_empty() {
            g.add(&adw::ActionRow::builder().title(tr!("No keyboards found")).build());
            return;
        }
        let mut options: Vec<(String, String)> = vec![(String::new(), tr!("Same as Input Sources"))];
        options.extend(system::XKB_LAYOUTS.iter().map(|(k, n)| (k.to_string(), gettext(n))));
        for (name, cur) in kbds.into_iter().zip(own) {
            if !cur.is_empty() && !options.iter().any(|o| o.0 == cur) {
                options.push((cur.clone(), cur.clone()));
            }
            let shown = if is_builtin_keyboard(&name) { tr!("Built-in Keyboard") } else { name.clone() };
            let (w, was) = (w.clone(), cur.clone());
            g.add(&combo_row(&shown, options.clone(), &cur, move |v: &String| {
                let (n, v2) = (name.clone(), v.clone());
                run_async(move || system::set_keyboard_device_layout(&n, &v2), |_| {});
                if v.is_empty() != was.is_empty() {
                    // Wayfire takes a keyboard's own section when it appears
                    if let Some(c) = w.upgrade() {
                        c.toast(&tr!("Unplug the keyboard and plug it in again (or log in again) to use it"));
                    }
                }
            }, ""));
        }
    });
    grp
}

fn set_layouts(ctx: &Ctx, lays: Vec<String>) {
    let w = Rc::downgrade(ctx);
    run_async(move || system::set_keyboard_layouts(&lays), move |_| {
        if let Some(c) = w.upgrade() {
            c.reload_page("keyboard");
        }
    });
}

// -- Trackpad, Mouse ---------------------------------------------------------------------------
/// A Wayfire speed (-1..1) from a slider (0..100).
fn speed_of(v: f64) -> String {
    py_float(v / 50.0 - 1.0)
}

fn wf_switch(ctx: &Ctx, title: &str, key: &'static str, default: &str, subtitle: &str) -> adw::SwitchRow {
    let w = Rc::downgrade(ctx);
    switch_row(title, wfconfig::get("input", key, default) == "true", move |on| {
        if let Some(c) = w.upgrade() {
            wf(&c, key, py_bool(on));
        }
    }, subtitle)
}

fn speed_row(ctx: &Ctx, key: &'static str) -> adw::ActionRow {
    let w = Rc::downgrade(ctx);
    slider_row(&tr!("Tracking speed"), speed(&wfconfig::get("input", key, "0")), 0.0, 100.0, move |v| {
        if let Some(c) = w.upgrade() {
            wf(&c, key, speed_of(v));
        }
    }, "", Some((&tr!("Slow"), &tr!("Fast"))), Some(50.0)).0
}

pub fn page_trackpad(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    // one section shows both: every group says which device it's for (Vini)
    let g = group(&tr!("Trackpad: Point & Click"), "");
    g.add(&speed_row(ctx, "touchpad_cursor_speed"));
    g.add(&wf_switch(ctx, &tr!("Tap to click"), "tap_to_click", "true", &tr!("Tap with one finger")));
    g.add(&wf_switch(ctx, &tr!("Tap and drag"), "tap_and_drag", "true", ""));
    let sc = group(&tr!("Trackpad: Scrolling"), "");
    sc.add(&wf_switch(ctx, &tr!("Natural scrolling"), "natural_scroll", "false", &tr!("Content tracks finger movement")));
    sc.add(&wf_switch(ctx, &tr!("Ignore trackpad while typing"), "disable_touchpad_while_typing", "false", ""));
    vec![g, sc]
}

pub fn page_mouse(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let g = group(&tr!("Mouse"), "");
    g.add(&speed_row(ctx, "mouse_cursor_speed"));
    let scroll: f64 = wfconfig::get("input", "mouse_scroll_speed", "1").trim().parse().unwrap_or(1.0);
    let w = Rc::downgrade(ctx);
    g.add(&slider_row(&tr!("Scrolling speed"), scroll * 50.0, 5.0, 150.0, move |v| {
        if let Some(c) = w.upgrade() {
            wf(&c, "mouse_scroll_speed", py_float(v / 50.0));
        }
    }, "", Some((&tr!("Slow"), &tr!("Fast"))), Some(50.0)).0);
    g.add(&wf_switch(ctx, &tr!("Natural scrolling"), "mouse_natural_scroll", "false", &tr!("Content tracks finger movement")));
    let w = Rc::downgrade(ctx);
    g.add(&combo_row(&tr!("Primary mouse button"), vec![(false, tr!("Left")), (true, tr!("Right"))],
        &(wfconfig::get("input", "left_handed_mode", "false") == "true"), move |v: &bool| {
            if let Some(c) = w.upgrade() {
                wf(&c, "left_handed_mode", py_bool(*v));
            }
        }, ""));
    vec![g, pointer_group(ctx)]
}

// -- the pointer (cursors): its look, a downloaded one, its size ---------------------------------
/// Pointer size (also in Accessibility).
pub fn pointer_size_row(ctx: &Ctx) -> adw::ComboRow {
    let opts: Vec<(i64, String)> = cursors::SIZES.iter().map(|&px| (px, match px {
        24 => tr!("Normal"),
        32 => tr!("Large"),
        48 => tr!("Larger"),
        _ => tr!("Largest"),
    })).collect();
    let values: Vec<f64> = opts.iter().map(|o| o.0 as f64).collect();
    let now = nearest(&values, cursors::size() as f64) as i64;
    let w = Rc::downgrade(ctx);
    combo_row(&tr!("Pointer size"), opts, &now, move |v: &i64| {
        let (px, w) = (*v, w.clone());
        run_async(move || cursors::apply(None, Some(px)), move |_| pointer_changed(&w));
    }, "")
}

fn pointer_group(ctx: &Ctx) -> adw::PreferencesGroup {
    let g = group(&tr!("Pointer"), "");
    let mut found = cursors::themes();
    let now = cursors::current();
    if !found.iter().any(|(n, _)| *n == now) {
        found.push((now.clone(), now.clone())); // set, but its folder is gone: still shown
    }
    // (a name)
    let opts: Vec<(String, String)> = found.into_iter().map(|(n, l)| {
        let label = if n == cursors::DEFAULT { "Sonata".to_string() } else { l };
        (n, label)
    }).collect();
    let w = Rc::downgrade(ctx);
    g.add(&combo_row(&tr!("Pointer style"), opts, &now, move |v: &String| {
        let (name, w) = (v.clone(), w.clone());
        run_async(move || cursors::apply(Some(&name), None), move |_| pointer_changed(&w));
    }, &tr!("Pointer themes installed on this computer")));
    g.add(&pointer_size_row(ctx));

    let add = adw::ActionRow::builder().title(tr!("Add a pointer theme")).subtitle(tr!("A downloaded file (.tar.gz, .tar.xz or .zip) or a folder")).build();
    let w = Rc::downgrade(ctx);
    let btn = sonata_ui::controls::push_button(&tr!("Add…"), move || {
        if let Some(c) = w.upgrade() {
            add_pointer(&c);
        }
    }, "");
    btn.set_valign(gtk::Align::Center);
    add.add_suffix(&btn);
    g.add(&add);

    let back = adw::ActionRow::builder().title(tr!("Sonata's pointer")).subtitle(tr!("Its own look at the normal size")).build();
    let w = Rc::downgrade(ctx);
    let rb = sonata_ui::controls::push_button(&tr!("Restore Defaults"), move || {
        let w = w.clone();
        run_async(cursors::reset, move |_| pointer_changed(&w));
    }, "");
    rb.set_valign(gtk::Align::Center);
    rb.set_sensitive(now != cursors::DEFAULT || cursors::size() != cursors::DEFAULT_SIZE);
    back.add_suffix(&rb);
    g.add(&back);
    g
}

/// Both places that show the pointer's settings, again.
fn pointer_changed(w: &std::rc::Weak<crate::window::Settings>) {
    if let Some(c) = w.upgrade() {
        for sid in ["mouse", "accessibility"] {
            c.rebuild_page(sid); // (a section not built yet is left alone)
        }
    }
}

fn add_pointer(ctx: &Ctx) {
    let filt = gtk::FileFilter::new();
    filt.set_name(Some(&tr!("Pointer themes")));
    for ext in cursors::ARCHIVES {
        filt.add_pattern(&format!("*{ext}"));
    }
    let filters = gio::ListStore::new::<gtk::FileFilter>();
    filters.append(&filt);
    let dlg = gtk::FileDialog::builder().title(tr!("Add a Pointer Theme")).modal(true).filters(&filters).default_filter(&filt).build();
    let w = Rc::downgrade(ctx);
    let chosen = move |res: Result<gio::File, glib::Error>| {
        let Some(path) = res.ok().and_then(|f| f.path()) else { return };
        let w = w.clone();
        let path = path.to_string_lossy().into_owned();
        run_async(move || cursors::add(&path).unwrap_or_default(), move |names| {
            let Some(c) = w.upgrade() else { return };
            let Some(first) = names.first() else {
                sonata_ui::dialog::alert(&tr!("No pointer theme was found there."),
                    &tr!("A pointer theme is a folder with a “cursors” folder inside, or a file that holds one."),
                    &[("ok", tr!("OK"), "default")], |_| {}, c.parent().as_ref());
                return;
            };
            cursors::apply(Some(first), None);
            pointer_changed(&w);
            c.toast(&tr!("Pointer theme added"));
        });
    };
    let win: gtk::Window = ctx.window.clone().upcast();
    sonata_ui::dialog::alert(&tr!("Add a pointer theme"), &tr!("From a downloaded file, or from a folder?"),
        &[("cancel", tr!("Cancel"), ""), ("folder", tr!("Folder…"), ""), ("file", tr!("File…"), "default")],
        move |rid| {
            let chosen = chosen.clone();
            match rid {
                "file" => dlg.open(Some(&win), gio::Cancellable::NONE, chosen),
                "folder" => dlg.select_folder(Some(&win), gio::Cancellable::NONE, chosen),
                _ => {}
            }
        }, ctx.parent().as_ref());
}

// -- Game Controllers ----------------------------------------------------------------------------
/// gamepad/evdev.py's lookup (the gamepad service isn't in Rust yet): the
/// controllers in /dev/input and their names, read with the kernel's ioctls.
mod evdev {
    use std::os::unix::fs::OpenOptionsExt;
    use std::os::unix::io::AsRawFd;

    const EV_KEY: u32 = 0x01;
    const EV_ABS: u32 = 0x03;
    pub const BTN_GAMEPAD: usize = 0x130; // BTN_SOUTH
    pub const ABS_X: usize = 0x00;
    pub const ABS_Y: usize = 0x01;

    fn ioc_read(nr: u32, size: usize) -> libc::c_ulong {
        (2 << 30) | ((size as libc::c_ulong) << 16) | ((b'E' as libc::c_ulong) << 8) | nr as libc::c_ulong
    }

    fn open(path: &str) -> Option<std::fs::File> {
        std::fs::OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK).open(path).ok()
    }

    fn bits(f: &std::fs::File, ev_type: u32, count: usize) -> Vec<u8> {
        let mut buf = vec![0u8; count.div_ceil(8)];
        // SAFETY: EVIOCGBIT writes at most buf.len() bytes (the size is in the request)
        unsafe { libc::ioctl(f.as_raw_fd(), ioc_read(0x20 + ev_type, buf.len()) as _, buf.as_mut_ptr()) };
        buf
    }

    pub fn has(bits: &[u8], code: usize) -> bool {
        bits.get(code / 8).is_some_and(|b| b & (1 << (code % 8)) != 0)
    }

    fn name_of(f: &std::fs::File) -> String {
        let mut buf = vec![0u8; 256];
        // SAFETY: EVIOCGNAME writes at most buf.len() bytes
        unsafe { libc::ioctl(f.as_raw_fd(), ioc_read(0x06, buf.len()) as _, buf.as_mut_ptr()) };
        let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
        String::from_utf8_lossy(&buf[..end]).into_owned()
    }

    /// A device with the gamepad's south button and two stick axes.
    fn is_gamepad(path: &str) -> bool {
        let Some(f) = open(path) else { return false };
        let (keys, axes) = (bits(&f, EV_KEY, 0x300), bits(&f, EV_ABS, 0x40));
        has(&keys, BTN_GAMEPAD) && has(&axes, ABS_X) && has(&axes, ABS_Y)
    }

    pub fn find_gamepads() -> Vec<String> {
        let Ok(rd) = std::fs::read_dir("/dev/input") else { return Vec::new() };
        let mut names: Vec<String> = rd.flatten().map(|e| e.file_name().to_string_lossy().into_owned()).filter(|n| n.starts_with("event")).collect();
        names.sort();
        names.into_iter().map(|n| format!("/dev/input/{n}")).filter(|p| is_gamepad(p)).collect()
    }

    pub fn gamepad_name(path: &str) -> String {
        open(path).map(|f| name_of(&f)).unwrap_or_default()
    }
}

/// gamepad/service.py's DEFAULTS.
fn gamepad_defaults() -> Value {
    json!({"enabled": false, "speed": 1.0, "scroll": 1.0, "pause_steam": true, "guide_gamebar": true})
}

/// What each button does, as Settings lists it (gamepad/service.py's
/// LEGEND: Xbox / PlayStation names; shown translated).
const LEGEND: [(&str, &str); 12] = [
    (n_!("Left stick"), n_!("Pointer (LT slower, RT faster)")),
    (n_!("Right stick"), n_!("Scroll")),
    (n_!("A / Cross"), n_!("Click (hold to drag)")),
    (n_!("X / Triangle"), n_!("Right-click")),
    (n_!("B / Circle"), n_!("Back (Esc)")),
    (n_!("Y / Square"), sonata_desktop::names::APPS),
    ("LB / RB", n_!("Switch apps")),
    (n_!("D-pad"), n_!("Arrow keys")),
    (n_!("Start / Options"), sonata_desktop::names::SEARCH),
    (n_!("View / Share"), sonata_desktop::names::OVERVIEW),
    (n_!("Xbox / PS button"), n_!("Game Bar")),
    ("LB + RB + L3 + R3", n_!("Desktop control on / off")),
];

fn fill_pads(ctx: &Ctx, pads: &adw::PreferencesGroup) {
    clear_group(pads);
    let found: Vec<String> = evdev::find_gamepads().iter().map(|p| {
        Some(evdev::gamepad_name(p)).filter(|n| !n.is_empty()).unwrap_or_else(|| tr!("Game Controller"))
    }).collect();
    for name in &found {
        let row = adw::ActionRow::builder().title(name.as_str()).subtitle(tr!("Connected")).use_markup(false).build();
        row.add_prefix(&gtk::Image::builder().icon_name("input-gaming-symbolic").pixel_size(24).build());
        pads.add(&row);
    }
    if found.is_empty() {
        let row = adw::ActionRow::builder().title(tr!("No controller connected")).subtitle(tr!("Plug one in with a cable, or pair it in Bluetooth")).build();
        let w = Rc::downgrade(ctx);
        let bt = sonata_ui::controls::push_button(&tr!("Bluetooth…"), move || {
            if let Some(c) = w.upgrade() {
                c.select("bluetooth");
            }
        }, "");
        bt.set_valign(gtk::Align::Center);
        row.add_suffix(&bt);
        pads.add(&row);
    }
}

fn save_switch(ctx: &Ctx, title: &str, key: &'static str, on: bool, subtitle: &str) -> adw::SwitchRow {
    let w = Rc::downgrade(ctx);
    switch_row(title, on, move |on| {
        if let Some(c) = w.upgrade() {
            c.save("gamepad", key, json!(on));
        }
    }, subtitle)
}

fn save_slider(ctx: &Ctx, title: &str, key: &'static str, value: f64) -> adw::ActionRow {
    let w = Rc::downgrade(ctx);
    slider_row(title, value * 50.0, 10.0, 100.0, move |v| {
        if let Some(c) = w.upgrade() {
            c.save_live("gamepad", key, json!(((v / 50.0) * 100.0).round() / 100.0));
        }
    }, "", Some((&tr!("Slow"), &tr!("Fast"))), Some(50.0)).0
}

/// Like macOS Game Controllers: the connected ones, then what they do on
/// the desktop (sonata2/gamepad; paused in fullscreen games).
pub fn page_gamepad(ctx: &Ctx) -> Vec<adw::PreferencesGroup> {
    let gp = sonata_config::load("gamepad", &gamepad_defaults());
    let b = |k: &str, d: bool| gp.get(k).and_then(Value::as_bool).unwrap_or(d);
    let f = |k: &str| gp.get(k).and_then(Value::as_f64).unwrap_or(1.0);
    let pads = group(&tr!("Controllers"), "");
    fill_pads(ctx, &pads);
    if let Ok(mon) = gio::File::for_path("/dev/input").monitor_directory(gio::FileMonitorFlags::NONE, gio::Cancellable::NONE) {
        let (w, p) = (Rc::downgrade(ctx), pads.downgrade());
        mon.connect_changed(move |_, _, _, _| {
            let (w, p) = (w.clone(), p.clone());
            // (a new device's node is readable a moment after it appears)
            glib::timeout_add_local_once(std::time::Duration::from_millis(700), move || {
                if let (Some(c), Some(p)) = (w.upgrade(), p.upgrade()) {
                    fill_pads(&c, &p);
                }
            });
        });
        ctx.keep("gamepad_monitor", Rc::new(mon)); // lives as long as the page (a rebuild replaces it)
    }

    let desk = group(&tr!("Desktop Control"), &tr!("Use a controller as mouse and keyboard when no game is running. It pauses by itself while a game fills the screen."));
    desk.add(&save_switch(ctx, &tr!("Control the desktop with a controller"), "enabled", b("enabled", false), ""));
    let hint = adw::ActionRow::builder().title(tr!("Shortcut on the controller"))
        .subtitle(tr!("Hold LB + RB + L3 + R3 (L1 + R1 + L3 + R3) together to turn it on or off, even in a game")).build();
    hint.add_prefix(&gtk::Image::builder().icon_name("input-gaming-symbolic").pixel_size(16).build());
    desk.add(&hint);
    desk.add(&save_slider(ctx, &tr!("Pointer speed"), "speed", f("speed")));
    desk.add(&save_slider(ctx, &tr!("Scrolling speed"), "scroll", f("scroll")));
    desk.add(&save_switch(ctx, &tr!("Pause while Steam is open"), "pause_steam", b("pause_steam", true),
        &tr!("Steam has its own desktop controls: both would act on every press")));
    let bar = group(&tr!("Game Bar"), &tr!("Capture, audio and performance over the game, like the Xbox Game Bar."));
    bar.add(&save_switch(ctx, &tr!("Xbox / PS button opens the Game Bar"), "guide_gamebar", b("guide_gamebar", true),
        &tr!("In games too. Off: the button shows the Overview while desktop control is on")));
    let keys = group(&tr!("Buttons"), "");
    for (button, does) in LEGEND {
        let row = adw::ActionRow::builder().title(gettext(button)).use_markup(false).build();
        row.add_suffix(&gtk::Label::builder().label(gettext(does)).xalign(1.0).css_classes(["dim-label"]).build());
        keys.add(&row);
    }
    // the Game Bar group too: app.py builds it and leaves it out of the page
    vec![pads, desk, bar, keys]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn floats_written_as_python_did() {
        assert_eq!(py_float(0.5), "0.5");
        assert_eq!(py_float(-1.0), "-1.0");
        assert_eq!(py_float(0.0), "0.0");
        assert_eq!(py_float(-0.001), "0.0");
        assert_eq!(speed_of(75.0), "0.5");
        assert_eq!(speed_of(0.0), "-1.0");
        assert_eq!(py_float(37.0 / 50.0), "0.74");
        assert_eq!(py_bool(true), "true");
    }

    #[test]
    fn keyboard_sections_read_from_the_config() {
        let mut found = Vec::new();
        sections_in("[input]\nxkb_layout = pt\n[input:HyperX Alloy Origins 65]\nxkb_layout = us\n[input-method]\n", &mut found);
        sections_in("  [input:HyperX Alloy Origins 65]  \n[input:Other]\n", &mut found);
        assert_eq!(found, ["HyperX Alloy Origins 65", "Other"]);
    }

    #[test]
    fn built_in_keyboard_named() {
        assert!(is_builtin_keyboard("AT Translated Set 2 keyboard"));
        assert!(is_builtin_keyboard("i8042 keyboard"));
        assert!(!is_builtin_keyboard("HyperX Alloy Origins 65"));
    }

    /// test_keyboard_layouts_per_device's: the page has its Keyboards
    /// group, and an option written keeps the own-layout keyboards in step.
    #[test]
    fn test_settings_page_has_it() {
        let src = include_str!("input.rs");
        assert!(src.contains("vec![rep, src, keyboards_group(ctx)]"));
        assert!(src.contains("sync_keyboard_option(key, &value);"));
    }

    #[test]
    fn evdev_bits() {
        let mut keys = vec![0u8; 0x300 / 8];
        assert!(!evdev::has(&keys, evdev::BTN_GAMEPAD));
        keys[evdev::BTN_GAMEPAD / 8] |= 1 << (evdev::BTN_GAMEPAD % 8);
        assert!(evdev::has(&keys, evdev::BTN_GAMEPAD));
        assert!(!evdev::has(&[], evdev::ABS_X));
        assert!(evdev::has(&[0b11], evdev::ABS_Y));
    }

    #[test]
    fn gamepad_defaults_as_the_service() {
        let d = gamepad_defaults();
        assert_eq!(d["enabled"], json!(false));
        assert_eq!(d["guide_gamebar"], json!(true));
        assert_eq!(LEGEND.len(), 12);
    }
}
