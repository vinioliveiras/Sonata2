"""Window chrome: macOS-style "traffic light" controls (from LayerOSX).

    window.traffic_lights(on_close, on_minimize, on_zoom=None)

on_zoom=None greys the green one out (fixed-size windows, like System
Settings). Colours are Apple's; every state is spelled out so no GTK theme
can repaint them."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import theme  # noqa: E402

# a GTK header bar's own padding around the traffic lights (Sonata's theme)
HEADERBAR_PAD_X, HEADERBAR_PAD_Y = 5, 6


def traffic_metrics() -> dict:
    """CSS values for the dots, from tokens.FRAME (also used by adwstyle.py)."""
    from .tokens import FRAME as F
    half = F["dot"] / 2
    left = F["dot_left"] - half - F["dot_gap"] / 2          # the box's margin: the first button adds half a gap
    top = F["dot_top"] - half
    px = lambda v: f"{v:g}px"                                # noqa: E731
    return {"tl_dot": px(F["dot"]), "tl_half_gap": px(F["dot_gap"] / 2),
            "tl_margin_left": px(left), "tl_margin_top": px(top),
            "tl_hb_left": px(left - HEADERBAR_PAD_X), "tl_hb_top": px(top - HEADERBAR_PAD_Y)}


theme.register("""
/* macOS-style window controls ("traffic lights"). Every state is spelled out
   and the provider is loaded above USER priority, so a user GTK theme (e.g. a
   macOS-look theme in ~/.config/gtk-4.0) can't repaint them grey on
   hover/press/focus. */
/* Size, spacing and place: tokens.FRAME (the same for every window: Sonata's,
   pixdecor's, other GTK 4 apps'). */
.traffic { margin-left: %(tl_margin_left)s; margin-top: %(tl_margin_top)s; }
headerbar .traffic { margin-left: %(tl_hb_left)s; margin-top: %(tl_hb_top)s; }   /* headerbar adds its own padding */
/* The dots are the same pictures pixdecor draws on other apps' title bars
   (tools/gen-decor.py), so every window looks and hovers alike: the glyph
   fades in on the button under the pointer. */
.traffic button,
.traffic button:hover,
.traffic button:active,
.traffic button:checked,
.traffic button:focus,
.traffic button:focus-visible,
.traffic button:backdrop {
  min-width: %(tl_dot)s; min-height: %(tl_dot)s; padding: 0; margin: 0 %(tl_half_gap)s;
  border: none; border-radius: 999px; outline: none; box-shadow: none; text-shadow: none;
  background-color: transparent; background-repeat: no-repeat; background-position: center;
  background-size: %(tl_dot)s %(tl_dot)s;
  transition: background-image 150ms ease;
}
.traffic button.tl-close { background-image: -gtk-icontheme("sonata-tl-close"); }
.traffic button.tl-close:hover { background-image: -gtk-icontheme("sonata-tl-close-hover"); }
.traffic button.tl-min { background-image: -gtk-icontheme("sonata-tl-minimize"); }
.traffic button.tl-min:hover { background-image: -gtk-icontheme("sonata-tl-minimize-hover"); }
.traffic button.tl-zoom { background-image: -gtk-icontheme("sonata-tl-maximize"); }
.traffic button.tl-zoom:hover { background-image: -gtk-icontheme("sonata-tl-maximize-hover"); }
.traffic button.tl-disabled, .traffic button.tl-disabled:hover, .traffic button.tl-disabled:backdrop {
  background-image: none; background-color: %(tl_disabled)s; box-shadow: inset 0 0 0 0.5px rgba(0,0,0,.18); }
