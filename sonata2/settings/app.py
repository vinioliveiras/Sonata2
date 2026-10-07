"""Sonata Settings (macOS System Settings layout, from the LayerOSX panel):
sidebar with coloured badges + one pane per section, fixed-size window
with traffic lights (zoom greyed out, like System Settings).

The sidebar keeps Vini's split:
  Linux  -- Wi-Fi, Bluetooth, Sound, Displays, Battery, Wallpaper: read and
            written through the system services (backend/system.py),
            nothing stored by Sonata;
  Sonata -- Appearance, Dock, Menu Bar, Control Center, Desktop & Windows, Launchpad: Sonata's own
            settings (~/.config/sonata2/*.json); the shell components watch
            those files and apply changes live;
  About.
`python3 -m sonata2 settings [--page ID]`."""
import os
import shutil
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, icons, names, ui  # noqa: E402
from ..backend import equalizer, system  # noqa: E402

SECTIONS = [  # id, title, icon, badge colour, group (colours varied, not mostly grey: Vini)
    ("wifi", "Wi-Fi", "network-wireless-symbolic", "blue", "linux"),
    ("network", "Network", "network-wired-symbolic", "indigo", "linux"),
    ("bluetooth", "Bluetooth", "bluetooth-active-symbolic", "blue", "linux"),
    ("sound", "Sound", "audio-volume-high-symbolic", "pink", "linux"),
    ("displays", "Displays", "video-display-symbolic", "teal", "linux"),
    ("battery", "Battery", "battery-full-symbolic", "green", "linux"),
    ("keyboard", "Keyboard", "input-keyboard-symbolic", "graphite", "input"),
    ("mouse", "Mouse & Trackpad", "input-mouse-symbolic", "gray", "input"),
    ("gamepad", "Game Controllers", "input-gaming-symbolic", "orange", "input"),
    ("printers", "Printers & Scanners", "printer-symbolic", "gray", "input"),
    ("appearance", "Appearance", "preferences-desktop-appearance-symbolic", "indigo", "sonata"),
    ("appicons", "App Icons", "applications-graphics-symbolic", "pink", "sonata"),
    # Dock, Menu Bar, Control Center, Desktop & Windows: what each does (Vini);
    # how they look (glass, transparency, corners) stays in Appearance
    ("dock", "Dock", "view-grid-symbolic", "black", "sonata"),
    ("menubar", "Menu Bar", "panel-top-symbolic", "blue", "sonata"),
    ("controlcenter", "Control Center", "sonata-control-center-symbolic", "purple", "sonata"),
    ("desktop", "Desktop & Windows", "user-desktop-symbolic", "teal", "sonata"),
    ("launchpad", names.APPS, "view-app-grid-symbolic", "graphite", "sonata"),
    ("notifications", "Notifications", "preferences-system-notifications-symbolic", "red", "sonata"),
    ("users", "Users & Groups", "system-users-symbolic", "orange", "system"),
    ("defaults", "Default Apps", "emblem-default-symbolic", "purple", "system"),
    ("privacy", "Security & Privacy", "security-high-symbolic", "indigo", "system"),
    ("accessibility", "Accessibility", "preferences-desktop-accessibility-symbolic", "blue", "system"),
    ("datetime", "Date & Time", "preferences-system-time-symbolic", "green", "system"),
    ("about", "About", "help-about-symbolic", "gray", "about"),
]

# Sections made of several parts (each part one _page_<part> builder, shown
# one after the other). The parts' old ids still open their section
# (--page wallpaper, "Change Desktop Background…", "Software Update").
PARTS = {
    "displays": ("displays", "wallpaper"),
    "keyboard": ("keyboard", "shortcuts"),
    "mouse": ("trackpad", "mouse"),
    "launchpad": ("launchpad", "hidden", "apps"),      # Apps: the grid, hidden apps, installed apps (Vini)
    "privacy": ("privacy", "sharing"),
    "about": ("about", "sonataupdate", "updates"),     # Sonata's own update, then the system's (Vini)
}
PART_TITLES = {"wallpaper": "Wallpaper", "shortcuts": "Keyboard Shortcuts", "trackpad": "Trackpad",
               "hidden": "Hidden & Protected Apps", "apps": "Installed Apps", "sharing": "Sharing",
               "updates": "Software Update", "sonataupdate": "Sonata Update"}


def section_of(sid: str) -> str:
    """The section showing `sid` (itself, or the one it was merged into)."""
    for sec, parts in PARTS.items():
        if sid in parts:
            return sec
    return sid


def parts_of(sid: str) -> tuple:
    return PARTS.get(sid, (sid,))


class _Pages(dict):
    """Built sections by id; a merged part's old id finds its section."""

    def __getitem__(self, k):
        return dict.__getitem__(self, section_of(k))

    def __contains__(self, k):
        return dict.__contains__(self, section_of(k))

    def get(self, k, default=None):
        return dict.get(self, section_of(k), default)

    def pop(self, k, *default):
        return dict.pop(self, section_of(k), *default)


# Search (sidebar field, macOS Ventura): words that find a section besides its title.
KEYWORDS = {
    "wifi": "wireless network internet ssid password", "network": "ethernet vpn proxy wired ip",
    "bluetooth": "devices headphones mouse keyboard pair", "printers": "printer scanner cups print",
    "sound": "volume output input microphone speakers headphones effects alert equalizer eq bass treble",
    "displays": "screen monitor resolution refresh rate hz scale brightness night shift main display  arrange arrangement main display position"
                "rounded corners",
    "battery": "power energy low power mode charge sleep display off",
    "wallpaper": "background desktop picture photo mountains", "keyboard": "layout input source repeat shortcuts",
    "trackpad": "touchpad tap click scroll gestures", "mouse": "pointer speed scroll natural",
    "shortcuts": "keyboard shortcuts keys hotkeys windows super win snap desktop lock screenshot",
    "gamepad": "game controller gamepad xbox playstation dualsense joystick steam",
    "datetime": "clock time zone date", "notifications": "do not disturb alerts banners",
    "users": "account password picture avatar login items", "privacy": "security lock screen location trash",
    "sharing": "file sharing remote", "accessibility": "zoom contrast reduce transparency motion graphics gpu hardware acceleration renderer",
    "appearance": "app icons regenerate frame generated dark light mode accent color theme icons font "
                  "glass transparency translucent blur frosted title bars corners radius",
    "dock": "magnification size position autohide recent apps displays indicators bounce edge",
    "desktop": "windows minimize genie scale resize resizing window contents title bar double-click zoom "
               "quit close background last window "
               "desktop icons sort name kind date",
    "controlcenter": "control center modules controls layout add remove reset cpu gpu memory network fps "
                     "temperature video memory vram mixer",
    "defaults": "default apps open with web browser chrome firefox mail email calendar music player video "
                "photos pictures images viewer pdf text editor folders file manager",
    "apps": "apps applications uninstall remove delete clear data storage size permissions camera microphone "
            "network location background notifications flatpak lock",
    "appicons": "icon icons app shape squircle circle rounded custom picture image package theme",
    "menubar": "clock battery percentage bluetooth sound now playing logo text automatically hide show wifi wi-fi search input source keyboard background apps cpu gpu memory ram network fps performance",
    "launchpad": "apps grid folders launchpad", "hidden": "hide hidden protected private lock password apps", "updates": "software update upgrade packages",
    "about": "computer system version restart sonata",
    "sonataupdate": "sonata update new version release what's new",
}

for _sec, _parts in PARTS.items():           # a merged section is found by its parts' words and titles
    KEYWORDS[_sec] = " ".join([KEYWORDS.get(p, "") for p in _parts] +
                              [PART_TITLES.get(p, "") for p in _parts]).strip()

# Sections showing Sonata/desktop settings other places change too (Control
# Center, the menu bar, the Setup Assistant, another section here): built
# again when shown if one of these files changed since (no stale switches).
PAGE_CONFIGS = {
    "sound": ("sounds",), "displays": ("displays", "nightshift"), "wallpaper": ("system",),
    "datetime": ("topbar",), "notifications": ("notifications",), "privacy": ("security", "system"),
    "accessibility": ("appearance", "system"), "appearance": ("appearance", "dock", "system"),
    "dock": ("dock", "system"), "menubar": ("topbar", "appearance"), "gamepad": ("gamepad",),
    "desktop": ("dock", "desktop", "system"), "controlcenter": ("controlcenter",),
}
for _sec, _parts in PARTS.items():           # a merged section: every part's files
    PAGE_CONFIGS[_sec] = tuple(dict.fromkeys(n for p in _parts for n in PAGE_CONFIGS.get(p, ())))

_ACCENT_CSS = "".join(f".st-accent.{n} {{ background: {c[0]}; }}\n" for n, c in ui.tokens.ACCENTS.items())
ui.register(_ACCENT_CSS + """
button.st-accent { min-width: 16px; min-height: 16px; padding: 0; margin: 0 3px; border-radius: 99px; border: none;
  box-shadow: inset 0 0 0 0.5px rgba(0,0,0,0.2); transition: box-shadow %(t_fast)s; }
button.st-accent.selected { box-shadow: 0 0 0 2px %(window_bg)s, 0 0 0 3.5px alpha(%(label)s, 0.45); }
""", key="settings-accent")

