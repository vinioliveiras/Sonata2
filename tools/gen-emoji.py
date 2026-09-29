#!/usr/bin/env python3
"""Build sonata2/data/emoji.tsv (group, emoji, name) for the Character
Viewer from Unicode's emoji-test.txt: fully-qualified, no skin-tone
variants, up to Emoji 14.0 (what common emoji fonts draw).
  tools/gen-emoji.py path/to/emoji-test.txt"""
import os
import re
import sys

SKIN = re.compile("[\U0001F3FB-\U0001F3FF]")
OUT = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "emoji.tsv")
group, rows = "", []
for line in open(sys.argv[1], encoding="utf-8"):
    if line.startswith("# group:"):
        group = line.split(":", 1)[1].strip()
    m = re.match(r"^[0-9A-F ]+;\s*fully-qualified\s*#\s*(\S+)\s+E(\d+)\.\d+\s+(.*)$", line)
    if not m or group == "Component" or SKIN.search(m.group(1)) or int(m.group(2)) > 14:
        continue
    rows.append(f"{group}\t{m.group(1)}\t{m.group(3)}")
with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(rows) + "\n")
print(len(rows), "emoji ->", os.path.normpath(OUT))
