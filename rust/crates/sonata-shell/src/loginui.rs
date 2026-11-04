//! What the lock, login and welcome screens share (shell/loginui.py): the
//! blurred wallpaper backdrop, the user's picture, the capsule password
//! field that shakes on a wrong password, the clock, the power buttons.
//! Every colour and size of this look lives in loginui.py's CSS.

use adw::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib, graphene};
use sonata_i18n::tr;
use std::cell::RefCell;
use std::rc::Rc;

/// the wallpaper blurred at a quarter of the display's size (looks the same)
pub const BLUR_SCALE: f64 = 4.0;
/// the picture reaches this far past each edge (the blur's edges stay off screen)
pub const BLUR_MARGIN: f64 = 40.0;
/// bottom margin, a share of the display height (it moves a centred widget
/// up by half of it): the column sits 3% of the display above its middle
pub const COLUMN_LIFT: f64 = 0.06;
pub const CLOCK_FORMAT: &str = "%a %-d %b  %H:%M";

pub fn register_css() {
    sonata_ui::css::register_module("shell/loginui.py");
}

/// ~/.cache/sonata2/power.log (or the in-memory logs): what the power
/// buttons did (Vini: Sleep and Restart did nothing, and nothing said why).
pub fn power_log(text: &str) {
    use std::io::Write;
    let dir = sonata_session::logs::log_dir();
    let _ = std::fs::create_dir_all(&dir);
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(dir.join("power.log")) {
        let now = glib::DateTime::now_local().ok().and_then(|d| d.format("%Y-%m-%d %H:%M:%S").ok()).unwrap_or_default();
        let _ = writeln!(f, "{now} {text}");
    }
    eprintln!("sonata2-power: {text}");
}

fn systemctl_verb(method: &str) -> Option<&'static str> {
    match method {
        "Suspend" => Some("suspend"),
        "Reboot" => Some("reboot"),
        "PowerOff" => Some("poweroff"),
        _ => None,
    }
}

/// Ask logind; an error is logged and `systemctl` is tried instead.
pub fn logind(method: &str) {
    power_log(&format!("{method}: asking logind"));
    let method = method.to_string();
    let fallback = {
        let method = method.clone();
        move |why: String| {
            power_log(&format!("{method}: logind refused ({why}); trying systemctl {}", systemctl_verb(&method).unwrap_or("?")));
            let Some(verb) = systemctl_verb(&method) else { return };
            let method = method.clone();
            std::thread::spawn(move || {
                // off the main loop: the screen stays responsive
                let (rc, out) = sonata_backend::run::run(&["systemctl", verb], 20);
                power_log(&format!("{method}: systemctl exit {rc} {}", out.trim()));
            });
        }
    };
    match gio::bus_get_sync(gio::BusType::System, gio::Cancellable::NONE) {
        Ok(bus) => {
            let m = method.clone();
            bus.call(Some("org.freedesktop.login1"), "/org/freedesktop/login1", "org.freedesktop.login1.Manager", &method, Some(&(true,).to_variant()), None, gio::DBusCallFlags::NONE, -1, gio::Cancellable::NONE, move |res| match res {
                Ok(_) => power_log(&format!("{m}: accepted")),
                Err(e) => fallback(e.message().to_string()),
            });
        }
        Err(e) => fallback(e.message().to_string()),
    }
}

/// Sleep / Restart / Shut Down along the bottom (macOS login and lock
/// screens); action(method) gets the logind method name.
pub fn power_bar(action: Rc<dyn Fn(&str)>) -> gtk::Box {
    let bar = gtk::Box::builder().spacing(20).halign(gtk::Align::Center).valign(gtk::Align::End).margin_bottom(40).homogeneous(true).css_classes(["gr-rise-late"]).build();
    for (label, icon, method) in [(tr!("Sleep"), "weather-clear-night-symbolic", "Suspend"), (tr!("Restart"), "view-refresh-symbolic", "Reboot"), (tr!("Shut Down"), "system-shutdown-symbolic", "PowerOff")] {
        let col = gtk::Box::new(gtk::Orientation::Vertical, 6);
        // the pointer only (macOS): never keyboard focus -- while the
        // password was checked the field gave its focus away, and the next
        // Enter restarted the computer (Vini, lock screen)
        let b = gtk::Button::builder().icon_name(icon).css_classes(["gr-power"]).halign(gtk::Align::Center).tooltip_text(label.as_str()).focusable(false).focus_on_click(false).build();
        let action = action.clone();
        b.connect_clicked(move |_| {
            power_log(&format!("{method}: button clicked"));
            action(method);
        });
        col.append(&b);
        col.append(&gtk::Label::builder().label(label.as_str()).css_classes(["gr-power-label"]).build());
        bar.append(&col);
    }
    bar
}

