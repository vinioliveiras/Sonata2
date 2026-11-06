# Sonata design system

Reference: macOS Big Sur/Monterey (Apple HIG) -- never Liquid Glass.
Code: the `sonata-ui` crate ([`rust/crates/sonata-ui/`](../rust/crates/sonata-ui)).
Gallery: `sonata2 gallery [--dark]`.

## Rules

1. **Every visual value comes from the tokens** (`rust/crates/sonata-ui/tokens.json`).
   No colour, radius, font, shadow or timing literal anywhere else. A new
   need = a new token.
2. **Build UI from `sonata-ui` components.** A new kind of control is added to
   `sonata-ui` (with its CSS, a constructor, a gallery row and a section below)
   before a surface uses it. Never use GTK/libadwaita widgets or system
   dialogs that bring their own look (Gtk.ColorDialog, Gtk.FileDialog's
   own chrome, Adw.AlertDialog...): they come out in another theme. Alerts
   are `sonata_ui::dialog`.
3. **Light/dark is automatic.** Components never check the appearance;
   `sonata_ui::theme` refills all CSS with the other palette on change.
4. **Component parity with macOS.** Each shell component ships with the
   full macOS behaviour: interactions, animations, drag and drop, menus,
   keyboard -- tracked in ROADMAP before it's called done.
