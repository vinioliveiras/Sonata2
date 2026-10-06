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
from gi.repository import Adw, GLib, Gtk  # noqa: E402

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

    def test_pixelate_hides_marks_under_it(self):
        """Review: on screen pixelate shows the plain picture; saved, a pen
        stroke drawn before it showed through as coloured blocks."""
        from PIL import Image
        base = Image.new("RGB", (200, 100), "white")
        items = [{"t": "pen", "c": "#ff0000", "w": 0.05, "p": [[0.1, 0.5], [0.9, 0.5]]},
                 {"t": "pixelate", "c": "#000", "p": [[0.0, 0.0], [1.0, 1.0]]}]
        out = M.apply(base, items)
        r, g, b = out.getpixel((100, 50))
        self.assertGreater(g, 200)                                  # white, not red blocks

    def test_no_line_joins_two_marks(self):
        """A text's pen position must not join the next mark (a white line ran
        from a text to a numbered step)."""
        from PIL import Image
        base = Image.new("RGB", (400, 400), "black")
        items = [{"t": "text", "c": "#ffffff", "p": [[0.05, 0.05]], "text": "A", "s": 0.05},
                 {"t": "step", "c": "#ff3b30", "p": [[0.8, 0.8]], "n": 1, "s": 0.05}]
        out = M.apply(base, items)
        self.assertEqual(out.getpixel((200, 200)), (0, 0, 0))       # nothing in between


