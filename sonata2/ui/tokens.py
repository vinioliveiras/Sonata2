"""Design tokens -- the single source of every colour, size, font and timing
used by Sonata's UI. Reference: macOS Big Sur/Monterey (Apple HIG semantic
colours and materials); never Liquid Glass.

Components never hard-code values: their CSS templates use `%(token)s`
placeholders filled from here (see theme.py), per appearance.
Names follow AppKit's semantic colours where one exists."""

# -- colours, per appearance -----------------------------------------------------
LIGHT = {
    # text
    "label": "rgba(0, 0, 0, 0.85)",
    "label_secondary": "rgba(0, 0, 0, 0.50)",
    "label_tertiary": "rgba(0, 0, 0, 0.26)",
    "label_on_accent": "#ffffff",
    # lines
    "separator": "rgba(0, 0, 0, 0.11)",
    "hairline": "rgba(0, 0, 0, 0.14)",          # 0.5 px outline of floating surfaces
    "highlight": "rgba(255, 255, 255, 0.60)",   # inner top/edge highlight
    # accents
    "accent": "#007aff",
    "accent_selected": "#0a64e1",               # selected menu row
    "accent_halo": "none",                      # (no longer used: accent_ink instead)
    "accent_ink": "#007aff",                    # accent text, icons and bars: readable on the background
    "destructive": "#ff3b30",
    # materials (backgrounds)
    "menu_bg": "rgba(236, 236, 236, 0.97)",     # menus, hover labels
    "window_bg": "#ececec",
    "control_bg": "#ffffff",                     # push buttons, pop-up buttons
    "control_pressed": "#e5e5e5",
    "control_off": "rgba(0, 0, 0, 0.09)",        # switch track off
    "knob": "#ffffff",
    "tl_disabled": "#d1d1d6",                    # greyed traffic light
    # light glass is more see-through than dark's: a pale tint at the same
    # alpha reads milky, almost opaque (Vini). Under 0.5 Wayfire's blur needs
    # a lower alpha_threshold: glass.blur_threshold counts these defaults.
    "glass_tint": "rgba(228, 228, 234, 0.45)",   # blurred by the compositor
    "solid_tint": "rgba(236, 236, 240, 0.78)",   # same surface without blur
    "indicator": "rgba(0, 0, 0, 0.62)",          # Dock running dot
    "bar_bg": "rgba(228, 228, 234, 0.45)",       # menu bar: the same glass as glass_tint
    # title bars Wayfire draws (Chrome, Spotify, X11 apps): the Dock's glass
    # windows' own glass (title bars, toolbars, sidebars): darker than the
    # Dock/menu bar glass, so windows read apart from the desktop (Vini);
    # a darker grey at a lower alpha: over window_bg it gives the same opaque
    # title bar as the old rgba(210, 210, 218, 0.74)
    "window_glass": "rgba(203, 203, 213, 0.58)",
    "titlebar_bg": "rgba(203, 203, 213, 0.58)",          # = window_glass
    "titlebar_bg_inactive": "rgba(224, 224, 230, 0.82)",
    "titlebar_text": "rgba(38, 38, 38, 1)",
    "titlebar_text_inactive": "rgba(154, 154, 154, 1)",
    "window_outline": "rgba(0, 0, 0, 0.18)",             # hairline of Wayfire-framed windows
    # Calculator (glass window; keys like macOS Ventura's)
    "calc_bg": "rgba(232, 232, 236, 0.80)",
    "calc_display": "rgba(0, 0, 0, 0.85)",
    "calc_key": "rgba(255, 255, 255, 0.72)",
    "calc_key_fn": "rgba(0, 0, 0, 0.10)",
    "calc_key_op": "#ff9f0a",
    "calc_key_text": "rgba(0, 0, 0, 0.85)",
    "term_bg": "#ffffff",                          # Terminal, macOS "Basic" profile
    "term_fg": "#000000",
    "bar_item_active": "rgba(0, 0, 0, 0.10)",    # open menu title / pressed extra
    "module_bg": "rgba(255, 255, 255, 0.55)",    # Control Center modules
    "module_track": "rgba(0, 0, 0, 0.10)",       # Control Center slider, empty part
    "module_fill": "#ffffff",                     # ... filled part and knob
    "module_button": "rgba(0, 0, 0, 0.08)",      # round buttons beside the sliders
    "toggle_off": "rgba(0, 0, 0, 0.10)",         # round module toggles
    "content_bg": "#ffffff",                     # document/list area of windows
    "pane_bg": "#fafafa",                        # Settings content pane (behind the cards)
    "sidebar_bg": "#ebebed",                     # source lists (Files, Settings), no blur
    "sidebar_selected": "rgba(0, 0, 0, 0.10)",   # selected source-list row
    "item_selected_bg": "rgba(0, 0, 0, 0.08)",   # backdrop behind a selected icon
    "tool_hover": "rgba(0, 0, 0, 0.06)",         # toolbar button hover
    "tool_icon": "rgba(0, 0, 0, 0.55)",          # toolbar glyphs
    "row_alt": "rgba(0, 0, 0, 0.035)",           # zebra stripe of list views
    # shadows
    "shadow_menu": "0 6px 18px rgba(0, 0, 0, 0.22)",
    "shadow_label": "0 2px 8px rgba(0, 0, 0, 0.18)",
    "shadow_plate": "0 6px 18px rgba(0, 0, 0, 0.14)",
    "shadow_control": "0 0.5px 1px rgba(0, 0, 0, 0.18)",
    "shadow_knob": "0 1px 2px rgba(0, 0, 0, 0.25)",
}

