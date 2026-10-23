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
# ...and a long fade can still be black there (Vini: the lock and login
# screens and Mission Control's desktops were black): later moments are
# tried, the first one bright enough is kept, else the brightest
POSTER_TRIES = (0.1, 0.25, 0.5)        # shares of the video's length
DARK_MEAN = 18                         # mean brightness (0-255) under which a frame counts as black
POSTER_VERSION = 2                     # bump: posters made the old way are made again
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
    key = hashlib.sha1(f"{os.path.abspath(video)}\0{stamp}\0{POSTER_VERSION}".encode()).hexdigest()[:20]
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


def duration(video: str) -> float:
    """The video's length in seconds (ffprobe), 0 when unknown."""
    exe = shutil.which("ffprobe")
    if not exe:
        return 0.0
    try:
        out = subprocess.run([exe, "-v", "error", "-show_entries", "format=duration", "-of",
                              "default=noprint_wrappers=1:nokey=1", video], capture_output=True, text=True,
                             timeout=TIMEOUT_S).stdout.strip()
        return max(0.0, float(out))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0.0


def brightness(path: str) -> float:
    """A picture's mean brightness 0-255 (255 when it can't be measured: kept)."""
    try:
        from PIL import Image, ImageStat
        with Image.open(path) as img:
            img.draft("L", (64, 64))                      # (a JPEG decoded small: quick)
            return ImageStat.Stat(img.convert("L").resize((64, 64))).mean[0]
    except Exception:
        return 255.0


def _frame(video: str, out: str) -> bool:
    """A frame that isn't black, into `out`: at POSTER_AT_S, else later on."""
    if not (_ffmpeg(video, out, POSTER_AT_S) or _ffmpeg(video, out, 0)):
        return _thumbnailer(video, out)
    if brightness(out) >= DARK_MEAN:
        return True
    length = duration(video)
    best, best_b = out + ".best.jpg", brightness(out)
    os.replace(out, best)
    try:
        for share in POSTER_TRIES if length > 0 else ():
            if _ffmpeg(video, out, length * share):
                b = brightness(out)
                if b >= DARK_MEAN:
                    return True
                if b > best_b:
                    os.replace(out, best)
                    best_b = b
        os.replace(best, out)                             # all dark: the brightest
        return True
    finally:
        if os.path.exists(best):
            os.unlink(best)


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
        ok = _frame(video, tmp)
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
