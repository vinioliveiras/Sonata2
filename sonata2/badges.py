"""Dock badges for apps that don't send one (Vini: WhatsApp had none).

An app's badge, first found wins:

  1. the count the app sends itself (Unity LauncherEntry: Telegram,
     Thunderbird, Discord, Signal... -- dock.py)
  2. a web app's unread count from its page title: "(3) WhatsApp", the
     way WhatsApp, Gmail, Telegram Web, Slack... show it in the tab
  3. its notifications still in Notification Center that arrived after
     its window was last in front (opening the app clears it)

The menu bar process (the notification server) publishes its list in
$XDG_RUNTIME_DIR/sonata2-notes.json; the Dock follows it:

    publish(notes, server)          # Notifications, whenever the list changes
    load() -> (server, notes)       # [{"id", "desktop", "app"}]
    watch(callback)                 # callback(), keep the returned monitor
    note_counts(notes, tiles, seen) # {tile key: count}
    title_count(titles)             # 3 for "(3) WhatsApp"
    label(n)                        # "", "7", "99+"

A Sonata web app on Chromium notifies as "Google Chrome": webapp_of(argv)
finds it by the sending process's own profile (notifications.py).

Its window's title is no help there either: Chrome names an --app window
after the site ("web.whatsapp.com", Vini: WhatsApp's count never showed).
The web app's own process reads the page's title over DevTools' pipe as it
changes and publishes it here (page_title); the Dock counts that one and
the window's own.

    publish_page_title(window_class, title)   # the web app's process
    page_titles() -> {window_class: title}    # the Dock
"""
import json
import os
import re

from gi.repository import Gio

STATE = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sonata2-notes.json")
PAGE_TITLES = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "sonata2-webapp-titles.json")
_page_cache = {"mtime": None, "value": {}}
TITLE_COUNT = re.compile(r"^\s*[(\[](\d{1,5})\+?[)\]]\s")


def allowed(cfg: dict, key: str, name: str = "") -> bool:
    """Settings > Notifications: Badge App Icons (all apps) and each app's
    own Badge App Icon (Vini). cfg: notifications.json."""
    if not (cfg or {}).get("badges", True):
        return False
    k, nm = _norm(key), (name or "").casefold()
    for akey, a in ((cfg or {}).get("apps") or {}).items():
        if isinstance(a, dict) and (_norm(akey) == k or (nm and (a.get("name") or "").casefold() == nm)):
            if not a.get("badge", True):
                return False
    return True


