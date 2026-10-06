"""TextEdit (macOS TextEdit, plain text, with Sublime-style extras).

A window holds documents in tabs: the tab bar (tabs.py) sits under the
glass toolbar and shows once there are two tabs. Files opened from the
system (Files, the Dock, `sonata2 textedit FILE`) or dropped on a window
become tabs of the frontmost window; a file already open comes forward.

Nothing is ever lost (macOS autosave / Sublime hot exit): every window and
tab, with the text of untitled and edited documents, cursor and scroll, is
saved about a second after each change and when a window closes
(session.py), and comes back when TextEdit starts without files -- after
quitting or a reboot. Closing a window therefore never asks; only closing
a *tab* with unsaved changes does (Don't Save / Cancel / Save…).

Files: any text file -- the encoding (UTF-8, UTF-16 with or without BOM,
Windows/ISO Latin 1) and line endings (LF / CRLF / CR) are detected and
kept on save; both show in the status bar (click to change), with the
cursor's line and column and the word / character count. .docx, .odt and
.rtf open read-only as their plain text ("Read-only (converted)"; Save
writes a .txt copy). Syntax highlighting, line numbers and bracket matching
come from GtkSourceView 5 when installed (source.py), else the text is
plain with line numbers drawn by TextEdit.

Toolbar: font (Automatic = monospaced for code, sans-serif for prose /
Sans-Serif / Serif / Monospace), text size A- / A+, wrap lines, line
numbers, find, new tab; remembered in ~/.config/sonata2/textedit.json.

Keyboard (⌘ is Ctrl or Super): N new window, T new tab, W close tab (the
window with its last tab), Ctrl+Tab / Ctrl+Shift+Tab (or ⌘Shift+] / [,
Ctrl+PageDown / PageUp) next / previous tab, ⌘1…9 a tab, O open, S save,
Shift+S save as, F find, Alt+F find & replace, G / Shift+G next /
previous match (Enter / Shift+Enter in the field, Esc closes), L go to
line, + / - / 0 text size, Z undo, Shift+Z / Y redo. Tabs: drag to reorder,
middle-click closes, right-click for Close Other Tabs / Move to New Window."""

import os
import re
import uuid

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402
from . import document, search, session, source  # noqa: E402
from .tabs import TabStrip  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.textedit"
DEFAULTS = {"font_size": 14, "wrap": True, "font": "auto", "line_numbers": False}
SIZES = (9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40, 48)
FONTS = (("auto", "Automatic"), ("sans", "Sans-Serif"), ("serif", "Serif"), ("mono", "Monospace"))
SAVE_DELAY_MS = 1000             # session autosave debounce
STATS_DELAY_MS = 300             # word count debounce
CHANGED_ON_DISK = "changed on disk"   # Document.write(): another program changed the file
MAX_STATS = 4 * 1024 * 1024      # no word count above this many characters
PROSE = (None, "Markdown", "Text", "reStructuredText", "AsciiDoc", "Plain Text")   # Automatic font: not monospaced

ui.register("""
window.sonata-textedit .te-stack { background: %(content_bg)s; }
textview.te-page, textview.te-page text { background: %(content_bg)s; color: %(label)s; caret-color: %(accent)s; }
textview.te-page.te-sans { font-family: %(font)s; }
textview.te-page.te-serif { font-family: %(te_serif)s; }
textview.te-page.te-mono { font-family: %(font_mono)s; }
textview.te-page text selection { background: alpha(%(accent)s, 0.30); color: %(label)s; }
textview.te-page border.left, textview.te-page border.left gutter { background: %(content_bg)s; }
.te-gutter { color: %(label_tertiary)s; font-family: %(font_mono)s; font-size: 0.85em; }
textview.te-page gutter { font-family: %(font_mono)s; font-size: 0.85em; }
.sonata-toolbar .te-tools { margin: 0 2px; }
.sonata-toolbar button.tool:checked { background: alpha(%(label)s, 0.14); color: %(label)s; }
.sonata-toolbar button.tool.te-font { padding: 0 6px 0 8px; font-size: %(text_body)s; font-weight: 400; }
.sonata-toolbar button.tool.te-font image { -gtk-icon-size: 10px; margin-left: 4px; }
.sonata-toolbar button.tool.te-a-small { font-size: 10px; font-weight: 600; }
.sonata-toolbar button.tool.te-a-big { font-size: 15px; font-weight: 600; }
.sonata-toolbar .te-size { font-size: %(text_small)s; color: %(label_secondary)s; min-width: 22px;
  font-feature-settings: "tnum"; }
.sonata-toolbar .te-sep { min-width: 1px; min-height: 16px; margin: 0 6px; background: %(separator)s; }
.te-find { padding: 6px 10px; background: %(window_bg)s; box-shadow: inset 0 -1px %(separator)s; }
.te-find entry { min-width: 240px; min-height: 24px; background: %(control_bg)s; color: %(label)s;
  border: none; border-radius: %(r_button)s; box-shadow: inset 0 0 0 1px %(separator)s, %(shadow_control)s; }
.te-find entry:focus-within { box-shadow: inset 0 0 0 1px %(accent)s, 0 0 0 3px alpha(%(accent)s, 0.30); }
.te-find entry text selection { background: alpha(%(accent)s, 0.30); color: %(label)s; }
.te-find entry image, .te-find entry text placeholder { color: %(label_tertiary)s; }
.te-find button.te-opt { min-height: 22px; min-width: 24px; padding: 0 6px; border-radius: %(r_button)s;
  border: none; background: none; box-shadow: none; color: %(label_secondary)s;
  font-size: %(text_small)s; font-weight: 700; font-family: %(font_mono)s; }
.te-find button.te-opt:hover { background: %(tool_hover)s; color: %(label)s; }
.te-find button.te-opt:checked { background: alpha(%(accent)s, 0.18); color: %(accent)s; text-shadow: %(accent_halo)s; -gtk-icon-shadow: %(accent_halo)s; }
.te-find button.te-nav { min-height: 24px; min-width: 26px; padding: 0 4px; border: none;
  background: %(control_bg)s; color: %(label)s; box-shadow: inset 0 0 0 1px %(separator)s, %(shadow_control)s; }
.te-find button.te-nav:active { background: %(control_pressed)s; }
.te-find button.te-nav:first-child { border-radius: %(r_button)s 0 0 %(r_button)s; }
.te-find button.te-nav:last-child { border-radius: 0 %(r_button)s %(r_button)s 0; }
.te-find label.te-count { color: %(label_secondary)s; font-size: %(text_small)s; font-feature-settings: "tnum"; }
.te-find label.te-count.error { color: %(destructive)s; }
.te-find checkbutton { font-size: %(text_small)s; color: %(label_secondary)s; }
.te-status { min-height: 22px; padding: 0 6px; background: %(window_bg)s; box-shadow: inset 0 1px %(separator)s; }
.te-status button, .te-status label { font-size: %(text_small)s; color: %(label_secondary)s; font-weight: 400;
  font-feature-settings: "tnum"; }
.te-status button { min-height: 18px; padding: 0 6px; margin: 2px 0; border-radius: %(r_menu_row)s;
  border: none; background: none; box-shadow: none; transition: background %(t_fast)s; }
.te-status button:hover { background: %(tool_hover)s; color: %(label)s; }
.te-status button:active { background: %(control_pressed)s; }
.te-status label.te-plain { padding: 0 6px; }
.te-status label.te-ro { color: %(label_on_accent)s; background: %(sys_orange)s; border-radius: %(r_menu_row)s;
  padding: 0 6px; margin: 3px 4px; font-weight: 600; }
.te-goto { padding: 10px 12px; }
.te-goto entry { min-width: 180px; }
.te-goto label { font-size: %(text_small)s; color: %(label_secondary)s; }
""", key="textedit", te_serif='"New York", "Noto Serif", "Source Serif 4", "DejaVu Serif", serif')

