"""The note editor (macOS Notes' page): a Gtk.TextView with paragraph
styles (Title, Heading, Subheading, Body, bulleted / numbered / checklist
lines) and character styles (bold, italic, underline, strikethrough),
loaded from and saved to the Markdown-ish text of markup.py.

Paragraph styles are tags over the whole line (its newline too, so an
empty line keeps its style); list lines start with a marker: "• ", "1. "
(renumbered as lines come and go) or a check circle (a child widget).
Return continues a list (on an empty item it ends the list) and goes back
to Body after a title or heading; Backspace right after a marker removes
it. Typed text takes the character styles of the text before it, or the
ones toggled with ⌘B / ⌘I / ⌘U while nothing is selected. The creation and
edit dates sit centred above the text."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, GObject, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from . import markup  # noqa: E402

PARA = ("title", "heading", "subheading", "item")
STYLE_OF = {"title": "title", "heading": "heading", "subheading": "subheading", "body": None,
            "bullet": "item", "number": "item", "check": "item"}
MARGIN = 28           # page margin (px)
HANG = 20             # list items: wrapped lines hang under the text, not the marker

ui.register("""
textview.nt-editor, textview.nt-editor text { background: %(content_bg)s; color: %(label)s;
  font-family: %(font)s; font-size: %(text_title)s; caret-color: %(sys_orange)s; }
textview.nt-editor text selection { background: alpha(%(sys_orange)s, 0.30); color: %(label)s; }
textview.nt-editor border.top { background: %(content_bg)s; }
.nt-date { color: %(label_secondary)s; font-size: %(text_small)s; padding: 14px 0 6px 0; }
checkbutton.nt-check { padding: 0; margin: 0 6px 0 0; min-height: 0; transform: translateY(3px); }
checkbutton.nt-check check { min-width: 16px; min-height: 16px; border-radius: 99px; margin: 0; padding: 0;
  background: none; border: 1px solid %(label_tertiary)s; box-shadow: none; color: transparent;
  -gtk-icon-source: none; transition: background-color %(t_fast)s, border-color %(t_fast)s; }
checkbutton.nt-check check:checked { background: %(sys_orange)s; border-color: %(sys_orange)s;
  color: %(label_on_accent)s; -gtk-icon-source: -gtk-icontheme("object-select-symbolic"); -gtk-icon-size: 12px; }
