"""Sonata's web apps (Vini): a website in a window of its own -- its own
Dock icon, its own login (cookies and storage apart from every other web
app), in Launchpad like any app. Made with "New Web App…" (right-click on
Launchpad's or the Desktop's background, or the Dock's divider).

Each one: config webapps.json {"apps": {id: {"name", "url"}}}, a desktop
entry sonata2-webapp-<id>.desktop (`sonata2 webapp <id>`, StartupWMClass =
its own app id, so the Dock tells them apart), its data in
~/.local/share/sonata2-data/webapps/<id>/ and its icon there (icon.png: the
site's own, fetched when it's made; the window updates it from the page).

    create(name, url) -> id          remove(id)
    apps() -> {id: {...}}            normalize_url("web.whatsapp.com") -> "https://web.whatsapp.com/"
    open_new()                       # the "New Web App" form (its own process)
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import urllib.parse
import uuid

from .. import config

NAME = "webapps"
DEFAULTS = {"apps": {}}
APP_ID_PREFIX = "io.github.vinioliveiras.sonata2.webapp."
DESKTOP_PREFIX = "sonata2-webapp-"
FALLBACK_ICON = "web-browser"
ICON_MIN = 48                         # smaller site icons look blurry on the Dock: kept only without a better one
FETCH_TIMEOUT = 8
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15"


def apps() -> dict:
    data = config.load(NAME, DEFAULTS).get("apps")
    return data if isinstance(data, dict) else {}


def get(app: str):
    return apps().get(app)


def app_id(app: str) -> str:
    return APP_ID_PREFIX + app


def desktop_id(app: str) -> str:
    return DESKTOP_PREFIX + app


def is_webapp(desktop: str) -> bool:
    return (desktop or "").removesuffix(".desktop").startswith(DESKTOP_PREFIX)


def id_of(desktop: str) -> str:
    return (desktop or "").removesuffix(".desktop")[len(DESKTOP_PREFIX):]


def data_dir(app: str) -> str:
    """~/.local/share/sonata2-data/webapps/<id> (userdata: never Sonata's own
    folder -- a dev install links that to the git clone, and the logins
    landed in the repository)."""
    from .. import userdata
    return os.path.join(userdata.folder("webapps"), app)


def _fix_moved_icon(app: str) -> None:
    """A picture chosen before the data moved (userdata.migrate): App Icons
    follows it to the new folder."""
    from .. import icons
    folder = data_dir(app)                           # (moves the old folder over first)
    apps_prefs = config.load("icons", icons.ICON_DEFAULTS).get("apps") or {}
    path = (apps_prefs.get(desktop_id(app)) or {}).get("path") or ""
    if path and not os.path.exists(path) and f"/webapps/{app}/" in path:
        moved = os.path.join(folder, os.path.basename(path))
        if os.path.exists(moved):
            icons.set_app_pref(desktop_id(app), path=moved)


def icon_path(app: str) -> str:
    return os.path.join(data_dir(app), "icon.png")


def normalize_url(text: str):
    """A typed address -> a full https URL, None when it isn't one."""
    text = (text or "").strip()
    if not text or " " in text:
        return None
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", text):
        text = "https://" + text
    u = urllib.parse.urlsplit(text)
    if u.scheme not in ("http", "https") or not u.hostname or ("." not in u.hostname and u.hostname != "localhost"):
        return None
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, u.fragment))


def default_name(url: str) -> str:
    """"https://web.whatsapp.com/" -> "Whatsapp" (what the form suggests)."""
    host = (urllib.parse.urlsplit(url or "").hostname or "").removeprefix("www.")
    parts = [p for p in host.split(".") if p not in ("web", "app", "m")]
    return (parts[0] if parts else host).capitalize()


def _command() -> str:
    from ..__main__ import self_command
    return self_command()


def webkit_available() -> bool:
    try:
        import gi
        gi.require_version("WebKit", "6.0")
        return True
    except ValueError:
        return False


MISSING_WEBKIT = ("Web apps need WebKitGTK 6.",
                  "Install it with your package manager (Arch / CachyOS: sudo pacman -S webkitgtk-6.0; "
                  "Ubuntu: gir1.2-webkit-6.0; Fedora: webkitgtk6.0), then open the web app again.")


