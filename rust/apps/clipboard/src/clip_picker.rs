//! Clipboard history panel (shell/clip_picker.py; Super+V, like Windows'
//! Win+V, drawn the macOS way): a glass panel with the last copies (newest
//! first; images as thumbnails), a search field and Clear All. Click a
//! copy, or pick it with the arrow keys and Return, and it is pasted into
//! the window that had focus (it goes to the clipboard, then Ctrl+V --
//! Ctrl+Shift+V in terminals -- with its real keycode: paste()). The
//! history itself is memory only, never on disk (clipboard.rs).

use crate::clipboard::{ClipItem, History};
use gtk::prelude::*;
use serde_json::{json, Value};
use sonata_i18n::{tr, trn};
use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

const WIDTH: i32 = 380;
const PREVIEW_LINES: usize = 3;
const THUMB_H: i32 = 110;
/// app ids where Ctrl+V is not "paste" (terminals: Ctrl+Shift+V); the menu
/// bar's tabs list (shell/apptabs.py TERMINALS), not ported yet
pub const TERMINALS: [&str; 16] = [
    "terminal", "konsole", "kitty", "alacritty", "foot", "ghostty", "wezterm", "xterm", "tilix", "terminator", "kgx", "console", "ptyxis", "blackbox", "rio", "warp",
];

/// Python's str.splitlines() boundaries
fn is_line_break(c: char) -> bool {
    matches!(c, '\n' | '\r' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}')
}

/// A copy's first non-empty lines.
pub fn preview(text: &str) -> String {
    let lines: Vec<&str> = text.trim().split(is_line_break).map(str::trim).filter(|l| !l.is_empty()).take(PREVIEW_LINES).collect();
    if lines.is_empty() {
        text.trim().to_string()
    } else {
        lines.join("\n")
    }
}

/// "12 characters · 2 lines"
pub fn meta(text: &str) -> String {
    let n = text.chars().count();
    let lines = text.matches('\n').count() + 1;
    let size = sonata_i18n::fmt(&trn!("{n} character", "{n} characters", n), &[("n", &n.to_string())]);
    if lines > 1 {
        size + " · " + &sonata_i18n::fmt(&trn!("{n} line", "{n} lines", lines), &[("n", &lines.to_string())])
    } else {
        size
    }
}

/// The search keeps this copy (`q` already lower-cased; images match
/// "image" in English and in the user's language).
pub fn matches(item: &ClipItem, q: &str) -> bool {
    if q.is_empty() {
        return true;
    }
    match item {
        ClipItem::Text(t) => t.to_lowercase().contains(q),
        ClipItem::Image(_) => "image".contains(q) || tr!("Image").to_lowercase().contains(q),
    }
}

pub struct ClipboardPicker {
    pub win: gtk::Window,
    search: gtk::SearchEntry,
    list: gtk::ListBox,
    scroll: gtk::ScrolledWindow,
    empty: gtk::Label,
    clear_btn: gtk::Button,
    history: Rc<History>,
    /// the rows' copies, in the list's order
    shown: RefCell<Vec<ClipItem>>,
}

