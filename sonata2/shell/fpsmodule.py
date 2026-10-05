"""Control Center > FPS Limit (Vini: a row of choices, off by default; not in
the default layout -- Add Controls). Games' frame rate through frame-pacer
(fpslimit.py): Off, 30, 60, 90, 120 or Max (the display's refresh rate).
Without frame-pacer the choices are greyed out and the module offers to
install it."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk, Pango  # noqa: E402

from .. import fpslimit, ui  # noqa: E402

LABELS = {"off": "Off", "max": "Max"}

ui.register("""
/* one grid row (56 px): padding 5 + title 19 + 3 + choices 24 + 5 (Vini: it was 59, the bottom cut) */
.cc-fps { padding-top: 5px; padding-bottom: 5px; }
.cc-fps .cc-fps-cap { font-size: %(text_small)s; color: %(label_secondary)s; }
.cc-fps .cc-fps-install { min-height: 0; padding: 0 8px; font-size: %(text_small)s; border-radius: 6px; }
.fps-seg { background: alpha(%(label)s, 0.08); border-radius: 9px; padding: 2px; }
.fps-seg button { min-height: 20px; min-width: 0; padding: 0 6px; border-radius: 7px; background: none;
  box-shadow: none; border: none; color: %(label)s; font-size: 12px; font-weight: 600;
  transition: background-color %(t_fast)s, color %(t_fast)s; }
.fps-seg button:hover { background: alpha(%(label)s, 0.08); }
.fps-seg button.on, .fps-seg button.on:hover { background: %(accent)s; color: %(label_on_accent)s; }
.fps-seg:disabled { opacity: 0.45; }
""", key="fpsmodule")


def refresh_hz() -> float:
    """The fastest display's refresh rate (Max follows it)."""
    best = 0.0
    disp = Gdk.Display.get_default()
    mons = disp.get_monitors() if disp else None
    for i in range(mons.get_n_items() if mons else 0):
        best = max(best, (mons.get_item(i).get_refresh_rate() or 0) / 1000)
    return best


class FpsModule(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=3, css_classes=["panel-module", "cc-fps"])
        head = Gtk.Box(spacing=6)
        head.append(Gtk.Label(label="FPS Limit", xalign=0, hexpand=True, css_classes=["panel-module-title"],
                              ellipsize=Pango.EllipsizeMode.END, width_chars=1))
        self.cap = Gtk.Label(label="games", css_classes=["cc-fps-cap"])
        head.append(self.cap)
        self.install_btn = Gtk.Button(label="Install frame-pacer", css_classes=["cc-fps-install"],
                                      can_focus=False, visible=False)
        self.install_btn.connect("clicked", lambda _b: self._install())
        head.append(self.install_btn)
        self.append(head)
        self.seg = Gtk.Box(css_classes=["fps-seg"], spacing=2, homogeneous=True)
        self.buttons = {}
        for c in fpslimit.CHOICES:
            b = Gtk.Button(label=LABELS.get(c, c), can_focus=False)
            b.connect("clicked", lambda _b, c=c: self.choose(c))
            self.seg.append(b)
            self.buttons[c] = b
        self.append(self.seg)
        self.connect("map", lambda *_: self.update())

    def update(self) -> None:
        ok = fpslimit.installed()
        self.seg.set_sensitive(ok)
        self.install_btn.set_visible(not ok and self.cap.get_label() != "Installing…")
        self.cap.set_visible(ok or self.cap.get_label() == "Installing…")
        cur = fpslimit.get()
        for c, b in self.buttons.items():
            (b.add_css_class if c == cur else b.remove_css_class)("on")
        hz = refresh_hz()
        self.buttons["max"].set_tooltip_text(f"The display's rate ({round(hz)} FPS)" if hz else None)

    def choose(self, c: str) -> None:
        fpslimit.set(c, refresh_hz())
        self.update()

    def _install(self) -> None:
        self.cap.set_label("Installing…")
        self.update()

        def done(err):
            self.cap.set_label("games" if not err else "couldn't install")
            self.cap.set_tooltip_text(err or "Steam picks it up the next time it opens")
            self.update()
        fpslimit.install(done)


def module() -> Gtk.Widget:
    return FpsModule()
