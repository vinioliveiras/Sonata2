"""Notes and Reminders data: notes.json and reminders.json in
GLib.get_user_data_dir()/sonata2-data/notes/, each rewritten atomically (a temp
file, then os.replace) after every change.

notes.json      {"folders": [{id, name}],
                 "notes": [{id, folder, body, created, modified, pinned, deleted}]}
reminders.json  {"lists": [{id, name, color}],
                 "reminders": [{id, list, title, notes, due, priority, flagged,
                                completed, completed_at, created}]}

Times are epoch seconds; a reminder's `due` is "YYYY-MM-DD" (all day) or
"YYYY-MM-DDTHH:MM" (local time). A deleted note keeps its folder and gets
`deleted` (the time): it shows in Recently Deleted for 30 days, then goes."""
import datetime
import json
import os
import time
import uuid

from . import markup

ALL = "all"                  # All Notes (every folder)
DELETED = "deleted"          # Recently Deleted
DEFAULT_FOLDER = "notes"     # "Notes", always there
DEFAULT_LIST = "reminders"   # "Reminders", always there
SMART = ("today", "scheduled", "all", "completed")
KEEP_DELETED = 30 * 86400
LIST_COLORS = ("blue", "red", "orange", "green", "purple", "pink", "teal", "indigo", "graphite")
ALL_DAY_HOUR = 9             # all-day reminders alert at 9:00 (macOS)


def data_dir() -> str:
    from .. import userdata
    return userdata.folder("notes")


def write_json(path: str, data) -> None:
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


def new_id() -> str:
    return uuid.uuid4().hex[:12]


# -- notes -----------------------------------------------------------------------------------
def note_title(note: dict) -> str:
    return markup.title_and_preview(note.get("body", ""))[0]


def sort_notes(notes: list) -> list:
    """Pinned first, then by date edited (newest first)."""
    return sorted(notes, key=lambda n: (not n.get("pinned"), -n.get("modified", 0)))


def search(notes: list, query: str) -> list:
    """Notes whose text contains every word of `query` (case-insensitive)."""
    words = (query or "").casefold().split()
    if not words:
        return list(notes)
    out = []
    for n in notes:
        text = "\n".join(markup.plain(n.get("body", ""))).casefold()
        if all(w in text for w in words):
            out.append(n)
    return out


# -- reminders ---------------------------------------------------------------------------------
def due_datetime(r: dict):
    """The moment a reminder is due (all-day: 9:00), or None."""
    due = r.get("due")
    if not due:
        return None
    try:
        if "T" in due:
            return datetime.datetime.strptime(due, "%Y-%m-%dT%H:%M")
        d = datetime.datetime.strptime(due, "%Y-%m-%d")
        return d.replace(hour=ALL_DAY_HOUR)
    except ValueError:
        return None


def due_date(r: dict):
    dt = due_datetime(r)
    return dt.date() if dt else None


def has_time(r: dict) -> bool:
    return "T" in (r.get("due") or "")


def make_due(date: datetime.date, hour: int = None, minute: int = 0):
    if date is None:
        return None
    if hour is None:
        return date.strftime("%Y-%m-%d")
    return "%sT%02d:%02d" % (date.strftime("%Y-%m-%d"), hour, minute)


def _by_due(r):
    dt = due_datetime(r)
    return (dt is None, dt or datetime.datetime.max, r.get("created", 0))


def smart_list(reminders: list, kind: str, today: datetime.date = None) -> list:
    """today: due today or overdue; scheduled: every dated one; all: every
    open one; completed: the done ones (latest first). Open ones only
    except for completed."""
    today = today or datetime.date.today()
    open_ = [r for r in reminders if not r.get("completed")]
    if kind == "today":
        return sorted((r for r in open_ if due_date(r) and due_date(r) <= today), key=_by_due)
    if kind == "scheduled":
        return sorted((r for r in open_ if due_date(r)), key=_by_due)
    if kind == "all":
        return sorted(open_, key=lambda r: r.get("created", 0))
    if kind == "completed":
        return sorted((r for r in reminders if r.get("completed")), key=lambda r: -(r.get("completed_at") or 0))
    return []


