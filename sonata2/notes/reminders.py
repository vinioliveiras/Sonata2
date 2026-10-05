"""Reminders inside Notes (macOS Reminders): the list view, the ⓘ detail
panel and the due-time alerts.

The view shows a smart list (Today: due today or overdue, Scheduled: every
dated reminder grouped by day, All: every open one grouped by list,
Completed) or a user list, under its name in the list's colour. A row:
round check (ticking it strikes the title through, then the row leaves
the list), the title (edit in place; Return adds the next reminder),
notes and due date (red when overdue), priority (!, !!, !!!) and flag.
The ⓘ button (or a click on the date) opens the detail panel: notes, a
date (calendar) and time (spin buttons), flag, priority, list.

Alerts: while Sonata runs, a reminder reaching its due time (all-day ones
at 9:00) is announced through org.freedesktop.Notifications. One timer
aims at the next due time (re-aimed on every change, at most 5 minutes
ahead so a suspend can't make it late); nothing polls."""
import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from ..ui import tokens  # noqa: E402
from . import store as S  # noqa: E402

SMART_INFO = {  # key: (name, icon, colour)
    "today": ("Today", "calendar-symbolic", "blue"),
    "scheduled": ("Scheduled", "alarm-symbolic", "red"),
    "all": ("All", "inbox-symbolic", "graphite"),
    "completed": ("Completed", "object-select-symbolic", "gray"),
}
PRIORITIES = ("None", "Low", "Medium", "High")

_colors = "".join(
    f".c-{c} {{ color: %(sys_{c})s; }}\n.bg-{c} {{ background: %(sys_{c})s; }}\n"
    f"checkbutton.rem-check.c-{c} check:checked {{ background: %(sys_{c})s; border-color: %(sys_{c})s; }}\n"
    for c in tokens.SYSTEM_COLORS)
ui.register(_colors + """
.nt-rem { background: %(content_bg)s; }
.nt-rem-head { padding: 14px 24px 6px 24px; }
.nt-rem-title { font-family: %(font_display)s; font-size: calc(%(text_title)s * 1.75); font-weight: 700; }
.nt-rem-count { font-family: %(font_display)s; font-size: calc(%(text_title)s * 1.75); font-weight: 600; }
.nt-rem list { background: none; padding: 0 12px 20px 12px; }
.nt-rem list row { padding: 0; border-radius: %(r_menu)s; background: none;
  transition: background-color %(t_fast)s; }
.nt-rem list row:selected { background: %(item_selected_bg)s; }
.nt-rem-row { padding: 7px 8px 7px 10px; }
.nt-rem-row-body { box-shadow: inset 0 -1px %(separator)s; padding-bottom: 7px; }
.nt-rem-section { padding: 16px 10px 4px 10px; font-weight: 700; font-size: %(text_title)s; color: %(label)s;
  box-shadow: inset 0 -1px %(separator)s; margin: 0 12px; }
text.rem-title { font-size: %(text_body)s; color: %(label)s; background: none; caret-color: %(accent)s; }
.nt-rem-row.done text.rem-title { color: %(label_secondary)s; }
.rem-sub { font-size: %(text_small)s; color: %(label_secondary)s; }
.rem-sub.overdue { color: %(destructive)s; }
.rem-prio { font-weight: 700; }
checkbutton.rem-check { padding: 0; margin: 1px 10px 0 0; }
checkbutton.rem-check check { min-width: 18px; min-height: 18px; border-radius: 99px; margin: 0; padding: 0;
  background: none; border: 1.5px solid %(label_tertiary)s; box-shadow: none; -gtk-icon-source: none;
  transition: background-color %(t_fast)s, border-color %(t_fast)s; }
checkbutton.rem-check check:checked { box-shadow: inset 0 0 0 3px %(content_bg)s; }
checkbutton.rem-check:active check { filter: brightness(0.85); transition: filter %(t_press)s; }
button.rem-info { min-width: 22px; min-height: 22px; padding: 0; border-radius: 99px; border: none;
  background: none; box-shadow: none; color: %(accent)s; text-shadow: %(accent_halo)s; -gtk-icon-shadow: %(accent_halo)s; opacity: 0; transition: opacity %(t_fast)s; }
.nt-rem list row:hover button.rem-info, .nt-rem list row:selected button.rem-info { opacity: 1; }
.nt-rem-empty { color: %(label_tertiary)s; font-size: %(text_title)s; }
.rem-detail { padding: 6px 4px; }
.rem-detail entry { min-height: 24px; }
.rem-detail spinbutton { min-height: 24px; }
.rem-detail .rem-detail-title { font-weight: 700; font-size: %(text_title)s; }
""", key="notes-reminders")


