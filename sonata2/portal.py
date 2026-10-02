"""Sonata's file chooser portal backend: every app that asks for an Open or
Save dialog through xdg-desktop-portal (browsers, Electron, Flatpak apps,
GTK/Qt apps with portals on) gets Sonata's panels (files/chooser.py).

`sonata2 portal` owns org.freedesktop.impl.portal.desktop.sonata on the
session bus (D-Bus starts it; install.sh registers it, and the Sonata
session's portals.conf points the FileChooser and Settings at it). It
stays up for the session: Settings (Dark Mode, fonts, accent colour...,
from prefs.py) is how every app follows Sonata's appearance, live.

Interface: org.freedesktop.impl.portal.FileChooser -- OpenFile, SaveFile,
SaveFiles (each: handle, app_id, parent_window, title, options -> response,
results). Response 0 = done, 1 = cancelled, 2 = other. Each call also
exports org.freedesktop.impl.portal.Request at `handle` (Close)."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

BUS_NAME = "org.freedesktop.impl.portal.desktop.sonata"
PATH = "/org/freedesktop/portal/desktop"
XML = """<node>
<interface name="org.freedesktop.impl.portal.FileChooser">
  <method name="OpenFile">
    <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
    <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
    <arg type="a{sv}" name="options" direction="in"/>
    <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
  </method>
  <method name="SaveFile">
    <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
    <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
    <arg type="a{sv}" name="options" direction="in"/>
    <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
  </method>
  <method name="SaveFiles">
    <arg type="o" name="handle" direction="in"/><arg type="s" name="app_id" direction="in"/>
    <arg type="s" name="parent_window" direction="in"/><arg type="s" name="title" direction="in"/>
    <arg type="a{sv}" name="options" direction="in"/>
    <arg type="u" name="response" direction="out"/><arg type="a{sv}" name="results" direction="out"/>
  </method>
  <property name="version" type="u" access="read"/>
</interface>
</node>"""
SETTINGS_XML = """<node>
<interface name="org.freedesktop.impl.portal.Settings">
  <method name="ReadAll">
    <arg type="as" name="namespaces" direction="in"/><arg type="a{sa{sv}}" name="value" direction="out"/>
  </method>
  <method name="Read">
    <arg type="s" name="namespace" direction="in"/><arg type="s" name="key" direction="in"/>
    <arg type="v" name="value" direction="out"/>
  </method>
  <signal name="SettingChanged">
    <arg type="s" name="namespace"/><arg type="s" name="key"/><arg type="v" name="value"/>
  </signal>
  <property name="version" type="u" access="read"/>
