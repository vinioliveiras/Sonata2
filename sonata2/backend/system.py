"""Linux system state for the shell (Wi-Fi, Bluetooth, sound, brightness,
battery, power actions, About). Ported from LayerOSX's layerosx_backend.py
(nmcli, sysfs battery, brightnessctl, DMI/lspci naming) without the VM
parts; ALSA replaced by PipeWire (wpctl), plus Bluetooth and session
actions. These are *Linux* settings: Sonata reads/writes the system
services and stores nothing itself.

Everything here may block (subprocesses): call it through `run_async`."""
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

from gi.repository import Gio, GLib

POWER_SUPPLY = "/sys/class/power_supply"
_JUNK_DMI = ("to be filled by o.e.m.", "default string", "system product name", "not applicable", "")


def run_async(fn, callback=None, *args) -> None:
    """Run fn(*args) in a thread; callback(result) back on the GTK thread."""
    def work():
        try:
            res = fn(*args)
        except Exception as e:          # never kill the shell over a status read
            print(f"sonata2: {fn.__name__} failed: {e}")
            res = None
        if callback:
            GLib.idle_add(lambda: (callback(res), False)[1])
    threading.Thread(target=work, daemon=True).start()


def _run(cmd: List[str], timeout: int = 10) -> Tuple[int, str]:
    if not shutil.which(cmd[0]):
        return 127, ""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def _spawn(cmd: List[str]) -> None:
    if shutil.which(cmd[0]):
        subprocess.Popen(cmd, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _split_nmcli(line: str) -> List[str]:
    """Split nmcli -t output on ':' honouring its '\\:' escapes."""
    parts, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return parts


def _int(s: str) -> int:
    try:
        return int(s)
    except ValueError:
        return 0


# -- Wi-Fi (NetworkManager) --------------------------------------------------------
@dataclass
class WifiNetwork:
    ssid: str
    signal: int
    secure: bool
    connected: bool


def wifi_available() -> bool:
    rc, out = _run(["nmcli", "-t", "-f", "TYPE", "device"])
    return rc == 0 and "wifi" in out.split()


def wifi_enabled() -> bool:
    rc, out = _run(["nmcli", "radio", "wifi"])
    return rc == 0 and out.strip().splitlines()[-1:] == ["enabled"]


def set_wifi_enabled(on: bool) -> bool:
    return _run(["nmcli", "radio", "wifi", "on" if on else "off"])[0] == 0


def wifi_current() -> Tuple[str, int, bool]:
    """(ssid, signal 0-100, wired connected)."""
    rc, out = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "device", "wifi"])
    ssid, sig = "", 0
    for line in out.splitlines() if rc == 0 else []:
        parts = _split_nmcli(line)
        if len(parts) >= 3 and parts[0] == "yes":
            ssid, sig = parts[1], _int(parts[2])
            break
    rc, out = _run(["nmcli", "-t", "-f", "TYPE,STATE", "device"])
    wired = rc == 0 and any(ln.startswith("ethernet:connected") for ln in out.splitlines())
    return ssid, sig, wired


def wifi_scan(rescan: bool = False) -> List[WifiNetwork]:
    if rescan:
        _run(["nmcli", "device", "wifi", "rescan"], timeout=15)
    rc, out = _run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"], timeout=20)
    best = {}
    for line in out.splitlines() if rc == 0 else []:
        parts = _split_nmcli(line)
        if len(parts) < 4 or not parts[1]:
            continue
        inuse, ssid, sig, sec = parts[0] == "*", parts[1], _int(parts[2]), parts[3]
        cur = best.get(ssid)
        if cur is None or sig > cur.signal or inuse:
            best[ssid] = WifiNetwork(ssid, max(sig, cur.signal if cur else 0), sec not in ("", "--"),
                                     inuse or (cur.connected if cur else False))
    return sorted(best.values(), key=lambda n: (not n.connected, -n.signal, n.ssid.lower()))


