//! Launchpad's other layout, the Apps Menu (launchpad_window.py; Vini: the
//! "Applications" view of macOS 26): the same component -- the same apps,
//! folders, Hidden folder, menus, drags, jiggle mode -- on a glass panel in
//! the middle of the screen instead of over all of it.
//!
//! ```text
//! [icon] Apps (type to search)                                   [...]
//! [ Social ][ Creativity ][ Entertainment ][ Productivity & Finance ][ Utilities ][ Other ]
//! your apps and folders in the full screen's order (Hidden last);
//! a tab: that category's apps by name (again: back to your order)
//! ```
//!
//! MenuView is only the layout: Launchpad (pad.rs, mode Menu) renders its
//! pages into PageGrids stacked here instead of its carousel. Only the
//! category tabs are the menu's.

use crate::logic::{self, CATEGORIES, OTHER};
use crate::pad::Pad;
use crate::tiles::{PageGrid, Tile};
use gtk::prelude::*;
use gtk::{gdk, glib};
use sonata_desktop::launchpad_model::{self as m, Item};
use sonata_desktop::names;
use sonata_i18n::{gettext, tr};
use std::cell::RefCell;
use std::collections::BTreeMap;
use std::rc::Rc;
use std::time::Duration;

pub struct MenuView {
    pub tab: RefCell<Option<&'static str>>,
    /// PageGrids of the Launchpad's pages, stacked (pad.render fills them)
    pages: RefCell<Vec<Rc<PageGrid>>>,
    pub panel: gtk::Box,
    head: gtk::Box,
    pub content: gtk::Box,
    tabs: gtk::Box,
    tab_buttons: RefCell<Vec<(&'static str, gtk::ToggleButton)>>,
    body: gtk::Box,
    scroll: gtk::ScrolledWindow,
    room: gtk::Box,
    pub results: Rc<PageGrid>,
    tab_grid: RefCell<Rc<PageGrid>>,
    pub overlay: gtk::Overlay,
    pub root: gtk::Box,
    mon_surface: RefCell<Option<gdk::Surface>>,
}

impl MenuView {
    pub fn new(pad: &Rc<Pad>) -> Rc<MenuView> {
        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["lpw-panel"]).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
        let head = gtk::Box::builder().spacing(10).css_classes(["lpw-head"]).build();
        head.append(&gtk::Image::builder().icon_name("view-app-grid-symbolic").css_classes(["lpw-glyph"]).build());
        let more = gtk::Button::builder().child(&gtk::Label::builder().label("\u{2022}\u{2022}\u{2022}").css_classes(["lpw-dots"]).build()).css_classes(["lpw-more"]).can_focus(false).valign(gtk::Align::Center).tooltip_text(tr!("Options")).build();
        let w = Rc::downgrade(pad);
        more.connect_clicked(move |b| {
            if let Some(p) = w.upgrade() {
                p.options_menu(b);
            }
        });
        head.append(&more); // (Launchpad's search field goes before it: take_search)
        panel.append(&head);
        let content = gtk::Box::builder().orientation(gtk::Orientation::Vertical).vexpand(true).css_classes(["lpw-content"]).build();
        content.append(&gtk::Box::builder().css_classes(["lpw-rule"]).build());
        let tabs = gtk::Box::builder().spacing(8).homogeneous(true).build();
        content.append(&tabs);
        content.append(&gtk::Box::builder().css_classes(["lpw-rule"]).build());
        // (all the scrolled area's height: the room under the apps fills what
        // the grids leave -- they stay at the top)
        let body = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(4).margin_bottom(18).css_classes(["lpw-body"]).build();
        let scroll = gtk::ScrolledWindow::builder().child(&body).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build();
        content.append(&scroll);
        // the empty room under the apps takes a drag too -- the end of the last
        // page (Vini: an app dragged out of a folder, let go there, stayed in
        // it). A box of its own after the grids, not a drop target around them:
        // that one heard every motion over the grids too
        let room = gtk::Box::builder().vexpand(true).css_classes(["lpw-room"]).build();
        panel.append(&content);
        let results = PageGrid::new(pad, -1, m::cols(), m::rows()); // search results
        let tab_grid = PageGrid::new(pad, -3, m::cols(), m::rows()); // a category's apps (no reordering)
        for g in [&results, &tab_grid] {
            g.grid.set_vexpand(false);
        }
        // folder panels open over the menu (Launchpad's own: pad.host)
        let overlay = gtk::Overlay::builder().child(&panel).halign(gtk::Align::Center).valign(gtk::Align::Center).hexpand(true).build();
        let root = gtk::Box::builder().css_classes(["lpw-root"]).hexpand(true).vexpand(true).build();
        root.append(&overlay);
        let me = Rc::new(MenuView {
            tab: RefCell::default(),
            pages: RefCell::default(),
            panel,
            head,
            content,
            tabs,
            tab_buttons: RefCell::default(),
            body,
            scroll,
            room,
            results,
            tab_grid: RefCell::new(tab_grid),
            overlay,
            root,
            mon_surface: RefCell::default(),
        });
        let below = gtk::DropTarget::new(glib::Type::STRING, gdk::DragAction::MOVE);
        let w = Rc::downgrade(pad);
        below.connect_accept(move |_, _| w.upgrade().is_some_and(|p| p.drag.borrow().is_some()));
        let (w, mv) = (Rc::downgrade(pad), Rc::downgrade(&me));
        below.connect_motion(move |_, _, _| match (w.upgrade(), mv.upgrade()) {
            (Some(p), Some(mv)) => mv.at_end(&p).map(|(g, x, y)| p.drag_over(&g, x, y)).unwrap_or(gdk::DragAction::MOVE),
            _ => gdk::DragAction::empty(),
        });
        let (w, mv) = (Rc::downgrade(pad), Rc::downgrade(&me));
        below.connect_drop(move |_, v, _, _| match (w.upgrade(), mv.upgrade()) {
            (Some(p), Some(mv)) => mv.at_end(&p).is_some_and(|(g, x, y)| p.drag_drop(&g, x, y, v.get::<String>().ok())),
            _ => false,
        });
        me.room.add_controller(below);
        let click = gtk::GestureClick::new();
        let (w, mv) = (Rc::downgrade(pad), Rc::downgrade(&me));
        click.connect_released(move |_, _, x, y| {
            if let (Some(p), Some(mv)) = (w.upgrade(), mv.upgrade()) {
                mv.outside(&p, x, y);
            }
        });
        me.root.add_controller(click);
        // the glass panel's "solid" class in step with the setting
        let sync = {
            let panel = me.panel.downgrade();
            move || {
                if let Some(p) = panel.upgrade() {
                    if sonata_ui::glass() { p.remove_css_class("solid") } else { p.add_css_class("solid") }
                }
            }
        };
        sync();
        sonata_ui::on_change(sync);
        me
    }

