"""Thumbnails for images and videos (Finder shows the picture instead of
the file icon).

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
MEMORY = 400                     # textures kept in RAM

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sonata2-thumbs")
_mem = OrderedDict()             # key -> Gdk.Texture or None (failed)
_pending = {}                    # key -> [callbacks]


def wanted(info) -> bool:
    ct = info.get_content_type() or ""
    return ct.startswith("image/") or ct.startswith("video/")


def request(info, gfile, callback) -> None:
    """callback(Gdk.Texture) on the GTK thread when a thumbnail is ready
    (never called when there is none)."""
    path = gfile.get_path()
    if not path or not wanted(info):
        return
    mtime = info.get_attribute_uint64("time::modified")
    key = (path, mtime)
    if key in _mem:
        _mem.move_to_end(key)
        if _mem[key] is not None:
            callback(_mem[key])
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
        thumb = existing if existing and os.path.exists(existing) else _cached(uri, mtime)
        if not thumb and not _failed(uri, mtime):
            thumb = _make(uri, path, mtime, ct, size)
        if thumb:
            tex = Gdk.Texture.new_from_filename(thumb)
    except Exception:                  # a broken file never breaks the view
        tex = None
    GLib.idle_add(_deliver, key, tex)


def _deliver(key, tex):
    _mem[key] = tex
    while len(_mem) > MEMORY:
        _mem.popitem(last=False)
    for cb in _pending.pop(key, []):
        if tex is not None:
            cb(tex)
    return False


def _name(uri) -> str:
    return hashlib.md5(uri.encode()).hexdigest() + ".png"


def _cached(uri, mtime):
    p = os.path.join(CACHE_DIR, _name(uri))
    if not os.path.exists(p):
        return None
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(p)
        if pb.get_option("tEXt::Thumb::MTime") == str(mtime):
            return p
    except GLib.Error:
        pass
    return None


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
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, SIZE, SIZE, True)
            pb = pb.apply_embedded_orientation() or pb
        except GLib.Error:
            pb = None
    elif ct.startswith("video/"):
        pb = _video_frame(path)
    if pb is None:
        _mark_failed(uri, mtime)
        return None
    os.makedirs(CACHE_DIR, mode=0o700, exist_ok=True)
    out = os.path.join(CACHE_DIR, _name(uri))
    tmp = out + f".sonata{os.getpid()}.tmp"
    pb.savev(tmp, "png", ["tEXt::Thumb::URI", "tEXt::Thumb::MTime", "tEXt::Software"],
             [uri, str(mtime), "Sonata Files"])
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
