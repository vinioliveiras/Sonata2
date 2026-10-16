"""Desktops in Mission Control (Vini: Win+Tab shows the windows like Mission
Control, and more desktops can be made, as on Windows).

Wayfire's `scale` lays the windows out (Super+Tab, Ctrl+Up, F3, three
fingers up); while it does, this bar sits centred at the top of the
display: one small picture per desktop (the wallpaper, the icons of the
apps on it), its name under it -- the current one in a glass pill -- and a
"+" beside them, centred on the pictures. Always there while Mission
Control is (Vini: not only under the pointer), in room scale keeps free for
it ([scale] outer_margin = SCALE_MARGIN): it never covers a window.

Click a desktop to go there (Mission Control closes, as on macOS); + adds
one; x on a picture (under the pointer) closes one -- its windows go to the
desktop before it, and desktop 1's apps close instead (there is none before
it). One desktop to start with (core/vwidth = 1); the ones you add are kept
in Sonata's Wayfire config. Scale's input grab sits under
the top layers (grab_input(WORKSPACE)), so the bar takes clicks meanwhile.

Switching: scale holds the output, so vswitch can't animate a switch while
it's open -- going to a desktop closes Mission Control first (scale/toggle),
then vswitch slides there; closing a desktop moves the windows with
vswitch/send-view and keeps Mission Control open on the right one
(sonata/set-workspace, Sonata's plugin; without it, the vswitch way).
The arithmetic is plain functions (desktop_of, plan_close), tested without
a compositor."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from .. import apps, icons, ui  # noqa: E402
from ..i18n import _  # noqa: E402
from ..wl.wfipc import WayfireIPC  # noqa: E402
from . import layer  # noqa: E402

MAX_DESKTOPS = 9
THUMB_W, THUMB_H = 128, 80
ICONS_PER_DESKTOP = 5
BAR_GAP = 10                       # below the menu bar, and below the bar
PILL_H = 22
ADD_SIZE = 30
RING = 4                           # room around a picture for its ring (the bar would clip it)
BAR_H = BAR_GAP + RING + THUMB_H + RING + 4 + PILL_H
# what scale keeps free around the windows (config/wayfire.ini [scale] outer_margin):
# the whole bar and a gap under it, so it never covers a window
SCALE_MARGIN = BAR_H + BAR_GAP

ui.register("""
window.sonata-spaces, window.sonata-spaces > contents { background: none; box-shadow: none; }
.sp-row { padding: %(gap)dpx 16px 0 16px; }
.sp-desk { background: none; border: none; box-shadow: none; padding: 0; min-height: 0; min-width: 0; }
.sp-name { color: %(label)s; font-family: %(font)s; font-size: %(text_small)s; font-weight: 500;
  min-height: %(pill_h)dpx; padding: 0 10px; border-radius: 7px; margin-top: 4px;
  text-shadow: 0 1px 2px rgba(0,0,0,0.25); transition: background-color %(t_fast)s ease-out; }
.sp-desk:hover .sp-name { background: alpha(%(label)s, 0.10); }
.sp-desk.current .sp-name { background: %(glass_tint)s; text-shadow: none;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, %(shadow_control)s; }
.sp-desk.current .sp-name.solid { background: %(menu_bg)s; }
.sp-thumb { border-radius: 8px; box-shadow: 0 0 0 0.5px %(hairline)s, 0 2px 6px rgba(0,0,0,0.25);
  transition: box-shadow %(t_fast)s ease-out; }
.sp-desk.current .sp-thumb { box-shadow: 0 0 0 2.5px %(accent)s, 0 2px 6px rgba(0,0,0,0.25); }
.sp-desk:hover .sp-thumb { box-shadow: 0 0 0 2px alpha(white, 0.7), 0 2px 6px rgba(0,0,0,0.25); }
.sp-icons { margin: 0 0 6px 0; }
.sp-close { min-width: 20px; min-height: 20px; padding: 0; margin: -4px 0 0 -4px; border-radius: 99px;
  background: %(menu_bg)s; color: %(label)s; border: none; opacity: 0;
  box-shadow: 0 0 0 0.5px %(hairline)s, %(shadow_control)s; transition: opacity %(t_fast)s; }
