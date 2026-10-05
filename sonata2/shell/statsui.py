"""CPU, GPU, memory, network and FPS for the menu bar and Control Center
(Vini; off by default). The figures come from backend/stats.py, read only
while one of these is on screen.

    KINDS                               # cpu gpu vram temp ram net fps
    menu_item(kind, style, on_click)    # menu bar: "text" or "graph"
    module(kind)                        # Control Center module (2x1)"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gsk, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from ..backend import stats as S  # noqa: E402

KINDS = S.KINDS                            # cpu, gpu or gpu_<card> for each card, ram, net, fps
TITLES = {"cpu": "CPU", "gpu": "GPU", "ram": "Memory", "net": "Network", "fps": "FPS"}
CAPTIONS = {"cpu": "CPU", "gpu": "GPU", "ram": "RAM", "net": "NET", "fps": "FPS"}     # beside a menu bar graph
ICONS = {"cpu": "cpu-symbolic", "gpu": "gpu-symbolic", "ram": "media-memory-symbolic",
         "net": "network-transmit-receive-symbolic", "fps": "speedometer-symbolic"}
SERIES = {"cpu": ("cpu",), "gpu": ("gpu",), "ram": ("ram",), "net": ("down", "up"), "fps": ("fps",)}
PERCENT = {"cpu", "gpu", "ram"}            # 0..100 scale; the others scale to their own peak
SHORT = {"NVIDIA": "NV", "Intel": "INT"}   # a card's maker beside a menu bar graph
for _k, _maker in S.GPU_MAKERS.items():    # two cards or more: one of each per card
    TITLES[_k] = f"GPU ({_maker})"
    CAPTIONS[_k] = SHORT.get(_maker, _maker)
    ICONS[_k] = "gpu-symbolic"
    SERIES[_k] = (_k,)
    PERCENT.add(_k)
TITLES["temp_cpu"], CAPTIONS["temp_cpu"] = "CPU Temperature", "CPU°"
ICONS["temp_cpu"], SERIES["temp_cpu"] = "temperature-symbolic", ("temp_cpu",)
PERCENT.add("temp_cpu")                    # °C on a 0..100 scale
for _k in S.TEMP_KINDS:                    # each card's temperature (Vini)
    _maker = S.TEMP_MAKERS_BY_KIND[_k]
    TITLES[_k] = "GPU Temperature" if _k == "temp_gpu" else f"GPU Temperature ({_maker})"
    CAPTIONS[_k] = "GPU°" if _k == "temp_gpu" else f"{SHORT.get(_maker, _maker)}°"
    ICONS[_k] = "freon-gpu-temperature-symbolic"
    SERIES[_k] = (_k,)
    PERCENT.add(_k)
for _k, _key in S.VRAM_KINDS.items():      # each card's video memory (Vini)
    _maker = S.VRAM_MAKERS_BY_KIND[_k]
    TITLES[_k] = "Video Memory" if _k == "vram" else f"Video Memory ({_maker})"
    CAPTIONS[_k] = "VRAM" if _k == "vram" else f"VRAM {SHORT.get(_maker, _maker)}"
    ICONS[_k] = "media-memory-symbolic"
    SERIES[_k] = (_k,)                     # history: % of the card's memory
    PERCENT.add(_k)

ui.register("""
.stat-item label { font-feature-settings: "tnum"; }
.stat-caption { font-size: 9px; font-weight: 700; opacity: 0.75; margin-right: 3px; }
.cc-stat .cc-stat-value { font-feature-settings: "tnum"; font-weight: 600; }
.cc-stat .cc-stat-speeds { font-size: %(text_small)s; }
.cc-stat .cc-stat-hint { font-size: %(text_small)s; opacity: 0.6; }
.cc-stat .cc-stat-maker { font-size: 9px; font-weight: 700; opacity: 0.55; margin: 1px 0 0 2px; }
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


def card_maker(kind: str) -> str:
    """The card's maker for a per-card figure ("gpu_nvidia" -> "NVIDIA"), "" otherwise."""
    return (S.GPU_MAKERS.get(kind) or S.VRAM_MAKERS_BY_KIND.get(kind) or S.TEMP_MAKERS_BY_KIND.get(kind) or "")


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
    shown = graph
    maker = card_maker(kind)
    if maker:                            # two cards: whose graph it is, inside it (the title gets cut -- Vini)
        shown = Gtk.Overlay(child=graph, hexpand=True, vexpand=True)
        tag = Gtk.Label(label=maker, css_classes=["cc-stat-maker"], halign=Gtk.Align.START,
                        valign=Gtk.Align.START, can_target=False)
        shown.add_overlay(tag)
        box_tag = tag
    else:
        box_tag = None
    hint = Gtk.Label(label="", xalign=0, css_classes=["cc-stat-hint"], visible=False, wrap=True,
                     width_chars=1, natural_wrap_mode=Gtk.NaturalWrapMode.WORD)
    if kind == "net":                    # two speeds: a line of their own under the title
        value.set_xalign(0)
        value.set_ellipsize(Pango.EllipsizeMode.END)
        value.set_max_width_chars(-1)
        value.add_css_class("cc-stat-speeds")
        box = ui.panel.module(head, value, shown, hint, spacing=4)
    else:
        head.append(value)
        box = ui.panel.module(head, shown, hint, spacing=6)
    box.add_css_class("cc-stat")
    box.maker_tag = box_tag                   # (tests)

    def update(r):
        t = S.text(kind, r)
        value.set_label(S.vram_text(r, S.VRAM_KINDS[kind]) if kind in S.VRAM_KINDS else
                        t.split(" ", 1)[1] if kind in PERCENT else
                        (t.replace("  ", " ") if kind == "net" else ("–" if r.fps is None else str(r.fps))))
        graph.push(r.history)
        h = S.FPS_HINTS.get(r.fps_state, "") if kind == "fps" else ""
        hint.set_label(h)
        hint.set_visible(bool(h))
    box._live = _Live(box, update)
    return box