# process-wide state: live windows (frontmost first), windows kept in the
# session after they closed ("parked"), whether the saved session was read
_S = {"windows": [], "parked": [], "loaded": False, "timer": 0, "cfg": None, "app_hooks": set()}


def _cfg() -> dict:
    if _S["cfg"] is None:
        _S["cfg"] = config.load("textedit", DEFAULTS)
        if _S["cfg"]["font"] not in dict(FONTS):
            _S["cfg"]["font"] = "auto"
    return _S["cfg"]


def _apply_size() -> None:
    ui.register("textview.te-page { font-size: %(te_size)spx; }", key="textedit-size",
                te_size=int(_cfg()["font_size"]))


# -- documents -----------------------------------------------------------------------------------
class Document:
    """One tab: a text view in a scroller, the file it belongs to (None:
    untitled) and how that file is written (encoding, BOM, line endings)."""

    def __init__(self, win):
        self.win = win
        self.id = uuid.uuid4().hex
        self.file = None
        self.etag = None                       # the file's etag when read/saved (on-disk change check)
        self.untitled = _untitled_name()
        self.encoding, self.bom, self.newline = "utf-8", False, "LF"
        self.readonly = False                  # converted .docx/.odt/.rtf
        self.language = None
        self.gen, self.backed_gen = 0, -1      # edits, and the edit last written to the session
        self.view, self.buffer = source.make_view()
        self.view.add_css_class("te-page")
        self.view.set_left_margin(28)
        self.view.set_right_margin(28)
        self.view.set_top_margin(18)
        self.view.set_bottom_margin(40)
        self.view.set_pixels_below_lines(2)
        self.view.set_vexpand(True)
        self.buffer.set_enable_undo(True)
        self.found = self.buffer.create_tag("te-found", background_rgba=_rgba("accent", 0.28))
        self.scroll = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True)
        self.scroll.doc = self
        self.buffer.connect("changed", self._changed)
        self.buffer.connect("modified-changed", lambda *_: self.win._doc_modified(self))
        self.buffer.connect("mark-set", self._mark_set)
        self.scroll.get_vadjustment().connect("value-changed", lambda *_: schedule_save())
        self.apply_look()

    # -- state -------------------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.file.get_basename() if self.file else self.untitled

    @property
    def dirty(self) -> bool:
        """Unsaved changes worth keeping (an untitled document with text)."""
        if not self.buffer.get_modified():
            return False
        return self.file is not None or self.buffer.get_char_count() > 0

    @property
    def blank(self) -> bool:
        return self.file is None and self.buffer.get_char_count() == 0 and not self.readonly

    def text(self) -> str:
        return self.buffer.get_text(*self.buffer.get_bounds(), True)

    def _changed(self, _b) -> None:
        self.gen += 1
        self.win._doc_changed(self)

    def _mark_set(self, _b, _it, mark) -> None:
        if mark.get_name() == "insert":
            self.win._cursor_moved(self)

    def apply_look(self) -> None:
        cfg = _cfg()
        fam = cfg["font"]
        if fam == "auto":
            fam = "mono" if document.is_code(self.name) or self.language not in PROSE else "sans"
        for f in ("sans", "serif", "mono"):
            (self.view.add_css_class if f == fam else self.view.remove_css_class)("te-" + f)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if cfg["wrap"] else Gtk.WrapMode.NONE)
        source.show_line_numbers(self.view, bool(cfg["line_numbers"]))
        self.view.set_left_margin(12 if cfg["line_numbers"] else 28)

    # -- files -------------------------------------------------------------------------------
    def read(self, f: Gio.File):
        """Load a file; returns None or an error message."""
        try:
            _ok, data, etag = f.load_contents(None)
        except GLib.Error as e:
            return e.message
        name = f.get_basename() or ""
        data = bytes(data)
        readonly = False
        try:
            text = document.convert(data, name)
            if text is not None:
                dec, readonly = document.Decoded(document.normalize(text)), True
            else:
                dec = document.decode(data)
        except document.BinaryFile:
            return "It isn't a text document TextEdit can read."
        except ValueError as e:
            return f"The document is damaged ({e})."
        self.set_text(dec.text)
        self.file = f
        self.etag = etag
        self.encoding, self.bom, self.newline = dec.encoding, dec.bom, dec.newline
        self.readonly = readonly
        self.view.set_editable(not readonly)
        self.language = None if readonly else source.guess_language(self.buffer, name, data)
        self.apply_look()
        return None

    def set_text(self, text: str, modified: bool = False) -> None:
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.set_modified(modified)

    def write(self, force: bool = False):
        """Save to self.file; None or an error message (UnicodeEncodeError raised;
        CHANGED_ON_DISK when another program changed the file since it was read,
        unless force)."""
        data = document.encode(self.text(), self.encoding, self.bom, self.newline)
        try:
            _ok, self.etag = self.file.replace_contents(data, None if force else self.etag, False,
                                                        Gio.FileCreateFlags.NONE, None)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.WRONG_ETAG):
                return CHANGED_ON_DISK
            return e.message
        self.buffer.set_modified(False)
        Gtk.RecentManager.get_default().add_item(self.file.get_uri())
        return None

    # -- session -----------------------------------------------------------------------------
    def state(self) -> dict:
        """This tab for session.json (writes its text aside when unsaved)."""
        backup = self.dirty
        if backup and self.backed_gen != self.gen:
            try:
                session.write_buffer(self.id, self.text())
                self.backed_gen = self.gen
            except OSError:
                pass
        cur = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        top = getattr(self, "_restore_top", 0)
        if self.view.get_realized():
            top = self.view.get_line_at_y(self.view.get_visible_rect().y)[0].get_line()
        return {"id": self.id, "uri": self.file.get_uri() if self.file else None,
                "title": self.untitled, "backup": backup, "cursor": cur.get_offset(), "top_line": top,
                "encoding": self.encoding, "bom": self.bom, "newline": self.newline, "readonly": self.readonly}

    def restore(self, t: dict) -> bool:
        """Fill from a session tab; False when there's nothing left of it."""
        text = session.read_buffer(t.get("id")) if t.get("backup") else None
        uri = t.get("uri")
        if uri:
            f = Gio.File.new_for_uri(uri)
            err = self.read(f) if f.query_exists(None) else "missing"
            if err and text is None:
                return False
            if err:
                self.language = source.guess_language(self.buffer, f.get_basename(), b"")
            self.file = f
        elif text is None and t.get("backup"):
            return False
        if isinstance(t.get("id"), str) and re.match(r"^[0-9a-f]{8,64}$", t["id"]):
            self.id = t["id"]
        if isinstance(t.get("title"), str) and not uri:
            self.untitled = t["title"]
        if text is not None:
            self.set_text(text, modified=True)
            self.backed_gen = self.gen
            if t.get("encoding") in document.ENCODINGS:
                self.encoding, self.bom = t["encoding"], bool(t.get("bom"))
            if t.get("newline") in document.NEWLINES:
                self.newline = t["newline"]
        self._restore_top = int(t.get("top_line") or 0)
        it = self.buffer.get_iter_at_offset(int(t.get("cursor") or 0))
        self.buffer.place_cursor(it)
        top = self.buffer.get_iter_at_line(self._restore_top)
        top = top[1] if isinstance(top, tuple) else top
        mark = self.buffer.create_mark(None, top, True)
        self.view.scroll_to_mark(mark, 0, True, 0, 0)
        return True


