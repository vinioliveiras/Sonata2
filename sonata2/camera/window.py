"""Camera (layout like the iPhone's Camera, iOS 18): black all round, the
picture in the middle, the mode under it (VIDEO / PHOTO, the chosen one in
yellow) and one row of controls: the last photo or video on the left, the
shutter in the middle (a white ring; red inside for video, a red rounded
square while recording), the camera switch on the right. Along the top:
flash (the screen lights up white for the photo, iOS' front "flash"), the
timer (3 s), the options (camera, mirror); while recording, the time in a
red pill.

Photos go to Pictures/Camera, videos (with the microphone) to
Videos/Camera. The thumbnail opens the last one; right-click: Open, Show
in Files, Move to Trash. The window never changes size by itself: every
part has its own height.

Keys: Space or Return take the photo / start or stop the video, Esc
cancels the timer, ← / → switch the mode.
The camera: camera/engine.py. Settings: ~/.config/sonata2/camera.json."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402
from ..backend.system import run_async  # noqa: E402
from . import engine  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.camera"
CONFIG = "camera"
DEFAULTS = {"camera": "", "mirror": True, "countdown": False, "flash": False, "mode": "photo"}
MODES = (("video", "VIDEO"), ("photo", "PHOTO"))
COUNT_FROM = 3
FLASH_LEAD_MS = 180          # the screen is white this long before the photo (it lights the face)
THUMB = 44

ui.register("""
window.sonata-camera, window.sonata-camera .cam-stage, .cam-top, .cam-bottom { background: %(sys_pure_black)s; }
.cam-top { min-height: 44px; padding: 0 12px; }
.cam-bottom { padding: 6px 0 16px 0; }
.cam-message { color: %(on_scrim)s; font-family: %(font)s; font-size: %(text_title)s; font-weight: 600; }
.cam-hint { color: %(on_scrim_secondary)s; font-family: %(font)s; font-size: %(text_body)s; }
.cam-count { color: %(on_scrim)s; font-family: %(font)s; font-size: 120px; font-weight: 300;
  text-shadow: 0 2px 18px rgba(0,0,0,0.45); }
.cam-flash { background: %(on_scrim)s; }
.cam-blink { background: %(sys_pure_black)s; }
/* round, see-through buttons on black (iOS) */
button.cam-icon, menubutton.cam-icon > button { min-width: 36px; min-height: 36px; padding: 0; border-radius: 99px;
  border: none; box-shadow: none; background: none; color: %(on_scrim)s; }
button.cam-icon:hover, menubutton.cam-icon > button:hover { background: alpha(%(on_scrim)s, 0.12); }
button.cam-icon.on { color: %(sys_yellow)s; }
button.cam-icon image, menubutton.cam-icon image { -gtk-icon-size: 18px; }
button.cam-switch { min-width: 44px; min-height: 44px; padding: 0; border-radius: 99px; border: none; box-shadow: none;
  background: alpha(%(on_scrim)s, 0.14); color: %(on_scrim)s; }
button.cam-switch:hover { background: alpha(%(on_scrim)s, 0.22); }
button.cam-switch image { -gtk-icon-size: 20px; }
/* the mode picker */
button.cam-mode { padding: 2px 10px; border: none; box-shadow: none; background: none; border-radius: 99px;
  color: %(on_scrim_secondary)s; font-family: %(font)s; font-size: 12px; font-weight: 600; letter-spacing: 1px; }
button.cam-mode:hover { color: %(on_scrim)s; }
button.cam-mode.selected { color: %(sys_yellow)s; }
/* the shutter: a white ring around a white disc (photo) or a red one (video); recording, a red square */
button.cam-shutter { min-width: 70px; min-height: 70px; padding: 0; border-radius: 99px; background: none;
  border: 4px solid %(on_scrim)s; box-shadow: none; }
button.cam-shutter .cam-core { min-width: 54px; min-height: 54px; border-radius: 99px; background: %(on_scrim)s;
  transition: all %(t_standard)s %(ease_out)s; }
button.cam-shutter:active .cam-core { filter: brightness(0.8); }
button.cam-shutter.video .cam-core { background: %(sys_red)s; }
button.cam-shutter.recording .cam-core { min-width: 26px; min-height: 26px; border-radius: 6px; margin: 14px; }
button.cam-shutter:disabled { opacity: 0.4; }
/* the last photo / video */
button.cam-last { padding: 0; border: none; background: none; box-shadow: none; border-radius: 8px;
  min-width: 44px; min-height: 44px; }
