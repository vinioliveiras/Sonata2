"""The menu bar (macOS Big Sur): 24 px translucent bar at the top.

Left:  Sonata menu (logo) -- About This Computer, Recent Items, Sleep,
       Restart..., Shut Down..., Lock Screen, Log Out...
       active app name (bold) -- About, Hide, Hide Others, Show All, Quit
       Window -- Minimize, Zoom, the app's windows, Bring All to Front
       (both hidden while the desktop has the focus -- Vini's choice)
Right: menu extras -- Sound, Battery, Wi-Fi, Control Center, clock; own
       icons (sonata-volume/-wifi/-battery-*, tools/gen-status-icons.py).

Apps' own menus (File, Edit, ...) need a global-menu protocol GTK4/Qt6 apps
don't export on Wayland; the bar offers what wlr-foreign-toplevel allows.
Menus hang from the title's left edge like macOS. Linux state comes from
backend/system.py (async)."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from .. import apps, config, ui  # noqa: E402
from ..backend import system  # noqa: E402
from . import layer  # noqa: E402

BAR_H = 24
DEFAULTS = {"battery_percent": False, "clock_format": "%a %-d %b  %H:%M"}
POLL_S = 10

ui.register("""
window.sonata-topbar, window.sonata-topbar > contents { background: none; box-shadow: none; }
.topbar { min-height: %(bar_h)dpx; padding: 0 8px; font-family: %(font)s; font-size: %(text_body)s;
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
.about-box { padding: 28px 36px 24px 36px; font-family: %(font)s; color: %(label)s; }
.about-name { font-family: %(font_display)s; font-size: 26px; font-weight: 700; }
.about-version { color: %(label_secondary)s; margin-bottom: 14px; }
.about-key { font-weight: 700; }
window.sonata-about { background: %(window_bg)s; }
""", key="topbar", bar_h=BAR_H, item_h=BAR_H - 2)


class Bar(Gtk.CenterBox):
    """The bar's content; painted with the bar material in do_snapshot."""

    def __init__(self, manager=None):
        super().__init__(css_classes=["topbar"], hexpand=True)
        self.cfg = config.load("topbar", DEFAULTS)
        self.manager = manager if manager and manager.available else None
        self.backdrop = None
        self.items = []
        left = Gtk.Box()
        self.logo = self._item(left, icon="sonata-logo-symbolic", on_click=self._sonata_menu, css="icon")
        self.app_btn = self._item(left, text=self._fallback_app_name(), on_click=self._app_menu, css="app")
        self.win_btn = self._item(left, text="Window", on_click=self._window_menu)
        self.set_start_widget(left)

        right = Gtk.Box()
        # Now Playing (while a player runs), clipboard history (wl-clipboard),
        # input source (with 2+ keyboard layouts)
        from . import clipboard, mpris
        self.players = mpris.players()
        self.nowplaying = self._item(right, icon="sonata-now-playing-symbolic", on_click=self._nowplaying_panel,
                                     css="icon")
        self.players.listeners.append(lambda: self.nowplaying.set_visible(self.players.active))
        self.nowplaying.set_visible(self.players.active)
        self.clip = clipboard.History()
        self.clip_btn = self._item(right, icon="sonata-clipboard-symbolic", on_click=self._clipboard_panel,
                                   css="icon")
        self.clip_btn.set_visible(self.clip.available)
        self.input_btn = self._item(right, text="", on_click=self._input_panel)
        self.input_btn.add_css_class("input-src")
        self._update_input()
        self.sound = self._item(right, icon="sonata-volume-3-symbolic", on_click=self._sound_panel, css="icon")
        self.battery = self._item(right, icon="sonata-battery-100-symbolic", on_click=self._battery_panel,
                                  css="icon")
        self.battery.add_css_class("battery")
        self.battery_pct = Gtk.Label(css_classes=["percent"])
        self.battery.get_child().prepend(self.battery_pct)          # "87% [battery]" like Big Sur
        self.wifi = self._item(right, icon="sonata-wifi-3-symbolic",
                               on_click=self._wifi_panel, css="icon")
        self.spotlight = self._item(right, icon="sonata-search-symbolic", on_click=self._spotlight, css="icon")
        self.cc = self._item(right, icon="sonata-control-center-symbolic", on_click=self._control_center,
                             css="icon")
        self.clock = self._item(right, text="", on_click=self._calendar)
        self.set_end_widget(right)

        ui.on_change(self.queue_draw)
        self._cfg_mon = config.watch("topbar", self._config_changed)
        if self.manager:
            self.manager.listeners.append(self._active_changed)
        self._tick_clock()
        self._active_changed()
        self._poll()
        GLib.timeout_add_seconds(POLL_S, lambda: (self._poll(), True)[1])

    def _config_changed(self) -> None:
        """Settings app changed topbar.json: apply live."""
        self.cfg = config.load("topbar", DEFAULTS)
        self.battery_pct.set_visible(self.cfg["battery_percent"] and self.battery.get_visible())
        now = GLib.DateTime.new_now_local()
        self._set_text(self.clock, now.format(self.cfg["clock_format"]) or now.format("%a %H:%M"))

    # -- drawing -------------------------------------------------------------------
    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        if w > 0 and h > 0:
            rect = Graphene.Rect()
            rect.init(0, 0, w, h)
            if self.backdrop:        # preview: stands in for the compositor's blur
                snap.append_texture(self.backdrop, Graphene.Rect().init(0, 0, self.backdrop.get_width(),
                                                                        self.backdrop.get_height()))
            # Big Sur: translucent material, no bottom line
            snap.append_color(ui.rgba("window_bg" if ui.theme.reduce_transparency() else "bar_bg"), rect)
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

    def _set_text(self, btn, text) -> None:
        lbl = btn.get_child().get_last_child()
        if isinstance(lbl, Gtk.Label):
            lbl.set_label(text)

    def _set_icon(self, btn, name) -> None:
        img = btn.get_child().get_first_child()
        if isinstance(img, Gtk.Image):
            img.set_from_icon_name(name)

    def _open(self, btn, builder) -> None:
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
        pop = ui.menu.popup(btn, sections, position=Gtk.PositionType.BOTTOM, gap=2)
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
                docs.append(Item(r.get_display_name(), lambda u=r.get_uri():
                                 Gio.AppInfo.launch_default_for_uri(u, None)))
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
                        lambda r: system.power_action(kind) if r == kind else None)

    # -- active app ------------------------------------------------------------------
    def _fallback_app_name(self) -> str:
        """No active window: the file manager (macOS shows Finder)."""
        pins = config.load("dock", {"pinned": []}).get("pinned") or []
        info = apps.lookup(pins[0]) if pins else None
        return info.get_display_name() if info else "Files"

    def _active(self):
        if not self.manager:
            return None, []
        act = next((t for t in self.manager.toplevels if t.activated), None)
        if not act:
            return None, []
        key = apps.match_app_id(act.app_id) or act.app_id
        wins = [t for t in self.manager.toplevels if (apps.match_app_id(t.app_id) or t.app_id) == key]
        return key, wins

    def _active_changed(self) -> None:
        """App name + Window menu follow the focused app; on the desktop
        (nothing focused) both are hidden."""
        key, _wins = self._active()
        self.app_btn.set_visible(bool(key))
        self.win_btn.set_visible(bool(key))
        if key:
            info = apps.lookup(key)
            self._set_text(self.app_btn, info.get_display_name() if info else key)

    def _app_menu(self, btn):
        Item = ui.menu.Item
        key, wins = self._active()
        info = apps.lookup(key) if key else None
        name = info.get_display_name() if info else (key or self._fallback_app_name())
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
        GLib.timeout_add_seconds(max(1, 60 - now.get_second()), self._tick_clock)
        return False

    def _calendar(self, btn):
        """Big Sur: the clock opens Notification Center."""
        nc = getattr(self, "notifications", None)
        if nc is not None:
            nc.toggle_center()
            return None
        cal = Gtk.Calendar()
        return ui.panel.popup(btn, ui.panel.column(cal), gap=2)

    # -- status polling ------------------------------------------------------------------
    def _poll(self) -> None:
        system.run_async(lambda: (system.wifi_available(), system.wifi_enabled(), system.wifi_current()),
                         self._wifi_state)
        system.run_async(system.battery, self._battery_state)
        system.run_async(system.volume, self._sound_state)

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
        self.wifi.set_visible(True)
        self._set_icon(self.wifi, name)

    def _battery_state(self, res) -> None:
        pct, status = res or (None, "")
        self.battery.set_visible(pct is not None)
        if pct is None:
            return
        level = min(100, (pct + 5) // 10 * 10)
        charging = status in ("Charging", "Full")
        self._set_icon(self.battery, f"sonata-battery-{level}{'-charging' if charging else ''}-symbolic")
        self.battery_pct.set_label(f"{pct}%")
        self.battery_pct.set_visible(self.cfg["battery_percent"])

    def _sound_state(self, res) -> None:
        self.sound.set_visible(res is not None)
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
        pop = ui.panel.popup(btn, col, gap=2)
        col.append(self._prefs_row(pop, "Wi-Fi Preferences…", "wifi"))
        ui.panel.align_to_start(pop, btn, 2)

        def badge(n):
            level = 3 if n.signal > 60 else 2 if n.signal > 30 else 1
            b = Gtk.Box(css_classes=["wifi-badge"] + (["on"] if n.connected else []), valign=Gtk.Align.CENTER)
            b.append(Gtk.Image(icon_name=f"sonata-wifi-{level}-symbolic", pixel_size=14, hexpand=True,
                               halign=Gtk.Align.CENTER))
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
        system.run_async(lambda: (system.wifi_enabled(), system.wifi_scan()), fill)
        on.connect("state-set", lambda _s, st: (system.run_async(system.set_wifi_enabled, lambda _r: self._poll(), st),
                                                False)[1])
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
        col = ui.panel.column(ui.panel.header("Battery", pct_lbl), src_row, ui.panel.separator(),
                              ui.panel.row(None, "Show Percentage", percent_sw), ui.panel.separator())
        pop = ui.panel.popup(btn, col, gap=2)
        col.append(self._prefs_row(pop, "Battery Preferences…", "battery"))
        ui.panel.align_to_start(pop, btn, 2)

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
        slider = ui.controls.slider(0, lambda v: self._set_volume(v))
        holder = Gtk.Box(css_classes=["panel-header"])
        holder.append(slider)
        outs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col = ui.panel.column(ui.panel.header("Sound"), holder, ui.panel.separator(),
                              ui.panel.section_title("Output"), outs, ui.panel.separator())
        pop = ui.panel.popup(btn, col, gap=2)
        col.append(self._prefs_row(pop, "Sound Preferences…", "sound"))
        ui.panel.align_to_start(pop, btn, 2)

        def fill(res):
            vol, sinks = res or (None, [])
            if vol:
                slider.set_value(vol[0])
            for sk in sinks:
                r = ui.panel.row("object-select-symbolic", sk.name, on_click=lambda sk=sk: (
                    pop.popdown(), system.run_async(system.set_default_sink, None, sk.id)))
                r.icon.set_opacity(1 if sk.default else 0)            # checkmark column (menus)
                outs.append(r)
            if not sinks:
                outs.append(ui.panel.row(None, "No output devices"))
        system.run_async(lambda: (system.volume(), system.audio_sinks()), fill)
        return pop

    def _set_volume(self, v) -> None:
        system.run_async(system.set_volume, lambda _r: self._poll(), int(v), False)

    # -- Now Playing / clipboard / input source -----------------------------------------------
    def _nowplaying_panel(self, btn):
        pop = ui.panel.popup(btn, ui.panel.column(now_playing_module(self.players, header=True)), gap=2)
        ui.panel.align_to_start(pop, btn, 2)
        return pop

    def _clipboard_panel(self, btn):
        col = ui.panel.column(ui.panel.header("Clipboard"))
        pop = ui.panel.popup(btn, col, gap=2)
        for text in self.clip.items:
            first = " ".join(text.split())
            col.append(ui.panel.row(None, first[:48] + ("…" if len(first) > 48 else ""),
                                    on_click=lambda t=text: (pop.popdown(), self.clip.copy(t))))
        if not self.clip.items:
            col.append(ui.panel.row(None, "Nothing copied yet"))
        col.append(ui.panel.separator())
        col.append(ui.panel.row(None, "Clear History", on_click=lambda: (pop.popdown(), self.clip.clear())))
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
        self.input_btn.set_visible(len(lays) > 1)
        if lays:
            self._set_text(self.input_btn, lays[0].split("(")[0].upper()[:3])

    def _input_panel(self, btn):
        names = dict(system.XKB_LAYOUTS)
        lays = self._layouts()
        col = ui.panel.column()
        pop = ui.panel.popup(btn, col, gap=2)
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
        """Big Sur's magnifier: search apps (Launchpad opens with its search field)."""
        from ..__main__ import self_command
        btn.remove_css_class("open")
        try:
            GLib.spawn_async(self_command().split() + ["launchpad"], flags=GLib.SpawnFlags.SEARCH_PATH)
        except GLib.Error:
            pass
        return None

    def _control_center(self, btn):
        cc = ControlCenter(self)
        return ui.panel.popup(btn, cc, gap=2)

    def _poll_soon(self) -> None:
        GLib.timeout_add(600, lambda: (self._poll(), False)[1])


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
.wifi-badge.on { background: %(accent)s; color: %(label_on_accent)s; }
.cc-small label { font-size: %(text_small)s; font-weight: 400; }
.cc-small.on image { color: %(accent)s; }
.cc-slider-box { min-height: 22px; }
.cc-slider-icon { color: alpha(%(label)s, 0.55); margin-left: 6px; -gtk-icon-size: 12px; }
.cc-np-title { font-weight: 700; }
.cc-np-artist { color: %(label_secondary)s; font-size: %(text_small)s; }
.cc-np button { min-width: 26px; min-height: 26px; padding: 0; border: none; background: none;
  box-shadow: none; color: %(label)s; border-radius: 99px; }
.cc-np button:hover { background: %(tool_hover)s; }
""", key="control-center")


def _slider_with_icon(icon, value, on_change, sensitive=True):
    """Big Sur module slider: the symbol sits inside the capsule, left."""
    over = Gtk.Overlay(css_classes=["cc-slider-box"])
    sl = ui.controls.slider(value, on_change, style="module")
    sl.set_sensitive(sensitive)
    over.set_child(sl)
    img = Gtk.Image(icon_name=icon, css_classes=["cc-slider-icon"], halign=Gtk.Align.START,
                    valign=Gtk.Align.CENTER, can_target=False)
    over.add_overlay(img)
    return over, sl


def now_playing_module(p, header=False) -> Gtk.Widget:
    """Big Sur Now Playing: title, artist, previous / play-pause / next;
    follows the player live."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    if header:
        box.append(Gtk.Label(label="Now Playing", xalign=0, css_classes=["panel-module-title"]))
    row = Gtk.Box(spacing=8, css_classes=["cc-np"])
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=26, css_classes=["cc-np-title"])
    artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=26, css_classes=["cc-np-artist"])
    texts.append(title)
    texts.append(artist)
    row.append(texts)
    btns = {}
    for key, icon, method in (("prev", "media-skip-backward-symbolic", "Previous"),
                              ("play", "media-playback-start-symbolic", "PlayPause"),
                              ("next", "media-skip-forward-symbolic", "Next")):
        b = Gtk.Button(icon_name=icon)
        b.connect("clicked", lambda _b, m=method: p.call(m))
        btns[key] = b
        row.append(b)

    def update():
        title.set_label(p.title)
        artist.set_label(p.artist)
        artist.set_visible(bool(p.artist))
        btns["play"].set_icon_name("media-playback-pause-symbolic" if p.playing else "media-playback-start-symbolic")
    update()
    p.listeners.append(update)
    row.connect("unrealize", lambda *_: update in p.listeners and p.listeners.remove(update))
    box.append(row)
    return ui.panel.module(box)