def search_reminders(reminders: list, query: str) -> list:
    words = (query or "").casefold().split()
    return [r for r in reminders
            if all(w in (r.get("title", "") + "\n" + r.get("notes", "")).casefold() for w in words)]


class Store:
    def __init__(self, folder: str = None):
        self.dir = folder or data_dir()
        self.notes_path = os.path.join(self.dir, "notes.json")
        self.reminders_path = os.path.join(self.dir, "reminders.json")
        self.load()

    # -- files --------------------------------------------------------------------------------
    def load(self) -> None:
        n = _read(self.notes_path)
        self.folders = [f for f in n.get("folders", []) if isinstance(f, dict) and f.get("id")]
        if not any(f["id"] == DEFAULT_FOLDER for f in self.folders):
            self.folders.insert(0, {"id": DEFAULT_FOLDER, "name": "Notes"})
        self.notes = [x for x in n.get("notes", []) if isinstance(x, dict) and x.get("id")]
        r = _read(self.reminders_path)
        self.lists = [x for x in r.get("lists", []) if isinstance(x, dict) and x.get("id")]
        if not any(x["id"] == DEFAULT_LIST for x in self.lists):
            self.lists.insert(0, {"id": DEFAULT_LIST, "name": "Reminders", "color": "blue"})
        self.reminders = [x for x in r.get("reminders", []) if isinstance(x, dict) and x.get("id")]
        if self.purge():
            self.save_notes()

    def save_notes(self) -> None:
        write_json(self.notes_path, {"version": 1, "folders": self.folders, "notes": self.notes})

    def save_reminders(self) -> None:
        write_json(self.reminders_path, {"version": 1, "lists": self.lists, "reminders": self.reminders})

    # -- notes ----------------------------------------------------------------------------------
    def note(self, nid: str):
        return next((n for n in self.notes if n["id"] == nid), None)

    def notes_in(self, folder: str) -> list:
        if folder == DELETED:
            return sort_notes([n for n in self.notes if n.get("deleted")])
        live = [n for n in self.notes if not n.get("deleted")]
        if folder != ALL:
            live = [n for n in live if n.get("folder") == folder]
        return sort_notes(live)

    def new_note(self, folder: str = DEFAULT_FOLDER, body: str = "", now: float = None) -> dict:
        now = time.time() if now is None else now
        if folder in (ALL, DELETED) or not self.folder(folder):
            folder = DEFAULT_FOLDER
        note = {"id": new_id(), "folder": folder, "body": body, "created": now, "modified": now,
                "pinned": False, "deleted": None}
        self.notes.append(note)
        self.save_notes()
        return note

    def update_note(self, note: dict, body: str, now: float = None) -> None:
        if note.get("body") == body:
            return
        note["body"] = body
        note["modified"] = time.time() if now is None else now
        self.save_notes()

    def set_pinned(self, note: dict, pinned: bool) -> None:
        note["pinned"] = bool(pinned)
        self.save_notes()

    def move_note(self, note: dict, folder: str) -> None:
        note["folder"] = folder
        self.save_notes()

    def delete_note(self, note: dict, now: float = None) -> None:
        """To Recently Deleted (or for good when it's already there)."""
        if note.get("deleted"):
            self.destroy_note(note)
            return
        note["deleted"] = time.time() if now is None else now
        note["pinned"] = False
        self.save_notes()

    def recover_note(self, note: dict) -> None:
        note["deleted"] = None
        if not self.folder(note.get("folder")):
            note["folder"] = DEFAULT_FOLDER
        self.save_notes()

    def destroy_note(self, note: dict) -> None:
        self.notes = [n for n in self.notes if n is not note]
        self.save_notes()

    def purge(self, now: float = None) -> bool:
        """Forget notes deleted more than 30 days ago."""
        now = time.time() if now is None else now
        keep = [n for n in self.notes if not n.get("deleted") or now - n["deleted"] < KEEP_DELETED]
        gone = len(keep) != len(self.notes)
        self.notes = keep
        return gone

    # -- folders ----------------------------------------------------------------------------------
    def folder(self, fid: str):
        return next((f for f in self.folders if f["id"] == fid), None)

    def unique_name(self, base: str, names) -> str:
        names = {x.casefold() for x in names}
        if base.casefold() not in names:
            return base
        i = 2
        while f"{base} {i}".casefold() in names:
            i += 1
        return f"{base} {i}"

    def add_folder(self, name: str = "New Folder") -> dict:
        f = {"id": new_id(), "name": self.unique_name(name, (x["name"] for x in self.folders))}
        self.folders.append(f)
        self.save_notes()
        return f

    def rename_folder(self, f: dict, name: str) -> None:
        name = name.strip()
        if name and name != f["name"]:
            f["name"] = self.unique_name(name, (x["name"] for x in self.folders if x is not f))
            self.save_notes()

    def delete_folder(self, f: dict, now: float = None) -> None:
        """Its notes go to Recently Deleted (in Notes when recovered)."""
        if f["id"] == DEFAULT_FOLDER:
            return
        now = time.time() if now is None else now
        for n in self.notes:
            if n.get("folder") == f["id"]:
                n["folder"] = DEFAULT_FOLDER
                if not n.get("deleted"):
                    n["deleted"], n["pinned"] = now, False
        self.folders = [x for x in self.folders if x is not f]
        self.save_notes()

    def folder_count(self, fid: str) -> int:
        return len(self.notes_in(fid))

    # -- reminders ----------------------------------------------------------------------------------
    def rlist(self, lid: str):
        return next((x for x in self.lists if x["id"] == lid), None)

    def reminders_in(self, key: str, today: datetime.date = None) -> list:
        """A smart list (today/scheduled/all/completed) or a user list's open ones."""
        if key in SMART:
            return smart_list(self.reminders, key, today)
        return sorted((r for r in self.reminders if r.get("list") == key and not r.get("completed")),
                      key=lambda r: r.get("created", 0))

    def new_reminder(self, list_id: str = DEFAULT_LIST, title: str = "", due: str = None,
                     now: float = None) -> dict:
        if not self.rlist(list_id):
            list_id = DEFAULT_LIST
        now = time.time() if now is None else now
        r = {"id": new_id(), "list": list_id, "title": title, "notes": "", "due": due, "priority": 0,
             "flagged": False, "completed": False, "completed_at": None, "created": now}
        self.reminders.append(r)
        self.save_reminders()
        return r

    def update_reminder(self, r: dict, **changes) -> None:
        if "completed" in changes:
            changes["completed_at"] = time.time() if changes["completed"] else None
        if all(r.get(k) == v for k, v in changes.items()):
            return
        r.update(changes)
        self.save_reminders()

    def delete_reminder(self, r: dict) -> None:
        self.reminders = [x for x in self.reminders if x is not r]
        self.save_reminders()

    def add_list(self, name: str = "New List") -> dict:
        used = [x.get("color") for x in self.lists]
        color = min(LIST_COLORS, key=lambda c: (used.count(c), LIST_COLORS.index(c)))
        x = {"id": new_id(), "name": self.unique_name(name, (y["name"] for y in self.lists)), "color": color}
        self.lists.append(x)
        self.save_reminders()
        return x

    def rename_list(self, x: dict, name: str) -> None:
        name = name.strip()
        if name and name != x["name"]:
            x["name"] = self.unique_name(name, (y["name"] for y in self.lists if y is not x))
            self.save_reminders()

    def delete_list(self, x: dict) -> None:
        """The list and its reminders (the default list stays)."""
        if x["id"] == DEFAULT_LIST:
            return
        self.reminders = [r for r in self.reminders if r.get("list") != x["id"]]
        self.lists = [y for y in self.lists if y is not x]
        self.save_reminders()

    def due_between(self, start: datetime.datetime, end: datetime.datetime) -> list:
        """Open reminders falling due in (start, end]."""
        return [r for r in self.reminders if not r.get("completed")
                and (dt := due_datetime(r)) is not None and start < dt <= end]

    def next_due_after(self, t: datetime.datetime):
        times = [dt for r in self.reminders if not r.get("completed")
                 and (dt := due_datetime(r)) is not None and dt > t]
        return min(times) if times else None