.cam-last-pic { border-radius: 8px; box-shadow: 0 0 0 1px alpha(%(on_scrim)s, 0.25); }
.cam-rec-time { background: %(sys_red)s; color: %(on_scrim)s; border-radius: 6px; padding: 2px 8px;
  font-family: %(font)s; font-size: 15px; font-weight: 600; font-feature-settings: "tnum"; }
""", key="camera")


# -- pure logic (tested) ----------------------------------------------------------------------
def _folder(kind) -> str:
    from .. import userdirs                      # (~/Camera when the session had no Pictures set)
    base = userdirs.special(kind, create=True) or GLib.get_home_dir()
    return os.path.join(base, "Camera")


def photos_dir() -> str:
    return _folder(GLib.UserDirectory.DIRECTORY_PICTURES)


def videos_dir() -> str:
    return _folder(GLib.UserDirectory.DIRECTORY_VIDEOS)


def photo_name(now=None, prefix="Photo", ext="jpg") -> str:
    now = now or GLib.DateTime.new_now_local()
    return now.format(f"{prefix} %Y-%m-%d at %H.%M.%S") + (f".{ext}" if ext else "")


def unique(folder: str, name: str) -> str:
    """name in folder, "name 2.jpg" and so on when it exists (two photos a second)."""
    base, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base} {n}{ext}"), n + 1
    return path


MEDIA = (".jpg", ".jpeg", ".png", ".mp4", ".webm")


def recent_photos(folder: str, limit: int = 60) -> list:
    """Photos and videos, newest first."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith(MEDIA)]
    except OSError:
        return []
    dated = []
    for n in names:
        p = os.path.join(folder, n)
        try:                        # a dangling link, or deleted meanwhile: skipped, never a crash
            dated.append((os.stat(p).st_mtime, p))
        except OSError:
            continue
    dated.sort(reverse=True)
    return [p for _t, p in dated[:limit]]


def _mtime(path: str) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def last_capture():
    """The newest photo or video of both folders."""
    found = recent_photos(photos_dir(), 1) + recent_photos(videos_dir(), 1)
    return max(found, key=_mtime) if found else None


def square_texture(pixbuf, size: int):
    """The middle square of a picture, size x size (the thumbnail, iOS)."""
    w, h = pixbuf.get_width(), pixbuf.get_height()
    side = min(w, h)
    sq = pixbuf.new_subpixbuf((w - side) // 2, (h - side) // 2, side, side)
    return Gdk.Texture.new_for_pixbuf(sq.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR))


def fmt_time(seconds: int) -> str:
    h, rest = divmod(max(0, int(seconds)), 3600)
    return f"{h:02d}:{rest // 60:02d}:{rest % 60:02d}"


