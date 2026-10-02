"""Disk Utility: parses a fake UDisks2 tree (disks, volumes, Internal /
External grouping, protected volumes, capacity segments) and builds its
window with and without UDisks
(xvfb-run python3 -m unittest tests.test_diskutil)."""
import os
import tempfile
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.diskutil import model  # noqa: E402

U = "org.freedesktop.UDisks2."
OBJ = "/org/freedesktop/UDisks2/"
GB = 1000 ** 3
MB = 1000 ** 2


def ay(s: str) -> list:
    return list(s.encode()) + [0]


def block(dev, size, drive="/", usage="", fs="", label="", uuid="", **kw):
    b = {"Device": ay(dev), "PreferredDevice": ay(dev), "Size": size, "Drive": drive, "IdUsage": usage,
         "IdType": fs, "IdLabel": label, "IdUUID": uuid, "IdVersion": kw.pop("version", ""),
         "HintIgnore": kw.pop("ignore", False), "CryptoBackingDevice": kw.pop("backing", "/")}
    return {U + "Block": b}


def part(table, number, offset, size, **kw):
    return {U + "Partition": {"Table": table, "Number": number, "Offset": offset, "Size": size,
                              "Name": kw.get("name", ""), "IsContainer": kw.get("container", False)}}


def fs(*mounts):
    return {U + "Filesystem": {"MountPoints": [ay(m) for m in mounts]}}


def fake_objects() -> dict:
    """An NVMe system disk (EFI, /, swap, encrypted /home), a SATA disk,
    a USB stick (ExFAT + locked LUKS), an SD card with no partition table
    and an ISO mounted from a file."""
    D = OBJ + "drives/"
    nvme, hdd, usb, sd = D + "Samsung_SSD_980", D + "WDC_WD20", D + "SanDisk", D + "SD"
    B = OBJ + "block_devices/"
    t = {
        nvme: {U + "Drive": {"Vendor": "", "Model": "Samsung SSD 980 1TB", "Size": 1000 * GB, "ConnectionBus": "",
                             "Removable": False, "MediaRemovable": False, "RotationRate": 0},
               U + "NVMe.Controller": {"SmartUpdated": 1700000000, "SmartCriticalWarning": []}},
        hdd: {U + "Drive": {"Vendor": "WDC", "Model": "WD20EZRZ", "Size": 2000 * GB, "RotationRate": 5400},
              U + "Drive.Ata": {"SmartSupported": True, "SmartEnabled": True, "SmartFailing": False}},
        usb: {U + "Drive": {"Vendor": "SanDisk", "Model": "Ultra", "Size": 64 * GB, "ConnectionBus": "usb",
                            "Removable": True, "Ejectable": True, "CanPowerOff": True}},
        sd: {U + "Drive": {"Vendor": "", "Model": "SD Card Reader", "Size": 32 * GB, "ConnectionBus": "sdio",
                           "MediaRemovable": True, "Media": "flash_sd"}},
        # system disk
        B + "nvme0n1": {**block("/dev/nvme0n1", 1000 * GB, nvme), U + "PartitionTable": {"Type": "gpt"}},
        B + "nvme0n1p1": {**block("/dev/nvme0n1p1", 512 * MB, nvme, "filesystem", "vfat", "", "AB12-CD34",
                                  version="FAT32"),
                          **part(B + "nvme0n1", 1, 1 * MB, 512 * MB, name="EFI System Partition"), **fs("/boot/efi")},
        B + "nvme0n1p2": {**block("/dev/nvme0n1p2", 200 * GB, nvme, "filesystem", "ext4", "CachyOS", "u-root"),
                          **part(B + "nvme0n1", 2, 513 * MB, 200 * GB), **fs("/")},
        B + "nvme0n1p3": {**block("/dev/nvme0n1p3", 16 * GB, nvme, "other", "swap"),
                          **part(B + "nvme0n1", 3, 513 * MB + 200 * GB, 16 * GB), U + "Swapspace": {"Active": True}},
        B + "nvme0n1p4": {**block("/dev/nvme0n1p4", 600 * GB, nvme, "crypto", "crypto_LUKS", "", "u-luks"),
                          **part(B + "nvme0n1", 4, 513 * MB + 216 * GB, 600 * GB),
                          U + "Encrypted": {"CleartextDevice": B + "dm_2d0"}},
        B + "dm_2d0": {**block("/dev/dm-0", 600 * GB, "/", "filesystem", "ext4", "home", "u-home",
                               backing=B + "nvme0n1p4"), **fs("/home")},
        # data disk
        B + "sda": {**block("/dev/sda", 2000 * GB, hdd), U + "PartitionTable": {"Type": "gpt"}},
        B + "sda1": {**block("/dev/sda1", 2000 * GB - 2 * MB, hdd, "filesystem", "ntfs", "Games", "u-games"),
                     **part(B + "sda", 1, 1 * MB, 2000 * GB - 2 * MB), **fs()},
        # USB stick
        B + "sdb": {**block("/dev/sdb", 64 * GB, usb), U + "PartitionTable": {"Type": "dos"}},
        B + "sdb1": {**block("/dev/sdb1", 48 * GB, usb, "filesystem", "exfat", "BACKUP", "u-backup"),
                     **part(B + "sdb", 1, 1 * MB, 48 * GB), **fs("/run/media/vini/BACKUP")},
        B + "sdb2": {**block("/dev/sdb2", 16 * GB - 1 * MB, usb, "crypto", "crypto_LUKS", "", "u-vault"),
                     **part(B + "sdb", 2, 1 * MB + 48 * GB, 16 * GB - 1 * MB, name="Vault"),
                     U + "Encrypted": {"CleartextDevice": "/"}},
        # SD card, no partition table
        B + "mmcblk0": {**block("/dev/mmcblk0", 32 * GB, sd, "filesystem", "vfat", "CAMERA", "u-cam", version="FAT32"),
                        **fs()},
        # a disk image and a snap loop (ignored)
        B + "loop0": {**block("/dev/loop0", 5 * GB, "/", "filesystem", "iso9660", "Ubuntu 24.04", "u-iso"),
                      U + "Loop": {"BackingFile": ay("/home/vini/Downloads/ubuntu.iso")},
                      **fs("/run/media/vini/Ubuntu 24.04")},
        B + "loop1": {**block("/dev/loop1", 50 * MB, "/", "filesystem", "squashfs", ignore=True),
                      U + "Loop": {"BackingFile": ay("/var/lib/snapd/snaps/core.snap")}, **fs("/snap/core/1")},
        B + "loop2": {**block("/dev/loop2", 0, "/"), U + "Loop": {"BackingFile": []}},
        OBJ + "Manager": {U + "Manager": {"Version": "2.10.1"}},
    }
    return t


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class FakeClient:
    """Stands in for udisks.Client: hands over a fixed tree (or fails)."""

    def __init__(self, objects=None, error=None):
        self.objects, self.error, self.calls = objects, error, []

    def start(self, on_objects, on_error):
        if self.error is not None:
            on_error(self.error)
        else:
            on_objects(self.objects)

    def stop(self):
        pass

    def can(self, what, fs_type, done):
        done(fs_type != "ntfs", "mkfs.ntfs" if fs_type == "ntfs" else "")

    def __getattr__(self, name):          # mount, unmount, format...: recorded
        return lambda *a, **kw: self.calls.append((name, a, kw))


