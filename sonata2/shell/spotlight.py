"""Spotlight (macOS Big Sur): Super+Space or the menu bar magnifier.

A glass search bar in the upper middle of the screen; typing lists Top Hit,
Applications, Folders and Documents, plus a calculator answer, with a
preview of the selected result on the right. Up/Down move, Return opens,
Escape closes. Resident and hidden between uses (instant opening); files
come from an index of the home folders built in the background (no
tracker/locate needed), refreshed when older than a few minutes."""
import ast
import bisect
import operator
import os
import threading
import time
from array import array

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import icons, ui  # noqa: E402
from .. import apps as _apps  # noqa: E402
from .. import launchpad_model as M  # noqa: E402


def _keywords(info) -> str:
    """Search words of an app (robust to GioUnix binding the method unbound)."""
    try:
        return " ".join(info.get_keywords() or [])
    except (AttributeError, TypeError):
        return _apps._entry_field(info, "", "Keywords").replace(";", " ")
from . import layer  # noqa: E402

WIDTH = 680
MAX_FILES = 60000
INDEX_DEPTH = 5
INDEX_TTL = 300
SHOW = {"apps": 6, "folders": 4, "docs": 8}
# the results area keeps one height while typing (it used to grow and
# shrink with every key); it slides open under the bar once, then stays
RESULTS_H = 400
FIELD_H = 64               # the search field and the line under it (.sp-field: 48 + padding)
OPEN_MS, CLOSE_MS, REVEAL_MS = 200, 120, 180
SEARCH_DELAY_MS = 40            # a burst of keys: one search

ui.register("""
window.sonata-spotlight, window.sonata-spotlight > contents { background: none; box-shadow: none; }
.sp-panel { background: %(glass_tint)s; border-radius: %(r_dialog)s; color: %(label)s; font-family: %(font)s;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 18px 50px rgba(0,0,0,0.28); }
.sp-panel.solid { background: %(menu_bg)s; }
.sp-field { min-height: 48px; padding: 0 14px; }
.sp-field image { color: %(label_secondary)s; }
.sp-field text { font-size: 22px; font-weight: 300; background: none; color: %(label)s; }
.sp-field text placeholder { color: %(label_tertiary)s; }
.sp-sep { min-height: 1px; background: %(separator)s; }
.sp-list { background: none; padding: 4px 0 6px 0; }
.sp-list row { min-height: 26px; padding: 0 12px; background: none; color: %(label)s; border-radius: 5px;
  margin: 0 6px; }
.sp-list row:selected { background: %(accent_selected)s; color: %(label_on_accent)s; }
.sp-list row.sp-head { min-height: 20px; margin-top: 6px; }
.sp-list row.sp-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_secondary)s; }
.sp-preview { padding: 22px 16px; border-left: 1px solid %(separator)s; }
.sp-prev-name { font-weight: 700; font-size: %(text_title)s; }
.sp-prev-meta { color: %(label_secondary)s; font-size: %(text_small)s; }
.sp-calc { font-size: 30px; font-weight: 300; }
/* Big Sur: the bar drops in softly and fades out; rows ease their highlight */
@keyframes sp-in { from { opacity: 0; transform: translateY(-8px) scale(0.97); }
                   to { opacity: 1; transform: none; } }
@keyframes sp-out { from { opacity: 1; } to { opacity: 0; transform: scale(0.98); } }
.sp-panel.opening { animation: sp-in %(open_ms)dms cubic-bezier(0.2, 0.9, 0.3, 1); }
.sp-panel.closing { animation: sp-out %(close_ms)dms ease-in forwards; }
.sp-list row { transition: background-color 90ms ease-out, color 90ms ease-out; }
""", key="spotlight", open_ms=OPEN_MS, close_ms=CLOSE_MS)


# -- calculator (safe: numbers and + - * / % ** only) -----------------------------------------
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos}


_MAX_BITS = 14000           # ~4200 digits: under Python's int->str limit, instant to compute


