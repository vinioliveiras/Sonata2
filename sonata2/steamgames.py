"""Steam games in the Dock: their windows have the app_id "steam_app_<id>"
and no .desktop entry (unless the user made a shortcut), so the Dock showed
a generic icon named "steam_app_1234". Here: the game's name (its
appmanifest in a Steam library) and icon (a shortcut's hicolor icon, else
Steam's own copy in appcache/librarycache), and how to start it again.

    steamgames.appid("steam_app_3219630")   # "3219630" (None for other app_ids)
    steamgames.name("3219630")               # "Halloween: The Game"
    steamgames.icon_path("3219630")          # a square picture, or None
    steamgames.launch("3219630")
    steamgames.shown("steam_app_3219630")    # (name, Gio.Icon): the Dock and the app switcher"""
import glob
import os
import re
import subprocess

STEAM_DIRS = ("~/.local/share/Steam", "~/.steam/steam", "~/.var/app/com.valvesoftware.Steam/.local/share/Steam")
_names = {}


def appid(app_id: str):
    m = re.fullmatch(r"steam_app_(\d+)", app_id or "")
    return m.group(1) if m else None


def _roots() -> list:
    out = []
    for d in STEAM_DIRS:
        p = os.path.realpath(os.path.expanduser(d))
        if os.path.isdir(p) and p not in out:
            out.append(p)
    return out


def libraries() -> list:
    """steamapps folders of every Steam library (libraryfolders.vdf)."""
    libs = []
    for root in _roots():
        for vdf in (os.path.join(root, "steamapps", "libraryfolders.vdf"),
                    os.path.join(root, "config", "libraryfolders.vdf")):
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    paths = re.findall(r'"path"\s+"([^"]+)"', f.read())
            except OSError:
                continue
            for p in [root] + paths:
                sa = os.path.join(p.replace("\\\\", "\\"), "steamapps")
                if os.path.isdir(sa) and sa not in libs:
                    libs.append(sa)
        sa = os.path.join(root, "steamapps")
        if os.path.isdir(sa) and sa not in libs:
            libs.append(sa)
    return libs


def name(aid: str):
    if aid in _names:
        return _names[aid]
    found = None
    for lib in libraries():
        try:
            with open(os.path.join(lib, f"appmanifest_{aid}.acf"), encoding="utf-8", errors="replace") as f:
                m = re.search(r'"name"\s+"([^"]+)"', f.read())
        except OSError:
            continue
        if m:
            found = m.group(1)
            break
    _names[aid] = found
    return found


def icon_path(aid: str):
    """The biggest icon Steam keeps for the game: a desktop shortcut's
    hicolor PNG (up to 256 px), else the library cache's square icon
    (appcache/librarycache/<id>/<sha1>.jpg, 32 px; older clients:
    <id>_icon.jpg)."""
    data = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    best, best_px = None, 0
    for f in glob.glob(os.path.join(data, "icons", "hicolor", "*", "apps", f"steam_icon_{aid}.png")):
        m = re.search(r"/(\d+)x\d+/", f)
        px = int(m.group(1)) if m else 0
        if px > best_px:
            best, best_px = f, px
    if best:
        return best
    for root in _roots():
        cache = os.path.join(root, "appcache", "librarycache")
        for f in glob.glob(os.path.join(cache, aid, "*.jpg")):
            if re.fullmatch(r"[0-9a-f]{40}\.jpg", os.path.basename(f)):
                return f
        old = os.path.join(cache, f"{aid}_icon.jpg")
        if os.path.exists(old):
            return old
    return None


def launch(aid: str) -> bool:
    try:
        subprocess.Popen(["steam", f"steam://rungameid/{aid}"], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return True
    except OSError:
        return False


def shown(key: str, fallback_name: str = ""):
    """(name, Gio.Icon) of a Steam game's window, None for other app_ids --
    one answer for the Dock and the app switcher (Vini: Alt+Tab showed
    "steam_app_3219630" with a generic icon). Not in a library: the window's
    title (fallback_name) and Steam's icon."""
    aid = appid(key)
    if not aid:
        return None
    from gi.repository import Gio
    from . import icons
    pic = icon_path(aid)
    gicon = (icons.picture_icon(pic) if pic else None) or Gio.ThemedIcon.new("steam")
    return name(aid) or (fallback_name or "").strip() or key, gicon