def wifi_connect(ssid: str, password: str = "") -> Tuple[bool, str]:
    cmd = ["nmcli", "device", "wifi", "connect", ssid] + (["password", password] if password else [])
    rc, out = _run(cmd, timeout=45)
    return rc == 0, (out.replace(password, "•••") if password else out).strip()


def wifi_disconnect() -> bool:
    rc, out = _run(["nmcli", "-t", "-f", "DEVICE,TYPE", "device"])
    dev = next((ln.split(":")[0] for ln in out.splitlines() if ln.endswith(":wifi")), None)
    return bool(dev) and _run(["nmcli", "device", "disconnect", dev])[0] == 0


# -- Network services (Big Sur Network pane): Ethernet, VPN, others -----------------
@dataclass
class NetService:
    name: str
    uuid: str
    kind: str          # "ethernet" | "vpn" | "wifi" | other nmcli type
    device: str
    active: bool
    address: str = ""
    gateway: str = ""
    dns: str = ""


NET_KINDS = {"802-3-ethernet": "ethernet", "vpn": "vpn", "wireguard": "vpn", "802-11-wireless": "wifi",
             "bridge": "bridge", "bond": "bond", "gsm": "mobile", "bluetooth": "bluetooth"}


def net_services() -> List[NetService]:
    """Saved NetworkManager connections except Wi-Fi networks and loopback,
    active ones first (with their address)."""
    rc, out = _run(["nmcli", "-t", "-f", "NAME,UUID,TYPE,DEVICE,ACTIVE", "connection", "show"])
    if rc != 0:
        return []
    services = []
    for ln in out.splitlines():
        p = _split_nmcli(ln)
        if len(p) < 5 or p[2] in ("loopback", "802-11-wireless"):
            continue
        s = NetService(p[0], p[1], NET_KINDS.get(p[2], p[2]), p[3], p[4] == "yes")
        if s.active and s.device:
            rc2, info = _run(["nmcli", "-t", "-f", "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", s.device])
            for il in info.splitlines():
                k, _, v = il.partition(":")
                if k.startswith("IP4.ADDRESS") and not s.address:
                    s.address = v.split("/")[0]
                elif k == "IP4.GATEWAY":
                    s.gateway = v
                elif k.startswith("IP4.DNS") and not s.dns:
                    s.dns = v
        services.append(s)
    services.sort(key=lambda s: (not s.active, s.kind != "ethernet", s.name.lower()))
    return services


def net_service_set(uuid: str, up: bool) -> Tuple[bool, str]:
    rc, out = _run(["nmcli", "connection", "up" if up else "down", "uuid", uuid], timeout=45)
    return rc == 0, out.strip()


def vpn_import(path: str) -> Tuple[bool, str]:
    """OpenVPN (.ovpn) or WireGuard (.conf) file -> a NetworkManager VPN."""
    kind = "wireguard" if path.endswith(".conf") else "openvpn"
    rc, out = _run(["nmcli", "connection", "import", "type", kind, "file", path], timeout=20)
    return rc == 0, out.strip()


def net_service_delete(uuid: str) -> bool:
    return _run(["nmcli", "connection", "delete", "uuid", uuid])[0] == 0


# -- Printers (CUPS) ----------------------------------------------------------------
@dataclass
class Printer:
    name: str
    state: str          # "Idle", "Printing", "Disabled"
    default: bool


def printers() -> Optional[List[Printer]]:
    """None when CUPS isn't installed."""
    if not shutil.which("lpstat"):
        return None
    _rc, d = _run(["lpstat", "-d"])
    default = d.split(":", 1)[1].strip() if ":" in d else ""
    _rc, out = _run(["lpstat", "-p"])
    out_list = []
    for ln in out.splitlines():
        m = re.match(r"printer (\S+) (?:is )?(\w+)", ln)
        if m:
            st = {"idle": "Idle", "now": "Printing", "disabled": "Disabled"}.get(m.group(2), m.group(2).title())
            out_list.append(Printer(m.group(1), st, m.group(1) == default))
    return out_list


def set_default_printer(name: str) -> bool:
    return _run(["lpoptions", "-d", name])[0] == 0


