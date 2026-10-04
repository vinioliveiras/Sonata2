"""The desktop on every display (xvfb-run python3 -m unittest tests.test_desktop).
Regression: the second display's desktop did nothing (no menu, no folders)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from sonata2 import config, ui  # noqa: E402
from sonata2.shell import desktop as D  # noqa: E402


def settle(ms=400):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class DesktopDisplaysTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ui.setup()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        for n in ("a.txt", "b.txt", "c.txt"):
            open(os.path.join(self.dir, n), "w").close()
        config.save("desktop", {"positions": {"b.txt": [0, 1, "HDMI-A-1"], "c.txt": [0, 0, "DP-9"]},
                                "sort": "none"})
        self.patches = [mock.patch.object(D, "desktop_dir", return_value=Gio.File.new_for_path(self.dir)),
                        mock.patch.object(D, "_connected", return_value={"eDP-1", "HDMI-A-1"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def desks(self):
        main, other = D.Desktop(screen="eDP-1", main=True), D.Desktop(screen="HDMI-A-1", main=False)
        for d in (main, other):
            d.resized(1920, 1080)
        settle()
        return main, other

    def test_icons_split_between_displays(self):
        main, other = self.desks()
        self.assertEqual(sorted(other.items), ["b.txt"])
        self.assertEqual(sorted(main.items), ["a.txt", "c.txt"])     # DP-9 isn't plugged in: on the main one

    def test_spots_on_another_display_name_it_and_survive(self):
        main, other = self.desks()
        other._save_positions({"a.txt": (2, 3)})
        pos = config.load("desktop", D.DEFAULTS)["positions"]
        self.assertEqual(pos["a.txt"], [2, 3, "HDMI-A-1"])
        self.assertEqual(pos["c.txt"], [0, 0, "DP-9"])                 # other icons' spots kept
        settle()
        main._config_changed()
        other._config_changed()
        settle()
        self.assertIn("a.txt", other.items)
        self.assertNotIn("a.txt", main.items)
        main._save_positions({"a.txt": (0, 0)})                         # dragged back: the main display
        self.assertEqual(config.load("desktop", D.DEFAULTS)["positions"]["a.txt"], [0, 0])

    def test_new_folder_spot_not_pruned(self):
        main, other = self.desks()
        other._save_positions({"New Folder": (1, 1)})                  # being made: not on disk yet
        self.assertEqual(config.load("desktop", D.DEFAULTS)["positions"]["New Folder"], [1, 1, "HDMI-A-1"])

    def test_new_folder_appears_where_clicked_and_takes_the_name(self):
        """Vini: a new folder showed top right and glided to the clicked spot,
        and its name had to be clicked before typing."""
        main, _other = self.desks()
        took = []
        with mock.patch("sonata2.shell.layer.take_keyboard", side_effect=lambda w, on, rest="none": took.append((on, rest))):
            main.new_folder((3, 2))
            # the first layout that sees it already puts it at the click
            seen = []
            real = main.move

            def move(item, x, y):
                if item.info.get_name() == "untitled folder":
                    seen.append((x, y))
                real(item, x, y)
            with mock.patch.object(main, "move", side_effect=move):
                for _ in range(20):
                    settle(100)
                    main._config_changed()
            self.assertTrue(seen)
            self.assertEqual(set(seen), {main.cell_xy(3, 2)})              # never anywhere else first
            settle(400)
            item = main.items["untitled folder"]
            entry = item.lbl.get_next_sibling()
            self.assertEqual(type(entry).__name__, "Entry")                 # typing the name right away
            self.assertEqual(took[-1], (True, "on_demand"))                # the desktop takes the keyboard
            main.items["untitled folder"].info = item.info.dup()            # a refresh while typing:
            main._sync()                                                    # the field stays
            settle(100)
            self.assertIs(main.items["untitled folder"], item)
            entry.emit("activate")
            settle(100)
        self.assertEqual(took[-1], (False, "on_demand"))                   # and gives it back
        # the folder's info refreshed (a second monitor event): a new icon for it, at its spot,
        # never gliding in from the top-left corner (Vini)
        main.items["untitled folder"].info = main.items["untitled folder"].info.dup()
        main._sync()
        fresh = main.items["untitled folder"]                            # before any layout ran:
        t = main.get_layout_manager().get_layout_child(fresh).get_transform()
        self.assertEqual(tuple(round(v) for v in t.to_translate()), main.cell_xy(3, 2))
        settle(300)
        self.assertIsNone(getattr(fresh, "_glide_anim", None))

    def test_every_display_has_a_desktop(self):
        from sonata2.shell.wallpaper import WallpaperWindow
        import inspect
        self.assertIn("main", inspect.signature(WallpaperWindow).parameters)
        src = open(os.path.join(os.path.dirname(D.__file__), "..", "__main__.py")).read()
        self.assertNotIn("desktop=m is monitors.main()", src)


if __name__ == "__main__":
    unittest.main()
