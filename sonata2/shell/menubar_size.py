"""The menu bar's height: 24 px (Big Sur), or 32 px like the menu bar of
the MacBooks with a notch (Settings > Menu Bar > "Taller menu bar", Vini).
Read once by each process that lays things out under it (menu bar,
notifications, desktop icons); Settings restarts the menu bar on a change."""
STANDARD_H, TALL_H = 24, 32


def height() -> int:
    from .. import config
    return TALL_H if config.load("topbar", {"tall": False}).get("tall") else STANDARD_H
