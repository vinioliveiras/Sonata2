"""Audio tags (Videos shows them for a song): title, artist, album, album
artist, track number, year, genre, duration and embedded cover art.

mutagen reads every format when it is installed. Without it a small
built-in reader covers the common cases: ID3v2.2/2.3/2.4 (MP3, with the
duration from the Xing/Info header or the first frame's bitrate), FLAC
(Vorbis comments, STREAMINFO, PICTURE), Ogg Vorbis/Opus (comments,
METADATA_BLOCK_PICTURE, last granule), MP4/M4A (ilst atoms, mvhd) and WAV
(RIFF header). Anything missing falls back to the file name; a duration
may stay 0 (the player fills it in later).

    read(path) -> dict(title, artist, album, album_artist, track, disc, year,
                       genre, duration, cover)   # cover: (bytes, mime) or None
"""
import base64
import os
import re
import struct

EXTS = (".mp3", ".flac", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".wav", ".mp4")
MAX_TAG = 16 * 1024 * 1024          # never read more than this from one tag

# ID3v1 genres (the start of the list: what "(17)" style TCON values use)
GENRES = ("Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge", "Hip-Hop", "Jazz", "Metal",
          "New Age", "Oldies", "Other", "Pop", "R&B", "Rap", "Reggae", "Rock", "Techno", "Industrial",
          "Alternative", "Ska", "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop",
          "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical", "Instrumental", "Acid", "House", "Game",
          "Sound Clip", "Gospel", "Noise", "Alternative Rock", "Bass", "Soul", "Punk", "Space", "Meditative",
          "Instrumental Pop", "Instrumental Rock", "Ethnic", "Gothic", "Darkwave", "Techno-Industrial",
          "Electronic", "Pop-Folk", "Eurodance", "Dream", "Southern Rock", "Comedy", "Cult", "Gangsta",
          "Top 40", "Christian Rap", "Pop/Funk", "Jungle", "Native American", "Cabaret", "New Wave",
          "Psychedelic", "Rave", "Showtunes", "Trailer", "Lo-Fi", "Tribal", "Acid Punk", "Acid Jazz", "Polka",
          "Retro", "Musical", "Rock & Roll", "Hard Rock")


def empty() -> dict:
    return {"title": "", "artist": "", "album": "", "album_artist": "", "track": 0, "disc": 0, "year": 0,
            "genre": "", "duration": 0.0, "cover": None}


def _num(v) -> int:
    """'3/12' -> 3, '2004-05-01' -> 2004."""
    m = re.match(r"\s*(\d+)", str(v or ""))
    return int(m.group(1)) if m else 0


def _genre(v: str) -> str:
    v = (v or "").strip()
    m = re.fullmatch(r"\((\d+)\)(.*)", v)
    if m:
        n = int(m.group(1))
        return m.group(2).strip() or (GENRES[n] if n < len(GENRES) else "")
    if v.isdigit() and int(v) < len(GENRES):
        return GENRES[int(v)]
    return v


def from_filename(path: str) -> dict:
    """Title and track number from '01 - Title.mp3' / '01 Title.mp3'."""
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"^\s*(?:(\d{1,2})-)?(\d{1,3})[\s._-]+(.+)$", base)
    if m:
        return {"track": int(m.group(2)), "title": m.group(3).strip(" -_.") or base}
    return {"track": 0, "title": base}


def read(path: str) -> dict:
    """Tags of one file; never raises."""
    t = empty()
    try:
        got = _read_mutagen(path)
        if got is None:
            got = _read_builtin(path)
        t.update({k: v for k, v in (got or {}).items() if v})
    except Exception:            # a broken file is only a song without tags
        pass
    t["genre"] = _genre(t["genre"])
    if not t["title"]:
        fn = from_filename(path)
        t["title"] = fn["title"]
        t["track"] = t["track"] or fn["track"]
    return t


