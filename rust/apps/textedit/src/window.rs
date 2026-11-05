//! TextEdit (macOS TextEdit, plain text, with Sublime-style extras), ported
//! from `sonata2/textedit/window.py`.
//!
//! A window holds documents in tabs: the tab bar sits under the glass
//! toolbar. Files opened from the system or dropped on a window become tabs
//! of the frontmost window; a file already open comes forward.
//!
//! Nothing is ever lost (macOS autosave / Sublime hot exit): every window
//! and tab, with the text of untitled and edited documents, cursor and
//! scroll, is saved about a second after each change and when a window
//! closes (session.rs), and comes back when TextEdit starts without files.
//! Closing a window therefore never asks; only closing a *tab* with unsaved
//! changes does (Don't Save / Cancel / Save…).
//!
//! Files: any text file -- the encoding and line endings are detected and
//! kept on save; both show in the status bar (click to change), with the
//! cursor's line and column and the word / character count. .docx, .odt and
//! .rtf open read-only as their plain text. Open and Save go through the
//! file chooser portal (Sonata's own panels in a Sonata session).
//!
//! Keyboard (⌘ is Ctrl or Super): N new window, T new tab, W close tab,
//! Ctrl+Tab / Ctrl+Shift+Tab (or ⌘Shift+] / [, Ctrl+PageDown / PageUp) next /
//! previous tab, ⌘1…9 a tab, O open, S save, Shift+S save as, F find, Alt+F
//! find & replace, G / Shift+G next / previous match, L go to line, + / - / 0
//! text size, Z undo, Shift+Z / Y redo.

use crate::document as D;
use crate::search::{self, Opts};
use crate::session;
use crate::source;
use crate::tabs::{Handlers, TabStrip};
use gtk::gdk::{self, Key, ModifierType};
use gtk::{gio, glib, prelude::*};
use serde_json::{json, Map, Value};
use sonata_i18n::{fmt, gettext, language, n_, tr, trn};
use sonata_ui::{controls, dialog, menu};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::time::Duration;

const SIZES: [i64; 14] = [9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40, 48];
const FONTS: [(&str, &str); 4] = [("auto", n_!("Automatic")), ("sans", n_!("Sans-Serif")), ("serif", n_!("Serif")), ("mono", n_!("Monospace"))];
const SAVE_DELAY_MS: u64 = 1000; // session autosave debounce
const STATS_DELAY_MS: u64 = 300; // word count debounce
const MAX_STATS: i32 = 4 * 1024 * 1024; // no word count above this many characters
/// Automatic font: not monospaced
const PROSE: [Option<&str>; 6] = [None, Some("Markdown"), Some("Text"), Some("reStructuredText"), Some("AsciiDoc"), Some("Plain Text")];
/// never smaller (Vini: the page shrank to one line under the toolbar)
const MIN_SIZE: (i32, i32) = (420, 280);

fn defaults() -> Value {
    json!({"font_size": 14, "wrap": true, "font": "auto", "line_numbers": false})
}

pub(crate) fn register_tabs_css() {
    sonata_ui::css::register_module("textedit/tabs.py");
}

// -- process-wide state: live windows (frontmost first), windows kept in the
// session after they closed ("parked"), whether the saved session was read
#[derive(Default)]
struct Global {
    windows: Vec<Rc<TeWindow>>,
    parked: Vec<Value>,
    loaded: bool,
    timer: Option<glib::SourceId>,
    cfg: Option<Map<String, Value>>,
}

thread_local! {
    static S: RefCell<Global> = RefCell::default();
}

fn windows() -> Vec<Rc<TeWindow>> {
    S.with(|s| s.borrow().windows.clone())
}

fn cfg() -> Map<String, Value> {
    S.with(|s| {
        let mut s = s.borrow_mut();
        s.cfg
            .get_or_insert_with(|| {
                let mut c = sonata_config::load("textedit", &defaults());
                let font = c.get("font").and_then(Value::as_str).unwrap_or("");
                if !FONTS.iter().any(|f| f.0 == font) {
                    c.insert("font".into(), json!("auto"));
                }
                c
            })
            .clone()
    })
}

fn cfg_bool(k: &str) -> bool {
    cfg().get(k).and_then(Value::as_bool).unwrap_or(false)
}

fn cfg_size() -> i64 {
    cfg().get("font_size").and_then(Value::as_f64).map(|f| f as i64).unwrap_or(14)
}

fn cfg_font() -> String {
    cfg().get("font").and_then(Value::as_str).unwrap_or("auto").to_string()
}

fn apply_size() {
    // (window.py's _apply_size: a template of its own, filled with the size)
    let mut local = sonata_ui::tokens::Values::new();
    local.insert("te_size".into(), cfg_size().to_string());
    sonata_ui::register_with("textedit-size", "textview.te-page { font-size: %(te_size)spx; }", local);
}

fn untitled_name() -> String {
    let mut used: Vec<String> = Vec::new();
    for w in windows() {
        for d in w.docs.borrow().iter() {
            if d.file.borrow().is_none() {
                used.push(d.untitled.borrow().clone());
            }
        }
    }
    S.with(|s| {
        for p in &s.borrow().parked {
            for t in p.get("tabs").and_then(Value::as_array).into_iter().flatten() {
                if t.get("uri").and_then(Value::as_str).is_none() {
                    if let Some(title) = t.get("title").and_then(Value::as_str) {
                        used.push(title.to_string());
                    }
                }
            }
        }
    });
    let name = |n: usize| if n == 1 { tr!("Untitled") } else { fmt(&tr!("Untitled {n}"), &[("n", &n.to_string())]) };
    let mut n = 1;
    while used.contains(&name(n)) {
        n += 1;
    }
    name(n)
}

/// 1,234 / 1.234 / 1 234: the language's digit grouping.
fn thousands(n: i64) -> String {
    let digits = n.abs().to_string();
    let sep = match language().as_str() {
        "pt_BR" => ".",
        "pt_PT" => "\u{a0}",
        _ => ",",
    };
    let mut out = String::new();
    for (i, c) in digits.chars().enumerate() {
        if i > 0 && (digits.len() - i) % 3 == 0 {
            out.push_str(sep);
        }
        out.push(c);
    }
    if n < 0 { format!("-{out}") } else { out }
}

/// A document window's first size (ui/window.py's roomy_size): (w, h) grown
/// to about two thirds of a `screen`-sized display (Vini: on 1080p windows
/// opened too small), never smaller than the app's own default.
fn roomy_size(w: i32, h: i32, screen: Option<(i32, i32)>) -> (i32, i32) {
    match screen {
        Some((sw, sh)) => (w.max((sw as f64 * 0.66) as i32), h.max((sh as f64 * 0.65) as i32)),
        None => (w, h),
    }
}

/// Go to Line's field: "12" or "12:3" / "12,3" (window.py's
/// `^\s*(\d+)\s*(?:[:,]\s*(\d+))?\s*$`); None for anything else -- a sign,
/// a stray character, "12:" -- so the cursor stays put.
fn parse_goto(text: &str) -> Option<(i64, i64)> {
    let num = |s: &str| -> Option<i64> {
        let s = s.trim();
        if s.is_empty() || !s.chars().all(|c| c.is_ascii_digit()) {
            return None;
        }
        // a number past any line: the last line (clamped by go_to)
        Some(s.parse::<i64>().unwrap_or(i64::MAX))
    };
    let mut parts = text.splitn(2, [':', ',']);
    let line = num(parts.next()?)?;
    match parts.next() {
        None => Some((line, 1)),
        Some(c) => Some((line, num(c)?)),
    }
}

fn rgba(token: &str, alpha: f32) -> gdk::RGBA {
    let c = sonata_ui::rgba(token);
    gdk::RGBA::new(c.red(), c.green(), c.blue(), alpha)
}

// -- documents -----------------------------------------------------------------------------------
pub enum WriteError {
    Encoding,
    ChangedOnDisk,
    Message(String),
}

/// One tab: a text view in a scroller, the file it belongs to (None:
/// untitled) and how that file is written (encoding, BOM, line endings).
pub struct Document {
    pub id: RefCell<String>,
    pub file: RefCell<Option<gio::File>>,
    /// the file's etag when read/saved (on-disk change check)
    etag: RefCell<Option<String>>,
    pub untitled: RefCell<String>,
    pub encoding: RefCell<String>,
    pub bom: Cell<bool>,
    pub newline: RefCell<String>,
    /// converted .docx/.odt/.rtf
    pub readonly: Cell<bool>,
    pub language: RefCell<Option<String>>,
    /// edits, and the edit last written to the session
    gen: Cell<u64>,
    backed_gen: Cell<i64>,
    restore_top: Cell<i32>,
    pub view: sourceview5::View,
    pub buffer: sourceview5::Buffer,
    found: gtk::TextTag,
    pub scroll: gtk::ScrolledWindow,
    win: RefCell<Weak<TeWindow>>,
}

fn random_id() -> String {
    let mut b = [0u8; 16];
    if let Ok(mut f) = std::fs::File::open("/dev/urandom") {
        use std::io::Read;
        let _ = f.read_exact(&mut b);
    }
    b.iter().map(|x| format!("{x:02x}")).collect()
}

impl Document {
    fn new(win: &Rc<TeWindow>) -> Rc<Self> {
        let (view, buffer) = source::make_view();
        view.add_css_class("te-page");
        view.set_left_margin(28);
        view.set_right_margin(28);
        view.set_top_margin(18);
        view.set_bottom_margin(40);
        view.set_pixels_below_lines(2);
        view.set_vexpand(true);
        buffer.set_enable_undo(true);
        let found = gtk::TextTag::builder().name("te-found").background_rgba(&rgba("accent", 0.28)).build();
        buffer.tag_table().add(&found);
        let scroll = gtk::ScrolledWindow::builder().child(&view).vexpand(true).hexpand(true).build();
        let doc = Rc::new(Document {
            id: RefCell::new(random_id()),
            file: RefCell::new(None),
            etag: RefCell::new(None),
            untitled: RefCell::new(untitled_name()),
            encoding: RefCell::new("utf-8".into()),
            bom: Cell::new(false),
            newline: RefCell::new("LF".into()),
            readonly: Cell::new(false),
            language: RefCell::new(None),
            gen: Cell::new(0),
            backed_gen: Cell::new(-1),
            restore_top: Cell::new(0),
            view,
            buffer,
            found,
            scroll,
            win: RefCell::new(Rc::downgrade(win)),
        });
        let w = Rc::downgrade(&doc);
        doc.buffer.connect_changed(move |_| {
            if let Some(d) = w.upgrade() {
                d.gen.set(d.gen.get() + 1);
                if let Some(win) = d.win() {
                    win.doc_changed(&d);
                }
            }
        });
        let w = Rc::downgrade(&doc);
        doc.buffer.connect_modified_changed(move |_| {
            if let Some(d) = w.upgrade() {
                if let Some(win) = d.win() {
                    win.doc_modified(&d);
                }
            }
        });
        let w = Rc::downgrade(&doc);
        doc.buffer.connect_mark_set(move |_, _, mark| {
            if mark.name().as_deref() == Some("insert") {
                if let Some(d) = w.upgrade() {
                    if let Some(win) = d.win() {
                        win.cursor_moved(&d);
                    }
                }
            }
        });
        doc.scroll.vadjustment().connect_value_changed(|_| schedule_save());
        doc.apply_look();
        doc
    }

