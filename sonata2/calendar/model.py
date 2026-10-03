"""Calendar's data and layout logic (no GTK): the store of calendars (one
.ics file each), the macOS calendar colour palette, month grids, week
start, and how overlapping events share a column (timeline) or a lane
(month rows, all-day strip)."""
import collections
import datetime as dt
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import ics
from ..config import atomic_write

# macOS Calendar's colour palette: (id, name, token or colour). The Apple
# system colours already are tokens (sys_*); yellow and brown are not, so
# they are given here -- the only colours of this module.
PALETTE = (("red", "Red", "sys_red"), ("orange", "Orange", "sys_orange"), ("yellow", "Yellow", "#ffcc00"),
           ("green", "Green", "sys_green"), ("blue", "Blue", "sys_blue"), ("purple", "Purple", "sys_purple"),
           ("brown", "Brown", "#a2845e"))
PALETTE_IDS = tuple(p[0] for p in PALETTE)
DEFAULT_CALENDARS = (("Home", "blue"), ("Work", "red"))
# the hex written as X-APPLE-CALENDAR-COLOR (other apps' view of the colour)
PALETTE_HEX = {"red": "#ff3b30", "orange": "#ff9500", "yellow": "#ffcc00", "green": "#34c759",
               "blue": "#007aff", "purple": "#af52de", "brown": "#a2845e"}


def color_from_hex(hex_color: str) -> str:
    """The palette colour nearest to a hex colour (imported calendars)."""
    try:
        r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "blue"

    def dist(item):
        h = item[1].lstrip("#")
        return sum((int(h[i:i + 2], 16) - c) ** 2 for i, c in zip((0, 2, 4), (r, g, b)))
    return min(PALETTE_HEX.items(), key=dist)[0]


# -- dates ------------------------------------------------------------------------------------
def locale_first_weekday() -> int:
    """First day of the week of the current locale, Python numbering
    (Monday 0 ... Sunday 6); Sunday when unknown (US, macOS default)."""
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        libc.nl_langinfo.restype = ctypes.c_void_p
        origin = libc.nl_langinfo(0x20066)                # _NL_TIME_WEEK_1STDAY (a date as a number)
        first = ctypes.string_at(libc.nl_langinfo(0x20068), 1)[0]   # _NL_TIME_FIRST_WEEKDAY, 1 = origin
        base = {19971130: 6, 19971201: 0}.get(origin)       # the origin is a Sunday / a Monday
        if base is None or not 1 <= first <= 7:
            return 6
        return (base + first - 1) % 7
    except Exception:
        return 6


def week_start(d: dt.date, first_weekday: int = 6) -> dt.date:
    return d - dt.timedelta(days=(d.weekday() - first_weekday) % 7)


def month_grid(year: int, month: int, first_weekday: int = 6) -> List[dt.date]:
    """The 42 dates (6 weeks) of a month page, starting on first_weekday."""
    start = week_start(dt.date(year, month, 1), first_weekday)
    return [start + dt.timedelta(days=i) for i in range(42)]


def day_start(d) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day)


# -- layout -----------------------------------------------------------------------------------
def columns(occs) -> Dict[ics.Occurrence, tuple]:
    """Timeline layout: occurrences that overlap sit side by side. Returns
    {occurrence: (column, columns in its group)}; a group is a run of
    events that overlap one another (transitively)."""
    out = {}
    group, cols, group_end = [], [], None     # cols: end time of the last event in each column

    def flush():
        for o, c in group:
            out[o] = (c, len(cols))
    for o in sorted(occs, key=lambda o: (o.start, -(o.end - o.start).total_seconds())):
        end = max(o.end, o.start + dt.timedelta(minutes=15))    # tiny events still take room
        if group_end is not None and o.start >= group_end:
            flush()
            group, cols, group_end = [], [], None
        for i, c_end in enumerate(cols):
            if c_end <= o.start:
                cols[i] = end
                group.append((o, i))
                break
        else:
            cols.append(end)
            group.append((o, len(cols) - 1))
        group_end = end if group_end is None else max(group_end, end)
    flush()
    return out


def is_multiday(o: ics.Occurrence) -> bool:
    """Shown as a bar (all-day strip, month bars) rather than a timed pill."""
    if o.event.all_day:
        return True
    return (o.end - day_start(o.start)) > dt.timedelta(days=1)


def lanes(occs, first_day: dt.date, ndays: int) -> List[tuple]:
    """Bars over a row of ndays days (a month week, the all-day strip):
    [(occurrence, first column, last column, lane)], each in the first
    lane free over all its days."""
    row_a = day_start(first_day)
    used = []                                 # used[lane] = set of columns
    out = []
    for o in sorted(occs, key=lambda o: (day_start(o.start), -(o.end - o.start).total_seconds(), o.start)):
        s = max(0, (day_start(o.start) - row_a).days)
        last_moment = o.end - dt.timedelta(microseconds=1) if o.end > o.start else o.end
        e = min(ndays - 1, (day_start(last_moment) - row_a).days)
        if e < 0 or s > ndays - 1:
            continue
        span = set(range(s, e + 1))
        for lane, cols in enumerate(used):
            if not cols & span:
                cols |= span
                out.append((o, s, e, lane))
                break
        else:
            used.append(set(span))
            out.append((o, s, e, len(used) - 1))
    return out


