//! Videos' Trim bar (videos/trimbar.py; QuickTime Player's Edit > Trim):
//! it takes the control bar's place -- a filmstrip of the movie with a
//! yellow frame around the part kept; drag its ends (the movie shows the
//! frame there), Cancel or Trim.

use crate::edit as E;
use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, graphene, gsk};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

/// the filmstrip's height
const STRIP_H: i32 = 48;
/// the yellow frame's ends, where they are dragged
const HANDLE_W: f64 = 12.0;
/// its top and bottom
const FRAME_W: f64 = 3.0;
/// px around an end that still picks it up
const GRAB: f64 = 14.0;

fn rgba(r: f32, g: f32, b: f32, a: f32) -> gdk::RGBA {
    gdk::RGBA::new(r, g, b, a)
}

fn rect(x: f64, y: f64, w: f64, h: f64) -> graphene::Rect {
    graphene::Rect::new(x as f32, y as f32, w as f32, h as f32)
}

mod imp {
    use super::*;

    pub struct Filmstrip {
        pub duration: Cell<f64>,
        pub start: Cell<f64>,
        pub end: Cell<f64>,
        pub position: Cell<f64>,
        pub thumbs: RefCell<Vec<Option<gdk::Texture>>>,
        pub grab: RefCell<Option<&'static str>>,
        pub x0: Cell<f64>,
        pub on_change: RefCell<Option<Box<dyn Fn(f64, f64)>>>,
        pub on_seek: RefCell<Option<Rc<dyn Fn(f64)>>>,
    }

