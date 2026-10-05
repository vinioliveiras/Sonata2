"""Steam in Sonata's window layout (Vini: "keep Steam's own theme, but fit
Sonata's window layout"). Steam draws its own title bar and window buttons
(its interface is a web page), so the window manager can't frame it. Steam
loads that interface from CSS files in its own folder: Sonata adds a small
stylesheet of its own to them -- Steam's colours and layout stay as they are,
only the window buttons become Sonata's round traffic lights, on Sonata's
side (Settings > Appearance > Window buttons). The rounded corners are
Wayfire's (sonata-corners/own_frame_apps).

    steamtheme.apply()            # add it (cheap; idempotent)
    steamtheme.ensure()           # at login, and when Steam updates itself: again if it was undone
    steamtheme.remove()

How Steam's files are patched (the way Adwaita-for-Steam does it, MIT): the
file Steam loads (library.css...) is renamed X.original.css, and a new X.css
imports it and then Sonata's stylesheet, padded with spaces to the original's
size. Steam updates put its files back: Sonata looks again at login and
whenever Steam's interface folder changes (the menu bar watches it). Steam
shows it the next time it starts. Settings > Appearance > "Sonata look for
Steam" (on by default) turns it off (and takes it out).

An earlier Sonata installed the whole Adwaita-for-Steam skin (grey, GNOME
colours): it is taken out the first time this runs."""
import base64
import os
import shutil

from . import config

KEY = "steam_theme"
STEAM_ROOTS = {"default": "~/.steam/steam", "flatpak": "~/.var/app/com.valvesoftware.Steam/.steam/steam"}
PATCH_FILES = ("library.css", "gamerecording.css", "gamenotes.css")   # login/dialogs, main windows, notes
HEADER = "/* Sonata 2 */"
SKIN_DIR = "sonata"                                  # steamui/sonata/window.css
ADWAITA_HEADER = "/* Adwaita-for-Steam */"           # the skin an earlier Sonata installed
TL = ("close", "minimize", "maximize", "restore")
# Steam's main window: its top bar (with the Steam / View / Friends... menu).
# Class names from Steam's current interface (as Adwaita-for-Steam 4.4 uses them).
TOPBAR = "div._3Z7VQ1IMk4E3HsHvrkLNgo"
MENUBAR = "div._3s0lkohH8wU2do0K1il28Y"
CONTROLS_SLOT = "div.qP17eBPXkfezFfexZ4hC3"          # the top bar's room for Steam's own window buttons
BIG_PICTURE = "div._3LKQ3S_yqrebeNLF6aeiog"            # the top bar's Big Picture button (GamepadUIToggle)


def enabled() -> bool:
    from .icons import APPEARANCE_DEFAULTS
    return bool(config.load("appearance", APPEARANCE_DEFAULTS).get(KEY, True))


def targets() -> dict:
    """{name: steam root} of the Steam installs here (with their interface files)."""
    out = {}
    for name, root in STEAM_ROOTS.items():
        r = os.path.realpath(os.path.expanduser(root))
        if os.path.isdir(os.path.join(r, "steamui", "css")):
            out[name] = r
    return out