    fn win(&self) -> Option<Rc<TeWindow>> {
        self.win.borrow().upgrade()
    }

    pub fn name(&self) -> String {
        match self.file.borrow().as_ref() {
            Some(f) => f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default(),
            None => self.untitled.borrow().clone(),
        }
    }

    /// Unsaved changes worth keeping (an untitled document with text).
    pub fn dirty(&self) -> bool {
        self.buffer.is_modified() && (self.file.borrow().is_some() || self.buffer.char_count() > 0)
    }

    pub fn blank(&self) -> bool {
        self.file.borrow().is_none() && self.buffer.char_count() == 0 && !self.readonly.get()
    }

    pub fn text(&self) -> String {
        let (a, b) = self.buffer.bounds();
        self.buffer.text(&a, &b, true).to_string()
    }

    pub fn apply_look(&self) {
        let mut fam = cfg_font();
        if fam == "auto" {
            let lang = self.language.borrow().clone();
            fam = if D::is_code(&self.name()) || !PROSE.contains(&lang.as_deref()) { "mono".into() } else { "sans".into() };
        }
        for f in ["sans", "serif", "mono"] {
            let class = format!("te-{f}");
            if f == fam {
                self.view.add_css_class(&class);
            } else {
                self.view.remove_css_class(&class);
            }
        }
        self.view.set_wrap_mode(if cfg_bool("wrap") { gtk::WrapMode::WordChar } else { gtk::WrapMode::None });
        let lines = cfg_bool("line_numbers");
        source::show_line_numbers(&self.view, lines);
        self.view.set_left_margin(if lines { 12 } else { 28 });
    }

    // -- files -------------------------------------------------------------------------------
    /// Load a file; None or an error message.
    pub fn read(&self, f: &gio::File) -> Option<String> {
        let (data, etag) = match f.load_contents(gio::Cancellable::NONE) {
            Ok(x) => x,
            Err(e) => return Some(e.message().to_string()),
        };
        let name = f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
        let (dec, readonly) = match D::convert(&data, &name) {
            Ok(Some(text)) => (D::Decoded::plain(D::normalize(&text)), true),
            Ok(None) => match D::decode(&data) {
                Ok(d) => (d, false),
                Err(_) => return Some(tr!("It isn't a text document TextEdit can read.")),
            },
            Err(e) => return Some(fmt(&tr!("The document is damaged ({error})."), &[("error", &e)])),
        };
        self.set_text(&dec.text, false);
        *self.file.borrow_mut() = Some(f.clone());
        *self.etag.borrow_mut() = etag.map(|e| e.to_string());
        *self.encoding.borrow_mut() = dec.encoding;
        self.bom.set(dec.bom);
        *self.newline.borrow_mut() = dec.newline;
        self.readonly.set(readonly);
        self.view.set_editable(!readonly);
        *self.language.borrow_mut() = if readonly { None } else { source::guess_language(&self.buffer, &name, &data) };
        self.apply_look();
        None
    }

    pub fn set_text(&self, text: &str, modified: bool) {
        self.buffer.begin_irreversible_action();
        self.buffer.set_text(text);
        self.buffer.end_irreversible_action();
        self.buffer.place_cursor(&self.buffer.start_iter());
        self.buffer.set_modified(modified);
    }

    /// Save to self.file (ChangedOnDisk when another program changed the
    /// file since it was read, unless force).
    pub fn write(&self, force: bool) -> Result<(), WriteError> {
        let data = D::encode(&self.text(), &self.encoding.borrow(), self.bom.get(), &self.newline.borrow()).map_err(|_| WriteError::Encoding)?;
        let Some(file) = self.file.borrow().clone() else { return Err(WriteError::Message(String::new())) };
        let etag = if force { None } else { self.etag.borrow().clone() };
        match file.replace_contents(&data, etag.as_deref(), false, gio::FileCreateFlags::NONE, gio::Cancellable::NONE) {
            Ok(new) => *self.etag.borrow_mut() = new.map(|e| e.to_string()),
            Err(e) if e.matches(gio::IOErrorEnum::WrongEtag) => return Err(WriteError::ChangedOnDisk),
            Err(e) => return Err(WriteError::Message(e.message().to_string())),
        }
        self.buffer.set_modified(false);
        gtk::RecentManager::default().add_item(&file.uri());
        Ok(())
    }

    // -- session -----------------------------------------------------------------------------
    /// This tab for session.json (writes its text aside when unsaved).
    fn state(&self) -> Value {
        let backup = self.dirty();
        if backup && self.backed_gen.get() != self.gen.get() as i64 && session::write_buffer(&self.id.borrow(), &self.text()).is_ok() {
            self.backed_gen.set(self.gen.get() as i64);
        }
        let cur = self.buffer.iter_at_mark(&self.buffer.get_insert());
        let mut top = self.restore_top.get();
        if self.view.is_realized() {
            top = self.view.line_at_y(self.view.visible_rect().y()).0.line();
        }
        json!({"id": *self.id.borrow(), "uri": self.file.borrow().as_ref().map(|f| f.uri().to_string()),
               "title": *self.untitled.borrow(), "backup": backup, "cursor": cur.offset(), "top_line": top,
               "encoding": *self.encoding.borrow(), "bom": self.bom.get(), "newline": *self.newline.borrow(),
               "readonly": self.readonly.get()})
    }

    /// Fill from a session tab; false when there's nothing left of it.
    fn restore(&self, t: &Value) -> bool {
        let backup = t.get("backup").and_then(Value::as_bool).unwrap_or(false);
        let id = t.get("id").and_then(Value::as_str).unwrap_or("");
        let text = if backup { session::read_buffer(id) } else { None };
        let uri = t.get("uri").and_then(Value::as_str).filter(|u| !u.is_empty());
        if let Some(uri) = uri {
            let f = gio::File::for_uri(uri);
            let err = if f.query_exists(gio::Cancellable::NONE) { self.read(&f) } else { Some("missing".into()) };
            if err.is_some() && text.is_none() {
                return false;
            }
            if err.is_some() {
                let name = f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
                *self.language.borrow_mut() = source::guess_language(&self.buffer, &name, b"");
            }
            *self.file.borrow_mut() = Some(f);
        } else if text.is_none() && backup {
            return false;
        }
        if session::valid_id(id) {
            *self.id.borrow_mut() = id.to_string();
        }
        if let (Some(title), None) = (t.get("title").and_then(Value::as_str), uri) {
            *self.untitled.borrow_mut() = title.to_string();
        }
        if let Some(text) = text {
            self.set_text(&text, true);
            self.backed_gen.set(self.gen.get() as i64);
            if let Some(e) = t.get("encoding").and_then(Value::as_str).filter(|e| D::is_encoding(e)) {
                *self.encoding.borrow_mut() = e.to_string();
                self.bom.set(t.get("bom").and_then(Value::as_bool).unwrap_or(false));
            }
            if let Some(n) = t.get("newline").and_then(Value::as_str).filter(|n| D::is_newline(n)) {
                *self.newline.borrow_mut() = n.to_string();
            }
        }
        let top = t.get("top_line").and_then(Value::as_i64).unwrap_or(0) as i32;
        self.restore_top.set(top);
        let it = self.buffer.iter_at_offset(t.get("cursor").and_then(Value::as_i64).unwrap_or(0) as i32);
        self.buffer.place_cursor(&it);
        if let Some(top) = self.buffer.iter_at_line(top) {
            let mark = self.buffer.create_mark(None, &top, true);
            self.view.scroll_to_mark(&mark, 0.0, true, 0.0, 0.0);
        }
        true
    }
}

// -- windows -----------------------------------------------------------------------------------
pub struct TeWindow {
    pub window: gtk::ApplicationWindow,
    pub docs: RefCell<Vec<Rc<Document>>>,
    pub doc: RefCell<Option<Rc<Document>>>,
    closing: Cell<bool>,
    stats_id: RefCell<Option<glib::SourceId>>,
    refind_id: RefCell<Option<glib::SourceId>>,
    replacing: Cell<bool>,
    syncing: Cell<bool>,
    tabs: RefCell<Option<Rc<TabStrip>>>,
    tabs_rev: gtk::Revealer,
    pub find_bar: gtk::Revealer,
    pub find_entry: gtk::SearchEntry,
    case_btn: gtk::ToggleButton,
    regex_btn: gtk::ToggleButton,
    word_btn: gtk::ToggleButton,
    pub find_count: gtk::Label,
    replace_check: gtk::CheckButton,
    replace_rev: gtk::Revealer,
    pub replace_entry: gtk::Entry,
    stack: gtk::Stack,
    pos_btn: gtk::Button,
    ro_label: gtk::Label,
    lang_label: gtk::Label,
    count_label: gtk::Label,
    enc_btn: gtk::Button,
    nl_btn: gtk::Button,
    font_label: gtk::Label,
    size_label: gtk::Label,
    wrap_btn: gtk::ToggleButton,
    lines_btn: gtk::ToggleButton,
    monitors: RefCell<Vec<gio::FileMonitor>>,
    me: Weak<TeWindow>,
}

fn tool(icon: Option<&str>, label: Option<&str>, tip: &str, toggle: bool) -> gtk::Button {
    let b: gtk::Button = if toggle { gtk::ToggleButton::new().upcast() } else { gtk::Button::new() };
    if let Some(i) = icon {
        b.set_icon_name(i);
    }
    if let Some(l) = label {
        b.set_label(l);
    }
    b.add_css_class("tool");
    b.set_can_focus(false);
    b.set_tooltip_text(Some(tip));
    b
}

fn sep() -> gtk::Box {
    let s = gtk::Box::builder().valign(gtk::Align::Center).build();
    s.add_css_class("te-sep");
    s
}

