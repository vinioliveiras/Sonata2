//! TextEdit's file formats (sonata2/textedit/document.py; no GTK): reading
//! any text-like file with its encoding and line endings, writing it back
//! the same way, and a plain-text extraction of word-processor files
//! (.docx, .odt, .rtf) opened read-only.

use regex::Regex;
use sonata_i18n::{fmt, n_, tr};
use std::io::Read;
use std::sync::OnceLock;

/// encoding id -> label shown in the status bar menu (translated where marked)
pub const ENCODINGS: [(&str, &str); 5] = [
    ("utf-8", "Unicode (UTF-8)"),
    ("utf-16-le", "Unicode (UTF-16 LE)"),
    ("utf-16-be", "Unicode (UTF-16 BE)"),
    ("windows-1252", n_!("Western (Windows Latin 1)")),
    ("iso-8859-1", n_!("Western (ISO Latin 1)")),
];
pub const NEWLINES: [(&str, &str); 3] = [("LF", "\n"), ("CRLF", "\r\n"), ("CR", "\r")];
pub const NEWLINE_LABELS: [(&str, &str); 3] =
    [("LF", "Linux / Unix (LF)"), ("CRLF", "Windows (CRLF)"), ("CR", n_!("Old style (CR)"))];

fn short(encoding: &str) -> String {
    match encoding {
        "utf-8" => "UTF-8".into(),
        "utf-16-le" => "UTF-16 LE".into(),
        "utf-16-be" => "UTF-16 BE".into(),
        "windows-1252" => "Windows-1252".into(),
        "iso-8859-1" => "ISO-8859-1".into(),
        e => e.to_uppercase(),
    }
}

pub fn is_encoding(e: &str) -> bool {
    ENCODINGS.iter().any(|x| x.0 == e)
}

pub fn is_newline(n: &str) -> bool {
    NEWLINES.iter().any(|x| x.0 == n)
}

pub const CONVERTED: [&str; 3] = [".docx", ".odt", ".rtf"];
/// extensions shown in a monospaced face when the font is "Automatic"
const CODE_EXTS: &[&str] = &[
    "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "cs", "java", "kt", "kts", "go", "rs", "swift", "m", "mm", "py", "pyw",
    "pyi", "rb", "pl", "pm", "php", "lua", "r", "jl", "dart", "scala", "clj", "hs", "ml", "js", "mjs", "cjs", "jsx",
    "ts", "tsx", "vue", "svelte", "css", "scss", "sass", "less", "html", "htm", "xml", "xhtml", "svg", "json", "jsonc",
    "yaml", "yml", "toml", "ini", "cfg", "conf", "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd", "mk", "cmake",
    "gradle", "sql", "diff", "patch", "desktop", "service", "gitignore", "dockerfile", "nix", "zig", "v", "sv", "vhd",
    "asm", "s", "log", "csv", "tsv", "env", "properties", "gd", "glsl", "frag", "vert", "hlsl", "tex",
];
const CODE_NAMES: &[&str] = &["makefile", "dockerfile", "cmakelists.txt", "pkgbuild", "meson.build", "justfile", "gemfile"];

/// The data isn't text (NUL bytes that aren't UTF-16).
#[derive(Debug, PartialEq)]
pub struct BinaryFile;

/// A character the encoding can't write.
#[derive(Debug, PartialEq)]
pub struct EncodeError;

#[derive(Clone, Debug, PartialEq)]
pub struct Decoded {
    /// "\n" line endings
    pub text: String,
    pub encoding: String,
    pub bom: bool,
    pub newline: String,
}

impl Decoded {
    pub fn plain(text: String) -> Self {
        Decoded { text, encoding: "utf-8".into(), bom: false, newline: "LF".into() }
    }
}

/// The line ending used most: "LF", "CRLF" or "CR" (LF when none).
pub fn detect_newline(text: &str) -> &'static str {
    let crlf = text.matches("\r\n").count();
    let cr = text.matches('\r').count() - crlf;
    let lf = text.matches('\n').count() - crlf;
    // max() keeps the first of equals: LF, then CRLF, then CR
    let mut best = (lf, "LF");
    for c in [(crlf, "CRLF"), (cr, "CR")] {
        if c.0 > best.0 {
            best = c;
        }
    }
    if best.0 > 0 { best.1 } else { "LF" }
}

