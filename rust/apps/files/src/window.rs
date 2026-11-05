//! Files window (files/window.py; macOS Finder): sidebar | toolbar + tabs
//! + view + path bar.
//!
//! Icons / List / Columns views, navigation (back / forward / enclosing
//! folder), open with the default app, search (this computer or the
//! current folder, recursive, with Finder's criteria), hidden files per
//! folder, live folder updates, context menus, rename, drag and drop,
//! clipboard, undo, tags, Quick Look and Get Info.
//!
//! Tabs (Finder): ⌘T opens a tab on the same folder, ⌘W closes the tab
//! (the window with the last one), Ctrl+Tab / Ctrl+Shift+Tab and ⌘⇧[ / ⌘⇧]
//! switch tabs (⌘ = Ctrl or Super). A middle-click on a folder opens it in
//! a new tab behind. Each tab keeps its folder, view, selection and
//! history.

use crate::folder::{self, file_of, is_dir, APPS, RECENTS};
use crate::views::{ColumnsView, Dnd, IconsView, ListView, OnCommit, View, SORT_BY};
use crate::{actions, folderprefs, ops, pathbar::PathBar, search, sidebar::Sidebar, tabs, tags, undo};
use adw::prelude::*;
use gtk::{gdk, pango};
use serde_json::{json, Value};
use sonata_i18n::{fmt, n_, tr, trn};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};

pub const VIEWS: [(&str, &str, &str); 3] = [("icons", "view-grid-symbolic", n_!("as Icons")), ("list", "view-list-symbolic", n_!("as List")), ("columns", "view-dual-symbolic", n_!("as Columns"))];
const LOADING_MS: u64 = 400;
const ENTER_TWICE_MS: u64 = 350;

fn defaults() -> Value {
    json!({"view": "icons", "list_columns": {}, "path_bar": true, "list_shown": null, "count_sizes": false, "show_hidden": false})
}

fn cfg() -> serde_json::Map<String, Value> {
    sonata_config::load("files", &defaults())
}

fn same_disk(a: &gio::File, b: &gio::File) -> bool {
    let fs = |f: &gio::File| f.query_info("id::filesystem", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).ok().and_then(|i| i.attribute_string("id::filesystem")).map(|s| s.to_string());
    matches!((fs(a), fs(b)), (Some(x), Some(y)) if !x.is_empty() && x == y)
}

/// One Finder tab: its folder (history), its views with their selection,
/// its view mode.
pub struct Tab {
    pub id: u64,
    pub history: RefCell<Vec<String>>,
    pub pos: Cell<isize>,
    pub folder: Rc<folder::Folder>,
    pub name: RefCell<String>,
    pub filtered: gtk::FilterListModel,
    filter: gtk::CustomFilter,
    pub icons: Rc<IconsView>,
    pub list: Rc<ListView>,
    pub columns: Rc<ColumnsView>,
    pub view_id: RefCell<String>,
    stack: gtk::Stack,
    pub widget: gtk::Overlay,
    empty: gtk::Label,
    loading_box: gtk::Box,
    loading_src: RefCell<Option<glib::SourceId>>,
}

impl Tab {
    pub fn uri(&self) -> String {
        let p = self.pos.get();
        if p >= 0 { self.history.borrow().get(p as usize).cloned().unwrap_or_default() } else { String::new() }
    }

    pub fn view(&self) -> Rc<dyn View> {
        match self.view_id.borrow().as_str() {
            "list" => self.list.clone(),
            "columns" => self.columns.clone(),
            _ => self.icons.clone(),
        }
    }

    fn views(&self) -> [(&'static str, Rc<dyn View>); 3] {
        [("icons", self.icons.clone()), ("list", self.list.clone()), ("columns", self.columns.clone())]
    }

    /// That view in front, with the folder's items.
    fn show(&self, vid: &str) {
        let vid = if ["icons", "list", "columns"].contains(&vid) { vid } else { "icons" };
        self.view_id.replace(vid.into());
        self.view().show_items(true);
        self.stack.set_visible_child_name(vid);
    }

    /// A new folder coming: the views not shown let go of the items.
    fn hide_others(&self) {
        let cur = self.view_id.borrow().clone();
        for (id, v) in self.views() {
            if id != cur {
                v.show_items(false);
            }
        }
    }

    pub fn title(&self) -> String {
        let n = self.name.borrow().clone();
        if !n.is_empty() {
            return n;
        }
        let u = self.uri();
        if u.is_empty() { tr!("Files") } else { folder::display_name(&u) }
    }

    /// A load began (shown after LOADING_MS) / ended (hidden at once).
    fn loading(&self, on: bool) {
        if let Some(s) = self.loading_src.take() {
            s.remove();
        }
        if !on {
            self.loading_box.set_visible(false);
            return;
        }
        let b = self.loading_box.downgrade();
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis(LOADING_MS), move || {
            if let Some(b) = b.upgrade() {
                b.set_visible(true);
            }
        });
        self.loading_src.replace(Some(id));
    }
}

pub struct FilesWindow {
    pub window: adw::ApplicationWindow,
    pub tabs: RefCell<Vec<Rc<Tab>>>,
    pub tab: RefCell<Option<Rc<Tab>>>,
    next_id: Cell<u64>,
    show_hidden: bool,
    syncing: Cell<bool>,
    dragged: RefCell<Vec<gio::File>>,
    pub sidebar: Rc<Sidebar>,
    pub strip: Rc<tabs::TabStrip>,
    pub pathbar: Rc<PathBar>,
    tab_stack: gtk::Stack,
    back: gtk::Button,
    fwd: gtk::Button,
    title: gtk::Label,
    empty_btn: gtk::Button,
    view_buttons: Vec<(&'static str, gtk::ToggleButton)>,
    hidden_btn: gtk::ToggleButton,
    pub search: gtk::SearchEntry,
    search_rev: gtk::Revealer,
    search_btn: gtk::Button,
    scope_rev: gtk::Revealer,
    scope_here: gtk::ToggleButton,
    match_contents: gtk::ToggleButton,
    search_kind: gtk::DropDown,
    search_date: gtk::DropDown,
    search_status: gtk::Label,
    searcher: RefCell<search::Search>,
    results: gio::ListStore,
    search_src: RefCell<Option<glib::SourceId>>,
    in_results: Cell<bool>,
    view_before_search: RefCell<Option<String>>,
    typed: RefCell<String>,
    typed_src: RefCell<Option<glib::SourceId>>,
    enter_src: RefCell<Option<glib::SourceId>>,
    title_entry: RefCell<Option<gtk::Entry>>,
    ql: RefCell<Option<Rc<crate::quicklook::QuickLook>>>,
    /// The window's column right of the sidebar (the Open / Save panels add rows to it).
    pub content: gtk::Box,
    tabs_on: bool,
    /// An Open / Save panel's say in what shows and what a double-click does.
    pub embed: RefCell<Option<Embed>>,
    pub me: Weak<FilesWindow>,
}

/// The Open / Save panels (chooser.rs) are a Files window with a say in it.
pub struct Embed {
    /// besides the search: does this item show
    pub matches: Box<dyn Fn(&gio::FileInfo) -> bool>,
    /// a double-click: true when the panel took it
    pub open: Box<dyn Fn(&gio::FileInfo) -> bool>,
    /// the selection or the folder changed
    pub changed: Box<dyn Fn()>,
}

thread_local! {
    static WINDOWS: RefCell<Vec<Rc<FilesWindow>>> = const { RefCell::new(Vec::new()) };
}

fn icon_button(icon: &str, tip: &str, f: impl Fn() + 'static) -> gtk::Button {
    let b = gtk::Button::builder().icon_name(icon).tooltip_text(tip).focus_on_click(false).valign(gtk::Align::Center).build();
    b.connect_clicked(move |_| f());
    b
}

impl FilesWindow {
    pub fn new(app: &gtk::Application, uri: Option<&str>) -> Rc<Self> {
        Self::new_with(app, uri, ("files", 920, 560), true)
    }

