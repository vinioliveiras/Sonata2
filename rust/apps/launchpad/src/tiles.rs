//! An app or folder in the grid (LaunchItem), and a page of them (PageGrid).

use crate::logic::{self, HOLD_MS};
use crate::pad::Pad;
use gtk::prelude::*;
use gtk::{gdk, glib};
use sonata_desktop::launchpad_model::{Folder, Item};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::Rc;

pub struct Tile {
    pub button: gtk::Button,
    pub item: RefCell<Item>,
    pub size: i32,
    pub name: RefCell<String>,
    pub label: gtk::Label,
    /// the jiggle-mode x (apps that can be uninstalled)
    pub badge: Option<gtk::Button>,
    /// what a drag shows: the icon
    pub icon: gtk::Widget,
    /// a folder's apps and lock when its icon was drawn (else it's drawn again)
    pub drawn: (Vec<String>, bool),
}

impl Tile {
    pub fn new(pad: &Rc<Pad>, item: &Item, size: i32) -> Rc<Tile> {
        let button = gtk::Button::builder().css_classes(["lp-item"]).focus_on_click(false).can_focus(false).hexpand(true).vexpand(true).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let over = gtk::Overlay::new();
        let mut badge = None;
        let mut drawn = (Vec::new(), false);
        let (name, icon): (String, gtk::Widget) = match item {
            Item::Folder(f) => {
                // the same folder icon as the Dock's: exactly an app's frame (Vini),
                // its apps in a 3 x 3 grid; Hidden (locked): blank tiles and a lock
                let locked = f.kept.contains_key("locked") || logic::is_secure(f);
                let icon = sonata_dock::FolderIcon::new(&f.apps, size, locked, true, &["lp-folder"]);
                icon.set_halign(gtk::Align::Center);
                drawn = (f.apps.clone(), locked);
                (f.name.clone(), icon.upcast())
            }
            Item::App(id) => {
                let info = pad.info(id);
                let img = gtk::Image::builder().pixel_size(size).css_classes(["lp-icon"]).build();
                match &info {
                    Some(i) => sonata_ui::icons::set_image(&img, &sonata_ui::icons::app_icon(i.upcast_ref())),
                    None => img.set_icon_name(Some("application-x-executable")),
                }
                over.set_child(Some(&img));
                if let Some(info) = &info {
                    let did = info.id().map(|s| s.to_string()).unwrap_or_default();
                    if sonata_desktop::applock::locked(&did) {
                        over.add_overlay(&sonata_dock::lock_badge(size)); // Lock App: a padlock
                    }
                    if logic::removable(&did) {
                        let b = gtk::Button::builder().icon_name("window-close-symbolic").css_classes(["sonata-edit-badge"]).tooltip_text(tr!("Delete")).halign(gtk::Align::Start).valign(gtk::Align::Start).can_focus(false).visible(pad.jiggling.get()).build();
                        let (w, id) = (Rc::downgrade(pad), id.clone());
                        b.connect_clicked(move |_| {
                            if let Some(p) = w.upgrade() {
                                p.ask_delete(&id);
                            }
                        });
                        over.add_overlay(&b);
                        badge = Some(b);
                    }
                }
                (info.map(|i| i.display_name().to_string()).unwrap_or_else(|| id.clone()), img.upcast())
            }
        };
        if item.is_folder() {
            over.set_child(Some(&icon));
        }
        col.append(&over);
        // every tile as wide as a long name (cells >= 128 px fit it): the selection box is one size
        let label = gtk::Label::builder().label(name.as_str()).css_classes(["lp-label"]).ellipsize(gtk::pango::EllipsizeMode::End).max_width_chars(14).width_chars(14).justify(gtk::Justification::Center).build();
        col.append(&label);
        button.set_child(Some(&col));
        let tile = Rc::new(Tile { button, item: RefCell::new(item.clone()), size, name: RefCell::new(name), label, badge, icon, drawn });
        let (w, t) = (Rc::downgrade(pad), Rc::downgrade(&tile));
        tile.button.connect_clicked(move |_| {
            if let (Some(p), Some(t)) = (w.upgrade(), t.upgrade()) {
                p.activate_item(&t);
            }
        });
        // press and hold: jiggle mode (ui.edit.hold)
        let hold = gtk::GestureLongPress::new();
        hold.set_delay_factor(HOLD_MS as f64 / 500.0);
        let w = Rc::downgrade(pad);
        hold.connect_pressed(move |_, _, _| {
            if let Some(p) = w.upgrade() {
                p.set_jiggle(true, true);
            }
        });
        tile.button.add_controller(hold);
        let menu = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
        let (w, t) = (Rc::downgrade(pad), Rc::downgrade(&tile));
        menu.connect_pressed(move |_, _, x, y| {
            if let (Some(p), Some(t)) = (w.upgrade(), t.upgrade()) {
                p.item_menu(&t, x, y);
            }
        });
        tile.button.add_controller(menu);
        pad.attach_drag(&tile);
        tile
    }

