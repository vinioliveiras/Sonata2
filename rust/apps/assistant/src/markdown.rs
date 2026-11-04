//! The Markdown answers come in, as blocks the chat shows with labels
//! (assistant/markdown.py).
//!
//! ```text
//! blocks(text)  -> [Block]   kind: p, h1, h2, h3, li, ol, quote, code, hr
//! inline(text)  -> Pango markup (bold, italic, `code`, ~~strike~~, links)
//! ```
//!
//! Lists keep their marker in the text ("•  item", "2.  item"); a code
//! block's text is raw (shown monospaced, never parsed). An unclosed ```
//! (still streaming) is a code block up to the end.

use fancy_regex::{Captures, Regex};
use std::sync::LazyLock;

macro_rules! re {
    ($name:ident, $pat:expr) => {
        static $name: LazyLock<Regex> = LazyLock::new(|| Regex::new($pat).unwrap());
    };
}

re!(FENCE, r"^\s*(```|~~~)");
re!(HEAD, r"^(#{1,6})\s+(.*)$");
re!(BULLET, r"^(\s*)[-*+]\s+(.*)$");
re!(NUMBER, r"^(\s*)(\d+)[.)]\s+(.*)$");
re!(RULE, r"^\s*([-*_])(\s*\1){2,}\s*$");
re!(CODE, r"`([^`\n]+)`");
re!(LINK, r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)");
re!(BOLD, r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1");
re!(ITALIC, r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?!\*)|(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])");
re!(STRIKE, r"~~(?=\S)(.+?)(?<=\S)~~");
re!(HREF_MARK, "\u{e002}(\\d+)\u{e003}");
re!(CODE_MARK, "\u{e000}(\\d+)\u{e001}");

#[derive(Clone, Debug, PartialEq)]
pub struct Block {
    pub kind: &'static str,
    pub text: String,
    /// a code block's language ("```python")
    pub lang: String,
}

fn b(kind: &'static str, text: impl Into<String>) -> Block {
    Block { kind, text: text.into(), lang: String::new() }
}

fn caps<'t>(re: &Regex, s: &'t str) -> Option<Captures<'t>> {
    re.captures(s).ok().flatten()
}

fn group<'t>(c: &Captures<'t>, i: usize) -> &'t str {
    c.get(i).map(|m| m.as_str()).unwrap_or("")
}

/// A list's indent level: two columns a level, tabs as four (expandtabs(4)).
fn indent(ind: &str) -> String {
    let cols: usize = ind.chars().fold(0, |n, c| if c == '\t' { n + 4 - n % 4 } else { n + 1 });
    "    ".repeat(cols / 2)
}

pub fn blocks(text: &str) -> Vec<Block> {
    let mut out: Vec<Block> = Vec::new();
    let mut para: Vec<&str> = Vec::new();
    let end_para = |out: &mut Vec<Block>, para: &mut Vec<&str>| {
        if !para.is_empty() {
            out.push(b("p", para.iter().map(|s| s.trim()).collect::<Vec<_>>().join(" ")));
            para.clear();
        }
    };
    let lines: Vec<&str> = text.split('\n').collect();
    let mut i = 0;
    while i < lines.len() {
        let line = lines[i];
        if let Some(c) = caps(&FENCE, line) {
            end_para(&mut out, &mut para);
            let fence = group(&c, 1);
            let lang = line.trim().get(3..).unwrap_or("").trim().to_string();
            let mut code = Vec::new();
            i += 1;
            while i < lines.len() && !lines[i].trim().starts_with(fence) {
                code.push(lines[i]);
                i += 1;
            }
            out.push(Block { kind: "code", text: code.join("\n"), lang });
            i += 1;
            continue;
        }
        if let Some(c) = caps(&HEAD, line) {
            end_para(&mut out, &mut para);
            let kind = ["h1", "h2", "h3"][group(&c, 1).len().min(3) - 1];
            out.push(b(kind, group(&c, 2).trim().trim_end_matches('#').trim()));
        } else if caps(&RULE, line).is_some() {
            end_para(&mut out, &mut para);
            out.push(b("hr", ""));
        } else if let Some(c) = caps(&BULLET, line) {
            end_para(&mut out, &mut para);
            out.push(b("li", format!("{}•  {}", indent(group(&c, 1)), group(&c, 2))));
        } else if let Some(c) = caps(&NUMBER, line) {
            end_para(&mut out, &mut para);
            out.push(b("ol", format!("{}{}.  {}", indent(group(&c, 1)), group(&c, 2), group(&c, 3))));
        } else if line.starts_with('>') {
            end_para(&mut out, &mut para);
            let body = line.trim_start_matches('>').trim();
            match out.last_mut() {
                Some(last) if last.kind == "quote" => {
                    last.text.push('\n');
                    last.text.push_str(body);
                }
                _ => out.push(b("quote", body)),
            }
        } else if line.trim().is_empty() {
            end_para(&mut out, &mut para);
        } else if para.is_empty() && (line.starts_with(' ') || line.starts_with('\t')) && out.last().is_some_and(|l| l.kind == "li" || l.kind == "ol") {
            // a wrapped list item
            let last = out.last_mut().unwrap();
            last.text.push(' ');
            last.text.push_str(line.trim());
        } else {
            para.push(line);
        }
        i += 1;
    }
    end_para(&mut out, &mut para);
    out
}

