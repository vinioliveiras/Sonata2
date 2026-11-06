//! The note editor (sonata2/notes/editor.py; macOS Notes' page): a TextView
//! with paragraph styles (Title, Heading, Subheading, Body, bulleted /
//! numbered / checklist lines) and character styles (bold, italic,
//! underline, strikethrough), loaded from and saved to markup.rs's text.
//!
//! Paragraph styles are tags over the whole line (its newline too, so an
//! empty line keeps its style); list lines start with a marker: "• ", "1. "
//! (renumbered as lines come and go) or a check circle (a child widget).
//! Return continues a list (on an empty item it ends the list) and goes back
//! to Body after a title or heading; Backspace right after a marker removes
//! it. Typed text takes the character styles of the text before it, or the
//! ones toggled with ⌘B / ⌘I / ⌘U while nothing is selected.

use crate::markup::{self, Line, Tags, INLINE, LISTS};
use gtk::gdk::{Key, ModifierType};
use gtk::{glib, pango, prelude::*};
use sonata_i18n::tr;
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};

const PARA: [&str; 4] = ["title", "heading", "subheading", "item"];
/// page margin (px)
const MARGIN: i32 = 28;
/// list items: wrapped lines hang under the text, not the marker
const HANG: i32 = 20;

pub fn style_of(kind: &str) -> Option<&'static str> {
    match kind {
        "title" => Some("title"),
        "heading" => Some("heading"),
        "subheading" => Some("subheading"),
        "bullet" | "number" | "check" => Some("item"),
        _ => None,
    }
}

fn inline_name(name: &str) -> Option<&'static str> {
    INLINE.iter().copied().find(|n| *n == name)
}

pub struct NoteEditor {
    pub scroll: gtk::ScrolledWindow,
    pub view: gtk::TextView,
    pub buffer: gtk::TextBuffer,
    date: gtk::Label,
    /// character styles new text gets
    typing: RefCell<Tags>,
    /// (line, style) for an empty last line (nothing to tag)
    pending: Cell<Option<(i32, Option<&'static str>)>>,
    /// our own edits: no auto-styling
    guard: Cell<i32>,
    /// loading a note: no "edited"
    loading: Cell<bool>,
    inserting: Cell<bool>,
    /// text typed or pasted: (start offset, characters), styled once it's in
    inserted: Cell<Option<(i32, i32)>>,
    pub on_edited: RefCell<Option<Box<dyn Fn()>>>,
    me: Weak<NoteEditor>,
}

impl NoteEditor {
    pub fn new() -> Rc<Self> {
        let view = gtk::TextView::builder()
            .wrap_mode(gtk::WrapMode::WordChar)
            .left_margin(MARGIN)
            .right_margin(MARGIN)
            .top_margin(4)
            .bottom_margin(60)
            .pixels_below_lines(3)
            .build();
        view.add_css_class("nt-editor");
        let scroll = gtk::ScrolledWindow::builder().vexpand(true).hexpand(true).hscrollbar_policy(gtk::PolicyType::Never).child(&view).build();
        let b = view.buffer();
        b.set_enable_undo(true);
        let tag = |name: &str, props: &[(&str, &dyn ToValue)]| {
            b.create_tag(Some(name), props);
        };
        tag("title", &[("weight", &700i32), ("scale", &1.75f64), ("pixels-below-lines", &8i32)]);
        tag("heading", &[("weight", &700i32), ("scale", &1.4f64), ("pixels-above-lines", &10i32), ("pixels-below-lines", &4i32)]);
        tag("subheading", &[("weight", &700i32), ("scale", &1.15f64), ("pixels-above-lines", &6i32), ("pixels-below-lines", &2i32)]);
        tag("item", &[("left-margin", &(MARGIN + HANG)), ("indent", &(-HANG))]);
        tag("marker", &[]);
        tag("bold", &[("weight", &700i32)]);
        tag("italic", &[("style", &pango::Style::Italic)]);
        tag("underline", &[("underline", &pango::Underline::Single)]);
        tag("strike", &[("strikethrough", &true)]);
        let date = gtk::Label::builder().halign(gtk::Align::Center).build();
        date.add_css_class("nt-date");
        view.set_gutter(gtk::TextWindowType::Top, Some(&date));
        let ed = Rc::new_cyclic(|me| NoteEditor {
            scroll,
            view,
            buffer: b,
            date,
            typing: RefCell::default(),
            pending: Cell::new(None),
            guard: Cell::new(0),
            loading: Cell::new(false),
            inserting: Cell::new(false),
            inserted: Cell::new(None),
            on_edited: RefCell::new(None),
            me: me.clone(),
        });
        let w = Rc::downgrade(&ed);
        ed.buffer.connect_insert_text(move |_, it, text| {
            if let Some(e) = w.upgrade() {
                e.inserting.set(true);
                if e.guard.get() == 0 {
                    e.inserted.set(Some((it.offset(), text.chars().count() as i32)));
                }
            }
        });
        let w = Rc::downgrade(&ed);
        ed.buffer.connect_changed(move |_| {
            let Some(e) = w.upgrade() else { return };
            e.inserting.set(false);
            if let Some((start, n)) = e.inserted.take() {
                e.style_inserted(start, start + n);
            }
            if !e.loading.get() {
                e.edited();
            }
        });
        let w = Rc::downgrade(&ed);
        ed.buffer.connect_mark_set(move |b, it, mark| {
            if let Some(e) = w.upgrade() {
                if mark == &b.get_insert() {
                    e.mark_set(it);
                }
            }
        });
        let keys = gtk::EventControllerKey::new();
        keys.set_propagation_phase(gtk::PropagationPhase::Capture);
        let w = Rc::downgrade(&ed);
        keys.connect_key_pressed(move |_, keyval, _code, state| match w.upgrade() {
            Some(e) if e.key(keyval, state) => glib::Propagation::Stop,
            _ => glib::Propagation::Proceed,
        });
        ed.view.add_controller(keys);
        ed
    }

