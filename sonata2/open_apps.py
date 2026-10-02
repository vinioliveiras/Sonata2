"""The apps that were open, for "Reopen Apps" after a crash (Vini).

The Dock notes the running apps (desktop ids) a few seconds after they
change, in ~/.cache/sonata2/open-apps.json. After a crash (feedback/report.py
crash note) the first note of the new session first keeps the old list as
open-apps.before-crash.json; Feedbacker offers to reopen those.

    note(["firefox", "org.gnome.Nautilus"])     # the Dock (debounced)
    before_crash() -> ["firefox", ...]           # Feedbacker
    reopen(ids)
"""
import json
import os

FILE = "open-apps.json"
BEFORE = "open-apps.before-crash.json"
DELAY_MS = 3000
SKIP = ("sonata2-launchpad", "io.github.vinioliveiras.sonata2.feedback")   # not "apps you had open"

_pending = {"src": 0, "ids": None}


def _dir() -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2")


def _read(name: str) -> list:
    try:
        with open(os.path.join(_dir(), name), encoding="utf-8") as f:
            data = json.load(f)
        return [i for i in data.get("apps", []) if isinstance(i, str)]
    except (OSError, ValueError, AttributeError):
        return []


def _write(name: str, ids) -> None:
    os.makedirs(_dir(), exist_ok=True)
    path = os.path.join(_dir(), name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"apps": list(ids)}, f)
    os.replace(tmp, path)


def rotate_after_crash() -> None:
    """The last session crashed and its list wasn't kept yet -> it becomes
    the "before crash" list (the Dock's first note, or Feedbacker -- whichever
    comes first; by the files' times, so only once)."""
    from .feedback import report
    c = report.crash()
    if not c:
        return
    src, dst = os.path.join(_dir(), FILE), os.path.join(_dir(), BEFORE)
    try:
        if os.path.getmtime(src) <= c["time"] + 5 and not (os.path.exists(dst) and
                                                           os.path.getmtime(dst) >= os.path.getmtime(src)):
            os.replace(src, dst)
    except OSError:
        pass


def wanted(ids) -> list:
    """Real apps (a desktop entry), Sonata's shell left out, in order, once."""
    from . import apps
    out = []
    for i in ids:
        if not i or i in SKIP or i in out or not apps.lookup(i):
            continue
        out.append(i)
    return out


def save_now(ids) -> None:
    rotate_after_crash()
    ids = wanted(ids)
    if ids != _read(FILE):
        try:
            _write(FILE, ids)
        except OSError:
            pass


def note(ids) -> None:
    """The Dock's running apps changed: saved a little later (a burst of
    windows, one write)."""
    from gi.repository import GLib
    _pending["ids"] = list(ids)
    if not _pending["src"]:
        def later():
            _pending["src"] = 0
            save_now(_pending["ids"] or [])
            return False
        _pending["src"] = GLib.timeout_add(DELAY_MS, later)


def before_crash() -> list:
    rotate_after_crash()
    return wanted(_read(BEFORE))


def names(ids) -> list:
    from . import apps
    out = []
    for i in ids:
        info = apps.lookup(i)
        out.append(info.get_display_name() if info else i)
    return out


def reopen(ids, launch=None) -> int:
    """Starts them, a little apart (not all at once at login); the list is
    then used up. Returns how many were started."""
    from gi.repository import GLib
    from . import apps
    infos = [apps.lookup(i) for i in ids]
    infos = [i for i in infos if i is not None]

    def start(info):
        try:
            (launch or (lambda inf: inf.launch([], None)))(info)
        except Exception as e:
            print(f"sonata2: reopen {info.get_id()}: {e}")
        return False
    for n, info in enumerate(infos):
        GLib.timeout_add(250 * n, start, info)
    try:
        os.remove(os.path.join(_dir(), BEFORE))
    except OSError:
        pass
    return len(infos)
