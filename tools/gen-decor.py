#!/usr/bin/env python3
"""The bundled traffic-light pictures (the default, colourful look): PNGs for
the pixdecor title bars (windows that don't draw their own: terminals like
Alacritty, X11 apps) and SVGs for the icon theme (GTK, Steam). Drawn by
sonata2/trafficlights.py -- the same drawing makes Settings' other looks
(Graphite, Black & White, Custom) at run time.
python3 tools/gen-decor.py"""
import os
import sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from sonata2 import trafficlights as T  # noqa: E402
from sonata2.ui.tokens import FRAME, TL_COLORS  # noqa: E402

OUT = os.path.join(ROOT, "sonata2", "data", "decor")
ICONS = os.path.join(ROOT, "sonata2", "data", "icons", "Sonata", "apps", "scalable")

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    size = FRAME["dot"]
    for name, color in TL_COLORS.items():
        for suffix, g in (("", None), ("-hover", name)):
            T._png(os.path.join(OUT, f"{name}{suffix}.png"), size, color, g)
            T._svg(os.path.join(ICONS, f"sonata-tl-{name}{suffix}.svg"), size, color, g)
    print("written to", os.path.normpath(OUT))
