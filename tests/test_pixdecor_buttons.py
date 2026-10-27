"""Window buttons on title bars pixdecor draws (other apps' windows): the
same bigger targets as Sonata's own (Vini: the dots were hard to hit) --
wayfire-plugin/pixdecor-buttons.patch. Its set_button_hits() is taken out of
the patch and run here against a stand-in of pixdecor's layout: half the gap
to each neighbour (no dead space), the whole title bar height, out to the
window's side edge (the screen's corner when maximized).
Run: python3 -m unittest tests.test_pixdecor_buttons"""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = os.path.join(REPO, "wayfire-plugin", "pixdecor-buttons.patch")

HEADER = r"""
#include <algorithm>
#include <cmath>
#include <vector>
#include <memory>
#include <cstdio>
#include <cassert>
namespace wf { struct geometry_t { int x, y, width, height; }; struct point_t { int x, y; }; }
bool operator&(const wf::geometry_t& g, const wf::point_t& p){ return p.x>=g.x && p.y>=g.y && p.x<g.x+g.width && p.y<g.y+g.height; }
enum decoration_area_type_t { DECORATION_AREA_BUTTON = 1, DECORATION_AREA_TITLE = 2 };
struct decoration_area_t {
  decoration_area_type_t type; wf::geometry_t geometry; wf::geometry_t hit={0,0,0,0};
  decoration_area_type_t get_type() const { return type; }
  wf::geometry_t get_geometry() const { return geometry; }
  wf::geometry_t get_hit() const { return (hit.width>0&&hit.height>0)?hit:geometry; }
  void set_hit(wf::geometry_t g){ hit=g; }
};
struct pixdecor_layout_t {
  std::vector<std::unique_ptr<decoration_area_t>> layout_areas;
  void set_button_hits(size_t first, double edge_left, double edge_right, double top, double height);
  decoration_area_t* at(int x,int y){ for(auto&a:layout_areas) if(a->get_type()==DECORATION_AREA_BUTTON && (a->get_hit() & wf::point_t{x,y})) return a.get(); return nullptr; }
};
"""
MAIN = r"""
int main(){
  // left: three 12px buttons at x=13,33,53 (gap 8), y=8, title 0..28, window edge at 1
  pixdecor_layout_t L;
  for (int x: {13,33,53}) L.layout_areas.push_back(std::make_unique<decoration_area_t>(decoration_area_t{DECORATION_AREA_BUTTON,{x,8,12,12}}));
  L.set_button_hits(0, 1, -1, 0, 28);
  auto *c=L.layout_areas[0].get(), *m=L.layout_areas[1].get(), *z=L.layout_areas[2].get();
  assert(L.at(1,0)==c);          // window corner
  assert(L.at(28,14)==c && L.at(29,14)==m);   // no dead gap: midpoint 29
  assert(L.at(33,27)==m && L.at(33,28)==nullptr);
  assert(L.at(68,14)==z && L.at(69,14)==nullptr);  // half gap past the last, toward the title
  assert(L.at(0,5)==nullptr);    // the frame edge stays resize/other
  // right side, maximized: buttons at 1000-..., edge 1024
  pixdecor_layout_t R;
  for (int x: {1012,992,972}) R.layout_areas.push_back(std::make_unique<decoration_area_t>(decoration_area_t{DECORATION_AREA_BUTTON,{x,8,12,12}}));
  R.set_button_hits(0, -1, 1024, 0, 28);
  assert(R.at(1023,0)->get_geometry().x==1012);  // screen corner
  assert(R.at(968,10)->get_geometry().x==972 && R.at(967,10)==nullptr);
  puts("ok");
}
"""


def added_function(patch: str, name: str) -> str:
    """The lines a patch adds for one function (its body is all new)."""
    added = [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    text = "\n".join(added)
    start = text.index(f"void pixdecor_layout_t::{name}")
    depth, i = 0, text.index("{", start)
    for j in range(i, len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[start:j + 1]
    raise ValueError(name)


class PixdecorButtonsTest(unittest.TestCase):
    def test_build_applies_the_patch(self):
        with open(os.path.join(REPO, "tools", "build-pixdecor.sh")) as f:
            self.assertIn("pixdecor-buttons.patch", f.read())

    def test_buttons_checked_after_resize_handles(self):
        """The frame's edge still resizes: buttons are skipped in the first pass."""
        with open(PATCH) as f:
            patch = f.read()
        self.assertRegex(patch, re.compile(r"\+.*DECORATION_AREA_MOVE_BIT\).*DECORATION_AREA_BUTTON"))
        self.assertIn("+    /* Sonata: a button anywhere in the room around its picture", patch)

    @unittest.skipUnless(shutil.which("g++"), "needs g++")
    def test_hit_areas(self):
        with open(PATCH) as f:
            fn = added_function(f.read(), "set_button_hits")
        with tempfile.TemporaryDirectory() as d:
            src, exe = os.path.join(d, "t.cpp"), os.path.join(d, "t")
            with open(src, "w") as f:
                f.write(HEADER + fn + MAIN)
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Werror", "-o", exe, src], check=True)
            out = subprocess.run([exe], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
