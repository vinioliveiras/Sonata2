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
    "destructive": "#ff3b30",
    # materials (backgrounds)
    "menu_bg": "rgba(236, 236, 236, 0.97)",     # menus, hover labels
    "window_bg": "#ececec",
    "control_bg": "#ffffff",                     # push buttons, pop-up buttons
    "control_pressed": "#e5e5e5",
    "control_off": "rgba(0, 0, 0, 0.09)",        # switch track off
    "knob": "#ffffff",
    "tl_disabled": "#d1d1d6",                    # greyed traffic light
    "glass_tint": "rgba(228, 228, 234, 0.70)",   # blurred by the compositor
    "solid_tint": "rgba(236, 236, 240, 0.78)",   # same surface without blur
    "indicator": "rgba(0, 0, 0, 0.62)",          # Dock running dot
    "bar_bg": "rgba(228, 228, 234, 0.70)",       # menu bar: the same glass as glass_tint
    # title bars Wayfire draws (Chrome, Spotify, X11 apps): the Dock's glass
    "titlebar_bg": "rgba(228, 228, 234, 0.70)",          # = glass_tint
    "titlebar_bg_inactive": "rgba(240, 240, 243, 0.82)",
    "titlebar_text": "rgba(38, 38, 38, 1)",
    "titlebar_text_inactive": "rgba(154, 154, 154, 1)",
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
    "destructive": "#ff453a",
    "menu_bg": "rgba(44, 44, 46, 0.97)",
    "window_bg": "#1e1e1e",
    "control_bg": "rgba(255, 255, 255, 0.16)",
    "control_pressed": "rgba(255, 255, 255, 0.24)",
    "control_off": "rgba(255, 255, 255, 0.14)",
    "knob": "#d9d9d9",
    "tl_disabled": "#4a4a4e",
    "glass_tint": "rgba(16, 16, 20, 0.80)",
    "solid_tint": "rgba(30, 30, 34, 0.86)",
    "indicator": "rgba(255, 255, 255, 0.72)",
    "bar_bg": "rgba(16, 16, 20, 0.80)",           # = glass_tint
    "titlebar_bg": "rgba(16, 16, 20, 0.80)",
    "titlebar_bg_inactive": "rgba(30, 30, 34, 0.86)",
    "titlebar_text": "rgba(230, 230, 230, 1)",
    "titlebar_text_inactive": "rgba(138, 138, 138, 1)",
    "bar_item_active": "rgba(255, 255, 255, 0.18)",
    "module_bg": "rgba(255, 255, 255, 0.08)",
    "module_track": "rgba(255, 255, 255, 0.12)",
    "module_fill": "rgba(255, 255, 255, 0.92)",
    "module_button": "rgba(255, 255, 255, 0.12)",
    "toggle_off": "rgba(255, 255, 255, 0.16)",
    "content_bg": "#1e1e1e",
    "pane_bg": "#242424",
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
    "r_window": "10px",
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
                 "graphite": "#636366", "gray": "#8e8e93", "black": "#1c1c1e"}

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


def accent_tokens(name: str, dark: bool) -> dict:
    """Token overrides for an accent colour (empty for the default blue)."""
    if name not in ACCENTS or name == "blue":
        return {}
    c = ACCENTS[name][1 if dark else 0]
    return {"accent": c, "accent_selected": c if dark else _darker(c)}


# Spacing scale (px) for layout code.
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


def palette(dark: bool, theme: str = "mac") -> dict:
    """All tokens for one theme and appearance."""
    t = THEMES.get(theme, THEMES["mac"])
    return {**t["shared"], **{"sys_" + k: v for k, v in SYSTEM_COLORS.items()},
            **(t["dark"] if dark else t["light"])}
