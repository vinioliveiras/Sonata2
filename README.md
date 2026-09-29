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

**Dock (in progress):** Big Sur-style frosted-glass plate (the compositor
blurs the wallpaper behind it) with the pinned apps, running apps, a
separator and the Trash (empty/full icon follows `~/.local/share/Trash`).
- Hover shows the app name above the icon.
- A dot marks running apps; running apps that aren't pinned appear after the
  pinned ones and leave when they quit.
- Click: brings the app's windows to the front (restores them if all are
  minimized), or launches it -- the icon bounces until its first window
  appears.
- Windows minimize into their Dock icon (Wayfire `squeezimize`).
- Light/dark follows the system appearance.

Window tracking uses wlr-foreign-toplevel, so it works on any wlroots
compositor.
- Right-click (Big Sur menu): open windows, Options > Keep in Dock / Open at
  Login / Open File Location, Hide / Quit (or Open). Trash: Open, Empty Trash.
  "Open File Location" shows the app's real executable (AppImage, binary,
  script) selected in the file manager; Flatpak/Snap apps show their
  .desktop file.
- Drag an icon to reorder (saved); drag it out of the Dock to remove it
  (a running app stays until it quits); dropping a running unpinned app
  among the icons pins it.

- Drop files on an app to open them with it (the icon darkens only if the
  app handles those file types), on the Trash to trash them; drop an app's
  .desktop file on the Dock to pin it at that spot.

- Magnification (off by default, like macOS): icons near the pointer grow
  in a smooth wave above the plate. Right-click the divider (line before
  the Trash) -> Turn Magnification On. Drag the divider up/down to resize.

- Divider menu: Turn Hiding On (auto-hide: slides out, comes back when the
  pointer touches the screen edge), Magnification, Position on Screen
  (Left / Bottom / Right), Show Recent Applications.
- Recent apps: the last 3 unpinned apps you used, after a second bar.
- Stacks: Downloads right of the divider (drop any folder on the Dock to add
  one); click for a Grid/List panel, right-click for sort/display options.

Remaining: hardware polish (multi-monitor, HiDPI) -- see
[ROADMAP.md](ROADMAP.md).

The look is macOS Big Sur/Monterey -- deliberately **not** Liquid Glass.

## System Settings

`python3 -m sonata2 settings [--page dock]` (also in Launchpad and the
Sonata menu): macOS System Settings layout. Linux sections (Wi-Fi,
Bluetooth, Sound, Displays, Battery, Wallpaper) change the system;
Sonata sections (Appearance, Desktop & Dock, Menu Bar, Launchpad) write
~/.config/sonata2 and the running shell applies them immediately.

## Windows

Inside a Sonata session every GTK3/GTK4/libadwaita app gets Big Sur windows:
traffic lights on the left, Big Sur title bars, controls and colours
(bundled `Sonata-Light/Dark`, from WhiteSur -- sonata2/data/themes), Sonata
icons and cursors. `tools/session-env.sh` sets this up **only for the
session** (GTK_THEME, XDG_DATA_DIRS, a Sonata-only dconf layer in
~/.config/dconf/sonata) -- GNOME/KDE keep their own look. Apps that don't
draw title bars get Wayfire's (config/wayfire.ini [decoration]).


## Menu bar

`python3 -m sonata2 topbar`: Big Sur menu bar -- Sonata menu (logo), the
active app's name with About/Hide/Quit, Window menu, and on the right
Sound, Battery, Wi-Fi, Control Center and the clock (click: calendar).
Linux state comes from NetworkManager (nmcli), PipeWire (wpctl),
brightnessctl, bluetoothctl and sysfs (backend/system.py, ported from
LayerOSX). Optional tools: `networkmanager wireplumber brightnessctl bluez-utils`.

## Launchpad

`python3 -m sonata2 launchpad` (again to close; F4 in the Wayfire config).
Full-screen app grid over the blurred desktop, like Big Sur: pages, search
(just type), folders (hold an app over another), drag to reorder or onto
the Dock, press and hold an icon (or hold Alt) to jiggle -- shortcuts you
installed yourself get a delete badge (moved to the Trash). Layout is saved
in `~/.config/sonata2/launchpad.json`. The Dock gets a Launchpad icon after
Finder on its first run with this version.

## Install

```
./install.sh            # for you (~/.local), with every dependency (asks your sudo password)
./install.sh --system   # for all users (/usr/local)
./install.sh --no-deps  # don't install packages, only list what's missing
./install.sh --uninstall
```

