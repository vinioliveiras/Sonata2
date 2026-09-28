# Roadmap

Full feature list: [docs/PARITY.md](docs/PARITY.md) (carried over from Sonata).

- **M0 -- Skeleton** *(done)*: repo layout, style module ported from LayerOSX.
- **M1 -- Top bar**: Apple-style logo menu (left), clock + status icons
  (right), Big Sur metrics (24 px, translucent), light/dark.
- **M2 -- Dock**: pinned apps, running indicators via wlr-foreign-toplevel,
  click to launch/activate/minimize.
- **M3 -- Session**: Wayfire config (animations, decorations with traffic
  lights), session `.desktop`, `install.sh` (`~/.local` or `/usr/local`).
- **M4 -- Control Center**: Wi-Fi, Bluetooth, sound, brightness, power mode
  (LayerOSX backend, PipeWire via wpctl, wlr-randr for displays).
- **M5 -- Launcher, notifications.**
- **M6 -- Settings app** (LayerOSX panel, generalized).
- **Later**: global menu, "Task Bar" (Windows 11) mode.
