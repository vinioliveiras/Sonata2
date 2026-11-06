//! Control Center's layout (shell/controlcenter.py): which modules it
//! shows, in which order, on a grid of 4 columns where each module has its
//! size (macOS' newer Control Center). Edited like Launchpad (Vini): hold
//! a module (or Edit Controls…) to edit; drag one to move it, its x takes
//! it out, "Add Controls" brings back what was taken out.
//!
//! ```text
//! order = load()                     module ids, from controlcenter.json
//! pack(order) -> {id: (col, row, w, h)}
//! ModuleGrid::new(widgets, order, width)
//! ```
//!
//! The modules themselves are built by cc.rs. When one moves, is taken
//! out (it shrinks away first) or put back (it grows in), the others glide
//! to their new places (sonata_ui::transition); a module sized by its
//! content grows and shrinks smoothly, and so does the grid's own height
//! (Control Center follows each frame through on_height). Not ported yet:
//! the jiggle and the x's look are ui/edit.py's CSS, not exported yet.

use adw::prelude::*;
use gtk::{gdk, glib, graphene, gsk, subclass::prelude::*};
use serde_json::{json, Value};
use sonata_i18n::{n_, tr};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::rc::Rc;

pub const COLS: i32 = 4;
/// The gap between modules, and Control Center's border around them: 12,
/// as macOS's measured (Vini's photo: border = gap, ~3.4 % of the width --
/// at 8 they read as cramped).
pub const SPACING: i32 = 12;
/// One grid row: every module is a whole number of these, always.
pub const UNIT_H: i32 = 56;
/// Control Center's width from the display's.
/// (12 wider than at an 8 px gap: the modules keep their size)
pub const WIDTH_MIN: i32 = 356;
pub const WIDTH_MAX: i32 = 412;
pub const WIDTH_SHARE: f64 = 0.18;
/// Press and hold before edit mode starts (ui/edit.py).
pub const HOLD_MS: f64 = 800.0;

/// The fixed modules: id, title in Add Controls (shown through tr), (columns, rows).
const FIXED: [(&str, &str, (i32, i32)); 10] = [
    ("connectivity", n_!("Wi-Fi & Bluetooth"), (2, 2)),
    ("dnd", n_!("Do Not Disturb"), (2, 1)),
    ("darkmode", n_!("Dark Mode"), (1, 1)),
    ("screenshot", n_!("Screenshot"), (1, 1)),
    ("display", n_!("Display"), (4, 2)),
    ("sound", n_!("Sound"), (4, 2)),
    ("nowplaying", n_!("Now Playing"), (4, 1)),
    // each app's volume; grows a row per app (set_rows)
    ("mixer", n_!("Volume Mixer"), (4, 2)),
    // games' frame rate + frame-time graph (fpsmodule.py; Add Controls)
    ("fpslimit", n_!("FPS Limit"), (4, 2)),
    // input sources, a click switches (kbdmodule.py; Add Controls)
    ("keyboard", n_!("Keyboard"), (4, 1)),
];

pub const DEFAULT_ORDER: [&str; 7] = ["connectivity", "dnd", "darkmode", "screenshot", "display", "sound", "nowplaying"];

/// Every module id in the catalog's order, the performance ones (statsui:
/// not in the default layout, offered by Add Controls) after the fixed ones.
pub fn catalog_in(kinds: &[String]) -> Vec<String> {
    FIXED.iter().map(|f| f.0.to_string()).chain(kinds.iter().map(|k| format!("stat_{k}"))).collect()
}

pub fn catalog() -> Vec<String> {
    catalog_in(crate::statsui::kinds())
}

pub fn known(mid: &str) -> bool {
    FIXED.iter().any(|f| f.0 == mid) || mid.strip_prefix("stat_").is_some_and(|k| crate::statsui::kinds().iter().any(|x| x == k))
}

/// (columns, rows) of a module; a performance one is 2x2.
pub fn size(mid: &str) -> (i32, i32) {
    FIXED.iter().find(|f| f.0 == mid).map_or((2, 2), |f| f.2)
}