    fn edited(&self) {
        if let Some(f) = self.on_edited.borrow().as_ref() {
            f();
        }
    }

    fn iter_at_line(&self, line: i32) -> gtk::TextIter {
        self.buffer.iter_at_line(line).unwrap_or_else(|| self.buffer.end_iter())
    }

    fn tag(&self, name: &str) -> Option<gtk::TextTag> {
        self.buffer.tag_table().lookup(name)
    }

    // -- load / save --------------------------------------------------------------------------
    pub fn load(&self, md: &str, editable: bool) {
        self.loading.set(true);
        self.guard.set(self.guard.get() + 1);
        let b = &self.buffer;
        b.begin_irreversible_action();
        b.set_text("");
        let lines = markup::parse(md);
        for (i, l) in lines.iter().enumerate() {
            if i > 0 {
                b.insert(&mut b.end_iter(), "\n");
            }
            self.insert_marker(&mut b.end_iter(), l.kind, l.checked);
            for (text, tags) in &l.runs {
                let start = b.end_iter().offset();
                b.insert(&mut b.end_iter(), text);
                for t in tags {
                    b.apply_tag_by_name(t, &b.iter_at_offset(start), &b.end_iter());
                }
            }
        }
        for (i, l) in lines.iter().enumerate() {
            self.apply_line_style(i as i32, style_of(l.kind));
        }
        self.pending.set(if md.trim().is_empty() { Some((0, Some("title"))) } else { None });
        self.renumber();
        b.end_irreversible_action();
        let at = if self.pending.get().is_some() { b.start_iter() } else { b.end_iter() };
        b.place_cursor(&at);
        b.set_modified(false);
        self.typing.borrow_mut().clear();
        self.view.set_editable(editable);
        self.view.set_cursor_visible(editable);
        self.guard.set(self.guard.get() - 1);
        self.loading.set(false);
    }

