//! About This Computer (Big Sur's About This Mac: Overview, Displays,
//! Storage and Support, tabs in the title bar) and an app's About window,
//! from the menu bar's menus (topbar.py's AboutWindow and AboutAppWindow).

use crate::kit::{self, run_async};
use adw::prelude::*;
use gtk::{gdk, gio, glib};
use sonata_backend::system;
use sonata_i18n::{fmt, gettext, language, n_, tr};
use std::path::Path;

/// Big Sur's About This Mac (logical px): a fixed window. Grows only when
/// a language's longer words need it (small windows show everything).
const SIZE: (i32, i32) = (700, 420);

/// The tabs in the title bar: (id, name). Big Sur's Service tab has
/// nothing to show here (no warranty to look up).
pub const TABS: [(&str, &str); 4] = [("overview", n_!("Overview")), ("displays", n_!("Displays")), ("storage", n_!("Storage")), ("support", n_!("Support"))];

const REPO: &str = "https://github.com/vinioliveiras/sonata2";

/// Lets a small window shrink below its content (scrolls instead).
fn scroller(child: &impl IsA<gtk::Widget>) -> gtk::ScrolledWindow {
    gtk::ScrolledWindow::builder().child(child).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).propagate_natural_width(true).build()
}

/// zoom: Some(true) maximizes; None greys the green button (a fixed window).
fn frame(title: &str, width: i32, zoom: Option<bool>) -> (adw::Window, gtk::WindowHandle) {
    let win = adw::Window::builder().title(title).default_width(width).build();
    for c in ["sonata-about", "sonata-glass-window"] {
        win.add_css_class(c); // frosted glass, like the Dock
    }
    sonata_ui::window::standard(&win);
    // topbar.py's ui.window.titlebar(self, zoom=True): the window buttons
    // (Vini: no close button, and a black glass toolbar over the glass)
    let head = sonata_ui::window::titlebar(&win, None, None, zoom);
    (win, head)
}

fn memory_text(a: &system::About) -> String {
    fmt(&tr!("{n} GB"), &[("n", &a.memory_gb.to_string())])
}

fn graphics_text(a: &system::About) -> String {
    if a.gpus.is_empty() { "—".to_string() } else { a.gpus.join(", ") }
}

/// The rows of About This Computer: (key, value).
pub fn rows(a: &system::About) -> Vec<(String, String)> {
    vec![
        (tr!("Computer"), a.machine.clone()),
        (tr!("Processor"), a.cpu.clone()),
        (tr!("Memory"), memory_text(a)),
        (tr!("Graphics"), graphics_text(a)),
        (tr!("Kernel"), a.kernel.clone()),
        (tr!("Desktop"), "Sonata 2".to_string()),
    ]
}

/// Overview's bold-key rows under the computer's model (Big Sur's Chip,
/// Memory, Graphics, Serial Number), the system it runs on included: the
/// big name is Sonata's now. No serial row when it can't be read.
pub fn overview_rows(a: &system::About, serial: Option<&str>) -> Vec<(String, String)> {
    let mut r = vec![
        (tr!("Processor"), a.cpu.clone()),
        (tr!("Memory"), memory_text(a)),
        (tr!("Graphics"), graphics_text(a)),
        (tr!("System"), a.os_name.clone()),
        (tr!("Kernel"), a.kernel.clone()),
    ];
    if let Some(s) = serial {
        r.push((tr!("Serial Number"), s.to_string()));
    }
    r
}

/// "Version 0.22.0-alpha": this build's (the VERSION file it was made from).
pub fn version_text() -> String {
    fmt(&tr!("Version {version}"), &[("version", sonata_config::version())])
}

/// The serial number as the firmware gives it, when it is one: makers
/// leave placeholders ("To be filled by O.E.M.", "Default string", zeros).
pub fn serial_shown(raw: &str) -> Option<String> {
    let s = raw.trim();
    let lower = s.to_lowercase();
    let filler = ["to be filled", "default string", "system serial number", "not specified", "not applicable", "none", "n/a", "serial", "0123456789"];
    let blank = s.is_empty() || s.chars().all(|c| c == '0' || c == ' ' || c == '.' || c == '-') || filler.iter().any(|f| lower.starts_with(f));
    (!blank).then(|| s.to_string())
}