/// Its name in Add Controls, translated.
pub fn title(mid: &str) -> String {
    match FIXED.iter().find(|f| f.0 == mid) {
        Some(f) => sonata_i18n::gettext(f.1),
        None => crate::statsui::title(mid.strip_prefix("stat_").unwrap_or(mid)),
    }
}

/// Control Center's width on a display this wide (logical px): the same
/// share of the screen, never narrower than the default layout needs.
pub fn width_for(screen_width: i32) -> i32 {
    if screen_width <= 0 {
        return WIDTH_MIN;
    }
    ((screen_width as f64 * WIDTH_SHARE).round_ties_even() as i32).clamp(WIDTH_MIN, WIDTH_MAX)
}

pub fn span_height(rows: i32) -> i32 {
    rows * UNIT_H + (rows - 1) * SPACING
}

/// The fewest whole grid rows (1..most) that hold `px` of content.
pub fn rows_for(px: f64, most: i32) -> i32 {
    let mut rows = 1;
    while rows < most && (span_height(rows) as f64) < px {
        rows += 1;
    }
    rows
}

/// The modules shown, in order, from what controlcenter.json holds: known
/// ids, each once; anything else -- the default layout.
pub fn order_from(saved: Option<&Value>, is_known: impl Fn(&str) -> bool) -> Vec<String> {
    let list: Vec<String> = match saved.and_then(Value::as_array) {
        Some(a) => a.iter().filter_map(|v| v.as_str().map(String::from)).collect(),
        None => DEFAULT_ORDER.iter().map(|s| s.to_string()).collect(),
    };
    let mut out: Vec<String> = Vec::new();
    for m in list {
        if is_known(&m) && !out.contains(&m) {
            out.push(m);
        }
    }
    out
}

pub fn load() -> Vec<String> {
    let cfg = sonata_config::load("controlcenter", &json!({"modules": null}));
    order_from(cfg.get("modules"), known)
}

/// Saved where this build writes (on trial `sonata2-rs/`: the Python
/// Control Center's layout is never changed by the Rust one).
pub fn save(order: &[String]) {
    if let Err(e) = sonata_config::update("controlcenter", &json!({"modules": order})) {
        eprintln!("sonata2-topbar: controlcenter.json: {e}");
    }
}

/// What Add Controls offers: the catalog's modules that aren't shown.
pub fn hidden_in(catalog: &[String], order: &[String]) -> Vec<String> {
    catalog.iter().filter(|m| !order.contains(m)).cloned().collect()
}

pub fn hidden(order: &[String]) -> Vec<String> {
    hidden_in(&catalog(), order)
}

pub type Place = (i32, i32, i32, i32);

/// {id: (col, row, w, h)}: each module in order at the first free place
/// where it fits (left to right, top to bottom), like the macOS grid.
/// rows: {id: h} for modules whose height follows their content (the mixer).
pub fn pack(order: &[String], cols: i32, rows: &HashMap<String, i32>) -> BTreeMap<String, Place> {
    let mut taken: HashSet<(i32, i32)> = HashSet::new();
    let mut out = BTreeMap::new();
    for m in order {
        let (w, h) = size(m);
        let h = rows.get(m).copied().unwrap_or(h);
        let w = w.min(cols);
        let mut row = 0;
        'rows: loop {
            for col in 0..=(cols - w) {
                let cells: Vec<(i32, i32)> = (0..w).flat_map(|dx| (0..h).map(move |dy| (col + dx, row + dy))).collect();
                if cells.iter().all(|c| !taken.contains(c)) {
                    taken.extend(cells);
                    out.insert(m.clone(), (col, row, w, h));
                    break 'rows;
                }
            }
            row += 1;
        }
    }
    out
}

/// One module on the grid: the module, a cover that takes its clicks while
/// editing, and its x.
pub struct Slot {
    pub root: gtk::Overlay,
    pub cover: gtk::Box,
    pub badge: gtk::Button,
}

impl Slot {
    fn set_editing(&self, on: bool) {
        self.cover.set_visible(on);
        self.badge.set_visible(on);
    }
}

mod imp {
    use super::*;

