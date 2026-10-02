"""Launchpad in a window (Vini; the "Applications" view of macOS 26): the
same apps as the full-screen Launchpad, on a glass panel in the middle of the
screen instead of over all of it. Settings > Launchpad > Style picks it.

    [icon] Applications (type to search)                     [...]
    [ Social ][ Creativity ][ Entertainment ][ Productivity & Finance ][ Utilities ][ Other ]
    suggestions: the apps opened last from here (then the Dock's)
    Social ...  Creativity ...  one titled grid per category

A tab shows that category alone (again: all). Typing searches every app;
Enter opens the first. Esc (or a click outside the panel) closes; opening an
app closes it. The menu bar and the Dock stay reachable: the surface keeps
out of their reserved space.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import apps, config, icons, ui  # noqa: E402
from .. import launchpad_model as M  # noqa: E402
from . import layer  # noqa: E402

NAME = "launcher"                 # ~/.config/sonata2/launcher.json
DEFAULTS = {"style": "fullscreen", "recent": []}
STYLES = (("fullscreen", "Full Screen"), ("window", "Window"))
COLS = 7
ICON = 64
SUGGESTIONS = 7
OPEN_MS, CLOSE_MS, FADE_MS = 220, 140, 150
TILE_W = 128                      # a column's width: the panel's width decides how many (4-7)

# tab id, title, freedesktop categories (first match wins, in this order)
CATEGORIES = (
    ("social", "Social", {"Chat", "InstantMessaging", "Email", "IRCClient", "Telephony", "VideoConference",
                          "News", "Feed", "ContactManagement"}),
    ("creativity", "Creativity", {"Graphics", "2DGraphics", "3DGraphics", "RasterGraphics", "VectorGraphics",
                                  "Photography", "AudioVideoEditing", "Midi", "Mixer", "Sequencer", "Publishing"}),
    ("entertainment", "Entertainment", {"Game", "AudioVideo", "Audio", "Video", "Player", "TV", "Music"}),
    ("productivity", "Productivity & Finance", {"Office", "Finance", "Development", "Education", "Science",
                                                "Calendar", "ProjectManagement", "WordProcessor", "Spreadsheet",
                                                "Presentation", "IDE", "TextEditor"}),
    ("utilities", "Utilities", {"Utility", "System", "Settings", "FileManager", "Accessibility", "Monitor",
                                "TerminalEmulator", "Network", "WebBrowser", "Security", "Archiving", "Core"}),
)
OTHER = ("other", "Other")

ui.register("""
window.sonata-lpwin, window.sonata-lpwin > contents { background: none; box-shadow: none; }
.lpw-panel { background: %(panel_material)s; border-radius: 26px; padding: 18px 22px 0 22px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 24px 60px rgba(0,0,0,0.30);
  color: %(label)s; font-family: %(font)s; }
.lpw-panel.solid { background: %(menu_bg)s; }
.lpw-head image.lpw-glyph { color: %(label_secondary)s; -gtk-icon-size: 26px; }
.lpw-search, .lpw-search text { background: none; border: none; box-shadow: none; outline: none;
  font-family: %(font_display)s; font-size: 26px; color: %(label)s; padding: 0; min-height: 40px; }
.lpw-search text placeholder { color: %(label_secondary)s; }
.lpw-more { border-radius: 99px; min-width: 30px; min-height: 30px; padding: 0; background: none;
  border: none; box-shadow: none; color: %(label_secondary)s; }
.lpw-more:hover { background: %(tool_hover)s; color: %(label)s; }
.lpw-rule { min-height: 1px; background: %(separator)s; margin: 12px 0; }
.lpw-tab { border-radius: %(r_button)s; padding: 4px 8px; min-height: 26px; border: none; box-shadow: none;
  background: alpha(%(label)s, 0.06); color: %(label_secondary)s; font-size: %(text_body)s;
  transition: background-color %(t_fast)s, color %(t_fast)s; }
.lpw-tab:hover { background: alpha(%(label)s, 0.11); color: %(label)s; }
.lpw-tab:checked { background: %(accent)s; color: %(label_on_accent)s; }
.lpw-section { font-weight: 700; font-size: %(text_body)s; color: %(label)s; margin: 4px 2px 6px 2px; }
.lpw-app { padding: 8px 2px 6px 2px; border-radius: 14px; background: none; border: none; box-shadow: none;
  transition: background-color %(t_fast)s; }
