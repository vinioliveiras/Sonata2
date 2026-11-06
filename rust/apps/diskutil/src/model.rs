//! Disk Utility's model (diskutil/model.py): UDisks2's object tree (the
//! reply of ObjectManager.GetManagedObjects, {path: {interface: {property:
//! value}}}) turned into disks and their volumes, grouped like macOS Disk
//! Utility's sidebar (Internal / External / Disk Images). No GTK, no D-Bus
//! calls: the tests feed it a fake tree.
//!
//! Erase is refused for the running system's volumes (`protected`): the
//! same rules as model.py, checked again on a fresh tree right before
//! erasing (window.rs).

use glib::Variant;
use sonata_i18n::{n_, tr};
use std::collections::{BTreeMap, HashMap, HashSet};

pub const U: &str = "org.freedesktop.UDisks2.";
/// mount points Disk Utility never erases (the running system)
pub const PROTECTED_MOUNTS: [&str; 7] = ["/", "/boot", "/boot/efi", "/efi", "/home", "/usr", "/var"];
/// members of pooled storage UDisks can't map to their mounts: never erased here
pub const POOL_MEMBERS: [&str; 3] = ["LVM2_member", "zfs_member", "bcache"];
pub const EXTERNAL_BUSES: [&str; 3] = ["usb", "ieee1394", "sdio"];
/// unallocated gaps smaller than this don't show on the bar
pub const FREE_MIN: u64 = 16 * 1024 * 1024;
/// Erase: (UDisks type, label in the Format menu, mkfs tool)
pub const FORMATS: [(&str, &str, &str); 5] = [
    ("ext4", "Linux ext4", "mkfs.ext4"),
    ("exfat", "ExFAT", "mkfs.exfat"),
    ("vfat", "MS-DOS (FAT32)", "mkfs.vfat"),
    ("ntfs", n_!("Windows NT File System (NTFS)"), "mkfs.ntfs"),
    ("btrfs", "Btrfs", "mkfs.btrfs"),
];
pub const SCHEMES: [(&str, &str); 2] = [("gpt", n_!("GUID Partition Map")), ("dos", n_!("Master Boot Record"))];
/// colours of the capacity bar's segments, in order (ui tokens)
pub const SEGMENT_COLORS: [&str; 8] = ["sys_blue", "sys_green", "sys_orange", "sys_purple", "sys_teal", "sys_pink", "sys_indigo", "sys_red"];

/// display names of filesystem types (macOS wording where there is one)
pub fn fs_name(t: &str) -> Option<&'static str> {
    Some(match t {
        "ext4" => "Linux ext4",
        "ext3" => "Linux ext3",
        "ext2" => "Linux ext2",
        "vfat" => "MS-DOS (FAT)",
        "exfat" => "ExFAT",
        "ntfs" => n_!("Windows NT File System (NTFS)"),
        "btrfs" => "Btrfs",
        "xfs" => "XFS",
        "f2fs" => "F2FS",
        "crypto_LUKS" => n_!("Encrypted (LUKS)"),
        "swap" => n_!("Linux Swap"),
        "iso9660" => "ISO 9660",
        "udf" => "UDF",
        "hfsplus" => "HFS+",
        "apfs" => "APFS",
        "LVM2_member" => n_!("LVM Physical Volume"),
        "zfs_member" => "ZFS",
        "BitLocker" => n_!("Encrypted (BitLocker)"),
        _ => return None,
    })
}

pub fn table_name(t: &str) -> String {
    match t {
        "gpt" => tr!("GUID Partition Map"),
        "dos" => tr!("Master Boot Record"),
        other => other.to_uppercase(),
    }
}

/// The bus as people know it ("" for none): USB, FireWire, SD Card.
pub fn bus_name(b: &str) -> String {
    match b {
        "usb" => "USB".into(),
        "ieee1394" => "FireWire".into(),
        "sdio" => tr!("SD Card"),
        "" => String::new(),
        other => other.to_uppercase(),
    }
}

/// {path: {interface (short): {property: value}}}, as GetManagedObjects
/// gives it ("org.freedesktop.UDisks2." left off the interface names).
pub type Props = HashMap<String, Variant>;
pub type Ifaces = HashMap<String, Props>;
pub type Tree = BTreeMap<String, Ifaces>;

