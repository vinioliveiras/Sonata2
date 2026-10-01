"""Clock's alarms going off, in the menu bar process (always running, so
an alarm rings with Clock closed).

One timer aims at the next alarm (at most 30 s ahead: after a suspend the
check runs at once and a missed alarm still rings, once). Ringing: the
alarm sound in a loop and a card at the top right (Snooze / Stop) above
everything -- Do Not Disturb never holds an alarm back (it isn't a
notification). Nobody stops it: quiet after RING_MAX_S. A one-time alarm
turns itself off; Snooze rings it again SNOOZE_MIN minutes later."""
import datetime as dt
import os
import shutil
import signal
import subprocess

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .. import ui  # noqa: E402
from ..clock import alarms as A  # noqa: E402
from . import layer  # noqa: E402

CHECK_MAX_S = 30

ui.register("""
window.sonata-alarm { background: transparent; }
.alarm-card { background: %(panel_material)s; border-radius: 16px; padding: 14px 14px 12px 16px;
  margin: 10px; box-shadow: 0 8px 28px rgba(0,0,0,0.35), 0 0 0 0.5px rgba(0,0,0,0.35),
  inset 0 0 0 0.5px %(highlight)s; }
.alarm-app { font-size: %(text_small)s; color: %(label_secondary)s; font-weight: 600; }
.alarm-label { font-size: 15px; font-weight: 700; color: %(label)s; }
.alarm-time { font-family: %(font_display)s; font-size: 30px; font-weight: 300; color: %(label)s; }
.alarm-card button { min-height: 28px; padding: 0 16px; border-radius: 8px; font-weight: 600; }
""", key="alarm")


RAMP = (0.3, 0.45, 0.6, 0.8)        # the first plays, softer; then full volume (a gentle wake-up)


def ring_command(path: str, player: str, loop: bool = True) -> list:
    """sh -c: play the sound getting louder, then on and on (loop) -- or once (a preview)."""
    def play(v):
        if player == "pw-play":
            return f'pw-play --volume {v} "$0"'
        return f'paplay --volume {int(v * 65536)} "$0"'
    if not loop:
        return ["sh", "-c", play(0.8), path]
    ramp = "; ".join(f"{play(v)}; sleep 0.6" for v in RAMP)
    return ["sh", "-c", f'{ramp}; while :; do {play(1.0)}; sleep 0.6; done', path]


class Ringer:
    """An alarm sound, looped until stop() (or played once: preview)."""

    def __init__(self):
        self.proc = None

    def start(self, sound: str = A.SOUND, loop: bool = True) -> None:
        self.stop()
        path = A.sound_path(sound)
        player = next((p for p in ("pw-play", "paplay") if shutil.which(p)), None)
        if not player or not os.path.exists(path):
            return
        try:
            self.proc = subprocess.Popen(ring_command(path, player, loop),
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                         start_new_session=True)
        except OSError:
            self.proc = None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass
        self.proc = None

    @property
    def ringing(self) -> bool:
        return bool(self.proc and self.proc.poll() is None)


class AlarmCard(Gtk.Window):
    """Top right, above everything: the alarm's name and time, Snooze and Stop."""

    def __init__(self, app, alarm: dict, on_snooze, on_stop):
        super().__init__(application=app, title="Alarm", decorated=False, css_classes=["sonata-alarm"])
        self.alarm = alarm
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, css_classes=["alarm-card"])
        card.set_size_request(320, -1)
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Image(icon_name="alarm-symbolic", pixel_size=14))
        head.append(Gtk.Label(label="ALARM", xalign=0, css_classes=["alarm-app"]))
        card.append(head)
        row = Gtk.Box(spacing=10, margin_top=4)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        texts.append(Gtk.Label(label=A.time_text(alarm, h24()), xalign=0, css_classes=["alarm-time"]))
        texts.append(Gtk.Label(label=alarm.get("label") or "Alarm", xalign=0, css_classes=["alarm-label"],
                               ellipsize=3))
        row.append(texts)
        btns = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        if alarm.get("snooze"):
            self.snooze_btn = ui.controls.push_button("Snooze", on_snooze)
            btns.append(self.snooze_btn)
        self.stop_btn = ui.controls.push_button("Stop", on_stop, style="default")
        btns.append(self.stop_btn)
        row.append(btns)
        card.append(row)
        self.set_child(card)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-alarm")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)
            LS.set_anchor(self, LS.Edge.RIGHT, True)
            LS.set_margin(self, LS.Edge.TOP, 24)
            LS.set_keyboard_mode(self, LS.KeyboardMode.ON_DEMAND)


def h24() -> bool:
    from .. import config
    from .topbar import DEFAULTS
    return "%H" in (config.load("topbar", DEFAULTS).get("clock_format") or "%H")


class AlarmService:
    def __init__(self, app, now=None):
        self.app = app
        self.alarms = A.load()
        self.last = now or dt.datetime.now()
        self.snoozed = []                # [(when, alarm)]
        self.ringer = Ringer()
        self.card = None
        self.current = None              # the alarm ringing
        self.timer = 0
        self.quiet = 0                   # RING_MAX_S timeout
        self.rung = []                   # (tests, log)
        try:
            self._mon = Gio.File.new_for_path(A.path()).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._mon.connect("changed", lambda *_a: self.reload())
        except GLib.Error:
            self._mon = None
        self.schedule()

    def reload(self) -> None:
        self.alarms = A.load()
        self.schedule()

    def schedule(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
        nxt = A.next_any(self.alarms, self.last)
        times = [t for t in [nxt] + [w for w, _a in self.snoozed] if t]
        secs = min(((min(times) - dt.datetime.now()).total_seconds() + 0.5) if times else CHECK_MAX_S,
                   CHECK_MAX_S)
        self.timer = GLib.timeout_add(int(max(0.2, secs) * 1000), self._tick)

    def _tick(self) -> bool:
        self.timer = 0
        self.check()
        return False

    def check(self, now=None) -> list:
        now = now or dt.datetime.now()
        due = A.due(self.alarms, self.last, now)
        due += [a for w, a in self.snoozed if w <= now]
        self.snoozed = [(w, a) for w, a in self.snoozed if w > now]
        self.last = now
        once = [a["id"] for a in due if not a["repeat"]]
        if once:                                       # a one-time alarm turns itself off
            self.alarms = A.load()
            for a in self.alarms:
                if a["id"] in once:
                    a["enabled"] = False
            A.save(self.alarms)
        for a in due:
            self.ring(a)
        self.schedule()
        return due

    def ring(self, alarm: dict) -> None:
        self.rung.append(alarm["id"])
        self.dismiss(stop_sound=False)
        self.current = alarm
        self.card = AlarmCard(self.app, alarm, self.snooze, self.stop)
        self.card.present()
        self.ringer.start(alarm.get("sound", A.SOUND))
        self.quiet = GLib.timeout_add_seconds(A.RING_MAX_S, lambda: (self.stop(), False)[1])

    def snooze(self) -> None:
        if self.current:
            self.snoozed.append((dt.datetime.now() + dt.timedelta(minutes=A.SNOOZE_MIN), self.current))
        self.stop()

    def stop(self) -> None:
        self.dismiss()
        self.schedule()

    def dismiss(self, stop_sound: bool = True) -> None:
        if stop_sound:
            self.ringer.stop()
        if self.quiet:
            GLib.source_remove(self.quiet)
            self.quiet = 0
        if self.card is not None:
            self.card.destroy()
            self.card = None
        self.current = None
