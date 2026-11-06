//! tests/test_diskutil.py's ModelTest, on the same fake UDisks tree.

use super::*;
use glib::prelude::*;

const OBJ: &str = "/org/freedesktop/UDisks2/";
const GB: u64 = 1000 * 1000 * 1000;
const MB: u64 = 1000 * 1000;

fn ay(s: &str) -> Variant {
    let mut b = s.as_bytes().to_vec();
    b.push(0);
    b.to_variant()
}

fn o(p: &str) -> Variant {
    glib::variant::ObjectPath::try_from(p.to_string()).unwrap().to_variant()
}

fn props(list: Vec<(&str, Variant)>) -> Props {
    list.into_iter().map(|(k, v)| (k.to_string(), v)).collect()
}

#[derive(Default)]
struct B<'a> {
    usage: &'a str,
    fs: &'a str,
    label: &'a str,
    uuid: &'a str,
    version: &'a str,
    ignore: bool,
    backing: &'a str,
}

fn block(dev: &str, size: u64, drive: &str, x: B) -> (String, Props) {
    (
        "Block".into(),
        props(vec![
            ("Device", ay(dev)),
            ("PreferredDevice", ay(dev)),
            ("Size", size.to_variant()),
            ("Drive", o(drive)),
            ("IdUsage", x.usage.to_variant()),
            ("IdType", x.fs.to_variant()),
            ("IdLabel", x.label.to_variant()),
            ("IdUUID", x.uuid.to_variant()),
            ("IdVersion", x.version.to_variant()),
            ("HintIgnore", x.ignore.to_variant()),
            ("CryptoBackingDevice", o(if x.backing.is_empty() { "/" } else { x.backing })),
        ]),
    )
}

fn part(table: &str, number: u32, offset: u64, size: u64, name: &str) -> (String, Props) {
    ("Partition".into(), props(vec![("Table", o(table)), ("Number", number.to_variant()), ("Offset", offset.to_variant()), ("Size", size.to_variant()), ("Name", name.to_variant()), ("IsContainer", false.to_variant())]))
}

fn fs(mounts: &[&str]) -> (String, Props) {
    let list: Vec<Variant> = mounts.iter().map(|m| ay(m)).collect();
    ("Filesystem".into(), props(vec![("MountPoints", Variant::array_from_iter_with_type(glib::VariantTy::new("ay").unwrap(), list))]))
}

fn iface(name: &str, list: Vec<(&str, Variant)>) -> (String, Props) {
    (name.into(), props(list))
}

fn obj_of(parts: Vec<(String, Props)>) -> Ifaces {
    parts.into_iter().collect()
}

