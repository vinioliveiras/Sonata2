//! Videos' Crop (videos/cropbar.py): a frame over the movie to drag
//! (inside: move it; its edges and corners: resize it), the rest dimmed,
//! with thirds lines like Photos; a bar with the shape (Free, 16:9, 4:3,
//! 1:1, 9:16), the size in pixels, Cancel and Crop.

use crate::edit as E;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, graphene};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

/// screen px around an edge / corner that pick it up
const GRAB: f64 = 14.0;
/// the corners' L marks
const HANDLE: f64 = 18.0;
const LINE: f64 = 3.0;

fn cursor_of(part: &str) -> Option<&'static str> {
    Some(match part {
        "move" => "move",
        "l" => "w-resize",
        "r" => "e-resize",
        "t" => "n-resize",
        "b" => "s-resize",
        "tl" => "nw-resize",
        "tr" => "ne-resize",
        "bl" => "sw-resize",
        "br" => "se-resize",
        _ => return None,
    })
}

type Rect = (i64, i64, i64, i64);

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct CropFrame {
        pub iw: Cell<i64>,
        pub ih: Cell<i64>,
        pub ratio: Cell<Option<f64>>,
        pub rect: Cell<Rect>,
        pub part: RefCell<String>,
        pub start: Cell<Option<Rect>>,
        pub on_change: RefCell<Option<Box<dyn Fn(Rect)>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for CropFrame {
        const NAME: &'static str = "SonataRsCropFrame";
        type Type = super::CropFrame;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for CropFrame {}

    impl WidgetImpl for CropFrame {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let (ox, oy, s) = obj.map();
            let (pw, ph) = (self.iw.get() as f64 * s, self.ih.get() as f64 * s);
            let (x, y, w, h) = obj.on_screen();
            let r = |x: f64, y: f64, w: f64, h: f64| graphene::Rect::new(x as f32, y as f32, w as f32, h as f32);
            let dim = gdk::RGBA::new(0.0, 0.0, 0.0, 0.55);
            for (a, b, c, d) in [(ox, oy, pw, y - oy), (ox, y + h, pw, oy + ph - y - h), (ox, y, x - ox, h), (x + w, y, ox + pw - x - w, h)] {
                if c > 0.0 && d > 0.0 {
                    snap.append_color(&dim, &r(a, b, c, d));
                }
            }
            let (white, thin) = (gdk::RGBA::new(1.0, 1.0, 1.0, 0.95), gdk::RGBA::new(1.0, 1.0, 1.0, 0.35));
            for i in [1.0, 2.0] {
                // thirds
                snap.append_color(&thin, &r(x + w * i / 3.0, y, 1.0, h));
                snap.append_color(&thin, &r(x, y + h * i / 3.0, w, 1.0));
            }
            for (a, b, c, d) in [(x, y, w, 1.0), (x, y + h - 1.0, w, 1.0), (x, y, 1.0, h), (x + w - 1.0, y, 1.0, h)] {
                snap.append_color(&white, &r(a, b, c, d));
            }
            let k = HANDLE.min(w / 3.0).min(h / 3.0); // thick L marks in the corners
            let t = LINE;
            for (a, b, c, d) in [(x, y, k, t), (x, y, t, k), (x + w - k, y, k, t), (x + w - t, y, t, k), (x, y + h - t, k, t), (x, y + h - k, t, k), (x + w - k, y + h - t, k, t), (x + w - t, y + h - k, t, k)] {
                snap.append_color(&white, &r(a, b, c, d));
            }
        }
    }
}

glib::wrapper! {
    /// The crop over the movie (letterboxed like the picture under it);
    /// rect: (x, y, w, h) in the movie's pixels.
    pub struct CropFrame(ObjectSubclass<imp::CropFrame>) @extends gtk::Widget;
}

impl CropFrame {
    pub fn new(iw: i64, ih: i64) -> Self {
        let f: Self = glib::Object::builder().property("hexpand", true).property("vexpand", true).build();
        let (iw, ih) = (iw.max(1), ih.max(1));
        f.imp().iw.set(iw);
        f.imp().ih.set(ih);
        f.imp().rect.set((0, 0, iw, ih));
        let drag = gtk::GestureDrag::new();
        let w = f.downgrade();
        drag.connect_drag_begin(move |_, x, y| {
            if let Some(f) = w.upgrade() {
                f.imp().part.replace(f.part_at(x, y));
                f.imp().start.set(Some(f.imp().rect.get()));
            }
        });
        let w = f.downgrade();
        drag.connect_drag_update(move |_, dx, dy| {
            let Some(f) = w.upgrade() else { return };
            let part = f.imp().part.borrow().clone();
            let Some(start) = f.imp().start.get().filter(|_| !part.is_empty()) else { return };
            let (_, _, s) = f.map();
            f.set_rect(E::drag_crop(start, &part, dx / s, dy / s, f.imp().iw.get(), f.imp().ih.get(), f.imp().ratio.get()));
        });
        let w = f.downgrade();
        drag.connect_drag_end(move |_, _, _| {
            if let Some(f) = w.upgrade() {
                f.imp().part.replace(String::new());
            }
        });
        f.add_controller(drag);
        let motion = gtk::EventControllerMotion::new();
        let w = f.downgrade();
        motion.connect_motion(move |_, x, y| {
            if let Some(f) = w.upgrade() {
                if f.imp().part.borrow().is_empty() {
                    let part = f.part_at(x, y);
                    f.set_cursor_from_name(cursor_of(&part));
                }
            }
        });
        f.add_controller(motion);
        f
    }

