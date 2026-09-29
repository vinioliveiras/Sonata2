"""File operations with Finder's behaviour: copy / move (with the progress
window and "already exists" alerts), duplicate, new folder, Move to Trash,
rename, and the clipboard (interoperable with GNOME/KDE file managers).

Copies run in a worker thread with Gio's sync API (one thread per
operation); the GTK thread only receives throttled progress updates.
Folders are copied recursively; a move is a rename when source and
destination share a filesystem, else copy + delete."""
import os
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib  # noqa: E402

from .. import ui  # noqa: E402

ATTRS = "standard::name,standard::type,standard::size,standard::is-symlink"
UI_EVERY_S = 0.1
NOFOLLOW = Gio.FileQueryInfoFlags.NOFOLLOW_SYMLINKS


# -- names ------------------------------------------------------------------------------------
def _split(name: str):
    """"photo.tar.gz" -> ("photo", ".tar.gz"); folders/dotfiles keep it whole."""
    if name.startswith(".") and name.count(".") == 1:
        return name, ""
    for ext in (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst"):
        if name.endswith(ext):
            return name[:-len(ext)], ext
    base, ext = os.path.splitext(name)
    return base, ext


def free_name(folder: Gio.File, name: str, is_dir=False, style="copy") -> str:
    """A name not used in `folder`. style "copy": "a copy.txt", "a copy 2.txt"
    (Duplicate / Keep Both); "number": "untitled folder 2"."""
    base, ext = (name, "") if is_dir else _split(name)
    if style == "number":
        cands = (name if i == 1 else f"{base} {i}{ext}" for i in range(1, 10000))
    else:
        cands = (f"{base} copy{ext}" if i == 1 else f"{base} copy {i}{ext}" for i in range(1, 10000))
    for c in cands:
        if not folder.get_child(c).query_exists(None):
            return c
    return name


def rename_selection(name: str, is_dir: bool):
    """(start, end) Finder selects when renaming: the name without extension."""
    if is_dir:
        return 0, len(name)
    base, _ext = _split(name)
    return 0, len(base) or len(name)


# -- simple operations -------------------------------------------------------------------------
def new_folder(folder: Gio.File, on_done, on_error) -> None:
    name = free_name(folder, "untitled folder", True, style="number")
    child = folder.get_child(name)

    def done(f, res):
        try:
            f.make_directory_finish(res)
            on_done(child)
        except GLib.Error as e:
            on_error(e)
    child.make_directory_async(GLib.PRIORITY_DEFAULT, None, done)


def rename(f: Gio.File, new_name: str, on_done, on_error) -> None:
    def done(src, res):
        try:
            on_done(src.set_display_name_finish(res))
        except GLib.Error as e:
            on_error(e)
    f.set_display_name_async(new_name, GLib.PRIORITY_DEFAULT, None, done)


def trash(files, on_error=None) -> None:
    from .. import sounds
    if files:
        sounds.play("trash")
    for f in files:
        def done(src, res):
            try:
                src.trash_finish(res)
            except GLib.Error as e:
                if on_error:
                    on_error(src, e)
        f.trash_async(GLib.PRIORITY_DEFAULT, None, done)


# -- the Trash (trash:///, gvfs) -------------------------------------------------------------
TRASH = "trash:///"


def is_trash(uri: str) -> bool:
    return (uri or "").startswith("trash:")


def put_back(files, on_error=None) -> None:
    """Finder "Put Back": move each item to where it was trashed from."""
    for f in files:
        try:
            orig = f.query_info("trash::orig-path", NOFOLLOW, None).get_attribute_byte_string("trash::orig-path")
            if not orig:
                raise GLib.Error("The original location is unknown.")
            dest = Gio.File.new_for_path(orig)
            parent = dest.get_parent()
            if parent and not parent.query_exists(None):
                parent.make_directory_with_parents(None)
            if dest.query_exists(None):
                dest = parent.get_child(free_name(parent, dest.get_basename()))
            f.move(dest, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None, None)
        except GLib.Error as e:
            if on_error:
                on_error(f, e)


def delete_now(files, on_done=None, on_error=None) -> None:
    """Delete Immediately / Empty Trash, in a thread (big folders)."""
    import threading

    def run():
        for f in files:
            try:
                _delete(f, None)
            except GLib.Error as e:
                if on_error:
                    GLib.idle_add(lambda f=f, e=e: (on_error(f, e), False)[1])
        if on_done:
            GLib.idle_add(lambda: (on_done(), False)[1])
    threading.Thread(target=run, daemon=True).start()


def empty_trash(on_done=None, on_error=None) -> None:
    from .. import sounds
    sounds.play("empty-trash")
    t = Gio.File.new_for_uri(TRASH)
    try:
        kids = [t.get_child(i.get_name()) for i in t.enumerate_children("standard::name", NOFOLLOW, None)]
    except GLib.Error as e:
        if on_error:
            on_error(t, e)
        return
    delete_now(kids, on_done, on_error)


# -- copy / move -------------------------------------------------------------------------------
class Transfer:
    """copy (or move) `sources` into the folder `dest`. `parent` is the
    window for the alerts; on_done() runs on the GTK thread at the end."""

    def __init__(self, sources, dest: Gio.File, move=False, parent=None, on_done=None, duplicate=False):
        self.sources, self.dest, self.move = list(sources), dest, move
        self.duplicate = duplicate           # copies next to the originals ("x copy")
        self.parent, self.on_done = parent, on_done
        self.cancel = Gio.Cancellable()
        self.total = self.done_bytes = 0
        self._last_ui = 0.0
        self._apply_all = None               # remembered conflict answer
        verb = "Moving" if move else "Copying"
        first = self.sources[0].get_basename() if self.sources else ""
        what = f"“{first}”" if len(self.sources) == 1 else f"{len(self.sources)} items"
        self.op = ui.progress.start(f"{verb} {what} to “{dest.get_basename() or dest.get_uri()}”",
                                    on_cancel=self.cancel.cancel, heading="Move" if move else "Copy")
        threading.Thread(target=self._run, daemon=True).start()

    # -- worker thread ----------------------------------------------------------------
    def _run(self):
        err = None
        try:
            self.total = sum(self._size(f) for f in self.sources)
            for src in self.sources:
                if self.cancel.is_cancelled():
                    break
                self._one(src)
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                err = e
        except _Stop:
            pass
        GLib.idle_add(self._finish, err)

    def _size(self, f) -> int:
        info = f.query_info(ATTRS, NOFOLLOW, self.cancel)
        if info.get_file_type() != Gio.FileType.DIRECTORY:
            return info.get_size()
        total = 0
        en = f.enumerate_children(ATTRS, NOFOLLOW, self.cancel)
        for child in en:
            total += (self._size(f.get_child(child.get_name())) if child.get_file_type() == Gio.FileType.DIRECTORY
                      else child.get_size())
        return total

    def _one(self, src):
        name = src.get_basename()
        info = src.query_info(ATTRS, NOFOLLOW, self.cancel)
        is_dir = info.get_file_type() == Gio.FileType.DIRECTORY
        if self.duplicate:
            target = self.dest.get_child(free_name(self.dest, name, is_dir))
        else:
            target = self.dest.get_child(name)
            if src.equal(target) and not self.move:        # paste into the same folder = duplicate
                target = self.dest.get_child(free_name(self.dest, name, is_dir))
            elif src.equal(target):
                return
            elif target.query_exists(self.cancel):
                answer = self._ask(name, is_dir)
                if answer == "stop":
                    raise _Stop()
                if answer == "keep":
                    target = self.dest.get_child(free_name(self.dest, name, is_dir))
                else:                                   # replace
                    _delete(target, self.cancel)
        if self.move:
            try:
                src.move(target, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.NO_FALLBACK_FOR_MOVE,
                         self.cancel, None, None)
                self.done_bytes += self._size(target) if is_dir else info.get_size()
                self._progress(0, 0)
                return
            except GLib.Error as e:
                if not (e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.NOT_SUPPORTED)
                        or e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.WOULD_RECURSE)):
                    raise
        self._copy(src, target, is_dir)
        if self.move:
            _delete(src, self.cancel)

    def _copy(self, src, target, is_dir):
        if is_dir:
            target.make_directory(self.cancel)
            en = src.enumerate_children(ATTRS, NOFOLLOW, self.cancel)
            for child in en:
                self._copy(src.get_child(child.get_name()), target.get_child(child.get_name()),
                           child.get_file_type() == Gio.FileType.DIRECTORY)
            return
        base = self.done_bytes
        src.copy(target, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS | Gio.FileCopyFlags.ALL_METADATA, self.cancel,
                 lambda cur, _tot, *_a: self._progress(base, cur), None)
        self.done_bytes = base + (src.query_info(ATTRS, NOFOLLOW, None).get_size())

    def _progress(self, base, cur):
        now = time.monotonic()
        if now - self._last_ui >= UI_EVERY_S:
            self._last_ui = now
            done = base + cur if cur else self.done_bytes
            GLib.idle_add(lambda: (self.op.update(done, self.total), False)[1])

    def _ask(self, name, is_dir) -> str:
        """Finder's conflict alert, answered on the GTK thread."""
        if self._apply_all:
            return self._apply_all
        ev, box = threading.Event(), {}

        def show():
            kind = "folder" if is_dir else "item"
            verb = "moving" if self.move else "copying"

            def answered(rid, apply_all):
                box["a"] = rid
                if apply_all:
                    self._apply_all = rid
                ev.set()
            ui.dialog.alert(f"An {kind} named “{name}” already exists in this location.",
                            f"Do you want to replace it with the one you’re {verb}?",
                            [("keep", "Keep Both", ""), ("stop", "Stop", ""), ("replace", "Replace", "default")],
                            answered, parent=self.parent, check="Apply to All")
            return False
        GLib.idle_add(show)
        ev.wait()
        return box.get("a", "stop")

    # -- GTK thread ---------------------------------------------------------------------
    def _finish(self, err):
        self.op.finish()
        from .. import sounds
        sounds.play("done" if err is None else "error")
        if err is not None:
            ui.dialog.alert("The operation can’t be completed.", err.message, [("ok", "OK", "default")],
                            parent=self.parent)
        if self.on_done:
            self.on_done()
        return False


