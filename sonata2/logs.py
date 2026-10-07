"""Sonata's logs: off by default (Vini), Settings > About > Logs.

Off: the parts still log, but to memory ($XDG_RUNTIME_DIR/sonata2-logs,
gone at logout) and only errors -- `sonata2 doctor`, crash recovery and
a bug report sent during the session still have them; nothing is kept on
disk. On (the flag file below, or SONATA_DEBUG=1): detailed logs (window
log, frame timings, start-up timings, component chatter, Wayfire's info
lines) in ~/.cache/sonata2, kept between sessions. Logs never leave the
computer.

    from sonata2 import logs
    if logs.verbose(): ...
"""
import os

CAP = 1 << 20                     # a component's log, normal installs (bytes)
CAP_VERBOSE = 32 << 20


def log_dir() -> str:
    """Where everything logs: ~/.cache/sonata2 with logs on, else memory
    (tools/sonata-session picks the same folder)."""
    if verbose():
        return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2")
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", "sonata2-logs")


def path(name: str) -> str:
    """A log file's path (its folder made)."""
    d = log_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return os.path.join(d, name)


def flag_path() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "sonata2", "debug-logging")


def verbose() -> bool:
    """Logs on (Settings > About). A dev install too used to force them on:
    no more -- off by default everywhere (Vini)."""
    return os.environ.get("SONATA_DEBUG") == "1" or os.path.exists(flag_path())


def set_verbose(on: bool) -> None:
    """Settings > About > Logs on / off (from the next start of each part)."""
    path = flag_path()
    try:
        if on:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("Sonata writes detailed logs while this file exists.\n")
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def trim(path: str, cap: int = None) -> None:
    """Keep a log under its cap: past it, only the newest half stays. (The
    writer appends -- O_APPEND -- so it carries on at the new end.)"""
    cap = cap or (CAP_VERBOSE if verbose() else CAP)
    try:
        size = os.path.getsize(path)
        if size <= cap:
            return
        with open(path, "rb") as f:
            f.seek(size - cap // 2)
            tail = f.read()
        tail = tail[tail.find(b"\n") + 1:]
        with open(path, "r+b") as f:
            f.write(b"--- (older lines removed: the log is kept small)\n" + tail)
            f.truncate()
    except OSError:
        pass
