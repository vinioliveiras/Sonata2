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
.traffic { margin-left: 8px; }
.traffic button,
.traffic button:hover,
.traffic button:active,
.traffic button:checked,
.traffic button:focus,
.traffic button:focus-visible,
.traffic button:backdrop {
  min-width: 12px; min-height: 12px; padding: 0; margin: 0 3px;
  border: none; border-radius: 999px; outline: none;
  background-image: none; text-shadow: none;
  box-shadow: inset 0 0 0 0.5px rgba(0,0,0,.18);
  transition: none;
}
.traffic button.tl-close,
.traffic button.tl-close:hover,
.traffic button.tl-close:active,
.traffic button.tl-close:focus,
.traffic button.tl-close:backdrop { background-color: #ff5f57; }
.traffic button.tl-min,
.traffic button.tl-min:hover,
.traffic button.tl-min:active,
.traffic button.tl-min:focus,
.traffic button.tl-min:backdrop { background-color: #febc2e; }
.traffic button.tl-zoom,
.traffic button.tl-zoom:hover,
.traffic button.tl-zoom:active,
.traffic button.tl-zoom:focus,
.traffic button.tl-zoom:backdrop { background-color: #28c840; }
.traffic button.tl-disabled,
.traffic button.tl-disabled:hover,
.traffic button.tl-disabled:backdrop { background-color: %(tl_disabled)s; }
.traffic:hover button.tl-disabled label { color: transparent; }
.traffic button:active { filter: brightness(0.85); }
.traffic button label { font-size: 9px; font-weight: 900; color: transparent; padding: 0; margin: 0; }
.traffic:hover button label { color: rgba(0,0,0,.55); }
""")


def traffic_lights(on_close, on_minimize, on_zoom=None) -> Gtk.Box:
    """Close / minimize / zoom as macOS-style coloured dots. on_zoom=None greys the
    green one out (fixed-size windows, like System Settings)."""
    box = Gtk.Box(css_classes=["traffic"], valign=Gtk.Align.CENTER)
    for css, glyph, tip, cb in (("tl-close", "×", "Close", on_close),
                                ("tl-min", "−", "Minimize", on_minimize),
                                ("tl-zoom", "+", "Zoom" if on_zoom else None, on_zoom)):
        b = Gtk.Button(label=glyph, tooltip_text=tip, css_classes=[css], valign=Gtk.Align.CENTER,
                       focus_on_click=False, can_focus=False)
        if cb:
            b.connect("clicked", lambda _b, f=cb: f())
        else:
            b.add_css_class("tl-disabled")
            b.set_can_target(False)
        box.append(b)
    return box