impl TeWindow {
    pub fn new(app: &impl IsA<gtk::Application>, path: Option<gio::File>, restore: Option<&Value>) -> Rc<Self> {
        sonata_ui::css::register_module("textedit/window.py"); // the Python TextEdit's own CSS (css.json)
        register_tabs_css();
        if glib::application_name().is_none() {
            glib::set_application_name("TextEdit"); // Recent documents need it
        }
        apply_size();
        let win = Rc::new_cyclic(|me: &Weak<TeWindow>| {
            let window = gtk::ApplicationWindow::builder().application(app).title(tr!("Untitled")).build();
            window.add_css_class("sonata-textedit");
            sonata_ui::window::standard(&window);
            window.set_size_request(MIN_SIZE.0, MIN_SIZE.1);
            // a session window: its own size (restore); a document window's
            // first size, with no size kept yet, is about two thirds of the
            // display (window.py: remember_size(..., roomy=True))
            let (w0, h0) = if sonata_ui::window::saved_size("textedit").is_none() {
                roomy_size(760, 580, sonata_ui::window::screen_size_of(&window))
            } else {
                (760, 580)
            };
            sonata_ui::window::remember_size(window.upcast_ref(), "textedit", w0, h0);
            let call = |f: fn(&TeWindow)| -> Box<dyn Fn()> {
                let w = me.clone();
                Box::new(move || {
                    if let Some(t) = w.upgrade() {
                        f(&t);
                    }
                })
            };
            // -- toolbar
            let (handle, bar, _) = sonata_ui::window::glass_toolbar(
                &window,
                vec![
                    ("edit-find-symbolic", tr!("Find (⌘F)"), call(|t| t.show_find(false))),
                    ("tab-new-symbolic", tr!("New Tab (⌘T)"), call(|t| {
                        if let Some(me) = t.me.upgrade() {
                            me.new_tab(true, None);
                        }
                    })),
                ],
            );
            let start = gtk::Box::builder().spacing(2).valign(gtk::Align::Center).build();
            start.add_css_class("te-tools");
            let font_btn = tool(None, None, &tr!("Font"), false);
            font_btn.add_css_class("te-font");
            let fb = gtk::Box::new(gtk::Orientation::Horizontal, 0);
            let font_label = gtk::Label::new(None);
            fb.append(&font_label);
            fb.append(&gtk::Image::from_icon_name("pan-down-symbolic"));
            font_btn.set_child(Some(&fb));
            let w = me.clone();
            font_btn.connect_clicked(move |b| {
                if let Some(t) = w.upgrade() {
                    t.font_menu(b);
                }
            });
            start.append(&font_btn);
            start.append(&sep());
            let smaller = tool(None, Some("A"), &tr!("Smaller (⌘−)"), false);
            smaller.add_css_class("te-a-small");
            let f = call(|t| t.zoom(-1));
            smaller.connect_clicked(move |_| f());
            let size_label = gtk::Label::new(None);
            size_label.add_css_class("te-size");
            let bigger = tool(None, Some("A"), &tr!("Bigger (⌘+)"), false);
            bigger.add_css_class("te-a-big");
            let f = call(|t| t.zoom(1));
            bigger.connect_clicked(move |_| f());
            start.append(&smaller);
            start.append(&size_label);
            start.append(&bigger);
            start.append(&sep());
            let wrap_btn: gtk::ToggleButton = tool(Some("view-wrapped-symbolic"), None, &tr!("Wrap Lines"), true).downcast().unwrap();
            let lines_btn: gtk::ToggleButton = tool(Some("view-list-ordered-symbolic"), None, &tr!("Line Numbers"), true).downcast().unwrap();
            let w = me.clone();
            wrap_btn.connect_toggled(move |b| {
                if let Some(t) = w.upgrade() {
                    t.set_option("wrap", json!(b.is_active()));
                }
            });
            let w = me.clone();
            lines_btn.connect_toggled(move |b| {
                if let Some(t) = w.upgrade() {
                    t.set_option("line_numbers", json!(b.is_active()));
                }
            });
            start.append(&wrap_btn);
            start.append(&lines_btn);
            bar.set_start_widget(Some(&start));
            let col = gtk::Box::new(gtk::Orientation::Vertical, 0);
            col.append(&handle);
            // -- tabs (filled in below, once the window exists)
            let tabs_rev = gtk::Revealer::builder().reveal_child(false).transition_duration(150).transition_type(gtk::RevealerTransitionType::SlideDown).build();
            col.append(&tabs_rev);
            // -- find bar
            let find_col = gtk::Box::new(gtk::Orientation::Vertical, 6);
            find_col.add_css_class("te-find");
            let row = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            let find_entry = gtk::SearchEntry::builder().placeholder_text(tr!("Find")).hexpand(false).build();
            let opt = |label: &str, tip: String| {
                let b = gtk::ToggleButton::builder().label(label).can_focus(false).tooltip_text(tip).build();
                b.add_css_class("te-opt");
                b
            };
            let case_btn = opt("Aa", tr!("Match Case"));
            let regex_btn = opt(".*", tr!("Regular Expression"));
            let word_btn = opt("W", tr!("Whole Words"));
            let nav = gtk::Box::new(gtk::Orientation::Horizontal, 0);
            nav.add_css_class("linked");
            let prev = gtk::Button::builder().icon_name("go-previous-symbolic").tooltip_text(tr!("Previous (⇧⌘G)")).build();
            prev.add_css_class("te-nav");
            let nxt = gtk::Button::builder().icon_name("go-next-symbolic").tooltip_text(tr!("Next (⌘G)")).build();
            nxt.add_css_class("te-nav");
            let f = call(|t| t.find(-1, true));
            prev.connect_clicked(move |_| f());
            let f = call(|t| t.find(1, true));
            nxt.connect_clicked(move |_| f());
            nav.append(&prev);
            nav.append(&nxt);
            let find_count = gtk::Label::new(None);
            find_count.add_css_class("te-count");
            let replace_check = gtk::CheckButton::with_label(&tr!("Replace"));
            let done = controls::push_button(&tr!("Done"), call(|t| t.hide_find()), "");
            row.append(&find_entry);
            row.append(&case_btn);
            row.append(&word_btn);
            row.append(&regex_btn);
            row.append(&nav);
            row.append(&find_count);
            row.append(&gtk::Box::builder().hexpand(true).build());
            row.append(&replace_check);
            row.append(&done);
            find_col.append(&row);
            let rrow = gtk::Box::new(gtk::Orientation::Horizontal, 6);
            let replace_entry = gtk::Entry::builder().placeholder_text(tr!("Replace")).width_chars(24).build();
            rrow.append(&replace_entry);
            rrow.append(&controls::push_button(&tr!("Replace"), call(|t| t.replace_one()), ""));
            rrow.append(&controls::push_button(&tr!("Replace All"), call(|t| {
                t.replace_all();
            }), ""));
            let replace_rev = gtk::Revealer::builder().child(&rrow).reveal_child(false).transition_duration(120).transition_type(gtk::RevealerTransitionType::SlideDown).build();
            find_col.append(&replace_rev);
            let find_bar = gtk::Revealer::builder().child(&find_col).reveal_child(false).transition_type(gtk::RevealerTransitionType::SlideDown).transition_duration(180).build();
            col.append(&find_bar);
            let stack = gtk::Stack::builder().vexpand(true).hexpand(true).build();
            stack.add_css_class("te-stack");
            col.append(&stack);
            // -- status bar
            let status = gtk::Box::new(gtk::Orientation::Horizontal, 0);
            status.add_css_class("te-status");
            let pos_btn = gtk::Button::builder().can_focus(false).tooltip_text(tr!("Go to Line (⌘L)")).build();
            let f = call(|t| t.goto_line());
            pos_btn.connect_clicked(move |_| f());
            status.append(&pos_btn);
            let ro_label = gtk::Label::builder().label(tr!("Read-only (converted)")).visible(false).tooltip_text(tr!("Converted to plain text: Save writes a .txt copy")).build();
            ro_label.add_css_class("te-ro");
            status.append(&ro_label);
            status.append(&gtk::Box::builder().hexpand(true).build());
            let lang_label = gtk::Label::new(None);
            lang_label.add_css_class("te-plain");
            let count_label = gtk::Label::new(None);
            count_label.add_css_class("te-plain");
            let enc_btn = gtk::Button::builder().can_focus(false).tooltip_text(tr!("Text Encoding")).build();
            let w = me.clone();
            enc_btn.connect_clicked(move |b| {
                if let Some(t) = w.upgrade() {
                    t.encoding_menu(b);
                }
            });
            let nl_btn = gtk::Button::builder().can_focus(false).tooltip_text(tr!("Line Endings")).build();
            let w = me.clone();
            nl_btn.connect_clicked(move |b| {
                if let Some(t) = w.upgrade() {
                    t.newline_menu(b);
                }
            });
            for x in [lang_label.upcast_ref::<gtk::Widget>(), count_label.upcast_ref(), enc_btn.upcast_ref(), nl_btn.upcast_ref()] {
                status.append(x);
            }
            col.append(&status);
            window.set_child(Some(&col));
            TeWindow {
                window,
                docs: RefCell::default(),
                doc: RefCell::default(),
                closing: Cell::new(false),
                stats_id: RefCell::default(),
                refind_id: RefCell::default(),
                replacing: Cell::new(false),
                syncing: Cell::new(false),
                tabs: RefCell::default(),
                tabs_rev,
                find_bar,
                find_entry,
                case_btn,
                regex_btn,
                word_btn,
                find_count,
                replace_check,
                replace_rev,
                replace_entry,
                stack,
                pos_btn,
                ro_label,
                lang_label,
                count_label,
                enc_btn,
                nl_btn,
                font_label,
                size_label,
                wrap_btn,
                lines_btn,
                monitors: RefCell::default(),
                me: me.clone(),
            }
        });
        win.wire();
        S.with(|s| s.borrow_mut().windows.insert(0, win.clone()));
        if let Some(st) = restore {
            win.restore(st);
        }
        if win.docs.borrow().is_empty() {
            win.new_tab(true, None);
            if let Some(f) = path {
                win.open_file(&f);
            }
        }
        win
    }