def label(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return "" if n <= 0 else ("99+" if n > 99 else str(n))


def title_count(titles) -> int:
    best = 0
    for t in titles or ():
        m = TITLE_COUNT.match(t or "")
        if m:
            best = max(best, int(m.group(1)))
    return best


def counted_title(app_id: str, key: str) -> bool:
    """Web pages put the count in their title; other apps' titles are their
    own business (a document named "(1) draft")."""
    from . import webapps
    aid = (app_id or "").lower()
    return webapps.is_webapp(key) or aid.startswith(("chrome-", "chromium-", "brave-", "msedge-", "vivaldi-"))


# -- web apps' page titles, between processes ------------------------------------------------
def publish_page_title(window_class: str, title, path=None) -> None:
    """The page's title of a web app (None: its window is gone)."""
    path = path or PAGE_TITLES
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    if title is None:
        if window_class not in data:
            return
        data.pop(window_class, None)
    elif data.get(window_class) == title:
        return
    else:
        data[window_class] = title
    tmp = f"{path}.{os.getpid()}.new"               # (several web apps write it)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except OSError:
        pass


def page_titles(path=None) -> dict:
    path = path or PAGE_TITLES
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return {}
    if _page_cache["mtime"] != (path, mtime):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            value = {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except (OSError, ValueError, AttributeError):
            value = {}
        _page_cache.update(mtime=(path, mtime), value=value)
    return _page_cache["value"]


def window_titles(wins, pages: dict = None) -> list:
    """The titles that count: each window's own and its web app's page
    title, both -- the page's stood in for the window's, and a stale one
    (Chrome's word at the last move: "Instagram", the address) hid the
    count the window's title had ("(3) Instagram", Vini: no badge)."""
    pages = page_titles() if pages is None else pages
    out = []
    for t in wins:
        page = pages.get(getattr(t, "app_id", "") or "")
        out += [page, t.title] if page and page != t.title else [t.title]
    return out


# -- the notification list, between processes ----------------------------------------------
def publish(notes, server, path=None) -> None:
    data = {"server": server, "notes": [{"id": n.id, "desktop": n.desktop, "app": n.app} for n in notes]}
    path = path or STATE
    try:
        with open(path + ".new", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(path + ".new", path)
    except OSError:
        pass


def load(path=None):
    try:
        with open(path or STATE, encoding="utf-8") as f:
            data = json.load(f)
        notes = [n for n in data.get("notes") or [] if isinstance(n, dict) and isinstance(n.get("id"), int)]
        return data.get("server"), notes
    except (OSError, ValueError, AttributeError):
        return None, []


def watch(callback, path=None):
    mon = Gio.File.new_for_path(path or STATE).monitor_file(Gio.FileMonitorFlags.NONE, None)
    mon.connect("changed", lambda *_a: callback())
    return mon


def _norm(s: str) -> str:
    return (s or "").removesuffix(".desktop").casefold()


def owner(note: dict, tiles: dict):
    """The tile key a notification belongs to: by its desktop entry, else
    by the app's name. tiles: {key: name}."""
    d = _norm(note.get("desktop"))
    if d:
        for key in tiles:
            if _norm(key) == d:
                return key
    name = (note.get("app") or "").casefold()
    if name:
        for key, tname in tiles.items():
            if (tname or "").casefold() == name:
                return key
    return None


def note_counts(notes, tiles: dict, seen: dict) -> dict:
    """{key: notifications newer than seen[key]} (ids only grow)."""
    out = {}
    for n in notes:
        key = owner(n, tiles)
        if key is not None and n["id"] > seen.get(key, 0):
            out[key] = out.get(key, 0) + 1
    return out


def newest(notes, tiles: dict, key: str) -> int:
    return max((n["id"] for n in notes if owner(n, tiles) == key), default=0)


# -- who sent it ---------------------------------------------------------------------------------
def webapp_of(argv) -> str:
    """A Sonata web app's id from its process's command line: Chromium with
    the web app's profile (.../webapps/<id>/chromium), or `sonata2 webapp <id>`."""
    from . import webapps
    argv = list(argv or ())
    for a in argv:
        # Chrome writes its whole command line back as one string, spaces
        # between ("chrome --user-data-dir=... --app=..."): its own sender
        # was never found by this (its notifications went to "Google Chrome")
        m = re.search(r"(?:^|\s)--user-data-dir=(.+?)(?=\s--|$)", a)
        if m:
            path = os.path.normpath(m.group(1))
            parent, leaf = os.path.split(path)
            root, app = os.path.split(parent)
            if leaf == "chromium" and os.path.basename(root) == "webapps" and app:
                return app
    for i in range(1, len(argv) - 1):              # sonata2 webapp <id>, python -m sonata2 webapp <id>
        if argv[i] == "webapp" and "sonata2" in os.path.basename(argv[i - 1]):
            return argv[i + 1]
    return ""


def _host(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "").strip().lower()
    if "://" in s:
        from urllib.parse import urlsplit
        s = urlsplit(s).hostname or ""
    return s.removeprefix("www.")


def webapp_for_site(body: str, entries: dict) -> str:
    """The web app (id) a browser notification came from, by the site line
    Chrome puts first in its body ("web.whatsapp.com") -- when the sending
    process can't tell (Chrome hands the notification to its own helper)."""
    first = (body or "").strip().split("\n", 1)[0]
    host = _host(first)
    if not host or " " in host or "." not in host:
        return ""
    for app, entry in (entries or {}).items():
        if isinstance(entry, dict) and _host(entry.get("url", "")) == host:
            return app
    return ""


def cmdline(pid: int) -> list:
    try:
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return [p.decode("utf-8", "replace") for p in f.read().split(b"\0") if p]
    except (OSError, ValueError):
        return []
