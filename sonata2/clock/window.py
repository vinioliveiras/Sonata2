"""Clock (macOS Ventura Clock): Alarms.

The glass toolbar continues the compositor's title bar, with + (new
alarm) at its end. Each alarm is a row: the time in large light figures,
its name and repeat days under it, a switch to turn it on or off. A click
edits it in a popover (time, repeat days, name, snooze, Delete);
right-click deletes. Alarms ring from the menu bar process
(shell/alarmservice.py), so Clock may be closed."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from . import alarms as A  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.clock"

ui.register("""
window.sonata-clock .ck-main { background: %(content_bg)s; }
.ck-title { font-family: %(font_display)s; font-size: 26px; font-weight: 700; color: %(label)s; padding: 14px 24px 4px 24px; }
.ck-list { background: none; padding: 0 12px 16px 12px; }
.ck-list row { border-radius: 10px; padding: 0; background: none; }
.ck-list row:hover { background: %(tool_hover)s; }
.ck-row { padding: 10px 14px; box-shadow: inset 0 -1px %(separator)s; }
.ck-time { font-family: %(font_display)s; font-size: 40px; font-weight: 300; color: %(label)s; }
.ck-row.off .ck-time, .ck-row.off .ck-sub { color: %(label_tertiary)s; }
.ck-sub { font-size: %(text_body)s; color: %(label_secondary)s; }
.ck-empty { color: %(label_tertiary)s; font-size: %(text_title)s; }
.ck-edit { padding: 12px; }
.ck-edit spinbutton { font-family: %(font_display)s; font-size: 20px; font-weight: 400; min-width: 0; }
.ck-edit spinbutton text { min-width: 0; padding: 2px 0; }
.ck-colon { font-family: %(font_display)s; font-size: 20px; color: %(label)s; }
.ck-day { min-width: 30px; min-height: 30px; padding: 0; border-radius: 99px; font-weight: 600; }
.ck-day:checked { background: %(accent)s; color: white; }
""", key="clock")


def h24() -> bool:
    from .. import config
    from ..shell.topbar import DEFAULTS, is_24h
    return is_24h(config.load("topbar", DEFAULTS).get("clock_format") or "%H")


class ClockWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        if not GLib.get_application_name():
            GLib.set_application_name("Clock")
        super().__init__(application=app, title="Clock", css_classes=["sonata-clock"])
        ui.window.standard(self)
        ui.window.remember_size(self, "clock", 460, 560)
        self.set_size_request(380, 360)
        self.toolbar = ui.window.glass_toolbar(self, end=(("list-add-symbolic", "New Alarm", self.add),))
        self.add_btn = self.toolbar.get_child().get_end_widget().get_first_child()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self.toolbar)
        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True, css_classes=["ck-main"])
        main.append(Gtk.Label(label="Alarms", xalign=0, css_classes=["ck-title"]))
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["ck-list"])
        self.list.connect("row-activated", lambda _l, row: self.edit(row.alarm, row))
        self.empty = Gtk.Label(label="No Alarms", css_classes=["ck-empty"], vexpand=True, valign=Gtk.Align.CENTER)
        main.append(self.empty)
        main.append(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        col.append(main)
        self.set_child(col)
        self.alarms = []
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        from gi.repository import Gio
        self._mon = Gio.File.new_for_path(A.path()).monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        self._mon.connect("changed", lambda *_a: self.refresh())     # a one-time alarm went off
        self.refresh()

    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        if cmd and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self.add()
            return True
        if cmd and keyval in (Gdk.KEY_w, Gdk.KEY_W):
            self.close()
            return True
        return False

    # -- list ------------------------------------------------------------------------------
    def refresh(self) -> None:
        self.alarms = A.load()
        while (row := self.list.get_first_child()) is not None:
            self.list.remove(row)
        for a in self.alarms:
            self.list.append(self._row(a))
        self.empty.set_visible(not self.alarms)
        self.list.get_parent().set_visible(bool(self.alarms))

    def _row(self, a: dict) -> Gtk.ListBoxRow:
        box = Gtk.Box(spacing=12, css_classes=["ck-row"] + ([] if a["enabled"] else ["off"]))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        texts.append(Gtk.Label(label=A.time_text(a, h24()), xalign=0, css_classes=["ck-time"]))
        sub = ", ".join(t for t in (a["label"], A.repeat_text(a["repeat"])) if t)
        texts.append(Gtk.Label(label=sub, xalign=0, css_classes=["ck-sub"], ellipsize=Pango.EllipsizeMode.END))
        box.append(texts)
        sw = Gtk.Switch(active=a["enabled"], valign=Gtk.Align.CENTER)
        sw.connect("notify::active", lambda s, _p, i=a["id"]: self.set_enabled(i, s.get_active()))
        box.append(sw)
        row = Gtk.ListBoxRow(child=box)
        row.alarm, row.switch = a, sw
        menu = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        menu.connect("pressed", lambda g, _n, x, y, r=row: ui.menu.popup(
            r, [[ui.menu.Item("Delete Alarm", lambda: self.delete(r.alarm["id"]))]], at=(x, y)))
        row.add_controller(menu)
        return row

    # -- changes -----------------------------------------------------------------------------
    def _store(self, alarms) -> None:
        A.save(alarms)
        # after the switch's signal and the popover's closing: the row they
        # belong to is rebuilt
        GLib.idle_add(lambda: (self.refresh(), False)[1])

    def set_enabled(self, aid: str, on: bool) -> None:
        alarms = A.load()
        for a in alarms:
            if a["id"] == aid and a["enabled"] != on:
                a["enabled"] = on
                self._store(alarms)
                return

    def delete(self, aid: str) -> None:
        self._store([a for a in A.load() if a["id"] != aid])

    def put(self, alarm: dict) -> None:
        alarms = [a for a in A.load() if a["id"] != alarm["id"]] + [alarm]
        self._store(sorted(alarms, key=lambda a: (a["hour"], a["minute"])))

    def preview(self, sound) -> None:
        """Play an alarm sound once (None: stop the one playing)."""
        if not hasattr(self, "_previewer"):
            self._previewer = _preview_player()
        if sound is None:
            self._previewer.stop()
        else:
            self._previewer.start(sound, loop=False)

    def add(self) -> None:
        now = GLib.DateTime.new_now_local()
        a = A.new(now.get_hour(), now.get_minute())
        self.edit(a, self.add_btn, new=True)

    # -- edit popover ------------------------------------------------------------------------
    def edit(self, alarm: dict, anchor: Gtk.Widget, new: bool = False) -> Gtk.Popover:
        a = dict(alarm, repeat=list(alarm["repeat"]))
        pop = Gtk.Popover()
        pop.set_parent(anchor)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["ck-edit"])

        def spin(value, upper):
            s = Gtk.SpinButton.new_with_range(0, upper, 1)
            s.set_wrap(True)
            s.set_numeric(True)
            s.set_value(value)
            s.connect("output", lambda sp: (sp.set_text(f"{int(sp.get_value()):02d}"), True)[1])
            s.set_orientation(Gtk.Orientation.VERTICAL)
            s.set_width_chars(2)                  # two digits, never wider (they wrapped)
            s.set_max_width_chars(2)
            return s
        hour, minute = spin(a["hour"], 23), spin(a["minute"], 59)
        times = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        times.append(hour)
        times.append(Gtk.Label(label=":", css_classes=["ck-colon"]))
        times.append(minute)
        box.append(times)
        days = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        toggles = []
        for i, d in enumerate(A.DAYS):
            t = Gtk.ToggleButton(label=d[0], active=i in a["repeat"], css_classes=["ck-day"], tooltip_text=d)
            toggles.append(t)
            days.append(t)
        box.append(days)
        label = Gtk.Entry(text=a["label"], placeholder_text="Alarm")
        box.append(label)
        snooze = Gtk.Box(spacing=8)
        snooze.append(Gtk.Label(label="Snooze", hexpand=True, xalign=0))
        snooze_sw = Gtk.Switch(active=a["snooze"])
        snooze.append(snooze_sw)
        box.append(snooze)
        # the sound: picking one plays it once (a preview), like the iPhone
        keys = list(A.SOUNDS)
        sound = Gtk.Box(spacing=8)
        sound.append(Gtk.Label(label="Sound", hexpand=True, xalign=0))
        sound_dd = ui.controls.popup_button([A.SOUNDS[k][0] for k in keys],
                                            keys.index(a["sound"]) if a["sound"] in keys else 0,
                                            lambda i: self.preview(keys[i]))
        sound.append(sound_dd)
        box.append(sound)
        btns = Gtk.Box(spacing=8, margin_top=4)
        if not new:
            btns.append(ui.controls.push_button("Delete", lambda: (pop.popdown(), self.delete(a["id"])),
                                                style="destructive"))
        btns.append(Gtk.Box(hexpand=True))
        btns.append(ui.controls.push_button("Cancel", pop.popdown))

        def save():
            a.update(hour=int(hour.get_value()), minute=int(minute.get_value()),
                     repeat=[i for i, t in enumerate(toggles) if t.get_active()],
                     label=label.get_text().strip(), snooze=snooze_sw.get_active(),
                     sound=keys[sound_dd.get_selected()], enabled=True)
            pop.popdown()
            self.put(a)
        btns.append(ui.controls.push_button("Save", save, style="default"))
        box.append(btns)
        label.connect("activate", lambda _e: save())
        pop.set_child(box)
        pop.connect("closed", lambda p: (self.preview(None), GLib.idle_add(p.unparent)))
        pop.save, pop.hour, pop.minute, pop.days, pop.label = save, hour, minute, toggles, label   # (tests)
        pop.sound = sound_dd
        pop.popup()
        return pop


def _preview_player():
    from ..shell.alarmservice import Ringer
    return Ringer()


def open_windows(app, paths=()) -> None:
    """One Clock window; opening it again brings it forward."""
    win = next((w for w in app.get_windows() if isinstance(w, ClockWindow)), None)
    (win or ClockWindow(app)).present()


def clock_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Clock\n"
                              "Comment=Alarms that ring even with Do Not Disturb on\nIcon=sonata-clock\n"
                              "Categories=Utility;Clock;\nKeywords=alarm;clock;wake;timer;stopwatch;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} clock\n")