    /// The signals that need the window itself.
    fn wire(self: &Rc<Self>) {
        let w = |me: &Rc<Self>| Rc::downgrade(me);
        let me = w(self);
        let on = move |f: fn(&TeWindow, &str)| {
            let me = me.clone();
            Box::new(move |id: &str| {
                if let Some(t) = me.upgrade() {
                    f(&t, id);
                }
            })
        };
        let (m1, m2, m3) = (w(self), w(self), w(self));
        let strip = TabStrip::new(Handlers {
            on_select: on(|t, id| {
                if let Some(d) = t.doc_by_id(id) {
                    t.select(&d);
                }
            }),
            on_close: on(|t, id| {
                if let Some(d) = t.doc_by_id(id) {
                    t.close_tab(Some(d), None);
                }
            }),
            on_new: Box::new(move || {
                if let Some(t) = m1.upgrade() {
                    t.new_tab(true, None);
                }
            }),
            on_reorder: Box::new(move |order| {
                if let Some(t) = m2.upgrade() {
                    t.reordered(order);
                }
            }),
            on_menu: Box::new(move |id, widget, x, y| {
                if let Some(t) = m3.upgrade() {
                    t.tab_menu(id, widget, x, y);
                }
            }),
        });
        self.tabs_rev.set_child(Some(&strip.root));
        *self.tabs.borrow_mut() = Some(strip);
        let me = w(self);
        let mons = sonata_ui::window::follow_tab_bar(move || {
            if let Some(t) = me.upgrade() {
                t.show_tabs();
            }
        });
        *self.monitors.borrow_mut() = mons;
        self.sync_tools();
        // find bar
        let me = w(self);
        self.find_entry.connect_search_changed(move |_| {
            if let Some(t) = me.upgrade() {
                t.find(0, true);
            }
        });
        let me = w(self);
        self.find_entry.connect_activate(move |_| {
            if let Some(t) = me.upgrade() {
                t.find(1, true);
            }
        });
        let me = w(self);
        self.find_entry.connect_stop_search(move |_| {
            if let Some(t) = me.upgrade() {
                t.hide_find();
            }
        });
        for b in [&self.case_btn, &self.regex_btn, &self.word_btn] {
            let me = w(self);
            b.connect_toggled(move |_| {
                if let Some(t) = me.upgrade() {
                    t.find(0, false);
                }
            });
        }
        let rev = self.replace_rev.clone();
        self.replace_check.connect_toggled(move |b| rev.set_reveal_child(b.is_active()));
        let me = w(self);
        self.replace_entry.connect_activate(move |_| {
            if let Some(t) = me.upgrade() {
                t.replace_one();
            }
        });
        // keys, drops, closing
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let me = w(self);
        keys.connect_key_pressed(move |_, keyval, _code, state| match me.upgrade() {
            Some(t) if t.key(keyval, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        self.window.add_controller(keys);
        self.file_drops();
        let me = w(self);
        self.window.connect_close_request(move |_| {
            if let Some(t) = me.upgrade() {
                t.close_request();
            }
            glib::Propagation::Proceed
        });
        let me = w(self);
        self.window.connect_is_active_notify(move |win| {
            if win.is_active() {
                if let Some(t) = me.upgrade() {
                    S.with(|s| {
                        let mut s = s.borrow_mut();
                        if let Some(i) = s.windows.iter().position(|x| Rc::ptr_eq(x, &t)) {
                            let x = s.windows.remove(i);
                            s.windows.insert(0, x);
                        }
                    });
                }
            }
        });
    }

    fn strip(&self) -> Rc<TabStrip> {
        self.tabs.borrow().clone().expect("the tab strip is made with the window")
    }

    fn doc_by_id(&self, id: &str) -> Option<Rc<Document>> {
        self.docs.borrow().iter().find(|d| *d.id.borrow() == id).cloned()
    }

    fn current(&self) -> Option<Rc<Document>> {
        self.doc.borrow().clone()
    }

    fn is_current(&self, doc: &Rc<Document>) -> bool {
        self.doc.borrow().as_ref().is_some_and(|d| Rc::ptr_eq(d, doc))
    }

    fn index_of(&self, doc: &Rc<Document>) -> Option<usize> {
        self.docs.borrow().iter().position(|d| Rc::ptr_eq(d, doc))
    }

    fn sync_tools(&self) {
        let font = cfg_font();
        let label = FONTS.iter().find(|f| f.0 == font).map(|f| gettext(f.1)).unwrap_or_default();
        self.font_label.set_label(&label);
        self.size_label.set_label(&cfg_size().to_string());
        self.syncing.set(true);
        self.wrap_btn.set_active(cfg_bool("wrap"));
        self.lines_btn.set_active(cfg_bool("line_numbers"));
        self.syncing.set(false);
    }

    // -- tabs --------------------------------------------------------------------------------
    pub fn new_tab(self: &Rc<Self>, select: bool, index: Option<usize>) -> Rc<Document> {
        let doc = Document::new(self);
        self.adopt(&doc, index);
        if select {
            self.select(&doc);
        }
        schedule_save();
        doc
    }

    fn adopt(self: &Rc<Self>, doc: &Rc<Document>, index: Option<usize>) {
        *doc.win.borrow_mut() = Rc::downgrade(self);
        // new tabs open next to the current one
        let index = index.or_else(|| self.current().and_then(|c| self.index_of(&c)).map(|i| i + 1)).unwrap_or(self.docs.borrow().len());
        let index = index.min(self.docs.borrow().len());
        self.docs.borrow_mut().insert(index, doc.clone());
        self.stack.add_child(&doc.scroll);
        self.strip().add(&doc.id.borrow(), Some(index));
        self.update_tab(doc);
        self.show_tabs();
    }

    fn show_tabs(&self) {
        self.tabs_rev.set_reveal_child(sonata_ui::window::tab_bar_shown(self.docs.borrow().len()));
    }

    fn detach(&self, doc: &Rc<Document>) {
        let Some(i) = self.index_of(doc) else { return };
        self.docs.borrow_mut().remove(i);
        self.strip().remove(&doc.id.borrow());
        self.stack.remove(&doc.scroll);
        self.show_tabs();
        if self.is_current(doc) {
            *self.doc.borrow_mut() = None;
            let next = {
                let docs = self.docs.borrow();
                if docs.is_empty() { None } else { Some(docs[i.min(docs.len() - 1)].clone()) }
            };
            if let Some(n) = next {
                self.select(&n);
            }
        }
    }

    pub fn select(&self, doc: &Rc<Document>) {
        if self.index_of(doc).is_none() {
            return;
        }
        *self.doc.borrow_mut() = Some(doc.clone());
        self.stack.set_visible_child(&doc.scroll);
        self.strip().select(&doc.id.borrow());
        self.update_title();
        self.update_status(true);
        if self.find_bar.reveals_child() {
            self.find(0, false);
        }
        if !self.find_entry.has_focus() {
            doc.view.grab_focus();
        }
        schedule_save();
    }

    fn cycle(&self, step: i32) {
        let docs = self.docs.borrow().clone();
        if docs.len() > 1 {
            if let Some(i) = self.current().and_then(|c| self.index_of(&c)) {
                let n = docs.len() as i32;
                self.select(&docs[((i as i32 + step).rem_euclid(n)) as usize]);
            }
        }
    }

    /// Close a tab (asking first when it has unsaved changes); the window
    /// goes with its last tab.
    pub fn close_tab(&self, doc: Option<Rc<Document>>, then: Option<Rc<dyn Fn()>>) {
        let Some(doc) = doc.or_else(|| self.current()) else { return };
        if !doc.dirty() {
            self.remove_tab(&doc);
            if let Some(t) = then {
                t();
            }
            return;
        }
        self.select(&doc);
        let me = self.me.clone();
        let d = doc.clone();
        let save_label = if doc.file.borrow().is_none() || doc.readonly.get() { tr!("Save…") } else { tr!("Save") };
        dialog::alert(
            &fmt(&tr!("Do you want to save the changes made to the document “{name}”?"), &[("name", &doc.name())]),
            &tr!("Your changes will be lost if you don't save them."),
            &[("discard", tr!("Don't Save"), "destructive"), ("cancel", tr!("Cancel"), ""), ("save", save_label, "default")],
            move |rid| {
                let Some(t) = me.upgrade() else { return };
                match rid {
                    "save" => {
                        let (me2, d2, then2) = (t.me.clone(), d.clone(), then.clone());
                        t.save(Some(d.clone()), Some(Rc::new(move || {
                            if let Some(t) = me2.upgrade() {
                                t.remove_tab(&d2);
                            }
                            if let Some(f) = &then2 {
                                f();
                            }
                        })), false);
                    }
                    "discard" => {
                        t.remove_tab(&d);
                        if let Some(f) = &then {
                            f();
                        }
                    }
                    _ => {}
                }
            },
            Some(self.window.upcast_ref()),
        );
    }

    fn remove_tab(&self, doc: &Rc<Document>) {
        if self.index_of(doc).is_none() {
            return;
        }
        self.detach(doc);
        doc.buffer.set_modified(false); // its text is dropped from the session
        if self.docs.borrow().is_empty() {
            self.window.close();
        }
        schedule_save();
    }

    fn reordered(&self, order: Vec<String>) {
        let docs = self.docs.borrow().clone();
        let new: Vec<Rc<Document>> = order.iter().filter_map(|id| docs.iter().find(|d| *d.id.borrow() == *id).cloned()).collect();
        *self.docs.borrow_mut() = new;
        schedule_save();
    }

    fn tab_menu(&self, id: &str, widget: &gtk::Widget, x: f64, y: f64) {
        let Some(doc) = self.doc_by_id(id) else { return };
        let others: Vec<Rc<Document>> = self.docs.borrow().iter().filter(|d| !Rc::ptr_eq(d, &doc)).cloned().collect();
        let has_others = !others.is_empty();
        let (m1, m2, m3) = (self.me.clone(), self.me.clone(), self.me.clone());
        let (d1, d3) = (doc.clone(), doc.clone());
        menu::popup(
            widget,
            vec![
                vec![
                    menu::Item::new(&tr!("Close Tab"), move || {
                        if let Some(t) = m1.upgrade() {
                            t.close_tab(Some(d1.clone()), None);
                        }
                    }),
                    menu::Item::new(&tr!("Close Other Tabs"), move || {
                        if let Some(t) = m2.upgrade() {
                            t.close_many(others.clone());
                        }
                    })
                    .enabled(has_others),
                ],
                vec![menu::Item::new(&tr!("Move Tab to New Window"), move || {
                    if let Some(t) = m3.upgrade() {
                        t.move_to_new_window(&d3);
                    }
                })
                .enabled(has_others)],
            ],
            menu::Options::at(x, y).passthrough(), // (window.py: passthrough=True)
        );
    }

    fn close_many(&self, docs: Vec<Rc<Document>>) {
        let Some(first) = docs.first().cloned() else { return };
        let rest: Vec<Rc<Document>> = docs[1..].to_vec();
        let me = self.me.clone();
        self.close_tab(Some(first), Some(Rc::new(move || {
            if let Some(t) = me.upgrade() {
                t.close_many(rest.clone());
            }
        })));
    }

    fn move_to_new_window(&self, doc: &Rc<Document>) {
        if self.docs.borrow().len() < 2 {
            return;
        }
        self.detach(doc);
        let Some(app) = self.window.application() else { return };
        let w = TeWindow::new(&app, None, Some(&json!({"tabs": []})));
        let blank = w.docs.borrow().first().cloned();
        w.adopt(doc, None);
        w.select(doc);
        if let Some(b) = blank {
            w.detach(&b);
        }
        w.window.present();
        schedule_save();
    }

    // -- per-document updates ----------------------------------------------------------------
    fn update_tab(&self, doc: &Rc<Document>) {
        let tip = match doc.file.borrow().as_ref() {
            Some(f) => f.path().map(|p| p.display().to_string()).unwrap_or_else(|| f.uri().to_string()),
            None => doc.name(),
        };
        let (name, dirty) = (doc.name(), doc.dirty());
        self.strip().with_tab(&doc.id.borrow(), |t| t.set_title(&name, dirty, &tip));
    }

    fn doc_modified(&self, doc: &Rc<Document>) {
        self.update_tab(doc);
        if self.is_current(doc) {
            self.update_title();
        }
        schedule_save();
    }

    fn doc_changed(&self, doc: &Rc<Document>) {
        if doc.file.borrow().is_none() {
            self.update_tab(doc); // an untitled tab's dot follows its text
        }
        if self.is_current(doc) {
            self.update_status(false);
            if self.find_bar.reveals_child() && !self.replacing.get() && self.refind_id.borrow().is_none() {
                let me = self.me.clone();
                *self.refind_id.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(150), move || {
                    if let Some(t) = me.upgrade() {
                        *t.refind_id.borrow_mut() = None;
                        if t.current().is_some() && t.find_bar.reveals_child() {
                            t.find(0, false);
                        }
                    }
                }));
            }
        }
        schedule_save();
    }

    fn cursor_moved(&self, doc: &Rc<Document>) {
        if self.is_current(doc) {
            let it = doc.buffer.iter_at_mark(&doc.buffer.get_insert());
            self.pos_btn.set_label(&fmt(&tr!("Line {line}, Column {column}"), &[("line", &(it.line() + 1).to_string()), ("column", &(it.line_offset() + 1).to_string())]));
            schedule_save();
        }
    }

    fn update_title(&self) {
        let Some(doc) = self.current() else { return };
        let title = if doc.dirty() { fmt(&tr!("{name} — Edited"), &[("name", &doc.name())]) } else { doc.name() };
        self.window.set_title(Some(&title));
    }

    fn update_status(&self, full: bool) {
        let Some(doc) = self.current() else { return };
        self.cursor_moved(&doc);
        if full {
            self.enc_btn.set_label(&D::label(&doc.encoding.borrow(), doc.bom.get()));
            self.nl_btn.set_label(&doc.newline.borrow());
            self.ro_label.set_visible(doc.readonly.get());
            let lang = doc.language.borrow().clone();
            self.lang_label.set_label(lang.as_deref().unwrap_or(""));
            self.lang_label.set_visible(lang.is_some());
            self.enc_btn.set_sensitive(!doc.readonly.get());
            self.nl_btn.set_sensitive(!doc.readonly.get());
        }
        if let Some(id) = self.stats_id.borrow_mut().take() {
            id.remove();
        }
        let me = self.me.clone();
        let delay = if full { 1 } else { STATS_DELAY_MS };
        *self.stats_id.borrow_mut() = Some(glib::timeout_add_local_once(Duration::from_millis(delay), move || {
            if let Some(t) = me.upgrade() {
                *t.stats_id.borrow_mut() = None;
                t.stats();
            }
        }));
    }

    fn stats(&self) {
        let Some(doc) = self.current() else { return };
        let n = doc.buffer.char_count();
        let chars = fmt(&trn!("{n} character", "{n} characters", n), &[("n", &thousands(n as i64))]);
        if n <= MAX_STATS {
            let w = D::words(&doc.text());
            let words = fmt(&trn!("{n} word", "{n} words", w), &[("n", &thousands(w as i64))]);
            self.count_label.set_label(&fmt(&tr!("{words}, {characters}"), &[("words", &words), ("characters", &chars)]));
        } else {
            self.count_label.set_label(&chars);
        }
    }

    // -- files -------------------------------------------------------------------------------
    /// Open a file as a tab here (an empty Untitled tab takes it); a file
    /// already open anywhere comes forward instead.
    pub fn open_file(self: &Rc<Self>, f: &gio::File) -> bool {
        if let Some((w, d)) = find_open(f) {
            w.select(&d);
            w.window.present();
            return true;
        }
        let doc = match self.current() {
            Some(d) if d.blank() && !d.buffer.is_modified() => d,
            _ => self.new_tab(true, None),
        };
        if let Some(err) = doc.read(f) {
            if self.docs.borrow().len() > 1 && doc.blank() {
                self.remove_tab(&doc);
            }
            let name = f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
            self.tell(&fmt(&tr!("“{name}” couldn't be opened."), &[("name", &name)]), &err);
            return false;
        }
        gtk::RecentManager::default().add_item(&f.uri());
        self.update_tab(&doc);
        self.select(&doc);
        true
    }

    fn tell(&self, heading: &str, body: &str) {
        let (h, b, win) = (heading.to_string(), body.to_string(), self.window.clone());
        let show = move || {
            dialog::alert(&h, &b, &[("ok", tr!("OK"), "default")], |_| {}, Some(win.upcast_ref()));
        };
        if self.window.is_mapped() {
            show();
        } else {
            // an alert on a window not on screen yet never appears
            glib::timeout_add_local_once(Duration::from_millis(150), show);
        }
    }

    pub fn save(&self, doc: Option<Rc<Document>>, then: Option<Rc<dyn Fn()>>, force: bool) {
        let Some(doc) = doc.or_else(|| self.current()) else { return };
        if doc.file.borrow().is_none() || doc.readonly.get() {
            self.save_as(Some(doc), then);
            return;
        }
        match doc.write(force) {
            Ok(()) => {}
            Err(WriteError::Encoding) => {
                let (me, d, then) = (self.me.clone(), doc.clone(), then.clone());
                dialog::alert(
                    &fmt(&tr!("“{name}” can't be saved using {encoding}."), &[("name", &doc.name()), ("encoding", &D::label(&doc.encoding.borrow(), false))]),
                    &tr!("Some characters can't be written in that encoding. Save it as Unicode (UTF-8)?"),
                    &[("cancel", tr!("Cancel"), ""), ("utf8", tr!("Save as UTF-8"), "default")],
                    move |rid| {
                        if rid == "utf8" {
                            *d.encoding.borrow_mut() = "utf-8".into();
                            d.bom.set(false);
                            if let Some(t) = me.upgrade() {
                                t.update_status(true);
                                t.save(Some(d.clone()), then.clone(), false);
                            }
                        }
                    },
                    Some(self.window.upcast_ref()),
                );
                return;
            }
            Err(WriteError::ChangedOnDisk) => {
                let (me, d, then) = (self.me.clone(), doc.clone(), then.clone());
                dialog::alert(
                    &fmt(&tr!("“{name}” has been changed by another application."), &[("name", &doc.name())]),
                    &tr!("Saving now replaces those changes with this document."),
                    &[("cancel", tr!("Cancel"), "default"), ("overwrite", tr!("Save Anyway"), "destructive")],
                    move |rid| {
                        if rid == "overwrite" {
                            if let Some(t) = me.upgrade() {
                                t.save(Some(d.clone()), then.clone(), true);
                            }
                        }
                    },
                    Some(self.window.upcast_ref()),
                );
                return;
            }
            Err(WriteError::Message(err)) => {
                self.tell(&fmt(&tr!("“{name}” couldn't be saved."), &[("name", &doc.name())]), &err);
                return;
            }
        }
        self.update_tab(&doc);
        schedule_save();
        if let Some(t) = then {
            t();
        }
    }

    pub fn save_as(&self, doc: Option<Rc<Document>>, then: Option<Rc<dyn Fn()>>) {
        let Some(doc) = doc.or_else(|| self.current()) else { return };
        let file = doc.file.borrow().clone();
        let folder = file.as_ref().and_then(|f| f.parent()).unwrap_or_else(|| {
            gio::File::for_path(glib::user_special_dir(glib::UserDirectory::Documents).unwrap_or_else(glib::home_dir))
        });
        let mut name = file.as_ref().and_then(|f| f.basename()).map(|b| b.to_string_lossy().into_owned()).unwrap_or_else(|| format!("{}.txt", doc.untitled.borrow()));
        if doc.readonly.get() {
            let stem = std::path::Path::new(&name).file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or(name.clone());
            name = format!("{stem}.txt");
        }
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let plain = gtk::FileFilter::new();
        plain.set_name(Some(&tr!("Plain Text")));
        plain.add_pattern("*.txt");
        let all = gtk::FileFilter::new();
        all.set_name(Some(&tr!("All Files")));
        all.add_pattern("*");
        filters.append(&plain);
        filters.append(&all);
        let dlg = gtk::FileDialog::builder().title(tr!("Save")).modal(true).initial_folder(&folder).initial_name(name).filters(&filters).build();
        let me = self.me.clone();
        dlg.save(Some(&self.window), gio::Cancellable::NONE, move |res| {
            let (Ok(f), Some(t)) = (res, me.upgrade()) else { return };
            if doc.readonly.get() {
                // the converted text becomes an ordinary document
                doc.readonly.set(false);
                doc.view.set_editable(true);
                *doc.encoding.borrow_mut() = "utf-8".into();
                doc.bom.set(false);
                *doc.newline.borrow_mut() = "LF".into();
            }
            if !doc.file.borrow().as_ref().is_some_and(|old| old.equal(&f)) {
                *doc.etag.borrow_mut() = None; // another file: the chooser already asked to replace it
            }
            let name = f.basename().map(|b| b.to_string_lossy().into_owned()).unwrap_or_default();
            *doc.file.borrow_mut() = Some(f);
            *doc.language.borrow_mut() = source::guess_language(&doc.buffer, &name, b"");
            doc.apply_look();
            t.update_status(true);
            t.update_title();
            t.save(Some(doc.clone()), then.clone(), false);
        });
    }

    pub fn open_panel(&self) {
        let filters = gio::ListStore::new::<gtk::FileFilter>();
        let text = gtk::FileFilter::new();
        text.set_name(Some(&tr!("Text Documents")));
        text.add_mime_type("text/*");
        let all = gtk::FileFilter::new();
        all.set_name(Some(&tr!("All Files")));
        all.add_pattern("*");
        filters.append(&text);
        filters.append(&all);
        let dlg = gtk::FileDialog::builder().title(tr!("Open")).modal(true).filters(&filters).build();
        let me = self.me.clone();
        dlg.open_multiple(Some(&self.window), gio::Cancellable::NONE, move |res| {
            let (Ok(files), Some(t)) = (res, me.upgrade()) else { return };
            for i in 0..files.n_items() {
                if let Some(f) = files.item(i).and_downcast::<gio::File>() {
                    t.open_file(&f);
                }
            }
        });
    }

    /// Files dropped anywhere on the window open as tabs (text drags still
    /// go to the text).
    fn file_drops(&self) {
        let tgt = gtk::DropTargetAsync::new(Some(gdk::ContentFormats::new(&["text/uri-list"])), gdk::DragAction::COPY);
        tgt.set_propagation_phase(gtk::PropagationPhase::Capture);
        tgt.connect_accept(|_, drop| drop.formats().contain_mime_type("text/uri-list"));
        tgt.connect_drag_enter(|_, _, _, _| gdk::DragAction::COPY);
        tgt.connect_drag_motion(|_, _, _, _| gdk::DragAction::COPY);
        let me = self.me.clone();
        tgt.connect_drop(move |_, drop, _, _| {
            let (drop, me) = (drop.clone(), me.clone());
            glib::spawn_future_local(async move {
                let Ok((stream, _mime)) = drop.read_future(&["text/uri-list"], glib::Priority::DEFAULT).await else {
                    drop.finish(gdk::DragAction::empty());
                    return;
                };
                let mut data = Vec::new();
                while let Ok(b) = stream.read_bytes_future(65536, glib::Priority::DEFAULT).await {
                    if b.is_empty() {
                        break;
                    }
                    data.extend_from_slice(&b);
                }
                drop.finish(gdk::DragAction::COPY);
                let Some(t) = me.upgrade() else { return };
                for line in String::from_utf8_lossy(&data).lines() {
                    let u = line.trim();
                    if !u.is_empty() && !u.starts_with('#') {
                        t.open_file(&gio::File::for_uri(u));
                    }
                }
            });
            true
        });
        self.window.add_controller(tgt);
    }

    // -- status bar menus --------------------------------------------------------------------
    fn encoding_menu(&self, btn: &gtk::Button) {
        let Some(doc) = self.current() else { return };
        let saves: Vec<menu::Item> = D::ENCODINGS
            .iter()
            .map(|(e, lbl)| {
                let (me, d, e) = (self.me.clone(), doc.clone(), e.to_string());
                menu::Item::check(&gettext(lbl), *doc.encoding.borrow() == e, move |_on| {
                    let latin = e == "iso-8859-1" || e == "windows-1252";
                    d.bom.set(d.bom.get() && !latin);
                    *d.encoding.borrow_mut() = e.clone();
                    d.buffer.set_modified(true);
                    if let Some(t) = me.upgrade() {
                        t.update_status(true);
                    }
                })
            })
            .collect();
        let reopens: Vec<menu::Item> = D::ENCODINGS
            .iter()
            .map(|(e, lbl)| {
                let (me, d, e) = (self.me.clone(), doc.clone(), e.to_string());
                menu::Item::new(&gettext(lbl), move || {
                    if let Some(t) = me.upgrade() {
                        t.reopen(&d, &e);
                    }
                })
            })
            .collect();
        let can_reopen = doc.file.borrow().is_some() && !doc.dirty();
        menu::popup(btn, vec![saves, vec![menu::Item::submenu(&tr!("Reopen with Encoding"), vec![reopens]).enabled(can_reopen)]], menu::Options::above());
    }

    fn reopen(&self, doc: &Rc<Document>, enc: &str) {
        let Some(file) = doc.file.borrow().clone() else { return };
        let (data, etag) = match file.load_contents(gio::Cancellable::NONE) {
            Ok(x) => x,
            Err(e) => {
                self.tell(&fmt(&tr!("“{name}” couldn't be reopened."), &[("name", &doc.name())]), e.message());
                return;
            }
        };
        *doc.etag.borrow_mut() = etag.map(|e| e.to_string());
        let text = D::decode_lossy(&data, enc);
        doc.set_text(&D::normalize(&text), false);
        *doc.encoding.borrow_mut() = enc.to_string();
        doc.bom.set(false);
        *doc.newline.borrow_mut() = D::detect_newline(&text).to_string();
        self.update_status(true);
        self.update_title();
    }

    fn newline_menu(&self, btn: &gtk::Button) {
        let Some(doc) = self.current() else { return };
        let items: Vec<menu::Item> = D::NEWLINE_LABELS
            .iter()
            .map(|(n, lbl)| {
                let (me, d, n) = (self.me.clone(), doc.clone(), n.to_string());
                menu::Item::check(&gettext(lbl), *doc.newline.borrow() == n, move |_on| {
                    if *d.newline.borrow() != n {
                        *d.newline.borrow_mut() = n.clone();
                        d.buffer.set_modified(true);
                        if let Some(t) = me.upgrade() {
                            t.update_status(true);
                        }
                    }
                })
            })
            .collect();
        menu::popup(btn, vec![items], menu::Options::above());
    }

    fn font_menu(&self, btn: &gtk::Button) {
        let cur = cfg_font();
        let item = |k: &'static str, lbl: &str| {
            let me = self.me.clone();
            menu::Item::check(&gettext(lbl), cur == k, move |_on| {
                if let Some(t) = me.upgrade() {
                    t.set_option("font", json!(k));
                }
            })
        };
        let sections = vec![vec![item(FONTS[0].0, FONTS[0].1)], FONTS[1..].iter().map(|(k, l)| item(k, l)).collect()];
        menu::popup(btn, sections, menu::Options::below());
    }

