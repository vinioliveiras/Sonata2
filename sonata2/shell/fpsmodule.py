"""Control Center > FPS Limit (Vini: a row of choices, off by default; not in
the default layout -- Add Controls). Games' frame rate through frame-pacer
(fpslimit.py): Off, 30, 60, 90, 120 or Max (the display's refresh rate).
Without frame-pacer the choices are greyed out and the module offers to
install it.

Under the choices, a frame-time graph of the app in front, named in the
title row (Vini: as tall as the other two-row modules): its frame time over
the last half minute -- one point per read, the average of the frames since
the one before (Vini: frame by frame it ran by too fast) -- the limit's
target as a dashed line, the average and the 1 % low. From sonata-corners
(IPC sonata/fps with frametimes), read only while shown."""
import math
import statistics
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import fpslimit, ui  # noqa: E402

LABELS = {"off": "Off", "max": "Max"}

ui.register("""
/* two grid rows (120 px): padding 5 + title 19 + 3 + choices 24 + 4 + the graph + 5 */
.cc-fps { padding-top: 5px; padding-bottom: 5px; }
.cc-fps .cc-fps-cap { font-size: %(text_small)s; color: %(label_secondary)s; }
.cc-fps .cc-fps-install { min-height: 0; padding: 0 8px; font-size: %(text_small)s; border-radius: 6px; }
.cc-fps .cc-fps-app { font-size: %(text_small)s; color: %(label_secondary)s; }
.cc-fps .cc-fps-live { font-size: %(text_small)s; font-weight: 600; font-feature-settings: "tnum"; }
.cc-fps .ft-cap { font-size: 10px; color: %(label_secondary)s; font-feature-settings: "tnum"; margin: 2px 6px; }
""", key="fpsmodule")


def refresh_hz() -> float:
    """The fastest display's refresh rate (Max follows it)."""
    best = 0.0
    disp = Gdk.Display.get_default()
    mons = disp.get_monitors() if disp else None
    for i in range(mons.get_n_items() if mons else 0):
        best = max(best, (mons.get_item(i).get_refresh_rate() or 0) / 1000)
    return best


class FpsModule(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3, css_classes=["panel-module", "cc-fps"])
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label="FPS Limit", xalign=0, css_classes=["panel-module-title"]))
        # the app being measured (Vini)
        self.app = Gtk.Label(label="", xalign=0, hexpand=True, css_classes=["cc-fps-app"],
                             ellipsize=Pango.EllipsizeMode.END, width_chars=1)
        head.append(self.app)
        self.live = Gtk.Label(label="", css_classes=["cc-fps-live"])          # "58 fps"
        head.append(self.live)
        self.cap = Gtk.Label(label="games", css_classes=["cc-fps-cap"])
        head.append(self.cap)
        self.install_btn = Gtk.Button(label="Install frame-pacer", css_classes=["cc-fps-install"],
                                      can_focus=False, visible=False)
        self.install_btn.connect("clicked", lambda _b: self._install())
        head.append(self.install_btn)
        self.append(head)
        self.seg = ui.controls.segmented([(c, LABELS.get(c, c)) for c in fpslimit.CHOICES], None, self.choose)
        self.buttons = self.seg.buttons
        self.append(self.seg)
        self.graph = FrameTimeGraph()
        over = Gtk.Overlay(child=self.graph, vexpand=True, margin_top=1)
        self.avg = Gtk.Label(label="", css_classes=["ft-cap"], halign=Gtk.Align.START, valign=Gtk.Align.START,
                             can_target=False)
        self.low = Gtk.Label(label="", css_classes=["ft-cap"], halign=Gtk.Align.END, valign=Gtk.Align.START,
                             can_target=False)
        over.add_overlay(self.avg)
        over.add_overlay(self.low)
        self.append(over)
        self._poll = 0
        self._busy = False
        self.history, self._app_id = [], None       # one point per read (the graph)
        self.connect("map", lambda *_: (self.update(), self._start()))
        self.connect("unmap", lambda *_: self._stop())

    def update(self) -> None:
        ok = fpslimit.installed()
        self.seg.set_sensitive(ok)
        self.install_btn.set_visible(not ok and self.cap.get_label() != "Installing…")
        self.cap.set_visible((ok and not self.live.get_label()) or self.cap.get_label() == "Installing…")
        cur = fpslimit.get()
        for c, b in self.buttons.items():
            (b.add_css_class if c == cur else b.remove_css_class)("on")
        hz = refresh_hz()
        self.buttons["max"].set_tooltip_text(f"The display's rate ({round(hz)} FPS)" if hz else None)
        lim = fpslimit.resolve(cur, hz)
        self.graph.target = 1000.0 / int(lim) if lim.isdigit() else None
        self.graph.queue_draw()

    # -- the frame-time graph: read only while Control Center shows it ---------------------------
    def _start(self) -> None:
        if not self._poll:
            self._poll = GLib.timeout_add(POLL_MS, self._tick)
            self._tick()

    def _stop(self) -> None:
        if self._poll:
            GLib.source_remove(self._poll)
            self._poll = 0

    def _tick(self) -> bool:
        if self._busy:
            return True
        self._busy = True
        from ..backend import system

        def done(r):
            self._busy = False
            self.show_frames(r)
        system.run_async(read_frames, done)
        return True

    def show_frames(self, r) -> None:
        """r: the plugin's answer (None: no plugin / no Wayfire)."""
        if not isinstance(r, dict) or "frametimes" not in r:
            self.graph.set_times([], "Update Sonata's Wayfire plugin (./install.sh)" if r is not None else "")
            self.history, self._app_id = [], None
            self.app.set_label("")
            self.live.set_label("")
            self.avg.set_label("")
            self.low.set_label("")
            return
        times = [t for t in r.get("frametimes") or [] if isinstance(t, (int, float)) and t > 0]
        if not r.get("app-id") or len(times) < 2:
            self.graph.set_times([], "No app drawing in front")
            self.history, self._app_id = [], None
            self.app.set_label("")
            self.live.set_label("")
            self.cap.set_visible(fpslimit.installed())
            self.avg.set_label("")
            self.low.set_label("")
            return
        if r["app-id"] != self._app_id:                  # another app in front: its own graph
            self.history, self._app_id = [], r["app-id"]
            self.app.set_label(app_name(r["app-id"]))
        self.history = (self.history + [recent_average(times)])[-SHOWN:]
        self.graph.set_times(self.history)
        avg, low = summary(times)
        self.live.set_label(f"{int(r.get('fps') or 0)} fps")
        self.avg.set_label(f"{avg:.1f} ms")
        self.low.set_label(f"1% low {low} fps")
        self.cap.set_visible(False)                     # the live figure says it

    def choose(self, c: str) -> None:
        fpslimit.set(c, refresh_hz())
        self.update()

    def _install(self) -> None:
        self.cap.set_label("Installing…")
        self.update()

        def done(err):
            self.cap.set_label("games" if not err else "couldn't install")
            self.cap.set_tooltip_text(err or "Steam picks it up the next time it opens")
            self.update()
        fpslimit.install(done)


