"""Apps on the discrete graphics card (laptops with two GPUs).

Some apps (GitHub Desktop, games, 3D tools) don't start, or run badly, on
the integrated GPU. GNOME calls it "Launch using Discrete Graphics Card";
in Sonata it's an option each app keeps: right-click it in the Dock or in
Launchpad -> "Use High-Performance Graphics". Apps whose desktop entry asks for it
(PrefersNonDefaultGPU=true) get it on their own. Every Sonata launch goes
through apps.py, which adds the environment below when the app wants it.

Which card is the high-performance one: scan_cards() (switcheroo-control's
"Discrete" when it says, else each card's driver, address and memory). Its
environment: NVIDIA's PRIME render offload variables, or DRI_PRIME with the
card's address."""
import glob
import os
import re
from collections import namedtuple

from . import config

NAME = "gpu"
# desktop ids (without .desktop) the user put on the discrete GPU, or took
# off it -- an app whose entry asks for it (PrefersNonDefaultGPU) is on it
# unless the user said no
DEFAULTS = {"discrete": [], "integrated": [],
            # Smart Graphics Switching (Vini; on by default): each app without a choice of
            # its own gets the GPU that suits it -- games and creative apps the
            # high-performance card, everyday apps the GPU that draws the screens
            "smart": True,
            "everyday_integrated": False,      # (before Smart Graphics Switching; no longer read)
            "light_effects": True,
            # Settings > Displays > Graphics: a card's DRI_PRIME tag, "" = Sonata decides
            "games_gpu": "", "apps_gpu": ""}             # gamemode.LightEffects
NVIDIA_ENV = {"__NV_PRIME_RENDER_OFFLOAD": "1", "__GLX_VENDOR_LIBRARY_NAME": "nvidia",
              "__VK_LAYER_NV_optimus": "NVIDIA_only", "__EGL_VENDOR_LIBRARY_FILENAMES":
              "/usr/share/glvnd/egl_vendor.d/10_nvidia.json"}
_env = None


def _switcheroo():
    """(dual, env of the non-default GPU) from switcheroo-control, or None."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        props = bus.call_sync("net.hadess.SwitcherooControl", "/net/hadess/SwitcherooControl",
                              "org.freedesktop.DBus.Properties", "GetAll",
                              GLib.Variant("(s)", ("net.hadess.SwitcherooControl",)),
                              None, Gio.DBusCallFlags.NONE, 800, None).unpack()[0]
    except Exception:                                        # not installed / not running
        return None
    for g in props.get("GPUs", []):
        if not g.get("Default"):
            e = list(g.get("Environment", []))
            return bool(props.get("HasDualGpu")), dict(zip(e[::2], e[1::2]))
    return bool(props.get("HasDualGpu")), {}


def _cards() -> list:
    """Driver names of the display GPUs (/sys/class/drm/cardN)."""
    out = []
    for card in sorted(glob.glob("/sys/class/drm/card[0-9]")):
        drv = os.path.realpath(os.path.join(card, "device", "driver"))
        if os.path.isdir(drv):
            out.append(os.path.basename(drv))
    return out


_dual = None


def has_dual_gpu() -> bool:
    global _dual
    if _dual is None:
        sw = _switcheroo()
        _dual = bool(sw and sw[0]) or len(_cards()) > 1
    return _dual


def discrete_env() -> dict:
    """The environment that puts an app on the high-performance card."""
    if _env is not None:                                   # tests
        return _env
    c = high_performance()
    return env_for(c) if c else {"DRI_PRIME": "1"}


# -- which card is which --------------------------------------------------------------------------
# Not "the NVIDIA one" nor switcheroo's non-default GPU: with a MUX in dGPU
# mode that is the integrated one, and Steam's games went there (Vini). Each
# card is judged by itself, so an APU + Radeon, Intel + Arc or Intel + NVIDIA
# machine gets it right too; Settings > Displays > Graphics can force either.
Card = namedtuple("Card", "tag addr driver discrete vram")      # tag: DRI_PRIME's "pci-0000_01_00_0"
NVIDIA_DRIVERS = ("nvidia", "nouveau")
_card_list = None


def _read_int(path: str) -> int:
    try:
        return int(open_text(path) or "0", 0)
    except ValueError:
        return 0


def _switcheroo_discrete() -> dict:
    """{DRI_PRIME tag: discrete} from switcheroo-control (versions with "Discrete")."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        props = bus.call_sync("net.hadess.SwitcherooControl", "/net/hadess/SwitcherooControl",
                              "org.freedesktop.DBus.Properties", "GetAll",
                              GLib.Variant("(s)", ("net.hadess.SwitcherooControl",)),
                              None, Gio.DBusCallFlags.NONE, 800, None).unpack()[0]
    except Exception:
        return {}
    out = {}
    for g in props.get("GPUs", []):
        e = list(g.get("Environment", []))
        tag = dict(zip(e[::2], e[1::2])).get("DRI_PRIME", "")
        if tag and "Discrete" in g:
            out[tag] = bool(g["Discrete"])
    return out


