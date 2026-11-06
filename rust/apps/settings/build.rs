//! The search index (search.py's `_index_source`), read from the pages'
//! own source at build time: every string a section can show is one its
//! `page_<part>` function -- or a function of the same file it uses --
//! puts through tr! / trn! / trp! / n_! / nc_!. Searching never builds a
//! section (that starts Bluetooth searches, password prompts, system reads).
//!
//! Written to $OUT_DIR/search_index.rs as
//! `INDEX: &[(part, &[(msgid, Option<context>)])]`.

use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::Path;

/// Modules whose whole text belongs to one part (search.py's MODULES).
const MODULES: &[(&str, &str)] = &[
    ("src/apps_page.rs", "apps"),
    ("src/appicons_page.rs", "appicons"),
    ("src/language_page.rs", "language"),
    ("src/shortcuts_page.rs", "shortcuts"),
    ("src/arrange.rs", "displays"),
];

#[derive(Debug, PartialEq)]
enum Tok {
    Ident(String),
    Str(String),
    Punct(char),
}

fn unescape(s: &str) -> String {
    let mut out = String::new();
    let mut it = s.chars().peekable();
    while let Some(c) = it.next() {
        if c != '\\' {
            out.push(c);
            continue;
        }
        match it.next() {
            Some('n') => out.push('\n'),
            Some('t') => out.push('\t'),
            Some('r') => out.push('\r'),
            Some('0') => out.push('\0'),
            Some('\\') => out.push('\\'),
            Some('"') => out.push('"'),
            Some('\'') => out.push('\''),
            Some('u') => {
                let mut hex = String::new();
                it.next(); // {
                for h in it.by_ref() {
                    if h == '}' {
                        break;
                    }
                    hex.push(h);
                }
                if let Some(ch) = u32::from_str_radix(&hex, 16).ok().and_then(char::from_u32) {
                    out.push(ch);
                }
            }
            Some('\n') => {
                // a line continuation: the next line's leading blanks go too
                while it.peek().is_some_and(|c| c.is_whitespace()) {
                    it.next();
                }
            }
            Some(o) => out.push(o),
            None => {}
        }
    }
    out
}

fn tokens(src: &str) -> Vec<Tok> {
    let b: Vec<char> = src.chars().collect();
    let mut out = Vec::new();
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        if c == '/' && b.get(i + 1) == Some(&'/') {
            while i < b.len() && b[i] != '\n' {
                i += 1;
            }
        } else if c == '/' && b.get(i + 1) == Some(&'*') {
            i += 2;
            while i + 1 < b.len() && !(b[i] == '*' && b[i + 1] == '/') {
                i += 1;
            }
            i += 2;
        } else if c == 'r' && (b.get(i + 1) == Some(&'"') || (b.get(i + 1) == Some(&'#') && b.get(i + 2).is_some_and(|c| *c == '"' || *c == '#'))) {
            let mut j = i + 1;
            let mut hashes = 0;
            while b[j] == '#' {
                hashes += 1;
                j += 1;
            }
            j += 1; // "
            let start = j;
            loop {
                if j >= b.len() {
                    break;
                }
                if b[j] == '"' && (0..hashes).all(|k| b.get(j + 1 + k) == Some(&'#')) {
                    break;
                }
                j += 1;
            }
            out.push(Tok::Str(b[start..j.min(b.len())].iter().collect()));
            i = j + 1 + hashes;
        } else if c == '"' {
            let mut j = i + 1;
            let mut raw = String::new();
            while j < b.len() && b[j] != '"' {
                if b[j] == '\\' && j + 1 < b.len() {
                    raw.push(b[j]);
                    j += 1;
                }
                raw.push(b[j]);
                j += 1;
            }
            out.push(Tok::Str(unescape(&raw)));
            i = j + 1;
        } else if c == '\'' {
            // a char literal ('a', '\n', '\u{4}') or a lifetime ('a)
            if b.get(i + 1) == Some(&'\\') {
                let mut j = i + 2;
                while j < b.len() && b[j] != '\'' {
                    j += 1;
                }
                i = j + 1;
            } else if b.get(i + 2) == Some(&'\'') {
                i += 3;
            } else {
                i += 1;
            }
        } else if c.is_alphabetic() || c == '_' {
            let mut j = i;
            while j < b.len() && (b[j].is_alphanumeric() || b[j] == '_') {
                j += 1;
            }
            out.push(Tok::Ident(b[i..j].iter().collect()));
            i = j;
        } else if c.is_whitespace() || c.is_ascii_digit() {
            i += 1;
        } else {
            out.push(Tok::Punct(c));
            i += 1;
        }
    }
    out
}

type Strings = Vec<(String, Option<String>)>;

