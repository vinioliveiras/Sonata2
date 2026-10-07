"""What FPS measures (Control Center's graph, the menu bar's FPS).
Vini: first it said "No app drawing in front" with a game open (Control
Center has the keyboard while open); then it measured Claude -- "only
what's full screen? can it find games?". It now measures a full-screen
window or a game (Steam's steam_app_<id>, a Windows program's .exe,
gamescope), the one focused last (not whatever has the keyboard), and
nothing otherwise. (Wayfire can't run here: the source is checked.)"""
import os
import unittest

SRC = os.path.join(os.path.dirname(__file__), "..", "wayfire-plugin", "src", "sonata-corners.cpp")


class MeasuredViewTest(unittest.TestCase):
    def setUp(self):
        self.src = open(SRC).read()

    def test_ask_measures_games_and_full_screen_only(self):
        ask = self.src[self.src.index("wf::json_t ask("):]
        ask = ask[:ask.index("return response;")]
        self.assertIn("measured_view()", ask)
        self.assertNotIn("seat->get_active_view()", ask)            # not whatever has the keyboard
        part = self.src[self.src.index("static wayfire_view measured_view()"):self.src.index("wf::json_t ask(")]
        self.assertIn("pending_fullscreen() || game_like(v)", part)
        self.assertIn("last_focus_timestamp", part)                  # several: the one focused last
        self.assertIn("minimized", part)

    def test_what_counts_as_a_game(self):
        part = self.src[self.src.index("static bool game_like"):self.src.index("static wayfire_view measured_view")]
        for needle in ('"steam_app_"', '"gamescope"', '".exe"', "tolower"):
            self.assertIn(needle, part)


if __name__ == "__main__":
    unittest.main()
