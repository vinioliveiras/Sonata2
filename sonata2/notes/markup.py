"""The Markdown-ish text a note is stored as (pure Python, no GTK).

One line per paragraph; a line's prefix is its style, inline markers its
character formatting:

    # Title            ## Heading         ### Subheading
    - bullet           1. numbered        - [ ] / - [x] checklist
    **bold**  *italic*  __underline__  ~~strikethrough~~

A backslash escapes the next character (the serializer escapes every
literal \\ * _ ~ and a body line that would read as a prefix), so any text
round-trips. A parsed line is (kind, checked, runs), runs being
[(text, frozenset of inline tags)]."""
import re

KINDS = ("title", "heading", "subheading", "body", "bullet", "number", "check")
LISTS = ("bullet", "number", "check")
INLINE = ("bold", "italic", "underline", "strike")
MARK = {"bold": "**", "italic": "*", "underline": "__", "strike": "~~"}
_TOKENS = (("**", "bold"), ("__", "underline"), ("~~", "strike"), ("*", "italic"))
_PREFIX = (("### ", "subheading"), ("## ", "heading"), ("# ", "title"), ("- [ ] ", "check"),
           ("- [x] ", "check"), ("- [X] ", "check"), ("- ", "bullet"))
_NUMBER = re.compile(r"^\d+\. ")
_LOOKS_LIKE_PREFIX = re.compile(r"^(#|- |\d+\. )")
_ESCAPE = re.compile(r"([\\*_~])")
_CHECKBOX = re.compile(r"^\[[ xX]\] ")     # a bullet's text that would reload as a checklist


def parse_inline(s: str) -> list:
    runs, cur, tags, i = [], [], set(), 0

    def flush():
        if cur:
            text, fs = "".join(cur), frozenset(tags)
            if runs and runs[-1][1] == fs:
                runs[-1] = (runs[-1][0] + text, fs)
            else:
                runs.append((text, fs))
            cur.clear()
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            cur.append(s[i + 1])
            i += 2
            continue
        for tok, tag in _TOKENS:
            if s.startswith(tok, i):
                flush()
                tags ^= {tag}
                i += len(tok)
                break
        else:
            cur.append(c)
            i += 1
    flush()
    return runs


def parse_line(line: str) -> tuple:
    for prefix, kind in _PREFIX:
        if line.startswith(prefix):
            return kind, prefix in ("- [x] ", "- [X] "), parse_inline(line[len(prefix):])
    m = _NUMBER.match(line)
    if m:
        return "number", False, parse_inline(line[m.end():])
    return "body", False, parse_inline(line)


def parse(md: str) -> list:
    return [parse_line(line) for line in (md or "").split("\n")]


def serialize_runs(runs) -> str:
    out, open_tags = [], []
    for text, tags in runs:
        if not text:
            continue
        for t in reversed(open_tags):             # close what this run doesn't have
            if t not in tags:
                out.append(MARK[t])
        open_tags = [t for t in open_tags if t in tags]
        for t in INLINE:
            if t in tags and t not in open_tags:
                out.append(MARK[t])
                open_tags.append(t)
        out.append(_ESCAPE.sub(r"\\\1", text))
    out.extend(MARK[t] for t in reversed(open_tags))
    return "".join(out)


def serialize_line(kind: str, checked: bool, runs) -> str:
    body = serialize_runs(runs)
    if kind == "body":
        return ("\\" + body) if _LOOKS_LIKE_PREFIX.match(body) else body
    if kind == "bullet" and _CHECKBOX.match(body):
        body = "\\" + body                       # written "- \[ ] x": stays a bullet
    prefix = {"title": "# ", "heading": "## ", "subheading": "### ", "bullet": "- ", "number": "1. ",
              "check": "- [x] " if checked else "- [ ] "}[kind]
    return prefix + body


def serialize(lines) -> str:
    return "\n".join(serialize_line(*ln) for ln in lines)


def plain(md: str) -> list:
    """The note's lines as plain text (no markers)."""
    return ["".join(t for t, _ in runs) for _k, _c, runs in parse(md)]


def title_and_preview(md: str) -> tuple:
    """The first non-empty line (the note's title) and the next one."""
    lines = [s.strip() for s in plain(md) if s.strip()]
    return (lines[0] if lines else "New Note"), (lines[1] if len(lines) > 1 else "No additional text")