    fn goto_line(&self) {
        let Some(doc) = self.current() else { return };
        let col = gtk::Box::new(gtk::Orientation::Vertical, 6);
        col.add_css_class("te-goto");
        let entry = gtk::Entry::builder()
            .placeholder_text(fmt(&tr!("Line (1–{count}), or line:column"), &[("count", &doc.buffer.line_count().to_string())]))
            .build();
        col.append(&gtk::Label::builder().label(tr!("Go to Line")).xalign(0.0).build());
        col.append(&entry);
        let pop = sonata_ui::panel::popup(&self.pos_btn, &col, gtk::PositionType::Top);
        let me = self.me.clone();
        entry.connect_activate(move |e| {
            if let (Some((line, column)), Some(t)) = (parse_goto(&e.text()), me.upgrade()) {
                let clamp = |n: i64| n.min(i32::MAX as i64) as i32;
                t.go_to(clamp(line), clamp(column));
            }
            pop.popdown();
        });
        entry.grab_focus();
    }

    pub fn go_to(&self, line: i32, column: i32) {
        let Some(doc) = self.current() else { return };
        let buf = &doc.buffer;
        let line = line.clamp(1, buf.line_count()) - 1;
        let Some(mut it) = buf.iter_at_line(line) else { return };
        let mut end = it;
        if !end.ends_line() {
            end.forward_to_line_end();
        }
        it.set_line_offset((column - 1).clamp(0, end.line_offset()));
        buf.place_cursor(&it);
        doc.view.scroll_to_iter(&mut it, 0.2, true, 0.0, 0.3);
        doc.view.grab_focus();
    }

