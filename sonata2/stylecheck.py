"""Are the window styles Sonata puts on other apps still in place?
(Vini: "a way to monitor them all, to see they work").

    sonata2 doctor windows        # the report, in the terminal
    stylecheck.Watch()            # the menu bar: looks after login, a notification when one broke

Two parts:

1. The settings Sonata writes into apps so they use its look (titlebars.py,
   adwstyle.py, steamtheme.py): Chromium browsers' GTK mode and flags file,
   Firefox's user.js, VS Code's title bar style, Vesktop's, Claude's
   environment, the libadwaita style, Steam's patched interface. An app's
   update or its own settings can undo one.
2. Every window open now (Wayfire IPC): who draws its title bar -- Sonata
   (Wayfire's decoration) or the app itself, and if so whether that frame is
   one Sonata styles -- and whether its corners are rounded (sonata-corners,
   "sonata/rounded").

Rows are (status, what, detail): ok / warn / FAIL. The notification only
names what changed since the last one (a new problem), never the same twice."""
import json
import os
import re
import sys

OK, WARN, FAIL = "ok", "warn", "FAIL"
_COLOR = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}
# browsers whose own frame Sonata styles (GTK mode: its traffic lights), by app_id
CHROMIUM_IDS = ("google-chrome", "chromium", "brave-browser", "microsoft-edge", "vivaldi", "opera", "thorium")


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


# -- 1. the settings Sonata writes into apps -------------------------------------------------
def config_rows() -> list:
    from gi.repository import GLib
    from . import titlebars as T
    rows = []
    if not T.enabled():
        return [(OK, "Sonata title bars", "off in Settings > Appearance: apps keep their own")]
    cfg = GLib.get_user_config_dir()
    for base in T.chromium_roots(cfg):
        name = os.path.basename(base)
        for prof in sorted(p for p in os.listdir(base) if p == "Default" or p.startswith("Profile ")):
            try:
                prefs = json.loads(_read(os.path.join(base, prof, "Preferences")) or "{}")
            except ValueError:
                continue
            theme = prefs.get("extensions", {}).get("theme", {})
            frame = prefs.get("browser", {}).get("custom_chrome_frame")
            mode = theme.get("system_theme")
            if mode == T.GTK_MODE and not theme.get("id") and frame is True:
                rows.append((OK, f"{name} ({prof})", "GTK mode: Sonata's traffic lights"))
            elif mode != T.GTK_MODE or theme.get("id"):
                rows.append((OK, f"{name} ({prof})", "Classic mode or a theme: Sonata's title bar above the tabs"
                             if frame is False else "Classic mode or a theme"))
            else:
                rows.append((WARN, f"{name} ({prof})", "its frame setting changed: put back at the next login "
                             "(while the browser is closed)"))
    for flags in T.CHROMIUM_FLAGS:
        path = os.path.join(cfg, flags)
        if os.path.exists(path) or T._installed(flags):
            ok = any(ln.strip().startswith("--gtk-version") for ln in _read(path).splitlines())
            rows.append((OK if ok else WARN, flags, "GTK 3 window buttons" if ok else
                         "no --gtk-version: plain buttons in GTK mode; written at the next login"))
    for base in T.MOZILLA:
        base = os.path.expanduser(base)
        try:
            profs = [p for p in os.listdir(base) if os.path.isfile(os.path.join(base, p, "prefs.js"))]
        except OSError:
            continue
        for p in profs:
            ok = T.MOZ_LINE in _read(os.path.join(base, p, "user.js"))
            rows.append((OK if ok else WARN, f"{os.path.basename(base)} {p}",
                         "tabs under Sonata's title bar" if ok else "user.js line missing: written at the next login"))
    for app in T.CODE:
        if os.path.isdir(os.path.join(cfg, app)):
            text = _read(os.path.join(cfg, app, "User", "settings.json"))
            ok = re.search(r'(?m)^(?:(?!//)[^\n])*?"window\.titleBarStyle"\s*:\s*"native"', text) is not None
            rows.append((OK if ok else FAIL, app, "native title bar (Sonata's)" if ok else
                         'window.titleBarStyle is not "native": its own frame'))
    for path in T.VESKTOP:
        path = os.path.expanduser(path)
        if os.path.isfile(path):
            try:
                ok = json.loads(_read(path) or "{}").get("titleBar") == "system"
            except ValueError:
                ok = False
            rows.append((OK if ok else FAIL, "Vesktop", "system title bar" if ok else "its own title bar"))
    import shutil
    if shutil.which("claude-desktop") or os.path.isdir("/usr/lib/claude-desktop"):
        ok = os.environ.get("CLAUDE_NATIVE_TITLEBAR") == "1"
        rows.append((OK if ok else WARN, "Claude Desktop", "native title bar" if ok else
                     "CLAUDE_NATIVE_TITLEBAR not set in this session (tools/session-env.sh)"))
    try:
        from . import adwstyle
        css, user = adwstyle.css_path(), _read(adwstyle.user_css())
        ok = os.path.isfile(css) and f"file://{css}" in user
        rows.append((OK if ok else FAIL, "GTK 4 / libadwaita apps", "Sonata's traffic lights and title bar"
                     if ok else "style not linked from ~/.config/gtk-4.0/gtk.css (next login)"))
    except Exception as e:                       # noqa: BLE001 -- a report
        rows.append((WARN, "GTK 4 / libadwaita apps", str(e)))
    rows += steam_rows()
    return rows


