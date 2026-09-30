"""Menu bar tray: background apps' status icons (macOS menu extras).

Discord, Steam, KeePassXC, Telegram, Dropbox, nm-applet... publish a
StatusNotifierItem (KDE/freedesktop SNI spec; Electron, libappindicator and
Qt use it). This module is, over plain Gio D-Bus (no extra typelibs):

- Watcher: org.kde.StatusNotifierWatcher at /StatusNotifierWatcher, owned
  on the session bus when nobody else does (queued otherwise). Items
  register with it; items whose bus name vanishes are dropped.
- Host: org.kde.StatusNotifierHost-<pid>; follows whichever watcher runs
  and keeps one `TrayItem` per registered item (Id, Title, Status, icons,
  ToolTip, Menu, ItemIsMenu), refreshed on NewIcon/NewStatus/... signals.
- TrayBox: the icons of one menu bar, left of the other status items.
  Left-click: Activate (ItemIsMenu, or an item without Activate: its menu).
  Right-click: the item's com.canonical.dbusmenu menu, shown as Sonata's
  glass menu. Middle-click: SecondaryActivate. Scroll: Scroll.
  Passive items are hidden; NeedsAttention shows the attention icon.

Everything is event-driven and async: no polling, no sync calls."""
import os
import warnings

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402

WATCHER = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
MENU_IFACE = "com.canonical.dbusmenu"
PROPS = "org.freedesktop.DBus.Properties"
ICON_PX = 16
TIMEOUT = 2000

WATCHER_XML = """<node><interface name="org.kde.StatusNotifierWatcher">
  <method name="RegisterStatusNotifierItem"><arg name="service" type="s" direction="in"/></method>
  <method name="RegisterStatusNotifierHost"><arg name="service" type="s" direction="in"/></method>
  <property name="RegisteredStatusNotifierItems" type="as" access="read"/>
  <property name="IsStatusNotifierHostRegistered" type="b" access="read"/>
  <property name="ProtocolVersion" type="i" access="read"/>
  <signal name="StatusNotifierItemRegistered"><arg type="s"/></signal>
  <signal name="StatusNotifierItemUnregistered"><arg type="s"/></signal>
  <signal name="StatusNotifierHostRegistered"/>
  <signal name="StatusNotifierHostUnregistered"/>
</interface></node>"""
# The spec's signals are StatusNotifierItemRegistered/-Unregistered (what
# every host and item listens for); "ItemRegistered" in the brief means them.



def _register(conn, path, xml, call, get=None) -> int:
    info = Gio.DBusNodeInfo.new_for_xml(xml).interfaces[0]
    fn = getattr(conn, "register_object_with_closures2", None)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return (fn or conn.register_object)(path, info, call, get, None)


def split_service(service: str, sender: str = ""):
    """"name/path", "name" or "/path" (then the sender's) -> (name, path)."""
    if service.startswith("/"):
        return sender, service
    name, slash, rest = service.partition("/")
    return name, ("/" + rest) if slash else "/StatusNotifierItem"