.traffic button:active { filter: brightness(0.85); }
.traffic button label { color: transparent; font-size: 1px; padding: 0; margin: 0; }
""", key="traffic-lights", **traffic_metrics())


# Standard title bar: every Sonata window puts the traffic lights at the
# same spot (first dot centred 13 px from the left edge and 14 px from the
# top, in a 52 px bar), whatever the window. Use it instead of placing
# traffic_lights() by hand.
from .tokens import FRAME as _FRAME  # noqa: E402
TITLEBAR_H = _FRAME["title_h"]
theme.register("""
.sonata-titlebar { min-height: %(title_h)s; padding: 0 10px 0 0; }
.sonata-titlebar > .sonata-titlebar-title { font-weight: 700; font-size: %(text_body)s; color: %(label)s; }
""", key="titlebar", title_h=f"{TITLEBAR_H}px")


def toggle_zoom(win) -> None:
    """The green button: maximize / restore."""
    win.unmaximize() if win.is_maximized() else win.maximize()


def show_again(win) -> None:
    """Present a window that was hidden instead of closed (Settings lingers,
    Calculator...). GTK remembers a minimize (the yellow button) as "minimize
    when shown" and Wayland never tells it the Dock brought the window back,
    so a hidden window came back minimized. Forget that first."""
    if not win.get_visible():
        win.unminimize()
    win.present()


def titlebar(win, title: str = None, end: Gtk.Widget = None, zoom=None) -> Gtk.WindowHandle:
    """52 px bar: traffic lights (zoom=None greys the green one, zoom=True
    maximizes/restores), optional bold centred title (returned as
    .title_label) and an end widget."""
    if zoom is True:
        zoom = lambda: toggle_zoom(win)     # noqa: E731
    bar = Gtk.CenterBox(css_classes=["sonata-titlebar"])
    lights = traffic_lights(win.close, win.minimize, zoom)
    if buttons_side() == "right":
        bar.set_end_widget(lights)                  # (an end widget given below goes before them)
    else:
        bar.set_start_widget(lights)
    handle = Gtk.WindowHandle(child=bar)
    handle.title_label = None
    if title is not None:
        handle.title_label = Gtk.Label(label=title, css_classes=["sonata-titlebar-title"])
        from gi.repository import Pango
        handle.title_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        handle.title_label.set_max_width_chars(50)
        bar.set_center_widget(handle.title_label)
    if end is not None:
        end.set_valign(Gtk.Align.CENTER)
        if buttons_side() == "right":              # the end widget, then the buttons at the edge
            bar.set_end_widget(None)
            both = Gtk.Box(spacing=8)
            both.append(end)
            both.append(lights)
            bar.set_end_widget(both)
        else:
            bar.set_end_widget(end)
    handle.bar = bar
    return handle


# Materials (Big Sur vibrancy). A window using them keeps a transparent
# background; the compositor blurs what shows through (Wayfire blur plugin,
# app_id contains "sonata2"), and panes that must be opaque set their own.
theme.register("""
.sonata-sidebar { background: %(sidebar_material)s; }
window.sonata-glass { background: transparent; }
/* GTK's own drop-target outline (Adwaita paints it orange): Sonata marks
   drop targets itself (.drop-target / .drop-hover / .folder-target) */
*:drop(active), *:drop(active):focus { box-shadow: none; outline: none; }
/* small utility windows (About, Get Info): all glass, the Dock's material */
window.sonata-glass-window { background: %(panel_material)s; }
""", key="materials")


# Standard frame of Sonata's app windows (Files, System Settings...): Big
# Sur corners and shadow, contents clipped to the corners; maximized/tiled
# keep the corners without the shadow (Vini's call), fullscreen is square.
theme.register("""
window.sonata-window.csd { border-radius: %(r_window)s;
  box-shadow: %(window_shadow)s, 0 0 0 0.5px rgba(0, 0, 0, 0.40),
              inset 0 0 0 0.5px %(highlight)s; }
window.sonata-window.csd:backdrop { box-shadow: %(window_shadow_backdrop)s, 0 0 0 0.5px rgba(0, 0, 0, 0.30); }
/* maximized/tiled: a hairline only for windows drawing their own frame; under
   the compositor's title bar it drew a dark line between the glass title
   bar and a glass toolbar (Preview) */
window.sonata-window.csd.maximized, window.sonata-window.csd.tiled,
window.sonata-window.csd.tiled-top, window.sonata-window.csd.tiled-left, window.sonata-window.csd.tiled-right,
window.sonata-window.csd.tiled-bottom { box-shadow: 0 0 0 0.5px rgba(0, 0, 0, 0.40), inset 0 0 0 0.5px %(highlight)s; }
window.sonata-window:not(.csd) { box-shadow: none; }
/* the divider between a sidebar and the content: opaque (the window itself
   is see-through for the glass toolbar, so a bare divider showed the
   wallpaper through a 1 px gap) */
