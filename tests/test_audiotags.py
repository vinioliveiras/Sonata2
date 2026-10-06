"""Audio tags (sonata2/audiotags.py, what Videos shows for a song):
synthetic MP3 / FLAC / Ogg / MP4 / WAV files, parsed with the built-in
reader. (Moved here from the Music app's tests when Music was removed.)"""
import os
import struct
import tempfile
import unittest

from sonata2 import audiotags as tags

_TMP = tempfile.mkdtemp()


def _write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        f.write(text)


PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201a5f1e7"
                    "5a0000000049454e44ae426082")


# -- synthetic files ----------------------------------------------------------------------
def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def _id3_text(fid: str, text: str, enc: int = 3) -> bytes:
    codec = {0: "latin-1", 1: "utf-16", 3: "utf-8"}[enc]
    body = bytes([enc]) + text.encode(codec)
    return fid.encode() + struct.pack(">I", len(body)) + b"\x00\x00" + body


def make_mp3(path, title="Song", artist="Artist", album="Album", track="3/12", year="2004", genre="(17)",
             cover=PNG, seconds=10):
    frames = b"".join((_id3_text("TIT2", title, 1), _id3_text("TPE1", artist), _id3_text("TALB", album, 0),
                       _id3_text("TRCK", track), _id3_text("TYER", year), _id3_text("TCON", genre)))
    if cover:
        body = b"\x00image/png\x00\x03cover\x00" + cover
        frames += b"APIC" + struct.pack(">I", len(body)) + b"\x00\x00" + body
    tag = b"ID3\x03\x00\x00" + _syncsafe(len(frames)) + frames
    # one MPEG-1 layer III frame header (128 kbit/s, 44.1 kHz) and CBR data
    audio = b"\xff\xfb\x90\x64" + b"\x00" * (16000 * seconds - 4)
    with open(path, "wb") as f:
        f.write(tag + audio)


def _vorbis_comment(fields: dict) -> bytes:
    vendor = b"test"
    out = struct.pack("<I", len(vendor)) + vendor + struct.pack("<I", len(fields))
    for k, v in fields.items():
        e = f"{k}={v}".encode()
        out += struct.pack("<I", len(e)) + e
    return out


def _flac_picture(data: bytes, mime="image/png") -> bytes:
    return (struct.pack(">I", 3) + struct.pack(">I", len(mime)) + mime.encode() + struct.pack(">I", 0)
            + struct.pack(">IIII", 1, 1, 32, 0) + struct.pack(">I", len(data)) + data)


def make_flac(path, fields=None, rate=44100, samples=441000, cover=PNG):
    fields = fields or {"TITLE": "Flac Song", "ARTIST": "Flac Artist", "ALBUM": "Flac Album",
                        "TRACKNUMBER": "7", "DATE": "1999-01-01", "GENRE": "Jazz", "ALBUMARTIST": "Band"}
    bits = (rate << 44) | (1 << 41) | (15 << 36) | samples       # rate, 2 channels, 16 bit, total samples
    info = b"\x10\x00\x10\x00" + b"\x00" * 6 + bits.to_bytes(8, "big") + b"\x00" * 16
    blocks = [(0, info), (4, _vorbis_comment(fields))]
    if cover:
        blocks.append((6, _flac_picture(cover)))
    out = b"fLaC"
    for i, (kind, data) in enumerate(blocks):
        last = 0x80 if i == len(blocks) - 1 else 0
        out += bytes([last | kind]) + len(data).to_bytes(3, "big") + data
    with open(path, "wb") as f:
        f.write(out + b"\x00" * 64)


def _ogg_page(packet: bytes, seq: int, granule: int, flags: int = 0) -> bytes:
    lace, n = [], len(packet)
    while n >= 255:
        lace.append(255)
        n -= 255
    lace.append(n)
    return (b"OggS\x00" + bytes([flags]) + struct.pack("<qIII", granule, 1, seq, 0) + bytes([len(lace)])
            + bytes(lace) + packet)


