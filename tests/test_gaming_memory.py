"""Gaming with a full graphics card (Vini: browsers crawled while a game held
6.5 of 8 GB). Lighter effects while a full-screen game fills it
(gamemode.LightEffects) and, optionally, everyday apps on the integrated
GPU (gpu.launch_env).
Run: python3 -m unittest tests.test_gaming_memory"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_RUNTIME_DIR"] = tempfile.mkdtemp()

from sonata2 import gamemode, gpu  # noqa: E402


class LightEffectsTest(unittest.TestCase):
    def setUp(self):
        self.cfg = {("blur", "blur_by_default"): 'app_id contains "sonata2"', ("animate", "open_animation"): "zoom",
                    ("animate", "close_animation"): "zoom", ("animate", "minimize_animation"): "squeezimize"}
        self.orig = dict(self.cfg)
        self.light = gamemode.LightEffects(get=lambda s, k, d="": self.cfg.get((s, k), d),
                                           set_=lambda s, k, v: self.cfg.__setitem__((s, k), v),
                                           wanted=lambda: True)
        if os.path.exists(gamemode.LIGHT):
            os.remove(gamemode.LIGHT)

    def test_only_when_a_game_fills_the_card(self):
        self.light.update(full=False, used=7800, total=8188)
        self.assertEqual(self.cfg, self.orig)                        # not playing
        self.light.update(full=True, used=6000, total=8188)
        self.assertEqual(self.cfg, self.orig)                        # playing, room left
        self.light.update(used=7800, total=8188)
        self.assertEqual(self.cfg[("animate", "open_animation")], "none")
        self.assertEqual(self.cfg[("blur", "blur_by_default")], 'app_id is "sonata2-no-blur"')

    def test_back_when_the_game_leaves(self):
        self.light.update(full=True, used=7900, total=8188)
        self.light.update(used=5000, total=8188)                     # less full: still playing, stays light
        self.assertEqual(self.cfg[("animate", "close_animation")], "none")
        self.light.update(full=False)
        self.assertEqual(self.cfg, self.orig)
        self.assertFalse(self.light.on())

    def test_a_restart_brings_them_back(self):
        self.light.update(full=True, used=7900, total=8188)
        again = gamemode.LightEffects(get=self.light.get, set_=self.light.set, wanted=lambda: True)
        again.update(full=False)                                     # the menu bar restarted
        self.assertEqual(self.cfg, self.orig)

    def test_session_copy_only(self):
        src = open(gamemode.__file__).read()
        self.assertIn("wfconfig.runtime_set", src)


class EverydayIntegratedTest(unittest.TestCase):
    def info(self, did, cats=""):
        i = mock.Mock()
        i.get_id.return_value = did
        i.get_categories.return_value = cats
        i.has_key.return_value = False
        return i

    def test_smart_switching_on_by_default(self):
        """Vini: Smart Graphics Switching comes on."""
        self.assertTrue(gpu.smart())

    def test_everyday_apps_move_games_stay(self):
        with mock.patch.object(gpu, "smart", return_value=True), \
                mock.patch.object(gpu, "has_dual_gpu", return_value=True), \
                mock.patch.object(gpu, "_cards", return_value=["amdgpu", "nvidia"]):
            with mock.patch.object(gpu, "render_gpu", return_value="amdgpu"):
                chrome = gpu.launch_env(self.info("google-chrome.desktop", "Network;WebBrowser;"))
            self.assertEqual(chrome.get("__GLX_VENDOR_LIBRARY_NAME"), "mesa")
            with mock.patch.object(gpu, "discrete_env", return_value={"NV": "1"}):   # games: the strong card
                self.assertEqual(gpu.launch_env(self.info("steam.desktop", "Network;FileTransfer;Game;")), {"NV": "1"})
                self.assertEqual(gpu.launch_env(self.info("org.gimp.GIMP.desktop",
                                                          "Graphics;2DGraphics;RasterGraphics;")), {"NV": "1"})
                self.assertEqual(gpu.launch_env(self.info("heroic.desktop", "")), {"NV": "1"})

    def test_apps_follow_the_gpu_that_draws_the_screens(self):
        """Vini: with the screens on the NVIDIA card, Spotify sent to the
        integrated GPU opened empty -- everyday apps stay with the screens' GPU."""
        chrome = self.info("google-chrome.desktop", "WebBrowser;")
        spotify = self.info("spotify-launcher.desktop", "Audio;Music;")
        with mock.patch.object(gpu, "smart", return_value=True), \
                mock.patch.object(gpu, "_cards", return_value=["amdgpu", "nvidia"]):
            with mock.patch.object(gpu, "render_gpu", return_value="nvidia"):
                self.assertEqual(gpu.launch_env(spotify), {})
                self.assertEqual(gpu.launch_env(chrome), {})
            with mock.patch.object(gpu, "render_gpu", return_value="amdgpu"):
                self.assertEqual(gpu.launch_env(spotify).get("__GLX_VENDOR_LIBRARY_NAME"), "mesa")

    def test_render_gpu_from_the_session(self):
        root = tempfile.mkdtemp()
        for n, drv in (("card0", "amdgpu"), ("card1", "nvidia")):
            os.makedirs(os.path.join(root, "drivers", drv))
            os.makedirs(os.path.join(root, n, "device"))
            os.symlink(os.path.join(root, "drivers", drv), os.path.join(root, n, "device", "driver"))
        real_join = os.path.join
        with mock.patch.object(gpu.os.path, "join",
                               lambda a, *b: real_join(root if a == "/sys/class/drm" else a, *b)):
            with mock.patch.dict(os.environ, {"WLR_DRM_DEVICES": "/dev/dri/card1:/dev/dri/card0"}):
                self.assertEqual(gpu.render_gpu(), "nvidia")
            with mock.patch.dict(os.environ, {"WLR_DRM_DEVICES": "/dev/dri/card0"}):
                self.assertEqual(gpu.render_gpu(), "amdgpu")

    def test_nothing_without_an_integrated_gpu(self):
        with mock.patch.object(gpu, "smart", return_value=True), \
                mock.patch.object(gpu, "_cards", return_value=["nvidia"]):
            self.assertEqual(gpu.launch_env(self.info("google-chrome.desktop", "WebBrowser;")), {})

    def test_an_apps_own_choice_wins_and_can_be_given_back(self):
        """Right-click: unchecking Smart Graphics Switching keeps what the app
        gets now as its own choice; checking it again lets Sonata pick."""
        from sonata2 import config
        game = self.info("heroic.desktop", "Game;")
        with mock.patch.object(gpu, "has_dual_gpu", return_value=True):
            self.assertTrue(gpu.wants_discrete(game))
            self.assertFalse(gpu.chosen(game))
            gpu.set_smart_for(game, False)
            self.assertTrue(gpu.chosen(game))
            self.assertTrue(gpu.wants_discrete(game))                 # nothing changed
            gpu.set_discrete(game, False)
            self.assertFalse(gpu.wants_discrete(game))                # its own choice wins
            gpu.set_smart_for(game, True)
            self.assertFalse(gpu.chosen(game))
            self.assertTrue(gpu.wants_discrete(game))
            gpu.set_smart(False)
            self.assertFalse(gpu.wants_discrete(game))                # off: the default GPU
            gpu.set_smart(True)
        self.assertTrue(config.load(gpu.NAME, gpu.DEFAULTS)["smart"])

    def test_menu_section(self):
        Item = lambda label, cb, checked=None, enabled=True: (label, checked, enabled)  # noqa: E731
        app = self.info("google-chrome.desktop", "WebBrowser;")
        with mock.patch.object(gpu, "has_dual_gpu", return_value=True):
            # Vini (Spotify): Smart came out unchecked "by itself" -- unchecking High-Performance
            # made the app's own choice. While Smart picks, High-Performance only shows its choice.
            self.assertEqual(gpu.menu_items(app, Item), [("Smart Graphics Switching", True, True),
                                                         ("Use High-Performance Graphics", False, False)])
            gpu.set_smart_for(app, False)                   # the user unchecks Smart: now it's theirs
            self.assertEqual(gpu.menu_items(app, Item), [("Smart Graphics Switching", False, True),
                                                         ("Use High-Performance Graphics", False, True)])
            gpu.set_smart_for(app, True)
            gpu.set_smart(False)
            self.assertEqual([i[0] for i in gpu.menu_items(app, Item)], ["Use High-Performance Graphics"])
            gpu.set_smart(True)
        with mock.patch.object(gpu, "has_dual_gpu", return_value=False):
            self.assertEqual(gpu.menu_items(app, Item), [])


if __name__ == "__main__":
    unittest.main()
