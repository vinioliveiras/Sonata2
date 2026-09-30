"""MPRIS server for Music: org.mpris.MediaPlayer2 and .Player on the session
bus as org.mpris.MediaPlayer2.sonata_music, so the menu bar's Now Playing,
Control Center and the media keys control it.

The window hands a `controller` with: player (Player), current_track()
(dict or None), play(), pause(), toggle(), next(), previous(), stop(),
seek_to(seconds), set_volume(0..1), raise_window(), quit(), can_next(),
can_previous()."""
import hashlib

from gi.repository import Gio, GLib

BUS_NAME = "org.mpris.MediaPlayer2.sonata_music"
PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
NO_TRACK = "/org/mpris/MediaPlayer2/TrackList/NoTrack"

XML = """<node>
  <interface name="org.mpris.MediaPlayer2">
    <method name="Raise"/><method name="Quit"/>
    <property name="CanQuit" type="b" access="read"/>
    <property name="CanRaise" type="b" access="read"/>
    <property name="HasTrackList" type="b" access="read"/>
    <property name="Identity" type="s" access="read"/>
    <property name="DesktopEntry" type="s" access="read"/>
    <property name="SupportedUriSchemes" type="as" access="read"/>
    <property name="SupportedMimeTypes" type="as" access="read"/>
  </interface>
  <interface name="org.mpris.MediaPlayer2.Player">
    <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/>
    <method name="Stop"/><method name="Play"/>
    <method name="Seek"><arg direction="in" name="Offset" type="x"/></method>
    <method name="SetPosition"><arg direction="in" name="TrackId" type="o"/>
      <arg direction="in" name="Position" type="x"/></method>
    <method name="OpenUri"><arg direction="in" name="Uri" type="s"/></method>
    <signal name="Seeked"><arg name="Position" type="x"/></signal>
    <property name="PlaybackStatus" type="s" access="read"/>
    <property name="LoopStatus" type="s" access="readwrite"/>
    <property name="Rate" type="d" access="readwrite"/>
    <property name="Shuffle" type="b" access="readwrite"/>
    <property name="Metadata" type="a{sv}" access="read"/>
    <property name="Volume" type="d" access="readwrite"/>
    <property name="Position" type="x" access="read"/>
    <property name="MinimumRate" type="d" access="read"/>
    <property name="MaximumRate" type="d" access="read"/>
    <property name="CanGoNext" type="b" access="read"/>
    <property name="CanGoPrevious" type="b" access="read"/>
    <property name="CanPlay" type="b" access="read"/>
    <property name="CanPause" type="b" access="read"/>
    <property name="CanSeek" type="b" access="read"/>
    <property name="CanControl" type="b" access="read"/>
  </interface>
</node>"""

MIME_TYPES = ["audio/mpeg", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg", "audio/opus",
              "audio/x-opus+ogg", "audio/mp4", "audio/x-m4a", "audio/aac", "audio/x-wav", "audio/wav"]
LOOP = {"off": "None", "all": "Playlist", "one": "Track"}


def track_id(path: str) -> str:
    """A D-Bus object path for a song (stable per file)."""
    return "/io/github/vinioliveiras/sonata2/music/track/t" + hashlib.sha1(path.encode()).hexdigest()[:16]


def metadata(track) -> dict:
    """MPRIS Metadata of a library entry: {key: GLib.Variant}."""
    if not track:
        return {"mpris:trackid": GLib.Variant("o", NO_TRACK)}
    md = {"mpris:trackid": GLib.Variant("o", track_id(track["path"])),
          "xesam:title": GLib.Variant("s", track.get("title") or ""),
          "xesam:artist": GLib.Variant("as", [track.get("artist") or ""]),
          "xesam:album": GLib.Variant("s", track.get("album") or ""),
          "xesam:url": GLib.Variant("s", Gio.File.new_for_path(track["path"]).get_uri())}
    if track.get("album_artist"):
        md["xesam:albumArtist"] = GLib.Variant("as", [track["album_artist"]])
    if track.get("track"):
        md["xesam:trackNumber"] = GLib.Variant("i", int(track["track"]))
    if track.get("genre"):
        md["xesam:genre"] = GLib.Variant("as", [track["genre"]])
    if track.get("duration"):
        md["mpris:length"] = GLib.Variant("x", int(track["duration"] * 1e6))
    if track.get("art"):
        md["mpris:artUrl"] = GLib.Variant("s", Gio.File.new_for_path(track["art"]).get_uri())
    return md


