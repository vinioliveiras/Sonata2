//! The icon inside a Windows program (files/peicon.py; .exe, .dll): Files
//! shows it instead of the generic one, like Explorer does. A PE file
//! keeps its icons as resources: RT_GROUP_ICON lists the sizes of an icon,
//! each one an RT_ICON (a PNG, or a DIB as in an .ico file). Only the
//! headers and the resource section are read (seek, never the whole file:
//! a game's .exe can be gigabytes).

use gtk::gdk_pixbuf::{prelude::*, InterpType, Pixbuf, PixbufLoader};
use std::collections::HashMap;
use std::io::{Read, Seek, SeekFrom};

const RT_ICON: u32 = 3;
const RT_GROUP_ICON: u32 = 14;
pub const CONTENT_TYPES: [&str; 5] = ["application/x-ms-dos-executable", "application/x-msdownload", "application/x-dosexec", "application/vnd.microsoft.portable-executable", "application/x-ms-ne-executable"];
const MAX_RSRC: u64 = 1 << 31;
const PNG_MAGIC: &[u8] = b"\x89PNG\r\n\x1a\n";

pub fn is_program(content_type: &str) -> bool {
    CONTENT_TYPES.contains(&content_type)
}

fn u16le(b: &[u8], o: usize) -> Option<u32> {
    b.get(o..o + 2).map(|s| u16::from_le_bytes([s[0], s[1]]) as u32)
}

fn u32le(b: &[u8], o: usize) -> Option<u32> {
    b.get(o..o + 4).map(|s| u32::from_le_bytes([s[0], s[1], s[2], s[3]]))
}

/// The resource section, read where asked (seek + read).
struct Section<'a> {
    f: &'a mut std::fs::File,
    raw: u64,
    size: u64,
}

impl Section<'_> {
    fn get(&mut self, start: u64, stop: u64) -> Vec<u8> {
        let stop = stop.min(self.size);
        if stop <= start {
            return Vec::new();
        }
        let mut buf = vec![0; (stop - start) as usize];
        if self.f.seek(SeekFrom::Start(self.raw + start)).is_err() {
            return Vec::new();
        }
        let n = self.f.read(&mut buf).unwrap_or(0);
        buf.truncate(n);
        buf
    }

    /// [(id or None for a name, offset, is_directory)] of a resource directory.
    fn entries(&mut self, off: u64) -> Vec<(Option<u32>, u64, bool)> {
        let head = self.get(off, off + 16);
        let (Some(named), Some(ids)) = (u16le(&head, 12), u16le(&head, 14)) else { return Vec::new() };
        let n = (named + ids).min(4096) as u64;
        let data = self.get(off + 16, off + 16 + 8 * n);
        (0..data.len() / 8)
            .map(|i| {
                let (name, target) = (u32le(&data, i * 8).unwrap(), u32le(&data, i * 8 + 4).unwrap());
                (if name & 0x8000_0000 != 0 { None } else { Some(name) }, (target & 0x7FFF_FFFF) as u64, target & 0x8000_0000 != 0)
            })
            .collect()
    }

    /// (rva, size) of the first leaf under a directory (any name, any language).
    fn first_leaf(&mut self, mut off: u64) -> Option<(u32, u32)> {
        for _ in 0..4 {
            let (_, o, is_dir) = *self.entries(off).first()?;
            off = o;
            if !is_dir {
                let head = self.get(off, off + 8);
                return Some((u32le(&head, 0)?, u32le(&head, 4)?));
            }
        }
        None
    }

    fn read(&mut self, base: u32, leaf: Option<(u32, u32)>) -> Option<Vec<u8>> {
        let (rva, size) = leaf?;
        let start = rva.checked_sub(base)? as u64;
        (start <= self.size).then(|| self.get(start, start + size as u64))
    }
}

/// (the resource section's file offset and size, its RVA).
fn rsrc(f: &mut std::fs::File) -> Option<(u64, u64, u32)> {
    let mut b = [0u8; 64];
    f.seek(SeekFrom::Start(0)).ok()?;
    f.read_exact(&mut b).ok()?;
    if &b[..2] != b"MZ" {
        return None;
    }
    let pe = u32le(&b, 0x3C)? as u64;
    let mut h = [0u8; 24];
    f.seek(SeekFrom::Start(pe)).ok()?;
    f.read_exact(&mut h).ok()?;
    if &h[..4] != b"PE\0\0" {
        return None;
    }
    let nsec = u16le(&h, 6)?;
    let optsize = u16le(&h, 20)? as usize;
    let mut opt = vec![0u8; optsize];
    f.read_exact(&mut opt).ok()?;
    let dirs = match u16le(&opt, 0)? {
        0x10B => 96,
        0x20B => 112,
        _ => return None,
    };
    let (rva, size) = (u32le(&opt, dirs + 16)?, u32le(&opt, dirs + 20)?); // #2: resources
    if rva == 0 || size == 0 {
        return None;
    }
    for _ in 0..nsec.min(96) {
        let mut sec = [0u8; 40];
        f.read_exact(&mut sec).ok()?;
        let (vsize, vaddr, rawsize, rawptr) = (u32le(&sec, 8)?, u32le(&sec, 12)?, u32le(&sec, 16)?, u32le(&sec, 20)?);
        // (in u64: a damaged header near 4 GiB overflowed the sum)
        if vaddr <= rva && (rva as u64) < vaddr as u64 + vsize.max(rawsize) as u64 {
            return Some((rawptr as u64, (vsize.max(rawsize) as u64).min(MAX_RSRC), vaddr));
        }
    }
    None
}

