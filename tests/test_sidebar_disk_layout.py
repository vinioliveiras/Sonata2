"""Files sidebar disks (Vini): the free space on the name's line, ending
where the meter ends; the eject button beside the meter."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.files import sidebar as SB  # noqa: E402


class DiskLayoutTest(unittest.TestCase):
    def test_grid(self):
        Gtk.init()
        with mock.patch.object(SB.Sidebar, "_read_space", lambda self, row: None):
            sb = SB.Sidebar(lambda *a: None, Gtk.Box())
            m = mock.Mock()
            m.can_eject.return_value = False
            m.can_unmount.return_value = True
            sb._place("DATA", "drive-harddisk-symbolic", "file:///x", m, disk=True)
        row = sb._disks[-1]
        grid = row.meter.get_parent()
        self.assertIsInstance(grid, Gtk.Grid)
        self.assertIs(grid.get_child_at(1, 0), row.free)
        self.assertIs(grid.get_child_at(0, 1), row.meter)
        self.assertIs(grid.get_child_at(2, 1), row.eject)
        self.assertIsNone(grid.get_child_at(2, 0))
        self.assertIs(row.eject.get_parent(), grid)          # not again at the row's end


if __name__ == "__main__":
    unittest.main()
