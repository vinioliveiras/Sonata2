"""Finder search: every file under a folder whose name contains all the
typed words (case- and accent-insensitive), found by a background thread
and delivered in batches so the first hits show at once. Hidden folders
and files are skipped (like Spotlight). New searches cancel old ones."""
import os
import threading
import unicodedata

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib  # noqa: E402

from .folder import ATTRS, sort_key  # noqa: E402

LIMIT = 2000
BATCH = 50
SKIP = {"node_modules", "__pycache__", ".git", "proc", "sys"}


def fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))


class Search:
    def __init__(self):
        self._token = None

    def cancel(self) -> None:
        if self._token:
            self._token.set()
            self._token = None

    def start(self, root: str, query: str, on_batch, on_done) -> None:
        """on_batch([Gio.FileInfo]) and on_done(truncated: bool) run on the main loop."""
        self.cancel()
        words = fold(query).split()
        if not words:
            return
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
                        if e.is_dir(follow_symlinks=False) and e.name not in SKIP:
                            stack.append(e.path)
                    except OSError:
                        pass
                    n = fold(e.name)
                    if all(w in n for w in words):
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
