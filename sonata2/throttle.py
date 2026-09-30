"""Waits after wrong passwords (macOS: "Try again in 1 minute"), for the
login screen and the lock screen. Kept in a small JSON file so restarting
the screen (or the computer) doesn't reset it; counted per user.

    t = Throttle(path)
    t.wait_left("vini")     # seconds still to wait (0: may try)
    t.failed("vini")        # a wrong password; returns the wait it starts
    t.succeeded("vini")     # right password: the count starts over

The first FREE_TRIES wrong passwords cost nothing; then each one waits
longer (WAITS), the last step repeating."""
import json
import os
import time

FREE_TRIES = 4
WAITS = (60, 5 * 60, 15 * 60, 60 * 60)      # 5th, 6th, 7th, 8th+ wrong password


class Throttle:
    def __init__(self, path: str, clock=time.time):
        self.path, self.clock = path, clock

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = f"{self.path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def wait_left(self, user: str) -> int:
        until = self._load().get(user, {}).get("until", 0)
        return max(0, int(until - self.clock() + 0.999))

    def failed(self, user: str) -> int:
        data = self._load()
        entry = data.get(user, {})
        n = int(entry.get("failures", 0)) + 1
        wait = 0 if n <= FREE_TRIES else WAITS[min(n - FREE_TRIES, len(WAITS)) - 1]
        data[user] = {"failures": n, "until": self.clock() + wait if wait else 0}
        self._save(data)
        return wait

    def succeeded(self, user: str) -> None:
        data = self._load()
        if data.pop(user, None) is not None:
            self._save(data)


def describe(seconds: int) -> str:
    """"Try again in 4:59" (minutes:seconds), "Try again in 59 seconds"."""
    if seconds >= 60:
        return f"Try again in {seconds // 60}:{seconds % 60:02d}"
    return f"Try again in {seconds} second{'s' if seconds != 1 else ''}"
