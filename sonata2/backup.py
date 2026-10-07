"""Sonata's settings in one file (Settings > About > Backup, Vini).

    export(path, data=True)    a .sonata-backup (a zip): ~/.config/sonata2 --
                               appearance, Dock, Launchpad, shortcuts,
                               permissions, sounds... -- and, with data, the
                               data of Sonata's own apps (notes, calendars,
                               reminders: userdata.root())
    read_manifest(path)        what a backup holds (None: not one)
    restore(path)              its settings (and data) in place of yours;
                               yours go to the Trash first

The session's own files (wayfire.ini and the like, factory_reset.SESSION_FILES)
are never in a backup: each computer makes its own.
"""
import json
import os
import time
import zipfile

from gi.repository import Gio, GLib

from . import __version__, config, userdata
from .factory_reset import SESSION_FILES

FORMAT = 1
EXT = ".sonata-backup"
MANIFEST = "sonata-backup.json"
NAME = "backup"                         # backup.json: when it was last exported


def default_name() -> str:
    return time.strftime("Sonata Settings %Y-%m-%d") + EXT


def _walk(root: str, top: str, skip_top=()):
    """(path on disk, name in the zip) of every file under root."""
    if not os.path.isdir(root):
        return
    for base, dirs, files in os.walk(root):
        rel = os.path.relpath(base, root)
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and not (rel == "." and d in skip_top))
        for f in sorted(files):
            if f.startswith(".") or (rel == "." and f in skip_top):
                continue
            p = os.path.join(base, f)
            if os.path.islink(p) or not os.path.isfile(p):
                continue
            yield p, "/".join(x for x in (top, "" if rel == "." else rel, f) if x)


def export(path: str, data: bool = True) -> int:
    """Blocking. Returns how many files went in."""
    n = 0
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(MANIFEST, json.dumps({"format": FORMAT, "sonata": __version__, "data": bool(data),
                                         "created": int(time.time())}, indent=1))
        for p, name in _walk(config.CONFIG_DIR, "config", SESSION_FILES):
            z.write(p, name)
            n += 1
        if data:
            for p, name in _walk(userdata.root(), "data"):
                z.write(p, name)
                n += 1
    os.replace(tmp, path)
    config.update(NAME, last_export=int(time.time()))
    return n


def last_export():
    """When settings were last exported (epoch seconds), None if never."""
    return config.load(NAME, {"last_export": None}).get("last_export")


def read_manifest(path: str):
    try:
        with zipfile.ZipFile(path) as z:
            m = json.loads(z.read(MANIFEST))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None
    return m if isinstance(m, dict) and m.get("format") == FORMAT else None


def _safe(name: str) -> bool:
    parts = name.split("/")
    return bool(name) and not name.startswith("/") and ".." not in parts and "\\" not in name


def _trash_all(root: str, skip_top=()) -> None:
    """What's in root, to the Trash (put back from there); session files stay."""
    try:
        names = os.listdir(root)
    except OSError:
        return
    for n in names:
        if n.startswith(".") or n in skip_top:
            continue
        try:
            Gio.File.new_for_path(os.path.join(root, n)).trash(None)
        except GLib.Error:
            pass


def restore(path: str) -> bool:
    """Blocking. False: not a Sonata backup (nothing changed)."""
    m = read_manifest(path)
    if m is None:
        return False
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n != MANIFEST and _safe(n) and not n.endswith("/")]
        has_data = any(n.startswith("data/") for n in names)
        _trash_all(config.CONFIG_DIR, SESSION_FILES)
        if has_data:
            _trash_all(userdata.root())
        for n in names:
            top, _, rest = n.partition("/")
            if top == "config" and rest.split("/")[0] not in SESSION_FILES:
                dest = os.path.join(config.CONFIG_DIR, rest)
            elif top == "data":
                dest = os.path.join(userdata.root(), rest)
            else:
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with z.open(n) as src, open(dest, "wb") as out:
                out.write(src.read())
    return True
