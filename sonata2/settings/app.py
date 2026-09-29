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
import shutil

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, icons, ui  # noqa: E402
from ..backend import equalizer, system  # noqa: E402

SECTIONS = [  # id, title, icon, badge colour, group
    ("wifi", "Wi-Fi", "network-wireless-symbolic", "blue", "linux"),
    ("network", "Network", "network-wired-symbolic", "blue", "linux"),
    ("bluetooth", "Bluetooth", "bluetooth-active-symbolic", "blue", "linux"),
    ("printers", "Printers & Scanners", "printer-symbolic", "gray", "linux"),
    ("sound", "Sound", "audio-volume-high-symbolic", "pink", "linux"),
    ("displays", "Displays", "video-display-symbolic", "blue", "linux"),
    ("battery", "Battery", "battery-full-symbolic", "green", "linux"),
    ("wallpaper", "Wallpaper", "image-x-generic-symbolic", "teal", "linux"),
    ("keyboard", "Keyboard", "input-keyboard-symbolic", "gray", "input"),
    ("trackpad", "Trackpad", "input-touchpad-symbolic", "gray", "input"),
    ("mouse", "Mouse", "input-mouse-symbolic", "gray", "input"),
    ("datetime", "Date & Time", "preferences-system-time-symbolic", "blue", "system"),
    ("notifications", "Notifications", "preferences-system-notifications-symbolic", "red", "system"),
    ("users", "Users & Groups", "system-users-symbolic", "gray", "system"),
    ("privacy", "Security & Privacy", "security-high-symbolic", "gray", "system"),
    ("sharing", "Sharing", "folder-publicshare-symbolic", "blue", "system"),
    ("accessibility", "Accessibility", "preferences-desktop-accessibility-symbolic", "blue", "system"),
    ("appearance", "General", "preferences-system-symbolic", "gray", "sonata"),
    ("dock", "Desktop & Dock", "view-grid-symbolic", "black", "sonata"),
    ("menubar", "Menu Bar", "view-restore-symbolic", "indigo", "sonata"),
    ("launchpad", "Launchpad", "view-app-grid-symbolic", "graphite", "sonata"),
    ("updates", "Software Update", "software-update-available-symbolic", "gray", "about"),
    ("about", "About", "help-about-symbolic", "gray", "about"),
]

