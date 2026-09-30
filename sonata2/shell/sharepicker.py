"""Screen sharing picker (macOS: "Share Screen" in a meeting app).

Apps share the screen through the ScreenCast portal (Chrome, Firefox,
Discord, OBS, Zoom...). xdg-desktop-portal-wlr does the capture and asks
this picker which screen (or window) to share: its choices come on stdin,
one per line ("HDMI-A-1", or "Monitor: HDMI-A-1" / "Window: ...: title"
on newer versions); the chosen line goes back on stdout, nothing when
cancelled. Set up by install.sh in
~/.config/xdg-desktop-portal-wlr/Sonata (chooser_type=dmenu).

A glass sheet in the middle of the screen: a live-looking thumbnail and
the name of every display, the first one selected; Share / Cancel,
Return / Escape, double-click shares."""
import shutil
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from . import layer  # noqa: E402

THUMB_W = 240

ui.register("""
window.sonata-share, window.sonata-share > contents { background: none; box-shadow: none; }
.share-panel { background: %(panel_material)s; border-radius: 14px; padding: 18px 18px 14px 18px; margin: 30px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 18px 50px rgba(0,0,0,0.32);
  color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.share-panel .share-title { font-weight: 700; }
.share-panel .share-text { color: %(label_secondary)s; font-size: %(text_small)s; }
.share-item { padding: 8px; border-radius: 10px; }
.share-item:hover { background: alpha(%(label)s, 0.06); }
.share-item.selected { background: alpha(%(accent)s, 0.22); }
.share-item .share-thumb { border-radius: 6px; box-shadow: 0 0 0 0.5px %(hairline)s, 0 2px 6px rgba(0,0,0,0.25); }
.share-item .share-name { font-size: %(text_small)s; margin-top: 6px; }
@keyframes share-in { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: none; } }
.share-panel { animation: share-in 180ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
""", key="share-picker")


def _parse(line: str):
    """(kind, name to show, output name for the thumbnail)"""
    if line.startswith("Monitor: "):
        name = line[len("Monitor: "):].strip()
        return "screen", name, name
    if line.startswith("Window: "):
        rest = line[len("Window: "):]
        title = rest.split(": ", 1)[1] if ": " in rest else rest
        return "window", title.strip() or "Window", None
    return "screen", line.strip(), line.strip()


def _display_name(connector: str) -> str:
    """"Acer KG241Y" for HDMI-A-1 (the built-in panel: "Built-in Display")."""
    if connector.startswith(("eDP", "LVDS", "DSI")):
        return "Built-in Display"
    mons = Gdk.Display.get_default().get_monitors()
    for i in range(mons.get_n_items()):
        m = mons.get_item(i)
        if m.get_connector() == connector:
            return (m.get_description() or m.get_model() or connector).split(" (")[0]
    return connector


def _thumb(output):
    if not output or not shutil.which("grim"):
        return None
    try:
        png = subprocess.run(["grim", "-o", output, "-s", "0.25", "-"], capture_output=True, timeout=4).stdout
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png)
        loader.close()
        pb = loader.get_pixbuf()
        h = max(1, round(pb.get_height() * THUMB_W / pb.get_width()))
        return Gdk.Texture.new_for_pixbuf(pb.scale_simple(THUMB_W, h, GdkPixbuf.InterpType.BILINEAR))
    except (OSError, subprocess.SubprocessError, GLib.Error, ZeroDivisionError, AttributeError):
        return None


class SharePicker(Gtk.ApplicationWindow):
    def __init__(self, app, lines):
        super().__init__(application=app, title="Share Screen", decorated=False, resizable=False)
        self.add_css_class("sonata-share")
        self.lines, self.index, self.result = lines, 0, None
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["share-panel"])
        ui.theme.glass_class(panel)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head.append(Gtk.Label(label="Choose what to share", xalign=0, css_classes=["share-title"]))
        head.append(Gtk.Label(label="The app will see everything on the screen you pick.", xalign=0,
                              css_classes=["share-text"]))
        panel.append(head)
        row = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=max(1, min(3, len(lines))), min_children_per_line=1,
                          homogeneous=True, column_spacing=8, row_spacing=8)
        self.items = []
        for i, line in enumerate(lines):
            kind, label, output = _parse(line)
            item = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["share-item"])
            tex = _thumb(output) if kind == "screen" else None
            if tex is not None:
                pic = Gtk.Picture(paintable=tex, can_shrink=False, css_classes=["share-thumb"],
                                  overflow=Gtk.Overflow.HIDDEN, halign=Gtk.Align.CENTER)
                item.append(pic)
            else:
                item.append(Gtk.Image(icon_name="video-display-symbolic" if kind == "screen" else "window-symbolic",
                                      pixel_size=64, width_request=THUMB_W, height_request=135))
            name = _display_name(label) if kind == "screen" else label
            item.append(Gtk.Label(label=name, css_classes=["share-name"], ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=28))
            click = Gtk.GestureClick()
            click.connect("pressed", lambda _g, n, _x, _y, i=i: (self._select(i), n == 2 and self._share()))
            item.add_controller(click)
            row.append(item)
            self.items.append(item)
        panel.append(row)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel", css_classes=["sonata-button"])
        cancel.connect("clicked", lambda *_: self._finish(None))
        share = Gtk.Button(label="Share", css_classes=["sonata-button", "default"])
        share.connect("clicked", lambda *_: self._share())
        buttons.append(cancel)
        buttons.append(share)
        panel.append(buttons)
        self.set_child(panel)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        LS = layer.layer_shell()
        if LS:
            LS.init_for_window(self)
            LS.set_namespace(self, "sonata2-share")
            LS.set_layer(self, LS.Layer.OVERLAY)
            LS.set_keyboard_mode(self, LS.KeyboardMode.EXCLUSIVE)
        self.connect("close-request", lambda *_: (self._finish(None), True)[1])
        self._select(0)

    def _select(self, i):
        self.index = i
        for j, it in enumerate(self.items):
            (it.add_css_class if j == i else it.remove_css_class)("selected")

    def _key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self._finish(None)
        elif keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._share()
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and self.items:
            self._select((self.index + (1 if keyval == Gdk.KEY_Right else -1)) % len(self.items))
        else:
            return False
        return True

    def _share(self):
        self._finish(self.lines[self.index] if self.lines else None)

    def _finish(self, line):
        self.result = line
        self.get_application().quit()


def main() -> int:
    lines = [ln.rstrip("\n") for ln in sys.stdin if ln.strip()]
    if not lines:
        return 1
    out, sys.stdout = sys.stdout, sys.stderr        # only the answer may reach the portal
    ui_app = Gtk.Application(application_id="io.github.vinioliveiras.sonata2.share",
                             flags=__import__("gi").repository.Gio.ApplicationFlags.NON_UNIQUE)
    holder = {}

    def activate(app):
        ui.setup()
        holder["w"] = SharePicker(app, lines)
        holder["w"].present()
    ui_app.connect("activate", activate)
    ui_app.run([sys.argv[0]])
    result = holder["w"].result if "w" in holder else None
    if result is None:
        return 1
    out.write(result + "\n")
    out.flush()
    return 0
