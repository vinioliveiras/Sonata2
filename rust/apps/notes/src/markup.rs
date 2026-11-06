//! The Markdown-ish text a note is stored as (sonata2/notes/markup.py, no
//! GTK). One line per paragraph; a line's prefix is its style, inline
//! markers its character formatting:
//!
//! ```text
//! # Title            ## Heading         ### Subheading
//! - bullet           1. numbered        - [ ] / - [x] checklist
//! **bold**  *italic*  __underline__  ~~strikethrough~~
//! ```
//!
//! A backslash escapes the next character (the serializer escapes every
//! literal \ * _ ~ and a body line that would read as a prefix), so any
//! text round-trips. A parsed line is (kind, checked, runs), runs being
//! [(text, inline tags)].

use sonata_i18n::tr;
use std::collections::BTreeSet;

pub const KINDS: [&str; 7] = ["title", "heading", "subheading", "body", "bullet", "number", "check"];
pub const LISTS: [&str; 3] = ["bullet", "number", "check"];
pub const INLINE: [&str; 4] = ["bold", "italic", "underline", "strike"];

pub type Tags = BTreeSet<&'static str>;
pub type Runs = Vec<(String, Tags)>;

#[derive(Clone, Debug, PartialEq)]
pub struct Line {
    pub kind: &'static str,
    pub checked: bool,
    pub runs: Runs,
}

fn mark(tag: &str) -> &'static str {
    match tag {
        "bold" => "**",
        "italic" => "*",
        "underline" => "__",
        _ => "~~",
    }
}

const TOKENS: [(&str, &str); 4] = [("**", "bold"), ("__", "underline"), ("~~", "strike"), ("*", "italic")];
const PREFIX: [(&str, &str); 7] = [
    ("### ", "subheading"),
    ("## ", "heading"),
    ("# ", "title"),
    ("- [ ] ", "check"),
    ("- [x] ", "check"),
    ("- [X] ", "check"),
    ("- ", "bullet"),
];

pub fn parse_inline(s: &str) -> Runs {
    let mut runs: Runs = Vec::new();
    let mut cur = String::new();
    let mut tags: Tags = Tags::new();
    let flush = |cur: &mut String, tags: &Tags, runs: &mut Runs| {
        if cur.is_empty() {
            return;
        }
        match runs.last_mut() {
            Some(last) if last.1 == *tags => last.0.push_str(cur),
            _ => runs.push((cur.clone(), tags.clone())),
        }
        cur.clear();
    };
    let mut rest = s;
    while let Some(c) = rest.chars().next() {
        if c == '\\' && rest.len() > 1 {
            let mut it = rest.chars();
            it.next();
            if let Some(n) = it.next() {
                cur.push(n);
                rest = &rest[1 + n.len_utf8()..];
                continue;
            }
        }
        if let Some((tok, tag)) = TOKENS.iter().find(|(t, _)| rest.starts_with(t)) {
            flush(&mut cur, &tags, &mut runs);
            if !tags.remove(tag) {
                tags.insert(tag);
            }
            rest = &rest[tok.len()..];
            continue;
        }
        cur.push(c);
        rest = &rest[c.len_utf8()..];
    }
    flush(&mut cur, &tags, &mut runs);
    runs
}

/// "12. " at the start: the length of that prefix.
fn number_prefix(line: &str) -> Option<usize> {
    let digits = line.bytes().take_while(u8::is_ascii_digit).count();
    (digits > 0 && line[digits..].starts_with(". ")).then_some(digits + 2)
}

pub fn parse_line(line: &str) -> Line {
    for (prefix, kind) in PREFIX {
        if let Some(rest) = line.strip_prefix(prefix) {
            return Line { kind, checked: prefix == "- [x] " || prefix == "- [X] ", runs: parse_inline(rest) };
        }
    }
    if let Some(n) = number_prefix(line) {
        return Line { kind: "number", checked: false, runs: parse_inline(&line[n..]) };
    }
    Line { kind: "body", checked: false, runs: parse_inline(line) }
}

pub fn parse(md: &str) -> Vec<Line> {
    md.split('\n').map(parse_line).collect()
}

fn escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    for c in text.chars() {
        if matches!(c, '\\' | '*' | '_' | '~') {
            out.push('\\');
        }
        out.push(c);
    }
    out
}

