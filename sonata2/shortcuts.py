"""Keyboard shortcuts Settings can show and change (Settings > Keyboard
Shortcuts). They are Wayfire options -- key bindings of the command,
wm-actions, grid, scale, expo, vswitch and core plugins -- so changing one
is a write to Sonata's Wayfire overrides (wfconfig.py), applied at once.

A binding is Wayfire's activator text: alternatives split by "|", each
"<super> <shift> KEY_S", a modifier alone ("<super>": pressed and released)
or a gesture ("swipe up 3"). No GTK here: settings/shortcuts_page.py draws it.

    for s in SHORTCUTS: s.title, current(s), default(s)
    set_binding(s, ["<super> KEY_D"])
    combo_from_key(keycode, ctrl, shift, alt, super_)  -> "<ctrl> <super> KEY_D"
"""
import os
from dataclasses import dataclass

from . import names, wfconfig

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODS = ("<ctrl>", "<alt>", "<shift>", "<super>")          # Wayfire's order when written
MOD_LABEL = {"<ctrl>": "Ctrl", "<alt>": "Alt", "<shift>": "Shift", "<super>": "Super"}
# (GTK accelerator modifier for Gtk.ShortcutLabel)
MOD_ACCEL = {"<ctrl>": "<Control>", "<alt>": "<Alt>", "<shift>": "<Shift>", "<super>": "<Super>"}


@dataclass(frozen=True)
class Shortcut:
    id: str
    title: str
    group: str
    section: str                 # Wayfire plugin section
    key: str                     # its option


SHORTCUTS = [
    Shortcut("apps", f"Open {names.APPS}", "Apps and Search", "command", "binding_launchpad"),
    Shortcut("search", names.SEARCH, "Apps and Search", "command", "binding_spotlight"),
    Shortcut("files", "Open Files", "Apps and Search", "command", "binding_files"),
    Shortcut("settings", "Open Settings", "Apps and Search", "command", "binding_settings"),
    Shortcut("activity", "Open Task Manager", "Apps and Search", "command", "binding_activity"),
    Shortcut("terminal", "Open a Terminal", "Apps and Search", "command", "binding_terminal"),
    Shortcut("emoji", "Emoji and Symbols", "Apps and Search", "command", "binding_emoji"),
    Shortcut("input", "Previous Input Source", "Apps and Search", "command", "binding_input"),
    Shortcut("clipboard", "Clipboard History", "Apps and Search", "command", "binding_clipboard"),
    Shortcut("close", "Close Window", "Windows", "core", "close_top_view"),
    Shortcut("minimize", "Minimize", "Windows", "wm-actions", "minimize"),
    Shortcut("fullscreen", "Full Screen", "Windows", "command", "binding_fullscreen"),
    Shortcut("maximize", "Maximize", "Windows", "grid", "slot_c"),
    Shortcut("restore", "Restore Size", "Windows", "grid", "restore"),
    Shortcut("snap_left", "Snap to the Left Half", "Windows", "grid", "slot_l"),
    Shortcut("snap_right", "Snap to the Right Half", "Windows", "grid", "slot_r"),
    Shortcut("desktop", "Show Desktop", "Windows", "command", "binding_showdesktop"),
    Shortcut("switcher", "Switch Apps", "Windows", "command", "binding_switcher"),
    Shortcut("switcher_back", "Switch Apps Backwards", "Windows", "command", "binding_switcher_back"),
    Shortcut("overview", names.OVERVIEW, "Spaces", "scale", "toggle"),
    Shortcut("overview_all", f"{names.OVERVIEW}: All Spaces", "Spaces", "scale", "toggle_all"),
    Shortcut("spaces", "Show All Spaces", "Spaces", "expo", "toggle"),
    Shortcut("space_left", "Previous Space", "Spaces", "vswitch", "binding_left"),
    Shortcut("space_right", "Next Space", "Spaces", "vswitch", "binding_right"),
    Shortcut("shot", "Screenshot", "Screenshots", "command", "binding_screenshot"),
    Shortcut("shot_area", "Screenshot of a Selection", "Screenshots", "command", "binding_screenshot_area"),
    Shortcut("draw", "Draw on the Screen", "Screenshots", "command", "binding_draw"),
    Shortcut("capture", "Screenshot and Recording Options", "Screenshots", "command", "binding_capture"),
    Shortcut("lock", "Lock Screen", "System", "command", "binding_lock"),
]
GROUPS = ["Apps and Search", "Windows", "Spaces", "Screenshots", "System"]


