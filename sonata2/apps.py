"""Installed applications (.desktop entries) and the default Dock pins."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio  # noqa: E402

# GLib >= 2.86 moved DesktopAppInfo to GioUnix (Gio's copy is deprecated);
# older GLib only has Gio's.
try:
    gi.require_version("GioUnix", "2.0")
    from gi.repository import GioUnix  # noqa: E402
    DesktopAppInfo = getattr(GioUnix, "DesktopAppInfo", None) or Gio.DesktopAppInfo
except (ValueError, ImportError):
    DesktopAppInfo = Gio.DesktopAppInfo


# -- app names without packaging noise --------------------------------------------------
# "Spotify (Launcher)", "Discord (Flatpak)", "Firefox (Wayland)": macOS shows
# just the app's name. Every place that names an app goes through
# DesktopAppInfo.get_display_name/get_name, so they are cleaned there, once.
import re  # noqa: E402

_NOISE = re.compile(r"\s*[\(\[](?:launcher|flatpak|snap|appimage|wayland|x11|xwayland|native|"
                    r"official|unofficial|web ?app|electron|bin|git|stable)[\)\]]\s*$|"
                    r"\s+-\s+(?:launcher|flatpak|snap)$", re.IGNORECASE)


def clean_name(name: str) -> str:
    if not name:
        return name
    out = _NOISE.sub("", name).strip()
    return out or name


def _cleaned(method):
    def wrapper(self, *a):
        return clean_name(method(self, *a))
    wrapper.__name__ = method.__name__
    return wrapper


def _gpu_aware(method):
    """Every Sonata launch path goes here: the discrete GPU's environment
    for apps that want it (gpu.py); never a GTK_THEME (apps read the theme
    from Sonata's settings)."""
    def wrapper(self, arg, context=None, *rest):
        try:
            from . import gpu
            if gpu.wants_discrete(self):
                if context is None:
                    context = Gio.AppLaunchContext()
                for k, v in gpu.discrete_env().items():
                    context.setenv(k, v)
            for k, v in gpu.extra_env(self).items():
                if context is None:
                    context = Gio.AppLaunchContext()
                context.setenv(k, v)
            import os as _os
            # never hand these on: GTK_THEME breaks libadwaita apps; the shell's
            # ADW_DISABLE_PORTAL would keep apps from following Dark Mode
            for name in ("GTK_THEME", "ADW_DISABLE_PORTAL"):
                if name in _os.environ:
                    if context is None:
                        context = Gio.AppLaunchContext()
                    context.unsetenv(name)
        except Exception as e:                    # never keep an app from opening
            print(f"sonata2: discrete GPU: {e}")
        return method(self, arg, context, *rest)
    wrapper.__name__ = method.__name__
    return wrapper


for _cls in {DesktopAppInfo, Gio.DesktopAppInfo}:
    for _m in ("launch", "launch_uris"):
        if not getattr(getattr(_cls, _m, None), "_sonata_gpu", False):
            _w = _gpu_aware(getattr(_cls, _m))
            _w._sonata_gpu = True
            setattr(_cls, _m, _w)
    for _m in ("get_display_name", "get_name"):
        if not getattr(getattr(_cls, _m, None), "_sonata_clean", False):
            _w = _cleaned(getattr(_cls, _m))
            _w._sonata_clean = True
            setattr(_cls, _m, _w)

# Default pins, macOS order: Finder, browser, Mail, ..., Terminal, Settings.
# Each slot lists candidate desktop ids across distros/desktops; the first one
# installed wins, so the Dock never shows a broken icon.
DEFAULT_SLOTS = (
    ("io.github.vinioliveiras.sonata2.files", "org.gnome.Nautilus", "nautilus", "org.kde.dolphin", "nemo", "thunar", "pcmanfm-qt", "pcmanfm"),
    ("@browser",),   # the user's default web browser
    ("org.gnome.Evolution", "org.mozilla.Thunderbird", "thunderbird", "geary", "org.gnome.Geary"),
    ("org.gnome.Calendar", "org.kde.merkuro.calendar"),
    ("org.gnome.Music", "rhythmbox", "org.gnome.Rhythmbox3", "elisa", "org.kde.elisa", "spotify"),
    ("io.github.vinioliveiras.sonata2.preview", "org.gnome.Loupe", "org.gnome.eog", "eog", "org.kde.gwenview", "gwenview"),
    ("io.github.vinioliveiras.sonata2.textedit", "org.gnome.TextEditor", "org.gnome.gedit", "org.kde.kate", "mousepad", "code", "code-oss"),
    ("steam",),
    ("io.github.vinioliveiras.sonata2.terminal", "org.gnome.Console", "org.gnome.Terminal", "com.mitchellh.ghostty", "kitty", "Alacritty",
     "foot", "org.kde.konsole", "xfce4-terminal"),
    ("io.github.vinioliveiras.sonata2.feedback",),   # last: Feedbacker (can be removed)
)


# Sonata's own apps (Files, System Settings, Launchpad): part of the system,
# never deleted or trashed even though their entries live in ~/.local
PROTECTED = ("io.github.vinioliveiras.sonata2.", "sonata2-")


def write_desktop_file(filename: str, text: str) -> str:
    """Write ~/.local/share/applications/<filename> (only when it changed)."""
    import os
    from gi.repository import GLib
    path = os.path.join(GLib.get_user_data_dir(), "applications", filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def app_dirs() -> list:
    """Every applications folder, highest priority first: the user's, the
    XDG_DATA_DIRS ones, and Flatpak's exports even when the session's
    XDG_DATA_DIRS lacks them (a login screen doesn't run /etc/profile.d)."""
    import os
    from gi.repository import GLib
    data = [GLib.get_user_data_dir()] + list(GLib.get_system_data_dirs())
    data += [os.path.join(GLib.get_user_data_dir(), "flatpak", "exports", "share"),
             "/var/lib/flatpak/exports/share"]
    out = []
    for d in data:
        p = os.path.join(d, "applications")
        if p not in out:
            out.append(p)
    return out


def signature() -> tuple:
    """Cheap: changes when any applications folder (or subfolder) changes."""
    import os
    sig = []
    for d in app_dirs():
        for root, dirs, _files in os.walk(d):
            try:
                sig.append((root, os.stat(root).st_mtime_ns))
            except OSError:
                pass
    return tuple(sig)


_scan = {}          # desktop id -> DesktopAppInfo (scan())


def scan() -> dict:
    """desktop id -> DesktopAppInfo, read from the folders themselves (GIO's
    own list only notices folders it watched since the process started)."""
    import os
    global _scan
    out = {}
    for d in app_dirs():
        for root, _dirs, files in os.walk(d):
            for n in files:
                if not n.endswith(".desktop"):
                    continue
                did = os.path.relpath(os.path.join(root, n), d).replace(os.sep, "-")
                if did in out:
                    continue                           # a higher-priority folder has it
                try:
                    info = DesktopAppInfo.new_from_filename(os.path.join(root, n))
                except TypeError:
                    info = None
                out[did] = info
    _scan = {k: v for k, v in out.items() if v is not None}
    return dict(_scan)


def lookup(desktop_id: str):
    """Gio.DesktopAppInfo for 'firefox' or 'firefox.desktop', or None."""
    if not desktop_id.endswith(".desktop"):
        desktop_id += ".desktop"
    try:
        return DesktopAppInfo.new(desktop_id)
    except TypeError:   # PyGObject raises on a NULL constructor result
        return _scan.get(desktop_id)          # installed after GIO's first look (see scan())


def _default_browser():
    info = Gio.AppInfo.get_default_for_uri_scheme("https")
    return info.get_id() if info else None


def default_pins() -> list:
    pins = []
    for slot in DEFAULT_SLOTS:
        for cand in slot:
            did = _default_browser() if cand == "@browser" else cand
            if did and lookup(did):
                did = did[:-8] if did.endswith(".desktop") else did
                if did not in pins:
                    pins.append(did)
                break
    return pins


_INDEX = None


def _build_index() -> dict:
    """Lower-cased app_id candidates -> desktop id (without .desktop)."""
    idx = {}
    for info in Gio.AppInfo.get_all():
        did = info.get_id() or ""
        if not did.endswith(".desktop"):
            continue
        did = did[:-8]
        keys = [did, did.rsplit(".", 1)[-1]]
        if isinstance(info, DesktopAppInfo):
            wm = info.get_startup_wm_class()
            if wm:
                keys.insert(0, wm)
            exe = (info.get_executable() or "").rsplit("/", 1)[-1]
            if exe:
                keys.append(exe)
        for k in keys:
            idx.setdefault(k.lower(), did)
    return idx


def match_app_id(app_id: str):
    """Desktop id for a Wayland app_id (exact, StartupWMClass, last reverse-DNS
    part, executable), or None when no .desktop matches."""
    global _INDEX
    if not app_id:
        return None
    if lookup(app_id):
        return app_id[:-8] if app_id.endswith(".desktop") else app_id
    if _INDEX is None:
        _INDEX = _build_index()
    a = app_id.lower()
    return _INDEX.get(a) or _INDEX.get(a.rsplit(".", 1)[-1])


def refresh() -> None:
    """Forget the app_id index (call when apps are installed/removed)."""
    global _INDEX
    _INDEX = None