/// The reply of GetManagedObjects (a{oa{sa{sv}}}) as a Tree.
pub fn tree_of(reply: &Variant) -> Tree {
    let mut out = Tree::new();
    for i in 0..reply.n_children() {
        let entry = reply.child_value(i);
        let path = entry.child_value(0).str().unwrap_or("").to_string();
        let ifs = entry.child_value(1);
        let mut ifaces = Ifaces::new();
        for j in 0..ifs.n_children() {
            let e = ifs.child_value(j);
            let name = e.child_value(0).str().unwrap_or("").to_string();
            let props_v = e.child_value(1);
            let mut props = Props::new();
            for k in 0..props_v.n_children() {
                let p = props_v.child_value(k);
                if let (Some(key), Some(val)) = (p.child_value(0).str().map(String::from), p.child_value(1).as_variant()) {
                    props.insert(key, val);
                }
            }
            ifaces.insert(name.strip_prefix(U).unwrap_or(&name).to_string(), props);
        }
        out.insert(path, ifaces);
    }
    out
}

/// A UDisks byte string ('ay', NUL-terminated) or a string, as text.
pub fn text(v: Option<&Variant>) -> String {
    let Some(v) = v else { return String::new() };
    if let Some(s) = v.str() {
        return s.trim_end_matches('\0').to_string();
    }
    match v.get::<Vec<u8>>() {
        Some(b) => {
            let end = b.iter().rposition(|c| *c != 0).map(|i| i + 1).unwrap_or(0);
            String::from_utf8_lossy(&b[..end]).into_owned()
        }
        None => String::new(),
    }
}

fn s(p: Option<&Props>, k: &str) -> String {
    p.and_then(|p| p.get(k)).map(|v| text(Some(v))).unwrap_or_default()
}

fn n(p: Option<&Props>, k: &str) -> u64 {
    let Some(v) = p.and_then(|p| p.get(k)) else { return 0 };
    v.get::<u64>().or_else(|| v.get::<i64>().map(|x| x.max(0) as u64)).or_else(|| v.get::<u32>().map(u64::from)).or_else(|| v.get::<i32>().map(|x| x.max(0) as u64)).unwrap_or(0)
}

/// Python's truth of a value: true, a number but 0, a non-empty string or list.
fn b(p: Option<&Props>, k: &str) -> bool {
    let Some(v) = p.and_then(|p| p.get(k)) else { return false };
    if let Some(x) = v.get::<bool>() {
        return x;
    }
    if v.is_container() {
        return v.n_children() > 0;
    }
    if let Some(t) = v.str() {
        return !t.is_empty();
    }
    n(p, k) != 0 || v.get::<f64>().is_some_and(|f| f != 0.0)
}

/// An object path that names something ("/" names nothing).
fn obj(p: Option<&Props>, k: &str) -> Option<String> {
    let t = s(p, k);
    (!t.is_empty() && t != "/").then_some(t)
}

#[derive(Clone, Debug)]
pub struct Volume {
    /// the block object
    pub path: String,
    pub device: String,
    pub size: u64,
    pub offset: u64,
    pub number: u64,
    pub part_name: String,
    /// an extended partition
    pub is_container: bool,
    pub usage: String,
    pub id_type: String,
    pub version: String,
    pub encrypted: bool,
    /// the unlocked block's path
    pub cleartext: Option<String>,
    /// where Filesystem calls go
    pub fs_path: String,
    pub has_fs: bool,
    pub fs_type: String,
    pub label: String,
    pub uuid: String,
    pub mounts: Vec<String>,
    pub swap: bool,
    pub hidden: bool,
    pub name: String,
    /// part of the running system's storage (parse)
    pub system_member: bool,
    /// the disk it's on (its whole-disk block path)
    pub disk: String,
}

