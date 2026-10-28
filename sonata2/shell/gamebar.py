"""Game Bar (Super+G; Vini: Windows' Xbox Game Bar, drawn the Big Sur way).

Over the game, a full-screen overlay: the screen dimmed like Overview's
(Win+Tab: mission.DIM; no blur -- Vini: a full-screen blur over a game cost
too much), a bar at the top -- the app in front, a toggle per widget, the
clock -- and the widgets themselves as Control Center-style glass panels.
Super+G again, Esc or a click on the dimmed screen closes it; the game keeps
running underneath.

Controllers (Vini): the Xbox / PS button opens and closes it; while it's
open the controller drives it (gamepad/service.py hands the presses to
pad()) -- D-pad / left stick move the focus, A presses, B closes, LB / RB
jump between the bar and the widgets. The keyboard's arrows and Tab move
the same focus.

The pen on the bar (Vini) is the recording pill's: Draw on the screen
(livedraw.py) with the same palette -- pen, highlighter, shapes, text,
emoji, colours, fading marks. Pressed, the Game Bar closes and drawing
starts on the game's display; pressed again (drawing on), it stops.

Glass (Vini, the "fake" glass, at no cost per frame): opening, the Game
Bar first takes a small picture of the game's display (grim, a quarter of
its size), blurs it once, and shows it behind its bar and widgets (_Glass),
which turn see-through over it. The compositor never blurs behind the Game
Bar (glass.NEVER_BLUR: a full-screen blur over a running game cost too much)
-- behind the panels the moment you opened it, the game itself dimmed
around them. No picture (no grim, too slow): the panels stay solid.

Capture widget (P2): Screenshot and Record / Stop (with the time) -- the
menu bar's Capture (shell/capture.py), on the game's display once the Game
Bar has gone; the recording keeps its pill (stop, pen) at the top.

Audio widget (P3): the output and microphone volumes (Control Center's
sliders) and what a recording takes -- System, Microphone, Both (two
separate tracks, capture.tracks_command) or None (the capture options).
Performance widget (P4): FPS, CPU, GPU and memory, Control Center's graphs
(statsui), read only while on screen. Widgets move by their title (dragged;
a double-click puts one back) and the pin keeps one over the game after the
Game Bar closes (P5: a small click-through window each -- the Performance
one is the HUD). Instant replay (P6, replay.py): the last 30 seconds kept,
Save Clip (or Super+Alt+G) writes them.

Built in parts (ROADMAP M8): P1 this window, the bar and the widget frames;
P2 Capture, P3 microphone + system audio as two tracks,
P4 Performance (backend/stats.py, shell/statsui.py), P5 moving / pinning
widgets and the small HUD, then the last-30-seconds replay.

Lives in the menu bar's process (it already has the capture, the stats and
the window list): `sonata2 gamebar` -> its "gamebar" action. Nothing runs
while it's closed: the clock ticks only while it's on screen.

    bar = GameBar(app, topbar)      # topbar: for the app in front (optional)
    bar.toggle(); bar.open(); bar.close()
"""
import time

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from ..i18n import N_, _  # noqa: E402
from ..ui import tokens  # noqa: E402
from . import layer  # noqa: E402
from .mission import DIM  # noqa: E402  (the same dark layer as Overview, Win+Tab)

