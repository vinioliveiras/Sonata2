"""Settings > Apps: one app's size, its data, and what it may use.

Permissions exist only for Flatpak apps (sandboxed): the portals' own
permission store (permstore.py: camera, location, background,
notifications) and Flatpak's overrides (network, microphone, home
folder) -- the same ones `flatpak override --user` writes. Apps from the
distro's packages run unsandboxed: nothing can be refused them.

    o = uninstall.owner(info)
    permissions(o)            # [(key, title, on)] -- [] when not Flatpak
    set_permission(o, key, on)
    app_size(o)               # bytes or None (blocking)
    data_dirs(info, o)        # the app's own folders in your home
    folder_size(paths)        # bytes (blocking)
    clear_data(paths)         # to the Trash (they can be put back)
"""
import os
import shutil
import subprocess

from gi.repository import Gio, GLib

from . import permstore

# (key, title, how): "store" = the portals' permission store (asked when
# used), else a Flatpak override (context key, value)
PERMISSIONS = (
    ("camera", "Camera", "store"),
    # (one socket for both: no microphone means no sound either -- said so)
    ("microphone", "Sound & Microphone", ("sockets", "pulseaudio")),
    ("network", "Network", ("shared", "network")),
    ("location", "Location", "store"),
    ("background", "Running in the Background", "store"),
    ("notifications", "Notifications", "store"),
    ("files", "Home Folder", ("filesystems", "home")),
)
FLAG = {"sockets": "socket", "shared": "share", "filesystems": "filesystem"}
NOT_FLAG = {"sockets": "nosocket", "shared": "unshare", "filesystems": "nofilesystem"}
HOME_ALIASES = ("home", "host")               # either gives the whole home folder


def _run(cmd, timeout=20) -> str:
    if not shutil.which(cmd[0]):
        return ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout if p.returncode == 0 else ""


def _context(text: str) -> dict:
    """[Context] of `flatpak info --show-permissions`: {key: [values]}."""
    kf = GLib.KeyFile()
    try:
        kf.load_from_data(text, len(text.encode()), GLib.KeyFileFlags.NONE)
        return {k: [v for v in kf.get_string("Context", k).split(";") if v]
                for k in kf.get_keys("Context")[0]}
    except GLib.Error:
        return {}


def _has(ctx: dict, key: str, value: str) -> bool:
    vals = ctx.get(key, [])
    if key == "filesystems":
        return any(v.split(":")[0] in HOME_ALIASES for v in vals)
    return value in vals


# Apps from packages: what Sonata can keep from them when it opens them
# (appperms.limits, enforced by sandbox.restricted_command)
NATIVE = (("camera", "Camera"), ("microphone", "Sound & Microphone"), ("network", "Network"),
          ("files", "Home Folder"))


def permissions(o, info=None) -> list:
    """[(key, title, on)]: a Flatpak app's; a packaged app's (given its
    info) the ones Sonata enforces when it opens it; [] for Sonata's own."""
    if o is None or o.kind != "flatpak":
        if info is None or o is None:
            return []
        from .. import appperms
        from . import location
        refused = appperms.limits(info.get_id())
        out = [(k, t, k not in refused) for k, t in NATIVE]
        if location.available():                  # GeoClue's own per-app setting (location.py)
            out.append(("location", "Location", location.allowed(info.get_id())))
        return out
    ctx = _context(_run(["flatpak", "info", "--show-permissions", o.name]))
    out = []
    for key, title, how in PERMISSIONS:
        if how == "store":
            _k, _t, table, ident, yes = next(k for k in permstore.KINDS if k[0] == key)
            stored = permstore.lookup(table, ident).get(o.name)
            # never asked yet: it asks first -- shown on (allowed until refused)
            on = permstore.allowed(stored, yes) if stored else True
        else:
            on = _has(ctx, *how)
        out.append((key, title, on))
    return out


def override_args(key: str, on: bool) -> list:
    how = next(h for k, _t, h in PERMISSIONS if k == key)
    ctx, value = how
    if ctx == "filesystems" and not on:
        return [f"--nofilesystem={v}" for v in HOME_ALIASES]
    return [f"--{(FLAG if on else NOT_FLAG)[ctx]}={value}"]


