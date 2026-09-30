"""Videos (macOS QuickTime Player): one window per movie.

The window takes the movie's shape (fitted to 70 % of the screen) and the
movie fills it, letterboxed on black; the title bar is the compositor's.
A rounded glass control bar floats near the bottom (volume, back 10 s,
play/pause, forward 10 s, elapsed / remaining time and a scrubber). It
fades in when the pointer moves and fades out after 2.5 s without motion
while the movie plays (the pointer hides too); paused, it stays.

Keys: Space or K play/pause, ← / → 5 s back/forward, J / L 10 s,
↑ / ↓ volume, M mute, F or ⌘F full screen, Esc leaves full screen,
⌘O open, ⌘W close. Double-click the movie: full screen. Dragging the movie
moves the window. Right-click: play/pause, loop, mute, full screen,
Show in Files. Each file's position is remembered and playback resumes
there next time (config videos.json).

Playback goes through Gtk.MediaFile (GTK's GStreamer media backend); with
no backend, or a file it can't decode, the window says why. Playback
speed is not offered: Gtk.MediaStream has no rate API."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.videos"
CONFIG = "videos"
DEFAULTS = {"positions": {}, "volume": 1.0, "muted": False}
MAX_POSITIONS = 200        # remembered files (oldest forgotten first)
RESUME_MIN = 10.0          # s: closer than this to the start or the end, start over
HIDE_AFTER = 2500          # ms without motion before the controls fade (playing)
MIME_TYPES = ("video/mp4", "video/x-matroska", "video/webm", "video/x-msvideo", "video/avi", "video/quicktime",
              "video/mpeg", "video/ogg", "video/x-ogm+ogg", "video/x-flv", "video/3gpp", "video/3gpp2",
              "video/x-m4v", "video/mp2t", "video/x-ms-wmv")

ui.register("""
.vd-canvas { background: %(sys_black)s; }
.vd-hud { padding: 8px 14px 9px 14px; border-radius: %(r_dialog)s; background: %(solid_tint)s; color: %(label)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
  font-family: %(font)s; transition: opacity %(t_standard)s %(ease_out)s; }
.vd-hud.hidden { opacity: 0; }
.vd-hud button { min-width: 30px; min-height: 30px; padding: 0; border: none; border-radius: 99px;
  background: none; box-shadow: none; color: %(label)s;
  transition: background-color %(t_fast)s ease-out, filter %(t_press)s ease-out; }
.vd-hud button:hover { background: %(tool_hover)s; }
.vd-hud button:active { background: %(bar_item_active)s; filter: brightness(0.85); transition: none; }
.vd-hud button image { -gtk-icon-size: 18px; }
.vd-hud button.vd-play { min-width: 38px; min-height: 38px; }
.vd-hud button.vd-play image { -gtk-icon-size: 26px; }
.vd-hud button.vd-small { min-width: 24px; min-height: 24px; color: %(label_secondary)s; }
.vd-hud button.vd-small image { -gtk-icon-size: 14px; }
.vd-hud label.vd-time { font-size: %(text_small)s; font-feature-settings: "tnum"; color: %(label_secondary)s;
  min-width: 46px; }
.vd-hud label.vd-skip { font-size: 7px; font-weight: 700; }
.vd-hud scale.sonata-slider { padding: 4px 0; min-width: 0; }
.vd-hud scale.sonata-slider trough { min-height: 4px; background: %(control_off)s; }
.vd-hud scale.sonata-slider highlight { background: %(label_secondary)s; }
.vd-hud scale.sonata-slider slider { min-width: 12px; min-height: 12px; margin: -4px; }
.vd-hud scale.vd-volume { min-width: 64px; }
.vd-message { font-family: %(font)s; }
.vd-message label.vd-msg-title { color: %(on_scrim)s; font-size: %(text_title)s; font-weight: 600; }
.vd-message label.vd-msg-body { color: %(on_scrim_secondary)s; font-size: %(text_body)s; }
""", key="videos")


# -- pure logic (tested) ----------------------------------------------------------------------
def fmt_time(seconds: float, remaining: bool = False) -> str:
    """QuickTime's clock: 0:05, 12:34, 1:02:03; remaining time gets a minus."""
    s = max(0, int(seconds + (0.999 if remaining else 0)))   # remaining rounds up: never "-0:00" too early
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    text = f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"
    return "-" + text if remaining else text


def fit_size(w: int, h: int, max_w: float, max_h: float, min_w: int = 320, min_h: int = 180) -> tuple:
    """A window of the movie's shape that fits (max_w, max_h), never larger
    than the movie itself."""
    w, h = max(1, w), max(1, h)
    s = min(1.0, max_w / w, max_h / h)
    return max(min_w, int(w * s)), max(min_h, int(h * s))


