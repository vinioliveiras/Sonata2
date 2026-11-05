//! Assistant: chat with Claude (the Anthropic API) in a Sonata window
//! (assistant/window.py).
//!
//! The sidebar lists the conversations by date (Today, Yesterday, Previous
//! 7 Days...; right-click: Rename, Delete); the chat is in the middle and
//! the message field at the bottom (Return sends, Shift+Return starts a new
//! line, Esc stops the answer). Answers stream in as they are written,
//! formatted from their Markdown. Everything is saved locally (store.rs).
//!
//! Above the message field, three meters: the conversation's share of the
//! model's context window, and the tokens used in the last hour and the
//! last 7 days against your own limits (counted locally, usage.rs).
//!
//! Settings (the gear, a panel): the API key (in the keyring), the model,
//! your usage limits, and the folders Claude may use. With folders, Claude
//! can list, read and create files there (tools.rs); every action waits
//! for your OK in an alert, and shows in the chat.
//!
//! Keyboard (⌘ is Ctrl or Super): N new chat, comma Settings, W close, Esc stop.

use crate::markdown::{self, Block};
use crate::store::{Chat, Store};
use crate::usage::{self, Usage};
use crate::{api, keystore, tools};
use adw::prelude::AdwDialogExt;
use gtk::prelude::*;
use serde_json::{json, Map, Value};
use sonata_i18n::{fmt, tr, trn};
use std::cell::{Cell, RefCell};
use std::collections::{HashMap, VecDeque};
use std::rc::{Rc, Weak};

/// the conversation's reading width (centred)
const COLUMN_W: i32 = 720;
const SIDEBAR_W: i32 = 280;
const COMPOSER_MAX_H: i32 = 180;

fn defaults() -> Value {
    json!({"selected": "", "model": "", "folders": [], "hourly_limit": 1_000_000, "weekly_limit": 10_000_000})
}

type ChatRc = Rc<RefCell<Chat>>;

fn same(a: &Option<ChatRc>, b: &Option<ChatRc>) -> bool {
    matches!((a, b), (Some(x), Some(y)) if Rc::ptr_eq(x, y))
}

fn cid_of(c: &Chat) -> String {
    c.get("id").and_then(Value::as_str).unwrap_or("").to_string()
}

fn messages_of(c: &Chat) -> Vec<Value> {
    c.get("messages").and_then(Value::as_array).cloned().unwrap_or_default()
}

fn push_message(c: &ChatRc, m: Value) {
    let mut c = c.borrow_mut();
    match c.get_mut("messages").and_then(Value::as_array_mut) {
        Some(a) => a.push(m),
        None => {
            c.insert("messages".into(), json!([m]));
        }
    }
}

fn now() -> f64 {
    usage::now()
}

/// A conversation's title as shown (a new one's "New Chat", which the
/// store keeps in English, in the session's language).
pub fn chat_title(meta: Option<&Chat>) -> String {
    match meta.and_then(|m| m.get("title")).and_then(Value::as_str) {
        None | Some("") | Some("New Chat") => tr!("New Chat"),
        Some(t) => t.to_string(),
    }
}

/// 1,234,567 (Python's f"{n:,}").
fn grouped(n: i64) -> String {
    let s = n.abs().to_string();
    let mut out = String::new();
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    if n < 0 { format!("-{out}") } else { out }
}

fn label(markup: &str, css: Option<&str>) -> gtk::Label {
    let lab = gtk::Label::builder().wrap(true).wrap_mode(gtk::pango::WrapMode::WordChar).xalign(0.0).selectable(true).use_markup(true).can_focus(false).build();
    lab.set_markup(markup);
    if let Some(c) = css {
        lab.add_css_class(c);
    }
    lab
}

/// New messages fade and slide in (t_fast).
fn appear(widget: &impl IsA<gtk::Widget>) -> gtk::Revealer {
    let rev = gtk::Revealer::builder().child(widget).transition_type(gtk::RevealerTransitionType::SlideDown).transition_duration(sonata_ui::tokens::ms(150.0)).reveal_child(false).build();
    let r = rev.downgrade();
    glib::idle_add_local_once(move || {
        if let Some(r) = r.upgrade() {
            r.set_reveal_child(true);
        }
    });
    rev
}

/// An answer, shown from its Markdown. set_text() while it streams only
/// touches the blocks that changed (usually the last one).
pub struct AnswerView {
    pub root: gtk::Box,
    blocks: RefCell<Vec<(Block, gtk::Widget, Option<gtk::Label>)>>,
    text: RefCell<String>,
}

impl AnswerView {
    pub fn new(text: &str) -> Rc<Self> {
        let root = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(8).css_classes(["as-answer"]).build();
        let a = Rc::new(Self { root, blocks: RefCell::default(), text: RefCell::new("\0".into()) });
        a.set_text(text);
        a
    }

    pub fn set_text(&self, text: &str) {
        if *self.text.borrow() == text && !self.blocks.borrow().is_empty() {
            return;
        }
        self.text.replace(text.to_string());
        let new = markdown::blocks(text);
        for (i, b) in new.iter().enumerate() {
            if i < self.blocks.borrow().len() {
                let (old_kind, same_block) = {
                    let bs = self.blocks.borrow();
                    (bs[i].0.kind, bs[i].0 == *b)
                };
                if same_block {
                    continue;
                }
                if old_kind == b.kind {
                    // same kind: new text only
                    let mut bs = self.blocks.borrow_mut();
                    Self::fill(bs[i].2.as_ref(), b);
                    bs[i].0 = b.clone();
                    continue;
                }
                self.drop_from(i);
            }
            let (w, lab) = Self::make(b);
            self.root.append(&w);
            self.blocks.borrow_mut().push((b.clone(), w, lab));
        }
        self.drop_from(new.len());
    }

    pub fn text(&self) -> String {
        self.text.borrow().clone()
    }

    fn drop_from(&self, i: usize) {
        let mut bs = self.blocks.borrow_mut();
        for (_, w, _) in bs.iter().skip(i) {
            self.root.remove(w);
        }
        bs.truncate(i);
    }

    fn make(b: &Block) -> (gtk::Widget, Option<gtk::Label>) {
        match b.kind {
            "hr" => (gtk::Box::builder().css_classes(["as-rule"]).build().upcast(), None),
            "code" => {
                let lab = gtk::Label::builder().xalign(0.0).selectable(true).can_focus(false).wrap(false).build();
                let frame = gtk::ScrolledWindow::builder().child(&lab).css_classes(["as-code"]).vscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).build();
                Self::fill(Some(&lab), b);
                (frame.upcast(), Some(lab))
            }
            kind => {
                let css = match kind {
                    "h1" => Some("as-h1"),
                    "h2" => Some("as-h2"),
                    "h3" => Some("as-h3"),
                    "quote" => Some("as-quote"),
                    _ => None,
                };
                let lab = label("", css);
                Self::fill(Some(&lab), b);
                (lab.clone().upcast(), Some(lab))
            }
        }
    }

    fn fill(lab: Option<&gtk::Label>, b: &Block) {
        let Some(lab) = lab else { return };
        if b.kind == "code" {
            lab.set_text(&b.text);
        } else {
            lab.set_markup(&markdown::inline(&b.text));
        }
    }
}

fn tool_icon(name: &str) -> &'static str {
    match name {
        "list_folder" => "folder-symbolic",
        "read_file" => "text-x-generic-symbolic",
        "create_file" => "document-new-symbolic",
        _ => "dialog-information-symbolic",
    }
}

