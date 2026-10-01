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
DEFAULTS = {"discrete": [], "integrated": []}
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
    config.save(NAME, {"discrete": discrete, "integrated": integrated})


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
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write("Sonata draws with the displays' GPU while this file exists.\n")
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


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


def menu_item(info, Item):
    """The checkmark item for right-click menus (None on one-GPU machines)."""
    if info is None or not has_dual_gpu():
        return None
    return Item("Use Discrete Graphics", lambda on: set_discrete(info, on), checked=wants_discrete(info))
