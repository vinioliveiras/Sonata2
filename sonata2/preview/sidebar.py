"""Preview's thumbnail sidebar (macOS Preview: View > Thumbnails, ⌥⌘2).

A lazy Gtk.ListView of the pictures (the folder's, or the files opened
together); only the rows on screen ask for a thumbnail. Thumbnails are
made off the main loop by one worker, newest request first (the rows
just scrolled to), and kept in a small in-memory LRU shared by every
window."""
import os
import threading
from collections import OrderedDict

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402

THUMB = 160                   # px: the longest side made (sharp on 2x screens at the row's size)
CACHE_MAX = 160               # thumbnails kept (~12 MB at most)
ROW_W, ROW_H = 104, 78        # the thumbnail's box in a row
SIDEBAR_W = 148

ui.register("""
.pv-sidebar { background: %(sidebar_material)s; box-shadow: inset -1px 0 %(separator)s; }
.pv-thumbs, .pv-thumbs > row { background: none; }
.pv-thumbs > row { padding: 6px 8px 4px 8px; margin: 0 6px; border-radius: %(r_label)s; outline: none;
  transition: background-color %(t_fast)s ease; }
.pv-thumbs > row:hover { background: alpha(%(label)s, 0.04); }
.pv-thumbs > row:selected { background: %(sidebar_selected)s; }
.pv-thumb { border-radius: 2px; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.25), 0 0 0 0.5px %(hairline)s; }
.pv-thumb-name { font-size: %(text_small)s; color: %(label_secondary)s; }
.pv-thumbs > row:selected .pv-thumb-name { color: %(label)s; font-weight: 600; }
""", key="preview-sidebar")


class ThumbLoader:
    """Thumbnails made by one worker thread (imageload.pixbuf_at_scale),
    cached as textures in an LRU. get() returns a cached one, or asks for it
    and calls back(path, texture) on the main loop."""

    def __init__(self, size: int = THUMB, limit: int = CACHE_MAX):
        self.size, self.limit = size, limit
        self.cache = OrderedDict()           # (path, mtime) -> Gdk.Texture | None
        self.waiting = {}                    # key -> [callbacks]
        self.pending = []                    # keys to make, newest last
        self.lock = threading.Lock()
        self.busy = False

    @staticmethod
    def key(path: str):
        try:
            return path, os.stat(path).st_mtime_ns
        except OSError:
            return path, 0

    def get(self, path: str, back):
        k = self.key(path)
        if k in self.cache:
            self.cache.move_to_end(k)
            return self.cache[k]
        if k in self.waiting:
            self.waiting[k].append(back)
            with self.lock:                  # asked again: to the front of the queue
                if k in self.pending:
                    self.pending.remove(k)
                    self.pending.append(k)
            return None
        self.waiting[k] = [back]
        with self.lock:
            self.pending.append(k)
        self._kick()
        return None

    def forget(self, path: str, back) -> None:
        """A row left the screen: its thumbnail isn't needed any more."""
        k = self.key(path)
        backs = self.waiting.get(k)
        if backs and back in backs:
            backs.remove(back)
            if not backs:
                del self.waiting[k]
                with self.lock:
                    if k in self.pending:
                        self.pending.remove(k)

    def _kick(self) -> None:
        if self.busy or not self.pending:
            return
        self.busy = True
        from ..backend.system import run_async
        run_async(self._work, self._finished)

    def _work(self):                          # worker thread
        from .. import imageload
        while True:
            with self.lock:
                if not self.pending:
                    return None
                k = self.pending.pop()
            try:
                pb = imageload.pixbuf_at_scale(k[0], self.size)
            except Exception:
                pb = None
            GLib.idle_add(self._deliver, k, pb)

    def _finished(self, _res) -> None:
        self.busy = False
        self._kick()                          # asked for while the worker was stopping

    def _deliver(self, k, pb) -> bool:
        tex = None
        if pb is not None:
            try:
                fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
                tex = Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt, pb.read_pixel_bytes(),
                                            pb.get_rowstride())
            except Exception:
                tex = None
        self.cache[k] = tex
        while len(self.cache) > self.limit:
            self.cache.popitem(last=False)
        for back in self.waiting.pop(k, []):
            back(k[0], tex)
        return False