    /// `size`: (the key its size is kept under, default width, height);
    /// `tabs` false: one folder only (the Open / Save panels).
    pub fn new_with(app: &gtk::Application, uri: Option<&str>, size: (&str, i32, i32), tabs: bool) -> Rc<Self> {
        let c = cfg();
        let show_hidden = c.get("show_hidden").and_then(Value::as_bool).unwrap_or(false);
        let win = Rc::new_cyclic(|me: &Weak<FilesWindow>| {
            let window = adw::ApplicationWindow::builder().application(app).title(tr!("Files")).build();
            sonata_ui::window::remember_size(window.upcast_ref(), size.0, size.1, size.2);
            for cl in ["sonata-files", "sonata-glass"] {
                window.add_css_class(cl);
            }
            window.set_size_request(560, 320);
            sonata_ui::window::standard(&window);
            let call = |f: fn(&Rc<FilesWindow>)| {
                let w = me.clone();
                move || {
                    if let Some(t) = w.upgrade() {
                        f(&t);
                    }
                }
            };
            let sidebar = Sidebar::new(&gtk::Box::new(gtk::Orientation::Horizontal, 0));
            sidebar.root.set_size_request(280, -1);
            sidebar.root.set_hexpand(false);
            let split = gtk::Box::builder().css_classes(["fs-split"]).build();
            split.append(&sidebar.root);
            split.append(&gtk::Box::builder().css_classes(["fs-divider"]).build());
            let content = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["fs-content"]).hexpand(true).build();
            // -- toolbar
            let bar = gtk::Box::builder().spacing(4).css_classes(["fs-toolbar"]).build();
            let back = icon_button("go-previous-symbolic", &tr!("Back"), call(|t| t.go_back()));
            let fwd = icon_button("go-next-symbolic", &tr!("Forward"), call(|t| t.go_forward()));
            bar.append(&back);
            bar.append(&fwd);
            let title = gtk::Label::builder().css_classes(["fs-title"]).margin_start(6).ellipsize(pango::EllipsizeMode::End).xalign(0.0).build();
            bar.append(&title);
            // a click on the folder's name renames it; the space after it still drags the window
            let click = gtk::GestureClick::builder().button(1).build();
            let f = call(|t| t.rename_folder());
            click.connect_released(move |_, n, _, _| {
                if n == 1 {
                    f();
                }
            });
            title.add_controller(click);
            bar.append(&gtk::Box::builder().hexpand(true).build());
            let empty_btn = gtk::Button::builder().label(tr!("Empty")).valign(gtk::Align::Center).visible(false).css_classes(["fs-text-btn"]).build();
            let f = call(|t| t.empty_trash());
            empty_btn.connect_clicked(move |_| f());
            bar.append(&empty_btn);
            // hidden files in this folder, on or off: remembered per folder
            let eye = gtk::Box::builder().css_classes(["fs-seg"]).valign(gtk::Align::Center).build();
            let hidden_btn = gtk::ToggleButton::builder().icon_name("view-reveal-symbolic").tooltip_text(tr!("Show Hidden Files")).focus_on_click(false).build();
            let w = me.clone();
            hidden_btn.connect_toggled(move |b| {
                if let Some(t) = w.upgrade() {
                    if !t.syncing.get() && t.cur().is_some_and(|tab| b.is_active() != tab.folder.show_hidden.get()) {
                        t.toggle_hidden();
                    }
                }
            });
            eye.append(&hidden_btn);
            bar.append(&eye);
            let seg = gtk::Box::builder().css_classes(["fs-seg"]).valign(gtk::Align::Center).build();
            let mut view_buttons: Vec<(&'static str, gtk::ToggleButton)> = Vec::new();
            for (vid, icon, tip) in VIEWS {
                let b = gtk::ToggleButton::builder().icon_name(icon).tooltip_text(sonata_i18n::gettext(tip)).focus_on_click(false).build();
                if let Some((_, first)) = view_buttons.first() {
                    b.set_group(Some(first));
                }
                let w = me.clone();
                b.connect_toggled(move |b| {
                    if let Some(t) = w.upgrade() {
                        if b.is_active() && !t.syncing.get() {
                            t.set_view(vid, true);
                        }
                    }
                });
                seg.append(&b);
                view_buttons.push((vid, b));
            }
            bar.append(&seg);
            let search = gtk::SearchEntry::builder().placeholder_text(tr!("Search")).width_chars(16).valign(gtk::Align::Center).build();
            let f = call(|t| t.search_changed());
            search.connect_search_changed(move |_| f());
            let f = call(|t| t.close_search());
            search.connect_stop_search(move |_| f());
            let search_rev = gtk::Revealer::builder().child(&search).transition_type(gtk::RevealerTransitionType::SlideLeft).transition_duration(150).build();
            let search_btn = icon_button("system-search-symbolic", &tr!("Search"), call(|t| t.open_search()));
            bar.append(&search_btn);
            bar.append(&search_rev);
            content.append(&gtk::WindowHandle::builder().child(&bar).build());
            let strip = tabs::TabStrip::new();
            content.append(&strip.root);
            // -- search's scope and criteria
            let sb = gtk::Box::builder().spacing(2).css_classes(["fs-scope"]).build();
            sb.append(&gtk::Label::builder().label(tr!("Search:")).css_classes(["fs-scope-title"]).build());
            let scope_home = gtk::ToggleButton::builder().label(tr!("This Computer")).active(true).focus_on_click(false).build();
            let scope_here = gtk::ToggleButton::builder().focus_on_click(false).build();
            scope_here.set_group(Some(&scope_home));
            sb.append(&scope_home);
            sb.append(&scope_here);
            sb.append(&gtk::Box::builder().css_classes(["fs-scope-sep"]).margin_start(6).margin_end(6).build());
            let match_name = gtk::ToggleButton::builder().label(tr!("Name")).active(true).focus_on_click(false).build();
            let match_contents = gtk::ToggleButton::builder().label(tr!("Contents")).focus_on_click(false).build();
            match_contents.set_group(Some(&match_name));
            for b in [&scope_home, &scope_here, &match_name, &match_contents] {
                let f = call(|t| t.run_search());
                b.connect_toggled(move |b| {
                    if b.is_active() {
                        f();
                    }
                });
            }
            sb.append(&match_name);
            sb.append(&match_contents);
            let kinds: Vec<String> = search::KINDS.iter().map(|k| sonata_i18n::gettext(k.1)).collect();
            let dates: Vec<String> = search::DATES.iter().map(|d| sonata_i18n::gettext(d.1)).collect();
            let f = call(|t| t.run_search());
            let search_kind = sonata_ui::controls::popup_button(&kinds, 0, move |_| f());
            let f = call(|t| t.run_search());
            let search_date = sonata_ui::controls::popup_button(&dates, 0, move |_| f());
            for d in [&search_kind, &search_date] {
                d.set_valign(gtk::Align::Center);
                d.set_margin_start(6);
                sb.append(d);
            }
            let search_status = gtk::Label::builder().css_classes(["fs-scope-status"]).hexpand(true).xalign(1.0).build();
            sb.append(&search_status);
            // the criteria never set the window's minimum width: a narrow window scrolls them
            let scroll = gtk::ScrolledWindow::builder().child(&sb).hscrollbar_policy(gtk::PolicyType::External).vscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).build();
            let scope_rev = gtk::Revealer::builder().child(&scroll).transition_duration(150).build();
            content.append(&scope_rev);
            let tab_stack = gtk::Stack::builder().vexpand(true).transition_type(gtk::StackTransitionType::None).build();
            content.append(&tab_stack);
            // where you are, at the bottom (Finder's path bar; Windows' address bar)
            let (w1, w2) = (me.clone(), me.clone());
            let pathbar = PathBar::new(
                move |u| {
                    if let Some(t) = w1.upgrade() {
                        t.go(u, true);
                    }
                },
                Some(Rc::new(move |files, dest, copy| w2.upgrade().is_some_and(|t| t.drop_on(files, dest, copy)))),
            );
            pathbar.root.set_visible(c.get("path_bar").and_then(Value::as_bool).unwrap_or(true));
            content.append(&pathbar.root);
            split.append(&content);
            window.set_content(Some(&split));
            FilesWindow {
                window,
                tabs: RefCell::default(),
                tab: RefCell::default(),
                next_id: Cell::new(1),
                show_hidden,
                syncing: Cell::new(false),
                dragged: RefCell::default(),
                sidebar,
                strip,
                pathbar,
                tab_stack,
                back,
                fwd,
                title,
                empty_btn,
                view_buttons,
                hidden_btn,
                search,
                search_rev,
                search_btn,
                scope_rev,
                scope_here,
                match_contents,
                search_kind,
                search_date,
                search_status,
                searcher: RefCell::default(),
                results: gio::ListStore::new::<gio::FileInfo>(),
                search_src: RefCell::default(),
                in_results: Cell::new(false),
                view_before_search: RefCell::default(),
                typed: RefCell::default(),
                typed_src: RefCell::default(),
                enter_src: RefCell::default(),
                title_entry: RefCell::default(),
                ql: RefCell::default(),
                content,
                tabs_on: tabs,
                embed: RefCell::default(),
                me: me.clone(),
            }
        });
        win.strip.root.set_visible(tabs);
        win.wire();
        let start = uri.map(folder::canonical).filter(|u| !u.is_empty() && u != folder::CONNECT).unwrap_or_else(|| gio::File::for_path(glib::home_dir()).uri().to_string());
        let view = c.get("view").and_then(Value::as_str).unwrap_or("icons").to_string();
        win.add_tab(&start, Some(&view), None, true);
        win
    }

    fn wire(self: &Rc<Self>) {
        {
            let mut h = self.sidebar.hooks.borrow_mut();
            let w = self.me.clone();
            h.on_open = Some(Box::new(move |u| {
                if let Some(t) = w.upgrade() {
                    t.go(u, true);
                }
            }));
            let w = self.me.clone();
            h.on_drop = Some(Box::new(move |files, dest, copy| w.upgrade().is_some_and(|t| t.drop_on(files, dest, copy))));
            let w = self.me.clone();
            h.on_tag = Some(Box::new(move |paths, tag| {
                if let Some(t) = w.upgrade() {
                    t.tag_files(&paths, tag, true);
                }
            }));
            let w = self.me.clone();
            h.on_info = Some(Box::new(move |u| {
                if let Some(t) = w.upgrade() {
                    let f = gio::File::for_uri(u);
                    if let Ok(info) = f.query_info("*", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE) {
                        info.set_attribute_object("sonata::file", &f);
                        crate::quicklook::get_info(&t.window, &info);
                    }
                }
            }));
            let w = self.me.clone();
            h.on_new_tab = Some(Box::new(move |u| {
                if let Some(t) = w.upgrade() {
                    t.new_tab(Some(u), true);
                }
            }));
        }
        let host: Rc<dyn tabs::Host> = self.clone();
        self.strip.set_host(Rc::downgrade(&host));
        let w = self.me.clone();
        *self.pathbar.on_edit.borrow_mut() = Some(Box::new(move || {
            if let Some(t) = w.upgrade() {
                t.edit_address();
            }
        }));
        let w = self.me.clone();
        self.window.connect_is_active_notify(move |win| {
            if win.is_active() {
                if let Some(t) = w.upgrade() {
                    t.sidebar.refresh_space();
                }
            }
        });
        self.shortcuts();
        let w = self.me.clone();
        self.window.connect_close_request(move |_| {
            if let Some(t) = w.upgrade() {
                for tab in t.tabs.borrow().iter() {
                    tab.folder.cancel();
                }
                WINDOWS.with(|l| l.borrow_mut().retain(|x| !Rc::ptr_eq(x, &t)));
            }
            glib::Propagation::Proceed
        });
    }

    pub fn cur(&self) -> Option<Rc<Tab>> {
        self.tab.borrow().clone()
    }

    pub fn view(&self) -> Option<Rc<dyn View>> {
        self.cur().map(|t| t.view())
    }

