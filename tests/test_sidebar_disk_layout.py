"""Files sidebar disks (Vini): the free space on the name's line, ending
where the meter ends; Eject is the disk's own icon on hover (a crossfade),
and a right click shows the disk's menu."""
import unittest
from unittest import mock

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from sonata2.files import sidebar as SB  # noqa: E402


def build():
    Gtk.init()
    with mock.patch.object(SB.Sidebar, "_read_space", lambda self, row: None):
        sb = SB.Sidebar(lambda *a: None, Gtk.Box())
        m = mock.Mock()
        m.can_eject.return_value = False
        m.can_unmount.return_value = True
        sb._place("DATA", "drive-harddisk-symbolic", "file:///x", m, disk=True)
    return sb, sb._disks[-1], m


class DiskLayoutTest(unittest.TestCase):
    def test_grid(self):
        sb, row, _m = build()
        grid = row.meter.get_parent()
        self.assertIs(grid.get_child_at(1, 0), row.free)
        self.assertIs(grid.get_child_at(0, 1), row.meter)
        self.assertIsNone(grid.get_child_at(2, 0))                  # no eject button any more
        self.assertIsNone(grid.get_child_at(2, 1))

    def test_icon_turns_into_eject(self):
        sb, row, m = build()
        stack = row.eject
        self.assertIsInstance(stack, Gtk.Stack)
        self.assertEqual(stack.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
        self.assertEqual(stack.get_visible_child_name(), "disk")
        hover = next(c for c in stack.observe_controllers() if isinstance(c, Gtk.EventControllerMotion))
        hover.emit("enter", 1, 1)
        self.assertEqual(stack.get_visible_child_name(), "eject")
        hover.emit("leave")
        self.assertEqual(stack.get_visible_child_name(), "disk")
        click = next(c for c in stack.observe_controllers() if isinstance(c, Gtk.GestureClick))
        with mock.patch.object(sb, "_eject") as ej:
            click.emit("pressed", 1, 1, 1)
        ej.assert_called_once_with(m)

    def test_menu(self):
        sb, row, m = build()
        with mock.patch.object(SB.ui.menu, "popup") as pop:
            sb._disk_menu(row, "DATA", 3, 4)
        labels = [i.label for sec in pop.call_args.args[1] for i in sec]
        self.assertIn("Get Info", labels)
        self.assertIn("Unmount “DATA”", labels)
        self.assertIn("Open Disk Utility", labels)
        m.can_eject.return_value = True
        with mock.patch.object(SB.ui.menu, "popup") as pop:
            sb._disk_menu(row, "DATA", 3, 4)
        self.assertIn("Eject “DATA”", [i.label for sec in pop.call_args.args[1] for i in sec])

    def test_computer_has_menu_without_eject(self):
        sb, _row, _m = build()
        comp = sb._rows["file:///"]
        self.assertIsNone(getattr(comp, "mount", None))
        with mock.patch.object(SB.ui.menu, "popup") as pop:
            sb._disk_menu(comp, "Computer", 1, 1)
        labels = [i.label for sec in pop.call_args.args[1] for i in sec]
        self.assertFalse(any(lbl.startswith(("Eject", "Unmount")) for lbl in labels))


if __name__ == "__main__":
    unittest.main()