    /// The note as markup (markup.rs).
    pub fn text(&self) -> String {
        let b = &self.buffer;
        let mut out = Vec::new();
        for line in 0..b.line_count() {
            let (kind, mlen, checked) = self.line_kind(line);
            let mut s = self.iter_at_line(line);
            s.forward_chars(mlen);
            let mut e = s;
            if !e.ends_line() {
                e.forward_to_line_end();
            }
            let mut runs = Vec::new();
            let mut it = s;
            while it < e {
                let mut nxt = it;
                nxt.forward_to_tag_toggle(None::<&gtk::TextTag>);
                if nxt > e {
                    nxt = e;
                }
                if nxt == it {
                    break;
                }
                let tags: Tags = it.tags().iter().filter_map(|t| t.name().and_then(|n| inline_name(&n))).collect();
                runs.push((b.text(&it, &nxt, false).to_string(), tags));
                it = nxt;
            }
            out.push(Line { kind, checked, runs });
        }
        markup::serialize(&out)
    }

    pub fn set_dates(&self, text: &str) {
        self.date.set_label(text);
    }

    // -- lines ----------------------------------------------------------------------------------
    /// (kind, marker length, checked) of a line.
    pub fn line_kind(&self, line: i32) -> (&'static str, i32, bool) {
        let it = self.iter_at_line(line);
        if let Some(anchor) = it.child_anchor() {
            for w in anchor.widgets() {
                if let Some(c) = w.downcast_ref::<gtk::CheckButton>() {
                    return ("check", 1, c.is_active());
                }
            }
        }
        if let Some(marker) = self.tag("marker") {
            if it.starts_tag(Some(&marker)) || it.has_tag(&marker) {
                let mut e = it;
                e.forward_to_tag_toggle(Some(&marker));
                let text = self.buffer.text(&it, &e, false);
                return (if text.starts_with('•') { "bullet" } else { "number" }, e.offset() - it.offset(), false);
            }
        }
        for t in it.tags() {
            match t.name().as_deref() {
                Some("title") => return ("title", 0, false),
                Some("heading") => return ("heading", 0, false),
                Some("subheading") => return ("subheading", 0, false),
                _ => {}
            }
        }
        ("body", 0, false)
    }

    fn line_bounds(&self, line: i32) -> (gtk::TextIter, gtk::TextIter) {
        let s = self.iter_at_line(line);
        let mut e = s;
        if !e.ends_line() {
            e.forward_to_line_end();
        }
        let mut e2 = e;
        e2.forward_char(); // with the newline (nothing at the end of the buffer)
        (s, e2)
    }

    fn apply_line_style(&self, line: i32, style: Option<&'static str>) {
        let (s, e2) = self.line_bounds(line);
        for name in PARA {
            self.buffer.remove_tag_by_name(name, &s, &e2);
        }
        if let Some(st) = style {
            self.buffer.apply_tag_by_name(st, &s, &e2);
        }
        if s == e2 {
            self.pending.set(style.map(|st| (line, Some(st))));
        } else if self.pending.get().is_some_and(|p| p.0 == line) {
            self.pending.set(None);
        }
    }

    fn insert_marker(&self, it: &mut gtk::TextIter, kind: &str, checked: bool) {
        let b = &self.buffer;
        match kind {
            "bullet" | "number" => {
                let start = it.offset();
                b.insert(it, if kind == "bullet" { "• " } else { "1. " });
                b.apply_tag_by_name("marker", &b.iter_at_offset(start), it);
            }
            "check" => {
                let anchor = b.create_child_anchor(it);
                let check = gtk::CheckButton::builder().active(checked).can_focus(false).sensitive(self.view.is_editable() || self.loading.get()).build();
                check.add_css_class("nt-check");
                let w = self.me.clone();
                check.connect_toggled(move |_| {
                    if let Some(e) = w.upgrade() {
                        if !e.loading.get() {
                            e.edited();
                        }
                    }
                });
                self.view.add_child_at_anchor(&check, &anchor);
            }
            _ => {}
        }
    }

