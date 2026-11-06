//! About This Computer (Big Sur layout: logo left, OS name, specs) and an
//! app's About window, from the menu bar's menus (topbar.py's AboutWindow
//! and AboutAppWindow). Resizable; narrow, the logo goes above the text.

use crate::kit::run_async;
use adw::prelude::*;
use gtk::{gio, glib};
use sonata_backend::system;
use sonata_i18n::{fmt, tr};
use std::time::Duration;

/// Lets a small window shrink below its content (scrolls instead).
fn scroller(child: &impl IsA<gtk::Widget>) -> gtk::ScrolledWindow {
    gtk::ScrolledWindow::builder().child(child).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).propagate_natural_width(true).build()
}

fn frame(title: &str, width: i32) -> (adw::Window, gtk::WindowHandle) {
    let win = adw::Window::builder().title(title).default_width(width).build();
    for c in ["sonata-about", "sonata-glass-window"] {
        win.add_css_class(c); // frosted glass, like the Dock
    }
    sonata_ui::window::standard(&win);
    // topbar.py's ui.window.titlebar(self, zoom=True): the window buttons
    // (Vini: no close button, and a black glass toolbar over the glass)
    let head = sonata_ui::window::titlebar(&win, None, None, Some(true));
    (win, head)
}

/// The rows of About This Computer: (key, value).
pub fn rows(a: &system::About) -> Vec<(String, String)> {
    let gpus = if a.gpus.is_empty() { "—".to_string() } else { a.gpus.join(", ") };
    vec![
        (tr!("Computer"), a.machine.clone()),
        (tr!("Processor"), a.cpu.clone()),
        (tr!("Memory"), fmt(&tr!("{n} GB"), &[("n", &a.memory_gb.to_string())])),
        (tr!("Graphics"), gpus),
        (tr!("Kernel"), a.kernel.clone()),
        (tr!("Desktop"), "Sonata 2".to_string()),
    ]
}

pub struct AboutWindow;

impl AboutWindow {
    pub fn open() {
        let (win, head) = frame(&tr!("About This Computer"), 720); // its content's height
        win.set_size_request(320, 260);
        let body = gtk::Box::builder().spacing(36).css_classes(["about-box"]).halign(gtk::Align::Center).valign(gtk::Align::Center).vexpand(true).build();
        let logo = gtk::Image::builder().pixel_size(120).valign(gtk::Align::Center).build();
        sonata_ui::icons::set_logo(&logo); // the distro's logo (os-release LOGO=)
        body.append(&logo);
        let info = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(3).valign(gtk::Align::Center).build();
        body.append(&info);
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        col.append(&head);
        let scroll = scroller(&gtk::WindowHandle::builder().child(&body).build()); // drag from anywhere, like macOS
        col.append(&scroll);
        win.set_content(Some(&col));
        if let Ok(cond) = adw::BreakpointCondition::parse("max-width: 560sp") {
            let bp = adw::Breakpoint::new(cond);
            bp.add_setter(&body, "orientation", Some(&gtk::Orientation::Vertical.to_value()));
            bp.add_setter(&body, "spacing", Some(&16.to_value()));
            bp.add_setter(&logo, "pixel-size", Some(&80.to_value()));
            bp.add_setter(&logo, "halign", Some(&gtk::Align::Center.to_value()));
            bp.add_setter(&info, "halign", Some(&gtk::Align::Center.to_value()));
            win.add_breakpoint(bp);
        }
        let (w, s) = (win.downgrade(), scroll.clone());
        run_async(system::about, move |a| {
            let Some(win) = w.upgrade() else { return };
            let (name, version) = a.os_name.split_once(' ').unwrap_or((&a.os_name, ""));
            info.append(&gtk::Label::builder().label(name).xalign(0.0).css_classes(["about-name"]).build());
            let version = if version.is_empty() { fmt(&tr!("Kernel {version}"), &[("version", &a.kernel)]) } else { version.to_string() };
            info.append(&gtk::Label::builder().label(&version).xalign(0.0).css_classes(["about-version"]).build());
            for (key, value) in rows(&a) {
                let row = gtk::Box::new(gtk::Orientation::Horizontal, 6);
                row.append(&gtk::Label::builder().label(&key).css_classes(["about-key"]).valign(gtk::Align::Start).build());
                // wraps instead of cutting: the window can be narrow or wide
                row.append(&gtk::Label::builder().label(&value).xalign(0.0).wrap(true).natural_wrap_mode(gtk::NaturalWrapMode::None).max_width_chars(60).hexpand(true).build());
                info.append(&row);
            }
            // once laid out, grow by what doesn't fit; the next couple of
            // seconds only (then the size is the user's)
            let adj = s.vadjustment();
            let w2 = win.downgrade();
            let id = adj.connect_changed(move |a| {
                if let Some(win) = w2.upgrade() {
                    fit(&win, a);
                }
            });
            let a2 = adj.clone();
            glib::timeout_add_local_once(Duration::from_secs(2), move || a2.disconnect(id));
            let w3 = win.downgrade();
            glib::idle_add_local_once(move || {
                if let Some(win) = w3.upgrade() {
                    fit(&win, &adj);
                }
            });
        });
        win.present();
    }
}

/// Its whole content in sight (Vini: it opened before the specs came and
/// kept that height -- a scroll bar on a small window): as tall as its
/// content, never taller than the screen (then it scrolls).
fn fit(win: &adw::Window, adj: &gtk::Adjustment) {
    let missing = adj.upper() - adj.page_size();
    let height = win.height();
    if missing < 1.0 || height <= 0 {
        return;
    }
    let limit = win.surface().and_then(|s| gtk::prelude::WidgetExt::display(win).monitor_at_surface(&s)).map_or(1_000_000, |m| m.geometry().height() - 80);
    // (the default size counts the window's frame, width()/height() don't)
    let frame = (win.default_size().0 - win.width()).max(0);
    win.set_default_size(win.width() + frame, limit.min(height + frame + missing.ceil() as i32));
}

/// About {app}: its icon, name, description.
pub fn about_app(info: Option<&gio::DesktopAppInfo>, name: &str) {
    let (win, head) = frame(&fmt(&tr!("About {app}"), &[("app", name)]), 360);
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

    #[test]
    fn about_rows() {
        let _e = crate::testenv::env();
        let a = system::About { os_name: "CachyOS Linux".into(), machine: "ASUS TUF".into(), cpu: "Ryzen 7".into(), memory_gb: 16, gpus: vec![], kernel: "7.3".into() };
        let r = rows(&a);
        assert_eq!(r[2], ("Memory".to_string(), "16 GB".to_string()));
        assert_eq!(r[3].1, "—"); // no GPU named
        assert_eq!(r[5].1, "Sonata 2");
    }
}
