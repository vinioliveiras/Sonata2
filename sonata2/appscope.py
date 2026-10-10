"""Every app opened from Sonata in a systemd scope of its own, under
sonata-apps.slice, which systemd-oomd watches (macOS: an app that runs the
memory out is closed, the computer goes on). Apps together never take the
memory kept for Sonata and the system (reserve_mb).

GNOME does the same (app-gnome-<id>-<pid>.scope): the launched process is
moved into a transient scope over D-Bus right after it starts. Nothing is
capped in normal use; when the whole system's memory is under pressure (or
swap is nearly full), systemd-oomd kills the app that is causing it, not the
session. Web apps make their own scope (webapps.main) in the same slice.

The slice is written to $XDG_RUNTIME_DIR/systemd/user (gone at logout,
nothing left behind)."""
import os

from gi.repository import Gio, GLib

SLICE = "sonata-apps.slice"
# Memory kept for Sonata and the system: apps together never take it (the
# menu bar, the Dock and Wayfire stay responsive while an app runs away).
RESERVE_MIN_MB, RESERVE_MAX_MB, RESERVE_SHARE = 1536, 4096, 0.10
APP_OOM_SCORE = 200        # the kernel's own OOM killer, if it ever comes to it: apps before Sonata


def reserve_mb(total_mb: int) -> int:
    return int(min(RESERVE_MAX_MB, max(RESERVE_MIN_MB, total_mb * RESERVE_SHARE)))


def slice_text(total_mb: int = None) -> str:
    if total_mb is None:
        from .webapps import _meminfo
        total_mb = _meminfo().get("MemTotal", 0)
    limits = ""
    if total_mb > 2 * RESERVE_MIN_MB:
        top = total_mb - reserve_mb(total_mb)
        limits = f"MemoryHigh={top - 512}M\nMemoryMax={top}M\n"      # slowed down first, then the cap
    return ("[Unit]\nDescription=Apps opened from Sonata\n\n[Slice]\n" + limits +
            "ManagedOOMMemoryPressure=kill\nManagedOOMMemoryPressureLimit=50%\n"
            "ManagedOOMMemoryPressureDurationSec=10s\nManagedOOMSwap=kill\n")


OWN_SCOPE = ("io.github.vinioliveiras.sonata2.webapp.",)     # make their own scope
_SAFE = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_.")


def escape(app_id: str) -> str:
    """systemd's unit-name escaping ("-" too: it separates the name's parts)."""
    return "".join(c if c in _SAFE else "".join(f"\\x{b:02x}" for b in c.encode()) for c in app_id or "app")


def unit_name(app_id: str, pid: int) -> str:
    """app-sonata2-<id>-<pid>.scope (the XDG name: app-<launcher>-<id>-<random>)."""
    if app_id.endswith(".desktop"):
        app_id = app_id[:-len(".desktop")]
    return f"app-sonata2-{escape(app_id)}-{int(pid)}.scope"


def slice_path() -> str:
    run = os.environ.get("XDG_RUNTIME_DIR") or GLib.get_user_runtime_dir()
    return os.path.join(run, "systemd", "user", SLICE)


def _bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def _manager(bus, method, params, sig=None):
    bus.call("org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.systemd1.Manager",
             method, params, GLib.VariantType(sig) if sig else None, Gio.DBusCallFlags.NONE, 5000, None,
             None, None)


def ensure_slice() -> bool:
    """The slice in place (once per session: rewritten only when it changed)."""
    path, text = slice_path(), slice_text()
    try:
        with open(path) as f:
            if f.read() == text:
                return True
    except OSError:
        pass
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            f.write(text)
        os.replace(path + ".tmp", path)
    except OSError:
        return False
    bus = _bus()
    if bus is not None:
        _manager(bus, "Reload", None)
    return True


# the newest scope of each app opened from Sonata: {desktop id: unit name}
# (the Dock stops one whose app never showed a window -- stuck while starting)
launched = {}


def stop(app_id: str) -> bool:
    """Stop the app's newest scope (every process of that launch). False: none known."""
    unit = launched.pop(app_id or "", None)
    bus = _bus()
    if not unit or bus is None:
        return False
    _manager(bus, "StopUnit", GLib.Variant("(ss)", (unit, "replace")))
    return True


