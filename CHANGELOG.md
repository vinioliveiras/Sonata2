# Changelog

## 0.7.0-alpha -- 2026-10-05

The session locks and goes dark when you leave it, whatever apps ask;
window buttons on either side; and more of Files.

### Lock and sleep
- Automatic locking is on by default: when the display turns off, and
  before sleep.
- Only something playing keeps the session awake (a player, a video, a
  sound): an app asking to stay awake no longer keeps the screen on or
  stops the lock (Sonata watches keyboard and mouse idle itself).
- Locked, the display and the keyboard's light turn off after 30 seconds,
  and come back on any key or move.

### Files
- Type the address in the path bar: Ctrl+L, or a click on its empty part.
- An eye in the toolbar shows a folder's hidden files, remembered per
  folder; hidden everywhere else.
- Click the folder's name in the toolbar to rename it.

### Windows
- Settings > Appearance > Window buttons: on the left (the default) or on
  the right.
- Window sizes are kept across a restart.
- Windows programs run with Proton or Wine outside Steam (Faugus, Lutris,
  Bottles) show their name, not "steam_app_default"; any app without a
  desktop entry gets a readable name.

### Desktop
- The desktop's grid is symmetric and reaches both edges on any display.
- A new folder appears where you clicked, without gliding in.
- Calculator and Videos have new icons.

## 0.6.0-alpha -- 2026-10-05

A much bigger Files, apps that really quit with their last window, web
apps on Chromium, and glass in more places.

### Files
- Undo and Redo (Ctrl+Z, Ctrl+Shift+Z): renames, moves, copies, new
  folders and Move to Trash.
- Colour tags: dots next to the names, a row of colours in the right-click
  menu, and Tags in the sidebar.
- Connect to Server (Ctrl+K): Windows shares, SFTP, WebDAV, FTP and NFS;
  Network in the sidebar.
- A path bar, and Go to Folder (Ctrl+Shift+G, Ctrl+L).
- Rename N Items: replace text, add text, or number them.
- Search by contents, kind and date.
- Compress, Copy Path, and Quick Actions for pictures (rotate, convert to
  PNG / JPEG).
- RAR and 7-Zip archives expand like a .zip; protected archives ask for
  their password.
- Get Info: change the app a file opens with, and its permissions.
- List view: choose the columns (Date Created, Date Last Opened), and
  Calculate All Sizes for folders.
- Folders always come first.
- Items glide to their new place when others come and go; renaming or
  tagging never resizes the grid.
- A dirty Windows disk opens read-only instead of failing.
- Long names are cut in the right-click menu, the extension kept.

### Apps and windows
- Apps quit when their last window closes (Settings > Desktop & Windows,
  on by default) -- the whole app, so Steam doesn't come back.
- Apps opened from Sonata come to the front.
- Long window titles keep a margin from the title bar buttons.
- Closing apps: the Dock no longer re-reads every process many times.

### Web apps
- Chromium or WebKit, chosen per app; every app keeps its window size.
- Sending a video works, and can't freeze the computer.

### Memory and security
- Apps run in their own scopes; background apps give memory back.
- Privileged protocols for Sonata only, app permissions, firewall.
- A new user account needs a password.

### Desktop
- The menu bar is taller, like the newest MacBooks' (Settings > Menu Bar
  to go back to 24 px).
- Glass for every alert, Files' Copy window, pop-up lists, Dock folder
  stacks and hover labels.
- Screen sharing: a menu bar pill with Stop, and window thumbnails to pick
  from.
- Screenshots go to the clipboard and the Screenshots folder at once.
- Control Center: each card's maker in its graph, the five busiest
  programs in the CPU and GPU graphs.
- Volume per app keeps the levels set in the app itself.
- Menus with a single item no longer have a gap under it.
- Icons with a tile of their own fill the frame; Bazaar wears the App
  Store icon; a new Disk Manager icon.
- Login screen: users wrap into rows that fit the display; the power
  buttons never take the keyboard.
- Settings: Dock, Menu Bar, Control Center and Desktop & Windows on pages
  of their own.

## 0.5.4-alpha -- 2026-10-04

Temperatures in the menu bar and Control Center, and no frozen cursor on
games after Alt+Tab.

