"""A Windows disk left "dirty" (Windows didn't shut down cleanly: Fast
Startup, hibernation, a crash) can't be mounted for writing -- Linux
refuses it so nothing gets damaged. Files then opens it read-only (Vini:
DATA and WIN couldn't be opened at all), like macOS opens every NTFS disk:
its files can be read and copied; to write to it again, Windows has to be
started and shut down (or chkdsk run).

The read-only mount goes straight to UDisks (Filesystem.Mount with
options=ro): GIO's volume mount has no way to ask for it."""
from gi.repository import Gio, GLib

DIRTY_WORDS = ("dirty", "unclean", "hibernat", "fast restart", "fast startup", "not cleanly unmounted")
NOTE = ("Windows didn't shut it down cleanly, so it opened read-only: you can read and copy its files. "
        "To change them, start Windows and shut it down (not restart), or run chkdsk there.")


def is_dirty_error(message: str) -> bool:
    m = (message or "").lower()
    return any(w in m for w in DIRTY_WORDS)


def fs_type(device: str) -> str:
    """UDisks' IdType of the device ("ntfs", "vfat"...; "" if unknown)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        r = bus.call_sync("org.freedesktop.UDisks2", block_path(device), "org.freedesktop.DBus.Properties", "Get",
                          GLib.Variant("(ss)", ("org.freedesktop.UDisks2.Block", "IdType")),
                          GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 3000, None)
        return str(r.unpack()[0] or "")
    except GLib.Error:
        return ""


def try_readonly(device: str, message: str) -> bool:
    """Whether a failed mount is worth retrying read-only: the error says the
    disk is dirty, or it's NTFS (the kernel's ntfs3 only says "wrong fs type,
    bad option..." for a dirty volume; the real reason is in the kernel log)."""
    return is_dirty_error(message) or fs_type(device) == "ntfs"


def block_path(device: str) -> str:
    """/dev/nvme1n1p1 -> UDisks' object path for it."""
    name = device.rsplit("/", 1)[-1]
    return "/org/freedesktop/UDisks2/block_devices/" + "".join(c if c.isalnum() else "_" for c in name)


def mount_readonly(device: str, done) -> None:
    """done(mount point or None, error message or None), on the main loop."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error as e:
        done(None, e.message)
        return
    opts = {"options": GLib.Variant("s", "ro"), "auth.no_user_interaction": GLib.Variant("b", False)}

    def finish(conn, res):
        try:
            done(conn.call_finish(res).unpack()[0], None)
        except GLib.Error as e:
            if "AlreadyMounted" in (e.message or "") or "already mounted" in (e.message or "").lower():
                done("", None)
            else:
                done(None, e.message)
    bus.call("org.freedesktop.UDisks2", block_path(device), "org.freedesktop.UDisks2.Filesystem", "Mount",
             GLib.Variant("(a{sv})", (opts,)), GLib.VariantType("(s)"), Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION,
             60000, None, finish)
