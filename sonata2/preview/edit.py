"""Preview's edits (macOS Preview: Rotate, Flip, Crop, Adjust Color, Adjust Size):
a list of operations over the picture, applied to a small copy while
editing (instant) and to the full picture when saving. Pillow does the
work. Nothing touches the file until Save.

    ed = Edits.load(path)            # None when Pillow can't read it
    ed.push(("rotate", 90)); ed.push(("crop", (0.1, 0.1, 0.9, 0.8)))
    ed.push(("resize", (0.5, 0.5)))  # factors of the size at that point
    ed.adjust["brightness"] = 0.2    # -1 .. 1, 0 = unchanged
    tex = ed.texture()               # the edited picture (small copy)
    ed.save(path)                    # the full-size result (then it's the new start)
    ed.write(path, "JPEG", 80)       # Export As: a copy, the edits stay"""
import os

import gi

gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib  # noqa: E402

PROXY = 1800                     # the working copy's longest side
ADJUSTMENTS = (("brightness", "Brightness"), ("contrast", "Contrast"), ("saturation", "Saturation"),
               ("warmth", "Temperature"), ("sharpness", "Sharpness"))
# formats Preview writes back in place; others (RAW, SVG, animated GIF...) need Save As
WRITABLE = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP", ".bmp": "BMP",
            ".tif": "TIFF", ".tiff": "TIFF"}
# Export As: (menu name, Pillow format, extension, has a quality setting)
EXPORT_FORMATS = (("PNG", "PNG", ".png", False), ("JPEG", "JPEG", ".jpg", True),
                  ("WebP", "WEBP", ".webp", True), ("TIFF", "TIFF", ".tiff", False))


