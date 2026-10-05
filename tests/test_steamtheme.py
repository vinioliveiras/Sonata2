"""Vini: Steam keeps its own theme, but in Sonata's window layout: only its
window buttons become Sonata's round traffic lights, on Sonata's side; an
earlier full Adwaita skin is taken out; Steam updates are patched again."""
import os
import tempfile
import unittest
from unittest import mock

from sonata2 import config, steamtheme as S


class SteamThemeTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.root = os.path.join(self.home, ".steam", "steam")
        os.makedirs(os.path.join(self.root, "steamui", "css"))
        self.original = ".x{color:red}\n" + "/* steam */\n" * 284 + "." * (4000 - 14 - 12 * 284)
        for f in ("library", "gamerecording", "gamenotes"):
            with open(os.path.join(self.root, "steamui", "css", f + ".css"), "w") as fh:
                fh.write(self.original)
        cfg = tempfile.mkdtemp()
        for p in (mock.patch.dict(os.environ, {"HOME": self.home, "XDG_CACHE_HOME": os.path.join(self.home, "c")}),
                  mock.patch.object(config, "CONFIG_DIR", cfg)):
            p.start()
            self.addCleanup(p.stop)

    def css(self, name):
        with open(os.path.join(self.root, "steamui", "css", name), encoding="utf-8") as fh:
            return fh.read()

    def test_css_is_only_sonatas_window_buttons(self):
        css = S.window_css()
        self.assertEqual(css.count("svg+xml"), 8)                    # close/min/max/restore x plain/hover
        self.assertIn("border-radius: 50%", css)                     # round, not Steam's squares
        self.assertIn("div._3LKQ3S_yqrebeNLF6aeiog { display: none", css)   # no Big Picture button
        self.assertNotIn("--adw-", css)                              # Steam's own colours stay
        self.assertIn("left: 7px", css)                              # Sonata's place, on the left
        self.assertRegex(css, r"window-controls\) \{ position: fixed !important; top: 8px !important; right: 7px")  # Friends...
        self.assertIn("div.qP17eBPXkfezFfexZ4hC3 { flex: 0 0 8px", css)   # no empty room after the profile
        self.assertIn("margin-left:", css)                           # Steam's menu moved clear of them
        # Friends / Settings keep the buttons on the right: close at the edge (Windows' order)
        self.assertRegex(css, r"closeButton \{ order: 2 !important; background-image")
        self.assertRegex(css, r"maximizeButton \{ order: 0 !important; background-image")
        self.assertRegex(css, r":has\(div\._3Z7VQ1IMk4E3HsHvrkLNgo\) .*\.closeButton \{ order: 0")   # main window: macOS
        config.update("appearance", buttons_side="right")
        css = S.window_css()
        self.assertIn("right: 7px", css)
        self.assertNotIn("margin-left:", css)

    def test_patches_once_and_again_after_a_steam_update(self):
        self.assertEqual(list(S.targets()), ["default"])
        self.assertFalse(S.installed(self.root))
        self.assertTrue(S.ensure())
        self.assertTrue(S.installed(self.root))
        lib = self.css("library.css")
        self.assertTrue(lib.startswith(S.HEADER))
        self.assertEqual(len(lib), 4000)                             # padded to the original's size
        self.assertIn('@import url("library.original.css")', lib)
        self.assertEqual(self.css("library.original.css"), self.original)
        S.ensure()                                                   # again: nothing changes
        self.assertEqual(self.css("library.original.css"), self.original)
        with open(os.path.join(self.root, "steamui", "css", "library.css"), "w") as fh:
            fh.write(self.original)                                  # Steam updated itself
        self.assertFalse(S.installed(self.root))
        S.ensure()
        self.assertTrue(S.installed(self.root))
        config.update("appearance", steam_theme=False)               # turned off: left alone by ensure
        self.assertFalse(S.ensure())
        S.remove()
        self.assertEqual(self.css("library.css"), self.original)
        self.assertFalse(os.path.exists(os.path.join(self.root, "steamui", "css", "library.original.css")))
        self.assertFalse(os.path.isdir(os.path.join(self.root, "steamui", S.SKIN_DIR)))

    def test_earlier_adwaita_skin_is_taken_out(self):
        css = os.path.join(self.root, "steamui", "css")
        os.makedirs(os.path.join(self.root, "steamui", "adwaita"))
        for f in ("library", "gamerecording", "gamenotes"):
            os.replace(os.path.join(css, f + ".css"), os.path.join(css, f + ".original.css"))
            with open(os.path.join(css, f + ".css"), "w") as fh:
                fh.write(S.ADWAITA_HEADER + "\n@import url('../adwaita/base.css');\n")
        S.apply()
        self.assertFalse(os.path.isdir(os.path.join(self.root, "steamui", "adwaita")))
        self.assertEqual(self.css("gamerecording.original.css"), self.original)   # Steam's own, not Adwaita's
        self.assertTrue(S.installed(self.root))

    def test_steam_window_corners_rounded(self):
        """Steam draws its own square frame: sonata-corners rounds it too
        (exact app_id: its games, steam_app_<id>, keep theirs)."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        self.assertIn("own_frame_apps = steam", (root / "config" / "wayfire.ini").read_text())
        self.assertIn('name="own_frame_apps"', (root / "wayfire-plugin" / "metadata" / "sonata-corners.xml").read_text())
        cpp = (root / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()
        self.assertIn("|| own_frame_app(view)", cpp)
        self.assertIn("(m.left <= 0) && (m.top <= 0)", cpp)          # no decoration shadow inset for it

    def test_wired(self):
        import inspect
        from sonata2.settings import app
        from sonata2.shell import topbar
        self.assertIn("steamtheme.Watch()", inspect.getsource(topbar))
        self.assertIn('"Sonata look for Steam"', inspect.getsource(app))


if __name__ == "__main__":
    unittest.main()
