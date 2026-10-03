"""Preview: opens a picture, goes to the next one, zooms around a point,
thumbnails, Info, slideshow, Resize/Export, and no resize loop behind the
save alert (xvfb-run python3 -m unittest tests.test_preview)."""
import os
import tempfile
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def wait(cond, ms=3000):
    """← / → and Save run off the main loop: wait for them."""
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


def picture(path, w, h, color=0x3366ccff):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pb.fill(color)
    pb.savev(path, os.path.splitext(path)[1].lstrip(".").replace("jpg", "jpeg"), [], [])
    return path


class PreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.preview")
        cls.app.register(None)

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.a = picture(os.path.join(self.dir, "a.png"), 300, 100)
        self.b = picture(os.path.join(self.dir, "b.png"), 200, 100, 0xcc6633ff)
        self.c = picture(os.path.join(self.dir, "c.png"), 1200, 800, 0x33cc66ff)
        self.wins = []

    def tearDown(self):
        for w in self.wins:
            if w.edits is not None:
                w.edits.revert()
            w.destroy()
        settle(50)

    def window(self, path, paths=None, size=(600, 450)):
        from sonata2.preview.window import PreviewWindow
        win = PreviewWindow(self.app, path, paths=paths)
        win.set_default_size(*size)
        win.present()
        self.wins.append(win)
        settle(300)
        return win

    def test_pictures(self):
        win = self.window(self.a)
        self.assertEqual(win.size_text, "300 × 100")
        self.assertEqual(len(win.pics), 3)                  # the folder, listed off the main loop
        self.assertFalse(win.sidebar.get_reveal_child())    # one picture: no thumbnails
        win.go(1)
        self.assertTrue(wait(lambda: win.path.endswith("b.png")))
        win.step_zoom(1)
        self.assertIsNotNone(win.zoom)
        win.rotate(90)
        self.assertEqual(win.texture.get_width(), 100)

    def test_zoom_around_pointer(self):
        win = self.window(self.c)
        cv = win.canvas
        self.assertIsNone(win.zoom)
        self.assertLess(cv.scale(), 1.0)                    # 1200 × 800 fits a smaller window
        anchor = (cv.get_width() * 0.2, cv.get_height() * 0.3)
        before = cv.to_image(*anchor)
        win.set_zoom(2.0, anchor=anchor)
        after = cv.to_image(*anchor)
        self.assertAlmostEqual(before[0], after[0], delta=0.5)   # the point under the pointer stays put
        self.assertAlmostEqual(before[1], after[1], delta=0.5)
        self.assertTrue(cv.pannable())
        x0 = cv.image_rect()[0]
        self.assertTrue(cv.pan_by(-50, 0))
        self.assertAlmostEqual(cv.image_rect()[0], x0 - 50, delta=0.5)
        for _ in range(100):                                 # can't pan past the edge
            cv.pan_by(-500, 0)
        r = cv.image_rect()
        self.assertAlmostEqual(r[0] + r[2], cv.get_width(), delta=0.5)
        # animated steps end on the next zoom level
        from sonata2.preview.window import ZOOMS
        win.set_zoom(None)
        fit = cv.fit_scale()
        win.step_zoom(1)
        settle(500)
        self.assertEqual(win.zoom, next(z for z in ZOOMS if z > fit + 1e-3))
        win.step_zoom(1)
        win.step_zoom(1)                                     # a second press while gliding adds up
        settle(500)
        self.assertEqual(win.zoom, [z for z in ZOOMS if z > fit + 1e-3][2])
        # double-click: fit <-> actual size
        win.set_zoom(None)
        cv.set_zoom(1.0, anchor=(10, 10), animate=True)
        settle(500)
        self.assertEqual(win.zoom, 1.0)
        # a picture smaller than the view stays centred
        win.go(-1)
        win.go(-1)          # a.png (300 × 100)
        self.assertTrue(wait(lambda: win.path.endswith("a.png")))
        settle(100)
        x, y, w, h = cv.image_rect()
        self.assertAlmostEqual(x + w / 2, cv.get_width() / 2, delta=1)
        self.assertEqual((w, h), (300, 100))

    def test_open_several(self):
        from sonata2.preview import sidebar
        from sonata2.preview.window import PreviewWindow, open_paths
        before = [w for w in self.app.get_windows() if isinstance(w, PreviewWindow)]
        open_paths(self.app, [self.c, self.a])
        wins = [w for w in self.app.get_windows() if isinstance(w, PreviewWindow) and w not in before]
        self.wins += wins
        self.assertEqual(len(wins), 1)                       # one window, not two
        win = wins[0]
        settle(600)
        self.assertTrue(win.sidebar.get_reveal_child())
        self.assertEqual(win.pics, [self.c, self.a])         # the files opened, in that order
        self.assertEqual(win.thumbs.model.get_n_items(), 2)
        self.assertEqual(win.thumbs.selection.get_selected(), 0)
        win.go(1)
        self.assertTrue(wait(lambda: win.path == self.a))
        self.assertEqual(win.thumbs.selection.get_selected(), 1)   # selection follows ← →
        win.thumbs.selection.set_selected(0)                 # a click jumps
        self.assertTrue(wait(lambda: win.path == self.c))
        # thumbnails were made off the main loop and cached, small
        keys = [k for k in sidebar.LOADER.cache if k[0] in (self.a, self.c)]
        self.assertEqual(len(keys), 2)
        tex = sidebar.LOADER.cache[sidebar.LOADER.key(self.c)]
        self.assertLessEqual(max(tex.get_width(), tex.get_height()), sidebar.THUMB)
        # ⌥⌘2 hides it
        win._key(None, Gdk.KEY_2, 0, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK)
        self.assertFalse(win.sidebar.get_reveal_child())

    def test_thumb_cache_limit(self):
        from sonata2.preview.sidebar import ThumbLoader
        loader = ThumbLoader(size=32, limit=2)
        got = []
        for p in (self.a, self.b, self.c):
            self.assertIsNone(loader.get(p, lambda path, tex: got.append(path)))
        settle(800)
        self.assertEqual(sorted(got), sorted([self.a, self.b, self.c]))
        self.assertEqual(len(loader.cache), 2)               # the oldest dropped
        self.assertIsNotNone(loader.get(self.c, lambda *_: None))

    def test_info(self):
        from sonata2.preview import info
        data = info.read(self.a)
        general = dict(data["general"])
        self.assertEqual(general["Dimensions"], "300 × 100 pixels")
        self.assertIn("File Size", general)
        self.assertEqual(info.exposure_text(0.004), "1/250 s")
        self.assertEqual(info.fnumber_text(2.8), "ƒ/2.8")
        self.assertEqual(info.fnumber_text(8.0), "ƒ/8")
        self.assertEqual(info.focal_text(35.0), "35 mm")
        self.assertEqual(info.exif_date_text("2024:05:01 12:34:56"), "May 1, 2024 at 12:34")
        win = self.window(self.a)
        win._key(None, Gdk.KEY_i, 0, Gdk.ModifierType.CONTROL_MASK)
        settle(400)
        self.assertTrue(win.info_rev.get_reveal_child())
        self.assertEqual(win.info.path, self.a)

    @unittest.skipUnless(HAVE_PIL, "Pillow")
    def test_exif(self):
        from PIL import Image
        from sonata2.preview import info
        p = os.path.join(self.dir, "cam.jpg")
        ex = Image.Exif()
        ex[0x010F], ex[0x0110] = "Canon", "Canon EOS R6"
        sub = ex.get_ifd(0x8769)
        sub.update({0x829A: 1 / 125, 0x829D: 4.0, 0x8827: 800, 0x920A: 50.0, 0x9003: "2023:12:24 18:05:00",
                    0xA434: "RF50mm F1.8 STM"})
        ex.get_ifd(0x8825)[1] = "N"
        Image.new("RGB", (64, 48), (200, 100, 50)).save(p, "JPEG", exif=ex)
        rows = dict(info.read(p)["camera"])
        self.assertEqual(rows["Camera"], "Canon EOS R6")
        self.assertEqual(rows["Lens"], "RF50mm F1.8 STM")
        self.assertEqual(rows["Exposure"], "1/125 s")
        self.assertEqual(rows["Aperture"], "ƒ/4")
        self.assertEqual(rows["ISO"], "800")
        self.assertEqual(rows["Focal Length"], "50 mm")
        self.assertEqual(rows["Date Taken"], "Dec 24, 2023 at 18:05")
        self.assertIn("Location", rows)

    def test_slideshow(self):
        win = self.window(self.a)
        win._key(None, Gdk.KEY_f, 0, Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        self.assertIsNotNone(win._slides)
        self.assertFalse(win.toolbar.get_visible())
        self.assertTrue(win._slides["timer"])
        win._next_slide()                                     # what the 3 s timer does
        self.assertTrue(wait(lambda: win.path == self.b))
        self.assertIsNotNone(win.canvas._fade)                # cross-fading
        win._key(None, Gdk.KEY_Right, 0, 0)
        self.assertTrue(wait(lambda: win.path == self.c))
        win._key(None, Gdk.KEY_space, 0, 0)                   # pause
        self.assertEqual(win._slides["timer"], 0)
        win._key(None, Gdk.KEY_Escape, 0, 0)
        self.assertIsNone(win._slides)
        self.assertTrue(win.toolbar.get_visible())

    def test_crop_still_works(self):
        win = self.window(self.c)
        win.crop_button()
        self.assertTrue(win.crop_area.get_visible())
        x, y, w, h = win._picture_rect()
        win.crop = (x, y, x + w / 2, y + h / 2)
        win.crop_button()                                   # apply
        self.assertFalse(win.crop_area.get_visible())
        if win.edits is not None:
            self.assertEqual(win.edits.size(), (600, 400))

    @unittest.skipUnless(HAVE_PIL, "Pillow")
    def test_resize_and_export(self):
        from PIL import Image
        win = self.window(self.c)
        sheet = win.resize_sheet()
        s = sheet.sheet
        s["percent"].set_value(50)
        self.assertEqual((s["width"].get_value(), s["height"].get_value()), (600, 400))
        s["width"].set_value(300)                            # keeps the ratio
        self.assertEqual(s["height"].get_value(), 200)
        s["ok"]()
        self.assertEqual(win.edits.size(), (300, 200))
        self.assertTrue(win.edits.edited)
        win.undo()
        win.resize_to(240, 100)
        self.assertEqual(win.edits.size(), (240, 100))
        for fmt, ext, kind in (("JPEG", ".jpg", "JPEG"), ("WEBP", ".webp", "WEBP"), ("TIFF", ".tiff", "TIFF"),
                               ("PNG", ".png", "PNG")):
            target = os.path.join(self.dir, "out" + ext)
            self.assertTrue(win.export_as(fmt, ext, 70, target=target))
            with Image.open(target) as im:
                self.assertEqual(im.format, kind)
                self.assertEqual(im.size, (240, 100))
        self.assertTrue(win.edits.edited)                    # exporting keeps the edits
        self.assertEqual(win.path, self.c)
        ex = win.export_sheet().sheet
        self.assertEqual(ex["format"].get_selected(), 0)     # PNG, like the picture
        # saving writes the full-size result
        win.save()
        self.assertTrue(win._saving)                         # written off the main loop
        self.assertTrue(wait(lambda: not win._saving))
        with Image.open(self.c) as im:
            self.assertEqual(im.size, (240, 100))
        self.assertFalse(win.edits.edited)

    @unittest.skipUnless(HAVE_PIL, "Pillow")
    def test_save_alert_no_resize_loop(self):
        """The "keep the changes?" alert: the window behind it doesn't lay
        itself out again and again (the alert "wobbled"), and there's one alert."""
        from sonata2.preview.window import PreviewWindow
        counts = {"n": 0}

        class Counting(PreviewWindow):
            def do_size_allocate(self, w, h, baseline):
                counts["n"] += 1
                Gtk.ApplicationWindow.do_size_allocate(self, w, h, baseline)
        win = Counting(self.app, self.c)
        win.set_default_size(600, 450)
        win.present()
        self.wins.append(win)
        settle(300)
        win.rotate(90)
        win.close()                                          # asks
        prompt = win.prompt
        win.close()                                          # asked again: no second alert
        self.assertIs(win.prompt, prompt)
        settle(300)
        counts["n"] = 0
        settle(1000)
        self.assertLessEqual(counts["n"], 2, "the window keeps laying itself out while the alert is up")
        root = prompt.get_root()
        self.assertIsNot(root, win)                          # the alert is its own window
        prompt.emit("response", "cancel")
        if hasattr(prompt, "force_close"):
            prompt.force_close()
        settle(100)
        self.assertTrue(win.edits.edited)                    # Cancel keeps the edits and the window
        self.assertFalse(win._asking)


if __name__ == "__main__":
    unittest.main()
