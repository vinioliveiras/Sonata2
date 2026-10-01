"""Music (macOS Music / iTunes): the library in ~/Music, played in one window.

Layout as in macOS Music: the glass toolbar is the player -- shuffle,
previous, play/pause, next, repeat (off / all / one) at the left, the "LCD"
in the middle (cover, title, artist — album, a thin scrubber with elapsed
and remaining time), volume and Up Next at the right. The translucent
sidebar has the search field, Library (Recently Added, Artists, Albums,
Songs) and Playlists (New Playlist; right-click a song > Add to Playlist).
The content shows album grids, the sortable Songs table, Artists (list +
the artist's albums) and album pages.

Double-click a song: it plays and the rest of the current view is queued.
Plays are counted when a song ends. Opening audio files (Files, the
desktop file's %F) queues them next and plays the first one.

Keys (Ctrl or Super act as ⌘): Space play/pause, ⌘→ / ⌘← next / previous,
⌘↑ / ⌘↓ volume, ⌘F search, ⌘L show the current song, ⌘N new playlist,
⌘W close. The library scan (library.py), tags (tags.py), queue (queue.py),
playback through Gtk.MediaFile (player.py) and the MPRIS server
(mpris.py) live beside this file."""
import os
from collections import OrderedDict

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from ..backend.system import run_async  # noqa: E402
from . import library as lib  # noqa: E402
from .player import NO_BACKEND, Player  # noqa: E402
from .queue import REPEAT_OFF, REPEAT_ONE, Queue  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.music"
DEFAULTS = {"volume": 0.8, "shuffle": False, "repeat": REPEAT_OFF, "view": "albums"}
MIME_TYPES = ("audio/mpeg", "audio/mp3", "audio/flac", "audio/x-flac", "audio/ogg", "audio/x-vorbis+ogg",
              "audio/vorbis", "audio/opus", "audio/x-opus+ogg", "audio/mp4", "audio/x-m4a", "audio/aac",
              "audio/x-aac", "audio/wav", "audio/x-wav", "audio/vnd.wave")
LIBRARY_VIEWS = (("recent", "Recently Added", "document-open-recent-symbolic"),
                 ("artists", "Artists", "audio-input-microphone-symbolic"),
                 ("albums", "Albums", "media-optical-symbolic"),
                 ("songs", "Songs", "music-note-symbolic"))
TITLES = {"recent": "Recently Added", "albums": "Albums", "songs": "Songs", "artists": "Artists"}

ui.register("""
window.sonata-music { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.mu-content { background: %(content_bg)s; }
.mu-paned > separator { min-width: 1px; background: %(content_bg)s; box-shadow: inset 1px 0 %(separator)s; }   /* opaque base: no see-through gap */

/* the player: the glass toolbar */
.sonata-toolbar.mu-player { min-height: 52px; padding: 0 10px; }
.mu-player button.tool { min-width: 30px; min-height: 30px; padding: 0 4px; color: %(label)s;
  transition: background-color %(t_fast)s, color %(t_fast)s, filter %(t_press)s; }
.mu-player button.tool:active { filter: brightness(0.6); background: %(tool_hover)s; transition: none; }
.mu-player button.tool image { -gtk-icon-size: 16px; }
.mu-player button.tool.mu-big image { -gtk-icon-size: 22px; }
.mu-player button.tool.mu-toggle { color: %(label_secondary)s; }
.mu-player button.tool.mu-toggle.on { color: %(accent)s; }
.mu-player button.tool:disabled { color: %(label_tertiary)s; }
.mu-player .mu-vol-icon { color: %(label_secondary)s; -gtk-icon-size: 14px; }
.mu-lcd { min-height: 40px; margin: 6px 0; border-radius: %(r_button)s; background: alpha(%(label)s, 0.04);
  box-shadow: inset 0 0 0 0.5px %(separator)s; }
.mu-lcd .mu-art { border-radius: %(r_button)s 0 0 %(r_button)s; box-shadow: none; }
.mu-lcd-title { font-size: 12px; font-weight: 600; color: %(label)s; }
.mu-lcd-sub { font-size: %(text_small)s; color: %(label_secondary)s; }
.mu-lcd-time { font-size: 9px; color: %(label_secondary)s; font-feature-settings: "tnum"; min-width: 34px; }
.mu-lcd-idle image { color: %(label_tertiary)s; -gtk-icon-size: 20px; }
scale.mu-scrub { padding: 0; margin: 0; min-height: 8px; }
scale.mu-scrub > trough { min-height: 3px; border-radius: 2px; background: alpha(%(label)s, 0.12); border: none;
  box-shadow: none; }
scale.mu-scrub > trough > highlight { min-height: 3px; border-radius: 2px; background: %(label_secondary)s;
  border: none; }
scale.mu-scrub > trough > slider { min-width: 0; min-height: 0; margin: 0; padding: 0; border: none;
  background: none; box-shadow: none; transition: min-width %(t_fast)s, min-height %(t_fast)s; }
.mu-lcd:hover scale.mu-scrub > trough > slider { min-width: 9px; min-height: 9px; margin: -4px;
  border-radius: 99px; background: %(knob)s; box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_knob)s; }

/* sidebar (source list) */
.mu-sidebar { padding-top: 10px; }
.mu-sidebar entry.search { margin: 0 10px 6px 10px; min-height: 26px; border-radius: %(r_menu)s;
  background: alpha(%(label)s, 0.06); box-shadow: none; border: none; color: %(label)s; }
.mu-sidebar list { background: none; padding: 0 10px 10px 10px; }
.mu-sidebar list row { min-height: 28px; padding: 0 6px; border-radius: %(r_menu)s; color: %(label)s;
  transition: background-color %(t_fast)s; }
.mu-sidebar list row:hover { background: none; }
.mu-sidebar list row:active { background: %(tool_hover)s; transition: background-color %(t_press)s; }
.mu-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.mu-sidebar list row.mu-head { min-height: 22px; margin-top: 10px; }
.mu-sidebar .mu-head label { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s; }
.mu-sidebar row image.mu-src { color: %(accent)s; }
.mu-sidebar row entry { min-height: 20px; padding: 0 4px; border-radius: 4px; background: %(content_bg)s;
  box-shadow: inset 0 0 0 1px %(accent)s, 0 0 0 3px alpha(%(accent)s, 0.3); }

/* pages */
.mu-title { font-family: %(font_display)s; font-size: 26px; font-weight: 700; color: %(label)s;
  padding: 18px 24px 10px 24px; }
.mu-page-head button { min-width: 26px; min-height: 26px; padding: 0; margin: 10px 0 0 16px; border-radius: %(r_button)s;
  background: none; border: none; box-shadow: none; color: %(tool_icon)s; }
.mu-page-head button:hover { background: %(tool_hover)s; }
.mu-art { background: alpha(%(label)s, 0.07); border-radius: %(r_label)s;
  box-shadow: 0 0 0 0.5px %(separator)s; }
.mu-art .mu-art-note { color: %(label_tertiary)s; }
gridview.mu-grid { background: none; padding: 0 16px 20px 16px; }
gridview.mu-grid > child { padding: 10px 10px 14px 10px; background: none; }
gridview.mu-grid > child .mu-art { transition: filter %(t_press)s; }
gridview.mu-grid > child:active .mu-art { filter: brightness(0.8); }
gridview.mu-grid > child:selected .mu-art { box-shadow: 0 0 0 3px %(accent)s; }
.mu-card-title { font-size: 12px; font-weight: 500; color: %(label)s; margin-top: 6px; }
.mu-card-artist { font-size: 12px; color: %(label_secondary)s; }

/* Songs table (Finder-like list) */
columnview.mu-songs { background: %(content_bg)s; font-size: 12px; }
columnview.mu-songs > header { background: %(content_bg)s; box-shadow: inset 0 -1px %(separator)s; }
columnview.mu-songs > header > button { padding: 3px 8px; font-size: %(text_small)s; font-weight: 400;
  color: %(label_secondary)s; background: none; border: none; box-shadow: inset -1px 0 %(separator)s;
  border-radius: 0; }
columnview.mu-songs > header > button:hover { background: %(tool_hover)s; }
columnview.mu-songs > header > button sort-indicator { color: %(label_secondary)s; -gtk-icon-size: 10px; }
columnview.mu-songs > listview { background: %(content_bg)s; }
columnview.mu-songs > listview > row { min-height: 26px; padding: 0; background: none; border-radius: 0;
  color: %(label)s; transition: background-color %(t_fast)s, color %(t_fast)s; }
columnview.mu-songs > listview > row:nth-child(even) { background: %(row_alt)s; }
columnview.mu-songs > listview > row:selected { background: %(accent_selected)s; color: %(label_on_accent)s; }
window:backdrop columnview.mu-songs > listview > row:selected { background: %(sidebar_selected)s; color: %(label)s; }
columnview.mu-songs > listview > row > cell { padding: 0 8px; }
columnview.mu-songs .mu-dim { color: %(label_secondary)s; font-feature-settings: "tnum"; }
columnview.mu-songs > listview > row:selected .mu-dim,
columnview.mu-songs > listview > row:selected .mu-now { color: inherit; }
.mu-now { color: %(accent)s; }

/* artists */
.mu-artists { background: %(content_bg)s; box-shadow: inset -1px 0 %(separator)s; }
.mu-artists listview { background: none; padding: 6px 8px; }
.mu-artists listview > row { min-height: 44px; padding: 0 8px; border-radius: %(r_label)s; color: %(label)s;
  transition: background-color %(t_fast)s; }
.mu-artists listview > row:selected { background: %(sidebar_selected)s; }
.mu-avatar { min-width: 32px; min-height: 32px; border-radius: 99px; font-size: 12px; font-weight: 600;
  color: %(label_on_accent)s; background: linear-gradient(to bottom, alpha(%(label)s, 0.22), alpha(%(label)s, 0.42)); }
.mu-artist-name { font-family: %(font_display)s; font-size: 26px; font-weight: 700; padding: 18px 28px 4px 28px; }

/* album page / album blocks */
.mu-album-head { padding: 24px 28px 18px 28px; }
.mu-album-name { font-family: %(font_display)s; font-size: 24px; font-weight: 700; color: %(label)s; }
.mu-block-name { font-size: %(text_title)s; font-weight: 700; color: %(label)s; }
.mu-album-artist { font-family: %(font_display)s; font-size: 20px; font-weight: 500; color: %(accent)s; }
.mu-album-meta { font-size: %(text_small)s; font-weight: 600; color: %(label_secondary)s; }
button.mu-pill { min-height: 28px; min-width: 96px; padding: 0 16px; border-radius: %(r_label)s; border: none;
  font-weight: 600; color: %(label_on_accent)s; background: %(accent)s; box-shadow: %(shadow_control)s;
  transition: filter %(t_press)s; }
button.mu-pill:hover { filter: brightness(1.06); }
button.mu-pill:active { filter: brightness(0.85); transition: none; }
list.mu-tracks { background: none; }
list.mu-tracks > row { min-height: 32px; padding: 0 10px; border-radius: %(r_label)s; color: %(label)s;
  transition: background-color %(t_fast)s; }
list.mu-tracks > row:nth-child(even) { background: %(row_alt)s; }
list.mu-tracks > row:selected { background: %(accent_selected)s; color: %(label_on_accent)s; }
list.mu-tracks > row:selected .mu-dim, list.mu-tracks > row:selected .mu-now { color: inherit; }
list.mu-tracks .mu-dim { color: %(label_secondary)s; font-feature-settings: "tnum"; }
.mu-foot { color: %(label_secondary)s; font-size: %(text_small)s; padding: 10px 10px 28px 10px; }

/* status */
.mu-banner { padding: 8px 12px 8px 16px; background: alpha(%(destructive)s, 0.10);
  box-shadow: inset 0 -1px %(separator)s; color: %(label)s; }
.mu-banner image { color: %(destructive)s; }
.mu-banner button { min-width: 22px; min-height: 22px; padding: 0; background: none; border: none;
  box-shadow: none; color: %(label_secondary)s; }
.mu-empty image { color: %(label_tertiary)s; }
.mu-empty-title { font-family: %(font_display)s; font-size: 20px; font-weight: 700; color: %(label)s; }
.mu-empty-body { color: %(label_secondary)s; }

/* Up Next */
.mu-upnext-list { background: none; }
.mu-upnext-list > row { min-height: 40px; padding: 2px 8px; border-radius: %(r_menu_row)s; color: %(label)s;
  transition: background-color %(t_fast)s; }
.mu-upnext-list > row:hover { background: alpha(%(label)s, 0.08); }
.mu-upnext-list label { font-weight: 400; }
.mu-upnext-list .mu-upnext-title { font-weight: 500; }
.mu-upnext-list .mu-art { border-radius: 4px; }
.mu-upnext-list .mu-dim { color: %(label_secondary)s; font-size: %(text_small)s; }
.mu-upnext-empty { color: %(label_secondary)s; padding: 16px; }
.mu-panel-btn { min-height: 20px; padding: 0 8px; border-radius: %(r_button)s; border: none; box-shadow: none;
  background: %(tool_hover)s; color: %(label)s; font-size: %(text_small)s; }
""", key="music")


