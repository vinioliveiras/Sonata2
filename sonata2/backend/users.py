"""Users & Groups through AccountsService (org.freedesktop.Accounts on the
system bus) -- what GNOME and KDE use: list, create, delete accounts, set
the picture, the full name, the account type and the password. Changes to
other accounts (and creating/deleting) are authorized by polkit (an
admin password prompt). Blocking: call through system.run_async."""
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import List, Optional

from gi.repository import Gio, GLib

BUS = "org.freedesktop.Accounts"
PATH = "/org/freedesktop/Accounts"
IFACE = "org.freedesktop.Accounts"
USER_IFACE = "org.freedesktop.Accounts.User"
INTERACTIVE = Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION


@dataclass
class User:
    path: str
    uid: int
    name: str
    real_name: str
    icon: str
    admin: bool
    current: bool


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SYSTEM, None)


def available() -> bool:
    try:
        _bus().call_sync(BUS, PATH, "org.freedesktop.DBus.Peer", "Ping", None, None,
                         Gio.DBusCallFlags.NONE, 2000, None)
        return True
    except GLib.Error:
        return False


def _props(path: str) -> dict:
    v = _bus().call_sync(BUS, path, "org.freedesktop.DBus.Properties", "GetAll",
                         GLib.Variant("(s)", (USER_IFACE,)), None, Gio.DBusCallFlags.NONE, 3000, None)
    return v.unpack()[0]


def users() -> List[User]:
    try:
        paths = _bus().call_sync(BUS, PATH, IFACE, "ListCachedUsers", None, None, Gio.DBusCallFlags.NONE,
                                 3000, None).unpack()[0]
        me = _bus().call_sync(BUS, PATH, IFACE, "FindUserById", GLib.Variant("(x)", (os.getuid(),)), None,
                              Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    except GLib.Error:
        return []
    if me not in paths:
        paths = [me] + list(paths)
    out = []
    for p in paths:
        try:
            pr = _props(p)
        except GLib.Error:
            continue
        if pr.get("SystemAccount"):
            continue
        icon = pr.get("IconFile") or ""
        out.append(User(p, int(pr.get("Uid", -1)), pr.get("UserName", ""), pr.get("RealName", ""),
                        icon if icon and os.path.isfile(icon) else "", pr.get("AccountType", 0) == 1,
                        p == me))
    out.sort(key=lambda u: (not u.current, u.real_name.lower() or u.name))
    return out


def _call_user(path: str, method: str, sig: str, *args) -> Optional[str]:
    """None on success, else the error message."""
    try:
        _bus().call_sync(BUS, path, USER_IFACE, method, GLib.Variant(sig, args), None, INTERACTIVE, 120000, None)
        return None
    except GLib.Error as e:
        return e.message


AVATARS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "avatars")
PICTURE_PX = 256


def stock_pictures() -> List[str]:
    """Sonata's own user pictures (tools/gen-avatars.py)."""
    try:
        return sorted(os.path.join(AVATARS, n) for n in os.listdir(AVATARS) if n.endswith(".png"))
    except OSError:
        return []


def _square_png(file_path: str) -> str:
    """The picture cropped to a centred square, 256 px, as a PNG in /tmp:
    accounts-daemon (root) refuses big files and can't read some home
    folders, so it always gets a small copy it can read."""
    import tempfile
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file(file_path)
    pb = pb.apply_embedded_orientation() or pb
    side = min(pb.get_width(), pb.get_height())
    pb = pb.new_subpixbuf((pb.get_width() - side) // 2, (pb.get_height() - side) // 2, side, side)
    pb = pb.scale_simple(PICTURE_PX, PICTURE_PX, GdkPixbuf.InterpType.HYPER)
    fd, out = tempfile.mkstemp(prefix="sonata-picture-", suffix=".png", dir="/tmp")
    os.close(fd)
    pb.savev(out, "png", [], [])
    os.chmod(out, 0o644)
    return out


def set_picture(user: User, file_path: str) -> Optional[str]:
    try:
        small = _square_png(file_path)
    except GLib.Error as e:
        return f"This picture can't be used: {e.message}"
    try:
        err = _call_user(user.path, "SetIconFile", "(s)", small)
        if err is None and user.current:          # ~/.face too (lock screen, other apps)
            try:
                shutil.copyfile(small, os.path.expanduser("~/.face"))
            except OSError:
                pass
        return err
    finally:
        try:
            os.remove(small)
        except OSError:
            pass


def set_real_name(user: User, name: str) -> Optional[str]:
    return _call_user(user.path, "SetRealName", "(s)", name)


def set_admin(user: User, admin: bool) -> Optional[str]:
    return _call_user(user.path, "SetAccountType", "(i)", 1 if admin else 0)


def _crypt(password: str) -> Optional[str]:
    """SHA-512 crypt (what /etc/shadow uses), via openssl (no Python crypt
    module since 3.13)."""
    if not shutil.which("openssl"):
        return None
    try:
        r = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password, capture_output=True, text=True,
                           timeout=10)               # a stuck openssl must not hang the caller
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def set_password(user: User, password: str, hint: str = "") -> Optional[str]:
    hashed = _crypt(password)
    if not hashed:
        return "openssl is needed to set passwords"
    return _call_user(user.path, "SetPassword", "(ss)", hashed, hint)


def create_user(name: str, real_name: str, admin: bool, password: str) -> Optional[str]:
    try:
        path = _bus().call_sync(BUS, PATH, IFACE, "CreateUser", GLib.Variant("(ssi)", (name, real_name, 1 if admin else 0)),
                                None, INTERACTIVE, 120000, None).unpack()[0]
    except GLib.Error as e:
        return e.message
    if password:
        u = User(path, -1, name, real_name, "", admin, False)
        return set_password(u, password)
    return None


def delete_user(user: User, remove_files: bool) -> Optional[str]:
    try:
        _bus().call_sync(BUS, PATH, IFACE, "DeleteUser", GLib.Variant("(xb)", (user.uid, remove_files)), None,
                         INTERACTIVE, 120000, None)
        return None
    except GLib.Error as e:
        return e.message


def valid_name(name: str) -> bool:
    import re
    return bool(re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", name))


def short_name(real_name: str) -> str:
    """macOS derives the account name from the full name ("Ana Souza" -> "anasouza")."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", real_name).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return (s if s and not s[0].isdigit() else "user" + s)[:32]