impl ClipboardPicker {
    pub fn new(app: &gtk::Application, history: Rc<History>) -> Rc<Self> {
        let win = gtk::Window::builder().application(app).title(tr!("Clipboard").as_str()).decorated(false).css_classes(["sonata-clip-picker"]).build();
        let panel = gtk::Box::builder().orientation(gtk::Orientation::Vertical).css_classes(["clip-panel"]).build();
        glass_class(&panel);
        let head = gtk::Box::builder().css_classes(["clip-head"]).build();
        head.append(&gtk::Label::builder().label(tr!("Clipboard").as_str()).xalign(0.0).hexpand(true).css_classes(["clip-title"]).build());
        let clear_btn = gtk::Button::builder().label(tr!("Clear All").as_str()).css_classes(["clip-clear"]).can_focus(false).build();
        head.append(&clear_btn);
        panel.append(&head);
        let search = gtk::SearchEntry::builder().placeholder_text(tr!("Search").as_str()).build();
        panel.append(&search);
        let list = gtk::ListBox::builder().selection_mode(gtk::SelectionMode::Browse).activate_on_single_click(true).build();
        let scroll = gtk::ScrolledWindow::builder().child(&list).hscrollbar_policy(gtk::PolicyType::Never).propagate_natural_height(true).max_content_height(420).build();
        panel.append(&scroll);
        let empty = gtk::Label::builder().label(tr!("Nothing copied yet").as_str()).css_classes(["clip-empty"]).build();
        panel.append(&empty);
        // long copied text never widens it
        let boxed = sonata_ui::fixed::FixedWidth::new(&panel, WIDTH);
        win.set_child(Some(&boxed));

        let me = Rc::new(Self { win, search, list, scroll, empty, clear_btn, history, shown: RefCell::default() });
        let weak = Rc::downgrade(&me);
        me.clear_btn.connect_clicked(move |_| {
            if let Some(me) = weak.upgrade() {
                me.history.clear();
                me.fill();
            }
        });
        let weak = Rc::downgrade(&me);
        me.search.connect_search_changed(move |_| {
            if let Some(me) = weak.upgrade() {
                me.fill();
            }
        });
        let weak = Rc::downgrade(&me);
        me.search.connect_activate(move |_| {
            if let Some(me) = weak.upgrade() {
                me.paste_selected();
            }
        });
        let weak = Rc::downgrade(&me);
        me.list.connect_row_activated(move |_, row| {
            if let Some(me) = weak.upgrade() {
                let item = me.shown.borrow().get(row.index() as usize).cloned();
                if let Some(item) = item {
                    me.paste(&item);
                }
            }
        });

        let keys = gtk::EventControllerKey::builder().propagation_phase(gtk::PropagationPhase::Capture).build();
        let weak = Rc::downgrade(&me);
        keys.connect_key_pressed(move |_, keyval, _, _| match weak.upgrade() {
            Some(me) => me.key(keyval),
            None => glib::Propagation::Proceed,
        });
        me.win.add_controller(keys);
        // in the middle of the screen, a little lower, like the emoji picker;
        // a click outside closes it (Vini: it kept the keyboard, so it never
        // lost focus)
        let w = me.win.downgrade();
        let close = move || {
            if let Some(w) = w.upgrade() {
                w.set_visible(false);
            }
        };
        if !crate::picker::centered_picker(&me.win, boxed.upcast_ref(), "sonata2-clipboard", close) {
            me.win.set_resizable(false);
            // a plain window: closed when another window takes the focus
            me.win.connect_is_active_notify(|w| {
                if !w.is_active() && w.is_visible() {
                    let w = w.downgrade();
                    glib::timeout_add_local_once(Duration::from_millis(120), move || {
                        if let Some(w) = w.upgrade().filter(|w| !w.is_active()) {
                            w.set_visible(false);
                        }
                    });
                }
            });
        }
        let weak = Rc::downgrade(&me);
        me.history.connect(move || {
            if let Some(me) = weak.upgrade().filter(|me| me.win.is_visible()) {
                me.fill();
            }
        });
        me
    }

    pub fn history(&self) -> &Rc<History> {
        &self.history
    }

