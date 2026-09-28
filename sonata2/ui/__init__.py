"""Sonata's design system: tokens + themed components. Every Sonata surface
builds its UI from here -- no colours, radii or fonts outside ui/tokens.py.
See docs/DESIGN.md.

    from sonata2 import ui
    ui.setup()                       # once per process, before widgets
    ui.menu.popup(widget, sections)
    ui.label.HoverLabel(widget, "Name")
    ui.controls.push_button("OK", cb, style="default")
    ui.dialog.alert(heading, body, responses, on_response)
    ui.window.traffic_lights(close, minimize, zoom)
"""
from . import controls, dialog, label, menu, panel, theme, tokens, window  # noqa: F401  (register CSS)
from .theme import force_appearance, is_dark, on_change, px, register, rgba, setup, shadow, values  # noqa: F401

__all__ = ["controls", "dialog", "label", "menu", "panel", "theme", "tokens", "window",
           "force_appearance", "is_dark", "on_change", "px", "register", "rgba", "setup", "shadow", "values"]