.sp-close image { -gtk-icon-size: 10px; }
.sp-desk-box:hover .sp-close, .sp-close:hover { opacity: 1; }
/* New Desktop: a bare plus in a soft disc under the pointer, centred on the pictures */
.sp-add { min-width: %(add)dpx; min-height: %(add)dpx; padding: 0; border-radius: 99px; border: none;
  box-shadow: none; background: alpha(%(label)s, 0.08); color: %(label)s;
  transition: background-color %(t_fast)s ease-out, transform %(t_press)s ease-out; }
.sp-add image { -gtk-icon-size: 18px; }
.sp-add:hover { background: alpha(%(label)s, 0.16); }
.sp-add:active { background: alpha(%(label)s, 0.22); transform: scale(0.94); }
@keyframes sp-in { from { opacity: 0; transform: translateY(-8px); } to { opacity: 1; transform: none; } }
.sp-row.opening { animation: sp-in 200ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
""", key="spaces", gap=BAR_GAP, pill_h=PILL_H, add=ADD_SIZE)


# -- the arithmetic ---------------------------------------------------------------------
def desktop_of(view: dict, out: dict) -> int:
    """Which desktop (column) a window is on: its centre, from the current
    one, in display widths."""
    g, og = view.get("geometry") or {}, out.get("geometry") or {}
    width = og.get("width") or 1
    cur = (out.get("workspace") or {}).get("x", 0)
    centre = g.get("x", 0) + g.get("width", 0) / 2
    return cur + int(centre // width)


def plan_close(index: int, count: int, views: list, current: int):
    """Closing desktop `index` of `count` (views: [(view id, desktop)]):
    ([(view id, its desktop after)], the desktop to show, the new count).
    Its windows go to the one before it (the first's: to the next one,
    which becomes the first); the desktops after it move one to the left.
    Every window goes straight to where it ends, then the last column goes."""
    if count <= 1 or not 0 <= index < count:
        return [], current, count
    moves = []
    for vid, desk in views:
        if desk == index:
            moves.append((vid, max(0, index - 1)))
        elif desk > index:
            moves.append((vid, desk - 1))
    if current > index or (current == index and index > 0):
        current -= 1
    return moves, max(0, min(current, count - 2)), count - 1


class _Thumb(Gtk.Widget):
    """A desktop's picture: the wallpaper cropped to THUMB_W x THUMB_H (cover),
    rounded -- a fixed size, whatever the wallpaper's (a Gtk.Picture asks for
    the image's own proportions and stretches the row)."""

    def __init__(self, paintable):
        super().__init__(css_classes=["sp-thumb"], overflow=Gtk.Overflow.HIDDEN)
        self.paintable = paintable

    def do_measure(self, orientation, _for_size):
        n = THUMB_W if orientation == Gtk.Orientation.HORIZONTAL else THUMB_H
        return n, n, -1, -1

    def do_snapshot(self, snap):
        w, h, p = self.get_width(), self.get_height(), self.paintable
        if p is None:
            return
        pw, ph = p.get_intrinsic_width() or w, p.get_intrinsic_height() or h
        k = max(w / pw, h / ph)
        sw, sh = pw * k, ph * k
        bounds, clip = Graphene.Rect().init(0, 0, w, h), Gsk.RoundedRect()    # (kept: the clip
        clip.init_from_rect(bounds, 8)                                        # reads them later)
        snap.push_rounded_clip(clip)
        area = Graphene.Rect().init((w - sw) / 2, (h - sh) / 2, sw, sh)
        if isinstance(p, Gdk.Texture):
            snap.append_texture(p, area)
        else:
            snap.save()
            snap.translate(area.get_top_left())
            p.snapshot(snap, sw, sh)
            snap.restore()
        snap.pop()


# -- Wayfire --------------------------------------------------------------------------------
def _outputs(ipc) -> list:
    got = ipc.call("window-rules/list-outputs")
    return got if isinstance(got, list) else []


def _views(ipc) -> list:
    got = ipc.call("window-rules/list-views")
    return [v for v in (got if isinstance(got, list) else [])
            if v.get("type") == "toplevel" and v.get("mapped", True) and not v.get("sticky")]


def set_count(n: int) -> None:
    from .. import wfconfig
    wfconfig.wayfire_set("core", "vwidth", max(1, min(MAX_DESKTOPS, int(n))))
    wfconfig.wayfire_set("core", "vheight", 1)


class SpacesBar(Gtk.Window):
    """One display's desktops bar (shown while scale lays its windows out)."""

    def __init__(self, app, monitor, connector: str, wallpaper):
        super().__init__(application=app, title="Desktops", decorated=False, resizable=False)
        self.add_css_class("sonata-spaces")
        self.connector, self.wallpaper = connector, wallpaper
        self.ipc = WayfireIPC()
        self.count = 1
        self.row = Gtk.Box(spacing=14, halign=Gtk.Align.CENTER, valign=Gtk.Align.START, css_classes=["sp-row"])
        self.desks = Gtk.Box(spacing=14)
        self.row.append(self.desks)
        self.add = Gtk.Button(icon_name="list-add-symbolic", css_classes=["sp-add"], valign=Gtk.Align.START,
                              tooltip_text=_("New Desktop"), can_focus=False,
                              margin_top=RING + (THUMB_H - ADD_SIZE) // 2)      # centred on the pictures
        self.add.connect("clicked", lambda _b: self.add_desktop(self.count))
        self.row.append(self.add)
        self.set_child(self.row)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-spaces")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_anchor(self, LS.Edge.TOP, True)       # (top only: centred on the display, as wide as it needs)
            LS.set_exclusive_zone(self, -1)
            LS.set_keyboard_mode(self, LS.KeyboardMode.NONE)
            if monitor is not None:
                LS.set_monitor(self, monitor)

    def _out(self):
        return next((o for o in _outputs(self.ipc) if o.get("name") == self.connector), None)

    def show_bar(self) -> None:
        self.refresh()
        out = self._out()
        LS = layer.layer_shell()
        if LS and out:
            top = (out.get("workarea") or {}).get("y", 0) - (out.get("geometry") or {}).get("y", 0)
            LS.set_margin(self, LS.Edge.TOP, max(0, top))
        self.row.remove_css_class("opening")
        self.present()
        self.row.add_css_class("opening")

    def children(self) -> list:
        out, c = [], self.desks.get_first_child()
        while c is not None:
            out.append(c)
            c = c.get_next_sibling()
        return out

    def refresh(self) -> None:
        out = self._out()
        while self.desks.get_first_child():
            self.desks.remove(self.desks.get_first_child())
        if out is None:
            return
        ws = out.get("workspace") or {}
        count, current = max(1, ws.get("grid_width", 1)), ws.get("x", 0)
        self.count = count
        per = {i: [] for i in range(count)}
        for v in _views(self.ipc):
            if v.get("output-id") == out.get("id"):
                d = desktop_of(v, out)
                if 0 <= d < count and v.get("app-id") not in per[d]:
                    per[d].append(v.get("app-id"))
        for i in range(count):
            self.desks.append(self._desktop(i, i == current, per[i], count))
        self.add.set_visible(count < MAX_DESKTOPS)

    def _desktop(self, i, current, app_ids, count) -> Gtk.Widget:
        over = Gtk.Overlay(halign=Gtk.Align.CENTER, margin_start=RING, margin_end=RING, margin_top=RING,
                           margin_bottom=RING)
        over.set_child(_Thumb(self.wallpaper))
        row = Gtk.Box(spacing=3, halign=Gtk.Align.CENTER, valign=Gtk.Align.END, css_classes=["sp-icons"])
        for aid in app_ids[:ICONS_PER_DESKTOP]:
            info = apps.lookup(apps.match_app_id(aid) or aid or "")
            img = Gtk.Image(pixel_size=18)
            if info:
                icons.set_image(img, icons.app_icon(info))
            else:
                img.set_from_icon_name("application-x-executable")
            row.append(img)
        over.add_overlay(row)
        name = _("Desktop {n}").format(n=i + 1) if count > 1 else _("Desktop")   # (macOS: one is "Desktop")
        label = ui.theme.glass_class(Gtk.Label(label=name, css_classes=["sp-name"], halign=Gtk.Align.CENTER))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(over)
        col.append(label)
        b = Gtk.Button(child=col, css_classes=["sp-desk"] + (["current"] if current else []), can_focus=False)
        b.connect("clicked", lambda _b: self.go(i))
        box = Gtk.Overlay(css_classes=["sp-desk-box"])            # (the x outside the button: its own click)
        box.set_child(b)
        if count > 1 or (i == 0 and app_ids):            # (the first one alone: x closes its apps)
            x = Gtk.Button(icon_name="window-close-symbolic", css_classes=["sp-close"], halign=Gtk.Align.START,
                           valign=Gtk.Align.START, tooltip_text=_("Close Desktop"), can_focus=False)
            x.connect("clicked", lambda _b: self.close_desktop(i))
            box.add_overlay(x)
        box.desk_button = b
        return box

    # -- actions --------------------------------------------------------------------------
    def go(self, i: int) -> None:
        """That desktop, and out of Mission Control (macOS). Scale closes
        first: while it holds the output vswitch can't switch (Vini: the
        desktops did nothing when clicked)."""
        out = self._out()
        if out is None:
            return
        self.ipc.call("scale/toggle", {"output_id": out["id"]})       # (it's open: this closes it)
        if i != (out.get("workspace") or {}).get("x", 0):
            self.ipc.call("vswitch/set-workspace", {"x": i, "y": 0, "output-id": out["id"]})

    def add_desktop(self, count: int) -> None:
        set_count(count + 1)
        self._refresh_soon(250)                         # (Wayfire reads its config again)

    def switch_here(self, out, x: int) -> None:
        """Show desktop x, Mission Control staying open on it: Sonata's
        plugin sets the workspace itself; without it, close and slide."""
        got = self.ipc.call("sonata/set-workspace", {"x": x, "y": 0, "output-id": out["id"]})
        if not (isinstance(got, dict) and got.get("result") == "ok"):
            self.ipc.call("scale/toggle", {"output_id": out["id"]})
            self.ipc.call("vswitch/set-workspace", {"x": x, "y": 0, "output-id": out["id"]})

    def close_desktop(self, index: int) -> None:
        out = self._out()
        if out is None:
            return
        ws = out.get("workspace") or {}
        count, current = ws.get("grid_width", 1), ws.get("x", 0)
        views = [(v["id"], desktop_of(v, out)) for v in _views(self.ipc) if v.get("output-id") == out["id"]]
        if index == 0:
            # Desktop 1 has none before it: its apps close (Vini) -- each may
            # still ask to save; one that stays goes on to the next desktop
            for vid, desk in views:
                if desk == 0:
                    self.ipc.call("window-rules/close-view", {"id": vid})
            views = [(vid, desk) for vid, desk in views if desk != 0]
            if count <= 1:
                self._refresh_soon(300)
                return
        moves, after, new_count = plan_close(index, count, views, current)
        if new_count == count:
            return
        for vid, desk in moves:                         # every window straight to where it ends
            self.ipc.call("vswitch/send-view", {"view-id": vid, "x": desk, "y": 0})
        if after != current:
            self.switch_here(out, after)                # (before the last column goes: still valid)
        set_count(new_count)                            # the last column goes, empty now
        self._refresh_soon(300)

    def _refresh_soon(self, ms: int = 120) -> None:
        GLib.timeout_add(ms, lambda: (self.get_visible() and self.refresh(), False)[1])