    // -- content ----------------------------------------------------------------------------------
    fn fill(&self) {
        let q = self.search.text().trim().to_lowercase();
        let all = self.history.items();
        let items: Vec<ClipItem> = all.iter().filter(|t| matches(t, &q)).cloned().collect();
        self.list.remove_all();
        for item in &items {
            let row = gtk::ListBoxRow::new();
            let b = gtk::Box::new(gtk::Orientation::Vertical, 4);
            let tex = match item {
                ClipItem::Image(i) => i.texture(WIDTH - 60, THUMB_H),
                ClipItem::Text(_) => None,
            };
            if let Some(tex) = tex {
                // an image: its thumbnail, at its own size
                let pic = gtk::Picture::builder().paintable(&tex).can_shrink(false).content_fit(gtk::ContentFit::Fill).css_classes(["clip-thumb"]).overflow(gtk::Overflow::Hidden).build();
                pic.set_size_request(tex.width(), tex.height());
                let holder = gtk::Box::builder().halign(gtk::Align::Start).build();
                holder.append(&pic);
                b.append(&holder);
                b.append(&gtk::Label::builder().label(item.label().as_str()).xalign(0.0).css_classes(["clip-meta"]).build());
            } else {
                let text = item.label();
                b.append(
                    &gtk::Label::builder()
                        .label(preview(&text).as_str())
                        .xalign(0.0)
                        .wrap(true)
                        .wrap_mode(gtk::pango::WrapMode::WordChar)
                        .lines(PREVIEW_LINES as i32)
                        .ellipsize(gtk::pango::EllipsizeMode::End)
                        .max_width_chars(44)
                        .build(),
                );
                b.append(&gtk::Label::builder().label(meta(&text).as_str()).xalign(0.0).css_classes(["clip-meta"]).build());
            }
            row.set_child(Some(&b));
            self.list.append(&row);
        }
        self.scroll.set_visible(!items.is_empty());
        self.empty.set_label(&if !q.is_empty() && !all.is_empty() { tr!("No Results") } else { tr!("Nothing copied yet") });
        self.empty.set_visible(items.is_empty());
        self.clear_btn.set_sensitive(!all.is_empty());
        self.shown.replace(items);
        if let Some(first) = self.list.row_at_index(0) {
            self.list.select_row(Some(&first));
        }
    }

    fn move_by(&self, step: i32) {
        let i = self.list.selected_row().map_or(-1, |r| r.index()) + step;
        if let Some(next) = self.list.row_at_index(i.max(0)) {
            self.list.select_row(Some(&next));
            next.grab_focus();
            // back to typing in the field, its text left as it was (a
            // SearchEntry has no grab_focus_without_selecting: its Text does)
            if let Some(text) = self.search.delegate().and_downcast::<gtk::Text>() {
                text.grab_focus_without_selecting();
            }
        }
    }

    fn key(&self, keyval: gtk::gdk::Key) -> glib::Propagation {
        use gtk::gdk::Key;
        if keyval == Key::Escape {
            self.win.set_visible(false);
        } else if keyval == Key::Down || keyval == Key::Up {
            self.move_by(if keyval == Key::Down { 1 } else { -1 });
        } else if keyval == Key::Return || keyval == Key::KP_Enter {
            self.paste_selected();
        } else {
            return glib::Propagation::Proceed;
        }
        glib::Propagation::Stop
    }

    // -- pasting ----------------------------------------------------------------------------------
    fn paste_selected(&self) {
        let item = self.list.selected_row().and_then(|r| self.shown.borrow().get(r.index() as usize).cloned());
        if let Some(item) = item {
            self.paste(&item);
        }
    }

    fn paste(&self, item: &ClipItem) {
        self.history.copy(item);
        self.win.set_visible(false);
        // once the keyboard is back on the app and wl-copy owns the clipboard
        glib::timeout_add_local_once(Duration::from_millis(200), paste);
    }

    pub fn open(&self) {
        self.search.set_text("");
        self.fill();
        self.win.present();
        self.search.grab_focus();
    }
}

/// The glass panel's "solid" class in step with the setting
/// (ui.theme.glass_class).
pub(crate) fn glass_class(w: &impl IsA<gtk::Widget>) {
    let weak = w.upcast_ref::<gtk::Widget>().downgrade();
    let sync = move || {
        if let Some(w) = weak.upgrade() {
            if sonata_ui::glass() {
                w.remove_css_class("solid")
            } else {
                w.add_css_class("solid")
            }
        }
    };
    sync();
    sonata_ui::on_change(sync);
}

/// Paste in the focused app: Ctrl+V (Ctrl+Shift+V in a terminal) with its
/// real keycode (vkeyboard.rs). Vini: Instagram took no emoji from the
/// Character Viewer -- wtype gives the first key it types keycode 1, so the
/// page saw Ctrl+Esc (event.code "Escape") and ignored it; WhatsApp only
/// looks at the letter. wtype only when there's no virtual keyboard.
pub fn paste() {
    paste_with(terminal_focused(), crate::vkeyboard::press, glib::find_program_in_path("wtype").is_some(), |args| {
        let _ = std::process::Command::new("wtype").args(args).spawn().map(|mut c| std::thread::spawn(move || c.wait()));
    });
}

