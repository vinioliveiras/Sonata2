"""Sonata title bars for every app (Settings > Appearance, on by default).

Wayfire asks apps for server-side title bars (wayfire.ini
preferred_decoration_mode = server), drawn by pixdecor like the rest of
Sonata. Apps that draw their own frame unless told otherwise are told,
at every login (`sonata2 autostart`):

- Chromium browsers (Chrome, Chromium, Brave, Edge, Vivaldi): the
  per-profile "Use system title bar and borders" preference. Skipped while
  the browser runs (it would write its own value back on quit): next login.
- VS Code / VSCodium / Code - OSS: "window.titleBarStyle": "native".

Turning the option off puts both back. Apps with a frame of their own
and no such setting (Spotify, Claude, Discord...) keep drawing theirs."""
import json
import os
import re

from gi.repository import GLib

from . import config

BROWSERS = {                      # config folder -> process names
    "google-chrome": ("chrome",), "google-chrome-beta": ("chrome",), "google-chrome-unstable": ("chrome",),
    "chromium": ("chromium",), "BraveSoftware/Brave-Browser": ("brave",),
    "microsoft-edge": ("msedge",), "vivaldi": ("vivaldi-bin", "vivaldi"),
}
CODE = ("Code", "Code - OSS", "VSCodium", "Code - Insiders")


def enabled() -> bool:
    from .icons import APPEARANCE_DEFAULTS
    return bool(config.load("appearance", APPEARANCE_DEFAULTS).get("system_titlebars", True))


def apply(on: bool = None) -> None:
    on = enabled() if on is None else on
    running = _running()
    cfg = GLib.get_user_config_dir()
    for folder, procs in BROWSERS.items():
        if running & set(procs):
            continue
        base = os.path.join(cfg, folder)
        try:
            profiles = [p for p in os.listdir(base) if p == "Default" or p.startswith("Profile ")]
        except OSError:
            continue
        for p in profiles:
            _browser_pref(os.path.join(base, p, "Preferences"), not on)
    for app in CODE:
        path = os.path.join(cfg, app, "User", "settings.json")
        if os.path.isdir(os.path.join(cfg, app)):
            _code_setting(path, "native" if on else "custom")


def _running() -> set:
    names = set()
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
                    names.add(f.read().strip())
            except OSError:
                pass
    return names


def _browser_pref(path: str, custom_frame: bool) -> None:
    try:
        with open(path, encoding="utf-8") as f:
            prefs = json.load(f)
    except (OSError, ValueError):
        return
    browser = prefs.setdefault("browser", {})
    if browser.get("custom_chrome_frame") == custom_frame:
        return
    browser["custom_chrome_frame"] = custom_frame
    _write(path, json.dumps(prefs, separators=(",", ":")))


def _code_setting(path: str, style: str) -> None:
    """VS Code's settings.json allows comments (JSONC): edit the text."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = "{\n}\n"
    key = re.compile(r'("window\.titleBarStyle"\s*:\s*)"[^"]*"')
    if key.search(text):
        new = key.sub(rf'\1"{style}"', text, count=1)
    else:
        brace = text.find("{")
        if brace < 0:
            return
        rest = text[brace + 1:]
        comma = "," if rest.strip() and not rest.strip().startswith("}") else ""
        new = text[:brace + 1] + f'\n    "window.titleBarStyle": "{style}"{comma}' + rest
    if new != text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        _write(path, new)


def _write(path: str, text: str) -> None:
    tmp = path + ".sonata-tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        pass
