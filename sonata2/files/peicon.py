"""The icon inside a Windows program (.exe, .dll): Files shows it instead
of the generic one (Vini), like Explorer does.

A PE file keeps its icons as resources: RT_GROUP_ICON lists the sizes of
an icon, each one an RT_ICON (a PNG, or a DIB as in an .ico file). Only
the headers and the resource section are read (seek, never the whole
file: a game's .exe can be gigabytes). Pure Python, no tools needed.

    pb = peicon.pixbuf("/path/setup.exe", 256)    # GdkPixbuf, or None"""
import struct

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

RT_ICON, RT_GROUP_ICON = 3, 14
CONTENT_TYPES = ("application/x-ms-dos-executable", "application/x-msdownload", "application/x-dosexec",
                 "application/vnd.microsoft.portable-executable", "application/x-ms-ne-executable")
MAX_RSRC = 64 << 20          # a resource section bigger than this isn't read
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_program(content_type: str) -> bool:
    return (content_type or "") in CONTENT_TYPES


def _rsrc(f):
    """(the resource section's bytes, its RVA), or None."""
    f.seek(0)
    if f.read(2) != b"MZ":
        return None
    f.seek(0x3C)
    pe = struct.unpack("<I", f.read(4))[0]
    f.seek(pe)
    if f.read(4) != b"PE\0\0":
        return None
    _machine, nsec, _t, _p, _n, optsize, _c = struct.unpack("<HHIIIHH", f.read(20))
    opt = f.read(optsize)
    if len(opt) < 2:
        return None
    magic = struct.unpack("<H", opt[:2])[0]
    dirs = {0x10B: 96, 0x20B: 112}.get(magic)       # data directories' offset (PE32 / PE32+)
    if dirs is None or len(opt) < dirs + 3 * 8:
        return None
    rva, size = struct.unpack("<II", opt[dirs + 2 * 8:dirs + 3 * 8])     # #2: resources
    if not rva or not size or size > MAX_RSRC:
        return None
    for _ in range(nsec):
        sec = f.read(40)
        if len(sec) < 40:
            return None
        vsize, vaddr, rawsize, rawptr = struct.unpack("<IIII", sec[8:24])
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            f.seek(rawptr)
            data = f.read(min(max(vsize, rawsize), MAX_RSRC))
            return data, vaddr
    return None


def _entries(data, off):
    """[(id or None for a name, offset, is_directory)] of a resource directory."""
    if off + 16 > len(data):
        return []
    named, ids = struct.unpack("<HH", data[off + 12:off + 16])
    out = []
    for i in range(min(named + ids, 4096)):
        e = off + 16 + 8 * i
        if e + 8 > len(data):
            break
        name, target = struct.unpack("<II", data[e:e + 8])
        out.append((None if name & 0x80000000 else name, target & 0x7FFFFFFF, bool(target & 0x80000000)))
    return out


def _first_data(data, off, base):
    """The bytes of the first leaf under a directory (any name, any language)."""
    for _ in range(4):                               # type > name > language > data
        ents = _entries(data, off)
        if not ents:
            return None
        _id, off, is_dir = ents[0]
        if not is_dir:
            if off + 8 > len(data):
                return None
            rva, size = struct.unpack("<II", data[off:off + 8])
            start = rva - base
            return data[start:start + size] if 0 <= start <= len(data) else None
    return None


def _icons(data, base):
    """({icon id: bytes}, the first icon group's bytes) of a resource section."""
    icons, group = {}, None
    for tid, off, is_dir in _entries(data, 0):
        if not is_dir:
            continue
        if tid == RT_ICON:
            for iid, ioff, idir in _entries(data, off):
                if iid is not None and idir:
                    b = _first_data(data, ioff, base)
                    if b:
                        icons[iid] = b
        elif tid == RT_GROUP_ICON and group is None:
            group = _first_data(data, off, base)
    return icons, group


def best_icon(path: str, size: int = 256):
    """The icon's image closest above `size` (else the biggest): PNG bytes,
    or an .ico file holding it -- None when the program has no icon."""
    try:
        with open(path, "rb") as f:
            got = _rsrc(f)
    except (OSError, struct.error):
        return None
    if got is None:
        return None
    try:
        icons, group = _icons(*got)
    except struct.error:
        return None
    if not icons or not group or len(group) < 6:
        return None
    count = struct.unpack("<H", group[4:6])[0]
    choices = []
    for i in range(count):
        e = 6 + 14 * i
        if e + 14 > len(group):
            break
        w, _h, _colors, _r, _planes, bits, nbytes, iid = struct.unpack("<BBBBHHIH", group[e:e + 14])
        if iid in icons:
            choices.append((w or 256, bits, iid, group[e:e + 12]))
    if not choices:
        return None
    above = [c for c in choices if c[0] >= size]
    w, _bits, iid, head = (min(above, key=lambda c: (c[0], -c[1])) if above
                           else max(choices, key=lambda c: (c[0], c[1])))
    img = icons[iid]
    if img.startswith(PNG_MAGIC):
        return img
    # a DIB: wrapped in a one-image .ico, which GdkPixbuf reads
    entry = head[:8] + struct.pack("<II", len(img), 6 + 16)
    return struct.pack("<HHH", 0, 1, 1) + entry + img


def pixbuf(path: str, size: int = 256):
    """The program's icon as a GdkPixbuf at most `size` px (never scaled up), or None."""
    data = best_icon(path, size)
    if not data:
        return None
    try:
        loader = GdkPixbuf.PixbufLoader()
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
    except GLib.Error:
        return None
    if pb is None:
        return None
    w, h = pb.get_width(), pb.get_height()
    if max(w, h) > size:
        k = size / max(w, h)
        pb = pb.scale_simple(max(1, round(w * k)), max(1, round(h * k)), GdkPixbuf.InterpType.BILINEAR)
    return pb
