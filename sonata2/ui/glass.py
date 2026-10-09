"""Glass (frosted, see-through materials), set per part of the desktop:
Settings > Appearance > Glass & Transparency (appearance.json "glass").

    "glass": {"dock": {"on": true, "alpha": 0.6}, "menubar": {...},
              "menus": {...}, "windows": {...}, "blur": 50}

Each part: on (blurred and see-through) or solid, and how see-through
("alpha": the tint's opacity; None = the theme's own). The blur's
strength is one for all: Wayfire's blur plugin has a single radius
(kawase_offset), applied with the title bar colours (titlebars.apply_colors).
Accessibility > Reduce transparency still turns every part solid.

No GTK here: ui/theme.py turns these into the material tokens
(dock_material, bar_material, panel_material, sidebar_material,
launchpad_material)."""

ITEMS = ("dock", "menubar", "menus", "windows", "launchpad")
TITLES = {"dock": "Dock", "menubar": "Menu bar", "menus": "Menus and panels",
          "windows": "Windows", "launchpad": "Apps Menu"}
SUBTITLES = {"menus": "Menu bar menus, Control Center, Wi-Fi, notifications panels",
             "windows": "Sidebars, and title bars when Glass title bars is on",
             "launchpad": "The apps panel when Apps > Style is Apps Menu"}
# glass token -> solid token, per part
MATERIALS = {"dock": ("glass_tint", "solid_tint"), "menubar": ("bar_bg", "window_bg"),
             "menus": ("glass_tint", "menu_bg"), "windows": ("window_glass", "sidebar_bg"),
             "launchpad": ("glass_tint", "menu_bg")}
# Wayfire blurs only where a pixel's alpha reaches alpha_threshold (0.5 in
# wayfire.ini: soft shadows stay unfrosted). A part set more see-through
# than that lowers the threshold (blur_threshold), never under THRESHOLD_MIN
# (dark menus' shadows would get a frosted halo).
ALPHA_RANGE = (0.0, 0.97)        # most see-through: clear glass (Vini); under ~0.36 the frost fades
THRESHOLD, THRESHOLD_MIN = 0.5, 0.36
BLUR_DEFAULT = 50
# kawase_offset at strength 0 / 100 (50 -> 3.38, the default): for kawase_degrade 4 -- the
# frosting of 1.5 .. 7.5 on the earlier third-size picture (x 3/4)
OFFSET_RANGE = (1.1, 5.66)


def settings(cfg: dict = None) -> dict:
    """{"dock": {"on", "alpha"}, ..., "blur": int} with defaults filled in.
    `cfg`: appearance.json's content (read when None). Before this setting
    existed one switch ("Translucent glass", dock.json "glass") made every
    part solid: that choice stands until a part is set."""
    from .. import config
    if cfg is None:
        from ..icons import APPEARANCE_DEFAULTS
        cfg = config.load("appearance", APPEARANCE_DEFAULTS)
    raw = cfg.get("glass") if isinstance(cfg.get("glass"), dict) else {}
    legacy_on = bool(config.load("dock", {"glass": True}).get("glass", True))
    out = {}
    for item in ITEMS:
        part = raw.get(item) if isinstance(raw.get(item), dict) else {}
        alpha = part.get("alpha")
        out[item] = {"on": bool(part.get("on", legacy_on)),
                     "alpha": clamp_alpha(alpha) if isinstance(alpha, (int, float)) else None}
    blur = raw.get("blur")
    out["blur"] = int(min(100, max(0, blur))) if isinstance(blur, (int, float)) else BLUR_DEFAULT
    return out


def clamp_alpha(a: float) -> float:
    return round(min(ALPHA_RANGE[1], max(ALPHA_RANGE[0], float(a))), 3)


def css_alpha(css: str) -> float:
    css = css.strip()
    if css.startswith("rgba("):
        return float(css[5:-1].split(",")[3])
    return 1.0


def with_alpha(css: str, alpha: float) -> str:
    """'rgba(r, g, b, a)' (or rgb / #rrggbb) with another alpha."""
    css = css.strip()
    if css.startswith("#"):
        r, g, b = (int(css[i:i + 2], 16) for i in (1, 3, 5))
    else:
        r, g, b = (float(x) for x in css[css.index("(") + 1:css.index(")")].split(",")[:3])
        r, g, b = round(r), round(g), round(b)
    return f"rgba({r}, {g}, {b}, {alpha:.3f})"


def material(item: str, tokens: dict, glass_ok: bool, cfg: dict = None) -> str:
    """The colour of a part's background: its glass (with the chosen
    alpha) when on and the compositor blurs (glass_ok), else its solid one."""
    s = (cfg or settings())[item]
    glass, solid = MATERIALS[item]
    if not (glass_ok and s["on"]):
        return tokens[solid]
    return with_alpha(tokens[glass], s["alpha"]) if s["alpha"] is not None else tokens[glass]


def alpha_of(item: str, tokens: dict, cfg: dict = None) -> float:
    """What the slider shows: the chosen alpha, or the theme's."""
    s = (cfg or settings())[item]
    return s["alpha"] if s["alpha"] is not None else css_alpha(tokens[MATERIALS[item][0]])


def blur_offset(strength: int) -> float:
    lo, hi = OFFSET_RANGE
    return round(lo + (hi - lo) * min(100, max(0, strength)) / 100, 2)


# -- what Wayfire blurs (titlebars.apply_colors) ----------------------------------------------
# A part that is solid needs no blur behind it: leaving it out saves the
# blur pass on every frame it changes (GPU time; the Radeon 680M hung in
# that pass). Only positive rules (no "!"): with every part on, the rule
# is exactly the one in wayfire.ini.
BLUR_ALL = 'app_id contains "sonata2" | type is "unmanaged"'
BLUR_PARTS = {"dock": 'app_id is "sonata2-dock"', "menubar": 'app_id is "sonata2-topbar"',
              # popups, and every other Sonata layer surface (panels, notifications, Launchpad...)
              "menus": 'app_id contains "sonata2-" | type is "unmanaged"',
              "windows": 'app_id contains "sonata2."',     # Sonata apps (io.github....sonata2.files)
              "launchpad": 'app_id is "sonata2-launchpad-window"'}
BLUR_NONE = 'app_id is "sonata2-blur-nothing"'           # matches nothing: the plugin stays loaded


def blur_rule(cfg: dict = None) -> str:
    s = cfg or settings()
    on = [i for i in ITEMS if s[i]["on"]]
    if len(on) == len(ITEMS):
        return BLUR_ALL
    if "menus" in on:                        # it covers the Dock's, the menu bar's and Launchpad's surfaces too
        on = [i for i in on if i not in ("dock", "menubar", "launchpad")]
    rule = " | ".join(BLUR_PARTS[i] for i in on)
    if "dock" in on:                         # an open Dock folder (a popup) has the Dock's glass
        rule += ' | type is "unmanaged"'
    return rule or BLUR_NONE


def blur_threshold(cfg: dict = None) -> float:
    """Wayfire's alpha_threshold for the alphas chosen: just under the most
    see-through part that is on, 0.5 otherwise."""
    s = cfg or settings()
    alphas = [s[i]["alpha"] for i in ITEMS if s[i]["on"] and s[i]["alpha"] is not None]
    t = min([THRESHOLD] + [a - 0.02 for a in alphas])
    return round(max(THRESHOLD_MIN, t), 2)