### Performance
- CPU and GPU temperatures, in the menu bar (text or graph) and as Control
  Center modules: the CPU's whole chip, and each graphics card on its own.

### Games
- After Alt+Tab, the cursor no longer stays frozen on a full-screen game
  (Sonata's Wayfire: run tools/build-wayfire.sh).

## 0.5.3-alpha -- 2026-10-04

A fix for a crash while a game fills the graphics card.

### Stability
- Wayfire no longer crashes when a game fills the graphics card's memory
  and window blur has to give way: the shared 1x1 buffer it falls back to
  is released correctly.

## 0.5.2-alpha -- 2026-10-04

Graphics that pick the right card on any laptop, video memory figures,
resizing without the window's contents, and smoother details.

### Graphics
- The high-performance card is found on any machine: AMD + NVIDIA, AMD +
  Radeon, Intel + Arc, Intel + NVIDIA, and laptops with a MUX in dGPU
  mode (Steam and its games went to the integrated GPU there and didn't
  open).
- Settings > Displays > Graphics: "Graphics for Games" and "Graphics for
  Apps" -- Automatic, or a card of your choice.
- Smart Graphics Switching no longer unchecks itself for an app.

### Performance
- Video memory of each card, in the menu bar (text or graph) and as a
  Control Center module.

### Windows
- "Show window contents while resizing" (Settings > Desktop & Dock): off,
  a rounded panel with the window's shadow follows the pointer and the
  window fades back in at its new size.
- Quit quits the whole app, also when it keeps running without a window.

### Desktop
- Restart Sonata: the Dock and menu bar slide back in together.
- Control Center resizes smoothly when a module is added or removed.
- A new desktop folder no longer glides in from the top-left corner.

### Stability
- Sonata's Wayfire plugins build again (rounded corners, FPS, resize).
- Wayfire survives a failed buffer allocation with a shared 1x1 buffer.

## 0.5.1-alpha -- 2026-10-03

Airplane Mode, a smooth Restart Sonata, and fixes for web apps and the
desktop.

### Control Center
- Airplane Mode, in the Wi-Fi & Bluetooth module: every radio off at once;
  turned off, Wi-Fi and Bluetooth come back as they were.
- Wi-Fi, Bluetooth and Airplane Mode share the module's height evenly.

### Desktop
- Restart Sonata is smooth: the Dock and menu bar slide out and back in,
  and the wallpaper stays on screen (it no longer turns grey).
- A new folder on the desktop appears where you clicked and is ready for
  its name: just type.

### Web apps
- Pictures paste with Ctrl+V (a screenshot, an image copied in a browser
  or files copied in Files), in WhatsApp and other web apps.

### Project page
- A new design, with what's new in 0.5.

## 0.5.0-alpha -- 2026-10-03

A Control Center you arrange yourself, smarter graphics switching, display
arrangement, and apps that quit properly before a restart.

### Control Center
- Modules sit on a 4-column grid and are edited like Apps: hold one (or
  Edit Controls…) and they jiggle; drag to move, x to remove, Add Controls
  to bring one back.
- New modules, off until added: CPU, GPU (one per card), Memory, Network,
  FPS of the full-screen game, and the Volume Mixer, which grows with the
  apps playing.
- Its width follows the display and never changes; modules keep one size.

### Menu bar
- "Automatically hide and show the menu bar", like the Dock.
- Every status item can be turned off (Settings > Menu Bar), and CPU, GPU,
  memory, network and FPS can be shown as text or a graph.
- Double-click a background app's icon to open the app.

### Displays and graphics
- Settings > Displays: drag the displays to arrange them; drag the menu
  bar to choose the main display. The login screen follows it.
- With nothing chosen, an external monitor is the main display.
- Smart Graphics Switching (on): games and creative apps use the
  high-performance graphics, everyday apps the graphics that draw the
  screens. Each app can have its own choice from its right-click menu.
  Spotify no longer opens empty.

### Apps
- Restart, Shut Down and Log Out quit the apps first: Chrome no longer
  asks to restore its tabs. An app with unsaved work cancels it.
- Settings > Default Apps: browser, mail, calendar, music, videos,
  pictures, PDFs, text files and folders in one place.
- Web apps: pictures and copied files paste (WhatsApp); a link brings the
  browser forward.
- Task Manager: which graphics card each process uses; a Steam game's
  processes show as that game.

### Fixes
- One password when waking up, not three.
- Windows, panels and pickers fit small displays.
- Review of every area: over 360 new tests, and fixes in Files, Preview,
  Calendar, Notes, the login screen, the menu bar, the Dock and more.

## 0.4.1-alpha -- 2026-10-03

Gaming and everyday apps share the graphics card better, and Steam opens
every time.

### Gaming
- Lighter Effects While Gaming (Settings > Displays > Graphics, on): when a
  full-screen game fills the graphics card, blur and window animations
  pause until you leave the game.
- Everyday Apps on the Integrated Graphics (same place, off for now):
  browsers, chat and office apps use the integrated GPU and the computer's
  memory; games and creative apps keep the graphics card.
- The wallpaper takes much less graphics memory (decoded at the display's
  size).
- Steam opens every time on computers with an NVIDIA card (its window
  sometimes never showed).
- "Use Discrete Graphics" is now "Use High-Performance Graphics".

### Desktop
- The emoji picker closes with Esc or a click outside it.
- New Web App… is only in Apps' right-click menu.

### Project page
- Ko-fi support, credits for the icons, themes and software Sonata uses,
  and a favicon.

## 0.4.0-alpha -- 2026-10-02

Wallpapers, a fresh look for the project page, and a steadier session
while gaming.

### Stability
- Sonata's build of Wayfire now skips every frame or effect the graphics
  card can't make room for (blur, animations, minimizing every window with
  Super+D) instead of crashing. Run `tools/build-wayfire.sh` again.