class ControlCenter(Gtk.Box):
    """Big Sur Control Center: connectivity module (Wi-Fi, Bluetooth) beside
    Dark Mode and two small modules (Screenshot, Lock Screen); Display and
    Sound sliders; Now Playing (MPRIS) when a player runs. Opens at once;
    states fill in from background reads."""

    def __init__(self, bar: Bar):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, width_request=320)
        self.bar = bar
        self.wifi = ui.panel.toggle("network-wireless-symbolic", "Wi-Fi", False,
                                    lambda on: system.run_async(system.set_wifi_enabled, lambda _r: bar._poll(), on),
                                    caption="…")
        self.bt = ui.panel.toggle("bluetooth-active-symbolic", "Bluetooth", False,
                                  lambda on: system.run_async(system.set_bluetooth, None, on), caption="…")
        conn = ui.panel.module(self.wifi, self.bt, spacing=12)
        conn.set_valign(Gtk.Align.FILL)
        dark = Adw.StyleManager.get_default().get_dark()
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        nc = getattr(bar, "notifications", None)
        right.append(ui.panel.module(ui.panel.toggle("weather-clear-night-symbolic", "Do Not Disturb",
                                                     bool(nc and nc.dnd),
                                                     lambda on: nc and nc.set_dnd(on))))
        smalls = Gtk.Box(spacing=8, homogeneous=True)
        self.dark_btn = self._small("sonata-dark-mode-symbolic", "Dark Mode",
                                    lambda: self._set_dark(not Adw.StyleManager.get_default().get_dark()),
                                    close=False)
        (self.dark_btn.add_css_class if dark else self.dark_btn.remove_css_class)("on")
        smalls.append(self.dark_btn)
        smalls.append(self._small("camera-photo-symbolic", "Screenshot", self._screenshot))
        right.append(smalls)
        row = Gtk.Box(spacing=8, homogeneous=True)
        row.append(conn)
        row.append(right)
        self.append(row)
        disp, self.bright = _slider_with_icon("display-brightness-symbolic", 50,
                                              lambda v: system.run_async(system.set_brightness, None, int(v)))
        self.append(ui.panel.module(Gtk.Label(label="Display", xalign=0, css_classes=["panel-module-title"]), disp))
        snd, self.vol = _slider_with_icon("audio-volume-high-symbolic", 50, bar._set_volume)
        self.append(ui.panel.module(Gtk.Label(label="Sound", xalign=0, css_classes=["panel-module-title"]), snd))
        self.np = None
        system.run_async(lambda: (system.wifi_enabled(), system.wifi_current(), system.bluetooth_state(),
                                  system.brightness(), system.volume()), self._fill)
        self._now_playing()

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

    def _fill(self, res):
        if not res:
            return
        wifi_on, (ssid, _sig, _wired), bt, b, vol = res
        ui.panel.set_toggle(self.wifi, bool(wifi_on), ssid or ("Not Connected" if wifi_on else "Off"))
        ui.panel.set_toggle(self.bt, bool(bt), "On" if bt else "Off" if bt is not None else "Unavailable")
        if b is None:
            self.bright.set_sensitive(False)
        else:
            self.bright.set_value(b)
        if vol is None:
            self.vol.set_sensitive(False)
        else:
            self.vol.set_value(vol[0])

    def _screenshot(self):
        """Whole screen to ~/Pictures (grim), like Cmd+Shift+3."""
        def shoot():
            import shutil
            pics = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES) or GLib.get_home_dir()
            name = GLib.DateTime.new_now_local().format("Screenshot %Y-%m-%d at %H.%M.%S.png")
            if shutil.which("grim"):
                system._run(["grim", os.path.join(pics, name)], timeout=10)
        GLib.timeout_add(350, lambda: (system.run_async(shoot), False)[1])   # after the panel closes

    def _now_playing(self):
        from . import mpris
        p = mpris.players()
        if p.active:
            self.append(now_playing_module(p))

    def _set_dark(self, on: bool) -> None:
        """Dark Mode is a Linux setting (freedesktop colour-scheme), so every
        app follows -- and Sonata with them."""
        if hasattr(self, "dark_btn"):
            (self.dark_btn.add_css_class if on else self.dark_btn.remove_css_class)("on")
        system.run_async(lambda: system._run(["gsettings", "set", "org.gnome.desktop.interface",
                                              "color-scheme", "prefer-dark" if on else "default"]))


