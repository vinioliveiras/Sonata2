"""Vini: pop-up buttons' lists, the Dock's folder stacks and the hover labels
were solid; they're the menus' glass (panel_material) now."""
import inspect
import re
import unittest

from sonata2.shell import dock_stack
from sonata2.ui import controls, label


def _rule(src, selector):
    i = src.index(selector + " {")
    return src[i:src.index("}", i)]


class PopoverGlassTest(unittest.TestCase):
    def test_glass(self):
        for mod, sel in ((controls, "dropdown.sonata-popup popover > contents"),
                         (dock_stack, "popover.stack-panel > contents"),
                         (label, "popover.hover-label > contents")):
            rule = _rule(inspect.getsource(mod), sel)
            self.assertIn("%(panel_material)s", rule, sel)
            self.assertIsNone(re.search(r"background[^;]*%\(menu_bg\)s", rule), sel)


if __name__ == "__main__":
    unittest.main()
