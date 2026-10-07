"""Vini: "o bug das janelas ainda persiste" -- Claude (Electron) drew
itself at an old, bigger width ~2 s after a resize: the frame 1187 px wide,
the app's picture 1811 px over the desktop beside it (frame checks: surface
1811x665 in a 1187x704 window, the app's own window size unchanged).
sonata-resize watches each window it frames: an app drawing more than
OVERSIZE_PX past its window for OVERSIZE_WAIT_MS is asked again for its
size (a configure 1 px wider, then back), at most NUDGES times in
NUDGE_WINDOW_MS."""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "wayfire-plugin" / "src" / "sonata-resize.cpp").read_text()


def const(name):
    return int(re.search(rf"constexpr int {name} = (\d+);", SRC).group(1))


def oversize(surface, geometry, margins, fullscreen=False, resizing=False):
    """sonata-resize's oversize(), in Python (the same rules)."""
    if fullscreen or resizing or sum(margins) == 0:
        return 0
    l, r, t, b = margins
    cw, ch = geometry[0] - l - r, geometry[1] - t - b
    over = max(surface[0] - cw, surface[1] - ch)
    return over if over > const("OVERSIZE_PX") else 0


class OversizeTest(unittest.TestCase):
    def test_vinis_log(self):
        m = (6, 6, 33, 6)                                       # Sonata's frame: 12 wide, 39 tall
        self.assertEqual(oversize((1175, 665), (1187, 704), m), 0)          # as it should be
        self.assertEqual(oversize((1811, 665), (1187, 704), m), 636)        # the bug
        self.assertEqual(oversize((1539, 790), (1143, 829), m), 408)
        self.assertEqual(oversize((1811, 665), (1187, 704), (0, 0, 0, 0)), 0)  # its own frame
        self.assertEqual(oversize((1811, 665), (1187, 704), m, fullscreen=True), 0)
        self.assertEqual(oversize((1811, 665), (1187, 704), m, resizing=True), 0)

    def test_rules_in_the_plugin(self):
        part = SRC[SRC.index("static int oversize("):SRC.index("struct size_watch_t")]
        self.assertIn("pending_fullscreen()", part)
        self.assertIn("m.left + m.right + m.top + m.bottom == 0", part)
        self.assertIn("is_object_pending(v->toplevel())", part)
        self.assertIn("is_object_committed(v->toplevel())", part)
        self.assertIn("shrink_geometry_by_margins", SRC)

    def test_asked_again_not_forever(self):
        part = SRC[SRC.index("struct size_watch_t"):SRC.index("static void remove_size_watch")]
        self.assertIn("wait.set_timeout(OVERSIZE_WAIT_MS", part)            # not a frame in passing
        self.assertIn("(int)nudged.size() >= NUDGES", part)
        self.assertIn("geometry.width = pg.width + 1", part)
        self.assertIn("geometry.width = pg.width;", part)                    # and back
        self.assertGreaterEqual(const("NUDGE_WINDOW_MS"), 10000)
        self.assertLessEqual(const("NUDGES"), 3)

    def test_listeners_never_outlive_the_surface(self):
        part = SRC[SRC.index("static void add_size_watch"):SRC.index("static void remove_size_watch")]
        self.assertIn("connect(&surface->events.destroy)", part)
        self.assertIn("raw->on_commit.disconnect();", part)
        self.assertIn("add_size_watch(toplevel_cast(ev->view));", SRC)      # each map
        self.assertIn("remove_size_watch(t);", SRC)                         # plugin unloaded

    def test_never_while_resizing(self):
        """Vini: "I get stuck, I can't resize the window" -- Claude draws at
        the size it's dragged to; asked for its old size mid-drag, it snapped
        back. Nothing while any window is resized, nor just after."""
        part = SRC[SRC.index("static int oversize("):SRC.index("struct size_watch_t")]
        self.assertIn("g_resizing || (wf::get_current_time() - g_resize_ended < AFTER_RESIZE_MS)", part)
        init = SRC[SRC.index("bool initiate("):SRC.index("void uncount_resize()")]
        self.assertIn("g_resizing++", init)
        unc = SRC[SRC.index("void uncount_resize()"):SRC.index("void input_pressed(")]
        self.assertIn("g_resize_ended = wf::get_current_time()", unc)
        rel = SRC[SRC.index("void input_pressed("):SRC.index("// Convert resize edges to gravity")]
        self.assertIn("uncount_resize();", rel)
        self.assertGreaterEqual(const("AFTER_RESIZE_MS"), 1000)

    def test_frame_check_logs_the_apps_size(self):
        self.assertIn('" app geometry ", xg.width', SRC)


if __name__ == "__main__":
    unittest.main()