window.sonata-window paned:not(.vertical) > separator { min-width: 1px; background: %(content_bg)s;
  box-shadow: inset 1px 0 %(separator)s; }
window.sonata-window paned.vertical > separator { min-height: 1px; background: %(content_bg)s;
  box-shadow: inset 0 1px %(separator)s; }
window.sonata-window.fullscreen { border-radius: 0; box-shadow: none; }
""", key="window-frame")


# one sidebar width for every Sonata app with a source list (Settings, Files,
# Notes, Music, Calendar, Disk Manager, Task Manager's Performance): 230 + 50 (Vini)
SIDEBAR_W = 280

# What the shell keeps of every display: the 24 px menu bar and a resting
# Dock (default size, ~72 px) plus a small margin. App windows open within
# the rest, so a 1280x720 laptop never gets a window taller than it.
WORK_MARGIN_W, WORK_MARGIN_H = 32, 104


def screen_size(widget=None):
    """(width, height) in logical px of the display `widget` is shown on.
    Not shown yet (a window before present()): the smallest display, so
    whatever is sized from it fits whichever display it opens on. None: no
    display at all."""
    from gi.repository import Gdk
    disp = widget.get_display() if widget is not None else Gdk.Display.get_default()
    if disp is None:
        return None
    native = widget.get_native() if widget is not None else None
    surf = native.get_surface() if native is not None else None
    mon = disp.get_monitor_at_surface(surf) if surf is not None else None
    if mon is not None:
        g = mon.get_geometry()
        return g.width, g.height
    ms = disp.get_monitors()
    sizes = [(g.width, g.height) for g in (ms.get_item(i).get_geometry() for i in range(ms.get_n_items()))]
    return min(sizes, key=lambda s: s[0] * s[1]) if sizes else None


def fit_size(w: int, h: int, screen=None) -> tuple:
    """(w, h) shrunk to the work area of a `screen`-sized display (menu bar
    and Dock taken off); never grown. screen=None: unchanged."""
    if not screen:
        return int(w), int(h)
    return (int(min(w, max(1, screen[0] - WORK_MARGIN_W))),
            int(min(h, max(1, screen[1] - WORK_MARGIN_H))))


def fit_default_size(win, w: int, h: int) -> None:
    """set_default_size() that never opens a window bigger than the display
    (the defaults were picked on 1080p; laptops have 720/768 px)."""
    win.set_default_size(*fit_size(w, h, screen_size(win if win.get_realized() else None)))


SAVE_SIZE_MS = 600          # a resize settles, then its size is kept


def remember_size(win, key: str, w: int, h: int) -> None:
    """The window opens at the size it last had (winsize; first time: w x h,
    never bigger than the display) and maximized if it was; that size is
    kept when it closes (Vini: every app should remember it)."""
    from .. import winsize
    st = winsize.saved(key)
    fit_default_size(win, *((st["width"], st["height"]) if st else (w, h)))
    if st and st["maximized"]:
        win.maximize()

    def keep(*_a):
        if win.is_fullscreen():
            return False
        if win.is_maximized() or not win.get_realized():
            dw, dh = win.get_default_size()         # GTK keeps it as the unmaximized size
        else:
            dw, dh = win.get_width(), win.get_height()
        if dw > 0 and dh > 0:
            winsize.save(key, dw, dh, win.is_maximized())
        return False
    win.connect("close-request", keep)            # connected first: runs before an app's own handler
    win.connect("unrealize", keep)                # the app quit without closing it
    # also while it's open, shortly after a resize: a restart or log out ends
    # the app without closing its window (Vini: sizes were lost on restart)
    pending = {"src": 0}

    def soon(*_a):
        if pending["src"]:
            GLib.source_remove(pending["src"])

        def run():
            pending["src"] = 0
            if win.get_realized():
                keep()
            return False
        pending["src"] = GLib.timeout_add(SAVE_SIZE_MS, run)
    for prop in ("default-width", "default-height", "maximized"):
        win.connect(f"notify::{prop}", soon)
    win.connect("destroy", lambda *_a: pending["src"] and GLib.source_remove(pending["src"]))


def standard(win) -> None:
    """Give an app window Sonata's standard frame (call once, any time)."""
    from gi.repository import Gtk as _Gtk
    win.add_css_class("sonata-window")
    win.set_overflow(_Gtk.Overflow.HIDDEN)      # children clipped to the rounded corners