    #[derive(Default)]
    pub struct ModuleGrid {
        pub width: Cell<i32>,
        pub widgets: RefCell<HashMap<String, gtk::Widget>>,
        pub order: RefCell<Vec<String>>,
        pub places: RefCell<BTreeMap<String, Place>>,
        pub slots: RefCell<HashMap<String, Rc<Slot>>>,
        pub rows: RefCell<HashMap<String, i32>>,
        pub editing: Cell<bool>,
        pub dragging: RefCell<Option<String>>,
        pub on_change: RefCell<Option<Rc<dyn Fn()>>>,
        /// a module growing or shrinking to its new rows: its height now (set_rows)
        pub grow: RefCell<HashMap<String, f64>>,
        pub grow_anims: RefCell<HashMap<String, adw::TimedAnimation>>,
        /// the height drawn while it changes (resize_to); None: target_height
        pub height: Cell<Option<f64>>,
        pub height_anim: RefCell<Option<adw::TimedAnimation>>,
        /// each frame of a size change (Control Center's height)
        pub on_height: RefCell<Option<Rc<dyn Fn()>>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ModuleGrid {
        const NAME: &'static str = "SonataCcGrid";
        type Type = super::ModuleGrid;
        type ParentType = gtk::Widget;
    }

    impl ObjectImpl for ModuleGrid {
        fn dispose(&self) {
            for s in self.slots.borrow().values() {
                if s.root.parent().as_ref() == Some(self.obj().upcast_ref()) {
                    s.root.unparent();
                }
            }
        }
    }

    impl WidgetImpl for ModuleGrid {
        fn measure(&self, orientation: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
            let size = if orientation == gtk::Orientation::Horizontal {
                self.width.get()
            } else {
                self.height.get().map_or_else(|| self.obj().target_height(), |h| h.round_ties_even() as i32)
            };
            (size, size, -1, -1)
        }

        fn size_allocate(&self, _w: i32, _h: i32, _baseline: i32) {
            let obj = self.obj();
            for mid in self.order.borrow().iter() {
                let Some(slot) = self.slots.borrow().get(mid).cloned() else { continue };
                if slot.root.parent().as_ref() != Some(obj.upcast_ref()) {
                    continue;
                }
                let Some((x, y, w, h)) = obj.rect(mid) else { continue };
                slot.root.measure(gtk::Orientation::Horizontal, -1); // (GTK wants a measure before allocate)
                let t = gsk::Transform::new().translate(&graphene::Point::new(x as f32, y as f32));
                slot.root.allocate(w, h, -1, Some(t));
            }
        }

        fn snapshot(&self, snapshot: &gtk::Snapshot) {
            sonata_ui::transition::snapshot_children(&*self.obj(), snapshot); // modules glide when re-ordered
        }
    }
}

glib::wrapper! {
    /// The modules on the 4-column grid; edit mode reorders and removes.
    /// Each module gets exactly its cells (its span x the column width and
    /// UNIT_H): the same size in any layout, whatever is around it.
    pub struct ModuleGrid(ObjectSubclass<imp::ModuleGrid>) @extends gtk::Widget;
}

impl ModuleGrid {
    pub fn new(widgets: HashMap<String, gtk::Widget>, order: &[String], width: i32) -> Self {
        let g: Self = glib::Object::builder().build();
        g.add_css_class("cc-grid");
        let imp = g.imp();
        imp.width.set(width);
        imp.order.replace(order.iter().filter(|m| widgets.contains_key(*m)).cloned().collect());
        imp.widgets.replace(widgets);
        let target = gtk::DropTarget::new(glib::Type::STRING, gdk::DragAction::MOVE);
        let w = g.downgrade();
        target.connect_motion(move |_, x, y| {
            if let Some(g) = w.upgrade() {
                g.drag_over(x, y);
            }
            gdk::DragAction::MOVE
        });
        let w = g.downgrade();
        target.connect_drop(move |_, _, _, _| {
            if let Some(g) = w.upgrade() {
                g.changed();
            }
            true
        });
        g.add_controller(target);
        g.layout(false);
        g
    }

    /// on_change(): the order, editing or a module's size changed.
    pub fn set_on_change(&self, f: impl Fn() + 'static) {
        self.imp().on_change.replace(Some(Rc::new(f)));
    }

