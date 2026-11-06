//! Sonata in the system's language: Portuguese (Brazil), Portuguese
//! (Portugal), English otherwise -- the same rules and the **same
//! catalogs** as `sonata2/i18n.py` (`sonata2/locale/<lang>/*.json`), so a
//! string is translated once for both the Python and the Rust code.
//!
//! ```no_run
//! use sonata_i18n::{tr, trn, trp, fmt};
//! let title = tr!("Move to Trash");                    // "Mover para a Lixeira" / "Mover para o Lixo"
//! let copy = fmt(&tr!("Copy {name}"), &[("name", "a.txt")]);
//! let items = fmt(&trn!("{n} item", "{n} items", 3), &[("n", "3")]);
//! let reset = trp!("stopwatch", "Reset");              // a word that reads differently by place
//! ```
//!
//! Catalogs: `{"English": "translation"}`; a plural is `{"singular": ["one",
//! "other"]}` keyed by the singular; a context is `"context\x04text"`. A
//! string that isn't translated shows in English. Mark strings only with
//! `tr!` / `trn!` / `trp!` (and `n_!` / `nc_!` for strings translated later):
//! `tools/i18n_missing.py` finds them there. Placeholders are `{name}`,
//! filled with [`fmt`] -- never `format!` inside a translated string.

use serde_json::Value;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{OnceLock, RwLock};

/// The languages with a catalog.
pub const SUPPORTED: [&str; 2] = ["pt_BR", "pt_PT"];
/// gettext's separator between a context and its text.
pub const CONTEXT_SEP: char = '\u{4}';

/// One catalog entry: a translation, or a plural's two forms.
#[derive(Clone, Debug, PartialEq)]
pub enum Entry {
    One(String),
    Plural(Vec<String>),
}

/// The language Sonata shows for this environment: "pt_BR", "pt_PT" or
/// "en" (LC_ALL, LC_MESSAGES, LANG, LANGUAGE -- the first one set).
pub fn detect_from<F: Fn(&str) -> Option<String>>(get: F) -> &'static str {
    for var in ["LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"] {
        let value = get(var).unwrap_or_default();
        let value = value.trim();
        if value.is_empty() || value == "C" || value == "POSIX" {
            continue;
        }
        let first = value.split(':').next().unwrap_or("");          // LANGUAGE: "pt_BR:pt:en"
        let code = first.split('.').next().unwrap_or("").split('@').next().unwrap_or("");
        let (lang, region) = code.split_once('_').unwrap_or((code, ""));
        if lang == "pt" {
            // plain "pt": Brazil's, the most spoken
            return if region.eq_ignore_ascii_case("PT") { "pt_PT" } else { "pt_BR" };
        }
        return "en";
    }
    "en"
}

/// [`detect_from`] the process environment.
pub fn detect() -> &'static str {
    detect_from(|v| std::env::var(v).ok())
}

/// Where the catalogs are: `$SONATA2_LOCALE_DIR`; next to the installed
/// binary (`<prefix>/share/sonata2-rs/locale`); else this clone's
/// `locale` (development).
pub fn locale_dir() -> PathBuf {
    if let Some(d) = std::env::var_os("SONATA2_LOCALE_DIR").filter(|d| !d.is_empty()) {
        return PathBuf::from(d);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(prefix) = exe.parent().and_then(Path::parent) {
            let d = prefix.join("share").join("sonata2-rs").join("locale");
            if d.is_dir() {
                return d;
            }
        }
    }
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../locale")
}

/// Every `*.json` of `<dir>/<lang>/`, merged in name order (each area of
/// Sonata keeps its own file). Unreadable files and empty values are skipped.
pub fn load(dir: &Path, lang: &str) -> HashMap<String, Entry> {
    let mut out = HashMap::new();
    let folder = dir.join(lang);
    let Ok(rd) = std::fs::read_dir(&folder) else { return out };
    let mut names: Vec<PathBuf> =
        rd.filter_map(|e| e.ok().map(|e| e.path())).filter(|p| p.extension().is_some_and(|x| x == "json")).collect();
    names.sort();
    for p in names {
        let Some(Value::Object(part)) = std::fs::read(&p).ok().and_then(|b| serde_json::from_slice(&b).ok()) else {
            continue;
        };
        for (k, v) in part {
            match v {
                Value::String(s) if !s.is_empty() => {
                    out.insert(k, Entry::One(s));
                }
                Value::Array(a) if !a.is_empty() => {
                    let forms = a.into_iter().map(|x| x.as_str().unwrap_or("").to_string()).collect();
                    out.insert(k, Entry::Plural(forms));
                }
                _ => {}
            }
        }
    }
    out
}

/// A language and its catalog: what every lookup reads.
pub struct Catalog {
    pub lang: String,
    entries: HashMap<String, Entry>,
}

impl Catalog {
    pub fn new(lang: &str, entries: HashMap<String, Entry>) -> Self {
        Catalog { lang: lang.to_string(), entries }
    }

