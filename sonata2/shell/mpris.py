"""Now Playing: media players over MPRIS (D-Bus), for the Control Center
module and the menu bar item. Event-driven (NameOwnerChanged and
PropertiesChanged signals): no polling."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

PREFIX = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
IFACE = "org.mpris.MediaPlayer2.Player"


class Players:
    """The most recent player; `listeners` are called on any change."""

    def __init__(self):
        self.listeners = []
        self.names = []                # bus names, most recently active last
        self.proxy = None
        self._wanted = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            self.bus = None
            return
        self.bus.signal_subscribe("org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                                  "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, self._owner, None)
        self.bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                      None, None, Gio.DBusCallFlags.NONE, 1000, None, self._listed)

    def _listed(self, bus, res):
        try:
            names = bus.call_finish(res).unpack()[0]
        except GLib.Error:
            return
        self.names = [n for n in names if n.startswith(PREFIX)]
        self._pick()

    def _owner(self, _c, _s, _p, _i, _sig, params, *_d):
        name, old, new = params.unpack()
        if not name.startswith(PREFIX):
            return
        if new and name not in self.names:
            self.names.append(name)
        elif not new and name in self.names:
            self.names.remove(name)
        self._pick()

    def _pick(self):
        name = self.names[-1] if self.names else None
        if self.proxy is not None and self.proxy.get_name() == name:
            return
        self.proxy = None
        self._wanted = name
        if name:
            # async: a hung player must not freeze the menu bar
            Gio.DBusProxy.new(self.bus, Gio.DBusProxyFlags.NONE, None, name, PATH, IFACE, None,
                              self._made, name)
        self._changed()

    def _made(self, _src, res, name):
        try:
            proxy = Gio.DBusProxy.new_finish(res)
        except GLib.Error:
            return
        if name != getattr(self, "_wanted", None):          # another player took over meanwhile
            return
        self.proxy = proxy
        proxy.connect("g-properties-changed", lambda *_: self._changed())
        self._changed()

    def _changed(self):
        for cb in list(self.listeners):
            cb()

    # -- state ------------------------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.proxy is not None

    def _prop(self, name):
        v = self.proxy.get_cached_property(name) if self.proxy else None
        return v.unpack() if v is not None else None

    @property
    def playing(self) -> bool:
        return self._prop("PlaybackStatus") == "Playing"

    @property
    def title(self) -> str:
        meta = self._prop("Metadata") or {}
        return meta.get("xesam:title") or "Not Playing"

    @property
    def artist(self) -> str:
        meta = self._prop("Metadata") or {}
        artist = meta.get("xesam:artist") or []
        return artist if isinstance(artist, str) else ", ".join(artist)   # some players send a plain string

    @property
    def art(self) -> str:
        meta = self._prop("Metadata") or {}
        return meta.get("mpris:artUrl") or ""

    @property
    def app(self) -> str:
        return self.proxy.get_name()[len(PREFIX):].split(".")[0].capitalize() if self.proxy else ""

    def call(self, method: str) -> None:
        if self.proxy:
            self.proxy.call(method, None, Gio.DBusCallFlags.NONE, 1000, None, None)


_players = None


def players() -> Players:
    global _players
    if _players is None:
        _players = Players()
    return _players
