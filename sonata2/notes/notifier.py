"""Reminder alerts: a desktop notification (org.freedesktop.Notifications)
when a reminder falls due (all-day ones at 9:00).

Vini: "reminders don't notify" -- the only timer lived in the Notes window,
so with Notes closed nothing ever fired. ReminderWatch runs the same
Notifier in the menu bar process (always running, like Clock's alarms in
shell/alarmservice.py), reading reminders.json read-only and re-aiming on
every save Notes makes. The Notes window keeps its own Notifier (alerts
still work if the menu bar is missing); both share a small ledger file so a
reminder is announced once, whichever process sees it first.

No storm at login: a fresh Notifier only catches up on reminders due in the
last GRACE minutes; older overdue ones stay quiet (they show red in Notes).
Gio/GLib only -- no Gtk -- so the menu bar loads none of Notes' UI."""
import datetime
import json
import os
import time

from gi.repository import Gio, GLib

from ..config import atomic_write
from . import store as S

APP_NAME = "Reminders"
DESKTOP_ENTRY = "io.github.vinioliveiras.sonata2.notes"     # Reminders lives in Notes
GRACE = datetime.timedelta(minutes=5)
MAX_WAIT = 300               # seconds: a suspend or a clock change can't make an alert late by more
LEDGER = "reminders-notified.json"
LEDGER_KEEP = 3 * 86400      # seconds a sent mark is kept


def _key(r: dict) -> str:
    # the due time is part of the key: moving a reminder later notifies again
    return f"{r.get('id')}|{r.get('due')}"


class Notifier:
    """Notifies the reminders of `store` (a notes Store, or ReminderFile)
    falling due after `now - catch_up`; one timer aims at the next one."""

    def __init__(self, store, now: datetime.datetime = None, catch_up: datetime.timedelta = GRACE):
        self.store = store
        self.last = (now or datetime.datetime.now()) - catch_up
        self.timer = 0
        self.sent = []                     # (title, body): for tests and the log
        self.schedule()

    def schedule(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        now = datetime.datetime.now()
        nxt = self.store.next_due_after(self.last)
        if nxt is None:
            return
        secs = 0 if nxt <= now else (nxt - now).total_seconds() + 0.5
        self.timer = GLib.timeout_add_seconds(int(max(1, min(MAX_WAIT, secs))), self._fire)

    def _fire(self) -> bool:
        self.timer = 0
        self.check()
        return False

    def check(self, now: datetime.datetime = None) -> list:
        now = now or datetime.datetime.now()
        if now < self.last - GRACE:                       # the clock went back
            self.last = now - GRACE
        due = self.store.due_between(self.last, now)
        self.last = now
        if due:
            due = self._claim(due)
        for r in due:
            lst = self.store.rlist(r.get("list"))
            body = r.get("notes") or (lst["name"] if lst else "")
            self.notify(r.get("title") or "New Reminder", body)
        self.schedule()
        return due

    def _claim(self, due: list) -> list:
        """The reminders not announced yet (by this or the other process),
        marked as announced. Read right before writing: the window across
        processes is a few milliseconds."""
        path = os.path.join(self.store.dir, LEDGER)
        try:
            with open(path, encoding="utf-8") as f:
                sent = json.load(f)
            sent = sent if isinstance(sent, dict) else {}
        except (OSError, ValueError):
            sent = {}
        t = time.time()
        sent = {k: v for k, v in sent.items() if isinstance(v, (int, float)) and t - v < LEDGER_KEEP}
        fresh = [r for r in due if _key(r) not in sent]
        if fresh:
            sent.update((_key(r), t) for r in fresh)
            try:
                atomic_write(path, json.dumps(sent).encode("utf-8"), fsync=False)
            except OSError:
                pass
        return fresh

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))

        def got_bus(_src, res):
            try:
                bus = Gio.bus_get_finish(res)
            except GLib.Error:
                return
            bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications", "Notify",
                     GLib.Variant("(susssasa{sv}i)", (APP_NAME, 0, "x-office-calendar", title, body, [],
                                                      {"urgency": GLib.Variant("y", 1),
                                                       "desktop-entry": GLib.Variant("s", DESKTOP_ENTRY)}, -1)),
                     None, Gio.DBusCallFlags.NONE, -1, None, lambda b, r: _finish(b, r))
        Gio.bus_get(Gio.BusType.SESSION, None, got_bus)

    def stop(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0


def _finish(bus, res) -> None:
    try:
        bus.call_finish(res)
    except GLib.Error as e:
        print(f"sonata2 reminders: notification failed: {e.message}")


class ReminderFile(S.Store):
    """reminders.json, read-only (the menu bar never writes Notes' data:
    no purge, no save -- Notes is the only writer)."""

    def load(self) -> None:
        try:
            with open(self.reminders_path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = None                    # mid-replace or damaged: keep what we had
        if not isinstance(data, dict):
            if not hasattr(self, "reminders"):
                self.folders, self.notes, self.lists, self.reminders = [], [], [], []
            return
        self.folders, self.notes = [], []
        self.lists = [x for x in data.get("lists", []) if isinstance(x, dict) and x.get("id")]
        self.reminders = [x for x in data.get("reminders", []) if isinstance(x, dict) and x.get("id")]

    def save_notes(self) -> None:
        pass

    def save_reminders(self) -> None:
        pass


class ReminderWatch:
    """Reminder alerts with Notes closed (menu bar process)."""

    def __init__(self, folder: str = None, now: datetime.datetime = None):
        self.store = ReminderFile(folder)
        self.notifier = Notifier(self.store, now)
        self._mon = None
        try:
            self._mon = Gio.File.new_for_path(self.store.reminders_path).monitor_file(
                Gio.FileMonitorFlags.WATCH_MOVES, None)          # Notes saves by rename
            self._mon.connect("changed", lambda *_a: self.reload())
        except GLib.Error:
            pass

    def reload(self) -> None:
        self.store.load()
        self.notifier.schedule()

    def stop(self) -> None:
        self.notifier.stop()
        if self._mon is not None:
            self._mon.cancel()