    /// on_height(): each frame of the grid's own size change.
    pub fn set_on_height(&self, f: impl Fn() + 'static) {
        self.imp().on_height.replace(Some(Rc::new(f)));
    }

    /// Control Center grows or shrinks to the modules smoothly (Vini: a
    /// module taken out made it jump); on_height() follows each frame.
    fn resize_to(&self, old: i32) {
        let new = self.target_height();
        if let Some(a) = self.imp().height_anim.take() {
            a.pause(); // a new size while one runs: from where it is now (`old`)
        }
        if old == new || !self.is_mapped() {
            self.imp().height.set(None);
            return;
        }
        self.imp().height.set(Some(old as f64));
        let w = self.downgrade();
        let target = adw::CallbackAnimationTarget::new(move |v| {
            if let Some(g) = w.upgrade() {
                g.height_step(v, new);
            }
        });
        let anim = adw::TimedAnimation::new(self, old as f64, new as f64, sonata_ui::tokens::ms(240.0), target);
        anim.set_easing(adw::Easing::EaseInOutCubic);
        self.imp().height_anim.replace(Some(anim.clone()));
        anim.play();
    }

    /// One frame of resize_to: the grid that tall, and whoever follows told.
    fn height_step(&self, v: f64, new: i32) {
        self.imp().height.set(if v == new as f64 { None } else { Some(v) });
        self.queue_resize();
        let f = self.imp().on_height.borrow().clone();
        if let Some(f) = f {
            f();
        }
    }

    fn notify_change(&self) {
        let f = self.imp().on_change.borrow().clone();
        if let Some(f) = f {
            f();
        }
    }

    pub fn order(&self) -> Vec<String> {
        self.imp().order.borrow().clone()
    }

    pub fn editing(&self) -> bool {
        self.imp().editing.get()
    }

    pub fn slot(&self, mid: &str) -> Option<Rc<Slot>> {
        self.imp().slots.borrow().get(mid).cloned()
    }

    fn col_w(&self) -> f64 {
        (self.imp().width.get() - (COLS - 1) * SPACING) as f64 / COLS as f64
    }

    /// Where a module is drawn: (x, y, width, height) in the grid.
    pub fn rect(&self, mid: &str) -> Option<(i32, i32, i32, i32)> {
        let (col, row, w, h) = *self.imp().places.borrow().get(mid)?;
        let cw = self.col_w();
        let height = self.imp().grow.borrow().get(mid).map_or(span_height(h), |g| g.round_ties_even() as i32);
        Some(((col as f64 * (cw + SPACING as f64)).round() as i32, row * (UNIT_H + SPACING), (w as f64 * cw + ((w - 1) * SPACING) as f64).round() as i32, height))
    }

    pub fn target_height(&self) -> i32 {
        let rows = self.imp().places.borrow().values().map(|(_, r, _, h)| r + h).max().unwrap_or(0);
        if rows > 0 { span_height(rows) } else { 0 }
    }

    /// A module sized by its content (the mixer: a row per app playing)
    /// takes `rows` grid rows: it grows or shrinks smoothly, the modules
    /// below glide along; the grid never gets wider.
    pub fn set_rows(&self, mid: &str, rows: i32) {
        let old = self.imp().places.borrow().get(mid).copied();
        if self.imp().rows.borrow().get(mid) == Some(&rows) && old.is_none_or(|o| o.3 == rows) {
            return;
        }
        self.imp().rows.borrow_mut().insert(mid.to_string(), rows);
        let Some(old) = old.filter(|o| o.3 != rows) else { return };
        let (start, end) = (span_height(old.3) as f64, span_height(rows) as f64);
        self.layout(true);
        let (w, m) = (self.downgrade(), mid.to_string());
        let target = adw::CallbackAnimationTarget::new(move |v| {
            let Some(g) = w.upgrade() else { return };
            if v == end {
                g.imp().grow.borrow_mut().remove(&m);
            } else {
                g.imp().grow.borrow_mut().insert(m.clone(), v);
            }
            g.queue_allocate();
        });
        self.imp().grow.borrow_mut().insert(mid.to_string(), start);
        let anim = adw::TimedAnimation::new(self, start, end, sonata_ui::tokens::ms(220.0), target);
        anim.set_easing(adw::Easing::EaseInOutCubic);
        // a new size while one runs: the old one stops where it is
        if let Some(a) = self.imp().grow_anims.borrow_mut().insert(mid.to_string(), anim.clone()) {
            a.pause();
        }
        anim.play();
        self.notify_change();
    }