/// The icon's image closest above `size` (else the biggest): PNG bytes, or
/// an .ico file holding it -- None when the program has no icon.
pub fn best_icon(path: &std::path::Path, size: u32) -> Option<Vec<u8>> {
    let mut f = std::fs::File::open(path).ok()?;
    let (raw, sz, base) = rsrc(&mut f)?;
    let mut s = Section { f: &mut f, raw, size: sz };
    let mut icons: HashMap<u32, (u32, u32)> = HashMap::new();
    let mut group = None;
    for (tid, off, is_dir) in s.entries(0) {
        if !is_dir {
            continue;
        }
        if tid == Some(RT_ICON) {
            for (iid, ioff, idir) in s.entries(off) {
                if let (Some(i), true) = (iid, idir) {
                    if let Some(leaf) = s.first_leaf(ioff) {
                        icons.insert(i, leaf);
                    }
                }
            }
        } else if tid == Some(RT_GROUP_ICON) && group.is_none() {
            let leaf = s.first_leaf(off);
            group = s.read(base, leaf);
        }
    }
    let group = group.filter(|g| g.len() >= 6)?;
    let count = u16le(&group, 4)?;
    let mut choices = Vec::new();
    for i in 0..count as usize {
        let e = 6 + 14 * i;
        if e + 14 > group.len() {
            break;
        }
        let w = group[e] as u32;
        let (bits, iid) = (u16le(&group, e + 6)?, u16le(&group, e + 12)?);
        if icons.contains_key(&iid) {
            choices.push((if w == 0 { 256 } else { w }, bits, iid, group[e..e + 12].to_vec()));
        }
    }
    let above: Vec<&(u32, u32, u32, Vec<u8>)> = choices.iter().filter(|c| c.0 >= size).collect();
    let pick = if above.is_empty() { choices.iter().max_by_key(|c| (c.0, c.1))? } else { *above.iter().min_by_key(|c| (c.0, std::cmp::Reverse(c.1)))? };
    let img = s.read(base, icons.get(&pick.2).copied())?;
    if img.is_empty() {
        return None;
    }
    if img.starts_with(PNG_MAGIC) {
        return Some(img);
    }
    // a DIB: wrapped in a one-image .ico, which GdkPixbuf reads
    let mut ico = vec![0, 0, 1, 0, 1, 0];
    ico.extend_from_slice(&pick.3[..8]);
    ico.extend_from_slice(&(img.len() as u32).to_le_bytes());
    ico.extend_from_slice(&22u32.to_le_bytes());
    ico.extend_from_slice(&img);
    Some(ico)
}

