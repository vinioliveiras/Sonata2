"""TextEdit's file formats (no GTK): reading any text-like file with its
encoding and line endings, writing it back the same way, and a plain-text
extraction of word-processor files (.docx, .odt, .rtf) opened read-only.

    d = decode(data)            # Decoded(text, encoding, bom, newline); text uses "\\n"
    data = encode(text, d.encoding, d.bom, d.newline)
    text = convert(data, name)  # .docx / .odt / .rtf -> plain text (None: not one of them)
"""
import codecs
import io
import re
import zipfile
from typing import NamedTuple

# encoding id (Python codec name) -> label shown in the status bar menu
ENCODINGS = {
    "utf-8": "Unicode (UTF-8)",
    "utf-16-le": "Unicode (UTF-16 LE)",
    "utf-16-be": "Unicode (UTF-16 BE)",
    "windows-1252": "Western (Windows Latin 1)",
    "iso-8859-1": "Western (ISO Latin 1)",
}
SHORT = {"utf-8": "UTF-8", "utf-16-le": "UTF-16 LE", "utf-16-be": "UTF-16 BE",
         "windows-1252": "Windows-1252", "iso-8859-1": "ISO-8859-1"}
NEWLINES = {"LF": "\n", "CRLF": "\r\n", "CR": "\r"}
NEWLINE_LABELS = {"LF": "Linux / Unix (LF)", "CRLF": "Windows (CRLF)", "CR": "Old style (CR)"}
_BOMS = ((codecs.BOM_UTF8, "utf-8"), (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be"))

CONVERTED = (".docx", ".odt", ".rtf")
# extensions shown in a monospaced face when the font is "Automatic"
CODE_EXTS = {
    "c", "h", "cc", "cpp", "cxx", "hpp", "hh", "cs", "java", "kt", "kts", "go", "rs", "swift", "m", "mm",
    "py", "pyw", "pyi", "rb", "pl", "pm", "php", "lua", "r", "jl", "dart", "scala", "clj", "hs", "ml",
    "js", "mjs", "cjs", "jsx", "ts", "tsx", "vue", "svelte", "css", "scss", "sass", "less",
    "html", "htm", "xml", "xhtml", "svg", "json", "jsonc", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd", "mk", "cmake", "gradle", "sql", "diff", "patch",
    "desktop", "service", "gitignore", "dockerfile", "nix", "zig", "v", "sv", "vhd", "asm", "s", "log", "csv",
    "tsv", "env", "properties", "gd", "glsl", "frag", "vert", "hlsl", "tex",
}
CODE_NAMES = {"makefile", "dockerfile", "cmakelists.txt", "pkgbuild", "meson.build", "justfile", "gemfile"}


class BinaryFile(ValueError):
    """The data isn't text (NUL bytes that aren't UTF-16)."""


class Decoded(NamedTuple):
    text: str
    encoding: str = "utf-8"
    bom: bool = False
    newline: str = "LF"


def detect_newline(text: str) -> str:
    """The line ending used most: "LF", "CRLF" or "CR" (LF when none)."""
    crlf = text.count("\r\n")
    cr = text.count("\r") - crlf
    lf = text.count("\n") - crlf
    best = max((lf, "LF"), (crlf, "CRLF"), (cr, "CR"), key=lambda p: p[0])
    return best[1] if best[0] else "LF"


def normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n") if "\r" in text else text


def _utf16_guess(data: bytes):
    """UTF-16 without a BOM: one byte of each pair is mostly zero (ASCII-heavy text)."""
    sample = data[:4096]
    pairs = len(sample) // 2
    if pairs < 2:
        return None
    even = sum(1 for i in range(0, pairs * 2, 2) if sample[i] == 0)
    odd = sum(1 for i in range(1, pairs * 2, 2) if sample[i] == 0)
    enc = "utf-16-le" if odd > pairs * 0.3 and even < pairs * 0.05 else \
        "utf-16-be" if even > pairs * 0.3 and odd < pairs * 0.05 else None
    if enc is None:
        return None
    text = sample[:pairs * 2].decode(enc, errors="replace")
    bad = sum(1 for ch in text if (ch < " " and ch not in "\t\n\r\f") or ch == "\ufffd")
    return enc if bad <= len(text) * 0.02 else None


def decode(data: bytes) -> Decoded:
    """Text of a file: BOM (UTF-8 / UTF-16) first, then UTF-8, then Windows
    Latin 1 and ISO Latin 1 (which decodes anything). Raises BinaryFile."""
    data = bytes(data)
    enc, bom = None, False
    for mark, name in _BOMS:
        if data.startswith(mark):
            enc, bom, data = name, True, data[len(mark):]
            break
    if enc is None and b"\0" in data:      # anywhere: a NUL past the sniff window is binary too
        enc = _utf16_guess(data)
        if enc is None:
            raise BinaryFile("binary data")
    if enc is not None:
        text = data.decode(enc, errors="replace")
    else:
        for enc in ("utf-8", "windows-1252", "iso-8859-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    if "\0" in text:
        # GtkTextBuffer stops at NUL and a replacement char would be saved
        # in its place: refuse rather than change the file's bytes
        raise BinaryFile("NUL characters")
    nl = detect_newline(text)
    return Decoded(normalize(text), enc, bom, nl)


def encode(text: str, encoding: str = "utf-8", bom: bool = False, newline: str = "LF") -> bytes:
    """Bytes to write: the buffer's "\\n" turned back into the document's line
    ending, in its encoding (UnicodeEncodeError when a character doesn't fit)."""
    nl = NEWLINES.get(newline, "\n")
    if nl != "\n":
        text = text.replace("\n", nl)
    data = text.encode(encoding)
    if bom:
        data = {"utf-8": codecs.BOM_UTF8, "utf-16-le": codecs.BOM_UTF16_LE,
                "utf-16-be": codecs.BOM_UTF16_BE}.get(encoding, b"") + data
    return data


def label(encoding: str, bom: bool = False) -> str:
    return SHORT.get(encoding, encoding.upper()) + (" with BOM" if bom and encoding == "utf-8" else "")


# -- statistics ------------------------------------------------------------------------------
_WORD = re.compile(r"\w+(?:['’]\w+)*")


def words(text: str) -> int:
    return sum(1 for _ in _WORD.finditer(text))


def is_code(name: str) -> bool:
    """A source/config file (shown monospaced when the font is Automatic)."""
    if not name:
        return False
    low = name.lower()
    if low in CODE_NAMES:
        return True
    ext = low.rsplit(".", 1)[-1] if "." in low else ""
    return ext in CODE_EXTS or low.startswith(".") and low.lstrip(".") in CODE_EXTS | {"bashrc", "zshrc", "profile"}


def is_converted(name: str) -> bool:
    return (name or "").lower().endswith(CONVERTED)


# -- word-processor files (read-only, converted) ---------------------------------------------
def convert(data: bytes, name: str):
    """Plain text of a .docx / .odt / .rtf document, None for other files
    (ValueError when the file is damaged)."""
    low = (name or "").lower()
    try:
        if low.endswith(".docx"):
            return _docx(data)
        if low.endswith(".odt"):
            return _odt(data)
        if low.endswith(".rtf"):
            return _rtf(bytes(data).decode("latin-1"))
    except (zipfile.BadZipFile, KeyError, SyntaxError) as e:     # ElementTree's ParseError is a SyntaxError
        raise ValueError(str(e)) from e
    return None


def _xml(data: bytes, member: str):
    import xml.etree.ElementTree as ET
    with zipfile.ZipFile(io.BytesIO(bytes(data))) as z:
        return ET.fromstring(z.read(member))


def _docx(data: bytes) -> str:
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = _xml(data, "word/document.xml")
    paras = []
    for p in root.iter(w + "p"):
        out = []
        for el in p.iter():
            if el.tag == w + "t":
                out.append(el.text or "")
            elif el.tag == w + "tab":
                out.append("\t")
            elif el.tag in (w + "br", w + "cr"):
                out.append("\n")
        paras.append("".join(out))
    return "\n".join(paras)


def _odt(data: bytes) -> str:
    t = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    root = _xml(data, "content.xml")
    body = root.find("{urn:oasis:names:tc:opendocument:xmlns:office:1.0}body")
    paras = []

    def inline(el, out):
        if el.text:
            out.append(el.text)
        for c in el:
            if c.tag == t + "s":
                out.append(" " * int(c.get(t + "c", "1")))
            elif c.tag == t + "tab":
                out.append("\t")
            elif c.tag == t + "line-break":
                out.append("\n")
            elif c.tag not in (t + "note",):
                inline(c, out)
            if c.tail:
                out.append(c.tail)

    def walk(el):
        for c in el:
            if c.tag in (t + "p", t + "h"):
                out = []
                inline(c, out)
                paras.append("".join(out))
            else:
                walk(c)
    walk(body if body is not None else root)
    return "\n".join(paras)


_RTF_TOKEN = re.compile(r"\\([a-z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-f]{2})|\\([^a-z])|([{}])|[\r\n]+|(.)", re.I | re.S)
_RTF_SKIP = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "headerl", "headerr",
             "footerl", "footerr", "object", "themedata", "colorschememapping", "latentstyles", "datastore",
             "xmlnstbl", "listtable", "listoverridetable", "rsidtbl", "generator", "fldinst", "filetbl"}


def _rtf(src: str) -> str:
    """RTF -> text (the classic group-stack stripper: skips destinations,
    maps \\par / \\tab / \\'hh / \\uN)."""
    stack, skip, ucskip, out, pending = [], False, 1, [], 0
    for word, arg, hexc, char, brace, tchar in _RTF_TOKEN.findall(src):
        if brace == "{":
            stack.append((skip, ucskip))
        elif brace == "}":
            if stack:
                skip, ucskip = stack.pop()
        elif char:
            if char == "*":
                skip = True
            elif not skip and char in "\\{}":
                out.append(char)
            elif not skip and char == "~":
                out.append("\u00a0")
        elif word:
            if word in _RTF_SKIP:
                skip = True
            elif skip:
                pass
            elif word in ("par", "line", "sect", "page"):
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            elif word == "uc":
                ucskip = int(arg or 1)
            elif word == "u":
                n = int(arg)
                out.append(chr(n + 65536 if n < 0 else n))
                pending = ucskip
            elif word in ("emdash", "endash", "bullet", "lquote", "rquote", "ldblquote", "rdblquote"):
                out.append({"emdash": "—", "endash": "–", "bullet": "•", "lquote": "‘", "rquote": "’",
                            "ldblquote": "“", "rdblquote": "”"}[word])
        elif hexc:
            if pending:
                pending -= 1
            elif not skip:
                out.append(bytes([int(hexc, 16)]).decode("windows-1252", errors="replace"))
        elif tchar:
            if pending:
                pending -= 1
            elif not skip:
                out.append(tchar)
    return "".join(out).strip("\n") + "\n"
