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
