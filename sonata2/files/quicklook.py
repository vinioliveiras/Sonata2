"""Quick Look (Space) and Get Info (Ctrl+I), macOS Big Sur style.

Quick Look: a floating panel that previews the selection -- pictures,
videos/audio (GTK media), text files, anything else as a big icon with its
details -- and follows the selection while it is open. Space or Escape
closes it; "Open with <app>" opens the file.

Get Info: one small window per item: icon, name, kind, size (folders
counted in the background), where, created, modified."""
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from .folder import file_of, is_dir  # noqa: E402

TEXT_MAX = 256 * 1024

ui.register("""
.ql-title { font-weight: 700; font-size: %(text_body)s; }
button.ql-open { min-height: 22px; padding: 0 10px; border-radius: 6px; border: none; font-size: %(text_small)s;
  background: alpha(%(label)s, 0.08); color: %(label)s; box-shadow: none; }
button.ql-open:hover { background: alpha(%(label)s, 0.14); }
.ql-text { font-family: "SF Mono", monospace; font-size: 12px; background: %(content_bg)s; }
.ql-big-name { font-weight: 700; font-size: 18px; }
.ql-meta { color: %(label_secondary)s; }
.gi-name { font-weight: 700; font-size: %(text_body)s; }
.gi-key { color: %(label_secondary)s; font-size: %(text_small)s; font-weight: 700; }
.gi-val { font-size: %(text_small)s; }
""", key="files-quicklook")


def previewable(info) -> bool:
    """Quick Look shows it itself (picture, movie, sound, text): a
    double-click opens it here instead of in another app (Vini's call)."""
    if info is None or is_dir(info) or not file_of(info).get_path():
        return False
    ct = info.get_content_type() or ""
    if info.get_name().endswith(".desktop"):
        return False                     # an app: opening it launches it
    if ct.startswith(("image/", "video/", "audio/")):
        return True
    return (ct.startswith("text/") or Gio.content_type_is_a(ct, "text/plain")) and info.get_size() <= TEXT_MAX * 8


def _fmt_date(info, attr):
    t = info.get_attribute_uint64(attr) if info.has_attribute(attr) else 0
    if not t:
        return "--"
    return GLib.DateTime.new_from_unix_local(t).format("%-d %B %Y at %H:%M")


class QuickLook(Adw.Window):
    def __init__(self, parent, on_close=None):
        # not transient: Wayfire won't maximize a window with a parent, and
        # Quick Look is its own window on macOS too (the desktop's has none)
        super().__init__(application=parent.get_application() if parent else None,
                         default_width=720, default_height=520, title="Quick Look")
        for c in ("sonata-ql", "sonata-glass-window"):          # frosted glass, like the Dock
            self.add_css_class(c)
        ui.window.standard(self)
        self.on_close = on_close
        self.info = None
        tv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.open_btn = Gtk.Button(css_classes=["ql-open"], valign=Gtk.Align.CENTER)
        self.open_btn.connect("clicked", lambda *_: self._open())
        head = ui.window.titlebar(self, "", end=self.open_btn, zoom=self._zoom)
        self.title = head.title_label
        tv.append(head)
        self.body = Gtk.Box(vexpand=True, hexpand=True)
        tv.append(self.body)
        self.set_content(tv)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", lambda *_: (self.on_close and self.on_close(), False)[1])

    def _zoom(self):
        self.unmaximize() if self.is_maximized() else self.maximize()

    def _key(self, _c, keyval, _code, _state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_Escape):
            self.close()
            return True
        return False

    def show_item(self, info) -> None:
        self.info = info
        f = file_of(info)
        self.title.set_label(info.get_display_name())
        ct = info.get_content_type() or ""
        app = None if is_dir(info) else Gio.AppInfo.get_default_for_type(ct, False) if ct else None
        self.open_btn.set_label(f"Open with {app.get_display_name()}" if app else "Open")
        self.open_btn.app = app
        child = self.body.get_first_child()
        if child is not None:
            if isinstance(child, Gtk.Video):
                child.get_media_stream() and child.get_media_stream().pause()
            self.body.remove(child)
        self.body.append(self._preview(info, f, ct))
        self.present()

    def _preview(self, info, f, ct) -> Gtk.Widget:
        path = f.get_path()
        if path and ct.startswith("image/"):
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, hexpand=True, vexpand=True,
                              margin_start=12, margin_end=12, margin_bottom=12)
            from .. import imageload                  # WebP, AVIF, camera RAW... too
            from ..backend.system import run_async

            def loaded(tex):                          # decoded off the main loop (big photos)
                if pic.get_parent() is not self.body:
                    return                            # the selection moved on meanwhile
                if tex is not None:
                    pic.set_paintable(tex)
                else:
                    pic.set_file(f)
            run_async(imageload.texture, loaded, path)
            return pic
        if path and (ct.startswith("video/") or ct.startswith("audio/")):
            v = Gtk.Video(file=f, autoplay=True, hexpand=True, vexpand=True)
            return v
        if path and (ct.startswith("text/") or Gio.content_type_is_a(ct, "text/plain")) and \
                info.get_size() <= TEXT_MAX * 8:
            view = Gtk.TextView(editable=False, cursor_visible=False, monospace=True, css_classes=["ql-text"],
                                left_margin=14, right_margin=14, top_margin=10, bottom_margin=10,
                                wrap_mode=Gtk.WrapMode.WORD_CHAR)
            from ..backend.system import run_async

            def read():
                try:
                    with open(path, "rb") as fh:
                        return fh.read(TEXT_MAX).decode("utf-8", "replace")
                except OSError:
                    return None
            run_async(read, lambda text: text is not None and view.get_buffer().set_text(text))
            return Gtk.ScrolledWindow(child=view, hexpand=True, vexpand=True)
        # anything else: big icon + details, like macOS
        from .views import kind, size, date
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True, vexpand=True,
                      valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        img = Gtk.Image(pixel_size=180)
        from .views import set_icon
        set_icon(img, info)
        col.append(img)
        col.append(Gtk.Label(label=info.get_display_name(), css_classes=["ql-big-name"], wrap=True,
                             justify=Gtk.Justification.CENTER))
        col.append(Gtk.Label(label=f"{kind(info)} – {size(info)}", css_classes=["ql-meta"]))
        col.append(Gtk.Label(label=f"Modified {date(info)}", css_classes=["ql-meta"]))
        return col

    def _open(self):
        if not self.info:
            return
        f = file_of(self.info)
        try:
            if getattr(self.open_btn, "app", None):
                self.open_btn.app.launch([f], self.get_display().get_app_launch_context())
            else:
                Gio.AppInfo.launch_default_for_uri(f.get_uri(), self.get_display().get_app_launch_context())
        except GLib.Error:
            pass
        self.close()