/// A file action in the chat: waiting for your OK, done, or not allowed.
pub struct ToolRow {
    pub root: gtk::Box,
    pub label: gtk::Label,
}

impl ToolRow {
    fn new(name: &str, text: &str, state: &str) -> Rc<Self> {
        let root = gtk::Box::builder().spacing(6).css_classes(["as-tool"]).build();
        let img = gtk::Image::from_icon_name(tool_icon(name));
        img.set_pixel_size(14);
        root.append(&img);
        let label = gtk::Label::builder().label(text).xalign(0.0).ellipsize(gtk::pango::EllipsizeMode::Middle).hexpand(true).build();
        root.append(&label);
        let r = Rc::new(Self { root, label });
        r.set_state(state, None);
        r
    }

    pub fn set_state(&self, state: &str, text: Option<&str>) {
        for c in ["as-wait", "as-denied"] {
            self.root.remove_css_class(c);
        }
        if state == "wait" || state == "denied" {
            self.root.add_css_class(&format!("as-{state}"));
        }
        if let Some(t) = text {
            self.label.set_text(t);
        }
    }
}

fn summary_of(blk: &Value) -> String {
    tools::Action::of_block(blk).summary()
}

fn waiting_text(blk: &Value) -> String {
    fmt(&tr!("Waiting for your OK: {action}"), &[("action", &summary_of(blk))])
}

fn not_allowed_text(summary: &str) -> String {
    fmt(&tr!("Not allowed: {action}"), &[("action", summary)])
}

fn couldnt_text(summary: &str) -> String {
    fmt(&tr!("Couldn’t do: {action}"), &[("action", summary)])
}

fn block_id(blk: &Value) -> String {
    blk.get("id").and_then(Value::as_str).unwrap_or("").to_string()
}

/// File actions waiting for your OK, asked one at a time.
struct Pending {
    chat: ChatRc,
    queue: VecDeque<Value>,
    results: Vec<Value>,
    rows: HashMap<String, Rc<ToolRow>>,
    current: Option<Value>,
    stopped: bool,
    running: bool,
}

type PendingRc = Rc<RefCell<Pending>>;

/// The settings panel's parts that change after it opens.
struct SettingsParts {
    panel: gtk::Popover,
    key_field: gtk::PasswordEntry,
    key_caption: gtk::Label,
    model_row: gtk::Box,
    folders_box: gtk::Box,
}

pub struct AssistantWindow {
    pub window: gtk::ApplicationWindow,
    cfg: RefCell<Map<String, Value>>,
    store: RefCell<Store>,
    usage: RefCell<Usage>,
    /// the open conversation (with its messages)
    chat: RefCell<Option<ChatRc>>,
    /// the answer being written...
    job: RefCell<Option<Rc<api::Job>>>,
    /// ...and its conversation (also while its file actions wait)
    job_chat: RefCell<Option<ChatRc>>,
    /// its text so far
    partial: RefCell<String>,
    /// its Answer widget, when that chat is shown
    live: RefCell<Option<Rc<AnswerView>>>,
    /// the spinner before the first words
    typing: RefCell<Option<gtk::Widget>>,
    pending: RefCell<Option<PendingRc>>,
    alert: RefCell<Option<adw::AlertDialog>>,
    /// follow the end while answers grow
    stick: Cell<bool>,
    syncing: Cell<bool>,
    tick: RefCell<Option<glib::SourceId>>,
    tool_rows: RefCell<HashMap<String, Rc<ToolRow>>>,
    settings: RefCell<Option<SettingsParts>>,
    settings_btn: gtk::Button,
    sidebar: gtk::Box,
    side: gtk::ListBox,
    /// the sidebar's chat rows: (row, chat id, its title)
    rows: RefCell<Vec<(gtk::ListBoxRow, String, gtk::EditableLabel)>>,
    messages: gtk::Box,
    empty: gtk::Label,
    send_btn: gtk::Button,
    composer: sonata_ui::controls::TextArea,
    meters: Vec<(gtk::ProgressBar, gtk::Label, gtk::Box)>,
    me: Weak<AssistantWindow>,
}

thread_local! {
    static WINDOWS: RefCell<Vec<Rc<AssistantWindow>>> = const { RefCell::new(Vec::new()) };
}

/// One Assistant window: opening it again brings it forward.
pub fn present(app: &gtk::Application) {
    let existing = WINDOWS.with(|w| w.borrow().first().cloned());
    match existing {
        Some(w) => w.window.present(),
        None => {
            let w = AssistantWindow::new(app, Store::open(crate::store::data_dir()));
            WINDOWS.with(|l| l.borrow_mut().push(w.clone()));
            w.window.present();
        }
    }
}

impl AssistantWindow {
    pub fn new(app: &gtk::Application, store: Store) -> Rc<Self> {
        let cfg = sonata_config::load("assistant", &defaults());
        let usage = Usage::open(&store.folder);
        let me = Rc::new_cyclic(|me: &Weak<AssistantWindow>| {
            let window = gtk::ApplicationWindow::builder().application(app).title(tr!("Assistant")).build();
            // its last size; the first time roomy, about two thirds of the
            // display (ui.window.remember_size(roomy=True), 0.20.3)
            sonata_ui::window::remember_size_roomy(window.upcast_ref(), "assistant", 1000, 680);
            window.add_css_class("sonata-assistant");
            window.set_size_request(600, 400);
            sonata_ui::window::standard(&window);
            let call = |f: fn(&Rc<AssistantWindow>)| -> Box<dyn Fn()> {
                let w = me.clone();
                Box::new(move || {
                    if let Some(t) = w.upgrade() {
                        f(&t);
                    }
                })
            };
            let (toolbar, bar, tools) = sonata_ui::window::glass_toolbar(&window, vec![("emblem-system-symbolic", tr!("Settings"), call(|t| t.show_settings())), ("document-edit-symbolic", tr!("New Chat"), call(|t| t.new_chat()))]);
            let side_btn = gtk::Button::builder().icon_name("sidebar-show-symbolic").tooltip_text(tr!("Show Sidebar")).can_focus(false).build();
            side_btn.add_css_class("tool");
            let f = call(|t| t.sidebar.set_visible(!t.sidebar.get_visible()));
            side_btn.connect_clicked(move |_| f());
            let start = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
            start.append(&side_btn);
            bar.set_start_widget(Some(&start));

            // -- sidebar
            let sidebar = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["as-sidebar", "sonata-sidebar"]).build();
            sidebar.set_size_request(180, -1);
            let side = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Single).build();
            side.set_placeholder(Some(&gtk::Label::builder().label(tr!("No Conversations")).css_classes(["as-sidebar-empty"]).build()));
            let w = me.clone();
            side.connect_row_selected(move |_, row| {
                if let (Some(t), Some(row)) = (w.upgrade(), row) {
                    t.side_selected(row);
                }
            });
            let menu = gtk::GestureClick::builder().button(3).build();
            let w = me.clone();
            menu.connect_pressed(move |_, _, x, y| {
                if let Some(t) = w.upgrade() {
                    t.side_menu(x, y);
                }
            });
            side.add_controller(menu);
            sidebar.append(&gtk::ScrolledWindow::builder().child(&side).vexpand(true).hscrollbar_policy(gtk::PolicyType::Never).build());

            // -- chat
            let messages = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["as-column"]).build();
            let scroll = gtk::ScrolledWindow::builder().hscrollbar_policy(gtk::PolicyType::Never).vexpand(true).build();
            scroll.set_child(Some(&sonata_ui::fixed::MaxWidth::new(&messages, COLUMN_W)));
            let adj = scroll.vadjustment();
            let w = me.clone();
            adj.connect_value_changed(move |a| {
                if let Some(t) = w.upgrade() {
                    t.stick.set(a.value() >= a.upper() - a.page_size() - 40.0);
                }
            });
            let w = me.clone();
            adj.connect_changed(move |a| {
                if w.upgrade().is_some_and(|t| t.stick.get()) {
                    a.set_value(a.upper() - a.page_size());
                }
            });
            let empty = gtk::Label::builder().label(tr!("Start a conversation")).css_classes(["as-empty"]).can_target(false).halign(gtk::Align::Center).valign(gtk::Align::Center).build();
            let over = gtk::Overlay::builder().child(&scroll).vexpand(true).build();
            over.add_overlay(&empty);

