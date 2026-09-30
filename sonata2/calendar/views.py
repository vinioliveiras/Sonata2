"""Calendar's views, painted with cairo (one widget per view, not one per
event): Month (6 x 7 grid), Day / Week (hourly timeline with an all-day
strip), Year (12 small months) and the sidebar's mini month. Colours come
from tokens (ui.rgba) and the calendar palette (model.PALETTE); they
repaint on appearance changes.

Every view talks to its window (`win`) through a small interface:
win.date, win.first_weekday, win.occurrences(a, b), win.selected,
win.select(occ), win.edit(occ, widget, rect), win.create(start, end,
all_day, widget, rect), win.move(occ, dstart, dend), win.go(date, view),
win.event_menu(occ, widget, x, y), win.empty_menu(widget, x, y, start,
all_day), win.fmt_time(t), win.fmt_hour(h), win.calendar_color(occ)."""
import datetime as dt
import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .. import ui  # noqa: E402
from . import model  # noqa: E402

HOUR_H = 48              # timeline: pixels per hour
GUTTER = 56              # timeline: hour labels column
LANE = 20                # all-day strip / month bars: one line
DAYS_H = 30              # week header: "Wed 30"
SNAP = 15                # minutes: moves and new events snap to it
EDGE = 6                 # px at an event's bottom that resize it
MONTH_HEAD = 26          # month view: weekday names
ONE_DAY = dt.timedelta(days=1)


# -- painting helpers ---------------------------------------------------------------------------
_fonts = {}


def font(token="text_small", weight=400, scale=1.0, display=False) -> Pango.FontDescription:
    key = (token, weight, scale, display, ui.values()["font"])
    if key not in _fonts:
        fam = ui.values()["font_display" if display else "font"].replace('"', "")
        d = Pango.FontDescription()
        d.set_family(fam)
        d.set_absolute_size(ui.px(token) * scale * Pango.SCALE)
        d.set_weight(weight)
        _fonts[key] = d
    return _fonts[key]


def layout(widget, text, token="text_small", weight=400, width=None, align=Pango.Alignment.LEFT, scale=1.0,
           display=False) -> Pango.Layout:
    lay = widget.create_pango_layout(text)
    lay.set_font_description(font(token, weight, scale, display))
    lay.set_alignment(align)
    if width is not None:
        lay.set_width(max(1, int(width * Pango.SCALE)))
        lay.set_ellipsize(Pango.EllipsizeMode.END)
    return lay


def show(cr, lay, x, y, rgba, alpha=1.0) -> None:
    cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha * alpha)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, lay)


def text_h(lay) -> float:
    return lay.get_pixel_extents()[1].height


def source(cr, rgba, alpha=1.0) -> None:
    cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha * alpha)