.lpw-app:hover { background: alpha(%(label)s, 0.07); }
.lpw-app:active { background: alpha(%(label)s, 0.13); }
.lpw-app.selected { background: alpha(%(accent)s, 0.22); }
.lpw-app label { color: %(label)s; font-size: %(text_body)s; margin-top: 4px; }
.lpw-empty { color: %(label_secondary)s; font-size: %(text_title)s; margin: 40px 0; }
@keyframes lpw-fade { from { opacity: 0; } to { opacity: 1; } }
.lpw-body { animation: lpw-fade %(fade_ms)dms ease-out both; }
.lpw-more label { font-size: 15px; letter-spacing: 1px; margin-top: -4px; }
@keyframes lpw-in { from { opacity: 0; transform: scale(0.94); } to { opacity: 1; transform: none; } }
.lpw-panel.opening { animation: lpw-in %(open_ms)dms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
.lpw-panel.closing { opacity: 0; transform: scale(0.97);
  transition: opacity %(close_ms)dms ease-in, transform %(close_ms)dms ease-in; }
""", key="launchpad-window", open_ms=ui.tokens.ms(OPEN_MS), close_ms=ui.tokens.ms(CLOSE_MS),
   fade_ms=ui.tokens.ms(FADE_MS))


def style() -> str:
    s = config.load(NAME, DEFAULTS).get("style")
    return s if s in dict(STYLES) else "fullscreen"


def category_of(info) -> str:
    """The tab an app goes under (its desktop entry's Categories)."""
    cats = set(c for c in apps._entry_field(info, "get_categories", "Categories").split(";") if c)
    for cid, _title, names in CATEGORIES:
        if cats & names:
            return cid
    return OTHER[0]


def listed_apps() -> dict:
    """desktop id (no .desktop) -> info, as the full-screen Launchpad lists them
    (its hidden apps left out)."""
    from .launchpad import installed_apps
    hidden = set(config.load("launchpad", {"pages": [], "hidden": []}).get("hidden") or [])
    return {k: v for k, v in installed_apps().items() if k not in hidden}


def suggestions(found: dict) -> list:
    """The apps opened last from here, then the Dock's, up to SUGGESTIONS."""
    out = [k for k in config.load(NAME, DEFAULTS).get("recent") or [] if k in found]
    if len(out) < SUGGESTIONS:
        from . import dock as D
        for key in config.load("dock", D.DEFAULTS).get("pinned") or []:
            k = key[:-8] if key.endswith(".desktop") else key
            if k in found and k not in out:
                out.append(k)
    return out[:SUGGESTIONS]


def note_opened(key: str) -> None:
    cfg = config.load(NAME, DEFAULTS)
    cfg["recent"] = ([key] + [k for k in cfg.get("recent") or [] if k != key])[:SUGGESTIONS * 2]
    config.update(NAME, recent=cfg["recent"])


class AppTile(Gtk.Button):
    def __init__(self, key, info, on_open):
        super().__init__(css_classes=["lpw-app"], can_focus=False, tooltip_text=info.get_display_name())
        self.key, self.info = key, info
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        img = Gtk.Image(pixel_size=ICON)
        icons.set_image(img, icons.app_icon(info))
        col.append(img)
        col.append(Gtk.Label(label=info.get_display_name(), ellipsize=Pango.EllipsizeMode.END, max_width_chars=14,
                             width_chars=14, justify=Gtk.Justification.CENTER))
        self.set_child(col)
        self.connect("clicked", lambda _b: on_open(self))


def grid(tiles, cols: int = COLS) -> Gtk.FlowBox:
    fb = Gtk.FlowBox(max_children_per_line=cols, min_children_per_line=cols, homogeneous=True,
                     selection_mode=Gtk.SelectionMode.NONE, column_spacing=4, row_spacing=4,
                     activate_on_single_click=False)
    for t in tiles:
        fb.append(t)
    return fb


class LaunchpadWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Launchpad", decorated=False, css_classes=["sonata-lpwin"])
        self.found = {}
        self.tab = None
        self.tiles = []             # the tiles in view, in order (keyboard / Enter)
        self.selected = -1
        self.panel = ui.theme.glass_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                                  css_classes=["lpw-panel"], halign=Gtk.Align.CENTER,
                                                  valign=Gtk.Align.CENTER))
        head = Gtk.Box(spacing=10, css_classes=["lpw-head"])
        head.append(Gtk.Image(icon_name="view-app-grid-symbolic", css_classes=["lpw-glyph"]))
        self.search = Gtk.SearchEntry(placeholder_text="Applications", hexpand=True, css_classes=["lpw-search"])
        lens = self.search.get_first_child()           # the title is the field: no magnifier in it
        if isinstance(lens, Gtk.Image):
            lens.set_visible(False)
        self.search.connect("search-changed", lambda *_: self.refresh())
        self.search.connect("activate", lambda *_: self.open_selected())
        self.search.connect("stop-search", lambda *_: self.escape())
        head.append(self.search)
        more = Gtk.Button(label="\u2022\u2022\u2022", css_classes=["lpw-more"], can_focus=False,
                          valign=Gtk.Align.CENTER, tooltip_text="Options")
        more.connect("clicked", lambda b: self._menu(b))
        head.append(more)
        self.panel.append(head)
        self.panel.append(Gtk.Box(css_classes=["lpw-rule"]))
        self.tabs = Gtk.Box(spacing=8, homogeneous=True)
        self.panel.append(self.tabs)
        self.panel.append(Gtk.Box(css_classes=["lpw-rule"]))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=18,
                            css_classes=["lpw-body"])
        self.cache = {}             # key -> AppTile, kept between opens (built once: smooth typing)
        self.cols = COLS
        self.scroll = Gtk.ScrolledWindow(child=self.body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.panel.append(self.scroll)
        self.set_child(self.panel)

        click = Gtk.GestureClick()                     # outside the panel: close
        click.connect("released", self._outside)
        self.add_controller(click)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        LS = layer.layer_shell()
        self.layer = bool(LS)
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-launchpad-window")
            LS.set_layer(self, LS.Layer.TOP)
            for e in (LS.Edge.TOP, LS.Edge.BOTTOM, LS.Edge.LEFT, LS.Edge.RIGHT):
                LS.set_anchor(self, e, True)
            LS.set_exclusive_zone(self, 0)             # inside the menu bar's and the Dock's space
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)

    # -- open / close -----------------------------------------------------------------------------
    def toggle(self) -> None:
        if self.get_visible() and not self.panel.has_css_class("closing"):
            self.close_window()
        else:
            self.open_window()

    def _size(self) -> None:
        mon = None
        display = self.get_display()
        if display is not None and display.get_monitors().get_n_items():
            mon = display.get_monitors().get_item(0)
        w, h = (mon.get_geometry().width, mon.get_geometry().height) if mon else (1600, 1000)
        pw = max(600, min(1180, int(w * 0.60)))
        ph = max(460, min(820, int(h * 0.66)))
        self.panel.set_size_request(pw, ph)
        self.cols = max(4, min(COLS, (pw - 44) // TILE_W))

    def prepare(self) -> None:
        """The apps and the grids, built (also before the first open: prewarm)."""
        found = listed_apps()
        if set(found) != set(self.found):
            self.cache = {k: t for k, t in self.cache.items() if k in found}
        self.found = found
        self._size()
        self.search.set_text("")
        self.tab = None
        self._build_tabs()
        self.refresh()

    def open_window(self) -> None:
        self.prepare()
        self.panel.remove_css_class("closing")
        self.panel.remove_css_class("opening")
        self.present()
        self.panel.add_css_class("opening")
        self.search.grab_focus()
        self.scroll.get_vadjustment().set_value(0)

    def close_window(self, then=None) -> None:
        if not self.get_visible():
            if then:
                then()
            return
        self.panel.remove_css_class("opening")
        self.panel.add_css_class("closing")

        def done():
            if self.panel.has_css_class("closing"):
                self.set_visible(False)
            if then:
                then()
            return False
        GLib.timeout_add(ui.tokens.ms(CLOSE_MS), done)

    def escape(self) -> None:
        if self.search.get_text():
            self.search.set_text("")
        elif self.tab is not None:
            self.set_tab(None)
        else:
            self.close_window()

    def _outside(self, _g, _n, x, y) -> None:
        ok, b = self.panel.compute_bounds(self)
        if ok and not (b.get_x() <= x <= b.get_x() + b.get_width() and b.get_y() <= y <= b.get_y() + b.get_height()):
            self.close_window()

    def _menu(self, btn) -> None:
        Item = ui.menu.Item
        ui.menu.popup(btn, [[Item("Use Full-Screen Launchpad", lambda: self.set_style("fullscreen"))],
                            [Item("Launchpad Settings…", self._settings)]], position=Gtk.PositionType.BOTTOM)

    def set_style(self, s: str) -> None:
        config.update(NAME, style=s)
        self.close_window()

    def _settings(self) -> None:
        def run():
            import os
            import subprocess
            import sys
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in (root, os.environ.get("PYTHONPATH")) if p))
            subprocess.Popen([sys.executable, "-m", "sonata2", "settings", "--page", "launchpad"], env=env,
                             start_new_session=True)
        self.close_window(run)

    # -- content ----------------------------------------------------------------------------------
    def _groups(self) -> dict:
        groups = {}
        for key, info in self.found.items():
            groups.setdefault(category_of(info), []).append(key)
        for keys in groups.values():
            keys.sort(key=lambda k: self.found[k].get_display_name().lower())
        return groups

    def _build_tabs(self) -> None:
        while (c := self.tabs.get_first_child()) is not None:
            self.tabs.remove(c)
        self.tab_buttons = {}
        groups = self._groups()
        for cid, title in [(c[0], c[1]) for c in CATEGORIES] + [OTHER]:
            if not groups.get(cid):
                continue
            b = Gtk.ToggleButton(label=title, css_classes=["lpw-tab"], can_focus=False)
            b.connect("clicked", lambda _b, c=cid: self.set_tab(None if self.tab == c else c))
            self.tab_buttons[cid] = b
            self.tabs.append(b)

    def set_tab(self, cid) -> None:
        self.tab = cid
        for c, b in self.tab_buttons.items():
            b.set_active(c == cid)
        self.refresh(fade=True)
        self.scroll.get_vadjustment().set_value(0)

    def _tile(self, key, spare: bool = False) -> AppTile:
        """The app's tile, made once; `spare`: a second one (the suggestions
        row shows apps that are also in their category)."""
        ck = key + "\0s" if spare else key
        t = self.cache.get(ck)
        if t is None:
            t = self.cache[ck] = AppTile(key, self.found[key], self.open_tile)
        self.tiles.append(t)
        return t

    def refresh(self, fade: bool = False) -> None:
        for t in self.tiles:                           # kept tiles leave their old grid
            t.remove_css_class("selected")
            box = t.get_parent()                       # its FlowBoxChild (removing that keeps the tile in it)
            if isinstance(box, Gtk.FlowBoxChild):
                box.set_child(None)
        while (c := self.body.get_first_child()) is not None:
            self.body.remove(c)
        self.tiles, self.selected = [], -1
        if fade:                                       # another tab: the grids fade in (not while typing)
            self.body.remove_css_class("lpw-body")
            GLib.idle_add(lambda: (self.body.add_css_class("lpw-body"), False)[1])
        q = self.search.get_text().strip()
        if q:
            meta = {k: (v.get_display_name(), apps._entry_field(v, "get_generic_name", "GenericName"))
                    for k, v in self.found.items()}
            keys = M.search(meta, q)
            if keys:
                self.body.append(grid([self._tile(k) for k in keys], self.cols))
                self._select(0)
            else:
                self.body.append(Gtk.Label(label="No Results", css_classes=["lpw-empty"]))
            return
        groups = self._groups()
        if self.tab is None:
            top = suggestions(self.found)
            if top:
                self.body.append(grid([self._tile(k, spare=True) for k in top], self.cols))
                self.body.append(Gtk.Box(css_classes=["lpw-rule"]))
        for cid, title in [(c[0], c[1]) for c in CATEGORIES] + [OTHER]:
            keys = groups.get(cid)
            if not keys or (self.tab is not None and cid != self.tab):
                continue
            self.body.append(Gtk.Label(label=title, xalign=0, css_classes=["lpw-section"]))
            self.body.append(grid([self._tile(k) for k in keys], self.cols))

    # -- keyboard ---------------------------------------------------------------------------------
    def _select(self, i: int) -> None:
        if 0 <= self.selected < len(self.tiles):
            self.tiles[self.selected].remove_css_class("selected")
        self.selected = i if 0 <= i < len(self.tiles) else -1
        if self.selected >= 0:
            self.tiles[self.selected].add_css_class("selected")

    def _key(self, _c, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.escape()
            return True
        moves = {Gdk.KEY_Left: -1, Gdk.KEY_Right: 1, Gdk.KEY_Up: -self.cols, Gdk.KEY_Down: self.cols}
        if keyval in moves and self.tiles and (self.selected >= 0 or keyval in (Gdk.KEY_Down, Gdk.KEY_Right)):
            self._select(max(0, min(len(self.tiles) - 1, self.selected + moves[keyval]))
                         if self.selected >= 0 else 0)
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.selected >= 0:
            self.open_selected()
            return True
        return False

    def open_selected(self) -> None:
        if 0 <= self.selected < len(self.tiles):
            self.open_tile(self.tiles[self.selected])
        elif self.tiles and self.search.get_text().strip():
            self.open_tile(self.tiles[0])

    def open_tile(self, tile) -> None:
        note_opened(tile.key)
        ctx = self.get_display().get_app_launch_context()

        def launch():
            try:
                tile.info.launch([], ctx)
            except GLib.Error as e:
                print(f"sonata2-launchpad: can't open {tile.key}: {e.message}", flush=True)
        self.close_window(launch)
