"""Vini: with Control Center open, the pointer stayed a resize arrow over
the windows and the Dock. While a pop-up holds the pointer, no app under
it sets its own cursor -- so sonata-resize's edge band puts back the
normal pointer itself when the pointer leaves the edge."""
import pathlib
import unittest

SRC = (pathlib.Path(__file__).resolve().parent.parent / "wayfire-plugin" / "src" / "sonata-resize.cpp").read_text()


class EdgeCursorTest(unittest.TestCase):
    def part(self):
        return SRC[SRC.index("class edge_grab_node_t"):SRC.index('static const char *EDGE_DATA')]

    def test_leaving_the_edge_puts_the_pointer_back(self):
        part = self.part()
        leave = part[part.index("void handle_pointer_leave()"):]
        leave = leave[:leave.index("\n    }\n")]
        self.assertIn('set_cursor("default")', leave)
        self.assertIn("if (hovered)", leave)                 # only the arrow we set

    def test_moving_off_the_band_inside_the_window_too(self):
        show = self.part()[self.part().index("void show("):self.part().index("void handle_pointer_enter")]
        self.assertIn('else if (!e && hovered)', show)
        self.assertIn('set_cursor("default")', show)


    def test_no_edge_over_the_dock_or_menu_bar(self):
        """Vini: the arrow still showed over the Dock -- a window's bottom
        edge passing under it. A shell surface above the windows wins."""
        fn = SRC[SRC.index("static bool under_shell"):SRC.index("class edge_grab_node_t")]
        self.assertIn("VIEW_ROLE_DESKTOP_ENVIRONMENT", fn)
        self.assertIn("wf::scene::layer::TOP", fn)
        self.assertIn("get_bounding_box()", fn)
        edges = self.part()[self.part().index("uint32_t edges_at"):self.part().index("find_node_at")]
        self.assertIn("under_shell(at)", edges)


if __name__ == "__main__":
    unittest.main()
