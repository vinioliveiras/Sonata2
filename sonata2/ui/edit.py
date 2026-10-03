"""Edit mode for grids of things the user arranges (Launchpad's apps, the
Control Center's modules): the jiggle, the delete badge and the hold that
starts it -- one look and one feel everywhere (Vini: "the same behaviour as
the apps in Launchpad").

    ui.edit.badge(on_click)          # the round x at an item's top-left corner
    ui.edit.hold(widget, on_hold)    # press and hold starts edit mode
    container.add_css_class("jiggle")   # its .sonata-jiggle children wiggle
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import theme  # noqa: E402

HOLD_MS = 800                       # press and hold before edit mode starts

theme.register("""
@keyframes sonata-jiggle { 0%% { transform: rotate(-1.6deg); } 50%% { transform: rotate(1.6deg); }
                           100%% { transform: rotate(-1.6deg); } }
/* wide items (a Control Center row) wiggle less: the same angle moves their ends a lot */
@keyframes sonata-jiggle-soft { 0%% { transform: rotate(-0.45deg); } 50%% { transform: rotate(0.45deg); }
                                100%% { transform: rotate(-0.45deg); } }
.jiggle .sonata-jiggle { animation: sonata-jiggle 260ms ease-in-out infinite; }
.jiggle .sonata-jiggle.odd { animation-delay: -130ms; }
.jiggle .sonata-jiggle.wide { animation-name: sonata-jiggle-soft; }
.sonata-edit-badge { min-width: 20px; min-height: 20px; padding: 0; border-radius: 99px; border: none;
  background: rgba(60, 60, 64, 0.92); color: white; box-shadow: 0 1px 3px rgba(0,0,0,0.4);
  -gtk-icon-size: 10px; }
.sonata-edit-badge:hover { background: rgba(80, 80, 86, 0.95); }
/* half outside the item's corner, like macOS */
.sonata-edit-badge.corner { transform: translate(-7px, -7px); }
@keyframes sonata-badge-in { from { opacity: 0; transform: scale(0.4); } to { opacity: 1; transform: none; } }
@keyframes sonata-badge-in-corner { from { opacity: 0; transform: translate(-7px, -7px) scale(0.4); }
                                    to { opacity: 1; transform: translate(-7px, -7px); } }
.sonata-edit-badge { animation: sonata-badge-in 200ms cubic-bezier(0.2, 0.8, 0.2, 1); }
.sonata-edit-badge.corner { animation-name: sonata-badge-in-corner; }
""", key="edit")


def badge(on_click, tooltip: str = "Remove", corner: bool = False) -> Gtk.Button:
    """The round x shown on an item in edit mode (hidden until then).
    corner: half outside the item's top-left corner (a module edge to edge)."""
    b = Gtk.Button(icon_name="window-close-symbolic", css_classes=["sonata-edit-badge"] + (["corner"] if corner else []),
                   tooltip_text=tooltip,
                   halign=Gtk.Align.START, valign=Gtk.Align.START, can_focus=False, visible=False)
    b.connect("clicked", lambda _b: on_click())
    return b


def hold(widget: Gtk.Widget, on_hold) -> Gtk.GestureLongPress:
    """Press and hold on `widget` starts edit mode (like an app in Launchpad)."""
    g = Gtk.GestureLongPress(delay_factor=HOLD_MS / 500)
    g.connect("pressed", lambda *_a: on_hold())
    widget.add_controller(g)
    return g
