"""First-run Setup Assistant (macOS Setup Assistant's last screens).

After the first login into Sonata, over the blurred wallpaper (the login
screen's look): a "hello" in several languages, Choose Your Look (Light /
Dark, accent colour), a few Sonata choices, then "Get Started". Every choice
applies at once and stays in Settings. Shown once: setup.json "done".
`sonata2 autostart` starts it (`sonata2 setup`) while it isn't done;
`sonata2 setup --preview` shows it again any time."""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Adw, Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from .. import config, icons, ui  # noqa: E402
from ..backend import system  # noqa: E402
from . import layer  # noqa: E402
from .loginui import Backdrop, wallpaper_texture  # noqa: E402

DEFAULTS = {"done": False}
HELLOS = ("hello", "olá", "hola", "bonjour", "ciao", "hallo", "こんにちは", "안녕하세요", "привет", "你好")
HELLO_MS = 1800

ui.register("""
.su-hello { color: white; font-family: %(font_display)s; font-size: 76px; font-weight: 300;
  letter-spacing: -1px; text-shadow: 0 2px 18px rgba(0,0,0,0.25); }
.su-title { color: white; font-family: %(font_display)s; font-size: 30px; font-weight: 600;
  text-shadow: 0 1px 8px rgba(0,0,0,0.25); }
.su-text { color: rgba(255,255,255,0.82); font-size: %(text_body)s; text-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.su-caption { color: white; font-size: %(text_body)s; font-weight: 600; text-shadow: 0 1px 3px rgba(0,0,0,0.3); }
.su-card { background: none; border: none; box-shadow: none; padding: 6px; border-radius: 14px;
  transition: background-color 180ms ease-out, transform 180ms ease-out; }
.su-card:hover { background: rgba(255,255,255,0.10); }
.su-card:active { transform: scale(0.97); }
.su-card.selected > box > .su-preview { box-shadow: 0 0 0 3px white, 0 8px 24px rgba(0,0,0,0.35); }
.su-preview { border-radius: 10px; box-shadow: 0 0 0 0.5px rgba(255,255,255,0.35), 0 8px 24px rgba(0,0,0,0.3);
  transition: box-shadow 200ms ease-out; }
.su-panel { background: rgba(255,255,255,0.14); border-radius: 14px; padding: 4px 0;
  box-shadow: inset 0 0 0 0.5px rgba(255,255,255,0.22); }
.su-row { padding: 10px 16px; }
.su-row label.su-row-title { color: white; font-size: %(text_body)s; }
.su-row label.su-row-sub { color: rgba(255,255,255,0.7); font-size: %(text_small)s; }
.su-next { min-width: 44px; min-height: 44px; padding: 0; border-radius: 99px; border: none;
  background: rgba(255,255,255,0.22); color: white; box-shadow: inset 0 0 0 0.5px rgba(255,255,255,0.3);
  -gtk-icon-size: 20px; transition: background-color 160ms ease-out, transform 160ms ease-out; }
.su-next:hover { background: rgba(255,255,255,0.34); }
.su-next:active { transform: scale(0.92); }
button.sonata-button.su-start { min-height: 34px; padding: 0 22px; border-radius: 99px; font-weight: 600; }
.su-dot { min-width: 22px; min-height: 22px; padding: 0; margin: 0 4px; border-radius: 99px; border: none;
  box-shadow: inset 0 0 0 0.5px rgba(0,0,0,0.25); transition: box-shadow 160ms ease-out; }
.su-dot.selected { box-shadow: 0 0 0 2px rgba(0,0,0,0.25), 0 0 0 4px white; }
""" + "".join(f".su-dot.{n} {{ background: {c[0]}; }}\n" for n, c in ui.tokens.ACCENTS.items()), key="setup")


def done() -> bool:
    return bool(config.load("setup", DEFAULTS)["done"])


