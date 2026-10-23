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
environment the session needs."""
import os
import shutil
import subprocess
import sys

from . import config, videowall

NAME = "system"
I, WM, BG = "org.gnome.desktop.interface", "org.gnome.desktop.wm.preferences", "org.gnome.desktop.background"
P = "org.gnome.desktop.privacy"

def _frame() -> dict:
    """Window buttons and title font from tokens.FRAME (one place for every
    window frame). tokens.py is plain Python: read without GTK."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("sonata_tokens",
                                                  os.path.join(os.path.dirname(__file__), "ui", "tokens.py"))
    t = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t)
    return {"layout": t.button_layout(t.frame()), "font": t.FRAME["title_font"]}      # (the user's side)


def _wallpaper() -> tuple:
    from . import wallpapers
    return wallpapers.default_uris()


# Sonata's look (macOS), what a fresh session starts with (frame and wallpaper read once:
# each loads a module / scans files)
_FRAME, _WALL = _frame(), _wallpaper()
DEFAULTS = {
    f"{I}/color-scheme": "default", f"{I}/gtk-theme": "Sonata-Light", f"{I}/icon-theme": "Sonata",
    f"{I}/cursor-theme": "Sonata-Cursors", f"{I}/cursor-size": "24", f"{I}/accent-color": "blue",
    f"{I}/text-scaling-factor": "1.0", f"{I}/enable-animations": "true", f"{I}/font-antialiasing": "grayscale",
    f"{I}/font-hinting": "slight", f"{I}/gtk-enable-primary-paste": "false", f"{I}/overlay-scrolling": "true",
    f"{I}/font-name": "Inter 10", f"{I}/document-font-name": "Inter 10", f"{I}/monospace-font-name": "Monospace 10",
    f"{I}/clock-format": "24h", f"{I}/cursor-blink": "true",
    f"{WM}/button-layout": _FRAME["layout"], f"{WM}/action-double-click-titlebar": "toggle-maximize",
    f"{WM}/titlebar-font": _FRAME["font"],
    # Sonata's own "Mountains" (wallpapers.py): snowy peaks in Light, under the stars in Dark
    f"{BG}/picture-uri": _WALL[0], f"{BG}/picture-uri-dark": _WALL[1], f"{BG}/picture-options": "zoom",
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


def get(schema: str, key: str, default=None, data: dict = None):
    """data: system.json already read (callers reading many keys load it once)."""
    data = _load() if data is None else data
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


def usable_picture(uri: str) -> bool:
    """A picture that's there and can be shown (GNOME's / KDE's default can
    be a .jxl no pixbuf loader reads, or a file another desktop removed)."""
    if not uri:
        return False
    try:
        from gi.repository import Gio, GdkPixbuf
        path = Gio.File.new_for_uri(uri).get_path()
        if path and videowall.is_video(path):                 # a video wallpaper (videowall.py)
            return os.path.isfile(path)
        return bool(path) and os.path.isfile(path) and GdkPixbuf.Pixbuf.get_file_info(path)[0] is not None
    except Exception:
        return False


def wallpaper_uri(dark: bool) -> str:
    """The desktop picture for Light / Dark: the chosen one, else Sonata's
    "Mountains" (Vini: a friend's first login showed the bare gradient --
    the picture taken over from his old desktop couldn't be shown)."""
    uri = get(BG, "picture-uri-dark" if dark else "picture-uri") or get(BG, "picture-uri")
    return uri if usable_picture(uri) else _WALL[1 if dark else 0]


def wallpaper_still_uri(dark: bool) -> str:
    """The wallpaper as a picture, for what can't play a video (the lock and
    login screens' blurred backdrop, Mission Control, previews): a video's
    poster frame (made the first time: call it off the UI thread), else the
    picture itself. Sonata's default when the video has no frame to give."""
    uri = wallpaper_uri(dark)
    try:
        from gi.repository import Gio
        path = Gio.File.new_for_uri(uri).get_path()
    except Exception:
        return uri
    if not path or not videowall.is_video(path):
        return uri
    still = videowall.poster(path)
    return "file://" + still if still else _WALL[1 if dark else 0]


def set_wallpaper(uri: str, dark: str = None) -> None:
    """Finder's "Set Desktop Picture" (and Settings): the picture (or video)
    for Light and Dark (`dark`: another one for Dark). The desktop's
    wallpaper is then started again, fresh (Vini: after a video wallpaper,
    a second change didn't show until Sonata restarted -- its player held
    the old wallpaper; a new one shows the new picture from the start)."""
    dark = dark or uri
    if get(BG, "picture-uri") == uri and get(BG, "picture-uri-dark") == dark:
        return
    set(BG, "picture-uri", uri)
    set(BG, "picture-uri-dark", dark)
    reload_wallpaper()


RELOAD_AFTER_MS = 400       # quick changes: one fresh wallpaper, for the last one
_reload = {"src": 0}


def reload_wallpaper() -> None:
    """The desktop's wallpaper started again (`sonata2 restart wallpaper`:
    the new one comes up under the old one, no black flash), once changes
    settle. Without a main loop (a script): at once."""
    try:
        from gi.repository import GLib
    except ImportError:
        GLib = None

    def run():
        _reload["src"] = 0
        from .backend import system
        system.restart_sonata("wallpaper")
        return False
    if GLib is None or not GLib.main_depth() and not _has_loop():
        run()
        return
    if _reload["src"]:
        GLib.source_remove(_reload["src"])
    _reload["src"] = GLib.timeout_add(RELOAD_AFTER_MS, run)


def _has_loop() -> bool:
    """A GTK app (its main loop runs between events: main_depth is 0 then)."""
    try:
        from gi.repository import Gio
        return Gio.Application.get_default() is not None
    except Exception:
        return False


def is_picture(content_type: str) -> bool:
    return bool(content_type) and content_type.startswith("image/") and "svg" not in content_type \
        and "icon" not in content_type


def is_video(content_type: str) -> bool:
    """A video a wallpaper can be made of (videowall.py)."""
    return videowall.is_video(content_type)


def watch(callback):
    return config.watch(NAME, callback)


def typed(schema: str, key: str, data: dict = None):
    """(GVariant type, python value) of a setting, for the portal."""
    v = get(schema, key, "", data)
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


def keys(data: dict = None):
    """{schema: [keys]} Sonata knows."""
    out = {}
    for k in list(DEFAULTS) + list(_load() if data is None else data):
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
            if old and (key == "color-scheme" or usable_picture(old)):   # a picture that can be shown
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
    # other apps take the GTK theme from the settings (portal / Sonata's dconf
    # layer) -- never from a GTK_THEME variable, which breaks libadwaita apps
    return "unset GTK_THEME\n"


if __name__ == "__main__":
    if sys.argv[1:] == ["session-env"]:
        sys.stdout.write(session_env())