def steam_rows() -> list:
    from . import steamtheme as S
    rows = []
    for name, root in S.targets().items():
        if not S.enabled():
            rows.append((OK, f"Steam ({name})", "Sonata look off in Settings"))
        elif S.installed(root):
            rows.append((OK, f"Steam ({name})", "Sonata's window buttons"))
        else:
            rows.append((FAIL, f"Steam ({name})", "Steam's own buttons (an update undid it): put back within "
                         "a minute, or at the next login"))
    return rows


# -- 2. the windows open now ------------------------------------------------------------------
def _uses_lib(pid: int, lib: str) -> bool:
    return lib in _read(f"/proc/{pid}/maps") if isinstance(pid, int) and pid > 1 else False


def _own_frame_apps() -> set:
    from .wfconfig import wayfire_get
    raw = wayfire_get("sonata-corners", "own_frame_apps", "") or ""
    return {w.lower() for w in re.split(r"[\s,]+", raw) if w}


_ASK = object()


def window_rows(views=None, rounded=_ASK) -> list:
    """views: Wayfire's list-views; rounded: view ids sonata-corners rounds
    (None: unknown -- an older build without "sonata/rounded")."""
    from .wl.wfipc import WayfireIPC
    if views is None:
        ipc = WayfireIPC()
        if not ipc.available:
            return [(WARN, "Windows", "not in a Wayfire session: nothing to look at")]
        views = ipc.call("window-rules/list-views")
        if rounded is _ASK:
            r = ipc.call("sonata/rounded")
            rounded = set(r.get("rounded", [])) if isinstance(r, dict) and "rounded" in r else None
    if rounded is _ASK:
        rounded = None
    own = _own_frame_apps()
    rows = []
    for v in views or []:
        if not isinstance(v, dict) or v.get("type") != "toplevel" or not v.get("mapped"):
            continue
        app = v.get("app-id") or "?"
        if "sonata2" in app:                     # Sonata's own: drawn by Sonata itself
            continue
        what = f"{app} -- {(v.get('title') or '')[:40]}"
        if v.get("fullscreen"):
            rows.append((OK, what, "full screen (no frame)"))
            continue
        g, b = v.get("geometry") or {}, v.get("base-geometry") or {}
        sonata_bar = (g.get("height", 0) - b.get("height", 0)) > 1 or (g.get("width", 0) - b.get("width", 0)) > 1
        pid = v.get("pid")
        if sonata_bar:
            frame, ok = "Sonata's title bar", True
        elif app.lower() in own:
            frame, ok = "its own frame, styled by Sonata", True
        elif any(app.lower().startswith(c) for c in CHROMIUM_IDS):
            frame, ok = "browser frame (GTK mode: Sonata's traffic lights)", True
        elif _uses_lib(pid, "libadwaita-1"):
            frame, ok = "libadwaita frame, Sonata's style", True
        elif _uses_lib(pid, "libgtk-4"):
            frame, ok = "GTK 4 frame, Sonata's style", True
        else:
            frame, ok = "draws its own title bar: not Sonata's look", False
        rounds = rounded is None or v.get("id") in rounded
        expect_round = sonata_bar or app.lower() in own
        if expect_round and not rounds:
            rows.append((FAIL, what, frame + "; corners NOT rounded (sonata-corners not built or not loaded?)"))
        else:
            rows.append((OK if ok else WARN, what, frame + ("; rounded corners" if expect_round and rounded
                                                            is not None else "")))
    if rounded is None:
        rows.append((WARN, "Rounded corners", "unknown: this sonata-corners build has no \"sonata/rounded\" "
                     "(./install.sh rebuilds it)"))
    return rows


