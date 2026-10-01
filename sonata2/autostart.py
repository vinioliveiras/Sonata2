"""XDG autostart for the Sonata session (Wayfire doesn't run it).

Starts the apps in ~/.config/autostart and $XDG_CONFIG_DIRS/xdg/autostart
("Open at Login" in the Dock writes there), following the spec: user
entries override system ones with the same file name; Hidden=true,
OnlyShowIn/NotShowIn (desktop "Sonata") and TryExec are honoured.
`sonata2 autostart` runs once at session start."""
import os

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

DESKTOP = "Sonata"


def _dirs() -> list:
    """Highest priority first: user dir, then system dirs."""
    dirs = [os.path.join(GLib.get_user_config_dir(), "autostart")]
    dirs += [os.path.join(d, "autostart") for d in GLib.get_system_config_dirs()]
    return dirs


def entries() -> list:
    """(file name, Gio.DesktopAppInfo) of the entries to start."""
    seen, out = set(), []
    for d in _dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".desktop") or name in seen:
                continue
            seen.add(name)                     # a user entry hides the system one
            kf = GLib.KeyFile()
            try:
                kf.load_from_file(os.path.join(d, name), GLib.KeyFileFlags.NONE)
            except GLib.Error:
                continue
            if not _wanted(kf):
                continue
            try:
                from .apps import DesktopAppInfo
                info = DesktopAppInfo.new_from_keyfile(kf)
            except TypeError:
                info = None
            if info:
                out.append((name, info))
    return out


def _get(kf, key, kind="string"):
    try:
        return getattr(kf, f"get_{kind}")("Desktop Entry", key)
    except GLib.Error:
        return None


def _wanted(kf) -> bool:
    if _get(kf, "Hidden", "boolean") or _get(kf, "X-GNOME-Autostart-enabled", "boolean") is False:
        return False
    only = _get(kf, "OnlyShowIn", "string_list")
    if only and DESKTOP not in only:
        return False
    if DESKTOP in (_get(kf, "NotShowIn", "string_list") or []):
        return False
    try_exec = _get(kf, "TryExec")
    if try_exec and not GLib.find_program_in_path(try_exec):
        return False
    return True


def run() -> int:
    from .trace import mark
    mark("autostart: title bars")
    from . import titlebars
    try:
        titlebars.apply()                  # Sonata title bars for Chrome, VS Code... (before they start)
    except Exception as e:                 # never keep the login's apps from starting
        print(f"sonata2-autostart: title bars: {e}")
    mark("autostart: setup check")
    try:
        from . import config
        if not config.load("setup", {"done": False})["done"]:     # first login: the Setup Assistant
            from .__main__ import self_command
            GLib.spawn_command_line_async(self_command() + " setup")
    except Exception as e:
        print(f"sonata2-autostart: setup: {e}")
    mark("autostart: polkit")
    try:                                   # Sonata's password prompt (polkit agent), kept alive
        import subprocess
        if subprocess.run(["pgrep", "-f", r"-m sonata2 keep polkit( |$)"],
                          stdout=subprocess.DEVNULL).returncode != 0:
            from .__main__ import self_command
            GLib.spawn_command_line_async(self_command() + " keep polkit")
    except Exception as e:
        print(f"sonata2-autostart: polkit: {e}")
    mark("autostart: files")
    try:                                   # Files resident (like Finder): windows open at once
        import subprocess
        if subprocess.run(["pgrep", "-f", r"-m sonata2 files( |$)"],
                          stdout=subprocess.DEVNULL).returncode != 0:
            from .__main__ import self_command
            GLib.spawn_command_line_async(self_command() + " files --background")
    except Exception as e:
        print(f"sonata2-autostart: files: {e}")
    mark("autostart: gtk style")
    try:
        from . import gtkstyle
        # (gtk.css keeps only adwstyle's import line now, written by titlebars.apply above)
        mark("autostart: reset env")
        gtkstyle.reset_env()               # (and forced GTK_THEME on D-Bus activated apps)
        mark("autostart: flatpak theme")
        from . import flatpak_theme
        flatpak_theme.apply()              # Flatpak apps in Sonata's look (Settings > General)
    except Exception as e:
        print(f"sonata2-autostart: gtk style: {e}")
    mark("autostart: keyring")
    try:
        from . import keyring
        keyring.start()                    # KeePassXC: saved passwords for every app
    except Exception as e:
        print(f"sonata2-autostart: keyring: {e}")
    mark("autostart: login items")
    started = 0
    for name, info in entries():
        try:
            mark(f"autostart: {name}")
            info.launch([], None)
            started += 1
        except GLib.Error as e:
            print(f"sonata2-autostart: {name}: {e.message}")
    return started
