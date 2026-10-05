"""Vini: a way to watch every window style Sonata puts on other apps.
`sonata2 doctor windows` lists the apps' settings and the open windows;
the menu bar tells (once) when one broke."""
import json
import os
import tempfile
import unittest
from unittest import mock

from sonata2 import stylecheck as SC


def view(i, app, title="t", sonata_bar=True, pid=-1, **kw):
    g = {"x": 0, "y": 0, "width": 800, "height": 600}
    b = dict(g, height=600 - (38 if sonata_bar else 0))
    return dict({"id": i, "app-id": app, "title": title, "type": "toplevel", "mapped": True, "pid": pid,
                 "geometry": g, "base-geometry": b, "fullscreen": False}, **kw)


class WindowRowsTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(SC, "_own_frame_apps", lambda: {"steam"})
        p.start()
        self.addCleanup(p.stop)

    def rows(self, views, rounded):
        return {r[1].split(" -- ")[0]: r for r in SC.window_rows(views, rounded)}

    def test_frames_and_corners(self):
        r = self.rows([view(1, "code"), view(2, "steam", sonata_bar=False),
                       view(3, "google-chrome", sonata_bar=False), view(4, "Spotify", sonata_bar=False),
                       view(5, "io.github.vinioliveiras.sonata2.files"), view(6, "mpv", mapped=False)],
                      rounded={1, 2})
        self.assertEqual(r["code"][0], SC.OK)
        self.assertIn("Sonata's title bar", r["code"][2])
        self.assertEqual(r["steam"][0], SC.OK)                        # own frame Sonata styles, rounded
        self.assertEqual(r["google-chrome"][0], SC.OK)
        self.assertEqual(r["Spotify"][0], SC.WARN)                    # its own title bar
        self.assertNotIn("io.github.vinioliveiras.sonata2.files", r)  # Sonata's own
        self.assertNotIn("mpv", r)                                    # not shown

    def test_corners_missing_is_a_failure(self):
        r = self.rows([view(1, "code"), view(2, "steam", sonata_bar=False)], rounded=set())
        self.assertEqual(r["code"][0], SC.FAIL)
        self.assertEqual(r["steam"][0], SC.FAIL)

    def test_old_plugin_build(self):
        rows = SC.window_rows([view(1, "code")], rounded=None)
        self.assertEqual(rows[0][0], SC.OK)                           # can't tell: not a failure
        self.assertEqual(rows[-1][1], "Rounded corners")


class NotifyOnceTest(unittest.TestCase):
    def test_told_once_and_again_when_it_comes_back(self):
        path = os.path.join(tempfile.mkdtemp(), "seen.json")
        bad = [(SC.FAIL, "VS Code", "x"), (SC.WARN, "Spotify -- Spotify", "draws its own title bar: not ...")]
        self.assertEqual(SC.new_problems(bad, path), ["VS Code", "Spotify (own title bar)"])
        self.assertEqual(SC.new_problems(bad, path), [])              # not twice
        self.assertEqual(SC.new_problems([], path), [])               # fixed: forgotten
        self.assertEqual(SC.new_problems(bad[:1], path), ["VS Code"])  # back: told again


class ConfigRowsTest(unittest.TestCase):
    def test_vscode_setting_undone(self):
        home = tempfile.mkdtemp()
        cfg = os.path.join(home, ".config")
        os.makedirs(os.path.join(cfg, "Code", "User"))
        with open(os.path.join(cfg, "Code", "User", "settings.json"), "w") as f:
            json.dump({"window.titleBarStyle": "custom"}, f)
        with mock.patch.dict(os.environ, {"HOME": home, "XDG_CONFIG_HOME": cfg}), \
                mock.patch("gi.repository.GLib.get_user_config_dir", lambda: cfg), \
                mock.patch("sonata2.titlebars.enabled", lambda: True), \
                mock.patch.object(SC, "steam_rows", lambda: []):
            rows = {r[1]: r for r in SC.config_rows()}
        self.assertEqual(rows["Code"][0], SC.FAIL)

    def test_official_claude_is_not_ok(self):
        """The official Claude Desktop ignores CLAUDE_NATIVE_TITLEBAR (only the
        community package reads it): not reported as styled."""
        d = tempfile.mkdtemp()
        launcher = os.path.join(d, "claude-desktop")
        with open(launcher, "w") as f:
            f.write("#!/bin/sh\nexec electron /usr/lib/claude-desktop/app.asar\n")
        os.chmod(launcher, 0o755)
        cfg = tempfile.mkdtemp()
        with mock.patch.dict(os.environ, {"PATH": d, "CLAUDE_NATIVE_TITLEBAR": "1"}), \
                mock.patch("gi.repository.GLib.get_user_config_dir", lambda: cfg), \
                mock.patch("sonata2.titlebars.enabled", lambda: True), \
                mock.patch("sonata2.titlebars.chromium_roots", lambda c: []), \
                mock.patch.object(SC, "steam_rows", lambda: []):
            rows = {r[1]: r for r in SC.config_rows()}
        self.assertEqual(rows["Claude Desktop"][0], SC.WARN)
        self.assertIn("official", rows["Claude Desktop"][2])

    def test_wired(self):
        import inspect
        from sonata2 import __main__ as M
        from sonata2.shell import topbar
        self.assertIn('sys.argv[2] == "windows"', inspect.getsource(M))
        self.assertIn("stylecheck.Watch()", inspect.getsource(topbar))


if __name__ == "__main__":
    unittest.main()
