"""Mission Control's desktops (Vini: Win+Tab shows the windows like Mission
Control; more desktops can be made, as on Windows): the arithmetic, the bar
built from a fake Wayfire, Super+Tab bound to scale.

    xvfb-run -a python3 -m unittest tests.test_spaces
"""
import configparser
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GDK_BACKEND", "x11")

from sonata2.shell import spaces  # noqa: E402

OUT = {"id": 1, "name": "DP-1", "geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080},
       "workarea": {"x": 0, "y": 30, "width": 1920, "height": 1050},
       "workspace": {"x": 1, "y": 0, "grid_width": 3, "grid_height": 1}}


def view(vid, x, app="org.gnome.Nautilus"):
    return {"id": vid, "type": "toplevel", "mapped": True, "app-id": app, "output-id": 1,
            "geometry": {"x": x, "y": 40, "width": 800, "height": 600}}


class ArithmeticTest(unittest.TestCase):
    def test_desktop_of(self):
        self.assertEqual(spaces.desktop_of(view(1, 100), OUT), 1)        # on the current one
        self.assertEqual(spaces.desktop_of(view(2, -1900), OUT), 0)      # one to the left
        self.assertEqual(spaces.desktop_of(view(3, 1920 + 50), OUT), 2)  # one to the right
        self.assertEqual(spaces.desktop_of(view(4, 1700), OUT), 2)       # mostly on the next

    def test_close_middle(self):
        moves, after, count = spaces.plan_close(1, 3, [(1, 0), (2, 1), (3, 2)], 1)
        self.assertEqual(sorted(moves), [(2, 0), (3, 1)])
        self.assertEqual((after, count), (0, 2))

    def test_close_first(self):
        moves, after, count = spaces.plan_close(0, 3, [(1, 0), (2, 1), (3, 2)], 2)
        self.assertEqual(sorted(moves), [(1, 0), (2, 0), (3, 1)])
        self.assertEqual((after, count), (1, 2))

    def test_close_last_and_nonsense(self):
        self.assertEqual(spaces.plan_close(2, 3, [(5, 2)], 2), ([(5, 1)], 1, 2))
        self.assertEqual(spaces.plan_close(0, 1, [(5, 0)], 0), ([], 0, 1))     # the only one stays
        self.assertEqual(spaces.plan_close(7, 3, [], 0), ([], 0, 3))

    def test_close_before_current(self):
        self.assertEqual(spaces.plan_close(0, 3, [], 2)[1], 1)                  # still the same desktop


class ConfigTest(unittest.TestCase):
    def test_super_tab_is_mission_control(self):
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.read(os.path.join(ROOT, "config", "wayfire.ini"))
        self.assertIn("<super> KEY_TAB", cfg["scale"]["toggle"])
        self.assertEqual(int(cfg["scale"]["outer_margin"]), spaces.SCALE_MARGIN)
        binds = [v for k, v in cfg["command"].items() if k.startswith("binding_")]
        self.assertNotIn("<super> KEY_TAB", binds)
        self.assertIn("<alt> KEY_TAB", binds)                                   # Alt+Tab: as it was


