"""User corner radii (xvfb-run python3 -m unittest tests.test_radius)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import config, icons, ui  # noqa: E402
from sonata2.ui import tokens as T  # noqa: E402


def settle(ms=400):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class RadiusTest(unittest.TestCase):
    def setUp(self):
        p = os.path.join(config.CONFIG_DIR, "appearance.json")
        if os.path.exists(p):
            os.remove(p)

    def test_defaults_are_the_designed_values(self):
        self.assertEqual(T.user_radii(), T.RADIUS_DEFAULTS)
        pal = T.palette(False)
        for k, v in (("r_window", "10px"), ("r_button", "5px"), ("r_plate", "18px"), ("r_menu", "7px"),
                     ("r_menu_row", "4px"), ("r_label", "6px"), ("r_dialog", "12px")):
            self.assertEqual(pal[k], v, k)
        self.assertEqual(T.frame()["radius"], T.FRAME["radius"])

    def test_user_values_reach_every_place(self):
        config.update("appearance", radius={"window": 16, "dock": 4, "menu": 99})
        r = T.user_radii()
        self.assertEqual(r, {"window": 16, "dock": 4, "menu": T.RADIUS_RANGE["menu"][1]})     # clamped
        pal = T.palette(True)
        self.assertEqual((pal["r_window"], pal["r_plate"], pal["r_menu"]), ("16px", "4px", "16px"))
        self.assertEqual(pal["r_button"], "8px")
        from sonata2 import adwstyle, wfconfig
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(T.frame())}
        self.assertEqual(opts[("pixdecor", "rounded_corner_radius")], "16")
        self.assertEqual(opts[("sonata-corners", "radius")], "16")
        self.assertIn("border-radius: 16px", adwstyle.css("/x"))
        sh = open(os.path.join(os.path.dirname(T.__file__), "..", "..", "tools", "wayfire-config.sh")).read()
        self.assertIn("tokens.frame()", sh)

    def test_other_appearance_saves_keep_it(self):
        self.assertIn("radius", icons.APPEARANCE_DEFAULTS)            # config.load keeps known keys only
        config.update("appearance", radius={"window": 3})
        cfg = config.load("appearance", icons.APPEARANCE_DEFAULTS)
        config.save("appearance", cfg)
        self.assertEqual(T.user_radii()["window"], 3)

    def test_settings_sliders_save(self):
        Adw.init()
        ui.setup()
        from sonata2.settings import app as S
        win = S.Settings(None)
        win.present()
        settle(200)
        self.assertEqual(set(win.radius_rows), {"window", "dock", "menu"})
        with mock.patch.object(S.system, "run_async"):
            win.radius_rows["dock"].slider.set_value(24)
            settle(500)
        self.assertEqual(T.user_radii()["dock"], 24)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
