"""Window buttons sit in the same place on either side
(dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_window_buttons_place).

Regression: with Settings > Appearance > Window buttons on the right, Files
(and Preview's file chooser, which is a Files window) had them centred in
the toolbar -- 16 px lower than on the left -- and 7 px further from the
edge; Settings' were 3 px further in. The close dot's centre is tokens'
dot_left from the side edge and dot_top from the top, on both sides.

Other apps' GTK 3 title bars (adwstyle.css3; Chrome in its GTK mode too):
the dots sat 20 px in and centred in the 46 px bar, 26 px apart, with the
theme's x / - / square drawn over them all the time."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PROBE = r"""
import json, sys
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

def dots(w):
    out, stack = [], [w]
    while stack:
        x = stack.pop()
        if x.has_css_class("tl-close") and x.get_mapped():
            out.append(x)
        c = x.get_first_child()
        while c is not None:
            stack.append(c)
            c = c.get_next_sibling()
    return out

def report():
    got = []
    for win in Gtk.Window.list_toplevels():
        if win.get_mapped():
            for d in dots(win):
                ok, b = d.compute_bounds(win)
                cx, cy = b.get_x() + b.get_width() / 2, b.get_y() + b.get_height() / 2
                got.append({"left": cx, "right": win.get_width() - cx, "top": cy})
    print("PLACES " + json.dumps(got), flush=True)
    sys.exit(0)

GLib.timeout_add(5000, report)
sys.argv = ["sonata2", sys.argv[1]]
from sonata2.__main__ import main
main()
"""


@unittest.skipUnless(os.environ.get("DISPLAY") and shutil.which("dbus-run-session"), "needs a display (xvfb-run)")
class WindowButtonsPlaceTest(unittest.TestCase):
    APPS = ("files", "preview", "settings")

    def test_buttons_on_the_right_sit_where_the_left_ones_do(self):
        from sonata2.ui.tokens import FRAME
        procs = {}
        for side in ("left", "right"):
            for app in self.APPS:
                cfg = tempfile.mkdtemp()
                os.makedirs(os.path.join(cfg, "sonata2"))
                with open(os.path.join(cfg, "sonata2", "appearance.json"), "w") as f:
                    json.dump({"buttons_side": side}, f)
                env = dict(os.environ, XDG_CONFIG_HOME=cfg, XDG_DATA_HOME=tempfile.mkdtemp(),
                           XDG_CACHE_HOME=tempfile.mkdtemp(), GDK_BACKEND="x11", PYTHONPATH=ROOT)
                # each on its own bus: one app instance would hand the others to the first
                procs[side, app] = subprocess.Popen(["dbus-run-session", "--", sys.executable, "-c", PROBE, app],
                                                    cwd=ROOT, env=env,
                                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        end = time.monotonic() + 60
        for (side, app), p in procs.items():
            try:
                out, _err = p.communicate(timeout=max(1, end - time.monotonic()))
            except subprocess.TimeoutExpired:
                p.kill()
                self.fail(f"{app} ({side}) didn't open")
            line = next((ln for ln in out.splitlines() if ln.startswith("PLACES ")), None)
            self.assertIsNotNone(line, f"{app} ({side}): no report")
            places = json.loads(line[len("PLACES "):])
            self.assertTrue(places, f"{app} ({side}): no window buttons")
            for got in places:
                with self.subTest(app=app, side=side):
                    self.assertAlmostEqual(got[side], FRAME["dot_left"], delta=1)
                    self.assertAlmostEqual(got["top"], FRAME["dot_top"], delta=1)


GTK3_PROBE = r"""
import json, os, subprocess, sys
css, layout = subprocess.run([sys.executable, "-c",
    "import os; from sonata2 import adwstyle; from sonata2.ui import tokens; "
    "os.makedirs(adwstyle.runtime_dir(), exist_ok=True); adwstyle.write(True, glass=False); "
    "print(adwstyle.css_path3()); print(tokens.button_layout(tokens.frame()))"],
    capture_output=True, text=True).stdout.split()[-2:]
import gi
gi.require_version("Gtk", "3.0"); gi.require_version("Gdk", "3.0")
from gi.repository import GLib, Gtk, Gdk
prov = Gtk.CssProvider(); prov.load_from_path(css)
Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_USER)
Gtk.Settings.get_default().set_property("gtk-decoration-layout", layout)
win = Gtk.Window(title="GTK 3"); win.set_default_size(500, 300)
hb = Gtk.HeaderBar(show_close_button=True, title="GTK 3"); win.set_titlebar(hb); win.add(Gtk.Label(label="x"))
win.show_all()

