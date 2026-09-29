"""Sonata's desktop settings, kept by Sonata itself -- no GNOME needed.

Appearance and desktop choices (Dark Mode, wallpaper, fonts, cursor,
icons, animations, title bar buttons...) live in
~/.config/sonata2/system.json, keyed like the settings every Linux app
knows ("org.gnome.desktop.interface/color-scheme": "prefer-dark"). Apps
get them from Sonata's settings portal (portal.py), the standard way GTK
3/4, libadwaita, Qt, Firefox and Chromium read them on Wayland. When
GSettings schemas happen to be installed, every change is also copied
there (older apps that read them directly), but nothing depends on it.

    prefs.get("org.gnome.desktop.interface", "color-scheme")   # "prefer-dark"
    prefs.set("org.gnome.desktop.background", "picture-uri", uri)
    prefs.watch(callback)              # keep the returned monitor

`python -m sonata2.prefs session-env` (tools/session-env.sh, every login):
fills in Sonata's defaults (first run), picks the UI font, and prints the
environment the session needs (GTK_THEME)."""
import os
import shutil
import subprocess
import sys

from . import config

NAME = "system"
I, WM, BG = "org.gnome.desktop.interface", "org.gnome.desktop.wm.preferences", "org.gnome.desktop.background"
P = "org.gnome.desktop.privacy"

# Sonata's look (macOS), what a fresh session starts with
DEFAULTS = {
    f"{I}/color-scheme": "default", f"{I}/gtk-theme": "Sonata-Light", f"{I}/icon-theme": "Sonata",
    f"{I}/cursor-theme": "Sonata-Cursors", f"{I}/cursor-size": "24", f"{I}/accent-color": "blue",
    f"{I}/text-scaling-factor": "1.0", f"{I}/enable-animations": "true", f"{I}/font-antialiasing": "grayscale",
    f"{I}/font-hinting": "slight", f"{I}/gtk-enable-primary-paste": "false", f"{I}/overlay-scrolling": "true",
    f"{I}/font-name": "Inter 10", f"{I}/document-font-name": "Inter 10", f"{I}/monospace-font-name": "Monospace 10",
    f"{I}/clock-format": "24h", f"{I}/cursor-blink": "true",
    f"{WM}/button-layout": "close,minimize,maximize:", f"{WM}/action-double-click-titlebar": "toggle-maximize",
    f"{WM}/titlebar-font": "Inter Bold 10",
    f"{BG}/picture-uri": "", f"{BG}/picture-uri-dark": "", f"{BG}/picture-options": "zoom",
    f"{P}/remember-recent-files": "true", f"{P}/remove-old-trash-files": "false", f"{P}/old-files-age": "30",
    "org.gnome.system.location/enabled": "false",
}
# types for the portal (everything else is a string)
BOOLS = {"enable-animations", "gtk-enable-primary-paste", "overlay-scrolling", "cursor-blink",
         "remember-recent-files", "remove-old-trash-files", "enabled"}
INTS = {"cursor-size", "old-files-age"}
DOUBLES = {"text-scaling-factor"}


