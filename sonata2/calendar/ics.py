"""A minimal iCalendar (RFC 5545) reader/writer for Calendar, and recurrence
expansion. Pure Python (no GTK), so it is easy to test.

Supported subset: VCALENDAR with X-WR-CALNAME / X-APPLE-CALENDAR-COLOR /
X-SONATA-COLOR, VEVENT with UID, SUMMARY, LOCATION, DESCRIPTION, DTSTART /
DTEND (floating local time, UTC "Z" or TZID -- both converted to local
time -- or VALUE=DATE), DURATION (when there is no DTEND), RRULE
FREQ / INTERVAL / UNTIL / COUNT, EXDATE, and one VALARM TRIGGER (relative
to the start). Everything is kept as naive local datetimes; an all-day
event runs from midnight of its first day to midnight after its last."""
import datetime as dt
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import Iterator, List, Optional, Tuple

FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")


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

    def copy(self) -> "Event":
        return replace(self, exdates=list(self.exdates))

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


def parse_datetime(value: str, params: dict = None) -> Tuple[dt.datetime, bool]:
    """(naive local datetime, is_date)."""
    params = params or {}
    value = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", value):
        return dt.datetime.strptime(value[:8], "%Y%m%d"), True
    utc = value.endswith("Z")
    d = dt.datetime.strptime(value.rstrip("Z")[:15], "%Y%m%dT%H%M%S")
    if utc:
        return _local(d.replace(tzinfo=dt.timezone.utc)), False
    tzid = params.get("TZID")
    if tzid:
        try:
            from zoneinfo import ZoneInfo
            return _local(d.replace(tzinfo=ZoneInfo(tzid.lstrip("/")))), False
        except Exception:            # unknown zone: keep the wall time
            pass
    return d, False


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
    """-> ({'name': X-WR-CALNAME, 'color': ...}, [Event])."""
    info, events = {}, []
    ev = None
    in_alarm = False
    dur = None
    for line in _unfold(text):
        name, params, value = _split(line)
        if name == "BEGIN" and value.upper() == "VEVENT":
            ev, dur, in_alarm = Event(uid="", summary=""), None, False
            continue
        if name == "BEGIN" and value.upper() == "VALARM":
            in_alarm = True
            continue
        if name == "END" and value.upper() == "VALARM":
            in_alarm = False
            continue
        if name == "END" and value.upper() == "VEVENT" and ev is not None:
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
        if ev is None:
            if name == "X-WR-CALNAME":
                info["name"] = _unescape(value)
            elif name == "X-SONATA-COLOR":
                info["color"] = value.strip().lower()
            elif name == "X-APPLE-CALENDAR-COLOR":
                info.setdefault("hex", value.strip()[:7].lower())
            continue
        try:
            if in_alarm:
                if name == "TRIGGER" and ev.alarm is None and params.get("VALUE", "").upper() != "DATE-TIME":
                    td = parse_duration(value)
                    if td is not None and params.get("RELATED", "START").upper() == "START":
                        ev.alarm = max(0, int(-td.total_seconds() // 60))
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
                ev.start, ev.all_day = parse_datetime(value, params)
            elif name == "DTEND":
                ev.end, _ = parse_datetime(value, params)
            elif name == "DURATION":
                dur = parse_duration(value)
            elif name == "RRULE":
                _parse_rrule(ev, value)
            elif name == "EXDATE":
                for v in value.split(","):
                    ev.exdates.append(parse_datetime(v, params)[0])
        except ValueError:
            continue                         # a malformed property: skip it, keep the event
    return info, events


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


def _dtprop(name: str, d: dt.datetime, all_day: bool) -> str:
    if all_day:
        return f"{name};VALUE=DATE:{d:%Y%m%d}"
    return f"{name}:{d:%Y%m%dT%H%M%S}"


def event_lines(ev: Event, stamp: str) -> List[str]:
    lines = ["BEGIN:VEVENT", f"UID:{ev.uid}", f"DTSTAMP:{stamp}",
             _dtprop("DTSTART", ev.start, ev.all_day), _dtprop("DTEND", ev.end, ev.all_day),
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
            rule += f";UNTIL={ev.until:%Y%m%dT%H%M%S}"
        lines.append(f"RRULE:{rule}")
        if ev.exdates:
            lines.append(("EXDATE;VALUE=DATE:" if ev.all_day else "EXDATE:") +
                         ",".join(f"{d:%Y%m%d}" if ev.all_day else f"{d:%Y%m%dT%H%M%S}" for d in ev.exdates))
    if ev.alarm is not None:
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(ev.summary)}",
                  f"TRIGGER:{format_duration(dt.timedelta(minutes=-ev.alarm))}", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def serialize(events, name: str = "", color: str = "", hex_color: str = "") -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Sonata//Calendar//EN", "CALSCALE:GREGORIAN"]
    if name:
        lines.append(f"X-WR-CALNAME:{_escape(name)}")
    if color:
        lines.append(f"X-SONATA-COLOR:{color}")
    if hex_color:
        lines.append(f"X-APPLE-CALENDAR-COLOR:{hex_color.upper()}")
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


def _nth(ev: Event, n: int) -> Optional[dt.datetime]:
    """The n-th candidate start of the rule (None = skipped: no such date)."""
    k = n * ev.interval
    if ev.freq == "DAILY":
        return ev.start + dt.timedelta(days=k)
    if ev.freq == "WEEKLY":
        return ev.start + dt.timedelta(weeks=k)
    if ev.freq == "MONTHLY":
        return _add_months(ev.start, k)
    try:
        return ev.start.replace(year=ev.start.year + k)
    except ValueError:                      # 29 February in a common year
        return None


def occurrences(ev: Event, a: dt.datetime, b: dt.datetime) -> Iterator[Occurrence]:
    """Occurrences of ev overlapping [a, b) (an empty event counts when it
    starts inside)."""
    dur = ev.end - ev.start

    def hit(s):
        e = s + dur
        return s < b and (e > a or (e == s and s >= a))
    if not ev.freq:
        if hit(ev.start):
            yield Occurrence(ev, ev.start, ev.end)
        return
    ex = set(ev.exdates)
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
        s = _nth(ev, n)
        n += 1
        if s is None:
            continue
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
