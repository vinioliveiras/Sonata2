"""UDisks2 over D-Bus with plain Gio (no UDisks typelib): the object tree
(ObjectManager.GetManagedObjects), live changes (InterfacesAdded/Removed,
PropertiesChanged: mounts, labels) and async method calls. Every call asks
for interactive authorization, so UDisks' polkit rules (and Sonata's polkit
agent) decide what needs a password."""
from gi.repository import Gio, GLib

NAME = "org.freedesktop.UDisks2"
ROOT = "/org/freedesktop/UDisks2"
MANAGER = ROOT + "/Manager"
DEBOUNCE_MS = 250                 # a plug-in sends a burst of signals: one reload
LONG_MS = 60 * 60 * 1000          # Format / Check / Repair may take long


def options(**extra) -> GLib.Variant:
    """The a{sv} options of every UDisks call (user interaction allowed);
    extra keys use _ for - (tear_down=True -> "tear-down")."""
    d = {"auth.no_user_interaction": GLib.Variant("b", False)}
    for k, v in extra.items():
        key = k.replace("_", "-")
        if isinstance(v, GLib.Variant):
            d[key] = v
        elif isinstance(v, bool):
            d[key] = GLib.Variant("b", v)
        else:
            d[key] = GLib.Variant("s", str(v))
    return GLib.Variant("a{sv}", d)


def error_text(err) -> str:
    """A GLib.Error from UDisks as a sentence (the D-Bus error name stripped)."""
    if isinstance(err, GLib.Error):
        # strip_remote_error() changes a C copy in PyGObject: strip it here
        msg = err.message or ""
        if msg.startswith("GDBus.Error:"):
            name, sep, rest = msg.partition(": ")
            if sep and " " not in name:
                return rest
        return msg
    return str(err)


def dismissed(err) -> bool:
    """The user cancelled the password prompt: nothing to report."""
    name = Gio.DBusError.get_remote_error(err) if isinstance(err, GLib.Error) else ""
    return bool(name) and (name.endswith("NotAuthorizedDismissed") or name.endswith(".Cancelled"))


