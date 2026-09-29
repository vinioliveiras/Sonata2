"""Startup timing for Sonata's processes (their logs in ~/.cache/sonata2).

    trace.start("dock")      # time 0; stacks are dumped if not ready in 8 s
    trace.mark("activate")   # "[trace dock] +0.42s activate (22:40:59.512)"
    trace.ready()            # first frame reached: stop the watchdog

If a process is still starting after WATCHDOG seconds, every thread's
Python stack is written to the log (faulthandler), once, then again every
WATCHDOG seconds until ready() -- where a login hangs, in one look."""
import faulthandler
import sys
import time

WATCHDOG = 8
_t0 = None
_name = "sonata2"
_done = False


def start(name: str) -> None:
    global _t0, _name
    _t0, _name = time.monotonic(), name
    mark("start")
    try:
        faulthandler.dump_traceback_later(WATCHDOG, repeat=True, file=sys.stderr)
    except (ValueError, RuntimeError, OSError):
        pass


def mark(label: str) -> None:
    if _t0 is None:
        return
    now = time.time()
    wall = time.strftime("%H:%M:%S", time.localtime(now)) + f".{int(now % 1 * 1000):03d}"
    print(f"[trace {_name}] +{time.monotonic() - _t0:.2f}s {label} ({wall})", file=sys.stderr, flush=True)


def ready(label: str = "ready") -> None:
    global _done
    if _done or _t0 is None:
        return
    _done = True
    mark(label)
    faulthandler.cancel_dump_traceback_later()