    // -- Launchpad's search field, moved here while this layout shows --------------------

    pub fn take_search(&self, pad: &Pad) {
        let e = &pad.search;
        if e.parent().as_ref() == Some(self.head.upcast_ref()) {
            return;
        }
        e.unparent();
        e.remove_css_class("lp-search");
        e.add_css_class("lpw-search");
        e.set_placeholder_text(Some(&gettext(names::APPS)));
        e.set_hexpand(true);
        e.set_halign(gtk::Align::Fill);
        e.set_margin_top(0);
        // the title is the field: no magnifier in it
        if let Some(lens) = e.first_child().filter(|c| c.is::<gtk::Image>()) {
            lens.set_visible(false);
        }
        self.head.insert_child_after(e, self.head.first_child().as_ref());
    }

    pub fn give_search(&self, pad: &Pad) {
        let e = &pad.search;
        if e.parent().as_ref() != Some(self.head.upcast_ref()) {
            return;
        }
        self.head.remove(e);
        e.remove_css_class("lpw-search");
        e.add_css_class("lp-search");
        e.set_placeholder_text(Some(&tr!("Search")));
        e.set_hexpand(false);
        e.set_halign(gtk::Align::Center);
        e.set_margin_top(40);
        if let Some(lens) = e.first_child().filter(|c| c.is::<gtk::Image>()) {
            lens.set_visible(true);
        }
        pad.col.prepend(e);
    }

    // -- open / close ----------------------------------------------------------------------

    pub fn is_open(&self, pad: &Pad) -> bool {
        pad.win.is_visible() && !self.panel.has_css_class("closing")
    }

    /// The panel sized for the display it opens on (the one its surface was
    /// last on, else the main display -- not always the first one).
    pub(crate) fn size(&self, pad: &Pad, mon: Option<gdk::Monitor>) {
        let mon = mon.or_else(|| pad.win.surface().and_then(|s| WidgetExt::display(&pad.win).monitor_at_surface(&s))).or_else(sonata_shell::layer::main_monitor);
        let (w, h) = mon.map(|m| (m.geometry().width(), m.geometry().height())).unwrap_or((1600, 1000));
        let (pw, ph) = logic::panel_size(w, h);
        self.panel.set_size_request(pw, ph);
    }

