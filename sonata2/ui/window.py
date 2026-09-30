"""Window chrome: macOS-style "traffic light" controls (from LayerOSX).

    window.traffic_lights(on_close, on_minimize, on_zoom=None)

on_zoom=None greys the green one out (fixed-size windows, like System
Settings). Colours are Apple's; every state is spelled out so no GTK theme
can repaint them."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import theme  # noqa: E402

theme.register("""
/* macOS-style window controls ("traffic lights"). Every state is spelled out
   and the provider is loaded above USER priority, so a user GTK theme (e.g. a
   macOS-look theme in ~/.config/gtk-4.0) can't repaint them grey on
   hover/press/focus. */
/* 12px dots, 8px apart (centres 20px apart). */
/* Every window (Sonata's, pixdecor's): first dot centred 13 px from the left
   edge and 14 px from the top, close to the corner (Vini's call). */
.traffic { margin-left: 3px; margin-top: 8px; }
headerbar .traffic { margin-left: -2px; margin-top: 2px; }   /* headerbar adds its own padding */
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
  min-width: 12px; min-height: 12px; padding: 0; margin: 0 4px;
  border: none; border-radius: 999px; outline: none; box-shadow: none; text-shadow: none;
  background-color: transparent; background-repeat: no-repeat; background-position: center;
  background-size: 12px 12px;
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
""", key="traffic-lights")


# Standard title bar: every Sonata window puts the traffic lights at the
# same spot (first dot centred 13 px from the left edge and 14 px from the
# top, in a 52 px bar), whatever the window. Use it instead of placing
# traffic_lights() by hand.
TITLEBAR_H = 52
theme.register("""
.sonata-titlebar { min-height: 52px; padding: 0 10px 0 0; }
.sonata-titlebar > .sonata-titlebar-title { font-weight: 700; font-size: %(text_body)s; color: %(label)s; }
""", key="titlebar")


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
    bar.set_start_widget(traffic_lights(win.close, win.minimize, zoom))
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
  box-shadow: 0 22px 56px rgba(0, 0, 0, 0.30), 0 0 0 0.5px rgba(0, 0, 0, 0.40),
              inset 0 0 0 0.5px %(highlight)s; }
window.sonata-window.csd:backdrop { box-shadow: 0 14px 34px rgba(0, 0, 0, 0.20), 0 0 0 0.5px rgba(0, 0, 0, 0.30); }
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


def standard(win) -> None:
    """Give an app window Sonata's standard frame (call once, any time)."""
    from gi.repository import Gtk as _Gtk
    win.add_css_class("sonata-window")
    win.set_overflow(_Gtk.Overflow.HIDDEN)      # children clipped to the rounded corners


def traffic_lights(on_close, on_minimize, on_zoom=None) -> Gtk.Box:
    """Close / minimize / zoom as macOS-style coloured dots. on_zoom=None greys the
    green one out (fixed-size windows, like System Settings)."""
    box = Gtk.Box(css_classes=["traffic"], valign=Gtk.Align.START)
    for css, tip, cb in (("tl-close", "Close", on_close),
                         ("tl-min", "Minimize", on_minimize),
                         ("tl-zoom", "Zoom" if on_zoom else None, on_zoom)):
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
