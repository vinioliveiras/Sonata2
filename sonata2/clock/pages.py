"""Clock's Stopwatch and Timers pages (macOS Clock). The state lives in
clock/timers.py (a file): both keep going with Clock closed, and the menu
bar process rings the timer (shell/alarmservice.py).

Nothing ticks while a page isn't on screen or nothing runs: the figures
follow the display's frames (a tick callback) only then."""
import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from . import timers as T  # noqa: E402

ui.register("""
.ck-big { font-family: %(font_display)s; font-weight: 200; font-size: 72px; color: %(label)s;
  font-feature-settings: "tnum"; }
.ck-ring-time { font-family: %(font_display)s; font-weight: 200; font-size: 50px; color: %(label)s;
  font-feature-settings: "tnum"; }
.ck-ring-sub { color: %(label_secondary)s; font-size: %(text_body)s; font-feature-settings: "tnum"; }
.ck-laps { background: none; margin: 0 24px 12px 24px; }
.ck-laps row { padding: 8px 2px; box-shadow: inset 0 1px %(separator)s; background: none; }
.ck-laps label { font-size: 14px; color: %(label)s; font-feature-settings: "tnum"; }
.ck-laps .best label { color: %(sys_green)s; }
.ck-laps .worst label { color: %(sys_red)s; }
.ck-chip { border-radius: 99px; padding: 3px 8px; min-height: 22px; background: alpha(%(label)s, 0.07);
  border: none; box-shadow: none; color: %(label)s; font-size: 12px; }
.ck-chip:hover { background: alpha(%(label)s, 0.13); }
.ck-pick spinbutton { font-family: %(font_display)s; font-size: 34px; font-weight: 300; min-width: 0;
  color: %(label)s; }
.ck-pick spinbutton text { min-width: 0; padding: 2px 0; }
.ck-pick .ck-unit { color: %(label_secondary)s; font-size: %(text_small)s; }
""", key="clock-pages")

SIDE = 48                      # the round buttons' distance from the window's sides


class _State:
    """timers.json, shared by both pages; reread when it changes on disk
    (the menu bar process sets a timer that rang back to idle)."""

    def __init__(self):
        self.sw, self.tm = T.load()
        self.listeners = []
        self._mon = None
        try:
            self._mon = Gio.File.new_for_path(T.path()).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._mon.connect("changed", lambda *_a: self.reload())
        except GLib.Error:
            pass

    def reload(self) -> None:
        sw, tm = T.load()
        if (sw, tm) != (self.sw, self.tm):
            self.sw, self.tm = sw, tm
            for f in self.listeners:
                f()

    def save(self) -> None:
        T.save(self.sw, self.tm)
        for f in self.listeners:
            f()


def _buttons(left: Gtk.Widget, right: Gtk.Widget) -> Gtk.Box:
    row = Gtk.Box(margin_start=SIDE, margin_end=SIDE)
    row.append(left)
    row.append(Gtk.Box(hexpand=True))
    row.append(right)
    return row


class _Ticking:
    """Follows the display's frames while `running()` and the page is on screen."""

    def _watch_frames(self, widget: Gtk.Widget) -> None:
        self._tick_id = 0
        widget.connect("map", lambda *_a: self._frames())
        widget.connect("unmap", lambda *_a: self._frames(False))

    def _frames(self, on: bool = None) -> None:
        on = (self.running() and self.root.get_mapped()) if on is None else on
        if on and not self._tick_id:
            self._tick_id = self.root.add_tick_callback(lambda *_a: (self.update(), GLib.SOURCE_CONTINUE)[1])
        elif not on and self._tick_id:
            self.root.remove_tick_callback(self._tick_id)
            self._tick_id = 0


