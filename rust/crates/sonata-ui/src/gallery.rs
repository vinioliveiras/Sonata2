//! Design-system gallery (sonata2/ui/gallery.py): every Sonata component
//! on one page, to check the look in light and dark, and the Rust one next
//! to the Python one (`sonata2-rs gallery [--dark|--light] [--menu N]`).
//!
//! Its text is a developer's page, untranslated as in gallery.py; only the
//! components' own text (the dots' tooltips) goes through tr!.

use crate::{controls, menu, progress, window};
use gtk::{glib, prelude::*};
use sonata_i18n::tr;

/// gallery.py's own CSS (registered there with key="gallery", which the
/// exporter doesn't carry over: copied as it is).
const CSS: &str = "
window.sonata-gallery { background: %(window_bg)s; color: %(label)s; font-family: %(font)s; }
.gallery-title { font-family: %(font_display)s; font-size: %(text_title)s; font-weight: 700; }
.gallery-caption { font-size: %(text_small)s; color: %(label_secondary)s; }
";

// tokens.SPACE
const S_L: i32 = 12;
const S_XL: i32 = 16;
const S_XXL: i32 = 20;

fn app_id() -> &'static str {
    if sonata_config::side_by_side() {
        "io.github.vinioliveiras.sonata2rs.gallery"
    } else {
        "io.github.vinioliveiras.sonata2.gallery"
    }
}

fn caption(text: &str) -> gtk::Label {
    gtk::Label::builder().label(text).css_classes(["gallery-caption"]).build()
}

fn row(caption: &str, widgets: &[&gtk::Widget]) -> gtk::Box {
    let bx = gtk::Box::builder().spacing(S_L).valign(gtk::Align::Center).build();
    bx.append(&gtk::Label::builder().label(caption).xalign(0.0).width_chars(14).css_classes(["gallery-caption"]).build());
    for w in widgets {
        bx.append(*w);
    }
    bx
}

fn sized<W: IsA<gtk::Widget>>(w: W, width: i32) -> W {
    w.set_size_request(width, -1);
    w
}

/// The menu gallery.py shows: a title, a submenu with checks, a disabled item.
pub fn sample_menu(anchor: &impl IsA<gtk::Widget>) -> gtk::PopoverMenu {
    use menu::Item;
    let pop = menu::popup(
        anchor,
        vec![
            vec![Item::new("Window title", || {})],
            vec![Item::submenu(
                "Options",
                vec![vec![Item::check("Keep in Dock", true, |_| {}), Item::check("Open at Login", false, |_| {})], vec![Item::new("Open File Location", || {})]],
            )],
            vec![Item::new("Hide", || {}), Item::new("Quit", || {}), Item::new("Disabled item", || {}).enabled(false)],
        ],
        menu::Options::below(),
    );
    pop.set_offset(0, 4); // (gallery.py's gap=4)
    pop
}

/// ui/label.py's HoverLabel with hover=False: the name bubble over its
/// anchor, shown by the page itself (the Dock's own is in the Dock).
fn hover_label(anchor: &impl IsA<gtk::Widget>, text: &str) -> gtk::Popover {
    crate::css::register_module("ui/label.py");
    let pop = gtk::Popover::builder()
        .css_classes(["hover-label"])
        .has_arrow(false)
        .autohide(false)
        .can_target(false)
        .position(gtk::PositionType::Top)
        .child(&gtk::Label::new(Some(text)))
        .build();
    pop.set_offset(0, -8);
    pop.set_parent(anchor);
    pop
}

/// ui.window.order_lights as the gallery shows it: the three dots, each
/// draggable; the gallery's on_change does nothing, so a drop changes
/// nothing either (Settings > Appearance has the working one).
fn order_lights() -> gtk::Box {
    let bx = gtk::Box::builder().css_classes(["traffic", "traffic-order"]).valign(gtk::Align::Center).halign(gtk::Align::Center).build();
    for (name, css, tip) in [("close", "tl-close", tr!("Close")), ("minimize", "tl-min", tr!("Minimize")), ("maximize", "tl-zoom", tr!("Zoom"))] {
        let b = gtk::Button::builder().css_classes([css]).tooltip_text(tip).valign(gtk::Align::Center).focus_on_click(false).can_focus(false).build();
        b.set_cursor_from_name(Some("grab"));
        let src = gtk::DragSource::builder().actions(gtk::gdk::DragAction::MOVE).build();
        src.connect_prepare(move |_, _, _| Some(gtk::gdk::ContentProvider::for_value(&name.to_value())));
        let bw = b.downgrade();
        src.connect_drag_begin(move |s, _| {
            if let Some(w) = bw.upgrade() {
                s.set_icon(Some(&gtk::WidgetPaintable::new(Some(&w))), w.width() / 2, w.height() / 2);
            }
        });
        b.add_controller(src);
        bx.append(&b);
    }
    bx
}

