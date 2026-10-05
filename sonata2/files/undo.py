"""Edit > Undo / Redo for Files (Finder: ⌘Z, ⇧⌘Z) -- Ctrl+Z / Ctrl+Shift+Z.

What can be undone, the last first: a rename, a move (put back where it
was), a copy or duplicate (the copies go to the Trash), a new folder (to the
Trash, while empty), Move to Trash (put back from the Trash). One history
for the Files process (every window and tab), like Finder's.

Each action is a pair of callables run on the GTK thread; the file work
itself runs in a thread (ops._in_thread) and reports errors the usual way."""
from gi.repository import Gio, GLib

LIMIT = 50


class Action:
    def __init__(self, label: str, undo, redo=None):
        self.label, self.undo, self.redo = label, undo, redo


class History:
    def __init__(self):
        self.done, self.undone = [], []

    def push(self, action: Action) -> None:
        self.done.append(action)
        del self.done[:-LIMIT]
        self.undone.clear()

    def can_undo(self) -> bool:
        return bool(self.done)

    def can_redo(self) -> bool:
        return bool(self.undone) and self.undone[-1].redo is not None

    def take_undo(self):
        """The action to undo (moved to the redo list), or None. The caller
        runs action.undo() -- in a thread: file work never blocks the window."""
        if not self.done:
            return None
        a = self.done.pop()
        self.undone.append(a)
        return a

    def take_redo(self):
        if not self.can_redo():
            return None
        a = self.undone.pop()
        self.done.append(a)
        return a


history = History()


# -- the actions Files records ---------------------------------------------------------------
def _move(src: Gio.File, dest: Gio.File) -> None:
    src.move(dest, Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None, None)


def renamed(new: Gio.File, old_name: str) -> None:
    new_name = new.get_basename()
    parent = new.get_parent()
    history.push(Action(f"Rename “{old_name}”",
                        lambda: parent.get_child(new_name).set_display_name(old_name, None),
                        lambda: parent.get_child(old_name).set_display_name(new_name, None)))


def moved(pairs) -> None:
    """pairs: [(where it was, where it is now)]."""
    pairs = list(pairs)
    if pairs:
        history.push(Action("Move" if len(pairs) > 1 else f"Move “{pairs[0][0].get_basename()}”",
                            lambda: [_move(now, was) for was, now in reversed(pairs)],
                            lambda: [_move(was, now) for was, now in pairs]))


def copied(created) -> None:
    """created: the new copies (Copy, Duplicate, Paste): undone, they go to the Trash."""
    created = list(created)
    if created:
        history.push(Action("Copy" if len(created) > 1 else f"Copy “{created[0].get_basename()}”",
                            lambda: [f.trash(None) for f in created]))


def new_folder(child: Gio.File) -> None:
    def undo():
        try:
            child.delete(None)                       # only while empty
        except GLib.Error:
            child.trash(None)
    history.push(Action("New Folder", undo, lambda: child.make_directory(None)))


def trashed(files) -> None:
    """Undo: each item back from the Trash to where it was."""
    paths = [f.get_path() for f in files if f.get_path()]
    if paths:
        history.push(Action("Move to Trash" if len(paths) > 1 else f"Move “{files[0].get_basename()}” to Trash",
                            lambda: put_back_paths(paths),
                            lambda: [Gio.File.new_for_path(p).trash(None) for p in paths]))


def in_trash(orig_path: str):
    """The newest Trash item that came from orig_path, or None."""
    trash = Gio.File.new_for_uri("trash:///")
    best, when = None, ""
    en = trash.enumerate_children("standard::name,trash::orig-path,trash::deletion-date",
                                  Gio.FileQueryInfoFlags.NONE, None)
    for info in en:
        if info.get_attribute_byte_string("trash::orig-path") == orig_path:
            d = info.get_attribute_string("trash::deletion-date") or ""
            if d >= when:
                best, when = trash.get_child(info.get_name()), d
    return best


def put_back_paths(paths) -> None:
    for p in paths:
        item = in_trash(p)
        if item is not None:
            item.move(Gio.File.new_for_path(p), Gio.FileCopyFlags.NOFOLLOW_SYMLINKS, None, None, None)