Then log out and pick **Sonata** on the login screen. The session runs
Wayfire with `~/.config/sonata2/wayfire.ini` (your own
`~/.config/wayfire.ini` is never touched; an edited Sonata config is kept on
reinstall), starts the wallpaper, Dock, menu bar, Launchpad and your
"Open at Login" apps, a polkit agent if one is installed, and hands the
session environment to portals.

Developing inside a real session: `./install.sh --dev` links the installed
Sonata to this clone (no copy), so the session runs the code being edited;
`sonata2 restart [dock topbar launchpad wallpaper]` reloads the shell parts
(apps like Files/Settings: just reopen them).

Keys (macOS-like, Super = Cmd):

| Keys | Action |
|---|---|
| Super+Space | Spotlight |
| F4, pinch (4 fingers) | Launchpad |
| Super+Tab | switch apps |
| Super+Q / Super+M / Ctrl+Super+F | close / minimize / zoom |
| Ctrl+Up, F3, swipe up (3 fingers) | Mission Control |
| Ctrl+Down, swipe down (3 fingers) | App Exposé (all windows) |
| swipe left/right (3 fingers), Super+Alt+arrows | switch Spaces |
| Super+Shift+3 / Super+Shift+4, Print | screenshot (whole / selection) to the Desktop, floating thumbnail |
| Super+Shift+5 | capture toolbar: screenshots, screen recording (wf-recorder), Options |
| Ctrl+Super+Space | emoji (Character Viewer) |
| volume / brightness keys | with the Big Sur HUD |
| Super+, | System Settings |
| Ctrl+Super+Q | lock screen |
| Ctrl+Alt+T | terminal (handled by Wayfire: works even if the Sonata shell crashed) |

Files: Ctrl+F search (This Mac or the folder), Space Quick Look, Return rename, Ctrl+I Get Info, Ctrl+Shift+N new
folder, Ctrl+C/V copy/paste (Ctrl+Alt+V move), Ctrl+D duplicate,
Delete/Ctrl+Backspace Trash, Ctrl+1/2/3 views, Ctrl+Shift+. hidden files.

Font: Sonata ships Inter (free). If you install Apple's SF Pro yourself (developer.apple.com/fonts, into ~/.local/share/fonts), the session uses it at the next login; Apple's licence doesn't allow bundling it.

## Use it as your desktop

```
./install.sh --dev            # from this clone, with every dependency
sonata2 doctor                # what's missing, with the command that fixes it
```

Then log out and pick **Sonata** on the login screen. The shell components
restart themselves if one crashes (`sonata2 keep`). Something wrong? The
logs are in `~/.cache/sonata2/` (`login.log`, `session.log`), and
`sonata2 doctor` adds the last session's errors to `doctor.txt` there.
The keyboard layout comes from the system (`localectl`) until you pick
one in Settings > Keyboard. To go back, pick your old desktop on the
login screen; `./install.sh --uninstall` removes Sonata (settings stay).

The installer also keeps the console quiet (no kernel messages or
"[ OK ]" lines on screen at boot, login and shutdown; `--uninstall`
undoes it) and offers Sonata's own login screen (greetd in place of
GDM/SDDM, from the next boot; same look as the lock screen). Try it first
inside the session with `SONATA_GREETER_FAKE=1 sonata2 greeter` (password
`sonata`, Esc leaves). `./install.sh --gdm` puts the old login screen
back; if the login screen ever fails to start, do that from a text
console (Ctrl+Alt+F2) and restart.

## Try it without logging out

`tools/dev-session.sh` opens Wayfire **as a window inside your current
desktop** with the Sonata shell from this clone (needs the dependencies
above; `python-pillow` for a sample wallpaper).

Tests: `python3 -m unittest tests.test_launchpad_model tests.test_autostart`
(no display); `python3 -m unittest tests.test_dock tests.test_launchpad
tests.test_topbar tests.test_settings` (need a display).
Design system gallery: `python3 -m sonata2 gallery [--dark]`.

## Run (development)

```
python3 -m sonata2 dock              # on Wayland: layer-shell surface at the bottom edge
python3 -m sonata2 dock --preview    # in a normal window over a sample wallpaper
tools/wl-preview.sh dock --dark --label 1   # screenshot in headless sway -> screenshots/
```

Options: `--dark` / `--light` force the appearance, `--label N` keeps the
N-th name label visible. Pins, icon size and edge gap are in
`~/.config/sonata2/dock.json` (created on first run from the installed apps);
`"glass": false` makes the plate nearly opaque (for compositors without blur).

