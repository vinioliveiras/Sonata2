"""Installing an AppImage (Vini: Install in its right-click, Files and the
desktop), the way an app is dragged into Applications on macOS: the file is
copied into ~/Applications and made executable, and a launcher is written
for it with the name, icon and categories the AppImage carries -- it's then
in the Apps Menu, Launchpad and Spotlight, and can be kept in the Dock.
Uninstalling it (Dock, Launchpad, Settings > Apps) moves both the launcher
and the AppImage to the Trash (backend/uninstall.py: a "shortcut" whose
launcher names its file in X-Sonata-AppImage).

    path = appimages.install("/home/me/Downloads/App-1.0.AppImage")   # (blocking: run it off the main loop)
"""
import configparser
import glob
import os
import re
import shutil
import subprocess
import tempfile

from gi.repository import GLib

APPS_DIR = "~/Applications"
KEY = "X-Sonata-AppImage"
EXTRACT_TIMEOUT_S = 30


def apps_dir() -> str:
    return os.path.expanduser(APPS_DIR)


def launchers_dir() -> str:
    return os.path.join(GLib.get_user_data_dir(), "applications")


def icons_dir() -> str:
    return os.path.join(GLib.get_user_data_dir(), "sonata2-data", "appimages")


def slug(name: str) -> str:
    """A launcher id from a name: "Some App 2" -> "some-app-2"."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "app"


def _extract(appimage: str, pattern: str, into: str) -> str:
    """The AppImage's own runtime extracts what matches `pattern` (type 2:
    --appimage-extract <pattern>) into `into`/squashfs-root; that folder."""
    try:
        subprocess.run([appimage, "--appimage-extract", pattern], cwd=into, capture_output=True,
                       timeout=EXTRACT_TIMEOUT_S, env=dict(os.environ, APPIMAGE_EXTRACT_AND_RUN="0"))
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.join(into, "squashfs-root")


def read_entry(root: str) -> dict:
    """The AppImage's own launcher (its top-level .desktop file): Name, Icon,
    Categories, Comment, StartupWMClass ({} if it has none)."""
    for path in sorted(glob.glob(os.path.join(root, "*.desktop"))):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        cp.optionxform = str
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, UnicodeDecodeError):
            continue
        if cp.has_section("Desktop Entry"):
            return dict(cp["Desktop Entry"])
    return {}


def _icon(appimage: str, root: str, work: str, entry: dict, did: str):
    """Its icon copied next to Sonata's data (.DirIcon, or the file its
    launcher's Icon= names); the path, or None."""
    os.makedirs(icons_dir(), exist_ok=True)
    candidates = []
    dir_icon = os.path.join(root, ".DirIcon")
    if os.path.islink(dir_icon):                       # usually a link to the real file: that one too
        target = os.readlink(dir_icon)
        _extract(appimage, target, work)
        candidates.append(os.path.join(root, target))
    candidates.append(dir_icon)
    name = entry.get("Icon", "")
    if name:
        for ext in (".png", ".svg", ".xpm"):
            _extract(appimage, name + ext, work)
            candidates.append(os.path.join(root, name + ext))
    for c in candidates:
        if os.path.isfile(c) and os.path.getsize(c) > 0:
            with open(c, "rb") as f:
                head = f.read(256)
            ext = ".svg" if b"<svg" in head or c.endswith(".svg") else ".png"
            dest = os.path.join(icons_dir(), did + ext)
            shutil.copyfile(c, dest)
            return dest
    return None


def _value(text: str) -> str:
    return " ".join(str(text).split())


def launcher_text(appimage: str, entry: dict, icon, fallback_name: str) -> str:
    name = _value(entry.get("Name") or fallback_name)
    lines = ["[Desktop Entry]", "Type=Application", f"Name={name}",
             f"Exec={_quote(appimage)} %U", f"TryExec={appimage}",
             f"Icon={icon or 'application-x-executable'}"]
    for key in ("Comment", "Categories", "StartupWMClass", "MimeType", "Keywords"):
        if entry.get(key):
            lines.append(f"{key}={_value(entry[key])}")
    if not entry.get("Categories"):
        lines.append("Categories=Utility;")
    lines += ["Terminal=false", f"{KEY}={appimage}"]
    return "\n".join(lines) + "\n"


def _quote(path: str) -> str:
    """A path as an Exec= argument (the desktop entry spec's quoting)."""
    if re.search(r'[\s"\'\\$`]', path):
        return '"' + re.sub(r'(["`$\\])', r"\\\1", path) + '"'
    return path


def _free(path: str) -> str:
    """`path`, or "name 2.ext", "name 3.ext"... if it's taken."""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{base} {n}{ext}"):
        n += 1
    return f"{base} {n}{ext}"


def install(source: str) -> dict:
    """Copy `source` into ~/Applications, executable, and write its launcher.
    {"appimage": its new path, "desktop": the launcher, "name": its name}.
    Raises OSError when the copy or the launcher can't be written."""
    os.makedirs(apps_dir(), exist_ok=True)
    src = os.path.realpath(source)
    inside = os.path.dirname(src) == os.path.realpath(apps_dir())
    dest = src if inside else _free(os.path.join(apps_dir(), os.path.basename(src)))
    if not inside:
        shutil.copy2(src, dest)
    os.chmod(dest, os.stat(dest).st_mode | 0o111)
    work = tempfile.mkdtemp(prefix="sonata2-appimage-")
    try:
        root = _extract(dest, "*.desktop", work)
        entry = read_entry(root)
        _extract(dest, ".DirIcon", work)
        fallback = re.sub(r"(?i)[-_ ]?(x86_64|aarch64|amd64|linux|\d+(\.\d+)+)+", "",
                          os.path.splitext(os.path.basename(dest))[0]) or os.path.basename(dest)
        did = "appimage-" + slug(entry.get("Name") or fallback)
        os.makedirs(launchers_dir(), exist_ok=True)
        desktop = _free(os.path.join(launchers_dir(), did + ".desktop"))
        did = os.path.splitext(os.path.basename(desktop))[0]
        icon = _icon(dest, root, work, entry, did)
        with open(desktop, "w", encoding="utf-8") as f:
            f.write(launcher_text(dest, entry, icon, fallback))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"appimage": dest, "desktop": desktop, "name": (entry.get("Name") or fallback)}


def file_of(desktop_path: str):
    """The AppImage an installed launcher runs (X-Sonata-AppImage), or None."""
    try:
        with open(desktop_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith(KEY + "="):
                    path = line.split("=", 1)[1].strip()
                    return path if os.path.isfile(path) else None
    except OSError:
        pass
    return None
