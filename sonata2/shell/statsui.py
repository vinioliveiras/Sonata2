"""CPU, GPU, memory, network and FPS for the menu bar and Control Center
(Vini; off by default). The figures come from backend/stats.py, read only
while one of these is on screen.

    KINDS                               # cpu gpu ram net fps
    menu_item(kind, style, on_click)    # menu bar: "text" or "graph"
    module(kind)                        # Control Center module (2x1)"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gsk, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from ..backend import stats as S  # noqa: E402

KINDS = ("cpu", "gpu", "ram", "net", "fps")
TITLES = {"cpu": "CPU", "gpu": "GPU", "ram": "Memory", "net": "Network", "fps": "FPS"}
CAPTIONS = {"cpu": "CPU", "gpu": "GPU", "ram": "RAM", "net": "NET", "fps": "FPS"}     # beside a menu bar graph
ICONS = {"cpu": "cpu-symbolic", "gpu": "gpu-symbolic", "ram": "media-memory-symbolic",
         "net": "network-transmit-receive-symbolic", "fps": "speedometer-symbolic"}
SERIES = {"cpu": ("cpu",), "gpu": ("gpu",), "ram": ("ram",), "net": ("down", "up"), "fps": ("fps",)}
PERCENT = {"cpu", "gpu", "ram"}            # 0..100 scale; the others scale to their own peak

ui.register("""
.stat-item label { font-feature-settings: "tnum"; }
.stat-caption { font-size: 9px; font-weight: 700; opacity: 0.75; margin-right: 3px; }
.cc-stat .cc-stat-value { font-feature-settings: "tnum"; font-weight: 600; }
.cc-stat .cc-stat-speeds { font-size: %(text_small)s; }
.cc-stat .cc-stat-hint { font-size: %(text_small)s; opacity: 0.6; }
""", key="statsui")


class Graph(Gtk.Widget):
    """A small live area graph (one or two series), drawn in the label colour;
    each new sample slides in from the right."""

    def __init__(self, series, width: int, height: int, percent: bool):
        super().__init__(width_request=width, height_request=height, valign=Gtk.Align.CENTER)
        self.series, self.percent = series, percent
        self.data = {k: [] for k in series}

    def push(self, history: dict) -> None:
        self.data = {k: list(history.get(k, [])) for k in self.series}
        self.queue_draw()

    def do_snapshot(self, snap) -> None:
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        peak = 100.0 if self.percent else max([1.0] + [v for vals in self.data.values() for v in vals])
        colors = (ui.rgba("label"), ui.rgba("accent"))
        n = S.HISTORY
        for i, k in enumerate(self.series):
            vals = self.data.get(k) or []
            if not vals:
                continue
            col = colors[i % 2].copy()
            b = Gsk.PathBuilder()
            step = w / max(1, n - 1)
            x0 = w - step * (len(vals) - 1)
            b.move_to(x0, h)
            for j, v in enumerate(vals):
                b.line_to(x0 + j * step, h - max(0.0, min(1.0, v / peak)) * (h - 1))
            b.line_to(w, h)
            b.close()
            col.alpha = 0.55 if i == 0 else 0.8
            snap.append_fill(b.to_path(), Gsk.FillRule.WINDING, col)


class _Live:
    """Subscribes to the figures while its widget is on screen."""

    def __init__(self, widget, update):
        self.update = update
        widget.connect("map", lambda *_a: S.Stats.shared().subscribe(self.update))
        widget.connect("unmap", lambda *_a: S.Stats.shared().unsubscribe(self.update))


def menu_item(kind: str, style: str) -> Gtk.Box:
    """The menu bar's content for one figure: "CPU 12%" or a caption + graph."""
    box = Gtk.Box(spacing=0, valign=Gtk.Align.CENTER, css_classes=["stat-item"])
    if style == "graph":
        box.append(Gtk.Label(label=CAPTIONS[kind], css_classes=["stat-caption"]))
        graph = Graph(SERIES[kind], 30, 14, kind in PERCENT)
        box.append(graph)

        def update(r):
            graph.push(r.history)
            box.set_tooltip_text(S.text(kind, r))
    else:
        label = Gtk.Label(label=S.text(kind, S.Reading()), width_chars=len(S.text(kind, S.Reading())))
        box.append(label)

        def update(r):
            label.set_label(S.text(kind, r))
    box._live = _Live(box, update)
    return box


def module(kind: str) -> Gtk.Widget:
    """Control Center module: icon, title, value and the graph."""
    head = Gtk.Box(spacing=8)
    head.append(Gtk.Image(icon_name=ICONS[kind], pixel_size=16))
    # nothing here may widen Control Center (its size is fixed): texts shrink with an ellipsis
    head.append(Gtk.Label(label=TITLES[kind], xalign=0, hexpand=True, css_classes=["panel-module-title"],
                          ellipsize=Pango.EllipsizeMode.END, width_chars=1))
    value = Gtk.Label(label="–", xalign=1, css_classes=["cc-stat-value"], ellipsize=Pango.EllipsizeMode.START,
                      width_chars=1, max_width_chars=12)
    graph = Graph(SERIES[kind], 1, 26, kind in PERCENT)        # as wide as the module, never wider
    graph.set_hexpand(True)
    graph.set_vexpand(True)                                     # and as tall as its cells leave
    graph.set_valign(Gtk.Align.FILL)
    hint = Gtk.Label(label="", xalign=0, css_classes=["cc-stat-hint"], visible=False, wrap=True,
                     width_chars=1, natural_wrap_mode=Gtk.NaturalWrapMode.WORD)
    if kind == "net":                    # two speeds: a line of their own under the title
        value.set_xalign(0)
        value.set_ellipsize(Pango.EllipsizeMode.END)
        value.set_max_width_chars(-1)
        value.add_css_class("cc-stat-speeds")
        box = ui.panel.module(head, value, graph, hint, spacing=4)
    else:
        head.append(value)
        box = ui.panel.module(head, graph, hint, spacing=6)
    box.add_css_class("cc-stat")

    def update(r):
        t = S.text(kind, r)
        value.set_label(t.split(" ", 1)[1] if kind in ("cpu", "gpu", "ram") else
                        (t.replace("  ", " ") if kind == "net" else ("–" if r.fps is None else str(r.fps))))
        graph.push(r.history)
        h = S.FPS_HINTS.get(r.fps_state, "") if kind == "fps" else ""
        hint.set_label(h)
        hint.set_visible(bool(h))
    box._live = _Live(box, update)
    return box
