"""A minimal iCalendar (RFC 5545) reader/writer for Calendar, and recurrence
expansion. Pure Python (no GTK), so it is easy to test.

Supported subset: VCALENDAR with X-WR-CALNAME / X-APPLE-CALENDAR-COLOR /
X-SONATA-COLOR, VEVENT with UID, SUMMARY, LOCATION, DESCRIPTION, DTSTART /
DTEND (floating local time, UTC "Z" or TZID -- both converted to local
time -- or VALUE=DATE), DURATION (when there is no DTEND), RRULE
FREQ / INTERVAL / UNTIL / COUNT (+ WEEKLY BYDAY), EXDATE, RECURRENCE-ID
(a changed instance, kept with its series) and one VALARM TRIGGER (relative
to the start). Times are kept as naive local datetimes; an event's zone
(UTC or TZID) is remembered, so a series repeats in that zone (right
across DST changes) and is written back in it. Anything else -- other
RRULE parts, properties, components (VTIMEZONE, ATTENDEE...) -- is kept
verbatim and written back. An all-day event runs from midnight of its
first day to midnight after its last."""
import datetime as dt
import functools
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Iterator, List, Optional, Tuple

FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_RRULE_KNOWN = ("FREQ", "INTERVAL", "COUNT", "UNTIL")
# properties parse() turns into fields (DTSTAMP is written fresh); the rest is kept verbatim
_EVENT_KNOWN = {"UID", "SUMMARY", "LOCATION", "DESCRIPTION", "DTSTART", "DTEND", "DURATION", "RRULE",
                "EXDATE", "RECURRENCE-ID", "DTSTAMP"}
_CAL_KNOWN = {"VERSION", "PRODID", "CALSCALE", "METHOD", "X-WR-CALNAME", "X-SONATA-COLOR",
              "X-APPLE-CALENDAR-COLOR"}


@dataclass
class Event:
    uid: str
    summary: str = "New Event"
    start: dt.datetime = None
    end: dt.datetime = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    freq: Optional[str] = None           # one of FREQS, None = no repeat
    interval: int = 1
    until: Optional[dt.datetime] = None  # inclusive
    count: Optional[int] = None
    exdates: List[dt.datetime] = field(default_factory=list)
    alarm: Optional[int] = None          # minutes before the start (0 = at the time), None = no alert
    calendar: str = ""                   # the calendar's id (its file name); not written to the file
    tzid: Optional[str] = None           # DTSTART's zone ("UTC" or a TZID), None = floating local time
    rrule_extra: List[str] = field(default_factory=list)   # other RRULE parts ("BYDAY=MO,WE"), kept
    rrule_extra_freq: Optional[str] = None                 # ... for this FREQ (dropped when it changes)
    extra: List[str] = field(default_factory=list)         # unknown properties/components, verbatim
    recurrence_id: Optional[dt.datetime] = None            # set on a changed instance of a series
    overrides: List["Event"] = field(default_factory=list)  # a series' changed instances

    def copy(self) -> "Event":
        return replace(self, exdates=list(self.exdates), rrule_extra=list(self.rrule_extra),
                       extra=list(self.extra), overrides=[o.copy() for o in self.overrides])

    @property
    def duration(self) -> dt.timedelta:
        return self.end - self.start


@dataclass(frozen=True)
class Occurrence:
    """One occurrence of an event in a view (recurring events have many)."""
    event: Event
    start: dt.datetime
    end: dt.datetime

    @property
    def key(self) -> Tuple[str, dt.datetime]:
        return (self.event.uid, self.start)

    def __hash__(self):
        return hash(self.key)

    def __eq__(self, other):
        return isinstance(other, Occurrence) and self.key == other.key


def new_uid() -> str:
    return str(uuid.uuid4()).upper()


# -- reading ----------------------------------------------------------------------------------
def _unfold(text: str) -> List[str]:
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _split(line: str):
    """'NAME;P1=a;P2="b:c":value' -> ('NAME', {'P1': 'a', 'P2': 'b:c'}, 'value')."""
    i, quoted = 0, False
    while i < len(line):
        c = line[i]
        if c == '"':
            quoted = not quoted
        elif c == ":" and not quoted:
            break
        i += 1
    head, value = line[:i], line[i + 1:]
    parts = re.findall(r'(?:[^;"]|"[^"]*")+', head)
    name = parts[0].upper() if parts else ""
    params = {}
    for p in parts[1:]:
        k, _, v = p.partition("=")
        params[k.upper()] = v.strip('"')
    return name, params, value


