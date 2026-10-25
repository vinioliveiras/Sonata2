#!/usr/bin/env python3
"""The website's hero as a video: Sonata's desktop (menu bar, desktop icons,
Dock) over a video wallpaper, the glass blurring each frame as the
compositor would. The UI comes from a real capture (readme-shots.py
--layers: matted over black and white), the wallpaper from any video you
may publish.

    xvfb-run -a -s "-screen 0 1920x1200x24" python3 tools/hero-video.py VIDEO [--seconds 10] [--scene desktop-dark]

Writes docs/screenshots/desktop-video.mp4 (H.264: every browser plays it)
and desktop-video.jpg (the first frame: the poster)."""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots", "desktop-video")
W, H, FPS = 1920, 1080, 25
FADE_S = 0.6                     # the end fades into the start: a seamless loop on the page


def frost(img):
    """readme-shots.frost: the compositor's glass (blur + saturation)."""
    from PIL import ImageEnhance, ImageFilter
    return ImageEnhance.Color(img.filter(ImageFilter.GaussianBlur(18))).enhance(1.6)


def frames(video: str, seconds: float):
    """The video cover-fit to W x H, RGB frames (PIL images)."""
    from PIL import Image
    vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS}"
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-t", str(seconds), "-vf", vf,
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    size = W * H * 3
    while True:
        buf = p.stdout.read(size)
        if len(buf) < size:
            break
        yield Image.frombytes("RGB", (W, H), buf)
    p.wait()


def main(argv) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    video = argv[0]
    seconds = float(argv[argv.index("--seconds") + 1]) if "--seconds" in argv else 10.0
    scene = argv[argv.index("--scene") + 1] if "--scene" in argv else "desktop-dark"
    from PIL import Image
    work = tempfile.mkdtemp(prefix="sonata-hero-")
    try:
        layers = os.path.join(work, "layers")
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "readme-shots.py"), "--layers", scene, layers],
                           capture_output=True, text=True)
        if not os.path.exists(os.path.join(layers, "ui.png")):
            print(r.stdout + r.stderr, file=sys.stderr)
            return 1
        ui = Image.open(os.path.join(layers, "ui.png")).convert("RGBA")
        glass = Image.open(os.path.join(layers, "glass.png")).convert("L")
        box = glass.getbbox()
        # every frame, composed, as PNGs for the encoders (the loop's fade needs the first ones again)
        out_frames = []
        for i, f in enumerate(frames(video, seconds)):
            if box:
                region = f.crop(box)
                f.paste(frost(region), box[:2], glass.crop(box))
            f = f.convert("RGBA")
            f.alpha_composite(ui)
            path = os.path.join(work, f"f{i:05d}.png")
            f.convert("RGB").save(path, compress_level=1)
            out_frames.append(path)
        if not out_frames:
            print("no frames read from the video", file=sys.stderr)
            return 1
        n = int(FADE_S * FPS)
        if len(out_frames) > 2 * n:                     # the last n frames cross into the first n
            for k in range(n):
                a = Image.open(out_frames[len(out_frames) - n + k])
                b = Image.open(out_frames[k])
                Image.blend(a, b, (k + 1) / (n + 1)).save(out_frames[len(out_frames) - n + k], compress_level=1)
            for path in out_frames[:n]:                 # (shown at the end now)
                os.remove(path)
            out_frames = out_frames[n:]
            for j, path in enumerate(out_frames):
                os.rename(path, os.path.join(work, f"g{j:05d}.png"))
        else:
            for j, path in enumerate(out_frames):
                os.rename(path, os.path.join(work, f"g{j:05d}.png"))
        src = ["-framerate", str(FPS), "-i", os.path.join(work, "g%05d.png")]
        subprocess.run(["ffmpeg", "-v", "error", "-y", *src, "-c:v", "libx264", "-preset", "slow", "-crf", "24",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", OUT + ".mp4"], check=True)
        shutil.copy(os.path.join(work, "g00000.png"), OUT + ".png")
        Image.open(OUT + ".png").convert("RGB").save(OUT + ".jpg", quality=86, optimize=True, progressive=True)
        os.remove(OUT + ".png")
        for ext in (".mp4", ".jpg"):
            print(f"desktop-video{ext}: {os.path.getsize(OUT + ext) // 1024} KB")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