def make_opus(path, seconds=5):
    head = b"OpusHead\x01\x02" + struct.pack("<HIhB", 312, 48000, 0, 0)
    tags_ = b"OpusTags" + _vorbis_comment({"TITLE": "Opus Song", "ARTIST": "Opus Artist", "ALBUM": "Opus Album"})
    with open(path, "wb") as f:
        f.write(_ogg_page(head, 0, 0, 2) + _ogg_page(tags_, 1, 0) + _ogg_page(b"\x00" * 10, 2, 312 + 48000 * seconds, 4))



def _atom(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _mp4(title="Mp4 Song", track=5, seconds=12, cover=b"\x89PNGxx"):
    mvhd = _atom(b"mvhd", b"\x00\x00\x00\x00" + b"\x00" * 8 + struct.pack(">II", 1000, seconds * 1000) + b"\x00" * 80)
    data = lambda v: _atom(b"data", b"\x00\x00\x00\x01\x00\x00\x00\x00" + v)  # noqa: E731
    ilst = _atom(b"ilst", _atom(b"\xa9nam", data(title.encode())) + _atom(b"\xa9ART", data(b"Mp4 Artist"))
                 + _atom(b"trkn", data(struct.pack(">HHHH", 0, track, 10, 0))) + _atom(b"gnre", data(b"\x00\x0a"))
                 + _atom(b"covr", data(cover)))
    moov = _atom(b"moov", mvhd + _atom(b"udta", _atom(b"meta", b"\x00\x00\x00\x00" + ilst)))
    return _atom(b"ftyp", b"M4A \x00\x00\x00\x00") + _atom(b"mdat", b"\x00" * 64) + moov


class TagEdgeTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_TMP)

    def path(self, name, data: bytes):
        p = os.path.join(self.dir, name)
        _write(p, data, "wb")
        return p

    def test_mp4_ilst_and_duration(self):
        """M4A tags (title, artist, track, numeric genre, cover) and mvhd duration with moov after mdat."""
        t = tags.read(self.path("a.m4a", _mp4()))
        self.assertEqual((t["title"], t["artist"], t["track"], t["genre"]), ("Mp4 Song", "Mp4 Artist", 5, "Metal"))
        self.assertAlmostEqual(t["duration"], 12.0)
        self.assertEqual(t["cover"], (b"\x89PNGxx", "image/png"))

    def test_wav_duration(self):
        """WAV length = data size / byte rate (from the fmt chunk)."""
        fmt = struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16)
        body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", 176400 * 3)
        p = self.path("01 Intro.wav", b"RIFF" + struct.pack("<I", len(body)) + body)
        t = tags.read(p)
        self.assertAlmostEqual(t["duration"], 3.0)
        self.assertEqual((t["title"], t["track"]), ("Intro", 1))

    def test_mp3_xing_vbr_and_id3v1(self):
        """A VBR MP3 without ID3v2 takes its length from the Xing frame count and its tags from ID3v1."""
        frame = b"\xff\xfb\x90\x64" + b"\x00" * 32 + b"Xing" + struct.pack(">II", 1, 1000) + b"\x00" * 400
        v1 = (b"TAG" + b"V1 Title".ljust(30, b"\0") + b"V1 Artist".ljust(30, b"\0") + b"V1 Album".ljust(30, b"\0")
              + b"1999" + b"\0" * 28 + b"\x00\x07" + bytes([17]))
        t = tags.read(self.path("x.mp3", frame + b"\x00" * 5000 + v1))
        self.assertAlmostEqual(t["duration"], 1000 * 1152 / 44100, places=2)
        self.assertEqual((t["title"], t["artist"], t["album"], t["year"], t["track"], t["genre"]),
                         ("V1 Title", "V1 Artist", "V1 Album", 1999, 7, "Rock"))

    def test_number_genre_and_filename_helpers(self):
        """'3/12', dates, ID3 numeric genres and disc-track file names parse; years in names aren't tracks."""
        self.assertEqual((tags._num("3/12"), tags._num("2004-05-01"), tags._num(None), tags._num("x")),
                         (3, 2004, 0, 0))
        self.assertEqual((tags._genre("(9)"), tags._genre("(9)Death Thrash"), tags._genre("13"), tags._genre("Indie")),
                         ("Metal", "Death Thrash", "Pop", "Indie"))
        self.assertEqual(tags._genre("(250)"), "")
        self.assertEqual(tags.from_filename("/m/1-05 Song Name.flac"), {"track": 5, "title": "Song Name"})
        self.assertEqual(tags.from_filename("/m/1984.mp3"), {"track": 0, "title": "1984"})
        self.assertEqual(tags.from_filename("/m/2001 - A Space Odyssey.mp3")["track"], 0)


class TagTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_TMP)

    def test_mp3_id3v2(self):
        p = os.path.join(self.dir, "a.mp3")
        make_mp3(p, title="Héllo AC/DC", artist="Ärtist", album="Albüm")
        with open(p, "rb") as fh:
            t = tags.read_mp3(fh)
        self.assertEqual(t["title"], "Héllo AC/DC")            # UTF-16 with BOM, "/" kept
        self.assertEqual(t["artist"], "Ärtist")
        self.assertEqual(t["album"], "Albüm")                  # latin-1
        self.assertEqual(t["track"], 3)
        self.assertEqual(t["year"], 2004)
        self.assertEqual(tags._genre(t["genre"]), "Rock")      # "(17)"
        self.assertEqual(t["cover"], (PNG, "image/png"))
        self.assertAlmostEqual(t["duration"], 10.0, delta=0.1)
        full = tags.read(p)
        self.assertEqual(full["genre"], "Rock")

    def test_id3v24_syncsafe_frames(self):
        body = b"\x03" + "Four".encode()
        frame = b"TIT2" + _syncsafe(len(body)) + b"\x00\x00" + body
        self.assertEqual(tags.parse_id3(frame, 4)["title"], "Four")
        v22 = b"TT2" + (len(body)).to_bytes(3, "big") + body
        self.assertEqual(tags.parse_id3(v22, 2)["title"], "Four")

    def test_flac(self):
        p = os.path.join(self.dir, "b.flac")
        make_flac(p)
        with open(p, "rb") as fh:
            t = tags.read_flac(fh)
        self.assertEqual((t["title"], t["artist"], t["album"], t["album_artist"]),
                         ("Flac Song", "Flac Artist", "Flac Album", "Band"))
        self.assertEqual((t["track"], t["year"], t["genre"]), (7, 1999, "Jazz"))
        self.assertAlmostEqual(t["duration"], 10.0)
        self.assertEqual(t["cover"], (PNG, "image/png"))

    def test_opus(self):
        p = os.path.join(self.dir, "c.opus")
        make_opus(p, 5)
        t = tags.read(p)
        self.assertEqual((t["title"], t["artist"], t["album"]), ("Opus Song", "Opus Artist", "Opus Album"))
        self.assertAlmostEqual(t["duration"], 5.0)

    def test_filename_fallback(self):
        p = os.path.join(self.dir, "04 - Some Title.m4a")
        with open(p, "wb") as f:
            f.write(b"not really audio")
        t = tags.read(p)
        self.assertEqual((t["title"], t["track"]), ("Some Title", 4))
        self.assertEqual(tags.from_filename("Plain.mp3")["title"], "Plain")

    def test_broken_file_never_raises(self):
        p = os.path.join(self.dir, "x.flac")
        with open(p, "wb") as f:
            f.write(b"fLaC\x84\xff\xff")
        self.assertEqual(tags.read(p)["title"], "x")


if __name__ == "__main__":
    unittest.main()
