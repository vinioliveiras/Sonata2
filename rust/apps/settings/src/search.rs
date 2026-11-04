//! Settings' search reaches every setting, not only the sections (Vini):
//! any text a section shows -- a group's title, a row's title or subtitle,
//! a switch's label, a button -- finds it, and picking a result opens that
//! section scrolled to that setting, which lights up for a moment.
//!
//! The index is read from the pages' source at build time (build.rs: the
//! strings each `page_<part>` and the functions it uses put through tr! and
//! friends), not by building every section: building one starts things (a
//! Bluetooth search, password prompts, reads of the system) that searching
//! mustn't. Its texts are translated as they're searched, and the English
//! ones match too.
//!
//! ```ignore
//! let hits = search::find("night shift", section_of);   // best first
//! search::reveal(&page, &hit.text);                     // in the built section
//! ```

use adw::prelude::*;
use gtk::glib;
use std::collections::HashSet;

include!(concat!(env!("OUT_DIR"), "/search_index.rs"));

pub const MAX_HITS: usize = 40;
pub const FLASH_MS: u64 = 1600;

#[derive(Clone, Debug, PartialEq)]
pub struct Hit {
    /// the section to open
    pub sid: String,
    /// the part of it the text is in
    pub part: String,
    /// as shown (translated)
    pub text: String,
}

/// `{name}` / `%s` / `%(name)d` placeholders, replaced by `with`.
fn placeholders(text: &str, with: &str) -> String {
    let b: Vec<char> = text.chars().collect();
    let mut out = String::new();
    let mut i = 0;
    while i < b.len() {
        if b[i] == '{' {
            if let Some(j) = b[i..].iter().position(|c| *c == '}') {
                out.push_str(with);
                i += j + 1;
                continue;
            }
        }
        if b[i] == '%' {
            // %s, %d, %(name)s, %(name)d, %()s
            let mut j = i + 1;
            if b.get(j) == Some(&'(') {
                j += 1;
                while j < b.len() && (b[j].is_alphanumeric() || b[j] == '_') {
                    j += 1;
                }
                if b.get(j) == Some(&')') {
                    j += 1;
                } else {
                    j = i + 1;
                }
            } else {
                while j < b.len() && (b[j].is_alphanumeric() || b[j] == '_') && !matches!(b[j], 's' | 'd') {
                    j += 1;
                }
            }
            if matches!(b.get(j), Some('s') | Some('d')) {
                out.push_str(with);
                i = j + 1;
                continue;
            }
        }
        out.push(b[i]);
        i += 1;
    }
    out
}

/// As the section shows it (looked up as trp! would, not marked again here).
fn shown(msgid: &str, ctx: Option<&str>, tr: &dyn Fn(&str) -> String) -> String {
    if let Some(c) = ctx {
        let got = sonata_i18n::pgettext(c, msgid);
        if !got.is_empty() && got != msgid {
            return got;
        }
    }
    tr(msgid)
}

/// Real words a person reads (not a lone placeholder, symbol or letter).
pub fn searchable(text: &str) -> bool {
    let bare = placeholders(text, "");
    let bare = bare.trim_matches(|c: char| " …:.,-–—()".contains(c));
    bare.chars().count() >= 3 && bare.chars().any(char::is_alphabetic)
}

/// Every text that has all of the query's words, best first: a text that
/// starts with the query, then one with a word starting with it, then the
/// rest; shorter texts first (a setting's name over a sentence about it).
pub fn find(query: &str, section_of: impl Fn(&str) -> String) -> Vec<Hit> {
    find_with(query, section_of, MAX_HITS, &sonata_i18n::gettext)
}

