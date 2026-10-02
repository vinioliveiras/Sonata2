# Changelog

## 0.2.0-alpha -- 2026-10-02

Sonata now updates itself: from this version on, a new release shows up as
a notification and in Settings > Software Update.

### Dock and Apps
- Folders in the Dock: hold an app over another to make one, or drag a
  folder in from Apps (and back). Folders can be locked with your password.
- Apps' folders use the same icon frame as apps.
- Dropped icons glide into place; a puff of smoke when one is removed.
- The Dock keeps its order across log-ins, and stays matched to apps that
  were already open when Sonata restarts (Spotify, Claude...).
- A launching app's icon bounces until its window opens, 10 bounces at most.

### Settings
- Fewer sections (20 instead of 27), nothing removed; Settings opens on
  Appearance.
- Appearance: light/dark, any accent colour (with Sonata's colour picker),
  title bars, glass and transparency for the Dock, menu bar, menus and
  windows each, blur strength, corner radius for windows, Dock and menus.
- App Icons (new): every app's icon from Sonata, its package, a picture of
  your own or the icon theme; the frame's shape (squircle, circle, rounded
  square) for all apps or one, and how big the picture sits in it.
- Mouse & Trackpad say which options are for which device; section icons
  in more colours.
- Settings that need a restart offer Restart Now or Later.

### Desktop
- Volume per app in the menu bar's Sound menu, kept for the next time.
- Rounded screen corners (on by default), also on the lock and login screens.
- Date and time at the top of the lock and login screens.
- A desktop on every display; darker dark mode; softer window shadows;
  opaque title bars by default (glass title bars optional); lighter blur.
- Long window titles no longer run over the title bar buttons (run
  install.sh again: it builds the patched title bar plugin).
- Open/Save panels look exactly like Files and always open on top.
- Saved passwords: the login keyring opens with your login, no extra prompt.
- RGB devices and the keyboard backlight turn off with the display.
- Menu bar logo can be text (Sonata, your name or your own words).

### Apps
- Clock (new): alarms with soft sounds that ring even with Do Not Disturb.
- Feedbacker (new): report a problem with logs attached; opens by itself
  after a crash.
- Files: each folder remembers its view and sort; Sort By in icon view.

### Fixes
- Do Not Disturb really hides every banner.
- Many smaller fixes; every fixed bug has a test so it stays fixed.

## 0.1.0-alpha -- 2026-10-01

First public preview. Sonata 2 is a desktop shell for Wayland (Wayfire,
GTK 4, libadwaita) with a macOS-like reference look; the look is a theme,
and more are planned. Expect rough edges: report what you find.

### The desktop
- Login and lock screen, menu bar (with each display's own), Control
  Center, Notification Center with the calendar, notifications.
- Dock: glass, magnification, auto-hide, stacks, recent apps, window
  previews, drag and drop; icons bounce until the app's window opens and
  close up smoothly when removed.
- Apps (Launchpad-style grid with folders and pages), Search, Overview
  (Mission Control-style), Spaces.
- Screenshots and screen recording (a display, a window or an area, with
  sound), Night Shift, emoji picker, sound effects.
- Keyboard shortcuts, including the familiar Windows ones, changeable in
  Settings.

### Apps
Files (with Open/Save panels for every app), Settings, Calculator,
TextEdit, Preview, Terminal, Notes and Reminders, Calendar, Music, Videos,
Camera, Task Manager (with a GPU column) and Disk Manager.

### Every app in the same frame
- One window style for Sonata's apps, GTK 3 and GTK 4 / libadwaita apps
  (traffic lights, title bar, corners), and the title bars the compositor
  draws for terminals, Qt, X11 and Wine apps. Chrome, Firefox, VS Code and
  Vesktop are switched to it.
- One place defines every window frame (`ui/tokens.py`, `FRAME`).

### Games and hardware
- Game controllers drive the desktop (off by default; five taps on the
  Guide button), GameMode, full screen remembered per app and for Wine
  games, Steam games with their own names and icons.
- Laptops with two graphics cards: the desktop draws on the integrated
  card, games and chosen apps on the discrete one; Automatic energy mode
  runs full screen apps at High Performance.
- Brightness per display, external monitors included (DDC/CI).

### Known issues
- Steam's main window can take a while to appear on an external display.
- Apps with their own title bars (Discord, Spotify, Steam) keep them.
- Drawing the desktop with an NVIDIA card is opt-in (Settings > Displays):
  its driver can end the session; Sonata then turns it off by itself.
