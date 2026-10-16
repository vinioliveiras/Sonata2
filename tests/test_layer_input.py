"""layer.whole_input: a surface takes input everywhere again, whatever the
PyGObject version (some refuse None: "Argument 1 does not allow None" --
the menu bar's warm-up stopped there in a real session)."""
import unittest
from unittest import mock

from sonata2.shell import layer


class WholeInputTest(unittest.TestCase):
    def test_none_accepted(self):
        s = mock.Mock()
        layer.whole_input(s)
        s.set_input_region.assert_called_once_with(None)

    def test_none_refused(self):
        s = mock.Mock()
        s.set_input_region.side_effect = [TypeError("Argument 1 does not allow None as a value"), None]
        layer.whole_input(s)
        region = s.set_input_region.call_args[0][0]
        import cairo
        self.assertIsInstance(region, cairo.Region)
        self.assertTrue(region.contains_point(3000, 2000))


if __name__ == "__main__":
    unittest.main()
