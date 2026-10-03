# Code review — October 2026

A read-only review of the whole project; no product code was changed. Each
area got a new test file, `tests/test_review_<area>.py` (369 tests). Tests
marked `@unittest.expectedFailure` with a `# BUG:` comment prove a bug listed
here: when it gets fixed, the test reports an "unexpected success" — drop the
decorator then, and it stays as the regression test.

Tags: **[xfail]** = proven by an expected-failure test.

## Fix first (data loss, security, stuck UI)

1. **Escape discards unsaved documents** — `ui/dialog.py:86` makes the first response the close response; TextEdit and Preview list "Don't Save" first. Use the first non-destructive / "cancel" response. [xfail]
2. **Files: Replace can delete what is being moved** — `files/ops.py:256` deletes the target before the move; moving `dir/x/x` into `dir`, or a copy failing after the delete, loses data. Refuse source-inside-target; copy to a temp name, then swap. [xfail]
3. **Calendar can lose or resurrect data** — `calendar/model.py:194` one thread per async write (older text can win); `:223` delete without the lock (a pending write brings the calendar back) [xfail]; `RECURRENCE-ID` ignored, an edited instance replaces the whole series [xfail]; BYDAY etc. dropped on save [xfail]; UTC/TZID series off by 1 h after DST [xfail]; all-day UNTIL written as DATE-TIME [xfail]; foreign properties (ATTENDEE, VTIMEZONE…) lost on the first edit.
4. **Notes: a corrupt notes.json is overwritten with an empty store** — `notes/store.py:52`. Move it aside (`.corrupt-<ts>`) before saving. [xfail]
5. **TextEdit corrupts NUL bytes after 8 KB** on save (`textedit/document.py:93`) [xfail]; no etag check, so changes made on disk by another program are overwritten (`window.py:239`).
6. **Preview save drops file mode, follows/replaces symlinks, strips EXIF/ICC** — `preview/edit.py:159`. [xfail ×3] Save As / Export add the extension after the overwrite check (`preview/window.py:594,656`).
7. **Disk Utility can erase a running-system member** — `diskutil/model.py:91` protects only the device that reports the mount (btrfs RAID 2nd member, zfs/md/bcache members). [xfail] The "checked again" in `diskutil/window.py:623` reads the same stale object.
8. **Greeter stuck after login** — "Other Users" stays clickable during login; `_started` then crashes on `self.user = None` (`shell/greeter.py:285`). [xfail] Login worker and lock screen catch only some exceptions: any other error leaves the spinner forever (`greeter.py:398`, `lock.py:391`).
9. **Security**
   - `pam.py:91` never calls `pam_acct_mgmt`: an expired/locked account unlocks the lock screen.
   - `install.sh:398` installs a polkit rule (mount system disks, unlock system LUKS without password) without asking.
   - `shell/polkit.py:299` doesn't check the caller; a missing uid defaults to 0.
   - `backend/selfupdate.py:109` puts the GitHub tag unquoted in a shell command (`shlex.quote`).
   - `webapps/window.py:164` hands any non-web link (`file://`, custom schemes) to the system without asking.
   - `files/packages.py:145` runs any `*.AppImage` on double-click (chmod +x) with no first-run confirmation.
10. **Config writes can collide between processes** — `config.py:26` uses one fixed `<name>.json.tmp` for all writers and `update()` is an unlocked read-modify-write; `wfconfig.py:~134` rewrites the live Wayfire ini in place (Wayfire can reload a half file). Unique temp + `os.replace` + `flock`. Also Settings sliders save on every pixel (`settings/app.py:2369`) and can overwrite the Dock's pins/folders.
11. **System updates can stall** — `backend/updates.py:202` stops reading the pipe at the first non-UTF-8 byte (pacman can block or get SIGPIPE mid-upgrade). [xfail]
12. **Dock freezes on a locked-folder password** — `shell/dock_folder.py:288` runs PAM on the main loop. [xfail]

## Bugs by area

