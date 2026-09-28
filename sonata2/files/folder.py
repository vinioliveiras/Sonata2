"""Folder contents for the views: one Gio.ListStore of Gio.FileInfo, kept
sorted (Finder order: names compared like Finder, numbers numerically,
folders mixed with files) and live (directory monitor).

Loading is async and batched; the store is filled with one splice so a
10k-item folder is one model update. Each FileInfo carries its Gio.File in
the "sonata::file" attribute (Recents mixes folders)."""
import bisect
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

ATTRS = ",".join((
    "standard::name", "standard::display-name", "standard::icon", "standard::symbolic-icon",
    "standard::type", "standard::is-hidden", "standard::is-backup", "standard::content-type",
    "standard::size", "standard::target-uri", "time::modified", "access::can-write",
    "thumbnail::path", "thumbnail::failed"))
RECENTS = "sonata:recents"
BATCH = 500


def file_of(info: Gio.FileInfo) -> Gio.File:
    return info.get_attribute_object("sonata::file")


def is_dir(info: Gio.FileInfo) -> bool:
    return info.get_file_type() in (Gio.FileType.DIRECTORY, Gio.FileType.MOUNTABLE)


def sort_key(info: Gio.FileInfo) -> str:
    """Finder name order; cached on the info ("sonata::key") for the views' sorters."""
    k = info.get_attribute_string("sonata::key")
    if k is None:
        name = info.get_display_name()      # case-insensitive in every locale, like Finder
        k = GLib.utf8_collate_key_for_filename(name.casefold(), -1) + "\x00" + name
        info.set_attribute_string("sonata::key", k)
    return k


def display_name(uri: str) -> str:
    if uri == RECENTS:
        return "Recents"
    f = Gio.File.new_for_uri(uri)
    if f.get_path() == GLib.get_home_dir():
        return GLib.get_user_name()
    if f.get_path() == "/":
        return "Computer"
    try:
        return f.query_info("standard::display-name", Gio.FileQueryInfoFlags.NONE, None).get_display_name()
    except GLib.Error:
        return f.get_basename() or uri


class Folder:
    """`store` holds the current folder's items. load(uri) replaces them;
    on_loaded(uri) / on_error(uri, GLib.Error) report the outcome."""

    def __init__(self, on_loaded, on_error):
        self.store = Gio.ListStore(item_type=Gio.FileInfo)
        self.uri = None
        self.show_hidden = False
        self._on_loaded, self._on_error = on_loaded, on_error
        self._keys = []            # sort keys, parallel to store
        self._cancel = None
        self._monitor = None

    # -- loading ---------------------------------------------------------------------
    def load(self, uri: str) -> None:
        if self._cancel:
            self._cancel.cancel()
        self._cancel = cancel = Gio.Cancellable()
        if self._monitor:
            self._monitor.cancel()
            self._monitor = None
        if uri == RECENTS:
            self._load_recents(uri, cancel)
            return
        d = Gio.File.new_for_uri(uri)
        items = []

        def got_enum(src, res):
            try:
                en = src.enumerate_children_finish(res)
            except GLib.Error as e:
                if not cancel.is_cancelled():
                    self._on_error(uri, e)
                return
            en.next_files_async(BATCH, GLib.PRIORITY_DEFAULT, cancel, got_batch, en)

        def got_batch(en, res, _en):
            try:
                batch = en.next_files_finish(res)
            except GLib.Error as e:
                if not cancel.is_cancelled():
                    self._on_error(uri, e)
                return
            if batch:
                for info in batch:
                    if self._visible(info):
                        info.set_attribute_object("sonata::file", d.get_child(info.get_name()))
                        items.append(info)
                en.next_files_async(BATCH, GLib.PRIORITY_DEFAULT, cancel, got_batch, en)
                return
            en.close_async(GLib.PRIORITY_LOW, None, None)
            self._fill(uri, items)
            self._watch(d)

        d.enumerate_children_async(ATTRS, Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT,
                                   cancel, got_enum)

    def cancel(self) -> None:
        """Stop loading and watching (a column that was closed)."""
        if self._cancel:
            self._cancel.cancel()
        if self._monitor:
            self._monitor.cancel()
            self._monitor = None

    def reload(self) -> None:
        if self.uri:
            self.load(self.uri)

    def _visible(self, info) -> bool:
        return self.show_hidden or not (info.get_is_hidden() or info.get_is_backup())

    def _fill(self, uri, items, keep_order=False) -> None:
        if not keep_order:
            items.sort(key=sort_key)
        self._keys = [sort_key(i) for i in items]
        self.uri = uri
        self.store.splice(0, self.store.get_n_items(), items)
        self._on_loaded(uri)

    def _load_recents(self, uri, cancel) -> None:
        """Recently used files (GTK's recent list), newest first."""
        entries = sorted((i for i in Gtk.RecentManager.get_default().get_items() if i.is_local()),
                         key=lambda i: i.get_modified().to_unix(), reverse=True)[:100]
        uris = [e.get_uri() for e in entries]

        def work():
            out = []
            for u in uris:
                f = Gio.File.new_for_uri(u)
                try:
                    info = f.query_info(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
                except GLib.Error:
                    continue
                info.set_attribute_object("sonata::file", f)
                out.append(info)
            return out

        def done(items):
            if not cancel.is_cancelled():
                self._fill(uri, items or [], keep_order=True)
                self._keys = []           # not name-sorted: live inserts go last
        from ..backend.system import run_async
        run_async(work, done)

    # -- live updates -------------------------------------------------------------------
    def _watch(self, d: Gio.File) -> None:
        try:
            self._monitor = d.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            return
        self._monitor.connect("changed", self._changed)

    def _changed(self, _mon, f, other, event):
        E = Gio.FileMonitorEvent
        if event in (E.DELETED, E.MOVED_OUT):
            self._remove(f.get_basename())
        elif event in (E.CREATED, E.MOVED_IN):
            self._add(f)
        elif event == E.RENAMED:
            self._remove(f.get_basename())
            if other is not None:
                self._add(other)
        elif event in (E.CHANGES_DONE_HINT, E.ATTRIBUTE_CHANGED):
            self._add(f, replace=True)

    def _index(self, name: str) -> int:
        for i in range(self.store.get_n_items()):
            if self.store.get_item(i).get_name() == name:
                return i
        return -1

    def _remove(self, name: str) -> None:
        i = self._index(name)
        if i >= 0:
            self.store.remove(i)
            if self._keys:
                del self._keys[i]

    def _add(self, f: Gio.File, replace=False) -> None:
        uri = self.uri

        def got(src, res):
            try:
                info = src.query_info_finish(res)
            except GLib.Error:
                return
            if self.uri != uri:
                return
            i = self._index(info.get_name())
            if i >= 0:
                if not replace:
                    return
                self.store.remove(i)
                del self._keys[i]
            if not self._visible(info):
                return
            info.set_attribute_object("sonata::file", f)
            k = sort_key(info)
            at = bisect.bisect(self._keys, k)
            self._keys.insert(at, k)
            self.store.insert(at, info)
        f.query_info_async(ATTRS, Gio.FileQueryInfoFlags.NONE, GLib.PRIORITY_DEFAULT, None, got)


def xdg_uri(kind) -> str:
    """URI of an XDG user folder (GLib.UserDirectory), or "" when unset/home."""
    p = GLib.get_user_special_dir(kind)
    if not p or os.path.realpath(p) == os.path.realpath(GLib.get_home_dir()) or not os.path.isdir(p):
        return ""
    return Gio.File.new_for_path(p).get_uri()