# -- helpers ------------------------------------------------------------------------------
def fmt_time(seconds) -> str:
    """3:07, 1:02:03 (macOS Music)."""
    s = int(round(max(0, seconds or 0)))
    h, m, s = s // 3600, s // 60 % 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_total(seconds) -> str:
    """'48 minutes', '1 hour, 12 minutes'."""
    m = int(round((seconds or 0) / 60))
    if m < 60:
        return f"{m} minute{'s' if m != 1 else ''}"
    h, m = divmod(m, 60)
    return f"{h} hour{'s' if h != 1 else ''}, {m} minute{'s' if m != 1 else ''}"


def matches(query: str, *fields) -> bool:
    """Every word of the query appears in one of the fields."""
    if not query:
        return True
    hay = " ".join(str(f or "") for f in fields).casefold()
    return all(w in hay for w in query.casefold().split())


def _initials(name: str) -> str:
    words = [w for w in name.replace("&", " ").split() if w[:1].isalnum()]
    return "".join(w[0] for w in words[:2]).upper() or "♪"


_TEX = OrderedDict()


def texture(path: str):
    """Cover textures, decoded once (an LRU of the most recent 400)."""
    if not path:
        return None
    tex = _TEX.get(path)
    if tex is None:
        try:
            tex = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            tex = False
        _TEX[path] = tex
        if len(_TEX) > 400:
            _TEX.popitem(last=False)
    else:
        _TEX.move_to_end(path)
    return tex or None


