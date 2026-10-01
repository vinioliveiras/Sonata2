"""Sonata's keyring: where apps keep saved passwords (Chrome, VS Code,
NetworkManager's Wi-Fi secrets...) through the freedesktop Secret Service.

"gnome" (the default): GNOME's keyring, unlocked by the login itself --
the login screen's PAM hands it your password (pam_gnome_keyring in
/etc/pam.d/greetd), so nothing asks for a password after logging in.
"keepassxc": KeePassXC's Secret Service (Sonata's keyring before): its
database has a password of its own, asked at every login.

Settings > Security & Privacy > "Use Login Password…" moves from KeePassXC
to GNOME's keyring once: the apps' secrets are copied over (Chrome's key
for its saved passwords and cookies too), the login screen learns to
unlock the keyring, and KeePassXC stays installed as a password manager.

    sonata2 keyring backend     # "gnome" | "keepassxc" (tools/sonata-session)
    sonata2 keyring pam         # as root (pkexec): the login screen unlocks the keyring"""
import configparser
import os
import shutil
import subprocess
import time

from gi.repository import GLib

from . import config

CONFIG = "~/.config/keepassxc/keepassxc.ini"
PAM_FILE = "/etc/pam.d/greetd"
# the lines pam_gnome_keyring needs: unlock with the login password,
# start the daemon, and follow password changes
PAM_LINES = (("auth", "auth       optional     pam_gnome_keyring.so"),
             ("session", "session    optional     pam_gnome_keyring.so auto_start"),
             ("password", "password   optional     pam_gnome_keyring.so"))
DEFAULTS = {"backend": ""}
SECRETS = "org.freedesktop.secrets"


# -- which keyring --------------------------------------------------------------------------
def _ini(path: str = None) -> configparser.RawConfigParser:
    cfg = configparser.RawConfigParser(strict=False)
    cfg.optionxform = str                        # keep KeePassXC's key case
    try:
        cfg.read(os.path.expanduser(path or CONFIG), encoding="utf-8")
    except configparser.Error:
        pass
    return cfg


def _keepassxc_in_use(path: str = None) -> bool:
    """KeePassXC was the Secret Service with a database in use (Sonata's
    keyring before): its secrets live there until moved."""
    cfg = _ini(path)
    on = cfg.get("FdoSecrets", "Enabled", fallback="").lower() == "true"
    dbs = cfg.get("General", "LastOpenedDatabases", fallback="") or cfg.get("General", "LastActiveDatabase",
                                                                              fallback="")
    return on and bool(dbs.strip())


def backend() -> str:
    """"gnome" or "keepassxc" (your choice, else what is already in use)."""
    chosen = config.load("keyring", DEFAULTS)["backend"]
    if chosen in ("gnome", "keepassxc"):
        return chosen
    if shutil.which("keepassxc") and _keepassxc_in_use():
        return "keepassxc"
    return "gnome" if shutil.which("gnome-keyring-daemon") or not shutil.which("keepassxc") else "keepassxc"


def set_backend(name: str) -> None:
    config.update("keyring", backend=name)


# -- KeePassXC ---------------------------------------------------------------------------------
def enable_secret_service(path: str, on: bool = True, force: bool = False) -> bool:
    """[FdoSecrets] Enabled=on unless the user chose otherwise (force: set it
    anyway). True: written."""
    path = os.path.expanduser(path)
    cfg = _ini(path)
    if cfg.has_option("FdoSecrets", "Enabled") and not force:
        return False                             # your choice stands
    if not cfg.has_section("FdoSecrets"):
        cfg.add_section("FdoSecrets")
    cfg.set("FdoSecrets", "Enabled", "true" if on else "false")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f, space_around_delimiters=False)
    return True


def _running() -> bool:
    return subprocess.run(["pgrep", "-x", "keepassxc"], stdout=subprocess.DEVNULL).returncode == 0


def start() -> bool:
    """At login: KeePassXC with its Secret Service, minimized -- only while
    it is the keyring (GNOME's is unlocked by the login itself)."""
    exe = shutil.which("keepassxc")
    if not exe or backend() != "keepassxc":
        return False
    if not _running():                  # (a running KeePassXC writes its settings back on quit)
        enable_secret_service(CONFIG)
        GLib.spawn_async([exe, "--minimized"], flags=GLib.SpawnFlags.DEFAULT)
    return True