pub fn normalize(text: &str) -> String {
    if text.contains('\r') { text.replace("\r\n", "\n").replace('\r', "\n") } else { text.to_string() }
}

// -- the encodings -----------------------------------------------------------------------------
/// Windows-1252's 0x80..0x9F (None: the five bytes it leaves undefined,
/// which Python's codec refuses).
const CP1252_HIGH: [Option<char>; 32] = [
    Some('\u{20AC}'), None, Some('\u{201A}'), Some('\u{0192}'), Some('\u{201E}'), Some('\u{2026}'), Some('\u{2020}'),
    Some('\u{2021}'), Some('\u{02C6}'), Some('\u{2030}'), Some('\u{0160}'), Some('\u{2039}'), Some('\u{0152}'), None,
    Some('\u{017D}'), None, None, Some('\u{2018}'), Some('\u{2019}'), Some('\u{201C}'), Some('\u{201D}'),
    Some('\u{2022}'), Some('\u{2013}'), Some('\u{2014}'), Some('\u{02DC}'), Some('\u{2122}'), Some('\u{0161}'),
    Some('\u{203A}'), Some('\u{0153}'), None, Some('\u{017E}'), Some('\u{0178}'),
];

fn cp1252_char(b: u8) -> Option<char> {
    if (0x80..0xA0).contains(&b) { CP1252_HIGH[(b - 0x80) as usize] } else { Some(b as char) }
}

fn utf16_decode(data: &[u8], big: bool) -> String {
    let units = data.chunks(2).filter(|c| c.len() == 2).map(|c| if big { u16::from_be_bytes([c[0], c[1]]) } else { u16::from_le_bytes([c[0], c[1]]) });
    let mut s: String = char::decode_utf16(units).map(|r| r.unwrap_or('\u{FFFD}')).collect();
    if data.len() % 2 == 1 {
        s.push('\u{FFFD}'); // a byte left over (errors="replace")
    }
    s
}

/// Decode with Python's codec of that name, errors="replace".
pub fn decode_lossy(data: &[u8], encoding: &str) -> String {
    match encoding {
        "utf-16-le" => utf16_decode(data, false),
        "utf-16-be" => utf16_decode(data, true),
        "windows-1252" => data.iter().map(|b| cp1252_char(*b).unwrap_or('\u{FFFD}')).collect(),
        "iso-8859-1" => data.iter().map(|b| *b as char).collect(),
        _ => String::from_utf8_lossy(data).into_owned(),
    }
}

/// UTF-16 without a BOM: one byte of each pair is mostly zero (ASCII-heavy text).
fn utf16_guess(data: &[u8]) -> Option<&'static str> {
    let sample = &data[..data.len().min(4096)];
    let pairs = sample.len() / 2;
    if pairs < 2 {
        return None;
    }
    let even = (0..pairs).filter(|i| sample[i * 2] == 0).count() as f64;
    let odd = (0..pairs).filter(|i| sample[i * 2 + 1] == 0).count() as f64;
    let p = pairs as f64;
    let enc = if odd > p * 0.3 && even < p * 0.05 {
        "utf-16-le"
    } else if even > p * 0.3 && odd < p * 0.05 {
        "utf-16-be"
    } else {
        return None;
    };
    let text = decode_lossy(&sample[..pairs * 2], enc);
    let n = text.chars().count() as f64;
    let bad = text.chars().filter(|c| (*c < ' ' && !"\t\n\r\x0c".contains(*c)) || *c == '\u{FFFD}').count() as f64;
    if bad <= n * 0.02 { Some(enc) } else { None }
}

const BOMS: [(&[u8], &str); 3] = [(b"\xef\xbb\xbf", "utf-8"), (b"\xff\xfe", "utf-16-le"), (b"\xfe\xff", "utf-16-be")];