def add_printer() -> None:
    """The distro's printer tool, else the CUPS web page."""
    for tool in ("system-config-printer", "kde-add-printer"):
        if shutil.which(tool):
            _spawn([tool])
            return
    Gio.AppInfo.launch_default_for_uri("http://localhost:631/admin", None)


# -- Software Update ---------------------------------------------------------------
# (updates.py lists and installs; a terminal is its fallback)
TERMINALS = (("kgx", ["--"]), ("gnome-terminal", ["--"]), ("konsole", ["-e"]), ("kitty", []),
             ("alacritty", ["-e"]), ("foot", []), ("ghostty", ["-e"]), ("wezterm", ["start", "--"]),
             ("xfce4-terminal", ["-x"]), ("xterm", ["-e"]))


def run_in_terminal(command: str) -> bool:
    """Run a shell command in a terminal window that stays open at the end."""
    import shutil
    import shlex
    try:                                    # Sonata's own Terminal first (needs VTE for GTK 4)
        import gi
        gi.require_version("Vte", "3.91")
        from gi.repository import Vte  # noqa: F401
        from ..__main__ import self_command
        subprocess.Popen(shlex.split(self_command()) + ["terminal", "--exec", command], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except (ValueError, ImportError, OSError):
        pass
    script = f"{command}; echo; read -p 'Press Enter to close' _"
    for term, flag in ([(os.environ["TERMINAL"], ["-e"])] if os.environ.get("TERMINAL") else []) + list(TERMINALS):
        if shutil.which(term):
            _spawn([term] + flag + ["sh", "-c", script])
            return True
    return False


# -- Bluetooth -----------------------------------------------------------------------
def bluetooth_state() -> Optional[bool]:
    """True/False = powered on/off, None = no adapter."""
    rc, out = _run(["bluetoothctl", "show"], timeout=5)
    if rc != 0 or "Controller" not in out:
        return None
    return "Powered: yes" in out


def set_bluetooth(on: bool) -> bool:
    if on:
        _run(["rfkill", "unblock", "bluetooth"])
    return _run(["bluetoothctl", "power", "on" if on else "off"], timeout=8)[0] == 0


# -- sound (PipeWire / WirePlumber) ---------------------------------------------------
def volume() -> Optional[Tuple[int, bool]]:
    """(percent, muted) of the default output, None without wpctl."""
    rc, out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"], timeout=5)
    m = re.search(r"Volume:\s*([\d.]+)", out) if rc == 0 else None
    return (round(float(m.group(1)) * 100), "[MUTED]" in out) if m else None


def set_volume(percent: Optional[int] = None, muted: Optional[bool] = None,
               node: str = "@DEFAULT_AUDIO_SINK@") -> bool:
    ok = True
    if percent is not None:
        ok = _run(["wpctl", "set-volume", "-l", "1.0", node, f"{max(0, min(100, int(percent)))}%"])[0] == 0
    if muted is not None:
        ok = _run(["wpctl", "set-mute", node, "1" if muted else "0"])[0] == 0 and ok
    return ok


def input_volume() -> Optional[Tuple[int, bool]]:
    """(percent, muted) of the default microphone, None without one."""
    rc, out = _run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"], timeout=5)
    m = re.search(r"Volume:\s*([\d.]+)", out) if rc == 0 else None
    return (round(float(m.group(1)) * 100), "[MUTED]" in out) if m else None


def set_input_volume(percent: Optional[int] = None, muted: Optional[bool] = None) -> bool:
    return set_volume(percent, muted, "@DEFAULT_AUDIO_SOURCE@")


# -- display brightness ----------------------------------------------------------------
BACKLIGHT = "/sys/class/backlight"


def _backlight():
    """(device name, current, max) of the laptop panel, read from sysfs
    (firmware/platform interfaces first, like brightnessctl), or None."""
    try:
        names = sorted(os.listdir(BACKLIGHT))
    except OSError:
        return None
    rank = {"firmware": 0, "platform": 1, "raw": 2}
    names.sort(key=lambda n: rank.get(_read(os.path.join(BACKLIGHT, n, "type")), 3))
    for n in names:
        cur, mx = _read(os.path.join(BACKLIGHT, n, "brightness")), _read(os.path.join(BACKLIGHT, n, "max_brightness"))
        if cur.isdigit() and mx.isdigit() and int(mx) > 0:
            return n, int(cur), int(mx)
    return None


def brightness() -> Optional[int]:
    """Panel brightness in %: sysfs (no extra tool needed), else brightnessctl."""
    bl = _backlight()
    if bl:
        return round(bl[1] * 100 / bl[2])
    rc, out = _run(["brightnessctl", "-m", "-c", "backlight"], timeout=5)
    m = re.search(r",(\d+)%,", out) if rc == 0 else None
    return int(m.group(1)) if m else None


def set_brightness(percent: int) -> bool:
    """Through logind (SetBrightness: allowed for the active session, no root,
    no brightnessctl), else brightnessctl."""
    percent = max(5, min(100, int(percent)))   # never fully black
    bl = _backlight()
    if bl:
        name, _cur, mx = bl
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1/session/auto",
                          "org.freedesktop.login1.Session", "SetBrightness",
                          GLib.Variant("(ssu)", ("backlight", name, round(mx * percent / 100))), None,
                          Gio.DBusCallFlags.NONE, 2000, None)
            return True
        except GLib.Error:
            pass
    return _run(["brightnessctl", "-q", "-c", "backlight", "set", f"{percent}%"])[0] == 0


