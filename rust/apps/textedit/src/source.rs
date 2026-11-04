//! TextEdit's text views (sonata2/textedit/source.py): GtkSourceView 5
//! (syntax highlighting, line numbers, current line, bracket matching) with
//! a Sonata style scheme built from the tokens. Syntax colours are Xcode's
//! default ("Default (Light)" / "Default (Dark)"), as in Python.
//!
//! The Rust build always links GtkSourceView: the plain-TextView fallback
//! of the Python code (its own line-number gutter) isn't ported.

use gtk::{gio, glib, prelude::*};
use sourceview5::prelude::*;
use std::cell::RefCell;
use std::path::PathBuf;

/// Xcode's default source colours: (light, dark)
const SYNTAX: &[(&str, &str, &str)] = &[
    ("keyword", "#9b2393", "#fc5fa3"),
    ("string", "#c41a16", "#fc6a5d"),
    ("comment", "#5d6c79", "#7f8c98"),
    ("number", "#1c00cf", "#d0bf69"),
    ("type", "#0b4f79", "#5dd8ff"),
    ("function", "#326d74", "#67b7a4"),
    ("preproc", "#643820", "#fd8f3f"),
    ("builtin", "#6c36a9", "#d0a8ff"),
    ("attribute", "#815f03", "#bf8555"),
    ("heading", "#0b4f79", "#5dd8ff"),
    ("link", "#0e0eff", "#6699ff"),
    ("added", "#1e7a1e", "#7ed97e"),
    ("removed", "#c41a16", "#fc6a5d"),
];

/// "#rrggbbaa" of a token (or a CSS colour), its alpha times `alpha`.
fn hex(token: &str, alpha: f32) -> String {
    let vals = sonata_ui::values();
    let css = vals.get(token).map(String::as_str).unwrap_or(token);
    let c = gtk::gdk::RGBA::parse(css).unwrap_or(gtk::gdk::RGBA::BLACK);
    let ch = |v: f32| (v * 255.0).round() as u8;
    format!("#{:02x}{:02x}{:02x}{:02x}", ch(c.red()), ch(c.green()), ch(c.blue()), ch(c.alpha() * alpha))
}

pub fn scheme_xml(sid: &str, dark: bool) -> String {
    let s = |k: &str| SYNTAX.iter().find(|x| x.0 == k).map(|x| if dark { x.2 } else { x.1 }).unwrap_or("#000000").to_string();
    let a = |pairs: &[(&str, String)]| pairs.iter().map(|(k, v)| format!("{k}=\"{v}\"")).collect::<Vec<_>>().join(" ");
    let fg = |c: String| vec![("foreground", c)];
    let fg_bold = |c: String| vec![("foreground", c), ("bold", "true".to_string())];
    let styles: Vec<(&str, Vec<(&str, String)>)> = vec![
        ("text", vec![("foreground", hex("label", 1.0)), ("background", hex("content_bg", 1.0))]),
        ("selection", vec![("background", hex("accent", 0.30))]),
        ("selection-unfocused", vec![("background", hex("label", 0.12))]),
        ("cursor", vec![("foreground", hex("accent", 1.0))]),
        ("current-line", vec![("background", hex("accent", 0.06))]),
        ("current-line-number", vec![("foreground", hex("label_secondary", 1.0)), ("background", hex("content_bg", 1.0))]),
        ("line-numbers", vec![("foreground", hex("label_tertiary", 1.0)), ("background", hex("content_bg", 1.0))]),
        ("bracket-match", vec![("background", hex("accent", 0.22)), ("bold", "true".into())]),
        ("bracket-mismatch", vec![("background", hex("destructive", 0.25))]),
        ("search-match", vec![("background", hex("accent", 0.30))]),
        ("draw-spaces", vec![("foreground", hex("label_tertiary", 1.0))]),
        ("def:comment", vec![("foreground", s("comment")), ("italic", "false".into())]),
        ("def:shebang", fg(s("comment"))),
        ("def:doc-comment-element", fg_bold(s("comment"))),
        ("def:string", fg(s("string"))),
        ("def:character", fg(s("string"))),
        ("def:special-char", fg(s("number"))),
        ("def:keyword", fg_bold(s("keyword"))),
        ("def:statement", fg_bold(s("keyword"))),
        ("def:number", fg(s("number"))),
        ("def:floating-point", fg(s("number"))),
        ("def:decimal", fg(s("number"))),
        ("def:base-n-integer", fg(s("number"))),
        ("def:boolean", fg_bold(s("keyword"))),
        ("def:constant", fg(s("number"))),
        ("def:special-constant", fg_bold(s("keyword"))),
        ("def:type", fg(s("type"))),
        ("def:function", fg(s("function"))),
        ("def:builtin", fg(s("builtin"))),
        ("def:preprocessor", fg(s("preproc"))),
        ("def:note", fg_bold(s("comment"))),
        ("def:error", vec![("underline", "error".into())]),
        ("def:warning", vec![("underline", "error".into())]),
        ("def:net-address", vec![("foreground", s("link")), ("underline", "single".into())]),
        ("def:link-destination", fg(s("link"))),
        ("def:heading", fg_bold(s("heading"))),
        ("def:emphasis", vec![("italic", "true".into())]),
        ("def:strong-emphasis", vec![("bold", "true".into())]),
        ("def:inline-code", fg(s("string"))),
        ("def:insertion", fg(s("added"))),
        ("def:deletion", fg(s("removed"))),
        ("xml:attribute-name", fg(s("attribute"))),
        ("xml:element-name", fg(s("keyword"))),
        ("css:property-name", fg(s("attribute"))),
        ("diff:added-line", fg(s("added"))),
        ("diff:removed-line", fg(s("removed"))),
        ("diff:location", fg(s("type"))),
        ("markdown:header", fg_bold(s("heading"))),
    ];
    let rows: Vec<String> = styles.iter().filter(|(_, v)| !v.is_empty()).map(|(n, v)| format!("  <style name=\"{n}\" {}/>", a(v))).collect();
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<style-scheme id=\"{sid}\" name=\"Sonata\" version=\"1.0\">\n  <description>Sonata (generated from the design tokens)</description>\n{}\n</style-scheme>\n",
        rows.join("\n")
    )
}