    // -- tabs -------------------------------------------------------------------------------------
    fn make_tab(self: &Rc<Self>, view_id: &str) -> Rc<Tab> {
        let id = self.next_id.get();
        self.next_id.set(id + 1);
        let (w1, w2, w3) = (self.me.clone(), self.me.clone(), self.me.clone());
        let fo = folder::Folder::new(
            move |u| {
                if let Some(t) = w1.upgrade() {
                    t.tab_loaded(id, u);
                }
            },
            move |u, e| {
                if let Some(t) = w2.upgrade() {
                    t.tab_failed(id, u, e);
                }
            },
            Some(Box::new(move |_| {
                if let Some(tab) = w3.upgrade().and_then(|t| t.tab_by_id(id)) {
                    tab.hide_others();
                    tab.loading(true);
                }
            })),
        );
        fo.show_hidden.set(self.show_hidden);
        let w = self.me.clone();
        let filter = gtk::CustomFilter::new(move |o| {
            let (Some(t), Some(info)) = (w.upgrade(), o.downcast_ref::<gio::FileInfo>()) else { return true };
            if t.cur().map(|c| c.id) != Some(id) {
                return true;
            }
            t.matches(info)
        });
        let filtered = gtk::FilterListModel::new(Some(fo.store.clone()), Some(filter.clone()));
        let model: gio::ListModel = filtered.clone().upcast();
        let w = self.me.clone();
        let open: crate::views::OnOpen = Rc::new(move |info| {
            if let Some(t) = w.upgrade() {
                t.open_item(info);
            }
        });
        let icons = IconsView::new(&model, open.clone());
        let list = ListView::new(&model, open.clone());
        let (w, w2) = (self.me.clone(), Rc::downgrade(&fo));
        let columns = ColumnsView::new(&model, open, move |u| {
            if let Some(t) = w.upgrade() {
                t.column_location(id, u);
            }
        }, move || w2.upgrade().is_some_and(|f| f.show_hidden.get()));
        let c = cfg();
        let shown: Option<Vec<String>> = c.get("list_shown").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect());
        list.show_columns(shown.as_deref());
        list.count_sizes.set(c.get("count_sizes").and_then(Value::as_bool).unwrap_or(false));
        let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(120).build();
        let tab = Rc::new(Tab { id, history: RefCell::default(), pos: Cell::new(-1), folder: fo, name: RefCell::default(), filtered, filter, icons, list, columns, view_id: RefCell::new(view_id.into()), stack, widget: gtk::Overlay::builder().vexpand(true).build(), empty: gtk::Label::builder().css_classes(["fs-empty"]).visible(false).can_target(false).build(), loading_box: gtk::Box::builder().spacing(8).halign(gtk::Align::Center).valign(gtk::Align::Center).visible(false).can_target(false).css_classes(["fs-loading"]).build(), loading_src: RefCell::default() });
        // a header click sorts this folder that way from now on
        let (wt, wi) = (Rc::downgrade(&tab), Rc::downgrade(&tab.icons));
        *tab.list.on_sort.borrow_mut() = Some(Box::new(move |state| {
            if let Some(i) = wi.upgrade() {
                i.set_sort(&state.0, state.1);
            }
            if let Some(t) = wt.upgrade() {
                let u = t.uri();
                if !u.is_empty() {
                    folderprefs::remember(&u, &[("sort", json!([state.0, state.1]))]);
                }
            }
        }));
        let dnd: Rc<dyn Dnd> = self.clone();
        for (vid, v) in tab.views() {
            v.set_dnd(Rc::downgrade(&dnd));
            let w = v.widget();
            // the list's columns scroll sideways when the window is narrow
            let holder: gtk::Widget = if vid == "columns" { w.clone() } else { gtk::ScrolledWindow::builder().child(&w).hscrollbar_policy(if vid == "list" { gtk::PolicyType::Automatic } else { gtk::PolicyType::Never }).build().upcast() };
            tab.stack.add_named(&holder, Some(vid));
            let bg = gtk::DropTarget::new(gdk::FileList::static_type(), gdk::DragAction::COPY | gdk::DragAction::MOVE);
            let wd = self.me.clone();
            bg.connect_drop(move |t, val, _, _| {
                let (Some(win), Ok(list)) = (wd.upgrade(), val.get::<gdk::FileList>()) else { return false };
                let here = win.location();
                win.drop_on(list.files(), gio::File::for_uri(&here), t.current_event_state().contains(gdk::ModifierType::CONTROL_MASK))
            });
            w.add_controller(bg);
            let menu = gtk::GestureClick::builder().button(gdk::BUTTON_SECONDARY).build();
            let (wm, vv) = (self.me.clone(), Rc::downgrade(&v));
            menu.connect_pressed(move |g, _, x, y| {
                if let (Some(win), Some(v), Some(wd)) = (wm.upgrade(), vv.upgrade(), g.widget()) {
                    win.context_menu(&v, &wd, x, y);
                }
            });
            w.add_controller(menu);
            let middle = gtk::GestureClick::builder().button(gdk::BUTTON_MIDDLE).build();
            let (wm, vv) = (self.me.clone(), Rc::downgrade(&v));
            middle.connect_pressed(move |g, _, x, y| {
                let (Some(win), Some(v)) = (wm.upgrade(), vv.upgrade()) else { return };
                if let Some(info) = v.info_at(x, y).filter(is_dir) {
                    g.set_state(gtk::EventSequenceState::Claimed);
                    win.open_in_new_tab(&info, false); // a folder in a new tab (behind)
                }
            });
            w.add_controller(middle);
            let wq = self.me.clone();
            v.selection().connect_selection_changed(move |_, _, _| {
                if let Some(win) = wq.upgrade() {
                    win.follow_quicklook();
                    win.embed_changed();
                }
            });
        }
        tab.widget.set_child(Some(&tab.stack));
        tab.widget.add_overlay(&tab.empty);
        tab.loading_box.append(&sonata_ui::progress::Spinner::new(true));
        tab.loading_box.append(&gtk::Label::new(Some(&tr!("Loading…"))));
        tab.widget.add_overlay(&tab.loading_box);
        tab.show(view_id);
        tab
    }

    fn tab_by_id(&self, id: u64) -> Option<Rc<Tab>> {
        self.tabs.borrow().iter().find(|t| t.id == id).cloned()
    }

    fn add_tab(self: &Rc<Self>, uri: &str, view_id: Option<&str>, index: Option<usize>, select: bool) -> Rc<Tab> {
        let uri = folder::canonical(uri);
        let vid = view_id.map(str::to_string).or_else(|| self.cur().map(|t| t.view_id.borrow().clone())).unwrap_or_else(|| "icons".into());
        let tab = self.make_tab(&vid);
        let index = index.unwrap_or_else(|| self.cur().and_then(|c| self.tabs.borrow().iter().position(|t| Rc::ptr_eq(t, &c))).map_or(self.tabs.borrow().len(), |i| i + 1));
        self.tabs.borrow_mut().insert(index, tab.clone());
        self.tab_stack.add_child(&tab.widget);
        self.strip.add(tab.id, &folder::display_name(&uri), index);
        if select || self.cur().is_none() {
            self.select_tab_rc(&tab);
        }
        tab.history.replace(vec![uri.clone()]);
        tab.pos.set(0);
        self.apply_folder_prefs(&tab, &uri);
        if self.is_current(&tab) {
            self.update_nav();
        }
        tab.folder.load(&uri);
        tab
    }

    fn is_current(&self, tab: &Rc<Tab>) -> bool {
        self.cur().is_some_and(|c| Rc::ptr_eq(&c, tab))
    }

    pub fn new_tab(self: &Rc<Self>, uri: Option<&str>, select: bool) {
        let u = uri.map(str::to_string).or_else(|| self.cur().map(|t| t.uri())).unwrap_or_default();
        self.add_tab(&u, None, None, select);
    }

    fn open_in_new_tab(self: &Rc<Self>, info: &gio::FileInfo, select: bool) {
        if !self.tabs_on {
            return; // a panel shows one folder
        }
        if is_dir(info) {
            let u = info.attribute_string("standard::target-uri").map(|s| s.to_string()).filter(|s| !s.is_empty()).or_else(|| file_of(info).map(|f| f.uri().to_string())).unwrap_or_default();
            self.new_tab(Some(&u), select);
        }
    }

    fn select_tab_rc(&self, tab: &Rc<Tab>) {
        if self.is_current(tab) {
            return;
        }
        if self.cur().is_some() && (!self.search.text().is_empty() || self.search_rev.reveals_child()) {
            self.close_search(); // the search belongs to the tab it ran in
        }
        self.tab.replace(Some(tab.clone()));
        self.tab_stack.set_visible_child(&tab.widget);
        self.strip.set_active(tab.id);
        self.sync_view_buttons();
        self.sync_hidden_button();
        self.update_nav();
        let u = tab.uri();
        if !u.is_empty() {
            self.empty_btn.set_visible(ops::is_trash(&u));
            self.sidebar.select(&u);
            self.pathbar.set_uri(&u);
        }
        self.set_titles(&tab.title());
        self.update_empty();
        tab.view().focus();
    }

    fn set_titles(&self, t: &str) {
        self.title.set_label(t);
        self.window.set_title(Some(t));
    }

    fn close_tab_rc(&self, tab: Option<Rc<Tab>>) {
        let Some(tab) = tab.or_else(|| self.cur()) else { return };
        if self.tabs.borrow().len() <= 1 {
            self.window.close();
            return;
        }
        let i = self.tabs.borrow().iter().position(|t| Rc::ptr_eq(t, &tab)).unwrap_or(0);
        if self.is_current(&tab) {
            let next = {
                let tabs = self.tabs.borrow();
                if i + 1 < tabs.len() { tabs[i + 1].clone() } else { tabs[i - 1].clone() }
            };
            self.select_tab_rc(&next);
        }
        self.tabs.borrow_mut().retain(|t| !Rc::ptr_eq(t, &tab));
        self.strip.remove(tab.id);
        self.tab_stack.remove(&tab.widget);
        tab.folder.cancel();
    }

    fn cycle_tab(&self, step: isize) {
        let tabs = self.tabs.borrow().clone();
        if tabs.len() < 2 {
            return;
        }
        let Some(cur) = self.cur() else { return };
        let i = tabs.iter().position(|t| Rc::ptr_eq(t, &cur)).unwrap_or(0) as isize;
        self.select_tab_rc(&tabs[(i + step).rem_euclid(tabs.len() as isize) as usize]);
    }

    fn embed_changed(&self) {
        if let Some(e) = self.embed.borrow().as_ref() {
            (e.changed)();
        }
    }

    fn tab_loaded(&self, id: u64, uri: &str) {
        let Some(tab) = self.tab_by_id(id) else { return };
        let me = self.me.clone();
        glib::idle_add_local_once(move || {
            if let Some(t) = me.upgrade() {
                t.embed_changed();
            }
        });
        tab.loading(false);
        tab.name.replace(folder::display_name(uri));
        for (_, v) in tab.views() {
            v.unselect_all();
            v.scroll_top();
        }
        tab.columns.reset(uri);
        self.strip.retitle(id, &tab.title());
        if self.is_current(&tab) {
            self.empty_btn.set_visible(ops::is_trash(uri));
            self.set_titles(&tab.title());
            self.sidebar.select(uri);
            self.pathbar.set_uri(uri);
            self.update_empty();
        }
    }

    fn tab_failed(&self, id: u64, uri: &str, err: &glib::Error) {
        let Some(tab) = self.tab_by_id(id) else { return };
        tab.loading(false);
        // stay where we were (drop the failed step from the history)
        {
            let mut h = tab.history.borrow_mut();
            let p = tab.pos.get();
            if p >= 0 && h.get(p as usize).map(String::as_str) == Some(uri) {
                h.remove(p as usize);
                tab.pos.set(p - 1);
            }
        }
        if self.is_current(&tab) {
            self.update_nav();
        }
        if tab.pos.get() < 0 && self.tabs.borrow().len() > 1 {
            self.close_tab_rc(Some(tab)); // a new tab that never opened
        }
        let body = if err.matches(gio::IOErrorEnum::PermissionDenied) { tr!("You don't have permission to see its contents.") } else { err.message().to_string() };
        self.alert(&fmt(&tr!("The folder “{name}” can’t be opened."), &[("name", &folder::display_name(uri))]), &body);
    }

    fn alert(&self, heading: &str, body: &str) {
        sonata_ui::dialog::alert(heading, body, &[("ok", "OK".into(), "default")], |_| {}, Some(self.window.upcast_ref()));
    }

    fn error(&self, heading: &str, err: &glib::Error) {
        self.alert(heading, err.message());
    }

    // -- views ------------------------------------------------------------------------------------
    pub fn set_view(&self, vid: &str, save: bool) {
        let Some(tab) = self.cur() else { return };
        tab.show(vid);
        self.sync_view_buttons();
        if vid == "columns" && tab.pos.get() >= 0 {
            tab.columns.reset(&tab.uri());
        }
        if save {
            // your choice: this folder, and the default
            let u = tab.uri();
            if !u.is_empty() {
                folderprefs::remember(&u, &[("view", json!(vid))]);
            }
            if cfg().get("view").and_then(Value::as_str) != Some(vid) {
                let _ = sonata_config::update("files", &json!({"view": vid}));
            }
        }
        tab.view().focus();
    }

    /// Before a folder shows: its own view and sort, if you chose them there.
    fn apply_folder_prefs(&self, tab: &Rc<Tab>, uri: &str) {
        let p = folderprefs::get(uri);
        tab.folder.show_hidden.set(p.hidden.unwrap_or(self.show_hidden)); // before it lists
        if self.is_current(tab) {
            self.sync_hidden_button();
        }
        let vid = p.view.clone().unwrap_or_else(|| cfg().get("view").and_then(Value::as_str).unwrap_or("icons").to_string());
        if vid != *tab.view_id.borrow() && !self.in_results.get() {
            if self.is_current(tab) {
                self.set_view(&vid, false);
            } else {
                tab.show(&vid);
            }
        }
        let (t, d) = p.sort.unwrap_or((folderprefs::DEFAULT_SORT.0.into(), folderprefs::DEFAULT_SORT.1));
        tab.list.set_sort(&t, d);
        tab.icons.set_sort(&t, d);
    }

    /// View > Sort By: this folder, both icons and list (Finder's direction for each).
    pub fn sort_by(&self, title: &str) {
        let Some(tab) = self.cur() else { return };
        let desc = SORT_BY.iter().find(|s| s.0 == title).map(|s| s.1).unwrap_or(false);
        tab.list.set_sort(title, desc);
        tab.icons.set_sort(title, desc);
        let u = tab.uri();
        if !u.is_empty() {
            folderprefs::remember(&u, &[("sort", json!([title, desc]))]);
        }
    }

    fn sync_view_buttons(&self) {
        let Some(tab) = self.cur() else { return };
        let id = tab.view_id.borrow().clone();
        if let Some((_, b)) = self.view_buttons.iter().find(|(v, _)| *v == id) {
            if !b.is_active() {
                self.syncing.set(true);
                b.set_active(true);
                self.syncing.set(false);
            }
        }
    }

    /// Columns view: the title (and the tab) follow the deepest open folder.
    fn column_location(&self, id: u64, uri: &str) {
        let Some(tab) = self.tab_by_id(id) else { return };
        tab.name.replace(folder::display_name(uri));
        self.strip.retitle(id, &tab.title());
        if self.is_current(&tab) {
            self.set_titles(&tab.title());
            self.pathbar.set_uri(uri);
        }
    }

    fn matches(&self, info: &gio::FileInfo) -> bool {
        if self.in_results.get() {
            return true; // the search already matched them
        }
        let q = self.search.text().trim().to_lowercase();
        if !q.is_empty() && !info.display_name().to_lowercase().contains(&q) {
            return false;
        }
        self.embed.borrow().as_ref().is_none_or(|e| (e.matches)(info))
    }

    pub fn refilter(&self) {
        if let Some(t) = self.cur() {
            t.filter.changed(gtk::FilterChange::Different);
        }
        self.update_empty();
    }

    fn update_empty(&self) {
        let Some(tab) = self.cur() else { return };
        let empty = tab.filtered.n_items() == 0;
        let searching = !self.search.text().trim().is_empty();
        let busy = self.in_results.get() && self.search_status.label() == tr!("Searching…");
        tab.empty.set_label(&if searching { tr!("No Results") } else { String::new() });
        tab.empty.set_visible(empty && searching && !busy);
    }

    // -- search -----------------------------------------------------------------------------------
    fn open_search(&self) {
        self.search_btn.set_visible(false);
        self.search_rev.set_reveal_child(true);
        self.search.grab_focus();
    }

    /// Filter the folder at once; the recursive search follows after a pause.
    fn search_changed(&self) {
        let q = self.search.text().trim().to_string();
        if let Some(s) = self.search_src.take() {
            s.remove();
        }
        if q.is_empty() {
            self.leave_results();
            return;
        }
        let here = self.cur().map(|t| t.uri()).unwrap_or_default();
        self.scope_here.set_label(&format!("“{}”", folder::display_name(&here)));
        self.scope_here.set_visible(!folder::is_virtual(&here));
        self.scope_rev.set_reveal_child(true);
        if !self.in_results.get() {
            self.refilter();
        }
        let w = self.me.clone();
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis(250), move || {
            if let Some(t) = w.upgrade() {
                t.search_src.replace(None);
                t.run_search();
            }
        });
        self.search_src.replace(Some(id));
    }

    fn run_search(&self) {
        let q = self.search.text().trim().to_string();
        let Some(tab) = self.cur() else { return };
        if q.is_empty() {
            return;
        }
        let here = tab.uri();
        let root_uri = if self.scope_here.is_active() && !folder::is_virtual(&here) { here.clone() } else { gio::File::for_path(glib::home_dir()).uri().to_string() };
        let Some(root) = gio::File::for_uri(&root_uri).path() else { return };
        self.results.remove_all();
        if !self.in_results.get() {
            self.in_results.set(true);
            if *tab.view_id.borrow() == "columns" {
                self.view_before_search.replace(Some("columns".into()));
                self.set_view("list", false);
            }
            tab.hide_others();
            tab.filtered.set_model(Some(&self.results));
            self.refilter();
        }
        self.search_status.set_label(&tr!("Searching…"));
        let scope = if self.scope_here.is_active() { folder::display_name(&here) } else { tr!("This Computer") };
        self.title.set_label(&fmt(&tr!("Searching “{name}”"), &[("name", &scope)]));
        let (w1, w2) = (self.me.clone(), self.me.clone());
        let c = search::Criteria { kind: search::KINDS[self.search_kind.selected() as usize].0.into(), date: search::DATES[self.search_date.selected() as usize].0.into(), contents: self.match_contents.is_active() };
        self.searcher.borrow_mut().start(
            &root.display().to_string(),
            &q,
            move |items| {
                if let Some(t) = w1.upgrade() {
                    t.results.splice(t.results.n_items(), 0, &items);
                    t.update_empty();
                }
            },
            move |truncated| {
                if let Some(t) = w2.upgrade() {
                    let n = t.results.n_items();
                    t.search_status.set_label(&if truncated { fmt(&tr!("{n}+ items"), &[("n", &n.to_string())]) } else { fmt(&trn!("{n} item", "{n} items", n), &[("n", &n.to_string())]) });
                    t.update_empty();
                }
            },
            c,
        );
    }

    fn leave_results(&self) {
        self.searcher.borrow_mut().cancel();
        self.scope_rev.set_reveal_child(false);
        if self.in_results.replace(false) {
            if let Some(tab) = self.cur() {
                tab.filtered.set_model(Some(&tab.folder.store));
                self.results.remove_all();
                self.set_titles(&tab.title());
            }
            if let Some(v) = self.view_before_search.take() {
                self.set_view(&v, false);
            }
        }
        self.refilter();
    }

    fn close_search(&self) {
        self.search.set_text("");
        self.leave_results();
        self.search_rev.set_reveal_child(false);
        self.search_btn.set_visible(true);
        if let Some(v) = self.view() {
            v.focus();
        }
    }

    // -- navigation -------------------------------------------------------------------------------
    pub fn go(&self, uri: &str, record: bool) {
        let uri = folder::canonical(uri);
        let Some(tab) = self.cur() else { return };
        if record {
            let mut h = tab.history.borrow_mut();
            let p = tab.pos.get();
            if p >= 0 && h.get(p as usize) == Some(&uri) {
                return;
            }
            h.truncate((p + 1).max(0) as usize);
            h.push(uri.clone());
            tab.pos.set(h.len() as isize - 1);
        }
        if !self.search.text().is_empty() {
            self.close_search();
        }
        self.apply_folder_prefs(&tab, &uri);
        tab.folder.load(&uri);
        self.update_nav();
    }

    fn go_back(&self) {
        let Some(tab) = self.cur() else { return };
        if tab.pos.get() > 0 {
            tab.pos.set(tab.pos.get() - 1);
            self.go(&tab.uri(), false);
        }
    }

    fn go_forward(&self) {
        let Some(tab) = self.cur() else { return };
        if (tab.pos.get() as usize) + 1 < tab.history.borrow().len() {
            tab.pos.set(tab.pos.get() + 1);
            self.go(&tab.uri(), false);
        }
    }

    fn go_up(&self) {
        let Some(tab) = self.cur() else { return };
        let u = tab.uri();
        if u.is_empty() || folder::is_virtual(&u) {
            return;
        }
        if let Some(p) = gio::File::for_uri(&u).parent() {
            self.go(&p.uri(), true);
        }
    }

    fn update_nav(&self) {
        let Some(tab) = self.cur() else { return };
        self.back.set_sensitive(tab.pos.get() > 0);
        self.fwd.set_sensitive(((tab.pos.get() + 1) as usize) < tab.history.borrow().len());
    }

    // -- opening ----------------------------------------------------------------------------------
    pub fn open_item(&self, info: &gio::FileInfo) {
        if self.embed.borrow().as_ref().is_some_and(|e| (e.open)(info)) {
            return;
        }
        self.open_folder_or_file(info);
    }

    /// Folders open here; files in their app.
    pub fn open_folder_or_file(&self, info: &gio::FileInfo) {
        let Some(f) = file_of(info) else { return };
        let target = info.attribute_string("standard::target-uri").map(|s| s.to_string()).filter(|s| !s.is_empty());
        if is_dir(info) {
            self.go(&target.unwrap_or_else(|| f.uri().to_string()), true);
            return;
        }
        let ctx = WidgetExt::display(&self.window).app_launch_context();
        if info.name().to_string_lossy().ends_with(".desktop") {
            // an app (Applications, shortcuts)
            if let Some(app) = f.path().and_then(gio::DesktopAppInfo::from_filename) {
                let _ = app.launch(&[], Some(&ctx));
                return;
            }
        }
        let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
        let app = if ct.is_empty() || target.is_some() { None } else { gio::AppInfo::default_for_type(&ct, false) };
        // macOS: double-click opens the file in its app; Quick Look only
        // when no app opens it
        if app.is_none() && target.is_none() && crate::quicklook::previewable(info) {
            self.quick_look_item(info);
            return;
        }
        if let Some(app) = app {
            if let Err(e) = app.launch(&[f.clone()], Some(&ctx)) {
                self.error(&fmt(&tr!("There is no application set to open “{name}”."), &[("name", &info.display_name())]), &e);
            }
            return;
        }
        let (w, name, uri) = (self.me.clone(), info.display_name().to_string(), target.unwrap_or_else(|| f.uri().to_string()));
        gio::AppInfo::launch_default_for_uri_async(&uri, Some(&ctx), gio::Cancellable::NONE, move |res| {
            if let (Err(e), Some(t)) = (res, w.upgrade()) {
                t.error(&fmt(&tr!("There is no application set to open “{name}”."), &[("name", &name)]), &e);
            }
        });
    }

    fn open_selection(&self) {
        for i in self.view().map(|v| v.selected()).unwrap_or_default() {
            self.open_item(&i);
        }
    }

    // -- file actions -----------------------------------------------------------------------------
    /// The folder new items / pasted items go to.
    pub fn location(&self) -> String {
        let Some(tab) = self.cur() else { return String::new() };
        if *tab.view_id.borrow() == "columns" {
            if let Some(l) = tab.columns.location() {
                return l;
            }
        }
        tab.uri()
    }

    fn writable_here(&self) -> bool {
        let l = self.location();
        !folder::is_virtual(&l) && !ops::is_trash(&l)
    }

    fn in_trash(&self) -> bool {
        self.cur().is_some_and(|t| ops::is_trash(&t.uri()))
    }

    fn reload_all(&self) {
        for t in self.tabs.borrow().iter() {
            t.folder.reload();
        }
    }

    pub fn empty_trash(&self) {
        let w = self.me.clone();
        sonata_ui::dialog::alert(
            &tr!("Are you sure you want to permanently erase the items in the Trash?"),
            &tr!("You can’t undo this action."),
            &[("cancel", tr!("Cancel"), ""), ("empty", tr!("Empty Trash"), "destructive")],
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                if rid != "empty" {
                    return;
                }
                let (w1, w2) = (t.me.clone(), t.me.clone());
                ops::empty_trash(
                    Some(Box::new(move || {
                        if let Some(t) = w1.upgrade() {
                            t.reload_all();
                        }
                    })),
                    Some(Rc::new(move |_f, e| {
                        if let Some(t) = w2.upgrade() {
                            t.error(&tr!("The Trash can’t be emptied."), e);
                        }
                    })),
                );
            },
            Some(self.window.upcast_ref()),
        );
    }

    fn selected_files(&self) -> Vec<gio::File> {
        self.view().map(|v| v.selected().iter().filter_map(file_of).collect()).unwrap_or_default()
    }

    fn delete_selection_now(&self) {
        let files = self.selected_files();
        if files.is_empty() {
            return;
        }
        let heading = if files.len() == 1 { fmt(&tr!("Are you sure you want to delete “{name}” immediately?"), &[("name", &ops::base(&files[0]))]) } else { fmt(&tr!("Are you sure you want to delete these {n} items immediately?"), &[("n", &files.len().to_string())]) };
        let w = self.me.clone();
        sonata_ui::dialog::alert(&heading, &tr!("You can’t undo this action."), &[("cancel", tr!("Cancel"), ""), ("delete", tr!("Delete"), "destructive")], move |rid| {
            if rid != "delete" {
                return;
            }
            let w = w.clone();
            ops::delete_now(files.clone(), None, Some(Rc::new(move |f, e| {
                if let Some(t) = w.upgrade() {
                    t.error(&fmt(&tr!("“{name}” can’t be deleted."), &[("name", &ops::base(f))]), e);
                }
            })));
        }, Some(self.window.upcast_ref()));
    }

    fn put_back_selection(&self) {
        let w = self.me.clone();
        ops::put_back(self.selected_files(), Some(Rc::new(move |f, e| {
            if let Some(t) = w.upgrade() {
                t.error(&fmt(&tr!("“{name}” can’t be put back."), &[("name", &ops::base(f))]), e);
            }
        })), None);
    }

    fn report(&self, heading: &'static str) -> Option<Rc<dyn Fn(&gio::File, &glib::Error)>> {
        let w = self.me.clone();
        Some(Rc::new(move |f, e| {
            if let Some(t) = w.upgrade() {
                t.error(&fmt(&sonata_i18n::gettext(heading), &[("name", &ops::base(f))]), e);
            }
        }))
    }

    fn trash_selection(&self) {
        let files = self.selected_files();
        if files.is_empty() {
            return;
        }
        ops::trash(files, self.report(n_!("“{name}” can’t be moved to the Trash.")), Some(Box::new(|done| undo::trashed(&done))));
        let w = self.me.clone();
        glib::timeout_add_local_once(std::time::Duration::from_millis(600), move || {
            if let Some(t) = w.upgrade() {
                t.sidebar.refresh_space();
            }
        });
    }

    fn duplicate_selection(&self) {
        let files = self.selected_files();
        if files.is_empty() || !self.writable_here() {
            return;
        }
        // duplicates go next to each original
        let mut groups: Vec<(gio::File, Vec<gio::File>)> = Vec::new();
        for f in files {
            let Some(p) = f.parent() else { continue };
            match groups.iter_mut().find(|(g, _)| g.equal(&p)) {
                Some((_, v)) => v.push(f),
                None => groups.push((p, vec![f])),
            }
        }
        for (parent, fs) in groups {
            ops::transfer(fs, &parent, false, Some(self.window.clone().upcast()), self.refresh_after(), true);
        }
    }

    fn refresh_after(&self) -> Option<Box<dyn FnOnce()>> {
        let w = self.me.clone();
        Some(Box::new(move || {
            if let Some(t) = w.upgrade() {
                t.sidebar.refresh_space();
            }
        }))
    }

    fn compress_selection(&self) {
        let files = self.selected_files();
        if files.is_empty() || !self.writable_here() {
            return;
        }
        let (w1, w2) = (self.me.clone(), self.me.clone());
        actions::compress(&files, Some(Box::new(move |z| {
            if let Some(t) = w1.upgrade() {
                t.select_when_listed(&ops::base(&z), false, 40);
            }
        })), Some(Rc::new(move |_f, e| {
            if let Some(t) = w2.upgrade() {
                t.error(&tr!("The items can’t be compressed."), e);
            }
        })));
    }

    fn copy_selection(&self, cut: bool) {
        let files = self.selected_files();
        if !files.is_empty() {
            ops::copy_to_clipboard(&self.window, &files, cut);
        }
    }

    fn paste(&self, mv: bool) {
        if !self.writable_here() {
            return;
        }
        let dest = gio::File::for_uri(&self.location());
        let w = self.me.clone();
        ops::read_clipboard(&self.window, move |files, cut| {
            let Some(t) = w.upgrade() else { return };
            if !files.is_empty() {
                ops::transfer(files, &dest, mv || cut, Some(t.window.clone().upcast()), t.refresh_after(), false);
                if cut {
                    let _ = t.window.clipboard().set_content(None::<&gdk::ContentProvider>); // a cut is pasted once
                }
            } else {
                // a copied picture becomes a file
                let w = t.me.clone();
                ops::paste_image(&t.window, &dest, None, Some(Box::new(move |e| {
                    if let Some(t) = w.upgrade() {
                        t.error(&tr!("The picture can’t be pasted."), &e);
                    }
                })));
            }
        });
    }

    /// Add / remove a colour tag; a folder showing that tag lists them again.
    pub fn tag_files(&self, paths: &[std::path::PathBuf], tag: &str, on: bool) {
        let failed = tags::toggle(paths, tag, on);
        if let Some((p, e)) = failed.first() {
            let name = p.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
            self.alert(&fmt(&tr!("“{name}” can’t be tagged."), &[("name", &name)]), &if e.raw_os_error().is_some() { e.to_string() } else { tr!("The disk it’s on can’t keep tags.") });
        }
        for t in self.tabs.borrow().iter() {
            if t.folder.uri.borrow().as_deref().is_some_and(|u| !tags::tag_of(u).is_empty()) {
                t.folder.reload();
            }
        }
    }

    pub fn new_folder(&self) {
        if !self.writable_here() {
            return;
        }
        let (w1, w2) = (self.me.clone(), self.me.clone());
        ops::new_folder(&gio::File::for_uri(&self.location()), move |f| {
            undo::new_folder(f.clone());
            if let Some(t) = w1.upgrade() {
                t.select_when_listed(&ops::base(&f), true, 40);
            }
        }, move |e| {
            if let Some(t) = w2.upgrade() {
                t.error(&tr!("The folder can’t be created."), &e);
            }
        }, None);
    }

    /// Select (and maybe rename) `name` once the live listing shows it.
    /// A file opened from outside: its folder, with it selected.
    pub fn reveal(&self, name: &str) {
        self.select_when_listed(name, false, 40);
    }

    fn select_when_listed(&self, name: &str, rename: bool, tries: u32) {
        let Some(v) = self.view() else { return };
        if v.select_name(name) {
            if rename {
                let m = v.model();
                let info = (0..m.n_items()).filter_map(|i| m.item(i).and_downcast::<gio::FileInfo>()).find(|i| i.name().to_string_lossy() == name);
                if let Some(info) = info {
                    let (w, n) = (self.me.clone(), info);
                    glib::timeout_add_local_once(std::time::Duration::from_millis(60), move || {
                        if let Some(t) = w.upgrade() {
                            if let Some(v) = t.view() {
                                v.begin_rename(&n, t.commit_rename());
                            }
                        }
                    });
                }
            }
            return;
        }
        if tries > 0 {
            let (w, n) = (self.me.clone(), name.to_string());
            glib::timeout_add_local_once(std::time::Duration::from_millis(50), move || {
                if let Some(t) = w.upgrade() {
                    t.select_when_listed(&n, rename, tries - 1);
                }
            });
        }
    }

    fn rename_selection(&self) {
        let Some(v) = self.view() else { return };
        let sel = v.selected();
        if sel.len() == 1 {
            v.begin_rename(&sel[0], self.commit_rename());
        } else if sel.len() > 1 {
            crate::batchrename::dialog(&self.window, sel.iter().filter_map(file_of).collect());
        }
    }

    fn commit_rename(&self) -> OnCommit {
        let w = self.me.clone();
        Rc::new(move |info, new_name| {
            let Some(t) = w.upgrade() else { return };
            if new_name.contains('/') {
                t.alert(&fmt(&tr!("The name “{name}” can’t be used."), &[("name", new_name)]), &tr!("Try using a name with fewer characters, or with no punctuation marks."));
                return;
            }
            let Some(f) = file_of(info) else { return };
            let old = ops::base(&f);
            let (w1, w2, nn) = (t.me.clone(), t.me.clone(), new_name.to_string());
            ops::rename(&f, new_name, move |nf| {
                undo::renamed(&nf, &old);
                if let Some(p) = nf.parent() {
                    tags::moved_files(&[(p.child(&old), nf.clone())]);
                }
                if let Some(t) = w1.upgrade() {
                    t.select_when_listed(&ops::base(&nf), false, 40);
                }
            }, move |e| {
                if let Some(t) = w2.upgrade() {
                    t.error(&fmt(&tr!("The name “{name}” can’t be used."), &[("name", &nn)]), &e);
                }
            });
        })
    }

    /// The toolbar's folder name becomes a field (Return renames the folder
    /// you're in, Escape keeps it). Not for places (Recents, the Trash,
    /// your home, the disk's top).
    fn rename_folder(&self) {
        let Some(tab) = self.cur() else { return };
        if self.in_results.get() || *tab.view_id.borrow() == "columns" || self.title_entry.borrow().is_some() {
            return;
        }
        let uri = tab.uri();
        if folder::is_virtual(&uri) || ops::is_trash(&uri) {
            return;
        }
        let f = gio::File::for_uri(&uri);
        let Some(path) = f.path() else { return };
        if path == std::path::Path::new("/") || path == glib::home_dir() || f.parent().is_none() {
            return;
        }
        if f.query_info("access::can-rename", gio::FileQueryInfoFlags::NONE, gio::Cancellable::NONE).is_ok_and(|i| i.has_attribute("access::can-rename") && !i.boolean("access::can-rename")) {
            return;
        }
        let old = ops::base(&f);
        let entry = gtk::Entry::builder().text(&old).css_classes(["fs-rename", "fs-title-rename"]).valign(gtk::Align::Center).width_chars((old.chars().count() as i32 + 2).clamp(8, 32)).build();
        self.title_entry.replace(Some(entry.clone()));
        self.title.set_visible(false);
        if let Some(p) = self.title.parent().and_downcast::<gtk::Box>() {
            p.insert_child_after(&entry, Some(&self.title));
        }
        let done = Rc::new(Cell::new(false));
        let finish = {
            let (w, e, done) = (self.me.clone(), entry.downgrade(), done.clone());
            Rc::new(move |commit: bool| {
                if done.replace(true) {
                    return;
                }
                let (Some(t), Some(entry)) = (w.upgrade(), e.upgrade()) else { return };
                let new = entry.text().trim().to_string();
                t.title_entry.replace(None);
                if let Some(p) = entry.parent().and_downcast::<gtk::Box>() {
                    p.remove(&entry);
                }
                t.title.set_visible(true);
                if !commit || new.is_empty() || new == old {
                    return;
                }
                if new.contains('/') {
                    t.alert(&fmt(&tr!("The name “{name}” can’t be used."), &[("name", &new)]), &tr!("Try using a name with fewer characters, or with no punctuation marks."));
                    return;
                }
                let (w1, w2, f2, old2, nn) = (t.me.clone(), t.me.clone(), f.clone(), old.clone(), new.clone());
                ops::rename(&f, &new, move |nf| {
                    undo::renamed(&nf, &old2);
                    tags::moved_files(&[(f2.clone(), nf.clone())]);
                    folderprefs::moved(&f2.uri(), &nf.uri());
                    crate::sidebar::moved(&f2.uri(), &nf.uri());
                    if let Some(t) = w1.upgrade() {
                        t.folder_moved(&f2.uri(), &nf.uri());
                    }
                }, move |e| {
                    if let Some(t) = w2.upgrade() {
                        t.error(&fmt(&tr!("The name “{name}” can’t be used."), &[("name", &nn)]), &e);
                    }
                });
            })
        };
        let f1 = finish.clone();
        entry.connect_activate(move |_| f1(true));
        let keys = gtk::EventControllerKey::new();
        let f2 = finish.clone();
        keys.connect_key_pressed(move |_, k, _, _| {
            if k == gdk::Key::Escape {
                f2(false);
                return glib::Propagation::Stop;
            }
            glib::Propagation::Proceed
        });
        entry.add_controller(keys);
        let focus = gtk::EventControllerFocus::new();
        focus.connect_leave(move |_| {
            let f = finish.clone();
            glib::idle_add_local_once(move || f(true));
        });
        entry.add_controller(focus);
        entry.grab_focus();
        entry.select_region(0, -1);
    }

    /// A folder you're in (or inside) got a new name: every tab's history
    /// follows, and the window shows it under its new name.
    fn folder_moved(&self, old: &str, new: &str) {
        let o = old.trim_end_matches('/');
        let moved = |u: &String| -> String {
            if u == old {
                new.to_string()
            } else if let Some(rest) = u.strip_prefix(&format!("{o}/")) {
                format!("{}/{rest}", new.trim_end_matches('/'))
            } else {
                u.clone()
            }
        };
        for t in self.tabs.borrow().iter() {
            let h: Vec<String> = t.history.borrow().iter().map(moved).collect();
            t.history.replace(h);
        }
        if let Some(t) = self.cur() {
            self.go(&t.uri(), false);
        }
    }

    // -- context menus ----------------------------------------------------------------------------
    fn context_menu(self: &Rc<Self>, view: &Rc<dyn View>, widget: &gtk::Widget, x: f64, y: f64) {
        use sonata_ui::menu::Item;
        let me = self.me.clone();
        let act = |f: fn(&Rc<FilesWindow>)| {
            let w = me.clone();
            move || {
                if let Some(t) = w.upgrade() {
                    f(&t);
                }
            }
        };
        let sections: Vec<Vec<Item>> = match view.info_at(x, y) {
            Some(info) => {
                view.ensure_selected(&info);
                let sel = { let s = view.selected(); if s.is_empty() { vec![info.clone()] } else { s } };
                let n = sel.len();
                let what = if n == 1 { format!("“{}”", folder::short_name(&sel[0].display_name(), folder::MENU_NAME_MAX)) } else { fmt(&trn!("{n} Item", "{n} Items", n), &[("n", &n.to_string())]) };
                if self.in_trash() {
                    // Finder's Trash menu
                    vec![
                        vec![Item::new(&tr!("Put Back"), act(|t| t.put_back_selection()))],
                        vec![Item::new(&tr!("Delete Immediately…"), act(|t| t.delete_selection_now()))],
                        vec![Item::new(&tr!("Get Info"), act(|t| t.get_info())), Item::new(&fmt(&tr!("Quick Look {what}"), &[("what", &what)]), act(|t| t.toggle_quicklook()))],
                        vec![Item::new(&tr!("Empty Trash"), act(|t| t.empty_trash()))],
                    ]
                } else {
                    let writable = sel.iter().all(|i| !i.has_attribute("access::can-write") || i.boolean("access::can-write"));
                    let here = self.writable_here();
                    let mut first = vec![Item::new(&tr!("Open"), act(|t| t.open_selection()))];
                    // an AppImage: Install, ahead of Open (packages.py's menu_items;
                    // its Run isn't ported yet)
                    if let Some(path) = sel.first().filter(|i| n == 1 && !is_dir(i)).and_then(file_of).and_then(|f| f.path()).filter(|p| crate::appimage::is_appimage(p)) {
                        let w = me.clone();
                        first.insert(0, Item::new(&tr!("Install"), move || {
                            if let Some(t) = w.upgrade() {
                                crate::appimage::install(&path, Some(t.window.upcast_ref()));
                            }
                        }));
                    }
                    if n == 1 && is_dir(&sel[0]) {
                        let (w, i) = (me.clone(), sel[0].clone());
                        first.push(Item::new(&tr!("Open in New Tab"), move || {
                            if let Some(t) = w.upgrade() {
                                t.open_in_new_tab(&i, true);
                            }
                        }));
                    }
                    if n == 1 && !is_dir(&sel[0]) {
                        let apps = self.open_with_items(&sel[0]);
                        if !apps.is_empty() {
                            first.push(Item::submenu(&tr!("Open With"), vec![apps]));
                        }
                        let ct = sel[0].content_type().map(|c| c.to_string()).unwrap_or_default();
                        if ct.starts_with("image/") {
                            // Finder's Quick Action
                            let f = file_of(&sel[0]);
                            first.push(Item::new(&tr!("Set Desktop Picture"), move || {
                                if let Some(f) = &f {
                                    let _ = sonata_config::set_wallpaper(&f.uri());
                                }
                            }));
                        }
                    }
                    let mut s = vec![first];
                    s.push(vec![Item::new(&tr!("Move to Trash"), act(|t| t.trash_selection())).enabled(writable)]);
                    s.push(vec![Item::new(&tr!("Get Info"), act(|t| t.get_info())), Item::new(&fmt(&tr!("Quick Look {what}"), &[("what", &what)]), act(|t| t.toggle_quicklook()))]);
                    s.push(vec![
                        Item::new(&if n == 1 { tr!("Rename") } else { fmt(&tr!("Rename {n} Items…"), &[("n", &n.to_string())]) }, act(|t| t.rename_selection())).enabled(writable),
                        Item::new(&fmt(&tr!("Compress {what}"), &[("what", &what)]), act(|t| t.compress_selection())).enabled(here),
                        Item::new(&tr!("Duplicate"), act(|t| t.duplicate_selection())).enabled(here),
                    ]);
                    s.push(vec![
                        Item::new(&tr!("Create Shortcut"), act(|t| t.shortcut_selection(false))).enabled(here),
                        Item::new(&tr!("Create Shortcut on Desktop"), act(|t| t.shortcut_selection(true))),
                    ]);
                    s.push(vec![Item::new(&fmt(&tr!("Copy {what}"), &[("what", &what)]), act(|t| t.copy_selection(false))), Item::new(&if n == 1 { tr!("Copy Path") } else { tr!("Copy Paths") }, act(|t| actions::copy_paths(&t.window, &t.selected_files())))]);
                    let pics: Vec<(gio::File, String)> = sel.iter().filter_map(|i| Some((file_of(i)?, actions::picture_kind(&i.content_type().map(|c| c.to_string()).unwrap_or_default()).to_string()))).collect();
                    if pics.len() == n && pics.iter().all(|(_, k)| !k.is_empty()) && writable {
                        let (w1, w2, w3, w4) = (me.clone(), me.clone(), me.clone(), me.clone());
                        let (p1, p2, p3, p4) = (pics.clone(), pics.clone(), pics.clone(), pics);
                        s.push(vec![Item::submenu(&tr!("Quick Actions"), vec![
                            vec![Item::new(&tr!("Rotate Left"), move || if let Some(t) = w1.upgrade() { t.rotate(p1.clone(), false) }), Item::new(&tr!("Rotate Right"), move || if let Some(t) = w2.upgrade() { t.rotate(p2.clone(), true) })],
                            vec![Item::new(&tr!("Convert to PNG"), move || if let Some(t) = w3.upgrade() { t.convert(p3.clone(), "png") }), Item::new(&tr!("Convert to JPEG"), move || if let Some(t) = w4.upgrade() { t.convert(p4.clone(), "jpeg") })],
                        ])]);
                    }
                    let in_virtual = self.cur().is_some_and(|t| folder::is_virtual(&t.uri()));
                    if in_virtual || !self.search.text().is_empty() {
                        let (w, i) = (me.clone(), sel[0].clone());
                        s.push(vec![Item::new(&tr!("Show in Enclosing Folder"), move || {
                            if let (Some(t), Some(f)) = (w.upgrade(), file_of(&i)) {
                                if let Some(p) = f.parent() {
                                    t.go(&p.uri(), true);
                                    t.select_when_listed(&ops::base(&f), false, 40);
                                }
                            }
                        })]);
                    }
                    if n == 1 && is_dir(&sel[0]) {
                        let (w, f) = (me.clone(), file_of(&sel[0]));
                        s.push(vec![Item::new(&tr!("New Terminal at Folder"), move || {
                            if let (Some(t), Some(f)) = (w.upgrade(), f.as_ref()) {
                                t.terminal(f);
                            }
                        })]);
                    }
                    s
                }
            }
            None => {
                view.unselect_all();
                let here = self.writable_here();
                let tab = self.cur();
                let vid = tab.as_ref().map(|t| t.view_id.borrow().clone()).unwrap_or_default();
                let mut views: Vec<Item> = VIEWS.iter().map(|(v, _, label)| {
                    let w = me.clone();
                    Item::check(&sonata_i18n::gettext(label), vid == *v, move |_| if let Some(t) = w.upgrade() { t.set_view(v, true) })
                }).collect();
                let path_label = if self.pathbar.root.is_visible() { tr!("Hide Path Bar") } else { tr!("Show Path Bar") };
                let hidden_label = if tab.as_ref().is_some_and(|t| t.folder.show_hidden.get()) { tr!("Hide Hidden Files") } else { tr!("Show Hidden Files") };
                let mut view_sections = vec![std::mem::take(&mut views), vec![Item::new(&path_label, act(|t| t.toggle_path_bar())), Item::new(&hidden_label, act(|t| t.toggle_hidden()))]];
                if vid != "columns" {
                    let cur = tab.as_ref().map(|t| t.view().sort_state().0).unwrap_or_default();
                    view_sections.push(vec![Item::submenu(&tr!("Sort By"), vec![SORT_BY.iter().map(|(t, _)| {
                        let w = me.clone();
                        Item::check(&sonata_i18n::gettext(t), cur == *t, move |_| if let Some(win) = w.upgrade() { win.sort_by(t) })
                    }).collect()])]);
                }
                if vid == "list" {
                    if let Some(tab) = &tab {
                        let shown = tab.list.shown_columns();
                        view_sections.push(vec![
                            Item::submenu(&tr!("Show Columns"), vec![crate::views::LIST_COLUMNS.iter().map(|(t, _, _)| {
                                let w = me.clone();
                                Item::check(&sonata_i18n::gettext(t), shown.iter().any(|s| s == t), move |on| if let Some(win) = w.upgrade() { win.show_column(t, on) })
                            }).collect()]),
                            {
                                let w = me.clone();
                                Item::check(&tr!("Calculate All Sizes"), tab.list.count_sizes.get(), move |on| if let Some(win) = w.upgrade() { win.set_count_sizes(on) })
                            },
                        ]);
                    }
                }
                let mut s = vec![
                    vec![Item::new(&tr!("New Folder"), act(|t| t.new_folder())).enabled(here)],
                    vec![Item::new(&tr!("Paste"), act(|t| t.paste(false))).enabled(here && ops::clipboard_has_files(&self.window))],
                    vec![Item::submenu(&tr!("View"), view_sections)],
                ];
                if self.in_trash() {
                    s.insert(0, vec![Item::new(&tr!("Empty Trash"), act(|t| t.empty_trash()))]);
                }
                if here {
                    s.push(vec![Item::new(&tr!("New Terminal at Folder"), act(|t| t.terminal(&gio::File::for_uri(&t.location()))))]);
                }
                s
            }
        };
        // the tag colours, as a row under the menu (Finder)
        let sel = view.selected();
        let paths: Vec<std::path::PathBuf> = sel.iter().filter_map(|i| file_of(i).and_then(|f| f.path())).collect();
        let pop = sonata_ui::menu::popup(widget, sections, sonata_ui::menu::Options::at(x, y));
        if !sel.is_empty() && paths.len() == sel.len() && !self.in_trash() {
            let states: HashMap<String, &'static str> = tags::COLORS.iter().map(|(n, _)| (n.to_string(), tags::state(&paths, n))).collect();
            let w = me.clone();
            let row = crate::views::tags_menu_row(&states, Rc::new(move |t, on| {
                if let Some(win) = w.upgrade() {
                    win.tag_files(&paths, t, on);
                }
            }));
            pop.add_child(&row, "tags");
        }
    }

    fn open_with_items(&self, info: &gio::FileInfo) -> Vec<sonata_ui::menu::Item> {
        let ct = info.content_type().map(|c| c.to_string()).unwrap_or_default();
        if ct.is_empty() {
            return Vec::new();
        }
        let default = gio::AppInfo::default_for_type(&ct, false);
        gio::AppInfo::all_for_type(&ct)
            .into_iter()
            .map(|app| {
                let name = app.display_name().to_string();
                let label = if default.as_ref().is_some_and(|d| d.equal(&app)) { fmt(&tr!("{app} (default)"), &[("app", &name)]) } else { name };
                let (w, f) = (self.me.clone(), file_of(info));
                sonata_ui::menu::Item::new(&label, move || {
                    if let (Some(t), Some(f)) = (w.upgrade(), f.as_ref()) {
                        let ctx = WidgetExt::display(&t.window).app_launch_context();
                        let _ = app.launch(&[f.clone()], Some(&ctx));
                    }
                })
            })
            .collect()
    }

    fn shortcut_selection(&self, desktop: bool) {
        let dest = if desktop { glib::user_special_dir(glib::UserDirectory::Desktop).map(gio::File::for_path) } else { self.cur().map(|t| gio::File::for_uri(&t.uri())) };
        if let Some(d) = dest {
            ops::make_shortcuts(&self.selected_files(), &d);
        }
    }

    /// New Terminal at Folder: Sonata's own Terminal first, then $TERMINAL
    /// and the common ones.
    fn terminal(&self, f: &gio::File) {
        let Some(path) = f.path() else { return };
        let p = path.display().to_string();
        let mut cmds: Vec<Vec<String>> = Vec::new();
        if let Ok(me) = std::env::current_exe() {
            cmds.push(vec![me.display().to_string(), "terminal".into(), p.clone()]);
        }
        let flags = |name: &str| -> Vec<String> {
            match name {
                "kgx" | "gnome-terminal" | "foot" | "ghostty" | "xfce4-terminal" | "tilix" | "terminator" => vec![format!("--working-directory={p}")],
                "konsole" => vec!["--workdir".into(), p.clone()],
                "kitty" => vec!["--directory".into(), p.clone()],
                "alacritty" => vec!["--working-directory".into(), p.clone()],
                "wezterm" => vec!["start".into(), "--cwd".into(), p.clone()],
                _ => Vec::new(),
            }
        };
        let env = std::env::var("TERMINAL").unwrap_or_default();
        let mut names: Vec<String> = if env.trim().is_empty() { Vec::new() } else { vec![env.trim().to_string()] };
        names.extend(["kgx", "gnome-terminal", "konsole", "kitty", "alacritty", "foot", "ghostty", "wezterm", "xfce4-terminal", "tilix", "terminator", "x-terminal-emulator", "xterm"].iter().map(|s| s.to_string()));
        for n in names {
            if let Some(exe) = glib::find_program_in_path(&n) {
                let base = std::path::Path::new(&n).file_name().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
                cmds.push([vec![exe.display().to_string()], flags(&base)].concat());
            }
        }
        for argv in cmds {
            use std::os::unix::process::CommandExt;
            if std::process::Command::new(&argv[0]).args(&argv[1..]).current_dir(&path).process_group(0).spawn().is_ok() {
                return;
            }
        }
        self.alert(&tr!("No terminal app was found."), &tr!("Install a terminal app, like kitty or Konsole, to open folders in it."));
    }

    fn rotate(&self, pics: Vec<(gio::File, String)>, clockwise: bool) {
        let w = self.me.clone();
        actions::rotate(pics, clockwise, Some(Box::new(move || {
            if let Some(t) = w.upgrade() {
                t.reload_all();
            }
        })), self.report(n_!("“{name}” can’t be rotated.")));
    }

    fn convert(&self, pics: Vec<(gio::File, String)>, kind: &'static str) {
        actions::convert(pics.into_iter().map(|p| p.0).collect(), kind, None, self.report(n_!("“{name}” can’t be converted.")));
    }

    fn show_column(&self, title: &str, on: bool) {
        let Some(tab) = self.cur() else { return };
        let mut shown: Vec<String> = tab.list.shown_columns().into_iter().filter(|t| t != title).collect();
        if on {
            shown.push(title.into());
        }
        for t in self.tabs.borrow().iter() {
            t.list.show_columns(Some(&shown));
        }
        let _ = sonata_config::update("files", &json!({"list_shown": shown}));
    }

    fn set_count_sizes(&self, on: bool) {
        for t in self.tabs.borrow().iter() {
            t.list.count_sizes.set(on);
            if on {
                t.folder.reload(); // rows bound again: their sizes counted
            }
        }
        let _ = sonata_config::update("files", &json!({"count_sizes": on}));
    }

    fn toggle_path_bar(&self) {
        let on = !self.pathbar.root.is_visible();
        self.pathbar.root.set_visible(on);
        let _ = sonata_config::update("files", &json!({"path_bar": on}));
    }

    /// The toolbar's eye, View > Show / Hide Hidden Files, Ctrl+Shift+.:
    /// this folder's hidden files, remembered for it.
    fn toggle_hidden(&self) {
        let Some(tab) = self.cur() else { return };
        let on = !tab.folder.show_hidden.get();
        tab.folder.show_hidden.set(on);
        let u = tab.uri();
        if !u.is_empty() && !folder::is_virtual(&u) {
            folderprefs::remember(&u, &[("hidden", json!(on))]);
        }
        self.sync_hidden_button();
        tab.folder.reload();
    }

    fn sync_hidden_button(&self) {
        let on = self.cur().is_some_and(|t| t.folder.show_hidden.get());
        self.hidden_btn.set_tooltip_text(Some(&if on { tr!("Hide Hidden Files") } else { tr!("Show Hidden Files") }));
        self.hidden_btn.set_icon_name(if on { "view-conceal-symbolic" } else { "view-reveal-symbolic" });
        if self.hidden_btn.is_active() != on {
            self.syncing.set(true);
            self.hidden_btn.set_active(on);
            self.syncing.set(false);
        }
    }

    // -- Go to Folder / the address -----------------------------------------------------------------
    fn typed_location(&self) -> (String, String) {
        let here = self.location();
        let start = if folder::is_virtual(&here) { String::new() } else { gio::File::for_uri(&here).path().map(|p| p.display().to_string()).unwrap_or_default() };
        (here, start)
    }

    fn go_typed(&self, text: &str, here: &str) {
        match crate::pathbar::resolve(text, Some(here)) {
            None => self.alert(&tr!("The folder can’t be found."), &fmt(&tr!("“{name}” doesn’t exist."), &[("name", text)])),
            Some((uri, name)) => {
                self.go(&uri, true);
                if let Some(n) = name {
                    self.select_when_listed(&n, false, 40);
                }
            }
        }
    }

    /// Finder's Go to Folder (Ctrl+Shift+G).
    fn go_to_folder(&self) {
        let (here, start) = self.typed_location();
        let w = self.me.clone();
        sonata_ui::dialog::ask_text(&tr!("Go to Folder"), &start, &tr!("Go"), move |t| {
            if let Some(win) = w.upgrade() {
                win.go_typed(&t, &here);
            }
        }, "", Some(self.window.upcast_ref()));
    }

    /// Finder's Go > Connect to Server (Ctrl+K): server.rs; opened here once connected.
    pub fn connect_to_server(&self) {
        let w = self.me.clone();
        crate::server::dialog(self.window.upcast_ref(), move |uri| {
            if let Some(t) = w.upgrade() {
                t.go(uri, true);
            }
        });
    }

    /// Ctrl+L, or a click on the path bar's empty part: type the address in
    /// the path bar; the Go to Folder dialog when the path bar is hidden.
    fn edit_address(&self) {
        if !self.pathbar.root.is_visible() {
            self.go_to_folder();
            return;
        }
        let (here, start) = self.typed_location();
        let w = self.me.clone();
        self.pathbar.edit(&start, move |t| {
            if let Some(win) = w.upgrade() {
                win.go_typed(t, &here);
            }
        });
    }

    fn home_dir(&self, kind: Option<glib::UserDirectory>) {
        match kind {
            None => self.go(&gio::File::for_path(glib::home_dir()).uri(), true),
            Some(k) => {
                let u = folder::xdg_uri(k);
                if !u.is_empty() {
                    self.go(&u, true);
                }
            }
        }
    }

    // -- Quick Look and Get Info ------------------------------------------------------------------
    fn toggle_quicklook(&self) {
        if let Some(q) = self.ql.take() {
            q.close();
            return;
        }
        let sel = self.view().map(|v| v.selected()).unwrap_or_default();
        if let Some(i) = sel.first() {
            self.quick_look_item(i);
        }
    }

    fn quick_look_item(&self, info: &gio::FileInfo) {
        if self.ql.borrow().is_none() {
            let w = self.me.clone();
            let q = crate::quicklook::QuickLook::new(&self.window, Box::new(move || {
                if let Some(t) = w.upgrade() {
                    t.ql.replace(None);
                }
            }));
            self.ql.replace(Some(q));
        }
        if let Some(q) = self.ql.borrow().as_ref() {
            q.show_item(info);
        }
    }

    fn follow_quicklook(&self) {
        let Some(q) = self.ql.borrow().clone() else { return };
        if let Some(i) = self.view().and_then(|v| v.selected().into_iter().next()) {
            if q.info().as_ref() != Some(&i) {
                q.show_item(&i);
            }
        }
    }

    fn get_info(&self) {
        for info in self.view().map(|v| v.selected()).unwrap_or_default().iter().take(10) {
            crate::quicklook::get_info(&self.window, info);
        }
    }

    // -- undo -------------------------------------------------------------------------------------
    fn editing(&self) -> bool {
        gtk::prelude::GtkWindowExt::focus(&self.window).is_some_and(|f| f.is::<gtk::Text>() || f.ancestor(gtk::Entry::static_type()).is_some())
    }

    fn run_history(&self, redo: bool) -> bool {
        if self.editing() {
            return false; // the text field's own undo
        }
        let action = undo::HISTORY.with(|h| if redo { h.borrow_mut().take_redo() } else { h.borrow_mut().take_undo() });
        let Some(a) = action else {
            WidgetExt::display(&self.window).beep();
            return true;
        };
        let work = if redo { a.redo.clone() } else { Some(a.undo.clone()) };
        let Some(work) = work else { return true };
        let heading = fmt(&if redo { tr!("Redo {action} can’t be completed.") } else { tr!("Undo {action} can’t be completed.") }, &[("action", &a.label)]);
        let w = self.me.clone();
        undo::run(work, move |err| {
            let Some(t) = w.upgrade() else { return };
            t.reload_all();
            if let Some(e) = err {
                t.error(&heading, &e);
            }
        });
        true
    }

    // -- keys -------------------------------------------------------------------------------------
    fn shortcuts(&self) {
        let ctl = gtk::ShortcutController::new();
        ctl.set_scope(gtk::ShortcutScope::Global);
        type Act = fn(&Rc<FilesWindow>) -> bool;
        let keys: Vec<(&str, Act)> = vec![
            ("<Control>bracketleft|<Alt>Left", |t| { t.go_back(); true }),
            ("<Control>bracketright|<Alt>Right", |t| { t.go_forward(); true }),
            ("<Control>Up|<Alt>Up", |t| { t.go_up(); true }),
            ("<Control>Down|<Control>o", |t| { t.open_selection(); true }),
            ("<Control><Shift>h", |t| { t.home_dir(None); true }),
            ("<Control><Shift>d", |t| { t.home_dir(Some(glib::UserDirectory::Desktop)); true }),
            ("<Control><Shift>o", |t| { t.home_dir(Some(glib::UserDirectory::Documents)); true }),
            ("<Control><Alt>l", |t| { t.home_dir(Some(glib::UserDirectory::Downloads)); true }),
            ("<Control><Shift>f", |t| { t.go(RECENTS, true); true }),
            ("<Control><Shift>a", |t| { t.go(APPS, true); true }),
            ("<Control><Shift>period", |t| { t.toggle_hidden(); true }),
            ("<Control>f", |t| { t.open_search(); true }),
            ("<Control>n", |t| {
                if let Some(app) = t.window.application() {
                    present(&app, Some(&t.cur().map(|c| c.uri()).unwrap_or_default()), true);
                }
                true
            }),
            ("<Control>a", |t| { if let Some(v) = t.view() { v.select_all() } true }),
            ("<Control>1", |t| { t.set_view("icons", true); true }),
            ("<Control>2", |t| { t.set_view("list", true); true }),
            ("<Control>3", |t| { t.set_view("columns", true); true }),
            ("Escape", |t| {
                if t.search_rev.reveals_child() { t.close_search() } else if let Some(v) = t.view() { v.unselect_all() }
                true
            }),
            ("<Control><Shift>n", |t| { t.new_folder(); true }),
            ("<Control>c", |t| { if t.editing() { return false } t.copy_selection(false); true }),
            ("<Control><Alt>c", |t| { actions::copy_paths(&t.window, &t.selected_files()); true }),
            ("<Control>x", |t| { if t.editing() { return false } t.copy_selection(true); true }),
            ("<Control>v", |t| { if t.editing() { return false } t.paste(false); true }),
            ("<Control><Alt>v", |t| { t.paste(true); true }),
            ("<Control>d", |t| { t.duplicate_selection(); true }),
            ("<Control>i", |t| { t.get_info(); true }),
            ("<Control>y", |t| { t.toggle_quicklook(); true }),
            ("<Control><Shift>g", |t| { t.go_to_folder(); true }),
            ("<Control>l", |t| { t.edit_address(); true }),
            ("<Control><Alt>p", |t| { t.toggle_path_bar(); true }),
            ("<Control>k", |t| { t.connect_to_server(); true }), // Finder: ⌘K
            ("<Control>z", |t| t.run_history(false)),
            ("<Control><Shift>z", |t| t.run_history(true)),
        ];
        for (trig, f) in keys {
            let w = self.me.clone();
            let action = gtk::CallbackAction::new(move |_, _| w.upgrade().is_some_and(|t| f(&t)).into());
            ctl.add_shortcut(gtk::Shortcut::new(gtk::ShortcutTrigger::parse_string(trig), Some(action)));
        }
        self.window.add_controller(ctl);
        let file_keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
        let w = self.me.clone();
        file_keys.connect_key_pressed(move |_, k, _, st| match w.upgrade() {
            Some(t) if t.tab_keys(k, st) || t.file_keys(k, st) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.window.add_controller(file_keys);
    }

    /// ⌘T, ⌘W, Ctrl+Tab / Ctrl+Shift+Tab, ⌘⇧[ / ⌘⇧].
    fn tab_keys(self: &Rc<Self>, keyval: gdk::Key, state: gdk::ModifierType) -> bool {
        use gdk::Key;
        if !self.tabs_on {
            return false;
        }
        let ctrl = state.contains(gdk::ModifierType::CONTROL_MASK);
        let cmd = ctrl || state.contains(gdk::ModifierType::SUPER_MASK);
        let shift = state.contains(gdk::ModifierType::SHIFT_MASK);
        let k = keyval.to_lower();
        if ctrl && matches!(k, Key::Tab | Key::ISO_Left_Tab | Key::KP_Tab) {
            self.cycle_tab(if shift || k == Key::ISO_Left_Tab { -1 } else { 1 });
            return true;
        }
        if !cmd {
            return false;
        }
        if shift && matches!(k, Key::bracketleft | Key::braceleft | Key::bracketright | Key::braceright) {
            self.cycle_tab(if matches!(k, Key::bracketleft | Key::braceleft) { -1 } else { 1 });
            return true;
        }
        if !shift && k == Key::t {
            self.new_tab(None, true);
            return true;
        }
        if !shift && k == Key::w {
            self.close_tab_rc(None);
            return true;
        }
        false
    }

    fn file_keys(&self, keyval: gdk::Key, state: gdk::ModifierType) -> bool {
        use gdk::Key;
        if self.editing() {
            return false;
        }
        let ctrl = state.contains(gdk::ModifierType::CONTROL_MASK);
        let has_sel = self.view().is_some_and(|v| !v.selected().is_empty());
        match keyval {
            Key::F2 if !ctrl && has_sel => {
                self.rename_selection();
                true
            }
            Key::Return | Key::KP_Enter if !ctrl => self.enter(),
            Key::BackSpace if !ctrl => {
                if self.cur().is_some_and(|t| t.pos.get() > 0) {
                    self.go_back();
                } else {
                    self.go_up();
                }
                true
            }
            Key::space if !ctrl && (has_sel || self.ql.borrow().is_some()) && self.typed.borrow().is_empty() => {
                self.toggle_quicklook();
                true
            }
            Key::Delete if has_sel => {
                self.trash_selection();
                true
            }
            Key::BackSpace if ctrl && has_sel => {
                self.trash_selection();
                true
            }
            _ => {
                let alt = state.intersects(gdk::ModifierType::ALT_MASK | gdk::ModifierType::SUPER_MASK);
                match keyval.to_unicode() {
                    Some(ch) if !ch.is_control() && !ctrl && !alt && (ch != ' ' || !self.typed.borrow().is_empty()) => self.type_select(ch),
                    _ => false,
                }
            }
        }
    }

    /// Enter opens the selection; Enter twice (quickly) renames it.
    fn enter(&self) -> bool {
        if self.view().is_none_or(|v| v.selected().is_empty()) {
            return false;
        }
        if let Some(s) = self.enter_src.take() {
            s.remove();
            if self.view().is_some_and(|v| v.selected().len() == 1) {
                self.rename_selection();
            }
            return true;
        }
        let w = self.me.clone();
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis(ENTER_TWICE_MS), move || {
            if let Some(t) = w.upgrade() {
                t.enter_src.replace(None);
                t.open_selection();
            }
        });
        self.enter_src.replace(Some(id));
        true
    }

    /// Letters jump to the first item starting with what was typed; typing
    /// on within a second extends the name ("do" -> "Documents").
    fn type_select(&self, ch: char) -> bool {
        if let Some(s) = self.typed_src.take() {
            s.remove();
        }
        self.typed.borrow_mut().push(ch);
        let w = self.me.clone();
        let id = glib::timeout_add_local_once(std::time::Duration::from_millis(1000), move || {
            if let Some(t) = w.upgrade() {
                t.typed.borrow_mut().clear();
                t.typed_src.replace(None);
            }
        });
        self.typed_src.replace(Some(id));
        let Some(v) = self.view() else { return true };
        let typed = self.typed.borrow().clone();
        if !v.select_prefix(&typed) {
            v.select_prefix(&ch.to_string()); // a new word: start again from this letter
            self.typed.replace(ch.to_string());
        }
        true
    }

    /// Files dropped on a folder (view background, folder, sidebar, tab).
    /// Onto their own folder, onto themselves or a folder into its own
    /// subfolder: silently nothing.
    pub fn drop_on(&self, files: Vec<gio::File>, dest: gio::File, copy: bool) -> bool {
        let d = dest.uri().to_string();
        if folder::is_virtual(&d) || files.is_empty() {
            return false;
        }
        if ops::is_trash(&d) {
            // dropped on Trash: move to the Trash
            let files: Vec<gio::File> = files.into_iter().filter(|f| !ops::is_trash(&f.uri())).collect();
            if files.is_empty() {
                return false;
            }
            ops::trash(files, self.report(n_!("“{name}” can’t be moved to the Trash.")), Some(Box::new(|done| undo::trashed(&done))));
            return true;
        }
        let files = ops::drop_plan(&files, &dest);
        if files.is_empty() {
            return false;
        }
        let mv = !copy && files.iter().all(|f| same_disk(f, &dest));
        ops::transfer(files, &dest, mv, Some(self.window.clone().upcast()), self.refresh_after(), false);
        true
    }
}

