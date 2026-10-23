"""Vini: with the window buttons on the right, the close button of WhatsApp
and VS Code (title bars pixdecor draws) was cut by the window's corner, and
not as far from the edge as on the left. pixdecor adds x_offset to the
buttons' x on both sides -- inwards on the left, outwards on the right."""
import unittest

from sonata2 import wfconfig
from sonata2.ui import tokens


def pixdecor_run(side: str, width: int, opts: dict, frame: dict, shadow: int = 3, border: int = 0) -> list:
    """The buttons' x as pixdecor 5a6f590 lays them out (deco-layout.cpp,
    create_left_buttons / create_right_buttons, called from resize with the
    shadow's radius r and width - 2r on the right)."""
    names = list(frame["buttons"])
    dot, gap = frame["dot"], frame["dot_gap"]
    r = shadow
    off = float(opts.get(("pixdecor", f"{side}_button_x_offset"), 0))
    xs = []
    if side == "left":
        x = r * 2 + border + off
        for i, _n in enumerate(names):
            xs.append(x)
            x += dot + (0 if i == len(names) - 1 else gap)
    else:
        x = (width - 2 * r - border) + off
        for i, _n in enumerate(reversed(names)):
            x -= dot + (0 if i == 0 else gap)
            xs.append(x)
    return xs


class RightButtonsTest(unittest.TestCase):
    def test_right_buttons_mirror_the_left(self):
        width, r = 800, 3
        left_f = dict(tokens.FRAME, buttons_side="left")
        right_f = dict(tokens.FRAME, buttons_side="right")
        lo = {(s, k): v for s, k, v in wfconfig.frame_options(left_f)}
        ro = {(s, k): v for s, k, v in wfconfig.frame_options(right_f)}
        left = pixdecor_run("left", width, lo, left_f, r)
        right = pixdecor_run("right", width, ro, right_f, r)
        dot = tokens.FRAME["dot"]
        frame_l, frame_r = 2 * r, width - 2 * r            # the window inside its shadow
        first_in_left = left[0] - frame_l                   # the outermost dot's distance from its edge
        first_in_right = frame_r - (right[0] + dot)
        self.assertEqual(first_in_right, first_in_left)
        self.assertEqual(first_in_left, tokens.FRAME["dot_left"] - dot / 2)
        self.assertLessEqual(right[0] + dot, frame_r)       # inside the window: never cut by its corner

    def test_right_offset_is_negative(self):
        F = dict(tokens.FRAME, buttons_side="right")
        opts = {(s, k): v for s, k, v in wfconfig.frame_options(F)}
        self.assertLess(float(opts[("pixdecor", "right_button_x_offset")]), 0)


if __name__ == "__main__":
    unittest.main()


class LoginConfigTest(unittest.TestCase):
    """At login the buttons' options come from the side, never from a value
    Settings saved before (the old +7 on the right kept cutting the button)."""
    def test_saved_offset_does_not_win(self):
        import os
        import subprocess
        import tempfile
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "sonata2"))
        with open(os.path.join(d, "sonata2", "appearance.json"), "w") as f:
            f.write('{"buttons_side": "right"}')
        with open(os.path.join(d, "sonata2", "wayfire-overrides.ini"), "w") as f:
            f.write("[pixdecor]\nright_button_x_offset = 7\n[core]\nxwayland = true\n")
        out = os.path.join(d, "out.ini")
        subprocess.run(["bash", os.path.join(root, "tools", "wayfire-config.sh"),
                        os.path.join(root, "config", "wayfire.ini"), out],
                       env={**os.environ, "XDG_CONFIG_HOME": d, "HOME": d}, capture_output=True, check=True)
        text = open(out).read()
        inset = tokens.FRAME["dot_left"] - tokens.FRAME["dot"] / 2
        self.assertIn(f"right_button_x_offset = -{inset:g}", text)
        self.assertNotIn("right_button_x_offset = 7\n", text)
        self.assertIn("xwayland = true", text)                      # other saved settings still apply