/// find, with its translation given (tests).
pub fn find_with(query: &str, section_of: impl Fn(&str) -> String, limit: usize, tr: &dyn Fn(&str) -> String) -> Vec<Hit> {
    let q = query.to_lowercase();
    let words: Vec<&str> = q.split_whitespace().collect();
    if words.is_empty() {
        return Vec::new();
    }
    let joined = words.join(" ");
    let (mut hits, mut seen) = (Vec::new(), HashSet::new());
    for (part, strings) in INDEX {
        let sid = section_of(part);
        for (msgid, ctx) in strings.iter() {
            let text = shown(msgid, *ctx, tr);
            if !searchable(&text) {
                continue;
            }
            let hay = format!("{text} {msgid}").to_lowercase();
            if !words.iter().all(|w| hay.contains(w)) {
                continue;
            }
            if !seen.insert((sid.clone(), text.clone())) {
                continue;
            }
            let low = text.to_lowercase();
            let rank = if low.starts_with(&joined) {
                0
            } else if word_starts(&low, words[0]) {
                1
            } else {
                2
            };
            hits.push((rank, text.chars().count(), Hit { sid: sid.clone(), part: part.to_string(), text: placeholders(&text, "…").trim().to_string() }));
        }
    }
    hits.sort_by_key(|h| (h.0, h.1));
    hits.into_iter().take(limit).map(|h| h.2).collect()
}

/// A word of `low` starts with `w` (Python's `\b` + w).
fn word_starts(low: &str, w: &str) -> bool {
    let word = |c: char| c.is_alphanumeric() || c == '_';
    low.match_indices(w).any(|(i, _)| {
        let before = low[..i].chars().next_back();
        let first = w.chars().next().is_some_and(word);
        // \b: a change between a word character and not one
        before.map_or(first, |b| word(b) != first)
    })
}

/// The index, by part: {part: [(msgid, context)]}.
pub fn index() -> &'static [(&'static str, &'static [(&'static str, Option<&'static str>)])] {
    INDEX
}

// -- in the built section -----------------------------------------------------------------------
/// What `w` shows as text.
fn texts(w: &gtk::Widget) -> Vec<String> {
    let mut out = Vec::new();
    if let Some(g) = w.downcast_ref::<adw::PreferencesGroup>() {
        out.push(g.title().to_string());
        out.push(g.description().map(|d| d.to_string()).unwrap_or_default());
    }
    if let Some(r) = w.downcast_ref::<adw::PreferencesRow>() {
        out.push(r.title().to_string());
    }
    if let Some(r) = w.downcast_ref::<adw::ActionRow>() {
        out.push(r.subtitle().map(|d| d.to_string()).unwrap_or_default());
    }
    if let Some(r) = w.downcast_ref::<adw::ExpanderRow>() {
        out.push(r.subtitle().to_string());
    }
    if let Some(l) = w.downcast_ref::<gtk::Label>() {
        out.push(l.text().to_string());
    }
    if !w.is::<gtk::MenuButton>() {
        if let Some(b) = w.downcast_ref::<gtk::Button>() {
            out.push(b.label().map(|d| d.to_string()).unwrap_or_default());
        }
        if let Some(b) = w.downcast_ref::<gtk::CheckButton>() {
            out.push(b.label().map(|d| d.to_string()).unwrap_or_default());
        }
    }
    out
}

/// The same text (a text with a value in it: its fixed parts).
pub fn same(a: &str, b: &str) -> bool {
    let a = placeholders(a, "…").trim().to_lowercase();
    let b = placeholders(b, "…").trim().to_lowercase();
    if a.is_empty() || b.is_empty() {
        return false;
    }
    if b.contains('…') {
        return b.split('…').map(str::trim).filter(|p| !p.is_empty()).all(|p| a.contains(p));
    }
    a == b
}

/// The widget in `root` showing `text` (the first one, in reading order).
pub fn locate(root: &gtk::Widget, text: &str) -> Option<gtk::Widget> {
    let mut stack = vec![root.clone()];
    while !stack.is_empty() {
        let w = stack.remove(0);
        if texts(&w).iter().any(|t| same(t, text)) {
            return Some(w);
        }
        let mut kids = Vec::new();
        let mut c = w.first_child();
        while let Some(ch) = c {
            c = ch.next_sibling();
            kids.push(ch);
        }
        stack.splice(0..0, kids); // depth first, in order
    }
    None
}