impl tabs::Host for FilesWindow {
    fn select_tab(&self, id: u64) {
        if let Some(t) = self.tab_by_id(id) {
            self.select_tab_rc(&t);
        }
    }
    fn close_tab(&self, id: u64) {
        let t = self.tab_by_id(id);
        self.close_tab_rc(t);
    }
    fn new_tab(&self) {
        if let Some(me) = self.me.upgrade() {
            FilesWindow::new_tab(&me, None, true);
        }
    }
    fn move_tab(&self, id: u64, index: usize) {
        let mut tabs = self.tabs.borrow_mut();
        let Some(i) = tabs.iter().position(|t| t.id == id) else { return };
        let index = index.min(tabs.len() - 1);
        if i != index {
            let t = tabs.remove(i);
            tabs.insert(index, t);
            drop(tabs);
            self.strip.move_to(id, index);
        }
    }
    fn drop_files(&self, files: Vec<gio::File>, dest: gio::File, copy: bool) -> bool {
        self.drop_on(files, dest, copy)
    }
    fn tab_uri(&self, id: u64) -> Option<String> {
        self.tab_by_id(id).map(|t| t.uri())
    }
}

impl Dnd for FilesWindow {
    fn files_for_drag(&self, info: &gio::FileInfo) -> Vec<gio::File> {
        let sel = self.view().map(|v| v.selected()).unwrap_or_default();
        let items = if sel.contains(info) { sel } else { vec![info.clone()] };
        items.iter().filter_map(file_of).collect()
    }
    fn drop_files(&self, files: Vec<gio::File>, dest: gio::File, copy: bool) -> bool {
        self.drop_on(files, dest, copy)
    }
    /// A drag hovering on a folder opens it (spring-loaded folders), never
    /// the folder being dragged.
    fn spring_open(&self, info: &gio::FileInfo) {
        let Some(f) = file_of(info) else { return };
        if self.is_dragged(&f) {
            return;
        }
        let t = info.attribute_string("standard::target-uri").map(|s| s.to_string()).filter(|s| !s.is_empty()).unwrap_or_else(|| f.uri().to_string());
        self.go(&t, true);
    }
    fn is_dragged(&self, f: &gio::File) -> bool {
        self.dragged.borrow().iter().any(|d| d.equal(f))
    }
    fn drag_started(&self, files: Option<Vec<gio::File>>) {
        self.dragged.replace(files.unwrap_or_default());
    }
}

/// A Files window on `uri` (a new one; `fresh` false: the open one, brought forward).
pub fn present(app: &gtk::Application, uri: Option<&str>, fresh: bool) -> Rc<FilesWindow> {
    if !fresh {
        if let Some(w) = WINDOWS.with(|w| w.borrow().first().cloned()) {
            if let Some(u) = uri {
                w.go(u, true);
            }
            w.window.present();
            return w;
        }
    }
    let w = FilesWindow::new(app, uri);
    WINDOWS.with(|l| l.borrow_mut().push(w.clone()));
    w.window.present();
    w
}

#[cfg(test)]
mod tests {
    #[test]
    fn same_disk_of_a_folder_and_itself() {
        let d = tempfile::tempdir().unwrap();
        let g = gio::File::for_path(d.path());
        assert!(super::same_disk(&g, &g));
    }
}
