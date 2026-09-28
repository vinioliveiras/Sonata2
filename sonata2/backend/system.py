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

from gi.repository import GLib

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


def set_volume(percent: Optional[int] = None, muted: Optional[bool] = None) -> bool:
    ok = True
    if percent is not None:
        ok = _run(["wpctl", "set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@",
                   f"{max(0, min(100, int(percent)))}%"])[0] == 0
    if muted is not None:
        ok = _run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if muted else "0"])[0] == 0 and ok
    return ok


# -- display brightness ----------------------------------------------------------------
def brightness() -> Optional[int]:
    rc, out = _run(["brightnessctl", "-m", "-c", "backlight"], timeout=5)
    m = re.search(r",(\d+)%,", out) if rc == 0 else None
    return int(m.group(1)) if m else None


def set_brightness(percent: int) -> bool:
    percent = max(5, min(100, int(percent)))   # never fully black
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
    try:
        return any(_read(os.path.join(power_supply, x, "online")) == "1"
                   for x in os.listdir(power_supply) if x.startswith(("AC", "ADP", "ACAD")))
    except OSError:
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
