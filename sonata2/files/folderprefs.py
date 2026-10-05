"""Per-folder view settings (Finder keeps them per folder too): the view
(icons / list / columns), the list's sort column and direction, and
whether hidden files show (the toolbar's eye, Vini), saved
only for folders where you chose them. Other folders use the default view
(files.json "view") and Name, ascending.

    ~/.config/sonata2/files-folders.json   {"folders": {uri: {"view": "list", "sort": ["Size", true]}}}

The most recently changed folders are kept; past MAX the oldest go."""
from .. import config

NAME = "files-folders"
MAX = 1000
DEFAULT_SORT = ("Name", False)          # (column title, descending)
VIEWS = ("icons", "list", "columns")


def _all() -> dict:
    data = config.load(NAME, {"folders": {}})["folders"]
    return data if isinstance(data, dict) else {}


def get(uri: str) -> dict:
    """{"view": ..., "sort": (title, descending)} with only what was chosen here."""
    p = _all().get(uri or "")
    if not isinstance(p, dict):
        return {}
    out = {}
    if p.get("view") in VIEWS:
        out["view"] = p["view"]
    if isinstance(p.get("hidden"), bool):
        out["hidden"] = p["hidden"]
    s = p.get("sort")
    if isinstance(s, (list, tuple)) and len(s) == 2 and isinstance(s[0], str):
        out["sort"] = (s[0], bool(s[1]))
    return out


def moved(old: str, new: str) -> None:
    """A folder renamed: its settings follow it."""
    folders = _all()
    if old in folders:
        folders[new] = folders.pop(old)
        config.save(NAME, {"folders": folders})


def remember(uri: str, **values) -> None:
    """Save view= and/or sort= for this folder (moved to the newest end)."""
    if not uri:
        return
    folders = _all()
    p = folders.pop(uri, None)
    p = dict(p) if isinstance(p, dict) else {}
    for k, v in values.items():
        p[k] = list(v) if k == "sort" else v
    folders[uri] = p
    while len(folders) > MAX:
        folders.pop(next(iter(folders)))
    config.save(NAME, {"folders": folders})