    pub fn set_name_text(&self, name: &str) {
        self.name.replace(name.to_string());
        self.label.set_label(name);
    }

    pub fn folder(&self) -> Option<Folder> {
        match &*self.item.borrow() {
            Item::Folder(f) => Some(f.clone()),
            Item::App(_) => None,
        }
    }

    pub fn app(&self) -> Option<String> {
        match &*self.item.borrow() {
            Item::App(a) => Some(a.clone()),
            Item::Folder(_) => None,
        }
    }
}

/// A page: fixed cols x rows cells even when it isn't full (placeholders).
/// index: the page's number; -1 search results, -2 an open folder, -3 an
/// Apps Menu tab (below 0: no reordering).
pub struct PageGrid {
    pub grid: gtk::Grid,
    pub index: Cell<i32>,
    pub cols: usize,
    pub rows: usize,
    pub tiles: RefCell<Vec<Rc<Tile>>>,
    /// search results: what was found
    pub found: RefCell<Vec<String>>,
}

impl PageGrid {
    pub fn new(pad: &Rc<Pad>, index: i32, cols: usize, rows: usize) -> Rc<PageGrid> {
        let grid = gtk::Grid::builder().row_homogeneous(true).column_homogeneous(true).hexpand(true).vexpand(true).build();
        for i in 0..cols * rows {
            grid.attach(&gtk::Box::new(gtk::Orientation::Horizontal, 0), (i % cols) as i32, (i / cols) as i32, 1, 1);
        }
        let me = Rc::new(PageGrid { grid, index: Cell::new(index), cols, rows, tiles: RefCell::default(), found: RefCell::default() });
        let target = gtk::DropTarget::new(glib::Type::STRING, gdk::DragAction::MOVE);
        let (w, g) = (Rc::downgrade(pad), Rc::downgrade(&me));
        target.connect_motion(move |_, x, y| match (w.upgrade(), g.upgrade()) {
            (Some(p), Some(g)) => p.drag_over(&g, x, y),
            _ => gdk::DragAction::empty(),
        });
        let (w, g) = (Rc::downgrade(pad), Rc::downgrade(&me));
        target.connect_drop(move |_, v, x, y| match (w.upgrade(), g.upgrade()) {
            (Some(p), Some(g)) => p.drag_drop(&g, x, y, v.get::<String>().ok()),
            _ => false,
        });
        me.grid.add_controller(target);
        me
    }

    pub fn cell_at(&self, x: f64, y: f64) -> (usize, bool) {
        logic::cell_at(self.grid.width() as f64, self.grid.height() as f64, self.cols, self.rows, x, y)
    }

    pub fn right_half(&self, x: f64) -> bool {
        logic::right_half(self.grid.width() as f64, self.cols, x)
    }

    /// The tiles in order, `empty` (No Results) when there are none.
    pub fn fill(&self, tiles: Vec<Rc<Tile>>, empty: Option<gtk::Widget>) {
        let mut widgets: Vec<gtk::Widget> = tiles.iter().map(|t| t.button.clone().upcast()).collect();
        if widgets.is_empty() {
            widgets.extend(empty);
        }
        for i in 0..self.cols * self.rows {
            let (c, r) = ((i % self.cols) as i32, (i / self.cols) as i32);
            if let Some(old) = self.grid.child_at(c, r) {
                self.grid.remove(&old);
            }
            let w = widgets.get(i).cloned().unwrap_or_else(|| gtk::Box::new(gtk::Orientation::Horizontal, 0).upcast());
            if w.parent().is_some() {
                w.unparent();
            }
            self.grid.attach(&w, c, r, 1, 1);
        }
        self.tiles.replace(tiles.into_iter().take(self.cols * self.rows).collect());
    }
}