# -- mutagen ------------------------------------------------------------------------------
def _read_mutagen(path: str):
    try:
        import mutagen
    except ImportError:
        return None
    f = mutagen.File(path)
    if f is None:
        return {}
    out = {"duration": float(getattr(getattr(f, "info", None), "length", 0) or 0)}
    tags = f.tags or {}

    def first(*keys):
        for k in keys:
            try:
                v = tags.get(k)
            except (ValueError, KeyError):
                v = None
            if v is None:
                continue
            if hasattr(v, "text"):
                v = v.text
            if isinstance(v, (list, tuple)):
                v = v[0] if v else None
            if isinstance(v, tuple):
                v = v[0]
            if v not in (None, ""):
                return str(v)
        return ""
    out["title"] = first("TIT2", "title", "TITLE", "\xa9nam")
    out["artist"] = first("TPE1", "artist", "ARTIST", "\xa9ART")
    out["album"] = first("TALB", "album", "ALBUM", "\xa9alb")
    out["album_artist"] = first("TPE2", "albumartist", "ALBUMARTIST", "album artist", "aART")
    out["genre"] = first("TCON", "genre", "GENRE", "\xa9gen")
    out["year"] = _num(first("TDRC", "TYER", "date", "DATE", "year", "\xa9day"))
    trk = tags.get("trkn") if hasattr(tags, "get") else None
    out["track"] = trk[0][0] if trk else _num(first("TRCK", "tracknumber", "TRACKNUMBER"))
    out["disc"] = _num(first("TPOS", "discnumber", "DISCNUMBER"))
    out["cover"] = _mutagen_cover(f)
    return out


def _mutagen_cover(f):
    pics = getattr(f, "pictures", None)                 # FLAC
    if pics:
        return bytes(pics[0].data), pics[0].mime
    tags = f.tags
    if tags is None:
        return None
    if hasattr(tags, "getall"):                         # ID3
        apic = tags.getall("APIC") or tags.getall("PIC")
        if apic:
            return bytes(apic[0].data), getattr(apic[0], "mime", "image/jpeg")
    try:
        covr = tags.get("covr")                          # MP4
        if covr:
            return bytes(covr[0]), "image/png" if bytes(covr[0])[:4] == b"\x89PNG" else "image/jpeg"
        mbp = tags.get("metadata_block_picture")        # Ogg
        if mbp:
            return _flac_picture(base64.b64decode(mbp[0]))
    except (TypeError, ValueError, AttributeError):
        pass
    return None


# -- built-in readers ---------------------------------------------------------------------
def _read_builtin(path: str) -> dict:
    with open(path, "rb") as fh:
        head = fh.read(12)
        fh.seek(0)
        if head[:3] == b"ID3" or path.lower().endswith(".mp3"):
            return read_mp3(fh)
        if head[:4] == b"fLaC":
            return read_flac(fh)
        if head[:4] == b"OggS":
            return read_ogg(fh)
        if head[4:8] == b"ftyp":
            return read_mp4(fh)
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return read_wav(fh)
    return {}


# ID3v2 ------------------------------------------------------------------------------------
_ID3_KEYS = {"TIT2": "title", "TT2": "title", "TPE1": "artist", "TP1": "artist", "TALB": "album", "TAL": "album",
             "TPE2": "album_artist", "TP2": "album_artist", "TRCK": "track", "TRK": "track", "TPOS": "disc",
             "TPA": "disc", "TYER": "year", "TYE": "year", "TDRC": "year", "TCON": "genre", "TCO": "genre",
             "TLEN": "tlen", "TLE": "tlen"}


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _decode(enc: int, data: bytes) -> str:
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    try:
        s = data.decode(codec)
    except UnicodeDecodeError:
        s = data.decode("latin-1", "replace")
    return s.lstrip("﻿").split("\x00")[0].strip()        # (several values: the first)


def _split_term(enc: int, data: bytes):
    """(text before the encoding's terminator, rest)."""
    if enc in (1, 2):
        i = 0
        while i + 1 < len(data):
            if data[i] == 0 and data[i + 1] == 0:
                return data[:i], data[i + 2:]
            i += 2
        return data, b""
    i = data.find(b"\x00")
    return (data, b"") if i < 0 else (data[:i], data[i + 1:])


