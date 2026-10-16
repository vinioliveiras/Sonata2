"""Vini: Windows programs (.exe) in Files show their own icon. A PE file's
icon read from its resources (files/peicon.py), as a thumbnail.
python3 -m unittest tests.test_peicon"""
import glob
import os
import struct
import tempfile
import unittest

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib  # noqa: E402

from sonata2.files import peicon  # noqa: E402


def png_bytes(w, h):
    pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
    pb.fill(0xcc3333ff)
    ok, buf = pb.save_to_bufferv("png", [], [])
    return bytes(buf)


def make_pe(icons):
    """A minimal PE32+ whose resources hold one icon group of `icons`
    ([(width, png bytes)]), the way compilers write them."""
    # resource section, RVA 0x1000
    base = 0x1000

    def directory(entries):          # [(id, offset, is_dir)]
        out = struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries))
        for i, off, d in entries:
            out += struct.pack("<II", i, off | (0x80000000 if d else 0))
        return out
    # layout: root(2 types) | type ICON dir | type GROUP dir | per-icon name+lang dirs | group name+lang | data entries | data
    n = len(icons)
    root_sz, type_sz = 16 + 16, 16 + 8 * n
    grp_type_sz = 16 + 8
    name_sz = 16 + 8                          # a name dir with one language
    off_type_icon = root_sz
    off_type_group = off_type_icon + type_sz
    off_icon_names = off_type_group + grp_type_sz
    off_group_name = off_icon_names + n * name_sz
    off_data_entries = off_group_name + name_sz
    off_data = off_data_entries + 16 * (n + 1)
    blobs = [png for _w, png in icons]
    group = struct.pack("<HHH", 0, 1, n) + b"".join(
        struct.pack("<BBBBHHIH", w % 256, w % 256, 0, 0, 1, 32, len(png), i + 1) for i, (w, png) in enumerate(icons))
    blobs.append(group)
    data, positions = b"", []
    for b in blobs:
        positions.append(off_data + len(data))
        data += b + b"\0" * (-len(b) % 4)
    sec = directory([(peicon.RT_ICON, off_type_icon, True), (peicon.RT_GROUP_ICON, off_type_group, True)])
    sec += directory([(i + 1, off_icon_names + i * name_sz, True) for i in range(n)])
    sec += directory([(1, off_group_name, True)])
    for i in range(n):
        sec += directory([(1033, off_data_entries + 16 * i, False)])
    sec += directory([(1033, off_data_entries + 16 * n, False)])
    for p, b in zip(positions, blobs):
        sec += struct.pack("<IIII", base + p, len(b), 0, 0)
    assert len(sec) == off_data
    sec += data
    # headers
    dos = b"MZ" + b"\0" * 0x3A + struct.pack("<I", 0x40)
    coff = struct.pack("<HHIIIHH", 0x8664, 1, 0, 0, 0, 240, 0x22)
    opt = bytearray(240)
    opt[0:2] = struct.pack("<H", 0x20B)
    opt[112 + 16:112 + 24] = struct.pack("<II", base, len(sec))       # data directory #2: resources
    raw = 0x200
    section = b".rsrc\0\0\0" + struct.pack("<IIIIIIHHI", len(sec), base, len(sec), raw, 0, 0, 0, 0, 0x40000040)
    head = dos + b"PE\0\0" + coff + bytes(opt) + section
    return head + b"\0" * (raw - len(head)) + sec


class PeIconTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def write(self, name, data):
        p = os.path.join(self.d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_png_icon_picks_the_size(self):
        p = self.write("app.exe", make_pe([(32, png_bytes(32, 32)), (256, png_bytes(256, 256)),
                                            (48, png_bytes(48, 48))]))
        self.assertTrue(peicon.best_icon(p, 256).startswith(peicon.PNG_MAGIC))
        pb = peicon.pixbuf(p, 256)
        self.assertEqual((pb.get_width(), pb.get_height()), (256, 256))
        pb = peicon.pixbuf(p, 40)                                   # the closest above, made to fit
        self.assertEqual(pb.get_width(), 40)

    def test_real_windows_programs(self):
        found = [p for p in glob.glob(os.path.expanduser("~/.cache/uv/archive-v0/*/distlib/w*.exe"))]
        if not found:
            self.skipTest("no Windows program here")
        pb = peicon.pixbuf(found[0], 256)
        self.assertIsNotNone(pb)                                   # an old-style (DIB) icon
        self.assertGreaterEqual(pb.get_width(), 16)

    def test_no_icon_or_not_a_program(self):
        for data in (b"", b"MZ", b"hello world" * 100, b"MZ" + b"\0" * 0x3A + struct.pack("<I", 9999),
                     make_pe([])[:0x300]):
            self.assertIsNone(peicon.pixbuf(self.write("x.exe", data)), data[:10])

    def test_thumbnails_ask_for_programs(self):
        from gi.repository import Gio
        from sonata2.files import thumbs
        info = Gio.FileInfo()
        for ct in peicon.CONTENT_TYPES:
            info.set_content_type(ct)
            self.assertTrue(thumbs.wanted(info), ct)
        info.set_content_type("application/x-executable")         # a Linux program: its usual icon
        self.assertFalse(thumbs.wanted(info))

    def test_thumbnail_made(self):
        from unittest import mock
        from gi.repository import Gio
        from sonata2.files import thumbs
        p = self.write("game.exe", make_pe([(256, png_bytes(256, 256))]))
        with mock.patch.object(thumbs, "CACHE_DIR", os.path.join(self.d, "large")), \
                mock.patch.object(thumbs, "FAIL_DIR", os.path.join(self.d, "fail")):
            out = thumbs._make(Gio.File.new_for_path(p).get_uri(), p, 1, peicon.CONTENT_TYPES[0], os.path.getsize(p))
            self.assertIsNotNone(out)
            self.assertEqual(GdkPixbuf.Pixbuf.new_from_file(out).get_width(), 256)


if __name__ == "__main__":
    unittest.main()
