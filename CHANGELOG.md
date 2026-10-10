# Changelog

## Unreleased

### Fixes
- The recording / screen-sharing pill is centred in the taller menu bar.
- Files: New Terminal at Folder opens Sonata's Terminal there (other
  terminals as a fallback, and a message when there is none).
- Files: Go > Recents and Go > Connect to Server work again with gvfs
  installed; an alert shown while its window opens comes in front of it.
- Task Manager lists every graphics card (the NVIDIA one too, "Sleeping"
  when powered down -- not woken up to be read).
- Reminders notify at their time with Notes closed (from the menu bar),
  once each, without a burst of old ones at login.
- The Control Center icon no longer goes blank after an app opens or
  closes: the first Dock or appearance save no longer cross-fades every
  Sonata window.
- With Control Center (or any pop-up) open, the pointer is the normal
  arrow over the Dock and other apps, not an app's resize arrow.
- Settings > Wi-Fi: a spinner while the networks are read.

### Performance
- Windows behind an opaque window are no longer drawn again with it (the
  rounded-corner effect hid that from Wayfire): scrolling or a video in a
  maximized window costs only that window.
- A minimized or hidden window frees its picture's video memory after a few
  seconds; sharing one window is paced like sharing the screen.
- The blur is as strong and looks the same, for about half the GPU work
  (worked on a quarter-size picture instead of a third).
- While the screen is locked or a full-screen game has the focus, Sonata's
  background checks pause (Wi-Fi, battery, camera, USB, graphics card).
- The menu bar checks Wi-Fi and battery once for every display, without
  making Wi-Fi scan every half minute; the volume comes from events.
- The camera check reads /proc only when a camera is opened or closed.
- One `pactl subscribe` per process instead of four; the equalizer no
  longer runs pw-dump and pw-cli on every sound event (nor at all when
  it's off), and doesn't wake itself up again.
- The Dock: a window's title changing (browser tabs, terminals) updates
  only names and badges; apps without an entry and Steam icons are looked
  up once; the Downloads stack refreshes at most once a second during a
  download.
- The tray updates only what an app changed (tooltips no longer reload
  its icons).
- Memory: one supervisor for the shell instead of five (`keep-all`), the
  Mission Control backdrop decoded at a quarter size (~78 MB -> ~1 MB),
  icon and thumbnail caches limited, a smaller Spotlight index, the emoji
  and clipboard pickers built on first use, and Docks, menu bars and
  desktop icons of a removed display no longer kept in memory.
- The spinner and the recording dot no longer redraw at the display's rate.

### Changes
- Settings > Displays > Games > WebGPU in Browsers (off by default): 3D
  browser games in Chrome, Chromium, Brave and Firefox. Chromium browsers
  then run through XWayland with Vulkan (on Wayland, Chrome's Vulkan draws
  an empty window); Sonata's web apps stay on Wayland. Firefox gets
  dom.webgpu.enabled.

### Fixes
- An app being updated stays in the Dock: an app leaves it only when its
  entry is still gone 20 seconds later, and not while pacman is running.
- Sharing or recording the screen no longer slows the computer down: a
  picture is made only when the screen changed (or the pointer moved), at
  most 60 a second -- it was every refresh (180 on a 180 Hz display), with
  the whole screen drawn again each time.

## 0.14.0-alpha -- 2026-10-07

Dock badges for every app (WhatsApp too), web app notifications as their
own app, settings backup and restore, windows cut to their frame, and
smoother full-screen games.

### Changes
- Settings > Notifications > Badge App Icons, and Badge App Icon on each
  app: badges off for every app, or for one.
- Dock badges for apps that send none: a web app's unread count from its
  page ("(3) WhatsApp"), else the app's notifications that came in since
  it was last in front. A new badge pops in.
- A Sonata web app's notifications are its own (WhatsApp's name and icon,
  not Chrome's); clicking one brings WhatsApp forward, or opens it.
- Notifications are all the same size: a picture is a small square,
  blank lines and Chrome's site line go, and Chrome's Settings button
  isn't shown.
- Settings > About > Backup: export Sonata's settings -- and, if you like,
  the data of Notes, Calendar and Reminders -- to one .sonata-backup file,
  and import it back (on this computer or another one; your current
  settings go to the Trash first).

### Fixes
- No resize arrow over the Dock or the menu bar where a window's edge
  passes under them.
- Full-screen games run smoother: the rounded screen corners leave the
  display a full-screen app is on, so Wayfire can hand the game's picture
  to the display instead of composing every frame.
- Windows are cut to their frame: an app that draws itself bigger than its
  window (Claude, after or while resizing) never shows past the frame --
  only the frame's shadow is drawn outside it.
- Resizing Claude (or another app drawing ahead of its window) no longer
  gets stuck: an app isn't asked for its size while a window is being
  resized, nor just after.
- Steam's menu in the menu bar works: Library, Store, Community, Friends,
  Settings, Big Picture and Exit Steam open with Steam's own links (Steam
  ignored the clicks).
