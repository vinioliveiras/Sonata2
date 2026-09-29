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

from .. import config, icons, ui  # noqa: E402
from ..backend import system  # noqa: E402

SECTIONS = [  # id, title, icon, badge colour, group
    ("wifi", "Wi-Fi", "network-wireless-symbolic", "blue", "linux"),
    ("bluetooth", "Bluetooth", "bluetooth-active-symbolic", "blue", "linux"),
    ("sound", "Sound", "audio-volume-high-symbolic", "pink", "linux"),
    ("displays", "Displays", "video-display-symbolic", "blue", "linux"),
    ("battery", "Battery", "battery-full-symbolic", "green", "linux"),
    ("wallpaper", "Wallpaper", "image-x-generic-symbolic", "teal", "linux"),
    ("keyboard", "Keyboard", "input-keyboard-symbolic", "gray", "input"),
    ("trackpad", "Trackpad", "input-touchpad-symbolic", "gray", "input"),
    ("mouse", "Mouse", "input-mouse-symbolic", "gray", "input"),
    ("datetime", "Date & Time", "preferences-system-time-symbolic", "blue", "system"),
    ("users", "Users & Groups", "system-users-symbolic", "gray", "system"),
    ("sharing", "Sharing", "folder-publicshare-symbolic", "blue", "system"),
    ("accessibility", "Accessibility", "preferences-desktop-accessibility-symbolic", "blue", "system"),
    ("appearance", "General", "preferences-system-symbolic", "gray", "sonata"),
    ("dock", "Desktop & Dock", "view-grid-symbolic", "black", "sonata"),
    ("menubar", "Menu Bar", "view-restore-symbolic", "indigo", "sonata"),
    ("launchpad", "Launchpad", "view-app-grid-symbolic", "graphite", "sonata"),
    ("about", "About", "help-about-symbolic", "gray", "about"),
]

# One line under each section's title (the page's hero row, like LayerOSX
# Settings and macOS System Settings).
DESCRIPTIONS = {
    "wifi": "Choose a network and see how this computer is connected.",
    "bluetooth": "Connect keyboards, mice, headphones and other wireless devices.",
    "sound": "Output and input devices and their volume.",
    "displays": "Brightness, resolution and how your screens are arranged.",
    "battery": "Battery level, energy mode and the menu bar percentage.",
    "wallpaper": "The picture on your desktop.",
    "appearance": "Appearance, default web browser and Sonata's look.",
    "keyboard": "Key repeat and the keyboard layout (input source).",
    "trackpad": "Tracking speed, tap to click and scrolling.",
    "mouse": "Tracking speed, scrolling and the primary button.",
    "datetime": "Time zone, automatic time and the menu bar clock.",
    "users": "Your picture and password, other accounts and the apps that open at login.",
    "sharing": "The name other computers see on the network.",
    "accessibility": "Motion, transparency, text and pointer size.",
    "dock": "Size, magnification, position and hiding of the Dock.",
    "menubar": "The clock and the items in the menu bar.",
    "launchpad": "How Launchpad arranges your apps.",
}

_ACCENT_CSS = "".join(f".st-accent.{n} {{ background: {c[0]}; }}\n" for n, c in ui.tokens.ACCENTS.items())
ui.register(_ACCENT_CSS + """
button.st-accent { min-width: 16px; min-height: 16px; padding: 0; margin: 0 3px; border-radius: 99px; border: none;
  box-shadow: inset 0 0 0 0.5px rgba(0,0,0,0.2); transition: box-shadow %(t_fast)s; }
button.st-accent.selected { box-shadow: 0 0 0 2px %(window_bg)s, 0 0 0 3.5px alpha(%(label)s, 0.45); }
""", key="settings-accent")