def _untitled_name() -> str:
    used = {d.untitled for w in _S["windows"] for d in w.docs if d.file is None}
    for p in _S["parked"]:
        used |= {t.get("title") for t in p.get("tabs", []) if not t.get("uri")}
    n = 1
    while ("Untitled" if n == 1 else f"Untitled {n}") in used:
        n += 1
    return "Untitled" if n == 1 else f"Untitled {n}"


def _rgba(token, alpha=1.0):
    c = ui.theme.rgba(token).copy()
    c.alpha = alpha
    return c


class _Bar:
    """The old title bar's label (tests, scripts): the window title."""

    def __init__(self):
        self.title_label = Gtk.Label()


# -- windows -----------------------------------------------------------------------------------
class TextEditWindow(Gtk.ApplicationWindow):
    def __init__(self, app, path: str = None, restore: dict = None):
        if not GLib.get_application_name():
            GLib.set_application_name("TextEdit")          # Recent documents need it
        super().__init__(application=app, title="Untitled", css_classes=["sonata-textedit"])
        ui.window.standard(self)
        _apply_size()
        ui.window.remember_size(self, "textedit", 760, 580)     # a session window: its own size (_restore)
        self.docs, self.doc = [], None
        self._closing = False
        self._stats_id = self._refind_id = 0
        self.bar = _Bar()
        _hook_app(app)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self._toolbar())
        self.tabs = TabStrip(self.select, lambda d: self.close_tab(d), lambda: self.new_tab(),
                             self._reordered, self._tab_menu)
        self.tabs_rev = Gtk.Revealer(child=self.tabs, reveal_child=False, transition_duration=150,
                                     transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        col.append(self.tabs_rev)
        ui.window.follow_tab_bar(self, self._show_tabs)
        self.find_bar = self._find_bar()
        col.append(self.find_bar)
        self.stack = Gtk.Stack(vexpand=True, hexpand=True, css_classes=["te-stack"])
        col.append(self.stack)
        col.append(self._status_bar())
        self.set_child(col)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._file_drops()
        self.connect("close-request", self._close_request)
        self.connect("notify::is-active", self._activated)
        _S["windows"].insert(0, self)
        if restore:
            self._restore(restore)
        if not self.docs:
            self.new_tab()
            if path:
                self.open_file(Gio.File.new_for_commandline_arg(path))

    # compatibility: the current document's pieces
    @property
    def buffer(self):
        return self.doc.buffer

    @property
    def view(self):
        return self.doc.view

    @property
    def file(self):
        return self.doc.file if self.doc else None

    # -- chrome ------------------------------------------------------------------------------
    def _toolbar(self):
        handle = ui.window.glass_toolbar(self, end=(
            ("edit-find-symbolic", "Find (⌘F)", lambda: self._show_find()),
            ("tab-new-symbolic", "New Tab (⌘T)", lambda: self.new_tab())))
        bar = handle.get_child()
        start = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER, css_classes=["te-tools"])
        self.font_btn = Gtk.Button(css_classes=["tool", "te-font"], can_focus=False, tooltip_text="Font")
        fb = Gtk.Box()
        self.font_label = Gtk.Label()
        fb.append(self.font_label)
        fb.append(Gtk.Image(icon_name="pan-down-symbolic"))
        self.font_btn.set_child(fb)
        self.font_btn.connect("clicked", lambda b: self._font_menu(b))
        start.append(self.font_btn)
        start.append(Gtk.Box(css_classes=["te-sep"], valign=Gtk.Align.CENTER))
        smaller = Gtk.Button(label="A", css_classes=["tool", "te-a-small"], can_focus=False,
                             tooltip_text="Smaller (⌘−)")
        smaller.connect("clicked", lambda *_: self._zoom(-1))
        self.size_label = Gtk.Label(css_classes=["te-size"])
        bigger = Gtk.Button(label="A", css_classes=["tool", "te-a-big"], can_focus=False, tooltip_text="Bigger (⌘+)")
        bigger.connect("clicked", lambda *_: self._zoom(1))
        for w in (smaller, self.size_label, bigger):
            start.append(w)
        start.append(Gtk.Box(css_classes=["te-sep"], valign=Gtk.Align.CENTER))
        self.wrap_btn = Gtk.ToggleButton(icon_name="view-wrapped-symbolic", css_classes=["tool"], can_focus=False,
                                         tooltip_text="Wrap Lines")
        self.lines_btn = Gtk.ToggleButton(icon_name="view-list-ordered-symbolic", css_classes=["tool"],
                                          can_focus=False, tooltip_text="Line Numbers")
        self.wrap_btn.connect("toggled", lambda b: self._set_option("wrap", b.get_active()))
        self.lines_btn.connect("toggled", lambda b: self._set_option("line_numbers", b.get_active()))
        start.append(self.wrap_btn)
        start.append(self.lines_btn)
        bar.set_start_widget(start)
        self._sync_tools()
        return handle

    def _sync_tools(self) -> None:
        cfg = _cfg()
        self.font_label.set_label(dict(FONTS)[cfg["font"]])
        self.size_label.set_label(str(cfg["font_size"]))
        self._syncing = True
        self.wrap_btn.set_active(bool(cfg["wrap"]))
        self.lines_btn.set_active(bool(cfg["line_numbers"]))
        self._syncing = False

    def _status_bar(self):
        bar = Gtk.Box(css_classes=["te-status"])
        self.pos_btn = Gtk.Button(can_focus=False, tooltip_text="Go to Line (⌘L)")
        self.pos_btn.connect("clicked", lambda *_: self._goto_line())
        bar.append(self.pos_btn)
        self.ro_label = Gtk.Label(label="Read-only (converted)", css_classes=["te-ro"], visible=False,
                                  tooltip_text="Converted to plain text: Save writes a .txt copy")
        bar.append(self.ro_label)
        bar.append(Gtk.Box(hexpand=True))
        self.lang_label = Gtk.Label(css_classes=["te-plain"])
        self.count_label = Gtk.Label(css_classes=["te-plain"])
        self.enc_btn = Gtk.Button(can_focus=False, tooltip_text="Text Encoding")
        self.enc_btn.connect("clicked", lambda b: self._encoding_menu(b))
        self.nl_btn = Gtk.Button(can_focus=False, tooltip_text="Line Endings")
        self.nl_btn.connect("clicked", lambda b: self._newline_menu(b))
        for w in (self.lang_label, self.count_label, self.enc_btn, self.nl_btn):
            bar.append(w)
        return bar

    # -- tabs --------------------------------------------------------------------------------
    def new_tab(self, select: bool = True, index: int = None) -> Document:
        doc = Document(self)
        self._adopt(doc, index)
        if select:
            self.select(doc)
        schedule_save()
        return doc

    def _adopt(self, doc: Document, index: int = None) -> None:
        doc.win = self
        if index is None and self.doc in self.docs:
            index = self.docs.index(self.doc) + 1       # new tabs open next to the current one
        index = len(self.docs) if index is None else index
        self.docs.insert(index, doc)
        self.stack.add_child(doc.scroll)
        self.tabs.add(doc, index)
        self._update_tab(doc)
        self._show_tabs()

    def _show_tabs(self) -> None:
        self.tabs_rev.set_reveal_child(ui.window.tab_bar_shown(len(self.docs)))

    def _detach(self, doc: Document) -> None:
        i = self.docs.index(doc)
        self.docs.remove(doc)
        self.tabs.remove(doc)
        self.stack.remove(doc.scroll)
        self._show_tabs()
        if self.doc is doc:
            self.doc = None
            if self.docs:
                self.select(self.docs[min(i, len(self.docs) - 1)])

    def select(self, doc: Document) -> None:
        if doc not in self.docs:
            return
        self.doc = doc
        self.stack.set_visible_child(doc.scroll)
        self.tabs.select(doc)
        self._update_title()
        self._update_status(full=True)
        if self.find_bar.get_reveal_child():
            self._find(0, move=False)
        if not self.find_entry.has_focus():
            doc.view.grab_focus()
        schedule_save()

    def cycle(self, step: int) -> None:
        if len(self.docs) > 1 and self.doc in self.docs:
            self.select(self.docs[(self.docs.index(self.doc) + step) % len(self.docs)])

    def close_tab(self, doc: Document = None, then=None) -> None:
        """Close a tab (asking first when it has unsaved changes); the window
        goes with its last tab."""
        doc = doc or self.doc
        if doc is None:
            return
        if not doc.dirty:
            self._remove_tab(doc)
            if then:
                then()
            return
        self.select(doc)

        def answer(rid):
            if rid == "save":
                self.save(doc, lambda: (self._remove_tab(doc), then and then()))
            elif rid == "discard":
                self._remove_tab(doc)
                if then:
                    then()
        ui.dialog.alert(f"Do you want to save the changes made to the document “{doc.name}”?",
                        "Your changes will be lost if you don't save them.",
                        [("discard", "Don't Save", "destructive"), ("cancel", "Cancel", ""),
                         ("save", "Save…" if doc.file is None or doc.readonly else "Save", "default")],
                        answer, parent=self)

    def _remove_tab(self, doc: Document) -> None:
        if doc not in self.docs:
            return
        self._detach(doc)
        doc.buffer.set_modified(False)          # its text is dropped from the session
        if not self.docs:
            self.close()
        schedule_save()

    def _reordered(self, order) -> None:
        self.docs = [d for d in order if d in self.docs]
        schedule_save()

    def _tab_menu(self, doc, widget, x, y) -> None:
        others = [d for d in self.docs if d is not doc]
        ui.menu.popup(widget, [
            [ui.menu.Item("Close Tab", lambda: self.close_tab(doc)),
             ui.menu.Item("Close Other Tabs", lambda: self._close_many(others), enabled=bool(others))],
            [ui.menu.Item("Move Tab to New Window", lambda: self.move_to_new_window(doc), enabled=bool(others))],
        ], at=(x, y), glass=True, passthrough=True)

    def _close_many(self, docs) -> None:
        if docs:
            self.close_tab(docs[0], lambda: self._close_many(docs[1:]))

    def move_to_new_window(self, doc: Document) -> None:
        if len(self.docs) < 2:
            return
        self._detach(doc)
        w = TextEditWindow(self.get_application(), restore={"tabs": []})
        blank = w.docs[0] if w.docs else None
        w._adopt(doc)
        w.select(doc)
        if blank is not None:
            w._detach(blank)
        w.present()
        schedule_save()

    # -- per-document updates ----------------------------------------------------------------
    def _update_tab(self, doc: Document) -> None:
        tab = self.tabs.tab_of(doc)
        if tab is not None:
            tip = doc.file.get_path() or doc.file.get_uri() if doc.file else doc.name
            tab.set_title(doc.name, doc.dirty, tip)

    def _doc_modified(self, doc: Document) -> None:
        self._update_tab(doc)
        if doc is self.doc:
            self._update_title()
        schedule_save()

    def _doc_changed(self, doc: Document) -> None:
        if doc.file is None:                     # an untitled tab's dot follows its text
            self._update_tab(doc)
        if doc is self.doc:
            self._update_status()
            if self.find_bar.get_reveal_child() and not self._replacing and not self._refind_id:
                self._refind_id = GLib.timeout_add(150, self._refind)
        schedule_save()

    def _refind(self) -> bool:
        self._refind_id = 0
        if self.doc is not None and self.find_bar.get_reveal_child():
            self._find(0, move=False)
        return False

    def _cursor_moved(self, doc: Document) -> None:
        if doc is self.doc:
            it = doc.buffer.get_iter_at_mark(doc.buffer.get_insert())
            self.pos_btn.set_label(f"Line {it.get_line() + 1}, Column {it.get_line_offset() + 1}")
            schedule_save()

    def _update_title(self) -> None:
        doc = self.doc
        if doc is None:
            return
        title = doc.name + (" — Edited" if doc.dirty else "")
        self.set_title(title)
        self.bar.title_label.set_label(title)

    def _update_status(self, full: bool = False) -> None:
        doc = self.doc
        if doc is None:
            return
        self._cursor_moved(doc)
        if full:
            self.enc_btn.set_label(document.label(doc.encoding, doc.bom))
            self.nl_btn.set_label(doc.newline)
            self.ro_label.set_visible(doc.readonly)
            self.lang_label.set_label(doc.language or "")
            self.lang_label.set_visible(bool(doc.language))
            self.enc_btn.set_sensitive(not doc.readonly)
            self.nl_btn.set_sensitive(not doc.readonly)
        if self._stats_id:
            GLib.source_remove(self._stats_id)
        self._stats_id = GLib.timeout_add(1 if full else STATS_DELAY_MS, self._stats)

    def _stats(self) -> bool:
        self._stats_id = 0
        doc = self.doc
        if doc is None:
            return False
        n = doc.buffer.get_char_count()
        chars = f"{n:,} character{'s' if n != 1 else ''}"
        if n <= MAX_STATS:
            w = document.words(doc.text())
            self.count_label.set_label(f"{w:,} word{'s' if w != 1 else ''}, {chars}")
        else:
            self.count_label.set_label(chars)
        return False

    # -- files -------------------------------------------------------------------------------
    def open_file(self, f: Gio.File) -> bool:
        """Open a file as a tab here (an empty Untitled tab takes it); a file
        already open anywhere comes forward instead."""
        found = _find_open(f)
        if found:
            w, d = found
            w.select(d)
            w.present()
            return True
        doc = self.doc if self.doc is not None and self.doc.blank and not self.doc.buffer.get_modified() \
            else self.new_tab()
        err = doc.read(f)
        if err:
            if len(self.docs) > 1 and doc.blank:
                self._remove_tab(doc)
            self._tell(f"“{f.get_basename()}” couldn't be opened.", err)
            return False
        Gtk.RecentManager.get_default().add_item(f.get_uri())
        self._update_tab(doc)
        self.select(doc)
        return True

    def load(self, f: Gio.File) -> None:
        """Compatibility: open a file in this window."""
        self.open_file(f)

    def _tell(self, heading: str, body: str) -> None:
        def show():
            ui.dialog.alert(heading, body, [("ok", "OK", "default")], parent=self)
            return False
        if self.get_mapped():
            show()
        else:                      # an alert on a window not on screen yet never appears
            GLib.timeout_add(150, show)

    def save(self, doc: Document = None, then=None, force: bool = False) -> None:
        doc = doc or self.doc
        if doc.file is None or doc.readonly:
            self.save_as(doc, then)
            return
        try:
            err = doc.write(force)
        except UnicodeEncodeError:
            def answer(rid):
                if rid == "utf8":
                    doc.encoding, doc.bom = "utf-8", False
                    self._update_status(full=True)
                    self.save(doc, then)
            ui.dialog.alert(f"“{doc.name}” can't be saved using {document.label(doc.encoding)}.",
                            "Some characters can't be written in that encoding. Save it as Unicode (UTF-8)?",
                            [("cancel", "Cancel", ""), ("utf8", "Save as UTF-8", "default")], answer, parent=self)
            return
        if err is CHANGED_ON_DISK:
            def overwrite(rid):
                if rid == "overwrite":
                    self.save(doc, then, force=True)
            ui.dialog.alert(f"“{doc.name}” has been changed by another application.",
                            "Saving now replaces those changes with this document.",
                            [("cancel", "Cancel", "default"), ("overwrite", "Save Anyway", "destructive")],
                            overwrite, parent=self)
            return
        if err:
            self._tell(f"“{doc.name}” couldn't be saved.", err)
            return
        self._update_tab(doc)
        schedule_save()
        if then:
            then()

    def save_as(self, doc: Document = None, then=None) -> None:
        from ..files.chooser import ChooserWindow
        doc = doc or self.doc
        folder = doc.file.get_parent().get_uri() if doc.file and doc.file.get_parent() else \
            Gio.File.new_for_path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
                                  or GLib.get_home_dir()).get_uri()
        name = doc.file.get_basename() if doc.file else doc.untitled + ".txt"
        if doc.readonly:
            name = os.path.splitext(name)[0] + ".txt"

        def done(uris, _i):
            if uris:
                f = Gio.File.new_for_uri(uris[0])
                if doc.readonly:                  # the converted text becomes an ordinary document
                    doc.readonly = False
                    doc.view.set_editable(True)
                    doc.encoding, doc.bom, doc.newline = "utf-8", False, "LF"
                if doc.file is None or not doc.file.equal(f):
                    doc.etag = None               # another file: the chooser already asked to replace it
                doc.file = f
                doc.language = source.guess_language(doc.buffer, f.get_basename(), b"")
                doc.apply_look()
                self._update_status(full=True)
                self._update_title()
                self.save(doc, then)
        dlg = ChooserWindow(self.get_application(), mode="save", title="Save", folder=folder, name=name,
                            filters=[("Plain Text", [(0, "*.txt")]), ("All Files", [(0, "*")])],
                            on_done=done)
        dlg.set_transient_for(self)
        dlg.set_modal(True)
        dlg.present()

    def open_panel(self) -> None:
        from ..files.chooser import ChooserWindow

        def done(uris, _i):
            for uri in uris or []:
                self.open_file(Gio.File.new_for_uri(uri))
        dlg = ChooserWindow(self.get_application(), mode="open", title="Open", multiple=True,
                            filters=[("Text Documents", [(1, "text/*")]), ("All Files", [(0, "*")])],
                            on_done=done)
        dlg.set_transient_for(self)
        dlg.present()

    def _file_drops(self) -> None:
        """Files dropped anywhere on the window open as tabs (text drags
        still go to the text)."""
        tgt = Gtk.DropTargetAsync.new(Gdk.ContentFormats.new(["text/uri-list"]), Gdk.DragAction.COPY)
        tgt.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        tgt.connect("accept", lambda _t, drop: drop.get_formats().contain_mime_type("text/uri-list"))
        tgt.connect("drag-enter", lambda *_: Gdk.DragAction.COPY)
        tgt.connect("drag-motion", lambda *_: Gdk.DragAction.COPY)
        tgt.connect("drop", self._drop)
        self.add_controller(tgt)

    def _drop(self, _t, drop, _x, _y) -> bool:
        def got_stream(d, res):
            try:
                stream, _mime = d.read_finish(res)
            except GLib.Error:
                d.finish(0)
                return
            chunks = []

            def got(st, r):
                try:
                    b = st.read_bytes_finish(r)
                except GLib.Error:
                    b = None
                if b is not None and b.get_size():
                    chunks.append(b.get_data())
                    st.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, None, got)
                    return
                d.finish(Gdk.DragAction.COPY)
                uris = [ln.strip() for ln in b"".join(chunks).decode("utf-8", "replace").splitlines()
                        if ln.strip() and not ln.startswith("#")]
                for u in uris:
                    self.open_file(Gio.File.new_for_uri(u))
            stream.read_bytes_async(65536, GLib.PRIORITY_DEFAULT, None, got)
        drop.read_async(["text/uri-list"], GLib.PRIORITY_DEFAULT, None, got_stream)
        return True

    # -- status bar menus --------------------------------------------------------------------
    def _encoding_menu(self, btn) -> None:
        doc = self.doc

        def use(enc):
            doc.encoding, doc.bom = enc, doc.bom and enc != "iso-8859-1" and enc != "windows-1252"
            doc.buffer.set_modified(True)
            self._update_status(full=True)

        def reopen(enc):
            try:
                _ok, data, etag = doc.file.load_contents(None)
            except GLib.Error as e:
                self._tell(f"“{doc.name}” couldn't be reopened.", e.message)
                return
            doc.etag = etag
            text = bytes(data).decode(enc, errors="replace")
            doc.set_text(document.normalize(text))
            doc.encoding, doc.bom, doc.newline = enc, False, document.detect_newline(text)
            self._update_status(full=True)
            self._update_title()
        save_items = [ui.menu.Item(lbl, (lambda _on, e=e: use(e)), checked=doc.encoding == e)
                      for e, lbl in document.ENCODINGS.items()]
        reopen_items = [ui.menu.Item(lbl, (lambda e=e: reopen(e)))
                        for e, lbl in document.ENCODINGS.items()]
        ui.menu.popup(btn, [save_items,
                            [ui.menu.Item("Reopen with Encoding", submenu=[reopen_items],
                                          enabled=doc.file is not None and not doc.dirty)]], glass=True)

    def _newline_menu(self, btn) -> None:
        doc = self.doc

        def use(nl):
            if nl != doc.newline:
                doc.newline = nl
                doc.buffer.set_modified(True)
                self._update_status(full=True)
        ui.menu.popup(btn, [[ui.menu.Item(lbl, (lambda _on, n=n: use(n)), checked=doc.newline == n)
                             for n, lbl in document.NEWLINE_LABELS.items()]], glass=True)

    def _font_menu(self, btn) -> None:
        cur = _cfg()["font"]
        ui.menu.popup(btn, [[ui.menu.Item(lbl, (lambda _on, k=k: self._set_option("font", k)), checked=cur == k)
                             for k, lbl in FONTS[:1]],
                            [ui.menu.Item(lbl, (lambda _on, k=k: self._set_option("font", k)), checked=cur == k)
                             for k, lbl in FONTS[1:]]], position=Gtk.PositionType.BOTTOM, glass=True)

    def _goto_line(self) -> None:
        doc = self.doc
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["te-goto"])
        entry = Gtk.Entry(placeholder_text=f"Line (1–{doc.buffer.get_line_count()}), or line:column")
        box.append(Gtk.Label(label="Go to Line", xalign=0))
        box.append(entry)
        pop = ui.panel.popup(self.pos_btn, box, position=Gtk.PositionType.TOP)

        def go(*_a):
            m = re.match(r"^\s*(\d+)\s*(?:[:,]\s*(\d+))?\s*$", entry.get_text())
            if m:
                self.go_to(int(m.group(1)), int(m.group(2) or 1))
            pop.popdown()
        entry.connect("activate", go)
        entry.grab_focus()

    def go_to(self, line: int, column: int = 1) -> None:
        buf = self.doc.buffer
        line = max(1, min(line, buf.get_line_count())) - 1
        it = buf.get_iter_at_line(line)
        it = it[1] if isinstance(it, tuple) else it
        end = it.copy()
        if not end.ends_line():
            end.forward_to_line_end()
        it.set_line_offset(min(max(0, column - 1), end.get_line_offset()))
        buf.place_cursor(it)
        self.doc.view.scroll_to_iter(it, 0.2, True, 0, 0.3)
        self.doc.view.grab_focus()

    # -- look --------------------------------------------------------------------------------
    def _set_option(self, key, value) -> None:
        if getattr(self, "_syncing", False) or _cfg().get(key) == value:
            return
        _cfg()[key] = value
        config.update("textedit", **{key: value})
        _apply_look_everywhere()

    def _zoom(self, step: int) -> None:
        cur = _cfg()["font_size"]
        if step == 0:
            size = DEFAULTS["font_size"]
        else:
            bigger = [s for s in SIZES if s > cur]
            smaller = [s for s in SIZES if s < cur]
            size = (bigger[0] if bigger else cur) if step > 0 else (smaller[-1] if smaller else cur)
        self._set_option("font_size", size)

    # -- find & replace ----------------------------------------------------------------------
    def _find_bar(self) -> Gtk.Revealer:
        self._replacing = False
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["te-find"])
        row = Gtk.Box(spacing=6)
        self.find_entry = Gtk.SearchEntry(placeholder_text="Find", hexpand=False)
        self.find_entry.connect("search-changed", lambda *_: self._find(0))
        self.find_entry.connect("activate", lambda *_: self._find(1))
        self.find_entry.connect("stop-search", lambda *_: self._hide_find())
        self.case_btn = Gtk.ToggleButton(label="Aa", css_classes=["te-opt"], can_focus=False,
                                         tooltip_text="Match Case")
        self.regex_btn = Gtk.ToggleButton(label=".*", css_classes=["te-opt"], can_focus=False,
                                          tooltip_text="Regular Expression")
        self.word_btn = Gtk.ToggleButton(label="W", css_classes=["te-opt"], can_focus=False,
                                         tooltip_text="Whole Words")
        for b in (self.case_btn, self.regex_btn, self.word_btn):
            b.connect("toggled", lambda *_: self._find(0, move=False))
        nav = Gtk.Box(css_classes=["linked"])
        prev = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["te-nav"], tooltip_text="Previous (⇧⌘G)")
        prev.connect("clicked", lambda *_: self._find(-1))
        nxt = Gtk.Button(icon_name="go-next-symbolic", css_classes=["te-nav"], tooltip_text="Next (⌘G)")
        nxt.connect("clicked", lambda *_: self._find(1))
        nav.append(prev)
        nav.append(nxt)
        self.find_count = Gtk.Label(css_classes=["te-count"])
        self.replace_check = Gtk.CheckButton(label="Replace")
        self.replace_check.connect("toggled", lambda b: self.replace_rev.set_reveal_child(b.get_active()))
        done = ui.controls.push_button("Done", self._hide_find)
        for w in (self.find_entry, self.case_btn, self.word_btn, self.regex_btn, nav, self.find_count,
                  Gtk.Box(hexpand=True), self.replace_check, done):
            row.append(w)
        col.append(row)
        rrow = Gtk.Box(spacing=6)
        self.replace_entry = Gtk.Entry(placeholder_text="Replace", width_chars=24)
        self.replace_entry.connect("activate", lambda *_: self.replace_one())
        rrow.append(self.replace_entry)
        rrow.append(ui.controls.push_button("Replace", self.replace_one))
        rrow.append(ui.controls.push_button("Replace All", self.replace_all))
        self.replace_rev = Gtk.Revealer(child=rrow, reveal_child=False, transition_duration=120,
                                        transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        col.append(self.replace_rev)
        return Gtk.Revealer(child=col, reveal_child=False,
                            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=180)

    def _show_find(self, replace: bool = False) -> None:
        self.find_bar.set_reveal_child(True)
        if replace:
            self.replace_check.set_active(True)
        sel = self.buffer.get_selection_bounds()
        if sel:
            text = self.buffer.get_text(sel[0], sel[1], False)
            if "\n" not in text:
                self.find_entry.set_text(text[:200])
        self.find_entry.grab_focus()
        self.find_entry.select_region(0, -1)
        if self.find_entry.get_text():
            self._find(0, move=False)

    def _hide_find(self) -> None:
        for d in self.docs:
            d.buffer.remove_tag(d.found, *d.buffer.get_bounds())
        self.find_bar.set_reveal_child(False)
        if self.doc:
            self.doc.view.grab_focus()

    def _opts(self) -> dict:
        return {"case": self.case_btn.get_active(), "regex": self.regex_btn.get_active(),
                "whole": self.word_btn.get_active()}

    def _spans(self):
        """Every match in the current document (None: invalid pattern)."""
        needle = self.find_entry.get_text()
        self.find_count.remove_css_class("error")
        try:
            return search.find_all(self.doc.text(), needle, **self._opts())
        except re.error:
            self.find_count.add_css_class("error")
            self.find_count.set_label("Invalid pattern")
            return None

    def _find(self, direction: int, move: bool = True) -> None:
        """Highlight every match and select the next (1), previous (-1) or the
        first from the cursor (0). move=False only refreshes the highlight."""
        doc = self.doc
        buf = doc.buffer
        buf.remove_tag(doc.found, *buf.get_bounds())
        spans = self._spans()
        if spans is None:
            return
        if not self.find_entry.get_text():
            self.find_count.set_label("")
            return
        for a, b in spans:
            buf.apply_tag(doc.found, buf.get_iter_at_offset(a), buf.get_iter_at_offset(b))
        n = len(spans)
        more = "+" if n >= search.MAX_MATCHES else ""
        self.find_count.set_label(f"{n}{more} found" if n else "Not found")
        if not n or not move:
            return
        sel = buf.get_selection_bounds()
        cur = buf.get_iter_at_mark(buf.get_insert()).get_offset()
        if direction < 0:
            span = search.next_span(spans, sel[0].get_offset() if sel else cur, backwards=True)
        else:
            anchor = sel[0].get_offset() if sel else cur
            span = search.next_span(spans, anchor, include_current=direction == 0 or not sel)
        a, b = buf.get_iter_at_offset(span[0]), buf.get_iter_at_offset(span[1])
        buf.select_range(a, b)
        doc.view.scroll_to_iter(a, 0.2, False, 0, 0)

    def replace_one(self) -> None:
        """Replace the selected match, then select the next one."""
        doc = self.doc
        if doc.readonly:
            return
        buf = doc.buffer
        spans = self._spans()
        if not spans:
            self._find(1)
            return
        sel = buf.get_selection_bounds()
        cur = (sel[0].get_offset(), sel[1].get_offset()) if sel else None
        if cur in spans:
            text = doc.text()
            repl = search.expand(text, cur, self.find_entry.get_text(), self.replace_entry.get_text(), **self._opts())
            self._replacing = True
            buf.begin_user_action()
            a, b = buf.get_iter_at_offset(cur[0]), buf.get_iter_at_offset(cur[1])
            buf.delete(a, b)
            buf.insert(a, repl)
            buf.end_user_action()
            self._replacing = False
        self._find(1)

    def replace_all(self) -> int:
        doc = self.doc
        if doc.readonly:
            return 0
        buf = doc.buffer
        needle, repl = self.find_entry.get_text(), self.replace_entry.get_text()
        try:
            text = doc.text()
            spans = search.find_all(text, needle, limit=10 ** 9, **self._opts())
        except re.error:
            self._spans()
            return 0
        if not spans:
            self.find_count.set_label("Not found")
            return 0
        opts = self._opts()
        news = [search.expand(text, s, needle, repl, **opts) for s in spans] if opts["regex"] else None
        self._replacing = True
        buf.begin_user_action()
        for i in range(len(spans) - 1, -1, -1):
            a, b = buf.get_iter_at_offset(spans[i][0]), buf.get_iter_at_offset(spans[i][1])
            buf.delete(a, b)
            buf.insert(a, news[i] if news else repl)
        buf.end_user_action()
        self._replacing = False
        self._find(0, move=False)
        self.find_count.set_label(f"{len(spans)} replaced")
        return len(spans)

    # -- keys --------------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        shift = state & Gdk.ModifierType.SHIFT_MASK
        alt = state & Gdk.ModifierType.ALT_MASK
        k = Gdk.keyval_to_lower(keyval)
        if ctrl and keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab, Gdk.KEY_KP_Tab):
            self.cycle(-1 if shift or keyval == Gdk.KEY_ISO_Left_Tab else 1)
            return True
        if ctrl and keyval in (Gdk.KEY_Page_Down, Gdk.KEY_Page_Up):
            self.cycle(1 if keyval == Gdk.KEY_Page_Down else -1)
            return True
        if not cmd:
            if keyval == Gdk.KEY_Escape and self.find_bar.get_reveal_child():
                self._hide_find()
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and shift and self.find_entry.has_focus():
                self._find(-1)
                return True
            return False
        if shift and k in (Gdk.KEY_bracketright, Gdk.KEY_braceright, Gdk.KEY_bracketleft, Gdk.KEY_braceleft):
            self.cycle(1 if k in (Gdk.KEY_bracketright, Gdk.KEY_braceright) else -1)
            return True
        if Gdk.KEY_1 <= keyval <= Gdk.KEY_9 and not shift:
            i = keyval - Gdk.KEY_1
            if self.docs:
                self.select(self.docs[-1] if keyval == Gdk.KEY_9 else self.docs[min(i, len(self.docs) - 1)])
            return True
        app = self.get_application()
        actions = {
            Gdk.KEY_n: lambda: TextEditWindow(app).present(),
            Gdk.KEY_t: lambda: self.new_tab(),
            Gdk.KEY_o: self.open_panel,
            Gdk.KEY_s: (lambda: self.save_as()) if shift else (lambda: self.save()),
            Gdk.KEY_w: lambda: self.close_tab(),
            Gdk.KEY_f: lambda: self._show_find(replace=bool(alt)),
            Gdk.KEY_g: lambda: self._find(-1 if shift else 1) if self.find_entry.get_text() else self._show_find(),
            Gdk.KEY_l: self._goto_line,
            Gdk.KEY_plus: lambda: self._zoom(1), Gdk.KEY_equal: lambda: self._zoom(1),
            Gdk.KEY_KP_Add: lambda: self._zoom(1),
            Gdk.KEY_minus: lambda: self._zoom(-1), Gdk.KEY_KP_Subtract: lambda: self._zoom(-1),
            Gdk.KEY_0: lambda: self._zoom(0),
            Gdk.KEY_z: (lambda: self.buffer.redo()) if shift else (lambda: self.buffer.undo()),
            Gdk.KEY_y: lambda: self.buffer.redo(),
        }
        act = actions.get(k)
        if act is None:
            return False
        act()
        return True

    # -- window life / session ---------------------------------------------------------------
    def _activated(self, *_a) -> None:
        if self.is_active() and self in _S["windows"]:
            _S["windows"].remove(self)
            _S["windows"].insert(0, self)

    def _close_request(self, _w) -> bool:
        """Never asks (macOS autosave): the last window, or one with unsaved
        documents, stays in the session and comes back next time."""
        if self._closing:
            return False
        self._closing = True
        others = [w for w in _S["windows"] if w is not self and w.get_application() is self.get_application()]
        if self.docs and (not others or any(d.dirty for d in self.docs)):
            _ensure_loaded()
            _S["parked"].insert(0, self.state())
        self._forget()
        save_now()
        return False

    def _forget(self) -> None:
        if self in _S["windows"]:
            _S["windows"].remove(self)

    def state(self) -> dict:
        w, h = (self.get_width(), self.get_height()) if self.get_realized() else self.get_default_size()
        return {"width": w, "height": h, "maximized": self.is_maximized(),
                "active": self.docs.index(self.doc) if self.doc in self.docs else 0,
                "tabs": [d.state() for d in self.docs]}

    def _restore(self, st: dict) -> None:
        if st.get("width") and st.get("height"):
            # saved on a bigger display: shrunk to this one
            ui.window.fit_default_size(self, max(360, int(st["width"])), max(240, int(st["height"])))
        if st.get("maximized"):
            self.maximize()
        for t in st.get("tabs", []):
            doc = Document(self)
            if doc.restore(t):
                self._adopt(doc, len(self.docs))
                self._update_tab(doc)
        if self.docs:
            self.select(self.docs[max(0, min(int(st.get("active") or 0), len(self.docs) - 1))])