class _Stop(Exception):
    pass


def _delete(f, cancel):
    """Recursive delete (replace, and the second half of a cross-disk move)."""
    info = f.query_info(ATTRS, NOFOLLOW, cancel)
    if info.get_file_type() == Gio.FileType.DIRECTORY:
        for child in f.enumerate_children(ATTRS, NOFOLLOW, cancel):
            _delete(f.get_child(child.get_name()), cancel)
    f.delete(cancel)


# -- clipboard ---------------------------------------------------------------------------------
GNOME_MIME = "x-special/gnome-copied-files"


def file_content(files, cut=False, with_image=True) -> Gdk.ContentProvider:
    """Files for the clipboard or a drag, in every form apps look for:
    text/uri-list (GTK apps read it as a file list; Chromium/Electron, Qt...),
    GNOME's copied-files format (Nautilus, Nemo, Dolphin) and, for a single
    picture, the picture itself (chats and editors that only take image data).
    No Gdk.FileList value on purpose: GTK would then offer the portal's
    file-transfer format first, and receivers without the document portal
    (the Dock, Files, other apps) refused the drop."""
    uris = [f.get_uri() for f in files]
    gnome = ("cut" if cut else "copy") + "\n" + "\n".join(uris)
    providers = [
        Gdk.ContentProvider.new_for_bytes("text/uri-list", GLib.Bytes.new(("\r\n".join(uris) + "\r\n").encode())),
        Gdk.ContentProvider.new_for_bytes(GNOME_MIME, GLib.Bytes.new(gnome.encode())),
    ]
    img = _image_data(files[0]) if with_image and len(files) == 1 and not cut else None
    if img is not None:
        providers.append(Gdk.ContentProvider.new_for_bytes(*img))
    return Gdk.ContentProvider.new_union(providers)


