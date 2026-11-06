//! Standard motion (sonata2/ui/transition.py), its CSS transition.py's own:
//! every popover opens with the same short fade + grow.
//!
//! Cross-fade for content that changes in place (a Settings section
//! rebuilt): a picture of the old content, over the new, fading out.
//!
//! ```no_run
//! # use gtk::prelude::*;
//! use sonata_ui::transition::{self, CrossFade, GlideBox};
//! let fade = CrossFade::new(&gtk::Stack::new()); // an overlay around the child
//! fade.capture();                                // before the change
//! // ... change the content ...
//! fade.play();                                   // when the new content is there
//!
//! let row = GlideBox::new(gtk::Orientation::Horizontal, 0);
//! let before = transition::glide_record(&[], row.upcast_ref()); // before re-ordering
//! // ... re-order ...
//! transition::glide_play(before, row.upcast_ref(), transition::GLIDE_MS); // they slide into place
//! ```
//!
//! A container of its own draws its children with [`snapshot_children`]
//! in its snapshot; a box can be a [`GlideBox`]. Cheap: one texture or one
//! offset per item; nothing runs when idle.

use adw::prelude::*;
use gtk::{glib, graphene, subclass::prelude::*};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::Rc;

pub(crate) fn register_css() {
    crate::css::register_module("ui/transition.py");
}

pub const DURATION_MS: u32 = 200;
pub const GLIDE_MS: u32 = 260;

// -- cross-fade ----------------------------------------------------------------------------
/// An overlay around content that changes in place: capture() freezes how
/// it looks, play() fades that picture out over the new content.
#[derive(Clone)]
pub struct CrossFade {
    pub overlay: gtk::Overlay,
    duration: u32,
    pic: Rc<RefCell<Option<gtk::Picture>>>,
    anim: Rc<RefCell<Option<adw::TimedAnimation>>>,
}

impl CrossFade {
    pub fn new(child: &impl IsA<gtk::Widget>) -> Self {
        Self::with_duration(child, DURATION_MS)
    }

    pub fn with_duration(child: &impl IsA<gtk::Widget>, duration: u32) -> Self {
        let overlay = gtk::Overlay::builder().child(child).build();
        CrossFade { overlay, duration, pic: Rc::default(), anim: Rc::default() }
    }

    /// Freeze how the content looks now (covers it until play()).
    pub fn capture(&self) {
        self.finish();
        let Some(child) = self.overlay.child() else { return };
        let (w, h) = (child.width(), child.height());
        let Some(renderer) = self.overlay.native().and_then(|n| n.renderer()) else { return };
        if !child.is_mapped() || w <= 0 || h <= 0 {
            return;
        }
        let snap = gtk::Snapshot::new();
        gtk::WidgetPaintable::new(Some(&child)).snapshot(&snap, w as f64, h as f64);
        let Some(node) = snap.to_node() else { return };
        let tex = renderer.render_texture(&node, Some(&graphene::Rect::new(0.0, 0.0, w as f32, h as f32)));
        let pic = gtk::Picture::builder().paintable(&tex).can_target(false).content_fit(gtk::ContentFit::Fill).build();
        self.overlay.add_overlay(&pic);
        self.pic.replace(Some(pic));
    }

    /// Fade the frozen picture out, revealing the new content.
    pub fn play(&self) {
        let Some(pic) = self.pic.borrow().clone() else { return };
        if self.anim.borrow().is_some() {
            return;
        }
        let target = adw::PropertyAnimationTarget::new(&pic, "opacity");
        let anim = adw::TimedAnimation::new(&pic, 1.0, 0.0, crate::tokens::ms(self.duration as f64), target);
        anim.set_easing(adw::Easing::EaseOutCubic);
        let me = self.clone();
        anim.connect_done(move |_| me.finish());
        self.anim.replace(Some(anim.clone()));
        anim.play();
    }

    /// Take a picture still on top away at once, without fading (a page
    /// switch is instant; a leftover picture would cover the new page).
    pub fn drop_picture(&self) {
        self.finish();
    }

    fn finish(&self) {
        let anim = self.anim.borrow_mut().take();
        if let Some(a) = anim.filter(|a| a.state() == adw::AnimationState::Playing) {
            a.skip();
        }
        if let Some(pic) = self.pic.borrow_mut().take() {
            self.overlay.remove_overlay(&pic);
        }
    }
}

// -- glide: items that change place slide there ---------------------------------------------
type Offset = (f64, f64, Option<adw::TimedAnimation>);

thread_local! {
    /// each gliding widget's offset from its laid-out place (and its
    /// animation), by widget; gone when the glide ends
    static GLIDES: RefCell<HashMap<usize, Offset>> = RefCell::new(HashMap::new());
}

fn key(w: &gtk::Widget) -> usize {
    use glib::translate::ToGlibPtr;
    let p: *mut gtk::ffi::GtkWidget = w.to_glib_none().0;
    p as usize
}

/// Where `w` is drawn now relative to its layout (zero when it isn't gliding).
pub fn glide_offset(w: &gtk::Widget) -> (f64, f64) {
    GLIDES.with(|g| g.borrow().get(&key(w)).map_or((0.0, 0.0), |o| (o.0, o.1)))
}

