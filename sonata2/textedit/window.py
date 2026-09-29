"""TextEdit (macOS TextEdit, plain text): a document window per file.

Title (the compositor's glass title bar): "name — Edited" while unsaved. The page
fills the window (white / dark), text wraps to the window. Open and Save
use Sonata's panels (files/chooser.py). Closing an edited document asks
first (Save / Don't Save / Cancel). Keyboard: ⌘ is Ctrl or Super --
N new, O open, S save, Shift+S save as, W close, F find (Enter next,
Shift+Enter previous, Esc closes), Z undo, Shift+Z / Y redo, + / - / 0 text
size."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.textedit"
DEFAULTS = {"font_size": 14, "wrap": True}
SIZES = (9, 10, 11, 12, 13, 14, 16, 18, 20, 24, 28, 32, 40, 48)

ui.register("""
window.sonata-textedit { background: %(content_bg)s; }
.te-bar { background: %(window_bg)s; box-shadow: inset 0 -1px %(separator)s; }
textview.te-page, textview.te-page text { background: %(content_bg)s; color: %(label)s;
  font-family: %(font)s; caret-color: %(accent)s; }
textview.te-page text selection { background: alpha(%(accent)s, 0.30); color: %(label)s; }
.te-find { padding: 6px 12px; background: %(window_bg)s; box-shadow: inset 0 -1px %(separator)s; }
.te-find entry { min-width: 220px; }
.te-find label { color: %(label_secondary)s; font-size: %(text_small)s; }
""", key="textedit")


class TextEditWindow(Gtk.ApplicationWindow):
    def __init__(self, app, path: str = None):
        if not GLib.get_application_name():
            GLib.set_application_name("TextEdit")          # Recent documents need it
        super().__init__(application=app, title="Untitled", css_classes=["sonata-textedit"])
        ui.window.standard(self)
        self.set_default_size(720, 560)
        self.cfg = config.load("textedit", DEFAULTS)
        self.file = None
        self._closing = False
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.bar = ui.window.titlebar(self, "Untitled", zoom=True)
        self.bar.add_css_class("te-bar")
        # the title bar is the glass one the compositor draws (pixdecor): no second bar
        self.find_bar = self._find_bar()
        col.append(self.find_bar)
        self.view = Gtk.TextView(css_classes=["te-page"], left_margin=28, right_margin=28, top_margin=20,
                                 bottom_margin=40, pixels_below_lines=2, vexpand=True)
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR if self.cfg["wrap"] else Gtk.WrapMode.NONE)
        self.buffer = self.view.get_buffer()
        self.buffer.set_enable_undo(True)
        self.buffer.connect("modified-changed", lambda *_: self._update_title())
        self.found = self.buffer.create_tag("found", background_rgba=_rgba("accent", 0.35))
        scroll = Gtk.ScrolledWindow(child=self.view, vexpand=True)
        col.append(scroll)
        self.set_child(col)
        self._apply_font()
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._close_request)
        if path:
            self.load(Gio.File.new_for_commandline_arg(path))
        self.view.grab_focus()

    # -- files ------------------------------------------------------------------------------
    def load(self, f: Gio.File) -> None:
        try:
            ok, data, _etag = f.load_contents(None)
        except GLib.Error as e:
            ui.dialog.alert(f"“{f.get_basename()}” couldn't be opened.", e.message,
                            [("ok", "OK", "default")], parent=self)
            return
        text = bytes(data).decode("utf-8", errors="replace")
        self.buffer.begin_irreversible_action()
        self.buffer.set_text(text)
        self.buffer.end_irreversible_action()
        self.buffer.place_cursor(self.buffer.get_start_iter())
        self.buffer.set_modified(False)
        self.file = f
        Gtk.RecentManager.get_default().add_item(f.get_uri())
        self._update_title()

    def save(self, then=None) -> None:
        if self.file is None:
            self.save_as(then)
            return
        start, end = self.buffer.get_bounds()
        text = self.buffer.get_text(start, end, True)
        try:
            self.file.replace_contents(text.encode("utf-8"), None, False, Gio.FileCreateFlags.NONE, None)
        except GLib.Error as e:
            ui.dialog.alert(f"“{self.file.get_basename()}” couldn't be saved.", e.message,
                            [("ok", "OK", "default")], parent=self)
            return
        self.buffer.set_modified(False)
        Gtk.RecentManager.get_default().add_item(self.file.get_uri())
        if then:
            then()

    def save_as(self, then=None) -> None:
        from ..files.chooser import ChooserWindow
        folder = self.file.get_parent().get_uri() if self.file else \
            Gio.File.new_for_path(GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
                                  or GLib.get_home_dir()).get_uri()
        name = self.file.get_basename() if self.file else "Untitled.txt"

        def done(uris, _i):
            if uris:
                self.file = Gio.File.new_for_uri(uris[0])
                self.save(then)
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
                f = Gio.File.new_for_uri(uri)
                if self.file is None and not self.buffer.get_modified() and self.buffer.get_char_count() == 0:
                    self.load(f)                  # an empty Untitled window takes it
                else:
                    w = TextEditWindow(self.get_application())
                    w.load(f)
                    w.present()
        dlg = ChooserWindow(self.get_application(), mode="open", title="Open", multiple=True,
                            filters=[("Text Documents", [(1, "text/*")]), ("All Files", [(0, "*")])],
                            on_done=done)
        dlg.set_transient_for(self)
        dlg.present()

    def _update_title(self) -> None:
        name = self.file.get_basename() if self.file else "Untitled"
        edited = self.buffer.get_modified()
        self.set_title(name + (" — Edited" if edited else ""))
        self.bar.title_label.set_label(name + (" — Edited" if edited else ""))

    def _close_request(self, _w) -> bool:
        if self._closing or not self.buffer.get_modified():
            return False
        name = self.file.get_basename() if self.file else "Untitled"

        def answer(rid):
            if rid == "save":
                self.save(self._really_close)
            elif rid == "discard":
                self._really_close()
        ui.dialog.alert(f"Do you want to keep the changes you made to “{name}”?",
                        "Your changes will be lost if you don't save them.",
                        [("cancel", "Cancel", ""), ("discard", "Don't Save", "destructive"),
                         ("save", "Save", "default")], answer, parent=self)
        return True

    def _really_close(self) -> None:
        self._closing = True
        self.close()

    # -- text size --------------------------------------------------------------------------
    def _apply_font(self) -> None:
        css = Gtk.CssProvider()
        css.load_from_string(f"textview.te-page {{ font-size: {self.cfg['font_size']}px; }}")
        ctx = self.view.get_style_context()
        if getattr(self, "_font_css", None):
            ctx.remove_provider(self._font_css)
        ctx.add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 10)
        self._font_css = css

    def _zoom(self, step: int) -> None:
        cur = self.cfg["font_size"]
        if step == 0:
            size = DEFAULTS["font_size"]
        else:
            bigger = [s for s in SIZES if s > cur]
            smaller = [s for s in SIZES if s < cur]
            size = (bigger[0] if bigger else cur) if step > 0 else (smaller[-1] if smaller else cur)
        self.cfg["font_size"] = size
        config.update("textedit", font_size=size)
        self._apply_font()

    # -- find -------------------------------------------------------------------------------
    def _find_bar(self) -> Gtk.Revealer:
        box = Gtk.Box(spacing=8, css_classes=["te-find"])
        self.find_entry = Gtk.SearchEntry(placeholder_text="Find")
        self.find_entry.connect("search-changed", lambda *_: self._find(0))
        self.find_entry.connect("activate", lambda *_: self._find(1))
        self.find_entry.connect("stop-search", lambda *_: self._hide_find())
        self.find_count = Gtk.Label()
        prev = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Previous")
        prev.connect("clicked", lambda *_: self._find(-1))
        nxt = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Next")
        nxt.connect("clicked", lambda *_: self._find(1))
        done = Gtk.Button(label="Done")
        done.connect("clicked", lambda *_: self._hide_find())
        box.append(self.find_entry)
        box.append(prev)
        box.append(nxt)
        box.append(self.find_count)
        box.append(Gtk.Box(hexpand=True))
        box.append(done)
        return Gtk.Revealer(child=box, reveal_child=False,
                            transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=180)

    def _show_find(self) -> None:
        self.find_bar.set_reveal_child(True)
        start, end = self.buffer.get_selection_bounds() or (None, None)
        if start is not None:
            self.find_entry.set_text(self.buffer.get_text(start, end, False)[:200])
        self.find_entry.grab_focus()

    def _hide_find(self) -> None:
        self.buffer.remove_tag(self.found, *self.buffer.get_bounds())
        self.find_bar.set_reveal_child(False)
        self.view.grab_focus()

    def _find(self, direction: int) -> None:
        """Highlight every match, select the next (1), previous (-1) or the
        first from the cursor (0)."""
        needle = self.find_entry.get_text()
        self.buffer.remove_tag(self.found, *self.buffer.get_bounds())
        if not needle:
            self.find_count.set_label("")
            return
        flags = Gtk.TextSearchFlags.CASE_INSENSITIVE
        it, n = self.buffer.get_start_iter(), 0
        while (m := it.forward_search(needle, flags, None)):
            self.buffer.apply_tag(self.found, m[0], m[1])
            it, n = m[1], n + 1
        self.find_count.set_label(f"{n} found" if n else "Not found")
        if not n:
            return
        sel = self.buffer.get_selection_bounds()
        cur = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        if direction < 0:
            anchor = sel[0] if sel else cur
            m = anchor.backward_search(needle, flags, None) or \
                self.buffer.get_end_iter().backward_search(needle, flags, None)
        else:
            anchor = (sel[1] if direction > 0 else sel[0]) if sel else cur
            m = anchor.forward_search(needle, flags, None) or \
                self.buffer.get_start_iter().forward_search(needle, flags, None)
        if m:
            self.buffer.select_range(m[0], m[1])
            self.view.scroll_to_iter(m[0], 0.2, False, 0, 0)

    # -- keys -------------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        shift = state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_to_lower(keyval)
        if not cmd:
            if keyval == Gdk.KEY_Escape and self.find_bar.get_reveal_child():
                self._hide_find()
                return True
            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and shift and self.find_entry.has_focus():
                self._find(-1)
                return True
            return False
        app = self.get_application()
        actions = {
            Gdk.KEY_n: lambda: TextEditWindow(app).present(),
            Gdk.KEY_o: self.open_panel,
            Gdk.KEY_s: (lambda: self.save_as()) if shift else (lambda: self.save()),
            Gdk.KEY_w: self.close,
            Gdk.KEY_f: self._show_find,
            Gdk.KEY_g: lambda: self._find(-1 if shift else 1),
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


def _rgba(token, alpha=1.0):
    c = ui.theme.rgba(token).copy()
    c.alpha = alpha
    return c


def open_paths(app, paths) -> None:
    """A window per file (or one Untitled); files already open come forward."""
    wins = [w for w in app.get_windows() if isinstance(w, TextEditWindow)]
    if not paths:
        if not wins:
            TextEditWindow(app).present()
        else:
            wins[0].present()
        return
    for p in paths:
        f = Gio.File.new_for_commandline_arg(p)
        same = next((w for w in wins if w.file and w.file.equal(f)), None)
        (same or TextEditWindow(app, f.get_path() or f.get_uri())).present()


def textedit_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=TextEdit\n"
                              "Comment=Write and edit plain text documents\n"
                              "Icon=accessories-text-editor\nCategories=Utility;TextEditor;\n"
                              "MimeType=text/plain;text/markdown;text/x-log;application/x-shellscript;"
                              "text/x-python;application/json;text/csv;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} textedit %F\n")
