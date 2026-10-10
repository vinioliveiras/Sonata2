"""The drawing palette is left out of captures from outside its blur (Vini:
its icons flickered on screen while sharing -- the capture pass still ran
the palette's blur, inside which the hiding node sat)."""
import pathlib
import unittest

SRC = (pathlib.Path(__file__).resolve().parent.parent / "wayfire-plugin" / "src" / "sonata-corners.cpp").read_text()


class CaptureHideZTest(unittest.TestCase):
    def test_outside_the_blur(self):
        self.assertIn("static const int CAPTURE_HIDE_Z = wf::TRANSFORMER_BLUR + 100;", SRC)
        self.assertIn("add_transformer(std::make_shared<capture_hide_node_t>(), CAPTURE_HIDE_Z, capture_hide_name)",
                      SRC)
        self.assertNotIn("add_transformer(std::make_shared<capture_hide_node_t>(), 0,", SRC)

    def test_build_bumped(self):
        self.assertIn('#define SONATA_CORNERS_BUILD "2026-10-10.1 ', SRC)


if __name__ == "__main__":
    unittest.main()
