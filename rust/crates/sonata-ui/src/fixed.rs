//! A container whose width never follows its content (ui/fixed.py):
//! panels, cards and widgets keep one width however long a title, a
//! network name or a file name is (they ellipsize or wrap instead). GTK
//! sizes a box by its children's natural widths, and an ellipsized label
//! still asks for its whole text, so a long name used to stretch them.

use gtk::prelude::*;
use gtk::subclass::prelude::*;
use gtk::glib;
use std::cell::Cell;

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct FixedWidth {
        pub width: Cell<i32>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for FixedWidth {
        const NAME: &'static str = "SonataRsFixedWidth";
        type Type = super::FixedWidth;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for FixedWidth {
        fn dispose(&self) {
            if let Some(child) = self.obj().first_child() {
                child.unparent();
            }
        }
    }

    impl WidgetImpl for FixedWidth {
        fn request_mode(&self) -> gtk::SizeRequestMode {
            gtk::SizeRequestMode::HeightForWidth
        }

        fn measure(&self, o: gtk::Orientation, for_size: i32) -> (i32, i32, i32, i32) {
            let Some(child) = self.obj().first_child() else { return (0, 0, -1, -1) };
            let w = self.fixed(&child);
            if o == gtk::Orientation::Horizontal {
                return (w, w, -1, -1);
            }
            let (mn, nat, _, _) = child.measure(o, for_size.max(w));
            (mn, nat, -1, -1)
        }

        fn size_allocate(&self, width: i32, height: i32, baseline: i32) {
            if let Some(child) = self.obj().first_child() {
                child.allocate(width, height, baseline, None);
            }
        }
    }

    impl FixedWidth {
        /// The fixed width, or the child's minimum when that is wider: GTK
        /// warns (and clips) when a child gets less than its minimum. The
        /// minimum never follows long text (labels ellipsize).
        fn fixed(&self, child: &gtk::Widget) -> i32 {
            self.width.get().max(child.measure(gtk::Orientation::Horizontal, -1).0)
        }
    }
}

glib::wrapper! {
    pub struct FixedWidth(ObjectSubclass<imp::FixedWidth>) @extends gtk::Widget;
}

impl FixedWidth {
    pub fn new(child: &impl IsA<gtk::Widget>, width: i32) -> Self {
        let f: Self = glib::Object::new();
        f.imp().width.set(width);
        child.set_parent(&f);
        f
    }
}