### Dock / Launchpad / Desktop (`test_review_dock.py`)
- Interrupted animations never stop their `FrameStats` tick (frame clock awake forever, list grows): `dock.py:779` magnification [xfail], `dock.py:2114`, `launchpad.py:404`.
- `dock_stack.py:124` saves the fitted icon size instead of `user_size` (Dock shrinks for good) [xfail]; removed stack's FileMonitor never cancelled [xfail].
- `info.get_filename()` breaks with GioUnix (Open File Location, Open at Login, Launchpad Show in Files / drag) — `dock_menu.py:30,55`, `launchpad.py:1034,1135`. [xfail] (also why `test_dock.test_app_file_is_real_path` fails)
- `detach()` leaves `ui.on_change`, the LauncherEntry subscription and monitors: old Dock trees stay alive [xfail].
- Mission Control: a quick off/on hides the backdrop while open (`mission.py:213`) [xfail]; full wallpaper decode + blur each open.
- Poof lands in the wrong place (Dock vs monitor coordinates; side Docks) — `dock.py:1491`.
- `launchpad_model.reconcile` crashes on `"apps": null` [xfail]; `open_apps._read` on `{"apps": 5}` [xfail].
- Perf: sync Wayfire IPC on the main loop (`dock.py:1714` every reallocation…); Launchpad search O(N²) per key (`launchpad.py:874`); `config.load("dock")` on every allocation (`launchpad.py:388`); Downloads stack re-listed per monitor event (debounce).
- Launchpad's Apps Menu sized from monitor 0 (`launchpad_window.py:214`). Cleanup: `_ask_password` duplicates the Dock folder password panel.