def scan_cards(sys: str = "/sys", told=None) -> list:
    """The GPUs, in card order. told: switcheroo's {tag: discrete}, which wins.
    Otherwise: NVIDIA is discrete; Intel's integrated GPU is always 00:02.0
    (Arc is elsewhere); an AMD card is discrete beside an Intel iGPU, not
    beside NVIDIA, and of two AMD cards the APU has the smaller memory."""
    raw = []
    for card in sorted(glob.glob(os.path.join(sys, "class/drm/card[0-9]*"))):
        if not os.path.basename(card)[4:].isdigit():
            continue                                      # a connector (card1-HDMI-A-1)
        dev = os.path.realpath(os.path.join(card, "device"))
        drv = os.path.realpath(os.path.join(dev, "driver"))
        if not os.path.isdir(drv):
            continue
        addr = os.path.basename(dev)
        raw.append((addr, os.path.basename(drv), _read_int(os.path.join(dev, "mem_info_vram_total"))))
    told = _switcheroo_discrete() if told is None else told
    out = []
    for addr, drv, vram in raw:
        tag = "pci-" + addr.replace(":", "_").replace(".", "_")
        if tag in told:
            disc = told[tag]
        elif drv.startswith(NVIDIA_DRIVERS):
            disc = True
        elif drv in ("i915", "xe"):
            disc = not addr.endswith(":00:02.0")
        elif drv in ("amdgpu", "radeon"):
            amd = [v for a, d, v in raw if a != addr and d in ("amdgpu", "radeon")]
            if amd:
                disc = vram > max(amd)
            else:
                disc = any(d in ("i915", "xe") and a.endswith(":00:02.0") for a, d, _v in raw)
        else:
            disc = False
        out.append(Card(tag, addr, drv, disc, vram))
    return out


def cards() -> list:
    global _card_list
    if _card_list is None:
        _card_list = scan_cards()
    return _card_list


def _by_tag(tag):
    return next((c for c in cards() if c.tag == tag), None) if tag else None


def best_discrete(cs=None):
    """The most capable discrete card (NVIDIA first, then the most memory)."""
    disc = [c for c in (cards() if cs is None else cs) if c.discrete]
    return max(disc, key=lambda c: (c.driver.startswith(NVIDIA_DRIVERS), c.vram)) if disc else None


def high_performance():
    """The card games and creative apps get: the user's (Settings) or the best discrete one."""
    return _by_tag(config.load(NAME, DEFAULTS).get("games_gpu")) or best_discrete()


def everyday():
    """The card the user picked for everyday apps (Settings), None = Sonata decides."""
    return _by_tag(config.load(NAME, DEFAULTS).get("apps_gpu"))


def set_card(kind: str, tag: str) -> None:
    """kind: "games" or "apps"; tag "" = automatic."""
    config.update(NAME, **{f"{kind}_gpu": tag or ""})


def env_for(card) -> dict:
    """NVIDIA's offload variables, or DRI_PRIME with the card's own address
    (two AMD cards told apart); a Mesa card beside NVIDIA also needs Mesa's
    GL/EGL/Vulkan, or NVIDIA's libraries answer anyway."""
    if card.driver.startswith(NVIDIA_DRIVERS):
        env = dict(NVIDIA_ENV)
        if not os.path.exists(env["__EGL_VENDOR_LIBRARY_FILENAMES"]):
            env.pop("__EGL_VENDOR_LIBRARY_FILENAMES")
        return env
    env = integrated_env() if any(c.driver.startswith(NVIDIA_DRIVERS) for c in cards()) else {}
    env["DRI_PRIME"] = card.tag
    return env