            let w = me.clone();
            let send_btn = sonata_ui::controls::round_button("go-up-symbolic", &tr!("Send"), move || {
                if let Some(t) = w.upgrade() {
                    if t.busy_here() {
                        t.stop(true);
                    } else {
                        t.send();
                    }
                }
            });
            send_btn.set_sensitive(false);
            let (w1, w2) = (me.clone(), me.clone());
            let composer = sonata_ui::controls::TextArea::new(
                &tr!("Message"),
                COMPOSER_MAX_H,
                Some(send_btn.upcast_ref()),
                move |_| {
                    if let Some(t) = w1.upgrade() {
                        t.send();
                    }
                },
                move |_| {
                    if let Some(t) = w2.upgrade() {
                        t.update_send();
                    }
                },
            );
            let meters_box = gtk::Box::builder().spacing(16).homogeneous(true).css_classes(["as-meters"]).build();
            let mut meters = Vec::new();
            for title in [tr!("Context"), tr!("Last Hour"), tr!("Last 7 Days")] {
                let head = gtk::Box::new(gtk::Orientation::Horizontal, 6);
                head.append(&gtk::Label::builder().label(title).xalign(0.0).hexpand(true).css_classes(["as-meter-title"]).build());
                let value = gtk::Label::builder().css_classes(["as-meter-value"]).build();
                head.append(&value);
                let meter = sonata_ui::progress::meter(0.0);
                let col = gtk::Box::new(gtk::Orientation::Vertical, 3);
                col.append(&head);
                col.append(&meter);
                meters_box.append(&col);
                meters.push((meter, value, col));
            }
            let stack = gtk::Box::new(gtk::Orientation::Vertical, 0);
            stack.append(&meters_box);
            stack.append(&composer.root);
            let cbar = gtk::Box::builder().css_classes(["as-composer-bar"]).build();
            cbar.append(&sonata_ui::fixed::MaxWidth::new(&stack, COLUMN_W));
            let pane = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["as-chat"]).build();
            pane.set_size_request(320, -1);
            pane.append(&over);
            pane.append(&cbar);

            let paned = gtk::Paned::builder().start_child(&sidebar).end_child(&pane).shrink_start_child(false).resize_start_child(false).shrink_end_child(false).css_classes(["as-paned"]).vexpand(true).build();
            paned.set_position(SIDEBAR_W);
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            col.append(&toolbar);
            col.append(&paned);
            window.set_child(Some(&col));

            AssistantWindow {
                window,
                cfg: RefCell::new(cfg),
                store: RefCell::new(store),
                usage: RefCell::new(usage),
                chat: RefCell::default(),
                job: RefCell::default(),
                job_chat: RefCell::default(),
                partial: RefCell::default(),
                live: RefCell::default(),
                typing: RefCell::default(),
                pending: RefCell::default(),
                alert: RefCell::default(),
                stick: Cell::new(true),
                syncing: Cell::new(false),
                tick: RefCell::default(),
                tool_rows: RefCell::default(),
                settings: RefCell::default(),
                settings_btn: tools[0].clone(),
                sidebar,
                side,
                rows: RefCell::default(),
                messages,
                empty,
                send_btn,
                composer,
                meters,
                me: me.clone(),
            }
        });

        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let w = me.me.clone();
        keys.connect_key_pressed(move |_, key, _, state| match w.upgrade() {
            Some(t) if t.key(key, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        me.window.add_controller(keys);
        let w = me.me.clone();
        me.window.connect_close_request(move |_| {
            if let Some(t) = w.upgrade() {
                t.close_request();
            }
            glib::Propagation::Proceed
        });
        me.rebuild_sidebar();
        let selected = me.cfg.borrow().get("selected").and_then(Value::as_str).unwrap_or("").to_string();
        if !selected.is_empty() && me.store.borrow().get(&selected).is_some() {
            me.open_chat(&selected);
        } else {
            me.new_chat();
        }
        // read the key early (a locked keyring may ask), then the models' context windows
        let w = me.me.clone();
        keystore::load(move |k| {
            if !k.is_empty() {
                api::models_async(&k, move |_| {
                    if let Some(t) = w.upgrade() {
                        t.update_meters();
                    }
                });
            }
        });
        // the hour and week roll on
        let w = me.me.clone();
        let id = glib::timeout_add_seconds_local(60, move || match w.upgrade() {
            Some(t) => {
                t.update_meters();
                glib::ControlFlow::Continue
            }
            None => glib::ControlFlow::Break,
        });
        me.tick.replace(Some(id));
        me
    }

    fn cfg_str(&self, key: &str) -> String {
        self.cfg.borrow().get(key).and_then(Value::as_str).unwrap_or("").to_string()
    }

    fn folders(&self) -> Vec<String> {
        self.cfg.borrow().get("folders").and_then(Value::as_array).map(|a| a.iter().filter_map(Value::as_str).filter(|s| !s.is_empty()).map(str::to_string).collect()).unwrap_or_default()
    }

    fn set_cfg(&self, key: &str, value: Value) {
        self.cfg.borrow_mut().insert(key.into(), value.clone());
        let _ = sonata_config::update("assistant", &json!({ key: value }));
    }

    fn chat_id(&self) -> Option<String> {
        self.chat.borrow().as_ref().map(|c| cid_of(&c.borrow()))
    }

    // -- sidebar ----------------------------------------------------------------------------------
    pub fn rebuild_sidebar(&self) {
        self.syncing.set(true);
        self.side.remove_all();
        self.rows.borrow_mut().clear();
        let mut section: Option<(i32, i32, String)> = None;
        let chats = self.store.borrow().chats.clone();
        let t = now();
        for meta in &chats {
            let modified = meta.get("modified").and_then(Value::as_f64).unwrap_or(0.0);
            let sec = sonata_ui::fmt::date_section(modified, false, t);
            if section.as_ref() != Some(&sec) {
                let head = gtk::ListBoxRow::builder().selectable(false).activatable(false).can_focus(false).css_classes(["as-head"]).build();
                head.set_child(Some(&gtk::Label::builder().label(&sec.2).xalign(0.0).build()));
                self.side.append(&head);
                section = Some(sec);
            }
            let cid = cid_of(meta);
            let row = gtk::ListBoxRow::new();
            let bx = gtk::Box::new(gtk::Orientation::Horizontal, 8);
            let name = gtk::EditableLabel::builder().text(chat_title(Some(meta))).hexpand(true).editable(false).build();
            // the label inside: ellipsize long titles
            if let Some(inner) = name.first_child().and_downcast::<gtk::Stack>().and_then(|s| s.first_child()).and_downcast::<gtk::Label>() {
                inner.set_ellipsize(gtk::pango::EllipsizeMode::End);
                inner.set_xalign(0.0);
            }
            bx.append(&name);
            bx.append(&gtk::Label::builder().label(sonata_ui::fmt::short_date(modified, t)).css_classes(["as-date"]).build());
            row.set_child(Some(&bx));
            let w = self.me.clone();
            let c = cid.clone();
            name.connect_editing_notify(move |l| {
                if let Some(t) = w.upgrade() {
                    t.renamed(l, &c);
                }
            });
            self.side.append(&row);
            self.rows.borrow_mut().push((row, cid, name));
        }
        let cur = self.chat_id().and_then(|id| self.row(&id));
        self.side.select_row(cur.as_ref());
        self.syncing.set(false);
    }

    fn row(&self, cid: &str) -> Option<gtk::ListBoxRow> {
        self.rows.borrow().iter().find(|r| r.1 == cid).map(|r| r.0.clone())
    }

    fn cid_of_row(&self, row: &gtk::ListBoxRow) -> Option<String> {
        self.rows.borrow().iter().find(|r| &r.0 == row).map(|r| r.1.clone())
    }

    fn side_selected(&self, row: &gtk::ListBoxRow) {
        if self.syncing.get() {
            return;
        }
        let Some(cid) = self.cid_of_row(row) else { return };
        if self.chat_id().as_deref() != Some(cid.as_str()) {
            self.open_chat(&cid);
        }
    }

    fn side_menu(&self, x: f64, y: f64) {
        let Some(row) = self.side.row_at_y(y as i32) else { return };
        let Some(cid) = self.cid_of_row(&row) else { return };
        let (w1, w2, c1, c2) = (self.me.clone(), self.me.clone(), cid.clone(), cid);
        sonata_ui::menu::popup(
            &self.side,
            vec![
                vec![sonata_ui::menu::Item::new(&tr!("Rename"), move || {
                    if let Some(t) = w1.upgrade() {
                        t.rename(&c1);
                    }
                })],
                vec![sonata_ui::menu::Item::new(&tr!("Delete…"), move || {
                    if let Some(t) = w2.upgrade() {
                        t.delete_chat(&c2);
                    }
                })],
            ],
            sonata_ui::menu::Options::at(x, y),
        );
    }

    pub fn rename(&self, cid: &str) {
        let name = self.rows.borrow().iter().find(|r| r.1 == cid).map(|r| r.2.clone());
        if let Some(n) = name {
            n.set_editable(true);
            n.start_editing();
        }
    }

    fn renamed(&self, label: &gtk::EditableLabel, cid: &str) {
        if label.is_editing() {
            return;
        }
        label.set_editable(false);
        let shown = chat_title(self.store.borrow().get(cid));
        if label.text() != shown {
            // unchanged: "New Chat" stays new
            self.store.borrow_mut().rename(cid, &label.text());
        }
        let meta = self.store.borrow().get(cid).cloned();
        if let Some(meta) = meta {
            let title = chat_title(Some(&meta));
            if label.text() != title {
                label.set_text(&title);
            }
            if let Some(c) = self.chat.borrow().as_ref().filter(|c| cid_of(&c.borrow()) == cid) {
                c.borrow_mut().insert("title".into(), meta.get("title").cloned().unwrap_or(Value::Null));
            }
        }
    }

    pub fn delete_chat(&self, cid: &str) {
        let Some(meta) = self.store.borrow().get(cid).cloned() else { return };
        let w = self.me.clone();
        let cid = cid.to_string();
        sonata_ui::dialog::alert(
            &fmt(&tr!("Delete “{name}”?"), &[("name", &chat_title(Some(&meta)))]),
            &tr!("This conversation will be deleted. You can’t undo this."),
            &[("cancel", tr!("Cancel"), ""), ("delete", tr!("Delete"), "destructive")],
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                if rid != "delete" {
                    return;
                }
                if t.job_chat.borrow().as_ref().is_some_and(|c| cid_of(&c.borrow()) == cid) {
                    t.stop(false);
                }
                t.store.borrow_mut().delete(&cid);
                if t.chat_id().as_deref() == Some(cid.as_str()) {
                    t.new_chat();
                } else {
                    t.rebuild_sidebar();
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    // -- settings ---------------------------------------------------------------------------------
    /// The settings panel: API key, model, usage limits, folders Claude may use.
    pub fn show_settings(&self) {
        let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["as-settings"]).build();
        bx.append(&sonata_ui::panel::header(&tr!("Assistant Settings"), None));
        bx.append(&sonata_ui::panel::section_title(&tr!("Anthropic API Key")));
        let key_field = sonata_ui::controls::secret_field(&keystore::cached(), "sk-ant-…");
        key_field.set_hexpand(true);
        let w = self.me.clone();
        key_field.connect_activate(move |_| {
            if let Some(t) = w.upgrade() {
                t.save_key();
            }
        });
        let w = self.me.clone();
        let save = sonata_ui::controls::push_button(
            &tr!("Save"),
            move || {
                if let Some(t) = w.upgrade() {
                    t.save_key();
                }
            },
            "",
        );
        let row = gtk::Box::builder().spacing(6).css_classes(["as-field-row"]).build();
        row.append(&key_field);
        row.append(&save);
        bx.append(&row);
        let key_caption = gtk::Label::builder().xalign(0.0).wrap(true).max_width_chars(40).css_classes(["panel-caption"]).build();
        bx.append(&key_caption);

        bx.append(&sonata_ui::panel::section_title(&tr!("Model")));
        let model_row = gtk::Box::builder().spacing(6).css_classes(["as-field-row"]).build();
        bx.append(&model_row);

        bx.append(&sonata_ui::panel::section_title(&tr!("Usage Limits")));
        for (key, title, options) in [("hourly_limit", tr!("Per Hour"), usage::HOURLY), ("weekly_limit", tr!("Per Week"), usage::WEEKLY)] {
            let cur = self.cfg.borrow().get(key).and_then(Value::as_i64).filter(|n| *n > 0).unwrap_or(options[0]);
            let mut opts: Vec<i64> = options.to_vec();
            if !opts.contains(&cur) {
                opts.push(cur);
            }
            opts.sort();
            let names: Vec<String> = opts.iter().map(|o| fmt(&tr!("{count} tokens"), &[("count", &sonata_ui::fmt::count(*o))])).collect();
            let w = self.me.clone();
            let chosen = opts.clone();
            let dd = sonata_ui::controls::popup_button(&names, opts.iter().position(|o| *o == cur).unwrap_or(0) as u32, move |i| {
                if let (Some(t), Some(v)) = (w.upgrade(), chosen.get(i as usize)) {
                    t.set_cfg(key, json!(v));
                    t.update_meters();
                }
            });
            let row = gtk::Box::builder().spacing(6).css_classes(["as-field-row"]).build();
            row.append(&gtk::Label::builder().label(title).xalign(0.0).hexpand(true).build());
            row.append(&dd);
            bx.append(&row);
        }

        bx.append(&sonata_ui::panel::separator());
        bx.append(&sonata_ui::panel::section_title(&tr!("Folders Claude Can Use")));
        let folders_box = gtk::Box::new(gtk::Orientation::Vertical, 0);
        bx.append(&folders_box);
        let w = self.me.clone();
        let (add, _) = sonata_ui::panel::row(
            Some("list-add-symbolic"),
            &tr!("Add Folder…"),
            None,
            Some(Box::new(move || {
                if let Some(t) = w.upgrade() {
                    t.add_folder();
                }
            })),
        );
        bx.append(&add);
        bx.append(&gtk::Label::builder().label(tr!("Claude can list, read and create files only in these folders, and asks before every action.")).xalign(0.0).wrap(true).max_width_chars(40).css_classes(["panel-caption"]).build());
        // a fixed width (a long folder or model name ellipsizes instead of
        // stretching the panel) and the close dot of forms (panel.popup(width=360, close=True))
        let panel = sonata_ui::panel::popup_with(&self.settings_btn, &bx, sonata_ui::panel::Options { width: Some(360), close: true, ..Default::default() });
        self.settings.replace(Some(SettingsParts { panel, key_field, key_caption, model_row, folders_box }));
        self.key_status(None);
        let key = keystore::cached();
        self.fill_models(None, !key.is_empty(), false);
        if !key.is_empty() {
            let w = self.me.clone();
            api::models_async(&key, move |found| {
                if let Some(t) = w.upgrade() {
                    t.fill_models(found, false, false);
                }
            });
        }
        self.fill_folders();
    }

    fn key_status(&self, text: Option<String>) {
        let Some(s) = self.settings.borrow().as_ref().map(|s| s.key_caption.clone()) else { return };
        let text = text.unwrap_or_else(|| {
            let env = std::env::var("ANTHROPIC_API_KEY").unwrap_or_default().trim().to_string();
            let key = keystore::cached();
            if !key.is_empty() && key != env {
                tr!("Saved in your keyring.")
            } else if !key.is_empty() {
                tr!("Using the ANTHROPIC_API_KEY environment variable.")
            } else {
                tr!("Create a key at console.anthropic.com.")
            }
        });
        s.set_label(&text);
    }

    fn save_key(&self) {
        let Some(field) = self.settings.borrow().as_ref().map(|s| s.key_field.clone()) else { return };
        let key = field.text().trim().to_string();
        let w = self.me.clone();
        keystore::save(&key.clone(), move |ok| {
            let Some(t) = w.upgrade() else { return };
            if !ok {
                t.key_status(Some(tr!("Couldn’t save the key: no keyring is running.")));
                return;
            }
            t.key_status(None);
            t.fill_models(None, !key.is_empty(), false);
            if !key.is_empty() {
                let w = t.me.clone();
                api::models_async(&key, move |found| {
                    if let Some(t) = w.upgrade() {
                        let failed = found.is_none();
                        t.fill_models(found, false, failed);
                    }
                });
            }
        });
    }

    fn fill_models(&self, found: Option<Vec<(String, String)>>, loading: bool, failed: bool) {
        let Some(row) = self.settings.borrow().as_ref().map(|s| s.model_row.clone()) else { return };
        while let Some(c) = row.first_child() {
            row.remove(&c);
        }
        let found = found.unwrap_or_default();
        let mut ids: Vec<String> = vec![String::new()];
        let mut names: Vec<String> = vec![tr!("Newest Available")];
        for (id, name) in &found {
            ids.push(id.clone());
            names.push(name.clone());
        }
        let cur = self.cfg_str("model");
        if !cur.is_empty() && !ids.contains(&cur) {
            // chosen before; not listed (yet)
            ids.push(cur.clone());
            names.push(cur.clone());
        }
        let w = self.me.clone();
        let sel = ids.iter().position(|i| *i == cur).unwrap_or(0) as u32;
        let ids2 = ids.clone();
        let dd = sonata_ui::controls::popup_button(&names, sel, move |i| {
            let Some(t) = w.upgrade() else { return };
            if let Some(id) = ids2.get(i as usize).filter(|id| **id != t.cfg_str("model")) {
                t.set_cfg("model", json!(id));
            }
        });
        dd.set_hexpand(true);
        dd.set_sensitive(!found.is_empty() || !cur.is_empty());
        row.append(&dd);
        if loading {
            row.append(&sonata_ui::progress::Spinner::new(true));
        } else if failed {
            row.append(&gtk::Label::builder().label(tr!("Couldn’t load")).css_classes(["panel-caption"]).build());
        }
    }

    fn fill_folders(&self) {
        let Some(bx) = self.settings.borrow().as_ref().map(|s| s.folders_box.clone()) else { return };
        while let Some(c) = bx.first_child() {
            bx.remove(&c);
        }
        let home = glib::home_dir().display().to_string();
        for f in self.folders() {
            let shown = if f == home || f.starts_with(&format!("{home}/")) { format!("~{}", &f[home.len()..]) } else { f.clone() };
            let w = self.me.clone();
            let path = f.clone();
            let rm = sonata_ui::controls::push_button(
                &tr!("Remove"),
                move || {
                    if let Some(t) = w.upgrade() {
                        t.remove_folder(&path);
                    }
                },
                "",
            );
            let (row, _) = sonata_ui::panel::row(Some("folder-symbolic"), &shown, Some(rm.upcast_ref()), None);
            row.add_css_class("as-folder-row");
            row.set_tooltip_text(Some(&f));
            bx.append(&row);
        }
    }

    /// A folder for Claude (the chooser the session gives: Sonata's own
    /// through the portal).
    pub fn add_folder(&self) {
        if let Some(p) = self.settings.borrow().as_ref().map(|s| s.panel.clone()) {
            p.popdown();
        }
        let dlg = gtk::FileDialog::builder().title(tr!("Choose a Folder for Claude")).accept_label(tr!("Allow")).modal(true).build();
        let w = self.me.clone();
        dlg.select_folder(Some(&self.window), gio::Cancellable::NONE, move |res| {
            let (Some(t), Ok(file)) = (w.upgrade(), res) else { return };
            let Some(path) = file.path().map(|p| p.display().to_string()) else { return };
            let mut folders = t.folders();
            if !folders.contains(&path) {
                folders.push(path);
                t.set_cfg("folders", json!(folders));
            }
        });
    }

    pub fn remove_folder(&self, path: &str) {
        let folders: Vec<String> = self.folders().into_iter().filter(|f| f != path).collect();
        self.set_cfg("folders", json!(folders));
        self.fill_folders();
    }

    // -- chat -------------------------------------------------------------------------------------
    /// Context of the open chat; tokens of the last hour and week.
    pub fn update_meters(&self) {
        let (model, used) = match self.chat.borrow().as_ref() {
            Some(c) => {
                let c = c.borrow();
                (c.get("model").and_then(Value::as_str).unwrap_or("").to_string(), c.get("context_tokens").and_then(Value::as_i64).unwrap_or(0))
            }
            None => (String::new(), 0),
        };
        let model = if model.is_empty() { self.cfg_str("model") } else { model };
        let t = now();
        let limit = |k: &str| self.cfg.borrow().get(k).and_then(Value::as_i64).filter(|n| *n > 0).unwrap_or(1);
        let rows = [
            (used, api::context_window(&model), tr!("This conversation’s share of the model’s context window")),
            (self.usage.borrow().last_hour(t), limit("hourly_limit"), tr!("Tokens used in the last hour, against your limit (Settings)")),
            (self.usage.borrow().last_week(t), limit("weekly_limit"), tr!("Tokens used in the last 7 days, against your limit (Settings)")),
        ];
        for ((n, lim, tip), (meter, value, col)) in rows.iter().zip(&self.meters) {
            sonata_ui::progress::set_meter(meter, *n as f64 / (*lim).max(1) as f64);
            value.set_label(&format!("{} / {}", sonata_ui::fmt::count(*n), sonata_ui::fmt::count(*lim)));
            col.set_tooltip_text(Some(&fmt(&tr!("{what}: {n} of {limit} tokens"), &[("what", tip), ("n", &grouped(*n)), ("limit", &grouped(*lim))])));
        }
    }

    fn clear(&self) {
        while let Some(c) = self.messages.first_child() {
            self.messages.remove(&c);
        }
        self.live.replace(None);
        self.tool_rows.borrow_mut().clear();
    }

    fn add(&self, widget: &impl IsA<gtk::Widget>, animate: bool) {
        if animate {
            self.messages.append(&appear(widget));
        } else {
            self.messages.append(widget);
        }
        self.empty.set_visible(false);
    }

    /// Take a message's widget out of the chat (with its revealer).
    fn remove_shown(&self, widget: &impl IsA<gtk::Widget>) {
        let w = widget.upcast_ref::<gtk::Widget>();
        match w.parent() {
            Some(p) if p == *self.messages.upcast_ref::<gtk::Widget>() => self.messages.remove(w),
            Some(p) if p.parent().as_ref() == Some(self.messages.upcast_ref()) => self.messages.remove(&p),
            _ => {}
        }
    }

    fn add_message(&self, msg: &Value, animate: bool, results: &HashMap<String, bool>) {
        let content = msg.get("content").cloned().unwrap_or(json!(""));
        let text = api::text_of(&content);
        if msg.get("role").and_then(Value::as_str) == Some("user") {
            if text.trim().is_empty() {
                return; // only file-action results: shown on their rows
            }
            let lab = label(&glib::markup_escape_text(&text), Some("as-user"));
            lab.set_halign(gtk::Align::End);
            self.add(&lab, animate);
            return;
        }
        if !text.trim().is_empty() {
            self.add(&AnswerView::new(&text).root, animate);
        }
        for blk in content.as_array().into_iter().flatten() {
            if blk.get("type").and_then(Value::as_str) != Some("tool_use") {
                continue;
            }
            let act = tools::Action::of_block(blk);
            let denied = results.get(&block_id(blk)).copied().unwrap_or(false);
            let row = ToolRow::new(&act.name, &if denied { not_allowed_text(&act.summary()) } else { act.summary() }, if denied { "denied" } else { "done" });
            self.add(&row.root, animate);
            self.tool_rows.borrow_mut().insert(block_id(blk), row);
        }
    }

    pub fn open_chat(&self, cid: &str) {
        // still being answered: the live copy
        let live = self.job_chat.borrow().clone().filter(|c| cid_of(&c.borrow()) == cid);
        let chat = live.unwrap_or_else(|| Rc::new(RefCell::new(self.store.borrow().load(cid))));
        self.show(chat);
    }

    pub fn new_chat(&self) {
        let fresh = self.chat.borrow().as_ref().is_some_and(|c| {
            let c = c.borrow();
            messages_of(&c).is_empty() && self.store.borrow().get(&cid_of(&c)).is_none()
        });
        if fresh {
            self.composer.grab_focus(); // already a fresh chat
            return;
        }
        let chat = self.store.borrow().new_chat();
        self.show(Rc::new(RefCell::new(chat)));
    }

    fn show(&self, chat: ChatRc) {
        self.chat.replace(Some(chat.clone()));
        self.clear();
        let msgs = messages_of(&chat.borrow());
        // tool_use id -> refused (from the tool_result blocks)
        let mut results = HashMap::new();
        for m in &msgs {
            if m["role"] == "user" {
                for b in m["content"].as_array().into_iter().flatten() {
                    if b.get("type").and_then(Value::as_str) == Some("tool_result") {
                        results.insert(b.get("tool_use_id").and_then(Value::as_str).unwrap_or("").to_string(), b.get("is_error").and_then(Value::as_bool).unwrap_or(false));
                    }
                }
            }
        }
        for m in &msgs {
            self.add_message(m, false, &results);
        }
        let current = Some(chat.clone());
        if self.job.borrow().is_some() && same(&self.job_chat.borrow(), &current) {
            let live = AnswerView::new(&self.partial.borrow());
            self.add(&live.root, false);
            self.live.replace(Some(live));
        }
        if let Some(p) = self.pending.borrow().clone() {
            let mut p = p.borrow_mut();
            p.rows.clear();
            if Rc::ptr_eq(&p.chat, &chat) {
                // its waiting actions: back on their rows
                let blks: Vec<Value> = p.current.iter().cloned().chain(p.queue.iter().cloned()).collect();
                for blk in blks {
                    if let Some(row) = self.tool_rows.borrow().get(&block_id(&blk)) {
                        row.set_state("wait", Some(&waiting_text(&blk)));
                        p.rows.insert(block_id(&blk), row.clone());
                    }
                }
            }
        }
        self.empty.set_visible(msgs.is_empty());
        self.stick.set(true);
        self.syncing.set(true);
        let row = self.row(&cid_of(&chat.borrow()));
        self.side.select_row(row.as_ref());
        self.syncing.set(false);
        let title = if msgs.is_empty() { tr!("Assistant") } else { chat_title(Some(&chat.borrow())) };
        self.window.set_title(Some(&title));
        self.update_send();
        self.update_meters();
        let w = self.me.clone();
        glib::idle_add_local_once(move || {
            if let Some(t) = w.upgrade() {
                t.composer.grab_focus();
            }
        });
    }

    // -- sending ----------------------------------------------------------------------------------
    fn busy(&self) -> bool {
        self.job.borrow().is_some() || self.pending.borrow().is_some()
    }

    pub fn busy_here(&self) -> bool {
        self.busy() && same(&self.job_chat.borrow(), &self.chat.borrow())
    }

    fn update_send(&self) {
        let here = self.busy_here();
        self.send_btn.set_icon_name(if here { "media-playback-stop-symbolic" } else { "go-up-symbolic" });
        self.send_btn.set_tooltip_text(Some(&if here { tr!("Stop") } else { tr!("Send") }));
        self.send_btn.set_sensitive(here || (!self.composer.text().trim().is_empty() && !self.busy()));
    }

    pub fn send(&self) {
        let text = self.composer.text().trim().to_string();
        let Some(chat) = self.chat.borrow().clone() else { return };
        if text.is_empty() || self.busy() {
            return;
        }
        self.composer.set_text("");
        let msg = json!({"role": "user", "content": text});
        push_message(&chat, msg.clone());
        self.add_message(&msg, true, &HashMap::new());
        self.stick.set(true);
        self.store.borrow_mut().save(&mut chat.borrow_mut());
        self.window.set_title(Some(&chat_title(Some(&chat.borrow()))));
        self.rebuild_sidebar();
        self.ask(chat);
    }

    /// Start the answer to the conversation as it is.
    fn ask(&self, chat: ChatRc) {
        let key = keystore::cached();
        let shown = same(&Some(chat.clone()), &self.chat.borrow());
        if key.is_empty() {
            self.job_chat.replace(None);
            if shown {
                let w = self.me.clone();
                self.error(
                    &tr!("Add your Anthropic API key in Settings to start chatting."),
                    false,
                    Some((
                        tr!("Open Settings"),
                        Box::new(move || {
                            if let Some(t) = w.upgrade() {
                                t.show_settings();
                            }
                        }),
                    )),
                );
            }
            self.update_send();
            return;
        }
        self.job_chat.replace(Some(chat.clone()));
        self.partial.replace(String::new());
        self.live.replace(None);
        self.typing.replace(None);
        if shown {
            let live = AnswerView::new("");
            let spin = sonata_ui::progress::Spinner::new(true);
            spin.set_halign(gtk::Align::Start);
            live.root.append(&spin);
            self.add(&live.root, true);
            self.typing.replace(Some(spin.upcast()));
            self.live.replace(Some(live));
        }
        let folders = self.folders();
        let req = api::Request {
            key,
            model: self.cfg_str("model"),
            messages: messages_of(&chat.borrow()),
            system: tools::system_prompt(&folders),
            tools: if folders.is_empty() { None } else { Some(tools::definitions()) },
        };
        let (w1, w2, w3) = (self.me.clone(), self.me.clone(), self.me.clone());
        let job = api::stream(
            req,
            move |piece| {
                if let Some(t) = w1.upgrade() {
                    t.on_text(piece);
                }
            },
            move |blocks, _stop, used| {
                if let Some(t) = w2.upgrade() {
                    t.on_done(blocks, used);
                }
            },
            move |e| {
                if let Some(t) = w3.upgrade() {
                    t.on_error(&e);
                }
            },
        );
        self.job.replace(Some(job));
        self.update_send();
    }

    fn on_text(&self, piece: &str) {
        self.partial.borrow_mut().push_str(piece);
        if !same(&self.job_chat.borrow(), &self.chat.borrow()) {
            return;
        }
        let Some(live) = self.live.borrow().clone() else { return };
        if let Some(t) = self.typing.take() {
            if t.parent().as_ref() == Some(live.root.upcast_ref()) {
                live.root.remove(&t);
            }
        }
        live.set_text(&self.partial.borrow());
    }

    /// The stream is over: (its chat, its text); the live widgets settle.
    fn end_stream(&self) -> (Option<ChatRc>, String) {
        let chat = self.job_chat.borrow().clone();
        let text = self.partial.take();
        self.job.replace(None);
        let live = self.live.take();
        if let (Some(t), Some(l)) = (self.typing.take(), live.as_ref()) {
            if t.parent().as_ref() == Some(l.root.upcast_ref()) {
                l.root.remove(&t);
            }
        }
        if let Some(l) = live.filter(|_| text.trim().is_empty()) {
            self.remove_shown(&l.root);
        }
        (chat, text)
    }

    fn save_chat(&self, chat: &ChatRc) {
        let known = self.store.borrow().get(&cid_of(&chat.borrow())).is_some();
        if known || same(&Some(chat.clone()), &self.chat.borrow()) {
            self.store.borrow_mut().save(&mut chat.borrow_mut());
            self.rebuild_sidebar();
        }
    }

    fn finish(&self, keep: bool) {
        let (chat, text) = self.end_stream();
        self.job_chat.replace(None);
        if let (true, false, Some(chat)) = (keep, text.trim().is_empty(), chat) {
            push_message(&chat, json!({"role": "assistant", "content": text}));
            self.save_chat(&chat);
        }
        self.update_send();
    }

    fn on_done(&self, blocks: Vec<Value>, used: Map<String, Value>) {
        let (chat, text) = self.end_stream();
        let Some(chat) = chat else {
            self.update_send();
            return;
        };
        let tokens = api::total_tokens(&used);
        self.usage.borrow_mut().add(tokens, now());
        {
            // the conversation's size after this answer
            let mut c = chat.borrow_mut();
            c.insert("context_tokens".into(), json!(tokens));
            if let Some(m) = used.get("model").and_then(Value::as_str).filter(|m| !m.is_empty()) {
                c.insert("model".into(), json!(m));
            }
        }
        let shown = same(&Some(chat.clone()), &self.chat.borrow());
        if shown {
            self.update_meters();
        }
        let uses: Vec<Value> = blocks.iter().filter(|b| b.get("type").and_then(Value::as_str) == Some("tool_use")).cloned().collect();
        if uses.is_empty() {
            self.job_chat.replace(None);
            let content = if blocks.is_empty() { text } else { api::text_of(&Value::Array(blocks)) };
            if !content.trim().is_empty() {
                push_message(&chat, json!({"role": "assistant", "content": content}));
                self.save_chat(&chat);
            }
            self.update_send();
            return;
        }
        push_message(&chat, json!({"role": "assistant", "content": blocks}));
        self.save_chat(&chat);
        let mut p = Pending { chat: chat.clone(), queue: uses.iter().cloned().collect(), results: Vec::new(), rows: HashMap::new(), current: None, stopped: false, running: false };
        if shown {
            for b in &uses {
                let row = ToolRow::new(b.get("name").and_then(Value::as_str).unwrap_or(""), &waiting_text(b), "wait");
                self.add(&row.root, true);
                p.rows.insert(block_id(b), row.clone());
                self.tool_rows.borrow_mut().insert(block_id(b), row);
            }
        }
        self.pending.replace(Some(Rc::new(RefCell::new(p))));
        self.update_send();
        self.next_tool();
    }

    fn is_pending(&self, p: &PendingRc) -> bool {
        self.pending.borrow().as_ref().is_some_and(|q| Rc::ptr_eq(q, p))
    }

    /// Ask about the next file action (one alert at a time).
    fn next_tool(&self) {
        let Some(p) = self.pending.borrow().clone() else { return };
        let next = p.borrow_mut().queue.pop_front();
        let Some(blk) = next else {
            self.pending.replace(None);
            let (chat, results, stopped) = {
                let p = p.borrow();
                (p.chat.clone(), p.results.clone(), p.stopped)
            };
            push_message(&chat, json!({"role": "user", "content": results}));
            self.save_chat(&chat);
            if stopped {
                self.job_chat.replace(None);
                self.update_send();
            } else {
                self.ask(chat);
            }
            return;
        };
        let row = p.borrow().rows.get(&block_id(&blk)).cloned();
        let name = blk.get("name").and_then(Value::as_str).unwrap_or("");
        let act = match tools::check(name, blk.get("input").unwrap_or(&Value::Null), &self.folders()) {
            Ok(a) => a,
            Err(e) => {
                self.tool_result(&p, &blk, &e, true, row.as_deref(), &couldnt_text(&summary_of(&blk)));
                self.next_tool();
                return;
            }
        };
        let base = act.path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_else(|| act.path.display().to_string());
        p.borrow_mut().current = Some(blk.clone());
        let mut body = act.path.display().to_string();
        if act.name == "create_file" {
            let content = act.args.get("content").and_then(Value::as_str).unwrap_or("");
            let n = content.matches('\n').count() + 1;
            body.push('\n');
            body.push_str(&fmt(&trn!("{n} line, {size}", "{n} lines, {size}", n), &[("n", &n.to_string()), ("size", &sonata_ui::fmt::size(content.len() as u64))]));
        }
        let question = match act.name.as_str() {
            "list_folder" => tr!("Allow Claude to see what’s in “{name}”?"),
            "read_file" => tr!("Allow Claude to read “{name}”?"),
            _ if act.exists => tr!("Allow Claude to replace “{name}”?"),
            _ => tr!("Allow Claude to create “{name}”?"),
        };
        let w = self.me.clone();
        let (p2, blk2) = (p.clone(), blk.clone());
        let alert = sonata_ui::dialog::alert(
            &fmt(&question, &[("name", &base)]),
            &body,
            &[("deny", tr!("Don’t Allow"), ""), ("allow", tr!("Allow"), if act.exists { "destructive" } else { "default" })],
            move |rid| {
                let Some(t) = w.upgrade() else { return };
                let current = p2.borrow().current.as_ref().map(block_id);
                if !t.is_pending(&p2) || current.as_deref() != Some(block_id(&blk2).as_str()) {
                    return;
                }
                t.alert.replace(None);
                p2.borrow_mut().current = None;
                let row = p2.borrow().rows.get(&block_id(&blk2)).cloned();
                if rid == "allow" {
                    // file I/O (up to 2 MB written / 256 KB read) off the main loop
                    p2.borrow_mut().running = true;
                    let (w, p3, blk3, act2) = (t.me.clone(), p2.clone(), blk2.clone(), act.clone());
                    glib::spawn_future_local(async move {
                        let job = act2.clone();
                        let (text, err) = gio::spawn_blocking(move || tools::run(&job)).await.unwrap_or_else(|_| ("The action failed.".into(), true));
                        p3.borrow_mut().running = false;
                        let Some(t) = w.upgrade() else { return };
                        if t.is_pending(&p3) {
                            let line = if err { couldnt_text(&act2.summary()) } else { act2.summary() };
                            t.tool_result(&p3, &blk3, &text, err, row.as_deref(), &line);
                            t.next_tool();
                        }
                    });
                    return;
                }
                t.tool_result(&p2, &blk2, "The user did not allow this.", true, row.as_deref(), &not_allowed_text(&act.summary()));
                t.next_tool();
            },
            Some(self.window.upcast_ref()),
        );
        self.alert.replace(Some(alert));
    }

    fn tool_result(&self, p: &PendingRc, blk: &Value, text: &str, is_error: bool, row: Option<&ToolRow>, line: &str) {
        let mut res = json!({"type": "tool_result", "tool_use_id": block_id(blk), "content": text});
        if is_error {
            res["is_error"] = json!(true);
        }
        p.borrow_mut().results.push(res);
        if let Some(r) = row {
            r.set_state(if is_error { "denied" } else { "done" }, Some(line));
        }
    }

    fn on_error(&self, message: &str) {
        let shown = same(&self.job_chat.borrow(), &self.chat.borrow());
        self.finish(true);
        if shown {
            self.error(message, true, None);
        }
    }

    fn error(&self, message: &str, retry: bool, button: Option<(String, Box<dyn Fn()>)>) {
        let bx = gtk::Box::builder().orientation(gtk::Orientation::Vertical).spacing(6).css_classes(["as-error"]).build();
        bx.append(&label(&glib::markup_escape_text(message), None));
        let button = if retry {
            let (w, b) = (self.me.clone(), bx.downgrade());
            Some((
                tr!("Try Again"),
                Box::new(move || {
                    if let (Some(t), Some(b)) = (w.upgrade(), b.upgrade()) {
                        t.retry(&b);
                    }
                }) as Box<dyn Fn()>,
            ))
        } else {
            button
        };
        if let Some((text, f)) = button {
            let btn = sonata_ui::controls::push_button(&text, f, "");
            btn.set_halign(gtk::Align::Start);
            bx.append(&btn);
        }
        self.add(&bx, true);
    }

    fn retry(&self, bx: &gtk::Box) {
        self.remove_shown(bx);
        let Some(chat) = self.chat.borrow().clone() else { return };
        let last_user = messages_of(&chat.borrow()).last().is_some_and(|m| m["role"] == "user");
        if !self.busy() && last_user {
            self.ask(chat);
        }
    }

    /// Stop the answer; what was written so far stays. File actions still
    /// waiting are not done (Claude is told so).
    pub fn stop(&self, keep: bool) {
        if let Some(job) = self.job.borrow().clone() {
            job.cancel();
            self.finish(keep);
        }
        let Some(p) = self.pending.borrow().clone() else { return };
        let blks: Vec<Value> = {
            let mut q = p.borrow_mut();
            q.stopped = true;
            let cur = q.current.take();
            cur.into_iter().chain(q.queue.drain(..)).collect()
        };
        for blk in blks {
            let row = p.borrow().rows.get(&block_id(&blk)).cloned();
            self.tool_result(&p, &blk, "The user stopped this.", true, row.as_deref(), &not_allowed_text(&summary_of(&blk)));
        }
        if let Some(a) = self.alert.take() {
            a.force_close();
        }
        // a running action ends the turn itself
        if self.is_pending(&p) && !p.borrow().running {
            self.next_tool();
        }
    }

    // -- window -----------------------------------------------------------------------------------
    fn key(&self, key: gtk::gdk::Key, state: gtk::gdk::ModifierType) -> bool {
        use gtk::gdk::{Key, ModifierType as M};
        let cmd = state.intersects(M::CONTROL_MASK | M::SUPER_MASK);
        if key == Key::Escape && self.busy_here() {
            self.stop(true);
            return true;
        }
        if cmd && (key == Key::n || key == Key::N) {
            self.new_chat();
            return true;
        }
        if cmd && key == Key::comma {
            self.show_settings();
            return true;
        }
        if cmd && (key == Key::w || key == Key::W) {
            self.window.close();
            return true;
        }
        false
    }

    fn close_request(&self) {
        self.stop(true);
        if let Some(id) = self.tick.take() {
            sonata_ui::cancel(id);
        }
        let sel = self.chat_id().filter(|id| self.store.borrow().get(id).is_some()).unwrap_or_default();
        if sel != self.cfg_str("selected") {
            self.set_cfg("selected", json!(sel));
        }
        let me = self.me.clone();
        WINDOWS.with(|l| l.borrow_mut().retain(|w| !Weak::ptr_eq(&Rc::downgrade(w), &me)));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The settings panel (a form) opened without the close dot every form
    /// panel has (tests/test_dialog_close.py's test_forms_ask_for_it), and
    /// stretched with a long folder or model name instead of keeping 360 px.
    #[test]
    fn test_settings_panel_had_no_close_dot() {
        let src = include_str!("window.rs");
        let i = src.find("panel::popup_with(&self.settings_btn").expect("the settings panel");
        let call = &src[i..i + 200];
        assert!(call.contains("close: true") && call.contains("width: Some(360)"), "{call}");
    }

    /// The Assistant opened at 1000 x 680 the first time on any display:
    /// the Python one opens roomy, about two thirds of it.
    #[test]
    fn test_first_size_not_roomy() {
        use sonata_ui::window::first_size;
        assert_eq!(first_size(None, 1000, 680, true, Some((1920, 1080))), (1267, 702, false));
        assert_eq!(first_size(None, 1000, 680, true, Some((2560, 1440))), (1689, 936, false));
    }

    #[test]
    fn grouped_like_python() {
        assert_eq!(grouped(0), "0");
        assert_eq!(grouped(999), "999");
        assert_eq!(grouped(1000), "1,000");
        assert_eq!(grouped(10_000_000), "10,000,000");
    }

    #[test]
    fn new_chat_title_in_the_session_language() {
        sonata_i18n::use_language("pt_BR");
        let m: Chat = json!({"title": "New Chat"}).as_object().unwrap().clone();
        assert_eq!(chat_title(Some(&m)), "Nova Conversa");
        let m: Chat = json!({"title": "Hi"}).as_object().unwrap().clone();
        assert_eq!(chat_title(Some(&m)), "Hi");
        sonata_i18n::use_language("en");
    }
}
