# Sonata 2 (pre-alpha)

**A modern desktop UI for Linux**: a complete shell, not a GTK theme. It
covers the login screen, menu bar, Dock, Launchpad, Search, notifications,
Control Center, a file manager and a Settings app. Everything is animated,
light and dark, and made to feel smooth on ordinary hardware.

The look comes from **themes**. The first theme is inspired by macOS
(Big Sur to Ventura: layout, glass, motion). A Windows 11-style theme with
a task bar comes next. Later you will be able to make your own. Every
colour, size, radius and animation timing lives in one place (design
tokens, [sonata2/ui/tokens.py](sonata2/ui/tokens.py)). A theme is a set
of those values, not a fork of the code.

Sonata runs on [Wayfire](https://github.com/WayfireWM/wayfire) with GTK 4
and libadwaita. It works on any distribution that ships them, and it
doesn't need GNOME or KDE.

> History (decisions, bugs, root causes): [DEVLOG.md](DEVLOG.md).
> Roadmap: [ROADMAP.md](ROADMAP.md). This README describes the current state.

![The Sonata desktop: menu bar, desktop icons and the Dock](docs/screenshots/desktop.png)
<sub>The desktop: menu bar, desktop icons and the Dock ([in Dark Mode](docs/screenshots/desktop-dark.png)).</sub>

![Launchpad: every app in a grid over the blurred desktop](docs/screenshots/launchpad.png)
<sub>**Launchpad**: every app over the blurred desktop, with search, pages and folders.</sub>

![Control Center open under the menu bar](docs/screenshots/controlcenter.png)
<sub>**Control Center**: Wi-Fi, Bluetooth, Dark Mode, display and sound, Now Playing.</sub>

![Files zoomed between the menu bar and the Dock](docs/screenshots/files-full.png)
<sub>**Files** with tabs; the menu bar follows the app in front.</sub>

![System Settings on the desktop](docs/screenshots/settings-full.png)
<sub>**System Settings**: Sonata's own settings and the Linux ones, side by side.</sub>

| | |
|---|---|
| ![Files, icon view with two tabs](docs/screenshots/files.png)<br>**Files**: icon view, tabs | ![Files in Dark Mode](docs/screenshots/files-dark.png)<br>**Files** in Dark Mode |
| ![Notes with a checklist](docs/screenshots/notes.png)<br>**Notes** | ![Notes in Dark Mode](docs/screenshots/notes-dark.png)<br>**Notes** in Dark Mode |
| ![Reminders: the Today list](docs/screenshots/reminders.png)<br>**Reminders**, in the Notes window | ![Calendar, month view](docs/screenshots/calendar.png)<br>**Calendar**: month view |
| ![Music, albums view](docs/screenshots/music.png)<br>**Music**: albums | ![Music in Dark Mode](docs/screenshots/music-dark.png)<br>**Music** in Dark Mode |
| ![Task Manager, Processes page](docs/screenshots/taskmanager.png)<br>**Task Manager**: processes | ![Task Manager, Performance page](docs/screenshots/taskmanager-performance.png)<br>**Task Manager**: performance |
| ![Task Manager in Dark Mode](docs/screenshots/taskmanager-dark.png)<br>**Task Manager** in Dark Mode | ![Disk Manager](docs/screenshots/diskmanager.png)<br>**Disk Manager** |
| ![TextEdit with three tabs](docs/screenshots/textedit.png)<br>**TextEdit**: tabs | ![Preview with the thumbnail sidebar](docs/screenshots/preview.png)<br>**Preview**: thumbnails |
| ![Settings, General page](docs/screenshots/settings.png)<br>**Settings**: General | ![Calculator](docs/screenshots/calculator.png)<br>**Calculator** |

<sub>Screenshots use invented demo data; `tools/readme-shots.py` makes them.</sub>

---

## Contents

- [Install](#install)
- [Use it](#use-it)
- [What's in it](#whats-in-it)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Settings: Sonata vs. Linux](#settings-sonata-vs-linux)
- [Themes and design system](#themes-and-design-system)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Project layout](#project-layout)
- [License](#license)

---

## Install

```
git clone https://github.com/vinioliveiras/sonata2.git && cd sonata2
./install.sh
```

The installer:

- **installs the dependencies** with your package manager
  (pacman/apt/dnf/zypper/xbps/apk). Optional extras are installed one by
  one, so a package your release lacks doesn't block the rest. pywayland
  comes from PyPI if the distro has none, and pixdecor (the title bars
  Sonata draws for other apps) comes from the AUR on Arch;
- installs Sonata for you (`~/.local`) and adds **"Sonata" to the login
  screen**;
- makes Sonata's Files the file manager of the session (folders, "Show in
  Folder" from browsers and editors);
- builds the small Wayfire plugin that rounds every window's corners;
- keeps the console quiet: no kernel or "[ OK ]" text on screen at boot,
  login and shutdown;
- asks once whether to use **Sonata's own login screen** (greetd in place
  of GDM/SDDM, from the next boot) and keeps it up to date after that;
- ends with `sonata2 doctor`: what's missing, and the command that fixes it.

| Command | What it does |
|---|---|
| `./install.sh` | install for you (asks for sudo only where needed) |
| `./install.sh --dev` | link to this clone instead of copying it: the session runs the code you edit |
| `./install.sh --system` | install for all users (`/usr/local`) |
| `./install.sh --yes` | don't ask (login screen included) |
| `./install.sh --no-deps` | don't install packages, only list what's missing |
| `./install.sh --no-greeter` | keep your current login screen |
| `./install.sh --gdm` | back to the previous login screen (GDM/SDDM...) |
| `./install.sh --uninstall` | remove Sonata (your settings stay in `~/.config/sonata2`) |

**Requirements:** Wayfire ≥ 0.9, GTK ≥ 4.12, libadwaita ≥ 1.4,
gtk4-layer-shell ≥ 1.0 and Python ≥ 3.10 with PyGObject, pycairo and
pywayland.

### Which distributions

Sonata is developed and tested on **CachyOS** (Arch). Other distributions
are judged by the versions they ship (September 2026). The ones built on
**Ubuntu 24.04** can't run it yet: that release has no gtk4-layer-shell,
and its Wayfire is 0.8.

| Distribution | Status | Notes |
|---|---|---|
| Arch, CachyOS, EndeavourOS, Manjaro, Garuda | ✅ works | the reference; everything, including pixdecor title bars (AUR) |
| Fedora 41 and newer | 🟡 should work | not tested yet |
| openSUSE Tumbleweed | 🟡 should work | not tested yet |
| Debian 13 "trixie", LMDE 7, MX Linux 25 | 🟡 should work | Wayfire 0.9: the rounded-corners plugin may not build (windows keep square corners) |
| Ubuntu 26.04 LTS and its flavours (Kubuntu, Xubuntu...) | 🟡 should work | Wayfire 0.10, gtk4-layer-shell 1.3; not tested yet |
| Ubuntu 25.10 | 🟡 should work | Wayfire 0.9 (same note as Debian 13) |
| **Linux Mint 22.x** | ❌ not yet | Ubuntu 24.04 base; wait for **Mint 23** (Ubuntu 26.04 base, planned for Christmas 2026) |
| Ubuntu 24.04 LTS, Pop!_OS 24.04, Zorin OS 18, elementary OS 8, KDE neon | ❌ not yet | Ubuntu 24.04 base: no gtk4-layer-shell, Wayfire 0.8 |
| Debian 12 "bookworm" | ❌ no | Wayfire 0.7, no gtk4-layer-shell |
| Void, Alpine | 🟡 should work | the installer knows their package names; not tested |
| NixOS, Gentoo, Slackware | ⚪ not by the installer | the dependencies exist; install them yourself and run `./install.sh --no-deps` |

What to expect outside Arch:

- **Title bars for other apps:** Sonata draws them with pixdecor, which is
  packaged only in the AUR. Elsewhere Wayfire's own simpler title bars are
  used, in Sonata's colours.
- **Rounded corners for every window:** the corner plugin is compiled
  against your Wayfire. If that fails, the installer goes on and windows
  keep square corners.
- **Sonata's login screen:** needs greetd. On a distribution that isn't
  tested yet, install with `--no-greeter` first. If the login screen
  doesn't start, you are left without a graphical login until you run
  `./install.sh --gdm` from a text console (Ctrl+Alt+F2).
- **Easiest way to try it:** a virtual machine (GNOME Boxes, VirtualBox)
  with CachyOS or Fedora. Blur and animations are slower there, but
  everything shows.

Found a problem on your distribution? Send `~/.cache/sonata2/doctor.txt`
(`sonata2 doctor` writes it) and the logs in that folder.

## Use it

1. Log out and pick **Sonata** on the login screen. With Sonata's login
   screen it is already chosen, and remembered per user.
2. After the password, a welcome screen stays up while the desktop loads.
   Then the wallpaper sharpens, the Dock rises from its edge and the menu
   bar slides down.
3. To go back, pick your old desktop on the login screen. `./install.sh
   --gdm` restores the old login screen itself.

**Try it without logging out:** `tools/dev-session.sh` runs Sonata inside
your current desktop, in a Wayfire window.

**Try the login screen:** `SONATA_GREETER_FAKE=1 sonata2 greeter`
(password `sonata`, Esc leaves).

**Apply changes to the shell:** Sonata menu (the logo at the top left) →
Restart Sonata, or `sonata2 restart`. Open apps stay open.

## What's in it

**Login and lock screen.** Your wallpaper blurred, your picture and name,
a password field that shakes on a wrong password, and a spinner while
logging in. Sleep, Restart and Shut Down are at the bottom. It also lists
other users and sessions, and has a display button: resolution at the
highest refresh rate, saved, confirmed within 15 s. The lock screen has
the same look.

**Menu bar.** Your distribution's logo (or a shape or symbol of your
choice) with the Sonata menu: About, Settings, Recent Items, Sleep,
Restart, Shut Down, Lock, Log Out, Restart Sonata. Next to it are the
active app's menus. On the right: Sound, Battery, Wi-Fi, Bluetooth,
Control Center and the clock, which opens Notification Center with the
calendar.

**Dock.** Frosted glass, magnification, auto-hide, left/bottom/right, and
one dot per open window.
- Clicking the app in front minimizes it.
- Right-click: New Window, the app's windows (with a close "x"), Keep in
  Dock, Open at Login, Show in Files.
- Drag to reorder, out to remove, files onto apps, the Trash or folders.
- Stacks (Downloads), recent apps, and a thumbnail on hover.
- It shrinks to fit the screen when full and grows back.

**Launchpad.** Full-screen grid over the blurred desktop, with pages,
search (just type), folders, jiggle mode, and dragging apps to the Dock.
New apps appear as soon as they're installed.

**Search.** Apps, files and settings (Super+Space).

**Files.**
- Icons, list and columns views, tabs, with Quick Look (Space), Get Info
  and live updates.
- Recents, Applications, pinned folders (drag a folder between two
  Favorites) and drives with capacity.
- Search in This Mac or the folder, type to select, and drag and drop
  everywhere.
- Double-clicking a Linux package installs it (with debtap on Arch), and
  archives are extracted.

**Settings.**
- Wi-Fi, Bluetooth, Sound (with a 10-band equalizer per output),
  Displays, Battery, Keyboard, Mouse and Trackpad, Wallpaper, Users,
  Notifications.
- Appearance: light/dark, accent, glass, icons, menu bar logo, title
  bars.
- Dock, Menu Bar, Launchpad, Sound Effects, Accessibility.
- There is a search field, and a double-click on any slider resets it.

**Sonata's own apps.** No GNOME apps needed for the everyday things:
- Calculator: the macOS Basic calculator, with the keyboard, copy and paste.
- TextEdit: plain text in tabs, with find, text size, encodings and line
  endings kept, syntax highlighting (with GtkSourceView), and nothing lost
  on quit: open tabs and unsaved text come back.
- Preview: pictures, with a thumbnail sidebar, zoom (pinch, Ctrl+wheel),
  rotate, crop, colour adjustments, slideshow and full screen.
- Terminal: your shell in tabs, with the macOS "Basic" colours in light
  and dark. It needs VTE for GTK 4 (`vte4`; install.sh adds it).
- Notes, with Reminders in the same window: folders, pinned notes,
  checklists and formatting; reminder lists with Today, Scheduled, due
  dates, flags and alerts.
- Calendar: local calendars in Day, Week, Month and Year views, with
  repeating events, alerts and .ics import.
- Music: the library in ~/Music, with albums, artists, songs, playlists,
  Up Next and media keys (MPRIS).
- Videos: a QuickTime-style player, with a floating control bar, and
  every movie resumes where you left it.
- Task Manager: processes grouped by app, performance graphs (CPU,
  memory, disks, network, GPU), app history, startup apps, users and
  services.
- Disk Manager: disks and volumes through UDisks: mount, unmount, eject,
  rename, erase, First Aid, encrypted volumes.
- Open and Save panels for every app (through the file chooser portal).

**Mission Control.** F3 or Ctrl+Up lays out the windows over the blurred,
darkened desktop; Ctrl+Super+Up shows every Space.

**Software Update.** Settings → Software Update lists what can be updated
(System, AUR, Flatpak) with versions, and updates in place.

**First login.** A short Setup Assistant: appearance, accent colour, a few
Sonata choices.

**Every app's window in the same style.** GTK apps use Sonata's GTK
theme. Chrome, Firefox, VS Code, terminals and X11 apps get Sonata's title
bar, drawn by the compositor, with rounded corners and shadows. App icons
Sonata has no artwork for sit on a squircle frame of their own colour.
They are generated once and kept (Settings → Appearance → Regenerate).

**Details.**
- Notification banners, with animations when they are dismissed.
- Volume and brightness indicators, Night Shift, screenshots and screen
  recording (Super+Shift+3/4/5), emoji picker, sound effects.
- One menu bar and wallpaper per display, and the highest refresh rate
  by default.

## Keyboard shortcuts

Super (the Windows key) plays the part of Cmd.

| Keys | Action |
|---|---|
| Super (alone), F4, pinch with 4 fingers | Launchpad |
| Super+Space | Search |
| Super+Tab / Super+Shift+Tab | switch apps |
| Super+Q, Alt+F4 / Super+M / Ctrl+Super+F | close / minimize / zoom |
| Ctrl+Up, F3, swipe up with 3 fingers | Mission Control |
| Ctrl+Down, swipe down with 3 fingers | App Exposé |
| swipe left/right with 3 fingers, Super+Alt+arrows | switch Spaces |
| Super+Shift+3 / 4, Print | screenshot (screen / selection) |
| Super+Shift+5 | capture toolbar (screenshots, screen recording) |
| Ctrl+Super+Space | emoji |
| Super+, | Settings |
| Ctrl+Super+Q | lock the screen |
| Ctrl+Alt+T | terminal (works even if the shell crashed) |

**Files:**

| Keys | Action |
|---|---|
| Enter / Enter twice / F2 | open / rename / rename |
| Backspace | back one folder |
| letters | select the first item starting with them |
| Space | Quick Look |
| Ctrl+I | Get Info |
| Ctrl+F | search |
| Ctrl+Shift+N | new folder |
| Ctrl+C / Ctrl+V / Ctrl+Alt+V | copy / paste / move |
| Ctrl+D | duplicate |
| Delete, Ctrl+Backspace | move to Trash |
| Ctrl+1/2/3 | Icons / List / Columns |
| Ctrl+Shift+. | hidden files |
| Ctrl+Shift+A | Applications |
| Ctrl+Shift+F | Recents |

## Settings: Sonata vs. Linux

Two separate worlds, so Sonata can be customized freely without touching
the system:

- **Sonata settings**: how the shell looks and behaves (Dock, glass,
  icons, animations, menu bar, Launchpad...). They are stored only in
  `~/.config/sonata2/*.json` and applied live by the running shell.
- **Linux settings**: the machine itself (Wi-Fi, Bluetooth, sound,
  displays, power, wallpaper, keyboard, users). Sonata stores nothing
  here. It uses the standard services (NetworkManager, PipeWire, UPower,
  BlueZ, AccountsService, logind), so these stay the same in any desktop.

The Settings app keeps this split in its sidebar.

## Themes and design system

- **Tokens** ([sonata2/ui/tokens.py](sonata2/ui/tokens.py)): colours,
  materials (glass tints), sizes, radii, font sizes and animation timings,
  for light and dark. Themes are entries in `THEMES`. Today there is
  `mac`; `windows` is next.
- **Components** ([sonata2/ui/](sonata2/ui)): menus, alerts, sliders,
  switches, windows, traffic lights and transitions. They are all styled
  from tokens, never from hard-coded values.
- **Rules**: [docs/DESIGN.md](docs/DESIGN.md). A standard spacing
  everywhere, responsive layouts that fit any screen size, and every
  change animated.
- **Gallery**: `sonata2 gallery [--dark]` shows every component.
- **Icons**: `Sonata` (our own) → `Sonata-MacTahoe` (bundled) → `hicolor`
  → the system theme, only for apps none of them has. Details:
  [sonata2/data/icons](sonata2/data/icons/README.md).

User-made themes will be a folder of token values plus optional artwork,
picked in Settings → Appearance.

## Troubleshooting

- **Logs**: `~/.cache/sonata2/` holds `session.log` (Wayfire), one log per
  component (`dock.log`, `topbar.log`...) and `login.log`.
- **`sonata2 doctor`**: checks the machine and writes `doctor.txt` with the
  last errors.
- **Something stuck**: Sonata menu → Restart Sonata (or `sonata2 restart`).
  Components also restart themselves if they crash.
- **The login screen doesn't start**: open a text console (Ctrl+Alt+F2), log
  in, run `./install.sh --gdm` in the Sonata folder, and restart.
- **An app's title bar looks off**: Settings → Appearance → "Sonata title
  bars for all apps" (off gives apps back their own frames).
- **Icons look wrong after an app update**: Settings → Appearance → App
  icons → Regenerate.
- **Fonts**: Sonata ships Inter. If you install Apple's SF Pro yourself
  (into `~/.local/share/fonts`), the session uses it from the next login.

## Development

```
./install.sh --dev                       # the session runs this clone
sonata2 restart [dock topbar ...]        # reload shell parts after editing
python3 -m sonata2 dock --preview        # a component in a normal window
tools/wl-preview.sh dock --dark          # screenshot in a headless compositor -> screenshots/
tools/dev-session.sh                     # the whole shell in a Wayfire window
xvfb-run -a -s "-screen 0 1920x1200x24" python3 tools/readme-shots.py   # README screenshots (demo data) -> docs/screenshots/
```

- **Tests without a display**: `python3 -m unittest tests.test_launchpad_model
  tests.test_autostart`.
- **Tests that need a display**: `tests.test_dock`, `tests.test_launchpad`,
  `tests.test_topbar`, `tests.test_settings`, `tests.test_files`. Run each
  module on its own.

Each shell component is its own process (a crash doesn't take the others
with it). They talk through D-Bus and the config files.

**Stack:**

| Layer | Choice | Why |
|---|---|---|
| Compositor | Wayfire (+ pixdecor, + Sonata's corner plugin) | animations, blur, server-side decorations, IPC, layer-shell |
| UI | Python 3, PyGObject, GTK 4, libadwaita | one toolkit for the shell and the apps |
| Panels | gtk4-layer-shell | menu bar, Dock, overlays anchored to the screen |
| Windows | wlr-foreign-toplevel via pywayland | works on any wlroots compositor |
| Login | greetd | small, any distro, no desktop dependency |

## Project layout

```
sonata2/            Python package (`python3 -m sonata2 <component>`)
  ui/               design system: tokens, theme, menus, controls, windows, logo
  shell/            menu bar, Dock, Launchpad, Search, notifications, login,
                    lock and welcome screens, wallpaper, desktop icons
  files/            the Files app
  settings/         the Settings app
  backend/          Linux services: network, audio (equalizer), power, displays, users
  wl/               Wayland protocol clients
  data/             icons, cursors, GTK themes, fonts, sounds, title bar artwork
config/             the session's Wayfire config
wayfire-plugin/     sonata-corners (rounded corners for every window)
tools/              session scripts, installer helpers, previews
tests/              unit and UI tests
docs/               design system, feature parity
```

Sonata succeeds [Sonata](https://github.com/vinioliveiras/sonata)
(Quickshell on KWin) and started from the GTK4 look of
[LayerOSX](https://github.com/vinioliveiras/LayerOSX).

## License

Free to use, study, modify and share, for any noncommercial purpose:
[PolyForm Noncommercial 1.0.0](LICENSE.md). Nobody may sell Sonata or
use it commercially. The icon themes, cursors, fonts and GTK theme it
bundles keep their own licenses (listed at the end of LICENSE.md).
