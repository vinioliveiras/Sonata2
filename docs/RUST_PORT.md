# Porting Sonata 2 to Rust

Status: **step 1 done** (foundations + Calculator), **step 2 done**:
Clock and TextEdit tried in Vini's session; Preview and Notes build and
their tests pass, not tried in a session yet. Branch
`claude/rust-port-fdcd02`.
The Python code stays the working Sonata on this branch until a
component's Rust replacement is ready; the Wayfire plugin
(`wayfire-plugin/`, C++) stays as it is.

## Why the port goes this way

Sonata is ~90 000 lines of Python in ~30 processes. Each shell component
and app is already its own process (`python3 -m sonata2 <component>`,
see `sonata2/__main__.py`), and they only talk through:

- **config files** in `$XDG_CONFIG_HOME/sonata2/<name>.json`
  (`sonata2/config.py`: missing keys fall back to defaults, unknown keys
  are ignored on load and kept on `update()`, atomic writes, a
  `.<name>.lock` flock per file, a Gio file monitor to follow changes);
- **D-Bus**: each process is a `Gtk.Application` with the id
  `io.github.vinioliveiras.sonata2.<component>` (single instance, actions
  activated from other components), plus the freedesktop services it
  serves or uses (Notifications, FileManager1, the portal backend
  `org.freedesktop.impl.portal.desktop.sonata`, StatusNotifierWatcher,
  MPRIS, logind, NetworkManager, UPower, BlueZ, UDisks2, Accounts...);
- **Wayfire IPC**: `$WAYFIRE_SOCKET`, 4-byte little-endian length + JSON
  (`sonata2/wl/wfipc.py`), methods like `window-rules/list-views`,
  `wm-actions/set-minimized`, `vswitch/*`, `scale/toggle` and the
  plugin's own `sonata/*`;
- **Wayland protocols** (wlr-foreign-toplevel, idle, virtual keyboard)
  and gtk4-layer-shell for the shell's surfaces;
- **files on disk**: generated `.desktop` files, the icon cache,
  `~/.cache/sonata2/*.log`.

So a component can be replaced **whole**: a Rust binary that reads the
same JSON, owns the same D-Bus name and speaks the same IPC is invisible
to the rest of the desktop. Nothing is ported half in Python, half in
Rust inside one process (no PyO3 bridge): that would double the work and
keep both runtimes' costs.

## Side by side with the installed Sonata (Vini's request)

While the port is young, the Rust build installs **next to** the Python
Sonata, never over it, so it can be tried with no risk to the working
desktop:

| | Python Sonata (today) | Rust build (trial) |
|---|---|---|
| Binaries | `~/.local/bin/sonata2` | `~/.local/bin/sonata2-rs` (one multi-call binary: `sonata2-rs calculator`) |
| App ids / D-Bus names | `io.github.vinioliveiras.sonata2.<c>` | `io.github.vinioliveiras.sonata2rs.<c>` |
| Desktop files | `io.github.vinioliveiras.sonata2.<c>.desktop` | `io.github.vinioliveiras.sonata2rs.<c>.desktop`, named "Calculator (Rust)" |
| Settings | `~/.config/sonata2/` | **reads** `~/.config/sonata2/` (your real settings, look and language), **writes** only to `~/.config/sonata2-rs/` |
| Logs | `~/.cache/sonata2/` | `~/.cache/sonata2-rs/` |
| Installer | `./install.sh` | `rust/install-rs.sh` (and `--uninstall`), touches nothing of the Python install |

- Different app ids mean both Calculators can be open at once, and the
  Rust one never steals the Python one's single-instance D-Bus name.