- After a crash, the old Dock and menu bar no longer come back next to the
  new ones.

### Wallpapers
- Mountains is the new default: snowy peaks in Light, the same peaks under
  the stars in Dark.
- Settings > Wallpaper shows Sonata's ten wallpapers (photos from
  Unsplash); one click sets it for Light and Dark.

### Desktop
- Minimizing from the Dock flies to the icon on the display you clicked.
- Apps: no more empty last row when the grid size changed (any screen size).
- Menus: every text starts on the same line; checks sit after the text,
  also in submenus (the Dock's Options > Keep in Dock).
- Disk Manager has its own icon, a storage ring.

### Project page
- New look built around the Mountains wallpaper, line icons, a Wallpapers
  section and fresh screenshots.

## 0.3.0-alpha -- 2026-10-02

Steadier sessions on laptops with two graphics cards, web apps, and an
Apps Menu.

### Stability
- A game or app that fills the graphics card's memory no longer takes the
  session down: Sonata's build of Wayfire skips a frame or an effect
  instead (run `tools/build-wayfire.sh`; the installer offers it).
- On laptops whose screens are all on the NVIDIA card, the integrated GPU
  never draws the session (most AMD crashes came from there).
- After a crash Sonata starts again in the same login, and Feedbacker names
  the cause, keeps a history and offers to reopen your apps.
- A warning when the NVIDIA card's memory is almost full, naming the app.
- No more duplicated Dock and menu bar after a restart.

### Apps and web apps
- New Web App… (right-click Apps, the Desktop or the Dock's divider): a
  site in its own window, with its own login, icon and Dock item. Edit it
  or choose its icon later; it can keep running for notifications.
- Apps Menu: Apps as a compact glass panel with categories, chosen in
  Settings > Appearance or in the setup; same folders, drags and search as
  the full screen. Arrow keys and Esc work in both.
- Folders made in the Dock show up in Apps, and stay in sync both ways.
- Assistant (new): chat with Claude, with your own API key.
- Screenshot is in Apps; the capture toolbar opens on the last mode.

### Desktop
- Super+D shows the desktop; clicking one app brings back only that app.
- Restoring a window from another display's Dock brings it to that display.
- Screen Sharing (Settings > Sharing): see and control Sonata from another
  device with any VNC viewer, always with a password.
- Steam games show their name and icon in Alt+Tab and the sound mixer.
- Dock folders are renamed in their own dialog; folder names stay centred.

### Fixes
- The installer no longer uses the AUR for pixdecor (it conflicted with the
  stable wayfire, #1); it is built from source.
- The recording's status shows once, in the middle of the top of the screen.
- Many smaller fixes, each with a test.

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