DARK = {
    "label": "rgba(255, 255, 255, 0.85)",
    "label_secondary": "rgba(255, 255, 255, 0.55)",
    "label_tertiary": "rgba(255, 255, 255, 0.25)",
    "label_on_accent": "#ffffff",
    "separator": "rgba(255, 255, 255, 0.12)",
    "hairline": "rgba(0, 0, 0, 0.55)",
    "highlight": "rgba(255, 255, 255, 0.15)",
    "accent": "#0a84ff",
    "accent_selected": "#0a84ff",
    "accent_halo": "none",
    "accent_ink": "#0a84ff",
    "destructive": "#ff453a",
    "menu_bg": "rgba(44, 44, 46, 0.97)",
    "window_bg": "#18181a",                   # darker than macOS' #1e1e1e (Vini)
    "control_bg": "rgba(255, 255, 255, 0.16)",
    "control_pressed": "rgba(255, 255, 255, 0.24)",
    "control_off": "rgba(255, 255, 255, 0.14)",
    "knob": "#d9d9d9",
    "tl_disabled": "#4a4a4e",
    "glass_tint": "rgba(16, 16, 20, 0.58)",
    "solid_tint": "rgba(30, 30, 34, 0.86)",
    "indicator": "rgba(255, 255, 255, 0.72)",
    "bar_bg": "rgba(16, 16, 20, 0.58)",           # = glass_tint
    "window_glass": "rgba(4, 4, 6, 0.76)",
    "titlebar_bg": "rgba(4, 4, 6, 0.76)",                # = window_glass
    "titlebar_bg_inactive": "rgba(16, 16, 18, 0.82)",
    "titlebar_text": "rgba(230, 230, 230, 1)",
    "titlebar_text_inactive": "rgba(138, 138, 138, 1)",
    "window_outline": "rgba(255, 255, 255, 0.14)",       # dark windows: a light edge, like macOS
    "calc_bg": "rgba(30, 30, 32, 0.72)",
    "calc_display": "#ffffff",
    "calc_key": "rgba(255, 255, 255, 0.20)",
    "calc_key_fn": "rgba(255, 255, 255, 0.10)",
    "calc_key_op": "#ff9f0a",
    "calc_key_text": "#ffffff",
    "term_bg": "#1e1e1e",
    "term_fg": "#e6e6e6",
    "bar_item_active": "rgba(255, 255, 255, 0.18)",
    "module_bg": "rgba(255, 255, 255, 0.08)",
    "module_track": "rgba(255, 255, 255, 0.12)",
    "module_fill": "rgba(255, 255, 255, 0.92)",
    "module_button": "rgba(255, 255, 255, 0.12)",
    "toggle_off": "rgba(255, 255, 255, 0.16)",
    "content_bg": "#151517",
    "pane_bg": "#19191b",
    "sidebar_bg": "#2a2a2c",
    "sidebar_selected": "rgba(255, 255, 255, 0.10)",
    "item_selected_bg": "rgba(255, 255, 255, 0.10)",
    "tool_hover": "rgba(255, 255, 255, 0.08)",
    "tool_icon": "rgba(255, 255, 255, 0.60)",
    "row_alt": "rgba(255, 255, 255, 0.04)",
    "shadow_menu": "0 6px 18px rgba(0, 0, 0, 0.40)",
    "shadow_label": "0 2px 8px rgba(0, 0, 0, 0.35)",
    "shadow_plate": "0 6px 18px rgba(0, 0, 0, 0.28)",
    "shadow_control": "0 0.5px 1px rgba(0, 0, 0, 0.40)",
    "shadow_knob": "0 1px 2px rgba(0, 0, 0, 0.45)",
}

