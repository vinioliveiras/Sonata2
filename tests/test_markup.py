"""Vini: draw on a screenshot -- pen, shapes, symbols, text, emoji. Preview's
Markup: marks stay editable until Done, then they're one edit; what's saved
is what was drawn; a screenshot's thumbnail opens straight into it."""
import os
import tempfile
import unittest

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2.preview import markup as M  # noqa: E402


def spin(ms):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class ApplyTest(unittest.TestCase):
    def test_marks_drawn_into_the_picture_at_any_size(self):
        from PIL import Image
        items = [{"t": "rect", "c": "#ff0000", "w": 0.01, "p": [[0.25, 0.25], [0.75, 0.75]]}]
        for size in ((400, 200), (1600, 800)):
            im = M.apply(Image.new("RGB", size, "white"), items)
            self.assertEqual(im.mode, "RGB")
            w, h = size
            r, g, b = im.getpixel((w // 2, int(h * 0.25)))        # on the rectangle's top edge
            self.assertGreater(r, 200)
            self.assertLess(g, 80)
            self.assertEqual(im.getpixel((w // 2, h // 2)), (255, 255, 255))   # inside: untouched

    def test_every_kind_draws(self):
        from PIL import Image
        base = Image.new("RGB", (300, 200), "white")
        kinds = [{"t": s, "c": "#007aff", "w": 0.01, "p": [[0.2, 0.2], [0.8, 0.8]]} for s in M.SHAPES]
        kinds += [{"t": "pen", "c": "#007aff", "w": 0.01, "p": [[0.1, 0.1], [0.5, 0.4], [0.9, 0.2]]},
                  {"t": "hl", "c": "#ffcc00", "w": 0.01, "p": [[0.1, 0.5], [0.9, 0.5]]},
                  {"t": "text", "c": "#ff3b30", "p": [[0.1, 0.1]], "text": "Hi", "s": 0.1},
                  {"t": "emoji", "c": "#000", "p": [[0.4, 0.4]], "text": "🔥", "s": 0.15},
                  {"t": "step", "c": "#ff3b30", "p": [[0.5, 0.5]], "n": 3, "s": 0.1}]
        for it in kinds:
            out = M.apply(base, [it])
            self.assertNotEqual(list(out.getdata()), list(base.getdata()), it["t"])

    def test_pixelate_hides_detail(self):
        from PIL import Image
        im = Image.new("RGB", (200, 100), "white")
        for x in range(0, 200, 2):                                 # fine stripes
            for y in range(100):
                im.putpixel((x, y), (0, 0, 0))
        out = M.apply(im, [{"t": "pixelate", "c": "#000", "p": [[0, 0], [1, 1]]}])
        row = [out.getpixel((x, 50))[0] for x in range(40, 60)]
        self.assertLess(max(row) - min(row), 60)                   # the stripes became flat blocks

    def test_no_line_joins_two_marks(self):
        """A text's pen position must not join the next mark (a white line ran
        from a text to a numbered step)."""
        from PIL import Image
        base = Image.new("RGB", (400, 400), "black")
        items = [{"t": "text", "c": "#ffffff", "p": [[0.05, 0.05]], "text": "A", "s": 0.05},
                 {"t": "step", "c": "#ff3b30", "p": [[0.8, 0.8]], "n": 1, "s": 0.05}]
        out = M.apply(base, items)
        self.assertEqual(out.getpixel((200, 200)), (0, 0, 0))       # nothing in between


class LayerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def layer(self):
        lay = M.MarkupLayer(lambda: (0, 0, 400, 200), lambda: None)
        return lay

    def drag(self, lay, x, y, dx, dy):
        lay._begin(None, x, y)
        lay._update(None, dx / 2, dy / 2)
        lay._update(None, dx, dy)
        lay._end(None, dx, dy)

    def test_draw_select_move_recolour_delete_undo(self):
        lay = self.layer()
        lay.set_tool("pen")
        self.drag(lay, 10, 10, 100, 50)
        self.assertEqual(lay.items[0]["t"], "pen")
        lay.set_tool("shape")
        lay.shape = "arrow"
        self.drag(lay, 200, 100, 80, 40)
        self.assertEqual(lay.items[1]["t"], "arrow")
        self.assertEqual(lay.items[1]["p"][0], [0.5, 0.5])
        lay.set_tool("shape")
        self.drag(lay, 300, 150, 1, 1)                             # a click: no shape
        self.assertEqual(len(lay.items), 2)
        lay.set_tool("select")
        lay._begin(None, 240, 120)                                 # the arrow: selected, moved
        self.assertEqual(lay.sel, 1)
        lay._update(None, 40, 20)
        lay._end(None, 40, 20)
        self.assertAlmostEqual(lay.items[1]["p"][0][0], 0.6)
        lay.set_color("#34c759")
        self.assertEqual(lay.items[1]["c"], "#34c759")
        lay.delete_selected()
        self.assertEqual(len(lay.items), 1)
        lay.undo()
        self.assertEqual(lay.items[1]["c"], "#34c759")
        lay.undo()
        lay.undo()
        self.assertAlmostEqual(lay.items[1]["p"][0][0], 0.5)       # back before the move
        lay.redo()
        self.assertAlmostEqual(lay.items[1]["p"][0][0], 0.6)

    def test_steps_count_up_and_emoji_centre(self):
        lay = self.layer()
        lay.set_tool("step")
        lay._begin(None, 50, 50)
        lay._begin(None, 100, 50)
        self.assertEqual([it["n"] for it in lay.items], [1, 2])
        lay.set_tool("emoji")
        lay._begin(None, 200, 100)
        x0, y0, x1, y1 = M.bounds(lay.items[-1], 400, 200)
        self.assertAlmostEqual((x0 + x1) / 2, 200, delta=2)


class PreviewMarkupTest(unittest.TestCase):
    def test_done_is_one_edit_and_saved(self):
        from PIL import Image
        from sonata2 import ui
        from sonata2.preview.window import PreviewWindow
        Adw.init()
        ui.setup()
        d = tempfile.mkdtemp()
        path = os.path.join(d, "shot.png")
        Image.new("RGB", (300, 200), "white").save(path)
        app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.markuptest")
        app.register(None)
        w = PreviewWindow(app, path, markup=True)
        w.present()
        spin(1200)
        self.assertIsNotNone(w.markup)                             # opened straight into Markup
        self.assertTrue(w.markup_rev.get_reveal_child())
        w.markup.items = [{"t": "rect", "c": "#ff0000", "w": 0.02, "p": [[0.1, 0.1], [0.9, 0.9]]}]
        w.end_markup(keep=True)
        self.assertIsNone(w.markup)
        self.assertEqual([o[0] for o in w.edits.ops], ["markup"])
        out = os.path.join(d, "out.png")
        w.edits.write(out)
        self.assertGreater(Image.open(out).convert("RGB").getpixel((150, 20))[0], 200)
        w.undo()
        self.assertEqual(w.edits.ops, [])
        w.markup_button()
        w.end_markup(keep=False)                                   # Cancel: nothing
        self.assertEqual(w.edits.ops, [])
        w.destroy()

    def test_thumbnail_opens_markup(self):
        import inspect
        from sonata2.shell import capture
        self.assertIn('"preview", "--markup"', inspect.getsource(capture.Thumbnail._open))


if __name__ == "__main__":
    unittest.main()
