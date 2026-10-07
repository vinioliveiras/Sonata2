"""Open in Sandbox (Vini): an app with an empty home folder of its own --
none of your files, sign-ins or settings, and nothing it does is kept:
the folder is deleted when the app quits. bubblewrap (bwrap) mounts a
temporary folder over your home for the app alone; a Flatpak app gets
one over its ~/.var/app/<id> and no access to your home.

The Dock shows an orange "S" on the app while a sandboxed copy runs
(running()). An app that is already open may hand the request to its
open window (one-window apps); quit it first.

    available()            # bwrap installed
    open_app(info)         # starts it; False when it can't
"""
import os
import re
import shlex
import shutil
import tempfile

from gi.repository import Gio, GLib

_running = {}           # desktop id -> number of sandboxed copies running
listeners = []          # fn() when that changes (the Dock's badges)


def available() -> bool:
    return shutil.which("bwrap") is not None


def running(app_id: str) -> bool:
    return _running.get(app_id, 0) > 0


def _flatpak_id(info) -> str:
    from .appperms import flatpak_id
    return flatpak_id(info)


def command(info, home: str) -> list:
    """argv: the app inside bwrap, `home` (empty, temporary) as its home."""
    real_home = GLib.get_home_dir()
    fid = _flatpak_id(info)
    if fid:
        data = os.path.join(real_home, ".var", "app", fid)
        os.makedirs(data, exist_ok=True)                  # (the mount needs it there; Flatpak makes it anyway)
        return ["bwrap", "--dev-bind", "/", "/", "--bind", home, data,
                "flatpak", "run", "--nofilesystem=home", "--nofilesystem=host", fid]
    argv = exec_args(info.get_commandline() or "")
    if not argv:
        return []
    # (/tmp stays: X11 apps reach Xwayland through /tmp/.X11-unix)
    return ["bwrap", "--dev-bind", "/", "/", "--bind", home, real_home,
            "--setenv", "HOME", real_home, "--chdir", real_home, "--", *argv]


def restricted_command(info, refused, uris=()) -> list:
    """argv: a packaged app kept from what it may not use (appperms.limits):
    network (its own empty network), camera (/dev/video*, /dev/media*
    hidden), microphone (and sound: the PipeWire / PulseAudio sockets and
    /dev/snd hidden), files (a home of its own, kept between opens, in
    Sonata's data folder, userdata.folder("app-homes")/<app> -- never the
    install, which ./install.sh replaces)."""
    import glob
    argv = exec_args(info.get_commandline() or "", uris)
    if not argv:
        return []
    out = ["bwrap", "--dev-bind", "/", "/"]
    if "network" in refused:
        out.append("--unshare-net")
    if "camera" in refused:
        for dev in sorted(glob.glob("/dev/video*") + glob.glob("/dev/media*")):
            out += ["--ro-bind", "/dev/null", dev]
    if "microphone" in refused:
        rt = GLib.get_user_runtime_dir() or ""
        for sock in glob.glob(os.path.join(rt, "pipewire-*")):
            if not sock.endswith(".lock"):
                out += ["--ro-bind", "/dev/null", sock]
        if os.path.isdir(os.path.join(rt, "pulse")):
            out += ["--tmpfs", os.path.join(rt, "pulse")]
        if os.path.isdir("/dev/snd"):
            out += ["--tmpfs", "/dev/snd"]
    if "files" in refused:
        home = GLib.get_home_dir()
        from . import userdata
        own = os.path.join(userdata.folder("app-homes"), (info.get_id() or "app").removesuffix(".desktop"))
        os.makedirs(own, mode=0o700, exist_ok=True)
        out += ["--bind", own, home]
    return out + ["--", *argv]


def run_restricted(info, refused, uris=(), launch_env=None) -> bool:
    argv = restricted_command(info, refused, uris)
    if not argv or not available():
        return False
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
    for k, v in (launch_env or {}).items():
        launcher.setenv(k, v, True)
    try:
        launcher.spawnv(argv)
        return True
    except GLib.Error as e:
        print(f"sonata2: restricted launch: {e.message}", flush=True)
        return False


def exec_args(line: str, uris=()) -> list:
    """An Exec line with its field codes (%U, %f, --uri=%U...) filled with
    `uris` (file paths for %f/%F), or out when there are none."""
    out = []
    for a in shlex.split(line):
        if a in ("%U", "%F", "%u", "%f") and uris:
            files = [Gio.File.new_for_uri(u).get_path() or u if "%f" in a.lower() else u for u in uris]
            out += files if a in ("%U", "%F") else files[:1]
            continue
        b = re.sub(r"%[fFuUdDnNickvm]", "", a).replace("%%", "%")
        if b and not (b != a and b.endswith("=")):
            out.append(b)
    return out


def open_app(info, launch_env=None) -> bool:
    if not available():
        return False
    base = GLib.get_user_runtime_dir() or GLib.get_tmp_dir()
    try:
        home = tempfile.mkdtemp(prefix="sonata-sandbox-", dir=base)    # 0700: the app's alone
    except OSError:
        return False
    argv = command(info, home)
    if not argv:
        shutil.rmtree(home, ignore_errors=True)
        return False
    launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.NONE)
    for k, v in (launch_env or {}).items():
        launcher.setenv(k, v, True)
    try:
        proc = launcher.spawnv(argv)
    except GLib.Error as e:
        print(f"sonata2: sandbox: {e.message}", flush=True)
        shutil.rmtree(home, ignore_errors=True)
        return False
    app_id = (info.get_id() or "").removesuffix(".desktop")
    _running[app_id] = _running.get(app_id, 0) + 1
    _changed()

    def gone(p, res):
        try:
            p.wait_finish(res)
        except GLib.Error:
            pass
        shutil.rmtree(home, ignore_errors=True)          # nothing kept
        _running[app_id] = max(0, _running.get(app_id, 0) - 1)
        _changed()
    proc.wait_async(None, gone)
    return True


def _changed() -> None:
    for fn in list(listeners):
        try:
            fn()
        except Exception as e:
            print(f"sonata2: sandbox listener: {e}", flush=True)


def ask_open(info) -> None:
    """The menus' "Open in Sandbox": said why when it can't."""
    from . import applock
    from .ui import dialog
    if not available():
        dialog.alert("Open in Sandbox needs bubblewrap",
                     "Install the bubblewrap package (./install.sh installs it), then try again.",
                     [("ok", "OK", "default")])
        return

    def go():
        if not open_app(info):
            dialog.alert(f"“{info.get_display_name()}” couldn't open in a sandbox", "",
                         [("ok", "OK", "default")])
    # a locked app asks its password here too (this launch skips apps.py's wrapper)
    if not applock.gate(info, go):
        go()
