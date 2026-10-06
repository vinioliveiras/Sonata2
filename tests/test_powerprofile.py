"""Vini: unplugged, the laptop went to Low Power (right); plugged in again,
the energy mode must go back to the one he chose (Automatic)."""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

from sonata2 import config, powerprofile as P  # noqa: E402


class FollowTest(unittest.TestCase):
    def setUp(self):
        config.save(P.NAME, {})
        self.ac, self.cur, self.set, self.boost = [True], ["balanced"], [], [False]

    def follow(self):
        def apply(p):
            self.set.append(p)
            self.cur[0] = p
            return True
        return P.Follow(on_ac=lambda: self.ac[0], current=lambda: self.cur[0], apply=apply,
                        boosted=lambda: self.boost[0])

    def test_back_to_automatic_when_plugged_in(self):
        f = self.follow()
        self.ac[0], self.cur[0] = False, "power-saver"          # unplugged: something lowered it
        self.assertFalse(f.changed())                            # (left as it is on battery)
        self.ac[0] = True
        self.assertTrue(f.changed())
        self.assertEqual(self.set, ["balanced"])
        self.assertFalse(f.changed())                            # still plugged in: nothing again

    def test_the_mode_you_chose(self):
        P.remember("performance")
        f = self.follow()
        self.ac[0], self.cur[0] = False, "power-saver"
        f.changed()
        self.ac[0] = True
        f.changed()
        self.assertEqual(self.set, ["performance"])
        P.remember("nonsense")                                   # unknown: kept as before
        self.assertEqual(P.chosen(), "performance")

    def test_a_game_boost_is_left_alone(self):
        f = self.follow()
        self.ac[0] = False
        f.changed()
        self.ac[0], self.cur[0], self.boost[0] = True, "performance", True
        self.assertFalse(f.changed())
        self.assertEqual(self.set, [])

    def test_wired(self):
        import inspect
        from sonata2.settings import app
        from sonata2.shell import topbar
        self.assertIn('_shared("powerfollow", powerprofile.Follow)', inspect.getsource(topbar._power_changed))
        self.assertIn("powerprofile.remember(key)", inspect.getsource(topbar))
        self.assertIn("powerprofile.remember(p)", inspect.getsource(app))


if __name__ == "__main__":
    unittest.main()
