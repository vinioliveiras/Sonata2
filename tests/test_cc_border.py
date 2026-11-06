"""Control Center's own border as wide as the gaps between its modules, as
macOS's (Vini, with a photo: 6 px outside, 8 px between, looked cramped)."""
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ControlCenterBorderTest(unittest.TestCase):
    def test_control_center_border_narrower_than_its_gaps(self):
        spec = importlib.util.spec_from_file_location("export_tokens", os.path.join(ROOT, "tools", "export_tokens.py"))
        ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ex)
        path = os.path.join(ROOT, "sonata2", "shell", "controlcenter.py")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        spacing = int(re.search(r"^SPACING = (\d+)", src, re.M).group(1))
        blocks = ex._registered(src, "shell/controlcenter.py")
        css = next(b for b in blocks if b["key"] == "controlcenter")
        rule = re.search(r"popover\.sonata-panel\.cc-panel > contents \{ padding: %\((\w+)\)spx; \}", css["css"])
        self.assertIsNotNone(rule)
        self.assertEqual(int(css["local"][rule.group(1)]), spacing)
        with open(os.path.join(ROOT, "sonata2", "shell", "topbar.py"), encoding="utf-8") as f:
            self.assertIn('pop.add_css_class("cc-panel")', f.read())


if __name__ == "__main__":
    unittest.main()
