"""Character Viewer, compact (macOS Big Sur Ctrl+Cmd+Space): a small
panel with a search field, the emoji of one category (Frequently Used
first) and the category tabs at the bottom. Picking one types it into the
window that had focus (wtype, when installed) and leaves it on the
clipboard. Lazy grid (GridView): only the visible cells exist.
Data: sonata2/data/emoji.tsv (tools/gen-emoji.py)."""
import os
import shutil
import subprocess

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .. import config, ui  # noqa: E402
from . import layer  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "emoji.tsv")
RECENT_MAX = 16
# (group in emoji.tsv, tab icon, title) in macOS order
CATEGORIES = (("recent", "document-open-recent-symbolic", "Frequently Used"),
              ("Smileys & Emotion", "face-smile-symbolic", "Smileys & People"),
              ("Animals & Nature", "emoji-nature-symbolic", "Animals & Nature"),
              ("Food & Drink", "emoji-food-symbolic", "Food & Drink"),
              ("Activities", "emoji-activities-symbolic", "Activity"),
              ("Travel & Places", "emoji-travel-symbolic", "Travel & Places"),
              ("Objects", "emoji-objects-symbolic", "Objects"),
              ("Symbols", "emoji-symbols-symbolic", "Symbols"),
              ("Flags", "emoji-flags-symbolic", "Flags"))
MERGE = {"People & Body": "Smileys & Emotion"}     # macOS: "Smileys & People"

ui.register("""
window.sonata-emoji, window.sonata-emoji > contents { background: none; box-shadow: none; }
.emoji-panel { background: %(panel_material)s; border-radius: %(r_dialog)s; margin: 20px;
  box-shadow: 0 0 0 0.5px %(hairline)s, 0 12px 36px rgba(0,0,0,0.3); color: %(label)s; font-family: %(font)s; }
.emoji-panel entry { margin: 10px 10px 4px; min-height: 26px; border-radius: 7px; }
.emoji-title { font-size: %(text_small)s; font-weight: 600; color: %(label_secondary)s; margin: 6px 12px 2px; }
.emoji-panel gridview { background: none; padding: 0 6px; }
.emoji-panel gridview > child { padding: 0; border-radius: 6px; min-width: 34px; min-height: 34px; }
.emoji-panel gridview > child:hover, .emoji-panel gridview > child:selected { background: alpha(%(label)s, 0.12); }
.emoji-cell { font-size: 22px; font-family: "Noto Color Emoji", "Apple Color Emoji", emoji; }
.emoji-tabs { border-top: 0.5px solid %(separator)s; padding: 4px 6px; }
.emoji-tabs button { min-width: 26px; min-height: 24px; padding: 0; border: none; box-shadow: none;
  background: none; color: %(label_secondary)s; border-radius: 6px; }
.emoji-tabs button:checked { color: %(accent)s; background: alpha(%(label)s, 0.08); }
""", key="emoji")


def load() -> list:
    """[(group, emoji, name)]"""
    out = []
    try:
        with open(DATA, encoding="utf-8") as f:
            for line in f:
                g, e, n = line.rstrip("\n").split("\t")
                out.append((MERGE.get(g, g), e, n))
    except (OSError, ValueError):
        pass
    return out