_RAW = ("image/png", "image/jpeg", "image/gif", "image/webp")


def _image_data(f):
    """(mime, bytes) of a picture file as it is (PNG, JPEG, GIF, WebP: no
    re-encoding, so a drag starts at once); other pictures become PNG."""
    try:
        info = f.query_info("standard::content-type,standard::size", Gio.FileQueryInfoFlags.NONE, None)
        ct = info.get_content_type() or ""
        if not ct.startswith("image/") or info.get_size() > 40 << 20:
            return None
        if ct in _RAW:
            return ct, GLib.Bytes.new(f.load_contents(None)[1])
    except (GLib.Error, TypeError):
        return None
    png = _image_png(f)
    return ("image/png", png) if png is not None else None


def copy_to_clipboard(widget, files, cut=False) -> None:
    widget.get_clipboard().set_content(file_content(files, cut))


def _image_png(f):
    """PNG bytes of an image file (<= 40 MB), or None."""
    try:
        info = f.query_info("standard::content-type,standard::size", Gio.FileQueryInfoFlags.NONE, None)
        if not (info.get_content_type() or "").startswith("image/") or info.get_size() > 40 << 20:
            return None
        if info.get_content_type() == "image/png":
            return GLib.Bytes.new(f.load_contents(None)[1])
        return Gdk.Texture.new_from_file(f).save_to_png_bytes()
    except (GLib.Error, TypeError):
        return None