ui.register("""
window.sonata-settings { color: %(label)s; }
/* glass sidebar (standard material), opaque content pane */
.sonata-settings .sidebar-pane { background: none; }
.st-sidebar headerbar, .st-sidebar toolbarview, .st-sidebar scrolledwindow,
.st-sidebar list { background: none; box-shadow: none; }
.st-content, .st-content toolbarview, .st-content headerbar { background: %(pane_bg)s; box-shadow: none; }
.st-hero label.title { font-weight: 700; }
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
.st-gap-row, .st-gap-row:hover { min-height: 8px; padding: 0; margin: 0; background: none; }
.st-pane-title { font-weight: 700; font-size: %(text_title)s; color: %(label)s; }
.st-about-name { font-family: %(font_display)s; font-weight: 700; font-size: 26px; color: %(label)s; }
.st-caption { color: %(label_secondary)s; font-size: %(text_small)s; }
.st-wall { border-radius: 10px; }
""", key="settings")


def _clear_group(grp) -> None:
    """Remove the rows added to an Adw.PreferencesGroup."""
    rows = []
    w = grp
    stack = [grp]
    while stack:                       # the rows live in the group's inner list box
        w = stack.pop()
        c = w.get_first_child()
        while c is not None:
            if isinstance(c, Gtk.ListBox):
                r = c.get_first_child()
                while r is not None:
                    rows.append(r)
                    r = r.get_next_sibling()
            else:
                stack.append(c)
            c = c.get_next_sibling()
    for r in rows:
        grp.remove(r)


def _is_admin() -> bool:
    try:
        import grp
        return any(GLib.get_user_name() in grp.getgrnam(g).gr_mem for g in ("wheel", "sudo", "admin")
                   if _group_exists(g))
    except (ImportError, KeyError):
        return False


def _group_exists(name) -> bool:
    import grp
    try:
        grp.getgrnam(name)
        return True
    except KeyError:
        return False


def _login_items():
    """(file name, app name, enabled) for every autostart entry (user and
    system; a user copy overrides the system one)."""
    from .. import autostart
    seen, out = set(), []
    for d in autostart._dirs():
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for name in names:
            if not name.endswith(".desktop") or name in seen:
                continue
            seen.add(name)
            kf = GLib.KeyFile()
            try:
                kf.load_from_file(os.path.join(d, name), GLib.KeyFileFlags.NONE)
                title = kf.get_locale_string("Desktop Entry", "Name", None)
            except GLib.Error:
                continue
            hidden = autostart._get(kf, "Hidden", "boolean") or autostart._get(
                kf, "X-GNOME-Autostart-enabled", "boolean") is False
            only = autostart._get(kf, "OnlyShowIn", "string_list")
            if only and autostart.DESKTOP not in only:
                continue
            out.append((name, title, not hidden))
    return out


def _set_login_item(name, on) -> None:
    """Enable/disable through a user copy (Hidden=true), like GNOME/KDE."""
    from .. import autostart
    user_dir = autostart._dirs()[0]
    src = next((os.path.join(d, name) for d in autostart._dirs() if os.path.exists(os.path.join(d, name))), None)
    if not src:
        return
    kf = GLib.KeyFile()
    kf.load_from_file(src, GLib.KeyFileFlags.KEEP_TRANSLATIONS)
    kf.set_boolean("Desktop Entry", "Hidden", not on)
    kf.set_boolean("Desktop Entry", "X-GNOME-Autostart-enabled", on)
    os.makedirs(user_dir, exist_ok=True)
    kf.save_to_file(os.path.join(user_dir, name))


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


def slider_row(title, value, lower, upper, on_change, subtitle="", ends=None) -> Adw.ActionRow:
    """ends=("Slow", "Fast"): small labels under the slider's ends (macOS)."""
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    s = ui.controls.slider(value, on_change, lower=lower, upper=upper)
    s.set_size_request(220, -1)
    s.set_valign(Gtk.Align.CENTER)
    if ends:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, margin_top=4, margin_bottom=4)
        col.append(s)
        labels = Gtk.Box()
        labels.append(Gtk.Label(label=ends[0], css_classes=["st-caption"], hexpand=True, xalign=0))
        labels.append(Gtk.Label(label=ends[1], css_classes=["st-caption"], xalign=1))
        col.append(labels)
        row.add_suffix(col)
    else:
        row.add_suffix(s)
    row.slider = s
    return row