/// Text of a file: BOM (UTF-8 / UTF-16) first, then UTF-8, then Windows
/// Latin 1 and ISO Latin 1 (which decodes anything).
pub fn decode(data: &[u8]) -> Result<Decoded, BinaryFile> {
    let mut data = data;
    let (mut enc, mut bom) = (None, false);
    for (mark, name) in BOMS {
        if data.starts_with(mark) {
            enc = Some(name);
            bom = true;
            data = &data[mark.len()..];
            break;
        }
    }
    if enc.is_none() && data.contains(&0) {
        // anywhere: a NUL past the sniff window is binary too
        enc = Some(utf16_guess(data).ok_or(BinaryFile)?);
    }
    let (text, enc) = match enc {
        Some(e) => (decode_lossy(data, e), e),
        None => match std::str::from_utf8(data) {
            Ok(s) => (s.to_string(), "utf-8"),
            Err(_) => match data.iter().map(|b| cp1252_char(*b)).collect::<Option<String>>() {
                Some(s) => (s, "windows-1252"),
                None => (decode_lossy(data, "iso-8859-1"), "iso-8859-1"),
            },
        },
    };
    if text.contains('\0') {
        // GtkTextBuffer stops at NUL and a replacement char would be saved
        // in its place: refuse rather than change the file's bytes
        return Err(BinaryFile);
    }
    let nl = detect_newline(&text);
    Ok(Decoded { text: normalize(&text), encoding: enc.into(), bom, newline: nl.into() })
}

/// Bytes to write: the buffer's "\n" turned back into the document's line
/// ending, in its encoding (EncodeError when a character doesn't fit).
pub fn encode(text: &str, encoding: &str, bom: bool, newline: &str) -> Result<Vec<u8>, EncodeError> {
    let nl = NEWLINES.iter().find(|n| n.0 == newline).map(|n| n.1).unwrap_or("\n");
    let text = if nl != "\n" { text.replace('\n', nl) } else { text.to_string() };
    let mut out = Vec::new();
    if bom {
        match encoding {
            "utf-8" => out.extend_from_slice(b"\xef\xbb\xbf"),
            "utf-16-le" => out.extend_from_slice(b"\xff\xfe"),
            "utf-16-be" => out.extend_from_slice(b"\xfe\xff"),
            _ => {}
        }
    }
    match encoding {
        "utf-16-le" => text.encode_utf16().for_each(|u| out.extend_from_slice(&u.to_le_bytes())),
        "utf-16-be" => text.encode_utf16().for_each(|u| out.extend_from_slice(&u.to_be_bytes())),
        "iso-8859-1" => {
            for c in text.chars() {
                out.push(u8::try_from(c as u32).map_err(|_| EncodeError)?);
            }
        }
        "windows-1252" => {
            for c in text.chars() {
                let b = if (c as u32) < 0x80 || (0xA0..0x100).contains(&(c as u32)) {
                    c as u32 as u8
                } else {
                    CP1252_HIGH.iter().position(|x| *x == Some(c)).map(|i| 0x80 + i as u8).ok_or(EncodeError)?
                };
                out.push(b);
            }
        }
        _ => out.extend_from_slice(text.as_bytes()),
    }
    Ok(out)
}

/// "UTF-8", "UTF-8 with BOM", "Windows-1252"...
pub fn label(encoding: &str, bom: bool) -> String {
    let s = short(encoding);
    if bom && encoding == "utf-8" { fmt(&tr!("{encoding} with BOM"), &[("encoding", &s)]) } else { s }
}

// -- statistics ------------------------------------------------------------------------------
pub fn words(text: &str) -> usize {
    static WORD: OnceLock<Regex> = OnceLock::new();
    WORD.get_or_init(|| Regex::new(r"\w+(?:['’]\w+)*").unwrap()).find_iter(text).count()
}

/// A source/config file (shown monospaced when the font is Automatic).
pub fn is_code(name: &str) -> bool {
    if name.is_empty() {
        return false;
    }
    let low = name.to_lowercase();
    if CODE_NAMES.contains(&low.as_str()) {
        return true;
    }
    let ext = if low.contains('.') { low.rsplit('.').next().unwrap_or("") } else { "" };
    CODE_EXTS.contains(&ext) || (low.starts_with('.') && {
        let rest = low.trim_start_matches('.');
        CODE_EXTS.contains(&rest) || ["bashrc", "zshrc", "profile"].contains(&rest)
    })
}

