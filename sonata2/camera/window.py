"""Camera (like macOS Photo Booth): the camera fills the window, a red
shutter button under it, and a strip of the photos taken so far.

A photo: 3, 2, 1 in big numbers over the picture, then the screen flashes
white and the shutter sounds (Photo Booth); saved in Pictures/Camera. The
camera menu (right of the shutter): which camera, mirror the picture (on,
like a mirror: photos are kept as seen), the countdown. A thumbnail opens
the photo; right-click: Open, Show in Files, Move to Trash.

Keys: Space or Return take a photo, Esc cancels the countdown.
The camera: camera/engine.py. Settings: ~/.config/sonata2/camera.json."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402
from . import engine  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.camera"
CONFIG = "camera"
DEFAULTS = {"camera": "", "mirror": True, "countdown": True}
COUNT_FROM = 3
FLASH_MS = 420
THUMB = (96, 72)
STRIP_MAX = 60

ui.register("""
window.sonata-camera { background: %(sys_black)s; }
.cam-stage { background: %(sys_black)s; }
.cam-message { color: %(on_scrim)s; font-family: %(font)s; font-size: %(text_title)s; font-weight: 600; }
.cam-hint { color: %(on_scrim_secondary)s; font-family: %(font)s; font-size: %(text_body)s; }
.cam-count { color: white; font-family: %(font)s; font-size: 120px; font-weight: 700;
  text-shadow: 0 2px 18px rgba(0,0,0,0.45); }
.cam-flash { background: white; opacity: 0; }
.cam-bar { background: %(window_bg)s; padding: 10px 14px; box-shadow: inset 0 1px %(separator)s; }
.cam-bar menubutton.cam-tool > button { min-width: 32px; min-height: 28px; padding: 0 6px; border-radius: 6px;
  border: none; background: none; box-shadow: none; color: %(label)s; opacity: 0.75; }
.cam-bar menubutton.cam-tool > button:hover, .cam-bar menubutton.cam-tool > button:checked {
  background: %(tool_hover)s; opacity: 1; }
.cam-bar menubutton.cam-tool image { -gtk-icon-size: 18px; }
button.cam-shutter { min-width: 52px; min-height: 52px; padding: 0; border-radius: 99px; border: none;
  background: %(sys_red)s; color: white;
  box-shadow: 0 0 0 3px alpha(%(sys_red)s, 0.28), inset 0 1px rgba(255,255,255,0.25);
  transition: filter %(t_press)s ease-out; }
