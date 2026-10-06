"""Draw on the screen while recording or sharing it (Vini; also any time:
Super+Shift+D). The recording / sharing pill gets a pen: a click turns
drawing on and shows the palette under the menu bar, another click (or Esc)
gives the pointer back to the apps -- the marks stay until Clear, or fade
away by themselves (the palette's clock).

    draw = livedraw.get(app)          # one per menu bar process
    draw.toggle(output=None)          # on / off (the pill's pen, the shortcut)
    draw.stop()                       # off and cleared (the recording or sharing ended)

One transparent surface per display above everything (layer-shell overlay,
"sonata2-draw"): it takes the pointer only while drawing is on, otherwise
clicks go through it to the apps. The marks are Preview's Markup marks
(preview/markup.py: the same tools and renderer). The palette is its own
surface ("sonata2-draw-palette") so it can be left out of what's captured."""
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import layer  # noqa: E402

FADE_AFTER_S, FADE_S = 3.0, 0.8          # fading marks: shown this long, then fade out
TOOLS = (("pen", "Pen"), ("hl", "Highlighter"), ("shape", "Shapes"), ("text", "Text"), ("emoji", "Emoji"))
COLORS = ("#ff3b30", "#ffcc00", "#34c759", "#007aff", "#ffffff")
PALETTE_NS, OVERLAY_NS = "sonata2-draw-palette", "sonata2-draw"
PALETTE_SIZE = (560, 62)                 # before the palette has been laid out
SETTLE_MS = 150                          # the input once more after on/off (surfaces map late)

ui.register("""
window.sonata-draw, window.sonata-draw > contents { background: none; box-shadow: none; }
window.sonata-draw-palette, window.sonata-draw-palette > contents { background: none; box-shadow: none; }
.draw-palette { background: %(panel_material)s; border-radius: %(r_dialog)s; margin: 6px 12px 16px 12px;
  box-shadow: 0 0 0 0.5px %(hairline)s, 0 10px 30px rgba(0,0,0,0.3); }
.draw-palette .pv-markup { border-bottom: none; padding: 4px 6px; }
.rec-pill button.rec-pen { min-width: 18px; min-height: 18px; padding: 0; margin: 1px 0; border-radius: 6px;
  background: none; border: none; box-shadow: none; color: %(label)s; }
.rec-pill button.rec-pen.on { background: %(accent)s; color: %(label_on_accent)s; }
""", key="livedraw")


class Overlay(Gtk.Window):
    """One display's drawing surface."""

    def __init__(self, app, monitor, owner):
        super().__init__(application=app, title="Drawing", decorated=False, css_classes=["sonata-draw"])
        from ..preview.markup import MarkupLayer
        self.owner = owner
        self.monitor = monitor
        self.layer = MarkupLayer(self._rect, lambda: None, on_change=lambda: owner.changed(self.layer))
        self.layer.follow = owner._sync
        self.layer.set_cursor(Gdk.Cursor.new_from_name("crosshair"))
        self.set_child(self.layer)
        LS = layer.layer_shell()
        if LS:
            layer.overlay_fullscreen(self, OVERLAY_NS)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
            LS.set_monitor(self, monitor)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, kv, _k, st: kv == Gdk.KEY_Escape and (owner.set_on(False), True)[1])
        self.add_controller(keys)
        self.connect("map", lambda *_: self.pointer(owner.on))
        # Vini: drawing worked with the palette closed and not with it open.
        # The input region was set once per on/off, and went stale when the
        # surface was laid out again (or mapped late): every layout sets it again.
        self.connect("realize", lambda *_: self.get_surface().connect(
            "layout", lambda *_a: GLib.idle_add(lambda: (self.pointer(owner.on), False)[1])))

    def _rect(self):
        return 0, 0, self.get_width(), self.get_height()

    def region(self, on: bool) -> list:
        """Where the pointer comes to the drawing while it's on: the whole
        display (its own size, known before GTK has laid the window out -- at
        map it was 0 x 0, and only one pixel took the clicks: "the screen
        stays clickable", Vini), less the menu bar (the pill's pen turns it
        off) and the palette."""
        if not on:
            return []
        g = self.monitor.get_geometry() if self.monitor is not None else None
        W, H = (g.width, g.height) if g else (self.get_width(), self.get_height())
        from .menubar_size import height
        top = height()
        pal = self.owner.palette
        if getattr(self.owner, "palette_on", None) is not self.monitor:
            return [(0, top, W, H - top)]          # the palette is on another display: all of this one
        pw, ph = PALETTE_SIZE
        if pal is not None and pal.get_mapped() and pal.get_width() > 0:
            pw, ph = pal.get_width(), pal.get_height()
        x0, band = (W - pw) // 2, top + ph
        return [(0, band, W, H - band), (0, top, x0, ph), (x0 + pw, top, W - x0 - pw, ph)]

    def pointer(self, on: bool) -> None:
        """Drawing: the pointer comes here; off: clicks go through to the apps."""
        if not self.get_mapped():
            return
        layer.set_input_region(self, self.region(on))
        LS = layer.layer_shell()
        if LS and LS.is_layer_window(self):
            LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND if on else LS.KeyboardMode.NONE)
        # Wayland takes a new input region with the surface's next frame: with
        # nothing to redraw it waited for the next on/off, one step behind
        # (Vini's video: drawing with the palette closed, not with it open)
        self.layer.queue_draw()
        self.queue_draw()


