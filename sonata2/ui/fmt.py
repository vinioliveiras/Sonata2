"""Human-readable values the macOS way (Finder, Get Info, progress)."""


def size(n: int) -> str:
    """Decimal units like Finder: "Zero bytes", "653 bytes", "12 KB", "1.4 MB"."""
    if n <= 0:
        return "Zero bytes"
    if n < 1000:
        return f"{n} bytes"
    if n < 1000 ** 2:
        return f"{round(n / 1000)} KB"
    for unit, p in (("MB", 2), ("GB", 3), ("TB", 4)):
        if n < 1000 ** (p + 1) or unit == "TB":
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