pub fn fake_tree() -> Tree {
    let d = |n: &str| format!("{OBJ}drives/{n}");
    let bd = |n: &str| format!("{OBJ}block_devices/{n}");
    let (nvme, hdd, usb, sd) = (d("Samsung_SSD_980"), d("WDC_WD20"), d("SanDisk"), d("SD"));
    let mut t = Tree::new();
    t.insert(nvme.clone(), obj_of(vec![
        iface("Drive", vec![("Vendor", "".to_variant()), ("Model", "Samsung SSD 980 1TB".to_variant()), ("Size", (1000 * GB).to_variant()), ("ConnectionBus", "".to_variant()), ("Removable", false.to_variant()), ("MediaRemovable", false.to_variant()), ("RotationRate", 0i32.to_variant())]),
        iface("NVMe.Controller", vec![("SmartUpdated", 1700000000u64.to_variant()), ("SmartCriticalWarning", Vec::<String>::new().to_variant())]),
    ]));
    t.insert(hdd.clone(), obj_of(vec![
        iface("Drive", vec![("Vendor", "WDC".to_variant()), ("Model", "WD20EZRZ".to_variant()), ("Size", (2000 * GB).to_variant()), ("RotationRate", 5400i32.to_variant())]),
        iface("Drive.Ata", vec![("SmartSupported", true.to_variant()), ("SmartEnabled", true.to_variant()), ("SmartFailing", false.to_variant())]),
    ]));
    t.insert(usb.clone(), obj_of(vec![iface("Drive", vec![("Vendor", "SanDisk".to_variant()), ("Model", "Ultra".to_variant()), ("Size", (64 * GB).to_variant()), ("ConnectionBus", "usb".to_variant()), ("Removable", true.to_variant()), ("Ejectable", true.to_variant()), ("CanPowerOff", true.to_variant())])]));
    t.insert(sd.clone(), obj_of(vec![iface("Drive", vec![("Vendor", "".to_variant()), ("Model", "SD Card Reader".to_variant()), ("Size", (32 * GB).to_variant()), ("ConnectionBus", "sdio".to_variant()), ("MediaRemovable", true.to_variant()), ("Media", "flash_sd".to_variant())])]));
    // system disk
    let n0 = bd("nvme0n1");
    t.insert(n0.clone(), obj_of(vec![block("/dev/nvme0n1", 1000 * GB, &nvme, B::default()), iface("PartitionTable", vec![("Type", "gpt".to_variant())])]));
    t.insert(bd("nvme0n1p1"), obj_of(vec![block("/dev/nvme0n1p1", 512 * MB, &nvme, B { usage: "filesystem", fs: "vfat", uuid: "AB12-CD34", version: "FAT32", ..Default::default() }), part(&n0, 1, MB, 512 * MB, "EFI System Partition"), fs(&["/boot/efi"])]));
    t.insert(bd("nvme0n1p2"), obj_of(vec![block("/dev/nvme0n1p2", 200 * GB, &nvme, B { usage: "filesystem", fs: "ext4", label: "CachyOS", uuid: "u-root", ..Default::default() }), part(&n0, 2, 513 * MB, 200 * GB, ""), fs(&["/"])]));
    t.insert(bd("nvme0n1p3"), obj_of(vec![block("/dev/nvme0n1p3", 16 * GB, &nvme, B { usage: "other", fs: "swap", ..Default::default() }), part(&n0, 3, 513 * MB + 200 * GB, 16 * GB, ""), iface("Swapspace", vec![("Active", true.to_variant())])]));
    t.insert(bd("nvme0n1p4"), obj_of(vec![block("/dev/nvme0n1p4", 600 * GB, &nvme, B { usage: "crypto", fs: "crypto_LUKS", uuid: "u-luks", ..Default::default() }), part(&n0, 4, 513 * MB + 216 * GB, 600 * GB, ""), iface("Encrypted", vec![("CleartextDevice", o(&bd("dm_2d0")))])]));
    t.insert(bd("dm_2d0"), obj_of(vec![block("/dev/dm-0", 600 * GB, "/", B { usage: "filesystem", fs: "ext4", label: "home", uuid: "u-home", backing: &bd("nvme0n1p4"), ..Default::default() }), fs(&["/home"])]));
    // data disk
    let sda = bd("sda");
    t.insert(sda.clone(), obj_of(vec![block("/dev/sda", 2000 * GB, &hdd, B::default()), iface("PartitionTable", vec![("Type", "gpt".to_variant())])]));
    t.insert(bd("sda1"), obj_of(vec![block("/dev/sda1", 2000 * GB - 2 * MB, &hdd, B { usage: "filesystem", fs: "ntfs", label: "Games", uuid: "u-games", ..Default::default() }), part(&sda, 1, MB, 2000 * GB - 2 * MB, ""), fs(&[])]));
    // USB stick
    let sdb = bd("sdb");
    t.insert(sdb.clone(), obj_of(vec![block("/dev/sdb", 64 * GB, &usb, B::default()), iface("PartitionTable", vec![("Type", "dos".to_variant())])]));
    t.insert(bd("sdb1"), obj_of(vec![block("/dev/sdb1", 48 * GB, &usb, B { usage: "filesystem", fs: "exfat", label: "BACKUP", uuid: "u-backup", ..Default::default() }), part(&sdb, 1, MB, 48 * GB, ""), fs(&["/run/media/vini/BACKUP"])]));
    t.insert(bd("sdb2"), obj_of(vec![block("/dev/sdb2", 16 * GB - MB, &usb, B { usage: "crypto", fs: "crypto_LUKS", uuid: "u-vault", ..Default::default() }), part(&sdb, 2, MB + 48 * GB, 16 * GB - MB, "Vault"), iface("Encrypted", vec![("CleartextDevice", o("/"))])]));
    // SD card, no partition table
    t.insert(bd("mmcblk0"), obj_of(vec![block("/dev/mmcblk0", 32 * GB, &sd, B { usage: "filesystem", fs: "vfat", label: "CAMERA", uuid: "u-cam", version: "FAT32", ..Default::default() }), fs(&[])]));
    // a disk image, a snap loop (ignored) and an empty loop
    t.insert(bd("loop0"), obj_of(vec![block("/dev/loop0", 5 * GB, "/", B { usage: "filesystem", fs: "iso9660", label: "Ubuntu 24.04", uuid: "u-iso", ..Default::default() }), iface("Loop", vec![("BackingFile", ay("/home/vini/Downloads/ubuntu.iso"))]), fs(&["/run/media/vini/Ubuntu 24.04"])]));
    t.insert(bd("loop1"), obj_of(vec![block("/dev/loop1", 50 * MB, "/", B { usage: "filesystem", fs: "squashfs", ignore: true, ..Default::default() }), iface("Loop", vec![("BackingFile", ay("/var/lib/snapd/snaps/core.snap"))]), fs(&["/snap/core/1"])]));
    t.insert(bd("loop2"), obj_of(vec![block("/dev/loop2", 0, "/", B::default()), iface("Loop", vec![("BackingFile", Vec::<u8>::new().to_variant())])]));
    t.insert(format!("{OBJ}Manager"), obj_of(vec![iface("Manager", vec![("Version", "2.10.1".to_variant())])]));
    t
}

