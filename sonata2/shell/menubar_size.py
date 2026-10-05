"""The menu bar's height: 32 px like the menu bar of the MacBooks with a
notch (the default, Vini), or 24 px (Big Sur) -- Settings > Menu Bar >
"Taller menu bar". Read once by each process that lays things out under it (menu bar,
notifications, desktop icons); Settings restarts the menu bar on a change."""
STANDARD_H, TALL_H = 24, 32


def height() -> int:
    from .. import config
    return TALL_H if config.load("topbar", {"tall": True}).get("tall", True) else STANDARD_H
