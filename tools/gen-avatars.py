#!/usr/bin/env python3
"""Generate Sonata's stock user pictures (sonata2/data/avatars/*.png, 256 px):
a Noto Color Emoji glyph (Apache 2.0 / OFL) on a soft Big Sur-style gradient.
  tools/gen-avatars.py [path/to/NotoColorEmoji.ttf]"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = sys.argv[1] if len(sys.argv) > 1 else "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"
OUT = os.path.join(os.path.dirname(__file__), "..", "sonata2", "data", "avatars")
SIZE = 256
# (name, emoji, gradient top, gradient bottom)
PICTURES = [
    ("cat", "🐱", "#ffd29a", "#ff9f6b"), ("dog", "🐶", "#c9e7ff", "#7fb6ff"),
    ("fox", "🦊", "#ffe0b2", "#ff8a65"), ("panda", "🐼", "#d7f5d0", "#86d39a"),
    ("owl", "🦉", "#e3d7ff", "#a48bff"), ("penguin", "🐧", "#d0f0ff", "#6ec6ff"),
    ("flower", "🌸", "#ffe3ef", "#ff9ec4"), ("sunflower", "🌻", "#fff4c2", "#ffc947"),
    ("cactus", "🌵", "#e6f7d9", "#9ed98a"), ("leaf", "🍃", "#dff5e8", "#6fcf97"),
    ("wave", "🌊", "#d6ecff", "#4f9cff"), ("mountain", "🏔️", "#e6ecf5", "#9fb3d1"),
    ("rocket", "🚀", "#e0e3ff", "#7c83ff"), ("guitar", "🎸", "#ffe6d6", "#ff9d7a"),
    ("soccer", "⚽", "#e8f5e9", "#81c784"), ("donut", "🍩", "#ffe9f2", "#f48fb1"),
    ("coffee", "☕", "#f3e5d8", "#c49a6c"), ("gamepad", "🎮", "#e5e1ff", "#8e7dff"),
]


def gradient(top, bottom):
    t = tuple(int(top[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(bottom[i:i + 2], 16) for i in (1, 3, 5))
    img = Image.new("RGB", (SIZE, SIZE))
    d = ImageDraw.Draw(img)
    for y in range(SIZE):
        f = y / (SIZE - 1)
        d.line([(0, y), (SIZE, y)], fill=tuple(int(t[i] + (b[i] - t[i]) * f) for i in range(3)))
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    font = ImageFont.truetype(FONT, 109)          # Noto's bitmap size
    for name, emoji, top, bottom in PICTURES:
        glyph = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((80, 80), emoji, font=font, embedded_color=True, anchor="mm")
        glyph = glyph.crop(glyph.getbbox()).resize((150, 150), Image.LANCZOS)
        img = gradient(top, bottom).convert("RGBA")
        img.alpha_composite(glyph, ((SIZE - 150) // 2, (SIZE - 150) // 2 + 3))
        img.convert("RGB").save(os.path.join(OUT, f"{name}.png"), optimize=True)
    with open(os.path.join(OUT, "LICENSE.txt"), "w") as f:
        f.write("Sonata stock user pictures: emoji from Noto Color Emoji (Google, Apache License 2.0 / "
                "SIL OFL 1.1) on generated gradients (tools/gen-avatars.py).\n")
    print(len(PICTURES), "pictures ->", os.path.normpath(OUT))


if __name__ == "__main__":
    main()
