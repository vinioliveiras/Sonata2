"""Vini: clicks lost in a Proton game. Holding quick releases back (taps,
clicks read in a burst) didn't help the game and made every click feel
late ("o mouse ta com muito delay no clique") -- taken out again: Sonata
never holds or replays a pointer button. What stays: [sonata-corners]
debug_input logs each button, the device, the window and node under the
pointer and the keyboard focus."""
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = (ROOT / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()


class ClicksTest(unittest.TestCase):
    def test_buttons_never_held(self):
        self.assertNotIn("input_event_processing_mode_t::IGNORE", SRC)   # nothing held back
        self.assertNotIn("events.button", SRC)                            # nothing replayed
        self.assertNotIn("MIN_CLICK_MS", SRC)

    def test_debug_log(self):
        part = SRC[SRC.index("struct input_debug_t"):SRC.index("class sonata_corners_t")]
        self.assertIn('option_str("sonata-corners/debug_input") != "true"', part)
        self.assertIn("ev->pointer->base.name", part)
        self.assertIn("post_input_event_signal<wlr_pointer_button_event>", part)
        self.assertIn("keyboard_focus_changed_signal", part)
        self.assertIn("node->stringify()", part)
        self.assertIn("input_debug.init();", SRC)
        self.assertIn("input_debug.fini();", SRC)
        meta = (ROOT / "wayfire-plugin" / "metadata" / "sonata-corners.xml").read_text()
        self.assertIn('<option name="debug_input" type="bool">', meta)


if __name__ == "__main__":
    unittest.main()
