"""A Chromium web app's links (Vini: a link opened a whole browser window
with tabs, out of Sonata's look): no tabs or windows of its own -- a new
tab or window goes to the default browser and closes; login popups (a
window the page keeps a hand on, e.g. "Sign in with Google") stay.

The browser runs with --remote-debugging-pipe: DevTools' protocol over two
pipes (no port anyone else could reach), messages are JSON ended by a NUL.
Sonata only watches which pages open (Target.setDiscoverTargets).

    guard = Guard(app_url, write)      # write(bytes): to the browser
    guard.feed(data)                   # bytes from the browser
"""
import json

KEEP_SCHEMES = ("devtools:", "chrome-extension:")


class Guard:
    def __init__(self, app_url: str, write, open_outside=None, log=None):
        self.app_url = app_url
        self.write = write
        self.open_outside = open_outside or _open_default
        self.log = log or (lambda _m: None)
        self.main = None                   # the web app's own page (the first one)
        self.pages = {}                    # targetId -> targetInfo
        self.handled = set()
        self._buf = b""
        self._id = 0
        self.send("Target.setDiscoverTargets", {"discover": True})

    def send(self, method: str, params=None) -> None:
        self._id += 1
        self.write(json.dumps({"id": self._id, "method": method, "params": params or {}}).encode() + b"\0")

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
        if method not in ("Target.targetCreated", "Target.targetInfoChanged"):
            return
        info = (msg.get("params") or {}).get("targetInfo") or {}
        if info.get("type") != "page":
            return
        tid = info.get("targetId")
        if self.main is None:
            self.main = tid                # the --app window's page
        if tid == self.main or tid in self.handled:
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
