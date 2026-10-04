"""Show window contents while resizing (Vini: Chrome with a heavy page
juddered while a window was resized). Off: Sonata's resize plugin moves only
the window's background and fades the contents in on release."""
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent


class PluginTest(unittest.TestCase):
    def test_plugin_source(self):
        src = (ROOT / "wayfire-plugin" / "src" / "sonata-resize.cpp").read_text()
        self.assertIn('live{"sonata-resize/live"}', src)
        self.assertIn("ghost->set(desired);", src)                         # only the background follows
        self.assertIn("uniform float radius;", src)                       # rounded, like the window
        self.assertIn("float shadow = ", src)                             # with its shadow
        self.assertIn("set_alpha(view, 0.0);", src)
        self.assertIn("view_geometry_changed_signal", src)                # contents fade once redrawn
        self.assertIn("> 400", src)                                       # a frozen app: shown anyway
        self.assertIn('.name = "resize"', src)                            # same activation as resize
        meta = (ROOT / "wayfire-plugin" / "metadata" / "sonata-resize.xml").read_text()
        self.assertIn('<option name="live" type="bool">', meta)
        self.assertIn("<default>true</default>", meta)                    # Mac-like by default
        meson = (ROOT / "wayfire-plugin" / "meson.build").read_text()
        self.assertIn("shared_module('sonata-resize'", meson)
        self.assertIn("metadata/sonata-resize.xml", meson)

    def test_plugins_build_on_wayfire_0_12(self):
        """Vini: the new resize did nothing -- Sonata's plugins had stopped
        building (sonata-corners: wlr_surface incomplete, min(int, double)),
        so neither the new one nor the FPS counter got installed, and
        install.sh only said "couldn't build it"."""
        src = (ROOT / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()
        self.assertIn("#include <wayfire/nonstd/wlroots-full.hpp>", src)
        self.assertNotIn("std::min(f.width, f.height) / 2);", src)
        inst = (ROOT / "install.sh").read_text()
        self.assertIn('grep -m8 -E "error|FAILED" "$bdir.log"', inst)       # the reason, right there

    def test_replaces_resize_once_built(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "in.ini"), "w") as f:
            f.write("[core]\nplugins = alpha move resize ipc\n")
        env = dict(os.environ, XDG_CONFIG_HOME=d, XDG_DATA_HOME=d)
        run = lambda: subprocess.run(["bash", str(ROOT / "tools" / "wayfire-config.sh"), os.path.join(d, "in.ini"),
                                      os.path.join(d, "out.ini")], env=env, capture_output=True)
        run()
        self.assertIn(" resize ", open(os.path.join(d, "out.ini")).read())          # not built: Wayfire's
        lib = os.path.join(d, "wayfire/plugin-manager/install/lib/wayfire")
        os.makedirs(lib)
        open(os.path.join(lib, "libsonata-resize.so"), "w").close()
        run()
        line = next(l for l in open(os.path.join(d, "out.ini")) if l.startswith("plugins"))
        self.assertIn("sonata-resize", line.split())
        self.assertNotIn("resize", line.split())

    def test_colours_follow_the_appearance(self):
        from sonata2 import titlebars
        sets = []
        with mock.patch("sonata2.backend.system.wayfire_set", side_effect=lambda *a: sets.append(a)):
            try:
                titlebars.apply_colors(True)
            except Exception:
                pass
        keys = {(a[0], a[1]) for a in sets}
        self.assertIn(("sonata-resize", "fill"), keys)
        self.assertIn(("sonata-resize", "border"), keys)

    def test_setting(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw
        Adw.init()
        from sonata2.settings import app as S
        win = S.Settings.__new__(S.Settings)
        win._save = lambda *a: None
        win._save_live = lambda *a: None
        with mock.patch.object(S.system, "wayfire_get", return_value="true"), \
                mock.patch.object(S.system, "run_async") as ra, mock.patch.object(S.system, "default_browser",
                                                                                   return_value=""):
            groups = S.Settings._page_dock(win)
            rows = []

            def walk(w):
                if isinstance(w, Adw.SwitchRow):
                    rows.append(w)
                c = w.get_first_child()
                while c is not None:
                    walk(c)
                    c = c.get_next_sibling()
            for g in groups:
                walk(g)
            row = next(r for r in rows if r.get_title() == "Show window contents while resizing")
            self.assertTrue(row.get_active())
            row.set_active(False)
        self.assertEqual(ra.call_args[0][2:], ("sonata-resize", "live", False))


if __name__ == "__main__":
    unittest.main()