/// The page: every component in its row. Returns it, the hover label
/// (shown once the page is up) and the menu's anchor.
fn page() -> (gtk::Box, gtk::Popover, gtk::Button) {
    let col = gtk::Box::builder()
        .orientation(gtk::Orientation::Vertical)
        .spacing(S_L)
        .margin_top(S_XL)
        .margin_bottom(S_XL)
        .margin_start(S_XXL)
        .margin_end(S_XXL)
        .build();
    let head = gtk::Box::builder().spacing(S_L).build();
    head.append(&window::traffic_lights(|| {}, || {}, Some(Box::new(|| {}))));
    head.append(&gtk::Label::builder().label("Sonata design system").css_classes(["gallery-title"]).build());
    col.append(&head);

    let disabled = controls::push_button("Disabled", || {}, "");
    disabled.set_sensitive(false);
    col.append(&row(
        "Push buttons",
        &[
            controls::push_button("Cancel", || {}, "").upcast_ref(),
            controls::push_button("OK", || {}, "default").upcast_ref(),
            controls::push_button("Empty Trash", || {}, "destructive").upcast_ref(),
            disabled.upcast_ref(),
        ],
    ));
    let options: Vec<String> = ["Automatic", "Light", "Dark"].map(String::from).to_vec();
    col.append(&row("Pop-up button", &[controls::popup_button(&options, 0, |_| {}).upcast_ref()]));
    col.append(&row("Switches", &[controls::switch(true).upcast_ref(), controls::switch(false).upcast_ref()]));
    col.append(&row(
        "Text fields",
        &[sized(controls::text_field("", "Name"), 180).upcast_ref(), sized(controls::secret_field("secret", ""), 180).upcast_ref()],
    ));
    let send = controls::round_button("go-up-symbolic", "Send", || {});
    let area = controls::TextArea::new("Message", 180, Some(send.upcast_ref()), |_| {}, |_| {});
    col.append(&row("Text area", &[sized(area.root, 360).upcast_ref()]));
    let tip_anchor = gtk::Label::new(Some("Hover label anchor"));
    let tip = hover_label(&tip_anchor, "Firefox");
    col.append(&row("Hover label", &[tip_anchor.upcast_ref()]));
    let menu_anchor = controls::push_button("Right-click menu", || {}, "");
    let ma = menu_anchor.downgrade();
    menu_anchor.connect_clicked(move |_| {
        if let Some(a) = ma.upgrade() {
            sample_menu(&a);
        }
    });
    col.append(&row("Menu", &[menu_anchor.upcast_ref()]));
    let pulse = progress::bar(None);
    let pw = pulse.downgrade();
    glib::timeout_add_local(std::time::Duration::from_millis(120), move || match pw.upgrade() {
        Some(p) => {
            p.pulse();
            glib::ControlFlow::Continue
        }
        None => glib::ControlFlow::Break,
    });
    col.append(&row(
        "Progress",
        &[sized(progress::bar(Some(0.42)), 160).upcast_ref(), sized(pulse, 160).upcast_ref(), progress::Spinner::new(true).upcast_ref()],
    ));
    col.append(&row(
        "Meter",
        &[sized(progress::meter(0.55), 160).upcast_ref(), sized(progress::meter(0.95), 160).upcast_ref(), caption("(disk capacity, red above 90 %)").upcast_ref()],
    ));
    col.append(&row("Traffic lights", &[window::traffic_lights(|| {}, || {}, None).upcast_ref(), caption("(zoom disabled)").upcast_ref()]));
    col.append(&row("Button order", &[order_lights().upcast_ref(), caption("(drag a dot to another place)").upcast_ref()]));
    (col, tip, menu_anchor)
}

/// The gallery window; returns it and the menu's anchor (`--menu` opens it).
pub fn gallery_window(app: &adw::Application) -> (gtk::ApplicationWindow, gtk::Button) {
    crate::register("gallery", CSS);
    let win = gtk::ApplicationWindow::builder()
        .application(app)
        .title("Sonata 2 preview")
        .css_classes(["sonata-gallery"])
        .default_width(960)
        .default_height(420)
        .decorated(false)
        .build();
    let (col, tip, menu_anchor) = page();
    win.set_child(Some(&col));
    // the bubble shown once the page is up (it points at a laid-out anchor)
    win.connect_map(move |_| {
        let tip = tip.clone();
        glib::timeout_add_local_once(std::time::Duration::from_millis(300), move || tip.popup());
    });
    (win, menu_anchor)
}

/// `--menu N` (N >= 0) opens the sample menu, for a screenshot
/// (tools/wl-preview.sh gallery --menu 0); -1 when not given.
fn menu_arg(args: &[String]) -> i64 {
    args.iter().position(|a| a == "--menu").and_then(|i| args.get(i + 1)).and_then(|n| n.parse().ok()).unwrap_or(-1)
}

/// `sonata2 gallery`: the design-system page. `args`: what follows the
/// command (`--dark` / `--light`, `--menu N`).
pub fn run_gallery(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(app_id()).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let menu = menu_arg(args);
    app.connect_activate(move |app| {
        if let Some(f) = force {
            crate::force_appearance(f);
        }
        crate::setup();
        let (win, anchor) = gallery_window(app);
        win.present();
        if menu >= 0 {
            glib::timeout_add_local_once(std::time::Duration::from_millis(500), move || {
                sample_menu(&anchor);
            });
        }
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn menu_argument_as_gallery_py() {
        let a = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(menu_arg(&a(&[])), -1);
        assert_eq!(menu_arg(&a(&["--dark", "--menu", "0"])), 0);
        assert_eq!(menu_arg(&a(&["--menu"])), -1);
        assert_eq!(menu_arg(&a(&["--menu", "x"])), -1);
    }

    /// Every component on the page builds (where GTK can start).
    #[test]
    fn gallery_builds() {
        if !crate::gtk_for_tests() {
            return; // (no display)
        }
        let (col, tip, anchor) = page();
        assert_eq!(anchor.label().as_deref(), Some("Right-click menu"));
        assert_eq!(tip.parent().and_downcast::<gtk::Label>().map(|l| l.label().to_string()).as_deref(), Some("Hover label anchor"));
        let mut rows = 0;
        let mut c = col.first_child();
        while let Some(w) = c {
            rows += 1;
            c = w.next_sibling();
        }
        assert_eq!(rows, 12); // the title and gallery.py's eleven rows
        tip.unparent();
    }
}