class GetInfo(Adw.Window):
    def __init__(self, parent, info):
        super().__init__(transient_for=None, default_width=300,
                         title=f"{info.get_display_name()} Info")
        self.set_size_request(240, 200)
        for c in ("sonata-info", "sonata-glass-window"):       # frosted glass, like the Dock
            self.add_css_class(c)
        ui.window.standard(self)
        f = file_of(info)
        from .views import kind, size, set_icon
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_start=16, margin_end=16,
                      margin_bottom=16)
        top = Gtk.Box(spacing=10)
        img = Gtk.Image(pixel_size=48)
        set_icon(img, info)
        top.append(img)
        names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        names.append(Gtk.Label(label=info.get_display_name(), xalign=0, css_classes=["gi-name"],
                               ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=28, hexpand=True))
        self.size_lbl = Gtk.Label(label=size(info), xalign=0, css_classes=["gi-val"])
        names.append(self.size_lbl)
        top.append(names)
        col.append(top)
        col.append(Gtk.Separator())
        grid = Gtk.Grid(column_spacing=10, row_spacing=4)
        parent_dir = f.get_parent()
        rows = [("Kind:", kind(info)), ("Size:", size(info)),
                ("Where:", parent_dir.get_path() or parent_dir.get_uri() if parent_dir else "--"),
                ("Created:", _fmt_date(info, "time::created")), ("Modified:", _fmt_date(info, "time::modified"))]
        self.size_row = None
        for r, (k, v) in enumerate(rows):
            grid.attach(Gtk.Label(label=k, xalign=1, css_classes=["gi-key"]), 0, r, 1, 1)
            val = Gtk.Label(label=v, xalign=0, css_classes=["gi-val"], wrap=True, max_width_chars=30,
                            hexpand=True, natural_wrap_mode=Gtk.NaturalWrapMode.NONE)
            grid.attach(val, 1, r, 1, 1)
            if k == "Size:":
                self.size_row = val
        col.append(grid)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.append(ui.window.titlebar(self, zoom=True))    # traffic lights where every window has them
        outer.append(Gtk.ScrolledWindow(child=col, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                        propagate_natural_height=True, propagate_natural_width=True))
        self.set_content(outer)
        if is_dir(info) and f.get_path():
            self.size_row.set_label("Calculating…")
            threading.Thread(target=self._count, args=(f.get_path(),), daemon=True).start()
        elif not is_dir(info):
            self.size_row.set_label(f"{info.get_size():,} bytes ({size(info)} on disk)")

    def _count(self, path):
        total = items = 0
        for root, dirs, files in os.walk(path, onerror=lambda e: None):
            for n in files:
                try:
                    total += os.lstat(os.path.join(root, n)).st_size
                except OSError:
                    pass
            items += len(files) + len(dirs)
        text = f"{ui.fmt.size(total)} for {items:,} items"
        GLib.idle_add(lambda: (self.size_row.set_label(text), self.size_lbl.set_label(ui.fmt.size(total)), False)[-1])