# -- battery -------------------------------------------------------------------------------
def battery(power_supply: str = POWER_SUPPLY) -> Tuple[Optional[int], str]:
    """(percent or None without a battery, status: Charging/Discharging/Full/...)."""
    try:
        bats = sorted(x for x in os.listdir(power_supply) if x.startswith("BAT"))
    except OSError:
        bats = []
    if not bats:
        return None, ""
    b = os.path.join(power_supply, bats[0])
    cap = _read(os.path.join(b, "capacity"))
    return (int(cap) if cap.isdigit() else None), _read(os.path.join(b, "status"))


def on_ac(power_supply: str = POWER_SUPPLY) -> bool:
    """Plugged in: any mains / USB-C supply online (names vary: AC, ACAD,
    ADP1, ucsi-source-psy-...; the "type" file is what counts)."""
    try:
        names = os.listdir(power_supply)
    except OSError:
        return False
    for x in names:
        kind = _read(os.path.join(power_supply, x, "type"))
        if (kind in ("Mains", "USB") or x.startswith(("AC", "ADP"))) and \
                _read(os.path.join(power_supply, x, "online")) == "1":
            return True
    return False


# -- session / power -------------------------------------------------------------------------
def power_action(kind: str) -> None:
    """sleep / restart / shutdown / lock / logout."""
    cmd = {"sleep": ["systemctl", "suspend"], "restart": ["systemctl", "reboot"],
           "shutdown": ["systemctl", "poweroff"], "lock": ["loginctl", "lock-session"]}.get(kind)
    if kind == "logout":
        sid = os.environ.get("XDG_SESSION_ID")
        cmd = ["loginctl", "terminate-session", sid] if sid else ["loginctl", "terminate-user", os.environ.get("USER", "")]
    if cmd:
        _spawn(cmd)


