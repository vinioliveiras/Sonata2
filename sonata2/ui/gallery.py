"""Design-system gallery: every Sonata component on one page, to check the
look in light and dark (`python3 -m sonata2 gallery [--dark]`,
`tools/wl-preview.sh gallery --dark --menu 0` for a screenshot)."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import controls, label, menu, register, tokens, window  # noqa: E402

register("""
window.sonata-gallery { background: %(window_bg)s; color: %(label)s; font-family: %(font)s; }
.gallery-title { font-family: %(font_display)s; font-size: %(text_title)s; font-weight: 700; }
.gallery-caption { font-size: %(text_small)s; color: %(label_secondary)s; }
""", key="gallery")

S = tokens.SPACE


def _row(caption: str, *widgets) -> Gtk.Box:
    box = Gtk.Box(spacing=S["l"], valign=Gtk.Align.CENTER)
    cap = Gtk.Label(label=caption, xalign=0, width_chars=14, css_classes=["gallery-caption"])
    box.append(cap)
    for w in widgets:
        box.append(w)
    return box


def sample_menu(anchor) -> Gtk.PopoverMenu:
    Item = menu.Item
    return menu.popup(anchor, [
        [Item("Window title")],
        [Item("Options", submenu=[[Item("Keep in Dock", checked=True), Item("Open at Login", checked=False)],
                                  [Item("Open File Location")]])],
        [Item("Hide"), Item("Quit"), Item("Disabled item", enabled=False)],
    ], position=Gtk.PositionType.BOTTOM, offset=4)


class GalleryWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Sonata 2 preview", css_classes=["sonata-gallery"],
                         default_width=960, default_height=420, decorated=False)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=S["l"],
                      margin_top=S["xl"], margin_bottom=S["xl"], margin_start=S["xxl"], margin_end=S["xxl"])
        head = Gtk.Box(spacing=S["l"])
        head.append(window.traffic_lights(lambda: None, lambda: None, lambda: None))
        head.append(Gtk.Label(label="Sonata design system", css_classes=["gallery-title"]))
        col.append(head)

        col.append(_row("Push buttons", controls.push_button("Cancel"),
                        controls.push_button("OK", style="default"),
                        controls.push_button("Empty Trash", style="destructive"),
                        _disabled(controls.push_button("Disabled"))))
        col.append(_row("Pop-up button", controls.popup_button(["Automatic", "Light", "Dark"])))
        col.append(_row("Switches", controls.switch(True), controls.switch(False)))
        tip_anchor = Gtk.Label(label="Hover label anchor")
        self.tip = label.HoverLabel(tip_anchor, "Firefox", hover=False)
        col.append(_row("Hover label", tip_anchor))
        self.menu_anchor = controls.push_button("Right-click menu", lambda: sample_menu(self.menu_anchor))
        col.append(_row("Menu", self.menu_anchor))
        col.append(_row("Traffic lights", window.traffic_lights(lambda: None, lambda: None),
                        Gtk.Label(label="(zoom disabled)", css_classes=["gallery-caption"])))
        self.set_child(col)
        self.connect("map", lambda *_: GLib.timeout_add(300, lambda: (self.tip.popup(), False)[1]))


def _disabled(w):
    w.set_sensitive(False)
    return w
