"""Full screen (sonata2/fullscreen.py): Ctrl+Super+F remembered per app, and
Windows games (Wine / Proton) opening in full screen by themselves.
Bug: Skate 3 from Heroic opened as a window with a title bar.
Run: python3 -m unittest tests.test_fullscreen"""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from gi.repository import GLib  # noqa: E402

from sonata2 import fullscreen as F  # noqa: E402


def settle(ms=100):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def view(vid=1, app="skate3recomp.exe", w=1920, h=1080, **kw):
    v = {"id": vid, "app-id": app, "title": "Skate 3", "pid": -1, "role": "toplevel", "type": "toplevel",
         "parent": -1, "geometry": {"x": 0, "y": 0, "width": w, "height": h}, "output-id": 1,
         "fullscreen": False, "activated": True, "layer": "workspace"}
    v.update(kw)
    return v


class FakeIPC:
    available = True

    def __init__(self, views):
        self.views, self.sent, self.cb = views, [], None

    def watch(self, _events, cb):
        self.cb = cb
        return True

    def call(self, method, data=None):
        if method == "window-rules/list-views":
            return self.views
        if method == "window-rules/output-info":
            return {"geometry": {"x": 0, "y": 0, "width": 1920, "height": 1080}}
        if method == "wm-actions/set-fullscreen":
            self.sent.append((data["view_id"], data["state"]))
            for v in self.views:
                if v["id"] == data["view_id"]:
                    v["fullscreen"] = data["state"]
        return {"result": "ok"}


class FullscreenTest(unittest.TestCase):
    def setUp(self):
        F.config.save("fullscreen", {"apps": {}})
        self._recheck, F.RECHECK_MS = F.RECHECK_MS, (10, 30)

    def tearDown(self):
        F.RECHECK_MS = self._recheck

    def rules(self, views):
        ipc = FakeIPC(views)
        return F.Rules(ipc), ipc

    def test_wine_detection(self):
        self.assertTrue(F.is_wine(view(app="Skate3Recomp.exe")))
        self.assertTrue(F.is_wine(view(app="steam_app_1234")))
        self.assertFalse(F.is_wine(view(app="org.gnome.Nautilus")))

    def test_wine_game_asking_for_the_screen_goes_full_screen(self):
        v = view(w=1920, h=1000)                      # squeezed under the menu bar
        r, ipc = self.rules([v])
        ipc.cb({"event": "view-mapped", "view": v})
        settle(80)
        self.assertEqual(ipc.sent, [(1, True)])

    def test_fixed_size_game_window(self):
        v = view(w=1280, h=720, **{"min-size": {"width": 1280, "height": 720},
                                     "max-size": {"width": 1280, "height": 720}})
        self.assertTrue(F.looks_like_game(v, (1920, 1080)))

    def test_launchers_installers_and_small_windows_stay_windows(self):
        self.assertFalse(F.looks_like_game(view(app="setup.exe"), (1920, 1080)))
        self.assertFalse(F.looks_like_game(view(app="epicgameslauncher.exe"), (1920, 1080)))
        self.assertFalse(F.looks_like_game(view(w=800, h=600), (1920, 1080)))
        self.assertFalse(F.looks_like_game(view(app="org.gnome.Nautilus"), (1920, 1080)))   # native: asks itself
        self.assertFalse(F.looks_like_game(view(parent=4), (1920, 1080)))                  # a dialog

    def test_shortcut_toggles_and_is_remembered(self):
        v = view(app="org.gnome.TextEditor", w=900, h=700)
        r, ipc = self.rules([v])
        r.toggle()
        self.assertEqual(ipc.sent, [(1, True)])
        self.assertTrue(r.remembered("org.gnome.TextEditor"))
        ipc2 = FakeIPC([view(vid=7, app="org.gnome.TextEditor", w=900, h=700)])
        r2 = F.Rules(ipc2)                            # next session / next launch
        ipc2.cb({"event": "view-mapped", "view": ipc2.views[0]})
        self.assertEqual(ipc2.sent, [(7, True)])

    def test_taken_out_of_full_screen_stays_windowed(self):
        v = view()
        r, ipc = self.rules([v])
        v["fullscreen"] = True
        r.toggle()                                    # the user leaves full screen
        self.assertFalse(r.remembered("skate3recomp.exe"))
        ipc.sent.clear()
        v2 = view(vid=2)
        ipc.views = [v2]
        ipc.cb({"event": "view-mapped", "view": v2})
        settle(80)
        self.assertEqual(ipc.sent, [])                # no automatic full screen any more


if __name__ == "__main__":
    unittest.main()
