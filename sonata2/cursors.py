"""The mouse pointer (Settings > Mouse & Trackpad > Pointer, Vini): Sonata's
own pointers by default, any other pointer theme installed, one added from
a downloaded file (a .tar.gz / .tar.xz / .zip from gnome-look and the like,
or a folder), its size, and back to Sonata's.

Where it shows: Wayfire draws the pointer ([input] cursor_theme / size,
applied at once); GTK and the apps that read the desktop's settings follow
Sonata's settings (prefs: cursor-theme / cursor-size, through the portal and
XSETTINGS); other apps read XCURSOR_THEME / XCURSOR_SIZE at login
(prefs session-env).

    cursors.themes()            # [(name, label)]: Sonata's first
    cursors.current()           # "Sonata-Cursors"
    cursors.apply("Bibata-Modern-Ice")
    cursors.add("~/Downloads/Bibata.tar.xz")    # the themes it held, installed
    cursors.reset()             # Sonata's pointer, its normal size"""
import os
import shutil
import tarfile
import tempfile
import zipfile

DEFAULT = "Sonata-Cursors"
DEFAULT_SIZE = 24
SIZES = (24, 32, 48, 64)              # the sizes pointer themes are drawn at (others snap to one)
ARCHIVES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar", ".zip")
BUNDLED = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "icons")


def user_dir() -> str:
    """Where added pointers go: ~/.local/share/icons (every toolkit looks there)."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "icons")


def search_path() -> list:
    """The folders pointer themes are looked up in, in XCURSOR_PATH's order."""
    home = os.path.expanduser("~")
    dirs = [BUNDLED, user_dir(), os.path.join(home, ".icons")]
    for d in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":"):
        if d:
            dirs.append(os.path.join(d, "icons"))
    dirs += ["/usr/share/icons", "/usr/share/pixmaps"]
    out = []
    for d in dirs:
        if d not in out:
            out.append(d)
    return out


def is_theme(folder: str) -> bool:
    """A pointer theme: a folder with a cursors/ folder holding the pointer."""
    cur = os.path.join(folder, "cursors")
    return os.path.isdir(cur) and any(os.path.exists(os.path.join(cur, n))
                                      for n in ("default", "left_ptr", "arrow"))


def _label(folder: str, name: str) -> str:
    """index.theme's Name, else the folder's name."""
    try:
        with open(os.path.join(folder, "index.theme"), encoding="utf-8", errors="replace") as f:
            for line in f:
                key, _eq, value = line.partition("=")
                if key.strip() == "Name" and value.strip():
                    return value.strip()
    except OSError:
        pass
    return name


def themes() -> list:
    """[(name, label)] of every pointer theme found, Sonata's first, then by label."""
    found = {}
    for d in search_path():
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            folder = os.path.join(d, name)
            if name not in found and is_theme(folder):
                found[name] = _label(folder, name)
    rest = sorted(((n, lbl) for n, lbl in found.items() if n != DEFAULT), key=lambda t: t[1].casefold())
    return [(DEFAULT, found.get(DEFAULT, "Sonata"))] + rest


def current() -> str:
    from . import prefs
    return prefs.get(prefs.I, "cursor-theme") or DEFAULT


def size() -> int:
    from . import prefs
    try:
        return int(prefs.get(prefs.I, "cursor-size") or DEFAULT_SIZE)
    except ValueError:
        return DEFAULT_SIZE


def apply(name: str = None, px: int = None) -> None:
    """The pointer theme and / or size, everywhere it can change now."""
    from . import prefs, wfconfig
    if name is not None:
        prefs.set(prefs.I, "cursor-theme", name)
        wfconfig.wayfire_set("input", "cursor_theme", name)
    if px is not None:
        prefs.set(prefs.I, "cursor-size", str(int(px)))
        wfconfig.wayfire_set("input", "cursor_size", int(px))


def reset() -> None:
    """Sonata's pointer at its normal size."""
    apply(DEFAULT, DEFAULT_SIZE)


class NotATheme(Exception):
    """The file held no pointer theme."""


def _safe_members(tar: tarfile.TarFile, dest: str):
    """Only files and folders that stay inside `dest` (no links out, no
    devices, no ../): a downloaded archive is never trusted."""
    root = os.path.realpath(dest)
    for m in tar.getmembers():
        if not (m.isfile() or m.isdir() or m.issym()):
            continue
        target = os.path.realpath(os.path.join(root, m.name))
        if target != root and not target.startswith(root + os.sep):
            continue
        if m.issym():                 # pointer themes link one name to another: inside the theme only
            link = os.path.realpath(os.path.join(os.path.dirname(target), m.linkname))
            if not link.startswith(root + os.sep):
                continue
        yield m


def _unpack(path: str, dest: str) -> None:
    low = path.lower()
    if low.endswith(".zip"):
        root = os.path.realpath(dest)
        with zipfile.ZipFile(path) as z:
            for info in z.infolist():
                target = os.path.realpath(os.path.join(root, info.filename))
                if target == root or target.startswith(root + os.sep):
                    z.extract(info, dest)
        return
    with tarfile.open(path) as tar:
        tar.extractall(dest, members=list(_safe_members(tar, dest)))


def _found_in(folder: str) -> list:
    """The pointer themes in an unpacked folder (the top, or a few levels down)."""
    out = []
    for here, dirs, _files in os.walk(folder):
        if here[len(folder):].count(os.sep) > 3:
            dirs[:] = []
            continue
        if is_theme(here):
            out.append(here)
            dirs[:] = []                  # a theme's own folders aren't themes
    return out


def add(path: str) -> list:
    """Install the pointer themes in a downloaded file or a folder into
    ~/.local/share/icons; returns their names. A theme of the same name is
    replaced (the newer download). NotATheme when there is none."""
    path = os.path.expanduser(path)
    with tempfile.TemporaryDirectory() as tmp:
        if os.path.isdir(path):
            src = path
        elif path.lower().endswith(ARCHIVES):
            try:
                _unpack(path, tmp)
            except (tarfile.TarError, zipfile.BadZipFile, OSError, EOFError) as e:
                raise NotATheme(str(e)) from e
            src = tmp
        else:
            raise NotATheme(path)
        found = _found_in(src)
        if not found:
            raise NotATheme(path)
        os.makedirs(user_dir(), exist_ok=True)
        names = []
        for folder in found:
            name = os.path.basename(os.path.normpath(folder))
            if folder == os.path.normpath(src) and src == tmp:
                name = os.path.basename(path).split(".")[0] or "Pointer"
            if name == DEFAULT:
                name = f"{DEFAULT}-custom"       # never over Sonata's own
            dest = os.path.join(user_dir(), name)
            if os.path.realpath(folder) == os.path.realpath(dest):
                names.append(name)               # already there (picked from ~/.local/share/icons)
                continue
            if os.path.lexists(dest):
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(folder, dest, symlinks=True)
            names.append(name)
    return names
