# Changelog

## Unreleased

- Apps Menu: the room under the apps is now a box of its own after the grids (a drop there goes to the end), instead of a drop target around them that heard every motion over the grids too -- reordering and making folders work in the Apps Menu as they do full screen.
- Dock folders: while an app is dragged out of a folder's panel, the (invisible) panel takes no pointer at all -- the drop under it went to the panel, which has no drop target, and the app fell back into its folder.
- Tests: the Dock, Dock folders and Launchpad tests no longer skip on a machine without certain apps -- a fixed set of test apps (tests/fake_apps.py) is installed for them, so they always run (they had hidden regressions); two stale tests brought up to date (a reorder that landed in the new folder zone, a centred name measured against an old width).
- Screenshots to the clipboard work again with every caller (the Print Screen change passed a new argument to code that took the path alone).
- Apps Menu: dropping an app on another makes a folder again -- the drop target added under the apps (for the empty room) heard every motion over the grid too and undid the folder's target; it now only answers in the empty room. A test drags with a real pointer (xdotool) so this can't come back unseen.
- Game Bar: its bar and widgets open already in glass -- the game dims at once while they wait (at most 220 ms) for the game's picture, then pop in with it; they used to open solid and turn to glass a moment later.
- Apps and the Apps Menu: an app taken out of a folder goes right beside the app it's let go on -- over its left half before it, over its right half after it, the bar on that side showing where.
- Dock folders: an app dragged out of a folder's panel can be put anywhere on the Dock -- the panel closed as the drag began and, on Wayland, the drag ended with it (the app fell back into its folder). The panel now goes invisible and lets the pointer through until the drag ends.
- Apps and the Apps Menu: an app held out of its folder (Hidden too) moves only when let go -- meanwhile a bar beside a tile marks where it will go and nothing jumps; let go away from the grid, it stays in its folder.
- Apps and the Apps Menu: an open folder follows its apps -- Hide takes the app out of it at once, Lock App shows its padlock there, and a folder left empty closes (it stayed as it was until opened again).
- Apps Menu: an app can be dragged out of an open folder again -- the dimmed grid behind the folder let no drag through (so a click behind the folder closes it); it hears drags again while one comes out of the folder. Let go in the empty room under the apps, it goes to the end.
- Game Bar lighter still: each panel draws its own glass under itself, instead of one glass layer over the whole screen that was drawn again whole whenever a panel changed (and looked at on a timer); only the panel that changes is drawn again.
- Game Bar opens and animates lighter: it waits at most 80 ms for the game's picture (a later one fades the glass in); the glass looks where the panels are a few times a second instead of on every frame (the frame clock never rested while it was open); the volumes, radios and brightness are read once the opening animation is over (pactl, nmcli, rfkill and DDC/CI started with it).
- Game Bar: the glass no longer goes missing on some openings -- when the game's picture took longer than the Game Bar waits, it was thrown away; now the Game Bar still opens at once and the glass fades in when the picture comes (that opening's, while it's open).
- Game Bar: the Performance widget has every graph Control Center has -- CPU, each GPU, their memory, the temperatures, memory, network -- in Control Center's order, under the FPS Limit; many of them scroll inside the widget.
- Game Bar: a Control Center widget at the top right -- Wi-Fi, Bluetooth, Airplane Mode, Do Not Disturb and the brightness of the game's display, Control Center's own controls (made once for both: topbar.QuickControls), read again at each opening; reachable with the controller. Shown the first time after the update.
- Game Bar: a Now Playing widget at the top left (the menu bar's module: artwork, title, artist, previous / play-pause / next), with its toggle on the bar; shown the first time after the update. Now Playing follows the player only while on screen, in Control Center too (a hidden window stays realized and kept listening).
- Game Bar: the volume mixer in the Audio widget -- each app playing sound with its own volume, mute and output (the Sound menu's rows), kept up to date only while it's on screen (open or pinned); reachable with the controller.
- Game Bar: Control Center's FPS Limit in the Performance widget (the same module: Off, 30, 60, 90, 120, Max, and the game's frame times), above CPU, the games' GPU, a temperature and memory. Reachable with the controller.
- Dock folders: an app dragged out of a folder's panel leaves it only when dropped on the Dock; let go anywhere else (or barely dragged, or cancelled) it goes back into its folder where it was -- a two-app folder isn't undone any more.
- Game Bar: a dragged widget no longer shakes under the pointer -- the drag was measured from its own title, which moved with it; now from the Game Bar, which doesn't.
- Game Bar in glass again, at no cost per frame: opening, it takes a small picture of the game's display, blurs it once and shows it behind its bar and widgets, which turn see-through over it (the compositor still never blurs behind it). Without grim, or if the picture is late, solid as before.
- The blur rule Wayfire runs with is brought up to date when the menu bar starts: after an update, a surface newly never blurred (the Game Bar) kept its blur until the look was changed or you logged in again.
- Apps and the Apps Menu: an open folder is glass again -- light in the light look, dark in the dark -- over the faded and blurred apps behind it.
- Game Bar finished:
  - Audio widget: the output and microphone volumes, and what recordings take -- System, Microphone, Both (two separate tracks in the movie) or None. "Record System Sound and Microphone" is in the screenshot toolbar's Options too.
  - Performance widget: FPS, CPU, GPU and memory graphs (Control Center's), read only while on screen.
  - Widgets move by their title (a double-click puts one back) and the pin keeps one over the game after the Game Bar closes -- a pinned Performance is the HUD; pinned widgets let clicks through.
  - Instant replay: keeps the last 30 seconds of the game (wl-screenrec); Save Last 30 s in the Capture widget, or Super+Alt+G, writes them as a clip.
  - The controller moves the Audio sliders with the D-pad's left / right.
- Game Bar: a Steam game (or a Windows program) shows its real name and icon, as in the Dock, instead of "steam_app_3219630".
- Game Bar records games: its Capture widget has Screenshot and Record / Stop (with the recording's time), on the game's display once the Game Bar has gone; the recording keeps its pill (stop, pen) at the top.
- Game Bar: no blur behind it any more (a full-screen blur over a running game cost too much) -- the game dims like Overview (Win+Tab), and its bar and widgets are solid.
- Videos: Trim and Crop buttons in the controls, beside full screen (a song shows only Trim); pressable once the movie can be edited.
- Videos: the right-click menu (Trim…, Crop…, Loop, Show in Files…) shows again -- the movie's window handle took the right-click for the window menu first. Without FFmpeg, Trim and Crop say what to install instead of sitting greyed out.
- Window buttons' bigger target reaches the window's real corner on both sides: in Settings (a header bar) the dots sat a pixel further in than designed and the very corner missed, buttons on the left or on the right.
- The drawing palette (Game Bar, recording and sharing pills) lost its square shadow: its shadow was cut at the edges of its surface; the room around it now holds the whole (menus') shadow, and only the palette itself takes clicks -- you can draw right up to it.
- After Print Screen the capture toolbar stays open -- even once the picture is closed -- to take more shots or change the options; each shot from it opens in Markup, and it comes back after each one (or a cancelled selection, or a recording). Its close button or Esc ends it. While kept it takes the keyboard only when clicked, so the editor can be typed in.
- A smallest window size for the apps that had none: Preview (420 x 300), Terminal (420 x 260), TextEdit (420 x 280), Disk Manager (640 x 420) and Calendar (720 x 440). Preview could shrink to a sliver and TextEdit to one line under its toolbar.
- Videos: the movie / song window has a smallest size -- never narrower than its controls (nor the trim and crop bars), nor shorter than them plus some picture; shrunk, it used to cut the volume and time row. A song's window opens square and whole.
- Reopen your apps after Restart, Shut Down or Log Out (macOS' "Reopen windows when logging back in"): the confirmation has the box, ticked by default and remembered as you leave it; the next session's Dock opens the apps that were open (not the ones Open at Login starts anyway). Settings' restarts follow the same choice.
- Game Bar: the recording pill's pen is on its bar too -- Draw on the screen with the same palette (pen, highlighter, shapes, text, emoji, colours, fading marks). Pressed, the Game Bar closes and drawing starts on the game's display; pressed again while drawing, it stops. Reachable with the controller.
- An open folder in Apps and the Apps Menu stands out more: a denser folder background (nearly solid in the Apps Menu) and the apps outside it faded further and blurred, both easing in and out.
- Window buttons are easier to hit, with the same look: each dot takes the clicks of the room around it -- half the gap to its neighbours (no dead space between them), up to the window's top edge, and out to its side edge for the outermost one (maximized: the screen's corner). On Sonata's windows, dialogs and on other apps' title bars (pixdecor, rebuilt by install.sh). The window's edge still resizes.
- Game Bar with a controller: the Xbox / PS button opens and closes it (in games too, with desktop control off); while it's open the D-pad / left stick move between its controls, A presses, B closes and LB / RB jump between the bar and the widgets. The controllers are held by the Game Bar while it's open, so the game doesn't get those presses. Settings > Game Controllers can give the button back to the Overview.
- Alerts no longer show a close dot where a button already backs out: Later (Restart / Log Out / Restart Sonata), OK-only alerts, Done, Not Now, Don't Allow, Revert (display settings) and Stop (copy conflicts). It stays only where every button acts (a disk's "in use" choices).
- Print Screen captures at once and opens the shot in the editor (Markup) to draw, crop and save, with the options last used in the screenshot toolbar (Super+Shift+5): its mode -- the display in front, the window in front, or a portion to draw (after a recording mode, the same kind of still) -- its folder and its clipboard choice; no list to pick from. Listed in Settings > Keyboard Shortcuts as "Screenshot and Edit".
- Game Bar (Super+G, like Windows' Win+G, in the Big Sur look): a bar over the dimmed game with the app in front, a toggle per widget (Audio, Capture, Performance -- the widgets along the bottom: Audio left, Capture centre, Performance right) and the clock; Esc, Super+G or a click on the game closes it. The widgets' content comes next.

## 0.20.5-alpha -- 2026-10-23

### Changes
- Video wallpaper: paused on every display while a game or anything full
  screen is in front, whatever the wallpaper switches say.

## 0.20.4-alpha -- 2026-10-23

### Fixes
- Video wallpaper: at the end of the video it went back to the first frame
  and stopped, and the desktop stopped answering the mouse. The loop seeks
  the same player back (the last frame still fades into the first), and a
  player is never freed from another thread again.

## 0.20.3-alpha -- 2026-10-23

### Changes
- Changing the wallpaper starts the desktop's wallpaper again, fresh (like
  restarting Sonata, but only the wallpaper; no black flash).
- Sonata's document windows (Files, Settings, Notes, Calendar, TextEdit,
  Activity Monitor, Disk Utility, Assistant) open at about two thirds of the
  display the first time (~1260 x 700 on 1080p), not small.

### Fixes
- Wallpaper: the second one chosen after a video still didn't show until
  Sonata restarted.
- Screenshots and recordings with a timer: the display / window list (and
  the portion) comes first, then the countdown.

## 0.20.2-alpha -- 2026-10-23

### Changes
- Video wallpaper, per display: a display with no window keeps playing
  while another one with windows shows it paused (Settings > Wallpaper >
  "Pause each display on its own", on by default).
- Settings > Wallpaper: "Keep playing with windows open" (off by default):
  windows never pause the video; Overview and the lock screen still do.
- Overview: a video wallpaper's desktop thumbnail shows the frame the video
  paused on, not its poster.

### Fixes
- Wallpaper: after a video, the first picture chosen showed and the next
  ones didn't until Sonata restarted (freeing the video's player held the
  desktop; it's freed on a thread of its own now).

## 0.20.1-alpha -- 2026-10-23

Fixes that missed 0.20.0.

### Fixes
- Settings > Wallpaper: Choose... did nothing; it opens Sonata's own Open
  panel for pictures and videos ("Picture or Video").
- Video wallpaper with sound on: the picture went black, the sound playing
  (the audio outputs came back all at one rank; PulseAudio / PipeWire's
  wins again).
- Dock: a folder dragged from Files (its list or its sidebar) was refused
  over app icons and showed nothing; its place now opens by the other
  folders and it's kept there (an app that opens folders still opens it).

## 0.20.0-alpha -- 2026-10-23

Video wallpapers loop with a smooth fade, play blurred on the lock and
login screens, can have sound, and pause under any app, a dragged window
and Mission Control. Window buttons get a size slider and move in with a
rounder corner; on the right they sit as on the left in every app. The
colour picker is a colour wheel, folders from Files' sidebar go on the
Dock, and a controller turns desktop control on with LB + RB + L3 + R3.

### Changes
- Video wallpapers: loop with a smooth fade from the last frame into the
  first; a player that stalls or fails is started again (never black).
- Video wallpapers play on the lock and login screens too, blurred.
- Settings > Wallpaper: sound for a video wallpaper (off by default) and
  its volume (25 % by default).
- Appearance > Title Bars: a Button size slider for close, minimize and
  zoom on every window (Sonata's, GTK apps', the title bars Sonata draws,
  Steam's); they keep their distance from the window's edge at any size.
- A rounder window corner moves the window buttons in with it, and a
  rounder Dock moves its end icons in, so neither crowds the curve.
- Files: folders in the sidebar (Favorites, Locations) can be dragged onto
  the Dock as stacks; a pinned folder dragged in the sidebar moves.
- Dock folders show the folder's icon by default (the newest file's
  picture stays as "Display as > Stack").
- Colour picker: a colour wheel -- drag the knob for hue and saturation,
  a slider for brightness -- in place of the grid of swatches.
- Game controllers: desktop control turns on or off by holding LB + RB +
  L3 + R3 (L1 + R1 + L3 + R3) together, instead of pressing the Xbox / PS
  button 5 times. The Xbox / PS button opens Mission Control at once.

### Fixes
- Video wallpaper: the login screen's video stayed paused after a restart
  (GStreamer slow to open it at boot), and the desktop's could stay paused
  with no window open after closing the last one.
- Video wallpaper: changing the wallpaper after a video sometimes needed
  Sonata restarted (the first frame switched the desktop from GStreamer's
  thread); a big video went black after its end until a window moved.
- Video wallpaper: kept playing while a window was dragged and under
  Mission Control (Super+Tab).
- Video wallpaper: the lock and login screens and Mission Control's
  desktops were black when the video fades in (its poster was a black
  frame); a bright frame is taken now.
- Downloads in the Dock: its panel no longer runs across the screen when
  a file has a long name with no spaces.
- A greyed-out zoom button (Settings) turned green with custom button
  colours.
- Language & Region: changing the language moves the formats region along
  when it went with the old language (dates and months stayed in
  Portuguese after switching to English). A region picked apart stays.
- Window buttons on the right in title bars Sonata draws for other apps
  (WhatsApp, VS Code...): the same distance from the edge as on the left;
  the close button was pushed past the edge and cut by the corner.
- Window buttons on the right in WhatsApp, VS Code and other Chrome /
  Electron apps: the close button keeps its room from the edge (the
  window's corner cut it).
- Window buttons on the right in Bazaar and other GNOME apps left running
  while the side changed: 20 px apart and in Sonata's place, not lower and
  further in (an app reads its style once, at start; both sides are always
  written now).

## 0.19.4-alpha -- 2026-10-19

Desktop icons stay where you put them: sorting, deleting, Clean Up and
drops no longer shuffle them, and a shortcut whose original is gone says
so. Window buttons on the right sit in the same corner as on the left, in
Sonata's apps and in GTK 3 apps. Settings no longer closes when Dark Mode
changes with an alert open, and fits a narrow window in every section.

### Fixes
- Desktop: dragging one icon while Sort By is on no longer sends every
  other icon back to where it was before sorting.
- Desktop: deleting a file no longer shuffles the icons after it, and
  icons keep their spots after logging in again.
- Desktop: Clean Up leaves the icons on the other display where they are
  (they all went to the main display).
- Desktop: several files dropped on the desktop land together where they
  were dropped (only the first did; the rest went to the top right).
- Desktop: renaming an icon to a name already in use says so, and no
  longer moves the other file's icon.
- Desktop: opening a shortcut whose original was deleted says the original
  can't be found, with Move to Trash (it opened an empty Quick Look).
- Desktop: icons follow the menu bar right away when Taller menu bar is
  switched.
- New folders and shortcuts are named in Portuguese ("pasta sem título",
  "Projetos - Atalho").
- Switching Dark Mode while an alert is open (Settings' "Restart Sonata
  now?", any app's) no longer closes the app.
- Settings > Apps > Hidden & Protected Apps: after the password the hidden
  apps show where the password field was, not at the bottom of the section
  under every installed app.
- Settings > Desktop & Windows > Reset: a double-click on a window's top
  edge zooms it again, like the title bar.
- Settings > Menu Bar > Reset: a menu bar made shorter goes back to its
  taller height at once.
- Settings > Displays no longer makes a narrow Settings window wider: the
  wallpapers wrap to four on a line.
- Settings > Keyboard Shortcuts in a narrow window: a shortcut's name
  breaks between words ("Overview" came out one letter per line) and
  "Swipe up with 3 fingers" wraps.
- Settings > Keyboard > Add: "Add Input Source…" is no longer cut in
  Portuguese.
- Window buttons on the right (Settings > Appearance): in Files and
  Preview's file chooser they sit at the window's top corner, where they
  are on the left (they were centred in the toolbar, lower and further
  in); Settings' and dialogs' close dots keep the same room from the
  right edge.
- GTK 3 apps (and Chrome in its GTK mode): the window buttons sit in
  Sonata's place on either side, and the theme's x / - / square no longer
  shows over the dots all the time.
- Settings > Keyboard: a shortcut with two key combos shows them one
  under the other; side by side they made a narrow Settings window wider.

## 0.19.3-alpha -- 2026-10-17

Alt+Tab picks a window in Mission Control and a right-click under the Dock
opens a taskbar menu. The FPS limit works for Steam at login, window
animations come back after a game, the video wallpaper fills the whole
screen and dark menus are dark in every app. Lighter all round: Files
opens big folders several times faster, Control Center opens in a blink and
the shell no longer wakes up while idle.

### New
- Dock: a right-click on the Dock's own space -- the strip under it, or
  its plate between the icons -- opens a menu like Windows' taskbar: Task
  Manager, Show Desktop, Turn Hiding On/Off, Position on Screen and Dock
  Preferences…. An icon or the divider keeps its own menu.
- Mission Control: Alt+Tab (and Alt+Shift+Tab) moves the highlight from
  window to window, and letting go of Alt opens the one highlighted -- the
  app switcher's list used to open hidden under Mission Control (Sonata's
  Wayfire: wayfire-scale-tab.patch, built again by the next update).

### Fixes
- Four texts left in English in Portuguese are translated: the Close
  button's tooltip (every window), Assistant's "No Conversations",
  Calendar's "No Results" and Camera's "Open".
- Video wallpaper: no black band down the right of the screen. GTK draws a
  video's frame at frame size / video size, from the left, and a decoded
  frame a little narrower than the video (hardware decoding) stopped short
  of the edge; the wallpaper now asks for that much more.
- The Control Center icon no longer turns dark in Light Mode after the Sound
  menu (or any menu bar panel) closes: every item in the menu bar is drawn
  anew once a panel closes, not only the one that opened it.
- FPS Limit works again for Steam set to Open at Login: started from its
  login entry, it never got frame-pacer's switch (nor its graphics card), so
  its games weren't limited until Steam was opened again from the Dock.
- Dark Mode: the menus of GTK 3 and Electron apps are as dark as their menu
  bar -- GitHub Desktop's menu bar and its menus stayed grey (Electron paints
  both in the menus' colour).
- Window animations (and blur) come back after a full-screen game even when
  the session's Wayfire config couldn't be written at that moment: Sonata
  tries again instead of leaving them off until the next login.
- A Wayfire option Sonata adds to a config file that doesn't end with a
  newline goes on its own line instead of being joined to the last one.
- The on-screen keyboard and the gamepad's pointer can't be left broken by
  a half-made protocol file in the cache (Sonata stopped while making it).
- Screenshots: Capture in the toolbar (Print) no longer sometimes does
  nothing -- it began before the toolbar had gone, while it still held the
  keyboard.
- The menu bar's Sound menu has no list of outputs under its slider any
  more: the slider's output button picks it, as in Control Center.

### Performance
- Files lists a big folder about four times faster (5,000 files: ~10.5 s
  -> ~2.5 s): each file's cell no longer reloads Sonata's whole stylesheet.
- Files lists a big folder about three times faster again (5,000 files:
  ~2.5 s -> ~0.6-0.9 s in each view): the icons view makes cells for what
  its width holds (it kept 2,049 ready), the two views not shown no longer
  make, sort and fill theirs, and a file named like a picture that isn't
  one is no longer looked at again on every listing.
- Lighter on the battery while nothing happens:
  - the Dock, wallpaper, Launchpad, Spotlight and every Sonata app no
    longer wake up once a second (a diagnostics check now waits for its
    file instead of looking for it);
  - the menu bar follows Wi-Fi and the battery from NetworkManager's and
    UPower's own signals instead of running nmcli every 10 seconds;
  - app windows no longer run code on every frame of an animation (Disk
    Utility did ~60 times a second while its spinner turned).
- Settings > Displays opens faster and holds ~40 MB less: the wallpaper
  preview is read in the background, at its own size.
- Settings > Displays and Menu Bar open faster the first time (~95 ms ->
  ~75 ms and ~85 ms -> ~45 ms): they no longer load Files' package
  installer or the whole menu bar only to read a setting.
- The menu bar no longer pauses for a moment just after it appears at
  login (a version report now runs in the background).
- The menu bar runs about 40% fewer programs in its first seconds (~33 ->
  ~19): Wi-Fi, Bluetooth and Airplane Mode are read once for the menus and
  Control Center alike, and Control Center is warmed up once, not twice.
- Control Center opens about five times faster (~125 ms -> ~25 ms to its
  first frame): it is kept between openings and only brought up to date
  as it opens, instead of being built again, in a new panel, every time.

## 0.19.2-alpha -- 2026-10-17

Sonata's title bars on every app that can take them, a lighter video
wallpaper (silent, no slowdown after changing it), a Notification Center
that opens at once, and web apps that count their notifications again --
WhatsApp Web loads again too. Plus a Volume Mixer aligned with the Sound
sliders, with an output per app, and menus that no longer grow with long
names.

### Fixes
- Dark Mode: menu bars of GTK 3 and Electron apps (GitHub Desktop, VS Code,
  Discord...) are as dark as the title bar above them, not grey.
- VS Code (and VSCodium, Code - OSS, Insiders, Flatpak too) gets Sonata's
  title bar again, set at every login and whenever the option is turned on
  -- whatever was chosen since. Its menu bar follows Sonata's theme and Dark
  Mode now; it had been left with its own title bar for that.
- Every app that has a setting for it gets Sonata's title bar at every
  login: Chromium browsers (Chrome, Brave, Edge... -- the tabs below
  Sonata's bar) and Obsidian too, besides VS Code, Firefox, Thunderbird and
  Vesktop. Apps without one keep their own, so no window shows two.
- WhatsApp Web loads again (it stayed on its loading screen): Sonata no
  longer runs a watcher of its own inside a web app's page; it only reads
  the page's title, as before.
- Web apps on Chrome: the Dock counts both the page's title and the
  window's, so a stale one no longer hides the count.
- A web app's notifications on Chrome are told apart by the browser
  process that sends them, not only by the site in their text (Chrome
  rewrites its command line, so its profile wasn't found).
- Notification Center opens smoothly again with notifications in it: a new
  notification adds only its own card instead of rebuilding every card and
  the calendar, which the next opening then had to lay out again (~0.25 s
  with 100 notifications, now ~10 ms). Each card's time ("5m ago") is now
  current when it opens.
- Video wallpaper: changing it several times no longer slows the desktop
  down. Each video let go of is freed once paused, and the memory goes back
  to the system: a player freed while GStreamer was still busy with it was
  never freed, its threads and ~30 MB staying behind at every change.
- Video wallpaper: no sound, ever. The wallpaper's player has no audio
  output at all, rather than a muted one the sound server could unmute.
- Menus: a very long item (a window's title in the menu bar's Window menu,
  a recent file, a tray app's entry) ends in "…" instead of making the whole
  menu as wide as its text; hovering it shows the full text.
- Control Center: the Volume Mixer's sliders line up with the Sound
  module's (same edges, same size) -- they started further in and ended
  earlier.

### New
- Volume Mixer: each app has an output button, like the Sound module's,
  that sends that app alone to another output (the one it plays on is
  checked) -- at its slider's right, in Control Center and in the menu
  bar's Sound menu alike.

## 0.19.1-alpha -- 2026-10-17

Quicker to open and smoother to use: the Apps menu opens at once, the
Control Center's first opening is as fast as the rest, translucent windows
no longer flicker, and Mission Control's windows drag at once -- onto + for
a new desktop.

### New
- Mission Control: drop a window on + for a new desktop with the window on
  it.

### Fixes
- Translucent windows (Terminal and Sonata's other apps) no longer flicker
  where they redraw a little (a blinking cursor), nor show a faint square
  around the pointer: the blur of a small part is computed on the same grid
  as the whole screen's (Sonata's Wayfire: wayfire-kawase-align.patch).
- Faster: the Apps menu (Launchpad) begins to open at once from the Dock and
  ~0.1 s after Super / F4 -- every opening started a whole Sonata process
  (~0.5 s) only to tell the one already running.
- Faster: the Control Center's first opening after login is as quick as
  the others (it's built once, unseen, a few seconds after login).
- Mission Control: a window follows the pointer as soon as you drag it --
  it stayed in its place until pulled about 200 px (Sonata's Wayfire:
  wayfire-scale-sonata-events.patch).

## 0.19.0-alpha -- 2026-10-16

Mission Control grows up: right-click a window for its Dock menu, drag it
onto another desktop, close it from its corner, a click on the empty space
to leave; animated desktops. The Dock's window previews show the windows,
WhatsApp's window its name, and Sonata is lighter: the Control Center
opens at once, with its sliders where they are.

### New
- Mission Control: a click where no window is closes it, back to the window
  that was in front.
- Mission Control: the window under the pointer has a close button (x) at
  its corner.
- The small close buttons -- on Mission Control's windows and desktops and
  on the Dock's window previews -- are on the side of your window buttons
  (Settings > Appearance).
- Mission Control: right-click a window for its app's Dock menu (its
  windows, Options, Minimize, Quit...); hold a window and drag it onto a
  desktop in the bar to move it there. Desktops grow in when added and
  shrink away when closed, the others gliding to their places. (Sonata's
  Wayfire: wayfire-scale-sonata-events.patch.)

### Fixes
- WhatsApp's web app window is titled "WhatsApp" (its name in Sonata), not
  "web.whatsapp.com", while the page gives Chrome no title; once it does
  ("(3) WhatsApp"), that one shows.
- The Dock's window previews show the windows: their pictures came back
  empty (transparent) on some GPUs. Sonata's Wayfire plugin now has them
  drawn and read by the renderer itself.
- Mission Control: a window's menu (right click) closes with it -- its
  items stayed on the screen.
- A video in full screen no longer comes back from Mission Control with
  blue in its four corners (its rounded corners were cut and filled).
- Faster: the Control Center opens at once with its sliders already at
  the last levels read, and only moves them to the live ones -- it set the
  volume, the microphone and the brightness again on every opening (with
  DDC/CI, seconds of `ddcutil`), which also hid its opening animation.
  Sound reads no longer wait for an external display's brightness; a
  computer without DDC/CI displays isn't probed again on every opening.
- Faster: the app a window belongs to is remembered a few seconds instead of
  its .desktop file being read again on every window event.
- Lighter, more of it:
  - `sonata2 keep` (polkit's agent, all session long) sleeps until its
    program ends -- it woke 20 times a second;
  - the Dock's window previews never take screen pictures (grim) where
    Sonata's plugin draws them, and never two at once;
  - the menu bar's icons are drawn again only when the front app or the
    windows change, not on every title change;
  - the Downloads stack in the Dock isn't listed again while a download is
    being written, only when a file comes, goes or is done;
  - Mission Control's desktops bar is rebuilt once after a burst of window
    events, not once per event;
  - a change to the Dock's settings no longer re-reads the appearance in
    every Sonata process;
  - per-app volumes changed outside Sonata are saved after 5 s, not 1.5 s
    (each look reads every playing stream).
- Faster: every sound stream's events no longer read the volume again (many
  a second while something played); quit-on-close no longer lists every
  window on each title change of an app whose process it never found.
- The Dock's notification badges come back after the menu bar restarts --
  they only came back with a new notification.
- Dock previews show again on an icon after a window was picked from them
  (they were taken as still open), after 1 s on the icon, and for an app
  with a single window too.
- A click on a window in Mission Control brings it forward even where
  Wayfire's lookup found no window under the pointer -- only minimized ones
  came forward (Sonata's Wayfire: wayfire-scale-click.patch).
- Mission Control (Super+Tab) closes Launchpad and the Apps menu.
- Mission Control, its desktops bar and the wallpaper are never blurred.
- Mission Control's desktops work: a click goes to that desktop (an empty
  one too) and closes Mission Control; x closes a desktop, its windows going
  to the one before. The pictures are always there, centred at the top with
  + beside them (centred on the pictures), in room Mission Control keeps
  free: they no longer cover the windows. One desktop to start with.
  Closing desktop 1 closes its apps (there is no desktop before it to take
  them); the only desktop's x does the same.
- Mission Control opens on an empty desktop too, and stays open when its
  last window closes (Sonata's Wayfire: wayfire-scale-empty-desktop.patch) --
  an empty desktop could not be left.
- Mission Control only darkens the desktop behind the windows: no blur
  (lighter on the machine).
- Dock previews: after the pointer rests on its icon, as far from the Dock as a maximized
  window; small pictures of the windows themselves, the size of Mission
  Control's desktop pictures (no big empty frame: the app's icon, small,
  until one comes), and x on one closes that window. The preview is as
  small as its pictures (a window's whole picture made it ~1000 px wide), in
  a thin frame; with only the old way of taking the pictures (grim), they
  are taken again (Wayfire 0.12's sizes with decimals were refused).
- A video wallpaper is silent even where GStreamer's player starts with its
  own volume, and fills the screen whatever its shape (cut to fit, centred).
- Switch User asks for the password once: the login screen checks it and
  brings your session back unlocked (it went back without checking, and the
  lock screen asked again).
- The menu bar's warm-up no longer stops half-way with some PyGObject
  versions ("does not allow None"): its surface takes clicks everywhere again.

## 0.18.0-alpha -- 2026-10-16

Mission Control with desktops on Super+Tab, videos as wallpapers, Switch
User on the lock screen, every window of an app in the Dock and in Alt+Tab,
Sonata in Portuguese (Brazil and Portugal), and Electron apps that resize.

### Changes
- install.sh builds Wayfire with Sonata's fixes (and Wayfire 0.12 on
  systems with an older one) without asking: it says what it builds and
  how long it takes.
- A new wired network icon, an Ethernet port: in the menu bar when the
  connection is a cable, not Wi-Fi, and on Settings' Network section.
- Lock screen: Switch User, when the computer has another user. Your
  session stays locked; a user who is already logged in goes straight back
  to their session (their own lock screen), anyone else gets the login
  screen on a free console. With Sonata's login screen that is a second
  greetd, installed by `install.sh --greeter` with a polkit rule allowing
  only it to be started without a password; GDM and LightDM use their own.
- Videos as wallpapers: choose one in Settings > Wallpaper, or right-click
  a video on the desktop or in Files > Use Video as Wallpaper. It plays in
  a loop, silent, only while the desktop is in front: any app in front,
  the lock screen or a dark display pause it (no decoding meanwhile). One
  decoder serves every display showing it. A frame of it (made once with
  ffmpeg) is what the lock and login screens blur, and what shows at login
  until the video starts.
- Super+Tab is Mission Control (Windows' Task View): every window laid out,
  and desktops as on Windows -- their names at the top as in macOS Ventura,
  the current one in a glass pill, each one's picture (wallpaper, its apps'
  icons) while the pointer is over the bar. + (top right) adds a desktop,
  x on a picture closes one (its windows go to the desktop before it), a
  click goes there. Alt+Tab stays the app switcher; a wayfire.ini kept
  from before is moved over at login.
- Alt+Tab: an app with several windows has one icon per window, side by
  side, the window's title under the selected one; picking one brings that
  window alone forward. One press still goes to the previous app.
- Dock: hovering a running app shows every window it has open (minimized
  ones too), live, drawn by Sonata's Wayfire plugin -- covered windows or
  windows on another desktop as well; click one to bring it forward.
- Settings no longer gets stuck on a section after adding (or deleting) a
  user: the page rebuilt in place left a picture of itself on top of every
  section opened after it.
- Settings > Dock > Folders in the Dock: the Dock's folders, each with
  Remove; Downloads back with one click once removed; any other folder
  (Add Folder…). The running Dock follows at once.
- External monitors' brightness on NVIDIA cards: their DDC/CI bus is found
  through ddcutil's own detection (by connector, else by the monitor's
  EDID) when the driver links none to the display -- the slider did
  nothing with DDC/CI on.
- Locked after the display went dark, it stays dark: the lock screen no
  longer shows black with the pointer in the middle until its own 30 s
  pass. Dark while locked also hides the pointer and turns plugged-in
  monitors really off, as when unlocked (the laptop's panel: backlight only).
- Electron apps (VS Code, Claude...) no longer snap back to their old size
  after a resize: a frame drawn before the app took its new size, coming
  late, was taken by Wayfire as the app resizing itself and its old size
  sent back to it. Fixed in Sonata's Wayfire (wayfire-configure-ack.patch);
  install.sh builds it again when Sonata's Wayfire patches change.
- X11 apps (through XWayland: Electron in X11 mode, GTK 2/3, Wine...) get
  Sonata's look -- theme, Dark Mode, icons, cursor, fonts -- from Sonata's
  own XSETTINGS manager, live; their GTK menu bars were light in Dark Mode.
- VS Code is no longer forced to the system title bar (its GTK menu bar
  was light): it keeps its own, in its own theme. Where Sonata had set it,
  it goes back once.
- Web apps on Chrome: after the page moves within itself (WhatsApp) or
  loads, its own title is set again so Chrome takes it for the window --
  the page's name and unread count, no name made up for it.
- Dock: an app opening gets its place smoothly -- the neighbours slide
  aside, then its icon fades in -- as its place closes up when it quits.
- About This Computer shows all of its content: it grows to it once the
  specs are read (it kept the height it opened with, and longer lines --
  Portuguese's -- needed a scroll bar).
- Apps and the menu bar show Sonata's apps' names in the system's
  language right after an update (a launcher rewritten in place went
  unnoticed until something else changed its folder); the menu bar's
  "Files" with no window in front, and Calendar's window title, too.
- Sonata's apps are named in the system's language (Arquivos / Ficheiros,
  Calendário...): their launchers carry the Portuguese names and
  descriptions too, so the Dock, Apps and the menu bar show them.
- System Settings is now just Settings (Ajustes / Definições).
- Dock: while the Apps Menu is open, Launchpad's icon has the dot an open
  app has.
- Dialogs and panels with a Cancel, Close or Quit button no longer get the
  close dot too.
- Web apps on Chrome: the Dock's unread count follows the page's title
  ("(3) WhatsApp") -- Chrome titles the window with the site's name, so it
  never showed. The menu bar names them right again (it showed "Sonata2
  Webapp W6bf…" after a web app was made again).
- Resizing a window without its contents: the panel that follows the
  pointer is frosted glass like the windows' (their colour, background
  colour, transparency and blur strength; solid with the windows' glass off
  or Reduce transparency).
- Settings > Language & Region (as on a first install): the system's
  language, and apart from it the region of its formats -- dates, times,
  numbers, money, measures, paper -- with an example, and the first day of
  the week (the Calendar's). The keyboard stays in Keyboard (Portuguese
  (Brazil) with a Portugal keyboard, say). Set for the whole system
  (localectl, the locale generated first when missing; one password) and
  in effect at the next login.
- Sonata in Portuguese: Brazil's and Portugal's, following the system's
  language by itself (English otherwise). Strings that read differently
  by place have their own words ("Zerar" on the stopwatch); a few
  subtitles were shortened so the longer Portuguese fits.
- The background colour of Light and Dark Mode tints the glass too: the
  Dock, the menu bar, menus and panels, sidebars and glass title bars,
  moved as much as they show (as see-through as before). Eight colours per
  mode, as many as the accent colours (new: Butter, Teal).
- Web apps (Chrome): a window whose title read the site's address
  ("web.whatsapp.com") shows the page's title again -- or the web app's
  name, for a page without one.
- Files: the Trash no longer shows an item of itself inside
  ("Wastebasket", after opening and closing it).
- The Copy window is as tall as its copies: shorter when one ends, and
  new for the next batch (it came back as tall as it once was). About This
  Computer opens at its content's height.
- Files: a folder that takes a while to open (a big one, a network share)
  shows Finder's spinner and "Loading…" until it is in; a quick one never
  flashes it.
- Files and the desktop: Windows programs (.exe, .dll) show their own
  icon, read from the file itself (its resources; only the headers and
  icons are read, never a whole big game).
- Files and the desktop: an edited picture or video gets a new thumbnail
  (the old one stayed: GIO still pointed at it, and two saves in the same
  second shared one).
- Files' path bar: right-click > Copy Path copies the folder under the
  pointer (or, on the bar's empty part, the folder shown). Typing an
  address there, the field's focus ring is drawn inside it (it was cut off).
- ç on U.S. International: ' + c types "ç" (and ' + C "Ç"), as on Windows
  and macOS, instead of "ć". Set at login in ~/.XCompose, after the
  locale's own table; a file of your own keeps its lines. Apps opened
  after the next login follow it.
- Settings > Appearance: the background colour of Light and Dark Mode,
  picked like the accent colour -- a few for each mode, the default first
  to go back to, any colour through Sonata's colour picker. Windows,
  sidebars, menus and title bars follow it (Sonata's, Wayfire's and other
  GTK apps'); a colour too dark for Light Mode (or too light for Dark) is
  eased until the text reads well.
- Right-click > Install (and a package's double-click) asks for the
  password first, in Sonata's own prompt (pkexec), instead of sudo asking
  inside the terminal; the terminal still shows the install.
- Every Sonata dialog (Edit Web App, errors, confirmations...) and the
  panels that work as forms (Change Icon, Get Info, Disk Info, Resize,
  Export, Adjust Color, the Assistant's settings, a Calendar event) have
  the windows' close dot -- their look, colours and side -- at the top
  corner; it closes like Escape. None is added where one already is.
- Notifications stay unread until read: bringing an app to the front no
  longer clears its Dock count, and a web app (WhatsApp) taking its
  notifications back as its window is focused only hides the banners --
  they stay in Notification Center until clicked or dismissed. The app's
  own count (LauncherEntry, the page's "(3)" title) still comes first.
- The Sonata menu's logo (top left) is drawn anew with the other menu bar
  icons (an app window gone, its menu closed, Light/Dark, a new display
  scale): it could go blank.
- Files and the desktop: a shell script's right-click menu has Run in
  Terminal (its output in a window that stays open) and Run (in the
  background). Scripts are .sh / .bash / .zsh files, or executables that
  start with "#!"; one without the executable bit runs through its "#!"
  line or its shell. A double-click still opens it to read.
- Preview: right-click > Copy puts the picture on the clipboard as shown
  (its edits, and Markup's marks while Markup is open). Quick Look in
  Files: right-click a picture > Copy Image.
- Software Update asks for the password once, not at every package: install.sh adds a polkit rule that keeps the
  administrator's "yes" to pacman for a few minutes, for that update only.
- Displays nobody picked a scale for get one that keeps the size of a
  1080p desktop monitor: from the display's DPI (the size it reports; a
  laptop's panel, seen closer, a higher reference), else its resolution;
  never under 100 % (blurry, and harder on the GPU). It follows a new
  resolution until a scale is picked in Settings > Displays (100-300 %).
- Software Update runs System and AUR as one paru/yay run: one password.
- The emoji and clipboard pickers open in the middle of the screen, a
  little lower, and a click anywhere outside closes them (the emoji one
  was at the top left, the clipboard one at the top and never closed).
- A picked emoji is pasted into the app instead of typed as keys:
  WhatsApp and other Chromium/Electron apps got other characters.
- Copy in Preview and Quick Look sends the file itself when unedited
  (PNG, JPEG, GIF, WebP as they are: no slow re-encoding of big photos);
  an edited picture is copied at full size.
- install.sh no longer asks "mount system disks without a password": the
  rule is added for administrators by default (it lowers security: any app
  in the session can mount system disks and unlock system LUKS);
  --no-mount-without-password removes it.
- Light Mode glass is more see-through by default (Dock, menu bar, menus
  and panels 0.60 -> 0.45; sidebars 0.74 -> 0.58), closer to how Dark Mode
  reads: a pale tint at the same opacity looked milky, almost opaque. Wayfire's
  blur threshold follows the theme's own glass, so it stays frosted; the
  opaque title bars keep their colour.

### Fixes
- After a Light/Dark switch, a menu bar icon (often Control Center's) or a
  Dock count badge could keep its Light Mode look until clicked: once the
  change settles, the menu bar looks its icons up again and the Dock draws
  every part anew.

## 0.16.1-alpha -- 2026-10-10

Fixes for installing Sonata on other computers: the login screen, title bars and the wallpaper.

### Fixes
- install.sh: Sonata's login screen replaces whichever login manager the
  system uses (Plasma Login on CachyOS stopped the install: "display-
  manager.service already exists"); `install.sh --gdm` puts it back.
- install.sh refuses `sudo ./install.sh` (Sonata then went to root's home
  and every login went back to the login screen); it asks for the password
  itself. `--system` still installs it for every user.
- pixdecor built by install.sh gets its settings file (pixdecor.xml):
  without a system pixdecor, Wayfire aborted as it loaded and every login
  went back to the login screen. And if Wayfire still ends at once, the
  session starts it again without the plugins built here.
- A first login shows Sonata's "Mountains" when the picture taken over
  from the previous desktop can't be shown (missing, or a format like
  .jxl): it showed a bare gradient.

## 0.16.0-alpha -- 2026-10-10

Vulkan and WebGPU per app, window buttons that follow your choice in every
window, Settings that installs what it needs, USB protection that works,
the Dock's badges in your accent colour, and many fixes.

### Changes
- Control Center: the whole Do Not Disturb square turns it on and off,
  not only its round button.
- Files sidebar: a disk's free space sits on its name's line; the disk's
  icon turns into Eject (a short crossfade) while the pointer is on it, and
  a click there ejects it. A right click on a disk: Open in New Tab, Get
  Info, Eject / Unmount, Open Disk Utility.
- The menu bar's icons are drawn anew when an app's window goes or the
  in-use dots change (the Control Center icon went blank after closing
  Chrome).
- Lock and login screens: the picture, name and password sit a little
  higher (7% of the display above the middle).
- The lock screen's background is blurred once, as a small finished
  picture (it once showed a ghost of another window; no GPU blur pass
  any more -- Mission Control's backdrop too).
- An app opened from the Dock that shows no window (30 s; Steam 90 s, it
  updates itself first) is force-closed and opened again, up to 3 times;
  then a notification says it couldn't open. An app that can't be started
  at all says so at once.
- The same for apps opened from Launchpad and Files (an app, or a file in
  its app; not when the app was already open).
- A dark accent colour in Dark Mode: icons, accent text, progress bars and
  sliders use a lighter tone of it automatically -- no glow behind them
  any more. The accent itself stays where it fills (selected rows,
  buttons).
- The desktop's Install / Extract alerts come in front of the desktop.
- Web apps on Chromium no longer get the browsers' WebGPU flags (they
  showed "unsupported command-line flag: --enable-unsafe-webgpu"): they
  start the browser's own program with the user's other flags.
- Settings: everything that needs installing has a button that opens
  Terminal with the command running (Night Shift's wlsunset, automatic
  locking's swayidle, the firewall, CUPS, Screen Sharing's wayvnc), and
  the page updates itself once it's there.
- USB protection works once USBGuard is installed: Security & Privacy >
  USB tells what's missing (not installed, services not running, no
  permission) and Set Up does all of it -- installs it, allows every
  device while unlocked (USBGuard's default blocked them all), lets your
  account block new devices while locked, starts it now and at boot.
- Files > Applications updates itself when an app is uninstalled (dragged
  to the Trash) or installed.
- install.sh installs Bazaar, the app store (Flatpak apps from Flathub;
  --no-bazaar skips it).
- Block new USB devices while locked is off until you turn it on.
- install.sh turns the firewall on (ufw, the first time only: turned off
  in Settings it stays off; --no-firewall skips it). Nothing comes in
  unasked; from the local network only Screen Sharing and printer
  discovery.
- Web apps never show Chrome's "unsupported command-line flag" bar.
- The "loading the desktop" screen after logging in keeps the picture
  where the login screen had it (it dropped back down).
- Settings > Apps: a spinner while an app's permissions are read.
- No "Manage Printing" app (CUPS' web page): printers are in Settings,
  which offers Turn On when CUPS is installed but not running.
- Window buttons on the right / Button colours: windows already open
  follow at once (Settings itself, Files, every Sonata app); GTK 3 apps
  and Chrome's GTK mode get Sonata's buttons in the chosen colours, with
  room at the edge (the close button was cut on the right).
- Steam's menu in the menu bar: its items work again and the menu bar no
  longer freezes after a click; Store / Library / ... bring Steam's window
  to the front.
- Login, lock and "loading the desktop" screens: the picture, name and
  password field (or spinner) are centred as one block, 3% above the
  middle of the display.
- Removed "Turn RGB lights off with the screen" (it left the keyboard
  dark); install.sh still installs the OpenRGB app.
- install.sh installs what Settings would ask for (ufw, CUPS, USBGuard
  too), turns printing on and sets USBGuard up the safe way, with a
  warning (--no-usbguard leaves it alone). Set Up in Settings warns first
  too: USBGuard on its own blocks every USB device, keyboard included.
- The Dock's count badges use your accent colour (a lighter tone of a
  dark accent in Dark Mode), the number white or black, whichever reads.
- Vulkan and WebGPU per app: right-click an app in the Dock or Launchpad >
  Use Vulkan / Allow WebGPU, or Settings > Apps > the app > Graphics.
  Chromium browsers, web apps and Electron apps: Vulkan through XWayland,
  WebGPU for 3D web games; Firefox: WebGPU; Steam, Lutris, Heroic,
  Bottles: Vulkan (DXVK) or WineD3D; other apps: GTK 4 / Qt 6 draw with
  Vulkan. Replaces Settings > Displays > Games > WebGPU in Browsers (the
  browsers it was on for keep it).
- Open in Sandbox is only in the Dock's menu (not Launchpad / Apps Menu),
  and not offered for web apps (they couldn't open there).

## 0.15.0-alpha -- 2026-10-10

A lighter Sonata -- less work for the CPU, the GPU and memory, the same
look -- WebGPU for browser games, reminders that notify, every GPU in Task
Manager, and many fixes.

### Changes
- Settings > Displays > Games > WebGPU in Browsers (off by default): 3D
  browser games in Chrome, Chromium, Brave and Firefox. Chromium browsers
  then run through XWayland with Vulkan (on Wayland, Chrome's Vulkan draws
  an empty window); Sonata's web apps stay on Wayland. Firefox gets
  dom.webgpu.enabled.

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
- The drawing palette's icons no longer flicker while sharing the screen.
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
