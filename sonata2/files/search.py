"""Finder search: every file under a folder whose name contains all the
typed words (case- and accent-insensitive), found by a background thread
and delivered in batches so the first hits show at once. Hidden folders
and files are skipped (like Spotlight). New searches cancel old ones.

Finder's search criteria too (Vini): a kind (folders, documents, pictures,
videos, music, archives), when it was last modified (today, 7 / 30 days,
this year) and the words in the file's contents as well as its name (text
files up to CONTENT_MAX)."""
import mimetypes
import os
import threading
import time
import unicodedata

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from .folder import ATTRS, sort_key  # noqa: E402

LIMIT = 2000
BATCH = 50
SKIP = {"node_modules", "__pycache__", ".git", "proc", "sys"}
CONTENT_MAX = 4 << 20
KINDS = (("any", "Any Kind"), ("folder", "Folders"), ("document", "Documents"), ("image", "Pictures"),
         ("video", "Movies"), ("audio", "Music"), ("archive", "Archives"))
DATES = (("any", "Any Time", 0), ("today", "Today", 0), ("week", "Last 7 Days", 7), ("month", "Last 30 Days", 30),
         ("year", "This Year", 0))
_DOC_TYPES = ("text/", "application/pdf", "application/msword", "application/vnd.", "application/rtf",
              "application/x-tex", "application/json", "application/xml")
_ARCHIVES = (".zip", ".tar", ".gz", ".tgz", ".xz", ".bz2", ".zst", ".7z", ".rar")


def kind_of(name: str, is_dir: bool) -> str:
    if is_dir:
        return "folder"
    low = name.lower()
    if low.endswith(_ARCHIVES):
        return "archive"
    mt = mimetypes.guess_type(name)[0] or ""
    for k in ("image", "video", "audio"):
        if mt.startswith(k + "/"):
            return k
    if mt.startswith(_DOC_TYPES):
        return "document"
    return "other"


def since(date_key: str, now: float = None) -> float:
    """The oldest modification time `date_key` keeps (0: any)."""
    now = time.time() if now is None else now
    if date_key == "today":
        lt = time.localtime(now)
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if date_key == "year":
        return time.mktime((time.localtime(now).tm_year, 1, 1, 0, 0, 0, 0, 0, -1))
    days = next((d for k, _l, d in DATES if k == date_key), 0)
    return now - days * 86400 if days else 0


def contents_match(path: str, words) -> bool:
    """All the words in a text file's contents (binary and big files: no)."""
    try:
        if os.path.getsize(path) > CONTENT_MAX:
            return False
        with open(path, "rb") as f:
            data = f.read(CONTENT_MAX)
    except OSError:
        return False
    if b"\0" in data[:4096]:
        return False                                      # binary
    text = fold(data.decode("utf-8", errors="ignore"))
    return all(w in text for w in words)


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))


class Search:
    def __init__(self):
        self._token = None

    def cancel(self) -> None:
        if self._token:
            self._token.set()
            self._token = None

    def start(self, root: str, query: str, on_batch, on_done, kind: str = "any", date: str = "any",
              contents: bool = False) -> None:
        """on_batch([Gio.FileInfo]) and on_done(truncated: bool) run on the main loop.
        kind / date: KINDS / DATES keys; contents: the words in the file too."""
        self.cancel()
        words = fold(query).split()
        if not words:
            return
        oldest = since(date)
        token = self._token = threading.Event()

        def deliver(items):
            if not token.is_set():
                on_batch(items)
            return False

        def run():
            found, batch = 0, []
            stack = [root]
            while stack and not token.is_set():
                d = stack.pop()
                try:
                    with os.scandir(d) as it:
                        entries = list(it)
                except OSError:
                    continue
                for e in entries:
                    if e.name.startswith(".") or e.name.endswith("~"):
                        continue
                    try:
                        is_dir = e.is_dir(follow_symlinks=False)
                        if is_dir and e.name not in SKIP:
                            stack.append(e.path)
                    except OSError:
                        is_dir = False
                    if kind != "any" and kind_of(e.name, is_dir) != kind:
                        continue
                    if oldest:
                        try:
                            if e.stat(follow_symlinks=False).st_mtime < oldest:
                                continue
                        except OSError:
                            continue
                    n = fold(e.name)
                    if all(w in n for w in words) or (contents and not is_dir and e.is_file()
                                                       and contents_match(e.path, words)):
                        f = Gio.File.new_for_path(e.path)
                        try:
                            info = f.query_info(ATTRS, Gio.FileQueryInfoFlags.NONE, None)
                        except GLib.Error:
                            continue
                        info.set_attribute_object("sonata::file", f)
                        sort_key(info)
                        batch.append(info)
                        found += 1
                        if len(batch) >= BATCH:
                            GLib.idle_add(deliver, batch)
                            batch = []
                        if found >= LIMIT:
                            stack = []
                            break
            if batch:
                GLib.idle_add(deliver, batch)
            if not token.is_set():
                GLib.idle_add(lambda: (on_done(found >= LIMIT), False)[1])
        threading.Thread(target=run, daemon=True).start()
