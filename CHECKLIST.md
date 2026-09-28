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