def rrect(cr, x, y, w, h, r) -> None:
    r = max(0.0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def palette_rgba(color_id: str) -> Gdk.RGBA:
    ref = next((p[2] for p in model.PALETTE if p[0] == color_id), "sys_blue")
    if ref.startswith("#"):
        c = Gdk.RGBA()
        c.parse(ref)
        return c
    return ui.rgba(ref).copy()


def event_colors(color_id: str):
    """(solid, fill, text) for events of a calendar colour, per appearance:
    a tinted fill with text in a deeper (light) / lighter (dark) shade of
    the colour, like macOS Calendar."""
    solid = palette_rgba(color_id)
    lab = ui.rgba("label")
    dark = ui.is_dark()
    fill = solid.copy()
    fill.alpha = 0.30 if dark else 0.18
    t = 0.30 if dark else 0.42
    text = Gdk.RGBA()
    text.red, text.green, text.blue = (solid.red * (1 - t) + lab.red * t, solid.green * (1 - t) + lab.green * t,
                                       solid.blue * (1 - t) + lab.blue * t)
    text.alpha = 1.0
    return solid, fill, text


class Canvas(Gtk.DrawingArea):
    """A painted view: repaints on appearance changes, keeps hit rects."""

    def __init__(self, win, **kw):
        super().__init__(**kw)
        self.win = win
        self.hits = []            # [(x, y, w, h, occurrence)], topmost last
        self.set_draw_func(self._draw)
        ui.on_change(self.queue_draw)

    def hit(self, x, y):
        for hx, hy, hw, hh, o in reversed(self.hits):
            if hx <= x < hx + hw and hy <= y < hy + hh:
                return o, (hx, hy, hw, hh)
        return None, None

    def _draw(self, _a, cr, w, h):
        self.hits = []
        self.paint(cr, w, h)

    def paint(self, cr, w, h):
        raise NotImplementedError

    def rect(self, x, y, w, h) -> Gdk.Rectangle:
        r = Gdk.Rectangle()
        r.x, r.y, r.width, r.height = int(x), int(y), max(1, int(w)), max(1, int(h))
        return r


def _today_circle(cr, cx, cy, r) -> None:
    source(cr, ui.rgba("destructive"))
    cr.arc(cx, cy, r, 0, 2 * math.pi)
    cr.fill()


def draw_block(canvas, cr, x, y, w, h, occ, selected, first=True, time_text=None) -> None:
    """A timed event on the timeline: tinted block, solid left bar, title
    (+ time and location when there is room)."""
    solid, fill, text = event_colors(canvas.win.calendar_color(occ))
    r = ui.px("r_menu_row")
    cr.save()
    rrect(cr, x, y, w, h, r)
    cr.clip()
    source(cr, solid if selected else fill)
    cr.paint()
    if not selected:
        source(cr, solid)
        cr.rectangle(x, y, 3, h)
        cr.fill()
    col = ui.rgba("label_on_accent") if selected else text
    tx, tw = x + 7, w - 10
    lay = layout(canvas, occ.event.summary or "New Event", weight=600, width=tw)
    if first and h >= 30 and w > 40:
        lay.set_width(int(tw * Pango.SCALE))
        show(cr, lay, tx, y + 2, col)
        cy = y + 2 + text_h(lay)
        for extra in (time_text, occ.event.location):
            if not extra:
                continue
            sub = layout(canvas, extra, width=tw)
            if cy + text_h(sub) > y + h:
                break
            show(cr, sub, tx, cy, col, 0.85)
            cy += text_h(sub)
    else:                                   # short: one line, title then time
        line = occ.event.summary or "New Event"
        if first and time_text and w > 90:
            line += "  " + time_text
        lay = layout(canvas, line, weight=600, width=tw)
        show(cr, lay, tx, y + max(0, (min(h, 18) - text_h(lay)) / 2), col)
    cr.restore()


def draw_bar(canvas, cr, x, y, w, h, occ, selected, label=True, round_l=True, round_r=True) -> None:
    """An all-day / multi-day event: a filled bar with its title."""
    solid, fill, text = event_colors(canvas.win.calendar_color(occ))
    r = ui.px("r_menu_row")
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    ext_l, ext_r = (0 if round_l else r + 1), (0 if round_r else r + 1)
    rrect(cr, x - ext_l, y, w + ext_l + ext_r, h, r)
    cr.clip()
    source(cr, solid if selected else fill)
    cr.paint()
    if label:
        lay = layout(canvas, occ.event.summary or "New Event", weight=600, width=max(1, w - 10))
        show(cr, lay, x + 6, y + (h - text_h(lay)) / 2, ui.rgba("label_on_accent") if selected else text)
    cr.restore()


# -- timeline: Day and Week ---------------------------------------------------------------------
class Timeline(Gtk.Box):
    """Week (7 days) or Day (1 day): day names, an all-day strip, and a
    scrolled 24-hour grid with events as blocks side by side when they
    overlap, and the red current-time line."""

    def __init__(self, win, ndays: int):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["cal-timeline"])
        self.win, self.ndays = win, ndays
        self.days_h = DAYS_H if ndays > 1 else 0      # Day view: the title names the day
        self.head = TimelineHead(self)
        self.grid = TimelineGrid(self)
        self.scroll = Gtk.ScrolledWindow(child=self.grid, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.append(self.head)
        self.append(Gtk.Separator(css_classes=["cal-sep"]))
        self.append(self.scroll)
        self.segments = {}          # day index -> [(occurrence, seg_start, seg_end, col, ncols)]
        self.bars = []              # [(occurrence, first col, last col, lane)]
        self._scrolled = False
        self._tick = 0
        self.connect("map", self._mapped)
        self.connect("unmap", self._unmapped)

    def days(self):
        first = self.win.date if self.ndays == 1 else model.week_start(self.win.date, self.win.first_weekday)
        return [first + dt.timedelta(days=i) for i in range(self.ndays)]

    def range(self):
        d = self.days()
        return model.day_start(d[0]), model.day_start(d[-1]) + ONE_DAY

    def refresh(self) -> None:
        a, b = self.range()
        occs = self.win.occurrences(a, b)
        days = self.days()
        self.bars = model.lanes([o for o in occs if model.is_multiday(o)], days[0], self.ndays)
        self.segments = {}
        from .ics import Occurrence
        for i, d in enumerate(days):
            da, db = model.day_start(d), model.day_start(d) + ONE_DAY
            segs, orig = [], {}
            for o in occs:
                if model.is_multiday(o) or not (o.start < db and (o.end > da or o.start == o.end >= da)):
                    continue
                s = Occurrence(o.event, max(o.start, da), min(o.end, db))
                segs.append(s)
                orig[s.key] = o
            cols = model.columns(segs)
            self.segments[i] = [(orig[s.key], s.start, s.end, *cols[s]) for s in segs]
        nl = max(1, (max(b[3] for b in self.bars) + 1) if self.bars else 1)
        self.head.set_content_height(self.days_h + min(nl, 6) * LANE + 8)
        self.head.queue_draw()
        self.grid.queue_draw()

    def col_x(self, width, i):
        cw = (width - GUTTER) / self.ndays
        return GUTTER + i * cw, cw

    # smooth scroll to 8 AM the first time the timeline shows
    def scroll_to_hour(self, hour: float, animate=True) -> None:
        adj = self.scroll.get_vadjustment()
        target = min(max(0.0, hour * HOUR_H - 8), max(0.0, adj.get_upper() - adj.get_page_size()))
        if not animate:
            adj.set_value(target)
            return
        from gi.repository import Adw
        start = adj.get_value()
        ms = ui.tokens.ms(2 * float(ui.values()["t_standard"].rstrip("ms")))
        anim = Adw.TimedAnimation.new(self.scroll, 0, 1, ms,
                                      Adw.CallbackAnimationTarget.new(
                                          lambda v: adj.set_value(start + (target - start) * v)))
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.play()
        self._anim = anim

    def _mapped(self, *_a):
        if not self._scrolled:
            self._scrolled = True

            def go():
                if self.scroll.get_vadjustment().get_upper() <= 1:
                    return True                  # not allocated yet
                self.scroll_to_hour(8)
                return False
            GLib.timeout_add(60, go)
        self._schedule_tick()

    def _unmapped(self, *_a):
        if self._tick:
            GLib.source_remove(self._tick)
            self._tick = 0

    def _schedule_tick(self):
        """Repaint at the next minute (the current-time line), while shown."""
        if self._tick:
            GLib.source_remove(self._tick)
        now = dt.datetime.now()

        def tick():
            self._tick = 0
            root = self.get_root()
            if not (root and hasattr(root, "is_suspended") and root.is_suspended()):   # minimized: no repaint
                self.grid.queue_draw()
            if self.get_mapped():
                self._schedule_tick()
            return False
        self._tick = GLib.timeout_add_seconds(max(1, 60 - now.second), tick)


class _Dragging:
    """Shared drag logic: move by days (and minutes on the grid) or resize."""

    def _drag_setup(self):
        self.drag = None
        g = Gtk.GestureDrag(button=1)
        g.connect("drag-begin", self._drag_begin)
        g.connect("drag-update", self._drag_update)
        g.connect("drag-end", self._drag_end)
        self.add_controller(g)

    def _drag_begin(self, _g, x, y):
        occ, rect = self.hit(x, y)
        self.drag = None
        if occ is None:
            return
        mode = "move"
        if getattr(self, "resizable", False) and rect and y > rect[1] + rect[3] - EDGE and occ.end == \
                self._seg_end(occ, rect):
            mode = "resize"
        self.drag = {"occ": occ, "mode": mode, "x0": x, "y0": y, "active": False, "ds": dt.timedelta(0),
                     "de": dt.timedelta(0)}

    def _seg_end(self, occ, rect):
        return occ.end

    def _drag_update(self, _g, dx, dy):
        d = self.drag
        if d is None:
            return
        if not d["active"] and abs(dx) + abs(dy) < 4:
            return
        d["active"] = True
        days, minutes = self.drag_delta(d["x0"], d["y0"], dx, dy)
        delta = dt.timedelta(days=days, minutes=minutes)
        if d["mode"] == "resize":
            o = d["occ"]
            de = dt.timedelta(minutes=minutes)
            if o.end + de < o.start + dt.timedelta(minutes=SNAP):
                de = o.start + dt.timedelta(minutes=SNAP) - o.end
            d["ds"], d["de"] = dt.timedelta(0), de
        else:
            d["ds"] = d["de"] = delta
        self.drag_changed()

    def drag_changed(self):
        self.queue_draw()

    def _drag_end(self, _g, _dx, _dy):
        d, self.drag = self.drag, None
        if d and d["active"] and (d["ds"] or d["de"]):
            self.win.move(d["occ"], d["ds"], d["de"])
        else:
            self.drag_changed()

    def dragged(self, occ):
        """(start, end) of an occurrence, as moved by the drag in progress."""
        d = self.drag
        if d and d["active"] and d["occ"] == occ:
            return occ.start + d["ds"], occ.end + d["de"]
        return occ.start, occ.end


def _clicks(canvas, pressed):
    g = Gtk.GestureClick(button=0)
    g.connect("pressed", lambda gest, n, x, y: pressed(gest.get_current_button(), n, x, y))
    canvas.add_controller(g)


class TimelineGrid(_Dragging, Canvas):
    resizable = True

    def __init__(self, tl):
        super().__init__(tl.win, content_height=24 * HOUR_H, vexpand=True, hexpand=True)
        self.tl = tl
        self._drag_setup()
        _clicks(self, self._pressed)
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._motion)
        self.add_controller(motion)

    def slot(self, x, y):
        w = self.get_width()
        i = int(min(self.tl.ndays - 1, max(0, (x - GUTTER) // ((w - GUTTER) / self.tl.ndays))))
        minutes = int(max(0, min(24 * 60 - SNAP, y / HOUR_H * 60)) // SNAP * SNAP)
        return model.day_start(self.tl.days()[i]) + dt.timedelta(minutes=minutes)

    def drag_delta(self, x0, y0, dx, dy):
        w = self.get_width()
        cw = (w - GUTTER) / self.tl.ndays
        i0 = int((x0 - GUTTER) // cw)
        i1 = int(min(self.tl.ndays - 1, max(0, (x0 + dx - GUTTER) // cw)))
        minutes = round(dy / HOUR_H * 60 / SNAP) * SNAP
        return (i1 - i0 if self.drag and self.drag["mode"] == "move" else 0), minutes

    def _seg_end(self, occ, rect):
        return occ.end if occ.end <= model.day_start(self.slot(rect[0] + 1, rect[1]).date()) + ONE_DAY else None

    def _motion(self, _c, x, y):
        occ, rect = self.hit(x, y)
        edge = occ is not None and y > rect[1] + rect[3] - EDGE
        self.set_cursor_from_name("ns-resize" if edge else None)

    def _pressed(self, button, n, x, y):
        occ, rect = self.hit(x, y)
        win = self.win
        if button == 3:
            if occ is not None:
                win.select(occ)
                win.event_menu(occ, self, x, y)
            else:
                win.empty_menu(self, x, y, self.slot(x, y), False)
            return
        if button != 1:
            return
        if n == 1:
            win.select(occ)
        elif n == 2:
            if occ is not None:
                win.edit(occ, self, self.rect(*rect))
            else:
                s = self.slot(x, y)
                s = s.replace(minute=0) if s.minute < 30 else s.replace(minute=30)
                x0, cw = self.tl.col_x(self.get_width(), self.tl.days().index(s.date()))
                win.create(s, s + dt.timedelta(hours=1), False, self,
                           self.rect(x0, (s.hour + s.minute / 60) * HOUR_H, cw, HOUR_H))

    def paint(self, cr, w, h):
        tl, win = self.tl, self.win
        days = tl.days()
        source(cr, ui.rgba("content_bg"))
        cr.paint()
        today = dt.date.today()
        cw = (w - GUTTER) / tl.ndays
        # weekends a shade darker
        for i, d in enumerate(days):
            if d.weekday() >= 5 and tl.ndays > 1:
                source(cr, ui.rgba("row_alt"))
                cr.rectangle(GUTTER + i * cw, 0, cw, h)
                cr.fill()
        # hour lines and labels
        sep, lab2 = ui.rgba("separator"), ui.rgba("label_secondary")
        for hr in range(24):
            y = hr * HOUR_H + 0.5
            source(cr, sep)
            cr.rectangle(GUTTER - 6, y, w - GUTTER + 6, 1)
            cr.fill()
            if hr:
                lay = layout(self, win.fmt_hour(hr), width=GUTTER - 12, align=Pango.Alignment.RIGHT)
                show(cr, lay, 2, y - text_h(lay) / 2 - 1, lab2)
        for i in range(tl.ndays + 1):
            source(cr, sep)
            cr.rectangle(int(GUTTER + i * cw), 0, 1, h)
            cr.fill()
        # events
        sel = win.selected
        drag = self.drag if self.drag and self.drag["active"] else None
        for i in range(tl.ndays):
            x0 = GUTTER + i * cw
            for occ, s, e, col, ncols in tl.segments.get(i, []):
                if drag and drag["occ"] == occ:
                    continue
                self._block(cr, occ, s, e, x0, cw, col, ncols, sel)
        if drag:                                    # the dragged event, where it would go
            occ = drag["occ"]
            s, e = self.dragged(occ)
            for i, d in enumerate(days):
                da = model.day_start(d)
                ss, ee = max(s, da), min(e, da + ONE_DAY)
                if ss < ee or (ss == ee == s and da <= s < da + ONE_DAY):
                    self._block(cr, occ, ss, ee, GUTTER + i * cw, cw, 0, 1, occ.key, first=ss == s)
        # the current time: a red line over today, its time in the gutter
        if today in days:
            now = dt.datetime.now()
            i = days.index(today)
            y = (now.hour + now.minute / 60) * HOUR_H
            red = ui.rgba("destructive")
            source(cr, red)
            x0 = GUTTER + i * cw
            cr.rectangle(x0, y - 0.75, cw, 1.5)
            cr.fill()
            if tl.ndays > 1:
                source(cr, red, 0.35)
                cr.rectangle(GUTTER, y - 0.5, w - GUTTER, 1)
                cr.fill()
            cr.arc(x0, y, 4, 0, 2 * math.pi)
            source(cr, red)
            cr.fill()
            lay = layout(self, win.fmt_time(now.time()), weight=600, width=GUTTER - 12, align=Pango.Alignment.RIGHT)
            th = text_h(lay)
            source(cr, ui.rgba("content_bg"))
            cr.rectangle(0, y - th / 2 - 1, GUTTER - 4, th + 2)
            cr.fill()
            show(cr, lay, 2, y - th / 2 - 1, red)

    def _block(self, cr, occ, s, e, x0, cw, col, ncols, sel, first=None):
        y = (s.hour + s.minute / 60) * HOUR_H
        mins = (e - s).total_seconds() / 60
        hgt = max(mins / 60 * HOUR_H, 18)
        colw = (cw - 6) / ncols
        x = x0 + 1 + col * colw
        bw = colw - 1 if ncols > 1 else cw - 6
        first = s == occ.start if first is None else first
        tt = self.win.fmt_time(occ.start.time()) if first else None
        draw_block(self, cr, x, y + 1, bw, hgt - 2, occ, sel == occ.key, first=True, time_text=tt)
        self.hits.append((x, y, bw, hgt, occ))


class TimelineHead(_Dragging, Canvas):
    """Day names (today's date in a red circle) and the all-day strip."""

    def __init__(self, tl):
        super().__init__(tl.win, content_height=(DAYS_H if tl.ndays > 1 else 0) + LANE + 8, hexpand=True)
        self.tl = tl
        self.add_css_class("cal-head")
        self._drag_setup()
        _clicks(self, self._pressed)

    def drag_delta(self, x0, y0, dx, dy):
        cw = (self.get_width() - GUTTER) / self.tl.ndays
        return int(min(self.tl.ndays - 1, max(0, (x0 + dx - GUTTER) // cw))) - int((x0 - GUTTER) // cw), 0

    def day_at(self, x):
        cw = (self.get_width() - GUTTER) / self.tl.ndays
        i = int(min(self.tl.ndays - 1, max(0, (x - GUTTER) // cw)))
        return self.tl.days()[i], i

    def _pressed(self, button, n, x, y):
        occ, rect = self.hit(x, y)
        d, i = self.day_at(x)
        win = self.win
        if y < self.tl.days_h:
            if button == 1 and self.tl.ndays > 1 and x > GUTTER:
                win.go(d, "day")
            return
        if button == 3:
            if occ is not None:
                win.select(occ)
                win.event_menu(occ, self, x, y)
            else:
                win.empty_menu(self, x, y, model.day_start(d), True)
            return
        if button != 1:
            return
        if n == 1:
            win.select(occ)
        elif n == 2:
            if occ is not None:
                win.edit(occ, self, self.rect(*rect))
            elif x > GUTTER:
                x0, cw = self.tl.col_x(self.get_width(), i)
                s = model.day_start(d)
                win.create(s, s + ONE_DAY, True, self, self.rect(x0, self.tl.days_h, cw, LANE))

    def paint(self, cr, w, h):
        tl, win = self.tl, self.win
        days = tl.days()
        source(cr, ui.rgba("content_bg"))
        cr.paint()
        cw = (w - GUTTER) / tl.ndays
        today = dt.date.today()
        lab, lab2 = ui.rgba("label"), ui.rgba("label_secondary")
        for i, d in enumerate(days):
            x0 = GUTTER + i * cw
            if d.weekday() >= 5 and tl.ndays > 1:
                source(cr, ui.rgba("row_alt"))
                cr.rectangle(x0, 0, cw, h)
                cr.fill()
            if not self.tl.days_h:
                continue
            name = layout(self, d.strftime("%a"), "text_body")
            num = layout(self, str(d.day), "text_body", weight=600 if d == today else 400)
            nw, nh = num.get_pixel_extents()[1].width, text_h(num)
            ty = (self.tl.days_h - nh) / 2
            # right-aligned like macOS: "Wed (30)"
            right = x0 + cw - 10
            if d == today:
                _today_circle(cr, right - nw / 2, self.tl.days_h / 2, max(nw, nh) / 2 + 4)
                show(cr, num, right - nw, ty, ui.rgba("label_on_accent"))
            else:
                show(cr, num, right - nw, ty, lab if tl.ndays == 1 or d.weekday() < 5 else lab2)
            show(cr, name, right - nw - (10 if d == today else 5) - name.get_pixel_extents()[1].width, ty,
                 lab if d.weekday() < 5 or tl.ndays == 1 else lab2)
        sep = ui.rgba("separator")
        for i in range(tl.ndays + 1):
            source(cr, sep)
            cr.rectangle(int(GUTTER + i * cw), self.tl.days_h, 1, h - self.tl.days_h)
            cr.fill()
        source(cr, sep)
        cr.rectangle(GUTTER, self.tl.days_h, w - GUTTER, 1)
        cr.fill()
        lay = layout(self, "all-day", width=GUTTER - 12, align=Pango.Alignment.RIGHT)
        show(cr, lay, 2, self.tl.days_h + 4 + (LANE - text_h(lay)) / 2, lab2)
        # all-day and multi-day bars
        sel = win.selected
        drag = self.drag if self.drag and self.drag["active"] else None
        for occ, s, e, lane in tl.bars:
            if lane >= 6:
                continue
            if drag and drag["occ"] == occ:
                ds = drag["ds"].days
                s, e = max(0, s + ds), min(tl.ndays - 1, e + ds)
                if e < s:
                    continue
            x = GUTTER + s * cw + 2
            y = self.tl.days_h + 4 + lane * LANE
            bw = (e - s + 1) * cw - 4
            draw_bar(self, cr, x, y + 1, bw, LANE - 2, occ, sel == occ.key)
            self.hits.append((x, y, bw, LANE, occ))


# -- month -------------------------------------------------------------------------------------
class MonthView(_Dragging, Canvas):
    """6 x 7 days; all-day / multi-day events as filled bars, timed ones as
    a coloured dot, title and time; "N more" when a day overflows."""

    def __init__(self, win):
        super().__init__(win, vexpand=True, hexpand=True, css_classes=["cal-month"])
        self.dates = []
        self.occs = []
        self.more = []            # [(x, y, w, h, date)]
        self._drag_setup()
        _clicks(self, self._pressed)
        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL |
                                       Gtk.EventControllerScrollFlags.DISCRETE)
        sc.connect("scroll", lambda _c, _dx, dy: (self.win.step(1 if dy > 0 else -1), True)[1])
        self.add_controller(sc)
        self._target = None

    def range(self):
        d = self.win.date
        self.dates = model.month_grid(d.year, d.month, self.win.first_weekday)
        return model.day_start(self.dates[0]), model.day_start(self.dates[-1]) + ONE_DAY

    def refresh(self):
        a, b = self.range()
        self.occs = self.win.occurrences(a, b)
        self.queue_draw()

    def cell(self, x, y):
        w, h = self.get_width(), self.get_height()
        cw, ch = w / 7, (h - MONTH_HEAD) / 6
        c = int(min(6, max(0, x // cw)))
        r = int(min(5, max(0, (y - MONTH_HEAD) // ch)))
        return r * 7 + c

    def drag_delta(self, x0, y0, dx, dy):
        return self.cell(x0 + dx, y0 + dy) - self.cell(x0, y0), 0

    def drag_changed(self):
        d = self.drag
        self._target = self.cell(d["x0"], d["y0"]) + d["ds"].days if d and d["active"] else None
        self.queue_draw()

    def _pressed(self, button, n, x, y):
        for mx, my, mw, mh, d in self.more:
            if mx <= x < mx + mw and my <= y < my + mh and button == 1:
                self.win.go(d, "day")
                return
        occ, rect = self.hit(x, y)
        if y < MONTH_HEAD:
            return
        d = self.dates[self.cell(x, y)] if self.dates else self.win.date
        if button == 3:
            if occ is not None:
                self.win.select(occ)
                self.win.event_menu(occ, self, x, y)
            else:
                self.win.empty_menu(self, x, y, model.day_start(d), True)
            return
        if button != 1:
            return
        if n == 1:
            self.win.select(occ)
        elif n == 2:
            if occ is not None:
                self.win.edit(occ, self, self.rect(*rect))
            else:
                s = model.day_start(d)
                i = self.cell(x, y)
                cw, ch = self.get_width() / 7, (self.get_height() - MONTH_HEAD) / 6
                self.win.create(s, s + ONE_DAY, True, self,
                                self.rect((i % 7) * cw, MONTH_HEAD + (i // 7) * ch, cw, ch))

    def paint(self, cr, w, h):
        win = self.win
        self.more = []
        if not self.dates:
            self.range()
        source(cr, ui.rgba("content_bg"))
        cr.paint()
        cw, ch = w / 7, (h - MONTH_HEAD) / 6
        lab, lab2, lab3, sep = ui.rgba("label"), ui.rgba("label_secondary"), ui.rgba("label_tertiary"), \
            ui.rgba("separator")
        today = dt.date.today()
        month = win.date.month
        for c in range(7):
            d = self.dates[c]
            if d.weekday() >= 5:
                source(cr, ui.rgba("row_alt"))
                cr.rectangle(c * cw, 0, cw, h)
                cr.fill()
            lay = layout(self, d.strftime("%a"), "text_body", width=cw - 10, align=Pango.Alignment.RIGHT)
            show(cr, lay, c * cw, (MONTH_HEAD - text_h(lay)) / 2 + 2, lab)
        if self._target is not None and 0 <= self._target < 42:          # the drop target of a drag
            source(cr, ui.rgba("accent"), 0.12)
            cr.rectangle((self._target % 7) * cw, MONTH_HEAD + (self._target // 7) * ch, cw, ch)
            cr.fill()
        for r in range(6):
            source(cr, sep)
            cr.rectangle(0, int(MONTH_HEAD + r * ch), w, 1)
            cr.fill()
        for c in range(1, 7):
            source(cr, sep)
            cr.rectangle(int(c * cw), MONTH_HEAD, 1, h - MONTH_HEAD)
            cr.fill()
        num_h = 26
        sel = win.selected
        drag = self.drag if self.drag and self.drag["active"] else None
        for r in range(6):
            row = self.dates[r * 7:r * 7 + 7]
            y0 = MONTH_HEAD + r * ch
            # day numbers
            for c, d in enumerate(row):
                text = d.strftime("%b ") + str(d.day) if d.day == 1 else str(d.day)
                lay = layout(self, text, "text_body", weight=600 if d == today else 400)
                tw, th = lay.get_pixel_extents()[1].width, text_h(lay)
                xr = (c + 1) * cw - 8
                if d == today:
                    rr = max(th / 2 + 3, (tw + 10) / 2)
                    source(cr, ui.rgba("destructive"))
                    rrect(cr, xr - tw - (2 * rr - tw) / 2 - 1, y0 + 4, 2 * rr, 2 * (th / 2 + 3), rr)
                    cr.fill()
                    show(cr, lay, xr - tw - 1, y0 + 7, ui.rgba("label_on_accent"))
                else:
                    show(cr, lay, xr - tw, y0 + 7, lab if d.month == month else lab3)
            # events of this week
            ra, rb = model.day_start(row[0]), model.day_start(row[-1]) + ONE_DAY
            occs = [o for o in self.occs if o.start < rb and (o.end > ra or o.start == o.end >= ra)]
            if drag:
                occs = [o for o in occs if o != drag["occ"]]
            bars = model.lanes([o for o in occs if model.is_multiday(o)], row[0], 7)
            slots = [dict() for _ in range(7)]            # per day: slot -> item
            for item in bars:
                o, s, e, lane = item
                for c in range(s, e + 1):
                    slots[c][lane] = ("bar", item)
            for o in occs:
                if model.is_multiday(o):
                    continue
                c = (model.day_start(o.start) - ra).days
                if 0 <= c < 7:
                    k = 0
                    while k in slots[c]:
                        k += 1
                    slots[c][k] = ("pill", o)
            avail = max(1, int((ch - num_h - 2) // (LANE - 2)))
            line = LANE - 2
            labels = []
            for c in range(7):
                items = slots[c]
                limit = self._limit(items, avail)
                x = c * cw
                for k, (kind, it) in sorted(items.items()):
                    if k >= limit:
                        continue
                    y = y0 + num_h + k * line
                    if kind == "pill":
                        self._pill(cr, x + 3, y, cw - 6, line - 1, it, sel == it.key)
                        continue
                    o, s, e, lane = it
                    prev_vis = c > s and lane < self._limit(slots[c - 1], avail)
                    next_vis = c < e and lane < self._limit(slots[c + 1], avail)
                    bx = x + (0 if prev_vis else 3)
                    bw = cw - (0 if prev_vis else 3) - (0 if next_vis else 3)
                    draw_bar(self, cr, bx, y + 1, bw, line - 2, o, sel == o.key, label=False,
                             round_l=not prev_vis, round_r=not next_vis)
                    self.hits.append((bx, y, bw, line, o))
                    if not prev_vis:              # the title runs over the following visible pieces
                        span = 1
                        while c + span <= e and lane < self._limit(slots[c + span], avail):
                            span += 1
                        labels.append((bx, y, span * cw - 6, line, o))
            for bx, y, lw, lh, o in labels:
                col = ui.rgba("label_on_accent") if sel == o.key else event_colors(win.calendar_color(o))[2]
                lay = layout(self, o.event.summary or "New Event", weight=600, width=lw - 10)
                show(cr, lay, bx + 6, y + 1 + (lh - 2 - text_h(lay)) / 2, col)
            for c in range(7):
                items = slots[c]
                limit = self._limit(items, avail)
                hidden = sum(1 for k in items if k >= limit)
                x = c * cw
                if hidden:
                    y = y0 + num_h + limit * line
                    lay = layout(self, f"{hidden} more", weight=600, width=cw - 14)
                    show(cr, lay, x + 8, y + (line - text_h(lay)) / 2, lab2)
                    self.more.append((x, y, cw, line, row[c]))
        if drag and self._target is not None:
            o = drag["occ"]
            c = self._target
            if 0 <= c < 42:
                x, y = (c % 7) * cw, MONTH_HEAD + (c // 7) * ch + num_h
                if model.is_multiday(o):
                    draw_bar(self, cr, x + 3, y + 1, cw - 6, line - 2, o, True)
                else:
                    self._pill(cr, x + 3, y, cw - 6, line - 1, o, True)

    @staticmethod
    def _limit(items, avail):
        return avail if not items or max(items) < avail else avail - 1

    def _pill(self, cr, x, y, w, h, occ, selected):
        solid, fill, text = event_colors(self.win.calendar_color(occ))
        if selected:
            source(cr, solid)
            rrect(cr, x, y, w, h, ui.px("r_menu_row"))
            cr.fill()
        source(cr, ui.rgba("label_on_accent") if selected else solid)
        cr.arc(x + 7, y + h / 2, 3.5, 0, 2 * math.pi)
        cr.fill()
        t = layout(self, self.win.fmt_time(occ.start.time()), width=None)
        tw = t.get_pixel_extents()[1].width
        show_time = w > tw + 60
        name = layout(self, occ.event.summary or "New Event", width=w - 18 - (tw + 6 if show_time else 0))
        fg = ui.rgba("label_on_accent") if selected else ui.rgba("label")
        show(cr, name, x + 15, y + (h - text_h(name)) / 2, fg)
        if show_time:
            show(cr, t, x + w - tw - 4, y + (h - text_h(t)) / 2,
                 ui.rgba("label_on_accent") if selected else ui.rgba("label_secondary"))
        self.hits.append((x, y, w, h, occ))


# -- year --------------------------------------------------------------------------------------
class YearView(Canvas):
    """Twelve small months; a click on a day opens it in Day view, on a
    month's name in Month view. Days with events get a soft disc."""

    def __init__(self, win):
        super().__init__(win, vexpand=True, hexpand=True, css_classes=["cal-year"])
        self.busy = set()
        self.cells = []           # [(x, y, w, h, date or month-date, kind)]
        _clicks(self, self._pressed)
        sc = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL |
                                       Gtk.EventControllerScrollFlags.DISCRETE)
        sc.connect("scroll", lambda _c, _dx, dy: (self.win.step(1 if dy > 0 else -1), True)[1])
        self.add_controller(sc)

    def range(self):
        y = self.win.date.year
        return dt.datetime(y, 1, 1), dt.datetime(y + 1, 1, 1)

    def refresh(self):
        a, b = self.range()
        busy = set()
        for o in self.win.occurrences(a, b):
            d = o.start.date()
            last = (o.end - dt.timedelta(microseconds=1)).date() if o.end > o.start else d
            while d <= last and d.year == a.year:
                busy.add(d)
                d += ONE_DAY
        self.busy = busy
        self.queue_draw()

    def _pressed(self, button, n, x, y):
        if button != 1:
            return
        for cx, cy, cw, chh, d, kind in self.cells:
            if cx <= x < cx + cw and cy <= y < cy + chh:
                self.win.go(d, "day" if kind == "day" else "month")
                return

    def paint(self, cr, w, h):
        win = self.win
        self.cells = []
        source(cr, ui.rgba("content_bg"))
        cr.paint()
        ncols = 4 if w >= h * 1.1 else 3
        nrows = 12 // ncols
        pad = 18
        mw, mh = (w - pad * (ncols + 1)) / ncols, (h - pad * (nrows + 1)) / nrows
        today = dt.date.today()
        year = win.date.year
        lab, lab2, red = ui.rgba("label"), ui.rgba("label_secondary"), ui.rgba("destructive")
        for m in range(12):
            mx = pad + (m % ncols) * (mw + pad)
            my = pad + (m // ncols) * (mh + pad)
            first = dt.date(year, m + 1, 1)
            title = layout(self, first.strftime("%B"), "text_title", weight=700)
            show(cr, title, mx + 4, my, red if (year, m + 1) == (today.year, today.month) else lab)
            th = text_h(title) + 6
            self.cells.append((mx, my, mw, th, first, "month"))
            dates = model.month_grid(year, m + 1, win.first_weekday)
            cellw, cellh = mw / 7, (mh - th) / 7
            for c in range(7):
                lay = layout(self, dates[c].strftime("%a")[:1], weight=600, width=cellw,
                             align=Pango.Alignment.CENTER)
                show(cr, lay, mx + c * cellw, my + th + (cellh - text_h(lay)) / 2, lab2)
            for i, d in enumerate(dates):
                if d.month != m + 1:
                    continue
                cx, cy = mx + (i % 7) * cellw, my + th + (i // 7 + 1) * cellh
                r = min(cellw, cellh) / 2 - 1
                if d == today:
                    _today_circle(cr, cx + cellw / 2, cy + cellh / 2, r)
                elif d in self.busy:
                    source(cr, ui.rgba("item_selected_bg"))
                    cr.arc(cx + cellw / 2, cy + cellh / 2, r, 0, 2 * math.pi)
                    cr.fill()
                lay = layout(self, str(d.day), weight=600 if d == today else 400, width=cellw,
                             align=Pango.Alignment.CENTER)
                col = ui.rgba("label_on_accent") if d == today else lab if d.weekday() < 5 else lab2
                show(cr, lay, cx, cy + (cellh - text_h(lay)) / 2, col)
                self.cells.append((cx, cy, cellw, cellh, d, "day"))


# -- sidebar mini month ------------------------------------------------------------------------
class MiniMonth(Gtk.Box):
    """The sidebar's small month: ‹ › change the month shown, a click on a
    day shows it in the main view; the shown day / week is highlighted."""

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, css_classes=["cal-mini"])
        self.win = win
        self.month = win.date.replace(day=1)
        head = Gtk.Box(css_classes=["cal-mini-head"])
        self.title = Gtk.Label(xalign=0, hexpand=True, css_classes=["cal-mini-title"])
        head.append(self.title)
        for icon, step in (("go-previous-symbolic", -1), ("go-next-symbolic", 1)):
            b = Gtk.Button(icon_name=icon, css_classes=["cal-mini-nav"], can_focus=False, valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda _b, s=step: self.shift(s))
            head.append(b)
        self.append(head)
        self.canvas = _MiniCanvas(self)
        self.append(self.canvas)
        self._sync_title()

    def shift(self, step):
        y, m = divmod(self.month.month - 1 + step, 12)
        self.month = dt.date(self.month.year + y, m + 1, 1)
        self._sync_title()
        self.canvas.queue_draw()

    def follow(self):
        """Show the month of the main view's date."""
        self.month = self.win.date.replace(day=1)
        self._sync_title()
        self.canvas.queue_draw()

    def _sync_title(self):
        self.title.set_label(self.month.strftime("%B %Y"))


class _MiniCanvas(Canvas):
    def __init__(self, mini):
        super().__init__(mini.win, content_height=7 * 22, hexpand=True)
        self.mini = mini
        _clicks(self, self._pressed)

    def _pressed(self, button, n, x, y):
        if button != 1:
            return
        w = self.get_width()
        cw, chh = w / 7, self.get_height() / 7
        r, c = int(y // chh) - 1, int(min(6, x // cw))
        if r < 0:
            return
        dates = model.month_grid(self.mini.month.year, self.mini.month.month, self.win.first_weekday)
        d = dates[min(41, r * 7 + c)]
        self.win.go(d, None)

    def paint(self, cr, w, h):
        win = self.win
        dates = model.month_grid(self.mini.month.year, self.mini.month.month, win.first_weekday)
        cw, chh = w / 7, h / 7
        lab, lab2, lab3 = ui.rgba("label"), ui.rgba("label_secondary"), ui.rgba("label_tertiary")
        today = dt.date.today()
        for c in range(7):
            lay = layout(self, dates[c].strftime("%a")[:2], weight=600, width=cw, align=Pango.Alignment.CENTER,
                         scale=0.9)
            show(cr, lay, c * cw, (chh - text_h(lay)) / 2, lab2)
        # the shown period: a soft band (week) or disc (day)
        view, focus = win.view, win.date
        for i, d in enumerate(dates):
            cx, cy = (i % 7) * cw, (i // 7 + 1) * chh
            shown = (view == "day" and d == focus) or \
                    (view == "week" and model.week_start(d, win.first_weekday) ==
                     model.week_start(focus, win.first_weekday))
            if shown:
                source(cr, ui.rgba("sidebar_selected"))
                if view == "week":
                    if i % 7 == 0:
                        rrect(cr, cx + 1, cy + 1, cw * 7 - 2, chh - 2, (chh - 2) / 2)
                        cr.fill()
                else:
                    rrect(cr, cx + (cw - chh) / 2 + 1, cy + 1, chh - 2, chh - 2, (chh - 2) / 2)
                    cr.fill()
        for i, d in enumerate(dates):
            cx, cy = (i % 7) * cw, (i // 7 + 1) * chh
            if d == today:
                _today_circle(cr, cx + cw / 2, cy + chh / 2, chh / 2 - 1)
            lay = layout(self, str(d.day), weight=600 if d == today else 400, width=cw,
                         align=Pango.Alignment.CENTER, scale=0.95)
            col = ui.rgba("label_on_accent") if d == today else lab if d.month == self.mini.month.month else lab3
            show(cr, lay, cx, cy + (chh - text_h(lay)) / 2, col)
