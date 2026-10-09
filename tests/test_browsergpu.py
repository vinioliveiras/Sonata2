"""WebGPU for browser games (Vini: "Unable to find a GPU"; Chrome's Vulkan
left a transparent window on Wayland): Settings > Displays > Games writes
X11 + Vulkan + WebGPU into the browsers' flag files and takes it out
again, the user's own lines kept; Sonata's web apps stay on Wayland."""
import os
import tempfile
import unittest
from unittest import mock

from sonata2 import browsergpu as G, config


class EditTest(unittest.TestCase):
    def test_on_off_roundtrip(self):
        mine = ["--gtk-version=3"]
        on, app = G.edit(mine, True)
        self.assertEqual(on, mine + [G.MARK, "--enable-features=Vulkan", "--ozone-platform=x11",
                                     "--enable-unsafe-webgpu"])
        self.assertFalse(app)
        self.assertEqual(G.edit(on, True), (on, False))                 # twice: once
        self.assertEqual(G.edit(on, False), (mine, False))

    def test_users_own_features_line(self):
        """Chrome keeps only the last --enable-features: Vulkan joins theirs."""
        mine = ["--enable-features=TouchpadOverscrollHistoryNavigation"]
        on, app = G.edit(mine, True)
        self.assertTrue(app)
        self.assertEqual(on[0], "--enable-features=TouchpadOverscrollHistoryNavigation,Vulkan")
        self.assertEqual(sum(ln.startswith("--enable-features") for ln in on), 1)
        self.assertEqual(G.edit(on, False, app), (mine, False))

    def test_users_vulkan_kept(self):
        mine = ["--enable-features=Vulkan"]
        on, app = G.edit(mine, True)
        self.assertFalse(app)
        self.assertEqual(G.edit(on, False, app)[0], mine)

    def test_no_comment_on_a_flag_line(self):
        """The launchers pass every word of a line to the browser."""
        on, _ = G.edit(["--enable-features=A"], True)
        self.assertFalse(any("#" in ln for ln in on if ln.startswith("--")))


class ApplyTest(unittest.TestCase):
    def test_files_of_installed_browsers(self):
        with tempfile.TemporaryDirectory() as cfg, tempfile.TemporaryDirectory() as home, \
                mock.patch.object(config, "CONFIG_DIR", home), \
                mock.patch("sonata2.titlebars._installed", side_effect=lambda n: n == "chrome-flags.conf"):
            with open(os.path.join(cfg, "brave-flags.conf"), "w") as f:
                f.write("--enable-features=X\n")
            G.apply(True, cfg)
            self.assertIn("--ozone-platform=x11", open(os.path.join(cfg, "chrome-flags.conf")).read())
            self.assertIn("--enable-features=X,Vulkan", open(os.path.join(cfg, "brave-flags.conf")).read())
            self.assertFalse(os.path.exists(os.path.join(cfg, "chromium-flags.conf")))
            G.apply(False, cfg)
            self.assertEqual(open(os.path.join(cfg, "chrome-flags.conf")).read().strip(), "")
            self.assertEqual(open(os.path.join(cfg, "brave-flags.conf")).read(), "--enable-features=X\n")


class WebAppTest(unittest.TestCase):
    def test_web_apps_stay_on_wayland(self):
        from sonata2 import webapps as W
        e = {"url": "https://web.whatsapp.com/"}
        with mock.patch.object(G, "enabled", return_value=True):
            cmd = W.chromium_command("a", e, "/b")
        self.assertIn("--ozone-platform=wayland", cmd)
        self.assertIn("--disable-features=Vulkan", cmd)
        with mock.patch.object(G, "enabled", return_value=False):
            self.assertNotIn("--ozone-platform=wayland", W.chromium_command("a", e, "/b"))


if __name__ == "__main__":
    unittest.main()