    fn make_slot(&self, mid: &str) -> Rc<Slot> {
        if let Some(s) = self.slot(mid) {
            return s;
        }
        let odd = self.imp().slots.borrow().len() % 2 == 1;
        let child = self.imp().widgets.borrow().get(mid).cloned();
        let root = gtk::Overlay::new();
        for c in ["cc-slot", "sonata-jiggle"] {
            root.add_css_class(c);
        }
        if odd {
            root.add_css_class("odd");
        }
        if size(mid).0 > 2 {
            root.add_css_class("wide");
        }
        if let Some(child) = child {
            // the module fills its cells: the same size for the same span, always
            child.set_vexpand(true);
            child.set_valign(gtk::Align::Fill);
            root.set_child(Some(&child));
        }
        let cover = gtk::Box::builder().hexpand(true).vexpand(true).visible(false).build(); // the module doesn't react
        root.add_overlay(&cover);
        let badge = gtk::Button::builder().icon_name("window-close-symbolic").css_classes(["sonata-edit-badge"]).tooltip_text(tr!("Remove")).halign(gtk::Align::Start).valign(gtk::Align::Start).can_focus(false).visible(false).build();
        let (w, m) = (self.downgrade(), mid.to_string());
        badge.connect_clicked(move |_| {
            if let Some(g) = w.upgrade() {
                g.remove_module(&m);
            }
        });
        root.add_overlay(&badge); // inside: the scroller would clip it outside
        let hold = gtk::GestureLongPress::new();
        hold.set_delay_factor(HOLD_MS / 500.0);
        let w = self.downgrade();
        hold.connect_pressed(move |_, _, _| {
            if let Some(g) = w.upgrade() {
                g.set_editing(true);
            }
        });
        root.add_controller(hold);
        let drag = gtk::DragSource::new();
        drag.set_actions(gdk::DragAction::MOVE);
        let (w, m) = (self.downgrade(), mid.to_string());
        drag.connect_prepare(move |_, _, _| {
            let g = w.upgrade()?;
            // outside edit mode a drag is the module's own
            g.editing().then(|| gdk::ContentProvider::for_value(&m.to_value()))
        });
        let (w, m) = (self.downgrade(), mid.to_string());
        drag.connect_drag_begin(move |_, d| {
            let Some(g) = w.upgrade() else { return };
            g.imp().dragging.replace(Some(m.clone()));
            if let Some(slot) = g.slot(&m) {
                if let Some(child) = slot.root.child() {
                    let icon = gtk::DragIcon::for_drag(d);
                    let pic = gtk::Picture::for_paintable(&gtk::WidgetPaintable::new(Some(&child)));
                    pic.set_can_shrink(false);
                    pic.add_css_class("cc-drag-icon");
                    icon.set_child(Some(&pic));
                }
                slot.root.add_css_class("cc-slot-dragged");
            }
        });
        let (w, m) = (self.downgrade(), mid.to_string());
        drag.connect_drag_end(move |_, _, _| {
            let Some(g) = w.upgrade() else { return };
            if let Some(slot) = g.slot(&m) {
                slot.root.remove_css_class("cc-slot-dragged");
            }
            g.imp().dragging.replace(None);
        });
        root.add_controller(drag);
        let slot = Rc::new(Slot { root, cover, badge });
        slot.set_editing(self.editing());
        self.imp().slots.borrow_mut().insert(mid.to_string(), slot.clone());
        slot
    }

