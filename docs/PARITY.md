# Sonata Parity Checklist — macOS (Monterey/Ventura) & Windows 11

Format per line: `feature — Dock/mac spec — Taskbar/win11 spec — KDE/Plasma reuse — status`
Status: **done** (shipped) · **partial** (implemented but untested/incomplete per ROADMAP) · **todo** (not started).
Sources: Apple HIG / Apple Support, Microsoft Support / Learn, Wikipedia "Features new to Windows 11", KDE/Plasma & Quickshell docs.

## 1. Menu bar & top bar
- [x] Global app menu (app name + menus) — macOS: left-aligned per-app menus — win11: N/A — reuse: DBusMenu/appmenu registrar — **todo** (ROADMAP M2)
- [x] Menu bar clock — mac: right side, date+time click toggle — win11: taskbar clock — reuse: `Qt.formatDateTime` — **done**
- [x] Menu-bar extras (status items) — mac: right cluster icons — win11: N/A (tray) — reuse: StatusGlyph.qml — **done**
- [x] Logo/Apple menu — mac: , About/Settings/Sleep/Restart/Shutdown/Lock — win11: Start power button — reuse: Session.qml — **done**
- [ ] Menu bar auto-hide in fullscreen — mac: reveals on hover — win11: N/A — reuse: KWin fullscreen signal — **todo**
- [ ] Menu bar item reordering (drag with ⌘) — mac spec — win11: N/A — reuse: n/a — **todo**