- App data (`~/.local/share/sonata2-data/<app>`) is decided app by app.
  Clock (Rust) edits the same `alarms.json` and `timers.json` as the
  Python Clock (Vini's call), since the Python menu bar is what rings
  them. TextEdit and Notes keep their own (`sonata2-rs-data/<app>`): two
  copies of the same app writing one file would undo each other's work.
  Notes fills its own copy from the Python Notes' data the first time,
  so the trial shows your notes without risking them.
- Settings: on load the Rust code looks in `sonata2-rs/` first, then
  `sonata2/`; on save it only ever writes `sonata2-rs/`. Your Python
  Sonata's files are never written by the trial build. (For a component
  that *serves* something system-wide -- notifications, the portal, the
  Dock -- a trial instance can't run at the same time as the Python one;
  those get a separate session entry, "Sonata (Rust preview)", much later,
  once enough of the shell exists.)
- `SONATA2_RS_SIDE_BY_SIDE=0` (or a build feature) switches to the final
  mode: the real ids and `~/.config/sonata2/` for both reading and
  writing. That is the mode a component switches to when it **replaces**
  its Python version.

## Crate layout

A Cargo workspace in `rust/` (the Python package stays where it is):

```
rust/
  Cargo.toml              workspace; shared versions of gtk4, libadwaita, zbus, serde
  install-rs.sh           side-by-side installer (see above)
  .gitignore              target/
  crates/
    sonata-config/        config.py: load with defaults, update (flock + atomic write),
                          watch (gio::FileMonitor), side-by-side read/write dirs
    sonata-i18n/          i18n.py: detect(), _(), ngettext(), pgettext(), reading the
                          SAME sonata2/locale/<lang>/*.json; tr!() / trn!() macros
    sonata-ui/            ui/: tokens (light/dark), the CSS templates, theme.setup()
                          (appearance.json, accent, glass), window frame, controls
    sonata-backend/       backend/system.py and its helpers: Wi-Fi, sound, brightness
                          (DDC/CI too), battery, displays, keyboards...; wfconfig.py,
                          the Wayfire IPC client (wl/wfipc.py), sounds.py
    sonata-session/       the session's plumbing: logs, keep / keep-all, key, screenshot
    sonata-shell/         shell/'s shared pieces: layer-shell, intro, wallpaper, loginui
    sonata-dbus/          zbus proxies/services: Notifications, logind, NM, UPower...
    sonata-apps/          apps.py: .desktop parsing, write_desktop_file, icons
  apps/
    sonata2-rs/           the multi-call binary: `sonata2-rs <component> [args]`
                          (same CLI as `python3 -m sonata2`)
    calculator/           one library crate per component, linked into sonata2-rs
    ...
```

Dependencies: `gtk4` (0.9+, feature `v4_12`), `libadwaita` (`v1_4`),
`gtk4-layer-shell` (the `gtk4-layer-shell` crate), `zbus` (blocking +
glib executor via `glib::MainContext::spawn_local`), `serde` /
`serde_json`, `num-bigint` (the Calculator's exact decimals: Python's
`Decimal` rounding, digit for digit), `wayland-client` +
`wayland-protocols-wlr` for toplevels later.

One multi-call binary rather than 30: one build, one install, shared
code linked once; `sonata2-rs <component>` mirrors `sonata2 <component>`.

## The design system

`ui/tokens.py` + `ui/theme.py` hold every colour, radius and CSS
template. Two copies would drift. During the port:

1. A small generator (`tools/export_tokens.py`) writes the token tables
   (LIGHT, DARK, SHARED, FRAME, ACCENTS) to `rust/crates/sonata-ui/tokens.json`;
   `sonata-ui` embeds it with `include_str!`. A test fails when the JSON
   is stale, so tokens.py stays the one place to change a value.
2. The same generator reads the CSS templates (`ui.register("""...""")`)
   of the ported modules out of the Python files (with `ast`) into
   `css.json`; Rust registers them with `sonata_ui::css::register_module
   ("clock/pages.py")`, filled from the same tokens into one
   `gtk::CssProvider` refilled on appearance change. No CSS is copied by
   hand: a style changed in Python reaches Rust at the next export.
3. Once the Python side is gone, `tokens.json` (or a `tokens.rs`) becomes
   the source.

