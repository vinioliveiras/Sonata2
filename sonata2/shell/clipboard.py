"""Clipboard history for the menu bar (a Linux extra macOS doesn't have).

Watches the Wayland clipboard with `wl-paste --watch` (wl-clipboard,
wlr-data-control), keeps the last text copies in memory only (never on
disk), and puts a chosen one back with `wl-copy`."""
import shutil
import subprocess
import threading

from gi.repository import GLib

KEEP = 15
MAX_BYTES = 64 * 1024        # larger copies (a whole file...) are not kept


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
            self._proc = subprocess.Popen(["wl-paste", "--type", "text", "--watch", "sh", "-c", "cat; printf '\\0'"],
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
                if 0 < len(item) <= MAX_BYTES:
                    text = item.decode("utf-8", "replace")
                    GLib.idle_add(self._add, text)
            if len(buf) > MAX_BYTES * 4:
                buf = b""

    def _add(self, text):
        if text.strip():
            self.items = [text] + [t for t in self.items if t != text]
            del self.items[KEEP:]
            for cb in list(self.listeners):
                cb()
        return False

    def copy(self, text: str) -> None:
        try:
            p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
            p.stdin.write(text.encode())
            p.stdin.close()
        except OSError:
            pass

    def clear(self) -> None:
        self.items = []
        for cb in list(self.listeners):
            cb()