def calculate(text: str):
    expr = text.strip().replace("×", "*").replace("÷", "/").replace("^", "**").replace(",", ".")
    if not expr or not any(c.isdigit() for c in expr) or not any(c in "+-*/%" for c in expr[1:]):
        return None

    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            a, b = ev(n.left), ev(n.right)                # each side once (nested powers stay linear)
            if isinstance(n.op, ast.Pow) and (abs(b) > 100 or (
                    isinstance(a, int) and a.bit_length() * abs(b) > _MAX_BITS)):
                raise ValueError
            return _OPS[type(n.op)](a, b)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        raise ValueError
    try:
        v = ev(ast.parse(expr, mode="eval"))
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
        return None
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        v = round(v, 10)
        if v.is_integer():
            v = int(v)
    try:
        return f"{v:,}".replace(",", " ") if isinstance(v, int) else str(v)
    except ValueError:                                    # too many digits to print
        return None


def layout_for(screen_h: int) -> tuple:
    """(margin above the panel, results height) on a display this tall:
    22 % down like macOS, the results 400 px; on a short display (a small
    laptop at 2x) both give way so the results stay on screen."""
    top = int(screen_h * 0.22)
    room = screen_h - FIELD_H - 16                 # 16: a margin under the results
    results = max(160, min(RESULTS_H, room - 24))
    return max(24, min(top, room - results)), results


# -- file index ------------------------------------------------------------------------------------

class _Names:
    """One build of the index, never changed once made (the search on the
    GTK thread reads one while the next is built on another)."""
    __slots__ = ("roots", "parent", "dirs", "names", "starts", "lower", "lstarts")

    def __init__(self, roots=(), parent=None, names=(), lowers=(), dirs=None):
        self.roots, self.parent, self.dirs = list(roots), parent or array("I"), dirs or bytearray()
        # "\nname\nname...": entry i's name begins at starts[i] (after its "\n")
        self.names, self.starts = self._join(names)
        self.lower, self.lstarts = self._join(lowers)

    @staticmethod
    def _join(names):
        starts, pos = array("I"), 1
        for n in names:
            starts.append(pos)
            pos += len(n) + 1
        return ("\n" + "\n".join(names) if names else ""), starts

    def entry(self, i: int):
        end = self.starts[i + 1] - 1 if i + 1 < len(self.starts) else len(self.names)
        return os.path.join(self.roots[self.parent[i]], self.names[self.starts[i]:end]), bool(self.dirs[i])


class Index:
    """Your files by name (home, INDEX_DEPTH folders deep, MAX_FILES at most).

    Memory review: kept as 60k (name lower, path, is_dir) tuples it held
    ~15 MB for the session. Now compact (_Names): each folder's path once,
    the names as one string per case with their offsets, small arrays --
    a few MB -- and str.find over the joined names is faster than the old
    loop over tuples."""

    def __init__(self):
        self.data = _Names()
        self.built = 0.0
        self._busy = False

    def __len__(self) -> int:
        return len(self.data.dirs)

    def refresh(self):
        if self._busy or time.time() - self.built < INDEX_TTL:
            return
        self._busy = True
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        home = GLib.get_home_dir()
        roots, parent, names, lowers, dirs = [], array("I"), [], [], bytearray()
        base_depth = home.rstrip("/").count("/")

        def add(name, is_dir):
            if "\n" in name:                          # (the separator; such a name isn't searchable)
                return
            parent.append(len(roots) - 1)
            names.append(name)
            lowers.append(name.lower())
            dirs.append(is_dir)
        for root, subdirs, files in os.walk(home, onerror=lambda e: None):
            subdirs[:] = [d for d in subdirs if not d.startswith(".") and d not in ("node_modules", "__pycache__")]
            depth = root.count("/") - base_depth
            if depth >= INDEX_DEPTH:
                subdirs[:] = []
            roots.append(root)
            for d in subdirs:
                add(d, True)
            for f in files:
                if not f.startswith("."):
                    add(f, False)
            if len(dirs) > MAX_FILES:
                break
        self.data = _Names(roots, parent, names, lowers, dirs)      # one swap: never half built
        self.built = time.time()
        self._busy = False

    def search(self, q: str):
        """Names starting with q first, then names containing it, each
        shortest first (then by path) -- the 201st name starting with it
        ends the scan, in index order, as the old loop over tuples did."""
        q = q.lower()
        d = self.data
        lower, ls, n = d.lower, d.lstarts, len(d.dirs)
        if not n or "\n" in q:
            return []

        def line(pos):                                  # the entry whose name holds `pos`
            return bisect.bisect_right(ls, pos) - 1
        pre, at, last = [], 0, n - 1
        while True:
            at = lower.find("\n" + q, at)
            if at < 0:
                break
            i = line(at + 1)
            pre.append(i)
            if len(pre) > 200:
                last = i
                break
            at += 1
        prefix, sub = set(pre), []
        stop = ls[last + 1] - 1 if last + 1 < n else len(lower)
        at = 1
        while q:
            at = lower.find(q, at, stop)
            if at < 0:
                break
            i = line(at)
            if i not in prefix:
                sub.append(i)
            if i + 1 >= n:
                break
            at = ls[i + 1]                              # one hit per name is enough

        def ranked(idx):
            rows = [((ls[i + 1] if i + 1 < n else len(lower) + 1) - ls[i] - 1,) + d.entry(i) for i in idx]
            rows.sort()
            return [(p, is_dir) for _n, p, is_dir in rows]
        return ranked(pre) + ranked(sub)


