"""Disk Utility's model: UDisks2's object tree (the unpacked reply of
ObjectManager.GetManagedObjects, {path: {interface: {property: value}}})
turned into disks and their volumes, grouped like macOS Disk Utility's
sidebar (Internal / External / Disk Images).

Pure Python, no GTK or D-Bus: tests feed it a fake tree."""
import shutil

U = "org.freedesktop.UDisks2."
DRIVE, ATA, NVME = U + "Drive", U + "Drive.Ata", U + "NVMe.Controller"
BLOCK, PART, TABLE = U + "Block", U + "Partition", U + "PartitionTable"
FS, CRYPT, LOOP, SWAP = U + "Filesystem", U + "Encrypted", U + "Loop", U + "Swapspace"

# mount points Disk Utility never erases (the running system)
PROTECTED_MOUNTS = ("/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var")
EXTERNAL_BUSES = ("usb", "ieee1394", "sdio")
FREE_MIN = 16 * 1024 * 1024            # unallocated gaps smaller than this don't show on the bar

# display names of filesystem types (macOS wording where there is one)
FS_NAMES = {"ext4": "Linux ext4", "ext3": "Linux ext3", "ext2": "Linux ext2", "vfat": "MS-DOS (FAT)",
            "exfat": "ExFAT", "ntfs": "Windows NT File System (NTFS)", "btrfs": "Btrfs", "xfs": "XFS",
            "f2fs": "F2FS", "crypto_LUKS": "Encrypted (LUKS)", "swap": "Linux Swap",
            "iso9660": "ISO 9660", "udf": "UDF", "hfsplus": "Mac OS Extended", "apfs": "APFS",
            "LVM2_member": "LVM Physical Volume", "zfs_member": "ZFS", "BitLocker": "Encrypted (BitLocker)"}
TABLE_NAMES = {"gpt": "GUID Partition Map", "dos": "Master Boot Record"}
BUS_NAMES = {"usb": "USB", "ieee1394": "FireWire", "sdio": "SD Card", "": ""}
# Erase: (UDisks type, label in the Format menu, mkfs tool)
FORMATS = (("ext4", "Linux ext4", "mkfs.ext4"), ("exfat", "ExFAT", "mkfs.exfat"),
           ("vfat", "MS-DOS (FAT32)", "mkfs.vfat"), ("ntfs", "Windows NT File System (NTFS)", "mkfs.ntfs"),
           ("btrfs", "Btrfs", "mkfs.btrfs"))
SCHEMES = (("gpt", "GUID Partition Map"), ("dos", "Master Boot Record"))
# colours of the capacity bar's segments, in order (ui tokens)
SEGMENT_COLORS = ("sys_blue", "sys_green", "sys_orange", "sys_purple", "sys_teal", "sys_pink", "sys_indigo",
                  "sys_red")