def _unescape(v: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _escape(v: str) -> str:
    return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _local(d: dt.datetime) -> dt.datetime:
    """An aware datetime in local time, as a naive one."""
    return d.astimezone().replace(tzinfo=None)


@functools.lru_cache(maxsize=32)
def zone(tzid: Optional[str]):
    """The tzinfo of a zone name ("UTC" or IANA), None when unknown/floating."""
    if not tzid:
        return None
    if tzid.upper() in ("UTC", "Z", "GMT", "ETC/UTC"):
        return dt.timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tzid.lstrip("/"))
    except Exception:
        return None


def to_zone(d: dt.datetime, tzid: Optional[str]) -> dt.datetime:
    """A naive local time as the wall time in tzid (unchanged when floating/unknown)."""
    z = zone(tzid)
    return d if z is None else d.astimezone(z).replace(tzinfo=None)


def from_zone(d: dt.datetime, tzid: Optional[str]) -> dt.datetime:
    """A wall time in tzid as a naive local time."""
    z = zone(tzid)
    return d if z is None else _local(d.replace(tzinfo=z))


def _parse_dt(value: str, params: dict = None) -> Tuple[dt.datetime, bool, Optional[str]]:
    """(naive local datetime, is_date, zone name or None)."""
    params = params or {}
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        return dt.datetime.strptime(value[:8], "%Y%m%d"), True, None
    d = dt.datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    tzid = "UTC" if value.endswith("Z") else params.get("TZID") or None
    return from_zone(d, tzid), False, tzid   # unknown zone: the wall time is kept


def parse_datetime(value: str, params: dict = None) -> Tuple[dt.datetime, bool]:
    """(naive local datetime, is_date)."""
    return _parse_dt(value, params)[:2]


