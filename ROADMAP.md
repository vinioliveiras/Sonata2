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
  - [x] M1.5b position left/bottom/right, auto-hide (slide, reveal delay,
        stays while a menu is open), divider menu: Turn Hiding On/Off,
        Turn Magnification On/Off, Position on Screen, Show Recent
        Applications. (All Dock options will also be in the macOS-style
        Settings app, M6.)
  - [x] M1.6 recent apps section (3, own separator), Stacks: Downloads by
        default, folders dropped on the Dock; Grid/List panel, drag items
        out, Sort by / Display as / View content as, Remove, Open
  - [ ] M1.7 polish on hardware (Vini's tests): multi-monitor (the Dock
        shows on the compositor's default output), HiDPI, performance
  - Later (needs other components): notification badges, attention bounce
    (M5), Show All Windows (Wayfire scale IPC), minimized-window tiles
  - Rule (Vini): no fixed icons. Finder (file manager) and Launchpad are
    ordinary tiles: movable, removable, same menu options.
- **M2 -- Launchpad** *(done, to test on hardware)*: full-screen app grid
  over the blurred desktop, 7x5 pages + page dots (swipe/scroll/keys),
  search, folders (hold an app over another; named by category; rename;
  drag out), live drag reorder + page flip at the sides, drag to the Dock
  to pin, jiggle mode (hold an icon / hold Alt) with delete badge for
  user-installed shortcuts (-> Trash), zoom+fade open/close, keyboard
  (arrows, Return, Esc, Page Up/Down). Launchpad tile in the Dock (own
  icon), F4 in Wayfire.
- **M3b -- Top bar** *(done, to test on hardware)*: 24 px translucent bar;
  Sonata menu (About This Computer, Recent Items, Sleep/Restart/Shut Down/
  Lock/Log Out with confirmations), active app menu (About, Hide, Hide
  Others, Show All, Quit), Window menu (Minimize, Zoom, windows, Bring All
  to Front); extras: Sound (slider), Battery (percent option), Wi-Fi (list,
  join with password), Control Center (Wi-Fi, Bluetooth, Dark Mode,
  Display + Sound sliders), clock + calendar. Menus hang left-aligned.
  Not possible yet: apps' own menus (no global-menu protocol on Wayland).
- **M3c -- Windows** *(done for GTK apps)*: Big Sur GTK theme (WhiteSur,
  bundled) + macOS button layout + Sonata icons/cursors, session-only via
  session-env.sh and a Sonata dconf layer; Wayfire decoration colours for
  server-side title bars. Open: Wayfire can't put its own buttons on the
  left nor round/shadow them -> a small Sonata decorator plugin (C++) later;
  Qt apps follow only as far as the GTK platform theme goes.
- **M6 -- Settings app** *(first version done)*: macOS System Settings
  layout (from the LayerOSX panel): Linux -- Wi-Fi, Bluetooth, Sound
  (volume, mute, output device), Displays (brightness, mode, scale via
  wlr-randr), Battery (level, energy mode via power-profiles-daemon),
  Wallpaper; Sonata -- Appearance (light/dark, style, icons, glass),
  Desktop & Dock (all Dock options), Menu Bar (battery %, clock),
  Launchpad (reset, hidden apps); About. Dock/menu bar/Launchpad apply
  changes live (config.watch). Wallpaper drawn by `sonata2 wallpaper`.
  Later: Keyboard, Mouse/Trackpad, Notifications, Users, Privacy, Sharing.
- **M3 -- Session + install** *(done, to test)*: install.sh (user or
  system, dependency check + distro package commands, launchers, login
  screen entry, portal preferences, session Wayfire config kept when
  edited, --uninstall), sonata-session (session env, polkit agent,
  wayfire), XDG autostart runner, macOS-like key bindings.
  Next: a Sonata login/lock screen in the macOS style (ext-session-lock for
  locking; a greeter for the login screen).
- **Next (Vini)**: Files -- our own macOS-style file manager (columns view,
  sidebar, Quick Look...), after the session.
- **M4 -- Control Center**: Wi-Fi, Bluetooth, sound, brightness, power mode
  (LayerOSX backend, PipeWire via wpctl, wlr-randr for displays).
- **M5 -- Launcher, notifications.**
- **Later**: global menu, "Task Bar" (Windows 11) mode.
