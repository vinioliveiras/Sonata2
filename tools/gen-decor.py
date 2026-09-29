#!/usr/bin/env python3
"""Traffic-light PNGs for the pixdecor title bars (windows that don't draw
their own: terminals like Alacritty, X11 apps). Same colours as
ui/window.py; glyphs on hover. Output: sonata2/data/decor/*.png (2x
artwork downscaled for clean edges).  python3 tools/gen-decor.py"""
import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "decor")
SIZE, SS = 12, 8                      # px, supersampling
COLORS = {"close": "#ff5f57", "minimize": "#febc2e", "maximize": "#28c840", "restore": "#28c840"}
RIM = (0, 0, 0, 46)                   # 0.5 px darker rim, like macOS
GLYPH = (0, 0, 0, 140)


def dot(color, glyph=None):
    n = SIZE * SS
    im = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, n - 1, n - 1), fill=RIM)
    d.ellipse((SS // 2, SS // 2, n - 1 - SS // 2, n - 1 - SS // 2), fill=color)
    w = int(1.1 * SS)
    c, a = n / 2, n * 0.22
    if glyph == "close":
        d.line((c - a, c - a, c + a, c + a), fill=GLYPH, width=w)
        d.line((c - a, c + a, c + a, c - a), fill=GLYPH, width=w)
    elif glyph == "minimize":
        d.line((c - a * 1.2, c, c + a * 1.2, c), fill=GLYPH, width=w)
    elif glyph in ("maximize", "restore"):      # macOS zoom: two small triangles
        b = n * 0.2
        d.polygon([(c - b - 1, c - b - 1), (c + b * 0.6, c - b - 1), (c - b - 1, c + b * 0.6)], fill=GLYPH)
        d.polygon([(c + b + 1, c + b + 1), (c - b * 0.6, c + b + 1), (c + b + 1, c - b * 0.6)], fill=GLYPH)
    return im.resize((SIZE, SIZE), Image.LANCZOS)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, color in COLORS.items():
        dot(color).save(os.path.join(OUT, f"{name}.png"))
        dot(color, name).save(os.path.join(OUT, f"{name}-hover.png"))
    print("written to", os.path.normpath(OUT))