    pub fn open(self: &Rc<Self>, pad: &Rc<Pad>) {
        self.size(pad, None);
        pad.search.set_text("");
        self.tab.replace(None);
        self.build_tabs(pad);
        pad.render(); // (fills the pages and shows them)
        self.panel.remove_css_class("closing");
        self.panel.remove_css_class("opening");
        pad.win.present();
        if let Some(surface) = pad.win.surface() {
            if self.mon_surface.borrow().as_ref() != Some(&surface) {
                // the compositor puts it on the focused display: sized again for that one
                self.mon_surface.replace(Some(surface.clone()));
                let (w, mv) = (Rc::downgrade(pad), Rc::downgrade(self));
                surface.connect_enter_monitor(move |_, m| {
                    if let (Some(p), Some(mv)) = (w.upgrade(), mv.upgrade()) {
                        mv.size(&p, Some(m.clone()));
                    }
                });
            }
        }
        self.panel.add_css_class("opening");
        pad.search.grab_focus();
        self.scroll.vadjustment().set_value(0.0);
    }

    pub fn close(&self, pad: &Rc<Pad>, then: crate::pad::Then) {
        if !pad.win.is_visible() {
            if let Some(t) = then {
                t();
            }
            return;
        }
        self.panel.remove_css_class("opening");
        self.panel.add_css_class("closing");
        let (w, panel) = (Rc::downgrade(pad), self.panel.downgrade());
        glib::timeout_add_local_once(Duration::from_millis(sonata_ui::tokens::ms(logic::MENU_CLOSE_MS) as u64), move || {
            if let (Some(p), Some(panel)) = (w.upgrade(), panel.upgrade()) {
                if panel.has_css_class("closing") {
                    p.win.set_visible(false);
                }
            }
            if let Some(t) = then {
                t();
            }
        });
    }

    pub fn inside(widget: &gtk::Widget, root: &gtk::Widget, x: f64, y: f64) -> bool {
        widget.compute_bounds(root).is_some_and(|b| (b.x() as f64..=(b.x() + b.width()) as f64).contains(&x) && (b.y() as f64..=(b.y() + b.height()) as f64).contains(&y))
    }

    /// A click outside the open folder closes the folder (Vini: only a click
    /// outside the whole menu did); outside the menu, the menu.
    fn outside(&self, pad: &Rc<Pad>, x: f64, y: f64) {
        let root: &gtk::Widget = self.root.upcast_ref();
        let fv = pad.folder_view.borrow().as_ref().map(|fv| (fv.panel.clone(), fv.wrap.first_child()));
        if let Some((panel, title)) = fv {
            let on_title = title.is_some_and(|t| Self::inside(&t, root, x, y));
            if !(Self::inside(panel.upcast_ref(), root, x, y) || on_title) {
                pad.close_folder();
            }
            return;
        }
        if !Self::inside(self.panel.upcast_ref(), root, x, y) {
            pad.close_launchpad(None);
        }
    }

    // -- what shows: the Launchpad's pages, a category, or the search results ----------------

    /// A PageGrid per Launchpad page (kept: tiles stay in them); the last
    /// one only as tall as its apps (no empty rows at the end).
    pub fn page_grids(&self, pad: &Rc<Pad>, pages: &[Vec<Item>]) -> Vec<Rc<PageGrid>> {
        let old = self.pages.borrow().clone();
        let out: Vec<Rc<PageGrid>> = pages
            .iter()
            .enumerate()
            .map(|(i, page)| {
                let rows = logic::menu_page_rows(i, pages.len(), page.len(), m::cols(), m::rows());
                match old.get(i) {
                    Some(g) if g.rows == rows && g.cols == m::cols() => g.clone(),
                    _ => {
                        let g = PageGrid::new(pad, i as i32, m::cols(), rows);
                        g.grid.set_vexpand(false);
                        g
                    }
                }
            })
            .collect();
        self.pages.replace(out.clone());
        out
    }

    /// Put the right grids in the body; grids already there stay.
    pub fn show(&self, pad: &Pad) {
        let want: Vec<gtk::Widget> = if !pad.search.text().trim().is_empty() {
            vec![self.results.grid.clone().upcast()]
        } else if self.tab.borrow().is_none() {
            // (the room under the apps: drops go to the end)
            self.pages.borrow().iter().map(|g| g.grid.clone().upcast()).chain([self.room.clone().upcast()]).collect()
        } else {
            vec![self.tab_grid.borrow().grid.clone().upcast()]
        };
        let mut have = Vec::new();
        let mut c = self.body.first_child();
        while let Some(w) = c {
            c = w.next_sibling();
            have.push(w);
        }
        if have == want {
            return;
        }
        for w in &have {
            self.body.remove(w);
        }
        for w in &want {
            if w.parent().is_some() {
                w.unparent();
            }
            self.body.append(w);
        }
    }