# -- formatting --------------------------------------------------------------------------------------
def due_text(r: dict, today: datetime.date = None) -> str:
    """"Today, 14:00", "Tomorrow", "Yesterday", "12/10/2026, 09:30"."""
    dt = S.due_datetime(r)
    if dt is None:
        return ""
    today = today or datetime.date.today()
    delta = (dt.date() - today).days
    day = {0: "Today", 1: "Tomorrow", -1: "Yesterday"}.get(delta)
    if day is None:
        day = GLib.DateTime.new_local(dt.year, dt.month, dt.day, 0, 0, 0).format("%x")
    return f"{day}, {dt:%H:%M}" if S.has_time(r) else day


def is_overdue(r: dict, now: datetime.datetime = None) -> bool:
    dt = S.due_datetime(r)
    if dt is None or r.get("completed"):
        return False
    now = now or datetime.datetime.now()
    return dt < now if S.has_time(r) else dt.date() < now.date()


def day_heading(d: datetime.date, today: datetime.date = None) -> str:
    today = today or datetime.date.today()
    if d < today:
        return "Overdue"
    delta = (d - today).days
    if delta in (0, 1):
        return ("Today", "Tomorrow")[delta]
    return GLib.DateTime.new_local(d.year, d.month, d.day, 0, 0, 0).format("%A, %-d %B")


