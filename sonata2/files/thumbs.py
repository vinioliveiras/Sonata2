"""Thumbnails for images and videos (Finder shows the picture instead of
the file icon), and Windows programs' own icons (peicon.py).

Uses the freedesktop thumbnail cache (~/.cache/thumbnails/large, shared
with GNOME/KDE apps): an existing up-to-date thumbnail is loaded; otherwise
one is made -- images with GdkPixbuf, videos with ffmpegthumbnailer or
ffmpeg -- and saved there with Thumb::URI / Thumb::MTime. Work runs on 2
background threads, only for items being shown; results are kept in a
small in-memory cache."""
import hashlib
import os
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

SIZE = 256                       # "large" thumbnails
MAX_IMAGE_BYTES = 150 * 1000 ** 2
CACHE_DIR = os.path.join(GLib.get_user_cache_dir(), "thumbnails", "large")
FAIL_DIR = os.path.join(GLib.get_user_cache_dir(), "thumbnails", "fail", "sonata2")
MEMORY = 400                     # textures kept in RAM, at most...
# ...and at most this many bytes of pixels (memory review: 400 large
# thumbnails were up to ~100 MB in the resident Files process); a folder's
# worth of 256 px thumbnails still fits (~130)
MEMORY_BYTES = 32 << 20

from .peicon import is_program  # noqa: E402  (Windows programs: their own icon)

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sonata2-thumbs")
_mem = OrderedDict()             # key -> Gdk.Texture
_pending = {}                    # key -> [callbacks]
# files that have none (a ".png" that isn't one): asked once a session. They
# were kept with the pictures, as None: a folder of them pushed the pictures
# out of the cache, and were asked again (a thread, two file checks) on every
# listing
_none = OrderedDict()
NONE_KEEP = 20000


def wanted(info) -> bool:
    ct = info.get_content_type() or ""
    return ct.startswith("image/") or ct.startswith("video/") or is_program(ct)


def request(info, gfile, callback) -> None:
    """callback(Gdk.Texture) on the GTK thread when a thumbnail is ready
    (never called when there is none)."""
    path = gfile.get_path()
    if not path or not wanted(info):
        return
    mtime = info.get_attribute_uint64("time::modified")
    # an edit makes a new key: its seconds, microseconds and size (two edits in
    # the same second kept the old picture -- Vini: edited files' thumbnails
    # didn't change)
    key = (path, mtime, info.get_attribute_uint32("time::modified-usec"), info.get_size())
    if key in _mem:
        _mem.move_to_end(key)
        callback(_mem[key])
        return
    if key in _none:
        return
    if key in _pending:
        _pending[key].append(callback)
        return
    _pending[key] = [callback]
    existing = info.get_attribute_byte_string("thumbnail::path") if info.has_attribute("thumbnail::path") else None
    ct = info.get_content_type() or ""
    _pool.submit(_work, key, gfile.get_uri(), path, mtime, ct, info.get_size(), existing)


def _work(key, uri, path, mtime, ct, size, existing):
    tex = None
    try:
        # GIO's thumbnail::path names the cached file even when it is out of
        # date (the file edited since): used only when it is this version's
        thumb = (existing if existing and _valid(existing, mtime, size) else None) or _cached(uri, mtime, size)
        if not thumb and not _failed(uri, mtime):
            thumb = _make(uri, path, mtime, ct, size)
        if thumb:
            tex = Gdk.Texture.new_from_filename(thumb)
    except Exception:                  # a broken file never breaks the view
        tex = None
    GLib.idle_add(_deliver, key, tex)


def _bytes(tex) -> int:
    try:
        return tex.get_width() * tex.get_height() * 4 if tex is not None else 0
    except AttributeError:
        return 0


def _deliver(key, tex):
    if tex is None:
        _none[key] = True
        while len(_none) > NONE_KEEP:
            _none.popitem(last=False)
        _pending.pop(key, None)
        return False
    _mem[key] = tex
    _mem.move_to_end(key)
    used = sum(_bytes(t) for t in _mem.values())       # (a few hundred entries at most)
    while len(_mem) > 1 and (len(_mem) > MEMORY or used > MEMORY_BYTES):
        _k, old = _mem.popitem(last=False)
        used -= _bytes(old)
    for cb in _pending.pop(key, []):
        if tex is not None:
            cb(tex)
    return False


