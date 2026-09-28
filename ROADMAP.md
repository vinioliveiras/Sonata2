# Roadmap

Base: LayerOSX code (style, Settings, backend). Sonata is only a feature
list: [docs/PARITY.md](docs/PARITY.md).

- **M0 -- Skeleton** *(done)*: repo layout, style module ported from LayerOSX.
- **M1 -- Dock** (finish it completely before the next component):
  - [x] M1.1 pinned apps, Trash, hover labels, launch bounce
  - [x] M1.2 frosted glass (Wayfire blur), running dots, unpinned running
        apps, click to activate/restore, minimize into the icon
  - [x] M1.3 right-click menus: app (open windows list, Options > Keep in
        Dock / Open at Login / Open File Location, Hide, Quit / Open);
        Trash (Open, Empty Trash). Left for later: Show All Windows (needs
        a Wayfire scale-by-app IPC call)
  - [~] M1.4 drag: [x] reorder, [x] drag out to remove, [x] running app
        dropped in place gets pinned; [ ] drop an app from the launcher to
        pin, [ ] drop files on an app (open with) or on the Trash
  - [ ] M1.5 preferences: size, magnification, position (left/bottom/right),
        auto-hide, show indicators, show recent apps
  - [ ] M1.6 polish on hardware: multi-monitor, HiDPI, performance check
  - Later (needs other components): notification badges (M5)
- **M2 -- Top bar**: Apple-style logo menu (left), clock + status icons
  (right), Big Sur metrics (24 px, translucent), light/dark.
- **M3 -- Session**: Wayfire config (animations, decorations with traffic
  lights), session `.desktop`, `install.sh` (`~/.local` or `/usr/local`).
- **M4 -- Control Center**: Wi-Fi, Bluetooth, sound, brightness, power mode
  (LayerOSX backend, PipeWire via wpctl, wlr-randr for displays).
- **M5 -- Launcher, notifications.**
- **M6 -- Settings app** (LayerOSX panel, generalized).
- **Later**: global menu, "Task Bar" (Windows 11) mode.
