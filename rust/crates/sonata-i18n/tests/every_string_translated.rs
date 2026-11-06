//! Sonata in Portuguese (Brazil / Portugal) when the system is (Vini).
//! Every string the Rust code marks for translation -- `tr!`, `trn!`,
//! `trp!`, `n_!`, `nc_!` -- has both translations, with the same
//! `{placeholders}`; every macro gets literal strings on one line (anything
//! else could never be found in a catalog); the catalogs are valid and
//! agree with themselves. (What tests/test_i18n.py checked while there was
//! a Python Sonata; tools/i18n_missing.py lists the gaps of one area.)
//!
//! `cargo test -p sonata-i18n --test every_string_translated`

use regex::Regex;
use serde_json::Value;
use sonata_i18n::{Catalog, Entry, CONTEXT_SEP, SUPPORTED};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

fn rust_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn locale_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../locale")
}

/// A Rust string literal's text ("\n", "\u{2026}", "\"" ...).
fn unescape(text: &str) -> String {
    let mut out = String::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match chars.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('r') => out.push('\r'),
            Some('0') => out.push('\0'),
            Some('u') if chars.peek() == Some(&'{') => {
                chars.next();
                let hex: String = chars.by_ref().take_while(|c| *c != '}').collect();
                out.extend(u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32));
            }
            Some(other) => out.push(other),
            None => {}
        }
    }
    out
}

/// (singular or context\x04text, plural) -> where it is marked.
type Marked = BTreeMap<(String, Option<String>), Vec<String>>;

/// Every marked string under rust/apps and rust/crates, and the lines
/// whose macros don't take literal strings.
fn marked() -> (Marked, Vec<String>) {
    const S: &str = r#""((?:[^"\\]|\\.)*)""#;
    let one = Regex::new(&format!(r"\b(?:tr|n_)!\(\s*{S}\s*\)")).unwrap();
    let plural = Regex::new(&format!(r"\btrn!\(\s*{S}\s*,\s*{S}")).unwrap();
    let context = Regex::new(&format!(r"\b(?:trp|nc_)!\(\s*{S}\s*,\s*{S}\s*\)")).unwrap();
    let any = Regex::new(r"\b(tr|trn|trp|n_|nc_)!\(").unwrap();
    fn walk(dir: &Path, out: &mut Vec<PathBuf>) {
        for e in std::fs::read_dir(dir).into_iter().flatten().flatten() {
            let p = e.path();
            if p.is_dir() {
                if p.file_name().is_some_and(|n| n != "target") {
                    walk(&p, out);
                }
            } else if p.extension().is_some_and(|x| x == "rs") {
                out.push(p);
            }
        }
    }
    let mut files = Vec::new();
    for d in ["apps", "crates"] {
        walk(&rust_root().join(d), &mut files);
    }
    files.sort();
    let (mut found, mut bad) = (Marked::new(), Vec::new());
    for f in files {
        let src = std::fs::read_to_string(&f).unwrap_or_default();
        let rel = f.strip_prefix(rust_root()).unwrap_or(&f).display().to_string();
        for (no, line) in src.lines().enumerate() {
            let code = if line.trim_start().starts_with("//") { "" } else { line.split("//").next().unwrap_or("") };
            if code.contains("macro_rules!") || !any.is_match(code) {
                continue;
            }
            let at = format!("{rel}:{}", no + 1);
            let calls = any.find_iter(code).count();
            let mut got = 0;
            for m in one.captures_iter(code) {
                got += 1;
                found.entry((unescape(&m[1]), None)).or_default().push(at.clone());
            }
            for m in plural.captures_iter(code) {
                got += 1;
                found.entry((unescape(&m[1]), Some(unescape(&m[2])))).or_default().push(at.clone());
            }
            for m in context.captures_iter(code) {
                got += 1;
                found.entry((format!("{}{CONTEXT_SEP}{}", unescape(&m[1]), unescape(&m[2])), None)).or_default().push(at.clone());
            }
            if got < calls {
                bad.push(format!("{at}: a translation macro needs literal strings on one line"));
            }
        }
    }
    (found, bad)
}