5. **Reuse before you build (Vini).** Before writing anything, look for
   what already exists -- functions, layout patterns, animations, the way
   a component behaves -- and use it. Never duplicate code: a variant of
   something that exists is the same component with another layout (the
   Apps Menu is Launchpad's own page grids, tiles, search, keys and drags in
   a panel; only its window's look is its own). If the
   thing you need is almost there, extend it in place.
6. Sonata settings (look/behaviour) live in `~/.config/sonata2`; Linux
   settings stay in the system (see README).

## Themes

Two visual themes are planned: **mac** (now) and **windows** (later, with
the "Task Bar" mode). Each is a set of light / dark token tables in
`tokens.json`; components read tokens only, so a new theme is new values,
not new code. Selected in `~/.config/sonata2/appearance.json` (`theme`).

## Tokens (`tokens.json`)

`rust/crates/sonata-ui/tokens.json` is where a value is changed: `light`,
`dark`, `accents`, `animation_speed` and `frame`. `sonata_ui::tokens` reads
it (built into the binary). The CSS templates are
`rust/crates/sonata-ui/css.json`: one list of blocks (`key`, `css` with
`%(token)s` placeholders, `local` values) per module, filled from the tokens
into one CSS provider that is refilled when the appearance changes. Both
files were exported from the Python Sonata while it existed; they are the
sources now (the module keys keep the Python file names, e.g.
`"clock/window.py"`, because `sonata_ui::css::register_module` uses them).

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
| Window frame | `FRAME`: corners + shadow (`radius` 10, `shadow_radius`, `shadow_color`), buttons (`buttons` order from the edge inwards, `buttons_side` left/right -- both the user's in `frame()`, `dot` 12, `dot_gap` 8, `dot_left` 13, `dot_top` 14), title (`title_h` 52, `title_align`, `title_font`), Wayfire's own bars (`fallback_title_h`, `fallback_font`); `TL_COLORS`. `r_window`, `button_layout()` and the GSettings button layout / title font follow it |

**Window frame: one place.** Change `frame` in tokens.json and every window
follows: Sonata's own (`sonata_ui::window`), the title bars Wayfire draws (pixdecor,
sonata-corners, Wayfire's own bars: set by `sonata2 wayfire-config frame` at
login), other GTK 4 apps (adwstyle), GTK/GNOME apps' button layout (prefs).
(`sonata-backend`'s wfconfig keeps copies of the frame numbers; a test keeps
them equal to tokens.json.) A Windows-style theme is a different FRAME
(buttons on the right, title on the left...); Sonata's own windows still place
their traffic lights on the left by code. The dot pictures are files: run `tools/gen-decor.py` after
changing `dot` or the colours (a test fails until they match).

## Components

| Module | Component | Use |
|---|---|---|
| `ui/menu.py` | Context menu / menu popover: sections, check items, disabled items, side submenus (NESTED); a row's text ends in "…" past `LABEL_CHARS` (40), whole text as its tooltip | `menu.popup(widget, [[Item(...)], ...])` |
| `ui/label.py` | Hover label (Dock name bubble, tooltips) | `HoverLabel(widget, "Name")` |
| `ui/controls.py` | Push button (plain / default / destructive), pop-up button with the accent chevron cap, switch | `push_button`, `popup_button`, `switch` |
| `ui/dialog.py` | Alert (bold heading, body, Cancel default for destructive actions; optional "Apply to All" checkbox) | `alert(heading, body, responses, on_response, check=None)` |
| `ui/progress.py` | Progress bar (6 px, accent; indeterminate pulse), spinner, capacity meter (4 px, red above 90 %), Finder "Copy" window of running operations (shows after 0.7 s, stop buttons, "12 MB of 140 MB — About 5 seconds") | `bar(f)`, `spinner()`, `meter(f)`, `start(title, on_cancel).update(done, total)` |
| `ui/fmt.py` | Finder formats: sizes ("12 KB"), remaining time ("About a minute") | `fmt.size(n)`, `fmt.eta(s)` |
| `ui/controls.py` | Text field (one line; `secret=True`: password with the peek eye), text area (rounded, grows with its text up to `max_height`, placeholder, optional trailing widget; Return submits, Shift+Return new line), round accent icon button | `text_field(text, placeholder, secret, on_activate, on_change)`, `TextArea(placeholder, trailing=round_button(icon, tip, cb), on_submit=cb)` |
| `ui/fixed.py` | Fixed-width container (panels), centred reading column of at most N px (chat, documents) | `FixedWidth(child, 320)`, `MaxWidth(child, 720)` |
| `ui/window.py` | Traffic lights (close / minimize / zoom, zoom greyed for fixed windows), in the user's side and order (`tokens.frame()`) | `traffic_lights(close, minimize, zoom=None)` |
| `ui/window.py` | Button order picker: the three dots, dragged to another place (Settings > Appearance > Button order) | `order_lights(order, right, on_change)` |

The table names the Python modules the components were ported from; their
Rust counterparts are the `sonata-ui` modules of the same name (`menu`,
`label`, `controls`, `dialog`, `progress`, `fmt`, `fixed`, `window`).
Shell components (Dock, Launchpad, ...) register their CSS with
`sonata_ui::css::register_module("<module>")` (css.json) or
`sonata_ui::theme::register(key, template)`, using the same tokens.

## Adding a component

1. CSS template with `%(token)s` placeholders: a block in css.json (or
   `sonata_ui::theme::register(...)`).
2. Constructor in the same `sonata-ui` module; plain GTK widgets underneath.
3. A row in `sonata-ui`'s gallery (`sonata2 gallery`); screenshot light + dark.
4. A section in this file.

## Principles

- **Independent look.** A first Sonata login looks right whatever GNOME/KDE
  themes, fonts or scaling the user has: themes, icons, cursors (Sonata-Cursors)
  and fonts (Inter) are bundled; the session seeds every visual gsettings key
  into its own dconf layer once (`tools/session-env.sh` through
  `sonata2 prefs session-env`), forces the Qt platform theme, and never reads
  ~/.config/wayfire.ini. Known leak: a user `~/.config/gtk-4.0/gtk.css`
  still applies to third-party GTK4 apps (Sonata's own UI is above it).
- **Every click animates (Vini).** Anything clickable gives press feedback
  and eases between states: hover/selection colours transition
  (`t_fast`), pressed state darkens quickly (`t_press`) -- icons darken
  like Finder's, buttons/rows get the pressed fill. Surfaces open/close
  with their own animation (menus, Launchpad zoom, Dock bounce).
- **Low CPU, even with animations.** Animate only transform/opacity or
  snapshot-painted values; drive them with Adw.TimedAnimation/frame clock
  (no timers polling); stop every animation/tick callback when idle; no
  work while hidden (auto-hidden Dock, closed Launchpad); compositor effects
  (blur) only on shell surfaces; profile new components idle (0% CPU) and
  during animation.