class LookPreview(Gtk.Widget):
    """A small desktop in one appearance: the wallpaper, the menu bar, a
    window and the Dock, in that appearance's tokens."""

    def __init__(self, texture, dark: bool):
        super().__init__(css_classes=["su-preview"], overflow=Gtk.Overflow.HIDDEN)
        self.texture, self.dark = texture, dark
        self.set_size_request(200, 125)

    def do_snapshot(self, snap):
        w, h = self.get_width(), self.get_height()
        t = ui.tokens.palette(self.dark)

        def rgba(key):
            c = Gdk.RGBA()
            c.parse(t[key])
            return c

        def rect(x, y, rw, rh):
            return Graphene.Rect().init(x, y, rw, rh)

        def rounded(r, radius, color):
            rr = Gsk.RoundedRect()
            rr.init_from_rect(r, radius)
            snap.push_rounded_clip(rr)
            snap.append_color(color, r)
            snap.pop()
        if self.texture:
            tw, th = self.texture.get_width(), self.texture.get_height()
            s = max(w / tw, h / th)
            snap.append_texture(self.texture, rect((w - tw * s) / 2, (h - th * s) / 2, tw * s, th * s))
        if self.dark:
            snap.append_color(Gdk.RGBA(red=0, green=0, blue=0, alpha=0.28), rect(0, 0, w, h))
        snap.append_color(rgba("bar_bg"), rect(0, 0, w, 8))                       # menu bar
        win = rect(w * 0.16, h * 0.2, w * 0.56, h * 0.5)                           # a window
        rounded(win, 5, rgba("window_bg"))
        rounded(rect(win.get_x(), win.get_y(), win.get_width() * 0.3, win.get_height()), 5, rgba("solid_tint"))
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
            col = Gdk.RGBA()
            col.parse(c)
            rounded(rect(win.get_x() + 5 + i * 6, win.get_y() + 4, 4, 4), 2, col)
        rounded(rect(w * 0.28, h - 16, w * 0.44, 11), 4, rgba("glass_tint"))  # Dock


