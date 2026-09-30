"""Event alerts while Calendar runs: a desktop notification
(org.freedesktop.Notifications, over Gio D-Bus) at each alert time.

One GLib timeout waits for the next alert (at most 5 minutes, so a
suspend or a clock change is caught up soon); no polling in between."""
import datetime as dt

from . import ics

HORIZON = dt.timedelta(days=2)
MAX_WAIT = 300              # seconds


def due(events, after: dt.datetime, until: dt.datetime):
    """[(alert time, occurrence)] with an alert time in (after, until],
    sorted by time."""
    out = []
    for ev in events:
        if ev.alarm is None:
            continue
        lead = dt.timedelta(minutes=ev.alarm)
        # occurrences starting in (after + lead, until + lead]
        for o in ics.occurrences(ev, after + lead, until + lead + dt.timedelta(seconds=1)):
            t = o.start - lead
            if after < t <= until:
                out.append((t, o))
    out.sort(key=lambda x: x[0])
    return out


class Alerts:
    """Fires the alerts of `events()` (a callable) through `notify(occ)`."""

    def __init__(self, events, notify):
        self.events, self.notify = events, notify
        self.last = dt.datetime.now()
        self._src = 0

    def reschedule(self) -> None:
        from gi.repository import GLib
        if self._src:
            GLib.source_remove(self._src)
            self._src = 0
        now = dt.datetime.now()
        upcoming = due(self.events(), self.last, now + HORIZON)
        wait = MAX_WAIT
        if upcoming:
            wait = max(1, min(MAX_WAIT, int((upcoming[0][0] - now).total_seconds() + 0.999)))
        self._src = GLib.timeout_add_seconds(wait, self._fire)

    def _fire(self) -> bool:
        self._src = 0
        now = dt.datetime.now()
        if now < self.last:                    # the clock went back
            self.last = now
        for _t, occ in due(self.events(), self.last, now):
            self.notify(occ)
        self.last = now
        self.reschedule()
        return False

    def stop(self) -> None:
        from gi.repository import GLib
        if self._src:
            GLib.source_remove(self._src)
            self._src = 0


def send_notification(summary: str, body: str) -> None:
    """Notify over the session bus, asynchronously (errors are ignored: no
    notification daemon means no alert, never a crash)."""
    from gi.repository import Gio, GLib

    def got_bus(_src, res):
        try:
            bus = Gio.bus_get_finish(res)
        except GLib.Error:
            return
        bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications", "Notify",
                 GLib.Variant("(susssasa{sv}i)", ("Calendar", 0, "x-office-calendar", summary, body, [],
                                                  {"desktop-entry": GLib.Variant(
                                                      "s", "io.github.vinioliveiras.sonata2.calendar"),
                                                   "category": GLib.Variant("s", "x-calendar")}, -1)),
                 None, Gio.DBusCallFlags.NONE, 3000, None, lambda b, r: _finish(b, r))
    Gio.bus_get(Gio.BusType.SESSION, None, got_bus)


def _finish(bus, res):
    from gi.repository import GLib
    try:
        bus.call_finish(res)
    except GLib.Error:
        pass
