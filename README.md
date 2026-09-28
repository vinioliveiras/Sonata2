# Sonata 2 (pre-alpha)

A macOS-style desktop **shell** for Linux -- top bar, dock, launcher, control
center, notifications and a Settings app -- not a theme. Big Sur is the
reference for layout and system icons; app icons are MacTahoe-based. A
Windows 11 preset ("Task Bar" mode, vs. "Dock" mode) comes later.

It succeeds [Sonata](https://github.com/vinioliveiras/sonata) (Quickshell on
KWin) and reuses the GTK4/libadwaita look of
[LayerOSX](https://github.com/vinioliveiras/LayerOSX).

> **History:** decisions, bugs and root causes live in [DEVLOG.md](DEVLOG.md).
> This README describes the current state only.

## Status

**Dock (first cut):** Big Sur-style plate with the pinned apps, a separator
and the Trash (empty/full icon follows `~/.local/share/Trash`). Hover shows
the app name above the icon; click launches it with a bounce. Light/dark
follows the system appearance. Not yet: running-app dots and window
switching (next milestone), right-click menu, drag to reorder.
See [ROADMAP.md](ROADMAP.md).

The look is macOS Big Sur/Monterey -- deliberately **not** Liquid Glass.

## Run (development)

```
python3 -m sonata2 dock              # on Wayland: layer-shell surface at the bottom edge
python3 -m sonata2 dock --preview    # in a normal window over a sample wallpaper
tools/wl-preview.sh dock --dark --label 1   # screenshot in headless sway -> screenshots/
```

Options: `--dark` / `--light` force the appearance, `--label N` keeps the
N-th name label visible. Pins, icon size and edge gap are in
`~/.config/sonata2/dock.json` (created on first run from the installed apps).

## Stack

| Layer | Choice | Why |
|---|---|---|
| Compositor | [Wayfire](https://github.com/WayfireWM/wayfire) (labwc as fallback) | Animations, custom server-side decorations, IPC, layer-shell, wlr-foreign-toplevel |
| Shell UI | Python 3 + PyGObject + GTK4 + libadwaita | Same code base as the LayerOSX Settings/dialogs |
| Panels | [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) | Top bar / dock anchored to screen edges |
| Window list | wlr-foreign-toplevel via [pywayland](https://github.com/flacjacket/pywayland) | Compositor-agnostic (any wlroots compositor), no D-Bus bridge |

Portability: only these packages are required, and all major distros ship
them. No distro-specific paths or tools.

## Layout

```
sonata2/          Python package
  __main__.py     entry point (`python3 -m sonata2 dock`)
  style.py        shared CSS, theme, traffic lights (from LayerOSX)
  config.py       JSON settings in ~/.config/sonata2
  apps.py         .desktop lookup, default Dock pins
  shell/          top bar, dock, launcher, control center, notifications
  wl/             Wayland protocol clients (foreign-toplevel)
  backend/        system backend (network, power, audio, displays)
  settings/       the Settings app
config/           default Wayfire config
session/          display-manager session entry
docs/PARITY.md    macOS / Windows 11 feature parity list (from Sonata)
tests/  tools/    headless tests, screenshot helpers
```

## Dependencies (Arch / CachyOS)

```
sudo pacman -S --needed wayfire gtk4 libadwaita gtk4-layer-shell python-gobject python-pywayland
# screenshots only: sway grim
```
