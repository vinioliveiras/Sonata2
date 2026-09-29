"""Other apps' windows in Sonata's look -- only inside the Sonata session.

Apps read the look from Sonata's settings, served by its portal (and a
Sonata-only dconf layer): GTK 3 apps and Qt apps (QT_QPA_PLATFORMTHEME=gtk3)
take the Sonata GTK theme (gtk-theme: Sonata-Light / Sonata-Dark) and follow
Dark Mode live; libadwaita apps keep their own widgets and take Dark Mode
and the accent colour. Nothing uses a GTK_THEME variable: forcing a theme
that way made libadwaita apps see-through. Another desktop's session never
sees any of it.

Nothing is written to ~/.config/gtk-4.0: an earlier version did, and clean()
removes that block at login. reset_env() clears a GTK_THEME an earlier
version left in the D-Bus / systemd activation environment."""
import os
import shutil
import subprocess

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
    """Dark Mode changed (apps follow the settings live); Flatpak apps too."""
    try:
        from . import flatpak_theme
        flatpak_theme.apply(dark)
    except Exception as e:
        print(f"sonata2: flatpak theme: {e}")


def reset_env() -> None:
    if not in_session():
        return
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "unset-environment", "GTK_THEME"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if shutil.which("dbus-update-activation-environment"):
        subprocess.run(["dbus-update-activation-environment", "GTK_THEME="],     # empty = no theme forced
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def clean(path=None) -> bool:
    """Remove the block an earlier Sonata put in ~/.config/gtk-4.0/gtk.css."""
    path = path or os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                "gtk-4.0", "gtk.css")
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