class Art(Gtk.Widget):
    """Square cover art with rounded corners; a music note when there's none.
    fluid: at least `size`, as wide as its cell and as tall as wide (the
    album grid's cards fill the columns, like macOS Music)."""

    def __init__(self, size: int, fluid: bool = False):
        align = Gtk.Align.FILL if fluid else Gtk.Align.START
        super().__init__(css_classes=["mu-art"], overflow=Gtk.Overflow.HIDDEN, halign=align, valign=align)
        self.size, self.fluid = size, fluid
        self.inner = Gtk.Overlay()
        self.inner.set_parent(self)
        self.note = Gtk.Image(icon_name="music-note-symbolic", pixel_size=max(12, int(size * 0.36)),
                              css_classes=["mu-art-note"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.inner.set_child(self.note)
        self.pic = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, visible=False)
        self.inner.add_overlay(self.pic)

    def do_get_request_mode(self):
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH if self.fluid else Gtk.SizeRequestMode.CONSTANT_SIZE

    def do_measure(self, orientation, for_size):
        if self.fluid and orientation == Gtk.Orientation.VERTICAL and for_size > 0:
            return for_size, for_size, -1, -1
        return self.size, self.size, -1, -1

    def do_size_allocate(self, w, h, baseline):
        self.inner.allocate(w, h, baseline, None)

    def do_dispose(self):
        if self.inner is not None:
            self.inner.unparent()
            self.inner = None
        Gtk.Widget.do_dispose(self)

    def set_path(self, path: str) -> None:
        tex = texture(path)
        self.pic.set_paintable(tex)
        self.pic.set_visible(tex is not None)
        self.note.set_visible(tex is None)


class TrackItem(GObject.Object):
    def __init__(self, t: dict, index: int = -1):
        super().__init__()
        self.t = t
        self.index = index            # position in a playlist


class AlbumItem(GObject.Object):
    def __init__(self, a: dict):
        super().__init__()
        self.a = a


class ArtistItem(GObject.Object):
    def __init__(self, name: str):
        super().__init__()
        self.name = name


def _cmp(a, b) -> int:
    return (a > b) - (a < b)


def _fold(s) -> str:
    return (s or "").casefold()


# -- Songs table --------------------------------------------------------------------------
class SongTable:
    """The Songs view (and a playlist's): sortable columns Title, Time,
    Artist, Album, Genre, Plays; a speaker marks the song playing."""

    def __init__(self, win, sort_title: bool = True):
        self.win = win
        self.store = Gio.ListStore(item_type=TrackItem)
        self.filtered = Gtk.FilterListModel(model=self.store, filter=win.track_filter)
        self.view = Gtk.ColumnView(show_column_separators=False, show_row_separators=False, reorderable=True,
                                   css_classes=["mu-songs"])
        self.sorted = Gtk.SortListModel(model=self.filtered, sorter=self.view.get_sorter())
        self.selection = Gtk.MultiSelection(model=self.sorted)
        self.view.set_model(self.selection)
        self.view.connect("activate", lambda _v, pos: win.play_list(self.paths(), pos))
        self.bound_now, self.bound_plays = set(), set()
        self._column("", self._setup_now, self._bind_now, None, width=26, resizable=False,
                     unbind=lambda _f, it: self.bound_now.discard(it.get_child()))
        title = self._column("Title", self._setup_text, lambda _f, it: self._text(it, it.get_item().t.get("title")),
                             lambda t: _fold(t.get("title")), expand=True, dim=False)
        self._column("Time", lambda f, it: self._setup_text(f, it, xalign=1),
                     lambda _f, it: self._text(it, fmt_time(it.get_item().t.get("duration"))
                                               if it.get_item().t.get("duration") else ""),
                     lambda t: t.get("duration") or 0, width=64)
        self._column("Artist", self._setup_text, lambda _f, it: self._text(it, it.get_item().t.get("artist")),
                     lambda t: (_fold(t.get("artist")), _fold(t.get("album")), lib.track_sort_key(t)), width=180)
        self._column("Album", self._setup_text, lambda _f, it: self._text(it, it.get_item().t.get("album")),
                     lambda t: (_fold(t.get("album")), lib.track_sort_key(t)), width=200)
        self._column("Genre", self._setup_text, lambda _f, it: self._text(it, it.get_item().t.get("genre")),
                     lambda t: _fold(t.get("genre")), width=110)
        self._column("Plays", lambda f, it: self._setup_text(f, it, xalign=1), self._bind_plays,
                     lambda t: win.library.play_count(t["path"]), width=60,
                     unbind=lambda _f, it: self.bound_plays.discard(it.get_child()))
        if sort_title:
            self.view.sort_by_column(title, Gtk.SortType.ASCENDING)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._menu)
        self.view.add_controller(menu)
        self.widget = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True)

    def _column(self, title, setup, bind, key, expand=False, width=-1, resizable=True, unbind=None, dim=True):
        f = Gtk.SignalListItemFactory()
        f.connect("setup", lambda fa, it: setup(fa, it) if setup != self._setup_text else setup(fa, it, dim=dim))
        f.connect("bind", bind)
        if unbind:
            f.connect("unbind", unbind)
        sorter = Gtk.CustomSorter.new(lambda a, b, _d: _cmp(key(a.t), key(b.t))) if key else None
        col = Gtk.ColumnViewColumn(title=title, factory=f, expand=expand, resizable=resizable, sorter=sorter)
        if width > 0:
            col.set_fixed_width(width)
        self.view.append_column(col)
        return col

    def _setup_text(self, _f, item, xalign=0, dim=True):
        item.set_child(Gtk.Label(xalign=xalign, ellipsize=Pango.EllipsizeMode.END,
                                 css_classes=["mu-dim"] if dim else []))

    def _text(self, item, text):
        lbl = item.get_child()
        lbl.set_label(str(text or ""))
        lbl.mu_item = item.get_item()

    def _setup_now(self, _f, item):
        item.set_child(Gtk.Image(icon_name="audio-volume-high-symbolic", pixel_size=12, css_classes=["mu-now"]))

    def _bind_now(self, _f, item):
        img = item.get_child()
        img.path = item.get_item().t["path"]
        img.mu_item = item.get_item()
        img.set_opacity(1 if img.path == self.win.current_path() else 0)
        self.bound_now.add(img)

    def _bind_plays(self, _f, item):
        lbl = item.get_child()
        n = self.win.library.play_count(item.get_item().t["path"])
        self._text(item, n or "")
        self.bound_plays.add(lbl)

    def refresh_now(self) -> None:
        cur = self.win.current_path()
        for img in self.bound_now:
            img.set_opacity(1 if img.path == cur else 0)

    def refresh_plays(self) -> None:
        for lbl in self.bound_plays:
            n = self.win.library.play_count(lbl.mu_item.t["path"])
            lbl.set_label(str(n or ""))

    def set_tracks(self, tracks, indexed: bool = False) -> None:
        items = [TrackItem(t, i if indexed else -1) for i, t in enumerate(tracks)]
        self.store.splice(0, self.store.get_n_items(), items)

    def paths(self) -> list:
        return [self.sorted.get_item(i).t["path"] for i in range(self.sorted.get_n_items())]

    def selected_items(self) -> list:
        out = []
        bits = self.selection.get_selection()
        for n in range(bits.get_size()):
            it = self.sorted.get_item(bits.get_nth(n))
            if it is not None:
                out.append(it)
        return out

    def select_path(self, path: str) -> bool:
        for i in range(self.sorted.get_n_items()):
            if self.sorted.get_item(i).t["path"] == path:
                self.selection.select_item(i, True)
                self.view.scroll_to(i, None, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)
                return True
        return False

    def _menu(self, gesture, _n, x, y) -> None:
        w = self.view.pick(x, y, Gtk.PickFlags.DEFAULT)
        while w is not None and not hasattr(w, "mu_item"):
            w = w.get_parent()
        if w is None:
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        item = w.mu_item
        items = self.selected_items()
        if item not in items:
            for i in range(self.sorted.get_n_items()):
                if self.sorted.get_item(i) is item:
                    self.selection.select_item(i, True)
                    break
            items = [item]
        self.win.song_menu(self.view, items, (x, y), table=self)


# -- Album grid ---------------------------------------------------------------------------
class AlbumGrid:
    CARD = 150                     # smallest cover; cards grow to fill the columns

    def __init__(self, win):
        self.win = win
        self.model = Gtk.FilterListModel(filter=win.album_filter)
        f = Gtk.SignalListItemFactory()
        f.connect("setup", self._setup)
        f.connect("bind", self._bind)
        sel = Gtk.SingleSelection(model=self.model, autoselect=False, can_unselect=True)
        self.view = Gtk.GridView(model=sel, factory=f, min_columns=2, max_columns=12, single_click_activate=True,
                                 css_classes=["mu-grid"])
        self.view.connect("activate", lambda _v, pos: win.show_album(self.model.get_item(pos).a))
        self.widget = Gtk.ScrolledWindow(child=self.view, vexpand=True, hexpand=True,
                                         hscrollbar_policy=Gtk.PolicyType.NEVER)

    def set_store(self, store) -> None:
        self.model.set_model(store)

    def _setup(self, _f, item):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.START)
        box.art = Art(self.CARD, fluid=True)
        box.title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                              css_classes=["mu-card-title"])
        box.artist = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                               css_classes=["mu-card-artist"])
        for w in (box.art, box.title, box.artist):
            box.append(w)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda g, _n, x, y: (g.set_state(Gtk.EventSequenceState.CLAIMED),
                                                     self.win.album_menu(box, box.album, (x, y))))
        box.add_controller(menu)
        item.set_child(box)

    def _bind(self, _f, item):
        a, box = item.get_item().a, item.get_child()
        box.album = a
        box.art.set_path(a["thumb"])
        box.title.set_label(a["title"])
        box.artist.set_label(a["artist"])
        box.set_tooltip_text(f"{a['title']} — {a['artist']}")