/// Only root can read it on most machines: then there is no row.
fn read_serial() -> Option<String> {
    std::fs::read_to_string("/sys/class/dmi/id/product_serial").ok().and_then(|s| serial_shown(&s))
}

pub struct AboutWindow;

impl AboutWindow {
    pub fn open() {
        let (win, head) = frame(&tr!("About This Computer"), SIZE.0, None);
        win.set_default_height(SIZE.1);
        win.set_size_request(SIZE.0, SIZE.1);
        win.set_resizable(false);
        let pages = gtk::Stack::builder().vexpand(true).transition_type(gtk::StackTransitionType::Crossfade).build();
        pages.add_named(&overview(), Some("overview"));
        pages.add_named(&displays(), Some("displays"));
        pages.add_named(&storage(), Some("storage"));
        pages.add_named(&support(), Some("support"));
        let choices: Vec<(&str, String)> = TABS.iter().map(|(k, t)| (*k, gettext(t))).collect();
        let p = pages.downgrade();
        let tabs = sonata_ui::controls::Segmented::new(&choices, Some("overview"), move |id: &str| {
            if let Some(p) = p.upgrade() {
                p.set_visible_child_name(id);
            }
        }, true);
        tabs.root.set_valign(gtk::Align::Center);
        if let Some(bar) = head.child().and_downcast::<gtk::CenterBox>() {
            bar.set_center_widget(Some(&tabs.root));
        }
        pages.connect_visible_child_name_notify(move |p| tabs.select(&p.visible_child_name().unwrap_or_default()));
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&head);
        col.append(&gtk::WindowHandle::builder().child(&pages).vexpand(true).build()); // drag from anywhere, like macOS
        win.set_content(Some(&col));
        win.present();
    }
}

/// A Big Sur push button at a page's bottom right.
fn footer(buttons: &[gtk::Button]) -> gtk::Box {
    let row = gtk::Box::builder().spacing(12).halign(gtk::Align::End).css_classes(["about-footer"]).build();
    for b in buttons {
        row.append(b);
    }
    row
}

fn page() -> gtk::Box {
    gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["about-box"]).build()
}

// -- Overview -------------------------------------------------------------------------------------

/// The logo, "Sonata 2", the version, the computer and its parts, System
/// Report… (Task Manager) and Software Update… (Settings).
fn overview() -> gtk::Box {
    let body = gtk::Box::builder().spacing(48).halign(gtk::Align::Center).valign(gtk::Align::Center).vexpand(true).build();
    let logo = gtk::Image::builder().pixel_size(168).valign(gtk::Align::Center).build();
    sonata_ui::icons::set_logo(&logo); // the distro's logo (os-release LOGO=)
    body.append(&logo);
    let info = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).valign(gtk::Align::Center).build();
    body.append(&info);
    // "macOS Big Sur": the name bold, the number light (a brand: not translated)
    let name = gtk::Label::builder().label("<b>Sonata</b> <span weight=\"light\">2</span>").use_markup(true).xalign(0.0).css_classes(["about-name"]).build();
    info.append(&name);
    info.append(&gtk::Label::builder().label(version_text()).xalign(0.0).css_classes(["about-version"]).build());
    let specs = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).build();
    info.append(&specs);
    let report = sonata_ui::controls::push_button(&tr!("System Report…"), || kit::spawn_self(&["activity"]), "");
    let update = sonata_ui::controls::push_button(&tr!("Software Update…"), || kit::open_settings("sonataupdate"), "");
    let buttons = gtk::Box::builder().spacing(12).css_classes(["about-buttons"]).build();
    buttons.append(&report);
    buttons.append(&update);
    info.append(&buttons);
    let s = specs.downgrade();
    run_async(|| (system::about(), read_serial()), move |(a, serial)| {
        let Some(specs) = s.upgrade() else { return };
        specs.append(&gtk::Label::builder().label(&a.machine).xalign(0.0).css_classes(["about-model"]).visible(!a.machine.is_empty()).build());
        for (key, value) in overview_rows(&a, serial.as_deref()) {
            let row = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            row.append(&gtk::Label::builder().label(&key).css_classes(["about-key"]).valign(gtk::Align::Start).build());
            // wraps instead of growing the window sideways (long GPU names)
            row.append(&gtk::Label::builder().label(&value).xalign(0.0).wrap(true).natural_wrap_mode(gtk::NaturalWrapMode::None).max_width_chars(40).hexpand(true).build());
            specs.append(&row);
        }
    });
    let p = page();
    p.append(&body);
    p
}