fn sub(re: &Regex, s: &str, f: impl Fn(&Captures) -> String) -> String {
    let mut out = String::with_capacity(s.len());
    let mut last = 0;
    for c in re.captures_iter(s).flatten() {
        let m = c.get(0).unwrap();
        out.push_str(&s[last..m.start()]);
        out.push_str(&f(&c));
        last = m.end();
    }
    out.push_str(&s[last..]);
    out
}

/// Pango markup for one block of Markdown text (escaped first).
pub fn inline(text: &str) -> String {
    let codes = std::cell::RefCell::new(Vec::<String>::new());
    let hrefs = std::cell::RefCell::new(Vec::<String>::new());
    // code spans are never formatted: held aside behind private-use marks
    // (a NUL would cut the C string)
    let text = sub(&CODE, text, |c| {
        codes.borrow_mut().push(group(c, 1).to_string());
        format!("\u{e000}{}\u{e001}", codes.borrow().len() - 1)
    });
    let plain = glib::markup_escape_text(&text).to_string();
    // the <a href> tag is held aside too: emphasis never runs inside a URL
    let plain = sub(&LINK, &plain, |c| {
        hrefs.borrow_mut().push(group(c, 2).to_string());
        format!("\u{e002}{}\u{e003}{}</a>", hrefs.borrow().len() - 1, group(c, 1))
    });
    let s = sub(&BOLD, &plain, |c| format!("<b>{}</b>", group(c, 2)));
    let s = sub(&ITALIC, &s, |c| format!("<i>{}</i>", c.get(1).or(c.get(2)).map(|m| m.as_str()).unwrap_or("")));
    let mut s = sub(&STRIKE, &s, |c| format!("<s>{}</s>", group(c, 1)));
    // overlapping ** ~~ give misnested tags: GtkLabel would show nothing at
    // all (Pango has no <a>: checked as <span>)
    let check = sub(&HREF_MARK, &s, |_| "<span>".into()).replace("</a>", "</span>");
    if gtk::pango::parse_markup(&check, '\0').is_err() {
        s = plain; // links and code only, the text stays
    }
    let hrefs = hrefs.into_inner();
    let codes = codes.into_inner();
    let s = sub(&HREF_MARK, &s, |c| format!("<a href=\"{}\">", hrefs[group(c, 1).parse::<usize>().unwrap()]));
    sub(&CODE_MARK, &s, |c| format!("<tt>{}</tt>", glib::markup_escape_text(&codes[group(c, 1).parse::<usize>().unwrap()])))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_of_each_kind() {
        let text = "# Title\nSome *text*\nwrapped.\n\n- one\n  - two\n1. first\n> quote\n> more\n---\n```python\nx = 1 < 2\n```\nafter";
        let bs = blocks(text);
        assert_eq!(bs.iter().map(|b| b.kind).collect::<Vec<_>>(), ["h1", "p", "li", "li", "ol", "quote", "hr", "code", "p"]);
        assert_eq!(bs[1].text, "Some *text* wrapped.");
        assert_eq!(bs[3].text, "    •  two");
        assert_eq!(bs[4].text, "1.  first");
        assert_eq!(bs[5].text, "quote\nmore");
        assert_eq!(bs[7], Block { kind: "code", text: "x = 1 < 2".into(), lang: "python".into() });
        assert_eq!(blocks("## Head ##")[0], b("h2", "Head"));
        assert_eq!(blocks("- a\n  wrapped")[0].text, "•  a wrapped");
    }

    #[test]
    fn unclosed_fence_while_streaming() {
        assert_eq!(blocks("Look:\n```\ncode so far"), vec![b("p", "Look:"), b("code", "code so far")]);
    }

    #[test]
    fn inline_markup() {
        let s = inline("**bold** *it* `a<b & **c**` [x](https://e.com/?a=1&b=2) 3 * 4 a_b_c");
        assert!(s.contains("<b>bold</b>"), "{s}");
        assert!(s.contains("<i>it</i>"));
        assert!(s.contains("<tt>a&lt;b &amp; **c**</tt>")); // code spans never formatted
        assert!(s.contains("<a href=\"https://e.com/?a=1&amp;b=2\">x</a>"));
        assert!(s.contains("3 * 4 a_b_c"));
        assert!(inline("~~gone~~").contains("<s>gone</s>"));
        let plain = regex_strip_a(&s);
        assert!(gtk::pango::parse_markup(&plain, '\0').is_ok());
        assert!(gtk::pango::parse_markup(&inline("<script> & **unclosed"), '\0').is_ok());
    }

    #[test]
    fn misnested_emphasis_falls_back_to_plain_text() {
        // "**a ~~b** c~~" nests <b><s></b></s>: GtkLabel would show nothing
        let s = inline("**a ~~b** c~~");
        assert!(gtk::pango::parse_markup(&s, '\0').is_ok(), "{s}");
        assert_eq!(s, "**a ~~b** c~~");
    }

    fn regex_strip_a(s: &str) -> String {
        Regex::new(r"</?a[^>]*>").unwrap().replace_all(s, "").into_owned()
    }
}