# an app's processes besides its launcher's name (Steam's own runs from ~/.local/share/Steam)
EXTRA_PROCESSES = {"steam": ("steam", "steamwebhelper", "steam.sh")}


def process_names(info) -> set:
    """The process names (/proc/<pid>/comm, 15 characters) an app runs as:
    its command's program, and the known extra ones (Steam's)."""
    from .sandbox import exec_args
    names = set()
    argv = exec_args((info.get_commandline() if info is not None else "") or "")
    if argv:
        prog = os.path.basename(argv[0])
        if prog not in ("env", "sh", "bash", "flatpak", "python3", "python"):
            names.add(prog[:15])
    key = ((info.get_id() if info is not None else "") or "").removesuffix(".desktop")
    names.update(n[:15] for n in EXTRA_PROCESSES.get(key, ()))
    return names


def kill(app_id: str, info=None, proc="/proc") -> int:
    """Force the app closed (Vini: before opening it again when it didn't
    open): every process of its launch's scope, and the user's own processes
    running as the app's program (one left by an earlier launch, outside the
    scope -- Steam's). SIGKILL. Returns how many processes were signalled."""
    import signal
    n = 0
    unit = launched.pop(app_id or "", None)
    bus = _bus()
    if unit and bus is not None:
        _manager(bus, "KillUnit", GLib.Variant("(ssi)", (unit, "all", int(signal.SIGKILL))))
        n += 1
    names = process_names(info)
    if not names:
        return n
    me, uid = os.getpid(), os.getuid()
    try:
        pids = [int(p) for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return n
    for pid in pids:
        if pid == me:
            continue
        try:
            if os.stat(os.path.join(proc, str(pid))).st_uid != uid:
                continue
            with open(os.path.join(proc, str(pid), "comm"), encoding="utf-8") as f:
                comm = f.read().strip()
        except OSError:
            continue
        if comm in names:
            try:
                os.kill(pid, signal.SIGKILL)
                n += 1
            except OSError:
                pass
    return n


def move(pid: int, app_id: str) -> bool:
    """The launched app's process into its scope (asynchronous)."""
    if not pid or any((app_id or "").startswith(p) for p in OWN_SCOPE):
        return False
    launched[app_id or ""] = unit_name(app_id, pid)
    bus = _bus()
    if bus is None:
        return False
    props = [("PIDs", GLib.Variant("au", [int(pid)])),
             ("Slice", GLib.Variant("s", SLICE)),
             ("CollectMode", GLib.Variant("s", "inactive-or-failed")),
             ("Description", GLib.Variant("s", f"{app_id} (opened from Sonata)"))]
    _manager(bus, "StartTransientUnit",
             GLib.Variant("(ssa(sv)a(sa(sv)))", (unit_name(app_id, pid), "fail", props, [])))
    raise_oom_score(pid)
    return True


def raise_oom_score(pid: int, score: int = APP_OOM_SCORE) -> bool:
    """Apps go before Sonata if the kernel has to kill (raising it needs no rights)."""
    try:
        with open(f"/proc/{int(pid)}/oom_score_adj", "r+") as f:
            if int(f.read().strip() or 0) >= score:
                return True
            f.seek(0)
            f.write(str(score))
        return True
    except (OSError, ValueError):
        return False


def flush() -> None:
    """Send the pending scope moves now (a short-lived launcher -- the login
    items' -- would exit before they leave)."""
    bus = _bus()
    if bus is not None:
        try:
            bus.flush_sync(None)
        except GLib.Error:
            pass


def watch(context, app_id: str) -> None:
    """Move what `context` launches (Gio's "launched": the new process' pid)."""
    if context is None or getattr(context, "_sonata_scope", False):
        return
    context._sonata_scope = True

    def launched(_ctx, info, platform_data):
        try:
            pid = platform_data.lookup_value("pid", GLib.VariantType("i"))
            move(pid.get_int32() if pid else 0, (info.get_id() if info else None) or app_id)
        except Exception as e:                  # never in the way of an app opening
            print(f"sonata2: app scope: {e}")
    context.connect("launched", launched)
