"""App switcher (macOS Cmd+Tab): Super+Tab shows the open apps, most
recently used first, as big icons on a glass panel in the middle of the
screen; Tab / Shift+Tab (or the arrows) move, releasing Super switches to
the app (all its windows come forward, minimized ones restored), Q quits
it, Escape cancels. Lives in the menu bar process (it already tracks the
windows); Wayfire's binding reaches it over D-Bus (gdbus: no start-up
cost per key press)."""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from .. import apps, icons, ui  # noqa: E402
from . import layer  # noqa: E402

ICON = 96

ui.register("""
window.sonata-switcher, window.sonata-switcher > contents { background: none; box-shadow: none; }
/* room around the panel for its shadow: a shadow cut by the window's edge
   left hard, blurred triangles under the bottom corners */
.sw-panel { background: %(glass_tint)s; border-radius: 22px; padding: 14px; margin: 24px 40px 56px 40px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 16px 40px rgba(0,0,0,0.28); }
.sw-panel.solid { background: %(menu_bg)s; }
.sw-item { padding: 8px; border-radius: 14px; }
.sw-name { color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; margin-top: 6px; }
@keyframes sw-in { from { opacity: 0; transform: scale(0.94); } to { opacity: 1; transform: none; } }
.sw-panel.opening { animation: sw-in 170ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
.sw-panel.closing { opacity: 0; transform: scale(0.97); transition: opacity 120ms ease-in, transform 120ms ease-in; }
""", key="switcher")

SLIDE_MS = 150          # the selection glides from app to app


class SwitcherPanel(Gtk.Box):
    """The row of apps with the selection drawn behind them, gliding to the
    selected app instead of jumping. The selection is measured on the app
    when drawing (never before the layout exists: that drew a small dot in
    the corner)."""

    def __init__(self):
        super().__init__(spacing=4, css_classes=["sw-panel"])
        self.widget = None       # the selected app
        self.start = None        # where the glide started (x, y, w, h)
        self.t = 1.0             # glide progress
        self._anim = None

    @property
    def sel(self):
        return self.widget

    @sel.setter
    def sel(self, value):        # None: the next select() lands without a glide
        if value is None:
            self.widget, self.start, self.t = None, None, 1.0

    def _rect(self, widget):
        if widget is None or widget.get_parent() is not self:
            return None
        ok, b = widget.compute_bounds(self)
        if not ok or b.get_width() < 2 or b.get_height() < 2:
            return None
        return (b.get_x(), b.get_y(), b.get_width(), b.get_height())

    def select(self, widget, animate: bool) -> None:
        now = self._drawn_rect() if animate else None
        self.widget = widget
        if self._anim:
            self._anim.pause()
        if now is None:
            self.start, self.t = None, 1.0
            self.queue_draw()
            return
        self.start, self.t = now, 0.0

        def step(v):
            self.t = v
            self.queue_draw()
        self._anim = Adw.TimedAnimation.new(self, 0.0, 1.0, SLIDE_MS, Adw.CallbackAnimationTarget.new(step))
        self._anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._anim.play()

    def _drawn_rect(self):
        to = self._rect(self.widget)
        if to is None:
            return None
        if self.start is None or self.t >= 1.0:
            return to
        return tuple(a + (c - a) * self.t for a, c in zip(self.start, to))

    def do_snapshot(self, snap) -> None:
        # the panel's own background/border (CSS), then the selection, then the icons
        r = self._drawn_rect()
        if r:
            rect = Graphene.Rect().init(*r)
            rr = Gsk.RoundedRect()
            rr.init_from_rect(rect, 14)
            snap.push_rounded_clip(rr)
            snap.append_color(_alpha(ui.rgba("label"), 0.16), rect)
            snap.pop()
        child = self.get_first_child()
        while child is not None:
            self.snapshot_child(child, snap)
            child = child.get_next_sibling()


def _alpha(c, a):
    c = c.copy()
    c.alpha = a
    return c


