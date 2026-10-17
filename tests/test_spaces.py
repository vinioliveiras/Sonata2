"""Mission Control's desktops (Vini: Win+Tab shows the windows like Mission
Control; more desktops can be made, as on Windows): the arithmetic, the bar
built from a fake Wayfire, Super+Tab bound to scale.

    xvfb-run -a python3 -m unittest tests.test_spaces
"""
import configparser
import os
import unittest
from unittest import mock

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

    def test_one_desktop_to_start_with(self):
        """Vini: one desktop by default; more only when you add them."""
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.read(os.path.join(ROOT, "config", "wayfire.ini"))
        self.assertEqual((cfg["core"]["vwidth"], cfg["core"]["vheight"]), ("1", "1"))

    def test_room_for_the_whole_bar(self):
        """Vini: the desktops covered the windows -- scale keeps the bar's height free."""
        self.assertGreaterEqual(spaces.SCALE_MARGIN, spaces.BAR_H + spaces.BAR_GAP)
        self.assertGreaterEqual(spaces.BAR_H, spaces.THUMB_H + spaces.PILL_H)


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

[core]
vwidth = 4
vheight = 1

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
        self.assertEqual(cfg["core"]["vwidth"], "1")                                  # the old four: one
        self.assertEqual(wfconfig.migrate_mission_control(new, spaces.SCALE_MARGIN), new)   # once
        again = self.OLD.replace("outer_margin = 28", "outer_margin = 56").replace("vwidth = 4", "vwidth = 3")
        cfg.read_string(wfconfig.migrate_mission_control(again, spaces.SCALE_MARGIN))
        self.assertEqual(int(cfg["scale"]["outer_margin"]), spaces.SCALE_MARGIN)       # the bar's first margin too
        self.assertEqual(cfg["core"]["vwidth"], "3")                                  # one's own choice stays

    def test_new_config_untouched(self):
        from sonata2 import wfconfig
        with open(os.path.join(ROOT, "config", "wayfire.ini"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(wfconfig.migrate_mission_control(text, spaces.SCALE_MARGIN), text)

    def test_login_script_margin(self):
        with open(os.path.join(ROOT, "tools", "wayfire-config.sh"), encoding="utf-8") as f:
            self.assertIn(f"migrate_mission_control(text, {spaces.SCALE_MARGIN})", f.read())


def walk(w):
    out, stack = [], [w]
    while stack:
        x = stack.pop()
        out.append(x)
        c = x.get_last_child()
        while c is not None:
            stack.append(c)
            c = c.get_prev_sibling()
    return out


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
        global Gtk
        from gi.repository import Gtk
        self.bar = spaces.SpacesBar(None, None, "DP-1", None)
        self.bar.ipc = FakeIPC()
        self.bar.refresh()

    def children(self):
        return self.bar.children()

    def test_pictures_names_and_add_centred(self):
        """Vini: the pictures always there, the row centred, + beside them,
        centred on the pictures."""
        kids = self.children()
        self.assertEqual(len(kids), 3)
        self.assertTrue(kids[1].desk_button.has_css_class("current"))
        self.assertEqual(self.bar.row.get_halign(), Gtk.Align.CENTER)
        self.assertIs(self.bar.row.get_last_child(), self.bar.add)                   # in the row, after them
        self.assertEqual(self.bar.add.get_icon_name(), "list-add-symbolic")
        self.assertEqual(self.bar.add.get_margin_top() + spaces.ADD_SIZE // 2,
                         spaces.RING + spaces.THUMB_H // 2)                         # centred on the pictures
        thumbs = [w for w in walk(self.bar.row) if isinstance(w, spaces._Thumb)]
        self.assertEqual(len(thumbs), 3)                                             # not only on hover
        src = open(os.path.join(ROOT, "sonata2", "shell", "spaces.py")).read()
        # as wide as the display (a desktop added or closed doesn't move it:
        # the others glide), the row centred in it
        self.assertIn("for e in (LS.Edge.TOP, LS.Edge.LEFT, LS.Edge.RIGHT):", src)

    def test_one_desktop_is_just_desktop(self):
        from sonata2.i18n import _
        OUT["workspace"]["grid_width"], OUT["workspace"]["x"] = 1, 0
        try:
            self.bar.refresh()
            labels = [w.get_label() for w in walk(self.bar.row) if isinstance(w, Gtk.Label)]
            self.assertEqual(labels, [_("Desktop")])
            closes = [w for w in walk(self.bar.row) if isinstance(w, Gtk.Button) and w.has_css_class("sp-close")]
            self.assertEqual(len(closes), 1)                                         # its apps can be closed
            with mock.patch.object(spaces, "_views", return_value=[]):
                self.bar.refresh()
            closes = [w for w in walk(self.bar.row) if isinstance(w, Gtk.Button) and w.has_css_class("sp-close")]
            self.assertEqual(closes, [])                                             # nothing on it: no x
        finally:
            OUT["workspace"]["grid_width"], OUT["workspace"]["x"] = 3, 1

    def test_go_closes_mission_control_then_switches(self):
        """Vini: the desktops did nothing -- vswitch can't switch while scale
        holds the output; scale closes first."""
        self.bar.ipc.calls.clear()
        self.bar.go(2)
        self.assertEqual([m for m, _d in self.bar.ipc.calls if m != "window-rules/list-outputs"],
                         ["scale/toggle", "vswitch/set-workspace"])
        self.assertEqual(self.bar.ipc.calls[-1][1], {"x": 2, "y": 0, "output-id": 1})
        self.bar.ipc.calls.clear()
        self.bar.go(1)                                                               # the current one: just closes
        self.assertEqual([m for m, _d in self.bar.ipc.calls if m != "window-rules/list-outputs"], ["scale/toggle"])

    def test_close_x_on_the_users_side(self):
        """Vini: Mission Control's buttons follow the user's window buttons."""
        from sonata2.ui import window as W
        for side, align, end in (("left", Gtk.Align.START, False), ("right", Gtk.Align.END, True)):
            with mock.patch.object(W, "buttons_side", return_value=side):
                self.bar.refresh()
            x = [w for w in walk(self.bar.row) if isinstance(w, Gtk.Button) and w.has_css_class("sp-close")]
            self.assertTrue(x)
            self.assertTrue(all(w.get_halign() == align and w.has_css_class("end") == end for w in x), side)

    def test_close_x_works_on_a_picture(self):
        x = [w for w in walk(self.bar.row) if isinstance(w, Gtk.Button) and w.has_css_class("sp-close")]
        self.assertEqual(len(x), 3)
        with mock.patch.object(self.bar, "close_desktop") as close:
            x[2].emit("clicked")
        close.assert_called_once_with(2)

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
            with mock.patch.object(spaces.GLib, "timeout_add", side_effect=lambda ms, fn: fn()):
                self.bar.close_desktop(0)
        finally:
            spaces.set_count = old
        sent = [d for m, d in self.bar.ipc.calls if m == "vswitch/send-view"]
        self.assertEqual(sorted((d["view-id"], d["x"]) for d in sent), [(2, 0), (3, 0), (4, 1)])
        # (Vini: desktop 1 closes its apps -- none before it to take them)
        self.assertEqual([d for m, d in self.bar.ipc.calls if m == "window-rules/close-view"], [{"id": 1}])
        # (Mission Control stays open, on the desktop that was the current one)
        self.assertEqual(self.bar.ipc.calls[-1], ("sonata/set-workspace", {"x": 0, "y": 0, "output-id": 1}))
        self.assertEqual(set_to, [2])

    def test_close_the_only_desktop_closes_its_apps(self):
        OUT["workspace"]["grid_width"], OUT["workspace"]["x"] = 1, 0
        try:
            self.bar.refresh()
            with mock.patch.object(spaces, "set_count") as count:
                self.bar.close_desktop(0)
            closed = sorted(d["id"] for m, d in self.bar.ipc.calls if m == "window-rules/close-view")
            self.assertEqual(closed, [2, 3])                                         # the ones on it
            count.assert_not_called()                                                # the desktop stays
            self.assertNotIn("vswitch/send-view", [m for m, _d in self.bar.ipc.calls])
        finally:
            OUT["workspace"]["grid_width"], OUT["workspace"]["x"] = 3, 1

    def test_close_without_sonatas_plugin(self):
        """An older plugin: the vswitch way (Mission Control closes)."""
        ipc = self.bar.ipc
        real = ipc.call
        ipc.call = lambda m, d=None: (ipc.calls.append((m, d)), {"error": "No such method"})[1] \
            if m == "sonata/set-workspace" else real(m, d)
        with mock.patch.object(spaces, "set_count") as count, \
                mock.patch.object(spaces.GLib, "timeout_add", side_effect=lambda ms, fn: fn()):
            self.bar.close_desktop(0)
        tail = [m for m, _d in ipc.calls[-3:]]
        self.assertEqual(tail, ["sonata/set-workspace", "scale/toggle", "vswitch/set-workspace"])
        count.assert_called_once_with(2)

    def test_a_burst_of_window_events_refreshes_once(self):
        """Performance: each window event rebuilt the bar (and asked Wayfire twice)."""
        from gi.repository import GLib
        self.bar.set_visible(True)
        with mock.patch.object(self.bar, "refresh") as refresh:
            for _ in range(10):
                self.bar._refresh_soon(30)
            ctx, end = GLib.MainContext.default(), GLib.get_monotonic_time() + 300_000
            while GLib.get_monotonic_time() < end:
                ctx.iteration(False)
        refresh.assert_called_once()
        self.bar.set_visible(False)

    # -- Vini: creating and deleting desktops had no animation
    def test_a_new_desktop_grows_in_the_others_glide(self):
        with mock.patch.object(spaces, "set_count"), mock.patch.object(spaces.GLib, "timeout_add"):
            self.bar.add_desktop(3)
        old = self.children()
        OUT["workspace"]["grid_width"] = 4
        try:
            with mock.patch.object(spaces.ui.transition, "glide_record",
                                   return_value={w: (i * 10.0, 0.0, None) for i, w in enumerate(old)}), \
                    mock.patch.object(spaces.ui.transition, "glide_play") as play:
                self.bar.refresh()
        finally:
            OUT["workspace"]["grid_width"] = 3
        kids = self.children()
        self.assertTrue(kids[3].has_css_class("appearing"))
        self.assertFalse(kids[0].has_css_class("appearing"))
        moved = play.call_args[0][0]
        self.assertEqual([moved[k][0] for k in kids[:3]], [0.0, 10.0, 20.0])         # from the old places
        self.assertNotIn(kids[3], moved)

    def test_a_closed_desktop_shrinks_away_first(self):
        later = []
        with mock.patch.object(spaces.GLib, "timeout_add", side_effect=lambda ms, fn: later.append((ms, fn))):
            self.bar.close_desktop(2)
        self.assertTrue(self.children()[2].has_css_class("vanishing"))
        self.assertNotIn("vswitch/send-view", [m for m, _d in self.bar.ipc.calls])   # not yet
        self.assertEqual(later[0][0], spaces.VANISH_MS)
        self.assertEqual(self.bar._glide, None)
        with mock.patch.object(spaces, "set_count"), mock.patch.object(spaces.GLib, "timeout_add"):
            later[0][1]()
        count, glide = self.bar._glide
        self.assertEqual((count, [glide(i) for i in range(2)]), (2, [0, 1]))
        with mock.patch.object(spaces, "set_count"), mock.patch.object(spaces.GLib, "timeout_add"):
            self.bar._close_desktop(1)
        count, glide = self.bar._glide
        self.assertEqual((count, [glide(i) for i in range(2)]), (2, [0, 2]))         # the ones after: one over

    # -- Vini: hold a window in Mission Control and drag it onto another desktop
    def test_a_window_dropped_on_a_desktop_goes_there(self):
        self.bar.dragging({"state": "start", "view-id": 2})
        self.assertTrue(self.bar.row.has_css_class("dropping"))
        target = self.children()[2].desk_button.get_child()
        with mock.patch.object(self.bar, "pick", return_value=target), \
                mock.patch.object(self.bar, "get_height", return_value=200), \
                mock.patch.object(spaces.GLib, "timeout_add"):
            self.bar.dragging({"state": "drop", "view-id": 2, "x": 900, "y": 60})
        self.assertFalse(self.bar.row.has_css_class("dropping"))
        self.assertIn(("vswitch/send-view", {"view-id": 2, "x": 2, "y": 0}), self.bar.ipc.calls)

    def test_a_window_dropped_on_add_gets_a_new_desktop(self):
        """Vini: drag a window onto + -- a new desktop, and the window goes there."""
        later = []
        with mock.patch.object(self.bar, "pick", return_value=self.bar.add), \
                mock.patch.object(self.bar, "get_height", return_value=200), \
                mock.patch.object(spaces, "set_count") as count, \
                mock.patch.object(spaces.GLib, "timeout_add", side_effect=lambda ms, fn: later.append(fn)):
            self.bar.dragging({"state": "drop", "view-id": 2, "x": 900, "y": 60})
        count.assert_called_once_with(4)                                         # 3 -> 4
        self.assertNotIn("vswitch/send-view", [m for m, _d in self.bar.ipc.calls])  # not before it exists
        OUT["workspace"]["grid_width"] = 4                                      # Wayfire read its config
        self.bar.set_visible(True)
        try:
            later[-1]()
        finally:
            OUT["workspace"]["grid_width"] = 3
            self.bar.set_visible(False)
        self.assertIn(("vswitch/send-view", {"view-id": 2, "x": 3, "y": 0}), self.bar.ipc.calls)

    def test_dropped_elsewhere_or_on_its_own_desktop_stays(self):
        self.bar.ipc.calls.clear()
        own = self.children()[1].desk_button                                         # view 2 is on desktop 1
        with mock.patch.object(self.bar, "get_height", return_value=200):
            for picked, y in ((own, 60), (None, 60), (own, 900)):
                with mock.patch.object(self.bar, "pick", return_value=picked):
                    self.bar.dragging({"state": "drop", "view-id": 2, "x": 900, "y": y})
        self.assertNotIn("vswitch/send-view", [m for m, _d in self.bar.ipc.calls])


class ScaleEventsTest(unittest.TestCase):
    """Sonata's Wayfire tells what is done to a window in Mission Control:
    a right click (the Dock's menu) and a drag (onto a desktop)."""

    def test_patch_and_listeners(self):
        patch = open(os.path.join(ROOT, "wayfire-plugin", "wayfire-scale-sonata-events.patch")).read()
        for part in ("case BTN_RIGHT:", '"sonata-scale-menu#"', '"sonata-scale-drag#", last_selected_view, to_f, "start"',
                     '"drop"', "wf::ipc_rules::send_event_to_subscribes(data, name);"):
            self.assertIn(part, patch)
        mission = open(os.path.join(ROOT, "sonata2", "shell", "mission.py")).read()
        self.assertIn('"sonata-scale-drag#"', mission)
        dock = open(os.path.join(ROOT, "sonata2", "shell", "dock.py")).read()
        self.assertIn('"sonata-scale-menu#"', dock)

    def test_right_click_is_the_docks_menu(self):
        import types
        from sonata2.shell import dock_menu, layer
        tile = mock.Mock(key="org.gnome.Calculator")
        dock = types.SimpleNamespace(tiles={"org.gnome.Calculator": tile})
        with mock.patch.object(dock_menu, "app_sections", return_value=[["items"]]) as sections, \
                mock.patch.object(layer, "menu_at") as at, \
                mock.patch("sonata2.apps.match_app_id", side_effect=lambda a: a):
            dock_menu.scale_menu(dock, {"app-id": "org.gnome.Calculator", "output": "DP-1", "x": 700.0, "y": 400.0})
            dock_menu.scale_menu(dock, {"app-id": "unknown.app", "output": "DP-1", "x": 1, "y": 1})
        sections.assert_called_once_with(dock, "org.gnome.Calculator", tile)
        self.assertEqual(at.call_args[0][1:], ("DP-1", 700.0, 400.0, [["items"]]))


if __name__ == "__main__":
    unittest.main()


class EmptyDesktopTest(unittest.TestCase):
    """Vini: an empty desktop couldn't be left -- Wayfire's scale refused to
    open with no window. Sonata's Wayfire opens it anyway (a patch), and it
    stays open when the last window closes."""

    def test_patch_is_built(self):
        patch = os.path.join(ROOT, "wayfire-plugin", "wayfire-scale-empty-desktop.patch")
        text = open(patch).read()
        self.assertIn("-            output->deactivate_plugin(&grab_interface);\n-            return false;", text)
        for script in ("build-wayfire.sh", "build-wayfire-stack.sh"):
            src = open(os.path.join(ROOT, "tools", script)).read()
            self.assertIn("wayfire-plugin/wayfire-*.patch", src, script)


class DimBackdropTest(unittest.TestCase):
    """Vini: Mission Control only darkens the desktop -- blur is too heavy."""

    def test_no_blur(self):
        src = open(os.path.join(ROOT, "sonata2", "shell", "mission.py")).read()
        self.assertNotIn("Backdrop(", src)
        self.assertNotIn("blur=", src)
        self.assertIn('add_css_class("sonata-mission")', src)
