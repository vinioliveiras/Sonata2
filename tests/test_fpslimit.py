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
        from sonata2 import ui
        ui.setup()
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


if __name__ == "__main__":
    unittest.main()