    /// Every module in its cells; glide: the ones that moved slide there.
    fn layout(&self, glide: bool) {
        let imp = self.imp();
        let before = if glide {
            let roots: Vec<gtk::Widget> = imp.slots.borrow().values().map(|s| s.root.clone().upcast()).collect();
            sonata_ui::transition::glide_record(&roots, self.upcast_ref())
        } else {
            Vec::new()
        };
        let old_h = (!imp.places.borrow().is_empty()).then(|| self.measure(gtk::Orientation::Vertical, -1).1);
        let places = pack(&imp.order.borrow(), COLS, &imp.rows.borrow());
        imp.places.replace(places);
        let slots: Vec<(String, Rc<Slot>)> = imp.slots.borrow().iter().map(|(k, v)| (k.clone(), v.clone())).collect();
        for (mid, slot) in slots {
            if !imp.places.borrow().contains_key(&mid) && slot.root.parent().as_ref() == Some(self.upcast_ref()) {
                slot.root.unparent();
            }
        }
        let order = imp.order.borrow().clone();
        for mid in order {
            let slot = self.make_slot(&mid);
            if slot.root.parent().as_ref() != Some(self.upcast_ref()) {
                slot.root.set_parent(self);
            }
        }
        self.queue_resize();
        if let Some(old) = old_h.filter(|_| glide) {
            self.resize_to(old);
        }
        sonata_ui::transition::glide_play(before, self.upcast_ref(), sonata_ui::transition::GLIDE_MS);
    }

    fn changed(&self) {
        save(&self.order());
        self.notify_change();
    }

    pub fn set_editing(&self, on: bool) {
        if on == self.editing() {
            return;
        }
        self.imp().editing.set(on);
        if on {
            self.add_css_class("jiggle");
        } else {
            self.remove_css_class("jiggle");
        }
        for slot in self.imp().slots.borrow().values() {
            slot.set_editing(on);
        }
        self.notify_change();
    }

    /// Another widget for a module, in the same place (Control Center kept
    /// between openings: the Keyboard module's sources changed).
    pub fn replace(&self, mid: &str, widget: &gtk::Widget) {
        self.imp().widgets.borrow_mut().insert(mid.to_string(), widget.clone());
        if let Some(slot) = self.slot(mid) {
            widget.set_vexpand(true);
            widget.set_valign(gtk::Align::Fill);
            slot.root.set_child(Some(widget));
        }
    }

    /// The put-back animation's class off every module: a kept Control
    /// Center played it again at each opening otherwise.
    pub fn clear_arriving(&self) {
        for s in self.imp().slots.borrow().values() {
            s.root.remove_css_class("cc-slot-arriving");
        }
    }

    pub fn remove_module(&self, mid: &str) {
        if !self.imp().order.borrow().iter().any(|m| m == mid) {
            return;
        }
        match self.slot(mid) {
            Some(slot) => {
                // it shrinks away first (Launchpad's delete), then the others glide in
                slot.root.add_css_class("cc-slot-leaving");
                let (w, m) = (self.downgrade(), mid.to_string());
                glib::timeout_add_local_once(std::time::Duration::from_millis(sonata_ui::tokens::ms(160.0) as u64), move || {
                    if let Some(g) = w.upgrade() {
                        g.take_out(&m);
                    }
                });
            }
            None => self.take_out(mid),
        }
    }

    fn take_out(&self, mid: &str) {
        if !self.imp().order.borrow().iter().any(|m| m == mid) {
            return;
        }
        self.imp().order.borrow_mut().retain(|m| m != mid);
        let slot = self.imp().slots.borrow_mut().remove(mid);
        if let Some(slot) = slot {
            if slot.root.parent().as_ref() == Some(self.upcast_ref()) {
                slot.root.unparent();
            }
            // let go of the module: put back (Add Controls) it goes into a
            // new slot -- still held by this one, it came back empty (Vini)
            slot.root.set_child(None::<&gtk::Widget>);
        }
        self.layout(true);
        self.changed();
    }

    pub fn add_module(&self, mid: &str) {
        if self.imp().order.borrow().iter().any(|m| m == mid) || !self.imp().widgets.borrow().contains_key(mid) {
            return;
        }
        self.imp().order.borrow_mut().push(mid.to_string());
        self.layout(true);
        if let Some(slot) = self.slot(mid) {
            slot.root.add_css_class("cc-slot-arriving");
        }
        self.changed();
    }

