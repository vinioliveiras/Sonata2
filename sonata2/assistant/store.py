"""Assistant conversations, saved locally in the app's data folder
(GLib.get_user_data_dir()/sonata2-data/assistant/):

index.json             {"chats": [{id, title, created, modified}]}   (the sidebar)
chats/<id>.json        {"id", "title", "created", "modified",
                        "messages": [{"role": "user"|"assistant", "content": str | [blocks]}]}
                       blocks (the API's): text, tool_use (a file action), tool_result

Only the index is read at start; a conversation's messages are read when it
is opened (low memory with many long chats). Every write is atomic (a temp
file, then os.replace)."""
import json
import os
import time
import uuid

TITLE_LEN = 60          # a chat is titled after its first message, cut here


def data_dir() -> str:
    from .. import userdata
    return userdata.folder("assistant")


def _write(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def title_from(text: str) -> str:
    """The first line of a message, shortened: the chat's title."""
    line = " ".join(text.strip().split())
    if len(line) > TITLE_LEN:
        line = line[:TITLE_LEN].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return line or "New Chat"


class Store:
    def __init__(self, folder: str = None):
        self.folder = folder or data_dir()
        self.index_path = os.path.join(self.folder, "index.json")
        chats = _read(self.index_path).get("chats", [])
        self.chats = [c for c in chats if isinstance(c, dict) and c.get("id")]

    def _chat_path(self, cid: str) -> str:
        return os.path.join(self.folder, "chats", cid + ".json")

    def _save_index(self) -> None:
        _write(self.index_path, {"chats": self.chats})

    def get(self, cid: str):
        return next((c for c in self.chats if c["id"] == cid), None)

    def new_chat(self) -> dict:
        """A chat that is not saved until its first message."""
        now = time.time()
        return {"id": uuid.uuid4().hex[:12], "title": "New Chat", "created": now, "modified": now, "messages": []}

    def load(self, cid: str) -> dict:
        meta = self.get(cid) or {}
        data = _read(self._chat_path(cid))
        msgs = [m for m in data.get("messages", []) if isinstance(m, dict)
                and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), (str, list))]
        return {**meta, **{k: v for k, v in data.items() if k != "messages"}, "id": cid, "messages": msgs}

    def save(self, chat: dict) -> None:
        """Write the chat and bring it to the top of the index."""
        chat["modified"] = time.time()
        if chat.get("title") in (None, "", "New Chat"):
            first = next((m["content"] for m in chat["messages"]
                          if m["role"] == "user" and isinstance(m["content"], str)), "")
            chat["title"] = title_from(first)
        _write(self._chat_path(chat["id"]), chat)
        meta = {k: chat[k] for k in ("id", "title", "created", "modified")}
        self.chats = [meta] + [c for c in self.chats if c["id"] != chat["id"]]
        self._save_index()

    def rename(self, cid: str, title: str) -> None:
        meta = self.get(cid)
        title = " ".join(title.split())
        if meta is None or not title:
            return
        meta["title"] = title
        data = _read(self._chat_path(cid))
        if data:
            data["title"] = title
            _write(self._chat_path(cid), data)
        self._save_index()

    def delete(self, cid: str) -> None:
        self.chats = [c for c in self.chats if c["id"] != cid]
        try:
            os.remove(self._chat_path(cid))
        except OSError:
            pass
        self._save_index()