pub fn serialize_runs(runs: &Runs) -> String {
    let mut out = String::new();
    let mut open: Vec<&'static str> = Vec::new();
    for (text, tags) in runs {
        if text.is_empty() {
            continue;
        }
        for t in open.iter().rev() {
            // close what this run doesn't have
            if !tags.contains(t) {
                out.push_str(mark(t));
            }
        }
        open.retain(|t| tags.contains(t));
        for t in INLINE {
            if tags.contains(t) && !open.contains(&t) {
                out.push_str(mark(t));
                open.push(t);
            }
        }
        out.push_str(&escape(text));
    }
    for t in open.iter().rev() {
        out.push_str(mark(t));
    }
    out
}

/// A body line that would read as a prefix: `#`, `- `, `12. `.
fn looks_like_prefix(s: &str) -> bool {
    s.starts_with('#') || s.starts_with("- ") || number_prefix(s).is_some()
}

pub fn serialize_line(l: &Line) -> String {
    let body = serialize_runs(&l.runs);
    if l.kind == "body" {
        return if looks_like_prefix(&body) { format!("\\{body}") } else { body };
    }
    let body = if l.kind == "bullet" && (body.starts_with("[ ] ") || body.starts_with("[x] ") || body.starts_with("[X] ")) {
        format!("\\{body}") // written "- \[ ] x": stays a bullet
    } else {
        body
    };
    let prefix = match l.kind {
        "title" => "# ",
        "heading" => "## ",
        "subheading" => "### ",
        "bullet" => "- ",
        "number" => "1. ",
        _ if l.checked => "- [x] ",
        _ => "- [ ] ",
    };
    format!("{prefix}{body}")
}

pub fn serialize(lines: &[Line]) -> String {
    lines.iter().map(serialize_line).collect::<Vec<_>>().join("\n")
}

/// The note's lines as plain text (no markers).
pub fn plain(md: &str) -> Vec<String> {
    parse(md).into_iter().map(|l| l.runs.into_iter().map(|r| r.0).collect()).collect()
}

/// The first non-empty line (the note's title) and the next one.
pub fn title_and_preview(md: &str) -> (String, String) {
    let lines: Vec<String> = plain(md).into_iter().map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
    (
        lines.first().cloned().unwrap_or_else(|| tr!("New Note")),
        lines.get(1).cloned().unwrap_or_else(|| tr!("No additional text")),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = "# Shopping\nPlain **bold** *italic* __under__ ~~gone~~ and a*star\n## Heading\n### Sub\n- bullet\n1. one\n2. two\n- [ ] todo\n- [x] done\n\\- not a list\n";

    #[test]
    fn round_trip() {
        sonata_i18n::use_language("en");
        let again = serialize(&parse(SAMPLE));
        assert_eq!(parse(&again), parse(SAMPLE));
        let kinds: Vec<&str> = parse(SAMPLE).iter().map(|l| l.kind).collect();
        assert_eq!(&kinds[..9], ["title", "body", "heading", "subheading", "bullet", "number", "number", "check", "check"]);
        assert_eq!(plain(SAMPLE)[9], "- not a list");
        assert_eq!(title_and_preview(SAMPLE).0, "Shopping");
        assert_eq!(title_and_preview("").0, "New Note");
    }

    #[test]
    fn inline_tags_and_escapes() {
        let runs = parse_inline("a **b *c*** \\*d");
        let tags: Vec<Vec<&str>> = runs.iter().map(|r| r.1.iter().copied().collect()).collect();
        assert_eq!(runs.iter().map(|r| r.0.as_str()).collect::<Vec<_>>(), ["a ", "b ", "c", " *d"]);
        assert_eq!(tags, vec![vec![], vec!["bold"], vec!["bold", "italic"], vec![]]);
        assert_eq!(serialize_runs(&runs), "a **b *c*** \\*d");
        // a body line that would read as a list stays a body line
        let l = Line { kind: "body", checked: false, runs: vec![("1. not a list".into(), Tags::new())] };
        assert_eq!(parse_line(&serialize_line(&l)).kind, "body");
        let b = Line { kind: "bullet", checked: false, runs: vec![("[ ] x".into(), Tags::new())] };
        assert_eq!(parse_line(&serialize_line(&b)), b);
    }
}
