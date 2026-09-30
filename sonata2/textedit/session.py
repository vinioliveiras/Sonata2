"""TextEdit's saved session (like macOS state restoration / Sublime's hot
exit): every window and tab, with the text of untitled and unsaved
documents, so quitting, closing the last window or rebooting loses nothing.

    <data>/sonata2/textedit/session.json     {"version": 1, "windows": [window, ...]}
    <data>/sonata2/textedit/buffers/<id>.txt  text of a tab with unsaved changes (UTF-8)

window = {"width", "height", "maximized", "active": tab index, "tabs": [tab, ...]}
tab    = {"id", "uri" (None: untitled), "title", "backup" (bool: buffers/<id>.txt holds
          its text), "cursor" (char offset), "top_line", "encoding", "bom", "newline",
          "readonly"}
"""
import json
import os
import re

from gi.repository import GLib

VERSION = 1
_ID = re.compile(r"^[0-9a-f]{8,64}$")


def data_dir() -> str:
    return os.path.join(GLib.get_user_data_dir(), "sonata2", "textedit")


def _buffers() -> str:
    return os.path.join(data_dir(), "buffers")


def _atomic_write(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def load() -> list:
    """The saved windows ([] when there is no usable session)."""
    try:
        with open(os.path.join(data_dir(), "session.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    wins = data.get("windows") if isinstance(data, dict) else None
    return [w for w in wins if isinstance(w, dict) and isinstance(w.get("tabs"), list)] if isinstance(wins, list) \
        else []


def save(windows: list) -> None:
    """Write session.json and delete the buffer files no tab refers to."""
    _atomic_write(os.path.join(data_dir(), "session.json"),
                  json.dumps({"version": VERSION, "windows": windows}, indent=1).encode("utf-8"))
    keep = {t.get("id") for w in windows for t in w.get("tabs", []) if t.get("backup")}
    try:
        names = os.listdir(_buffers())
    except OSError:
        return
    for n in names:
        stem = n[:-4] if n.endswith(".txt") else None
        if stem and _ID.match(stem) and stem not in keep:
            try:
                os.remove(os.path.join(_buffers(), n))
            except OSError:
                pass


def write_buffer(doc_id: str, text: str) -> None:
    if _ID.match(doc_id):
        _atomic_write(os.path.join(_buffers(), doc_id + ".txt"), text.encode("utf-8", errors="surrogatepass"))


def read_buffer(doc_id: str):
    if not _ID.match(doc_id or ""):
        return None
    try:
        with open(os.path.join(_buffers(), doc_id + ".txt"), "rb") as f:
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
