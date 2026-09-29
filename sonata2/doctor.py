"""`sonata2 doctor`: is this computer ready to run Sonata as its desktop?

Checks libraries, Wayfire and its plugins, the login-screen entry, the
helper programs each feature uses, the lock screen's PAM service, the GPU
setup and the keyboard layout; prints what's missing with the command that
fixes it, and saves the report to ~/.cache/sonata2/doctor.txt (plus the
last session's errors, for bug reports)."""
import os
import re
import shutil
import subprocess
import sys

OK, WARN, FAIL = "ok", "warn", "FAIL"
_COLOR = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m"}


def _run(cmd, timeout=5) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _family() -> str:
    try:
        from .files.packages import family
        return family()
    except Exception:           # noqa: BLE001 -- a report, never fatal
        return ""


# optional programs: (command, what it's for, package on Arch / Debian / Fedora)
TOOLS = [
    ("nmcli", "Wi-Fi and network", "networkmanager", "network-manager", "NetworkManager"),
    ("wpctl", "sound", "wireplumber", "wireplumber", "wireplumber"),
    ("pactl", "headphones / speakers ports", "libpulse", "pulseaudio-utils", "pulseaudio-utils"),
    ("bluetoothctl", "Bluetooth", "bluez-utils", "bluez", "bluez"),
    ("brightnessctl", "brightness keys (fallback)", "brightnessctl", "brightnessctl", "brightnessctl"),
    ("wlr-randr", "Settings > Displays", "wlr-randr", "wlr-randr", "wlr-randr"),
    ("powerprofilesctl", "energy modes / Low Power Mode", "power-profiles-daemon", "power-profiles-daemon",
     "power-profiles-daemon"),
    ("grim", "screenshots, Dock window previews", "grim", "grim", "grim"),
    ("slurp", "screenshot of an area", "slurp", "slurp", "slurp"),
    ("wf-recorder", "screen recording", "wf-recorder", "wf-recorder", "wf-recorder"),
    ("wl-copy", "clipboard from the shell", "wl-clipboard", "wl-clipboard", "wl-clipboard"),
    ("wlsunset", "Night Shift", "wlsunset", "wlsunset", "wlsunset"),
    ("swayidle", "lock after the display sleeps", "swayidle", "swayidle", "swayidle"),
    ("wtype", "typing emoji from the Character Viewer", "wtype", "wtype", "wtype"),
    ("ffmpegthumbnailer", "video thumbnails in Files", "ffmpegthumbnailer", "ffmpegthumbnailer",
     "ffmpegthumbnailer"),
    ("Xwayland", "X11 apps (Steam, older apps)", "xorg-xwayland", "xwayland", "xorg-x11-server-Xwayland"),
]
POLKIT_AGENTS = ["/usr/lib/polkit-gnome/polkit-gnome-authentication-agent-1",
                 "/usr/libexec/polkit-gnome-authentication-agent-1",
                 "/usr/lib/x86_64-linux-gnu/polkit-gnome-authentication-agent-1",
                 "/usr/lib/polkit-kde-authentication-agent-1",
                 "/usr/libexec/polkit-kde-authentication-agent-1",
                 "/usr/lib/mate-polkit/polkit-mate-authentication-agent-1",
                 "/usr/libexec/polkit-mate-authentication-agent-1",
                 "/usr/bin/lxqt-policykit-agent", "/usr/lib/xfce-polkit/xfce-polkit",
                 "/usr/libexec/xfce-polkit", "/usr/lib/hyprpolkitagent/hyprpolkitagent"]


