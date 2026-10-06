"""Vini: un-maximizing left a smear of the title bar: Wayfire's grid
crossfaded a picture of the window at its old size into the new one (two
title bars over each other). sonata-resize animates zoom like macOS: the
window's frame moves to its new place, the contents fade in there; grid's
crossfade is off when sonata-resize is loaded."""
import os
import pathlib
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "wayfire-plugin" / "src" / "sonata-resize.cpp").read_text()


def body(name, until):
    return SRC[SRC.index(name):SRC.index(until, SRC.index(name))]


class ZoomPluginTest(unittest.TestCase):
    def test_follows_every_tile_request(self):
        self.assertIn("wf::signal::connection_t<wf::view_tile_request_signal> on_tile_request", SRC)
        self.assertIn("output->connect(&on_tile_request);", SRC)
        self.assertIn("start_zoom(ev->view, ev->desired_size);", SRC)

    def test_frame_moves_contents_hidden_then_faded_in(self):
        start = body("void start_zoom(", "wf::geometry_t zoom_target()")
        self.assertIn("zoom_from = v->get_geometry();", start)          # from where it is now
        self.assertIn("set_alpha(v, 0.0);", start)                      # no picture of the old size
        self.assertIn("ghost = std::make_shared<ghost_node_t>();", start)   # the outline resize's frame
        step = body("void zoom_step()", "void end_zoom(")
        self.assertIn("ghost->set(r);", step)
        self.assertIn("end_zoom(true);", step)
        end = body("void end_zoom(bool fade_in)", "};\n\nDECLARE")
        self.assertIn("fading->connect(&on_new_size);", end)            # the outline resize's fade
        self.assertIn("start_fade();", end)

    def test_not_while_dragging_or_resizing(self):
        start = body("void start_zoom(", "wf::geometry_t zoom_target()")
        self.assertIn('output->is_plugin_active("move")', start)       # a maximized window dragged off
        self.assertIn("|| view ||", start)                               # a resize under way
        self.assertIn("v->pending_fullscreen()", start)

    def test_cleaned_up(self):
        gone = body("on_view_disappeared =", "wf::button_callback activate_binding;")
        self.assertIn("end_zoom(false);", gone)
        fini = body("void fini() override", "void start_zoom(")
        self.assertIn("end_zoom(false);", fini)

    def test_option(self):
        xml = (ROOT / "wayfire-plugin" / "metadata" / "sonata-resize.xml").read_text()
        self.assertRegex(xml, r'name="zoom" type="bool">[\s\S]*?<default>true</default>')


class GridCrossfadeOffTest(unittest.TestCase):
    def run_config(self, plugins, built):
        home = tempfile.mkdtemp()
        pdir = os.path.join(home, ".local/share/wayfire/plugin-manager/install/lib/wayfire")
        os.makedirs(pdir)
        for p in built:
            open(os.path.join(pdir, f"lib{p}.so"), "w").close()
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "in.ini"), "w") as f:
            f.write(f"[core]\nplugins = {plugins}\n\n[grid]\nslot_c = <super> KEY_UP\n")
        r = subprocess.run(["bash", str(ROOT / "tools/wayfire-config.sh"), os.path.join(d, "in.ini"),
                            os.path.join(d, "out.ini")], capture_output=True, text=True,
                           env={**os.environ, "HOME": home, "XDG_DATA_HOME": os.path.join(home, ".local/share"),
                                "XDG_CONFIG_HOME": os.path.join(home, ".config"), "PYTHONPATH": str(ROOT)})
        self.assertEqual(r.returncode, 0, r.stderr)
        return open(os.path.join(d, "out.ini")).read()

    def test_off_with_sonata_resize(self):
        out = self.run_config("autostart grid resize", ["autostart", "grid", "resize", "sonata-resize"])
        grid = out[out.index("[grid]"):]
        self.assertRegex(grid.split("\n[")[0], r"(?m)^type = none$")

    def test_kept_without_it(self):
        out = self.run_config("autostart grid resize", ["autostart", "grid", "resize"])
        grid = out[out.index("[grid]"):].split("\n[")[0]
        self.assertNotIn("type = none", grid)


if __name__ == "__main__":
    unittest.main()
