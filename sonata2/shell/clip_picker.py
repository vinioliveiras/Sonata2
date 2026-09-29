"""Clipboard history panel (Super+V, like Windows' Win+V, drawn the macOS
way): a glass panel under the menu bar with the last copies (newest
first), a search field and Clear All. Click a copy, or pick it with the
arrow keys and Return, and it is pasted into the window that had focus
(it goes to the clipboard, then Ctrl+V -- Ctrl+Shift+V in terminals --
through wtype). The history itself is the menu bar's (clipboard.py):
memory only, never on disk."""
import shutil
import subprocess

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from . import layer  # noqa: E402

WIDTH = 380
PREVIEW_LINES = 3
# app ids where Ctrl+V is not "paste" (terminals: Ctrl+Shift+V)
TERMINALS = ("terminal", "konsole", "kitty", "alacritty", "foot", "ghostty", "wezterm", "xterm", "tilix",
             "terminator", "kgx", "console", "ptyxis", "blackbox", "rio", "warp")

ui.register("""
window.sonata-clip-picker, window.sonata-clip-picker > contents { background: none; box-shadow: none; }
.clip-panel { background: %(panel_material)s; border-radius: 12px; margin: 20px; padding-bottom: 4px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 12px 36px rgba(0,0,0,0.3);
  color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.clip-panel .clip-head { margin: 10px 12px 2px; }
.clip-panel .clip-title { font-weight: 700; }
.clip-panel .clip-clear { font-size: %(text_small)s; color: %(accent)s; background: none; border: none;
  box-shadow: none; padding: 0 2px; min-height: 0; }
.clip-panel .clip-clear:hover { text-decoration: underline; }
.clip-panel entry { margin: 6px 10px 6px; min-height: 26px; border-radius: 7px; }
.clip-panel list { background: none; padding: 0 6px; }
.clip-panel list > row { padding: 7px 10px; border-radius: %(r_menu_row)s; margin: 1px 0; }
.clip-panel list > row:hover { background: alpha(%(label)s, 0.08); }
.clip-panel list > row:selected, .clip-panel list > row:selected:hover {
  background: %(accent_selected)s; color: %(label_on_accent)s; }
.clip-panel list > row:selected .clip-meta { color: alpha(%(label_on_accent)s, 0.75); }
.clip-panel .clip-meta { font-size: %(text_small)s; color: %(label_secondary)s; }
.clip-panel .clip-empty { color: %(label_secondary)s; margin: 18px 12px 22px; }
@keyframes clip-in { from { opacity: 0; transform: translateY(-6px) scale(0.98); } to { opacity: 1; transform: none; } }
.clip-panel { animation: clip-in 180ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
""", key="clip-picker")


def _preview(text: str) -> str:
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[:PREVIEW_LINES]) or text.strip()


def _meta(text: str) -> str:
    n = len(text)
    lines = text.count("\n") + 1
    size = f"{n} characters" if n != 1 else "1 character"
    return size + (f" · {lines} lines" if lines > 1 else "")


class ClipboardPicker(Gtk.Window):
    def __init__(self, app, history):
        super().__init__(application=app, title="Clipboard", decorated=False, resizable=False)
        self.add_css_class("sonata-clip-picker")
        self.history = history
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["clip-panel"])
        panel.set_size_request(WIDTH, -1)
        ui.theme.glass_class(panel)
        head = Gtk.Box(css_classes=["clip-head"])
        head.append(Gtk.Label(label="Clipboard", xalign=0, hexpand=True, css_classes=["clip-title"]))
        self.clear_btn = Gtk.Button(label="Clear All", css_classes=["clip-clear"], can_focus=False)
        self.clear_btn.connect("clicked", lambda *_: (history.clear(), self._fill()))
        head.append(self.clear_btn)
        panel.append(head)
        self.search = Gtk.SearchEntry(placeholder_text="Search")
        self.search.connect("search-changed", lambda *_: self._fill())
        self.search.connect("activate", lambda *_: self._paste_selected())
        panel.append(self.search)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE, activate_on_single_click=True)
        self.list.connect("row-activated", lambda _l, row: self._paste(row.text))
        self.scroll = Gtk.ScrolledWindow(child=self.list, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                         propagate_natural_height=True, max_content_height=420)
        panel.append(self.scroll)
        self.empty = Gtk.Label(label="Nothing copied yet", css_classes=["clip-empty"])
        panel.append(self.empty)
        self.set_child(panel)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-clipboard")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_margin(self, LS.Edge.TOP, 90)             # where Spotlight opens
        # a click outside (the window loses the keyboard) closes it
        self.connect("notify::is-active", lambda w, _p: (not w.is_active() and w.get_visible()
                                                         and GLib.timeout_add(120, self._close_if_inactive)))
        history.listeners.append(lambda: self.get_visible() and self._fill())

    def _close_if_inactive(self):
        if not self.is_active():
            self.set_visible(False)
        return False

    # -- content ----------------------------------------------------------------------------------
    def _fill(self) -> None:
        q = self.search.get_text().strip().casefold()
        items = [t for t in self.history.items if not q or q in t.casefold()]
        self.list.remove_all()
        for text in items:
            row = Gtk.ListBoxRow()
            row.text = text
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            box.append(Gtk.Label(label=_preview(text), xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                 lines=PREVIEW_LINES, ellipsize=Pango.EllipsizeMode.END, max_width_chars=44))
            box.append(Gtk.Label(label=_meta(text), xalign=0, css_classes=["clip-meta"]))
            row.set_child(box)
            self.list.append(row)
        self.scroll.set_visible(bool(items))
        self.empty.set_label("No Results" if q and self.history.items else "Nothing copied yet")
        self.empty.set_visible(not items)
        self.clear_btn.set_sensitive(bool(self.history.items))
        first = self.list.get_row_at_index(0)
        if first:
            self.list.select_row(first)

    def _move(self, step: int) -> None:
        row = self.list.get_selected_row()
        i = (row.get_index() if row else -1) + step
        nxt = self.list.get_row_at_index(max(0, i))
        if nxt:
            self.list.select_row(nxt)
            nxt.grab_focus()
            self.search.grab_focus_without_selecting()

    def _key(self, _c, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Up):
            self._move(1 if keyval == Gdk.KEY_Down else -1)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._paste_selected()
            return True
        return False

    # -- pasting ----------------------------------------------------------------------------------
    def _paste_selected(self) -> None:
        row = self.list.get_selected_row()
        if row:
            self._paste(row.text)

    def _paste(self, text: str) -> None:
        self.history.copy(text)
        self.set_visible(False)
        if not shutil.which("wtype"):
            return                                  # it is on the clipboard: Ctrl+V by hand
        combo = ["-M", "ctrl", "-M", "shift", "v", "-m", "shift", "-m", "ctrl"] if _terminal_focused() \
            else ["-M", "ctrl", "v", "-m", "ctrl"]
        # once the keyboard is back on the app and wl-copy owns the clipboard
        GLib.timeout_add(200, lambda: (subprocess.Popen(["wtype"] + combo), False)[1])

    def open(self) -> None:
        self.search.set_text("")
        self._fill()
        self.present()
        self.search.grab_focus()


def _terminal_focused() -> bool:
    try:
        from ..wl.wfipc import WayfireIPC
        views = WayfireIPC().call("window-rules/list-views") or []
    except Exception:                               # no IPC: the usual Ctrl+V
        return False
    app = next(((v.get("app-id") or "").lower() for v in views if v.get("activated")), "")
    return any(t in app for t in TERMINALS)
