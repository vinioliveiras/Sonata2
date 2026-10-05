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
.share-panel { background: %(panel_material)s; border-radius: calc(%(r_dialog)s * 1.17); padding: 18px 18px 14px 18px; margin: 30px;
  box-shadow: 0 0 0 0.5px %(hairline)s, inset 0 0 0 0.5px %(highlight)s, 0 18px 50px rgba(0,0,0,0.32);
  color: %(label)s; font-family: %(font)s; font-size: %(text_body)s; }
.share-panel .share-title { font-weight: 700; }
.share-panel .share-text { color: %(label_secondary)s; font-size: %(text_small)s; }
.share-item { padding: 8px; border-radius: 10px; }
.share-item:hover { background: alpha(%(label)s, 0.06); }
.share-item.selected { background: alpha(%(accent)s, 0.22); }
.share-item .share-thumb { border-radius: 6px; box-shadow: 0 0 0 0.5px %(hairline)s, 0 2px 6px rgba(0,0,0,0.25); }
.share-item .share-name { font-size: %(text_small)s; margin-top: 6px; }
.share-item .share-badge { margin-bottom: 4px; -gtk-icon-shadow: 0 2px 3px rgba(0,0,0,0.3); }
@keyframes share-in { from { opacity: 0; transform: scale(0.95); } to { opacity: 1; transform: none; } }
.share-panel { animation: share-in 180ms cubic-bezier(0.2, 0.8, 0.2, 1) both; }
""", key="share-picker")


def _parse(line: str):
    """(kind, name to show, output name / toplevel identifier for the thumbnail).
    xdg-desktop-portal-wlr: "Monitor: HDMI-A-1 Acer KG241Y ...", "Window: <title> (<identifier>)"."""
    if line.startswith("Monitor: "):
        name = line[len("Monitor: "):].strip().split(" ", 1)[0]
        return "screen", name, name
    if line.startswith("Window: "):
        rest = line[len("Window: "):].strip()
        if rest.endswith(")") and " (" in rest:
            title, ident = rest[:-1].rsplit(" (", 1)
            return "window", title.strip() or "Window", ident.strip()
        if ": " in rest:                            # older versions: "Window: <id>: <title>"
            return "window", rest.split(": ", 1)[1].strip() or "Window", None
        return "window", "Window", rest or None     # only the identifier
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


THUMB_H = 135
# the panel around the choices: its margin and padding, the title, the buttons
CHROME_H = 200


def grid_max_height(screen_h: int) -> int:
    """The tallest the choices may get before they scroll: many windows
    to share on a 720 px laptop pushed Cancel / Share off the screen."""
    return max(THUMB_H + 40, int(screen_h) - CHROME_H)


def fit_texture(pb):
    """A pixbuf scaled to fit a picker item (never stretched); None stays None."""
    if pb is None:
        return None
    w, h = pb.get_width(), pb.get_height()
    if w <= 0 or h <= 0:
        return None
    scale = min(THUMB_W / w, THUMB_H / h)
    return Gdk.Texture.new_for_pixbuf(pb.scale_simple(max(1, round(w * scale)), max(1, round(h * scale)),
                                                      GdkPixbuf.InterpType.BILINEAR))


def _grim_texture(args):
    try:
        png = subprocess.run(["grim"] + args + ["-"], capture_output=True, timeout=4).stdout
        loader = GdkPixbuf.PixbufLoader.new_with_type("png")
        loader.write(png)
        loader.close()
        return fit_texture(loader.get_pixbuf())
    except (OSError, subprocess.SubprocessError, GLib.Error, AttributeError):
        return None


def _thumb(output):
    if not output or not shutil.which("grim"):
        return None
    return _grim_texture(["-o", output, "-s", "0.25"])


def _window_thumb(ident):
    """A window's own picture (grim -T: the toplevel capture xdg-desktop-portal-wlr shares), even when covered."""
    if not ident or not shutil.which("grim"):
        return None
    return _grim_texture(["-T", ident, "-s", "0.25"])


def _views_by_title() -> dict:
    """{title: app id} of the open windows (the app's icon on a window's thumbnail)."""
    try:
        from .capture import _ipc
        return {v.get("title"): v.get("app-id") for v in _ipc().call("window-rules/list-views") or []
                if isinstance(v, dict) and v.get("title")}
    except Exception:
        return {}


def _textures(jobs):
    """[fn(arg)...] at once (one grim per window: six windows waited 1 s one by one)."""
    from concurrent.futures import ThreadPoolExecutor
    if not jobs:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(jobs))) as ex:
        return list(ex.map(lambda j: j[0](j[1]), jobs))