    /// Scroll so a tile chosen with the arrows is in view.
    pub fn reveal(&self, widget: &gtk::Button) {
        let Some(b) = widget.compute_bounds(&self.body) else { return };
        let adj = self.scroll.vadjustment();
        let (top, bottom) = (b.y() as f64, (b.y() + b.height()) as f64);
        if top < adj.value() {
            adj.set_value(top);
        } else if bottom > adj.value() + adj.page_size() {
            adj.set_value(bottom - adj.page_size());
        }
    }

    /// (grid, x, y) at the last cell of the last page (None: no drag or no
    /// page): the empty room under the apps.
    fn at_end(&self, pad: &Pad) -> Option<(Rc<PageGrid>, f64, f64)> {
        pad.drag.borrow().as_ref()?;
        let g = self.pages.borrow().iter().rfind(|g| g.grid.is_mapped()).cloned()?;
        let (w, h) = ((g.grid.width() - 1).max(0) as f64, (g.grid.height() - 1).max(0) as f64);
        Some((g, w, h))
    }

    pub fn visible_items(&self, pad: &Pad) -> Vec<Rc<Tile>> {
        if !pad.search.text().trim().is_empty() {
            return self.results.tiles.borrow().clone();
        }
        if self.tab.borrow().is_some() {
            return self.tab_grid.borrow().tiles.borrow().clone();
        }
        self.pages.borrow().iter().flat_map(|g| g.tiles.borrow().clone()).collect()
    }

    /// Category -> the apps on the grid (not those inside folders), by name.
    fn groups(&self, pad: &Pad) -> BTreeMap<&'static str, Vec<String>> {
        let installed = pad.installed.borrow();
        let mut groups: BTreeMap<&'static str, Vec<String>> = BTreeMap::new();
        for it in pad.model.borrow().pages.iter().flatten() {
            if let Item::App(a) = it {
                if let Some(info) = installed.get(a) {
                    groups.entry(logic::category_of(info.categories().as_deref().unwrap_or(""))).or_default().push(a.clone());
                }
            }
        }
        for keys in groups.values_mut() {
            keys.sort_by_key(|k| installed[k].display_name().to_lowercase());
        }
        groups
    }

    pub fn build_tabs(self: &Rc<Self>, pad: &Rc<Pad>) {
        while let Some(c) = self.tabs.first_child() {
            self.tabs.remove(&c);
        }
        let groups = self.groups(pad);
        let mut buttons = Vec::new();
        for (cid, title) in CATEGORIES.iter().map(|c| (c.0, c.1)).chain([OTHER]) {
            if groups.get(cid).is_none_or(Vec::is_empty) {
                continue;
            }
            let b = gtk::ToggleButton::builder().label(gettext(title)).css_classes(["lpw-tab"]).can_focus(false).build();
            let (w, mv) = (Rc::downgrade(pad), Rc::downgrade(self));
            b.connect_clicked(move |_| {
                if let (Some(p), Some(mv)) = (w.upgrade(), mv.upgrade()) {
                    let same = *mv.tab.borrow() == Some(cid);
                    mv.set_tab(&p, if same { None } else { Some(cid) });
                }
            });
            self.tabs.append(&b);
            buttons.push((cid, b));
        }
        self.tab_buttons.replace(buttons);
    }

    pub fn set_tab(&self, pad: &Rc<Pad>, cid: Option<&'static str>) {
        self.tab.replace(cid);
        for (c, b) in self.tab_buttons.borrow().iter() {
            b.set_active(Some(*c) == cid);
        }
        if let Some(cid) = cid {
            let keys = self.groups(pad).remove(cid).unwrap_or_default();
            let rows = keys.len().div_ceil(m::cols()).max(1);
            let fresh = {
                let g = self.tab_grid.borrow();
                g.rows != rows || g.cols != m::cols()
            };
            if fresh {
                let g = PageGrid::new(pad, -3, m::cols(), rows);
                g.grid.set_vexpand(false);
                self.tab_grid.replace(g);
            }
            let tiles = keys.iter().map(|k| pad.item_widget(&Item::App(k.clone()))).collect();
            self.tab_grid.borrow().fill(tiles, None);
        }
        pad.select(-1);
        self.body.remove_css_class("lpw-body"); // the grids fade in
        let body = self.body.downgrade();
        glib::idle_add_local_once(move || {
            if let Some(b) = body.upgrade() {
                b.add_css_class("lpw-body");
            }
        });
        if cid.is_none() {
            pad.render(); // your order again (the tiles came back from the tab)
        } else {
            self.show(pad);
        }
        self.scroll.vadjustment().set_value(0.0);
    }
}
