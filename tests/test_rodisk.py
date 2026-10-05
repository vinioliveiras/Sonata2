"""Vini: the DATA and WIN disks couldn't be opened -- Windows left them
"dirty" (Fast Startup). Files mounts such a disk read-only (macOS opens NTFS
read-only too) and says why; Files' automount does the same at login."""
import inspect
import unittest
from unittest import mock

from sonata2.files import rodisk as R


class RoDiskTest(unittest.TestCase):
    def test_dirty_words(self):
        self.assertTrue(R.is_dirty_error("The disk contains an unclean file system (0, 0)."))
        self.assertTrue(R.is_dirty_error('volume is dirty and "force" flag is not set'))
        self.assertTrue(R.is_dirty_error("Windows is hibernated, refused to mount."))
        self.assertFalse(R.is_dirty_error("Not authorized to perform operation"))

    def test_ntfs_generic_error(self):
        """ntfs3 only says "wrong fs type, bad option..." for a dirty volume."""
        msg = "Error mounting /dev/nvme1n1p1: wrong fs type, bad option, bad superblock on /dev/nvme1n1p1"
        with mock.patch.object(R, "fs_type", return_value="ntfs"):
            self.assertTrue(R.try_readonly("/dev/nvme1n1p1", msg))
        with mock.patch.object(R, "fs_type", return_value="ext4"):
            self.assertFalse(R.try_readonly("/dev/sda1", msg))

    def test_block_path(self):
        self.assertEqual(R.block_path("/dev/nvme1n1p1"), "/org/freedesktop/UDisks2/block_devices/nvme1n1p1")
        self.assertEqual(R.block_path("/dev/mapper/x-y"), "/org/freedesktop/UDisks2/block_devices/x_y")

    def test_readonly_option(self):
        calls = []

        class Bus:
            def call(self, *a):
                calls.append(a)
        with mock.patch.object(R.Gio, "bus_get_sync", return_value=Bus()):
            R.mount_readonly("/dev/nvme1n1p1", lambda *_a: None)
        method, params = calls[0][3], calls[0][4].unpack()[0]
        self.assertEqual(method, "Mount")
        self.assertEqual(params["options"], "ro")

    def test_wired(self):
        from sonata2.files import automount, sidebar
        self.assertIn("rodisk.try_readonly", inspect.getsource(automount))
        self.assertIn("rodisk.try_readonly", inspect.getsource(sidebar))
        self.assertIn("_open_readonly", inspect.getsource(sidebar))


if __name__ == "__main__":
    unittest.main()
