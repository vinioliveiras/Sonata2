"""Vini: Steam in Sonata's window look. Adwaita-for-Steam's installer patches
Steam's interface CSS with Sonata's colours, buttons' side and its own
traffic-light pictures; Sonata puts it back after Steam updates."""
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
        for f in ("library", "gamerecording", "gamenotes"):
            with open(os.path.join(self.root, "steamui", "css", f + ".css"), "w") as fh:
                fh.write(".x{}\n")
        cfg = tempfile.mkdtemp()
        for p in (mock.patch.dict(os.environ, {"HOME": self.home, "XDG_CACHE_HOME": os.path.join(self.home, "c")}),
                  mock.patch.object(config, "CONFIG_DIR", cfg)):
            p.start()
            self.addCleanup(p.stop)

    def patch(self):
        os.makedirs(os.path.join(self.root, "steamui", "adwaita"), exist_ok=True)
        with open(os.path.join(self.root, "steamui", "css", "library.css"), "w") as fh:
            fh.write(S.PATCH_HEADER + "\n@import url('../adwaita/base.css');\n")

    def test_css_has_sonatas_look(self):
        css = S.custom_css()
        self.assertEqual(css.count("svg+xml"), 12)                   # close/min/max/restore x plain/hover/active
        self.assertIn("--adw-headerbar-bg: light-dark(", css)
        opts = S.options("/x.css")
        self.assertEqual(opts[opts.index("--windowcontrols-layout") + 1], "close,minimize,maximize:")
        config.update("appearance", buttons_side="right")
        opts = S.options("/x.css")
        self.assertEqual(opts[opts.index("--windowcontrols-layout") + 1], ":maximize,minimize,close")

    def test_installs_once_and_again_after_a_steam_update(self):
        runs = []

        def run(args):
            runs.append(args)
            self.patch()
            return True
        with mock.patch.object(S, "_run", run):
            self.assertEqual(list(S.targets()), ["default"])
            self.assertFalse(S.installed(self.root))
            self.assertTrue(S.ensure())
            self.assertTrue(S.installed(self.root))
            S.ensure()
            self.assertEqual(len(runs), 1)                           # nothing changed: not again
            with open(os.path.join(self.root, "steamui", "css", "library.css"), "w") as fh:
                fh.write(".x{}\n")                                   # Steam updated itself
            S.ensure()
            self.assertEqual(len(runs), 2)
            config.update("appearance", accent="green")              # the accent changed
            S.ensure()
            self.assertEqual(len(runs), 3)
            self.assertIn("#62ba46", runs[-1])
            config.update("appearance", steam_theme=False)           # turned off: left alone
            S.ensure()
            self.assertEqual(len(runs), 3)

    def test_wired(self):
        import inspect
        from sonata2.settings import app
        from sonata2.shell import topbar
        self.assertIn("steamtheme.Watch()", inspect.getsource(topbar))
        self.assertIn('"Sonata look for Steam"', inspect.getsource(app))


if __name__ == "__main__":
    unittest.main()