class Report:
    def __init__(self):
        self.rows = []

    def add(self, status, what, detail="", fix=""):
        self.rows.append((status, what, detail, fix))

    def text(self, color=False) -> str:
        out = []
        for status, what, detail, fix in self.rows:
            tag = f"{_COLOR[status]}{status:>4}\033[0m" if color else f"{status:>4}"
            out.append(f"[{tag}] {what}" + (f" -- {detail}" if detail else ""))
            if fix:
                out.append(f"       fix: {fix}")
        n_fail = sum(1 for r in self.rows if r[0] == FAIL)
        n_warn = sum(1 for r in self.rows if r[0] == WARN)
        out.append("")
        out.append("Ready to log in to Sonata." if not n_fail else f"{n_fail} problem(s) to fix before logging in.")
        if n_warn:
            out.append(f"{n_warn} optional thing(s) missing: those features stay off.")
        return "\n".join(out)


def check_python(r: Report) -> None:
    r.add(OK if sys.version_info >= (3, 10) else FAIL, f"Python {sys.version.split()[0]}")
    import gi
    for ns, ver, pkg in (("Gtk", "4.0", "gtk4"), ("Adw", "1", "libadwaita"),
                         ("Gtk4LayerShell", "1.0", "gtk4-layer-shell"),
                         ("Gtk4SessionLock", "1.0", "gtk4-layer-shell (1.1+, lock screen)")):
        try:
            gi.require_version(ns, ver)
            mod = __import__("gi.repository." + ns, fromlist=[ns])
            v = ""
            if hasattr(mod, "get_major_version"):
                v = f"{mod.get_major_version()}.{mod.get_minor_version()}.{mod.get_micro_version()}"
            r.add(OK, f"{ns} {v}".strip())
        except (ValueError, ImportError):
            r.add(FAIL if ns != "Gtk4SessionLock" else WARN, f"{ns} missing", fix=f"install {pkg}")
    for mod, pkg in (("pywayland", "python-pywayland (or pip install --user pywayland)"),
                     ("cairo", "python-cairo")):
        try:
            __import__(mod)
            r.add(OK, mod)
        except ImportError:
            r.add(FAIL, f"{mod} missing", fix=f"install {pkg}")


def _plugin_dirs():
    dirs = [_run(["pkg-config", "--variable=plugindir", "wayfire"])]
    dirs += ["/usr/lib/wayfire", "/usr/lib64/wayfire", "/usr/local/lib/wayfire", "/usr/lib/x86_64-linux-gnu/wayfire"]
    return [d for d in dirs if d and os.path.isdir(d)]


def check_wayfire(r: Report, repo: str) -> None:
    if not shutil.which("wayfire"):
        r.add(FAIL, "Wayfire missing", fix="install wayfire (0.9+)")
        return
    r.add(OK, "Wayfire " + (_run(["wayfire", "--version"]).splitlines() or ["?"])[0])
    dirs = _plugin_dirs()
    have = {f[3:-3] for d in dirs for f in os.listdir(d) if f.startswith("lib") and f.endswith(".so")}
    cfg = os.path.join(repo, "config", "wayfire.ini")
    try:
        m = re.search(r"^plugins\s*=\s*(.+)$", open(cfg).read(), re.M)
        wanted = m.group(1).split() if m else []
    except OSError:
        wanted = []
    missing = [p for p in wanted if p not in have and not (p == "decoration" and "pixdecor" in have)]
    if have and missing:
        r.add(FAIL, "Wayfire plugins missing: " + " ".join(missing),
              fix="install wayfire's full plugin set (plus wayfire-plugins-extra if listed)")
    elif have:
        r.add(OK, "Wayfire plugins")
    corners = os.path.expanduser("~/.local/share/wayfire/plugin-manager/install/lib/wayfire/libsonata-corners.so")
    r.add(OK if os.path.exists(corners) else WARN, "sonata-corners (rounded corners for Chrome, Spotify...)",
          fix="" if os.path.exists(corners) else "./install.sh   (needs meson, ninja and Wayfire's headers)")
    r.add(OK if "pixdecor" in have else WARN, "pixdecor (macOS title bars for terminals / X11 apps)",
          fix="" if "pixdecor" in have else "AUR: wayfire-plugin-pixdecor-git")