def icon_names(name: str, url: str) -> list:
    """Theme icon names a web app called `name` may have: "WhatsApp" ->
    whatsapp; "Google Calendar" -> google-calendar, googlecalendar..."""
    out = []
    for text in (name, default_name(url)):
        slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
        if slug:
            out += [slug, slug.replace("-", ""), slug.replace("-", "_")]
    return list(dict.fromkeys(out))


def theme_icon(name: str, url: str):
    """The icon theme's own icon with the web app's name (Vini: the icon pack
    first, when it has one), None without one -- or without a display."""
    try:
        from gi.repository import Gdk, Gtk
        display = Gdk.Display.get_default()
        if display is None:
            return None
        theme = Gtk.IconTheme.get_for_display(display)
    except Exception:
        return None
    return next((n for n in icon_names(name, url) if theme.has_icon(n)), None)


def update_theme_icon(app: str) -> None:
    """Look the theme icon up again (main thread: GTK) and keep it in the entry."""
    data = config.load(NAME, DEFAULTS)
    entry = data.get("apps", {}).get(app)
    if not entry:
        return
    found = theme_icon(entry.get("name"), entry.get("url"))
    if found is None and _no_display():
        return                                   # no display: keep what was found before
    if entry.get("theme_icon") != found:
        if found:
            entry["theme_icon"] = found
        else:
            entry.pop("theme_icon", None)
        config.save(NAME, data)


def _no_display() -> bool:
    try:
        from gi.repository import Gdk
        return Gdk.Display.get_default() is None
    except Exception:
        return True


def desktop_text(app: str, entry: dict, command: str = None) -> str:
    # the icon pack's icon with its name, else the site's, else a generic one
    icon = entry.get("theme_icon") or (icon_path(app) if os.path.isfile(icon_path(app)) else FALLBACK_ICON)
    name = (entry.get("name") or "Web App").replace("\n", " ")
    return ("[Desktop Entry]\nType=Application\n"
            f"Name={name}\nComment={entry.get('url', '')}\nIcon={icon}\n"
            "Categories=Network;WebApps;\nKeywords=web;app;site;\n"
            f"StartupWMClass={app_id(app)}\nStartupNotify=true\n"
            f"X-Sonata-WebApp={app}\n"
            f"Exec={command or _command()} webapp {app}\n")


def write_desktop(app: str, command: str = None) -> str:
    from ..apps import write_desktop_file
    entry = get(app)
    if not entry:
        return ""
    return write_desktop_file(desktop_id(app) + ".desktop", desktop_text(app, entry, command))


def create(name: str, url: str, command: str = None, fetch: bool = True, background: bool = False) -> str:
    url = normalize_url(url)
    if not url:
        raise ValueError("not a web address")
    app = "w" + uuid.uuid4().hex[:10]
    data = config.load(NAME, DEFAULTS)
    data.setdefault("apps", {})[app] = {"name": (name or "").strip() or default_name(url), "url": url}
    if background:
        data["apps"][app]["background"] = True
    config.save(NAME, data)
    os.makedirs(data_dir(app), exist_ok=True)
    update_theme_icon(app)
    write_desktop(app, command)
    if fetch:
        threading.Thread(target=lambda: fetch_icon(app) and write_desktop(app, command), daemon=True).start()
    return app


def update(app: str, name: str = None, url: str = None, background: bool = None, command: str = None) -> bool:
    """Edit a web app (its form): name, address, keep running. Its login stays."""
    data = config.load(NAME, DEFAULTS)
    entry = data.get("apps", {}).get(app)
    if entry is None:
        return False
    if name is not None and name.strip():
        entry["name"] = name.strip()
    if url is not None:
        url = normalize_url(url)
        if url:
            entry["url"] = url
    if background is not None:
        if background:
            entry["background"] = True
        else:
            entry.pop("background", None)
    config.save(NAME, data)
    update_theme_icon(app)                       # a new name may have its own icon in the pack
    write_desktop(app, command)
    return True


def rename(app: str, name: str) -> None:
    update(app, name=name)


def set_custom_icon(app: str, picture: str) -> str:
    """A picture the user chose: copied next to the web app's data (the
    original may move) and set as its icon in App Icons (icons.json)."""
    from .. import icons
    ext = os.path.splitext(picture)[1].lower() or ".png"
    os.makedirs(data_dir(app), exist_ok=True)
    for old in os.listdir(data_dir(app)):
        if old.startswith("custom-icon"):
            try:
                os.remove(os.path.join(data_dir(app), old))
            except OSError:
                pass
    dest = os.path.join(data_dir(app), "custom-icon" + ext)
    shutil.copyfile(picture, dest)
    icons.set_app_pref(desktop_id(app), source="file", path=dest)
    return dest