class Spotlight(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Search", decorated=False, resizable=True)
        self.add_css_class("sonata-spotlight")
        self.index = Index()
        self.apps = {}
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["sp-panel"] +
                        ([] if ui.theme.glass() else ["solid"]), halign=Gtk.Align.CENTER, valign=Gtk.Align.START)
        ui.theme.glass_class(panel)
        field = Gtk.Box(spacing=10, css_classes=["sp-field"])
        field.append(Gtk.Image(icon_name="sonata-search-symbolic", pixel_size=22))
        self.entry = Gtk.Text(placeholder_text="Search", hexpand=True)
        self.entry.connect("changed", lambda *_: self._search_soon())
        self.entry.connect("activate", lambda *_: self._open_selected())
        field.append(self.entry)
        panel.append(field)
        self.sep = Gtk.Box(css_classes=["sp-sep"])
        results = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        results.append(self.sep)
        self.body = Gtk.Box()
        self.list = Gtk.ListBox(css_classes=["sp-list"], selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.set_placeholder(Gtk.Label(label="No Results", css_classes=["sp-prev-meta"], margin_top=16))
        self.list.connect("row-selected", lambda _l, r: self._preview(r))
        self.list.connect("row-activated", lambda _l, r: self._open(r))
        scroller = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_size_request(290, RESULTS_H)
        self.scroller = scroller
        self.body.append(scroller)
        self.prev = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["sp-preview"],
                            hexpand=True, valign=Gtk.Align.FILL)
        self.body.append(self.prev)
        results.append(self.body)
        self.reveal = Gtk.Revealer(child=results, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                   transition_duration=REVEAL_MS, reveal_child=False)
        panel.append(self.reveal)
        self.panel = panel
        self._search_src = 0
        self._close_src = 0
        from ..ui.fixed import FixedWidth
        # one width: a long file name in the results never widens Search
        self.set_child(FixedWidth(panel, WIDTH, halign=Gtk.Align.CENTER, valign=Gtk.Align.START))
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        click = Gtk.GestureClick()
        click.connect("released", lambda g, n, x, y: self.pick(x, y, Gtk.PickFlags.DEFAULT) in (self, None)
                      and self.close_spotlight())
        self.add_controller(click)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-spotlight")
            LS.set_layer(self, LS.Layer.OVERLAY)
            for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self, e, True)
            LS.set_exclusive_zone(self, -1)
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)
        self.connect("realize", lambda *_: self._place(panel))

    def _place(self, panel, mon=None):
        if mon is None:
            mon = self.get_display().get_monitor_at_surface(self.get_surface()) if self.get_surface() else None
        top, results = layout_for(mon.get_geometry().height if mon else 900)
        panel.set_margin_top(top)
        self.scroller.set_size_request(290, results)

    # -- open / close --------------------------------------------------------------------
    def toggle(self):
        if self.get_visible():
            self.close_spotlight()
        else:
            self.open_spotlight()

    def open_spotlight(self):
        self.index.refresh()
        from .. import config
        hidden = {h + ".desktop" for h in config.load("launchpad", {"pages": [], "hidden": []}).get("hidden", [])}
        # apps in Launchpad's Hidden folder (behind the password) aren't found here either
        self.apps = {a.get_id(): a for a in Gio.AppInfo.get_all()
                     if a.should_show() and a.get_id() and a.get_id() not in hidden}
        if self._close_src:                       # reopened while fading out
            GLib.source_remove(self._close_src)
            self._close_src = 0
        self.panel.remove_css_class("closing")
        self.entry.set_text("")
        self._search()
        self.reveal.set_reveal_child(False)
        self.panel.remove_css_class("opening")    # (again: restarts the animation)
        self.panel.add_css_class("opening")
        self.present()
        surface = self.get_surface()
        if surface is not None and getattr(self, "_mon_surface", None) is not surface:
            # the compositor puts it on the focused display: placed again for
            # that one (else a 4K's margin pushed the results off a laptop)
            self._mon_surface = surface
            surface.connect("enter-monitor", lambda _s, m: self._place(self.panel, m))
        self.entry.grab_focus()

    def close_spotlight(self):
        if not self.get_visible() or self._close_src:
            return
        self.panel.remove_css_class("opening")
        self.panel.add_css_class("closing")

        def gone():
            self._close_src = 0
            self.set_visible(False)
            self.panel.remove_css_class("closing")
            return False
        self._close_src = GLib.timeout_add(CLOSE_MS, gone)

    def _key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            if self.entry.get_text():
                self.entry.set_text("")
            else:
                self.close_spotlight()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move(1 if keyval == Gdk.KEY_Down else -1)
            return True
        return False

    def _move(self, d):
        rows, cur = [], self.list.get_selected_row()
        r = self.list.get_first_child()
        while r is not None:
            if isinstance(r, Gtk.ListBoxRow) and r.get_selectable():
                rows.append(r)
            r = r.get_next_sibling()
        if not rows:
            return
        i = rows.index(cur) if cur in rows else -1
        self.list.select_row(rows[max(0, min(len(rows) - 1, i + d))])

    # -- results -------------------------------------------------------------------------
    def _search_soon(self):
        if self._search_src:
            GLib.source_remove(self._search_src)

        def run():
            self._search_src = 0
            self._search()
            return False
        self._search_src = GLib.timeout_add(SEARCH_DELAY_MS, run)

    def _search(self):
        q = self.entry.get_text().strip()
        self.list.remove_all()
        if not q:
            self.reveal.set_reveal_child(False)
            return
        results = []            # (section, kind, payload)
        calc = calculate(q)
        if calc is not None:
            results.append(("Calculator", "calc", (q, calc)))
        meta = {i: (a.get_display_name(), " ".join(filter(None, (
                    _apps._entry_field(a, "get_generic_name", "GenericName"), a.get_description(),
                    _keywords(a)))))
                for i, a in self.apps.items()}
        app_ids = M.search(meta, q, limit=SHOW["apps"])
        found = self.index.search(q) if len(q) > 1 else []
        folders = [p for p, d in found if d][:SHOW["folders"]]
        docs = [p for p, d in found if not d][:SHOW["docs"]]
        if app_ids:
            results.append(("Top Hit", "app", app_ids[0]))
            results += [("Applications", "app", i) for i in app_ids[1:]]
        elif docs or folders:
            top = (folders or docs)[0]
            results.append(("Top Hit", "file", top))
            folders = [p for p in folders if p != top]
            docs = [p for p in docs if p != top]
        results += [("Folders", "file", p) for p in folders]
        results += [("Documents", "file", p) for p in docs]
        last = None
        first_row = None
        for section, kind, payload in results:
            if section != last:
                head = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["sp-head"])
                head.set_child(Gtk.Label(label=section, xalign=0))
                self.list.append(head)
                last = section
            row = Gtk.ListBoxRow()
            row.kind, row.payload = kind, payload
            box = Gtk.Box(spacing=8)
            img = Gtk.Image(pixel_size=20)
            box.append(img)
            box.append(Gtk.Label(label=self._title(kind, payload), xalign=0, hexpand=True,
                                 ellipsize=Pango.EllipsizeMode.MIDDLE))
            self._icon(img, kind, payload)
            row.set_child(box)
            self.list.append(row)
            first_row = first_row or row
        # once open, the results area stays (a "no results" moment while
        # typing doesn't collapse and reopen it)
        if results or not self.reveal.get_reveal_child():
            self.reveal.set_reveal_child(bool(results))
        if first_row:
            self.list.select_row(first_row)

    def _title(self, kind, payload):
        if kind == "app":
            return self.apps[payload].get_display_name()
        if kind == "calc":
            return f"{payload[0]} = {payload[1]}"
        return os.path.basename(payload)

    def _icon(self, img, kind, payload):
        if kind == "app":
            icons.set_image(img, icons.app_icon(self.apps[payload]))
        elif kind == "calc":
            img.set_from_icon_name("sonata-calculator")
        else:
            f = Gio.File.new_for_path(payload)
            try:
                info = f.query_info("standard::icon", Gio.FileQueryInfoFlags.NONE, None)
                icons.set_image(img, info.get_icon())
            except GLib.Error:
                img.set_from_icon_name("text-x-generic")

    def _preview(self, row):
        while self.prev.get_first_child():
            self.prev.remove(self.prev.get_first_child())
        if row is None or not hasattr(row, "kind"):
            return
        if row.kind == "calc":
            self.prev.append(Gtk.Label(label=row.payload[1], css_classes=["sp-calc"], wrap=True,
                                       valign=Gtk.Align.CENTER, vexpand=True))
            return
        ct = Gio.content_type_guess(row.payload, None)[0] if row.kind == "file" else ""
        if ct.startswith("image/"):               # pictures: the picture itself
            pic = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, margin_top=6)
            pic.set_filename(row.payload)
            pic.set_size_request(-1, 170)
            self.prev.append(pic)
        else:
            img = Gtk.Image(pixel_size=128, margin_top=10)
            self._icon(img, row.kind, row.payload)
            self.prev.append(img)
        self.prev.append(Gtk.Label(label=self._title(row.kind, row.payload), css_classes=["sp-prev-name"],
                                   wrap=True, justify=Gtk.Justification.CENTER))
        if row.kind == "app":
            desc = self.apps[row.payload].get_description() or ""
            self.prev.append(Gtk.Label(label=desc, css_classes=["sp-prev-meta"], wrap=True,
                                       justify=Gtk.Justification.CENTER, max_width_chars=40))
        else:
            home = GLib.get_home_dir()
            where = os.path.dirname(row.payload).replace(home, "~", 1)
            self.prev.append(Gtk.Label(label=where, css_classes=["sp-prev-meta"], wrap=True,
                                       justify=Gtk.Justification.CENTER, max_width_chars=40))

    def _open_selected(self):
        r = self.list.get_selected_row()
        if r is not None:
            self._open(r)

    def _open(self, row):
        if not hasattr(row, "kind"):
            return
        ctx = self.get_display().get_app_launch_context()
        try:
            if row.kind == "app":
                self.apps[row.payload].launch([], ctx)
            elif row.kind == "calc":
                self.get_clipboard().set(row.payload[1])          # like macOS: copy the result
            elif os.path.isdir(row.payload):
                from ..files import open_folder
                open_folder(Gio.File.new_for_path(row.payload).get_uri())
            else:
                from ..files import packages
                if not packages.open_path(row.payload):        # install / run / extract
                    Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(row.payload).get_uri(), ctx)
        except GLib.Error:
            pass
        self.close_spotlight()
