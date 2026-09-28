# Devlog

## 2026-09-28 -- M0: project start

- Why a new project: Sonata (Quickshell on KWin, KWin script + Python D-Bus
  bridge for windows) gave poor design results; the GTK4/libadwaita UI built
  for LayerOSX is the look to keep.
- Bases checked:
  - Ignis rejected: its Python line stopped at v0.6.0 ("final release ...
    before the Rust rewrite"), API unstable, no compositor-agnostic window list.
  - Wayfire 0.11 (Jul 2026, Arch extra): animations, SSD decorations, IPC,
    layer-shell, foreign-toplevel. labwc kept as fallback (refuses
    animations, no IPC). Hyprland rejected (tiling-first, no native title bars).
  - gtk4-layer-shell 1.3.0 and python-pywayland 0.4.19 are in Arch extra.
- `sonata2/style.py` ported from LayerOSX `panel/layerosx_style.py`; the
  picom kick-window (X11 workaround) was dropped.

## 2026-09-28 -- M1: Dock first cut

- Order changed with Vini: Dock first, then the top bar. LayerOSX is the base
  (windows, style, Settings); Sonata only serves as a feature list.
- Look: Big Sur/Monterey, not Liquid Glass. 48 px icons, 18 px plate radius,
  5 px top padding, 9 px dot row (4 px dot), plate 4 px above the edge,
  13 px regular name label 8 px above the icon. No compositor blur yet, so
  the plate is more opaque than on macOS (0.78 light / 0.74 dark).
- Layer-shell: gtk4-layer-shell must load before libwayland-client, so
  `layer.ensure_preload()` re-execs once with LD_PRELOAD; without layer-shell
  the Dock is a plain window.
- Default pins: first installed .desktop per slot (Files, default browser,
  mail, calendar, music, images, editor, Steam, terminal) -> no broken icons
  on any distro.
- Screenshots: Xvfb has no alpha for popovers (black box around the label),
  so `tools/wl-preview.sh` runs the preview in headless sway + grim.
  The cloud test box's GTK 4.14 also ignored settings.ini's icon theme on
  Wayland; SONATA2_ICON_THEME sets it for previews.
- Not tested yet on hardware / real layer-shell (the test box has no
  gtk4-layer-shell).

## 2026-09-28 -- M1.2: glass + running apps

- Vini's rule from now on: finish one component completely before the next.
  ROADMAP lists the Dock sub-steps M1.1-M1.6.
- Glass: Vini chose the Big Sur frosted glass (not Leopard's 3D shelf, not
  Liquid Glass). Wayfire's blur plugin blurs layer-shell surfaces when
  matched by app_id, which Wayfire sets to the layer-shell namespace
  (`sonata2-dock`). Its blend shader weights the blur by the surface alpha
  (`blur_alpha = clamp(a / alpha_threshold)` ^ `alpha_exponent`), so the
  transparent corners stay sharp; threshold 0.3 + exponent 2 blur the plate
  fully and the soft shadow barely. Tints: light rgba(246,246,250,.38),
  dark rgba(30,30,34,.42), saturation 1.6.
- The preview can't blur (no compositor effect), so it paints a
  Pillow-blurred copy of a generated wallpaper under the plate with the same
  tint -- same recipe, for screenshots only.
- Window tracking: wlr-foreign-toplevel via pywayland, on GTK's own
  wl_display (pointer via ctypes; GdkWayland's getters are skipped in GIR),
  so GTK's loop dispatches the events -- no second connection or thread.
  pywayland has no wlr protocols built in: bindings are generated from the
  bundled XML on first run into ~/.cache/sonata2/pywayland-<ver>/.
  `set_rectangle` gives the compositor each tile's rect, so Wayfire's
  squeezimize minimizes into the icon.
- app_id -> .desktop: exact id, StartupWMClass, last reverse-DNS part,
  executable name. Unknown apps get a generic icon.
- Fixes: the LD_PRELOAD re-exec lost `-m sonata2` (now uses sys.orig_argv);
  GLib.set_prgname(APP_ID) so the app_id is right even without a session bus.
- Verified in headless sway: layer-shell surface anchored with a 66 px
  exclusive zone (maximized windows stop above it), dots for test windows.
  Blur itself only verifiable on Wayfire with a GPU (not in the test box).

## 2026-09-28 -- M1.3/M1.4: right-click menus, drag to reorder

- Vini asked for drag-to-reorder and an "Open File Location" entry
  (Windows-style) in the right-click menu; it lives under Options, where
  macOS has "Show in Finder". It resolves the real executable (realpath of
  the Exec binary, so AppImages/scripts land in their folder) and selects it
  via the org.freedesktop.FileManager1 D-Bus API; Flatpak/Snap fall back to
  the .desktop file.
- Menus: Gtk.PopoverMenu from a Gio.Menu, NESTED flag for a real Options
  submenu (not GTK's sliding one); Big Sur metrics: 7 px radius, 5 px
  padding, 22 px rows, 13 px text, accent-blue hover (#0a64e1 / #0a84ff).
- Open at Login writes an XDG autostart entry; Wayfire doesn't run XDG
  autostart by itself -- the session (M3) must (e.g. dex).
- Drag: Gtk.DragSource per tile (MOVE, key as string) + one DropTarget on
  the plate; the dragged tile stays as a transparent gap and moves live on
  every motion event (slot = icons whose centre is left of the pointer).
  Drop saves the order; cancel with NO_TARGET after leaving the plate =
  remove; any other cancel restores the original slot.
- tests/test_dock.py (6 tests, virtual display): reorder + save, Esc
  restores, drag-out removes, Keep in Dock, menus build, app_file.
- tools/dev-session.sh: Wayfire nested in the current desktop with the
  repo config, wallpaper (swaybg) and the Dock -- how Vini tests from GNOME.

## 2026-09-28 -- Dock: round Trash icon

- Vini wanted the older round trash can instead of MacTahoe's squircle-ish
  one. Taken from WhiteSur-icon-theme (GPL-3.0, same author as MacTahoe):
  `user-trash(-full).svg`, bundled in sonata2/data/icons and loaded as
  Gio.FileIcon, so it works with any icon theme. `trash_icon: "theme"`
  switches back.

## 2026-09-28 -- Sonata's own icons; Sonata vs. Linux settings

- Vini: Sonata's icons must work apart from the Linux icon theme, bundled
  in the repo, and later also used by apps inside the Sonata session.
  MacTahoe (commit 839848b, blue default) installed as `Sonata-MacTahoe`
  into sonata2/data/icons (relative symlinks; ~60 MB); the round Trash
  moved into a small `Sonata` override theme (Inherits=Sonata-MacTahoe,
  hicolor), so it's the standard `user-trash` name again (the
  `trash_icon` option is gone).
- icons.setup(): adds data/icons to the display's icon search path and sets
  gtk-icon-theme-name *inside the shell process only*; icons.set_image()
  falls back to a Gtk.IconTheme with the system theme for apps neither
  bundled theme knows.
- Side effect: previews no longer need SONATA2_ICON_THEME.
- Principle set by Vini: Sonata UI/behaviour settings live only in
  ~/.config/sonata2 (future: 100% customizable); Linux settings (Wi-Fi,
  wallpaper, sound...) go to the system services and aren't stored by
  Sonata. Documented in the README.
- Not bundled yet: MacTahoe's -dark/-light variants (GTK recolors symbolic
  icons itself; revisit with the top bar).

## 2026-09-28 -- Fix: Options submenu used the default theme

- Reported by Vini (screenshot): the nested Options submenu kept
  Adwaita's look. GTK creates nested submenus as separate popovers that
  neither get our `dock-menu` class nor sit under the window's `.dark`.
  Menu CSS now targets every `popover.menu` of the shell process, and
  light/dark is applied by reloading that provider on
  Adw.StyleManager `notify::dark`.
- `--menu N --submenu` opens the Options submenu for screenshots.

## 2026-09-28 -- Dock: running dot optically centred

- Vini: macOS places the dot too low; centre it between the icon and the
  plate edge. Measured in screenshots: Sonata-MacTahoe icons end 4 px above
  their 48 px box (1/12), so the gap above the dot is 4 px minus that inset
  and the gap below is 4 px -> the visible gaps are equal. Plate is now
  61 px at 48 px icons (exclusive zone follows). Scales with icon_size.

## 2026-09-28 -- Design system (sonata2/ui)

- Vini: one organized pattern for every component (right-click menus,
  dropdowns, everything), with full macOS behaviour per component.
- sonata2/ui: tokens.py (semantic colours per appearance, materials,
  shadows, type, radii, sizes, motion, spacing), theme.py (one CSS provider
  filled from the tokens, refilled on light/dark -- no `.dark` selectors
  anywhere), components: menu (sections, checks, disabled, side submenus),
  label (hover label), controls (push/default/destructive button, Big Sur
  pop-up button with accent chevron cap -- own `sonata-updown-symbolic`
  icon, switch), dialog (alert), window (traffic lights, from LayerOSX
  style.py, which is gone), gallery (`python3 -m sonata2 gallery`).
- Dock + its menus now use ui.* only; icons.setup() moved into ui.setup().
- docs/DESIGN.md: rules, token table, components, how to add one.
- ROADMAP: Dock sub-steps expanded to full macOS parity; Launchpad (M2)
  comes before the top bar, at Vini's request.

## 2026-09-28 -- M1.4 done: file drops

- dock_drop.py: Gtk.DropTarget(Gdk.FileList) with preload, so acceptance
  is decided while hovering (macOS darkens the icon only when the app can
  open the files): every file's content type must be_a one of the app's
  MimeType entries. Drop -> AppInfo.launch(files) (+ launch bounce if not
  running). Trash tile: Gio.File.trash() each file. .desktop files dropped
  on a tile or the plate are pinned at that slot (copied to
  ~/.local/share/applications if they live elsewhere).
- Dock.pin_at() and Dock._slot_at() shared with drag-reorder.
- tests: pin at position, MIME check (8 tests).

## 2026-09-28 -- M1.5a: magnification, divider resize

- The plate is now painted in Dock.do_snapshot at a fixed height at the
  bottom of the allocation (outset shadow, glass/solid tint, inner
  highlight, 0.5 px hairline -- all from tokens), so magnified icons grow
  above it like macOS. No CSS padding on the Dock (it offsets the snapshot
  origin); side spacers + min height instead.
- DockIcon: custom widget drawing one paintable at any size (exact size at
  rest, one large paintable while magnified) -- no SVG re-render per frame.
- Magnification: cos^2 falloff over 3 icons, distances on the unmagnified
  layout (no feedback), Adw.TimedAnimation eases strength in (120 ms) /
  out (250 ms). Off by default (macOS), 80 px when on.
- The layer surface now spans the bottom edge (anchored left+right) with a
  fixed height for the biggest possible Dock; the input region is the
  Dock's bounds, so clicks elsewhere go through. Exclusive zone = plate.
- Divider: GestureDrag resizes (16-128 px, saved on release), right-click
  -> Turn Magnification On/Off.
- PyGObject gotcha: `Gsk.RoundedRect().init_from_rect(...)` returns a view
  of a freed temporary (bounds read as 0) -- keep the object (_rounded()).
  Also: a blurred shadow painted before allocation (0 px) aborts GSK.
- Themes: tokens.THEMES = {"mac": ...}; `theme` in appearance.json. The
  windows theme will only add token values (Vini's plan).
- tests: resize, magnification wave (10 tests).

## 2026-09-28 -- M1.5b/M1.6: position, auto-hide, recents, stacks

- Vini left to test everything later; asked to finish Dock, Launchpad and
  top bar in one go.
- Dock is edge-aware (edge-left/bottom/right classes): orientation, tile
  alignment, dot beside the icon towards the edge, labels/menus on the
  "away" side, plate painted against the edge, magnification and drag slots
  along the Dock's axis, divider resizes along the perpendicular axis,
  sideways launch bounce. Position change rebuilds the Dock
  (DockWindow.rebuild; Dock.detach() drops the manager listener).
- Auto-hide: exclusive zone 0; the Dock's snapshot is translated towards the
  edge (Adw.TimedAnimation, 250 ms); while hidden the input region is a
  2 px strip at the edge along the Dock; reveal after autohide_delay_ms
  (300); hide 200 ms after leaving unless a menu/stack panel is open
  (ui.menu.OPEN / on_closed).
- Recents: last 3 unpinned apps that ran, kept after they quit, in a section
  between two bars (recent_sep only when non-empty). Layout order is
  rebuilt from config by _relayout() after every change.
- Stacks (dock_stack.py): Downloads by default (XDG dir or ~/Downloads);
  icon = newest item (Display as Stack) or folder icon; panel = popover with
  FlowBox grid (5 columns, thumbnails when the thumbnailer made them) or
  list; items are drag sources (Gdk.FileList); sort by name/added
  (created/changed incl. usec)/modified/kind; monitored for changes.
- `--set key=value` overrides Dock settings for previews; PREVIEW_SIZE
  sizes the preview window.
- tests: 13.

## 2026-09-28 -- Fix: Dock surface didn't span the edge

- Headless sway run of the real layer surface: the window kept its natural
  width (615 px) at x=0 instead of stretching along the edge, so the Dock
  sat at the left. A non-resizable GtkWindow makes gtk4-layer-shell send
  its fixed size; `resizable=True` lets the anchored edges size it (960 px,
  Dock centred). Auto-hide started hidden without errors (left edge).

## 2026-09-28 -- M2: Launchpad

- Order changed by Vini: Launchpad before the top bar.
- launchpad_model.py (pure, 7 tests): pages of ids/folder dicts, reconcile
  with installed apps (new ones alphabetically at the end, missing dropped,
  1-app folders dissolved), overflow cascades to the next page (macOS),
  folders named from freedesktop categories, hidden list, ranked search.
- launchpad.py: layer OVERLAY surface, all edges, exclusive keyboard;
  ZoomBin paints scrim (+ preview backdrop) and draws the content with the
  Big Sur zoom (112 % -> 100 %) + fade; Adw.Carousel pages of homogeneous
  7x5 grids (placeholders keep the grid when a page isn't full) and
  Adw.CarouselIndicatorDots; icon size from the screen (~56 % of a cell,
  48-128 px); widgets cached per item and re-attached on changes.
- Drag: one DragSource per item offering a union of a string (internal) and
  a Gdk.FileList with the .desktop file (the Dock pins it); per-grid
  DropTarget: centre of a cell held 400 ms = folder target, otherwise live
  reorder; 650 ms at the side = page flip; dragging an app from the open
  folder closes it and takes the app out.
- Jiggle: CSS rotate +-1.6 deg, odd items phase-shifted; delete badge only
  for .desktop files in ~/.local/share/applications (macOS only deletes
  user-installed apps), moved to the Trash after an alert.
- Launchpad is single-instance (GApplication): running it again toggles;
  `--background` at login keeps it resident for instant opening. The Dock
  writes ~/.local/share/applications/sonata2-launchpad.desktop (NoDisplay)
  with an original icon (Sonata theme: app grid on a tile) and pins it once.
- layer_shell() no longer calls into gtk4-layer-shell on X11 (assertions).

## 2026-09-28 -- M3b: menu bar

- topbar.py: layer TOP surface (top + left + right, exclusive 24 px,
  keyboard on demand), painted with `bar_bg` (blurred by Wayfire through the
  `sonata2-` namespace rule) and a 0.5 px bottom separator. Active app from
  wlr-foreign-toplevel `activated`; the bar offers what that protocol
  allows (minimize, maximize, activate, close). No global menus: GTK4/Qt6
  apps export none on Wayland.
- ui/panel.py (design system): menu-material popovers for custom content
  (header, section title, rows, Control Center modules, round toggles,
  styled calendar) + align_to_start() so bar menus hang from the title's
  left edge. ui/controls.slider: menu and Control Center styles.
- backend/system.py: LayerOSX nmcli/battery/brightnessctl/About code
  without the VM parts, PipeWire volume via wpctl, bluetoothctl, power
  actions via systemctl/loginctl; run_async() keeps the UI responsive.
- Dark Mode in Control Center writes the freedesktop colour-scheme
  (gsettings) -- a Linux setting every app follows.
- Original symbolic icons: Sonata logo (beamed notes), Control Center.
- tests/test_topbar.py: all menus open; nmcli split, battery, DMI name.
- Vini asked next: default window layout, then a Settings app like macOS.

## 2026-09-28 -- M3c: window look

- Wayfire's own decorations: round buttons that turn red/yellow/green on
  hover (deco-theme.cpp) but always on the right (deco-layout.cpp), no
  rounded corners/shadows; pixdecor/vecdecor have no per-button colours.
  So apps draw their title bars (preferred_decoration_mode = client) and
  GTK apps get a Big Sur theme; Wayfire's decoration only colours the rest.
- WhiteSur-gtk-theme (vinceliuice, MIT, Big Sur) prebuilt release tarballs
  at d578265 -> sonata2/data/themes/Sonata-{Light,Dark} (gtk-2/3/4 only,
  4.4 MB). GTK_THEME makes GTK4/libadwaita apps load it (they ignore the
  gtk-theme setting).
- Session isolation: tools/session-env.sh (sourced by tools/sonata-session
  and dev-session.sh) prepends sonata2/data to XDG_DATA_DIRS (themes and
  icons without installing), sets XCURSOR_*, and DCONF_PROFILE =
  `user-db:sonata` over `user-db:user`: button-layout
  'close,minimize,maximize:', icon/cursor/gtk theme and font are written to
  ~/.config/dconf/sonata only. Verified in headless sway: macOS button
  layout inside, GNOME's 'appmenu:close' outside.
- GTK_THEME is fixed per process: after switching Dark Mode, apps pick the
  dark theme when reopened.

## 2026-09-28 -- M6: System Settings (first version)

- sonata2/settings/app.py follows the LayerOSX panel structure Vini liked
  (Adw.NavigationSplitView, badge sidebar with a user card, one
  PreferencesPage per section, fixed 920x640, traffic lights with zoom
  greyed out). Sidebar grouped Linux / Sonata / About (his split).
- Badge colours are design tokens now (tokens.SYSTEM_COLORS -> sys_*).
- backend/system.py grew: Bluetooth devices, PipeWire sinks (wpctl status),
  wlr-randr displays/modes/scale, power-profiles-daemon, gsettings helpers.
- Live apply: config.watch(name, cb) (Gio file monitor); the Dock rebuilds
  when appearance/behaviour keys change (its own pin/recents writes don't
  trigger it), the menu bar reloads, Launchpad reloads unless it was its own
  save. Settings writes one key and keeps the rest of the file.
- Wallpaper: `sonata2 wallpaper`, a layer-shell BACKGROUND surface showing
  org.gnome.desktop.background picture-uri(-dark), live; replaces swaybg.
- Labels in our windows set their colour explicitly: the session GTK theme
  otherwise painted them white on the light window background.
- tests/test_settings.py (all sections build; _save keeps other keys).

## 2026-09-28 -- M3: installer and login session

- Vini: login/session first, then our own Files app.
- install.sh: user (~/.local) or --system (/usr/local); checks the real
  requirements by importing them (Gtk 4, Adw 1, Gtk4LayerShell, pywayland,
  cairo + gi-cairo, wayfire) and prints/runs the package command for
  pacman/apt/dnf/zypper (optional extras listed too); copies the package,
  config and session tools to <prefix>/share/sonata2; `sonata2` and
  `sonata-session` launchers; /usr/share/wayland-sessions/sonata.desktop
  (the only sudo step in user mode); sonata-portals.conf (gtk default,
  wlr for screenshots/screencast); ~/.config/sonata2/wayfire.ini replaced
  only if still the installed default (else .new). --uninstall.
- The session uses its own Wayfire config and `sonata2 <component>`
  commands; dev-session.sh rewrites those to this clone and never runs
  dbus-update-activation-environment (would leak into GNOME).
- sonata2/autostart.py: XDG autostart runner (user overrides system,
  Hidden, OnlyShowIn/NotShowIn "Sonata", X-GNOME-Autostart-enabled,
  TryExec) -> "Open at Login" now works. tests/test_autostart.py.
- Desktop entries (Launchpad, Settings) use the installed launcher when
  present (SONATA2_LAUNCHER), else this clone.
- Key bindings like macOS with Super as Cmd.
- Verified: install into a scratch HOME, launcher runs, autostart rules.
  Not verifiable here: a real login (no GPU for Wayfire in the test box).

## 2026-09-29 -- look independence, cursors, Files F1

- Cursors: MacTahoe had none; bundled apple_cursor (macOS, GPL-3.0)
  trimmed to 24/32/48 as Sonata-Cursors (2.3 MB).
- Independent look: first session run seeds every visual gsettings key
  into Sonata's dconf layer (versioned marker), bundled Inter via the
  session's own fontconfig file, Qt platform theme forced to gtk3.
- Fix: the layer-shell re-exec flag leaked to apps started from the Dock;
  a nested dev session started from such a terminal showed the shell as
  plain windows. ensure_preload() now restores the environment.
- Wayfire "Unknown animation type" log lines: harmless (fixed upstream, #3132).
- Files F1 (sonata2/files): folder.py (async batched listing, one splice,
  Finder name order, live monitor with sorted inserts), sidebar.py,
  window.py (Icons view on Gtk.GridView, virtualised). Tokens: content_bg,
  sidebar_bg, sidebar_selected, item_selected_bg, tool_hover, tool_icon.
- Traffic lights: Big Sur geometry (centres 20 px apart, first centre
  20 px from the edge), measured in screenshots for Files and Settings.
- apps.write_desktop_file() replaces three copies; Files entry
  io.github.vinioliveiras.sonata2.files.desktop (inode/directory).
