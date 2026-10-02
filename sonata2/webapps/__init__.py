"""Sonata's web apps (Vini): a website in a window of its own -- its own
Dock icon, its own login (cookies and storage apart from every other web
app), in Launchpad like any app. Made with "New Web App…" (right-click on
Launchpad's or the Desktop's background, or the Dock's divider).

Each one: config webapps.json {"apps": {id: {"name", "url"}}}, a desktop
entry sonata2-webapp-<id>.desktop (`sonata2 webapp <id>`, StartupWMClass =
its own app id, so the Dock tells them apart), its data in
~/.local/share/sonata2/webapps/<id>/ and its icon there (icon.png: the
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
    from gi.repository import GLib
    return os.path.join(GLib.get_user_data_dir(), "sonata2", "webapps", app)


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


def desktop_text(app: str, entry: dict, command: str = None) -> str:
    icon = icon_path(app) if os.path.isfile(icon_path(app)) else FALLBACK_ICON
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


def create(name: str, url: str, command: str = None, fetch: bool = True) -> str:
    url = normalize_url(url)
    if not url:
        raise ValueError("not a web address")
    app = "w" + uuid.uuid4().hex[:10]
    data = config.load(NAME, DEFAULTS)
    data.setdefault("apps", {})[app] = {"name": (name or "").strip() or default_name(url), "url": url}
    config.save(NAME, data)
    os.makedirs(data_dir(app), exist_ok=True)
    write_desktop(app, command)
    if fetch:
        threading.Thread(target=lambda: fetch_icon(app) and write_desktop(app, command), daemon=True).start()
    return app


def rename(app: str, name: str) -> None:
    data = config.load(NAME, DEFAULTS)
    if app in data.get("apps", {}) and name.strip():
        data["apps"][app]["name"] = name.strip()
        config.save(NAME, data)
        write_desktop(app)


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


def write_all(command: str = None) -> None:
    """Every web app's desktop entry (the Dock at login: Sonata's command may have moved)."""
    for app in apps():
        write_desktop(app, command)


def main(argv) -> int:
    from . import window
    return window.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
