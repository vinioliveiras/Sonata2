# Changelog

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
