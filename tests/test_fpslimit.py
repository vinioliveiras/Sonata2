"""Vini: limit games' FPS from Control Center (a row of choices) and
Settings, off by default -- through frame-pacer's config file."""
import os
import tempfile
import types
import unittest
from unittest import mock

from sonata2 import config, fpslimit as F


class FpsLimitTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        for p in (mock.patch.dict(os.environ, {"HOME": self.home, "XDG_CONFIG_HOME": os.path.join(self.home, "c")}),
                  mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())):
            p.start()
            self.addCleanup(p.stop)

    def conf(self):
        with open(F.conf_path()) as f:
            return f.read()

    def test_off_by_default(self):
        self.assertEqual(F.get(), "off")
        from sonata2.shell import controlcenter as CC
        self.assertIn("fpslimit", CC.CATALOG)
        self.assertNotIn("fpslimit", CC.DEFAULT_ORDER)            # Add Controls brings it

    def test_set_writes_frame_pacer_conf_keeping_game_sections(self):
        os.makedirs(os.path.dirname(F.conf_path()))
        with open(F.conf_path(), "w") as f:
            f.write("global_fps_limit = 45\nhud = on\n\n[My game]\nexecutable = \"Game.exe\"\nfps_limit = 30\n")
        F.set("60")
        text = self.conf()
        self.assertIn("global_fps_limit = 60\nhud = off\n", text)
        self.assertNotIn("= 45", text)
        self.assertIn('[My game]\nexecutable = "Game.exe"\nfps_limit = 30', text)
        F.set("max", refresh_hz=143.9)
        self.assertIn("global_fps_limit = 144", self.conf())
        F.set("off", hud=True)
        self.assertIn("global_fps_limit = off\nhud = on", self.conf())
        self.assertEqual(self.conf().count("global_fps_limit"), 1)
        self.assertEqual(F.get(), "off")

    def test_only_game_launchers_get_the_layer(self):
        info = lambda did: types.SimpleNamespace(get_id=lambda: did)   # noqa: E731
        with mock.patch.object(F, "installed", lambda: True):
            self.assertEqual(F.env_for(info("steam.desktop")), {"ENABLE_FRAME_PACER": "1"})
            self.assertEqual(F.env_for(info("google-chrome.desktop")), {})
        with mock.patch.object(F, "installed", lambda: False):
            self.assertEqual(F.env_for(info("steam.desktop")), {})

    def test_release_asset(self):
        rels = [{"assets": [{"name": "src.tar.gz", "browser_download_url": "x"}]},
                {"assets": [{"name": "frame-pacer-0.1.0-linux-x86_64-multilib.tar.xz", "browser_download_url": "T"},
                            {"name": "frame-pacer-0.1.0-linux-x86_64-multilib.tar.xz.sha256",
                             "browser_download_url": "S"}]}]
        self.assertEqual(F.latest_asset(rels), ("T", "S"))
        self.assertIsNone(F.latest_asset([]))


