"""Finds what freezes a Sonata process (Vini: lag when closing apps).

Off unless ~/.cache/sonata2/stallwatch exists (tools/closelag.sh makes it
for a minute): then each process checks its main loop every 50 ms, and when
the loop doesn't answer within STALL_MS, what the main thread is running is
written to ~/.cache/sonata2/stalls.log -- the process, how long it stayed
stuck, and the Python stack. Off, it costs nothing: the flag is watched
(inotify) and the thread runs only while the flag exists. (It was a stat()
a second in every Sonata process, all session long: a wakeup a second each
for a tool run now and then.)"""
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
    state = {"thread": None}

    def run():
        while os.path.exists(flag_path()):
            _probe(label, main_id)
            time.sleep(0.05)
        state["thread"] = None
        if os.path.exists(flag_path()):            # made again as it ended: on again
            from gi.repository import GLib
            GLib.idle_add(lambda: (check(), False)[1])

    def check(*_a):
        if state["thread"] is not None or not os.path.exists(flag_path()):
            return
        try:                                       # the report shows which processes were watched
            with open(log_path(), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} watching {label} pid {os.getpid()}\n")
        except OSError:
            pass
        state["thread"] = threading.Thread(target=run, daemon=True, name="stallwatch")
        state["thread"].start()
    try:
        from gi.repository import Gio
        # (the folder made now: GIO polls a missing one every few seconds)
        os.makedirs(os.path.dirname(flag_path()), exist_ok=True)
        mon = Gio.File.new_for_path(flag_path()).monitor_file(Gio.FileMonitorFlags.NONE, None)
        mon.connect("changed", check)
        _started.append(mon)                       # kept alive
    except Exception as e:                         # (a read-only cache: not watched)
        print(f"sonata2: stallwatch: {e}", file=sys.stderr)
    check()
