"""The desktop's video wallpaper (Vini: videos as wallpapers, in a loop,
silent, playing only while the desktop has the focus, paused under any
app, and light on the machine).

- **One decoder per video.** Every display showing the same video shares
  one Gtk.MediaFile (GTK's GStreamer backend: hardware decoding where the
  system has it, frames handed to the GPU); a Gtk.Picture per display only
  draws it. The last display to let go of it stops and frees it.
- **Plays only on the desktop** (paused under Mission Control too). Wayfire tells (IPC "view-focused") which
  surface has the keyboard. An app window: paused (its last frame stays);
  nothing, or the desktop itself: playing. The menu bar, the Dock and the
  overlays (Spotlight, menus) change nothing -- clicking the Dock over an
  app doesn't start it, opening a menu on the desktop doesn't stop it.
- **Paused while locked or dark** (lockdisplay's files in the runtime
  folder): the lock screen takes the focus without being a window.
- **Silent unless asked, and looped:** no audio output at all (GStreamer's
  audio sinks ranked out for this process's player), muted besides --
  Settings > Wallpaper can turn the sound on, at its own volume. The
  loop is a new player at the end, its first frame fading in over the
  last; a stalled or failed player is started again the same way.
- **Players let go of once settled.** Paused first, freed once the pause
  has gone through: a GStreamer player dropped while its thread still held
  it (starting up, or playing: position updates every 100 ms) was never
  freed -- its pipeline, threads and ~30 MB stayed, more at every change
  (Vini: changing the wallpaper several times froze it).

    DesktopFocus.get().active          -> may a video wallpaper play now
    v = acquire(path); v.media         # a Gdk.Paintable; release(v) when done
"""
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Graphene, Gtk  # noqa: E402

# the view types (ipc-rules' "type") that are the desktop, an app, or neither
DESKTOP_TYPES = {"background"}
# Wayfire plugins that cover the desktop while on: Mission Control (scale,
# Super+Tab -- Vini: the video kept playing under it), the desktops' grid
COVERING_PLUGINS = {"scale", "expo"}
APP_TYPES = {"toplevel"}


def desktop_has_focus(view, before: bool) -> bool:
    """After a "view-focused" event: is the desktop the one in front? (view:
    the event's view, None when nothing has the keyboard.) Panels and
    overlays -- the menu bar, the Dock, Spotlight, menus -- keep `before`."""
    if not isinstance(view, dict):
        return True
    kind = view.get("type") or "toplevel"
    if kind in DESKTOP_TYPES:
        return True
    if kind in APP_TYPES:
        return False
    return before


def front_is_desktop(views) -> bool:
    """From window-rules/list-views: no app window is the active one."""
    for v in views if isinstance(views, list) else []:
        if isinstance(v, dict) and v.get("activated") and (v.get("type") or "toplevel") in APP_TYPES \
                and v.get("mapped", True) and not v.get("minimized"):
            return False
    return True


def _runtime(name: str) -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or "/tmp", name)


LOCKED_FILE = "sonata2-lock-display.json"         # lockdisplay._state()
DARK_FILE = "sonata2-idle-dark"                   # lockdisplay._dark_marker()


class AwakeGate:
    """When the lock screen's video may play: while the display is lit
    (paused once it goes dark, like the desktop's)."""

    def __init__(self):
        self.listeners = []
        self._was = self.active
        try:
            self._mon = Gio.File.new_for_path(_runtime(DARK_FILE)).monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._mon.connect("changed", lambda *_a: self._changed())
        except GLib.Error:
            self._mon = None

    @property
    def active(self) -> bool:
        return not os.path.exists(_runtime(DARK_FILE))

    def _changed(self) -> None:
        now = self.active
        if now != self._was:
            self._was = now
            for fn in list(self.listeners):
                fn(now)


