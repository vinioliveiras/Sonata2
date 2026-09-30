"""Music library: every audio file under ~/Music (the XDG music folder),
its tags, play counts and the user's playlists.

The tags are cached in ~/.cache/sonata2/music-library.json keyed by path,
with each file's mtime and size: a rescan only reads files that are new or
changed (`scan` is blocking -- run it through run_async). Cover art is
written once per picture to ~/.cache/sonata2/music-art/<sha1>.<ext> (the
file:// MPRIS hands to the menu bar) with a small thumbnail beside it for
the album grid. Play counts and playlists are user data:
~/.local/share/sonata2/music/{plays,playlists}.json.

No GTK here (GdkPixbuf only, lazily, to make thumbnails)."""
import hashlib
import json
import os
import time
from collections import Counter

from gi.repository import GLib

from . import tags

CACHE_VERSION = 2
FOLDER_COVERS = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png", "album.jpg",
                 "Cover.jpg", "Folder.jpg", "AlbumArt.jpg")
THUMB = 360                     # px: 180 px cards at 2x
UNKNOWN_ARTIST, UNKNOWN_ALBUM = "Unknown Artist", "Unknown Album"


def music_dir() -> str:
    from .. import userdirs
    d = userdirs.special(GLib.UserDirectory.DIRECTORY_MUSIC)
    return d or os.path.join(GLib.get_home_dir(), "Music")


def cache_path() -> str:
    return os.path.join(GLib.get_user_cache_dir(), "sonata2", "music-library.json")


def art_dir() -> str:
    return os.path.join(GLib.get_user_cache_dir(), "sonata2", "music-art")


def data_dir() -> str:
    return os.path.join(GLib.get_user_data_dir(), "sonata2", "music")


def _load_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def is_audio(name: str) -> bool:
    return name.lower().endswith(tags.EXTS) and not name.startswith(".")


# -- cover art ----------------------------------------------------------------------------
def save_art(data: bytes, mime: str, folder: str = None) -> str:
    """Write a cover once (named by its hash); returns its path."""
    folder = folder or art_dir()
    ext = ".png" if "png" in (mime or "") or data[:4] == b"\x89PNG" else ".jpg"
    path = os.path.join(folder, hashlib.sha1(data).hexdigest() + ext)
    if not os.path.exists(path):
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    return path


def thumbnail(src: str) -> str:
    """A THUMB px copy of a cover next to it (the grid decodes small files);
    the cover itself when it's small already or GdkPixbuf can't read it."""
    if not src:
        return ""
    root, _ext = os.path.splitext(src)
    dst = f"{root}-{THUMB}.png"
    if os.path.exists(dst):
        return dst
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        w, h = GdkPixbuf.Pixbuf.get_file_info(src)[1:]
        if max(w, h) <= THUMB:
            return src
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(src, THUMB, THUMB, True)
        pb.savev(dst, "png", [], [])
        return dst
    except Exception:
        return src


def _folder_cover(folder: str, cache: dict) -> str:
    if folder not in cache:
        cache[folder] = next((os.path.join(folder, n) for n in FOLDER_COVERS
                              if os.path.isfile(os.path.join(folder, n))), "")
    return cache[folder]


# -- reading one file ---------------------------------------------------------------------
def read_track(path: str, root: str = None, st=None, art_folder: str = None, folder_covers: dict = None) -> dict:
    """A library entry for one file (blocking: reads the tags)."""
    st = st or os.stat(path)
    t = tags.read(path)
    cover = t.pop("cover", None)
    art = ""
    if cover and cover[0]:
        try:
            art = save_art(cover[0], cover[1], art_folder)
        except OSError:
            art = ""
    if not art:
        art = _folder_cover(os.path.dirname(path), folder_covers if folder_covers is not None else {})
    # no tags: Artist/Album/NN Title.ext, the iTunes folder layout
    parts = os.path.relpath(path, root).split(os.sep) if root and path.startswith(root) else []
    if not t["album"]:
        t["album"] = parts[-2] if len(parts) >= 2 else UNKNOWN_ALBUM
    if not t["artist"]:
        t["artist"] = t["album_artist"] or (parts[-3] if len(parts) >= 3 else UNKNOWN_ARTIST)
    t.update(path=path, mtime=int(st.st_mtime), size=st.st_size, art=art,
             thumb=thumbnail(art) if art else "", added=time.time())
    return t


