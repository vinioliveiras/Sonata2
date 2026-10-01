"""Loads every component's CSS, filled with the tokens of the current
appearance, into one provider; reloads it when light/dark changes.

Components call `register(template)` at import time (or when their
geometry is known). Templates use
`%(token)s` placeholders (write `%%` for a literal percent sign)."""
import re

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gtk  # noqa: E402

from . import tokens  # noqa: E402

# Above USER priority: a user GTK theme (e.g. a macOS-look theme in
# ~/.config/gtk-4.0) must not repaint Sonata's components.
PRIORITY = Gtk.STYLE_PROVIDER_PRIORITY_USER + 10
_MS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)ms\b")

_templates = {}       # key -> (template, local placeholders)
_extra = {}           # token overrides (future: user customization)
_provider = None
_listeners = []       # called after every reload (light/dark switch)
_parsed = {}          # (token, dark) -> parsed value, for snapshot drawing


def register(template: str, key: str = None, **local) -> None:
    """Add a component's CSS. `local` = extra placeholders for this template
    only (e.g. geometry from the component's settings). Registering the same
    `key` again replaces it."""
    _templates[key or template] = (template, local)
    if _provider:
        _load()


def is_dark() -> bool:
    return Adw.StyleManager.get_default().get_dark()


def glass() -> bool:
    """True when the compositor blurs Sonata windows (Sonata session or the
    dev session: SONATA_GLASS=1 from session-env.sh). Elsewhere materials
    fall back to their solid tokens."""
    import os
    return os.environ.get("SONATA_GLASS") == "1" and not reduce_transparency()


def glass_titlebars() -> bool:
    """Settings > Appearance > "Glass title bars" (off: opaque, like GNOME apps)."""
    from .. import config
    return bool(config.load("appearance", _appearance_defaults()).get("glass_titlebars", False))


def reduce_transparency() -> bool:
    """Solid materials instead of glass: Accessibility > Reduce transparency,
    (each part: Appearance > Glass & Transparency, ui/glass.py). Live: watched
    (setup) and every Sonata surface re-styles when either changes."""
    global _reduce
    if _reduce is None:
        _reduce = reduce_transparency_now()
    return _reduce


_reduce = None
_dock_mon = None


def reduce_transparency_now() -> bool:
    """The settings as they are on disk now."""
    from .. import config
    return bool(config.load("appearance", _appearance_defaults())["reduce_transparency"])


def glass_class(widget: Gtk.Widget) -> Gtk.Widget:
    """Keep a glass surface's "solid" class in step with the setting."""
    def sync(*_a):
        (widget.remove_css_class if glass() else widget.add_css_class)("solid")
    sync()
    on_change(sync)
    return widget


def _transparency_changed() -> None:
    global _reduce
    new = reduce_transparency_now()
    if new != _reduce:
        _reduce = new
        _parsed.clear()
        _load(fade=True)


def values() -> dict:
    """Current tokens (for code that needs a value, e.g. drawing), plus the
    materials resolved for this compositor: `sidebar_material`, `panel_material`."""
    v = {**tokens.palette(is_dark(), _theme()), **tokens.accent_tokens(_accent(), is_dark()), **_extra}
    # Sidebars: the windows' glass (as their title bars), darker than the Dock's
    # Each part's glass is set on its own (ui/glass.py, Settings > Appearance)
    from . import glass as G
    gcfg = _glass_cfg()
    ok = glass()
    v["sidebar_material"] = G.material("windows", v, ok, gcfg)
    v["panel_material"] = G.material("menus", v, ok, gcfg)      # menus and panels
    v["dock_material"] = G.material("dock", v, ok, gcfg)
    v["bar_material"] = G.material("menubar", v, ok, gcfg)
    if ok and gcfg["windows"]["on"] and _glass_bars():   # title bars and toolbars: the glass again
        a = gcfg["windows"]["alpha"]
        for k in ("titlebar_glass", "titlebar_glass_inactive"):
            if a is not None:
                v[k] = G.with_alpha(v[k], a)
        v["titlebar_bg"], v["titlebar_bg_inactive"] = v["titlebar_glass"], v["titlebar_glass_inactive"]
    return v


_glass_seen = None


def _glass_cfg() -> dict:
    """The glass settings (read once; _appearance_changed reads them again)."""
    global _glass_seen
    if _glass_seen is None:
        from . import glass as G
        _glass_seen = G.settings()
    return _glass_seen


_glass_bars_on = None
_radii_seen = None


def _glass_bars() -> bool:
    global _glass_bars_on
    if _glass_bars_on is None:
        _glass_bars_on = glass_titlebars()
    return _glass_bars_on


_theme_name = None
_accent_name = None


def _appearance_defaults() -> dict:
    from ..icons import APPEARANCE_DEFAULTS
    return APPEARANCE_DEFAULTS


def _accent() -> str:
    """Accent colour from Sonata's appearance settings (live: see setup)."""
    global _accent_name
    if _accent_name is None:
        from .. import config
        _accent_name = config.load("appearance", _appearance_defaults())["accent"]
    return _accent_name


