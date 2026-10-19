"""Desktop icons: regressions found testing the desktop's folders and
shortcuts (xvfb-run -a python3 -m unittest tests.test_desktop_bugs)."""
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


def inside(desk, col, row):
    """A point inside a cell (cell_xy is its icon's top-left corner)."""
    x, y = desk.cell_xy(col, row)
    return x + D.CELL_W / 2, y + 20


class _Files:
    def __init__(self, files):
        self.files = files

    def get_files(self):
        return self.files


class DesktopBugsTest(unittest.TestCase):
    NAMES = ("alpha.txt", "beta.pdf", "gamma.txt", "zeta.png")

    @classmethod
    def setUpClass(cls):
        ui.setup()

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.mkdir(os.path.join(self.dir, "Projects"))
        for n in self.NAMES:
            with open(os.path.join(self.dir, n), "w") as f:
                f.write("x")
        config.save("desktop", {"positions": {}, "sort": "none"})
        self.patches = [mock.patch.object(D, "desktop_dir", return_value=Gio.File.new_for_path(self.dir)),
                        mock.patch.object(D, "_connected", return_value={"eDP-1", "HDMI-A-1"})]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def desk(self, screen="eDP-1", main=True):
        d = D.Desktop(screen=screen, main=main)
        d.resized(1280, 800)
        settle(700)
        return d

    def reload(self, *desks):
        for d in desks:
            d._config_changed()
        settle(700)

    def test_dragging_while_sorted_keeps_the_other_icons(self):
        """Dragging one icon with Sort By on put every other icon back where
        it was before sorting."""
        desk = self.desk()
        desk.set_sort("kind")
        self.reload(desk)
        before = dict(desk._placed)
        desk.select([desk.items["zeta.png"]])
        desk.drag_prepare(desk.items["zeta.png"])
        desk._drag_from = desk._placed["zeta.png"]
        desk._drop(None, _Files([]), *inside(desk, 5, 3))
        self.reload(desk)
        self.assertEqual(config.load("desktop", D.DEFAULTS)["sort"], "none")
        self.assertEqual(desk._placed["zeta.png"], (5, 3))
        for name, spot in before.items():
            if name != "zeta.png":
                self.assertEqual(desk._placed[name], spot, name)

    def test_deleting_a_file_doesnt_shuffle_the_other_icons(self):
        desk = self.desk()
        settle(400)                                   # (the spots it got are kept)
        before = dict(desk._placed)
        os.unlink(os.path.join(self.dir, "beta.pdf"))
        settle(1500)
        self.assertNotIn("beta.pdf", desk.items)
        for name, spot in before.items():
            if name != "beta.pdf":
                self.assertEqual(desk._placed[name], spot, name)

    def test_clean_up_keeps_icons_on_the_other_display(self):
        """Clean Up wiped every spot: the second display's icons all went to
        the main one."""
        config.save("desktop", {"positions": {"gamma.txt": [2, 2, "HDMI-A-1"]}, "sort": "none"})
        main, other = self.desk(), self.desk("HDMI-A-1", main=False)
        self.assertIn("gamma.txt", other.items)
        main.clean_up()
        self.reload(main, other)
        self.assertIn("gamma.txt", other.items)
        self.assertNotIn("gamma.txt", main.items)
        other.clean_up()                              # its own Clean Up keeps it there too
        self.reload(main, other)
        self.assertEqual(config.load("desktop", D.DEFAULTS)["positions"]["gamma.txt"], [0, 0, "HDMI-A-1"])
        self.assertIn("gamma.txt", other.items)

    def test_clean_up_sorts_by_name_from_the_first_cell(self):
        desk = self.desk()
        desk._save_positions({"alpha.txt": (4, 4)})
        self.reload(desk)
        desk.clean_up()
        self.reload(desk)
        self.assertEqual(desk._placed["Projects"], (0, 0))
        self.assertEqual(desk._placed["alpha.txt"], (0, 1))

    def test_renaming_to_a_taken_name_doesnt_move_that_icon(self):
        """Renaming an icon to a name already on the Desktop failed silently,
        and the other file's icon was sent to the renamed one's spot."""
        desk = self.desk()
        desk._save_positions({"alpha.txt": (3, 3), "beta.pdf": (5, 1)})
        self.reload(desk)
        alerts = []
        desk.select([desk.items["alpha.txt"]])
        with mock.patch("sonata2.files.views._inline_rename",
                        side_effect=lambda item, info, commit, on_end=None: commit(info, "beta.pdf")), \
                mock.patch("sonata2.shell.layer.take_keyboard"), \
                mock.patch.object(ui.dialog, "alert", side_effect=lambda *a, **k: alerts.append(a[0])):
            desk.rename_selection()
            settle(600)
        self.assertEqual(config.load("desktop", D.DEFAULTS)["positions"]["beta.pdf"][:2], [5, 1])
        self.assertTrue(alerts and "beta.pdf" in alerts[0])

    def test_dropping_several_files_puts_them_all_where_dropped(self):
        """Only the first dropped file went to the drop spot; the others went
        to the top right."""
        src = tempfile.mkdtemp()
        files = []
        for n in ("one.txt", "two.txt", "alpha.txt"):           # alpha.txt: already on the Desktop
            with open(os.path.join(src, n), "w") as f:
                f.write("x")
            files.append(Gio.File.new_for_path(os.path.join(src, n)))
        desk = self.desk()
        alpha = desk._placed["alpha.txt"]
        target = mock.Mock()
        target.get_current_event_state.return_value = 0
        with mock.patch.object(desk, "drop_into", return_value=True):
            desk._drop(target, _Files(files), *inside(desk, 6, 2))
        pos = config.load("desktop", D.DEFAULTS)["positions"]
        self.assertEqual(pos["one.txt"][:2], [6, 2])
        self.assertEqual(pos["two.txt"][:2], [6, 3])
        self.assertEqual(tuple(pos["alpha.txt"][:2]), alpha)     # the icon already there stays put

    def test_drag_into_a_full_column_stays_on_the_grid(self):
        desk = self.desk()
        rows = desk.rows()
        taken = {(desk.cols() - 1, r) for r in range(rows)}
        self.assertIsNone(desk._next_free(desk.cols() - 1, 0, taken))
        self.assertEqual(desk._next_free(desk.cols() - 2, rows - 1, taken), (desk.cols() - 2, rows - 1))

    def test_broken_shortcut_says_the_original_is_gone(self):
        """Opening a shortcut whose original was deleted showed an empty
        Quick Look (or nothing)."""
        os.symlink(os.path.join(self.dir, "gamma.txt"), os.path.join(self.dir, "gamma.txt - Shortcut"))
        desk = self.desk()
        os.unlink(os.path.join(self.dir, "gamma.txt"))
        settle(1500)
        item = desk.items["gamma.txt - Shortcut"]
        seen = []
        with mock.patch.object(ui.dialog, "alert", side_effect=lambda *a, **k: seen.append(a)), \
                mock.patch.object(desk, "quick_look") as ql, \
                mock.patch.object(Gio.AppInfo, "launch_default_for_uri") as launch:
            desk.select([item])
            desk.open_selection()
        ql.assert_not_called()
        launch.assert_not_called()
        self.assertEqual(len(seen), 1)
        self.assertIn("gamma.txt - Shortcut", seen[0][0])
        with mock.patch.object(D.ops, "trash") as trash:
            seen[0][3]("trash")                              # Move to Trash
        trash.assert_called_once()

    def test_working_shortcut_still_opens(self):
        os.symlink(os.path.join(self.dir, "gamma.txt"), os.path.join(self.dir, "gamma.txt - Shortcut"))
        desk = self.desk()
        self.assertFalse(D._broken(desk.items["gamma.txt - Shortcut"].info,
                                   Gio.File.new_for_path(os.path.join(self.dir, "gamma.txt - Shortcut"))))

    def test_taller_menu_bar_moves_the_icons(self):
        """The grid started under the menu bar of when the session began:
        switching Taller menu bar left icons under it, or a gap."""
        config.save("topbar", {"tall": False})
        desk = self.desk()
        y_short = desk.cell_xy(0, 0)[1]
        config.save("topbar", {"tall": True})
        desk._bar_changed()
        self.assertEqual(desk.cell_xy(0, 0)[1] - y_short, 8)
        self.assertIsNotNone(desk._bar_mon)
        desk.release()
        self.assertIsNone(desk._bar_mon)