def load_position(path: str) -> float:
    """Where the movie was left (seconds), 0 for none."""
    pos = config.load(CONFIG, DEFAULTS)["positions"]
    try:
        return float(pos.get(path, 0) or 0) if isinstance(pos, dict) else 0.0
    except (TypeError, ValueError):
        return 0.0


def save_position(path: str, position: float, duration: float) -> None:
    """Remember where the movie is, or forget it when it's (almost) at the
    start or the end. The latest file goes last; the oldest are dropped."""
    pos = config.load(CONFIG, DEFAULTS)["positions"]
    pos = dict(pos) if isinstance(pos, dict) else {}
    pos.pop(path, None)
    if position >= RESUME_MIN and (duration <= 0 or duration - position > RESUME_MIN):
        pos[path] = round(position, 1)
    while len(pos) > MAX_POSITIONS:
        pos.pop(next(iter(pos)))
    config.update(CONFIG, positions=pos)


def volume_icon(volume: float, muted: bool) -> str:
    if muted or volume <= 0:
        return "sonata-volume-muted-symbolic"
    return "sonata-volume-%d-symbolic" % (1 if volume < 0.34 else 2 if volume < 0.67 else 3)


def media_file(path: str):
    """The movie as a Gtk.MediaStream (GTK's media backend)."""
    return Gtk.MediaFile.new_for_filename(path)


