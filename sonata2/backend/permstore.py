"""What sandboxed apps (Flatpak) were allowed through the portals (macOS:
Security & Privacy > Privacy): the camera, the location, screenshots,
notifications, running in the background. Read from and changed in the
portals' own permission store (org.freedesktop.impl.portal.PermissionStore),
the same one GNOME Settings and KDE's Flatpak Permissions use. An app turned
off here is refused next time it asks; a reset one is asked again."""
from gi.repository import Gio, GLib

BUS = "org.freedesktop.impl.portal.PermissionStore"
PATH = "/org/freedesktop/impl/portal/PermissionStore"
IFACE = "org.freedesktop.impl.portal.PermissionStore"
TIMEOUT_MS = 2000

# (key, title, table, id, "allowed" value)
KINDS = (("camera", "Camera", "devices", "camera", "yes"),
         ("location", "Location", "location", "location", None),
         ("screenshot", "Screenshots", "screenshot", "screenshot", "yes"),
         ("notifications", "Notifications", "notifications", "notification", "yes"),
         ("background", "Running in the Background", "background", "background", "yes"))


def _bus():
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def allowed(perms, yes) -> bool:
    """Whether a stored permission says yes (location keeps an accuracy level instead)."""
    if not perms:
        return False
    if yes is None:                                   # location: ["EXACT", "<time>"] / ["NONE", ...]
        return perms[0].upper() not in ("NONE", "NO")
    return perms[0] == yes


def lookup(table: str, ident: str) -> dict:
    """{app id: [permissions]} ({} when nothing is stored)."""
    bus = _bus()
    if bus is None:
        return {}
    try:
        r = bus.call_sync(BUS, PATH, IFACE, "Lookup", GLib.Variant("(ss)", (table, ident)),
                          GLib.VariantType("(a{sas}v)"), Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        return dict(r.unpack()[0])
    except GLib.Error:
        return {}                                    # no such table yet: nobody asked


def all_permissions() -> list:
    """[(key, title, [(app id, allowed)])] for every kind."""
    out = []
    for key, title, table, ident, yes in KINDS:
        apps = sorted((app, allowed(p, yes)) for app, p in lookup(table, ident).items() if app)
        out.append((key, title, apps))
    return out


def set_allowed(key: str, app: str, on: bool) -> bool:
    kind = next((k for k in KINDS if k[0] == key), None)
    bus = _bus()
    if kind is None or bus is None:
        return False
    _k, _t, table, ident, yes = kind
    if yes is None:                                   # location: an accuracy level and when it was granted
        perms = lookup(table, ident).get(app) or ["EXACT", "0"]
        value = (["EXACT"] if on else ["NONE"]) + perms[1:]
    else:
        value = [yes if on else "no"]
    try:
        bus.call_sync(BUS, PATH, IFACE, "SetPermission",
                      GLib.Variant("(sbssas)", (table, False, ident, app, value)), None,
                      Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        return True
    except GLib.Error as e:
        print(f"sonata2: permission {key} {app}: {e.message}", flush=True)
        return False


def reset(key: str, app: str) -> bool:
    """Forget the answer: the app is asked again next time."""
    kind = next((k for k in KINDS if k[0] == key), None)
    bus = _bus()
    if kind is None or bus is None:
        return False
    try:
        bus.call_sync(BUS, PATH, IFACE, "DeletePermission", GLib.Variant("(sss)", (kind[2], kind[3], app)), None,
                      Gio.DBusCallFlags.NONE, TIMEOUT_MS, None)
        return True
    except GLib.Error:
        return False
