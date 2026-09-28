# Devlog

## 2026-09-28 -- M0: project start

- Why a new project: Sonata (Quickshell on KWin, KWin script + Python D-Bus
  bridge for windows) gave poor design results; the GTK4/libadwaita UI built
  for LayerOSX is the look to keep.
- Bases checked:
  - Ignis rejected: its Python line stopped at v0.6.0 ("final release ...
    before the Rust rewrite"), API unstable, no compositor-agnostic window list.
  - Wayfire 0.11 (Jul 2026, Arch extra): animations, SSD decorations, IPC,
    layer-shell, foreign-toplevel. labwc kept as fallback (refuses
    animations, no IPC). Hyprland rejected (tiling-first, no native title bars).
  - gtk4-layer-shell 1.3.0 and python-pywayland 0.4.19 are in Arch extra.
- `sonata2/style.py` ported from LayerOSX `panel/layerosx_style.py`; the
  picom kick-window (X11 workaround) was dropped.

## 2026-09-28 -- M1: Dock first cut

- Order changed with Vini: Dock first, then the top bar. LayerOSX is the base
  (windows, style, Settings); Sonata only serves as a feature list.
- Look: Big Sur/Monterey, not Liquid Glass. 48 px icons, 18 px plate radius,
  5 px top padding, 9 px dot row (4 px dot), plate 4 px above the edge,
  13 px regular name label 8 px above the icon. No compositor blur yet, so
  the plate is more opaque than on macOS (0.78 light / 0.74 dark).
- Layer-shell: gtk4-layer-shell must load before libwayland-client, so
  `layer.ensure_preload()` re-execs once with LD_PRELOAD; without layer-shell
  the Dock is a plain window.
- Default pins: first installed .desktop per slot (Files, default browser,
  mail, calendar, music, images, editor, Steam, terminal) -> no broken icons
  on any distro.
- Screenshots: Xvfb has no alpha for popovers (black box around the label),
  so `tools/wl-preview.sh` runs the preview in headless sway + grim.
  The cloud test box's GTK 4.14 also ignored settings.ini's icon theme on
  Wayland; SONATA2_ICON_THEME sets it for previews.
- Not tested yet on hardware / real layer-shell (the test box has no
  gtk4-layer-shell).
