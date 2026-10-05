"""What to call a window's app when no desktop entry matches its app_id --
one answer for the Dock, the app switcher and the menu bar (Vini: an
installer run with Faugus showed as "steam_app_default"; it must work for
any app, not only Steam).

    name, icon, dynamic = windowapps.describe(app_id, title)

In order:
1. A Steam game ("steam_app_<id>"): its name and picture from Steam's library.
2. A Windows program run with Proton or Wine outside Steam ("steam_app_default",
   "wine", "something.exe"...): a launcher's shortcut named like the window
   (Faugus, Lutris, Bottles... make them: "FL Studio 2026" for the window
   "FL Studio 2026 - untitled.flp"), else the window's title -- dynamic: it
   follows the title as it changes (the installer, then the program).
3. Any other app: its app_id made readable ("org.example.CoolApp" -> "CoolApp",
   "my-tool" -> "My Tool"), and an icon of that name if the theme has one.

`dynamic` is True when the name comes from the window's title: callers
ask again when the title changes."""
import re

from gi.repository import Gio

# app_ids that say nothing about the program behind them: a Windows program
# run with Proton without a Steam game id, or Wine's own
GENERIC = ("steam_app_default", "steam_app_0", "wine", "wine64", "explorer.exe", "")
LAUNCHER_WORDS = ("faugus", "umu", "proton", "wine", "lutris", "bottles", "heroic")
WINDOWS_ICONS = ("faugus-launcher", "io.github.Faugus.faugus-launcher", "wine", "application-x-ms-dos-executable",
                 "application-x-executable")


def is_windows_program(app_id: str) -> bool:
    a = (app_id or "").casefold()
    return a in GENERIC or a.endswith(".exe")


def readable(app_id: str) -> str:
    """"org.example.CoolApp" -> "CoolApp", "my-tool" -> "My Tool"."""
    a = (app_id or "").strip()
    if "." in a and not a.casefold().endswith(".exe"):
        a = a.rsplit(".", 1)[-1]
    if a.casefold().endswith(".exe"):
        a = a[:-4]
    words = re.split(r"[-_ ]+", a)
    if a == a.lower():
        return " ".join(w.capitalize() for w in words if w) or app_id
    return " ".join(w for w in words if w) or app_id


def _shortcut_for(title: str):
    """A launcher's desktop entry whose name begins the window's title (the
    longest one: the most specific), or None."""
    from . import apps
    low = (title or "").strip().casefold()
    if not low:
        return None
    best = None
    for info in (apps._scan or apps.scan()).values():
        name = (info.get_display_name() or "").strip()
        cmd = (info.get_commandline() or "").casefold()
        if name and low.startswith(name.casefold()) and any(w in cmd for w in LAUNCHER_WORDS):
            if best is None or len(name) > len(best.get_display_name()):
                best = info
    return best


def describe(app_id: str, title: str = ""):
    """(name, Gio.Icon, dynamic) for a window whose app_id has no desktop entry."""
    from . import icons, steamgames
    title = (title or "").strip()
    if steamgames.appid(app_id):
        name = steamgames.name(steamgames.appid(app_id))
        shown = steamgames.shown(app_id, title)
        return shown[0], shown[1], not name
    if is_windows_program(app_id):
        info = _shortcut_for(title)
        if info is not None:
            return info.get_display_name(), icons.app_icon(info), False
        exe = readable(app_id) if (app_id or "").casefold().endswith(".exe") else ""
        return title or exe or "Windows App", Gio.ThemedIcon.new_from_names(list(WINDOWS_ICONS)), not exe or bool(title)
    names = [n for n in (app_id, (app_id or "").lower(), (app_id or "").rsplit(".", 1)[-1].lower()) if n]
    return readable(app_id) or title or "App", Gio.ThemedIcon.new_from_names(names + ["application-x-executable"]), False


def window_title(windows) -> str:
    """The title to name an app by: its focused window's, else the first one's."""
    act = next((t for t in windows if getattr(t, "activated", False)), None)
    for t in ([act] if act else []) + list(windows):
        if getattr(t, "title", ""):
            return t.title
    return ""
