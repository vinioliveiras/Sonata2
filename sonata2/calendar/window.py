"""Calendar (macOS Ventura Calendar): local calendars, one window.

Toolbar (continuing the compositor's glass title bar): calendar list
toggle, + (new event), Day / Week / Month / Year, search, and ‹ Today ›.
The translucent sidebar lists the calendars ("On My Mac": Home, Work...)
with coloured checkboxes that show / hide them -- right-click to add,
rename, recolour or delete one; the selected calendar receives new
events -- and a mini month to jump to a date.

Views: Month (6 x 7 grid, today's date in a red circle, all-day events
filled, timed ones with a dot and time, "N more"), Week and Day (hourly
timeline, overlapping events side by side, all-day strip, red current-time
line; opens scrolled to 8 AM) and Year (12 months). Double-click an empty
slot to create an event there, an event to edit it in a popover (title,
location, all-day, start / end, repeat, alert, calendar, notes); drag an
event to move it, its bottom edge to change its end. Delete removes the
selected event.

Keys (⌘ is Ctrl or Super): ⌘1..4 Day / Week / Month / Year, ⌘T today,
← / → previous / next, ⌘N new event, ⌘F search, ⌘Z undo, ⇧⌘Z redo,
Delete / Backspace delete, Esc deselects.

Storage: one iCalendar file per calendar in ~/.local/share/sonata2-data/calendar/
(ics.py). .ics files opened with Calendar or dropped on its window are
imported. While Calendar runs, alerts show as desktop notifications
(alerts.py)."""
import datetime as dt
import os

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import config, ui  # noqa: E402
from . import alerts, ics, model, views  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.calendar"
DEFAULTS = {"view": "week", "sidebar": True, "hidden": [], "default_calendar": "", "week_start": "locale"}
VIEWS = (("day", "Day"), ("week", "Week"), ("month", "Month"), ("year", "Year"))
REPEATS = ((None, "None"), ("DAILY", "Every Day"), ("WEEKLY", "Every Week"), ("MONTHLY", "Every Month"),
           ("YEARLY", "Every Year"))
ALERTS = ((None, "None"), (0, "At time of event"), (5, "5 minutes before"), (15, "15 minutes before"),
          (30, "30 minutes before"), (60, "1 hour before"), (1440, "1 day before"))
SIDEBAR_W = ui.window.SIDEBAR_W          # the apps' one sidebar width
RESULTS_W = 270


def _palette_css() -> str:
    """Checkbox and dot colours of the palette (tokens where they exist)."""
    out = []
    for cid, _name, ref in model.PALETTE:
        c = f"%({ref})s" if not ref.startswith("#") else ref
        out.append(f"checkbutton.cal-check.c-{cid} check {{ border-color: {c}; }}\n"
                   f"checkbutton.cal-check.c-{cid} check:checked {{ background-color: {c}; border-color: {c}; }}\n"
                   f".cal-dot.c-{cid} {{ background-color: {c}; }}\n")
    return "".join(out)


ui.register("""
window.sonata-calendar { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.cal-content { background: %(content_bg)s; }
.cal-sidebar { box-shadow: inset -1px 0 %(separator)s; }
.cal-sidebar list { background: none; padding: 0 8px; }
.cal-sidebar list row { min-height: 26px; padding: 0 6px; border-radius: %(r_menu)s; background: none;
  color: %(label)s; transition: background-color %(t_fast)s; }
.cal-sidebar list row:selected { background: %(sidebar_selected)s; color: %(label)s; }
.cal-sidebar list row:active { background: %(tool_hover)s; transition: background-color %(t_press)s; }
.cal-sidebar .cal-sec { font-size: %(text_small)s; font-weight: 700; color: %(label_tertiary)s;
  margin: 12px 16px 4px 16px; }
.cal-sidebar entry { min-height: 20px; padding: 0 4px; border-radius: %(r_menu_row)s; }
checkbutton.cal-check { padding: 0; margin-right: 4px; }
checkbutton.cal-check check { min-width: 12px; min-height: 12px; border-radius: %(r_menu_row)s;
  border: 1.5px solid; background: none; box-shadow: none; color: %(label_on_accent)s;
  transition: background-color %(t_fast)s; }
.cal-dot { min-width: 8px; min-height: 8px; border-radius: 99px; }
.cal-heading { padding: 12px 18px 8px 18px; background: %(content_bg)s; }
.cal-heading .cal-title { font-family: %(font_display)s; color: %(label)s; }
.cal-heading .cal-subtitle { color: %(label_secondary)s; font-size: %(text_title)s; }
.cal-sep { min-height: 1px; background: %(separator)s; }
.cal-seg { background: %(control_off)s; border-radius: %(r_label)s; padding: 2px; }
.cal-seg button { min-height: 20px; min-width: 52px; padding: 0 10px; border-radius: %(r_menu_row)s; border: none;
  background: none; box-shadow: none; color: %(label)s; font-weight: 500;
  transition: background-color %(t_fast)s; }
.cal-seg button:hover { background: %(tool_hover)s; }
.cal-seg button:checked { background: %(control_bg)s; box-shadow: %(shadow_control)s; }
.sonata-toolbar button.cal-today { padding: 0 8px; color: %(label)s; font-weight: 500; }
.sonata-toolbar button.tool:active { background: %(sidebar_selected)s; transition: background-color %(t_press)s; }
.sonata-toolbar entry.cal-search { min-height: 24px; border-radius: %(r_button)s; margin-right: 10px; }
.cal-mini { padding: 4px 12px 12px 12px; }
.cal-mini-head { padding: 0 2px 4px 6px; }
.cal-mini-title { font-weight: 700; font-size: %(text_body)s; }
button.cal-mini-nav { min-width: 20px; min-height: 20px; padding: 0; border: none; background: none;
  box-shadow: none; color: %(label_secondary)s; border-radius: %(r_menu_row)s; }
button.cal-mini-nav:hover { background: %(tool_hover)s; color: %(label)s; }
button.cal-mini-nav:active { background: %(sidebar_selected)s; }
.cal-results { background: %(content_bg)s; box-shadow: inset 1px 0 %(separator)s; }
.cal-results list { background: none; }
.cal-results list row { padding: 6px 12px; border-bottom: 1px solid %(separator)s; }
.cal-results list row:selected { background: %(accent)s; color: %(label_on_accent)s; }
.cal-results .cal-res-date { font-size: %(text_small)s; color: %(label_secondary)s; }
.cal-results list row:selected .cal-res-date { color: %(label_on_accent)s; }
.cal-results .cal-empty { color: %(label_tertiary)s; margin: 20px; }
.cal-editor { padding: 4px 6px 6px 6px; min-width: 300px; }
entry.cal-ed-title, entry.cal-ed-flat { background: none; border: none; box-shadow: none; outline: none;
  padding: 0 4px; min-height: 24px; color: %(label)s; }
entry.cal-ed-title { font-size: %(text_title)s; font-weight: 700; }
entry.cal-ed-flat:focus-within, entry.cal-ed-title:focus-within { background: %(control_off)s;
  border-radius: %(r_menu_row)s; }
.cal-ed-key { color: %(label_secondary)s; }
entry.cal-ed-time { min-height: 22px; padding: 0 6px; border-radius: %(r_button)s; border: none;
  background: %(control_bg)s; box-shadow: %(shadow_control)s; }
menubutton.cal-ed-date > button { min-height: 22px; padding: 0 8px; border-radius: %(r_button)s; border: none;
  background: %(control_bg)s; box-shadow: %(shadow_control)s; font-weight: 400; color: %(label)s; }
menubutton.cal-ed-date > button:active { background: %(control_pressed)s; }
textview.cal-ed-notes, textview.cal-ed-notes text { background: none; color: %(label)s; }
.cal-ed-placeholder { color: %(label_tertiary)s; }
""" + _palette_css(), key="calendar")