class AboutWindow(Adw.Window):
    """About This Computer (Big Sur layout: logo left, OS name, specs)."""

    def __init__(self):
        super().__init__(title="About This Computer", resizable=False)
        self.add_css_class("sonata-about")
        ui.window.standard(self)
        head = Gtk.Box(margin_top=10, margin_start=8)
        head.append(ui.window.traffic_lights(self.close, self.minimize))
        body = Gtk.Box(spacing=36, css_classes=["about-box"])
        logo = Gtk.Image(icon_name="sonata-logo-symbolic", pixel_size=120, valign=Gtk.Align.CENTER)
        body.append(logo)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
        self.info = info
        body.append(info)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(head)
        col.append(body)
        self.set_content(Gtk.WindowHandle(child=col))
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
            row.append(Gtk.Label(label=key, css_classes=["about-key"]))
            row.append(Gtk.Label(label=value, xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=48))
            self.info.append(row)


class AboutAppWindow(Adw.Window):
    def __init__(self, info, name):
        super().__init__(title=f"About {name}", resizable=False)
        self.add_css_class("sonata-about")
        ui.window.standard(self)
        head = Gtk.Box(margin_top=10, margin_start=8)
        head.append(ui.window.traffic_lights(self.close, self.minimize))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["about-box"])
        img = Gtk.Image(pixel_size=96)
        if info:
            from .. import icons
            icons.set_image(img, icons.app_icon(info))
        col.append(img)
        col.append(Gtk.Label(label=name, css_classes=["about-name"]))
        if info:
            for text in (info.get_description(), info.get_generic_name()):
                if text:
                    col.append(Gtk.Label(label=text, css_classes=["about-version"], wrap=True, max_width_chars=40))
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(head)
        outer.append(col)
        self.set_content(Gtk.WindowHandle(child=outer))