# -- alerts -------------------------------------------------------------------------------------------
class Notifier:
    """Desktop notifications for reminders falling due while Sonata runs."""

    def __init__(self, store):
        self.store = store
        self.last = datetime.datetime.now()
        self.timer = 0
        self.sent = []                     # (title, body): for tests and the log
        self.schedule()

    def schedule(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        nxt = self.store.next_due_after(self.last)
        if nxt is None:
            return
        secs = (nxt - datetime.datetime.now()).total_seconds()
        self.timer = GLib.timeout_add_seconds(int(max(1, min(300, secs + 0.5))), self._fire)

    def _fire(self) -> bool:
        self.timer = 0
        self.check()
        return False

    def check(self, now: datetime.datetime = None) -> list:
        now = now or datetime.datetime.now()
        due = self.store.due_between(self.last, now)
        self.last = now
        for r in due:
            lst = self.store.rlist(r.get("list"))
            body = r.get("notes") or (lst["name"] if lst else "")
            self.notify(r.get("title") or "New Reminder", body)
        self.schedule()
        return due

    def notify(self, title: str, body: str) -> None:
        self.sent.append((title, body))

        def got_bus(_src, res):
            try:
                bus = Gio.bus_get_finish(res)
            except GLib.Error:
                return
            bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                     "org.freedesktop.Notifications", "Notify",
                     GLib.Variant("(susssasa{sv}i)", ("Reminders", 0, "x-office-calendar", title, body, [],
                                                      {"urgency": GLib.Variant("y", 1)}, -1)),
                     None, Gio.DBusCallFlags.NONE, -1, None, lambda b, r: _finish(b, r))
        Gio.bus_get(Gio.BusType.SESSION, None, got_bus)

    def stop(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0


def _finish(bus, res) -> None:
    try:
        bus.call_finish(res)
    except GLib.Error as e:
        print(f"sonata2 notes: notification failed: {e.message}")


# -- rows ---------------------------------------------------------------------------------------------
class ReminderRow(Gtk.ListBoxRow):
    def __init__(self, view, r: dict):
        super().__init__(activatable=False)
        self.view, self.r = view, r
        lst = view.store.rlist(r.get("list")) or {"color": "blue"}
        self.color = lst.get("color", "blue")
        self.revealer = Gtk.Revealer(reveal_child=True, transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                     transition_duration=200)
        box = Gtk.Box(css_classes=["nt-rem-row"])
        self.check = Gtk.CheckButton(css_classes=["rem-check", f"c-{self.color}"], valign=Gtk.Align.START,
                                     active=bool(r.get("completed")), can_focus=False)
        self.check.connect("toggled", self._toggled)
        box.append(self.check)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True,
                       css_classes=["nt-rem-row-body"])
        line = Gtk.Box(spacing=4)
        self.prio = Gtk.Label(css_classes=["rem-prio", f"c-{self.color}"], visible=False)
        line.append(self.prio)
        self.title = Gtk.Text(css_classes=["rem-title"], hexpand=True, placeholder_text="New Reminder")
        self.title.get_buffer().set_text(r.get("title", ""), -1)
        self.title.connect("activate", lambda *_: self._commit_title(then_new=True))
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self._commit_title(drop_empty=True))
        self.title.add_controller(focus)
        self.strike = Gtk.DrawingArea(can_target=False)
        self.strike.set_draw_func(self._draw_strike)
        self.progress = 1.0 if r.get("completed") else 0.0
        over = Gtk.Overlay(child=self.title, hexpand=True)
        over.add_overlay(self.strike)
        line.append(over)
        self.flag = Gtk.Image(icon_name="flag-filled-symbolic", css_classes=["c-orange"], visible=False)
        line.append(self.flag)
        info = Gtk.Button(icon_name="info-symbolic", css_classes=["rem-info"], tooltip_text="Show Details",
                          can_focus=False, valign=Gtk.Align.CENTER)
        info.connect("clicked", lambda b: detail_panel(b, view, r))
        line.append(info)
        body.append(line)
        self.notes = Gtk.Label(css_classes=["rem-sub"], xalign=0, ellipsize=Pango.EllipsizeMode.END, visible=False)
        body.append(self.notes)
        self.due = Gtk.Label(css_classes=["rem-sub"], xalign=0, visible=False)
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: detail_panel(self.due, view, r))
        self.due.add_controller(click)
        body.append(self.due)
        box.append(body)
        self.revealer.set_child(box)
        self.set_child(self.revealer)
        self.box = box
        menu = Gtk.GestureClick(button=3)
        menu.connect("pressed", self._menu)
        self.add_controller(menu)
        self.update()

    def update(self) -> None:
        r = self.r
        (self.box.add_css_class if r.get("completed") else self.box.remove_css_class)("done")
        p = int(r.get("priority") or 0)
        self.prio.set_label("!" * p)
        self.prio.set_visible(p > 0)
        self.flag.set_visible(bool(r.get("flagged")))
        notes = (r.get("notes") or "").strip().split("\n")[0]
        self.notes.set_label(notes)
        self.notes.set_visible(bool(notes))
        text = due_text(r)
        self.due.set_label(text)
        self.due.set_visible(bool(text))
        (self.due.add_css_class if is_overdue(r) else self.due.remove_css_class)("overdue")
        self.strike.queue_draw()

    def _commit_title(self, then_new: bool = False, drop_empty: bool = False) -> None:
        text = self.title.get_buffer().get_text().strip()
        r = self.r
        if drop_empty and not text and not r.get("notes") and not r.get("due") and r in self.view.store.reminders:
            # a new reminder left empty goes away (macOS)
            GLib.idle_add(lambda: (r in self.view.store.reminders and not r.get("title")
                                   and not ui.menu.OPEN and self.view.delete(r), False)[1])
            return
        if text != self.r.get("title", ""):
            self.view.store.update_reminder(self.r, title=text)
            self.view.changed()
        if then_new and text:
            self.view.new_reminder()

    def _toggled(self, check) -> None:
        done = check.get_active()
        if done == bool(self.r.get("completed")):
            return
        self.view.store.update_reminder(self.r, completed=done)
        self.update()
        target = Adw.CallbackAnimationTarget.new(self._set_progress)
        anim = Adw.TimedAnimation.new(self.strike, self.progress, 1.0 if done else 0.0, tokens.ms(300), target)
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.play()
        self._anim = anim
        # the row leaves a list it no longer belongs to (macOS: after a beat)
        stays = (self.view.key == "completed") == done
        if not stays:
            GLib.timeout_add(900, self._leave)
        self.view.changed(refresh=False)

    def _leave(self) -> bool:
        if bool(self.r.get("completed")) != (self.view.key == "completed"):
            self.revealer.connect("notify::child-revealed",
                                  lambda rv, _p: rv.get_child_revealed() or self.view.refresh())
            self.revealer.set_reveal_child(False)
        return False

    def _set_progress(self, v: float) -> None:
        self.progress = v
        self.strike.queue_draw()

    def _draw_strike(self, area, cr, _w, h) -> None:
        if self.progress <= 0:
            return
        text = self.title.get_buffer().get_text() or ""
        tw, _th = self.title.create_pango_layout(text).get_pixel_size()
        c = ui.rgba("label_secondary")
        cr.set_source_rgba(c.red, c.green, c.blue, c.alpha)
        cr.set_line_width(1)
        y = int(h / 2) + 0.5
        cr.move_to(0, y)
        cr.line_to(tw * self.progress, y)
        cr.stroke()

    def _menu(self, gest, _n, x, y) -> None:
        r, v = self.r, self.view
        lists = [[ui.menu.Item(x_["name"], (lambda lid=x_["id"]: (v.store.update_reminder(r, list=lid),
                                                                   v.changed())),
                               checked=r.get("list") == x_["id"]) for x_ in v.store.lists]]
        ui.menu.popup(self, [
            [ui.menu.Item("Show Details", lambda: detail_panel(self.due if self.due.get_visible() else self, v, r))],
            [ui.menu.Item("Unflag" if r.get("flagged") else "Flag",
                          lambda: (v.store.update_reminder(r, flagged=not r.get("flagged")), v.changed())),
             ui.menu.Item("Mark as Not Completed" if r.get("completed") else "Mark as Completed",
                          lambda: self.check.set_active(not self.check.get_active())),
             ui.menu.Item("Move to", submenu=lists)],
            [ui.menu.Item("Delete", lambda: v.delete(r))],
        ], at=(x, y), glass=True, passthrough=True)


