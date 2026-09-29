"""Preview (macOS Preview, images): one window per picture.

The picture fits the window; ⌘+ / ⌘- / ⌘0 (or Ctrl+wheel, or a pinch)
zoom around it, ⌘9 shows it at actual size, dragging pans a zoomed
picture. ← / → go through the other pictures in the same folder,
⌘R / ⌘L rotate (the view only), Space or ⌘F fill the screen, ⌘W
closes. Double-click on the title bar zooms the window (the title bar is a
glass one the compositor draws; the tools sit on a toolbar of the same glass)."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.preview"
from ..imageload import RAW_EXTS, RAW_TYPES  # noqa: E402

EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".ico", ".avif", ".heic",
        ".heif", ".jxl", ".tga", ".qoi") + RAW_EXTS
ZOOMS = (0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)

ui.register("""
.pv-canvas { background: %(content_bg)s; }
.pv-adjust { padding: 12px 14px 10px 14px; }
.pv-adjust label.pv-adj-name { font-size: %(text_small)s; color: %(label_secondary)s; }
.sonata-toolbar button.tool:checked { background: alpha(%(label)s, 0.14); }
""", key="preview")


def load_texture(path: str):
    """Gdk.Texture of any picture: what GTK/GdkPixbuf/Pillow read, camera
    RAW files through their built-in preview (imageload.py)."""
    from .. import imageload
    return imageload.texture(path)


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
        # zoom and rotate on a toolbar that continues the glass title bar
        self.edits = None                 # edit.Edits once something is edited
        self.crop = None                  # crop mode: the selection (x0, y0, x1, y1) in canvas pixels
        self.toolbar = ui.window.glass_toolbar(self, end=(
            ("zoom-out-symbolic", "Zoom Out", lambda: self.step_zoom(-1)),
            ("zoom-in-symbolic", "Zoom In", lambda: self.step_zoom(1)),
            ("object-rotate-left-symbolic", "Rotate Left", lambda: self.rotate(-90)),
            ("sonata-crop-symbolic", "Crop", self.crop_button),
            ("sonata-adjust-symbolic", "Adjust Color", self.adjust_panel)))
        tools = self.toolbar.get_child().get_end_widget()
        self.crop_btn = tools.get_last_child().get_prev_sibling()
        self.adjust_btn = tools.get_last_child()
        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.scroll = Gtk.ScrolledWindow(child=self.picture, vexpand=True, hexpand=True, css_classes=["pv-canvas"])
        # the crop selection is drawn over the picture
        self.overlay = Gtk.Overlay(child=self.scroll, vexpand=True)
        self.crop_area = Gtk.DrawingArea(visible=False, can_target=True)
        self.crop_area.set_draw_func(self._draw_crop)
        self.overlay.add_overlay(self.crop_area)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self.toolbar)
        col.append(self.overlay)
        self.set_child(col)
        self._input()
        self._crop_input()
        self.connect("close-request", self._close_request)
        self.open(path)

    # -- file ------------------------------------------------------------------------------
    def open(self, path: str) -> None:
        if self.edits is not None and self.edits.edited:       # ← / → with unsaved edits: ask first
            self._ask_save(lambda: (setattr(self, "edits", None), self.open(path)))
            return
        tex = load_texture(path)
        self.edits, self.crop = None, None
        self.crop_area.set_visible(False)
        self.path, self.texture, self.rotation, self.zoom = path, tex, 0, None
        name = os.path.basename(path)
        self.set_title(name)
        if tex is None:
            self.picture.set_paintable(None)
            self.set_default_size(520, 360)

            # once the window is on screen: an alert on a window not shown yet
            # never appeared, and left Preview running with nothing visible
            def tell():
                ui.dialog.alert(f"“{name}” couldn't be opened.", "It isn't a picture Preview can read.",
                                [("ok", "OK", "default")], lambda _r: self.close(), parent=self)
                return False
            GLib.timeout_add(150, tell)
            return
        self.size_text = f"{tex.get_width()} × {tex.get_height()}"
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

    # -- editing ---------------------------------------------------------------------------
    def _editor(self):
        """The picture's edits (loaded the first time something is edited)."""
        if self.edits is None and self.path:
            from .edit import Edits
            self.edits = Edits.load(self.path)
            if self.edits is None:
                ui.dialog.alert("This picture can't be edited.", "Preview can only view it.",
                                [("ok", "OK", "default")], parent=self)
        return self.edits

    def _show_edits(self) -> None:
        ed = self.edits
        if ed is None:
            return
        self.texture = ed.texture()
        self.picture.set_paintable(self.texture)
        name = os.path.basename(self.path or "")
        self.set_title(name + (" — Edited" if ed.edited else ""))
        self._layout()

    def edit(self, op) -> None:
        ed = self._editor()
        if ed is not None:
            ed.push(op)
            self._show_edits()

    def rotate(self, degrees: int) -> None:
        self.edit(("rotate", degrees % 360))

    def undo(self) -> None:
        if self.edits is not None and self.edits.undo():
            self._show_edits()

    def revert(self) -> None:
        if self.edits is not None:
            self.edits.revert()
            self._show_edits()

    # crop: drag a selection over the picture, then Crop (or Return)
    def crop_button(self) -> None:
        if self.crop_area.get_visible():
            if self.crop and self._crop_fraction():
                self._apply_crop()
            else:
                self._end_crop()
            return
        if self._editor() is None:
            return
        self.set_zoom(None)                         # crop on the whole picture
        self.crop = None
        self.crop_area.set_visible(True)
        self.crop_area.queue_draw()

    def _picture_rect(self):
        """Where the picture is drawn on the canvas (x, y, w, h)."""
        if self.texture is None:
            return None
        cw, ch = self.overlay.get_width(), self.overlay.get_height()
        tw, th = self.texture.get_width(), self.texture.get_height()
        s = min(1.0, cw / max(1, tw), ch / max(1, th))
        w, h = tw * s, th * s
        return ((cw - w) / 2, (ch - h) / 2, w, h)

    def _crop_fraction(self):
        r = self._picture_rect()
        if not r or not self.crop:
            return None
        x, y, w, h = r
        x0, y0, x1, y1 = self.crop
        fx0, fx1 = sorted(((x0 - x) / w, (x1 - x) / w))
        fy0, fy1 = sorted(((y0 - y) / h, (y1 - y) / h))
        fx0, fy0, fx1, fy1 = max(0, fx0), max(0, fy0), min(1, fx1), min(1, fy1)
        if fx1 - fx0 < 0.01 or fy1 - fy0 < 0.01:
            return None
        return (fx0, fy0, fx1, fy1)

    def _apply_crop(self) -> None:
        frac = self._crop_fraction()
        self._end_crop()
        if frac:
            self.edit(("crop", frac))

    def _end_crop(self) -> None:
        self.crop = None
        self.crop_area.set_visible(False)

    def _crop_input(self) -> None:
        drag = Gtk.GestureDrag()
        state = {}

        def begin(_g, x, y):
            c = self.crop
            inside = c and min(c[0], c[2]) < x < max(c[0], c[2]) and min(c[1], c[3]) < y < max(c[1], c[3])
            state.update(x=x, y=y, move=c if inside else None)
            if not inside:
                self.crop = (x, y, x, y)

        def update(_g, dx, dy):
            if state.get("move"):
                x0, y0, x1, y1 = state["move"]
                self.crop = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
            else:
                self.crop = (state["x"], state["y"], state["x"] + dx, state["y"] + dy)
            self.crop_area.queue_draw()
        drag.connect("drag-begin", begin)
        drag.connect("drag-update", update)
        self.crop_area.add_controller(drag)
        self.crop_area.set_cursor(Gdk.Cursor.new_from_name("crosshair"))

    def _draw_crop(self, _area, cr, w, h) -> None:
        r = self._picture_rect()
        cr.set_source_rgba(0, 0, 0, 0.45)                   # the picture outside the selection dims
        if r:
            cr.rectangle(*r)
        if self.crop:
            x0, y0, x1, y1 = self.crop
            cr.rectangle(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
            cr.set_fill_rule(1)                             # EVEN_ODD: a hole where the selection is
        cr.fill()
        if self.crop:
            x0, y0, x1, y1 = self.crop
            cr.set_source_rgba(1, 1, 1, 0.95)
            cr.set_line_width(1.5)
            cr.rectangle(min(x0, x1) + 0.5, min(y0, y1) + 0.5, abs(x1 - x0), abs(y1 - y0))
            cr.stroke()
            cr.set_source_rgba(1, 1, 1, 0.35)             # thirds, like Photos/Preview
            cr.set_line_width(1)
            for i in (1, 2):
                xx = min(x0, x1) + abs(x1 - x0) * i / 3
                yy = min(y0, y1) + abs(y1 - y0) * i / 3
                cr.move_to(xx, min(y0, y1))
                cr.line_to(xx, max(y0, y1))
                cr.move_to(min(x0, x1), yy)
                cr.line_to(max(x0, x1), yy)
            cr.stroke()

    # adjust color: sliders in a panel under the toolbar button
    def adjust_panel(self) -> None:
        ed = self._editor()
        if ed is None:
            return
        from .edit import ADJUSTMENTS
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, css_classes=["pv-adjust"])
        box.set_size_request(260, -1)
        pending = {"src": 0}

        def refresh():
            pending["src"] = 0
            self._show_edits()
            return False

        def changed(key, v):
            ed.adjust[key] = v / 100
            if not pending["src"]:                   # a drag sends many values: one redraw per frame
                pending["src"] = GLib.idle_add(refresh)
        sliders = {}
        for key, title in ADJUSTMENTS:
            box.append(Gtk.Label(label=title, xalign=0, css_classes=["pv-adj-name"]))
            sl = ui.controls.slider(ed.adjust[key] * 100, lambda v, k=key: changed(k, v), lower=-100, upper=100,
                                    default=0)
            sliders[key] = sl
            box.append(sl)
        reset = Gtk.Button(label="Reset All", halign=Gtk.Align.END, margin_top=6)

        def reset_all(*_a):
            for k, sl in sliders.items():
                ed.adjust[k] = 0.0
                sl.set_value(0)
            self._show_edits()
        reset.connect("clicked", reset_all)
        box.append(reset)
        ui.panel.popup(self.adjust_btn, box)

    # saving
    def save(self, then=None) -> None:
        ed = self.edits
        if ed is None or not ed.edited:
            if then:
                then()
            return
        from .edit import Edits
        if not Edits.writable(self.path):
            self.save_as(then)
            return
        try:
            ed.save(self.path)
        except Exception as e:                     # never lose the edits silently
            ui.dialog.alert("The picture couldn't be saved.", str(e), [("ok", "OK", "default")], parent=self)
            return
        self._show_edits()
        if then:
            then()

    def save_as(self, then=None) -> None:
        if self.edits is None and self._editor() is None:
            return
        from ..files.chooser import ChooserWindow
        base = os.path.splitext(os.path.basename(self.path))[0]
        from .edit import Edits
        ext = os.path.splitext(self.path)[1] if Edits.writable(self.path) else ".jpg"

        def done(uris, _i):
            if not uris:
                return
            target = Gio.File.new_for_uri(uris[0]).get_path()
            if not target:
                return
            if os.path.splitext(target)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif",
                                                           ".tiff"):
                target += ".png"
            try:
                self.edits.save(target)
            except Exception as e:
                ui.dialog.alert("The picture couldn't be saved.", str(e), [("ok", "OK", "default")], parent=self)
                return
            self.edits = None
            self.open(target)
            if then:
                then()
        dlg = ChooserWindow(self.get_application(), mode="save", title="Save As",
                            folder=Gio.File.new_for_path(os.path.dirname(self.path)).get_uri(), name=base + ext,
                            filters=[("Images", [(0, "*.png"), (0, "*.jpg"), (0, "*.jpeg"), (0, "*.webp")])],
                            on_done=done)
        dlg.set_transient_for(self)
        dlg.set_modal(True)
        dlg.present()

    def _ask_save(self, then) -> None:
        name = os.path.basename(self.path or "")

        def answer(rid):
            if rid == "save":
                self.save(then)
            elif rid == "discard":
                self.edits.revert()
                then()
        ui.dialog.alert(f"Do you want to keep the changes you made to “{name}”?",
                        "Your changes will be lost if you don't save them.",
                        [("discard", "Don't Save", "destructive"), ("cancel", "Cancel", ""),
                         ("save", "Save", "default")], answer, parent=self)

    def _close_request(self, _w) -> bool:
        if self.edits is not None and self.edits.edited:
            self._ask_save(self.destroy)
            return True
        return False

    # -- view ------------------------------------------------------------------------------

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
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._context_menu)
        self.scroll.add_controller(menu)
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, n, _x, _y: n == 2 and self.set_zoom(
            None if self.zoom is not None else 1.0))
        self.scroll.add_controller(click)

    def _context_menu(self, gesture, _n, x, y) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        from .. import prefs
        Item = ui.menu.Item
        uri = Gio.File.new_for_path(self.path).get_uri() if self.path else None
        ui.menu.popup(self.scroll, [
            [Item("Zoom In", lambda: self.step_zoom(1)), Item("Zoom Out", lambda: self.step_zoom(-1)),
             Item("Zoom to Fit", lambda: self.set_zoom(None)), Item("Actual Size", lambda: self.set_zoom(1.0))],
            [Item("Rotate Left", lambda: self.rotate(-90)), Item("Rotate Right", lambda: self.rotate(90)),
             Item("Flip Horizontal", lambda: self.edit(("flip", "h"))),
             Item("Flip Vertical", lambda: self.edit(("flip", "v")))],
            [Item("Crop", self.crop_button), Item("Adjust Color…", self.adjust_panel)],
            [Item("Undo", self.undo, enabled=bool(self.edits and self.edits.ops)),
             Item("Revert", self.revert, enabled=bool(self.edits and self.edits.edited)),
             Item("Save", self.save, enabled=bool(self.edits and self.edits.edited)),
             Item("Save As…", self.save_as)],
            [Item("Set Desktop Picture", lambda: prefs.set_wallpaper(uri), enabled=bool(uri))],
        ], at=(x, y), glass=True, passthrough=True)

    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        k = Gdk.keyval_to_lower(keyval)
        if cmd:
            act = {Gdk.KEY_plus: lambda: self.step_zoom(1), Gdk.KEY_equal: lambda: self.step_zoom(1),
                   Gdk.KEY_minus: lambda: self.step_zoom(-1), Gdk.KEY_0: lambda: self.set_zoom(None),
                   Gdk.KEY_9: lambda: self.set_zoom(1.0), Gdk.KEY_r: lambda: self.rotate(90),
                   Gdk.KEY_l: lambda: self.rotate(-90), Gdk.KEY_w: self.close,
                   Gdk.KEY_f: self._toggle_fullscreen, Gdk.KEY_z: self.undo, Gdk.KEY_s: self.save,
                   Gdk.KEY_k: self.crop_button}.get(k)
            if k == Gdk.KEY_s and state & Gdk.ModifierType.SHIFT_MASK:
                act = self.save_as
        elif self.crop_area.get_visible() and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Escape):
            act = self._apply_crop if keyval != Gdk.KEY_Escape else self._end_crop
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
        full = not self.is_fullscreen()
        self.fullscreen() if full else self.unfullscreen()
        self.toolbar.set_visible(not full)


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
                              "image/svg+xml;image/x-icon;image/avif;image/heic;image/heif;image/jxl;"
                              + "".join(t + ";" for t in RAW_TYPES) + "\n"
                              "StartupNotify=true\n"
                              f"Exec={command} preview %F\n")