def check_install(r: Report) -> None:
    bin_ = shutil.which("sonata-session") or os.path.expanduser("~/.local/bin/sonata-session")
    r.add(OK if os.path.exists(bin_) else FAIL, "sonata-session launcher",
          fix="" if os.path.exists(bin_) else "./install.sh --dev   (from the repo)")
    entry = "/usr/share/wayland-sessions/sonata.desktop"
    try_exec = ""
    try:
        m = re.search(r"^TryExec=(.+)$", open(entry).read(), re.M)
        try_exec = m.group(1).strip() if m else ""
    except OSError:
        pass
    if not os.path.exists(entry):
        r.add(FAIL, "\"Sonata\" on the login screen", fix="./install.sh --dev")
    elif try_exec.startswith("/home/") or (try_exec and not os.path.exists(try_exec)):
        # the login screen checks TryExec as its own user: a private home is invisible to it
        r.add(FAIL, "\"Sonata\" is hidden on the login screen", f"it can't reach {try_exec}",
              fix="./install.sh --dev   (installs /usr/local/bin/sonata-login)")
    else:
        r.add(OK, "\"Sonata\" on the login screen")
    for conf in ("/etc/gdm/custom.conf", "/etc/gdm3/custom.conf", "/etc/gdm3/daemon.conf"):
        try:
            if re.search(r"^\s*WaylandEnable\s*=\s*false", open(conf).read(), re.M):
                r.add(FAIL, "GDM runs without Wayland: it hides Wayland sessions like Sonata",
                      conf, fix=f"remove WaylandEnable=false from {conf}, then restart")
        except OSError:
            pass
    share = os.path.expanduser("~/.local/share/sonata2")
    if os.path.islink(share):
        r.add(OK, "dev install", os.path.realpath(share))
    elif os.path.isdir(share):
        r.add(WARN, "installed copy (not linked to the repo)", "code edits need ./install.sh again",
              fix="./install.sh --dev")
    cfg = os.path.expanduser("~/.config/sonata2/wayfire.ini")
    if os.path.exists(cfg + ".new"):
        r.add(WARN, "session config edited by you; a newer default is next to it", cfg + ".new")
    dm = next((d for d in ("sddm", "gdm", "lightdm", "greetd", "ly", "plasmalogin")
               if _run(["systemctl", "is-active", d]) == "active"), "")
    r.add(OK if dm else WARN, f"login screen: {dm or 'unknown'}",
          "" if dm in ("sddm", "gdm", "lightdm", "plasmalogin", "") else "make sure it lists wayland-sessions")


def check_tools(r: Report) -> None:
    fam = _family()
    col = {"arch": 2, "debian": 3, "rpm": 4}.get(fam, 2)
    missing = []
    for row in TOOLS:
        cmd, what = row[0], row[1]
        if shutil.which(cmd):
            r.add(OK, f"{cmd} ({what})")
        else:
            r.add(WARN, f"{cmd} missing", what)
            missing.append(row[col])
    agent = next((a for a in POLKIT_AGENTS if os.path.exists(a)), None)
    r.add(OK if agent else FAIL, "polkit agent (password prompts: users, printers, updates)", agent or "",
          fix="" if agent else {"arch": "sudo pacman -S polkit-gnome", "debian": "sudo apt install policykit-1-gnome",
                                "rpm": "sudo dnf install polkit-gnome"}.get(fam, "install polkit-gnome"))
    portals = [p for p in ("xdg-desktop-portal", "xdg-desktop-portal-gtk", "xdg-desktop-portal-wlr")
               if any(os.path.exists(os.path.join(d, p)) for d in ("/usr/lib", "/usr/libexec", "/usr/lib/x86_64-linux-gnu"))]
    lack = {"xdg-desktop-portal-gtk", "xdg-desktop-portal-wlr"} - set(portals)
    r.add(OK if not lack else WARN, "portals (file dialogs, screen sharing)", " ".join(sorted(lack)) + " missing" if lack else "")
    if lack:
        missing += sorted(lack)
    kp = shutil.which("keepassxc")
    r.add(OK if kp else WARN, "KeePassXC (saved passwords: Chrome, VS Code, Wi-Fi -- Sonata's keyring)",
          fix="" if kp else "install keepassxc")
    if missing:
        pm = {"arch": "sudo pacman -S --needed", "debian": "sudo apt install", "rpm": "sudo dnf install"}.get(fam, "install")
        r.add(WARN, "all optional packages in one go", fix=f"{pm} {' '.join(dict.fromkeys(missing))}")


