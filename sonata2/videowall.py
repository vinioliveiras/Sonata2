"""Videos as wallpapers (Vini: natively, in a loop, silent, playing only
while the desktop has the focus -- paused under any app -- and light on the
machine). No GTK here: the lock / login screens, Settings and the greeter
use it too.

A video wallpaper is set like a picture (picture-uri / picture-uri-dark
point at the video). Whatever needs a picture -- the blurred backdrop of
the lock and login screens, Mission Control, Settings' preview, the
desktop's first frame at login -- gets its poster: one frame taken with
ffmpeg, at most the size of a 4K display, kept in Sonata's cache and made
again when the video changes. The desktop (shell/wallpaper.py) plays the
video itself.

    is_video("/x/clip.mp4")         -> True
    poster("/x/clip.mp4")           -> "~/.cache/sonata2/wallpapers/<key>.jpg" or None (made if needed)
    still("/x/clip.mp4")            -> the poster; a picture's own path for a picture
"""
import hashlib
import os
import shutil
import subprocess

EXTENSIONS = (".mp4", ".m4v", ".webm", ".mkv", ".mov", ".ogv", ".avi")
POSTER_MAX = 3840                      # (longest side: a 4K display; the backdrops shrink it further)
POSTER_AT_S = 1.0                      # a moment in: the first frame is often black (a fade in)
TIMEOUT_S = 20


def is_video(path_or_type: str) -> bool:
    """A file's path or its content type ("video/mp4")."""
    s = (path_or_type or "").lower()
    return s.startswith("video/") or s.endswith(EXTENSIONS)


def _cache() -> str:
    return os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "sonata2", "wallpapers")


def poster_path(video: str) -> str:
    """Where the video's poster goes: named after its path, size and time
    (another video at the same path gets a new poster)."""
    try:
        st = os.stat(video)
        stamp = f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        stamp = ""
    key = hashlib.sha1(f"{os.path.abspath(video)}\0{stamp}".encode()).hexdigest()[:20]
    return os.path.join(_cache(), key + ".jpg")


def _ffmpeg(video: str, out: str, at: float) -> bool:
    exe = shutil.which("ffmpeg")
    if not exe:
        return False
    scale = f"scale='if(gt(iw,ih),min({POSTER_MAX},iw),-2)':'if(gt(iw,ih),-2,min({POSTER_MAX},ih))'"
    cmd = [exe, "-v", "error", "-nostdin", "-y", "-ss", str(at), "-i", video, "-frames:v", "1",
           "-vf", scale, "-q:v", "3", "-f", "image2", out]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return os.path.isfile(out) and os.path.getsize(out) > 0


def _thumbnailer(video: str, out: str) -> bool:
    exe = shutil.which("ffmpegthumbnailer")
    if not exe:
        return False
    try:
        subprocess.run([exe, "-i", video, "-o", out, "-s", "0", "-c", "jpeg", "-q", "8"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return os.path.isfile(out) and os.path.getsize(out) > 0


def poster(video: str):
    """The video's poster, made now if it isn't there yet (a fraction of a
    second; call it off the UI thread the first time). None when the video
    can't be read, or nothing can take a frame of it."""
    if not video or not os.path.isfile(video):
        return None
    out = poster_path(video)
    if os.path.isfile(out):
        return out
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
    except OSError:
        return None
    tmp = f"{out}.{os.getpid()}.tmp.jpg"
    try:
        # a short clip has no frame at 1 s: its first one then
        ok = _ffmpeg(video, tmp, POSTER_AT_S) or _ffmpeg(video, tmp, 0) or _thumbnailer(video, tmp)
        if not ok:
            return None
        os.replace(tmp, out)                   # (whole or not at all: another process may be reading)
        return out
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def still(path: str):
    """A picture for `path`: its poster for a video, the path itself otherwise."""
    return poster(path) if is_video(path) else path


def forget_others(keep=()) -> None:
    """Drop posters of videos no longer used (a few MB each), keeping `keep`'s."""
    folder = _cache()
    keep = {os.path.basename(poster_path(v)) for v in keep if v}
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        if name.endswith(".jpg") and name not in keep:
            try:
                os.unlink(os.path.join(folder, name))
            except OSError:
                pass