/// The program's icon at most `size` px (never scaled up).
pub fn pixbuf(path: &std::path::Path, size: i32) -> Option<Pixbuf> {
    let data = best_icon(path, size as u32)?;
    let loader = PixbufLoader::new();
    loader.write(&data).ok()?;
    loader.close().ok()?;
    let pb = loader.pixbuf()?;
    let (w, h) = (pb.width(), pb.height());
    if w.max(h) > size {
        let k = size as f64 / w.max(h) as f64;
        return pb.scale_simple(((w as f64 * k).round() as i32).max(1), ((h as f64 * k).round() as i32).max(1), InterpType::Bilinear);
    }
    Some(pb)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn png_bytes(w: i32) -> Vec<u8> {
        let pb = Pixbuf::new(gtk::gdk_pixbuf::Colorspace::Rgb, true, 8, w, w).unwrap();
        pb.fill(0xcc3333ff);
        pb.save_to_bufferv("png", &[]).unwrap()
    }

    /// A minimal PE32+ whose resources hold one icon group of `icons`
    /// ([(width, png)]), as compilers write them (tests/test_peicon.py's make_pe).
    fn make_pe(icons: &[(u32, Vec<u8>)]) -> Vec<u8> {
        let base = 0x1000u32;
        let dir = |entries: &[(u32, u32, bool)]| {
            let mut out = Vec::new();
            out.extend_from_slice(&[0u8; 12]);
            out.extend_from_slice(&0u16.to_le_bytes());
            out.extend_from_slice(&(entries.len() as u16).to_le_bytes());
            for (i, off, d) in entries {
                out.extend_from_slice(&i.to_le_bytes());
                out.extend_from_slice(&(off | if *d { 0x8000_0000 } else { 0 }).to_le_bytes());
            }
            out
        };
        let n = icons.len() as u32;
        let (root_sz, type_sz, grp_type_sz, name_sz) = (32, 16 + 8 * n, 24, 24);
        let off_type_icon = root_sz;
        let off_type_group = off_type_icon + type_sz;
        let off_icon_names = off_type_group + grp_type_sz;
        let off_group_name = off_icon_names + n * name_sz;
        let off_data_entries = off_group_name + name_sz;
        let off_data = off_data_entries + 16 * (n + 1);
        let mut blobs: Vec<Vec<u8>> = icons.iter().map(|(_, p)| p.clone()).collect();
        let mut group = vec![0, 0, 1, 0];
        group.extend_from_slice(&(n as u16).to_le_bytes());
        for (i, (w, png)) in icons.iter().enumerate() {
            group.extend_from_slice(&[(w % 256) as u8, (w % 256) as u8, 0, 0, 1, 0, 32, 0]);
            group.extend_from_slice(&(png.len() as u32).to_le_bytes());
            group.extend_from_slice(&(i as u16 + 1).to_le_bytes());
        }
        blobs.push(group);
        let (mut data, mut positions) = (Vec::new(), Vec::new());
        for b in &blobs {
            positions.push(off_data + data.len() as u32);
            data.extend_from_slice(b);
            data.resize(data.len().div_ceil(4) * 4, 0);
        }
        let mut sec = dir(&[(RT_ICON, off_type_icon, true), (RT_GROUP_ICON, off_type_group, true)]);
        sec.extend(dir(&(0..n).map(|i| (i + 1, off_icon_names + i * name_sz, true)).collect::<Vec<_>>()));
        sec.extend(dir(&[(1, off_group_name, true)]));
        for i in 0..n {
            sec.extend(dir(&[(1033, off_data_entries + 16 * i, false)]));
        }
        sec.extend(dir(&[(1033, off_data_entries + 16 * n, false)]));
        for (p, b) in positions.iter().zip(&blobs) {
            for v in [base + p, b.len() as u32, 0, 0] {
                sec.extend_from_slice(&v.to_le_bytes());
            }
        }
        assert_eq!(sec.len() as u32, off_data);
        sec.extend(data);
        let mut head = b"MZ".to_vec();
        head.resize(0x3C, 0);
        head.extend_from_slice(&0x40u32.to_le_bytes());
        head.extend_from_slice(b"PE\0\0");
        for v in [0x8664u16, 1] {
            head.extend_from_slice(&v.to_le_bytes());
        }
        head.extend_from_slice(&[0u8; 12]);
        head.extend_from_slice(&240u16.to_le_bytes());
        head.extend_from_slice(&0x22u16.to_le_bytes());
        let mut opt = vec![0u8; 240];
        opt[0..2].copy_from_slice(&0x20Bu16.to_le_bytes());
        opt[128..132].copy_from_slice(&base.to_le_bytes());
        opt[132..136].copy_from_slice(&(sec.len() as u32).to_le_bytes());
        head.extend(opt);
        let raw = 0x200u32;
        head.extend_from_slice(b".rsrc\0\0\0");
        for v in [sec.len() as u32, base, sec.len() as u32, raw, 0, 0] {
            head.extend_from_slice(&v.to_le_bytes());
        }
        head.extend_from_slice(&[0u8; 4]);
        head.extend_from_slice(&0x4000_0040u32.to_le_bytes());
        head.resize(raw as usize, 0);
        head.extend(sec);
        head
    }

    /// From tests/test_peicon.py: the size closest above wins, made to fit.
    #[test]
    fn png_icon_picks_the_size() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("app.exe");
        std::fs::write(&p, make_pe(&[(32, png_bytes(32)), (256, png_bytes(256)), (48, png_bytes(48))])).unwrap();
        assert!(best_icon(&p, 256).unwrap().starts_with(PNG_MAGIC));
        let pb = pixbuf(&p, 256).unwrap();
        assert_eq!((pb.width(), pb.height()), (256, 256));
        assert_eq!(pixbuf(&p, 40).unwrap().width(), 40);
        assert_eq!(pixbuf(&p, 16).unwrap().width(), 16); // (32, the closest above, scaled down)
    }

    #[test]
    fn not_a_program() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path().join("x.exe");
        std::fs::write(&p, b"MZ not really").unwrap();
        assert!(super::best_icon(&p, 256).is_none());
        assert!(super::is_program("application/x-msdownload"));
    }
}
