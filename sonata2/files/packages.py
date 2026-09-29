"""Linux app packages and archives, opened the way people expect:
double-click an installer (.pkg.tar.zst, .deb, .rpm, .flatpak(ref),
.snap) to install it, an AppImage to run it, an archive (.tar.gz, .zip...)
to extract it next to itself (macOS Archive Utility). The same actions
are in the right-click menus (Files, desktop).

Installs run in a terminal window: the package manager asks for the
password and shows what it does (dependencies, conflicts), as on any
Linux system. A package for another distro family isn't forced in; an
alert says what this system installs instead."""
import os
import shlex
import shutil
import tarfile
import threading
import zipfile

from gi.repository import GLib

from .. import ui
from ..backend import system

# suffix -> package format (longest suffixes first)
_FORMATS = [(".pkg.tar.zst", "arch"), (".pkg.tar.xz", "arch"), (".deb", "debian"), (".rpm", "rpm"),
            (".flatpakref", "flatpakref"), (".flatpak", "flatpak"), (".snap", "snap"),
            (".appimage", "appimage")]
_ARCHIVES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tar", ".zip")
FAMILY_NAMES = {"arch": "Arch Linux (.pkg.tar.zst)", "debian": "Debian and Ubuntu (.deb)",
                "rpm": "Fedora, openSUSE and RHEL (.rpm)"}


def kind(path: str):
    """"arch", "debian", "rpm", "flatpak", "flatpakref", "snap", "appimage",
    "archive" or None."""
    low = os.path.basename(path or "").lower()
    for suffix, k in _FORMATS:
        if low.endswith(suffix):
            return k
    if low.endswith(_ARCHIVES):
        return "archive"
    return None


def family() -> str:
    """This system's native package family: arch, debian, rpm (or "")."""
    osr = {}
    try:
        with open("/etc/os-release") as f:
            osr = {k: v.strip().strip('"') for k, v in (ln.split("=", 1) for ln in f if "=" in ln)}
    except OSError:
        pass
    ids = f"{osr.get('ID', '')} {osr.get('ID_LIKE', '')}".lower().split()
    for fam, names in (("arch", {"arch", "archlinux", "cachyos", "manjaro", "endeavouros"}),
                       ("debian", {"debian", "ubuntu"}),
                       ("rpm", {"fedora", "rhel", "centos", "suse", "opensuse", "opensuse-tumbleweed"})):
        if names & set(ids):
            return fam
    for fam, tool in (("arch", "pacman"), ("debian", "apt"), ("rpm", "dnf"), ("rpm", "zypper")):
        if shutil.which(tool):
            return fam
    return ""


def install_command(path: str):
    """Shell command that installs `path` here, or None."""
    k, q = kind(path), shlex.quote(path)
    if k in ("arch", "debian", "rpm") and k != family():
        return None
    if k == "arch":
        return f"sudo pacman -U {q}"
    if k == "debian":
        return f"sudo apt install {q}"
    if k == "rpm":
        return f"sudo dnf install {q}" if shutil.which("dnf") else f"sudo zypper install {q}"
    if k == "flatpakref" and shutil.which("flatpak"):
        return f"flatpak install --user {q}"
    if k == "flatpak" and shutil.which("flatpak"):
        return f"flatpak install --user --bundle {q}"
    if k == "snap" and shutil.which("snap"):
        return f"sudo snap install --dangerous {q}"
    return None


def open_path(path: str, parent=None) -> bool:
    """Double-click: install / run / extract. False = not a package (the
    caller opens it the usual way)."""
    k = kind(path)
    if k is None:
        return False
    if k == "appimage":
        run_appimage(path, parent)
    elif k == "archive":
        extract(path, parent)
    else:
        install(path, parent)
    return True


def install(path: str, parent=None) -> None:
    name = os.path.basename(path)
    cmd = install_command(path)
    if cmd is None:
        k = kind(path)
        if k in FAMILY_NAMES:
            here = FAMILY_NAMES.get(family(), "")
            body = f"This is a package for {FAMILY_NAMES[k]}." + \
                (f" This computer installs packages for {here}." if here else "") + \
                " Look for a version of the app made for this system, a Flatpak or an AppImage."
        else:
            tool = {"flatpak": "Flatpak", "flatpakref": "Flatpak", "snap": "Snap"}.get(k, "its installer")
            body = f"{tool} isn't installed on this computer."
        ui.dialog.alert(f"“{name}” can’t be installed.", body, [("ok", "OK", "default")], parent=parent)
        return
    if not system.run_in_terminal(cmd):
        ui.dialog.alert("No terminal app was found.", f"Install it with:\n{cmd}", [("ok", "OK", "default")],
                        parent=parent)


def run_appimage(path: str, parent=None) -> None:
    try:
        mode = os.stat(path).st_mode
        if not mode & 0o100:
            os.chmod(path, mode | 0o111)               # make it executable, like "chmod +x"
        GLib.spawn_async([path], flags=GLib.SpawnFlags.DEFAULT,
                         working_directory=os.path.dirname(path) or None)
    except (OSError, GLib.Error) as e:
        ui.dialog.alert(f"“{os.path.basename(path)}” can’t be opened.", str(e), [("ok", "OK", "default")],
                        parent=parent)


def _extract_dir(path: str) -> str:
    base = os.path.basename(path)
    low = base.lower()
    for suffix in _ARCHIVES:
        if low.endswith(suffix):
            base = base[:-len(suffix)]
            break
    folder, n = os.path.dirname(path), 1
    target = os.path.join(folder, base)
    while os.path.exists(target):
        n += 1
        target = os.path.join(folder, f"{base} {n}")
    return target


def extract(path: str, parent=None, done=None) -> None:
    """Into a folder named after the archive, next to it (in a thread)."""
    target = _extract_dir(path)

    def work():
        err = None
        try:
            os.makedirs(target)
            if path.lower().endswith(".zip"):
                with zipfile.ZipFile(path) as z:
                    z.extractall(target)
            elif path.lower().endswith(".tar.zst") and shutil.which("tar"):
                import subprocess
                r = subprocess.run(["tar", "--zstd", "-xf", path, "-C", target], capture_output=True, text=True)
                if r.returncode:
                    raise OSError(r.stderr.strip() or "tar failed")
            else:
                with tarfile.open(path) as t:
                    t.extractall(target, filter="data")      # no absolute paths / links out of the folder
        except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as e:
            err = str(e)
        GLib.idle_add(finish, err)

    def finish(err):
        if err:
            try:
                os.rmdir(target)
            except OSError:
                pass
            ui.dialog.alert(f"Unable to expand “{os.path.basename(path)}”.", err, [("ok", "OK", "default")],
                            parent=parent)
        elif done:
            done(target)
        return False
    threading.Thread(target=work, daemon=True).start()


def menu_items(path: str, parent=None) -> list:
    """Right-click entries for a package/archive ([] for other files)."""
    Item = ui.menu.Item
    k = kind(path)
    if k is None:
        return []
    if k == "appimage":
        return [Item("Run", lambda: run_appimage(path, parent))]
    if k == "archive":
        return [Item("Extract Here", lambda: extract(path, parent))]
    return [Item("Install", lambda: install(path, parent))]    # explains when it can't