def scan(root: str, old: dict, art_folder: str = None) -> dict:
    """Every audio file under root -> entry, re-reading only new/changed
    files (mtime or size differ from `old`). Blocking."""
    new, covers = {}, {}
    for folder, dirs, files in os.walk(root, followlinks=True):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not is_audio(name):
                continue
            path = os.path.join(folder, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            prev = old.get(path)
            if prev and prev.get("mtime") == int(st.st_mtime) and prev.get("size") == st.st_size:
                new[path] = prev
                continue
            entry = read_track(path, root, st, art_folder, covers)
            if prev:
                entry["added"] = prev.get("added", entry["added"])
                if not entry.get("duration") and prev.get("duration"):
                    entry["duration"] = prev["duration"]
            new[path] = entry
    return new


def album_key(t: dict) -> str:
    """Songs of one album: same album title and album artist -- or, without
    an album artist, the same folder (a compilation keeps together)."""
    who = t.get("album_artist") or os.path.dirname(t.get("path") or "")
    return (who + "\x00" + (t.get("album") or "")).casefold()


def track_sort_key(t: dict):
    return (t.get("disc") or 0, t.get("track") or 0, (t.get("title") or "").casefold())


def group_albums(tracks) -> list:
    """Albums (dicts: key, title, artist, year, genre, art, thumb, added,
    tracks) sorted by title."""
    groups = {}
    for t in tracks:
        groups.setdefault(album_key(t), []).append(t)
    out = []
    for key, ts in groups.items():
        ts.sort(key=track_sort_key)
        artists = Counter(t.get("artist") or "" for t in ts)
        artist = next((t["album_artist"] for t in ts if t.get("album_artist")), "")
        if not artist:
            artist = artists.most_common(1)[0][0] if len(artists) == 1 or len(ts) < 3 else "Various Artists"
        art = next((t for t in ts if t.get("art")), {})
        out.append({"key": key, "title": ts[0].get("album") or UNKNOWN_ALBUM, "artist": artist,
                    "year": max((t.get("year") or 0) for t in ts),
                    "genre": Counter(t.get("genre") for t in ts if t.get("genre")).most_common(1)[0][0]
                    if any(t.get("genre") for t in ts) else "",
                    "art": art.get("art", ""), "thumb": art.get("thumb") or art.get("art", ""),
                    "added": max(t.get("added", 0) for t in ts), "tracks": ts})
    out.sort(key=lambda a: (a["title"].casefold(), a["artist"].casefold()))
    return out


def artist_of(t: dict) -> str:
    return t.get("album_artist") or t.get("artist") or UNKNOWN_ARTIST


class Library:
    """The library of one music folder. `tracks`: path -> entry."""

    def __init__(self, root: str = None, cache: str = None, data: str = None, art: str = None):
        self.root = root or music_dir()
        self.cache = cache or cache_path()
        self.data = data or data_dir()
        self.art = art or art_dir()
        self.tracks = {}
        self.plays = _load_json(os.path.join(self.data, "plays.json"), {})
        self.playlists = _load_json(os.path.join(self.data, "playlists.json"), [])
        self.playlists = [p for p in self.playlists if isinstance(p, dict) and "name" in p]
        self._monitors = []

    # cache ---------------------------------------------------------------------------------
    def load_cache(self) -> bool:
        c = _load_json(self.cache, {})
        if c.get("version") != CACHE_VERSION or c.get("root") != self.root:
            return False
        self.tracks = {p: t for p, t in c.get("tracks", {}).items() if isinstance(t, dict)}
        return True

    def save_cache(self) -> None:
        try:
            _save_json(self.cache, {"version": CACHE_VERSION, "root": self.root, "tracks": self.tracks})
        except OSError:
            pass

    def rescan(self) -> dict:
        """Blocking: the new tracks dict (apply it with `apply`)."""
        if not os.path.isdir(self.root):
            return {}
        return scan(self.root, dict(self.tracks), self.art)

    def apply(self, tracks: dict) -> bool:
        """Take a scan's result; True when something changed."""
        if tracks is None:
            return False
        changed = tracks.keys() != self.tracks.keys() or any(
            tracks[p] is not self.tracks.get(p) and tracks[p] != self.tracks.get(p) for p in tracks)
        self.tracks = tracks
        if changed:
            self.save_cache()
        return changed

    def set_duration(self, path: str, seconds: float) -> None:
        t = self.tracks.get(path)
        if t is not None and not t.get("duration") and seconds > 0:
            t["duration"] = seconds
            self.save_cache()

    # queries -------------------------------------------------------------------------------
    def songs(self) -> list:
        return sorted(self.tracks.values(), key=lambda t: ((t.get("title") or "").casefold(), t["path"]))

    def albums(self) -> list:
        return group_albums(self.tracks.values())

    def artists(self) -> list:
        return sorted({artist_of(t) for t in self.tracks.values()}, key=str.casefold)

    def recent_albums(self, limit: int = 60) -> list:
        return sorted(self.albums(), key=lambda a: -a["added"])[:limit]

    # plays ---------------------------------------------------------------------------------
    def play_count(self, path: str) -> int:
        return int(self.plays.get(path, 0))

    def add_play(self, path: str) -> int:
        self.plays[path] = self.play_count(path) + 1
        try:
            _save_json(os.path.join(self.data, "plays.json"), self.plays)
        except OSError:
            pass
        return self.plays[path]

    # playlists -----------------------------------------------------------------------------
    def save_playlists(self) -> None:
        try:
            _save_json(os.path.join(self.data, "playlists.json"), self.playlists)
        except OSError:
            pass

    def playlist(self, name: str):
        return next((p for p in self.playlists if p["name"] == name), None)

    def new_playlist(self, name: str = "Untitled Playlist", paths=()) -> dict:
        names = {p["name"] for p in self.playlists}
        base, n = name, 2
        while name in names:
            name = f"{base} {n}"
            n += 1
        p = {"name": name, "tracks": list(paths)}
        self.playlists.append(p)
        self.save_playlists()
        return p

    def add_to_playlist(self, name: str, paths) -> None:
        p = self.playlist(name)
        if p is not None:
            p["tracks"].extend(paths)
            self.save_playlists()

    def remove_from_playlist(self, name: str, indices) -> None:
        p = self.playlist(name)
        if p is not None:
            for i in sorted(set(indices), reverse=True):
                if 0 <= i < len(p["tracks"]):
                    del p["tracks"][i]
            self.save_playlists()

    def rename_playlist(self, old: str, new: str) -> None:
        p = self.playlist(old)
        if p is not None and new and not self.playlist(new):
            p["name"] = new
            self.save_playlists()

    def delete_playlist(self, name: str) -> None:
        self.playlists = [p for p in self.playlists if p["name"] != name]
        self.save_playlists()

    def playlist_tracks(self, name: str) -> list:
        p = self.playlist(name)
        return [self.tracks[x] for x in (p["tracks"] if p else []) if x in self.tracks]

    # watching ------------------------------------------------------------------------------
    def watch(self, on_change) -> None:
        """on_change() when files come and go in the music folder or its
        top-level folders (Gio.FileMonitor is not recursive)."""
        from gi.repository import Gio
        self.unwatch()
        dirs = [self.root]
        try:
            dirs += [os.path.join(self.root, d) for d in os.listdir(self.root)
                     if not d.startswith(".") and os.path.isdir(os.path.join(self.root, d))][:256]
        except OSError:
            return
        for d in dirs:
            try:
                m = Gio.File.new_for_path(d).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            except GLib.Error:
                continue
            m.set_rate_limit(2000)
            m.connect("changed", lambda *_a: on_change())
            self._monitors.append(m)

    def unwatch(self) -> None:
        for m in self._monitors:
            m.cancel()
        self._monitors = []
