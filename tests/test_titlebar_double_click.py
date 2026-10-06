"""Vini: double-clicking a window's title bar resized it differently
depending on where you clicked -- on the top edge it did two tiny resizes
(sonata-resize) instead of what the title bar does. Now a double-click on the
top edge is the title bar's action (Zoom, or the Settings choice), a click
that doesn't move never resizes, and Settings sets both places."""
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "wayfire-plugin" / "src" / "sonata-resize.cpp").read_text()

HARNESS = r"""
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <chrono>
namespace wf { struct pointf_t { double x, y; }; }
enum { WLR_EDGE_TOP = 1, WLR_EDGE_BOTTOM = 2, WLR_EDGE_LEFT = 4, WLR_EDGE_RIGHT = 8 };
%s
int main()
{
    int fails = 0;
#define CHECK(c) if (!(c)) { std::printf("FAIL line %%d\n", __LINE__); fails++; }
    // two presses on the top edge, quickly, same spot, same window: a double-click
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 7, {100, 50}, 1000));
    CHECK(top_edge_double_click(WLR_EDGE_TOP, 7, {102, 51}, 1250));
    // a third press right after is a new first click
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 7, {102, 51}, 1300));
    // too slow
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 7, {100, 50}, 5000));
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 7, {100, 50}, 5500));
    // somewhere else on the edge
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 7, {100, 50}, 9000));
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 7, {160, 50}, 9100));
    // another window
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 7, {100, 50}, 12000));
    CHECK(!top_edge_double_click(WLR_EDGE_TOP, 8, {100, 50}, 12100));
    // corners and other edges stay resizes
    CHECK(!top_edge_double_click(WLR_EDGE_TOP | WLR_EDGE_LEFT, 7, {0, 0}, 15000));
    CHECK(!top_edge_double_click(WLR_EDGE_TOP | WLR_EDGE_LEFT, 7, {0, 0}, 15100));
    CHECK(!top_edge_double_click(WLR_EDGE_BOTTOM, 7, {0, 0}, 16000));
    CHECK(!top_edge_double_click(WLR_EDGE_BOTTOM, 7, {0, 0}, 16100));
    return fails;
}
"""


class TopEdgeTest(unittest.TestCase):
    def test_double_click_logic(self):
        cxx = shutil.which("g++") or shutil.which("c++")
        if not cxx:
            self.skipTest("no C++ compiler")
        start = SRC.index("static constexpr int DEAD_ZONE")
        end = SRC.index("class wayfire_resize")
        d = tempfile.mkdtemp()
        src, exe = os.path.join(d, "t.cpp"), os.path.join(d, "t")
        with open(src, "w") as f:
            f.write(HARNESS % SRC[start:end])
        subprocess.run([cxx, "-std=c++17", "-o", exe, src], check=True)
        r = subprocess.run([exe], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_a_double_click_does_the_title_bars_action(self):
        init = SRC[SRC.index("bool initiate("):SRC.index("void input_pressed(")]
        # checked before the grab (no resize starts) and returns
        self.assertLess(init.index("top_edge_double_click("), init.index("activate_plugin(&grab_interface)"))
        act = SRC[SRC.index("void title_bar_double_click("):SRC.index("void input_motion()")]
        self.assertIn("tile_request(v, zoomed ? 0 : wf::TILED_EDGES_ALL)", act)     # Zoom / back
        self.assertIn('what == "minimize"', act)
        self.assertIn('what != "none"', act)
        xml = (ROOT / "wayfire-plugin" / "metadata" / "sonata-resize.xml").read_text()
        self.assertRegex(xml, r'name="double_click" type="string">[\s\S]*?<default>toggle-maximize</default>')

    def test_a_click_never_resizes(self):
        motion = SRC[SRC.index("void input_motion()"):]
        dead = motion.index("DEAD_ZONE")
        self.assertLess(dead, motion.index("desired.width += dx"))
        self.assertIn("moved = false;", SRC[SRC.index("grab_start = get_input_coords();"):][:80])
        self.assertTrue(re.search(r"static constexpr int DEAD_ZONE = [2-5];", SRC))


class SettingsTest(unittest.TestCase):
    def test_choice_goes_to_gtk_and_the_top_edge(self):
        from sonata2.settings import app as S
        win = S.Settings.__new__(S.Settings)
        with mock.patch.object(S.system, "set_gsetting") as gs, mock.patch.object(S.system, "run_async") as ra:
            win._set_double_click("minimize")
        gs.assert_called_once_with("org.gnome.desktop.wm.preferences", "action-double-click-titlebar", "minimize")
        self.assertEqual(ra.call_args[0][1:], (None, "sonata-resize", "double_click", "minimize"))
        self.assertIs(ra.call_args[0][0], S.system.wayfire_set)


if __name__ == "__main__":
    unittest.main()