    pub fn move_to(&self, mid: &str, index: usize) {
        let mut order = self.imp().order.borrow().clone();
        let Some(at) = order.iter().position(|m| m == mid) else { return };
        let index = index.min(order.len() - 1);
        if at == index {
            return;
        }
        let m = order.remove(at);
        order.insert(index, m);
        self.imp().order.replace(order);
        self.layout(true);
    }

    /// The module under (x, y) in the grid's coordinates.
    pub fn slot_at(&self, x: f64, y: f64) -> Option<String> {
        self.order().into_iter().find(|mid| {
            self.rect(mid).is_some_and(|(rx, ry, w, h)| x >= rx as f64 && x < (rx + w) as f64 && y >= ry as f64 && y < (ry + h) as f64)
        })
    }

    fn drag_over(&self, x: f64, y: f64) {
        let Some(mid) = self.imp().dragging.borrow().clone() else { return };
        if let Some(over) = self.slot_at(x, y) {
            if over != mid {
                let i = self.order().iter().position(|m| *m == over).unwrap_or(0);
                self.move_to(&mid, i);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini, with a photo of macOS's: Control Center's own border (6 px)
    /// narrower than the gaps between its modules (8 px) looked cramped --
    /// the same width all round, as there.
    #[test]
    fn test_control_center_border_narrower_than_its_gaps() {
        let blocks = sonata_ui::css::blocks("shell/controlcenter.py");
        let (_, css, local) = blocks.iter().find(|(k, _, _)| k == "controlcenter").expect("its CSS");
        assert!(css.contains("popover.sonata-panel.cc-panel > contents { padding: %(cc_pad)spx; }"));
        assert_eq!(local.get("cc_pad").map(String::as_str), Some(SPACING.to_string().as_str()));
    }

    fn ids(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    fn pack_of(v: &[&str]) -> BTreeMap<String, Place> {
        pack(&ids(v), COLS, &HashMap::new())
    }

    /// Wi-Fi & Bluetooth 2x2 left; Do Not Disturb over Dark Mode + Screenshot right; rows below.
    #[test]
    fn test_default_layout_is_the_big_sur_one() {
        let p = pack_of(&DEFAULT_ORDER);
        assert_eq!(p["connectivity"], (0, 0, 2, 2));
        assert_eq!(p["dnd"], (2, 0, 2, 1));
        assert_eq!(p["darkmode"], (2, 1, 1, 1));
        assert_eq!(p["screenshot"], (3, 1, 1, 1));
        assert_eq!(["display", "sound", "nowplaying"].map(|m| p[m].1), [2, 4, 6]);
    }

    /// First free place where it fits, left to right, top to bottom.
    #[test]
    fn test_small_modules_fill_holes_first() {
        let p = pack_of(&["darkmode", "display", "screenshot"]);
        assert_eq!(p["darkmode"], (0, 0, 1, 1));
        assert_eq!(p["display"], (0, 1, 4, 2)); // doesn't fit beside: next row
        assert_eq!(p["screenshot"], (1, 0, 1, 1)); // back into the hole on row 0
    }

    #[test]
    fn test_never_overlaps() {
        let base = ["connectivity", "dnd", "darkmode", "screenshot"];
        let mut perms = vec![];
        for a in 0..4 {
            for b in 0..4 {
                for c in 0..4 {
                    for d in 0..4 {
                        let set: HashSet<_> = [a, b, c, d].into_iter().collect();
                        if set.len() == 4 {
                            perms.push([base[a], base[b], base[c], base[d]]);
                        }
                    }
                }
            }
        }
        assert_eq!(perms.len(), 24);
        for order in perms {
            let mut cells = vec![];
            for (col, row, w, h) in pack_of(&order).values() {
                cells.extend((0..*w).flat_map(|x| (0..*h).map(move |y| (col + x, row + y))));
            }
            let n = cells.len();
            assert_eq!(cells.into_iter().collect::<HashSet<_>>().len(), n);
        }
    }

    /// The mixer grows a row per app playing: the modules below move down.
    #[test]
    fn rows_given_by_content() {
        let rows = HashMap::from([("mixer".to_string(), 4)]);
        let p = pack(&ids(&["mixer", "nowplaying"]), COLS, &rows);
        assert_eq!(p["mixer"], (0, 0, 4, 4));
        assert_eq!(p["nowplaying"], (0, 4, 4, 1));
    }

    #[test]
    fn test_load_defaults_and_cleans_up() {
        let _e = crate::testenv::env();
        assert_eq!(load(), ids(&DEFAULT_ORDER));
        let k = |m: &str| FIXED.iter().any(|f| f.0 == m);
        // unknown and repeated ones dropped
        assert_eq!(order_from(Some(&json!(["sound", "nope", "sound", 5, "dnd"])), k), ids(&["sound", "dnd"]));
        assert_eq!(order_from(Some(&json!("junk")), k), ids(&DEFAULT_ORDER));
        save(&ids(&["sound", "dnd"]));
        assert_eq!(load(), ids(&["sound", "dnd"]));
    }

    /// FPS Limit: off by default, only through Add Controls (Vini).
    #[test]
    fn test_hidden_is_what_add_controls_offers() {
        let kinds = vec!["cpu".to_string(), "ram".to_string()];
        let cat = catalog_in(&kinds);
        let want: Vec<String> = cat.iter().filter(|m| m.starts_with("stat_") || ["mixer", "fpslimit", "keyboard"].contains(&m.as_str())).cloned().collect();
        assert_eq!(hidden_in(&cat, &ids(&DEFAULT_ORDER)), want);
        assert_eq!(hidden_in(&cat, &ids(&["dnd"])), cat.iter().filter(|m| *m != "dnd").cloned().collect::<Vec<_>>());
    }

    #[test]
    fn test_rows_for() {
        assert_eq!(rows_for(10.0, 6), 1);
        assert_eq!(rows_for(span_height(2) as f64, 6), 2);
        assert_eq!(rows_for(span_height(2) as f64 + 1.0, 6), 3);
        assert_eq!(rows_for(10_000.0, 6), 6);
    }

    #[test]
    fn test_width_follows_the_display() {
        assert_eq!(width_for(0), WIDTH_MIN);
        assert_eq!(width_for(1280), WIDTH_MIN);
        assert_eq!(width_for(2000), 360);
        assert_eq!(width_for(3840), WIDTH_MAX);
    }

    /// Vini: taking a module out made Control Center jump to its new size;
    /// it shrinks smoothly now, and the panel follows every frame. (Run
    /// where GTK can start: xvfb-run.)
    #[test]
    fn test_size_change_animates() {
        if adw::init().is_err() {
            return; // no display
        }
        let _e = crate::testenv::env(); // (after: the display's socket is in the real runtime dir)
        let order = ids(&DEFAULT_ORDER);
        let widgets: HashMap<String, gtk::Widget> = order.iter().map(|m| (m.clone(), gtk::Label::new(Some(m)).upcast())).collect();
        let g = ModuleGrid::new(widgets, &order, 300);
        let win = gtk::Window::new();
        win.set_child(Some(&g));
        win.present();
        let ctx = glib::MainContext::default();
        let until = std::time::Instant::now() + std::time::Duration::from_secs(3);
        while !g.is_mapped() && std::time::Instant::now() < until {
            ctx.iteration(false);
        }
        assert!(g.is_mapped());
        let before = g.measure(gtk::Orientation::Vertical, -1).1;
        let seen: Rc<RefCell<Vec<i32>>> = Rc::default();
        let (s, w) = (seen.clone(), g.downgrade());
        g.set_on_height(move || s.borrow_mut().push(w.upgrade().unwrap().measure(gtk::Orientation::Vertical, -1).1));
        g.take_out("nowplaying");
        let end = g.target_height();
        assert!(end < before);
        // the grid starts where it was, not at its new size
        assert_eq!(g.measure(gtk::Orientation::Vertical, -1).1, before);
        assert!(g.imp().height_anim.borrow().is_some());
        let mid = (before + end) as f64 / 2.0;
        g.height_step(mid, end); // mid-way: the grid (and the panel) in between
        assert_eq!(g.measure(gtk::Orientation::Vertical, -1).1, mid.round_ties_even() as i32);
        g.height_step(end as f64, end);
        assert_eq!(g.measure(gtk::Orientation::Vertical, -1).1, end);
        assert!(seen.borrow().len() >= 2); // every frame
        win.destroy();
    }
}