class SetupAssistant:
    def __init__(self, app, on_done=None):
        self.app = app
        self.on_done = on_done
        self.tex = wallpaper_texture()
        self.win = Gtk.Window(application=app, decorated=False, title="Setup Assistant")
        self.win.add_css_class("sonata-lock")
        over = Gtk.Overlay()
        over.set_child(Backdrop(self.tex, dim=0.22))
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
                               transition_duration=420, hexpand=True, vexpand=True)
        for name, page in (("hello", self._hello()), ("look", self._look()), ("sonata", self._sonata()),
                           ("ready", self._ready())):
            self.stack.add_named(page, name)
        over.add_overlay(self.stack)
        self.win.set_child(over)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.win.add_controller(keys)
        if not layer.overlay_fullscreen(self.win, "sonata2-setup"):
            self.win.fullscreen()
        self.win.present()

    # -- pages -----------------------------------------------------------------------
    def _page(self, title, text, body=None, back=True, next_to=None):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER, css_classes=["gr-rise"])
        col.append(Gtk.Label(label=title, css_classes=["su-title"]))
        col.append(Gtk.Label(label=text, css_classes=["su-text"], wrap=True, max_width_chars=56,
                             justify=Gtk.Justification.CENTER))
        if body:
            body.set_margin_top(18)
            col.append(body)
        nav = Gtk.Box(spacing=16, halign=Gtk.Align.CENTER, margin_top=28)
        if back:
            b = Gtk.Button(icon_name="go-previous-symbolic", css_classes=["su-next"], tooltip_text="Back")
            b.connect("clicked", lambda *_: self._go(-1))
            nav.append(b)
        if next_to:
            n = Gtk.Button(icon_name="go-next-symbolic", css_classes=["su-next"], tooltip_text="Continue")
            n.connect("clicked", lambda *_: self._go(1))
            nav.append(n)
        col.append(nav)
        return col

    def _hello(self):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.CENTER, css_classes=["gr-fade-in"])
        words = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=700,
                          hhomogeneous=False, interpolate_size=True)
        for i, w in enumerate(HELLOS):
            words.add_named(Gtk.Label(label=w, css_classes=["su-hello"]), str(i))
        col.append(words)
        col.append(Gtk.Label(label="Welcome to Sonata", css_classes=["su-text"]))
        state = {"i": 0}

        def cycle():
            if self.stack.get_visible_child_name() != "hello":
                return True
            state["i"] = (state["i"] + 1) % len(HELLOS)
            words.set_visible_child_name(str(state["i"]))
            return True
        GLib.timeout_add(HELLO_MS, cycle)
        n = Gtk.Button(icon_name="go-next-symbolic", css_classes=["su-next"], tooltip_text="Continue",
                       halign=Gtk.Align.CENTER, margin_top=36)
        n.connect("clicked", lambda *_: self._go(1))
        col.append(n)
        return col

    def _look(self):
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22, halign=Gtk.Align.CENTER)
        cards = Gtk.Box(spacing=24, halign=Gtk.Align.CENTER)
        dark_now = Adw.StyleManager.get_default().get_dark()
        self.cards = {}
        for dark, label in ((False, "Light"), (True, "Dark")):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.append(LookPreview(self.tex, dark))
            box.append(Gtk.Label(label=label, css_classes=["su-caption"]))
            b = Gtk.Button(child=box, css_classes=["su-card"] + (["selected"] if dark == dark_now else []))
            b.connect("clicked", lambda _b, d=dark: self._set_dark(d))
            self.cards[dark] = b
            cards.append(b)
        body.append(cards)
        dots = Gtk.Box(halign=Gtk.Align.CENTER)
        cur = config.load("appearance", icons.APPEARANCE_DEFAULTS)["accent"]
        self.dots = {}
        for name in ui.tokens.ACCENTS:
            d = Gtk.Button(css_classes=["su-dot", name] + (["selected"] if name == cur else []),
                           tooltip_text=name.capitalize())
            d.connect("clicked", lambda _b, n=name: self._set_accent(n))
            self.dots[name] = d
            dots.append(d)
        acc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        acc.append(Gtk.Label(label="Accent Colour", css_classes=["su-caption"]))
        acc.append(dots)
        body.append(acc)
        return self._page("Choose Your Look", "Pick an appearance. You can change it later in Settings > General.",
                          body, next_to=True)

    def _sonata(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["su-panel"], width_request=460)
        app_cfg = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        from . import dock as D
        dock_cfg = config.load("dock", D.DEFAULTS)
        rows = (
            ("Sonata title bars for all apps", "Chrome, VS Code, Firefox and others match Sonata's windows",
             app_cfg.get("system_titlebars", True), self._set_titlebars),
            ("Click an app in the Dock to minimize it", "Its windows go back into the Dock",
             dock_cfg.get("click_minimizes", True), lambda on: config.update("dock", click_minimizes=on)),
            ("Automatically hide the Dock", "It slides in when the pointer reaches the screen edge",
             dock_cfg.get("autohide", False), lambda on: config.update("dock", autohide=on)),
        )
        for i, (title, sub, on, cb) in enumerate(rows):
            if i:
                panel.append(Gtk.Separator(margin_start=16, margin_end=16))
            row = Gtk.Box(spacing=16, css_classes=["su-row"])
            text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
            text.append(Gtk.Label(label=title, xalign=0, css_classes=["su-row-title"]))
            text.append(Gtk.Label(label=sub, xalign=0, css_classes=["su-row-sub"], wrap=True))
            row.append(text)
            sw = Gtk.Switch(active=bool(on), valign=Gtk.Align.CENTER)
            sw.connect("notify::active", lambda s, _p, f=cb: f(s.get_active()))
            row.append(sw)
            panel.append(row)
        return self._page("Make It Yours", "A few of Sonata's choices. Everything else is in Settings.",
                          panel, next_to=True)

    def _ready(self):
        start = Gtk.Button(label="Get Started", css_classes=["sonata-button", "default", "su-start"],
                           halign=Gtk.Align.CENTER)
        start.connect("clicked", lambda *_: self.finish())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.append(start)
        return self._page("You're All Set", "Open Launchpad with the Super key, Search with Super+Space, "
                          "and Mission Control with F3.", box, back=True)

    # -- choices ------------------------------------------------------------------------
    def _set_dark(self, dark):
        for d, b in self.cards.items():
            (b.add_css_class if d == dark else b.remove_css_class)("selected")
        system.run_async(system.set_dark_mode, None, dark)

    def _set_accent(self, name):
        for n, b in self.dots.items():
            (b.add_css_class if n == name else b.remove_css_class)("selected")
        config.update("appearance", accent=name)

    def _set_titlebars(self, on):
        config.update("appearance", system_titlebars=on)
        from .. import titlebars
        system.run_async(titlebars.apply, None, on)

    # -- navigation ----------------------------------------------------------------------
    PAGES = ("hello", "look", "sonata", "ready")

    def _go(self, step):
        i = self.PAGES.index(self.stack.get_visible_child_name()) + step
        if 0 <= i < len(self.PAGES):
            self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT if step > 0
                                           else Gtk.StackTransitionType.SLIDE_RIGHT)
            self.stack.set_visible_child_name(self.PAGES[i])

    def _key(self, _c, keyval, _code, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_Right):
            if self.stack.get_visible_child_name() == "ready":
                self.finish()
            else:
                self._go(1)
            return True
        if keyval in (Gdk.KEY_Left, Gdk.KEY_BackSpace):
            self._go(-1)
            return True
        return False

    def finish(self):
        config.update("setup", done=True)
        self.stack.add_css_class("gr-leave")
        start = GLib.get_monotonic_time()

        def tick(win, _clock):
            t = min(1.0, (GLib.get_monotonic_time() - start) / 420_000)
            win.set_opacity(1 - t * t)
            if t >= 1:
                win.set_visible(False)
                if self.on_done:
                    self.on_done()
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE
        self.win.add_tick_callback(tick)