def text(v) -> str:
    """A UDisks byte string ('ay': bytes or a list of ints, NUL-terminated) as str."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v.rstrip("\0")
    return bytes(v).rstrip(b"\0").decode("utf-8", "replace")


class Volume:
    """A partition (or a whole unpartitioned disk) and what it holds."""

    def __init__(self, path: str, ifaces: dict, objects: dict):
        b = ifaces.get(BLOCK, {})
        p = ifaces.get(PART, {})
        self.path = path                         # the block object
        self.device = text(b.get("PreferredDevice")) or text(b.get("Device"))
        self.size = int(p.get("Size") or b.get("Size") or 0)
        self.offset = int(p.get("Offset") or 0)
        self.number = int(p.get("Number") or 0)
        self.part_name = p.get("Name", "")
        self.is_container = bool(p.get("IsContainer"))      # extended partition
        self.usage, self.type = b.get("IdUsage", ""), b.get("IdType", "")
        self.version = b.get("IdVersion", "")
        self.encrypted = CRYPT in ifaces
        self.cleartext = None                    # the unlocked block's path
        fs_ifaces = ifaces
        if self.encrypted:
            ct = ifaces[CRYPT].get("CleartextDevice", "/")
            if ct and ct != "/" and ct in objects:
                self.cleartext = ct
                fs_ifaces = objects[ct]
        cb = fs_ifaces.get(BLOCK, {})
        self.fs_path = self.cleartext or path    # where Filesystem calls go
        self.has_fs = FS in fs_ifaces
        self.fs_type = cb.get("IdType", "") if self.cleartext else self.type
        self.label = cb.get("IdLabel", "") or b.get("IdLabel", "")
        self.uuid = cb.get("IdUUID", "") or b.get("IdUUID", "")
        self.mounts = [text(m) for m in fs_ifaces.get(FS, {}).get("MountPoints", [])]
        self.swap = SWAP in ifaces or self.type == "swap"
        self.hidden = bool(b.get("HintIgnore"))
        self.name = self.label or b.get("HintName", "") or self.part_name or ("Swap" if self.swap else "") or \
            ("Untitled" if self.has_fs else (self.device.rsplit("/", 1)[-1] or "Volume"))
        self.disk = None

    @property
    def mounted(self) -> bool:
        return bool(self.mounts)

    @property
    def mount_point(self) -> str:
        return self.mounts[0] if self.mounts else ""

    @property
    def locked(self) -> bool:
        return self.encrypted and self.cleartext is None

    @property
    def protected(self) -> bool:
        """The running system's volumes: never erased."""
        return self.swap or self.fs_type in ("swap", "LVM2_member") or \
            any(m in PROTECTED_MOUNTS for m in self.mounts)

    @property
    def kind(self) -> str:
        t = self.fs_type or self.type
        if t == "vfat" and self.version:
            return f"MS-DOS ({self.version})"
        if self.locked:
            return "Encrypted (Locked)"
        return FS_NAMES.get(t, t.upper() if t else ("Extended" if self.is_container else "Unknown"))

    @property
    def listed(self) -> bool:
        """Shown in the sidebar: holds (or can hold, once unlocked) a filesystem."""
        return not self.hidden and not self.is_container and (self.has_fs or self.encrypted)


class Disk:
    """A physical drive (or a loop-mounted disk image) with its volumes."""

    def __init__(self, path: str, block_path: str, objects: dict):
        self.path = path                         # the Drive object (the block's for disk images)
        self.block_path = block_path             # the whole-disk block
        ifaces = objects.get(path, {})
        d = ifaces.get(DRIVE, {})
        b = objects.get(block_path, {}).get(BLOCK, {})
        self.device = text(b.get("PreferredDevice")) or text(b.get("Device"))
        self.size = int(d.get("Size") or b.get("Size") or 0)
        self.vendor, self.model = d.get("Vendor", "").strip(), d.get("Model", "").strip()
        self.bus = d.get("ConnectionBus", "")
        self.removable = bool(d.get("Removable") or d.get("MediaRemovable"))
        self.ejectable = bool(d.get("Ejectable"))
        self.can_power_off = bool(d.get("CanPowerOff"))
        self.rotational = bool(d.get("RotationRate", 0))
        self.optical = bool(d.get("Optical"))
        self.media = d.get("Media", "")
        loop = objects.get(block_path, {}).get(LOOP)
        self.image = text(loop.get("BackingFile")) if loop else ""
        self.is_image = bool(loop)
        table = objects.get(block_path, {}).get(TABLE)
        self.table = table.get("Type", "") if table else ""
        self.smart = smart_status(ifaces)
        name = " ".join(x for x in (self.vendor, self.model) if x)
        if self.is_image:
            name = self.image.rsplit("/", 1)[-1] or self.device.rsplit("/", 1)[-1]
        self.name = name or b.get("HintName", "") or self.device.rsplit("/", 1)[-1] or "Disk"
        self.volumes = []

    @property
    def external(self) -> bool:
        return self.removable or self.bus in EXTERNAL_BUSES

    @property
    def section(self) -> str:
        return "Disk Images" if self.is_image else ("External" if self.external else "Internal")

    @property
    def protected(self) -> bool:
        return any(v.protected for v in self.volumes)

    @property
    def kind(self) -> str:
        where = "Disk Image" if self.is_image else ("External" if self.external else "Internal")
        bus = BUS_NAMES.get(self.bus, self.bus.upper())
        return " ".join(x for x in (bus, where, "Physical Disk" if not self.is_image else "") if x)

    @property
    def media_kind(self) -> str:
        if self.is_image:
            return "Disk Image"
        if self.optical:
            return "Optical"
        if self.media.startswith("flash") or self.bus == "sdio":
            return "Flash Storage"
        return "Rotational" if self.rotational else "Solid State"

    @property
    def icon(self) -> str:
        if self.is_image:
            return "media-removable"
        if self.optical:
            return "drive-optical"
        if self.media.startswith("flash") or self.bus == "sdio":
            return "media-flash"
        if self.external:
            return "drive-harddisk-usb"
        return "drive-harddisk" if self.rotational else "drive-harddisk-solidstate"


