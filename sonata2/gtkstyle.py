"""Other apps' windows in Sonata's look -- only inside the Sonata session.

GTK 3/4 and libadwaita apps take the theme named by GTK_THEME (Sonata-Light
or Sonata-Dark, set by tools/session-env.sh for this session only; another
desktop never sees it). Qt apps follow it through QT_QPA_PLATFORMTHEME=gtk3.
GTK_THEME is read when an app starts, so switching Dark Mode updates it for
apps opened from then on: every Sonata launch sets it (apps.py), and D-Bus
activated apps get it through the activation environment (update()).

Nothing is written to ~/.config/gtk-4.0: an earlier version did, and clean()
removes that block at login so GNOME/KDE sessions keep their own look."""
import os
import shutil
import subprocess

from gi.repository import GLib

BEGIN = "/* >>> Sonata (written at login by sonata2/gtkstyle.py; edit outside these markers) */"
END = "/* <<< Sonata */"


def theme_name(dark: bool = None) -> str:
    if dark is None:
        from . import prefs
        dark = prefs.get(prefs.I, "color-scheme") == "prefer-dark"
    return "Sonata-Dark" if dark else "Sonata-Light"


def in_session() -> bool:
    return "sonata" in (os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()


def update(dark: bool) -> None:
    """Dark Mode changed: apps started from now on (also by D-Bus) get the new theme."""
    name = theme_name(dark)
    os.environ["GTK_THEME"] = name
    if in_session() and shutil.which("dbus-update-activation-environment"):
        subprocess.Popen(["dbus-update-activation-environment", "--systemd", f"GTK_THEME={name}"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def clean(path=None) -> bool:
    """Remove the block an earlier Sonata put in ~/.config/gtk-4.0/gtk.css."""
    path = path or os.path.join(GLib.get_user_config_dir(), "gtk-4.0", "gtk.css")
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    if BEGIN not in text or END not in text:
        return False
    new = text[:text.index(BEGIN)] + text[text.index(END) + len(END):].lstrip("\n")
    if new.strip():
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
    else:
        os.remove(path)
    return True
