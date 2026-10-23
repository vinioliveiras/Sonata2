"""Vini: with a bigger window radius the traffic lights stayed where they
were and crowded the rounder corner; the Dock's end icons did the same with
a rounder plate. What sits by a corner now moves in with it (half the extra
radius). xvfb-run -a python3 -m unittest tests.test_corner_room"""
import json
import os
import tempfile
import time
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

from sonata2 import adwstyle, config, wfconfig  # noqa: E402
from sonata2.ui import tokens  # noqa: E402


def set_radii(**radii) -> None:
    path = os.path.join(config.CONFIG_DIR, "appearance.json")
    os.makedirs(config.CONFIG_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"radius": radii}, f)
    t = time.time() + len(json.dumps(radii))           # a new mtime each time (the cache)
    os.utime(path, (t, t))


class CornerRoomTest(unittest.TestCase):
    def tearDown(self):
        try:
            os.remove(os.path.join(config.CONFIG_DIR, "appearance.json"))
        except OSError:
            pass

    def test_window_buttons_crowded_a_rounder_corner(self):
        set_radii(window=24)
        f = tokens.frame()
        self.assertEqual(f["radius"], 24)
        extra = (24 - tokens.FRAME["radius"]) / 2
        self.assertEqual(f["dot_left"], tokens.FRAME["dot_left"] + extra)
        self.assertEqual(f["dot_top"], tokens.FRAME["dot_top"] + extra)
        # Sonata's windows: the theme's tokens
        tl = tokens.palette(False)
        self.assertEqual(tl["tl_margin_left"],
                         f"{tokens.FRAME['dot_left'] + extra - tokens.FRAME['dot'] / 2 - tokens.FRAME['dot_gap'] / 2:g}px")
        # other apps' title bars: pixdecor and GTK 4 / GTK 3
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(f)}
        self.assertEqual(opts[("pixdecor", "left_button_x_offset")],
                         str(round(f["dot_left"] - tokens.FRAME["dot"] / 2)))
        self.assertIn(f"margin-left: {f['dot_left'] - tokens.FRAME['dot'] / 2 - adwstyle.ADW_START_INSET:g}px",
                      adwstyle.css(bars=False))

    def test_designed_radius_and_smaller_keep_the_designed_place(self):
        for r in (tokens.FRAME["radius"], 0):
            set_radii(window=r)
            f = tokens.frame()
            self.assertEqual((f["dot_left"], f["dot_top"]),
                             (tokens.FRAME["dot_left"], tokens.FRAME["dot_top"]))

    def test_button_size_keeps_the_room_from_the_edges(self):
        """Vini: the window buttons' size in Appearance, without breaking
        the rest: a bigger button grows inwards, its edge stays put."""
        F = tokens.FRAME
        set_radii()
        self.assertEqual(tokens.user_button_size(), F["dot"])
        path = os.path.join(config.CONFIG_DIR, "appearance.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"button_size": 16}, fh)
        os.utime(path, (time.time() + 99, time.time() + 99))
        f = tokens.frame()
        self.assertEqual(f["dot"], 16)
        self.assertEqual(f["dot_left"] - f["dot"] / 2, F["dot_left"] - F["dot"] / 2)    # the same room
        self.assertEqual(f["dot_top"] - f["dot"] / 2, F["dot_top"] - F["dot"] / 2)
        self.assertGreater(f["dot_gap"], F["dot_gap"])
        self.assertEqual(tokens.palette(False)["tl_dot"], "16px")             # Sonata's windows
        sheet = adwstyle.css(bars=False)                                      # GTK 4 apps
        self.assertIn("min-width: 16px", sheet)
        self.assertIn("min-width: 16px", adwstyle.css3("/x"))                # GTK 3, Chrome, Electron
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(f)}           # pixdecor
        self.assertEqual(opts[("pixdecor", "left_button_spacing")], str(f["dot_gap"]))
        self.assertEqual(opts[("pixdecor", "left_button_x_offset")], str(round(F["dot_left"] - F["dot"] / 2)))
        from sonata2 import trafficlights
        d = trafficlights.png_folder(False)                                   # pixdecor draws PNGs 1:1
        import cairo
        self.assertEqual(cairo.ImageSurface.create_from_png(os.path.join(d, "close.png")).get_width(), 16)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"button_size": 99}, fh)
        os.utime(path, (time.time() + 199, time.time() + 199))
        self.assertEqual(tokens.user_button_size(), tokens.BUTTON_SIZE_RANGE[1])   # kept in range

    def test_greyed_out_zoom_turned_green_in_another_size_or_look(self):
        """A fixed-size window's zoom button (Settings) is grey; with the
        pictures drawn for another size or colours it came out green."""
        from sonata2 import trafficlights
        path = os.path.join(config.CONFIG_DIR, "appearance.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"button_size": 16}, fh)
        os.utime(path, (time.time() + 299, time.time() + 299))
        rules = trafficlights.css_rules(False)
        self.assertIn(".tl-zoom:not(.tl-disabled)", rules)
        self.assertNotIn(".traffic button.tl-zoom {", rules)

    def test_dock_end_icons_crowded_a_rounder_plate(self):
        from sonata2.shell import dock
        self.assertEqual(dock.pad_side(), dock.PAD_SIDE)
        set_radii(dock=32)
        self.assertEqual(dock.pad_side(), dock.PAD_SIDE + (32 - tokens.RADIUS_DEFAULTS["dock"]) / 2)
        set_radii(dock=4)
        self.assertEqual(dock.pad_side(), dock.PAD_SIDE)


if __name__ == "__main__":
    unittest.main()
