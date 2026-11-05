//! The Settings window (app.py's Settings class, its frame): the sidebar
//! with your card, the search and the sections; one pane per section,
//! built when first shown from its parts' page builders (pages/), and
//! built again when a file it shows changed meanwhile (PAGE_CONFIGS);
//! details pushed in place (an app in Apps) with a back button.
//!
//! The window is the pages' `Ctx`: see pages/mod.rs for what they may use.
//!
//! The window's own traffic lights sit in the sidebar's header (buttons on
//! the left) or each pane's (on the right), and move when Settings >
//! Appearance changes their side or order (place_lights).
//!
//! A section rebuilt while shown (reload_page) cross-fades from a picture
//! of how it looked: no flash.

use crate::pages;
use crate::search::{self, Hit};
use crate::sections::{self, parts_of, section_of, SECTIONS};
use adw::prelude::*;
use gtk::{gdk, glib};
use serde_json::{Map, Value};
use sonata_i18n::tr;
use std::any::Any;
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};
use std::time::{Duration, SystemTime};

/// The pages' handle on the window.
pub type Ctx = Rc<Settings>;

pub const SIDEBAR_W: i32 = 280;
/// A moving slider is saved at most this often (`save_live`).
pub const GLASS_LIVE_MS: u64 = 120;

/// One built section: its header (title, back button) and its page.
pub struct Pane {
    pub tv: adw::ToolbarView,
    pub hb: adw::HeaderBar,
    pub title: gtk::Label,
    /// the section's own page (a detail pushed over it is `detail`)
    pub main: adw::PreferencesPage,
    back: RefCell<Option<gtk::Button>>,
    detail: RefCell<Option<adw::PreferencesPage>>,
    /// the window buttons, when they're on the right (at the pane's edge)
    lights: RefCell<Option<gtk::Box>>,
}

#[derive(Default)]
struct Job {
    busy: Cell<bool>,
    pending: RefCell<Option<Box<dyn FnOnce() + Send>>>,
}

