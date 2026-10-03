"""Preview (macOS Preview, images): a window per picture, or one window for
the pictures opened together, with their thumbnails in a sidebar.

The picture fits the window; ⌘+ / ⌘- / ⌘0 zoom in steps that glide, ⌘9
shows it at actual size. Ctrl+wheel and a pinch zoom around the pointer,
two-finger scroll or dragging pans a zoomed picture (a flick glides on),
a double-click goes to actual size around the click and back to fit.
← / → go through the other pictures (the folder's, or the files opened
together), ⌥⌘2 shows the thumbnail sidebar, ⌘I the Info panel (size,
file, colour, camera EXIF), ⌘⇧F plays a slideshow (full screen, a
cross-fade every 3 s, ← → move, Space pauses, Esc stops). ⌘R / ⌘L rotate,
⌘K crops, Adjust Color / Resize… / Export As… are on the toolbar and the
context menu; ⌘Z undoes, ⌘S saves, ⌘⇧S saves as. Space or ⌘F fill the
screen, ⌘W closes (asking about unsaved edits). The title bar is the
glass one the compositor draws; the tools sit on a toolbar of the same
glass."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from ..ui import tokens  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.preview"
from ..imageload import RAW_EXTS, RAW_TYPES  # noqa: E402

EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".svg", ".ico", ".avif", ".heic",
        ".heif", ".jxl", ".tga", ".qoi") + RAW_EXTS
ZOOMS = (0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0)
SLIDE_S = 3                        # slideshow: seconds per picture

ui.register("""
.pv-canvas { background: %(content_bg)s; }
window.pv-slideshow .pv-canvas { background: #000000; }   /* the slideshow's stage is black, like macOS */
.pv-adjust { padding: 12px 14px 10px 14px; }
.pv-adjust label.pv-adj-name { font-size: %(text_small)s; color: %(label_secondary)s; }
.pv-sheet { padding: 12px 14px 10px 14px; }
.pv-sheet label { font-size: %(text_small)s; }
.pv-sheet label.pv-sheet-title { font-size: %(text_body)s; font-weight: 700; }
.pv-sheet label.pv-sheet-dim { color: %(label_secondary)s; }
.pv-sheet spinbutton { min-height: %(control_h)s; font-size: %(text_small)s; }
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
    def __init__(self, app, path: str, paths=None):
        """paths: the pictures opened together (their thumbnails show);
        None: the folder's pictures, sidebar hidden."""
        if not GLib.get_application_name():
            GLib.set_application_name("Preview")           # Recent documents need it
        super().__init__(application=app, css_classes=["sonata-preview"])
        ui.window.standard(self)
        from .canvas import Canvas
        from .info import InfoPanel
        from .sidebar import Thumbnails
        self.path = None
        self.texture = None
        self.size_text = ""
        self.edits = None                 # edit.Edits once something is edited
        self.crop = None                  # crop mode: the selection (x0, y0, x1, y1) in canvas pixels
        self.group = list(paths) if paths and len(paths) > 1 else None   # opened together
        self.pics = list(self.group or [path])
        self._asking = False              # a save prompt is up
        self._slides = None               # slideshow: {"timer": id, "paused": bool, "sidebar", "info"}
        self._info_serial = 0
        self._open_serial = 0             # ← / →: the latest picture asked for wins
        self._loading = None              # the picture being decoded off the main loop
        self._saving = False              # a save is being written (edits wait)
        self.toolbar = ui.window.glass_toolbar(self, start=(
            ("sidebar-show-symbolic", "Thumbnails", self.toggle_sidebar),
        ), end=(
            ("info-symbolic", "Show Info", self.toggle_info),
            ("zoom-out-symbolic", "Zoom Out", lambda: self.step_zoom(-1)),
            ("zoom-in-symbolic", "Zoom In", lambda: self.step_zoom(1)),
            ("object-rotate-left-symbolic", "Rotate Left", lambda: self.rotate(-90)),
            ("sonata-crop-symbolic", "Crop", self.crop_button),
            ("sonata-adjust-symbolic", "Adjust Color", self.adjust_panel)))
        tools = self.toolbar.get_child().get_end_widget()
        self.adjust_btn = tools.get_last_child()
        self.crop_btn = self.adjust_btn.get_prev_sibling()
        self.info_btn = tools.get_first_child()
        self.sidebar_btn = self.toolbar.get_child().get_start_widget().get_first_child()
        self.canvas = Canvas()
        # the crop selection is drawn over the picture
        self.overlay = Gtk.Overlay(child=self.canvas, vexpand=True, hexpand=True)
        self.crop_area = Gtk.DrawingArea(visible=False, can_target=True)
        self.crop_area.set_draw_func(self._draw_crop)
        self.overlay.add_overlay(self.crop_area)
        # thumbnails | picture | info
        dur = tokens.ms(250)
        self.thumbs = Thumbnails(self._pick)
        self.sidebar = Gtk.Revealer(child=self.thumbs, transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
                                    transition_duration=dur, reveal_child=bool(self.group))
        self.info = InfoPanel()
        self.info_rev = Gtk.Revealer(child=self.info, transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                     transition_duration=dur, reveal_child=False)
        body = Gtk.Box(vexpand=True)
        body.append(self.sidebar)
        body.append(self.overlay)
        body.append(self.info_rev)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self.toolbar)
        col.append(body)
        self.set_child(col)
        self._input()
        self._crop_input()
        self.connect("close-request", self._close_request)
        self.connect("notify::fullscreened", self._fullscreen_changed)
        self.connect("unmap", lambda *_: self.stop_slideshow())
        self.open(path)
        if self.group is None and self.path:
            self._load_siblings()

    # -- properties --------------------------------------------------------------------------
    @property
    def zoom(self):
        """None: fits the window; a number: the scale."""
        return self.canvas.zoom

    def _load_siblings(self) -> None:
        """The folder's pictures, listed off the main loop."""
        from ..backend.system import run_async
        path = self.path

        def done(pics):
            if pics and self.group is None and self.path and os.path.dirname(self.path) == os.path.dirname(path):
                self.pics = pics if self.path in pics else pics + [self.path]
                self.thumbs.set_paths(self.pics, self.path)
        run_async(siblings, done, path)

    # -- file ------------------------------------------------------------------------------
    def open(self, path: str, fade: bool = False) -> None:
        if self._saving:
            return
        if self.edits is not None and self.edits.edited:       # ← / → with unsaved edits: ask first
            self._ask_save(lambda: (setattr(self, "edits", None), self.open(path, fade)))
            return
        self._open_serial += 1
        if not self.get_realized():               # the first picture sizes the window
            self._loading = None
            self._opened(path, load_texture(path), fade)
            return
        # ← / →: decoded off the main loop (a big photo froze the window)
        from ..backend.system import run_async
        serial = self._open_serial
        self._loading = path

        def done(tex):
            if serial == self._open_serial:
                self._loading = None
                self._opened(path, tex, fade)
        run_async(load_texture, done, path)

    def _opened(self, path: str, tex, fade: bool) -> None:
        self.edits, self.crop = None, None
        self.crop_area.set_visible(False)
        known = self.path is None or path in self.pics
        self.path, self.texture = path, tex
        name = os.path.basename(path)
        self.set_title(name)
        if not known:                                           # Save As: a new file, maybe elsewhere
            self.pics = self.pics + [path] if self.group else [path]
            if self.group is None:
                self._load_siblings()
        self.thumbs.set_paths(self.pics, path)
        if tex is None:
            self.canvas.set_texture(None)
            self.set_default_size(520, 360)

            # once the window is on screen: an alert on a window not shown yet
            # never appeared, and left Preview running with nothing visible
            def tell():
                if self.path != path or len(self.pics) > 1:     # moved on, or others to see: no need to close
                    if self.path == path:
                        ui.dialog.alert(f"“{name}” couldn't be opened.", "It isn't a picture Preview can read.",
                                        [("ok", "OK", "default")], parent=self)
                    return False
                ui.dialog.alert(f"“{name}” couldn't be opened.", "It isn't a picture Preview can read.",
                                [("ok", "OK", "default")], lambda _r: self.close(), parent=self)
                return False
            GLib.timeout_add(150, tell)
            return
        self.size_text = f"{tex.get_width()} × {tex.get_height()}"
        self.canvas.set_texture(tex, fade=fade)
        Gtk.RecentManager.get_default().add_item(Gio.File.new_for_path(path).get_uri())
        if not self.get_realized():               # the window takes the picture's shape
            w, h = tex.get_width(), tex.get_height()
            mon = Gdk.Display.get_default().get_monitors().get_item(0)
            g = mon.get_geometry() if mon else None
            max_w, max_h = (g.width * 0.7, g.height * 0.7) if g else (1100, 760)
            side = self.thumbs.get_size_request()[0] if self.sidebar.get_reveal_child() else 0
            s = min(1.0, (max_w - side) / max(1, w), (max_h - ui.window.TITLEBAR_H) / max(1, h))
            self.set_default_size(max(420, int(w * s) + side), max(300, int(h * s) + ui.window.TITLEBAR_H))
        if self.info_rev.get_reveal_child():
            self._refresh_info()

    def go(self, step: int, fade: bool = False) -> None:
        cur = self._loading or self.path          # pressed again while one loads: from that one
        if not cur:
            return
        pics = self.pics
        if cur in pics and len(pics) > 1:
            self.open(pics[(pics.index(cur) + step) % len(pics)], fade=fade)

    def _pick(self, path: str) -> None:
        """A thumbnail was clicked."""
        if path != (self._loading or self.path):
            self.open(path)
            if self._loading != path and self.path != path:   # asked about the edits first: stay
                self.thumbs.select(self.path)

    # -- sidebar / info ----------------------------------------------------------------------
    def toggle_sidebar(self) -> None:
        self.sidebar.set_reveal_child(not self.sidebar.get_reveal_child())
        if self.sidebar.get_reveal_child():
            GLib.idle_add(lambda: (self.thumbs.select(self.path), False)[1])

    def toggle_info(self) -> None:
        show = not self.info_rev.get_reveal_child()
        self.info_rev.set_reveal_child(show)
        if show:
            self._refresh_info()

    def _refresh_info(self) -> None:
        if not self.path:
            return
        from ..backend.system import run_async
        from .info import read
        self._info_serial += 1
        serial, path = self._info_serial, self.path
        size = (self.texture.get_width(), self.texture.get_height()) if self.texture is not None else None
        if self.info.path != path:
            self.info.show(path, {"name": os.path.basename(path)})

        def done(data):
            if data is not None and serial == self._info_serial:
                self.info.show(path, data)
        run_async(read, done, path, size)

    # -- slideshow -------------------------------------------------------------------------
    def slideshow(self) -> None:
        """⌘⇧F: full screen, a cross-fade to the next picture every 3 s."""
        if self._slides is not None:
            self.stop_slideshow()
            return
        if self.edits is not None and self.edits.edited:
            self._ask_save(self.slideshow)
            return
        self._end_crop()
        self._slides = {"timer": 0, "paused": False, "sidebar": self.sidebar.get_reveal_child(),
                        "info": self.info_rev.get_reveal_child()}
        for rev in (self.sidebar, self.info_rev):
            rev.set_transition_duration(0)
            rev.set_reveal_child(False)
        self.add_css_class("pv-slideshow")
        self.toolbar.set_visible(False)
        self.canvas.set_zoom(None)
        self.fullscreen()
        self._arm_slides()

    def _arm_slides(self) -> None:
        s = self._slides
        if s is None:
            return
        if s["timer"]:
            GLib.source_remove(s["timer"])
        s["timer"] = 0 if s["paused"] else GLib.timeout_add_seconds(SLIDE_S, self._next_slide)

    def _next_slide(self) -> bool:
        if self._slides is None:
            return False
        if self.get_mapped() and not (self.get_surface() and self.get_surface().get_state()
                                      & Gdk.ToplevelState.MINIMIZED):
            self.go(1, fade=True)
        return True

    def stop_slideshow(self) -> None:
        s, self._slides = self._slides, None
        if s is None:
            return
        if s["timer"]:
            GLib.source_remove(s["timer"])
        self.remove_css_class("pv-slideshow")
        self.toolbar.set_visible(True)
        for rev, shown in ((self.sidebar, s["sidebar"]), (self.info_rev, s["info"])):
            rev.set_reveal_child(shown)
            rev.set_transition_duration(tokens.ms(250))
        if self.is_fullscreen():
            self.unfullscreen()

    def _fullscreen_changed(self, *_a) -> None:
        full = self.is_fullscreen()
        if not full and self._slides is not None:      # left full screen some other way
            self.stop_slideshow()
        self.toolbar.set_visible(not full)

    # -- editing ---------------------------------------------------------------------------
    def _editor(self):
        """The picture's edits (loaded the first time something is edited)."""
        if self._saving:                          # edits wait for the save being written
            return None
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
        self.canvas.set_texture(self.texture, keep_view=True)
        name = os.path.basename(self.path or "")
        self.set_title(name + (" — Edited" if ed.edited else ""))

    def edit(self, op) -> None:
        if self._saving:                          # the save renders these edits right now
            return
        ed = self._editor()
        if ed is not None:
            ed.push(op)
            self._show_edits()

    def rotate(self, degrees: int) -> None:
        self.edit(("rotate", degrees % 360))

    def undo(self) -> None:
        if not self._saving and self.edits is not None and self.edits.undo():
            self._show_edits()

    def revert(self) -> None:
        if not self._saving and self.edits is not None:
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
        return self.canvas.image_rect()

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
        reset = Gtk.Button(label="Reset All", halign=Gtk.Align.END, margin_top=6, css_classes=["sonata-button"])

        def reset_all(*_a):
            for k, sl in sliders.items():
                ed.adjust[k] = 0.0
                sl.set_value(0)
            self._show_edits()
        reset.connect("clicked", reset_all)
        box.append(reset)
        ui.panel.popup(self.adjust_btn, box)

    # Resize… (macOS: Tools > Adjust Size…): a small sheet under the toolbar
    def resize_sheet(self):
        ed = self._editor()
        if ed is None:
            return None
        w0, h0 = ed.size()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["pv-sheet"])
        box.append(Gtk.Label(label="Resize", xalign=0, css_classes=["pv-sheet-title"]))
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)

        def spin(value, upper):
            s = Gtk.SpinButton.new_with_range(1, upper, 1)
            s.set_value(value)
            s.set_width_chars(6)
            return s
        width, height, percent = spin(w0, 100000), spin(h0, 100000), spin(100, 1000)
        for i, (name, sb, unit) in enumerate((("Width:", width, "pixels"), ("Height:", height, "pixels"),
                                              ("Scale:", percent, "percent"))):
            grid.attach(Gtk.Label(label=name, xalign=1), 0, i, 1, 1)
            grid.attach(sb, 1, i, 1, 1)
            grid.attach(Gtk.Label(label=unit, xalign=0, css_classes=["pv-sheet-dim"]), 2, i, 1, 1)
        box.append(grid)
        keep = Gtk.Box(spacing=8)
        ratio = ui.controls.switch(True)
        keep.append(ratio)
        keep.append(Gtk.Label(label="Scale proportionally", xalign=0))
        box.append(keep)
        result = Gtk.Label(xalign=0, css_classes=["pv-sheet-dim"])
        box.append(result)
        busy = {"on": False}

        def sync(src):
            if busy["on"]:
                return
            busy["on"] = True
            w, h = width.get_value(), height.get_value()
            if src is percent:
                w, h = w0 * percent.get_value() / 100, h0 * percent.get_value() / 100
                width.set_value(round(w))
                height.set_value(round(h))
            elif ratio.get_active():
                if src is width:
                    height.set_value(max(1, round(w * h0 / w0)))
                elif src is height:
                    width.set_value(max(1, round(h * w0 / h0)))
                percent.set_value(round(width.get_value() / w0 * 100))
            result.set_label(f"Resulting size: {int(width.get_value())} × {int(height.get_value())} pixels")
            busy["on"] = False
        for sb in (width, height, percent):
            sb.connect("value-changed", sync)
        ratio.connect("notify::active", lambda *_: ratio.get_active() and sync(width))
        sync(None)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=4)
        pop = {}

        def ok():
            self.resize_to(int(width.get_value()), int(height.get_value()))
            pop["p"].popdown()
        buttons.append(ui.controls.push_button("Cancel", lambda: pop["p"].popdown()))
        buttons.append(ui.controls.push_button("OK", ok, "default"))
        box.append(buttons)
        box.sheet = {"width": width, "height": height, "percent": percent, "ratio": ratio, "ok": ok}
        pop["p"] = ui.panel.popup(self.toolbar, box)
        return box

    def resize_to(self, w: int, h: int) -> None:
        ed = self._editor()
        if ed is not None and (w, h) != ed.size():
            ed.resize_to(w, h)
            self._show_edits()

    # Export As…: the format (and quality) first, then where
    def export_sheet(self):
        from .edit import EXPORT_FORMATS, WRITABLE
        if not self.path:
            return None
        pil = WRITABLE.get(os.path.splitext(self.path)[1].lower())
        current = next((i for i, f in enumerate(EXPORT_FORMATS) if f[1] == pil), 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["pv-sheet"])
        box.set_size_request(260, -1)
        box.append(Gtk.Label(label="Export As", xalign=0, css_classes=["pv-sheet-title"]))
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(label="Format:", xalign=1))
        fmt = ui.controls.popup_button([f[0] for f in EXPORT_FORMATS], current)
        fmt.set_hexpand(True)
        row.append(fmt)
        box.append(row)
        qrow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        qlabel = Gtk.Label(xalign=0)
        state = {"q": 85}

        def quality(v):
            state["q"] = int(v)
            qlabel.set_label(f"Quality: {state['q']} %")
        qrow.append(qlabel)
        qrow.append(ui.controls.slider(85, quality, lower=10, upper=100, default=85))
        quality(85)
        box.append(qrow)

        def fmt_changed(*_a):
            qrow.set_visible(EXPORT_FORMATS[fmt.get_selected()][3])
        fmt.connect("notify::selected", fmt_changed)
        fmt_changed()
        pop = {}

        def go():
            _name, pil, fext, _q = EXPORT_FORMATS[fmt.get_selected()]
            pop["p"].popdown()
            self.export_as(pil, fext, state["q"])
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END, margin_top=4)
        buttons.append(ui.controls.push_button("Cancel", lambda: pop["p"].popdown()))
        buttons.append(ui.controls.push_button("Save…", go, "default"))
        box.append(buttons)
        box.sheet = {"format": fmt, "go": go, "quality": state}
        pop["p"] = ui.panel.popup(self.toolbar, box)
        return box

    def export_as(self, fmt: str, ext: str, quality: int = 85, target: str = None) -> bool:
        """A copy of the (edited) picture in another format; the window
        stays on this one. target: skip the Save panel (tests)."""
        ed = self.edits
        if ed is None:
            from .edit import Edits
            ed = Edits.load(self.path) if self.path else None
            if ed is None:
                ui.dialog.alert("This picture can't be exported.", "Preview can only view it.",
                                [("ok", "OK", "default")], parent=self)
                return False

        def write(path):
            try:
                ed.write(path, fmt, quality)
            except Exception as e:
                ui.dialog.alert("The picture couldn't be exported.", str(e), [("ok", "OK", "default")],
                                parent=self)
                return False
            return True

        def chosen(path):
            final = path
            if os.path.splitext(path)[1].lower() not in {".jpg": (".jpg", ".jpeg"),
                                                         ".tiff": (".tiff", ".tif")}.get(ext, (ext,)):
                final = path + ext
            return self._confirm_replace(path, final, write)
        if target:
            return chosen(target)
        from ..files.chooser import ChooserWindow
        base = os.path.splitext(os.path.basename(self.path))[0]

        def done(uris, _i):
            target = Gio.File.new_for_uri(uris[0]).get_path() if uris else None
            if target:
                chosen(target)
        dlg = ChooserWindow(self.get_application(), mode="save", title="Export As",
                            folder=Gio.File.new_for_path(os.path.dirname(self.path)).get_uri(), name=base + ext,
                            filters=[("Images", [(0, "*" + ext)])], on_done=done)
        dlg.set_transient_for(self)
        dlg.set_modal(True)
        dlg.present()
        return True

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
        path = self.path

        def done():
            self._show_edits()
            self.thumbs.refresh(path)              # its thumbnail is made again (new mtime)
            if then:
                then()
        self._save_async(ed, path, done)

    def _save_async(self, ed, path: str, done) -> None:
        """Render and write the full-size picture off the main loop; edits,
        ← / → wait meanwhile. done() on success; an alert on failure."""
        if self._saving:
            return
        from ..backend.system import run_async
        self._saving = True

        def work():
            try:
                ed.save(path)
            except Exception as e:                 # never lose the edits silently
                return e
            return None

        def finished(err):
            self._saving = False
            if err is not None:
                ui.dialog.alert("The picture couldn't be saved.", str(err), [("ok", "OK", "default")],
                                parent=self)
                return
            done()
        run_async(work, finished)

    def _confirm_replace(self, chosen: str, final: str, write):
        """Save As / Export add the extension after the Save panel checked
        the name: a file with the final name is asked about here."""
        if final == chosen or not os.path.lexists(final):
            return write(final)
        name = os.path.basename(final)
        ui.dialog.alert(f"“{name}” already exists. Do you want to replace it?",
                        "A file with the same name already exists. Replacing it will overwrite its contents.",
                        [("cancel", "Cancel", ""), ("replace", "Replace", "destructive")],
                        lambda rid: rid == "replace" and write(final), parent=self)
        return False

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
            final = target
            if os.path.splitext(target)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif",
                                                           ".tiff"):
                final += ".png"
            self._confirm_replace(target, final, write)

        def write(target):
            def saved():
                self.edits = None
                self.open(target)
                if then:
                    then()
            self._save_async(self.edits, target, saved)
        dlg = ChooserWindow(self.get_application(), mode="save", title="Save As",
                            folder=Gio.File.new_for_path(os.path.dirname(self.path)).get_uri(), name=base + ext,
                            filters=[("Images", [(0, "*.png"), (0, "*.jpg"), (0, "*.jpeg"), (0, "*.webp")])],
                            on_done=done)
        dlg.set_transient_for(self)
        dlg.set_modal(True)
        dlg.present()

    def _ask_save(self, then) -> None:
        """The "keep the changes?" alert. Once at a time: ⌘W pressed twice, or
        the close button while ← asked, used to stack alerts on each other."""
        if self._asking:
            return
        self._asking = True
        name = os.path.basename(self.path or "")

        def answer(rid):
            self._asking = False
            if rid == "save":
                self.save(then)
            elif rid == "discard":
                self.edits.revert()
                then()
            else:
                self.thumbs.select(self.path)
        self.prompt = ui.dialog.alert(f"Do you want to keep the changes you made to “{name}”?",
                                      "Your changes will be lost if you don't save them.",
                                      [("discard", "Don't Save", "destructive"), ("cancel", "Cancel", ""),
                                       ("save", "Save", "default")], answer, parent=self)

    def _close_request(self, _w) -> bool:
        if self.edits is not None and self.edits.edited:
            self._ask_save(self.destroy)
            return True
        self.stop_slideshow()
        return False

    # -- view ------------------------------------------------------------------------------
    def fit_scale(self) -> float:
        return self.canvas.fit_scale()

    def set_zoom(self, z, anchor=None, animate: bool = False) -> None:
        """z: a scale, None fits the window; anchor: the point that stays put
        (default: the middle of what shows)."""
        if z is not None:
            z = max(ZOOMS[0], min(ZOOMS[-1], z))
        self.canvas.set_zoom(z, anchor=anchor, animate=animate)

    def step_zoom(self, step: int) -> None:
        cur = self.canvas._target or self.zoom or self.fit_scale()
        if step > 0:
            nxt = next((z for z in ZOOMS if z > cur + 1e-3), ZOOMS[-1])
        else:
            nxt = next((z for z in reversed(ZOOMS) if z < cur - 1e-3), ZOOMS[0])
        self.set_zoom(nxt, animate=True)

    # -- input -----------------------------------------------------------------------------
    def _input(self) -> None:
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._context_menu)
        self.canvas.add_controller(menu)

    def _context_menu(self, gesture, _n, x, y) -> None:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        from .. import prefs
        Item = ui.menu.Item
        uri = Gio.File.new_for_path(self.path).get_uri() if self.path else None
        edited = bool(self.edits and self.edits.edited)
        ui.menu.popup(self.canvas, [
            [Item("Zoom In", lambda: self.step_zoom(1)), Item("Zoom Out", lambda: self.step_zoom(-1)),
             Item("Zoom to Fit", lambda: self.set_zoom(None, animate=True)),
             Item("Actual Size", lambda: self.set_zoom(1.0, anchor=(x, y), animate=True))],
            [Item("Thumbnails", lambda *_: self.toggle_sidebar(), checked=self.sidebar.get_reveal_child()),
             Item("Show Info", lambda *_: self.toggle_info(), checked=self.info_rev.get_reveal_child()),
             Item("Slideshow", self.slideshow)],
            [Item("Rotate Left", lambda: self.rotate(-90)), Item("Rotate Right", lambda: self.rotate(90)),
             Item("Flip Horizontal", lambda: self.edit(("flip", "h"))),
             Item("Flip Vertical", lambda: self.edit(("flip", "v")))],
            [Item("Crop", self.crop_button), Item("Adjust Color…", self.adjust_panel),
             Item("Resize…", self.resize_sheet)],
            [Item("Undo", self.undo, enabled=bool(self.edits and self.edits.ops)),
             Item("Revert", self.revert, enabled=edited),
             Item("Save", self.save, enabled=edited),
             Item("Save As…", self.save_as), Item("Export As…", self.export_sheet)],
            [Item("Set Desktop Picture", lambda: prefs.set_wallpaper(uri), enabled=bool(uri))],
        ], at=(x, y), glass=True, passthrough=True)

    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        shift = state & Gdk.ModifierType.SHIFT_MASK
        alt = state & Gdk.ModifierType.ALT_MASK
        k = Gdk.keyval_to_lower(keyval)
        if self._slides is not None and not cmd:         # slideshow keys
            act = {Gdk.KEY_Left: lambda: (self.go(-1, fade=True), self._arm_slides()),
                   Gdk.KEY_Up: lambda: (self.go(-1, fade=True), self._arm_slides()),
                   Gdk.KEY_Right: lambda: (self.go(1, fade=True), self._arm_slides()),
                   Gdk.KEY_Down: lambda: (self.go(1, fade=True), self._arm_slides()),
                   Gdk.KEY_space: self._pause_slides,
                   Gdk.KEY_Escape: self.stop_slideshow}.get(keyval)
        elif cmd:
            act = {Gdk.KEY_plus: lambda: self.step_zoom(1), Gdk.KEY_equal: lambda: self.step_zoom(1),
                   Gdk.KEY_minus: lambda: self.step_zoom(-1), Gdk.KEY_0: lambda: self.set_zoom(None, animate=True),
                   Gdk.KEY_9: lambda: self.set_zoom(1.0, animate=True), Gdk.KEY_r: lambda: self.rotate(90),
                   Gdk.KEY_l: lambda: self.rotate(-90), Gdk.KEY_w: self.close,
                   Gdk.KEY_f: self._toggle_fullscreen, Gdk.KEY_z: self.undo, Gdk.KEY_s: self.save,
                   Gdk.KEY_k: self.crop_button, Gdk.KEY_i: self.toggle_info}.get(k)
            if k == Gdk.KEY_s and shift:
                act = self.save_as
            elif k == Gdk.KEY_f and shift:
                act = self.slideshow
            elif alt and keyval in (Gdk.KEY_2, Gdk.KEY_at, Gdk.KEY_KP_2, Gdk.KEY_twosuperior):
                act = self.toggle_sidebar
        elif self.crop_area.get_visible() and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Escape):
            act = self._apply_crop if keyval != Gdk.KEY_Escape else self._end_crop
        else:
            act = {Gdk.KEY_Left: lambda: self.go(-1), Gdk.KEY_Right: lambda: self.go(1),
                   Gdk.KEY_Up: lambda: self.go(-1), Gdk.KEY_Down: lambda: self.go(1),
                   Gdk.KEY_space: self._toggle_fullscreen,
                   Gdk.KEY_Escape: lambda: self.is_fullscreen() and self._toggle_fullscreen()}.get(keyval)
        if act is None:
            return False
        act()
        return True

    def _pause_slides(self) -> None:
        if self._slides is not None:
            self._slides["paused"] = not self._slides["paused"]
            self._arm_slides()

    def _toggle_fullscreen(self) -> None:
        if self._slides is not None:
            self.stop_slideshow()
            return
        full = not self.is_fullscreen()
        self.fullscreen() if full else self.unfullscreen()
        self.toolbar.set_visible(not full)


def open_paths(app, paths) -> None:
    """One window per picture; several pictures opened together share one
    window with their thumbnails in the sidebar."""
    files = [Gio.File.new_for_commandline_arg(p).get_path() for p in paths]
    files = [p for p in files if p]
    if len(files) > 1:
        PreviewWindow(app, files[0], paths=files).present()
    for path in files[:1] if len(files) == 1 else []:
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
