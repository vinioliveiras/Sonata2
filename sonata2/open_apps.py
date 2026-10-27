"""The apps that were open, for "Reopen Apps" after a crash, and to reopen
them at the next login after Restart / Shut Down / Log Out (Vini; macOS'
"Reopen windows when logging back in").

The Dock notes the running apps (desktop ids) a few seconds after they
change, in ~/.cache/sonata2/open-apps.json. After a crash (feedback/report.py
crash note) the first note of the new session first keeps the old list as
open-apps.before-crash.json; Feedbacker offers to reopen those.

Restart / Shut Down / Log Out with the box ticked (quitapps.end_session)
keep the list as open-apps.reopen.json before the apps quit; the next
session's Dock reopens them (reopen_after_login), leaving out the apps
already open by then ("Open at Login" ones).

    note(["firefox", "org.gnome.Nautilus"])     # the Dock (debounced)
    before_crash() -> ["firefox", ...]           # Feedbacker
    reopen(ids)
    save_for_login(ids) / drop_for_login()       # quitapps.end_session
    reopen_after_login(running)                  # the Dock, once per session
"""
import json
import os

FILE = "open-apps.json"
BEFORE = "open-apps.before-crash.json"
REOPEN = "open-apps.reopen.json"             # for the next login (Restart / Shut Down / Log Out)
CONFIG, CONFIG_DEFAULTS = "session", {"reopen": True}     # the box's last choice (ticked, like macOS)
DELAY_MS = 3000
SKIP = ("sonata2-launchpad", "io.github.vinioliveiras.sonata2.feedback")   # not "apps you had open"

_pending = {"src": 0, "ids": None}


def _dir() -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2")


def _read(name: str) -> list:
    try:
        with open(os.path.join(_dir(), name), encoding="utf-8") as f:
            data = json.load(f)
        apps = data.get("apps", [])
        return [i for i in apps if isinstance(i, str)] if isinstance(apps, list) else []
    except (OSError, ValueError, AttributeError):
        return []


def _write(name: str, ids) -> None:
    from .config import atomic_write
    # unique temp (Dock and Feedbacker both write here); a cache: no fsync on the main loop
    atomic_write(os.path.join(_dir(), name), json.dumps({"apps": list(ids)}).encode(), fsync=False)


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


def reopen_wanted() -> bool:
    from . import config
    return bool(config.load(CONFIG, CONFIG_DEFAULTS).get("reopen", True))


def set_reopen_wanted(on: bool) -> None:
    from . import config
    config.update(CONFIG, reopen=bool(on))


def current(manager=None) -> list:
    """The apps open now: the Dock's last note, then any window it didn't
    have yet (a note is a few seconds late)."""
    ids = list(_read(FILE))
    from . import apps
    for t in (getattr(manager, "toplevels", None) or []):
        key = apps.match_app_id(t.app_id or "") or (t.app_id or "")
        if key and key not in ids:
            ids.append(key)
    return wanted(ids)


def save_for_login(ids) -> None:
    try:
        _write(REOPEN, wanted(ids))
    except OSError:
        pass


def drop_for_login() -> None:
    try:
        os.remove(os.path.join(_dir(), REOPEN))
    except OSError:
        pass


def for_login() -> list:
    return wanted(_read(REOPEN))


def bare(desktop_id: str) -> str:
    """"firefox.desktop" and "firefox" are the same app."""
    return desktop_id[:-8] if desktop_id.endswith(".desktop") else desktop_id


def _autostarted() -> set:
    """Apps "Open at Login" starts anyway: not opened a second time."""
    try:
        from . import autostart
        return {info.get_id() for _name, info in autostart.entries() if info is not None}
    except Exception:
        return set()


def reopen_after_login(running=(), launch=None) -> int:
    """The new session's apps from the last one (Restart / Shut Down / Log
    Out with the box ticked), once: the list is used up. Apps open already
    (or that Open at Login starts) are left alone."""
    ids = for_login()
    drop_for_login()
    skip = {bare(i) for i in set(running) | _autostarted() if i}
    ids = [i for i in ids if bare(i) not in skip]
    if not ids:
        return 0
    return reopen(ids, launch)


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
            from . import applock
            with applock.trusted():        # reopened as they were: a locked app doesn't ask
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