## Translations

The catalogs stay exactly where and how they are:
`sonata2/locale/{pt_BR,pt_PT}/*.json`, `{"English": "translation"}`,
plurals as `[one, other]`, contexts as `"context\x04text"`. `sonata-i18n`
loads them at runtime (installed next to the binary under
`share/sonata2-rs/locale`, or the clone's folder in development) with the
same rules: language from LC_ALL / LC_MESSAGES / LANG / LANGUAGE, plain
`pt` is Brazil, `0` is singular in pt-BR only, a missing string shows in
English.

Rust code marks strings with `tr!("...")`, `trn!(...)`, `trp!(ctx, ...)`.
`tests/test_i18n.py` / `tools/i18n_missing.py` learn to scan `rust/**/*.rs`
for those macros, so "every user-visible string in both catalogs" keeps
being checked by the same tool. Placeholders stay `{name}`, filled with a
small `fmt(&str, &[("name", value)])` helper (not `format!`, whose
arguments must be literal).

## Tests

- **Pure logic** (Calculator engine, config merging, i18n rules, IPC
  framing, .desktop parsing) becomes `#[test]`s in each crate, ported
  case by case from `tests/test_*.py`. The bug-named regression tests
  keep their names (`test_<what_went_wrong>`) and docstrings as `///`.
- **Same isolation as tests/__init__.py**: `cargo test` never touches the
  real session. A shared `sonata-testenv` helper (used by every crate's
  tests) gives each run a temporary HOME / XDG dirs, unsets
  `WAYFIRE_SOCKET` and `DBUS_SESSION_BUS_ADDRESS`; GUI tests run under
  `dbus-run-session -- xvfb-run -a cargo test -p <crate> --features ui-tests`
  and are off by default.
- **Behaviour parity**: while both exist, the Python tests of a component
  stay; a component is "replaced" only when its Rust tests cover what the
  Python ones did. `tests.test_smoke_languages` grows a Rust leg that
  opens each ported component in the three languages.

## Order

Small, self-contained pieces first; each step ends with something that
runs and can be tried side by side.

1. **Foundations + Calculator (the proof).** `sonata-config`,
   `sonata-i18n`, a minimal `sonata-ui` (tokens, theme setup, the window
   frame), the `sonata2-rs` binary, and the Calculator: a pure engine
   (`calculator/engine.py`, 200 lines, with its tests) and a small window.
   Installed side by side as "Calculator (Rust)".
2. **Small apps:** Clock, TextEdit, Preview, Notes. They grow `sonata-ui`
   (menus, dialogs, controls) one component at a time.
3. **Background pieces without UI:** `autostart`, the `keep` / `keep-all`
   supervisor (a big memory win: ~9 MB Python processes become ~1 MB),
   `sonata2 key`, `screenshot`, `doctor`.
4. **Simple shell surfaces:** wallpaper, welcome, lock, spotlight
   (layer-shell, `sonata-wfipc`). From here a "Sonata (Rust preview)"
   session entry starts the Rust pieces and the Python ones for the rest.
5. **Shell services:** notifications (`org.freedesktop.Notifications`),
   the portal backend, polkit agent, tray (StatusNotifierWatcher).
6. **Big apps:** Files, Calendar, Activity, Disk Manager, Videos, Music,
   Terminal, Assistant.
7. **The core shell, last:** Launchpad, top bar + Control Center, Dock
   (2 900 lines, the most behaviour), Settings (4 000+ lines, every
   backend), greeter.
8. Drop the Python package; `install.sh` installs the Rust build; the
   side-by-side mode is retired.

A component switches from trial to replacement when: its Rust tests pass,
it has been used side by side, the smoke test passes in the three
languages, and Vini says so. Then `__main__.py` (or the session's
`keep-all` line) starts the Rust binary for it, with the real app id.

## Risks

- **One binary, every library.** A library linked into `sonata2-rs`
  must be there for any part of it to start -- the lock screen too.
  libpam and polkit's libraries are opened at run time for that reason,
  and Terminal (libvte) is a binary of its own; TextEdit's GtkSourceView
  is still linked in (move it out the same way before the Rust session).
- **Size.** 90 000 lines is many months. Mitigation: every step ships on
  its own; the port can stop at any step and Sonata still works.
- **Two design systems drifting.** Mitigation: tokens generated from
  `tokens.py`, one CSS source per component, the gallery ported early.
- **Behaviour parity.** Much of the behaviour lives in details
  (animations, drags, timings) that tests don't cover. Mitigation: a
  component is replaced only after side-by-side use.
- **Bindings gaps.** gtk4-layer-shell, pixdecor / plugin specifics,
  GtkSourceView 5 and VTE 4 have younger Rust bindings; Wayland toplevel
  handling must be written against `wayland-client`. Check each before
  its step.
- **Config compatibility.** Rust must accept everything Python wrote
  (wrong types, extra keys, missing files) like `config.load()` does:
  `serde_json::Value` merged over defaults, never strict structs that
  reject a file. Ported with tests built from real config files.
- **libadwaita 1.5.** The Rust alerts use AlertDialog (1.5); the Python
  code falls back to MessageDialog on 1.4. Distributions with 1.4 only
  (none that Sonata supports today, docs/GUIDE.md) would need that fallback.
- **Build tooling.** Rust is not installed on Vini's machine yet
  (`pacman -S rust` or rustup). `install.sh` will need cargo, and
  distributions with an old rustc may lag behind the gtk4-rs MSRV.
- **Safety on the real session.** Tests must be isolated as strictly as
  the Python ones (see Tests); nothing writes `~/.config/sonata2` in
  trial mode.

## Working on it

```
cd rust
cargo build                                   # everything
cargo test -p sonata-calculator --no-default-features   # the engine alone, no GTK
cargo test                                    # every crate's own tests (none opens a window)
python3 tools/export_tokens.py                # after changing sonata2/ui/tokens.py
rust/install-rs.sh                            # try it next to Sonata (--uninstall removes it)
sonata2-rs calculator --dark                  # a component, as `sonata2 calculator`
```

Tests that read or write settings set their own temporary
`XDG_CONFIG_HOME` (one at a time: they share the process environment);
none talks to Wayfire or the session bus. GUI tests, when they come,
stay behind `--features ui-tests` and run only under
`dbus-run-session -- xvfb-run -a`.

### Step 1, what is in it

- `sonata-config`: load / save / update / atomic writes / the same
  `.<name>.lock` flock / watch, the side-by-side folders, and the exact
  text `json.dumps(indent=2)` writes.
- `sonata-i18n`: the same catalogs and rules; `tr!` and friends, which
  `tests/test_i18n.py` (and so `tools/i18n_missing.py`) now finds in
  `rust/**/*.rs` too.
- `sonata-ui`: tokens from `tokens.json` (`tools/export_tokens.py`,
  `tests.test_rust_tokens` checks it is fresh), the CSS provider refilled
  on Light/Dark (system.json) and accent (appearance.json) changes, the
  window frame. Not yet: per-part glass, the user's radii and
  backgrounds, the cross-fade, the window buttons.
- `sonata-calculator`: the engine (its Python tests plus ~50 key
  sequences checked against what the Python engine shows) and the window,
  keyboard, copy and paste.
- `sonata2-rs`: `calculator`, `desktop-files`.

### Step 2 so far: Clock

- `sonata-ui` grew what Clock needs: the glass materials per part
  (ui/glass.py), controls (push / pop-up / circle buttons, switch, text
  field, segmented tabs), context menus, the unified glass toolbar, the
  size a window remembers (windows.json). Their CSS is Python's own
  (css.json).
- `sonata-clock`: alarms and timers (the same files, the same JSON text,
  unknown keys kept; tests from tests/test_clock*.py), the sound preview,
  Alarms with the edit popover, Stopwatch with laps, Timers with the ring.
  Not ported: ringing (the menu bar's).

### Step 2 so far: TextEdit

- `sonata-ui` grew alerts (ui/dialog.py, on libadwaita's AlertDialog),
  panels (ui/panel.py), menus with checks after the text and submenus, tab
  bar visibility (always_show_tabs).
- `sonata-textedit`: the file formats (UTF-8 / UTF-16 / Windows and ISO
  Latin 1 with Python's exact codec rules, line endings, .docx / .odt /
  .rtf as read-only text), Find & Replace (Python-style `\1` replacements),
  the session (hot exit, own folder on trial: `sonata2-rs-data/textedit`,
  so the two TextEdits never undo each other's windows), GtkSourceView
  with the Sonata scheme, tabs, status bar menus, Go to Line, drops.
- Differences from Python, on purpose for now: Open / Save are
  GtkFileDialog, which a Sonata session sends through the file chooser
  portal to Sonata's own panels (`GDK_DEBUG=portals`); outside a Sonata
  session they are GTK's. On trial the entry claims no file types (the
  Python TextEdit stays the one that opens text files). Not ported: the
  tabs' glide, the first-size "roomy" window, the plain-TextView fallback
  without GtkSourceView.

### Step 2 so far: Preview

- `sonata-preview`: the picture view (zoom that glides, pinch, pan with
  inertia, cross-fades), thumbnails made off the main loop, the Info panel
  (EXIF through kamadak-exif, the colour profile's own name), slideshow,
  crop, rotate / flip, Adjust Color (Pillow's ImageEnhance formulas),
  Resize, Export As, Save / Save As (EXIF and colour profile kept, written
  next to the file then moved over it), Copy, and all of Markup (pen,
  highlighter, shapes, text, emoji, steps, pixelate; one Cairo + Pango
  renderer for the screen and the saved file). Camera RAW files show
  their built-in JPEG, as in Python.
- `sonata-ui` grew the thin slider (double-click resets) and file sizes.
- Differences on purpose: the pixels are read and written by GdkPixbuf
  (Python used Pillow): a format GdkPixbuf can't write is not offered;
  WebP's colour profile is dropped when its saver takes none. Set Desktop
  Picture writes the shared `~/.config/sonata2/system.json` even on trial
  (it is the desktop's wallpaper: the user asked for it), then
  `sonata2 restart wallpaper`.

### Step 2 so far: Notes

- `sonata-notes`: the note format (markup.rs, the same Markdown-ish text,
  round-trips byte for byte), the data (unknown keys kept, damaged files
  set aside, Recently Deleted for 30 days), the editor (paragraph and
  character styles, lists renumbered, checklists, Return / Backspace
  rules), the notes list in date sections, folders and lists with
  rename / delete, search, Reminders (smart lists, rows with the strike
  animation, the ⓘ panel with date, time, flag, priority, list) and the
  reminder alerts (org.freedesktop.Notifications, the shared ledger so an
  alert goes out once).
- `sonata-ui` grew panel rows and separators, and the date sections and
  short dates of lists (ui/fmt.py).
- The CSS exporter also takes `_colors + """..."""` registrations (the
  literal part; Rust makes the colour classes itself).

### Step 3 so far: the supervisor, keys and screenshots

- `sonata-session`: logs (off by default, in memory; ~/.cache with Logs on;
  kept under their cap) and `keep` / `keep-all` with the Python rules
  (stopped on purpose stays stopped, 5 crashes in a minute gives up, the
  memory limit, the compositor gone ends it). A component the Rust build
  has runs as `sonata2-rs <name>`, any other through the Python `sonata2`:
  this is what a mixed "Sonata (Rust preview)" session will start.
  Each child's pidfd wakes the supervisor when it ends (no signals, no
  polling).
- `sonata-backend`: backend/system.py (Wi-Fi and network services over
  nmcli and NetworkManager's D-Bus, printers, Bluetooth through
  bluetoothctl, Airplane Mode, sound, brightness with DDC/CI for external
  monitors, battery, power, About, displays, power profiles, keyboards,
  date & time, computer name, default browser), wfconfig.py, the Wayfire
  IPC client, sounds.py and the "remember" half of devicevolume.py. The
  parsers carry the Python tests' cases.
- `sonata2-rs key <name>` (the volume, brightness and media keys; the HUD
  is still the Python menu bar's, asked over D-Bus) and `sonata2-rs
  screenshot [screen|area|edit]` (the menu bar's capture, else grim and
  a notification). Wayfire's bindings still call the Python ones: the
  "Sonata (Rust preview)" session switches them over.
- Not ported yet: BlueZ over D-Bus (bluez.py) and watching NetworkManager
  and the sound server.
- Left for the "Sonata (Rust preview)" session (step 4), on purpose:
  `restart` (it stops and starts the Python shell by its command lines,
  with intro.py's slide-away signals), `doctor` (it checks what the
  *Python* Sonata needs: PyGObject, typelibs) and autostart (title bars,
  compose, keyring, Flatpak theme, app lock and scopes: all Python
  modules still). Each changes shape once the session runs Rust pieces;
  ported now they would be rewritten then.

### Step 4 so far: the welcome screen and the lock screen

- `sonata-shell`: what the shell's surfaces share -- gtk4-layer-shell
  (0.5, the gtk4 0.9 one; the process starts itself again with the
  library preloaded, as layer.py does), intro.py's markers, the
  wallpaper of the moment (system.json, else Sonata's "Mountains"; a
  video's poster made with ffmpeg and named like the Python one, so
  both share the cache), the login look (loginui.py: the blurred
  backdrop -- blurred on the CPU with the `image` crate --, avatar,
  password field, shake, clock, power buttons with logind and the
  systemctl fallback) and the accounts and sessions the login screen
  lists.
- `sonata-ui::progress`: the macOS spinner and the progress bar.
- `sonata2-rs welcome [--preview]`: welcome.py. It waits for the Python
  shell's D-Bus names (those are the parts that run), then finishes the
  intro and fades.
- Not ported yet in the welcome screen: the video under the backdrop
  (videowallpaper.py).
- `sonata2-rs lock`: lock.py on ext-session-lock (gtk4-session-lock 0.1,
  the gtk4 0.9 one). PAM through libpam opened at run time, as pam.py
  does (no libpam or no session lock: it refuses to lock), the password
  wiped from memory after the check; the waits after wrong passwords
  (throttle.py, the same file, so both lock screens count together); the
  field mirrored on every display, displays that come and go; logind's
  Unlock signal (Switch User coming back); USB devices blocked while
  locked (usbprotect.py) and locked folders closed (vault.py); the screen
  dark after its 30 s (lockdisplay.py, with idlelock.py's backlight
  commands -- compared with the Python ones in a test).
- Not ported yet in the lock: Switch User (userswitch.py), the video
  wallpaper under the blur, the screen corners, and the compositor's idle
  watch (ext-idle-notify): the screen goes dark on the lock's own timer,
  as lock.py does without it.
- Found on the way: serde_json's fast float parser read some numbers one
  bit off (a Notes timestamp changed on save); `float_roundtrip` is on now,
  with a test.

### Step 4 so far: Spotlight, and Sonata's icons

- `sonata-ui::icons`: icons.py -- Sonata's icon themes first, app icons
  under every name they may have, the user's App Icons choices, and the
  plate for apps without Sonata artwork (a tile's own colour, a logo keyed
  out of its tile, discs and squares filling the frame). The plated icons
  are cached under the Python names and stamps (~/.cache/sonata2/app-icons),
  so either build reuses the other's. The plate's outline is a cairo mask:
  icons.py fills a GSK path, which needs GTK 4.14. The picture analysis is
  checked against icons.py's own on the same pixels.
- `sonata_ui::setup()` now sets Sonata's icon theme, as ui.setup does in
  every Python app: the Rust apps had been using the system's icons.
- `sonata-ui::fixed`: FixedWidth (ui/fixed.py). CSS templates fill
  `%(name)d` too, and the exporter resolves a module's own constants
  (`open_ms=OPEN_MS`).
- `sonata2-rs spotlight [--background] [--search TEXT]`: spotlight.py --
  the calculator (its own small parser, Python's rules; checked against
  calculate() on the same sums), the home folder's index, Launchpad's app
  ranking, the preview, the layout on short displays.
- Not ported yet in Spotlight: opening a package, AppImage, archive or
  script (files/packages.py: they open with their default app), and the
  hidden warm-up draw (layer.prewarm). Folders open in the Python Files.
- install-rs.sh links the icon themes (125 MB) instead of copying them.

### Step 5 so far: the password prompt for system actions

- `sonata-shell::authsheet`: the password sheet (authsheet.py), and
  ask() -- the login password checked with PAM -- for the prompts that
  will need it.
- `sonata-shell::polkitagent`: PolkitAgent.Session through
  libpolkit-agent-1, opened at run time (as pam.rs opens libpam). The
  password is passed to polkit's own helper, which checks it, and wiped
  afterwards.
- `sonata2-rs polkit`: polkit.py. polkitd's calls only (any other sender
  is refused); identities without an id are skipped, never taken as root;
  several accounts: a pop-up; Cancel and polkitd's CancelAuthentication
  close it. Registered for the login session (logind's, else
  $XDG_SESSION_ID); with another agent there it exits quietly.
- The supervisor now runs welcome, lock, spotlight and polkit as Rust
  (`sonata2-rs keep <name>`); nothing starts them yet.
- Not portable yet: the portal (its Open/Save panels are the Files
  window), notifications and the tray (inside the menu bar, step 7).

### Step 6 so far: Terminal

- `sonata2-rs-terminal`, a binary of its own (`sonata2-rs terminal`
  starts it): only it links libvte (vte4 0.8, the gtk4 0.9 one), so the
  shell's other parts start on a system without VTE. terminal/window.py:
  tabs with their own shells, the strip (middle-click, drag to reorder),
  the Basic profile in Light / Dark, the shortcuts, the context menu,
  "terminate running processes?" for a tab and a window, `--exec` for
  Sonata's commands, folders opening as tabs of the front window.
- Its trial entry "Terminal (Rust)" isn't a TerminalEmulator: the Python
  Terminal stays the one the system picks.

### Step 6 so far: Feedbacker

- `sonata2-rs feedback`: feedback/ -- the report .zip (description,
  system facts, the Python `sonata2 doctor`, the session's logs, and on
  trial the Rust parts' own under logs/rust/), the GitHub link (Python's
  urlencode, cut to 6000 characters), the crash note with its kind from
  the logs and the week's history, Monitoring with its "turn it on"
  question.
- Monitoring is the session's one switch (Settings > About > Logs): the
  Python Sonata's flag file, written on trial too -- like the wallpaper,
  it is the user asking for it.
- Not ported yet: "Reopen your apps" after a crash (open_apps.py).

### Step 6 so far: Disk Manager

- `sonata2-rs diskutil`: diskutil/ -- the model (UDisks2's tree into
  disks and volumes; the properties kept as GLib variants, read the way
  Python reads them) with test_diskutil.py's fake tree and cases, plus
  the system-storage members (btrfs RAID, LVM, md); the UDisks client
  (async Gio, interactive authorization, debounced live reloads); the
  window -- sidebar, capacity bar and legend, info table, First Aid,
  Erase, Rename, Mount / Unmount / Unlock, Eject, Info, the context menu
  and the keys.
- Erase keeps every guard: the running system's volumes refused, two
  confirmations, and a fresh tree checked right before (a volume mounted
  as a system one meanwhile is refused then).
- sonata-ui gains `dialog::ask_text`; the CSS exporter reads
  `"""...""" + computed` registrations too.

### Step 6 so far: Camera

- `sonata2-rs-camera`, a binary of its own like Terminal's
  (`sonata2-rs camera` starts it): only it links GStreamer (gstreamer-rs
  0.23, the glib 0.20 one). camera/: the engine (device monitor, V4L2
  first; decodebin, mirror, tee; GTK 4 paintable sink, else an appsink
  whose frames reach the window through the main context; the photo from
  the last sample; recording with the GPU's H.264 encoder first, AAC from
  the microphone, or VP8/Opus; an encoder setting this version can't read
  is left at its default, never a panic) and the window (modes, timer,
  screen flash, the last capture with Open / Show in Files / Move to
  Trash, options, keys).
- `sonata-backend::userdirs`: userdirs.py (Pictures and Videos found by
  their usual names when user-dirs.dirs has none).
- Not tested with a real camera yet: no GUI or device test runs on
  Vini's machine. A cloud session could run them once the branch is
  pushed.

### Step 6 so far: Videos

- `sonata2-rs-videos`, a binary of its own (GStreamer). videos/: the
  window (the movie's shape, the fading glass controls, positions
  remembered, volume saved once the slider rests, songs square with
  their cover, full screen, the menu, the keys), Trim (the filmstrip with
  ffmpeg's frames) and Crop (the frame, the shapes), the ffmpeg exports
  (a failed or cancelled one leaves nothing beside the original), and
  songs through classic playbin as a gtk::MediaStream.
- A song's tags come from GStreamer's discoverer, not audiotags.py's own
  parsers; the title falls back to the file name the same way
  (audiotags.from_filename, checked on the same names).
- The Open panel is GTK's file dialog (the portal's): Files' own
  chooser isn't ported yet. Its trial entry takes no file types.

### Step 6 so far: Assistant

- `sonata2-rs-assistant`, a binary of its own: only it links libsoup 3
  (soup3-rs) and libsecret (libsecret-rs), both already on the system --
  no TLS stack is bundled. Python's urllib in a worker thread becomes
  libsoup reading the stream on the main loop; Stop drops the request
  (no callback runs after it).
- assistant/: the store and usage files (the same formats; on trial in
  `~/.local/share/sonata2-rs-data/assistant`), the API (models and their
  context windows, the stream put together event by event, errors made
  readable, tested against a local HTTP server), the Markdown blocks and
  inline markup (fancy-regex: the same lookaround patterns), the keyring
  (the same entry as the Python Assistant: a key saved in one is the
  other's), the file tools (paths resolved before the folder check,
  tested with links and `..` that lead out), and the window (sidebar by
  date with Rename / Delete, streaming answers, the meters, the settings
  panel, the file actions' alerts, the keys).
- Add Folder uses GTK's folder dialog (the portal's): Files' own chooser
  isn't ported yet.

### Step 6 so far: Calendar

- `sonata-calendar`, in the main binary (GTK only). calendar/: ics.rs
  (the RFC 5545 subset, foreign lines kept verbatim, recurrence with
  BYDAY, COUNT, UNTIL, EXDATE and changed instances; zones through
  GLib's own time zone database, so a series repeats in its zone across
  DST changes), model.rs (columns, lanes, month grids, the store with
  its ordered writer thread), alerts.rs (notifications over D-Bus),
  views.rs (Month, Week, Day, Year and the mini month, painted with
  cairo, drag to move or resize) and window.rs (sidebar, search, undo,
  the event popover, import by drop or argument, the keys).
- chrono's naive date-times stand in for Python's naive datetimes;
  weekday and month names come from GLib (the session's language).
- The palette CSS is computed in Rust (`palette_css`), as the exporter
  leaves computed parts to the Rust side.
- On trial it reads a copy of the Python Calendar's files
  (`sonata2-rs-data/calendar`, made once) and its entry takes no file
  types.
