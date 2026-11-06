//! A notification card, the same in a banner and in Notification Center:
//! the app's icon, the title and time, up to three lines of body, the
//! app's buttons, its picture on the right; a click is the default
//! action, hovering shows the close button.

use crate::model::{self, Note, BANNER_W, BODY_LINES, THUMB};
use crate::server::Server;
use gtk::prelude::*;
use sonata_ui::fixed::FixedWidth;
use std::rc::Rc;

pub struct Card {
    pub root: gtk::Widget,
    /// its "5m ago": Notification Center keeps it current
    pub when: gtk::Label,
}

/// The picture cut to a square (centre), `size` px at `scale`: a texture,
/// None if unreadable.
pub fn thumbnail(path: &str, size: i32, scale: i32) -> Option<gtk::gdk::Texture> {
    use gtk::gdk_pixbuf::Pixbuf;
    let (_, w, h) = Pixbuf::file_info(path)?;
    if w <= 0 || h <= 0 {
        return None;
    }
    let (w, h) = (w as f64, h as f64);
    let px = size * scale;
    let k = px as f64 / w.min(h);
    let pb = Pixbuf::from_file_at_scale(path, px.max((w * k).round() as i32), px.max((h * k).round() as i32), false).ok()?;
    let (x, y) = ((pb.width() - px) / 2, (pb.height() - px) / 2);
    Some(gtk::gdk::Texture::for_pixbuf(&pb.new_subpixbuf(x, y, px, px)))
}

/// The app's icon: by its desktop entry, else by its name, else the icon
/// the notification names.
fn set_icon(img: &gtk::Image, n: &Note) {
    let info = model::lookup_app(&n.desktop).or_else(|| {
        if n.app.is_empty() {
            return None;
        }
        model::lookup_app(&n.app.to_lowercase().replace(' ', "-")).or_else(|| model::lookup_app(&n.app))
    });
    if let Some(info) = info {
        sonata_ui::icons::set_image(img, &sonata_ui::icons::app_icon(info.upcast_ref()));
    } else if let Some(path) = n.icon.strip_prefix("file://").or_else(|| n.icon.starts_with('/').then_some(n.icon.as_str())) {
        img.set_from_file(Some(path));
    } else if !n.icon.is_empty() {
        img.set_icon_name(Some(&n.icon));
    } else {
        img.set_icon_name(Some("dialog-information"));
    }
}

pub fn card(n: &Note, server: &Rc<Server>) -> Card {
    card_with(n, server, None, None)
}

/// A card whose click and x do `on_click` / `on_close` in place of its
/// default action and closing it (a stack's top card: they expand the
/// stack, clear it).
pub fn card_with(n: &Note, server: &Rc<Server>, on_click: Option<Rc<dyn Fn()>>, on_close: Option<Rc<dyn Fn()>>) -> Card {
    let over = gtk::Overlay::new();
    let row = gtk::Box::builder().spacing(10).css_classes(["nt-card"]).build();
    if !sonata_ui::glass() {
        row.add_css_class("solid");
    }
    row.set_size_request(BANNER_W, -1);
    let img = gtk::Image::builder().pixel_size(36).valign(gtk::Align::Start).build();
    set_icon(&img, n);
    row.append(&img);
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(1).hexpand(true).build();
    let top = gtk::Box::new(gtk::Orientation::Horizontal, 6);
    let title = if n.summary.is_empty() { n.app.as_str() } else { n.summary.as_str() };
    top.append(&gtk::Label::builder().label(title).xalign(0.0).hexpand(true).ellipsize(gtk::pango::EllipsizeMode::End).css_classes(["nt-title"]).build());
    let when = gtk::Label::builder().label(model::ago(n.at, model::now())).css_classes(["nt-time"]).valign(gtk::Align::Start).build();
    top.append(&when);
    col.append(&top);
    if !n.body.is_empty() {
        col.append(
            &gtk::Label::builder()
                .label(model::markup(&n.body))
                .use_markup(true)
                .xalign(0.0)
                .wrap(true)
                .wrap_mode(gtk::pango::WrapMode::WordChar)
                .lines(BODY_LINES)
                .ellipsize(gtk::pango::EllipsizeMode::End)
                .max_width_chars(36)
                .css_classes(["nt-body"])
                .build(),
        );
    }
    let acts: Vec<&(String, String)> = n.actions.iter().filter(|(k, l)| k != "default" && !l.is_empty()).collect();
    if !acts.is_empty() {
        let buttons = gtk::Box::builder().spacing(6).margin_top(6).build();
        for (k, l) in acts.into_iter().take(3) {
            let b = gtk::Button::builder().label(l).css_classes(["nt-action"]).build();
            let (w, n, k) = (Rc::downgrade(server), n.clone(), k.clone());
            b.connect_clicked(move |_| {
                if let Some(s) = w.upgrade() {
                    s.invoke(&n, &k);
                }
            });
            buttons.append(&b);
        }
        col.append(&buttons);
    }
    row.append(&col);
    if !n.image.is_empty() {
        // (a full-size picture made the card huge: Vini)
        if let Some(tex) = thumbnail(&n.image, THUMB, 2) {
            let pic = gtk::Image::builder().paintable(&tex).pixel_size(THUMB).valign(gtk::Align::Center).overflow(gtk::Overflow::Hidden).css_classes(["nt-image"]).build();
            row.append(&pic);
        }
    }
    over.set_child(Some(&FixedWidth::new(&row, BANNER_W))); // a long title or body never widens it
    let click = gtk::GestureClick::new();
    let (w, note) = (Rc::downgrade(server), n.clone());
    click.connect_released(move |g, _, _, _| {
        if g.current_button() == 1 {
            if let Some(f) = &on_click {
                f();
            } else if let Some(s) = w.upgrade() {
                s.invoke(&note, "default");
            }
        }
    });
    row.add_controller(click);
    let close = gtk::Button::builder()
        .icon_name("window-close-symbolic")
        .css_classes(["nt-close"])
        .halign(gtk::Align::Start)
        .valign(gtk::Align::Start)
        .visible(false)
        .build();
    let (w, id) = (Rc::downgrade(server), n.id);
    close.connect_clicked(move |_| {
        if let Some(f) = &on_close {
            f();
        } else if let Some(s) = w.upgrade() {
            s.close(id, model::DISMISSED);
        }
    });
    over.add_overlay(&close);
    let hover = gtk::EventControllerMotion::new();
    let c = close.clone();
    hover.connect_enter(move |_, _, _| c.set_visible(true));
    let c = close.clone();
    hover.connect_leave(move |_| c.set_visible(false));
    over.add_controller(hover);
    over.set_margin_start(6);
    over.set_margin_top(6);
    Card { root: over.upcast(), when }
}