// -- Displays -------------------------------------------------------------------------------------

/// A display's name: the panel of a laptop is its Built-in Display (like
/// macOS), a monitor its model from the EDID, else its connector.
pub fn display_name(builtin: bool, model: &str, connector: &str) -> String {
    if builtin {
        tr!("Built-in Display")
    } else if !model.trim().is_empty() {
        model.trim().to_string()
    } else {
        connector.to_string()
    }
}

/// The diagonal in inches, when the display reports a real size (TVs and
/// projectors make one up: then None).
pub fn diagonal_inches(w_px: i32, h_px: i32, w_mm: i32, h_mm: i32) -> Option<f64> {
    sonata_sessionui::monitors::physical_dpi(w_px, h_px, w_mm, h_mm)?;
    let (w, h) = (w_mm as f64, h_mm as f64);
    Some((w * w + h * h).sqrt() / 25.4)
}

/// No trailing zeros ("2", "1.25"), written the language's way ("15,6"
/// in Portuguese).
fn number(v: f64) -> String {
    let t = format!("{:.2}", (v * 100.0).round() / 100.0);
    let t = t.trim_end_matches('0').trim_end_matches('.');
    if matches!(language().as_str(), "pt_BR" | "pt_PT") { t.replace('.', ",") } else { t.to_string() }
}

/// "27-inch (2560 × 1440)", or the resolution alone without a real size.
pub fn display_size_text(inches: Option<f64>, w_px: i32, h_px: i32) -> String {
    let res = format!("{w_px} × {h_px}");
    match inches {
        Some(i) => fmt(&tr!("{inches}-inch ({resolution})"), &[("inches", &number((i * 10.0).round() / 10.0)), ("resolution", &res)]),
        None => res,
    }
}

/// "144 Hz, scale 1.25" (no rate known: the scale alone).
pub fn display_detail_text(refresh_mhz: i32, scale: f64) -> String {
    let scale = number(scale);
    if refresh_mhz > 0 {
        fmt(&tr!("{rate} Hz, scale {scale}"), &[("rate", &((refresh_mhz as f64 / 1000.0).round() as i64).to_string()), ("scale", &scale)])
    } else {
        fmt(&tr!("Scale {scale}"), &[("scale", &scale)])
    }
}

fn display_card(m: &gdk::Monitor) -> gtk::Box {
    let d = sonata_sessionui::monitors::display_of(m);
    let builtin = system::is_builtin(Some(&d.connector)) && !d.connector.is_empty();
    let card = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(2).valign(gtk::Align::Center).build();
    let icon = if builtin { "computer-laptop" } else { "video-display" };
    card.append(&gtk::Image::builder().icon_name(icon).pixel_size(112).css_classes(["about-display-icon"]).build());
    let model = m.model().map(|s| s.to_string()).unwrap_or_default();
    card.append(&gtk::Label::builder().label(display_name(builtin, &model, &d.connector)).css_classes(["about-key"]).justify(gtk::Justification::Center).wrap(true).max_width_chars(28).build());
    let inches = diagonal_inches(d.width, d.height, d.w_mm, d.h_mm);
    card.append(&gtk::Label::builder().label(display_size_text(inches, d.width, d.height)).build());
    let scale = sonata_sessionui::monitors::monitor_scale(m);
    card.append(&gtk::Label::builder().label(display_detail_text(m.refresh_rate(), scale)).css_classes(["about-detail"]).build());
    card
}

/// Each connected display, side by side (they come and go: kept current).
fn displays() -> gtk::Box {
    let row = gtk::Box::builder().spacing(40).halign(gtk::Align::Center).valign(gtk::Align::Center).vexpand(true).build();
    let fill = {
        let r = row.downgrade();
        move || {
            let Some(row) = r.upgrade() else { return };
            while let Some(c) = row.first_child() {
                row.remove(&c);
            }
            for m in sonata_sessionui::monitors::list() {
                row.append(&display_card(&m));
            }
        }
    };
    fill();
    if let Some(d) = gdk::Display::default() {
        // a new display's connector arrives a moment after items-changed
        let model = d.monitors();
        let id = model.connect_items_changed(move |_, _, _, _| {
            glib::timeout_add_local_once(std::time::Duration::from_millis(300), fill.clone());
        });
        // (the list outlives the window: let go of it with the window)
        let id = std::cell::Cell::new(Some(id));
        row.connect_destroy(move |_| {
            if let Some(id) = id.take() {
                model.disconnect(id);
            }
        });
    }
    let p = page();
    p.append(&row);
    p.append(&footer(&[sonata_ui::controls::push_button(&tr!("Displays Settings…"), || kit::open_settings("displays"), "")]));
    p
}