pub struct Settings {
    pub window: adw::ApplicationWindow,
    toasts: adw::ToastOverlay,
    content: gtk::Stack,
    /// around `content`: a section rebuilt in place fades in (reload_page)
    fade: sonata_ui::transition::CrossFade,
    /// reload_page is showing the section again: its picture stays to fade
    reloading: Cell<bool>,
    /// the sidebar's search field (Ctrl+F)
    pub search: gtk::SearchEntry,
    listbox: gtk::ListBox,
    rows: RefCell<Vec<(&'static str, gtk::ListBoxRow)>>,
    results: gtk::ListBox,
    results_title: gtk::Label,
    hits: RefCell<Vec<(gtk::ListBoxRow, Hit)>>,
    pages: RefCell<HashMap<&'static str, Rc<Pane>>>,
    built: RefCell<HashMap<&'static str, SystemTime>>,
    current: Cell<Option<&'static str>>,
    jobs: RefCell<HashMap<String, Rc<Job>>>,
    slots: RefCell<HashMap<String, Rc<dyn Any>>>,
    focus_handlers: RefCell<HashMap<String, Rc<dyn Fn(&str)>>>,
    focus: RefCell<Option<(String, String)>>,
    close_hooks: RefCell<Vec<Rc<dyn Fn()>>>,
    live: RefCell<Map<String, Value>>,
    live_src: RefCell<Option<glib::SourceId>>,
    pub card_avatar: adw::Avatar,
    found: RefCell<Option<gtk::Widget>>,
    /// the sidebar's header and the window buttons in it (on the left)
    side_hb: RefCell<Option<adw::HeaderBar>>,
    side_lights: RefCell<Option<gtk::Box>>,
    me: Weak<Settings>,
}

impl Settings {
    /// A Settings window opened on `start` (a section, a part's old id, or
    /// "appicons/<desktop id>"); unknown: Appearance.
    pub fn new(app: &impl IsA<gtk::Application>, start: &str) -> Ctx {
        crate::kit::register_css();
        let me_name = {
            let real = glib::real_name().to_string_lossy().into_owned();
            if real.is_empty() || real == "Unknown" {
                glib::user_name().to_string_lossy().into_owned()
            } else {
                real
            }
        };
        let s = Rc::new_cyclic(|me: &Weak<Settings>| {
            let window = adw::ApplicationWindow::builder().application(app).title(tr!("Settings")).build();
            for c in ["sonata-settings", "sonata-glass"] {
                window.add_css_class(c); // added, not passed (keeps GTK's "csd")
            }
            // Resizable with a working Zoom (Vini's call; macOS keeps it fixed)
            // the first time about two thirds of the display (app.py's
            // roomy=True), after that its last size
            sonata_ui::window::remember_size_roomy(window.upcast_ref(), "settings", 920, 640);
            window.set_size_request(760, 480);
            sonata_ui::window::standard(&window);
            let search = crate::kit::search_entry(Some(&tr!("Search")), false);
            let content = gtk::Stack::builder().transition_type(gtk::StackTransitionType::None).build();
            let fade = sonata_ui::transition::CrossFade::new(&content);
            Settings {
                window,
                toasts: adw::ToastOverlay::new(),
                content,
                fade,
                reloading: Cell::new(false),
                search,
                listbox: gtk::ListBox::builder().css_classes(["navigation-sidebar"]).vexpand(true).build(),
                rows: RefCell::new(Vec::new()),
                results: gtk::ListBox::builder().css_classes(["navigation-sidebar", "st-results"]).visible(false).selection_mode(gtk::SelectionMode::None).build(),
                results_title: gtk::Label::builder().label(tr!("Settings")).xalign(0.0).css_classes(["st-results-title"]).visible(false).build(),
                hits: RefCell::new(Vec::new()),
                pages: RefCell::new(HashMap::new()),
                built: RefCell::new(HashMap::new()),
                current: Cell::new(None),
                jobs: RefCell::new(HashMap::new()),
                slots: RefCell::new(HashMap::new()),
                focus_handlers: RefCell::new(HashMap::new()),
                focus: RefCell::new(None),
                close_hooks: RefCell::new(Vec::new()),
                live: RefCell::new(Map::new()),
                live_src: RefCell::new(None),
                card_avatar: adw::Avatar::new(44, Some(&me_name), true),
                found: RefCell::new(None),
                side_hb: RefCell::new(None),
                side_lights: RefCell::new(None),
                me: me.clone(),
            }
        });
        sonata_tweaks::trafficlights::style_windows(); // Button colours on its own buttons
        s.layout(&me_name);
        // changed elsewhere too: this window follows
        let w = s.me.clone();
        sonata_ui::on_buttons_change(move || {
            if let Some(s) = w.upgrade() {
                s.place_lights();
            }
        });
        let w = s.me.clone();
        s.window.connect_close_request(move |_| {
            if let Some(s) = w.upgrade() {
                let hooks: Vec<Rc<dyn Fn()>> = s.close_hooks.borrow().clone();
                for h in hooks {
                    h(); // (Bluetooth's search for nearby devices ends with the window)
                }
                s.flush_live(); // a slider's last value
            }
            glib::Propagation::Proceed
        });
        let keys = gtk::EventControllerKey::new();
        let w = s.me.clone();
        keys.connect_key_pressed(move |_, key, _, state| match w.upgrade() {
            Some(s) => s.key(key, state),
            None => glib::Propagation::Proceed,
        });
        s.window.add_controller(keys);
        let first = section_of(start.split('/').next().unwrap_or(""));
        s.select(if sections::id(first).is_some() { start } else { "appearance" });
        s
    }

    fn layout(self: &Rc<Self>, me_name: &str) {
        // Sidebar | content in a plain box (like Files): whole-pixel edges,
        // no see-through seam between the panes.
        let split = gtk::Box::builder().vexpand(true).build();
        let side = self.sidebar(me_name);
        side.set_size_request(SIDEBAR_W, -1);
        side.add_css_class("sidebar-pane");
        split.append(&side);
        split.append(&gtk::Separator::builder().orientation(gtk::Orientation::Vertical).css_classes(["st-divider"]).build());
        // one content page; the sections are its stack's children
        let pane = adw::NavigationPage::builder().title(tr!("Settings")).child(&self.fade.overlay).hexpand(true).css_classes(["st-content"]).build();
        split.append(&pane);
        self.toasts.set_child(Some(&split));
        self.window.set_content(Some(&self.toasts));
    }

    fn key(&self, key: gdk::Key, state: gdk::ModifierType) -> glib::Propagation {
        if (key == gdk::Key::f || key == gdk::Key::F) && state.contains(gdk::ModifierType::CONTROL_MASK) {
            self.search.grab_focus(); // Ctrl+F: the search field (Cmd+F)
            return glib::Propagation::Stop;
        }
        if key == gdk::Key::Escape {
            if !self.search.text().is_empty() {
                self.search.set_text("");
            } else {
                self.window.close();
            }
            return glib::Propagation::Stop;
        }
        glib::Propagation::Proceed
    }

