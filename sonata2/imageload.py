"""Pictures for Preview, Quick Look and Files' thumbnails -- every format
the system can read, plus camera RAW files.

GTK reads PNG, JPEG and TIFF itself; GdkPixbuf's loaders add the rest
(WebP through webp-pixbuf-loader, AVIF/HEIF through their loaders, SVG
through librsvg). RAW files (Canon, Nikon, Sony, Fujifilm, DNG...) carry
a full-size JPEG their camera made: that one is shown, like macOS Quick
Look does -- no RAW decoder needed, and instant.

    tex = imageload.texture(path)                 # Gdk.Texture or None
    pb = imageload.pixbuf_at_scale(path, 256)     # thumbnails"""
import os

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

RAW_EXTS = (".cr2", ".cr3", ".crw", ".nef", ".nrw", ".arw", ".srf", ".sr2", ".dng", ".orf", ".rw2", ".raf",
            ".pef", ".srw", ".3fr", ".erf", ".kdc", ".mrw", ".x3f", ".iiq", ".rwl", ".mos")
RAW_TYPES = ("image/x-canon-cr2", "image/x-canon-cr3", "image/x-canon-crw", "image/x-nikon-nef",
             "image/x-nikon-nrw", "image/x-sony-arw", "image/x-sony-srf", "image/x-sony-sr2",
             "image/x-adobe-dng", "image/x-olympus-orf", "image/x-panasonic-rw2", "image/x-panasonic-raw",
             "image/x-fuji-raf", "image/x-pentax-pef", "image/x-samsung-srw", "image/x-sigma-x3f",
             "image/x-dcraw")
MAX_RAW = 200 * 1000 ** 2


def is_raw(path: str, content_type: str = "") -> bool:
    return (path or "").lower().endswith(RAW_EXTS) or content_type in RAW_TYPES


def _jpeg_end(data: bytes, start: int) -> int:
    """End of the JPEG at `start` (after its EOI), walking its segments so
    thumbnails nested in its EXIF are skipped; -1 if it isn't one."""
    i, n = start + 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return -1
        marker = data[i + 1]
        if marker == 0xD9:
            return i + 2
        if marker == 0xDA:                          # scan data: up to the EOI
            end = data.find(b"\xff\xd9", i + 2)
            return end + 2 if end >= 0 else -1
        if 0xD0 <= marker <= 0xD8 or marker == 0x01:
            i += 2
            continue
        i += 2 + int.from_bytes(data[i + 2:i + 4], "big")
    return -1


def raw_preview(path: str) -> bytes:
    """The largest JPEG inside a RAW file (the camera's full preview)."""
    import mmap
    try:
        if os.path.getsize(path) > MAX_RAW:
            return b""
        # mapped, not read: only the pages walked are loaded, as page cache the
        # kernel can drop (a read() held the whole RAW, up to 200 MB, in memory)
        with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as data:
            best = (0, 0)
            pos = data.find(b"\xff\xd8\xff")
            while pos >= 0:
                end = _jpeg_end(data, pos)
                if end > pos and end - pos > best[1] - best[0]:
                    best = (pos, end)
                pos = data.find(b"\xff\xd8\xff", max(pos + 3, end if end > pos else pos + 3))
            return data[best[0]:best[1]]
    except (OSError, ValueError):                  # ValueError: an empty file can't be mapped
        return b""


def _pixbuf_from_bytes(data: bytes, size: int = 0):
    loader = GdkPixbuf.PixbufLoader()
    if size:
        def prepared(ld, w, h):
            s = min(1.0, size / max(1, w), size / max(1, h))
            ld.set_size(max(1, round(w * s)), max(1, round(h * s)))
        loader.connect("size-prepared", prepared)
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    pb = loader.get_pixbuf()
    return pb.apply_embedded_orientation() or pb if pb else None


def _pil(path: str, size: int = 0):
    """Last resort (WebP/AVIF without their GdkPixbuf loader...): Pillow,
    when installed. Returns a Gdk.MemoryTexture."""
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if size:
                im.thumbnail((size, size))
            im = im.convert("RGBA")
            w, h = im.size
            data = GLib.Bytes.new(im.tobytes())
    except Exception:
        return None
    return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, data, w * 4)


def texture(path: str):
    """Gdk.Texture of any picture (RAW: its camera preview), or None."""
    if is_raw(path):
        data = raw_preview(path)
        pb = _pixbuf_from_bytes(data) if data else None
        return Gdk.Texture.new_for_pixbuf(pb) if pb else None
    try:
        return Gdk.Texture.new_from_filename(path)
    except GLib.Error:
        pass
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
        return Gdk.Texture.new_for_pixbuf(pb.apply_embedded_orientation() or pb)
    except GLib.Error:
        return _pil(path)


def pixbuf_at_scale(path: str, size: int):
    """A thumbnail at most size x size (orientation applied), or None."""
    if is_raw(path):
        data = raw_preview(path)
        return _pixbuf_from_bytes(data, size) if data else None
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, size, size, True)
        return pb.apply_embedded_orientation() or pb
    except GLib.Error:
        pass
    tex = _pil(path, size)                  # Pillow: back to a pixbuf for the thumbnail cache
    if tex is None:
        return None
    try:
        return Gdk.pixbuf_get_from_texture(tex)
    except (AttributeError, GLib.Error):
        return None