def card_name(card) -> str:
    """"NVIDIA GeForce RTX 4060 ..." from lspci (Settings only)."""
    import subprocess
    from .backend.system import _gpu_name
    try:
        out = subprocess.run(["lspci", "-mm", "-s", card.addr], capture_output=True, text=True,
                             timeout=2).stdout
        f = re.findall(r'"([^"]*)"', out)
        if len(f) >= 3:
            return _gpu_name(f[1], f[2])
    except (OSError, subprocess.SubprocessError):
        pass
    return {"nvidia": "NVIDIA", "nouveau": "NVIDIA", "amdgpu": "AMD", "radeon": "AMD"}.get(
        card.driver, "Intel" if card.driver in ("i915", "xe") else card.driver) + " graphics"


def _key(info) -> str:
    did = info.get_id() or ""
    return did[:-8] if did.endswith(".desktop") else did


def wants_discrete(info) -> bool:
    cfg = config.load(NAME, DEFAULTS)
    if _key(info) in cfg.get("integrated", []):            # unchecked: the user's choice wins
        return False
    if _key(info) in cfg.get("discrete", []):
        return True
    try:
        if info.has_key("PrefersNonDefaultGPU") and info.get_boolean("PrefersNonDefaultGPU"):
            return True
    except Exception:
        pass
    return smart() and has_dual_gpu() and heavy(info)      # a game or a creative app


def smart() -> bool:
    """Smart Graphics Switching (Settings > Displays > Graphics), on by default."""
    return bool(config.load(NAME, DEFAULTS).get("smart", True))


def set_smart(on: bool) -> None:
    config.update(NAME, smart=bool(on))


def chosen(info) -> bool:
    """The user picked this app's GPU himself: Smart Graphics Switching leaves it alone."""
    cfg = config.load(NAME, DEFAULTS)
    return _key(info) in cfg.get("discrete", []) or _key(info) in cfg.get("integrated", [])


def set_smart_for(info, on: bool) -> None:
    """On: Sonata picks this app's GPU again (its own choice forgotten). Off:
    the GPU it gets now becomes its choice -- nothing changes until the user
    picks another one."""
    if on:
        cfg = config.load(NAME, DEFAULTS)
        key = _key(info)
        config.update(NAME, discrete=[k for k in cfg.get("discrete", []) if k != key],
                      integrated=[k for k in cfg.get("integrated", []) if k != key])
    else:
        set_discrete(info, wants_discrete(info))


def set_discrete(info, on: bool) -> None:
    """The user's choice for this app, kept either way (Steam asks for the
    discrete GPU in its entry: unchecking it used to come back checked)."""
    cfg = config.load(NAME, DEFAULTS)
    key = _key(info)
    discrete = [k for k in cfg.get("discrete", []) if k != key]
    integrated = [k for k in cfg.get("integrated", []) if k != key]
    (discrete if on else integrated).append(key)
    config.update(NAME, discrete=discrete, integrated=integrated)      # the other keys stay


def display_gpu_flag() -> str:
    """While this file exists, Wayfire draws with the GPU the displays are
    wired to (tools/sonata-session) instead of wlroots' choice."""
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                        "sonata2", "compositor-display-gpu")


def compositor_on_display_gpu() -> bool:
    return os.path.exists(display_gpu_flag())


def set_compositor_on_display_gpu(on: bool) -> None:
    """Settings > Displays > Graphics (from the next login). Off by default:
    on NVIDIA it crashed the session (its GBM refused buffers)."""
    path = display_gpu_flag()
    try:
        if on:
            try:
                os.remove(crash_marker())          # turned on again: the old crash is history
            except OSError:
                pass
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("Sonata draws with the displays' GPU while this file exists.\n")
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def notify(summary: str, body: str, icon: str = "video-display") -> bool:
    """An urgent notification from "Sonata" (False when no server is there).

    Sent without waiting for the answer: Sonata's notification server lives
    in the menu bar process, the one that calls this -- a call waiting for
    its own reply blocked the menu bar for 2 s, timed out and looked failed,
    so the notice was sent again and again (Vini: four copies of the same
    one at login, after a plain restart)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owned = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", ("org.freedesktop.Notifications",)),
                              GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        if not owned:
            return False                          # no server yet (login): tried again later
        bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications", "Notify",
                 GLib.Variant("(susssasa{sv}i)", ("Sonata", 0, icon, summary, body, [],
                                                  {"urgency": GLib.Variant("y", 2)}, -1)),
                 None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        return True
    except Exception:
        return False


def fallback_marker() -> str:
    """Left by tools/sonata-session when Wayfire couldn't start on the chosen
    GPU and fell back to wlroots' own choice (the last resort)."""
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                        "sonata2", "gpu-start-failed")