def parse_time(text: str):
    """'9', '9:30', '0930', '9.30 pm', '21:30' -> datetime.time or None."""
    import re
    t = text.strip().lower().replace(".", ":")
    m = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?\s*(am|pm|a|p)?", t)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ap:
        if not 1 <= h <= 12:
            return None
        h = h % 12 + (12 if ap.startswith("p") else 0)
    if h > 23 or mi > 59:
        return None
    return dt.time(h, mi)


class CalendarWindow(Gtk.ApplicationWindow):
    def __init__(self, app, folder: str = None):
        if not GLib.get_application_name():
            GLib.set_application_name("Calendar")
        super().__init__(application=app, title="Calendar", css_classes=["sonata-calendar"])
        ui.window.standard(self)
        ui.window.remember_size(self, "calendar", 1100, 720)  # its last size (never bigger than the display)
        self.cfg = config.load("calendar", DEFAULTS)
        ws = self.cfg["week_start"]
        self.first_weekday = ws if isinstance(ws, int) and 0 <= ws <= 6 else model.locale_first_weekday()
        topbar = config.load("topbar", {"clock_format": "%H:%M"})
        self.h24 = any(x in topbar["clock_format"] for x in ("%H", "%k", "%R", "%T"))
        from .. import userdata
        self.store = model.Store(folder or userdata.folder("calendar"))
        self.store.load()
        self.store.async_writes = True
        self.hidden = set(self.cfg["hidden"])
        self.date = dt.date.today()
        self.view = self.cfg["view"] if self.cfg["view"] in dict(VIEWS) else "week"
        self.selected = None             # key (uid, start) of the selected occurrence
        self._selected_occ = None
        self._cache = {}
        self.undo_stack, self.redo_stack = [], []
        self.editor = None

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self._toolbar())
        body = Gtk.Box(vexpand=True)
        self.sidebar = self._sidebar()
        self.sidebar_rev = Gtk.Revealer(child=self.sidebar, reveal_child=self.cfg["sidebar"],
                                        transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
                                        transition_duration=ui.tokens.ms(250))
        self.sidebar_rev.set_hexpand(False)
        body.append(self.sidebar_rev)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, css_classes=["cal-content"])
        content.append(self._heading())
        self.views = {"day": views.Timeline(self, 1), "week": views.Timeline(self, 7),
                      "month": views.MonthView(self), "year": views.YearView(self)}
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=ui.tokens.ms(150),
                               vexpand=True, hexpand=True)
        for vid, v in self.views.items():
            self.stack.add_named(v, vid)
        content.append(self.stack)
        body.append(content)
        self.results_rev = Gtk.Revealer(child=self._results(), reveal_child=False,
                                        transition_type=Gtk.RevealerTransitionType.SLIDE_LEFT,
                                        transition_duration=ui.tokens.ms(250))
        body.append(self.results_rev)
        col.append(body)
        self.set_child(col)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._dropped)
        self.add_controller(drop)

        self.alerts = alerts.Alerts(lambda: list(self.store.events.values()), self._notify)
        self.alerts.reschedule()
        self.connect("close-request", lambda *_: (self.alerts.stop(), False)[1])
        self.set_view(self.view)
        self.connect("map", lambda *_: GLib.idle_add(lambda: (self.set_focus(None), False)[1]))

    # -- building ---------------------------------------------------------------------------------
    def _toolbar(self):
        tb = ui.window.glass_toolbar(self, start=(
            ("sidebar-show-symbolic", "Show Calendar List", self.toggle_sidebar),
            ("list-add-symbolic", "New Event", self.new_event)))
        bar = tb.get_child()
        self.add_btn = bar.get_start_widget().get_last_child()
        seg = Gtk.Box(css_classes=["cal-seg"], valign=Gtk.Align.CENTER, homogeneous=True)
        self.view_buttons = {}
        first = None
        for vid, label in VIEWS:
            b = Gtk.ToggleButton(label=label, group=first, can_focus=False)
            b.connect("toggled", lambda b, v=vid: b.get_active() and self.view != v and self.set_view(v))
            first = first or b
            seg.append(b)
            self.view_buttons[vid] = b
        bar.set_center_widget(seg)
        end = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        self.search = Gtk.SearchEntry(placeholder_text="Search", width_chars=16, css_classes=["cal-search"],
                                      valign=Gtk.Align.CENTER)
        self.search.connect("search-changed", lambda *_: self._search_changed())
        self.search.connect("stop-search", lambda *_: self._close_search())
        end.append(self.search)
        for icon, label, tip, cb in (("go-previous-symbolic", None, "Previous", lambda: self.step(-1)),
                                     (None, "Today", "Today", self.today),
                                     ("go-next-symbolic", None, "Next", lambda: self.step(1))):
            b = Gtk.Button(tooltip_text=tip, can_focus=False, css_classes=["tool"] + (["cal-today"] if label else []))
            if icon:
                b.set_icon_name(icon)
            else:
                b.set_label(label)
            b.connect("clicked", lambda _b, f=cb: f())
            end.append(b)
        bar.set_end_widget(end)
        return tb

    def _heading(self):
        box = Gtk.Box(css_classes=["cal-heading"], spacing=10)
        self.title = Gtk.Label(xalign=0, css_classes=["cal-title"], valign=Gtk.Align.BASELINE)
        self.subtitle = Gtk.Label(xalign=0, css_classes=["cal-subtitle"], valign=Gtk.Align.BASELINE)
        box.append(self.title)
        box.append(self.subtitle)
        return box

    def _sidebar(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["sonata-sidebar", "cal-sidebar"])
        box.set_size_request(SIDEBAR_W, -1)
        box.append(Gtk.Label(label="On My Computer", xalign=0, css_classes=["cal-sec"]))
        self.cal_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.cal_list.connect("row-selected", self._calendar_selected)
        scroll = Gtk.ScrolledWindow(child=self.cal_list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        box.append(scroll)
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._sidebar_menu)
        scroll.add_controller(click)
        self.mini = views.MiniMonth(self)
        box.append(self.mini)
        self._fill_calendars()
        return box

    def _fill_calendars(self):
        self.cal_list.remove_all()
        default = self.default_calendar()
        for cal in self.store.calendars:
            row = Gtk.ListBoxRow(can_focus=False)
            row.cal_id = cal.id
            hb = Gtk.Box(spacing=4)
            chk = Gtk.CheckButton(active=cal.id not in self.hidden, css_classes=["cal-check", "c-" + cal.color],
                                  valign=Gtk.Align.CENTER, can_focus=False)
            chk.connect("toggled", lambda c, cid=cal.id: self.set_visible_calendar(cid, c.get_active()))
            hb.append(chk)
            stack = Gtk.Stack(hexpand=True)
            stack.add_named(Gtk.Label(label=cal.name, xalign=0, ellipsize=Pango.EllipsizeMode.END), "label")
            entry = Gtk.Entry(text=cal.name)
            entry.connect("activate", lambda e, cid=cal.id: self._rename_done(cid, e.get_text()))
            focus = Gtk.EventControllerFocus()
            focus.connect("leave", lambda _f, e=entry, cid=cal.id: self._rename_done(cid, e.get_text()))
            entry.add_controller(focus)
            stack.add_named(entry, "entry")
            hb.append(stack)
            row.set_child(hb)
            row.stack, row.entry = stack, entry
            self.cal_list.append(row)
            if cal.id == default:
                self.cal_list.select_row(row)

    def _results(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["cal-results"])
        box.set_size_request(RESULTS_W, -1)
        self.results = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.results.connect("row-activated", self._result_activated)
        self.results.set_activate_on_single_click(True)
        self.results.set_placeholder(Gtk.Label(label="No Results", css_classes=["cal-empty"]))
        box.append(Gtk.ScrolledWindow(child=self.results, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        return box

    # -- data -------------------------------------------------------------------------------------
    def occurrences(self, a: dt.datetime, b: dt.datetime):
        key = (a, b)
        if key not in self._cache:
            if len(self._cache) > 16:
                self._cache.clear()
            self._cache[key] = ics.expand(self.store.visible_events(self.hidden), a, b)
        return self._cache[key]

    def calendar_color(self, occ) -> str:
        cal = self.store.calendar(occ.event.calendar)
        return cal.color if cal else "blue"

    def default_calendar(self) -> str:
        ids = [c.id for c in self.store.calendars]
        if self.cfg["default_calendar"] in ids:
            return self.cfg["default_calendar"]
        visible = [i for i in ids if i not in self.hidden]
        return (visible or ids or [""])[0]

    def data_changed(self) -> None:
        self._cache.clear()
        if self._selected_occ is not None:
            ev = self.store.events.get(self._selected_occ.event.uid)
            if ev is None:
                self.selected = self._selected_occ = None
            else:
                self._selected_occ = ics.Occurrence(ev, self._selected_occ.start, self._selected_occ.end)
        self.refresh()
        self.alerts.reschedule()
        if self.results_rev.get_reveal_child():
            self._search_changed()

    def apply(self, pairs, record=True) -> None:
        """Apply [(before, after)] event changes (None = absent), undoably."""
        for before, after in pairs:
            if after is None:
                if before is not None:
                    self.store.remove(before.uid)
            else:
                self.store.put(after.copy())
        if record:
            self.undo_stack.append(pairs)
            del self.undo_stack[:-100]
            self.redo_stack.clear()
        self.data_changed()

    def undo(self) -> None:
        if self.undo_stack:
            pairs = self.undo_stack.pop()
            self.apply([(a, b) for b, a in reversed(pairs)], record=False)
            self.redo_stack.append(pairs)

    def redo(self) -> None:
        if self.redo_stack:
            pairs = self.redo_stack.pop()
            self.apply(pairs, record=False)
            self.undo_stack.append(pairs)

    # -- calendars ---------------------------------------------------------------------------------
    def set_visible_calendar(self, cal_id, visible) -> None:
        (self.hidden.discard if visible else self.hidden.add)(cal_id)
        config.update("calendar", hidden=sorted(self.hidden))
        self.data_changed()

    def _calendar_selected(self, _l, row):
        if row is not None and row.cal_id != self.cfg["default_calendar"]:
            self.cfg["default_calendar"] = row.cal_id
            config.update("calendar", default_calendar=row.cal_id)

    def _row_at(self, y):
        return self.cal_list.get_row_at_y(int(y))

    def _sidebar_menu(self, gesture, _n, x, y):
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
        Item = ui.menu.Item
        widget = gesture.get_widget()
        res = widget.translate_coordinates(self.cal_list, x, y)     # (x, y), or (ok, x, y) in older PyGObject
        row = self._row_at(res[-1]) if res and res[0] is not False else None
        sections = [[Item("New Calendar", self.new_calendar)]]
        if row is not None:
            cal = self.store.calendar(row.cal_id)
            colors = [Item(name, lambda _on=None, cid=cid, c=cal.id: self.recolor_calendar(c, cid), checked=cal.color == cid)
                      for cid, name, _r in model.PALETTE]
            sections = [[Item("Rename", lambda r=row: self.rename_calendar(r)),
                         Item("Color", submenu=[colors])],
                        [Item("Delete", lambda c=cal.id: self.delete_calendar(c),
                              enabled=len(self.store.calendars) > 1)]] + sections
        ui.menu.popup(widget, sections, at=(x, y), glass=True, passthrough=True)

    def new_calendar(self) -> None:
        cal = self.store.add_calendar("Untitled")
        self._fill_calendars()
        row = next((r for r in self._rows() if r.cal_id == cal.id), None)
        if row is not None:
            GLib.idle_add(lambda: (self.rename_calendar(row), False)[1])
        self.data_changed()

    def _rows(self):
        r = self.cal_list.get_first_child()
        while r is not None:
            if isinstance(r, Gtk.ListBoxRow):
                yield r
            r = r.get_next_sibling()

    def rename_calendar(self, row) -> None:
        row.entry.set_text(self.store.calendar(row.cal_id).name)
        row.stack.set_visible_child_name("entry")
        row.entry.grab_focus()

    def _rename_done(self, cal_id, name) -> None:
        row = next((r for r in self._rows() if r.cal_id == cal_id), None)
        if row is None or row.stack.get_visible_child_name() != "entry":
            return
        name = name.strip()
        cal = self.store.calendar(cal_id)
        if name and cal and name != cal.name:
            self.store.update_calendar(cal_id, name=name)
        GLib.idle_add(lambda: (self._fill_calendars(), False)[1])

    def recolor_calendar(self, cal_id, color) -> None:
        self.store.update_calendar(cal_id, color=color)
        self._fill_calendars()
        self.data_changed()

    def delete_calendar(self, cal_id) -> None:
        cal = self.store.calendar(cal_id)
        if cal is None or len(self.store.calendars) < 2:
            return
        n = sum(1 for e in self.store.events.values() if e.calendar == cal_id)

        def answer(rid):
            if rid == "delete":
                self.store.delete_calendar(cal_id)
                self.undo_stack = [p for p in self.undo_stack
                                   if not any((b or a).calendar == cal_id for b, a in p)]
                self.redo_stack.clear()
                self._fill_calendars()
                self.data_changed()
        ui.dialog.alert(f"Are you sure you want to delete the calendar “{cal.name}”?",
                        f"Its {n} event{'s' if n != 1 else ''} will be deleted too. This can't be undone." if n
                        else "This can't be undone.",
                        [("cancel", "Cancel", "default"), ("delete", "Delete", "destructive")], answer, parent=self)

    # -- navigation --------------------------------------------------------------------------------
    def set_view(self, view: str) -> None:
        self.view = view
        if not self.view_buttons[view].get_active():
            self.view_buttons[view].set_active(True)
        self.stack.set_visible_child_name(view)
        if self.cfg["view"] != view:
            self.cfg["view"] = view
            config.update("calendar", view=view)
        self.refresh()

    def go(self, date: dt.date, view: str = None) -> None:
        self.date = date
        if view and view != self.view:
            self.set_view(view)
        else:
            self.refresh()

    def step(self, n: int) -> None:
        d = self.date
        if self.view == "day":
            d += dt.timedelta(days=n)
        elif self.view == "week":
            d += dt.timedelta(days=7 * n)
        elif self.view == "month":
            y, m = divmod(d.month - 1 + n, 12)
            d = dt.date(d.year + y, m + 1, 1)
        else:
            d = dt.date(d.year + n, 1, 1)
        self.go(d)

    def today(self) -> None:
        self.go(dt.date.today())
        if self.view in ("day", "week"):
            now = dt.datetime.now()
            self.views[self.view].scroll_to_hour(max(0, now.hour - 2))

    def toggle_sidebar(self) -> None:
        show = not self.sidebar_rev.get_reveal_child()
        self.sidebar_rev.set_reveal_child(show)
        config.update("calendar", sidebar=show)

    def refresh(self) -> None:
        d = self.date
        size = ui.px("text_title") * 1.75
        esc = GLib.markup_escape_text
        if self.view == "day":
            title = f"<b>{d.day} {esc(d.strftime('%B'))}</b> {d.year}"
            self.subtitle.set_label(d.strftime("%A"))
        elif self.view == "year":
            title = f"<b>{d.year}</b>"
            self.subtitle.set_label("")
        else:
            if self.view == "week":
                first = model.week_start(d, self.first_weekday)
                d = first + dt.timedelta(days=3)          # the month most of the week is in
            title = f"<b>{esc(d.strftime('%B'))}</b> {d.year}"
            self.subtitle.set_label("")
        self.title.set_markup(f"<span size='{int(size * Pango.SCALE)}'>{title}</span>")
        self.subtitle.set_visible(bool(self.subtitle.get_label()))
        self.views[self.view].refresh()
        self.mini.follow()

    # -- selection and editing ---------------------------------------------------------------------
    def select(self, occ) -> None:
        self._selected_occ = occ
        self.selected = occ.key if occ is not None else None
        if isinstance(self.get_focus(), Gtk.Text):
            self.set_focus(None)                 # keys (Delete, arrows) now go to the calendar
        self._redraw()

    def _redraw(self):
        v = self.views[self.view]
        if isinstance(v, views.Timeline):
            v.head.queue_draw()
            v.grid.queue_draw()
        else:
            v.queue_draw()

    def create(self, start, end, all_day, widget=None, rect=None) -> None:
        cal = self.default_calendar()
        if not cal:
            return
        if cal in self.hidden:
            self.set_visible_calendar(cal, True)
            self._fill_calendars()
        ev = ics.Event(uid=ics.new_uid(), summary="New Event", start=start, end=end, all_day=all_day, calendar=cal)
        self.apply([(None, ev)])
        occ = ics.Occurrence(self.store.events[ev.uid], start, end)
        self.select(occ)
        if widget is not None:
            self.edit(occ, widget, rect, new=True)

    def new_event(self) -> None:
        """⌘N / +: a one-hour event on the day shown, at the next hour
        (today) or 9 AM, edited from the + button."""
        now = dt.datetime.now()
        if self.date == now.date():
            start = now.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
            if start.date() != now.date():
                start = now.replace(minute=0, second=0, microsecond=0)
        else:
            start = model.day_start(self.date) + dt.timedelta(hours=9)
        if self.view == "year":
            self.set_view("day")
        self.create(start, start + dt.timedelta(hours=1), False, self.add_btn, None)

    def edit(self, occ, widget, rect=None, new=False) -> None:
        if self.editor is not None:
            self.editor.pop.popdown()
        self.editor = Editor(self, occ, widget, rect, new)

    def move(self, occ, dstart: dt.timedelta, dend: dt.timedelta) -> None:
        """Drag result: the event (the whole series, for a repeating one)
        moves by dstart / its end by dend."""
        ev = self.store.events.get(occ.event.uid)
        if ev is None:
            return
        new = ev.copy()
        new.start, new.end = ev.start + dstart, ev.end + dend
        if new.end < new.start:
            new.end = new.start
        if new.exdates and dstart:
            new.exdates = [d + dstart for d in new.exdates]
        if dstart:                         # changed instances move with their series
            for o in new.overrides:
                o.recurrence_id += dstart
                o.start, o.end = o.start + dstart, o.end + dstart
        self.apply([(ev.copy(), new)])
        moved = ics.Occurrence(self.store.events[ev.uid], occ.start + dstart, occ.end + dend)
        self.select(moved)

    def delete_selected(self) -> None:
        occ = self._selected_occ
        if occ is None:
            return
        ev = self.store.events.get(occ.event.uid)
        if ev is None:
            return
        if not ev.freq:
            self.apply([(ev.copy(), None)])
            self.select(None)
            return

        def answer(rid):
            if rid == "all":
                self.apply([(ev.copy(), None)])
            elif rid == "this":
                new = ev.copy()
                # a changed instance (also when the selection was rebound to the series):
                # drop it and exclude its slot
                rec = occ.event.recurrence_id or next(
                    (o.recurrence_id for o in ev.overrides if o.start == occ.start), None)
                if rec is not None:
                    new.overrides = [o for o in new.overrides if o.recurrence_id != rec]
                new.exdates.append(occ.start if rec is None else rec)
                self.apply([(ev.copy(), new)])
            elif rid == "future":
                new = ev.copy()
                new.until, new.count = occ.start - dt.timedelta(seconds=1), None
                self.apply([(ev.copy(), new if occ.start > ev.start else None)])
            if rid != "cancel":
                self.select(None)
        ui.dialog.alert(f"You're deleting an event “{ev.summary}”.",
                        "Do you want to delete this and all future occurrences of this event, or only the "
                        "selected occurrence?",
                        [("cancel", "Cancel", ""), ("future", "Delete All Future", "destructive"),
                         ("this", "Delete Only This Event", "default")], answer, parent=self)

    def duplicate(self, occ) -> None:
        ev = self.store.events.get(occ.event.uid)
        if ev is None:
            return
        new = ev.copy()
        new.uid = ics.new_uid()
        self.apply([(None, new)])

    def event_menu(self, occ, widget, x, y) -> None:
        Item = ui.menu.Item
        ev = self.store.events.get(occ.event.uid)
        if ev is None:
            return
        cals = [Item(c.name, lambda _on=None, cid=c.id: self._move_to_calendar(occ, cid), checked=c.id == ev.calendar)
                for c in self.store.calendars]
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        ui.menu.popup(widget, [
            [Item("Get Info", lambda: self.edit(occ, widget, r))],
            [Item("Duplicate", lambda: self.duplicate(occ)), Item("Delete", self.delete_selected)],
            [Item("Calendar", submenu=[cals])],
        ], at=(x, y), glass=True, passthrough=True)

    def _move_to_calendar(self, occ, cal_id) -> None:
        ev = self.store.events.get(occ.event.uid)
        if ev is not None and ev.calendar != cal_id:
            new = ev.copy()
            new.calendar = cal_id
            self.apply([(ev.copy(), new)])

    def empty_menu(self, widget, x, y, start, all_day) -> None:
        Item = ui.menu.Item
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), 1, 1
        end = start + (dt.timedelta(days=1) if all_day else dt.timedelta(hours=1))
        ui.menu.popup(widget, [[Item("New Event", lambda: self.create(start, end, all_day, widget, r))],
                               [Item("Go to Today", self.today)]], at=(x, y), glass=True, passthrough=True)

    # -- search -----------------------------------------------------------------------------------
    def _search_changed(self) -> None:
        q = self.search.get_text().strip().lower()
        self.results_rev.set_reveal_child(bool(q))
        self.results.remove_all()
        if not q:
            return
        now = dt.datetime.now()
        found = []
        for ev in self.store.visible_events(self.hidden):
            if q in ev.summary.lower() or q in ev.location.lower() or q in ev.description.lower():
                when = ev.start
                if ev.freq:
                    nxt = next(ics.occurrences(ev, now, now + dt.timedelta(days=800)), None)
                    when = nxt.start if nxt else ev.start
                found.append((when, ev))
        found.sort(key=lambda x: x[0])
        for when, ev in found[:200]:
            row = Gtk.ListBoxRow()
            row.target = (when, ev)
            hb = Gtk.Box(spacing=8)
            cal = self.store.calendar(ev.calendar)
            hb.append(Gtk.Box(css_classes=["cal-dot", "c-" + (cal.color if cal else "blue")], valign=Gtk.Align.CENTER))
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            vb.append(Gtk.Label(label=ev.summary or "New Event", xalign=0, ellipsize=Pango.EllipsizeMode.END))
            date = f"{when:%a} {when.day} {when:%b %Y}" + ("" if ev.all_day else "  " + self.fmt_time(when.time()))
            vb.append(Gtk.Label(label=date + ("  ↻" if ev.freq else ""), xalign=0, css_classes=["cal-res-date"]))
            hb.append(vb)
            row.set_child(hb)
            self.results.append(row)

    def _result_activated(self, _l, row) -> None:
        when, ev = row.target
        self.go(when.date(), "day" if self.view in ("year", "month") else self.view)
        self.select(ics.Occurrence(ev, when, when + ev.duration))
        if not ev.all_day and self.view in ("day", "week"):
            self.views[self.view].scroll_to_hour(max(0, when.hour - 1))

    def _close_search(self) -> None:
        self.search.set_text("")
        self.results_rev.set_reveal_child(False)
        self.set_focus(None)

    # -- import -----------------------------------------------------------------------------------
    def _dropped(self, _t, value, _x, _y) -> bool:
        files = [f for f in value.get_files() if (f.get_basename() or "").lower().endswith((".ics", ".ical", ".ifb"))]
        if not files:
            return False
        self.import_files(files)
        return True

    def import_files(self, files) -> None:
        def loaded(f, res):                # read off the main loop
            try:
                _ok, data, _e = f.load_contents_finish(res)
            except GLib.Error as e:
                ui.dialog.alert(f"“{f.get_basename()}” couldn't be imported.", e.message,
                                [("ok", "OK", "default")], parent=self)
                return
            self._ask_import(f, bytes(data).decode("utf-8", errors="replace"))
        for f in files:
            f.load_contents_async(None, loaded)

    def _ask_import(self, f, text) -> None:
        info, events = ics.parse(text)
        name = f.get_basename()
        if not events:
            ui.dialog.alert(f"“{name}” has no events.", "There is nothing to import.",
                            [("ok", "OK", "default")], parent=self)
            return
        target = self.store.calendar(self.default_calendar())

        def answer(rid):
            if rid == "cancel":
                return
            if rid == "new":
                stem = os.path.splitext(name)[0]
                color = info.get("color") if info.get("color") in model.PALETTE_IDS else \
                    model.color_from_hex(info["hex"]) if info.get("hex") else None
                cal = self.store.add_calendar(info.get("name") or stem, color)
            else:
                cal = target
            self.store.import_text(text, cal.id)
            self.hidden.discard(cal.id)
            self._fill_calendars()
            first = min(events, key=lambda e: e.start)
            self.go(first.start.date())
            self.data_changed()
        n = len(events)
        responses = [("cancel", "Cancel", ""), ("new", "New Calendar", "")]
        if target is not None:
            responses.append(("add", f"Add to “{target.name}”", "default"))
        ui.dialog.alert(f"Import {n} event{'s' if n != 1 else ''} from “{name}”?",
                        "Add them to a calendar you have or to a new calendar.", responses, answer, parent=self)

    # -- alerts -----------------------------------------------------------------------------------
    def _notify(self, occ) -> None:
        ev = occ.event
        if ev.all_day:
            body = "All day"
        else:
            body = f"{self.fmt_time(occ.start.time())} – {self.fmt_time(occ.end.time())}"
        if ev.location:
            body += "\n" + ev.location
        alerts.send_notification(ev.summary or "New Event", body)

    # -- formats ----------------------------------------------------------------------------------
    def fmt_time(self, t: dt.time) -> str:
        if self.h24:
            return f"{t.hour:02d}:{t.minute:02d}"
        h = t.hour % 12 or 12
        ap = "AM" if t.hour < 12 else "PM"
        return f"{h} {ap}" if t.minute == 0 else f"{h}:{t.minute:02d} {ap}"

    def fmt_hour(self, hour: int) -> str:
        if self.h24:
            return f"{hour:02d}:00"
        if hour == 12:
            return "Noon"
        return f"{hour % 12 or 12} {'AM' if hour < 12 else 'PM'}"

    # -- keys -------------------------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state) -> bool:
        cmd = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        shift = state & Gdk.ModifierType.SHIFT_MASK
        k = Gdk.keyval_to_lower(keyval)
        if cmd:
            act = {Gdk.KEY_1: lambda: self.set_view("day"), Gdk.KEY_2: lambda: self.set_view("week"),
                   Gdk.KEY_3: lambda: self.set_view("month"), Gdk.KEY_4: lambda: self.set_view("year"),
                   Gdk.KEY_KP_1: lambda: self.set_view("day"), Gdk.KEY_KP_2: lambda: self.set_view("week"),
                   Gdk.KEY_KP_3: lambda: self.set_view("month"), Gdk.KEY_KP_4: lambda: self.set_view("year"),
                   Gdk.KEY_t: self.today, Gdk.KEY_n: self.new_event, Gdk.KEY_w: self.close,
                   Gdk.KEY_f: self.search.grab_focus, Gdk.KEY_y: self.redo,
                   Gdk.KEY_z: self.redo if shift else self.undo,
                   Gdk.KEY_d: lambda: self._selected_occ and self.duplicate(self._selected_occ),
                   Gdk.KEY_Left: lambda: self.step(-1), Gdk.KEY_Right: lambda: self.step(1)}.get(k)
            if k == Gdk.KEY_z and isinstance(self.get_focus(), Gtk.Text):
                return False                     # the search field's own undo
        else:
            act = {Gdk.KEY_Left: lambda: self.step(-1), Gdk.KEY_Right: lambda: self.step(1),
                   Gdk.KEY_Delete: self.delete_selected, Gdk.KEY_BackSpace: self.delete_selected,
                   Gdk.KEY_KP_Delete: self.delete_selected,
                   Gdk.KEY_Escape: lambda: self.select(None)}.get(keyval)
            if isinstance(self.get_focus(), (Gtk.Text, Gtk.TextView)):
                return False
        if act is None:
            return False
        act()
        return True


class Editor:
    """The event popover (macOS: title, location, all-day, starts / ends,
    repeat, alert, calendar, notes). Changes apply when it closes, as one
    undoable step; for a repeating event, the whole series follows the
    change of the occurrence shown."""

    def __init__(self, win, occ, widget, rect, new):
        self.win, self.occ, self.new = win, occ, new
        self.master = win.store.events[occ.event.uid].copy()
        ev = self.master
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["cal-editor"])
        self.title = Gtk.Entry(text=ev.summary, placeholder_text="New Event", css_classes=["cal-ed-title"])
        self.title.connect("activate", lambda *_: self.pop.popdown())
        self.location = Gtk.Entry(text=ev.location, placeholder_text="Add Location", css_classes=["cal-ed-flat"])
        self.location.connect("activate", lambda *_: self.pop.popdown())
        box.append(self.title)
        box.append(self.location)
        box.append(ui.panel.separator())
        grid = Gtk.Grid(row_spacing=6, column_spacing=8, margin_start=4, margin_end=4)
        self.all_day = ui.controls.switch(ev.all_day, lambda on: self._all_day(on))
        self.all_day.set_halign(Gtk.Align.START)
        start, end = occ.start, occ.end
        end_shown = end - dt.timedelta(days=1) if ev.all_day else end
        self.s_date, self.s_time = self._date_button(start.date()), self._time_entry(start.time())
        self.e_date, self.e_time = self._date_button(max(start.date(), end_shown.date())), self._time_entry(end.time())
        self.repeat = ui.controls.popup_button([t for _v, t in REPEATS], [v for v, _t in REPEATS].index(ev.freq))
        alert_vals = [v for v, _t in ALERTS]
        alert_names = [t for _v, t in ALERTS]
        if ev.alarm not in alert_vals:
            alert_vals.append(ev.alarm)
            alert_names.append(f"{ev.alarm} minutes before")
        self.alert_vals = alert_vals
        self.alert = ui.controls.popup_button(alert_names, alert_vals.index(ev.alarm))
        self.cal_ids = [c.id for c in win.store.calendars]
        self.calendar = ui.controls.popup_button([c.name for c in win.store.calendars],
                                                 max(0, self.cal_ids.index(ev.calendar))
                                                 if ev.calendar in self.cal_ids else 0)
        rows = (("all-day:", self.all_day), ("starts:", self._pair(self.s_date, self.s_time)),
                ("ends:", self._pair(self.e_date, self.e_time)), ("repeat:", self.repeat),
                ("alert:", self.alert), ("calendar:", self.calendar))
        for i, (key, w) in enumerate(rows):
            grid.attach(Gtk.Label(label=key, xalign=1, css_classes=["cal-ed-key"]), 0, i, 1, 1)
            w.set_halign(Gtk.Align.START)
            grid.attach(w, 1, i, 1, 1)
        box.append(grid)
        box.append(ui.panel.separator())
        self.notes = Gtk.TextView(css_classes=["cal-ed-notes"], wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=4,
                                  right_margin=4, top_margin=2, bottom_margin=2)
        self.notes.get_buffer().set_text(ev.description)
        over = Gtk.Overlay(child=self.notes)
        self.placeholder = Gtk.Label(label="Add Notes", xalign=0, valign=Gtk.Align.START, can_target=False,
                                     css_classes=["cal-ed-placeholder"], margin_start=4, margin_top=2)
        over.add_overlay(self.placeholder)
        self.notes.get_buffer().connect("changed", lambda b: self.placeholder.set_visible(b.get_char_count() == 0))
        self.placeholder.set_visible(not ev.description)
        scroll = Gtk.ScrolledWindow(child=over, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True,
                                    max_content_height=120)
        scroll.set_size_request(-1, 48)
        box.append(scroll)
        self._all_day(ev.all_day, init=True)
        scroll = widget.get_ancestor(Gtk.ScrolledWindow)
        if scroll is not None:                     # point at the part of the event on screen
            adj = scroll.get_vadjustment()
            top, bottom = adj.get_value(), adj.get_value() + adj.get_page_size()
            if rect is None or rect.y + rect.height < top or rect.y > bottom:
                widget, rect = win.add_btn, None      # scrolled away: hang from the + button
            else:
                y0, y1 = max(rect.y, int(top)), min(rect.y + rect.height, int(bottom))
                rect.y, rect.height = y0, max(1, y1 - y0)
        self.pop = ui.panel.popup(widget, box, position=Gtk.PositionType.RIGHT if rect else Gtk.PositionType.BOTTOM)
        if rect is not None:
            self.pop.set_pointing_to(rect)
        self.pop.connect("closed", lambda *_: self.commit())
        self.title.grab_focus()
        if new:
            self.title.select_region(0, -1)

    def _pair(self, a, b):
        hb = Gtk.Box(spacing=6)
        hb.append(a)
        hb.append(b)
        return hb

    def _date_button(self, d: dt.date):
        btn = Gtk.MenuButton(css_classes=["cal-ed-date"])
        btn.label = Gtk.Label(label=self._date_label(d))
        btn.set_child(btn.label)                 # a plain field, no arrow
        btn.date = d
        cal = Gtk.Calendar()
        when = GLib.DateTime.new_local(d.year, d.month, d.day, 0, 0, 0)
        (cal.set_date if hasattr(cal, "set_date") else cal.select_day)(when)      # GTK 4.20 renamed it
        pop = Gtk.Popover(child=cal, css_classes=["sonata-panel"], has_arrow=False)

        def picked(c):
            g = c.get_date()
            btn.date = dt.date(g.get_year(), g.get_month(), g.get_day_of_month())
            btn.label.set_label(self._date_label(btn.date))
            pop.popdown()
            self._dates_changed(btn)
        cal.connect("day-selected", picked)
        btn.set_popover(pop)
        return btn

    @staticmethod
    def _date_label(d: dt.date) -> str:
        return d.strftime("%x")

    def _time_entry(self, t: dt.time):
        e = Gtk.Entry(text=self.win.fmt_time(t), width_chars=8, max_width_chars=8, css_classes=["cal-ed-time"])
        e.time = t

        def done(*_a):
            v = parse_time(e.get_text())
            if v is not None:
                e.time = v
            e.set_text(self.win.fmt_time(e.time))
            self._dates_changed(e)
        e.connect("activate", done)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", done)
        e.add_controller(focus)
        return e

    def _dates_changed(self, source):
        """Changing the start keeps the duration (macOS); the end can't go
        before the start."""
        s, e = self._range()
        if source in (self.s_date, self.s_time):
            dur = self._dur
            ne = s + dur
            self.e_date.date = (ne - dt.timedelta(days=1)).date() if self.all_day.get_active() else ne.date()
            self.e_date.label.set_label(self._date_label(self.e_date.date))
            self.e_time.time = ne.time()
            self.e_time.set_text(self.win.fmt_time(ne.time()))
        elif e < s:
            self.e_date.date, self.e_time.time = s.date(), s.time()
            self.e_date.label.set_label(self._date_label(s.date()))
            self.e_time.set_text(self.win.fmt_time(s.time()))
        s, e = self._range()
        self._dur = e - s

    def _range(self):
        if self.all_day.get_active():
            s = model.day_start(self.s_date.date)
            e = model.day_start(self.e_date.date) + dt.timedelta(days=1)
        else:
            s = dt.datetime.combine(self.s_date.date, self.s_time.time)
            e = dt.datetime.combine(self.e_date.date, self.e_time.time)
        return s, max(s, e)

    def _all_day(self, on, init=False):
        self.s_time.set_visible(not on)
        self.e_time.set_visible(not on)
        if not init and not on and self.s_time.time == dt.time(0) and self.e_time.time == dt.time(0):
            self.s_time.time, self.e_time.time = dt.time(9), dt.time(10)
            self.e_date.date = self.s_date.date
            self.e_date.label.set_label(self._date_label(self.e_date.date))
            for w in (self.s_time, self.e_time):
                w.set_text(self.win.fmt_time(w.time))
        s, e = self._range()
        self._dur = e - s

    def commit(self) -> None:
        win = self.win
        if win.editor is self:
            win.editor = None
        for entry in (self.s_time, self.e_time):          # a time typed without Enter
            v = parse_time(entry.get_text())
            if v is not None:
                entry.time = v
        cur = win.store.events.get(self.master.uid)
        if cur is None:
            return
        new = cur.copy()
        new.summary = self.title.get_text().strip() or "New Event"
        new.location = self.location.get_text().strip()
        buf = self.notes.get_buffer()
        new.description = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        new.all_day = self.all_day.get_active()
        s, e = self._range()
        new.start = cur.start + (s - self.occ.start)
        new.end = new.start + (e - s)
        if new.exdates and s != self.occ.start:
            new.exdates = [d + (s - self.occ.start) for d in new.exdates]
        freq = REPEATS[self.repeat.get_selected()][0]
        if freq != cur.freq:
            new.freq, new.interval, new.until, new.count = freq, 1, None, None
            if not freq:
                new.exdates = []
        new.alarm = self.alert_vals[self.alert.get_selected()]
        new.calendar = self.cal_ids[self.calendar.get_selected()] if self.cal_ids else cur.calendar
        if new != cur:
            if self.new and win.undo_stack and win.undo_stack[-1] == [(None, self.master)]:
                win.undo_stack.pop()                  # creating + first edit: one undo step
                win.apply([(None, new)])
            else:
                win.apply([(cur.copy(), new)])
            win.select(ics.Occurrence(win.store.events[new.uid], s, e))


def open_windows(app, paths) -> None:
    """One Calendar window; .ics files given are imported into it."""
    win = next((w for w in app.get_windows() if isinstance(w, CalendarWindow)), None)
    if win is None:
        win = CalendarWindow(app)
    win.present()
    # "sonata-date:2026-10-01" (the menu bar's Calendar widget): that day
    for p in paths or []:
        if p.startswith("sonata-date:"):
            try:
                win.go(dt.date.fromisoformat(p[len("sonata-date:"):]), "day")
            except ValueError:
                pass
    paths = [p for p in paths or [] if not p.startswith("sonata-date:")]
    files = [Gio.File.new_for_commandline_arg(p) for p in paths]
    if files:
        GLib.timeout_add(200, lambda: (win.import_files(files), False)[1])


def calendar_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Calendar\n"
                              "Comment=Keep track of your events\n"
                              "Icon=x-office-calendar\nCategories=Office;Calendar;\n"
                              "MimeType=text/calendar;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} calendar %F\n")