button.cam-shutter:hover { filter: brightness(1.08); }
button.cam-shutter:active { filter: brightness(0.85); transition: none; }
button.cam-shutter:disabled { filter: saturate(0.2) opacity(0.5); }
button.cam-shutter image { -gtk-icon-size: 22px; }
.cam-strip { background: %(window_bg)s; padding: 0 10px 10px 10px; }
.cam-thumb { border-radius: 4px; box-shadow: 0 0 0 0.5px %(hairline)s; }
.cam-thumb-button { padding: 0; border: none; background: none; box-shadow: none; border-radius: 5px; }
.cam-thumb-button:hover .cam-thumb { box-shadow: 0 0 0 2px %(accent)s; }
""", key="camera")


# -- pure logic (tested) ----------------------------------------------------------------------
def photos_dir() -> str:
    from .. import userdirs                      # (~/Camera when the session had no Pictures set)
    pics = userdirs.special(GLib.UserDirectory.DIRECTORY_PICTURES, create=True) or GLib.get_home_dir()
    return os.path.join(pics, "Camera")


def photo_name(now=None) -> str:
    now = now or GLib.DateTime.new_now_local()
    return now.format("Photo %Y-%m-%d at %H.%M.%S.jpg")


def unique(folder: str, name: str) -> str:
    """name in folder, "name 2.jpg" and so on when it exists (two photos a second)."""
    base, ext = os.path.splitext(name)
    path, n = os.path.join(folder, name), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base} {n}{ext}"), n + 1
    return path


def recent_photos(folder: str, limit: int = STRIP_MAX) -> list:
    """Newest first."""
    try:
        names = [n for n in os.listdir(folder) if n.lower().endswith((".jpg", ".jpeg", ".png"))]
    except OSError:
        return []
    paths = [os.path.join(folder, n) for n in names]
    paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return paths[:limit]


# -- the window ------------------------------------------------------------------------------------
class CameraWindow(Gtk.ApplicationWindow):
    def __init__(self, app, source=None):
        super().__init__(application=app, title="Camera", default_width=860, default_height=680,
                         css_classes=["sonata-camera"])
        ui.window.standard(self)
        self.set_size_request(480, 420)
        self.cfg = config.load(CONFIG, DEFAULTS)
        self.cam = engine.Camera(on_error=self._show_error, source=source)
        self.cams = []
        self._count_src = 0
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # the picture, with the countdown, the flash and messages over it
        stage = Gtk.Overlay(css_classes=["cam-stage"], vexpand=True)
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
        self.flash = Gtk.Box(css_classes=["cam-flash"], can_target=False)
        stage.add_overlay(self.flash)
        root.append(stage)
        # the bar: (modes later) | shutter | camera menu
        bar = Gtk.CenterBox(css_classes=["cam-bar"])
        self.shutter = Gtk.Button(icon_name="camera-photo-symbolic", css_classes=["cam-shutter"],
                                  tooltip_text="Take a Photo", valign=Gtk.Align.CENTER)
        self.shutter.connect("clicked", lambda *_: self.capture())
        bar.set_center_widget(self.shutter)
        self.menu_btn = Gtk.MenuButton(icon_name="camera-web-symbolic", css_classes=["cam-tool"],
                                       tooltip_text="Camera", valign=Gtk.Align.CENTER, direction=Gtk.ArrowType.UP)
        self.menu_btn.set_create_popup_func(self._menu)
        bar.set_end_widget(self.menu_btn)
        root.append(bar)
        # the strip of photos
        self.strip_box = Gtk.Box(spacing=8)
        self.strip = Gtk.ScrolledWindow(child=self.strip_box, css_classes=["cam-strip"],
                                        vscrollbar_policy=Gtk.PolicyType.NEVER,
                                        hscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        self.strip.set_size_request(-1, THUMB[1] + 12)
        root.append(self.strip)
        self.set_child(root)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: (self._cancel_count(), self.cam.stop(), False)[2])
        self._fill_strip()
        GLib.idle_add(lambda: (self._open_camera(), False)[1])     # the window first, then the camera

    # -- camera ---------------------------------------------------------------------------------
    def _open_camera(self):
        if self.cam.source is None:
            self.cams = engine.devices()
            if not self.cams:
                self._show_error("No Camera", "Connect a camera, or check that no other app is using it.")
                return
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

    def _switch(self, name):
        self.cfg["camera"] = name
        config.save(CONFIG, self.cfg)
        self._open_camera()

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
            Item("Mirror Image", lambda on: self._set("mirror", on), checked=self.cfg.get("mirror", True)),
            Item("Countdown", lambda on: self._set("countdown", on), checked=self.cfg.get("countdown", True))]]
        pop = ui.menu.popup(button, sections, position=Gtk.PositionType.TOP)
        button.set_active(False)
        return pop

    # -- taking a photo ---------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.CONTROL_MASK:
            self.capture()
            return True
        if keyval == Gdk.KEY_Escape and self._count_src:
            self._cancel_count()
            return True
        return False

    def capture(self):
        if self._count_src or not self.cam.running:
            return
        if not self.cfg.get("countdown", True):
            self._snap()
            return
        left = {"n": COUNT_FROM}
        self.count.set_label(str(COUNT_FROM))
        self.count.set_visible(True)
        self.shutter.set_sensitive(False)

        def tick():
            left["n"] -= 1
            if left["n"] <= 0:
                self._count_src = 0
                self.count.set_visible(False)
                self.shutter.set_sensitive(True)
                self._snap()
                return False
            self.count.set_label(str(left["n"]))
            return True
        self._count_src = GLib.timeout_add(1000, tick)

    def _cancel_count(self):
        if self._count_src:
            GLib.source_remove(self._count_src)
            self._count_src = 0
        self.count.set_visible(False)
        self.shutter.set_sensitive(self.cam.running)

    def _snap(self):
        folder = photos_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            return
        path = unique(folder, photo_name())
        if not self.cam.photo(path):
            return
        self._flash()
        from .. import sounds
        sounds.play("screenshot")
        self._add_thumb(path, first=True)
        self.last_photo = path

    def _flash(self):
        t0 = GLib.get_monotonic_time()
        self.flash.set_opacity(1.0)

        def fade():
            t = (GLib.get_monotonic_time() - t0) / (FLASH_MS * 1000)
            self.flash.set_opacity(max(0.0, 1.0 - t) ** 2)
            return t < 1.0
        GLib.timeout_add(16, fade)

    # -- the strip ---------------------------------------------------------------------------------
    def _fill_strip(self):
        for path in reversed(recent_photos(photos_dir())):
            self._add_thumb(path, first=True)
        self.strip.set_visible(self.strip_box.get_first_child() is not None)

    def _add_thumb(self, path, first=False):
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, THUMB[0] * 2, THUMB[1] * 2, True)
        except GLib.Error:
            return
        pic = Gtk.Picture(paintable=Gdk.Texture.new_for_pixbuf(pb), content_fit=Gtk.ContentFit.COVER,
                          css_classes=["cam-thumb"], overflow=Gtk.Overflow.HIDDEN)
        pic.set_size_request(*THUMB)
        btn = Gtk.Button(child=pic, css_classes=["cam-thumb-button"], tooltip_text=os.path.basename(path))
        btn.path = path
        btn.connect("clicked", lambda b: self._open(b.path))
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", lambda _g, _n, x, y, b=btn: self._thumb_menu(b, x, y))
        btn.add_controller(click)
        if first:
            self.strip_box.prepend(btn)
        else:
            self.strip_box.append(btn)
        self.strip.set_visible(True)
        while len(list(_children(self.strip_box))) > STRIP_MAX:
            self.strip_box.remove(self.strip_box.get_last_child())

    def _open(self, path):
        try:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(path).get_uri(), None)
        except GLib.Error:
            pass

    def _thumb_menu(self, btn, x, y):
        Item = ui.menu.Item
        path = btn.path

        def trash():
            try:
                Gio.File.new_for_path(path).trash(None)
            except GLib.Error:
                return
            self.strip_box.remove(btn)
            self.strip.set_visible(self.strip_box.get_first_child() is not None)

        def reveal():
            from ..__main__ import self_command
            try:
                GLib.spawn_async(self_command().split() + ["files", Gio.File.new_for_path(path).get_uri()],
                                 flags=GLib.SpawnFlags.SEARCH_PATH)
            except GLib.Error:
                pass
        ui.menu.popup(btn, [[Item("Open", lambda: self._open(path)), Item("Show in Files", reveal)],
                            [Item("Move to Trash", trash)]], at=(x, y))


def _children(box):
    c = box.get_first_child()
    while c is not None:
        yield c
        c = c.get_next_sibling()


def open_windows(app, paths=None) -> None:
    """One Camera window (a camera can't be shared between two)."""
    wins = [w for w in app.get_windows() if isinstance(w, CameraWindow)]
    (wins[0] if wins else CameraWindow(app)).present()


def camera_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Camera\n"
                              "Comment=Take photos with your camera\n"
                              "Icon=camera\nCategories=AudioVideo;Video;Recorder;\n"
                              "Keywords=photo;webcam;selfie;picture;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} camera\n")