def _ini_value(path, section, key):
    try:
        with open(path, encoding="utf-8") as f:
            cur = None
            for line in f:
                s = line.strip()
                if s.startswith("[") and s.endswith("]"):
                    cur = s[1:-1]
                elif cur == section and "=" in s and not s.startswith("#"):
                    k, v = s.split("=", 1)
                    if k.strip() == key:
                        return v.strip()
    except OSError:
        pass
    return None


def default(s: Shortcut) -> str:
    """Sonata's own binding (config/wayfire.ini)."""
    return _ini_value(os.path.join(REPO, "config", "wayfire.ini"), s.section, s.key) or ""


def current(s: Shortcut) -> str:
    return wfconfig.wayfire_get(s.section, s.key, default(s))


def split(binding: str) -> list:
    """"<super> KEY_Q | <alt> KEY_F4" -> ["<super> KEY_Q", "<alt> KEY_F4"]."""
    return [" ".join(p.split()) for p in (binding or "").split("|") if p.strip()]


def join(combos) -> str:
    return " | ".join(c for c in combos if c)


def set_binding(s: Shortcut, combos) -> None:
    wfconfig.wayfire_set(s.section, s.key, join(combos))


def reset(s: Shortcut) -> None:
    wfconfig.wayfire_set(s.section, s.key, default(s))


def owner(combo: str, but: Shortcut = None):
    """The shortcut already using this combo, if any."""
    for s in SHORTCUTS:
        if s != but and combo in split(current(s)):
            return s
    return None


def combo_from_key(keycode: int, ctrl=False, shift=False, alt=False, super_=False):
    """Wayfire text for a key pressed with modifiers (keycode: the X / GDK
    hardware keycode, evdev + 8); None for a modifier key alone."""
    name = EVDEV.get(keycode - 8)
    if name is None or name in MODIFIER_KEYS:
        return None
    mods = [m for m, on in zip(MODS, (ctrl, alt, shift, super_)) if on]
    return " ".join(mods + ["KEY_" + name])


def describe(combo: str) -> str:
    """Words for one combo: "Super+Shift+S", "Super", "Swipe up with 3 fingers"."""
    parts = combo.split()
    if parts and parts[0] in ("swipe", "pinch", "edge-swipe"):
        how = {"swipe": "Swipe", "pinch": "Pinch", "edge-swipe": "Swipe from the edge"}[parts[0]]
        rest = parts[1:]
        fingers = rest[-1] if rest and rest[-1].isdigit() else ""
        way = " ".join(rest[:-1] if fingers else rest)
        return f"{how} {way} with {fingers} fingers".replace("  ", " ").strip()
    words = [MOD_LABEL.get(p, "") for p in parts if p in MOD_LABEL]
    keys = [KEY_LABEL.get(p[4:], p[4:].title()) for p in parts if p.startswith("KEY_")]
    return "+".join(words + keys)


def accelerator(combo: str):
    """GTK accelerator ("<Super>d") for Gtk.ShortcutLabel, or None (gestures,
    a modifier alone, keys GTK has no name for)."""
    parts = combo.split()
    keys = [p[4:] for p in parts if p.startswith("KEY_")]
    if len(keys) != 1 or any(not (p in MOD_ACCEL or p.startswith("KEY_")) for p in parts):
        return None
    k = keys[0]
    name = GTK_KEY.get(k) or (k.lower() if len(k) == 1 else k if k.startswith("F") and k[1:].isdigit() else None)
    if name is None:
        return None
    return "".join(MOD_ACCEL[p] for p in parts if p in MOD_ACCEL) + name