# -- store ------------------------------------------------------------------------------------
@dataclass
class Calendar:
    id: str
    name: str
    color: str = "blue"
    extra: list = field(default_factory=list)   # the file's other blocks (VTIMEZONE...), kept


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "calendar"


class Store:
    """Calendars and their events, one .ics file per calendar in `folder`."""

    def __init__(self, folder: str):
        self.folder = folder
        self.calendars: List[Calendar] = []
        self.events: Dict[str, ics.Event] = {}
        self._lock = threading.Lock()        # held while a file is written or removed
        self.async_writes = False            # the window writes files off the main loop
        # async: one worker runs the file jobs in the order they were made,
        # so an older text never wins and a delete can't be undone by a write
        self._jobs = collections.deque()
        self._jobs_lock = threading.Lock()
        self._worker = None

    def load(self) -> None:
        os.makedirs(self.folder, exist_ok=True)
        self.calendars, self.events = [], {}
        names = sorted(n for n in os.listdir(self.folder) if n.endswith(".ics"))
        for n in names:
            try:
                with open(os.path.join(self.folder, n), encoding="utf-8", errors="replace") as f:
                    info, events = ics.parse(f.read(), n[:-4])
            except OSError:
                continue
            color = info.get("color") if info.get("color") in PALETTE_IDS else \
                color_from_hex(info["hex"]) if info.get("hex") else "blue"
            self.calendars.append(Calendar(n[:-4], info.get("name") or n[:-4].title(), color,
                                           info.get("extra", [])))
            for ev in events:
                self.events[ev.uid] = ev
        if not self.calendars:
            for name, color in DEFAULT_CALENDARS:
                self.add_calendar(name, color)

    def calendar(self, cal_id: str) -> Optional[Calendar]:
        return next((c for c in self.calendars if c.id == cal_id), None)

    def _write(self, cal_id: str) -> None:
        cal = self.calendar(cal_id)
        if cal is None:
            return
        text = ics.serialize(sorted((e for e in self.events.values() if e.calendar == cal_id),
                                    key=lambda e: e.start), cal.name, cal.color, PALETTE_HEX.get(cal.color, ""),
                             cal.extra)
        path = os.path.join(self.folder, cal_id + ".ics")
        self._submit(lambda: atomic_write(path, text.encode("utf-8")))

    def _submit(self, job) -> None:
        if not self.async_writes:
            with self._lock:
                job()
            return
        with self._jobs_lock:
            self._jobs.append(job)
            if self._worker is None:
                # not a daemon: quitting waits for the last write (the worker ends when idle)
                self._worker = threading.Thread(target=self._run_jobs, daemon=False)
                self._worker.start()

    def _run_jobs(self) -> None:
        while True:
            with self._jobs_lock:
                if not self._jobs:
                    self._worker = None
                    return
                job = self._jobs.popleft()
            with self._lock:
                try:
                    job()
                except OSError as e:
                    print(f"sonata2 calendar: not saved: {e}")

    # calendars
    def add_calendar(self, name: str, color: str = None) -> Calendar:
        taken = {c.id for c in self.calendars}
        base = _slug(name)
        cid, i = base, 2
        while cid in taken or os.path.exists(os.path.join(self.folder, cid + ".ics")):
            cid, i = f"{base}-{i}", i + 1
        if color is None:
            used = [c.color for c in self.calendars]
            color = min(PALETTE_IDS, key=lambda p: (used.count(p), PALETTE_IDS.index(p)))
        cal = Calendar(cid, name, color)
        self.calendars.append(cal)
        self._write(cid)
        return cal

    def update_calendar(self, cal_id: str, name: str = None, color: str = None) -> None:
        cal = self.calendar(cal_id)
        if cal is None:
            return
        if name:
            cal.name = name
        if color in PALETTE_IDS:
            cal.color = color
        self._write(cal_id)

    def delete_calendar(self, cal_id: str) -> None:
        self.calendars = [c for c in self.calendars if c.id != cal_id]
        self.events = {u: e for u, e in self.events.items() if e.calendar != cal_id}
        path = os.path.join(self.folder, cal_id + ".ics")

        def remove():
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
        self._submit(remove)             # after the writes queued before it

    # events
    def put(self, ev: ics.Event) -> None:
        """Add or replace an event (by UID); both calendars' files are written
        when it moved from one to another."""
        old = self.events.get(ev.uid)
        self.events[ev.uid] = ev
        self._write(ev.calendar)
        if old is not None and old.calendar != ev.calendar:
            self._write(old.calendar)

    def remove(self, uid: str) -> Optional[ics.Event]:
        ev = self.events.pop(uid, None)
        if ev is not None:
            self._write(ev.calendar)
        return ev

    def visible_events(self, hidden=()) -> List[ics.Event]:
        return [e for e in self.events.values() if e.calendar not in hidden]

    def import_text(self, text: str, cal_id: str) -> int:
        """Add the events of an .ics text to a calendar (same UID: replaced)."""
        info, events = ics.parse(text, cal_id)
        cal = self.calendar(cal_id)
        if cal is not None:                  # zone definitions the imported events refer to
            cal.extra += [b for b in info.get("extra", [])
                          if b and b[0].upper() == "BEGIN:VTIMEZONE" and b not in cal.extra]
        for ev in events:
            old = self.events.get(ev.uid)
            if old is not None and old.calendar != cal_id:
                self.events.pop(ev.uid)
                self._write(old.calendar)
            self.events[ev.uid] = ev
        if events:
            self._write(cal_id)
        return len(events)
