"""Videos' editing (QuickTime's Trim; Crop): the commands and the work
behind the bars (trimbar.py), no GTK widgets here.

The original is never touched: the result is a new file beside it
("Holiday (Trimmed).mp4", "Holiday (Cropped).mp4"), then opened in the
same window. ffmpeg does the work, re-encoding so the cut is exact to the
frame (copying streams cuts at the nearest keyframe, seconds off).

    out = output_path(path, "Trimmed")
    job = Export(trim_command(path, out, 12.5, 40.0), 27.5, on_progress, on_done)
    thumbnails(path, duration, 12, 48, on_thumb)       # the filmstrip's frames
"""
import os
import shutil
import subprocess
import threading

from gi.repository import GLib

# containers that take H.264 + AAC as they are; any other movie becomes .mp4
VIDEO_KEEP = (".mp4", ".m4v", ".mov", ".mkv")
# a song keeps its format: the encoder for it
AUDIO_CODECS = {".mp3": ["-c:a", "libmp3lame", "-q:a", "2"], ".flac": ["-c:a", "flac"],
                ".ogg": ["-c:a", "libvorbis", "-q:a", "6"], ".oga": ["-c:a", "libvorbis", "-q:a", "6"],
                ".opus": ["-c:a", "libopus", "-b:a", "160k"], ".m4a": ["-c:a", "aac", "-b:a", "256k"],
                ".aac": ["-c:a", "aac", "-b:a", "256k"], ".wav": ["-c:a", "pcm_s16le"]}
VIDEO_CODECS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
MIN_LENGTH = 0.5            # s: the shortest trim


def available() -> bool:
    return bool(shutil.which("ffmpeg"))


def output_path(path: str, what: str, audio: bool = False) -> str:
    """Beside the original, never over a file: "name (Trimmed).ext", then
    "name (Trimmed 2).ext"..."""
    folder, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    ext = ext.lower()
    if not audio and ext not in VIDEO_KEEP:
        ext = ".mp4"
    if audio and ext not in AUDIO_CODECS:
        ext = ".m4a"
    n = 1
    while True:
        tag = what if n == 1 else f"{what} {n}"
        out = os.path.join(folder, f"{stem} ({tag}){ext}")
        if not os.path.exists(out):
            return out
        n += 1


def _codecs(dst: str, audio: bool) -> list:
    if audio:
        return ["-vn", "-map", "0:a:0"] + AUDIO_CODECS.get(os.path.splitext(dst)[1].lower(), AUDIO_CODECS[".m4a"])
    return ["-map", "0:v:0", "-map", "0:a?"] + VIDEO_CODECS


def _base(src: str) -> list:
    return ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats"]


def clamp_range(start: float, end: float, duration: float) -> tuple:
    """A trim inside the movie, at least MIN_LENGTH long."""
    duration = max(duration, MIN_LENGTH)
    start = max(0.0, min(start, duration - MIN_LENGTH))
    end = max(start + MIN_LENGTH, min(end, duration))
    return start, end


def trim_command(src: str, dst: str, start: float, end: float, audio: bool = False) -> list:
    """Keep start..end (seconds). -ss after -i: exact to the frame."""
    return _base(src) + ["-i", src, "-ss", f"{start:.3f}", "-to", f"{end:.3f}"] + _codecs(dst, audio) + [dst]


def crop_command(src: str, dst: str, x: int, y: int, w: int, h: int) -> list:
    """Keep the w x h rectangle at (x, y) of the picture (even sizes: H.264)."""
    w, h = max(2, w - w % 2), max(2, h - h % 2)
    return _base(src) + ["-i", src, "-vf", f"crop={w}:{h}:{x}:{y}"] + _codecs(dst, False) + [dst]


def progress_of(line: str, duration: float):
    """ffmpeg's -progress lines: 0..1 from out_time_us / out_time_ms, else None."""
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms") and value.lstrip("-").isdigit() and duration > 0:
        return max(0.0, min(1.0, int(value) / 1e6 / duration))
    return None


class Export:
    """ffmpeg in the background; on_progress(0..1) and on_done(ok, message)
    on the main loop. cancel() stops it and removes the half-written file."""

    def __init__(self, cmd: list, duration: float, on_progress, on_done):
        self.cmd, self.duration = cmd, duration
        self.on_progress, self.on_done = on_progress, on_done
        self.dst = cmd[-1]
        self.proc = None
        self.cancelled = False
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        try:
            self.proc = subprocess.Popen(self.cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
        except OSError as e:
            GLib.idle_add(self._finish, False, str(e))
            return
        for line in self.proc.stdout:
            p = progress_of(line, self.duration)
            if p is not None:
                GLib.idle_add(self.on_progress, p)
        err = self.proc.stderr.read()
        code = self.proc.wait()
        ok = code == 0 and not self.cancelled and os.path.exists(self.dst)
        GLib.idle_add(self._finish, ok, (err.strip().splitlines() or [""])[-1])

    def _finish(self, ok: bool, message: str) -> bool:
        if not ok:
            try:
                os.unlink(self.dst)                    # nothing half-made left beside the original
            except OSError:
                pass
        if not self.cancelled:
            self.on_done(ok, message)
        return False

    def cancel(self) -> None:
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()


def thumbnail_times(duration: float, count: int) -> list:
    """The middle of each of `count` equal parts of the movie."""
    count = max(1, count)
    return [duration * (i + 0.5) / count for i in range(count)]


def thumbnails(path: str, duration: float, count: int, height: int, on_thumb, stop=lambda: False) -> None:
    """The filmstrip's frames, one by one in the background: on_thumb(i,
    png bytes) on the main loop. stop() true: no more."""
    def work():
        for i, t in enumerate(thumbnail_times(duration, count)):
            if stop():
                return
            try:
                r = subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{t:.3f}",
                                    "-i", path, "-frames:v", "1", "-vf", f"scale=-2:{height * 2}",
                                    "-f", "image2pipe", "-vcodec", "png", "-"],
                                   capture_output=True, timeout=20)
            except (OSError, subprocess.SubprocessError):
                return
            if r.stdout:
                GLib.idle_add(on_thumb, i, r.stdout)
    threading.Thread(target=work, daemon=True).start()