/// paste(), with the keyboard and wtype handed in (tests).
pub fn paste_with(terminal: bool, press: impl Fn(&str, &[&str]) -> bool, has_wtype: bool, wtype: impl Fn(&[&str])) {
    let mods: &[&str] = if terminal { &["ctrl", "shift"] } else { &["ctrl"] };
    if press("v", mods) {
        return;
    }
    if has_wtype {
        wtype(&paste_keys(terminal));
    }
}

/// wtype's arguments for Paste in the focused app (paste()'s fallback):
/// Ctrl+V, or Ctrl+Shift+V in a terminal.
pub fn paste_keys(terminal: bool) -> Vec<&'static str> {
    if terminal {
        vec!["-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"]
    } else {
        vec!["-M", "ctrl", "v", "-m", "ctrl"]
    }
}

fn terminal_focused() -> bool {
    // no IPC: the usual Ctrl+V
    sonata_backend::wfipc::call("window-rules/list-views", json!({})).is_some_and(|v| terminal_in(&v))
}

/// The activated view in Wayfire's list is a terminal.
pub fn terminal_in(views: &Value) -> bool {
    let app = views
        .as_array()
        .and_then(|vs| vs.iter().find(|v| v.get("activated").and_then(Value::as_bool).unwrap_or(false)))
        .and_then(|v| v.get("app-id").and_then(Value::as_str))
        .unwrap_or("")
        .to_lowercase();
    TERMINALS.iter().any(|t| app.contains(t))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn picker_text_helpers_and_terminal_detection() {
        // Super+V rows: the preview keeps the first non-empty lines;
        // terminals get Ctrl+Shift+V
        let _e = crate::testenv::env();
        assert_eq!(preview("\n  a \n\n b\nc\nd"), "a\nb\nc");
        assert_eq!(meta("x"), "1 character");
        assert_eq!(meta("ab\ncd"), "5 characters · 2 lines");
        let views = |id: &str| json!([{"app-id": "other", "activated": false}, {"app-id": id, "activated": true}]);
        assert!(terminal_in(&views("org.gnome.Ptyxis")));
        assert!(!terminal_in(&views("firefox")));
        assert!(!terminal_in(&Value::Null));
    }

    #[test]
    fn pasted_with_the_real_keycode_wtype_only_without_a_virtual_keyboard() {
        let presses = RefCell::new(Vec::new());
        let spawned = RefCell::new(Vec::new());
        let press = |ok: bool| {
            let p = &presses;
            move |k: &str, m: &[&str]| {
                p.borrow_mut().push((k.to_string(), m.join("+")));
                ok
            }
        };
        let spawn = |a: &[&str]| spawned.borrow_mut().push(a.join(" "));
        paste_with(false, press(true), true, spawn);
        paste_with(true, press(true), true, spawn);
        assert_eq!(*presses.borrow(), [("v".to_string(), "ctrl".to_string()), ("v".to_string(), "ctrl+shift".to_string())]);
        assert!(spawned.borrow().is_empty());
        paste_with(false, press(false), true, spawn); // no virtual keyboard: wtype
        assert_eq!(*spawned.borrow(), ["-M ctrl v -m ctrl"]);
        paste_with(false, press(false), false, spawn); // nor wtype: nothing
        assert_eq!(spawned.borrow().len(), 1);
    }

    #[test]
    fn paste_keys_in_a_terminal() {
        assert_eq!(paste_keys(true), ["-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"]);
    }

    #[test]
    fn search_matches_text_and_images() {
        let _e = crate::testenv::env();
        let t = ClipItem::Text("Hello World".into());
        assert!(matches(&t, "") && matches(&t, "world") && !matches(&t, "image"));
        let i = ClipItem::Image(Rc::new(crate::clipboard::ClipImage::new(vec![])));
        assert!(matches(&i, "ima") && !matches(&i, "hello"));
    }
}