# -- detail panel ------------------------------------------------------------------------------------
def detail_panel(anchor: Gtk.Widget, view, r: dict) -> Gtk.Popover:
    """The ⓘ panel: notes, date & time, flag, priority, list."""
    st = view.store
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, css_classes=["rem-detail"])
    title = Gtk.Entry(text=r.get("title", ""), placeholder_text="Title")
    title.add_css_class("rem-detail-title")
    notes = Gtk.Entry(text=r.get("notes", ""), placeholder_text="Notes")
    col.append(title)
    col.append(notes)
    col.append(ui.panel.separator())

    def save(**kw):
        st.update_reminder(r, **kw)
        view.changed()
    title.connect("changed", lambda e: save(title=e.get_text().strip()))
    notes.connect("changed", lambda e: save(notes=e.get_text()))

    dt = S.due_datetime(r)
    cal = Gtk.Calendar()
    if dt:
        cal.select_day(GLib.DateTime.new_local(dt.year, dt.month, dt.day, 0, 0, 0))
    hour = Gtk.SpinButton.new_with_range(0, 23, 1)
    minute = Gtk.SpinButton.new_with_range(0, 59, 5)
    for sp in (hour, minute):
        sp.set_wrap(True)
        sp.set_orientation(Gtk.Orientation.HORIZONTAL)
    now = datetime.datetime.now()
    hour.set_value(dt.hour if dt and S.has_time(r) else (now.hour + 1) % 24)
    minute.set_value(dt.minute if dt and S.has_time(r) else 0)
    times = Gtk.Box(spacing=4, halign=Gtk.Align.END)
    times.append(hour)
    times.append(Gtk.Label(label=":"))
    times.append(minute)
    cal_rev = Gtk.Revealer(child=cal, reveal_child=dt is not None)
    time_rev = Gtk.Revealer(child=times, reveal_child=S.has_time(r))
    date_sw = ui.controls.switch(dt is not None)
    time_sw = ui.controls.switch(S.has_time(r))

    def apply_due(*_a):
        if not date_sw.get_active():
            save(due=None)
            return
        d = cal.get_date()
        day = datetime.date(d.get_year(), d.get_month(), d.get_day_of_month())
        if time_sw.get_active():
            save(due=S.make_due(day, int(hour.get_value()), int(minute.get_value())))
        else:
            save(due=S.make_due(day))

    def date_toggled(on):
        cal_rev.set_reveal_child(on)
        if not on and time_sw.get_active():
            time_sw.set_active(False)
        apply_due()

    def time_toggled(on):
        time_rev.set_reveal_child(on)
        if on and not date_sw.get_active():
            date_sw.set_active(True)
        apply_due()
    date_sw.connect("notify::active", lambda s, _p: date_toggled(s.get_active()))
    time_sw.connect("notify::active", lambda s, _p: time_toggled(s.get_active()))
    cal.connect("day-selected", apply_due)
    hour.connect("value-changed", apply_due)
    minute.connect("value-changed", apply_due)
    col.append(ui.panel.row("calendar-symbolic", "Date", date_sw))
    col.append(cal_rev)
    col.append(ui.panel.row("alarm-symbolic", "Time", time_sw))
    col.append(time_rev)
    col.append(ui.panel.separator())
    col.append(ui.panel.row("flag-symbolic", "Flag",
                            ui.controls.switch(bool(r.get("flagged")), lambda on: save(flagged=on))))
    col.append(ui.panel.row(None, "Priority", ui.controls.popup_button(
        PRIORITIES, int(r.get("priority") or 0), lambda i: save(priority=i))))
    ids = [x["id"] for x in st.lists]
    col.append(ui.panel.row(None, "List", ui.controls.popup_button(
        [x["name"] for x in st.lists], ids.index(r["list"]) if r.get("list") in ids else 0,
        lambda i: save(list=ids[i]))))
    pop = ui.panel.popup(anchor, col, position=Gtk.PositionType.LEFT if isinstance(anchor, Gtk.Button)
                         else Gtk.PositionType.BOTTOM)
    pop.connect("closed", lambda *_: view.refresh())
    return pop