For the glass effect and minimize-into-icon, merge
[config/wayfire.ini](config/wayfire.ini) into `~/.config/wayfire.ini`.

`tools/wl-preview.sh` extras: `PREVIEW_APPS="firefox org.gnome.Console"`
opens test windows with those app_ids (running dots);
the preview uses Sonata's icons like the real shell.

## Settings: Sonata vs. Linux

Two separate worlds, so Sonata can become fully customizable without
touching the system:

- **Sonata settings** -- how the shell looks and behaves (Dock, icons,
  glass, animations, later the top bar, Launchpad...). Stored only in
  `~/.config/sonata2/*.json` and read only by Sonata; never taken from
  GNOME/KDE/gsettings.
  - `dock.json`: pins, icon_size, edge_gap, glass, magnification,
    magnified_size, position, autohide, autohide_delay_ms, show_recents,
    recent, stacks
  - `appearance.json`: `icon_theme` (default `Sonata`), `theme` (`mac`;
    a `windows` theme comes later)
- **Linux settings** -- the machine itself: Wi-Fi, Bluetooth, sound,
  displays, power, wallpaper, keyboard... Sonata doesn't store them; it
  reads/writes the standard system services (NetworkManager, PipeWire,
  UPower...), so they stay the same in any desktop.

The Settings app (later) keeps the same split in its sidebar.

## Icons

Sonata has its own icons, separate from the system icon theme:
`Sonata` (our overrides, e.g. the round Trash) -> `Sonata-MacTahoe`
(bundled, pinned) -> `hicolor` -> the system theme only for apps none of
them has. Details: [sonata2/data/icons](sonata2/data/icons/README.md).

## Stack

| Layer | Choice | Why |
|---|---|---|
| Compositor | [Wayfire](https://github.com/WayfireWM/wayfire) (labwc as fallback) | Animations, custom server-side decorations, IPC, layer-shell, wlr-foreign-toplevel |
| Shell UI | Python 3 + PyGObject + GTK4 + libadwaita | Same code base as the LayerOSX Settings/dialogs |
| Panels | [gtk4-layer-shell](https://github.com/wmww/gtk4-layer-shell) | Top bar / dock anchored to screen edges |
| Window list | wlr-foreign-toplevel via [pywayland](https://github.com/flacjacket/pywayland) | Compositor-agnostic (any wlroots compositor), no D-Bus bridge |

Portability: only these packages are required, and all major distros ship
them. No distro-specific paths or tools. Minimums: Wayfire 0.9, GTK 4.12,
libadwaita 1.4, gtk4-layer-shell 1.0, Python 3.10 (developed on the newest:
GTK 4.24 / libadwaita 1.10). Known good: Arch and derivatives (CachyOS,
EndeavourOS, Manjaro), Fedora 41+, Debian 13+, Ubuntu 25.04+ and
derivatives, openSUSE Tumbleweed. `./install.sh` installs what's
missing with pacman/apt/dnf/zypper/xbps/apk (optional extras one by one, so a
package a release lacks doesn't block the rest; pywayland from PyPI when the
distro has none). Too-old releases (e.g. Debian 12, Ubuntu 24.04: no
gtk4-layer-shell) are reported.

## Layout

```
sonata2/          Python package
  __main__.py     entry point (`python3 -m sonata2 dock`)
  ui/             design system: tokens, theme, menu, hover label,
                  controls, alert, traffic lights, gallery (docs/DESIGN.md)
  config.py       Sonata settings (JSON in ~/.config/sonata2)
  icons.py        Sonata's own icon lookup (bundled themes, system fallback)
  data/icons/     bundled icon and cursor themes (Sonata, Sonata-MacTahoe, Sonata-Cursors)
  apps.py         .desktop lookup, default Dock pins
  shell/          top bar, dock, launcher, control center, notifications
  wl/             Wayland protocol clients (foreign-toplevel, on GTK's
                  own connection; bindings generated by pywayland at runtime)
  backend/        system backend (network, power, audio, displays)
  settings/       the Settings app
config/           default Wayfire config
session/          display-manager session entry
docs/DESIGN.md    design system rules, tokens, components
docs/PARITY.md    macOS / Windows 11 feature parity list (from Sonata)
tests/  tools/    headless tests, screenshot helpers
```

## Dependencies (Arch / CachyOS)

```
sudo pacman -S --needed wayfire gtk4 libadwaita gtk4-layer-shell python-gobject python-cairo python-pywayland
# optional: networkmanager wireplumber brightnessctl bluez-utils wlr-randr power-profiles-daemon
# previews/screenshots only: sway grim python-pillow
```