// -- Storage --------------------------------------------------------------------------------------

/// A disk About lists, its space in bytes.
#[derive(Clone, Debug, PartialEq)]
pub struct Volume {
    pub name: String,
    pub mount: String,
    pub total: u64,
    pub free: u64,
    pub removable: bool,
}

/// /proc/mounts writes a space as \040 (and a tab, \n, \\ the same way);
/// by-label links write one as \x20.
fn unescape(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        let code = if b[i] == b'\\' { b.get(i + 1..i + 4) } else { None }.and_then(|t| match t[0] {
            b'x' => std::str::from_utf8(&t[1..]).ok().and_then(|h| u8::from_str_radix(h, 16).ok()),
            _ => std::str::from_utf8(t).ok().and_then(|o| u8::from_str_radix(o, 8).ok()),
        });
        if let Some(c) = code {
            out.push(c);
            i += 4;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The system's own corners: boot partitions, /var, snaps, temporary folders.
fn system_mount(mp: &str) -> bool {
    ["/boot", "/efi", "/var", "/usr", "/tmp", "/snap", "/recovery", "/swap"].iter().any(|p| mp == *p || mp.starts_with(&format!("{p}/")))
}

/// (device, mount point) of the disks About This Computer lists, from
/// /proc/self/mounts: local disks, each once (btrfs mounts one at / and
/// /home), not the system's own corners nor snaps' squashfs; / first,
/// /home next, then as mounted.
pub fn listed_mounts(mounts: &str) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = Vec::new();
    for line in mounts.lines() {
        let f: Vec<&str> = line.split_whitespace().collect();
        let [dev, mp, fs, ..] = f[..] else { continue };
        let mp = unescape(mp);
        if !dev.starts_with("/dev/") || ["/dev/loop", "/dev/zram", "/dev/ram"].iter().any(|p| dev.starts_with(p)) || ["squashfs", "erofs", "swap"].contains(&fs) || system_mount(&mp) {
            continue;
        }
        if out.iter().any(|(d, _)| d == dev) {
            continue;
        }
        out.push((dev.to_string(), mp));
    }
    out.sort_by_key(|(_, mp)| match mp.as_str() {
        "/" => 0,
        "/home" => 1,
        _ => 2,
    });
    out
}

/// Its label, else what it is: the system's disk, the home folders' disk,
/// or the folder it's mounted on.
pub fn volume_name(mount: &str, label: Option<&str>) -> String {
    match (label.map(str::trim).filter(|l| !l.is_empty()), mount) {
        (Some(l), _) => l.to_string(),
        (None, "/") => tr!("System Disk"),
        (None, "/home") => tr!("Home Folders"),
        (None, mp) => Path::new(mp).file_name().map_or_else(|| mp.to_string(), |n| n.to_string_lossy().into_owned()),
    }
}

/// Plugged in (USB sticks, SD cards): mounted where the desktop mounts those.
pub fn is_removable(mount: &str) -> bool {
    ["/run/media/", "/media/"].iter().any(|p| mount.starts_with(p))
}

/// "120,4 GB available of 512,1 GB" (Big Sur's line, Finder's sizes).
pub fn storage_text(free: u64, total: u64) -> String {
    let size = sonata_ui::fmt::size;
    fmt(&tr!("{free} available of {total}"), &[("free", &size(free)), ("total", &size(total))])
}

/// The used part of the bar, 0..1.
pub fn used_fraction(free: u64, total: u64) -> f64 {
    if total == 0 { 0.0 } else { (total.saturating_sub(free)) as f64 / total as f64 }
}

fn label_of(dev: &str) -> Option<String> {
    let dev = std::fs::canonicalize(dev).ok()?;
    std::fs::read_dir("/dev/disk/by-label").ok()?.flatten().find(|e| std::fs::canonicalize(e.path()).is_ok_and(|p| p == dev)).map(|e| unescape(&e.file_name().to_string_lossy()))
}

fn space_of(mount: &str) -> Option<(u64, u64)> {
    let c = std::ffi::CString::new(mount).ok()?;
    let mut s: libc::statvfs = unsafe { std::mem::zeroed() };
    (unsafe { libc::statvfs(c.as_ptr(), &mut s) } == 0).then(|| (s.f_blocks as u64 * s.f_frsize as u64, s.f_bavail as u64 * s.f_frsize as u64))
}

/// The disks and their space (off the main loop: a sleeping disk answers slowly).
fn volumes() -> Vec<Volume> {
    let mounts = std::fs::read_to_string("/proc/self/mounts").unwrap_or_default();
    listed_mounts(&mounts)
        .into_iter()
        .filter_map(|(dev, mount)| {
            let (total, free) = space_of(&mount).filter(|(t, _)| *t > 0)?;
            Some(Volume { name: volume_name(&mount, label_of(&dev).as_deref()), removable: is_removable(&mount), mount, total, free })
        })
        .collect()
}

fn volume_row(v: &Volume) -> gtk::Box {
    let row = gtk::Box::builder().spacing(16).build();
    let icon = if v.removable { "drive-removable-media" } else { "drive-harddisk" };
    row.append(&gtk::Image::builder().icon_name(icon).pixel_size(64).valign(gtk::Align::Center).build());
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).hexpand(true).valign(gtk::Align::Center).build();
    let top = gtk::Box::new(gtk::Orientation::Horizontal, 12);
    top.append(&gtk::Label::builder().label(&v.name).xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::Middle).tooltip_text(&v.mount).css_classes(["about-key"]).build());
    top.append(&gtk::Label::builder().label(storage_text(v.free, v.total)).xalign(1.0).css_classes(["about-detail"]).build());
    col.append(&top);
    let bar = sonata_ui::progress::meter(used_fraction(v.free, v.total));
    bar.add_css_class("about-usage");
    col.append(&bar);
    row.append(&col);
    row
}