class Palette(Gtk.Window):
    def __init__(self, app, owner):
        super().__init__(application=app, title="Drawing Tools", decorated=False, resizable=False,
                         css_classes=["sonata-draw-palette"])
        from ..preview.markup import MarkupBar
        self.owner = owner
        actions = (("fade", "Marks fade away by themselves", owner.toggle_fade),
                   ("undo", "Undo", owner.undo), ("trash", "Clear the screen", owner.clear),
                   ("close", "Stop drawing (Esc)", lambda: owner.set_on(False)))
        self.bar = MarkupBar(owner.proto, tools=TOOLS, colors=COLORS, actions=actions)
        frame = Gtk.Box(css_classes=["draw-palette"])
        frame.append(self.bar)
        self.set_child(frame)
        self.fade_btn = [c for c in _children(self.bar.box) if c.get_tooltip_text() ==
                         "Marks fade away by themselves"][0]
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, PALETTE_NS)
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_exclusive_zone(self, -1)
            from .menubar_size import height
            LS.set_margin(self, LS.Edge.TOP, height())   # right under the menu bar's pill
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
        self.connect("map", lambda *_: GLib.timeout_add(50, lambda: (owner.regions(), False)[1]))

    def show_on(self, monitor) -> None:
        LS = layer.layer_shell()
        if LS and monitor is not None:
            LS.set_monitor(self, monitor)
        (self.fade_btn.add_css_class if self.owner.fade else self.fade_btn.remove_css_class)("on")
        self.present()


def _children(w):
    c = w.get_first_child()
    while c is not None:
        yield c
        c = c.get_next_sibling()


