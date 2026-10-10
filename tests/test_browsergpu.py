"""Vulkan / WebGPU per app (Vini: a toggle for each app instead of one
switch for every browser; before: "Unable to find a GPU" in a WebGPU game,
Chrome's Vulkan left a transparent window on Wayland, WhatsApp showed
"unsupported command-line flag")."""
import os
import tempfile
import unittest
from unittest import mock

from sonata2 import browsergpu as G, config


class EditTest(unittest.TestCase):
    def test_on_off_roundtrip(self):
        mine = ["--gtk-version=3"]
        on, app = G.edit(mine, list(G.FLAGS))
        self.assertEqual(on, mine + [G.MARK, "--enable-features=Vulkan", "--ozone-platform=x11",
                                     "--enable-unsafe-webgpu"])
        self.assertFalse(app)
        self.assertEqual(G.edit(on, list(G.FLAGS)), (on, False))                 # twice: once
        self.assertEqual(G.edit(on, []), (mine, False))

    def test_users_own_features_line(self):
        """Chrome keeps only the last --enable-features: Vulkan joins theirs."""
        mine = ["--enable-features=TouchpadOverscrollHistoryNavigation"]
        on, app = G.edit(mine, list(G.FLAGS))
        self.assertTrue(app)
        self.assertEqual(on[0], "--enable-features=TouchpadOverscrollHistoryNavigation,Vulkan")
        self.assertEqual(sum(ln.startswith("--enable-features") for ln in on), 1)
        self.assertEqual(G.edit(on, [], app), (mine, False))

    def test_users_vulkan_kept(self):
        mine = ["--enable-features=Vulkan"]
        on, app = G.edit(mine, list(G.FLAGS))
        self.assertFalse(app)
        self.assertEqual(G.edit(on, [], app)[0], mine)

    def test_no_comment_on_a_flag_line(self):
        """The launchers pass every word of a line to the browser."""
        on, _ = G.edit(["--enable-features=A"], list(G.FLAGS))
        self.assertFalse(any("#" in ln for ln in on if ln.startswith("--")))


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



def info(did, line):
    return type("I", (), {"get_id": lambda s: did + ".desktop", "get_commandline": lambda s: line})()


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.cfg = tempfile.TemporaryDirectory()
        p = mock.patch.object(config, "CONFIG_DIR", self.home.name)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.home.cleanup)
        self.addCleanup(self.cfg.cleanup)
        G._kinds.clear()
        from sonata2 import gpu
        self.addCleanup(setattr, gpu, "_dual", gpu._dual)       # cached for the real machine otherwise

    def flag_file(self, name="chrome-flags.conf"):
        try:
            return open(os.path.join(self.cfg.name, name)).read().splitlines()
        except FileNotFoundError:
            return []


class OldBlockTest(Base):
    def test_old_global_block_read_and_replaced(self):
        """Files written by the old global switch keep working."""
        old = ["--gtk-version=3", G.MARKS[1], G.OWN, G.X11, G.WEBGPU]
        self.assertEqual(G.block(old), [G.X11, G.WEBGPU])
        self.assertEqual(G.edit(old, [])[0], ["--gtk-version=3"])


class KindTest(Base):
    def test_kinds(self):
        with mock.patch.object(G, "electron", side_effect=lambda n, f: n in ("discord", "com.discordapp.Discord")):
            for did, line, want in (
                    ("google-chrome", "/usr/bin/google-chrome-stable %U", "chromium"),
                    ("com.google.Chrome", "/usr/bin/flatpak run --branch=stable com.google.Chrome @@u %U @@",
                     "chromium"),
                    ("sonata2-webapp-wa", "sonata2 webapp wa", "webapp"),
                    ("firefox", "/usr/lib/firefox/firefox %u", "mozilla"),
                    ("steam", "/usr/bin/steam %U", "wine"),
                    ("discord", "env FOO=1 discord", "electron"),
                    ("com.discordapp.Discord", "flatpak run com.discordapp.Discord", "electron"),
                    ("org.gnome.Nautilus", "nautilus --new-window", "other")):
                self.assertEqual(G.kind(info(did, line)), want, did)
        self.assertEqual(G.supports(info("firefox", "firefox")), (False, True))
        self.assertEqual(G.supports(info("gedit", "gedit")), (True, False))
        self.assertEqual(G.supports(info("google-chrome", "google-chrome-stable")), (True, True))

    def test_electron_by_asar_or_script(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "app", "resources"))
            open(os.path.join(d, "app", "resources", "app.asar"), "w").close()
            exe = os.path.join(d, "app", "code")
            open(exe, "w").close()
            self.assertTrue(G.electron(exe, False))
            script = os.path.join(d, "obsidian")
            open(script, "w").write("#!/bin/sh\nexec electron37 /usr/lib/obsidian/app.asar \"$@\"\n")
            self.assertTrue(G.electron(script, False))
            plain = os.path.join(d, "plain")
            open(plain, "w").write("#!/bin/sh\nexec true\n")
            self.assertFalse(G.electron(plain, False))


