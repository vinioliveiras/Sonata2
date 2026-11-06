//! The Launchpad window (launchpad.py's Launchpad): the pages, the search,
//! folders, jiggle mode, menus, drags; and the Apps Menu layout's switch
//! (menu.rs only lays it out).

use crate::logic::{self, Arrow, DockDot, Escape, CLOSE_MS, FLIP_HOLD_MS, FOLDER_HOLD_MS, OPEN_MS, REORDER_HOLD_MS};
use crate::menu::MenuView;
use crate::tiles::{PageGrid, Tile};
use crate::zoom::ZoomBin;
use adw::prelude::*;
use gtk::{gdk, gio, glib};
use serde_json::{json, Map, Value};
use sonata_desktop::launchpad_model::{self as m, Folder, Item, Model};
use sonata_desktop::names;
use sonata_i18n::{fmt, gettext, tr};
use sonata_ui::menu::{Item as MItem, Options};
use std::cell::{Cell, RefCell};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::rc::Rc;
use std::time::Duration;

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Mode {
    FullScreen,
    /// the Apps Menu layout (menu.rs)
    Menu,
}

/// An open folder: its panel over the grid.
pub struct FolderView {
    pub wrap: gtk::Box,
    pub folder: Folder,
    /// where it was in the model (None: the Hidden folder)
    pub pos: Option<(usize, usize)>,
    pub panel: gtk::Box,
    pub title: gtk::EditableLabel,
    /// its apps' grid, kept: its tiles' gestures hold them weakly, and a
    /// grid let go left a right-click on an app in the folder with no menu
    pub grid: Rc<PageGrid>,
}

/// A drag of one of our tiles.
pub struct Drag {
    pub item: Item,
    pub tile: Rc<Tile>,
    /// the open folder it is being dragged out of
    pub folder: Option<Folder>,
    pub target: Option<Rc<Tile>>,
    pub pending: Option<(usize, usize)>,
    pub out: Option<(usize, usize)>,
    pub out_mark: Option<Rc<Tile>>,
}

pub type Then = Option<Box<dyn Fn()>>;

/// desktop id (without .desktop) -> the apps a launcher should list.
pub fn installed_apps() -> BTreeMap<String, gio::DesktopAppInfo> {
    // the folders themselves: new apps show up at once
    // (should_show as apps.py made it: "Manage Printing" -- cups -- isn't an
    // app here; the key checked too, for an entry GIO gave no id)
    sonata_ui::apps::scan().into_iter().filter(|(k, i)| sonata_ui::apps::should_show(i) && !sonata_ui::apps::NOT_APPS.contains(&k.as_str())).map(|(k, i)| (k.strip_suffix(".desktop").unwrap_or(&k).to_string(), i)).collect()
}

fn names_of(installed: &BTreeMap<String, gio::DesktopAppInfo>) -> BTreeMap<String, String> {
    installed.iter().map(|(k, v)| (k.clone(), sonata_ui::apps::display_name(v))).collect()
}

/// The drag icon, macOS style (ui/drag.py's HangingIcon): the item's icon
/// itself, a little translucent, held steady under the pointer -- GTK's
/// own drag icon is opaque. A Picture keeps a photo's shape as drag.py does.
pub const HANG_OPACITY: f64 = 0.78;

fn hang(drag: &gdk::Drag, icon: &gtk::Widget, size: i32) {
    let pic = gtk::Picture::builder().paintable(&gtk::WidgetPaintable::new(Some(icon))).content_fit(gtk::ContentFit::Contain).can_target(false).opacity(HANG_OPACITY).build();
    pic.set_size_request(size, size);
    if let Ok(di) = gtk::DragIcon::for_drag(drag).downcast::<gtk::DragIcon>() {
        di.set_child(Some(&pic));
    }
    drag.set_hotspot(size / 2, size / 2);
}

fn is_hidden_folder(f: &Folder) -> bool {
    f.kept.contains_key("locked")
}

/// Settings and the web app form (sonata_dock's spawn_sonata).
pub fn spawn_sonata(args: &[&str]) {
    sonata_dock::dock_menu::spawn_sonata(args);
}

pub fn open_settings(page: &str) {
    spawn_sonata(&["settings", "--page", page]);
}

pub struct Pad {
    pub win: gtk::ApplicationWindow,
    pub preview: bool,
    pub installed: RefCell<BTreeMap<String, gio::DesktopAppInfo>>,
    apps_sig: RefCell<Option<Vec<(std::path::PathBuf, i128)>>>,
    pub model: RefCell<Model>,
    pub jiggling: Cell<bool>,
    jiggle_sticky: Cell<bool>,
    icon_size: Cell<i32>,
    sized: Cell<bool>,
    screen: Cell<(i32, i32)>,
    widgets: RefCell<HashMap<String, Rc<Tile>>>,
    selected: Cell<i32>,
    pub drag: RefCell<Option<Drag>>,
    timers: RefCell<HashMap<&'static str, glib::SourceId>>,
    anim: RefCell<Option<adw::TimedAnimation>>,
    pub folder_view: RefCell<Option<FolderView>>,
    unlocked_hidden: Cell<bool>,
    unlocked: RefCell<HashSet<String>>,
    pub mode: Cell<Mode>,
    pub menu: RefCell<Option<Rc<MenuView>>>,
    pub search: gtk::SearchEntry,
    carousel: adw::Carousel,
    pages: RefCell<Vec<Rc<PageGrid>>>,
    results: RefCell<Rc<PageGrid>>,
    stack: gtk::Stack,
    dots: adw::CarouselIndicatorDots,
    pub col: gtk::Box,
    grid_area: gtk::Box,
    overlay: gtk::Overlay,
    pub bin: ZoomBin,
    /// a layer surface (not a preview / plain window)
    layer: Cell<bool>,
    /// on the OVERLAY layer (_pick_layer)
    on_overlay: Cell<bool>,
    dock_cfg: RefCell<Option<Map<String, Value>>>,
    dock_dot: RefCell<DockDot>,
    monitors: RefCell<Vec<gio::FileMonitor>>,
    meta_cache: RefCell<Option<(Vec<String>, BTreeMap<String, (String, String)>)>>,
    edge_x: Cell<Option<f64>>,
    follow: Cell<bool>,
}

