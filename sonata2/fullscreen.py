"""Full screen like macOS, for every app and game.

  - Ctrl+Super+F (macOS Ctrl+Cmd+F) puts the focused window in full screen
    or takes it out (wayfire.ini runs the menu bar's "fullscreen" action).
  - Apps remember it: an app put in full screen that way opens in full
    screen next time, and one taken out of it stays windowed.
  - Games under Wine / Proton (Heroic, Lutris, Steam...) open in full screen
    by themselves when their window asks for about the whole screen, or
    is a fixed-size game window: many ask for it in a way Wayfire never
    sees as a full-screen request (a screen-sized window, or a size that
    doesn't match the display under Xwayland).

Remembered in ~/.config/sonata2/fullscreen.json {"apps": {app_id: bool}}.
Lives in the menu bar process (it already talks to Wayfire)."""
import os
import re
import time

from gi.repository import GLib

from . import config, logs

DEFAULTS = {"apps": {}}
SCREEN_FRACTION = 0.9           # asks for this much of the display: a game wanting the screen
MIN_GAME = (800, 600)           # fixed-size windows at least this big are games
RECHECK_MS = (300, 1500, 4000)  # Wine sizes its window after mapping it
# Wine windows that aren't games: installers, launchers, tools. Short words
# only match as whole words (or a word's start: "unins000", "setup.exe"), so
# games called "Control", "Crash Bandicoot" or "Assassin's Creed Origins"
# still count; the unambiguous ones match anywhere ("UnityCrashHandler64").
NOT_GAMES = ("explorer", "setup", "install", "unins", "report", "uplay", "origin", "rockstar", "steam.exe",
             "notepad", "control panel", "control.exe", "battle.net")
NOT_GAMES_ANYWHERE = ("winecfg", "regedit", "installer", "redist", "dxsetup", "launcher", "crashhandler",
                      "crashreport", "crash reporter", "crashpad", "ubisoftconnect", "eadesktop",
                      "galaxyclient", "webhelper")
_NOT_GAMES = re.compile(r"(?<![a-z0-9])(?:%s)(?:\d*|er|s?\.exe)(?![a-z])|%s"
                        % ("|".join(map(re.escape, NOT_GAMES)), "|".join(map(re.escape, NOT_GAMES_ANYWHERE))))


def _app(view) -> str:
    return (view.get("app-id") or "").strip()


def is_wine(view) -> bool:
    """A window of a Windows program (Wine, Proton)."""
    app = _app(view).lower()
    if app.endswith(".exe") or app.startswith("steam_app_"):
        return True
    pid = view.get("pid") or 0
    if pid > 0:
        try:
            exe = os.path.basename(os.readlink(f"/proc/{pid}/exe"))
            return "wine" in exe or "proton" in exe
        except OSError:
            pass
    return False


def looks_like_game(view, screen) -> bool:
    """A Wine window that wants the screen (or a big fixed-size one)."""
    if not is_wine(view) or view.get("parent", -1) not in (-1, None):
        return False
    if view.get("role", "toplevel") != "toplevel" or view.get("type", "toplevel") != "toplevel":
        return False
    name = f"{_app(view)} {view.get('title') or ''}".lower()
    if _NOT_GAMES.search(name):
        return False
    g = view.get("geometry") or {}
    w, h = g.get("width", 0), g.get("height", 0)
    sw, sh = screen
    if sw and sh and (w >= sw * SCREEN_FRACTION or h >= sh * SCREEN_FRACTION):
        return True
    lo, hi = view.get("min-size") or {}, view.get("max-size") or {}
    fixed = lo.get("width") and lo == hi
    return bool(fixed and w >= MIN_GAME[0] and h >= MIN_GAME[1])


LOG = os.path.join(logs.log_dir(), "windows.log")
LOG_LINES = 400


