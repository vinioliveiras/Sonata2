//! Find & Replace (sonata2/textedit/search.py; no GTK): matches as
//! character offsets (the same offsets GtkTextIter uses), literal or
//! regular expression, with or without case.
//!
//! Regular expressions are Rust's `regex` syntax, which is Python's for
//! everything but back-references and look-around; replacements take
//! Python's `\1` / `\g<name>` (turned into Rust's own form here).

use regex::{Regex, RegexBuilder};

/// highlighting stops there (huge files, one-letter searches)
pub const MAX_MATCHES: usize = 20000;

#[derive(Clone, Copy, Debug, Default)]
pub struct Opts {
    pub case: bool,
    pub regex: bool,
    pub whole: bool,
}

/// A compiled pattern (Err for an invalid regular expression).
pub fn compile_pattern(needle: &str, o: Opts) -> Result<Regex, regex::Error> {
    let mut src = if o.regex { needle.to_string() } else { regex::escape(needle) };
    if o.whole {
        src = format!(r"\b(?:{src})\b");
    }
    RegexBuilder::new(&src).case_insensitive(!o.case).unicode(true).build()
}

/// Byte offsets of `text` -> character offsets, walking forward once.
struct Chars<'a> {
    text: &'a str,
    byte: usize,
    chars: usize,
}

impl<'a> Chars<'a> {
    fn new(text: &'a str) -> Self {
        Chars { text, byte: 0, chars: 0 }
    }

    fn at(&mut self, byte: usize) -> usize {
        if byte < self.byte {
            self.byte = 0;
            self.chars = 0;
        }
        self.chars += self.text[self.byte..byte].chars().count();
        self.byte = byte;
        self.chars
    }
}

/// The byte offset of character `n`.
pub fn byte_at(text: &str, n: usize) -> usize {
    text.char_indices().nth(n).map(|(b, _)| b).unwrap_or(text.len())
}

/// Every match, as (start, end) character offsets (empty matches are skipped).
pub fn find_all(text: &str, needle: &str, o: Opts, limit: usize) -> Result<Vec<(usize, usize)>, regex::Error> {
    if needle.is_empty() {
        return Ok(Vec::new());
    }
    let pat = compile_pattern(needle, o)?;
    let mut pos = Chars::new(text);
    let mut out = Vec::new();
    for m in pat.find_iter(text) {
        if m.start() == m.end() {
            continue; // empty regex matches (e.g. "^") are skipped
        }
        let a = pos.at(m.start());
        let b = pos.at(m.end());
        out.push((a, b));
        if out.len() >= limit {
            break;
        }
    }
    Ok(out)
}

/// Python's replacement template (`\1`, `\g<name>`, `\n`) in Rust's form.
pub fn rust_template(repl: &str) -> String {
    let mut out = String::new();
    let mut chars = repl.chars().peekable();
    while let Some(c) = chars.next() {
        match c {
            '$' => out.push_str("$$"),
            '\\' => match chars.next() {
                Some(d) if d.is_ascii_digit() => {
                    let mut n = d.to_string();
                    if let Some(e) = chars.peek().filter(|e| e.is_ascii_digit()) {
                        n.push(*e);
                        chars.next();
                    }
                    out.push_str(&format!("${{{n}}}"));
                }
                Some('g') if chars.peek() == Some(&'<') => {
                    chars.next();
                    let name: String = chars.by_ref().take_while(|c| *c != '>').collect();
                    out.push_str(&format!("${{{name}}}"));
                }
                Some('n') => out.push('\n'),
                Some('t') => out.push('\t'),
                Some('r') => out.push('\r'),
                Some('\\') => out.push('\\'),
                Some(other) => {
                    out.push('\\');
                    out.push(other);
                }
                None => out.push('\\'),
            },
            c => out.push(c),
        }
    }
    out
}