/// Draw `container`'s children, each shifted by its glide offset (call
/// from the container's snapshot instead of the parent class's).
pub fn snapshot_children(container: &impl IsA<gtk::Widget>, snapshot: &gtk::Snapshot) {
    let container = container.upcast_ref::<gtk::Widget>();
    let mut child = container.first_child();
    while let Some(c) = child {
        let (dx, dy) = glide_offset(&c);
        if dx != 0.0 || dy != 0.0 {
            snapshot.save();
            snapshot.translate(&graphene::Point::new(dx as f32, dy as f32));
            container.snapshot_child(&c, snapshot);
            snapshot.restore();
        } else {
            container.snapshot_child(&c, snapshot);
        }
        child = c.next_sibling();
    }
}

/// Where each widget was drawn: (widget, x, y, its parent).
pub type Glide = Vec<(gtk::Widget, f64, f64, Option<gtk::Widget>)>;

/// Where each widget is drawn now (layout + running glide), in
/// `reference`'s coordinates. Call before re-ordering.
pub fn glide_record(widgets: &[gtk::Widget], reference: &gtk::Widget) -> Glide {
    widgets
        .iter()
        .filter(|w| w.is_mapped())
        .filter_map(|w| {
            let p = w.compute_point(reference, &graphene::Point::new(0.0, 0.0))?;
            let (dx, dy) = glide_offset(w);
            Some((w.clone(), p.x() as f64 + dx, p.y() as f64 + dy, w.parent()))
        })
        .collect()
}

/// After re-ordering: every widget recorded in `before` that moved (and
/// kept its parent) slides from its old place to the new one. Runs in the
/// layout phase of the next frame, so the new place is never shown first.
pub fn glide_play(before: Glide, reference: &gtk::Widget, ms: u32) {
    let Some(clock) = reference.frame_clock() else { return };
    if before.is_empty() {
        return;
    }
    let id: Rc<Cell<Option<glib::SignalHandlerId>>> = Rc::default();
    let (id2, r) = (id.clone(), reference.downgrade());
    let handler = clock.connect_layout(move |clock| {
        if let Some(h) = id2.take() {
            clock.disconnect(h);
        }
        let Some(r) = r.upgrade() else { return };
        for (w, ox, oy, parent) in &before {
            if w.parent() != *parent || !w.is_mapped() {
                continue;
            }
            let Some(p) = w.compute_point(&r, &graphene::Point::new(0.0, 0.0)) else { continue };
            let (dx, dy) = (ox - p.x() as f64, oy - p.y() as f64);
            if dx.abs() >= 0.5 || dy.abs() >= 0.5 {
                start(w, dx, dy, ms);
            }
        }
    });
    id.set(Some(handler));
    reference.queue_allocate();
}

fn start(w: &gtk::Widget, dx: f64, dy: f64, ms: u32) {
    let k = key(w);
    let old = GLIDES.with(|g| g.borrow_mut().remove(&k)).and_then(|o| o.2);
    if let Some(a) = old {
        a.pause();
    }
    let parent = w.parent();
    let redraw = move || {
        if let Some(p) = parent.as_ref() {
            p.queue_draw(); // (the parent draws the offset)
        }
    };
    let r = redraw.clone();
    let target = adw::CallbackAnimationTarget::new(move |v| {
        GLIDES.with(|g| {
            if let Some(o) = g.borrow_mut().get_mut(&k) {
                (o.0, o.1) = (dx * v, dy * v);
            }
        });
        r();
    });
    // (the animation holds the widget: its key can't be reused while it runs)
    let anim = adw::TimedAnimation::new(w, 1.0, 0.0, crate::tokens::ms(ms as f64), target);
    anim.set_easing(adw::Easing::EaseOutCubic);
    GLIDES.with(|g| g.borrow_mut().insert(k, (dx, dy, Some(anim.clone()))));
    anim.connect_done(move |a| {
        GLIDES.with(|g| {
            let mut g = g.borrow_mut();
            if g.get(&k).and_then(|o| o.2.as_ref()) == Some(a) {
                g.remove(&k);
            }
        });
        redraw();
    });
    anim.play();
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct GlideBox;

    #[glib::object_subclass]
    impl ObjectSubclass for GlideBox {
        const NAME: &'static str = "SonataGlideBox";
        type Type = super::GlideBox;
        type ParentType = gtk::Box;
    }

    impl ObjectImpl for GlideBox {}
    impl BoxImpl for GlideBox {}

    impl WidgetImpl for GlideBox {
        fn snapshot(&self, snapshot: &gtk::Snapshot) {
            snapshot_children(&*self.obj(), snapshot);
        }
    }
}

glib::wrapper! {
    /// A gtk::Box whose children glide to a new place (glide_play) instead
    /// of jumping there.
    pub struct GlideBox(ObjectSubclass<imp::GlideBox>)
        @extends gtk::Box, gtk::Widget,
        @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

impl GlideBox {
    pub fn new(orientation: gtk::Orientation, spacing: i32) -> Self {
        glib::Object::builder().property("orientation", orientation).property("spacing", spacing).build()
    }
}