</interface>
</node>"""
APPEARANCE = "org.freedesktop.appearance"
# accent colours as the portal spec wants them (sRGB 0..1), from Sonata's tokens
REQUEST_XML = """<node><interface name="org.freedesktop.impl.portal.Request">
  <method name="Close"/></interface></node>"""


def settings_values() -> dict:
    """{namespace: {key: GLib.Variant}}: Sonata's settings (prefs.py), plus
    org.freedesktop.appearance (color-scheme 1 dark / 2 light, accent, contrast)."""
    from . import config, prefs
    out = {}
    for schema, keys in prefs.keys().items():
        out[schema] = {}
        for key in keys:
            t, v = prefs.typed(schema, key)
            out[schema][key] = GLib.Variant(t, v)
    dark = prefs.get(prefs.I, "color-scheme") == "prefer-dark"
    from .ui import tokens
    accent = config.load("appearance", {"accent": "blue"}).get("accent", "blue")
    hexc = tokens.accent_hex(accent)                 # named or picked (#rrggbb)
    rgb = tuple(int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)) if hexc.startswith("#") else (0.0, 0.48, 1.0)
    reduce = config.load("appearance", {"reduce_transparency": False}).get("reduce_transparency", False)
    out[APPEARANCE] = {"color-scheme": GLib.Variant("u", 1 if dark else 2),
                       "accent-color": GLib.Variant("(ddd)", rgb),
                       "contrast": GLib.Variant("u", 1 if reduce else 0)}
    return out


def _matches(ns: str, patterns) -> bool:
    """Portal namespace patterns: exact, or a trailing ".*"."""
    if not patterns or patterns == [""]:
        return True
    return any(ns == p or (p.endswith("*") and ns.startswith(p[:-1])) for p in patterns)


def _bytes_path(v) -> str:
    """ay (NUL-terminated file name bytes) -> str."""
    raw = bytes(v or b"")
    return raw.rstrip(b"\0").decode("utf-8", "surrogateescape") if raw else ""


def parse(method: str, title: str, opts: dict) -> dict:
    """Portal options -> ChooserWindow arguments (pure: tested without D-Bus)."""
    filters = [(name, [(int(k), p) for k, p in rules]) for name, rules in opts.get("filters", [])]
    cur = opts.get("current_filter")
    index = 0
    if cur:
        for i, f in enumerate(filters):
            if f[0] == cur[0]:
                index = i
                break
        else:                                         # a filter that isn't in the list: add it
            filters.insert(0, (cur[0], [(int(k), p) for k, p in cur[1]]))
    folder = _bytes_path(opts.get("current_folder"))
    name = opts.get("current_name", "") or ""
    current_file = _bytes_path(opts.get("current_file"))
    if current_file:
        folder = folder or os.path.dirname(current_file)
        name = name or os.path.basename(current_file)
    if method == "OpenFile":
        mode = "folder" if opts.get("directory") else "open"
    elif method == "SaveFiles":
        mode = "folder"
    else:
        mode = "save"
    return {"mode": mode, "title": title, "accept_label": opts.get("accept_label", "") or "",
            "multiple": bool(opts.get("multiple")) and method == "OpenFile", "filters": filters,
            "current_filter": index, "folder": Gio.File.new_for_path(folder).get_uri() if folder else None,
            "name": name, "files": [_bytes_path(f) for f in opts.get("files", [])]}


def results(args: dict, uris, index: int) -> dict:
    """What the portal answers for the chosen uris."""
    out = {}
    if args["mode"] == "folder" and args.get("files"):         # SaveFiles: one uri per file in the folder
        base = Gio.File.new_for_uri(uris[0])
        uris = [base.get_child(os.path.basename(f)).get_uri() for f in args["files"]]
    out["uris"] = GLib.Variant("as", list(uris))
    if args["filters"]:
        name, rules = args["filters"][index]
        out["current_filter"] = GLib.Variant("(sa(us))", (name, [(int(k), p) for k, p in rules]))
    out["writable"] = GLib.Variant("b", True)
    return out


class Portal:
    def __init__(self, app):
        self.app = app
        self.windows = {}              # handle -> (window, invocation)
        self._idle = 0
        conn = app.get_dbus_connection()
        node = Gio.DBusNodeInfo.new_for_xml(XML)
        conn.register_object(PATH, node.interfaces[0], self._call, self._get, None)
        # Settings (Dark Mode, fonts, accent... for every app): lives as long as the session
        snode = Gio.DBusNodeInfo.new_for_xml(SETTINGS_XML)
        conn.register_object(PATH, snode.interfaces[0], self._settings_call,
                             lambda *_a: GLib.Variant("u", 2), None)
        self._last = settings_values()
        from . import config, prefs
        self._mons = [prefs.watch(self._settings_changed), config.watch("appearance", self._settings_changed)]
        self.conn = conn
        Gio.bus_own_name_on_connection(conn, BUS_NAME, Gio.BusNameOwnerFlags.REPLACE, None,
                                       lambda *_: app.quit())
        app.hold()
        self._arm_idle()

    def _settings_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        vals = settings_values()
        if method == "ReadAll":
            (patterns,) = params.unpack()
            res = {ns: kv for ns, kv in vals.items() if _matches(ns, patterns)}
            invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("a{sa{sv}}", res)))
        else:
            ns, key = params.unpack()
            if ns in vals and key in vals[ns]:
                invocation.return_value(GLib.Variant.new_tuple(GLib.Variant("v", vals[ns][key])))
            else:
                invocation.return_dbus_error("org.freedesktop.portal.Error.NotFound", f"{ns} {key}")

    def _settings_changed(self, *_a):
        """Tell apps what changed (they switch Dark Mode, fonts... live)."""
        new = settings_values()
        for ns, kv in new.items():
            for key, v in kv.items():
                old = self._last.get(ns, {}).get(key)
                if old is None or not old.equal(v):
                    self.conn.emit_signal(None, PATH, "org.freedesktop.impl.portal.Settings", "SettingChanged",
                                          GLib.Variant("(ssv)", (ns, key, v)))
        self._last = new

    def _get(self, _c, _s, _p, _i, prop):
        return GLib.Variant("u", 4) if prop == "version" else None

    def _call(self, conn, _sender, _path, _iface, method, params, invocation):
        handle, app_id, parent, title, options = params.unpack()
        args = parse(method, title, options)
        from .files.chooser import ChooserWindow

        def done(uris, index):
            self._forget(handle)
            if uris is None:
                invocation.return_value(GLib.Variant("(ua{sv})", (1, {})))
            else:
                invocation.return_value(GLib.Variant("(ua{sv})", (0, results(args, uris, index))))
            self._arm_idle()
        win = ChooserWindow(self.app, mode=args["mode"], title=args["title"], accept_label=args["accept_label"],
                            multiple=args["multiple"], filters=args["filters"],
                            current_filter=args["current_filter"], folder=args["folder"], name=args["name"],
                            on_done=done)
        _attach_to_parent(win, parent)
        bring_to_front(win)                          # above the asking app, always
        reg = self._export_request(conn, handle, win)
        self.windows[handle] = (win, reg)
        if self._idle:
            GLib.source_remove(self._idle)
            self._idle = 0
        win.present()

    def _export_request(self, conn, handle, win):
        node = Gio.DBusNodeInfo.new_for_xml(REQUEST_XML)

        def call(_c, _s, _p, _i, method, _params, invocation):
            invocation.return_value(None)
            if method == "Close":
                win._finish(None)
        try:
            return conn.register_object(handle, node.interfaces[0], call, None, None)
        except GLib.Error:
            return 0

    def _forget(self, handle):
        _win, reg = self.windows.pop(handle, (None, 0))
        if reg:
            self.conn.unregister_object(reg)

    def _arm_idle(self):
        """(Kept for the panels' code path.) The settings half serves the
        whole session, so the portal never quits on idle."""
        return


def bring_to_front(win, tries: int = 10) -> None:
    """The Open/Save panel always comes up above the app that asked (Vini: it
    opened under Settings' panel): Wayfire keeps it on top and focuses it
    (IPC: our window, found by this process and its title). Asked again a
    few times while Wayfire hasn't mapped it yet."""
    import os
    title = win.get_title() or ""

    def find(views):
        mine = [v for v in views if isinstance(v, dict) and v.get("pid") == os.getpid()
                and v.get("type") in (None, "toplevel") and v.get("mapped", True)]
        exact = [v for v in mine if v.get("title") == title]
        return (exact or mine or [None])[-1]

    def attempt(left):
        try:
            from .wl.wfipc import WayfireIPC
            ipc = WayfireIPC()
            view = find(ipc.call("window-rules/list-views") or [])
        except Exception:
            return False
        if view is None:
            if left > 0:
                GLib.timeout_add(80, lambda: attempt(left - 1))
            return False
        ipc.call("wm-actions/set-always-on-top", {"view_id": view["id"], "state": True})
        ipc.call("window-rules/focus-view", {"id": view["id"]})
        return False
    win.connect("map", lambda _w: GLib.idle_add(lambda: attempt(tries)))


def _attach_to_parent(win, parent: str) -> None:
    """Keep the panel on top of the app that asked ("wayland:<handle>",
    xdg-foreign); nothing when that isn't possible."""
    if not parent.startswith("wayland:"):
        return
    handle = parent.split(":", 1)[1]

    def realized(w):
        try:
            gi.require_version("GdkWayland", "4.0")
            from gi.repository import GdkWayland
            surface = w.get_surface()
            if isinstance(surface, GdkWayland.WaylandToplevel):
                surface.set_transient_for_exported(handle)
        except (ImportError, ValueError, AttributeError, GLib.Error):
            pass
    win.connect("realize", realized)