class ModelTest(unittest.TestCase):
    def setUp(self):
        self.disks = model.parse(fake_objects())
        self.by_dev = {d.device: d for d in self.disks}

    def test_drives_and_volumes(self):
        self.assertEqual(sorted(self.by_dev), ["/dev/loop0", "/dev/mmcblk0", "/dev/nvme0n1", "/dev/sda", "/dev/sdb"])
        nvme = self.by_dev["/dev/nvme0n1"]
        self.assertEqual(nvme.name, "Samsung SSD 980 1TB")
        self.assertEqual([v.device for v in nvme.volumes], ["/dev/nvme0n1p1", "/dev/nvme0n1p2", "/dev/nvme0n1p3",
                                                            "/dev/nvme0n1p4"])
        efi, root, swap, home = nvme.volumes
        self.assertEqual(efi.kind, "MS-DOS (FAT32)")
        self.assertEqual(root.name, "CachyOS")
        self.assertEqual(root.mount_point, "/")
        self.assertFalse(swap.listed)                   # swap: on the bar, not in the sidebar
        self.assertTrue(home.encrypted and not home.locked)
        self.assertEqual((home.fs_path, home.fs_type, home.mount_point, home.name),
                         (fake_path("dm_2d0"), "ext4", "/home", "home"))
        self.assertEqual(nvme.table, "gpt")
        self.assertEqual(nvme.smart, "Verified")
        self.assertEqual(self.by_dev["/dev/sda"].smart, "Verified")
        self.assertEqual(self.by_dev["/dev/sda"].icon, "drive-harddisk")
        self.assertEqual(nvme.icon, "drive-harddisk-solidstate")
        vault = self.by_dev["/dev/sdb"].volumes[1]
        self.assertTrue(vault.locked and vault.listed)
        self.assertEqual(vault.name, "Vault")
        self.assertEqual(vault.kind, "Encrypted (Locked)")
        sd = self.by_dev["/dev/mmcblk0"]                 # unpartitioned: the disk is its own volume
        self.assertEqual([v.name for v in sd.volumes], ["CAMERA"])
        self.assertEqual(sd.icon, "media-flash")
        self.assertEqual(self.by_dev["/dev/loop0"].name, "ubuntu.iso")

    def test_text(self):
        self.assertEqual(model.text(ay("/dev/sda")), "/dev/sda")
        self.assertEqual(model.text(b"/mnt\0"), "/mnt")
        self.assertEqual(model.text(None), "")

    def test_grouping(self):
        groups = {title: [d.device for d in ds] for title, ds in model.grouped(self.disks)}
        self.assertEqual(list(groups), ["Internal", "External", "Disk Images"])
        self.assertEqual(groups["Internal"], ["/dev/nvme0n1", "/dev/sda"])
        self.assertEqual(sorted(groups["External"]), ["/dev/mmcblk0", "/dev/sdb"])
        self.assertEqual(groups["Disk Images"], ["/dev/loop0"])

    def test_protected(self):
        nvme = self.by_dev["/dev/nvme0n1"]
        self.assertTrue(all(v.protected for v in nvme.volumes))    # /boot/efi, /, swap, /home (unlocked LUKS)
        self.assertTrue(nvme.protected)
        self.assertFalse(self.by_dev["/dev/sda"].protected)
        self.assertFalse(self.by_dev["/dev/sdb"].protected)
        self.assertFalse(any(v.protected for v in self.by_dev["/dev/sdb"].volumes))

    def test_segments(self):
        nvme = self.by_dev["/dev/nvme0n1"]
        segs = model.segments(nvme)
        self.assertEqual([s[0] for s in segs], ["EFI System Partition", "CachyOS", "Swap", "home", "Free Space"])
        self.assertEqual(segs[2][2], "sys_gray")         # swap in grey
        self.assertEqual(segs[-1][2], "free")
        self.assertEqual(segs[-1][1], 1000 * GB - (513 * MB + 816 * GB))
        self.assertEqual(sum(s[1] for s in segs), 1000 * GB - 1 * MB)   # the 1 MB gap before p1 is too small
        root = nvme.volumes[1]
        self.assertEqual(model.segments(nvme, root, (50 * GB, 150 * GB)),
                         [("Used", 50 * GB, root_colour(nvme, root)), ("Free", 150 * GB, "free")])
        self.assertEqual(model.segments(nvme, root), [("CachyOS", 200 * GB, root_colour(nvme, root))])
        sd = self.by_dev["/dev/mmcblk0"]
        self.assertEqual([s[0] for s in model.segments(sd)], ["CAMERA"])

    def test_formats(self):
        self.assertIn("ntfs", [t for t, _l in model.formats(lambda t: True)])
        self.assertNotIn("ntfs", [t for t, _l in model.formats(lambda t: t != "ntfs")])
        self.assertEqual([t for t, _l in model.formats(lambda t: False)][:1], ["ext4"])


