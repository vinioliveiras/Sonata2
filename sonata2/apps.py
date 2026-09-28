"""Installed applications (.desktop entries) and the default Dock pins."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

# Default pins, macOS order: Finder, browser, Mail, ..., Terminal, Settings.
# Each slot lists candidate desktop ids across distros/desktops; the first one
# installed wins, so the Dock never shows a broken icon.
DEFAULT_SLOTS = (
    ("org.gnome.Nautilus", "nautilus", "org.kde.dolphin", "nemo", "thunar", "pcmanfm-qt", "pcmanfm"),
    ("@browser",),   # the user's default web browser
    ("org.gnome.Evolution", "org.mozilla.Thunderbird", "thunderbird", "geary", "org.gnome.Geary"),
    ("org.gnome.Calendar", "org.kde.merkuro.calendar"),
    ("org.gnome.Music", "rhythmbox", "org.gnome.Rhythmbox3", "elisa", "org.kde.elisa", "spotify"),
    ("org.gnome.Loupe", "org.gnome.eog", "eog", "org.kde.gwenview", "gwenview"),
    ("org.gnome.TextEditor", "org.gnome.gedit", "org.kde.kate", "mousepad", "code", "code-oss"),
    ("steam",),
    ("org.gnome.Console", "org.gnome.Terminal", "com.mitchellh.ghostty", "kitty", "Alacritty",
     "foot", "org.kde.konsole", "xfce4-terminal"),
)


def lookup(desktop_id: str):
    """Gio.DesktopAppInfo for 'firefox' or 'firefox.desktop', or None."""
    if not desktop_id.endswith(".desktop"):
        desktop_id += ".desktop"
    try:
        return Gio.DesktopAppInfo.new(desktop_id)
    except TypeError:   # PyGObject raises on a NULL constructor result
        return None


def _default_browser():
    info = Gio.AppInfo.get_default_for_uri_scheme("https")
    return info.get_id() if info else None


def default_pins() -> list:
    pins = []
    for slot in DEFAULT_SLOTS:
        for cand in slot:
            did = _default_browser() if cand == "@browser" else cand
            if did and lookup(did):
                did = did[:-8] if did.endswith(".desktop") else did
                if did not in pins:
                    pins.append(did)
                break
    return pins
