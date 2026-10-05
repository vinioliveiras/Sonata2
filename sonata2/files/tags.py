"""Finder tags: Red, Orange, Yellow, Green, Blue, Purple, Gray on files and
folders. A tagged item shows its colours next to its name; right-click >
the coloured dots add or remove a tag; the sidebar's Tags list every item
with that tag (and a drop on one tags the files).

Where they're kept: the file's own extended attribute "user.xdg.tags" (the
freedesktop name KDE's Dolphin uses too, comma-separated), so a tag travels
with the file when it's moved or renamed on the disk. Tags another app set
that aren't one of the seven colours are kept as they were.

The sidebar's lists come from an index of what Files tagged (Spotlight's
job on a Mac): ~/.local/share/sonata/files-tags.json, {path: [tags]}; Files'
own moves and renames update it, and an item whose tag is gone (removed,
trashed) drops out when the list is read."""
import json
import os
import re
import threading

from gi.repository import GLib

XATTR = "user.xdg.tags"
GIO_ATTR = "xattr::xdg.tags"          # how Gio's enumerations hand it over (in folder.ATTRS)
PREFIX = "sonata:tag/"
COLORS = (("Red", "#ff3b30"), ("Orange", "#ff9500"), ("Yellow", "#ffcc00"), ("Green", "#34c759"),
          ("Blue", "#007aff"), ("Purple", "#af52de"), ("Gray", "#8e8e93"))
NAMES = tuple(n for n, _c in COLORS)
_lock = threading.Lock()


def index_path() -> str:
    return os.path.join(GLib.get_user_data_dir(), "sonata", "files-tags.json")


# -- reading and writing a file's tags ---------------------------------------------------------
def parse(text) -> list:
    """The tags in an attribute's text: "Red,Work" -> ["Red", "Work"]."""
    if not text:
        return []
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    out = []
    for t in text.split(","):
        t = t.strip()
        if t and t not in out:
            out.append(t)
    return out


def _unescape(s: str) -> bytes:
    """Gio hands xattrs over escaped: non-ASCII bytes (and backslash) as \\xNN."""
    return re.sub(rb"\\x([0-9a-fA-F]{2})", lambda m: bytes([int(m.group(1), 16)]), s.encode("latin-1", "replace"))


def of_info(info) -> list:
    """The tags of a listed item (Gio.FileInfo queried with folder.ATTRS)."""
    s = info.get_attribute_string(GIO_ATTR) if info.has_attribute(GIO_ATTR) else None
    return parse(_unescape(s)) if s else []


def colors(tag_list) -> list:
    """The colour tags among `tag_list`, in Finder's order: [(name, hex)]."""
    return [(n, c) for n, c in COLORS if n in tag_list]


def read(path: str) -> list:
    try:
        return parse(os.getxattr(path, XATTR, follow_symlinks=False))
    except OSError:
        return []


def write(path: str, tag_list) -> None:
    """Set a file's tags (none: the attribute goes). OSError when the disk
    can't keep them (FAT, some network shares)."""
    tag_list = parse(",".join(tag_list))
    if tag_list:
        os.setxattr(path, XATTR, ",".join(tag_list).encode("utf-8"), follow_symlinks=False)
    else:
        try:
            os.removexattr(path, XATTR, follow_symlinks=False)
        except OSError as e:
            if e.errno not in (61, 95):          # ENODATA (had none), ENOTSUP
                raise
    _index_set(path, tag_list)


def state(paths, tag: str) -> str:
    """"all", "some" or "none" of `paths` have `tag` (the menu's dots)."""
    have = [tag in read(p) for p in paths]
    return "all" if have and all(have) else "some" if any(have) else "none"


def toggle(paths, tag: str, on: bool) -> list:
    """Add `tag` to (on) or remove it from each file; [(path, OSError)] that failed."""
    failed = []
    for p in paths:
        cur = read(p)
        if on:
            new = cur if tag in cur else cur + [tag]
        else:
            new = [t for t in cur if t != tag]
        if new == cur:
            continue
        try:
            write(p, new)
        except OSError as e:
            failed.append((p, e))
    return failed


# -- the sidebar's lists -----------------------------------------------------------------------
def uri(tag: str) -> str:
    return PREFIX + tag


def tag_of(location) -> str:
    """The tag a sidebar location lists ("" when it isn't one)."""
    return location[len(PREFIX):] if isinstance(location, str) and location.startswith(PREFIX) else ""


