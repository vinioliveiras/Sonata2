//! Icons that change place slide there (launchpad.py's PageGrid with
//! ui.transition's glide): a page's grid draws each tile shifted by its
//! glide offset; render() records where the tiles were before re-ordering
//! and plays the slide once the new layout is known
//! (sonata_ui::transition).

use gtk::subclass::prelude::*;
use gtk::{glib, prelude::*};

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct GlideGrid;

    #[glib::object_subclass]
    impl ObjectSubclass for GlideGrid {
        const NAME: &'static str = "SonataLaunchpadGlideGrid";
        type Type = super::GlideGrid;
        type ParentType = gtk::Grid;
    }

    impl ObjectImpl for GlideGrid {}

    impl WidgetImpl for GlideGrid {
        fn snapshot(&self, snap: &gtk::Snapshot) {
            sonata_ui::transition::snapshot_children(&*self.obj(), snap); // icons glide when re-ordered
        }
    }

    impl GridImpl for GlideGrid {}
}

glib::wrapper! {
    pub struct GlideGrid(ObjectSubclass<imp::GlideGrid>) @extends gtk::Grid, gtk::Widget, @implements gtk::Accessible, gtk::Buildable, gtk::ConstraintTarget, gtk::Orientable;
}

/// A page's grid that draws its tiles where they glide (a gtk::Grid to
/// everyone else).
pub fn grid() -> gtk::Grid {
    glib::Object::new::<GlideGrid>().upcast()
}
