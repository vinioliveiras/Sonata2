//! Notes (macOS Ventura Notes, with Reminders in the same window), ported
//! from `sonata2/notes/window.py`.
//!
//! Three panes: the sidebar lists the Notes folders -- All Notes, Notes,
//! your folders, Recently Deleted -- and the Reminders lists (Today,
//! Scheduled, All, Completed, your lists); the middle pane lists the
//! folder's notes (pinned first, then by date edited, in date sections);
//! the editor shows the selected note. A reminders list takes both panes.
//!
//! Notes save themselves half a second after you stop typing; an empty note
//! goes away when you leave it. Deleted notes stay in Recently Deleted for
//! 30 days. Keyboard (⌘ is Ctrl or Super): N new note (new reminder in
//! Reminders), Shift+N new folder, F search, B / I / U, Shift+T title,
//! Shift+H heading, Shift+J subheading, Shift+B body, Shift+L checklist,
//! Delete deletes the selected note, Z after a delete brings it back, W closes.

use crate::editor::{format_date, NoteEditor};
use crate::markup;
use crate::notifier::Notifier;
use crate::reminders::{smart, RemindersView};
use crate::store::{self as S, Rec, Store};
use gtk::gdk::{Key, ModifierType};
use gtk::{glib, pango, prelude::*};
use serde_json::{json, Value};
use sonata_i18n::{fmt, gettext, tr, trn};
use sonata_ui::{dialog, menu, panel};
use std::cell::{Cell, RefCell};
use std::collections::HashMap;
use std::rc::{Rc, Weak};
use std::time::Duration;

/// ms after the last keystroke
const SAVE_DELAY: u64 = 500;
/// the apps' one sidebar width (ui/window.py)
const SIDEBAR_W: i32 = 280;

fn defaults() -> Value {
    json!({"sidebar_width": 200, "list_width": 270, "selected": "n:all"})
}

/// A row of the notes list, as shown.
#[derive(Clone)]
struct Item {
    title: String,
    preview: String,
    modified: f64,
    section: (i32, i32, String),
    date: String,
}

fn item_of(n: &Rec) -> Item {
    let (title, preview) = markup::title_and_preview(S::s(n, "body"));
    let modified = S::f(n, "modified");
    let pinned = S::b(n, "pinned") && !S::b(n, "deleted");
    let now = S::now();
    Item { title, preview, modified, section: sonata_ui::fmt::date_section(modified, pinned, now), date: sonata_ui::fmt::short_date(modified, now) }
}

/// A sidebar row: a folder, a smart list or a list (key "n:<id>" / "r:<id>").
struct SideRow {
    row: gtk::ListBoxRow,
    key: String,
    name: gtk::Widget,
    count: gtk::Label,
}

struct NoteRow {
    title: gtk::Label,
    date: gtk::Label,
    preview: gtk::Label,
    folder: gtk::Box,
    fname: gtk::Label,
    id: RefCell<Option<String>>,
}

pub struct NotesWindow {
    pub window: gtk::ApplicationWindow,
    pub store: Rc<RefCell<Store>>,
    cfg: serde_json::Map<String, Value>,
    notifier: Rc<Notifier>,
    key: RefCell<Option<String>>,
    query: RefCell<String>,
    /// the note in the editor (its id)
    note: RefCell<Option<String>>,
    save_id: RefCell<Option<glib::SourceId>>,
    /// the last deleted note (⌘Z)
    undo: RefCell<Option<String>>,
    items: RefCell<HashMap<String, Item>>,
    syncing: Cell<bool>,
    side: gtk::ListBox,
    side_rows: RefCell<Vec<Rc<SideRow>>>,
    sidebar: gtk::Box,
    add_label: gtk::Label,
    outer: gtk::Paned,
    inner: gtk::Paned,
    stack: gtk::Stack,
    model: gtk::StringList,
    sorter: gtk::CustomSorter,
    sorted: gtk::SortListModel,
    selection: gtk::SingleSelection,
    listview: gtk::ListView,
    list_empty: gtk::Label,
    note_rows: RefCell<Vec<Rc<NoteRow>>>,
    pub editor: Rc<NoteEditor>,
    editor_empty: gtk::Label,
    reminders: Rc<RemindersView>,
    search: gtk::SearchEntry,
    delete_btn: gtk::Button,
    new_btn: gtk::Button,
    format_btn: gtk::Button,
    check_btn: gtk::Button,
    rem_btn: gtk::Button,
    me: Weak<NotesWindow>,
}

fn head(text: &str) -> gtk::ListBoxRow {
    let row = gtk::ListBoxRow::builder().selectable(false).activatable(false).can_focus(false).build();
    row.add_css_class("nt-head");
    row.set_child(Some(&gtk::Label::builder().label(text).xalign(0.0).build()));
    row
}