/// The `{names}` of a text, sorted ("{{" is a brace); None when the braces
/// don't pair (Python's string.Formatter, which the catalogs were written for).
fn fields(text: &str) -> Option<Vec<String>> {
    let mut out = Vec::new();
    let mut chars = text.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '{' if chars.peek() == Some(&'{') => {
                chars.next();
            }
            '{' => {
                let mut name = String::new();
                loop {
                    match chars.next() {
                        Some('}') => break,
                        Some(ch) => name.push(ch),
                        None => return None,
                    }
                }
                let name = name.split([':', '!']).next().unwrap_or("").to_string();
                if !out.contains(&name) {
                    out.push(name);
                }
            }
            '}' if chars.peek() == Some(&'}') => {
                chars.next();
            }
            '}' => return None,
            _ => {}
        }
    }
    out.sort();
    Some(out)
}

#[test]
fn every_marked_string_translated_in_both_languages() {
    let (found, bad) = marked();
    assert!(found.len() > 1000, "the marked strings ({}) weren't found under {}", found.len(), rust_root().display());
    assert!(bad.is_empty(), "{}", bad.join("\n"));
    let mut problems = Vec::new();
    for lang in SUPPORTED {
        let cat = Catalog::load(&locale_dir(), lang);
        for ((s, p), at) in &found {
            let at = &at[0];
            match (cat.get(s), p) {
                (None, _) => problems.push(format!("MISSING {lang} {at}: {s:?}")),
                (Some(Entry::Plural(forms)), Some(p)) => {
                    if forms.len() != 2 {
                        problems.push(format!("PLURAL {lang} {at}: {s:?} needs [one, other]"));
                    } else {
                        for (tr, en) in [(&forms[0], s), (&forms[1], p)] {
                            if fields(tr) != fields(en) {
                                problems.push(format!("PLACEHOLDERS {lang} {at}: {en:?} -> {tr:?}"));
                            }
                        }
                    }
                }
                (Some(Entry::One(_)), Some(_)) => problems.push(format!("PLURAL {lang} {at}: {s:?} needs [one, other]")),
                (Some(Entry::Plural(_)), None) => problems.push(format!("NOT-A-STRING {lang} {at}: {s:?}")),
                (Some(Entry::One(t)), None) => {
                    let en = s.rsplit(CONTEXT_SEP).next().unwrap_or(s);
                    if fields(t) != fields(en) || t.matches('%').count() != en.matches('%').count() {
                        problems.push(format!("PLACEHOLDERS {lang} {at}: {en:?} -> {t:?}"));
                    }
                }
            }
        }
    }
    assert!(problems.is_empty(), "{} problems (tools/i18n_missing.py <folder> lists one area's):\n{}", problems.len(), problems.join("\n"));
}

#[test]
fn catalogs_are_json_objects_and_agree_with_themselves() {
    for lang in SUPPORTED {
        let folder = locale_dir().join(lang);
        let mut names: Vec<PathBuf> = std::fs::read_dir(&folder).unwrap().flatten().map(|e| e.path()).collect();
        names.sort();
        assert!(names.len() >= 5, "{}", folder.display());
        let mut seen: BTreeMap<String, (String, Value)> = BTreeMap::new();
        let mut conflicts = Vec::new();
        for p in names {
            let name = p.file_name().unwrap().to_string_lossy().into_owned();
            let v: Value = serde_json::from_slice(&std::fs::read(&p).unwrap()).unwrap_or_else(|e| panic!("{}: {e}", p.display()));
            let Value::Object(map) = v else { panic!("{}: not an object", p.display()) };
            // every catalog of a language agrees on a string (they're
            // merged: a second translation would win at random)
            for (k, v) in map {
                match seen.get(&k) {
                    Some((first, old)) if *old != v => conflicts.push(format!("{lang}: {k:?} in {first} and {name}")),
                    Some(_) => {}
                    None => {
                        seen.insert(k, (name.clone(), v));
                    }
                }
            }
        }
        assert!(conflicts.is_empty(), "{}", conflicts.join("\n"));
    }
}

#[test]
fn fields_are_pythons() {
    assert_eq!(fields("Copy {name} to {where}"), Some(vec!["name".into(), "where".into()]));
    assert_eq!(fields("{n:.1f} GB {{literal}}"), Some(vec!["n".into()]));
    assert_eq!(fields("{broken"), None);
    assert_eq!(unescape(r#"Wait\u{2026} \"x\"\n"#), "Wait\u{2026} \"x\"\n");
}
