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
- **M7 -- Files** (our own Finder; `sonata2 files [FOLDER]`), in parts:
  - [x] F1 window: sidebar (Favorites: Recents, Desktop, Documents,
        Downloads, home, GTK bookmarks; Locations: Computer, drives with
        eject; live), unified toolbar (back/forward, title, view switcher,
        search), Icons view (Finder sort, selection look, rubber band),
        open with default app, live folder updates, hidden files
        (Ctrl+Shift+.), Finder keys with Ctrl as Cmd
  - [x] F2 List view (Name/Date Modified/Size/Kind, click to sort, zebra
        rows) and Columns view (column browser, preview column, arrow
        keys); view saved in files.json; Ctrl+1/2/3
  - [ ] Gallery view; List: disclosure triangles (expand folders inline)
  - [x] Recursive search (This Mac / current folder scope bar, accent-
        insensitive, background thread, results in batches)
  - [x] F3 context menus, rename in place (Return/F2), New Folder,
        Move to Trash, Duplicate, copy/cut/paste (GNOME-compatible
        clipboard) with the Copy window + conflict alerts; thumbnails
        (images/videos, freedesktop cache)
  - [x] Get Info, Quick Look (Space)
  - [x] F4 drag and drop (copy/move/progress), spring-loaded folders
  - [ ] F5 tabs
  - [ ] F6 default file manager (Dock tile, Stacks, "Open File Location",
        FileManager1 D-Bus)
- **M4 -- Control Center**: Wi-Fi, Bluetooth, sound, brightness, power mode
  (LayerOSX backend, PipeWire via wpctl, wlr-randr for displays).
- **M5 -- Launcher, notifications.**
- **Later**: global menu, "Task Bar" (Windows 11) mode.

## Done in the Big Sur polish pass (2026-09-29)

- Standard window frame (corners, shadow, clipping) for Sonata apps;
  glass sidebars (Dock's tint); pixdecor title bars for apps without CSD
  (AUR wayfire-plugin-pixdecor-git), colours follow Dark Mode live.
- Menu bar: own status icons, Spotlight item, input source menu, clipboard
  history, Now Playing; Big Sur Wi-Fi/Battery/Sound menus; Control Center
  in the Big Sur layout (Do Not Disturb, Dark Mode, Screenshot, sliders,
  Now Playing).
- Notifications: server + banners + Notification Center (clock) + DND.
- Volume/brightness HUD on the media keys; macOS screenshots; Mission
  Control / App Exposé / Spaces gestures; lock screen (ext-session-lock).
- Settings: Keyboard (input sources), Trackpad, Mouse, Date & Time,
  Users & Groups (login items), Sharing, Accessibility, default browser,
  display sleep. Wayfire options go to ~/.config/sonata2/wayfire-overrides.ini.

## Done in the second autonomous pass (2026-09-29, night)

- Spotlight (Super+Space), app switcher (Super+Tab), Files drag and drop.
- Screenshots: floating thumbnail; Super+Shift+5 toolbar (screen /
  selection, recording with wf-recorder + stop item in the menu bar,
  Options: save to Desktop/Documents/Clipboard, 5/10 s timer).
- Night Shift (wlsunset): Settings > Displays (schedule off / custom /
  sunset to sunrise from the time zone, until tomorrow, warmth) and the
  Control Center Display module.
- Emoji picker (Ctrl+Super+Space), types with wtype + clipboard.
- Settings: Network (Ethernet/VPN services, import .ovpn / WireGuard),
  Printers & Scanners (CUPS), Notifications (per app: allow, banners,
  Notification Center), Security & Privacy (auto-lock via swayidle, off
  by default; recents; Trash after 30 days; location), Software Update
  (pacman-contrib / dnf / apt / zypper + Flatpak, Update Now in a terminal).
- Files: recursive search.

## Next

- Verify on hardware (the login session black screen: see
  ~/.cache/sonata2/login.log + session.log).
- Files: tabs, Gallery view, List disclosure triangles.
- Settings: Displays arrangement (drag screens, wlr-randr positions).
- Global menu (Wayfire kde-appmenu + DBusMenu) -- large.
- System Preferences as in Big Sur (icon grid) vs the current sidebar:
  Vini to decide.

## Open questions for Vini (ask before deciding)

1. System Settings: Big Sur icon grid (System Preferences) instead of the sidebar?
2. Switch Spaces with Ctrl+Left/Right like macOS (clashes with word jumps in Linux apps)? Now Super+Alt+arrows + 3-finger swipe.
3. Tap to click: off like macOS, or on (now on)?
4. Hide the plain "Wayfire" entry on the login screen?
5. Ask for the password after the display turns off (lock on idle)? Now in Settings > Security & Privacy, off by default until the lock screen is tested on your machine; macOS default is "immediately".
6. Push to GitHub once the sonata2 repository exists.