    // -- look --------------------------------------------------------------------------------
    fn set_option(&self, key: &str, value: Value) {
        if self.syncing.get() || cfg().get(key) == Some(&value) {
            return;
        }
        S.with(|s| {
            if let Some(c) = s.borrow_mut().cfg.as_mut() {
                c.insert(key.into(), value.clone());
            }
        });
        let _ = sonata_config::update("textedit", &json!({key: value}));
        apply_look_everywhere();
    }

    fn zoom(&self, step: i32) {
        let cur = cfg_size();
        let size = match step {
            0 => 14,
            s if s > 0 => SIZES.iter().copied().find(|x| *x > cur).unwrap_or(cur),
            _ => SIZES.iter().copied().rev().find(|x| *x < cur).unwrap_or(cur),
        };
        self.set_option("font_size", json!(size));
    }

    // -- find & replace ----------------------------------------------------------------------
    pub fn show_find(&self, replace: bool) {
        self.find_bar.set_reveal_child(true);
        if replace {
            self.replace_check.set_active(true);
        }
        if let Some(doc) = self.current() {
            if let Some((a, b)) = doc.buffer.selection_bounds() {
                let text = doc.buffer.text(&a, &b, false).to_string();
                if !text.contains('\n') {
                    self.find_entry.set_text(&text.chars().take(200).collect::<String>());
                }
            }
        }
        self.find_entry.grab_focus();
        self.find_entry.select_region(0, -1);
        if !self.find_entry.text().is_empty() {
            self.find(0, false);
        }
    }

    pub fn hide_find(&self) {
        for d in self.docs.borrow().iter() {
            let (a, b) = d.buffer.bounds();
            d.buffer.remove_tag(&d.found, &a, &b);
        }
        self.find_bar.set_reveal_child(false);
        if let Some(d) = self.current() {
            d.view.grab_focus();
        }
    }

    fn opts(&self) -> Opts {
        Opts { case: self.case_btn.is_active(), regex: self.regex_btn.is_active(), whole: self.word_btn.is_active() }
    }

    /// Every match in the current document (None: invalid pattern).
    fn spans(&self, doc: &Document, limit: usize) -> Option<Vec<(usize, usize)>> {
        self.find_count.remove_css_class("error");
        match search::find_all(&doc.text(), &self.find_entry.text(), self.opts(), limit) {
            Ok(s) => Some(s),
            Err(_) => {
                self.find_count.add_css_class("error");
                self.find_count.set_label(&tr!("Invalid pattern"));
                None
            }
        }
    }

    /// Highlight every match and select the next (1), previous (-1) or the
    /// first from the cursor (0). move_=false only refreshes the highlight.
    pub fn find(&self, direction: i32, move_: bool) {
        let Some(doc) = self.current() else { return };
        let buf = &doc.buffer;
        let (a, b) = buf.bounds();
        buf.remove_tag(&doc.found, &a, &b);
        let Some(spans) = self.spans(&doc, search::MAX_MATCHES) else { return };
        if self.find_entry.text().is_empty() {
            self.find_count.set_label("");
            return;
        }
        for (a, b) in &spans {
            buf.apply_tag(&doc.found, &buf.iter_at_offset(*a as i32), &buf.iter_at_offset(*b as i32));
        }
        let n = spans.len();
        let more = if n >= search::MAX_MATCHES { "+" } else { "" };
        self.find_count.set_label(&if n > 0 { fmt(&trn!("{n} found", "{n} found", n), &[("n", &format!("{n}{more}"))]) } else { tr!("Not found") });
        if n == 0 || !move_ {
            return;
        }
        let sel = buf.selection_bounds().map(|(a, _)| a.offset() as usize);
        let cur = buf.iter_at_mark(&buf.get_insert()).offset() as usize;
        let span = if direction < 0 {
            search::next_span(&spans, sel.unwrap_or(cur), true, false)
        } else {
            search::next_span(&spans, sel.unwrap_or(cur), false, direction == 0 || sel.is_none())
        };
        if let Some((a, b)) = span {
            let mut ia = buf.iter_at_offset(a as i32);
            buf.select_range(&ia, &buf.iter_at_offset(b as i32));
            doc.view.scroll_to_iter(&mut ia, 0.2, false, 0.0, 0.0);
        }
    }

    /// Replace the selected match, then select the next one.
    pub fn replace_one(&self) {
        let Some(doc) = self.current() else { return };
        if doc.readonly.get() {
            return;
        }
        let buf = &doc.buffer;
        let spans = self.spans(&doc, search::MAX_MATCHES).unwrap_or_default();
        if spans.is_empty() {
            self.find(1, true);
            return;
        }
        let cur = buf.selection_bounds().map(|(a, b)| (a.offset() as usize, b.offset() as usize));
        if let Some(cur) = cur.filter(|c| spans.contains(c)) {
            let repl = search::expand(&doc.text(), cur, &self.find_entry.text(), &self.replace_entry.text(), self.opts());
            self.replacing.set(true);
            buf.begin_user_action();
            let mut a = buf.iter_at_offset(cur.0 as i32);
            let mut b = buf.iter_at_offset(cur.1 as i32);
            buf.delete(&mut a, &mut b);
            buf.insert(&mut a, &repl);
            buf.end_user_action();
            self.replacing.set(false);
        }
        self.find(1, true);
    }

    pub fn replace_all(&self) -> usize {
        let Some(doc) = self.current() else { return 0 };
        if doc.readonly.get() {
            return 0;
        }
        let (needle, repl) = (self.find_entry.text().to_string(), self.replace_entry.text().to_string());
        let text = doc.text();
        let Some(spans) = self.spans(&doc, usize::MAX) else { return 0 };
        if spans.is_empty() {
            self.find_count.set_label(&tr!("Not found"));
            return 0;
        }
        let o = self.opts();
        let news: Option<Vec<String>> = o.regex.then(|| spans.iter().map(|s| search::expand(&text, *s, &needle, &repl, o)).collect());
        let buf = &doc.buffer;
        self.replacing.set(true);
        buf.begin_user_action();
        for i in (0..spans.len()).rev() {
            let mut a = buf.iter_at_offset(spans[i].0 as i32);
            let mut b = buf.iter_at_offset(spans[i].1 as i32);
            buf.delete(&mut a, &mut b);
            buf.insert(&mut a, news.as_ref().map(|n| n[i].as_str()).unwrap_or(&repl));
        }
        buf.end_user_action();
        self.replacing.set(false);
        self.find(0, false);
        let n = spans.len();
        self.find_count.set_label(&fmt(&trn!("{n} replaced", "{n} replaced", n), &[("n", &n.to_string())]));
        n
    }