# -- window -----------------------------------------------------------------------------------
class VideoWindow(Gtk.ApplicationWindow):
    def __init__(self, app, path: str, stream_factory=media_file):
        if not GLib.get_application_name():
            GLib.set_application_name("Videos")
        super().__init__(application=app, css_classes=["sonata-videos"])
        ui.window.standard(self)
        self.path = path
        self.stream = None
        self._stream_factory = stream_factory
        self._handlers = []
        self._sized = False
        self._hide_src = 0
        self._over_hud = False
        self._last_xy = None
        self._syncing = False
        self._shown_secs = (-1, -1)
        cfg = config.load(CONFIG, DEFAULTS)
        self.volume = max(0.0, min(1.0, float(cfg.get("volume", 1.0) or 0)))
        self.muted = bool(cfg.get("muted"))

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        handle = Gtk.WindowHandle(child=self.picture)            # drag the movie: the window moves
        self.overlay = Gtk.Overlay(child=handle, css_classes=["vd-canvas"])
        self.message = self._build_message()
        self.overlay.add_overlay(self.message)
        self.hud = self._build_hud()
        self.overlay.add_overlay(self.hud)
        self.set_child(self.overlay)

        mon = Gdk.Display.get_default().get_monitors().get_item(0)
        g = mon.get_geometry() if mon else None
        self._max = (g.width * 0.7, g.height * 0.7) if g else (1100, 700)
        self.set_default_size(*fit_size(1920, 1080, *self._max))
        self._input()
        self.connect("close-request", self._close_request)
        self.connect("notify::fullscreened", lambda *_: self._show_hud())
        self.open(path)

    # -- building ------------------------------------------------------------------------------
    def _button(self, icon, tip, cb, css=()):
        b = Gtk.Button(icon_name=icon, tooltip_text=tip, can_focus=False, css_classes=list(css))
        b.connect("clicked", lambda _b: cb())
        return b

    def _build_hud(self) -> Gtk.Widget:
        hud = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["vd-hud"],
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.END, margin_bottom=22, margin_start=16,
                      margin_end=16)
        hud.set_size_request(440, -1)
        top = Gtk.CenterBox()
        vol = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.mute_btn = self._button(volume_icon(self.volume, self.muted), "Mute", self.toggle_mute, ["vd-small"])
        vol.append(self.mute_btn)
        self.volume_scale = ui.controls.slider(self.volume * 100, self._volume_changed, lower=0, upper=100)
        self.volume_scale.add_css_class("vd-volume")
        self.volume_scale.set_hexpand(False)
        self.volume_scale.set_can_focus(False)          # arrows stay the movie's keys
        self.volume_scale.set_valign(Gtk.Align.CENTER)
        vol.append(self.volume_scale)
        top.set_start_widget(vol)
        transport = Gtk.Box(spacing=10, valign=Gtk.Align.CENTER)
        transport.append(self._button("media-seek-backward-symbolic", "Back 10 Seconds", lambda: self.skip(-10)))
        self.play_btn = self._button("media-playback-start-symbolic", "Play", self.toggle_play, ["vd-play"])
        transport.append(self.play_btn)
        transport.append(self._button("media-seek-forward-symbolic", "Forward 10 Seconds", lambda: self.skip(10)))
        top.set_center_widget(transport)
        self.full_btn = self._button("view-fullscreen-symbolic", "Enter Full Screen", self.toggle_fullscreen,
                                     ["vd-small"])
        self.full_btn.set_valign(Gtk.Align.CENTER)
        top.set_end_widget(self.full_btn)
        hud.append(top)
        bottom = Gtk.Box(spacing=8)
        self.elapsed = Gtk.Label(label="0:00", xalign=0, css_classes=["vd-time"])
        self.remaining = Gtk.Label(label="-0:00", xalign=1, css_classes=["vd-time"])
        self.scrubber = ui.controls.slider(0, self._scrubbed, lower=0, upper=1000)
        self.scrubber.set_valign(Gtk.Align.CENTER)
        self.scrubber.set_can_focus(False)
        bottom.append(self.elapsed)
        bottom.append(self.scrubber)
        bottom.append(self.remaining)
        hud.append(bottom)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: setattr(self, "_over_hud", True))
        motion.connect("leave", lambda *_: (setattr(self, "_over_hud", False), self._arm_hide()))
        hud.add_controller(motion)
        return hud

    def _build_message(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["vd-message"],
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, visible=False, can_target=False)
        box.append(Gtk.Label(label="This video can't be played", css_classes=["vd-msg-title"]))
        self.reason = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=60,
                                css_classes=["vd-msg-body"])
        box.append(self.reason)
        return box

    # -- file ----------------------------------------------------------------------------------
    def open(self, path: str) -> None:
        self._release()
        self.path = path
        self._sized = False
        self.set_title(os.path.basename(path))
        self.message.set_visible(False)
        self.hud.set_visible(True)
        try:
            stream = self._stream_factory(path)
        except Exception as e:                       # never crash on a broken backend
            self._fail(str(e))
            return
        self.stream = stream
        stream.set_volume(self.volume)
        stream.set_muted(self.muted)
        for sig, cb in (("notify::prepared", self._prepared), ("notify::error", self._errored),
                        ("notify::playing", self._playing_changed), ("notify::ended", self._ended),
                        ("notify::timestamp", self._tick), ("notify::duration", self._tick)):
            self._handlers.append(stream.connect(sig, cb))
        self.picture.set_paintable(stream)
        if os.path.exists(path):
            Gtk.RecentManager.get_default().add_item(Gio.File.new_for_path(path).get_uri())
        if stream.get_error() is not None:
            self._errored()
        elif stream.is_prepared():
            self._prepared()
        self._playing_changed()
        self._show_hud()

    def _release(self) -> None:
        """Let go of the current stream (remembering where it was)."""
        s = self.stream
        if s is None:
            return
        self._remember()
        for h in self._handlers:
            s.disconnect(h)
        self._handlers = []
        s.pause()
        if isinstance(s, Gtk.MediaFile):
            s.clear()
        self.picture.set_paintable(None)
        self.stream = None

    def _remember(self) -> None:
        s = self.stream
        if s is None or s.get_error() is not None or not s.is_prepared():
            return
        save_position(self.path, s.get_timestamp() / 1e6, s.get_duration() / 1e6)

    def _fail(self, reason: str) -> None:
        self.reason.set_label(reason)
        self.message.set_visible(True)
        self.hud.set_visible(False)
        self._set_cursor(True)

    def _errored(self, *_a) -> None:
        err = self.stream.get_error() if self.stream else None
        if err is None:
            return
        msg = err.message or str(err)
        if "media module" in msg or "backend" in msg.lower():
            msg = "No media backend is installed (GTK's GStreamer module and its plugins)."
        self._fail(msg)

    def _prepared(self, *_a) -> None:
        s = self.stream
        if s is None or not s.is_prepared() or s.get_error() is not None:
            return
        w, h = s.get_intrinsic_width(), s.get_intrinsic_height()
        if not self._sized and w > 0 and h > 0 and not (self.is_fullscreen() or self.is_maximized()):
            self._sized = True
            self.set_default_size(*fit_size(w, h, *self._max))
        resume = load_position(self.path)
        if resume and s.is_seekable() and (s.get_duration() <= 0 or resume < s.get_duration() / 1e6):
            s.seek(int(resume * 1e6))
        self._tick()

    # -- playback ------------------------------------------------------------------------------
    def toggle_play(self) -> None:
        s = self.stream
        if s is None or s.get_error() is not None:
            return
        if s.get_playing():
            s.pause()
        else:
            if s.get_ended():
                s.seek(0)
            s.play()

    def skip(self, seconds: float) -> None:
        self.seek_to((self.stream.get_timestamp() / 1e6 if self.stream else 0) + seconds)

    def seek_to(self, seconds: float) -> None:
        s = self.stream
        if s is None or not s.is_seekable():
            return
        dur = s.get_duration() / 1e6
        seconds = max(0.0, min(seconds, dur) if dur > 0 else seconds)
        s.seek(int(seconds * 1e6))
        self._show_hud()

    def set_volume(self, v: float) -> None:
        self.volume = max(0.0, min(1.0, v))
        if self.volume > 0:
            self.muted = False
        self._apply_volume()
        self._syncing = True
        self.volume_scale.set_value(self.volume * 100)
        self._syncing = False

    def _volume_changed(self, v) -> None:
        if not self._syncing:
            self.volume = v / 100
            self.muted = False
            self._apply_volume()

    def toggle_mute(self) -> None:
        self.muted = not self.muted
        self._apply_volume()

    def _apply_volume(self) -> None:
        if self.stream is not None:
            self.stream.set_volume(self.volume)
            self.stream.set_muted(self.muted)
        self.mute_btn.set_icon_name(volume_icon(self.volume, self.muted))
        self.mute_btn.set_tooltip_text("Unmute" if self.muted else "Mute")
        config.update(CONFIG, volume=round(self.volume, 3), muted=self.muted)

    def toggle_loop(self) -> None:
        if self.stream is not None:
            self.stream.set_loop(not self.stream.get_loop())

    def _playing_changed(self, *_a) -> None:
        playing = bool(self.stream and self.stream.get_playing())
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_btn.set_tooltip_text("Pause" if playing else "Play")
        if playing:
            self._arm_hide()
        else:
            self._show_hud()

    def _ended(self, *_a) -> None:
        if self.stream and self.stream.get_ended():
            self._show_hud()

    def _scrubbed(self, v) -> None:
        s = self.stream
        if self._syncing or s is None or s.get_duration() <= 0:
            return
        self.seek_to(v / 1000 * s.get_duration() / 1e6)

    def _tick(self, *_a) -> None:
        """Clock and scrubber follow the stream (only while they show)."""
        s = self.stream
        if s is None or self.hud.has_css_class("hidden"):
            return
        ts, dur = s.get_timestamp() / 1e6, s.get_duration() / 1e6
        secs = (int(ts), int(max(0.0, dur - ts) + 0.999))
        if secs != self._shown_secs:
            self._shown_secs = secs
            self.elapsed.set_label(fmt_time(ts))
            self.remaining.set_label(fmt_time(max(0.0, dur - ts), remaining=True))
        self.scrubber.set_sensitive(dur > 0 and s.is_seekable())
        if dur > 0:
            v = ts / dur * 1000
            if abs(self.scrubber.get_value() - v) >= 0.5:
                self._syncing = True
                self.scrubber.set_value(v)
                self._syncing = False

    # -- controls fading -----------------------------------------------------------------------
    def _show_hud(self) -> None:
        if self.hud.has_css_class("hidden"):
            self.hud.remove_css_class("hidden")
            self.hud.set_can_target(True)
            self._shown_secs = (-1, -1)
            self._tick()
        self._set_cursor(True)
        self._arm_hide()

    def _arm_hide(self) -> None:
        if self._hide_src:
            GLib.source_remove(self._hide_src)
            self._hide_src = 0
        if self.stream is not None and self.stream.get_playing():
            self._hide_src = GLib.timeout_add(HIDE_AFTER, self._hide_now)

    def _hide_now(self) -> bool:
        self._hide_src = 0
        if self._over_hud or not (self.stream and self.stream.get_playing()):
            return False
        self.hud.add_css_class("hidden")
        self.hud.set_can_target(False)
        self._set_cursor(False)
        return False

    def _set_cursor(self, visible: bool) -> None:
        self.overlay.set_cursor(None if visible else Gdk.Cursor.new_from_name("none"))

    def toggle_fullscreen(self) -> None:
        full = not self.is_fullscreen()
        self.fullscreen() if full else self.unfullscreen()
        self.full_btn.set_icon_name("view-restore-symbolic" if full else "view-fullscreen-symbolic")
        self.full_btn.set_tooltip_text("Exit Full Screen" if full else "Enter Full Screen")

    # -- input ---------------------------------------------------------------------------------
    def _input(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        motion = Gtk.EventControllerMotion()

        def moved(_c, x, y):
            if self._last_xy is None or abs(x - self._last_xy[0]) + abs(y - self._last_xy[1]) > 1:
                self._last_xy = (x, y)
                self._show_hud()

        def left(*_a):
            self._last_xy = None
            if self.stream is not None and self.stream.get_playing() and not self.is_fullscreen():
                self._hide_now()                    # the pointer left the movie: controls go (QuickTime)
        motion.connect("motion", moved)
        motion.connect("leave", left)
        self.overlay.add_controller(motion)
        click = Gtk.GestureClick(propagation_phase=Gtk.PropagationPhase.CAPTURE)

        def pressed(g, n, _x, _y):
            if n == 2:                                      # the movie, not the window handle: full screen
                g.set_state(Gtk.EventSequenceState.CLAIMED)
                self.toggle_fullscreen()
        click.connect("pressed", pressed)
        self.picture.get_parent().add_controller(click)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._context_menu)
        self.overlay.add_controller(menu)

    def _context_menu(self, gesture, _n, x, y) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        Item = ui.menu.Item
        s = self.stream
        ok = s is not None and s.get_error() is None
        playing = bool(ok and s.get_playing())
        ui.menu.popup(self.overlay, [
            [Item("Pause" if playing else "Play", self.toggle_play, enabled=ok)],
            [Item("Loop", lambda _v=None: self.toggle_loop(), checked=bool(ok and s.get_loop()), enabled=ok),
             Item("Mute", lambda _v=None: self.toggle_mute(), checked=self.muted, enabled=ok)],
            [Item("Exit Full Screen" if self.is_fullscreen() else "Enter Full Screen", self.toggle_fullscreen)],
            [Item("Show in Files", self.show_in_files), Item("Open…", self.open_dialog)],
        ], at=(x, y), glass=True, passthrough=True)

    def show_in_files(self) -> None:
        from ..files import open_folder
        open_folder(Gio.File.new_for_path(self.path).get_uri())

    def open_dialog(self) -> None:
        choose(self.get_application(), parent=self)

    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        k = Gdk.keyval_to_lower(keyval)
        if cmd:
            act = {Gdk.KEY_w: self.close, Gdk.KEY_o: self.open_dialog, Gdk.KEY_f: self.toggle_fullscreen}.get(k)
        else:
            act = {Gdk.KEY_space: self.toggle_play, Gdk.KEY_k: self.toggle_play,
                   Gdk.KEY_Left: lambda: self.skip(-5), Gdk.KEY_Right: lambda: self.skip(5),
                   Gdk.KEY_j: lambda: self.skip(-10), Gdk.KEY_l: lambda: self.skip(10),
                   Gdk.KEY_Up: lambda: self.set_volume(self.volume + 0.1),
                   Gdk.KEY_Down: lambda: self.set_volume(self.volume - 0.1),
                   Gdk.KEY_m: self.toggle_mute, Gdk.KEY_f: self.toggle_fullscreen,
                   Gdk.KEY_Escape: lambda: self.is_fullscreen() and self.toggle_fullscreen()}.get(k)
        if act is None:
            return False
        act()
        if not cmd and k not in (Gdk.KEY_Escape,):
            self._show_hud()
        return True

    def _close_request(self, _w) -> bool:
        if self._hide_src:
            GLib.source_remove(self._hide_src)
            self._hide_src = 0
        self._release()
        return False


def choose(app, parent=None) -> None:
    """Files' Open dialog, movies only."""
    from ..files.chooser import ChooserWindow

    def done(uris, _i):
        open_paths(app, uris or [])
    dlg = ChooserWindow(app, mode="open", title="Open", multiple=True,
                        filters=[("Movies", [(1, "video/*")])], on_done=done)
    if parent is not None:
        dlg.set_transient_for(parent)
        dlg.set_modal(True)
    dlg.present()


def open_paths(app, paths) -> None:
    for p in paths:
        path = Gio.File.new_for_commandline_arg(p).get_path()
        if not path:
            continue
        same = next((w for w in app.get_windows() if isinstance(w, VideoWindow) and w.path == path), None)
        (same or VideoWindow(app, path)).present()
    if not paths:
        wins = [w for w in app.get_windows() if isinstance(w, VideoWindow)]
        if wins:
            wins[0].present()
        elif not app.get_windows():
            choose(app)


def videos_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Videos\nComment=Watch movies\n"
                              "Icon=multimedia-video-player\nCategories=AudioVideo;Video;Player;\n"
                              "MimeType=" + "".join(t + ";" for t in MIME_TYPES) + "\n"
                              "StartupNotify=true\n"
                              f"Exec={command} videos %F\n")