def _quit_keepassxc(timeout: float = 8.0) -> None:
    subprocess.run(["pkill", "-x", "keepassxc"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    end = time.monotonic() + timeout
    while _running() and time.monotonic() < end:
        time.sleep(0.2)


# -- the login screen (PAM) ----------------------------------------------------------------------
def pam_text(text: str) -> str:
    """greetd's PAM file with pam_gnome_keyring after each kind's last line
    (unchanged when already there)."""
    lines = text.splitlines()
    for kind, line in PAM_LINES:
        if any(ln.split()[:1] == [kind] and "pam_gnome_keyring" in ln for ln in lines):
            continue
        last = max((i for i, ln in enumerate(lines) if ln.split()[:1] == [kind]), default=len(lines) - 1)
        lines.insert(last + 1, line)
    return "\n".join(lines) + "\n"


def pam_ready(path: str = PAM_FILE) -> bool:
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return False
    return pam_text(text) == (text if text.endswith("\n") else text + "\n")


def write_pam(path: str = PAM_FILE) -> int:
    """As root (`pkexec sonata2 keyring pam`): add the lines, keeping a copy."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = pam_text(text)
    if new == text:
        return 0
    if not os.path.exists(path + ".sonata-bak"):
        shutil.copy2(path, path + ".sonata-bak")
    tmp = path + ".sonata-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return 0


# -- moving the secrets ---------------------------------------------------------------------------
def _secret():
    import gi
    gi.require_version("Secret", "1")
    from gi.repository import Secret
    return Secret


def read_secrets() -> list:
    """Every item of the Secret Service now running (KeePassXC asks for its
    database password to unlock): [(label, attributes, bytes, content type)]."""
    Secret = _secret()
    svc = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION | Secret.ServiceFlags.LOAD_COLLECTIONS, None)
    out = []
    for col in svc.get_collections() or []:
        if col.get_locked():
            svc.unlock_sync([col], None)
        col.load_items_sync(None)
        for item in col.get_items() or []:
            item.load_secret_sync(None)
            value = item.get_secret()
            if value is None:
                continue
            out.append((item.get_label(), dict(item.get_attributes()), bytes(value.get()),
                        value.get_content_type() or "text/plain"))
    Secret.Service.disconnect()                  # the next one talks to the new keyring
    return out


def write_secrets(items: list) -> int:
    """Into GNOME's login keyring (the default collection). Returns how many."""
    Secret = _secret()
    svc = Secret.Service.get_sync(Secret.ServiceFlags.OPEN_SESSION | Secret.ServiceFlags.LOAD_COLLECTIONS, None)
    col = Secret.Collection.for_alias_sync(svc, "default", Secret.CollectionFlags.NONE, None)
    if col is None:
        col = Secret.Collection.new_for_dbus_path_sync(svc, "/org/freedesktop/secrets/collection/login",
                                                       Secret.CollectionFlags.NONE, None)
        svc.set_alias_sync("default", col, None)
    if col.get_locked():
        svc.unlock_sync([col], None)
    for label, attrs, data, ctype in items:
        Secret.Item.create_sync(col, None, attrs, label, Secret.Value.new(data, len(data), ctype),
                                Secret.ItemCreateFlags.REPLACE, None)
    return len(items)


def _bus_owner_ready(timeout: float = 8.0) -> bool:
    from gi.repository import Gio
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                             "NameHasOwner", GLib.Variant("(s)", (SECRETS,)), None,
                             Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]:
                return True
        except GLib.Error:
            pass
        time.sleep(0.2)
    return False


def start_gnome_keyring(password: str) -> bool:
    """GNOME's keyring as the Secret Service, its login keyring unlocked (or
    made) with the login password -- what the login screen does from now on."""
    exe = shutil.which("gnome-keyring-daemon")
    if not exe:
        return False
    p = subprocess.run([exe, "--replace", "--unlock", "--daemonize", "--components=secrets,pkcs11"],
                       input=password.encode(), capture_output=True, timeout=20)
    for line in p.stdout.decode(errors="replace").splitlines():      # SSH_AUTH_SOCK=... for this session
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v
    return p.returncode == 0 and _bus_owner_ready()


def switch_to_gnome(password: str, step=lambda _t: None, ops=None) -> int:
    """KeePassXC -> GNOME's keyring, once. The login password must be
    right (it becomes the keyring's). Nothing is changed until the secrets
    are read; if a later step fails, KeePassXC is put back. Returns the
    number of secrets moved. `ops` replaces the system calls (tests)."""
    o = dict(auth=_auth, pam_ready=pam_ready, write_pam=_pkexec_pam, read=read_secrets,
             quit_kp=_quit_keepassxc, kp_service=lambda on: enable_secret_service(CONFIG, on, force=True),
             start_gnome=start_gnome_keyring, write=write_secrets, start_kp=_start_keepassxc,
             set_backend=set_backend)
    o.update(ops or {})
    step("Checking your password…")
    if not o["auth"](password):
        raise PermissionError("That's not your login password.")
    if not o["pam_ready"]():
        step("Letting the login screen unlock the keyring…")
        if not o["write_pam"]():
            raise RuntimeError("The login screen's settings couldn't be changed.")
    step("Reading the saved passwords from KeePassXC…")
    items = o["read"]()
    step("Moving them to the login keyring…")
    o["quit_kp"]()
    o["kp_service"](False)
    try:
        if not o["start_gnome"](password):
            raise RuntimeError("GNOME's keyring didn't start.")
        n = o["write"](items)
    except Exception:
        o["kp_service"](True)                    # back as it was
        o["start_kp"]()
        raise
    o["set_backend"]("gnome")
    return n


def _auth(password: str) -> bool:
    from . import pam
    return pam.authenticate(GLib.get_user_name(), password)


def _pkexec_pam() -> bool:
    from .__main__ import self_command
    try:
        return subprocess.run(["pkexec"] + self_command().split() + ["keyring", "pam"],
                              timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _start_keepassxc() -> None:
    exe = shutil.which("keepassxc")
    if exe:
        subprocess.Popen([exe, "--minimized"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(args: list) -> int:
    """`sonata2 keyring backend|pam`."""
    if args[:1] == ["backend"]:
        print(backend())
        return 0
    if args[:1] == ["pam"]:
        if os.geteuid() != 0:
            print("sonata2 keyring pam: run it as root (pkexec)")
            return 1
        return write_pam(args[1] if len(args) > 1 else PAM_FILE)
    print("usage: sonata2 keyring backend|pam")
    return 2