impl Volume {
    fn new(path: &str, ifaces: &Ifaces, tree: &Tree) -> Self {
        let blk = ifaces.get("Block");
        let p = ifaces.get("Partition");
        let device = Some(s(blk, "PreferredDevice")).filter(|d| !d.is_empty()).unwrap_or_else(|| s(blk, "Device"));
        let size = Some(n(p, "Size")).filter(|x| *x > 0).unwrap_or_else(|| n(blk, "Size"));
        let id_type = s(blk, "IdType");
        let encrypted = ifaces.contains_key("Encrypted");
        let mut cleartext = None;
        let mut fs_ifaces = ifaces;
        if encrypted {
            if let Some(ct) = obj(ifaces.get("Encrypted"), "CleartextDevice").filter(|ct| tree.contains_key(ct)) {
                fs_ifaces = &tree[&ct];
                cleartext = Some(ct);
            }
        }
        let cb = fs_ifaces.get("Block");
        let has_fs = fs_ifaces.contains_key("Filesystem");
        let fs_type = if cleartext.is_some() { s(cb, "IdType") } else { id_type.clone() };
        let label = Some(s(cb, "IdLabel")).filter(|l| !l.is_empty()).unwrap_or_else(|| s(blk, "IdLabel"));
        let uuid = Some(s(cb, "IdUUID")).filter(|l| !l.is_empty()).unwrap_or_else(|| s(blk, "IdUUID"));
        let mounts: Vec<String> = fs_ifaces.get("Filesystem").and_then(|f| f.get("MountPoints")).map(|v| (0..v.n_children()).map(|i| text(Some(&v.child_value(i)))).collect()).unwrap_or_default();
        let swap = ifaces.contains_key("Swapspace") || id_type == "swap";
        let part_name = s(p, "Name");
        let base = device.rsplit('/').next().unwrap_or("").to_string();
        let name = [label.clone(), s(blk, "HintName"), part_name.clone()]
            .into_iter()
            .find(|x| !x.is_empty())
            .or_else(|| swap.then(|| tr!("Swap")))
            .unwrap_or_else(|| if has_fs { tr!("Untitled") } else if base.is_empty() { tr!("Volume") } else { base });
        Self {
            path: path.into(),
            device,
            size,
            offset: n(p, "Offset"),
            number: n(p, "Number"),
            part_name,
            is_container: b(p, "IsContainer"),
            usage: s(blk, "IdUsage"),
            id_type,
            version: s(blk, "IdVersion"),
            encrypted,
            fs_path: cleartext.clone().unwrap_or_else(|| path.to_string()),
            cleartext,
            has_fs,
            fs_type,
            label,
            uuid,
            mounts,
            swap,
            hidden: b(blk, "HintIgnore"),
            name,
            system_member: false,
            disk: String::new(),
        }
    }

    pub fn mounted(&self) -> bool {
        !self.mounts.is_empty()
    }

    pub fn mount_point(&self) -> &str {
        self.mounts.first().map(String::as_str).unwrap_or("")
    }

    pub fn locked(&self) -> bool {
        self.encrypted && self.cleartext.is_none()
    }

    /// The running system's volumes: never erased.
    pub fn protected(&self) -> bool {
        self.swap || self.system_member || self.fs_type == "swap" || POOL_MEMBERS.contains(&self.fs_type.as_str()) || self.mounts.iter().any(|m| PROTECTED_MOUNTS.contains(&m.as_str()))
    }

    pub fn kind(&self) -> String {
        let t = if self.fs_type.is_empty() { &self.id_type } else { &self.fs_type };
        if t == "vfat" && !self.version.is_empty() {
            return format!("MS-DOS ({})", self.version);
        }
        if self.locked() {
            return tr!("Encrypted (Locked)");
        }
        if let Some(n) = fs_name(t) {
            return sonata_i18n::gettext(n);
        }
        if !t.is_empty() {
            t.to_uppercase()
        } else if self.is_container {
            tr!("Extended")
        } else {
            tr!("Unknown")
        }
    }

    /// Shown in the sidebar: holds (or can hold, once unlocked) a filesystem.
    pub fn listed(&self) -> bool {
        !self.hidden && !self.is_container && (self.has_fs || self.encrypted)
    }
}

#[derive(Clone, Debug)]
pub struct Disk {
    /// the Drive object (the block's for disk images)
    pub path: String,
    /// the whole-disk block
    pub block_path: String,
    pub device: String,
    pub size: u64,
    pub vendor: String,
    pub model: String,
    pub bus: String,
    pub removable: bool,
    pub ejectable: bool,
    pub can_power_off: bool,
    pub rotational: bool,
    pub optical: bool,
    pub media: String,
    pub image: String,
    pub is_image: bool,
    pub table: String,
    pub smart: String,
    pub name: String,
    pub volumes: Vec<Volume>,
    /// the whole disk is a system storage member (parse)
    pub system_member: bool,
}

