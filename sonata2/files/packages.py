"""Linux app packages and archives, opened the way people expect:
double-click an installer (.pkg.tar.zst, .deb, .rpm, .flatpak(ref),
.snap) to install it, an AppImage to run it, an archive (.tar.gz, .zip, .rar, .7z...)
to extract it next to itself (macOS Archive Utility). The same actions
are in the right-click menus (Files, desktop), and a shell script's Run
(right-click only: a double-click still opens it to read).

Installs run in a terminal window that shows what the package manager does
(dependencies, conflicts); the password is asked first by Sonata's own
prompt (pkexec), not inside the terminal. A package for another distro family isn't forced in; an
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
from ..i18n import N_, _

# suffix -> package format (longest suffixes first)
_FORMATS = [(".pkg.tar.zst", "arch"), (".pkg.tar.xz", "arch"), (".deb", "debian"), (".rpm", "rpm"),
            (".flatpakref", "flatpakref"), (".flatpak", "flatpak"), (".snap", "snap"),
            (".appimage", "appimage")]
_ARCHIVES = (".tar.gz", ".tgz", ".tar.xz", ".txz", ".tar.bz2", ".tbz2", ".tar.zst", ".tar", ".zip", ".rar", ".7z")
# RAR and 7-Zip (Vini): through the first tool there is -- 7-Zip, unrar, or
# libarchive's bsdtar (installed with pacman itself on Arch / CachyOS: RAR 4
# and 5 work with nothing more installed)
_TOOLS = {".rar": ("7z", "7zz", "unrar", "bsdtar"), ".7z": ("7z", "7zz", "bsdtar")}
FAMILY_NAMES = {"arch": N_("Arch Linux (.pkg.tar.zst)"), "debian": N_("Debian and Ubuntu (.deb)"),
                "rpm": N_("Fedora, openSUSE and RHEL (.rpm)")}


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


family = system.package_family     # (here too: this module's callers and tests)


def _root() -> str:
    """How an install becomes root: pkexec, so Sonata's own password prompt
    asks first (Vini: sudo asked inside the terminal and the install could
    fail there); sudo where there's no pkexec."""
    return "pkexec" if shutil.which("pkexec") else "sudo"