# -- the window ---------------------------------------------------------------------------
class MusicWindow(Gtk.ApplicationWindow):
    def __init__(self, app, library=None, scan: bool = True, mpris: bool = True):
        if not GLib.get_application_name():
            GLib.set_application_name("Music")
        super().__init__(application=app, title="Music", default_width=1120, default_height=720)
        self.add_css_class("sonata-music")
        self.set_size_request(760, 460)
        ui.window.standard(self)
        self.cfg = config.load("music", DEFAULTS)
        self.library = library or lib.Library()
        self.queue = Queue()
        self.queue.shuffle = bool(self.cfg["shuffle"])
        self.queue.repeat = self.cfg["repeat"] if self.cfg["repeat"] in ("off", "all", "one") else REPEAT_OFF
        self.player = Player()
        self.player.set_volume(float(self.cfg["volume"]))
        self.player.on_state = self._state_changed
        self.player.on_position = self._position
        self.player.on_duration = self._duration
        self.player.on_ended = self._ended
        self.player.on_error = self._error
        self.extra = {}                   # opened files outside the library: path -> entry
        self.mpris = None
        self._ready = not scan            # the library has been read (cache or scan)
        self.query = ""
        self.view = None
        self._back = "albums"
        self._scanning = False
        self._rescan_src = 0
        self.album_blocks = []            # (path, number stack) of the album/artist page, for the speaker
        self.track_filter = Gtk.CustomFilter.new(lambda it: matches(
            self.query, it.t.get("title"), it.t.get("artist"), it.t.get("album"), it.t.get("genre")))
        self.album_filter = Gtk.CustomFilter.new(lambda it: matches(self.query, it.a["title"], it.a["artist"]))
        self.artist_filter = Gtk.CustomFilter.new(lambda it: matches(self.query, it.name))
        self.albums_store = Gio.ListStore(item_type=AlbumItem)
        self.recent_store = Gio.ListStore(item_type=AlbumItem)
        self.artists_store = Gio.ListStore(item_type=ArtistItem)

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self._player_bar())
        paned = Gtk.Paned(start_child=self._sidebar(), end_child=self._content(), shrink_start_child=False,
                          resize_start_child=False, shrink_end_child=False, css_classes=["mu-paned"], vexpand=True)
        paned.set_position(200)
        col.append(paned)
        self.set_child(col)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("close-request", self._closing)
        self._update_modes()
        self._update_lcd()
        if not self.player.available:
            self._show_banner(NO_BACKEND)
        if mpris:
            from .mpris import Server
            self.mpris = Server(self)
        self.show(self.cfg["view"] if self.cfg["view"] in TITLES else "albums")
        if scan:
            self.stack.set_visible_child_name("loading")
            run_async(lambda: (self.library.load_cache(), self.library.tracks)[1], self._cache_loaded)

    # -- building ---------------------------------------------------------------------------
    def _player_bar(self) -> Gtk.Widget:
        handle = ui.window.glass_toolbar(self, start=(
            ("media-playlist-shuffle-symbolic", "Shuffle", self.toggle_shuffle),
            ("media-skip-backward-symbolic", "Previous", self.previous),
            ("media-playback-start-symbolic", "Play", self.toggle),
            ("media-skip-forward-symbolic", "Next", self.next),
            ("media-playlist-repeat-symbolic", "Repeat", self.cycle_repeat)),
            end=(("view-list-bullet-symbolic", "Playing Next", self.up_next),))
        bar = handle.get_child()
        bar.add_css_class("mu-player")
        b = bar.get_start_widget().get_first_child()
        self.shuffle_btn, b = b, b.get_next_sibling()
        self.prev_btn, b = b, b.get_next_sibling()
        self.play_btn, b = b, b.get_next_sibling()
        self.next_btn, self.repeat_btn = b, b.get_next_sibling()
        self.play_btn.add_css_class("mu-big")
        for t in (self.shuffle_btn, self.repeat_btn):
            t.add_css_class("mu-toggle")
        end = bar.get_end_widget()
        self.upnext_btn = end.get_first_child()
        # room from the LCD, and the same gap either side of the slider
        vol = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_start=16, margin_end=10)
        vol.append(Gtk.Image(icon_name="audio-volume-low-symbolic", css_classes=["mu-vol-icon"], valign=Gtk.Align.CENTER))
        self._vol_guard = False
        self.volume = ui.controls.slider(self.player.volume * 100, self._volume_slid, lower=0, upper=100)
        self.volume.set_size_request(92, -1)
        self.volume.set_hexpand(False)
        vol.append(self.volume)
        vol.append(Gtk.Image(icon_name="audio-volume-high-symbolic", css_classes=["mu-vol-icon"], valign=Gtk.Align.CENTER))
        end.prepend(vol)
        bar.set_center_widget(self._lcd())
        return handle

    def _lcd(self) -> Gtk.Widget:
        lcd = Gtk.Box(css_classes=["mu-lcd"], valign=Gtk.Align.CENTER, overflow=Gtk.Overflow.HIDDEN)
        lcd.set_size_request(460, 40)
        self.lcd_art = Art(40)
        lcd.append(self.lcd_art)
        self.lcd_stack = Gtk.Stack(hexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE)
        idle = Gtk.Box(css_classes=["mu-lcd-idle"], halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        idle.append(Gtk.Image(icon_name="music-note-symbolic"))
        self.lcd_stack.add_named(idle, "idle")
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, margin_start=8, margin_end=8)
        self.lcd_title = Gtk.Label(css_classes=["mu-lcd-title"], ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                                   hexpand=True)
        self.lcd_sub = Gtk.Label(css_classes=["mu-lcd-sub"], ellipsize=Pango.EllipsizeMode.END, max_width_chars=1,
                                 hexpand=True)
        row = Gtk.Box(spacing=6)
        self.lcd_elapsed = Gtk.Label(css_classes=["mu-lcd-time"], xalign=0)
        self.lcd_remaining = Gtk.Label(css_classes=["mu-lcd-time"], xalign=1)
        self.scrub = ui.controls.slider(0, None, lower=0, upper=1)
        self.scrub.add_css_class("mu-scrub")
        self.scrub.remove_css_class("sonata-slider")
        self.scrub.set_valign(Gtk.Align.CENTER)
        self.scrub.connect("change-value", self._scrubbed)
        row.append(self.lcd_elapsed)
        row.append(self.scrub)
        row.append(self.lcd_remaining)
        for w in (self.lcd_title, self.lcd_sub, row):
            info.append(w)
        self.lcd_stack.add_named(info, "track")
        lcd.append(self.lcd_stack)
        click = Gtk.GestureClick()
        click.connect("released", lambda _g, n, x, y: n == 1 and y < 26 and self.show_current())
        info.add_controller(click)
        return lcd

    def _sidebar(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["mu-sidebar", "sonata-sidebar"])
        box.set_size_request(180, -1)
        self.search = Gtk.SearchEntry(placeholder_text="Search", css_classes=["search"])
        self.search.connect("search-changed", lambda e: self.set_query(e.get_text()))
        self.search.connect("stop-search", lambda e: e.set_text(""))
        box.append(self.search)
        self.src = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.src.connect("row-activated", self._source_activated)
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", self._source_menu)
        self.src.add_controller(menu)
        box.append(Gtk.ScrolledWindow(child=self.src, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self._rebuild_sources()
        return box

    def _source_row(self, key, title, icon) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.key = key
        b = Gtk.Box(spacing=8)
        b.append(Gtk.Image(icon_name=icon, css_classes=["mu-src"]))
        row.label_stack = Gtk.Stack(hexpand=True)
        row.label = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END)
        row.label_stack.add_named(row.label, "label")
        b.append(row.label_stack)
        row.set_child(b)
        self.src.append(row)
        return row

    def _head(self, text) -> None:
        row = Gtk.ListBoxRow(selectable=False, activatable=False, css_classes=["mu-head"])
        row.key = None
        row.set_child(Gtk.Label(label=text, xalign=0, margin_start=2))
        self.src.append(row)

    def _rebuild_sources(self) -> None:
        self.src.remove_all()
        self._head("Library")
        for key, title, icon in LIBRARY_VIEWS:
            self._source_row(key, title, icon)
        self._head("Playlists")
        for p in self.library.playlists:
            self._source_row("playlist:" + p["name"], p["name"], "view-list-bullet-symbolic")
        self._source_row("new-playlist", "New Playlist", "list-add-symbolic")
        self._select_source(self.view)

    def _select_source(self, key) -> None:
        row = self.src.get_first_child()
        while row is not None:
            if getattr(row, "key", None) == key:
                self.src.select_row(row)
                return
            row = row.get_next_sibling()

    def _content(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["mu-content"], hexpand=True)
        self.banner = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        bb = Gtk.Box(spacing=10, css_classes=["mu-banner"])
        bb.append(Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.START))
        self.banner_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        bb.append(self.banner_label)
        close = Gtk.Button(icon_name="window-close-symbolic", valign=Gtk.Align.START, tooltip_text="Close")
        close.connect("clicked", lambda *_: self.banner.set_reveal_child(False))
        bb.append(close)
        self.banner.set_child(bb)
        box.append(self.banner)
        self.stack = Gtk.Stack(vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=ui.tokens.ms(150))
        box.append(self.stack)
        # albums grid (Albums, Recently Added)
        self.grid = AlbumGrid(self)
        self.grid_title = Gtk.Label(xalign=0, css_classes=["mu-title"])
        self.stack.add_named(self._page(self.grid_title, self.grid.widget), "grid")
        # songs
        self.songs = SongTable(self)
        self.stack.add_named(self._page(Gtk.Label(label="Songs", xalign=0, css_classes=["mu-title"]),
                                        self.songs.widget), "songs")
        # playlist
        self.plist = SongTable(self, sort_title=False)
        self.plist_title = Gtk.Label(xalign=0, css_classes=["mu-title"])
        self.plist_empty = self._status("view-list-bullet-symbolic", "No Songs",
                                        "Right-click a song and choose Add to Playlist.")
        over = Gtk.Overlay(child=self.plist.widget, vexpand=True)
        over.add_overlay(self.plist_empty)
        self.plist_empty.set_can_target(False)
        self.stack.add_named(self._page(self.plist_title, over), "playlist")
        # artists
        self.stack.add_named(self._artists_page(), "artists")
        # album page
        self.album_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.stack.add_named(self.album_scroll, "album")
        # status pages
        loading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                          valign=Gtk.Align.CENTER, css_classes=["mu-empty"])
        loading.append(ui.progress.spinner())
        loading.append(Gtk.Label(label="Loading Library…", css_classes=["mu-empty-body"]))
        self.stack.add_named(loading, "loading")
        self.stack.add_named(self._status("folder-music-symbolic", "Your library is empty",
                                          f"Add songs to your Music folder ({self._pretty(self.library.root)}) "
                                          "and they'll appear here."), "empty")
        return box

    @staticmethod
    def _pretty(path: str) -> str:
        home = GLib.get_home_dir()
        return "~" + path[len(home):] if path.startswith(home) else path

    @staticmethod
    def _status(icon, title, body) -> Gtk.Widget:
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                    valign=Gtk.Align.CENTER, css_classes=["mu-empty"])
        b.append(Gtk.Image(icon_name=icon, pixel_size=56, margin_bottom=6))
        b.append(Gtk.Label(label=title, css_classes=["mu-empty-title"]))
        body_l = Gtk.Label(label=body, css_classes=["mu-empty-body"], wrap=True, justify=Gtk.Justification.CENTER,
                           max_width_chars=48)
        b.append(body_l)
        return b

    @staticmethod
    def _page(title: Gtk.Widget, body: Gtk.Widget) -> Gtk.Widget:
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        b.append(title)
        b.append(body)
        return b

    def _artists_page(self) -> Gtk.Widget:
        filt = Gtk.FilterListModel(model=self.artists_store, filter=self.artist_filter)
        self.artist_sel = Gtk.SingleSelection(model=filt, autoselect=True)
        f = Gtk.SignalListItemFactory()

        def setup(_f, item):
            b = Gtk.Box(spacing=10)
            b.avatar = Gtk.Label(css_classes=["mu-avatar"], valign=Gtk.Align.CENTER)
            b.name = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, hexpand=True)
            b.append(b.avatar)
            b.append(b.name)
            item.set_child(b)

        def bind(_f, item):
            name, b = item.get_item().name, item.get_child()
            b.avatar.set_label(_initials(name))
            b.name.set_label(name)
        f.connect("setup", setup)
        f.connect("bind", bind)
        lv = Gtk.ListView(model=self.artist_sel, factory=f)
        self.artist_sel.connect("notify::selected-item", lambda s, _p: self._artist_selected())
        left = Gtk.ScrolledWindow(child=lv, hscrollbar_policy=Gtk.PolicyType.NEVER, css_classes=["mu-artists"])
        left.set_size_request(230, -1)
        self.artist_scroll = Gtk.ScrolledWindow(hexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box = Gtk.Box()
        box.append(left)
        box.append(self.artist_scroll)
        return box

    # -- library ----------------------------------------------------------------------------
    def _cache_loaded(self, _tracks) -> None:
        if self.library.tracks:
            self._ready = True
            self.populate()
        self.rescan()
        self.library.watch(self._fs_changed)

    def rescan(self) -> None:
        if self._scanning:
            self._rescan_src = self._rescan_src or GLib.timeout_add_seconds(3, self._rescan_later)
            return
        self._scanning = True

        def done(tracks):
            self._scanning = False
            self._ready = True
            if (tracks is not None and self.library.apply(tracks)) or \
                    self.stack.get_visible_child_name() in ("loading", "empty"):
                self.populate()
        run_async(self.library.rescan, done)

    def _rescan_later(self) -> bool:
        self._rescan_src = 0
        self.rescan()
        return False

    def _fs_changed(self) -> None:
        if not self._rescan_src:
            self._rescan_src = GLib.timeout_add_seconds(2, self._rescan_later)

    def populate(self) -> None:
        """Fill every view from the library (after a load or a scan)."""
        songs = self.library.songs()
        albums = self.library.albums()
        self.songs.set_tracks(songs)
        self.albums_store.splice(0, self.albums_store.get_n_items(), [AlbumItem(a) for a in albums])
        recent = sorted(albums, key=lambda a: -a["added"])[:60]
        self.recent_store.splice(0, self.recent_store.get_n_items(), [AlbumItem(a) for a in recent])
        self.artists_store.splice(0, self.artists_store.get_n_items(),
                                  [ArtistItem(n) for n in self.library.artists()])
        if self.view and self.view.startswith("playlist:"):
            self._fill_playlist(self.view[9:])
        if self.stack.get_visible_child_name() in ("loading", "empty"):
            self.show(self.view or "albums")
        elif self.view == "album" and getattr(self, "_album", None):
            key = self._album["key"]
            a = next((x for x in albums if x["key"] == key), None)
            self.show_album(a) if a else self.show("albums")

    def track(self, path: str):
        return self.library.tracks.get(path) or self.extra.get(path)

    def current_path(self):
        return self.queue.current if self.player.path else None

    def current_track(self):
        p = self.current_path()
        return self.track(p) if p else None

    # -- views ------------------------------------------------------------------------------
    def show(self, view: str) -> None:
        if view == "new-playlist":
            self.new_playlist()
            return
        self.view = view
        if not self.library.tracks and not view.startswith("playlist:"):
            self.stack.set_visible_child_name("empty" if self._ready else "loading")
            self._select_source(view)
            return
        if view in ("albums", "recent"):
            self.grid.set_store(self.albums_store if view == "albums" else self.recent_store)
            self.grid_title.set_label(TITLES[view])
            self.stack.set_visible_child_name("grid")
        elif view == "songs":
            self.stack.set_visible_child_name("songs")
        elif view == "artists":
            self.stack.set_visible_child_name("artists")
            self._artist_selected()
        elif view.startswith("playlist:"):
            self._fill_playlist(view[9:])
            self.stack.set_visible_child_name("playlist")
        if view in TITLES and view != self.cfg.get("view"):
            self.cfg["view"] = view
            config.update("music", view=view)
        self._select_source(view)

    def _source_activated(self, _lb, row) -> None:
        if row.key:
            self.show(row.key)

    def _fill_playlist(self, name: str) -> None:
        self.plist_title.set_label(name)
        self.plist.set_tracks(self.library.playlist_tracks(name), indexed=True)
        self.plist_empty.set_visible(self.plist.store.get_n_items() == 0)

    def set_query(self, text: str) -> None:
        self.query = text.strip()
        for f in (self.track_filter, self.album_filter, self.artist_filter):
            f.changed(Gtk.FilterChange.DIFFERENT)
        if self.query and self.view == "album":
            self.show("songs")

    def show_album(self, a: dict, select_path: str = None) -> None:
        if a is None:
            return
        if self.view != "album":
            self._back = self.view or "albums"
        self.view = "album"
        self._album = a
        self.album_blocks = []
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        head = Gtk.Box(css_classes=["mu-page-head"])
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text="Back")
        back.connect("clicked", lambda *_: self.show(self._back))
        head.append(back)
        page.append(head)
        top = Gtk.Box(spacing=24, css_classes=["mu-album-head"])
        art = Art(220)
        art.set_path(a["thumb"] or a["art"])
        top.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.END, hexpand=True)
        info.append(Gtk.Label(label=a["title"], xalign=0, wrap=True, css_classes=["mu-album-name"]))
        artist = Gtk.Label(label=a["artist"], xalign=0, css_classes=["mu-album-artist"])
        info.append(artist)
        meta = " · ".join(x for x in ((a["genre"] or "").upper(), str(a["year"] or "")) if x)
        info.append(Gtk.Label(label=meta, xalign=0, css_classes=["mu-album-meta"], margin_bottom=14))
        btns = Gtk.Box(spacing=12)
        btns.append(self._pill("media-playback-start-symbolic", "Play", lambda: self.play_album(a)))
        btns.append(self._pill("media-playlist-shuffle-symbolic", "Shuffle", lambda: self.play_album(a, True)))
        info.append(btns)
        top.append(info)
        page.append(top)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_start=18, margin_end=28)
        lb = self._track_list(a["tracks"], a["artist"], select_path)
        body.append(lb)
        total = sum(t.get("duration") or 0 for t in a["tracks"])
        n = len(a["tracks"])
        foot = f"{n} song{'s' if n != 1 else ''}" + (f", {fmt_total(total)}" if total else "")
        body.append(Gtk.Label(label=foot, xalign=0, css_classes=["mu-foot"]))
        page.append(body)
        self.album_scroll.set_child(page)
        self.album_scroll.get_vadjustment().set_value(0)
        self.stack.set_visible_child_name("album")
        self.src.unselect_all()

    def _pill(self, icon, label, cb) -> Gtk.Button:
        b = Gtk.Button(css_classes=["mu-pill"])
        box = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        box.append(Gtk.Image(icon_name=icon))
        box.append(Gtk.Label(label=label))
        b.set_child(box)
        b.connect("clicked", lambda *_: cb())
        return b

    def _track_list(self, tracks, album_artist, select_path=None) -> Gtk.ListBox:
        """An album's songs: number (a speaker while playing), title, the
        artist when it differs from the album's, time. Double-click plays."""
        lb = Gtk.ListBox(css_classes=["mu-tracks"], selection_mode=Gtk.SelectionMode.MULTIPLE,
                         activate_on_single_click=False)
        paths = [t["path"] for t in tracks]
        cur = self.current_path()
        for t in tracks:
            row = Gtk.ListBoxRow()
            row.mu_item = TrackItem(t)
            b = Gtk.Box(spacing=12)
            num = Gtk.Stack()
            num.set_size_request(24, -1)
            num.add_named(Gtk.Label(label=str(t.get("track") or ""), xalign=1, css_classes=["mu-dim"]), "n")
            num.add_named(Gtk.Image(icon_name="audio-volume-high-symbolic", pixel_size=12, halign=Gtk.Align.END,
                                    css_classes=["mu-now"]), "now")
            num.set_visible_child_name("now" if t["path"] == cur else "n")
            self.album_blocks.append((t["path"], num))
            b.append(num)
            b.append(Gtk.Label(label=t.get("title") or "", xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            if t.get("artist") and t.get("artist") != album_artist:
                b.append(Gtk.Label(label=t["artist"], xalign=1, ellipsize=Pango.EllipsizeMode.END,
                                   css_classes=["mu-dim"], max_width_chars=28))
            b.append(Gtk.Label(label=fmt_time(t.get("duration")) if t.get("duration") else "", xalign=1,
                               css_classes=["mu-dim"], width_chars=6))
            row.set_child(b)
            lb.append(row)
            if select_path and t["path"] == select_path:
                lb.select_row(row)
                GLib.idle_add(lambda r=row: (r.grab_focus(), False)[1])
        lb.connect("row-activated", lambda _l, r: self.play_list(paths, r.get_index()))
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)

        def pressed(g, _n, x, y):
            row = lb.get_row_at_y(int(y))
            if row is None:
                return
            g.set_state(Gtk.EventSequenceState.CLAIMED)
            if not row.is_selected():
                lb.unselect_all()
                lb.select_row(row)
            items = [r.mu_item for r in lb.get_selected_rows()]
            self.song_menu(lb, items, (x, y))
        menu.connect("pressed", pressed)
        lb.add_controller(menu)
        return lb

    def _artist_selected(self) -> None:
        it = self.artist_sel.get_selected_item()
        self.album_blocks = []
        if it is None:
            self.artist_scroll.set_child(None)
            return
        name = it.name
        tracks = [t for t in self.library.tracks.values() if lib.artist_of(t) == name or t.get("artist") == name]
        albums = sorted(lib.group_albums(tracks), key=lambda a: (-(a["year"] or 0), a["title"].casefold()))
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        page.append(Gtk.Label(label=name, xalign=0, css_classes=["mu-artist-name"]))
        for a in albums:
            block = Gtk.Box(spacing=22, margin_start=28, margin_end=28, margin_top=18)
            art = Art(150)
            art.set_path(a["thumb"])
            click = Gtk.GestureClick()
            click.connect("released", lambda *_a, al=a: self.show_album(al))
            art.add_controller(click)
            art.set_cursor(Gdk.Cursor.new_from_name("pointer"))
            block.append(art)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            col.append(Gtk.Label(label=a["title"], xalign=0, css_classes=["mu-block-name"], wrap=True))
            meta = " · ".join(x for x in ((a["genre"] or "").upper(), str(a["year"] or "")) if x)
            if meta:
                col.append(Gtk.Label(label=meta, xalign=0, css_classes=["mu-album-meta"], margin_bottom=6))
            col.append(self._track_list(a["tracks"], a["artist"]))
            block.append(col)
            page.append(block)
        page.append(Gtk.Box(margin_bottom=28))
        self.artist_scroll.set_child(page)

    # -- menus ------------------------------------------------------------------------------
    def _playlist_submenu(self, paths) -> list:
        Item = ui.menu.Item
        return [[Item("New Playlist", lambda: self.new_playlist(paths))],
                [Item(p["name"], lambda n=p["name"]: self.add_to_playlist(n, paths))
                 for p in self.library.playlists]]

    def song_menu(self, widget, items, at, table=None) -> None:
        Item = ui.menu.Item
        paths = [it.t["path"] for it in items]
        first = items[0].t
        album = self._album_of(first)
        sections = [
            [Item("Play", lambda: self.play_list(paths, 0)), Item("Play Next", lambda: self.play_next(paths)),
             Item("Play Later", lambda: self.play_later(paths))],
            [Item("Add to Playlist", submenu=[s for s in self._playlist_submenu(paths) if s])],
            [Item("Show Album", lambda: self.show_album(album, first["path"]), enabled=album is not None),
             Item("Show Artist", lambda: self.show_artist(lib.artist_of(first)))],
            [Item("Show in Files", lambda: self._reveal(first["path"]))],
        ]
        if table is self.plist and self.view and self.view.startswith("playlist:"):
            name = self.view[9:]
            idx = [it.index for it in items if it.index >= 0]
            sections.insert(2, [Item("Remove from Playlist", lambda: self._remove_from_playlist(name, idx))])
        ui.menu.popup(widget, sections, at=at, glass=True, passthrough=True)

    def album_menu(self, widget, a, at) -> None:
        Item = ui.menu.Item
        paths = [t["path"] for t in a["tracks"]]
        ui.menu.popup(widget, [
            [Item("Play", lambda: self.play_album(a)), Item("Shuffle", lambda: self.play_album(a, True)),
             Item("Play Next", lambda: self.play_next(paths)), Item("Play Later", lambda: self.play_later(paths))],
            [Item("Add to Playlist", submenu=[s for s in self._playlist_submenu(paths) if s])],
            [Item("Show Artist", lambda: self.show_artist(a["artist"]))],
        ], at=at, glass=True, passthrough=True)

    def _source_menu(self, gesture, _n, x, y) -> None:
        row = self.src.get_row_at_y(int(y))
        key = getattr(row, "key", None) if row else None
        if not key or not key.startswith("playlist:"):
            return
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        name = key[9:]
        Item = ui.menu.Item
        ui.menu.popup(self.src, [
            [Item("Play", lambda: self.play_list([t["path"] for t in self.library.playlist_tracks(name)], 0)),
             Item("Shuffle", lambda: self.play_list([t["path"] for t in self.library.playlist_tracks(name)], 0,
                                                     shuffle=True))],
            [Item("Rename", lambda: self.rename_playlist(name)),
             Item("Delete Playlist", lambda: self.delete_playlist(name))],
        ], at=(x, y), glass=True, passthrough=True)

    def _reveal(self, path: str) -> None:
        try:
            Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SESSION, Gio.DBusProxyFlags.NONE, None,
                                           "org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                           "org.freedesktop.FileManager1", None).call(
                "ShowItems", GLib.Variant("(ass)", ([Gio.File.new_for_path(path).get_uri()], "")),
                Gio.DBusCallFlags.NONE, 2000, None, None)
        except GLib.Error:
            Gio.AppInfo.launch_default_for_uri(Gio.File.new_for_path(os.path.dirname(path)).get_uri(), None)

    def _album_of(self, t: dict):
        key = lib.album_key(t)
        it = next((self.albums_store.get_item(i) for i in range(self.albums_store.get_n_items())
                   if self.albums_store.get_item(i).a["key"] == key), None)
        if it is not None:
            return it.a
        albums = lib.group_albums([x for x in self.library.tracks.values() if lib.album_key(x) == key] or [t])
        return albums[0] if albums else None

    def show_artist(self, name: str) -> None:
        self.search.set_text("")
        self.show("artists")
        filt = self.artist_sel.get_model()
        for i in range(filt.get_n_items()):
            if filt.get_item(i).name == name:
                self.artist_sel.set_selected(i)
                break

    def show_current(self) -> None:
        """⌘L: the album of the song playing, with the song selected."""
        t = self.current_track()
        if t is None:
            return
        self.search.set_text("")
        self.show_album(self._album_of(t), t["path"])

    # -- playlists --------------------------------------------------------------------------
    def new_playlist(self, paths=()) -> None:
        p = self.library.new_playlist("Untitled Playlist", paths)
        self._rebuild_sources()
        self.show("playlist:" + p["name"])
        self.rename_playlist(p["name"])

    def add_to_playlist(self, name: str, paths) -> None:
        self.library.add_to_playlist(name, paths)
        if self.view == "playlist:" + name:
            self._fill_playlist(name)

    def _remove_from_playlist(self, name: str, indices) -> None:
        self.library.remove_from_playlist(name, indices)
        self._fill_playlist(name)

    def rename_playlist(self, name: str) -> None:
        """Rename in place: the sidebar row's label becomes a field."""
        row = self.src.get_first_child()
        while row is not None and getattr(row, "key", None) != "playlist:" + name:
            row = row.get_next_sibling()
        if row is None:
            return
        entry = Gtk.Entry(text=name)
        row.label_stack.add_named(entry, "entry")
        row.label_stack.set_visible_child(entry)
        done = {"v": False}

        def finish(commit: bool):
            if done["v"]:
                return
            done["v"] = True
            new = entry.get_text().strip()
            if commit and new and new != name and not self.library.playlist(new):
                was = self.view == "playlist:" + name
                self.library.rename_playlist(name, new)
                self.view = "playlist:" + new if was else self.view
            GLib.idle_add(lambda: (self._rebuild_sources(), self.view and self.view.startswith("playlist:")
                                   and self._fill_playlist(self.view[9:]), False)[2])
        entry.connect("activate", lambda *_: finish(True))
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, *_a: (finish(False), True)[1] if kv == Gdk.KEY_Escape else False)
        entry.add_controller(keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: finish(True))
        entry.add_controller(focus)
        entry.grab_focus()

    def delete_playlist(self, name: str) -> None:
        def answer(rid):
            if rid == "delete":
                self.library.delete_playlist(name)
                if self.view == "playlist:" + name:
                    self.view = "songs"
                self._rebuild_sources()
                self.show(self.view)
        ui.dialog.alert(f"Are you sure you want to delete the playlist “{name}”?",
                        "The songs stay in your library.",
                        [("cancel", "Cancel", "default"), ("delete", "Delete", "destructive")], answer, parent=self)

    # -- playback ---------------------------------------------------------------------------
    def play_list(self, paths, index: int = 0, shuffle: bool = None) -> None:
        """Play paths[index] and queue the rest of the list (a double-click)."""
        if not paths:
            return
        if shuffle is not None and shuffle != self.queue.shuffle:
            self.queue.shuffle = shuffle
            self._save_modes()
        if shuffle:
            index = self.queue.rng.randrange(len(paths))
        self.queue.set(paths, index)
        self._load_current()

    def play_album(self, a: dict, shuffle: bool = False) -> None:
        self.play_list([t["path"] for t in a["tracks"]], 0, shuffle=True if shuffle else None)

    def play_next(self, paths) -> None:
        empty = self.queue.current is None
        self.queue.play_next(paths)
        if empty:
            self._load_current()
        self._notify()

    def play_later(self, paths) -> None:
        empty = self.queue.current is None
        self.queue.append(paths)
        if empty:
            self._load_current()
        self._notify()

    def open_files(self, paths) -> None:
        """Opened from Files: queue the files next and play the first."""
        paths = [p for p in paths if p and os.path.isfile(p)]
        if not paths:
            return
        for p in paths:
            if p not in self.library.tracks and p not in self.extra:
                try:
                    self.extra[p] = lib.read_track(p, None, art_folder=self.library.art)
                except OSError:
                    continue
        paths = [p for p in paths if self.track(p)]
        had = self.queue.current is not None and self.player.path is not None
        self.queue.play_next(paths)
        if had:
            self.queue.jump(1)
        self._load_current()

    def open_uris(self, uris) -> None:
        self.open_files([Gio.File.new_for_uri(u).get_path() for u in uris])

    def _load_current(self) -> None:
        path = self.queue.current
        if path is None:
            self.player.stop()
            return
        self.player.load(path)
        self._song_changed()

    def toggle(self) -> None:
        if self.player.path is None:
            if self.queue.current is not None:
                self._load_current()
            else:
                paths = self.songs.paths()
                if paths:
                    self.play_list(paths, 0)
            return
        self.player.toggle()

    def play(self) -> None:
        if not self.player.playing:
            self.toggle()

    def pause(self) -> None:
        self.player.pause()

    def stop(self) -> None:
        self.player.stop()
        self._song_changed()

    def next(self) -> None:
        if self.queue.next() is not None:
            self._load_current()
        else:
            self.stop()

    def previous(self) -> None:
        if self.player.path and self.player.position > 3:
            self.seek_to(0)
            return
        if self.queue.previous() is not None:
            self._load_current()

    def can_next(self) -> bool:
        return self.queue.current is not None and (bool(self.queue.upcoming()) or self.queue.repeat != REPEAT_OFF)

    def seek_to(self, seconds: float) -> None:
        self.player.seek(seconds)
        if self.mpris:
            self.mpris.seeked(seconds)

    def set_volume(self, v: float) -> None:
        self.player.set_volume(v)
        self._vol_guard = True
        self.volume.set_value(self.player.volume * 100)
        self._vol_guard = False
        self._save_volume()

    def _volume_slid(self, v) -> None:
        if not self._vol_guard:
            self.player.set_volume(v / 100)
            self._save_volume()

    def _save_volume(self) -> None:
        if self.mpris:
            self.mpris.changed("Volume")
        if getattr(self, "_vol_src", 0):
            GLib.source_remove(self._vol_src)

        def save():
            self._vol_src = 0
            config.update("music", volume=round(self.player.volume, 3))
            return False
        self._vol_src = GLib.timeout_add(600, save)

    def toggle_shuffle(self) -> None:
        self.set_shuffle(not self.queue.shuffle)

    def set_shuffle(self, on: bool) -> None:
        self.queue.set_shuffle(on)
        self._save_modes()

    def cycle_repeat(self) -> None:
        self.queue.cycle_repeat()
        self._save_modes()

    def set_repeat(self, mode: str) -> None:
        self.queue.repeat = mode
        self._save_modes()

    def _save_modes(self) -> None:
        config.update("music", shuffle=self.queue.shuffle, repeat=self.queue.repeat)
        self._update_modes()
        self._notify()

    def _update_modes(self) -> None:
        (self.shuffle_btn.add_css_class if self.queue.shuffle else self.shuffle_btn.remove_css_class)("on")
        rep = self.queue.repeat
        (self.repeat_btn.add_css_class if rep != REPEAT_OFF else self.repeat_btn.remove_css_class)("on")
        self.repeat_btn.set_icon_name("media-playlist-repeat-song-symbolic" if rep == REPEAT_ONE
                                      else "media-playlist-repeat-symbolic")
        self.repeat_btn.set_tooltip_text({"off": "Repeat: Off", "all": "Repeat: All", "one": "Repeat: One"}[rep])
        self.shuffle_btn.set_tooltip_text("Shuffle: On" if self.queue.shuffle else "Shuffle: Off")

    # -- player events ----------------------------------------------------------------------
    def _song_changed(self) -> None:
        self._update_lcd()
        self.songs.refresh_now()
        self.plist.refresh_now()
        cur = self.current_path()
        for path, num in self.album_blocks:
            num.set_visible_child_name("now" if path == cur else "n")
        self._notify()

    def _state_changed(self) -> None:
        playing = self.player.playing
        self.play_btn.set_icon_name("media-playback-pause-symbolic" if playing else "media-playback-start-symbolic")
        self.play_btn.set_tooltip_text("Pause" if playing else "Play")
        if self.mpris:
            self.mpris.changed("PlaybackStatus")

    def _notify(self) -> None:
        if self.mpris:
            self.mpris.changed()

    def _position(self, seconds: float) -> None:
        if self.get_mapped():                     # nothing to draw while hidden/minimized
            self._update_time(seconds)

    def _duration(self, seconds: float) -> None:
        if self.player.path:
            self.library.set_duration(self.player.path, seconds)
            t = self.extra.get(self.player.path)
            if t is not None and not t.get("duration"):
                t["duration"] = seconds
        self._update_time(self.player.position)
        self._notify()

    def _ended(self) -> None:
        path = self.player.path
        if path and path in self.library.tracks:
            self.library.add_play(path)
            self.songs.refresh_plays()
            self.plist.refresh_plays()
        if self.queue.next(auto=True) is not None:
            self._load_current()
        else:
            self.player.stop()
            self.queue.pos = 0 if len(self.queue) else -1
            self._song_changed()

    def _error(self, message: str) -> None:
        self._show_banner(message)

    def _show_banner(self, text: str) -> None:
        self.banner_label.set_label(text)
        self.banner.set_reveal_child(True)

    def _update_lcd(self) -> None:
        t = self.current_track()
        has = t is not None
        self.lcd_stack.set_visible_child_name("track" if has else "idle")
        self.lcd_art.set_path(t.get("thumb") or t.get("art") if has else "")
        for b in (self.prev_btn, self.next_btn):
            b.set_sensitive(has)
        if has:
            self.lcd_title.set_label(t.get("title") or "")
            sub = " — ".join(x for x in (t.get("artist"), t.get("album")) if x)
            self.lcd_sub.set_label(sub)
        # the song shows in the LCD right under the title bar: the title stays
        # "Music" (it was the song twice, in the title bar and the LCD)
        self.set_title("Music")
        self._update_time(self.player.position if has else 0)
        self._state_changed()

    def _update_time(self, pos: float) -> None:
        t = self.current_track()
        dur = self.player.duration or (t.get("duration") if t else 0) or 0
        self.scrub.set_sensitive(dur > 0)
        self.scrub.set_range(0, max(dur, 1))
        self.scrub.set_value(min(pos, dur) if dur else 0)
        self.lcd_elapsed.set_label(fmt_time(pos))
        self.lcd_remaining.set_label("-" + fmt_time(max(0, dur - pos)) if dur else "--:--")

    def _scrubbed(self, _scale, _scroll, value) -> bool:
        if self.player.path:
            self.seek_to(value)
        return False

    # -- Up Next ----------------------------------------------------------------------------
    def up_next(self) -> None:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.set_size_request(320, -1)
        clear = Gtk.Button(label="Clear", css_classes=["mu-panel-btn"], valign=Gtk.Align.CENTER)
        col.append(ui.panel.header("Playing Next", clear))
        upcoming = self.queue.upcoming()[:200]
        if not upcoming:
            col.append(Gtk.Label(label="No songs are queued.", css_classes=["mu-upnext-empty"]))
            clear.set_sensitive(False)
        else:
            lb = Gtk.ListBox(css_classes=["mu-upnext-list"], selection_mode=Gtk.SelectionMode.NONE)
            for i, p in enumerate(upcoming):
                t = self.track(p) or {"title": os.path.basename(p)}
                row = Gtk.Box(spacing=10)
                art = Art(32)
                art.set_path(t.get("thumb", ""))
                row.append(art)
                txt = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
                txt.append(Gtk.Label(label=t.get("title") or "", xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                     max_width_chars=1, hexpand=True, css_classes=["mu-upnext-title"]))
                txt.append(Gtk.Label(label=t.get("artist") or "", xalign=0, ellipsize=Pango.EllipsizeMode.END,
                                     max_width_chars=1, css_classes=["mu-dim"]))
                row.append(txt)
                if t.get("duration"):
                    row.append(Gtk.Label(label=fmt_time(t["duration"]), css_classes=["mu-dim"]))
                lr = Gtk.ListBoxRow(child=row)
                lr.offset = i + 1
                lb.append(lr)
            sc = Gtk.ScrolledWindow(child=lb, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                    max_content_height=420)
            col.append(sc)
        pop = ui.panel.popup(self.upnext_btn, col)
        if upcoming:
            def jump(_l, r):
                pop.popdown()
                self.queue.jump(r.offset)
                self._load_current()
            lb.connect("row-activated", jump)

        def clear_all(*_a):
            cur = self.queue.current
            self.queue.set([cur] if cur else [], 0)
            pop.popdown()
            self._notify()
        clear.connect("clicked", clear_all)

    # -- keys & lifetime --------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        focus = self.get_focus()
        typing = isinstance(focus, (Gtk.Text, Gtk.Editable)) or isinstance(getattr(focus, "get_parent", lambda: None)(),
                                                                          Gtk.Editable)
        k = Gdk.keyval_to_lower(keyval)
        if cmd:
            act = {Gdk.KEY_Right: self.next, Gdk.KEY_Left: self.previous,
                   Gdk.KEY_Up: lambda: self.set_volume(self.player.volume + 0.1),
                   Gdk.KEY_Down: lambda: self.set_volume(self.player.volume - 0.1),
                   Gdk.KEY_f: lambda: self.search.grab_focus(), Gdk.KEY_l: self.show_current,
                   Gdk.KEY_n: self.new_playlist, Gdk.KEY_w: self.close}.get(k)
            if act is None or (typing and keyval in (Gdk.KEY_Left, Gdk.KEY_Right)):
                return False
            act()
            return True
        if keyval == Gdk.KEY_space and not typing:
            self.toggle()
            return True
        return False

    def raise_window(self) -> None:
        self.present()

    def quit(self) -> None:
        self.close()

    def _closing(self, _w) -> bool:
        self.player.stop(notify=False)
        self.library.unwatch()
        if self.mpris:
            self.mpris.close()
            self.mpris = None
        return False


def open_windows(app, paths=()) -> None:
    """Present the Music window (one per app); files open in it: queued
    next and the first one plays."""
    win = next((w for w in app.get_windows() if isinstance(w, MusicWindow)), None)
    if win is None:
        win = MusicWindow(app)
    win.present()
    files = [Gio.File.new_for_commandline_arg(p).get_path() if "://" not in p else Gio.File.new_for_uri(p).get_path()
             for p in paths]
    if files:
        win.open_files([f for f in files if f])


def music_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Music\nComment=Play your music library\n"
                              "Icon=gnome-music\nCategories=AudioVideo;Audio;Player;\n"
                              "Keywords=music;songs;albums;playlist;itunes;\n"
                              "MimeType=" + "".join(t + ";" for t in MIME_TYPES) + "\n"
                              "StartupNotify=true\n"
                              f"Exec={command} music %F\n")
