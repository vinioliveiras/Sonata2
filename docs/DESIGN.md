# Sonata design system

Reference: macOS Big Sur/Monterey (Apple HIG) -- never Liquid Glass.
Code: [`sonata2/ui/`](../sonata2/ui). Gallery: `python3 -m sonata2 gallery`
(`tools/wl-preview.sh gallery --dark --menu 0 --submenu` for a screenshot).

## Rules

1. **Every visual value comes from `ui/tokens.py`.** No colour, radius,
   font, shadow or timing literal anywhere else. A new need = a new token.
2. **Build UI from `ui` components.** A new kind of control is added to
   `ui/` (with its CSS, a constructor, a gallery row and a section below)
   before a surface uses it.
3. **Light/dark is automatic.** Components never check the appearance;
   `ui/theme.py` refills all CSS with the other palette on change.
4. **Component parity with macOS.** Each shell component ships with the
   full macOS behaviour: interactions, animations, drag and drop, menus,
   keyboard -- tracked in ROADMAP before it's called done.
5. Sonata settings (look/behaviour) live in `~/.config/sonata2`; Linux
   settings stay in the system (see README).

## Themes

Two visual themes are planned: **mac** (now) and **windows** (later, with
the "Task Bar" mode). Each is a set of LIGHT / DARK / SHARED token dicts in
`tokens.THEMES`; components read tokens only, so a new theme is new values,
not new code. Selected in `~/.config/sonata2/appearance.json` (`theme`).

## Tokens (`ui/tokens.py`)

| Group | Tokens |
|---|---|
| Text | `label`, `label_secondary`, `label_tertiary`, `label_on_accent` |
| Lines | `separator`, `hairline` (0.5 px outline of floating surfaces), `highlight` (inner edge) |
| Accent | `accent` (#007AFF / #0A84FF), `accent_selected` (menu selection), `destructive` |
| Materials | `menu_bg`, `window_bg`, `control_bg`, `control_pressed`, `control_off`, `knob`, `glass_tint` (blurred by the compositor), `solid_tint` (no blur), `indicator`, `tl_disabled` |
| Shadows | `shadow_menu`, `shadow_label`, `shadow_plate`, `shadow_control`, `shadow_knob` |
| Type | `font` (SF Pro Text -> Inter), `font_display`, `text_small` 11, `text_body` 13, `text_title` 15 |
| Radii | `r_button` 5, `r_label` 6, `r_menu` 7, `r_menu_row` 4, `r_window` 10, `r_dialog` 12, `r_plate` 18 |
| Sizes | `control_h` 22, `switch_w` 32, `switch_h` 18, `menu_min_w` 190 |
| Motion | `t_press` 80 ms, `t_fast` 150 ms, `t_standard` 250 ms |
| Spacing (layout code) | `SPACE`: xxs 2, xs 4, s 6, m 8, l 12, xl 16, xxl 20 |

## Components

| Module | Component | Use |
|---|---|---|
| `ui/menu.py` | Context menu / menu popover: sections, check items, disabled items, side submenus (NESTED) | `menu.popup(widget, [[Item(...)], ...])` |
| `ui/label.py` | Hover label (Dock name bubble, tooltips) | `HoverLabel(widget, "Name")` |
| `ui/controls.py` | Push button (plain / default / destructive), pop-up button with the accent chevron cap, switch | `push_button`, `popup_button`, `switch` |
| `ui/dialog.py` | Alert (bold heading, body, Cancel default for destructive actions) | `alert(heading, body, responses, on_response)` |
| `ui/window.py` | Traffic lights (close / minimize / zoom, zoom greyed for fixed windows) | `traffic_lights(close, minimize, zoom=None)` |

Shell components (Dock, Launchpad, ...) register their own CSS through
`ui.register(template, key=..., **geometry)` using the same tokens.
Widgets that paint in `do_snapshot` use `ui.rgba()`, `ui.shadow()`,
`ui.px()` and redraw on `ui.on_change()`.

## Adding a component

1. CSS template with `%(token)s` placeholders -> `theme.register(...)`.
2. Constructor/class in the same module; plain GTK widgets underneath.
3. A row in `ui/gallery.py`; screenshot light + dark.
4. A section in this file.
