"""Resetting Sonata (Settings > About).

    settings_only()   every Sonata setting back to its default. What you made
                      stays: the Dock's apps and folders, Launchpad's layout,
                      protected apps, history, your apps' data.
    everything()      Sonata as just installed: all of the above goes too, its
                      caches are emptied (generated icons, thumbnails...) and
                      your apps' data (notes, calendars, TextEdit, Music:
                      ~/.local/share/sonata2-data) is moved to the Trash -- never
                      deleted outright.

The session's own files in ~/.config/sonata2 (wayfire.ini, the dconf profile,
fonts.conf, dotfile markers) are never touched: the next login needs them.
Both return what they removed (paths), for the log.
"""
import os
import shutil

from gi.repository import Gio, GLib

from . import config, userdata

# made by the session / install.sh, not settings
SESSION_FILES = {"wayfire.ini", "dconf-profile", "fonts.conf"}
# what you made, not settings: kept by settings_only()
CONTENT = {"launchpad.json", "activity-history.json", "files-folders.json", "security.json", "setup.json"}
DOCK_CONTENT = ("pinned", "folders", "stacks", "recent")     # dock.json: kept by settings_only()


def _config_files() -> list:
    try:
        names = sorted(os.listdir(config.CONFIG_DIR))
    except OSError:
        return []
    return [n for n in names if not n.startswith(".") and n not in SESSION_FILES
            and os.path.isfile(os.path.join(config.CONFIG_DIR, n))]


def _remove(path: str, gone: list) -> None:
    try:
        os.remove(path)
        gone.append(path)
    except OSError:
        pass


def settings_only() -> list:
    gone = []
    for name in _config_files():
        path = os.path.join(config.CONFIG_DIR, name)
        if name in CONTENT:
            continue
        if name == "dock.json":
            dock = config.load("dock", {k: None for k in DOCK_CONTENT})
            config.save("dock", {k: dock[k] for k in DOCK_CONTENT if dock.get(k) is not None})
            gone.append(path)
            continue
        _remove(path, gone)
    return gone


def everything() -> list:
    gone = [os.path.join(config.CONFIG_DIR, n) for n in _config_files()]
    for name in _config_files():
        _remove(os.path.join(config.CONFIG_DIR, name), [])
    cache = os.path.join(GLib.get_user_cache_dir(), "sonata2")
    try:
        names = os.listdir(cache)
    except OSError:
        names = []
    for name in names:
        if name.endswith(".log"):                    # the running parts write there (and help after)
            continue
        path = os.path.join(cache, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            _remove(path, [])
        gone.append(path)
    data = userdata.root()
    if os.path.isdir(data):
        try:
            Gio.File.new_for_path(data).trash(None)
            gone.append(data)
        except GLib.Error as e:
            print(f"sonata2: reset: couldn't move {data} to the Trash: {e.message}", flush=True)
    return gone