def restart_sonata() -> None:
    """Reload the shell (Dock, menu bar, Launchpad, Spotlight, wallpaper)
    with the current code; open apps stay. Detached: it outlives the menu
    bar that asked for it."""
    import sys
    launcher = os.environ.get("SONATA2_LAUNCHER")
    if launcher and os.path.exists(launcher):
        cmd = [launcher, "restart"]
    else:
        cmd = [sys.executable, "-m", "sonata2", "restart"]
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (root, os.environ.get("PYTHONPATH")) if p))
    subprocess.Popen(cmd, env=env, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# -- About ---------------------------------------------------------------------------------
@dataclass
class About:
    os_name: str
    machine: str
    cpu: str
    memory_gb: float
    gpus: List[str]
    kernel: str


def _machine_name(vendor: str, family: str, product: str) -> str:
    """'ASUSTeK COMPUTER INC.' + 'ASUS TUF Gaming A15 FA507NV_FA507NV' ->
    'ASUS TUF Gaming A15 FA507NV' (from LayerOSX)."""
    vendor, family, product = (x.strip() for x in (vendor, family, product))
    if product.lower() in _JUNK_DMI:
        product = ""
    product = re.sub(r"\b(\w+)_\1\b", r"\1", product)
    if family and family.lower() not in _JUNK_DMI and family not in product:
        product = f"{family} {product}".strip()
    first = vendor.split()[0].lower().rstrip(",.") if vendor else ""
    brand = {"asustek": "asus", "hewlett-packard": "hp", "micro-star": "msi"}.get(first, first)
    if product and brand and product.lower().startswith(brand):
        return product
    return " ".join(x for x in (vendor if vendor.lower() not in _JUNK_DMI else "", product) if x) or "Computer"


def _gpu_name(vendor: str, device: str) -> str:
    v = vendor.lower()
    brand = "NVIDIA" if "nvidia" in v else "AMD" if ("advanced micro" in v or re.search(r"\bati\b", v)) else \
        "Intel" if "intel" in v else re.sub(r"\s*(Corporation|Inc\.?|Co\.?,? Ltd\.?)\s*", " ", vendor).strip()
    m = re.search(r"\[([^\]]+)\]", device)
    name = m.group(1) if m else device
    return name if name.lower().startswith(brand.lower()) else f"{brand} {name}"


def about() -> About:
    osr = dict(ln.split("=", 1) for ln in _read("/etc/os-release").splitlines() if "=" in ln)
    dmi = "/sys/class/dmi/id"
    machine = _machine_name(_read(f"{dmi}/sys_vendor"), _read(f"{dmi}/product_family"),
                            _read(f"{dmi}/product_name"))
    cpuinfo = _read("/proc/cpuinfo")
    m = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.M)
    cpu = re.sub(r"\s+", " ", m.group(1)).strip() if m else "Unknown"
    m = re.search(r"^MemTotal:\s*(\d+) kB", _read("/proc/meminfo"), re.M)
    mem = round(int(m.group(1)) / 1024 / 1024) if m else 0
    gpus = []
    rc, out = _run(["lspci", "-mm"])
    for line in out.splitlines() if rc == 0 else []:
        f = re.findall(r'"([^"]*)"', line)
        if len(f) >= 3 and ("VGA" in f[0] or "3D" in f[0] or "Display" in f[0]):
            gpus.append(_gpu_name(f[1], f[2]))
    return About(osr.get("PRETTY_NAME", "Linux").strip('"'), machine, cpu, mem, gpus, os.uname().release)


# -- Bluetooth devices --------------------------------------------------------------------
@dataclass
class BtDevice:
    mac: str
    name: str
    paired: bool
    connected: bool


def bluetooth_devices() -> List[BtDevice]:
    rc, out = _run(["bluetoothctl", "devices"], timeout=8)
    devs = []
    for line in out.splitlines() if rc == 0 else []:
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == "Device":
            _rc, info = _run(["bluetoothctl", "info", parts[1]], timeout=5)
            devs.append(BtDevice(parts[1], parts[2], "Paired: yes" in info, "Connected: yes" in info))
    return sorted(devs, key=lambda d: (not d.connected, not d.paired, d.name.lower()))


def bluetooth_connect(mac: str, on: bool) -> bool:
    return _run(["bluetoothctl", "connect" if on else "disconnect", mac], timeout=20)[0] == 0


# -- sound outputs ----------------------------------------------------------------------------
@dataclass
class AudioSink:
    id: int
    name: str
    default: bool


