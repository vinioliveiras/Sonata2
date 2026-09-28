# Manual test checklist

Run each item on real hardware (CachyOS, Wayfire session) after the change
that introduced it. `[ ]` = not yet verified on hardware.

## M0 -- Skeleton
- [ ] `python3 -c "import sonata2.style"` works with the dependencies installed.

## M1 -- Dock (first cut)
- [ ] `python3 -m sonata2 dock --preview` shows the Dock over the sample wallpaper.
- [ ] In a Wayfire session, `python3 -m sonata2 dock` sits centered at the
      bottom, 4 px above the edge; maximized windows stop above it.
- [ ] Pinned icons come from `~/.config/sonata2/dock.json`; no broken icons.
- [ ] Hover shows the app name above the icon; it disappears on leave.
- [ ] Click launches the app; the icon bounces twice. Pressed icon darkens.
- [ ] Trash icon turns "full" when a file is deleted to the Trash, empty after
      emptying it; click opens the Trash in the file manager.
- [ ] Switching the system to dark mode re-colors the Dock live.

## M1.2 -- Dock glass + running apps
Setup: merge `config/wayfire.ini` into `~/.config/wayfire.ini`, restart Wayfire.
- [ ] The plate is frosted: the wallpaper behind it is blurred and a bit more
      saturated; no blurred square around the rounded corners.
- [ ] Opening an app from the Dock: it bounces until its window appears, then
      a dot shows under it.
- [ ] An app opened elsewhere (terminal, launcher) that isn't pinned appears
      after the pinned apps with a dot; it disappears when it quits.
- [ ] Click a running app whose window is behind others: its windows come to
      the front. With all its windows minimized: they are restored.
- [ ] Minimizing a window animates into its Dock icon.
- [ ] Apps whose app_id differs from the .desktop name (e.g. Steam, Chrome)
      still map to the right icon (no duplicate generic icon).

## M1.3 / M1.4 -- Dock menus + drag
Run `tools/dev-session.sh` (or a Wayfire session).
- [ ] Right-click an app: Big Sur-style menu above the icon (rounded, blue
      hover); the hover name label disappears.
- [ ] Running app: its window titles on top; clicking one focuses it. Hide
      minimizes all its windows; Quit closes them.
- [ ] Options > Keep in Dock unchecked: icon leaves (or moves after the
      pinned apps if running). Checked again: stays.
- [ ] Options > Open at Login creates ~/.config/autostart/<app>.desktop;
      unchecking removes it.
- [ ] Options > Open File Location opens the file manager with the app's
      executable selected (try an AppImage, Firefox, a Flatpak).
- [ ] Trash right-click: Open; Empty Trash... disabled when empty, asks for
      confirmation, then empties.
- [ ] Drag an icon left/right: the others make room live; drop keeps the
      new order after restarting the Dock.
- [ ] Esc during a drag puts the icon back.
- [ ] Drag an icon out of the Dock and drop it on the desktop: removed.

## Dock -- round Trash icon
- [ ] The Trash is the round frosted can; it shows paper inside when the
      Trash has items and turns empty again after emptying.

## Sonata's own icons
- [ ] Change the system icon theme (e.g. to Adwaita): the Dock keeps the
      MacTahoe icons and the round Trash.
- [ ] An app MacTahoe has no icon for still shows its icon (system theme).

## Dock -- running dot
- [ ] The dot under a running app is vertically centred between the icon's
      bottom edge and the Dock's bottom edge (light and dark).

## M1.4 -- Dock file drops
- [ ] Drag a .txt from the file manager over the Text Editor icon: it
      darkens; drop -> opens in it (bounces if it wasn't running).
- [ ] Over an app that can't open it (e.g. Steam): no darkening, drop refused.
- [ ] Drop files on the Trash: they move to the Trash, icon turns full.
- [ ] Drag an app from /usr/share/applications (file manager) onto the Dock
      between two icons: it's pinned there and stays after a restart.

## M1.5a -- Magnification, resize
- [ ] Right-click the divider -> Turn Magnification On; moving along the Dock
      grows icons in a smooth wave above the plate; leaving eases back.
- [ ] Clicking a magnified icon works; clicks above the Dock (outside the
      icons) reach the window below.
- [ ] Drag the divider up: the Dock grows (ns-resize cursor); down: shrinks;
      the size stays after restarting.
- [ ] Maximized windows stop above the (unmagnified) plate at any size.

## M1.5b / M1.6 -- Position, auto-hide, recents, stacks
- [ ] Divider menu > Position on Screen > Left / Right: the Dock moves,
      icons stack vertically, dots sit between icon and screen edge, labels
      and menus open towards the screen centre; windows stop beside it.
- [ ] Turn Hiding On: the Dock slides out after the pointer leaves; touching
      the screen edge along the Dock brings it back (~0.3 s); a right-click
      menu keeps it visible; maximized windows now use the full screen.
- [ ] Open an unpinned app, quit it: it stays after a second bar (recent,
      no dot); after using 4 others only the last 3 remain. Divider menu >
      Show Recent Applications off: the section disappears.
- [ ] Downloads stack left of the Trash shows the newest file's icon; click:
      grid with names, click an item opens it, drag one to the desktop.
      Right-click > View content as > List; Display as > Folder; Sort by.
- [ ] Drop a folder on the Dock: new stack. Options > Remove from Dock.

## M2 -- Launchpad
- [ ] Dock shows Launchpad after Finder; click (or F4): the desktop blurs and
      the grid zooms/fades in; click empty space or Esc: it closes.
- [ ] Typing filters; arrows move the highlight; Return opens it.
- [ ] Swipe / scroll / Page Down changes pages; dots follow.
- [ ] Drag an icon: others move out of the way; hold it at the screen side
      to flip the page; release -> order kept after reopening.
- [ ] Hold an app over another ~0.5 s (it grows) and release: a folder named
      after their category; click it: panel with the apps; click the name to
      rename; drag an app out of the panel back to the grid.
- [ ] Drag an app from Launchpad onto the Dock: it gets pinned there.
- [ ] Press and hold an icon (or hold Alt): icons jiggle; your own shortcuts
      (~/.local/share/applications) have an x -> Delete asks, then Trash.
- [ ] Install/remove an app: it appears at the end / disappears.

## M3b -- Menu bar
- [ ] 24 px bar at the top, blurred/translucent; windows stop below it.
- [ ] Logo menu: About This Computer shows OS, machine, CPU, memory,
      graphics; Restart/Shut Down/Log Out ask first; Sleep and Lock work.
- [ ] Focus another app: its name appears in bold; Hide / Hide Others /
      Show All / Quit act on its windows; Window > Zoom maximizes.
- [ ] Wi-Fi icon shows signal; menu lists networks, switch turns Wi-Fi
      off/on, a secured network asks for the password.
- [ ] Sound slider changes the volume; icon follows (muted/low/high).
- [ ] Battery icon/level; Show Percentage adds the number.
- [ ] Control Center: Wi-Fi/Bluetooth toggles, Dark Mode switches every app
      and Sonata, Display/Sound sliders.
- [ ] Clock updates each minute; click shows the calendar.

## M3c -- Windows
- [ ] `tools/dev-session.sh`: a GTK app opened inside (e.g. Settings, Files,
      Text Editor) has the Big Sur title bar with close/minimize/zoom on the
      left, Sonata icons and the MacTahoe cursor.
- [ ] The same app opened in GNOME (outside) keeps GNOME's look.
- [ ] Dark Mode (Control Center) + reopening the app: dark Big Sur theme.
- [ ] An X11 app (e.g. xterm) gets Wayfire's light title bar.