impl Disk {
    fn new(path: &str, block_path: &str, tree: &Tree) -> Self {
        let ifaces = tree.get(path);
        let d = ifaces.and_then(|i| i.get("Drive"));
        let whole = tree.get(block_path);
        let blk = whole.and_then(|i| i.get("Block"));
        let device = Some(s(blk, "PreferredDevice")).filter(|x| !x.is_empty()).unwrap_or_else(|| s(blk, "Device"));
        let loop_ = whole.and_then(|i| i.get("Loop"));
        let image = s(loop_, "BackingFile");
        let (vendor, model) = (s(d, "Vendor").trim().to_string(), s(d, "Model").trim().to_string());
        let base = device.rsplit('/').next().unwrap_or("").to_string();
        let mut name = [vendor.as_str(), model.as_str()].iter().filter(|x| !x.is_empty()).copied().collect::<Vec<_>>().join(" ");
        if loop_.is_some() {
            name = Some(image.rsplit('/').next().unwrap_or("").to_string()).filter(|x| !x.is_empty()).unwrap_or_else(|| base.clone());
        }
        let name = [name, s(blk, "HintName"), base].into_iter().find(|x| !x.is_empty()).unwrap_or_else(|| tr!("Disk"));
        Self {
            path: path.into(),
            block_path: block_path.into(),
            device,
            size: Some(n(d, "Size")).filter(|x| *x > 0).unwrap_or_else(|| n(blk, "Size")),
            vendor,
            model,
            bus: s(d, "ConnectionBus"),
            removable: b(d, "Removable") || b(d, "MediaRemovable"),
            ejectable: b(d, "Ejectable"),
            can_power_off: b(d, "CanPowerOff"),
            rotational: b(d, "RotationRate"),
            optical: b(d, "Optical"),
            media: s(d, "Media"),
            image,
            is_image: loop_.is_some(),
            table: whole.and_then(|i| i.get("PartitionTable")).map(|t| s(Some(t), "Type")).unwrap_or_default(),
            smart: ifaces.map(smart_status).unwrap_or_default().into(),
            name,
            volumes: Vec::new(),
            system_member: false,
        }
    }

    pub fn external(&self) -> bool {
        self.removable || EXTERNAL_BUSES.contains(&self.bus.as_str())
    }

    /// "Internal" / "External" / "Disk Images" (untranslated: shown with gettext)
    pub fn section(&self) -> &'static str {
        if self.is_image {
            n_!("Disk Images")
        } else if self.external() {
            n_!("External")
        } else {
            n_!("Internal")
        }
    }

    pub fn protected(&self) -> bool {
        self.system_member || self.volumes.iter().any(Volume::protected)
    }

    pub fn kind(&self) -> String {
        let bus = bus_name(&self.bus);
        let t = if self.is_image {
            tr!("{bus} Disk Image")
        } else if self.external() {
            tr!("{bus} External Physical Disk")
        } else {
            tr!("{bus} Internal Physical Disk")
        };
        sonata_i18n::fmt(&t, &[("bus", &bus)]).split_whitespace().collect::<Vec<_>>().join(" ")
    }

    pub fn media_kind(&self) -> String {
        if self.is_image {
            tr!("Disk Image")
        } else if self.optical {
            tr!("Optical")
        } else if self.media.starts_with("flash") || self.bus == "sdio" {
            tr!("Flash Storage")
        } else if self.rotational {
            tr!("Rotational")
        } else {
            tr!("Solid State")
        }
    }

    pub fn icon(&self) -> &'static str {
        if self.is_image {
            "media-removable"
        } else if self.optical {
            "drive-optical"
        } else if self.media.starts_with("flash") || self.bus == "sdio" {
            "media-flash"
        } else if self.external() {
            "drive-harddisk-usb"
        } else if self.rotational {
            "drive-harddisk"
        } else {
            "drive-harddisk-solidstate"
        }
    }
}