NAMESPACE = "sonata2-gamebar"
PIN_NAMESPACE = "sonata2-gamebar-pin"
# the fake glass: the display's picture at this scale (blurred: no detail
# needed), blurred this much (px at full size), awaited this long at most
FROST_SCALE = 0.25
FROST_BLUR = 28
FROST_WAIT_MS = 80          # the Game Bar (its dimming) shows after this at most
PANELS_WAIT_MS = 220        # its panels wait this much more for their glass (later: solid, glass fading in)
READ_AFTER_MS = 260
GLASS_FADE_MS = 250         # a late picture: the glass fades in         # the states read once the opening animation is over
FROST_LATE_S = 2.0          # a later picture than that: the game has moved on, no glass this time
CONFIG = "gamebar"
# the widgets, in the bar's order -- the same left-to-right order as on the
# screen, so the toggles, the arrows and LB / RB follow them: id -> (title,
# icon, (halign, valign)). Vini: three along the bottom -- Audio at the
# left, Capture in the middle, Performance at the right -- Now Playing at the
# top left, Control Center's Wi-Fi, Bluetooth, Airplane Mode, Do Not Disturb
# and brightness at the top right.
WIDGETS = {
    "nowplaying": (N_("Now Playing"), "sonata-now-playing-symbolic", (Gtk.Align.START, Gtk.Align.START)),
    "controls": (N_("Control Center"), "sonata-control-center-symbolic", (Gtk.Align.END, Gtk.Align.START)),
    "audio": (N_("Audio"), "audio-volume-high-symbolic", (Gtk.Align.START, Gtk.Align.END)),
    "capture": (N_("Capture"), "camera-photo-symbolic", (Gtk.Align.CENTER, Gtk.Align.END)),
    "performance": (N_("Performance"), "speedometer-symbolic", (Gtk.Align.END, Gtk.Align.END)),
}
DEFAULTS = {"widgets": list(WIDGETS), "positions": {}, "pinned": [], "known": list(WIDGETS)}
# the Performance widget's graphs under Control Center's FPS Limit (which has
# the game's frame rate and frame times): every one Control Center has (Vini:
# both GPUs, their memory, the temperatures, memory, network) -- not FPS
# again; past PERF_MAX_H they scroll inside the widget
PERF_SKIP = ("fps",)
PERF_MAX_H = 440
SLIDER_STEP = 5                                  # the D-pad's left / right on a slider
MIXER_MAX_H = 200                                # the apps' volumes: more scroll inside the widget
BAR_TOP = 14                 # the bar below the screen's top edge
WIDGET_TOP = 76              # widgets start under the bar
WIDGET_SIDE = 24
WIDGET_W = 340
# the content's width inside a widget (its padding and the body's): long text
# (an FPS hint, a sound choice) wraps or ellipsizes, never widens a widget
WIDGET_INNER = WIDGET_W - 2 * 6 - 2 * 4
PERF_CELL = ((WIDGET_INNER - 6) // 2, 92)       # two graphs a row, the Grid's 6 px between


def _close_ms() -> int:
    """How long the closing animation runs (t_open, at Sonata's speed)."""
    return tokens.ms(float(tokens.SHARED["t_open"].rstrip("ms")))


ui.register("""
window.sonata-gamebar, window.sonata-gamebar > contents { background: none; box-shadow: none; }
/* the game dimmed like Overview's (Win+Tab); never blurred (glass.NEVER_BLUR) */
.gb-scrim { background: rgba(0, 0, 0, %(dim)s); transition: opacity %(t_open)s %(ease_out)s; }
.gb-root.closing .gb-scrim { opacity: 0; }
@keyframes gb-fade-in { from { opacity: 0; } to { opacity: 1; } }
.gb-scrim { animation: gb-fade-in %(t_open)s %(ease_out)s both; }

/* the bar: a Big Sur glass pill, sliding down from the top edge */
.gb-bar { background-color: %(menu_bg)s; border-radius: %(r_plate)s; padding: 6px 8px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
  color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
@keyframes gb-bar-in { from { opacity: 0; transform: translateY(-14px) scale(0.98); } to { opacity: 1; transform: none; } }
@keyframes gb-bar-out { to { opacity: 0; transform: translateY(-14px) scale(0.98); } }
.gb-bar { animation: gb-bar-in %(t_standard)s %(ease_out)s both; }
.gb-root.closing .gb-bar { animation: gb-bar-out %(t_open)s %(ease_out)s both; }
.gb-app { padding: 0 8px 0 4px; }
.gb-app-name { font-weight: 700; }
.gb-sep { min-width: 1px; margin: 4px 6px; background: %(separator)s; }
.gb-clock { padding: 0 8px; font-feature-settings: "tnum"; color: %(label_secondary)s; }

/* a widget's toggle on the bar (Big Sur toolbar toggles): plain when off;
   a soft fill and the accent ink when its widget shows -- three solid blue
   squares side by side were too loud over a game */
.gb-toggle { min-width: 34px; min-height: 30px; padding: 0; border-radius: 9px; border: none; box-shadow: none;
  background: none; color: %(label)s;
  transition: background-color %(t_fast)s %(ease_out)s, color %(t_fast)s %(ease_out)s; }
.gb-toggle:hover { background: alpha(%(label)s, 0.10); }
.gb-toggle:active { background: alpha(%(label)s, 0.18); transition-duration: %(t_press)s; }
.gb-toggle.on { background: alpha(%(label)s, 0.12); color: %(accent_ink)s; }
.gb-toggle.on:hover { background: alpha(%(label)s, 0.17); }
/* where the controller / keyboard is (Big Sur's focus ring, eased in) */
.gb-root button { outline: 0 solid transparent; outline-offset: 1px;
  transition: background-color %(t_fast)s %(ease_out)s, color %(t_fast)s %(ease_out)s,
              outline-color %(t_fast)s %(ease_out)s, outline-width %(t_fast)s %(ease_out)s; }
.gb-root button:focus-visible, .gb-root .sonata-module-slider:focus-visible {
  outline: 3px solid alpha(%(accent)s, 0.55); }
.gb-root .sonata-module-slider { outline: 0 solid transparent; outline-offset: 2px; border-radius: 99px;
  transition: outline-color %(t_fast)s %(ease_out)s, outline-width %(t_fast)s %(ease_out)s; }

/* the fake glass under them (_Frost): see-through over the blurred picture */
/* the panels waiting for their glass: not shown yet (they pop in with it) */
.gb-root.waiting .gb-bar, .gb-root.waiting .gb-widget { opacity: 0; animation: none; }
/* over the glass a panel paints its own tint (_Glass): no CSS background,
   its solid one fading out as the glass fades in */
.gb-root.frosted .gb-bar, .gb-root.frosted .gb-widget { background-color: transparent; }
.gb-bar, .gb-widget { transition: background-color %(t_standard)s %(ease_out)s; }

/* a widget: Control Center glass, popping in a little after the bar */
.gb-widget { background-color: %(menu_bg)s; border-radius: %(r_dialog)s; padding: 6px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
  color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
@keyframes gb-widget-in { from { opacity: 0; transform: scale(0.94); } to { opacity: 1; transform: none; } }
@keyframes gb-widget-out { to { opacity: 0; transform: scale(0.94); } }
.gb-widget { animation: gb-widget-in %(t_standard)s %(ease_out)s both; }
.gb-widget.hiding, .gb-root.closing .gb-widget { animation: gb-widget-out %(t_open)s %(ease_out)s both; }
.gb-widget-body { padding: 4px 4px 2px; }
.gb-widget-empty { color: %(label_secondary)s; font-size: %(text_small)s; margin: 14px 8px 16px; }

/* Capture: two round actions, Control Center style */
.gb-actions { margin: 6px 4px 2px; }
.gb-action { background: none; border: none; box-shadow: none; padding: 4px; border-radius: %(r_dialog)s;
  color: %(label)s; transition: background-color %(t_fast)s %(ease_out)s; }
.gb-action:hover { background: alpha(%(label)s, 0.06); }
.gb-action .gb-action-icon { min-width: 44px; min-height: 44px; border-radius: 99px; background: %(toggle_off)s;
  transition: background-color %(t_fast)s %(ease_out)s, transform %(t_press)s %(ease_out)s; }
.gb-action:active .gb-action-icon { transform: scale(0.92); }
.gb-action .gb-action-label { font-size: %(text_small)s; margin-top: 4px; }
.gb-action.recording .gb-action-icon { background: %(sys_red)s; color: %(label_on_accent)s; }
@keyframes gb-rec-pulse { from { opacity: 1; } to { opacity: 0.55; } }
.gb-action.recording .gb-action-icon image { animation: gb-rec-pulse 900ms ease-in-out infinite alternate; }
.gb-rec-dot { min-width: 14px; min-height: 14px; border-radius: 99px; background: %(sys_red)s; }
.gb-action.recording .gb-rec-dot { background: %(label_on_accent)s; border-radius: 3px; }
.gb-action:disabled { opacity: 0.45; }

/* the title: drag to move; the pin keeps the widget over the game */
.gb-widget .panel-header { padding-right: 4px; }
.gb-widget.dragging { opacity: 0.85; }
.gb-pin { min-width: 24px; min-height: 24px; padding: 0; border: none; border-radius: 99px; background: none;
  box-shadow: none; color: %(label_secondary)s;
  transition: background-color %(t_fast)s %(ease_out)s, color %(t_fast)s %(ease_out)s; }
.gb-pin:hover { background: alpha(%(label)s, 0.10); }
.gb-pin.on { color: %(accent_ink)s; }
/* pinned over the game: no title buttons to press (it lets clicks through) */
window.sonata-gamebar-pin, window.sonata-gamebar-pin > contents { background: none; box-shadow: none; }
window.sonata-gamebar-pin .gb-widget { animation: none; opacity: 0.92; }
window.sonata-gamebar-pin .gb-pin, window.sonata-gamebar-pin .gb-chips { opacity: 0; }

/* chips: Instant Replay, Save Clip; the sound a recording takes */
.gb-chips { margin: 4px 4px 2px; }
.gb-chip { min-height: 26px; padding: 0 10px; border-radius: 99px; border: none; box-shadow: none;
  background: alpha(%(label)s, 0.08); color: %(label)s; font-size: %(text_small)s;
  transition: background-color %(t_fast)s %(ease_out)s, color %(t_fast)s %(ease_out)s; }
.gb-chip:hover { background: alpha(%(label)s, 0.14); }
.gb-chip:active { background: alpha(%(label)s, 0.20); transition-duration: %(t_press)s; }
.gb-chip.on { background: %(accent)s; color: %(label_on_accent)s; }
.gb-chip:disabled { opacity: 0.45; }
.gb-section { font-size: %(text_small)s; font-weight: 600; color: %(label_secondary)s; margin: 4px 4px 0; }
.gb-widget .seg button { font-size: %(text_small)s; padding-left: 2px; padding-right: 2px; }
.gb-perf .panel-module { padding: 8px; }
""", key="gamebar", dim=DIM)


def _now() -> str:
    return GLib.DateTime.new_now_local().format("%H:%M")


# what the controller stops on: buttons and sliders (Control Center's capsule ones too)
_STOPS = (Gtk.Button, Gtk.Range, ui.controls.ModuleSlider)


def _slider_of(w):
    if w is None:
        return None
    for t in (Gtk.Range, ui.controls.ModuleSlider):
        if isinstance(w, t):
            return w
        a = w.get_ancestor(t)
        if a is not None:
            return a
    return None


def _focusables(root) -> list:
    """What the controller can stop on inside `root`, in reading order."""
    found, stack = [], [root]
    while stack:
        w = stack.pop(0)
        if w is not root and isinstance(w, _STOPS) and w.get_visible() and w.get_sensitive() \
                and not w.has_css_class("gb-pin"):
            found.append(w)
            continue
        c = w.get_first_child()
        kids = []
        while c is not None:
            kids.append(c)
            c = c.get_next_sibling()
        stack[0:0] = kids
    return found


GLASS_TINT = 0.55          # the panels' tint over the glass (menu_bg at this alpha)


def blur_texture(renderer, texture):
    """The game's picture blurred once (GSK), at its small size."""
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


class _Glass:
    """A panel (the bar, a widget) drawing the Game Bar's glass under itself:
    its slice of the blurred picture, then its tint, inside its rounded
    shape. Each panel draws its own (Vini: the Game Bar was slow -- one
    glass layer over the whole screen was drawn again whole whenever a panel
    changed, and was looked at on a timer); a panel moved draws again
    (place / queue_draw), its size changed redraws it anyway."""
    radius_token = "r_dialog"

    def _glass(self, snap) -> None:
        win = self.get_root()
        tex = getattr(win, "frost_tex", None)
        alpha = getattr(win, "frost_alpha", 0.0)
        root = getattr(win, "root", None)
        if tex is None or alpha <= 0 or root is None or self.get_parent() is None:
            return
        # (a snapshot's origin is the content box, inside the padding: the
        # whole panel is its border box, here and in the Game Bar)
        ok, b = self.compute_bounds(root)
        ok_self, own = self.compute_bounds(self)
        if not (ok and ok_self):
            return
        from gi.repository import Graphene, Gsk
        rr = Gsk.RoundedRect()
        rr.init_from_rect(own, ui.theme.px(self.radius_token))
        snap.push_rounded_clip(rr)
        snap.push_opacity(alpha)
        snap.append_texture(tex, Graphene.Rect().init(own.get_x() - b.get_x(), own.get_y() - b.get_y(),
                                                      root.get_width(), root.get_height()))
        tint = ui.rgba("menu_bg")
        tint.alpha = GLASS_TINT
        snap.append_color(tint, own)
        snap.pop()
        snap.pop()


class _GlassBox(_Glass, Gtk.Box):
    def do_snapshot(self, snap) -> None:
        self._glass(snap)
        Gtk.Box.do_snapshot(self, snap)


class Widget(_GlassBox):
    """One Game Bar widget: a glass panel with a header (icon + title) and
    `body`, the widget's own content (filled by its part)."""

    def __init__(self, wid: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["gb-widget"])
        title, icon, (halign, valign) = WIDGETS[wid]
        self.wid = wid
        ui.theme.glass_class(self)
        self.set_size_request(WIDGET_W, -1)
        self.set_halign(halign)
        self.set_valign(valign)
        self.set_margin_start(WIDGET_SIDE)
        self.set_margin_end(WIDGET_SIDE)
        self.set_margin_top(WIDGET_TOP)
        self.set_margin_bottom(WIDGET_SIDE)
        self.align = (halign, valign)
        self.pin_btn = Gtk.Button(icon_name="view-pin-symbolic", css_classes=["gb-pin"], focus_on_click=False,
                                  tooltip_text=_("Pin Over the Game"), valign=Gtk.Align.CENTER)
        head = ui.panel.header(_(title), self.pin_btn)
        head.prepend(Gtk.Image(icon_name=icon, pixel_size=16, margin_end=6))
        head.set_cursor(Gdk.Cursor.new_from_name("grab"))
        self.head = head
        self.append(head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["gb-widget-body"])
        self.empty = Gtk.Label(label=_("Coming soon"), css_classes=["gb-widget-empty"])
        self.body.append(self.empty)
        self.append(self.body)

    def place(self, pos) -> None:
        """At (x, y) in the Game Bar (moved there), or None: its own corner."""
        if pos is None:
            self.set_halign(self.align[0])
            self.set_valign(self.align[1])
            for side, v in (("start", WIDGET_SIDE), ("end", WIDGET_SIDE), ("top", WIDGET_TOP), ("bottom", WIDGET_SIDE)):
                getattr(self, f"set_margin_{side}")(v)
            return
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_margin_start(int(pos[0]))
        self.set_margin_top(int(pos[1]))
        self.set_margin_end(0)
        self.set_margin_bottom(0)
        self.queue_draw()                               # its slice of the glass, where it is now

    def bare(self) -> None:
        """No margins (in its own pinned window)."""
        for side in ("start", "end", "top", "bottom"):
            getattr(self, f"set_margin_{side}")(0)
        self.set_halign(Gtk.Align.FILL)
        self.set_valign(Gtk.Align.FILL)

    def fill(self, child: Gtk.Widget) -> None:
        """The widget's own content in place of "Coming soon"."""
        if self.empty.get_parent() is self.body:
            self.body.remove(self.empty)
        from ..ui.fixed import FixedWidth
        self.body.append(FixedWidth(child, WIDGET_INNER))


class _NoBar:
    """No menu bar (a preview, tests): the controls still work."""
    notifications = None

    def _poll(self) -> None:
        pass


def perf_kinds(available) -> list:
    """The Performance widget's graphs: all of Control Center's (statsui.KINDS,
    in its order: CPU, each GPU, their memory, temperatures, memory, network),
    FPS apart (the FPS Limit above shows it)."""
    return [k for k in available if k not in PERF_SKIP]


def frost_command(output) -> list:
    """grim: the display's picture, small and uncompressed (fast)."""
    cmd = ["grim", "-s", str(FROST_SCALE), "-l", "0"]
    if output:
        cmd += ["-o", output]
    return cmd + ["-"]


class PinWindow(Gtk.Window):
    """A pinned widget over the game once the Game Bar has gone: a small
    layer surface at the widget's place, clicks passing through it."""

    def __init__(self, app, widget: Widget, x: int, y: int, monitor=None):
        super().__init__(application=app, title=_("Game Bar"), decorated=False, resizable=False,
                         css_classes=["sonata-gamebar-pin"])
        widget.bare()
        self.set_child(widget)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, PIN_NAMESPACE)
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_anchor(self, LS.Edge.LEFT, True)
            LS.set_margin(self, LS.Edge.TOP, int(y))
            LS.set_margin(self, LS.Edge.LEFT, int(x))
            LS.set_exclusive_zone(self, -1)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
            if monitor is not None:
                LS.set_monitor(self, monitor)
        self.connect("map", lambda *_a: layer.set_input_region(self, []))   # the game keeps the clicks

    def release(self) -> Widget:
        w = self.get_child()
        self.set_child(None)
        self.destroy()
        return w