class NamesInTheUsersLanguageTest(unittest.TestCase):
    """In Portuguese a new folder was "untitled folder" and a shortcut
    "Projetos - Shortcut"."""

    def tearDown(self):
        from sonata2 import i18n
        i18n.use("en")

    def test_new_folder_and_shortcut_names_are_translated(self):
        from sonata2 import i18n
        from sonata2.files import ops
        for lang in ("pt_BR", "pt_PT"):
            i18n.use(lang)
            d = tempfile.mkdtemp()
            os.mkdir(os.path.join(d, "Projetos"))
            here = Gio.File.new_for_path(d)
            made = []
            ops.new_folder(here, made.append, lambda e: self.fail(e))
            end = GLib.get_monotonic_time() + 2_000_000
            while not made and GLib.get_monotonic_time() < end:
                GLib.MainContext.default().iteration(False)
            self.assertEqual(made[0].get_basename(), "pasta sem título")
            links = ops.make_shortcuts([here.get_child("Projetos")], here)
            self.assertEqual(links[0].get_basename(), "Projetos - Atalho")
            self.assertEqual(ops.shortcut_name("app.desktop"), "app.desktop")
        i18n.use("en")
        self.assertEqual(ops.shortcut_name("a.pdf"), "a.pdf - Shortcut")


if __name__ == "__main__":
    unittest.main()