    impl Default for Filmstrip {
        fn default() -> Self {
            Self { duration: Cell::new(E::MIN_LENGTH), start: Cell::new(0.0), end: Cell::new(E::MIN_LENGTH), position: Cell::new(0.0), thumbs: RefCell::default(), grab: RefCell::default(), x0: Cell::new(0.0), on_change: RefCell::default(), on_seek: RefCell::default() }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Filmstrip {
        const NAME: &'static str = "SonataRsFilmstrip";
        type Type = super::Filmstrip;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for Filmstrip {}

    impl WidgetImpl for Filmstrip {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let h = obj.height() as f64;
            let (x0, sw) = obj.inner();
            let thumbs = self.thumbs.borrow();
            let cell = sw / thumbs.len().max(1) as f64;
            // the frames (each fills its cell, cropped to it), on black while they load
            snap.push_rounded_clip(&gsk::RoundedRect::from_rect(rect(x0, 0.0, sw, h), 4.0));
            snap.append_color(&sonata_ui::rgba("sys_black"), &rect(x0, 0.0, sw, h));
            for (i, tex) in thumbs.iter().enumerate() {
                let Some(tex) = tex else { continue };
                let (tw, th) = (tex.width() as f64, tex.height() as f64);
                let scale = (cell / tw).max(h / th);
                let (dw, dh) = (tw * scale, th * scale);
                snap.push_clip(&rect(x0 + i as f64 * cell, 0.0, cell + 0.5, h));
                snap.append_texture(tex, &rect(x0 + i as f64 * cell + (cell - dw) / 2.0, (h - dh) / 2.0, dw, dh));
                snap.pop();
            }
            // outside the part kept: dimmed
            let dim = rgba(0.0, 0.0, 0.0, 0.6);
            let (xs, xe) = (obj.x_of(self.start.get()), obj.x_of(self.end.get()));
            snap.append_color(&dim, &rect(x0, 0.0, (xs - x0).max(0.0), h));
            snap.append_color(&dim, &rect(xe, 0.0, (x0 + sw - xe).max(0.0), h));
            snap.pop();
            // the yellow frame: its ends wide (handles), top and bottom thin
            let yellow = sonata_ui::rgba("sys_yellow");
            snap.push_rounded_clip(&gsk::RoundedRect::from_rect(rect(xs - HANDLE_W, 0.0, xe - xs + 2.0 * HANDLE_W, h), 5.0));
            for r in [rect(xs - HANDLE_W, 0.0, HANDLE_W, h), rect(xe, 0.0, HANDLE_W, h), rect(xs, 0.0, xe - xs, FRAME_W), rect(xs, h - FRAME_W, xe - xs, FRAME_W)] {
                snap.append_color(&yellow, &r);
            }
            snap.pop();
            // a grip on each handle (QuickTime's chevrons, as short bars)
            let grip = rgba(0.0, 0.0, 0.0, 0.55);
            for cx in [xs - HANDLE_W / 2.0, xe + HANDLE_W / 2.0] {
                snap.append_color(&grip, &rect(cx - 1.0, h / 2.0 - 7.0, 2.0, 14.0));
            }
            // the playhead
            let p = self.position.get();
            if self.start.get() <= p && p <= self.end.get() {
                snap.append_color(&rgba(1.0, 1.0, 1.0, 1.0), &rect(obj.x_of(p) - 1.0, -1.0, 2.0, h + 2.0));
            }
        }
    }
}

glib::wrapper! {
    /// The movie's frames side by side, the part outside start..end dimmed,
    /// a yellow frame around the part kept, a white line at the playhead.
    pub struct Filmstrip(ObjectSubclass<imp::Filmstrip>) @extends gtk::Widget;
}

impl Filmstrip {
    fn new(duration: f64, count: usize, on_seek: Rc<dyn Fn(f64)>) -> Self {
        let s: Self = glib::Object::builder().property("hexpand", true).property("height-request", STRIP_H).build();
        let imp = s.imp();
        let d = duration.max(E::MIN_LENGTH);
        imp.duration.set(d);
        imp.end.set(d);
        imp.thumbs.replace(vec![None; count]);
        imp.on_seek.replace(Some(on_seek));
        let drag = gtk::GestureDrag::new();
        let w = s.downgrade();
        drag.connect_drag_begin(move |_, x, _| {
            if let Some(s) = w.upgrade() {
                s.imp().x0.set(x);
                s.imp().grab.replace(Some(s.pick(x)));
                s.move_to(x);
            }
        });
        let w = s.downgrade();
        drag.connect_drag_update(move |_, dx, _| {
            if let Some(s) = w.upgrade() {
                if s.imp().grab.borrow().is_some() {
                    s.move_to(s.imp().x0.get() + dx);
                }
            }
        });
        let w = s.downgrade();
        drag.connect_drag_end(move |_, _, _| {
            if let Some(s) = w.upgrade() {
                s.imp().grab.replace(None);
            }
        });
        s.add_controller(drag);
        s
    }

    /// The filmstrip between the handles' room: x0, width.
    fn inner(&self) -> (f64, f64) {
        (HANDLE_W, (self.width() as f64 - 2.0 * HANDLE_W).max(1.0))
    }

    pub fn x_of(&self, t: f64) -> f64 {
        let (x0, w) = self.inner();
        x0 + w * t / self.imp().duration.get()
    }

    fn time_at(&self, x: f64) -> f64 {
        let (x0, w) = self.inner();
        ((x - x0) / w * self.imp().duration.get()).clamp(0.0, self.imp().duration.get())
    }

    /// What a press at x takes: an end of the frame, or the playhead.
    pub fn pick(&self, x: f64) -> &'static str {
        let ds = (x - (self.x_of(self.start()) - HANDLE_W / 2.0)).abs();
        let de = (x - (self.x_of(self.end()) + HANDLE_W / 2.0)).abs();
        if ds.min(de) <= GRAB {
            return if ds <= de { "start" } else { "end" };
        }
        "playhead"
    }

    pub fn start(&self) -> f64 {
        self.imp().start.get()
    }

    pub fn end(&self) -> f64 {
        self.imp().end.get()
    }

    fn seek(&self, t: f64) {
        let f = self.imp().on_seek.borrow().clone();
        if let Some(f) = f {
            f(t);
        }
    }

