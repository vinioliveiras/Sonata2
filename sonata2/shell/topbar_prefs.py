"""The menu bar's settings and what Settings reads of them, without the
menu bar itself: Settings > Menu Bar and Date & Time imported the whole
topbar module (its widgets, styles, Control Center...) only for these."""
import re

from ..backend import stats as _stats

DEFAULTS = {"battery_percent": False, "clock_format": "%a %-d %b  %H:%M", "show_bluetooth": True,
            "show_sound": False, "show_now_playing": False, "show_tray": True,
            # every status item can be taken out of the menu bar (Settings > Menu Bar, Vini);
            # Control Center and the clock always stay, like macOS
            "show_wifi": True, "show_battery": True, "show_spotlight": True, "show_input": True,
            # performance figures (statsui.py), off by default; each as "text" or "graph"
            "show_cpu": False, "show_gpu": False, "show_ram": False, "show_net": False, "show_fps": False,
            "cpu_style": "text", "gpu_style": "text", "ram_style": "text", "net_style": "text",
            "fps_style": "text",
            "autohide": False,                  # Settings > Menu Bar: hide it like the Dock (Vini)
            "tall": True}                       # the taller menu bar of new MacBooks (menubar_size.py; on by default, Vini)
# a key per graphics card when there are two or more (stats.KINDS: gpu_amd, gpu_nvidia...)
for _k in _stats.KINDS:
    DEFAULTS.setdefault(f"show_{_k}", False)
    DEFAULTS.setdefault(f"{_k}_style", "text")


def shows_date(fmt: str) -> bool:
    """The clock format shows the date (%-d, %d, %e: the day of the month).
    Vini: Settings' "Show the date" looked for "%d" only and stayed off
    with the default "%-d"."""
    return bool(re.search(r"%[-_0^#]?[de]", fmt or ""))


def is_24h(fmt: str) -> bool:
    """The clock format is 24-hour (%H, %-H, %k)."""
    return bool(re.search(r"%[-_0^#]?[Hk]", fmt or ""))
