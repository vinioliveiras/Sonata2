"""Vini: out of full screen (a video in Chrome), the maximized window
sometimes kept the whole display, under the Dock. sonata-resize puts
maximized windows back inside the work area after full screen ends and
whenever the work area changes."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "wayfire-plugin" / "src" / "sonata-resize.cpp").read_text()

HARNESS = r"""
#include <algorithm>
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
    // work area: under a 32 px menu bar, above a 70 px Dock on 1920 x 1080
    CHECK(past_workarea(0, 32, 1920, 1048, 0, 32, 1920, 978));     // the whole display: under the Dock
    CHECK(!past_workarea(0, 32, 1920, 978, 0, 32, 1920, 978));     // just the work area
    CHECK(!past_workarea(8, 40, 1900, 960, 0, 32, 1920, 978));     // inside it
    CHECK(past_workarea(0, 0, 1920, 1010, 0, 32, 1920, 978));      // under the menu bar
    // Vini: restored, a window bigger than the work area: it fits, moved inside
    int x = -40, y = 10, w = 2100, h = 1100;
    fit_inside(x, y, w, h, 0, 32, 1920, 978);
    CHECK(x == 0 && y == 32 && w == 1920 && h == 978);
    x = 1500; y = 600; w = 800; h = 600;                           // off the right and bottom
    fit_inside(x, y, w, h, 0, 32, 1920, 978);
    CHECK(x == 1120 && y == 410 && w == 800 && h == 600);
    x = 100; y = 100; w = 800; h = 600;                            // inside: untouched
    fit_inside(x, y, w, h, 0, 32, 1920, 978);
    CHECK(x == 100 && y == 100 && w == 800 && h == 600);
    double dx = 0, dy = 0, dw = 1920, dh = 1080;                   // the Dock hidden: the whole display
    fit_inside(dx, dy, dw, dh, 0, 0, 1920, 1080);
    CHECK(dw == 1920 && dh == 1080);
    return fails;
}
"""


class RefitTest(unittest.TestCase):
    def test_restore_fits(self):
        tile = SRC[SRC.index("on_tile_request ="):SRC.index("void fit_restored_soon(")]
        self.assertIn("if ((ev->edges == 0) && ev->view && (ev->desired_size.width > 0))", tile)
        self.assertIn("output->workarea->get_workarea()", tile)            # Dock and menu bar left out
        self.assertIn("fit_restored_soon(ev->view);", tile)
        later = SRC[SRC.index("void fit_restored_soon("):SRC.index("bool moved = false;")]
        self.assertIn("past_workarea(", later)
        self.assertIn("tx_manager->schedule_object", later)
        self.assertIn("fit_timer.disconnect();", SRC[SRC.index("void fini() override"):])

    def test_past_workarea(self):
        cxx = shutil.which("g++") or shutil.which("c++")
        if not cxx:
            self.skipTest("no C++ compiler")
        start, end = SRC.index("static constexpr int DEAD_ZONE"), SRC.index("class wayfire_resize")
        d = tempfile.mkdtemp()
        src, exe = os.path.join(d, "t.cpp"), os.path.join(d, "t")
        with open(src, "w") as f:
            f.write(HARNESS % SRC[start:end])
        subprocess.run([cxx, "-std=c++17", "-o", exe, src], check=True)
        r = subprocess.run([exe], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_after_full_screen_and_work_area_changes(self):
        self.assertIn("output->connect(&on_fullscreen);", SRC)
        self.assertIn("output->connect(&on_workarea);", SRC)
        fs = SRC[SRC.index("on_fullscreen = "):SRC.index("on_workarea = ")]
        self.assertIn("if (!ev->state)", fs)                                 # leaving full screen
        refit = SRC[SRC.index("void refit_maximized()"):SRC.index("on_view_mapped =")]
        self.assertIn("v->pending_tiled_edges() != wf::TILED_EDGES_ALL", refit)   # only maximized ones
        self.assertIn("v->toplevel()->pending().geometry = wa;", refit)
        fini = SRC[SRC.index("void fini() override"):SRC.index("void start_zoom(")]
        self.assertIn("refit_timer.disconnect();", fini)


if __name__ == "__main__":
    unittest.main()
