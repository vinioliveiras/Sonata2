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
    "glass_tint": "rgba(246, 246, 250, 0.38)",   # blurred by the compositor
    "solid_tint": "rgba(236, 236, 240, 0.78)",   # same surface without blur
    "indicator": "rgba(0, 0, 0, 0.62)",          # Dock running dot
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
    "glass_tint": "rgba(30, 30, 34, 0.42)",
    "solid_tint": "rgba(38, 38, 42, 0.74)",
    "indicator": "rgba(255, 255, 255, 0.72)",
    "shadow_menu": "0 6px 18px rgba(0, 0, 0, 0.40)",
    "shadow_label": "0 2px 8px rgba(0, 0, 0, 0.35)",
    "shadow_plate": "0 6px 18px rgba(0, 0, 0, 0.28)",
    "shadow_control": "0 0.5px 1px rgba(0, 0, 0, 0.40)",
    "shadow_knob": "0 1px 2px rgba(0, 0, 0, 0.45)",
}

# -- appearance-independent ------------------------------------------------------
SHARED = {
    # typography (SF Pro when installed, Inter as the open substitute)
    "font": '"SF Pro Text", "Inter", "Cantarell", sans-serif',
    "font_display": '"SF Pro Display", "Inter Display", "Inter", sans-serif',
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
    "scrim": "rgba(0, 0, 0, 0.28)",
    "on_scrim": "#ffffff",
    "on_scrim_secondary": "rgba(255, 255, 255, 0.60)",
    "field_on_scrim": "rgba(255, 255, 255, 0.16)",
    "tile_on_scrim": "rgba(255, 255, 255, 0.20)",
    "folder_panel": "rgba(255, 255, 255, 0.16)",
    # timings
    "t_press": "80ms",
    "t_fast": "150ms",
    "t_standard": "250ms",
}

# Spacing scale (px) for layout code.
SPACE = {"xxs": 2, "xs": 4, "s": 6, "m": 8, "l": 12, "xl": 16, "xxl": 20}


# Visual themes. Only "mac" exists now; a "windows" theme (Windows 11 look,
# "Task Bar" mode) will add its own LIGHT/DARK/SHARED with the same keys, so
# components need no change -- only their token values differ.
THEMES = {"mac": {"light": LIGHT, "dark": DARK, "shared": SHARED}}


def palette(dark: bool, theme: str = "mac") -> dict:
    """All tokens for one theme and appearance."""
    t = THEMES.get(theme, THEMES["mac"])
    return {**t["shared"], **(t["dark"] if dark else t["light"])}
