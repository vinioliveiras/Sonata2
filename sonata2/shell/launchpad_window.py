"""Launchpad's other layout, the Apps Menu (Vini; the "Applications" view of
macOS 26): the same component -- the same apps, folders, Hidden folder,
menus, drags to the Dock and the Trash, jiggle mode, linked folders -- on a
glass panel in the middle of the screen instead of over all of it.
Settings > Appearance > Apps style picks it; its glass and transparency: Settings >
Appearance > Glass & Transparency.

    [icon] Apps (type to search)                                   [...]
    [ Social ][ Creativity ][ Entertainment ][ Productivity & Finance ][ Utilities ][ Other ]
    your apps and folders in the full screen's order (Hidden last);
    a tab: that category's apps by name (again: back to your order)

MenuView is only the layout: the tiles are Launchpad's own LaunchItems, and
everything they do goes through the Launchpad (shell/launchpad.py, mode
"menu"). A tab shows that category alone (again: all). Typing searches;
Enter opens the first. Esc steps back (search, tab, folder, jiggle) and
closes; a click outside the panel closes; opening an app closes it. Hold an
app over another (or a folder) to make a folder, as in the full screen.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from .. import apps, config, names, ui  # noqa: E402
from .. import launchpad_model as M  # noqa: E402

NAME = "launcher"                 # ~/.config/sonata2/launcher.json
DEFAULTS = {"style": "fullscreen"}
STYLES = (("fullscreen", "Full Screen"), ("window", names.APPS_MENU))
COLS = 7
ICON = 64
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
.lpw-panel { background: %(launchpad_material)s; border-radius: 26px; padding: 18px 22px 0 22px;
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
/* Launchpad's tiles, on the panel instead of the dimmed desktop */
.lpw-root .lp-item { padding: 8px 2px 6px 2px; border-radius: 14px; transition: background-color %(t_fast)s; }
.lpw-root .lp-item:hover { background: alpha(%(label)s, 0.07); }
.lpw-root .lp-item.selected { background: alpha(%(label)s, 0.12); }   /* grey, as in the full screen (Vini) */
.lpw-root .lp-label { color: %(label)s; font-size: %(text_body)s; text-shadow: none; margin-top: 4px; }
.lpw-root .lp-panel { background: %(launchpad_material)s; box-shadow: 0 0 0 0.5px %(hairline)s,
  0 12px 40px rgba(0,0,0,0.28); }
.lpw-root .lp-panel-title, .lpw-root .lp-panel-title text, .lpw-root .lp-lock, .lpw-root .lp-lock-text {
  color: %(label)s; }
.lpw-root .lp-lock-hint { color: %(label_secondary)s; }
.lpw-content { transition: opacity %(fade_ms)dms ease-out; }
.lpw-content.dimmed { opacity: 0.3; }
.lpw-empty { color: %(label_secondary)s; font-size: %(text_title)s; margin: 40px 0; }
@keyframes lpw-fade { from { opacity: 0; } to { opacity: 1; } }
.lpw-body { animation: lpw-fade %(fade_ms)dms ease-out both; }
.lpw-dots { font-size: 15px; letter-spacing: 1px; margin-top: -4px; }   /* (not "label": its menu inherits it) */
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


SETTINGS_PAGE = "appearance"          # where the style is chosen (Vini)


def set_style(pad, s: str) -> None:
    config.update(NAME, style=s)
    pad.close_launchpad()


def open_style_settings(pad) -> None:
    from .topbar import open_settings
    pad.close_launchpad(lambda: open_settings(SETTINGS_PAGE))


def options_menu(pad, btn) -> None:
    """The "•••" menu, in both layouts: the other layout, and its settings."""
    Item = ui.menu.Item
    other = (Item(f"Use Full-Screen {names.APPS}", lambda: set_style(pad, "fullscreen"))
             if pad.mode == "menu" else Item(f"Use {names.APPS_MENU}", lambda: set_style(pad, "window")))
    ui.menu.popup(btn, [[other], [Item(f"{names.APPS} Settings\u2026", lambda: open_style_settings(pad))]],
                  position=Gtk.PositionType.BOTTOM)


def panel_size(w: int, h: int) -> tuple:
    """The panel for a w x h screen (Vini: smaller than at first, which took
    60 % of the width): about 46 % x 58 %, within 560-940 x 440-680."""
    return max(560, min(940, int(w * 0.46))), max(440, min(680, int(h * 0.58)))


def flow(widgets, cols: int, pad=None) -> Gtk.FlowBox:
    fb = Gtk.FlowBox(max_children_per_line=cols, min_children_per_line=cols, homogeneous=True,
                     selection_mode=Gtk.SelectionMode.NONE, column_spacing=4, row_spacing=4,
                     activate_on_single_click=False, valign=Gtk.Align.START)   # few apps: at the top (Vini)
    for w in widgets:
        fb.append(w)
    if pad is not None:                       # hold an app over another: a folder (as in the full screen)
        target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        target.connect("motion", lambda _t, x, y: pad.menu.drag_over(fb, x, y))
        target.connect("drop", lambda _t, v, x, y: pad.menu.drag_drop(v))
        fb.add_controller(target)
    return fb


class MenuView:
    """The Apps Menu layout of a Launchpad (`pad`). root: what the window
    shows in this mode."""

    def __init__(self, pad):
        self.pad = pad
        self.tab = None
        self.tiles = []             # LaunchItems in view, in order (keyboard / Enter)
        self.selected = -1
        self.cache = {}             # (section, key) -> LaunchItem, kept: built once
        self.cols = COLS
        self.panel = ui.theme.glass_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                                  css_classes=["lpw-panel"], halign=Gtk.Align.CENTER,
                                                  valign=Gtk.Align.CENTER))
        head = Gtk.Box(spacing=10, css_classes=["lpw-head"])
        head.append(Gtk.Image(icon_name="view-app-grid-symbolic", css_classes=["lpw-glyph"]))
        self.search = Gtk.SearchEntry(placeholder_text=names.APPS, hexpand=True, css_classes=["lpw-search"])
        lens = self.search.get_first_child()           # the title is the field: no magnifier in it
        if isinstance(lens, Gtk.Image):
            lens.set_visible(False)
        self.search.connect("search-changed", lambda *_: self.refresh())
        self.search.connect("activate", lambda *_: self.open_selected())
        head.append(self.search)
        more = Gtk.Button(child=Gtk.Label(label="\u2022\u2022\u2022", css_classes=["lpw-dots"]),
                          css_classes=["lpw-more"], can_focus=False, valign=Gtk.Align.CENTER,
                          tooltip_text="Options")
        more.connect("clicked", lambda b: self._menu(b))
        head.append(more)
        self.panel.append(head)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, css_classes=["lpw-content"])
        self.content.append(Gtk.Box(css_classes=["lpw-rule"]))
        self.tabs = Gtk.Box(spacing=8, homogeneous=True)
        self.content.append(self.tabs)
        self.content.append(Gtk.Box(css_classes=["lpw-rule"]))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_bottom=18,
                            valign=Gtk.Align.START, css_classes=["lpw-body"])
        self.scroll = Gtk.ScrolledWindow(child=self.body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content.append(self.scroll)
        self.panel.append(self.content)
        # folder panels open over the menu (Launchpad's own, launchpad._host)
        self.overlay = Gtk.Overlay(child=self.panel, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.root = Gtk.Box(css_classes=["lpw-root"], hexpand=True, vexpand=True)
        self.overlay.set_hexpand(True)
        self.root.append(self.overlay)
        click = Gtk.GestureClick()
        click.connect("released", self._outside)
        self.root.add_controller(click)

    # -- open / close -----------------------------------------------------------------------------
    def is_open(self) -> bool:
        return self.pad.get_visible() and not self.panel.has_css_class("closing")

    def _size(self) -> None:
        display = self.pad.get_display()
        mon = display.get_monitors().get_item(0) if display and display.get_monitors().get_n_items() else None
        w, h = (mon.get_geometry().width, mon.get_geometry().height) if mon else (1600, 1000)
        pw, ph = panel_size(w, h)
        self.panel.set_size_request(pw, ph)
        self.cols = max(4, min(COLS, (pw - 44) // TILE_W))

    def open(self) -> None:
        self._size()
        self.search.set_text("")
        self.tab = None
        self._build_tabs()
        self.refresh()
        self.panel.remove_css_class("closing")
        self.panel.remove_css_class("opening")
        self.pad.present()
        self.panel.add_css_class("opening")
        self.search.grab_focus()
        self.scroll.get_vadjustment().set_value(0)

    def close(self, then=None) -> None:
        if not self.pad.get_visible():
            if then:
                then()
            return
        self.panel.remove_css_class("opening")
        self.panel.add_css_class("closing")

        def done():
            if self.panel.has_css_class("closing"):
                self.pad.set_visible(False)
            if then:
                then()
            return False
        GLib.timeout_add(ui.tokens.ms(CLOSE_MS), done)

    def escape(self) -> None:
        """One step back: search, tab, folder, jiggle, then close."""
        pad = self.pad
        if self.search.get_text():
            self.search.set_text("")
        elif pad.folder_view:
            pad._close_folder()
        elif pad.jiggling:
            pad.set_jiggle(False)
        elif self.tab is not None:
            self.set_tab(None)
        else:
            pad.close_launchpad()

    @staticmethod
    def _inside(widget, root, x, y) -> bool:
        ok, b = widget.compute_bounds(root)
        return ok and b.get_x() <= x <= b.get_x() + b.get_width() and b.get_y() <= y <= b.get_y() + b.get_height()

    def _outside(self, _g, _n, x, y) -> None:
        """A click outside the open folder closes the folder (Vini: only a
        click outside the whole menu did); outside the menu, the menu."""
        fv = self.pad.folder_view
        if fv:
            panel = fv[2]                               # the folder's own panel (its title row too)
            if not (self._inside(panel, self.root, x, y) or self._inside(fv[0].get_first_child(), self.root, x, y)):
                self.pad._close_folder()
            return
        if not self._inside(self.panel, self.root, x, y):
            self.pad.close_launchpad()

    def _menu(self, btn) -> None:
        options_menu(self.pad, btn)

    def set_style(self, s: str) -> None:
        set_style(self.pad, s)

    def _settings(self) -> None:
        open_style_settings(self.pad)

    # -- content ----------------------------------------------------------------------------------
    def _apps(self) -> dict:
        """The apps the full screen shows (Hidden ones only inside Hidden)."""
        shown = set(self.pad.model.all_apps())
        return {k: v for k, v in self.pad.installed.items() if k in shown}

    def _ordered(self) -> list:
        """Everything in the full screen's order: your arrangement, folders
        (an app in a folder shows only inside it) and Hidden last."""
        return [it for page in self.pad._pages_with_hidden() for it in page
                if M.is_folder(it) or it in self.pad.installed]

    def _groups(self) -> dict:
        """Category -> the apps on the grid (not those inside folders), by name."""
        on_grid = {it for page in self.pad.model.pages for it in page if not M.is_folder(it)}
        found = {k: v for k, v in self._apps().items() if k in on_grid}
        groups = {}
        for key, info in found.items():
            groups.setdefault(category_of(info), []).append(key)
        for keys in groups.values():
            keys.sort(key=lambda k: found[k].get_display_name().lower())
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

    def tile(self, item, section: str = ""):
        """Launchpad's tile for an app or folder (made once per section)."""
        from .launchpad import LaunchItem
        key = (section, id(item) if M.is_folder(item) else item)
        w = self.cache.get(key)
        if w is None or w.item is not item or (M.is_folder(item) and getattr(w, "_apps", None) != tuple(item["apps"])):
            w = self.cache[key] = LaunchItem(self.pad, item, ICON)
            w._apps = tuple(item["apps"]) if M.is_folder(item) else None
        elif M.is_folder(item) and w.name != item["folder"]:
            w.set_name_text(item["folder"])
        if hasattr(w, "badge"):
            w.badge.set_visible(self.pad.jiggling)
        w.set_vexpand(False)                           # (the full screen's cells stretch; rows here don't)
        w.set_halign(Gtk.Align.FILL)                   # every highlight the cell's size, whatever the name (Vini)
        self.tiles.append(w)
        return w

    def widgets_of(self, item) -> list:
        """Every tile showing this app / folder (live rename, drag targets)."""
        k = id(item) if M.is_folder(item) else item
        return [w for (_s, key), w in self.cache.items() if key == k]

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
        found = self._apps()
        q = self.search.get_text().strip()
        if q:
            from .spotlight import _keywords
            meta = {k: (v.get_display_name(), " ".join(filter(None, [
                apps._entry_field(v, "get_generic_name", "GenericName"), _keywords(v)])))
                for k, v in found.items()}
            keys = M.search(meta, q)
            if keys:
                self.body.append(flow([self.tile(k, "s") for k in keys], self.cols, self.pad))
                self._select(0)
            else:
                self.body.append(Gtk.Label(label="No Results", css_classes=["lpw-empty"]))
            return
        if self.tab is None:                           # Launchpad's own order (Vini); a tab sorts by category
            self.body.append(flow([self.tile(it, "all") for it in self._ordered()], self.cols, self.pad))
            return
        groups = self._groups()
        for cid, title in [(c[0], c[1]) for c in CATEGORIES] + [OTHER]:
            keys = groups.get(cid)
            if not keys or (self.tab is not None and cid != self.tab):
                continue
            self.body.append(Gtk.Label(label=title, xalign=0, css_classes=["lpw-section"]))
            self.body.append(flow([self.tile(k, cid) for k in keys], self.cols, self.pad))

    # -- making folders by dragging (the full screen's rules, without reordering) ----------------
    def drag_over(self, fb, x, y):
        pad = self.pad
        d = pad._drag
        if not d:
            return Gdk.DragAction.MOVE        # from the Dock: dropping here takes it out of the Dock
        if pad.leave_folder(d):                # out of Hidden: shown again
            return Gdk.DragAction.MOVE
        if d["folder"] is not None:            # dragged out of an open folder: out of it
            folder = d["folder"]
            pad._close_folder()
            last = len(pad.model.pages) - 1
            pad.model.take_out_of_folder(folder, d["item"], last, len(pad.model.pages[last]))
            d["folder"] = None
            pad.save()
            return Gdk.DragAction.MOVE
        picked = fb.pick(x, y, Gtk.PickFlags.DEFAULT)
        from .launchpad import LaunchItem
        while picked is not None and not isinstance(picked, LaunchItem):
            picked = picked.get_parent()
        if picked is None or picked.item is d["item"] or picked.item == d["item"]:
            pad._clear_target()
            return Gdk.DragAction.MOVE
        ok, b = picked.compute_bounds(fb)
        cx, cy = b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2
        if ok and not M.is_folder(d["item"]) and abs(x - cx) < b.get_width() * 0.3 \
                and abs(y - cy) < b.get_height() * 0.3:
            pad._cancel("reorder")
            d["pending"] = None
            if d["target"] is not picked:
                pad._clear_target()
                d["target"] = picked
                from .launchpad import FOLDER_HOLD_MS
                pad._timer("folder", FOLDER_HOLD_MS, lambda: picked.add_css_class("folder-target"))
            return Gdk.DragAction.MOVE
        pad._clear_target()
        # beside an icon: the dragged one moves there (Vini: icons couldn't be
        # arranged here) -- in your order only (no tab, no search), after a pause
        if ok and self.tab is None and not self.search.get_text().strip():
            want = (picked.item, x >= cx)
            if d.get("pending") != want:
                d["pending"] = want
                pad._cancel("reorder")
                from .launchpad import REORDER_HOLD_MS
                pad._timer("reorder", REORDER_HOLD_MS, lambda: self._reorder(want))
        return Gdk.DragAction.MOVE

    def _reorder(self, want) -> None:
        pad = self.pad
        d = pad._drag
        if not d or d.get("pending") != want:
            return
        d["pending"] = None
        target, after = want
        loc, src = pad._top_location(target), pad._top_location(d["item"])
        if loc is None or src is None:                # (Hidden, or inside a folder)
            return
        p, i = loc[0], loc[1] + (1 if after else 0)
        if src[0] == p and src[1] < i:
            i -= 1
        if (p, i) != src:
            pad.model.move(d["item"], p, i)
            pad.render()

    def drag_drop(self, value=None) -> bool:
        pad = self.pad
        if not pad._drag:                      # from the Dock
            folder = M.decode_folder(value)
            if folder is not None:             # a Dock folder: at the end of Launchpad
                last = len(pad.model.pages) - 1
                ok = pad.model.add_folder(folder["folder"], folder["apps"], last, len(pad.model.pages[last]),
                                          link=folder.get("link", "")) is not None
                if ok:
                    pad.save()
                    pad.render()
                return ok
            return isinstance(value, str) and value in pad.installed
        pad._drop_on_target()
        pad.save()
        pad.render()
        return True

    # -- keyboard ---------------------------------------------------------------------------------
    def _select(self, i: int) -> None:
        if 0 <= self.selected < len(self.tiles):
            self.tiles[self.selected].remove_css_class("selected")
        self.selected = i if 0 <= i < len(self.tiles) else -1
        if self.selected >= 0:
            self.tiles[self.selected].add_css_class("selected")

    def key(self, keyval) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.escape()
            return True
        if self.pad.folder_view:
            return False
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
            self.pad.activate_item(self.tiles[self.selected])
        elif self.tiles and self.search.get_text().strip():
            self.pad.activate_item(self.tiles[0])
