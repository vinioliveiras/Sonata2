#!/usr/bin/env python3
"""The strings marked for translation in some Rust files (tr!, trn!, trp!,
n_!, nc_!) that a catalog lacks, or whose {placeholders} differ, per
language -- for one area at a time. Standalone (Python's standard library
only); the whole tree is checked by `cargo test -p sonata-i18n`
(rust/crates/sonata-i18n/tests/every_string_translated.rs).

    python3 tools/i18n_missing.py rust/apps/files/        # files or folders
"""
import json
import os
import re
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALE = os.path.join(ROOT, "locale")
LANGS = ("pt_BR", "pt_PT")
SEP = "\x04"
_S = r'"((?:[^"\\]|\\.)*)"'
ONE = re.compile(r"\b(?:tr|n_)!\(\s*" + _S + r"\s*\)")
PLURAL = re.compile(r"\btrn!\(\s*" + _S + r"\s*,\s*" + _S)
CONTEXT = re.compile(r"\b(?:trp|nc_)!\(\s*" + _S + r"\s*,\s*" + _S + r"\s*\)")
ANY = re.compile(r"\b(tr|trn|trp|n_|nc_)!\(")


def unescape(text):
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            n = text[i + 1]
            if n == "u" and text[i + 2:i + 3] == "{":
                end = text.index("}", i)
                out.append(chr(int(text[i + 3:end], 16)))
                i = end + 1
                continue
            out.append({"n": "\n", "t": "\t", "r": "\r", "0": "\0"}.get(n, n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def rust_files(paths):
    for p in paths:
        if os.path.isfile(p):
            yield p
            continue
        for dp, dn, fns in os.walk(p):
            dn[:] = [d for d in dn if d != "target"]
            for fn in sorted(fns):
                if fn.endswith(".rs"):
                    yield os.path.join(dp, fn)


def marked(paths):
    found, bad = {}, []
    for path in rust_files(paths):
        with open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
        for no, line in enumerate(lines, 1):
            code = "" if line.lstrip().startswith("//") else line.split("//")[0]
            if "macro_rules!" in code or not ANY.search(code):
                continue
            where = f"{os.path.relpath(path, ROOT)}:{no}"
            got = 0
            for m in ONE.finditer(code):
                got += 1
                found.setdefault((unescape(m.group(1)), None), []).append(where)
            for m in PLURAL.finditer(code):
                got += 1
                found.setdefault((unescape(m.group(1)), unescape(m.group(2))), []).append(where)
            for m in CONTEXT.finditer(code):
                got += 1
                found.setdefault((unescape(m.group(1)) + SEP + unescape(m.group(2)), None), []).append(where)
            if got < len(ANY.findall(code)):
                bad.append((where, "a translation macro needs literal strings on one line"))
    return found, bad


def catalog(lang):
    out = {}
    folder = os.path.join(LOCALE, lang)
    for n in sorted(os.listdir(folder)):
        if n.endswith(".json"):
            with open(os.path.join(folder, n), encoding="utf-8") as f:
                out.update(json.load(f))
    return out


def fields(text):
    try:
        return sorted({f for _l, f, _s, _c in string.Formatter().parse(text) if f is not None})
    except ValueError:
        return None


def main(paths) -> int:
    found, bad = marked(paths)
    problems = 0
    for where, why in bad:
        print(f"BAD {where}: {why}")
        problems += 1
    for lang in LANGS:
        cat = catalog(lang)
        for (s, p), ws in sorted(found.items()):
            t = cat.get(s)
            if not t:
                print(f"MISSING {lang} {ws[0]}: {s!r}" + (f" / {p!r}" if p else ""))
                problems += 1
            elif p is not None and not (isinstance(t, list) and len(t) == 2):
                print(f"PLURAL {lang} {ws[0]}: {s!r} needs [one, other]")
                problems += 1
            elif p is None and not isinstance(t, str):
                print(f"NOT-A-STRING {lang} {ws[0]}: {s!r}")
                problems += 1
            else:
                en = s.split(SEP)[-1]
                pairs = [(t[0], en), (t[1], p)] if p is not None else [(t, en)]
                for tr, orig in pairs:
                    if fields(tr) != fields(orig):
                        print(f"PLACEHOLDERS {lang} {ws[0]}: {orig!r} -> {tr!r}")
                        problems += 1
    print(f"{len(found)} strings marked here; {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or [os.path.join(ROOT, "rust")]))