# -- appearance-independent ------------------------------------------------------
# Window frame -- the one place for every window's corners and traffic
# lights. Everything else is derived from it: Sonata's own windows
# (ui/window.py), the title bars Wayfire draws (pixdecor and sonata-corners,
# set by tools/wayfire-config.sh at login), other GTK 4 apps (adwstyle.py)
# and the dot pictures (tools/gen-decor.py; run it after changing "dot" or the
# colours -- tests/test_regressions.py checks the files match).
# A theme (macOS now, Windows later) is a different FRAME.
FRAME = {
    # corners and shadow
    "radius": 10,                       # window corners, px
    # almost none (Vini; 24 px 33 %, then 14 px 20 %, were too strong)
    "shadow_radius": 3,                 # the shadow Wayfire draws around other apps' windows
    "shadow_color": "#0000001f",
    # Sonata's own windows (CSS box-shadow): focused / in the background
    "shadow": "0 1px 4px rgba(0, 0, 0, 0.14)",
    "shadow_backdrop": "0 1px 3px rgba(0, 0, 0, 0.08)",
    # window buttons
    "buttons": ("close", "minimize", "maximize"),   # from the window's edge inwards
    "buttons_side": "left",             # "left" (macOS) / "right" (Windows)
    "dot": 12,                          # button size (traffic light diameter)
    "dot_gap": 8,                       # space between two buttons
    "dot_left": 13,                     # first button's centre, from the window's side edge
    "dot_top": 14,                      # ... and from its top edge
    # title bar
    "title_h": 52,                      # Sonata's own title bars / unified toolbars
    "title_align": "center",            # "left" / "center" / "right"
    "title_font": "Inter Variable Semi-Bold 10",    # Pango: title bars Wayfire draws, GSettings
    # Wayfire's own title bars (when pixdecor isn't installed)
    "fallback_title_h": 28,
    "fallback_font": "Inter Semi-Bold",
}
TL_COLORS = {"close": "#ff5f57", "minimize": "#febc2e", "maximize": "#28c840", "restore": "#28c840"}


def button_layout(frame: dict = None) -> str:
    """GTK / GNOME "button-layout" (and pixdecor's): "close,minimize,maximize:"
    with the buttons on the left, ":minimize,maximize,close" on the right."""
    f = frame or FRAME
    names = list(f["buttons"])
    return ",".join(names) + ":" if f["buttons_side"] == "left" else ":" + ",".join(reversed(names))

