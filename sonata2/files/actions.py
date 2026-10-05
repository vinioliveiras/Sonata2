"""Finder's file actions that aren't plain copy / move: Compress (a .zip next
to the items), Copy Path (Finder: Copy as Pathname) and the Quick Actions
for pictures (Rotate Left / Right, Convert to PNG / JPEG). The work runs in a
thread (ops._in_thread); what it makes can be undone (undo.copied)."""
import os
import zipfile

from gi.repository import Gio, GLib

from . import ops

ROTATABLE = {"image/png": "png", "image/jpeg": "jpeg", "image/bmp": "bmp", "image/tiff": "tiff"}
JPEG_QUALITY = "95"


# -- Compress -------------------------------------------------------------------------------------
def archive_name(files) -> str:
    """Finder: "<name>.zip" for one item, "Archive.zip" for several."""
    if len(files) == 1:
        return files[0].get_basename() + ".zip"
    return "Archive.zip"


def _add(zf, path, arc):
    if os.path.isdir(path) and not os.path.islink(path):
        zf.write(path, arc + "/")
        for name in sorted(os.listdir(path)):
            _add(zf, os.path.join(path, name), arc + "/" + name)
    elif os.path.islink(path):
        info = zipfile.ZipInfo(arc)
        info.external_attr = 0o120777 << 16                   # a symlink stays a symlink
        zf.writestr(info, os.readlink(path))
    else:
        zf.write(path, arc)


def compress(files, on_done=None, on_error=None) -> None:
    """A .zip of `files` in their folder (paths relative to it); on_done(zip file)."""
    files = [f for f in files if f.get_path()]
    if not files:
        return
    folder = files[0].get_parent()
    target = folder.get_child(ops.free_name(folder, archive_name(files), style="number"))
    out = {}

    def work(report):
        tmp = target.get_path() + ".part"
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                for f in files:
                    _add(zf, f.get_path(), f.get_basename())
            os.replace(tmp, target.get_path())
            out["file"] = target
        except OSError as e:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            report(target, GLib.Error.new_literal(Gio.io_error_quark(), str(e), Gio.IOErrorEnum.FAILED))

    def done():
        if "file" in out:
            from . import undo
            undo.copied([out["file"]])
            if on_done:
                on_done(out["file"])
    ops._in_thread(work, done, on_error)


# -- Copy Path ------------------------------------------------------------------------------------
def paths_text(files) -> str:
    return "\n".join(f.get_path() or f.get_uri() for f in files)


def copy_paths(widget, files) -> None:
    if files:
        widget.get_clipboard().set(paths_text(files))


# -- Quick Actions for pictures -------------------------------------------------------------------
def picture_kind(info) -> str:
    """"png", "jpeg"...: a picture these actions can rewrite; "" otherwise."""
    return ROTATABLE.get(info.get_content_type() or "", "")


def _save(pb, path, kind):
    opts = (["quality"], [JPEG_QUALITY]) if kind == "jpeg" else ([], [])
    tmp = path + ".sonata-new"
    pb.savev(tmp, kind, *opts)
    os.replace(tmp, path)


def rotate(files, clockwise: bool, on_done=None, on_error=None) -> None:
    """Rotate Left / Right, in place (Finder's Quick Action)."""
    from gi.repository import GdkPixbuf
    angle = GdkPixbuf.PixbufRotation.CLOCKWISE if clockwise else GdkPixbuf.PixbufRotation.COUNTERCLOCKWISE
    jobs = [(f, k) for f, k in files if k and f.get_path()]

    def work(report):
        for f, kind in jobs:
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(f.get_path())
                pb = pb.apply_embedded_orientation().rotate_simple(angle)
                _save(pb, f.get_path(), kind)
            except GLib.Error as e:
                report(f, e)
    ops._in_thread(work, on_done, on_error)


def convert(files, kind: str, on_done=None, on_error=None) -> None:
    """Convert to PNG / JPEG: a new file next to each picture."""
    from gi.repository import GdkPixbuf
    ext = {"png": ".png", "jpeg": ".jpg"}[kind]
    made = []

    def work(report):
        for f in files:
            if not f.get_path():
                continue
            parent = f.get_parent()
            base = os.path.splitext(f.get_basename())[0]
            target = parent.get_child(ops.free_name(parent, base + ext, style="number"))
            try:
                pb = GdkPixbuf.Pixbuf.new_from_file(f.get_path()).apply_embedded_orientation()
                if kind == "jpeg" and pb.get_has_alpha():          # JPEG has no transparency: on white
                    flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pb.get_width(), pb.get_height())
                    flat.fill(0xffffffff)
                    pb.composite(flat, 0, 0, pb.get_width(), pb.get_height(), 0, 0, 1, 1,
                                 GdkPixbuf.InterpType.BILINEAR, 255)
                    pb = flat
                _save(pb, target.get_path(), kind)
                made.append(target)
            except GLib.Error as e:
                report(f, e)

    def done():
        if made:
            from . import undo
            undo.copied(made)
        if on_done:
            on_done(made)
    ops._in_thread(work, done, on_error)