def _wpctl_nodes(section: str) -> List[AudioSink]:
    """Devices of the Audio > `section` ("Sinks" / "Sources") part of `wpctl status`."""
    rc, out = _run(["wpctl", "status"], timeout=5)
    nodes, in_audio, current = [], False, None
    for line in out.splitlines() if rc == 0 else []:
        s = line.strip(" │├└─")
        if not line.startswith((" ", "│", "├", "└")) and s:     # top-level: Audio / Video / Settings
            in_audio = s.startswith("Audio")
            current = None
            continue
        if not in_audio:
            continue
        if s.endswith(":"):
            current = s[:-1]
            continue
        if current != section or not s:
            continue
        m = re.match(r"(\*)?\s*(\d+)\.\s+(.+?)(\s+\[vol:.*\])?$", s)
        if m:
            nodes.append(AudioSink(int(m.group(2)), m.group(3).strip(), bool(m.group(1))))
    return nodes


def audio_sinks() -> List[AudioSink]:
    """Output devices (wpctl)."""
    return _wpctl_nodes("Sinks")


def audio_sources() -> List[AudioSink]:
    """Input devices (microphones; wpctl)."""
    return _wpctl_nodes("Sources")


def set_default_sink(sink_id: int) -> bool:
    return _run(["wpctl", "set-default", str(sink_id)])[0] == 0


set_default_source = set_default_sink            # same wpctl call for inputs


# Outputs / inputs as people see them: one entry per available *port*
# ("Headphones", "Speakers", "HDMI"...) of each device -- wired headphones are
# a port of the built-in card, not a device of their own. pactl (PipeWire's
# pulse layer) knows ports; without it, wpctl's device list.
@dataclass
class AudioDevice:
    key: str            # "sink-name|port-name" (or a wpctl id)
    name: str
    default: bool


def _pactl_devices(kind: str) -> Optional[List[AudioDevice]]:
    import json
    rc, out = _run(["pactl", "-f", "json", "list", kind], timeout=5)
    if rc != 0:
        return None
    try:
        nodes = json.loads(out)
    except ValueError:
        return None
    _rc, default = _run(["pactl", "get-default-" + kind[:-1]], timeout=3)
    default = default.strip()
    devs = []
    for n in nodes:
        name = n.get("name", "")
        if kind == "sources" and name.endswith(".monitor"):
            continue                                          # "Monitor of ..." isn't a microphone
        if name.startswith("sonata-eq"):
            continue                                          # the equalizer's filters (equalizer.py)
        desc = n.get("description") or name
        all_ports = n.get("ports") or []
        ports = [p for p in all_ports if p.get("availability") != "not available"]
        if not all_ports:
            devs.append(AudioDevice(f"{name}|", desc, name == default))      # e.g. Bluetooth headphones
            continue
        for p in ports:                                       # none left: unplugged HDMI etc.
            label = p.get("description") or p.get("name", "")
            if label.lower() in ("analog output", "analog input", "") or len(ports) == 1 and len(all_ports) == 1:
                label = desc
            devs.append(AudioDevice(f"{name}|{p.get('name', '')}", label,
                                    name == default and n.get("active_port") == p.get("name")))
    names = [d.name for d in devs]                            # same label twice: say which device
    for d in devs:
        if names.count(d.name) > 1:
            node = d.key.split("|")[0]
            d.name = f"{d.name} ({next((n.get('description') for n in nodes if n.get('name') == node), node)})"
    return devs


def audio_outputs() -> List[AudioDevice]:
    devs = _pactl_devices("sinks")
    if devs is None:
        devs = [AudioDevice(str(s.id), s.name, s.default) for s in audio_sinks()]
    return devs


def audio_inputs() -> List[AudioDevice]:
    devs = _pactl_devices("sources")
    if devs is None:
        devs = [AudioDevice(str(s.id), s.name, s.default) for s in audio_sources()]
    return devs


def _select(kind: str, key: str) -> bool:
    if "|" not in key:                                         # wpctl id
        return set_default_sink(int(key))
    name, port = key.split("|", 1)
    ok = _run(["pactl", f"set-default-{kind}", name])[0] == 0
    if port:
        ok = _run(["pactl", f"set-{kind}-port", name, port])[0] == 0 and ok
    return ok


