"""A notification from a Sonata process, without waiting for the answer.

Sonata's notification server lives in the menu bar process: a call waiting
for its own reply there blocked the menu bar for 2 s, timed out and looked
failed, so the notice was sent again and again (Vini: four copies of the
same one at login). So: is a server there (one quick question), then the
notice is sent and not waited on.

    send("Steam couldn't open", "...", app="Dock", icon="steam", desktop="steam")
"""
from gi.repository import Gio, GLib


def send(summary: str, body: str, app: str = "Sonata", icon: str = "", desktop: str = "",
         urgent: bool = False) -> bool:
    """False when no notification server is there (yet)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        owned = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "NameHasOwner", GLib.Variant("(s)", ("org.freedesktop.Notifications",)),
                              GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1000, None).unpack()[0]
        if not owned:
            return False
        hints = {"urgency": GLib.Variant("y", 2 if urgent else 1)}
        if desktop:
            hints["desktop-entry"] = GLib.Variant("s", desktop.removesuffix(".desktop"))
        bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                 "org.freedesktop.Notifications", "Notify",
                 GLib.Variant("(susssasa{sv}i)", (app, 0, icon, summary, body, [], hints, -1)),
                 None, Gio.DBusCallFlags.NONE, -1, None, None, None)
        return True
    except Exception:
        return False
