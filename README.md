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

Skeleton only: repository layout and the shared style module (ported from
LayerOSX). Nothing runs yet -- the top bar is the first milestone
(see [ROADMAP.md](ROADMAP.md)).

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
  style.py        shared CSS, theme, traffic lights
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
```