impl Pad {
    pub fn new(app: &gtk::Application, preview: bool) -> Rc<Pad> {
        let installed = installed_apps();
        let data = Value::Object(sonata_config::load("launchpad", &m::defaults()));
        let model = Model::new(&data, names_of(&installed));
        let win = gtk::ApplicationWindow::builder().application(app).title(gettext(names::APPS)).css_classes(["sonata-launchpad"]).decorated(false).build();
        let search = gtk::SearchEntry::builder().placeholder_text(tr!("Search")).css_classes(["lp-search"]).halign(gtk::Align::Center).margin_top(40).build();
        let carousel = adw::Carousel::builder().hexpand(true).vexpand(true).allow_scroll_wheel(true).spacing(0).reveal_duration(300).build();
        let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(sonata_ui::tokens::ms(120.0)).build();
        let dots = adw::CarouselIndicatorDots::builder().carousel(&carousel).css_classes(["lp-dots"]).margin_bottom(32).margin_top(12).build();
        let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["lp-col"]).build();
        let grid_area = gtk::Box::builder().hexpand(true).vexpand(true).build();
        let overlay = gtk::Overlay::new();
        let bin = ZoomBin::new(&overlay);
        let me = Rc::new(Pad {
            win,
            preview,
            installed: RefCell::new(installed),
            apps_sig: RefCell::new(Some(logic::apps_signature())),
            model: RefCell::new(model),
            jiggling: Cell::new(false),
            jiggle_sticky: Cell::new(false),
            icon_size: Cell::new(96),
            sized: Cell::new(false),
            screen: Cell::new((0, 0)),
            widgets: RefCell::default(),
            selected: Cell::new(-1),
            drag: RefCell::default(),
            timers: RefCell::default(),
            anim: RefCell::default(),
            folder_view: RefCell::default(),
            unlocked_hidden: Cell::new(false),
            unlocked: RefCell::default(),
            mode: Cell::new(Mode::FullScreen),
            menu: RefCell::default(),
            search,
            carousel,
            pages: RefCell::default(),
            // (replaced right below, once there is an Rc to give it)
            results: RefCell::new(Rc::new(PageGrid { grid: gtk::Grid::new(), index: Cell::new(-1), cols: 1, rows: 1, tiles: RefCell::default(), found: RefCell::default() })),
            stack,
            dots,
            col,
            grid_area,
            overlay,
            bin,
            layer: Cell::new(false),
            on_overlay: Cell::new(true),
            dock_cfg: RefCell::default(),
            dock_dot: RefCell::default(),
            monitors: RefCell::default(),
            meta_cache: RefCell::default(),
            edge_x: Cell::new(None),
            follow: Cell::new(false),
        });
        me.save(); // new/removed apps reconciled
        me.results.replace(PageGrid::new(&me, -1, m::cols(), m::rows()));
        me.build();
        me.render();
        me
    }

    fn build(self: &Rc<Self>) {
        let w = Rc::downgrade(self);
        self.search.connect_search_changed(move |_| {
            if let Some(p) = w.upgrade() {
                p.search_changed();
            }
        });
        let w = Rc::downgrade(self);
        self.search.connect_activate(move |_| {
            if let Some(p) = w.upgrade() {
                p.activate_selected();
            }
        });
        let w = Rc::downgrade(self);
        self.carousel.connect_page_changed(move |_, _| {
            if let Some(p) = w.upgrade() {
                p.bin.invalidate();
                p.select(-1);
            }
        });
        self.stack.add_named(&self.carousel, Some("pages"));
        self.stack.add_named(&self.results.borrow().grid, Some("results"));
        self.col.append(&self.search);
        self.grid_area.append(&self.stack);
        self.col.append(&self.grid_area);
        self.col.append(&self.dots);
        self.overlay.set_child(Some(&self.col));
        // the "•••" menu, as in the Apps Menu (Vini): the other layout, its settings
        let more = gtk::Button::builder().child(&gtk::Label::builder().label("\u{2022}\u{2022}\u{2022}").css_classes(["lp-more-dots"]).build()).css_classes(["lp-more"]).can_focus(false).halign(gtk::Align::End).valign(gtk::Align::Start).margin_top(40).margin_end(48).tooltip_text(tr!("Options")).build();
        let w = Rc::downgrade(self);
        more.connect_clicked(move |b| {
            if let Some(p) = w.upgrade() {
                p.options_menu(b);
            }
        });
        self.overlay.add_overlay(&more);
        self.win.set_child(Some(&self.bin));
        let w = Rc::downgrade(self);
        self.bin.connect_size(move |bw, bh| {
            if let Some(p) = w.upgrade() {
                p.on_size(bw, bh);
            }
        });

        let click = gtk::GestureClick::new();
        let w = Rc::downgrade(self);
        click.connect_released(move |_, _, x, y| {
            if let Some(p) = w.upgrade() {
                p.background_click(x, y);
            }
        });
        self.bin.add_controller(click);
        let menu_click = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build(); // on the window: both layouts
        let w = Rc::downgrade(self);
        menu_click.connect_pressed(move |g, _, x, y| {
            if let Some(p) = w.upgrade() {
                p.background_menu(g, x, y);
            }
        });
        self.win.add_controller(menu_click);
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let w = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, key, _, _| match w.upgrade() {
            Some(p) if p.key(key) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        let w = Rc::downgrade(self);
        keys.connect_key_released(move |_, key, _, _| {
            if let Some(p) = w.upgrade() {
                if matches!(key, gdk::Key::Alt_L | gdk::Key::Alt_R) && p.jiggling.get() && !p.jiggle_sticky.get() {
                    p.set_jiggle(false, true);
                }
            }
        });
        self.win.add_controller(keys);

        if !self.preview {
            let ns = "sonata2-launchpad";
            self.layer.set(sonata_shell::layer::overlay_fullscreen(self.win.upcast_ref(), ns));
        }
        let w = Rc::downgrade(self);
        gio::AppInfoMonitor::get().connect_changed(move |_| {
            if let Some(p) = w.upgrade() {
                p.apps_changed();
            }
        });
        let mut mons = Vec::new();
        let w = Rc::downgrade(self);
        mons.extend(sonata_config::watch("launchpad", move || {
            if let Some(p) = w.upgrade() {
                p.config_changed();
            }
        }));
        for name in ["icons", "applock"] {
            // App Icons, padlocks: every tile drawn again
            let w = Rc::downgrade(self);
            mons.extend(sonata_config::watch(name, move || {
                if let Some(p) = w.upgrade() {
                    p.widgets.borrow_mut().clear();
                    p.render();
                }
            }));
        }
        // dock.json read once, again only when it changes (the grid math
        // needs the Dock's size on every allocation)
        let w = Rc::downgrade(self);
        mons.extend(sonata_config::watch("dock", move || {
            if let Some(p) = w.upgrade() {
                p.dock_cfg.replace(None);
            }
        }));
        self.monitors.replace(mons);
        // Wayfire raises the layer surface you press on: any press here
        // (swiping pages, holding an icon) would put Launchpad over the Dock
        // -- the Dock is put back on top right away, it always stays above
        // Launchpad (only needed when Launchpad sits on the overlay layer)
        let press = gtk::GestureClick::builder().button(0).propagation_phase(gtk::PropagationPhase::Capture).build();
        let w = Rc::downgrade(self);
        press.connect_pressed(move |_, _, _, _| {
            let Some(p) = w.upgrade() else { return };
            if p.on_overlay.get() {
                let w = Rc::downgrade(&p);
                glib::timeout_add_local_once(Duration::from_millis(20), move || {
                    if let Some(p) = w.upgrade().filter(|p| p.win.is_visible()) {
                        p.dock_above(true);
                    }
                });
            }
        });
        self.win.add_controller(press);
        // hidden any other way (style changed, the panel gone): the dot goes too
        let w = Rc::downgrade(self);
        self.win.connect_visible_notify(move |win| {
            if let Some(p) = w.upgrade().filter(|_| !win.is_visible()) {
                p.dock_dot(false);
            }
        });
    }

    pub fn info(&self, id: &str) -> Option<gio::DesktopAppInfo> {
        self.installed.borrow().get(id).cloned()
    }

    // -- geometry / rendering ------------------------------------------------------------

    /// The full screen's size: its display's. Vini: the first full-screen
    /// open after the Apps Menu glitched -- the window still had the menu's
    /// size for a frame or two, and the grid was worked out for it. w x h
    /// when the display isn't known (a plain window: its own size).
    fn screen_size(&self, w: i32, h: i32) -> (i32, i32) {
        if !self.layer.get() {
            return (w, h);
        }
        let mon = self.win.surface().and_then(|s| WidgetExt::display(&self.win).monitor_at_surface(&s));
        match mon.map(|m| m.geometry()) {
            Some(g) if g.width() > 0 && g.height() > 0 => (g.width(), g.height()),
            _ => (w, h),
        }
    }

    fn on_size(self: &Rc<Self>, w: i32, h: i32) {
        if self.mode.get() == Mode::Menu {
            return; // the Apps Menu lays out its own panel: the full screen's grid stays as it is
        }
        let (w, h) = self.screen_size(w, h);
        if (w, h) != self.screen.get() {
            self.screen.set((w, h));
            self.bin.invalidate();
        }
        let dcfg = self.dock_config();
        let edge = dcfg.get("position").and_then(Value::as_str).unwrap_or("bottom").to_string();
        let dock = sonata_dock::dock::reserved(&dcfg);
        let g = logic::grid_for(w, h, dock, &edge);
        self.grid_area.set_margin_start(g.side);
        self.grid_area.set_margin_end(g.side);
        // also when the saved pages were filled for another size: pages made
        // for 7x4 kept an empty fifth row (Vini)
        if m::set_grid(g.cols, g.rows as i64) || self.model.borrow().grid != Some((m::cols(), m::rows())) {
            let w = Rc::downgrade(self);
            glib::idle_add_local_once(move || {
                if let Some(p) = w.upgrade() {
                    p.rows_changed(); // not during allocation
                }
            });
        }
        let size = logic::icon_for(w, h, dock, &edge, m::cols(), m::rows());
        if (size - self.icon_size.get()).abs() >= 4 || !self.sized.get() {
            self.sized.set(true);
            self.icon_size.set(size);
            let w = Rc::downgrade(self);
            glib::idle_add_local_once(move || {
                if let Some(p) = w.upgrade() {
                    p.render();
                }
            });
        }
    }

    fn tile_size(&self) -> i32 {
        if self.mode.get() == Mode::Menu {
            logic::MENU_ICON
        } else {
            self.icon_size.get()
        }
    }

    pub fn item_widget(self: &Rc<Self>, item: &Item) -> Rc<Tile> {
        let key = match item {
            Item::Folder(f) if is_hidden_folder(f) => "\u{1}hidden".to_string(),
            _ => logic::key_of(item),
        };
        let size = self.tile_size();
        let old = self.widgets.borrow().get(&key).cloned();
        let tile = match old {
            Some(t) if t.size == size && t.drawn == Self::drawn_of(item) => {
                if let Item::Folder(f) = item {
                    if *t.name.borrow() != f.name {
                        t.set_name_text(&f.name); // renamed: the kept tile follows
                    }
                }
                t.item.replace(item.clone());
                t
            }
            _ => {
                let t = Tile::new(self, item, size);
                self.widgets.borrow_mut().insert(key, t.clone());
                t
            }
        };
        // the Apps Menu: every highlight the cell's width, whatever the name (Vini)
        tile.button.set_halign(if self.mode.get() == Mode::Menu { gtk::Align::Fill } else { gtk::Align::Center });
        tile
    }

    fn drawn_of(item: &Item) -> (Vec<String>, bool) {
        match item {
            Item::Folder(f) => (f.apps.clone(), is_hidden_folder(f) || logic::is_secure(f)),
            Item::App(_) => (Vec::new(), false),
        }
    }

    /// The page size changed (another display): pages rebuilt with the new
    /// grid, filled up in order.
    fn rows_changed(self: &Rc<Self>) {
        self.model.borrow_mut().repack();
        let old = self.pages.take();
        for p in old {
            self.carousel.remove(&p.grid);
        }
        let old = self.results.replace(PageGrid::new(self, -1, m::cols(), m::rows()));
        self.stack.remove(&old.grid);
        self.stack.add_named(&self.results.borrow().grid, Some("results"));
        self.render();
        self.save();
        if !self.search.text().is_empty() {
            self.search_changed(); // searching: results on the new grid
        }
    }

    /// Sync the carousel's pages with the model (tiles are reused); in the
    /// Apps Menu, its grids.
    pub fn render(self: &Rc<Self>) {
        let menu = self.menu_if_shown();
        self.bin.invalidate();
        let pages = self.pages_with_hidden();
        // icons slide to their new place
        let tiles: Vec<gtk::Widget> = self.widgets.borrow().values().map(|t| t.button.clone().upcast()).collect();
        let before = sonata_ui::transition::glide_record(&tiles, self.win.upcast_ref());
        let grids: Vec<Rc<PageGrid>> = match &menu {
            Some(mv) => mv.page_grids(self, &pages),
            None => {
                // (no borrow held while the carousel changes: it tells page-changed)
                let have = self.pages.borrow().len();
                for i in have..pages.len() {
                    let g = PageGrid::new(self, i as i32, m::cols(), m::rows());
                    self.pages.borrow_mut().push(g.clone());
                    self.carousel.append(&g.grid);
                }
                for _ in pages.len()..have {
                    let g = self.pages.borrow_mut().pop();
                    if let Some(g) = g {
                        self.carousel.remove(&g.grid);
                    }
                }
                self.pages.borrow().clone()
            }
        };
        for (i, page) in pages.iter().enumerate() {
            let grid = &grids[i];
            grid.index.set(i as i32);
            let tiles: Vec<Rc<Tile>> = page.iter().map(|it| self.item_widget(it)).collect();
            for (n, t) in tiles.iter().enumerate() {
                if n % 2 == 1 { t.button.add_css_class("odd") } else { t.button.remove_css_class("odd") }
            }
            grid.fill(tiles, None);
        }
        if let Some(mv) = &menu {
            mv.show(self);
        }
        self.sync_folder_view();
        self.prune_widgets(&pages);
        self.select(self.selected.get());
        sonata_ui::transition::glide_play(before, self.win.upcast_ref(), sonata_ui::transition::GLIDE_MS);
    }

    fn menu_if_shown(&self) -> Option<Rc<MenuView>> {
        if self.mode.get() == Mode::Menu {
            self.menu.borrow().clone()
        } else {
            None
        }
    }

    /// Tiles of what is no longer in the grid let go (memory review: a
    /// removed folder's tile stayed for the whole session). The apps inside
    /// folders stay: the Apps Menu's tabs show them through item_widget too.
    fn prune_widgets(&self, pages: &[Vec<Item>]) {
        let mut live: HashSet<String> = HashSet::new();
        live.insert("\u{1}hidden".into());
        for it in pages.iter().flatten() {
            live.insert(logic::key_of(it));
            if let Item::Folder(f) = it {
                live.extend(f.apps.iter().cloned());
            }
        }
        self.widgets.borrow_mut().retain(|k, _| live.contains(k));
    }

    /// No empty places between the apps: they move up into them.
    pub fn clean_up(self: &Rc<Self>) {
        if self.model.borrow_mut().close_gaps() {
            self.render();
            self.save();
        }
    }

    pub fn save(&self) {
        let mut data = self.model.borrow().to_json();
        // the Dock's copies of linked folders follow (links made there come back in data)
        sonata_desktop::folder_link::to_dock(&mut data);
        if let Some(o) = data.as_object() {
            if let Err(e) = sonata_config::save("launchpad", o) {
                println!("sonata2-launchpad: launchpad.json not saved: {e}");
            }
        }
    }

    // -- Hidden: apps hidden from the grid, in a folder that asks for the password ------------

    fn hidden_folder(&self) -> Option<Folder> {
        let installed = self.installed.borrow();
        let apps: Vec<String> = self.model.borrow().hidden.iter().filter(|a| installed.contains_key(*a)).cloned().collect();
        if apps.is_empty() {
            return None;
        }
        let mut f = Folder::new(&tr!("Hidden"), apps);
        f.kept.insert("locked".into(), json!(true));
        Some(f)
    }

    pub fn pages_with_hidden(&self) -> Vec<Vec<Item>> {
        logic::pages_with_hidden(&self.model.borrow().pages, self.hidden_folder(), m::per_page())
    }

    /// Into the Hidden folder: out of the grid, search and the Dock.
    pub fn hide_app(self: &Rc<Self>, app_id: &str) {
        self.model.borrow_mut().hide(app_id);
        self.save();
        self.render();
        let pins = self.dock_pins();
        if let Some(pins) = pins.filter(|p| p.iter().any(|x| x == app_id)) {
            self.save_dock_pins(pins.into_iter().filter(|p| p != app_id).collect());
        }
    }

    /// Back from Hidden to the end of the grid.
    pub fn unhide_app(self: &Rc<Self>, app_id: &str) {
        let had = {
            let mut model = self.model.borrow_mut();
            let had = model.hidden.iter().any(|h| h == app_id);
            model.hidden.retain(|h| h != app_id);
            if had {
                model.reconcile();
            }
            had
        };
        if had {
            self.save();
            self.close_folder();
        }
    }

    fn dock_pins(&self) -> Option<Vec<String>> {
        let d = sonata_config::load("dock", &json!({"pinned": null}));
        d.get("pinned").and_then(Value::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
    }

    /// Only the "pinned" key of dock.json (the Dock reloads it live).
    fn save_dock_pins(&self, pins: Vec<String>) {
        let mut data = sonata_config::stored("dock");
        data.insert("pinned".into(), json!(pins));
        let _ = sonata_config::save("dock", &data);
    }

    /// Where folder panels open, and what dims behind them: the full
    /// screen's grid, or the Apps Menu's panel.
    fn host(&self) -> (gtk::Overlay, gtk::Widget) {
        match self.menu_if_shown() {
            Some(mv) => (mv.overlay.clone(), mv.content.clone().upcast()),
            None => (self.overlay.clone(), self.col.clone().upcast()),
        }
    }

    pub fn needs_password(&self, f: &Folder) -> bool {
        if is_hidden_folder(f) {
            return !self.unlocked_hidden.get();
        }
        logic::is_secure(f) && !self.unlocked.borrow().contains(&logic::folder_key(f))
    }

    /// Where `f` is in the model now: at `pos` if a folder sharing its apps
    /// is still there, else the first folder sharing them (folders are
    /// values here, Python follows them by identity).
    fn locate_folder(&self, f: &Folder, pos: Option<(usize, usize)>) -> Option<(usize, usize)> {
        let model = self.model.borrow();
        let shares = |it: &Item| matches!(it, Item::Folder(g) if g.apps.iter().any(|a| f.apps.contains(a)) || (!f.link().is_empty() && g.link() == f.link()));
        if let Some((p, i)) = pos {
            if model.pages.get(p).and_then(|pg| pg.get(i)).is_some_and(shares) {
                return Some((p, i));
            }
        }
        model.pages.iter().enumerate().find_map(|(p, pg)| pg.iter().position(shares).map(|i| (p, i)))
    }

    fn folder_at(&self, pos: (usize, usize)) -> Option<Folder> {
        match self.model.borrow().pages.get(pos.0).and_then(|p| p.get(pos.1)) {
            Some(Item::Folder(f)) => Some(f.clone()),
            _ => None,
        }
    }

    /// Lock Folder (Vini, like the Dock's): opening it asks for the
    /// password; its icon shows blank tiles and a lock.
    pub fn set_folder_locked(self: &Rc<Self>, f: &Folder, on: bool) {
        let Some((p, i)) = self.locate_folder(f, None) else { return };
        if let Some(Item::Folder(g)) = self.model.borrow_mut().pages[p].get_mut(i) {
            if on {
                g.kept.insert("secure".into(), json!(true));
            } else {
                g.kept.remove("secure");
            }
        }
        self.unlocked.borrow_mut().remove(&logic::folder_key(f));
        self.save();
        self.render();
    }

    /// The folder's apps back on the page where it was (its menu's Ungroup);
    /// a locked folder only once the password is right.
    pub fn ungroup_folder(self: &Rc<Self>, f: &Folder) {
        if logic::is_secure(f) && !self.unlocked.borrow().contains(&logic::folder_key(f)) {
            let (w, f2) = (Rc::downgrade(self), f.clone());
            self.ask_password(f, Some(Box::new(move || {
                if let Some(p) = w.upgrade() {
                    p.do_ungroup(&f2);
                }
            })));
            return;
        }
        self.do_ungroup(f);
    }

    fn do_ungroup(self: &Rc<Self>, f: &Folder) {
        self.close_folder();
        if self.model.borrow_mut().ungroup(f) {
            self.unlocked.borrow_mut().remove(&logic::folder_key(f));
            self.save();
            self.render();
        }
    }

    /// The Hidden folder -- and a folder locked with Lock Folder -- opens
    /// only with the user's password (PAM, like the lock screen); it stays
    /// open until Launchpad closes. then: instead of opening it (Unlock
    /// Folder). Asked in the sheet every login-password prompt shares.
    pub fn ask_password(self: &Rc<Self>, f: &Folder, then: Then) {
        self.close_folder();
        let hidden = is_hidden_folder(f);
        let text = if hidden {
            tr!("Enter your password to see hidden apps")
        } else if then.is_none() {
            tr!("Enter your password to open this folder")
        } else {
            tr!("Enter your password to change this folder")
        };
        let ok = if then.is_none() { tr!("Open") } else { tr!("OK") };
        let title = fmt(&tr!("“{name}” is locked"), &[("name", &f.name)]);
        let (w, f2) = (Rc::downgrade(self), f.clone());
        let then = Rc::new(then);
        let app = self.win.application();
        sonata_shell::authsheet::ask(&title, &text, Some(&ok), app.as_ref(), Some(self.win.upcast_ref()), move |_pw| {
            let Some(p) = w.upgrade() else { return };
            if hidden {
                p.unlocked_hidden.set(true);
            } else {
                p.unlocked.borrow_mut().insert(logic::folder_key(&f2));
            }
            match then.as_ref() {
                Some(t) => t(),
                None => {
                    // the Hidden folder: made again (its apps), a folder: as it is now
                    let f = if hidden { p.hidden_folder() } else { p.locate_folder(&f2, None).and_then(|pos| p.folder_at(pos)) };
                    if let Some(f) = f {
                        p.open_folder(&f);
                    }
                }
            }
        }, || {});
    }

    /// launchpad.json changed elsewhere (Settings: reset, unhide).
    fn config_changed(self: &Rc<Self>) {
        let data = Value::Object(sonata_config::load("launchpad", &m::defaults()));
        if data == self.model.borrow().to_json() {
            return; // our own save
        }
        let installed = self.model.borrow().installed.clone();
        self.model.replace(Model::new(&data, installed));
        self.widgets.borrow_mut().clear();
        self.render();
    }

    fn apps_changed(self: &Rc<Self>) {
        self.apps_sig.replace(Some(logic::apps_signature()));
        // (apps.refresh: a window id that failed to match a moment ago may
        // be the app just installed -- else no icon nor name for a minute,
        // and the launch watch took it for an app that never opened)
        sonata_desktop::appmatch::refresh();
        let installed = installed_apps();
        {
            let mut model = self.model.borrow_mut();
            model.installed = names_of(&installed);
            model.reconcile();
        }
        self.installed.replace(installed);
        self.meta_cache.replace(None);
        self.save();
        self.render();
    }

    /// Apps installed or removed since the last look (cheap: folder times).
    fn check_apps(self: &Rc<Self>) {
        let sig = logic::apps_signature();
        if self.apps_sig.borrow().as_ref() != Some(&sig) {
            self.apps_changed();
        }
    }

    // -- open / close ----------------------------------------------------------------------

    pub fn toggle(self: &Rc<Self>) {
        let shown = self.win.is_visible()
            && match self.menu_if_shown() {
                Some(mv) => mv.is_open(self),
                None => self.bin.progress() > 0.5,
            };
        if shown {
            self.close_launchpad(None);
        } else {
            self.open_launchpad();
        }
    }

    /// The two layouts: full screen, Apps Menu (Settings > Appearance > Apps style).
    pub fn set_mode(self: &Rc<Self>, mode: Mode) {
        if mode == self.mode.get() {
            return;
        }
        use gtk4_layer_shell::LayerShell;
        self.close_folder();
        if mode == Mode::Menu {
            if self.menu.borrow().is_none() {
                let mv = MenuView::new(self);
                self.menu.replace(Some(mv));
            }
            let mv = self.menu.borrow().clone().unwrap();
            mv.take_search(self); // the same field, in the menu's title
            self.win.set_child(Some(&mv.root));
            self.win.add_css_class("lpw-mode");
            if self.layer.get() {
                self.win.set_layer(gtk4_layer_shell::Layer::Top);
                self.win.set_exclusive_zone(0); // inside the menu bar's and the Dock's space
            }
            self.on_overlay.set(false);
        } else {
            if let Some(mv) = self.menu.borrow().clone() {
                mv.give_search(self);
            }
            self.win.set_child(Some(&self.bin));
            self.win.remove_css_class("lpw-mode");
            if self.layer.get() {
                self.win.set_exclusive_zone(-1);
            }
        }
        self.mode.set(mode);
        self.set_jiggle(false, true);
        self.render();
    }

    fn open_menu(self: &Rc<Self>) {
        self.set_mode(Mode::Menu);
        self.check_apps();
        if let Some(mv) = self.menu.borrow().clone() {
            mv.open(self);
        }
        self.dock_dot(true);
    }

    /// Ask the Dock (its own process) to sit above Launchpad.
    fn dock_above(&self, on: bool) {
        self.tell_dock("above", on);
    }

    /// The Apps Menu open or closed: the Dock's dot under Launchpad's icon
    /// (Vini: like an open app's). Only on a change.
    pub fn dock_dot(&self, on: bool) {
        let tell = self.dock_dot.borrow_mut().set(on);
        if let Some(on) = tell {
            self.tell_dock("launcher-open", on);
        }
    }

    /// The session's Dock (sonata_dock::app_id). Never in a preview: no
    /// Dock is asked anything.
    fn tell_dock(&self, action: &str, on: bool) {
        if self.preview {
            return;
        }
        let Ok(bus) = gio::bus_get_sync(gio::BusType::Session, gio::Cancellable::NONE) else { return };
        let app_id = sonata_dock::app_id();
        let path = format!("/{}", app_id.replace('.', "/"));
        let args = (action, vec![on.to_variant()], HashMap::<String, glib::Variant>::new()).to_variant();
        bus.call(Some(app_id), &path, "org.freedesktop.Application", "ActivateAction", Some(&args), None, gio::DBusCallFlags::NONE, 1000, gio::Cancellable::NONE, |_| {});
    }

    /// Launchpad goes on the TOP layer, under the Dock: Wayfire raises
    /// whatever you click, and a click on Launchpad could otherwise cover
    /// the Dock. A focused full-screen app hides the TOP layer, so then
    /// Launchpad uses OVERLAY.
    fn pick_layer(&self) {
        use gtk4_layer_shell::LayerShell;
        if !self.layer.get() {
            return;
        }
        let views = sonata_backend::wfipc::call("window-rules/list-views", json!({}));
        let full = views.as_ref().and_then(Value::as_array).is_some_and(|v| v.iter().any(|v| v.get("fullscreen") == Some(&json!(true)) && v.get("activated") == Some(&json!(true))));
        self.on_overlay.set(full);
        self.win.set_layer(if full { gtk4_layer_shell::Layer::Overlay } else { gtk4_layer_shell::Layer::Top });
    }

    pub fn open_launchpad(self: &Rc<Self>) {
        if logic::style() == "window" {
            self.open_menu();
            return;
        }
        self.set_mode(Mode::FullScreen);
        self.pick_layer();
        self.check_apps();
        // the Dock moves up to OVERLAY once Launchpad is mapped: the surface
        // that changes layer last is on top, so the Dock stays reachable
        // (drag an app onto it to pin it)
        let w = Rc::downgrade(self);
        glib::timeout_add_local_once(Duration::from_millis(80), move || {
            if let Some(p) = w.upgrade().filter(|p| p.win.is_visible()) {
                p.dock_above(true);
            }
        });
        self.clear_dock();
        self.search.set_text("");
        self.set_jiggle(false, true);
        self.close_folder();
        self.bin.set_progress(0.0);
        self.win.present();
        self.search.grab_focus();
        // start once the surface is on screen: mapping a full-screen surface
        // takes a few frames, and an animation started before it would
        // already be half over when it first shows (the "lag")
        let w = Rc::downgrade(self);
        self.bin.add_tick_callback(move |_, _| {
            if let Some(p) = w.upgrade() {
                p.animate(1.0, OPEN_MS, None);
            }
            glib::ControlFlow::Break
        });
    }

    fn dock_config(&self) -> Map<String, Value> {
        self.dock_cfg.borrow_mut().get_or_insert_with(|| sonata_config::load("dock", &sonata_dock::dock::defaults())).clone()
    }

    /// The Dock stays over Launchpad (macOS): the grid and the page dots
    /// keep out of its way, at whichever edge it is.
    fn clear_dock(&self) {
        let cfg = self.dock_config();
        let room = sonata_dock::dock::reserved(&cfg) as i32 + 8;
        let edge = cfg.get("position").and_then(Value::as_str).unwrap_or("bottom");
        self.col.set_margin_bottom(if edge == "bottom" { room } else { 0 });
        self.col.set_margin_start(if edge == "left" { room } else { 0 });
        self.col.set_margin_end(if edge == "right" { room } else { 0 });
    }

    /// Mission Control (Super+Tab) or all Spaces open over it: it closes (Vini).
    pub fn plugin_changed(self: &Rc<Self>, ev: &Value) {
        let plugin = ev.get("plugin").and_then(Value::as_str).unwrap_or("");
        let on = ev.get("state").is_some_and(|s| s.as_bool().unwrap_or(false));
        let open = match self.menu_if_shown() {
            Some(mv) => mv.is_open(self),
            None => true,
        };
        if (plugin == "scale" || plugin == "expo") && on && self.win.is_visible() && open {
            self.close_launchpad(None);
        }
    }

    pub fn close_launchpad(self: &Rc<Self>, then: Then) {
        self.unlocked_hidden.set(false); // Hidden asks for the password again next time
        self.unlocked.borrow_mut().clear(); // (and the locked folders)
        if let Some(mv) = self.menu_if_shown() {
            self.close_folder();
            self.set_jiggle(false, true);
            self.dock_dot(false); // (off as it starts going)
            mv.close(self, then);
            return;
        }
        self.dock_above(false);
        let w = Rc::downgrade(self);
        self.animate(0.0, CLOSE_MS, Some(Box::new(move || {
            if let Some(p) = w.upgrade() {
                p.win.set_visible(false);
            }
            if let Some(t) = &then {
                t();
            }
        })));
    }

    fn animate(self: &Rc<Self>, to: f64, ms: u32, done: Then) {
        if let Some(a) = self.anim.take() {
            a.pause(); // a paused animation never emits "done"
        }
        self.bin.freeze();
        let w = self.bin.downgrade();
        let target = adw::CallbackAnimationTarget::new(move |v| {
            if let Some(b) = w.upgrade() {
                b.set_progress(v);
            }
        });
        let anim = adw::TimedAnimation::new(&self.bin, self.bin.progress(), to, sonata_ui::tokens::ms(ms as f64), target);
        // macOS: a quick start that settles softly, both ways
        anim.set_easing(if to > 0.0 { adw::Easing::EaseOutQuart } else { adw::Easing::EaseOutCubic });
        let bin = self.bin.downgrade();
        anim.connect_done(move |_| {
            if let Some(b) = bin.upgrade() {
                b.thaw();
            }
            if let Some(d) = &done {
                d();
            }
        });
        anim.play();
        self.anim.replace(Some(anim));
    }

    /// Nothing of Launchpad's under (x, y) in root: no icon, search, dots,
    /// folder panel or button.
    pub fn on_background(root: &gtk::Widget, x: f64, y: f64) -> bool {
        let mut w = root.pick(x, y, gtk::PickFlags::DEFAULT);
        while let Some(c) = w {
            if c == *root {
                break;
            }
            if c.is::<gtk::SearchEntry>() || c.is::<adw::CarouselIndicatorDots>() || c.is::<gtk::EditableLabel>() || c.is::<gtk::Button>() || ["lp-item", "lp-panel", "lp-more"].iter().any(|k| c.has_css_class(k)) {
                return false;
            }
            w = c.parent();
        }
        true
    }

    /// Right-click on the background (both layouts): New Web App…
    fn background_menu(self: &Rc<Self>, g: &gtk::GestureClick, x: f64, y: f64) {
        let Some(root) = self.win.child() else { return };
        if self.folder_view.borrow().is_some() || self.jiggling.get() || !Self::on_background(&root, x, y) {
            return;
        }
        if let Some(mv) = self.menu_if_shown() {
            if !MenuView::inside(mv.panel.upcast_ref(), &root, x, y) {
                return;
            }
        }
        g.set_state(gtk::EventSequenceState::Claimed);
        let w = Rc::downgrade(self);
        let item = MItem::new(&tr!("New Web App…"), move || {
            if let Some(p) = w.upgrade() {
                p.close_launchpad(Some(Box::new(|| spawn_sonata(&["webapp", "new"]))));
            }
        });
        sonata_ui::menu::popup(&root, vec![vec![item]], Options::at(x, y));
    }

    fn background_click(self: &Rc<Self>, x: f64, y: f64) {
        if !Self::on_background(self.bin.upcast_ref(), x, y) {
            return;
        }
        if self.folder_view.borrow().is_some() {
            self.close_folder();
        } else if self.jiggling.get() {
            self.set_jiggle(false, true);
        } else {
            self.close_launchpad(None);
        }
    }

    // -- launching / folders ---------------------------------------------------------------

    pub fn activate_item(self: &Rc<Self>, tile: &Rc<Tile>) {
        if let Some(f) = tile.folder() {
            if self.needs_password(&f) {
                self.ask_password(&f, None);
            } else {
                self.open_folder(&f);
            }
            return;
        }
        if self.jiggling.get() {
            return;
        }
        let Some(info) = tile.app().and_then(|a| self.info(&a)) else { return };
        let ctx: gio::AppLaunchContext = WidgetExt::display(&self.win).app_launch_context().upcast();
        // opened again if no window comes (launchwatch: Vini, Steam)
        self.close_launchpad(Some(Box::new(move || {
            let (i, c) = (info.clone(), ctx.clone());
            sonata_desktop::launchwatch::open(info.upcast_ref(), Rc::new(move || sonata_dock::dock::launch_info(&i, &[], Some(c.clone()))), 0, None);
        })));
    }

    pub fn open_folder(self: &Rc<Self>, f: &Folder) {
        self.close_folder();
        self.bin.invalidate();
        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["lp-panel"]).build();
        let title = centred_editable_label(f.name.as_str());
        title.add_css_class("lp-panel-title");
        title.set_editable(!is_hidden_folder(f));
        let w = Rc::downgrade(self);
        title.connect_editing_notify(move |t| {
            if let Some(p) = w.upgrade().filter(|_| !t.is_editing()) {
                p.renamed(t);
            }
        });
        // live (Vini): the folder's name in the grid follows each key typed
        let w = Rc::downgrade(self);
        title.connect_changed(move |t| {
            let Some(p) = w.upgrade() else { return };
            if !t.is_editing() {
                return;
            }
            let Some(folder) = p.folder_view.borrow().as_ref().map(|fv| fv.folder.clone()) else { return };
            let name = t.text().trim().to_string();
            let name = if name.is_empty() { folder.name.clone() } else { name };
            let tile = p.widgets.borrow().get(&logic::folder_key(&folder)).cloned();
            if let Some(tile) = tile {
                tile.set_name_text(&name);
            }
        });
        let grid = self.folder_grid(f);
        panel.append(&grid.grid);
        // Big Sur: the folder name sits above the panel, editable in place
        let wrap = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(18).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
        wrap.append(&title);
        wrap.append(&panel);
        let (host, dim) = self.host();
        host.add_overlay(&wrap);
        let pos = if is_hidden_folder(f) { None } else { self.locate_folder(f, None) };
        self.folder_view.replace(Some(FolderView { wrap: wrap.clone(), folder: f.clone(), pos, panel, title, grid }));
        wrap.add_css_class("lp-folder-view");
        dim.add_css_class("dimmed");
        dim.set_can_target(false); // a click behind the folder closes it (Vini)
    }

    fn renamed(self: &Rc<Self>, title: &gtk::EditableLabel) {
        let Some((folder, pos)) = self.folder_view.borrow().as_ref().map(|fv| (fv.folder.clone(), fv.pos)) else { return };
        let name = title.text().trim().to_string();
        if name.is_empty() {
            // empty: the old name back
            title.set_text(&folder.name);
            if let Some(t) = self.widgets.borrow().get(&logic::folder_key(&folder)) {
                t.set_name_text(&folder.name);
            }
        } else if name != folder.name {
            if let Some((p, i)) = self.locate_folder(&folder, pos) {
                if let Some(Item::Folder(g)) = self.model.borrow_mut().pages[p].get_mut(i) {
                    g.name = name.clone();
                }
            }
            if let Some(fv) = self.folder_view.borrow_mut().as_mut() {
                fv.folder.name = name;
            }
            self.save();
            self.render();
        }
    }

    /// The open folder's apps: its panel's grid (made again when they change).
    fn folder_grid(self: &Rc<Self>, f: &Folder) -> Rc<PageGrid> {
        let (cols, rows) = logic::folder_dims(f.apps.len(), m::cols());
        let grid = PageGrid::new(self, -2, cols, rows);
        let size = self.tile_size();
        grid.grid.set_size_request(cols as i32 * (size as f64 * 1.7) as i32, rows as i32 * (size as f64 * 1.7) as i32);
        grid.fill(f.apps.iter().take(cols * rows).map(|a| Tile::new(self, &Item::App(a.clone()), size)).collect(), None);
        grid
    }

    /// The open folder follows its apps (Vini: Hide or Lock on an app in it
    /// left the folder as it was): its grid made again, or the folder closed
    /// when it no longer is. Not during a drag: its tile is the drag's source.
    fn sync_folder_view(self: &Rc<Self>) {
        if self.drag.borrow().is_some() {
            return;
        }
        let Some((folder, pos)) = self.folder_view.borrow().as_ref().map(|fv| (fv.folder.clone(), fv.pos)) else { return };
        let now = if is_hidden_folder(&folder) {
            self.hidden_folder()
        } else {
            self.locate_folder(&folder, pos).and_then(|p| self.folder_at(p).map(|f| (f, p))).map(|(f, p)| {
                if let Some(fv) = self.folder_view.borrow_mut().as_mut() {
                    fv.pos = Some(p);
                }
                f
            })
        };
        let Some(now) = now.filter(|f| !f.apps.is_empty()) else {
            self.close_folder();
            return;
        };
        let grid = self.folder_grid(&now);
        if let Some(fv) = self.folder_view.borrow_mut().as_mut() {
            if let Some(old) = fv.panel.first_child() {
                fv.panel.remove(&old);
            }
            fv.panel.append(&grid.grid);
            fv.grid = grid;
            fv.folder = now;
        }
    }

    /// Zooms / fades out (the grid comes back at once for drags and clicks).
    pub fn close_folder(self: &Rc<Self>) {
        let Some(fv) = self.folder_view.take() else { return };
        let (_, dim) = self.host();
        dim.remove_css_class("dimmed");
        dim.set_can_target(true);
        let wrap = fv.wrap;
        wrap.set_can_target(false);
        wrap.add_css_class("closing");
        glib::timeout_add_local_once(Duration::from_millis(sonata_ui::tokens::ms(logic::FOLD_OUT_MS) as u64 + 20), move || {
            if let Some(o) = wrap.parent().and_then(|p| p.downcast::<gtk::Overlay>().ok()) {
                o.remove_overlay(&wrap);
            }
        });
        self.render();
    }

    // -- search / selection ----------------------------------------------------------------

    pub fn search_changed(self: &Rc<Self>) {
        self.bin.invalidate();
        let q = self.search.text().to_string();
        self.dots.set_opacity(if q.is_empty() { 1.0 } else { 0.0 });
        let menu = self.menu_if_shown();
        if q.is_empty() {
            self.stack.set_visible_child_name("pages");
            if let Some(mv) = &menu {
                mv.show(self);
            }
            self.select(-1);
            return;
        }
        let found = m::search(&self.search_meta(), &q, None);
        let results = match &menu {
            Some(mv) => mv.results.clone(),
            None => self.results.borrow().clone(),
        };
        let size = self.tile_size();
        let empty = gtk::Label::builder().label(tr!("No Results")).css_classes(["lp-empty"]).build();
        results.fill(found.iter().map(|a| Tile::new(self, &Item::App(a.clone()), size)).collect(), Some(empty.upcast()));
        let any = !found.is_empty();
        results.found.replace(found);
        self.stack.set_visible_child_name("results");
        if let Some(mv) = &menu {
            mv.show(self);
        }
        self.select(if any { 0 } else { -1 });
    }

    /// id -> (name, extra text) of the apps shown, built once while the
    /// apps stay the same (it was rebuilt on every key typed).
    fn search_meta(&self) -> BTreeMap<String, (String, String)> {
        let shown = self.model.borrow().all_apps();
        if let Some((s, meta)) = self.meta_cache.borrow().as_ref() {
            if *s == shown {
                return meta.clone();
            }
        }
        let installed = self.installed.borrow();
        let meta: BTreeMap<String, (String, String)> = shown
            .iter()
            .filter_map(|k| {
                let v = installed.get(k)?;
                let keywords = v.keywords().iter().map(|k| k.to_string()).collect::<Vec<_>>().join(" ");
                let exec = sonata_ui::apps::executable(v).display().to_string();
                let extra = [v.generic_name().map(|s| s.to_string()).unwrap_or_default(), keywords, exec].into_iter().filter(|s| !s.is_empty()).collect::<Vec<_>>().join(" ");
                Some((k.clone(), (sonata_ui::apps::display_name(v), extra)))
            })
            .collect();
        self.meta_cache.replace(Some((shown, meta.clone())));
        meta
    }

    fn current_page(&self) -> usize {
        self.carousel.position().round().max(0.0) as usize
    }

    pub fn visible_items(&self) -> Vec<Rc<Tile>> {
        if let Some(mv) = self.menu_if_shown() {
            return mv.visible_items(self);
        }
        if self.stack.visible_child_name().as_deref() == Some("results") {
            return self.results.borrow().tiles.borrow().clone();
        }
        self.pages.borrow().get(self.current_page()).map(|g| g.tiles.borrow().clone()).unwrap_or_default()
    }

    pub fn select(&self, index: i32) {
        self.bin.invalidate();
        for t in self.widgets.borrow().values() {
            t.button.remove_css_class("selected");
        }
        let items = self.visible_items();
        for t in &items {
            t.button.remove_css_class("selected");
        }
        let ok = index >= 0 && (index as usize) < items.len();
        self.selected.set(if ok { index } else { -1 });
        if ok {
            let t = &items[index as usize];
            t.button.add_css_class("selected");
            if let Some(mv) = self.menu_if_shown() {
                mv.reveal(&t.button);
            }
        }
    }

    fn activate_selected(self: &Rc<Self>) {
        let items = self.visible_items();
        if let Some(t) = usize::try_from(self.selected.get()).ok().and_then(|i| items.get(i)) {
            self.activate_item(t);
        }
    }

    /// The folder's name being edited, or None.
    fn editing_title(&self) -> Option<gtk::EditableLabel> {
        let mut w = gtk::prelude::GtkWindowExt::focus(&self.win);
        while let Some(c) = w {
            if let Some(e) = c.downcast_ref::<gtk::EditableLabel>() {
                return e.is_editing().then(|| e.clone());
            }
            w = c.parent();
        }
        None
    }

    fn key(self: &Rc<Self>, key: gdk::Key) -> bool {
        if let Some(e) = self.editing_title() {
            // renaming a folder: the keys move in its text (Vini)
            if key == gdk::Key::Escape {
                e.stop_editing(false); // the old name back; the folder stays open
                return true;
            }
            return false;
        }
        if key == gdk::Key::Escape {
            let tab = self.menu_if_shown().is_some_and(|mv| mv.tab.borrow().is_some());
            let folder_open = self.folder_view.borrow().is_some();
            match logic::escape(!self.search.text().is_empty(), folder_open, self.jiggling.get(), tab) {
                Escape::ClearSearch => self.search.set_text(""),
                Escape::CloseFolder => self.close_folder(),
                Escape::StopJiggle => self.set_jiggle(false, true),
                Escape::AllTabs => {
                    if let Some(mv) = self.menu_if_shown() {
                        mv.set_tab(self, None); // the Apps Menu: its tab first
                    }
                }
                Escape::Close => self.close_launchpad(None),
            }
            return true;
        }
        if matches!(key, gdk::Key::Alt_L | gdk::Key::Alt_R) && !self.jiggling.get() {
            self.set_jiggle(true, false); // while Alt is held (macOS: Option)
            return false;
        }
        let cols = m::cols() as i64;
        let delta = match key {
            gdk::Key::Left => -1,
            gdk::Key::Right => 1,
            gdk::Key::Up => -cols,
            gdk::Key::Down => cols,
            gdk::Key::Page_Down | gdk::Key::Page_Up if self.mode.get() != Mode::Menu => {
                let page = self.current_page() as i64 + if key == gdk::Key::Page_Down { 1 } else { -1 };
                self.scroll_to_page(page);
                return true;
            }
            _ => return false,
        };
        let items = self.visible_items();
        let pages = self.mode.get() != Mode::Menu && self.stack.visible_child_name().as_deref() == Some("pages");
        let sel = usize::try_from(self.selected.get()).ok();
        match logic::arrow(sel, items.len(), delta, delta.abs() == 1, pages) {
            Arrow::Select(i) => self.select(i as i32),
            Arrow::Page(step) => {
                let np = self.current_page() as i64 + step as i64;
                if self.scroll_to_page(np) {
                    let w = Rc::downgrade(self);
                    glib::timeout_add_local_once(Duration::from_millis(320), move || {
                        if let Some(p) = w.upgrade() {
                            let last = p.visible_items().len() as i32 - 1;
                            p.select(if step > 0 { 0 } else { last });
                        }
                    });
                }
            }
            Arrow::Nothing => {}
        }
        true
    }

    fn scroll_to_page(&self, page: i64) -> bool {
        let pages = self.pages.borrow();
        match usize::try_from(page).ok().and_then(|p| pages.get(p)) {
            Some(g) => {
                self.carousel.scroll_to(&g.grid, true);
                true
            }
            None => false,
        }
    }

    // -- jiggle / menus --------------------------------------------------------------------

    pub fn set_jiggle(&self, on: bool, sticky: bool) {
        if on != self.jiggling.get() {
            self.bin.invalidate();
        }
        self.jiggling.set(on);
        self.jiggle_sticky.set(on && sticky);
        let roots: Vec<gtk::Widget> = std::iter::once(self.bin.clone().upcast()).chain(self.menu.borrow().as_ref().map(|mv| mv.root.clone().upcast())).collect();
        for r in roots {
            if on { r.add_css_class("jiggle") } else { r.remove_css_class("jiggle") }
        }
        for t in self.widgets.borrow().values() {
            if let Some(b) = &t.badge {
                b.set_visible(on);
            }
        }
    }

    /// Runs `f` once Launchpad has closed (its alerts are windows of their own).
    fn around(self: &Rc<Self>) -> sonata_dock::dock_menu::Around {
        let w = Rc::downgrade(self);
        Rc::new(move |f: Box<dyn Fn()>| {
            if let Some(p) = w.upgrade() {
                p.close_launchpad(Some(f));
            }
        })
    }

    /// Right-click menu (Sonata addition; Launchpad has none on macOS).
    pub fn item_menu(self: &Rc<Self>, tile: &Rc<Tile>, x: f64, y: f64) {
        let in_hidden = self.folder_view.borrow().as_ref().is_some_and(|fv| is_hidden_folder(&fv.folder));
        let open = {
            let (w, t) = (Rc::downgrade(self), Rc::downgrade(tile));
            move || {
                if let (Some(p), Some(t)) = (w.upgrade(), t.upgrade()) {
                    p.activate_item(&t);
                }
            }
        };
        let mut sections = vec![vec![MItem::new(&tr!("Open"), open)]];
        let w = Rc::downgrade(self);
        if let Some(f) = tile.folder() {
            let f_menu = f.clone(); // (Ungroup's: the lock item takes `f`)
            if !is_hidden_folder(&f) {
                // (not Hidden: it's always behind the password)
                let item = if logic::is_secure(&f) {
                    MItem::new(&tr!("Unlock Folder…"), move || {
                        let Some(p) = w.upgrade() else { return };
                        let (w2, f2) = (Rc::downgrade(&p), f.clone());
                        p.ask_password(&f, Some(Box::new(move || {
                            if let Some(p) = w2.upgrade() {
                                p.set_folder_locked(&f2, false);
                            }
                        })));
                    })
                } else {
                    MItem::new(&tr!("Lock Folder"), move || {
                        if let Some(p) = w.upgrade() {
                            p.set_folder_locked(&f, true);
                        }
                    })
                };
                sections.push(vec![item]);
                // Ungroup, as the Dock's folders have (Vini); a locked one asks
                // for the password first (its apps would show)
                let label = if logic::is_secure(&f_menu) { tr!("Ungroup…") } else { tr!("Ungroup") };
                let (w, f2) = (Rc::downgrade(self), f_menu);
                sections.push(vec![MItem::new(&label, move || {
                    if let Some(p) = w.upgrade() {
                        p.ungroup_folder(&f2);
                    }
                })]);
            }
        } else if let Some(app) = tile.app() {
            if in_hidden {
                let label = fmt(&tr!("Show in {apps}"), &[("apps", &gettext(names::APPS))]);
                sections.push(vec![MItem::new(&label, move || {
                    if let Some(p) = w.upgrade() {
                        p.unhide_app(&app);
                    }
                })]);
            } else {
                self.app_sections(&app, &mut sections);
            }
        }
        sonata_ui::menu::popup(&tile.button, sections, Options::at(x, y));
    }

    fn app_sections(self: &Rc<Self>, app: &str, sections: &mut Vec<Vec<MItem>>) {
        let info = self.info(app);
        // (no Open in Sandbox here: the Dock's menu only -- Vini)
        // Uninstall… right under Keep in Dock / Remove from Dock (Vini), in its group
        let mut dock_sec = Vec::new();
        if info.as_ref().is_some_and(|i| logic::removable(i.id().as_deref().unwrap_or(""))) {
            let (w, a) = (Rc::downgrade(self), app.to_string());
            dock_sec.push(MItem::new(&tr!("Uninstall…"), move || {
                if let Some(p) = w.upgrade() {
                    p.ask_delete(&a);
                }
            }));
        }
        if let Some(pins) = self.dock_pins().filter(|_| !sonata_dock::PERMANENT.contains(&app)) {
            // (Launchpad always stays)
            let kept = pins.iter().any(|p| p == app);
            let (w, a) = (Rc::downgrade(self), app.to_string());
            let label = if kept { tr!("Remove from Dock") } else { tr!("Keep in Dock") };
            dock_sec.insert(0, MItem::new(&label, move || {
                let Some(p) = w.upgrade() else { return };
                let Some(mut pins) = p.dock_pins() else { return };
                if !kept && !pins.contains(&a) {
                    pins.push(a.clone());
                } else if kept {
                    pins.retain(|x| *x != a);
                }
                p.save_dock_pins(pins);
            }));
        }
        if !dock_sec.is_empty() {
            sections.push(dock_sec);
        }
        if let Some(i) = &info {
            sections.extend(sonata_dock::dock_menu::gpu_sections(i));
        }
        if let Some(path) = info.as_ref().and_then(sonata_ui::apps::app_filename) {
            let w = Rc::downgrade(self);
            let p2 = path.clone();
            sections.push(vec![
                MItem::new(&tr!("Show in Files"), move || {
                    if let Some(p) = w.upgrade() {
                        let path = p2.clone();
                        p.close_launchpad(Some(Box::new(move || sonata_dock::show_in_files(Some(&path)))));
                    }
                }),
                MItem::new(&tr!("Add to Desktop"), move || {
                    sonata_dock::add_to_desktop(Some(&path));
                }),
            ]);
        }
        if let Some(i) = &info {
            sections.push(vec![sonata_dock::lock_item(i, Some(self.around()))]);
        }
        let w = Rc::downgrade(self);
        let a = app.to_string();
        let mut hide = vec![MItem::new(&tr!("Hide"), move || {
            if let Some(p) = w.upgrade() {
                p.hide_app(&a); // into the Hidden folder
            }
        })];
        if sonata_desktop::badges::is_webapp(app) {
            let wid = sonata_webapps::id_of(app);
            let w = Rc::downgrade(self);
            sections.push(vec![MItem::new(&tr!("Edit Web App…"), move || {
                if let Some(p) = w.upgrade() {
                    let wid = wid.clone();
                    p.close_launchpad(Some(Box::new(move || sonata_dock::dock_menu::edit_webapp(&wid))));
                }
            })]);
            // (no Change Icon… here: Vini didn't want it in the menu)
            let (w, a) = (Rc::downgrade(self), app.to_string());
            hide.push(MItem::new(&tr!("Delete Web App…"), move || {
                if let Some(p) = w.upgrade() {
                    p.ask_delete_webapp(&a);
                }
            }));
        }
        sections.push(hide);
    }

    /// The "•••" menu, in both layouts: the other layout, and its settings.
    pub fn options_menu(self: &Rc<Self>, btn: &gtk::Button) {
        let w = Rc::downgrade(self);
        let set_style = move |s: &'static str| {
            let w = w.clone();
            move || {
                let _ = sonata_config::update(logic::LAUNCHER, &json!({"style": s}));
                if let Some(p) = w.upgrade() {
                    p.close_launchpad(None);
                }
            }
        };
        let other = if self.mode.get() == Mode::Menu {
            MItem::new(&fmt(&tr!("Use Full-Screen {apps}"), &[("apps", &gettext(names::APPS))]), set_style("fullscreen"))
        } else {
            MItem::new(&fmt(&tr!("Use {apps_menu}"), &[("apps_menu", &gettext(names::APPS_MENU))]), set_style("window"))
        };
        // Clean Up (Vini): the empty places apps left behind, filled in order
        let w = Rc::downgrade(self);
        let tidy = MItem::new(&tr!("Clean Up"), move || {
            if let Some(p) = w.upgrade() {
                p.clean_up();
            }
        })
        .enabled(self.model.borrow().has_gaps());
        let w = Rc::downgrade(self);
        let settings = MItem::new(&fmt(&tr!("{apps} Settings…"), &[("apps", &gettext(names::APPS))]), move || {
            if let Some(p) = w.upgrade() {
                p.close_launchpad(Some(Box::new(|| open_settings(logic::SETTINGS_PAGE))));
            }
        });
        sonata_ui::menu::popup(btn, vec![vec![other], vec![tidy], vec![settings]], Options::below());
    }

    /// Asked like Uninstall (ask_delete): Launchpad closes and the question
    /// is a glass alert of its own (Vini: it came inside Launchpad, without
    /// the glass).
    pub fn ask_delete_webapp(self: &Rc<Self>, app: &str) {
        let wid = sonata_webapps::id_of(app);
        self.close_launchpad(Some(Box::new(move || sonata_dock::uninstall_ui::ask_webapp(&wid, None, None))));
    }

    /// Uninstall (asked first): the package, the Flatpak or the shortcut.
    /// Launchpad closes so the question shows.
    pub fn ask_delete(self: &Rc<Self>, app_id: &str) {
        let Some(info) = self.info(app_id) else { return };
        if !logic::removable(info.id().as_deref().unwrap_or("")) {
            return; // Sonata's own apps stay
        }
        self.close_launchpad(Some(Box::new(move || sonata_dock::uninstall_ui::ask(&info, None, None))));
    }

    // -- drag and drop ---------------------------------------------------------------------

    /// Held near a side of the screen while dragging, the pages turn (macOS).
    fn follow_drags(self: &Rc<Self>) {
        if self.follow.replace(true) {
            return;
        }
        let edge = gtk::DropControllerMotion::new();
        let w = Rc::downgrade(self);
        edge.connect_motion(move |_, x, _| {
            if let Some(p) = w.upgrade() {
                p.edge_x.set(Some(x));
                p.edge_flip(x);
            }
        });
        let w = Rc::downgrade(self);
        edge.connect_leave(move |_| {
            if let Some(p) = w.upgrade() {
                p.edge_x.set(None);
                p.cancel("flip");
            }
        });
        self.win.add_controller(edge);
    }

    fn edge_flip(self: &Rc<Self>, x: f64) {
        if self.drag.borrow().is_none() || self.mode.get() == Mode::Menu {
            return; // (the Apps Menu has no pages)
        }
        let w = self.win.width().max(1) as f64;
        let side = match self.grid_area.margin_start() {
            0 => (w * 0.12) as i32,
            s => s,
        } as f64;
        if x < side || x > w - side {
            // beside the grid: the next / previous page
            let step = if x < side { -1 } else { 1 };
            let page = self.current_page() as i64;
            let me = Rc::downgrade(self);
            self.timer("flip", FLIP_HOLD_MS, move || {
                let Some(p) = me.upgrade() else { return };
                p.flip(page + step);
                // still held there after the page turned: keep turning
                let w2 = Rc::downgrade(&p);
                glib::timeout_add_local_once(Duration::from_millis(450), move || {
                    if let Some(p) = w2.upgrade() {
                        if let Some(x) = p.edge_x.get().filter(|_| p.drag.borrow().is_some()) {
                            p.edge_flip(x);
                        }
                    }
                });
            });
        } else {
            self.cancel("flip");
        }
    }

    pub fn attach_drag(self: &Rc<Self>, tile: &Rc<Tile>) {
        self.follow_drags();
        let src = gtk::DragSource::builder().actions(gdk::DragAction::MOVE | gdk::DragAction::COPY).build();
        let (w, t) = (Rc::downgrade(self), Rc::downgrade(tile));
        src.connect_prepare(move |_, _, _| {
            let (p, t) = (w.upgrade()?, t.upgrade()?);
            p.drag_content(&t)
        });
        let (w, t) = (Rc::downgrade(self), Rc::downgrade(tile));
        src.connect_drag_begin(move |_, drag| {
            if let (Some(p), Some(t)) = (w.upgrade(), t.upgrade()) {
                hang(drag, &t.icon, t.size);
                p.drag_begun(&t);
            }
        });
        let (w, t) = (Rc::downgrade(self), Rc::downgrade(tile));
        src.connect_drag_end(move |_, _, _| {
            if let (Some(p), Some(t)) = (w.upgrade(), t.upgrade()) {
                p.drag_ended(&t);
            }
        });
        tile.button.add_controller(src);
    }

    fn drag_content(self: &Rc<Self>, t: &Rc<Tile>) -> Option<gdk::ContentProvider> {
        let mut providers = vec![gdk::ContentProvider::for_value(&"sonata2-launchpad-item".to_value())];
        let uri_list = |uri: String| gdk::ContentProvider::for_bytes("text/uri-list", &glib::Bytes::from_owned(format!("{uri}\r\n").into_bytes()));
        if let Some(f) = t.folder() {
            if is_hidden_folder(&f) {
                return None; // Hidden itself stays where it is (its apps can leave: Vini)
            }
            // the Dock keeps a copy of the folder, which stays the same folder (folder_link)
            let mut f = f;
            if f.link().is_empty() {
                if let Some((p, i)) = self.locate_folder(&f, None) {
                    let link = sonata_desktop::folder_link::new_link();
                    if let Some(Item::Folder(g)) = self.model.borrow_mut().pages[p].get_mut(i) {
                        g.kept.insert("link".into(), json!(link));
                        f = g.clone();
                    }
                    t.item.replace(Item::Folder(f.clone()));
                    self.save();
                }
            }
            providers.push(uri_list(m::encode_folder(&f.name, &f.apps, f.link())));
        } else if let Some(path) = t.app().and_then(|a| self.info(&a)).and_then(|i| sonata_ui::apps::app_filename(&i)) {
            // lets the Dock pin it: plain text/uri-list (a GdkFileList would also
            // offer the portal's file-transfer format, which the Dock picks first)
            providers.push(uri_list(gio::File::for_path(path).uri().to_string()));
        }
        Some(gdk::ContentProvider::new_union(&providers))
    }

    fn drag_begun(self: &Rc<Self>, tile: &Rc<Tile>) {
        if self.on_overlay.get() {
            self.dock_above(true); // pressing raised Launchpad over the Dock: bring it back
        }
        let item = tile.item.borrow().clone();
        let folder = match (&item, self.folder_view.borrow().as_ref()) {
            (Item::App(a), Some(fv)) if fv.folder.apps.contains(a) => Some(fv.folder.clone()),
            _ => None,
        };
        if folder.is_some() {
            // out of the folder: the grid behind it takes the drag again (Vini:
            // in the Apps Menu an app couldn't be dragged out of its folder)
            self.host().1.set_can_target(true);
        }
        self.drag.replace(Some(Drag { item, tile: tile.clone(), folder, target: None, pending: None, out: None, out_mark: None }));
        tile.button.add_css_class("dragging");
    }

    fn drag_ended(self: &Rc<Self>, tile: &Rc<Tile>) {
        tile.button.remove_css_class("dragging");
        if self.drag.borrow().is_some() {
            self.mark_out(None, 0, false); // (not dropped on the grid: it stays in its folder)
        }
        self.clear_target();
        self.drag.replace(None);
        if self.folder_view.borrow().is_some() {
            self.host().1.set_can_target(false); // still open (dropped back in it): clicks close it again
        }
        self.save();
        self.render();
    }

    fn timer(self: &Rc<Self>, name: &'static str, ms: u64, f: impl Fn() + 'static) {
        if self.timers.borrow().contains_key(name) {
            return;
        }
        let w = Rc::downgrade(self);
        let id = glib::timeout_add_local_once(Duration::from_millis(ms), move || {
            if let Some(p) = w.upgrade() {
                p.timers.borrow_mut().remove(name);
            }
            f();
        });
        self.timers.borrow_mut().insert(name, id);
    }

    fn cancel(&self, name: &str) {
        if let Some(id) = self.timers.borrow_mut().remove(name) {
            sonata_ui::cancel(id);
        }
    }

    fn clear_target(&self) {
        self.cancel("folder");
        if let Some(t) = self.drag.borrow_mut().as_mut().and_then(|d| d.target.take()) {
            t.button.remove_css_class("folder-target");
        }
    }

    /// Where an app held out of its folder will go: a bar on the side of the
    /// app it goes beside -- before page[index], or after it (after, or
    /// past the last one). grid None: no mark.
    fn mark_out(self: &Rc<Self>, grid: Option<&PageGrid>, index: usize, after: bool) {
        let old = self.drag.borrow_mut().as_mut().and_then(|d| d.out_mark.take());
        if let Some(o) = old {
            o.button.remove_css_class("drop-before");
            o.button.remove_css_class("drop-after");
        }
        let Some(grid) = grid.filter(|_| self.drag.borrow().is_some()) else { return };
        let shown = self.pages_with_hidden();
        let Some(page) = usize::try_from(grid.index.get()).ok().and_then(|i| shown.get(i)).filter(|p| !p.is_empty()) else { return };
        let (index, after) = if index >= page.len() { (page.len() - 1, true) } else { (index, after) };
        let t = self.item_widget(&page[index]);
        t.button.add_css_class(if after { "drop-after" } else { "drop-before" });
        if let Some(d) = self.drag.borrow_mut().as_mut() {
            d.out_mark = Some(t);
        }
    }

    /// Let go: the app held out of its folder goes where it was marked
    /// (Hidden: shown again there).
    fn take_out(&self) {
        let mut d = self.drag.borrow_mut();
        let Some(d) = d.as_mut() else { return };
        let (Some(folder), Some((page, index)), Item::App(app)) = (d.folder.clone(), d.out, d.item.clone()) else { return };
        let mut model = self.model.borrow_mut();
        if is_hidden_folder(&folder) {
            // out of Hidden
            if model.hidden.contains(&app) {
                model.hidden.retain(|h| *h != app);
                model.reconcile();
                model.move_item(&Item::App(app), page, index);
            }
        } else {
            model.take_out_of_folder(&folder, &app, page, index);
        }
        d.folder = None;
    }

    pub fn drag_over(self: &Rc<Self>, grid: &Rc<PageGrid>, x: f64, y: f64) -> gdk::DragAction {
        let mv = gdk::DragAction::MOVE;
        let Some((item, folder)) = self.drag.borrow().as_ref().map(|d| (d.item.clone(), d.folder.clone())) else {
            return mv; // an app dragged from the Dock: dropping here unpins it
        };
        let Ok(gi) = usize::try_from(grid.index.get()) else { return mv };
        if folder.is_some() && self.folder_view.borrow().is_some() {
            self.close_folder(); // dragged out of the open folder (Hidden too)
        }
        let (index, centre) = grid.cell_at(x, y);
        let shown = self.pages_with_hidden(); // the Hidden folder too: dropping on it hides
        let page = shown.get(gi).cloned().unwrap_or_default();
        let target = page.get(index).cloned();
        if let Some(target) = target.clone().filter(|t| centre && *t != item && !item.is_folder()) {
            self.cancel("reorder");
            if let Some(d) = self.drag.borrow_mut().as_mut() {
                d.pending = None;
                d.out = None;
            }
            self.mark_out(None, 0, false);
            let tw = self.item_widget(&target);
            let same = self.drag.borrow().as_ref().and_then(|d| d.target.clone()).is_some_and(|t| Rc::ptr_eq(&t, &tw));
            if !same {
                self.clear_target();
                if let Some(d) = self.drag.borrow_mut().as_mut() {
                    d.target = Some(tw.clone());
                }
                self.timer("folder", FOLDER_HOLD_MS, move || tw.button.add_css_class("folder-target"));
            }
            return mv;
        }
        self.clear_target();
        // held out of its folder: only marked -- it goes there when let go
        // (Vini: it moved as soon as it left the folder, the grid jumping)
        if folder.is_some() {
            let right = target.is_some() && grid.right_half(x);
            let out = logic::out_place(gi, index, page.len(), right);
            let changed = self.drag.borrow().as_ref().is_some_and(|d| d.out != Some(out));
            if changed {
                if let Some(d) = self.drag.borrow_mut().as_mut() {
                    d.out = Some(out);
                }
                self.mark_out(Some(grid), index, right);
            }
            return mv;
        }
        // reorder live, after a short pause over the same slot
        let want = (gi, index);
        let changed = self.drag.borrow().as_ref().is_some_and(|d| d.pending != Some(want));
        if changed {
            if let Some(d) = self.drag.borrow_mut().as_mut() {
                d.pending = Some(want);
            }
            self.cancel("reorder");
            let w = Rc::downgrade(self);
            self.timer("reorder", REORDER_HOLD_MS, move || {
                if let Some(p) = w.upgrade() {
                    p.reorder_to(want.0, want.1);
                }
            });
        }
        mv
    }

    fn reorder_to(self: &Rc<Self>, page_i: usize, index: usize) {
        let item = {
            let mut d = self.drag.borrow_mut();
            let Some(d) = d.as_mut().filter(|d| d.pending == Some((page_i, index))) else { return };
            d.pending = None;
            d.item.clone()
        };
        let len = self.model.borrow().pages.get(page_i).map(Vec::len).unwrap_or(0);
        let loc = self.top_location(&item);
        let here = loc.is_some_and(|l| l.0 == page_i);
        if loc != Some((page_i, index.min(len.saturating_sub(usize::from(here))))) {
            self.model.borrow_mut().move_item(&item, page_i, index);
            self.render();
        }
    }

    fn top_location(&self, item: &Item) -> Option<(usize, usize)> {
        self.model.borrow().pages.iter().enumerate().find_map(|(p, pg)| pg.iter().position(|it| it == item).map(|i| (p, i)))
    }

    fn flip(self: &Rc<Self>, page: i64) {
        let n = self.pages.borrow().len() as i64;
        if (0..n).contains(&page) {
            self.scroll_to_page(page);
        } else if page == n {
            let item = self.drag.borrow().as_ref().map(|d| d.item.clone());
            if let Some(item) = item {
                self.model.borrow_mut().move_item(&item, page as usize, 0); // a new last page
                self.render();
                self.scroll_to_page(page);
            }
        }
    }

    pub fn drag_drop(self: &Rc<Self>, grid: &Rc<PageGrid>, x: f64, y: f64, value: Option<String>) -> bool {
        if self.drag.borrow().is_none() {
            // dragged out of the Dock (its desktop id): accepted -- the Dock sees
            // a finished move and takes the app out (macOS)
            let value = value.unwrap_or_default();
            if let Some(folder) = m::decode_folder(&value) {
                return self.drop_dock_folder(grid, x, y, &folder); // a Dock folder: it moves here
            }
            return self.installed.borrow().contains_key(&value);
        }
        self.mark_out(None, 0, false);
        let (target, folder, out) = self.drag.borrow().as_ref().map(|d| (d.target.clone(), d.folder.clone(), d.out)).unwrap();
        if let (Some(_), Some(t)) = (&folder, target.as_ref().filter(|t| t.button.has_css_class("folder-target"))) {
            let at = out.or_else(|| self.top_location(&t.item.borrow())).unwrap_or((self.model.borrow().pages.len().saturating_sub(1), 0));
            if let Some(d) = self.drag.borrow_mut().as_mut() {
                d.out = Some(at);
            }
        }
        self.take_out(); // out of its folder: now, where marked
        self.drop_on_target();
        self.save();
        self.render();
        true
    }

    /// Dropped on an app (a new folder) or a folder (into it; Hidden:
    /// hidden) held long enough to light up.
    fn drop_on_target(self: &Rc<Self>) {
        let Some((t, item)) = self.drag.borrow().as_ref().and_then(|d| Some((d.target.clone()?, d.item.clone()))) else { return };
        if !t.button.has_css_class("folder-target") {
            return;
        }
        let Item::App(dragged) = item else { return };
        let target = t.item.borrow().clone();
        match &target {
            Item::Folder(f) if is_hidden_folder(f) => self.hide_app(&dragged), // hidden (and off the Dock)
            Item::Folder(f) => {
                let name = f.name.clone();
                self.model.borrow_mut().make_folder(&target, &dragged, &name);
            }
            Item::App(a) => {
                let cats = |k: &str| self.info(k).and_then(|i| i.categories()).map(|c| c.split(';').filter(|s| !s.is_empty()).map(String::from).collect::<Vec<_>>()).unwrap_or_default();
                let name = m::folder_name(&cats(a), &cats(&dragged));
                self.model.borrow_mut().make_folder(&target, &dragged, &name);
            }
        }
    }

    /// A folder dragged out of the Dock lands at that cell (its apps come
    /// out of where they were); the Dock then lets it go.
    fn drop_dock_folder(self: &Rc<Self>, grid: &PageGrid, x: f64, y: f64, f: &Folder) -> bool {
        let Ok(gi) = usize::try_from(grid.index.get()) else { return false };
        let (index, _) = grid.cell_at(x, y);
        if self.model.borrow_mut().add_folder(&f.name, &f.apps, gi, index, f.link()).is_none() {
            return false;
        }
        self.save();
        self.render();
        true
    }
}