def _apply_look_everywhere() -> None:
    _apply_size()
    for w in list(_S["windows"]):
        for d in w.docs:
            d.apply_look()
        w._sync_tools()


def _find_open(f: Gio.File):
    for w in _S["windows"]:
        for d in w.docs:
            if d.file is not None and d.file.equal(f):
                return w, d
    return None


# -- session -------------------------------------------------------------------------------------
def _ensure_loaded() -> None:
    """Keep the saved session (as parked windows) before writing a new one."""
    if not _S["loaded"]:
        _S["loaded"] = True
        _S["parked"].extend(session.load())


def schedule_save() -> None:
    if not _S["timer"]:
        _S["timer"] = GLib.timeout_add(SAVE_DELAY_MS, _timer_save)


def _timer_save() -> bool:
    _S["timer"] = 0
    save_now()
    return False


def save_now() -> None:
    if _S["timer"]:
        GLib.source_remove(_S["timer"])
        _S["timer"] = 0
    _ensure_loaded()
    wins = [w.state() for w in _S["windows"] if w.docs and not w._closing] + _S["parked"]
    try:
        session.save(wins)
    except OSError as e:
        print(f"sonata2 textedit: session not saved: {e}")


def _hook_app(app) -> None:
    """Save the session when the application quits (windows still alive)."""
    if app is not None and id(app) not in _S["app_hooks"]:
        _S["app_hooks"].add(id(app))
        app.connect("shutdown", lambda *_: save_now())
        app.connect("window-removed", lambda _a, w: isinstance(w, TextEditWindow) and w._forget())