SHARED = {
    # typography (SF Pro when installed, Inter as the open substitute)
    "font": '"SF Pro Text", "SF Pro", "Inter Variable", "Inter", "Cantarell", sans-serif',
    "font_display": '"SF Pro Display", "SF Pro", "Inter Display", "Inter Variable", "Inter", sans-serif',
    "font_mono": '"SF Mono", "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace',
    "text_small": "11px",
    "text_body": "13px",
    "text_title": "15px",
    # corner radii
    "r_button": "5px",
    "r_label": "6px",
    "r_menu": "7px",
    "r_menu_row": "4px",
    "r_window": f"{FRAME['radius']}px",
    "window_shadow": FRAME["shadow"],
    "window_shadow_backdrop": FRAME["shadow_backdrop"],
    "r_dialog": "12px",
    "r_plate": "18px",
    # sizes
    "control_h": "22px",       # push buttons, pop-up buttons, menu rows
    "switch_w": "32px",
    "switch_h": "18px",
    "menu_min_w": "190px",
    # full-screen overlays over the blurred desktop (Launchpad), same in
    # light and dark like macOS
    # alpha >= blur alpha_threshold (0.5, wayfire.ini) or Wayfire skips the
    # blur; a dark grey instead of black keeps the old ~28 % dimming
    "scrim": "rgba(64, 64, 70, 0.5)",
    "on_scrim": "#ffffff",
    "on_scrim_secondary": "rgba(255, 255, 255, 0.60)",
    "field_on_scrim": "rgba(255, 255, 255, 0.16)",
    "tile_on_scrim": "rgba(255, 255, 255, 0.20)",
    "folder_panel": "rgba(255, 255, 255, 0.16)",
    # timings
    "t_press": "80ms",
    "t_fast": "150ms",
    "t_standard": "250ms",
    "t_open": "180ms",                      # menus, panels, popovers appearing
    "ease_out": "cubic-bezier(0.2, 0.8, 0.2, 1)",
}

# Apple system colours (Settings sidebar badges, app accents). Same in
# light and dark, like the macOS System Settings icons.
SYSTEM_COLORS = {"blue": "#0a84ff", "green": "#30d158", "pink": "#ff375f", "orange": "#ff9f0a",
                 "red": "#ff453a", "purple": "#bf5af2", "teal": "#40c8e0", "indigo": "#5e5ce6",
                 "graphite": "#636366", "gray": "#8e8e93", "black": "#1c1c1e",
                 "yellow": "#ffd60a",           # the camera's selected mode (iOS)
                 "pure_black": "#000000"}       # behind a camera / video picture

# Accent colours (System Preferences > General > Accent colour, Big Sur):
# name -> (light, dark). "blue" is the default; the selected-row colour is
# derived from it.
ACCENTS = {
    "blue": ("#007aff", "#0a84ff"), "purple": ("#953d96", "#a550a7"), "pink": ("#f74f9e", "#f74f9e"),
    "red": ("#e0383e", "#ff453a"), "orange": ("#f7821b", "#ff9f0a"), "yellow": ("#fcb827", "#ffd60a"),
    "green": ("#62ba46", "#32d74b"), "graphite": ("#8c8c8c", "#98989d"),
}


def _darker(hex_color: str, f: float = 0.86) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))


def custom_accent(name) -> str:
    """A colour the user picked ("#rrggbb", Settings > Appearance > Accent
    colour > the custom dot), normalised; None for a named accent."""
    if not isinstance(name, str) or not name.startswith("#"):
        return None
    h = name.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        int(h, 16)
    except ValueError:
        return None
    return "#" + h.lower() if len(h) == 6 else None


def accent_hex(name, dark: bool = False) -> str:
    """The accent's colour, named or custom (blue when unknown)."""
    return custom_accent(name) or ACCENTS.get(name, ACCENTS["blue"])[1 if dark else 0]


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


DARK_BG = "#1e1e1e"
INK_MIN = 3.0                # accent icons, text and bars at least this contrast with a dark background