def parse_id3(tag: bytes, major: int) -> dict:
    """Frames of an ID3v2 tag body (after the 10-byte header)."""
    out = {}
    i, n = 0, len(tag)
    idlen, hlen = (3, 6) if major == 2 else (4, 10)
    while i + hlen <= n:
        fid = tag[i:i + idlen]
        if not fid.strip(b"\x00") or not re.fullmatch(rb"[A-Z0-9]+", fid):
            break
        if major == 2:
            size = int.from_bytes(tag[i + 3:i + 6], "big")
        elif major == 4:
            size = _syncsafe(tag[i + 4:i + 8])
        else:
            size = int.from_bytes(tag[i + 4:i + 8], "big")
        body = tag[i + hlen:i + hlen + size]
        i += hlen + size
        fid = fid.decode("latin-1")
        if not body:
            continue
        key = _ID3_KEYS.get(fid)
        if key:
            val = _decode(body[0], body[1:])
            if key in ("track", "disc", "year"):
                out[key] = _num(val)
            elif key == "tlen":
                out["duration"] = _num(val) / 1000.0
            else:
                out[key] = val
        elif fid in ("APIC", "PIC") and "cover" not in out:
            enc = body[0]
            if fid == "APIC":
                mime, rest = _split_term(0, body[1:])
                mime = mime.decode("latin-1") or "image/jpeg"
            else:
                fmt, rest = body[1:4], body[4:]
                mime = "image/png" if fmt.upper() == b"PNG" else "image/jpeg"
            _desc, data = _split_term(enc, rest[1:])            # (picture type byte first)
            if data:
                if "/" not in mime:
                    mime = "image/" + mime.lower().replace("jpg", "jpeg")
                out["cover"] = (bytes(data), mime)
    return out


_BITRATES = {  # (version 1, layer 3) and (version 2/2.5, layer 3), kbit/s
    1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0),
    2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def mp3_duration(fh, start: int, file_size: int) -> float:
    """From the first MPEG audio frame: Xing/Info/VBRI frame count, or CBR."""
    fh.seek(start)
    buf = fh.read(64 * 1024)
    i = 0
    while i + 4 <= len(buf):
        if buf[i] == 0xFF and (buf[i + 1] & 0xE0) == 0xE0:
            h = int.from_bytes(buf[i:i + 4], "big")
            ver = (h >> 19) & 3                       # 3: MPEG1, 2: MPEG2, 0: MPEG2.5
            layer = (h >> 17) & 3                     # 1: layer III
            br_i, sr_i = (h >> 12) & 15, (h >> 10) & 3
            if ver != 1 and layer == 1 and br_i not in (0, 15) and sr_i != 3:
                rate = _RATES[ver][sr_i]
                kbps = _BITRATES[1 if ver == 3 else 2][br_i]
                spf = 1152 if ver == 3 else 576
                mono = ((h >> 6) & 3) == 3
                side = (17 if mono else 32) if ver == 3 else (9 if mono else 17)
                x = i + 4 + side
                for tagname in (b"Xing", b"Info"):
                    if buf[x:x + 4] == tagname and int.from_bytes(buf[x + 4:x + 8], "big") & 1:
                        frames = int.from_bytes(buf[x + 8:x + 12], "big")
                        return frames * spf / rate
                if buf[i + 36:i + 40] == b"VBRI":
                    frames = int.from_bytes(buf[i + 50:i + 54], "big")
                    return frames * spf / rate
                return max(0, file_size - start - i) * 8 / (kbps * 1000)
        i += 1
    return 0.0


def read_mp3(fh) -> dict:
    out = {}
    head = fh.read(10)
    start = 0
    if len(head) == 10 and head[:3] == b"ID3":
        major, flags = head[3], head[5]
        size = _syncsafe(head[6:10])
        start = 10 + size + (10 if flags & 0x10 else 0)
        tag = fh.read(min(size, MAX_TAG))
        if flags & 0x80 and major < 4:                 # whole-tag unsynchronisation
            tag = tag.replace(b"\xff\x00", b"\xff")
        if flags & 0x40 and major == 3 and len(tag) >= 4:      # extended header
            tag = tag[4 + int.from_bytes(tag[:4], "big"):]
        out = parse_id3(tag, major)
    if not out.get("duration"):
        size = os.fstat(fh.fileno()).st_size
        out["duration"] = mp3_duration(fh, start, size)
    if not out.get("title"):                           # ID3v1 at the end
        try:
            fh.seek(-128, os.SEEK_END)
            v1 = fh.read(128)
        except OSError:
            v1 = b""
        if v1[:3] == b"TAG":
            def s(a, b):
                return v1[a:b].split(b"\x00")[0].decode("latin-1").strip()
            out.setdefault("title", s(3, 33))
            out.setdefault("artist", s(33, 63))
            out.setdefault("album", s(63, 93))
            out.setdefault("year", _num(s(93, 97)))
            if v1[125] == 0 and v1[126]:
                out.setdefault("track", v1[126])
            if v1[127] < len(GENRES):
                out.setdefault("genre", GENRES[v1[127]])
    return out