# -- crop geometry (video pixels) -------------------------------------------------------------
RATIOS = (("free", "Free", None), ("16:9", "16:9", 16 / 9), ("4:3", "4:3", 4 / 3), ("1:1", "1:1", 1.0),
          ("9:16", "9:16", 9 / 16))
MIN_CROP = 32               # px of the movie: the smallest crop


def video_rect(iw: int, ih: int, w: float, h: float) -> tuple:
    """Where a movie iw x ih shows in a w x h area, letterboxed (Gtk.Picture
    CONTAIN): (x, y, scale)."""
    s = min(w / max(1, iw), h / max(1, ih))
    return (w - iw * s) / 2, (h - ih * s) / 2, s


def ratio_rect(iw: int, ih: int, ratio) -> tuple:
    """The biggest rectangle of `ratio` (w / h; None: the whole picture), centred."""
    if not ratio:
        return 0, 0, iw, ih
    w, h = iw, iw / ratio
    if h > ih:
        w, h = ih * ratio, ih
    w, h = int(round(w)), int(round(h))
    return (iw - w) // 2, (ih - h) // 2, w, h


def drag_crop(rect: tuple, part: str, dx: float, dy: float, iw: int, ih: int, ratio=None) -> tuple:
    """The crop `rect` (x, y, w, h) with `part` dragged by (dx, dy): "move",
    an edge ("l", "r", "t", "b") or a corner ("tl", "tr", "bl", "br"). Stays
    inside the picture, at least MIN_CROP, and keeps `ratio` when set (the
    corner or edge opposite the one dragged stays put)."""
    x, y, w, h = rect
    if part == "move":
        return (int(max(0, min(iw - w, x + dx))), int(max(0, min(ih - h, y + dy))), w, h)
    left, top, right, bottom = x, y, x + w, y + h
    if "l" in part:
        left = min(max(0, left + dx), right - MIN_CROP)
    if "r" in part:
        right = max(min(iw, right + dx), left + MIN_CROP)
    if "t" in part:
        top = min(max(0, top + dy), bottom - MIN_CROP)
    if "b" in part:
        bottom = max(min(ih, bottom + dy), top + MIN_CROP)
    if ratio:
        nw, nh = right - left, bottom - top
        if part in ("t", "b"):
            nw = nh * ratio
        elif part in ("l", "r"):
            nh = nw / ratio
        else:
            nh = nw / ratio                          # a corner: the width leads
        # the space there is from the corner / edge that stays
        ax = right if "l" in part else left
        ay = bottom if "t" in part else top
        room_w = ax if "l" in part else (iw - ax if part not in ("t", "b") else min(x + w / 2, iw - x - w / 2) * 2)
        room_h = ay if "t" in part else (ih - ay if part not in ("l", "r") else min(y + h / 2, ih - y - h / 2) * 2)
        k = min(1.0, room_w / max(1, nw), room_h / max(1, nh))
        nw, nh = max(MIN_CROP, nw * k), max(MIN_CROP / ratio if ratio < 1 else MIN_CROP, nh * k)
        if part in ("t", "b"):
            left = x + w / 2 - nw / 2
        else:
            left = ax - nw if "l" in part else ax
        if part in ("l", "r"):
            top = y + h / 2 - nh / 2
        else:
            top = ay - nh if "t" in part else ay
        right, bottom = left + nw, top + nh
    left, top = max(0, left), max(0, top)
    right, bottom = min(iw, right), min(ih, bottom)
    return int(round(left)), int(round(top)), int(round(right - left)), int(round(bottom - top))


def crop_part(rect: tuple, px: float, py: float, grab: float) -> str:
    """What a press at (px, py) (video pixels) takes: a corner, an edge,
    "move" inside, or "" outside. grab: how close counts, in video pixels."""
    x, y, w, h = rect
    if not (x - grab <= px <= x + w + grab and y - grab <= py <= y + h + grab):
        return ""
    l, r = abs(px - x) <= grab, abs(px - (x + w)) <= grab
    t, b = abs(py - y) <= grab, abs(py - (y + h)) <= grab
    v = "t" if t else "b" if b else ""
    hz = "l" if l else "r" if r else ""
    return (v + hz) or "move"