/// "Verified" / "Failing" (S.M.A.R.T. of ATA and NVMe drives), "" when unknown
/// (untranslated: shown with gettext).
pub fn smart_status(ifaces: &Ifaces) -> &'static str {
    if let Some(ata) = ifaces.get("Drive.Ata").filter(|a| b(Some(a), "SmartSupported")) {
        let enabled = ata.get("SmartEnabled").and_then(|v| v.get::<bool>()).unwrap_or(true);
        if !enabled {
            return n_!("Not Enabled");
        }
        return if b(Some(ata), "SmartFailing") { n_!("Failing") } else { n_!("Verified") };
    }
    if let Some(nv) = ifaces.get("NVMe.Controller").filter(|nv| b(Some(nv), "SmartUpdated")) {
        return if b(Some(nv), "SmartCriticalWarning") { n_!("Failing") } else { n_!("Verified") };
    }
    ""
}

/// A block belonging to the running system's storage even though UDisks
/// reports the mount elsewhere: another device of the same multi-device
/// filesystem (btrfs RAID: same UUID), a member of a running md array that
/// holds it, or a pooled-storage member (LVM, zfs, bcache).
fn system_member(ifaces: Option<&Ifaces>, uuids: &HashSet<String>, arrays: &HashSet<String>) -> bool {
    let blk = ifaces.and_then(|i| i.get("Block"));
    let uuid = s(blk, "IdUUID");
    if !uuid.is_empty() && uuids.contains(&uuid) {
        return true;
    }
    if POOL_MEMBERS.contains(&s(blk, "IdType").as_str()) {
        return true;
    }
    obj(blk, "MDRaidMember").is_some_and(|m| arrays.contains(&m))
}

/// (filesystem UUIDs mounted at a system mount point, md arrays that are
/// running or hold such a mount).
fn system_storage(tree: &Tree) -> (HashSet<String>, HashSet<String>) {
    let (mut uuids, mut arrays) = (HashSet::new(), HashSet::new());
    for ifaces in tree.values() {
        let mounts: Vec<String> = ifaces.get("Filesystem").and_then(|f| f.get("MountPoints")).map(|v| (0..v.n_children()).map(|i| text(Some(&v.child_value(i)))).collect()).unwrap_or_default();
        if !mounts.iter().any(|m| PROTECTED_MOUNTS.contains(&m.as_str())) {
            continue;
        }
        let blk = ifaces.get("Block");
        let uuid = s(blk, "IdUUID");
        if !uuid.is_empty() {
            uuids.insert(uuid);
        }
        if let Some(md) = obj(blk, "MDRaid") {
            arrays.insert(md);
        }
        let backing = obj(blk, "CryptoBackingDevice").and_then(|p| tree.get(&p)).and_then(|i| i.get("Block"));
        if let Some(md) = obj(backing, "MDRaid") {
            arrays.insert(md); // LUKS on md
        }
    }
    for (p, ifaces) in tree {
        if b(ifaces.get("MDRaid"), "Running") {
            arrays.insert(p.clone());
        }
    }
    (uuids, arrays)
}

/// GetManagedObjects' reply -> the disks with their volumes, sorted by
/// section then device. Blocks UDisks marks to ignore and loop devices
/// with no backing file (snaps, zram) are left out.
pub fn parse(tree: &Tree) -> Vec<Disk> {
    let mut disks: BTreeMap<String, Disk> = BTreeMap::new(); // whole-disk block path -> Disk
    for (path, ifaces) in tree {
        let Some(blk) = ifaces.get("Block") else { continue };
        if ifaces.contains_key("Partition") || obj(Some(blk), "CryptoBackingDevice").is_some() {
            continue;
        }
        match obj(Some(blk), "Drive").filter(|d| tree.contains_key(d)) {
            Some(drive) => {
                disks.insert(path.clone(), Disk::new(&drive, path, tree));
            }
            None => {
                if ifaces.get("Loop").is_some_and(|l| !s(Some(l), "BackingFile").is_empty()) && !b(Some(blk), "HintIgnore") {
                    disks.insert(path.clone(), Disk::new(path, path, tree));
                }
            }
        }
    }
    for (path, ifaces) in tree {
        let Some(p) = ifaces.get("Partition") else { continue };
        if !ifaces.contains_key("Block") {
            continue;
        }
        if let Some(disk) = disks.get_mut(&s(Some(p), "Table")) {
            let mut v = Volume::new(path, ifaces, tree);
            v.disk = disk.block_path.clone();
            disk.volumes.push(v);
        }
    }
    let (uuids, arrays) = system_storage(tree);
    for disk in disks.values_mut() {
        disk.volumes.sort_by_key(|v| (v.offset, v.number));
        let whole = tree.get(&disk.block_path);
        if disk.volumes.is_empty() && whole.is_some_and(|w| w.contains_key("Filesystem") || w.contains_key("Encrypted")) {
            // unpartitioned: the disk is the volume
            let mut v = Volume::new(&disk.block_path, whole.expect("checked"), tree);
            v.disk = disk.block_path.clone();
            disk.volumes.push(v);
        }
        disk.system_member = system_member(whole, &uuids, &arrays); // e.g. a whole-disk RAID member
        for v in disk.volumes.iter_mut() {
            v.system_member = system_member(tree.get(&v.path), &uuids, &arrays) || v.cleartext.as_ref().is_some_and(|ct| system_member(tree.get(ct), &uuids, &arrays));
        }
    }
    let order = |s: &str| match s {
        "Internal" => 0,
        "External" => 1,
        _ => 2,
    };
    let mut out: Vec<Disk> = disks.into_values().collect();
    out.sort_by(|a, b| (order(a.section()), &a.device).cmp(&(order(b.section()), &b.device)));
    out
}

