"""Steam in Sonata's look (Vini): Steam draws its own title bar and window
buttons (its UI is a web page), so a window manager can't frame it. Steam
loads its interface from CSS files in its own folder; Adwaita-for-Steam
(github.com/tkashkin/Adwaita-for-Steam, MIT) patches them with a clean
header bar and window controls on either side. Sonata runs that installer
with Sonata's colours, its window buttons' side and its own traffic-light
pictures (custom CSS), so Steam's title bar matches every other window.

    steamtheme.apply()            # install / update (a thread: it downloads once)
    steamtheme.ensure()           # at login, and when Steam updates itself: again if it was undone
    steamtheme.remove()

Steam updates put its CSS back: Sonata looks again at login and whenever
Steam's interface folder changes (the menu bar watches it). Steam shows the
new look the next time it starts. Settings > Appearance > "Sonata look for
Steam" (on by default) turns it off (and uninstalls)."""
import base64
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import urllib.request

from . import config

KEY = "steam_theme"
# a known version of the installer (its options and Steam's patched files change with it)
COMMIT = "1e92107a51f6ed53c59c38646444c9eb3a52b030"            # Adwaita-for-Steam 4.4
URL = f"https://codeload.github.com/tkashkin/Adwaita-for-Steam/tar.gz/{COMMIT}"
STEAM_ROOTS = {"default": "~/.steam/steam", "flatpak": "~/.var/app/com.valvesoftware.Steam/.steam/steam"}
PATCH_HEADER = "/* Adwaita-for-Steam */"
TL = ("close", "minimize", "maximize", "restore")


def enabled() -> bool:
    from .icons import APPEARANCE_DEFAULTS
    return bool(config.load("appearance", APPEARANCE_DEFAULTS).get(KEY, True))


def _cache(*p) -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2", *p)


def targets() -> dict:
    """{name: steam root} of the Steam installs here (with their interface files)."""
    out = {}
    for name, root in STEAM_ROOTS.items():
        r = os.path.realpath(os.path.expanduser(root))
        if os.path.isdir(os.path.join(r, "steamui", "css")):
            out[name] = r
    return out


def installed(root: str) -> bool:
    """Steam's interface patched (an update puts the original files back)."""
    css = os.path.join(root, "steamui", "css")
    if not os.path.isdir(os.path.join(root, "steamui", "adwaita")):
        return False
    try:
        names = [n for n in os.listdir(css) if n.endswith(".css") and not n.endswith(".original.css")]
    except OSError:
        return False
    for n in names:
        try:
            with open(os.path.join(css, n), encoding="utf-8", errors="replace") as f:
                if f.readline().strip().startswith(PATCH_HEADER):
                    return True
        except OSError:
            continue
    return False


# -- Sonata's look, as Adwaita-for-Steam options + custom CSS ---------------------------------
def _data_uri(path: str) -> str:
    with open(path, "rb") as f:
        return "url('data:image/svg+xml;base64," + base64.b64encode(f.read()).decode() + "')"


def custom_css() -> str:
    """Sonata's traffic lights (the same pictures as every window's), its
    title bar colours and the buttons' place."""
    from .icons import ICONS_DIR
    from .ui import tokens
    f = tokens.frame()
    pics = os.path.join(ICONS_DIR, "Sonata", "apps", "scalable")
    lines = [":root {",
             f"  --adw-windowcontrols-button-width: {f['dot']}px !important;",
             f"  --adw-windowcontrols-button-height: {f['dot']}px !important;",
             f"  --adw-windowcontrols-button-gap: {f['dot_gap']}px !important;",
             f"  --adw-windowcontrols-buttons-margin-outer: {f['dot_left'] - f['dot'] // 2}px !important;"]
    for name in TL:
        plain = os.path.join(pics, f"sonata-tl-{name}.svg")
        hover = os.path.join(pics, f"sonata-tl-{name}-hover.svg")
        if os.path.isfile(plain) and os.path.isfile(hover):
            lines += [f"  --adw-icon-macos-window-{name}: {_data_uri(plain)} !important;",
                      f"  --adw-icon-macos-window-{name}-hover: {_data_uri(hover)} !important;",
                      f"  --adw-icon-macos-window-{name}-active: {_data_uri(hover)} !important;"]
    light, dark = tokens.palette(False), tokens.palette(True)
    for var, key in (("--adw-headerbar-bg", "titlebar_bg"), ("--adw-headerbar-backdrop", "titlebar_bg_inactive")):
        lines.append(f"  {var}: light-dark({light[key]}, {dark[key]}) !important;")
    lines.append("}")
    return "\n".join(lines) + "\n"