### Menu bar (`test_review_topbar.py`)
- Recording never remembers the working encoder: `"encoder"` missing from `capture.DEFAULTS` [xfail].
- Each secondary display's bar starts its own clipboard watcher, gamemode watcher, BlueZ watch and power subscriptions (`topbar.py:101-139`); `Bar.stop()` leaks all of them on unplug.
- `wallpaper.py:89` StyleManager handler never disconnected: old wallpaper windows (~33 MB at 4K) stay alive.
- Control Center brightness targets the old display after the main display changes (`main_changed` doesn't update `bar.monitor`).
- Notifications: `replaces_id` ignored for apps with Notification Center off (banners stack) [xfail]; notes never capped and the closed Center rebuilds on each notification; `_state_key` reads `n.time` (should be `n.at`).
- Spotlight calculator: huge result crashes search [xfail]; nested powers are exponential [xfail].
- Night Shift toggle shows off during a scheduled night (`nightshift.py:156`) [xfail].
- Super+Tab focuses the newest window of an app, not the last used (`switcher.py:290`).
- MPRIS artist sent as a plain string isn't shown [xfail].
- Capture: display/window pickers run grim again per item (use the frozen screen); Pickers are never destroyed; clipboard screenshots stay in /tmp.
- Perf: album art cache unbounded at full size (`topbar.py:869`); tray recomputes every icon's silhouette per change; sync D-Bus proxies (`mpris.py:56`, `topbar.py:490`), `wlsunset` wait up to 2 s.

### Session / login / install (`test_review_session.py`)
- Doctor: false FAIL when PAM files live in `/usr/lib/pam.d` [xfail]; ignores `XDG_CACHE_HOME` for logs [xfail].
- greetd: missing `GREETD_SOCK` raises KeyError [xfail].
- ~~`idlelock.py:22` swayidle `-w` waited for `sonata2 lock`~~ — fixed: `sonata2 lock-wait` returns once locked (queued requests re-locked after each unlock: password asked 3× on waking).
- keyring rollback restarts KeePassXC while gnome-keyring still owns the secrets name (`keyring.py:261`).
- `self_command()` string is split / put in `sh -c`: a path with spaces breaks restart, autostart, updates.
- `intro.py:30` race → Setup waits the full 12 s; polkit dialog avatar doesn't follow the chosen account.
- Perf: portal rebuilds all settings (≈30 file reads) on every Read call (`portal.py:169`); `prefs.py` computes frame/wallpaper twice at import.
- `tools/greeter-setup.sh` revert doesn't undo quiet-console nor the keyring PAM lines.

### System / GPU / backend / Wayfire plugin (`test_review_system.py`)
- `gamemode.py:163` never restores options that were unset: blur stays off after a game [xfail].
- `backend/system.py:41` mixes stderr into stdout: a pactl warning breaks device JSON [xfail] (also timezone/computer name/browser).
- `trash_cleanup.py:47` aware DeletionDate → TypeError aborts the purge [xfail].
- `fullscreen.py:29` NOT_GAMES matched as substring of the title ("Control"…) [xfail].
- `titlebars`: a commented-out key in VS Code settings hides the setting [xfail].
- sonata-corners.cpp: opaque region cuts fixed 16 px corners but radius goes to 24 (dark corner artifacts, `:524`); `on_unmap` destroys the connection that is running (`:721`, UB); ~10 config lookups + parses per window per frame at 180 Hz (cache them, refresh on reload).
- Three copies of the `pactl subscribe` reader (`system.py:386`, `mixer.py:248`, `equalizer.py:204`), buffered readline in an fd watch, no respawn if pactl dies.
- `wl/toplevels.py:32` bindings cache written non-atomically (several processes at login).
- `flatpak_theme.py:37` any load error saves an empty file over the user's overrides.
- Perf: `icons.picture_icon` decodes before checking the cache; `_rendered` keeps up to 400 textures; `imageload.raw_preview` reads whole RAWs (up to 200 MB).
- No timeout: `gtkstyle.py:47`, `users._crypt`. `steamgames` caches unknown names forever.

### Files / Disk Utility / Preview (`test_review_files.py`)
- `udisks.error_text` keeps the `GDBus.Error:…` prefix in every alert [xfail].
- `paste_image` overwrites a paste from the same second and fails silently [xfail].
- Drop on Trash without `on_error` (USB/NFS fail silently) — `files/window.py:838`.
- Eject result checked with a fresh `can_eject()` → false "wasn't ejected" [xfail].
- Preview info shows portrait photos as landscape (EXIF orientation) [xfail].
- Perf: full-size decode on the main loop (Quick Look, Preview ←/→, save); `folder.py:224` linear scan per monitor event (O(n²) on big copies); sync `query_info`/move/enumerate for Put Back, Empty Trash (plays the sound before it succeeds).
- Cleanup: Preview's `ThumbLoader` duplicates `files/thumbs.py`; Disk Utility rename hand-builds `ui.dialog.ask_text`; `packages.extract` leaves a partial folder.

### Media: Task Manager, Music, Videos, Camera, Gamepad (`test_review_media.py`)
- Music scan follows symlink loops (duplicates, can hang) [xfail].
- Camera doesn't open with a dangling `*.jpg` symlink in its folder [xfail]; video thumb at a fixed `/tmp` path.
- Gamepad: pausing mid-drag leaves the virtual button held [xfail]; one plug → several full rescans (debounce).
- `fmt_cpu_time` shows "1:60.00" [xfail].
- Task Manager: pkexec gets a 20 s timeout (password prompt killed); fd scan of every process every 5 s; GPUs never pruned; Properties popup readlinks every second.
- Perf: Music rewrites the whole library JSON for each learnt duration and parses opened files on the main loop; Videos saves volume on every slider step (Music already debounces); camera device probe on the main loop.

### UI kit + Settings (`test_review_ui.py`)
- `fmt.size` shows "1000 KB" [xfail]; `ask_text` OK enabled on empty text [xfail].
- `settings._save` breaks on a `null`/`[]` json file (re-implements `config.update`) [xfail].
- Style "Windows 11 (coming later)" saves mac but keeps showing Windows 11 [xfail].
- `ui.on_change` can't be removed: sliders/windows leak a listener each [xfail]; the Wallpaper page's dark-mode handler leaks per rebuild [xfail].
- App Icons: the typed theme-icon name is lost unless Return is pressed.
- Kit consistency: raw `Gtk.Entry` where `ui.controls.text_field` exists (mountop, colorpicker, appicons_page, settings/app.py ×7); hand-made `sonata-button`s instead of `push_button` (~15); `settings._ask_text` duplicates `ui.dialog.ask_text`; `slider(style="module")` ignores `default`; menus leave their action group on the anchor.

### Calendar / Notes / TextEdit (`test_review_docs.py`)
- See "Fix first" 3–5. Also: a bullet starting with "[ ] " reloads as a checklist [xfail]; re-completing a reminder moves its completion time [xfail]; sync `load_contents` on the main loop (TextEdit, Calendar import).
- Four separate atomic-write helpers (notes, textedit session, calendar, config), only one with fsync → make one `atomic_write`.

### Assistant / Web apps / Terminal / Feedback / Clock / Calculator (`test_review_apps.py`)
- `clock/alarms.py:63` `{}` alarms file crashes the menu bar's AlarmService [xfail].
- Assistant: a lone surrogate in tool content raises and leaves a tmp file [xfail]; markdown emphasis applied inside link hrefs and misnested tags blank the paragraph [xfail ×2]; tool file I/O on the main loop.
- Calculator: paste of "-5"/"1e5" wrong [xfail]; decimal Overflow escapes [xfail].
- Web apps: a smaller favicon replaces a bigger one [xfail]; IPs / shared suffixes treated as one site [xfail]; `\r` and backslashes reach `Name=`.
- Feedback: pre-1980 log mtime kills the worker and leaves Feedbacker busy forever [xfail]; `system_info()` runs twice; long titles can exceed `URL_MAX`.

## Known environment-only test failures (not bugs)
`test_regressions.test_group_titles_with_ampersand_show`, `test_dock.test_app_file_is_real_path` (real bug above, GioUnix), `test_tray`/`test_uninstall` without a session bus, `test_preview`/`test_textedit` segfault under this xvfb.
