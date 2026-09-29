"""org.freedesktop.FileManager1 on the session bus, served by Sonata's Files:
what apps call for "Show in Folder" / "Open containing folder" (Chrome and
Firefox downloads, VS Code, Steam, portals...). Without it D-Bus starts
GNOME Files (Nautilus) for them.

    filemanager1.own(app, show_folders, show_items, show_properties)

The name is taken by the running Files process; when none runs, D-Bus
starts `sonata2 files --service` through the service file install.sh writes
(Sonata session only; other desktops keep their own file manager)."""
from gi.repository import Gio, GLib

NAME = "org.freedesktop.FileManager1"
PATH = "/org/freedesktop/FileManager1"
XML = """<node><interface name="org.freedesktop.FileManager1">
  <method name="ShowFolders"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
  <method name="ShowItems"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
  <method name="ShowItemProperties"><arg type="as" name="URIs" direction="in"/><arg type="s" name="StartupId" direction="in"/></method>
</interface></node>"""

_owner = None


def own(app, show_folders, show_items, show_properties) -> None:
    """Serve the interface on `app`'s bus connection (once per process)."""
    global _owner
    if _owner is not None:
        return
    conn = app.get_dbus_connection()
    if conn is None:
        return
    info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
    handlers = {"ShowFolders": show_folders, "ShowItems": show_items, "ShowItemProperties": show_properties}

    def call(_c, _sender, _path, _iface, method, params, invocation):
        uris = list(params.unpack()[0])
        fn = handlers.get(method)
        if fn:
            GLib.idle_add(lambda: (fn(uris), False)[1])
        invocation.return_value(None)
    try:
        conn.register_object(PATH, info, call, None, None)
    except GLib.Error:
        return
    _owner = Gio.bus_own_name_on_connection(conn, NAME, Gio.BusNameOwnerFlags.REPLACE, None, None)
