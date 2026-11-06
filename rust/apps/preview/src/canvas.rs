//! Preview's picture view (sonata2/preview/canvas.py): draws the picture
//! with a scale and a centre instead of resizing a picture in a scrolled
//! window, so zooming and panning only redraw -- nothing is laid out again.
//!
//! - zoom None fits the window (never above 100 %); a number is a scale.
//! - Ctrl+wheel and pinch zoom around the pointer; wheel steps animate.
//! - Two-finger scroll / wheel / drag pan a picture larger than the view,
//!   with inertia after a trackpad flick.
//! - Double-click: actual size around the click, again: fit.
//! - set_texture(tex, fade) cross-fades (the slideshow).

use adw::prelude::*;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib, graphene, gsk};
use std::cell::{Cell, RefCell};
use std::rc::Rc;

pub const MIN_ZOOM: f64 = 0.1;
pub const MAX_ZOOM: f64 = 8.0;
/// a wheel notch with Ctrl
const WHEEL_STEP: f64 = 1.25;
/// inertia: velocity decays by e^-FRICTION per second
const FRICTION: f64 = 5.0;

type Rect = (f64, f64, f64, f64);

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct Canvas {
        pub texture: RefCell<Option<gdk::Texture>>,
        /// None: fit
        pub zoom: Cell<Option<f64>>,
        /// the picture point at the centre of the view
        pub cx: Cell<f64>,
        pub cy: Cell<f64>,
        /// while animating: (scale, cx, cy) shown
        pub view: Cell<Option<(f64, f64, f64)>>,
        /// the scale an animation heads to (wheel notches add up)
        pub target: Cell<Option<f64>>,
        pub anim: RefCell<Option<adw::TimedAnimation>>,
        pub fade: RefCell<Option<(gdk::Texture, Option<Rect>, adw::TimedAnimation, Rc<Cell<f64>>)>>,
        pub pointer: Cell<Option<(f64, f64)>>,
        pub inertia: RefCell<Option<gtk::TickCallbackId>>,
        pub on_zoom: RefCell<Option<Box<dyn Fn()>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for Canvas {
        const NAME: &'static str = "SonataRsPreviewCanvas";
        type Type = super::Canvas;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for Canvas {}

    impl WidgetImpl for Canvas {
        fn measure(&self, _o: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            (0, 0, -1, -1) // never asks the window for a size
        }

        fn size_allocate(&self, _w: i32, _h: i32, _baseline: i32) {
            let obj = self.obj();
            if let (Some(z), true) = (self.zoom.get(), self.texture.borrow().is_some() && self.view.get().is_none()) {
                let (cx, cy) = obj.clamped(z, self.cx.get(), self.cy.get());
                self.cx.set(cx);
                self.cy.set(cy);
            }
            obj.update_cursor();
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let Some(tex) = self.texture.borrow().clone() else { return };
            let rect = obj.image_rect();
            if let Some((old, old_rect, _anim, p)) = self.fade.borrow().as_ref() {
                snap.push_cross_fade(p.get());
                if let Some(r) = old_rect {
                    draw(snap, old, *r);
                }
                snap.pop();
                if let Some(r) = rect {
                    draw(snap, &tex, r);
                }
                snap.pop();
                return;
            }
            if let Some(r) = rect {
                draw(snap, &tex, r);
            }
        }
    }
}

fn draw(snap: &gtk::Snapshot, tex: &gdk::Texture, (x, y, w, h): Rect) {
    let s = w / tex.width().max(1) as f64;
    let filter = if s < 0.99 { gsk::ScalingFilter::Trilinear } else { gsk::ScalingFilter::Linear };
    snap.append_scaled_texture(tex, filter, &graphene::Rect::new(x.round() as f32, y.round() as f32, w.round() as f32, h.round() as f32));
}

glib::wrapper! {
    pub struct Canvas(ObjectSubclass<imp::Canvas>) @extends gtk::Widget;
}

impl Default for Canvas {
    fn default() -> Self {
        Self::new()
    }
}

impl Canvas {
    pub fn new() -> Self {
        let c: Canvas = glib::Object::builder().property("hexpand", true).property("vexpand", true).property("overflow", gtk::Overflow::Hidden).build();
        c.add_css_class("pv-canvas");
        c.input();
        c
    }

    pub fn texture(&self) -> Option<gdk::Texture> {
        self.imp().texture.borrow().clone()
    }

    pub fn zoom(&self) -> Option<f64> {
        self.imp().zoom.get()
    }

    pub fn target(&self) -> Option<f64> {
        self.imp().target.get()
    }

    pub fn set_on_zoom<F: Fn() + 'static>(&self, f: F) {
        *self.imp().on_zoom.borrow_mut() = Some(Box::new(f));
    }

    // -- geometry --------------------------------------------------------------------------------
    pub fn fit_scale(&self) -> f64 {
        let Some(t) = self.texture() else { return 1.0 };
        let (w, h) = (self.width() as f64, self.height() as f64);
        if w <= 1.0 || h <= 1.0 {
            return 1.0;
        }
        (w / t.width() as f64).min(h / t.height() as f64).min(1.0)
    }

    pub fn scale(&self) -> f64 {
        if let Some(v) = self.imp().view.get() {
            return v.0;
        }
        self.zoom().unwrap_or_else(|| self.fit_scale())
    }

    fn centre(&self) -> (f64, f64) {
        let imp = self.imp();
        if let Some(v) = imp.view.get() {
            return (v.1, v.2);
        }
        if let (None, Some(t)) = (imp.zoom.get(), self.texture()) {
            return (t.width() as f64 / 2.0, t.height() as f64 / 2.0);
        }
        (imp.cx.get(), imp.cy.get())
    }

    /// A centre that keeps the picture covering the view (centred when smaller).
    fn clamped(&self, s: f64, cx: f64, cy: f64) -> (f64, f64) {
        let Some(t) = self.texture() else { return (cx, cy) };
        let (tw, th) = (t.width() as f64, t.height() as f64);
        let (hw, hh) = (self.width() as f64 / 2.0 / s, self.height() as f64 / 2.0 / s);
        let cx = if tw <= 2.0 * hw { tw / 2.0 } else { cx.max(hw).min(tw - hw) };
        let cy = if th <= 2.0 * hh { th / 2.0 } else { cy.max(hh).min(th - hh) };
        (cx, cy)
    }

    /// Where the picture is drawn (x, y, w, h) in the canvas, or None.
    pub fn image_rect(&self) -> Option<Rect> {
        let t = self.texture()?;
        let s = self.scale();
        let (cx, cy) = self.centre();
        Some((self.width() as f64 / 2.0 - cx * s, self.height() as f64 / 2.0 - cy * s, t.width() as f64 * s, t.height() as f64 * s))
    }

    pub fn pannable(&self) -> bool {
        self.image_rect().is_some_and(|r| r.2 > self.width() as f64 + 0.5 || r.3 > self.height() as f64 + 0.5)
    }

    pub fn to_image(&self, x: f64, y: f64) -> (f64, f64) {
        let s = self.scale();
        let (cx, cy) = self.centre();
        (cx + (x - self.width() as f64 / 2.0) / s, cy + (y - self.height() as f64 / 2.0) / s)
    }

    fn update_cursor(&self) {
        self.set_cursor_from_name(if self.pannable() { Some("grab") } else { None });
    }

    // -- state ---------------------------------------------------------------------------------
    pub fn set_texture(&self, tex: Option<gdk::Texture>, fade: bool, keep_view: bool) {
        let imp = self.imp();
        let (old, old_rect) = (self.texture(), self.image_rect());
        let same_size = matches!((&old, &tex), (Some(o), Some(t)) if o.width() == t.width() && o.height() == t.height());
        self.stop();
        *imp.texture.borrow_mut() = tex.clone();
        if !(keep_view && same_size) {
            imp.zoom.set(None);
        }
        if let (Some(_), Some(z)) = (&tex, imp.zoom.get()) {
            let (cx, cy) = self.clamped(z, imp.cx.get(), imp.cy.get());
            imp.cx.set(cx);
            imp.cy.set(cy);
        }
        *imp.fade.borrow_mut() = None;
        if let (true, Some(old), Some(_)) = (fade, old, &tex) {
            let p = Rc::new(Cell::new(0.0));
            let (p2, w) = (p.clone(), self.downgrade());
            let target = adw::CallbackAnimationTarget::new(move |v| {
                p2.set(v);
                if let Some(c) = w.upgrade() {
                    c.queue_draw();
                }
            });
            let anim = adw::TimedAnimation::new(self, 0.0, 1.0, sonata_ui::tokens::ms(700.0), target);
            anim.set_easing(adw::Easing::EaseInOutCubic);
            let w = self.downgrade();
            anim.connect_done(move |_| {
                if let Some(c) = w.upgrade() {
                    *c.imp().fade.borrow_mut() = None;
                    c.queue_draw();
                }
            });
            *imp.fade.borrow_mut() = Some((old, old_rect, anim.clone(), p));
            anim.play();
        }
        self.changed();
    }

    /// z: a scale, or None to fit. anchor (x, y): the point of the view
    /// that stays put (default: the middle).
    pub fn set_zoom(&self, z: Option<f64>, anchor: Option<(f64, f64)>, animate: bool) {
        let imp = self.imp();
        let Some(t) = self.texture() else {
            imp.zoom.set(z);
            return;
        };
        let (w, h) = (self.width() as f64, self.height() as f64);
        let (s0, c0) = (self.scale(), self.centre());
        let z = z.map(|z| z.min(MAX_ZOOM).max(MIN_ZOOM.min(self.fit_scale())));
        let s1 = z.unwrap_or_else(|| self.fit_scale());
        let (ax, ay) = anchor.unwrap_or((w / 2.0, h / 2.0));
        let (px, py) = self.to_image(ax, ay); // the picture point under the anchor
        let centre_at = move |s: f64| (px - (ax - w / 2.0) / s, py - (ay - h / 2.0) / s);
        let end = match z {
            None => (t.width() as f64 / 2.0, t.height() as f64 / 2.0),
            Some(_) => {
                let (x, y) = centre_at(s1);
                self.clamped(s1, x, y)
            }
        };
        self.stop();
        if !animate || !self.is_mapped() || (s1 - s0).abs() < 1e-4 {
            imp.zoom.set(z);
            imp.cx.set(end.0);
            imp.cy.set(end.1);
            self.changed();
            return;
        }
        imp.target.set(Some(s1));
        let wk = self.downgrade();
        let step = adw::CallbackAnimationTarget::new(move |t| {
            let Some(c) = wk.upgrade() else { return };
            let s = (s0.ln() + (s1.ln() - s0.ln()) * t).exp(); // even steps to the eye
            let cc = match z {
                None => (c0.0 + (end.0 - c0.0) * t, c0.1 + (end.1 - c0.1) * t),
                Some(_) => {
                    let (x, y) = centre_at(s);
                    c.clamped(s, x, y)
                }
            };
            c.imp().view.set(Some((s, cc.0, cc.1)));
            c.queue_draw();
        });
        let anim = adw::TimedAnimation::new(self, 0.0, 1.0, sonata_ui::tokens::ms(250.0), step);
        anim.set_easing(adw::Easing::EaseOutCubic);
        let wk = self.downgrade();
        anim.connect_done(move |a| {
            let Some(c) = wk.upgrade() else { return };
            let imp = c.imp();
            let mine = imp.anim.borrow().as_ref() == Some(a);
            if mine {
                *imp.anim.borrow_mut() = None;
                imp.view.set(None);
                imp.target.set(None);
                imp.zoom.set(z);
                imp.cx.set(end.0);
                imp.cy.set(end.1);
                c.changed();
            }
        });
        *imp.anim.borrow_mut() = Some(anim.clone());
        imp.zoom.set(z);
        anim.play();
    }

    /// Ends an animation or a flick where it is.
    pub fn stop(&self) {
        let imp = self.imp();
        let anim = imp.anim.borrow_mut().take();
        if let Some(anim) = anim {
            let view = imp.view.get();
            anim.skip();
            if let Some(v) = view {
                // stay where the animation was
                imp.zoom.set(Some(v.0));
                imp.cx.set(v.1);
                imp.cy.set(v.2);
            }
            imp.view.set(None);
            imp.target.set(None);
        }
        if let Some(id) = imp.inertia.borrow_mut().take() {
            id.remove();
        }
    }

    /// Moves the picture by dx, dy view pixels; false when it can't move.
    pub fn pan_by(&self, dx: f64, dy: f64) -> bool {
        let imp = self.imp();
        let Some(s) = imp.zoom.get() else { return false };
        if self.texture().is_none() || !self.pannable() {
            return false;
        }
        let (cx, cy) = self.clamped(s, imp.cx.get() - dx / s, imp.cy.get() - dy / s);
        imp.cx.set(cx);
        imp.cy.set(cy);
        self.queue_draw();
        true
    }

    fn changed(&self) {
        self.update_cursor();
        self.queue_draw();
        if let Some(f) = self.imp().on_zoom.borrow().as_ref() {
            f();
        }
    }

    // -- input ---------------------------------------------------------------------------------
    fn input(&self) {
        let motion = gtk::EventControllerMotion::new();
        let w = self.downgrade();
        motion.connect_motion(move |_, x, y| {
            if let Some(c) = w.upgrade() {
                c.imp().pointer.set(Some((x, y)));
            }
        });
        let w = self.downgrade();
        motion.connect_leave(move |_| {
            if let Some(c) = w.upgrade() {
                c.imp().pointer.set(None);
            }
        });
        self.add_controller(motion);

        let scroll = gtk::EventControllerScroll::new(gtk::EventControllerScrollFlags::BOTH_AXES | gtk::EventControllerScrollFlags::KINETIC);
        let w = self.downgrade();
        scroll.connect_scroll_begin(move |_| {
            if let Some(c) = w.upgrade() {
                c.stop();
            }
        });
        let w = self.downgrade();
        scroll.connect_scroll(move |ctl, dx, dy| match w.upgrade() {
            Some(c) if c.on_scroll(ctl, dx, dy) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        let w = self.downgrade();
        scroll.connect_decelerate(move |_, vx, vy| {
            if let Some(c) = w.upgrade() {
                c.decelerate(vx, vy);
            }
        });
        self.add_controller(scroll);

        let pinch = gtk::GestureZoom::new();
        let state: Rc<Cell<(f64, Option<(f64, f64)>)>> = Rc::new(Cell::new((1.0, None)));
        let (w, st) = (self.downgrade(), state.clone());
        pinch.connect_begin(move |g, _| {
            let Some(c) = w.upgrade() else { return };
            c.stop();
            let anchor = g.bounding_box_center().map(|(x, y)| c.to_image(x, y));
            st.set((c.scale(), anchor));
        });
        let (w, st) = (self.downgrade(), state.clone());
        pinch.connect_scale_changed(move |g, factor| {
            let Some(c) = w.upgrade() else { return };
            let (s0, anchor) = st.get();
            let (Some((x, y)), Some((px, py)), true) = (g.bounding_box_center(), anchor, c.texture().is_some()) else { return };
            let (ww, hh) = (c.width() as f64, c.height() as f64);
            let s = (s0 * factor).min(MAX_ZOOM).max(MIN_ZOOM.min(c.fit_scale()));
            let imp = c.imp();
            imp.zoom.set(Some(s)); // fingers: follow them, no animation
            let (cx, cy) = c.clamped(s, px - (x - ww / 2.0) / s, py - (y - hh / 2.0) / s);
            imp.cx.set(cx);
            imp.cy.set(cy);
            c.changed();
        });
        let w = self.downgrade();
        pinch.connect_end(move |_, _| {
            if let Some(c) = w.upgrade() {
                if c.zoom().is_some_and(|z| z <= c.fit_scale() + 1e-3) {
                    c.set_zoom(None, None, true); // pinched in below fit: back to fit
                }
            }
        });
        self.add_controller(pinch);

        let drag = gtk::GestureDrag::new();
        let last = Rc::new(Cell::new((0.0, 0.0)));
        let (w, l) = (self.downgrade(), last.clone());
        drag.connect_drag_begin(move |_, _, _| {
            let Some(c) = w.upgrade() else { return };
            c.stop();
            l.set((0.0, 0.0));
            if c.pannable() {
                c.set_cursor_from_name(Some("grabbing"));
            }
        });
        let (w, l) = (self.downgrade(), last);
        drag.connect_drag_update(move |_, dx, dy| {
            let Some(c) = w.upgrade() else { return };
            let (lx, ly) = l.get();
            c.pan_by(dx - lx, dy - ly);
            l.set((dx, dy));
        });
        let w = self.downgrade();
        drag.connect_drag_end(move |_, _, _| {
            if let Some(c) = w.upgrade() {
                c.changed();
            }
        });
        self.add_controller(drag);

        let click = gtk::GestureClick::new();
        let w = self.downgrade();
        click.connect_pressed(move |_, n, x, y| {
            let Some(c) = w.upgrade() else { return };
            if n == 2 && c.texture().is_some() {
                let actual = c.zoom().is_some_and(|z| (z - 1.0).abs() < 1e-3);
                c.set_zoom(if actual { None } else { Some(1.0) }, Some((x, y)), true);
            }
        });
        self.add_controller(click);
    }

    fn on_scroll(&self, ctl: &gtk::EventControllerScroll, dx: f64, dy: f64) -> bool {
        if self.texture().is_none() {
            return false;
        }
        let wheel = ctl.unit() == gdk::ScrollUnit::Wheel;
        let state = ctl.current_event_state();
        if state.intersects(gdk::ModifierType::CONTROL_MASK | gdk::ModifierType::SUPER_MASK) {
            let anchor = self.imp().pointer.get();
            if wheel {
                // notches: animated steps that add up
                let base = self.target().unwrap_or_else(|| self.scale());
                self.set_zoom(Some(base * WHEEL_STEP.powf(-dy)), anchor, true);
            } else {
                // smooth (trackpad): follow the fingers
                self.set_zoom(Some(self.scale() * (-dy * 0.01).exp()), anchor, false);
            }
            return true;
        }
        if !self.pannable() {
            return false;
        }
        let step = if wheel { 48.0 } else { 1.0 };
        let (dx, dy) = if state.contains(gdk::ModifierType::SHIFT_MASK) && wheel { (dy, dx) } else { (dx, dy) };
        self.stop();
        self.pan_by(-dx * step, -dy * step);
        true
    }

    /// A trackpad flick: the picture glides on and slows down.
    fn decelerate(&self, vx: f64, vy: f64) {
        if !self.pannable() || vx.hypot(vy) < 50.0 {
            return;
        }
        let state = Rc::new(Cell::new((-vx, -vy, None::<f64>)));
        self.stop();
        let w = self.downgrade();
        let id = self.add_tick_callback(move |_, clock| {
            let Some(c) = w.upgrade() else { return glib::ControlFlow::Break };
            let now = clock.frame_time() as f64 / 1e6;
            let (vx, vy, t) = state.get();
            let Some(t) = t else {
                state.set((vx, vy, Some(now)));
                return glib::ControlFlow::Continue;
            };
            let dt = now - t;
            let decay = (-FRICTION * dt).exp();
            let moved = c.pan_by(vx * dt, vy * dt);
            let (vx, vy) = (vx * decay, vy * decay);
            state.set((vx, vy, Some(now)));
            if !moved || vx.hypot(vy) < 20.0 {
                c.imp().inertia.borrow_mut().take();
                return glib::ControlFlow::Break;
            }
            glib::ControlFlow::Continue
        });
        *self.imp().inertia.borrow_mut() = Some(id);
    }
}
