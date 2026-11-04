"""Vini: WhatsApp's videos never worked in WebKit (decoding, playback): each
web app picks its engine in its form -- Chromium (a browser of that family as
an app window, its own profile and app id) or WebKit."""
import unittest
from unittest import mock

from sonata2 import webapps as W


class EngineTest(unittest.TestCase):
    def test_browser_found_in_order(self):
        with mock.patch.object(W.shutil, "which", side_effect=lambda c: "/usr/bin/chromium" if c == "chromium" else None):
            self.assertEqual(W.chromium_browser(), ("/usr/bin/chromium", "Chromium"))
            self.assertEqual(W.default_engine(), "chromium")
        with mock.patch.object(W.shutil, "which", return_value=None):
            self.assertIsNone(W.chromium_browser())
            self.assertEqual(W.default_engine(), "webkit")

    def test_engine_of_an_app(self):
        with mock.patch.object(W, "chromium_browser", return_value=("/b", "B")):
            self.assertEqual(W.engine({"engine": "chromium"}), "chromium")
            self.assertEqual(W.engine({}), "webkit")                     # made before the choice: its login
        with mock.patch.object(W, "chromium_browser", return_value=None):
            self.assertEqual(W.engine({"engine": "chromium"}), "webkit")   # browser gone

    def test_command(self):
        cmd = W.chromium_command("wabc", {"url": "https://web.whatsapp.com/"}, "/usr/bin/google-chrome-stable")
        self.assertEqual(cmd[0], "/usr/bin/google-chrome-stable")
        self.assertIn("--app=https://web.whatsapp.com/", cmd)
        self.assertTrue(any(a.startswith("--user-data-dir=") and a.endswith("wabc/chromium") for a in cmd))

    def test_window_class(self):
        """Chrome names an --app window itself (seen: chrome-example.com__-Default; --class ignored)."""
        self.assertEqual(W.chromium_app_id("https://example.com"), "chrome-example.com__-Default")
        self.assertEqual(W.chromium_app_id("https://web.whatsapp.com/"), "chrome-web.whatsapp.com__-Default")
        self.assertEqual(W.chromium_app_id("https://x.com/a/b?q=1"), "chrome-x.com__a_b-Default")
        with mock.patch.object(W, "chromium_browser", return_value=("/b", "B")):
            e = {"name": "Zap", "url": "https://web.whatsapp.com/", "engine": "chromium"}
            self.assertIn("StartupWMClass=chrome-web.whatsapp.com__-Default\n", W.desktop_text("wabc", e, "s"))
            e["engine"] = "webkit"
            self.assertIn(f"StartupWMClass={W.app_id('wabc')}\n", W.desktop_text("wabc", e, "s"))

    def test_main_runs_the_browser(self):
        with mock.patch.dict(W.os.environ, {W.SCOPED_ENV: "1"}), mock.patch.object(W.MemoryGuard, "start"), \
                mock.patch.object(W, "get", return_value={"url": "https://x/", "engine": "chromium"}), \
                mock.patch.object(W, "chromium_browser", return_value=("/b", "B")), \
                mock.patch.object(W, "run_chromium", return_value=0) as run, \
                mock.patch("sonata2.webapps.window.main") as webkit:
            W.main(["wabc"])
        run.assert_called_once_with("wabc")
        webkit.assert_not_called()

    def test_saved(self):
        import tempfile
        d = tempfile.mkdtemp()
        from sonata2 import config
        with mock.patch.object(config, "CONFIG_DIR", d), mock.patch.object(W, "data_dir", return_value=d), \
                mock.patch.object(W, "write_desktop"), mock.patch.object(W, "update_theme_icon"):
            app = W.create("Zap", "web.whatsapp.com", fetch=False, engine_name="webkit")
            self.assertEqual(W.get(app)["engine"], "webkit")
            W.update(app, engine_name="chromium")
            self.assertEqual(W.get(app)["engine"], "chromium")
            W.update(app, engine_name="nonsense")
            self.assertEqual(W.get(app)["engine"], "chromium")