class Picker(Gtk.Window):
    """A glass sheet in the middle of the screen: a thumbnail and a name
    per choice, the first one selected; action / Cancel, Return / Escape,
    arrows, double-click. items: [{"name", "texture" | "icon", "badge"
    (a Gio.Icon over the thumbnail's corner), "value"}]; on_done(value or None)."""

    def __init__(self, app, title, text, items, action, on_done):
        super().__init__(application=app, title=title, decorated=False, resizable=False)
        self.add_css_class("sonata-share")
        self.values, self.index, self.on_done = [it["value"] for it in items], 0, on_done
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, css_classes=["share-panel"],
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        ui.theme.glass_class(panel)
        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        head.append(Gtk.Label(label=title, xalign=0, css_classes=["share-title"]))
        if text:
            head.append(Gtk.Label(label=text, xalign=0, css_classes=["share-text"]))
        panel.append(head)
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=max(1, min(3, len(items))),
                           min_children_per_line=1, homogeneous=True, column_spacing=8, row_spacing=8)
        self.items = []
        for i, it in enumerate(items):
            item = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["share-item"])
            if it.get("texture") is not None:
                pic = Gtk.Picture(paintable=it["texture"], can_shrink=False, css_classes=["share-thumb"],
                                  overflow=Gtk.Overflow.HIDDEN, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
                art = Gtk.Overlay(child=Gtk.Box(width_request=THUMB_W, height_request=THUMB_H))
                art.add_overlay(pic)
            else:
                art = Gtk.Overlay(child=Gtk.Image(icon_name=it.get("icon") or "window-symbolic", pixel_size=64,
                                                  width_request=THUMB_W, height_request=THUMB_H))
            if it.get("badge") is not None:                 # the app's icon, like macOS' window picker
                art.add_overlay(Gtk.Image(gicon=it["badge"], pixel_size=40, halign=Gtk.Align.CENTER,
                                          valign=Gtk.Align.END, css_classes=["share-badge"]))
            item.append(art)
            item.append(Gtk.Label(label=it["name"], css_classes=["share-name"], ellipsize=Pango.EllipsizeMode.END,
                                  max_width_chars=28))
            click = Gtk.GestureClick()
            click.connect("pressed", lambda _g, n, _x, _y, i=i: (self._select(i), n == 2 and self._act()))
            item.add_controller(click)
            grid.append(item)
            self.items.append(item)
        screen = ui.window.screen_size()     # (the smallest display: the compositor picks one)
        self.scroller = Gtk.ScrolledWindow(child=grid, hscrollbar_policy=Gtk.PolicyType.NEVER,
                                           propagate_natural_width=True, propagate_natural_height=True,
                                           max_content_height=grid_max_height(screen[1]) if screen else 520)
        panel.append(self.scroller)
        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel", css_classes=["sonata-button"])
        cancel.connect("clicked", lambda *_: self._finish(None))
        go = Gtk.Button(label=action, css_classes=["sonata-button", "default"])
        go.connect("clicked", lambda *_: self._act())
        buttons.append(cancel)
        buttons.append(go)
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
            self._act()
        elif keyval in (Gdk.KEY_Left, Gdk.KEY_Right) and self.items:
            self._select((self.index + (1 if keyval == Gdk.KEY_Right else -1)) % len(self.items))
        else:
            return False
        return True

    def _act(self):
        self._finish(self.values[self.index] if self.values else None)

    def _finish(self, value):
        done, self.on_done = self.on_done, None
        if done is None:
            return
        self.set_visible(False)
        done(value)
        self.destroy()                        # one per use: thumbnails freed, no hidden windows piling up


class SharePicker(Picker):
    """The ScreenCast portal's picker (a process of its own: see main())."""

    def __init__(self, app, lines):
        self.lines, self.result = lines, None
        parsed = [_parse(line) for line in lines]
        textures = _textures([(_thumb if kind == "screen" else _window_thumb, ref) for kind, _l, ref in parsed])
        apps = _views_by_title() if any(kind == "window" for kind, _l, _r in parsed) else {}
        items = []
        for line, (kind, label, _ref), tex in zip(lines, parsed, textures):
            badge = None
            if kind == "window" and apps.get(label):
                from .capture import _app_gicon
                badge = _app_gicon(apps[label])
            items.append({"name": _display_name(label) if kind == "screen" else label,
                          "texture": tex, "badge": badge,
                          "icon": "video-display-symbolic" if kind == "screen" else "window-symbolic",
                          "value": line})
        super().__init__(app, "Choose what to share", "The app will see everything on the screen you pick.",
                         items, "Share", self._done)

    def _done(self, line):
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