def _merged(states: list):
    """Every saved window as one: their tabs side by side, the frontmost
    window's first (Vini: documents come back as tabs, never as a pile of
    windows). A file in two windows comes back once; an empty Untitled tab
    (no text, no file) is left out. None when nothing is left."""
    tabs, seen, active_id = [], set(), None
    for i, st in enumerate(states):
        ts = [t for t in st.get("tabs") or [] if isinstance(t, dict)]
        if i == 0 and ts:
            active_id = ts[max(0, min(int(st.get("active") or 0), len(ts) - 1))].get("id")
        for t in ts:
            if not t.get("uri") and not t.get("backup"):
                continue
            key = t.get("uri") or ("id", t.get("id"))
            if key in seen:
                continue
            seen.add(key)
            tabs.append(t)
    if not tabs:
        return None
    active = next((i for i, t in enumerate(tabs) if t.get("id") == active_id), 0)
    return {**states[0], "tabs": tabs, "active": active}


def restore_session(app):
    """Bring back the saved documents as tabs of one window (the saved
    session is used up: what is closed afterwards stays closed). The window,
    or None when there was nothing to bring back."""
    _ensure_loaded()
    states, _S["parked"] = _S["parked"], []
    st = _merged(states)
    shown = None
    if st is not None:
        w = TextEditWindow(app, restore=st)
        if any(not d.blank or d.file for d in w.docs):
            w.present()
            shown = w
        else:
            w._closing = True
            w.destroy()
    schedule_save()
    return shown