checkbutton.nt-check:active check { filter: brightness(0.85); transition: filter %(t_press)s; }
""", key="notes-editor")


class NoteEditor(Gtk.ScrolledWindow):
    __gsignals__ = {"edited": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__(vexpand=True, hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.view = Gtk.TextView(css_classes=["nt-editor"], wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                 left_margin=MARGIN, right_margin=MARGIN, top_margin=4, bottom_margin=60,
                                 pixels_below_lines=3)
        self.set_child(self.view)
        self.buffer = self.view.get_buffer()
        self.buffer.set_enable_undo(True)
        b = self.buffer
        b.create_tag("title", weight=700, scale=1.75, pixels_below_lines=8)
        b.create_tag("heading", weight=700, scale=1.4, pixels_above_lines=10, pixels_below_lines=4)
        b.create_tag("subheading", weight=700, scale=1.15, pixels_above_lines=6, pixels_below_lines=2)
        b.create_tag("item", left_margin=MARGIN + HANG, indent=-HANG)
        b.create_tag("marker")
        b.create_tag("bold", weight=700)
        b.create_tag("italic", style=Pango.Style.ITALIC)
        b.create_tag("underline", underline=Pango.Underline.SINGLE)
        b.create_tag("strike", strikethrough=True)
        self.tags = b.get_tag_table()
        self.date = Gtk.Label(css_classes=["nt-date"], halign=Gtk.Align.CENTER)
        self.view.set_gutter(Gtk.TextWindowType.TOP, self.date)
        self.typing = set()          # character styles new text gets
        self.pending = None          # (line, style) for an empty last line (nothing to tag)
        self._guard = 0              # our own edits: no auto-styling
        self._loading = False        # loading a note: no "edited"
        self._inserting = False
        b.connect("insert-text", self._before_insert)
        b.connect_after("insert-text", self._inserted)
        b.connect("changed", lambda *_: self._loading or self.emit("edited"))
        b.connect("mark-set", self._mark_set)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.view.add_controller(keys)

    # -- load / save --------------------------------------------------------------------------
    def load(self, md: str, editable: bool = True) -> None:
        self._loading, self._guard = True, self._guard + 1
        b = self.buffer
        b.begin_irreversible_action()
        b.set_text("")
        lines = markup.parse(md or "")
        for i, (kind, checked, runs) in enumerate(lines):
            if i:
                b.insert(b.get_end_iter(), "\n")
            self._insert_marker(b.get_end_iter(), kind, checked)
            for text, tags in runs:
                b.insert_with_tags_by_name(b.get_end_iter(), text, *sorted(tags))
        for i, (kind, _c, _r) in enumerate(lines):
            self._apply_line_style(i, STYLE_OF[kind])
        self.pending = (0, "title") if not (md or "").strip() else None
        self._renumber()
        b.end_irreversible_action()
        b.place_cursor(b.get_start_iter() if self.pending else b.get_end_iter())
        b.set_modified(False)
        self.typing = set()
        self.view.set_editable(editable)
        self.view.set_cursor_visible(editable)
        self._guard -= 1
        self._loading = False

    def text(self) -> str:
        """The note as markup (markup.py)."""
        b = self.buffer
        out = []
        for line in range(b.get_line_count()):
            kind, mlen, checked = self.line_kind(line)
            s = b.get_iter_at_line(line)[1]
            s.forward_chars(mlen)
            e = s.copy()
            if not e.ends_line():
                e.forward_to_line_end()
            runs, it = [], s.copy()
            while it.compare(e) < 0:
                nxt = it.copy()
                nxt.forward_to_tag_toggle(None)
                if nxt.compare(e) > 0:
                    nxt = e.copy()
                if nxt.equal(it):
                    break
                tags = frozenset(t.props.name for t in it.get_tags() if t.props.name in markup.INLINE)
                runs.append((b.get_text(it, nxt, False), tags))
                it = nxt
            out.append((kind, checked, runs))
        return markup.serialize(out)

    def set_dates(self, text: str) -> None:
        self.date.set_label(text)

    # -- lines ----------------------------------------------------------------------------------
    def line_kind(self, line: int) -> tuple:
        """(kind, marker length, checked) of a line."""
        it = self.buffer.get_iter_at_line(line)[1]
        anchor = it.get_child_anchor()
        if anchor is not None:
            for w in anchor.get_widgets():
                if isinstance(w, Gtk.CheckButton):
                    return "check", 1, w.get_active()
        marker = self.tags.lookup("marker")
        if it.starts_tag(marker) or it.has_tag(marker):
            e = it.copy()
            e.forward_to_tag_toggle(marker)
            text = self.buffer.get_text(it, e, False)
            return ("bullet" if text.startswith("•") else "number"), e.get_offset() - it.get_offset(), False
        for t in it.get_tags():
            if t.props.name in ("title", "heading", "subheading"):
                return t.props.name, 0, False
        return "body", 0, False

    def _line_bounds(self, line: int):
        s = self.buffer.get_iter_at_line(line)[1]
        e = s.copy()
        if not e.ends_line():
            e.forward_to_line_end()
        e2 = e.copy()
        e2.forward_char()            # with the newline (nothing at the end of the buffer)
        return s, e, e2

    def _apply_line_style(self, line: int, style) -> None:
        s, _e, e2 = self._line_bounds(line)
        for name in PARA:
            self.buffer.remove_tag_by_name(name, s, e2)
        if style:
            self.buffer.apply_tag_by_name(style, s, e2)
        if s.equal(e2):
            self.pending = (line, style) if style else None
        elif self.pending and self.pending[0] == line:
            self.pending = None

    def _insert_marker(self, it, kind: str, checked: bool = False) -> None:
        b = self.buffer
        if kind == "bullet":
            b.insert_with_tags_by_name(it, "• ", "marker")
        elif kind == "number":
            b.insert_with_tags_by_name(it, "1. ", "marker")
        elif kind == "check":
            anchor = b.create_child_anchor(it)
            box = Gtk.CheckButton(css_classes=["nt-check"], active=checked, can_focus=False,
                                  sensitive=self.view.get_editable() or self._loading)
            box.connect("toggled", lambda *_: self._loading or self.emit("edited"))
            self.view.add_child_at_anchor(box, anchor)

    def set_kind(self, line: int, kind: str, checked: bool = False) -> None:
        """Give a line a paragraph style (its marker changes to match)."""
        self._guard += 1
        b = self.buffer
        _old, mlen, _c = self.line_kind(line)
        if mlen:
            s = b.get_iter_at_line(line)[1]
            e = s.copy()
            e.forward_chars(mlen)
            b.delete(s, e)
        self._insert_marker(b.get_iter_at_line(line)[1], kind, checked)
        self._apply_line_style(line, STYLE_OF[kind])
        self._guard -= 1
        if kind == "number" or _old == "number":
            self._renumber()
        self.emit("edited")

    def _renumber(self) -> None:
        b, n = self.buffer, 0
        self._guard += 1
        for line in range(b.get_line_count()):
            kind, mlen, _c = self.line_kind(line)
            if kind != "number":
                n = 0
                continue
            n += 1
            s = b.get_iter_at_line(line)[1]
            e = s.copy()
            e.forward_chars(mlen)
            want = f"{n}. "
            if b.get_text(s, e, False) != want:
                b.delete(s, e)
                b.insert_with_tags_by_name(b.get_iter_at_line(line)[1], want, "marker")
                self._apply_line_style(line, "item")
        self._guard -= 1

    # -- typing ---------------------------------------------------------------------------------
    def _before_insert(self, *_a):
        self._inserting = True

    def _inserted(self, b, end, text, _length):
        self._inserting = False
        if self._guard:
            return
        end_off = end.get_offset()
        start_off = end_off - len(text)
        s, e = b.get_iter_at_offset(start_off), b.get_iter_at_offset(end_off)
        first, last = s.get_line(), e.get_line()
        style = self._style_near(start_off, end_off, first)
        self._guard += 1
        for name in markup.INLINE + ("marker",):
            b.remove_tag_by_name(name, b.get_iter_at_offset(start_off), b.get_iter_at_offset(end_off))
        for name in self.typing:
            b.apply_tag_by_name(name, b.get_iter_at_offset(start_off), b.get_iter_at_offset(end_off))
        self._apply_line_style(first, style)
        for line in range(first + 1, last + 1):       # pasted lines: Body
            self._apply_line_style(line, "item" if self.line_kind(line)[1] else None)
        self._guard -= 1
        end.assign(b.get_iter_at_offset(end_off))    # the handler's iter stays valid

    def _style_near(self, start_off: int, end_off: int, line: int):
        b = self.buffer
        cands = []
        before = b.get_iter_at_offset(start_off)
        if before.backward_char() and before.get_line() == line:
            cands.append(before)
        after = b.get_iter_at_offset(end_off)
        if not after.is_end():
            cands.append(after)
        for it in cands:
            for t in it.get_tags():
                if t.props.name in PARA:
                    return t.props.name
        if self.pending and self.pending[0] == line:
            return self.pending[1]
        return None

    def _mark_set(self, b, it, mark):
        if mark is not b.get_insert() or self._inserting or self._guard:
            return
        prev = it.copy()
        marker = self.tags.lookup("marker")
        if prev.backward_char() and prev.get_line() == it.get_line() and not prev.has_tag(marker) \
                and prev.get_child_anchor() is None:
            src = prev
        elif not it.ends_line():
            src = it
        else:
            self.typing = set()
            return
        self.typing = {t.props.name for t in src.get_tags() if t.props.name in markup.INLINE}

    def _key(self, _c, keyval, _code, state) -> bool:
        from gi.repository import Gdk
        if not self.view.get_editable():
            return False
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.SHIFT_MASK):
            return False
        b = self.buffer
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            return self._enter()
        if keyval == Gdk.KEY_BackSpace and not b.get_has_selection():
            it = b.get_iter_at_mark(b.get_insert())
            kind, mlen, _c2 = self.line_kind(it.get_line())
            if mlen and it.get_line_offset() == mlen:
                self.set_kind(it.get_line(), "body")
                return True
        return False

    def _enter(self) -> bool:
        b = self.buffer
        if b.get_has_selection():
            b.delete_selection(True, True)
        it = b.get_iter_at_mark(b.get_insert())
        line = it.get_line()
        kind, mlen, _c = self.line_kind(line)
        if kind == "body":
            return False
        s, e, _e2 = self._line_bounds(line)
        if kind in markup.LISTS and e.get_offset() - s.get_offset() <= mlen:
            self.set_kind(line, "body")               # Return on an empty item ends the list
            return True
        self._guard += 1
        b.insert_at_cursor("\n")
        self._apply_line_style(line, STYLE_OF[kind])
        self._guard -= 1
        self.set_kind(line + 1, kind if kind in markup.LISTS else "body")
        cur = b.get_iter_at_line(line + 1)[1]
        cur.forward_chars(self.line_kind(line + 1)[1])
        b.place_cursor(cur)
        self.view.scroll_mark_onscreen(b.get_insert())
        return True

    # -- formatting commands -------------------------------------------------------------------
    def toggle_inline(self, name: str) -> None:
        b = self.buffer
        sel = b.get_selection_bounds()
        if not sel:
            self.typing ^= {name}
            return
        s, e = sel
        tag = self.tags.lookup(name)
        nxt = s.copy()
        nxt.forward_to_tag_toggle(tag)
        whole = s.has_tag(tag) and nxt.compare(e) >= 0
        (b.remove_tag if whole else b.apply_tag)(tag, s, e)
        self.emit("edited")

    def selected_lines(self) -> range:
        b = self.buffer
        sel = b.get_selection_bounds()
        if sel:
            return range(sel[0].get_line(), sel[1].get_line() + 1)
        line = b.get_iter_at_mark(b.get_insert()).get_line()
        return range(line, line + 1)

    def set_style(self, kind: str) -> None:
        """Title / Heading / Subheading / Body / a list for the selected lines
        (a list style already on every line turns them back to Body)."""
        if not self.view.get_editable():
            return
        lines = self.selected_lines()
        if kind in markup.LISTS and all(self.line_kind(n)[0] == kind for n in lines):
            kind = "body"
        for n in lines:
            self.set_kind(n, kind)
        if len(lines) == 1:
            it = self.buffer.get_iter_at_line(lines[0])[1]
            if not it.ends_line():
                it.forward_to_line_end()
            self.buffer.place_cursor(it)
        self.view.grab_focus()

    def active_styles(self) -> set:
        """Character styles at the cursor (the Aa panel shows them)."""
        b = self.buffer
        sel = b.get_selection_bounds()
        if sel:
            return {t.props.name for t in sel[0].get_tags() if t.props.name in markup.INLINE}
        return set(self.typing)


def format_date(t: float) -> str:
    """"30 September 2026 at 14:05" (macOS Notes' editor header)."""
    return GLib.DateTime.new_from_unix_local(int(t)).format("%-d %B %Y at %H:%M")