/// Each disk with its space and a bar, and Manage… (Disk Manager).
fn storage() -> gtk::Box {
    let list = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(22).valign(gtk::Align::Center).vexpand(true).css_classes(["about-storage"]).build();
    let l = list.downgrade();
    run_async(volumes, move |vols| {
        let Some(list) = l.upgrade() else { return };
        for v in vols.iter().take(4) {
            list.append(&volume_row(v)); // four fit; Disk Manager has the rest
        }
    });
    let p = page();
    p.append(&list);
    p.append(&footer(&[sonata_ui::controls::push_button(&tr!("Manage…"), || kit::spawn_self(&["diskutil"]), "")]));
    p
}

// -- Support --------------------------------------------------------------------------------------

/// Opens REPO + `path` in the browser.
fn open_page(path: &str) -> impl Fn() + 'static {
    let uri = format!("{REPO}{path}");
    move || {
        if let Err(e) = gio::AppInfo::launch_default_for_uri(&uri, gio::AppLaunchContext::NONE) {
            eprintln!("about: {uri}: {e}");
        }
    }
}

fn link(label: &str, on_click: impl Fn() + 'static) -> gtk::Button {
    let b = gtk::Button::builder().label(label).css_classes(["about-link"]).can_focus(false).build();
    b.connect_clicked(move |_| on_click());
    b
}

fn section(icon: &gtk::Image, title: &str, text: &str, links: Vec<gtk::Button>) -> gtk::Box {
    let row = gtk::Box::builder().spacing(18).build();
    icon.set_pixel_size(64);
    icon.set_valign(gtk::Align::Start);
    row.append(icon);
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(4).build();
    col.append(&gtk::Label::builder().label(title).xalign(0.0).css_classes(["about-section"]).build());
    col.append(&gtk::Label::builder().label(text).xalign(0.0).wrap(true).max_width_chars(60).css_classes(["about-detail"]).build());
    let bar = gtk::Box::new(gtk::Orientation::Horizontal, 18);
    for l in links {
        bar.append(&l);
    }
    col.append(&bar);
    row.append(&col);
    row
}

