"""The menu bar (macOS Big Sur): 24 px translucent bar at the top.

Left:  Sonata menu (logo) -- About This Computer, Recent Items, Sleep,
       Restart..., Shut Down..., Lock Screen, Log Out...
       active app name (bold) -- About, Hide, Hide Others, Show All, Quit
       Window -- Minimize, Zoom, the app's windows, Bring All to Front
       (both hidden while the desktop has the focus -- Vini's choice)
Right: background apps' tray icons (tray.py), then menu extras -- Sound, Battery, Wi-Fi, Control Center, clock; own
       icons (sonata-volume/-wifi/-battery-*, tools/gen-status-icons.py).

Apps' own menus (File, Edit, ...) need a global-menu protocol GTK4/Qt6 apps
don't export on Wayland; the bar offers what wlr-foreign-toplevel allows.
Menus hang from the title's left edge like macOS. Linux state comes from
backend/system.py (async)."""
import os
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from .. import apps, config, ui  # noqa: E402
from ..backend import power, system  # noqa: E402
from . import layer  # noqa: E402

BAR_H = 24
# the menu bar's panels (Control Center, Wi-Fi, clock...) open this far below it
PANEL_GAP = 2
# fixed content widths of the menu bar's panels: a long network, device or
# song name ellipsizes, never widens a panel (ui/fixed.py)
STATUS_W = 280
# Control Center: as wide as controlcenter.width_for() gives for its display, whatever
# is added (Vini); it grows downwards with more modules, up to the screen
# Sound and Now Playing live in the Control Center; the menu bar items are
# optional (Settings > Menu Bar), off by default (Vini).
APP_NAME_CHARS = 24     # the bold app name in the menu bar, then "…"
LOGO_PX = 12            # the Sonata menu's logo / shape / symbol (under the 16 px status icons; Vini's call)
DEFAULTS = {"battery_percent": False, "clock_format": "%a %-d %b  %H:%M", "show_bluetooth": True,
            "show_sound": False, "show_now_playing": False, "show_tray": True,
            # every status item can be taken out of the menu bar (Settings > Menu Bar, Vini);
            # Control Center and the clock always stay, like macOS
            "show_wifi": True, "show_battery": True, "show_spotlight": True, "show_input": True,
            # performance figures (statsui.py), off by default; each as "text" or "graph"
            "show_cpu": False, "show_gpu": False, "show_ram": False, "show_net": False, "show_fps": False,
            "cpu_style": "text", "gpu_style": "text", "ram_style": "text", "net_style": "text",
            "fps_style": "text",
            "autohide": False}                  # Settings > Menu Bar: hide it like the Dock (Vini)
# a key per graphics card when there are two or more (stats.KINDS: gpu_amd, gpu_nvidia...)
from ..backend import stats as _stats  # noqa: E402
for _k in _stats.KINDS:
    DEFAULTS.setdefault(f"show_{_k}", False)
    DEFAULTS.setdefault(f"{_k}_style", "text")
HIDE_MS = 250                                   # auto-hide slide (the Dock's)
REVEAL_MS = 150                                 # pointer at the top edge -> the bar comes down
POLL_S = 10

ui.register("""
window.sonata-topbar, window.sonata-topbar > contents { background: none; box-shadow: none; }
.topbar { min-height: %(bar_h)dpx; padding: 0; font-family: %(font)s; font-size: %(text_body)s;
  color: %(label)s; }
.topbar-item, .topbar-item:hover, .topbar-item:focus {
  min-height: %(item_h)dpx; min-width: 0; padding: 0 10px; margin: 0; border-radius: 4px;
  border: none; background: none; box-shadow: none; outline: none; color: %(label)s;
  font-weight: 400; }
.topbar-item.open, .topbar-item:active { background: %(bar_item_active)s; }
.topbar-item.app { font-weight: 700; }
.topbar-item.icon { padding: 0 8px; }
.topbar-item > box > image { -gtk-icon-size: 16px; }
.topbar-item.battery > box > image { -gtk-icon-size: 24px; }       /* wide battery, macOS proportions */
.topbar-item.input-src > box > label { font-size: 10px; font-weight: 700; padding: 0 3px; border-radius: 3px;
  box-shadow: inset 0 0 0 1.2px %(label)s; }
.topbar-item > box > label.percent { margin-right: 5px; font-size: %(text_body)s; }
.about-box { padding: 4px 36px 24px 36px; font-family: %(font)s; color: %(label)s; }
.about-name { font-family: %(font_display)s; font-size: 26px; font-weight: 700; }
.about-version { color: %(label_secondary)s; margin-bottom: 14px; }
.about-key { font-weight: 700; }
""", key="topbar", bar_h=BAR_H, item_h=BAR_H - 2)


# -- services shared by every display's bar (one watcher each, not one per bar) -----------
_SHARED = {}
_BARS = []                       # live bars (stop() removes one)


def _shared(key, make):
    if key not in _SHARED:
        _SHARED[key] = make()
    return _SHARED[key]


def _power_changed() -> None:
    for b in list(_BARS):
        b._poll_battery()


class _Bluetooth:
    """The BlueZ adapter, watched once for all bars (async proxy: a slow
    bluetoothd never stalls the menu bar)."""

    def __init__(self):
        self.proxy = None
        self.listeners = []
        Gio.bus_watch_name(Gio.BusType.SYSTEM, "org.bluez", Gio.BusNameWatcherFlags.NONE,
                           self._appeared, self._vanished)

    def _appeared(self, conn, _name, _owner):
        Gio.DBusProxy.new(conn, Gio.DBusProxyFlags.NONE, None, "org.bluez", "/org/bluez/hci0",
                          "org.bluez.Adapter1", None, self._made)

    def _made(self, _src, res):
        try:
            self.proxy = Gio.DBusProxy.new_finish(res)
        except GLib.Error:
            self.proxy = None
        if self.proxy is not None:
            self.proxy.connect("g-properties-changed", lambda *_: self._changed())
        self._changed()

    def _vanished(self, *_a):
        self.proxy = None
        self._changed()

    def _changed(self):
        for cb in list(self.listeners):
            cb()

    def powered(self):
        v = self.proxy.get_cached_property("Powered") if self.proxy else None
        return None if v is None else v.unpack()


