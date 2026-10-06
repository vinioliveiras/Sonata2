"""Control Center > Keyboard (Vini; Add Controls): the input sources in a
row -- the one in use in the accent, a click switches (as the menu bar's
input menu and Ctrl+Space do). With one source, a button to add more.

    module(bar)      # bar: the menu bar (its _use_layout / _update_input)"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from ..backend import system  # noqa: E402

ui.register("""
.cc-kbd { padding-top: 5px; padding-bottom: 5px; }
.cc-kbd .cc-kbd-name { font-size: %(text_small)s; color: %(label_secondary)s; }
.cc-kbd .cc-kbd-add { min-height: 0; padding: 0 8px; font-size: %(text_small)s; border-radius: 6px; }
""", key="kbdmodule")


class KeyboardModule(Gtk.Box):
    def __init__(self, bar=None, close=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3, css_classes=["panel-module", "cc-kbd"])
        self.bar, self.close = bar, close
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label="Keyboard", xalign=0, css_classes=["panel-module-title"]))
        self.name = Gtk.Label(xalign=1, hexpand=True, css_classes=["cc-kbd-name"],
                              ellipsize=Pango.EllipsizeMode.END, width_chars=1)
        head.append(self.name)
        self.append(head)
        self.order = system.keyboard_layouts()          # as they were when shown: they don't jump around
        names = dict(system.XKB_LAYOUTS)
        from .topbar import layout_badge
        self.seg = ui.controls.segmented([(l, layout_badge(l)) for l in self.order], self.order[0] if self.order
                                         else None, self.pick, tooltips={l: names.get(l, l) for l in self.order})
        row = Gtk.Box(spacing=6)
        self.seg.set_hexpand(True)
        row.append(self.seg)
        self.add_btn = Gtk.Button(label="Add…", css_classes=["cc-kbd-add"], can_focus=False,
                                  valign=Gtk.Align.CENTER, tooltip_text="Add an input source (Keyboard settings)",
                                  visible=len(self.order) < 2)
        self.add_btn.connect("clicked", lambda _b: self._settings())
        row.append(self.add_btn)
        self.append(row)
        self.show_current(self.order[0] if self.order else "")

    def show_current(self, layout: str) -> None:
        self.seg.select(layout)
        self.name.set_label(dict(system.XKB_LAYOUTS).get(layout, layout))

    def pick(self, layout: str) -> None:
        """That layout in use now (the first of the list, as Wayfire wants)."""
        now = system.keyboard_layouts()
        if layout not in now or now[0] == layout:
            return
        self.show_current(layout)
        if self.bar is not None and hasattr(self.bar, "_use_layout"):
            self.bar._use_layout(now.index(layout))       # the menu bar's badges follow
        else:
            now.insert(0, now.pop(now.index(layout)))
            system.run_async(system.set_keyboard_layouts, None, now)

    def _settings(self) -> None:
        if self.close:
            self.close()
        from .topbar import open_settings
        open_settings("keyboard")


def module(bar=None, close=None) -> Gtk.Widget:
    return KeyboardModule(bar, close)
