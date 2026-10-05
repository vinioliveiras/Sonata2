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