class Client:
    """start(on_objects, on_error): on_objects(dict) with the whole tree now
    and after every change; on_error(message) when UDisks can't be reached."""

    def __init__(self):
        self.conn = None
        self.objects = {}
        self._subs = []
        self._pending = 0
        self._on_objects = self._on_error = None

    def start(self, on_objects, on_error) -> None:
        self._on_objects, self._on_error = on_objects, on_error
        Gio.bus_get(Gio.BusType.SYSTEM, None, self._got_bus)

    def _got_bus(self, _src, res) -> None:
        try:
            self.conn = Gio.bus_get_finish(res)
        except GLib.Error as e:
            self._on_error(error_text(e))
            return
        for iface, member in (("org.freedesktop.DBus.ObjectManager", "InterfacesAdded"),
                              ("org.freedesktop.DBus.ObjectManager", "InterfacesRemoved"),
                              ("org.freedesktop.DBus.Properties", "PropertiesChanged")):
            self._subs.append(self.conn.signal_subscribe(NAME, iface, member, None, None,
                                                         Gio.DBusSignalFlags.NONE, self._changed))
        self.reload()

    def stop(self) -> None:
        if self.conn is not None:
            for s in self._subs:
                self.conn.signal_unsubscribe(s)
        self._subs = []
        if self._pending:
            GLib.source_remove(self._pending)
            self._pending = 0

    def _changed(self, *_a) -> None:
        if not self._pending:
            self._pending = GLib.timeout_add(DEBOUNCE_MS, self._reload_later)

    def _reload_later(self) -> bool:
        self._pending = 0
        self.reload()
        return False

    def reload(self) -> None:
        self.conn.call(NAME, ROOT, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                       GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 10000, None, self._got_objects)

    def _got_objects(self, conn, res) -> None:
        try:
            self.objects = conn.call_finish(res).unpack()[0]
        except GLib.Error as e:
            self._on_error(error_text(e))
            return
        self._on_objects(self.objects)

    def fetch(self, done) -> None:
        """done(objects or None): a fresh tree straight from UDisks (the last
        signal may still be debounced), for checks right before erasing."""
        def finish(conn, res):
            try:
                done(conn.call_finish(res).unpack()[0])
            except GLib.Error:
                done(None)
        self.conn.call(NAME, ROOT, "org.freedesktop.DBus.ObjectManager", "GetManagedObjects", None,
                       GLib.VariantType("(a{oa{sa{sv}}})"), Gio.DBusCallFlags.NONE, 10000, None, finish)

    # -- methods ---------------------------------------------------------------------------------
    def call(self, path: str, iface: str, method: str, args: GLib.Variant, done=None, timeout=30000) -> None:
        """done(result tuple or None, error or None) on the main loop."""
        def finish(conn, res):
            try:
                out = conn.call_finish(res).unpack()
            except GLib.Error as e:
                if done:
                    done(None, e)
                return
            if done:
                done(out, None)
        self.conn.call(NAME, path, "org.freedesktop.UDisks2." + iface, method, args, None,
                       Gio.DBusCallFlags.ALLOW_INTERACTIVE_AUTHORIZATION, timeout, None, finish)

    def mount(self, fs_path, done=None):
        self.call(fs_path, "Filesystem", "Mount", GLib.Variant.new_tuple(options()), done)

    def unmount(self, fs_path, done=None):
        self.call(fs_path, "Filesystem", "Unmount", GLib.Variant.new_tuple(options()), done)

    def set_label(self, fs_path, label, done=None):
        self.call(fs_path, "Filesystem", "SetLabel", GLib.Variant.new_tuple(GLib.Variant("s", label), options()), done)

    def check(self, fs_path, done=None):
        self.call(fs_path, "Filesystem", "Check", GLib.Variant.new_tuple(options()), done, LONG_MS)

    def repair(self, fs_path, done=None):
        self.call(fs_path, "Filesystem", "Repair", GLib.Variant.new_tuple(options()), done, LONG_MS)

    def unlock(self, path, passphrase, done=None):
        self.call(path, "Encrypted", "Unlock", GLib.Variant.new_tuple(GLib.Variant("s", passphrase), options()),
                  done)

    def lock(self, path, done=None):
        self.call(path, "Encrypted", "Lock", GLib.Variant.new_tuple(options()), done)

    def eject(self, drive_path, done=None):
        self.call(drive_path, "Drive", "Eject", GLib.Variant.new_tuple(options()), done)

    def power_off(self, drive_path, done=None):
        self.call(drive_path, "Drive", "PowerOff", GLib.Variant.new_tuple(options()), done)

    def format(self, block_path, fs_type, label="", done=None, **extra):
        """Block.Format (tear-down unmounts/locks what's on it first)."""
        opts = {"tear_down": True, **extra}
        if label:
            opts["label"] = label
        self.call(block_path, "Block", "Format", GLib.Variant.new_tuple(GLib.Variant("s", fs_type), options(**opts)),
                  done, LONG_MS)

    def create_partition(self, table_path, fs_type, label="", done=None):
        """One partition over the whole (empty) table, formatted."""
        fmt = options(label=label) if label else options()
        self.call(table_path, "PartitionTable", "CreatePartitionAndFormat",
                  GLib.Variant.new_tuple(GLib.Variant("t", 0), GLib.Variant("t", 0), GLib.Variant("s", ""),
                                         GLib.Variant("s", ""), options(), GLib.Variant("s", fs_type), fmt),
                  done, LONG_MS)

    def can(self, what: str, fs_type: str, done) -> None:
        """Manager.CanFormat / CanCheck / CanRepair -> done(available, missing utility)."""
        def finish(out, err):
            if err is not None or not out:
                done(err is None, "")
                return
            ok, util = out[0]
            done(bool(ok), util)
        self.call(MANAGER, "Manager", "Can" + what, GLib.Variant("(s)", (fs_type,)), finish, 5000)
