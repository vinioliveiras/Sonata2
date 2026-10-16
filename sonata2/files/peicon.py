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
MAX_RSRC = 1 << 31           # (a bound on offsets: only what is needed is read)
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_program(content_type: str) -> bool:
    return (content_type or "") in CONTENT_TYPES


class _Section:
    """The resource section, read where asked (seek + read): a game's
    resources can be hundreds of MB; only the directories and one icon
    are read."""

    def __init__(self, f, rawptr, size):
        self.f, self.rawptr, self.size = f, rawptr, size

    def __len__(self):
        return self.size

    def __getitem__(self, s):
        start, stop = max(0, s.start or 0), min(self.size, s.stop if s.stop is not None else self.size)
        if stop <= start:
            return b""
        self.f.seek(self.rawptr + start)
        return self.f.read(stop - start)


def _rsrc(f):
    """(the resource section, its RVA), or None."""
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
    if not rva or not size:
        return None
    for _ in range(min(nsec, 96)):
        sec = f.read(40)
        if len(sec) < 40:
            return None
        vsize, vaddr, rawsize, rawptr = struct.unpack("<IIII", sec[8:24])
        if vaddr <= rva < vaddr + max(vsize, rawsize):
            return _Section(f, rawptr, min(max(vsize, rawsize), MAX_RSRC)), vaddr
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


def _first_leaf(data, off):
    """(rva, size) of the first leaf under a directory (any name, any language)."""
    for _ in range(4):                               # type > name > language > data
        ents = _entries(data, off)
        if not ents:
            return None
        _id, off, is_dir = ents[0]
        if not is_dir:
            head = data[off:off + 8]
            return struct.unpack("<II", head) if len(head) == 8 else None
    return None


def _read(data, base, leaf):
    if not leaf:
        return None
    start = leaf[0] - base
    return data[start:start + leaf[1]] if 0 <= start <= len(data) else None


def _icons(data, base):
    """({icon id: (rva, size)}, the first icon group's bytes): only the
    group is read here, an icon's bytes once it is chosen."""
    icons, group = {}, None
    for tid, off, is_dir in _entries(data, 0):
        if not is_dir:
            continue
        if tid == RT_ICON:
            for iid, ioff, idir in _entries(data, off):
                if iid is not None and idir:
                    leaf = _first_leaf(data, ioff)
                    if leaf:
                        icons[iid] = leaf
        elif tid == RT_GROUP_ICON and group is None:
            group = _read(data, base, _first_leaf(data, off))
    return icons, group


def best_icon(path: str, size: int = 256):
    """The icon's image closest above `size` (else the biggest): PNG bytes,
    or an .ico file holding it -- None when the program has no icon."""
    try:
        with open(path, "rb") as f:
            got = _rsrc(f)
            if got is None:
                return None
            return _best(f, *got, size)
    except (OSError, struct.error):
        return None


def _best(f, data, base, size):
    icons, group = _icons(data, base)
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
    img = _read(data, base, icons[iid])
    if not img:
        return None
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
