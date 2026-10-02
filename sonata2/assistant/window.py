"""Assistant: chat with Claude (the Anthropic API) in a Sonata window.

The sidebar lists the conversations by date (Today, Yesterday, Previous 7
Days...; right-click: Rename, Delete); the chat is in the middle and the
message field at the bottom (Return sends, Shift+Return starts a new line,
Esc stops the answer). Answers stream in as they are written, formatted
from their Markdown (markdown.py). Everything is saved locally (store.py).

Above the message field, three meters: the conversation's share of the
model's context window, and the tokens used in the last hour and the last
7 days against your own limits (an API key has none of its own: counted
locally, usage.py).

Settings (the gear in the toolbar, a panel): the API key (kept in the
keyring, keystore.py), the model, your usage limits, and the folders
Claude may use. With
folders, Claude can list, read and create files there (tools.py); every
action waits for your OK in an alert, and shows in the chat.

Keyboard (⌘ is Ctrl or Super): N new chat, comma Settings, W close, Esc stop.

Built only from Sonata's UI kit (sonata2/ui): panel, controls (text
field, text area, round button, pop-up button, push buttons), dialog,
menu, fixed.MaxWidth, window."""
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from . import api, keystore, markdown, tools, usage  # noqa: E402
from . import store as S  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.assistant"
DEFAULTS = {"selected": "", "model": "", "folders": [], "hourly_limit": 1_000_000, "weekly_limit": 10_000_000}
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
.as-meters { padding: 0 8px 8px 8px; }
.as-meter-title { font-size: %(text_small)s; color: %(label_secondary)s; }
.as-meter-value { font-size: %(text_small)s; color: %(label_tertiary)s; font-feature-settings: "tnum"; }
.as-tool { color: %(label_secondary)s; font-size: %(text_small)s; margin: 2px 0; }
.as-tool image { color: %(label_secondary)s; }
.as-tool.as-wait { color: %(accent)s; }
.as-tool.as-wait image { color: %(accent)s; }
.as-tool.as-denied, .as-tool.as-denied image { color: %(label_tertiary)s; }
.as-settings { padding: 2px 0 6px 0; }
.as-settings .as-field-row { padding: 2px 10px 4px 10px; }
.as-settings .as-field-row > label { font-weight: 400; }
.as-settings .panel-caption { padding: 0 10px; font-weight: 400; }
.as-settings .as-folder-row button.sonata-button { min-width: 0; }
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


TOOL_ICONS = {"list_folder": "folder-symbolic", "read_file": "text-x-generic-symbolic",
              "create_file": "document-new-symbolic"}


class ToolRow(Gtk.Box):
    """A file action in the chat: waiting for your OK, done, or not allowed."""

    def __init__(self, name: str, text: str, state: str = "done"):
        super().__init__(spacing=6, css_classes=["as-tool"])
        self.append(Gtk.Image(icon_name=TOOL_ICONS.get(name, "dialog-information-symbolic"), pixel_size=14))
        self.label = Gtk.Label(label=text, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, hexpand=True)
        self.append(self.label)
        self.set_state(state, text)

    def set_state(self, state: str, text: str = None) -> None:
        for c in ("as-wait", "as-denied"):
            self.remove_css_class(c)
        if state in ("wait", "denied"):
            self.add_css_class("as-" + state)
        if text is not None:
            self.label.set_text(text)


def _action_of(block: dict) -> tools.Action:
    inp = block.get("input") or {}
    path = inp.get("path") if isinstance(inp.get("path"), str) else ""
    return tools.Action(block.get("name", ""), path, inp, exists=bool(inp.get("overwrite")))