def lighter_until(hex_color: str, bg: str = DARK_BG, minimum: float = INK_MIN) -> str:
    """`hex_color` mixed with white just enough to reach `minimum` contrast
    with `bg` -- the same hue, lighter (black becomes a mid grey)."""
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    for step in range(0, 101, 2):
        t = step / 100
        c = "#%02x%02x%02x" % tuple(round(v + (255 - v) * t) for v in rgb)
        if contrast(c, bg) >= minimum:
            return c
    return "#ffffff"


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def accent_tokens(name: str, dark: bool) -> dict:
    """Token overrides for an accent colour (empty for the default blue).
    A very dark accent (black, Vini's) stays itself where it fills (selected
    rows, buttons); in Dark Mode its icons, text and progress bars use a
    lighter tone of it (accent_ink) -- Vini: no glow behind them any more."""
    c = custom_accent(name)
    if c is None:
        if name not in ACCENTS or name == "blue":
            return {}
        c = ACCENTS[name][1 if dark else 0]
    out = {"accent": c, "accent_selected": c if dark else _darker(c), "accent_ink": c}
    if dark and contrast(c, DARK_BG) < INK_MIN:
        out["accent_ink"] = lighter_until(c)
    return out


# Spacing scale (px) for layout code.
# Animation speed for everything Sonata draws (CSS transitions, GTK/Adw
# animations, stack cross-fades): durations are divided by it. 1.3 = 30 %
# faster than the designed timings (Vini). Wayfire's own window
# animations (config/wayfire.ini [animate]/[scale]) are set to match.
ANIMATION_SPEED = 1.3


def ms(duration: float) -> int:
    """A designed duration (ms) at Sonata's animation speed."""
    return max(1, round(duration / ANIMATION_SPEED)) if duration > 0 else 0


SPACE = {"xxs": 2, "xs": 4, "s": 6, "m": 8, "l": 12, "xl": 16, "xxl": 20}


# Visual themes. Only "mac" exists now; a "windows" theme (Windows 11 look,
# "Task Bar" mode) will add its own LIGHT/DARK/SHARED with the same keys, so
# components need no change -- only their token values differ.
THEMES = {"mac": {"light": LIGHT, "dark": DARK, "shared": SHARED}}


def wayfire_color(css: str, premultiplied: bool = False) -> str:
    """'rgba(r, g, b, a)' or '#rrggbb' -> Wayfire's '#rrggbbaa'. pixdecor
    blends its colours as premultiplied: pass premultiplied=True for it,
    or a translucent title bar comes out lighter than the same glass
    elsewhere (and a light one looks opaque)."""
    css = css.strip()
    if css.startswith("#"):
        return (css + "ff")[:9] if len(css) == 7 else css
    r, g, b, *a = [float(x) for x in css[css.index("(") + 1:css.index(")")].split(",")]
    a = a[0] if a else 1.0
    k = a if premultiplied else 1.0
    return "#%02x%02x%02x%02x" % (round(r * k), round(g * k), round(b * k), round(255 * a))


def over(css: str, base: str) -> str:
    """A translucent colour composited over an opaque one -> 'rgb(...)'."""
    def parts(c):
        c = c.strip()
        if c.startswith("#"):
            return [int(c[i:i + 2], 16) for i in (1, 3, 5)] + [1.0]
        v = [float(x) for x in c[c.index("(") + 1:c.index(")")].split(",")]
        return v[:3] + [v[3] if len(v) > 3 else 1.0]
    r, g, b, a = parts(css)
    br, bg, bb, _ = parts(base)
    return "rgb(%d, %d, %d)" % (round(r * a + br * (1 - a)), round(g * a + bg * (1 - a)), round(b * a + bb * (1 - a)))


# Title bars are opaque, like libadwaita apps' header bars (Vini, so every
# window's bar looks the same): the old glass as it showed over the window's
# own colour. Sonata's title bars and toolbars, the ones Wayfire draws
# (titlebars.apply_colors) and GNOME apps' (adwstyle.py) all read these.
# The glass itself stays as titlebar_glass / titlebar_glass_inactive: Settings >
# Appearance > "Glass title bars" (off by default) puts it back (ui/theme.py).
for _p in (LIGHT, DARK):
    for _k in ("titlebar_bg", "titlebar_bg_inactive"):
        _p[_k.replace("_bg", "_glass")] = _p[_k]
        _p[_k] = over(_p[_k], _p["window_bg"])


