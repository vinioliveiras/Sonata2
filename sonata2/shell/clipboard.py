"""Clipboard history for the menu bar (a Linux extra macOS doesn't have).

Watches the Wayland clipboard with `wl-paste --watch` (wl-clipboard,
wlr-data-control), keeps the last copies -- text and images -- in memory
only (never on disk), and puts a chosen one back with `wl-copy`."""
import base64
import hashlib
import shutil
import subprocess
import threading

from gi.repository import GLib

KEEP = 15
MAX_BYTES = 64 * 1024        # larger copies (a whole file...) are not kept
MAX_IMAGE = 16 * 1024 * 1024  # PNG bytes
KEEP_IMAGES = 5              # images are big: only the latest few
# for each copy: "I" + the PNG in base64, or "T" + the text; then a NUL
WATCH = ("cat >/dev/null; t=$(wl-paste --list-types 2>/dev/null); case \"$t\" in "
         "*image/png*) printf I; wl-paste --no-newline --type image/png 2>/dev/null | base64 -w0 ;; "
         "*) printf T; wl-paste --no-newline --type text 2>/dev/null ;; esac; printf '\\0'")


class ClipImage(str):
    """An image copy. Reads as its description ("Image · 800×600") wherever
    a text copy is shown; .png holds the picture, .texture() a Gdk.Texture."""

    def __new__(cls, png: bytes):
        w = h = 0
        if png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) >= 24:
            w, h = int.from_bytes(png[16:20], "big"), int.from_bytes(png[20:24], "big")
        obj = super().__new__(cls, f"Image · {w}×{h}" if w else "Image")
        obj.png, obj.size, obj.key = png, (w, h), hashlib.sha1(png).hexdigest()
        obj._texture = None
        return obj

    def __eq__(self, other):
        return isinstance(other, ClipImage) and other.key == self.key

    def __hash__(self):
        return hash(self.key)

    def texture(self, max_w: int = 320, max_h: int = 110):
        """A thumbnail at most max_w x max_h (made once, then kept)."""
        if self._texture is None:
            try:
                import gi
                gi.require_version("GdkPixbuf", "2.0")
                from gi.repository import Gdk, GdkPixbuf
                loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                w, h = self.size
                if w and h:
                    s = min(1.0, max_w / w, max_h / h)
                    loader.set_size(max(1, round(w * s)), max(1, round(h * s)))
                loader.write(self.png)
                loader.close()
                self._texture = Gdk.Texture.new_for_pixbuf(loader.get_pixbuf())
            except (GLib.Error, ValueError, ImportError):
                return None
        return self._texture


class History:
    def __init__(self):
        self.items = []          # newest first
        self.listeners = []
        self.available = bool(shutil.which("wl-paste") and shutil.which("wl-copy"))
        self._proc = None
        if self.available:
            threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        try:
            # each copy: the text, then a NUL as separator
            self._proc = subprocess.Popen(["wl-paste", "--watch", "sh", "-c", WATCH],
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except OSError:
            return
        buf = b""
        while True:
            chunk = self._proc.stdout.read1(65536) if hasattr(self._proc.stdout, "read1") else \
                self._proc.stdout.read(4096)
            if not chunk:
                return
            buf += chunk
            while b"\0" in buf:
                item, buf = buf.split(b"\0", 1)
                kind, data = item[:1], item[1:]
                if kind == b"I" and data:
                    try:
                        png = base64.b64decode(data)
                    except ValueError:
                        continue
                    if len(png) <= MAX_IMAGE:
                        GLib.idle_add(self._add, ClipImage(png))
                elif kind == b"T" and 0 < len(data) <= MAX_BYTES:
                    GLib.idle_add(self._add, data.decode("utf-8", "replace"))
            if len(buf) > MAX_IMAGE * 2:
                buf = b""

    def _add(self, text):
        if isinstance(text, ClipImage) or text.strip():
            self.items = [text] + [t for t in self.items if not (t == text and type(t) is type(text))]
            del self.items[KEEP:]
            images = [t for t in self.items if isinstance(t, ClipImage)]
            for old in images[KEEP_IMAGES:]:
                self.items.remove(old)
            for cb in list(self.listeners):
                cb()
        return False

    def copy(self, text: str) -> None:
        image = isinstance(text, ClipImage)
        try:
            p = subprocess.Popen(["wl-copy"] + (["--type", "image/png"] if image else []), stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            p.stdin.write(text.png if image else text.encode())
            p.stdin.close()
        except OSError:
            pass

    def clear(self) -> None:
        self.items = []
        for cb in list(self.listeners):
            cb()