def _buttons(w):
    out, c = [], w.get_first_child()
    while c is not None:
        if isinstance(c, Gtk.Button):
            out.append(c)
        out += _buttons(c)
        c = c.get_next_sibling()
    return out


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

    def test_marks_only_on_the_picture(self):
        """Vini: no drawing outside the picture. A mark can't start around it,
        and a stroke that leaves it stops at its edge."""
        lay = M.MarkupLayer(lambda: (50, 50, 300, 100), lambda: None)      # picture inside the view
        for tool in ("pen", "hl", "shape", "emoji", "step"):
            lay.set_tool(tool)
            lay.shape = "arrow"
            self.drag(lay, 10, 10, 30, 30)                                 # all of it outside
            self.assertEqual(lay.items, [], tool)
        lay.set_tool("shape")
        lay.shape = "rect"
        self.drag(lay, 100, 80, 600, 400)                                  # starts inside, ends far out
        self.assertEqual(len(lay.items), 1)
        for x, y in lay.items[0]["p"]:
            self.assertTrue(0 <= x <= 1 and 0 <= y <= 1, (x, y))
        self.assertEqual(lay.items[0]["p"][-1], [1.0, 1.0])               # stopped at the corner
        lay.set_tool("select")                                             # selecting is not drawing
        lay._begin(None, 10, 10)
        lay._end(None, 0, 0)

    def test_trash_deletes_every_mark(self):
        """Vini: the trash button only removed the selected mark (none
        selected: nothing). It clears them all; Undo brings them back."""
        lay = self.layer()
        lay.items = [{"t": "rect", "c": "#ff0000", "w": 0.02, "p": [[0.1, 0.1], [0.4, 0.4]]},
                     {"t": "line", "c": "#00ff00", "w": 0.02, "p": [[0.5, 0.5], [0.9, 0.9]]}]
        bar = M.MarkupBar(lay, lambda: None, lambda: None)
        win = Gtk.Window(child=bar)
        win.present()
        self.addCleanup(win.destroy)
        trash = [b for b in _buttons(bar) if b.get_tooltip_text() == "Delete all marks"]
        self.assertEqual(len(trash), 1)
        trash[0].emit("clicked")
        self.assertEqual(lay.items, [])
        lay.undo()
        self.assertEqual(len(lay.items), 2)
        trash[0].emit("clicked")
        trash[0].emit("clicked")                                  # nothing left: no empty undo step
        lay.undo()
        self.assertEqual(len(lay.items), 2)

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

    def test_a_select_click_is_no_edit(self):
        """Review: clicking a mark to select it pushed an undo step and
        cleared Redo; only a real move is an edit."""
        lay = self.layer()
        lay.set_tool("shape")
        lay.shape = "rect"
        self.drag(lay, 100, 50, 80, 40)
        lay.undo()
        lay.redo()
        undo, redo = len(lay._undo), len(lay._redo)
        lay.set_tool("select")
        lay._begin(None, 140, 70)
        lay._end(None, 0, 0)
        self.assertEqual(lay.sel, 0)
        self.assertEqual((len(lay._undo), len(lay._redo)), (undo, redo))
        lay._begin(None, 140, 70)
        lay._update(None, 20, 0)
        lay._end(None, 20, 0)
        self.assertEqual(len(lay._undo), undo + 1)              # a move is one step

    def test_double_click_on_a_text_leaves_no_dots(self):
        """Review: with the pen, a double click on a text (to edit it) left two dots."""
        lay = self.layer()
        win = Gtk.Window(child=lay)
        win.present()
        spin(200)
        self.addCleanup(win.destroy)
        lay.items = [{"t": "text", "c": "#000", "p": [[0.25, 0.25]], "text": "Hello", "s": 0.1}]
        lay.set_tool("pen")
        x0, y0, x1, y1 = M.bounds(lay.items[0], 400, 200)
        x, y = (x0 + x1) / 2, (y0 + y1) / 2
        for _ in range(2):
            lay._begin(None, x, y)
            lay._end(None, 0, 0)
        lay._double_click(x, y)
        spin(M.DOUBLE_CLICK_MS + 150)
        self.assertEqual(len(lay.items), 1)
        if lay._text_pop is not None:
            lay._text_pop.popdown()
        lay._begin(None, 10, 10)                                # a single click: a dot, after the wait
        lay._end(None, 0, 0)
        spin(M.DOUBLE_CLICK_MS + 150)
        self.assertEqual(lay.items[-1]["t"], "pen")

    def test_text_kept_when_its_field_closes_without_return(self):
        lay = self.layer()
        win = Gtk.Window(child=lay)
        win.present()
        spin(200)
        lay.set_tool("text")
        lay._begin(None, 50, 50)
        pop = lay._text_pop
        pop.get_child().set_text("Note")
        pop.popdown()                                           # a click elsewhere
        spin(100)
        self.assertEqual([it.get("text") for it in lay.items], ["Note"])
        win.destroy()

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
        w.from_shot = False                                        # (a picture, not a fresh screenshot)
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

    def test_markup_bar_is_opaque(self):
        """Vini: the bar with the drawing tools was see-through (the window
        paints no background of its own): it is as opaque as the toolbar."""
        from PIL import Image
        from sonata2 import ui
        from sonata2.preview.window import PreviewWindow
        import layoutcheck as LC
        Adw.init()
        ui.setup()
        d = tempfile.mkdtemp()
        path = os.path.join(d, "shot.png")
        Image.new("RGB", (600, 400), "magenta").save(path)
        app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.markupopaque")
        app.register(None)
        w = PreviewWindow(app, path, markup=True)
        w.present()
        spin(1200)
        bar = w.markup_rev.get_child()
        for dark in (False, True):
            Adw.StyleManager.get_default().set_color_scheme(
                Adw.ColorScheme.FORCE_DARK if dark else Adw.ColorScheme.FORCE_LIGHT)
            spin(200)
            self.assertEqual(LC.see_through(bar), 0.0, f"dark={dark}")
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        w.destroy()

    def test_screenshot_done_saves_and_copies(self):
        """From a screenshot's thumbnail, Done saves the marks into the file;
        a clipboard screenshot goes back to the clipboard marked."""
        from unittest import mock
        from PIL import Image
        from sonata2 import ui
        from sonata2.preview import window as PW
        from sonata2.shell.capture import CLIPBOARD_SHOT
        Adw.init()
        ui.setup()
        d = tempfile.mkdtemp()
        path = os.path.join(d, CLIPBOARD_SHOT)
        Image.new("RGB", (200, 100), "white").save(path)
        app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.markuptest2")
        app.register(None)
        w = PW.PreviewWindow(app, path, markup=True)
        w.present()
        spin(1200)
        copied = []
        w.markup.items = [{"t": "rect", "c": "#ff0000", "w": 0.03, "p": [[0.1, 0.1], [0.9, 0.9]]}]
        with mock.patch("subprocess.Popen", lambda cmd, stdin=None: copied.append(cmd)), \
                mock.patch("shutil.which", lambda _n: "/usr/bin/wl-copy"):
            w.end_markup(keep=True)
            spin(1500)
        self.assertGreater(Image.open(path).convert("RGB").getpixel((100, 10))[0], 200)   # saved, marked
        self.assertEqual(copied, [["wl-copy", "--type", "image/png"]])
        w.destroy()

    def test_thumbnail_opens_markup(self):
        import inspect
        from sonata2.shell import capture
        self.assertIn('"preview", "--markup"', inspect.getsource(capture.Thumbnail._open))