def done():
    hx, hy = hb.translate_coordinates(win, 0, 0)
    w = win.get_window()
    pb = Gdk.pixbuf_get_from_window(w, 0, 0, w.get_width(), w.get_height())
    n, rs, px = pb.get_n_channels(), pb.get_rowstride(), pb.get_pixels()
    def blob(test):
        pts = [(x, y) for y in range(60) for x in range(pb.get_width())
               if test(px[y * rs + x * n], px[y * rs + x * n + 1], px[y * rs + x * n + 2])]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return {"x": (min(xs) + max(xs) + 1) / 2 - hx, "y": (min(ys) + max(ys) + 1) / 2 - hy} if pts else None
    close = blob(lambda r, g, b: r > 230 and 70 < g < 110 and 70 < b < 110)
    mini = blob(lambda r, g, b: r > 230 and g > 170 and b < 80)
    # the theme's glyph: dark pixels inside the close dot
    glyph = 0
    if close:
        cx, cy = int(close["x"] + hx), int(close["y"] + hy)
        glyph = sum(1 for y in range(cy - 3, cy + 4) for x in range(cx - 3, cx + 4)
                    if max(px[y * rs + x * n:y * rs + x * n + 3]) < 120)
    print("PLACE " + json.dumps({"close": close, "minimize": mini, "width": hb.get_allocated_width(),
                                 "glyph": glyph}), flush=True)
    Gtk.main_quit()

GLib.timeout_add(1500, done)
Gtk.main()
"""


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class Gtk3TitleButtonsTest(unittest.TestCase):
    def test_gtk3_title_buttons_sit_in_sonatas_place_without_glyphs(self):
        from sonata2.ui.tokens import FRAME
        # one after the other: the picture is read off the screen, where one
        # window would cover the other
        for side in ("left", "right"):
            cfg = tempfile.mkdtemp()
            os.makedirs(os.path.join(cfg, "sonata2"))
            with open(os.path.join(cfg, "sonata2", "appearance.json"), "w") as f:
                json.dump({"buttons_side": side}, f)
            env = dict(os.environ, XDG_CONFIG_HOME=cfg, XDG_RUNTIME_DIR=tempfile.mkdtemp(),
                       XDG_DATA_HOME=tempfile.mkdtemp(), XDG_CACHE_HOME=tempfile.mkdtemp(),
                       GDK_BACKEND="x11", PYTHONPATH=ROOT)
            out = subprocess.run([sys.executable, "-c", GTK3_PROBE], cwd=ROOT, env=env, timeout=60,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True).stdout
            line = next((ln for ln in out.splitlines() if ln.startswith("PLACE ")), None)
            self.assertIsNotNone(line, f"GTK 3 ({side}): no report")
            got = json.loads(line[len("PLACE "):])
            with self.subTest(side=side):
                self.assertIsNotNone(got["close"])
                x = got["close"]["x"] if side == "left" else got["width"] - got["close"]["x"]
                self.assertAlmostEqual(x, FRAME["dot_left"], delta=1)
                self.assertAlmostEqual(got["close"]["y"], FRAME["dot_top"], delta=1)
                # 22: GTK 3's closest to Sonata's 20 (its 16 px icons and 6 px spacing)
                self.assertLessEqual(abs(got["minimize"]["x"] - got["close"]["x"]), 22)
                self.assertEqual(got["glyph"], 0, "the theme's glyph is drawn over the close dot")



@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class CloseDotOnTheRightTest(unittest.TestCase):
    def test_dialog_close_dot_on_the_right_keeps_the_edge_room(self):
        """A dialog's lone close dot (and any .traffic box) on the right kept
        the left side's margin: no room from the right edge."""
        from unittest import mock
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import GLib, Gtk
        from sonata2 import ui
        from sonata2.ui import window as W
        from sonata2.ui.tokens import FRAME
        ui.setup()
        for make in (lambda: W.close_button(lambda: None),
                     lambda: W.traffic_lights(lambda: None, lambda: None, lambda: None)):
            with mock.patch.object(W, "buttons_side", return_value="right"):
                box = make()
            self.assertIn("traffic-right", box.get_css_classes())
            box.set_halign(Gtk.Align.END)
            win = Gtk.Window(default_width=300, default_height=120)
            holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            holder.append(box)
            win.set_child(holder)
            win.present()
            end = GLib.get_monotonic_time() + 800_000
            while GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
            close = next(b for b in [box.get_last_child()] if b.has_css_class("tl-close"))
            ok, r = close.compute_bounds(win)
            self.assertTrue(ok)
            centre = r.get_x() + r.get_width() / 2
            self.assertAlmostEqual(win.get_width() - centre, FRAME["dot_left"], delta=1)
            self.assertAlmostEqual(r.get_y() + r.get_height() / 2, FRAME["dot_top"], delta=1)
            win.destroy()


if __name__ == "__main__":
    unittest.main()