class AssistantWindow(Gtk.ApplicationWindow):
    def __init__(self, app, store: S.Store = None):
        super().__init__(application=app, title="Assistant", default_width=1000, default_height=680)
        self.add_css_class("sonata-assistant")
        self.set_size_request(600, 400)
        ui.window.standard(self)
        self.cfg = config.load("assistant", DEFAULTS)
        self.store = store or S.Store()
        self.usage = usage.Usage(self.store.folder)
        self.chat = None             # the open conversation (with its messages)
        self.job = None              # the answer being written
        self.job_chat = None         # ...and its conversation (also while its file actions wait)
        self.partial = ""            # its text so far
        self.live = None             # its Answer widget, when that chat is shown
        self.typing = None           # the spinner before the first words
        self.pending = None          # file actions waiting for your OK: {"chat", "queue", "results", "rows"}
        self._alert = None
        self._stick = True           # follow the end while answers grow
        self._syncing = False

        self.toolbar = ui.window.glass_toolbar(self, start=(
            ("sidebar-show-symbolic", "Show Sidebar", self.toggle_sidebar),), end=(
            ("emblem-system-symbolic", "Settings", self.show_settings),
            ("document-edit-symbolic", "New Chat", self.new_chat)))
        self.settings_btn = self.toolbar.get_child().get_end_widget().get_first_child()

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
        # read the key early (a locked keyring may ask), then the models' context windows
        keystore.load(lambda k: k and api.models_async(k, lambda _f: self.update_meters()))
        self._tick = GLib.timeout_add_seconds(60, self._meters_tick)     # the hour and week roll on

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
            if self.job_chat is not None and self.job_chat["id"] == cid:
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

    # -- settings -----------------------------------------------------------------------------------
    def show_settings(self) -> None:
        """The settings panel: API key, model, folders Claude may use."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["as-settings"])
        box.append(ui.panel.header("Assistant Settings"))
        box.append(ui.panel.section_title("Anthropic API Key"))
        self.key_field = ui.controls.text_field(keystore.cached(), "sk-ant-…", secret=True,
                                                on_activate=lambda _t: self._save_key(), hexpand=True)
        save = ui.controls.push_button("Save", self._save_key)
        row = Gtk.Box(spacing=6, css_classes=["as-field-row"])
        row.append(self.key_field)
        row.append(save)
        box.append(row)
        self.key_caption = Gtk.Label(xalign=0, wrap=True, max_width_chars=40, css_classes=["panel-caption"])
        box.append(self.key_caption)
        self._key_status()

        box.append(ui.panel.section_title("Model"))
        self.model_row = Gtk.Box(spacing=6, css_classes=["as-field-row"])
        box.append(self.model_row)
        self._fill_models(None, loading=bool(keystore.cached()))
        if keystore.cached():
            api.models_async(keystore.cached(), lambda found: self._fill_models(found))

        box.append(ui.panel.section_title("Usage Limits"))
        for key, title, options in (("hourly_limit", "Per Hour", usage.HOURLY), ("weekly_limit", "Per Week", usage.WEEKLY)):
            cur = int(self.cfg.get(key) or options[0])
            opts = sorted(set(options) | {cur})

            def chosen(i, key=key, opts=opts):
                self.cfg[key] = opts[i]
                config.update("assistant", **{key: opts[i]})
                self.update_meters()
            row = Gtk.Box(spacing=6, css_classes=["as-field-row"])
            row.append(Gtk.Label(label=title, xalign=0, hexpand=True))
            dd = ui.controls.popup_button([f"{ui.fmt.count(o)} tokens" for o in opts], opts.index(cur), chosen)
            row.append(dd)
            setattr(self, "limit_" + key, dd)
            box.append(row)

        box.append(ui.panel.separator())
        box.append(ui.panel.section_title("Folders Claude Can Use"))
        self.folders_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.folders_box)
        self._fill_folders()
        box.append(ui.panel.row("list-add-symbolic", "Add Folder…", on_click=self.add_folder))
        box.append(Gtk.Label(label="Claude can list, read and create files only in these folders, "
                             "and asks before every action.", xalign=0, wrap=True, max_width_chars=40,
                             css_classes=["panel-caption"]))
        self.settings_panel = ui.panel.popup(self.settings_btn, box, width=360)

    def _key_status(self, text: str = None) -> None:
        if text is None:
            env = keystore._env()
            key = keystore.cached()
            text = ("Saved in your keyring." if key and key != env else
                    "Using the ANTHROPIC_API_KEY environment variable." if key else
                    "Create a key at console.anthropic.com.")
        self.key_caption.set_label(text)

    def _save_key(self) -> None:
        key = self.key_field.get_text().strip()

        def done(ok):
            if not ok:
                self._key_status("Couldn’t save the key: no keyring is running.")
                return
            self._key_status()
            self._fill_models(None, loading=bool(key))
            if key:
                api.models_async(key, lambda found: self._fill_models(found, failed=found is None))
        keystore.save(key, done)

    def _fill_models(self, found, loading: bool = False, failed: bool = False) -> None:
        if getattr(self, "model_row", None) is None:
            return
        child = self.model_row.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.model_row.remove(child)
            child = nxt
        ids = [""] + [m for m, _n in (found or [])]
        names = ["Newest Available"] + [n for _m, n in (found or [])]
        cur = self.cfg.get("model") or ""
        if cur and cur not in ids:                 # chosen before; not listed (yet)
            ids.append(cur)
            names.append(cur)

        def chosen(i):
            if 0 <= i < len(ids) and ids[i] != (self.cfg.get("model") or ""):
                self.cfg["model"] = ids[i]
                config.update("assistant", model=ids[i])
        dd = ui.controls.popup_button(names, ids.index(cur) if cur in ids else 0, chosen)
        dd.set_hexpand(True)
        dd.set_sensitive(bool(found) or bool(cur))
        self.model_row.append(dd)
        if loading:
            self.model_row.append(ui.progress.spinner())
        elif failed:
            self.model_row.append(Gtk.Label(label="Couldn’t load", css_classes=["panel-caption"]))

    def _fill_folders(self) -> None:
        child = self.folders_box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.folders_box.remove(child)
            child = nxt
        for f in self.cfg.get("folders") or []:
            home = GLib.get_home_dir()
            shown = "~" + f[len(home):] if f == home or f.startswith(home + "/") else f
            rm = ui.controls.push_button("Remove", lambda f=f: self.remove_folder(f))
            row = ui.panel.row("folder-symbolic", shown, trailing=rm)
            row.add_css_class("as-folder-row")
            row.set_tooltip_text(f)
            self.folders_box.append(row)

    def add_folder(self) -> None:
        from ..files.chooser import ChooserWindow
        if getattr(self, "settings_panel", None) is not None:
            self.settings_panel.popdown()

        def done(uris, _i):
            for uri in uris or []:
                path = Gio.File.new_for_uri(uri).get_path()
                if path and path not in self.cfg["folders"]:
                    self.cfg["folders"] = self.cfg["folders"] + [path]
            config.update("assistant", folders=self.cfg["folders"])
        dlg = ChooserWindow(self.get_application(), mode="folder", title="Choose a Folder for Claude",
                            accept_label="Allow", on_done=done)
        dlg.set_transient_for(self)
        dlg.set_modal(True)
        dlg.present()

    def remove_folder(self, path: str) -> None:
        self.cfg["folders"] = [f for f in self.cfg["folders"] if f != path]
        config.update("assistant", folders=self.cfg["folders"])
        self._fill_folders()

    # -- chat ---------------------------------------------------------------------------------------
    def _chat_pane(self) -> Gtk.Widget:
        self.messages = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["as-column"])
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scroll.set_child(ui.fixed.MaxWidth(self.messages, COLUMN_W))
        adj = self.scroll.get_vadjustment()
        adj.connect("value-changed", self._scrolled)
        adj.connect("changed", self._grown)
        self.empty = Gtk.Label(label="Start a conversation", css_classes=["as-empty"], can_target=False,
                               halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        over = Gtk.Overlay(child=self.scroll, vexpand=True)
        over.add_overlay(self.empty)

        self.send_btn = ui.controls.round_button("go-up-symbolic", "Send",
                                                 lambda: self.stop() if self._busy_here() else self.send())
        self.send_btn.set_sensitive(False)
        self.composer = ui.controls.TextArea("Message", trailing=self.send_btn, on_submit=lambda _t: self.send(),
                                             on_change=lambda _t: self._update_send())
        self.input = self.composer.view
        self.meters = {}
        meters = Gtk.Box(spacing=16, homogeneous=True, css_classes=["as-meters"])
        for key, title in (("context", "Context"), ("hour", "Last Hour"), ("week", "Last 7 Days")):
            head = Gtk.Box(spacing=6)
            head.append(Gtk.Label(label=title, xalign=0, hexpand=True, css_classes=["as-meter-title"]))
            value = Gtk.Label(css_classes=["as-meter-value"])
            head.append(value)
            meter = ui.progress.meter(0)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            col.append(head)
            col.append(meter)
            self.meters[key] = (meter, value, col)
            meters.append(col)
        stack = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        stack.append(meters)
        stack.append(self.composer)
        bar = Gtk.Box(css_classes=["as-composer-bar"])
        bar.append(ui.fixed.MaxWidth(stack, COLUMN_W))

        pane = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["as-chat"])
        pane.set_size_request(320, -1)
        pane.append(over)
        pane.append(bar)
        return pane

    def update_meters(self) -> None:
        """Context of the open chat; tokens of the last hour and week."""
        chat = self.chat or {}
        window = api.context_window(chat.get("model") or self.cfg.get("model"))
        used = int(chat.get("context_tokens") or 0)
        for key, n, limit, tip in (
                ("context", used, window, "This conversation’s share of the model’s context window"),
                ("hour", self.usage.last_hour(), int(self.cfg.get("hourly_limit") or 1),
                 "Tokens used in the last hour, against your limit (Settings)"),
                ("week", self.usage.last_week(), int(self.cfg.get("weekly_limit") or 1),
                 "Tokens used in the last 7 days, against your limit (Settings)")):
            meter, value, col = self.meters[key]
            ui.progress.set_meter(meter, n / max(1, limit))
            value.set_label(f"{ui.fmt.count(n)} / {ui.fmt.count(limit)}")
            col.set_tooltip_text(f"{tip}: {n:,} of {limit:,} tokens")

    def _meters_tick(self) -> bool:
        self.update_meters()
        return True

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
        self._tool_rows = {}

    def _add(self, widget: Gtk.Widget, animate: bool = True) -> None:
        self.messages.append(_appear(widget) if animate else widget)
        self.empty.set_visible(False)

    def _add_message(self, msg: dict, animate: bool = True, results: dict = None):
        content = msg["content"]
        if msg["role"] == "user":
            text = api.text_of(content)
            if not text.strip():                  # only file-action results: shown on their rows
                return None
            lab = _label(GLib.markup_escape_text(text), "as-user")
            lab.set_halign(Gtk.Align.END)
            self._add(lab, animate)
            return lab
        text = api.text_of(content)
        ans = Answer(text) if text.strip() else None
        if ans is not None:
            self._add(ans, animate)
        if not isinstance(content, str):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "tool_use":
                    act = _action_of(blk)
                    denied = (results or {}).get(blk.get("id"))
                    row = ToolRow(act.name, ("Not allowed: " if denied else "") + act.summary(),
                                  "denied" if denied else "done")
                    self._tool_rows[blk.get("id")] = row
                    self._add(row, animate)
        return ans

    def open_chat(self, cid: str) -> None:
        if self.job_chat is not None and self.job_chat["id"] == cid:
            chat = self.job_chat                    # still being answered: the live copy
        else:
            chat = self.store.load(cid)
        self._show(chat)

    def new_chat(self) -> None:
        if self.chat is not None and not self.chat["messages"] and self.store.get(self.chat["id"]) is None:
            self.composer.grab_focus()              # already a fresh chat
            return
        self._show(self.store.new_chat())

    def _show(self, chat: dict) -> None:
        self.chat = chat
        self._clear()
        results = {}                                 # tool_use id -> refused (from the tool_result blocks)
        for m in chat["messages"]:
            if m["role"] == "user" and not isinstance(m["content"], str):
                for b in m["content"]:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        results[b.get("tool_use_id")] = bool(b.get("is_error"))
        for m in chat["messages"]:
            self._add_message(m, animate=False, results=results)
        if self.job is not None and self.job_chat is chat:
            self.live = Answer(self.partial)
            self._add(self.live, animate=False)
        p = self.pending
        if p is not None:
            p["rows"] = {}
            if p["chat"] is chat:                       # its waiting actions: back on their rows
                for blk in ([p["current"]] if p.get("current") else []) + p["queue"]:
                    row = self._tool_rows.get(blk.get("id"))
                    if row is not None:
                        row.set_state("wait", "Waiting for your OK: " + _action_of(blk).summary())
                        p["rows"][blk.get("id")] = row
        self.empty.set_visible(not chat["messages"])
        self._stick = True
        self._syncing = True
        self.side.select_row(self._row(chat["id"]))
        self._syncing = False
        self.set_title(chat.get("title") if chat["messages"] else "Assistant")
        self._update_send()
        self.update_meters()
        GLib.idle_add(lambda: (self.composer.grab_focus(), False)[1])

    # -- sending ------------------------------------------------------------------------------------
    def _busy(self) -> bool:
        return self.job is not None or self.pending is not None

    def _busy_here(self) -> bool:
        return self._busy() and self.job_chat is self.chat

    def _update_send(self) -> None:
        here = self._busy_here()
        self.send_btn.set_icon_name("media-playback-stop-symbolic" if here else "go-up-symbolic")
        self.send_btn.set_tooltip_text("Stop" if here else "Send")
        self.send_btn.set_sensitive(here or (bool(self.composer.text().strip()) and not self._busy()))

    def send(self) -> None:
        text = self.composer.text().strip()
        if not text or self._busy():
            return
        self.composer.set_text("")
        msg = {"role": "user", "content": text}
        self.chat["messages"].append(msg)
        self._add_message(msg)
        self._stick = True
        self.store.save(self.chat)
        self.set_title(self.chat["title"])
        self.rebuild_sidebar()
        self._ask(self.chat)

    def _ask(self, chat: dict) -> None:
        """Start the answer to the conversation as it is."""
        key = keystore.cached()
        if not key:
            self.job_chat = None
            if chat is self.chat:
                self._error("Add your Anthropic API key in Settings to start chatting.", retry=False,
                            button=("Open Settings", self.show_settings))
            self._update_send()
            return
        self.job_chat, self.partial = chat, ""
        self.live = self.typing = None
        if chat is self.chat:
            self.live = Answer()
            self.typing = ui.progress.spinner()
            self.typing.set_halign(Gtk.Align.START)
            self.live.append(self.typing)
            self._add(self.live)
        folders = [f for f in self.cfg.get("folders") or [] if f]
        self.job = api.stream(key, self.cfg.get("model") or None, chat["messages"],
                              self._on_text, self._on_done, self._on_error,
                              system=tools.system_prompt(folders) or None, tools=tools.TOOLS if folders else None)
        self._update_send()

    def _on_text(self, piece: str) -> None:
        self.partial += piece
        if self.live is not None and self.job_chat is self.chat:
            if self.typing is not None and self.typing.get_parent() is self.live:
                self.live.remove(self.typing)
                self.typing = None
            self.live.set_text(self.partial)

    def _end_stream(self) -> tuple:
        """The stream is over: (its chat, its text); the live widgets settle."""
        chat, text = self.job_chat, self.partial
        self.job, self.partial = None, ""
        if self.typing is not None and self.live is not None and self.typing.get_parent() is self.live:
            self.live.remove(self.typing)
        self.typing = None
        if self.live is not None and not text.strip() and self.live.get_parent() is not None:
            self.messages.remove(self.live.get_parent())
        self.live = None
        return chat, text

    def _save_chat(self, chat: dict) -> None:
        if self.store.get(chat["id"]) is not None or chat is self.chat:
            self.store.save(chat)
            self.rebuild_sidebar()

    def _finish(self, keep: bool = True) -> None:
        chat, text = self._end_stream()
        self.job_chat = None
        if keep and text.strip() and chat is not None:
            chat["messages"].append({"role": "assistant", "content": text})
            self._save_chat(chat)
        self._update_send()

    def _on_done(self, blocks=None, stop=None, used=None) -> None:
        chat, text = self._end_stream()
        if used and chat is not None:
            tokens = api.total_tokens(used)
            self.usage.add(tokens)
            chat["context_tokens"] = tokens            # the conversation's size after this answer
            if used.get("model"):
                chat["model"] = used["model"]
            if chat is self.chat:
                self.update_meters()
        uses = [b for b in blocks or [] if b.get("type") == "tool_use"]
        if not uses:
            self.job_chat = None
            content = api.text_of(blocks) if blocks else text
            if content.strip() and chat is not None:
                chat["messages"].append({"role": "assistant", "content": content})
                self._save_chat(chat)
            self._update_send()
            return
        chat["messages"].append({"role": "assistant", "content": blocks})
        self._save_chat(chat)
        self.pending = {"chat": chat, "queue": list(uses), "results": [], "rows": {}}
        if chat is self.chat:
            for b in uses:
                row = ToolRow(b.get("name", ""), "Waiting for your OK: " + _action_of(b).summary(), "wait")
                self.pending["rows"][b.get("id")] = row
                self._tool_rows[b.get("id")] = row
                self._add(row)
        self._update_send()
        self._next_tool()

    def _next_tool(self) -> None:
        """Ask about the next file action (one alert at a time)."""
        p = self.pending
        if p is None:
            return
        if not p["queue"]:
            self.pending = None
            chat = p["chat"]
            chat["messages"].append({"role": "user", "content": p["results"]})
            self._save_chat(chat)
            if p.get("stopped"):
                self.job_chat = None
                self._update_send()
            else:
                self._ask(chat)
            return
        blk = p["queue"].pop(0)
        row = p["rows"].get(blk.get("id"))
        try:
            act = tools.check(blk.get("name", ""), blk.get("input"), self.cfg.get("folders") or [])
        except tools.Denied as e:
            self._tool_result(blk, str(e), True, row, "Couldn’t do: " + _action_of(blk).summary())
            self._next_tool()
            return
        name = os.path.basename(act.path.rstrip(os.sep)) or act.path
        p["current"] = blk

        def answer(rid):
            if self.pending is not p or p.get("current") is not blk:
                return
            self._alert, p["current"] = None, None
            row = p["rows"].get(blk.get("id"))
            if rid == "allow":
                text, err = tools.run(act)
                self._tool_result(blk, text, err, row, ("Couldn’t do: " if err else "") + act.summary())
            else:
                self._tool_result(blk, "The user did not allow this.", True, row, "Not allowed: " + act.summary())
            self._next_tool()
        body = act.path
        if act.name == "create_file":
            n = act.args["content"].count("\n") + 1
            body += f"\n{n} line{'s' if n != 1 else ''}, {ui.fmt.size(len(act.args['content'].encode()))}"
        self._alert = ui.dialog.alert(f"Allow Claude to {act.verb} “{name}”?", body,
                                      [("deny", "Don’t Allow", ""), ("allow", "Allow",
                                                                    "destructive" if act.exists else "default")],
                                      answer, parent=self)

    def _tool_result(self, blk, text, is_error, row, line) -> None:
        res = {"type": "tool_result", "tool_use_id": blk.get("id"), "content": text}
        if is_error:
            res["is_error"] = True
        self.pending["results"].append(res)
        if row is not None:
            row.set_state("denied" if is_error else "done", line)

    def _on_error(self, message: str) -> None:
        shown = self.job_chat is self.chat
        self._finish()
        if shown:
            self._error(message, retry=True)

    def _error(self, message: str, retry: bool, button=None) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["as-error"])
        box.append(_label(GLib.markup_escape_text(message)))
        if retry:
            button = ("Try Again", lambda: self._retry(box))
        if button:
            btn = ui.controls.push_button(button[0], button[1])
            btn.set_halign(Gtk.Align.START)
            box.append(btn)
        self._add(box)

    def _retry(self, box) -> None:
        parent = box.get_parent()
        if parent is not None:
            self.messages.remove(parent)
        if not self._busy() and self.chat["messages"] and self.chat["messages"][-1]["role"] == "user":
            self._ask(self.chat)

    def stop(self, keep: bool = True) -> None:
        """Stop the answer; what was written so far stays. File actions still
        waiting are not done (Claude is told so)."""
        if self.job is not None:
            self.job.cancel()
            self._finish(keep)
        p = self.pending
        if p is not None:
            p["stopped"] = True
            cur, p["current"] = p.get("current"), None
            for blk in ([cur] if cur else []) + p["queue"]:
                self._tool_result(blk, "The user stopped this.", True, p["rows"].get(blk.get("id")),
                                  "Not allowed: " + _action_of(blk).summary())
            p["queue"] = []
            if self._alert is not None:
                alert, self._alert = self._alert, None
                try:
                    alert.force_close() if hasattr(alert, "force_close") else alert.close()
                except Exception:               # noqa: BLE001  (already gone)
                    pass
            if self.pending is p:
                self._next_tool()

    # -- window -------------------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        if keyval == Gdk.KEY_Escape and self._busy_here():
            self.stop()
            return True
        if cmd and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.new_chat()
            return True
        if cmd and keyval == Gdk.KEY_comma:
            self.show_settings()
            return True
        if cmd and keyval in (Gdk.KEY_w, Gdk.KEY_W):
            self.close()
            return True
        return False

    def _close_request(self, _w) -> bool:
        self.stop()
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0
        sel = self.chat["id"] if self.chat and self.store.get(self.chat["id"]) else ""
        if sel != self.cfg.get("selected"):
            config.update("assistant", selected=sel)
        return False


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