    pub fn rect(&self) -> Rect {
        self.imp().rect.get()
    }

    pub fn size(&self) -> (i64, i64) {
        (self.imp().iw.get(), self.imp().ih.get())
    }

    fn map(&self) -> (f64, f64, f64) {
        E::video_rect(self.imp().iw.get(), self.imp().ih.get(), self.width() as f64, self.height() as f64)
    }

    fn on_screen(&self) -> (f64, f64, f64, f64) {
        let (ox, oy, s) = self.map();
        let (x, y, w, h) = self.imp().rect.get();
        (ox + x as f64 * s, oy + y as f64 * s, w as f64 * s, h as f64 * s)
    }

    fn part_at(&self, x: f64, y: f64) -> String {
        let (ox, oy, s) = self.map();
        E::crop_part(self.imp().rect.get(), (x - ox) / s, (y - oy) / s, GRAB / s.max(1e-6))
    }

    /// A new shape: the biggest of it inside the picture, centred.
    pub fn set_ratio(&self, ratio: Option<f64>) {
        self.imp().ratio.set(ratio);
        self.set_rect(E::ratio_rect(self.imp().iw.get(), self.imp().ih.get(), ratio));
    }

    fn set_rect(&self, rect: Rect) {
        self.imp().rect.set(rect);
        if let Some(f) = self.imp().on_change.borrow().as_ref() {
            f(rect);
        }
        self.queue_draw();
    }
}

pub struct CropBar {
    pub root: gtk::Box,
    pub frame: CropFrame,
    pub size: gtk::Label,
    shapes: sonata_ui::controls::Segmented,
    progress: gtk::LevelBar,
    crop_btn: gtk::Button,
}

impl CropBar {
    pub fn new(frame: CropFrame, on_cancel: impl Fn() + 'static, on_crop: impl Fn(Rect) + 'static) -> Rc<Self> {
        let root = gtk::Box::builder().spacing(10).css_classes(["vd-trim"]).halign(gtk::Align::Center).valign(gtk::Align::End).margin_bottom(22).margin_start(22).margin_end(22).build();
        let me_cell: Rc<RefCell<Option<std::rc::Weak<CropBar>>>> = Rc::default();
        let c = me_cell.clone();
        let choices: Vec<(&str, String)> = E::RATIOS.iter().map(|(k, l, _)| (*k, sonata_i18n::gettext(l))).collect();
        let shapes = sonata_ui::controls::Segmented::new(&choices, Some("free"), move |k: &str| {
            if let Some(me) = c.borrow().as_ref().and_then(|w| w.upgrade()) {
                me.pick(k);
            }
        }, false);
        shapes.root.set_valign(gtk::Align::Center);
        root.append(&shapes.root);
        let size = gtk::Label::builder().css_classes(["vd-trim-time"]).width_chars(11).xalign(0.0).build();
        root.append(&size);
        let progress = gtk::LevelBar::builder().min_value(0.0).max_value(1.0).visible(false).valign(gtk::Align::Center).build();
        progress.set_size_request(120, -1);
        root.append(&progress);
        let cancel_btn = sonata_ui::controls::push_button(&tr!("Cancel"), on_cancel, "");
        let f = frame.downgrade();
        let crop_btn = sonata_ui::controls::push_button(&tr!("Crop"), move || {
            if let Some(f) = f.upgrade() {
                on_crop(f.rect());
            }
        }, "default");
        root.append(&cancel_btn);
        root.append(&crop_btn);
        let me = Rc::new(Self { root, frame, size, shapes, progress, crop_btn });
        me_cell.replace(Some(Rc::downgrade(&me)));
        let l = me.size.downgrade();
        let show = move |(_, _, w, h): Rect| {
            if let Some(l) = l.upgrade() {
                l.set_label(&format!("{} × {}", w - w % 2, h - h % 2)); // what the file gets (even sizes)
            }
        };
        show(me.frame.rect());
        me.frame.imp().on_change.replace(Some(Box::new(show)));
        me
    }

    pub fn pick(&self, key: &str) {
        self.shapes.select(key);
        self.frame.set_ratio(E::RATIOS.iter().find(|r| r.0 == key).and_then(|r| r.2));
    }

    pub fn exporting(&self, fraction: Option<f64>) {
        let busy = fraction.is_some();
        self.size.set_visible(!busy);
        self.progress.set_visible(busy);
        self.progress.set_value(fraction.unwrap_or(0.0));
        self.crop_btn.set_sensitive(!busy);
        self.shapes.root.set_sensitive(!busy);
        self.frame.set_sensitive(!busy);
    }
}