class DesktopFocus:
    """Whether a video wallpaper may play: the desktop in front, not locked,
    the display not dark. Listeners are called when that changes."""
    _one = None

    @classmethod
    def get(cls):
        if cls._one is None:
            cls._one = cls()
        return cls._one

    def __init__(self, ipc=None):
        self.listeners = []
        self.desktop = True
        self.covered = set()                           # (plugin, output) on now: COVERING_PLUGINS
        self._mons = []
        if ipc is None:
            from ..wl.wfipc import WayfireIPC
            ipc = WayfireIPC()
        self.ipc = ipc
        if ipc.available:
            self.desktop = front_is_desktop(ipc.call("window-rules/list-views"))
            ipc.watch(["view-focused", "plugin-activation-state-changed"], self._focused)
        for name in (LOCKED_FILE, DARK_FILE):
            try:
                mon = Gio.File.new_for_path(_runtime(name)).monitor_file(Gio.FileMonitorFlags.NONE, None)
                mon.connect("changed", lambda *_a: self._changed())
                self._mons.append(mon)
            except GLib.Error:
                pass
        self._was = self.active

    @staticmethod
    def blocked() -> bool:
        """Locked, or the display gone dark (idle)."""
        return any(os.path.exists(_runtime(n)) for n in (LOCKED_FILE, DARK_FILE))

    @property
    def active(self) -> bool:
        return self.desktop and not self.covered and not self.blocked()

    def _focused(self, ev) -> None:
        if ev.get("event") == "plugin-activation-state-changed":
            if ev.get("plugin") in COVERING_PLUGINS:
                key = (ev["plugin"], (ev.get("output-data") or {}).get("name", ""))
                (self.covered.add if ev.get("state") else self.covered.discard)(key)
                self._changed()
            return
        view = ev.get("view")
        if not isinstance(view, dict) and self.ipc.available:
            # nothing has the keyboard: also while a window is dragged or
            # resized (Vini: dragging a window started the video) -- the
            # windows say whether one is still in front
            self.desktop = front_is_desktop(self.ipc.call("window-rules/list-views"))
        else:
            self.desktop = desktop_has_focus(view, self.desktop)
        self._changed()

    def _changed(self) -> None:
        now = self.active
        if now != self._was:
            self._was = now
            for fn in list(self.listeners):
                fn(now)


# GStreamer reads its ranks once, when the first player starts it; 0 = never
# picked, so autoaudiosink falls back to a fake sink: nothing reaches the
# speakers whatever the sound server restores for the stream (muting alone
# didn't keep it quiet -- Vini: "it still plays sound").
AUDIO_SINKS = ("pulsesink", "pipewiresink", "alsasink", "jackaudiosink", "osssink", "oss4sink",
               "openalsink", "sdlaudiosink")


def silent_ranks(existing: str = "") -> str:
    """GST_PLUGIN_FEATURE_RANK with every audio sink off (what was set stays)."""
    parts = [p for p in (existing or "").split(",") if p]
    have = {p.split(":", 1)[0] for p in parts}
    return ",".join(parts + [f"{sink}:0" for sink in AUDIO_SINKS if sink not in have])


# Settings > Wallpaper: the video's sound (off by default) and its volume
SOUND_DEFAULTS = {"video_sound": False, "video_volume": 25}
_sink_ranks = {}            # the audio sinks' own ranks, before they were turned off


def sound() -> tuple:
    """(on, volume 0..1) from wallpaper.json."""
    from .. import config
    cfg = config.load("wallpaper", SOUND_DEFAULTS)
    try:
        vol = max(0.0, min(1.0, float(cfg.get("video_volume", 25)) / 100))
    except (TypeError, ValueError):
        vol = 0.25
    return bool(cfg.get("video_sound")), vol


def _audio_sinks(on: bool) -> None:
    """The audio sinks' ranks in this process's GStreamer, once it's running
    (the environment is read only when it starts): off for a silent
    wallpaper, back to their own for one with sound."""
    try:
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
    except (ImportError, ValueError):
        return
    if not Gst.is_initialized():
        return
    reg = Gst.Registry.get()
    for name in AUDIO_SINKS:
        f = reg.lookup_feature(name)
        if f is None:
            continue
        _sink_ranks.setdefault(name, f.get_rank() or Gst.Rank.PRIMARY)
        f.set_rank(_sink_ranks[name] if on else 0)


def new_player(path: str, with_sound: bool = False):
    """A Gtk.MediaFile, with no sound unless asked. The ranks are set only
    while it's made (GStreamer starts then): apps opened from the desktop
    inherit this process's environment and keep theirs."""
    _audio_sinks(with_sound)
    if with_sound:
        return Gtk.MediaFile.new_for_filename(path)
    before = os.environ.get("GST_PLUGIN_FEATURE_RANK")
    os.environ["GST_PLUGIN_FEATURE_RANK"] = silent_ranks(before)
    try:
        media = Gtk.MediaFile.new_for_filename(path)
    finally:
        if before is None:
            os.environ.pop("GST_PLUGIN_FEATURE_RANK", None)
        else:
            os.environ["GST_PLUGIN_FEATURE_RANK"] = before
    _audio_sinks(False)                       # (GStreamer started just now: off there too)
    return media