class EmojiPicker(Gtk.Window):
    def __init__(self, app):
        super().__init__(application=app, title="Emoji & Symbols", decorated=False, resizable=False)
        self.add_css_class("sonata-emoji")
        self.all = load()
        self.names = {e: n for _g, e, n in self.all}
        self.cfg = config.load("emoji", {"recent": []})
        self.group = "recent" if self.cfg["recent"] else "Smileys & Emotion"

        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["emoji-panel"],
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.panel = panel
        panel.set_size_request(340, 380)
        self.search = Gtk.SearchEntry(placeholder_text="Search")
        self.search.connect("search-changed", lambda *_: self._refill())
        self.search.connect("activate", lambda *_: self._pick_first())
        panel.append(self.search)
        self.title = Gtk.Label(xalign=0, css_classes=["emoji-title"])
        panel.append(self.title)

        self.store = Gtk.StringList()
        fac = Gtk.SignalListItemFactory()
        fac.connect("setup", lambda _f, item: item.set_child(Gtk.Label(css_classes=["emoji-cell"])))
        fac.connect("bind", self._bind)
        self.grid = Gtk.GridView(model=Gtk.SingleSelection(model=self.store, autoselect=False),
                                 factory=fac, max_columns=9, min_columns=9, single_click_activate=True)
        self.grid.connect("activate", lambda _g, pos: self._pick(self.store.get_string(pos)))
        scroll = Gtk.ScrolledWindow(child=self.grid, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        panel.append(scroll)
        self.scroll = scroll

        tabs = Gtk.Box(css_classes=["emoji-tabs"], homogeneous=True)
        self.tabs = {}
        first = None
        for key, icon, title in CATEGORIES:
            b = Gtk.ToggleButton(icon_name=icon, tooltip_text=title, group=first, can_focus=False)
            b.connect("toggled", lambda b, k=key: b.get_active() and self._set_group(k))
            first = first or b
            self.tabs[key] = b
            tabs.append(b)
        panel.append(tabs)
        self.set_child(panel)

        # Esc closes it, also from the search field (which kept Esc for itself: Vini)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.search.connect("stop-search", lambda *_: self.set_visible(False))
        # a click outside the panel closes it (Vini): the window covers the
        # screen, see-through; only the panel is drawn
        if layer.overlay_fullscreen(self, "sonata2-emoji"):
            click = Gtk.GestureClick(button=0)
            click.connect("pressed", self._clicked)
            self.add_controller(click)
        self.tabs[self.group].set_active(True)
        self._refill()

    # -- data -----------------------------------------------------------------------------------
    def _bind(self, _f, item):
        e = item.get_item().get_string()
        lbl = item.get_child()
        lbl.set_label(e)
        lbl.set_tooltip_text(self.names.get(e, ""))

    def _set_group(self, key):
        self.group = key
        if self.search.get_text():
            self.search.set_text("")      # search-changed refills
        else:
            self._refill()

    def _refill(self):
        q = self.search.get_text().strip().casefold()
        if q:
            words = q.split()
            items = [e for _g, e, n in self.all if all(w in n for w in words)]
            title = "Search Results" if items else "No Results"
        elif self.group == "recent":
            items = [e for e in self.cfg["recent"] if e in self.names]
            title = "Frequently Used"
        else:
            items = [e for g, e, _n in self.all if g == self.group]
            title = next(t for k, _i, t in CATEGORIES if k == self.group)
        self.title.set_label(title)
        self.store.splice(0, self.store.get_n_items(), items)
        self.scroll.get_vadjustment().set_value(0)

    # -- picking --------------------------------------------------------------------------------
    def _pick_first(self):
        if self.store.get_n_items():
            self._pick(self.store.get_string(0))

    def _pick(self, e):
        if not e:
            return
        recent = [e] + [r for r in self.cfg["recent"] if r != e]
        self.cfg["recent"] = recent[:RECENT_MAX]
        config.save("emoji", self.cfg)
        self.get_display().get_clipboard().set(e)
        self.set_visible(False)
        if shutil.which("wtype"):                 # type it once focus is back on the app
            GLib.timeout_add(180, lambda: (subprocess.Popen(["wtype", "--", e]), False)[1])

    def _clicked(self, _g, _n, x, y):
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if w is None or not (w is self.panel or w.is_ancestor(self.panel)):
            self.set_visible(False)

    def _key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.set_visible(False)
            return True
        return False

    def open(self):
        self.cfg = config.load("emoji", {"recent": []})
        self.search.set_text("")
        self._refill()
        self.present()
        self.search.grab_focus()
