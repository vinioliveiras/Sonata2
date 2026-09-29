"""Preview (macOS Preview, images): one window per picture.

The picture fits the window; ⌘+ / ⌘- / ⌘0 (or Ctrl+wheel, or a pinch)
zoom around it, ⌘9 shows it at actual size, dragging pans a zoomed
picture. ← / → go through the other pictures in the same folder,
⌘R / ⌘L rotate (the view only), Space or ⌘F fill the screen, ⌘W
closes. Double-click on the title bar zooms the window (the title bar is a
normal Sonata one). The title shows the file name and, dimmed, its size."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.preview"
EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".ico", ".avif", ".heic")
ZOOMS = (0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)

ui.register("""
window.sonata-preview { background: %(window_bg)s; }
.pv-bar { background: %(window_bg)s; box-shadow: inset 0 -1px %(separator)s; }
.pv-canvas { background: %(content_bg)s; }
.pv-bar .sonata-titlebar-title { font-weight: 700; font-size: %(text_body)s; color: %(label)s; }
.pv-dim { color: %(label_secondary)s; font-weight: 400; font-size: %(text_body)s; }
.pv-tool { min-width: 28px; min-height: 24px; padding: 0 6px; border-radius: 6px; border: none;
  background: none; box-shadow: none; color: %(label_secondary)s; }
.pv-tool:hover { background: %(tool_hover)s; color: %(label)s; }
""", key="preview")


def load_texture(path: str):
    """Gdk.Texture of any picture GTK or GdkPixbuf reads (SVG through librsvg)."""
    try:
        return Gdk.Texture.new_from_filename(path)
    except GLib.Error:
        pass
    try:
        return Gdk.Texture.new_for_pixbuf(GdkPixbuf.Pixbuf.new_from_file(path))
    except GLib.Error:
        return None


def siblings(path: str) -> list:
    """The pictures in the same folder, in Files' (name) order."""
    folder = os.path.dirname(path)
    try:
        names = sorted((n for n in os.listdir(folder) if n.lower().endswith(EXTS) and not n.startswith(".")),
                       key=lambda n: GLib.utf8_collate_key_for_filename(n, -1))
    except OSError:
        return [path]
    return [os.path.join(folder, n) for n in names]


