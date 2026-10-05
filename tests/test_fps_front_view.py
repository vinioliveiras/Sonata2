"""Vini: Control Center's frame-time graph said "No app drawing in front"
with a game open. Control Center (a panel) has the keyboard while it's open,
so the plugin watched the panel, not the game. It now falls back to the
window focused last. (Wayfire can't run here: the source is checked.)"""
import os
import re
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "wayfire-plugin", "src", "sonata-corners.cpp")


class FrontViewTest(unittest.TestCase):
    def test_ask_watches_the_window_in_front_not_the_panel(self):
        src = open(SRC).read()
        ask = src[src.index("wf::json_t ask("):]
        ask = ask[:ask.index("return response;")]
        self.assertIn("front_view()", ask)
        self.assertNotIn("seat->get_active_view()", ask)
        front = src[src.index("static wayfire_view front_view()"):]
        front = front[:front.index("wf::json_t ask(")]
        self.assertTrue(re.search(r"toplevel_cast\(active\)", front))             # a window: itself
        self.assertIn("last_focus_timestamp", front)                              # a panel: the last window
        self.assertIn("minimized", front)


if __name__ == "__main__":
    unittest.main()
