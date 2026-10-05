#!/bin/bash
# Which effect makes closing apps stutter? Turn one off for this session
# only (a new login brings everything back), close a few apps, compare.
#   bash tools/lagtest.sh noblur     # no frosted glass
#   bash tools/lagtest.sh noanim     # windows open / close without the zoom
#   bash tools/lagtest.sh restore    # everything back as it was
cd "$(dirname "$0")/.." || exit 1
mode="${1:-}"
case "$mode" in noblur|noanim|restore) ;; *) sed -n 2,7p "$0"; exit 1;; esac
python3 - "$mode" <<'PY'
import json, os, sys
from sonata2 import wfconfig as W
mode = sys.argv[1]
KEYS = (("core", "plugins"), ("animate", "open_animation"), ("animate", "close_animation"))
keep = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-lagtest.json")
if not os.path.exists(keep):                     # the values before any test: what restore puts back
    with open(keep, "w") as f:
        json.dump({f"{s}/{k}": W.wayfire_get(s, k) for s, k in KEYS}, f)
with open(keep) as f:
    orig = json.load(f)
if mode == "noblur":
    plugins = orig["core/plugins"].split()
    W.runtime_set("core", "plugins", " ".join(p for p in plugins if p != "blur"))
elif mode == "noanim":
    W.runtime_set("animate", "open_animation", "none")
    W.runtime_set("animate", "close_animation", "none")
else:
    for s, k in KEYS:
        if orig[f"{s}/{k}"]:
            W.runtime_set(s, k, orig[f"{s}/{k}"])
    os.unlink(keep)
print(mode, "applied")
PY