impl NotesWindow {
    pub fn new(app: &impl IsA<gtk::Application>) -> Rc<Self> {
        sonata_ui::css::register_module("notes/window.py"); // the Python Notes' own CSS (css.json)
        sonata_ui::css::register_module("notes/editor.py");
        let store = Rc::new(RefCell::new(Store::new()));
        let cfg = sonata_config::load("notes", &defaults());
        let w = Rc::new_cyclic(|me: &Weak<NotesWindow>| {
            let window = gtk::ApplicationWindow::builder().application(app).title(tr!("Notes")).build();
            // its last size; the first time, roomy (never bigger than the display)
            sonata_ui::window::remember_size_roomy(window.upcast_ref(), "notes", 1060, 640);
            window.add_css_class("sonata-notes");
            window.set_size_request(640, 380);
            sonata_ui::window::standard(&window);
            let call = |f: fn(&Rc<NotesWindow>)| -> Box<dyn Fn()> {
                let w = me.clone();
                Box::new(move || {
                    if let Some(t) = w.upgrade() {
                        f(&t);
                    }
                })
            };
            let (toolbar, bar, tools) = sonata_ui::window::glass_toolbar(
                &window,
                vec![
                    ("user-trash-symbolic", tr!("Delete"), call(|t| t.delete_selected())),
                    ("document-edit-symbolic", tr!("New Note"), call(|t| t.new_note())),
                    ("format-text-rich-symbolic", tr!("Format"), call(|t| t.format_panel())),
                    ("checkbox-checked-symbolic", tr!("Make a Checklist"), call(|t| t.style("check"))),
                    ("list-add-symbolic", tr!("New Reminder"), call(|t| t.new_reminder())),
                ],
            );
            let side_btn = gtk::Button::builder().icon_name("sidebar-show-symbolic").tooltip_text(tr!("Show Sidebar")).can_focus(false).build();
            side_btn.add_css_class("tool");
            let f = call(|t| t.sidebar.set_visible(!t.sidebar.get_visible()));
            side_btn.connect_clicked(move |_| f());
            let start = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
            start.append(&side_btn);
            bar.set_start_widget(Some(&start));
            let search = gtk::SearchEntry::builder().placeholder_text(tr!("Search")).build();
            search.add_css_class("nt-search");
            if let Some(end) = bar.end_widget().and_downcast::<gtk::Box>() {
                end.append(&search);
            }
            // -- sidebar
            let sidebar = gtk::Box::new(gtk::Orientation::Vertical, 0);
            sidebar.add_css_class("nt-sidebar");
            sidebar.add_css_class("sonata-sidebar");
            sidebar.set_size_request(160, -1);
            let side = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).build();
            sidebar.append(&gtk::ScrolledWindow::builder().child(&side).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build());
            let bottom = gtk::Box::new(gtk::Orientation::Horizontal, 0);
            bottom.add_css_class("nt-sidebar-bottom");
            let add_btn = gtk::Button::builder().can_focus(false).build();
            let inner_b = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            inner_b.append(&gtk::Image::from_icon_name("list-add-symbolic"));
            let add_label = gtk::Label::new(Some(&tr!("New Folder")));
            inner_b.append(&add_label);
            add_btn.set_child(Some(&inner_b));
            let f = call(|t| if t.notes_mode() { t.new_folder() } else { t.new_list() });
            add_btn.connect_clicked(move |_| f());
            bottom.append(&add_btn);
            sidebar.append(&bottom);
            // -- notes list
            let model = gtk::StringList::new(&[]);
            let w = me.clone();
            let sorter = gtk::CustomSorter::new(move |a, b| {
                let Some(t) = w.upgrade() else { return gtk::Ordering::Equal };
                let items = t.items.borrow();
                let get = |o: &glib::Object| o.downcast_ref::<gtk::StringObject>().and_then(|s| items.get(s.string().as_str()).cloned());
                match (get(a), get(b)) {
                    (Some(x), Some(y)) => ((x.section.0, x.section.1), -x.modified).partial_cmp(&((y.section.0, y.section.1), -y.modified)).unwrap_or(std::cmp::Ordering::Equal).into(),
                    _ => gtk::Ordering::Equal,
                }
            });
            let w = me.clone();
            let section_sorter = gtk::CustomSorter::new(move |a, b| {
                let Some(t) = w.upgrade() else { return gtk::Ordering::Equal };
                let items = t.items.borrow();
                let get = |o: &glib::Object| o.downcast_ref::<gtk::StringObject>().and_then(|s| items.get(s.string().as_str()).map(|i| (i.section.0, i.section.1)));
                get(a).cmp(&get(b)).into()
            });
            let sorted = gtk::SortListModel::new(Some(model.clone()), Some(sorter.clone()));
            sorted.set_section_sorter(Some(&section_sorter));
            let selection = gtk::SingleSelection::builder().model(&sorted).autoselect(false).can_unselect(true).build();
            let factory = gtk::SignalListItemFactory::new();
            let header = gtk::SignalListItemFactory::new();
            let listview = gtk::ListView::builder().model(&selection).factory(&factory).header_factory(&header).build();
            listview.add_css_class("nt-notes");
            let list_over = gtk::Overlay::new();
            list_over.add_css_class("nt-list-pane");
            list_over.set_child(Some(&gtk::ScrolledWindow::builder().child(&listview).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build()));
            let list_empty = gtk::Label::builder().label(tr!("No Notes")).can_target(false).visible(false).build();
            list_empty.add_css_class("nt-empty");
            list_over.add_overlay(&list_empty);
            list_over.set_size_request(200, -1);
            // -- editor
            let editor = NoteEditor::new();
            let ed_over = gtk::Overlay::new();
            ed_over.add_css_class("nt-editor-pane");
            ed_over.set_child(Some(&editor.scroll));
            let editor_empty = gtk::Label::builder().label(tr!("No Note Selected")).visible(false).can_target(false).build();
            editor_empty.add_css_class("nt-empty");
            ed_over.add_overlay(&editor_empty);
            ed_over.set_size_request(280, -1);
            let inner = gtk::Paned::builder().start_child(&list_over).end_child(&ed_over).shrink_start_child(false).resize_start_child(false).shrink_end_child(false).build();
            inner.add_css_class("nt-paned");
            inner.set_position(cfg.get("list_width").and_then(Value::as_i64).unwrap_or(270) as i32);
            let stack = gtk::Stack::builder().transition_type(gtk::StackTransitionType::Crossfade).transition_duration(sonata_ui::tokens::ms(150.0)).build();
            stack.add_named(&inner, Some("notes"));
            let w = me.clone();
            let reminders = RemindersView::new(store.clone(), Box::new(move || {
                if let Some(t) = w.upgrade() {
                    t.update_counts();
                    t.notifier.schedule();
                }
            }));
            stack.add_named(&reminders.root, Some("reminders"));
            let outer = gtk::Paned::builder().start_child(&sidebar).end_child(&stack).shrink_start_child(false).resize_start_child(false).shrink_end_child(false).vexpand(true).build();
            outer.add_css_class("nt-paned");
            outer.set_position(SIDEBAR_W);
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            col.append(&toolbar);
            col.append(&outer);
            window.set_child(Some(&col));
            NotesWindow {
                window,
                notifier: Notifier::new(store.clone()),
                store,
                cfg: cfg.clone(),
                key: RefCell::new(None),
                query: RefCell::default(),
                note: RefCell::new(None),
                save_id: RefCell::new(None),
                undo: RefCell::new(None),
                items: RefCell::default(),
                syncing: Cell::new(false),
                side,
                side_rows: RefCell::default(),
                sidebar,
                add_label,
                outer,
                inner,
                stack,
                model,
                sorter,
                sorted,
                selection,
                listview,
                list_empty,
                note_rows: RefCell::default(),
                editor,
                editor_empty,
                reminders,
                search,
                delete_btn: tools[0].clone(),
                new_btn: tools[1].clone(),
                format_btn: tools[2].clone(),
                check_btn: tools[3].clone(),
                rem_btn: tools[4].clone(),
                me: me.clone(),
            }
        });
        w.wire(&factory_of(&w.listview), &header_of(&w.listview));
        w.rebuild_sidebar();
        let sel = w.cfg.get("selected").and_then(Value::as_str).unwrap_or("n:all").to_string();
        let key = if w.side_row(&sel).is_some() { sel } else { "n:all".into() };
        w.select(&key);
        let me = Rc::downgrade(&w);
        glib::idle_add_local_once(move || {
            if let Some(t) = me.upgrade() {
                t.focus_content();
            }
        });
        w
    }

    fn wire(self: &Rc<Self>, factory: &gtk::SignalListItemFactory, header: &gtk::SignalListItemFactory) {
        let w = Rc::downgrade(self);
        self.side.connect_row_selected(move |_, row| {
            if let (Some(t), Some(row)) = (w.upgrade(), row) {
                t.side_selected(row);
            }
        });
        let menu_click = gtk::GestureClick::builder().button(3).build();
        let w = Rc::downgrade(self);
        menu_click.connect_pressed(move |_, _n, x, y| {
            if let Some(t) = w.upgrade() {
                t.side_menu(x, y);
            }
        });
        self.side.add_controller(menu_click);
        let w = Rc::downgrade(self);
        self.search.connect_search_changed(move |e| {
            if let Some(t) = w.upgrade() {
                t.set_query(&e.text());
            }
        });
        let w = Rc::downgrade(self);
        self.search.connect_stop_search(move |e| {
            e.set_text("");
            if let Some(t) = w.upgrade() {
                t.focus_content();
            }
        });
        // rows of the notes list
        let w = Rc::downgrade(self);
        factory.connect_setup(move |_, li| {
            let (Some(t), Some(li)) = (w.upgrade(), li.downcast_ref::<gtk::ListItem>()) else { return };
            let b = gtk::Box::new(gtk::Orientation::Vertical, 2);
            b.add_css_class("nt-note-row");
            let title = gtk::Label::builder().xalign(0.0).ellipsize(pango::EllipsizeMode::End).build();
            title.add_css_class("nt-note-title");
            let line = gtk::Box::new(gtk::Orientation::Horizontal, 8);
            let date = gtk::Label::builder().xalign(0.0).build();
            date.add_css_class("nt-note-date");
            let preview = gtk::Label::builder().xalign(0.0).hexpand(true).ellipsize(pango::EllipsizeMode::End).build();
            preview.add_css_class("nt-note-preview");
            line.append(&date);
            line.append(&preview);
            let folder = gtk::Box::new(gtk::Orientation::Horizontal, 4);
            folder.add_css_class("nt-note-folder");
            let img = gtk::Image::from_icon_name("folder-symbolic");
            img.set_pixel_size(12);
            folder.append(&img);
            let fname = gtk::Label::builder().xalign(0.0).ellipsize(pango::EllipsizeMode::End).build();
            folder.append(&fname);
            b.append(&title);
            b.append(&line);
            b.append(&folder);
            b.set_widget_name("note-row");
            li.set_child(Some(&b));
            t.note_rows.borrow_mut().push(Rc::new(NoteRow { title, date, preview, folder, fname, id: RefCell::new(None) }));
            unsafe {
                b.set_data("row", t.note_rows.borrow().len() - 1);
            }
        });
        let w = Rc::downgrade(self);
        factory.connect_bind(move |_, li| {
            let (Some(t), Some(li)) = (w.upgrade(), li.downcast_ref::<gtk::ListItem>()) else { return };
            let (Some(b), Some(s)) = (li.child(), li.item().and_downcast::<gtk::StringObject>()) else { return };
            if let Some(row) = t.row_of_widget(&b) {
                *row.id.borrow_mut() = Some(s.string().to_string());
                t.fill_row(&row);
            }
        });
        let w = Rc::downgrade(self);
        factory.connect_unbind(move |_, li| {
            let (Some(t), Some(li)) = (w.upgrade(), li.downcast_ref::<gtk::ListItem>()) else { return };
            if let Some(row) = li.child().and_then(|b| t.row_of_widget(&b)) {
                row.id.borrow_mut().take();
            }
        });
        header.connect_setup(|_, h| {
            if let Some(h) = h.downcast_ref::<gtk::ListHeader>() {
                h.set_child(Some(&gtk::Label::builder().xalign(0.0).build()));
            }
        });
        let w = Rc::downgrade(self);
        header.connect_bind(move |_, h| {
            let (Some(t), Some(h)) = (w.upgrade(), h.downcast_ref::<gtk::ListHeader>()) else { return };
            let id = h.item().and_downcast::<gtk::StringObject>().map(|s| s.string().to_string());
            let title = id.and_then(|i| t.items.borrow().get(&i).map(|x| x.section.2.clone())).unwrap_or_default();
            if let Some(l) = h.child().and_downcast::<gtk::Label>() {
                l.set_label(&title);
            }
        });
        let w = Rc::downgrade(self);
        self.selection.connect_selected_item_notify(move |_| {
            if let Some(t) = w.upgrade() {
                t.note_selected();
            }
        });
        let note_menu = gtk::GestureClick::builder().button(3).build();
        let w = Rc::downgrade(self);
        note_menu.connect_pressed(move |_, _n, x, y| {
            if let Some(t) = w.upgrade() {
                t.note_menu(x, y);
            }
        });
        self.listview.add_controller(note_menu);
        let w = Rc::downgrade(self);
        *self.editor.on_edited.borrow_mut() = Some(Box::new(move || {
            if let Some(t) = w.upgrade() {
                t.edited();
            }
        }));
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let w = Rc::downgrade(self);
        keys.connect_key_pressed(move |_, keyval, _code, state| match w.upgrade() {
            Some(t) if t.key(keyval, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.window.add_controller(keys);
        let w = Rc::downgrade(self);
        self.window.connect_close_request(move |_| {
            if let Some(t) = w.upgrade() {
                t.close_request();
            }
            glib::Propagation::Proceed
        });
    }

    fn row_of_widget(&self, b: &gtk::Widget) -> Option<Rc<NoteRow>> {
        let i = unsafe { b.data::<usize>("row").map(|p| *p.as_ptr()) }?;
        self.note_rows.borrow().get(i).cloned()
    }

    fn fill_row(&self, row: &NoteRow) {
        let Some(id) = row.id.borrow().clone() else { return };
        let Some(item) = self.items.borrow().get(&id).cloned() else { return };
        row.title.set_label(&item.title);
        row.date.set_label(&item.date);
        row.preview.set_label(&item.preview);
        let show = matches!(self.folder().as_str(), S::ALL | S::DELETED) || !self.query.borrow().trim().is_empty();
        let st = self.store.borrow();
        let f = st.note(&id).and_then(|n| st.folder(S::s(n, "folder")));
        row.folder.set_visible(show && f.is_some());
        row.fname.set_label(&S::display_name(f));
    }

    // -- sidebar ------------------------------------------------------------------------------------
    fn side_row(&self, key: &str) -> Option<Rc<SideRow>> {
        self.side_rows.borrow().iter().find(|r| r.key == key).cloned()
    }

    fn add_side_row(self: &Rc<Self>, key: String, name: &str, icon: &str, badge: Option<&str>, editable: bool) {
        let row = gtk::ListBoxRow::new();
        let b = gtk::Box::new(gtk::Orientation::Horizontal, 8);
        let img = gtk::Image::from_icon_name(icon);
        match badge {
            Some(color) => {
                img.set_pixel_size(12);
                img.add_css_class("nt-badge");
                img.add_css_class(&format!("bg-{color}"));
                img.set_valign(gtk::Align::Center);
                img.set_halign(gtk::Align::Center);
            }
            None => {
                img.set_pixel_size(16);
                img.add_css_class("nt-folder");
            }
        }
        b.append(&img);
        let name_w: gtk::Widget = if editable {
            gtk::EditableLabel::builder().text(name).hexpand(true).build().upcast()
        } else {
            gtk::Label::builder().label(name).xalign(0.0).hexpand(true).ellipsize(pango::EllipsizeMode::End).build().upcast()
        };
        b.append(&name_w);
        let count = gtk::Label::new(None);
        count.add_css_class("nt-count");
        b.append(&count);
        row.set_child(Some(&b));
        self.side.append(&row);
        let sr = Rc::new(SideRow { row, key: key.clone(), name: name_w.clone(), count });
        if let Some(el) = name_w.downcast_ref::<gtk::EditableLabel>() {
            let (w, k) = (Rc::downgrade(self), key);
            el.connect_editing_notify(move |l| {
                if let Some(t) = w.upgrade() {
                    if !l.is_editing() {
                        t.renamed(&k, l);
                    }
                }
            });
        }
        self.side_rows.borrow_mut().push(sr);
    }

    fn rebuild_sidebar(self: &Rc<Self>) {
        self.syncing.set(true);
        self.side.remove_all();
        self.side_rows.borrow_mut().clear();
        self.side.append(&head(&tr!("Notes")));
        self.add_side_row(format!("n:{}", S::ALL), &tr!("All Notes"), "folder-symbolic", None, false);
        let folders: Vec<(String, String)> = self.store.borrow().folders.iter().map(|f| (S::s(f, "id").to_string(), S::display_name(Some(f)))).collect();
        for (id, name) in folders {
            let editable = id != S::DEFAULT_FOLDER;
            self.add_side_row(format!("n:{id}"), &name, "folder-symbolic", None, editable);
        }
        if self.store.borrow().notes.iter().any(|n| S::b(n, "deleted")) {
            self.add_side_row(format!("n:{}", S::DELETED), &tr!("Recently Deleted"), "user-trash-symbolic", None, false);
        }
        self.side.append(&head(&tr!("Reminders")));
        for k in S::SMART {
            if let Some((name, icon, color)) = smart(k) {
                self.add_side_row(format!("r:{k}"), &gettext(name), icon, Some(color), false);
            }
        }
        let lists: Vec<(String, String, String)> = self.store.borrow().lists.iter().map(|x| (S::s(x, "id").to_string(), S::display_name(Some(x)), S::s(x, "color").to_string())).collect();
        for (id, name, color) in lists {
            let color = if color.is_empty() { "blue".to_string() } else { color };
            self.add_side_row(format!("r:{id}"), &name, "view-list-bullet-symbolic", Some(&color), true);
        }
        self.update_counts();
        if let Some(r) = self.key.borrow().as_ref().and_then(|k| self.side_row(k)) {
            self.side.select_row(Some(&r.row));
        }
        self.syncing.set(false);
    }

    fn update_counts(&self) {
        let st = self.store.borrow();
        let today = crate::reminders::today();
        for row in self.side_rows.borrow().iter() {
            let (kind, ident) = row.key.split_once(':').unwrap_or(("n", ""));
            let n = if kind == "n" { st.folder_count(ident) } else { st.reminders_in(ident, today).len() };
            row.count.set_label(&if n > 0 { n.to_string() } else { String::new() });
        }
    }

    pub fn select(&self, key: &str) {
        if let Some(r) = self.side_row(key) {
            self.side.select_row(Some(&r.row));
        }
    }

    fn side_selected(self: &Rc<Self>, row: &gtk::ListBoxRow) {
        let Some(sr) = self.side_rows.borrow().iter().find(|r| &r.row == row).cloned() else { return };
        if self.syncing.get() || self.key.borrow().as_deref() == Some(sr.key.as_str()) {
            return;
        }
        self.flush();
        *self.key.borrow_mut() = Some(sr.key.clone());
        let _ = sonata_config::update("notes", &json!({"selected": sr.key}));
        let notes = self.notes_mode();
        self.stack.set_visible_child_name(if notes { "notes" } else { "reminders" });
        for b in [&self.format_btn, &self.check_btn, &self.new_btn] {
            b.set_visible(notes);
        }
        self.rem_btn.set_visible(!notes);
        self.add_label.set_label(&if notes { tr!("New Folder") } else { tr!("Add List") });
        self.search.set_placeholder_text(Some(&if notes { tr!("Search") } else { tr!("Search Reminders") }));
        if notes {
            self.reload_notes(None);
        } else {
            let q = self.query.borrow().clone();
            self.reminders.show(&sr.key[2..], Some(&q));
        }
    }

    fn notes_mode(&self) -> bool {
        self.key.borrow().as_deref().unwrap_or("n:").starts_with("n:")
    }

    fn folder(&self) -> String {
        if self.notes_mode() { self.key.borrow().as_deref().map(|k| k[2..].to_string()).unwrap_or_else(|| S::ALL.into()) } else { S::ALL.into() }
    }

    fn side_menu(self: &Rc<Self>, x: f64, y: f64) {
        let row = self.side.row_at_y(y as i32);
        let sr = row.as_ref().and_then(|r| self.side_rows.borrow().iter().find(|s| &s.row == r).cloned());
        let item = |label: String, f: fn(&Rc<NotesWindow>)| {
            let w = self.me.clone();
            menu::Item::new(&label, move || {
                if let Some(t) = w.upgrade() {
                    f(&t);
                }
            })
        };
        let Some(sr) = sr else {
            let today_idx = self.side_row("r:today").map(|r| r.row.index()).unwrap_or(i32::MAX);
            let in_notes = row.as_ref().is_none_or(|r| r.index() < today_idx);
            let sections = if in_notes { vec![vec![item(tr!("New Folder"), |t| t.new_folder())]] } else { vec![vec![item(tr!("Add List"), |t| t.new_list())]] };
            crate::reminders::popup_menu(&self.side, sections, crate::reminders::menu_at(x, y));
            return;
        };
        let (kind, ident) = sr.key.split_once(':').map(|(a, b)| (a.to_string(), b.to_string())).unwrap_or_default();
        let edit = {
            let name = sr.name.clone();
            move || {
                if let Some(el) = name.downcast_ref::<gtk::EditableLabel>() {
                    el.start_editing();
                }
            }
        };
        let sections = if kind == "n" {
            if ident == S::DELETED {
                vec![vec![item(tr!("Empty Recently Deleted"), |t| t.empty_deleted())]]
            } else {
                let exists = self.store.borrow().folder(&ident).is_some();
                let ok = exists && ident != S::DEFAULT_FOLDER;
                let (w, id) = (self.me.clone(), ident.clone());
                vec![
                    vec![item(tr!("New Folder"), |t| t.new_folder())],
                    vec![
                        menu::Item::new(&tr!("Rename Folder"), edit).enabled(ok),
                        menu::Item::new(&tr!("Delete Folder"), move || {
                            if let Some(t) = w.upgrade() {
                                t.delete_folder(&id);
                            }
                        })
                        .enabled(ok),
                    ],
                ]
            }
        } else {
            let exists = self.store.borrow().rlist(&ident).is_some();
            let (w, id) = (self.me.clone(), ident.clone());
            vec![
                vec![item(tr!("Add List"), |t| t.new_list())],
                vec![
                    menu::Item::new(&tr!("Rename List"), edit).enabled(exists),
                    menu::Item::new(&tr!("Delete List"), move || {
                        if let Some(t) = w.upgrade() {
                            t.delete_list(&id);
                        }
                    })
                    .enabled(exists && ident != S::DEFAULT_LIST),
                ],
            ]
        };
        crate::reminders::popup_menu(&self.side, sections, crate::reminders::menu_at(x, y));
    }

    fn renamed(&self, key: &str, label: &gtk::EditableLabel) {
        let (kind, ident) = key.split_once(':').unwrap_or(("n", ""));
        let text = label.text().to_string();
        let current = {
            let st = self.store.borrow();
            if kind == "n" { st.folder(ident).map(|x| S::display_name(Some(x))) } else { st.rlist(ident).map(|x| S::display_name(Some(x))) }
        };
        if current.as_deref() == Some(text.as_str()) {
            return; // unchanged (a built-in name, translated)
        }
        let name = if kind == "n" {
            let r = self.store.borrow_mut().rename_folder(ident, &text);
            r.or(current)
        } else {
            let r = self.store.borrow_mut().rename_list(ident, &text);
            if self.key.borrow().as_deref() == Some(key) {
                self.reminders.refresh();
            }
            r.or(current)
        };
        if let Some(n) = name.filter(|n| *n != text) {
            label.set_text(&n);
        }
    }

    fn new_folder(self: &Rc<Self>) {
        let id = self.store.borrow_mut().add_folder(&tr!("New Folder"));
        self.rebuild_sidebar();
        let key = format!("n:{id}");
        self.select(&key);
        self.start_editing(&key);
    }

    fn new_list(self: &Rc<Self>) {
        let id = self.store.borrow_mut().add_list(&tr!("New List"));
        self.rebuild_sidebar();
        let key = format!("r:{id}");
        self.select(&key);
        self.start_editing(&key);
    }

    fn start_editing(&self, key: &str) {
        if let Some(r) = self.side_row(key) {
            let name = r.name.clone();
            glib::idle_add_local_once(move || {
                if let Some(el) = name.downcast_ref::<gtk::EditableLabel>() {
                    el.start_editing();
                }
            });
        }
    }

    fn delete_folder(self: &Rc<Self>, id: &str) {
        let count = self.store.borrow().folder_count(id);
        let name = S::display_name(self.store.borrow().folder(id));
        let (w, id) = (self.me.clone(), id.to_string());
        let answer = move |rid: &str| {
            let Some(t) = w.upgrade() else { return };
            if rid != "delete" {
                return;
            }
            if t.key.borrow().as_deref() == Some(&format!("n:{id}")) {
                t.flush();
                *t.key.borrow_mut() = None;
            }
            t.store.borrow_mut().delete_folder(&id, None);
            t.rebuild_sidebar();
            if t.key.borrow().is_none() {
                t.select(&format!("n:{}", S::ALL));
            }
        };
        if count == 0 {
            answer("delete");
            return;
        }
        dialog::alert(
            &fmt(&tr!("Are you sure you want to delete the folder “{name}”?"), &[("name", &name)]),
            &fmt(&trn!("Its {n} note will be moved to Recently Deleted.", "Its {n} notes will be moved to Recently Deleted.", count), &[("n", &count.to_string())]),
            &[("cancel", tr!("Cancel"), ""), ("delete", tr!("Delete"), "destructive")],
            answer,
            Some(self.window.upcast_ref()),
        );
    }

    fn delete_list(self: &Rc<Self>, id: &str) {
        let name = S::display_name(self.store.borrow().rlist(id));
        let (w, id) = (self.me.clone(), id.to_string());
        dialog::alert(
            &fmt(&tr!("Delete “{name}”?"), &[("name", &name)]),
            &tr!("This will delete all reminders in this list."),
            &[("cancel", tr!("Cancel"), ""), ("delete", tr!("Delete"), "destructive")],
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                if rid != "delete" {
                    return;
                }
                let was = t.key.borrow().as_deref() == Some(&format!("r:{id}"));
                t.store.borrow_mut().delete_list(&id);
                t.rebuild_sidebar();
                t.notifier.schedule();
                if was {
                    *t.key.borrow_mut() = None;
                    t.select("r:today");
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    fn empty_deleted(self: &Rc<Self>) {
        let w = self.me.clone();
        dialog::alert(
            &tr!("Are you sure you want to permanently delete these notes?"),
            &tr!("You can't undo this action."),
            &[("cancel", tr!("Cancel"), ""), ("delete", tr!("Delete"), "destructive")],
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                if rid == "delete" {
                    let ids: Vec<String> = t.store.borrow().notes_in(S::DELETED).iter().map(|n| S::s(n, "id").to_string()).collect();
                    for id in ids {
                        t.store.borrow_mut().destroy_note(&id);
                    }
                    t.after_notes_change(None);
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    // -- notes list ------------------------------------------------------------------------------------
    /// Fill the list with this folder's notes (or the search results).
    fn reload_notes(self: &Rc<Self>, select_id: Option<String>) {
        let notes = {
            let st = self.store.borrow();
            let q = self.query.borrow().clone();
            if q.trim().is_empty() { st.notes_in(&self.folder()) } else { S::search(&st.notes_in(S::ALL), &q) }
        };
        let keep = select_id.or_else(|| self.note.borrow().clone());
        {
            let mut items = self.items.borrow_mut();
            for n in &notes {
                items.insert(S::s(n, "id").to_string(), item_of(n));
            }
        }
        let ids: Vec<&str> = notes.iter().map(|n| S::s(n, "id")).collect();
        self.syncing.set(true);
        self.model.splice(0, self.model.n_items(), &ids);
        self.syncing.set(false);
        let pos = keep.as_deref().and_then(|k| self.position(k)).or(if ids.is_empty() { None } else { Some(0) });
        match pos {
            None => {
                self.selection.set_selected(gtk::INVALID_LIST_POSITION);
                self.open(None);
            }
            Some(p) => {
                self.selection.set_selected(p);
                self.note_selected();
            }
        }
        self.list_empty.set_label(&if self.query.borrow().trim().is_empty() { tr!("No Notes") } else { tr!("No Results") });
        self.list_empty.set_visible(ids.is_empty());
        self.delete_btn.set_sensitive(!ids.is_empty());
    }

    fn position(&self, id: &str) -> Option<u32> {
        (0..self.sorted.n_items()).find(|i| self.sorted.item(*i).and_downcast::<gtk::StringObject>().is_some_and(|s| s.string() == id))
    }

    fn id_at(&self, pos: u32) -> Option<String> {
        self.sorted.item(pos).and_downcast::<gtk::StringObject>().map(|s| s.string().to_string())
    }

    fn note_selected(self: &Rc<Self>) {
        if self.syncing.get() {
            return;
        }
        let id = self.selection.selected_item().and_downcast::<gtk::StringObject>().map(|s| s.string().to_string());
        if id != *self.note.borrow() {
            self.open(id);
        }
    }

    /// Show a note in the editor (the one we leave is saved; if empty, it goes).
    fn open(self: &Rc<Self>, id: Option<String>) {
        let prev = self.note.borrow().clone();
        self.flush();
        *self.note.borrow_mut() = id.clone();
        if let Some(p) = prev.filter(|p| Some(p) != id.as_ref()) {
            let empty = self.store.borrow().note(&p).is_some_and(|n| !S::b(n, "deleted") && markup::plain(S::s(n, "body")).join("").trim().is_empty());
            if empty {
                self.store.borrow_mut().destroy_note(&p);
                let w = self.me.clone();
                glib::idle_add_local_once(move || {
                    if let Some(t) = w.upgrade() {
                        t.after_notes_change(None);
                    }
                });
            }
        }
        let note = id.and_then(|i| self.store.borrow().note(&i).cloned());
        let Some(note) = note else {
            self.editor.load("", false);
            self.editor.scroll.set_visible(false);
            self.editor_empty.set_visible(true);
            return;
        };
        self.editor_empty.set_visible(false);
        self.editor.scroll.set_visible(true);
        self.editor.load(S::s(&note, "body"), !S::b(&note, "deleted"));
        self.show_dates(&note);
    }

    fn show_dates(&self, note: &Rec) {
        let t = [S::f(note, "modified"), S::f(note, "created")].into_iter().find(|x| *x > 0.0).unwrap_or_else(S::now);
        self.editor.set_dates(&format_date(t));
    }

    fn note_menu(self: &Rc<Self>, x: f64, y: f64) {
        // the row under the pointer: select it first (macOS)
        let mut picked = self.listview.pick(x, y, gtk::PickFlags::DEFAULT);
        while let Some(p) = picked.clone() {
            if p.widget_name() == "note-row" {
                break;
            }
            picked = p.parent().filter(|pp| pp != self.listview.upcast_ref::<gtk::Widget>());
        }
        let id = picked.and_then(|p| self.row_of_widget(&p)).and_then(|r| r.id.borrow().clone());
        let Some(id) = id else {
            let w = self.me.clone();
            crate::reminders::popup_menu(
                &self.listview,
                vec![vec![menu::Item::new(&tr!("New Note"), move || {
                    if let Some(t) = w.upgrade() {
                        t.new_note();
                    }
                })
                .enabled(self.folder() != S::DELETED)]],
                crate::reminders::menu_at(x, y),
            );
            return;
        };
        if let Some(p) = self.position(&id) {
            self.selection.set_selected(p);
        }
        let Some(note) = self.store.borrow().note(&id).cloned() else { return };
        let act = |label: String, f: Box<dyn Fn(&Rc<NotesWindow>)>| {
            let w = self.me.clone();
            menu::Item::new(&label, move || {
                if let Some(t) = w.upgrade() {
                    f(&t);
                }
            })
        };
        let sections = if S::b(&note, "deleted") {
            let (a, b) = (id.clone(), id.clone());
            vec![
                vec![act(tr!("Recover"), Box::new(move |t| t.recover(&a)))],
                vec![act(tr!("Delete Immediately"), Box::new(move |t| t.delete_note(&b)))],
            ]
        } else {
            let moves: Vec<menu::Item> = self
                .store
                .borrow()
                .folders
                .iter()
                .map(|f| {
                    let (w, nid, fid) = (self.me.clone(), id.clone(), S::s(f, "id").to_string());
                    menu::Item::check(&S::display_name(Some(f)), S::s(&note, "folder") == fid, move |_| {
                        if let Some(t) = w.upgrade() {
                            t.store.borrow_mut().move_note(&nid, &fid);
                            t.after_notes_change(None);
                        }
                    })
                })
                .collect();
            let pinned = S::b(&note, "pinned");
            let (a, b) = (id.clone(), id.clone());
            vec![
                vec![act(if pinned { tr!("Unpin Note") } else { tr!("Pin Note") }, Box::new(move |t| {
                    t.store.borrow_mut().set_pinned(&a, !pinned);
                    t.reload_notes(Some(a.clone()));
                }))],
                vec![menu::Item::submenu(&tr!("Move to"), vec![moves])],
                vec![act(tr!("Delete"), Box::new(move |t| t.delete_note(&b)))],
            ]
        };
        crate::reminders::popup_menu(&self.listview, sections, crate::reminders::menu_at(x, y));
    }

    // -- editor ------------------------------------------------------------------------------------------
    fn edited(&self) {
        let deleted = self.note.borrow().as_ref().and_then(|i| self.store.borrow().note(i).map(|n| S::b(n, "deleted")));
        if deleted != Some(false) {
            return;
        }
        if let Some(id) = self.save_id.borrow_mut().take() {
            sonata_ui::cancel(id);
        }
        let w = self.me.clone();
        *self.save_id.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(SAVE_DELAY), move || {
            if let Some(t) = w.upgrade() {
                t.save_id.borrow_mut().take();
                t.save();
            }
        }));
    }

    fn save(&self) {
        let Some(id) = self.note.borrow().clone() else { return };
        let live = self.store.borrow().note(&id).is_some_and(|n| !S::b(n, "deleted"));
        if !live {
            return;
        }
        let text = self.editor.text();
        let changed = self.store.borrow_mut().update_note(&id, &text, None);
        if changed {
            let note = self.store.borrow().note(&id).cloned();
            if let Some(n) = note {
                self.items.borrow_mut().insert(id.clone(), item_of(&n));
                for row in self.note_rows.borrow().iter() {
                    if row.id.borrow().as_deref() == Some(id.as_str()) {
                        self.fill_row(row);
                    }
                }
                self.sorter.changed(gtk::SorterChange::Different);
                self.show_dates(&n);
            }
        }
    }

    fn flush(&self) {
        let pending = self.save_id.borrow_mut().take();
        if let Some(id) = pending {
            sonata_ui::cancel(id);
            self.save();
        }
    }

    fn editable_note(&self) -> bool {
        self.notes_mode() && self.note.borrow().as_ref().and_then(|i| self.store.borrow().note(i).map(|n| !S::b(n, "deleted"))).unwrap_or(false)
    }

    fn style(&self, kind: &'static str) {
        if self.editable_note() {
            self.editor.set_style(kind);
        }
    }

    fn inline(&self, name: &'static str) {
        if self.editable_note() {
            self.editor.toggle_inline(name);
        }
    }

    fn format_panel(&self) {
        if !self.editable_note() {
            return;
        }
        let active = self.editor.active_styles();
        let kind = self.editor.line_kind(*self.editor.selected_lines().start()).0;
        let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
        let row = gtk::Box::builder().spacing(4).homogeneous(true).build();
        row.add_css_class("nt-fmt-row");
        for (name, markup_text, tip) in [("bold", "<b>B</b>", tr!("Bold")), ("italic", "<i>I</i>", tr!("Italic")), ("underline", "<u>U</u>", tr!("Underline")), ("strike", "<s>S</s>", tr!("Strikethrough"))] {
            let b = gtk::ToggleButton::builder().active(active.contains(name)).tooltip_text(tip).can_focus(false).build();
            let lab = gtk::Label::new(None);
            lab.set_markup(markup_text);
            b.set_child(Some(&lab));
            let w = self.me.clone();
            b.connect_toggled(move |_| {
                if let Some(t) = w.upgrade() {
                    t.inline(name);
                }
            });
            row.append(&b);
        }
        col.append(&row);
        let pop_cell: Rc<RefCell<Option<gtk::Popover>>> = Rc::default();
        let styles: [(Option<&'static str>, String); 8] = [
            (Some("title"), tr!("Title")),
            (Some("heading"), tr!("Heading")),
            (Some("subheading"), tr!("Subheading")),
            (Some("body"), tr!("Body")),
            (None, String::new()),
            (Some("bullet"), format!("•  {}", tr!("Bulleted List"))),
            (Some("number"), format!("1.  {}", tr!("Numbered List"))),
            (Some("check"), tr!("Checklist")),
        ];
        for (k, label) in styles {
            let Some(k) = k else {
                col.append(&panel::separator());
                continue;
            };
            let mark = gtk::Image::from_icon_name("object-select-symbolic");
            mark.set_opacity(if k == kind { 1.0 } else { 0.0 });
            let (w, pc) = (self.me.clone(), pop_cell.clone());
            let (r, l) = panel::row(None, &label, Some(mark.upcast_ref()), Some(Box::new(move || {
                if let Some(p) = pc.borrow().as_ref() {
                    p.popdown();
                }
                if let Some(t) = w.upgrade() {
                    t.style(k);
                }
            })));
            l.add_css_class(&format!("nt-fmt-{k}"));
            col.append(&r);
        }
        *pop_cell.borrow_mut() = Some(panel::popup(&self.format_btn, &col, gtk::PositionType::Bottom));
    }

    // -- note commands -----------------------------------------------------------------------------------
    pub fn new_note(self: &Rc<Self>) {
        if !self.notes_mode() || self.folder() == S::DELETED {
            let k = if self.folder() == S::DELETED || !self.notes_mode() { format!("n:{}", S::DEFAULT_FOLDER) } else { self.key.borrow().clone().unwrap_or_default() };
            self.select(&k);
        }
        self.flush();
        if !self.query.borrow().is_empty() {
            self.search.set_text("");
            self.query.borrow_mut().clear();
        }
        let id = self.store.borrow_mut().new_note(&self.folder(), "", None);
        self.update_counts();
        self.reload_notes(Some(id));
        self.editor.view.grab_focus();
    }

    fn delete_selected(self: &Rc<Self>) {
        if !self.notes_mode() {
            self.reminders.delete_selected();
            return;
        }
        let id = self.note.borrow().clone();
        if let Some(id) = id {
            self.delete_note(&id);
        }
    }

    fn delete_note(self: &Rc<Self>, id: &str) {
        let deleted = self.store.borrow().note(id).is_some_and(|n| S::b(n, "deleted"));
        if deleted {
            let (w, id) = (self.me.clone(), id.to_string());
            dialog::alert(
                &tr!("Are you sure you want to delete this note?"),
                &tr!("You can't undo this action."),
                &[("cancel", tr!("Cancel"), ""), ("delete", tr!("Delete"), "destructive")],
                move |rid| {
                    if let (Some(t), "delete") = (w.upgrade(), rid) {
                        t.do_delete(&id);
                    }
                },
                Some(self.window.upcast_ref()),
            );
            return;
        }
        self.do_delete(id);
    }

    fn do_delete(self: &Rc<Self>, id: &str) {
        self.flush();
        let pos = self.position(id);
        let was_deleted = self.store.borrow().note(id).is_some_and(|n| S::b(n, "deleted"));
        if self.note.borrow().as_deref() == Some(id) {
            *self.note.borrow_mut() = None; // not "left empty": it's going anyway
        }
        self.store.borrow_mut().delete_note(id, None);
        *self.undo.borrow_mut() = if was_deleted { None } else { Some(id.to_string()) };
        let n = self.sorted.n_items();
        let next = pos.filter(|_| n > 1).and_then(|p| self.id_at(if p + 1 < n { p + 1 } else { p.saturating_sub(1) }));
        self.after_notes_change(next);
    }

    fn undo_delete(self: &Rc<Self>) -> bool {
        let Some(id) = self.undo.borrow_mut().take() else { return false };
        let ok = self.store.borrow().note(&id).is_some_and(|n| S::b(n, "deleted"));
        if ok {
            self.recover(&id);
        }
        ok
    }

    fn recover(self: &Rc<Self>, id: &str) {
        self.store.borrow_mut().recover_note(id);
        let folder = self.store.borrow().note(id).map(|n| S::s(n, "folder").to_string()).unwrap_or_default();
        self.rebuild_sidebar();
        let cur = self.folder();
        if cur != S::ALL && cur != folder {
            self.select(&format!("n:{folder}"));
        }
        self.after_notes_change(Some(id.to_string()));
    }

    fn after_notes_change(self: &Rc<Self>, select_id: Option<String>) {
        let had = self.side_row(&format!("n:{}", S::DELETED)).is_some();
        let has = self.store.borrow().notes.iter().any(|n| S::b(n, "deleted"));
        if had != has {
            if self.key.borrow().as_deref() == Some(&format!("n:{}", S::DELETED)) && had {
                *self.key.borrow_mut() = None;
                self.rebuild_sidebar();
                self.select(&format!("n:{}", S::ALL));
                return;
            }
            self.rebuild_sidebar();
        }
        self.update_counts();
        if self.notes_mode() {
            self.reload_notes(select_id);
        }
    }

    // -- reminders ----------------------------------------------------------------------------------------
    fn new_reminder(&self) {
        if self.notes_mode() {
            self.select("r:today");
        }
        self.reminders.new_reminder();
    }

    // -- search ---------------------------------------------------------------------------------------------
    fn set_query(self: &Rc<Self>, q: &str) {
        if *self.query.borrow() == q {
            return;
        }
        self.flush();
        *self.query.borrow_mut() = q.to_string();
        if self.notes_mode() {
            self.reload_notes(None);
        } else {
            let k = self.key.borrow().clone().unwrap_or_default();
            self.reminders.show(&k[2..], Some(q));
        }
    }

    fn focus_content(&self) {
        if self.notes_mode() {
            self.listview.grab_focus();
        } else {
            self.reminders.list.grab_focus();
        }
    }

    // -- keys ------------------------------------------------------------------------------------------------
    fn typing(&self) -> bool {
        let Some(w) = gtk::prelude::RootExt::focus(&self.window) else { return false };
        w.is::<gtk::Text>() || w.is::<gtk::TextView>() || w.is::<gtk::Entry>() || w.is_ancestor(&self.editor.scroll)
    }

    fn key(self: &Rc<Self>, keyval: Key, state: ModifierType) -> bool {
        let cmd = state.intersects(ModifierType::CONTROL_MASK | ModifierType::SUPER_MASK);
        let shift = state.contains(ModifierType::SHIFT_MASK);
        let k = keyval.to_lower();
        if !cmd {
            if matches!(keyval, Key::Delete | Key::KP_Delete) && !self.typing() {
                self.delete_selected();
                return true;
            }
            return false;
        }
        let in_editor = gtk::prelude::RootExt::focus(&self.window).as_ref() == Some(self.editor.view.upcast_ref());
        if k == Key::z && !shift {
            if in_editor && self.editor.buffer.can_undo() {
                return false;
            }
            return self.undo_delete();
        }
        if shift {
            let style = match k {
                Key::t => Some("title"),
                Key::h => Some("heading"),
                Key::j => Some("subheading"),
                Key::b => Some("body"),
                Key::l => Some("check"),
                _ => None,
            };
            if let Some(s) = style {
                self.style(s);
                return true;
            }
            if k == Key::n {
                self.new_folder();
                return true;
            }
            return false;
        }
        match k {
            Key::n => {
                if self.notes_mode() {
                    self.new_note();
                } else {
                    self.new_reminder();
                }
            }
            Key::f => {
                self.search.grab_focus();
            }
            Key::w => self.window.close(),
            Key::b if in_editor => self.inline("bold"),
            Key::i if in_editor => self.inline("italic"),
            Key::u if in_editor => self.inline("underline"),
            _ => return false,
        }
        true
    }

    fn close_request(&self) {
        self.flush();
        let id = self.note.borrow().clone();
        if let Some(id) = id {
            let empty = self.store.borrow().note(&id).is_some_and(|n| !S::b(n, "deleted") && markup::plain(S::s(n, "body")).join("").trim().is_empty());
            if empty {
                self.store.borrow_mut().destroy_note(&id);
            }
        }
        self.notifier.stop();
        let cfg = json!({"sidebar_width": self.outer.position(), "list_width": self.inner.position()});
        let changed = cfg.as_object().is_some_and(|m| m.iter().any(|(k, v)| self.cfg.get(k) != Some(v)));
        if changed {
            let _ = sonata_config::update("notes", &cfg);
        }
    }
}

fn factory_of(lv: &gtk::ListView) -> gtk::SignalListItemFactory {
    lv.factory().and_downcast::<gtk::SignalListItemFactory>().expect("the notes list's factory")
}

fn header_of(lv: &gtk::ListView) -> gtk::SignalListItemFactory {
    lv.header_factory().and_downcast::<gtk::SignalListItemFactory>().expect("the notes list's header factory")
}

thread_local! {
    static WINDOW: RefCell<Option<Rc<NotesWindow>>> = const { RefCell::new(None) };
}

/// Run Notes: one window (opening Notes again brings it forward).
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(crate::app_id()).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    app.connect_activate(move |app| {
        sonata_ui::setup();
        if let Some(f) = force {
            sonata_ui::force_appearance(f);
        }
        let existing = WINDOW.with(|w| w.borrow().clone()).filter(|w| w.window.application().is_some());
        let win = existing.unwrap_or_else(|| {
            let w = NotesWindow::new(app);
            WINDOW.with(|x| *x.borrow_mut() = Some(w.clone()));
            w
        });
        win.window.present();
    });
    app.run_with_args(&[std::env::args().next().unwrap_or_default()])
}

#[cfg(test)]
mod tests {

    /// Notes opened at 1060 x 640 the first time even on a big display; the
    /// Python Notes opens roomy (remember_size(..., roomy=True)).
    #[test]
    fn test_first_window_was_not_roomy() {
        use sonata_ui::window::first_size;
        assert_eq!(first_size(None, 1060, 640, true, Some((2560, 1440))), (1689, 936, false));
        assert_eq!(first_size(None, 1060, 640, true, Some((1600, 900))), (1060, 640, false)); // never smaller than asked
        assert_eq!(first_size(None, 1060, 640, true, None), (1060, 640, false));
    }
}