thread_local! {
    static SCHEMES: RefCell<(Option<PathBuf>, Vec<glib::WeakRef<sourceview5::Buffer>>)> = RefCell::default();
}

fn scheme_dir() -> PathBuf {
    crate::session::data_dir().join("styles")
}

fn write_schemes(dir: &PathBuf) {
    let _ = std::fs::create_dir_all(dir);
    let dark = sonata_ui::is_dark();
    // the current palette is one appearance: write that one now, the other only when missing
    let (sid, other) = if dark { ("sonata-dark", "sonata-light") } else { ("sonata-light", "sonata-dark") };
    let _ = std::fs::write(dir.join(format!("{sid}.xml")), scheme_xml(sid, dark));
    let o = dir.join(format!("{other}.xml"));
    if !o.exists() {
        let _ = std::fs::write(o, scheme_xml(other, !dark));
    }
}

fn current_id() -> &'static str {
    if sonata_ui::is_dark() { "sonata-dark" } else { "sonata-light" }
}

/// The Sonata scheme for the current appearance (written on first use and
/// whenever light/dark or the accent changes).
pub fn scheme() -> Option<sourceview5::StyleScheme> {
    let mgr = sourceview5::StyleSchemeManager::default();
    let first = SCHEMES.with(|s| s.borrow().0.is_none());
    if first {
        let dir = scheme_dir();
        write_schemes(&dir);
        mgr.append_search_path(&dir.to_string_lossy());
        SCHEMES.with(|s| s.borrow_mut().0 = Some(dir));
        sonata_ui::on_change(restyle);
    }
    mgr.scheme(current_id())
}

fn restyle() {
    let Some(dir) = SCHEMES.with(|s| s.borrow().0.clone()) else { return };
    write_schemes(&dir);
    let mgr = sourceview5::StyleSchemeManager::default();
    mgr.force_rescan();
    let sch = mgr.scheme(current_id());
    SCHEMES.with(|s| {
        let mut s = s.borrow_mut();
        s.1.retain(|w| match w.upgrade() {
            Some(buf) => {
                buf.set_style_scheme(sch.as_ref());
                true
            }
            None => false,
        });
    });
}

/// (view, buffer).
pub fn make_view() -> (sourceview5::View, sourceview5::Buffer) {
    let buf = sourceview5::Buffer::new(None::<&gtk::TextTagTable>);
    buf.set_style_scheme(scheme().as_ref());
    buf.set_highlight_matching_brackets(true);
    SCHEMES.with(|s| s.borrow_mut().1.push(buf.downgrade()));
    let view = sourceview5::View::with_buffer(&buf);
    view.set_tab_width(4);
    view.set_auto_indent(true);
    view.set_smart_home_end(sourceview5::SmartHomeEndType::Before);
    (view, buf)
}

/// Set the buffer's language from the file name / content; its display name
/// ("Python") or None (plain text).
pub fn guess_language(buffer: &sourceview5::Buffer, name: &str, data: &[u8]) -> Option<String> {
    let ctype = if !name.is_empty() || !data.is_empty() {
        let (t, _uncertain) = gio::content_type_guess(if name.is_empty() { None } else { Some(name) }, &data[..data.len().min(4096)]);
        Some(t)
    } else {
        None
    };
    let lang = sourceview5::LanguageManager::default().guess_language(if name.is_empty() { None } else { Some(name) }, ctype.as_deref());
    buffer.set_language(lang.as_ref());
    buffer.set_highlight_syntax(lang.is_some());
    lang.map(|l| l.name().to_string())
}

pub fn show_line_numbers(view: &sourceview5::View, show: bool) {
    view.set_show_line_numbers(show);
    view.set_highlight_current_line(show);
}