def _appearance_changed() -> None:
    """appearance.json changed (Settings): new accent, re-style everything."""
    global _accent_name
    global _glass_bars_on
    old = _accent_name
    _accent_name = None
    _transparency_changed()
    bars = glass_titlebars()
    global _radii_seen, _glass_seen
    radii = tokens.user_radii()
    from . import glass as G
    gl = G.settings()
    if _accent() != old or bars != _glass_bars_on or radii != _radii_seen or gl != _glass_seen:
        _glass_bars_on = bars
        _radii_seen = radii
        _glass_seen = gl
        _parsed.clear()
        _load(fade=True)


def _theme() -> str:
    """Visual theme from Sonata's appearance settings (read once)."""
    global _theme_name
    if _theme_name is None:
        from .. import config
        _theme_name = config.load("appearance", _appearance_defaults())["theme"]
    return _theme_name


# -- cross-fade on appearance changes (Dark Mode, accent) --------------------------------
# Every window fades from a picture of its old look (_fade_window); what is
# painted with rgba() (Dock, menu bar...) also blends old and new colours.
FADE_MS = 320
_last_vals = None
_fade = None            # {"old": vals, "t0": µs} while fading

def _content(win):
    get = getattr(win, "get_content", None)
    return get() if get and not isinstance(win, Gtk.Popover) else win.get_child()


def _set_content(win, child) -> None:
    if hasattr(win, "set_content"):
        win.set_content(child)
    else:
        win.set_child(child)


def _fade_window(win) -> None:
    """Cross-fade one window: a picture of how it looked, over it, fading
    out while the new style shows underneath. (GTK doesn't run CSS
    transitions for stylesheet changes, so this is done with a snapshot.)"""
    child = _content(win)
    w, h = (child.get_width(), child.get_height()) if child else (0, 0)
    if not child or w <= 0 or h <= 0 or not win.get_renderer():
        return
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(child).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return
    tex = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    over = getattr(win, "_sonata_fade_overlay", None)
    if over is None or over.get_child() is not child:
        # moving the content drops the focus, and GTK hands it on: a sidebar
        # list would select its next row (Settings jumped a section). Keep it.
        focus = win.get_focus()
        _set_content(win, None)
        over = Gtk.Overlay()
        over.set_child(child)
        _set_content(win, over)
        win._sonata_fade_overlay = over
        if focus is not None and focus.get_root() is win:
            focus.grab_focus()
    # the old look at its own size, pinned to the top left: if the window's
    # layout changes meanwhile it is covered, never stretched
    pic = Gtk.Picture(paintable=tex, can_target=False, can_shrink=False, content_fit=Gtk.ContentFit.FILL,
                      halign=Gtk.Align.START, valign=Gtk.Align.START)
    pic.set_size_request(w, h)
    over.add_overlay(pic)
    t0 = GLib.get_monotonic_time()

    def tick(*_a):
        t = min(1.0, (GLib.get_monotonic_time() - t0) / (FADE_MS * 1000))
        pic.set_opacity(1.0 - t * t * (3 - 2 * t))
        if t >= 1.0:
            over.remove_overlay(pic)
            return False
        return True
    GLib.timeout_add(16, tick)


def _start_fade(old_vals) -> None:
    global _fade
    if not old_vals:
        return
    for w in Gtk.Window.list_toplevels():
        if w.get_visible() and w.get_mapped() and not getattr(w, "sonata_no_fade", False):
            try:
                _fade_window(w)
            except (GLib.Error, TypeError, AttributeError):
                pass                                   # never block the switch itself
    _fade = {"old": old_vals, "t0": GLib.get_monotonic_time()}

    def tick():
        global _fade
        done = _fade is None or GLib.get_monotonic_time() - _fade["t0"] >= FADE_MS * 1000
        if done:
            _fade = None
            for k in [k for k in _parsed if isinstance(k[0], str) and k[0].startswith("old:")]:
                del _parsed[k]
        for cb in list(_listeners):
            cb()
        return not done
    GLib.timeout_add(16, tick)


def _fade_t() -> float:
    if _fade is None:
        return 1.0
    t = min(1.0, (GLib.get_monotonic_time() - _fade["t0"]) / (FADE_MS * 1000))
    return t * t * (3 - 2 * t)                     # ease in-out


def _load(*_a, fade=False) -> None:
    global _last_vals
    vals = values()
    old = _last_vals
    _last_vals = vals
    if fade or (_a and isinstance(_a[0], Adw.StyleManager)):      # notify::dark
        _start_fade(old)
    css = "\n".join(t % {**vals, **loc} for t, loc in _templates.values())
    css = _MS.sub(lambda m: f"{tokens.ms(float(m.group(1)))}ms", css)     # ANIMATION_SPEED
    _provider.load_from_string(css)
    for cb in list(_listeners):
        cb()


def on_change(callback) -> None:
    """Call `callback()` whenever the appearance (and so the tokens) changes;
    for widgets that draw with rgba()/shadow() in their snapshot."""
    _listeners.append(callback)


def px(token: str) -> float:
    """A size token ("18px") as a number (cached per appearance)."""
    key = ("px:" + token, is_dark())
    if key not in _parsed:
        _parsed[key] = float(values()[token].rstrip("px"))
    return _parsed[key]