# -- the view -----------------------------------------------------------------------------------------
class RemindersView(Gtk.Box):
    """on_changed(): counts in the sidebar, alerts re-aimed."""

    def __init__(self, store, on_changed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["nt-rem"], hexpand=True)
        self.store, self.on_changed = store, on_changed
        self.key, self.query = "today", ""
        head = Gtk.Box(css_classes=["nt-rem-head"])
        self.title = Gtk.Label(css_classes=["nt-rem-title"], xalign=0, hexpand=True,
                               ellipsize=Pango.EllipsizeMode.END)
        self.count = Gtk.Label(css_classes=["nt-rem-count"])
        head.append(self.title)
        head.append(self.count)
        self.append(head)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.set_header_func(self._header)
        over = Gtk.Overlay(vexpand=True)
        over.set_child(Gtk.ScrolledWindow(child=self.list, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.empty = Gtk.Label(css_classes=["nt-rem-empty"], can_target=False, visible=False)
        over.add_overlay(self.empty)
        self.append(over)
        # a click on the empty space below the rows adds a reminder (macOS)
        click = Gtk.GestureClick()
        click.connect("released", self._clicked_empty)
        self.list.add_controller(click)

    def show(self, key: str, query: str = None) -> None:
        self.key = key
        if query is not None:
            self.query = query
        self.refresh()

    def rows_data(self) -> list:
        items = self.store.reminders_in(self.key)
        if self.query.strip():
            base = self.store.reminders if self.key == "completed" else \
                [r for r in self.store.reminders if not r.get("completed")]
            items = S.search_reminders(base, self.query)
        return items

    def refresh(self) -> None:
        sel = self.list.get_selected_row()
        sel_id = sel.r["id"] if sel is not None else None
        self.list.remove_all()
        items = self.rows_data()
        for r in items:
            row = ReminderRow(self, r)
            self.list.append(row)
            if r["id"] == sel_id:
                self.list.select_row(row)
        if self.key in S.SMART:
            name, _icon, color = SMART_INFO[self.key]
        else:
            lst = self.store.rlist(self.key) or {"name": "", "color": "blue"}
            name, color = lst["name"], lst.get("color", "blue")
        if self.query.strip():
            name = f"Results for “{self.query.strip()}”"
        self.title.set_label(name)
        for c in list(self.title.get_css_classes()):
            if c.startswith("c-"):
                self.title.remove_css_class(c)
            if c.startswith("c-") and c in self.count.get_css_classes():
                self.count.remove_css_class(c)
        self.title.add_css_class(f"c-{color}")
        self.count.add_css_class(f"c-{color}")
        self.count.set_label(str(len(items)) if self.key != "completed" else "")
        self.empty.set_label("No Completed Reminders" if self.key == "completed" else "No Reminders")
        self.empty.set_visible(not items)

    def _header(self, row, before) -> None:
        text = None
        if self.key == "scheduled":
            d = S.due_date(row.r)
            prev = S.due_date(before.r) if before else None
            h = day_heading(d) if d else None
            if h and (prev is None or day_heading(prev) != h):
                text = h
        elif self.key in ("all", "completed"):
            lid = row.r.get("list")
            if before is None or before.r.get("list") != lid:
                lst = self.store.rlist(lid)
                text = lst["name"] if lst else None
        if text is None:
            row.set_header(None)
            return
        cur = row.get_header()
        if isinstance(cur, Gtk.Label) and cur.get_label() == text:
            return
        row.set_header(Gtk.Label(label=text, xalign=0, css_classes=["nt-rem-section"]))

    def changed(self, refresh: bool = False) -> None:
        if refresh:
            self.refresh()
        else:
            for row in self._rows():
                row.update()
            self.count.set_label(str(len(self.rows_data())) if self.key != "completed" else "")
        if self.on_changed:
            self.on_changed()

    def _rows(self):
        child = self.list.get_first_child()
        while child is not None:
            if isinstance(child, ReminderRow):
                yield child
            child = child.get_next_sibling()

    def new_reminder(self) -> dict:
        """A new reminder in this list (Today / Scheduled: due today), its
        title ready for typing."""
        due = S.make_due(datetime.date.today()) if self.key in ("today", "scheduled") else None
        lid = self.key if self.key not in S.SMART else S.DEFAULT_LIST
        r = self.store.new_reminder(lid, due=due)
        if self.key == "completed":
            self.key = lid
        self.query = ""
        self.refresh()
        for row in self._rows():
            if row.r is r:
                self.list.select_row(row)
                row.title.grab_focus()
        if self.on_changed:
            self.on_changed()
        return r

    def delete(self, r: dict) -> None:
        self.store.delete_reminder(r)
        self.refresh()
        if self.on_changed:
            self.on_changed()

    def delete_selected(self) -> bool:
        row = self.list.get_selected_row()
        if row is None:
            return False
        self.delete(row.r)
        return True

    def _clicked_empty(self, gest, _n, _x, y) -> None:
        if self.list.get_row_at_y(int(y)) is None and self.key != "completed":
            # empty rows of this list first (an untitled one already waiting)
            if not any(not row.r.get("title") for row in self._rows()):
                self.new_reminder()
