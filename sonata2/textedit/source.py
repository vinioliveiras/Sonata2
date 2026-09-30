"""TextEdit's text views: GtkSourceView 5 when its typelib is installed
(syntax highlighting, line numbers, current line, bracket matching) with a
Sonata style scheme built from the tokens; otherwise a plain Gtk.TextView
with a line-number gutter drawn here. Nothing here fails at import.

Syntax colours are Xcode's default ("Default (Light)" / "Default (Dark)")
since the design tokens have no syntax palette yet."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gtk  # noqa: E402

from .. import ui  # noqa: E402

# Xcode's default source colours: (light, dark)
SYNTAX = {
    "keyword": ("#9b2393", "#fc5fa3"),
    "string": ("#c41a16", "#fc6a5d"),
    "comment": ("#5d6c79", "#7f8c98"),
    "number": ("#1c00cf", "#d0bf69"),
    "type": ("#0b4f79", "#5dd8ff"),
    "function": ("#326d74", "#67b7a4"),
    "preproc": ("#643820", "#fd8f3f"),
    "builtin": ("#6c36a9", "#d0a8ff"),
    "attribute": ("#815f03", "#bf8555"),
    "heading": ("#0b4f79", "#5dd8ff"),
    "link": ("#0e0eff", "#6699ff"),
    "added": ("#1e7a1e", "#7ed97e"),
    "removed": ("#c41a16", "#fc6a5d"),
}

_GS = "unset"


def gtksource():
    """The GtkSource module (5.x) or None. SONATA_TEXTEDIT_PLAIN=1 forces the
    plain views (tests)."""
    global _GS
    if _GS == "unset":
        _GS = None
        if not os.environ.get("SONATA_TEXTEDIT_PLAIN"):
            try:
                gi.require_version("GtkSource", "5")
                from gi.repository import GtkSource
                GtkSource.init()
                _GS = GtkSource
            except (ValueError, ImportError, AttributeError):
                _GS = None
    return _GS


# -- style scheme ----------------------------------------------------------------------------
def _hex(token_or_css: str, alpha: float = None) -> str:
    c = Gdk.RGBA()
    c.parse(ui.values().get(token_or_css, token_or_css))
    a = c.alpha if alpha is None else c.alpha * alpha
    return "#%02x%02x%02x%02x" % (round(c.red * 255), round(c.green * 255), round(c.blue * 255), round(a * 255))


def _scheme_xml(sid: str, dark: bool) -> str:
    i = 1 if dark else 0
    s = {k: v[i] for k, v in SYNTAX.items()}
    styles = [
        ("text", dict(foreground=_hex("label"), background=_hex("content_bg"))),
        ("selection", dict(background=_hex("accent", 0.30))),
        ("selection-unfocused", dict(background=_hex("label", 0.12))),
        ("cursor", dict(foreground=_hex("accent"))),
        ("current-line", dict(background=_hex("accent", 0.06))),
        ("current-line-number", dict(foreground=_hex("label_secondary"), background=_hex("content_bg"))),
        ("line-numbers", dict(foreground=_hex("label_tertiary"), background=_hex("content_bg"))),
        ("bracket-match", dict(background=_hex("accent", 0.22), bold="true")),
        ("bracket-mismatch", dict(background=_hex("destructive", 0.25))),
        ("search-match", dict(background=_hex("accent", 0.30))),
        ("draw-spaces", dict(foreground=_hex("label_tertiary"))),
        ("def:comment", dict(foreground=s["comment"], italic="false")),
        ("def:shebang", dict(foreground=s["comment"])),
        ("def:doc-comment-element", dict(foreground=s["comment"], bold="true")),
        ("def:string", dict(foreground=s["string"])),
        ("def:character", dict(foreground=s["string"])),
        ("def:special-char", dict(foreground=s["number"])),
        ("def:keyword", dict(foreground=s["keyword"], bold="true")),
        ("def:statement", dict(foreground=s["keyword"], bold="true")),
        ("def:number", dict(foreground=s["number"])),
        ("def:floating-point", dict(foreground=s["number"])),
        ("def:decimal", dict(foreground=s["number"])),
        ("def:base-n-integer", dict(foreground=s["number"])),
        ("def:boolean", dict(foreground=s["keyword"], bold="true")),
        ("def:constant", dict(foreground=s["number"])),
        ("def:special-constant", dict(foreground=s["keyword"], bold="true")),
        ("def:type", dict(foreground=s["type"])),
        ("def:function", dict(foreground=s["function"])),
        ("def:builtin", dict(foreground=s["builtin"])),
        ("def:preprocessor", dict(foreground=s["preproc"])),
        ("def:identifier", dict()),
        ("def:note", dict(foreground=s["comment"], bold="true")),
        ("def:error", dict(underline="error")),
        ("def:warning", dict(underline="error")),
        ("def:net-address", dict(foreground=s["link"], underline="single")),
        ("def:link-destination", dict(foreground=s["link"])),
        ("def:heading", dict(foreground=s["heading"], bold="true")),
        ("def:emphasis", dict(italic="true")),
        ("def:strong-emphasis", dict(bold="true")),
        ("def:inline-code", dict(foreground=s["string"])),
        ("def:insertion", dict(foreground=s["added"])),
        ("def:deletion", dict(foreground=s["removed"])),
        ("xml:attribute-name", dict(foreground=s["attribute"])),
        ("xml:element-name", dict(foreground=s["keyword"])),
        ("css:property-name", dict(foreground=s["attribute"])),
        ("diff:added-line", dict(foreground=s["added"])),
        ("diff:removed-line", dict(foreground=s["removed"])),
        ("diff:location", dict(foreground=s["type"])),
        ("markdown:header", dict(foreground=s["heading"], bold="true")),
    ]
    rows = "\n".join(f'  <style name="{n}" ' + " ".join(f'{k}="{v}"' for k, v in a.items()) + "/>"
                     for n, a in styles if a)
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<style-scheme id="{sid}" name="Sonata" version="1.0">\n'
            f'  <description>Sonata (generated from the design tokens)</description>\n{rows}\n</style-scheme>\n')


_schemes = {"dir": None, "buffers": []}


def _scheme_dir() -> str:
    return os.path.join(GLib.get_user_data_dir(), "sonata2", "textedit", "styles")


def scheme():
    """The Sonata scheme for the current appearance (written on first use and
    whenever light/dark or the accent changes)."""
    GS = gtksource()
    if GS is None:
        return None
    mgr = GS.StyleSchemeManager.get_default()
    if _schemes["dir"] is None:
        _schemes["dir"] = _scheme_dir()
        _write_schemes()
        mgr.append_search_path(_schemes["dir"])
        ui.on_change(_restyle)
    return mgr.get_scheme("sonata-dark" if ui.is_dark() else "sonata-light")


def _write_schemes() -> None:
    os.makedirs(_schemes["dir"], exist_ok=True)
    # the current palette is one appearance; write both from the right one
    sid = "sonata-dark" if ui.is_dark() else "sonata-light"
    with open(os.path.join(_schemes["dir"], sid + ".xml"), "w", encoding="utf-8") as f:
        f.write(_scheme_xml(sid, ui.is_dark()))
    other = "sonata-light" if ui.is_dark() else "sonata-dark"
    if not os.path.exists(os.path.join(_schemes["dir"], other + ".xml")):
        with open(os.path.join(_schemes["dir"], other + ".xml"), "w", encoding="utf-8") as f:
            f.write(_scheme_xml(other, not ui.is_dark()))


def _restyle() -> None:
    GS = gtksource()
    if GS is None or _schemes["dir"] is None:
        return
    _write_schemes()
    mgr = GS.StyleSchemeManager.get_default()
    mgr.force_rescan()
    sch = mgr.get_scheme("sonata-dark" if ui.is_dark() else "sonata-light")
    alive = []
    for ref in _schemes["buffers"]:
        buf = ref()
        if buf is not None:
            buf.set_style_scheme(sch)
            alive.append(ref)
    _schemes["buffers"] = alive


# -- views -----------------------------------------------------------------------------------
def make_view():
    """(view, buffer): a GtkSource.View when available, else a Gtk.TextView."""
    GS = gtksource()
    if GS is not None:
        import weakref
        buf = GS.Buffer()
        buf.set_style_scheme(scheme())
        buf.set_highlight_matching_brackets(True)
        _schemes["buffers"].append(weakref.ref(buf))
        view = GS.View(buffer=buf)
        view.set_tab_width(4)
        view.set_auto_indent(True)
        view.set_smart_home_end(GS.SmartHomeEndType.BEFORE)
        return view, buf
    view = Gtk.TextView()
    return view, view.get_buffer()


def is_source(view) -> bool:
    GS = gtksource()
    return GS is not None and isinstance(view, GS.View)


def guess_language(buffer, name: str, data: bytes = b""):
    """Set the buffer's language from the file name / content; returns its
    display name ("Python") or None (plain text, or no GtkSource)."""
    GS = gtksource()
    if GS is None or not isinstance(buffer, GS.Buffer):
        return None
    ctype = None
    if name or data:
        ctype, _uncertain = Gio.content_type_guess(name or None, bytes(data[:4096]) if data else None)
    lang = GS.LanguageManager.get_default().guess_language(name or None, ctype)
    buffer.set_language(lang)
    buffer.set_highlight_syntax(lang is not None)
    return lang.get_name() if lang else None


def language_name(buffer):
    GS = gtksource()
    if GS is None or not isinstance(buffer, GS.Buffer) or buffer.get_language() is None:
        return None
    return buffer.get_language().get_name()


def show_line_numbers(view, show: bool) -> None:
    if is_source(view):
        view.set_show_line_numbers(show)
        view.set_highlight_current_line(show)
        return
    gut = getattr(view, "_te_gutter", None)
    if show and gut is None:
        view._te_gutter = LineNumbers(view)
        view.set_gutter(Gtk.TextWindowType.LEFT, view._te_gutter)
    elif not show and gut is not None:
        view.set_gutter(Gtk.TextWindowType.LEFT, None)
        view._te_gutter = None


class LineNumbers(Gtk.Widget):
    """Line numbers for a plain Gtk.TextView (its LEFT gutter). Draws only the
    visible lines, redraws on scroll and edits."""

    def __init__(self, view: Gtk.TextView):
        super().__init__(css_classes=["te-gutter"])
        self.view = view
        self._digits = 0
        buf = view.get_buffer()
        self._ids = [(buf, buf.connect("changed", self._changed)),
                     (buf, buf.connect("mark-set", lambda _b, _i, m: m.get_name() == "insert" and self.queue_draw()))]
        view.connect("notify::vadjustment", lambda *_: self._watch_adj())
        self._adj = None
        self._watch_adj()

    def _watch_adj(self):
        adj = self.view.get_vadjustment()
        if adj is not None and adj is not self._adj:
            self._adj = adj
            adj.connect("value-changed", lambda *_: self.queue_draw())

    def _changed(self, buf):
        d = len(str(buf.get_line_count()))
        if d != self._digits:
            self.queue_resize()
        self.queue_draw()

    def do_measure(self, orientation, for_size):
        if orientation == Gtk.Orientation.VERTICAL:
            return 0, 0, -1, -1
        self._digits = len(str(self.view.get_buffer().get_line_count()))
        layout = self.create_pango_layout("9" * max(2, self._digits))
        w = layout.get_pixel_size()[0] + 22
        return w, w, -1, -1

    def do_snapshot(self, snap):
        view = self.view
        rect = view.get_visible_rect()
        buf = view.get_buffer()
        cur = buf.get_iter_at_mark(buf.get_insert()).get_line()
        it, _top = view.get_line_at_y(rect.y)
        width = self.get_width()
        dim, bright = ui.rgba("label_tertiary"), ui.rgba("label_secondary")
        layout = self.create_pango_layout("")
        above = view.get_pixels_above_lines()
        bottom = rect.y + rect.height
        while True:
            y, h = view.get_line_yrange(it)
            if y > bottom:
                break
            _x, wy = view.buffer_to_window_coords(Gtk.TextWindowType.LEFT, 0, y)
            n = it.get_line()
            layout.set_text(str(n + 1), -1)
            lw, lh = layout.get_pixel_size()
            first = view.get_iter_location(it).height        # the first display line of a wrapped one
            snap.save()
            snap.translate(Graphene.Point().init(width - lw - 10, wy + above + max(0, (first - lh) // 2)))
            snap.append_layout(layout, bright if n == cur else dim)
            snap.restore()
            if not it.forward_line() and it.get_line() == n:     # (the last, empty line still gets its number)
                break
