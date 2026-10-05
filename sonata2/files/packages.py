"""Linux app packages and archives, opened the way people expect:
double-click an installer (.pkg.tar.zst, .deb, .rpm, .flatpak(ref),
.snap) to install it, an AppImage to run it, an archive (.tar.gz, .zip, .rar, .7z...)
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
_ARCHIVES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tar", ".zip", ".rar", ".7z")
# RAR and 7-Zip (Vini): through the first tool there is -- 7-Zip, unrar, or
# libarchive's bsdtar (installed with pacman itself on Arch / CachyOS: RAR 4
# and 5 work with nothing more installed)
_TOOLS = {".rar": ("7z", "7zz", "unrar", "bsdtar"), ".7z": ("7z", "7zz", "bsdtar")}
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


def _debtap_command(path: str) -> str:
    """Convert a .deb into an Arch package with debtap, then install it."""
    d, q = shlex.quote(os.path.dirname(path) or "."), shlex.quote(path)
    return (f"cd {d} && debtap -Q {q} && "
            f"sudo pacman -U \"$(ls -t -- *.pkg.tar.zst | head -n 1)\"")


def aur_helper():
    return next((h for h in ("paru", "yay") if shutil.which(h)), None)


def install_command(path: str):
    """Shell command that installs `path` here, or None."""
    k, q = kind(path), shlex.quote(path)
    if k == "debian" and family() == "arch" and shutil.which("debtap"):
        return _debtap_command(path)                  # .deb on Arch: converted first
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
        helper = aur_helper()
        if k == "debian" and family() == "arch" and helper:
            # debtap (AUR) converts .deb packages for Arch; offer to get it
            def answer(r):
                if r == "debtap":
                    system.run_in_terminal(f"{helper} -S debtap && sudo debtap -u && {_debtap_command(path)}")
            ui.dialog.alert(f"“{name}” is a package for Debian and Ubuntu.",
                            "It can be converted into an Arch package with debtap, which isn't installed yet. "
                            "Converted packages usually work, but a version made for this system, a Flatpak or "
                            "an AppImage is safer.",
                            [("cancel", "Cancel", ""), ("debtap", "Install debtap and Convert", "default")],
                            on_response=answer, parent=parent)
            return
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


def _appimage_stamp(path: str):
    """What makes an AppImage "the same file" for the first-run question:
    its real path, size and mtime (a replaced file is asked about again)."""
    st = os.stat(path)
    return os.path.realpath(path), f"{st.st_size}:{st.st_mtime_ns}"


def _appimage_trusted(path: str) -> bool:
    from .. import config
    try:
        key, stamp = _appimage_stamp(path)
    except OSError:
        return False
    return config.load("files", {"trusted_appimages": {}})["trusted_appimages"].get(key) == stamp


def _trust_appimage(path: str) -> None:
    from .. import config
    try:
        key, stamp = _appimage_stamp(path)
    except OSError:
        return
    trusted = dict(config.load("files", {"trusted_appimages": {}})["trusted_appimages"] or {})
    trusted[key] = stamp
    config.update("files", trusted_appimages=trusted)


def run_appimage(path: str, parent=None) -> None:
    """Run an AppImage; the first time (per file) only after the user
    confirms (Gatekeeper's "downloaded from the Internet" question)."""
    if _appimage_trusted(path):
        _launch_appimage(path, parent)
        return

    def answer(rid):
        if rid == "open":
            _trust_appimage(path)
            _launch_appimage(path, parent)
    ui.dialog.alert(f"Are you sure you want to open “{os.path.basename(path)}”?",
                    "This app will run with your permissions and can access your files. "
                    "Only open apps from developers you trust.",
                    [("cancel", "Cancel", ""), ("open", "Open", "default")], answer, parent=parent)


def _launch_appimage(path: str, parent=None) -> None:
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


class PasswordNeeded(OSError):
    """The archive is protected: ask for its password (wrong: a wrong one was given)."""

    def __init__(self, wrong=False):
        super().__init__("The archive is protected by a password.")
        self.wrong = wrong


_PASSWORD_WORDS = ("password", "passphrase", "encrypted")


def tool_command(path: str, target: str, password: str = None):
    """argv that extracts a .rar / .7z into `target` (None: no tool for it).
    With no password, a tool never stops to ask for one: it fails."""
    ext = os.path.splitext(path.lower())[1]
    for tool in _TOOLS.get(ext, ()):
        exe = shutil.which(tool)
        if not exe:
            continue
        if tool in ("7z", "7zz"):
            return [exe, "x", "-y", "-bso0", "-bsp0", "-p" + (password or ""), "-o" + target, "--", path]
        if tool == "unrar":
            return [exe, "x", "-o+", "-y", "-p" + (password or "-"), "--", path, target + os.sep]
        pw = ["--passphrase", password] if password else []
        return [exe, "-xf", path, *pw, "-C", target]          # bsdtar: never writes outside target
    return None


def _run_tool(path: str, target: str, password: str = None) -> None:
    import subprocess
    argv = tool_command(path, target, password)
    if argv is None:
        raise OSError("Expanding this kind of archive needs 7-Zip (the 7zip package) or bsdtar (libarchive).")
    # no stdin: nothing waits for a password typed in a terminal
    r = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode:
        msg = (r.stderr or r.stdout).strip()
        if any(w in msg.lower() for w in _PASSWORD_WORDS):
            raise PasswordNeeded(wrong=bool(password))
        raise OSError(msg.splitlines()[-1] if msg else f"{os.path.basename(argv[0])} failed")


def ask_password(path: str, parent, wrong: bool, on_done) -> None:
    """Finder's Archive Utility: the archive's password, typed in a secure
    field; on_done(password) unless cancelled."""
    from gi.repository import GLib as _GLib
    field = ui.controls.text_field(placeholder="Password", secret=True)
    field.set_property("activates-default", True)
    name = os.path.basename(path)
    heading = "The password is incorrect." if wrong else f"“{name}” is protected by a password."
    body = f"Enter the password for “{name}”." if wrong else "Enter its password to expand it."

    def answered(rid):
        if rid == "ok" and field.get_text():
            on_done(field.get_text())
    dlg = ui.dialog.alert(heading, body, [("cancel", "Cancel", ""), ("ok", "Expand", "default")], answered,
                          parent=parent)
    dlg.set_extra_child(field)
    _GLib.idle_add(lambda: (field.grab_focus(), False)[1])
    return dlg


def extract(path: str, parent=None, done=None, password: str = None) -> None:
    """Into a folder named after the archive, next to it (in a thread). A
    protected archive asks for its password (and again when it's wrong)."""
    target = _extract_dir(path)

    def work():
        err = None
        try:
            os.makedirs(target)
            if path.lower().endswith((".rar", ".7z")):
                _run_tool(path, target, password)
            elif path.lower().endswith(".zip"):
                with zipfile.ZipFile(path) as z:
                    try:
                        z.extractall(target, pwd=password.encode() if password else None)
                    except RuntimeError as e:                    # ZipCrypto: "password required" / "Bad password"
                        if "password" in str(e).lower():
                            raise PasswordNeeded(wrong=bool(password))
                        raise OSError(str(e))
            elif path.lower().endswith(".tar.zst") and shutil.which("tar"):
                import subprocess
                r = subprocess.run(["tar", "--zstd", "-xf", path, "-C", target], capture_output=True, text=True)
                if r.returncode:
                    raise OSError(r.stderr.strip() or "tar failed")
            else:
                with tarfile.open(path) as t:
                    t.extractall(target, filter="data")      # no absolute paths / links out of the folder
        except PasswordNeeded as e:
            err = e
            shutil.rmtree(target, ignore_errors=True)
        except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError, NotImplementedError) as e:
            err = str(e)
            shutil.rmtree(target, ignore_errors=True)      # no half-extracted folder left behind
        GLib.idle_add(finish, err)

    def finish(err):
        if isinstance(err, PasswordNeeded):
            ask_password(path, parent, err.wrong, lambda pw: extract(path, parent, done, pw))
        elif err:
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
