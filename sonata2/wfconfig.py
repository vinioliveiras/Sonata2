"""Wayfire options Sonata changes (Settings: keyboard, trackpad, title bar
colours...). No GTK here: tools/wayfire-config.sh uses it at login."""
import os
import threading
from typing import List

# Settings writes from worker threads (sliders, title bar colours: several
# keys at once); each write is read-modify-write of the whole file, so two at
# a time would drop each other's change or read a half-written file.
_LOCK = threading.Lock()


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


def wayfire_set(section: str, key: str, value) -> bool:
    """Set `key = value` in [section] of the session's Wayfire config(s);
    Wayfire applies it at once. Comments and other lines stay as they are.
    Thread-safe (one writer at a time)."""
    if isinstance(value, bool):
        value = "true" if value else "false"
    with _LOCK:
        return _set(section, key, value)


def _set(section: str, key: str, value) -> bool:
    ok = False
    for path in _wayfire_files():
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
                out.append(f"{key} = {value}\n")
                done = True
                continue
            out.append(line)
        if not done:
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
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(out)
            ok = True
        except OSError:
            pass
    return ok