    // -- sidebar -------------------------------------------------------------------------------------
    /// The window's buttons (Zoom works: Settings is resizable, Vini's call).
    fn lights(&self) -> gtk::Box {
        let (c, m, z) = (self.window.downgrade(), self.window.downgrade(), self.window.downgrade());
        sonata_ui::window::traffic_lights(
            move || {
                if let Some(w) = c.upgrade() {
                    w.close();
                }
            },
            move || {
                if let Some(w) = m.upgrade() {
                    w.minimize();
                }
            },
            Some(Box::new(move || {
                if let Some(w) = z.upgrade() {
                    sonata_ui::window::toggle_zoom(&w);
                }
            })),
        )
    }

    /// This window's buttons where Window buttons says, now: the sidebar's
    /// header (left) or each pane's (right), in that side's order.
    pub fn place_lights(&self) {
        let right = sonata_ui::window::buttons_side() == "right";
        let hb = self.side_hb.borrow().clone();
        if let (Some(hb), Some(old)) = (hb.as_ref(), self.side_lights.take()) {
            hb.remove(&old);
        }
        if let (false, Some(hb)) = (right, hb.as_ref()) {
            let l = self.lights();
            hb.pack_start(&l);
            self.side_lights.replace(Some(l));
        }
        for pane in self.pages.borrow().values() {
            if let Some(old) = pane.lights.take() {
                pane.hb.remove(&old);
            }
            if right {
                let l = self.lights();
                pane.hb.pack_end(&l);
                pane.lights.replace(Some(l));
            }
        }
    }

    fn sidebar(self: &Rc<Self>, me_name: &str) -> adw::NavigationPage {
        let tv = adw::ToolbarView::new();
        let hb = adw::HeaderBar::builder().show_title(false).show_start_title_buttons(false).show_end_title_buttons(false).build();
        if sonata_ui::window::buttons_side() == "left" {
            // on the right: each pane's header (build)
            let l = self.lights();
            hb.pack_start(&l);
            self.side_lights.replace(Some(l));
        }
        self.side_hb.replace(Some(hb.clone()));
        tv.add_top_bar(&hb);
        let bx = gtk::Box::new(gtk::Orientation::Vertical, 0);
        // your picture (like the Apple ID card); a click opens Users & Groups
        let card = gtk::Box::builder().spacing(10).css_classes(["st-card"]).build();
        card.append(&self.card_avatar);
        self.refresh_card();
        let click = gtk::GestureClick::new();
        let w = self.me.clone();
        click.connect_released(move |_, _, _, _| {
            if let Some(s) = w.upgrade() {
                s.select("users");
            }
        });
        card.add_controller(click);
        let texts = gtk::Box::builder().orientation(gtk::Orientation::Vertical).valign(gtk::Align::Center).build();
        texts.append(&gtk::Label::builder().label(me_name).xalign(0.0).css_classes(["st-card-title"]).build());
        texts.append(&gtk::Label::builder().label("Sonata 2").xalign(0.0).css_classes(["st-card-sub"]).build());
        card.append(&texts);
        // Search, macOS Ventura style: filters the sections as you type;
        // Return opens the first match
        bx.append(&self.search);
        bx.append(&card);
        let w = self.me.clone();
        self.search.connect_search_changed(move |_| {
            if let Some(s) = w.upgrade() {
                s.filter_sections();
            }
        });
        let w = self.me.clone();
        self.search.connect_activate(move |_| {
            if let Some(s) = w.upgrade() {
                s.open_first_match();
            }
        });
        let mut last = "";
        for (sid, _, icon, color, grp) in SECTIONS {
            if !last.is_empty() && grp != last {
                // a gap between groups, like System Settings
                let gap = gtk::ListBoxRow::builder().selectable(false).activatable(false).css_classes(["st-gap-row"]).build();
                gap.set_child(Some(&gtk::Box::new(gtk::Orientation::Horizontal, 0)));
                self.listbox.append(&gap);
            }
            last = grp;
            let row = gtk::ListBoxRow::new();
            let h = gtk::Box::builder().spacing(10).margin_top(3).margin_bottom(3).margin_start(2).build();
            h.append(&crate::kit::badge(icon, color, false));
            h.append(&gtk::Label::builder().label(sections::title(sid)).xalign(0.0).build());
            row.set_child(Some(&h));
            self.listbox.append(&row);
            self.rows.borrow_mut().push((sid, row));
        }
        let w = self.me.clone();
        self.listbox.connect_row_selected(move |_, row| {
            let (Some(s), Some(row)) = (w.upgrade(), row) else { return };
            if let Some(sid) = s.sid_of(row) {
                s.show(sid);
            }
        });
        // the section you're in, clicked again: back to its first page (Vini: from an app in Apps)
        let w = self.me.clone();
        self.listbox.connect_row_activated(move |_, row| {
            let Some(s) = w.upgrade() else { return };
            if let Some(sid) = s.sid_of(row).filter(|sid| Some(*sid) == s.current.get()) {
                s.pop_detail(Some(sid));
            }
        });
        bx.append(&self.listbox); // the selection jumps: no slide (Vini)
        // every setting the search finds, under the sections (Vini: any text
        // in Settings, and a click goes right to that setting)
        let w = self.me.clone();
        self.results.connect_row_activated(move |_, row| {
            let Some(s) = w.upgrade() else { return };
            let hit = s.hits.borrow().iter().find(|(r, _)| r == row).map(|(_, h)| h.clone());
            if let Some(hit) = hit {
                s.open_hit(&hit);
            }
        });
        bx.append(&self.results_title);
        bx.append(&self.results);
        tv.set_content(Some(&gtk::ScrolledWindow::builder().child(&bx).hscrollbar_policy(gtk::PolicyType::Never).build()));
        adw::NavigationPage::builder().title(tr!("Settings")).child(&tv).css_classes(["st-sidebar", "sonata-sidebar"]).build()
    }