MODIFIER_KEYS = {"LEFTCTRL", "RIGHTCTRL", "LEFTSHIFT", "RIGHTSHIFT", "LEFTALT", "RIGHTALT", "LEFTMETA",
                 "RIGHTMETA", "CAPSLOCK", "NUMLOCK", "SCROLLLOCK", "COMPOSE"}
KEY_LABEL = {"LEFT": "←", "RIGHT": "→", "UP": "↑", "DOWN": "↓", "ESC": "Esc", "DOT": ".", "COMMA": ",",
             "SPACE": "Space", "TAB": "Tab", "ENTER": "Return", "BACKSPACE": "Backspace", "DELETE": "Delete",
             "SYSRQ": "Print Screen", "PRINT": "Print Screen", "MINUS": "-", "EQUAL": "=", "SLASH": "/",
             "SEMICOLON": ";", "APOSTROPHE": "'", "GRAVE": "`", "BACKSLASH": "\\", "LEFTBRACE": "[",
             "RIGHTBRACE": "]", "PAGEUP": "Page Up", "PAGEDOWN": "Page Down", "DASHBOARD": "Dashboard key"}
GTK_KEY = {"LEFT": "Left", "RIGHT": "Right", "UP": "Up", "DOWN": "Down", "ESC": "Escape", "DOT": "period",
           "COMMA": "comma", "SPACE": "space", "TAB": "Tab", "ENTER": "Return", "BACKSPACE": "BackSpace",
           "DELETE": "Delete", "SYSRQ": "Print", "PRINT": "Print", "MINUS": "minus", "EQUAL": "equal",
           "SLASH": "slash", "SEMICOLON": "semicolon", "APOSTROPHE": "apostrophe", "GRAVE": "grave",
           "BACKSLASH": "backslash", "LEFTBRACE": "bracketleft", "RIGHTBRACE": "bracketright",
           "PAGEUP": "Page_Up", "PAGEDOWN": "Page_Down", "HOME": "Home", "END": "End", "INSERT": "Insert"}