pub fn is_converted(name: &str) -> bool {
    let low = name.to_lowercase();
    CONVERTED.iter().any(|e| low.ends_with(e))
}

// -- word-processor files (read-only, converted) ---------------------------------------------
/// Plain text of a .docx / .odt / .rtf document, Ok(None) for other files,
/// Err when the file is damaged.
pub fn convert(data: &[u8], name: &str) -> Result<Option<String>, String> {
    let low = name.to_lowercase();
    if low.ends_with(".docx") {
        return docx(data).map(Some);
    }
    if low.ends_with(".odt") {
        return odt(data).map(Some);
    }
    if low.ends_with(".rtf") {
        let src: String = data.iter().map(|b| *b as char).collect(); // latin-1
        return Ok(Some(rtf(&src)));
    }
    Ok(None)
}

fn zip_member(data: &[u8], member: &str) -> Result<String, String> {
    let mut z = zip::ZipArchive::new(std::io::Cursor::new(data)).map_err(|e| e.to_string())?;
    let mut f = z.by_name(member).map_err(|e| e.to_string())?;
    let mut s = String::new();
    f.read_to_string(&mut s).map_err(|e| e.to_string())?;
    Ok(s)
}

fn docx(data: &[u8]) -> Result<String, String> {
    const W: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
    let xml = zip_member(data, "word/document.xml")?;
    let doc = roxmltree::Document::parse(&xml).map_err(|e| e.to_string())?;
    let is = |n: &roxmltree::Node, tag: &str| n.is_element() && n.tag_name().namespace() == Some(W) && n.tag_name().name() == tag;
    let mut paras = Vec::new();
    for p in doc.descendants().filter(|n| is(n, "p")) {
        let mut out = String::new();
        for el in p.descendants() {
            if is(&el, "t") {
                out.push_str(el.text().unwrap_or(""));
            } else if is(&el, "tab") {
                out.push('\t');
            } else if is(&el, "br") || is(&el, "cr") {
                out.push('\n');
            }
        }
        paras.push(out);
    }
    Ok(paras.join("\n"))
}

fn odt(data: &[u8]) -> Result<String, String> {
    const T: &str = "urn:oasis:names:tc:opendocument:xmlns:text:1.0";
    const O: &str = "urn:oasis:names:tc:opendocument:xmlns:office:1.0";
    let xml = zip_member(data, "content.xml")?;
    let doc = roxmltree::Document::parse(&xml).map_err(|e| e.to_string())?;
    let is = |n: &roxmltree::Node, ns: &str, tag: &str| n.is_element() && n.tag_name().namespace() == Some(ns) && n.tag_name().name() == tag;
    fn inline(el: roxmltree::Node, out: &mut String) {
        for c in el.children() {
            if c.is_text() {
                out.push_str(c.text().unwrap_or(""));
                continue;
            }
            if !c.is_element() || c.tag_name().namespace() != Some(T) {
                if c.is_element() {
                    inline(c, out);
                }
                continue;
            }
            match c.tag_name().name() {
                "s" => out.push_str(&" ".repeat(c.attribute((T, "c")).and_then(|n| n.parse().ok()).unwrap_or(1))),
                "tab" => out.push('\t'),
                "line-break" => out.push('\n'),
                "note" => {}
                _ => inline(c, out),
            }
        }
    }
    let root = doc.root_element();
    let body = root.children().find(|c| is(c, O, "body")).unwrap_or(root);
    let mut paras = Vec::new();
    fn walk(el: roxmltree::Node, paras: &mut Vec<String>) {
        for c in el.children().filter(|c| c.is_element()) {
            if c.tag_name().namespace() == Some(T) && matches!(c.tag_name().name(), "p" | "h") {
                let mut out = String::new();
                inline(c, &mut out);
                paras.push(out);
            } else {
                walk(c, paras);
            }
        }
    }
    walk(body, &mut paras);
    Ok(paras.join("\n"))
}

const RTF_SKIP: &[&str] = &[
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "headerl", "headerr", "footerl", "footerr",
    "object", "themedata", "colorschememapping", "latentstyles", "datastore", "xmlnstbl", "listtable",
    "listoverridetable", "rsidtbl", "generator", "fldinst", "filetbl",
];