def select_output(key: str) -> bool:
    return _select("sink", key)


def select_input(key: str) -> bool:
    return _select("source", key)


# -- displays (wlroots compositors) -------------------------------------------------------------
@dataclass
class Display:
    name: str
    description: str
    modes: List[str]        # "1920x1080@60.000"
    current: str
    scale: float


def displays() -> List[Display]:
    rc, out = _run(["wlr-randr"], timeout=5)
    result, cur = [], None
    for line in out.splitlines() if rc == 0 else []:
        if line and not line.startswith(" "):
            name, _, desc = line.partition(" ")
            cur = Display(name, desc.strip('" '), [], "", 1.0)
            result.append(cur)
        elif cur is not None:
            s = line.strip()
            m = re.match(r"(\d+x\d+) px, ([\d.]+) Hz(.*)", s)
            if m:
                mode = f"{m.group(1)}@{m.group(2)}"
                if mode not in cur.modes:
                    cur.modes.append(mode)
                if "current" in m.group(3):
                    cur.current = mode
            elif s.startswith("Scale:"):
                cur.scale = float(s.split(":")[1])
    return result


def display_mode_setting(name: str) -> str:
    """The saved mode of a display: "highrr" (highest refresh rate, the
    default), or "1920x1080@143.981" as wlr-randr writes it."""
    v = wayfire_get(f"output:{name}", "mode", "highrr") or "highrr"
    m = re.fullmatch(r"(\d+x\d+)@(\d+)", v)
    if m and int(m.group(2)) > 1000:                  # Wayfire keeps mHz
        return f"{m.group(1)}@{int(m.group(2)) / 1000:.3f}"
    return v


def set_display_mode(name: str, mode: str) -> bool:
    """Saved in the session's Wayfire config (kept across logins; Wayfire
    applies it at once). "highrr" = the highest refresh rate available."""
    if mode not in ("highrr", "highres", "auto"):
        res, _, hz = mode.partition("@")
        mode = f"{res}@{round(float(hz or 60) * 1000)}"
    return wayfire_set(f"output:{name}", "mode", mode)


def set_display_scale(name: str, scale: float) -> bool:
    return wayfire_set(f"output:{name}", "scale", scale)


# -- power profiles -------------------------------------------------------------------------------
POWER_PROFILES = (("performance", "High Performance"), ("balanced", "Automatic"),
                  ("power-saver", "Low Power"))


def power_profile_fast() -> Optional[str]:
    """ActiveProfile over D-Bus (powerprofilesctl is a Python script: too
    heavy for the menu bar's poll)."""
    for name, path in (("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"),
                       ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles")):
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            v = bus.call_sync(name, path, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (name, "ActiveProfile")), None, Gio.DBusCallFlags.NONE, 500, None)
            return v.unpack()[0]
        except GLib.Error:
            continue
    return None


def power_profile() -> Optional[str]:
    rc, out = _run(["powerprofilesctl", "get"], timeout=5)
    return out.strip() if rc == 0 else None


def set_power_profile(profile: str) -> bool:
    return _run(["powerprofilesctl", "set", profile], timeout=5)[0] == 0


# -- desktop settings (Sonata's own store, prefs.py; apps read them through its portal) ------------
def set_dark_mode(on: bool) -> None:
    """Dark Mode for everything: Sonata (ui.theme follows color-scheme),
    libadwaita/GTK4 apps (color-scheme), GTK3 apps (theme name)."""
    set_gsetting("org.gnome.desktop.interface", "color-scheme", "prefer-dark" if on else "default")
    set_gsetting("org.gnome.desktop.interface", "gtk-theme", "Sonata-Dark" if on else "Sonata-Light")
    from .. import gtkstyle
    gtkstyle.update(on)


def gsetting(schema: str, key: str) -> Optional[str]:
    """A desktop setting -- Sonata's own store (prefs.py), not GNOME's."""
    from .. import prefs
    return prefs.get(schema, key)


