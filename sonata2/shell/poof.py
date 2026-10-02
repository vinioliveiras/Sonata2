"""The puff of smoke where an item dragged out of the Dock disappears
(macOS): a small cloud that swells and fades in ~0.35 s, at the pointer.

The pointer's place comes from Wayfire (window-rules/get_cursor_position,
layout coordinates) and the display under it; the cloud is a tiny
layer-shell surface there (overlay, no input). Without Wayfire it shows
where the Dock last saw the icon."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

SIZE = 96                     # the cloud's box (px)

from .. import ui  # noqa: E402

# a see-through window: only the cloud shows (the theme painted it as a square)
ui.register("window.sonata-poof { background: none; box-shadow: none; }", key="poof")
POOF_MS = 360
# the puffs: (offset x, offset y, radius) as fractions of SIZE, from the centre
PUFFS = ((0.0, 0.0, 0.20), (-0.17, 0.05, 0.14), (0.17, 0.04, 0.15), (-0.08, -0.15, 0.13),
         (0.10, -0.14, 0.12), (0.0, 0.17, 0.13), (-0.2, -0.08, 0.09), (0.21, -0.06, 0.10))


def cursor() -> tuple:
    """(x, y) of the pointer in Wayfire's layout, or None."""
    try:
        from ..wl.wfipc import WayfireIPC
        ipc = WayfireIPC()
        for m in ("window-rules/get_cursor_position", "window-rules/get-cursor-position"):
            r = ipc.call(m) or {}
            pos = r.get("pos")
            if pos:
                return float(pos["x"]), float(pos["y"])
    except Exception:
        pass
    return None


def frame(t: float) -> list:
    """The puffs at t (0..1): [(cx, cy, radius, alpha)] in SIZE units. They
    swell outwards, then fade (macOS: five frames of a little cloud)."""
    grow = 1 - (1 - t) ** 3                         # ease-out
    alpha = 1.0 if t < 0.35 else max(0.0, 1 - (t - 0.35) / 0.65)
    out = []
    for dx, dy, r in PUFFS:
        spread = 0.55 + 0.75 * grow
        out.append((0.5 + dx * spread, 0.5 + dy * spread, r * (0.55 + 0.6 * grow), alpha))
    return out


class _Cloud(Gtk.Widget):
    def __init__(self):
        super().__init__(can_target=False)
        self.set_size_request(SIZE, SIZE)
        self.t = 0.0

    def do_snapshot(self, snap):
        puffs = [p for p in frame(self.t) if p[3] > 0]
        if not puffs:
            return
        a = puffs[0][3]

        def cloud(dy=0.0, grow=0.0):           # all the puffs as one shape: no darker overlaps
            b = Gsk.PathBuilder.new()
            for cx, cy, r, _a in puffs:
                b.add_circle(Graphene.Point().init(cx * SIZE, cy * SIZE + dy), r * SIZE + grow)
            return b.to_path()
        shade, fill = Gdk.RGBA(), Gdk.RGBA()
        shade.parse(f"rgba(0, 0, 0, {0.22 * a:.3f})")
        fill.parse(f"rgba(240, 240, 244, {0.95 * a:.3f})")
        snap.append_fill(cloud(dy=1.5, grow=1.0), Gsk.FillRule.WINDING, shade)   # a soft edge below
        snap.append_fill(cloud(), Gsk.FillRule.WINDING, fill)


class Poof(Gtk.Window):
    def __init__(self, app, monitor, x: float, y: float):
        super().__init__(application=app, decorated=False, css_classes=["sonata-poof"])
        self.cloud = _Cloud()
        self.set_child(self.cloud)
        self.set_default_size(SIZE, SIZE)
        from . import layer
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata-poof")          # (no "sonata2": not blurred)
            if monitor is not None:
                LS.set_monitor(self, monitor)
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_anchor(self, LS.Edge.LEFT, True)
            LS.set_margin(self, LS.Edge.LEFT, max(0, int(x - SIZE / 2)))
            LS.set_margin(self, LS.Edge.TOP, max(0, int(y - SIZE / 2)))
            LS.set_exclusive_zone(self, -1)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
        self.connect("realize", lambda w: w.get_surface().connect(
            "layout", lambda *_: layer.set_input_region(w, [])))
        self._t0 = None
        self.add_tick_callback(self._tick)

    def _tick(self, _w, clock):
        now = clock.get_frame_time()
        self._t0 = self._t0 or now
        self.cloud.t = min(1.0, (now - self._t0) / 1000 / POOF_MS)
        self.cloud.queue_draw()
        if self.cloud.t >= 1.0:
            GLib.idle_add(lambda: (self.destroy(), False)[1])
            return False
        return True


def at_pointer(app, fallback=None):
    """Show the puff where the pointer is (fallback: (monitor, x, y))."""
    pos = cursor()
    monitor, x, y = fallback or (None, 0, 0)
    if pos is not None:
        display = Gdk.Display.get_default()
        mons = display.get_monitors()
        for i in range(mons.get_n_items()):
            m = mons.get_item(i)
            g = m.get_geometry()
            if g.x <= pos[0] < g.x + g.width and g.y <= pos[1] < g.y + g.height:
                monitor, x, y = m, pos[0] - g.x, pos[1] - g.y
                break
    if monitor is None and fallback is None:
        return None
    w = Poof(app, monitor, x, y)
    w.present()
    return w