def _name(uri) -> str:
    return hashlib.md5(uri.encode()).hexdigest() + ".png"


def png_text(p) -> dict:
    """A PNG's tEXt entries, read from its chunks before the picture: no
    decoding (it was decoded once to check it, then again to show it)."""
    out = {}
    try:
        with open(p, "rb") as f:
            if f.read(8) != b"\x89PNG\r\n\x1a\n":
                return out
            while True:
                head = f.read(8)
                if len(head) < 8:
                    break
                n, kind = int.from_bytes(head[:4], "big"), head[4:]
                if kind in (b"IDAT", b"IEND") or n > 1 << 20:
                    break                             # (text comes before the picture in thumbnails)
                data = f.read(n)
                f.seek(4, 1)                          # its CRC
                if kind == b"tEXt" and b"\0" in data:
                    k, v = data.split(b"\0", 1)
                    out[k.decode("latin-1")] = v.decode("latin-1")
    except OSError:
        pass
    return out


def _valid(p, mtime, size=None) -> bool:
    """A cached thumbnail of this version of the file: its Thumb::MTime, and
    its Thumb::Size when it has one (an edit within the same second)."""
    if not os.path.exists(p):
        return False
    text = png_text(p)
    if text.get("Thumb::MTime") != str(mtime):
        return False
    kept = text.get("Thumb::Size")
    return size is None or kept is None or kept == str(size)


def _cached(uri, mtime, size=None):
    p = os.path.join(CACHE_DIR, _name(uri))
    return p if _valid(p, mtime, size) else None


def _failed(uri, mtime) -> bool:
    return os.path.exists(os.path.join(FAIL_DIR, _name(uri) + f".{mtime}"))


def _mark_failed(uri, mtime):
    try:
        os.makedirs(FAIL_DIR, exist_ok=True)
        open(os.path.join(FAIL_DIR, _name(uri) + f".{mtime}"), "w").close()
    except OSError:
        pass


def _make(uri, path, mtime, ct, size):
    pb = None
    if ct.startswith("image/") and size <= MAX_IMAGE_BYTES:
        from .. import imageload                      # WebP, AVIF, camera RAW... too
        pb = imageload.pixbuf_at_scale(path, SIZE)
    elif ct.startswith("video/"):
        pb = _video_frame(path)
    else:
        from . import peicon
        if peicon.is_program(ct):                     # a Windows program: its own icon (Vini)
            pb = peicon.pixbuf(path, SIZE)
    if pb is None:
        _mark_failed(uri, mtime)
        return None
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    out = os.path.join(CACHE_DIR, _name(uri))
    tmp = out + f".sonata{os.getpid()}.tmp"
    pb.savev(tmp, "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime", "tEXt::Thumb::Size", "tEXt::Software"],
             [uri, str(mtime), str(size), "Sonata Files"])
    os.replace(tmp, out)
    return out


def _video_frame(path):
    out = tempfile.NamedTemporaryFile(suffix=".png", delete=False).name
    try:
        if shutil.which("ffmpegthumbnailer"):
            cmd = ["ffmpegthumbnailer", "-i", path, "-o", out, "-s", str(SIZE), "-t", "10%"]
        elif shutil.which("ffmpeg"):
            cmd = ["ffmpeg", "-v", "quiet", "-y", "-ss", "3", "-i", path, "-frames:v", "1",
                   "-vf", f"scale={SIZE}:{SIZE}:force_original_aspect_ratio=decrease", out]
        else:
            return None
        subprocess.run(cmd, timeout=20, check=False, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.getsize(out) == 0 and shutil.which("ffmpeg") and cmd[0] == "ffmpeg":
            # very short clip: first frame instead
            subprocess.run(cmd[:5] + ["0"] + cmd[6:], timeout=20, check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return GdkPixbuf.Pixbuf.new_from_file(out) if os.path.getsize(out) else None
    except (OSError, GLib.Error, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.unlink(out)
        except OSError:
            pass