class StopwatchPage(_Ticking):
    def __init__(self, state: _State):
        self.state = state
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, css_classes=["ck-main"])
        self.big = Gtk.Label(css_classes=["ck-big"], margin_top=48, margin_bottom=20)
        self.root.append(self.big)
        self.lap_btn = ui.controls.circle_button("Lap", self.lap_or_reset)
        self.go_btn = ui.controls.circle_button("Start", self.start_or_stop, tone="green")
        self.root.append(_buttons(self.lap_btn, self.go_btn))
        self.laps = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["ck-laps"])
        self.root.append(Gtk.ScrolledWindow(child=self.laps, vexpand=True, margin_top=16,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER))
        self._laps_shown = None
        self._watch_frames(self.root)
        state.listeners.append(self.refresh)
        self.refresh()

    def running(self) -> bool:
        return self.state.sw["running"]

    def start_or_stop(self) -> None:
        sw = self.state.sw
        T.sw_stop(sw) if sw["running"] else T.sw_start(sw)
        self.state.save()

    def lap_or_reset(self) -> None:
        sw = self.state.sw
        T.sw_lap(sw) if sw["running"] else T.sw_reset(sw)
        self.state.save()

    def refresh(self) -> None:
        sw = self.state.sw
        running, started = sw["running"], sw["running"] or sw["before"] > 0
        self.go_btn.set_label("Stop" if running else "Start")
        self.go_btn.set_tone("red" if running else "green")
        self.lap_btn.set_label("Lap" if running or not started else "Reset")
        self.lap_btn.set_sensitive(started)
        self._fill_laps()
        self.update()
        self._frames()

    def _fill_laps(self) -> None:
        laps = list(self.state.sw["laps"])
        started = self.running() or self.state.sw["before"] > 0
        key = (tuple(laps), started)
        if key == self._laps_shown:
            return
        self._laps_shown = key
        while (row := self.laps.get_first_child()) is not None:
            self.laps.remove(row)
        self.current_row = None
        if not started:
            return
        best, worst = T.best_worst(laps)
        self.current_row = self._lap_row(len(laps) + 1, 0)           # the lap under way, at the top
        self.laps.append(self.current_row)
        for i in reversed(range(len(laps))):
            row = self._lap_row(i + 1, laps[i])
            if i == best:
                row.add_css_class("best")
            elif i == worst:
                row.add_css_class("worst")
            self.laps.append(row)

    @staticmethod
    def _lap_row(n: int, seconds: float) -> Gtk.ListBoxRow:
        box = Gtk.Box()
        box.append(Gtk.Label(label=f"Lap {n}", xalign=0, hexpand=True))
        box.time = Gtk.Label(label=T.stopwatch_text(seconds), xalign=1)
        box.append(box.time)
        row = Gtk.ListBoxRow(child=box, activatable=False)
        row.time = box.time
        return row

    def update(self) -> None:
        sw = self.state.sw
        self.big.set_label(T.stopwatch_text(T.elapsed(sw)))
        if getattr(self, "current_row", None) is not None:
            self.current_row.time.set_label(T.stopwatch_text(T.current_lap(sw)))


class Ring(Gtk.DrawingArea):
    """What's left of the timer, as an arc from the top (orange on a faint track)."""

    def __init__(self):
        super().__init__(content_width=240, content_height=240, halign=Gtk.Align.CENTER)
        self.fraction = 1.0
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, w, h) -> None:
        line = 7
        r = min(w, h) / 2 - line
        cx, cy = w / 2, h / 2
        cr.set_line_width(line)
        cr.set_line_cap(1)                                  # round
        track = ui.rgba("label")
        cr.set_source_rgba(track.red, track.green, track.blue, 0.10)
        cr.arc(cx, cy, r, 0, 2 * math.pi)
        cr.stroke()
        if self.fraction > 0:
            c = ui.rgba("sys_orange")
            cr.set_source_rgba(c.red, c.green, c.blue, 1)
            top = -math.pi / 2
            cr.arc(cx, cy, r, top, top + 2 * math.pi * self.fraction)
            cr.stroke()