class Bar(Gtk.CenterBox):
    """The bar's content; painted with the bar material in do_snapshot."""

    def __init__(self, manager=None):
        super().__init__(css_classes=["topbar"], hexpand=True)
        self.cfg = config.load("topbar", DEFAULTS)
        self.manager = manager if manager and manager.available else None
        self.backdrop = None
        self.items = []
        left = Gtk.Box()
        self.logo = self._item(left, on_click=self._sonata_menu, css="icon")
        from ..ui.logo import LogoGlyph                      # distro logo, a shape or a symbol (Settings)
        self.logo.get_child().append(LogoGlyph(LOGO_PX))
        self.app_btn = self._item(left, text="Files", on_click=self._app_menu, css="app")
        # a very long app name ("GNU Image Manipulation Program", a raw app id)
        # ellipsizes: with tray and stats on a 1280 px display the bar overflowed
        name = self.app_btn.get_child().get_last_child()
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(APP_NAME_CHARS)
        # on the desktop (nothing focused) the menus are Files' own, like Finder's
        self.file_btn = self._item(left, text="File", on_click=self._files_file_menu)
        self.go_btn = self._item(left, text="Go", on_click=self._files_go_menu)
        self.win_btn = self._item(left, text="Window", on_click=self._window_menu)
        left.set_margin_start(8)       # no CSS padding: the bar is painted over the whole allocation
        self.set_start_widget(left)

        right = Gtk.Box()
        # background apps' status icons (StatusNotifierItem, tray.py), left of the extras
        from .tray import TrayBox
        self.tray = TrayBox(self, self.cfg.get("show_tray", True))
        right.append(self.tray)
        # CPU, GPU, memory, network, FPS (Settings > Menu Bar; off by default)
        self.stats_box = Gtk.Box()
        right.append(self.stats_box)
        self._stat_items = []
        self._build_stats()
        # (Screen recording: its status and stop button are the control in the
        #  middle of the top of the screen -- capture.RecordingControl; not here too)
        # Now Playing (while a player runs), input source (with 2+ keyboard layouts)
        from . import clipboard, mpris
        self.players = mpris.players()
        self.nowplaying = self._item(right, icon="sonata-now-playing-symbolic", on_click=self._nowplaying_panel,
                                     css="icon")
        self.players.listeners.append(self._extras_visibility)
        # clipboard history: kept here, shown by Super+V (clip_picker.py), no menu bar item
        # (one wl-paste watcher for the process, whatever the number of displays)
        self.clip = _shared("clip", clipboard.History)
        # fullscreen first (gamemode.py): while a fullscreen app has the focus
        # the polling pauses; leaving it catches up at once. One watcher for all bars.
        from .. import gamemode
        self.fullscreen_first = _shared("gamemode", gamemode.Watcher)
        self._fullscreen_cb = lambda on: on or self._poll()
        self.fullscreen_first.listeners.append(self._fullscreen_cb)
        self.input_btn = self._item(right, text="", on_click=self._input_panel)
        self.input_btn.add_css_class("input-src")
        self._update_input()
        self.sound = self._item(right, icon="sonata-volume-3-symbolic", on_click=self._sound_panel, css="icon")
        self._extras_visibility()
        self.battery = self._item(right, icon="sonata-battery-100-symbolic", on_click=self._battery_panel,
                                  css="icon")
        self.battery.add_css_class("battery")
        self.battery_pct = Gtk.Label(css_classes=["percent"])
        self.battery.get_child().prepend(self.battery_pct)          # "87% [battery]" like Big Sur
        self.bt = self._item(right, icon="sonata-bluetooth-symbolic", on_click=self._bluetooth_panel,
                             css="icon")
        self.bt.set_visible(False)
        self._watch_bluetooth()
        self.wifi = self._item(right, icon="sonata-wifi-3-symbolic",
                               on_click=self._wifi_panel, css="icon")
        self.spotlight = self._item(right, icon="sonata-search-symbolic", on_click=self._spotlight, css="icon")
        self.spotlight.set_visible(self.cfg.get("show_spotlight", True))
        self.cc = self._item(right, icon="sonata-control-center-symbolic", on_click=self._control_center,
                             css="icon")
        self.clock = self._item(right, text="", on_click=self._calendar)
        right.set_margin_end(8)
        self.set_end_widget(right)

        self._theme_cb = ui.theme.on_change(self.queue_draw)
        self._cfg_mon = config.watch("topbar", self._config_changed)
        if self.manager:
            self.manager.listeners.append(self._active_changed)
        self.alive = True                        # False once its display is gone (timers stop)
        self._tick_clock()
        self._active_changed()
        self._poll()
        GLib.timeout_add_seconds(POLL_S, lambda: (self.alive and self._poll(), self.alive)[1])
        _BARS.append(self)
        # plug / charge / level: at once (subscribed once; it calls every live bar)
        _shared("power", lambda: power.watch(_power_changed))

    def stop(self) -> None:
        """Its display was unplugged: no more polling or listening (the shared
        watchers stay for the other bars)."""
        self.alive = False
        self.tray.stop()
        if self in _BARS:
            _BARS.remove(self)
        for owner, cb in ((self.manager, self._active_changed), (self.players, self._extras_visibility),
                          (self.fullscreen_first, self._fullscreen_cb), (self._bt, self._bt_update)):
            if owner is not None and cb in owner.listeners:
                owner.listeners.remove(cb)
        ui.theme.off_change(self._theme_cb)
        self._cfg_mon.cancel()

    def _extras_visibility(self) -> None:
        self.nowplaying.set_visible(self.cfg["show_now_playing"] and self.players.active)
        if hasattr(self, "sound"):
            self.sound.set_visible(self.cfg["show_sound"])

    def _config_changed(self) -> None:
        """Settings app changed topbar.json: apply live."""
        self.cfg = config.load("topbar", DEFAULTS)
        self.battery_pct.set_visible(self.cfg["battery_percent"] and self.battery.get_visible())
        self._bt_update()
        self._extras_visibility()
        self.tray.set_shown(self.cfg.get("show_tray", True))
        self.spotlight.set_visible(self.cfg.get("show_spotlight", True))
        self._build_stats()
        self._update_input()
        self._poll()                                # Wi-Fi / battery shown or not, at once
        if getattr(self, "on_autohide", None):
            self.on_autohide()
        now = GLib.DateTime.new_now_local()
        self._set_text(self.clock, now.format(self.cfg["clock_format"]) or now.format("%a %H:%M"))

    # -- drawing -------------------------------------------------------------------
    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        if w > 0 and h > 0:
            rect = Graphene.Rect()
            rect.init(0, 0, w, h)
            if self.backdrop:        # preview: stands in for the compositor's blur (bar area only)
                snap.push_clip(rect)
                snap.append_texture(self.backdrop, Graphene.Rect().init(0, 0, self.backdrop.get_width(),
                                                                        self.backdrop.get_height()))
                snap.pop()
            # Big Sur: translucent material, no bottom line
            snap.append_color(ui.rgba("bar_material"), rect)       # ui/glass.py
        Gtk.CenterBox.do_snapshot(self, snap)

    # -- items ---------------------------------------------------------------------
    def _item(self, box, text=None, icon=None, on_click=None, css=""):
        b = Gtk.Button(css_classes=["topbar-item"] + ([css] if css else []), can_focus=False,
                       valign=Gtk.Align.CENTER)
        content = Gtk.Box(valign=Gtk.Align.CENTER)
        if icon:
            content.append(Gtk.Image(icon_name=icon))
        if text is not None:
            content.append(Gtk.Label(label=text))
        b.set_child(content)
        b.connect("clicked", lambda btn: self._open(btn, on_click))
        box.append(b)
        self.items.append(b)
        return b

    def _build_stats(self) -> None:
        """The performance items the settings ask for, in a fixed order."""
        from . import statsui
        want = [(k, self.cfg.get(f"{k}_style", "text")) for k in statsui.KINDS if self.cfg.get(f"show_{k}")]
        if want == getattr(self, "_stats_shown", None):
            return
        self._stats_shown = want
        for b in self._stat_items:
            self.stats_box.remove(b)
            if b in self.items:
                self.items.remove(b)
        self._stat_items = []
        for kind, style in want:
            b = self._item(self.stats_box, on_click=self._open_task_manager, css="stat")
            b.set_child(statsui.menu_item(kind, style))
            b.set_tooltip_text(statsui.TITLES[kind])
            self._stat_items.append(b)

    def _open_task_manager(self, _btn):
        """A performance item: Task Manager (no menu of its own)."""
        from ..__main__ import self_argv
        try:
            GLib.spawn_async(self_argv() + ["activity"], flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error:
            pass
        return None

    def _set_text(self, btn, text) -> None:
        lbl = btn.get_child().get_last_child()
        if isinstance(lbl, Gtk.Label):
            lbl.set_label(text)

    def _set_icon(self, btn, name) -> None:
        # the item's image (the battery has its percentage label before it)
        child = btn.get_child().get_first_child()
        while child is not None and not isinstance(child, Gtk.Image):
            child = child.get_next_sibling()
        if child is not None and child.get_icon_name() != name:
            child.set_from_icon_name(name)

    def _open(self, btn, builder) -> None:
        reveal = getattr(self, "reveal", None)
        if reveal:                                # a hidden bar comes down for its menu
            reveal()
        btn.add_css_class("open")
        pop = builder(btn)
        if pop is None:
            btn.remove_css_class("open")
            return
        pop.connect("closed", lambda *_: btn.remove_css_class("open"))

    def open_menu(self, index: int) -> None:
        """Open the N-th item (screenshots)."""
        if 0 <= index < len(self.items):
            self.items[index].emit("clicked")

    def _menu(self, btn, sections):
        pop = ui.menu.popup(btn, sections, position=Gtk.PositionType.BOTTOM, gap=PANEL_GAP, glass=True)
        ui.panel.align_to_start(pop, btn, 2)
        return pop

    # -- Sonata menu -----------------------------------------------------------------
    def _sonata_menu(self, btn):
        Item = ui.menu.Item
        user = GLib.get_real_name() or GLib.get_user_name()
        return self._menu(btn, [
            [Item("About This Computer", lambda: AboutWindow().present())],
            [Item("System Settings…", lambda: open_settings())],
            [Item("Recent Items", submenu=self._recent_items())],
            [Item("Restart Sonata", system.restart_sonata)],      # reload the shell; apps stay open
            [Item("Sleep", lambda: system.power_action("sleep")),
             Item("Restart…", lambda: self._confirm("restart")),
             Item("Shut Down…", lambda: self._confirm("shutdown"))],
            [Item("Lock Screen", lambda: system.power_action("lock")),
             Item(f"Log Out {user}…", lambda: self._confirm("logout"))],
        ])

    def _recent_items(self):
        Item = ui.menu.Item
        dock = config.load("dock", {"recent": []})
        app_items = []
        for did in dock.get("recent", [])[:5]:
            info = apps.lookup(did)
            if info:
                app_items.append(Item(info.get_display_name(), lambda i=info: i.launch([], None)))
        docs = []
        for r in sorted(Gtk.RecentManager.get_default().get_items(), key=lambda r: -r.get_modified().to_unix())[:8]:
            if r.exists():
                docs.append(Item(r.get_display_name(), lambda u=r.get_uri(): _open_recent(u)))
        sections = []
        if app_items:
            sections.append(app_items)
        if docs:
            sections.append(docs)
        return sections or [[Item("No recent items", enabled=False)]]

    def _confirm(self, kind: str) -> None:
        text = {"restart": ("Are you sure you want to restart your computer now?", "Restart"),
                "shutdown": ("Are you sure you want to shut down your computer now?", "Shut Down"),
                "logout": ("Are you sure you want to quit all applications and log out now?", "Log Out")}[kind]
        ui.dialog.alert(text[0], "", [("cancel", "Cancel", ""), (kind, text[1], "default")],
                        lambda r: self._end_session(kind) if r == kind else None)

    def _end_session(self, kind: str) -> None:
        """The apps quit first (Chrome kept asking to restore its tabs)."""
        from . import quitapps
        self._quitting = quitapps.end_session(kind, self.manager)

    # -- active app ------------------------------------------------------------------
    def _active(self):
        if not self.manager:
            return None, []
        act = next((t for t in self.manager.toplevels if t.activated), None)
        if not act:
            return None, []
        act.focused_at = GLib.get_monotonic_time()  # window recency: Super+Tab raises the last used on top
        key = apps.match_app_id(act.app_id) or act.app_id
        wins = [t for t in self.manager.toplevels if (apps.match_app_id(t.app_id) or t.app_id) == key]
        return key, wins

    def _active_changed(self) -> None:
        """App name + menus follow the focused app; on the desktop (nothing
        focused) they are Files' -- File, Go, Window -- like Finder's."""
        key, _wins = self._active()
        if key:                                   # most recently used apps (app switcher)
            self.mru = [key] + [k for k in getattr(self, "mru", []) if k != key]
        desktop = not key
        self.file_btn.set_visible(desktop)
        self.go_btn.set_visible(desktop)
        if key:
            info = apps.lookup(key)
            self._set_text(self.app_btn, info.get_display_name() if info else key)
        else:
            self._set_text(self.app_btn, "Files")

    # -- Files' menus on the desktop -------------------------------------------------------
    def _files_file_menu(self, btn):
        from ..files import open_folder
        from ..shell.desktop import desktop_dir
        Item = ui.menu.Item
        home = Gio.File.new_for_path(GLib.get_home_dir()).get_uri()

        def new_folder():
            from ..files import ops
            ops.new_folder(desktop_dir(), lambda _f: None, lambda _e: None)
        return self._menu(btn, [
            [Item("New Files Window", lambda: open_folder(home)),
             Item("New Folder", new_folder)],
            [Item("Open Desktop in Files", lambda: open_folder(desktop_dir().get_uri()))],
            [Item("Empty Trash…", lambda: open_folder("trash:///"))],
        ])

    def _files_go_menu(self, btn):
        from ..files import open_folder
        Item = ui.menu.Item
        U = GLib.UserDirectory

        def place(kind):
            from .. import userdirs
            p = userdirs.special(kind)
            return Gio.File.new_for_path(p).get_uri() if p else None
        home = Gio.File.new_for_path(GLib.get_home_dir()).get_uri()
        rows = [("Recents", "sonata:recents"), ("Documents", place(U.DIRECTORY_DOCUMENTS)),
                ("Desktop", place(U.DIRECTORY_DESKTOP)), ("Downloads", place(U.DIRECTORY_DOWNLOAD)),
                ("Home", home), ("Pictures", place(U.DIRECTORY_PICTURES)), ("Music", place(U.DIRECTORY_MUSIC))]
        return self._menu(btn, [
            [Item(name, lambda u=uri: open_folder(u)) for name, uri in rows[:1]],
            [Item(name, lambda u=uri: open_folder(u), enabled=bool(uri)) for name, uri in rows[1:]],
            [Item("Computer", lambda: open_folder("file:///")), Item("Trash", lambda: open_folder("trash:///"))],
        ])

    def _app_menu(self, btn):
        Item = ui.menu.Item
        key, wins = self._active()
        from ..files import APP_ID as FILES_ID
        info = apps.lookup(key) if key else apps.lookup(FILES_ID)       # desktop: Files, like Finder
        name = info.get_display_name() if info else (key or "Files")
        m = self.manager
        others = [t for t in (m.toplevels if m else []) if t not in wins]
        return self._menu(btn, [
            [Item(f"About {name}", lambda: AboutAppWindow(info, name).present(), enabled=bool(info))],
            [Item(f"Hide {name}", lambda: [m.minimize(t) for t in wins], enabled=bool(wins)),
             Item("Hide Others", lambda: [m.minimize(t) for t in others], enabled=bool(others)),
             Item("Show All", lambda: [m.unminimize(t) for t in (m.toplevels if m else [])], enabled=bool(m))],
            [Item(f"Quit {name}", lambda: [m.close(t) for t in wins], enabled=bool(wins))],
        ])

    def _window_menu(self, btn):
        Item = ui.menu.Item
        key, wins = self._active()
        m = self.manager
        act = next((t for t in wins if t.activated), None)
        sections = [[Item("Minimize", lambda: m.minimize(act), enabled=bool(act)),
                     Item("Zoom", lambda: m.set_maximized(act, not act.maximized), enabled=bool(act))]]
        if wins:
            sections.append([Item(t.title or "Untitled", lambda t=t: m.activate(t), checked=t is act)
                             for t in wins])
        sections.append([Item("Bring All to Front", lambda: [m.activate(t) for t in wins], enabled=bool(wins))])
        return self._menu(btn, sections)

    # -- clock / calendar ----------------------------------------------------------------
    def _tick_clock(self) -> bool:
        now = GLib.DateTime.new_now_local()
        fmt = self.cfg["clock_format"]
        self._set_text(self.clock, now.format(fmt) or now.format("%a %H:%M"))
        if self.alive:
            GLib.timeout_add_seconds(max(1, 60 - now.get_second()), self._tick_clock)
        return False

    def _calendar(self, btn):
        """Big Sur: the clock opens Notification Center."""
        nc = getattr(self, "notifications", None)
        if nc is not None:
            nc.toggle_center()
            return None
        cal = Gtk.Calendar()
        return ui.panel.popup(btn, ui.panel.column(cal), gap=PANEL_GAP, width=STATUS_W)

    # -- status polling ------------------------------------------------------------------
    def _poll(self) -> None:
        if self.fullscreen_first.active:
            return                      # a fullscreen game/video has the focus: stay out of its way
        system.run_async(lambda: (system.wifi_available(), system.wifi_enabled(), system.wifi_current()),
                         self._wifi_state)
        self._poll_battery()
        system.run_async(system.volume, self._sound_state)

    def _poll_battery(self) -> None:
        system.run_async(lambda: (*system.battery(), system.on_ac(), system.power_profile_fast()),
                         self._battery_state)

    def _wifi_state(self, res) -> None:
        if not res:
            return
        avail, on, (ssid, sig, wired) = res
        if wired and not ssid:
            name = "network-wired-symbolic"
        elif not avail:
            self.wifi.set_visible(False)
            return
        elif not on:
            name = "sonata-wifi-off-symbolic"
        elif not ssid:
            name = "sonata-wifi-0-symbolic"
        else:
            name = f"sonata-wifi-{3 if sig > 60 else 2 if sig > 30 else 1}-symbolic"
        self.wifi.set_visible(self.cfg.get("show_wifi", True))
        self._set_icon(self.wifi, name)

    def _battery_state(self, res) -> None:
        pct, status, ac, profile = (tuple(res) + (None,) * 4)[:4] if res else (None, "", False, None)
        self.battery.set_visible((pct is not None or bool(status)) and self.cfg.get("show_battery", True))
        if pct is None:
            if status:
                self._set_icon(self.battery, "sonata-battery-missing-symbolic")    # battery, no reading
            return
        self._set_icon(self.battery, power.icon_name(pct, status, ac, profile))
        self.battery_pct.set_label(f"{pct}%")
        self.battery_pct.set_visible(self.cfg["battery_percent"])

    def _sound_state(self, res) -> None:
        self.sound.set_visible(res is not None and self.cfg["show_sound"])
        if res:
            vol, muted = res
            self._set_icon(self.sound, "sonata-volume-muted-symbolic" if muted or vol == 0 else
                           f"sonata-volume-{3 if vol > 66 else 2 if vol > 33 else 1}-symbolic")

    # -- extras panels -----------------------------------------------------------------------
    def _prefs_row(self, pop, title, page):
        """"Wi-Fi Preferences…" etc. at the bottom of a menu (Big Sur)."""
        return ui.panel.row(None, title, on_click=lambda: (pop.popdown(), open_settings(page)))

    def _wifi_panel(self, btn):
        """Big Sur Wi-Fi menu: switch, the joined network, other networks
        (round signal badges, lock), Wi-Fi Preferences…"""
        on = Gtk.Switch(css_classes=["sonata-switch"], valign=Gtk.Align.CENTER)
        col = ui.panel.column(ui.panel.header("Wi-Fi", on))
        cur_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        nets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(ui.panel.separator())
        col.append(cur_box)
        col.append(ui.panel.section_title("Other Networks"))
        nets.append(ui.panel.row(None, "Searching…"))
        col.append(nets)
        col.append(ui.panel.separator())
        pop = ui.panel.popup(btn, col, gap=PANEL_GAP, width=STATUS_W)
        col.append(self._prefs_row(pop, "Wi-Fi Preferences…", "wifi"))
        ui.panel.align_to_start(pop, btn, 2)

        def badge(n):
            level = 3 if n.signal > 60 else 2 if n.signal > 30 else 1
            b = Gtk.Box(css_classes=["wifi-badge"] + (["on"] if n.connected else []), valign=Gtk.Align.CENTER,
                        halign=Gtk.Align.START, hexpand=False)
            b.append(Gtk.Image(icon_name=f"sonata-wifi-{level}-symbolic", pixel_size=14,
                               halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER))
            return b

        def net_row(n):
            trailing = Gtk.Box(spacing=4)
            if n.secure:
                trailing.append(Gtk.Image(icon_name="system-lock-screen-symbolic", pixel_size=12,
                                          css_classes=["dim-label"]))
            row = ui.panel.row(None, n.ssid, trailing, on_click=lambda n=n: (pop.popdown(), self._join(n)))
            content = row.get_child() if isinstance(row, Gtk.Button) else row
            content.prepend(badge(n))
            return row

        def fill(res):
            enabled, networks = res or (False, [])
            on.set_active(enabled)
            for box in (cur_box, nets):
                while box.get_first_child():
                    box.remove(box.get_first_child())
            joined = [n for n in networks if n.connected]
            if joined:
                cur_box.append(ui.panel.section_title("Known Network"))
                cur_box.append(net_row(joined[0]))
            others = [n for n in networks if not n.connected][:10]
            for n in others:
                nets.append(net_row(n))
            if not others:
                nets.append(ui.panel.row(None, "No networks" if enabled else "Wi-Fi: Off"))
        cached("wifi", _wifi_list, fill)
        on.connect("state-set", lambda _s, st: (system.run_async(system.set_wifi_enabled, lambda _r: self._poll(), st),
                                                False)[1])
        return pop

    # -- Bluetooth (BlueZ over D-Bus: the icon follows the adapter, no polling) --------------
    def _watch_bluetooth(self) -> None:
        self._bt = _shared("bluetooth", _Bluetooth)
        self._bt.listeners.append(self._bt_update)
        self._bt_update()

    def _bt_powered(self):
        return self._bt.powered()

    def _bt_update(self) -> None:
        powered = self._bt_powered()
        self.bt.set_visible(powered is not None and self.cfg.get("show_bluetooth", True))
        self._set_icon(self.bt, "sonata-bluetooth-symbolic" if powered else "sonata-bluetooth-off-symbolic")

    def _bluetooth_panel(self, btn):
        """Big Sur Bluetooth menu: switch, Devices (paired, connected ones
        highlighted; click to connect/disconnect), Bluetooth Preferences…"""
        on = Gtk.Switch(css_classes=["sonata-switch"], valign=Gtk.Align.CENTER,
                        active=bool(self._bt_powered()))
        col = ui.panel.column(ui.panel.header("Bluetooth", on))
        col.append(ui.panel.separator())
        col.append(ui.panel.section_title("Devices"))
        devs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        devs.append(ui.panel.row(None, "Searching…"))
        col.append(devs)
        col.append(ui.panel.separator())
        pop = ui.panel.popup(btn, col, gap=PANEL_GAP, width=STATUS_W)
        col.append(self._prefs_row(pop, "Bluetooth Preferences…", "bluetooth"))
        ui.panel.align_to_start(pop, btn, 2)

        def badge(d):
            b = Gtk.Box(css_classes=["wifi-badge"] + (["on"] if d.connected else []), valign=Gtk.Align.CENTER,
                        halign=Gtk.Align.START, hexpand=False)
            b.append(Gtk.Image(icon_name=_bt_icon(d.name), pixel_size=14, halign=Gtk.Align.CENTER,
                               valign=Gtk.Align.CENTER))
            return b

        def fill(lst):
            while devs.get_first_child():
                devs.remove(devs.get_first_child())
            paired = [d for d in lst or [] if d.paired]
            for d in paired[:12]:
                row = ui.panel.row(None, d.name, on_click=lambda d=d: (
                    pop.popdown(), system.run_async(system.bluetooth_connect, None, d.mac, not d.connected)))
                content = row.get_child() if isinstance(row, Gtk.Button) else row
                content.prepend(badge(d))
                devs.append(row)
            if not paired:
                devs.append(ui.panel.row(None, "No devices" if self._bt_powered() else "Bluetooth: Off"))
        cached("bluetooth", lambda: system.bluetooth_devices(), fill)
        on.connect("state-set", lambda _s, st: (system.run_async(system.set_bluetooth, None, st), False)[1])
        return pop

    def _join(self, net) -> None:
        if net.connected:
            return
        if not net.secure:
            system.run_async(system.wifi_connect, lambda _r: self._poll(), net.ssid)
            return
        entry = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        dlg = ui.dialog.alert(f"The Wi-Fi network “{net.ssid}” requires a password.", "",
                              [("cancel", "Cancel", ""), ("join", "Join", "default")],
                              lambda r: r == "join" and system.run_async(
                                  system.wifi_connect, lambda _r: self._poll(), net.ssid, entry.get_text()))
        dlg.set_extra_child(entry)

    def _battery_panel(self, btn):
        """Big Sur battery menu: header with the level, power source,
        Show Percentage, Battery Preferences… (filled in the background)."""
        pct_lbl = Gtk.Label(css_classes=["dim-label"])
        src_row = ui.panel.row(None, "Power Source: …")
        percent_sw = ui.controls.switch(self.cfg["battery_percent"], self._toggle_percent)
        # Energy Mode (power-profiles-daemon), macOS Ventura: a checkmark on the current one
        modes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        mode_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=False)
        mode_box.append(ui.panel.section_title("Energy Mode"))
        mode_box.append(modes)
        mode_box.append(ui.panel.separator())
        col = ui.panel.column(ui.panel.header("Battery", pct_lbl), src_row, ui.panel.separator(), mode_box,
                              ui.panel.row(None, "Show Percentage", percent_sw), ui.panel.separator())
        pop = ui.panel.popup(btn, col, gap=PANEL_GAP, width=STATUS_W)
        col.append(self._prefs_row(pop, "Battery Preferences…", "battery"))
        ui.panel.align_to_start(pop, btn, 2)

        def pick(key):
            pop.popdown()
            from .. import gamemode
            if gamemode.boosted():              # raised for a full-screen game (gamemode.PowerBoost)
                if key == "balanced":
                    return                      # already Automatic
                gamemode.set_boosted(False)     # the user's own pick: kept after the game
            system.run_async(system.set_power_profile, lambda _r: self._poll_battery(), key)

        def fill_modes(current):
            if not current:
                return                          # no power-profiles-daemon: no section
            from .. import gamemode
            if current == "performance" and gamemode.boosted():
                current = "balanced"            # Automatic, running a game at full speed
            for key, label in system.POWER_PROFILES:
                r = ui.panel.row("object-select-symbolic", label, on_click=lambda k=key: pick(k))
                r.icon.set_opacity(1 if key == current else 0)       # checkmark column (menus)
                modes.append(r)
            mode_box.set_visible(True)
        system.run_async(system.power_profile_fast, fill_modes)

        def fill(res):
            (pct, status), ac = res or ((None, ""), False)
            pct_lbl.set_label(f"{pct}%" if pct is not None else "")
            text = "Power Source: " + ("Power Adapter" if ac else "Battery")
            src_row.label.set_label(text + (f" · {status}" if status and status not in ("Discharging", "Unknown")
                                            else ""))
        system.run_async(lambda: (system.battery(), system.on_ac()), fill)
        return pop

    def _toggle_percent(self, on: bool) -> None:
        self.cfg["battery_percent"] = on
        config.save("topbar", self.cfg)
        self.battery_pct.set_visible(on)

    def _sound_panel(self, btn):
        """Big Sur sound menu: slider, Output devices (check on the current
        one), Sound Preferences…"""
        # the same slider as Control Center's Sound module (icon in the capsule)
        box, slider = _slider_with_icon(_speaker_icon, 0, lambda v: self._set_volume(v))
        _volume_feedback(slider)
        holder = Gtk.Box(css_classes=["panel-header"])
        holder.append(box)
        outs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col = ui.panel.column(ui.panel.header("Sound"), holder, ui.panel.separator(),
                              ui.panel.section_title("Output"), outs, ui.panel.separator())
        from ..backend import mixer
        service = getattr(self, "mixer", None)
        if mixer.available():                                # every app's own volume, kept (Vini)
            from .mixer_ui import AppMixer
            apps_box = AppMixer()
            col.append(ui.panel.section_title("Applications"))
            col.append(apps_box)
            col.append(ui.panel.separator())
            self.app_mixer = apps_box                        # (tests)
            system.run_async(mixer.streams, apps_box.set_streams)
        pop = ui.panel.popup(btn, col, gap=PANEL_GAP, width=STATUS_W)
        if mixer.available() and service is not None:      # apps start / stop playing while it's open
            service.listeners.append(apps_box.set_streams)
            pop.connect("closed", lambda _p: apps_box.set_streams in service.listeners
                        and service.listeners.remove(apps_box.set_streams))
        col.append(self._prefs_row(pop, "Sound Preferences…", "sound"))
        ui.panel.align_to_start(pop, btn, 2)

        def fill(res):
            vol, sinks = res or (None, [])
            if vol:
                slider.set_value(vol[0])
            for sk in sinks:
                r = ui.panel.row("object-select-symbolic", sk.name, on_click=lambda sk=sk: (
                    pop.popdown(), system.run_async(system.select_output, None, sk.key)))
                r.icon.set_opacity(1 if sk.default else 0)            # checkmark column (menus)
                outs.append(r)
            if not sinks:
                outs.append(ui.panel.row(None, "No output devices"))
        system.run_async(lambda: (system.volume(), system.audio_outputs()), fill)
        return pop

    def _set_volume(self, v) -> None:
        system.run_async(system.set_volume, lambda _r: self._poll(), int(v), False)

    # -- Now Playing / input source -----------------------------------------------------------
    def _nowplaying_panel(self, btn):
        pop = ui.panel.popup(btn, ui.panel.column(now_playing_module(self.players, header=True)), gap=PANEL_GAP, width=STATUS_W)
        ui.panel.align_to_start(pop, btn, 2)
        return pop

    def _layouts(self):
        lay = system.wayfire_get("input", "xkb_layout", "us") or "us"
        var = system.wayfire_get("input", "xkb_variant", "")
        lays, vars_ = lay.split(","), var.split(",")
        return [f"{l}({v})" if i < len(vars_) and vars_[i] else l for i, (l, v) in
                enumerate(zip(lays, vars_ + [""] * len(lays)))]

    def _update_input(self):
        lays = self._layouts()
        self.input_btn.set_visible(len(lays) > 1 and self.cfg.get("show_input", True))
        if lays:
            self._set_text(self.input_btn, lays[0].split("(")[0].upper()[:3])

    def _input_panel(self, btn):
        names = dict(system.XKB_LAYOUTS)
        lays = self._layouts()
        col = ui.panel.column()
        pop = ui.panel.popup(btn, col, gap=PANEL_GAP, width=STATUS_W)
        for i, l in enumerate(lays):
            r = ui.panel.row("object-select-symbolic", names.get(l, l), on_click=lambda i=i: (
                pop.popdown(), self._use_layout(i)))
            r.icon.set_opacity(1 if i == 0 else 0)
            col.append(r)
        col.append(ui.panel.separator())
        col.append(self._prefs_row(pop, "Open Keyboard Preferences…", "keyboard"))
        ui.panel.align_to_start(pop, btn, 2)
        return pop

    def _use_layout(self, i):
        """Wayland has no "switch layout" request: the chosen one becomes the
        first of the list, which Wayfire applies at once."""
        lays = self._layouts()
        lays.insert(0, lays.pop(i))
        system.run_async(lambda: (system.wayfire_set("input", "xkb_layout", ",".join(x.split("(")[0] for x in lays)),
                                  system.wayfire_set("input", "xkb_variant", ",".join(
                                      x.split("(")[1].rstrip(")") if "(" in x else "" for x in lays))),
                         lambda _r: self._update_input())

    def _spotlight(self, btn):
        """Big Sur's magnifier: Spotlight."""
        from ..__main__ import self_argv
        btn.remove_css_class("open")
        try:
            GLib.spawn_async(self_argv() + ["spotlight"], flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error:
            pass
        return None

    def _control_center(self, btn):
        cc = ControlCenter(self)
        return ui.panel.popup(btn, cc, gap=PANEL_GAP, width=getattr(cc, "width", None))

    def _poll_soon(self) -> None:
        GLib.timeout_add(600, lambda: (self._poll(), False)[1])


def _bt_icon(name: str) -> str:
    """A device icon from its name (BlueZ's "Icon" needs another call)."""
    n = name.lower()
    for keys, icon in ((("airpods", "buds", "headphone", "headset", "wh-", "wf-"), "audio-headphones-symbolic"),
                       (("speaker", "soundbar", "jbl", "boom"), "audio-speakers-symbolic"),
                       (("mouse", "mx master", "trackpad"), "input-mouse-symbolic"),
                       (("keyboard", "keys"), "input-keyboard-symbolic"),
                       (("controller", "gamepad", "xbox", "dualsense", "dualshock", "joy-con"), "input-gaming-symbolic"),
                       (("phone", "iphone", "galaxy", "pixel"), "phone-symbolic")):
        if any(k in n for k in keys):
            return icon
    return "sonata-bluetooth-symbolic"


def open_settings(page: str = "") -> None:
    """Start Sonata Settings (its own process)."""
    import subprocess
    import sys
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    env = dict(os.environ, PYTHONPATH=repo)
    subprocess.Popen([sys.executable, "-m", "sonata2", "settings"] + (["--page", page] if page else []),
                     env=env, start_new_session=True)


ui.register("""
.cc-small { padding: 8px; }
.wifi-badge { min-width: 26px; min-height: 26px; border-radius: 99px; background: %(toggle_off)s;
  color: %(label)s; margin-right: 2px; }
.wifi-badge > image { margin: 0 6px; }          /* 26 px circle: 14 px glyph + 2 x 6 px */
.wifi-badge.on { background: %(accent)s; color: %(label_on_accent)s; }
.cc-small label { font-size: %(text_small)s; font-weight: 400; }
.cc-small.on image { color: %(accent)s; }
.cc-ns { background: none; box-shadow: none; border: none; padding: 2px 0; min-height: 0; color: %(label)s; }
.cc-ns .cc-ns-icon { min-width: 26px; min-height: 26px; border-radius: 13px; background: alpha(%(label)s, 0.1); }
.cc-ns:checked .cc-ns-icon { background: %(accent)s; color: %(label_on_accent)s; }
.cc-slider-box { min-height: 22px; }
.cc-slider-icon { color: rgba(0,0,0,0.5); margin-left: 5px; -gtk-icon-size: 12px; }   /* on the white fill;
   centred in the knob at 0 */
.cc-round { min-width: 26px; min-height: 26px; padding: 0; border-radius: 99px; border: none; box-shadow: none;
  background: %(module_button)s; color: %(label)s; }
.cc-round:hover { background: alpha(%(label)s, 0.18); }
.cc-np-art { border-radius: 6px; background: %(module_button)s; min-width: 36px; min-height: 36px; }
.cc-np-art image { color: %(label_tertiary)s; }
.cc-np-title { font-weight: 700; }
.cc-np-artist { color: %(label_secondary)s; font-size: %(text_small)s; }
.cc-np button { min-width: 26px; min-height: 26px; padding: 0; border: none; background: none;
  box-shadow: none; color: %(label)s; border-radius: 99px; }
.cc-np button:hover { background: %(tool_hover)s; }
""", key="control-center")


# -- last known answers (Wi-Fi networks, Bluetooth devices, Control Center) ------------------
# Filled once at start; a menu shows them at once and asks again, updating
# only what changed (macOS menus never open empty and fill in).
_CACHE = {}


def cached(name, fn, fill) -> None:
    if name in _CACHE:
        fill(_CACHE[name])

    def got(res):
        if res is None:
            return
        try:
            same = name in _CACHE and _CACHE[name] == res
        except Exception:
            same = False
        _CACHE[name] = res
        if not same:
            fill(res)
    system.run_async(fn, got)


def prefetch() -> None:
    """At start (after the login intro): the answers menus will need."""
    for name, fn in PREFETCH.items():
        system.run_async(fn, lambda res, n=name: res is not None and _CACHE.__setitem__(n, res))


def _wifi_list():
    return (system.wifi_enabled(), system.wifi_scan())


def _cc_state():
    return (system.wifi_enabled(), system.wifi_current(), system.bluetooth_state(), system.airplane_mode())


PREFETCH = {"wifi": _wifi_list, "bluetooth": lambda: system.bluetooth_devices(), "cc": _cc_state}


def _slider_with_icon(icon, value, on_change, sensitive=True, button=None):
    """Big Sur module slider: the symbol sits inside the capsule, left;
    `button` (a round one) at the right, like the Sound module's AirPlay.
    `icon` is a name, or a function value -> name (muted / levels)."""
    over = Gtk.Overlay(css_classes=["cc-slider-box"], hexpand=True)
    sl = ui.controls.slider(value, on_change, style="module")
    sl.set_sensitive(sensitive)
    over.set_child(sl)
    pick = icon if callable(icon) else (lambda _v: icon)
    img = Gtk.Image(icon_name=pick(value), css_classes=["cc-slider-icon"], halign=Gtk.Align.START,
                    valign=Gtk.Align.CENTER, can_target=False)
    sl.connect("value-changed", lambda s_: img.set_from_icon_name(pick(s_.get_value())))
    over.add_overlay(img)
    if button is None:
        return over, sl
    row = Gtk.Box(spacing=8)
    row.append(over)
    row.append(button)
    return row, sl


def _volume_feedback(slider) -> None:
    """The volume feedback sound when the slider is let go (Settings > Sound)."""
    from .. import sounds
    ui.controls.on_release(slider, lambda: sounds.play_soon("volume"))


def _speaker_icon(v) -> str:
    """Big Sur speaker: slashed at 0, then one to three waves."""
    return ("audio-volume-muted-symbolic" if v <= 0 else "audio-volume-low-symbolic" if v < 34 else
            "audio-volume-medium-symbolic" if v < 67 else "audio-volume-high-symbolic")


def _round_button(icon, tooltip, on_click) -> Gtk.Button:
    b = Gtk.Button(css_classes=["cc-round"], can_focus=False, valign=Gtk.Align.CENTER, tooltip_text=tooltip)
    b.set_child(Gtk.Image(icon_name=icon, pixel_size=14))
    b.connect("clicked", lambda btn: on_click(btn))
    return b


def _device_menu(btn, title, list_fn, set_fn) -> None:
    """Output/input picker (Big Sur: the list under the Sound module)."""
    def fill(devs):
        Item = ui.menu.Item
        items = [Item(d.name, lambda _on=None, d=d: system.run_async(set_fn, None, d.key), checked=d.default)
                 for d in devs or []]
        ui.menu.popup(btn, [[Item(title, None, enabled=False)], items or [Item("No devices", None, enabled=False)]],
                      position=Gtk.PositionType.BOTTOM, gap=4, glass=True)
    system.run_async(list_fn, fill)


_art_cache = {}                 # (url, size) -> texture, newest last; small and few (ART_KEEP)
ART_KEEP = 8


def _load_art(url: str, image: Gtk.Image, size: int) -> None:
    """Album art (mpris:artUrl: file:// or http[s]://) into `image`."""
    if not url:
        image.set_from_icon_name("sonata-now-playing-symbolic")
        image.set_pixel_size(size // 2)             # a small note in the empty square
        return
    key = (url, size)
    if key in _art_cache:
        _art_cache[key] = _art_cache.pop(key)      # most recently used last
        image.set_pixel_size(size)
        image.set_from_paintable(_art_cache[key])
        return
    px = size * 2                                   # sharp on HiDPI, not a full-size cover in memory

    def work():
        try:
            if url.startswith("file://"):
                with open(GLib.filename_from_uri(url)[0], "rb") as f:
                    data = f.read(16_000_000)
            else:
                import urllib.request
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = r.read(4_000_000)
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
            loader = GdkPixbuf.PixbufLoader()
            loader.connect("size-prepared", lambda ld, w, h: ld.set_size(
                *((px, max(1, round(h * px / w))) if w >= h else (max(1, round(w * px / h)), px)))
                if max(w, h) > px else None)
            loader.write(data)
            loader.close()
            return loader.get_pixbuf()
        except (OSError, ValueError, GLib.Error, ZeroDivisionError):
            return None

    def done(pb):
        if pb is None:
            return
        tex = Gdk.Texture.new_for_pixbuf(pb)
        _art_cache[key] = tex
        while len(_art_cache) > ART_KEEP:
            _art_cache.pop(next(iter(_art_cache)))
        image.set_pixel_size(size)
        image.set_from_paintable(tex)
    system.run_async(work, done)


def now_playing_module(p, header=False) -> Gtk.Widget:
    """Big Sur Now Playing: artwork, title, artist, previous / play-pause /
    next; follows the player live and says "Not Playing" when there is none."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    if header:
        box.append(Gtk.Label(label="Now Playing", xalign=0, css_classes=["panel-module-title"]))
    row = Gtk.Box(spacing=10, css_classes=["cc-np"])
    art_box = Gtk.Box(css_classes=["cc-np-art"], valign=Gtk.Align.CENTER, halign=Gtk.Align.START,
                      overflow=Gtk.Overflow.HIDDEN, hexpand=False)
    art_box.set_size_request(36, 36)               # one Control Center row (controlcenter.UNIT_H)
    art = Gtk.Image(pixel_size=36, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
    art_box.append(art)
    row.append(art_box)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    # asks for no width of its own (max_width_chars=1): a long title is cut
    # with "…" and never widens Control Center
    title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, hexpand=True,
                      css_classes=["cc-np-title"])
    artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1, hexpand=True,
                      css_classes=["cc-np-artist"])
    texts.append(title)
    texts.append(artist)
    row.append(texts)
    btns = {}
    # previous / play-pause / next (macOS shows no "previous" here; Vini wants it)
    for key, icon, method in (("prev", "media-skip-backward-symbolic", "Previous"),
                              ("play", "media-playback-start-symbolic", "PlayPause"),
                              ("next", "media-skip-forward-symbolic", "Next")):
        b = Gtk.Button(icon_name=icon, valign=Gtk.Align.CENTER)
        b.connect("clicked", lambda _b, m=method: p.call(m))
        btns[key] = b
        row.append(b)
    state = {"art": None}

    def update():
        title.set_label(p.title if p.active else "Not Playing")
        artist.set_label(p.artist if p.active else "")
        artist.set_visible(bool(p.active and p.artist))
        btns["play"].set_icon_name("media-playback-pause-symbolic" if p.playing else "media-playback-start-symbolic")
        for b in btns.values():
            b.set_sensitive(p.active)
        url = p.art if p.active else ""
        if url != state["art"]:
            state["art"] = url
            _load_art(url, art, 36)
    update()
    p.listeners.append(update)
    row.connect("unrealize", lambda *_: update in p.listeners and p.listeners.remove(update))
    box.append(row)
    return ui.panel.module(box)


class ControlCenter(Gtk.Box):
    """Control Center: modules on a 4-column grid (controlcenter.py) -- the
    connectivity module (Wi-Fi, Bluetooth), Do Not Disturb, Dark Mode,
    Screenshot, Display and Sound sliders, Now Playing. Which ones, their
    order: the user's (Edit Controls…, like Launchpad). Opens at once;
    states fill in from background reads."""

    def __init__(self, bar: Bar):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=320)
        from . import controlcenter as CCL
        self.bar = bar
        self.wifi = ui.panel.toggle("network-wireless-symbolic", "Wi-Fi", False,
                                    lambda on: system.run_async(system.set_wifi_enabled, lambda _r: bar._poll(), on),
                                    caption="…")
        self.bt = ui.panel.toggle("bluetooth-active-symbolic", "Bluetooth", False,
                                  lambda on: system.run_async(system.set_bluetooth, None, on), caption="…")
        # where macOS has AirDrop (Vini): Airplane Mode -- every radio off, and back as they were
        self.plane = ui.panel.toggle("airplane-mode-symbolic", "Airplane Mode", False, self._set_airplane, caption="…")
        conn = ui.panel.module(self.wifi, self.bt, self.plane, spacing=2)
        conn.add_css_class("cc-conn")                           # three rows in its 2x2 cells
        conn.set_valign(Gtk.Align.FILL)
        dark = Adw.StyleManager.get_default().get_dark()
        nc = getattr(bar, "notifications", None)
        dnd = ui.panel.module(ui.panel.toggle("weather-clear-night-symbolic", "Do Not Disturb",
                                              bool(nc and nc.dnd), lambda on: nc and nc.set_dnd(on)))
        self.dark_btn = self._small("sonata-dark-mode-symbolic", "Dark Mode",
                                    lambda: self._set_dark(not Adw.StyleManager.get_default().get_dark()),
                                    close=False)
        (self.dark_btn.add_css_class if dark else self.dark_btn.remove_css_class)("on")
        shot = self._small("sonata-screenshot-symbolic", "Screenshot", self._screenshot)   # opens the toolbar
        # the brightness of the display this menu bar is on: the laptop panel,
        # or an external monitor over DDC/CI
        self.output = self._output()
        out = self.output
        disp, self.bright = _slider_with_icon(
            "display-brightness-symbolic", 50,
            lambda v: system.run_latest(("brightness", out), system.set_brightness, int(v), out),
            button=_round_button("video-display-symbolic", "Displays Preferences",
                                 lambda _b: (self._close(), open_settings("displays"))))
        # Night Shift under the brightness slider (Big Sur's expanded Display module)
        from . import nightshift
        ns = Gtk.ToggleButton(css_classes=["cc-ns"], can_focus=False, active=nightshift.is_on(),
                              halign=Gtk.Align.START)
        ns_box = Gtk.Box(spacing=8)
        ns_box.append(Gtk.Image(icon_name="night-light-symbolic", css_classes=["cc-ns-icon"]))
        ns_box.append(Gtk.Label(label="Night Shift"))
        ns.set_child(ns_box)
        ns.connect("toggled", lambda b: nightshift.set_manual(b.get_active()))
        ns.set_sensitive(shutil.which("wlsunset") is not None)
        display = ui.panel.module(Gtk.Label(label="Display", xalign=0, css_classes=["panel-module-title"]),
                                  disp, ns)
        snd, self.vol = _slider_with_icon(
            _speaker_icon, 50, bar._set_volume,
            button=_round_button("sonata-audio-output-symbolic", "Output",
                                 lambda b: _device_menu(b, "Output", system.audio_outputs, system.select_output)))
        _volume_feedback(self.vol)
        mic, self.mic = _slider_with_icon(
            lambda v: "microphone-disabled-symbolic" if v <= 0 else "audio-input-microphone-symbolic", 50,
            lambda v: system.run_async(system.set_input_volume, None, int(v), False),
            button=_round_button("audio-input-microphone-symbolic", "Input",
                                 lambda b: _device_menu(b, "Input", system.audio_inputs, system.select_input)))
        sound = ui.panel.module(Gtk.Label(label="Sound", xalign=0, css_classes=["panel-module-title"]), snd, mic)
        from . import mpris
        self.np = None
        self.modules = {"connectivity": conn, "dnd": dnd, "darkmode": self.dark_btn, "screenshot": shot,
                        "display": display, "sound": sound,
                        "nowplaying": now_playing_module(mpris.players())}   # always, like Big Sur ("Not Playing")
        mixer_mod = self._mixer_module()                       # each app's volume (Add Controls)
        if mixer_mod is not None:
            self.modules["mixer"] = mixer_mod
        from . import statsui                                   # performance (Add Controls; read only when shown)
        for kind in statsui.KINDS:
            self.modules["stat_" + kind] = statsui.module(kind)
        mon = self._monitor()
        self.width = CCL.width_for(mon.get_geometry().width if mon is not None else 0)   # by display size
        self.grid = CCL.ModuleGrid(self.modules, CCL.load(),
                                   on_change=lambda _o: (self._edit_bar_update(), self._fit_height()),
                                   width=self.width)
        # fixed width (EXTERNAL keeps a too-wide child from widening it, and shows no bar);
        # the height follows the modules, and scrolls only past what the screen holds
        self.scroller = Gtk.ScrolledWindow(child=self.grid, hscrollbar_policy=Gtk.PolicyType.EXTERNAL,
                                           vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, overlay_scrolling=True,
                                           propagate_natural_width=False, propagate_natural_height=True,
                                           max_content_height=self._max_height(), width_request=self.width,
                                           css_classes=["cc-scroller"])
        self.append(self.scroller)
        self._fit_height()
        self.append(self._edit_bar())
        cached("cc", _cc_state, self._fill_toggles)             # Wi-Fi / Bluetooth: last known at once
        system.run_async(lambda: (system.brightness(out), system.volume(), system.input_volume()),
                         self._fill_sliders)                  # levels: always the live ones

    def _fit_height(self) -> None:
        """As tall as the modules (never squeezed by the panel), up to the screen."""
        if hasattr(self, "scroller"):
            h = self.grid.measure(Gtk.Orientation.VERTICAL, -1)[1]
            self.scroller.set_min_content_height(min(h, self.scroller.get_max_content_height()))

    def _monitor(self):
        """The display this menu bar is on (else the first one)."""
        mon = getattr(self.bar, "monitor", None)
        if mon is None:
            try:
                disp = Gdk.Display.get_default()
                mons = disp.get_monitors() if disp else None
                mon = mons.get_item(0) if mons and mons.get_n_items() else None
            except Exception:
                mon = None
        return mon

    def _max_height(self) -> int:
        """The tallest the modules area may get: the display's height under the
        menu bar, less room for the edit bar and the panel's margins."""
        mon = self._monitor()
        h = mon.get_geometry().height if mon is not None else 900
        return max(300, h - BAR_H - 120)

    def _mixer_module(self):
        """The Sound menu's per-app volumes as a module: live while shown
        (the menu bar's mixer service), its list scrolling inside the module."""
        from ..backend import mixer
        if not mixer.available():
            return None
        from .mixer_ui import AppMixer
        apps_box = AppMixer()
        from . import controlcenter as CCL
        scroller = Gtk.ScrolledWindow(child=apps_box, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vexpand=True,
                                      propagate_natural_height=False)
        title = Gtk.Label(label="Volume Mixer", xalign=0, css_classes=["panel-module-title"])
        box = ui.panel.module(title, scroller, spacing=4)
        box.add_css_class("cc-mixer")
        self.app_mixer = apps_box                               # (tests)
        service = getattr(self.bar, "mixer", None)

        def fit():
            """As many grid rows as the apps playing need (Vini), up to 6; past
            that the list scrolls inside. Rows sliding out don't count."""
            V = Gtk.Orientation.VERTICAL
            rows = list(apps_box.rows.values())
            inner = (sum(r.get_child().measure(V, -1)[1] for r in rows) if rows
                     else apps_box.empty.measure(V, -1)[1])
            chrome = box.measure(V, -1)[1] - scroller.measure(V, -1)[1]
            if getattr(self, "grid", None) is not None:
                self.grid.set_rows("mixer", CCL.rows_for(chrome + inner))

        def streams(found):
            apps_box.set_streams(found)
            fit()

        def mapped(*_a):
            system.run_async(mixer.streams, streams)
            if service is not None and streams not in service.listeners:
                service.listeners.append(streams)

        def unmapped(*_a):
            if service is not None and streams in service.listeners:
                service.listeners.remove(streams)
        box._streams = streams                                  # (tests)
        box.connect("map", mapped)
        box.connect("unmap", unmapped)
        return box

    # -- Edit Controls (like Launchpad's jiggle mode) -----------------------------------------
    def _edit_bar(self) -> Gtk.Widget:
        from . import controlcenter as CCL
        self.edit_btn = ui.controls.push_button("Edit Controls…", lambda: self.grid.set_editing(True))
        self.add_btn = ui.controls.push_button("Add Controls", lambda: self._add_menu(self.add_btn))
        self.done_btn = ui.controls.push_button("Done", lambda: self.grid.set_editing(False), style="default")
        self._CCL = CCL
        bar = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER, css_classes=["cc-edit-bar"])
        for b in (self.edit_btn, self.add_btn, self.done_btn):
            bar.append(b)
        self._edit_bar_update()
        return bar

    def _edit_bar_update(self) -> None:
        if not hasattr(self, "done_btn"):
            return
        on = self.grid.editing
        self.edit_btn.set_visible(not on)
        self.done_btn.set_visible(on)
        self.add_btn.set_visible(on)
        self.add_btn.set_sensitive(any(m in self.modules for m in self._CCL.hidden(self.grid.order)))

    def _add_menu(self, btn) -> None:
        """What was taken out, to put back (at the end)."""
        Item = ui.menu.Item
        CCL = self._CCL
        items = [Item(CCL.CATALOG[m][0], lambda m=m: (self.grid.add_module(m), self._edit_bar_update()))
                 for m in CCL.hidden(self.grid.order) if m in self.modules]
        if items:
            ui.menu.popup(btn, [items], position=Gtk.PositionType.TOP)

    def _output(self):
        """Connector name of the display this menu bar is on (None: unknown,
        the laptop panel then)."""
        try:
            mon = getattr(self.bar, "monitor", None)
            if mon is None:
                nat = self.bar.get_native()
                surf = nat.get_surface() if nat else None
                mon = surf.get_display().get_monitor_at_surface(surf) if surf else None
            return mon.get_connector() if mon is not None else None
        except Exception:
            return None

    def _small(self, icon, title, cb, close=True):
        b = Gtk.Button(css_classes=["panel-module", "cc-small"], can_focus=False)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        col.append(Gtk.Image(icon_name=icon, pixel_size=18))
        col.append(Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER))
        b.set_child(col)
        b.connect("clicked", lambda *_: ((self._close() if close else None), cb()))
        return b

    def _close(self):
        pop = self.get_ancestor(Gtk.Popover)
        if pop:
            pop.popdown()

    def _fill_toggles(self, res):
        if not res:
            return
        wifi_on, (ssid, _sig, _wired), bt = res[:3]
        plane = res[3] if len(res) > 3 else None
        ui.panel.set_toggle(self.wifi, bool(wifi_on), ssid or ("Not Connected" if wifi_on else "Off"))
        ui.panel.set_toggle(self.bt, bool(bt), "On" if bt else "Off" if bt is not None else "Unavailable")
        ui.panel.set_toggle(self.plane, bool(plane), "On" if plane else "Off" if plane is not None else "Unavailable")
        self.plane.button.set_sensitive(plane is not None)

    def _set_airplane(self, on: bool) -> None:
        """Every radio off (or back as it was); Wi-Fi and Bluetooth show it."""
        ui.panel.set_toggle(self.plane, on, "On" if on else "Off")
        if on:                                   # they go off with it, at once
            ui.panel.set_toggle(self.wifi, False, "Off")
            ui.panel.set_toggle(self.bt, False, "Off")

        def done(_ok):
            self.bar._poll()
            system.run_async(_cc_state, self._fill_toggles)
        system.run_async(system.set_airplane_mode, done, on)

    def _fill_sliders(self, res):
        if not res:
            return
        b, vol, mic = res
        if b is None:
            self.bright.set_sensitive(False)
            if not system.is_builtin(self.output):
                self.bright.set_tooltip_text("This display's brightness can't be changed from the computer "
                                             "(turn on DDC/CI in its menu; needs ddcutil)")
        else:
            self.bright.set_value(b)
        if vol is None:
            self.vol.set_sensitive(False)
        else:
            self.vol.set_value(vol[0])
        if mic is None:
            self.mic.set_sensitive(False)
        else:
            self.mic.set_value(mic[0])

    def _screenshot(self):
        """The capture toolbar (Super+Shift+5), after the panel closes."""
        cap = getattr(self.bar, "capture", None)
        if cap:
            GLib.timeout_add(300, lambda: (cap.show_toolbar(), False)[1])

    def _set_dark(self, on: bool) -> None:
        """Dark Mode is a Linux setting (freedesktop colour-scheme), so every
        app follows -- and Sonata with them."""
        if hasattr(self, "dark_btn"):
            (self.dark_btn.add_css_class if on else self.dark_btn.remove_css_class)("on")
        system.run_async(system.set_dark_mode, None, on)