def _load() -> dict:
    try:
        import json
        with open(os.path.join(config.CONFIG_DIR, NAME + ".json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _plain(value: str) -> str:
    """gsettings' printed form -> the plain value ("'x'" -> x, "uint32 30" -> 30)."""
    v = (value or "").strip()
    for prefix in ("uint32 ", "int32 ", "@as "):
        if v.startswith(prefix):
            v = v[len(prefix):]
    return v.strip("'")


def _from_gsettings(schema: str, key: str):
    """The value GNOME's database had (first run: nothing chosen is lost)."""
    if not shutil.which("gsettings"):
        return None
    try:
        p = subprocess.run(["gsettings", "get", schema, key], capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        return None
    return _plain(p.stdout) if p.returncode == 0 else None


def get(schema: str, key: str, default=None):
    data = _load()
    k = f"{schema}/{key}"
    if k in data:
        return str(data[k])
    if k in DEFAULTS:
        return DEFAULTS[k]
    old = _from_gsettings(schema, key)
    return old if old is not None else default


def set(schema: str, key: str, value) -> bool:        # noqa: A001 (the natural name)
    value = _plain(str(value))
    config.update(NAME, **{f"{schema}/{key}": value})
    _mirror(schema, key, value)
    return True


def _mirror(schema, key, value) -> None:
    """Copy to GSettings when its schemas exist (apps reading them directly)."""
    if not shutil.which("gsettings"):
        return
    typed = value if (key in BOOLS or key in INTS or key in DOUBLES) else "'" + value.replace("'", "\\'") + "'"
    if key == "old-files-age":
        typed = f"uint32 {value}"
    try:
        subprocess.Popen(["gsettings", "set", schema, key, typed], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError:
        pass


def set_wallpaper(uri: str) -> None:
    """Finder's "Set Desktop Picture": the same picture in Light and Dark."""
    for key in ("picture-uri", "picture-uri-dark"):
        set(BG, key, uri)


def is_picture(content_type: str) -> bool:
    return bool(content_type) and content_type.startswith("image/") and "svg" not in content_type \
        and "icon" not in content_type


def watch(callback):
    return config.watch(NAME, callback)


def typed(schema: str, key: str):
    """(GVariant type, python value) of a setting, for the portal."""
    v = get(schema, key, "")
    if key in BOOLS:
        return "b", v == "true"
    if key in INTS:
        try:
            return "i", int(float(v))
        except ValueError:
            return "i", 0
    if key in DOUBLES:
        try:
            return "d", float(v)
        except ValueError:
            return "d", 1.0
    return "s", v


def keys():
    """{schema: [keys]} Sonata knows."""
    out = {}
    for k in list(DEFAULTS) + list(_load()):
        schema, _s, key = k.rpartition("/")
        out.setdefault(schema, [])
        if key not in out[schema]:
            out[schema].append(key)
    return out


# -- every login ----------------------------------------------------------------------------
def _fonts() -> list:
    try:
        p = subprocess.run(["fc-list", ":", "family"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    return {f.strip() for ln in p.stdout.splitlines() for f in ln.split(",")}


def session_env() -> str:
    data = _load()
    first = not data
    if first:                                    # keep what GNOME had for the few keys people change
        seed = {}
        for k in (f"{BG}/picture-uri", f"{BG}/picture-uri-dark", f"{I}/color-scheme"):
            schema, _s, key = k.rpartition("/")
            old = _from_gsettings(schema, key)
            if old:
                seed[k] = old
        config.update(NAME, **seed)
        for k, v in {**DEFAULTS, **seed}.items():          # apps that read GSettings see the same look
            schema, _s, key = k.rpartition("/")
            if v != "":
                _mirror(schema, key, v)
    # UI font: SF Pro when installed (its licence forbids shipping it), else the bundled Inter;
    # only replaces Sonata's own defaults, never a font the user picked (macOS: 13 px = 10 pt)
    fams = _fonts()
    ui = next((f for f in ("SF Pro Text", "SF Pro", "Inter Variable", "Inter") if f in fams), None)
    ours = {"SF Pro Text 10", "SF Pro 10", "Inter Variable 10", "Inter 10", "Cantarell 11", "Adwaita Sans 11", ""}
    if ui and get(I, "font-name") in ours and get(I, "font-name") != f"{ui} 10":
        for key in ("font-name", "document-font-name"):
            set(I, key, f"{ui} 10")
        set(WM, "titlebar-font", f"{ui} Bold 10")
    mono = "SF Mono 10" if "SF Mono" in fams else "Monospace 10"
    if get(I, "monospace-font-name") in {"Monospace 10", "Source Code Pro 10", "Adwaita Mono 11", "SF Mono 10", ""} \
            and get(I, "monospace-font-name") != mono:
        set(I, "monospace-font-name", mono)
    dark = get(I, "color-scheme") == "prefer-dark"
    theme = "Sonata-Dark" if dark else "Sonata-Light"
    if get(I, "gtk-theme") != theme:
        set(I, "gtk-theme", theme)
    return f"export GTK_THEME={theme}\n"


if __name__ == "__main__":
    if sys.argv[1:] == ["session-env"]:
        sys.stdout.write(session_env())