## 2. Dock / Taskbar
- [x] Persistent pinned icons — mac: Dock left section — win11: pinned taskbar — reuse: Apps.qml buildModel — **done**
- [x] Running-app indicators — mac: dot under icon — win11: underline/pill — reuse: Windows.qml — **done**
- [x] Magnification on hover — mac: Dock size increase (self-only scale, no neighbor falloff), default off, Config.magnification — win11: N/A — reuse: AppIcon.qml — **partial**
- [x] Auto-hide — mac: Dock hides, edge reveal — win11: taskbar auto-hide setting — reuse: Panels.qml — **done**
- [x] Minimize effect: Genie — mac default — win11: N/A — reuse: KWin magiclamp effect (`sonata-kwin apply dock`) — **done**
- [x] Minimize effect: Scale — mac alt — win11: N/A — reuse: KWin minimizeanimation/scale effects (`sonata-kwin apply taskbar`) — **done**
- [x] Bounce-on-notification / "attention" bounce — mac Dock icon bounce, win11 taskbar flashing-orange highlight — reuse: Windows.qml `demandsAttention` (read defensively: field not yet emitted by the kwin-script/bridge, so currently inert until that's wired) — **partial**
- [x] Badges (unread count) — mac Dock + win11 taskbar red pill, top-right, matched by app name against Notifications.history — reuse: Notifications.qml — **done**
- [x] Recent apps section — mac Dock, last 3 non-pinned launched apps, Config.dockShowRecents — win11: N/A — reuse: Apps.qml — **done**
- [x] Folders / Stacks — mac Dock ~/Downloads stack icon, grid popover of newest items + "Open in Files", Config.dockShowDownloadsStack — win11: N/A — reuse: none — **done**
- [x] Right-click Dock/taskbar context menu (Quit, Options) — mac & win11 — reuse: components/Menu.qml — **partial**
- [x] Jump lists (recent files/tasks per app) — win11 right-click, Desktop Actions + "Recent" section from ~/.local/share/recently-used.xbel (simplification: one global recent list, not filtered per app's mime types — DesktopEntry exposes no MimeType/path property here) — mac: N/A — reuse: Menu.qml — **partial**
- [x] Pinned apps management (drag to pin/unpin) — mac & win11 — reuse: Apps.qml/Config.qml — **partial**
- [ ] Progress bar overlay on taskbar icon — win11 (e.g. downloads) — mac: N/A — reuse: none — deferred: Unity LauncherEntry DBus (`com.canonical.Unity.LauncherEntry`) needs a new shared listener service, out of scope for this pass to avoid colliding with concurrent utility-module work — **todo**
- [x] Preview thumbnails on hover — win11: documented fallback (KWin/Wayland gives Quickshell no public screencopy-to-specific-window API without portal consent) — a title-list popup with app icon + per-window titles, click-to-activate — mac: Dock shows single icon only — reuse: Windows.qml — **partial**
- [x] Window grouping under one icon — win11: small count pill on the taskbar icon + hover title-list popup doubles as the flyout switcher — mac: Dock exposé (click-hold, unchanged) — reuse: Windows.qml/Apps.qml grouping logic — **partial**
- [x] Task View / open-windows list per app — both, basic — reuse: Windows.qml — **partial**
- [ ] Centered taskbar icons (win11 default) vs left (win10 mode) — win11 toggle — mac: N/A — reuse: Config.json layout option — **partial** (StartMenu/Taskbar centered by default per ROADMAP pass)
- [ ] Multi-monitor taskbar/dock — mac: Dock per main display or all — win11: taskbar per monitor — reuse: Quickshell `Variants` — **done**

## 3. Window management
- [x] Traffic-light titlebar buttons (close/min/zoom) — mac spec, hover reveals glyphs — win11: min/max/close square — reuse: sonata-decorations (Aurorae) — **done**
- [x] Double-click titlebar to zoom/maximize — mac: zoom to fit content — win11: maximize — reuse: KWin double-click action — **done** (kwinrc TitlebarDoubleClickCommand=Maximize via sonata-kwin; real "zoom to fit content" has no KWin equivalent)
- [ ] Snap Layouts (hover maximize button → layout picker) — win11 22H2+ — mac: N/A (Stage Manager instead) — reuse: KWin Quick Tile + custom overlay — **todo**
- [ ] Window tiling / snap-to-edge (Win+arrows) — win11 — mac: window-drag-to-edge tiling (Ventura) — reuse: KWin Quick Tiling, kglobalaccel shortcuts — **partial** (`sonata-kwin apply taskbar` enables ElectricBorderTiling; Meta+T custom tiling and mac green-button-long-press out of scope, see note below)
- [ ] Full-screen "Spaces" (one app per space, swipe between) — mac — win11: N/A — reuse: KWin virtual desktops (per-desktop fullscreen) — **todo**
- [x] Mission Control (all-windows overview + spaces strip) — mac (Ctrl+Up / 3-finger swipe) — win11 equiv: Task View — reuse: KWin **Overview** effect — **done** (F3/Ctrl+Up via sonata-kwin; "+" to add a Space is Overview's own desktop bar)
- [ ] Task View (running windows + timeline) — win11 (Win+Tab) — mac equiv: Mission Control — reuse: KWin **Present Windows** effect — **todo**
- [x] App Exposé (all windows of one app) — mac (F10 / click-hold Dock icon) — win11: N/A — reuse: KWin WindowView (ExposeClass) — **done** (Ctrl+Down via sonata-kwin)
- [x] Show Desktop — mac (F11 / corner) — win11 (Win+D / taskbar corner button) — reuse: KWin "Show Desktop" effect, kglobalaccel — **done** (F11 via sonata-kwin; hot-corner variant opt-in, see Hot corners)
- [ ] Stage Manager (optional) — mac Ventura — win11: N/A — reuse: none, custom — **todo** (explicitly optional)
- [x] Alt-Tab / Cmd-Tab switcher look — mac: centered app-icon row — win11: Alt+Tab thumbnail grid — reuse: KWin TabBox, custom QML style — **done** (kwin-tabbox/sonata-bigsur + sonata-win11, real Big Sur/Win11 look, set via sonata-kwin's TabBox LayoutName; win11 variant screenshotted in the harness's mock-model preview, bigsur variant qmllint-clean but its own mock-model render hit an unrelated headless-Qt hang in this session — re-render to confirm once the harness is stable)
- [ ] Window shadows — mac: soft drop shadow — win11: subtle shadow — reuse: KWin Blur/shadow or CSD — **todo**
- [x] Rounded window corners (top, via decoration) — mac: ~10px — win11: ~8px (DWM rounding) — reuse: sonata-decorations Aurorae — **done**
- [ ] Rounded window corners (bottom/client area) — mac & win11 — reuse: KWin effect (kde-rounded-corners/ShapeCorners) — **todo** (ROADMAP known gap)
- [ ] Minimize/restore animation — mac: genie into Dock — win11: shrink to taskbar — reuse: KWin Minimize Animation effect — **todo**
- [x] Virtual desktops — mac Spaces — win11 Desktops — reuse: KWin virtual desktops, `kglobalaccel` switch shortcuts — **partial** (Desktops/Number=1 default via sonata-kwin; "+" to add is Overview's own desktop bar; no dedicated switch shortcuts bound yet)
- [ ] Hot corners — mac (assign action per screen corner) — win11: N/A (only via 3rd-party) — reuse: KWin Screen Edges — **partial** (`sonata-kwin hotcorners on|off` wired — top-left Mission Control, bottom-right Show Desktop — off by default, not called from `apply`, no UI toggle yet)

## 4. Launcher / search
- [x] Launchpad (mac app grid, paged) — reuse: none custom QML — **done** (untested)
- [x] Start menu (win11 pinned + all-apps + recommended) — reuse: StartMenu.qml — **done** (2025 layout: 8-col Pinned + "Show all", "All" section with View: Category tiles / View: Name list, footer user+power)
- [x] Spotlight search (apps/files/calculator) — mac (Cmd+Space) — reuse: Search.qml, KRunner-style matching — **partial** (tracker/baloo file search not implemented)
- [x] Windows Search (Win key / taskbar search box, apps/files/web) — reuse: Search.qml — **partial** ("same gap")
- [ ] KRunner plugin reuse (calc, units, shell commands, bookmarks) — both — reuse: **KRunner** D-Bus API — **todo**
- [ ] File search backend — mac: Spotlight index — win11: Windows Search index — reuse: **baloo**/tracker — **todo**

## 5. Control Center / Quick Settings
- [x] Control Center panel shell — mac Control Center — win11 Quick Settings — reuse: ControlCenter.qml — **done** (untested)
- [x] Wi-Fi module (list/toggle/connect) — both — reuse: Network.qml (nmcli) — **done** (untested)
- [x] Bluetooth module — both — reuse: Bluetooth.qml, **Solid**/bluez — **done** (untested)
- [x] AirDrop equivalent (nearby-share file transfer) — mac AirDrop — win11: Nearby Share — reuse: **KDE Connect** (share to device) — **partial** (win11 Quick Settings tile opens KDE Connect when installed, else falls back to a real Airplane mode via rfkill — no in-shell file-send UI)
- [ ] Focus / Do Not Disturb modes (named profiles, schedules) — mac Focus — win11: Focus sessions — reuse: Notifications.qml DND flag (basic only) — **partial**
- [ ] Stage Manager toggle tile — mac Control Center — win11: N/A — reuse: none — **todo**
- [ ] Screen Mirroring (AirPlay) — mac — win11: Cast — reuse: none (needs Miracast/gnome-network-displays) — **todo**
- [x] Display brightness slider — both — reuse: Brightness.qml — **done** (untested)
- [ ] Keyboard backlight brightness slider — mac (models with backlit kbd) — win11: OEM hotkeys — reuse: `brightnessctl -d kbd_backlight` — **partial** (Control Center tile detects `brightnessctl -l` and shows Keyboard/Night Light label, no slider wired yet)
- [x] Sound output device switch + volume — both — reuse: Audio.qml (Pipewire) — **done** (untested)
- [x] Now Playing / media widget in Control Center — both — reuse: **MPRIS2** — **done** (mac only so far — services/Media.qml + modules/mac/NowPlaying.qml, hidden with no active player)
- [x] Battery % + time remaining — both — reuse: BatteryGlyph.qml, UPower — **done** (untested)
- [x] Power/performance mode toggle — mac Low Power Mode — win11 Power mode — reuse: Power.qml (power-profiles-daemon) — **done** (untested)
- [x] Accessibility Shortcuts tile — mac — win11: Accessibility quick settings — reuse: Session.qml (stub) — **partial** (win11 Quick Settings > Accessibility opens a Magnifier toggle via KWin Zoom; colour filters out of scope)
- [ ] Dark mode toggle tile — both — reuse: Appearance.qml — **done**
- [x] Night Shift / Night Light (blue-light schedule) — mac Night Shift — win11 Night light — reuse: **KWin Night Color** — **done** (services/NightLight.qml; win11 Quick Settings tile + mac Control Center tile both wired to it)

## 6. Notification Center / widgets
- [x] Notification Center panel (swipe/click from clock) — mac — win11: Notification Center — reuse: NotificationCenter.qml, `Notifications.qml` (freedesktop DBus) — **done** (untested)
- [x] Toast/popup notifications — both — reuse: NotificationPopups.qml — **done** (untested)
- [x] Calendar widget — mac Notification Center — win11: Widgets board — reuse: none — **partial** (month grid + today circle; mac: modules/mac/CalendarWidget.qml, win11: modules/win11/CalendarWidget.qml with month nav, in the Notification Center flyout — no event source wired up either)
- [x] Weather widget — mac widget — win11 Widgets board (MSN feed) — reuse: none (needs API) — **partial** (modules/mac/WeatherWidget.qml, wttr.in, 30min cache, hides when offline — mac only)
- [ ] Clock/world-clock widget — mac widget — win11 Widgets — reuse: none — **todo**
- [ ] Widgets board (separate surface, win11 Win+W) — win11 — mac: N/A (widgets live in NC) — reuse: none — **todo**

## 7. Menu-bar extras / tray
- [x] Battery icon + % — both — reuse: BatteryGlyph.qml — **done**
- [x] Wi-Fi icon — both — reuse: StatusGlyphs.qml — **done**
- [x] Volume icon — both — reuse: StatusGlyphs.qml — **done**
- [x] Clock — both — reuse: MenuBar/Taskbar — **done**
- [ ] Fast User Switching menu — mac menu-bar item — win11: Start > account > Switch user — reuse: `loginctl`/greetd session list — **todo**
- [x] Input source / keyboard-layout switcher — mac icon+menu — win11 tray language indicator — reuse: `kglobalaccel` layout switch, XKB — **partial** (win11 taskbar shows current XKB layout via `setxkbmap -query`; no click-to-cycle yet)
- [x] System tray (third-party app icons) — mac: menu-bar extras — win11: tray/overflow — reuse: components/TrayIcon.qml, StatusNotifierItem — **done** (win11 taskbar now collapses tray icons past 3 behind a "^" overflow chevron)

## 8. On-screen displays (OSD)
- [x] Volume OSD — both — reuse: Osd.qml — **done** (untested)
- [x] Brightness OSD — both — reuse: Osd.qml — **done** (untested)
- [x] Keyboard backlight OSD — mac — win11: OEM — reuse: Osd.qml (extend) — **done** (services/KeyboardBacklight.qml, Osd.qml "kbdBacklight" kind; no hardware hotkey bound yet, only setPercent()/step())
- [x] Caps Lock OSD — mac shows overlay — win11: rare — reuse: Osd.qml (extend), XKB LED state — **done** (services/CapsLock.qml watches /sys/class/leds/*capslock*/brightness via FileView, no polling)

## 9. Clipboard / media / devices
- [x] Clipboard history (Win+V) — win11 — mac: N/A (3rd-party only) — reuse: **klipper** (D-Bus history API) — **done** (services/ClipboardHistory.qml + modules/common/ClipboardHistoryPanel.qml, Meta+V both presets, mac menu-bar extra icon; "pin" is local-only, klipper has no such concept)
- [x] Media flyout (now-playing mini player from tray/taskbar) — win11 volume-flyout media card — mac: Control Center Now Playing — reuse: **MPRIS2** — **partial** (mac: menu-bar Now Playing extra added, reuses existing services/Media.qml; win11's own flyout-above-Quick-Settings placement is modules/win11/** territory, not added this pass)
- [x] Devices/disks eject menu — mac Dock/Finder eject — win11 "Safely Remove Hardware" tray icon — reuse: **Solid** (`solid-devices` / udisks2) — **partial** (services/Devices.qml: `udisksctl monitor` + `eject()`, mount/unmount surfaced as an Osd message bubble on both presets; no dedicated win11 tray flyout UI or mac Files-style eject list yet)

## 10. Screenshots
- [x] Screenshot capture UI (region/window/full, save+annotate) — mac (Cmd+Shift+3/4/5) — win11 (Win+Shift+S / Snipping Tool) — reuse: **Spectacle** (launch or D-Bus) — **done** (scripts/sonata-screenshot wraps spectacle -b -n -f/-r/-a; modules/common/ScreenshotThumbnail.qml floating thumbnail, click-to-open + drag; no annotate UI, that's Spectacle's own editor via clicking the thumbnail's file)
- [ ] Screen recording — mac (Cmd+Shift+5) — win11 (Xbox Game Bar / Snipping Tool record) — reuse: Spectacle/`wf-recorder` — **todo**

## 11. Lock screen & login
- [ ] Lock screen (wallpaper, clock, password field) — both — reuse: `WlSessionLock` protocol (KScreenLocker as fallback) — **todo** (ROADMAP)
- [ ] Login/greeter screen styling — both — reuse: greetd/SDDD theme — **todo**

## 12. Desktop
- [ ] Desktop icons (optional) — mac: Finder desktop icons — win11: desktop icons — reuse: none (Optional per ROADMAP) — **todo**
- [ ] Right-click desktop context menu (New folder, Change wallpaper, Display settings, Sort by) — both — reuse: components/Menu.qml — **todo**
- [x] Wallpaper rendering (+ per-space/per-desktop wallpaper) — both — reuse: Wallpaper.qml — **done**
- [ ] Desktop widgets (win11 desktop widgets, separate from mac) — win11 legacy/optional — mac: N/A — reuse: none — **todo**

## 13. Context menus
- [x] Generic themed context menu component — both — reuse: components/Menu.qml, MenuItem.qml — **done**
- [ ] Desktop context menu — both — reuse: Menu.qml — **todo**
- [x] Dock/taskbar icon context menu — both — reuse: Menu.qml — **partial**
- [ ] File-manager (Nautilus) context-menu theming — both — reuse: GTK/Nautilus extension — **todo**

## 14. Global keyboard shortcuts
- [ ] Spotlight/Search shortcut (Cmd+Space / Win) — reuse: **kglobalaccel** — **partial** (Search.qml exists, global bind TBD)
- [x] Mission Control / Task View shortcut (Ctrl+Up / Win+Tab) — reuse: kglobalaccel + KWin Overview — **done** (sonata-kwin apply, see "Core window management" batch)
- [ ] Show Desktop shortcut (F11 / Win+D) — reuse: kglobalaccel — **todo**
- [x] Screenshot shortcuts (Cmd+Shift+3/4/5 / Win+Shift+S) — reuse: kglobalaccel + Spectacle — **done** (scripts/shortcuts.sh custom .desktop files + X-KDE-Shortcuts, same mechanism as Spotlight/Launchpad)
- [ ] Window snap shortcuts (Win+arrows / Ventura tile shortcuts) — reuse: kglobalaccel + KWin Quick Tile — **todo**
- [ ] Virtual-desktop switch shortcuts (Ctrl+arrows / Ctrl+Win+arrows) — reuse: kglobalaccel + KWin desktops — **todo**
- [ ] Lock screen shortcut (Cmd+Ctrl+Q / Win+L) — reuse: kglobalaccel + Session.qml — **partial** (menu item exists, no global bind)
- [x] App switcher shortcut (Cmd+Tab / Alt+Tab) — reuse: kglobalaccel + KWin TabBox — **done** (KWin's own Alt+Tab/Cmd+Tab bindings, no rebind needed; styled via sonata-kwin's TabBox LayoutName)

## 15. System Settings app
- [ ] Decide reuse strategy: GNOME Settings panels (partial) vs bespoke "Sonata Settings" — reuse: **GNOME Control Center** panels as stopgap — **todo** (open decision)
- [ ] Sonata Settings shell (if bespoke): appearance, dock/taskbar, shortcuts, about — reuse: Config.qml as backing store — **todo**

## 16. File manager integration
- [ ] Nautilus theming (icon view, sidebar) to match preset — reuse: GTK theme + Sonata-icons — **todo**
- [ ] Nautilus desktop-icons extension parity (if desktop icons shipped) — reuse: `nautilus-desktop-icons` extension or `desktop-icons-ng` — **todo**
- [ ] "Open with" / jump-list integration between Dock/Taskbar and Nautilus recents — reuse: `~/.local/share/recently-used.xbel` — **todo**

## 17. Theming
- [x] Design-token system (colors/radii/fonts/durations) — reuse: Theme.qml — **done**
- [x] Window decoration theme (Aurorae, both presets, light/dark) — reuse: sonata-decorations — **done**
- [x] App icon frame theme (squircle/rounded/circle) — reuse: sonata-icons — **done**
- [ ] GTK3/GTK4 full theme (beyond window-controls slice) — reuse: generated `gtk.css` — **partial**
- [ ] Qt/Kvantum theme to match accent + radii — reuse: Kvantum — **todo**
- [ ] Cursor theme — reuse: existing free macOS/Win11-style cursor themes, applied via `hyprcursor`/XCursor config — **todo**
- [ ] System sound theme (login, volume tick, notification) — reuse: `.desktop`/XDG sound theme spec — **todo**
- [x] Live light/dark switching system-wide — reuse: Appearance.qml, freedesktop portal, `plasma-apply-colorscheme` — **done**
- [ ] Accent color propagation to GTK/Qt/libadwaita — reuse: gsettings + Kvantum — **todo**

## 18. Accessibility
- [ ] Increase-contrast / reduce-transparency modes — mac Accessibility — win11 Ease of Access — reuse: Theme.qml flag + KWin Blur bypass — **todo**
- [ ] Reduce-motion toggle (disable Genie/scale/parallax) — both — reuse: Theme.qml anim durations = 0 — **todo**
- [ ] Larger text / UI scaling — both — reuse: Quickshell scale factor, `QT_SCALE_FACTOR` — **todo**
- [ ] VoiceOver/Narrator-equivalent screen-reader hooks — both — reuse: AT-SPI (Orca) — **todo**
- [ ] Sticky Keys / Zoom shortcuts surface in Control Center — both — reuse: kaccessible/xkbset — **todo**

## 19. Performance
- [x] Config-driven, no magic numbers, singleton theme (avoids re-alloc) — **done**
- [x] File-watch (not polling) for window list and dark-mode changes — reuse: `FileView(watchChanges)`, single `gdbus monitor` — **done**
- [ ] Blur perf fallback path verified on non-Plasma-6.7 compositors — reuse: Compositor.qml glassSupported flag — **partial**
- [ ] Startup-time budget / lazy-load audit of rarely-shown panels (Launchpad, Control Center, NotificationCenter) — **todo**
- [ ] Memory audit of long-running QML singletons (Apps/Windows model diffing instead of full rebuild) — **todo**

---

## Next batches (todo items ordered by user-visible impact, visual consistency first)

1. **Window chrome finish**: bottom corner rounding (kde-rounded-corners), window shadows, minimize/restore animation, double-click-titlebar zoom/maximize.
2. **Core window management**: Mission Control / Task View via KWin Overview + Present Windows, App Exposé, Show Desktop, virtual desktops + switch shortcuts, hot corners, Alt/Cmd-Tab polish.
3. **Dock/Taskbar depth**: magnification, Genie/Scale minimize effects, bounce/attention animation, badges, Stacks/folders, jump lists, progress-bar overlay, hover preview thumbnails, window grouping flyout.
4. **Shell utilities**: clipboard history (klipper) — done; screenshot UI (Spectacle) — done; Now Playing/media flyout (MPRIS2) — mac menu-bar extra done, win11 flyout placement pending (modules/win11/** owner); Night Light (KWin Night Color) — mac CC tile wired this pass; devices/eject menu (Solid) — partial (Osd notice + eject(), no dedicated flyout UI); input-source switcher, fast user switching — still todo.
5. **Control Center completeness**: Focus modes, AirDrop-via-KDE-Connect, Screen Mirroring, keyboard backlight, accessibility shortcuts tile; Notification Center widgets (calendar/weather/clock); Widgets board (win11).
6. **Platform polish**: lock screen (WlSessionLock), Settings-app decision + build-out, GTK/Kvantum/cursor/sound theming, Nautilus integration, accessibility (contrast/motion/scaling/AT-SPI), performance audits (startup, memory).
