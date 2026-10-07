"""Finds what freezes a Sonata process (Vini: lag when closing apps).

Off unless ~/.cache/sonata2/stallwatch exists (tools/closelag.sh makes it
for a minute): then each process checks its main loop every 50 ms, and when
the loop doesn't answer within STALL_MS, what the main thread is running is
written to ~/.cache/sonata2/stalls.log -- the process, how long it stayed
stuck, and the Python stack. Off, it costs one stat() per second in a
background thread."""
import os
import sys
import threading
import time
import traceback

STALL_MS = 80
_started = []


def _cache(*parts) -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2", *parts)


def flag_path() -> str:
    return _cache("stallwatch")


def log_path() -> str:
    from . import logs
    return logs.path("stalls.log")


def write(label: str, ms: float, stack: list) -> None:
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {label} pid {os.getpid()}: main loop stuck {ms:.0f} ms\n")
            f.write("".join(stack[-14:]) + "\n")
    except OSError:
        pass


def _probe(label, main_id) -> None:
    from gi.repository import GLib
    answered = threading.Event()
    t0 = time.monotonic()
    GLib.idle_add(lambda: (answered.set(), False)[1], priority=GLib.PRIORITY_HIGH)
    if answered.wait(STALL_MS / 1000):
        return
    frame = sys._current_frames().get(main_id)
    stack = traceback.format_stack(frame) if frame is not None else []
    answered.wait(10)
    write(label, (time.monotonic() - t0) * 1000, stack)


def start(label: str) -> None:
    """Watch this process's main loop (call from the main thread, once)."""
    if _started:
        return
    _started.append(label)
    main_id = threading.get_ident()

    def run():
        on = False
        while True:
            if os.path.exists(flag_path()):
                if not on:                 # the report shows which processes were watched
                    on = True
                    try:
                        with open(log_path(), "a", encoding="utf-8") as f:
                            f.write(f"{time.strftime('%H:%M:%S')} watching {label} pid {os.getpid()}\n")
                    except OSError:
                        pass
                _probe(label, main_id)
                time.sleep(0.05)
            else:
                on = False
                time.sleep(1)
    threading.Thread(target=run, daemon=True, name="stallwatch").start()
