"""An app that doesn't open is opened again (Vini: Steam above all).

Opened from the Dock, Launchpad or Files and no window of it this long
after: its processes are killed (appscope.kill) and it is opened again, up
to RETRIES times; then a notification says it couldn't open. An app that
can't be started at all says so at once.

    launchwatch.open(info, lambda: info.launch([], ctx))     # Launchpad, Files
    launchwatch.has_window(info)                             # a window of it is open (Wayfire's list)

The Dock keeps its own watch (it already follows every window and bounces
the icon): it uses the same timings and failed().
"""
from gi.repository import GLib

WATCH_S = 30
SLOW_S = {"steam": 90, "steam-native": 90, "steam-runtime": 90}    # updates itself first
RETRIES = 3
RELAUNCH_MS = 900


def key_of(info) -> str:
    return ((info.get_id() if info is not None else "") or "").removesuffix(".desktop")


def wait_s(info) -> int:
    return SLOW_S.get(key_of(info), WATCH_S)


def has_window(info, views=None, unit=None) -> bool:
    """A window of the app is open: its app id names the app's entry, or
    its process is in the app's launch scope (`unit`)."""
    if views is None:
        try:
            from .wl.wfipc import WayfireIPC
            views = WayfireIPC().call("window-rules/list-views")
        except Exception:
            return True                    # can't tell: never kill on a guess
    from . import apps
    key = key_of(info)
    for v in views if isinstance(views, list) else []:
        if not isinstance(v, dict) or v.get("type", "toplevel") != "toplevel":
            continue
        aid = v.get("app-id") or ""
        if aid and (aid == key or (apps.match_app_id(aid) or "").removesuffix(".desktop") == key):
            return True
        pid = v.get("pid")
        if unit and isinstance(pid, int) and pid > 0:
            try:
                with open(f"/proc/{pid}/cgroup", encoding="utf-8") as f:
                    if unit in f.read():
                        return True
            except OSError:
                pass
    return False


def failed(info, why: str) -> None:
    """The notification: it couldn't open."""
    from . import notify
    name = info.get_display_name() if info is not None else "The app"
    icon = info.get_icon() if info is not None else None
    notify.send(f"“{name}” couldn't open", why + " Try opening it again in a moment.", app="Sonata",
                icon=icon.to_string() if icon is not None else "",
                desktop=(info.get_id() or "") if info is not None else "")


def open(info, launch, tries: int = 0, already_open=None) -> bool:      # noqa: A001 (the natural name)
    """Run `launch()` (it starts `info`), then watch it. False: it couldn't
    be started (the notification is out). already_open: True when the app
    had a window before (a file handed to it: nothing to watch)."""
    if tries == 0 and already_open is None:
        already_open = has_window(info)
    try:
        launch()
    except GLib.Error as e:
        failed(info, e.message)
        return False
    if not already_open:
        GLib.timeout_add_seconds(wait_s(info), lambda: (_check(info, launch, tries), False)[1])
    return True


def _check(info, launch, tries: int) -> None:
    from . import appscope
    if has_window(info, unit=appscope.launched.get(info.get_id() or "")):
        return
    appscope.kill(info.get_id() or "", info)
    if tries >= RETRIES:
        failed(info, f"It was closed and opened again {RETRIES} times and still didn't show a window.")
        return
    print(f"sonata2: {key_of(info)} showed no window in {wait_s(info)} s: killed, opening it again "
          f"({tries + 1}/{RETRIES})", flush=True)
    GLib.timeout_add(RELAUNCH_MS, lambda: (open(info, launch, tries + 1, already_open=False), False)[1])
