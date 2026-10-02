"""Where Sonata's apps keep your data: notes, calendars, TextEdit's open
tabs, Music's playlists -- ~/.local/share/sonata2-data/<app>.

They used to live in ~/.local/share/sonata2/<app>, the folder Sonata itself
is installed in: install.sh replaces that folder on every update (your
notes and calendars went with it), and a dev install links it to the git
clone (calendars and TextEdit's tabs showed up in the repository). The
first time an app asks for its folder, what's in the old place moves over.

    userdata.folder("notes")      # ".../sonata2-data/notes", created
"""
import os
import shutil

from gi.repository import GLib

NAME = "sonata2-data"
APPS = ("notes", "calendar", "textedit", "music", "webapps")      # what install.sh carries over too


def root() -> str:
    return os.path.join(GLib.get_user_data_dir(), NAME)


def old_folder(app: str) -> str:
    return os.path.join(GLib.get_user_data_dir(), "sonata2", app)


def folder(app: str) -> str:
    """The app's data folder (created), with the old one's content moved in."""
    path = os.path.join(root(), app)
    if not os.path.isdir(path):
        migrate(app)
    os.makedirs(path, exist_ok=True)
    return path


def migrate(app: str) -> bool:
    """Move ~/.local/share/sonata2/<app> (or wherever it points: a dev
    install's git clone) to the data folder, unless that already exists."""
    old, new = old_folder(app), os.path.join(root(), app)
    if os.path.lexists(new) or not os.path.isdir(old):
        return False
    try:
        os.makedirs(root(), exist_ok=True)
        shutil.move(os.path.realpath(old), new)
        return True
    except OSError:
        return False