    /// `lang`'s catalog from `dir` (English: empty).
    pub fn load(dir: &Path, lang: &str) -> Self {
        let entries = if SUPPORTED.contains(&lang) { load(dir, lang) } else { HashMap::new() };
        Catalog::new(lang, entries)
    }

    pub fn get(&self, key: &str) -> Option<&Entry> {
        self.entries.get(key)
    }

    /// `_()`: the translation, or `text` itself.
    pub fn gettext(&self, text: &str) -> String {
        if text.is_empty() {
            return String::new();
        }
        match self.entries.get(text) {
            Some(Entry::One(s)) => s.clone(),
            // a plural's singular asked alone
            Some(Entry::Plural(f)) if f.first().is_some_and(|s| !s.is_empty()) => f[0].clone(),
            _ => text.to_string(),
        }
    }

    /// `ngettext()`: Brazil says "0 item", Portugal "0 itens".
    pub fn ngettext(&self, singular: &str, plural: &str, n: i64) -> String {
        if let Some(Entry::Plural(f)) = self.entries.get(singular) {
            if f.len() >= 2 {
                let one = n == 1 || (self.lang == "pt_BR" && n == 0);
                return if one { f[0].clone() } else { f[1].clone() };
            }
        }
        (if n == 1 { singular } else { plural }).to_string()
    }

    /// `pgettext()`: "Reset" is "Zerar" on a stopwatch, "Redefinir" in Settings.
    pub fn pgettext(&self, context: &str, text: &str) -> String {
        let key = format!("{context}{CONTEXT_SEP}{text}");
        match self.entries.get(&key) {
            Some(Entry::One(s)) => s.clone(),
            _ => self.gettext(text),
        }
    }
}

static CURRENT: OnceLock<RwLock<Option<std::sync::Arc<Catalog>>>> = OnceLock::new();

fn slot() -> &'static RwLock<Option<std::sync::Arc<Catalog>>> {
    CURRENT.get_or_init(|| RwLock::new(None))
}

/// This process's catalog (the session's language, read once: a new one
/// shows at the next login).
pub fn catalog() -> std::sync::Arc<Catalog> {
    if let Some(c) = slot().read().unwrap_or_else(|e| e.into_inner()).as_ref() {
        return c.clone();
    }
    let c = std::sync::Arc::new(Catalog::load(&locale_dir(), detect()));
    *slot().write().unwrap_or_else(|e| e.into_inner()) = Some(c.clone());
    c
}

/// Switch the language of this process (tests; previews).
pub fn use_language(lang: &str) {
    let c = std::sync::Arc::new(Catalog::load(&locale_dir(), lang));
    *slot().write().unwrap_or_else(|e| e.into_inner()) = Some(c);
}

/// The language shown: "pt_BR", "pt_PT" or "en".
pub fn language() -> String {
    catalog().lang.clone()
}

pub fn gettext(text: &str) -> String {
    catalog().gettext(text)
}

pub fn ngettext(singular: &str, plural: &str, n: i64) -> String {
    catalog().ngettext(singular, plural, n)
}

pub fn pgettext(context: &str, text: &str) -> String {
    catalog().pgettext(context, text)
}

/// Fill `{name}` placeholders (str.format with keywords). `{{` and `}}` are
/// literal braces; an unknown name stays as written.
pub fn fmt(template: &str, args: &[(&str, &str)]) -> String {
    let mut out = String::with_capacity(template.len());
    let mut rest = template;
    while let Some(i) = rest.find(['{', '}']) {
        out.push_str(&rest[..i]);
        let tail = &rest[i..];
        if tail.starts_with("{{") || tail.starts_with("}}") {
            out.push_str(&tail[..1]);
            rest = &tail[2..];
            continue;
        }
        if tail.starts_with('{') {
            if let Some(end) = tail.find('}') {
                let name = &tail[1..end];
                match args.iter().find(|(k, _)| *k == name) {
                    Some((_, v)) => out.push_str(v),
                    None => out.push_str(&tail[..=end]),
                }
                rest = &tail[end + 1..];
                continue;
            }
        }
        out.push_str(&tail[..1]);
        rest = &tail[1..];
    }
    out.push_str(rest);
    out
}

/// `_("...")`: a string the user sees, translated.
#[macro_export]
macro_rules! tr {
    ($text:literal) => {
        $crate::gettext($text)
    };
}

/// `ngettext(singular, plural, n)`.
#[macro_export]
macro_rules! trn {
    ($singular:literal, $plural:literal, $n:expr) => {
        $crate::ngettext($singular, $plural, ($n) as i64)
    };
}

/// `pgettext(context, text)`.
#[macro_export]
macro_rules! trp {
    ($context:literal, $text:literal) => {
        $crate::pgettext($context, $text)
    };
}

/// `N_("...")`: marked for translation, translated later where shown.
#[macro_export]
macro_rules! n_ {
    ($text:literal) => {
        $text
    };
}

/// `NC_(context, "...")`: `n_!` for a `trp!` pair.
#[macro_export]
macro_rules! nc_ {
    ($context:literal, $text:literal) => {
        $text
    };
}

#[cfg(test)]
mod tests;