class MarkupLifecycleTest(unittest.TestCase):
    """Review fixes: the marks never land on another picture, are never lost
    on close, and only a screenshot is saved without asking."""

    @classmethod
    def setUpClass(cls):
        from sonata2 import ui
        Adw.init()
        ui.setup()

    def window(self, name, paths, markup=True):
        from sonata2.preview import window as PW
        app = Adw.Application(application_id=f"io.github.vinioliveiras.sonata2.mlife.{name}")
        app.register(None)
        w = PW.PreviewWindow(app, paths[0], markup=markup)
        w.pics = list(paths)
        w.present()
        spin(900)
        self.addCleanup(lambda: w.destroy() if w.get_root() is not None else None)
        return app, w

    def pics(self, n=2, colour="white"):
        from PIL import Image
        d = tempfile.mkdtemp()
        out = []
        for i in range(n):
            p = os.path.join(d, f"p{i}.png")
            Image.new("RGB", (200, 100), colour).save(p)
            out.append(p)
        return out

    def test_another_picture_is_never_saved_as_the_shot(self):
        from unittest import mock
        a, b = self.pics()
        _app, w = self.window("other", [a, b])
        self.assertEqual(w.from_shot, a)
        w.end_markup(keep=False)
        w.from_shot = a
        w._opened(b, w.texture, False)                         # moved on to another picture
        w.markup_button()
        w.markup.items = [{"t": "rect", "c": "#ff0000", "w": 0.02, "p": [[0.1, 0.1], [0.9, 0.9]]}]
        with mock.patch.object(w, "save") as save:
            w.end_markup(keep=True)
        save.assert_not_called()                               # only asked when leaving, like any edit
        self.assertTrue(w.edits.edited)

    def test_opening_another_picture_mid_markup_keeps_the_marks_on_this_one(self):
        from unittest import mock
        a, b = self.pics()
        _app, w = self.window("pick", [a, b], markup=False)
        w.markup_button()
        w.markup.items = [{"t": "rect", "c": "#ff0000", "w": 0.02, "p": [[0.1, 0.1], [0.9, 0.9]]}]
        asked = []
        with mock.patch.object(w, "_ask_save", lambda then: asked.append(then)):
            w.open(b)
        self.assertIsNone(w.markup)
        self.assertEqual(w.path, a)                            # stays until the marks are saved or dropped
        self.assertEqual([o[0] for o in w.edits.ops], ["markup"])
        self.assertEqual(len(asked), 1)

    def test_closing_a_shot_mid_markup_saves_the_marks(self):
        from PIL import Image
        a, = self.pics(1)
        _app, w = self.window("close", [a])
        w.markup.items = [{"t": "rect", "c": "#ff0000", "w": 0.03, "p": [[0.1, 0.1], [0.9, 0.9]]}]
        self.assertTrue(w._close_request(w))                    # not yet: saving first
        spin(1500)
        self.assertGreater(Image.open(a).convert("RGB").getpixel((100, 10))[0], 200)

    def test_closing_another_picture_mid_markup_asks(self):
        from unittest import mock
        a, = self.pics(1)
        _app, w = self.window("closeask", [a], markup=False)
        w.markup_button()
        w.markup.items = [{"t": "rect", "c": "#ff0000", "w": 0.03, "p": [[0.1, 0.1], [0.9, 0.9]]}]
        with mock.patch.object(w, "_ask_save") as ask:
            self.assertTrue(w._close_request(w))
        ask.assert_called_once()

    def test_a_new_clipboard_shot_replaces_the_old_one_in_its_window(self):
        from PIL import Image
        from sonata2.preview import window as PW
        from sonata2.shell.capture import CLIPBOARD_SHOT
        d = tempfile.mkdtemp()
        p = os.path.join(d, CLIPBOARD_SHOT)
        Image.new("RGB", (200, 100), "white").save(p)
        app, w = self.window("clip", [p])
        w.end_markup(keep=False)
        Image.new("RGB", (300, 150), "black").save(p)          # the next screenshot, same name
        PW.open_markup(app, p)
        spin(600)
        self.assertEqual((w.texture.get_width(), w.texture.get_height()), (300, 150))
        self.assertEqual(w.from_shot, p)
        self.assertIsNotNone(w.markup)


if __name__ == "__main__":
    unittest.main()