def remove(app: str) -> None:
    """The web app, its desktop entry and its data (login, storage)."""
    from gi.repository import GLib
    data = config.load(NAME, DEFAULTS)
    data.get("apps", {}).pop(app, None)
    config.save(NAME, data)
    try:
        os.remove(os.path.join(GLib.get_user_data_dir(), "applications", desktop_id(app) + ".desktop"))
    except OSError:
        pass
    if re.fullmatch(r"w[0-9a-f]{10}", app):                 # never a path from elsewhere
        shutil.rmtree(data_dir(app), ignore_errors=True)
        from .. import icons
        icons.set_app_pref(desktop_id(app), source=None, path=None, name=None, shape=None, scale=None)


# -- the site's icon -------------------------------------------------------------------------------
class _IconLinks(__import__("html.parser").parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.found = []                  # (score, href)

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        a = dict(attrs)
        rel = (a.get("rel") or "").lower().split()
        if not a.get("href") or not ({"icon", "apple-touch-icon", "apple-touch-icon-precomposed"} & set(rel)):
            return
        size = 0
        for s in (a.get("sizes") or "").split():
            m = re.match(r"(\d+)x\d+", s)
            if m:
                size = max(size, int(m.group(1)))
        if "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
            size = size or 180
        if (a.get("type") or "").endswith("svg") or a["href"].split("?")[0].endswith(".svg"):
            size = size or 512
        self.found.append((size, a["href"]))


def icon_candidates(page_url: str, html: str) -> list:
    """The page's icons, biggest first, then /favicon.ico."""
    p = _IconLinks()
    try:
        p.feed(html or "")
    except Exception:
        pass
    urls = [urllib.parse.urljoin(page_url, h) for _s, h in sorted(p.found, key=lambda f: -f[0])]
    urls.append(urllib.parse.urljoin(page_url, "/favicon.ico"))
    return list(dict.fromkeys(urls))


def _get(url: str, limit: int = 2 << 20) -> bytes:
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
        return r.read(limit)


def save_icon(app: str, raw: bytes) -> bool:
    """Any picture GdkPixbuf reads (png, ico, svg...) -> icon.png (at most 256 px)."""
    from gi.repository import GdkPixbuf
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(raw)
        loader.close()
        pix = loader.get_pixbuf()
    except Exception:
        return False
    if pix is None:
        return False
    side = max(pix.get_width(), pix.get_height())
    if side > 256:
        pix = pix.scale_simple(pix.get_width() * 256 // side, pix.get_height() * 256 // side,
                               GdkPixbuf.InterpType.BILINEAR)
    old = icon_path(app)
    if os.path.isfile(old):
        try:
            if GdkPixbuf.Pixbuf.get_file_info(old)[1] >= max(pix.get_width(), ICON_MIN):
                return False                 # the one we have is as good
        except Exception:
            pass
    os.makedirs(data_dir(app), exist_ok=True)
    tmp = old + ".new"
    try:
        pix.savev(tmp, "png", [], [])
        os.replace(tmp, old)
        return True
    except Exception:
        return False


def fetch_icon(app: str, download=_get) -> bool:
    entry = get(app)
    if not entry:
        return False
    url = entry["url"]
    try:
        html = download(url).decode("utf-8", "replace")
    except Exception:
        html = ""
    for cand in icon_candidates(url, html):
        try:
            if save_icon(app, download(cand)):
                return True
        except Exception:
            continue
    return False


def _spawn(*args) -> None:
    subprocess.Popen(shlex.split(_command()) + ["webapp", *args], start_new_session=True,
                     stdin=subprocess.DEVNULL)


def open_new() -> None:
    """The "New Web App" form, in a process of its own (it takes the
    keyboard; the Dock and the Desktop can't)."""
    _spawn("new")


def launch(app: str) -> None:
    _spawn(app)


def edit(app: str) -> None:
    """The form, filled in (its own process: it takes the keyboard)."""
    _spawn("edit", app)


def write_all(command: str = None) -> None:
    """Every web app's desktop entry (the Dock at login: Sonata's command may have moved)."""
    for app in apps():
        _fix_moved_icon(app)
        update_theme_icon(app)               # (an icon pack installed or changed since)
        write_desktop(app, command)


def main(argv) -> int:
    from . import window
    return window.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
