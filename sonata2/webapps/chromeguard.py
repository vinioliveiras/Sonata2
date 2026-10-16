"""A Chromium web app's links (Vini: a link opened a whole browser window
with tabs, out of Sonata's look): no tabs or windows of its own -- a new
tab or window goes to the default browser and closes; login popups (a
window the page keeps a hand on, e.g. "Sign in with Google") stay.

The browser runs with --remote-debugging-pipe: DevTools' protocol over two
pipes (no port anyone else could reach), messages are JSON ended by a NUL.
Sonata only watches which pages open (Target.setDiscoverTargets).

Its window's title (Vini: WhatsApp's window read "web.whatsapp.com" -- its
address -- with neither its name nor its unread count, while a tab of it
read "(10) WhatsApp"): Chrome titles the window from the current history
entry, and a page that moves within itself (pushState: WhatsApp) leaves a
new entry without a title until the title changes again. The app's page is
watched (Page.enable on its own session) and after each such move, and
each load, its own title is set again (the same text, nudged), so Chrome
takes it -- the page's real name and count, nothing made up.

    guard = Guard(app_url, write)      # write(bytes): to the browser
    guard.feed(data)                   # bytes from the browser
"""
import json
import urllib.parse

KEEP_SCHEMES = ("devtools:", "chrome-extension:")


class Guard:
    TITLE_TRIES = 3                        # per address shown as title (never a loop with the page)
    # the page's own title, set again (a change Chrome hears: zero-width space, then back)
    NUDGE = ("(() => { const t = document.title;"
             " if (t) { document.title = t + '\\u200b'; document.title = t; } })()")

    def __init__(self, app_url: str, write, open_outside=None, log=None, name: str = "", on_title=None):
        self.app_url = app_url
        self.name = name
        self.on_title = on_title           # on_title(title): the app's page's (badges.py)
        self.write = write
        self.open_outside = open_outside or _open_default
        self.log = log or (lambda _m: None)
        self.main = None                   # the web app's own page (the first one)
        self.pages = {}                    # targetId -> targetInfo
        self.handled = set()
        self._buf = b""
        self._id = 0
        self.session = None                # attached to the main page (for its title)
        self._title_tries = {}             # url -> tries
        self.send("Target.setDiscoverTargets", {"discover": True})

    def send(self, method: str, params=None, session: str = None) -> None:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.write(json.dumps(msg).encode() + b"\0")

    def feed(self, data: bytes) -> None:
        self._buf += data
        while b"\0" in self._buf:
            msg, self._buf = self._buf.split(b"\0", 1)
            try:
                m = json.loads(msg)
            except ValueError:
                continue
            if isinstance(m, dict):                # (anything else: not a message; the watch goes on)
                self.message(m)

    def message(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "Target.attachedToTarget":
            params = msg.get("params") or {}
            if (params.get("targetInfo") or {}).get("targetId") == self.main and params.get("sessionId"):
                self.session = params["sessionId"]
                self.send("Page.enable", session=self.session)       # its moves and loads, below
                self._retitle()
            return
        if method in ("Page.navigatedWithinDocument", "Page.loadEventFired"):
            if self.session and msg.get("sessionId") == self.session:
                self._retitle()                    # a new history entry: its title again
            return
        if method not in ("Target.targetCreated", "Target.targetInfoChanged"):
            return
        info = (msg.get("params") or {}).get("targetInfo") or {}
        if info.get("type") != "page":
            return
        tid = info.get("targetId")
        if self.main is None:
            self.main = tid                # the --app window's page
            self.send("Target.attachToTarget", {"targetId": tid, "flatten": True})   # (its title: above)
        if tid == self.main:
            if isinstance(info.get("title"), str):
                if self.on_title:
                    self.on_title(info["title"])
                if info["title"] != getattr(self, "_last_title", None) and getattr(self, "_titles_logged", 0) < 5:
                    self._last_title = info["title"]          # (the first few: to compare with the window's)
                    self._titles_logged = getattr(self, "_titles_logged", 0) + 1
                    self.log(f"page title: {info['title'][:80]!r} at {(info.get('url') or '')[:80]}")
            self._check_title(info)
            return
        if tid in self.handled:
            return
        self.pages[tid] = info
        url = info.get("url") or ""
        if url.startswith(KEEP_SCHEMES):
            return
        if info.get("canAccessOpener"):
            return                         # a popup the page uses (a login): it stays
        if url in ("", "about:blank"):
            return                         # not navigated yet: its address comes next
        self.handled.add(tid)
        self.send("Target.closeTarget", {"targetId": tid})
        if url.startswith(("http:", "https:", "mailto:")):
            self.log(f"opened outside: {url[:120]}")
            self.open_outside(url)
        else:
            self.log(f"closed a new {'tab' if url.startswith('chrome:') else 'page'}: {url[:60]}")

    def _check_title(self, info: dict) -> None:
        url = info.get("url") or ""
        if "title" not in info or not url.startswith(("http:", "https:")) or not is_address(info["title"] or "", url):
            return
        tries = self._title_tries.get(url, 0)
        if tries >= self.TITLE_TRIES:
            return
        self._title_tries[url] = tries + 1
        self._retitle()

    def _retitle(self) -> None:
        """The page's own title set again, so Chrome takes it for the window
        (nothing when the page has none: nothing is made up)."""
        if self.session:
            self.send("Runtime.evaluate", {"expression": self.NUDGE}, session=self.session)


def is_address(title: str, url: str) -> bool:
    """Chrome's title for a page without one: its address (or nothing)."""
    title = title.strip().rstrip("/")
    if not title:
        return True
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    bare = url.split("://", 1)[-1].rstrip("/")
    return title in (host, url.rstrip("/"), bare, host.removeprefix("www."))


def _open_default(url: str) -> None:
    """In the default browser (or mail app): Chrome when it's the default.
    A browser started from here goes to a scope of its own (appscope): it
    stayed in the web app's, under its memory cap, and lived and died with it."""
    from gi.repository import Gio, GLib
    from .. import appscope
    ctx = Gio.AppLaunchContext()
    appscope.watch(ctx, "")
    try:
        Gio.AppInfo.launch_default_for_uri(url, ctx)
    except GLib.Error as e:
        print(f"sonata2 webapp: can't open {url}: {e.message}", flush=True)