_DUR = re.compile(r"([+-])?P(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_duration(value: str) -> Optional[dt.timedelta]:
    m = _DUR.match(value.strip().upper())
    if not m:
        return None
    sign, w, d, h, mi, s = m.groups()
    td = dt.timedelta(weeks=int(w or 0), days=int(d or 0), hours=int(h or 0), minutes=int(mi or 0),
                      seconds=int(s or 0))
    return -td if sign == "-" else td


def format_duration(td: dt.timedelta) -> str:
    secs = int(td.total_seconds())
    sign = "-" if secs < 0 else ""
    secs = abs(secs)
    if secs == 0:
        return "PT0S"
    if secs % 86400 == 0:
        return f"{sign}P{secs // 86400}D"
    if secs % 3600 == 0:
        return f"{sign}PT{secs // 3600}H"
    return f"{sign}PT{secs // 60}M"


def parse(text: str, calendar: str = "") -> Tuple[dict, List[Event]]:
    """-> ({'name': X-WR-CALNAME, 'color': ..., 'extra': [[line, ...], ...] when
    the file has other blocks}, [Event]).
    A changed instance (RECURRENCE-ID) goes into its series' `overrides`."""
    info, events = {}, []
    ev = None
    alarm = None                             # lines of the VALARM being read
    alarm_used = False                       # ... it became ev.alarm
    foreign = None                           # lines of an unknown component being read
    depth = 0                                # nesting inside `foreign`
    dur = None
    for line in _unfold(text):
        name, params, value = _split(line)
        upper = value.strip().upper()
        if foreign is not None:              # inside an unknown component: keep it whole
            foreign.append(line)
            depth += {"BEGIN": 1, "END": -1}.get(name, 0)
            if depth == 0:
                (ev.extra.extend(foreign) if ev is not None else info.setdefault("extra", []).append(foreign))
                foreign = None
            continue
        if name == "BEGIN" and upper == "VEVENT":
            ev, dur, alarm = Event(uid="", summary=""), None, None
            continue
        if name == "BEGIN" and upper == "VCALENDAR":
            continue
        if name == "BEGIN" and upper == "VALARM" and ev is not None and alarm is None:
            alarm, alarm_used = [line], False
            continue
        if name == "END" and upper == "VALARM" and alarm is not None:
            alarm.append(line)
            if not alarm_used:
                ev.extra.extend(alarm)       # a second alert, or one Calendar can't show: kept
            alarm = None
            continue
        if name == "BEGIN":
            foreign, depth = [line], 1
            continue
        if name == "END" and upper == "VEVENT" and ev is not None:
            if ev.start is not None:
                if ev.end is None:
                    ev.end = ev.start + (dur if dur is not None else
                                         dt.timedelta(days=1) if ev.all_day else dt.timedelta(0))
                if ev.end < ev.start:
                    ev.end = ev.start
                ev.uid = ev.uid or new_uid()
                ev.calendar = calendar
                events.append(ev)
            ev = None
            continue
        if name == "END":
            continue
        if ev is None:
            if name == "X-WR-CALNAME":
                info["name"] = _unescape(value)
            elif name == "X-SONATA-COLOR":
                info["color"] = value.strip().lower()
            elif name == "X-APPLE-CALENDAR-COLOR":
                info.setdefault("hex", value.strip()[:7].lower())
            elif name not in _CAL_KNOWN:
                info.setdefault("extra", []).append([line])
            continue
        try:
            if alarm is not None:
                alarm.append(line)
                if name == "TRIGGER" and ev.alarm is None and params.get("VALUE", "").upper() != "DATE-TIME":
                    td = parse_duration(value)
                    if td is not None and params.get("RELATED", "START").upper() == "START":
                        ev.alarm = max(0, int(-td.total_seconds() // 60))
                        alarm_used = True
                continue
            if name == "UID":
                ev.uid = value.strip()
            elif name == "SUMMARY":
                ev.summary = _unescape(value)
            elif name == "LOCATION":
                ev.location = _unescape(value)
            elif name == "DESCRIPTION":
                ev.description = _unescape(value)
            elif name == "DTSTART":
                ev.start, ev.all_day, ev.tzid = _parse_dt(value, params)
            elif name == "DTEND":
                ev.end, _ = parse_datetime(value, params)
            elif name == "DURATION":
                dur = parse_duration(value)
            elif name == "RRULE":
                _parse_rrule(ev, value)
            elif name == "EXDATE":
                for v in value.split(","):
                    ev.exdates.append(parse_datetime(v, params)[0])
            elif name == "RECURRENCE-ID":
                ev.recurrence_id = parse_datetime(value, params)[0]
            elif name not in _EVENT_KNOWN:
                ev.extra.append(line)
        except ValueError:
            if name not in _EVENT_KNOWN:
                ev.extra.append(line)
            continue                         # a malformed property: skip it, keep the event
    return info, _attach_overrides(events)


def _attach_overrides(events: List[Event]) -> List[Event]:
    """Changed instances (RECURRENCE-ID) join their series; one without a
    series stays a plain event."""
    masters = {e.uid: e for e in events if e.recurrence_id is None}
    out = []
    for e in events:
        m = masters.get(e.uid) if e.recurrence_id is not None else None
        if m is not None:
            m.overrides = [o for o in m.overrides if o.recurrence_id != e.recurrence_id] + [e]
        else:
            out.append(e)
    return out


def _parse_rrule(ev: Event, value: str) -> None:
    parts = dict(p.partition("=")[::2] for p in value.upper().split(";") if "=" in p)
    freq = parts.get("FREQ")
    if freq not in FREQS:
        return
    ev.freq = freq
    ev.interval = max(1, int(parts.get("INTERVAL", "1") or 1))
    if "COUNT" in parts:
        ev.count = max(1, int(parts["COUNT"]))
    if "UNTIL" in parts:
        until, is_date = parse_datetime(parts["UNTIL"])
        ev.until = until + dt.timedelta(days=1, microseconds=-1) if is_date else until
    ev.rrule_extra = [f"{k}={v}" for k, v in parts.items() if k not in _RRULE_KNOWN]
    ev.rrule_extra_freq = freq if ev.rrule_extra else None


# -- writing ----------------------------------------------------------------------------------
def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def _dtprop(name: str, d: dt.datetime, all_day: bool, tzid: Optional[str] = None) -> str:
    if all_day:
        return f"{name};VALUE=DATE:{d:%Y%m%d}"
    if not tzid:
        return f"{name}:{d:%Y%m%dT%H%M%S}"
    wall = to_zone(d, tzid)                  # an unknown zone: d already is its wall time
    if zone(tzid) is dt.timezone.utc:
        return f"{name}:{wall:%Y%m%dT%H%M%SZ}"
    return f"{name};TZID={tzid}:{wall:%Y%m%dT%H%M%S}"


def _until(ev: Event) -> str:
    if ev.all_day:                           # RFC 5545: a DATE when DTSTART is a DATE
        return f"{ev.until:%Y%m%d}"
    if zone(ev.tzid) is not None:            # with a zone, UNTIL is in UTC
        return f"{ev.until.astimezone(dt.timezone.utc):%Y%m%dT%H%M%SZ}"
    return f"{ev.until:%Y%m%dT%H%M%S}"


def event_lines(ev: Event, stamp: str) -> List[str]:
    tz = ev.tzid
    lines = ["BEGIN:VEVENT", f"UID:{ev.uid}", f"DTSTAMP:{stamp}"]
    if ev.recurrence_id is not None:
        lines.append(_dtprop("RECURRENCE-ID", ev.recurrence_id, ev.all_day, tz))
    lines += [_dtprop("DTSTART", ev.start, ev.all_day, tz), _dtprop("DTEND", ev.end, ev.all_day, tz),
              f"SUMMARY:{_escape(ev.summary)}"]
    if ev.location:
        lines.append(f"LOCATION:{_escape(ev.location)}")
    if ev.description:
        lines.append(f"DESCRIPTION:{_escape(ev.description)}")
    if ev.freq:
        rule = f"FREQ={ev.freq}"
        if ev.interval > 1:
            rule += f";INTERVAL={ev.interval}"
        if ev.count:
            rule += f";COUNT={ev.count}"
        elif ev.until:
            rule += f";UNTIL={_until(ev)}"
        if ev.rrule_extra and ev.rrule_extra_freq == ev.freq:
            rule += ";" + ";".join(ev.rrule_extra)
        lines.append(f"RRULE:{rule}")
        if ev.exdates:
            lines.append(",".join(_dtprop("EXDATE", d, ev.all_day, tz) if i == 0 else
                                  _dtprop("EXDATE", d, ev.all_day, tz).partition(":")[2]
                                  for i, d in enumerate(ev.exdates)))
    lines += ev.extra
    if ev.alarm is not None:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(ev.summary)}",
                  f"TRIGGER:{format_duration(dt.timedelta(minutes=-ev.alarm))}", "END:VALARM"]
    lines.append("END:VEVENT")
    if ev.freq:
        for o in ev.overrides:
            lines += event_lines(o, stamp)
    return lines


def serialize(events, name: str = "", color: str = "", hex_color: str = "", extra=()) -> str:
    """extra: calendar-level blocks kept from the file ([[line, ...], ...])."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sonata//Calendar//EN", "CALSCALE:GREGORIAN"]
    if name:
        lines.append(f"X-WR-CALNAME:{_escape(name)}")
    if color:
        lines.append(f"X-SONATA-COLOR:{color}")
    if hex_color:
        lines.append(f"X-APPLE-CALENDAR-COLOR:{hex_color.upper()}")
    for block in extra:
        lines += block
    for ev in events:
        lines += event_lines(ev, stamp)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(x) for x in lines) + "\r\n"


# -- recurrence -------------------------------------------------------------------------------
def _add_months(d: dt.datetime, months: int) -> Optional[dt.datetime]:
    """d moved by whole months; None when that month has no such day (RFC
    5545: the 31st repeats only in months with a 31st)."""
    y, m = divmod(d.month - 1 + months, 12)
    try:
        return d.replace(year=d.year + y, month=m + 1)
    except ValueError:
        return None


def _nth(ev: Event, n: int, start: dt.datetime = None) -> Optional[dt.datetime]:
    """The n-th candidate start of the rule (None = skipped: no such date),
    counted from `start` (default: ev.start)."""
    start = ev.start if start is None else start
    k = n * ev.interval
    if ev.freq == "DAILY":
        return start + dt.timedelta(days=k)
    if ev.freq == "WEEKLY":
        return start + dt.timedelta(weeks=k)
    if ev.freq == "MONTHLY":
        return _add_months(start, k)
    try:
        return start.replace(year=start.year + k)
    except ValueError:                      # 29 February in a common year
        return None


def _weekly_days(ev: Event):
    """(weekdays, week start) of a WEEKLY rule with a plain BYDAY, else None
    (other extra parts: repeat as if only FREQ/INTERVAL were there)."""
    if ev.freq != "WEEKLY" or not ev.rrule_extra or ev.rrule_extra_freq != ev.freq:
        return None
    days, wkst = None, 0
    for p in ev.rrule_extra:
        k, _, v = p.partition("=")
        if k == "BYDAY" and all(c in WEEKDAYS for c in v.split(",")):
            days = sorted({WEEKDAYS.index(c) for c in v.split(",")})
        elif k == "WKST" and v in WEEKDAYS:
            wkst = WEEKDAYS.index(v)
        else:
            return None
    return (days, wkst) if days else None


def _period(ev: Event, n: int, anchor: dt.datetime, byday) -> List[dt.datetime]:
    """Candidate starts (wall times in the event's zone) of the n-th period."""
    if byday is None:
        s = _nth(ev, n, anchor)
        return [] if s is None else [s]
    days, wkst = byday
    week = anchor - dt.timedelta(days=(anchor.weekday() - wkst) % 7) + dt.timedelta(weeks=n * ev.interval)
    out = []
    for d in sorted(days, key=lambda d: (d - wkst) % 7):
        s = week + dt.timedelta(days=(d - wkst) % 7)
        if s >= anchor:
            out.append(s)
    return out


def occurrences(ev: Event, a: dt.datetime, b: dt.datetime) -> Iterator[Occurrence]:
    """Occurrences of ev overlapping [a, b) (an empty event counts when it
    starts inside), its changed instances included."""
    yield from _series(ev, a, b)
    if ev.freq:
        for o in ev.overrides:
            yield from _series(o, a, b)


def _series(ev: Event, a: dt.datetime, b: dt.datetime) -> Iterator[Occurrence]:
    dur = ev.end - ev.start

    def hit(s):
        e = s + dur
        return s < b and (e > a or (e == s and s >= a))
    if not ev.freq:
        if hit(ev.start):
            yield Occurrence(ev, ev.start, ev.end)
        return
    ex = set(ev.exdates)
    ex.update(o.recurrence_id for o in ev.overrides)
    # the rule runs on the wall time of the event's zone (DST-safe), shown in local time
    tz = ev.tzid if zone(ev.tzid) is not None and not ev.all_day else None
    anchor = to_zone(ev.start, tz) if tz else ev.start
    byday = _weekly_days(ev)
    n = 0
    if ev.count is None and ev.freq in ("DAILY", "WEEKLY"):
        # jump close to the range: no need to walk from the first occurrence
        step = ev.interval * (1 if ev.freq == "DAILY" else 7)
        n = max(0, ((a - dur - ev.start).days // step) - 1)
    elif ev.count is None and ev.freq == "MONTHLY":
        n = max(0, ((a.year - ev.start.year) * 12 + a.month - ev.start.month) // ev.interval - 2)
    elif ev.count is None:
        n = max(0, (a.year - ev.start.year) // ev.interval - 2)
    produced = 0
    limit = 100000                          # safety net against broken rules
    while limit:
        limit -= 1
        cands = _period(ev, n, anchor, byday)
        n += 1
        for s in cands:
            if tz:
                s = from_zone(s, tz)
            if ev.until is not None and s > ev.until:
                return
            if ev.count is not None:
                produced += 1
                if produced > ev.count:
                    return
            if s >= b:
                return
            if s not in ex and hit(s):
                yield Occurrence(ev, s, s + dur)


def expand(events, a: dt.datetime, b: dt.datetime) -> List[Occurrence]:
    out = []
    for ev in events:
        out.extend(occurrences(ev, a, b))
    out.sort(key=lambda o: (o.start, -(o.end - o.start).total_seconds(), o.event.summary))
    return out
