"""Game Bar (Super+G; Vini: Windows' Xbox Game Bar, drawn the Big Sur way).

Over the game, a full-screen overlay: the screen dimmed (the Launchpad
scrim), a glass bar at the top -- the app in front, a toggle per widget, the
clock -- and the widgets themselves as Control Center-style glass panels.
Super+G again, Esc or a click on the dimmed screen closes it; the game keeps
running underneath.

Built in parts (ROADMAP M8): P1 this window, the bar and the widget frames;
P2 Capture (shell/capture.py), P3 microphone + system audio as two tracks,
P4 Performance (backend/stats.py, shell/statsui.py), P5 moving / pinning
widgets and the small HUD, then the last-30-seconds replay.

Lives in the menu bar's process (it already has the capture, the stats and
the window list): `sonata2 gamebar` -> its "gamebar" action. Nothing runs
while it's closed: the clock ticks only while it's on screen.

    bar = GameBar(app, topbar)      # topbar: for the app in front (optional)
    bar.toggle(); bar.open(); bar.close()
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from ..i18n import N_, _  # noqa: E402
from ..ui import tokens  # noqa: E402
from . import layer  # noqa: E402

NAMESPACE = "sonata2-gamebar"
CONFIG = "gamebar"
# the widgets, in the bar's order: id -> (title, icon, where it sits)
WIDGETS = {
    "capture": (N_("Capture"), "camera-photo-symbolic", (Gtk.Align.START, Gtk.Align.START)),
    "audio": (N_("Audio"), "audio-volume-high-symbolic", (Gtk.Align.START, Gtk.Align.END)),
    "performance": (N_("Performance"), "speedometer-symbolic", (Gtk.Align.END, Gtk.Align.START)),
}
DEFAULTS = {"widgets": ["capture", "audio", "performance"]}
BAR_TOP = 14                 # the bar below the screen's top edge
WIDGET_TOP = 76              # widgets start under the bar
WIDGET_SIDE = 24
WIDGET_W = 300


def _close_ms() -> int:
    """How long the closing animation runs (t_open, at Sonata's speed)."""
    return tokens.ms(float(tokens.SHARED["t_open"].rstrip("ms")))


ui.register("""
window.sonata-gamebar, window.sonata-gamebar > contents { background: none; box-shadow: none; }
/* the game dimmed, like Launchpad's backdrop */
.gb-scrim { background: %(scrim)s; transition: opacity %(t_open)s %(ease_out)s; }
.gb-root.closing .gb-scrim { opacity: 0; }
@keyframes gb-fade-in { from { opacity: 0; } to { opacity: 1; } }
.gb-scrim { animation: gb-fade-in %(t_open)s %(ease_out)s both; }

/* the bar: a Big Sur glass pill, sliding down from the top edge */
.gb-bar { background-color: %(panel_material)s; border-radius: %(r_plate)s; padding: 6px 8px;
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

/* a widget: Control Center glass, popping in a little after the bar */
.gb-widget { background-color: %(panel_material)s; border-radius: %(r_dialog)s; padding: 6px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_menu)s;
  color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
@keyframes gb-widget-in { from { opacity: 0; transform: scale(0.94); } to { opacity: 1; transform: none; } }
@keyframes gb-widget-out { to { opacity: 0; transform: scale(0.94); } }
.gb-widget { animation: gb-widget-in %(t_standard)s %(ease_out)s both; }
.gb-widget.hiding, .gb-root.closing .gb-widget { animation: gb-widget-out %(t_open)s %(ease_out)s both; }
.gb-widget-body { padding: 4px 4px 2px; }
.gb-widget-empty { color: %(label_secondary)s; font-size: %(text_small)s; margin: 14px 8px 16px; }
""", key="gamebar")


def _now() -> str:
    return GLib.DateTime.new_now_local().format("%H:%M")


class Widget(Gtk.Box):
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
        head = ui.panel.header(_(title))
        head.prepend(Gtk.Image(icon_name=icon, pixel_size=16, margin_end=6))
        self.append(head)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, css_classes=["gb-widget-body"])
        self.body.append(Gtk.Label(label=_("Coming soon"), css_classes=["gb-widget-empty"]))
        self.append(self.body)


class GameBar(Gtk.Window):
    def __init__(self, app, topbar=None):
        super().__init__(application=app, title=_("Game Bar"), decorated=False,
                         css_classes=["sonata-gamebar"])
        self.topbar = topbar
        self.cfg = config.load(CONFIG, DEFAULTS)
        self._clock_src = 0
        self._hide_src = 0

        self.root = Gtk.Overlay(css_classes=["gb-root"])
        self.scrim = Gtk.Box(css_classes=["gb-scrim"], hexpand=True, vexpand=True)
        self.root.set_child(self.scrim)
        self.root.add_overlay(self._bar())
        self.widgets = {}
        for wid in WIDGETS:
            w = Widget(wid)
            w.set_visible(wid in self.cfg["widgets"])
            self.widgets[wid] = w
            self.root.add_overlay(w)
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
        bar = Gtk.Box(css_classes=["gb-bar"], halign=Gtk.Align.CENTER, valign=Gtk.Align.START,
                      margin_top=BAR_TOP, spacing=2)
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
            b = Gtk.Button(icon_name=icon, css_classes=["gb-toggle"], can_focus=False,
                           tooltip_text=_(title), valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, w=wid: self.toggle_widget(w))
            self.toggles[wid] = b
            bar.append(b)
        bar.append(Gtk.Box(css_classes=["gb-sep"]))
        self.clock = Gtk.Label(css_classes=["gb-clock"], valign=Gtk.Align.CENTER)
        bar.append(self.clock)
        self.bar = bar
        return bar

    def _front_app(self) -> None:
        """The app in front (the game) beside the toggles."""
        from .capture import _app_gicon, _app_name
        key = None
        active = getattr(self.topbar, "_active", None)
        if active is not None:
            try:
                key = active()[0]
            except Exception:                           # a name on a bar never fails it
                key = None
        if key:
            gicon = _app_gicon(key)
            if gicon is not None:
                self.app_icon.set_from_gicon(gicon)
            else:
                self.app_icon.set_from_icon_name("input-gaming-symbolic")
            self.app_name.set_label(_app_name(key))
        else:
            self.app_icon.set_from_icon_name("input-gaming-symbolic")
            self.app_name.set_label(_("Game Bar"))

    def _sync_toggles(self) -> None:
        for wid, b in self.toggles.items():
            (b.add_css_class if self.widgets[wid].get_visible() else b.remove_css_class)("on")

    # -- widgets ----------------------------------------------------------------------------------
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
        self._front_app()
        self._tick()
        if not self._clock_src:
            self._clock_src = GLib.timeout_add_seconds(1, self._tick)
        # shown again: the CSS animations run anew only for newly mapped widgets
        self.set_visible(False)
        self.present()

    def close_bar(self) -> None:
        if not self.get_visible() or self.root.has_css_class("closing"):
            return
        self.root.add_css_class("closing")
        if self._clock_src:
            GLib.source_remove(self._clock_src)
            self._clock_src = 0

        def gone():
            self._hide_src = 0
            self.set_visible(False)
            self.root.remove_css_class("closing")
            return False
        self._hide_src = GLib.timeout_add(_close_ms(), gone)

    def _tick(self) -> bool:
        self.clock.set_label(_now())
        return True

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
