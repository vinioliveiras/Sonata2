"""Assistant: chat with Claude (the Anthropic API) in a Sonata window.

The sidebar lists the conversations by date (Today, Yesterday, Previous 7
Days...; right-click: Rename, Delete); the chat is in the middle and the
message field at the bottom (Return sends, Shift+Return starts a new line,
Esc stops the answer). Answers stream in as they are written, formatted
from their Markdown (markdown.py). Everything is saved locally (store.py).

Keyboard (⌘ is Ctrl or Super): N new chat, W close, Esc stop.

The API key and model: api.py (Settings come next)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from . import api, markdown  # noqa: E402
from . import store as S  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.assistant"
DEFAULTS = {"selected": "", "model": ""}
COLUMN_W = 720              # the conversation's reading width (centred)

ui.register("""
window.sonata-assistant { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.as-paned > separator { min-width: 1px; background: %(content_bg)s; box-shadow: inset 1px 0 %(separator)s; }
.as-sidebar list { background: none; padding: 4px 10px 10px 10px; }
.as-sidebar list row { min-height: 30px; padding: 0 8px; border-radius: %(r_menu)s; background: none;
  color: %(label)s; transition: background-color %(t_fast)s; }
.as-sidebar list row:hover { background: none; }
.as-sidebar list row:active { background: %(tool_hover)s; transition: background-color %(t_press)s; }
.as-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.as-sidebar list row.as-head { min-height: 22px; margin-top: 10px; }
.as-sidebar list row.as-head:first-child { margin-top: 0; }
.as-sidebar .as-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; }
.as-sidebar .as-date { color: %(label_secondary)s; font-size: %(text_small)s; }
.as-sidebar editablelabel text { background: %(content_bg)s; border-radius: 3px;
  box-shadow: 0 0 0 2px alpha(%(accent)s, 0.5); }
.as-sidebar-empty { color: %(label_tertiary)s; padding: 16px; }
.as-chat { background: %(content_bg)s; }
.as-column { padding: 18px 24px 10px 24px; }
.as-empty { color: %(label_tertiary)s; font-size: %(text_title)s; }
.as-user { background: %(accent)s; color: %(label_on_accent)s; border-radius: 16px; padding: 7px 12px;
  margin: 8px 0 4px 80px; }