/// (msgid, context) of every translatable string in `toks`.
fn strings(toks: &[Tok]) -> Strings {
    let mut out = Vec::new();
    for (k, t) in toks.iter().enumerate() {
        let Tok::Ident(name) = t else { continue };
        if toks.get(k + 1) != Some(&Tok::Punct('!')) || toks.get(k + 2) != Some(&Tok::Punct('(')) {
            continue;
        }
        let arg = |n: usize| match toks.get(k + 3 + n) {
            Some(Tok::Str(s)) => Some(s.clone()),
            _ => None,
        };
        match name.as_str() {
            "tr" | "n_" | "trn" => {
                if let Some(s) = arg(0) {
                    out.push((s, None));
                }
            }
            "trp" | "nc_" => {
                if let (Some(ctx), Some(Tok::Punct(',')), Some(Tok::Str(s))) = (arg(0), toks.get(k + 4), toks.get(k + 5)) {
                    out.push((s.clone(), Some(ctx)));
                }
            }
            _ => {}
        }
    }
    out
}

/// fn name -> (its strings, the names it uses), for every fn of a file
/// (methods of the same name merged).
fn functions(toks: &[Tok]) -> BTreeMap<String, (Strings, BTreeSet<String>)> {
    let mut out: BTreeMap<String, (Strings, BTreeSet<String>)> = BTreeMap::new();
    for (k, t) in toks.iter().enumerate() {
        if *t != Tok::Ident("fn".into()) {
            continue;
        }
        let Some(Tok::Ident(name)) = toks.get(k + 1) else { continue };
        // the body: the first `{` after the signature (none: a declaration)
        let mut j = k + 2;
        while j < toks.len() && toks[j] != Tok::Punct('{') && toks[j] != Tok::Punct(';') {
            j += 1;
        }
        if j >= toks.len() || toks[j] == Tok::Punct(';') {
            continue;
        }
        let (start, mut depth) = (j, 0);
        while j < toks.len() {
            match toks[j] {
                Tok::Punct('{') => depth += 1,
                Tok::Punct('}') => {
                    depth -= 1;
                    if depth == 0 {
                        break;
                    }
                }
                _ => {}
            }
            j += 1;
        }
        let body = &toks[start..j.min(toks.len())];
        let e = out.entry(name.clone()).or_default();
        e.0.extend(strings(body));
        e.1.extend(body.iter().filter_map(|t| match t {
            Tok::Ident(s) => Some(s.clone()),
            _ => None,
        }));
    }
    out
}

fn rs_files(dir: &Path, out: &mut Vec<std::path::PathBuf>) {
    let Ok(rd) = fs::read_dir(dir) else { return };
    let mut entries: Vec<_> = rd.flatten().map(|e| e.path()).collect();
    entries.sort();
    for p in entries {
        if p.is_dir() {
            rs_files(&p, out);
        } else if p.extension().is_some_and(|e| e == "rs") {
            out.push(p);
        }
    }
}

fn main() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"));
    println!("cargo:rerun-if-changed=src");
    let mut index: BTreeMap<String, Strings> = BTreeMap::new();
    let mut files = Vec::new();
    rs_files(&root.join("src"), &mut files);
    for f in &files {
        // a part's builder lives in src/pages/ (page_arg in lib.rs is no part)
        if !f.starts_with(root.join("src/pages")) {
            continue;
        }
        let src = fs::read_to_string(f).unwrap_or_default();
        let fns = functions(&tokens(&src));
        for (name, _) in fns.iter().filter(|(n, _)| n.starts_with("page_")) {
            let part = name["page_".len()..].to_string();
            let (mut seen, mut todo, mut found) = (BTreeSet::new(), vec![name.clone()], Vec::new());
            while let Some(m) = todo.pop() {
                if !seen.insert(m.clone()) {
                    continue;
                }
                let Some((strs, uses)) = fns.get(&m) else { continue };
                found.extend(strs.iter().cloned());
                // its helpers' strings too, not other sections'
                todo.extend(uses.iter().filter(|u| !u.starts_with("page_") && fns.contains_key(*u)).cloned());
            }
            index.entry(part).or_default().extend(found);
        }
    }
    for (file, part) in MODULES {
        let src = fs::read_to_string(root.join(file)).unwrap_or_default();
        index.entry(part.to_string()).or_default().extend(strings(&tokens(&src)));
    }
    let mut code = String::from("pub static INDEX: &[(&str, &[(&str, Option<&str>)])] = &[\n");
    for (part, strs) in &index {
        code.push_str(&format!("    ({part:?}, &["));
        let mut seen = BTreeSet::new();
        for (s, ctx) in strs {
            if seen.insert((s, ctx)) {
                code.push_str(&format!("({s:?}, {ctx:?}), "));
            }
        }
        code.push_str("]),\n");
    }
    code.push_str("];\n");
    let out = Path::new(&std::env::var("OUT_DIR").unwrap()).join("search_index.rs");
    fs::write(out, code).unwrap();
}