    fn sid_of(&self, row: &gtk::ListBoxRow) -> Option<&'static str> {
        self.rows.borrow().iter().find(|(_, r)| r == row).map(|(sid, _)| *sid)
    }

    fn row_of(&self, sid: &str) -> Option<gtk::ListBoxRow> {
        self.rows.borrow().iter().find(|(s, _)| *s == sid).map(|(_, r)| r.clone())
    }

    /// The card's picture: AccountsService's (what the login screen shows),
    /// else ~/.face, else your initials.
    pub fn refresh_card(&self) {
        let user = glib::user_name().to_string_lossy().into_owned();
        let avatar = self.card_avatar.downgrade();
        crate::kit::run_async(
            move || {
                [std::path::PathBuf::from(format!("/var/lib/AccountsService/icons/{user}")), glib::home_dir().join(".face")].into_iter().find(|p| p.is_file())
            },
            move |path| {
                let (Some(a), Some(p)) = (avatar.upgrade(), path) else { return };
                if let Ok(t) = gdk::Texture::from_filename(&p) {
                    a.set_custom_image(Some(&t));
                }
            },
        );
    }

    // -- search --------------------------------------------------------------------------------------
    fn filter_sections(&self) {
        let q = self.search.text().trim().to_lowercase();
        let mut row = self.listbox.first_child();
        while let Some(r) = row {
            row = r.next_sibling();
            let Some(lr) = r.downcast_ref::<gtk::ListBoxRow>() else { continue };
            match self.sid_of(lr) {
                None => r.set_visible(q.is_empty()), // group gaps only without a search
                Some(sid) => r.set_visible(q.is_empty() || sections::matches(sid, &sections::title(sid), &q)),
            }
        }
        self.fill_results(&q);
    }