.as-user selection { background: alpha(#ffffff, 0.35); color: %(label_on_accent)s; }
.as-answer { margin: 6px 0 10px 0; }
.as-answer label { line-height: 1.35; }
.as-answer label.as-h1 { font-size: calc(%(text_title)s * 1.25); font-weight: 700; margin-top: 6px; }
.as-answer label.as-h2 { font-size: %(text_title)s; font-weight: 700; margin-top: 6px; }
.as-answer label.as-h3 { font-weight: 700; margin-top: 4px; }
.as-answer label.as-quote { color: %(label_secondary)s; padding-left: 10px; box-shadow: inset 3px 0 %(separator)s; }
.as-answer .as-code { background: %(control_bg)s; border-radius: %(r_menu)s; padding: 8px 10px;
  box-shadow: inset 0 0 0 0.5px %(separator)s; }
.as-answer .as-code label { font-family: %(font_mono)s; font-size: %(text_small)s; }
.as-answer .as-rule { min-height: 1px; background: %(separator)s; margin: 6px 0; }
.as-error { color: %(destructive)s; margin: 6px 0; }
.as-composer-bar { background: %(content_bg)s; padding: 6px 24px 14px 24px; }
.as-composer { background: %(control_bg)s; border-radius: 18px; padding: 4px 4px 4px 14px;
  box-shadow: inset 0 0 0 0.5px %(separator)s, %(shadow_control)s;
  transition: box-shadow %(t_fast)s; }
.as-composer:focus-within { box-shadow: inset 0 0 0 0.5px %(separator)s, 0 0 0 3px alpha(%(accent)s, 0.35); }
.as-composer textview, .as-composer textview text { background: none; color: %(label)s; }
.as-composer .as-placeholder { color: %(label_tertiary)s; }
.as-composer button.as-send { min-width: 28px; min-height: 28px; padding: 0; border-radius: 99px; border: none;
  box-shadow: none; background: %(accent)s; color: %(label_on_accent)s;
  transition: background-color %(t_fast)s, opacity %(t_fast)s; }
.as-composer button.as-send:disabled { opacity: 0.35; }
.as-composer button.as-send:active { filter: brightness(0.85); transition: filter %(t_press)s; }
""", key="assistant-window")


def _head(text: str) -> Gtk.ListBoxRow:
    row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["as-head"], can_focus=False)
    row.cid = None
    row.set_child(Gtk.Label(label=text, xalign=0))
    return row


class ChatRow(Gtk.ListBoxRow):
    """A conversation in the sidebar: its title (renamable) and date."""

    def __init__(self, meta: dict):
        super().__init__()
        self.cid = meta["id"]
        box = Gtk.Box(spacing=8)
        self.name = Gtk.EditableLabel(text=meta.get("title") or "New Chat", hexpand=True, editable=False)
        lab = self.name.get_first_child()
        if isinstance(lab, Gtk.Stack):                       # the label inside: ellipsize long titles
            inner = lab.get_first_child()
            if isinstance(inner, Gtk.Label):
                inner.set_ellipsize(Pango.EllipsizeMode.END)
                inner.set_xalign(0)
        box.append(self.name)
        box.append(Gtk.Label(label=ui.fmt.short_date(meta.get("modified", 0)), css_classes=["as-date"]))
        self.set_child(box)


def _appear(widget: Gtk.Widget) -> Gtk.Revealer:
    """New messages fade and slide in (t_fast)."""
    rev = Gtk.Revealer(child=widget, transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                       transition_duration=150, reveal_child=False)
    GLib.idle_add(lambda: (rev.set_reveal_child(True), False)[1])
    return rev


def _label(markup: str, css: str = None) -> Gtk.Label:
    lab = Gtk.Label(wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, xalign=0, selectable=True,
                    use_markup=True, can_focus=False)
    lab.set_markup(markup)
    if css:
        lab.add_css_class(css)
    return lab


class Answer(Gtk.Box):
    """An answer, shown from its Markdown. set_text() while it streams only
    touches the blocks that changed (usually the last one)."""

    def __init__(self, text: str = ""):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["as-answer"])
        self._blocks = []            # [(block, widget, label)]
        self.text = ""
        self.set_text(text)

    def set_text(self, text: str) -> None:
        if text == self.text and self._blocks:
            return
        self.text = text
        new = markdown.blocks(text)
        for i, b in enumerate(new):
            if i < len(self._blocks):
                old, w, lab = self._blocks[i]
                if old == b:
                    continue
                if old[0] == b[0]:                           # same kind: new text only
                    self._fill(lab, b)
                    self._blocks[i] = (b, w, lab)
                    continue
                self._drop_from(i)
            w, lab = self._make(b)
            self.append(w)
            self._blocks.append((b, w, lab))
        self._drop_from(len(new))

    def _drop_from(self, i: int) -> None:
        for _b, w, _l in self._blocks[i:]:
            self.remove(w)
        del self._blocks[i:]

    def _make(self, b):
        kind = b[0]
        if kind == "hr":
            return Gtk.Box(css_classes=["as-rule"]), None
        if kind == "code":
            lab = Gtk.Label(xalign=0, selectable=True, can_focus=False, wrap=False)
            frame = Gtk.ScrolledWindow(child=lab, css_classes=["as-code"], vscrollbar_policy=Gtk.PolicyType.NEVER,
                                       propagate_natural_height=True)
            self._fill(lab, b)
            return frame, lab
        lab = _label("", {"h1": "as-h1", "h2": "as-h2", "h3": "as-h3", "quote": "as-quote"}.get(kind))
        self._fill(lab, b)
        return lab, lab

    @staticmethod
    def _fill(lab, b) -> None:
        if lab is None:
            return
        if b[0] == "code":
            lab.set_text(b[1])
        else:
            lab.set_markup(markdown.inline(b[1]))


class AssistantWindow(Gtk.ApplicationWindow):
    def __init__(self, app, store: S.Store = None):
        super().__init__(application=app, title="Assistant", default_width=1000, default_height=680)
        self.add_css_class("sonata-assistant")
        self.set_size_request(600, 400)
        ui.window.standard(self)
        self.cfg = config.load("assistant", DEFAULTS)
        self.store = store or S.Store()
        self.chat = None             # the open conversation (with its messages)
        self.job = None              # the answer being written
        self.job_chat = None         # ...and its conversation
        self.partial = ""            # its text so far
        self.live = None             # its Answer widget, when that chat is shown
        self.typing = None           # the spinner before the first words
        self._stick = True           # follow the end while answers grow
        self._syncing = False

        self.toolbar = ui.window.glass_toolbar(self, start=(
            ("sidebar-show-symbolic", "Show Sidebar", self.toggle_sidebar),), end=(
            ("document-edit-symbolic", "New Chat", self.new_chat),))

        self.sidebar = self._sidebar()
        self.paned = Gtk.Paned(start_child=self.sidebar, end_child=self._chat_pane(), shrink_start_child=False,
                               resize_start_child=False, shrink_end_child=False, css_classes=["as-paned"],
                               vexpand=True)
        self.paned.set_position(ui.window.SIDEBAR_W)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self.toolbar)
        col.append(self.paned)
        self.set_child(col)

        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._close_request)
        self.rebuild_sidebar()
        if self.store.get(self.cfg["selected"]):
            self.open_chat(self.cfg["selected"])
        else:
            self.new_chat()

    # -- sidebar ----------------------------------------------------------------------------------
    def _sidebar(self) -> Gtk.Box:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["as-sidebar", "sonata-sidebar"])
        box.set_size_request(180, -1)
        self.side = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.side.connect("row-selected", self._side_selected)
        self.side.set_placeholder(Gtk.Label(label="No Conversations", css_classes=["as-sidebar-empty"]))
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", self._side_menu)
        self.side.add_controller(menu)
        box.append(Gtk.ScrolledWindow(child=self.side, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        return box

    def rebuild_sidebar(self) -> None:
        self._syncing = True
        self.side.remove_all()
        section = None
        for meta in self.store.chats:
            sec = ui.fmt.date_section(meta.get("modified", 0))
            if sec != section:
                section = sec
                self.side.append(_head(sec[2]))
            row = ChatRow(meta)
            row.name.connect("notify::editing", self._renamed, row)
            self.side.append(row)
        cur = self._row(self.chat["id"]) if self.chat else None
        self.side.select_row(cur)
        self._syncing = False

    def _row(self, cid):
        row = self.side.get_first_child()
        while row is not None:
            if getattr(row, "cid", None) == cid:
                return row
            row = row.get_next_sibling()
        return None

    def _side_selected(self, _lb, row) -> None:
        if self._syncing or row is None or not row.cid:
            return
        if self.chat is None or row.cid != self.chat["id"]:
            self.open_chat(row.cid)

    def _side_menu(self, _gest, _n, x, y) -> None:
        row = self.side.get_row_at_y(int(y))
        if row is None or not row.cid:
            return
        ui.menu.popup(self.side, [[ui.menu.Item("Rename", lambda: self.rename(row.cid))],
                                  [ui.menu.Item("Delete…", lambda: self.delete_chat(row.cid))]],
                      at=(x, y), glass=True, passthrough=True)

    def rename(self, cid: str) -> None:
        row = self._row(cid)
        if row is not None:
            row.name.set_editable(True)
            row.name.start_editing()

    def _renamed(self, label, _p, row) -> None:
        if label.get_editing():
            return
        label.set_editable(False)
        self.store.rename(row.cid, label.get_text())
        meta = self.store.get(row.cid)
        if meta is not None:
            if label.get_text() != meta["title"]:
                label.set_text(meta["title"])
            if self.chat is not None and self.chat["id"] == row.cid:
                self.chat["title"] = meta["title"]

    def delete_chat(self, cid: str) -> None:
        meta = self.store.get(cid)
        if meta is None:
            return

        def answer(rid):
            if rid != "delete":
                return
            if self.job is not None and self.job_chat and self.job_chat["id"] == cid:
                self.stop(keep=False)
            self.store.delete(cid)
            if self.chat is not None and self.chat["id"] == cid:
                self.new_chat()
            else:
                self.rebuild_sidebar()
        ui.dialog.alert(f"Delete “{meta['title']}”?", "This conversation will be deleted. You can’t undo this.",
                        [("cancel", "Cancel", ""), ("delete", "Delete", "destructive")], answer, parent=self)

    def toggle_sidebar(self) -> None:
        self.sidebar.set_visible(not self.sidebar.get_visible())

    # -- chat ---------------------------------------------------------------------------------------
    def _chat_pane(self) -> Gtk.Widget:
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["as-column"])
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scroll.set_child(_Column(self.messages))
        adj = self.scroll.get_vadjustment()
        adj.connect("value-changed", self._scrolled)
        adj.connect("changed", self._grown)
        self.empty = Gtk.Label(label="Start a conversation", css_classes=["as-empty"], can_target=False,
                               halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        over = Gtk.Overlay(child=self.scroll, vexpand=True)
        over.add_overlay(self.empty)

        self.input = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, hexpand=True,
                                  top_margin=6, bottom_margin=6)
        self.input.get_buffer().connect("changed", self._input_changed)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._input_key)
        self.input.add_controller(keys)
        self.input_scroll = Gtk.ScrolledWindow(child=self.input, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                               propagate_natural_height=True, max_content_height=180,
                                               vscrollbar_policy=Gtk.PolicyType.EXTERNAL, hexpand=True,
                                               valign=Gtk.Align.FILL)
        self.placeholder = Gtk.Label(label="Message", css_classes=["as-placeholder"], xalign=0, can_target=False,
                                     halign=Gtk.Align.START, valign=Gtk.Align.CENTER)
        field = Gtk.Overlay(child=self.input_scroll, hexpand=True)
        field.add_overlay(self.placeholder)
        self.send_btn = Gtk.Button(icon_name="go-up-symbolic", css_classes=["as-send"], valign=Gtk.Align.END,
                                   tooltip_text="Send", can_focus=False, sensitive=False)
        self.send_btn.connect("clicked", lambda *_: self.stop() if self.job else self.send())
        composer = Gtk.Box(spacing=6, css_classes=["as-composer"])
        composer.append(field)
        composer.append(self.send_btn)
        bar = Gtk.Box(css_classes=["as-composer-bar"])
        bar.append(_Column(composer))
        bar.get_first_child().set_hexpand(True)

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["as-chat"])
        pane.set_size_request(320, -1)
        pane.append(over)
        pane.append(bar)
        return pane

    def _scrolled(self, adj) -> None:
        self._stick = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 40

    def _grown(self, adj) -> None:
        if self._stick:
            adj.set_value(adj.get_upper() - adj.get_page_size())

    def _clear(self) -> None:
        child = self.messages.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.messages.remove(child)
            child = nxt
        self.live = None

    def _add(self, widget: Gtk.Widget, animate: bool = True) -> None:
        self.messages.append(_appear(widget) if animate else widget)
        self.empty.set_visible(False)

    def _add_message(self, msg: dict, animate: bool = True):
        if msg["role"] == "user":
            lab = _label(GLib.markup_escape_text(msg["content"]), "as-user")
            lab.set_halign(Gtk.Align.END)
            self._add(lab, animate)
            return lab
        ans = Answer(msg["content"])
        self._add(ans, animate)
        return ans

    def open_chat(self, cid: str) -> None:
        if self.job_chat is not None and self.job_chat["id"] == cid:
            chat = self.job_chat                    # still being answered: the live copy
        else:
            chat = self.store.load(cid)
        self._show(chat)

    def new_chat(self) -> None:
        if self.chat is not None and not self.chat["messages"] and self.store.get(self.chat["id"]) is None:
            self.input.grab_focus()                 # already a fresh chat
            return
        self._show(self.store.new_chat())

    def _show(self, chat: dict) -> None:
        self.chat = chat
        self._clear()
        for m in chat["messages"]:
            self._add_message(m, animate=False)
        if self.job is not None and self.job_chat is chat:
            self.live = Answer(self.partial)
            self._add(self.live, animate=False)
        self.empty.set_visible(not chat["messages"])
        self._stick = True
        self._syncing = True
        self.side.select_row(self._row(chat["id"]))
        self._syncing = False
        self.set_title(chat.get("title") if chat["messages"] else "Assistant")
        self._update_send()
        GLib.idle_add(lambda: (self.input.grab_focus(), False)[1])

    # -- sending ------------------------------------------------------------------------------------
    def _input_changed(self, buf) -> None:
        self.placeholder.set_visible(buf.get_char_count() == 0)
        # a scroll bar only once the field is at its tallest (its minimum
        # length made a one-line field twice as tall)
        GLib.idle_add(self._input_bar)
        self._update_send()

    def _input_bar(self) -> bool:
        tall = self.input.measure(Gtk.Orientation.VERTICAL, max(self.input.get_width(), 1))[1] > 180
        self.input_scroll.set_policy(Gtk.PolicyType.NEVER,
                                     Gtk.PolicyType.AUTOMATIC if tall else Gtk.PolicyType.EXTERNAL)
        return False

    def _input_text(self) -> str:
        buf = self.input.get_buffer()
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

    def _update_send(self) -> None:
        busy = self.job is not None and self.job_chat is self.chat
        self.send_btn.set_icon_name("media-playback-stop-symbolic" if busy else "go-up-symbolic")
        self.send_btn.set_tooltip_text("Stop" if busy else "Send")
        self.send_btn.set_sensitive(busy or (bool(self._input_text().strip()) and self.job is None))

    def _input_key(self, _c, keyval, _code, state) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & Gdk.ModifierType.SHIFT_MASK:
            self.send()
            return True
        return False

    def send(self) -> None:
        text = self._input_text().strip()
        if not text or self.job is not None:
            return
        self.input.get_buffer().set_text("")
        msg = {"role": "user", "content": text}
        self.chat["messages"].append(msg)
        self._add_message(msg)
        self._stick = True
        self.store.save(self.chat)
        self.set_title(self.chat["title"])
        self.rebuild_sidebar()
        self._ask()

    def _ask(self) -> None:
        """Start the answer to the conversation as it is."""
        key = api.api_key()
        if not key:
            self._error("Add your Anthropic API key to start chatting "
                        "(for now: the ANTHROPIC_API_KEY environment variable).", retry=False)
            return
        self.job_chat, self.partial = self.chat, ""
        self.live = Answer()
        self.typing = ui.progress.spinner()
        self.typing.set_halign(Gtk.Align.START)
        self.live.append(self.typing)
        self._add(self.live)
        self.job = api.stream(key, self.cfg.get("model") or None, self.chat["messages"],
                              self._on_text, self._on_done, self._on_error)
        self._update_send()

    def _on_text(self, piece: str) -> None:
        self.partial += piece
        if self.live is not None and self.job_chat is self.chat:
            if self.typing is not None and self.typing.get_parent() is self.live:
                self.live.remove(self.typing)
                self.typing = None
            self.live.set_text(self.partial)

    def _finish(self, keep: bool = True) -> None:
        chat, text = self.job_chat, self.partial
        self.job, self.job_chat, self.partial = None, None, ""
        if self.typing is not None and self.live is not None and self.typing.get_parent() is self.live:
            self.live.remove(self.typing)
        self.typing = None
        if keep and text.strip() and chat is not None:
            chat["messages"].append({"role": "assistant", "content": text})
            if self.store.get(chat["id"]) is not None or chat is self.chat:
                self.store.save(chat)
                self.rebuild_sidebar()
        elif self.live is not None and self.live.get_parent() is not None and not text.strip():
            self.messages.remove(self.live.get_parent())
        self.live = None
        self._update_send()

    def _on_done(self) -> None:
        self._finish()

    def _on_error(self, message: str) -> None:
        shown = self.job_chat is self.chat
        self._finish()
        if shown:
            self._error(message, retry=True)

    def _error(self, message: str, retry: bool) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["as-error"])
        box.append(_label(GLib.markup_escape_text(message)))
        if retry:
            btn = ui.controls.push_button("Try Again", lambda: self._retry(box))
            btn.set_halign(Gtk.Align.START)
            box.append(btn)
        self._add(box)

    def _retry(self, box) -> None:
        parent = box.get_parent()
        if parent is not None:
            self.messages.remove(parent)
        if self.job is None and self.chat["messages"] and self.chat["messages"][-1]["role"] == "user":
            self._ask()

    def stop(self, keep: bool = True) -> None:
        """Stop the answer; what was written so far stays (macOS-like)."""
        if self.job is not None:
            self.job.cancel()
            self._finish(keep)

    # -- window -------------------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        if keyval == Gdk.KEY_Escape and self.job is not None:
            self.stop()
            return True
        if cmd and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.new_chat()
            return True
        if cmd and keyval in (Gdk.KEY_w, Gdk.KEY_W):
            self.close()
            return True
        return False

    def _close_request(self, _w) -> bool:
        self.stop()
        sel = self.chat["id"] if self.chat and self.store.get(self.chat["id"]) else ""
        if sel != self.cfg.get("selected"):
            config.update("assistant", selected=sel)
        return False


def _Column(child: Gtk.Widget) -> Gtk.Widget:
    """Centres its child at most COLUMN_W wide (a reading column). Adw.Clamp
    is layout only: it brings no look of its own."""
    gi.require_version("Adw", "1")
    from gi.repository import Adw
    return Adw.Clamp(child=child, maximum_size=COLUMN_W, tightening_threshold=COLUMN_W, hexpand=True)


def open_windows(app, paths=None) -> None:
    """One Assistant window: opening it again brings it forward."""
    wins = [w for w in app.get_windows() if isinstance(w, AssistantWindow)]
    (wins[0] if wins else AssistantWindow(app)).present()


def assistant_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Assistant\n"
                              "Comment=Chat with Claude\n"
                              "Icon=sonata-assistant\nCategories=Utility;\n"
                              "Keywords=ai;chat;claude;assistant;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} assistant\n")
