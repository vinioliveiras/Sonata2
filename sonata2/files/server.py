"""Finder's Go > Connect to Server (⌘K -- Ctrl+K): a server's address, its
shared folder opened like any other. Windows file shares (smb://), SSH
(sftp://), WebDAV (dav:// davs://), FTP and NFS, through GIO / gvfs; a name
and password are asked for when the server wants them (ui.mountop), and
can be kept in the keyring. A connected server shows in the sidebar's
Locations with an eject button, like a disk.

An address without a kind is a Windows share (Finder's default too):
"nas/fotos" -> smb://nas/fotos. The servers used last are listed under the
address (a click fills it in, a double-click connects, x forgets it).

And Network in the sidebar (when gvfs can browse it): the computers and
shares nearby."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config  # noqa: E402

SCHEMES = ("smb", "sftp", "ssh", "ftp", "ftps", "dav", "davs", "nfs", "afp")
NEEDS = {"smb": "gvfs-smb", "nfs": "gvfs-nfs", "afp": "gvfs-afp"}     # the package with that kind
RECENT_MAX = 10
NETWORK = "network:///"


def supported() -> set:
    return set(Gio.Vfs.get_default().get_supported_uri_schemes())


def can_browse_network() -> bool:
    return "network" in supported()


def normalize(text: str):
    """The uri to mount for what was typed, or None when it isn't an address."""
    text = (text or "").strip()
    if not text:
        return None
    if text.startswith("\\\\"):                                 # \\server\share (Windows)
        text = "smb://" + text[2:].replace("\\", "/")
    if "://" not in text:
        text = "smb://" + text.lstrip("/")
    scheme, _, rest = text.partition("://")
    scheme = scheme.lower()
    if scheme == "ssh":
        scheme = "sftp"
    if scheme not in SCHEMES or not rest.strip("/"):
        return None
    return f"{scheme}://{rest}"


def why_not(uri: str) -> str:
    """"" when GIO can reach this kind of server; else what's missing."""
    scheme = uri.partition("://")[0]
    if scheme in supported():
        return ""
    return f"Connecting to “{scheme}://” servers needs the {NEEDS.get(scheme, 'gvfs')} package."


# -- the servers used last ---------------------------------------------------------------------
def recent() -> list:
    return list(config.load("files-servers", {"recent": []})["recent"])


def remember(uri: str) -> None:
    config.update("files-servers", recent=([uri] + [u for u in recent() if u != uri])[:RECENT_MAX])


def forget(uri: str) -> None:
    config.update("files-servers", recent=[u for u in recent() if u != uri])


# -- connecting -------------------------------------------------------------------------------
def connect(uri: str, parent, on_done, on_error) -> None:
    """Mount `uri` (asking for a password if needed); on_done(uri) once its
    files can be listed, on_error(GLib.Error)."""
    from .. import ui
    f = Gio.File.new_for_uri(uri)

    def done(src, res):
        try:
            src.mount_enclosing_volume_finish(res)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):     # cancelled
                    on_error(e)
                return
        remember(uri)
        on_done(uri)
    f.mount_enclosing_volume(Gio.MountMountFlags.NONE, ui.mountop.MountOperation(parent), None, done)


def dialog(parent, on_connected):
    """The Connect to Server window: on_connected(uri) once it's mounted."""
    from .. import ui
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    entry = ui.controls.text_field(placeholder="smb://server/share", hexpand=True)
    entry.set_activates_default(True)
    box.append(entry)
    box.append(Gtk.Label(label="For example: smb://nas/photos · sftp://me@server · davs://cloud.example.com",
                         xalign=0, wrap=True, css_classes=["dim-label", "caption"]))
    servers = recent()
    lb = None
    if servers:
        box.append(Gtk.Label(label="Recent Servers", xalign=0, css_classes=["heading"], margin_top=4))
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, css_classes=["boxed-list"])
        for uri in servers:
            row = Gtk.ListBoxRow()
            row.uri = uri
            line = Gtk.Box(spacing=6, margin_start=8, margin_end=4, margin_top=4, margin_bottom=4)
            line.append(Gtk.Image(icon_name="network-server-symbolic"))
            line.append(Gtk.Label(label=uri, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE))
            x = Gtk.Button(icon_name="window-close-symbolic", css_classes=["flat", "circular"],
                           tooltip_text="Forget This Server", valign=Gtk.Align.CENTER)
            x.connect("clicked", lambda _b, r=row: (forget(r.uri), lb.remove(r)))
            line.append(x)
            row.set_child(line)
            lb.append(row)
        lb.connect("row-selected", lambda _l, r: r is not None and entry.set_text(r.uri))
        lb.connect("row-activated", lambda _l, r: (entry.set_text(r.uri), dlg.close(), answer("connect")))
        box.append(Gtk.ScrolledWindow(child=lb, propagate_natural_height=True, max_content_height=200,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER))

    def problem(uri):
        if uri is None:
            return "Type a server address, like smb://server/share."
        return why_not(uri)

    def answer(rid):
        if rid != "connect":
            return
        uri = normalize(entry.get_text())
        bad = problem(uri)
        if bad:
            ui.dialog.alert("You can’t connect to this server.", bad, [("ok", "OK", "default")], parent=parent)
            return
        connect(uri, parent, on_connected, lambda e: ui.dialog.alert(
            f"There was a problem connecting to the server “{uri}”.",
            e.message, [("ok", "OK", "default")], parent=parent))
    dlg = ui.dialog.alert("Connect to Server", "", [("cancel", "Cancel", ""), ("connect", "Connect", "default")],
                          answer, parent=parent)
    dlg.set_extra_child(box)
    if servers:
        entry.set_text(servers[0])
    if hasattr(dlg, "set_response_enabled"):
        entry.connect("changed", lambda e: dlg.set_response_enabled("connect", bool(e.get_text().strip())))
        dlg.set_response_enabled("connect", bool(servers))
    GLib.idle_add(lambda: (entry.grab_focus(), entry.select_region(0, -1), False)[2])
    dlg.entry, dlg.servers = entry, lb                 # (tests)
    return dlg
