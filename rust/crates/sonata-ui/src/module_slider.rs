//! Control Center's capsule slider (sonata2/ui/controls.py's ModuleSlider)
//! and topbar.py's _slider_with_icon: shared by Control Center, the Sound
//! menu, the per-app mixer and the Game Bar's Audio widget.

use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib, graphene, gsk};
use std::cell::{Cell, RefCell};
use std::rc::Rc;

/// the capsule's height
pub const SLIDER_H: f32 = 22.0;

/// Big Sur speaker: slashed at 0, then one to three waves.
pub fn speaker_icon(v: f64) -> &'static str {
    if v <= 0.0 {
        "audio-volume-muted-symbolic"
    } else if v < 34.0 {
        "audio-volume-low-symbolic"
    } else if v < 67.0 {
        "audio-volume-medium-symbolic"
    } else {
        "audio-volume-high-symbolic"
    }
}

/// The value under the pointer at `x` on a slider `width` wide: the
/// knob's centre follows the pointer.
pub fn value_at(x: f64, width: f64, lower: f64, upper: f64) -> f64 {
    let h = SLIDER_H as f64;
    let span = (width - h).max(1.0);
    let frac = ((x - h / 2.0) / span).clamp(0.0, 1.0);
    lower + frac * (upper - lower)
}

mod imp {
    use super::*;

    pub struct ModuleSlider {
        pub lower: Cell<f64>,
        pub upper: Cell<f64>,
        pub value: Cell<f64>,
        pub listeners: RefCell<Vec<Rc<dyn Fn(f64)>>>,
    }

    impl Default for ModuleSlider {
        fn default() -> Self {
            Self { lower: Cell::new(0.0), upper: Cell::new(100.0), value: Cell::new(0.0), listeners: RefCell::default() }
        }
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ModuleSlider {
        const NAME: &'static str = "SonataRsModuleSlider";
        type Type = super::ModuleSlider;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for ModuleSlider {}

    impl WidgetImpl for ModuleSlider {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let (w, h) = (obj.width() as f32, SLIDER_H);
            let y = (obj.height() as f32 - h) / 2.0;
            let r = h / 2.0;
            let frac = ((self.value.get() - self.lower.get()) / (self.upper.get() - self.lower.get()).max(1e-9)) as f32;
            let kx = (w - h) * frac; // the knob's left edge
            let dim = !obj.is_sensitive();
            if dim {
                snap.push_opacity(0.45);
            }
            let rounded = |x: f32, width: f32| gsk::RoundedRect::from_rect(graphene::Rect::new(x, y, width, h), r);
            snap.push_rounded_clip(&rounded(0.0, w));
            snap.append_color(&crate::theme::rgba("module_track"), &graphene::Rect::new(0.0, y, w, h));
            // the fill runs under the whole knob (Gtk.Scale's ends at its centre)
            snap.push_rounded_clip(&rounded(0.0, kx + h));
            snap.append_color(&crate::theme::rgba("module_fill"), &graphene::Rect::new(0.0, y, kx + h, h));
            snap.pop();
            snap.pop();
            let knob = rounded(kx, h);
            let rgba = |s: &str| gdk::RGBA::parse(s).unwrap_or(gdk::RGBA::BLACK);
            snap.append_outset_shadow(&knob, &rgba("rgba(0,0,0,0.28)"), 0.0, 0.0, 0.0, 2.5);
            snap.push_rounded_clip(&knob);
            snap.append_color(&gdk::RGBA::WHITE, &graphene::Rect::new(kx, y, h, h));
            snap.pop();
            let edge = rgba("rgba(0,0,0,0.12)");
            snap.append_border(&knob, &[0.5; 4], &[edge; 4]);
            if dim {
                snap.pop();
            }
        }
    }
}

glib::wrapper! {
    /// Control Center slider, drawn by hand so the knob and the fill line
    /// up exactly: a 22 px capsule, the fill under the whole knob.
    pub struct ModuleSlider(ObjectSubclass<imp::ModuleSlider>) @extends gtk::Widget;
}

impl ModuleSlider {
    pub fn new(value: f64, lower: f64, upper: f64) -> Self {
        let s: Self = glib::Object::builder().property("hexpand", true).property("valign", gtk::Align::Center).build();
        s.set_cursor_from_name(Some("default"));
        s.imp().lower.set(lower);
        s.imp().upper.set(upper);
        s.imp().value.set(value.clamp(lower, upper));
        s.set_size_request(120, SLIDER_H as i32);
        let drag = gtk::GestureDrag::new();
        let me = s.downgrade();
        drag.connect_drag_begin(move |_, x, _| {
            if let Some(s) = me.upgrade() {
                s.drag_to(x);
            }
        });
        let me = s.downgrade();
        drag.connect_drag_update(move |g, dx, _| {
            if let (Some(s), Some((x0, _))) = (me.upgrade(), g.start_point()) {
                s.drag_to(x0 + dx);
            }
        });
        s.add_controller(drag);
        s.connect_sensitive_notify(|s| s.queue_draw());
        let me = s.downgrade();
        crate::theme::on_change(move || {
            if let Some(s) = me.upgrade() {
                s.queue_draw();
            }
        });
        s
    }

    pub fn value(&self) -> f64 {
        self.imp().value.get()
    }

    /// Set it, and tell the listeners when it changed.
    pub fn set_value(&self, v: f64) {
        if self.store(v) {
            let ls: Vec<_> = self.imp().listeners.borrow().clone();
            for f in ls {
                f(self.value());
            }
        }
    }

    /// Move it to a level read from the system, without telling the
    /// listeners: they would set the same level again (controls.show_value).
    pub fn show_value(&self, v: f64) {
        self.store(v);
    }

    fn store(&self, v: f64) -> bool {
        let v = v.clamp(self.imp().lower.get(), self.imp().upper.get());
        if v == self.imp().value.get() {
            return false;
        }
        self.imp().value.set(v);
        self.queue_draw();
        true
    }

    pub fn connect_value_changed<F: Fn(f64) + 'static>(&self, f: F) {
        self.imp().listeners.borrow_mut().push(Rc::new(f));
    }

    fn drag_to(&self, x: f64) {
        if self.is_sensitive() {
            self.set_value(value_at(x, self.width() as f64, self.imp().lower.get(), self.imp().upper.get()));
        }
    }
}

/// Big Sur module slider: the symbol sits inside the capsule, left;
/// `button` (a round one) at the right, like the Sound module's AirPlay.
/// `icon(value)`: the symbol for a level (muted / levels).
pub fn slider_with_icon(icon: impl Fn(f64) -> String + 'static, value: f64, on_change: impl Fn(f64) + 'static, sensitive: bool, button: Option<&gtk::Button>) -> (gtk::Widget, ModuleSlider) {
    let over = gtk::Overlay::builder().css_classes(["cc-slider-box"]).hexpand(true).build();
    let sl = ModuleSlider::new(value, 0.0, 100.0);
    sl.set_sensitive(sensitive);
    sl.connect_value_changed(on_change);
    over.set_child(Some(&sl));
    let img = gtk::Image::builder().icon_name(icon(value)).css_classes(["cc-slider-icon"]).halign(gtk::Align::Start).valign(gtk::Align::Center).can_target(false).build();
    let i = img.clone();
    sl.connect_value_changed(move |v| i.set_icon_name(Some(&icon(v))));
    over.add_overlay(&img);
    let Some(button) = button else { return (over.upcast(), sl) };
    let row = gtk::Box::new(gtk::Orientation::Horizontal, 8);
    row.append(&over);
    row.append(button);
    (row.upcast(), sl)
}

