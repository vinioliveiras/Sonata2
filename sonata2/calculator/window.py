"""Calculator (macOS Calculator, Basic): a small glass window, the display on
top (under the compositor's glass title bar), 5 x 4 round-cornered keys:
function keys, digits, orange operators. The keyboard works too:
digits . + - * / x Enter = % Backspace Escape (AC) Delete (C), ⌘C / Ctrl+C
copies the result, Ctrl+V pastes a number. The operator waiting for a
number stays highlighted (white with orange text), as on a Mac."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .. import ui  # noqa: E402
from .engine import Engine  # noqa: E402

APP_ID = "io.github.vinioliveiras.sonata2.calculator"

ui.register("""
window.sonata-calculator { background: %(calc_bg)s; }
.calc-display { padding: 8px 16px 2px 16px; }
.calc-display label { color: %(calc_display)s; font-family: %(font_display)s; font-weight: 300; }
.calc-keys { padding: 6px 8px 10px 8px; }
button.calc-key { min-width: 48px; min-height: 42px; padding: 0; border-radius: 8px; border: none;
  font-size: 20px; font-weight: 400; box-shadow: none; background: %(calc_key)s; color: %(calc_key_text)s;
  transition: background-color 90ms ease-out, filter 90ms ease-out; }
button.calc-key:hover { filter: brightness(1.08); }
button.calc-key:active, button.calc-key.pressed { filter: brightness(1.35); transition: none; }
button.calc-key.fn { background: %(calc_key_fn)s; font-size: 17px; }
button.calc-key.op { background: %(calc_key_op)s; color: white; font-size: 24px; }
button.calc-key.op.armed { background: white; color: %(calc_key_op)s; }
""", key="calculator")

KEYS = (
    (("AC", "fn"), ("±", "fn"), ("%", "fn"), ("÷", "op")),
    (("7", ""), ("8", ""), ("9", ""), ("×", "op")),
    (("4", ""), ("5", ""), ("6", ""), ("−", "op")),
    (("1", ""), ("2", ""), ("3", ""), ("+", "op")),
    (("0", "zero"), (".", ""), ("=", "op")),
)
KEYMAP = {Gdk.KEY_plus: "+", Gdk.KEY_KP_Add: "+", Gdk.KEY_minus: "−", Gdk.KEY_KP_Subtract: "−",
          Gdk.KEY_asterisk: "×", Gdk.KEY_KP_Multiply: "×", Gdk.KEY_x: "×", Gdk.KEY_X: "×",
          Gdk.KEY_slash: "÷", Gdk.KEY_KP_Divide: "÷", Gdk.KEY_equal: "=", Gdk.KEY_Return: "=",
          Gdk.KEY_KP_Enter: "=", Gdk.KEY_percent: "%", Gdk.KEY_period: ".", Gdk.KEY_comma: ".",
          Gdk.KEY_KP_Decimal: ".", Gdk.KEY_KP_Separator: ".", Gdk.KEY_BackSpace: "⌫",
          Gdk.KEY_Escape: "AC", Gdk.KEY_Delete: "C"}


class CalculatorWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Calculator", resizable=False,
                         css_classes=["sonata-calculator", "sonata-glass"])
        ui.window.standard(self)
        self.engine = Engine()
        self.buttons = {}
        over = Gtk.Overlay()
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.display = Gtk.Label(xalign=1, ellipsize=Pango.EllipsizeMode.NONE, selectable=False)
        disp = Gtk.Box(css_classes=["calc-display"])
        disp.append(self.display)
        self.display.set_hexpand(True)
        col.append(Gtk.WindowHandle(child=disp))
        grid = Gtk.Grid(row_spacing=6, column_spacing=6, css_classes=["calc-keys"],
                        row_homogeneous=True, column_homogeneous=True)
        for r, row in enumerate(KEYS):
            c = 0
            for key, kind in row:
                b = Gtk.Button(label=key, css_classes=["calc-key"] + ([kind] if kind in ("fn", "op") else []),
                               can_focus=False)
                b.connect("clicked", lambda _b, k=key: self.press(k))
                width = 2 if kind == "zero" else 1
                grid.attach(b, c, r, width, 1)
                self.buttons[key] = b
                c += width
        col.append(grid)
        over.set_child(col)
        # the title bar is the glass one the compositor draws (pixdecor)
        self.set_child(over)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self._refresh()

    # -- input ----------------------------------------------------------------------------
    def press(self, key: str) -> None:
        if key == "AC":
            key = self.engine.clear_label             # the key reads C or AC
        self.engine.press(key)
        self._refresh()

    def _key(self, _c, keyval, _code, state):
        ctrl = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SUPER_MASK)
        if ctrl and keyval in (Gdk.KEY_c, Gdk.KEY_C):
            self.get_clipboard().set(self.engine.display().replace(",", ""))
            return True
        if ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V):
            self.get_clipboard().read_text_async(None, self._pasted)
            return True
        if ctrl:
            return False
        ch = chr(Gdk.keyval_to_unicode(keyval) or 0)
        key = ch if ch.isdigit() else KEYMAP.get(keyval)
        if key is None:
            return False
        self.press(key)
        shown = {"C": "AC", "⌫": None}.get(key, key)
        if shown in self.buttons:                     # the key lights up, like a click
            b = self.buttons[shown]
            b.add_css_class("pressed")
            GLib.timeout_add(110, lambda: (b.remove_css_class("pressed"), False)[1])
        return True

    def _pasted(self, clip, res):
        try:
            text = clip.read_text_finish(res) or ""
        except GLib.Error:
            return
        text = text.strip().replace(",", "")
        try:
            float(text)
        except ValueError:
            return
        for ch in text:
            self.engine.press("±" if ch == "-" else ch)
        self._refresh()

    # -- output ---------------------------------------------------------------------------
    def _refresh(self):
        text = self.engine.display()
        self.display.set_label(text)
        # the number shrinks to fit (macOS), from 44 px down to 22 px
        size = 44 if len(text) <= 9 else max(22, int(44 * 9 / len(text)))
        attrs = Pango.AttrList()
        attrs.insert(Pango.attr_size_new_absolute(size * Pango.SCALE))
        self.display.set_attributes(attrs)
        self.buttons["AC"].set_label(self.engine.clear_label)
        waiting = self.engine.entry is None and self.engine.tokens and self.engine.tokens[-1]
        for op in ("÷", "×", "−", "+"):
            (self.buttons[op].add_css_class if op == waiting else self.buttons[op].remove_css_class)("armed")


def calculator_desktop_file(command: str) -> str:
    from ..apps import write_desktop_file
    return write_desktop_file(APP_ID + ".desktop",
                              "[Desktop Entry]\nType=Application\nName=Calculator\n"
                              "Comment=Add, subtract, multiply and divide\n"
                              "Icon=accessories-calculator\nCategories=Utility;Calculator;\n"
                              "StartupNotify=true\n"
                              f"Exec={command} calculator\n")