class AboutWindow(Adw.Window):
    """About This Computer (Big Sur layout: logo left, OS name, specs).
    Resizable; narrow, the logo goes above the text (breakpoint)."""

    def __init__(self):
        super().__init__(title="About This Computer", default_width=720, default_height=300)
        for c in ("sonata-about", "sonata-glass-window"):       # frosted glass, like the Dock
            self.add_css_class(c)
        ui.window.standard(self)
        self.set_size_request(320, 260)
        head = ui.window.titlebar(self, zoom=True)
        body = Gtk.Box(spacing=36, css_classes=["about-box"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                       vexpand=True)
        logo = Gtk.Image(pixel_size=120, valign=Gtk.Align.CENTER)
        from .. import icons
        icons.set_logo(logo)                      # the distro's logo (os-release LOGO=)
        body.append(logo)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
        self.info = info
        body.append(info)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(head)
        col.append(_scroller(Gtk.WindowHandle(child=body)))   # drag from anywhere, like macOS
        self.set_content(col)
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 560sp"))
        bp.add_setter(body, "orientation", Gtk.Orientation.VERTICAL)
        bp.add_setter(body, "spacing", 16)
        bp.add_setter(logo, "pixel-size", 80)
        bp.add_setter(logo, "halign", Gtk.Align.CENTER)
        bp.add_setter(info, "halign", Gtk.Align.CENTER)
        self.add_breakpoint(bp)
        system.run_async(system.about, self._fill)

    def _fill(self, a) -> None:
        if not a:
            return
        name, _, version = a.os_name.partition(" ")
        self.info.append(Gtk.Label(label=name, xalign=0, css_classes=["about-name"]))
        self.info.append(Gtk.Label(label=version or f"Kernel {a.kernel}", xalign=0, css_classes=["about-version"]))
        for key, value in (("Computer", a.machine), ("Processor", a.cpu),
                           ("Memory", f"{a.memory_gb} GB"), ("Graphics", ", ".join(a.gpus) or "—"),
                           ("Kernel", a.kernel), ("Desktop", "Sonata 2")):
            row = Gtk.Box(spacing=6)
            row.append(Gtk.Label(label=key, css_classes=["about-key"], valign=Gtk.Align.START))
            # wraps instead of cutting: the window can be narrow or wide
            row.append(Gtk.Label(label=value, xalign=0, wrap=True, natural_wrap_mode=Gtk.NaturalWrapMode.NONE,
                                 max_width_chars=60, hexpand=True))
            self.info.append(row)


def _scroller(child) -> Gtk.ScrolledWindow:
    """Lets a small window shrink below its content (scrolls instead)."""
    return Gtk.ScrolledWindow(child=child, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                              propagate_natural_height=True, propagate_natural_width=True)


class AboutAppWindow(Adw.Window):
    def __init__(self, info, name):
        super().__init__(title=f"About {name}", default_width=360)
        for c in ("sonata-about", "sonata-glass-window"):       # frosted glass, like the Dock
            self.add_css_class(c)
        ui.window.standard(self)
        self.set_size_request(260, 220)
        head = ui.window.titlebar(self, zoom=True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["about-box"],
                      valign=Gtk.Align.CENTER, vexpand=True)
        img = Gtk.Image(pixel_size=96)
        if info:
            from .. import icons
            icons.set_image(img, icons.app_icon(info))
        col.append(img)
        col.append(Gtk.Label(label=name, css_classes=["about-name"], wrap=True, justify=Gtk.Justification.CENTER))
        if info:
            for text in (info.get_description(), info.get_generic_name()):
                if text:
                    col.append(Gtk.Label(label=text, css_classes=["about-version"], wrap=True, max_width_chars=40,
                                         justify=Gtk.Justification.CENTER))
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(head)
        outer.append(_scroller(col))
        self.set_content(outer)


def set_titlebar_colors(dark: bool) -> None:
    from ..titlebars import apply_colors
    apply_colors(dark)


def _listen_for_lock() -> None:
    """logind "Lock" (loginctl lock-session, the Control Center, idle
    tools) starts Sonata's lock screen; the menu bar always runs, so it
    listens for the session."""
    def lock(*_a):
        from ..__main__ import self_argv
        try:
            GLib.spawn_async(self_argv() + ["lock"], flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error:
            pass
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        sid = os.environ.get("XDG_SESSION_ID")
        method, arg = ("GetSession", GLib.Variant("(s)", (sid,))) if sid else \
            ("GetSessionByPID", GLib.Variant("(u)", (os.getpid(),)))
        path = bus.call_sync("org.freedesktop.login1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                             method, arg, None, Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
        bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Session", "Lock", path, None,
                             Gio.DBusSignalFlags.NONE, lock)
    except GLib.Error:
        pass                      # no logind: Ctrl+Super+Q still runs `sonata2 lock` directly


def _open_recent(uri: str) -> None:
    """A recent item: folders in Sonata's Files, files in their app."""
    from ..files import open_folder
    f = Gio.File.new_for_uri(uri)
    if f.query_file_type(Gio.FileQueryInfoFlags.NONE, None) == Gio.FileType.DIRECTORY:
        open_folder(uri)
        return
    try:
        Gio.AppInfo.launch_default_for_uri(uri, None)
    except GLib.Error:
        pass


class TopBarWindow(Gtk.ApplicationWindow):
    """The menu bar of one display. The main display's (secondary=False)
    also runs the menu bar's services (notifications, Night Shift, lock...);
    other displays get the same bar without them (macOS: a menu bar on
    every display)."""

    def __init__(self, app, preview: bool = False, monitor=None, manager=None, secondary: bool = False):
        super().__init__(application=app, title="Menu Bar", css_classes=["sonata-topbar"], decorated=False,
                         resizable=True)
        if manager is None:
            from ..wl.toplevels import ToplevelManager
            manager = ToplevelManager(Gdk.Display.get_default(),
                                      ignore_app_ids={"io.github.vinioliveiras.sonata2.topbar"})
        self.manager = manager
        self.bar = Bar(self.manager)
        self.bar.monitor = monitor          # Control Center's brightness follows this display
        self.set_size_request(-1, BAR_H)
        if not preview and not secondary:
            from .notifications import Notifications
            self.bar.notifications = Notifications(app)
            GLib.timeout_add_seconds(4, lambda: (prefetch(), False)[1])     # menus open already filled
            _listen_for_lock()
            from .nightshift import NightShift
            self.bar.nightshift = NightShift()
            from .idlelock import IdleLock
            self.bar.idlelock = IdleLock()
            try:                                                    # macOS' rounded screen corners
                from .screencorners import ScreenCorners
                self.bar.screen_corners = ScreenCorners(app)
            except Exception as e:                                  # never keeps the menu bar from starting
                print(f"sonata2-topbar: screen corners: {e}")
            try:                                                    # Clock's alarms ring from here
                from .alarmservice import AlarmService
                self.bar.alarms = AlarmService(app)
                app.connect("shutdown", lambda *_: self.bar.alarms.ringer.stop())
            except Exception as e:                                  # never keeps the menu bar from starting
                print(f"sonata2-topbar: alarms: {e}")
            try:                                                    # a new Sonata release: say so
                from .updatenotify import UpdateNotifier
                self.bar.update_notifier = UpdateNotifier()
            except Exception as e:                                  # never keeps the menu bar from starting
                print(f"sonata2-topbar: update check: {e}")
            try:                                                    # each app's saved volume
                from ..backend.mixer import MixerService
                self.bar.mixer = MixerService()
                app.connect("shutdown", lambda *_: self.bar.mixer.stop())
            except Exception as e:                                  # never keeps the menu bar from starting
                print(f"sonata2-topbar: mixer: {e}")
            from .mission import MissionBackdrop
            self.bar.mission = MissionBackdrop(app)
            from ..trash_cleanup import Housekeeping
            self.bar.housekeeping = Housekeeping()              # old Trash items (when that's on)
            from ..gamepad.service import Gamepads                  # controllers drive the desktop
            self.bar.gamepads = Gamepads(app, lambda: getattr(self.bar, "switcher_win", None))
            app.connect("shutdown", lambda *_: self.bar.gamepads.stop())
            from .. import fullscreen                               # Ctrl+Super+F, remembered; games
            self.bar.fullscreen = fullscreen.Rules()
            from .. import gpu                                      # crashed on the discrete GPU last time
            # tried until the notification server answers (a few times, at login)
            _tries = {"n": 0}

            def _gpu_notice():
                _tries["n"] += 1
                told = gpu.fallback_notice()                    # started on the other card (last resort)
                return (not told or (not gpu.crash_notice() and gpu.crashed_at() is not None)) and _tries["n"] < 6
            GLib.timeout_add_seconds(4, _gpu_notice)
            from ..vram import VramWatch                          # a full NVIDIA card: who, and a warning
            self.bar.vram = VramWatch(notify=gpu.notify)
            light = self.bar.fullscreen_first.light                # lighter effects while a game fills it
            self.bar.vram.listeners.append(lambda used, total: light.update(used=used, total=total))
            self.bar.vram.start()
            from ..backend.screenshare import ScreenSharing             # Settings > Sharing > Screen Sharing
            self.bar.screenshare = ScreenSharing()
            self.bar.screenshare.start()
            app.connect("shutdown", lambda *_: self.bar.screenshare.stop())
            from ..backend.equalizer import Equalizer
            self.bar.equalizer = Equalizer()
            self.bar.equalizer.start()
            app.connect("shutdown", lambda *_: self.bar.equalizer.stop())
        if not preview and not secondary:
            # Title bars Wayfire draws (terminals, X11 apps) follow Dark Mode
            # live too: the menu bar always runs, so it keeps them in sync.
            sm = Adw.StyleManager.get_default()
            sm.connect("notify::dark", lambda m, _p: system.run_async(set_titlebar_colors, None, m.get_dark()))
            system.run_async(set_titlebar_colors, None, sm.get_dark())
        if preview:
            self._preview()
        else:
            self.set_child(self.bar)
            if layer.layer_shell():
                LS = layer.layer_shell()
                LS.init_for_window(self)
                LS.set_namespace(self, "sonata2-topbar")
                if monitor is not None:
                    LS.set_monitor(self, monitor)
                LS.set_layer(self, LS.Layer.TOP)
                for e in (LS.Edge.TOP, LS.Edge.LEFT, LS.Edge.RIGHT):
                    LS.set_anchor(self, e, True)
                LS.set_exclusive_zone(self, BAR_H)
                LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND)
                self._init_autohide()
                from . import intro
                if self._hidden:                    # auto-hide: it starts out of sight
                    pass
                elif intro.pending():               # login: slides down once the welcome screen fades
                    # (the bar's drawing moves, not the surface: a surface
                    # moved off-screen gets no frames and would never come back)
                    self._intro_offset = float(BAR_H)
                    intro.wait(self._slide_in)

    def do_snapshot(self, snap) -> None:
        off = getattr(self, "_intro_offset", 0.0)
        if off <= 0:
            Gtk.ApplicationWindow.do_snapshot(self, snap)
            return
        snap.save()
        snap.translate(Graphene.Point().init(0, -off))
        Gtk.ApplicationWindow.do_snapshot(self, snap)
        snap.restore()

    # -- auto-hide (Settings > Menu Bar), the Dock's way ---------------------------------
    def _init_autohide(self) -> None:
        self._hidden = False
        self._inside = False
        self._hide_timer = 0
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_a: self._pointer(True))
        motion.connect("leave", lambda *_a: self._pointer(False))
        self.add_controller(motion)
        ui.menu.on_closed.append(lambda: self._pointer(self._inside))
        self.bar.on_autohide = self._apply_autohide
        self.bar.reveal = lambda: self._hidden and self._slide(False)
        if self.bar.cfg.get("autohide"):
            self._hidden = True
            self._intro_offset = float(BAR_H)
            layer.set_exclusive(self, 0)
        self.connect("realize", lambda *_a: self._update_input())
        self.connect("notify::default-width", lambda *_a: self._update_input())

    def _apply_autohide(self) -> None:
        """topbar.json changed: windows get the top of the screen while the bar hides."""
        on = bool(self.bar.cfg.get("autohide"))
        layer.set_exclusive(self, 0 if on else BAR_H)
        if not on and self._hidden:
            self._slide(False)
        else:
            self._pointer(self._inside)

    def _pointer(self, inside: bool) -> None:
        self._inside = inside
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
            self._hide_timer = 0
        if not self.bar.cfg.get("autohide"):
            return
        if inside and self._hidden:
            self._hide_timer = GLib.timeout_add(REVEAL_MS, self._timed, False)
        elif not inside and not self._hidden and not ui.menu.OPEN:      # a menu open: it stays
            self._hide_timer = GLib.timeout_add(400, self._timed, True)

    def _timed(self, hide: bool) -> bool:
        self._hide_timer = 0
        self._slide(hide)
        return False

    def _slide(self, hide: bool) -> None:
        self._hidden = hide

        def step(v):
            self._intro_offset = v
            self.queue_draw()
        ui.transition.tween(self, "hide", getattr(self, "_intro_offset", 0.0), float(BAR_H) if hide else 0.0,
                            HIDE_MS, step, "menu bar " + ("hide" if hide else "show"))
        self._update_input()

    def _update_input(self) -> None:
        """Hidden: only a thin strip at the top edge takes the pointer; the
        clicks under it go to the windows."""
        if not layer.layer_shell() or not self.get_surface():
            return
        w = self.get_width() or 10000
        layer.set_input_region(self, [(0, 0, w, layer.EDGE_TRIGGER if self._hidden else BAR_H)])

    def _slide_in(self, ms: int = 420) -> None:
        import time
        start = time.monotonic()

        def step():
            t = min(1.0, (time.monotonic() - start) * 1000 / ms)
            self._intro_offset = BAR_H * (1 - (1 - (1 - t) ** 3))
            self.queue_draw()
            return t < 1
        GLib.timeout_add(16, step)

    def _preview(self) -> None:
        """Bar over a sample wallpaper in a normal window (screenshots)."""
        GLib.timeout_add(300, lambda: (self.bar._sound_state((70, False)), self.bar._battery_state((64, "Discharging", False, None)),
                                       self.bar._wifi_state((True, True, ("Home", 80, False))),
                                       self.bar.bt.set_visible(True), False)[-1])
        from .preview import _wallpaper
        w, h = (int(v) for v in os.environ.get("PREVIEW_SIZE", "1280x400").split("x"))
        self.set_default_size(w, h)
        walls = _wallpaper(w, h, ui.is_dark())
        over = Gtk.Overlay()
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, hexpand=True, vexpand=True)
        if walls:
            pic.set_filename(walls[0])
            self.bar.backdrop = Gdk.Texture.new_from_filename(walls[1])
        over.set_child(pic)
        self.bar.set_valign(Gtk.Align.START)
        over.add_overlay(self.bar)
        self.set_child(over)