class MigrationTest(unittest.TestCase):
    OLD = """[command]
binding_switcher = <super> KEY_TAB
command_switcher = gdbus call switcher next
binding_switcher_back = <super> <shift> KEY_TAB
command_switcher_back = gdbus call switcher prev
binding_switcher_alt = <alt> KEY_TAB
command_switcher_alt = gdbus call switcher next
binding_term = <super> KEY_T
command_term = terminal

[scale]
toggle = <ctrl> KEY_UP | KEY_F3 | swipe up 3
outer_margin = 28
"""

    def test_kept_config(self):
        from sonata2 import wfconfig
        new = wfconfig.migrate_mission_control(self.OLD, spaces.SCALE_MARGIN)
        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read_string(new)
        self.assertNotIn("binding_switcher", cfg["command"])
        self.assertNotIn("command_switcher_back", cfg["command"])
        self.assertEqual(cfg["command"]["binding_switcher_alt"], "<alt> KEY_TAB")     # Alt+Tab stays
        self.assertEqual(cfg["command"]["command_term"], "terminal")
        self.assertEqual(cfg["scale"]["toggle"], "<super> KEY_TAB | <ctrl> KEY_UP | KEY_F3 | swipe up 3")
        self.assertEqual(int(cfg["scale"]["outer_margin"]), spaces.SCALE_MARGIN)
        self.assertEqual(wfconfig.migrate_mission_control(new, spaces.SCALE_MARGIN), new)   # once

    def test_new_config_untouched(self):
        from sonata2 import wfconfig
        with open(os.path.join(ROOT, "config", "wayfire.ini"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(wfconfig.migrate_mission_control(text, spaces.SCALE_MARGIN), text)

    def test_login_script_margin(self):
        with open(os.path.join(ROOT, "tools", "wayfire-config.sh"), encoding="utf-8") as f:
            self.assertIn(f"migrate_mission_control(text, {spaces.SCALE_MARGIN})", f.read())


class FakeIPC:
    def __init__(self):
        self.calls = []

    def call(self, method, data=None):
        self.calls.append((method, data))
        if method == "window-rules/list-outputs":
            return [OUT]
        if method == "window-rules/list-views":
            return [view(1, -1900), view(2, 100, "org.gnome.Calculator"), view(3, 300), view(4, 2000)]
        return {"result": "ok"}


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class BarTest(unittest.TestCase):
    def setUp(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        from sonata2 import ui
        Adw.init()
        ui.setup()
        self.bar = spaces.SpacesBar(None, None, "DP-1", None)
        self.bar.ipc = FakeIPC()
        self.bar.refresh()

    def children(self):
        out, c = [], self.bar.row.get_first_child()
        while c:
            out.append(c)
            c = c.get_next_sibling()
        return out

    def test_one_name_per_desktop_and_add(self):
        kids = self.children()
        self.assertEqual(len(kids), 3)
        self.assertTrue(kids[1].has_css_class("current"))
        self.assertTrue(self.bar.add.has_css_class("sp-add") and self.bar.add.get_visible())
        self.assertEqual(self.bar.add.get_icon_name(), "list-add-symbolic")
        self.assertEqual(self.bar.strip.get_end_widget(), self.bar.add)            # at the right edge

    def test_pictures_only_on_hover(self):
        kids = self.children()
        self.assertFalse(any(k.revealer.get_reveal_child() for k in kids))       # names only
        self.bar._reveal(True)
        self.assertTrue(all(k.revealer.get_reveal_child() for k in kids))
        self.bar.refresh()                                                         # rebuilt meanwhile: still open
        self.assertTrue(all(k.revealer.get_reveal_child() for k in self.children()))

    def test_one_desktop_is_just_desktop(self):
        from sonata2.i18n import _
        OUT["workspace"]["grid_width"], OUT["workspace"]["x"] = 1, 0
        try:
            self.bar.refresh()
            label = self.children()[0].get_child().get_last_child()
            self.assertEqual(label.get_label(), _("Desktop"))
            self.assertIsNone(self.children()[0].revealer.get_child().get_last_child()
                              .get_css_classes() == ["sp-close"] or None)       # no x on the only one
        finally:
            OUT["workspace"]["grid_width"], OUT["workspace"]["x"] = 3, 1

    def test_thumbnails_paint_the_wallpaper(self):
        """Every picture shows the wallpaper, cropped and rounded, at its fixed
        size (a clip made of temporaries painted the first one empty)."""
        from gi.repository import Gdk, GLib, Graphene, Gtk
        raw = bytes([240, 120, 30, 255]) * (400 * 200)
        wall = Gdk.MemoryTexture.new(400, 200, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(raw), 400 * 4)
        box = Gtk.Box()
        thumbs = [spaces._Thumb(wall) for _ in range(3)]
        for t in thumbs:
            box.append(t)
        win = Gtk.Window(child=box)
        win.present()
        ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + 500_000
        while GLib.get_monotonic_time() < end:
            ctx.iteration(False)
        for t in thumbs:
            self.assertEqual((t.get_width(), t.get_height()), (spaces.THUMB_W, spaces.THUMB_H))
            snap = Gtk.Snapshot()
            Gtk.WidgetPaintable.new(t).get_current_image().snapshot(snap, t.get_width(), t.get_height())
            tex = win.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, t.get_width(),
                                                                                         t.get_height()))
            dl = Gdk.TextureDownloader.new(tex)
            dl.set_format(Gdk.MemoryFormat.R8G8B8A8)
            data, stride = dl.download_bytes()
            px = data.get_data()
            centre = px[48 * stride + 80 * 4: 48 * stride + 80 * 4 + 4]
            self.assertEqual(tuple(centre), (240, 120, 30, 255))
            self.assertLess(px[3], 64)                                               # a rounded corner
        win.destroy()

    def test_close_sends_windows_then_switches(self):
        set_to = []
        spaces.set_count, old = (lambda n: set_to.append(n)), spaces.set_count
        try:
            self.bar.close_desktop(0)
        finally:
            spaces.set_count = old
        sent = [d for m, d in self.bar.ipc.calls if m == "vswitch/send-view"]
        self.assertEqual(sorted((d["view-id"], d["x"]) for d in sent), [(1, 0), (2, 0), (3, 0), (4, 1)])
        self.assertEqual(self.bar.ipc.calls[-1][0], "vswitch/set-workspace")
        self.assertEqual(set_to, [2])


if __name__ == "__main__":
    unittest.main()
