//! Progress (ui/progress.py): the macOS spinner and the progress bar.
//! The progress window (stop buttons, rows) isn't ported yet.

use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{glib, graphene};
use std::cell::Cell;

const SPOKES: i64 = 12;
/// one turn a second
const PERIOD_US: i64 = 1_000_000;
/// the faintest spoke's opacity
const TAIL: f64 = 0.22;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct Spinner {
        pub spinning: Cell<bool>,
        pub step: Cell<i64>,
        pub tick: std::cell::RefCell<Option<glib::SourceId>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Spinner {
        const NAME: &'static str = "SonataRsSpinner";
        type Type = super::Spinner;
        type ParentType = gtk::Widget;

        fn class_init(klass: &mut Self::Class) {
            klass.set_css_name("spinner");
        }
    }

    impl ObjectImpl for Spinner {
        fn constructed(&self) {
            self.parent_constructed();
            let obj = self.obj();
            obj.connect_map(|s| s.sync());
            obj.connect_unmap(|s| s.sync());
        }

        fn dispose(&self) {
            if let Some(id) = self.tick.take() {
                id.remove();
            }
        }
    }

    impl WidgetImpl for Spinner {
        fn measure(&self, _o: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            (16, 16, -1, -1) // (width/height_request and CSS min sizes go on top)
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            if !self.spinning.get() {
                return;
            }
            let obj = self.obj();
            let (w, h) = (obj.width() as f64, obj.height() as f64);
            // its own size (width_request / CSS min-width), even when a stack
            // or a box hands it more room (Vini: huge on the login screen)
            let own = obj.size_request().0.max(obj.measure(gtk::Orientation::Horizontal, -1).0) as f64;
            let size = w.min(h).min(own);
            if size <= 0.0 {
                return;
            }
            let c = obj.color();
            let cr = snap.append_cairo(&graphene::Rect::new(((w - size) / 2.0) as f32, ((h - size) / 2.0) as f32, size as f32, size as f32));
            cr.translate(w / 2.0, h / 2.0);
            cr.set_line_cap(gtk::cairo::LineCap::Round);
            cr.set_line_width((size * 0.08).max(1.0));
            let outer = size / 2.0 - size * 0.05;
            let inner = outer - size * 0.24;
            for i in 0..SPOKES {
                let age = (self.step.get() - i).rem_euclid(SPOKES); // 0: the leading spoke
                let a = std::f64::consts::TAU * i as f64 / SPOKES as f64;
                let alpha = c.alpha() as f64 * (1.0 - (1.0 - TAIL) * age as f64 / (SPOKES - 1) as f64);
                cr.set_source_rgba(c.red() as f64, c.green() as f64, c.blue() as f64, alpha);
                cr.move_to(a.sin() * inner, -a.cos() * inner);
                cr.line_to(a.sin() * outer, -a.cos() * outer);
                let _ = cr.stroke();
            }
        }
    }
}

glib::wrapper! {
    /// The macOS spinner: 12 rounded spokes fading behind the leading one,
    /// stepping round once a second. Drawn by Sonata, not taken from the
    /// icon theme (adwaita-icon-theme 51 dropped the one Gtk.Spinner
    /// draws). CSS node "spinner", :checked while spinning, so its colour
    /// and size still come from CSS.
    pub struct Spinner(ObjectSubclass<imp::Spinner>) @extends gtk::Widget;
}

impl Default for Spinner {
    fn default() -> Self {
        glib::Object::new()
    }
}

impl Spinner {
    pub fn new(spinning: bool) -> Self {
        let s = Self::default();
        s.set_spinning(spinning);
        s
    }

    pub fn spinning(&self) -> bool {
        self.imp().spinning.get()
    }

    pub fn set_spinning(&self, on: bool) {
        self.imp().spinning.set(on);
        self.sync();
    }

    fn sync(&self) {
        let imp = self.imp();
        let on = imp.spinning.get();
        if on {
            self.set_state_flags(gtk::StateFlags::CHECKED, false); // (the theme shows a spinner only while :checked)
        } else {
            self.unset_state_flags(gtk::StateFlags::CHECKED);
        }
        let run = on && self.is_mapped();
        // a timer at the spokes' pace, not a tick callback: that kept the
        // frame clock -- and the compositor -- running at the display's
        // refresh rate to redraw 12 times a second
        if run && imp.tick.borrow().is_none() {
            let weak = self.downgrade();
            let id = glib::timeout_add_local(std::time::Duration::from_millis((PERIOD_US / SPOKES / 1000) as u64), move || {
                let Some(s) = weak.upgrade() else { return glib::ControlFlow::Break };
                let step = glib::monotonic_time() * SPOKES / PERIOD_US % SPOKES;
                if step != s.imp().step.get() {
                    s.imp().step.set(step); // redrawn 12 times a second, not every frame
                    s.queue_draw();
                }
                glib::ControlFlow::Continue
            });
            imp.tick.replace(Some(id));
        } else if !run {
            if let Some(id) = imp.tick.take() {
                id.remove();
            }
        }
        self.queue_draw();
    }
}

/// macOS progress bar (6 px, accent fill). None = indeterminate: call
/// .pulse() while waiting.
pub fn bar(fraction: Option<f64>) -> gtk::ProgressBar {
    crate::css::register_module("ui/progress.py");
    let b = gtk::ProgressBar::builder().css_classes(["sonata-progress"]).valign(gtk::Align::Center).build();
    if let Some(f) = fraction {
        b.set_fraction(f.clamp(0.0, 1.0));
    }
    b
}