/// Big Sur's two resources: Sonata's own (its guide, what's new, the
/// license) and help (report a problem through Feedback, known problems).
fn support() -> gtk::Box {
    let list = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(28).valign(gtk::Align::Center).halign(gtk::Align::Center).vexpand(true).build();
    let logo = gtk::Image::builder().pixel_size(64).build();
    sonata_ui::icons::set_logo(&logo); // (sized first: set_logo reads it)
    list.append(&section(
        &logo,
        &tr!("Sonata Resources"),
        &tr!("Learn how to use Sonata, see what changed and read its license."),
        vec![
            link(&tr!("Sonata Guide"), open_page("/blob/main/docs/GUIDE.md")),
            link(&tr!("What's New"), open_page("/blob/main/CHANGELOG.md")),
            link(&tr!("License"), open_page("/blob/main/LICENSE")),
        ],
    ));
    list.append(&section(
        &gtk::Image::from_icon_name("help-browser"),
        &tr!("Help"),
        &tr!("Something not working? Tell us, or see the problems already known."),
        vec![link(&tr!("Report a Problem…"), || kit::spawn_self(&["feedback"])), link(&tr!("Known Problems"), open_page("/issues"))],
    ));
    let p = page();
    p.append(&list);
    p
}

/// About {app}: its icon, name, description.
pub fn about_app(info: Option<&gio::DesktopAppInfo>, name: &str) {
    let (win, head) = frame(&fmt(&tr!("About {app}"), &[("app", name)]), 360, Some(true));
    win.set_size_request(260, 220);
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).css_classes(["about-box"]).valign(gtk::Align::Center).vexpand(true).build();
    let img = gtk::Image::builder().pixel_size(96).build();
    if let Some(i) = info {
        sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(i.upcast_ref()));
    }
    col.append(&img);
    col.append(&gtk::Label::builder().label(name).css_classes(["about-name"]).wrap(true).justify(gtk::Justification::Center).build());
    if let Some(i) = info {
        for text in [i.description(), i.generic_name()].into_iter().flatten() {
            if !text.is_empty() {
                col.append(&gtk::Label::builder().label(text.as_str()).css_classes(["about-version"]).wrap(true).max_width_chars(40).justify(gtk::Justification::Center).build());
            }
        }
    }
    let outer = gtk::Box::new(gtk::Orientation::Vertical, 0);
    outer.append(&head);
    outer.append(&scroller(&col));
    win.set_content(Some(&outer));
    win.present();
}

#[cfg(test)]
mod tests {
    use super::*;

    fn about() -> system::About {
        system::About { os_name: "CachyOS Linux".into(), machine: "ASUS TUF".into(), cpu: "Ryzen 7".into(), memory_gb: 16, gpus: vec![], kernel: "7.3".into() }
    }

    #[test]
    fn about_rows() {
        let _e = crate::testenv::env();
        let r = rows(&about());
        assert_eq!(r[2], ("Memory".to_string(), "16 GB".to_string()));
        assert_eq!(r[3].1, "—"); // no GPU named
        assert_eq!(r[5].1, "Sonata 2");
    }

    /// Big Sur's tabs, in its order: Overview, Displays, Storage, Support.
    #[test]
    fn test_about_tabs_in_big_surs_order() {
        let ids: Vec<&str> = TABS.iter().map(|(k, _)| *k).collect();
        assert_eq!(ids, ["overview", "displays", "storage", "support"]);
    }

    /// The version under "Sonata 2" is this build's, from VERSION.
    #[test]
    fn test_about_version_is_the_builds() {
        let _e = crate::testenv::env();
        assert_eq!(version_text(), format!("Version {}", sonata_config::version()));
        assert!(!sonata_config::version().is_empty());
    }

    /// Overview: the system and kernel under the parts, and a serial row
    /// only when one could be read.
    #[test]
    fn test_about_overview_rows_serial_only_when_read() {
        let _e = crate::testenv::env();
        let keys = |r: Vec<(String, String)>| r.into_iter().map(|(k, _)| k).collect::<Vec<_>>();
        assert_eq!(keys(overview_rows(&about(), None)), ["Processor", "Memory", "Graphics", "System", "Kernel"]);
        let r = overview_rows(&about(), Some("ABC123"));
        assert_eq!(r.last().unwrap(), &("Serial Number".to_string(), "ABC123".to_string()));
        assert_eq!(r[3].1, "CachyOS Linux");
    }