def log_view(event: str, view: dict) -> None:
    """One line per window shown / closed (~/.cache/sonata2/windows.log, the
    last LOG_LINES): where apps put their windows, for window bugs (Steam's
    main window never showing up)."""
    if not logs.verbose():
        return
    g = view.get("geometry") or {}
    line = (f"{time.strftime('%H:%M:%S')} {event.replace('view-', ''):8} id={view.get('id')} "
            f"app={view.get('app-id')!r} title={(view.get('title') or '')[:60]!r} pid={view.get('pid')} "
            f"role={view.get('role')} type={view.get('type')} layer={view.get('layer')} "
            f"at={g.get('x')},{g.get('y')} {g.get('width')}x{g.get('height')} out={view.get('output-name')} "
            f"ws={view.get('wset-index')} min={view.get('minimized')} full={view.get('fullscreen')} "
            f"parent={view.get('parent')}\n")
    try:
        lines = []
        if os.path.exists(LOG) and os.path.getsize(LOG) > LOG_LINES * 400:
            with open(LOG, encoding="utf-8") as f:
                lines = f.readlines()[-LOG_LINES // 2:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(lines)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError:
        pass


class Rules:
    def __init__(self, ipc=None):
        if ipc is None:
            from .wl.wfipc import WayfireIPC
            ipc = WayfireIPC()
        self.ipc = ipc
        self.cfg = config.load("fullscreen", DEFAULTS)
        self._done = set()                     # views already decided
        if self.ipc.available:
            self.ipc.watch(["view-mapped", "view-unmapped"], self._event)

    # -- memory -------------------------------------------------------------------------------------
    def remembered(self, app):
        return self.cfg.get("apps", {}).get(app)

    def remember(self, app, on: bool) -> None:
        if app:
            self.cfg = config.load("fullscreen", DEFAULTS)
            self.cfg.setdefault("apps", {})[app] = bool(on)
            config.save("fullscreen", self.cfg)

    # -- Wayfire ------------------------------------------------------------------------------------
    def _views(self) -> list:
        views = self.ipc.call("window-rules/list-views")
        return [v for v in views if isinstance(v, dict)] if isinstance(views, list) else []

    def _screen(self, view) -> tuple:
        out = self.ipc.call("window-rules/output-info", {"id": view.get("output-id", -1)})
        g = (out or {}).get("geometry") or {} if isinstance(out, dict) else {}
        return g.get("width", 0), g.get("height", 0)

    def set_fullscreen(self, view, on: bool) -> None:
        self.ipc.call("wm-actions/set-fullscreen", {"view_id": view["id"], "state": bool(on)})

    # -- the shortcut -------------------------------------------------------------------------------
    def toggle(self) -> None:
        front = next((v for v in self._views() if v.get("activated") and v.get("role", "toplevel") == "toplevel"
                      and v.get("layer", "workspace") == "workspace"), None)
        if front is None:
            return
        on = not front.get("fullscreen")
        self.set_fullscreen(front, on)
        self.remember(_app(front), on)

    # -- new windows --------------------------------------------------------------------------------
    def _event(self, msg) -> None:
        view = msg.get("view") or {}
        vid = view.get("id")
        if vid is None:
            return
        log_view(msg.get("event", ""), view)
        if msg.get("event") == "view-unmapped":
            self._done.discard(vid)
            return
        if view.get("role", "toplevel") != "toplevel" or view.get("parent", -1) not in (-1, None):
            return
        want = self.remembered(_app(view))
        if want:
            self._done.add(vid)
            self.set_fullscreen(view, True)
        elif want is None and is_wine(view):
            for ms in RECHECK_MS:
                GLib.timeout_add(ms, self._check_game, vid)

    def _check_game(self, vid) -> bool:
        if vid in self._done:
            return False
        view = next((v for v in self._views() if v.get("id") == vid), None)
        if view is None or view.get("fullscreen"):
            if view is not None:
                self._done.add(vid)                # it went full screen by itself
            return False
        if looks_like_game(view, self._screen(view)):
            self._done.add(vid)
            self.set_fullscreen(view, True)
        return False