class GameBar(Gtk.Window):
    def __init__(self, app, topbar=None):
        super().__init__(application=app, title=_("Game Bar"), decorated=False,
                         css_classes=["sonata-gamebar"])
        self.topbar = topbar
        self.cfg = config.load(CONFIG, DEFAULTS)
        self._new_widgets_shown()
        self._clock_src = 0
        self._hide_src = 0
        self._read_src = 0

        self.root = Gtk.Overlay(css_classes=["gb-root"])
        self.scrim = Gtk.Box(css_classes=["gb-scrim"], hexpand=True, vexpand=True)
        self.root.set_child(self.scrim)
        self.frost_tex = None                           # the game's picture, blurred (_Glass)
        self.frost_alpha = 0.0
        self._frost_anim = None
        self.root.add_overlay(self._bar())
        self.widgets = {}
        for wid in WIDGETS:
            w = Widget(wid)
            w.set_visible(wid in self.cfg["widgets"])
            self.widgets[wid] = w
            self.root.add_overlay(w)
        self.widgets["capture"].fill(self._capture_actions())
        self.widgets["audio"].fill(self._audio_controls())
        self.widgets["performance"].fill(self._performance())
        self.widgets["nowplaying"].fill(self._now_playing())
        self.widgets["controls"].fill(self._controls())
        self.pins = {}                                  # wid -> PinWindow while the Game Bar is closed
        for wid, w in self.widgets.items():
            w.place(self._pos(wid))
            w.pin_btn.connect("clicked", lambda _b, k=wid: self.toggle_pin(k))
            self._movable(w)
        self._drag_widgets()
        self._sync_pins()
        self._sync_toggles()
        self.set_child(self.root)

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._pressed)
        self.add_controller(click)
        if not layer.overlay_fullscreen(self, NAMESPACE):
            self.set_default_size(1280, 720)           # no layer shell (preview): a plain window

    # -- the bar ----------------------------------------------------------------------------------
    def _bar(self) -> Gtk.Widget:
        bar = _GlassBox(css_classes=["gb-bar"], halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                        margin_top=BAR_TOP, spacing=2)
        bar.radius_token = "r_plate"
        ui.theme.glass_class(bar)
        app = Gtk.Box(css_classes=["gb-app"], spacing=8, valign=Gtk.Align.CENTER)
        self.app_icon = Gtk.Image(pixel_size=22)
        self.app_name = Gtk.Label(css_classes=["gb-app-name"], ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=24)
        app.append(self.app_icon)
        app.append(self.app_name)
        bar.append(app)
        bar.append(Gtk.Box(css_classes=["gb-sep"]))
        self.toggles = {}
        for wid, (title, icon, _where) in WIDGETS.items():
            b = Gtk.Button(icon_name=icon, css_classes=["gb-toggle"], focus_on_click=False,
                           tooltip_text=_(title), valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, w=wid: self.toggle_widget(w))
            self.toggles[wid] = b
            bar.append(b)
        bar.append(Gtk.Box(css_classes=["gb-sep"]))
        # the recording pill's pen, in the bar's look (livedraw.pen_button)
        from . import livedraw
        from .capture import focused_output
        self.pen = livedraw.pen_button(self.get_application(), focused_output, css="gb-toggle", size=16,
                                       start=self._draw, focus_on_click=False, can_focus=True)
        bar.append(self.pen)
        bar.append(Gtk.Box(css_classes=["gb-sep"]))
        self.clock = Gtk.Label(css_classes=["gb-clock"], valign=Gtk.Align.CENTER)
        bar.append(self.clock)
        self.bar = bar
        return bar

    def _front_app(self) -> None:
        """The app in front (the game) beside the toggles."""
        from .capture import _app_gicon, _app_name
        key, wins = None, []
        active = getattr(self.topbar, "_active", None)
        if active is not None:
            try:
                key, wins = active()
            except Exception:                           # a name on a bar never fails it
                key, wins = None, []
        if key and _app_gicon(key) is None:
            # no desktop entry -- a Steam game ("steam_app_3219630", Vini) or a
            # Windows program: named and drawn as the Dock and the menu bar do
            from .. import windowapps
            try:
                name, gicon, _dynamic = windowapps.describe(key, windowapps.window_title(wins))
            except Exception:
                name, gicon = key, None
            (self.app_icon.set_from_gicon if gicon is not None else
             lambda _g: self.app_icon.set_from_icon_name("input-gaming-symbolic"))(gicon)
            self.app_name.set_label(name or key)
        elif key:
            self.app_icon.set_from_gicon(_app_gicon(key))
            self.app_name.set_label(_app_name(key))
        else:
            self.app_icon.set_from_icon_name("input-gaming-symbolic")
            self.app_name.set_label(_("Game Bar"))

    def _draw(self, go) -> None:
        """Drawing starts once the Game Bar has gone (the marks go on the game)."""
        self.close_bar()
        GLib.timeout_add(_close_ms() + 20, lambda: (go(), False)[1])

    def _sync_toggles(self) -> None:
        for wid, b in self.toggles.items():
            (b.add_css_class if self.widgets[wid].get_visible() else b.remove_css_class)("on")

    # -- widgets ----------------------------------------------------------------------------------
    def _new_widgets_shown(self) -> None:
        """A widget added by an update (Now Playing) shows the first time
        (Vini added it, then it was off): the saved list predates it."""
        raw = config.load(CONFIG, {"widgets": None, "known": None})     # what was saved, no defaults
        known = raw["known"]
        if known is None:                               # saved before "known" existed: the first three
            known = ["audio", "capture", "performance"] if raw["widgets"] is not None else list(WIDGETS)
        new = [w for w in WIDGETS if w not in known]
        if new:
            self.cfg["widgets"] = list(self.cfg.get("widgets") or []) + [w for w in new
                                                                     if w not in (self.cfg.get("widgets") or [])]
            self.cfg["known"] = list(WIDGETS)
            config.update(CONFIG, widgets=self.cfg["widgets"], known=self.cfg["known"])

    # -- moving and pinning (P5) -----------------------------------------------------------------
    def _pos(self, wid: str):
        p = (self.cfg.get("positions") or {}).get(wid)
        return tuple(p) if isinstance(p, (list, tuple)) and len(p) == 2 else None

    def _movable(self, w: Widget) -> None:
        """A double-click on its title puts it back (the dragging itself is
        the Game Bar's: _drag_widgets)."""
        click = Gtk.GestureClick()
        click.connect("pressed", lambda _g, n, _x, _y: n == 2 and self.reset_position(w.wid))
        w.head.add_controller(click)

    def _drag_widgets(self) -> None:
        """Widgets dragged by their title. The gesture sits on the Game Bar,
        which doesn't move: on the title itself its offsets were measured
        from a title that moved with them, and the widget shook under the
        pointer (Vini). Buttons on the title keep their clicks."""
        drag = Gtk.GestureDrag()
        start = {}

        def begin(g, x, y):
            target = self.root.pick(x, y, Gtk.PickFlags.DEFAULT)
            w = next((wd for wd in self.widgets.values()
                      if target is not None and (target is wd.head or target.is_ancestor(wd.head))), None)
            if w is None or isinstance(target, Gtk.Button) or target.get_ancestor(Gtk.Button) is not None:
                g.set_state(Gtk.EventSequenceState.DENIED)
                return
            ok, b = w.compute_bounds(self.root)
            start.update(w=w, xy=(b.get_x(), b.get_y()) if ok else (0, 0),
                         wh=(b.get_width(), b.get_height()) if ok else (WIDGET_W, 100))
            g.set_state(Gtk.EventSequenceState.CLAIMED)
            w.add_css_class("dragging")

        def update(_g, dx, dy):
            if "w" not in start:
                return
            W, H = self.root.get_width(), self.root.get_height()
            x = max(0, min(W - start["wh"][0], start["xy"][0] + dx))
            y = max(0, min(H - start["wh"][1], start["xy"][1] + dy))
            start["w"].place((x, y))
            start["now"] = (int(x), int(y))

        def end(_g, _dx, _dy):
            w = start.get("w")
            if w is not None:
                w.remove_css_class("dragging")
                if "now" in start:
                    self._save_pos(w.wid, start["now"])
            start.clear()
        drag.connect("drag-begin", begin)
        drag.connect("drag-update", update)
        drag.connect("drag-end", end)
        self.root.add_controller(drag)
        self.drag = drag                                # (tests)
        self._drag_state = start

    def _save_pos(self, wid: str, xy) -> None:
        pos = dict(self.cfg.get("positions") or {})
        if xy is None:
            pos.pop(wid, None)
        else:
            pos[wid] = [int(xy[0]), int(xy[1])]
        self.cfg["positions"] = pos
        config.update(CONFIG, positions=pos)

    def reset_position(self, wid: str) -> None:
        """A double-click on its title: back to its own corner."""
        self._save_pos(wid, None)
        self.widgets[wid].place(None)
        self.widgets[wid].queue_draw()

    def toggle_pin(self, wid: str) -> None:
        pinned = [w for w in (self.cfg.get("pinned") or []) if w in WIDGETS]
        pinned = [w for w in pinned if w != wid] if wid in pinned else pinned + [wid]
        self.cfg["pinned"] = pinned
        config.update(CONFIG, pinned=pinned)
        self._sync_pins()

    def _sync_pins(self) -> None:
        pinned = self.cfg.get("pinned") or []
        for wid, w in self.widgets.items():
            on = wid in pinned
            (w.pin_btn.add_css_class if on else w.pin_btn.remove_css_class)("on")
            w.pin_btn.set_tooltip_text(_("Unpin") if on else _("Pin Over the Game"))

    def _show_pins(self) -> None:
        """The Game Bar has gone: its pinned widgets stay over the game."""
        pinned = [w for w in (self.cfg.get("pinned") or []) if w in self.widgets]
        mon = None
        try:
            mon = self.get_display().get_monitor_at_surface(self.get_surface())
        except Exception:
            pass
        for wid in pinned:
            w = self.widgets[wid]
            if not w.get_visible() or wid in self.pins:
                continue
            ok, b = w.compute_bounds(self.root)
            x, y = (b.get_x(), b.get_y()) if ok else (0, 0)
            self.root.remove_overlay(w)
            win = PinWindow(self.get_application(), w, x, y, mon)
            self.pins[wid] = win
            win.present()
        if self.pins and not self._clock_src:          # a pinned Capture shows its recording's time
            self._clock_src = GLib.timeout_add_seconds(1, self._tick)

    def _hide_pins(self) -> None:
        for wid, win in list(self.pins.items()):
            w = win.release()
            self.root.add_overlay(w)
            w.place(self._pos(wid))
        self.pins.clear()

    def toggle_widget(self, wid: str) -> None:
        w = self.widgets[wid]
        if w.get_visible() and not w.has_css_class("hiding"):
            w.add_css_class("hiding")                   # out, then gone
            self.toggles[wid].remove_css_class("on")

            def gone():
                if w.has_css_class("hiding"):
                    w.remove_css_class("hiding")
                    w.set_visible(False)
                return False
            GLib.timeout_add(_close_ms(), gone)
            shown = [x for x in self.cfg["widgets"] if x != wid]
        else:
            w.remove_css_class("hiding")
            w.set_visible(True)                         # its own pop-in animation
            self.toggles[wid].add_css_class("on")
            shown = [x for x in WIDGETS if x == wid or (x in self.cfg["widgets"])]
        self.cfg["widgets"] = shown
        config.update(CONFIG, widgets=shown)            # the same widgets next time

    # -- open / close -----------------------------------------------------------------------------
    def is_open(self) -> bool:
        return self.get_visible() and not self.root.has_css_class("closing")

    def toggle(self) -> None:
        self.close_bar() if self.is_open() else self.open()

    def open(self) -> None:
        if self._hide_src:
            GLib.source_remove(self._hide_src)
            self._hide_src = 0
        self.root.remove_css_class("closing")
        self._hide_pins()                               # back in the Game Bar
        # the levels and radios read once it's on screen: their reads (pactl,
        # nmcli, rfkill, DDC/CI) started with it and slowed its opening (Vini)
        if self._read_src:
            GLib.source_remove(self._read_src)
        self._read_src = GLib.timeout_add(READ_AFTER_MS, self._read_states)
        self._front_app()
        self._tick()
        if not self._clock_src:
            self._clock_src = GLib.timeout_add_seconds(1, self._tick)
        self._frost_then(self._show)

    def _show(self) -> None:
        # shown again: the CSS animations run anew only for newly mapped widgets
        self.set_visible(False)
        self.present()
        first = next(iter(self.toggles.values()))
        first.grab_focus()                              # where the controller / arrows start
        self._grab(True)

    # -- the fake glass ---------------------------------------------------------------------------
    def _frost_then(self, show) -> None:
        """The game's picture first (before the Game Bar is on it), then show();
        at most FROST_WAIT_MS. A picture that comes later still turns the glass
        on, fading in (Vini: some openings had no glass -- grim took longer
        than that now and then, and its picture was thrown away). grim asks
        for the screen as it starts: the late part is only its picture being
        made, the Game Bar isn't in it."""
        self._frost_gen = getattr(self, "_frost_gen", 0) + 1
        gen = self._frost_gen
        self._set_glass(None, 0.0)
        self.root.remove_css_class("frosted")
        self.root.remove_css_class("waiting")
        import shutil
        if not shutil.which("grim") or layer.layer_shell() is None:
            show()
            return
        state = {"shown": False}

        def once():
            if not state["shown"]:
                state["shown"] = True
                if self.frost_tex is None and gen == self._frost_gen:
                    # the panels wait for their glass (Vini: they opened solid,
                    # then turned to glass): the game dims at once, the bar and
                    # widgets pop in with the picture, or solid at the latest
                    self.root.add_css_class("waiting")
                    GLib.timeout_add(PANELS_WAIT_MS, lambda: (gen == self._frost_gen and self._panels_in(),
                                                              False)[1])
                show()
            return False
        GLib.timeout_add(FROST_WAIT_MS, once)
        from .capture import focused_output
        try:
            proc = Gio.Subprocess.new(frost_command(focused_output()),
                                      Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            once()
            return

        started = time.monotonic()

        def done(p, res):
            try:
                _ok, out, _err = p.communicate_finish(res)
                tex = Gdk.Texture.new_from_bytes(out) if out is not None and out.get_size() else None
            except GLib.Error:
                tex = None
            # this opening's picture, the Game Bar still open (or about to be)
            if tex is not None and gen == self._frost_gen and (not state["shown"] or self.is_open()) \
                    and time.monotonic() - started < FROST_LATE_S:
                self._set_frost(tex)
            once()
        proc.communicate_async(None, None, done)

    def _set_frost(self, tex) -> None:
        """The picture blurred, the glass on: at once before the Game Bar
        shows, fading in after (a late picture)."""
        if not self.get_realized():                     # blurred once it has a renderer
            hid = {}

            def realized(*_a):
                self.disconnect(hid["id"])
                self._set_frost(tex)
            hid["id"] = self.connect("realize", realized)
            return
        tex = blur_texture(self.get_renderer(), tex)
        self.root.add_css_class("frosted")
        if not self.get_mapped() or self.root.has_css_class("waiting"):
            self._set_glass(tex, 1.0)                   # in glass from their first frame
            self._panels_in()
            return
        self._set_glass(tex, 0.0)
        from gi.repository import Adw
        target = Adw.CallbackAnimationTarget.new(lambda v: self._set_glass(tex, v))
        self._frost_anim = Adw.TimedAnimation.new(self, 0.0, 1.0, tokens.ms(GLASS_FADE_MS), target)
        self._frost_anim.play()

    def _panels_in(self) -> None:
        """The bar and widgets pop in (they waited for the glass)."""
        self.root.remove_css_class("waiting")

    def _set_glass(self, tex, alpha: float) -> None:
        self.frost_tex, self.frost_alpha = tex, alpha
        for w in [self.bar] + list(self.widgets.values()):
            w.queue_draw()                              # each panel's own area only

    def close_bar(self) -> None:
        if not self.get_visible() or self.root.has_css_class("closing"):
            return
        self.root.add_css_class("closing")
        self._grab(False)                               # the game gets its controller back at once
        if self._clock_src:
            GLib.source_remove(self._clock_src)
            self._clock_src = 0

        def gone():
            self._hide_src = 0
            self._show_pins()                           # (measured where they are, before hiding)
            self._mixer_listen("audio" in self.pins)    # still on screen pinned: still listening
            self.set_visible(False)
            self.root.remove_css_class("closing")
            return False
        self._hide_src = GLib.timeout_add(_close_ms(), gone)

    def _tick(self) -> bool:
        if not self.is_open() and not self.pins:
            self._clock_src = 0
            return False                                # nothing on screen: nothing to tick
        self.clock.set_label(_now())
        self._sync_capture()
        return True

    # -- Audio (P3) ---------------------------------------------------------------------------
    def _audio_controls(self) -> Gtk.Widget:
        from ..backend import system
        from .topbar import _slider_with_icon, _speaker_icon, _volume_feedback
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        out, self.vol = _slider_with_icon(_speaker_icon, 50, lambda v: system.run_async(
            system.set_volume, None, int(v), False))
        _volume_feedback(self.vol)
        mic, self.mic = _slider_with_icon(
            lambda v: "microphone-disabled-symbolic" if v <= 0 else "audio-input-microphone-symbolic", 50,
            lambda v: system.run_async(system.set_input_volume, None, int(v), False))
        for s_ in (self.vol, self.mic):
            s_.set_focusable(True)                      # the controller: left / right change it
            s_.set_can_focus(True)
        box.append(out)
        box.append(mic)
        # every app's own volume (Vini): the Sound menu's mixer, the same rows
        from ..backend import mixer
        if mixer.available():
            from .mixer_ui import AppMixer
            box.append(Gtk.Label(label=_("Applications"), xalign=0, css_classes=["gb-section"]))
            self.app_mixer = AppMixer()
            self.app_mixer.add_css_class("cc-mixer")    # Control Center's edges: lined up with the sliders above
            scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                        max_content_height=MIXER_MAX_H, child=self.app_mixer)
            box.append(scroll)
        box.append(Gtk.Label(label=_("Recordings Take"), xalign=0, css_classes=["gb-section"]))
        from .capture import DEFAULTS as CAP_DEFAULTS
        cur = config.load("capture", CAP_DEFAULTS).get("audio", "system")
        self.sound_choice = ui.controls.segmented(
            [("system", _("System")), ("mic", _("Microphone")), ("both", _("Both")), ("none", _("None"))], cur,
            self._set_sound, tooltips={"both": _("System sound and microphone, two tracks")})
        for b in self.sound_choice.buttons.values():
            b.set_can_focus(True)                       # the controller reaches them
        box.append(self.sound_choice)
        return box

    def _set_sound(self, kind: str) -> None:
        config.update("capture", audio=kind)
        self.sound_choice.select(kind)

    def _streams(self, streams) -> None:
        """The apps playing sound (the mixer's rows), their sliders and mute
        buttons reachable with the controller."""
        mix = getattr(self, "app_mixer", None)
        if mix is None:
            return
        mix.set_streams(streams)
        for row in mix.rows.values():
            row.slider.set_focusable(True)
            row.slider.set_can_focus(True)
            row.mute.set_can_focus(True)

    def _mixer_listen(self, on: bool) -> None:
        """Apps starting / stopping sound heard only while the Audio widget is
        on screen (the Game Bar open, or it pinned)."""
        service = getattr(self.topbar, "mixer", None)
        if service is None or getattr(self, "app_mixer", None) is None:
            return
        listening = self._streams in service.listeners
        if on and not listening:
            service.listeners.append(self._streams)
        elif not on and listening:
            service.listeners.remove(self._streams)

    def _refresh_audio(self) -> None:
        """The levels as they are now (each opening; changed elsewhere meanwhile)."""
        if not hasattr(self, "vol"):
            return
        if getattr(self, "app_mixer", None) is not None:
            from ..backend import mixer, system as system_
            system_.run_async(mixer.streams, self._streams)
            self._mixer_listen(True)
        from ..backend import system
        from .capture import DEFAULTS as CAP_DEFAULTS
        self.sound_choice.select(config.load("capture", CAP_DEFAULTS).get("audio", "system"))

        def fill(res):
            vol, mic = res if res else (None, None)
            for slider, level in ((self.vol, vol), (self.mic, mic)):
                slider.set_sensitive(level is not None)
                if level is not None:
                    ui.controls.show_value(slider, level[0])
        system.run_async(lambda: (system.volume(), system.input_volume()), fill)

    # -- Control Center's controls ------------------------------------------------------------
    def _controls(self) -> Gtk.Widget:
        """Wi-Fi, Bluetooth, Airplane Mode, Do Not Disturb and the brightness of
        the game's display (Vini) -- Control Center's own (topbar.QuickControls),
        read again at each opening."""
        from .topbar import QuickControls, open_settings
        bar = self.topbar if hasattr(self.topbar, "_poll") else _NoBar()
        q = self.quick = QuickControls()
        disp = q._make_quick(bar, None, lambda: self._after_close(lambda: open_settings("displays")))
        for t in (q.wifi, q.bt, q.plane, q.dnd):
            t.button.set_can_focus(True)                # the controller reaches them
        q.bright.set_focusable(True)
        q.bright.set_can_focus(True)
        conn = ui.panel.module(q.wifi, q.bt, q.plane, spacing=2)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(conn)
        box.append(ui.panel.click_anywhere(ui.panel.module(q.dnd), q.dnd))
        box.append(ui.panel.module(Gtk.Label(label=_("Display"), xalign=0, css_classes=["panel-module-title"]), disp))
        return box

    def _read_states(self) -> bool:
        self._read_src = 0
        if self.is_open():
            self._refresh_audio()
            self._refresh_controls()
        return False

    def _refresh_controls(self) -> None:
        q = getattr(self, "quick", None)
        if q is None:
            return
        from .capture import focused_output
        q.output = focused_output()                     # the game's display: its brightness
        q.read_quick()

    # -- Now Playing --------------------------------------------------------------------------
    def _now_playing(self) -> Gtk.Widget:
        """The menu bar's / Control Center's Now Playing (Vini): artwork, title,
        artist, previous / play-pause / next -- the same module, following
        the player only while on screen."""
        from . import mpris
        from .topbar import now_playing_module
        players = getattr(self.topbar, "players", None) or mpris.players()
        return now_playing_module(players)

    # -- Performance (P4) ---------------------------------------------------------------------
    def _performance(self) -> Gtk.Widget:
        from . import statsui
        from .fpsmodule import FpsModule
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["gb-perf"])
        # Control Center's FPS Limit, the same module (Vini): Off / 30 / 60 / 90 /
        # 120 / Max, and the game's frame times -- read only while on screen
        self.fps_limit = FpsModule()
        for b in list(self.fps_limit.buttons.values()) + [self.fps_limit.install_btn]:
            b.set_can_focus(True)                       # the controller reaches them
        box.append(self.fps_limit)
        grid = Gtk.Grid(column_spacing=6, row_spacing=6, column_homogeneous=True)
        self.perf_kinds = perf_kinds(statsui.KINDS)
        for i, k in enumerate(self.perf_kinds):
            m = statsui.module(k)                       # read only while shown (statsui._Live)
            m.set_size_request(*PERF_CELL)
            grid.attach(m, i % 2, i // 2, 1, 1)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                    max_content_height=PERF_MAX_H, child=grid)
        box.append(scroll)
        return box

    # -- Capture (P2) -------------------------------------------------------------------------
    def _action(self, icon_widget, label: str, on_click) -> Gtk.Button:
        b = Gtk.Button(css_classes=["gb-action"], focus_on_click=False, hexpand=True)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        holder = Gtk.Box(css_classes=["gb-action-icon"], halign=Gtk.Align.CENTER)
        icon_widget.set_hexpand(True)
        icon_widget.set_halign(Gtk.Align.CENTER)
        icon_widget.set_valign(Gtk.Align.CENTER)
        holder.append(icon_widget)
        col.append(holder)
        b.label = Gtk.Label(label=label, css_classes=["gb-action-label"])
        col.append(b.label)
        b.set_child(col)
        b.connect("clicked", lambda *_a: on_click())
        return b

    def _capture_actions(self) -> Gtk.Widget:
        row = Gtk.Box(spacing=8, homogeneous=True, css_classes=["gb-actions"])
        self.shot_btn = self._action(Gtk.Image(icon_name="camera-photo-symbolic", pixel_size=20), _("Screenshot"),
                                     self.screenshot)
        self.rec_btn = self._action(Gtk.Box(css_classes=["gb-rec-dot"]), _("Record"), self.toggle_record)
        row.append(self.shot_btn)
        row.append(self.rec_btn)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(row)
        chips = Gtk.Box(spacing=6, homogeneous=True, css_classes=["gb-chips"])
        self.replay_btn = Gtk.Button(label=_("Instant Replay"), css_classes=["gb-chip"], focus_on_click=False)
        self.replay_btn.connect("clicked", lambda *_a: self.toggle_replay())
        self.clip_btn = Gtk.Button(label=_("Save Last 30 s"), css_classes=["gb-chip"], focus_on_click=False,
                                   tooltip_text=_("Save the last 30 seconds (Super+Alt+G)"))
        self.clip_btn.connect("clicked", lambda *_a: self.save_clip())
        chips.append(self.replay_btn)
        chips.append(self.clip_btn)
        col.append(chips)
        return col

    def _capture(self):
        return getattr(self.topbar, "capture", None)

    def _recording(self) -> bool:
        cap = self._capture()
        return cap is not None and cap.recorder is not None

    def _sync_capture(self) -> None:
        """Record or Stop (with the recording's time) as it is now."""
        if not hasattr(self, "rec_btn"):
            return
        cap = self._capture()
        rec = self._recording()
        (self.rec_btn.add_css_class if rec else self.rec_btn.remove_css_class)("recording")
        label = _("Record")
        if rec:
            t0 = getattr(getattr(cap, "pill", None), "_t0", 0)
            s = int((GLib.get_monotonic_time() - t0) / 1_000_000) if t0 else 0
            label = _("Stop") + f" · {s // 60}:{s % 60:02d}"
        self.rec_btn.label.set_label(label)
        self.shot_btn.set_sensitive(cap is not None)
        self.rec_btn.set_sensitive(cap is not None)
        rp = cap.replay if cap is not None else None
        ok = rp is not None and rp.available()
        on = bool(ok and rp.running)
        self.replay_btn.set_sensitive(ok)
        self.replay_btn.set_tooltip_text(None if ok else _("Instant replay needs wl-screenrec"))
        (self.replay_btn.add_css_class if on else self.replay_btn.remove_css_class)("on")
        self.clip_btn.set_sensitive(on)

    def _after_close(self, fn) -> None:
        """fn once the Game Bar has gone: the game alone in the picture."""
        self.close_bar()
        GLib.timeout_add(_close_ms() + 60, lambda: (fn(), False)[1])

    def _cfg(self) -> dict:
        from .capture import DEFAULTS as CAP_DEFAULTS
        return dict(config.load("capture", CAP_DEFAULTS), timer=0)     # shortcuts never wait for the timer

    def screenshot(self) -> None:
        cap = self._capture()
        if cap is None:
            return
        from .capture import focused_output
        self._after_close(lambda: cap._shoot(None, self._cfg(), focused_output()))

    def toggle_replay(self) -> None:
        """Instant replay on (the game's display: the one in front) or off."""
        cap = self._capture()
        if cap is None:
            return
        if cap.replay.running:
            cap.replay.stop()
        else:
            from .capture import focused_output
            cap.replay.start(focused_output())
        self._sync_capture()

    def save_clip(self) -> None:
        """The last 30 seconds as a clip -- once the Game Bar has gone (it
        would be in the last moments)."""
        cap = self._capture()
        if cap is None or not cap.replay.running:
            return
        if self.is_open():
            self._after_close(cap.replay.save)
        else:
            cap.replay.save()

    def toggle_record(self) -> None:
        cap = self._capture()
        if cap is None:
            return
        if self._recording():
            cap.stop_recording()                         # stop here (the bar stays: what's next)
            self._sync_capture()
            return
        from .capture import focused_output
        self._after_close(lambda: cap._record(None, self._cfg(), focused_output()))

    def _grab(self, on: bool) -> None:
        pads = getattr(self.topbar, "gamepads", None)
        if pads is not None:
            pads.grab(on)

    # -- controller -------------------------------------------------------------------------------
    DIRECTIONS = {"up": Gtk.DirectionType.UP, "down": Gtk.DirectionType.DOWN,
                  "left": Gtk.DirectionType.LEFT, "right": Gtk.DirectionType.RIGHT}

    def pad(self, name: str) -> None:
        """A controller press (gamepad/service.py's action names)."""
        if not self.is_open():
            return
        self.set_focus_visible(True)                    # the ring shows from the first press
        if name in self.DIRECTIONS:
            focus = self.get_focus()
            rng = _slider_of(focus)
            if focus is None:
                self._focus_zone(0)
            elif rng is not None and name in ("left", "right"):     # a slider: its value
                rng.set_value(rng.get_value() + (SLIDER_STEP if name == "right" else -SLIDER_STEP))
            else:
                self.child_focus(self.DIRECTIONS[name])
        elif name == "primary":
            focus = self.get_focus()
            if focus is not None:
                focus.activate()                        # (a button shows its press, then clicks)
        elif name in ("back", "guide"):
            self.close_bar()
        elif name in ("switch_next", "switch_prev"):
            zones = self._zones()
            here = next((i for i, z in enumerate(zones) if self._inside(z)), -1)
            step = 1 if name == "switch_next" else -1
            self._focus_zone((here + step) % len(zones) if here >= 0 else 0)

    def _zones(self) -> list:
        """The bar, then each widget shown that has something to press."""
        return [self.bar] + [w for w in self.widgets.values()
                             if w.get_visible() and not w.has_css_class("hiding") and _focusables(w)]

    def _inside(self, zone) -> bool:
        f = self.get_focus()
        return f is not None and (f is zone or f.is_ancestor(zone))

    def _focus_zone(self, i: int) -> None:
        zones = self._zones()
        targets = _focusables(zones[i % len(zones)])
        if targets:
            targets[0].grab_focus()

    # -- input ------------------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        from gi.repository import Gdk
        if keyval == Gdk.KEY_Escape:
            self.close_bar()
            return True
        if keyval in (Gdk.KEY_g, Gdk.KEY_G) and state & Gdk.ModifierType.SUPER_MASK:
            self.close_bar()                            # Super+G again (the keyboard is ours while open)
            return True
        return False

    def _pressed(self, _g, _n, x, y) -> None:
        """A click on the dimmed game (not on the bar or a widget) closes it."""
        w = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if w is None or w is self.scrim or w is self.root:
            self.close_bar()
