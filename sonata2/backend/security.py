"""Security & Privacy's Linux parts (macOS: Firewall, FileVault).

Firewall: ufw or firewalld, whichever is installed; its state read without
root (ufw: /etc/ufw/ufw.conf and its service; firewalld: its service),
turned on or off through pkexec (Sonata's polkit agent asks the password).
Disk encryption: whether the startup disk (and the home folder) sit on a
LUKS volume -- shown only: Linux encrypts a disk when it's installed."""
import json
import shutil
import subprocess

UFW_CONF = "/etc/ufw/ufw.conf"


def _active(unit: str) -> bool:
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", unit], timeout=3).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _ufw_enabled(path: str = UFW_CONF) -> bool:
    try:
        with open(path) as f:
            for line in f:
                if line.strip().upper().startswith("ENABLED="):
                    return line.split("=", 1)[1].strip().strip('"').lower() == "yes"
    except OSError:
        pass
    return False


def firewall() -> dict:
    """{"kind": "ufw" | "firewalld" | None, "on": bool}."""
    if shutil.which("ufw"):
        return {"kind": "ufw", "on": _ufw_enabled() and _active("ufw")}
    if shutil.which("firewall-cmd") or shutil.which("firewalld"):
        return {"kind": "firewalld", "on": _active("firewalld")}
    return {"kind": None, "on": False}


def firewall_command(kind: str, on: bool) -> list:
    """The root command that turns it on or off (and keeps it so after a restart)."""
    if kind == "ufw":
        # ufw enable also enables its service; ufw needs it running to load the rules at boot
        return ["pkexec", "sh", "-c", "systemctl enable --now ufw && ufw --force enable"] if on else \
            ["pkexec", "ufw", "disable"]
    if kind == "firewalld":
        return ["pkexec", "systemctl", "enable" if on else "disable", "--now", "firewalld"]
    return []


def set_firewall(kind: str, on: bool) -> bool:
    cmd = firewall_command(kind, on)
    if not cmd:
        return False
    try:
        return subprocess.run(cmd, timeout=120).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _encrypted_under(dev, mount: str) -> bool:
    """Whether `mount` is on this device and there is a crypt layer at or above it."""
    def walk(node, crypt):
        crypt = crypt or node.get("type") == "crypt" or node.get("fstype") == "crypto_LUKS"
        if mount in (node.get("mountpoints") or []):
            return crypt
        return any(walk(c, crypt) for c in node.get("children") or [])
    return walk(dev, False)


def _mounted(dev, mount: str) -> bool:
    if mount in (dev.get("mountpoints") or []):
        return True
    return any(_mounted(c, mount) for c in dev.get("children") or [])


def encryption(lsblk=None) -> dict:
    """{"/": True | False | None, "/home": ...} (None: not a mount of its own / unknown)."""
    if lsblk is None:
        try:
            r = subprocess.run(["lsblk", "-J", "-o", "NAME,TYPE,FSTYPE,MOUNTPOINTS"], capture_output=True,
                               text=True, timeout=5)
            lsblk = json.loads(r.stdout or "{}")
        except (OSError, subprocess.SubprocessError, ValueError):
            lsblk = {}
    out = {}
    for mount in ("/", "/home"):
        devs = [d for d in lsblk.get("blockdevices") or [] if _mounted(d, mount)]
        out[mount] = any(_encrypted_under(d, mount) for d in devs) if devs else None
    return out
