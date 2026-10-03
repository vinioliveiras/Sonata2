"""Apps on the discrete graphics card (laptops with two GPUs).

Some apps (GitHub Desktop, games, 3D tools) don't start, or run badly, on
the integrated GPU. GNOME calls it "Launch using Discrete Graphics Card";
in Sonata it's an option each app keeps: right-click it in the Dock or in
Launchpad -> "Use Discrete Graphics". Apps whose desktop entry asks for it
(PrefersNonDefaultGPU=true) get it on their own. Every Sonata launch goes
through apps.py, which adds the environment below when the app wants it.

The environment comes from switcheroo-control when it runs (it knows each
GPU's variables); otherwise NVIDIA's PRIME render offload variables, or
DRI_PRIME=1 for two Mesa GPUs."""
import glob
import os

from . import config

NAME = "gpu"
# desktop ids (without .desktop) the user put on the discrete GPU, or took
# off it -- an app whose entry asks for it (PrefersNonDefaultGPU) is on it
# unless the user said no
DEFAULTS = {"discrete": [], "integrated": [],
            # everyday apps (browsers, chat, office) drawn by the integrated GPU, which uses
            # the computer's memory: the NVIDIA card's stays for games (off until tested)
            "everyday_integrated": False,
            "light_effects": True}             # gamemode.LightEffects
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
    global _env
    if _env is None:
        sw = _switcheroo()
        if sw and sw[1]:
            _env = sw[1]
        elif "nvidia" in _cards():
            _env = dict(NVIDIA_ENV)
            if not os.path.exists(_env["__EGL_VENDOR_LIBRARY_FILENAMES"]):
                _env.pop("__EGL_VENDOR_LIBRARY_FILENAMES")
        else:
            _env = {"DRI_PRIME": "1"}
    return _env


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
        return bool(info.has_key("PrefersNonDefaultGPU") and info.get_boolean("PrefersNonDefaultGPU"))
    except Exception:
        return False


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
    """An urgent notification from "Sonata" (False when no server answered)."""
    try:
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Sonata", 0, icon, summary, body, [],
                                                       {"urgency": GLib.Variant("y", 2)}, -1)),
                      None, Gio.DBusCallFlags.NONE, 2000, None)
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


def everyday_integrated() -> bool:
    return bool(config.load(NAME, DEFAULTS).get("everyday_integrated"))


def set_everyday_integrated(on: bool) -> None:
    config.update(NAME, everyday_integrated=bool(on))


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


def launch_env(info) -> dict:
    """The GPU environment Sonata launches `info` with ({} = the default)."""
    if wants_discrete(info):
        return discrete_env()
    if everyday_integrated() and not heavy(info):
        return integrated_env()
    return {}


def menu_item(info, Item):
    """The checkmark item for right-click menus (None on one-GPU machines)."""
    if info is None or not has_dual_gpu():
        return None
    return Item("Use Discrete Graphics", lambda on: set_discrete(info, on), checked=wants_discrete(info))
