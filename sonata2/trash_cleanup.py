"""Empties old Trash items (Settings > Security & Privacy: "Remove items
from the Trash after 30 days"; GNOME's housekeeping does this elsewhere).

Reads the freedesktop Trash of the home folder: each item in files/ has an
info/<name>.trashinfo with its DeletionDate. Items deleted more than
`days` ago go for good. The menu bar runs it at login and every 6 hours,
off the main loop, only while the setting is on (org.gnome.desktop.privacy
remove-old-trash-files / old-files-age, the keys Settings writes)."""
import datetime as dt
import os
import shutil

from gi.repository import GLib

EVERY_S = 6 * 3600


def trash_dir() -> str:
    return os.path.join(GLib.get_user_data_dir(), "Trash")


def deletion_date(info_path: str):
    try:
        with open(info_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("DeletionDate="):
                    when = dt.datetime.fromisoformat(line.split("=", 1)[1].strip())
                    if when.tzinfo is not None:      # "...Z" from some tools: local naive like the spec
                        when = when.astimezone().replace(tzinfo=None)
                    return when
    except (OSError, ValueError):
        pass
    return None


def purge(days: int, root: str = None, now: dt.datetime = None) -> int:
    """Remove items deleted more than `days` days ago; returns how many."""
    root = root or trash_dir()
    now = now or dt.datetime.now()
    info_dir, files_dir = os.path.join(root, "info"), os.path.join(root, "files")
    gone = 0
    try:
        names = os.listdir(info_dir)
    except OSError:
        return 0
    for n in names:
        if not n.endswith(".trashinfo"):
            continue
        when = deletion_date(os.path.join(info_dir, n))
        if when is None or now - when < dt.timedelta(days=days):
            continue
        item = os.path.join(files_dir, n[:-len(".trashinfo")])
        try:
            if os.path.isdir(item) and not os.path.islink(item):
                shutil.rmtree(item)
            elif os.path.lexists(item):
                os.unlink(item)
            os.unlink(os.path.join(info_dir, n))
            gone += 1
        except OSError:
            continue
    return gone


class Housekeeping:
    """The menu bar's timer: purge when the setting is on."""

    def __init__(self):
        from .backend import system
        self.system = system
        GLib.timeout_add_seconds(60, lambda: (self._run(), False)[1])      # once login settled
        GLib.timeout_add_seconds(EVERY_S, self._run)

    def _run(self) -> bool:
        def work():
            P = "org.gnome.desktop.privacy"
            if self.system.gsetting(P, "remove-old-trash-files") != "true":
                return 0
            try:
                days = int(self.system.gsetting(P, "old-files-age") or 30)
            except ValueError:
                days = 30
            return purge(max(1, days))
        self.system.run_async(work, None)
        return True
