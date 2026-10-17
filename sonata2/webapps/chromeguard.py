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
takes it -- the page's real name and count.

The page's title as it changes (Vini: Instagram's count, only ever in its
title, never reached the Dock; WhatsApp's window still read its address).
Chrome tells DevTools a page's title only when its address changes
(Target.targetInfoChanged): a count that came into the title without a
move went unseen, and what was published was the address, read before the
page's <title>. A small watcher in the page (WATCH_JS, on every load)
reports each new title through a binding (Runtime.bindingCalled); while the
page has no title at all (none yet, or its <title> taken out) it holds the
web app's own name, given way to as soon as the page has its own.

    guard = Guard(app_url, write)      # write(bytes): to the browser
    guard.feed(data)                   # bytes from the browser
"""
import json
import urllib.parse

KEEP_SCHEMES = ("devtools:", "chrome-extension:")


class Guard:
    TITLE_TRIES = 3                        # per address shown as title (never a loop with the page)
    # the page's own title, set again (a change Chrome hears: zero-width space,
    # then back); it answers with that title, to know when the page has none
    NUDGE = ("(() => { const t = document.title;"
             " if (t) { document.title = t + '\\u200b'; document.title = t; } return t; })()")
    TITLES_LOGGED = 5
    BINDING = "sonataTitle"
    # in the page: its title reported on each change (BINDING). A page without
    # one holds the web app's name in a <title> of ours, taken out as soon as
    # the page has its own -- never over the page's count. Only <head> is
    # watched (a chat's other DOM changes cost nothing).
    WATCH_JS = """(() => {
  if (window.__sonataTitle) return;
  window.__sonataTitle = true;
  const NAME = %s, MARK = "data-sonata-name";
  let last = null;
  const look = () => {
    const titles = [...document.getElementsByTagName("title")];
    const mine = titles.filter((e) => e.hasAttribute(MARK));
    if (mine.length && titles.length > mine.length) mine.forEach((e) => e.remove());
    let t = document.title;
    if (!t && NAME && document.head && document.readyState !== "loading") {
      const e = document.createElement("title");
      e.setAttribute(MARK, "");
      e.textContent = NAME;
      document.head.appendChild(e);
      t = document.title;
    }
    if (t && t !== last) {
      last = t;
      try { %s(t); } catch (err) {}
    }
  };
  const watch = () => {
    if (!document.head) return false;
    new MutationObserver(look).observe(document.head, {childList: true, subtree: true, characterData: true});
    look();
    return true;
  };
  if (!watch()) {
    const waiting = new MutationObserver(() => { if (watch()) waiting.disconnect(); });
    waiting.observe(document, {childList: true, subtree: true});
  }
  document.addEventListener("readystatechange", look);
})()"""

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
        self._asked = set()                # ids of the NUDGEs sent (their answers: the page's title)
        self._own_logged = 0
        self._watch_logged = 0
        self.watching = False              # WATCH_JS reports the page's titles (Runtime.bindingCalled)
        self.send("Target.setDiscoverTargets", {"discover": True})

    def send(self, method: str, params=None, session: str = None) -> int:
        self._id += 1
        msg = {"id": self._id, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.write(json.dumps(msg).encode() + b"\0")
        return self._id

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
        if msg.get("id") in self._asked:
            self._asked.discard(msg["id"])
            self._own_title(((msg.get("result") or {}).get("result") or {}).get("value"))
            return
        method = msg.get("method")
        if method == "Target.attachedToTarget":
            params = msg.get("params") or {}
            if (params.get("targetInfo") or {}).get("targetId") == self.main and params.get("sessionId"):
                self.session = params["sessionId"]
                self.send("Page.enable", session=self.session)       # its moves and loads, below
                self._watch_title()
                self._retitle()
            return
        if method == "Runtime.bindingCalled":
            params = msg.get("params") or {}
            if self.session and msg.get("sessionId") == self.session and params.get("name") == self.BINDING:
                self._page_title(params.get("payload"))
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
                if self.on_title and not self.watching:
                    # (once the watcher reports, its word goes: Chrome's is
                    # read only at a move -- often the address, before <title>)
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
        (its answer: _own_title)."""
        if self.session:
            self._asked.add(self.send("Runtime.evaluate", {"expression": self.NUDGE, "returnByValue": True},
                                      session=self.session))

    def _own_title(self, title) -> None:
        """What the page's title really is (the first few logged, to compare
        with the window's). None: WATCH_JS gives it the web app's name --
        set from here once, at a load, the window went back to the address
        as soon as the page took its <title> out again (Vini: WhatsApp's
        window read "web.whatsapp.com" all the same)."""
        if self._own_logged < self.TITLES_LOGGED:
            self._own_logged += 1
            self.log(f"page's own title: {(title or '')[:80]!r}")

    def _watch_title(self) -> None:
        """WATCH_JS in the app's page: now, and in every page it loads."""
        js = self.WATCH_JS % (json.dumps(self.name or ""), self.BINDING)
        self.send("Runtime.enable", session=self.session)            # (the binding's calls come only then)
        self.send("Runtime.addBinding", {"name": self.BINDING}, session=self.session)
        self.send("Page.addScriptToEvaluateOnNewDocument", {"source": js}, session=self.session)
        self.send("Runtime.evaluate", {"expression": js}, session=self.session)

    def _page_title(self, title) -> None:
        """A title the page now has (WATCH_JS): the Dock's count reads it."""
        if not isinstance(title, str):
            return
        self.watching = True
        if self.on_title:
            self.on_title(title)
        if self._watch_logged < self.TITLES_LOGGED:
            self._watch_logged += 1
            self.log(f"page's title now: {title[:80]!r}")


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