# Vorbis comments (FLAC, Ogg) -----------------------------------------------------------
_VORBIS_KEYS = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album", "ALBUMARTIST": "album_artist",
                "ALBUM ARTIST": "album_artist", "TRACKNUMBER": "track", "DISCNUMBER": "disc", "DATE": "year",
                "YEAR": "year", "GENRE": "genre"}


def parse_vorbis_comment(data: bytes) -> dict:
    out = {}
    p = 0
    vlen = struct.unpack_from("<I", data, p)[0]
    p += 4 + vlen
    count = struct.unpack_from("<I", data, p)[0]
    p += 4
    for _ in range(count):
        if p + 4 > len(data):
            break
        ln = struct.unpack_from("<I", data, p)[0]
        p += 4
        entry = data[p:p + ln].decode("utf-8", "replace")
        p += ln
        k, _s, v = entry.partition("=")
        k = k.upper()
        key = _VORBIS_KEYS.get(k)
        if key and key not in out:
            out[key] = _num(v) if key in ("track", "disc", "year") else v.strip()
        elif k == "METADATA_BLOCK_PICTURE" and "cover" not in out:
            try:
                out["cover"] = _flac_picture(base64.b64decode(v))
            except (ValueError, struct.error):
                pass
    return out


def _flac_picture(data: bytes):
    p = 4
    mlen = struct.unpack_from(">I", data, p)[0]
    mime = data[p + 4:p + 4 + mlen].decode("latin-1") or "image/jpeg"
    p += 4 + mlen
    dlen = struct.unpack_from(">I", data, p)[0]
    p += 4 + dlen + 16
    ln = struct.unpack_from(">I", data, p)[0]
    return bytes(data[p + 4:p + 4 + ln]), mime


def read_flac(fh) -> dict:
    out = {}
    fh.read(4)
    while True:
        h = fh.read(4)
        if len(h) < 4:
            break
        last, kind, size = h[0] & 0x80, h[0] & 0x7F, int.from_bytes(h[1:4], "big")
        if kind in (0, 4, 6) and size <= MAX_TAG:
            block = fh.read(size)
            if kind == 0 and len(block) >= 18:
                bits = int.from_bytes(block[10:18], "big")
                rate, total = bits >> 44, bits & 0xFFFFFFFFF
                if rate:
                    out["duration"] = total / rate
            elif kind == 4:
                out.update({k: v for k, v in parse_vorbis_comment(block).items() if k not in out})
            elif kind == 6 and "cover" not in out:
                out["cover"] = _flac_picture(block)
        else:
            fh.seek(size, os.SEEK_CUR)
        if last:
            break
    return out


# Ogg ------------------------------------------------------------------------------------
def _ogg_packets(fh, want: int = 3, limit: int = MAX_TAG):
    """The first `want` packets of the first logical stream."""
    packets, cur, read = [], b"", 0
    while len(packets) < want and read < limit:
        h = fh.read(27)
        if len(h) < 27 or h[:4] != b"OggS":
            break
        nseg = h[26]
        lace = fh.read(nseg)
        data = fh.read(sum(lace))
        read += 27 + nseg + len(data)
        p = 0
        for ln in lace:
            cur += data[p:p + ln]
            p += ln
            if ln < 255:
                packets.append(cur)
                cur = b""
                if len(packets) >= want:
                    break
    return packets


def _ogg_last_granule(fh) -> int:
    size = os.fstat(fh.fileno()).st_size
    fh.seek(max(0, size - 65536))
    buf = fh.read()
    i = buf.rfind(b"OggS")
    if i < 0 or i + 14 > len(buf):
        return 0
    return struct.unpack_from("<q", buf, i + 6)[0]