def open_paths(app, paths) -> None:
    """Files become tabs of the frontmost window (files already open come
    forward). Without files: the open window comes forward, or the saved
    session comes back, or an Untitled window. With files and no window
    open, the saved session comes back too, in the same window (it used to
    wait hidden for the next launch, and its windows piled up)."""
    wins = [w for w in _S["windows"] if w.get_application() is app]
    if not paths:
        if wins:
            wins[0].present()
        elif not restore_session(app):
            TextEditWindow(app).present()
        return
    front = wins[0] if wins else restore_session(app)
    for p in paths:
        f = Gio.File.new_for_commandline_arg(p)
        if _find_open(f):
            w, d = _find_open(f)
            w.select(d)
            front = w
            continue
        if front is None:
            front = TextEditWindow(app)
        front.open_file(f)
    if front is not None:
        front.present()


def textedit_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=TextEdit\n"
                              "Comment=Write and edit plain text documents\n"
                              "Icon=accessories-text-editor\nCategories=Utility;TextEditor;\n"
                              "MimeType=text/plain;text/markdown;text/x-log;application/x-shellscript;"
                              "text/x-python;application/json;text/csv;text/x-csrc;text/x-chdr;"
                              "text/x-c++src;text/javascript;text/css;text/html;application/xml;"
                              "application/x-yaml;application/toml;text/rtf;"
                              "application/vnd.openxmlformats-officedocument.wordprocessingml.document;"
                              "application/vnd.oasis.opendocument.text;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} textedit %F\n"
                              "Actions=new-window;\n\n[Desktop Action new-window]\nName=New Window\n"
                              f"Exec={command} textedit --new-window\n")