/// A name you click to edit, centred before, during and after editing
/// (ui.controls.centred_editable_label; Vini: a folder's name moved left
/// once renamed). GtkEditableLabel's own label keeps xalign 0 and its
/// stack keeps the entry's width: both set here, again on each edit.
pub fn centred_editable_label(text: &str) -> gtk::EditableLabel {
    let el = gtk::EditableLabel::builder().text(text).halign(gtk::Align::Center).xalign(0.5).build();
    fn walk(mut w: Option<gtk::Widget>) {
        while let Some(c) = w {
            if let Some(l) = c.downcast_ref::<gtk::Label>() {
                l.set_xalign(0.5);
                l.set_justify(gtk::Justification::Center);
            } else if let Some(st) = c.downcast_ref::<gtk::Stack>() {
                st.set_hhomogeneous(false);
                st.set_interpolate_size(true);
            }
            walk(c.first_child());
            w = c.next_sibling();
        }
    }
    walk(el.first_child());
    el.connect_editing_notify(|el| walk(el.first_child()));
    el
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Vini: a web app's right-click menu in Apps had "Change Icon…",
    /// which he didn't want there; Edit Web App… and Delete Web App… stay.
    #[test]
    fn test_web_app_menu_had_change_icon() {
        let src = include_str!("pad.rs");
        let code = &src[..src.find("#[cfg(test)]").unwrap()];
        let f = &code[code.find("fn app_sections(").unwrap()..];
        let f = &f[..f.find("\n    }\n").unwrap()];
        // (the macro's name built: the translation check reads every tr! call)
        let item = |s: &str| format!("{}!(\"{s}\")", "tr");
        assert!(!f.contains(&item("Change Icon…")));
        assert!(f.contains(&item("Edit Web App…")) && f.contains(&item("Delete Web App…")));
    }

    /// Vini: in Launchpad, a right-click on an app inside an open folder
    /// opened no menu. The folder's grid was let go once its panel was
    /// made; its tiles' right-click gestures hold the tile weakly, so the
    /// press found nothing to show a menu for. The open folder keeps it.
    #[test]
    fn test_right_click_in_folder_opened_no_menu() {
        let src = include_str!("pad.rs");
        let code = &src[..src.find("#[cfg(test)]").unwrap()];
        let fv = &code[code.find("pub struct FolderView").unwrap()..];
        assert!(fv[..fv.find("\n}\n").unwrap()].contains("pub grid: Rc<PageGrid>"));
        let open = &code[code.find("pub fn open_folder(").unwrap()..];
        let open = &open[..open.find("\n    }\n").unwrap()];
        assert!(open.contains("let grid = self.folder_grid(f);") && open.contains("title, grid }"));
        let sync = &code[code.find("fn sync_folder_view(").unwrap()..];
        assert!(sync[..sync.find("\n    }\n").unwrap()].contains("fv.grid = grid;"));
        // the tiles' gesture holds the tile weakly: what keeps it is the grid
        let tiles = include_str!("tiles.rs");
        assert!(tiles.contains("let (w, t) = (Rc::downgrade(pad), Rc::downgrade(&tile));"));
    }

    fn entry(dir: &std::path::Path, file: &str, name: &str) {
        std::fs::create_dir_all(dir).unwrap();
        std::fs::write(dir.join(file), format!("[Desktop Entry]\nType=Application\nName={name}\nExec=true\n")).unwrap();
    }

    /// "Manage Printing" (cups.desktop) showed in Launchpad and the Apps
    /// Menu, and names kept their packaging noise ("Spotify (Launcher)"):
    /// GIO's own should_show / display_name were used, not apps.py's.
    #[test]
    fn test_manage_printing_and_launcher_suffix_shown() {
        let apps = glib::user_data_dir().join("applications");
        entry(&apps, "cups.desktop", "Manage Printing");
        entry(&apps, "lp-test-spotify.desktop", "Spotify (Launcher)");
        let installed = installed_apps();
        assert!(!installed.contains_key("cups"));
        assert_eq!(names_of(&installed).get("lp-test-spotify").map(String::as_str), Some("Spotify"));
    }

    /// A folder's name moved left once renamed: the EditableLabel's inner
    /// label kept xalign 0 (ui.controls.centred_editable_label).
    #[test]
    fn test_folder_name_moved_left_once_renamed() {
        if gtk::init().is_err() {
            return; // (no display)
        }
        let el = centred_editable_label("Games");
        let mut labels = 0;
        fn each(w: Option<gtk::Widget>, f: &mut dyn FnMut(&gtk::Widget)) {
            let mut w = w;
            while let Some(c) = w {
                f(&c);
                each(c.first_child(), f);
                w = c.next_sibling();
            }
        }
        each(el.first_child(), &mut |c| {
            if let Some(l) = c.downcast_ref::<gtk::Label>() {
                labels += 1;
                assert_eq!(l.xalign(), 0.5);
            }
            if let Some(st) = c.downcast_ref::<gtk::Stack>() {
                assert!(!st.is_hhomogeneous());
            }
        });
        assert!(labels > 0);
    }
}
