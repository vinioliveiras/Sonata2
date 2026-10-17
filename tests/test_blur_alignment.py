"""Vini: some apps (Terminal) flickered, and a faint square showed around
the pointer over them. Wayfire's blur re-blurs only the damaged part (the
pointer's, a blinking caret's); dual Kawase halves the picture once per
iteration, and a part aligned only to `degrade` was halved on another grid
than the whole screen -- blurred slightly differently. Sonata's Wayfire
aligns it to degrade x 2^iterations (wayfire-kawase-align.patch).

    python3 -m unittest tests.test_blur_alignment
"""
import configparser
import os
import random
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def degrade_and_halve(row, start, length, degrade, iterations):
    """A row's pixels [start, start+length) shrunk by `degrade` (every
    degrade-th pixel: GL_NEAREST), then halved `iterations` times (2x2 means:
    the Kawase downsample's footprint, in 1-D) -- what's left, by position on
    the screen (the left edge of each final pixel)."""
    small = [row[start + i] for i in range(0, length, degrade)]
    step = degrade
    for _ in range(iterations):
        small = [(small[i] + small[i + 1]) / 2 for i in range(0, len(small) - 1, 2)]
        step *= 2
    return {start + i * step: v for i, v in enumerate(small)}


class AlignmentTest(unittest.TestCase):
    def setUp(self):
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.read(os.path.join(ROOT, "config", "wayfire.ini"))
        self.degrade = cfg.getint("blur", "kawase_degrade")
        self.iterations = cfg.getint("blur", "kawase_iterations")
        rnd = random.Random(7)
        self.row = [rnd.random() for _ in range(1024)]
        self.whole = degrade_and_halve(self.row, 0, 1024, self.degrade, self.iterations)

    def differs(self, start, length=256):
        part = degrade_and_halve(self.row, start, length, self.degrade, self.iterations)
        return any(abs(v - self.whole[x]) > 1e-12 if x in self.whole else True for x, v in part.items())

    def test_aligned_only_to_degrade_differs(self):
        """What Wayfire did: the part blurred on another grid (the square)."""
        self.assertTrue(self.differs(self.degrade))                      # 4: a multiple of degrade

    def test_aligned_to_degrade_times_two_per_iteration_matches(self):
        align = self.degrade * (1 << self.iterations)
        for start in (0, align, 7 * align):
            self.assertFalse(self.differs(start), start)

    def test_patch(self):
        patch = open(os.path.join(ROOT, "wayfire-plugin", "wayfire-kawase-align.patch")).read()
        self.assertIn("subbox = sanitize(subbox, region_alignment(), source_box);", patch)
        self.assertIn("return degrade_opt * (1 << std::max(0, (int)iterations_opt));", patch)
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.read(os.path.join(ROOT, "config", "wayfire.ini"))
        self.assertEqual(cfg.get("blur", "method"), "kawase")               # (the one aligned)


if __name__ == "__main__":
    unittest.main()