# evdev key codes (linux/input-event-codes.h), code -> name without KEY_
EVDEV = {
    1: "ESC", 2: "1", 3: "2", 4: "3", 5: "4", 6: "5", 7: "6", 8: "7", 9: "8", 10: "9", 11: "0", 12: "MINUS",
    13: "EQUAL", 14: "BACKSPACE", 15: "TAB", 16: "Q", 17: "W", 18: "E", 19: "R", 20: "T", 21: "Y", 22: "U",
    23: "I", 24: "O", 25: "P", 26: "LEFTBRACE", 27: "RIGHTBRACE", 28: "ENTER", 29: "LEFTCTRL", 30: "A",
    31: "S", 32: "D", 33: "F", 34: "G", 35: "H", 36: "J", 37: "K", 38: "L", 39: "SEMICOLON", 40: "APOSTROPHE",
    41: "GRAVE", 42: "LEFTSHIFT", 43: "BACKSLASH", 44: "Z", 45: "X", 46: "C", 47: "V", 48: "B", 49: "N",
    50: "M", 51: "COMMA", 52: "DOT", 53: "SLASH", 54: "RIGHTSHIFT", 55: "KPASTERISK", 56: "LEFTALT",
    57: "SPACE", 58: "CAPSLOCK", 59: "F1", 60: "F2", 61: "F3", 62: "F4", 63: "F5", 64: "F6", 65: "F7",
    66: "F8", 67: "F9", 68: "F10", 69: "NUMLOCK", 70: "SCROLLLOCK", 71: "KP7", 72: "KP8", 73: "KP9",
    74: "KPMINUS", 75: "KP4", 76: "KP5", 77: "KP6", 78: "KPPLUS", 79: "KP1", 80: "KP2", 81: "KP3", 82: "KP0",
    83: "KPDOT", 85: "ZENKAKUHANKAKU", 86: "102ND", 87: "F11", 88: "F12", 89: "RO", 90: "KATAKANA",
    91: "HIRAGANA", 92: "HENKAN", 93: "KATAKANAHIRAGANA", 94: "MUHENKAN", 95: "KPJPCOMMA", 96: "KPENTER",
    97: "RIGHTCTRL", 98: "KPSLASH", 99: "SYSRQ", 100: "RIGHTALT", 101: "LINEFEED", 102: "HOME", 103: "UP",
    104: "PAGEUP", 105: "LEFT", 106: "RIGHT", 107: "END", 108: "DOWN", 109: "PAGEDOWN", 110: "INSERT",
    111: "DELETE", 112: "MACRO", 113: "MUTE", 114: "VOLUMEDOWN", 115: "VOLUMEUP", 116: "POWER", 117: "KPEQUAL",
    118: "KPPLUSMINUS", 119: "PAUSE", 120: "SCALE", 121: "KPCOMMA", 122: "HANGEUL", 123: "HANJA", 124: "YEN",
    125: "LEFTMETA", 126: "RIGHTMETA", 127: "COMPOSE", 128: "STOP", 129: "AGAIN", 130: "PROPS", 131: "UNDO",
    132: "FRONT", 133: "COPY", 134: "OPEN", 135: "PASTE", 136: "FIND", 137: "CUT", 138: "HELP", 139: "MENU",
    140: "CALC", 141: "SETUP", 142: "SLEEP", 143: "WAKEUP", 144: "FILE", 145: "SENDFILE", 146: "DELETEFILE",
    147: "XFER", 148: "PROG1", 149: "PROG2", 150: "WWW", 151: "MSDOS", 152: "COFFEE", 153: "ROTATE_DISPLAY",
    154: "CYCLEWINDOWS", 155: "MAIL", 156: "BOOKMARKS", 157: "COMPUTER", 158: "BACK", 159: "FORWARD",
    160: "CLOSECD", 161: "EJECTCD", 162: "EJECTCLOSECD", 163: "NEXTSONG", 164: "PLAYPAUSE",
    165: "PREVIOUSSONG", 166: "STOPCD", 167: "RECORD", 168: "REWIND", 169: "PHONE", 170: "ISO", 171: "CONFIG",
    172: "HOMEPAGE", 173: "REFRESH", 174: "EXIT", 175: "MOVE", 176: "EDIT", 177: "SCROLLUP", 178: "SCROLLDOWN",
    179: "KPLEFTPAREN", 180: "KPRIGHTPAREN", 181: "NEW", 182: "REDO", 183: "F13", 184: "F14", 185: "F15",
    186: "F16", 187: "F17", 188: "F18", 189: "F19", 190: "F20", 191: "F21", 192: "F22", 193: "F23", 194: "F24",
    200: "PLAYCD", 201: "PAUSECD", 202: "PROG3", 203: "PROG4", 204: "ALL_APPLICATIONS", 205: "SUSPEND",
    206: "CLOSE", 207: "PLAY", 208: "FASTFORWARD", 209: "BASSBOOST", 210: "PRINT", 211: "HP", 212: "CAMERA",
    213: "SOUND", 214: "QUESTION", 215: "EMAIL", 216: "CHAT", 217: "SEARCH", 218: "CONNECT", 219: "FINANCE",
    220: "SPORT", 221: "SHOP", 222: "ALTERASE", 223: "CANCEL", 224: "BRIGHTNESSDOWN", 225: "BRIGHTNESSUP",
    226: "MEDIA", 227: "SWITCHVIDEOMODE", 228: "KBDILLUMTOGGLE", 229: "KBDILLUMDOWN", 230: "KBDILLUMUP",
    231: "SEND", 232: "REPLY", 233: "FORWARDMAIL", 234: "SAVE", 235: "DOCUMENTS", 236: "BATTERY",
    237: "BLUETOOTH", 238: "WLAN", 239: "UWB", 240: "UNKNOWN", 241: "VIDEO_NEXT", 242: "VIDEO_PREV",
    243: "BRIGHTNESS_CYCLE", 244: "BRIGHTNESS_AUTO", 245: "DISPLAY_OFF", 246: "WWAN", 247: "RFKILL",
    248: "MICMUTE",
}