class BraveTest(unittest.TestCase):
    """Vini: web apps couldn't be made with Brave when Chrome was installed too
    -- only the first Chromium-family browser found was offered. Each one
    installed is a choice in the form, Brave among them."""

    INSTALLED = {"google-chrome-stable": "/usr/bin/google-chrome-stable", "brave": "/usr/bin/brave"}

    def which(self):
        return mock.patch.object(W.shutil, "which", side_effect=self.INSTALLED.get)

    def test_only_chrome_offered_when_brave_installed_too(self):
        with self.which():
            self.assertEqual([(n, k) for _p, n, k in W.chromium_browsers()],
                             [("Google Chrome", "chrome"), ("Brave", "brave")])
            self.assertEqual(W.chromium_browser(), ("/usr/bin/google-chrome-stable", "Google Chrome"))
            self.assertEqual(W.chromium_browser({"browser": "brave"}), ("/usr/bin/brave", "Brave"))

    def test_brave_app_runs_in_brave_with_its_own_profile_and_app_id(self):
        e = {"url": "https://web.whatsapp.com/", "engine": "chromium", "browser": "brave"}
        with self.which():
            self.assertEqual(W.window_class("wabc", e), "brave-web.whatsapp.com__-Default")
            self.assertTrue(W.profile_dir("wabc", e).endswith("wabc/brave"))
            self.assertTrue(W.profile_dir("wabc", {**e, "browser": "chrome"}).endswith("wabc/chromium"))

    def test_brave_flatpak_not_found(self):
        """Vini's Brave is a Flatpak (com.brave.Browser): it wasn't offered at
        all -- only browsers on PATH were. A Flatpak one is, run with
        `flatpak run`, its profile in its own folder (it can't reach home)."""
        with mock.patch.object(W.shutil, "which", side_effect={"flatpak": "/usr/bin/flatpak"}.get), \
                mock.patch.object(W.os.path, "isdir", side_effect=lambda p: p.endswith("/com.brave.Browser")):
            self.assertEqual(W.chromium_browsers(), [("flatpak:com.brave.Browser", "Brave", "brave")])
            e = {"url": "https://web.whatsapp.com/", "engine": "chromium", "browser": "brave"}
            cmd = W.chromium_command("wabc", e, "flatpak:com.brave.Browser")
            self.assertEqual(cmd[:3], ["flatpak", "run", "com.brave.Browser"])
            self.assertIn("--app=https://web.whatsapp.com/", cmd)
            prof = next(a for a in cmd if a.startswith("--user-data-dir="))
            self.assertTrue(prof.endswith("/.var/app/com.brave.Browser/data/sonata2-webapps/wabc"), prof)
            self.assertEqual(W.window_class("wabc", e), "brave-web.whatsapp.com__-Default")

    def test_brave_gone_falls_back(self):
        with mock.patch.object(W.shutil, "which", side_effect={"chromium": "/usr/bin/chromium"}.get):
            self.assertEqual(W.chromium_browser({"browser": "brave"}), ("/usr/bin/chromium", "Chromium"))

    def test_browser_saved(self):
        import tempfile
        d = tempfile.mkdtemp()
        from sonata2 import config
        with mock.patch.object(config, "CONFIG_DIR", d), mock.patch.object(W, "data_dir", return_value=d), \
                mock.patch.object(W, "write_desktop"), mock.patch.object(W, "update_theme_icon"):
            app = W.create("Zap", "web.whatsapp.com", fetch=False, engine_name="chromium", browser="brave")
            self.assertEqual(W.get(app)["browser"], "brave")
            W.update(app, browser="chrome")
            self.assertEqual(W.get(app)["browser"], "chrome")
            W.update(app, browser="nonsense")
            self.assertEqual(W.get(app)["browser"], "chrome")


class SizeTest(unittest.TestCase):
    """Vini: the web app (in Chrome) didn't keep the size he gave its window."""

    def setUp(self):
        import tempfile
        from sonata2 import config
        self.p = mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp())
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_size_of_an_app(self):
        from sonata2 import winsize
        self.assertEqual(W.window_size("a"), W.DEFAULT_SIZE)
        winsize.save(W.size_key("a"), 900, 700)
        self.assertEqual(W.window_size("a"), (900, 700))
        self.assertIn("--window-size=900,700", W.chromium_command("a", {"url": "https://x/"}, "/b"))

    def test_view_size(self):
        views = [{"app-id": "other", "geometry": {"width": 5, "height": 5}},
                 {"app-id": "chrome-x__-Default", "geometry": {"width": 1000, "height": 640}}]
        self.assertEqual(W.view_size(views, "chrome-x__-Default"), (1000, 640))
        views[1]["tiled-edges"] = 15                                     # maximized: not its size
        self.assertIsNone(W.view_size(views, "chrome-x__-Default"))
        self.assertIsNone(W.view_size([], "chrome-x__-Default"))

    def test_webkit_window_uses_it(self):
        import inspect
        from sonata2.webapps import window
        self.assertIn("remember_size(self, W.size_key(wid)", inspect.getsource(window.WebAppWindow))


if __name__ == "__main__":
    unittest.main()


class OneAppPerSiteTest(unittest.TestCase):
    def test_whatsapp_made_twice(self):
        """Vini: the WhatsApp web app was there twice -- both get one app id
        in the browser, so the Dock mixed up their windows and opening one
        opened Chrome. The same site again is the web app already made."""
        import tempfile
        from sonata2 import config
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()), \
                mock.patch.object(W, "data_dir", side_effect=lambda a: tempfile.mkdtemp()), \
                mock.patch.object(W, "update_theme_icon"), mock.patch.object(W, "write_desktop"):
            first = W.create("WhatsApp", "https://web.whatsapp.com/", fetch=False)
            again = W.create("WhatsApp", "web.whatsapp.com", fetch=False)
            other = W.create("Instagram", "https://www.instagram.com/", fetch=False)
            self.assertEqual(again, first)
            self.assertNotEqual(other, first)
            self.assertEqual(sorted(W.apps()), sorted([first, other]))


    def test_removed_app_came_back_in_the_same_process(self):
        """A web app made before webapps.json existed went into DEFAULTS' own
        "apps" dict (a shallow copy): every later read in that process --
        the Dock, the form -- still had it, in any folder, removed or not."""
        import tempfile
        from sonata2 import config
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()), \
                mock.patch.object(W, "data_dir", side_effect=lambda a: tempfile.mkdtemp()), \
                mock.patch.object(W, "update_theme_icon"), mock.patch.object(W, "write_desktop"):
            W.create("WhatsApp", "https://web.whatsapp.com/", fetch=False)
        self.assertEqual(W.DEFAULTS, {"apps": {}})
        with mock.patch.object(config, "CONFIG_DIR", tempfile.mkdtemp()):
            self.assertEqual(W.apps(), {})