fn is_dark() -> bool {
    adw::StyleManager::default().is_dark()
}

/// The wallpaper of the moment (Light/Dark picture, a video's poster) as
/// a texture, None if there is none. `max_size` > 0: decoded to fit that
/// many pixels on its longest side (for backdrops under a heavy blur).
pub fn wallpaper_texture(max_size: i32) -> Option<gdk::Texture> {
    if let Ok(over) = std::env::var("SONATA_LOCK_WALLPAPER") {
        // previews
        let p = std::path::PathBuf::from(&over);
        let still = if crate::wallpaper::is_video(&over) { crate::wallpaper::poster(&p).unwrap_or(p) } else { p };
        return decode(&gio::File::for_path(still), max_size);
    }
    let uri = crate::wallpaper::still_uri(is_dark());
    let f = gio::File::for_uri(&uri);
    if f.query_exists(gio::Cancellable::NONE) { decode(&f, max_size) } else { None }
}

fn decode(f: &gio::File, max_size: i32) -> Option<gdk::Texture> {
    if let (true, Some(path)) = (max_size > 0, f.path()) {
        if let Some((_fmt, w, h)) = gtk::gdk_pixbuf::Pixbuf::file_info(&path) {
            if w.max(h) > max_size {
                // (never scaled up; no EXIF rotation: Texture::from_file applies none either)
                if let Ok(pb) = gtk::gdk_pixbuf::Pixbuf::from_file_at_scale(&path, max_size, max_size, true) {
                    #[allow(deprecated)]
                    return Some(gdk::Texture::for_pixbuf(&pb));
                }
            }
        }
    }
    gdk::Texture::from_file(f).ok()
}