/// RTF -> text (the classic group-stack stripper: skips destinations, maps
/// \par / \tab / \'hh / \uN).
pub fn rtf(src: &str) -> String {
    static TOKEN: OnceLock<Regex> = OnceLock::new();
    let token = TOKEN.get_or_init(|| {
        Regex::new(r"(?is)\\([a-z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|(.)").unwrap()
    });
    let (mut stack, mut skip, mut ucskip, mut out, mut pending) = (Vec::new(), false, 1i64, String::new(), 0i64);
    for m in token.captures_iter(src) {
        let g = |i: usize| m.get(i).map(|x| x.as_str());
        if let Some(brace) = g(5) {
            if brace == "{" {
                stack.push((skip, ucskip));
            } else if let Some((s, u)) = stack.pop() {
                skip = s;
                ucskip = u;
            }
        } else if let Some(ch) = g(4) {
            if ch == "*" {
                skip = true;
            } else if !skip && "\\{}".contains(ch) {
                out.push_str(ch);
            } else if !skip && ch == "~" {
                out.push('\u{a0}');
            }
        } else if let Some(word) = g(1) {
            let arg = g(2);
            if RTF_SKIP.contains(&word) {
                skip = true;
            } else if skip {
            } else if ["par", "line", "sect", "page"].contains(&word) {
                out.push('\n');
            } else if word == "tab" {
                out.push('\t');
            } else if word == "uc" {
                ucskip = arg.and_then(|a| a.parse().ok()).unwrap_or(1);
            } else if word == "u" {
                let n: i64 = arg.and_then(|a| a.parse().ok()).unwrap_or(0);
                let n = if n < 0 { n + 65536 } else { n };
                out.push(char::from_u32(n as u32).unwrap_or('\u{FFFD}'));
                pending = ucskip;
            } else {
                let special = match word {
                    "emdash" => Some('—'),
                    "endash" => Some('–'),
                    "bullet" => Some('•'),
                    "lquote" => Some('‘'),
                    "rquote" => Some('’'),
                    "ldblquote" => Some('“'),
                    "rdblquote" => Some('”'),
                    _ => None,
                };
                if let Some(c) = special {
                    out.push(c);
                }
            }
        } else if let Some(hex) = g(3) {
            if pending > 0 {
                pending -= 1;
            } else if !skip {
                let b = u8::from_str_radix(hex, 16).unwrap_or(b'?');
                out.push(cp1252_char(b).unwrap_or('\u{FFFD}'));
            }
        } else if let Some(t) = g(6) {
            if pending > 0 {
                pending -= 1;
            } else if !skip {
                out.push_str(t);
            }
        }
    }
    format!("{}\n", out.trim_matches('\n'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    fn utf16(s: &str, big: bool) -> Vec<u8> {
        s.encode_utf16().flat_map(|u| if big { u.to_be_bytes() } else { u.to_le_bytes() }).collect()
    }

    fn cp1252(s: &str) -> Vec<u8> {
        encode(s, "windows-1252", false, "LF").unwrap()
    }

    #[test]
    fn encodings() {
        let d = decode("héllo\n".as_bytes()).unwrap();
        assert_eq!((d.text.as_str(), d.encoding.as_str(), d.bom, d.newline.as_str()), ("héllo\n", "utf-8", false, "LF"));
        let d = decode(b"\xef\xbb\xbfhi").unwrap();
        assert_eq!((d.text.as_str(), d.encoding.as_str(), d.bom), ("hi", "utf-8", true));
        let d = decode(&utf16("\u{feff}olá\r\n", false)).unwrap();
        assert_eq!((d.text.as_str(), d.encoding.as_str(), d.bom, d.newline.as_str()), ("olá\n", "utf-16-le", true, "CRLF"));
        let d = decode(&utf16("\u{feff}olá", true)).unwrap();
        assert_eq!((d.text.as_str(), d.encoding.as_str()), ("olá", "utf-16-be"));
        let d = decode(&utf16("plain utf16 without bom", false)).unwrap();
        assert_eq!((d.text.as_str(), d.encoding.as_str(), d.bom), ("plain utf16 without bom", "utf-16-le", false));
        let d = decode(&cp1252("café “quoted”")).unwrap();
        assert_eq!((d.text.as_str(), d.encoding.as_str()), ("café “quoted”", "windows-1252"));
        let d = decode(b"\x81\x8d caf\xe9").unwrap(); // not Windows-1252 either
        assert_eq!((d.text.as_str(), d.encoding.as_str()), ("\u{81}\u{8d} café", "iso-8859-1"));
        assert_eq!(decode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\xff\x13"), Err(BinaryFile));
    }

    #[test]
    fn newlines_round_trip() {
        assert_eq!(detect_newline("a\r\nb\r\nc\n"), "CRLF");
        assert_eq!(detect_newline("a\rb\r"), "CR");
        assert_eq!(detect_newline("no newline"), "LF");
        for (raw, enc) in [("one\r\ntwo\r\n", "utf-8"), ("uno\rdos\r", "windows-1252"), ("é\nè\n", "iso-8859-1")] {
            let data = encode(raw, enc, false, "LF").unwrap();
            let d = decode(&data).unwrap();
            assert!(!d.text.contains('\r'));
            assert_eq!(encode(&d.text, &d.encoding, d.bom, &d.newline).unwrap(), data, "{raw:?}");
        }
        let bom16 = utf16("\u{feff}x\r\n", false);
        let d = decode(&bom16).unwrap();
        assert_eq!(encode(&d.text, &d.encoding, d.bom, &d.newline).unwrap(), bom16);
        assert_eq!(encode("日本", "iso-8859-1", false, "LF"), Err(EncodeError));
        assert_eq!(encode("日本", "windows-1252", false, "LF"), Err(EncodeError));
    }

    fn zipped(member: &str, text: &str) -> Vec<u8> {
        let mut buf = std::io::Cursor::new(Vec::new());
        {
            let mut z = zip::ZipWriter::new(&mut buf);
            z.start_file(member, zip::write::SimpleFileOptions::default()).unwrap();
            z.write_all(text.as_bytes()).unwrap();
            z.finish().unwrap();
        }
        buf.into_inner()
    }

    #[test]
    fn converted() {
        let w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
        let data = zipped("word/document.xml", &format!(
            r#"<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:tab/><w:t>world</w:t></w:r></w:p><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>"#));
        assert_eq!(convert(&data, "a.docx").unwrap().unwrap(), "Hello\tworld\nSecond");
        let t = "urn:oasis:names:tc:opendocument:xmlns:text:1.0";
        let o = "urn:oasis:names:tc:opendocument:xmlns:office:1.0";
        let data = zipped("content.xml", &format!(
            r#"<office:document-content xmlns:office="{o}" xmlns:text="{t}"><office:body><office:text><text:h>Title</text:h><text:p>a<text:s text:c="2"/>b<text:span>c</text:span></text:p></office:text></office:body></office:document-content>"#));
        assert_eq!(convert(&data, "b.ODT").unwrap().unwrap(), "Title\na  bc");
        let rtf = br"{\rtf1\ansi{\fonttbl\f0 Helvetica;}\f0 Caf\'e9 \b bold\b0\par Next\tab line \u8364?\par}";
        assert_eq!(convert(rtf, "c.rtf").unwrap().unwrap(), "Café bold\nNext\tline €\n");
        assert_eq!(convert(b"x", "d.txt").unwrap(), None);
        assert!(convert(b"not a zip", "e.docx").is_err());
    }

    #[test]
    fn misc() {
        assert_eq!(words("Hello, world — it's  me"), 4);
        assert!(is_code("main.py") && is_code("Makefile") && is_code(".bashrc"));
        assert!(!(is_code("Letter.txt") || is_code("Notes.md")));
        assert!(is_converted("x.DOCX") && !is_converted("x.doc"));
    }

    #[test]
    fn labels() {
        sonata_i18n::use_language("en");
        assert_eq!(label("utf-8", true), "UTF-8 with BOM");
        assert_eq!(label("utf-16-le", true), "UTF-16 LE");
        assert_eq!(label("koi8-r", false), "KOI8-R");
    }
}
