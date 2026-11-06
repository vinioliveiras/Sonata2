"""A Game Bar widget's border round its modules as wide as the gaps between
them, as macOS's Control Center (Vini, with a photo of it: 16 px outside
and between there; a widget had 10 px round its modules and 6 between)."""
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _const(src: str, name: str) -> int:
    return int(re.search(rf"^{name} = (\d+)", src, re.M).group(1))


class GameBarBorderTest(unittest.TestCase):
    def test_widget_border_wider_than_its_gaps(self):
        """A widget's modules sat 10 px from its edges and 6 px apart."""
        spec = importlib.util.spec_from_file_location("export_tokens", os.path.join(ROOT, "tools", "export_tokens.py"))
        ex = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ex)
        src = _read("sonata2", "shell", "gamebar.py")
        gap, pad, body = (_const(src, n) for n in ("GAP", "WIDGET_PAD", "BODY_PAD"))
        css = next(b for b in ex._registered(src, "shell/gamebar.py") if b["key"] == "gamebar")
        widget = re.search(r"\.gb-widget \{[^}]*padding: %\((\w+)\)spx;", css["css"])
        inner = re.search(r"\.gb-widget-body \{ padding: \d+px %\((\w+)\)spx %\((\w+)\)spx; \}", css["css"])
        self.assertIsNotNone(widget)
        self.assertIsNotNone(inner)
        self.assertEqual(int(css["local"][widget.group(1)]), pad)
        self.assertEqual(int(css["local"][inner.group(1)]), body)   # the sides
        self.assertEqual(int(css["local"][inner.group(2)]), body)   # the bottom
        self.assertEqual(pad + body, gap)                           # the border round the modules
        # Control Center's own gap
        self.assertEqual(gap, _const(_read("sonata2", "shell", "controlcenter.py"), "SPACING"))
        # between the modules: GAP, no 6 px left
        for spacing in ("spacing=6", "column_spacing=6", "row_spacing=6"):
            for fn in ("_controls", "_performance"):
                body_src = src.split(f"def {fn}(", 1)[1].split("\n    def ", 1)[0]
                self.assertNotIn(spacing, body_src, fn)
        self.assertIn("WIDGET_INNER = WIDGET_W - 2 * WIDGET_PAD - 2 * BODY_PAD", src)


if __name__ == "__main__":
    unittest.main()