def buttons_side() -> str:
    """"left" (macOS, the default) or "right": where windows keep their buttons."""
    from . import tokens
    return tokens.user_buttons_side()


def traffic_lights(on_close, on_minimize, on_zoom=None) -> Gtk.Box:
    """Close / minimize / zoom as macOS-style coloured dots. on_zoom=None greys the
    green one out (fixed-size windows, like System Settings). On the right
    (Settings > Appearance), mirrored: close at the window's edge."""
    box = Gtk.Box(css_classes=["traffic"], valign=Gtk.Align.START)
    dots = (("tl-close", "Close", on_close),
            ("tl-min", "Minimize", on_minimize),
            ("tl-zoom", "Zoom" if on_zoom else None, on_zoom))
    if buttons_side() == "right":
        dots = tuple(reversed(dots))
    for css, tip, cb in dots:
        b = Gtk.Button(tooltip_text=tip, css_classes=[css], valign=Gtk.Align.CENTER,
                       focus_on_click=False, can_focus=False)
        if cb:
            b.connect("clicked", lambda _b, f=cb: f())
        else:
            b.add_css_class("tl-disabled")
            b.set_can_target(False)
        box.append(b)
    return box



# Toolbar under the compositor's glass title bar (Sonata apps keep the
# title bar pixdecor draws): the same glass, so bar and toolbar read as one
# (macOS unified toolbar). The window must be see-through behind it
# (window.sonata-unified); the rest of its content paints its own background.
theme.register("""
window.sonata-unified { background: transparent; }
.sonata-toolbar { min-height: 34px; padding: 2px 8px 0 8px; background: %(titlebar_bg)s;
  box-shadow: inset 0 -1px %(separator)s; }
window:backdrop .sonata-toolbar { background: %(titlebar_bg_inactive)s; }
/* pixdecor's title bar reaches under the window's top: 1 px, 5 px when
 * maximized. Those rows of the toolbar stay see-through, so the title bar's
 * glass shows there alone -- painted twice, the glass turned into a dark
 * band (the compositor's seam fix could miss it, maximized). */
window.sonata-unified .sonata-toolbar {
  background: linear-gradient(to bottom, transparent 1px, %(titlebar_bg)s 1px); }
window.sonata-unified:backdrop .sonata-toolbar {
  background: linear-gradient(to bottom, transparent 1px, %(titlebar_bg_inactive)s 1px); }
window.sonata-unified.maximized .sonata-toolbar {
  background: linear-gradient(to bottom, transparent 5px, %(titlebar_bg)s 5px); }
window.sonata-unified.maximized:backdrop .sonata-toolbar {
  background: linear-gradient(to bottom, transparent 5px, %(titlebar_bg_inactive)s 5px); }
.sonata-toolbar button.tool { min-width: 28px; min-height: 26px; padding: 0 6px; border-radius: 6px;
  border: none; background: none; box-shadow: none; color: %(label_secondary)s; }
.sonata-toolbar button.tool:hover { background: %(tool_hover)s; color: %(label)s; }
""", key="unified-toolbar")


def glass_toolbar(win, start=(), end=()) -> Gtk.CenterBox:
    """A toolbar continuing the glass title bar. start/end: [(icon, tooltip,
    callback)] buttons at either side."""
    win.add_css_class("sonata-unified")
    bar = Gtk.CenterBox(css_classes=["sonata-toolbar"])
    for items, setter in ((start, bar.set_start_widget), (end, bar.set_end_widget)):
        if not items:
            continue
        box = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        for icon, tip, cb in items:
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["tool"], can_focus=False)
            b.connect("clicked", lambda _b, f=cb: f())
            box.append(b)
        setter(box)
    return Gtk.WindowHandle(child=bar)