SETTLE_MS = 500             # after the pause, before the player is freed
SETTLE_MAX_MS = 5000        # one that never opens: freed anyway


def _let_go(media) -> None:
    """Pause now, free once paused and prepared (or failed)."""
    media.pause()
    waited = [0]

    def free():
        waited[0] += SETTLE_MS
        if not (media.is_prepared() or media.get_error() is not None) and waited[0] < SETTLE_MAX_MS:
            media.pause()                    # (prepared meanwhile: GTK starts what was asked)
            return True
        media.pause()
        media.clear()                        # the decoder and its frames go now, not at the next GC
        _settling.discard(media)
        GLib.timeout_add(TRIM_AFTER_MS, _trim)
        return False
    _settling.add(media)                     # held until then
    GLib.timeout_add(SETTLE_MS, free)


_settling = set()
TRIM_AFTER_MS = 1500        # GStreamer's threads let go of the frames a moment later


def _trim() -> bool:
    """Give the freed frames back to the system: glibc keeps freed memory
    for the process, and a video's frames (33 MB each at 4K) left the
    wallpaper hundreds of MB bigger after a few changes."""
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):
        pass                                 # (not glibc: nothing to do)
    return False


_MAIN = threading.get_ident()


def _on_main(fn) -> None:
    """fn() on the main thread: GTK's GStreamer player sends
    "invalidate-contents" from its streaming thread at times (measured), and
    what follows -- players swapped, pictures redrawn -- is GTK's to do on
    the main one."""
    if threading.get_ident() == _MAIN:
        fn()
    else:
        GLib.idle_add(lambda: (fn(), False)[1])


FADE_MS = 700               # the end of the video fading into its start
WATCH_MS = 2000             # how often a playing video is checked
STALL_MS = 8000             # playing, but no new frame for this long: started again
RETRY_MAX = 3               # in a row, with no frame in between: then left on its last frame