def fallback_notice() -> bool:
    """Once: Sonata is drawing with another graphics card this session.
    True when there was nothing to say or it was said."""
    path = fallback_marker()
    if not os.path.exists(path):
        return True
    if not notify("Sonata started with the other graphics card",
                  "It couldn't start on the card your displays use, so it is drawing with the other one "
                  "for this session. Log out and back in to try again; Feedbacker has the logs."):
        return False
    try:
        os.remove(path)
    except OSError:
        pass
    return True


def crash_marker() -> str:
    """Left by tools/sonata-session when Wayfire crashed on the displays' GPU."""
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"),
                        "sonata2", "display-gpu-crashed")


def crashed_at():
    """When a crash turned "Draw with the Displays' Graphics Card" off (the
    marker's time), None when it didn't. Settings says so next to the switch."""
    try:
        return os.path.getmtime(crash_marker())
    except OSError:
        return None


def crash_notice() -> bool:
    """Once after such a crash: tell the user the option was turned off. The
    marker stays (Settings shows why the switch is off) and is only marked
    as told once the notification really went out -- at login the
    notification server may not be up yet (Vini: the switch "turned itself
    off" with no word about it)."""
    path = crash_marker()
    try:
        with open(path, encoding="utf-8") as f:
            if "notified" in f.read():
                return False
    except OSError:
        return False
    if not notify("Graphics set back to the integrated card",
                  "The last session ended because the discrete card refused memory. "
                  "Sonata draws with the integrated card again (Settings > Displays > Graphics); "
                  "games still use the discrete card."):
        return False                              # not told yet: tried again later
    mtime = crashed_at()
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("notified\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))         # keep the crash's time
    except OSError:
        pass
    return True


# Electron apps whose native Wayland window never shows on hybrid laptops:
# their GPU process can't import the compositor's buffers (GitHub Desktop
# 3.4, Flatpak: "eglCreateImage failed with 0x3009", the GPU process
# restarting in a loop; the app stayed running with no window, so every
# later click did nothing). Under Xwayland they open fine.
X11_APPS = {"io.github.shiftey.Desktop", "github-desktop", "GitHub Desktop"}


def extra_env(info) -> dict:
    """Environment an app needs to open here, besides the GPU's."""
    if _key(info) in X11_APPS:
        return {"ELECTRON_OZONE_PLATFORM_HINT": "x11"}
    return {}


# Steam's own window (its web helper, CEF) never mapped here when CEF drew
# on the GPU: the client ran, the window was "created" and nothing showed
# (Vini, NVIDIA under Xwayland; also CachyOS-PKGBUILDS#1376). Drawn by the
# CPU it opens every time; games are not affected.
STEAM_APPS = {"steam", "steam-native", "steam-runtime", "com.valvesoftware.Steam"}
STEAM_ARGS = ["-cef-disable-gpu"]


def extra_args(info) -> list:
    """Arguments an app needs to open here (added to its Exec line)."""
    if _key(info) in STEAM_APPS and "nvidia" in _cards():
        return list(STEAM_ARGS)
    return []


def with_args(commandline: str, args) -> str:
    """`args` into an Exec line, before its first field code (%U, Flatpak's
    @@u) so they go to the app, not to the launcher; else at the end."""
    import shlex
    words = shlex.split(commandline)
    at = next((i for i, w in enumerate(words) if w.startswith("%") or w.startswith("@@")), len(words))
    return shlex.join(words[:at] + list(args) + words[at:]).replace("'%U'", "%U").replace("'%u'", "%u")


# -- everyday apps on the integrated GPU ----------------------------------------------------------
# NVIDIA's driver on Linux doesn't lend the computer's memory to the card when
# it's full (Windows does): a game using most of it left browsers starved and
# crawling (Vini). With this on, apps launched by Sonata that aren't games or
# graphics tools are drawn by the integrated GPU (AMD/Intel, Mesa), whose
# memory is the computer's; games and creative apps stay on the NVIDIA card.
HEAVY_CATEGORIES = {"Game", "Graphics", "3DGraphics", "RasterGraphics", "VectorGraphics", "Photography",
                    "VideoEditing", "Emulator", "Engineering"}
HEAVY_APPS = STEAM_APPS | {"com.heroicgameslauncher.hgl", "heroic", "net.lutris.Lutris", "lutris",
                           "com.usebottles.bottles", "org.prismlauncher.PrismLauncher", "obs",
                           "com.obsproject.Studio", "org.blender.Blender", "blender", "davinci-resolve"}
MESA_EGL = "/usr/share/glvnd/egl_vendor.d/50_mesa.json"


def heavy(info) -> bool:
    """A game or a graphics/video tool: it keeps the NVIDIA card."""
    if _key(info) in HEAVY_APPS:
        return True
    try:
        cats = set(filter(None, (info.get_categories() or "").split(";")))
    except Exception:
        cats = set()
    return bool(cats & HEAVY_CATEGORIES)


def integrated_env() -> dict:
    """Mesa (the integrated GPU) for GL/EGL/Vulkan, {} when there is none to use."""
    cards = _cards()
    if "nvidia" not in cards or not any(c in ("amdgpu", "i915", "xe", "radeon") for c in cards):
        return {}
    env = {"__GLX_VENDOR_LIBRARY_NAME": "mesa"}
    if os.path.exists(MESA_EGL):
        env["__EGL_VENDOR_LIBRARY_FILENAMES"] = MESA_EGL
    icds = sorted(glob.glob("/usr/share/vulkan/icd.d/radeon_icd*.json") +
                  glob.glob("/usr/share/vulkan/icd.d/intel_icd*.json"))
    if icds:
        env["VK_DRIVER_FILES"] = env["VK_ICD_FILENAMES"] = ":".join(icds)
    return env


INTEGRATED = ("amdgpu", "i915", "xe", "radeon")


def render_gpu() -> str:
    """Driver of the GPU Wayfire draws with: the first of WLR_DRM_DEVICES
    (tools/sonata-session picks it from where the screens are wired), else
    the boot GPU, as wlroots does; "" when unknown."""
    dev = (os.environ.get("WLR_DRM_DEVICES") or "").split(":")[0]
    if dev:
        card = os.path.join("/sys/class/drm", os.path.basename(os.path.realpath(dev)))
    else:
        card = next((c for c in sorted(glob.glob("/sys/class/drm/card[0-9]"))
                     if open_text(os.path.join(c, "device", "boot_vga")) == "1"), "")
    drv = os.path.realpath(os.path.join(card, "device", "driver")) if card else ""
    return os.path.basename(drv) if drv and os.path.isdir(drv) else ""


def open_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def launch_env(info) -> dict:
    """The GPU environment Sonata launches `info` with ({} = the default).
    With Smart Graphics Switching, everyday apps go to the integrated GPU
    only while Wayfire draws with it:
    with the screens on the NVIDIA card (a MUX in dGPU mode, or a session
    drawn by it), their frames would have to cross cards, and apps drawn
    through Xwayland (Spotify, CEF) opened empty (Vini)."""
    if wants_discrete(info):
        return discrete_env()
    if smart() and not heavy(info):
        picked = everyday()
        if picked:                                          # forced in Settings
            return env_for(picked)
        if render_gpu() in INTEGRATED:
            return integrated_env()
    return {}


def menu_items(info, Item) -> list:
    """The right-click menu section (empty on one-GPU machines): "Smart
    Graphics Switching" (checked while Sonata picks) and "Use
    High-Performance Graphics" -- greyed out while Sonata picks, showing
    its choice: unchecking it used to make the app's own choice by the way,
    and Smart came out unchecked as if by itself (Vini: Spotify). To choose
    yourself, uncheck Smart first."""
    if info is None or not has_dual_gpu():
        return []
    auto = smart() and not chosen(info)
    items = [Item("Use High-Performance Graphics", lambda on: set_discrete(info, on), checked=wants_discrete(info),
                  enabled=not auto)]
    if smart():
        items.insert(0, Item("Smart Graphics Switching", lambda on: set_smart_for(info, on), checked=auto))
    return items