class Server:
    def __init__(self, controller, bus_name: str = BUS_NAME):
        self.c = controller
        self.bus = None
        self._ids = []
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)
        self._owner = Gio.bus_own_name(Gio.BusType.SESSION, bus_name, Gio.BusNameOwnerFlags.NONE,
                                       self._acquired, None, None)

    def close(self) -> None:
        if self.bus is not None:
            for i in self._ids:
                self.bus.unregister_object(i)
        self._ids = []
        if self._owner:
            Gio.bus_unown_name(self._owner)
            self._owner = 0

    def _acquired(self, bus, _name) -> None:
        self.bus = bus
        for iface in self.node.interfaces:
            try:
                self._ids.append(bus.register_object(PATH, iface, self._call, self._get, self._set))
            except GLib.Error as e:
                print(f"sonata2 music: MPRIS {iface.name}: {e.message}")

    # properties ----------------------------------------------------------------------------
    def properties(self, iface: str) -> dict:
        c = self.c
        if iface == ROOT_IFACE:
            return {"CanQuit": GLib.Variant("b", True), "CanRaise": GLib.Variant("b", True),
                    "HasTrackList": GLib.Variant("b", False), "Identity": GLib.Variant("s", "Music"),
                    "DesktopEntry": GLib.Variant("s", "io.github.vinioliveiras.sonata2.music"),
                    "SupportedUriSchemes": GLib.Variant("as", ["file"]),
                    "SupportedMimeTypes": GLib.Variant("as", MIME_TYPES)}
        p = c.player
        has = c.current_track() is not None
        return {"PlaybackStatus": GLib.Variant("s", p.status),
                "LoopStatus": GLib.Variant("s", LOOP[c.queue.repeat]),
                "Rate": GLib.Variant("d", 1.0), "MinimumRate": GLib.Variant("d", 1.0),
                "MaximumRate": GLib.Variant("d", 1.0),
                "Shuffle": GLib.Variant("b", c.queue.shuffle),
                "Metadata": GLib.Variant("a{sv}", metadata(c.current_track())),
                "Volume": GLib.Variant("d", p.volume),
                "Position": GLib.Variant("x", int(p.position * 1e6)),
                "CanGoNext": GLib.Variant("b", c.can_next()), "CanGoPrevious": GLib.Variant("b", has),
                "CanPlay": GLib.Variant("b", has or len(c.queue) > 0), "CanPause": GLib.Variant("b", has),
                "CanSeek": GLib.Variant("b", has), "CanControl": GLib.Variant("b", True)}

    def _get(self, _bus, _sender, _path, iface, prop):
        return self.properties(iface).get(prop)

    def _set(self, _bus, _sender, _path, iface, prop, value) -> bool:
        v = value.unpack()
        if prop == "Volume":
            self.c.set_volume(float(v))
        elif prop == "Shuffle":
            self.c.set_shuffle(bool(v))
        elif prop == "LoopStatus":
            self.c.set_repeat({b: a for a, b in LOOP.items()}.get(v, "off"))
        return True

    def changed(self, *props) -> None:
        """PropertiesChanged for the Player properties named (all when none)."""
        if self.bus is None:
            return
        allp = self.properties(PLAYER_IFACE)
        names = props or [k for k in allp if k != "Position"]
        changed = {k: allp[k] for k in names if k in allp}
        try:
            self.bus.emit_signal(None, PATH, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                                 GLib.Variant("(sa{sv}as)", (PLAYER_IFACE, changed, [])))
        except GLib.Error:
            pass

    def seeked(self, seconds: float) -> None:
        if self.bus is not None:
            try:
                self.bus.emit_signal(None, PATH, PLAYER_IFACE, "Seeked", GLib.Variant("(x)", (int(seconds * 1e6),)))
            except GLib.Error:
                pass

    # methods -------------------------------------------------------------------------------
    def _call(self, _bus, _sender, _path, _iface, method, params, invocation) -> None:
        c = self.c
        args = params.unpack()
        if method == "SetPosition":
            tid, pos = args
            cur = c.current_track()
            if cur and tid == track_id(cur["path"]):
                c.seek_to(pos / 1e6)
        elif method == "Seek":
            c.seek_to(c.player.position + args[0] / 1e6)
        elif method == "OpenUri":
            c.open_uris([args[0]])
        else:
            act = {"Raise": c.raise_window, "Quit": c.quit, "Next": c.next, "Previous": c.previous,
                   "Pause": c.pause, "PlayPause": c.toggle, "Stop": c.stop, "Play": c.play}.get(method)
            if act is None:
                invocation.return_dbus_error("org.freedesktop.DBus.Error.UnknownMethod", method)
                return
            act()
        invocation.return_value(None)