class PreviewWindow(Gtk.ApplicationWindow):
    def __init__(self, app, path: str):
        if not GLib.get_application_name():
            GLib.set_application_name("Preview")           # Recent documents need it
        super().__init__(application=app, css_classes=["sonata-preview"])
        ui.window.standard(self)
        self.path = None
        self.texture = None
        self.zoom = None                  # None: fit the window
        self.rotation = 0
        tools = Gtk.Box(spacing=2)
        for icon, tip, cb in (("zoom-out-symbolic", "Zoom Out", lambda: self.step_zoom(-1)),
                              ("zoom-in-symbolic", "Zoom In", lambda: self.step_zoom(1)),
                              ("object-rotate-left-symbolic", "Rotate Left", lambda: self.rotate(-90))):
            b = Gtk.Button(icon_name=icon, tooltip_text=tip, css_classes=["pv-tool"], can_focus=False)
            b.connect("clicked", lambda _b, f=cb: f())
            tools.append(b)
        self.bar = ui.window.titlebar(self, "", end=tools, zoom=True)
        self.bar.add_css_class("pv-bar")
        self.title_size = Gtk.Label(css_classes=["pv-dim"])
        title = Gtk.Box(spacing=6)
        self.bar.bar.set_center_widget(title)
        title.append(self.bar.title_label)
        title.append(self.title_size)
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.scroll = Gtk.ScrolledWindow(child=self.picture, vexpand=True, hexpand=True, css_classes=["pv-canvas"])
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # the title bar is the glass one the compositor draws (pixdecor): no second bar
        col.append(self.scroll)
        self.set_child(col)
        self._input()
        self.open(path)

    # -- file ------------------------------------------------------------------------------
    def open(self, path: str) -> None:
        tex = load_texture(path)
        self.path, self.texture, self.rotation, self.zoom = path, tex, 0, None
        name = os.path.basename(path)
        self.set_title(name)
        self.bar.title_label.set_label(name)
        if tex is None:
            self.title_size.set_label("")
            self.picture.set_paintable(None)
            ui.dialog.alert(f"“{name}” couldn't be opened.", "It isn't a picture Preview can read.",
                            [("ok", "OK", "default")], parent=self)
            return
        self.title_size.set_label(f"{tex.get_width()} × {tex.get_height()}")
        self.picture.set_paintable(tex)
        Gtk.RecentManager.get_default().add_item(Gio.File.new_for_path(path).get_uri())
        if not self.get_realized():               # the window takes the picture's shape
            w, h = tex.get_width(), tex.get_height()
            mon = Gdk.Display.get_default().get_monitors().get_item(0)
            g = mon.get_geometry() if mon else None
            max_w, max_h = (g.width * 0.7, g.height * 0.7) if g else (1100, 760)
            s = min(1.0, max_w / max(1, w), (max_h - ui.window.TITLEBAR_H) / max(1, h))
            self.set_default_size(max(420, int(w * s)), max(300, int(h * s) + ui.window.TITLEBAR_H))
        self._layout()

    def go(self, step: int) -> None:
        if not self.path:
            return
        pics = siblings(self.path)
        if self.path in pics and len(pics) > 1:
            self.open(pics[(pics.index(self.path) + step) % len(pics)])

    # -- view ------------------------------------------------------------------------------
    def rotate(self, degrees: int) -> None:
        if self.texture is None:
            return
        self.rotation = (self.rotation + degrees) % 360
        pix = GdkPixbuf.Pixbuf.new_from_file(self.path) if self.path else None
        if pix is None:
            return
        pix = pix.apply_embedded_orientation()
        angle = {90: GdkPixbuf.PixbufRotation.CLOCKWISE, 180: GdkPixbuf.PixbufRotation.UPSIDEDOWN,
                 270: GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE}.get(self.rotation)
        if angle is not None:
            pix = pix.rotate_simple(angle)
        self.texture = Gdk.Texture.new_for_pixbuf(pix)
        self.picture.set_paintable(self.texture)
        self._layout()

    def fit_scale(self) -> float:
        if self.texture is None:
            return 1.0
        w = max(1, self.scroll.get_width() or self.get_width())
        h = max(1, self.scroll.get_height() or (self.get_height() - ui.window.TITLEBAR_H))
        return min(1.0, w / self.texture.get_width(), h / self.texture.get_height())

    def set_zoom(self, z) -> None:
        """Zoom around the middle of what shows (None: fit the window)."""
        ha, va = self.scroll.get_hadjustment(), self.scroll.get_vadjustment()

        def centre(a):
            return (a.get_value() + a.get_page_size() / 2) / a.get_upper() if a.get_upper() > 0 else 0.5
        fx, fy = (centre(ha), centre(va)) if self.zoom is not None else (0.5, 0.5)
        self.zoom = None if z is None else max(ZOOMS[0], min(ZOOMS[-1], z))
        self._layout()

        def restore():
            for a, f in ((ha, fx), (va, fy)):
                a.set_value(max(0, f * a.get_upper() - a.get_page_size() / 2))
            return False
        GLib.timeout_add(30, restore)          # once the picture has its new size

    def step_zoom(self, step: int) -> None:
        cur = self.zoom or self.fit_scale()
        if step > 0:
            nxt = next((z for z in ZOOMS if z > cur + 1e-3), ZOOMS[-1])
        else:
            nxt = next((z for z in reversed(ZOOMS) if z < cur - 1e-3), ZOOMS[0])
        self.set_zoom(nxt)

    def _layout(self) -> None:
        if self.texture is None:
            return
        if self.zoom is None:
            self.picture.set_size_request(-1, -1)
            self.picture.set_can_shrink(True)
            self.picture.set_halign(Gtk.Align.FILL)
            self.picture.set_valign(Gtk.Align.FILL)
        else:
            self.picture.set_can_shrink(False)
            self.picture.set_halign(Gtk.Align.CENTER)
            self.picture.set_valign(Gtk.Align.CENTER)
            self.picture.set_size_request(int(self.texture.get_width() * self.zoom),
                                          int(self.texture.get_height() * self.zoom))
            self.picture.set_content_fit(Gtk.ContentFit.FILL)
            return
        self.picture.set_content_fit(Gtk.ContentFit.SCALE_DOWN)

    # -- input -----------------------------------------------------------------------------
    def _input(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

        def scrolled(c, _dx, dy):
            if c.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
                self.step_zoom(-1 if dy > 0 else 1)
                return True
            return False
        wheel.connect("scroll", scrolled)
        self.scroll.add_controller(wheel)
        pinch = Gtk.GestureZoom()
        state = {"start": 1.0}
        pinch.connect("begin", lambda *_: state.update(start=self.zoom or self.fit_scale()))
        pinch.connect("scale-changed", lambda _g, s: self.set_zoom(state["start"] * s))
        self.scroll.add_controller(pinch)
        drag = Gtk.GestureDrag()
        pan = {"h": 0.0, "v": 0.0}
        drag.connect("drag-begin", lambda *_: pan.update(h=self.scroll.get_hadjustment().get_value(),
                                                          v=self.scroll.get_vadjustment().get_value()))

        def moved(_g, dx, dy):
            if self.zoom is not None:
                self.scroll.get_hadjustment().set_value(pan["h"] - dx)
                self.scroll.get_vadjustment().set_value(pan["v"] - dy)
        drag.connect("drag-update", moved)
        self.scroll.add_controller(drag)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, n, _x, _y: n == 2 and self.set_zoom(
            None if self.zoom is not None else 1.0))
        self.scroll.add_controller(click)

    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        k = Gdk.keyval_to_lower(keyval)
        if cmd:
            act = {Gdk.KEY_plus: lambda: self.step_zoom(1), Gdk.KEY_equal: lambda: self.step_zoom(1),
                   Gdk.KEY_minus: lambda: self.step_zoom(-1), Gdk.KEY_0: lambda: self.set_zoom(None),
                   Gdk.KEY_9: lambda: self.set_zoom(1.0), Gdk.KEY_r: lambda: self.rotate(90),
                   Gdk.KEY_l: lambda: self.rotate(-90), Gdk.KEY_w: self.close,
                   Gdk.KEY_f: self._toggle_fullscreen}.get(k)
        else:
            act = {Gdk.KEY_Left: lambda: self.go(-1), Gdk.KEY_Right: lambda: self.go(1),
                   Gdk.KEY_Up: lambda: self.go(-1), Gdk.KEY_Down: lambda: self.go(1),
                   Gdk.KEY_space: self._toggle_fullscreen,
                   Gdk.KEY_Escape: lambda: self.is_fullscreen() and self.unfullscreen()}.get(keyval)
        if act is None:
            return False
        act()
        return True

    def _toggle_fullscreen(self) -> None:
        self.unfullscreen() if self.is_fullscreen() else self.fullscreen()


def open_paths(app, paths) -> None:
    for p in paths:
        f = Gio.File.new_for_commandline_arg(p)
        path = f.get_path()
        if not path:
            continue
        same = next((w for w in app.get_windows() if isinstance(w, PreviewWindow) and w.path == path), None)
        (same or PreviewWindow(app, path)).present()
    if not paths and not app.get_windows():
        from ..files.chooser import ChooserWindow

        def done(uris, _i):
            open_paths(app, uris or [])
        ChooserWindow(app, mode="open", title="Open", multiple=True,
                      filters=[("Images", [(1, "image/*")])], on_done=done).present()


def preview_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Preview\nComment=View pictures\n"
                              "Icon=image-viewer\nCategories=Graphics;Viewer;\n"
                              "MimeType=image/png;image/jpeg;image/gif;image/webp;image/bmp;image/tiff;"
                              "image/svg+xml;image/x-icon;image/avif;image/heic;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} preview %F\n")
