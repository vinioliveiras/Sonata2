#!/usr/bin/env python3
"""Write Sonata's design tokens to rust/crates/sonata-ui/tokens.json, for the
Rust port (docs/RUST_PORT.md). sonata2/ui/tokens.py stays the one place a
value is changed; run this after changing it (tests.test_rust_tokens fails
while the JSON is stale).

    python3 tools/export_tokens.py            # write
    python3 tools/export_tokens.py --check    # exit 1 when stale

Only tokens.py is loaded (plain Python, no GTK), with an empty config
folder: the theme's own values, never one user's appearance.json."""
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "rust", "crates", "sonata-ui", "tokens.json")


def _tokens():
    spec = importlib.util.spec_from_file_location("sonata_tokens", os.path.join(ROOT, "sonata2", "ui", "tokens.py"))
    t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t)
    return t


def export() -> str:
    with tempfile.TemporaryDirectory() as empty:
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = empty           # no user's radii / backgrounds baked in
        try:
            t = _tokens()
            data = {
                "light": t.palette(False, "mac"),
                "dark": t.palette(True, "mac"),
                "accents": {name: {"light": t.accent_tokens(name, False), "dark": t.accent_tokens(name, True)}
                            for name in t.ACCENTS},
                "animation_speed": t.ANIMATION_SPEED,
            }
        finally:
            if old is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv) -> int:
    text = export()
    if "--check" in argv:
        try:
            with open(OUT, encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        if not same:
            print("rust/crates/sonata-ui/tokens.json is stale: run tools/export_tokens.py")
        return 0 if same else 1
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
