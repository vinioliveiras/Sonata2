"""Preview's edits (macOS Preview: Rotate, Flip, Crop, Adjust Color):
a list of operations over the picture, applied to a small copy while
editing (instant) and to the full picture when saving. Pillow does the
work. Nothing touches the file until Save.

    ed = Edits.load(path)            # None when Pillow can't read it
    ed.push(("rotate", 90)); ed.push(("crop", (0.1, 0.1, 0.9, 0.8)))
    ed.adjust["brightness"] = 0.2    # -1 .. 1, 0 = unchanged
    tex = ed.texture()               # the edited picture (small copy)
    ed.save(path)                    # the full-size result"""
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


class Edits:
    def __init__(self, image):
        self.full = image                        # the picture as opened (upright)
        self.proxy = image.copy()
        self.proxy.thumbnail((PROXY, PROXY))
        self.ops = []                            # [("rotate", 90) | ("flip", "h"|"v") | ("crop", (x0, y0, x1, y1))]
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
            im = ImageOps.exif_transpose(im)
            im.load()
        except Exception:
            return None
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB")
        return cls(im)

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

    def save(self, path: str) -> None:
        """The full-size result, written next to the file then moved over it."""
        fmt = WRITABLE.get(os.path.splitext(path)[1].lower(), "PNG")
        result = self.render(self.full)
        im = result.convert("RGB") if fmt in ("JPEG", "BMP") and result.mode == "RGBA" else result
        opts = {"quality": 92} if fmt in ("JPEG", "WEBP") else {}
        tmp = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.sonata-tmp")
        im.save(tmp, fmt, **opts)
        os.replace(tmp, path)
        self.full = result                       # the saved picture is the new start
        self.proxy = self.full.copy()
        self.proxy.thumbnail((PROXY, PROXY))
        self.revert()
