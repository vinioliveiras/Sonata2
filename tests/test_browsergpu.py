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


class MozillaTest(unittest.TestCase):
    def test_firefox_user_js(self):
        """Vini: the switch for Firefox too (dom.webgpu.enabled)."""
        with tempfile.TemporaryDirectory() as prof:
            open(os.path.join(prof, "prefs.js"), "w").write('user_pref("a", 1);\n')
            open(os.path.join(prof, "user.js"), "w").write('user_pref("mine", 2);\n')
            G.apply_mozilla(True, [prof])
            self.assertEqual(open(os.path.join(prof, "user.js")).read(),
                             'user_pref("mine", 2);\n' + G.MOZ_LINE + "\n")
            G.apply_mozilla(True, [prof])                                   # once
            self.assertEqual(open(os.path.join(prof, "user.js")).read().count("Sonata WebGPU"), 1)
            with open(os.path.join(prof, "prefs.js"), "a") as f:            # Firefox copied it
                f.write('user_pref("dom.webgpu.enabled", true);\n')
            G.apply_mozilla(False, [prof])
            self.assertEqual(open(os.path.join(prof, "user.js")).read(), 'user_pref("mine", 2);\n')
            self.assertEqual(open(os.path.join(prof, "prefs.js")).read(), 'user_pref("a", 1);\n')

    def test_profiles_found_by_prefs_js(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "abc.default"))
            open(os.path.join(base, "abc.default", "prefs.js"), "w").close()
            os.makedirs(os.path.join(base, "Crash Reports"))
            self.assertEqual(G.mozilla_profiles((base,)), [os.path.join(base, "abc.default")])


class WebAppBypassTest(unittest.TestCase):
    """Vini: WhatsApp showed "unsupported command-line flag:
    --enable-unsafe-webgpu" -- the launcher added the flag file's flags."""
    def setup(self, d):
        real = os.path.join(d, "opt-chrome")
        with open(real, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(real, 0o755)
        launcher = os.path.join(d, "google-chrome-stable")
        with open(launcher, "w") as f:
            f.write("#!/bin/bash\nXDG_CONFIG_HOME=${XDG_CONFIG_HOME:-~/.config}\n"
                    "if [[ -f $XDG_CONFIG_HOME/chrome-flags.conf ]]; then\n"
                    "  CHROME_USER_FLAGS=\"$(grep -v '^#' $XDG_CONFIG_HOME/chrome-flags.conf)\"\nfi\n"
                    f"exec {real} $CHROME_USER_FLAGS \"$@\"\n")
        cfg = os.path.join(d, "cfg")
        os.makedirs(cfg)
        lines, _ = G.edit(["--gtk-version=3", "--enable-features=Foo"], True)
        with open(os.path.join(cfg, "chrome-flags.conf"), "w") as f:
            f.write("\n".join(lines) + "\n")
        return real, launcher, cfg

    def test_past_the_launcher_without_webgpu(self):
        with tempfile.TemporaryDirectory() as d:
            real, launcher, cfg = self.setup(d)
            with mock.patch.object(G, "enabled", return_value=True), \
                    mock.patch.object(G.config, "load", return_value={"appended": {"chrome-flags.conf": True}}):
                argv = G.web_app_browser(launcher, cfg)
            self.assertEqual(argv, [real, "--gtk-version=3", "--enable-features=Foo"])

    def test_off_or_unknown_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            real, launcher, cfg = self.setup(d)
            with mock.patch.object(G, "enabled", return_value=False):
                self.assertEqual(G.web_app_browser(launcher, cfg), [launcher])
            with mock.patch.object(G, "enabled", return_value=True):
                self.assertEqual(G.web_app_browser(real, cfg), [real])          # not a launcher script

    def test_web_app_command_uses_it(self):
        from sonata2 import webapps as W
        with mock.patch.object(G, "web_app_browser", return_value=["/opt/c", "--x"]):
            cmd = W.chromium_command("a", {"url": "https://web.whatsapp.com/"}, "/usr/bin/google-chrome-stable")
        self.assertEqual(cmd[:2], ["/opt/c", "--x"])


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