def set_gsetting(schema: str, key: str, value: str) -> bool:
    from .. import prefs
    return prefs.set(schema, key, value)


# -- input devices (Wayfire [input] of the Sonata session) -----------------------------------
from ..wfconfig import wayfire_get, wayfire_set  # noqa: E402,F401  (gi-free, used by the session script)


XKB_LAYOUTS = [("us", "U.S."), ("us(intl)", "U.S. International"), ("br", "Brazilian (ABNT2)"),
               ("gb", "British"), ("de", "German"), ("fr", "French"), ("es", "Spanish"), ("pt", "Portuguese"),
               ("it", "Italian"), ("latam", "Latin American"), ("ru", "Russian"), ("jp", "Japanese")]


def keyboard_layouts() -> List[str]:
    """Input sources, the active (first) one first: ["br", "us(intl)"]."""
    lays = (wayfire_get("input", "xkb_layout", "us") or "us").split(",")
    vars_ = (wayfire_get("input", "xkb_variant", "") or "").split(",")
    vars_ += [""] * (len(lays) - len(vars_))
    return [f"{l}({v})" if v else l for l, v in zip(lays, vars_)]


def set_keyboard_layouts(layouts: List[str]) -> bool:
    lays = [x.split("(")[0] for x in layouts]
    vars_ = [x.split("(")[1].rstrip(")") if "(" in x else "" for x in layouts]
    return wayfire_set("input", "xkb_layout", ",".join(lays)) and wayfire_set("input", "xkb_variant", ",".join(vars_))


def keyboard_layout() -> str:
    lay = wayfire_get("input", "xkb_layout", "us") or "us"
    var = wayfire_get("input", "xkb_variant", "")
    return f"{lay}({var})" if var else lay


def set_keyboard_layout(value: str) -> bool:
    lay, _, var = value.partition("(")
    return wayfire_set("input", "xkb_layout", lay) and wayfire_set("input", "xkb_variant", var.rstrip(")"))


# -- date & time ----------------------------------------------------------------------------------
def timezone() -> str:
    rc, out = _run(["timedatectl", "show", "-p", "Timezone", "--value"], timeout=5)
    if rc == 0 and out.strip():
        return out.strip()
    try:
        return os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
    except (IndexError, OSError):
        return "UTC"


def timezones() -> List[str]:
    rc, out = _run(["timedatectl", "list-timezones"], timeout=10)
    return [z for z in out.split() if z] if rc == 0 else []


def set_timezone(zone: str) -> bool:
    return _run(["timedatectl", "set-timezone", zone], timeout=60)[0] == 0      # polkit may ask


def ntp() -> Optional[bool]:
    rc, out = _run(["timedatectl", "show", "-p", "NTP", "--value"], timeout=5)
    return out.strip() == "yes" if rc == 0 else None


def set_ntp(on: bool) -> bool:
    return _run(["timedatectl", "set-ntp", "true" if on else "false"], timeout=60)[0] == 0


# -- sharing ----------------------------------------------------------------------------------------
def computer_name() -> str:
    rc, out = _run(["hostnamectl", "--pretty"], timeout=5)
    if rc == 0 and out.strip():
        return out.strip()
    return GLib.get_host_name()


def set_computer_name(name: str) -> bool:
    """Pretty name as typed, host name derived (like macOS "Local hostname")."""
    host = re.sub(r"[^a-zA-Z0-9-]+", "-", name).strip("-").lower() or "computer"
    ok = _run(["hostnamectl", "set-hostname", "--pretty", name], timeout=60)[0] == 0
    return _run(["hostnamectl", "set-hostname", "--static", host], timeout=60)[0] == 0 and ok


# -- default apps ------------------------------------------------------------------------------------
def default_browser() -> str:
    rc, out = _run(["xdg-settings", "get", "default-web-browser"], timeout=5)
    return out.strip() if rc == 0 else ""


def set_default_browser(desktop_id: str) -> bool:
    return _run(["xdg-settings", "set", "default-web-browser", desktop_id], timeout=10)[0] == 0