def _debtap_command(path: str) -> str:
    """Convert a .deb into an Arch package with debtap, then install it."""
    d, q = shlex.quote(os.path.dirname(path) or "."), shlex.quote(path)
    return (f"cd {d} && debtap -Q {q} && "
            f"{_root()} pacman -U \"$(ls -t -- *.pkg.tar.zst | head -n 1)\"")


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
        return f"{_root()} pacman -U {q}"
    if k == "debian":
        return f"{_root()} apt install {q}"
    if k == "rpm":
        return f"{_root()} dnf install {q}" if shutil.which("dnf") else f"{_root()} zypper install {q}"
    if k == "flatpakref" and shutil.which("flatpak"):
        return f"flatpak install --user {q}"
    if k == "flatpak" and shutil.which("flatpak"):
        return f"flatpak install --user --bundle {q}"
    if k == "snap" and shutil.which("snap"):
        return f"{_root()} snap install --dangerous {q}"
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
                    system.run_in_terminal(f"{helper} -S debtap && {_root()} debtap -u && {_debtap_command(path)}")
            ui.dialog.alert(_("“{name}” is a package for Debian and Ubuntu.").format(name=name),
                            _("It can be converted into an Arch package with debtap, which isn't installed yet. "
                              "Converted packages usually work, but a version made for this system, a Flatpak or "
                              "an AppImage is safer."),
                            [("cancel", _("Cancel"), ""), ("debtap", _("Install debtap and Convert"), "default")],
                            on_response=answer, parent=parent)
            return
        if k in FAMILY_NAMES:
            here = FAMILY_NAMES.get(family(), "")
            body = _("This is a package for {family}.").format(family=_(FAMILY_NAMES[k])) + \
                (" " + _("This computer installs packages for {family}.").format(family=_(here)) if here else "") + \
                " " + _("Look for a version of the app made for this system, a Flatpak or an AppImage.")
        else:
            tool = {"flatpak": "Flatpak", "flatpakref": "Flatpak", "snap": "Snap"}.get(k)
            body = _("{tool} isn't installed on this computer.").format(tool=tool) if tool else \
                _("Its installer isn't installed on this computer.")
        ui.dialog.alert(_("“{name}” can’t be installed.").format(name=name), body, [("ok", "OK", "default")], parent=parent)
        return
    if not system.run_in_terminal(cmd):
        ui.dialog.alert(_("No terminal app was found."), _("Install it with:") + "\n" + cmd, [("ok", "OK", "default")],
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
    ui.dialog.alert(_("Are you sure you want to open “{name}”?").format(name=os.path.basename(path)),
                    _("This app will run with your permissions and can access your files. "
                      "Only open apps from developers you trust."),
                    [("cancel", _("Cancel"), ""), ("open", _("Open"), "default")], answer, parent=parent)


def _launch_appimage(path: str, parent=None) -> None:
    try:
        mode = os.stat(path).st_mode
        if not mode & 0o100:
            os.chmod(path, mode | 0o111)               # make it executable, like "chmod +x"
        GLib.spawn_async([path], flags=GLib.SpawnFlags.DEFAULT,
                         working_directory=os.path.dirname(path) or None)
    except (OSError, GLib.Error) as e:
        ui.dialog.alert(_("“{name}” can’t be opened.").format(name=os.path.basename(path)), str(e), [("ok", "OK", "default")],
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
        raise OSError(_("Expanding this kind of archive needs 7-Zip (the 7zip package) or bsdtar (libarchive)."))
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
    field = ui.controls.text_field(placeholder=_("Password"), secret=True)
    field.set_property("activates-default", True)
    name = os.path.basename(path)
    heading = _("The password is incorrect.") if wrong else _("“{name}” is protected by a password.").format(name=name)
    body = _("Enter the password for “{name}”.").format(name=name) if wrong else _("Enter its password to expand it.")

    def answered(rid):
        if rid == "ok" and field.get_text():
            on_done(field.get_text())
    dlg = ui.dialog.alert(heading, body, [("cancel", _("Cancel"), ""), ("ok", _("Expand"), "default")], answered,
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
            ui.dialog.alert(_("Unable to expand “{name}”.").format(name=os.path.basename(path)), err, [("ok", "OK", "default")],
                            parent=parent)
        elif done:
            done(target)
        return False
    threading.Thread(target=work, daemon=True).start()


# -- shell scripts: Run (Vini) ---------------------------------------------------------------
_SCRIPTS = {".sh": "bash", ".bash": "bash", ".zsh": "zsh"}


def is_script(path: str) -> bool:
    """A script to run: .sh / .bash / .zsh, or an executable file starting with "#!"."""
    if not path or not os.path.isfile(path):
        return False
    if os.path.splitext(path)[1].lower() in _SCRIPTS:
        return True
    return os.access(path, os.X_OK) and _shebang(path) is not None


def _shebang(path: str):
    """The "#!" line's interpreter and its arguments, or None."""
    try:
        with open(path, "rb") as f:
            line = f.readline(256)
    except OSError:
        return None
    if not line.startswith(b"#!"):
        return None
    try:
        return shlex.split(line[2:].decode("utf-8", "replace").strip()) or None
    except ValueError:
        return None


def script_argv(path: str) -> list:
    """How to run it: itself when executable, else through its "#!" line's
    interpreter (or the shell its suffix names) -- no chmod +x needed."""
    if os.access(path, os.X_OK):
        return [path]
    return (_shebang(path) or [_SCRIPTS.get(os.path.splitext(path)[1].lower(), "sh")]) + [path]


def run_script(path: str, parent=None, terminal: bool = True) -> None:
    """Run a script in its own folder: in a terminal window that stays open
    (its output, and whatever it asks), or in the background."""
    argv = script_argv(path)
    folder = os.path.dirname(path) or None
    if terminal:
        cmd = f"cd {shlex.quote(folder or '.')} && {shlex.join(argv)}"
        if system.run_in_terminal(cmd):
            return
        ui.dialog.alert(_("No terminal app was found."), _("Run it with:") + "\n" + cmd, [("ok", "OK", "default")],
                        parent=parent)
        return
    try:
        GLib.spawn_async(argv, working_directory=folder, flags=GLib.SpawnFlags.SEARCH_PATH)
    except GLib.Error as e:
        ui.dialog.alert(_("“{name}” can’t be run.").format(name=os.path.basename(path)), e.message, [("ok", "OK", "default")],
                        parent=parent)


def menu_items(path: str, parent=None) -> list:
    """Right-click entries for a package/archive/script ([] for other files)."""
    Item = ui.menu.Item
    k = kind(path)
    if k is None:
        if is_script(path):
            return [Item(_("Run in Terminal"), lambda: run_script(path, parent)),
                    Item(_("Run"), lambda: run_script(path, parent, terminal=False))]
        return []
    if k == "appimage":
        return [Item(_("Run"), lambda: run_appimage(path, parent))]
    if k == "archive":
        return [Item(_("Extract Here"), lambda: extract(path, parent))]
    return [Item(_("Install"), lambda: install(path, parent))]    # explains when it can't