# Search (sidebar field, macOS Ventura): words that find a section besides its title.
KEYWORDS = {
    "wifi": "wireless network internet ssid password", "network": "ethernet vpn proxy wired ip",
    "bluetooth": "devices headphones mouse keyboard pair", "printers": "printer scanner cups print",
    "sound": "volume output input microphone speakers headphones effects alert equalizer eq bass treble",
    "displays": "screen monitor resolution refresh rate hz scale brightness night shift main display",
    "battery": "power energy low power mode charge sleep display off",
    "wallpaper": "background desktop picture", "keyboard": "layout input source repeat shortcuts",
    "trackpad": "touchpad tap click scroll gestures", "mouse": "pointer speed scroll natural",
    "datetime": "clock time zone date", "notifications": "do not disturb alerts banners",
    "users": "account password picture avatar login items", "privacy": "security lock screen location trash",
    "sharing": "file sharing remote", "accessibility": "zoom contrast reduce transparency motion graphics gpu hardware acceleration renderer",
    "appearance": "app icons regenerate frame generated dark light mode accent color theme icons font", "dock": "magnification size position autohide "
    "recent apps displays minimize", "menubar": "clock battery percentage bluetooth sound now playing",
    "launchpad": "apps grid folders hidden", "updates": "software update upgrade packages",
    "about": "computer system version restart sonata",
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
.sonata-settings .sidebar-pane { box-shadow: none; }
/* the line between the panes, on an opaque 1 px column like Files' */
/* equalizer bands: plain knobs on the track (no fill from the bottom) */
.st-eq scale > trough > highlight, .st-eq scale:disabled > trough > highlight {
  background-color: transparent; background-image: none; border-color: transparent; box-shadow: none; }
.st-divider { min-width: 1px; background: %(pane_bg)s; box-shadow: inset 1px 0 %(separator)s; }
.st-sidebar headerbar, .st-content headerbar { min-height: 52px; }   /* traffic lights where Files has them */
.st-sidebar headerbar, .st-sidebar toolbarview, .st-sidebar scrolledwindow,
.st-sidebar list { background: none; box-shadow: none; }
.st-content, .st-content toolbarview, .st-content headerbar { background: %(pane_bg)s; box-shadow: none; }
.st-badge { border-radius: 7px; padding: 4px; color: white; }
.st-badge.big { border-radius: 12px; padding: 10px; }
.st-badge.blue { background: %(sys_blue)s; } .st-badge.green { background: %(sys_green)s; }
.st-badge.pink { background: %(sys_pink)s; } .st-badge.teal { background: %(sys_teal)s; }
.st-badge.indigo { background: %(sys_indigo)s; } .st-badge.graphite { background: %(sys_graphite)s; }
.st-status { min-width: 8px; min-height: 8px; border-radius: 4px; margin-right: 4px; }
.st-status.on { background: %(sys_green)s; } .st-status.off { background: %(sys_red)s; }
.st-badge.gray { background: %(sys_gray)s; } .st-badge.red { background: %(sys_red)s; }
.st-badge.black { background: %(sys_black)s; box-shadow: inset 0 0 0 1px rgba(255,255,255,.18); }
.st-card { padding: 10px 12px 6px 12px; }
entry.st-search, .st-search { margin: 0 10px 6px 10px; min-height: 26px; border-radius: 7px; border: none;
  background: alpha(%(label)s, 0.07); box-shadow: none; font-size: %(text_body)s; }
.st-card-title { font-weight: 700; }
.st-card-sub { color: %(label_secondary)s; font-size: %(text_small)s; }
.st-gap-row, .st-gap-row:hover { min-height: 8px; padding: 0; margin: 0; background: none; }
.st-pane-title { font-weight: 700; font-size: %(text_title)s; color: %(label)s; }
.st-about-name { font-family: %(font_display)s; font-weight: 700; font-size: 26px; color: %(label)s; }
.st-caption { color: %(label_secondary)s; font-size: %(text_small)s; }
textview.st-log, textview.st-log text { background: transparent; font-family: %(font_mono)s; font-size: %(text_small)s; }
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


def slider_row(title, value, lower, upper, on_change, subtitle="", ends=None, default=None) -> Adw.ActionRow:
    """ends=("Slow", "Fast"): small labels under the slider's ends (macOS).
    default: a double-click on the slider resets it to this value."""
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    s = ui.controls.slider(value, on_change, lower=lower, upper=upper, default=default)
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
        # Resizable with a working Zoom button (Vini's call; macOS keeps it fixed).
        self.set_default_size(920, 640)
        self.set_size_request(760, 480)
        ui.window.standard(self)
        self.toasts = Adw.ToastOverlay()
        # Sidebar | content in a plain box (like Files): whole-pixel edges, no
        # see-through seam (AdwNavigationSplitView left a half-transparent
        # column between the panes). The window never gets narrow enough to
        # need the split view's collapsing.
        self.split = Gtk.Box(vexpand=True)
        side = self._sidebar()
        side.set_size_request(230, -1)
        side.add_css_class("sidebar-pane")
        self.split.append(side)
        self.split.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL, css_classes=["st-divider"]))
        # One content page; sections are stack children, cross-faded on switch.
        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.NONE)
        self.fade = ui.transition.CrossFade(self.content)
        self.split.append(Adw.NavigationPage(title="System Settings", child=self.fade, hexpand=True,
                                             css_classes=["st-content"]))
        self.current = None
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)
        self.pages = {}
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.select(start if start in [s[0] for s in SECTIONS] else "wifi")
        from ..backend import power                     # the Battery section follows plug/charge changes
        power.watch(lambda: self.current == "battery" and self._reload_page("battery"))

    def _key(self, _c, keyval, _code, state) -> bool:
        if keyval in (Gdk.KEY_f, Gdk.KEY_F) and state & Gdk.ModifierType.CONTROL_MASK:
            self.search.grab_focus()                       # Ctrl+F: the search field (Cmd+F)
            return True
        if keyval == Gdk.KEY_Escape:
            if self.search.get_text():
                self.search.set_text("")
            else:
                self.close()
            return True
        return False

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    # -- sidebar -------------------------------------------------------------------
    def _sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False, show_start_title_buttons=False, show_end_title_buttons=False)
        hb.pack_start(ui.window.traffic_lights(self.close, self.minimize,
                                                  lambda: self.unmaximize() if self.is_maximized()
                                                  else self.maximize()))
        tv.add_top_bar(hb)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card = Gtk.Box(spacing=10, css_classes=["st-card"])
        # your picture (like the Apple ID card); a click opens Users & Groups
        me = GLib.get_real_name() or GLib.get_user_name()
        self.card_avatar = Adw.Avatar(size=44, text=me, show_initials=True)
        card.append(self.card_avatar)
        self._refresh_card()
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.select("users"))
        card.add_controller(click)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=GLib.get_real_name() or GLib.get_user_name(), xalign=0,
                               css_classes=["st-card-title"]))
        texts.append(Gtk.Label(label="Sonata 2", xalign=0, css_classes=["st-card-sub"]))
        card.append(texts)
        box.append(card)
        # Search, macOS Ventura style: filters the sections as you type;
        # Return opens the first match
        self.search = Gtk.SearchEntry(placeholder_text="Search", css_classes=["st-search"])
        self.search.connect("search-changed", lambda *_: self._filter_sections())
        self.search.connect("activate", lambda *_: self._open_first_match())
        box.prepend(self.search)
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

    def _matches(self, sid, title, q) -> bool:
        words = f"{title} {KEYWORDS.get(sid, '')}".casefold()
        return all(w in words for w in q.split())

    def _filter_sections(self) -> None:
        q = self.search.get_text().strip().casefold()
        titles = {s[0]: s[1] for s in SECTIONS}
        row = self.listbox.get_first_child()
        while row is not None:
            sid = getattr(row, "sid", None)
            if sid is None:                                  # group gaps only without a search
                row.set_visible(not q)
            else:
                row.set_visible(not q or self._matches(sid, titles[sid], q))
            row = row.get_next_sibling()

    def _open_first_match(self) -> None:
        row = self.listbox.get_first_child()
        while row is not None:
            if getattr(row, "sid", None) and row.get_visible():
                self.listbox.select_row(row)
                return
            row = row.get_next_sibling()

    def select(self, sid, from_sidebar=False):
        if not from_sidebar:
            self.listbox.select_row(self.rows[sid])
            return
        if sid not in self.pages:
            title = next(s[1] for s in SECTIONS if s[0] == sid)
            page = Adw.PreferencesPage()
            groups = getattr(self, f"_page_{sid}")()      # no hero row: the pane title names the section
            for g in groups:
                page.add(g)
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
            hb.set_title_widget(Gtk.Label(label=title, css_classes=["st-pane-title"]))
            tv.add_top_bar(hb)
            tv.set_content(page)
            self.pages[sid] = tv
            self.content.add_named(tv, sid)
        if self.current is not None and self.current != sid:
            self.fade.capture()
        self.current = sid
        self.content.set_visible_child(self.pages[sid])
        self.fade.play()

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
        sw = switch_row("Wi-Fi", False, lambda on: system.run_async(
            system.set_wifi_enabled, lambda _r: self._refresh("wifi"), on))
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

    def _page_network(self):
        """Big Sur Network: the services (Ethernet, VPN, ...) with their
        status and address; VPNs are imported from .ovpn / WireGuard files."""
        svc = group("Services")
        svc.add(Adw.ActionRow(title="Loading…"))
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                         tooltip_text="Import VPN Configuration…")
        add.connect("clicked", lambda *_: self._import_vpn())
        svc.set_header_suffix(add)
        labels = {"ethernet": "Ethernet", "vpn": "VPN", "bridge": "Bridge", "bond": "Bond",
                  "mobile": "Mobile Broadband", "bluetooth": "Bluetooth PAN"}

        def fill(lst):
            _clear_group(svc)
            for n in lst or []:
                row = Adw.ExpanderRow(title=n.name, use_markup=False,
                                      subtitle=f"{labels.get(n.kind, n.kind.title())} · "
                                               + ("Connected" if n.active else "Not Connected"))
                dot = Gtk.Box(css_classes=["st-status", "on" if n.active else "off"], valign=Gtk.Align.CENTER)
                row.add_prefix(dot)
                row.add_row(switch_row("Connected", n.active, lambda on, n=n: system.run_async(
                    system.net_service_set, lambda r: (self.toast(r[1] if r and not r[0] else "Done"),
                                                       self._reload_page("network")), n.uuid, on)))
                for title, val in (("IP Address", n.address), ("Router", n.gateway), ("DNS Server", n.dns),
                                   ("Device", n.device)):
                    if val:
                        r = Adw.ActionRow(title=title, subtitle=val, use_markup=False, subtitle_selectable=True)
                        row.add_row(r)
                if n.kind == "vpn":
                    rm = Adw.ActionRow(title="Remove VPN", activatable=True)
                    rm.add_css_class("error")
                    rm.connect("activated", lambda *_a, n=n: system.run_async(
                        system.net_service_delete, lambda _ok: self._reload_page("network"), n.uuid))
                    row.add_row(rm)
                svc.add(row)
            if not lst:
                svc.add(Adw.ActionRow(title="No network services",
                                      subtitle="NetworkManager (nmcli) not available or nothing configured"))
        system.run_async(system.net_services, fill)
        return [svc]

    def _import_vpn(self):
        dlg = Gtk.FileDialog(title="Import VPN Configuration")
        f = Gtk.FileFilter(name="VPN configurations")
        for pat in ("*.ovpn", "*.conf"):
            f.add_pattern(pat)
        store = Gio.ListStore(item_type=Gtk.FileFilter)
        store.append(f)
        dlg.set_filters(store)

        def done(d, res):
            try:
                file = d.open_finish(res)
            except GLib.Error:
                return
            system.run_async(system.vpn_import, lambda r: (self.toast("VPN added" if r and r[0] else
                                                                      "Couldn't import: " + (r[1] if r else "")),
                                                           self._reload_page("network")), file.get_path())
        dlg.open(self, None, done)

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
            top.add(switch_row("Bluetooth", state,
                               lambda on: system.run_async(system.set_bluetooth, None, on),
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
            v, sinks, sources, mic = res or (None, [], [], None)
            if v is None:
                vol.add(Adw.ActionRow(title="Output volume", subtitle="PipeWire (wpctl) not found"))
                return
            vol.add(slider_row("Output volume", v[0], 0, 100,
                               lambda x: system.run_async(system.set_volume, None, int(x))))
            vol.add(switch_row("Mute", v[1], lambda on: system.run_async(system.set_volume, None, None, on)))
            if mic is not None:
                vol.add(slider_row("Input volume", mic[0], 0, 100,
                                   lambda x: system.run_async(system.set_input_volume, None, int(x))))
            options = [(s.key, s.name) for s in sinks]
            if options:
                cur = next((s.key for s in sinks if s.default), options[0][0])
                out.add(combo_row("Output device", options, cur,
                                  lambda k: system.run_async(system.select_output, None, k)))
            ins = [(s.key, s.name) for s in sources]
            if ins:
                cur = next((s.key for s in sources if s.default), ins[0][0])
                out.add(combo_row("Input device", ins, cur,
                                  lambda k: system.run_async(system.select_input, None, k)))
        eq = group("Equalizer", "Each output keeps its own settings")

        def fill_eq(sinks):
            if sinks:
                self._equalizer(eq, sinks)
            else:
                eq.set_visible(False)
        system.run_async(lambda: (system.volume(), system.audio_outputs(), system.audio_inputs(),
                                  system.input_volume()), lambda res: (fill(res), fill_eq(res and res[1]
                                                                                           if equalizer.available()
                                                                                           else None)))
        from ..sounds import DEFAULTS as SND
        snd = config.load("sounds", SND)
        effects = group("Sound Effects")
        effects.add(switch_row("Play user interface sound effects", snd["effects"],
                               lambda on: self._save("sounds", "effects", on),
                               subtitle="Moving to the Trash, emptying it, copies finished, screenshots"))
        effects.add(switch_row("Play feedback when volume is changed", snd["volume_feedback"],
                               lambda on: self._save("sounds", "volume_feedback", on)))
        return [vol, out, eq, effects]

    def _equalizer(self, grp, outputs) -> None:
        """Output picker (the one in use first), on/off, preset, ten bands."""
        E = equalizer
        keys = [(d.key, d.name) for d in outputs if "|" in d.key]
        if not keys:
            grp.set_visible(False)
            return
        state = {"key": next((d.key for d in outputs if d.default and "|" in d.key), keys[0][0])}
        names = list(E.PRESETS) + ["Custom"]
        on = Adw.SwitchRow(title="Equalizer")
        preset = Adw.ComboRow(title="Preset", model=Gtk.StringList.new(names))
        bands = Gtk.Box(homogeneous=True, spacing=6, margin_end=12)
        scales = []
        axis = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)          # dB scale, like Music's
        for i, t in enumerate(("+12 dB", "0 dB", "-12 dB")):
            axis.append(Gtk.Label(label=t, css_classes=["st-caption"], xalign=1, vexpand=True,
                                  valign=(Gtk.Align.START, Gtk.Align.CENTER, Gtk.Align.END)[i]))
        axis.set_size_request(-1, 150)
        axis_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        axis_col.append(axis)
        axis_col.append(Gtk.Label(label=" ", css_classes=["st-caption"]))
        for label in E.LABELS:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            sc = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -E.MAX_DB, E.MAX_DB, 0.5)
            sc.set_inverted(True)
            sc.set_draw_value(False)
            sc.set_size_request(-1, 150)
            sc.set_halign(Gtk.Align.CENTER)
            col.append(sc)
            col.append(Gtk.Label(label=label, css_classes=["st-caption"]))
            bands.append(col)
            ui.controls.reset_on_double_click(sc, 0.0)
            scales.append(sc)
        wrap = Gtk.Box(spacing=8, margin_start=12, margin_top=12, margin_bottom=12)
        wrap.append(axis_col)
        bands.set_hexpand(True)
        bands.set_margin_start(0)
        wrap.append(bands)
        wrap.add_css_class("st-eq")
        row = Gtk.ListBoxRow(activatable=False, selectable=False, child=wrap)
        busy = {"on": False}

        def show():
            c = E.curve(state["key"])
            busy["on"] = True
            on.set_active(c["on"])
            preset.set_selected(names.index(c["preset"]) if c["preset"] in names else len(names) - 1)
            for sc, g in zip(scales, c["gains"]):
                sc.set_value(g)
            for w in (preset, row):
                w.set_sensitive(c["on"])
            busy["on"] = False

        def changed_on(r, _p):
            if not busy["on"]:
                E.set_curve(state["key"], on=r.get_active())
                show()

        def changed_preset(r, _p):
            name = names[r.get_selected()]
            if not busy["on"] and name != "Custom":
                E.set_curve(state["key"], preset=name)
                show()
        pending = {"src": 0}

        def changed_band(*_a):
            if busy["on"]:
                return
            if pending["src"]:
                GLib.source_remove(pending["src"])

            def save():
                pending["src"] = 0
                E.set_curve(state["key"], gains=[sc.get_value() for sc in scales])
                c = E.curve(state["key"])
                busy["on"] = True
                preset.set_selected(names.index(c["preset"]) if c["preset"] in names else len(names) - 1)
                busy["on"] = False
                return False
            pending["src"] = GLib.timeout_add(120, save)     # dragging: save (and apply) ~8x/s
        on.connect("notify::active", changed_on)
        preset.connect("notify::selected", changed_preset)
        for sc in scales:
            sc.connect("value-changed", changed_band)
        if len(keys) > 1:
            grp.add(combo_row("Output", keys, state["key"], lambda k: (state.update(key=k), show())))
        grp.add(on)
        grp.add(preset)
        grp.add(row)
        show()

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
            if len(ds) > 1:
                from ..shell.monitors import DEFAULTS as MON
                cur_main = config.load("displays", MON)["main"]
                builtin = next((d.name for d in ds if d.name.startswith(("eDP", "LVDS", "DSI"))), ds[0].name)
                screens.add(combo_row("Main display", [(d.name, d.description or d.name) for d in ds],
                                      cur_main if cur_main in [d.name for d in ds] else builtin,
                                      lambda n: config.save("displays", {"main": n}),
                                      subtitle="Dock and desktop icons; every display gets a menu bar"))
            for d in ds:
                saved = system.display_mode_setting(d.name)
                opts = [("highrr", "Highest refresh rate")] + \
                    [(m, m.replace("@", " @ ").split(".")[0] + " Hz") for m in d.modes]
                screens.add(combo_row(d.name, opts, saved if saved in [o[0] for o in opts] else d.current,
                                      lambda m, d=d: system.run_async(system.set_display_mode, None, d.name, m),
                                      subtitle=d.description))
                screens.add(combo_row("Scale", [(1.0, "100 %"), (1.25, "125 %"), (1.5, "150 %"), (2.0, "200 %")],
                                      d.scale, lambda s, d=d: system.run_async(system.set_display_scale, None,
                                                                               d.name, s)))
            if not ds:
                screens.add(Adw.ActionRow(title="Displays", subtitle="wlr-randr not found or no outputs"))
        system.run_async(lambda: (system.brightness(), system.displays()), fill)
        return [bright, screens, self._night_shift_group()]

    def _night_shift_group(self):
        """Displays > Night Shift (macOS sheet as a group): schedule, custom
        times, Turn On Until Tomorrow, Colour Temperature."""
        import shutil
        from ..shell import nightshift
        cfg = config.load("nightshift", nightshift.DEFAULTS)
        g = group("Night Shift", "Night Shift shifts the colours of your display to the warmer end of the "
                                 "spectrum after dark." + ("" if shutil.which("wlsunset") else
                                                          " Needs wlsunset (not installed)."))
        g.set_sensitive(shutil.which("wlsunset") is not None)

        def save(**kw):
            c = config.load("nightshift", nightshift.DEFAULTS)
            c.update(kw)
            config.save("nightshift", c)
        times = [(f"{h:02d}:{m:02d}", f"{h:02d}:{m:02d}") for h in range(24) for m in (0, 30)]
        frm = combo_row("From", times, cfg["from"], lambda v: save(**{"from": v}))
        to = combo_row("To", times, cfg["to"], lambda v: save(to=v))

        def sched(v):
            save(schedule=v)
            frm.set_visible(v == "custom")
            to.set_visible(v == "custom")
        g.add(combo_row("Schedule", [("off", "Off"), ("custom", "Custom"), ("sunset", "Sunset to Sunrise")],
                        cfg["schedule"], sched))
        g.add(frm)
        g.add(to)
        frm.set_visible(cfg["schedule"] == "custom")
        to.set_visible(cfg["schedule"] == "custom")
        g.add(switch_row("Turn On Until Tomorrow", nightshift.manual_active(cfg), nightshift.set_manual,
                         subtitle="Manual"))
        g.add(slider_row("Colour Temperature", cfg["warmth"], 0, 100, lambda v: save(warmth=int(v)),
                         ends=("Less Warm", "More Warm"), default=50))
        return g

    def _page_battery(self):
        info = group()
        mode = group("Energy Mode")

        def fill(res):
            (pct, status), ac, prof = res or ((None, ""), False, None)
            level = Adw.ActionRow(title="Battery level", subtitle=f"{pct}% · {status}" if pct is not None
                                  else "No battery")
            if pct is not None or status:
                from ..backend import power               # the menu bar's icon, same rule
                level.add_prefix(Gtk.Image(icon_name=power.icon_name(pct, status, ac, prof), pixel_size=24))
            info.add(level)
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
        g = group("Wallpaper", "Sonata draws it; apps that show the desktop picture get it too.")
        uri = system.gsetting("org.gnome.desktop.background", "picture-uri") or ""
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, css_classes=["st-wall"], height_request=180,
                          can_shrink=True, overflow=Gtk.Overflow.HIDDEN)
        f = Gio.File.new_for_uri(uri) if uri else None
        if f and f.query_exists(None):
            pic.set_file(f)
        g.add(pic)
        chooser = group()                        # its own group: the page's standard gap below the picture
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
        chooser.add(row)
        return [g, chooser]

    # -- input (Wayfire [input]; applied live) --------------------------------------------------
    def _wf(self, key, value):
        system.run_async(system.wayfire_set, None, "input", key, value)

    def _page_keyboard(self):
        get = system.wayfire_get
        rep = group()
        rate = int(get("input", "kb_repeat_rate", "40") or 40)
        delay = int(get("input", "kb_repeat_delay", "400") or 400)
        rep.add(slider_row("Key Repeat", rate, 2, 80, lambda v: self._wf("kb_repeat_rate", int(v)),
                           ends=("Slow", "Fast"), default=40))
        # Delay Until Repeat: Long (left) .. Short (right), like macOS
        rep.add(slider_row("Delay Until Repeat", 1150 - delay, 150, 1000,
                           lambda v: self._wf("kb_repeat_delay", int(1150 - v)), ends=("Long", "Short"), default=750))
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
        if page is None:
            return
        showing = self.current == sid
        if showing:
            self.fade.capture()                       # rebuilt in place: fade, no flash
        self.content.remove(page)
        if showing:
            self.current = None
            self.select(sid, from_sidebar=True)

    def _page_trackpad(self):
        get = system.wayfire_get
        g = group("Point & Click")
        g.add(slider_row("Tracking speed", _speed(get("input", "touchpad_cursor_speed", "0")), 0, 100,
                         lambda v: self._wf("touchpad_cursor_speed", round(v / 50 - 1, 2)), ends=("Slow", "Fast"),
                         default=50))
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
                         lambda v: self._wf("mouse_cursor_speed", round(v / 50 - 1, 2)), ends=("Slow", "Fast"),
                         default=50))
        try:
            scroll = float(get("input", "mouse_scroll_speed", "1") or 1)
        except ValueError:
            scroll = 1.0
        g.add(slider_row("Scrolling speed", scroll * 50, 5, 150,
                         lambda v: self._wf("mouse_scroll_speed", round(v / 50, 2)), ends=("Slow", "Fast"),
                         default=50))
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
        pic.connect("clicked", lambda b: self._pick_picture(u, b))
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
            self._refresh_card()
        system.run_async(lambda: fn(*args), done)

    def _refresh_card(self):
        """The card's picture: AccountsService's (what the login screen
        shows), else ~/.face, else your initials."""
        def found(path):
            for p in (path, os.path.expanduser("~/.face")):
                if p and os.path.isfile(p):
                    try:
                        self.card_avatar.set_custom_image(Gdk.Texture.new_from_filename(p))
                        return
                    except GLib.Error:
                        continue

        def lookup():
            from ..backend import users as U
            return next((u.icon for u in U.users() if u.current), "")
        system.run_async(lookup, found)

    def _pick_picture(self, u, anchor=None):
        """Big Sur picture picker: Sonata's stock pictures, or a file."""
        from ..backend import users as U
        pop = Gtk.Popover(has_arrow=True, position=Gtk.PositionType.RIGHT)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=10, margin_bottom=10,
                      margin_start=10, margin_end=10)
        col.append(Gtk.Label(label="Pictures", xalign=0, css_classes=["heading"]))
        grid = Gtk.FlowBox(max_children_per_line=6, min_children_per_line=6, selection_mode=Gtk.SelectionMode.NONE,
                           row_spacing=6, column_spacing=6, homogeneous=True)

        def use(path):
            pop.popdown()
            self._user_op(U.set_picture, u, path, done_text="Picture changed")
        for path in U.stock_pictures():
            av = Adw.Avatar(size=48)
            try:
                av.set_custom_image(Gdk.Texture.new_from_filename(path))
            except GLib.Error:
                continue
            b = Gtk.Button(child=av, css_classes=["flat", "circular"], tooltip_text=os.path.basename(path)[:-4].title())
            b.connect("clicked", lambda _b, p=path: use(p))
            grid.append(b)
        col.append(grid)
        other = Gtk.Button(label="Choose from Files…")
        col.append(other)
        pop.set_child(col)

        def from_file(*_):
            pop.popdown()
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
                    use(f.get_path())
            dlg.open(self, None, chosen)
        other.connect("clicked", from_file)
        pop.set_parent(anchor or self)
        pop.connect("closed", lambda p: GLib.idle_add(lambda: (p.unparent(), False)[1]))
        pop.popup()

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

    def _page_notifications(self):
        """Big Sur Notifications: Do Not Disturb, then every app that has
        notified (allow, alert style, Notification Center)."""
        from .. import apps
        from ..shell.notifications import APP_DEFAULTS, DEFAULTS
        cfg = config.load("notifications", DEFAULTS)
        dnd = group("Do Not Disturb", "Banners stay hidden; notifications still collect in the "
                                      "Notification Center.")
        dnd.add(switch_row("Do Not Disturb", cfg.get("dnd"), lambda on: self._save("notifications", "dnd", on)))
        lst = group("Application Notifications")

        def set_app(key, field, value, row=None):
            c = config.load("notifications", DEFAULTS)
            c.setdefault("apps", {}).setdefault(key, dict(APP_DEFAULTS))[field] = value
            config.save("notifications", c)
            if row is not None:
                a = dict(APP_DEFAULTS, **c["apps"][key])
                row.set_subtitle(summary(a))

        def summary(a):
            if not a["allow"]:
                return "Off"
            return "Banners" if a["style"] == "banners" else "Notification Center only" if a["center"] else "None"
        entries = sorted((cfg.get("apps") or {}).items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower())
        for key, a in entries:
            a = dict(APP_DEFAULTS, **a)
            info = apps.lookup(key)
            name = info.get_display_name() if info else (a["name"] or key)
            row = Adw.ExpanderRow(title=name, subtitle=summary(a), use_markup=False)
            img = Gtk.Image(pixel_size=32)
            if info:
                icons.set_image(img, icons.app_icon(info))
            else:
                img.set_from_icon_name("application-x-executable")
            row.add_prefix(img)
            row.add_row(switch_row("Allow Notifications", a["allow"],
                                   lambda on, k=key, r=row: set_app(k, "allow", on, r)))
            row.add_row(combo_row("Alert style", [("banners", "Banners"), ("none", "None")], a["style"],
                                  lambda v, k=key, r=row: set_app(k, "style", v, r),
                                  subtitle="Banners appear at the top right and go away automatically"))
            row.add_row(switch_row("Show in Notification Center", a["center"],
                                   lambda on, k=key, r=row: set_app(k, "center", on, r)))
            lst.add(row)
        if not entries:
            lst.add(Adw.ActionRow(title="No notifications yet",
                                  subtitle="Apps appear here after they send their first notification"))
        return [dnd, lst]

    def _page_updates(self):
        """Ventura Software Update: the distro's name and state on top, then
        what can be updated per source (System, AUR, Flatpak), each with
        its versions. Update Now updates in place (backend/updates.py),
        with a progress bar and the output under Details; sources without
        an unattended mode, or a failed update, go to a terminal."""
        from .. import icons
        from ..backend import updates as U
        head = group()
        status = Adw.ActionRow(title="Checking for updates…", use_markup=False)
        logo = Gtk.Image(pixel_size=40, valign=Gtk.Align.CENTER)
        icons.set_logo(logo)
        status.add_prefix(logo)
        spin = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        buttons = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        status.add_suffix(spin)
        status.add_suffix(buttons)
        head.add(status)
        prog_row = Gtk.ListBoxRow(activatable=False, selectable=False, visible=False)
        bar = Gtk.ProgressBar(margin_start=12, margin_end=12, margin_top=10, margin_bottom=10,
                              valign=Gtk.Align.CENTER)
        prog_row.set_child(bar)
        head.add(prog_row)
        details = Adw.ExpanderRow(title="Details", visible=False)
        log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["st-log"],
                           wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                           left_margin=12, right_margin=12)
        scroll = Gtk.ScrolledWindow(child=log, min_content_height=180, max_content_height=180)
        details.add_row(Gtk.ListBoxRow(activatable=False, selectable=False, child=scroll))
        head.add(details)
        lst = group("Updates Available")
        lst.set_visible(False)
        state = {"srcs": [], "found": {}, "pending": 0, "rows": [], "pulse": 0, "runner": None}

        def clear_buttons():
            while (c := buttons.get_first_child()):
                buttons.remove(c)

        def button(label, cb, suggested=False):
            b = Gtk.Button(label=label, css_classes=["suggested-action"] if suggested else [])
            b.connect("clicked", lambda *_: cb())
            buttons.append(b)

        def total():
            return sum(len(v) for v in state["found"].values() if v)

        def check():
            clear_buttons()
            for r in state["rows"]:
                lst.remove(r)
            state["rows"].clear()
            lst.set_visible(False)
            spin.set_visible(True)
            status.set_title("Checking for updates…")
            status.set_subtitle("")
            state["srcs"] = U.sources()
            state["found"] = {}
            state["pending"] = len(state["srcs"])
            if not state["srcs"]:
                spin.set_visible(False)
                status.set_title("Updates can't be checked here")
                status.set_subtitle("No supported package manager found (pacman, dnf, apt, zypper, Flatpak)")
                return
            for src in state["srcs"]:
                system.run_async(U.check, lambda ups, s=src: checked(s, ups), src)

        def checked(src, ups):
            state["found"][src.id] = ups
            state["pending"] -= 1
            if state["pending"] == 0:
                for s in state["srcs"]:                     # in the sources' order
                    add_list(s, state["found"].get(s.id))
                summary()

        def add_list(src, ups):
            if ups:
                exp = Adw.ExpanderRow(title=src.title, use_markup=False,
                                      subtitle=f"{len(ups)} update{'s' if len(ups) != 1 else ''}")
                for u in ups[:300]:
                    r = Adw.ActionRow(title=u.name, use_markup=False)
                    if u.new:
                        r.add_suffix(Gtk.Label(label=f"{u.old} → {u.new}" if u.old else u.new,
                                               css_classes=["st-caption"], ellipsize=Pango.EllipsizeMode.MIDDLE,
                                               max_width_chars=36))
                    exp.add_row(r)
                lst.add(exp)
                state["rows"].append(exp)
                lst.set_visible(True)

        def summary():
            spin.set_visible(False)
            clear_buttons()
            failed = [s.title for s in state["srcs"] if state["found"].get(s.id) is None]
            n = total()
            when = GLib.DateTime.new_now_local().format("%H:%M")
            if n:
                status.set_title(f"{n} update{'s' if n != 1 else ''} available")
                status.set_subtitle(f"Checked at {when}" + (f" · {', '.join(failed)} couldn't be checked"
                                                             if failed else ""))
                button("Update Now", update, suggested=True)
            else:
                status.set_title("Your computer is up to date")
                status.set_subtitle(f"Checked at {when}" + (f" · {', '.join(failed)} couldn't be checked"
                                                             if failed else ""))
                button("Check Again", check)

        def todo():
            return [s for s in state["srcs"] if state["found"].get(s.id)]

        def in_terminal():
            cmd = " && ".join(s.terminal for s in todo())
            if not system.run_in_terminal(cmd):
                self.toast("No terminal found")

        def say(text):
            buf = log.get_buffer()
            buf.insert(buf.get_end_iter(), text + "\n")
            if buf.get_line_count() > 4000:                  # keep the log light
                buf.delete(buf.get_start_iter(), buf.get_iter_at_line(1000)[1])
            log.scroll_to_mark(buf.get_insert(), 0, False, 0, 0)
            buf.place_cursor(buf.get_end_iter())

        def fraction(f):
            if f is None:
                if not state["pulse"]:
                    state["pulse"] = GLib.timeout_add(120, lambda: (bar.pulse(), True)[1])
                return
            if state["pulse"]:
                GLib.source_remove(state["pulse"])
                state["pulse"] = 0
            bar.set_fraction(f)

        def update():
            srcs = todo()
            if any(s.install is None for s in srcs):     # apt/dnf/zypper: their own terminal flow
                in_terminal()
                return
            clear_buttons()
            names = [s.title for s in srcs]
            status.set_title("Updating…")
            status.set_subtitle(", ".join(names) + " · you may be asked for your password")
            prog_row.set_visible(True)
            details.set_visible(True)
            log.get_buffer().set_text("")
            kernel = any(u.name.startswith(("linux", "nvidia")) and not u.name.startswith("linux-firmware")
                         for u in state["found"].get("system") or [])
            steps = [s.install for s in srcs]

            def line(t):
                say(t)
                i = state["runner"].index
                if 0 <= i < len(srcs):
                    status.set_title(f"Updating {srcs[i].title}…")

            def done(ok, failed_at):
                fraction(0.0)
                prog_row.set_visible(False)
                state["runner"] = None
                clear_buttons()
                if ok:
                    for r in state["rows"]:
                        lst.remove(r)
                    state["rows"].clear()
                    lst.set_visible(False)
                    status.set_title("Your computer is up to date")
                    if kernel:
                        status.set_subtitle("Restart to finish installing the system updates")
                        button("Restart…", lambda: system._spawn(["systemctl", "reboot"]), suggested=True)
                    else:
                        status.set_subtitle("Updated at " + GLib.DateTime.new_now_local().format("%H:%M"))
                        button("Check Again", check)
                else:
                    status.set_title(f"{srcs[failed_at].title} couldn't be updated")
                    status.set_subtitle("See Details, or update in a terminal")
                    details.set_expanded(True)
                    button("Open in Terminal", in_terminal)
                    button("Try Again", update, suggested=True)
            # the app stays alive (the log keeps being read) if the window closes mid-update:
            # a closed pipe would stop pacman halfway
            app = self.get_application()
            app.hold()

            def finished(ok, failed_at):
                app.release()
                done(ok, failed_at)
            state["runner"] = U.Runner(steps, line, fraction, finished)
            state["runner"].start()
        check()
        return [head, lst]

    def _apply_titlebars(self):
        from .. import titlebars
        system.run_async(titlebars.apply_colors, None, Adw.StyleManager.get_default().get_dark())

    def _page_privacy(self):
        """Big Sur Security & Privacy, with the Linux settings behind it."""
        import shutil
        from ..shell.idlelock import DEFAULTS as SEC
        sec = config.load("security", SEC)
        gen = group("General", "" if shutil.which("swayidle") else "Automatic locking needs swayidle "
                                                                    "(not installed).")
        opts = [(-1, "Never"), (0, "Immediately"), (5, "5 seconds"), (60, "1 minute"), (300, "5 minutes"),
                (900, "15 minutes"), (3600, "1 hour")]
        gen.add(combo_row("Require password after the display turns off", opts, sec["lock_after"],
                          lambda v: self._save("security", "lock_after", v)))
        gen.add(switch_row("Lock before sleep", sec["lock_before_sleep"],
                           lambda on: self._save("security", "lock_before_sleep", on)))
        gen.set_sensitive(shutil.which("swayidle") is not None)
        P = "org.gnome.desktop.privacy"
        priv = group("Privacy")
        rec = system.gsetting(P, "remember-recent-files")
        priv.add(switch_row("Remember recent files", rec != "false",
                            lambda on: system.set_gsetting(P, "remember-recent-files", "true" if on else "false"),
                            subtitle="Recents in Files and the Open dialogs"))
        clear = Adw.ActionRow(title="Clear Recent Items", activatable=True)
        clear.add_suffix(Gtk.Image(icon_name="edit-clear-all-symbolic"))

        def do_clear(*_):
            try:
                Gtk.RecentManager.get_default().purge_items()
                self.toast("Recent items cleared")
            except GLib.Error:
                pass
        clear.connect("activated", do_clear)
        priv.add(clear)
        trash = system.gsetting(P, "remove-old-trash-files")
        priv.add(switch_row("Remove items from the Trash after 30 days", trash == "true",
                            lambda on: (system.set_gsetting(P, "remove-old-trash-files", "true" if on else "false"),
                                        system.set_gsetting(P, "old-files-age", "uint32 30"))))
        loc = system.gsetting("org.gnome.system.location", "enabled")
        if loc is not None:
            priv.add(switch_row("Location Services", loc == "true",
                                lambda on: system.set_gsetting("org.gnome.system.location", "enabled",
                                                               "true" if on else "false"),
                                subtitle="Apps may ask for your location (GeoClue)"))
        return [gen, priv]

    def _page_printers(self):
        g = group("Printers")
        g.add(Adw.ActionRow(title="Loading…"))
        add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["flat"], valign=Gtk.Align.CENTER,
                         tooltip_text="Add Printer…")
        add.connect("clicked", lambda *_: system.add_printer())
        g.set_header_suffix(add)

        def fill(lst):
            _clear_group(g)
            if lst is None:
                g.add(Adw.ActionRow(title="Printing isn't set up", subtitle="Install CUPS to add printers"))
                add.set_sensitive(False)
                return
            for p in lst:
                row = Adw.ActionRow(title=p.name, use_markup=False,
                                    subtitle=p.state + (" · Default" if p.default else ""))
                row.add_prefix(Gtk.Image(icon_name="printer-symbolic", pixel_size=24))
                if not p.default:
                    b = Gtk.Button(label="Make Default", valign=Gtk.Align.CENTER)
                    b.connect("clicked", lambda *_a, p=p: system.run_async(
                        system.set_default_printer, lambda _ok: self._reload_page("printers"), p.name))
                    row.add_suffix(b)
                g.add(row)
            if not lst:
                g.add(Adw.ActionRow(title="No printers available", subtitle="Click + to add a printer"))
        system.run_async(system.printers, fill)
        return [g]

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
                                        self._apply_titlebars()),
                            subtitle="Solid sidebars, Dock and title bars instead of glass"))
        disp.add(combo_row("Graphics", [("gl", "Hardware (OpenGL)"), ("vulkan", "Hardware (Vulkan)"),
                                        ("software", "Software (no GPU)")],
                           app.get("renderer", "gl"),
                           lambda v: (self._save("appearance", "renderer", v),
                                      self.toast("Applies after Restart Sonata")),
                           subtitle="How Sonata draws its Dock, menu bar and windows"))
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
                        lambda v: system.run_async(system.set_dark_mode, None, v == "prefer-dark"),
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
        from ..ui import logo as L
        s.add(combo_row("Menu bar logo", L.choices(), app["menu_logo"],
                        lambda v: self._save("appearance", "menu_logo", v),
                        subtitle="Where the Apple logo is on a Mac"))
        s.add(switch_row("Sonata title bars for all apps", app["system_titlebars"],
                         lambda on: (self._save("appearance", "system_titlebars", on),
                                     __import__("sonata2.titlebars", fromlist=["apply"]).apply(on),
                                     self.toast("Apps pick it up when they open again")),
                         subtitle="Chrome, VS Code and others use Sonata's title bar instead of their own"))
        gen = Adw.ActionRow(title="App icons made by Sonata",
                            subtitle="Apps without Sonata artwork get their icon on the standard frame, saved on disk")
        regen = Gtk.Button(label="Regenerate", valign=Gtk.Align.CENTER)
        regen.connect("clicked", lambda *_: (icons.clear_generated(), system.restart_sonata(),
                                             self.toast("Making the app icons again…")))
        gen.add_suffix(regen)
        s.add(gen)
        from .. import flatpak_theme
        if shutil.which("flatpak"):
            s.add(switch_row("Sonata style for Flatpak apps", flatpak_theme.enabled(),
                             lambda on: (self._save("appearance", flatpak_theme.KEY, on),
                                         system.run_async(flatpak_theme.apply if on else flatpak_theme.remove),
                                         self.toast("Flatpak apps pick it up when they open again")),
                             subtitle="Also changes Flatpak apps in other desktops' sessions while on"))
        dock = config.load("dock", {"glass": True})
        s.add(switch_row("Translucent glass", dock["glass"],
                         lambda on: (self._save("dock", "glass", on), self._apply_titlebars()),
                         subtitle="Frosted Dock, menu bar, menus, sidebars and title bars "
                                  "(needs the Wayfire blur plugin)"))
        return [g, s]

    def _page_dock(self):
        from ..shell import dock as D
        cfg = config.load("dock", D.DEFAULTS)
        size = group("Dock")
        size.add(slider_row("Size", cfg["icon_size"], D.MIN_SIZE, D.MAX_SIZE,
                            lambda v: self._save("dock", "icon_size", int(v)), default=D.DEFAULTS["icon_size"]))
        size.add(switch_row("Magnification", cfg["magnification"], lambda on: self._save("dock", "magnification", on)))
        size.add(slider_row("Magnified size", cfg["magnified_size"], D.MIN_SIZE, D.MAX_SIZE,
                            lambda v: self._save("dock", "magnified_size", int(v)),
                            default=D.DEFAULTS["magnified_size"]))
        size.add(combo_row("Position on screen", [("left", "Left"), ("bottom", "Bottom"), ("right", "Right")],
                           cfg["position"], lambda v: self._save("dock", "position", v)))
        behave = group()
        behave.add(switch_row("Automatically hide and show the Dock", cfg["autohide"],
                              lambda on: self._save("dock", "autohide", on)))
        behave.add(switch_row("Show the Dock on every display", cfg.get("all_displays", False),
                              lambda on: self._save("dock", "all_displays", on),
                              subtitle="Otherwise only on the main display (Displays)"))
        behave.add(switch_row("Show suggested and recent apps in Dock", cfg["show_recents"],
                              lambda on: self._save("dock", "show_recents", on)))
        behave.add(switch_row("Click an app in front to minimize it", cfg["click_minimizes"],
                              lambda on: self._save("dock", "click_minimizes", on)))
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
        look.add(switch_row("Translucent glass", cfg["glass"],
                            lambda on: (self._save("dock", "glass", on), self._apply_titlebars()),
                            subtitle="The Dock, menu bar, menus and title bars (off: solid)"))
        look.add(slider_row("Distance from the screen edge", cfg["edge_gap"], 0, 24,
                            lambda v: self._save("dock", "edge_gap", int(v)), default=D.DEFAULTS["edge_gap"]))
        look.add(slider_row("Space above the Dock for zoomed windows", cfg["window_gap"], 0, 24,
                            lambda v: self._save("dock", "window_gap", int(v)), default=D.DEFAULTS["window_gap"]))
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
        g.add(switch_row("Show Bluetooth in menu bar", cfg["show_bluetooth"],
                         lambda on: self._save("topbar", "show_bluetooth", on)))
        g.add(switch_row("Show Sound in menu bar", cfg["show_sound"],
                         lambda on: self._save("topbar", "show_sound", on),
                         subtitle="Volume and outputs are always in Control Center"))
        g.add(switch_row("Show Now Playing in menu bar", cfg["show_now_playing"],
                         lambda on: self._save("topbar", "show_now_playing", on),
                         subtitle="While something plays"))
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
        logo = Gtk.Image(pixel_size=96, halign=Gtk.Align.CENTER)
        from .. import icons
        icons.set_logo(logo)                      # the distro's logo (os-release LOGO=)
        box.append(logo)
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
        shell = group("Sonata")
        row = Adw.ActionRow(title="Restart Sonata",
                            subtitle="Reloads the Dock, menu bar, Launchpad and wallpaper. Your apps stay open.")
        btn = Gtk.Button(label="Restart", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: (system.restart_sonata(), self.toast("Restarting Sonata…")))
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        shell.add(row)
        return [hero, specs, shell]


def settings_desktop_file(command: str) -> str:
    """sonata2-settings.desktop (shown in Launchpad as "System Settings")."""
    from ..apps import write_desktop_file
    return write_desktop_file("sonata2-settings.desktop",
                              "[Desktop Entry]\nType=Application\nName=System Settings\n"
                              "Comment=Sonata and system settings\nIcon=preferences-system\n"
                              "Categories=Settings;System;\nStartupWMClass=io.github.vinioliveiras.sonata2.settings\n"
                              f"Exec={command} settings\n")