def check_lock(r: Report) -> None:
    from . import pam
    svc = pam._service()
    r.add(OK if os.path.exists(f"/etc/pam.d/{svc}") else FAIL, f"lock screen PAM service: {svc}")


def check_gpu(r: Report) -> None:
    drm = sorted(d for d in os.listdir("/sys/class/drm") if re.fullmatch(r"card\d+", d)) if os.path.isdir("/sys/class/drm") else []
    drivers = []
    for c in drm:
        try:
            drivers.append(os.path.basename(os.readlink(f"/sys/class/drm/{c}/device/driver")))
        except OSError:
            pass
    r.add(OK, "GPUs: " + (", ".join(drivers) or "?"))
    if "nvidia" in drivers:
        ms = ""
        try:
            ms = open("/sys/module/nvidia_drm/parameters/modeset").read().strip()
        except OSError:
            pass
        r.add(OK if ms == "Y" else FAIL, "NVIDIA kernel modesetting", f"nvidia_drm.modeset={ms or '?'}",
              fix="" if ms == "Y" else "add nvidia_drm.modeset=1 to the kernel command line")
        if len(drivers) > 1:
            r.add(OK, "hybrid graphics: Wayfire draws on the first GPU (the integrated one); "
                      "games still use the NVIDIA card (prime-run / Steam)")


def check_keyboard(r: Report) -> None:
    out = _run(["localectl", "status"])
    lay = re.search(r"X11 Layout:\s*(\S+)", out)
    var = re.search(r"X11 Variant:\s*(\S+)", out)
    if lay:
        r.add(OK, f"keyboard layout from the system: {lay.group(1)}" + (f" ({var.group(1)})" if var else ""),
              "the Sonata session uses it unless you pick one in Settings > Keyboard")
    else:
        r.add(WARN, "no system keyboard layout (localectl)", "Sonata starts with US; pick yours in Settings > Keyboard")


def last_session_errors(n=25) -> str:
    logs = os.path.expanduser("~/.cache/sonata2")
    out = []
    for name in ("login.log", "session.log", "topbar.log", "dock.log", "launchpad.log", "wallpaper.log",
                 "spotlight.log"):
        p = os.path.join(logs, name)
        try:
            lines = open(p, errors="replace").read().splitlines()
        except OSError:
            continue
        bad = [ln for ln in lines if re.search(r"Traceback|Error|CRITICAL|EE |cannot|failed", ln)]
        if bad:
            out.append(f"--- {name} ---")
            out += bad[-n:]
    return "\n".join(out)


def main() -> int:
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = Report()
    for check in (check_python, lambda x: check_wayfire(x, repo), check_install, check_tools, check_lock,
                  check_gpu, check_keyboard):
        try:
            check(r)
        except Exception as e:          # noqa: BLE001 -- a report, never fatal
            r.add(WARN, f"check failed: {e}")
    print(r.text(color=sys.stdout.isatty()))
    errors = last_session_errors()
    d = os.path.expanduser("~/.cache/sonata2")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "doctor.txt"), "w") as f:
        f.write(r.text() + ("\n\nLast session:\n" + errors if errors else "") + "\n")
    print(f"\nSaved to {d}/doctor.txt")
    return 0 if not any(row[0] == FAIL for row in r.rows) else 1


if __name__ == "__main__":
    sys.exit(main())