class LiveDraw:
    def __init__(self, app):
        from ..preview.markup import MarkupLayer
        self.app = app
        self.on = False
        self.fade = False
        self.overlays = {}                 # connector -> Overlay
        # the palette's settings live on a layer of no display: every
        # overlay's layer takes its tool, colour and width from it
        self.proto = MarkupLayer(lambda: None, lambda: None)
        self.palette = None
        self.listeners = []                # fn(on): the pills' pens
        self._fade_src = 0
        self.palette_on = None             # the display showing the palette
        self.history = []                  # the overlays in the order their marks were made (Undo)

    # -- the palette's choices: each display's layer takes them before a mark -----------------------
    def _each(self):
        return [o.layer for o in self.overlays.values()]

    def _sync(self, lay):
        p = self.proto
        lay.tool, lay.shape, lay.emoji, lay.color, lay.width = p.tool, p.shape, p.emoji, p.color, p.width

    # -- on / off -------------------------------------------------------------------------------
    def _monitors(self):
        mons = Gdk.Display.get_default().get_monitors()
        return [mons.get_item(i) for i in range(mons.get_n_items())]

    def _monitor(self, output=None):
        mons = self._monitors()
        return next((m for m in mons if m.get_connector() == output), mons[0] if mons else None)

    def regions(self) -> None:
        """The overlays' input again (the palette's real size is known now)."""
        for ov in self.overlays.values():
            ov.pointer(self.on)

    def toggle(self, output=None) -> None:
        self.set_on(not self.on, output)

    def set_on(self, on: bool, output=None) -> None:
        self.on = on
        if on:
            for m in self._monitors():
                key = m.get_connector() or str(id(m))
                if key not in self.overlays:
                    self.overlays[key] = Overlay(self.app, m, self)
                ov = self.overlays[key]
                self._sync(ov.layer)
                ov.present()
                ov.pointer(True)
            if self.palette is None:
                self.palette = Palette(self.app, self)
            self.palette_on = self._monitor(output)
            self.palette.show_on(self.palette_on)
            self.regions()                         # (the overlays above asked before it was placed)
        else:
            for ov in self.overlays.values():
                ov.pointer(False)
                if not ov.layer.items:
                    ov.set_visible(False)          # nothing drawn: nothing left over the screen
            if self.palette is not None:
                self.palette.set_visible(False)
        for fn in list(self.listeners):
            fn(on)
        GLib.timeout_add(SETTLE_MS, lambda: (self.regions(), False)[1])   # whatever mapped since

    def stop(self) -> None:
        """The recording or sharing ended: off, and the screen clean."""
        self.clear()
        self.set_on(False)

    def clear(self) -> None:
        for ov in self.overlays.values():
            if ov.layer.items:
                ov.layer._snapshot()
                ov.layer.items = []
                ov.layer.sel = None
                ov.layer.queue_draw()
            if not self.on:
                ov.set_visible(False)

    def undo(self) -> None:
        """The last mark made, on whichever display (it undid one on every display)."""
        while self.history:
            lay = self.history.pop()
            if any(ov.layer is lay for ov in self.overlays.values()) and lay._undo:
                lay.undo()
                return

    # -- fading -----------------------------------------------------------------------------------
    def toggle_fade(self) -> None:
        self.fade = not self.fade
        if not self.fade:                          # off mid-fade: the marks whole again
            for lay in self._each():
                for it in lay.items:
                    it.pop("o", None)
                lay.queue_draw()
        if self.palette is not None:
            (self.palette.fade_btn.add_css_class if self.fade else self.palette.fade_btn.remove_css_class)("on")
        if self.fade:
            now = time.monotonic()
            for lay in self._each():
                for it in lay.items:
                    it["ts"] = now
            self._arm_fade()

    def changed(self, lay=None) -> None:
        """A mark was added (or changed): stamped, so it can fade."""
        now = time.monotonic()
        if lay is not None:
            n = len(lay._undo)                     # a new step on this display (not an undo)
            if n > getattr(lay, "_steps_seen", 0):
                self.history.append(lay)
            lay._steps_seen = n
        for lay in self._each():
            for it in lay.items:
                it.setdefault("ts", now)
        if self.fade:
            self._arm_fade()

    def _arm_fade(self) -> None:
        if not self._fade_src:
            self._fade_src = GLib.timeout_add(33, self._fade_tick)

    def _fade_tick(self) -> bool:
        if not self.fade:
            self._fade_src = 0
            return False
        now, alive = time.monotonic(), False
        for ov in self.overlays.values():
            lay, keep = ov.layer, []
            for it in lay.items:
                age = now - it.get("ts", now)
                if age < FADE_AFTER_S + FADE_S:
                    it["o"] = 1.0 if age < FADE_AFTER_S else max(0.0, 1 - (age - FADE_AFTER_S) / FADE_S)
                    keep.append(it)
            if len(keep) != len(lay.items) or keep:
                lay.items = keep
                lay.queue_draw()
            alive = alive or bool(keep)
            if not keep and not self.on:
                ov.set_visible(False)
        if not alive:
            self._fade_src = 0
            return False
        return True


_INSTANCE = {}


def get(app) -> LiveDraw:
    if "d" not in _INSTANCE:
        _INSTANCE["d"] = LiveDraw(app)
    return _INSTANCE["d"]


def pen_button(app, output_fn=lambda: None) -> Gtk.Button:
    """The pills' pen: drawing on / off (blue while on)."""
    from ..preview.markup import _glyph
    b = Gtk.Button(css_classes=["rec-pen"], tooltip_text="Draw on the screen (Super+Shift+D)", can_focus=False,
                   valign=Gtk.Align.CENTER)
    b.set_child(_glyph("pen", 12))
    d = get(app)
    b.connect("clicked", lambda *_: d.toggle(output_fn()))

    def follow(on):
        (b.add_css_class if on else b.remove_css_class)("on")
    d.listeners.append(follow)
    follow(d.on)
    return b
