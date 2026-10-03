"""Wayfire options Sonata changes (Settings: keyboard, trackpad, title bar
colours...). No GTK here: tools/wayfire-config.sh uses it at login."""
import fcntl
import os
import threading
from contextlib import contextmanager
from typing import List

from . import config

# Settings writes from worker threads (sliders, title bar colours: several
# keys at once); each write is read-modify-write of the whole file, so two at
# a time would drop each other's change or read a half-written file.
_LOCK = threading.Lock()


@contextmanager
def _locked():
    """One writer at a time: threads (_LOCK) and Sonata's processes (Settings,
    menu bar's lighter effects) through a flock beside the session copy."""
    with _LOCK:
        lock = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-wayfire.lock")
        try:
            fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        except OSError:
            fd = -1                     # no lock file possible: still one thread at a time
        try:
            if fd >= 0:
                fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            if fd >= 0:
                os.close(fd)            # also releases the flock


def _wayfire_files() -> List[str]:
    """Where Settings writes Wayfire options: Sonata's overrides file (kept
    apart from the installed wayfire.ini, so updates of that still apply;
    tools/wayfire-config.sh merges it in at login) and, while a session
    runs, the resolved copy Wayfire reads and reloads live."""
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sonata2")
    files = [os.path.join(cfg, "wayfire-overrides.ini")]
    run = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-wayfire.ini")
    if os.path.exists(run):
        files.append(run)
    return files


def _wayfire_read_files() -> List[str]:
    cfg = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sonata2")
    return [os.path.join(cfg, "wayfire.ini")] + _wayfire_files()


def wayfire_get(section: str, key: str, default: str = "") -> str:
    for path in reversed(_wayfire_read_files()):
        try:
            with open(path, encoding="utf-8") as f:
                cur = None
                for line in f:
                    s = line.strip()
                    if s.startswith("[") and s.endswith("]"):
                        cur = s[1:-1]
                    elif cur == section and "=" in s and not s.startswith("#"):
                        k, v = s.split("=", 1)
                        if k.strip() == key:
                            return v.strip()
        except OSError:
            continue
    return default


def frame_options(frame: dict) -> list:
    """(section, key, value) for the title bars Wayfire draws, from
    tokens.FRAME: the same corners, buttons, title and shadow as Sonata's
    windows. pixdecor puts the first button's outer edge x_offset in from its
    side and the buttons `spacing` apart; Wayfire's own decoration (no
    pixdecor) only takes the order, title and font."""
    side = frame["buttons_side"]
    names = list(frame["buttons"])
    layout = ",".join(names) + ":" if side == "left" else ":" + ",".join(reversed(names))
    align = {"left": "0", "center": "1", "right": "2"}[frame["title_align"]]
    return [("pixdecor", "rounded_corner_radius", str(frame["radius"])),
            ("pixdecor", "button_layout", layout),
            ("pixdecor", f"{side}_button_spacing", str(frame["dot_gap"])),
            ("pixdecor", f"{side}_button_x_offset", f"{frame['dot_left'] - frame['dot'] / 2:g}"),
            ("pixdecor", "title_font", frame["title_font"]),
            ("pixdecor", "title_text_align", align),
            ("pixdecor", "shadow_radius", str(frame["shadow_radius"])),
            ("pixdecor", "shadow_color", "\\" + frame["shadow_color"]),
            ("sonata-corners", "radius", str(frame["radius"])),
            # Wayfire's own bar keeps its buttons on the right: close at the edge
            ("decoration", "button_order", " ".join(reversed(names)) if side == "right" else
             " ".join([n for n in names if n != "close"] + ["close"])),
            ("decoration", "title_height", str(frame["fallback_title_h"])),
            ("decoration", "title_halign", frame["title_align"]),
            ("decoration", "font", frame["fallback_font"])]


def wayfire_set(section: str, key: str, value) -> bool:
    """Set `key = value` in [section] of the session's Wayfire config(s);
    Wayfire applies it at once. Comments and other lines stay as they are.
    Thread-safe (one writer at a time)."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    with _locked():
        return _set(section, key, value)


def runtime_set(section: str, key: str, value) -> bool:
    """Like wayfire_set, for this session only: the resolved copy Wayfire
    reads (made again at every login), never Sonata's overrides -- for
    temporary changes (lighter effects while gaming) a crash can't keep.
    value None removes the key (Wayfire's default applies again)."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    run = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-wayfire.ini")
    if not os.path.exists(run):
        return False
    with _locked():
        return _set(section, key, value, [run])


def _set(section: str, key: str, value, files=None) -> bool:
    ok = False
    for path in files or _wayfire_files():
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            lines = []
        out, cur, done, sec_end = [], None, False, None
        for i, line in enumerate(lines):
            s = line.strip()
            if s.startswith("[") and s.endswith("]"):
                if cur == section and not done:
                    sec_end = len(out)
                cur = s[1:-1]
            elif cur == section and "=" in s and not s.startswith("#") and s.split("=", 1)[0].strip() == key:
                if value is not None:
                    out.append(f"{key} = {value}\n")
                done = True
                continue
            out.append(line)
        if not done and value is not None:
            if cur == section:
                sec_end = len(out)
            if sec_end is not None:
                while sec_end > 0 and not out[sec_end - 1].strip():      # before the blank line
                    sec_end -= 1
                out.insert(sec_end, f"{key} = {value}\n")
            else:
                out += [f"\n[{section}]\n", f"{key} = {value}\n"]
        if out == lines:
            ok = True              # already so: no write, so Wayfire doesn't reload (no display modeset)
            continue
        try:
            # whole new file renamed over the old: Wayfire (inotify IN_MOVED_TO)
            # never reloads a half-written one
            config.atomic_write(path, "".join(out).encode("utf-8"), fsync=False)
            ok = True
        except OSError:
            pass
    return ok