TITLEBAR = {   # Big Sur title bars: (focused bg, unfocused bg, title, unfocused title)
    False: ("#e8e8e8ff", "#f6f6f6ff", "#262626ff", "#9a9a9aff"),
    True: ("#2d2d2dff", "#262626ff", "#e6e6e6ff", "#8a8a8aff"),
}


def set_titlebar_colors(dark: bool) -> None:
    """Colours of the server-side title bars (pixdecor and Wayfire's own
    decoration) for the appearance; Wayfire reloads its config live."""
    fg, bg, text, dim = TITLEBAR[bool(dark)]
    if system.wayfire_get("pixdecor", "fg_color") != "\\" + fg:
        for k, v in (("fg_color", fg), ("bg_color", bg), ("fg_text_color", text), ("bg_text_color", dim)):
            system.wayfire_set("pixdecor", k, "\\" + v)
        for k, v in (("active_color", fg), ("inactive_color", bg), ("font_color", text)):
            system.wayfire_set("decoration", k, "\\" + v)


def _listen_for_lock() -> None:
    """logind "Lock" (loginctl lock-session, the Control Center, idle
    tools) starts Sonata's lock screen; the menu bar always runs, so it
    listens for the session."""
    def lock(*_a):
        from ..__main__ import self_command
        try:
            GLib.spawn_async(self_command().split() + ["lock"], flags=GLib.SpawnFlags.SEARCH_PATH)
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