class Edits:
    def __init__(self, image, exif=None, icc=None):
        self.full = image                        # the picture as opened (upright)
        self.exif, self.icc = exif, icc          # kept on save (camera data, colour profile)
        self.proxy = image.copy()
        self.proxy.thumbnail((PROXY, PROXY))
        # [("rotate", 90) | ("flip", "h"|"v") | ("crop", (x0, y0, x1, y1)) | ("resize", (fx, fy))]
        self.ops = []
        self.adjust = {k: 0.0 for k, _t in ADJUSTMENTS}

    @classmethod
    def load(cls, path: str):
        try:
            from PIL import Image, ImageOps
        except ImportError:
            return None
        from ..imageload import is_raw, raw_preview
        try:
            if is_raw(path):
                import io
                data = raw_preview(path)
                if not data:
                    return None
                im = Image.open(io.BytesIO(data))
            else:
                im = Image.open(path)
                if getattr(im, "is_animated", False):
                    return None                  # an animation: not edited here
            icc = im.info.get("icc_profile")
            exif = im.getexif()
            im = ImageOps.exif_transpose(im)
            im.load()
        except Exception:
            return None
        if exif is not None and 0x0112 in exif:
            del exif[0x0112]                     # the pixels are upright now
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
        return cls(im, exif if exif else None, icc)

    # -- edits --------------------------------------------------------------------------------
    @property
    def edited(self) -> bool:
        return bool(self.ops) or any(abs(v) > 1e-3 for v in self.adjust.values())

    def push(self, op) -> None:
        self.ops.append(op)

    def undo(self) -> bool:
        if self.ops:
            self.ops.pop()
            return True
        return False

    def revert(self) -> None:
        self.ops.clear()
        for k in self.adjust:
            self.adjust[k] = 0.0

    def size(self) -> tuple:
        """The full-size result's pixels (w, h), without rendering it."""
        w, h = self.full.size
        for kind, arg in self.ops:
            if kind == "rotate" and arg % 180:
                w, h = h, w
            elif kind == "crop":
                x0, y0, x1, y1 = arg
                w, h = max(1, round(x1 * w) - round(x0 * w)), max(1, round(y1 * h) - round(y0 * h))
            elif kind == "resize":
                w, h = max(1, round(w * arg[0])), max(1, round(h * arg[1]))
        return w, h

    def resize_to(self, w: int, h: int) -> None:
        """Adjust Size: the result becomes w x h pixels."""
        cw, ch = self.size()
        if (w, h) != (cw, ch) and w > 0 and h > 0:
            self.push(("resize", (w / cw, h / ch)))

    def render(self, image):
        from PIL import Image, ImageEnhance
        im = image
        for kind, arg in self.ops:
            if kind == "rotate":                 # degrees clockwise
                im = im.transpose({90: Image.Transpose.ROTATE_270, 180: Image.Transpose.ROTATE_180,
                                   270: Image.Transpose.ROTATE_90}[arg % 360]) if arg % 360 else im
            elif kind == "flip":
                im = im.transpose(Image.Transpose.FLIP_LEFT_RIGHT if arg == "h" else Image.Transpose.FLIP_TOP_BOTTOM)
            elif kind == "crop":
                w, h = im.size
                x0, y0, x1, y1 = arg
                box = (round(x0 * w), round(y0 * h), max(round(x0 * w) + 1, round(x1 * w)),
                       max(round(y0 * h) + 1, round(y1 * h)))
                im = im.crop(box)
            elif kind == "resize":
                w, h = im.size
                im = im.resize((max(1, round(w * arg[0])), max(1, round(h * arg[1]))), Image.Resampling.LANCZOS)
        a = self.adjust
        if abs(a["warmth"]) > 1e-3:              # warmer: more red, less blue (and back)
            alpha = im.getchannel("A") if im.mode == "RGBA" else None
            r, g, b = im.convert("RGB").split()
            t = a["warmth"] * 0.25
            r = r.point(lambda v: min(255, int(v * (1 + t))))
            b = b.point(lambda v: min(255, int(v * (1 - t))))
            im = Image.merge("RGB", (r, g, b))
            if alpha is not None:
                im.putalpha(alpha)
        for key, enhancer in (("brightness", ImageEnhance.Brightness), ("contrast", ImageEnhance.Contrast),
                              ("saturation", ImageEnhance.Color), ("sharpness", ImageEnhance.Sharpness)):
            v = a[key]
            if abs(v) > 1e-3:
                im = enhancer(im).enhance(1 + v * (2 if key == "sharpness" else 1))
        return im

    def texture(self):
        im = self.render(self.proxy).convert("RGBA")
        w, h = im.size
        return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(im.tobytes()), w * 4)

    # -- saving ---------------------------------------------------------------------------------
    @staticmethod
    def writable(path: str) -> bool:
        from ..imageload import is_raw
        return os.path.splitext(path)[1].lower() in WRITABLE and not is_raw(path)

    def write(self, path: str, fmt: str = None, quality: int = 92):
        """The full-size result into path (fmt: a Pillow format; default: by
        the extension), written next to it then moved over it. The edits stay.
        A symlink is written through (the link stays), the file keeps its
        permissions, EXIF and colour profile go along."""
        fmt = fmt or WRITABLE.get(os.path.splitext(path)[1].lower(), "PNG")
        result = self.render(self.full)
        im = result.convert("RGB") if fmt in ("JPEG", "BMP") and result.mode == "RGBA" else result
        opts = {"quality": int(quality)} if fmt in ("JPEG", "WEBP") else {}
        if fmt == "TIFF":
            opts["compression"] = "tiff_lzw"
        if fmt in ("JPEG", "PNG", "WEBP", "TIFF"):
            if self.exif:
                opts["exif"] = self.exif.tobytes()
            if self.icc:
                opts["icc_profile"] = self.icc
        real = os.path.realpath(path)            # through a symlink: the picture it points to
        try:
            st = os.stat(real)
        except FileNotFoundError:
            st = None
        tmp = os.path.join(os.path.dirname(real), f".{os.path.basename(real)}.{os.getpid()}.sonata-tmp")
        try:
            im.save(tmp, fmt, **opts)
            if st is not None:                   # the old file's mode/owner, not the umask's
                os.chmod(tmp, st.st_mode & 0o7777)
                try:
                    os.chown(tmp, st.st_uid, st.st_gid)
                except OSError:
                    pass
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp, real)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return result

    def save(self, path: str) -> None:
        """The full-size result over path; it becomes the new start."""
        result = self.write(path)
        self.full = result                       # the saved picture is the new start
        self.proxy = self.full.copy()
        self.proxy.thumbnail((PROXY, PROXY))
        self.revert()
