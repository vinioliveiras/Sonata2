"""Vini: in a screenshot's Markup, a button to copy the picture to the
clipboard again -- with the marks drawn so far, Markup staying open."""
import io
import unittest

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402,F401
from PIL import Image  # noqa: E402

from sonata2.preview import markup, window  # noqa: E402


class FakeEdits:
    def __init__(self):
        self.full = Image.new("RGB", (40, 30), "white")

    def render(self, im):
        return im.rotate(0)


class MarkupCopyTest(unittest.TestCase):
    def test_png_with_the_marks(self):
        sent = []
        items = [{"t": "pen", "p": [(0.1, 0.5), (0.5, 0.5), (0.9, 0.5)], "c": "#ff3b30", "w": 0.05}]
        self.assertTrue(window.copy_picture(FakeEdits(), items, run=lambda data: sent.append(data) or True))
        im = Image.open(io.BytesIO(sent[0]))
        self.assertEqual(im.size, (40, 30))                  # full size
        self.assertEqual(im.format, "PNG")
        self.assertTrue(any(p != (255, 255, 255) for p in im.convert("RGB").getdata()))   # the mark is in it

    def test_failure_is_said(self):
        self.assertFalse(window.copy_picture(FakeEdits(), [], run=lambda data: False))

    def test_bar_has_copy_only_when_asked(self):
        layer = markup.MarkupLayer(lambda: (0, 0, 40, 30), lambda: None)
        got = []
        bar = markup.MarkupBar(layer, lambda: None, lambda: None, on_copy=lambda cb: got.append(cb))
        self.assertIsNotNone(bar.copy_btn)
        bar.copy_btn.emit("clicked")
        self.assertEqual(got, [bar.copied])
        bar.copied(True)
        self.assertEqual(bar.copy_btn.get_label(), "Copied")
        self.assertIsNone(markup.MarkupBar(layer).copy_btn)    # live drawing: no Copy

    def test_preview_wires_it(self):
        self.assertIn("on_copy=self.copy_marked", open(window.__file__).read())

if __name__ == "__main__":
    unittest.main()
