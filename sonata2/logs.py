"""How much Sonata writes to its logs (~/.cache/sonata2).

Normal installs keep them small: errors and crashes only, what `sonata2
doctor` and bug reports need. Detailed logs (window log, frame timings,
start-up timings, component chatter, Wayfire's info lines) are for
development: a dev install (a git clone, `install.sh --dev`), the flag
file below (Settings > About: click the version 7 times) or SONATA_DEBUG=1.
Logs never leave the computer.

    from sonata2 import logs
    if logs.verbose(): ...
"""
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAP = 1 << 20                     # a component's log, normal installs (bytes)
CAP_VERBOSE = 32 << 20


def log_dir() -> str:
    """Where components log ($XDG_CACHE_HOME/sonata2): `keep` writes there,
    the doctor and bug reports read there."""
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2")


def flag_path() -> str:
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "sonata2", "debug-logging")


def dev_install() -> bool:
    return os.path.isdir(os.path.join(REPO, ".git"))


def verbose() -> bool:
    return (os.environ.get("SONATA_DEBUG") == "1" or os.path.exists(flag_path()) or dev_install())


def set_verbose(on: bool) -> None:
    """Settings > About: detailed logs on / off (from the next start of each part)."""
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