POLL_MS = 250
SHOWN = 120                        # points on the graph, one per read: 30 s (newest at the right)


def recent_average(times) -> float:
    """The average frame time of the frames drawn since the last read (the
    newest ones adding up to POLL_MS)."""
    total, picked = 0.0, []
    for t in reversed(times):
        picked.append(t)
        total += t
        if total >= POLL_MS:
            break
    return statistics.fmean(picked)


def app_name(app_id: str) -> str:
    """The app's name for a window's app_id (Steam games too)."""
    from .. import apps, windowapps
    did = apps.match_app_id(app_id)
    info = apps.lookup(did) if did else None
    if info is not None:
        return info.get_display_name()
    try:
        return windowapps.describe(app_id)[0] or app_id
    except Exception:
        return app_id


def read_frames():
    try:
        from ..wl.wfipc import WayfireIPC
        return WayfireIPC().call("sonata/fps", {"frametimes": True})
    except Exception:
        return None


def summary(times) -> tuple:
    """(average frame time in ms, 1 % low in fps: the slowest 1 % of frames)."""
    avg = statistics.fmean(times)
    worst = sorted(times)[-max(1, len(times) // 100):]
    return avg, max(0, round(1000.0 / statistics.fmean(worst)))


class FrameTimeGraph(Gtk.DrawingArea):
    """Each frame's time as a line (spikes: stutters), the limit's target dashed."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True, content_height=40, can_target=False)
        self.times, self.note, self.target = [], "", None
        self.set_draw_func(self._draw)

    def set_times(self, times, note: str = "") -> None:
        self.times, self.note = list(times)[-SHOWN:], note
        self.queue_draw()

    def scale(self) -> float:
        """The top of the graph in ms: twice the target (or 60 fps), more for big spikes."""
        base = 2 * (self.target or 16.7)
        peak = sorted(self.times)[-max(1, len(self.times) // 50)] if self.times else 0
        return max(base, min(100.0, peak * 1.15))

    def _draw(self, _a, cr, w, h) -> None:
        c = self.get_color()
        r = 6
        cr.new_sub_path()
        cr.arc(w - r, r, r, -math.pi / 2, 0)
        cr.arc(w - r, h - r, r, 0, math.pi / 2)
        cr.arc(r, h - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 1.5 * math.pi)
        cr.close_path()
        cr.set_source_rgba(c.red, c.green, c.blue, 0.06)
        cr.fill()
        if not self.times:
            if self.note:
                from gi.repository import PangoCairo
                lay = PangoCairo.create_layout(cr)
                lay.set_text(self.note, -1)
                fd = Pango.FontDescription.from_string("sans 9")
                lay.set_font_description(fd)
                _i, log = lay.get_pixel_extents()
                cr.move_to((w - log.width) / 2, (h - log.height) / 2)
                cr.set_source_rgba(c.red, c.green, c.blue, 0.5)
                PangoCairo.show_layout(cr, lay)
            return
        top, pad = self.scale(), 3
        y = lambda ms: h - pad - min(ms, top) / top * (h - 2 * pad)   # noqa: E731
        if self.target:
            cr.set_source_rgba(c.red, c.green, c.blue, 0.3)
            cr.set_line_width(1)
            cr.set_dash([3, 3])
            cr.move_to(pad, round(y(self.target)) + 0.5)
            cr.line_to(w - pad, round(y(self.target)) + 0.5)
            cr.stroke()
            cr.set_dash([])
        acc = ui.rgba("accent")
        cr.set_source_rgba(acc.red, acc.green, acc.blue, 1)
        cr.set_line_width(1.4)
        cr.set_line_join(1)
        step = (w - 2 * pad) / max(1, SHOWN - 1)
        x0 = w - pad - step * (len(self.times) - 1)
        for i, t in enumerate(self.times):
            (cr.move_to if i == 0 else cr.line_to)(x0 + i * step, y(t))
        cr.stroke()


def module() -> Gtk.Widget:
    return FpsModule()