# -- watcher --------------------------------------------------------------------------------
class Watcher:
    """org.kde.StatusNotifierWatcher, when this process owns the name."""

    def __init__(self, conn: Gio.DBusConnection):
        self.conn = conn
        self.items = []                  # "busname/path", registration order
        self.hosts = set()
        self.owned = False
        self._reg = _register(conn, WATCHER_PATH, WATCHER_XML, self._call, self._get)
        self._noc = conn.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                          "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE,
                                          self._owner_changed)
        # queued: when another watcher (KDE, waybar...) quits, this one takes over
        self._own = Gio.bus_own_name_on_connection(conn, WATCHER, Gio.BusNameOwnerFlags.NONE,
                                                   lambda *_a: setattr(self, "owned", True),
                                                   lambda *_a: setattr(self, "owned", False))

    def _emit(self, signal, args=None) -> None:
        try:
            self.conn.emit_signal(None, WATCHER_PATH, WATCHER, signal, args)
        except GLib.Error:
            pass

    def _call(self, _conn, sender, _path, _iface, method, params, inv):
        if method == "RegisterStatusNotifierItem":
            name, path = split_service(params.unpack()[0], sender)
            key = f"{name}{path}"
            if name and key not in self.items:
                self.items.append(key)
                self._emit("StatusNotifierItemRegistered", GLib.Variant("(s)", (key,)))
            inv.return_value(None)
        elif method == "RegisterStatusNotifierHost":
            if sender not in self.hosts:
                self.hosts.add(sender)
                self._emit("StatusNotifierHostRegistered")
            inv.return_value(None)
        else:
            inv.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)

    def _get(self, _conn, _sender, _path, _iface, prop):
        if prop == "RegisteredStatusNotifierItems":
            return GLib.Variant("as", list(self.items))
        if prop == "IsStatusNotifierHostRegistered":
            return GLib.Variant("b", True)
        if prop == "ProtocolVersion":
            return GLib.Variant("i", 0)
        return None

    def _owner_changed(self, _c, _s, _p, _i, _sig, params, *_d):
        name, _old, new = params.unpack()
        if new:
            return
        if name in self.hosts:
            self.hosts.discard(name)
            self._emit("StatusNotifierHostUnregistered")
        for key in [k for k in self.items if split_service(k)[0] == name]:
            self.items.remove(key)
            self._emit("StatusNotifierItemUnregistered", GLib.Variant("(s)", (key,)))


# -- one item ------------------------------------------------------------------------------
def _strip_mnemonic(label: str) -> str:
    return label.replace("__", "\0").replace("_", "").replace("\0", "_")


def pixmap_texture(pixmaps, target: int):
    """IconPixmap a(iiay) (ARGB32, network byte order) -> Gdk.Texture of the
    size closest to `target` (the smallest one >= target, else the largest).
    `pixmaps`: a GLib.Variant 'a(iiay)' or a list of (w, h, bytes)."""
    best = None
    for w, h, data in _pixmaps(pixmaps):
        if w <= 0 or h <= 0 or data.get_size() < w * h * 4:
            continue
        s = max(w, h)
        key = (0, s) if s >= target else (1, -s)
        if best is None or key < best[0]:
            best = (key, w, h, data)
    if best is None:
        return None
    _k, w, h, data = best
    # ARGB32 big-endian is A,R,G,B in memory: GDK's A8R8G8B8, no conversion
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.A8R8G8B8, data, w * 4)


def _pixmaps(pixmaps):
    if isinstance(pixmaps, GLib.Variant):
        for i in range(pixmaps.n_children()):
            c = pixmaps.get_child_value(i)
            yield (c.get_child_value(0).get_int32(), c.get_child_value(1).get_int32(),
                   c.get_child_value(2).get_data_as_bytes())
        return
    for w, h, data in pixmaps or []:
        yield w, h, data if isinstance(data, GLib.Bytes) else GLib.Bytes.new(bytes(data))