    /// Firmware placeholders are no serial number.
    #[test]
    fn test_about_serial_placeholders_hidden() {
        for raw in ["", "  \n", "To be filled by O.E.M.", "Default string", "System Serial Number", "0000000", "Not Specified", "N/A"] {
            assert_eq!(serial_shown(raw), None, "{raw:?}");
        }
        assert_eq!(serial_shown("K4N0CV07X123456\n"), Some("K4N0CV07X123456".into()));
    }

    /// Storage lists local disks once each, / first: no boot partition,
    /// snaps, swap, tmpfs or btrfs's second mount of the same disk.
    #[test]
    fn test_about_storage_lists_local_disks_once() {
        let mounts = "\
proc /proc proc rw 0 0
/dev/nvme0n1p2 / btrfs rw,subvol=/@ 0 0
tmpfs /tmp tmpfs rw 0 0
/dev/nvme0n1p1 /boot/efi vfat rw 0 0
/dev/nvme0n1p2 /home btrfs rw,subvol=/@home 0 0
/dev/nvme0n1p2 /var/log btrfs rw,subvol=/@log 0 0
/dev/loop3 /snap/core/1 squashfs ro 0 0
/dev/sda1 /run/media/vini/My\\040Stick vfat rw 0 0
/dev/sdb1 /mnt/data ext4 rw 0 0
//nas/share /mnt/nas cifs rw 0 0
";
        let got = listed_mounts(mounts);
        let mps: Vec<&str> = got.iter().map(|(_, m)| m.as_str()).collect();
        assert_eq!(mps, ["/", "/run/media/vini/My Stick", "/mnt/data"]);
        // a /home of its own comes right after /
        let sep = "/dev/sdb1 /mnt/data ext4 rw 0 0\n/dev/sda2 /home ext4 rw 0 0\n/dev/sda1 / ext4 rw 0 0\n";
        let mps: Vec<String> = listed_mounts(sep).into_iter().map(|(_, m)| m).collect();
        assert_eq!(mps, ["/", "/home", "/mnt/data"]);
    }

    /// A disk's name: its label, else the system's / home's disk, else the
    /// folder it's mounted on; removable where USB sticks are mounted.
    #[test]
    fn test_about_storage_volume_names() {
        let _e = crate::testenv::env();
        assert_eq!(volume_name("/", Some("CachyOS")), "CachyOS");
        assert_eq!(volume_name("/", None), "System Disk");
        assert_eq!(volume_name("/home", Some(" ")), "Home Folders");
        assert_eq!(volume_name("/mnt/data", None), "data");
        assert!(is_removable("/run/media/vini/My Stick"));
        assert!(!is_removable("/mnt/data"));
        assert_eq!(unescape("Big\\x20Disk"), "Big Disk");
    }

    /// Big Sur's "X available of Y" in Finder's sizes, and the bar's fill.
    #[test]
    fn test_about_storage_text_and_bar() {
        let _e = crate::testenv::env();
        assert_eq!(storage_text(120_400_000_000, 512_100_000_000), "120.4 GB available of 512.1 GB");
        assert_eq!(used_fraction(25, 100), 0.75);
        assert_eq!(used_fraction(0, 0), 0.0);
        assert_eq!(used_fraction(200, 100), 0.0); // (a filesystem's odd answer)
    }

    /// Displays: the panel is the Built-in Display, a monitor its model;
    /// the size in inches only when the display reports a real one.
    #[test]
    fn test_about_display_texts() {
        let _e = crate::testenv::env();
        assert_eq!(display_name(true, "0x1234", "eDP-1"), "Built-in Display");
        assert_eq!(display_name(false, "Q27G2G4", "DP-1"), "Q27G2G4");
        assert_eq!(display_name(false, " ", "HDMI-A-1"), "HDMI-A-1");
        let i = diagonal_inches(2560, 1440, 597, 336);
        assert_eq!(display_size_text(i, 2560, 1440), "27-inch (2560 × 1440)");
        assert_eq!(display_size_text(diagonal_inches(1920, 1080, 344, 194), 1920, 1080), "15.5-inch (1920 × 1080)");
        assert_eq!(display_size_text(diagonal_inches(3840, 2160, 160, 90), 3840, 2160), "3840 × 2160"); // a TV's made-up size
        assert_eq!(display_detail_text(143_981, 1.25), "144 Hz, scale 1.25");
        assert_eq!(display_detail_text(0, 2.0), "Scale 2");
    }
}