fn disks() -> Vec<Disk> {
    sonata_i18n::use_language("en");
    parse(&fake_tree())
}

fn by_dev<'a>(ds: &'a [Disk], dev: &str) -> &'a Disk {
    ds.iter().find(|d| d.device == dev).unwrap()
}

#[test]
fn drives_and_volumes() {
    let ds = disks();
    let mut devs: Vec<&str> = ds.iter().map(|d| d.device.as_str()).collect();
    devs.sort();
    assert_eq!(devs, ["/dev/loop0", "/dev/mmcblk0", "/dev/nvme0n1", "/dev/sda", "/dev/sdb"]);
    let nvme = by_dev(&ds, "/dev/nvme0n1");
    assert_eq!(nvme.name, "Samsung SSD 980 1TB");
    assert_eq!(nvme.volumes.iter().map(|v| v.device.as_str()).collect::<Vec<_>>(), ["/dev/nvme0n1p1", "/dev/nvme0n1p2", "/dev/nvme0n1p3", "/dev/nvme0n1p4"]);
    let (efi, root, swap, home) = (&nvme.volumes[0], &nvme.volumes[1], &nvme.volumes[2], &nvme.volumes[3]);
    assert_eq!(efi.kind(), "MS-DOS (FAT32)");
    assert_eq!(root.name, "CachyOS");
    assert_eq!(root.mount_point(), "/");
    assert!(!swap.listed()); // swap: on the bar, not in the sidebar
    assert!(home.encrypted && !home.locked());
    assert_eq!((home.fs_path.as_str(), home.fs_type.as_str(), home.mount_point(), home.name.as_str()), (format!("{OBJ}block_devices/dm_2d0").as_str(), "ext4", "/home", "home"));
    assert_eq!(nvme.table, "gpt");
    assert_eq!(nvme.smart, "Verified");
    assert_eq!(by_dev(&ds, "/dev/sda").smart, "Verified");
    assert_eq!(by_dev(&ds, "/dev/sda").icon(), "drive-harddisk");
    assert_eq!(nvme.icon(), "drive-harddisk-solidstate");
    let vault = &by_dev(&ds, "/dev/sdb").volumes[1];
    assert!(vault.locked() && vault.listed());
    assert_eq!(vault.name, "Vault");
    assert_eq!(vault.kind(), "Encrypted (Locked)");
    let sd = by_dev(&ds, "/dev/mmcblk0"); // unpartitioned: the disk is its own volume
    assert_eq!(sd.volumes.iter().map(|v| v.name.as_str()).collect::<Vec<_>>(), ["CAMERA"]);
    assert_eq!(sd.icon(), "media-flash");
    assert_eq!(by_dev(&ds, "/dev/loop0").name, "ubuntu.iso");
}

#[test]
fn byte_strings() {
    assert_eq!(text(Some(&ay("/dev/sda"))), "/dev/sda");
    assert_eq!(text(Some(&b"/mnt\0".to_vec().to_variant())), "/mnt");
    assert_eq!(text(Some(&"/mnt".to_variant())), "/mnt");
    assert_eq!(text(None), "");
}

#[test]
fn grouping() {
    let ds = disks();
    let g = grouped(&ds);
    assert_eq!(g.iter().map(|x| x.0).collect::<Vec<_>>(), ["Internal", "External", "Disk Images"]);
    let devs = |i: usize| g[i].1.iter().map(|d| d.device.clone()).collect::<Vec<_>>();
    assert_eq!(devs(0), ["/dev/nvme0n1", "/dev/sda"]);
    let mut ext = devs(1);
    ext.sort();
    assert_eq!(ext, ["/dev/mmcblk0", "/dev/sdb"]);
    assert_eq!(devs(2), ["/dev/loop0"]);
}