    // -- keys --------------------------------------------------------------------------------
    fn key(&self, keyval: Key, state: ModifierType) -> bool {
        let ctrl = state.contains(ModifierType::CONTROL_MASK);
        let cmd = state.intersects(ModifierType::CONTROL_MASK | ModifierType::SUPER_MASK);
        let shift = state.contains(ModifierType::SHIFT_MASK);
        let alt = state.contains(ModifierType::ALT_MASK);
        let k = keyval.to_lower();
        if ctrl && matches!(keyval, Key::Tab | Key::ISO_Left_Tab | Key::KP_Tab) {
            self.cycle(if shift || keyval == Key::ISO_Left_Tab { -1 } else { 1 });
            return true;
        }
        if ctrl && matches!(keyval, Key::Page_Down | Key::Page_Up) {
            self.cycle(if keyval == Key::Page_Down { 1 } else { -1 });
            return true;
        }
        if !cmd {
            if keyval == Key::Escape && self.find_bar.reveals_child() {
                self.hide_find();
                return true;
            }
            if matches!(keyval, Key::Return | Key::KP_Enter) && shift && self.find_entry.has_focus() {
                self.find(-1, true);
                return true;
            }
            return false;
        }
        if shift && matches!(k, Key::bracketright | Key::braceright | Key::bracketleft | Key::braceleft) {
            self.cycle(if matches!(k, Key::bracketright | Key::braceright) { 1 } else { -1 });
            return true;
        }
        let digits = [Key::_1, Key::_2, Key::_3, Key::_4, Key::_5, Key::_6, Key::_7, Key::_8, Key::_9];
        if let Some(i) = digits.iter().position(|d| *d == keyval).filter(|_| !shift) {
            let docs = self.docs.borrow().clone();
            if !docs.is_empty() {
                let d = if i == 8 { docs[docs.len() - 1].clone() } else { docs[i.min(docs.len() - 1)].clone() };
                self.select(&d);
            }
            return true;
        }
        let Some(me) = self.me.upgrade() else { return false };
        match k {
            Key::n => {
                if let Some(app) = self.window.application() {
                    TeWindow::new(&app, None, None).window.present();
                }
            }
            Key::t => {
                me.new_tab(true, None);
            }
            Key::o => self.open_panel(),
            Key::s if shift => self.save_as(None, None),
            Key::s => self.save(None, None, false),
            Key::w => self.close_tab(None, None),
            Key::f => self.show_find(alt),
            Key::g => {
                if self.find_entry.text().is_empty() {
                    self.show_find(false);
                } else {
                    self.find(if shift { -1 } else { 1 }, true);
                }
            }
            Key::l => self.goto_line(),
            Key::plus | Key::equal | Key::KP_Add => self.zoom(1),
            Key::minus | Key::KP_Subtract => self.zoom(-1),
            Key::_0 => self.zoom(0),
            Key::z if shift => self.current().map(|d| d.buffer.redo()).unwrap_or(()),
            Key::z => self.current().map(|d| d.buffer.undo()).unwrap_or(()),
            Key::y => self.current().map(|d| d.buffer.redo()).unwrap_or(()),
            _ => return false,
        }
        true
    }

    // -- window life / session ---------------------------------------------------------------
    /// Never asks (macOS autosave): the last window, or one with unsaved
    /// documents, stays in the session and comes back next time.
    fn close_request(&self) {
        if self.closing.get() {
            return;
        }
        self.closing.set(true);
        let app = self.window.application();
        let others = windows().iter().any(|w| !std::ptr::eq(w.as_ref(), self) && w.window.application() == app);
        let docs = self.docs.borrow().clone();
        if !docs.is_empty() && (!others || docs.iter().any(|d| d.dirty())) {
            ensure_loaded();
            let st = self.state();
            S.with(|s| s.borrow_mut().parked.insert(0, st));
        }
        self.forget();
        save_now();
    }

    fn forget(&self) {
        S.with(|s| s.borrow_mut().windows.retain(|w| !std::ptr::eq(w.as_ref(), self)));
    }

    fn state(&self) -> Value {
        let (w, h) = if self.window.is_realized() { (self.window.width(), self.window.height()) } else { self.window.default_size() };
        let active = self.current().and_then(|c| self.index_of(&c)).unwrap_or(0);
        let tabs: Vec<Value> = self.docs.borrow().iter().map(|d| d.state()).collect();
        json!({"width": w, "height": h, "maximized": self.window.is_maximized(), "active": active, "tabs": tabs})
    }

    fn restore(self: &Rc<Self>, st: &Value) {
        if let (Some(w), Some(h)) = (st.get("width").and_then(Value::as_i64), st.get("height").and_then(Value::as_i64)) {
            if w > 0 && h > 0 {
                // saved on a bigger display: shrunk to this one
                sonata_ui::window::fit_default_size(self.window.upcast_ref(), (w as i32).max(360), (h as i32).max(240));
            }
        }
        if st.get("maximized").and_then(Value::as_bool).unwrap_or(false) {
            self.window.maximize();
        }
        for t in st.get("tabs").and_then(Value::as_array).into_iter().flatten() {
            let doc = Document::new(self);
            if doc.restore(t) {
                let n = self.docs.borrow().len();
                self.adopt(&doc, Some(n));
                self.update_tab(&doc);
            }
        }
        let docs = self.docs.borrow().clone();
        if !docs.is_empty() {
            let a = st.get("active").and_then(Value::as_i64).unwrap_or(0).clamp(0, docs.len() as i64 - 1) as usize;
            self.select(&docs[a]);
        }
    }
}

fn apply_look_everywhere() {
    apply_size();
    for w in windows() {
        for d in w.docs.borrow().iter() {
            d.apply_look();
        }
        w.sync_tools();
    }
}

fn find_open(f: &gio::File) -> Option<(Rc<TeWindow>, Rc<Document>)> {
    for w in windows() {
        for d in w.docs.borrow().iter() {
            if d.file.borrow().as_ref().is_some_and(|x| x.equal(f)) {
                return Some((w.clone(), d.clone()));
            }
        }
    }
    None
}

// -- session -------------------------------------------------------------------------------------
/// Keep the saved session (as parked windows) before writing a new one.
fn ensure_loaded() {
    let load = S.with(|s| !s.borrow().loaded);
    if load {
        let saved = session::load();
        S.with(|s| {
            let mut s = s.borrow_mut();
            s.loaded = true;
            s.parked.extend(saved);
        });
    }
}

pub fn schedule_save() {
    let pending = S.with(|s| s.borrow().timer.is_some());
    if !pending {
        let id = glib::timeout_add_local_once(Duration::from_millis(SAVE_DELAY_MS), || {
            S.with(|s| s.borrow_mut().timer = None);
            save_now();
        });
        S.with(|s| s.borrow_mut().timer = Some(id));
    }
}

pub fn save_now() {
    if let Some(id) = S.with(|s| s.borrow_mut().timer.take()) {
        id.remove();
    }
    ensure_loaded();
    let mut wins: Vec<Value> = windows().iter().filter(|w| !w.docs.borrow().is_empty() && !w.closing.get()).map(|w| w.state()).collect();
    wins.extend(S.with(|s| s.borrow().parked.clone()));
    if let Err(e) = session::save(&wins) {
        eprintln!("sonata2-rs textedit: session not saved: {e}");
    }
}

/// Bring back the saved documents as tabs of one window (the saved session
/// is used up: what is closed afterwards stays closed). The window, or None
/// when there was nothing to bring back.
pub fn restore_session(app: &impl IsA<gtk::Application>) -> Option<Rc<TeWindow>> {
    ensure_loaded();
    let states = S.with(|s| std::mem::take(&mut s.borrow_mut().parked));
    let mut shown = None;
    if let Some(st) = session::merged(&states) {
        let w = TeWindow::new(app, None, Some(&st));
        if w.docs.borrow().iter().any(|d| !d.blank() || d.file.borrow().is_some()) {
            w.window.present();
            shown = Some(w);
        } else {
            w.closing.set(true);
            w.forget();
            w.window.destroy();
        }
    }
    schedule_save();
    shown
}

/// Files become tabs of the frontmost window (files already open come
/// forward). Without files: the open window comes forward, or the saved
/// session comes back, or an Untitled window.
pub fn open_paths(app: &impl IsA<gtk::Application>, files: &[gio::File]) {
    let app_ = app.upcast_ref::<gtk::Application>().clone();
    let wins: Vec<Rc<TeWindow>> = windows().into_iter().filter(|w| w.window.application().as_ref() == Some(&app_)).collect();
    if files.is_empty() {
        if let Some(w) = wins.first() {
            w.window.present();
        } else if restore_session(app).is_none() {
            TeWindow::new(app, None, None).window.present();
        }
        return;
    }
    let mut front = wins.first().cloned().or_else(|| restore_session(app));
    for f in files {
        if let Some((w, d)) = find_open(f) {
            w.select(&d);
            front = Some(w);
            continue;
        }
        let w = front.get_or_insert_with(|| TeWindow::new(app, None, None)).clone();
        w.open_file(f);
    }
    if let Some(w) = front {
        w.window.present();
    }
}

/// Run TextEdit: files open as tabs of the running one; `--new-window`
/// gives a fresh window even when one is open (the Dock's New Window).
pub fn run(args: &[String]) -> glib::ExitCode {
    let app = adw::Application::builder().application_id(crate::app_id()).flags(gio::ApplicationFlags::HANDLES_OPEN).build();
    let force = args.iter().find_map(|a| match a.as_str() {
        "--dark" => Some("dark"),
        "--light" => Some("light"),
        _ => None,
    });
    let ready = Rc::new(Cell::new(false));
    let start = {
        let ready = ready.clone();
        move |app: &adw::Application| {
            if !ready.get() {
                ready.set(true);
                sonata_ui::setup();
                if let Some(f) = force {
                    sonata_ui::force_appearance(f);
                }
                app.connect_shutdown(|_| save_now());
                app.connect_window_removed(|_, w| S.with(|s| s.borrow_mut().windows.retain(|x| x.window.upcast_ref::<gtk::Window>() != w)));
            }
        }
    };
    let act = gio::SimpleAction::new("new-window", None);
    let (a, s) = (app.clone(), start.clone());
    act.connect_activate(move |_, _| {
        s(&a);
        TeWindow::new(&a, None, None).window.present();
    });
    app.add_action(&act);
    let s = start.clone();
    app.connect_open(move |app, files, _hint| {
        s(app);
        open_paths(app, files);
    });
    let new_window = args.iter().any(|a| a == "--new-window");
    let s = start.clone();
    app.connect_activate(move |app| {
        s(app);
        if new_window {
            app.activate_action("new-window", None);
        } else {
            open_paths(app, &[]);
        }
    });
    if new_window {
        // the desktop entry's New Window: the running TextEdit makes it
        if app.register(gio::Cancellable::NONE).is_ok() && app.is_remote() {
            app.activate_action("new-window", None);
            return glib::ExitCode::SUCCESS;
        }
    }
    let argv0 = std::env::args().next().unwrap_or_default();
    let files: Vec<String> = args.iter().filter(|a| !a.starts_with("--")).cloned().collect();
    let mut argv = vec![argv0];
    argv.extend(files);
    app.run_with_args(&argv)
}

