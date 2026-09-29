#!/usr/bin/env python3
"""Traffic-light PNGs for the pixdecor title bars (windows that don't draw
their own: terminals like Alacritty, X11 apps). Same colours as
ui/window.py; glyphs on hover. Output: sonata2/data/decor/*.png.
python3 tools/gen-decor.py"""
import math
import os

import cairo

OUT = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "decor")
ICONS = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "icons", "Sonata", "apps", "scalable")
SIZE = 12                             # px, like ui/window.py's dots
COLORS = {"close": "#ff5f57", "minimize": "#febc2e", "maximize": "#28c840", "restore": "#28c840"}
RIM = (0, 0, 0, 0.18)                 # GTK's `inset 0 0 0 0.5px rgba(0,0,0,.18)`
GLYPH = (0, 0, 0, 0.55)


def _rgb(hexcolor):
    return tuple(int(hexcolor[i:i + 2], 16) / 255 for i in (1, 3, 5))


SS = 8                                # PNGs: drawn 8x larger, then scaled down


def dot(color, glyph=None, surf=None, scale=1):
    """Drawn with cairo (premultiplied alpha, analytic antialiasing): no dark
    fringe, no ringing. The same drawing makes the PNGs (pixdecor draws
    them 1:1) and the SVGs Sonata's own windows use (sharp at any scale),
    so every window's buttons look and hover the same."""
    surf = surf or cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE * scale, SIZE * scale)
    cr = cairo.Context(surf)
    cr.scale(scale, scale)
    c = SIZE / 2
    # a hair inside the tile: a circle touching the pixel grid at its four
    # extremes renders as a diamond-ish shape at 12 px
    cr.arc(c, c, c - 0.2, 0, 2 * math.pi)
    cr.set_source_rgb(*_rgb(color))
    cr.fill()
    cr.arc(c, c, c - 0.45, 0, 2 * math.pi)          # 0.5 px rim inside the edge
    cr.set_source_rgba(*RIM)
    cr.set_line_width(0.5)
    cr.stroke()
    cr.set_source_rgba(*GLYPH)
    cr.set_line_width(1.1)
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    a = SIZE * 0.2
    if glyph == "close":
        cr.move_to(c - a, c - a)
        cr.line_to(c + a, c + a)
        cr.move_to(c - a, c + a)
        cr.line_to(c + a, c - a)
        cr.stroke()
    elif glyph == "minimize":
        cr.move_to(c - a * 1.25, c)
        cr.line_to(c + a * 1.25, c)
        cr.stroke()
    elif glyph in ("maximize", "restore"):         # macOS zoom: two small triangles
        b = SIZE * 0.2
        for sx in (1, -1):
            cr.move_to(c - sx * (b + 0.3), c - sx * (b + 0.3))
            cr.line_to(c + sx * b * 0.55, c - sx * (b + 0.3))
            cr.line_to(c - sx * (b + 0.3), c + sx * b * 0.55)
            cr.close_path()
            cr.fill()
    cr.show_page()
    return surf


def _png(big, path):
    """Supersampled: a box filter over 8x8 samples per pixel (in premultiplied
    alpha), so the edge is evenly antialiased all around the circle."""
    big.flush()
    w = big.get_width() // SS
    data, stride = big.get_data(), big.get_stride()
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, w)
    od, ost = out.get_data(), out.get_stride()
    for y in range(w):
        for x in range(w):
            acc = [0, 0, 0, 0]
            for yy in range(y * SS, y * SS + SS):
                row = yy * stride
                for xx in range(x * SS, x * SS + SS):
                    o = row + xx * 4
                    for k in range(4):
                        acc[k] += data[o + k]
            o = y * ost + x * 4
            for k in range(4):
                od[o + k] = (acc[k] + SS * SS // 2) // (SS * SS)
    out.mark_dirty()
    out.write_to_png(path)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for name, color in COLORS.items():
        for suffix, g in (("", None), ("-hover", name)):
            _png(dot(color, g, scale=SS), os.path.join(OUT, f"{name}{suffix}.png"))
        for suffix, g in (("", None), ("-hover", name)):     # icon theme: GTK renders SVG there
            svg = cairo.SVGSurface(os.path.join(ICONS, f"sonata-tl-{name}{suffix}.svg"), SIZE, SIZE)
            dot(color, g, svg)
            svg.finish()
    print("written to", os.path.normpath(OUT))