# -- the report ---------------------------------------------------------------------------------
def build() -> list:
    return [("Apps Sonata styles",)] + config_rows() + [("Windows open now",)] + window_rows()


def text(rows, color=False) -> str:
    out = []
    for row in rows:
        if len(row) == 1:
            out.append(("\n" if out else "") + row[0])
            continue
        status, what, detail = row
        tag = f"{_COLOR[status]}{status:>4}\033[0m" if color else f"{status:>4}"
        out.append(f"[{tag}] {what}" + (f" -- {detail}" if detail else ""))
    bad = [r for r in rows if len(r) == 3 and r[0] != OK]
    out.append("")
    out.append("Every style is in place." if not bad else f"{len(bad)} to look at.")
    return "\n".join(out)


def main() -> int:
    rows = build()
    print(text(rows, color=sys.stdout.isatty()))
    return 1 if any(len(r) == 3 and r[0] == FAIL for r in rows) else 0


# -- the menu bar: a notification when something broke ------------------------------------------
def _seen_path() -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2",
                        "stylecheck.json")


def problems(rows) -> list:
    """What to tell: FAILs, and windows drawing their own title bar (one line per app)."""
    out = []
    for r in rows:
        if len(r) != 3 or r[0] == OK:
            continue
        if r[0] == FAIL:
            out.append(r[1])
        elif "draws its own title bar" in r[2]:
            out.append(r[1].split(" -- ")[0] + " (own title bar)")
    return out


def new_problems(rows, seen_path: str = None) -> list:
    """Problems not told before (remembered in ~/.cache/sonata2/stylecheck.json);
    one that's fixed is forgotten, so it's told again if it comes back."""
    path = seen_path or _seen_path()
    try:
        seen = set(json.loads(_read(path) or "[]"))
    except ValueError:
        seen = set()
    now = problems(rows)
    new = [p for p in now if p not in seen]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(set(now)), f)
    except OSError:
        pass
    return new


def check_and_notify() -> list:
    rows = config_rows() + window_rows()
    new = new_problems(rows)
    if new:
        from .gpu import notify
        listed = ", ".join(new[:4]) + (f" and {len(new) - 4} more" if len(new) > 4 else "")
        notify("Window styles", f"Not in Sonata's look: {listed}. Details: sonata2 doctor windows",
               icon="preferences-desktop-theme")
    return new


class Watch:
    """The menu bar's side: a look a while after login, and again every
    INTERVAL_S (an app updated meanwhile, a new app opened) -- in a thread."""
    FIRST_S, INTERVAL_S = 90, 3600

    def __init__(self):
        from gi.repository import GLib
        GLib.timeout_add_seconds(self.FIRST_S, lambda: (self.run(), False)[1])
        GLib.timeout_add_seconds(self.INTERVAL_S, lambda: (self.run(), True)[1])

    def run(self) -> None:
        from .backend import system
        system.run_async(check_and_notify, None)


if __name__ == "__main__":
    sys.exit(main())