/// What lights up for a found text: its row, or its group.
pub fn target_of(w: &gtk::Widget) -> gtk::Widget {
    for ty in [adw::PreferencesRow::static_type(), gtk::ListBoxRow::static_type(), adw::PreferencesGroup::static_type()] {
        if w.type_().is_a(ty) {
            return w.clone();
        }
        if let Some(a) = w.ancestor(ty) {
            return a;
        }
    }
    w.clone()
}

/// Scroll `page` (a built section) to the setting showing `text` and light
/// it up for a moment. The widget lit up, or None (not on this page: the
/// section opened is all there is).
pub fn reveal(page: &gtk::Widget, text: &str) -> Option<gtk::Widget> {
    let found = locate(page, text)?;
    let target = target_of(&found);
    if let Some(scroller) = target.ancestor(gtk::ScrolledWindow::static_type()).and_downcast::<gtk::ScrolledWindow>() {
        if let Some(child) = scroller.child() {
            if let Some(pt) = target.compute_point(&child, &gtk::graphene::Point::new(0.0, 0.0)) {
                let adj = scroller.vadjustment();
                let want = pt.y() as f64 - ((adj.page_size() - target.height() as f64) / 3.0).max(0.0);
                adj.set_value(want.min(adj.upper() - adj.page_size()).max(adj.lower()));
            }
        }
    }
    target.add_css_class("st-found");
    let t = target.clone();
    glib::timeout_add_local_once(std::time::Duration::from_millis(FLASH_MS), move || t.remove_css_class("st-found"));
    Some(target)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sections::section_of;

    fn sec(p: &str) -> String {
        section_of(p).to_string()
    }

    #[test]
    fn test_no_noise() {
        assert!(find("", sec).is_empty());
        assert!(find("a", sec).iter().all(|h| h.text.trim_matches(|c| c == ' ' || c == '…').chars().count() >= 3));
        assert!(!searchable("{name}"));
        assert!(!searchable("…"));
        assert!(!searchable("%s:"));
        assert!(searchable("Accent colour"));
    }

    #[test]
    fn placeholders_go() {
        assert_eq!(placeholders("{size} data", "…"), "… data");
        assert_eq!(placeholders("%(n)d apps, %s more", "…"), "… apps, … more");
        assert_eq!(placeholders("100% sure", "…"), "100% sure");
    }

    #[test]
    fn same_text() {
        assert!(same("Night Shift", "night shift"));
        assert!(same("5 MB data", "{size} data"));
        assert!(!same("", "x"));
        assert!(!same("Night", "Night Shift"));
    }

    #[test]
    fn ranking() {
        assert!(word_starts("turn off night shift", "night"));
        assert!(!word_starts("fortnight", "night"));
    }

    /// Vini: Settings' search only found sections (by their title and a few
    /// keywords). Any text in Settings finds it now.
    #[test]
    fn test_search_only_found_sections() {
        // (the strings of the pages ported so far: every part's own come with them)
        let parts: Vec<&str> = index().iter().map(|(p, _)| *p).collect();
        for part in ["shortcuts", "appicons"] {
            assert!(parts.contains(&part), "{part}");
        }
        let hits = find_with("restore defaults", sec, MAX_HITS, &|t| t.to_string());
        assert_eq!(hits.first().map(|h| (h.part.as_str(), h.text.as_str())), Some(("shortcuts", "Restore Defaults")));
        assert_eq!(hits[0].sid, "keyboard"); // a merged part opens its section
    }

    /// The texts are searched as shown (in pt-BR / pt-PT too), and English still matches.
    #[test]
    fn test_found_in_portuguese() {
        let tr = |t: &str| if t == "Restore Defaults" { "Restaurar Predefinições".to_string() } else { t.to_string() };
        let texts: Vec<String> = find_with("restaurar", sec, MAX_HITS, &tr).into_iter().map(|h| h.text).collect();
        assert!(texts.contains(&"Restaurar Predefinições".to_string()));
        let texts: Vec<String> = find_with("restore defaults", sec, MAX_HITS, &tr).into_iter().map(|h| h.text).collect();
        assert!(texts.contains(&"Restaurar Predefinições".to_string()));
    }
}