#[test]
fn the_running_system_is_protected() {
    let ds = disks();
    let nvme = by_dev(&ds, "/dev/nvme0n1");
    assert!(nvme.volumes.iter().all(Volume::protected)); // /boot/efi, /, swap, /home (unlocked LUKS)
    assert!(nvme.protected());
    assert!(!by_dev(&ds, "/dev/sda").protected());
    assert!(!by_dev(&ds, "/dev/sdb").protected());
    assert!(!by_dev(&ds, "/dev/sdb").volumes.iter().any(Volume::protected));
}

#[test]
fn members_of_the_systems_storage_are_protected() {
    // a second device of the root's btrfs RAID (same UUID), an LVM member, a running md array's member
    let mut t = fake_tree();
    let sda1 = format!("{OBJ}block_devices/sda1");
    t.get_mut(&sda1).unwrap().get_mut("Block").unwrap().insert("IdUUID".into(), "u-root".to_variant());
    assert!(by_dev(&parse(&t), "/dev/sda").protected());
    let mut t = fake_tree();
    t.get_mut(&sda1).unwrap().get_mut("Block").unwrap().insert("IdType".into(), "LVM2_member".to_variant());
    assert!(by_dev(&parse(&t), "/dev/sda").protected());
    let mut t = fake_tree();
    let md = format!("{OBJ}mdraid/md0");
    t.insert(md.clone(), obj_of(vec![iface("MDRaid", vec![("Running", true.to_variant())])]));
    t.get_mut(&sda1).unwrap().get_mut("Block").unwrap().insert("MDRaidMember".into(), o(&md));
    assert!(by_dev(&parse(&t), "/dev/sda").protected());
}

#[test]
fn capacity_segments() {
    let ds = disks();
    let nvme = by_dev(&ds, "/dev/nvme0n1");
    let segs = segments(nvme, None, None);
    assert_eq!(segs.iter().map(|s| s.0.as_str()).collect::<Vec<_>>(), ["EFI System Partition", "CachyOS", "Swap", "home", "Free Space"]);
    assert_eq!(segs[2].2, "sys_gray"); // swap in grey
    assert_eq!(segs.last().unwrap().2, "free");
    assert_eq!(segs.last().unwrap().1, 1000 * GB - (513 * MB + 816 * GB));
    assert_eq!(segs.iter().map(|s| s.1).sum::<u64>(), 1000 * GB - MB); // the 1 MB gap before p1 is too small
    let root = &nvme.volumes[1];
    let colour = SEGMENT_COLORS[1];
    assert_eq!(segments(nvme, Some(root), Some((50 * GB, 150 * GB))), [("Used".to_string(), 50 * GB, colour), ("Free".to_string(), 150 * GB, "free")]);
    assert_eq!(segments(nvme, Some(root), None), [("CachyOS".to_string(), 200 * GB, colour)]);
    let sd = by_dev(&ds, "/dev/mmcblk0");
    assert_eq!(segments(sd, None, None).iter().map(|s| s.0.as_str()).collect::<Vec<_>>(), ["CAMERA"]);
}

#[test]
fn erase_formats() {
    assert!(formats(&|_| true).iter().any(|f| f.0 == "ntfs"));
    assert!(!formats(&|t| t != "ntfs").iter().any(|f| f.0 == "ntfs"));
    assert_eq!(formats(&|_| false)[0].0, "ext4");
}

#[test]
fn the_tree_from_dbus() {
    // a{oa{sa{sv}}}: interface names lose their prefix
    let inner: Vec<(String, Vec<(String, Variant)>)> = vec![(format!("{U}Block"), vec![("Size".into(), 5u64.to_variant())])];
    let reply = vec![(glib::variant::ObjectPath::try_from(format!("{OBJ}block_devices/x")).unwrap(), inner.into_iter().map(|(k, v)| (k, v.into_iter().collect::<HashMap<String, Variant>>())).collect::<HashMap<_, _>>())].into_iter().collect::<HashMap<_, _>>().to_variant();
    let t = tree_of(&reply);
    let b = &t[&format!("{OBJ}block_devices/x")]["Block"];
    assert_eq!(b["Size"].get::<u64>(), Some(5));
}
