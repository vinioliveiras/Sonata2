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
    """grim: the display's picture, small and uncompressed (fast)."""
    cmd = ["grim", "-s", str(FROST_SCALE), "-l", "0"]
    if output:
        cmd += ["-o", output]
    return cmd + ["-"]


def blur_texture(renderer, texture):
    """The picture blurred once (GSK), at its small size."""
    if renderer is None:
        return texture
    from gi.repository import Graphene
    w, h = texture.get_width(), texture.get_height()
    snap = Gtk.Snapshot()
    r = Graphene.Rect().init(0, 0, w, h)
    snap.push_clip(r)
    snap.push_blur(FROST_BLUR * FROST_SCALE)
    snap.append_texture(texture, Graphene.Rect().init(-8, -8, w + 16, h + 16))   # no dark edges
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
            tex = Gdk.Texture.new_from_bytes(out) if out is not None and out.get_size() else None
        except GLib.Error:
            tex = None
        if tex is not None:
            tex = blur_texture(renderer_of() if renderer_of else None, tex)
        done(tex)
    proc.communicate_async(None, None, finished)
    return True