class ChromeFileTest(Base):
    """A Chromium browser's choices live in its flag file (any way it opens)."""
    def test_toggles_write_the_file(self):
        chrome = info("google-chrome", "/usr/bin/google-chrome-stable %U")
        with open(os.path.join(self.cfg.name, "chrome-flags.conf"), "w") as f:
            f.write("--enable-features=X\n")
        self.assertEqual((G.vulkan(chrome, self.cfg.name), G.webgpu(chrome, self.cfg.name)), (False, False))
        G.set_vulkan(chrome, True, self.cfg.name)
        self.assertEqual(self.flag_file(), ["--enable-features=X,Vulkan", G.MARK, G.X11])
        self.assertTrue(G.vulkan(chrome, self.cfg.name))
        G.set_webgpu(chrome, True, self.cfg.name)
        self.assertEqual(self.flag_file()[1:], [G.MARK, G.X11, G.WEBGPU])
        self.assertTrue(G.vulkan_forced(chrome, self.cfg.name))
        G.set_webgpu(chrome, False, self.cfg.name)
        G.set_vulkan(chrome, False, self.cfg.name)
        self.assertEqual(self.flag_file(), ["--enable-features=X"])

    def test_no_file_made_for_off(self):
        G.set_vulkan(info("google-chrome", "google-chrome-stable"), False, self.cfg.name)
        self.assertFalse(os.path.exists(os.path.join(self.cfg.name, "chrome-flags.conf")))

    def test_firefox_webgpu_its_own_profiles(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "p"))
            open(os.path.join(base, "p", "prefs.js"), "w").close()
            ff = info("firefox", "firefox %u")
            with mock.patch.dict(G.MOZILLA, {"firefox": (base,)}):
                self.assertFalse(G.webgpu(ff))
                G.set_webgpu(ff, True)
                self.assertTrue(G.webgpu(ff))
                self.assertIn(G.MOZ_LINE, open(os.path.join(base, "p", "user.js")).read())
                G.set_webgpu(ff, False)
                self.assertFalse(G.webgpu(ff))


class LaunchTest(Base):
    def test_other_apps_vulkan_env(self):
        app = info("org.gnome.Nautilus", "nautilus")
        self.assertEqual(G.launch_env(app), {})
        G.set_vulkan(app, True)
        self.assertEqual(G.launch_env(app), G.VULKAN_ENV)
        self.assertEqual(G.launch_args(app), [])

    def test_wine_vulkan_default_on_off_is_wined3d(self):
        steam = info("steam", "/usr/bin/steam %U")
        self.assertTrue(G.vulkan(steam))
        self.assertEqual(G.launch_env(steam), {})
        G.set_vulkan(steam, False)
        self.assertEqual(G.launch_env(steam)["PROTON_USE_WINED3D"], "1")

    def test_electron_flags(self):
        with mock.patch.object(G, "electron", return_value=True):
            app = info("discord", "discord")
            self.assertEqual(G.launch_args(app), [])
            G.set_webgpu(app, True)
            self.assertEqual(G.launch_args(app), [G.X11, G.WEBGPU, G.OWN])
            self.assertTrue(G.vulkan_forced(app))

    def test_gpu_hooks_pass_them_on(self):
        from sonata2 import gpu
        app = info("org.gnome.Nautilus", "nautilus")
        G.set_vulkan(app, True)
        self.assertEqual(gpu.extra_env(app)["GSK_RENDERER"], "vulkan")
        with mock.patch.object(G, "launch_args", return_value=["--x"]):
            self.assertEqual(gpu.extra_args(info("discord", "discord")), ["--x"])


