"""Sonata title bars for every app (Settings > Appearance, on by default).

Wayfire asks apps for server-side title bars (wayfire.ini
preferred_decoration_mode = server), drawn by pixdecor like the rest of
Sonata. Apps that draw their own frame unless told otherwise are told,
at every login (`sonata2 autostart`):

- Every Chromium-based browser (Chrome, Chromium, Brave, Edge, Vivaldi,
  Opera, Thorium..., Flatpak ones too), found by its profile folder: the
  "Use system title bar and borders" preference. Skipped while the browser
  runs (it would write its own value back on quit): next login.
- Electron apps with a normal frame get Sonata's title bar by themselves
  (Wayfire prefers server-side decorations); the ones that draw their own
  frame and have no setting for it (Spotify, Discord...) keep theirs.
- VS Code / VSCodium / Code - OSS: "window.titleBarStyle": "native".
- Firefox, Thunderbird, LibreWolf, Floorp (every profile): user.js
  browser.tabs.inTitlebar = 0 (tabs under the system title bar).
- Claude Desktop (claude-desktop-bin): CLAUDE_NATIVE_TITLEBAR=1, set by
  tools/session-env.sh at login.
- Vesktop (the open Discord client, Flatpak too): settings.json
  "titleBar": "system". The official Discord app has no such setting (only
  patching its core.asar, undone by every update): use Vesktop for it.

Turning the option off puts both back. Apps with a frame of their own
and no such setting (Spotify, Claude, Discord...) keep drawing theirs."""
import json
import os
import re

from gi.repository import GLib

from . import config

VESKTOP = ("~/.config/vesktop/settings.json", "~/.var/app/dev.vencord.Vesktop/config/vesktop/settings.json")
CODE = ("Code", "Code - OSS", "VSCodium", "Code - Insiders")
MOZILLA = ("~/.mozilla/firefox", "~/.config/mozilla/firefox", "~/.thunderbird", "~/.librewolf", "~/.floorp",
           "~/.var/app/org.mozilla.firefox/.mozilla/firefox")
MOZ_LINE = 'user_pref("browser.tabs.inTitlebar", 0);  // Sonata title bars (Settings > Appearance)'
# always: Firefox's Open/Save dialogs through the portal (Sonata's panels)
MOZ_PORTAL = 'user_pref("widget.use-xdg-desktop-portal.file-picker", 1);  // Sonata Open/Save panels'


def enabled() -> bool:
    from .icons import APPEARANCE_DEFAULTS
    return bool(config.load("appearance", APPEARANCE_DEFAULTS).get("system_titlebars", True))


def apply(on: bool = None) -> None:
    on = enabled() if on is None else on
    cfg = GLib.get_user_config_dir()
    for base in chromium_roots(cfg):
        if os.path.lexists(os.path.join(base, "SingletonLock")):
            continue                         # running: it would write its own value back on quit
        try:
            profiles = [p for p in os.listdir(base) if p == "Default" or p.startswith("Profile ")]
        except OSError:
            continue
        for p in profiles:
            _browser_pref(os.path.join(base, p, "Preferences"), not on)
    for base in MOZILLA:
        base = os.path.expanduser(base)
        try:
            profiles = [os.path.join(base, p) for p in os.listdir(base)]
        except OSError:
            continue
        for prof in profiles:
            if os.path.isfile(os.path.join(prof, "prefs.js")):
                _mozilla_userjs(os.path.join(prof, "user.js"), on)
    for app in CODE:
        path = os.path.join(cfg, app, "User", "settings.json")
        if os.path.isdir(os.path.join(cfg, app)):
            _code_setting(path, "native" if on else "custom")
    for path in VESKTOP:
        _vesktop_setting(os.path.expanduser(path), on)


def chromium_roots(cfg: str) -> list:
    """Every Chromium-based browser's data folder -- Chrome, Chromium, Brave,
    Edge, Vivaldi, Opera, Thorium... -- found by what they all have: a
    "Local State" file next to Default/ (one or two levels under ~/.config;
    Flatpak browsers under ~/.var/app too). Electron apps keep no Default/
    profile, so they are left alone."""
    bases = [cfg] + [os.path.join(p, "config") for p in
                     _listdirs(os.path.expanduser("~/.var/app"))]
    out = []
    for base in bases:
        for d in _listdirs(base):
            for cand in [d] + _listdirs(d):
                if os.path.isfile(os.path.join(cand, "Local State")) and \
                        os.path.isdir(os.path.join(cand, "Default")):
                    out.append(cand)
    return out


def _listdirs(path: str) -> list:
    try:
        return [os.path.join(path, n) for n in os.listdir(path) if os.path.isdir(os.path.join(path, n))]
    except OSError:
        return []


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


def _mozilla_userjs(path: str, on: bool) -> None:
    """user.js is read at every start (and overrides prefs.js): our one
    marked line in or out."""
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        lines = []
    kept = [ln for ln in lines if "Sonata title bars" not in ln and "Sonata Open/Save panels" not in ln]
    new = kept + ([MOZ_LINE] if on else []) + [MOZ_PORTAL]
    if new != lines:
        _write(path, "\n".join(new) + ("\n" if new else ""))


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


def _vesktop_setting(path: str, on: bool) -> None:
    """Vesktop: "titleBar" ("system" = the window manager's; older builds:
    "customTitleBar" false). Only for a Vesktop that has run once."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    new = dict(data, titleBar="system" if on else "discord", customTitleBar=not on)
    if new != data:
        _write(path, json.dumps(new, indent=4))


def _write(path: str, text: str) -> None:
    tmp = path + ".sonata-tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except OSError:
        pass


# -- colours of the frames Wayfire draws -----------------------------------------------
BLUR_GLASS = 'app_id contains "sonata2" | type is "unmanaged" | type is "toplevel"'
BLUR_SOLID = 'app_id contains "sonata2" | type is "unmanaged"'


def apply_colors(dark: bool) -> None:
    """Title bars pixdecor (and Wayfire's own decoration) draw, for the
    appearance: the Dock's glass (tokens.titlebar_*), blurred by Wayfire.
    Reduce transparency: solid bars and no blur behind other apps' windows
    (lighter on the GPU too). Wayfire reloads its config live."""
    from .backend import system
    from .ui import theme, tokens
    t = dict(tokens.palette(bool(dark)))
    solid = theme.reduce_transparency_now()
    keys = ("titlebar_bg", "titlebar_bg_inactive", "titlebar_text", "titlebar_text_inactive")
    if solid:
        for k in keys[:2]:
            t[k] = tokens.over(t[k], t["window_bg"])
    system.wayfire_set("blur", "blur_by_default", BLUR_SOLID if solid else BLUR_GLASS)
    system.wayfire_set("sonata-corners", "outline", "\\" + tokens.wayfire_color(t["window_outline"]))
    fg, bg, text, dim = (tokens.wayfire_color(t[k], premultiplied=True) for k in keys)
    for k, v in (("fg_color", fg), ("bg_color", bg), ("fg_text_color", text), ("bg_text_color", dim)):
        system.wayfire_set("pixdecor", k, "\\" + v)
    fg, bg, text = (tokens.wayfire_color(t[k]) for k in keys[:3])          # Wayfire's own: straight
    for k, v in (("active_color", fg), ("inactive_color", bg), ("font_color", text)):
        system.wayfire_set("decoration", k, "\\" + v)