/// [(section title, disks)] in sidebar order, empty sections left out.
pub fn grouped(disks: &[Disk]) -> Vec<(&'static str, Vec<&Disk>)> {
    ["Internal", "External", "Disk Images"]
        .into_iter()
        .filter_map(|title| {
            let ds: Vec<&Disk> = disks.iter().filter(|d| d.section() == title).collect();
            (!ds.is_empty()).then_some((title, ds))
        })
        .collect()
}

/// The capacity bar: [(name, bytes, colour token)]. A disk: its partitions
/// in order (unallocated gaps as "Free Space"). A volume: Used / Free when
/// mounted (usage = (used, free)), else the whole volume in one segment.
pub fn segments(disk: &Disk, volume: Option<&Volume>, usage: Option<(u64, u64)>) -> Vec<(String, u64, &'static str)> {
    if let Some(v) = volume {
        let colour = disk.volumes.iter().position(|x| x.path == v.path).map(|i| SEGMENT_COLORS[i % SEGMENT_COLORS.len()]).unwrap_or(SEGMENT_COLORS[0]);
        if let Some((used, free)) = usage {
            return vec![(tr!("Used"), used, colour), (tr!("Free"), free, "free")];
        }
        return vec![(v.name.clone(), v.size, colour)];
    }
    let (mut out, mut pos, mut i) = (Vec::new(), 0u64, 0usize);
    for v in &disk.volumes {
        if v.is_container {
            continue; // logical partitions sit inside it
        }
        if v.offset > 0 && v.offset.saturating_sub(pos) >= FREE_MIN {
            out.push((tr!("Free Space"), v.offset - pos, "free"));
        }
        let colour = if v.swap || !v.listed() { "sys_gray" } else { SEGMENT_COLORS[i % SEGMENT_COLORS.len()] };
        out.push((v.name.clone(), v.size, colour));
        if v.listed() && !v.swap {
            i += 1;
        }
        pos = pos.max(if v.offset > 0 { v.offset } else { pos } + v.size);
    }
    if disk.size.saturating_sub(pos) >= FREE_MIN {
        out.push((tr!("Free Space"), disk.size - pos, "free"));
    }
    out
}

/// [(type, label)] the Erase sheet offers: the types whose mkfs is
/// installed (`can(type)` overrides: UDisks' CanFormat). NTFS only when
/// it can be made; never an empty list.
pub fn formats(can: &dyn Fn(&str) -> bool) -> Vec<(&'static str, String)> {
    let out: Vec<(&str, String)> = FORMATS.iter().filter(|(t, _, _)| *t != "ntfs" || can(t)).map(|(t, l, _)| (*t, sonata_i18n::gettext(l))).collect();
    if out.is_empty() { FORMATS.iter().filter(|(t, _, _)| *t != "ntfs").map(|(t, l, _)| (*t, sonata_i18n::gettext(l))).collect() } else { out }
}

/// mkfs for `t` is installed.
pub fn mkfs_installed(t: &str) -> bool {
    FORMATS.iter().find(|f| f.0 == t).is_some_and(|f| sonata_backend::run::which(f.2).is_some())
}

#[cfg(test)]
pub mod tests;

/// The tests' fake UDisks tree (the window's tests use it too).
#[cfg(test)]
pub fn tests_tree() -> Tree {
    tests::fake_tree()
}
