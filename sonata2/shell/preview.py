"""Development preview: a shell surface over a sample wallpaper, in a normal
window, so it can be judged (and screenshotted) without a Wayland session.

The compositor's backdrop blur doesn't exist here, so with glass on the
preview paints a blurred, saturated copy of the wallpaper under the plate --
the same recipe as the Wayfire blur settings in config/wayfire.ini. Needs
Pillow; without it the plain gradient wallpaper is used."""
import os
import random

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..ui import register  # noqa: E402

CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2")
BLUR_RADIUS, SATURATION = 18, 1.6   # match [blur] in config/wayfire.ini

CSS = """
.preview-wall { background-image: linear-gradient(160deg, #1d3b8f 0%%, #6b3fa0 38%%, #e0567a 70%%, #f3a452 100%%); }
"""


def _wallpaper(w: int, h: int, dark: bool):
    """(sharp, blurred) PNG paths of a generated abstract wallpaper, or None."""
    try:
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    except ImportError:
        return None
    os.makedirs(CACHE, exist_ok=True)
    photo = os.environ.get("PREVIEW_WALLPAPER")    # a real photo judges glass/contrast better
    if photo and os.path.isfile(photo):
        img = Image.open(photo).convert("RGB")
        k = max(w / img.width, h / img.height)
        img = img.resize((int(img.width * k) + 1, int(img.height * k) + 1), Image.LANCZOS)
        img = img.crop(((img.width - w) // 2, (img.height - h) // 2, (img.width - w) // 2 + w,
                        (img.height - h) // 2 + h))
        import hashlib
        tag = f"photo-{hashlib.md5(photo.encode()).hexdigest()[:8]}-{w}x{h}"
        sharp = os.path.join(CACHE, f"preview-wall-{tag}.png")
        blurred = os.path.join(CACHE, f"preview-wall-{tag}-blur.png")
        img.save(sharp)
        ImageEnhance.Color(img.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))).enhance(SATURATION).save(blurred)
        return sharp, blurred
    tag = f"{w}x{h}-{'dark' if dark else 'light'}"
    sharp = os.path.join(CACHE, f"preview-wall-{tag}.png")
    blurred = os.path.join(CACHE, f"preview-wall-{tag}-blur.png")
    if os.path.exists(sharp) and os.path.exists(blurred):
        return sharp, blurred
    rnd = random.Random(11)
    img = Image.new("RGB", (w, h), (18, 24, 60) if dark else (40, 80, 170))
    d = ImageDraw.Draw(img)
    palette = ([(90, 40, 140), (150, 40, 90), (30, 60, 130), (160, 90, 40)] if dark else
               [(120, 70, 200), (240, 90, 120), (250, 170, 80), (60, 170, 220)])
    for _ in range(9):   # big soft color fields
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(h // 3, h)
        d.ellipse((x - r, y - r, x + r, y + r), fill=rnd.choice(palette))
    img = img.filter(ImageFilter.GaussianBlur(60))
    d = ImageDraw.Draw(img)
    for _ in range(40):  # sharp details, so the blur is visible
        x, y, r = rnd.randint(0, w), rnd.randint(0, h), rnd.randint(4, 26)
        c = rnd.choice([(255, 255, 255), (255, 214, 10), (48, 209, 88), (10, 132, 255)])
        d.ellipse((x - r, y - r, x + r, y + r), outline=c, width=3)
    for i in range(0, w, 48):
        d.line((i, h - 90, i + 30, h), fill=(255, 255, 255), width=2)
    img.save(sharp)
    soft = ImageEnhance.Color(img.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))).enhance(SATURATION)
    soft.save(blurred)
    return sharp, blurred


class PreviewWindow(Gtk.ApplicationWindow):
    def __init__(self, app, dock, width: int = 0, height: int = 0):
        # tools/wl-preview.sh exports PREVIEW_SIZE (the virtual screen size).
        size = os.environ.get("PREVIEW_SIZE", "960x260").split("x")
        width, height = width or int(size[0]), height or int(size[1])
        super().__init__(application=app, title="Sonata 2 preview",
                         default_width=width, default_height=height, decorated=False,
                         resizable=False)
        register(CSS, key="preview")
        self.dock = dock
        self._size = (width, height)
        self._walls = {}
        self.wall = Gtk.Picture(content_fit=Gtk.ContentFit.FILL, can_shrink=True,
                                hexpand=True, vexpand=True, css_classes=["preview-wall"])
        over = Gtk.Overlay()
        over.set_child(self.wall)
        from .dock import apply_margins
        apply_margins(dock)
        over.add_overlay(dock)
        self.set_child(over)
        from gi.repository import Adw
        Adw.StyleManager.get_default().connect("notify::dark", lambda *_: GLib.idle_add(self._paint))
        self.connect("map", lambda *_: GLib.timeout_add(150, self._paint))

    def _paint(self) -> bool:
        from .. import ui
        dark = ui.is_dark()
        if dark not in self._walls:
            self._walls[dark] = _wallpaper(*self._size, dark)
        walls = self._walls[dark]
        if not walls:
            return False
        self.wall.set_filename(walls[0])
        from ..ui import glass as G
        if G.settings()["dock"]["on"]:
            from gi.repository import Gdk
            self.dock.backdrop = Gdk.Texture.new_from_filename(walls[1])
            self.dock.queue_draw()
        return False
