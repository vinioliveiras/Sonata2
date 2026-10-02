"""Screen Sharing (Vini): see and control this Sonata desktop from another
computer or a phone, with any VNC viewer (RealVNC Viewer, TigerVNC...).
Chrome Remote Desktop can't: on Wayland it only mirrors GNOME.

wayvnc (wlroots' VNC server; Wayfire has the screencopy and virtual
keyboard/pointer protocols it needs) runs from the menu bar process while
Settings > Sharing > Screen Sharing is on. Always with a password:
user "sonata" + a generated password, encrypted with RSA-AES (wayvnc's
own key in ~/.config/sonata2/wayvnc, files readable only by you).
From outside the home network: Tailscale on both devices, then the
computer's Tailscale address.

    config "sharing": {"screen": bool, "output": "HDMI-A-1" or ""}
    ScreenSharing().start() / .apply() / .stop()
    credentials() -> ("sonata", password)
"""
import os
import secrets
import shutil
import subprocess

from .. import config

NAME = "sharing"
DEFAULTS = {"screen": False, "output": "", "rev": 0}     # rev: bumped to restart it (a new password)
PORT = 5900
USER = "sonata"
RESTART_MS = (2000, 5000, 15000, 60000)     # a server that keeps quitting waits longer each time


def folder() -> str:
    return os.path.join(config.CONFIG_DIR, "wayvnc")


def installed() -> bool:
    return shutil.which("wayvnc") is not None


def _private(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def credentials() -> tuple:
    """("sonata", password): made once, kept in wayvnc's config."""
    path = os.path.join(folder(), "password")
    try:
        with open(path, encoding="utf-8") as f:
            pw = f.read().strip()
        if pw:
            return USER, pw
    except OSError:
        pass
    return USER, new_password()


def new_password() -> str:
    os.makedirs(folder(), mode=0o700, exist_ok=True)
    pw = "-".join(secrets.token_hex(2) for _ in range(3))        # "4f1a-9c02-b7e3": easy to type on a phone
    path = os.path.join(folder(), "password")
    with open(path, "w", encoding="utf-8") as f:
        f.write(pw + "\n")
    _private(path)
    return pw


def _key() -> str:
    """wayvnc's RSA key (RSA-AES: the password and the screen go encrypted)."""
    path = os.path.join(folder(), "rsa_key.pem")
    if not os.path.exists(path) and shutil.which("ssh-keygen"):
        subprocess.run(["ssh-keygen", "-q", "-m", "pem", "-t", "rsa", "-b", "3072", "-N", "", "-f", path],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=30)
        try:
            os.remove(path + ".pub")
        except OSError:
            pass
    if os.path.exists(path):
        _private(path)
        return path
    return ""


def write_config() -> str:
    """wayvnc's config file; returns its path."""
    os.makedirs(folder(), mode=0o700, exist_ok=True)
    user, pw = credentials()
    lines = ["address=0.0.0.0", f"port={PORT}", "enable_auth=true", f"username={user}", f"password={pw}"]
    key = _key()
    if key:
        lines.append(f"rsa_private_key_file={key}")
    path = os.path.join(folder(), "config")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    _private(path)
    return path


def addresses() -> list:
    """Where to connect: this computer's local addresses (Tailscale's first)."""
    out = []
    try:
        res = subprocess.run(["ip", "-o", "-4", "addr", "show", "scope", "global"], capture_output=True,
                             text=True, timeout=3)
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) > 3:
                iface, ip = parts[1], parts[3].split("/")[0]
                out.append((0 if iface.startswith("tailscale") else 1, f"{ip}:{PORT}", iface))
    except (OSError, subprocess.SubprocessError):
        pass
    return [(a, i) for _o, a, i in sorted(out)]


class ScreenSharing:
    """wayvnc while sharing is on; started again if it quits."""

    def __init__(self):
        self.proc = None
        self.fails = 0
        self._retry = 0
        self._mon = None
        self.running_with = None

    def start(self) -> None:
        self._mon = config.watch(NAME, self.apply)
        self.apply()

    def wanted(self) -> dict:
        cfg = config.load(NAME, DEFAULTS)
        return {"screen": bool(cfg.get("screen")) and installed(), "output": cfg.get("output") or "",
                "rev": cfg.get("rev", 0)}

    def apply(self) -> None:
        want = self.wanted()
        if not want["screen"]:
            self.stop()
            return
        if self.proc is not None and self.proc.poll() is None and self.running_with == want:
            return
        self.stop()
        self._spawn(want)

    def _spawn(self, want) -> None:
        from gi.repository import GLib
        cmd = ["wayvnc", "-C", write_config()]
        if want["output"]:
            cmd += ["-o", want["output"]]
        log = open(os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                                "sonata2", "screen-sharing.log"), "a")
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        except OSError as e:
            print(f"sonata2: screen sharing: {e}")
            self.proc = None
            return
        finally:
            log.close()
        self.running_with = want
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.proc.pid, self._ended, self.proc)

    def _ended(self, _pid, _status, proc) -> None:
        from gi.repository import GLib
        if proc is not self.proc:
            return                                    # stopped on purpose (stop / apply)
        self.proc = None
        if not self.wanted()["screen"]:
            return
        delay = RESTART_MS[min(self.fails, len(RESTART_MS) - 1)]
        self.fails += 1
        self._retry = GLib.timeout_add(delay, lambda: (setattr(self, "_retry", 0), self.apply(), False)[2])

    def stop(self) -> None:
        from gi.repository import GLib
        if self._retry:
            GLib.source_remove(self._retry)
            self._retry = 0
        proc, self.proc, self.running_with = self.proc, None, None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