class VideoWallpaper:
    """One video, playing for every display that shows it. Looped by hand:
    at the end a new player starts it again and the last frame fades into
    the first (Vini: a long video went black at some point after its end --
    the player's own loop seeks back, and a big file's seek could leave it
    with no picture). A player that stalls or fails is started again the
    same way; the last frame stays on screen meanwhile, never black."""

    def __init__(self, path: str, focus=None, silent: bool = False):
        """focus: when it may play (.active, .listeners) -- the desktop's by
        default (DesktopFocus); the lock screen gives its own. silent: never
        any sound (the lock screen), whatever Settings says."""
        self.path, self.users = path, 0
        self.media = None
        self.silent = silent
        self.cover = CoverPaintable(None)              # what the desktop draws: the screen filled
        self.focus = focus or DesktopFocus.get()
        self.focus.listeners.append(self._allowed)
        self._ids = []
        self._next = None                              # (player, ids) starting while the old one shows
        self._retries = 0
        self._seen = (None, 0)                         # (timestamp, when) of the last frame that moved
        self._watch = GLib.timeout_add(WATCH_MS, self._check)
        self.sound = (False, 0.0) if silent else sound()
        self._sound_mon = None
        if not silent:
            from .. import config
            self._sound_mon = config.watch("wallpaper", self._sound_changed)
        self._use(self._open())
        self._allowed(self.focus.active)

    # -- players --------------------------------------------------------------------------------
    def _open(self):
        media = new_player(self.path, self.sound[0])
        media.set_loop(False)                          # (looped here: _again)
        self._silence(media)
        return media

    def _sound_changed(self) -> None:
        """Settings: sound on / off needs a player made the other way (the
        audio output is picked when it starts): swapped with the same fade;
        a new volume applies at once."""
        was, self.sound = self.sound, sound()
        if self.media is None or was == self.sound:
            return
        if was[0] != self.sound[0]:
            self._again()
        else:
            self._silence(self.media)

    def _use(self, media) -> None:
        """`media` becomes the one shown and played."""
        self.media = media
        self.cover.set_media(media)
        self._ids = [media.connect("notify::error", self._failed),
                     media.connect("notify::prepared", lambda *_a: self._prepared()),
                     media.connect("notify::ended", lambda m, _p: m.get_ended() and self._again())]
        self._seen = (None, GLib.get_monotonic_time())

    def _drop(self, media, ids) -> None:
        for i in ids:
            if media.handler_is_connected(i):
                media.disconnect(i)
        _let_go(media)

    def _again(self) -> None:
        """From the start, on a new player; the last frame fades into its
        first once that one has a picture."""
        if self.media is None or self._next is not None:
            return
        nxt = self._open()

        def framed(*_a):
            if self._next is None or self._next[0] is not nxt or nxt.get_current_image() is None:
                return
            nxt.disconnect(self._next[1])
            self._next = None
            old, ids = self.media, self._ids
            self.cover.fade_from(old.get_current_image() if old is not None else None)
            self._use(nxt)
            if old is not None:
                self._drop(old, ids)
            self._allowed(self.focus.active)
        self._next = (nxt, nxt.connect("invalidate-contents", lambda *_a: _on_main(framed)))
        nxt.play() if self.focus.active else nxt.pause()   # (paused, it still shows its first frame)
        self._silence(nxt)

    def _check(self) -> bool:
        """A video that should be playing but shows nothing new (stuck) or
        failed: started again, a few times."""
        m = self.media
        if m is None:
            return False
        now = GLib.get_monotonic_time()
        if not self.focus.active or self._next is not None:
            self._seen = (None, now)
            return True
        ts = m.get_timestamp()
        if ts != self._seen[0]:
            if self._seen[0] is not None:
                self._retries = 0                      # it plays: retries start over
            self._seen = (ts, now)
            if m.get_error() is None:
                return True
        stuck = (now - self._seen[1]) / 1000 >= STALL_MS
        if (m.get_error() is not None or stuck) and self._retries < RETRY_MAX:
            self._retries += 1
            print(f"sonata2 wallpaper: {self.path} {'failed' if m.get_error() else 'stalled'}, "
                  f"starting it again", flush=True)
            self._again()
        return True

    # -- playing --------------------------------------------------------------------------------
    def _silence(self, m) -> None:
        """No sound unless Settings turns it on (Vini: it played with sound),
        else at its volume. Set again once the player is up and at every
        play: GStreamer's player can come up with its own volume, and GTK
        only passes a change on -- so the volume is moved and set back."""
        if m is None:
            return
        on, vol = getattr(self, "sound", (False, 0.0))
        m.set_muted(not on)
        m.set_volume(min(1.0, vol + 0.01) if vol < 1 else 0.99)
        m.set_volume(vol if on else 0.0)

    def _prepared(self) -> None:
        self._silence(self.media)
        if self.media is not None and self.media.is_prepared():
            self._allowed(self.focus.active)     # (a reused player: play once the new file is open)

    def _allowed(self, on: bool) -> None:
        if self.media is None:
            return
        if on and self.media.get_error() is None:
            if self.media.get_ended():
                self._again()                    # (it ended while paused at its last frame)
            self.media.play()
            self._silence(self.media)
        else:
            self.media.pause()                   # (the frame on screen stays: no black desktop)
        self._seen = (None, GLib.get_monotonic_time())

    def _failed(self, media, _p) -> None:
        err = media.get_error()
        if err is not None:
            print(f"sonata2 wallpaper: can't play {self.path}: {err.message}", flush=True)

    def close(self) -> None:
        if self._allowed in self.focus.listeners:
            self.focus.listeners.remove(self._allowed)
        if self._watch:
            GLib.source_remove(self._watch)
            self._watch = 0
        if self._sound_mon is not None:
            self._sound_mon.cancel()
            self._sound_mon = None
        if self._next is not None:
            nxt, i = self._next
            self._next = None
            self._drop(nxt, [i])
        if self.media is not None:
            self.cover.release()
            self._drop(self.media, self._ids)
            self.media = None


