"""Other apps' GTK 4 / libadwaita windows in Sonata's colours.

libadwaita apps (Resources, Papers, Meld, Lutris...) draw with their own
stylesheet whatever GTK theme is set, but read the user stylesheet
~/.config/gtk-4.0/gtk.css and its colour variables. Sonata writes its
palette there (tokens.py: window, content, sidebar, header bar, popovers,
in Light and Dark via prefers-color-scheme), between markers, keeping
anything else you put in that file. Dark Mode and the accent colour reach
them through Sonata's settings portal. Written at every login
(`sonata2 autostart`)."""
import os

from gi.repository import GLib

BEGIN = "/* >>> Sonata (written at login by sonata2/gtkstyle.py; edit outside these markers) */"
END = "/* <<< Sonata */"

# libadwaita variable -> Sonata token
VARS = {
    "window-bg-color": "window_bg", "window-fg-color": "label",
    "view-bg-color": "content_bg", "view-fg-color": "label",
    "headerbar-bg-color": "window_bg", "headerbar-fg-color": "label",
    "headerbar-backdrop-color": "window_bg", "headerbar-border-color": "separator",
    "headerbar-shade-color": "separator",
    "sidebar-bg-color": "sidebar_bg", "sidebar-fg-color": "label", "sidebar-backdrop-color": "sidebar_bg",
    "secondary-sidebar-bg-color": "pane_bg", "secondary-sidebar-fg-color": "label",
    "card-bg-color": "control_bg", "card-fg-color": "label",
    "popover-bg-color": "menu_bg", "popover-fg-color": "label",
    "dialog-bg-color": "window_bg", "dialog-fg-color": "label",
    "destructive-bg-color": "destructive", "destructive-color": "destructive",
    "borders": "separator",
}


def _block(palette) -> str:
    lines = [f"  --{k}: {palette[v]};" for k, v in VARS.items() if v in palette]
    # libadwaita < 1.6 reads named colours instead of variables
    named = [f"@define-color {k.replace('-', '_')} {palette[v]};" for k, v in VARS.items() if v in palette]
    return lines, named


def css() -> str:
    from .ui import tokens
    light, dark = tokens.palette(False), tokens.palette(True)
    lv, ln = _block(light)
    dv, _dn = _block(dark)
    return "\n".join([
        BEGIN,
        ":root {", *lv, "}",
        "@media (prefers-color-scheme: dark) {", "  :root {", *["  " + x for x in dv], "  }", "}",
        *ln,
        "/* Big Sur window corners */",
        f"window.csd, window.csd > .titlebar {{ border-top-left-radius: {tokens.SHARED.get('r_window', '10px')}; "
        f"border-top-right-radius: {tokens.SHARED.get('r_window', '10px')}; }}",
        END, ""])


def write(path=None) -> bool:
    """Put Sonata's block into the user stylesheet (only when it changed)."""
    path = path or os.path.join(GLib.get_user_config_dir(), "gtk-4.0", "gtk.css")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    if BEGIN in text and END in text:
        before = text[:text.index(BEGIN)]
        after = text[text.index(END) + len(END):].lstrip("\n")
    else:
        before, after = (text + "\n" if text and not text.endswith("\n") else text), ""
    new = before + css() + after
    if new == text:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".sonata-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)
    return True
