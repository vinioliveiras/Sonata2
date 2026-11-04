# Porting Sonata 2 to Rust

Status: **step 1 done** (foundations + Calculator), **step 2 under way**:
Clock and TextEdit (tried in Vini's session) and Preview ported; Preview
builds and its tests pass, not tried in a session yet. Branch
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
- App data (`~/.local/share/sonata2-data/<app>`) is the exception, app by
  app, when the Python shell acts on it: Clock (Rust) edits the same
  `alarms.json` and `timers.json` as the Python Clock (Vini's call), since
  the Python menu bar is what rings them.
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
    sonata-wfipc/         wl/wfipc.py: Wayfire IPC client (sync call + glib watch)
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