def smart_status(ifaces: dict) -> str:
    """ "Verified" / "Failing" (S.M.A.R.T. of ATA and NVMe drives), "" when unknown."""
    ata = ifaces.get(ATA)
    if ata and ata.get("SmartSupported"):
        if not ata.get("SmartEnabled", True):
            return "Not Enabled"
        return "Failing" if ata.get("SmartFailing") else "Verified"
    nvme = ifaces.get(NVME)
    if nvme and nvme.get("SmartUpdated"):
        return "Failing" if nvme.get("SmartCriticalWarning") else "Verified"
    return ""


def parse(objects: dict) -> list:
    """GetManagedObjects' reply -> [Disk] with their volumes, sorted by
    section then name. Blocks UDisks marks to ignore and loop devices with
    no backing file (snaps, zram) are left out."""
    disks = {}                                  # whole-disk block path -> Disk
    for path, ifaces in objects.items():
        b = ifaces.get(BLOCK)
        if b is None or PART in ifaces or b.get("CryptoBackingDevice", "/") != "/":
            continue
        drive = b.get("Drive", "/")
        if drive and drive != "/" and drive in objects:
            disks[path] = Disk(drive, path, objects)
        elif LOOP in ifaces and text(ifaces[LOOP].get("BackingFile")) and not b.get("HintIgnore"):
            disks[path] = Disk(path, path, objects)
    for path, ifaces in objects.items():
        p = ifaces.get(PART)
        if p is None or ifaces.get(BLOCK) is None:
            continue
        disk = disks.get(p.get("Table"))
        if disk is not None:
            v = Volume(path, ifaces, objects)
            v.disk = disk
            disk.volumes.append(v)
    for disk in disks.values():
        disk.volumes.sort(key=lambda v: (v.offset, v.number))
        whole = objects.get(disk.block_path, {})
        if not disk.volumes and (FS in whole or CRYPT in whole):     # unpartitioned: the disk is the volume
            v = Volume(disk.block_path, whole, objects)
            v.disk = disk
            disk.volumes.append(v)
    order = {"Internal": 0, "External": 1, "Disk Images": 2}
    return sorted(disks.values(), key=lambda d: (order[d.section], d.device))


def grouped(disks: list) -> list:
    """[(section title, [Disk])] in sidebar order, empty sections left out."""
    out = []
    for title in ("Internal", "External", "Disk Images"):
        ds = [d for d in disks if d.section == title]
        if ds:
            out.append((title, ds))
    return out


def segments(disk: Disk, volume: Volume = None, usage=None) -> list:
    """The capacity bar: [(name, bytes, colour token)].

    A disk: its partitions in order (unallocated gaps as "Free Space").
    A volume: Used / Free when mounted (usage = (used, free) from statvfs),
    else the whole volume in one segment."""
    if volume is not None:
        colour = SEGMENT_COLORS[disk.volumes.index(volume) % len(SEGMENT_COLORS)] \
            if volume in disk.volumes else SEGMENT_COLORS[0]
        if usage:
            used, free = usage
            return [("Used", used, colour), ("Free", free, "free")]
        return [(volume.name, volume.size, colour)]
    out, pos, i = [], 0, 0
    for v in disk.volumes:
        if v.is_container:                        # logical partitions sit inside it
            continue
        if v.offset and v.offset - pos >= FREE_MIN:
            out.append(("Free Space", v.offset - pos, "free"))
        out.append((v.name, v.size, "sys_gray" if v.swap or not v.listed else
                    SEGMENT_COLORS[i % len(SEGMENT_COLORS)]))
        if v.listed and not v.swap:
            i += 1
        pos = max(pos, (v.offset or pos) + v.size)
    if disk.size - pos >= FREE_MIN:
        out.append(("Free Space", disk.size - pos, "free"))
    return out


def formats(can_format=None) -> list:
    """[(type, label)] the Erase sheet offers: the types whose mkfs is
    installed (can_format(type) -> bool overrides, e.g. UDisks' CanFormat)."""
    ok = can_format or (lambda t: shutil.which(dict((f, m) for f, _l, m in FORMATS)[t]) is not None)
    out = [(t, label) for t, label, _m in FORMATS if t != "ntfs" or ok(t)]
    return out or [(t, label) for t, label, _m in FORMATS if t != "ntfs"]