class WebAppTest(Base):
    def launcher(self, d):
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
        return real, launcher

    def test_past_the_launcher_when_chrome_has_our_block(self):
        """Vini: WhatsApp showed "unsupported command-line flag"."""
        with tempfile.TemporaryDirectory() as d:
            real, launcher = self.launcher(d)
            self.assertEqual(G.web_app_command("wa", launcher, self.cfg.name), [launcher])   # nothing to skip
            chrome = info("google-chrome", launcher)
            with open(os.path.join(self.cfg.name, "chrome-flags.conf"), "w") as f:
                f.write("--gtk-version=3\n--enable-features=Foo\n")
            G.set_webgpu(chrome, True, self.cfg.name)
            self.assertEqual(G.web_app_command("wa", launcher, self.cfg.name),
                             [real, "--gtk-version=3", "--enable-features=Foo"])

    def test_web_apps_own_toggle(self):
        with tempfile.TemporaryDirectory() as d:
            real, launcher = self.launcher(d)
            from sonata2 import webapps
            G.set_webgpu(info(webapps.desktop_id("game"), ""), True)
            self.assertEqual(G.web_app_command("game", launcher, self.cfg.name),
                             [launcher, G.X11, G.WEBGPU, "--enable-features=Vulkan"])
            self.assertEqual(G.web_app_command("wa", launcher, self.cfg.name), [launcher])

    def test_unknown_launcher_with_block_stays_on_wayland(self):
        G.write_flag_file("chrome-flags.conf", [G.X11], self.cfg.name)
        self.assertEqual(G.web_app_command("wa", "/x/google-chrome-stable", self.cfg.name),
                         ["/x/google-chrome-stable", "--ozone-platform=wayland", "--disable-features=Vulkan"])

    def test_web_app_command_used(self):
        from sonata2 import webapps as W
        with mock.patch.object(G, "web_app_command", return_value=["/opt/c", "--x"]):
            cmd = W.chromium_command("a", {"url": "https://web.whatsapp.com/"}, "/usr/bin/google-chrome-stable")
        self.assertEqual(cmd[0], "/opt/c")
        self.assertIn("--x", cmd)
        self.assertEqual(cmd[-1], "--app=https://web.whatsapp.com/")

    def test_no_unsupported_flag_bar(self):
        """Vini (twice): the bar still showed in WhatsApp -- --test-type hides it, whatever flags come."""
        from sonata2 import webapps as W
        cmd = W.chromium_command("a", {"url": "https://web.whatsapp.com/"}, "/usr/bin/google-chrome-stable")
        self.assertIn("--test-type", cmd)


class MenuTest(Base):
    def test_menu_items(self):
        Item = lambda label, cb, checked=None, enabled=True: (label, checked, enabled)  # noqa: E731
        self.assertEqual(G.menu_items(info("org.gnome.Nautilus", "nautilus"), Item), [("Use Vulkan", False, True)])
        with mock.patch.dict(G.MOZILLA, {"firefox": ()}):
            self.assertEqual(G.menu_items(info("firefox", "firefox"), Item), [("Allow WebGPU", False, True)])
        with mock.patch.object(G, "electron", return_value=True):
            app = info("discord", "discord")
            G.set_webgpu(app, True)
            self.assertEqual(G.menu_items(app, Item), [("Use Vulkan", True, False), ("Allow WebGPU", True, True)])
        self.assertEqual(G.menu_items(None, Item), [])

    def test_dock_and_launchpad_show_them(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "sonata2" / "shell"
        for f in ("dock_menu.py", "launchpad.py"):
            self.assertIn("browsergpu.menu_items(info, Item)", (root / f).read_text(), f)

    def test_global_switch_gone(self):
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent / "sonata2" / "settings" / "app.py").read_text()
        self.assertNotIn("WebGPU in Browsers", src)


class SettingsPageTest(Base):
    def test_graphics_group(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from sonata2.settings.apps_page import AppsPage
        with mock.patch.object(G, "electron", return_value=True):
            app = info("discord", "discord")
            g = AppsPage.graphics(app)
            vk, wg = g.rows["vulkan"], g.rows["webgpu"]
            self.assertEqual((vk.get_active(), wg.get_active(), vk.get_sensitive()), (False, False, True))
            wg.set_active(True)
            self.assertTrue(G.webgpu(app))
            self.assertEqual((vk.get_active(), vk.get_sensitive()), (True, False))
            wg.set_active(False)
            self.assertEqual((vk.get_active(), vk.get_sensitive()), (False, True))
            vk.set_active(True)
            self.assertTrue(G.vulkan(app))
        g = AppsPage.graphics(info("firefox", "firefox"))
        self.assertEqual(list(g.rows), ["webgpu"])


if __name__ == "__main__":
    unittest.main()
