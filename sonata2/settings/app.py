"""Sonata Settings (macOS System Settings layout, from the LayerOSX panel):
sidebar with coloured badges + one pane per section, fixed-size window
with traffic lights (zoom greyed out, like System Settings).

The sidebar keeps Vini's split:
  Linux  -- Wi-Fi, Bluetooth, Sound, Displays, Battery, Wallpaper: read and
            written through the system services (backend/system.py),
            nothing stored by Sonata;
  Sonata -- Appearance, Desktop & Dock, Menu Bar, Launchpad: Sonata's own
            settings (~/.config/sonata2/*.json); the shell components watch
            those files and apply changes live;
  About.
`python3 -m sonata2 settings [--page ID]`."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from ..backend import system  # noqa: E402

SECTIONS = [  # id, title, icon, badge colour, group
    ("wifi", "Wi-Fi", "network-wireless-symbolic", "blue", "linux"),
    ("bluetooth", "Bluetooth", "bluetooth-active-symbolic", "blue", "linux"),
    ("sound", "Sound", "audio-volume-high-symbolic", "pink", "linux"),
    ("displays", "Displays", "video-display-symbolic", "blue", "linux"),
    ("battery", "Battery", "battery-full-symbolic", "green", "linux"),
    ("wallpaper", "Wallpaper", "image-x-generic-symbolic", "teal", "linux"),
    ("appearance", "Appearance", "applications-graphics-symbolic", "black", "sonata"),
    ("dock", "Desktop & Dock", "view-grid-symbolic", "black", "sonata"),
    ("menubar", "Menu Bar", "view-restore-symbolic", "indigo", "sonata"),
    ("launchpad", "Launchpad", "view-app-grid-symbolic", "graphite", "sonata"),
    ("about", "About", "help-about-symbolic", "gray", "about"),
]

ui.register("""
window.sonata-settings { background: %(window_bg)s; color: %(label)s; }
.st-badge { border-radius: 7px; padding: 4px; color: white; }
.st-badge.big { border-radius: 12px; padding: 10px; }
.st-badge.blue { background: %(sys_blue)s; } .st-badge.green { background: %(sys_green)s; }
.st-badge.pink { background: %(sys_pink)s; } .st-badge.teal { background: %(sys_teal)s; }
.st-badge.indigo { background: %(sys_indigo)s; } .st-badge.graphite { background: %(sys_graphite)s; }
.st-badge.gray { background: %(sys_gray)s; }
.st-badge.black { background: %(sys_black)s; box-shadow: inset 0 0 0 1px rgba(255,255,255,.18); }
.st-card { padding: 10px 12px 6px 12px; }
.st-card-title { font-weight: 700; }
.st-card-sub { color: %(label_secondary)s; font-size: %(text_small)s; }
.st-group-gap { min-height: 10px; }
.st-pane-title { font-weight: 700; font-size: %(text_title)s; color: %(label)s; }
.st-about-name { font-family: %(font_display)s; font-weight: 700; font-size: 26px; color: %(label)s; }
.st-caption { color: %(label_secondary)s; font-size: %(text_small)s; }
.st-wall { border-radius: 10px; }
""", key="settings")


def badge(icon, color, big=False) -> Gtk.Box:
    img = Gtk.Image(icon_name=icon, pixel_size=28 if big else 16)
    box = Gtk.Box(css_classes=["st-badge", color] + (["big"] if big else []),
                  valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    box.append(img)
    return box


def group(title="", description="") -> Adw.PreferencesGroup:
    return Adw.PreferencesGroup(title=title, description=description)


def switch_row(title, active, on_change, subtitle="") -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(active))
    row.connect("notify::active", lambda r, _p: on_change(r.get_active()))
    return row


def combo_row(title, options, selected, on_change, subtitle="") -> Adw.ComboRow:
    """options: [(value, label)]"""
    row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new([o[1] for o in options]))
    values = [o[0] for o in options]
    row.set_selected(values.index(selected) if selected in values else 0)
    row.connect("notify::selected", lambda r, _p: on_change(values[r.get_selected()]))
    return row


def slider_row(title, value, lower, upper, on_change, subtitle="") -> Adw.ActionRow:
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    s = ui.controls.slider(value, on_change, lower=lower, upper=upper)
    s.set_size_request(220, -1)
    s.set_valign(Gtk.Align.CENTER)
    row.add_suffix(s)
    row.slider = s
    return row


class Settings(Adw.ApplicationWindow):
    def __init__(self, app, start: str = "wifi"):
        super().__init__(application=app, title="System Settings", css_classes=["sonata-settings"])
        # Fixed size, like macOS System Settings.
        self.set_default_size(920, 640)
        self.set_resizable(False)
        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(vexpand=True, min_sidebar_width=230, max_sidebar_width=260)
        self.split.set_sidebar(self._sidebar())
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)
        self.pages = {}
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, k, *_: (self.close(), True)[1] if k == Gdk.KEY_Escape else False)
        self.add_controller(keys)
        self.select(start if start in [s[0] for s in SECTIONS] else "wifi")

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    # -- sidebar -------------------------------------------------------------------
    def _sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False, show_start_title_buttons=False, show_end_title_buttons=False)
        hb.pack_start(ui.window.traffic_lights(self.close, self.minimize, None))
        tv.add_top_bar(hb)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card = Gtk.Box(spacing=10, css_classes=["st-card"])
        card.append(badge("sonata-logo-symbolic", "black", big=True))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=GLib.get_real_name() or GLib.get_user_name(), xalign=0,
                               css_classes=["st-card-title"]))
        texts.append(Gtk.Label(label="Sonata 2", xalign=0, css_classes=["st-card-sub"]))
        card.append(texts)
        box.append(card)
        self.listbox = Gtk.ListBox(css_classes=["navigation-sidebar"], vexpand=True)
        self.rows = {}
        last = None
        for sid, title, icon, color, grp in SECTIONS:
            if last and grp != last:        # visual gap between groups, like System Settings
                gap = Gtk.ListBoxRow(selectable=False, activatable=False)
                gap.set_child(Gtk.Box(css_classes=["st-group-gap"]))
                self.listbox.append(gap)
            last = grp
            row = Gtk.ListBoxRow()
            row.sid = sid
            h = Gtk.Box(spacing=10, margin_top=3, margin_bottom=3, margin_start=2)
            h.append(badge(icon, color))
            h.append(Gtk.Label(label=title, xalign=0))
            row.set_child(h)
            self.listbox.append(row)
            self.rows[sid] = row
        self.listbox.connect("row-selected", lambda _lb, r: r and getattr(r, "sid", None) and
                             self.select(r.sid, from_sidebar=True))
        box.append(self.listbox)
        tv.set_content(Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
        return Adw.NavigationPage(title="System Settings", child=tv)

    def select(self, sid, from_sidebar=False):
        if not from_sidebar:
            self.listbox.select_row(self.rows[sid])
            return
        if sid not in self.pages:
            title = next(s[1] for s in SECTIONS if s[0] == sid)
            page = Adw.PreferencesPage()
            for g in getattr(self, f"_page_{sid}")():
                page.add(g)
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
            hb.set_title_widget(Gtk.Label(label=title, css_classes=["st-pane-title"]))
            tv.add_top_bar(hb)
            tv.set_content(page)
            self.pages[sid] = Adw.NavigationPage(title=title, child=tv)
        self.split.set_content(self.pages[sid])
        self.split.set_show_content(True)

    def _async_rows(self, grp, work, fill) -> None:
        """Fill `grp` from work() (threaded); a placeholder while loading."""
        holder = Adw.ActionRow(title="Loading…")
        grp.add(holder)

        def done(res):
            grp.remove(holder)
            fill(res)
        system.run_async(work, done)

    # -- Linux sections ------------------------------------------------------------
    def _page_wifi(self):
        top = group()
        sw = switch_row("Wi-Fi", False, lambda on: system.run_async(system.set_wifi_enabled,
                                                                  lambda _r: self._refresh("wifi"), on))
        top.add(sw)
        nets = group("Networks")
        rescan = Gtk.Button(icon_name="view-refresh-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                            tooltip_text="Scan again")
        rescan.connect("clicked", lambda *_: self._refresh("wifi", rescan=True))
        nets.set_header_suffix(rescan)
        self._wifi_nets = nets
        self._wifi_rows = []
        self._wifi_switch = sw
        self._fill_wifi(False)
        return [top, nets]

    def _fill_wifi(self, rescan: bool) -> None:
        def work():
            return system.wifi_enabled(), system.wifi_scan(rescan)

        def fill(res):
            enabled, networks = res or (False, [])
            self._wifi_switch.set_active(enabled)
            for r in self._wifi_rows:
                self._wifi_nets.remove(r)
            self._wifi_rows = []
            for n in networks[:20]:
                row = Adw.ActionRow(title=n.ssid, activatable=not n.connected,
                                    subtitle="Connected" if n.connected else "")
                sig = "excellent" if n.signal > 75 else "good" if n.signal > 50 else "ok" if n.signal > 25 else "weak"
                if n.secure:
                    row.add_suffix(Gtk.Image(icon_name="system-lock-screen-symbolic", pixel_size=12))
                row.add_suffix(Gtk.Image(icon_name=f"network-wireless-signal-{sig}-symbolic"))
                row.connect("activated", lambda _r, n=n: self._join(n))
                self._wifi_nets.add(row)
                self._wifi_rows.append(row)
            if not networks:
                row = Adw.ActionRow(title="No networks found" if enabled else "Wi-Fi is off")
                self._wifi_nets.add(row)
                self._wifi_rows.append(row)
        system.run_async(work, fill)

    def _join(self, n) -> None:
        def connect(pw=""):
            system.run_async(system.wifi_connect, lambda r: (self.toast(
                f"Connected to {n.ssid}" if r and r[0] else (r[1] if r else "Couldn't connect")),
                self._refresh("wifi")), n.ssid, pw)
        if not n.secure:
            connect()
            return
        entry = Gtk.PasswordEntry(show_peek_icon=True)
        dlg = ui.dialog.alert(f"The Wi-Fi network “{n.ssid}” requires a password.", "",
                              [("cancel", "Cancel", ""), ("join", "Join", "default")],
                              lambda r: r == "join" and connect(entry.get_text()), parent=self)
        dlg.set_extra_child(entry)

    def _refresh(self, sid: str, rescan=False) -> None:
        if sid == "wifi" and sid in self.pages:
            self._fill_wifi(rescan)

    def _page_bluetooth(self):
        top = group()
        devs = group("My Devices")

        def fill(res):
            state, devices = res or (None, [])
            if state is None:
                top.add(Adw.ActionRow(title="Bluetooth", subtitle="No Bluetooth adapter found"))
                return
            top.add(switch_row("Bluetooth", state, lambda on: system.run_async(system.set_bluetooth, None, on),
                               subtitle="This computer is discoverable while Bluetooth Settings is open"))
            for d in devices:
                row = Adw.ActionRow(title=d.name, subtitle="Connected" if d.connected else
                                    ("Not Connected" if d.paired else "Not Paired"))
                btn = Gtk.Button(label="Disconnect" if d.connected else "Connect", valign=Gtk.Align.CENTER)
                btn.connect("clicked", lambda b, d=d: system.run_async(
                    system.bluetooth_connect, lambda ok: self.toast(("Done" if ok else "That didn't work")),
                    d.mac, not d.connected))
                row.add_suffix(btn)
                devs.add(row)
            if not devices:
                devs.add(Adw.ActionRow(title="No devices"))
        system.run_async(lambda: (system.bluetooth_state(), system.bluetooth_devices()), fill)
        return [top, devs]

    def _page_sound(self):
        out = group("Output")
        vol = group("Volume")

        def fill(res):
            v, sinks = res or (None, [])
            if v is None:
                vol.add(Adw.ActionRow(title="Output volume", subtitle="PipeWire (wpctl) not found"))
                return
            vol.add(slider_row("Output volume", v[0], 0, 100,
                               lambda x: system.run_async(system.set_volume, None, int(x))))
            vol.add(switch_row("Mute", v[1], lambda on: system.run_async(system.set_volume, None, None, on)))
            options = [(s.id, s.name) for s in sinks]
            if options:
                cur = next((s.id for s in sinks if s.default), options[0][0])
                out.add(combo_row("Output device", options, cur,
                                  lambda sid: system.run_async(system.set_default_sink, None, sid)))
        system.run_async(lambda: (system.volume(), system.audio_sinks()), fill)
        return [vol, out]

    def _page_displays(self):
        bright = group("Brightness")
        screens = group("Displays")

        def fill(res):
            b, ds = res or (None, [])
            if b is not None:
                bright.add(slider_row("Brightness", b, 5, 100,
                                      lambda x: system.run_async(system.set_brightness, None, int(x))))
            else:
                bright.add(Adw.ActionRow(title="Brightness", subtitle="No backlight control (brightnessctl)"))
            for d in ds:
                screens.add(combo_row(f"{d.name}", [(m, m.replace("@", " @ ").split(".")[0] + " Hz")
                                                    for m in d.modes], d.current,
                                      lambda m, d=d: system.run_async(system.set_display_mode, None, d.name, m),
                                      subtitle=d.description))
                screens.add(combo_row("Scale", [(1.0, "100 %"), (1.25, "125 %"), (1.5, "150 %"), (2.0, "200 %")],
                                      d.scale, lambda s, d=d: system.run_async(system.set_display_scale, None,
                                                                               d.name, s)))
            if not ds:
                screens.add(Adw.ActionRow(title="Displays", subtitle="wlr-randr not found or no outputs"))
        system.run_async(lambda: (system.brightness(), system.displays()), fill)
        return [bright, screens]

    def _page_battery(self):
        info = group()
        mode = group("Energy Mode")

        def fill(res):
            (pct, status), ac, prof = res or ((None, ""), False, None)
            info.add(Adw.ActionRow(title="Battery level", subtitle=f"{pct}% · {status}" if pct is not None
                                   else "No battery"))
            info.add(Adw.ActionRow(title="Power source", subtitle="Power Adapter" if ac else "Battery"))
            if prof:
                mode.add(combo_row("Energy mode", list(system.POWER_PROFILES), prof,
                                   lambda p: system.run_async(system.set_power_profile, None, p)))
            else:
                mode.add(Adw.ActionRow(title="Energy mode", subtitle="power-profiles-daemon not available"))
        system.run_async(lambda: (system.battery(), system.on_ac(), system.power_profile()), fill)
        return [info, mode]

    def _page_wallpaper(self):
        g = group("Wallpaper", "A Linux desktop setting (org.gnome.desktop.background); "
                               "the Sonata session draws it.")
        uri = system.gsetting("org.gnome.desktop.background", "picture-uri") or ""
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, css_classes=["st-wall"], height_request=180,
                          can_shrink=True, overflow=Gtk.Overflow.HIDDEN)
        f = Gio.File.new_for_uri(uri) if uri else None
        if f and f.query_exists(None):
            pic.set_file(f)
        g.add(pic)
        row = Adw.ActionRow(title="Picture", subtitle=f.get_basename() if f else "None")
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)

        def pick(*_):
            dlg = Gtk.FileDialog(title="Choose a Picture")
            filt = Gtk.FileFilter(name="Images")
            filt.add_mime_type("image/*")
            dlg.set_default_filter(filt)

            def done(d, res):
                try:
                    chosen = d.open_finish(res)
                except GLib.Error:
                    return
                for key in ("picture-uri", "picture-uri-dark"):
                    system.set_gsetting("org.gnome.desktop.background", key, chosen.get_uri())
                pic.set_file(chosen)
                row.set_subtitle(chosen.get_basename())
            dlg.open(self, None, done)
        choose.connect("clicked", pick)
        row.add_suffix(choose)
        g.add(row)
        return [g]

    # -- Sonata sections ------------------------------------------------------------
    def _page_appearance(self):
        g = group("Appearance")
        scheme = system.gsetting("org.gnome.desktop.interface", "color-scheme") or "default"
        g.add(combo_row("Appearance", [("default", "Light"), ("prefer-dark", "Dark")],
                        "prefer-dark" if scheme == "prefer-dark" else "default",
                        lambda v: system.set_gsetting("org.gnome.desktop.interface", "color-scheme", v),
                        subtitle="Linux setting: every app follows it"))
        s = group("Sonata")
        app = config.load("appearance", {"icon_theme": "Sonata", "theme": "mac"})
        s.add(combo_row("Style", [("mac", "macOS"), ("windows", "Windows 11 (coming later)")], app["theme"],
                        lambda v: self._save("appearance", "theme", "mac")))
        themes = sorted({d for base in GLib.get_system_data_dirs() + [GLib.get_user_data_dir()]
                         for d in (os.listdir(os.path.join(base, "icons")) if os.path.isdir(os.path.join(base, "icons"))
                                   else []) if os.path.exists(os.path.join(base, "icons", d, "index.theme"))}
                        | {"Sonata"})
        s.add(combo_row("Icons", [(t, t) for t in themes], app["icon_theme"],
                        lambda v: (self._save("appearance", "icon_theme", v),
                                   self.toast("Restart the Dock and Launchpad to use the new icons"))))
        dock = config.load("dock", {"glass": True})
        s.add(switch_row("Translucent glass", dock["glass"], lambda on: self._save("dock", "glass", on),
                         subtitle="Frosted Dock and menu bar (needs the Wayfire blur plugin)"))
        return [g, s]

    def _page_dock(self):
        from ..shell import dock as D
        cfg = config.load("dock", D.DEFAULTS)
        size = group("Dock")
        size.add(slider_row("Size", cfg["icon_size"], D.MIN_SIZE, D.MAX_SIZE,
                            lambda v: self._save("dock", "icon_size", int(v))))
        size.add(switch_row("Magnification", cfg["magnification"], lambda on: self._save("dock", "magnification", on)))
        size.add(slider_row("Magnified size", cfg["magnified_size"], D.MIN_SIZE, D.MAX_SIZE,
                            lambda v: self._save("dock", "magnified_size", int(v))))
        size.add(combo_row("Position on screen", [("left", "Left"), ("bottom", "Bottom"), ("right", "Right")],
                           cfg["position"], lambda v: self._save("dock", "position", v)))
        behave = group()
        behave.add(switch_row("Automatically hide and show the Dock", cfg["autohide"],
                              lambda on: self._save("dock", "autohide", on)))
        behave.add(switch_row("Show suggested and recent apps in Dock", cfg["show_recents"],
                              lambda on: self._save("dock", "show_recents", on)))
        return [size, behave]

    def _page_menubar(self):
        from ..shell import topbar as T
        cfg = config.load("topbar", T.DEFAULTS)
        g = group("Menu Bar")
        g.add(switch_row("Show battery percentage", cfg["battery_percent"],
                         lambda on: self._save("topbar", "battery_percent", on)))
        g.add(combo_row("Clock", [("%a %-d %b  %H:%M", "Mon 28 Sep  21:41"), ("%a %H:%M", "Mon 21:41"),
                                  ("%a %-d %b  %-I:%M %p", "Mon 28 Sep  9:41 PM"), ("%H:%M", "21:41")],
                        cfg["clock_format"], lambda v: self._save("topbar", "clock_format", v)))
        return [g]

    def _page_launchpad(self):
        g = group("Launchpad")
        reset = Gtk.Button(label="Reset…", valign=Gtk.Align.CENTER)
        reset.connect("clicked", lambda *_: ui.dialog.alert(
            "Reset the Launchpad layout?", "Folders and your icon order are removed; apps are sorted by name.",
            [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")],
            lambda r: r == "reset" and (config.save("launchpad", {"pages": [], "hidden": []}),
                                        self.toast("Launchpad was reset")), parent=self))
        row = Adw.ActionRow(title="Layout", subtitle="Pages, folders and order")
        row.add_suffix(reset)
        g.add(row)
        hidden = config.load("launchpad", {"pages": [], "hidden": []}).get("hidden", [])
        h = group("Hidden apps")
        for did in hidden:
            r = Adw.ActionRow(title=did)
            b = Gtk.Button(label="Show", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, did=did, r=r: (self._unhide(did), h.remove(r)))
            r.add_suffix(b)
            h.add(r)
        if not hidden:
            h.add(Adw.ActionRow(title="None"))
        return [g, h]

    def _unhide(self, did: str) -> None:
        data = config.load("launchpad", {"pages": [], "hidden": []})
        data["hidden"] = [x for x in data.get("hidden", []) if x != did]
        config.save("launchpad", data)

    def _save(self, name: str, key: str, value) -> None:
        """Write one Sonata setting; the running component reloads it."""
        data = {}
        try:
            with open(os.path.join(config.CONFIG_DIR, name + ".json"), encoding="utf-8") as f:
                import json
                data = json.load(f)
        except (OSError, ValueError):
            pass
        data[key] = value
        config.save(name, data)

    # -- About ------------------------------------------------------------------------
    def _page_about(self):
        hero = group()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8)
        box.append(badge("sonata-logo-symbolic", "black", big=True))
        name = Gtk.Label(label="…", css_classes=["st-about-name"])
        sub = Gtk.Label(label="", css_classes=["st-caption"])
        box.append(name)
        box.append(sub)
        hero.add(box)
        specs = group()

        def fill(a):
            if not a:
                return
            name.set_label(a.os_name)
            sub.set_label("Sonata 2 desktop")
            for k, v in (("Computer", a.machine), ("Processor", a.cpu), ("Memory", f"{a.memory_gb} GB"),
                         ("Graphics", ", ".join(a.gpus) or "—"), ("Kernel", a.kernel)):
                row = Adw.ActionRow(title=k)
                row.add_suffix(Gtk.Label(label=v, css_classes=["st-caption"], ellipsize=Pango.EllipsizeMode.END,
                                         max_width_chars=40))
                specs.add(row)
        system.run_async(system.about, fill)
        return [hero, specs]


def settings_desktop_file(command: str) -> str:
    """~/.local/share/applications/sonata2-settings.desktop (shown in
    Launchpad as "System Settings")."""
    path = os.path.join(GLib.get_user_data_dir(), "applications", "sonata2-settings.desktop")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    text = ("[Desktop Entry]\nType=Application\nName=System Settings\nComment=Sonata and system settings\n"
            "Icon=preferences-system\nCategories=Settings;System;\n"
            f"Exec={command} settings\n")
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return path
    except OSError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path
