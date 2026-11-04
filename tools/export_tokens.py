#!/usr/bin/env python3
"""Write Sonata's design tokens to rust/crates/sonata-ui/tokens.json, and the
CSS templates of the parts already ported to rust/crates/sonata-ui/css.json,
for the Rust port (docs/RUST_PORT.md). The Python files stay the one place
a value or a style is changed; run this after changing them
(tests.test_rust_tokens fails while either JSON is stale).

    python3 tools/export_tokens.py            # write
    python3 tools/export_tokens.py --check    # exit 1 when stale

Only tokens.py is loaded (plain Python, no GTK), with an empty config
folder: the theme's own values, never one user's appearance.json."""
import ast
import importlib.util
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "rust", "crates", "sonata-ui", "tokens.json")
CSS_OUT = os.path.join(ROOT, "rust", "crates", "sonata-ui", "css.json")
# the modules whose CSS the Rust code registers (sonata_ui::css::register_module)
CSS_MODULES = ("ui/controls.py", "ui/menu.py", "ui/window.py", "calculator/window.py", "clock/window.py",
               "clock/pages.py")


def _registered(src: str, mod: str) -> list:
    """`ui.register("...")` / `theme.register("...", key="...")` calls at the
    module's top level, in order. A template with placeholders of its own
    (`title_h=...`) is filled the same way, from literal values only."""
    out = []
    for node in ast.parse(src).body:
        call = node.value if isinstance(node, ast.Expr) else None
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "register" and isinstance(call.func.value, ast.Name)
                and call.func.value.id in ("ui", "theme") and call.args
                and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
            continue
        key, local = None, {}
        for kw in call.keywords:
            try:
                value = ast.literal_eval(kw.value)
            except ValueError:
                value = None                    # computed (f"{TITLEBAR_H}px"): left to the token
            if kw.arg == "key":
                key = value
            elif value is not None:
                local[kw.arg] = str(value)
        out.append({"key": key or f"{mod}#{len(out)}", "css": call.args[0].value, "local": local})
    return out


def export_css() -> str:
    out = {}
    for mod in CSS_MODULES:
        with open(os.path.join(ROOT, "sonata2", mod), encoding="utf-8") as f:
            out[mod] = _registered(f.read(), mod)
    return json.dumps(out, indent=1, ensure_ascii=False) + "\n"


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
                # the window frame's numbers (title_h...), for templates that take them
                "frame": {k: v for k, v in t.FRAME.items() if isinstance(v, (int, float, str))},
            }
        finally:
            if old is None:
                os.environ.pop("XDG_CONFIG_HOME", None)
            else:
                os.environ["XDG_CONFIG_HOME"] = old
    return json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv) -> int:
    outputs = ((OUT, export()), (CSS_OUT, export_css()))
    if "--check" in argv:
        stale = []
        for path, text in outputs:
            try:
                with open(path, encoding="utf-8") as f:
                    if f.read() == text:
                        continue
            except OSError:
                pass
            stale.append(os.path.relpath(path, ROOT))
        if stale:
            print(f"{', '.join(stale)} stale: run tools/export_tokens.py")
        return 1 if stale else 0
    for path, text in outputs:
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
