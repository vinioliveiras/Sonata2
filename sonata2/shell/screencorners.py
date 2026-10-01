"""Rounded screen corners (macOS): the four corners of every display are
masked in black, as if the glass of the screen had round corners.
Settings > General > "Rounded screen corners" (appearance.json
"screen_corners", on by default).

The desktop: four tiny layer-shell surfaces per display (OVERLAY layer,
RADIUS px each, no input -- clicks go through), run by the menu bar
process; small on purpose: a full-screen overlay would be blended over
every frame. The lock screen and the login screen draw the same corners
in their own windows (CornersOverlay): ext-session-lock hides every other
surface."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Graphene, Gsk, Gtk  # noqa: E402

from .. import config  # noqa: E402

RADIUS = 10                 # px; macOS: about the menu bar's height / 2.4
CORNERS = ("tl", "tr", "bl", "br")
NAMESPACE = "sonata-screen-corners"     # (no "sonata2": Wayfire's blur rule must skip it)


def enabled() -> bool:
    from ..icons import APPEARANCE_DEFAULTS
    return bool(config.load("appearance", APPEARANCE_DEFAULTS).get("screen_corners", True))


BLEED = 1.0                 # the wedge reaches past the screen's edge: no half-lit outer pixel


def corner_path(corner: str, x: float, y: float, r: float) -> Gsk.Path:
    """The black wedge outside a quarter circle in an r x r square at (x, y)
    (and BLEED px past the screen's two edges)."""
    b = Gsk.PathBuilder.new()
    left, top = corner in ("tl", "bl"), corner in ("tl", "tr")
    cx, cy = (x if left else x + r), (y if top else y + r)            # the screen's corner
    ox, oy = (x + r if left else x), (y + r if top else y)            # the arc's centre side
    ex, ey = cx + (-BLEED if left else BLEED), cy + (-BLEED if top else BLEED)   # outside the screen
    b.move_to(ex, ey)
    b.line_to(ox, ey)
    b.line_to(ox, cy)
    # quarter circle from (ox, cy) to (cx, oy), bulging towards the corner
    b.conic_to(cx, cy, cx, oy, 0.70710678)
    b.line_to(ex, oy)
    b.close()
    return b.to_path()


def _black():
    from gi.repository import Gdk
    c = Gdk.RGBA()
    c.parse("black")
    return c


class CornersOverlay(Gtk.Widget):
    """All four corners over a full-screen window (lock / login screens)."""

    def __init__(self, radius: int = RADIUS):
        super().__init__(can_target=False, hexpand=True, vexpand=True)
        self.radius = radius

    def do_snapshot(self, snap):
        w, h, r = self.get_width(), self.get_height(), self.radius
        for c in CORNERS:
            x = 0 if c in ("tl", "bl") else w - r
            y = 0 if c in ("tl", "tr") else h - r
            snap.append_fill(corner_path(c, x, y, r), Gsk.FillRule.WINDING, _black())


class _Corner(Gtk.Widget):
    def __init__(self, corner, radius):
        super().__init__(can_target=False)
        self.corner, self.radius = corner, radius
        self.set_size_request(radius, radius)

    def do_snapshot(self, snap):
        snap.append_fill(corner_path(self.corner, 0, 0, self.radius), Gsk.FillRule.WINDING, _black())


class CornerWindow(Gtk.Window):
    """One corner of one display: a layer surface that takes no input."""

    def __init__(self, app, monitor, corner, radius=RADIUS):
        super().__init__(application=app, decorated=False, css_classes=["sonata-screen-corner"])
        self.set_child(_Corner(corner, radius))
        self.set_default_size(radius, radius)
        from . import layer
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, NAMESPACE)
            LS.set_monitor(self, monitor)
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP if corner in ("tl", "tr") else LS.Edge.BOTTOM, True)
            LS.set_anchor(self, LS.Edge.LEFT if corner in ("tl", "bl") else LS.Edge.RIGHT, True)
            LS.set_exclusive_zone(self, -1)              # the very corner, past the menu bar's zone
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
        self.connect("realize", lambda w: w.get_surface().connect(
            "layout", lambda *_: layer.set_input_region(w, [])))


class ScreenCorners:
    """The desktop's corners, on every display, following the setting live."""

    def __init__(self, app):
        from . import monitors
        self.app = app
        self.surfaces = None
        self._mon = config.watch("appearance", self.apply)
        self.monitors = monitors
        self.apply()

    def _create(self, m):
        if not enabled():
            return []
        wins = [CornerWindow(self.app, m, c) for c in CORNERS]
        for w in wins:
            w.present()
        return wins

    @staticmethod
    def _destroy(wins):
        for w in wins:
            w.destroy()

    def apply(self, *_a) -> None:
        on = enabled()
        if self.surfaces is None:
            self.surfaces = self.monitors.each(self._create, self._destroy)
            self._on = on
        elif on != self._on:
            self._on = on
            self.surfaces.rebuild()