/// The replacement for the match at `span` (regex: groups expanded).
pub fn expand(text: &str, span: (usize, usize), needle: &str, repl: &str, o: Opts) -> String {
    if !o.regex {
        return repl.to_string();
    }
    let Ok(pat) = compile_pattern(needle, o) else { return repl.to_string() };
    let (a, b) = (byte_at(text, span.0), byte_at(text, span.1));
    let caps = pat.captures_at(text, a).filter(|c| c.get(0).is_some_and(|m| m.start() == a && m.end() == b));
    let caps = caps.or_else(|| pat.captures(&text[..b]).filter(|c| c.get(0).is_some_and(|m| m.start() == a)));
    match caps {
        Some(c) => {
            let mut out = String::new();
            c.expand(&rust_template(repl), &mut out);
            out
        }
        None => repl.to_string(),
    }
}

/// (new text, number of replacements).
pub fn replace_all(text: &str, needle: &str, repl: &str, o: Opts) -> Result<(String, usize), regex::Error> {
    if needle.is_empty() {
        return Ok((text.to_string(), 0));
    }
    let pat = compile_pattern(needle, o)?;
    let n = pat.find_iter(text).count();
    let new = if o.regex {
        pat.replace_all(text, rust_template(repl).as_str()).into_owned()
    } else {
        pat.replace_all(text, regex::NoExpand(repl)).into_owned()
    };
    Ok((new, n))
}

/// The match after (or before) a character offset, wrapping around.
pub fn next_span(spans: &[(usize, usize)], offset: usize, backwards: bool, include_current: bool) -> Option<(usize, usize)> {
    if spans.is_empty() {
        return None;
    }
    if backwards {
        return Some(spans.iter().rev().find(|s| s.0 < offset).copied().unwrap_or(spans[spans.len() - 1]));
    }
    Some(spans.iter().find(|s| if include_current { s.0 >= offset } else { s.0 > offset }).copied().unwrap_or(spans[0]))
}

#[cfg(test)]
mod tests {
    use super::*;

    const CASE: Opts = Opts { case: true, regex: false, whole: false };

    #[test]
    fn find() {
        let text = "Sonata sonata SONATA sonatas";
        let all = |n: &str, o: Opts| find_all(text, n, o, MAX_MATCHES).unwrap();
        assert_eq!(all("sonata", Opts::default()).len(), 4);
        assert_eq!(all("sonata", CASE), vec![(7, 13), (21, 27)]);
        assert_eq!(all("sonata", Opts { whole: true, ..Default::default() }).len(), 3);
        assert_eq!(all(r"S\w+A", Opts { case: true, regex: true, whole: false }), vec![(14, 20)]);
        assert!(all("", Opts::default()).is_empty());
        assert_eq!(find_all("a.b", ".", Opts::default(), MAX_MATCHES).unwrap(), vec![(1, 2)]); // literal by default
        assert!(find_all(text, "(", Opts { regex: true, ..Default::default() }, MAX_MATCHES).is_err());
        // character offsets, not bytes
        assert_eq!(find_all("ção ção", "ção", Opts::default(), MAX_MATCHES).unwrap(), vec![(0, 3), (4, 7)]);
        let spans = [(0, 1), (5, 6), (9, 10)];
        assert_eq!(next_span(&spans, 5, false, false), Some((9, 10)));
        assert_eq!(next_span(&spans, 5, false, true), Some((5, 6)));
        assert_eq!(next_span(&spans, 9, false, false), Some((0, 1))); // wraps
        assert_eq!(next_span(&spans, 0, true, false), Some((9, 10)));
    }

    #[test]
    fn replace() {
        let re = Opts { regex: true, ..Default::default() };
        assert_eq!(replace_all("a.b.c", ".", "\\", Opts::default()).unwrap(), ("a\\b\\c".to_string(), 2));
        assert_eq!(replace_all("x=1, y=2", r"(\w)=(\d)", r"\2:\1", re).unwrap(), ("1:x, 2:y".to_string(), 2));
        assert_eq!(expand("key=value", (0, 9), r"(\w+)=(\w+)", r"\2=\1", re), "value=key");
        assert_eq!(expand("a1", (0, 2), r"(?P<l>\w)(\d)", r"\g<l>-$", re), "a-$");
    }
}