def paste_image(widget, dest, done=None) -> bool:
    """A picture on the clipboard (copied in a browser, a screenshot...)
    becomes "Pasted Image <date> at <time>.png" in `dest`. False when the
    clipboard holds no picture."""
    cb = widget.get_clipboard()
    if not _has_image(cb.get_formats()):
        return False

    def got(c, res):
        try:
            tex = c.read_texture_finish(res)
        except GLib.Error:
            return
        if tex is None:
            return
        name = GLib.DateTime.new_now_local().format("Pasted Image %Y-%m-%d at %H.%M.%S.png")
        target = dest.get_child(name)
        try:
            tex.save_to_png(target.get_path())
        except (GLib.Error, TypeError):
            return
        if done:
            done(target)
    cb.read_texture_async(None, got)
    return True


def read_clipboard(widget, callback) -> None:
    """callback(files, cut) with what the clipboard holds (files may be [])."""
    cb = widget.get_clipboard()
    formats = cb.get_formats()
    if formats.contain_mime_type(GNOME_MIME):
        def got(c, res):
            try:
                stream, _mime = c.read_finish(res)
                data = stream.read_bytes(1 << 20, None).get_data().decode(errors="replace")
            except GLib.Error:
                callback([], False)
                return
            lines = [line for line in data.splitlines() if line]
            if not lines:
                callback([], False)
                return
            callback([Gio.File.new_for_uri(u) for u in lines[1:]], lines[0] == "cut")
        cb.read_async([GNOME_MIME], GLib.PRIORITY_DEFAULT, None, got)
        return
    if formats.contain_gtype(Gdk.FileList):
        def got_value(c, res):
            try:
                value = c.read_value_finish(res)
                callback(list(value.get_files()), False)
            except GLib.Error:
                callback([], False)
        cb.read_value_async(Gdk.FileList, GLib.PRIORITY_DEFAULT, None, got_value)
        return
    callback([], False)


def _has_image(formats) -> bool:
    return formats.contain_gtype(Gdk.Texture) or any(m.startswith("image/") for m in formats.get_mime_types() or [])


def clipboard_has_files(widget) -> bool:
    """Something Paste can put in a folder: files, or a picture."""
    f = widget.get_clipboard().get_formats()
    return f.contain_mime_type(GNOME_MIME) or f.contain_gtype(Gdk.FileList) or _has_image(f)