# Corner radii the user picks (Settings > Appearance > Corners; appearance.json
# "radius"): windows (their title bars too, Sonata's, Wayfire's and GNOME
# apps'), the Dock's plate, menus and panels. Small parts follow their
# group's ratio. Not the screen's corners (shell/screencorners.py).
RADIUS_DEFAULTS = {"window": FRAME["radius"], "dock": 18, "menu": 7}
RADIUS_RANGE = {"window": (0, 24), "dock": (0, 32), "menu": (0, 16)}
# window buttons' size (Settings > Appearance > Title Bars), px: FRAME["dot"] by default
BUTTON_SIZE_RANGE = (10, 20)
_radii_cache = {"mtime": None, "value": dict(RADIUS_DEFAULTS), "set": {}, "button": None}


def user_radii() -> dict:
    """The radii in effect (appearance.json; no GTK: wayfire-config.sh reads it too)."""
    import json
    import os
    try:
        from .. import config                  # (the same folder every Sonata setting uses)
        folder = config.CONFIG_DIR
    except ImportError:                        # loaded on its own (wayfire-config.sh)
        folder = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sonata2")
    path = os.path.join(folder, "appearance.json")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return dict(RADIUS_DEFAULTS, window=FRAME["radius"])     # (the theme's own: FRAME)
    if _radii_cache["mtime"] != mtime:
        out = dict(RADIUS_DEFAULTS, window=FRAME["radius"])
        got, button = {}, None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f) or {}
            got = data.get("radius") or {}
            for k, (lo, hi) in RADIUS_RANGE.items():
                if isinstance(got.get(k), (int, float)):
                    out[k] = int(max(lo, min(hi, round(got[k]))))
            b = data.get("button_size")
            if isinstance(b, (int, float)) and not isinstance(b, bool):
                button = int(max(BUTTON_SIZE_RANGE[0], min(BUTTON_SIZE_RANGE[1], round(b))))
        except (OSError, ValueError, AttributeError):
            pass
        _radii_cache.update(mtime=mtime, value=out, set=got if isinstance(got, dict) else {}, button=button)
    out = dict(_radii_cache["value"])
    if "window" not in _radii_cache.get("set", {}):
        out["window"] = FRAME["radius"]                         # not picked: the theme's own
    return out


def user_buttons_side() -> str:
    """Where the window buttons are: "left" (macOS, the default) or "right"
    (Settings > Appearance > Window buttons, Vini). No GTK: wayfire-config.sh
    reads it too."""
    import json
    import os
    try:
        from .. import config
        folder = config.CONFIG_DIR
    except ImportError:                        # loaded on its own (wayfire-config.sh, prefs.py)
        folder = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sonata2")
    try:
        with open(os.path.join(folder, "appearance.json"), encoding="utf-8") as f:
            side = (json.load(f) or {}).get("buttons_side")
    except (OSError, ValueError, AttributeError):
        side = None
    return side if side in ("left", "right") else FRAME["buttons_side"]


def user_button_size() -> int:
    """The window buttons' size in effect (appearance.json "button_size";
    FRAME["dot"] when not picked)."""
    user_radii()                               # (reads appearance.json, cached)
    import os
    try:
        from .. import config
        folder = config.CONFIG_DIR
    except ImportError:
        folder = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sonata2")
    if not os.path.exists(os.path.join(folder, "appearance.json")):
        return FRAME["dot"]
    return _radii_cache.get("button") or FRAME["dot"]


def corner_extra(radius: float, designed: float) -> float:
    """How much further in what sits by a corner goes when the corner is
    rounder than designed: half the extra radius (Vini: with a bigger
    radius the window buttons -- and the Dock's end icons -- stayed put and
    crowded the curve). Never closer than designed."""
    return max(0.0, (radius - designed) / 2)


def frame() -> dict:
    """FRAME with the user's window radius, buttons side and size. The
    buttons keep their room from the edges whatever their size (a bigger
    one grows inwards; the gap between them grows with it) and move in with
    a rounder corner (corner_extra)."""
    r = user_radii()["window"]
    size = user_button_size()
    move = corner_extra(r, FRAME["radius"]) + (size - FRAME["dot"]) / 2
    return dict(FRAME, radius=r, buttons_side=user_buttons_side(), dot=size,
                dot_gap=round(FRAME["dot_gap"] * size / FRAME["dot"]),
                dot_left=FRAME["dot_left"] + move, dot_top=FRAME["dot_top"] + move)


