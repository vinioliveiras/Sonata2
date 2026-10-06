"""Layout checks for any Sonata surface (Vini: layout tests matter a lot).

Real widgets, laid out by GTK under xvfb, measured after allocation:

    overflows(root)       widgets sticking out of their parent (cut off, or a band beside them)
    overlaps(root)        siblings drawn over each other in a Box / Grid / FlowBox
    ellipsized(root)      labels whose text is cut with "…"
    geometry(root)        every widget's box, to compare before/after something (nothing jumps)
    shot(widget, name)    a PNG of it when SONATA_LAYOUT_SHOTS=<dir> is set (to look at)

Each returns a list of readable problems (empty: fine), so a failing test
says which widget, where and by how much."""
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

TOL = 1.0          # px: rounding
# their children move inside them on purpose (scrolling, sliding in, pages)
_SCROLLING = (Gtk.ScrolledWindow, Gtk.Viewport, Gtk.Revealer, Gtk.Stack, Adw.Carousel)
# placed past their parent's edge on purpose: the window buttons sit where
# macOS has them, over the header bar's own padding (ui/window.py)
ALLOW = ("traffic",)


def settle(ms: int = 150) -> None:
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def children(w):
    c = w.get_first_child()
    while c is not None:
        yield c
        c = c.get_next_sibling()


def shown(w) -> bool:
    return w.get_mapped() and w.get_width() > 0 and w.get_height() > 0 and w.get_opacity() > 0


def walk(root):
    """Shown widgets under root (root first)."""
    yield root
    for c in children(root):
        if shown(c) and not isinstance(c, Gtk.Popover):
            yield from walk(c)


def name(w) -> str:
    bits = [type(w).__name__]
    classes = [c for c in w.get_css_classes() if c not in ("horizontal", "vertical")]
    if classes:
        bits.append("." + ".".join(classes[:3]))
    label = getattr(w, "get_label", None)
    text = label() if callable(label) and not isinstance(w, Gtk.Frame) else None
    if isinstance(text, str) and text:
        bits.append(f' "{text[:24]}"')
    tip = w.get_tooltip_text()
    if tip and not text:
        bits.append(f" [{tip[:24]}]")
    return "".join(bits)


def path(w, root) -> str:
    parts = []
    while w is not None and w is not root:
        parts.append(name(w))
        w = w.get_parent()
    return " < ".join(parts[:3])


def rect(w, ref):
    ok, b = w.compute_bounds(ref)
    if not ok:
        return None
    return b.get_x(), b.get_y(), b.get_width(), b.get_height()


def turned(w, r) -> bool:
    """Rotated or scaled right now (a spinner, edit mode's jiggle, a zoom
    animation): its box in the parent isn't its own size."""
    return abs(r[2] - w.get_width()) > 0.5 or abs(r[3] - w.get_height()) > 0.5


def internal(w) -> bool:
    """A part GTK or libadwaita draws inside its own widget (a slider's knob)."""
    return type(w).__name__.endswith("Gizmo")


def overflows(root, allow=()) -> list:
    """A shown widget outside its parent's box. allow: css classes of
    widgets that hang out on purpose (a badge)."""
    out = []
    allow = tuple(allow) + ALLOW
    for w in walk(root):
        parent = w.get_parent()
        if w is root or parent is None or isinstance(parent, _SCROLLING) or internal(w):
            continue
        if any(w.has_css_class(c) for c in allow) or isinstance(w, Gtk.Popover):
            continue
        r = rect(w, parent)
        if r is None or turned(w, r):
            continue
        x, y, ww, hh = r
        pw, ph = parent.get_width(), parent.get_height()
        cut = {"left": -x, "top": -y, "right": x + ww - pw, "bottom": y + hh - ph}
        cut = {k: round(v, 1) for k, v in cut.items() if v > TOL}
        if cut:
            out.append(f"{path(w, root)}: out of {name(parent)} by {cut}")
    return out


def overlaps(root, allow=()) -> list:
    """Two shown children of a Box, Grid or FlowBox drawn over each other."""
    out = []
    for w in walk(root):
        if not isinstance(w, (Gtk.Box, Gtk.Grid, Gtk.FlowBox, Gtk.CenterBox)):
            continue
        kids = [c for c in children(w) if shown(c) and not isinstance(c, Gtk.Popover) and not internal(c)
                and not any(c.has_css_class(a) for a in allow)]
        boxes = [(c, r) for c in kids if (r := rect(c, w)) is not None and not turned(c, r)]
        for i, (a, ra) in enumerate(boxes):
            for b, rb in boxes[i + 1:]:
                if ra is None or rb is None:
                    continue
                dx = min(ra[0] + ra[2], rb[0] + rb[2]) - max(ra[0], rb[0])
                dy = min(ra[1] + ra[3], rb[1] + rb[3]) - max(ra[1], rb[1])
                if dx > TOL and dy > TOL:
                    out.append(f"{path(a, root)} and {name(b)} overlap {round(dx, 1)}x{round(dy, 1)}")
    return out


def ellipsized(root) -> list:
    """Labels shown with their text cut ("…")."""
    out = []
    for w in walk(root):
        if isinstance(w, Gtk.Label) and w.get_layout().is_ellipsized():
            out.append(f'{path(w, root)}: "{w.get_label()}" is cut')
    return out


def geometry(root) -> dict:
    """path -> (x, y, w, h) of every shown widget, relative to root."""
    seen = {}
    for w in walk(root):
        key = path(w, root)
        n = 0
        while (key, n) in seen:
            n += 1
        r = rect(w, root)
        seen[(key, n)] = tuple(round(v) for v in r) if r else None
    return seen


def moved(before: dict, after: dict, tol: float = TOL) -> list:
    out = []
    for key, a in before.items():
        b = after.get(key)
        if a and b and any(abs(p - q) > tol for p, q in zip(a, b)):
            out.append(f"{key[0]}: {a} -> {b}")
    return out


def problems(root, allow=()) -> list:
    return overflows(root, allow) + overlaps(root, allow)


def shot(widget, filename: str):
    """PNG of the widget into $SONATA_LAYOUT_SHOTS (nothing without it)."""
    folder = os.environ.get("SONATA_LAYOUT_SHOTS")
    if not folder or not widget.get_native():
        return None
    w, h = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable(widget=widget).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return None
    tex = widget.get_native().get_renderer().render_texture(node, None)
    os.makedirs(folder, exist_ok=True)
    out = os.path.join(folder, filename + ".png")
    tex.save_to_png(out)
    return out


def picture(widget):
    """The widget drawn on its own (no window behind it), as a PIL RGBA image."""
    import io
    from PIL import Image
    w, h = widget.get_width(), widget.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable(widget=widget).snapshot(snap, w, h)
    node = snap.to_node()
    tex = widget.get_native().get_renderer().render_texture(node, None)
    return Image.open(io.BytesIO(tex.save_to_png_bytes().get_data())).convert("RGBA")


def see_through(widget, min_alpha: int = 250) -> float:
    """Share of the widget's pixels the window behind would show through (a
    bar that should be opaque: 0)."""
    im = picture(widget)
    alpha = im.getchannel("A").getdata()
    return sum(1 for a in alpha if a < min_alpha) / max(1, len(alpha))