def rgba(token: str) -> Gdk.RGBA:
    """A colour token as Gdk.RGBA (cached per appearance); during a
    cross-fade, the blend of the previous and the new colour."""
    key = (token, is_dark())
    if key not in _parsed:
        c = Gdk.RGBA()
        c.parse(values()[token])
        _parsed[key] = c
    new = _parsed[key]
    if _fade is None or token not in _fade["old"]:
        return new
    okey = ("old:" + token, _fade["t0"])
    if okey not in _parsed:
        o = Gdk.RGBA()
        o.parse(_fade["old"][token])
        _parsed[okey] = o
    o, t = _parsed[okey], _fade_t()
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = (o.red + (new.red - o.red) * t, o.green + (new.green - o.green) * t,
                                       o.blue + (new.blue - o.blue) * t, o.alpha + (new.alpha - o.alpha) * t)
    return c


_SHADOW = re.compile(r"(-?[\d.]+)(?:px)?\s+(-?[\d.]+)(?:px)?\s+([\d.]+)(?:px)?\s+(.+)$")


def shadow(token: str):
    """A shadow token ("dx dy blur colour") as (dx, dy, blur, Gdk.RGBA)."""
    key = ("shadow:" + token, is_dark())
    if key not in _parsed:
        dx, dy, blur, col = _SHADOW.match(values()[token].strip()).groups()
        c = Gdk.RGBA()
        c.parse(col)
        _parsed[key] = (float(dx), float(dy), float(blur), c)
    return _parsed[key]


def _animation_speed() -> None:
    """Every Adw animation and GTK stack/revealer transition at Sonata's
    ANIMATION_SPEED (tokens.py), wherever its duration is set."""
    if getattr(Adw.TimedAnimation, "_sonata_speed", False):
        return
    new, set_dur = Adw.TimedAnimation.new, Adw.TimedAnimation.set_duration
    Adw.TimedAnimation.new = staticmethod(lambda w, a, b, d, t: new(w, a, b, tokens.ms(d), t))
    Adw.TimedAnimation.set_duration = lambda self, d: set_dur(self, tokens.ms(d))
    Adw.TimedAnimation._sonata_speed = True
    for cls in (Gtk.Stack, Gtk.Revealer):
        orig = cls.set_transition_duration
        cls.set_transition_duration = lambda self, d, _o=orig: _o(self, tokens.ms(d))

        def init(self, *a, _cls=cls, _init=cls.__init__, **kw):
            if "transition_duration" in kw:
                kw["transition_duration"] = tokens.ms(kw["transition_duration"])
            _init(self, *a, **kw)
        cls.__init__ = init


def setup() -> None:
    """Install the provider once per process (idempotent)."""
    global _provider
    if _provider:
        return
    from .. import icons
    icons.setup()     # Sonata's own icon theme, before any widget
    _provider = Gtk.CssProvider()
    Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _provider, PRIORITY)
    Adw.StyleManager.get_default().connect("notify::dark", _load)
    from .. import config
    global _appearance_mon
    _appearance_mon = config.watch("appearance", _appearance_changed)
    global _dock_mon
    _dock_mon = config.watch("dock", _appearance_changed)       # its old "glass" switch (ui/glass.py)
    _follow_color_scheme()
    _animation_speed()
    _load()
    _sliders_ignore_wheel()


def _sliders_ignore_wheel() -> None:
    """No slider moves under the mouse wheel (macOS): the wheel scrolls the
    page instead. GtkRange's own scroll controller is taken off every
    slider as it is realized, so the event goes on to the scrolled window."""
    def strip(scale, *_a):
        # the hook sees every widget's "realize" (the signal is GtkWidget's): sliders only
        if not isinstance(scale, Gtk.Range):
            return True
        ctrls = scale.observe_controllers()
        for i in range(ctrls.get_n_items() - 1, -1, -1):
            c = ctrls.get_item(i)
            if isinstance(c, Gtk.EventControllerScroll):
                scale.remove_controller(c)
        return True
    GObject.add_emission_hook(Gtk.Range, "realize", strip)


_appearance_mon = None
_forced = False
_iface = None


def _follow_color_scheme() -> None:
    """Dark Mode comes from Sonata's own settings (prefs.py), read directly
    and followed live."""
    global _iface
    from .. import prefs
    state = {"dark": None}

    def apply(*_a):
        if _forced:
            return
        dark = prefs.get(prefs.I, "color-scheme") == "prefer-dark"
        if dark == state["dark"]:
            return
        state["dark"] = dark
        Adw.StyleManager.get_default().set_color_scheme(
            Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
    _iface = prefs.watch(apply)
    apply()


def force_appearance(appearance: str) -> None:
    """"light" / "dark" for this process (previews); "auto" follows the system."""
    global _forced
    _forced = appearance in ("dark", "light")
    Adw.StyleManager.get_default().set_color_scheme(
        {"dark": Adw.ColorScheme.FORCE_DARK, "light": Adw.ColorScheme.FORCE_LIGHT}
        .get(appearance, Adw.ColorScheme.DEFAULT))
