//! ZoomBin (launchpad.py): one child, drawn with a zoom + opacity (the
//! open/close animation) over a scrim (+ the blurred wallpaper in previews).

use crate::logic::ZOOM_FROM;
use gtk::subclass::prelude::*;
use gtk::{gdk, glib, graphene, gsk, prelude::*};
use std::cell::{Cell, RefCell};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct ZoomBin {
        pub child: RefCell<Option<gtk::Widget>>,
        /// 0 closed .. 1 open
        pub progress: Cell<f64>,
        /// the blurred wallpaper (previews only)
        pub backdrop: RefCell<Option<gdk::Texture>>,
        /// the grid as one texture while it zooms (freeze)
        pub frozen: RefCell<Option<gdk::Texture>>,
        /// that texture, kept while the grid doesn't change
        pub cached: RefCell<Option<gdk::Texture>>,
        /// told the bin's size on every allocation (the grid follows it)
        pub on_size: RefCell<Option<Box<dyn Fn(i32, i32)>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ZoomBin {
        const NAME: &'static str = "SonataLaunchpadZoomBin";
        type Type = super::ZoomBin;
        type ParentType = gtk::Widget;

        fn class_init(klass: &mut Self::Class) {
            klass.set_layout_manager_type::<gtk::BinLayout>();
        }
    }

    impl ObjectImpl for ZoomBin {
        fn dispose(&self) {
            if let Some(c) = self.child.take() {
                c.unparent();
            }
        }
    }

    impl WidgetImpl for ZoomBin {
        fn size_allocate(&self, w: i32, h: i32, baseline: i32) {
            self.parent_size_allocate(w, h, baseline);
            if let Some(f) = self.on_size.borrow().as_ref() {
                f(w, h);
            }
        }

        fn snapshot(&self, snap: &gtk::Snapshot) {
            let obj = self.obj();
            let (w, h) = (obj.width() as f32, obj.height() as f32);
            let p = self.progress.get();
            let rect = graphene::Rect::new(0.0, 0.0, w, h);
            snap.push_opacity(p);
            if let Some(b) = self.backdrop.borrow().as_ref() {
                snap.append_texture(b, &rect);
            }
            snap.append_color(&sonata_ui::rgba("scrim"), &rect);
            snap.pop();
            if p <= 0.0 {
                return;
            }
            let s = (ZOOM_FROM + (1.0 - ZOOM_FROM) * p) as f32;
            snap.push_opacity(p);
            snap.save();
            snap.translate(&graphene::Point::new(w / 2.0, h / 2.0));
            snap.scale(s, s);
            snap.translate(&graphene::Point::new(-w / 2.0, -h / 2.0));
            match (self.frozen.borrow().as_ref(), self.child.borrow().as_ref()) {
                (Some(t), _) if p < 1.0 => snap.append_scaled_texture(t, gsk::ScalingFilter::Linear, &rect),
                (_, Some(c)) => obj.snapshot_child(c, snap),
                _ => {}
            }
            snap.restore();
            snap.pop();
        }
    }
}

glib::wrapper! {
    pub struct ZoomBin(ObjectSubclass<imp::ZoomBin>) @extends gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget;
}

impl ZoomBin {
    pub fn new(child: &impl IsA<gtk::Widget>) -> Self {
        let bin: Self = glib::Object::new();
        child.set_parent(&bin);
        bin.imp().child.replace(Some(child.clone().upcast()));
        let weak = bin.downgrade();
        sonata_ui::on_change(move || {
            if let Some(b) = weak.upgrade() {
                b.invalidate();
                b.queue_draw();
            }
        });
        bin
    }

    pub fn progress(&self) -> f64 {
        self.imp().progress.get()
    }

    pub fn set_progress(&self, p: f64) {
        self.imp().progress.set(p);
        self.queue_draw();
    }

    pub fn set_backdrop(&self, t: Option<gdk::Texture>) {
        self.imp().backdrop.replace(t);
        self.queue_draw();
    }

    pub fn connect_size(&self, f: impl Fn(i32, i32) + 'static) {
        self.imp().on_size.replace(Some(Box::new(f)));
    }

    /// The grid changed (apps, page, search, folder, jiggle, size, look):
    /// the next animation draws a new picture of it.
    pub fn invalidate(&self) {
        self.imp().cached.replace(None);
    }

    /// Draw the grid once into a texture for the open/close animation: each
    /// frame then scales one picture instead of re-drawing every icon and
    /// label (smooth at 144 Hz, little CPU). The picture is kept for the
    /// next open while the grid stays the same: opening starts at once.
    pub fn freeze(&self) {
        let imp = self.imp();
        if let Some(c) = imp.cached.borrow().clone() {
            imp.frozen.replace(Some(c));
            return;
        }
        imp.frozen.replace(None);
        let (w, h) = (self.width(), self.height());
        let Some(renderer) = self.native().and_then(|n| n.renderer()) else { return };
        let Some(child) = imp.child.borrow().clone() else { return };
        if w <= 0 || h <= 0 {
            return;
        }
        let sf = self.scale_factor().max(1) as f32;
        let snap = gtk::Snapshot::new();
        snap.scale(sf, sf);
        self.snapshot_child(&child, &snap);
        let Some(node) = snap.to_node() else { return };
        let tex = renderer.render_texture(&node, Some(&graphene::Rect::new(0.0, 0.0, w as f32 * sf, h as f32 * sf)));
        imp.frozen.replace(Some(tex.clone()));
        imp.cached.replace(Some(tex));
    }

    pub fn thaw(&self) {
        self.imp().frozen.replace(None);
        self.queue_draw();
    }
}
