"""Tabs and windows from the menu bar, for any app (Vini: "New Tab" in the
menu bar like macOS -- File > New Tab, Window > Show Previous / Next Tab).

Apps keep their own tabs; the menu bar presses the app's own shortcut once
its menu has closed and the keyboard is back on the app (wl/vkeyboard: real
keycodes -- wtype's made-up ones reached Wayfire as Ctrl+Shift+Esc and
opened Task Manager). Which shortcuts depends on the kind of app: terminals use
Ctrl+Shift (Ctrl+T, Ctrl+W belong to the shell), the rest Ctrl.

    kind(app_id)            # "terminal" | "tabbed" | None (no tabs known)
    keys(app_id, "new_tab") # ("t", ("ctrl", "shift")) or None
    press(app_id, "new_tab")
"""
TERMINALS = ("terminal", "konsole", "kitty", "alacritty", "foot", "ghostty", "wezterm", "xterm", "tilix",
             "terminator", "kgx", "console", "ptyxis", "blackbox", "rio", "warp")
# terminals with no tabs of their own
NO_TABS = ("alacritty", "foot", "xterm", "rio")
# apps with tabs: browsers, file managers, editors (Sonata's Files, TextEdit, Terminal among them)
TABBED = ("firefox", "chrome", "chromium", "brave", "vivaldi", "microsoft-edge", "librewolf", "zen-", "opera",
          "floorp",
          "waterfox", "epiphany", "falkon", "sonata2.files", "nautilus", "dolphin", "thunar", "nemo",
          "textedit", "texteditor", "gedit", "kate", "sublime", "zed")

SHORTCUTS = {
    "terminal": {"new_window": ("n", ("ctrl", "shift")), "new_tab": ("t", ("ctrl", "shift")),
                 "close_tab": ("w", ("ctrl", "shift")),
                 "next_tab": ("Page_Down", ("ctrl",)), "prev_tab": ("Page_Up", ("ctrl",))},
    "tabbed": {"new_window": ("n", ("ctrl",)), "new_tab": ("t", ("ctrl",)), "close_tab": ("w", ("ctrl",)),
               "next_tab": ("Tab", ("ctrl",)), "prev_tab": ("Tab", ("ctrl", "shift"))},
    None: {"new_window": ("n", ("ctrl",))},
}


def is_terminal(app_id: str) -> bool:
    a = (app_id or "").lower()
    return any(t in a for t in TERMINALS)


def kind(app_id: str):
    """"terminal" (Ctrl+Shift shortcuts), "tabbed" (Ctrl), or None."""
    a = (app_id or "").lower()
    if is_terminal(a):
        return None if any(t in a for t in NO_TABS) else "terminal"
    flat = a.replace("-", "").replace("_", "")
    if a in ("zen", "app.zen_browser.zen") or any(t in a or t in flat for t in TABBED):
        return "tabbed"
    return None


def keys(app_id: str, action: str):
    """(key, modifiers) for an action in this app, or None."""
    k = kind(app_id)
    if k is None and is_terminal(app_id):
        return ("n", ("ctrl", "shift")) if action == "new_window" else None
    return SHORTCUTS[k].get(action)


def press(app_id: str, action: str) -> bool:
    """Press the app's shortcut for `action` (the app has the keyboard)."""
    combo = keys(app_id, action)
    if combo is None:
        return False
    from ..wl import vkeyboard
    return vkeyboard.press(combo[0], combo[1])