- The menu bar no longer stops responding when a menu opened over another
  one can't show: that menu is closed again.
- Add to Desktop (an app's menu in the Dock) makes a shortcut to the app --
  its icon and name -- not to its program, which showed as a text file.
- A shortcut to an app dragged to the Trash just goes there: only an
  installed app asks to be uninstalled.
- A shortcut's arrow is smaller, with a shorter shaft, centred on its badge.

## 0.13.0-alpha -- 2026-10-07

Your devices keep their volume -- and apps can be kept from changing it --,
Location Services gets a switch for every app, app icons fill their frame,
and FPS measures your game.

### Changes
- Each sound device keeps its own volume: what you set from Sonata (menu
  bar, Control Center, volume keys, Settings > Sound) is set again when
  that device comes back -- a headset's microphone left at the maximum
  stays there after it reconnects.
- Settings > Sound > "Don't let apps change the volume" (off by default):
  an app turning a speaker or microphone down -- automatic gain in a call
  -- is put back at once.
- Settings > Apps > Location Services: off, no app gets your location
  (Flatpak or not); and a Location switch on each packaged app's page.
  Sonata writes GeoClue's settings (your password is asked).
- App icons with a shape of their own fill the frame: a rounded square or
  squircle tile (FilmCraft) or a full square is enlarged and cut to the
  frame; a disc sits on a plate of its edge's colour. Other icons stay on
  the white plate.
- Preview > Markup: Copy puts the picture with its marks on the clipboard
  (a screenshot's too), Markup staying open.

### Fixes
- A menu bar tray icon never goes blank: an app sending an empty picture
  (Claude's, for a moment) keeps its last icon.
- FPS (Control Center, the menu bar) measures a game or a full-screen
  window -- Steam's, a Windows program's, gamescope's -- not whatever app
  is in front (it measured Claude).
- With Control Center (or any menu) open, the pointer no longer stays a
  resize arrow over the windows and the Dock after passing a window's edge.
- Alerts no longer crash with libadwaita 1.5 (Ubuntu 24.04): their fixed
  width is set only from libadwaita 1.6.
- The displays turn off with the lock again where wlopm does it: Sonata's
  privacy plugin let it use output power management.
- Open with limits (Settings > Apps): an app's own home folder is kept in
  Sonata's data folder, not inside the install that ./install.sh replaces.
- Files: a shortcut's arrow stays visible with a very dark accent in Dark
  Mode.

## 0.12.1-alpha -- 2026-10-07

Windows keep their size and frame through maximize, restore and resize,
the spinners are back, and Sonata's logs are off unless you turn them on.

### Changes
- Logs are off by default: Settings > About > Logs. Off, only errors are
  logged, in memory, until you log out; on, detailed logs are kept in
  ~/.cache/sonata2 (it used to be always on in a git clone, and hidden
  behind seven clicks otherwise).

### Fixes
- The clock on every display opens Notification Center (with your
  notifications) on that display, not a plain calendar.
- An app that draws itself bigger than its window (Claude, after a
  resize: its picture went on past the frame over the desktop) is asked
  again for its size, and draws itself at the window's size.
- Spinners are back (the lock and login screens', Settings'...): Sonata
  draws its own instead of the icon theme's, which adwaita-icon-theme 51
  no longer has. The login screen's is small again (16 px).
- Maximized or restored, a slow app's title bar (Claude, other Electron
  apps) no longer stays at the old width: its frame is redrawn again a
  while after the zoom.
- A window brought back from maximized fits the work area (between the
  menu bar and the Dock; the whole display when they hide): one bigger
  than it no longer comes back under them -- and zooms like the others.
- Maximize / restore zoom again after a window was resized: once any
  window had been resized by its edge, every zoom was skipped.

## 0.12.0-alpha -- 2026-10-07

Your apps under your control: lock them, sandbox them, decide what they
may use, clear their data or uninstall them from Settings > Apps. And
Fedora, Debian and Ubuntu get the full Sonata look.

### Apps
- Lock App (an app's menu in the Dock, Options, or in Launchpad): opening
  it asks for your password first; its icon shows a padlock. Unlocking
  asks too. Login items and apps reopened after a crash don't ask.
- Settings > Apps > Installed Apps: every app with its size and its data. Open
  one to lock it, clear its data (to the Trash), uninstall it, and -- for
  Flatpak apps -- turn camera, microphone, network, location, running in
  the background, notifications and the home folder on or off.
- Ask before apps use things (Settings > Apps, on by default): a newly installed Flatpak app shows, the first time it opens,
  what it wants of the microphone, network and home folder (uncheck to
  refuse); the camera and location ask in a Sonata alert when first used.
  Off: apps get access without asking.
- Apps from the distro's packages get permissions too (Settings > Apps >
  the app): camera, sound & microphone, network and home folder, kept from
  the app when Sonata opens it (bubblewrap). Home Folder off: it gets a
  folder of its own instead of yours.
- App permissions all live in Settings > Apps (no longer in Security &
  Privacy). Clicking the section you're in goes back to its first page.
- Web apps can be uninstalled like any app: Settings > Apps > the web app >
  Uninstall, or dropped on the Dock's Trash (its login and data go too).
- Settings: every search field looks like the sidebar's (App Icons,
  Installed Apps, Hide an App).
- Open in Sandbox (an app's menu in the Dock or Launchpad): the app opens
  with an empty home folder of its own -- none of your files, sign-ins or
  settings -- and everything it saved is deleted when it quits. The Dock
  shows an orange "S" on it meanwhile. Needs bubblewrap (installed by
  ./install.sh).

### Control Center
- FPS Limit names the app it's measuring, and its graph moves slower: the
  last 30 seconds, a point every quarter second (it was frame by frame).

### Dock
- An app opened from a Dock folder stays in it: the folder shows the
  running dot, and so does the app inside its panel. Clicking it there
  brings its windows forward; minimized windows go into the folder.
- A folder undone (its last app dragged out, or Ungroup) no longer just
  vanishes: its apps pop out in its place, one after the other.

### Install
- Fedora, Debian and Ubuntu (whose Wayfire is older than 0.11) get Sonata's
  title bars, round corners and window effects too: the installer builds
  Wayfire 0.12 with its wlroots in its own folder (~/.local/opt/sonata-wayfire,
  10-20 minutes, asked first) and the session uses it. The system's Wayfire
  is left alone.
- A distro package that fails to set up no longer blocks the rest of the
  install (apt), and the installer says why a package was skipped.

### Fixes
- Uninstalling a Flatpak app, and its permissions, failed to find it on
  GLib 2.80+ (its .desktop file wasn't read).

## 0.11.0-alpha -- 2026-10-06

Tabs always at hand in Terminal, Files and TextEdit, a real sleep for
plugged-in monitors and keyboards, and Dock folders that come and go.

### Apps
- Terminal, Files and TextEdit show their tab bar (and +) even with one
  tab: Settings > Appearance > Always show the tab bar (on by default).
- Terminal, Files and TextEdit: the tab in front has the window's own
  colour, the others the bar's (no more grey).

### Fixes
- Away from the computer, monitors plugged into a laptop really turn off
  (they only went black), the pointer hides, and every keyboard's light
  goes off -- USB ones too, not only the laptop's.
- A folder dropped on the Dock opens its place and fades in; deleted (or
  put in the Trash), or its drive unplugged, it leaves the Dock.

## 0.10.0-alpha -- 2026-10-06

Stopwatch and Timers in Clock, Trim and Crop in Videos, windows that zoom
like macOS and resize from their own edge, and fixes.

### Windows
- Zoom (maximize / restore) animates like macOS: the window's frame grows
  or shrinks to its new place and the contents fade in there -- no more
  smear of two title bars over each other.
- Window shadows are almost gone.
- A window's edge itself resizes it (a few pixels inside it), not only
  the space just outside it.
- Fix: resizing or zooming a terminal (or another app with Sonata's title
  bar) showed a frame the size of its shadow, not of the window.
- Fix: zoomed from its title bar, a window with Sonata's title bar could
  keep the title bar cut at its old width.
- Fix: out of full screen (a video in Chrome), a maximized window could
  keep the whole display, behind the Dock.

### Login screen
- The display goes dark at the login screen too, after the time chosen in
  Settings > Battery; any key or move brings it back.

### Files and System Settings
- Picking an item in the sidebar switches at once: no sliding selection,
  no fade.
- Fix: Date & Time's "Show the date" (and 24-hour) switches showed off
  while the menu bar clock had them on.

### Videos
- Trim (right-click > Trim…, or Ctrl+T), like QuickTime: a filmstrip with
  a yellow frame; the part kept is saved as a new file beside the original
  and opened. Needs ffmpeg (now installed with Sonata).
- Crop (right-click > Crop…): a frame over the movie, its shape free or
  16:9, 4:3, 1:1, 9:16; saved the same way.

### Dock
- Fix: the Trash stayed full after Empty Trash when it held leftovers the
  Trash doesn't list (files without their .trashinfo); Empty Trash now
  clears those too.
- Return in "Empty Trash?" empties it (Esc cancels), like macOS.

### Clock
- Stopwatch (laps, fastest and slowest marked) and Timers (quick choices,
  pause, a ring of what's left), as tabs beside Alarms. Both keep going
  with Clock closed; the timer rings like an alarm.

### Menu bar
- The clock keeps room for today's date only (it may change width at
  midnight): no gap beside it most days.

### Launchpad
- Fix: the first full-screen open after using the Apps Menu glitched (its
  grid was worked out again for the menu's size).

## 0.9.1-alpha -- 2026-10-06

A fix for 0.9.0: zooming a window from its title bar.

### Fixes
- A click on a window's edge no longer starts a resize: zooming right
  after it (a double-click near the top of the title bar) left the title
  bar cut at the old width.

## 0.9.0-alpha -- 2026-10-06

Songs play in Videos (the Music app is gone), tabs and shortcuts from the
menu bar, Windows-style shortcuts, and an installer tested on Arch,
Fedora and Ubuntu from scratch.

### Install
- The installer was run from scratch in clean Arch, Fedora and Ubuntu
  (tools/test-install.sh) and fixed where it broke: it no longer stops
  without a word when the system keyboard layout can't be read; Fedora
  gets the GTK typelib it needs (Sonata didn't open); Ubuntu gets the
  gtk4-layer-shell library and pywayland's missing cffi backend.
- If a step fails, the installer says at which line; if Sonata still
  can't start once the packages are in, it says what's missing and stops.
- Wayfire plugins your Wayfire doesn't have are left out of the session.

### Display and lock
- Wayfire never powers the display off anymore (on an NVIDIA laptop it
  didn't come back until the lid was closed): Sonata darkens it itself,
  black with the backlight at zero, the pointer hidden; any key or move
  brings it back.
- Login and lock screen on every display; the keyboard light comes back
  after the lock.

### Menu bar
- File for every app (New Window, Close Window); Window has New Tab, Show
  Previous / Next Tab and Close Tab, for any app with tabs (browsers,
  terminals, Files, editors).
- The keyboard layout is always in the menu bar; Ctrl+Space switches back.
- Window > a window's name, and checked items in many menus (Notes,
  Calendar, Sort By, the login screen) work again.

### Files and desktop
- Create Shortcut (and on the Desktop), like Windows; Add to Desktop for
  apps in Launchpad and the Dock. Shortcuts show an arrow.
- A new folder's name can be typed at once.
- Windows keep the size you give them.

### Apps
- Videos plays songs (MP3, FLAC, Ogg, M4A, WAV) with their cover; the
  Music app was removed (better ones are in Bazaar).
- Preview: the Markup bar is opaque, the trash clears every mark, marks
  stay on the picture.
- Control Center: Keyboard module; Wi-Fi and Bluetooth lists in the
  panel; Add Controls closes again.

### Fixes
- Screenshots work again; the Screenshot icon no longer bounces.
- Double-clicking a title bar zooms wherever you click it.
- The screen-sharing pill's Stop really stops the share.
- Drawing on the screen follows the palette.
- New headphones and headsets are used at once; your energy mode comes
  back on AC; a layout per keyboard.

## 0.8.0-alpha -- 2026-10-05

Draw on screenshots and on the screen, an FPS limit for games, Steam in
Sonata's window layout, and window buttons in your colours.

### Screenshots and recording
- Markup in Preview: pen, highlighter, shapes, arrows, text, emoji,
  numbered steps and pixelate. A screenshot's thumbnail opens straight
  into it; Done saves the marks, and a clipboard screenshot goes back to
  the clipboard marked.
- Draw on the screen while recording or sharing it: the pen in the
  recording pill, or Super+Shift+D. Marks can fade away by themselves;
  the palette stays out of the recording.
- Screen recording uses wl-screenrec when it's installed.

### Games
- FPS Limit in Control Center (Add Controls) and Settings, through
  frame-pacer: Off, 30, 60, 90, 120 or the display's rate. Off by default.
- A frame-time graph under the choices: each frame of the app in front,
  the target, the average and the 1 % low.
- Steam keeps its own theme with Sonata's window layout: round buttons on
  Sonata's side in every window, rounded corners, no Big Picture button.

### Windows
- Settings > Appearance > Button colours: Colourful, Graphite, Black &
  White (black on light, white on dark) or a colour per button. Sonata's
  windows, other GTK 4 apps, title bars and Steam all follow.
- Windows fade in and out when they open and close; minimize keeps the
  genie.
- `sonata2 doctor windows` checks that the window style reaches other
  apps, and a notification says when one doesn't.

### Dock and apps
- Drag an app's icon from the Dock onto the Trash to uninstall it.
- An app stuck while starting opens again on the next click.
- Web apps get their own row in the mixer, and their links open in your
  browser instead of new tabs or windows.
- System sounds (like the volume feedback) never show in the mixer.

### Menu bar and sidebars
- The clock keeps one width: a new minute or a new day no longer nudges
  the items next to it.
- In Files and Settings, the sidebar's selection slides to the row you
  click.

### Fixes
- Rounded window corners are back (Sonata's Wayfire plugin failed to load).

## 0.7.1-alpha -- 2026-10-05

A fix for 0.7.0: Bazaar's icon files were stored in a way that broke
downloading the code (and the website's build).

### Fixes
- Bazaar's App Store icon is a plain file again: checkouts, updates and
  the GitHub Pages build work.

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