#[cfg(test)]
mod tests {
    use super::*;
    use sourceview5::prelude::ViewExt;
    use std::time::Instant;

    #[test]
    fn test_go_to_line_moved_on_a_signed_number() {
        // Go to Line took "+5" as line 5 and "-3" as line 1 (Rust's number
        // parsing); the Python field only takes digits and stays put otherwise.
        assert_eq!(parse_goto("12"), Some((12, 1)));
        assert_eq!(parse_goto(" 12 : 3 "), Some((12, 3)));
        assert_eq!(parse_goto("12,3"), Some((12, 3)));
        for bad in ["+5", "-3", "12:", "12:x", "", "1 2", "a"] {
            assert_eq!(parse_goto(bad), None, "{bad}");
        }
        assert_eq!(parse_goto("99999999999999999999"), Some((i64::MAX, 1))); // past the end: the last line
    }

    #[test]
    fn test_first_window_was_not_roomy() {
        // A first TextEdit window (no size kept yet) opened at 760 x 580 on a
        // 1080p display; Python's opens at about two thirds of it.
        assert_eq!(roomy_size(760, 580, Some((1920, 1080))), (1267, 702));
        assert_eq!(roomy_size(760, 580, Some((1024, 600))), (760, 580)); // never smaller than the default
        assert_eq!(roomy_size(760, 580, None), (760, 580));
    }

    // -- the window (tests/test_textedit.py's TextEditTest), where GTK can start

    /// GTK for these tests, on this thread: none without a display, and none
    /// when another test's thread has it already (GTK lives on one thread).
    fn gtk_here() -> bool {
        if gtk::is_initialized() && !gtk::is_initialized_main_thread() {
            return false;
        }
        gtk::init().is_ok()
    }

    fn settle(ms: u64) {
        let end = Instant::now() + Duration::from_millis(ms);
        while Instant::now() < end {
            glib::MainContext::default().iteration(false);
        }
    }

    fn write(name: &str, text: &str) -> String {
        let dir = tempfile::tempdir().unwrap().keep();
        let p = dir.join(name);
        std::fs::write(&p, text).unwrap();
        p.display().to_string()
    }

    fn reset() {
        for w in windows() {
            w.closing.set(true);
            w.forget();
            w.window.destroy();
        }
        S.with(|s| {
            let mut s = s.borrow_mut();
            s.parked.clear();
            s.loaded = true;
        });
        settle(20);
    }

    fn fresh_launch() {
        S.with(|s| {
            let mut s = s.borrow_mut();
            s.parked.clear();
            s.loaded = false;
        });
    }

    fn front() -> Rc<TeWindow> {
        windows()[0].clone()
    }

    fn path_of(d: &Document) -> Option<String> {
        d.file.borrow().as_ref().and_then(|f| f.path()).map(|p| p.display().to_string())
    }

    fn session_json() -> Value {
        serde_json::from_slice(&std::fs::read(session::data_dir().join("session.json")).unwrap()).unwrap()
    }

    #[test]
    fn window_and_session() {
        if !gtk_here() {
            return; // (no display: parse_goto and session.rs are still checked)
        }
        let _g = crate::TEST_ENV.lock().unwrap_or_else(|e| e.into_inner());
        let data = tempfile::tempdir().unwrap();
        std::env::set_var("XDG_DATA_HOME", data.path());
        let app = adw::Application::builder().application_id("io.test.textedit.rs").flags(gio::ApplicationFlags::NON_UNIQUE).build();
        app.register(gio::Cancellable::NONE).unwrap();
        sonata_ui::setup();
        reset();
        session_round_trip(&app);
        reset();
        closing_one_of_several_windows(&app);
        reset();
        session_comes_back_as_tabs_and_closed_tabs_stay_closed(&app);
        reset();
        look_settings(&app);
        reset();
    }

    fn session_round_trip(app: &adw::Application) {
        let path = write("kept.txt", "saved text\nline 2\n");
        let w = TeWindow::new(app, Some(gio::File::for_path(&path)), None);
        w.window.present();
        settle(50);
        let d0 = w.current().unwrap();
        d0.buffer.insert(&mut d0.buffer.end_iter(), "unsaved edit\n");
        d0.buffer.place_cursor(&d0.buffer.iter_at_offset(5));
        let u = w.new_tab(true, None);
        u.buffer.insert_at_cursor("scratch notes ✓");
        w.new_tab(true, None); // empty Untitled: not brought back
        let second = w.docs.borrow()[1].clone();
        w.select(&second);
        let names: Vec<String> = w.docs.borrow().iter().take(2).map(|d| d.name()).collect();
        save_now();
        let tabs = session_json()["windows"][0]["tabs"].clone();
        let backups: Vec<bool> = tabs.as_array().unwrap().iter().map(|t| t["backup"].as_bool().unwrap()).collect();
        assert_eq!(backups, [true, true, false]);
        assert_eq!(session::read_buffer(&u.id.borrow()).as_deref(), Some("scratch notes ✓"));
        // "quit": the last window closes without asking and stays in the session
        w.window.close();
        settle(50);
        assert_eq!(std::fs::metadata(&path).unwrap().len() as usize, "saved text\nline 2\n".len()); // the file itself untouched
        assert_eq!(S.with(|s| s.borrow().parked.len()), 1);
        // a new process: TextEdit starts without files
        fresh_launch();
        open_paths(app, &[]);
        settle(100);
        let w2 = front();
        let docs = w2.docs.borrow().clone();
        assert_eq!(docs.iter().map(|d| d.name()).collect::<Vec<_>>(), names);
        assert_eq!(docs[0].text(), "saved text\nline 2\nunsaved edit\n");
        assert!(docs[0].dirty());
        assert_eq!(path_of(&docs[0]).as_deref(), Some(path.as_str()));
        assert_eq!(docs[0].buffer.iter_at_mark(&docs[0].buffer.get_insert()).offset(), 5);
        assert_eq!(docs[1].text(), "scratch notes ✓");
        assert!(w2.is_current(&docs[1])); // the tab that was active
        // closing the untitled tab for good (Don't Save) drops its text from the disk
        w2.remove_tab(&docs[1]);
        save_now();
        assert_eq!(session::read_buffer(&u.id.borrow()), None);
        // saving the edited file: nothing unsaved is left in the session
        w2.save(Some(docs[0].clone()), None, false);
        save_now();
        let any_backup = session_json()["windows"].as_array().unwrap().iter().flat_map(|w| w["tabs"].as_array().unwrap().clone()).any(|t| t["backup"] == json!(true));
        assert!(!any_backup);
    }

    fn closing_one_of_several_windows(app: &adw::Application) {
        let w1 = TeWindow::new(app, None, None);
        let w2 = TeWindow::new(app, None, None);
        w1.window.present();
        w2.window.present();
        w2.current().unwrap().buffer.insert_at_cursor("don't lose me");
        w2.window.close(); // no question, the text is kept
        settle(50);
        assert_eq!(S.with(|s| s.borrow().parked.len()), 1);
        w1.window.close(); // clean and last: kept too
        settle(50);
        assert_eq!(S.with(|s| s.borrow().parked.len()), 2);
        fresh_launch();
        assert!(restore_session(app).is_some());
        assert_eq!(windows().len(), 1); // as tabs of one window
        let texts: Vec<String> = windows().iter().flat_map(|w| w.docs.borrow().iter().map(|d| d.text()).collect::<Vec<_>>()).collect();
        assert!(texts.iter().any(|t| t == "don't lose me"));
    }

    fn session_comes_back_as_tabs_and_closed_tabs_stay_closed(app: &adw::Application) {
        // regression (Vini, Python): opening a file kept the saved session
        // hidden, so old windows piled up and all came back at once as
        // windows, even after closing them
        session::save(&[]).unwrap(); // no session left by the others
        let (a, b, c) = (write("a.txt", "A"), write("b.txt", "B"), write("c.txt", "C"));
        for p in [&a, &b, &c] {
            // three launches, each with a file
            fresh_launch();
            open_paths(app, &[gio::File::for_path(p)]);
            settle(50);
            assert_eq!(windows().len(), 1);
            if *p != c {
                front().window.close();
                settle(50);
            }
        }
        let w = front();
        let mut open: Vec<String> = w.docs.borrow().iter().filter_map(|d| path_of(d)).collect();
        open.sort();
        let mut want = vec![a.clone(), b.clone(), c.clone()];
        want.sort();
        assert_eq!(open, want);
        assert!(S.with(|s| s.borrow().parked.is_empty())); // nothing left waiting
        for p in [&a, &b] {
            let d = w.docs.borrow().iter().find(|d| path_of(d).as_deref() == Some(p.as_str())).cloned().unwrap();
            w.remove_tab(&d);
        }
        w.window.close();
        settle(50);
        for _ in 0..2 {
            // two more launches
            fresh_launch();
            open_paths(app, &[]);
            settle(50);
            assert_eq!(windows().len(), 1);
            let paths: Vec<Option<String>> = front().docs.borrow().iter().map(|d| path_of(d)).collect();
            assert_eq!(paths, [Some(c.clone())]);
            front().window.close();
            settle(50);
        }
    }

    fn look_settings(app: &adw::Application) {
        let w = TeWindow::new(app, Some(gio::File::for_path(write("main.py", "print(1)\n"))), None);
        w.window.present();
        assert!(w.current().unwrap().view.has_css_class("te-mono")); // Automatic: code is monospaced
        w.open_file(&gio::File::for_path(write("prose.txt", "Words.")));
        assert!(w.current().unwrap().view.has_css_class("te-sans"));
        w.set_option("font", json!("serif"));
        assert!(w.docs.borrow().iter().all(|d| d.view.has_css_class("te-serif")));
        let size = cfg_size();
        w.zoom(1);
        assert!(cfg_size() > size);
        assert_eq!(w.size_label.label(), cfg_size().to_string());
        w.zoom(0);
        assert_eq!(cfg_size(), 14);
        w.wrap_btn.set_active(false);
        assert_eq!(w.current().unwrap().view.wrap_mode(), gtk::WrapMode::None);
        w.lines_btn.set_active(true);
        assert!(w.current().unwrap().view.shows_line_numbers());
        // kept in textedit.json, where the Python TextEdit reads them
        let saved = sonata_config::stored("textedit");
        assert_eq!(saved.get("font"), Some(&json!("serif")));
        assert_eq!(saved.get("wrap"), Some(&json!(false)));
        assert_eq!(saved.get("line_numbers"), Some(&json!(true)));
        w.set_option("font", json!("auto"));
        w.lines_btn.set_active(false);
        w.wrap_btn.set_active(true);
    }
}