LOADER = ThumbLoader()


class Thumbnails(Gtk.Box):
    """The sidebar. on_pick(path) when a thumbnail is clicked (or chosen
    with ↑ ↓ in the list)."""

    def __init__(self, on_pick):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["pv-sidebar", "sonata-sidebar"])
        self.set_size_request(SIDEBAR_W, -1)
        self.set_hexpand(False)
        self.on_pick = on_pick
        self.paths = []
        self._quiet = False
        self.model = Gtk.StringList()
        self.selection = Gtk.SingleSelection(model=self.model, autoselect=False, can_unselect=False)
        self.selection.connect("notify::selected", self._picked)
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup)
        factory.connect("bind", self._bind)
        factory.connect("unbind", self._unbind)
        self.view = Gtk.ListView(model=self.selection, factory=factory, css_classes=["pv-thumbs"],
                                 margin_top=6, margin_bottom=6)
        self.append(Gtk.ScrolledWindow(child=self.view, vexpand=True,
                                       hscrollbar_policy=Gtk.PolicyType.NEVER))

    # rows: the thumbnail over the name
    def _setup(self, _f, item) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        frame = Gtk.CenterBox(halign=Gtk.Align.CENTER)          # centres the picture in a fixed box
        frame.set_size_request(ROW_W, ROW_H)
        pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, valign=Gtk.Align.CENTER,
                          css_classes=["pv-thumb"])
        frame.set_center_widget(pic)
        name = Gtk.Label(css_classes=["pv-thumb-name"], ellipsize=Pango.EllipsizeMode.MIDDLE, max_width_chars=14,
                         justify=Gtk.Justification.CENTER)
        box.append(frame)
        box.append(name)
        box.pic, box.name, box.path = pic, name, None
        box.back = lambda path, tex, b=box: b.path == path and self._show(b, tex)
        item.set_child(box)

    def _show(self, box, tex) -> None:
        box.pic.set_paintable(tex)
        if tex is not None:        # the picture's own shape inside the box, so its shadow hugs it
            s = min(ROW_W / max(1, tex.get_width()), ROW_H / max(1, tex.get_height()), 1.0)
            box.pic.set_size_request(max(8, round(tex.get_width() * s)), max(8, round(tex.get_height() * s)))

    def _bind(self, _f, item) -> None:
        box = item.get_child()
        path = item.get_item().get_string()
        box.path = path
        box.name.set_label(os.path.basename(path))
        box.set_tooltip_text(os.path.basename(path))
        tex = LOADER.get(path, box.back)
        box.pic.set_paintable(None)
        box.pic.set_size_request(-1, -1)
        if tex is not None:
            self._show(box, tex)

    def _unbind(self, _f, item) -> None:
        box = item.get_child()
        if box.path:
            LOADER.forget(box.path, box.back)
        box.path = None

    # -- the list -----------------------------------------------------------------------------------
    def set_paths(self, paths, current: str = None) -> None:
        if list(paths) != self.paths:
            self.paths = list(paths)
            self._quiet = True
            self.model.splice(0, self.model.get_n_items(), self.paths)
            self._quiet = False
        self.select(current)

    def refresh(self, path: str) -> None:
        """The file changed: its row binds again (a new thumbnail)."""
        if path in self.paths:
            i = self.paths.index(path)
            self._quiet = True
            self.model.splice(i, 1, [path])
            self._quiet = False
            self.select(path)

    def select(self, path: str) -> None:
        """Selection follows the picture shown (← →); no on_pick."""
        if path not in self.paths:
            return
        i = self.paths.index(path)
        if self.selection.get_selected() != i:
            self._quiet = True
            self.selection.set_selected(i)
            self._quiet = False
        if self.get_mapped():
            self.view.scroll_to(i, Gtk.ListScrollFlags.NONE, None)

    def _picked(self, sel, _p) -> None:
        if self._quiet:
            return
        i = sel.get_selected()
        if 0 <= i < len(self.paths):
            self.on_pick(self.paths[i])
