"""The "fake" glass, at no cost per frame: a small picture of a display
(grim), blurred once, drawn under a surface's panels in place of the
compositor's blur -- which blurs a surface's whole changed area again on
every frame it changes. The Game Bar's (over a running game) and Notification
Center's (Vini: laggy with many notifications -- the column filled the
display's height, and each frame of scrolling or sliding was a blur pass
over all of it). Both are in glass.NEVER_BLUR.

    capture(connector, done)                 # done(texture or None), blurred, on the main loop
"""
import shutil

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

FROST_SCALE = 0.25          # the display's picture at this scale (blurred: no detail needed)
FROST_BLUR = 28             # blurred this much (px at full size)


def available() -> bool:
    return shutil.which("grim") is not None


def frost_command(output) -> list:
    """grim: the display's picture, uncompressed (fast). At full size: grim
    shrinking it itself (-s) took ~160 ms on a 1080p display, ~25 ms
    without -- blur_texture shrinks it on the GPU instead (Vini: the Game
    Bar opened solid, then turned to glass). PPM: its pixels as they are,
    made a texture without decoding (texture_from; a PNG of a 4K display
    took ~16 ms to decode on the main loop)."""
    cmd = ["grim", "-t", "ppm"]
    if output:
        cmd += ["-o", output]
    return cmd + ["-"]


def texture_from(data):
    """grim's picture (GLib.Bytes: a binary PPM, else anything GDK reads) as a
    texture; None when it isn't one. The PPM's pixels are used in place."""
    if data is None or not data.get_size():
        return None
    raw = data.get_data()
    if raw[:2] == b"P6":
        fields, i = [], 2
        while len(fields) < 3 and i < len(raw):            # width, height, maxval (and comments)
            while i < len(raw) and raw[i:i + 1].isspace():
                i += 1
            if raw[i:i + 1] == b"#":
                while i < len(raw) and raw[i:i + 1] != b"\n":
                    i += 1
                continue
            j = i
            while j < len(raw) and not raw[j:j + 1].isspace():
                j += 1
            fields.append(raw[i:j])
            i = j
        try:
            w, h, maxval = (int(f) for f in fields)
        except ValueError:
            return None
        i += 1                                             # the one whitespace before the pixels
        if maxval != 255 or w <= 0 or h <= 0 or len(raw) - i < w * h * 3:
            return None
        pixels = GLib.Bytes.new_from_bytes(data, i, w * h * 3)
        return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.R8G8B8, pixels, w * 3)
    try:
        return Gdk.Texture.new_from_bytes(data)
    except GLib.Error:
        return None


def blur_texture(renderer, texture):
    """The picture shrunk to FROST_SCALE and blurred once (GSK, on the GPU)."""
    if renderer is None:
        return texture
    from gi.repository import Graphene, Gsk
    w = max(1, round(texture.get_width() * FROST_SCALE))
    h = max(1, round(texture.get_height() * FROST_SCALE))
    snap = Gtk.Snapshot()
    r = Graphene.Rect().init(0, 0, w, h)
    snap.push_clip(r)
    snap.push_blur(FROST_BLUR * FROST_SCALE)
    snap.append_scaled_texture(texture, Gsk.ScalingFilter.TRILINEAR,      # (mipmapped: no shimmer)
                               Graphene.Rect().init(-8, -8, w + 16, h + 16))   # no dark edges
    snap.pop()
    snap.pop()
    node = snap.to_node()
    return renderer.render_texture(node, r) if node is not None else texture


def capture(output, done, renderer_of=None) -> bool:
    """The display `output` (a connector, None: grim's choice) pictured and
    blurred: done(texture) on the main loop, done(None) if it couldn't be.
    renderer_of(): the renderer to blur with, asked when the picture comes
    (the window may only be realized by then). False: no grim, nothing asked."""
    if not available():
        return False
    try:
        proc = Gio.Subprocess.new(frost_command(output),
                                  Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error:
        return False

    def finished(p, res):
        try:
            _ok, out, _err = p.communicate_finish(res)
            tex = texture_from(out)
        except GLib.Error:
            tex = None
        renderer = renderer_of() if renderer_of else None
        # never the picture unblurred: without a renderer yet, no glass this
        # time (the panels solid) -- not a sharp screenshot behind them
        done(blur_texture(renderer, tex) if tex is not None and renderer is not None else None)
    proc.communicate_async(None, None, finished)
    return True