class Switcher(Gtk.Window):
    def __init__(self, app, manager, mru):
        super().__init__(application=app, title="App Switcher", decorated=False, resizable=False)
        self.add_css_class("sonata-switcher")
        self.manager, self.mru = manager, mru       # mru: app keys, most recent first
        self.keys, self.index = [], 0
        self.panel = ui.theme.glass_class(SwitcherPanel())
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        col.append(self.panel)
        self.name = Gtk.Label(css_classes=["sw-name"], ellipsize=Pango.EllipsizeMode.END, max_width_chars=12)
        self.set_child(col)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        keys.connect("key-released", self._released)
        self.add_controller(keys)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-switcher")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)
            LS.set_exclusive_zone(self, -1)

    # -- driven by the Super+Tab binding ----------------------------------------------------
    def step(self, direction: int) -> None:
        if not self.get_visible() or self.panel.has_css_class("closing"):
            self._open()
            direction = 1 if len(self.keys) > 1 else 0      # the previous app is preselected
            self.index = 0
        if self.keys:
            self.index = (self.index + direction) % len(self.keys)
            self._mark()

    def _groups(self):
        groups = {}
        for t in self.manager.toplevels:
            key = apps.match_app_id(t.app_id) or t.app_id
            groups.setdefault(key, []).append(t)
        order = [k for k in self.mru if k in groups] + [k for k in groups if k not in self.mru]
        return order, groups

    def _open(self):
        self.keys, self.groups = self._groups()
        while self.panel.get_first_child():
            self.panel.remove(self.panel.get_first_child())
        self.items = []
        for key in self.keys:
            info = apps.lookup(key)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["sw-item"])
            img = Gtk.Image(pixel_size=ICON)
            if info:
                icons.set_image(img, icons.app_icon(info))
            else:
                img.set_from_icon_name("application-x-executable")
            box.append(img)
            box.name = info.get_display_name() if info else key
            self.panel.append(box)
            self.items.append(box)
        if not self.keys:
            return
        self.panel.sel = None                       # opens on the selection, no glide
        self.panel.remove_css_class("closing")
        self.panel.remove_css_class("opening")
        self.present()
        self.panel.add_css_class("opening")

    def _mark(self, animate: bool = True):
        for i, b in enumerate(self.items):
            # the name sits under the selected icon (macOS)
            if b.get_last_child() is self.name:
                b.remove(self.name)
        if self.items:
            self.name.set_label(self.items[self.index].name)
            self.items[self.index].append(self.name)
            item = self.items[self.index]
            # after the layout that moved the name: then glide there
            self.panel.select(item, animate and self.panel.sel is not None)

    # -- keys while open -------------------------------------------------------------------
    def _key(self, _c, keyval, _code, state):
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            self.step(-1 if shift or keyval == Gdk.KEY_ISO_Left_Tab else 1)
        elif keyval in (Gdk.KEY_Right, Gdk.KEY_Left):
            self.step(1 if keyval == Gdk.KEY_Right else -1)
        elif keyval == Gdk.KEY_Escape:
            self._close()
        elif keyval in (Gdk.KEY_q, Gdk.KEY_Q) and self.keys:
            for t in self.groups[self.keys[self.index]]:
                self.manager.close(t)
            self._close()
        elif keyval == Gdk.KEY_Return:
            self._switch()
        return True

    def _released(self, _c, keyval, _code, _state):
        # Super+Tab (macOS Cmd+Tab) and Alt+Tab (Windows): letting go switches
        if keyval in (Gdk.KEY_Super_L, Gdk.KEY_Super_R, Gdk.KEY_Meta_L, Gdk.KEY_Meta_R,
                      Gdk.KEY_Alt_L, Gdk.KEY_Alt_R):
            self._switch()

    def _switch(self):
        if self.get_visible() and self.keys:
            wins = self.groups[self.keys[self.index]]
            for t in [t for t in wins if not t.minimized] or wins:
                self.manager.activate(t)
        self._close()

    def _close(self):
        if not self.get_visible():
            return
        self.panel.remove_css_class("opening")
        self.panel.add_css_class("closing")          # a quick fade, then gone
        GLib.timeout_add(ui.tokens.ms(130), lambda: (self.get_visible() and self.panel.has_css_class("closing")
                                                     and self.set_visible(False), False)[1])
