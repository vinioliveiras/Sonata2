"""RGB lights (OpenRGB) dark with the screen, and back as they were (Vini:
a USB keyboard stayed lit behind the lock). Off unless chosen (Settings >
Lock Screen): it once saved the lights while they were already dark, and
the laptop keyboard stayed black after every "back on".

    python3 -m sonata2.rgblights off|on      (lockdisplay.lights, swayidle)

"Off" saves the colours as a profile first -- a new one replaces the kept
one only if it isn't dark itself (all black, every device in "Direct" or
"Off"), so the colours to put back are never black."""
import json
import os
import subprocess
import sys

PROFILE = "sonata-idle"


def _cache(*p) -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2", *p)


def profiles_dir() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "OpenRGB", "profiles")


MARK = _cache("rgb-before-dark")


def lit(path: str) -> bool:
    """A saved profile with some light in it."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    for c in data.get("controllers") or [] if isinstance(data, dict) else []:
        if any(isinstance(v, int) and v & 0xFFFFFF for v in c.get("colors") or []):
            return True
        modes = c.get("modes") or []
        i = c.get("active_mode", 0)
        name = (modes[i].get("name") if isinstance(i, int) and 0 <= i < len(modes) else "") or ""
        if name.lower() not in ("direct", "off", ""):
            return True                       # an effect (Static, Breathing...) keeps its own colours
    return False


def _openrgb(*args) -> int:
    try:
        return subprocess.run(["openrgb", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=60).returncode
    except (OSError, subprocess.SubprocessError):
        return 1


def off() -> None:
    if not os.path.exists(MARK):
        kept = os.path.join(profiles_dir(), PROFILE + ".json")
        new = os.path.join(profiles_dir(), PROFILE + "-new.json")
        if _openrgb("--save-profile", PROFILE + "-new") == 0 and lit(new):
            os.replace(new, kept)
        else:
            try:
                os.unlink(new)                # dark already: the kept colours stay
            except OSError:
                pass
        if lit(kept):
            os.makedirs(os.path.dirname(MARK), exist_ok=True)
            open(MARK, "w").close()
        else:
            return                            # nothing good to put back: leave the lights alone
    _openrgb("--mode", "off")


def on() -> None:
    if not os.path.exists(MARK):
        return
    kept = os.path.join(profiles_dir(), PROFILE + ".json")
    if lit(kept):
        _openrgb("--profile", PROFILE)
    try:
        os.unlink(MARK)
    except OSError:
        pass


if __name__ == "__main__":
    {"off": off, "on": on}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: None)()
