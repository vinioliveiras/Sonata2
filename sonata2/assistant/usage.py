"""Token use by the Assistant, counted locally (an API key has no hourly or
weekly limits of its own: the limits are yours, set in Settings).

usage.json in the app's data folder: {"calls": [[epoch seconds, tokens], ...]},
kept for 7 days.

    log = Usage()
    log.add(tokens)
    log.last_hour(), log.last_week()"""
import json
import os
import time

HOUR, WEEK = 3600, 7 * 86400
HOURLY = (100_000, 250_000, 500_000, 1_000_000, 2_000_000, 5_000_000)        # limit choices
WEEKLY = (1_000_000, 5_000_000, 10_000_000, 25_000_000, 50_000_000, 100_000_000)


class Usage:
    def __init__(self, folder: str = None):
        if folder is None:
            from .store import data_dir
            folder = data_dir()
        self.path = os.path.join(folder, "usage.json")
        try:
            with open(self.path, encoding="utf-8") as f:
                calls = json.load(f).get("calls", [])
        except (OSError, ValueError, AttributeError):
            calls = []
        self.calls = [(float(t), int(n)) for t, n in (c for c in calls if isinstance(c, list) and len(c) == 2)
                      if isinstance(t, (int, float)) and isinstance(n, int)]

    def add(self, tokens: int, now: float = None) -> None:
        now = time.time() if now is None else now
        if tokens > 0:
            self.calls.append((now, int(tokens)))
        self.calls = [c for c in self.calls if c[0] > now - WEEK]
        from .store import _write
        _write(self.path, {"calls": [[round(t, 1), n] for t, n in self.calls]})

    def _since(self, seconds: int, now: float = None) -> int:
        now = time.time() if now is None else now
        return sum(n for t, n in self.calls if t > now - seconds)

    def last_hour(self, now: float = None) -> int:
        return self._since(HOUR, now)

    def last_week(self, now: float = None) -> int:
        return self._since(WEEK, now)
