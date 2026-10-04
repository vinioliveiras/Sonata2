"""Tells the user when a new Sonata is out (menu bar process): checks the
GitHub releases a while after login and then every few hours, and posts a
notification once per release; clicking it opens Software Update.
A check is a single small HTTPS request, in a thread."""
import time

from gi.repository import Gio, GLib

from .. import config
from ..backend import selfupdate

FIRST_S = 120            # after login: let the session settle
EVERY_S = 6 * 3600


class UpdateNotifier:
    def __init__(self, check=None, now=time.time):
        self._check = check or selfupdate.available
        self._now = now
        self.sent = []                       # (tests)
        self._id = 0
        self._sub = 0
        self.timer = GLib.timeout_add_seconds(FIRST_S, self._tick)

    def _tick(self) -> bool:
        from ..backend import system
        system.run_async(self._check, self.found)
        self.timer = GLib.timeout_add_seconds(EVERY_S, self._tick)
        return False

    def found(self, rel) -> None:
        """A newer release (or None): notify once per tag."""
        config.update(selfupdate.NAME, last_check=int(self._now()))
        if not rel:
            return
        data = config.load(selfupdate.NAME, selfupdate.DEFAULTS)
        if data.get("notified") == rel["tag"]:
            return
        config.update(selfupdate.NAME, notified=rel["tag"])
        self.notify(rel)

    def notify(self, rel) -> None:
        version = rel["tag"].lstrip("vV")
        title, body = f"Sonata {version} is available", "Click to see what's new and update."
        self.sent.append((title, body))
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        if not self._sub:
            self._sub = bus.signal_subscribe(None, "org.freedesktop.Notifications", "ActionInvoked",
                                             "/org/freedesktop/Notifications", None, Gio.DBusSignalFlags.NONE,
                                             self._invoked)

        def sent(b, res):
            try:
                self._id = b.call_finish(res).unpack()[0]
            except GLib.Error as e:
                print(f"sonata2 updates: notification failed: {e.message}")
        bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications", "Notify",
                 GLib.Variant("(susssasa{sv}i)", ("Software Update", 0, "software-update-available", title, body,
                                                  ["default", "Update", "update", "Update…"],
                                                  {"urgency": GLib.Variant("y", 1)}, -1)),
                 None, Gio.DBusCallFlags.NONE, -1, None, sent)

    def _invoked(self, _c, _s, _p, _i, _sig, params) -> None:
        nid, _key = params.unpack()
        if nid == self._id and self._id:
            open_updates()

    def stop(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0


def open_updates() -> None:
    """Settings > About (Software Update)."""
    import subprocess
    from ..__main__ import self_argv
    subprocess.Popen(self_argv() + ["settings", "--page", "sonataupdate"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