class TopBarWindow(Gtk.ApplicationWindow):
    def __init__(self, app, preview: bool = False):
        super().__init__(application=app, title="Menu Bar", css_classes=["sonata-topbar"], decorated=False,
                         resizable=True)
        from ..wl.toplevels import ToplevelManager
        self.manager = ToplevelManager(Gdk.Display.get_default(),
                                       ignore_app_ids={"io.github.vinioliveiras.sonata2.topbar"})
        self.bar = Bar(self.manager)
        self.set_size_request(-1, BAR_H)
        if not preview:
            from .notifications import Notifications
            self.bar.notifications = Notifications(app)
            _listen_for_lock()
        if not preview:
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
                LS.set_layer(self, LS.Layer.TOP)
                for e in (LS.Edge.TOP, LS.Edge.LEFT, LS.Edge.RIGHT):
                    LS.set_anchor(self, e, True)
                LS.set_exclusive_zone(self, BAR_H)
                LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND)

    def _preview(self) -> None:
        """Bar over a sample wallpaper in a normal window (screenshots)."""
        GLib.timeout_add(300, lambda: (self.bar._sound_state((70, False)), self.bar._battery_state((64, "Discharging")),
                                       self.bar._wifi_state((True, True, ("Home", 80, False))), False)[-1])
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
