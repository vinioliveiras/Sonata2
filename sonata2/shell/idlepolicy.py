"""What keeps the session awake: only something playing (Vini: "nothing
should keep it awake, and the lock must always work, unless audio or video
is playing").

Without input for the display's timeout: the keyboard's light and the
displays go off, and after `lock_after` more seconds the screen locks --
whatever an app asks (Chrome kept the session awake for hours). Unless
media plays (an MPRIS player playing: Spotify, a video in Chrome, a music
app; or a sound being played right now): then it waits and looks again.
Any key or move brings everything back.

Needs a compositor with ext-idle-notify-v1 version 2 (input idle, ignoring
app inhibitors); otherwise IdleLock keeps swayidle as before."""
import json
import shutil
import subprocess

from gi.repository import Gio, GLib

RECHECK_S = 30                   # idle while media plays: looked at again this often


def media_playing() -> bool:
    """An MPRIS player playing, or a sound playing right now (PulseAudio /
    PipeWire streams not paused)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                              "ListNames", None, GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 500,
                              None).unpack()[0]
        for n in names:
            if not n.startswith("org.mpris.MediaPlayer2."):
                continue
            try:
                st = bus.call_sync(n, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "Get",
                                   GLib.Variant("(ss)", ("org.mpris.MediaPlayer2.Player", "PlaybackStatus")),
                                   GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 500, None).unpack()[0]
            except GLib.Error:
                continue
            if st == "Playing":
                return True
    except GLib.Error:
        pass
    return sound_playing()


def sound_playing() -> bool:
    if not shutil.which("pactl"):
        return False
    try:
        out = subprocess.run(["pactl", "-f", "json", "list", "sink-inputs"], capture_output=True, text=True,
                             timeout=2).stdout
        streams = json.loads(out or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    for s in streams if isinstance(streams, list) else []:
        props = s.get("properties") or {}
        app = (props.get("application.name") or "") + " " + (props.get("application.process.binary") or "")
        if "sonata" in app.lower():                     # Sonata's own sounds don't count
            continue
        if not s.get("corked") and not s.get("mute"):
            return True
    return False


class IdlePolicy:
    """watch: wl.idlewatch.IdleWatch (input idle). dark(on) / lock() are the
    actions (the keyboard light, the displays; the lock screen)."""

    def __init__(self, watch, dark, lock, playing=media_playing, later=GLib.timeout_add_seconds,
                 cancel=GLib.source_remove):
        self.watch, self._dark, self._lock, self.playing = watch, dark, lock, playing
        self.later, self.cancel = later, cancel
        self.wid, self.timers, self.is_dark = 0, [], False
        self.dpms, self.lock_after = 0, -1

    def apply(self, dpms: int, lock_after: int) -> None:
        """dpms: seconds before the display goes off (<= 0: never -- the lock
        still counts from 10 min); lock_after: seconds after that (-1 never)."""
        if (dpms, lock_after) == (self.dpms, self.lock_after) and self.wid:
            return
        self.stop()
        self.dpms, self.lock_after = dpms, lock_after
        if dpms <= 0 and lock_after < 0:
            return
        self.wid = self.watch.watch(dpms if dpms > 0 else 600, self._idle, self._back)

    def stop(self) -> None:
        if self.wid:
            self.watch.unwatch(self.wid)
            self.wid = 0
        self._clear()

    def _clear(self) -> None:
        for t in self.timers:
            self.cancel(t)
        self.timers = []

    def _idle(self) -> None:
        self._clear()
        if self.playing():                              # playing: awake; looked at again
            self.timers.append(self.later(RECHECK_S, lambda: (self._idle(), False)[1]))
            return
        if self.dpms > 0:
            self.is_dark = True
            self._dark(True)
        if self.lock_after >= 0:
            if self.lock_after == 0:
                self._lock()
            else:
                self.timers.append(self.later(self.lock_after, lambda: (self._lock(), False)[1]))

    def _back(self) -> None:
        self._clear()
        if self.is_dark:
            self.is_dark = False
            self._dark(False)
