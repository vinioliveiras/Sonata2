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
    ui.drag.hang(drag, paintable, size)   # drag icon swinging from the pointer
    ui.progress.bar(0.4) / spinner() / meter(0.7) / start(title, on_cancel)
    ui.transition.CrossFade(child)        # .capture() / .play() around an in-place change
    ui.transition.glide_record / glide_play  # re-ordered items slide to their new place
    ui.columns.fill_last(column_view)     # list columns keep their widths; the last fills
"""
from . import columns, controls, dialog, drag, fmt, label, menu, panel, progress, theme, tokens, transition, window  # noqa: F401  (register CSS)
from .theme import force_appearance, is_dark, on_change, px, register, rgba, setup, shadow, values  # noqa: F401

__all__ = ["columns", "controls", "dialog", "drag", "fmt", "label", "menu", "panel", "progress", "theme", "tokens", "transition",
           "window",
           "force_appearance", "is_dark", "on_change", "px", "register", "rgba", "setup", "shadow", "values"]