# a GTK header bar's own padding around the traffic lights (Sonata's theme)
HEADERBAR_PAD_X, HEADERBAR_PAD_Y = 5, 6


def traffic_tokens(f: dict) -> dict:
    """CSS values for the traffic lights (ui/window.py) from a frame():
    tokens, so a new window radius moves them with the rest of the theme."""
    half = f["dot"] / 2
    left = f["dot_left"] - half - f["dot_gap"] / 2          # the box's margin: the first button adds half a gap
    top = f["dot_top"] - half
    px = lambda v: f"{v:g}px"                                # noqa: E731
    return {"tl_dot": px(f["dot"]), "tl_half_gap": px(f["dot_gap"] / 2),
            "tl_margin_left": px(left), "tl_margin_top": px(top),
            "tl_hb_left": px(left - HEADERBAR_PAD_X), "tl_hb_top": px(top - HEADERBAR_PAD_Y)}


def radius_tokens(r: dict) -> dict:
    """The r_* tokens for these radii (the defaults give the designed values)."""
    def scaled(base, value, ratio):
        return f"{max(0, round(value * ratio / base)) if base else 0}px"
    w, d, m = r["window"], r["dock"], r["menu"]
    return {"r_window": f"{w}px", "r_button": scaled(10, w, 5), "r_plate": f"{d}px",
            "r_menu": f"{m}px", "r_menu_row": scaled(7, m, 4), "r_label": scaled(7, m, 6),
            "r_dialog": scaled(7, m, 12)}


# Background colour per appearance (Settings > Appearance, like the accent
# colour: Vini). A few to pick from -- the first is the theme's own -- or
# any colour. The window background takes it; the surfaces drawn around it
# (content, panes, sidebars, menus, title bars) keep their distance to it,
# so a tinted background tints them all alike.
def N_(text: str) -> str:
    """Marks a name for translation (i18n.N_; this file is also loaded on
    its own, outside the package -- tools/wayfire-config.sh)."""
    return text


# names shown translated: _(name)
BACKGROUNDS = {
    "light": ((N_("Default"), "#ececec"), (N_("Paper"), "#f5f3ee"), (N_("Sand"), "#efe8dc"),
              (N_("Mist"), "#e4e9f0"), (N_("Mint"), "#e3eee7"), (N_("Rose"), "#f1e5e8"),
              (N_("Lavender"), "#e9e5f2"), (N_("Butter"), "#f2eedb")),
    "dark": ((N_("Default"), "#18181a"), (N_("Black"), "#0b0b0c"), (N_("Graphite"), "#26262a"),
             (N_("Navy"), "#121826"), (N_("Forest"), "#121c16"), (N_("Plum"), "#1d1420"),
             (N_("Espresso"), "#1f1915"), (N_("Teal"), "#0f1e1f")),
}
# surfaces that follow the background (their own alpha, if any, is kept)
BACKGROUND_FOLLOWERS = ("window_bg", "content_bg", "pane_bg", "sidebar_bg", "menu_bg", "solid_tint")
# glass (Dock, menu bar, menus and panels, sidebars, glass title bars; Vini:
# "the colour changed only where it isn't see-through"): moved by the same
# amount *as seen* -- the shift over its alpha, since only that much of the
# tint shows over what is behind
BACKGROUND_GLASS = ("glass_tint", "bar_bg", "window_glass", "titlebar_glass", "titlebar_glass_inactive", "calc_bg")
# legible text: a light background stays this light, a dark one this dark (relative luminance)
LIGHT_BG_MIN, DARK_BG_MAX = 0.55, 0.045
_backgrounds_cache = {"mtime": None, "value": {}}