    /// The settings whose text has the query's words, each with its section.
    fn fill_results(&self, q: &str) {
        while let Some(c) = self.results.first_child() {
            self.results.remove(&c);
        }
        let hits = if q.is_empty() { Vec::new() } else { search::find(q, |p| section_of(p).to_string()) };
        let mut kept = Vec::new();
        for hit in hits {
            let row = gtk::ListBoxRow::new();
            let col = gtk::Box::builder().orientation(gtk::Orientation::Vertical).margin_top(3).margin_bottom(3).margin_start(4).build();
            col.append(&gtk::Label::builder().label(&hit.text).xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::End).css_classes(["st-hit-text"]).tooltip_text(&hit.text).build());
            col.append(&gtk::Label::builder().label(sections::title(&hit.sid)).xalign(0.0).css_classes(["st-hit-where"]).build());
            row.set_child(Some(&col));
            self.results.append(&row);
            kept.push((row, hit));
        }
        let any = !kept.is_empty();
        *self.hits.borrow_mut() = kept;
        self.results.set_visible(any);
        self.results_title.set_visible(any);
        self.listbox.set_vexpand(!any);
    }

    fn open_first_match(&self) {
        let rows = self.rows.borrow().clone();
        if let Some((_, r)) = rows.iter().find(|(_, r)| r.get_visible()) {
            self.listbox.select_row(Some(r));
            return;
        }
        // no section: the first setting found
        let first = self.hits.borrow().first().map(|(_, h)| h.clone());
        if let Some(h) = first.filter(|_| self.results.get_visible()) {
            self.open_hit(&h);
        }
    }

    /// A search result: its section, scrolled to that setting, lit up.
    pub fn open_hit(&self, hit: &Hit) {
        self.select(&hit.sid);
        let Some(pane) = self.pane(&hit.sid) else { return };
        let (tries, text, w) = (Cell::new(0), hit.text.clone(), self.me.clone());
        // once the section is laid out (a new one isn't on its first frame)
        glib::timeout_add_local(Duration::from_millis(30), move || {
            tries.set(tries.get() + 1);
            if pane.main.height() <= 0 && tries.get() < 20 {
                return glib::ControlFlow::Continue;
            }
            if let Some(s) = w.upgrade() {
                *s.found.borrow_mut() = search::reveal(pane.main.upcast_ref(), &text);
            }
            glib::ControlFlow::Break
        });
    }

    // -- sections ------------------------------------------------------------------------------------
    /// The built pane of a section (or of a part's old id).
    pub fn pane(&self, sid: &str) -> Option<Rc<Pane>> {
        self.pages.borrow().get(section_of(sid)).cloned()
    }

    /// The section shown now.
    pub fn current(&self) -> Option<&'static str> {
        self.current.get()
    }

    /// Open a section (or a part's old id; "appicons/<desktop id>": that
    /// app's icon form), selecting it in the sidebar.
    pub fn select(&self, sid: &str) {
        let sid = self.take_focus(sid);
        let Some(sid) = sections::id(section_of(&sid)) else { return };
        let Some(row) = self.row_of(sid) else { return };
        if self.listbox.selected_row().as_ref() == Some(&row) {
            self.show(sid); // (selected already: no row-selected)
        } else {
            self.listbox.select_row(Some(&row));
        }
    }

    /// "appicons/<desktop id>" -> "appicons", the app kept for its page.
    fn take_focus(&self, sid: &str) -> String {
        match sid.split_once('/') {
            Some((part, did)) => {
                *self.focus.borrow_mut() = Some((part.to_string(), did.to_string()));
                part.to_string()
            }
            None => sid.to_string(),
        }
    }

    /// The section on screen (the sidebar's selection already says so).
    fn show(&self, sid: &'static str) {
        let stale = self.current.get() != Some(sid) && self.pages.borrow().contains_key(sid) && self.stale(sid);
        if stale {
            if let Some(p) = self.pages.borrow_mut().remove(sid) {
                self.content.remove(&p.tv);
            }
        }
        if !self.pages.borrow().contains_key(sid) {
            let pane = self.build(sid);
            self.content.add_named(&pane.tv, Some(sid));
            self.pages.borrow_mut().insert(sid, pane);
            self.built.borrow_mut().insert(sid, SystemTime::now());
        }
        let hid = section_of("hidden");
        if self.current.get() == Some(hid) && sid != hid {
            // Hidden & Protected Apps locks again
            if let Some(old) = self.pages.borrow_mut().remove(hid) {
                let content = self.content.clone();
                glib::idle_add_local_once(move || content.remove(&old.tv));
            }
        }
        if let Some(cur) = self.current.get().filter(|c| *c != sid) {
            self.pop_detail(Some(cur)); // a section opens on its list again
        }
        self.current.set(Some(sid)); // at once, no fade (Vini)
        if let Some(p) = self.pane(sid) {
            self.content.set_visible_child(&p.tv);
        }
        // (a picture of a page rebuilt in place: never left on top -- but
        // reload_page's own, about to fade out. app.py dropped that one too,
        // so its fade never showed)
        if !self.reloading.get() {
            self.fade.drop_picture();
        }
        let focus = self.focus.borrow().clone();
        if let Some((part, did)) = focus {
            let handler = self.focus_handlers.borrow().get(&part).cloned();
            if section_of(&part) == sid {
                if let Some(h) = handler {
                    *self.focus.borrow_mut() = None;
                    h(&did);
                }
            }
        }
    }

    fn build(&self, sid: &'static str) -> Rc<Pane> {
        let ctx = self.me.upgrade().expect("the window builds its own pages");
        let page = adw::PreferencesPage::new();
        // no hero row: the pane's title names the section
        for part in parts_of(sid) {
            for g in pages::build(part, &ctx).unwrap_or_default() {
                page.add(&g);
            }
        }
        let tv = adw::ToolbarView::new();
        let hb = adw::HeaderBar::builder().show_start_title_buttons(false).show_end_title_buttons(false).build();
        let title = gtk::Label::builder().label(sections::title(sid)).css_classes(["st-pane-title"]).build();
        hb.set_title_widget(Some(&title));
        let lights = (sonata_ui::window::buttons_side() == "right").then(|| {
            // the window buttons at the right edge
            let l = self.lights();
            hb.pack_end(&l);
            l
        });
        tv.add_top_bar(&hb);
        tv.set_content(Some(&page));
        Rc::new(Pane { tv, hb, title, main: page, back: RefCell::new(None), detail: RefCell::new(None), lights: RefCell::new(lights) })
    }

    /// A config file the section shows changed after it was built.
    fn stale(&self, sid: &str) -> bool {
        let Some(built) = self.built.borrow().get(sid).copied() else { return false };
        sections::page_configs(sid).iter().flat_map(|n| sonata_config::paths(n)).filter_map(|p| std::fs::metadata(p).and_then(|m| m.modified()).ok()).any(|t| t > built)
    }

    /// One item of the section in its place (Settings > Apps > an app),
    /// with a back button (‹) and its name as the pane's title.
    pub fn push_detail(&self, title: &str, groups: &[adw::PreferencesGroup]) -> Option<adw::PreferencesPage> {
        let sid = self.current.get()?;
        let pane = self.pane(sid)?;
        let page = adw::PreferencesPage::new();
        for g in groups {
            page.add(g);
        }
        if pane.back.borrow().is_none() {
            let b = gtk::Button::builder().icon_name("go-previous-symbolic").tooltip_text(tr!("Back")).css_classes(["flat", "st-back"]).valign(gtk::Align::Center).build();
            let w = self.me.clone();
            b.connect_clicked(move |_| {
                if let Some(s) = w.upgrade() {
                    s.pop_detail(Some(sid));
                }
            });
            pane.hb.pack_start(&b);
            *pane.back.borrow_mut() = Some(b);
        }
        if let Some(b) = pane.back.borrow().as_ref() {
            b.set_visible(true);
        }
        pane.title.set_label(title);
        pane.tv.set_content(Some(&page));
        *pane.detail.borrow_mut() = Some(page.clone());
        Some(page)
    }

    /// Back to the section's own page (None: the one shown).
    pub fn pop_detail(&self, sid: Option<&str>) {
        let Some(sid) = sid.or(self.current.get()) else { return };
        let Some(pane) = self.pane(sid) else { return };
        if pane.detail.borrow_mut().take().is_none() {
            return;
        }
        pane.tv.set_content(Some(&pane.main));
        if let Some(b) = pane.back.borrow().as_ref() {
            b.set_visible(false);
        }
        pane.title.set_label(&sections::title(sid));
    }

    /// The section built again from its settings (shown as it is now).
    pub fn rebuild_page(&self, sid: &str) {
        let sid = section_of(sid);
        let Some(p) = self.pages.borrow_mut().remove(sid) else { return };
        self.content.remove(&p.tv); // (a stack child's name must be free again)
        if self.current.get() == Some(sid) {
            self.current.set(None);
            if let Some(id) = sections::id(sid) {
                self.show(id);
            }
        }
    }

    /// Rebuild a section (after its content changed); a shown one fades
    /// from how it looked, no flash.
    pub fn reload_page(&self, sid: &str) {
        let sid = section_of(sid);
        let showing = self.current.get() == Some(sid) && self.pages.borrow().contains_key(sid);
        if showing {
            self.fade.capture();
        }
        self.reloading.set(showing);
        self.rebuild_page(sid);
        self.reloading.set(false);
        if showing {
            // the picture taken above fades away: left on top, it covered every
            // section opened after (Vini: after adding a user, Settings stayed on
            // Users & Groups whatever was picked, the new user not in it)
            self.fade.play();
        }
    }

    // -- for the pages -------------------------------------------------------------------------------
    pub fn toast(&self, text: &str) {
        let t = adw::Toast::new(&glib::markup_escape_text(text));
        t.set_timeout(3);
        self.toasts.add_toast(t);
    }

    /// The window as an alert's parent.
    pub fn parent(&self) -> Option<gtk::Widget> {
        Some(self.window.clone().upcast())
    }

    /// Keep a page's object for the window's life (Python's
    /// `self.apps_page = ...`); `kept` finds it again.
    pub fn keep<T: Any>(&self, key: &str, value: Rc<T>) {
        self.slots.borrow_mut().insert(key.to_string(), value);
    }

    pub fn kept<T: Any>(&self, key: &str) -> Option<Rc<T>> {
        self.slots.borrow().get(key).cloned().and_then(|v| v.downcast::<T>().ok())
    }

    /// What opens one item of a part ("appicons/<desktop id>": Launchpad's
    /// "Change Icon…"): called with the id once that part is shown.
    pub fn on_focus(&self, part: &str, handler: impl Fn(&str) + 'static) {
        self.focus_handlers.borrow_mut().insert(part.to_string(), Rc::new(handler));
    }

    /// Run when the window closes (a search for devices ends with it).
    pub fn on_close(&self, hook: impl Fn() + 'static) {
        self.close_hooks.borrow_mut().push(Rc::new(hook));
    }

    /// `work` off the main loop, one at a time per key; while one runs only
    /// the newest call waits (a slider drag: no pile of threads, and the
    /// last value is the one that stays).
    pub fn latest(&self, key: &str, work: impl FnOnce() + Send + 'static) {
        let job = self.jobs.borrow_mut().entry(key.to_string()).or_default().clone();
        if job.busy.get() {
            *job.pending.borrow_mut() = Some(Box::new(work));
            return;
        }
        job.busy.set(true);
        let (w, key) = (self.me.clone(), key.to_string());
        crate::kit::run_async(work, move |_| {
            job.busy.set(false);
            let next = job.pending.borrow_mut().take();
            if let (Some(next), Some(s)) = (next, w.upgrade()) {
                s.latest(&key, next);
            }
        });
    }

    /// Fill `grp` from work() (in a worker thread); a placeholder while loading.
    pub fn async_rows<T: Send + 'static>(&self, grp: &adw::PreferencesGroup, work: impl FnOnce() -> T + Send + 'static, fill: impl FnOnce(T) + 'static) {
        let holder = adw::ActionRow::builder().title(tr!("Loading…")).build();
        grp.add(&holder);
        let g = grp.clone();
        crate::kit::run_async(work, move |res| {
            g.remove(&holder);
            fill(res);
        });
    }

    /// Write one Sonata setting; the running component reloads it (locked
    /// against the Dock / menu bar writing the same file).
    pub fn save(&self, name: &str, key: &str, value: Value) {
        let mut m = Map::new();
        m.insert(key.into(), value);
        let _ = sonata_config::update(name, &Value::Object(m));
    }

    /// A moving slider: saved at most every GLASS_LIVE_MS (live), not once
    /// per pixel -- each save makes the running component reload the file.
    /// The last value is always written.
    pub fn save_live(&self, name: &str, key: &str, value: Value) {
        {
            let mut live = self.live.borrow_mut();
            let e = live.entry(name.to_string()).or_insert_with(|| Value::Object(Map::new()));
            if let Some(o) = e.as_object_mut() {
                o.insert(key.into(), value);
            }
        }
        if self.live_src.borrow().is_none() {
            // throttle, not debounce: live while dragging
            let w = self.me.clone();
            let src = glib::timeout_add_local_once(Duration::from_millis(GLASS_LIVE_MS), move || {
                if let Some(s) = w.upgrade() {
                    s.live_src.borrow_mut().take();
                    s.flush_live();
                }
            });
            *self.live_src.borrow_mut() = Some(src);
        }
    }

    /// Write the sliders' pending values now (also when Settings closes).
    pub fn flush_live(&self) {
        if let Some(src) = self.live_src.borrow_mut().take() {
            sonata_ui::cancel(src);
        }
        let pending = std::mem::take(&mut *self.live.borrow_mut());
        for (name, values) in pending {
            let _ = sonata_config::update(&name, &values);
        }
    }

    /// Ask, then `then()` (Reset / Cancel).
    pub fn ask_reset(&self, title: &str, body: &str, then: impl Fn() + 'static) -> adw::AlertDialog {
        sonata_ui::dialog::alert(title, body, &[("cancel", tr!("Cancel"), ""), ("reset", tr!("Reset"), "destructive")], move |rid| {
            if rid == "reset" {
                then();
            }
        }, self.parent().as_ref())
    }

    /// Restart now or later (a setting that needs it was changed). kind:
    /// "sonata", "session" (log out) or "system"; what: the change, named.
    pub fn ask_restart(&self, kind: &str, what: &str) -> adw::AlertDialog {
        let (heading, body, action) = match kind {
            "sonata" => (tr!("Restart Sonata now?"), tr!("{} takes effect when Sonata restarts. Open apps stay open."), tr!("Restart Sonata")),
            "session" => (tr!("Log out now?"), tr!("{} takes effect from the next login. Save your work first: apps will quit."), tr!("Log Out")),
            _ => (tr!("Restart the computer now?"), tr!("{} takes effect after a restart. Save your work first: apps will quit."), tr!("Restart")),
        };
        let (kind, w) = (kind.to_string(), self.me.clone());
        sonata_ui::dialog::alert(&heading, &body.replacen("{}", what, 1), &[("later", tr!("Later"), ""), ("now", action, "default")], move |rid| {
            let Some(s) = w.upgrade() else { return };
            if rid != "now" {
                s.toast(&if kind != "session" { tr!("It takes effect after the next restart") } else { tr!("It takes effect from the next login") });
                return;
            }
            if kind == "sonata" {
                sonata_backend::system::restart_sonata(&[]);
            } else {
                // the apps quit first (Chrome's tabs): Wayfire's windows,
                // as Settings has no toplevel manager of its own
                let windows = sonata_sessionui::quitapps::session_windows();
                let q = sonata_sessionui::quitapps::end_session(if kind == "session" { "logout" } else { "restart" }, windows, None);
                s.keep("quitting", q);
            }
        }, self.parent().as_ref())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use gtk::gio;

    /// Settings opened at 920 x 640 the first time on any display: app.py
    /// opens it roomy, about two thirds of the display (1267 x 702 on 1080p).
    #[test]
    fn test_first_size_not_roomy() {
        use sonata_ui::window::first_size;
        assert_eq!(first_size(None, 920, 640, true, Some((1920, 1080))), (1267, 702, false));
        assert_eq!(first_size(None, 920, 640, true, Some((1280, 720))), (920, 616, false)); // never smaller (but within the work area)
    }

    fn settle(ms: u64) {
        let ctx = glib::MainContext::default();
        let end = std::time::Instant::now() + Duration::from_millis(ms);
        while std::time::Instant::now() < end {
            ctx.iteration(false);
        }
    }

    /// A picture over the content (CrossFade's overlay holds more than the stack).
    fn picture_on_top(s: &Settings) -> bool {
        let mut n = 0;
        let mut c = s.fade.overlay.first_child();
        while let Some(w) = c {
            n += 1;
            c = w.next_sibling();
        }
        n > 1
    }

    /// The content has been drawn (a picture of it can be taken): not on a
    /// display no one looks at (broadway without a browser draws nothing).
    fn drawn(s: &Settings) -> bool {
        let Some(child) = s.fade.overlay.child() else { return false };
        let snap = gtk::Snapshot::new();
        gtk::WidgetPaintable::new(Some(&child)).snapshot(&snap, child.width() as f64, child.height() as f64);
        snap.to_node().is_some()
    }

    /// tests/test_settings_reload_stuck.py. Vini: after adding a user,
    /// Settings stayed on Users & Groups whatever section was picked: a
    /// picture of the page rebuilt in place was left on top. It fades away,
    /// and a section switch takes a leftover picture away at once. (On a
    /// private display; without one, skipped. Sections that only read
    /// files: nothing here reaches the system.)
    #[test]
    fn test_no_picture_left_on_top() {
        let _lock = crate::tests::CONFIG.lock().unwrap_or_else(|e| e.into_inner());
        let dir = tempfile::tempdir().unwrap();
        std::env::set_var("XDG_CONFIG_HOME", dir.path());
        if gtk::init().is_err() {
            return; // (no display)
        }
        sonata_i18n::use_language("en");
        sonata_ui::setup();
        let app = adw::Application::new(Some("io.test.settings.rs"), gio::ApplicationFlags::NON_UNIQUE);
        let _ = app.register(gio::Cancellable::NONE);
        let s = Settings::new(&app, "appearance");
        s.window.set_default_size(900, 600);
        s.window.present();
        settle(500);
        // rebuilt in place (what adding a user does when it's done): it fades, then goes
        s.reload_page("appearance");
        assert_eq!(s.current(), Some("appearance"));
        if drawn(&s) {
            assert!(picture_on_top(&s), "rebuilt in place: it fades from how it looked");
        }
        settle(800);
        assert!(!picture_on_top(&s), "the picture faded away");
        s.select("dock");
        settle(300);
        assert!(!picture_on_top(&s));
        assert_eq!(s.content.visible_child(), s.pane("dock").map(|p| p.tv.clone().upcast()));
        // (a picture nobody played) a switch takes it away at once, no fade
        s.fade.capture();
        s.select("desktop");
        assert!(!picture_on_top(&s), "gone already, not fading over the new page");
        // the sections that only read Sonata's own files open without a panic
        // (the ones that ask the system -- Bluetooth's search, USBGuard,
        // updates, DDC/CI monitors -- are left out: never the real machine's)
        for sid in ["appicons", "menubar", "controlcenter", "launchpad", "notifications", "accessibility", "mouse", "keyboard", "appearance"] {
            s.select(sid);
            settle(100);
            assert_eq!(s.current(), Some(sid));
        }
        s.window.destroy();
    }
}
