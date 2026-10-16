#!/usr/bin/env python3
"""The strings marked for translation in some files that a catalog lacks
(or whose {placeholders} differ), per language -- for one area at a time.

    python3 tools/i18n_missing.py sonata2/files/          # files or folders
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_i18n import fields, marked  # noqa: E402

from sonata2 import i18n  # noqa: E402


def main(paths) -> int:
    paths = [os.path.relpath(os.path.abspath(p), os.path.join(os.path.dirname(i18n.__file__))) for p in paths]
    found, bad = marked()
    mine = {k: [w for w in ws if any(w.startswith(p.rstrip("/")) for p in paths)] for k, ws in found.items()}
    mine = {k: ws for k, ws in mine.items() if ws}
    problems = 0
    for where, why in bad:
        if any(where.startswith(p.rstrip("/")) for p in paths):
            print(f"BAD {where}: {why}")
            problems += 1
    for lang in i18n.SUPPORTED:
        cat = i18n._load(lang)
        for (s, p), ws in sorted(mine.items()):
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
                pairs = [(t[0], s), (t[1], p)] if p is not None else [(t, s)]
                for tr, en in pairs:
                    if fields(tr) != fields(en):
                        print(f"PLACEHOLDERS {lang} {ws[0]}: {en!r} -> {tr!r}")
                        problems += 1
    print(f"{len(mine)} strings marked here; {problems} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["sonata2"]))