def _load() -> dict:
    try:
        with open(index_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    p = index_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".new", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(p + ".new", p)


def _index_set(path, tag_list) -> None:
    with _lock:
        data = _load()
        if tag_list:
            data[path] = list(tag_list)
        elif data.pop(path, None) is None:
            return
        _save(data)


def tagged(tag: str) -> list:
    """Paths of the items with `tag` (checked on the disk; the gone ones leave the index)."""
    with _lock:
        data = _load()
        out, changed = [], False
        for path in list(data):
            now = read(path) if os.path.lexists(path) else []
            if now != data[path]:
                changed = True
                if now:
                    data[path] = now
                else:
                    del data[path]
            if tag in now:
                out.append(path)
        if changed:
            _save(data)
    return out


def moved(pairs) -> None:
    """Files moved or renamed items: [(Gio.File was, Gio.File now)] -- the
    index follows them (and what's inside a moved folder)."""
    pairs = [(a.get_path(), b.get_path()) for a, b in pairs if a.get_path() and b.get_path()]
    if not pairs:
        return
    with _lock:
        data = _load()
        if not data:
            return
        changed = False
        for was, now in pairs:
            for path in list(data):
                if path == was or path.startswith(was.rstrip("/") + "/"):
                    data[now + path[len(was):]] = data.pop(path)
                    changed = True
        if changed:
            _save(data)


# -- what they look like -----------------------------------------------------------------------
def _css() -> None:
    from .. import ui
    rules = "".join(f".fs-tag-{n.lower()} {{ background: {c}; }}\n" for n, c in COLORS)
    ui.register("""
.fs-tag { min-width: 8px; min-height: 8px; border-radius: 99px; }
.fs-tags { margin: 0 2px; }
.fs-tags .fs-tag + .fs-tag { margin-left: -2px; box-shadow: 0 0 0 1px %(content_bg)s; }
.fs-tag-pick { min-width: 0; min-height: 0; padding: 3px; border: none; box-shadow: none; background: none;
  border-radius: 99px; }
.fs-tag-pick .fs-tag { min-width: 14px; min-height: 14px; }
.fs-tag-pick:hover .fs-tag { box-shadow: 0 0 0 2px alpha(currentColor, 0.35); }
.fs-tag-pick image { -gtk-icon-size: 10px; color: white; }
.fs-tag-row { padding: 4px 8px; }
""" + rules, key="files-tags")


def dot(name: str, size: int = 8):
    from gi.repository import Gtk
    _css()
    d = Gtk.Box(css_classes=["fs-tag", "fs-tag-" + name.lower()], valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    d.set_size_request(size, size)
    return d


def dots_box():
    """The colours next to a name (empty: takes no room)."""
    from gi.repository import Gtk
    _css()
    return Gtk.Box(css_classes=["fs-tags"], valign=Gtk.Align.CENTER, visible=False)


def show(box, tag_list) -> None:
    """Put an item's colours in a dots_box()."""
    names = [n for n, _c in colors(tag_list)]
    if getattr(box, "_names", None) == names:
        return
    box._names = names
    while (c := box.get_first_child()) is not None:
        box.remove(c)
    for n in names:
        box.append(dot(n))
    box.set_visible(bool(names))


def menu_row(pop, states: dict, pick):
    """The context menu's row of colours (Finder): a click adds the tag to
    every selected item, or removes it when they all have it already.
    states: {tag: "all"|"some"|"none"}; pick(tag, on)."""
    from gi.repository import Gtk
    from ..ui import menu
    _css()
    row = Gtk.Box(spacing=2, css_classes=["fs-tag-row"], halign=Gtk.Align.START)
    for name, _c in COLORS:
        st = states.get(name, "none")
        b = Gtk.Button(css_classes=["fs-tag-pick"], can_focus=False,
                       tooltip_text=("Remove " if st == "all" else "Add ") + f"“{name}” tag")
        d = dot(name, 14)
        if st != "none":
            d.append(Gtk.Image(icon_name="object-select-symbolic" if st == "all" else "list-remove-symbolic",
                               hexpand=True, halign=Gtk.Align.CENTER))
        b.set_child(d)
        menu._one_click(b, lambda n=name, s=st: (pop.popdown(), pick(n, s != "all")))
        row.append(b)
    return row