def _first_line(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.readline().strip()
    except OSError:
        return ""


def installed(root: str) -> bool:
    """Every Steam interface file Sonata patches is patched (an update puts the originals back)."""
    css = os.path.join(root, "steamui", "css")
    files = [n for n in PATCH_FILES if os.path.isfile(os.path.join(css, n))]
    return (bool(files) and os.path.isfile(os.path.join(root, "steamui", SKIN_DIR, "window.css")) and
            all(_first_line(os.path.join(css, n)) == HEADER for n in files))


# -- Sonata's window buttons, as CSS ------------------------------------------------------------
def _data_uri(path: str) -> str:
    with open(path, "rb") as f:
        return "url('data:image/svg+xml;base64," + base64.b64encode(f.read()).decode() + "')"


def window_css() -> str:
    """Steam's window buttons as Sonata's: round traffic lights (the same
    pictures as every window's), close / minimize / maximize from the
    window's edge inwards, on the user's side, at Sonata's place. Nothing
    else in Steam changes."""
    from .ui import tokens
    f = tokens.frame()
    left = f["buttons_side"] == "left"
    dot, gap = f["dot"], f["dot_gap"]
    edge, top = f["dot_left"] - dot // 2, f["dot_top"] - dot // 2
    from . import trafficlights
    pics = trafficlights.folder(True)          # Settings' button colours (Steam is dark)
    # in the row, left to right: on the right side close is at the edge (Windows' order);
    # on the left, close first (macOS). Only the main window's buttons move to
    # the user's side; Steam's other windows (Friends, Settings) keep them on
    # the right (their own content is at the left), so there they always use
    # the right side's order (Vini: close first on the right was confusing).
    n = len(f["buttons"])
    left_order = {b: i for i, b in enumerate(f["buttons"])}
    left_order["restore"] = left_order["maximize"]
    right_order = {b: n - 1 - i for b, i in left_order.items()}
    sel = "body.DesktopUI .title-bar-actions.window-controls, html.client_chat_frame .title-bar-actions.window-controls"
    out = [HEADER,
           f"/* window buttons: {tokens.button_layout(f)} */",
           f":is({sel}) {{ display: flex !important; flex-direction: row !important; align-items: center !important;"
           f" gap: {gap}px !important; -webkit-app-region: no-drag !important; }}",
           f":is({sel}) .title-area-icon {{ width: {dot}px !important; height: {dot}px !important;"
           f" min-width: {dot}px !important; min-height: {dot}px !important; max-width: {dot}px !important;"
           f" padding: 0 !important; margin: 0 !important; border: none !important; border-radius: 50% !important;"
           f" box-shadow: none !important; background: no-repeat center / {dot}px {dot}px transparent !important; }}",
           f":is({sel}) .title-area-icon > * {{ display: none !important; }}"]
    for name in TL:
        plain = os.path.join(pics, f"sonata-tl-{name}.svg")
        hover = os.path.join(pics, f"sonata-tl-{name}-hover.svg")
        if not (os.path.isfile(plain) and os.path.isfile(hover)):
            continue
        b = f".title-area-icon.{name}Button"
        out += [f":is({sel}) {b} {{ order: {right_order[name]} !important; background-image: {_data_uri(plain)} !important; }}",
                f":is({sel}):hover {b} {{ background-image: {_data_uri(hover)} !important; }}"]
    # every window: the buttons at Sonata's place (its distance from the top and
    # the side edge), out of Steam's layout -- in Friends / Settings they sat
    # wherever Steam's own (bigger) buttons had been, against the corner (Vini)
    width = 3 * dot + 2 * gap
    side = "left" if left else "right"
    out.append(f":is({sel}) {{ position: fixed !important; top: {top}px !important; right: {edge}px !important;"
               f" left: auto !important; bottom: auto !important; height: {dot}px !important;"
               f" margin: 0 !important; padding: 0 !important; z-index: 1000 !important; }}")
    # the main window: on the user's side; the room Steam kept for its own buttons
    # (after the profile) goes, so its top bar reaches the edge (Vini)
    main = f"body.DesktopUI:has({TOPBAR})"
    if left:
        out.append(f"{main} .title-bar-actions.window-controls {{ left: {edge}px !important; right: auto !important; }}")
    out.append(f"{main} {TOPBAR} {CONTROLS_SLOT} {{ flex: 0 0 {8 if left else edge + width + 12}px !important;"
               f" width: {8 if left else edge + width + 12}px !important; min-width: 0 !important; }}")
    out.append(f"body.DesktopUI {TOPBAR} {BIG_PICTURE} {{ display: none !important; }}")   # Vini: not used
    if left:
        out += [f"{main} .title-bar-actions.window-controls .title-area-icon.{name}Button {{ order: {i} !important; }}"
                for name, i in left_order.items()]
        out.append(f"{main} {TOPBAR} {MENUBAR} {{ margin-left: {edge + width + 12}px !important; }}")
    return "\n".join(out) + "\n"


# -- Steam's files --------------------------------------------------------------------------------
def _patch_text(name: str) -> str:
    return (f'{HEADER}\n@import url("{name[:-4]}.original.css");\n'
            f'@import url("../{SKIN_DIR}/window.css");\n')


def _drop_adwaita(root: str) -> None:
    """The whole Adwaita-for-Steam skin an earlier Sonata installed: Steam's own files back."""
    css = os.path.join(root, "steamui", "css")
    for n in PATCH_FILES:
        p, orig = os.path.join(css, n), os.path.join(css, n[:-4] + ".original.css")
        if _first_line(p) == ADWAITA_HEADER and os.path.isfile(orig):
            os.replace(orig, p)
    shutil.rmtree(os.path.join(root, "steamui", "adwaita"), ignore_errors=True)


def _patch_root(root: str, css_text: str) -> bool:
    css = os.path.join(root, "steamui", "css")
    _drop_adwaita(root)
    skin = os.path.join(root, "steamui", SKIN_DIR)
    os.makedirs(skin, exist_ok=True)
    path = os.path.join(skin, "window.css")
    try:
        with open(path, encoding="utf-8") as fh:
            same = fh.read() == css_text
    except OSError:
        same = False
    if not same:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(css_text)
    ok = True
    for n in PATCH_FILES:
        p, orig = os.path.join(css, n), os.path.join(css, n[:-4] + ".original.css")
        if not os.path.isfile(p) or _first_line(p) == HEADER:
            continue
        text = _patch_text(n).encode()
        size = os.path.getsize(p)
        if len(text) > size:                         # (Steam reads a file of the original's size)
            ok = False
            continue
        os.replace(p, orig)
        with open(p, "wb") as fh:
            fh.write(text + b" " * (size - len(text)))
    return ok


def apply(force: bool = False) -> bool:
    """Sonata's window buttons in every Steam here (cheap: only what's missing is written)."""
    found = targets()
    if not found:
        return False
    text = window_css()
    return all([_patch_root(root, text) for root in found.values()])


def ensure() -> bool:
    """On, and undone (a Steam update) or out of date: apply again."""
    if not enabled():
        return False
    try:
        return apply()
    except Exception as e:
        print(f"sonata2: Steam theme: {e}", flush=True)
        return False


def remove() -> bool:
    """Steam's own files back (and an earlier Adwaita skin out)."""
    for root in targets().values():
        css = os.path.join(root, "steamui", "css")
        _drop_adwaita(root)
        for n in PATCH_FILES:
            p, orig = os.path.join(css, n), os.path.join(css, n[:-4] + ".original.css")
            if _first_line(p) == HEADER and os.path.isfile(orig):
                os.replace(orig, p)
        shutil.rmtree(os.path.join(root, "steamui", SKIN_DIR), ignore_errors=True)
    return True


class Watch:
    """The menu bar's side: at login, and after Steam rewrites its interface
    files (an update), the buttons are put back -- quietly, in a thread. Also
    when the window buttons' side changes (appearance.json)."""
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
