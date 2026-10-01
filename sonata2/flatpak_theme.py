"""Sonata's look for Flatpak apps too (Settings > General, on by default).

Flatpak apps run sandboxed: they don't see the system's themes nor the
session's GTK_THEME. Turning this on copies Sonata's GTK themes to
~/.local/share/themes (inside the sandbox's reach) and adds to Flatpak's
user overrides for every app: read access to that folder (the theme name
and Dark Mode come from Sonata's settings portal, which Flatpak apps read). Flatpak overrides apply in every session,
so a GNOME/KDE session's Flatpak apps look like Sonata's too while it's
on; turning it off removes exactly what Sonata added."""
import os
import shutil

from gi.repository import GLib

from . import config

NAME = "appearance"
KEY = "flatpak_theme"
FS = "xdg-data/themes:ro"
# GTK 4 apps' user stylesheet and the file it imports in Sonata's session (adwstyle.py)
EXTRA_FS = ("xdg-config/gtk-4.0:ro", "xdg-run/sonata2:ro")
THEMES = ("Sonata-Light", "Sonata-Dark")


def enabled() -> bool:
    return bool(config.load(NAME, {KEY: True}).get(KEY, True))


def _overrides_path() -> str:
    return os.path.join(GLib.get_user_data_dir(), "flatpak", "overrides", "global")


def _edit(fn) -> None:
    path = _overrides_path()
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(path, GLib.KeyFileFlags.KEEP_COMMENTS)
    except GLib.Error:
        pass
    fn(kf)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    kf.save_to_file(path)


def _filesystems(kf) -> list:
    try:
        return [f for f in kf.get_string("Context", "filesystems").split(";") if f]
    except GLib.Error:
        return []


def _copy_themes() -> None:
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "themes")
    dst = os.path.join(GLib.get_user_data_dir(), "themes")
    for name in THEMES:
        s, d = os.path.join(src, name), os.path.join(dst, name)
        stamp = os.path.join(d, ".sonata-version")
        version = str(int(max(os.path.getmtime(os.path.join(r, f)) for r, _d, fs in os.walk(s) for f in fs)))
        try:
            with open(stamp, encoding="utf-8") as f:
                if f.read() == version:
                    continue                  # already the current copy
        except OSError:
            pass
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        shutil.copytree(s, d, symlinks=False)
        with open(stamp, "w", encoding="utf-8") as f:
            f.write(version)


def apply(dark: bool = None) -> bool:
    """On (at login, on toggle, on Dark Mode): themes copied, overrides set."""
    if not shutil.which("flatpak") or not enabled():
        return False
    _copy_themes()

    def change(kf):
        fs = _filesystems(kf)
        want = [f for f in (FS,) + EXTRA_FS if f not in fs]
        if want:
            kf.set_string("Context", "filesystems", ";".join(fs + want) + ";")
        try:                                   # (an earlier version forced GTK_THEME: it broke libadwaita apps)
            if kf.get_string("Environment", "GTK_THEME").startswith("Sonata-"):
                kf.remove_key("Environment", "GTK_THEME")
        except GLib.Error:
            pass
    _edit(change)
    return True


def remove() -> None:
    """Off: only what Sonata added goes away (your own overrides stay)."""
    if not os.path.exists(_overrides_path()):
        return

    def change(kf):
        fs = [f for f in _filesystems(kf) if f != FS and f not in EXTRA_FS]
        if fs:
            kf.set_string("Context", "filesystems", ";".join(fs) + ";")
        else:
            try:
                kf.remove_key("Context", "filesystems")
            except GLib.Error:
                pass
        try:
            if kf.get_string("Environment", "GTK_THEME").startswith("Sonata-"):
                kf.remove_key("Environment", "GTK_THEME")
        except GLib.Error:
            pass
        for group in ("Context", "Environment"):
            try:
                if kf.has_group(group) and not kf.get_keys(group)[0]:
                    kf.remove_group(group)
            except GLib.Error:
                pass
    _edit(change)