    /// Give a line a paragraph style (its marker changes to match).
    pub fn set_kind(&self, line: i32, kind: &'static str, checked: bool) {
        self.guard.set(self.guard.get() + 1);
        let b = &self.buffer;
        let (old, mlen, _) = self.line_kind(line);
        if mlen > 0 {
            let mut s = self.iter_at_line(line);
            let mut e = s;
            e.forward_chars(mlen);
            b.delete(&mut s, &mut e);
        }
        self.insert_marker(&mut self.iter_at_line(line), kind, checked);
        self.apply_line_style(line, style_of(kind));
        self.guard.set(self.guard.get() - 1);
        if kind == "number" || old == "number" {
            self.renumber();
        }
        self.edited();
    }

    fn renumber(&self) {
        let b = &self.buffer;
        let mut n = 0;
        self.guard.set(self.guard.get() + 1);
        for line in 0..b.line_count() {
            let (kind, mlen, _) = self.line_kind(line);
            if kind != "number" {
                n = 0;
                continue;
            }
            n += 1;
            let mut s = self.iter_at_line(line);
            let mut e = s;
            e.forward_chars(mlen);
            let want = format!("{n}. ");
            if b.text(&s, &e, false) != want {
                b.delete(&mut s, &mut e);
                let mut at = self.iter_at_line(line);
                let start = at.offset();
                b.insert(&mut at, &want);
                b.apply_tag_by_name("marker", &b.iter_at_offset(start), &at);
                self.apply_line_style(line, Some("item"));
            }
        }
        self.guard.set(self.guard.get() - 1);
    }

    // -- typing ---------------------------------------------------------------------------------
    fn style_inserted(&self, start_off: i32, end_off: i32) {
        let b = &self.buffer;
        let first = b.iter_at_offset(start_off).line();
        let last = b.iter_at_offset(end_off).line();
        let style = self.style_near(start_off, end_off, first);
        self.guard.set(self.guard.get() + 1);
        for name in INLINE.iter().chain(&["marker"]) {
            b.remove_tag_by_name(name, &b.iter_at_offset(start_off), &b.iter_at_offset(end_off));
        }
        for name in self.typing.borrow().iter() {
            b.apply_tag_by_name(name, &b.iter_at_offset(start_off), &b.iter_at_offset(end_off));
        }
        self.apply_line_style(first, style);
        for line in first + 1..=last {
            // pasted lines: Body
            let item = self.line_kind(line).1 > 0;
            self.apply_line_style(line, if item { Some("item") } else { None });
        }
        self.guard.set(self.guard.get() - 1);
    }

    fn style_near(&self, start_off: i32, end_off: i32, line: i32) -> Option<&'static str> {
        let b = &self.buffer;
        let mut cands = Vec::new();
        let mut before = b.iter_at_offset(start_off);
        if before.backward_char() && before.line() == line {
            cands.push(before);
        }
        let after = b.iter_at_offset(end_off);
        if !after.is_end() {
            cands.push(after);
        }
        for it in cands {
            for t in it.tags() {
                if let Some(p) = t.name().and_then(|n| PARA.iter().copied().find(|x| *x == n.as_str())) {
                    return Some(p);
                }
            }
        }
        self.pending.get().filter(|p| p.0 == line).and_then(|p| p.1)
    }

    fn mark_set(&self, it: &gtk::TextIter) {
        if self.inserting.get() || self.guard.get() > 0 {
            return;
        }
        let marker = self.tag("marker");
        let mut prev = *it;
        let src = if prev.backward_char() && prev.line() == it.line() && !marker.as_ref().is_some_and(|m| prev.has_tag(m)) && prev.child_anchor().is_none() {
            prev
        } else if !it.ends_line() {
            *it
        } else {
            self.typing.borrow_mut().clear();
            return;
        };
        *self.typing.borrow_mut() = src.tags().iter().filter_map(|t| t.name().and_then(|n| inline_name(&n))).collect();
    }