def set_permission(o, key: str, on: bool, info=None) -> bool:
    if o is None or o.kind != "flatpak":
        if o is None or info is None:
            return False
        if key == "location":
            from . import location
            return location.set_app(info.get_id(), on)
        from .. import appperms
        appperms.set_limit(info.get_id(), key, not on)
        return True
    how = next(h for k, _t, h in PERMISSIONS if k == key)
    if how == "store":
        return permstore.set_allowed(key, o.name, on)
    cmd = ["flatpak", "override", "--user", *override_args(key, on), o.name]
    if not shutil.which("flatpak"):
        return False
    try:
        return subprocess.run(cmd, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _human_size(text: str) -> int:
    """"312.4 MB" / "1,2 GiB" / "12345" (bytes) -> bytes."""
    parts = text.replace(",", ".").replace("\xa0", " ").split()
    if not parts:
        return 0
    try:
        n = float(parts[0])
    except ValueError:
        return 0
    unit = (parts[1] if len(parts) > 1 else "B").lower().rstrip("b").rstrip("i")
    return int(n * 1024 ** "_kmgt".index(unit)) if unit in ("k", "m", "g", "t") else int(n)


def app_size(o):
    """The installed app's size in bytes (None: unknown)."""
    if o is None:
        return None
    if o.kind == "flatpak":
        for line in _run(["flatpak", "info", o.name]).splitlines():
            if line.strip().startswith("Installed:"):
                return _human_size(line.split(":", 1)[1])
    elif o.manager == "pacman":
        for line in _run(["pacman", "-Qi", o.name]).splitlines():
            if line.startswith("Installed Size"):
                return _human_size(line.split(":", 1)[1])
    elif o.manager == "apt":
        out = _run(["dpkg-query", "-W", "-f", "${Installed-Size}", o.name]).strip()
        return int(out) * 1024 if out.isdigit() else None
    elif o.manager in ("dnf", "zypper"):
        out = _run(["rpm", "-q", "--qf", "%{SIZE}", o.name]).strip()
        return int(out) if out.isdigit() else None
    return None


def _names(info, o) -> list:
    """What an app's folders are usually called: its id and its last part,
    its command's name (org.gnome.Calculator, calculator, gnome-calculator)."""
    did = (info.get_id() or "").removesuffix(".desktop")
    names = {did, did.rsplit(".", 1)[-1]}
    exe = (info.get_executable() or "") if hasattr(info, "get_executable") else ""
    names.add(os.path.basename(exe))
    if o is not None and o.kind == "package":
        names.add(o.name)
    out = set()
    for n in names:
        if len(n) >= 3 and n not in ("env", "sh", "bash", "flatpak", "python3", "python", "sonata2"):
            out.update({n, n.lower()})
    return sorted(out)


def data_dirs(info, o=None) -> list:
    """The app's own folders: Flatpak's ~/.var/app/<id>, else the ones named
    like it in ~/.config, ~/.local/share, ~/.local/state, ~/.cache.
    Sonata's own apps: none (their settings are Sonata's)."""
    from .. import webapps
    if webapps.is_webapp(info.get_id() or ""):           # its login and storage
        p = webapps.data_dir(webapps.id_of(info.get_id()))
        return [p] if os.path.isdir(p) else []
    if (info.get_id() or "").startswith(("io.github.vinioliveiras.sonata2.", "sonata2-")):
        return []
    home = GLib.get_home_dir()
    if o is not None and o.kind == "flatpak":
        p = os.path.join(home, ".var", "app", o.name)
        return [p] if os.path.isdir(p) else []
    bases = [GLib.get_user_config_dir(), GLib.get_user_data_dir(), GLib.get_user_cache_dir(),
             GLib.getenv("XDG_STATE_HOME") or os.path.join(home, ".local", "state")]
    out = []
    for base in bases:
        for n in _names(info, o):
            p = os.path.join(base, n)
            if os.path.isdir(p) and not os.path.islink(p) and p not in out:
                out.append(p)
    return out


def folder_size(paths) -> int:
    total = 0
    for top in paths:
        for root, _dirs, files in os.walk(top):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
    return total


def clear_data(paths) -> list:
    """Into the Trash (put back from there); returns the ones that failed."""
    failed = []
    for p in paths:
        try:
            Gio.File.new_for_path(p).trash(None)
        except GLib.Error:
            failed.append(p)
    return failed