class FpsModuleTest(unittest.TestCase):
    def test_module(self):
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk
        from sonata2 import ui
        ui.setup()
        from sonata2.shell import topbar  # noqa: F401  (Control Center's styles)
        from sonata2.shell import fpsmodule
        sets = []
        with mock.patch.object(F, "installed", lambda: False), mock.patch.object(F, "get", lambda: "off"):
            m = fpsmodule.module()
            m.update()
            self.assertFalse(m.seg.get_sensitive())               # not installed: greyed, offers to
            self.assertTrue(m.install_btn.get_visible())
        with mock.patch.object(F, "installed", lambda: True), mock.patch.object(F, "get", lambda: "60"), \
                mock.patch.object(F, "set", lambda c, hz=None: sets.append(c)):
            m.update()
            self.assertTrue(m.seg.get_sensitive())
            self.assertTrue(m.buttons["60"].has_css_class("on"))
            m.buttons["30"].emit("clicked")
        self.assertEqual(sets, ["30"])
        # two Control Center rows, with the graph (Vini: as tall as the others): nothing cut
        from sonata2.shell import controlcenter as CC
        self.assertEqual(CC.CATALOG["fpslimit"][1], (4, 2))
        self.assertLessEqual(m.measure(Gtk.Orientation.VERTICAL, 344)[1], CC.span_height(2))

    def test_frame_time_graph(self):
        """Vini: a frame-time graph under the choices -- each frame's time, the
        average and the 1 % low; a note when there's nothing to show."""
        import gi
        gi.require_version("Gtk", "4.0")
        from sonata2 import ui
        ui.setup()
        from sonata2.shell import fpsmodule
        avg, low = fpsmodule.summary([16.7] * 99 + [50.0])
        self.assertAlmostEqual(avg, 16.7 * 0.99 + 0.5, places=2)
        self.assertEqual(low, 20)                                   # the slowest 1 %: 50 ms
        with mock.patch.object(F, "installed", lambda: True), mock.patch.object(F, "get", lambda: "60"):
            m = fpsmodule.module()
            m.update()
        self.assertAlmostEqual(m.graph.target, 1000 / 60, places=3)
        with mock.patch.object(fpsmodule, "app_name", lambda a: "Hades"):
            m.show_frames({"fps": 58, "app-id": "steam_app_1", "frametimes": [16.6, 16.8, 33.0, 16.7]})
        self.assertEqual(m.live.get_label(), "58 fps")
        self.assertEqual(len(m.graph.times), 1)                     # one point per read (slower: Vini)
        self.assertEqual(m.app.get_label(), "Hades")                # the app measured (Vini)
        m.show_frames({"fps": 0, "app-id": "", "frametimes": []})
        self.assertEqual(m.graph.note, "No game or full-screen app")
        self.assertEqual(m.app.get_label(), "")
        m.show_frames({"fps": 60, "app-id": "x"})                  # an older plugin: no frame times
        self.assertIn("install.sh", m.graph.note)


    def test_graph_is_slower(self):
        """Vini: frame by frame it ran by too fast -- one point per read, the
        average of the frames since the last; half a minute shown; a new app
        in front starts its own graph."""
        import gi
        gi.require_version("Gtk", "4.0")
        from sonata2 import ui
        ui.setup()
        from sonata2.shell import fpsmodule as M
        self.assertEqual(M.SHOWN * M.POLL_MS, 30000)
        self.assertAlmostEqual(M.recent_average([100.0] * 10 + [10.0] * 25), 10.0)   # only the newest 250 ms
        self.assertAlmostEqual(M.recent_average([16.0, 18.0]), 17.0)
        with mock.patch.object(F, "installed", lambda: True), mock.patch.object(F, "get", lambda: "off"):
            m = M.module()
        with mock.patch.object(M, "app_name", lambda a: a.upper()):
            for _ in range(M.SHOWN + 5):
                m.show_frames({"fps": 60, "app-id": "game", "frametimes": [16.7] * 60})
            self.assertEqual(len(m.graph.times), M.SHOWN)
            m.show_frames({"fps": 60, "app-id": "other", "frametimes": [16.7] * 60})
        self.assertEqual(len(m.graph.times), 1)
        self.assertEqual(m.app.get_label(), "OTHER")

    def test_app_name(self):
        from sonata2.shell import fpsmodule as M
        info = mock.Mock()
        info.get_display_name.return_value = "Firefox"
        with mock.patch("sonata2.apps.match_app_id", return_value="firefox"), \
                mock.patch("sonata2.apps.lookup", return_value=info):
            self.assertEqual(M.app_name("firefox"), "Firefox")
        with mock.patch("sonata2.apps.match_app_id", return_value=None), \
                mock.patch("sonata2.windowapps.describe", return_value=("Hades II", None, False)):
            self.assertEqual(M.app_name("steam_app_1145350"), "Hades II")


if __name__ == "__main__":
    unittest.main()
