"""Find & Replace logic for TextEdit (no GTK): matches as character offsets
(the same offsets GtkTextIter uses), literal or regular expression, with or
without case.

    spans = find_all(text, "sonata", case=False)          # [(start, end), ...]
    new, n = replace_all(text, r"(\\w+)@", r"\\1 at ", regex=True)
"""
import re

MAX_MATCHES = 20000          # highlighting stops there (huge files, one-letter searches)


def compile_pattern(needle: str, case: bool = False, regex: bool = False, whole: bool = False):
    """A compiled pattern (re.error for an invalid regular expression)."""
    src = needle if regex else re.escape(needle)
    if whole:
        src = r"\b(?:" + src + r")\b"
    return re.compile(src, 0 if case else re.IGNORECASE | re.UNICODE)


def find_all(text: str, needle: str, case: bool = False, regex: bool = False, whole: bool = False,
             limit: int = MAX_MATCHES) -> list:
    if not needle:
        return []
    pat = compile_pattern(needle, case, regex, whole)
    out = []
    for m in pat.finditer(text):
        if m.end() == m.start():            # empty regex matches (e.g. "^") are skipped
            continue
        out.append((m.start(), m.end()))
        if len(out) >= limit:
            break
    return out


def expand(text: str, span, needle: str, repl: str, case: bool = False, regex: bool = False,
           whole: bool = False) -> str:
    """The replacement for the match at span (regex: \\1 / \\g<name> groups)."""
    if not regex:
        return repl
    pat = compile_pattern(needle, case, regex, whole)
    m = pat.match(text, span[0])
    if m is None or m.end() != span[1]:
        m = pat.fullmatch(text, span[0], span[1])
    return m.expand(repl) if m else repl


def replace_all(text: str, needle: str, repl: str, case: bool = False, regex: bool = False,
                whole: bool = False):
    """(new text, number of replacements)."""
    if not needle:
        return text, 0
    pat = compile_pattern(needle, case, regex, whole)
    if not regex:
        repl = repl.replace("\\", "\\\\")
    return pat.subn(repl, text)


def next_span(spans, offset: int, backwards: bool = False, include_current: bool = False):
    """The match after (or before) a character offset, wrapping around."""
    if not spans:
        return None
    if backwards:
        before = [s for s in spans if s[0] < offset]
        return before[-1] if before else spans[-1]
    after = [s for s in spans if (s[0] >= offset if include_current else s[0] > offset)]
    return after[0] if after else spans[0]