    fn key(&self, keyval: Key, state: ModifierType) -> bool {
        if !self.view.is_editable() || state.intersects(ModifierType::CONTROL_MASK | ModifierType::SUPER_MASK | ModifierType::SHIFT_MASK) {
            return false;
        }
        let b = &self.buffer;
        if matches!(keyval, Key::Return | Key::KP_Enter) {
            return self.enter();
        }
        if keyval == Key::BackSpace && !b.has_selection() {
            let it = b.iter_at_mark(&b.get_insert());
            let (_kind, mlen, _) = self.line_kind(it.line());
            if mlen > 0 && it.line_offset() == mlen {
                self.set_kind(it.line(), "body", false);
                return true;
            }
        }
        false
    }

    fn enter(&self) -> bool {
        let b = &self.buffer;
        if b.has_selection() {
            b.delete_selection(true, true);
        }
        let it = b.iter_at_mark(&b.get_insert());
        let line = it.line();
        let (kind, mlen, _) = self.line_kind(line);
        if kind == "body" {
            return false;
        }
        let s = self.iter_at_line(line);
        let mut e = s;
        if !e.ends_line() {
            e.forward_to_line_end();
        }
        if LISTS.contains(&kind) && e.offset() - s.offset() <= mlen {
            self.set_kind(line, "body", false); // Return on an empty item ends the list
            return true;
        }
        self.guard.set(self.guard.get() + 1);
        b.insert_at_cursor("\n");
        self.apply_line_style(line, style_of(kind));
        self.guard.set(self.guard.get() - 1);
        self.set_kind(line + 1, if LISTS.contains(&kind) { kind } else { "body" }, false);
        let mut cur = self.iter_at_line(line + 1);
        cur.forward_chars(self.line_kind(line + 1).1);
        b.place_cursor(&cur);
        self.view.scroll_mark_onscreen(&b.get_insert());
        true
    }

    // -- formatting commands -------------------------------------------------------------------
    pub fn toggle_inline(&self, name: &'static str) {
        let b = &self.buffer;
        let Some((s, e)) = b.selection_bounds() else {
            let mut t = self.typing.borrow_mut();
            if !t.remove(name) {
                t.insert(name);
            }
            return;
        };
        let Some(tag) = self.tag(name) else { return };
        let mut nxt = s;
        nxt.forward_to_tag_toggle(Some(&tag));
        let whole = s.has_tag(&tag) && nxt >= e;
        if whole {
            b.remove_tag(&tag, &s, &e);
        } else {
            b.apply_tag(&tag, &s, &e);
        }
        self.edited();
    }

    pub fn selected_lines(&self) -> std::ops::RangeInclusive<i32> {
        let b = &self.buffer;
        match b.selection_bounds() {
            Some((s, e)) => s.line()..=e.line(),
            None => {
                let l = b.iter_at_mark(&b.get_insert()).line();
                l..=l
            }
        }
    }

    /// Title / Heading / Subheading / Body / a list for the selected lines (a
    /// list style already on every line turns them back to Body).
    pub fn set_style(&self, kind: &'static str) {
        if !self.view.is_editable() {
            return;
        }
        let lines = self.selected_lines();
        let kind = if LISTS.contains(&kind) && lines.clone().all(|n| self.line_kind(n).0 == kind) { "body" } else { kind };
        for n in lines.clone() {
            self.set_kind(n, kind, false);
        }
        if lines.start() == lines.end() {
            let mut it = self.iter_at_line(*lines.start());
            if !it.ends_line() {
                it.forward_to_line_end();
            }
            self.buffer.place_cursor(&it);
        }
        self.view.grab_focus();
    }

    /// Character styles at the cursor (the format panel shows them).
    pub fn active_styles(&self) -> Tags {
        match self.buffer.selection_bounds() {
            Some((s, _)) => s.tags().iter().filter_map(|t| t.name().and_then(|n| inline_name(&n))).collect(),
            None => self.typing.borrow().clone(),
        }
    }
}

/// "30 September 2026 at 14:05" (macOS Notes' editor header).
pub fn format_date(t: f64) -> String {
    glib::DateTime::from_unix_local(t as i64).ok().and_then(|d| d.format(&tr!("%-d %B %Y at %H:%M")).ok()).map(|s| s.to_string()).unwrap_or_default()
}
