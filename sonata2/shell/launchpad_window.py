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

MenuView is only the layout (Vini: nothing duplicated, only the look
changes): Launchpad renders its pages into PageGrids stacked here instead of
its carousel, with its own tiles, search field, results, keys, drags
(reordering, folders, Hidden, the Dock), menus and folder panels
(shell/launchpad.py, mode "menu"). Only the category tabs are the menu's: a
tab shows that category's apps by name (again: back to your order).
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
ICON = 64
OPEN_MS, CLOSE_MS, FADE_MS = 220, 140, 150

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


class MenuView:
    """The Apps Menu layout of a Launchpad (`pad`). root: what the window
    shows in this mode; pad.render() fills its page grids (page_grids)."""

    def __init__(self, pad):
        from .launchpad import PageGrid
        self.pad = pad
        self.tab = None
        self.pages = []             # PageGrids of the Launchpad's pages, stacked (pad.render fills them)
        self.panel = ui.theme.glass_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                                  css_classes=["lpw-panel"], halign=Gtk.Align.CENTER,
                                                  valign=Gtk.Align.CENTER))
        self.head = Gtk.Box(spacing=10, css_classes=["lpw-head"])
        self.head.append(Gtk.Image(icon_name="view-app-grid-symbolic", css_classes=["lpw-glyph"]))
        more = Gtk.Button(child=Gtk.Label(label="\u2022\u2022\u2022", css_classes=["lpw-dots"]),
                          css_classes=["lpw-more"], can_focus=False, valign=Gtk.Align.CENTER,
                          tooltip_text="Options")
        more.connect("clicked", lambda b: options_menu(self.pad, b))
        self.more = more
        self.head.append(more)                         # (Launchpad's search field goes before it: take_search)
        self.panel.append(self.head)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, css_classes=["lpw-content"])
        self.content.append(Gtk.Box(css_classes=["lpw-rule"]))
        self.tabs = Gtk.Box(spacing=8, homogeneous=True)
        self.content.append(self.tabs)
        self.content.append(Gtk.Box(css_classes=["lpw-rule"]))
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_bottom=18,
                            valign=Gtk.Align.START, css_classes=["lpw-body"])
        self.scroll = Gtk.ScrolledWindow(child=self.body, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.content.append(self.scroll)
        self.panel.append(self.content)
        self.results = PageGrid(pad, -1)               # search results (pad._search_changed fills it)
        self.tab_grid = PageGrid(pad, -3)              # a category's apps (index < 0: no reordering)
        for g in (self.results, self.tab_grid):
            g.set_vexpand(False)
        # folder panels open over the menu (Launchpad's own, launchpad._host)
        self.overlay = Gtk.Overlay(child=self.panel, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.root = Gtk.Box(css_classes=["lpw-root"], hexpand=True, vexpand=True)
        self.overlay.set_hexpand(True)
        self.root.append(self.overlay)
        click = Gtk.GestureClick()
        click.connect("released", self._outside)
        self.root.add_controller(click)

    # -- Launchpad's search field, moved here while this layout shows -------------------------------
    def take_search(self) -> None:
        e = self.pad.search
        if e.get_parent() is not self.head:
            e.unparent()
            e.remove_css_class("lp-search")
            e.add_css_class("lpw-search")
            e.set_placeholder_text(names.APPS)
            e.set_hexpand(True)
            e.set_halign(Gtk.Align.FILL)
            e.set_margin_top(0)
            lens = e.get_first_child()                 # the title is the field: no magnifier in it
            if isinstance(lens, Gtk.Image):
                lens.set_visible(False)
            self.head.insert_child_after(e, self.head.get_first_child())

    def give_search(self) -> None:
        e = self.pad.search
        if e.get_parent() is self.head:
            self.head.remove(e)
            e.remove_css_class("lpw-search")
            e.add_css_class("lp-search")
            e.set_placeholder_text("Search")
            e.set_hexpand(False)
            e.set_halign(Gtk.Align.CENTER)
            e.set_margin_top(40)
            lens = e.get_first_child()
            if isinstance(lens, Gtk.Image):
                lens.set_visible(True)
            self.pad.col.prepend(e)

    # -- open / close -----------------------------------------------------------------------------
    def is_open(self) -> bool:
        return self.pad.get_visible() and not self.panel.has_css_class("closing")

    def _size(self, mon=None) -> None:
        """The panel sized for the display it opens on (the one its surface
        was last on, else the main display -- not always the first one)."""
        if mon is None:
            display = self.pad.get_display()
            surface = self.pad.get_surface()
            mon = display.get_monitor_at_surface(surface) if display and surface else None
        if mon is None:
            from . import monitors
            mon = monitors.main()
        w, h = (mon.get_geometry().width, mon.get_geometry().height) if mon else (1600, 1000)
        pw, ph = panel_size(w, h)
        self.panel.set_size_request(pw, ph)

    def open(self) -> None:
        self._size()
        self.pad.search.set_text("")
        self.tab = None
        self._build_tabs()
        self.pad.render()                              # (fills the pages and shows them)
        self.panel.remove_css_class("closing")
        self.panel.remove_css_class("opening")
        self.pad.present()
        surface = self.pad.get_surface()
        if surface is not None and getattr(self, "_mon_surface", None) is not surface:
            # the compositor puts it on the focused display: sized again for that one
            self._mon_surface = surface
            surface.connect("enter-monitor", lambda _s, m: self._size(m))
        self.panel.add_css_class("opening")
        self.pad.search.grab_focus()
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

    # -- what shows: the Launchpad's pages, a category, or the search results -------------------
    def page_grids(self, pages) -> list:
        """A PageGrid per Launchpad page (kept: tiles glide inside them); the
        last one only as tall as its apps (no empty rows at the end)."""
        from .launchpad import PageGrid
        out = []
        for i, page in enumerate(pages):
            rows = M.ROWS if i < len(pages) - 1 else max(1, -(-len(page) // M.COLS))
            g = self.pages[i] if i < len(self.pages) else None
            if g is None or g.rows != rows or g.cols != M.COLS:
                g = PageGrid(self.pad, i, M.COLS, rows)
                g.set_vexpand(False)
            out.append(g)
        self.pages = out
        return out

    def show(self) -> None:
        """Put the right grids in the body (pad.render / pad._search_changed /
        a tab call it); grids already there stay (their tiles keep gliding)."""
        if self.pad.search.get_text().strip():
            want = [self.results]
        elif self.tab is None:
            want = list(self.pages)
        else:
            want = [self.tab_grid]
        have = []
        c = self.body.get_first_child()
        while c is not None:
            have.append(c)
            c = c.get_next_sibling()
        if have == want:
            return
        for c in have:
            self.body.remove(c)
        for g in want:
            self.body.append(g)

    def reveal(self, widget) -> None:
        """Scroll so a tile chosen with the arrows is in view."""
        port = self.scroll.get_child()
        if isinstance(port, Gtk.Viewport) and hasattr(port, "scroll_to"):     # GTK 4.12+
            port.scroll_to(widget, None)
            return
        ok, b = widget.compute_bounds(self.body)
        if not ok:
            return
        adj = self.scroll.get_vadjustment()
        top, bottom = b.get_y(), b.get_y() + b.get_height()
        if top < adj.get_value():
            adj.set_value(top)
        elif bottom > adj.get_value() + adj.get_page_size():
            adj.set_value(bottom - adj.get_page_size())

    def visible_items(self) -> list:
        from .launchpad import LaunchItem
        grids = [self.results] if self.pad.search.get_text().strip() else \
            (self.pages if self.tab is None else [self.tab_grid])
        out = []
        for g in grids:
            for i in range(g.cols * g.rows):
                w = g.get_child_at(i % g.cols, i // g.cols)
                if isinstance(w, LaunchItem):
                    out.append(w)
        return out

    def _groups(self) -> dict:
        """Category -> the apps on the grid (not those inside folders), by name."""
        pad = self.pad
        on_grid = [it for page in pad.model.pages for it in page if not M.is_folder(it) and it in pad.installed]
        groups = {}
        for key in on_grid:
            groups.setdefault(category_of(pad.installed[key]), []).append(key)
        for keys in groups.values():
            keys.sort(key=lambda k: pad.installed[k].get_display_name().lower())
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
        from .launchpad import PageGrid
        self.tab = cid
        for c, b in self.tab_buttons.items():
            b.set_active(c == cid)
        if cid is not None:
            keys = self._groups().get(cid, [])
            rows = max(1, -(-len(keys) // M.COLS))
            if self.tab_grid.rows != rows or self.tab_grid.cols != M.COLS:
                self.tab_grid = PageGrid(self.pad, -3, M.COLS, rows)
                self.tab_grid.set_vexpand(False)
            self.tab_grid.fill([self.pad._item_widget(k) for k in keys])
        self.pad._select(-1)
        self.body.remove_css_class("lpw-body")         # the grids fade in
        GLib.idle_add(lambda: (self.body.add_css_class("lpw-body"), False)[1])
        if cid is None:
            self.pad.render()                          # your order again (the tiles came back from the tab)
        else:
            self.show()
        self.scroll.get_vadjustment().set_value(0)