def read_ogg(fh) -> dict:
    packets = _ogg_packets(fh, 2)
    if not packets:
        return {}
    first = packets[0]
    out = {}
    if first[:7] == b"\x01vorbis":
        rate, skip, codec = struct.unpack_from("<I", first, 12)[0], 0, "vorbis"
    elif first[:8] == b"OpusHead":
        rate, skip, codec = 48000, struct.unpack_from("<H", first, 10)[0], "opus"
    else:
        return {}
    if len(packets) > 1:
        c = packets[1]
        if codec == "vorbis" and c[:7] == b"\x03vorbis":
            out = parse_vorbis_comment(c[7:])
        elif codec == "opus" and c[:8] == b"OpusTags":
            out = parse_vorbis_comment(c[8:])
    g = _ogg_last_granule(fh)
    if g > 0 and rate:
        out["duration"] = max(0, g - skip) / rate
    return out


# MP4 / M4A ------------------------------------------------------------------------------
_MP4_KEYS = {b"\xa9nam": "title", b"\xa9ART": "artist", b"\xa9alb": "album", b"aART": "album_artist",
             b"\xa9day": "year", b"\xa9gen": "genre", b"gnre": "genre", b"trkn": "track", b"disk": "disc",
             b"covr": "cover"}


def _atoms(data: bytes, start: int = 0, end: int = None):
    end = len(data) if end is None else end
    p = start
    while p + 8 <= end:
        size = int.from_bytes(data[p:p + 4], "big")
        kind = data[p + 4:p + 8]
        head = 8
        if size == 1:
            size, head = int.from_bytes(data[p + 8:p + 16], "big"), 16
        elif size == 0:
            size = end - p
        if size < head:
            break
        yield kind, p + head, min(p + size, end)
        p += size


def read_mp4(fh) -> dict:
    moov = None
    fh.seek(0)
    while True:
        h = fh.read(8)
        if len(h) < 8:
            break
        size, kind = int.from_bytes(h[:4], "big"), h[4:8]
        head = 8
        if size == 1:
            size, head = int.from_bytes(fh.read(8), "big"), 16
        if kind == b"moov":
            moov = fh.read(min(size - head, MAX_TAG))
            break
        if size < head:
            break
        fh.seek(size - head, os.SEEK_CUR)
    if not moov:
        return {}
    out = {}
    for kind, a, b in _atoms(moov):
        if kind == b"mvhd":
            ver = moov[a]
            if ver == 1:
                scale, dur = struct.unpack_from(">IQ", moov, a + 20)
            else:
                scale, dur = struct.unpack_from(">II", moov, a + 12)
            if scale:
                out["duration"] = dur / scale
        elif kind == b"udta":
            for k2, a2, b2 in _atoms(moov, a, b):
                if k2 != b"meta":
                    continue
                for k3, a3, b3 in _atoms(moov, a2 + 4, b2):           # (meta has 4 bytes of version/flags)
                    if k3 != b"ilst":
                        continue
                    for item, a4, b4 in _atoms(moov, a3, b3):
                        key = _MP4_KEYS.get(item)
                        if not key:
                            continue
                        for k5, a5, b5 in _atoms(moov, a4, b4):
                            if k5 != b"data":
                                continue
                            val = moov[a5 + 8:b5]
                            if key in ("track", "disc"):
                                out[key] = int.from_bytes(val[2:4], "big") if len(val) >= 4 else 0
                            elif key == "cover":
                                out["cover"] = (bytes(val), "image/png" if val[:4] == b"\x89PNG" else "image/jpeg")
                            elif item == b"gnre":
                                n = int.from_bytes(val[:2], "big") - 1
                                out["genre"] = GENRES[n] if 0 <= n < len(GENRES) else ""
                            elif key == "year":
                                out["year"] = _num(val.decode("utf-8", "replace"))
                            else:
                                out[key] = val.decode("utf-8", "replace")
                            break
    return out


# WAV ------------------------------------------------------------------------------------
def read_wav(fh) -> dict:
    fh.seek(12)
    rate = 0
    while True:
        h = fh.read(8)
        if len(h) < 8:
            break
        kind, size = h[:4], struct.unpack("<I", h[4:])[0]
        if kind == b"fmt ":
            fmt = fh.read(size)
            rate = struct.unpack_from("<I", fmt, 8)[0]         # byte rate
            size = 0
        elif kind == b"data":
            return {"duration": size / rate} if rate else {}
        fh.seek(size + (size & 1), os.SEEK_CUR)
    return {}
