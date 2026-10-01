"""Other GTK 4 / libadwaita apps (Bazaar, GNOME apps, Flatpak ones too) in
Sonata's window look -- only inside the Sonata session.

libadwaita takes no theme, but every GTK 4 app reads the user's
~/.config/gtk-4.0/gtk.css. Sonata keeps one line there (between markers):
an @import of $XDG_RUNTIME_DIR/sonata2/adw/libadwaita.css. That file is
written at each Sonata login and removed when the session ends; the
runtime folder is emptied at logout/reboot anyway. In a GNOME or KDE
session it doesn't exist, the import finds nothing and their apps look as
usual.

What it changes, in windows that aren't Sonata's own (.sonata-window):
- the title bar buttons: Sonata's traffic lights (the same pictures
  pixdecor draws on other apps' title bars), already on the left
  (prefs: button-layout);
- the header bar: Sonata's title bar colour (glass tint, lighter when the
  window is inactive), bold title.

Flatpak apps see both paths through Flatpak overrides (flatpak_theme.py).
Off with Settings > Appearance > "Sonata title bars for all apps"."""
import os
import shutil

from .gtkstyle import BEGIN, END

# Sonata's traffic lights (ui/window.py): first dot's centre from the window's
# left and top edges; libadwaita centres its header bar's controls this far down
from .ui.tokens import FRAME  # noqa: E402  (dots and corners: one place for every window)

from .ui.tokens import frame as tokens_frame  # noqa: E402  (the user's window radius)

TL_LEFT, TL_TOP = FRAME["dot_left"], FRAME["dot_top"]
TL_CENTRE_Y_ADW = 23
ADW_START_INSET = 6     # the header bar's start box sits this far in (with no padding)
LIGHTS = ("close", "close-hover", "minimize", "minimize-hover", "maximize", "maximize-hover")


def runtime_dir() -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "sonata2", "adw")


def css_path() -> str:
    return os.path.join(runtime_dir(), "libadwaita.css")


def user_css() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "gtk-4.0", "gtk.css")


def _icons_src() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "icons", "Sonata", "apps", "scalable")