/// The login / lock column a little above the middle of its display
/// (Vini: higher up, like macOS).
pub fn lift(widget: &impl IsA<gtk::Widget>, monitor: Option<&gdk::Monitor>) {
    let h = monitor.map(|m| m.geometry().height()).unwrap_or(0);
    widget.set_margin_bottom((h as f64 * COLUMN_LIFT) as i32);
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct Balance {
        pub other: RefCell<Option<gtk::Widget>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Balance {
        const NAME: &'static str = "SonataRsBalance";
        type Type = super::Balance;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for Balance {}

    impl WidgetImpl for Balance {
        fn measure(&self, o: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            match self.other.borrow().as_ref() {
                Some(w) if o == gtk::Orientation::Vertical && w.is_visible() => {
                    let (mn, nat, _, _) = w.measure(o, -1);
                    (mn, nat, -1, -1)
                }
                _ => (0, 0, -1, -1),
            }
        }
    }

    #[derive(Default)]
    pub struct Backdrop {
        pub texture: RefCell<Option<gdk::Texture>>,
        pub dim: std::cell::Cell<f64>,
        pub blur: std::cell::Cell<f64>,
        /// (texture, w, h, blurred picture)
        pub ready: RefCell<Option<(gdk::Texture, i32, i32, Option<gdk::Texture>)>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Backdrop {
        const NAME: &'static str = "SonataRsBackdrop";
        type Type = super::Backdrop;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for Backdrop {}

    impl WidgetImpl for Backdrop {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let (w, h) = (obj.width(), obj.height());
            let rect = graphene::Rect::new(0.0, 0.0, w as f32, h as f32);
            let dim = gdk::RGBA::new(0.0, 0.0, 0.0, self.dim.get() as f32);
            let Some(tex) = self.texture.borrow().clone() else {
                snap.append_color(&gdk::RGBA::parse("#2a3550").expect("a colour"), &rect);
                return;
            };
            let blur = self.blur.get();
            if blur > 0.0 {
                let fresh = !matches!(self.ready.borrow().as_ref(), Some((t, rw, rh, _)) if *t == tex && (*rw, *rh) == (w, h));
                if fresh {
                    let b = blurred(&tex, w, h, blur);
                    self.ready.replace(Some((tex.clone(), w, h, b)));
                }
                if let Some((_, _, _, Some(b))) = self.ready.borrow().as_ref() {
                    snap.append_scaled_texture(b, gtk::gsk::ScalingFilter::Linear, &rect);
                    snap.append_color(&dim, &rect);
                    return;
                }
            }
            // (no CPU blur: the GPU's)
            let (tw, th) = (tex.width() as f64, tex.height() as f64);
            let scale = (w as f64 / tw).max(h as f64 / th);
            let (dw, dh) = (tw * scale, th * scale);
            snap.push_clip(&rect);
            snap.push_blur(blur);
            let m = BLUR_MARGIN;
            snap.append_texture(&tex, &graphene::Rect::new(((w as f64 - dw) / 2.0 - m) as f32, ((h as f64 - dh) / 2.0 - m) as f32, (dw + 2.0 * m) as f32, (dh + 2.0 * m) as f32));
            snap.pop();
            snap.append_color(&dim, &rect);
            snap.pop();
        }
    }
}

glib::wrapper! {
    /// Empty, as tall as `other`: put above a column, it balances the rows
    /// hanging below (links, hint) so the picture, name and field are
    /// centred as one block (Vini).
    pub struct Balance(ObjectSubclass<imp::Balance>) @extends gtk::Widget;
}

impl Balance {
    pub fn new(other: &impl IsA<gtk::Widget>) -> Self {
        let b: Self = glib::Object::builder().property("can-target", false).build();
        b.imp().other.replace(Some(other.clone().upcast()));
        b
    }
}

/// `below` (the rows under the field) in one box at the column's end, an
/// equal empty space at its start: the rest is centred.
pub fn balanced(col: &gtk::Box, below: &[gtk::Widget]) -> gtk::Box {
    let extra = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(col.spacing()).halign(gtk::Align::Center).build();
    for w in below {
        extra.append(w);
    }
    col.prepend(&Balance::new(&extra));
    col.append(&extra);
    extra
}

/// The cover-fit `size` (w, h) part of an `iw` x `ih` picture scaled by
/// `scale`: (scaled w, scaled h, crop x, crop y).
pub fn cover_crop(iw: u32, ih: u32, sw: u32, sh: u32, w: f64, h: f64) -> (u32, u32, u32, u32) {
    let scale = ((w + 2.0 * BLUR_MARGIN) / BLUR_SCALE / iw as f64).max((h + 2.0 * BLUR_MARGIN) / BLUR_SCALE / ih as f64);
    let (rw, rh) = (sw.max((iw as f64 * scale).round() as u32), sh.max((ih as f64 * scale).round() as u32));
    (rw, rh, (rw - sw) / 2, (rh - sh) / 2)
}

/// The wallpaper cover-fit to w x h and blurred on the CPU, at
/// 1/BLUR_SCALE size (drawn scaled up). Vini: the lock screen's background
/// once showed a ghost of another window -- the GPU blur's scratch picture
/// with another app's old pixels; a ready picture has none.
pub fn blurred(texture: &gdk::Texture, w: i32, h: i32, blur: f64) -> Option<gdk::Texture> {
    if w <= 0 || h <= 0 {
        return None;
    }
    let mut dl = gdk::TextureDownloader::new(texture);
    dl.set_format(gdk::MemoryFormat::R8g8b8a8);
    let (bytes, stride) = dl.download_bytes();
    let (tw, th) = (texture.width() as u32, texture.height() as u32);
    let mut rgba = image::RgbaImage::new(tw, th);
    for y in 0..th {
        let row = &bytes[y as usize * stride..y as usize * stride + tw as usize * 4];
        rgba.as_mut()[(y * tw * 4) as usize..((y + 1) * tw * 4) as usize].copy_from_slice(row);
    }
    let (sw, sh) = (((w as f64 / BLUR_SCALE) as u32).max(1), ((h as f64 / BLUR_SCALE) as u32).max(1));
    let img = image::DynamicImage::ImageRgba8(rgba).to_rgb8();
    let (rw, rh, x, y) = cover_crop(tw, th, sw, sh, w as f64, h as f64);
    let small = image::imageops::resize(&img, rw, rh, image::imageops::FilterType::Triangle);
    let crop = image::imageops::crop_imm(&small, x, y, sw, sh).to_image();
    // GSK's blur radius r is a Gaussian of sigma r / 2; at this size, / BLUR_SCALE
    let out = image::imageops::blur(&crop, (blur / 2.0 / BLUR_SCALE) as f32);
    let data = glib::Bytes::from_owned(out.into_raw());
    Some(gdk::MemoryTexture::new(sw as i32, sh as i32, gdk::MemoryFormat::R8g8b8, &data, sw as usize * 3).upcast())
}

glib::wrapper! {
    /// The wallpaper, cover-fit, blurred and dimmed (drawn once per size).
    pub struct Backdrop(ObjectSubclass<imp::Backdrop>) @extends gtk::Widget;
}

impl Backdrop {
    pub fn new(texture: Option<gdk::Texture>, dim: f64, blur: f64) -> Self {
        let b: Self = glib::Object::builder().property("hexpand", true).property("vexpand", true).build();
        b.imp().texture.replace(texture);
        b.imp().dim.set(dim);
        b.imp().blur.set(blur);
        b
    }

    pub fn set_texture(&self, texture: Option<gdk::Texture>) {
        self.imp().texture.replace(texture);
        self.queue_draw();
    }
}

impl Default for Backdrop {
    fn default() -> Self {
        Self::new(None, 0.18, 64.0)
    }
}

/// `user` None: this process's user. `icon`: a picture file to use first.
pub fn avatar(size: i32, user: Option<&str>, real_name: Option<&str>, icon: Option<&str>) -> adw::Avatar {
    let own = user.is_none();
    let user = user.map(String::from).unwrap_or_else(|| glib::user_name().to_string_lossy().into_owned());
    let real = real_name.map(String::from).filter(|s| !s.is_empty()).or_else(|| own.then(|| glib::real_name().to_string_lossy().into_owned())).filter(|s| !s.is_empty() && s != "Unknown").unwrap_or_else(|| user.clone());
    let mut paths: Vec<std::path::PathBuf> = icon.map(|i| vec![i.into()]).unwrap_or_default();
    if own {
        paths.push(glib::home_dir().join(".face"));
    }
    paths.push(format!("/var/lib/AccountsService/icons/{user}").into());
    for p in paths {
        if p.is_file() {
            if let Ok(tex) = gdk::Texture::from_filename(&p) {
                let av = adw::Avatar::new(size, None, false);
                av.set_custom_image(Some(&tex));
                return av;
            }
        }
    }
    adw::Avatar::new(size, Some(&real), true)
}

pub fn display_name() -> String {
    let real = glib::real_name().to_string_lossy().into_owned();
    if real.is_empty() || real == "Unknown" { glib::user_name().to_string_lossy().into_owned() } else { real }
}

pub fn password_field(placeholder: Option<&str>) -> gtk::PasswordEntry {
    let text = placeholder.map(String::from).unwrap_or_else(|| tr!("Enter Password"));
    gtk::PasswordEntry::builder().placeholder_text(text.as_str()).css_classes(["lk-field"]).halign(gtk::Align::Center).show_peek_icon(false).build()
}

/// Wrong password (macOS): the field shakes, empties and keeps focus.
pub fn shake(entry: &gtk::PasswordEntry) {
    entry.set_sensitive(true);
    entry.set_text("");
    entry.grab_focus();
    entry.remove_css_class("shake");
    let e = entry.clone();
    glib::idle_add_local_once(move || e.add_css_class("shake"));
    let e = entry.clone();
    glib::timeout_add_local_once(std::time::Duration::from_millis(500), move || e.remove_css_class("shake"));
}

/// The menu bar's clock format (Settings > Date & Time); the default else.
pub fn menu_bar_format() -> String {
    let cfg = sonata_config::load("topbar", &serde_json::json!({"clock_format": CLOCK_FORMAT}));
    cfg.get("clock_format").and_then(|v| v.as_str()).filter(|s| !s.is_empty()).unwrap_or(CLOCK_FORMAT).to_string()
}

/// Keep `label` on the date and time (every 10 s).
pub fn clock(label: &gtk::Label, fmt: &str) {
    let tick = {
        let (label, fmt) = (label.downgrade(), fmt.to_string());
        move || {
            let Some(label) = label.upgrade() else { return glib::ControlFlow::Break };
            label.set_label(&glib::DateTime::now_local().ok().and_then(|d| d.format(&fmt).ok()).map(|s| s.to_string()).unwrap_or_default());
            glib::ControlFlow::Continue
        }
    };
    tick();
    glib::timeout_add_seconds_local(10, tick);
}

/// The date and time at the top, centred, the menu bar's size and format.
pub fn top_clock(fmt: Option<&str>) -> gtk::Label {
    let when = gtk::Label::builder().css_classes(["lk-clock"]).halign(gtk::Align::Center).valign(gtk::Align::Start).margin_top(6).build();
    clock(&when, &fmt.map(String::from).unwrap_or_else(menu_bar_format));
    when
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_blur_covers_the_display_past_its_edges() {
        // a 1920x1080 display from a 3840x2160 picture: a quarter of the display, plus the margins
        let (rw, rh, x, y) = cover_crop(3840, 2160, 480, 270, 1920.0, 1080.0);
        assert!(rw >= 480 && rh >= 270);
        assert_eq!((x, y), ((rw - 480) / 2, (rh - 270) / 2));
        // a tall picture on a wide display: cut at the top and bottom
        let (rw, rh, x, _y) = cover_crop(1000, 3000, 480, 270, 1920.0, 1080.0);
        assert_eq!(x, (rw - 480) / 2);
        assert!(rh > 270);
    }

    #[test]
    fn power_methods_have_a_systemctl_fallback() {
        assert_eq!(systemctl_verb("Reboot"), Some("reboot"));
        assert_eq!(systemctl_verb("Nope"), None);
    }
}