class TimerPage(_Ticking):
    def __init__(self, state: _State):
        self.state = state
        self.root = Gtk.Stack(vexpand=True, css_classes=["ck-main"])
        self.root.add_named(self._setup_view(), "setup")
        self.root.add_named(self._run_view(), "run")
        self._watch_frames(self.root)
        state.listeners.append(self.refresh)
        self.refresh()

    # -- setting a timer up: hours, minutes, seconds; quick choices; a name -----------------
    def _setup_view(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=40, valign=Gtk.Align.START)
        pick = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, css_classes=["ck-pick"])
        self.spins = []
        for unit, upper in (("hours", 23), ("min", 59), ("sec", 59)):
            s = Gtk.SpinButton.new_with_range(0, upper, 1)
            s.set_wrap(True)
            s.set_numeric(True)
            s.set_orientation(Gtk.Orientation.VERTICAL)
            s.set_width_chars(2)
            s.set_max_width_chars(2)
            s.connect("output", lambda sp: (sp.set_text(f"{int(sp.get_value()):02d}"), True)[1])
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            cell.append(s)
            cell.append(Gtk.Label(label=unit, css_classes=["ck-unit"]))
            pick.append(cell)
            self.spins.append(s)
        col.append(pick)
        chips = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        for secs in T.PRESETS:
            b = Gtk.Button(label=T.duration_text(secs), css_classes=["ck-chip"], can_focus=False)
            b.connect("clicked", lambda _b, s=secs: self.start(s))
            chips.append(b)
        col.append(chips)
        self.name = ui.controls.text_field(placeholder="Timer", on_activate=lambda *_a: self.start())
        self.name.set_halign(Gtk.Align.CENTER)
        self.name.set_size_request(220, -1)
        col.append(self.name)
        self.start_btn = ui.controls.circle_button("Start", self.start, tone="green")
        col.append(self.start_btn)
        return col

    # -- a timer running or paused ---------------------------------------------------------------
    def _run_view(self) -> Gtk.Widget:
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, margin_top=28)
        over = Gtk.Overlay(halign=Gtk.Align.CENTER)
        self.ring = Ring()
        over.set_child(self.ring)
        mid = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.left_lbl = Gtk.Label(css_classes=["ck-ring-time"])
        self.sub = Gtk.Label(css_classes=["ck-ring-sub"])
        mid.append(self.left_lbl)
        mid.append(self.sub)
        over.add_overlay(mid)
        col.append(over)
        self.cancel_btn = ui.controls.circle_button("Cancel", self.cancel)
        self.pause_btn = ui.controls.circle_button("Pause", self.pause_or_resume, tone="orange")
        col.append(_buttons(self.cancel_btn, self.pause_btn))
        return col

    def running(self) -> bool:
        return self.state.tm["state"] == "running"

    def duration(self) -> int:
        h, m, s = (int(x.get_value()) for x in self.spins)
        return h * 3600 + m * 60 + s

    def start(self, seconds: int = None) -> None:
        seconds = seconds or self.duration()
        if seconds <= 0:
            return
        T.tm_start(self.state.tm, seconds, self.name.get_text().strip())
        self.state.save()

    def pause_or_resume(self) -> None:
        tm = self.state.tm
        T.tm_pause(tm) if tm["state"] == "running" else T.tm_resume(tm)
        self.state.save()

    def cancel(self) -> None:
        T.tm_cancel(self.state.tm)
        self.state.save()

    def refresh(self) -> None:
        tm = self.state.tm
        idle = tm["state"] == "idle"
        self.root.set_visible_child_name("setup" if idle else "run")
        if idle:                                         # ready again with the last one
            d = int(tm["duration"])
            for spin, v in zip(self.spins, (d // 3600, d % 3600 // 60, d % 60)):
                spin.set_value(v)
            self.name.set_text(tm["label"])
        else:
            self.pause_btn.set_label("Pause" if self.running() else "Resume")
            self.pause_btn.set_tone("orange" if self.running() else "green")
        self.update()
        self._frames()

    def update(self) -> None:
        tm = self.state.tm
        if tm["state"] == "idle":
            return
        self.left_lbl.set_label(T.timer_text(T.left(tm)))
        self.ring.fraction = T.progress(tm)
        self.ring.queue_draw()
        name = tm["label"] or T.duration_text(tm["duration"])
        if tm["state"] == "running":
            from .window import h24
            end = GLib.DateTime.new_from_unix_local(int(tm["ends"]))
            self.sub.set_label(f"{end.format('%H:%M' if h24() else '%-I:%M %p')} · {name}")
        else:
            self.sub.set_label(f"Paused · {name}")