def media_queries() -> bool:
    """GTK 4.20+ (libadwaita 1.8+) reads @media (prefers-color-scheme: dark);
    older ones skip the block, and a dark app would get the light title bar:
    then only the buttons and the bold title change."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        return (Gtk.get_major_version(), Gtk.get_minor_version()) >= (4, 20)
    except Exception:
        return False


ADW_HEADER_H = 47      # libadwaita's header bar height: the see-through band of a glass window


def css(folder: str = None, bars: bool = True, glass: bool = False) -> str:
    """The stylesheet (pictures from `folder`; bars: the title bar colour;
    glass: Settings > Appearance > "Glass title bars", experimental here --
    the window's background is see-through only behind its header bar,
    where Wayfire blurs; the rest stays the app's own colour)."""
    from .ui.tokens import DARK, LIGHT
    D, G = FRAME["dot"], FRAME["dot_gap"]
    folder = folder or runtime_dir()

    def url(name):
        return f'url("file://{os.path.join(folder, "sonata-tl-" + name + ".svg")}")'

    def bar(t):
        bg, bg_off = ((t["titlebar_glass"], t["titlebar_glass_inactive"]) if glass
                      else (t["titlebar_bg"], t["titlebar_bg_inactive"]))
        out = (f"window:not(.sonata-window) headerbar {{ background-color: {bg}; "
               f"box-shadow: inset 0 -1px {t['separator']}; }}\n"
               f"window:not(.sonata-window):backdrop headerbar {{ background-color: {bg_off}; }}\n")
        if glass:      # the app paints its window opaque: only the header's band is let through
            out += (f"window:not(.sonata-window).csd.background {{ background-color: transparent; "
                    f"background-image: linear-gradient(to bottom, transparent {ADW_HEADER_H}px, "
                    f"{t['window_bg']} {ADW_HEADER_H}px); }}\n")
        return out
    w = "window:not(.sonata-window) windowcontrols > button"
    side = FRAME["buttons_side"]                         # the header bar's start (left) or end box
    wc = f"window:not(.sonata-window) headerbar windowcontrols.{'start' if side == 'left' else 'end'}"
    edge = "first-child" if side == "left" else "last-child"
    # Sonata's place for the dots (ui/window.py): 12 px, centres 20 px apart,
    # the first one centred 13 px from the left edge and 14 px from the top.
    # libadwaita's header bar: no left padding here, its controls centred in ~46 px.
    return (f"/* Sonata's window look for other GTK 4 apps -- written by sonata2/adwstyle.py at login */\n"
            f"window:not(.sonata-window) headerbar {{ padding-{side}: 0; }}\n"
            # the corners of Sonata's windows and of the ones Wayfire draws (sonata-corners radius)
            f"window:not(.sonata-window).csd {{ border-radius: {tokens_frame()['radius']}px; }}\n"
            f"window:not(.sonata-window).csd.maximized, window:not(.sonata-window).csd.fullscreen,\n"
            f"window:not(.sonata-window).csd.tiled, window:not(.sonata-window).csd.tiled-top,\n"
            f"window:not(.sonata-window).csd.tiled-left, window:not(.sonata-window).csd.tiled-right,\n"
            f"window:not(.sonata-window).csd.tiled-bottom {{ border-radius: 0; }}\n"
            f"{wc} {{ border-spacing: 0; padding: 0; margin: 0 0 {2 * (TL_CENTRE_Y_ADW - TL_TOP)}px 0; }}\n"
            f"{w}, {w}:hover, {w}:active, {w}:backdrop {{\n"
            f"  min-width: {D}px; min-height: {D}px; padding: 0; margin: 0 {G / 2:g}px; border: none;\n"
            f"  border-radius: 999px;\n"
            f"  box-shadow: none; outline: none; background-color: transparent; background-repeat: no-repeat;\n"
            f"  background-position: center; background-size: {D}px {D}px; }}\n"
            f"{wc} > button:{edge} {{ margin-{side}: {TL_LEFT - D / 2 - ADW_START_INSET:g}px; }}\n"
            f"{w} > image {{ opacity: 0; background: none; box-shadow: none; padding: 0; margin: 0;\n"
            f"  min-width: {D}px; min-height: {D}px; -gtk-icon-size: {D}px; }}\n"
            f"{w}.close {{ background-image: {url('close')}; }}\n"
            f"{w}.close:hover {{ background-image: {url('close-hover')}; }}\n"
            f"{w}.minimize {{ background-image: {url('minimize')}; }}\n"
            f"{w}.minimize:hover {{ background-image: {url('minimize-hover')}; }}\n"
            f"{w}.maximize {{ background-image: {url('maximize')}; }}\n"
            f"{w}.maximize:hover {{ background-image: {url('maximize-hover')}; }}\n"
            f"{w}:active {{ filter: brightness(0.85); }}\n"
            f"window:not(.sonata-window) headerbar .title {{ font-weight: 700; }}\n"
            + ((bar(LIGHT) + "@media (prefers-color-scheme: dark) {\n" + bar(DARK) + "}\n") if bars else ""))


def write(on: bool = True, glass: bool = None) -> str:
    """At login: the pictures and the stylesheet (empty when turned off)."""
    folder = runtime_dir()
    os.makedirs(folder, exist_ok=True)
    if on:
        for n in LIGHTS:
            shutil.copyfile(os.path.join(_icons_src(), f"sonata-tl-{n}.svg"),
                            os.path.join(folder, f"sonata-tl-{n}.svg"))
    tmp = css_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if glass is None:
            from .titlebars import glass_bars
            glass = glass_bars()
        f.write(css(folder, bars=media_queries(), glass=glass) if on else "/* off: Settings > Appearance */\n")
    os.replace(tmp, css_path())
    return css_path()


def link(path: str = None) -> None:
    """Sonata's one line at the top of ~/.config/gtk-4.0/gtk.css (an @import
    must come first); anything else in the file is kept as it is."""
    path = path or user_css()
    block = f'{BEGIN}\n@import url("file://{css_path()}");\n{END}\n'
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    if BEGIN in text and END in text:
        rest = text[:text.index(BEGIN)] + text[text.index(END) + len(END):].lstrip("\n")
    else:
        rest = text
    new = block + rest
    if new != text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".sonata.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        os.replace(tmp, path)


def install() -> None:
    """At every Sonata login."""
    from . import titlebars
    write(titlebars.enabled())
    link()


def stop() -> None:
    """The session ends: other desktops' apps never see it."""
    try:
        os.remove(css_path())
    except OSError:
        pass