class TrayItem:
    """A registered StatusNotifierItem: its properties, icon and actions."""

    def __init__(self, host, conn, key: str):
        self.host, self.conn, self.key = host, conn, key
        self.name, self.path = split_service(key)
        self.props = {}
        self.ready = False
        self.menu = None
        self._tex = {}                   # scale -> paintable
        self._refresh_id = 0
        self._subs = [conn.signal_subscribe(self.name, ITEM_IFACE, None, self.path, None,
                                            Gio.DBusSignalFlags.NONE, self._signal)]
        self.refresh()

    def close(self) -> None:
        for s in self._subs:
            self.conn.signal_unsubscribe(s)
        self._subs = []
        if self._refresh_id:
            GLib.source_remove(self._refresh_id)
            self._refresh_id = 0
        if self.menu:
            self.menu.close()
            self.menu = None

    # -- properties ------------------------------------------------------------------
    def _signal(self, *_a):
        # NewIcon/NewStatus/NewTitle/NewToolTip... come in bursts: one read
        if not self._refresh_id:
            self._refresh_id = GLib.timeout_add(60, self._refresh_now)

    def _refresh_now(self):
        self._refresh_id = 0
        self.refresh()
        return False

    def refresh(self) -> None:
        self.conn.call(self.name, self.path, PROPS, "GetAll", GLib.Variant("(s)", (ITEM_IFACE,)),
                       GLib.VariantType("(a{sv})"), Gio.DBusCallFlags.NONE, TIMEOUT, None, self._got)

    def _got(self, conn, res):
        try:
            d = conn.call_finish(res).get_child_value(0)
        except GLib.Error:
            return                                      # gone, or not an SNI: stays hidden
        if not self._subs:
            return                                      # removed meanwhile
        props = {}
        for i in range(d.n_children()):
            e = d.get_child_value(i)
            k, v = e.get_child_value(0).get_string(), e.get_child_value(1).get_variant()
            # pixmaps stay variants (read without a Python copy)
            props[k] = v if k.endswith("Pixmap") else v.unpack()
        self.props = props
        self._tex.clear()
        menu = props.get("Menu") or ""
        if self.menu and self.menu.path != menu:
            self.menu.close()
            self.menu = None
        if not self.menu and menu and menu != "/":
            self.menu = DBusMenu(self.conn, self.name, menu)
        self.ready = True
        self.host.changed(self)

    @property
    def id(self) -> str:
        return self.props.get("Id") or self.name

    @property
    def status(self) -> str:
        return self.props.get("Status") or "Active"

    @property
    def visible(self) -> bool:
        return self.ready and self.status != "Passive"

    @property
    def item_is_menu(self) -> bool:
        return bool(self.props.get("ItemIsMenu"))

    @property
    def tooltip(self) -> str:
        tip = self.props.get("ToolTip")
        title = tip[2] if isinstance(tip, tuple) and len(tip) > 3 and tip[2] else self.props.get("Title") or ""
        body = tip[3] if isinstance(tip, tuple) and len(tip) > 3 else ""
        if body and "<" in body:
            body = _plain(body)
        return "\n".join(t for t in (title, body) if t)

    def icon(self, scale: int = 1):
        """("name", icon_name) or ("paintable", Gdk.Paintable) or None."""
        if scale in self._tex:
            return self._tex[scale]
        p = self.props
        order = ("Attention", "") if self.status == "NeedsAttention" else ("",)
        out = None
        for pre in order:
            name = p.get(f"{pre}IconName") or ""
            out = _named_icon(name, p.get("IconThemePath") or "") if name else None
            if out is None and p.get(f"{pre}IconPixmap") is not None:
                tex = pixmap_texture(p[f"{pre}IconPixmap"], ICON_PX * scale)
                out = ("paintable", tex) if tex else None
            if out is not None:
                break
        self._tex[scale] = out
        return out

    # -- actions ------------------------------------------------------------------------
    def _item_call(self, method, args, on_error=None):
        def done(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error:
                if on_error:
                    on_error()
        self.conn.call(self.name, self.path, ITEM_IFACE, method, args, None, Gio.DBusCallFlags.NONE,
                       TIMEOUT, None, done)

    def activate(self, x: int, y: int, fallback=None) -> None:
        self._item_call("Activate", GLib.Variant("(ii)", (x, y)), fallback)

    def secondary_activate(self, x: int, y: int) -> None:
        self._item_call("SecondaryActivate", GLib.Variant("(ii)", (x, y)))

    def context_menu(self, x: int, y: int) -> None:
        self._item_call("ContextMenu", GLib.Variant("(ii)", (x, y)))

    def scroll(self, delta: int, orientation: str) -> None:
        self._item_call("Scroll", GLib.Variant("(is)", (delta, orientation)))


def _plain(markup: str) -> str:
    """ToolTip descriptions may carry (HTML-ish) markup: plain text."""
    try:
        return Pango.parse_markup(markup, -1, "\0")[2]
    except GLib.Error:
        import re
        return re.sub(r"<[^>]*>", "", markup)


_search_paths = set()


class MonoIcon(Gtk.Widget):
    """An app's tray icon as a silhouette in the menu bar's text colour
    (white in Dark, black in Light, like macOS menu extras): whatever colours
    the app draws, only the shape (alpha) is kept."""

    def __init__(self):
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.paintable = None

    def set_icon(self, ic, scale: int) -> None:
        if ic is None:
            ic = ("name", "application-x-executable-symbolic")
        if ic[0] == "name":
            theme = Gtk.IconTheme.get_for_display(self.get_display())
            ic = ("paintable", theme.lookup_icon(ic[1], None, ICON_PX, scale, Gtk.TextDirection.NONE,
                                                 Gtk.IconLookupFlags.FORCE_REGULAR))
        elif isinstance(ic[1], Gdk.Texture):
            ic = ("paintable", _silhouette(ic[1]))
        self.paintable = ic[1]
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        return ICON_PX, ICON_PX, -1, -1

    def do_snapshot(self, snap) -> None:
        if self.paintable is None:
            return
        rect = Graphene.Rect().init(0, 0, ICON_PX, ICON_PX)
        snap.push_mask(Gsk.MaskMode.ALPHA)
        self.paintable.snapshot(snap, ICON_PX, ICON_PX)
        snap.pop()
        snap.append_color(self.get_color(), rect)
        snap.pop()

def _silhouette(tex):
    """A full-colour icon as a one-tone shape: the light details drawn on a
    dark body (Discord's logo on its disc) become holes, like the template
    images macOS apps give their menu extras. One-tone icons keep their
    alpha. Returns a texture whose alpha is the shape."""
    try:
        d = Gdk.TextureDownloader.new(tex)
        d.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = d.download_bytes()
        px = data.get_data()
    except (AttributeError, GLib.Error, TypeError):
        return tex
    w, h = tex.get_width(), tex.get_height()
    lum = []
    for y in range(h):
        row = y * stride
        for x in range(w):
            i = row + x * 4
            a = px[i + 3]
            if a > 128:
                lum.append((0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255)
    if len(lum) < 8:
        return tex
    lum.sort()
    lo, hi = lum[len(lum) // 10], lum[len(lum) * 9 // 10]
    if hi - lo < 0.3:
        return tex                                      # one tone: the alpha is the shape
    out = bytearray(w * h * 4)
    for y in range(h):
        row = y * stride
        for x in range(w):
            i, o = row + x * 4, (y * w + x) * 4
            l = (0.2126 * px[i] + 0.7152 * px[i + 1] + 0.0722 * px[i + 2]) / 255
            k = min(1.0, max(0.0, (hi - l) / (hi - lo)))
            out[o + 3] = int(px[i + 3] * k)
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(bytes(out)), w * 4)


def _named_icon(name: str, theme_path: str):
    if name.startswith("/"):
        return _file_texture(name)
    if theme_path:
        for sub in ("", "hicolor/16x16/apps", "hicolor/22x22/apps", "hicolor/24x24/apps", "hicolor/32x32/apps",
                    "hicolor/48x48/apps", "hicolor/scalable/apps"):
            for ext in (".png", ".svg"):
                f = os.path.join(theme_path, sub, name + ext)
                if os.path.isfile(f):
                    return _file_texture(f)
    disp = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(disp) if disp else None
    if theme is None:
        return None
    if not theme.has_icon(name) and theme_path and os.path.isdir(theme_path) and theme_path not in _search_paths:
        _search_paths.add(theme_path)                   # a theme-structured dir (hicolor/...)
        theme.add_search_path(theme_path)
    return ("name", name) if theme.has_icon(name) else None


def _file_texture(path: str):
    try:
        return ("paintable", Gdk.Texture.new_from_filename(path))
    except GLib.Error:
        return None


# -- menus (com.canonical.dbusmenu) ---------------------------------------------------------
def layout_to_sections(layout, on_click):
    """A GetLayout node (id, props, children) -> ui.menu sections; separators
    start a new section, children-display=submenu nodes become submenus.
    on_click(id) is called when an item is chosen."""
    Item = ui.menu.Item
    sections, cur = [], []
    for child in layout[2]:
        cid, props, kids = child if isinstance(child, tuple) else child.unpack()
        if not props.get("visible", True):
            continue
        if props.get("type") == "separator":
            if cur:
                sections.append(cur)
            cur = []
            continue
        label = _strip_mnemonic(props.get("label", ""))
        enabled = bool(props.get("enabled", True))
        if props.get("children-display") == "submenu" or kids:
            sub = layout_to_sections((cid, props, kids), on_click)
            cur.append(Item(label, submenu=sub or [[Item("", enabled=False)]], enabled=enabled))
            continue
        checked = None
        if props.get("toggle-type") in ("checkmark", "radio"):
            checked = props.get("toggle-state", 0) == 1
        cur.append(Item(label, lambda *_a, i=cid: on_click(i), checked=checked, enabled=enabled))
    if cur:
        sections.append(cur)
    return sections


class DBusMenu:
    """An item's exported menu: layout on demand, clicks as Events."""

    def __init__(self, conn, name, path):
        self.conn, self.name, self.path = conn, name, path
        self.open = None                        # (popover, widget) while shown
        self._sub = conn.signal_subscribe(name, MENU_IFACE, None, path, None, Gio.DBusSignalFlags.NONE,
                                          self._signal)

    def close(self) -> None:
        if self._sub:
            self.conn.signal_unsubscribe(self._sub)
            self._sub = 0

    def _call(self, method, args, rtype=None, cb=None):
        def done(conn, res):
            try:
                out = conn.call_finish(res)
            except GLib.Error:
                out = None
            if cb:
                cb(out)
        self.conn.call(self.name, self.path, MENU_IFACE, method, args,
                       GLib.VariantType(rtype) if rtype else None, Gio.DBusCallFlags.NONE, TIMEOUT, None, done)

    def fetch(self, cb) -> None:
        """AboutToShow(0), GetLayout(0, -1, []); submenus that come empty
        (filled lazily by Qt apps) get their own AboutToShow, then one more
        GetLayout. cb(sections-ready layout tuple or None)."""
        def layout(then):
            self._call("GetLayout", GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))",
                       lambda r: then(r.unpack()[1] if r else None))

        def first(lay):
            empty = _empty_submenus(lay) if lay else []
            if not empty:
                cb(lay)
                return
            left = {"n": len(empty)}

            def one(_r):
                left["n"] -= 1
                if left["n"] == 0:
                    layout(cb)
            for i in empty[:32]:
                self._call("AboutToShow", GLib.Variant("(i)", (i,)), None, one)
            if len(empty) > 32:
                left["n"] -= len(empty) - 32
        self._call("AboutToShow", GLib.Variant("(i)", (0,)), None, lambda _r: layout(first))

    def event(self, item_id: int, kind: str = "clicked") -> None:
        self._call("Event", GLib.Variant("(isvu)", (item_id, kind, GLib.Variant("i", 0),
                                                     GLib.get_real_time() // 1000 & 0xFFFFFFFF)))

    def sections(self, layout):
        return layout_to_sections(layout, self.event)

    def _signal(self, _c, _s, _p, _i, sig, *_a):
        # LayoutUpdated / ItemsPropertiesUpdated while open: redraw in place
        if self.open and sig in ("LayoutUpdated", "ItemsPropertiesUpdated"):
            self.fetch(self._update_open)

    def _update_open(self, lay):
        if not self.open or lay is None:
            return
        pop, widget = self.open
        group = Gio.SimpleActionGroup()
        model = ui.menu._build(self.sections(lay), group)
        widget.insert_action_group("m", group)
        pop.set_menu_model(model)


def _empty_submenus(node):
    out = []
    for child in node[2]:
        cid, props, kids = child if isinstance(child, tuple) else child.unpack()
        if props.get("children-display") == "submenu" and not kids:
            out.append(cid)
        elif kids:
            out.extend(_empty_submenus((cid, props, kids)))
    return out


# -- host --------------------------------------------------------------------------------------
class Host:
    """Tracks the watcher's items; `listeners` get the changed TrayItem
    (or None: many changed)."""

    def __init__(self, conn=None):
        self.listeners = []
        self.items = {}                          # key -> TrayItem, registration order
        self.conn = conn
        if conn is None:
            try:
                self.conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            except GLib.Error:
                return                           # no session bus: no tray
        c = self.conn
        self.watcher = Watcher(c)
        self.name = f"org.kde.StatusNotifierHost-{os.getpid()}"
        Gio.bus_own_name_on_connection(c, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        for sig in ("StatusNotifierItemRegistered", "StatusNotifierItemUnregistered"):
            c.signal_subscribe(WATCHER, WATCHER, sig, WATCHER_PATH, None, Gio.DBusSignalFlags.NONE, self._signal)
        Gio.bus_watch_name_on_connection(c, WATCHER, Gio.BusNameWatcherFlags.NONE, self._appeared,
                                         self._vanished)

    def _appeared(self, conn, _name, _owner):
        conn.call(WATCHER, WATCHER_PATH, WATCHER, "RegisterStatusNotifierHost", GLib.Variant("(s)", (self.name,)),
                  None, Gio.DBusCallFlags.NONE, TIMEOUT, None, None)
        conn.call(WATCHER, WATCHER_PATH, PROPS, "Get",
                  GLib.Variant("(ss)", (WATCHER, "RegisteredStatusNotifierItems")),
                  GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, TIMEOUT, None, self._listed)

    def _listed(self, conn, res):
        try:
            keys = conn.call_finish(res).unpack()[0]
        except GLib.Error:
            return
        for k in keys:
            self._add(k)

    def _vanished(self, *_a):
        for k in list(self.items):
            self._remove(k)

    def _signal(self, _c, _s, _p, _i, sig, params, *_d):
        key = params.unpack()[0]
        if sig.endswith("Unregistered"):
            self._remove(key)
        else:
            self._add(key)

    def _add(self, key):
        if key and key not in self.items:
            self.items[key] = TrayItem(self, self.conn, key)     # shown once its properties arrive

    def _remove(self, key):
        item = self.items.pop(key, None)
        if item is not None:
            item.close()
            self.changed(item)

    def changed(self, item) -> None:
        for cb in list(self.listeners):
            cb(item)


_host = None


def host() -> Host:
    global _host
    if _host is None:
        _host = Host()
    return _host


# -- menu bar ------------------------------------------------------------------------------------
class TrayBox(Gtk.Box):
    """One menu bar's tray icons (placed left of the status items)."""

    def __init__(self, bar=None, shown: bool = True, source: Host = None):
        super().__init__(css_classes=["tray-box"], valign=Gtk.Align.CENTER)
        self.bar = bar
        self.shown = shown
        self.host = source or host()
        self.buttons = {}                        # key -> Gtk.Button
        self.host.listeners.append(self._changed)
        self._changed(None)

    def stop(self) -> None:
        if self._changed in self.host.listeners:
            self.host.listeners.remove(self._changed)

    def set_shown(self, on: bool) -> None:
        self.shown = on
        self._changed(None)

    def _changed(self, _item) -> None:
        keys = [k for k, it in self.host.items.items() if it.visible] if self.shown else []
        for k in [k for k in self.buttons if k not in keys]:
            self.remove(self.buttons.pop(k))
        prev = None
        for k in keys:
            b = self.buttons.get(k)
            if b is None:
                b = self.buttons[k] = self._button(self.host.items[k])
                self.insert_child_after(b, prev)
            elif b.get_prev_sibling() is not prev:
                self.reorder_child_after(b, prev)
            self._update(b, self.host.items[k])
            prev = b
        self.set_visible(bool(keys))

    def _update(self, b, item) -> None:
        scale = max(1, self.get_scale_factor())
        b.get_child().get_first_child().set_icon(item.icon(scale), scale)
        b.set_tooltip_text(item.tooltip or None)

    def _button(self, item) -> Gtk.Button:
        b = Gtk.Button(css_classes=["topbar-item", "icon", "tray-item"], can_focus=False, valign=Gtk.Align.CENTER)
        box = Gtk.Box(valign=Gtk.Align.CENTER)
        box.append(MonoIcon())
        b.set_child(box)
        key = item.key
        b.connect("clicked", lambda btn: self._left(btn, self.host.items.get(key)))
        g = Gtk.GestureClick(button=0)
        g.connect("pressed", lambda gest, _n, x, y: self._press(gest, b, x, y, self.host.items.get(key)))
        b.add_controller(g)
        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES |
                                       Gtk.EventControllerScrollFlags.DISCRETE)
        sc.connect("scroll", lambda _c, dx, dy: self._scroll(self.host.items.get(key), dx, dy))
        b.add_controller(sc)
        return b

    @staticmethod
    def _point(btn, x=None, y=None):
        root = btn.get_root()
        x = btn.get_width() / 2 if x is None else x
        y = btn.get_height() if y is None else y
        ok, pt = btn.compute_point(root, Graphene.Point().init(x, y)) if root else (False, None)
        return (int(pt.x), int(pt.y)) if ok else (0, 0)

    def _left(self, btn, item) -> None:
        if item is None:
            return
        if item.item_is_menu:
            self.show_menu(btn, item)
            return
        # an item without Activate (libappindicator) answers with an error: its menu
        item.activate(*self._point(btn), fallback=lambda: self.show_menu(btn, item))

    def _press(self, gest, btn, x, y, item) -> None:
        if item is None:
            return
        n = gest.get_current_button()
        if n == 3:
            gest.set_state(Gtk.EventSequenceState.CLAIMED)
            self.show_menu(btn, item)
        elif n == 2:
            gest.set_state(Gtk.EventSequenceState.CLAIMED)
            item.secondary_activate(*self._point(btn, x, y))

    def _scroll(self, item, dx, dy) -> bool:
        if item is None:
            return False
        if dy:
            item.scroll(int(round(dy)) or (1 if dy > 0 else -1), "vertical")
        if dx:
            item.scroll(int(round(dx)) or (1 if dx > 0 else -1), "horizontal")
        return True

    def show_menu(self, btn, item, done=None) -> None:
        """The item's dbusmenu as a glass menu hanging from its icon; no
        exported menu: the item's own ContextMenu."""
        if item.menu is None:
            item.context_menu(*self._point(btn))
            return
        menu = item.menu
        btn.add_css_class("open")

        def show(lay):
            if lay is None or not btn.get_mapped():
                btn.remove_css_class("open")
                return
            sections = menu.sections(lay) or [[ui.menu.Item("No Items", enabled=False)]]
            pop = ui.menu.popup(btn, sections, position=Gtk.PositionType.BOTTOM, gap=2, glass=True)
            ui.panel.align_to_start(pop, btn, 2)
            menu.open = (pop, btn)
            menu.event(0, "opened")

            def closed(*_a):
                btn.remove_css_class("open")
                if menu.open and menu.open[0] is pop:
                    menu.open = None
                menu.event(0, "closed")
            pop.connect("closed", closed)
            if done:
                done(pop)
        menu.fetch(show)
