"""Vini: resizing a window, the pointer had to be just outside it -- "on its
shadow", never on the window itself (pixdecor's and GTK's handles are
outside the frame). Now a band EDGE_IN px inside every window's visible
edge resizes it too (sonata-resize: edge_grab_node_t)."""
import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

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
    // a 400 x 300 window at (100, 50)
    CHECK(band_edges(100, 50, 400, 300, 101, 200) == WLR_EDGE_LEFT);          // on its left edge
    CHECK(band_edges(100, 50, 400, 300, 498, 200) == WLR_EDGE_RIGHT);
    CHECK(band_edges(100, 50, 400, 300, 300, 51) == WLR_EDGE_TOP);
    CHECK(band_edges(100, 50, 400, 300, 300, 348) == WLR_EDGE_BOTTOM);
    CHECK(band_edges(100, 50, 400, 300, 300, 200) == 0);                      // inside: the app's
    CHECK(band_edges(100, 50, 400, 300, 105, 200) == 0);                      // past the band
    CHECK(band_edges(100, 50, 400, 300, 99, 200) == 0);                       // outside: the frame's own
    CHECK(band_edges(100, 50, 400, 300, 102, 52) == (WLR_EDGE_LEFT | WLR_EDGE_TOP));
    CHECK(band_edges(100, 50, 400, 300, 110, 349) == (WLR_EDGE_LEFT | WLR_EDGE_BOTTOM));   // near a corner
    CHECK(band_edges(100, 50, 400, 300, 499, 340) == (WLR_EDGE_RIGHT | WLR_EDGE_BOTTOM));
    CHECK(band_edges(0, 0, 6, 6, 1, 1) == 0);                                  // too small
    return fails;
}
"""


class EdgeGrabTest(unittest.TestCase):
    def test_band(self):
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

    def test_every_window_gets_it_and_it_starts_the_resize(self):
        node = SRC[SRC.index("class edge_grab_node_t"):SRC.index("static constexpr int ZOOM_MS")]
        self.assertIn("auto g = visible(view, view->get_geometry());", node)    # the window as seen
        self.assertIn("view->pending_tiled_edges()", node)                       # not maximized / tiled
        self.assertIn("wf::get_core().default_wm->resize_request(view, hovered);", node)
        self.assertIn("wlr_xcursor_get_resize_name", node)                       # the resize pointer
        self.assertIn("wf::scene::add_front(v->get_root_node(), data->node);", node)   # over the window
        self.assertIn("output->connect(&on_view_mapped);", SRC)
        fini = SRC[SRC.index("void fini() override"):SRC.index("void start_zoom(")]
        self.assertIn("remove_edge_grab(t);", fini)


if __name__ == "__main__":
    unittest.main()
