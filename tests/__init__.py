"""The tests never touch the session they're run from (2026-11-04: run on
Vini's desktop, tests rewrote his Dock, Launchpad and web apps -- the
config folder is fixed when sonata2.config is first imported, and a module
that set XDG_CONFIG_HOME after another had imported it wrote to the real
~/.config). Run first, on the first `import tests.<anything>`, before any
test module or sonata2 is imported:

- HOME and every XDG folder (config, data, cache, state, runtime) are
  temporary: settings, web apps, desktop entries, logs and icon caches go
  there, never into the person's own;
- WAYFIRE_SOCKET is dropped: nothing reaches the running compositor;
- a private D-Bus session bus (notifications, portals, the menu bar's
  services are not the real ones);
- no window on the real screen: WAYLAND_DISPLAY is dropped, and an X
  display that isn't xvfb-run's is too -- UI tests then skip. Run them
  with `xvfb-run -a`, or in a compositor of their own with
  SONATA2_TESTS_WAYLAND=1 (its WAYLAND_DISPLAY kept)."""
import atexit
import os
import shutil
import subprocess
import tempfile

_ROOT = tempfile.mkdtemp(prefix="sonata2-tests-")
_REAL_RUNTIME = os.environ.get("XDG_RUNTIME_DIR", "")


def _dir(*parts) -> str:
    path = os.path.join(_ROOT, *parts)
    os.makedirs(path, exist_ok=True)
    return path


def _isolate() -> None:
    env = os.environ
    home = _dir("home")
    for sub in ("Downloads", "Documents", "Pictures", "Music", "Videos", "Desktop"):
        _dir("home", sub)
    env["HOME"] = home
    env["XDG_CONFIG_HOME"] = _dir("home", ".config")
    env["XDG_DATA_HOME"] = _dir("home", ".local", "share")
    env["XDG_CACHE_HOME"] = _dir("home", ".cache")
    env["XDG_STATE_HOME"] = _dir("home", ".local", "state")
    runtime = _dir("runtime")
    os.chmod(runtime, 0o700)
    # a compositor of the tests' own (SONATA2_TESTS_WAYLAND=1): its socket
    # kept, named by its full path now the runtime folder moves
    wl = env.get("WAYLAND_DISPLAY", "")
    if env.get("SONATA2_TESTS_WAYLAND") == "1" and wl:
        env["WAYLAND_DISPLAY"] = wl if os.path.isabs(wl) else os.path.join(_REAL_RUNTIME, wl)
    else:
        env.pop("WAYLAND_DISPLAY", None)
        if env.get("DISPLAY") and "xvfb-run" not in env.get("XAUTHORITY", ""):
            env.pop("DISPLAY", None)                  # the person's own X display (Xwayland)
    env["XDG_RUNTIME_DIR"] = runtime
    env.pop("WAYFIRE_SOCKET", None)
    env.pop("SWAYSOCK", None)
    _private_bus()


def _private_bus() -> None:
    if not shutil.which("dbus-daemon"):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"
        return
    try:
        out = subprocess.run(["dbus-daemon", "--session", "--fork", "--print-address=1", "--print-pid=1"],
                             capture_output=True, text=True, timeout=10).stdout.split()
    except (OSError, subprocess.SubprocessError):
        out = []
    if len(out) >= 2:
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = out[0]
        pid = int(out[1])
        atexit.register(lambda: _kill(pid))
    else:
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = "disabled:"


def _kill(pid: int) -> None:
    try:
        os.kill(pid, 15)
    except OSError:
        pass


_isolate()
atexit.register(lambda: shutil.rmtree(_ROOT, ignore_errors=True))
