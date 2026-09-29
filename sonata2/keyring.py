"""Sonata's keyring: KeePassXC (not GNOME's or KDE's). Apps that save
passwords (Chrome, VS Code, NetworkManager's Wi-Fi secrets...) ask the
freedesktop Secret Service; KeePassXC provides it once its "Secret Service
Integration" is on. At every login (`sonata2 autostart`) Sonata turns that
on, if you never set it yourself, and starts KeePassXC minimized to the tray.
Unlock your database once and apps can read and save their passwords."""
import configparser
import os
import shutil
import subprocess

from gi.repository import GLib

CONFIG = "~/.config/keepassxc/keepassxc.ini"


def enable_secret_service(path: str) -> bool:
    """[FdoSecrets] Enabled=true unless the user chose otherwise. True: written."""
    cfg = configparser.RawConfigParser(strict=False)
    cfg.optionxform = str                        # keep KeePassXC's key case
    try:
        cfg.read(path, encoding="utf-8")
    except configparser.Error:
        return False
    if cfg.has_option("FdoSecrets", "Enabled"):
        return False                             # your choice stands
    if not cfg.has_section("FdoSecrets"):
        cfg.add_section("FdoSecrets")
    cfg.set("FdoSecrets", "Enabled", "true")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f, space_around_delimiters=False)
    return True


def _running() -> bool:
    return subprocess.run(["pgrep", "-x", "keepassxc"], stdout=subprocess.DEVNULL).returncode == 0


def start() -> bool:
    """At login: KeePassXC with its Secret Service, minimized."""
    exe = shutil.which("keepassxc")
    if not exe:
        return False
    if not _running():                  # (a running KeePassXC writes its settings back on quit)
        enable_secret_service(os.path.expanduser(CONFIG))
        GLib.spawn_async([exe, "--minimized"], flags=GLib.SpawnFlags.DEFAULT)
    return True