class CoverPaintable(GObject.Object, Gdk.Paintable):
    """The video drawn to fill whatever it's given, like a wallpaper: scaled
    to cover, the overflow cut off, centred (Vini: it didn't fill the
    screen). Done here, not by Gtk.Picture's fit: the size a video reports
    differs between GTK versions and GStreamer sinks; the aspect ratio is
    what's asked for. No size of its own: the picture gives it the screen.

    The player under it can change (VideoWallpaper loops on a new one): a
    frame kept from the old one fades out over the new one's (fade_from)."""

    def __init__(self, media):
        super().__init__()
        self.media = None
        self._ids = []
        self._fade = None                    # (frame paintable, start µs)
        self._fade_src = 0
        self.set_media(media)

    def set_media(self, media) -> None:
        self.release()
        self.media = media
        if media is not None:
            self._ids = [media.connect("invalidate-contents", lambda *_a: _on_main(self.invalidate_contents)),
                         media.connect("invalidate-size", lambda *_a: _on_main(self.invalidate_contents))]
        self.invalidate_contents()

    def release(self) -> None:
        if self.media is not None:
            for i in self._ids:
                if self.media.handler_is_connected(i):
                    self.media.disconnect(i)
        self._ids = []

    def has_frame(self) -> bool:
        """A frame to draw (the lock / login backdrop keeps its still until then)."""
        m = self.media
        return m is not None and m.get_intrinsic_width() > 0 and m.get_current_image() is not None

    def fade_from(self, frame) -> None:
        """`frame` (the old player's last picture) over the new one, fading out."""
        if frame is None:
            return
        self._fade = (frame, GLib.get_monotonic_time())
        if not self._fade_src:
            self._fade_src = GLib.timeout_add(16, self._fade_tick)

    def fade_alpha(self) -> float:
        if self._fade is None:
            return 0.0
        t = (GLib.get_monotonic_time() - self._fade[1]) / 1000 / FADE_MS
        return max(0.0, 1.0 - t * t * (3 - 2 * t)) if t < 1 else 0.0       # ease in-out

    def _fade_tick(self) -> bool:
        self.invalidate_contents()
        if self.fade_alpha() <= 0:
            self._fade, self._fade_src = None, 0
            return False
        return True

    def do_get_intrinsic_width(self):
        return 0

    def do_get_intrinsic_height(self):
        return 0

    def do_get_intrinsic_aspect_ratio(self):
        return 0.0

    def do_get_flags(self):
        return 0

    def do_get_current_image(self):
        if self.media is None:
            return Gdk.Paintable.new_empty(0, 0)
        return self.media.get_current_image()

    def _draw(self, snap, paintable, width, height, ratio_from=None) -> None:
        sw, sh = cover(ratio_from or paintable, width, height)
        if sw <= 0:
            return
        snap.save()
        snap.translate(Graphene.Point().init((width - sw) / 2, (height - sh) / 2))
        fx, fy = frame_shortfall(paintable) if ratio_from is None else (1.0, 1.0)
        paintable.snapshot(snap, sw / fx, sh / fy)
        snap.restore()

    def do_snapshot(self, snap, width, height):
        bounds = Graphene.Rect().init(0, 0, width, height)        # (kept: the clip reads it later)
        snap.push_clip(bounds)
        if self.media is not None:
            self._draw(snap, self.media, width, height)
        alpha = self.fade_alpha()
        if alpha > 0:
            snap.push_opacity(alpha)
            self._draw(snap, self._fade[0], width, height, ratio_from=self._fade[0])
            snap.pop()
        snap.pop()


def frame_shortfall(media) -> tuple:
    """(x, y) <= 1: how much smaller the decoded frame is than the video says
    it is. GTK's GStreamer picture draws its frame at frame size / video size
    of what it's asked, from the left: a frame a little narrower (hardware
    decoders) left a black band down the right of the screen (Vini: "the
    video wallpaper doesn't fill the right side"). Asked that much larger, the
    frame lands on the whole box; a frame bigger than the video (padding) is
    cut by GTK itself and needs nothing."""
    try:
        img = media.get_current_image()
        ratio, vh = media.get_intrinsic_aspect_ratio(), media.get_intrinsic_height()
        iw, ih = img.get_intrinsic_width(), img.get_intrinsic_height()
    except (AttributeError, TypeError):
        return 1.0, 1.0
    if not (img is not None and ratio > 0 and vh > 0 and iw > 0 and ih > 0):
        return 1.0, 1.0
    fx, fy = iw / (ratio * vh), ih / vh               # (the video's own width: its height x its shape)
    # only a small shortfall is that band; anything else isn't this
    return (fx if 0.8 <= fx < 1 else 1.0), (fy if 0.8 <= fy < 1 else 1.0)


def cover(paintable, width: float, height: float) -> tuple:
    """(w, h) to draw `paintable` at so it covers width x height, keeping its
    shape; its aspect ratio, else its size; nothing known: the box itself."""
    ratio = paintable.get_intrinsic_aspect_ratio()
    if not ratio:
        iw, ih = paintable.get_intrinsic_width(), paintable.get_intrinsic_height()
        ratio = iw / ih if iw > 0 and ih > 0 else 0
    if not ratio or width <= 0 or height <= 0:
        return width, height
    if width / height > ratio:                   # the box is wider: as wide as it, taller than it
        return width, width / ratio
    return height * ratio, height


_open = {}


def acquire(path: str) -> VideoWallpaper:
    v = _open.get(path)
    if v is None or v.media is None:
        v = _open[path] = VideoWallpaper(path)
    v.users += 1
    return v


def release(v) -> None:
    if v is None:
        return
    v.users -= 1
    if v.users <= 0:
        v.close()
        if _open.get(v.path) is v:
            del _open[v.path]
