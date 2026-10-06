"""Keys pressed for the app in front (virtual-keyboard-unstable-v1): the
menu bar's New Tab / New Window (shell/apptabs.py), the game controller's
keys.

Not `wtype`: it makes up its own keymap and gives the first key it types
keycode 1 -- Esc to Wayfire, which matches its shortcuts by keycode, so the
menu bar's Ctrl+Shift+T reached Wayfire as Ctrl+Shift+Esc and opened Task
Manager (Vini). Here the keys go out with their real keycodes (evdev) and a
plain US keymap: Wayfire and the app read the same key.

    press("t", ("ctrl", "shift"))     # False: no virtual keyboard here
"""
import os
import sys
import time

# evdev keycodes (linux/input-event-codes.h)
CODES = {"Escape": 1, "BackSpace": 14, "Tab": 15, "Return": 28, "space": 57, "Delete": 111,
         "Home": 102, "Up": 103, "Page_Up": 104, "Left": 105, "Right": 106, "End": 107, "Down": 108,
         "Page_Down": 109, "minus": 12, "equal": 13, "bracketleft": 26, "bracketright": 27}
CODES.update({c: k for c, k in zip("1234567890", range(2, 12))})
CODES.update({c: k for c, k in zip("qwertyuiop", range(16, 26))})
CODES.update({c: k for c, k in zip("asdfghjkl", range(30, 39))})
CODES.update({c: k for c, k in zip("zxcvbnm", range(44, 51))})
CODES.update({f"F{i}": 58 + i for i in range(1, 11)})
# modifier: (its key, its bit in the US keymap's modifier mask)
MODS = {"shift": (42, 1), "ctrl": (29, 4), "alt": (56, 8), "super": (125, 64)}
KEYMAP = ('xkb_keymap {\n  xkb_keycodes { include "evdev+aliases(qwerty)" };\n'
          '  xkb_types { include "complete" };\n  xkb_compat { include "complete" };\n'
          '  xkb_symbols { include "pc+us+inet(evdev)" };\n};\n')
PRESSED, RELEASED = 1, 0


def _ms() -> int:
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


def press(key: str, mods=()) -> bool:
    """Press and release `key` with `mods` held. One short Wayland connection."""
    code = CODES.get(key)
    if code is None or any(m not in MODS for m in mods):
        return False
    try:
        from pywayland.client import Display
        from pywayland.protocol.wayland import WlSeat
        from . import scan
        mod = scan.load("virtual-keyboard-unstable-v1.xml", "virtual_keyboard_unstable_v1")
        Manager = mod.ZwpVirtualKeyboardManagerV1
    except Exception as e:
        print(f"sonata2: no virtual keyboard ({e})", file=sys.stderr, flush=True)
        return False
    display = Display()
    try:
        display.connect()
        found = {}

        def global_(reg, name, iface, version):
            if iface == Manager.name:
                found["mgr"] = reg.bind(name, Manager, 1)
            elif iface == "wl_seat" and "seat" not in found:
                found["seat"] = reg.bind(name, WlSeat, 1)
        registry = display.get_registry()
        registry.dispatcher["global"] = global_
        display.roundtrip()
        if "mgr" not in found or "seat" not in found:
            return False
        kb = found["mgr"].create_virtual_keyboard(found["seat"])
        data = KEYMAP.encode() + b"\0"
        fd = os.memfd_create("sonata2-keymap", os.MFD_CLOEXEC)
        try:
            os.write(fd, data)
            kb.keymap(1, fd, len(data))                        # 1: XKB_V1
        finally:
            os.close(fd)
        mask = 0
        for m in mods:                                          # modifiers down, in order
            mask |= MODS[m][1]
            kb.key(_ms(), MODS[m][0], PRESSED)
            kb.modifiers(mask, 0, 0, 0)
        kb.key(_ms(), code, PRESSED)
        kb.key(_ms(), code, RELEASED)
        for m in reversed(mods):
            mask &= ~MODS[m][1]
            kb.key(_ms(), MODS[m][0], RELEASED)
            kb.modifiers(mask, 0, 0, 0)
        display.roundtrip()
        kb.destroy()
        display.roundtrip()
        return True
    except Exception as e:
        print(f"sonata2: virtual keyboard: {e}", file=sys.stderr, flush=True)
        return False
    finally:
        try:
            display.disconnect()
        except Exception:
            pass