ui.register("""
window.sonata-settings { color: %(label)s; }
/* a row that belongs to the one above (Glass: Transparency under its switch) */
row.st-sub-row > box.header { margin-left: 16px; }
row.st-sub-row { transition: opacity %(t_fast)s ease-out; }
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
.st-badge.orange { background: %(sys_orange)s; } .st-badge.purple { background: %(sys_purple)s; }
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
.st-wall { border-radius: 10px; background: alpha(%(label)s, 0.06); }   /* an empty frame shows too */
/* Sonata's wallpapers (Settings > Wallpaper): a ring on the one in use */
button.st-wall-tile { padding: 0; border: none; border-radius: 9px; background: none; box-shadow: none;
  transition: box-shadow 160ms ease-out; }
button.st-wall-tile picture { border-radius: 8px; }
button.st-wall-tile:hover { box-shadow: 0 0 0 2px alpha(%(label)s, 0.25); }
button.st-wall-tile.selected { box-shadow: 0 0 0 2px %(window_bg)s, 0 0 0 4px %(accent)s; }
.st-wall-name { color: %(label_secondary)s; font-size: %(text_small)s; }
.st-value { color: %(label_secondary)s; }        /* a row's value (About), body size like macOS */
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


SLIDER_W = 240          # every slider row's slider: same width, same leading edge (macOS)


def group(title="", description="") -> Adw.PreferencesGroup:
    # titles/descriptions are markup: "Point & Click" would come out blank
    return Adw.PreferencesGroup(title=GLib.markup_escape_text(title),
                                description=GLib.markup_escape_text(description))


def switch_row(title, active, on_change, subtitle="") -> Adw.SwitchRow:
    # plain text: app, device and network names may hold "&" or "<"
    row = Adw.SwitchRow(title=title, subtitle=subtitle, active=bool(active), use_markup=False)
    row.connect("notify::active", lambda r, _p: getattr(r, "quiet", False) or on_change(r.get_active()))
    return row


BUTTON_KEYS = ("button_layout", "button_order", "left_button_spacing", "left_button_x_offset",
               "right_button_spacing", "right_button_x_offset")


def apply_buttons_side() -> None:
    """The window buttons' side (appearance.json) for everything drawn outside
    Sonata's own windows: Wayfire's title bars (pixdecor, decoration), GNOME's
    button-layout (GTK 3/4 apps), libadwaita apps' style (adwstyle)."""
    from .. import prefs, titlebars, wfconfig
    from ..ui import tokens
    f = tokens.frame()
    for sec, k, v in wfconfig.frame_options(f):
        if k in BUTTON_KEYS:
            system.wayfire_set(sec, k, v)
    prefs.set("org.gnome.desktop.wm.preferences", "button-layout", tokens.button_layout(f))
    titlebars.apply()


def minimize_animation(effect: str) -> str:
    """Settings' minimize effect -> Wayfire's animate/minimize_animation."""
    return "squeezimize" if effect == "genie" else "zoom"


def combo_row(title, options, selected, on_change, subtitle="") -> Adw.ComboRow:
    """options: [(value, label)]"""
    row = Adw.ComboRow(title=title, subtitle=subtitle, model=Gtk.StringList.new([o[1] for o in options]),
                       use_markup=False)
    values = [o[0] for o in options]
    row.values = values
    row.set_selected(values.index(selected) if selected in values else 0)
    row.connect("notify::selected", lambda r, _p: getattr(r, "quiet", False) or on_change(values[r.get_selected()]))
    return row


def show_quietly(row, value) -> None:
    """Show a state read back from the system on a switch/combo row without
    writing it back (its on_change doesn't run)."""
    row.quiet = True
    try:
        if isinstance(row, Adw.ComboRow):
            if value in row.values:
                row.set_selected(row.values.index(value))
        else:
            row.set_active(bool(value))
    finally:
        row.quiet = False


def nearest(options, value):
    """The option value closest to a number read from the system (1.75 -> 2.0)."""
    return min((o[0] for o in options), key=lambda v: abs(v - value))


def clock_format(date: bool, h24: bool) -> str:
    """The menu bar clock format for Date & Time's two switches."""
    return ("%a %-d %b  " if date else "%a ") + ("%H:%M" if h24 else "%-I:%M %p")


def slider_row(title, value, lower, upper, on_change, subtitle="", ends=None, default=None) -> Adw.ActionRow:
    """ends=("Slow", "Fast"): small labels under the slider's ends (macOS).
    default: a double-click on the slider resets it to this value."""
    row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
    s = ui.controls.slider(value, on_change, lower=lower, upper=upper, default=default)
    s.set_size_request(SLIDER_W, -1)
    s.set_hexpand(False)                  # fixed width: the trailing edge lines up with the other controls
    s.set_valign(Gtk.Align.CENTER)
    if ends:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, margin_top=4, margin_bottom=4,
                      hexpand=False)          # set: the labels' hexpand would widen it past SLIDER_W
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
    def __init__(self, app, start: str = "appearance"):
        super().__init__(application=app, title="System Settings")
        for c in ("sonata-settings", "sonata-glass"):      # added, not passed (keeps GTK's "csd")
            self.add_css_class(c)
        # Resizable with a working Zoom button (Vini's call; macOS keeps it fixed).
        ui.window.remember_size(self, "settings", 920, 640)  # its last size (never bigger than the display)
        self.set_size_request(760, 480)
        # Bluetooth's search for nearby devices ends with the window
        self.connect("close-request", lambda *_: (getattr(self, "_bt_scan_src", 0) and self._bt_scan(False), False)[1])
        self.connect("close-request", lambda *_: self._flush_live())     # a slider's last value
        ui.window.standard(self)
        self.toasts = Adw.ToastOverlay()
        # Sidebar | content in a plain box (like Files): whole-pixel edges, no
        # see-through seam (AdwNavigationSplitView left a half-transparent
        # column between the panes). The window never gets narrow enough to
        # need the split view's collapsing.
        self.split = Gtk.Box(vexpand=True)
        side = self._sidebar()
        side.set_size_request(ui.window.SIDEBAR_W, -1)
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
        self.pages = _Pages()
        self.built = {}                  # section -> time.time() it was built (PAGE_CONFIGS)
        self._jobs = {}                  # _latest(): key -> [busy, pending args]
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.select(section_of(start) if section_of(start) in [s[0] for s in SECTIONS] else "appearance")
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

    def _lights(self):
        return ui.window.traffic_lights(self.close, self.minimize,
                                        lambda: self.unmaximize() if self.is_maximized() else self.maximize())

    # -- sidebar -------------------------------------------------------------------
    def _sidebar(self):
        tv = Adw.ToolbarView()
        hb = Adw.HeaderBar(show_title=False, show_start_title_buttons=False, show_end_title_buttons=False)
        if ui.window.buttons_side() == "left":            # on the right: each pane's header (_show)
            hb.pack_start(self._lights())
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
        # the section you're in, clicked again: back to its first page (Vini: from an app in Apps)
        self.listbox.connect("row-activated", lambda _lb, r: getattr(r, "sid", None) == self.current and
                             self.pop_detail(r.sid))
        box.append(self.listbox)                    # the selection jumps: no slide (Vini)
        self.listbox.set_vexpand(True)
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
        if "/" in sid:                    # "appicons/<desktop id>": that app's icon form (a web app's menu)
            sid, self._focus_app = sid.split("/", 1)
            self._focus_part = sid
        sid = section_of(sid)
        if not from_sidebar:
            self.listbox.select_row(self.rows[sid])
            return
        if sid != self.current and sid in self.pages and self._stale(sid):
            self.content.remove(self.pages.pop(sid))
        if sid not in self.pages:
            title = next(s[1] for s in SECTIONS if s[0] == sid)
            page = Adw.PreferencesPage()
            groups = []                                    # no hero row: the pane title names the section
            for part in parts_of(sid):
                groups += getattr(self, f"_page_{part}")()
            for g in groups:
                page.add(g)
            tv = Adw.ToolbarView()
            hb = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
            hb.set_title_widget(Gtk.Label(label=title, css_classes=["st-pane-title"]))
            if ui.window.buttons_side() == "right":       # the window buttons at the right edge
                hb.pack_end(self._lights())
            tv.add_top_bar(hb)
            tv.set_content(page)
            tv.hb, tv.title, tv.main, tv.back = hb, hb.get_title_widget(), page, None
            self.pages[sid] = tv
            self.built[sid] = time.time()
            self.content.add_named(tv, sid)
        hid = section_of("hidden")
        if self.current == hid and sid != hid and hid in self.pages:
            old = self.pages.pop(hid)               # Hidden & Protected Apps locks again
            GLib.idle_add(lambda: (self.content.remove(old), False)[1])
        if self.current in self.pages and self.current != sid:
            self.pop_detail(self.current)           # a section opens on its list again
        self.current = sid                          # at once, no fade (Vini)
        self.content.set_visible_child(self.pages[sid])
        focus = getattr(self, "_focus_app", None)
        part = getattr(self, "_focus_part", None)
        if focus and part in ("appicons", "apps") and getattr(self, part + "_page", None):
            self._focus_app = None
            getattr(self, part + "_page").focus(focus)

    def push_detail(self, title: str, groups) -> Adw.PreferencesPage:
        """One item of the section in its place (Settings > Apps > an app),
        with a back button (‹) and its name as the pane's title."""
        tv = self.pages[self.current]
        page = Adw.PreferencesPage()
        for g in groups:
            page.add(g)
        if tv.back is None:
            tv.back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back",
                                 css_classes=["flat", "st-back"], valign=Gtk.Align.CENTER)
            tv.back.connect("clicked", lambda _b, sid=self.current: self.pop_detail(sid))
            tv.hb.pack_start(tv.back)
        tv.back.set_visible(True)
        tv.title.set_label(title)
        tv.set_content(page)
        tv.detail = page
        return page

    def pop_detail(self, sid=None) -> None:
        tv = self.pages.get(sid or self.current)
        if tv is None or getattr(tv, "detail", None) is None:
            return
        tv.detail = None
        tv.set_content(tv.main)
        tv.back.set_visible(False)
        tv.title.set_label(next(s[1] for s in SECTIONS if s[0] == section_of(sid or self.current)))

    def _stale(self, sid) -> bool:
        """A config file the section shows changed after it was built."""
        for name in PAGE_CONFIGS.get(sid, ()):
            try:
                if os.stat(os.path.join(config.CONFIG_DIR, name + ".json")).st_mtime > self.built.get(sid, 0):
                    return True
            except OSError:
                continue
        return False

    def _latest(self, key, fn, *args) -> None:
        """fn(*args) off the main loop, one at a time per key; while one runs
        only the newest call waits (a slider drag: no pile of threads, and
        the last value is the one that stays)."""
        job = self._jobs.setdefault(key, [False, None])
        if job[0]:
            job[1] = args
            return
        job[0] = True

        def done(_res):
            job[0], nxt, job[1] = False, job[1], None
            if nxt is not None:
                self._latest(key, fn, *nxt)
        system.run_async(fn, done, *args)

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
            show_quietly(self._wifi_switch, enabled)       # read back: no second `nmcli radio wifi`
            for r in self._wifi_rows:
                self._wifi_nets.remove(r)
            self._wifi_rows = []
            for n in networks[:20]:
                row = Adw.ActionRow(title=n.ssid, activatable=not n.connected, use_markup=False,
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
        entry = ui.controls.text_field(secret=True)
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
        near = group("Nearby Devices", "Put a device in pairing mode to see it here.")

        def device_row(d):
            row = Adw.ActionRow(title=d.name, use_markup=False, subtitle="Connected" if d.connected else
                                ("Not Connected" if d.paired else ""))
            btn = ui.controls.push_button("Disconnect" if d.connected else "Connect", valign=Gtk.Align.CENTER)

            def done(res):
                ok, msg = res or (False, "That didn't work")
                self.toast(msg)
                self._reload_page("bluetooth")
            btn.connect("clicked", lambda b, d=d: (b.set_sensitive(False), b.set_label(
                "Connecting…" if not d.connected else "Disconnecting…"), system.run_async(
                system.bluetooth_connect_result, done, d.mac, not d.connected)))
            row.add_suffix(btn)
            if d.paired:                       # macOS: the device's (i) menu
                more = Gtk.Button(icon_name="view-more-horizontal-symbolic", valign=Gtk.Align.CENTER,
                                  css_classes=["flat", "circular"], tooltip_text="Options")
                more.connect("clicked", lambda b, d=d: self._bt_device_menu(b, d, done))
                row.add_suffix(more)
            return row

        def fill(res):
            state, devices = res or (None, [])
            if state is None:
                top.add(Adw.ActionRow(title="Bluetooth", subtitle="No Bluetooth adapter found"))
                devs.set_visible(False)
                near.set_visible(False)
                return
            top.add(switch_row("Bluetooth", state, lambda on: system.run_async(
                system.set_bluetooth, lambda _ok: self._reload_page("bluetooth"), on)))
            paired = [d for d in devices if d.paired]
            self._bt_shown = {d.mac: d.connected for d in paired}
            for d in paired:
                devs.add(device_row(d))
            if not paired:
                devs.add(Adw.ActionRow(title="No devices"))
            self._bt_near = near
            self._bt_near_rows = {}
            self._bt_fill_near([d for d in devices if not d.paired])
            if state:
                from ..backend import bluez
                bluez.register_agent()             # answers "pair?" (on this, the GTK thread)
                self._bt_scan(True)
            else:
                near.set_visible(False)
        system.run_async(lambda: (system.bluetooth_state(), system.bluetooth_devices()), fill)
        return [top, devs, near]

    def _bt_device_menu(self, anchor, d, done) -> None:
        """Forget This Device… / Forget and Pair Again…, each asked first."""
        def ask(title, body, label, fn):
            ui.dialog.alert(title, body, [("cancel", "Cancel", ""), ("go", label, "destructive")],
                            lambda r: r == "go" and system.run_async(fn, done, d.mac), parent=self)
        I = ui.menu.Item
        ui.menu.popup(anchor, [[
            I("Forget This Device…", lambda: ask(
                f"Forget {d.name}?", "It won't connect by itself any more. To use it again, pair it from "
                "Nearby Devices.", "Forget Device", system.bluetooth_forget)),
            I("Forget and Pair Again…", lambda: ask(
                f"Pair {d.name} again?", "Put the device in pairing mode first. Sonata forgets it and pairs "
                "it anew (this fixes most devices that stopped connecting).", "Pair Again",
                system.bluetooth_pair_again))]], position=Gtk.PositionType.BOTTOM, glass=True)

    def _bt_fill_near(self, nearby) -> None:
        near = getattr(self, "_bt_near", None)
        if near is None:
            return
        for mac, row in list(self._bt_near_rows.items()):
            if mac not in {d.mac for d in nearby}:
                near.remove(row)
                del self._bt_near_rows[mac]
        for d in nearby:
            if d.mac not in self._bt_near_rows:
                row = Adw.ActionRow(title=d.name, use_markup=False)
                btn = ui.controls.push_button("Connect", valign=Gtk.Align.CENTER)

                def done(res):
                    ok, msg = res or (False, "That didn't work")
                    self.toast(msg)
                    if ok:
                        self._reload_page("bluetooth")
                    else:
                        GLib.idle_add(lambda: (self._bt_poll(), False)[1])
                btn.connect("clicked", lambda b, d=d: (b.set_sensitive(False), b.set_label("Connecting…"),
                                                       system.run_async(system.bluetooth_connect_result, done,
                                                                        d.mac, True)))
                row.add_suffix(btn)
                near.add(row)
                self._bt_near_rows[d.mac] = row

    def _bt_scan(self, on: bool) -> None:
        """Look for nearby devices while the Bluetooth section shows (macOS);
        the list follows every 3 s; stops when another section is chosen."""
        from ..backend import bluez
        src = getattr(self, "_bt_scan_src", 0)
        if src:
            GLib.source_remove(src)
            self._bt_scan_src = 0
        system.run_async(bluez.discovery, None, on)
        if on:
            self._bt_scan_src = GLib.timeout_add_seconds(3, self._bt_poll)

    def _bt_poll(self) -> bool:
        if self.current != "bluetooth":
            self._bt_scan(False)
            return False
        def got(devs):
            devs = devs or []
            # a saved device that connected or dropped since the page was built: show it
            shown = getattr(self, "_bt_shown", None)
            now = {d.mac: d.connected for d in devs if d.paired}
            if shown is not None and now != shown:
                self._reload_page("bluetooth")
                return
            self._bt_fill_near([d for d in devs if not d.paired])
        system.run_async(system.bluetooth_devices, got)
        return True

    def _follow_levels(self, anchor, out_row, mic_row) -> None:
        """Sliders follow volume changes made elsewhere (Control Center,
        keys, other apps) while the page is open."""
        def update(res):
            v, mic = res or (None, None)
            for row, val in ((out_row, v), (mic_row, mic)):
                if row is not None and val is not None and abs(row.slider.get_value() - val[0]) >= 1:
                    row.slider.set_value(val[0])

        def changed():
            if anchor.get_root() is None:              # the page is gone
                if proc is not None:
                    proc.kill()
                return
            system.run_async(lambda: (system.volume(), system.input_volume()), update)
        proc = system.watch_audio(changed)
        if proc is not None:
            anchor.connect("destroy", lambda *_: proc.kill())

    def _page_sound(self):
        out = group("Output")
        vol = group("Volume")

        def fill(res):
            v, sinks, sources, mic = res or (None, [], [], None)
            if v is None:
                vol.add(Adw.ActionRow(title="Output volume", subtitle="PipeWire (wpctl) not found"))
                out.set_visible(False)
                return
            out_row = slider_row("Output volume", v[0], 0, 100,
                                 lambda x: self._latest("volume", system.set_volume, int(x)))
            from .. import sounds
            ui.controls.on_release(out_row.slider, lambda: sounds.play_soon("volume"))   # feedback on release
            vol.add(out_row)
            vol.add(switch_row("Mute", v[1], lambda on: system.run_async(system.set_volume, None, None, on)))
            mic_row = None
            if mic is not None:
                mic_row = slider_row("Input volume", mic[0], 0, 100,
                                     lambda x: self._latest("mic", system.set_input_volume, int(x)))
                vol.add(mic_row)
            self._follow_levels(vol, out_row, mic_row)
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
            from ..sounds import DEFAULTS as SND_D
            out.add(switch_row("Use new headphones right away",
                               config.load("sounds", SND_D).get("follow_new_devices", True),
                               lambda on: self._save("sounds", "follow_new_devices", on),
                               subtitle="USB and Bluetooth headphones and headsets, their microphone too; "
                                        "the device before comes back when they go"))
            out.set_visible(bool(options or ins))
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
        arranged = group("Arrangement")            # only with two displays or more
        arranged.set_visible(False)
        screens = group("Displays")

        def fill(res):
            levels, ds = (res or ({}, []))[:2]
            # one slider per display: the laptop panel and each external
            # monitor that answers over DDC/CI
            names = [d.name for d in ds] or [None]
            if not any(system.is_builtin(n) for n in names):
                names.insert(0, None)                       # a panel wlr-randr didn't list
            for n in names:
                d = next((x for x in ds if x.name == n), None)
                title = "Built-in Display" if system.is_builtin(n) else (d.description if d and d.description
                                                                           else n)
                b = levels.get(n)
                if b is not None:
                    bright.add(slider_row(title, b, 0, 100,
                                          lambda x, n=n: self._latest(("brightness", n), system.set_brightness,
                                                                      int(x), n)))
                elif system.is_builtin(n):
                    bright.add(Adw.ActionRow(title=title, subtitle="No backlight control (brightnessctl)"))
                else:
                    bright.add(Adw.ActionRow(title=title, subtitle="Turn on DDC/CI in the monitor's own menu "
                                                                   "to change its brightness here (needs ddcutil)"))
            if len(ds) > 1:
                from ..shell.monitors import DEFAULTS as MON
                from . import arrange
                cur_main = config.load("displays", MON)["main"]
                # nothing chosen: what monitors.main() picks -- an external monitor first
                external = next((d.name for d in ds if not d.name.startswith(("eDP", "LVDS", "DSI"))), ds[0].name)
                main = cur_main if cur_main in [d.name for d in ds] else external
                from ..shell.monitors import share_with_login_screen

                def set_main(n):
                    config.save("displays", {"main": n})
                    share_with_login_screen()          # the login screen too (it can't read ~/.config)
                main_row = combo_row("Main display", [(d.name, arrange.display_title(d)) for d in ds], main,
                                     set_main,
                                     subtitle="Dock, desktop icons and notifications; every display gets a menu bar")

                def new_main(n):                      # the menu bar dragged to another display
                    set_main(n)
                    show_quietly(main_row, n)
                # where the displays sit, macOS' Arrange: drag a display, drag the menu bar
                thumb = res[2] if len(res) > 2 else None
                art = arrange.Arrangement(ds, main,
                                          lambda pos: system.run_async(system.set_display_positions, None, pos),
                                          new_main, Gdk.Texture.new_for_pixbuf(thumb) if thumb else None)
                hint = Gtk.Label(label="Drag the displays to arrange them. Drag the white menu bar to choose "
                                       "the main display.", wrap=True, xalign=0, css_classes=["dim-label"],
                                 margin_top=6, margin_bottom=10)
                frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["display-arrange-box"])
                frame.append(art)
                frame.append(hint)
                arranged.add(frame)
                arranged.set_visible(True)
                screens.add(main_row)
                self.arrangement = art                # (tests)
            for d in ds:
                saved = system.display_mode_setting(d.name)
                opts = [("highrr", "Highest refresh rate")] + \
                    [(m, m.replace("@", " @ ").split(".")[0] + " Hz") for m in d.modes]
                screens.add(combo_row(d.name, opts, saved if saved in [o[0] for o in opts] else d.current,
                                      lambda m, d=d: system.run_async(system.set_display_mode, None, d.name, m),
                                      subtitle=d.description))
                scales = [(1.0, "100 %"), (1.25, "125 %"), (1.5, "150 %"), (2.0, "200 %")]
                screens.add(combo_row("Scale", scales, nearest(scales, d.scale), lambda s, d=d: system.run_async(system.set_display_scale, None,
                                                                               d.name, s)))
            if not ds:
                screens.add(Adw.ActionRow(title="Displays", subtitle="wlr-randr not found or no outputs"))
        def read():
            ds = system.displays()
            levels = {None: system.brightness()}
            for d in ds:
                levels[d.name] = levels[None] if system.is_builtin(d.name) else system.brightness(d.name)
            from . import arrange
            return levels, ds, (arrange.wallpaper_thumb() if len(ds) > 1 else None)
        system.run_async(read, fill)
        look = group("Appearance")
        from .. import icons as _icons
        look.add(switch_row("Rounded screen corners",
                            config.load("appearance", _icons.APPEARANCE_DEFAULTS).get("screen_corners", True),
                            lambda on: self._save("appearance", "screen_corners", on),
                            subtitle="The corners of every display rounded (the lock screen too)"))
        pages = [bright, arranged, screens, look, self._night_shift_group()]
        from .. import gpu
        nvidia = "nvidia" in gpu._cards()
        if gpu.has_dual_gpu() or nvidia:
            graphics = group("Graphics")
            if gpu.has_dual_gpu():
                graphics.add(switch_row("Draw with the Displays' Graphics Card", gpu.compositor_on_display_gpu(),
                                        lambda on: (gpu.set_compositor_on_display_gpu(on),
                                                    self.ask_restart("session", "The graphics card change")),
                                        subtitle=self._gpu_subtitle(gpu)))
            if nvidia:
                from .. import gamemode
                graphics.add(switch_row("Lighter Effects While Gaming", gamemode.light_effects_wanted(),
                                        lambda on: config.update("gpu", light_effects=bool(on)),
                                        subtitle="When a full-screen game fills the graphics card, blur and "
                                                 "window animations pause until you leave the game."))
            if gpu.has_dual_gpu():
                graphics.add(switch_row("Smart Graphics Switching", gpu.smart(), gpu.set_smart,
                                        subtitle="Games and creative apps use the high-performance graphics; "
                                                 "everyday apps use the graphics that draw the screens. An app "
                                                 "can have its own choice: right-click it in the Dock or "
                                                 "Launchpad. For apps opened after the change."))
                self._gpu_card_rows(graphics, gpu)
            pages.append(graphics)
        pages.append(self._fps_group())
        return pages

    def _fps_group(self):
        """Displays > Games: limit games' frame rate (frame-pacer; off by default,
        the same choice as Control Center > FPS Limit)."""
        from .. import fpslimit
        from ..shell.fpsmodule import refresh_hz
        g = group("Games")
        ok = fpslimit.installed()
        cfg = fpslimit.settings()
        choices = [(c, "Off" if c == "off" else "Display's rate" if c == "max" else f"{c} FPS")
                   for c in fpslimit.CHOICES]
        limit = combo_row("Limit Frame Rate", choices, fpslimit.get(), lambda v: fpslimit.set(v, refresh_hz()),
                          subtitle="Vulkan and Proton games opened from Steam, Faugus, Lutris, Heroic or "
                                   "Bottles (frame-pacer). Also in Control Center (Add Controls).")
        hud = switch_row("Show frame-pacer's Overlay", cfg.get("hud", False),
                         lambda on: fpslimit.set(fpslimit.get(), refresh_hz(), hud=on),
                         subtitle="Its frame rate and timings in the corner of the game")
        for r in (limit, hud):
            r.set_sensitive(ok)
            g.add(r)
        if not ok:
            row = Adw.ActionRow(title="frame-pacer isn't installed",
                                subtitle="Downloaded from its GitHub releases into your home folder (no "
                                         "password). Steam picks it up the next time it opens.")
            btn = ui.controls.push_button("Install", valign=Gtk.Align.CENTER)

            def done(err):
                btn.set_label("Install")
                btn.set_sensitive(True)
                if err:
                    row.set_subtitle(f"Couldn't install: {err}")
                else:
                    row.set_visible(False)
                    for r in (limit, hud):
                        r.set_sensitive(True)
            btn.connect("clicked", lambda _b: (btn.set_label("Installing…"), btn.set_sensitive(False),
                                               fpslimit.install(done)))
            row.add_suffix(btn)
            g.add(row)
        g.fps_rows = (limit, hud)                                   # (tests)
        return g

    @staticmethod
    def _gpu_card_rows(graphics, gpu) -> None:
        """Which card games and everyday apps use: Automatic (Sonata's pick,
        named) or one forced by the user (Vini: in case Sonata guesses wrong)."""
        cs = gpu.cards()
        if len(cs) < 2:
            return
        names = {c.tag: gpu.card_name(c) for c in cs}
        best = gpu.best_discrete()
        auto = f"Automatic ({names[best.tag]})" if best else "Automatic"
        cfg = config.load(gpu.NAME, gpu.DEFAULTS)
        graphics.add(combo_row("Graphics for Games", [("", auto)] + list(names.items()),
                               cfg.get("games_gpu", ""), lambda tag: gpu.set_card("games", tag),
                               subtitle="Games, creative apps and apps set to use high-performance graphics."))
        graphics.add(combo_row("Graphics for Apps", [("", "Automatic")] + list(names.items()),
                               cfg.get("apps_gpu", ""), lambda tag: gpu.set_card("apps", tag),
                               subtitle="Everyday apps, while Smart Graphics Switching is on. A card without "
                                        "displays of its own can keep some apps from opening."))

    @staticmethod
    def _gpu_subtitle(gpu) -> str:
        """Why the switch is off when a crash turned it off (it looked like it
        turned itself off -- Vini)."""
        base = ("Smoother on displays wired to the discrete card, but some NVIDIA drivers can end the "
                "session. From the next login.")
        when = gpu.crashed_at()
        if when is None or gpu.compositor_on_display_gpu():
            return base
        stamp = GLib.DateTime.new_from_unix_local(int(when)).format("%-d %b, %H:%M")
        return f"Turned off after the session ended on {stamp}: the card refused memory. " + base
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
            config.update("nightshift", **kw)
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
        g.add(slider_row("Colour Temperature", cfg["warmth"], 0, 100,
                         lambda v: self._save_live("nightshift", "warmth", int(v)),
                         ends=("Less Warm", "More Warm"), default=50))
        return g

    def _page_battery(self):
        info = group()
        mode = group("Energy Mode", "Automatic switches to High Performance while a game or another app is "
                                    "full screen, and back when it closes.")

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
                from .. import gamemode

                def pick(p):
                    if gamemode.boosted():          # raised for a full-screen game
                        if p == "balanced":
                            return
                        gamemode.set_boosted(False)
                    from .. import powerprofile
                    powerprofile.remember(p)            # back to it when plugged in again
                    system.run_async(system.set_power_profile, None, p)
                shown = "balanced" if prof == "performance" and gamemode.boosted() else prof
                mode.add(combo_row("Energy mode", list(system.POWER_PROFILES), shown, pick))
            else:
                mode.add(Adw.ActionRow(title="Energy mode", subtitle="power-profiles-daemon not available"))
        system.run_async(lambda: (system.battery(), system.on_ac(), system.power_profile()), fill)
        screen = group("Display")
        from .. import displaysleep
        cur = displaysleep.seconds()          # Sonata's own (Wayfire's display timeout stays off)
        opts = [(60, "1 minute"), (120, "2 minutes"), (300, "5 minutes"), (600, "10 minutes"),
                (1200, "20 minutes"), (1800, "30 minutes"), (-1, "Never")]
        screen.add(combo_row("Turn display off after", opts, min((o[0] for o in opts), key=lambda v: abs(v - cur)),
                             lambda v: system.run_async(displaysleep.set_seconds, None, v)))
        return [info, mode, screen]

    def _page_wallpaper(self):
        from .. import wallpapers as W
        g = group("Wallpaper", "Sonata draws it; apps that show the desktop picture get it too.")
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, css_classes=["st-wall"], height_request=180,
                          can_shrink=True, overflow=Gtk.Overflow.HIDDEN)
        g.add(pic)
        BG = "org.gnome.desktop.background"

        def shown():
            """(light uri, dark uri) the desktop uses now."""
            light = system.gsetting(BG, "picture-uri") or ""
            return light, system.gsetting(BG, "picture-uri-dark") or light

        # Sonata's own pictures (Vini): a click sets them for Light and Dark
        gallery = group("Sonata Wallpapers")
        grid = Gtk.FlowBox(max_children_per_line=5, min_children_per_line=5, selection_mode=Gtk.SelectionMode.NONE,
                           row_spacing=12, column_spacing=12, homogeneous=True)
        tiles = {}
        for w in W.CATALOG:
            thumb = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, width_request=88,
                                height_request=58, overflow=Gtk.Overflow.HIDDEN)
            thumb.set_filename(W.thumb(w))
            tip = w.name + (" (changes with Light and Dark)" if w.light != w.dark else "")
            b = Gtk.Button(child=thumb, css_classes=["st-wall-tile"], tooltip_text=tip)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            col.append(b)
            col.append(Gtk.Label(label=w.name, css_classes=["st-wall-name"], ellipsize=3, max_width_chars=12))
            b.connect("clicked", lambda _b, w=w: use(W.uri(w.light), W.uri(w.dark)))
            tiles[w.id] = b
            grid.append(col)
        gallery.add(grid)

        chooser = group()                        # its own group: the page's standard gap below the picture
        row = Adw.ActionRow(title="Picture")
        choose = ui.controls.push_button("Choose…", valign=Gtk.Align.CENTER)

        # the widgets refresh() updates, emptied when the page goes: the tiles'
        # click closures hold refresh(), and refs to the page from it formed a
        # cycle through GTK that Python's gc can't see -- the page never died
        alive = {"pic": pic, "row": row, "tiles": tiles}

        def refresh():
            if not alive:
                return
            light, dark = shown()
            uri = dark if Adw.StyleManager.get_default().get_dark() else light
            f = Gio.File.new_for_uri(uri) if uri else None
            alive["pic"].set_file(f if f and f.query_exists(None) else None)
            cur = W.current(light, dark)
            for wid, b in alive["tiles"].items():
                (b.add_css_class if wid == cur else b.remove_css_class)("selected")
            name = next((w.name for w in W.CATALOG if w.id == cur), None)
            alive["row"].set_subtitle(name or (f.get_basename() if f else "None"))

        def use(light, dark):
            system.set_gsetting(BG, "picture-uri", light)
            system.set_gsetting(BG, "picture-uri-dark", dark)
            refresh()

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
                use(chosen.get_uri(), chosen.get_uri())
            dlg.open(self, None, done)
        choose.connect("clicked", pick)
        row.add_suffix(choose)
        chooser.add(row)
        refresh()
        sm = Adw.StyleManager.get_default()
        hid = sm.connect("notify::dark", lambda *_: refresh())

        def release():
            if alive:
                alive.clear()
                sm.disconnect(hid)

        def unrealized(w):
            # removed from the window (rebuilt), not just hidden: let it go
            # (checked once unparenting is over; DEFAULT priority: idles can
            # wait behind redraws)
            GLib.timeout_add(0, lambda: (w.get_root() is None and release(), False)[1])
        pic.connect("unrealize", unrealized)
        pic.connect("destroy", lambda *_: release())          # the window closed
        return [g, gallery, chooser]

    # -- input (Wayfire [input]; applied live) --------------------------------------------------
    def _wf(self, key, value):
        def both(k, v):                          # (keyboards with a layout of their own: theirs too)
            system.wayfire_set("input", k, v)
            system.sync_keyboard_option(k, v)
        self._latest(("input", key), both, key, value)

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
        src = group("Input Sources", "The first one is in use; switch from the input menu in the menu bar, "
                    "or with Ctrl+Space (the one used before).")
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
        return [rep, src, self._keyboards_group(names)]

    def _keyboards_group(self, names):
        """A layout per keyboard (Vini: a U.S. International USB keyboard on a
        Portuguese laptop): each connected keyboard follows Input Sources, or
        has one of its own."""
        grp = group("Keyboards", "Each keyboard can have a layout of its own; "
                                 "the others follow Input Sources.")
        grp.add(Adw.ActionRow(title="Looking for keyboards…"))
        options = [("", "Same as Input Sources")] + [(k, v) for k, v in system.XKB_LAYOUTS]

        def fill(found):
            kbds, own = found or ([], {})
            child = grp.get_first_child()
            rows = []
            self._walk_rows(child, rows)
            for r in rows:
                grp.remove(r)
            if not kbds:
                grp.add(Adw.ActionRow(title="No keyboards found"))
                return
            for name in kbds:
                cur = own.get(name, "")
                if cur and cur not in dict(options):
                    options.append((cur, cur))

                def changed(v, name=name, was=cur):
                    system.run_async(system.set_keyboard_device_layout, None, name, v)
                    if bool(v) != bool(was):            # Wayfire takes a keyboard's own section when it appears
                        self.toast("Unplug the keyboard and plug it in again (or log in again) to use it")
                shown = "Built-in Keyboard" if "AT Translated" in name or "i8042" in name else name
                row = combo_row(shown, options, cur, changed)
                grp.add(row)

        def look():
            kbds = system.keyboards()
            return kbds, {n: system.keyboard_device_layout(n) for n in kbds}
        system.run_async(look, fill)
        return grp

    @staticmethod
    def _walk_rows(child, rows):
        """The rows a preferences group holds (its list box's children)."""
        while child is not None:
            if isinstance(child, Adw.PreferencesRow):
                rows.append(child)
            else:
                Settings._walk_rows(child.get_first_child(), rows)
            child = child.get_next_sibling()

    def _set_layouts(self, lays):
        system.run_async(system.set_keyboard_layouts, lambda _r: self._reload_page("keyboard"), lays)

    def _reload_page(self, sid):
        """Rebuild a section (after its content changed)."""
        sid = section_of(sid)
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
        # one section shows both: every group says which device it's for (Vini)
        g = group("Trackpad: Point & Click")
        g.add(slider_row("Tracking speed", _speed(get("input", "touchpad_cursor_speed", "0")), 0, 100,
                         lambda v: self._wf("touchpad_cursor_speed", round(v / 50 - 1, 2)), ends=("Slow", "Fast"),
                         default=50))
        g.add(switch_row("Tap to click", get("input", "tap_to_click", "true") == "true",
                         lambda on: self._wf("tap_to_click", on), subtitle="Tap with one finger"))
        g.add(switch_row("Tap and drag", get("input", "tap_and_drag", "true") == "true",
                         lambda on: self._wf("tap_and_drag", on)))
        sc = group("Trackpad: Scrolling")
        sc.add(switch_row("Natural scrolling", get("input", "natural_scroll", "false") == "true",
                          lambda on: self._wf("natural_scroll", on),
                          subtitle="Content tracks finger movement"))
        sc.add(switch_row("Ignore trackpad while typing", get("input", "disable_touchpad_while_typing", "false")
                          == "true", lambda on: self._wf("disable_touchpad_while_typing", on)))
        return [g, sc]

    def _page_mouse(self):
        get = system.wayfire_get
        g = group("Mouse")
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

    def _page_apps(self):
        from .apps_page import AppsPage
        self.apps_page = AppsPage(self)
        # what apps may use lives here, with the apps (Vini) -- not in Security & Privacy
        return [self._permissions_group()] + self.apps_page.groups()

    def _page_appicons(self):
        from .appicons_page import AppIconsPage
        self.appicons_page = AppIconsPage(self)
        return self.appicons_page.groups()

    def _page_shortcuts(self):
        from .shortcuts_page import ShortcutsPage
        self.shortcuts_page = ShortcutsPage(self)
        return self.shortcuts_page.groups()

    def _page_gamepad(self):
        """Like macOS Game Controllers: the connected ones, then what they do
        on the desktop (sonata2/gamepad; paused in fullscreen games)."""
        from ..gamepad import evdev as E
        from ..gamepad.service import DEFAULTS as GP, LEGEND
        gp = config.load("gamepad", GP)
        pads = group("Controllers")

        def fill():
            for r in getattr(pads, "_rows", ()):
                pads.remove(r)
            pads._rows = []
            found = [E.gamepad_name(p) or "Game Controller" for p in E.find_gamepads()]
            for name in found:
                row = Adw.ActionRow(title=GLib.markup_escape_text(name), subtitle="Connected")
                row.add_prefix(Gtk.Image(icon_name="input-gaming-symbolic", pixel_size=24))
                pads.add(row)
                pads._rows.append(row)
            if not found:
                row = Adw.ActionRow(title="No controller connected",
                                    subtitle="Plug one in with a cable, or pair it in Bluetooth")
                bt = ui.controls.push_button("Bluetooth…", valign=Gtk.Align.CENTER)
                bt.connect("clicked", lambda *_: self.select("bluetooth"))
                row.add_suffix(bt)
                pads.add(row)
                pads._rows.append(row)
        fill()
        mon = Gio.File.new_for_path("/dev/input").monitor_directory(Gio.FileMonitorFlags.NONE, None)
        mon.connect("changed", lambda *_a: GLib.timeout_add(700, lambda: (fill(), False)[1]))
        pads._mon = mon                                   # lives as long as the page

        desk = group("Desktop Control", "Use a controller as mouse and keyboard when no game is running. "
                                        "It pauses by itself while a game fills the screen.")
        desk.add(switch_row("Control the desktop with a controller", gp["enabled"],
                            lambda on: self._save("gamepad", "enabled", on)))
        from ..gamepad.service import TOGGLE_TAPS
        hint = Adw.ActionRow(title="Shortcut on the controller",
                             subtitle=f"Press the Xbox / PS button {TOGGLE_TAPS} times quickly to turn it on or off, "
                                      "even in a game")
        hint.add_prefix(Gtk.Image(icon_name="input-gaming-symbolic", pixel_size=16))
        desk.add(hint)
        desk.add(slider_row("Pointer speed", float(gp["speed"]) * 50, 10, 100,
                            lambda v: self._save_live("gamepad", "speed", round(v / 50, 2)), ends=("Slow", "Fast"),
                            default=50))
        desk.add(slider_row("Scrolling speed", float(gp["scroll"]) * 50, 10, 100,
                            lambda v: self._save_live("gamepad", "scroll", round(v / 50, 2)), ends=("Slow", "Fast"),
                            default=50))
        desk.add(switch_row("Pause while Steam is open", gp["pause_steam"],
                            lambda on: self._save("gamepad", "pause_steam", on),
                            subtitle="Steam has its own desktop controls: both would act on every press"))
        keys = group("Buttons")
        for button, does in LEGEND:
            row = Adw.ActionRow(title=button)
            row.add_suffix(Gtk.Label(label=does, xalign=1, css_classes=["dim-label"]))
            keys.add(row)
        return [pads, desk, keys]

    # -- system --------------------------------------------------------------------------------
    def _page_datetime(self):
        from ..shell import topbar as T
        auto = group()
        clock = group("Clock")
        zone = group("Time Zone")

        def fill(res):
            on, cur, zones = res or (None, "UTC", [])
            auto.set_visible(on is not None)
            if on is not None:
                state = {"ntp": on}

                def ntp_done(ok, v):
                    if ok:
                        state["ntp"] = v
                    else:                             # back to what the system still has
                        self.toast("Couldn't change the setting")
                        show_quietly(ntp_row, state["ntp"])
                ntp_row = switch_row("Set date and time automatically", on,
                                     lambda v: system.run_async(system.set_ntp, lambda ok: ntp_done(ok, v), v))
                auto.add(ntp_row)
            if zones:
                zstate = {"zone": cur}

                def zone_done(ok, z):
                    if ok:
                        zstate["zone"] = z
                        self.toast(f"Time zone: {z}")
                    else:
                        self.toast("Couldn't change the time zone")
                        show_quietly(row, zstate["zone"])
                row = combo_row("Time zone", [(z, z.replace("_", " ")) for z in zones], cur,
                                lambda z: system.run_async(system.set_timezone, lambda ok: zone_done(ok, z), z))
                row.set_enable_search(True)
                zone.add(row)
            else:
                zone.add(Adw.ActionRow(title="Time zone", subtitle=cur))
        system.run_async(lambda: (system.ntp(), system.timezone(), system.timezones()), fill)
        fmt = config.load("topbar", T.DEFAULTS)["clock_format"]

        def set_clock(date=None, h24=None):
            cur = config.load("topbar", T.DEFAULTS)["clock_format"]      # now, not when the page was built
            self._save("topbar", "clock_format", clock_format(T.shows_date(cur) if date is None else date,
                                                              T.is_24h(cur) if h24 is None else h24))
        clock.add(switch_row("Use a 24-hour clock", T.is_24h(fmt), lambda on: set_clock(h24=on)))
        clock.add(switch_row("Show the date", T.shows_date(fmt), lambda on: set_clock(date=on)))
        return [auto, zone, clock]

    def _page_defaults(self):
        """Which app opens each kind of file and link (Vini: one place for all)."""
        from .. import defaultapps as DA
        apps_ = group("Default Apps")
        for k in DA.KINDS:
            options = DA.candidates(k.id)
            if not options:
                continue
            row = combo_row(k.title, options, options[0][0],
                            lambda v, kid=k.id: system.run_async(DA.set_default, None, kid, v))
            row.add_prefix(Gtk.Image(icon_name=k.icon))
            apps_.add(row)
            if k.id == "web":           # xdg-settings is a slow shell script: read it off the main loop
                row.set_sensitive(False)
                system.run_async(system.default_browser,
                                 lambda cur, r=row: (show_quietly(r, cur), r.set_sensitive(True)))
            else:
                show_quietly(row, DA.current(k.id))
        hint = group(description="Apps can also be chosen for one file: right-click it in Files, "
                                 "then Open With.")
        return [apps_, hint]

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
        login = _login_items()
        for name, title, enabled in login:
            items.add(switch_row(title, enabled, lambda on, n=name: _set_login_item(n, on)))
        if not login:
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
            name = ui.controls.push_button("Edit Name…", valign=Gtk.Align.CENTER)
            name.connect("clicked", lambda *_: ui.dialog.ask_text(
                "Full name", u.real_name, "OK", lambda v: self._user_op(U.set_real_name, u, v), parent=self))
            pw = ui.controls.push_button("Change Password…", valign=Gtk.Align.CENTER)
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
        other = ui.controls.push_button("Choose from Files…")
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

    def _password_dialog(self, u):
        from ..backend import users as U
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        new = ui.controls.text_field(placeholder="New password", secret=True)
        verify = ui.controls.text_field(placeholder="Verify", secret=True)
        verify.set_property("activates-default", True)
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
        full = ui.controls.text_field(hexpand=True)
        acct = ui.controls.text_field(hexpand=True)
        pw = ui.controls.text_field(secret=True, hexpand=True)
        verify = ui.controls.text_field(secret=True, hexpand=True)
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

        # a password is needed (Vini: an account made without one was locked --
        # it never showed on the login screen and couldn't log in)
        def check(*_a):
            dlg.set_response_enabled("create", U.valid_name(acct.get_text().strip()) and bool(pw.get_text())
                                     and pw.get_text() == verify.get_text())
        for e in (acct, pw, verify, full):
            e.connect("changed", check)
        check()

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
        head = group("System Update", "Packages, AUR and Flatpak apps. Sonata updates itself above.")
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
            b = ui.controls.push_button(label, style="default" if suggested else "")
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
                        button("Restart…", lambda: self.ask_restart("system", "The system update"),
                               suggested=True)
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
        clear = Adw.ActionRow(title="Recent items", subtitle="Forget the files opened recently")
        clear_btn = ui.controls.push_button("Clear", valign=Gtk.Align.CENTER)
        clear.add_suffix(clear_btn)

        def do_clear(*_):
            try:
                Gtk.RecentManager.get_default().purge_items()
                self.toast("Recent items cleared")
            except GLib.Error:
                pass
        clear_btn.connect("clicked", do_clear)
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
        return [gen, priv, self._firewall_group(), self._encryption_group(),
                self._usb_group(sec), self._keyring_group()]

    def _permissions_group(self):
        """macOS' Privacy list: what sandboxed (Flatpak) apps were allowed through
        the portals; a switch per app (permstore.py)."""
        from ..backend import permstore
        from .. import apps
        grp = group("App Permissions", "Apps installed with Flatpak ask before using these. "
                                       "Other apps aren't sandboxed.")
        from .. import appperms
        ask = switch_row("Ask before apps use the camera, microphone, network…", appperms.asking(),
                         appperms.set_asking,
                         subtitle="A new app asks the first time it opens; off: apps get access without asking")
        ask.set_subtitle_lines(0)
        grp.add(ask)
        for key, title, entries in permstore.all_permissions():
            exp = Adw.ExpanderRow(title=title, use_markup=False,
                                  subtitle=(f"{len(entries)} app" + ("s" if len(entries) != 1 else ""))
                                  if entries else "No app has asked")
            exp.set_enable_expansion(bool(entries))
            for app_id, on in entries:
                info = apps.lookup(app_id)
                row = switch_row(info.get_display_name() if info else app_id, on,
                                 lambda v, k=key, a=app_id: permstore.set_allowed(k, a, v))
                if info is not None and info.get_icon() is not None:
                    row.add_prefix(Gtk.Image(gicon=info.get_icon(), pixel_size=24))
                exp.add_row(row)
            grp.add(exp)
        return grp

    def _firewall_group(self):
        """macOS' Firewall: ufw or firewalld, on or off (pkexec asks the password)."""
        from ..backend import security
        fw = security.firewall()
        grp = group("Firewall")
        if fw["kind"] is None:
            row = Adw.ActionRow(title="No firewall installed", use_markup=False,
                                subtitle="Install ufw (sudo pacman -S ufw) to turn it on here")
            row.set_subtitle_lines(0)
            grp.add(row)
            return grp
        row = switch_row("Firewall", fw["on"], lambda _on: None,
                         subtitle=f"Blocks connections from other computers to this one ({fw['kind']})")

        def changed(r, _p):
            if getattr(r, "quiet", False):
                return
            want = r.get_active()
            r.set_sensitive(False)

            def done(ok):
                r.set_sensitive(True)
                if not ok:                                   # cancelled or failed: the switch shows the truth
                    r.quiet = True
                    r.set_active(security.firewall()["on"])
                    r.quiet = False
                self.toast(("Firewall on" if want else "Firewall off") if ok else "Firewall not changed")
            system.run_async(lambda: security.set_firewall(fw["kind"], want), done)
        row.connect("notify::active", changed)
        grp.add(row)
        return grp

    def _encryption_group(self):
        """macOS' FileVault: shown only (Linux encrypts a disk when it's installed)."""
        from ..backend import security
        enc = security.encryption()
        grp = group("Disk Encryption")
        if enc.get("/"):
            title, sub = "The startup disk is encrypted", "Its data can't be read without your disk password (LUKS)"
            if enc.get("/home") is False:
                sub = "The home folder is on a disk that isn't encrypted"
        elif enc.get("/") is False:
            title, sub = "The startup disk isn't encrypted", ("Anyone with the disk can read its files. Linux "
                                                              "encrypts a disk when it's installed: choose "
                                                              "encryption in the installer.")
        else:
            title, sub = "Unknown", "The startup disk couldn't be checked"
        row = Adw.ActionRow(title=title, subtitle=sub, use_markup=False)
        row.add_prefix(Gtk.Image(icon_name="object-locked-symbolic" if enc.get("/") else "object-unlocked-symbolic"))
        row.set_subtitle_lines(0)
        grp.add(row)
        return grp

    def _usb_group(self, sec):
        """GNOME's USB protection: devices plugged in while locked are blocked (USBGuard)."""
        from ..backend import usbprotect
        grp = group("USB")
        ok = usbprotect.available()
        row = switch_row("Block new USB devices while locked", ok and sec.get("usb_protection", True),
                         lambda on: self._save("security", "usb_protection", on),
                         subtitle="Devices already plugged in keep working" if ok else
                         "Needs USBGuard: sudo pacman -S usbguard, then sudo systemctl enable --now usbguard-dbus")
        row.set_subtitle_lines(0)
        row.set_sensitive(ok)
        grp.add(row)
        import shutil as _sh
        rgb = switch_row("Turn RGB lights off with the screen", bool(sec.get("rgb_dark", False)),
                         lambda on: self._save("security", "rgb_dark", on),
                         subtitle="USB keyboards, mice and other RGB devices, through OpenRGB" if _sh.which("openrgb")
                         else "Needs OpenRGB")
        rgb.set_sensitive(bool(_sh.which("openrgb")))
        grp.add(rgb)
        return grp

    def _keyring_group(self):
        """Where saved passwords live (keyring.py): the login keyring (no
        prompt after logging in) or KeePassXC (its own password each time)."""
        from .. import keyring
        grp = group("Saved Passwords")
        if keyring.backend() == "keepassxc":
            row = Adw.ActionRow(title="Kept by KeePassXC", use_markup=False,
                                subtitle="It asks for its own password at every login")
            btn = ui.controls.push_button("Use Login Password…", valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda _b: self._switch_keyring())
            row.add_suffix(btn)
        else:
            row = Adw.ActionRow(title="Login keyring", use_markup=False,
                                subtitle="Unlocked by your login password: nothing asks after logging in")
        row.set_subtitle_lines(0)
        grp.add(row)
        return grp

    def _switch_keyring(self) -> None:
        """KeePassXC -> the login keyring, with the apps' saved passwords."""
        from .. import keyring
        entry = ui.controls.text_field(secret=True, hexpand=True)

        def go(rid):
            if rid != "switch":
                return
            pw = entry.get_text()
            self.toast("Moving your saved passwords… KeePassXC may ask to unlock its database")

            def work():
                try:
                    n = keyring.switch_to_gnome(pw, lambda t: GLib.idle_add(self.toast, t))
                    return f"{n} saved passwords moved"
                except Exception as e:                       # noqa: BLE001 -- told, nothing lost
                    return f"Not switched: {e}"
            system.run_async(work, lambda msg: (self.toast(msg), self._reload_page("privacy"),
                                                msg.startswith("Not") or
                                                self.ask_restart("session", "The login keyring")))
        dlg = ui.dialog.alert("Use your login password for saved passwords?",
                              "Apps' saved passwords (Chrome, VS Code, Wi-Fi) move from KeePassXC to the "
                              "login keyring, which your login unlocks: no password prompt after logging in. "
                              "KeePassXC stays installed as a password manager. Type your login password:",
                              [("cancel", "Cancel", ""), ("switch", "Use Login Password", "default")], go,
                              parent=self)
        dlg.set_extra_child(entry)

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
                    b = ui.controls.push_button("Make Default", valign=Gtk.Align.CENTER)
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
        entry.set_text(GLib.get_host_name())
        system.run_async(system.computer_name, lambda n: n and entry.set_text(n))    # hostnamectl: off the main loop
        entry.connect("apply", lambda e: system.run_async(
            system.set_computer_name, lambda ok: self.toast("Computer name changed" if ok else
                                                            "Couldn't change the name"), e.get_text().strip()))
        g.add(entry)
        g.add(Adw.ActionRow(title="Local hostname", subtitle=GLib.get_host_name() + ".local", use_markup=False))
        return [g, self._screen_sharing_group()]

    def _screen_sharing_group(self):
        """See and control this screen from another computer or a phone
        (backend/screenshare.py: wayvnc). Vini: Chrome Remote Desktop can't
        show a Wayfire session."""
        from ..backend import screenshare as S
        g = group("Screen Sharing", "Others can see and control this screen with a VNC viewer "
                                    "(RealVNC Viewer, TigerVNC) and the password below.")
        if not S.installed():
            row = Adw.ActionRow(title="Screen Sharing needs wayvnc", use_markup=False,
                                subtitle="Install it with your package manager (Arch / CachyOS: "
                                         "sudo pacman -S wayvnc), then open this page again.")
            row.set_subtitle_lines(0)
            g.add(row)
            return g
        cfg = config.load(S.NAME, S.DEFAULTS)
        details = []
        g.add(switch_row("Screen Sharing", cfg["screen"],
                         lambda on: (self._save(S.NAME, "screen", on), [d.set_visible(on) for d in details])))
        shown = [("", "Main display")] + [(d.name, f"{d.name} — {d.description}" if d.description else d.name)
                                          for d in system.displays()]
        details.append(combo_row("Display", shown, cfg.get("output", ""),
                                 lambda v: self._save(S.NAME, "output", v), subtitle="The one others see"))
        where = Adw.ActionRow(title="Address", use_markup=False, subtitle="…")
        where.set_subtitle_lines(0)
        details.append(where)

        def fill(addrs):
            where.set_subtitle("\n".join(f"{a}  ({'Tailscale' if i.startswith('tailscale') else i})"
                                         for a, i in addrs) or "No network")
        system.run_async(S.addresses, fill)
        user, pw = S.credentials()
        login = Adw.ActionRow(title="User and password", use_markup=False, subtitle=f"{user}  ·  {pw}")
        box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)

        def copy():
            self.get_clipboard().set(S.credentials()[1])
            self.toast("Password copied")

        def renew():
            S.new_password()
            login.set_subtitle(f"{user}  ·  {S.credentials()[1]}")
            self._save(S.NAME, "rev", config.load(S.NAME, S.DEFAULTS).get("rev", 0) + 1)   # wayvnc restarts
            self.toast("New password: viewers connected now are disconnected")
        box.append(ui.controls.push_button("Copy", copy))
        box.append(ui.controls.push_button("New Password", renew))
        login.add_suffix(box)
        details.append(login)
        far = Adw.ActionRow(title="From outside your network", use_markup=False,
                            subtitle="Install Tailscale on this computer and on the other device, then use the "
                                     "Tailscale address. Never open port 5900 on your router.")
        far.set_subtitle_lines(0)
        details.append(far)
        for d in details:
            d.set_visible(cfg["screen"])
            g.add(d)
        self.sharing_rows = details                                    # (tests)
        return g

    def _page_accessibility(self):
        I = "org.gnome.desktop.interface"
        disp = group("Display")
        anim = system.gsetting(I, "enable-animations")
        disp.add(switch_row("Reduce motion", anim == "false", lambda on: (
            system.set_gsetting(I, "enable-animations", "false" if on else "true"),
            system.run_async(system.wayfire_set, None, "animate", "open_animation", "fade"),
            system.run_async(system.wayfire_set, None, "animate", "close_animation", "fade"))))
        app = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        disp.add(switch_row("Reduce transparency", app.get("reduce_transparency", False),
                            lambda on: (self._save("appearance", "reduce_transparency", on),
                                        self._apply_titlebars()),
                            subtitle="Solid sidebars, Dock and title bars instead of glass"))
        disp.add(combo_row("Graphics", [("gl", "Hardware (OpenGL)"), ("vulkan", "Hardware (Vulkan)"),
                                        ("software", "Software (no GPU)")],
                           app.get("renderer", "gl"),
                           lambda v: (self._save("appearance", "renderer", v),
                                      self.ask_restart("sonata", "The new graphics setting")),
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

        custom_now = ui.tokens.custom_accent(cur)

        def pick(name):
            for n, b in buttons.items():
                (b.add_css_class if n == name else b.remove_css_class)("selected")
            (custom.add_css_class if ui.tokens.custom_accent(name) else custom.remove_css_class)("selected")
            self._save("appearance", "accent", name)
        for name in ui.tokens.ACCENTS:
            b = Gtk.Button(css_classes=["st-accent", name] + (["selected"] if name == cur else []),
                           tooltip_text=name.capitalize(), valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, n=name: pick(n))
            buttons[name] = b
            box.append(b)
        # any colour (Vini): Sonata's own colour picker; the dot shows the colour picked
        custom = ui.colorpicker.ColorDot(custom_now or ui.tokens.accent_hex(cur))
        if custom_now:
            custom.add_css_class("selected")

        def picked(hexc):
            custom.set_color(hexc)
            pick(hexc)
        custom.connect("clicked", lambda b: setattr(b, "picker", ui.colorpicker.popup(
            b, custom.swatch.color, picked)))
        box.append(custom)
        row.custom_accent = custom                        # (tests)
        row.add_suffix(box)
        return row

    def _page_appearance(self):
        """Everything about how Sonata looks (macOS Appearance): light/dark and
        accent, style and icons, title bars, glass, corners."""
        g = group("Appearance")
        scheme = system.gsetting("org.gnome.desktop.interface", "color-scheme") or "default"
        g.add(combo_row("Appearance", [("default", "Light"), ("prefer-dark", "Dark")],
                        "prefer-dark" if scheme == "prefer-dark" else "default",
                        lambda v: system.run_async(system.set_dark_mode, None, v == "prefer-dark"),
                        subtitle="Linux setting: every app follows it"))
        g.add(self._accent_row())
        s = group("Style")
        app = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        def style(v):
            self._save("appearance", "theme", "mac")
            if v != "mac":                     # not available yet: the pop-up goes back to what's in use
                show_quietly(style_row, "mac")
        style_row = combo_row("Style", [("mac", "Sonata"), ("windows", "Windows 11 (coming later)")], app["theme"],
                              style)
        s.add(style_row)
        themes = sorted({d for base in GLib.get_system_data_dirs() + [GLib.get_user_data_dir()]
                         for d in (os.listdir(os.path.join(base, "icons")) if os.path.isdir(os.path.join(base, "icons"))
                                   else []) if os.path.exists(os.path.join(base, "icons", d, "index.theme"))}
                        | {"Sonata"})
        from ..shell import launchpad_window as LW          # Vini: with the rest of the look
        s.add(combo_row(f"{names.APPS} style", list(LW.STYLES), LW.style(),
                        lambda v: config.update(LW.NAME, style=v),
                        subtitle=f"Full Screen: over the whole screen. {names.APPS_MENU}: a panel in the middle, "
                                 "apps by category"))
        s.add(combo_row("Icons", [(t, t) for t in themes], app["icon_theme"],
                        lambda v: (self._save("appearance", "icon_theme", v),
                                   self.ask_restart("sonata", "The new icons"))))
        gen = Adw.ActionRow(title="App icons made by Sonata",
                            subtitle="Apps without Sonata artwork get their icon on the standard frame, saved on disk")
        regen = ui.controls.push_button("Regenerate", valign=Gtk.Align.CENTER)
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
        bars = group("Title Bars")
        bars.add(combo_row("Window buttons", [("left", "Left"), ("right", "Right")],
                           app.get("buttons_side", "left"), self._set_buttons_side,
                           subtitle="Where close, minimize and zoom sit on every window"))
        self._button_colour_rows(bars, app)
        bars.add(switch_row("Sonata title bars for all apps", app["system_titlebars"],
                            lambda on: (self._save("appearance", "system_titlebars", on),
                                        system.run_async(__import__("sonata2.titlebars", fromlist=["apply"]).apply,
                                                         None, on),
                                        self.toast("Apps pick it up when they open again")),
                            subtitle="Chrome, VS Code and others use Sonata's title bar instead of their own"))
        bars.add(switch_row("Always show the tab bar", app.get("always_show_tabs", True),
                            lambda on: self._save("appearance", "always_show_tabs", on),
                            subtitle="Terminal, Files and TextEdit show their tabs and + even with one tab"))
        bars.add(switch_row("Sonata look for Steam", app.get("steam_theme", True), self._set_steam_theme,
                            subtitle="Steam keeps its own look, with Sonata's round window buttons "
                                     "and corners"))
        bars.add(switch_row("Glass title bars", app.get("glass_titlebars", False), self._set_glass_titlebars,
                            subtitle="See-through, blurred title bars on every window, GNOME apps too "
                                     "(experimental; heavier on the graphics card)"))
        reset = self._reset_group("Reset Appearance", "Accent colour, style, icons, title bars, glass and "
                                  "corners back to the theme's defaults", self.ask_reset_appearance)
        return [g, s, bars, self._glass_group(), self._corners_group(), reset]

    def _reset_group(self, title, subtitle, ask):
        """A section's last row: everything on it back to the defaults (asked first)."""
        g = group()
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
        rb = ui.controls.push_button("Reset\u2026", ask, style="destructive")
        rb.set_valign(Gtk.Align.CENTER)
        row.add_suffix(rb)
        g.add(row)
        g.reset_button = rb                            # (tests)
        return g

    # what Reset Appearance puts back (Vini: the theme's defaults); light/dark is
    # the system's setting and stays
    APPEARANCE_RESET = ("accent", "theme", "icon_theme", "flatpak_theme", "system_titlebars",
                        "glass_titlebars", "glass", "radius", "buttons_side", "buttons_style", "buttons_colors",
                        "always_show_tabs")

    def _set_steam_theme(self, on):
        """Settings > Appearance > Sonata look for Steam (steamtheme.py)."""
        from .. import steamtheme
        self._save("appearance", "steam_theme", bool(on))
        system.run_async(lambda: steamtheme.apply(force=True) if on else steamtheme.remove(), None)
        self.toast("Steam shows it the next time it opens")

    BUTTON_STYLES = [("color", "Colourful"), ("graphite", "Graphite"), ("mono", "Black & White"),
                     ("custom", "Custom")]

    def _button_colour_rows(self, bars, app) -> None:
        """Settings > Appearance > Button Colours (Vini): colourful, graphite,
        black & white (black on light, white on dark) or a colour per button."""
        from .. import trafficlights
        look = app.get("buttons_style", "color")
        row = combo_row("Button colours", self.BUTTON_STYLES, look if look in trafficlights.STYLES else "color",
                        self._set_button_style, subtitle="Close, minimize and zoom on every window")
        bars.add(row)
        custom = Adw.ActionRow(title="Custom colours", subtitle="Close, minimize, zoom",
                               visible=look == "custom")
        box = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        self.button_dots = {}
        for name, tip in (("close", "Close"), ("minimize", "Minimize"), ("maximize", "Zoom")):
            dot = ui.colorpicker.ColorDot(trafficlights.custom()[name], tooltip=tip)

            def picked(hexc, n=name, d=dot):
                d.set_color(hexc)
                cols = dict(config.load("appearance", icons.APPEARANCE_DEFAULTS).get("buttons_colors") or {})
                cols[n] = hexc
                self._save("appearance", "buttons_colors", cols)
                system.run_async(trafficlights.apply, None, Adw.StyleManager.get_default().get_dark())
            dot.connect("clicked", lambda b, n=name, f=picked, t=tip: setattr(b, "picker", ui.colorpicker.popup(
                b, b.swatch.color, f, title=f"{t} Button")))
            box.append(dot)
            self.button_dots[name] = dot
        custom.add_suffix(box)
        bars.add(custom)
        self.button_custom_row = custom                           # (tests)

    def _set_button_style(self, look):
        from .. import trafficlights
        self._save("appearance", "buttons_style", look)
        if getattr(self, "button_custom_row", None) is not None:
            self.button_custom_row.set_visible(look == "custom")
        system.run_async(trafficlights.apply, None, Adw.StyleManager.get_default().get_dark())
        self.toast("Other apps pick it up when they open again")

    def _set_buttons_side(self, side):
        """Settings > Appearance > Window buttons: left (macOS) or right. The
        title bars Wayfire draws change at once; GTK apps follow GNOME's
        button-layout; Sonata's apps place theirs when their windows open."""
        self._save("appearance", "buttons_side", side)
        system.run_async(apply_buttons_side, None)
        self.toast("Open windows pick it up when they open again")

    def ask_reset_appearance(self):
        return ui.dialog.alert("Reset Appearance?",
                               "The accent colour, style, icons, title bars, glass and corners go back to "
                               "the theme's defaults.",
                               [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")],
                               lambda rid: rid == "reset" and self.reset_appearance(), parent=self)

    def reset_appearance(self) -> None:
        import copy
        from .. import flatpak_theme, titlebars, wfconfig
        from ..ui import tokens
        for src in ("_glass_src", "_glass_wf_src", "_radius_src"):      # a slider still saving
            if getattr(self, src, 0):
                GLib.source_remove(getattr(self, src))
                setattr(self, src, 0)
        self._glass_pending, self._radius_pending = {}, {}
        old = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        for k in self.APPEARANCE_RESET:
            self._save("appearance", k, copy.deepcopy(icons.APPEARANCE_DEFAULTS[k]))
        dark = Adw.StyleManager.get_default().get_dark()
        flatpak = old.get(flatpak_theme.KEY) != icons.APPEARANCE_DEFAULTS[flatpak_theme.KEY]
        buttons = old.get("buttons_style", "color") != "color"

        def apply():
            for sec, k, v in wfconfig.frame_options(tokens.frame()):
                if k in ("rounded_corner_radius", "radius"):
                    system.wayfire_set(sec, k, v)
            apply_buttons_side()
            titlebars.apply(icons.APPEARANCE_DEFAULTS["system_titlebars"])
            titlebars.apply_colors(dark)
            if flatpak:
                flatpak_theme.apply()
            if buttons:                                    # Steam's and GTK apps' buttons too
                from .. import trafficlights
                trafficlights.apply(dark)
        system.run_async(apply, None)
        self.rebuild_page("appearance")
        if old.get("icon_theme") != icons.APPEARANCE_DEFAULTS["icon_theme"]:
            self.ask_restart("sonata", "Sonata's icons")
        else:
            self.toast("Appearance reset")

    def rebuild_page(self, sid) -> None:
        """The section built again from its settings (shown as it is now)."""
        sid = section_of(sid)
        page = self.pages.pop(sid, None)
        if page is None:
            return
        self.content.remove(page)                 # (a stack child's name must be free again)
        if self.current == sid:
            self.current = None
            self.select(sid, from_sidebar=True)

    # -- glass, per part (ui/glass.py) -------------------------------------------------------
    def _glass_group(self):
        from ..ui import glass as G
        cur = G.settings()
        vals = ui.theme.values()
        g = group("Glass & Transparency",
                  "Frosted, see-through backgrounds. Off: solid. "
                  + ("Accessibility > Reduce transparency is on: everything is solid now."
                     if ui.theme.reduce_transparency() else ""))
        self.glass_rows = {}
        for item in G.ITEMS:
            sw = switch_row(G.TITLES[item], cur[item]["on"], lambda on, k=item: self._set_glass(k, on=on),
                            subtitle=G.SUBTITLES.get(item, ""))
            sl = slider_row("Transparency", self._alpha_to_slider(G.alpha_of(item, vals, cur)), 0, 100,
                            lambda v, k=item: self._set_glass(k, alpha=self._slider_to_alpha(v)),
                            ends=("Less", "More"),
                            default=self._alpha_to_slider(G.css_alpha(vals[G.MATERIALS[item][0]])))
            sl.add_css_class("st-sub-row")
            sl.set_sensitive(cur[item]["on"])
            self.glass_rows[item] = (sw, sl)
            g.add(sw)
            g.add(sl)
        blur = slider_row("Blur strength", cur["blur"], 0, 100, lambda v: self._set_glass("blur", blur=v),
                          subtitle="How frosted every glass part is (one for all: Wayfire's blur)",
                          ends=("Light", "Strong"), default=G.BLUR_DEFAULT)
        self.glass_rows["blur"] = blur
        g.add(blur)
        return g

    @staticmethod
    def _alpha_to_slider(alpha: float) -> float:
        from ..ui import glass as G
        lo, hi = G.ALPHA_RANGE
        return round((hi - min(hi, max(lo, alpha))) / (hi - lo) * 100, 1)

    @staticmethod
    def _slider_to_alpha(v: float) -> float:
        from ..ui import glass as G
        lo, hi = G.ALPHA_RANGE
        return G.clamp_alpha(hi - (hi - lo) * v / 100)

    GLASS_LIVE_MS = 120      # while a slider moves: saved this often (every Sonata surface follows live)
    GLASS_WAYFIRE_MS = 300   # Wayfire's part (blur rule, strength, threshold): once it stops

    def _set_glass(self, item, on=None, alpha=None, blur=None) -> None:
        """A switch saves at once. A moving slider saves every GLASS_LIVE_MS
        (a live preview: it used to wait for the slider to stop, so dragging
        seemed to do nothing); Wayfire's settings follow once it stops."""
        pend = self._glass_pending = getattr(self, "_glass_pending", {})
        if item == "blur":
            pend["blur"] = int(round(blur))
        else:
            part = pend.setdefault(item, {})
            if on is not None:
                part["on"] = bool(on)
                rows = getattr(self, "glass_rows", {}).get(item)
                if rows:
                    rows[1].set_sensitive(bool(on))
            if alpha is not None:
                part["alpha"] = alpha

        def save():
            self._glass_src = 0
            if not self._glass_pending:
                return False
            from ..ui import glass as G
            raw = dict(config.load("appearance", icons.APPEARANCE_DEFAULTS).get("glass") or {})
            cur = G.settings()
            for k, v in self._glass_pending.items():
                if k == "blur":
                    raw["blur"] = v
                else:
                    part = {"on": cur[k]["on"]}            # (a dict(..., **a, **b) with the same key raised)
                    part.update(raw.get(k) if isinstance(raw.get(k), dict) else {})
                    part.update(v)
                    raw[k] = part
            self._glass_pending = {}
            config.update("appearance", glass=raw)
            return False

        def wayfire():
            self._glass_wf_src = 0
            from .. import titlebars
            system.run_async(titlebars.apply_colors, None, Adw.StyleManager.get_default().get_dark())
            return False
        if getattr(self, "_glass_wf_src", 0):
            GLib.source_remove(self._glass_wf_src)
        if on is not None:
            if getattr(self, "_glass_src", 0):
                GLib.source_remove(self._glass_src)
            save()
            self._glass_wf_src = 0
            wayfire()
            return
        if not getattr(self, "_glass_src", 0):          # throttle, not debounce: live while dragging
            self._glass_src = GLib.timeout_add(self.GLASS_LIVE_MS, save)
        self._glass_wf_src = GLib.timeout_add(self.GLASS_WAYFIRE_MS, wayfire)

    def _page_dock(self):
        from ..shell import dock as D
        cfg = config.load("dock", D.DEFAULTS)
        size = group("Dock")
        size.add(slider_row("Size", cfg["icon_size"], D.MIN_SIZE, D.MAX_SIZE,
                            lambda v: self._save_live("dock", "icon_size", int(v)), default=D.DEFAULTS["icon_size"]))
        size.add(switch_row("Magnification", cfg["magnification"], lambda on: self._save("dock", "magnification", on)))
        size.add(slider_row("Magnified size", cfg["magnified_size"], D.MIN_SIZE, D.MAX_SIZE,
                            lambda v: self._save_live("dock", "magnified_size", int(v)),
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
        look = group("Look")
        # (its glass: Appearance > Glass & Transparency)
        look.add(slider_row("Distance from the screen edge", cfg["edge_gap"], 0, 24,    # and from zoomed windows
                            lambda v: self._save_live("dock", "edge_gap", int(v)), default=D.DEFAULTS["edge_gap"]))
        reset = self._reset_group("Reset Dock", "The Dock's options back to the defaults; your apps and "
                                  "folders in the Dock stay", self.ask_reset_dock)
        return [size, behave, look, reset]

    def _page_desktop(self):
        """Desktop & Windows: how windows behave, the desktop's icons."""
        from ..shell import desktop as DK, dock as D
        cfg = config.load("dock", D.DEFAULTS)
        wins = group("Windows")
        live = (system.wayfire_get("sonata-resize", "live", "true") or "true").lower() != "false"
        wins.add(switch_row("Show window contents while resizing", live,
                            lambda on: system.run_async(system.wayfire_set, None, "sonata-resize", "live", bool(on)),
                            subtitle="Off: only the window's background follows the pointer, and its contents "
                                     "fade in when you let go -- smoother with heavy apps"))
        wins.add(switch_row("Quit apps when their last window closes", cfg.get("quit_on_close", True),
                            lambda on: self._save("dock", "quit_on_close", on),
                            subtitle="Closing an app's last window quits it, also Steam, Discord and "
                                     "others that keep running in the background"))
        wins.add(combo_row("Minimize windows using", [("genie", "Genie effect"), ("scale", "Scale effect")],
                           cfg["minimize_effect"], self._set_minimize_effect))
        dbl = system.gsetting("org.gnome.desktop.wm.preferences", "action-double-click-titlebar") or "toggle-maximize"
        wins.add(combo_row("Double-click a window's title bar to",
                           [("toggle-maximize", "Zoom"), ("minimize", "Minimize"), ("none", "Do Nothing")],
                           dbl if dbl in ("toggle-maximize", "minimize", "none") else "toggle-maximize",
                           self._set_double_click))
        desk = group("Desktop")
        desk.add(combo_row("Sort icons by", list(DK.SORTS), config.load("desktop", DK.DEFAULTS).get("sort", "none"),
                           lambda v: self._save("desktop", "sort", v),
                           subtitle="None: icons stay where you put them"))
        reset = self._reset_group("Reset Desktop & Windows", "Window options back to the defaults; your "
                                  "desktop icons stay where they are", self.ask_reset_desktop)
        return [wins, desk, reset]

    def _set_double_click(self, v):
        """GTK's title bars (the gsetting) and the top edge of every window
        (sonata-resize: a double-click there used to resize it a little)."""
        system.set_gsetting("org.gnome.desktop.wm.preferences", "action-double-click-titlebar", v)
        system.run_async(system.wayfire_set, None, "sonata-resize", "double_click", v)

    def _set_minimize_effect(self, v):
        self._save("dock", "minimize_effect", v)
        system.run_async(system.wayfire_set, None, "animate", "minimize_animation", minimize_animation(v))

    def _set_tall_menubar(self, on):
        """The menu bar's height is set when it starts: it starts again (~1 s)."""
        self._save("topbar", "tall", bool(on))
        import subprocess
        from ..__main__ import self_argv
        system.run_async(lambda: subprocess.run(self_argv() + ["restart", "topbar"], check=False))

    def _page_menubar(self):
        from ..shell import topbar as T
        cfg = config.load("topbar", T.DEFAULTS)
        g = group("Menu Bar")
        g.add(switch_row("Automatically hide and show the menu bar", cfg.get("autohide", False),
                         lambda on: self._save("topbar", "autohide", on),
                         subtitle="It slides back down when the pointer reaches the top of the screen"))
        g.add(switch_row("Taller menu bar", cfg.get("tall", True), self._set_tall_menubar,
                         subtitle="Like the menu bar of the newest MacBooks"))
        g.add(combo_row("Clock", [("%a %-d %b  %H:%M", "Mon 28 Sep  21:41"), ("%a %H:%M", "Mon 21:41"),
                                  ("%a %-d %b  %-I:%M %p", "Mon 28 Sep  9:41 PM"), ("%a %-I:%M %p", "Mon 9:41 PM"),
                                  ("%H:%M", "21:41")],
                        cfg["clock_format"], lambda v: self._save("topbar", "clock_format", v)))
        # every status item, in the menu bar's order (Control Center and the clock always stay)
        items = group("Show in Menu Bar")
        for key, title, sub in (("show_tray", "Background apps", "Status icons of apps running in the background "
                                 "(Discord, Steam…)"),
                                ("show_now_playing", "Now Playing", "While something plays"),
                                ("show_input", "Keyboard layout", "The layout in use (BR, US…); Ctrl+Space: the previous one"),
                                ("show_sound", "Sound", "Volume and outputs are always in Control Center"),
                                ("show_battery", "Battery", ""),
                                ("battery_percent", "Battery percentage", ""),
                                ("show_bluetooth", "Bluetooth", ""),
                                ("show_wifi", "Wi-Fi", ""),
                                ("show_spotlight", "Search", "")):
            items.add(switch_row(title, cfg.get(key, T.DEFAULTS[key]),
                                 lambda on, k=key: self._save("topbar", k, on), subtitle=sub))
        # CPU, GPU, memory, network, FPS: off, as text or as a small graph (off by default)
        from ..shell import statsui
        perf = group("Performance in Menu Bar")
        for kind in statsui.KINDS:
            now = cfg.get(f"{kind}_style", "text") if cfg.get(f"show_{kind}") else "off"
            perf.add(combo_row(statsui.TITLES[kind], [("off", "Off"), ("text", "Text"), ("graph", "Graph")], now,
                               lambda v, k=kind: self._set_stat(k, v),
                               subtitle="Frames per second of the app in front" if kind == "fps" else ""))
        from ..ui import logo as L
        app = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        # always there, usable only for "Text: custom…": showing / hiding it
        # moved the rows under it, and a click meant for the next row landed
        # on it (it turned "Sonata title bars for all apps" off once)
        text_row = Adw.EntryRow(title="Menu bar text", text=app.get("menu_text") or "", use_markup=False,
                                sensitive=app["menu_logo"] == "text:custom", show_apply_button=True)
        if hasattr(text_row, "set_max_length"):         # libadwaita 1.5+
            text_row.set_max_length(L.TEXT_MAX)
        text_row.connect("apply", lambda r: self._save("appearance", "menu_text", r.get_text().strip()))
        logo = group("Logo")
        logo.add(combo_row("Menu bar logo", L.choices(), app["menu_logo"],
                           lambda v: (self._save("appearance", "menu_logo", v),
                                      text_row.set_sensitive(v == "text:custom")),
                           subtitle="The menu at the left end of the menu bar"))
        logo.add(text_row)                                # text:custom: your words (emoji drawn in one colour)
        logo.menu_text_row = text_row                     # (tests)
        reset = self._reset_group("Reset Menu Bar", "The menu bar's options and logo back to the defaults",
                                  self.ask_reset_menubar)
        return [g, items, perf, logo, reset]

    def _page_controlcenter(self):
        """Control Center: which modules it shows (also: hold one in Control
        Center to move or remove it, Add Controls to add)."""
        from ..shell import controlcenter as CCL, statsui
        order = CCL.load()

        def toggle(mid, on):
            now = CCL.load()
            if on and mid not in now:
                now.append(mid)
            elif not on and mid in now:
                now.remove(mid)
            CCL.save(now)
        perf_ids = {"stat_" + k for k in statsui.KINDS}
        groups = []
        for title, desc, ids in (("Controls", "Hold a module in Control Center to move it.",
                                  [m for m in CCL.CATALOG if m not in perf_ids]),
                                 ("Performance", "Read only while Control Center is open.",
                                  [m for m in CCL.CATALOG if m in perf_ids])):
            g = group(title, desc)
            for mid in ids:
                g.add(switch_row(CCL.CATALOG[mid][0], mid in order, lambda on, m=mid: toggle(m, on)))
            groups.append(g)
        reset = self._reset_group("Reset Control Center", "Its modules and their order back to the defaults",
                                  self.ask_reset_controlcenter)
        return groups + [reset]

    def ask_reset_controlcenter(self):
        return ui.dialog.alert("Reset Control Center?", "Its modules and their order go back to the defaults.",
                               [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")],
                               lambda rid: rid == "reset" and self.reset_controlcenter(), parent=self)

    def reset_controlcenter(self) -> None:
        from ..shell import controlcenter as CCL
        self._save("controlcenter", "modules", CCL.DEFAULTS["modules"])
        self.rebuild_page("controlcenter")
        self.toast("Control Center reset")

    def _set_stat(self, kind: str, value: str) -> None:
        if value != "off":
            config.update("topbar", **{f"{kind}_style": value})
        self._save("topbar", f"show_{kind}", value != "off")

    # what Reset Dock puts back: options, never what's in the Dock
    # (pinned apps, folders, stacks, recents) nor the default browser
    DOCK_RESET = ("icon_size", "magnification", "magnified_size", "position", "autohide", "all_displays",
                  "show_recents", "click_minimizes", "bounce", "indicators", "edge_gap")

    def _ask_reset(self, title, body, then):
        return ui.dialog.alert(title, body, [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")],
                               lambda rid: rid == "reset" and then(), parent=self)

    def ask_reset_dock(self):
        return self._ask_reset("Reset Dock?", "The Dock's options go back to the defaults. The apps and folders "
                               "in your Dock stay.", self.reset_dock)

    def reset_dock(self) -> None:
        import copy
        from ..shell import dock as D
        for k in self.DOCK_RESET:
            self._save("dock", k, copy.deepcopy(D.DEFAULTS[k]))
        self.rebuild_page("dock")
        self.toast("Dock reset")

    def ask_reset_menubar(self):
        return self._ask_reset("Reset Menu Bar?", "The menu bar's options and logo go back to the defaults.",
                               self.reset_menubar)

    def reset_menubar(self) -> None:
        from ..shell import topbar as T
        for k, v in T.DEFAULTS.items():
            self._save("topbar", k, v)
        for k in ("menu_logo", "menu_text"):
            self._save("appearance", k, icons.APPEARANCE_DEFAULTS[k])
        self.rebuild_page("menubar")
        self.toast("Menu Bar reset")

    def ask_reset_desktop(self):
        return self._ask_reset("Reset Desktop & Windows?", "Window options go back to the defaults. Your desktop "
                               "icons stay where they are.", self.reset_desktop)

    def reset_desktop(self) -> None:
        from ..shell import dock as D
        self._save("dock", "minimize_effect", D.DEFAULTS["minimize_effect"])
        self._save("dock", "quit_on_close", D.DEFAULTS["quit_on_close"])

        def apply():
            system.wayfire_set("animate", "minimize_animation", minimize_animation(D.DEFAULTS["minimize_effect"]))
            system.set_gsetting("org.gnome.desktop.wm.preferences", "action-double-click-titlebar",
                                "toggle-maximize")
            system.wayfire_set("sonata-resize", "live", True)
        system.run_async(apply, None)
        self.rebuild_page("desktop")
        self.toast("Desktop & Windows reset")

    def _page_launchpad(self):
        g = group(names.APPS)
        reset = ui.controls.push_button("Reset…", valign=Gtk.Align.CENTER)
        reset.connect("clicked", lambda *_: ui.dialog.alert(
            f"Reset the {names.APPS} layout?", "Folders and your icon order are removed; apps are sorted by name.",
            [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")],
            lambda r: r == "reset" and (config.save("launchpad", {"pages": [], "hidden": config.load(
                "launchpad", {"pages": [], "hidden": []}).get("hidden", [])}),
                                        self.toast(f"{names.APPS} was reset")), parent=self))
        row = Adw.ActionRow(title="Layout", subtitle="Pages, folders and order")
        row.add_suffix(reset)
        g.add(row)
        return [g]

    # -- Hidden & Protected Apps: behind the user's password ----------------------------------
    def _page_hidden(self):
        """Locked until the user's password (PAM, like the lock screen) is
        typed; locks again when another section is chosen."""
        from .. import pam
        g = group("", f"Apps hidden from {names.APPS}, {names.SEARCH} and the Dock. Enter your password to see and edit them.")
        row = Adw.ActionRow(title="Password")
        row.add_prefix(Gtk.Image(icon_name="system-lock-screen-symbolic"))
        entry = ui.controls.text_field(secret=True)
        entry.set_valign(Gtk.Align.CENTER)
        entry.set_property("width-chars", 18)
        row.add_suffix(entry)
        g.add(row)
        hint = Gtk.Label(label="" if pam.available() else "PAM is not available: can't check passwords",
                         xalign=0, css_classes=["dim-label", "caption"])
        g.add(hint)
        self._hidden_lock = g
        self._hidden_groups = []

        def done(ok):
            entry.set_sensitive(True)
            if ok:
                self._show_hidden_apps()
            else:
                hint.set_label("Wrong password")
                entry.set_text("")
                entry.grab_focus()
            return False

        def check(_e):
            pw = entry.get_text()
            if pw:
                import threading
                entry.set_sensitive(False)
                user = GLib.get_user_name()
                threading.Thread(target=lambda: GLib.idle_add(done, pam.authenticate(user, pw)),
                                 daemon=True).start()
        entry.connect("activate", check)
        GLib.idle_add(lambda: (entry.grab_focus(), False)[1])
        return [g]

    def _hidden_page(self):
        return self.pages["hidden"].get_content()        # the Adw.PreferencesPage

    def _show_hidden_apps(self) -> None:
        page = self._hidden_page()
        for grp in [self._hidden_lock] + self._hidden_groups:
            if grp.get_parent() is not None:
                page.remove(grp)
        self._hidden_groups = []
        from .. import apps
        installed = {d[:-8]: i for d, i in apps.scan().items() if i.should_show()}
        hidden = [a for a in config.load("launchpad", {"pages": [], "hidden": []}).get("hidden", [])
                  if a in installed]
        h = group("Hidden apps", f"In the Hidden folder in {names.APPS} (opened with your password).")
        for did in hidden:
            info = installed[did]
            r = Adw.ActionRow(title=info.get_display_name(), use_markup=False)
            img = Gtk.Image(pixel_size=32)
            icons.set_image(img, icons.app_icon(info))
            r.add_prefix(img)
            b = ui.controls.push_button("Show", valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, did=did: (self._set_hidden(did, False), self._show_hidden_apps()))
            r.add_suffix(b)
            h.add(r)
        if not hidden:
            h.add(Adw.ActionRow(title="No hidden apps"))
        add = ui.controls.push_button("Hide an App…", halign=Gtk.Align.START, margin_top=10)
        add.connect("clicked", lambda b: self._pick_app_to_hide(b, installed, hidden))
        h.add(add)
        page.add(h)
        self._hidden_groups = [h]

    def _pick_app_to_hide(self, anchor, installed, hidden) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        search = Gtk.SearchEntry(placeholder_text="Search")
        box.append(search)
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        names = sorted(((i.get_display_name(), d) for d, i in installed.items() if d not in hidden),
                       key=lambda t: t[0].casefold())
        for name, did in names:
            r = Gtk.ListBoxRow()
            rb = Gtk.Box(spacing=8, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
            img = Gtk.Image(pixel_size=24)
            icons.set_image(img, icons.app_icon(installed[did]))
            rb.append(img)
            rb.append(Gtk.Label(label=name, xalign=0))
            r.set_child(rb)
            r.name, r.did = name.casefold(), did
            lb.append(r)
        lb.set_filter_func(lambda r: search.get_text().casefold() in r.name)
        search.connect("search-changed", lambda *_: lb.invalidate_filter())
        box.append(Gtk.ScrolledWindow(child=lb, min_content_height=300, min_content_width=280,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER))
        pop = ui.panel.popup(anchor, box)

        def picked(_lb, r):
            pop.popdown()
            self._set_hidden(r.did, True)
            self._show_hidden_apps()
        lb.connect("row-activated", picked)
        search.grab_focus()

    @staticmethod
    def _set_hidden(did: str, on: bool) -> None:
        """Hide (out of Launchpad's pages, the Dock) or show again; Launchpad
        and the Dock reload their files."""
        from .. import apps, launchpad_model as M
        data = config.load("launchpad", M.DEFAULTS)
        names = {d[:-8]: i.get_display_name() for d, i in apps.scan().items() if i.should_show()}
        m = M.Model(data, names)
        if on:
            m.hide(did)
            dock = config.load("dock", {"pinned": None})
            if dock.get("pinned") and did in dock["pinned"]:
                config.update("dock", pinned=[p for p in dock["pinned"] if p != did])
        elif did in m.hidden:
            m.hidden.remove(did)
            m.reconcile()
        config.save("launchpad", m.to_json())

    def _save(self, name: str, key: str, value) -> None:
        """Write one Sonata setting; the running component reloads it.
        config.update: locked against the Dock/menu bar writing the same file."""
        config.update(name, **{key: value})

    def _save_live(self, name: str, key: str, value) -> None:
        """A moving slider: saved at most every GLASS_LIVE_MS (live, like
        _set_glass), not once per pixel -- each save makes the running
        component reload the file. The last value is always written."""
        pend = self._live_pending = getattr(self, "_live_pending", {})
        pend.setdefault(name, {})[key] = value
        if not getattr(self, "_live_src", 0):           # throttle, not debounce: live while dragging
            self._live_src = GLib.timeout_add(self.GLASS_LIVE_MS, self._live_tick)

    def _live_tick(self) -> bool:
        self._live_src = 0                              # (this source ends by returning False)
        return self._flush_live()

    def _flush_live(self) -> bool:
        """Write the sliders' pending values now (also when Settings closes)."""
        if getattr(self, "_live_src", 0):
            GLib.source_remove(self._live_src)
            self._live_src = 0
        pend, self._live_pending = getattr(self, "_live_pending", {}), {}
        for name, values in pend.items():
            config.update(name, **values)
        return False

    def _page_sonataupdate(self):
        """About > Sonata Update: Sonata's own releases (backend/selfupdate.py),
        apart from the system's packages (Vini). Checks on open; What's New
        opens the release, Update runs its installer in a terminal (it may
        ask for sudo), Check Again asks GitHub again."""
        from .. import __version__
        from ..backend import selfupdate as SU
        g = group("Sonata Update")
        row = Adw.ActionRow(title="Checking for a new Sonata…", subtitle=f"Installed: Sonata {__version__}",
                            use_markup=False)
        icon = Gtk.Image(icon_name="sonata-launchpad", pixel_size=40, valign=Gtk.Align.CENTER)
        row.add_prefix(icon)
        spin = Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER)
        buttons = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        row.add_suffix(spin)
        row.add_suffix(buttons)
        g.add(row)

        def clear():
            while (c := buttons.get_first_child()):
                buttons.remove(c)

        def button(label, cb, suggested=False):
            buttons.append(ui.controls.push_button(label, cb, style="default" if suggested else ""))

        def check():
            clear()
            spin.set_visible(True)
            row.set_title("Checking for a new Sonata…")
            system.run_async(SU.latest_release, found)

        def found(rel):
            spin.set_visible(False)
            clear()
            when = GLib.DateTime.new_now_local().format("%H:%M")
            if rel is None:
                row.set_title("Couldn't check for a new Sonata")
                row.set_subtitle(f"Installed: Sonata {__version__} · GitHub couldn't be reached")
                button("Check Again", check)
            elif SU.newer(rel["tag"]):
                version = rel["tag"].lstrip("vV")
                row.set_title(f"Sonata {version} is available")
                row.set_subtitle(f"Installed: Sonata {__version__} · checked at {when}")
                button("What's New", lambda: Gtk.UriLauncher.new(rel["url"]).launch(self, None, None, None)
                       if hasattr(Gtk, "UriLauncher") else Gio.AppInfo.launch_default_for_uri(rel["url"], None))
                button("Update", lambda: system.run_in_terminal(SU.terminal_command(rel["tag"]))
                       or self.toast("No terminal found"), suggested=True)
            else:
                row.set_title("Sonata is up to date")
                row.set_subtitle(f"Sonata {__version__} · checked at {when}")
                button("Check Again", check)
        g.status_row = row                          # (tests)
        g.found = found
        check()
        return [g]

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
            from .. import __version__
            for k, v in (("Sonata", __version__), ("Computer", a.machine), ("Processor", a.cpu),
                         ("Memory", f"{a.memory_gb} GB"),
                         ("Graphics", ", ".join(a.gpus) or "—"), ("Kernel", a.kernel)):
                row = Adw.ActionRow(title=k)
                row.add_suffix(Gtk.Label(label=v, css_classes=["st-value"], ellipsize=Pango.EllipsizeMode.END,
                                         max_width_chars=40))
                specs.add(row)
        system.run_async(system.about, fill)
        shell = group("Sonata")
        row = Adw.ActionRow(title="Restart Sonata",
                            subtitle=f"Reloads the Dock, menu bar, {names.APPS} and wallpaper. Your apps stay open.")
        btn = ui.controls.push_button("Restart", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: (system.restart_sonata(), self.toast("Restarting Sonata…")))
        row.add_suffix(btn)
        row.set_activatable_widget(btn)
        shell.add(row)
        # detailed logs (sonata2/logs.py): hidden until the "Sonata 2 desktop"
        # line is clicked 7 times, like Android's build number
        from .. import logs
        dev = logs.dev_install()
        logs_row = switch_row("Detailed Logs", logs.verbose(), lambda on: self._set_detailed_logs(on, logs_row),
                              subtitle="Always on: development install" if dev else
                              "For finding problems: window, timing and component logs in ~/.cache/sonata2")
        logs_row.set_sensitive(not dev)
        logs_row.set_visible(logs.verbose())
        shell.add(logs_row)
        taps = {"n": 0, "t": 0.0}

        def tapped(*_a):
            now = time.monotonic()
            taps["n"] = taps["n"] + 1 if now - taps["t"] < 1.5 else 1
            taps["t"] = now
            if taps["n"] >= 7 and not logs_row.get_visible():
                taps["n"] = 0
                logs_row.set_visible(True)
                self.toast("Detailed Logs is now in About")
        click = Gtk.GestureClick()
        click.connect("released", tapped)
        sub.add_controller(click)
        sub.logs_row = logs_row                    # (tests)
        reset = group("Reset", "Both ask first, then you log out and back in.")
        for title, subtitle, what in (
                ("Reset Settings", "Every Sonata setting back to the defaults. Your Dock and Launchpad "
                                   "apps, folders and your apps' data stay.", "settings"),
                ("Reset Sonata", "Sonata as just installed: settings, Dock and Launchpad layout, history and "
                                 "caches. Notes, calendars and other app data go to the Trash.", "everything")):
            row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False)
            rb = ui.controls.push_button("Reset\u2026", lambda w=what: self.ask_factory_reset(w),
                                         style="destructive")
            rb.set_valign(Gtk.Align.CENTER)
            row.add_suffix(rb)
            reset.add(row)
        return [hero, specs, shell, reset]

    def ask_factory_reset(self, what: str):
        title, body = {
            "settings": ("Reset all settings?",
                         "Every Sonata setting goes back to the default. Your Dock and Launchpad apps and "
                         "folders, protected apps and your apps' data stay."),
            "everything": ("Reset Sonata?",
                           "Sonata goes back to how it was just installed: every setting, the Dock and "
                           "Launchpad layout, history and caches are removed. Notes, calendars and TextEdit "
                           "data are moved to the Trash."),
        }[what]
        return ui.dialog.alert(title, body, [("cancel", "Cancel", ""), ("reset", "Reset", "destructive")],
                               lambda rid: rid == "reset" and self.factory_reset(what), parent=self)

    def factory_reset(self, what: str) -> None:
        """Then a new login: Wayfire's options and the graphics card choice are read there."""
        from .. import factory_reset
        gone = (factory_reset.everything if what == "everything" else factory_reset.settings_only)()
        print(f"sonata2-settings: reset {what}: {len(gone)} items", flush=True)
        self.ask_restart("session", "The reset")

    # Settings that take effect only after a restart (each asks "now or later"):
    #   sonata   Restart Sonata    Accessibility > Graphics, Appearance > Icons, About > Detailed Logs
    #   session  Log Out           Displays > Graphics card, Security & Privacy > Use Login Password
    #   system   Restart computer  Software Update (system packages; its own Restart… button)
    RESTARTS = {"sonata": ("Restart Sonata now?", "{} takes effect when Sonata restarts. Open apps stay open.",
                           "Restart Sonata"),
                "session": ("Log out now?", "{} takes effect from the next login. Save your work first: "
                                            "apps will quit.", "Log Out"),
                "system": ("Restart the computer now?", "{} takes effect after a restart. Save your work "
                                                         "first: apps will quit.", "Restart")}

    def ask_restart(self, kind: str, what: str):
        """Restart now or later (a setting that needs it was changed)."""
        heading, body, action = self.RESTARTS[kind]

        def answered(rid):
            if rid != "now":
                self.toast("It takes effect after the next restart" if kind != "session"
                           else "It takes effect from the next login")
                return
            if kind == "sonata":
                system.restart_sonata()
            else:
                from ..shell import quitapps                 # the apps quit first (Chrome's tabs)
                self._quitting = quitapps.end_session("logout" if kind == "session" else "restart")
        return ui.dialog.alert(heading, body.format(what), [("later", "Later", ""), ("now", action, "default")],
                               answered, parent=self)

    RADIUS_ROWS = (("window", "Windows", "Their corners and title bars, other apps' too"),
                   ("dock", "Dock", ""),
                   ("menu", "Menus and panels", "Menus, the menu bar's panels, notifications, alerts"))

    def _corners_group(self):
        """Corner radii (tokens.user_radii): one slider per kind; a double-click resets it."""
        from ..ui import tokens
        g = group("Corners", "How round Sonata's windows, Dock and menus are (not the screen's corners).")
        cur = tokens.user_radii()
        self.radius_rows = {}
        for key, title, sub in self.RADIUS_ROWS:
            lo, hi = tokens.RADIUS_RANGE[key]
            row = slider_row(title, cur[key], lo, hi, lambda v, k=key: self._set_radius(k, v), subtitle=sub,
                             ends=("Square", "Round"), default=tokens.RADIUS_DEFAULTS[key])
            self.radius_rows[key] = row
            g.add(row)
        return g

    def _set_radius(self, key, value) -> None:
        """Saved a moment after the slider stops (each save re-styles every Sonata
        surface); the title bars Wayfire draws and GNOME apps' follow."""
        self._radius_pending = dict(getattr(self, "_radius_pending", {}), **{key: int(round(value))})
        if getattr(self, "_radius_src", 0):
            GLib.source_remove(self._radius_src)

        def save():
            self._radius_src = 0
            from ..ui import tokens
            radii = dict(tokens.user_radii(), **self._radius_pending)
            self._radius_pending = {}
            config.update("appearance", radius=radii)

            def wayfire():
                from .. import titlebars, wfconfig
                for sec, k, v in wfconfig.frame_options(tokens.frame()):
                    if k in ("rounded_corner_radius", "radius"):
                        system.wayfire_set(sec, k, v)
                titlebars.apply()                            # GNOME apps' corners (adwstyle)
            system.run_async(wayfire, None)
            return False
        self._radius_src = GLib.timeout_add(250, save)

    def _set_glass_titlebars(self, on) -> None:
        """Title bars of every window: the glass, or opaque (the default)."""
        self._save("appearance", "glass_titlebars", on)
        from .. import titlebars
        dark = Adw.StyleManager.get_default().get_dark()

        def apply():
            titlebars.apply()                                # GNOME apps (adwstyle)
            titlebars.apply_colors(dark)                     # the bars Wayfire draws
        system.run_async(apply, None)
        self.toast("Apps pick it up when they open again")

    def _set_detailed_logs(self, on, row):
        from .. import logs
        logs.set_verbose(on)
        self.ask_restart("sonata", "Detailed logs " + ("on" if on else "off"))


def settings_desktop_file(command: str) -> str:
    """sonata2-settings.desktop (shown in Launchpad as "System Settings")."""
    from ..apps import write_desktop_file
    return write_desktop_file("sonata2-settings.desktop",
                              "[Desktop Entry]\nType=Application\nName=System Settings\n"
                              "Comment=Sonata and system settings\nIcon=preferences-system\n"
                              "Categories=Settings;System;\nStartupWMClass=io.github.vinioliveiras.sonata2.settings\n"
                              f"Exec={command} settings\n")
