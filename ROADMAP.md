# Roadmap

Base: LayerOSX code (style, Settings, backend). Sonata is only a feature
list: [docs/PARITY.md](docs/PARITY.md).

- **M0 -- Skeleton** *(done)*: repo layout, style module ported from LayerOSX.
- **M1 -- Dock** (finish it completely -- full macOS behaviour, animations,
  drag and drop -- before the next component):
  - [x] M1.1 pinned apps, Trash, hover labels, launch bounce
  - [x] M1.2 frosted glass (Wayfire blur), running dots, unpinned running
        apps, click to activate/restore, minimize into the icon
  - [x] M1.3 right-click menus (windows, Options > Keep in Dock / Open at
        Login / Open File Location, Hide, Quit / Open; Trash: Open, Empty)
  - [x] M1.4 drag: reorder, drag out to remove, running app dropped in
        place gets pinned, drop files on an app (open with, icon darkens
        only if it can open them) or on the Trash (move to Trash), drop an
        app (.desktop) anywhere on the Dock to pin it there
  - [x] M1.5a magnification (animated cosine wave, icons grow above the
        plate), drag the divider to resize (16-128 px), divider menu
        (Turn Magnification On/Off)
  - [ ] M1.5b position left/bottom/right, auto-hide (slide, delay),
        divider menu: Turn Hiding On/Off, Position on Screen
        (All Dock options will also be in the macOS-style Settings app, M6.)
  - [ ] M1.6 recent apps section, Stacks (Downloads folder: fan/grid)
  - [ ] M1.7 polish on hardware: multi-monitor, HiDPI, performance
  - Later (needs other components): notification badges, attention bounce
    (M5), Show All Windows (Wayfire scale IPC), minimized-window tiles
  - Rule (Vini): no fixed icons. Finder (file manager) and Launchpad are
    ordinary tiles: movable, removable, same menu options.
- **M2 -- Launchpad** (next, before the top bar): full-screen app grid over
  the blurred desktop, pages + page dots, search, folders (drag onto each
  other), drag to reorder / to the Dock, jiggle mode to delete, open/close
  animations, keyboard navigation.
- **M3b -- Top bar**: Apple-style logo menu (left), clock + status icons
  (right), Big Sur metrics (24 px, translucent), light/dark.
- **M3 -- Session**: Wayfire config (animations, decorations with traffic
  lights), session `.desktop`, `install.sh` (`~/.local` or `/usr/local`).
  Apps inside the Sonata session also use Sonata's icons: install links
  the bundled themes into the icon path and the session (only the Sonata
  session) sets them for apps.
- **M4 -- Control Center**: Wi-Fi, Bluetooth, sound, brightness, power mode
  (LayerOSX backend, PipeWire via wpctl, wlr-randr for displays).
- **M5 -- Launcher, notifications.**
- **M6 -- Settings app** (LayerOSX panel, generalized), sidebar split:
  Sonata (Dock, appearance, icons, animations...) vs. Linux (Wi-Fi,
  Bluetooth, sound, displays, power, wallpaper...).
- **Later**: global menu, "Task Bar" (Windows 11) mode.