    fn move_to(&self, x: f64) {
        let t = self.time_at(x);
        let grab = *self.imp().grab.borrow();
        match grab {
            Some("start") => {
                self.set_range(t.min(self.end() - E::MIN_LENGTH), self.end());
                self.seek(self.start());
            }
            Some("end") => {
                self.set_range(self.start(), t.max(self.start() + E::MIN_LENGTH));
                self.seek(self.end());
            }
            _ => self.seek(t.min(self.end()).max(self.start())),
        }
    }

    pub fn set_range(&self, start: f64, end: f64) {
        let (s, e) = E::clamp_range(start, end, self.imp().duration.get());
        self.imp().start.set(s);
        self.imp().end.set(e);
        if let Some(f) = self.imp().on_change.borrow().as_ref() {
            f(s, e);
        }
        self.queue_draw();
    }

    pub fn set_position(&self, t: f64) {
        self.imp().position.set(t);
        self.queue_draw();
    }
}

pub struct TrimBar {
    pub root: gtk::Box,
    pub strip: Filmstrip,
    pub times: gtk::Label,
    progress: gtk::LevelBar,
    trim_btn: gtk::Button,
}

impl TrimBar {
    pub fn new(duration: f64, count: usize, on_seek: Rc<dyn Fn(f64)>, on_cancel: impl Fn() + 'static, on_trim: impl Fn(f64, f64) + 'static) -> Rc<Self> {
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).css_classes(["vd-trim"]).halign(gtk::Align::Fill).valign(gtk::Align::End).margin_bottom(22).margin_start(22).margin_end(22).build();
        let strip = Filmstrip::new(duration, count, on_seek);
        root.append(&strip);
        let row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        let times = gtk::Label::builder().xalign(0.0).hexpand(true).css_classes(["vd-trim-time"]).build();
        row.append(&times);
        let progress = gtk::LevelBar::builder().min_value(0.0).max_value(1.0).hexpand(true).visible(false).valign(gtk::Align::Center).build();
        row.append(&progress);
        let cancel_btn = sonata_ui::controls::push_button(&tr!("Cancel"), on_cancel, "");
        let s = strip.downgrade();
        let trim_btn = sonata_ui::controls::push_button(&tr!("Trim"), move || {
            if let Some(s) = s.upgrade() {
                on_trim(s.start(), s.end());
            }
        }, "default");
        row.append(&cancel_btn);
        row.append(&trim_btn);
        root.append(&row);
        let me = Rc::new(Self { root, strip, times, progress, trim_btn });
        let t = me.times.downgrade();
        me.strip.imp().on_change.replace(Some(Box::new(move |s, e| {
            if let Some(t) = t.upgrade() {
                t.set_label(&format!("{} – {}   ({})", crate::fmt_time(s, false), crate::fmt_time(e, false), crate::fmt_time(e - s, false)));
            }
        })));
        let d = me.strip.imp().duration.get();
        me.strip.set_range(0.0, d);
        me
    }

    pub fn set_thumbnail(&self, i: usize, tex: gdk::Texture) {
        if let Some(slot) = self.strip.imp().thumbs.borrow_mut().get_mut(i) {
            *slot = Some(tex);
        }
        self.strip.queue_draw();
    }

    pub fn thumb_count(&self) -> usize {
        self.strip.imp().thumbs.borrow().len()
    }

    pub fn set_position(&self, t: f64) {
        self.strip.set_position(t);
    }

    /// None: back to choosing; 0..1: saving, the progress instead of the times.
    pub fn exporting(&self, fraction: Option<f64>) {
        let busy = fraction.is_some();
        self.times.set_visible(!busy);
        self.progress.set_visible(busy);
        self.progress.set_value(fraction.unwrap_or(0.0));
        self.trim_btn.set_sensitive(!busy);
        self.strip.set_sensitive(!busy);
    }
}
