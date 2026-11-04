//! Progress (ui/progress.py): the macOS spinner and the progress bar.
//! The progress window (stop buttons, rows) isn't ported yet.

use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{glib, graphene};
use std::cell::{Cell, RefCell};

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

/// meter turns red above this
const FULL: f64 = 0.9;

/// Capacity meter (4 px): used / total; red above 90 %.
pub fn meter(fraction: f64) -> gtk::ProgressBar {
    let m = bar(Some(fraction));
    m.add_css_class("meter");
    set_meter(&m, fraction);
    m
}

pub fn set_meter(m: &gtk::ProgressBar, fraction: f64) {
    m.set_fraction(fraction.clamp(0.0, 1.0));
    if fraction >= FULL { m.add_css_class("full") } else { m.remove_css_class("full") }
}

// -- operations window (ui/progress.py) -----------------------------------------------------
/// quick copies never flash a window
const SHOW_DELAY_MS: u64 = 700;
const WIDTH: i32 = 460;

/// One row of the progress window. Update it from the GTK thread.
pub struct Operation {
    pub root: gtk::Box,
    title: gtk::Label,
    pub bar: gtk::ProgressBar,
    detail: gtk::Label,
    on_cancel: Option<Box<dyn Fn()>>,
    started: std::time::Instant,
    done: std::cell::Cell<bool>,
}

impl Operation {
    pub fn set_title(&self, text: &str) {
        self.title.set_label(text);
    }

    /// Bytes done of total: fills the bar and estimates the time left.
    pub fn update(&self, done: u64, total: u64) {
        if total == 0 {
            self.bar.pulse();
            return;
        }
        let f = (done as f64 / total as f64).min(1.0);
        self.bar.set_fraction(f);
        let mut text = sonata_i18n::fmt(&sonata_i18n::tr!("{done} of {total}"), &[("done", &crate::fmt::size(done)), ("total", &crate::fmt::size(total))]);
        let elapsed = self.started.elapsed().as_secs_f64();
        text.push_str(" — ");
        if f > 0.02 && elapsed > 1.0 {
            text.push_str(&crate::fmt::eta(elapsed * (1.0 - f) / f));
        } else {
            text.push_str(&sonata_i18n::tr!("Estimating time remaining…"));
        }
        self.detail.set_label(&text);
    }

    pub fn set_detail(&self, text: &str) {
        self.detail.set_label(text);
    }

    pub fn finish(&self) {
        if self.done.replace(true) {
            return;
        }
        OPS.with(|w| {
            let mut st = w.borrow_mut();
            let Some((win, rows, ops)) = st.as_mut() else { return };
            ops.retain(|o| o.upgrade().is_some_and(|o| !std::ptr::eq(o.as_ref(), self)));
            rows.remove(&self.root);
            if ops.is_empty() {
                // gone, not hidden: the next copy opens a new window at its own
                // height (a hidden one came back as tall as it once was)
                win.destroy();
                *st = None;
            } else {
                win.set_default_size(WIDTH, 1); // a row less: shorter again
            }
        });
    }

    pub fn is_done(&self) -> bool {
        self.done.get()
    }
}

type OpsWindow = (gtk::Window, gtk::Box, Vec<std::rc::Weak<Operation>>);

thread_local! {
    static OPS: RefCell<Option<OpsWindow>> = const { RefCell::new(None) };
}

/// Add an operation to the progress window (shown after a short delay;
/// closing it leaves the operations running, like Finder).
pub fn start(title: &str, on_cancel: Option<Box<dyn Fn()>>, heading: Option<&str>) -> std::rc::Rc<Operation> {
    crate::css::register_module("ui/progress.py");
    let heading = heading.map(str::to_string).unwrap_or_else(|| sonata_i18n::tr!("Copy"));
    let root = gtk::Box::builder().spacing(12).css_classes(["sp-row"]).build();
    let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).hexpand(true).build();
    let title_l = gtk::Label::builder().label(title).xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::Middle).css_classes(["sp-title"]).build();
    let pbar = bar(None);
    let detail = gtk::Label::builder().label(sonata_i18n::tr!("Preparing…")).xalign(0.0).css_classes(["sp-detail"]).build();
    col.append(&title_l);
    col.append(&pbar);
    col.append(&detail);
    root.append(&col);
    let has_cancel = on_cancel.is_some();
    let op = std::rc::Rc::new(Operation { root, title: title_l, bar: pbar, detail, on_cancel, started: std::time::Instant::now(), done: std::cell::Cell::new(false) });
    if has_cancel {
        let stop = gtk::Button::builder().icon_name("window-close-symbolic").tooltip_text(sonata_i18n::tr!("Stop")).valign(gtk::Align::Center).css_classes(["sp-stop"]).build();
        let w = std::rc::Rc::downgrade(&op);
        stop.connect_clicked(move |_| {
            if let Some(o) = w.upgrade() {
                if let Some(c) = &o.on_cancel {
                    c();
                }
                o.finish();
            }
        });
        op.root.append(&stop);
    }
    OPS.with(|w| {
        let mut st = w.borrow_mut();
        let (win, rows, ops) = st.get_or_insert_with(|| {
            let win = gtk::Window::builder().default_width(WIDTH).resizable(false).hide_on_close(true).build();
            for c in ["sonata-progress-window", "sonata-glass-window"] {
                win.add_css_class(c); // frosted glass, like Get Info
            }
            crate::window::standard(&win);
            let rows = gtk::Box::new(gtk::Orientation::Vertical, 0);
            win.set_child(Some(&rows));
            (win, rows, Vec::new())
        });
        win.set_title(Some(&heading));
        rows.append(&op.root);
        ops.push(std::rc::Rc::downgrade(&op));
        let (wk, ow) = (win.downgrade(), std::rc::Rc::downgrade(&op));
        glib::timeout_add_local_once(std::time::Duration::from_millis(SHOW_DELAY_MS), move || {
            if let (Some(win), Some(o)) = (wk.upgrade(), ow.upgrade()) {
                if !o.is_done() {
                    win.present();
                }
            }
        });
    });
    op
}
