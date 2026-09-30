"""Music: tag parsing (synthetic MP3/FLAC/Ogg), the library cache and its
incremental rescan, playlists and plays, the queue (shuffle / repeat), MPRIS
metadata and the window (xvfb-run python3 -m unittest tests.test_music)."""
import os
import random
import struct
import tempfile
import unittest

_TMP = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = os.path.join(_TMP, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_TMP, "cache")
os.environ["XDG_CONFIG_HOME"] = os.path.join(_TMP, "config")
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2.music import library as lib  # noqa: E402
from sonata2.music import tags  # noqa: E402
from sonata2.music.queue import REPEAT_ALL, REPEAT_OFF, REPEAT_ONE, Queue  # noqa: E402

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


def settle(ms=150):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


# -- tags ---------------------------------------------------------------------------------
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


# -- library ------------------------------------------------------------------------------
class LibraryTest(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp(dir=_TMP)
        self.root = os.path.join(base, "Music")
        os.makedirs(os.path.join(self.root, "Artist", "Album"))
        self.lib = lib.Library(self.root, cache=os.path.join(base, "cache.json"), data=os.path.join(base, "data"),
                               art=os.path.join(base, "art"))

    def test_scan_cache_and_incremental(self):
        a = os.path.join(self.root, "Artist", "Album", "01 - One.mp3")
        b = os.path.join(self.root, "Artist", "Album", "02 - Two.flac")
        make_mp3(a, title="One", track="1")
        make_flac(b, fields={"TITLE": "Two"})               # no artist/album tags: from the folders
        with open(os.path.join(self.root, "notes.txt"), "w") as f:
            f.write("x")
        self.assertTrue(self.lib.apply(self.lib.rescan()))
        self.assertEqual(len(self.lib.tracks), 2)
        tb = self.lib.tracks[b]
        self.assertEqual((tb["artist"], tb["album"]), ("Artist", "Album"))
        self.assertTrue(os.path.isfile(self.lib.tracks[a]["art"]))
        self.assertTrue(self.lib.tracks[a]["art"].startswith(self.lib.art))
        # the cache is reloaded as it was saved
        other = lib.Library(self.root, cache=self.lib.cache, data=self.lib.data, art=self.lib.art)
        self.assertTrue(other.load_cache())
        self.assertEqual(other.tracks.keys(), self.lib.tracks.keys())
        # unchanged files are not read again
        calls = []
        real = lib.read_track
        lib.read_track = lambda *a_, **k: (calls.append(a_[0]), real(*a_, **k))[1]
        try:
            self.assertFalse(other.apply(other.rescan()))
            self.assertEqual(calls, [])
            c = os.path.join(self.root, "Artist", "Album", "03 - Three.opus")
            make_opus(c)
            os.remove(b)
            st = os.stat(a)
            make_mp3(a, title="One (Remastered)", track="1")
            os.utime(a, (st.st_atime, st.st_mtime + 5))
            self.assertTrue(other.apply(other.rescan()))
            self.assertEqual(sorted(calls), sorted([a, c]))
        finally:
            lib.read_track = real
        self.assertNotIn(b, other.tracks)
        self.assertEqual(other.tracks[a]["title"], "One (Remastered)")
        self.assertEqual(other.tracks[a]["added"], self.lib.tracks[a]["added"])   # added date survives edits

    def test_albums_plays_playlists(self):
        for i, artist in enumerate(("X", "Y", "Z")):
            make_mp3(os.path.join(self.root, f"{i}.mp3"), title=f"T{i}", artist=artist, album="Mix",
                     track=str(3 - i), cover=None)
        make_mp3(os.path.join(self.root, "solo.mp3"), title="Solo", artist="X", album="Solo", cover=None)
        self.lib.apply(self.lib.rescan())
        albums = {a["title"]: a for a in self.lib.albums()}
        self.assertEqual(albums["Mix"]["artist"], "Various Artists")
        self.assertEqual([t["track"] for t in albums["Mix"]["tracks"]], [1, 2, 3])
        self.assertEqual(self.lib.artists(), ["X", "Y", "Z"])
        p = os.path.join(self.root, "solo.mp3")
        self.lib.add_play(p)
        self.assertEqual(self.lib.add_play(p), 2)
        self.assertEqual(lib.Library(self.root, data=self.lib.data).play_count(p), 2)
        pl = self.lib.new_playlist("Road", [p])
        self.assertEqual(self.lib.new_playlist("Road")["name"], "Road 2")
        self.lib.add_to_playlist(pl["name"], [os.path.join(self.root, "0.mp3"), "/gone.mp3"])
        self.assertEqual([t["title"] for t in self.lib.playlist_tracks("Road")], ["Solo", "T0"])
        self.lib.remove_from_playlist("Road", [0])
        self.lib.rename_playlist("Road", "Trip")
        again = lib.Library(self.root, data=self.lib.data)
        self.assertEqual([p_["name"] for p_ in again.playlists], ["Trip", "Road 2"])
        self.assertEqual(again.playlist("Trip")["tracks"][0], os.path.join(self.root, "0.mp3"))


# -- queue --------------------------------------------------------------------------------
class QueueTest(unittest.TestCase):
    def test_order_and_repeat(self):
        q = Queue()
        q.set(["a", "b", "c"], 1)
        self.assertEqual(q.current, "b")
        self.assertEqual(q.upcoming(), ["c"])
        self.assertEqual(q.next(), "c")
        self.assertIsNone(q.next())                           # end, repeat off
        q.repeat = REPEAT_ALL
        self.assertEqual(q.next(), "a")
        self.assertEqual(q.previous(), "c")                    # wraps back under repeat all
        q.repeat = REPEAT_ONE
        self.assertEqual(q.next(auto=True), "c")              # the song ended: again
        self.assertEqual(q.next(), "a")                        # the Next button still skips
        self.assertEqual(q.cycle_repeat(), REPEAT_OFF)

    def test_shuffle(self):
        q = Queue(random.Random(4))
        items = [str(i) for i in range(20)]
        q.set(items, 5)
        q.set_shuffle(True)
        self.assertEqual(q.current, "5")                       # the current song stays
        self.assertEqual(sorted(q.upcoming() + [q.current]), sorted(items))
        self.assertNotEqual(q.upcoming(), items[6:])
        seen = [q.current]
        while q.next() is not None:
            seen.append(q.current)
        self.assertEqual(sorted(seen), sorted(items))         # every song once
        q.set_shuffle(False)
        self.assertEqual(q.upcoming(), items[items.index(q.current) + 1:])
        q2 = Queue(random.Random(1))
        q2.shuffle = True
        q2.set(items, 3)
        self.assertEqual(q2.current, "3")

    def test_play_next_and_later(self):
        q = Queue()
        q.play_next(["x"])
        self.assertEqual(q.current, "x")
        q.set(["a", "b"], 0)
        q.play_next(["n1", "n2"])
        q.append(["z"])
        self.assertEqual(q.upcoming(), ["n1", "n2", "b", "z"])
        q.jump(2)
        self.assertEqual(q.current, "n2")


# -- MPRIS --------------------------------------------------------------------------------
class MprisTest(unittest.TestCase):
    def test_metadata(self):
        from sonata2.music import mpris
        t = {"path": "/m/a b.mp3", "title": "T", "artist": "A", "album": "Al", "album_artist": "AA",
             "track": 2, "genre": "Pop", "duration": 61.5, "art": "/c/x.png"}
        md = mpris.metadata(t)
        self.assertEqual(md["xesam:title"].unpack(), "T")
        self.assertEqual(md["xesam:artist"].unpack(), ["A"])
        self.assertEqual(md["xesam:album"].unpack(), "Al")
        self.assertEqual(md["mpris:length"].get_type_string(), "x")
        self.assertEqual(md["mpris:length"].unpack(), 61500000)
        self.assertEqual(md["mpris:artUrl"].unpack(), "file:///c/x.png")
        self.assertEqual(md["xesam:url"].unpack(), "file:///m/a%20b.mp3")
        tid = md["mpris:trackid"].unpack()
        self.assertTrue(GLib.Variant.is_object_path(tid))
        self.assertEqual(tid, mpris.track_id("/m/a b.mp3"))
        self.assertNotIn("mpris:artUrl", mpris.metadata({"path": "/x.mp3"}))
        self.assertEqual(mpris.metadata(None)["mpris:trackid"].unpack(), mpris.NO_TRACK)
        GLib.Variant("a{sv}", md)                              # a valid Metadata value
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        info = Gio.DBusNodeInfo.new_for_xml(mpris.XML)
        self.assertEqual([i.name for i in info.interfaces], [mpris.ROOT_IFACE, mpris.PLAYER_IFACE])


# -- window -------------------------------------------------------------------------------
def fake_tracks(root, art_dir=None, n_albums=6, per=5):
    out, t0 = {}, 1_700_000_000
    genres = ("Rock", "Jazz", "Pop", "Electronic", "Classical", "Soundtrack")
    for a in range(n_albums):
        art = ""
        if art_dir:
            art = os.path.join(art_dir, f"cover{a}.png")
        for i in range(per):
            p = os.path.join(root, f"Artist {a % 3}", f"Album {a}", f"{i + 1:02d} Song.mp3")
            out[p] = {"path": p, "title": f"Song {a}-{i + 1}", "artist": f"Artist {a % 3}",
                      "album": f"Album {a}", "album_artist": "", "track": i + 1, "disc": 0, "year": 2000 + a,
                      "genre": genres[a % len(genres)], "duration": 150 + 17 * i, "art": art, "thumb": art,
                      "added": t0 + a * 100 + i, "mtime": 0, "size": 0}
    return out


class WindowTest(unittest.TestCase):
    def test_window(self):
        Adw.init()
        from sonata2 import ui
        ui.setup()
        from sonata2.music import window as mw
        base = tempfile.mkdtemp(dir=_TMP)
        library = lib.Library(os.path.join(base, "Music"), cache=os.path.join(base, "c.json"),
                              data=os.path.join(base, "d"), art=os.path.join(base, "art"))
        library.tracks = fake_tracks(library.root)
        app = Adw.Application(application_id="io.test.music")
        app.register(None)
        win = mw.MusicWindow(app, library=library, scan=False, mpris=False)
        win.populate()
        win.present()
        settle()
        self.assertEqual(win.stack.get_visible_child_name(), "grid")
        self.assertEqual(win.albums_store.get_n_items(), 6)
        self.assertEqual(win.songs.store.get_n_items(), 30)
        win.show("songs")
        self.assertEqual(win.stack.get_visible_child_name(), "songs")
        win.set_query("song 2-3")
        settle()
        self.assertEqual(win.songs.paths(), [p for p, t in library.tracks.items() if t["title"] == "Song 2-3"])
        win.set_query("")
        win.show("artists")
        settle()
        self.assertEqual(win.artists_store.get_n_items(), 3)
        win.show_album(win.albums_store.get_item(0).a)
        self.assertEqual(win.stack.get_visible_child_name(), "album")
        # playback without a media backend: the queue fills, a clear message shows
        paths = win.songs.paths()
        win.play_list(paths, 2)
        self.assertEqual(win.queue.current, paths[2])
        self.assertEqual(win.queue.upcoming(), paths[3:])
        if not win.player.available:
            self.assertTrue(win.banner.get_reveal_child())
            self.assertIn("media backend", win.banner_label.get_label())
        self.assertEqual(win.lcd_stack.get_visible_child_name(), "track")
        self.assertEqual(win.lcd_title.get_label(), library.tracks[paths[2]]["title"])
        win.next()
        self.assertEqual(win.queue.current, paths[3])
        win.toggle_shuffle()
        self.assertIn("on", win.shuffle_btn.get_css_classes())
        win.cycle_repeat()
        win.cycle_repeat()
        self.assertEqual(win.repeat_btn.get_icon_name(), "media-playlist-repeat-song-symbolic")
        win.set_volume(0.3)
        self.assertAlmostEqual(win.volume.get_value(), 30)
        # plays: a song that ends is counted
        win.player.path = paths[3]
        win._ended()
        self.assertEqual(library.play_count(paths[3]), 1)
        # playlists
        win.new_playlist([paths[0]])
        settle()
        self.assertTrue(win.view.startswith("playlist:"))
        self.assertEqual(win.plist.store.get_n_items(), 1)
        win.show_current()
        self.assertEqual(win.view, "album")
        win.up_next()
        settle()
        # opening a file queues it next and plays it
        f = os.path.join(base, "opened.mp3")
        make_mp3(f, title="Opened")
        mw.open_windows(app, [f])
        self.assertEqual(win.queue.current, f)
        self.assertEqual(win.current_track()["title"], "Opened")
        self.assertIn("audio/flac", mw.MIME_TYPES)
        win.destroy()
        settle(50)

    def test_desktop_file(self):
        from sonata2.music.window import music_desktop_file
        path = music_desktop_file("/usr/bin/sonata2")
        with open(path) as f:
            text = f.read()
        self.assertIn("Exec=/usr/bin/sonata2 music %F", text)
        self.assertIn("MimeType=audio/mpeg;", text)
        self.assertIn("Name=Music", text)


if __name__ == "__main__":
    unittest.main()