def fake_path(name):
    return OBJ + "block_devices/" + name


def root_colour(disk, vol):
    return model.SEGMENT_COLORS[disk.volumes.index(vol) % len(model.SEGMENT_COLORS)]


class WindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.test.diskutil")
        cls.app.register(None)

    def test_without_udisks(self):
        from sonata2.diskutil.window import DiskUtilityWindow
        win = DiskUtilityWindow(self.app, client=FakeClient(error="The name org.freedesktop.UDisks2 was not provided"))
        win.present()
        settle()
        self.assertEqual(win.stack.get_visible_child_name(), "missing")
        self.assertEqual(win.missing_title.get_label(), "Disk Manager needs UDisks2")
        self.assertFalse(win.sidebar.get_visible())
        self.assertFalse(any(win.btn[k].get_sensitive() for k in ("erase", "mount", "eject", "info", "partition")))
        win.destroy()

    def test_real_client_degrades(self):
        """With the real client and no UDisks on the system bus (tests), the
        window shows the message instead of crashing."""
        from sonata2.diskutil.window import DiskUtilityWindow
        win = DiskUtilityWindow(self.app)
        win.present()
        settle(1500)
        self.assertIn(win.stack.get_visible_child_name(), ("missing", "detail", "empty", "loading"))
        win.destroy()

    def test_with_disks(self):
        from sonata2.diskutil.window import DiskUtilityWindow
        client = FakeClient(fake_objects())
        win = DiskUtilityWindow(self.app, client=client)
        win.present()
        settle()
        self.assertEqual(win.stack.get_visible_child_name(), "detail")
        rows, i = [], 0
        while (r := win.list.get_row_at_index(i)) is not None:
            rows.append(r)
            i += 1
        items = [r.item for r in rows]
        self.assertEqual([r.get_child().get_label() for r in rows if r.item is None],
                         ["Internal", "External", "Disk Images"])
        names = [it.name for it in items if it is not None]
        self.assertIn("Vault", names)
        self.assertNotIn("Swap", names)             # swap stays out of the sidebar
        # the system disk: nothing destructive
        self.assertIsInstance(win.selected, model.Disk)
        self.assertEqual(win.selected.device, "/dev/nvme0n1")
        a = win.actions()
        self.assertFalse(a["erase"] or a["eject"])
        self.assertTrue(a["info"])
        root = next(it for it in items if isinstance(it, model.Volume) and it.mount_point == "/")
        a = win.actions(root)
        self.assertFalse(a["erase"] or a["mount"] or a["first_aid"])
        # the USB stick's ExFAT volume
        backup = next(it for it in items if it is not None and it.name == "BACKUP")
        win.list.select_row(rows[items.index(backup)])
        settle(300)
        self.assertIs(win.selected, backup)
        a = win.actions()
        self.assertTrue(a["erase"] and a["mount"] and a["eject"] and a["rename"] and a["first_aid"])
        self.assertEqual(a["verb"], "Unmount")
        self.assertEqual(win.name_label.get_label(), "BACKUP")
        self.assertIn("ExFAT", win.sub_label.get_label())
        self.assertFalse(win.btn["partition"].get_sensitive())
        # a locked volume offers Unlock
        vault = next(it for it in items if it is not None and it.name == "Vault")
        self.assertEqual(win.actions(vault)["verb"], "Unlock")
        # erase goes through the confirmation, then Block.Format with tear-down
        win._do_erase(backup, "USB", "exfat", None)
        self.assertEqual(client.calls[-1][0], "format")
        self.assertEqual(client.calls[-1][1][:3], (backup.path, "exfat", "USB"))
        win._set_busy("")
        # erasing a protected volume is refused even if asked directly
        n = len(client.calls)
        win._do_erase(root, "x", "ext4", None)
        self.assertEqual(len(client.calls), n)
        # eject unmounts first, then ejects and powers off
        client.calls.clear()
        win.eject(backup.disk)
        self.assertEqual(client.calls[0][0], "unmount")
        # live update: the stick is unplugged
        objs = {k: v for k, v in fake_objects().items() if "sdb" not in k and "SanDisk" not in k}
        win.load(objs)
        settle()
        self.assertNotIn("/dev/sdb", [d.device for d in win.disks])
        self.assertIsNotNone(win.selected)
        self.assertTrue(win.info_fields())
        win.destroy()


if __name__ == "__main__":
    unittest.main()


class DiskManagerIconTest(unittest.TestCase):
    """Vini: Disk Manager's icon (MacTahoe's) showed an Apple logo -- no
    Apple names or logos on screen. Sonata's own drive icon, also for the
    names other disk apps ask for."""

    def test_own_icon(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent
        apps = root / "sonata2" / "data" / "icons" / "Sonata" / "apps" / "scalable"
        art = (apps / "sonata-diskmanager.svg").read_text()
        self.assertNotIn("apple", art.lower())
        for name in ("gnome-disk-utility", "gnome-disks", "org.gnome.DiskUtility", "palimpsest"):
            self.assertEqual((apps / (name + ".svg")).resolve(), (apps / "sonata-diskmanager.svg").resolve(), name)
        src = (root / "sonata2" / "diskutil" / "window.py").read_text()
        self.assertIn("Icon=sonata-diskmanager", src)
        self.assertNotIn('icon_name="gnome-disk-utility"', src)
