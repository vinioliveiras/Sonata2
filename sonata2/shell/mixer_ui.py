"""The Sound menu's per-app volumes (backend/mixer.py): one row per app
playing sound -- its icon (click: mute), its name, a slider. Rows come and
go with a short slide (Gtk.Revealer, no relayout of the others' content);
a slider's level is applied and saved a moment after it stops moving."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio, Gtk, Pango  # noqa: E402

from .. import apps, icons, ui  # noqa: E402
from ..backend import mixer  # noqa: E402

SLIDE_MS = 160
APPLY_MS = 120          # a moving slider: one pactl call per pause, not per step
ROW_SLIDER_W = 110

ui.register("""
.mixer-row { min-height: 26px; padding: 1px 10px; }
.mixer-row label { font-size: %(text_body)s; }
.mixer-row button.mixer-mute { padding: 0; min-width: 20px; min-height: 20px; background: none; border: none;
  box-shadow: none; transition: opacity %(t_fast)s ease-out; }
.mixer-row.muted button.mixer-mute { opacity: 0.4; }
.mixer-row.muted label { color: %(label_secondary)s; }
.mixer-empty { padding: 2px 10px 4px 10px; font-size: %(text_body)s; color: %(label_secondary)s; }
""", key="mixer")


def _gicon(stream: "mixer.Stream"):
    try:
        did = apps.match_app_id(stream.key) or (apps.match_app_id(stream.icon) if stream.icon else None)
        info = apps.lookup(did) if did else None
    except Exception:                       # an icon is never worth a broken menu
        info = None
    if info:
        return icons.app_icon(info)
    return Gio.ThemedIcon.new_from_names([n for n in (stream.icon, "audio-x-generic") if n])


class MixerRow(Gtk.Revealer):
    def __init__(self, owner, stream):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=SLIDE_MS)
        self.owner, self.key = owner, stream.key
        self.box = Gtk.Box(spacing=8, css_classes=["mixer-row"])
        self.mute = Gtk.Button(css_classes=["mixer-mute"], valign=Gtk.Align.CENTER, can_focus=False,
                               tooltip_text="Mute")
        self.image = Gtk.Image(pixel_size=16)
        icons.set_image(self.image, _gicon(stream))
        self.mute.set_child(self.image)
        self.mute.connect("clicked", lambda _b: self.toggle_mute())
        self.box.append(self.mute)
        self.label = Gtk.Label(label=stream.name, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END,
                               max_width_chars=14, width_chars=1)
        self.box.append(self.label)
        self.slider = ui.controls.slider(stream.volume, self._moved, lower=0, upper=mixer.MAX_PERCENT)
        self.slider.set_size_request(ROW_SLIDER_W, -1)
        self.slider.set_hexpand(False)
        self.box.append(self.slider)
        self.set_child(self.box)
        self._src = 0
        self._quiet = False
        self.muted = False
        self.update(stream)

    def update(self, stream) -> None:
        """New values from PipeWire (not while the slider is being moved)."""
        self.label.set_label(stream.name)
        if not self._src and abs(self.slider.get_value() - stream.volume) >= 1:
            self._quiet = True
            self.slider.set_value(stream.volume)
            self._quiet = False
        self._set_muted(stream.muted)

    def _set_muted(self, on: bool) -> None:
        self.muted = bool(on)
        (self.box.add_css_class if on else self.box.remove_css_class)("muted")
        self.mute.set_tooltip_text("Unmute" if on else "Mute")

    def toggle_mute(self) -> None:
        on = not self.muted
        self._set_muted(on)
        self.owner.apply(self.key, muted=on)

    def _moved(self, v) -> None:
        if self._quiet:
            return
        if self._src:
            GLib.source_remove(self._src)

        def go():
            self._src = 0
            self.owner.apply(self.key, percent=int(round(self.slider.get_value())),
                             muted=False if self.muted else None)
            if self.muted:
                self._set_muted(False)           # moving the level unmutes (macOS)
            return False
        self._src = GLib.timeout_add(APPLY_MS, go)


class AppMixer(Gtk.Box):
    """The list; set_streams() with what's playing now (diffed: rows that
    stay keep their state, new ones slide in, gone ones slide out)."""

    def __init__(self, apply=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.rows = {}
        self._streams = []
        self.apply_fn = apply or self._apply_async
        self.empty = Gtk.Label(label="No apps playing sound", xalign=0, css_classes=["mixer-empty"])
        self.append(self.empty)

    def set_streams(self, streams) -> None:
        self._streams = list(streams)
        shown = mixer.group(self._streams)
        keys = [s.key for s in shown]
        for key in [k for k in self.rows if k not in keys]:
            self._drop(key)
        for s in shown:
            row = self.rows.get(s.key)
            if row is None:
                row = self.rows[s.key] = MixerRow(self, s)
                self.append(row)
                GLib.idle_add(lambda r=row: (r.set_reveal_child(True), False)[1])   # slides in
            else:
                row.update(s)
        self.empty.set_visible(not shown)

    def _drop(self, key) -> None:
        row = self.rows.pop(key)
        row.set_reveal_child(False)

        def gone(r, _p):
            if not r.get_child_revealed() and r.get_parent() is self:
                self.remove(r)
        row.connect("notify::child-revealed", gone)
        if not row.get_mapped():
            self.remove(row)

    def apply(self, key, percent=None, muted=None) -> None:
        self.apply_fn(key, percent, muted, list(self._streams))

    @staticmethod
    def _apply_async(key, percent, muted, streams) -> None:
        from ..backend import system
        system.run_async(lambda: mixer.set_level(key, percent, muted, streams))