def _speed(v) -> float:
    """Wayfire speed (-1..1) <-> slider (0..100)."""
    try:
        return (float(v) + 1) * 50
    except ValueError:
        return 50


class Settings(Adw.ApplicationWindow):
    def __init__(self, app, start: str = "wifi"):
        super().__init__(application=app, title="System Settings")
        for c in ("sonata-settings", "sonata-glass"):      # added, not passed (keeps GTK's "csd")
            self.add_css_class(c)
        # Fixed size, like macOS System Settings.
        self.set_default_size(920, 640)
        self.set_resizable(False)
        ui.window.standard(self)
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
                gap = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["st-gap-row"])
                gap.set_child(Gtk.Box())
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
        return Adw.NavigationPage(title="System Settings", child=tv, css_classes=["st-sidebar", "sonata-sidebar"])

    def select(self, sid, from_sidebar=False):
        if not from_sidebar:
            self.listbox.select_row(self.rows[sid])
            return
        if sid not in self.pages:
            title = next(s[1] for s in SECTIONS if s[0] == sid)
            page = Adw.PreferencesPage()
            self._hero_done = sid == "about"          # About has its own big header
            groups = getattr(self, f"_page_{sid}")()
            if not self._hero_done:                    # page didn't turn a row into the hero
                hero = group()
                hero.add(self._hero(Adw.ActionRow(title=title, use_markup=False), sid))
                page.add(hero)
            for g in groups:
                page.add(g)
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
            hb.set_title_widget(Gtk.Label(label=title, css_classes=["st-pane-title"]))
            tv.add_top_bar(hb)
            tv.set_content(page)
            self.pages[sid] = Adw.NavigationPage(title=title, child=tv, css_classes=["st-content"])
        self.split.set_content(self.pages[sid])
        self.split.set_show_content(True)

    def _hero(self, row, sid):
        """Make `row` the page's first row: the section's big badge, bold
        title, one-line description (LayerOSX / System Settings)."""
        _sid, _title, icon, color, _grp = next(s for s in SECTIONS if s[0] == sid)
        row.add_prefix(badge(icon, color, big=True))
        if not row.get_subtitle():
            row.set_subtitle(DESCRIPTIONS.get(sid, ""))
        row.add_css_class("st-hero")
        self._hero_done = True
        return row

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
        sw = self._hero(switch_row("Wi-Fi", False, lambda on: system.run_async(
            system.set_wifi_enabled, lambda _r: self._refresh("wifi"), on)), "wifi")
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
        self._hero_done = True                         # the Bluetooth row below is the hero

        def fill(res):
            state, devices = res or (None, [])
            if state is None:
                top.add(self._hero(Adw.ActionRow(title="Bluetooth", subtitle="No Bluetooth adapter found"),
                                   "bluetooth"))
                return
            top.add(self._hero(switch_row("Bluetooth", state,
                                          lambda on: system.run_async(system.set_bluetooth, None, on),
                                          subtitle="This computer is discoverable while Bluetooth Settings is open"),
                               "bluetooth"))
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
        screen = group("Display")
        try:
            cur = int(system.wayfire_get("idle", "dpms_timeout", "600") or 600)
        except ValueError:
            cur = 600
        opts = [(60, "1 minute"), (120, "2 minutes"), (300, "5 minutes"), (600, "10 minutes"),
                (1200, "20 minutes"), (1800, "30 minutes"), (-1, "Never")]
        screen.add(combo_row("Turn display off after", opts, min((o[0] for o in opts), key=lambda v: abs(v - cur)),
                             lambda v: system.run_async(system.wayfire_set, None, "idle", "dpms_timeout", v)))
        return [info, mode, screen]

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

    # -- input (Wayfire [input]; applied live) --------------------------------------------------
    def _wf(self, key, value):
        system.run_async(system.wayfire_set, None, "input", key, value)

    def _page_keyboard(self):
        get = system.wayfire_get
        rep = group()
        rate = int(get("input", "kb_repeat_rate", "40") or 40)
        delay = int(get("input", "kb_repeat_delay", "400") or 400)
        rep.add(slider_row("Key Repeat", rate, 2, 80, lambda v: self._wf("kb_repeat_rate", int(v)),
                           ends=("Slow", "Fast")))
        # Delay Until Repeat: Long (left) .. Short (right), like macOS
        rep.add(slider_row("Delay Until Repeat", 1150 - delay, 150, 1000,
                           lambda v: self._wf("kb_repeat_delay", int(1150 - v)), ends=("Long", "Short")))
        src = group("Input Sources", "The first one is in use; switch from the input menu in the menu bar.")
        names = dict(system.XKB_LAYOUTS)
        lays = system.keyboard_layouts()
        for i, lay in enumerate(lays):
            r = Adw.ActionRow(title=names.get(lay, lay), subtitle="In use" if i == 0 else "", use_markup=False)
            rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                            tooltip_text="Remove", sensitive=len(lays) > 1)
            rm.connect("clicked", lambda _b, lay=lay: self._set_layouts([x for x in system.keyboard_layouts()
                                                                         if x != lay]))
            r.add_suffix(rm)
            src.add(r)
        add_opts = [("", "Add Input Source…")] + [o for o in system.XKB_LAYOUTS if o[0] not in lays]
        src.add(combo_row("Add", add_opts, "", lambda v: v and self._set_layouts(system.keyboard_layouts() + [v])))
        test = Adw.EntryRow(title="Type here to test")
        src.add(test)
        return [rep, src]

    def _set_layouts(self, lays):
        system.run_async(system.set_keyboard_layouts, lambda _r: self._reload_page("keyboard"), lays)

    def _reload_page(self, sid):
        """Rebuild a section (after its content changed)."""
        page = self.pages.pop(sid, None)
        if page is not None and self.split.get_content() is page:
            self.select(sid, from_sidebar=True)

    def _page_trackpad(self):
        get = system.wayfire_get
        g = group("Point & Click")
        g.add(slider_row("Tracking speed", _speed(get("input", "touchpad_cursor_speed", "0")), 0, 100,
                         lambda v: self._wf("touchpad_cursor_speed", round(v / 50 - 1, 2)), ends=("Slow", "Fast")))
        g.add(switch_row("Tap to click", get("input", "tap_to_click", "true") == "true",
                         lambda on: self._wf("tap_to_click", on), subtitle="Tap with one finger"))
        g.add(switch_row("Tap and drag", get("input", "tap_and_drag", "true") == "true",
                         lambda on: self._wf("tap_and_drag", on)))
        sc = group("Scroll & Zoom")
        sc.add(switch_row("Natural scrolling", get("input", "natural_scroll", "false") == "true",
                          lambda on: self._wf("natural_scroll", on),
                          subtitle="Content tracks finger movement"))
        sc.add(switch_row("Ignore trackpad while typing", get("input", "disable_touchpad_while_typing", "false")
                          == "true", lambda on: self._wf("disable_touchpad_while_typing", on)))
        return [g, sc]

    def _page_mouse(self):
        get = system.wayfire_get
        g = group()
        g.add(slider_row("Tracking speed", _speed(get("input", "mouse_cursor_speed", "0")), 0, 100,
                         lambda v: self._wf("mouse_cursor_speed", round(v / 50 - 1, 2)), ends=("Slow", "Fast")))
        try:
            scroll = float(get("input", "mouse_scroll_speed", "1") or 1)
        except ValueError:
            scroll = 1.0
        g.add(slider_row("Scrolling speed", scroll * 50, 5, 150,
                         lambda v: self._wf("mouse_scroll_speed", round(v / 50, 2)), ends=("Slow", "Fast")))
        g.add(switch_row("Natural scrolling", get("input", "mouse_natural_scroll", "false") == "true",
                         lambda on: self._wf("mouse_natural_scroll", on),
                         subtitle="Content tracks finger movement"))
        g.add(combo_row("Primary mouse button", [(False, "Left"), (True, "Right")],
                        get("input", "left_handed_mode", "false") == "true",
                        lambda v: self._wf("left_handed_mode", v)))
        return [g]

    # -- system --------------------------------------------------------------------------------
    def _page_datetime(self):
        from ..shell import topbar as T
        auto = group()
        clock = group("Clock")
        zone = group("Time Zone")

        def fill(res):
            on, cur, zones = res or (None, "UTC", [])
            if on is not None:
                auto.add(switch_row("Set date and time automatically", on,
                                    lambda v: system.run_async(system.set_ntp, lambda ok: ok or self.toast(
                                        "Couldn't change the setting"), v)))
            if zones:
                row = combo_row("Time zone", [(z, z.replace("_", " ")) for z in zones], cur,
                                lambda z: system.run_async(system.set_timezone, lambda ok: self.toast(
                                    f"Time zone: {z}" if ok else "Couldn't change the time zone"), z))
                row.set_enable_search(True)
                zone.add(row)
            else:
                zone.add(Adw.ActionRow(title="Time zone", subtitle=cur))
        system.run_async(lambda: (system.ntp(), system.timezone(), system.timezones()), fill)
        cfg = config.load("topbar", T.DEFAULTS)
        h24 = "%H" in cfg["clock_format"]
        clock.add(switch_row("Use a 24-hour clock", h24, lambda on: self._save(
            "topbar", "clock_format", cfg["clock_format"].replace("%-I:%M %p", "%H:%M") if on
            else cfg["clock_format"].replace("%H:%M", "%-I:%M %p"))))
        clock.add(switch_row("Show the date", "%d" in cfg["clock_format"], lambda on: self._save(
            "topbar", "clock_format", ("%a %-d %b  " if on else "%a ") + ("%H:%M" if "%H" in config.load(
                "topbar", T.DEFAULTS)["clock_format"] else "%-I:%M %p"))))
        return [auto, zone, clock]

    def _page_users(self):
        """Big Sur Users & Groups: your account (picture, name, password),
        other users (+ add, administrator, delete) and Login Items.
        Accounts go through AccountsService; polkit asks for an admin
        password where needed."""
        from ..backend import users as U
        me = group("Current User")
        others = group("Other Users")
        me.add(Adw.ActionRow(title="Loading…"))

        def fill(lst):
            for g in (me, others):
                _clear_group(g)
            if not lst:
                me.add(Adw.ActionRow(title=GLib.get_real_name() or GLib.get_user_name(), use_markup=False,
                                     subtitle="AccountsService is not available: accounts can't be changed here"))
                others.set_visible(False)
                return
            for u in lst:
                (me if u.current else others).add(self._user_row(u))
            if not any(not u.current for u in lst):
                others.add(Adw.ActionRow(title="No other users"))
            add = Adw.ButtonRow(title="Add User…", start_icon_name="list-add-symbolic") \
                if hasattr(Adw, "ButtonRow") else None
            if add is not None:
                add.connect("activated", lambda *_: self._add_user_dialog())
                others.add(add)
            else:
                row = Adw.ActionRow(title="Add User…", activatable=True)
                row.add_prefix(Gtk.Image(icon_name="list-add-symbolic"))
                row.connect("activated", lambda *_: self._add_user_dialog())
                others.add(row)
        system.run_async(U.users, fill)
        items = group("Login Items", "These apps open automatically when you log in.")
        for name, title, enabled in _login_items():
            items.add(switch_row(title, enabled, lambda on, n=name: _set_login_item(n, on)))
        if not _login_items():
            items.add(Adw.ActionRow(title="No login items", subtitle="Use Options > Open at Login in the Dock"))
        return [me, others, items]

    def _user_row(self, u):
        from ..backend import users as U
        row = Adw.ActionRow(title=u.real_name or u.name, use_markup=False,
                            subtitle=f"{u.name} · {'Admin' if u.admin else 'Standard'}")
        av = Adw.Avatar(size=44 if u.current else 32, text=u.real_name or u.name, show_initials=True)
        if u.icon:
            try:
                av.set_custom_image(Gdk.Texture.new_from_filename(u.icon))
            except GLib.Error:
                pass
        pic = Gtk.Button(child=av, css_classes=["flat", "circular"], valign=Gtk.Align.CENTER,
                         tooltip_text="Change picture")
        pic.connect("clicked", lambda *_: self._pick_picture(u))
        row.add_prefix(pic)
        if u.current:
            name = Gtk.Button(label="Edit Name…", valign=Gtk.Align.CENTER)
            name.connect("clicked", lambda *_: self._ask_text(
                "Full name", u.real_name, lambda v: self._user_op(U.set_real_name, u, v)))
            pw = Gtk.Button(label="Change Password…", valign=Gtk.Align.CENTER)
            pw.connect("clicked", lambda *_: self._password_dialog(u))
            row.add_suffix(name)
            row.add_suffix(pw)
        else:
            Item = ui.menu.Item
            more = Gtk.Button(icon_name="view-more-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER)
            more.connect("clicked", lambda b: ui.menu.popup(b, [
                [Item("Allow user to administer this computer", lambda on: self._user_op(U.set_admin, u, on),
                      checked=u.admin)],
                [Item("Reset Password…", lambda: self._password_dialog(u))],
                [Item("Delete User…", lambda: self._delete_user(u))]], position=Gtk.PositionType.BOTTOM))
            row.add_suffix(more)
        return row

    def _user_op(self, fn, *args, done_text=None):
        def done(err):
            if err:
                self.toast(err)
            elif done_text:
                self.toast(done_text)
            self._reload_page("users")
        system.run_async(lambda: fn(*args), done)

    def _pick_picture(self, u):
        from ..backend import users as U
        dlg = Gtk.FileDialog(title="Choose a picture", modal=True)
        flt = Gtk.FileFilter(name="Pictures")
        flt.add_mime_type("image/*")
        store = Gio.ListStore(item_type=Gtk.FileFilter)
        store.append(flt)
        dlg.set_filters(store)

        def chosen(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            if f and f.get_path():
                self._user_op(U.set_picture, u, f.get_path())
        dlg.open(self, None, chosen)

    def _ask_text(self, title, value, cb):
        entry = Gtk.Entry(text=value, activates_default=True, hexpand=True)
        dlg = ui.dialog.alert(title, "", [("cancel", "Cancel", ""), ("ok", "OK", "default")],
                              lambda r: r == "ok" and entry.get_text().strip() and cb(entry.get_text().strip()),
                              parent=self)
        dlg.set_extra_child(entry)

    def _password_dialog(self, u):
        from ..backend import users as U
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        new = Gtk.PasswordEntry(placeholder_text="New password", show_peek_icon=True)
        verify = Gtk.PasswordEntry(placeholder_text="Verify", show_peek_icon=True, activates_default=True)
        box.append(new)
        box.append(verify)

        def answer(r):
            if r != "ok":
                return
            if not new.get_text() or new.get_text() != verify.get_text():
                self.toast("The passwords don't match")
                return
            self._user_op(U.set_password, u, new.get_text(), done_text="Password changed")
        dlg = ui.dialog.alert(f"Change the password for “{u.real_name or u.name}”", "",
                              [("cancel", "Cancel", ""), ("ok", "Change Password", "default")], answer, parent=self)
        dlg.set_extra_child(box)

    def _add_user_dialog(self):
        from ..backend import users as U
        grid = Gtk.Grid(row_spacing=6, column_spacing=8)
        full = Gtk.Entry(hexpand=True)
        acct = Gtk.Entry(hexpand=True)
        pw = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        verify = Gtk.PasswordEntry(show_peek_icon=True, hexpand=True)
        admin = Gtk.CheckButton(label="Allow user to administer this computer")
        edited = {"acct": False}
        full.connect("changed", lambda e: not edited["acct"] and acct.set_text(U.short_name(e.get_text())))
        acct.connect("changed", lambda e: e.has_focus() and edited.update(acct=True))
        for r, (label, w) in enumerate((("Full Name:", full), ("Account Name:", acct), ("Password:", pw),
                                        ("Verify:", verify))):
            grid.attach(Gtk.Label(label=label, xalign=1), 0, r, 1, 1)
            grid.attach(w, 1, r, 1, 1)
        grid.attach(admin, 1, 4, 1, 1)

        def answer(r):
            if r != "create":
                return
            name = acct.get_text().strip()
            if not U.valid_name(name):
                self.toast("The account name can use lowercase letters, numbers, - and _")
                return
            if pw.get_text() != verify.get_text():
                self.toast("The passwords don't match")
                return
            self._user_op(U.create_user, name, full.get_text().strip() or name, admin.get_active(), pw.get_text(),
                          done_text=f"User “{name}” created")
        dlg = ui.dialog.alert("New Account", "", [("cancel", "Cancel", ""), ("create", "Create User", "default")],
                              answer, parent=self)
        dlg.set_extra_child(grid)

    def _delete_user(self, u):
        from ..backend import users as U
        ui.dialog.alert(f"Delete the user “{u.real_name or u.name}”?",
                        "Their home folder can be kept or deleted.",
                        [("cancel", "Cancel", ""), ("keep", "Keep Home Folder", ""),
                         ("delete", "Delete Home Folder", "destructive")],
                        lambda r: r in ("keep", "delete") and self._user_op(U.delete_user, u, r == "delete",
                                                                            done_text="User deleted"),
                        parent=self)

    def _page_sharing(self):
        g = group()
        entry = Adw.EntryRow(title="Computer Name", show_apply_button=True)
        entry.set_text(system.computer_name())
        entry.connect("apply", lambda e: system.run_async(
            system.set_computer_name, lambda ok: self.toast("Computer name changed" if ok else
                                                            "Couldn't change the name"), e.get_text().strip()))
        g.add(entry)
        g.add(Adw.ActionRow(title="Local hostname", subtitle=GLib.get_host_name() + ".local", use_markup=False))
        return [g]

    def _page_accessibility(self):
        I = "org.gnome.desktop.interface"
        disp = group("Display")
        anim = system.gsetting(I, "enable-animations")
        disp.add(switch_row("Reduce motion", anim == "false", lambda on: (
            system.set_gsetting(I, "enable-animations", "false" if on else "true"),
            system.run_async(system.wayfire_set, None, "animate", "open_animation", "fade" if on else "zoom"),
            system.run_async(system.wayfire_set, None, "animate", "close_animation", "fade" if on else "zoom"))))
        app = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        disp.add(switch_row("Reduce transparency", app.get("reduce_transparency", False),
                            lambda on: (self._save("appearance", "reduce_transparency", on),
                                        self.toast("Applies to windows opened from now on")),
                            subtitle="Solid sidebars and Dock instead of glass"))
        try:
            scale = float(system.gsetting(I, "text-scaling-factor") or 1)
        except ValueError:
            scale = 1.0
        disp.add(combo_row("Text size", [(1.0, "Default"), (1.15, "Large"), (1.3, "Larger")],
                           min((1.0, 1.15, 1.3), key=lambda v: abs(v - scale)),
                           lambda v: system.set_gsetting(I, "text-scaling-factor", str(v))))
        ptr = group("Pointer")
        try:
            size = int(system.gsetting(I, "cursor-size") or 24)
        except ValueError:
            size = 24
        ptr.add(combo_row("Pointer size", [(24, "Normal"), (32, "Large"), (48, "Larger")],
                          min((24, 32, 48), key=lambda v: abs(v - size)),
                          lambda v: (system.set_gsetting(I, "cursor-size", str(v)),
                                     system.run_async(system.wayfire_set, None, "input", "cursor_size", v))))
        return [disp, ptr]

    # -- Sonata sections ------------------------------------------------------------
    def _accent_row(self):
        """Big Sur "Accent colour": a row of colour dots (live everywhere)."""
        row = Adw.ActionRow(title="Accent colour", subtitle="Buttons, selections, switches and menus")
        box = Gtk.Box(valign=Gtk.Align.CENTER)
        cur = config.load("appearance", icons.APPEARANCE_DEFAULTS)["accent"]
        buttons = {}

        def pick(name):
            for n, b in buttons.items():
                (b.add_css_class if n == name else b.remove_css_class)("selected")
            self._save("appearance", "accent", name)
        for name in ui.tokens.ACCENTS:
            b = Gtk.Button(css_classes=["st-accent", name] + (["selected"] if name == cur else []),
                           tooltip_text=name.capitalize(), valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, n=name: pick(n))
            buttons[name] = b
            box.append(b)
        row.add_suffix(box)
        return row

    def _page_appearance(self):
        g = group("Appearance")
        scheme = system.gsetting("org.gnome.desktop.interface", "color-scheme") or "default"
        g.add(combo_row("Appearance", [("default", "Light"), ("prefer-dark", "Dark")],
                        "prefer-dark" if scheme == "prefer-dark" else "default",
                        lambda v: system.set_gsetting("org.gnome.desktop.interface", "color-scheme", v),
                        subtitle="Linux setting: every app follows it"))
        browsers = [(a.get_id(), a.get_display_name()) for a in Gio.AppInfo.get_all_for_type("x-scheme-handler/https")
                    if a.get_id()]
        if browsers:
            cur = system.default_browser()
            if cur not in [b[0] for b in browsers]:
                cur = browsers[0][0]
            g.add(combo_row("Default web browser", browsers, cur,
                            lambda v: system.run_async(system.set_default_browser, None, v)))
        g.add(self._accent_row())
        s = group("Sonata")
        app = config.load("appearance", icons.APPEARANCE_DEFAULTS)
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
        behave.add(switch_row("Animate opening applications", cfg["bounce"],
                              lambda on: self._save("dock", "bounce", on)))
        behave.add(switch_row("Show indicators for open applications", cfg["indicators"],
                              lambda on: self._save("dock", "indicators", on)))
        wins = group("Windows")
        wins.add(combo_row("Minimize windows using", [("genie", "Genie effect"), ("scale", "Scale effect")],
                           cfg["minimize_effect"], self._set_minimize_effect))
        dbl = system.gsetting("org.gnome.desktop.wm.preferences", "action-double-click-titlebar") or "toggle-maximize"
        wins.add(combo_row("Double-click a window's title bar to",
                           [("toggle-maximize", "Zoom"), ("minimize", "Minimize"), ("none", "Do Nothing")],
                           dbl if dbl in ("toggle-maximize", "minimize", "none") else "toggle-maximize",
                           lambda v: system.set_gsetting("org.gnome.desktop.wm.preferences",
                                                         "action-double-click-titlebar", v)))
        look = group("Look")
        look.add(switch_row("Translucent Dock", cfg["glass"], lambda on: self._save("dock", "glass", on),
                            subtitle="Frosted glass (off: solid)"))
        look.add(slider_row("Distance from the screen edge", cfg["edge_gap"], 0, 24,
                            lambda v: self._save("dock", "edge_gap", int(v))))
        look.add(slider_row("Space above the Dock for zoomed windows", cfg["window_gap"], 0, 24,
                            lambda v: self._save("dock", "window_gap", int(v))))
        return [size, behave, wins, look]

    def _set_minimize_effect(self, v):
        self._save("dock", "minimize_effect", v)
        system.run_async(system.wayfire_set, None, "animate", "minimize_animation",
                         "squeezimize" if v == "genie" else "zoom")

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
    """sonata2-settings.desktop (shown in Launchpad as "System Settings")."""
    from ..apps import write_desktop_file
    return write_desktop_file("sonata2-settings.desktop",
                              "[Desktop Entry]\nType=Application\nName=System Settings\n"
                              "Comment=Sonata and system settings\nIcon=preferences-system\n"
                              "Categories=Settings;System;\nStartupWMClass=io.github.vinioliveiras.sonata2.settings\n"
                              f"Exec={command} settings\n")