def _rgb(c: str):
    """'#rrggbb' / 'rgb(...)' / 'rgba(...)' -> ([r, g, b], alpha or None)."""
    c = c.strip()
    if c.startswith("#"):
        h = c.lstrip("#")
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)], None
    v = [float(x) for x in c[c.index("(") + 1:c.index(")")].split(",")]
    return [round(x) for x in v[:3]], (v[3] if len(v) > 3 else None)


def legible_background(hex_color: str, dark: bool) -> str:
    """`hex_color` moved towards white (light) or black (dark), just enough
    for the theme's text to stay readable on it."""
    c = custom_accent(hex_color)
    if c is None:
        return BACKGROUNDS["dark" if dark else "light"][0][1]
    rgb = _rgb(c)[0]
    target = 0 if dark else 255
    for step in range(0, 101, 2):
        t = step / 100
        out = "#%02x%02x%02x" % tuple(round(v + (target - v) * t) for v in rgb)
        lum = _luminance(out)
        if (dark and lum <= DARK_BG_MAX) or (not dark and lum >= LIGHT_BG_MIN):
            return out
    return "#000000" if dark else "#ffffff"


def user_backgrounds() -> dict:
    """{"light": "#rrggbb", "dark": ...} as picked (appearance.json
    "background"); a missing one is the theme's own. No GTK."""
    import json
    import os
    try:
        from .. import config
        folder = config.CONFIG_DIR
    except ImportError:
        folder = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "sonata2")
    path = os.path.join(folder, "appearance.json")
    try:
        mtime = os.stat(path).st_mtime
    except OSError:
        return {}
    if _backgrounds_cache["mtime"] != mtime:
        got = {}
        try:
            with open(path, encoding="utf-8") as f:
                raw = (json.load(f) or {}).get("background") or {}
            got = {k: custom_accent(v) for k, v in raw.items() if k in ("light", "dark") and custom_accent(v)}
        except (OSError, ValueError, AttributeError):
            pass
        _backgrounds_cache.update(mtime=mtime, value=got)
    return dict(_backgrounds_cache["value"])


def background_tokens(tokens: dict, picked: str, dark: bool) -> dict:
    """The tokens a picked background changes: the window's own colour, and
    every surface in BACKGROUND_FOLLOWERS moved by the same amount; the
    opaque title bars made again over it (their glass over the window)."""
    if not picked:
        return {}
    key = (dark, picked, id(tokens))
    if key not in _background_memo:            # (palette() runs often: drawing code asks for values())
        _background_memo[key] = _background_tokens(tokens, picked, dark)
    return dict(_background_memo[key])


_background_memo = {}


def _background_tokens(tokens: dict, picked: str, dark: bool) -> dict:
    new = _rgb(legible_background(picked, dark))[0]
    base = _rgb(tokens["window_bg"])[0]
    delta = [n - b for n, b in zip(new, base)]
    out = {}
    for k in BACKGROUND_FOLLOWERS + BACKGROUND_GLASS:
        if k not in tokens:
            continue
        rgb, a = _rgb(tokens[k])
        gain = 1 / max(a, 0.35) if k in BACKGROUND_GLASS and a else 1   # (clear glass: never a glare)
        moved = [max(0, min(255, round(v + d * gain))) for v, d in zip(rgb, delta)]
        out[k] = ("#%02x%02x%02x" % tuple(moved)) if a is None else "rgba(%d, %d, %d, %s)" % (*moved, a)
    for k in ("titlebar_bg", "titlebar_bg_inactive"):
        # the moved glass over the theme's window: as seen, the bar moved like the window
        out[k] = over(out.get(k.replace("_bg", "_glass")) or tokens[k.replace("_bg", "_glass")], tokens["window_bg"])
    return out


def palette(dark: bool, theme: str = "mac") -> dict:
    """All tokens for one theme and appearance."""
    t = THEMES.get(theme, THEMES["mac"])
    base = t["dark"] if dark else t["light"]
    return {**t["shared"], **{"sys_" + k: v for k, v in SYSTEM_COLORS.items()}, **base,
            **background_tokens(base, user_backgrounds().get("dark" if dark else "light"), dark),
            **radius_tokens(user_radii()), **traffic_tokens(frame())}
