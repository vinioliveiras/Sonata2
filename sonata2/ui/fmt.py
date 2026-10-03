"""Human-readable values the macOS way (Finder, Get Info, progress, list dates)."""
import datetime
import time


def _dt():
    from gi.repository import GLib
    return GLib.DateTime


def size(n: int) -> str:
    """Decimal units like Finder: "Zero bytes", "653 bytes", "12 KB", "1.4 MB"."""
    if n <= 0:
        return "Zero bytes"
    if n < 1000:
        return f"{n} bytes"
    if round(n / 1000) < 1000:                      # 999 500 bytes: "1.0 MB", not "1000 KB"
        return f"{round(n / 1000)} KB"
    for unit, p in (("MB", 2), ("GB", 3), ("TB", 4)):
        if round(n / 1000 ** p, 1) < 1000 or unit == "TB":
            return f"{n / 1000 ** p:.1f} {unit}"


def eta(seconds: float) -> str:
    """Finder's remaining-time phrase."""
    s = int(seconds + 0.5)
    if s < 5:
        return "About 5 seconds"
    if s < 60:
        return f"About {((s + 4) // 5) * 5} seconds"
    m = (s + 30) // 60
    if m == 1:
        return "About a minute"
    if m < 60:
        return f"About {m} minutes"
    h = (m + 30) // 60
    return "About an hour" if h == 1 else f"About {h} hours"


def date_section(t: float, pinned: bool = False, now: float = None) -> tuple:
    """(rank, sub-rank, title) of the list section a date falls in (macOS: Pinned,
    Today, Yesterday, Previous 7 Days, Previous 30 Days, months, years)."""
    if pinned:
        return (0, 0, "Pinned")
    today = datetime.date.fromtimestamp(now if now is not None else time.time())
    d = datetime.date.fromtimestamp(t)
    days = (today - d).days
    if days <= 0:
        return (1, 0, "Today")
    if days == 1:
        return (2, 0, "Yesterday")
    if days <= 7:
        return (3, 0, "Previous 7 Days")
    if days <= 30:
        return (4, 0, "Previous 30 Days")
    if d.year == today.year:
        return (5, -d.month, _dt().new_local(d.year, d.month, 1, 0, 0, 0).format("%B"))
    return (6, -d.year, str(d.year))


def short_date(t: float, now: float = None) -> str:
    """14:05 today, Yesterday, a weekday this week, else the date."""
    today = datetime.date.fromtimestamp(now if now is not None else time.time())
    dt = _dt().new_from_unix_local(int(t))
    days = (today - datetime.date.fromtimestamp(t)).days
    if days <= 0:
        return dt.format("%H:%M")
    if days == 1:
        return "Yesterday"
    if days < 7:
        return dt.format("%A")
    return dt.format("%x")


def count(n: int) -> str:
    """Short counts: 950, 24K, 1.2M (tokens, items)."""
    n = int(n)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.0f}K" if n >= 10_000 else f"{n / 1000:.1f}K".replace(".0K", "K")
    return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