def accent() -> str:
    from .icons import APPEARANCE_DEFAULTS
    from .ui import tokens
    name = config.load("appearance", APPEARANCE_DEFAULTS).get("accent", "blue")
    return tokens.accent_tokens(name, False).get("accent") or tokens.palette(False)["accent"]


def options(css_path: str) -> list:
    from .ui import tokens
    return ["--color-theme", "adwaita", "--color-scheme", "auto", "--accent-color", accent(),
            "--windowcontrols-theme", "macos", "--windowcontrols-layout", tokens.button_layout(tokens.frame()),
            "--font", "system", "--custom-css", css_path]


# -- the installer ------------------------------------------------------------------------------
def installer_dir() -> str:
    """Adwaita-for-Steam at COMMIT, downloaded once into the cache."""
    d = _cache("adwaita-for-steam", COMMIT)
    if os.path.isfile(os.path.join(d, "install.py")):
        return d
    with urllib.request.urlopen(URL, timeout=60) as r:
        data = r.read()
    os.makedirs(d, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        top = t.getmembers()[0].name.split("/")[0]
        for m in t.getmembers():
            if not m.name.startswith(top + "/"):
                continue
            m.name = m.name[len(top) + 1:]
            if m.name:
                t.extract(m, d, filter="data")
    return d


def _stamp_path() -> str:
    return _cache("steam-theme.json")


def _wanted() -> str:
    """What the last install was made with: a change installs again."""
    return hashlib.sha1((COMMIT + custom_css() + " ".join(options(""))).encode()).hexdigest()


def _run(args: list) -> bool:
    d = installer_dir()
    r = subprocess.run([sys.executable, "install.py"] + args, cwd=d, capture_output=True, text=True, timeout=180,
                       stdin=subprocess.DEVNULL)
    if r.returncode:
        print(f"sonata2: Steam theme: {(r.stderr or r.stdout).strip()[-400:]}", flush=True)
    return r.returncode == 0


def apply(force: bool = False) -> bool:
    """Install (or update) Sonata's look in every Steam here. Blocking: call in a thread."""
    found = targets()
    if not found:
        return False
    wanted = _wanted()
    try:
        with open(_stamp_path()) as f:
            stamp = json.load(f)
    except (OSError, ValueError):
        stamp = {}
    todo = [n for n, root in found.items() if force or stamp.get(n) != wanted or not installed(root)]
    if not todo:
        return True
    os.makedirs(_cache(), exist_ok=True)
    css = _cache("steam-sonata.css")
    with open(css, "w", encoding="utf-8") as f:
        f.write(custom_css())
    ok = _run(["--target"] + [found[n] for n in todo] + options(css))
    if ok:
        stamp.update({n: wanted for n in todo})
        with open(_stamp_path(), "w") as f:
            json.dump(stamp, f)
    return ok


def ensure() -> bool:
    """On, and undone (a Steam update) or out of date: apply again."""
    if not enabled():
        return False
    try:
        return apply()
    except Exception as e:                        # (offline the first time: next login)
        print(f"sonata2: Steam theme: {e}", flush=True)
        return False


def remove() -> bool:
    found = {n: r for n, r in targets().items() if installed(r)}
    if not found:
        return True
    ok = _run(["--uninstall", "--target"] + list(found.values()))
    try:
        os.unlink(_stamp_path())
    except OSError:
        pass
    return ok


class Watch:
    """The menu bar's side: at login, and after Steam rewrites its interface
    files (an update), the look is put back -- quietly, in a thread. Also
    when the accent or the window buttons' side change (appearance.json)."""
    SETTLE_S = 60

    def __init__(self, first_s: int = 20):
        from gi.repository import Gio, GLib
        self._src = 0
        self._busy = False
        self._mons = []
        for root in targets().values():
            try:
                m = Gio.File.new_for_path(os.path.join(root, "steamui", "css")).monitor_directory(
                    Gio.FileMonitorFlags.NONE, None)
            except Exception:
                continue
            m.connect("changed", lambda *_a: self.soon(self.SETTLE_S))
            self._mons.append(m)
        self._cfg = config.watch("appearance", lambda *_a: self.soon(5))
        GLib.timeout_add_seconds(first_s, lambda: (self.run(), False)[1])

    def soon(self, seconds: int) -> None:
        from gi.repository import GLib
        if self._src:
            GLib.source_remove(self._src)
        self._src = GLib.timeout_add_seconds(seconds, lambda: (self.run(), False)[1])

    def run(self) -> None:
        self._src = 0
        if self._busy:
            return
        self._busy = True
        from .backend import system
        system.run_async(ensure, lambda _ok: setattr(self, "_busy", False))