# -- the window ------------------------------------------------------------------------------------
class CameraWindow(Gtk.ApplicationWindow):
    def __init__(self, app, source=None):
        super().__init__(application=app, title="Camera", default_width=820, default_height=700,
                         css_classes=["sonata-camera"])
        ui.window.standard(self)
        self.set_size_request(460, 480)
        self.cfg = config.load(CONFIG, DEFAULTS)
        self.cam = engine.Camera(on_error=self._show_error, source=source)
        self.cams = []
        self.last_photo = None
        self._count_src = self._rec_src = 0
        self._rec_t0 = 0
        self._closed = False
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self._top_bar())
        root.append(self._stage())
        root.append(self._bottom())
        self.set_child(root)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: (self._cancel_count(), self._stop_video(), self.cam.stop(),
                                                  setattr(self, "_closed", True), False)[4])
        self._set_mode(self.cfg.get("mode", "photo"), save=False)
        self._show_last(last_capture())
        GLib.idle_add(lambda: (self._open_camera(), False)[1])     # the window first, then the camera

    # -- building --------------------------------------------------------------------------------
    def _top_bar(self):
        bar = Gtk.CenterBox(css_classes=["cam-top"])
        left = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        self.flash_btn = Gtk.Button(icon_name="sonata-flash-symbolic", css_classes=["cam-icon"], tooltip_text="Flash")
        self.flash_btn.connect("clicked", lambda *_: self._toggle("flash"))
        left.append(self.flash_btn)
        bar.set_start_widget(left)
        self.rec_time = Gtk.Label(label=fmt_time(0), css_classes=["cam-rec-time"], valign=Gtk.Align.CENTER,
                                  visible=False)
        bar.set_center_widget(self.rec_time)
        right = Gtk.Box(spacing=4, valign=Gtk.Align.CENTER)
        self.timer_btn = Gtk.Button(icon_name="timer-symbolic", css_classes=["cam-icon"], tooltip_text="Timer (3 s)")
        self.timer_btn.connect("clicked", lambda *_: self._toggle("countdown"))
        right.append(self.timer_btn)
        self.menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic", css_classes=["cam-icon"], tooltip_text="Options")
        self.menu_btn.set_create_popup_func(self._menu)
        right.append(self.menu_btn)
        bar.set_end_widget(right)
        self._sync_toggles()
        return bar

    def _stage(self):
        stage = Gtk.Overlay(css_classes=["cam-stage"], vexpand=True, hexpand=True)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        stage.set_child(self.picture)
        self.count = Gtk.Label(css_classes=["cam-count"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                               visible=False, can_target=False)
        stage.add_overlay(self.count)
        msg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER, visible=False)
        self.msg_title = Gtk.Label(css_classes=["cam-message"])
        self.msg_hint = Gtk.Label(css_classes=["cam-hint"], wrap=True, justify=Gtk.Justification.CENTER,
                                  max_width_chars=48)
        msg.append(self.msg_title)
        msg.append(self.msg_hint)
        self.msg = msg
        stage.add_overlay(msg)
        self.flash = Gtk.Box(css_classes=["cam-flash"], can_target=False, opacity=0)
        stage.add_overlay(self.flash)
        self.blink = Gtk.Box(css_classes=["cam-blink"], can_target=False, opacity=0)
        stage.add_overlay(self.blink)
        return stage

    def _bottom(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["cam-bottom"])
        modes = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        self.mode_btns = {}
        for key, label in MODES:
            b = Gtk.Button(label=label, css_classes=["cam-mode"])
            b.connect("clicked", lambda _b, k=key: self._set_mode(k))
            modes.append(b)
            self.mode_btns[key] = b
        col.append(modes)
        row = Gtk.CenterBox(margin_start=28, margin_end=28)
        self.last_btn = Gtk.Button(css_classes=["cam-last"], valign=Gtk.Align.CENTER, tooltip_text="Open")
        self.last_pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, css_classes=["cam-last-pic"],
                                    overflow=Gtk.Overflow.HIDDEN, can_shrink=True)
        from ..ui.fixed import FixedWidth
        self.last_btn.set_child(FixedWidth(self.last_pic, THUMB))  # a square whatever the photo's shape
        self.last_btn.connect("clicked", lambda *_: self.last_photo and self._open(self.last_photo))
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda _g, _n, x, y: self.last_photo and self._last_menu(x, y))
        self.last_btn.add_controller(click)
        row.set_start_widget(self.last_btn)
        self.shutter = Gtk.Button(css_classes=["cam-shutter"], tooltip_text="Take a Photo", halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.CENTER, sensitive=False)
        self.shutter.set_child(Gtk.Box(css_classes=["cam-core"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER))
        self.shutter.connect("clicked", lambda *_: self.capture())
        row.set_center_widget(self.shutter)
        self.switch_btn = Gtk.Button(icon_name="camera-switch-symbolic", css_classes=["cam-switch"],
                                     valign=Gtk.Align.CENTER, tooltip_text="Switch Camera")
        self.switch_btn.connect("clicked", lambda *_: self._next_camera())
        row.set_end_widget(self.switch_btn)
        col.append(row)
        return col

    # -- camera ---------------------------------------------------------------------------------
    def _open_camera(self):
        if self.cam.source is None:
            # The device probe can take a while (USB, PipeWire): off the main loop.
            run_async(engine.devices, self._got_cameras)
            return
        self._start_camera()

    def _got_cameras(self, cams):
        if self._closed:
            return
        self.cams = cams or []
        self._start_camera()

    def _start_camera(self):
        if self.cam.source is None:
            if not self.cams:
                self._show_error("No Camera", "Connect a camera, or check that no other app is using it.")
                self.switch_btn.set_sensitive(False)
                return
        self.switch_btn.set_sensitive(len(self.cams) > 1)
        cam = next((c for c in self.cams if c.name == self.cfg.get("camera")), self.cams[0] if self.cams else None)
        self.msg.set_visible(False)
        if self.cam.start(cam, mirror=self.cfg.get("mirror", True)):
            self.picture.set_paintable(self.cam.paintable)
            self.shutter.set_sensitive(True)

    def _show_error(self, text, hint=None):
        """(text) from the camera: "Camera Unavailable" with it under; or a title and a hint."""
        title, hint = ("Camera Unavailable", text) if hint is None else (text, hint)
        self.msg_title.set_label(title)
        self.msg_hint.set_label(hint)
        self.msg.set_visible(True)
        self.shutter.set_sensitive(False)
        self._stop_video()

    def _switch(self, name):
        if self.cam.recording:
            return
        self.cfg["camera"] = name
        config.save(CONFIG, self.cfg)
        self._open_camera()

    def _next_camera(self):
        if len(self.cams) > 1:
            names = [c.name for c in self.cams]
            cur = self.cfg.get("camera") if self.cfg.get("camera") in names else names[0]
            self._switch(names[(names.index(cur) + 1) % len(names)])

    def _toggle(self, key):
        self._set(key, not self.cfg.get(key, DEFAULTS[key]))
        self._sync_toggles()

    def _sync_toggles(self):
        for btn, key in ((self.flash_btn, "flash"), (self.timer_btn, "countdown")):
            (btn.add_css_class if self.cfg.get(key) else btn.remove_css_class)("on")

    def _set(self, key, value):
        self.cfg[key] = value
        config.save(CONFIG, self.cfg)
        if key == "mirror":
            self.cam.set_mirror(value)

    def _menu(self, button):
        Item = ui.menu.Item
        current = self.cfg.get("camera") or (self.cams[0].name if self.cams else "")
        cams = [Item(c.name, lambda _on, n=c.name: self._switch(n), checked=c.name == current) for c in self.cams]
        sections = ([cams] if cams else []) + [[
            Item("Mirror Image", lambda on: self._set("mirror", on), checked=self.cfg.get("mirror", True))]]
        pop = ui.menu.popup(button, sections, position=Gtk.PositionType.BOTTOM)
        button.set_active(False)
        return pop

    # -- modes ------------------------------------------------------------------------------------
    def _set_mode(self, mode, save=True):
        if self.cam.recording or self._count_src or mode not in dict(MODES):
            return
        self.mode = mode
        for key, b in self.mode_btns.items():
            (b.add_css_class if key == mode else b.remove_css_class)("selected")
        (self.shutter.add_css_class if mode == "video" else self.shutter.remove_css_class)("video")
        self.shutter.set_tooltip_text("Record" if mode == "video" else "Take a Photo")
        self.flash_btn.set_visible(mode == "photo")
        if save:
            self._set("mode", mode)

    def _key(self, _c, keyval, _code, state):
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.capture()
            return True
        if keyval == Gdk.KEY_Escape and self._count_src:
            self._cancel_count()
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_Right):
            keys = [k for k, _l in MODES]
            i = keys.index(self.mode) + (1 if keyval == Gdk.KEY_Right else -1)
            self._set_mode(keys[max(0, min(len(keys) - 1, i))])
            return True
        return False

    # -- photos and videos --------------------------------------------------------------------------
    def capture(self):
        """The shutter: a photo (after the timer), or start / stop the video."""
        if self.mode == "video" and self.cam.recording:
            self._stop_video()
            return
        if self._count_src or not self.cam.running:
            return
        go = self._start_video if self.mode == "video" else self._photo
        if not self.cfg.get("countdown"):
            go()
            return
        left = {"n": COUNT_FROM}
        self.count.set_label(str(COUNT_FROM))
        self.count.set_visible(True)

        def tick():
            left["n"] -= 1
            if left["n"] <= 0:
                self._count_src = 0
                self.count.set_visible(False)
                go()
                return False
            self.count.set_label(str(left["n"]))
            return True
        self._count_src = GLib.timeout_add(1000, tick)

    def _cancel_count(self):
        if self._count_src:
            GLib.source_remove(self._count_src)
            self._count_src = 0
        self.count.set_visible(False)

    def _photo(self):
        if self.cfg.get("flash"):                # the screen lights up first, the photo in that light
            self.flash.set_opacity(1.0)
            GLib.timeout_add(FLASH_LEAD_MS, lambda: (self._snap(), self._fade(self.flash, 300), False)[2])
        else:
            self._snap()
            self.blink.set_opacity(0.9)          # iOS: the picture blinks
            self._fade(self.blink, 220)

    def _snap(self):
        folder = photos_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return
        path = unique(folder, photo_name())
        if not self.cam.photo(path):
            return
        from .. import sounds
        sounds.play("screenshot")
        self._show_last(path)

    def _fade(self, widget, ms):
        t0 = GLib.get_monotonic_time()

        def step():
            t = (GLib.get_monotonic_time() - t0) / (ms * 1000)
            widget.set_opacity(max(0.0, 1.0 - t) ** 2 * (1.0 if widget is self.flash else 0.9))
            return t < 1.0
        GLib.timeout_add(16, step)

    def _start_video(self):
        folder = videos_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return
        base = unique(folder, photo_name(prefix="Video", ext=""))
        path = self.cam.start_recording(base)
        if path is None:
            return
        self.shutter.add_css_class("recording")
        self.shutter.set_tooltip_text("Stop")
        for b in list(self.mode_btns.values()) + [self.switch_btn, self.last_btn]:
            b.set_sensitive(False)
        self.rec_time.set_label(fmt_time(0))
        self.rec_time.set_visible(True)
        self._rec_t0 = GLib.get_monotonic_time()
        self._rec_src = GLib.timeout_add(500, self._rec_tick)

    def _rec_tick(self):
        self.rec_time.set_label(fmt_time((GLib.get_monotonic_time() - self._rec_t0) // 1_000_000))
        return True

    def _stop_video(self):
        if self._rec_src:
            GLib.source_remove(self._rec_src)
            self._rec_src = 0
        self.rec_time.set_visible(False)
        self.shutter.remove_css_class("recording")
        self.shutter.set_tooltip_text("Record" if self.mode == "video" else "Take a Photo")
        for b in self.mode_btns.values():
            b.set_sensitive(True)
        self.switch_btn.set_sensitive(len(self.cams) > 1)
        self.last_btn.set_sensitive(True)
        if self.cam.recording:
            thumb = self._frame_texture()
            self.cam.stop_recording(done=lambda path: self._show_last(path, thumb))

    # -- the last photo / video ----------------------------------------------------------------------
    def _frame_texture(self):
        """The frame on screen, small (a video's thumbnail)."""
        jpeg = self.cam.frame_jpeg()            # in memory: no shared temp file
        if not jpeg:
            return None
        try:
            loader = GdkPixbuf.PixbufLoader()
            loader.write(jpeg)
            loader.close()
            return square_texture(loader.get_pixbuf(), THUMB * 2)
        except GLib.Error:
            return None

    def _show_last(self, path, texture=None):
        self.last_photo = path
        if path and texture is None and not path.lower().endswith((".mp4", ".webm")):
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB * 4, THUMB * 4, True)
                texture = square_texture(pb, THUMB * 2)
            except GLib.Error:
                texture = None
        self.last_pic.set_paintable(texture)
        self.last_btn.set_opacity(1.0 if path else 0.0)
        self.last_btn.set_can_target(bool(path))

    def _open(self, path):
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error:
            pass

    def _last_menu(self, x, y):
        Item = ui.menu.Item
        path = self.last_photo

        def trash():
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error:
                return
            self._show_last(last_capture())

        def reveal():
            from ..__main__ import self_argv
            try:
                GLib.spawn_async(self_argv() + ["files", Gio.File.new_for_path(path).get_uri()],
                                 flags=GLib.SpawnFlags.SEARCH_PATH)
            except GLib.Error:
                pass
        ui.menu.popup(self.last_btn, [[Item("Open", lambda: self._open(path)), Item("Show in Files", reveal)],
                                      [Item("Move to Trash", trash)]], at=(x, y))


def open_windows(app, paths=None) -> None:
    """One Camera window (a camera can't be shared between two)."""
    wins = [w for w in app.get_windows() if isinstance(w, CameraWindow)]
    (wins[0] if wins else CameraWindow(app)).present()


def camera_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Camera\n"
                              "Comment=Take photos and videos with your camera\n"
                              "Icon=camera\nCategories=AudioVideo;Video;Recorder;\n"
                              "Keywords=photo;video;webcam;selfie;picture;record;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} camera\n")
