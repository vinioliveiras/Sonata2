"""Finder's "Rename N Items…": several items renamed at once.

    Replace Text   "IMG_" -> "Trip " in every name
    Add Text       "Final " before (or " v2" after) each name
    Format         "Trip 1", "Trip 2"... (from a start number), the extension kept

new_names() is the pure part (the dialog previews the first one); rename()
renames them in a thread, skips a name already taken, and the whole batch is
one Undo."""
import os

from gi.repository import Gio, GLib

from . import ops

MODES = (("replace", "Replace Text"), ("add", "Add Text"), ("format", "Format"))


def _split(name: str, is_dir: bool):
    return (name, "") if is_dir else ops._split(name)


def new_names(names, mode: str, a: str = "", b: str = "", after: bool = True, start: int = 1,
              dirs=None) -> list:
    """The new name of each of `names` (unchanged where the rule does nothing)."""
    dirs = dirs or [False] * len(names)
    out = []
    width = len(str(start + len(names) - 1))
    for i, (name, is_dir) in enumerate(zip(names, dirs)):
        base, ext = _split(name, is_dir)
        if mode == "replace":
            new = base.replace(a, b) + ext if a else name
        elif mode == "add":
            new = (base + a + ext) if after else (a + base + ext)
        elif mode == "format":
            new = f"{a or 'Untitled'} {str(start + i).zfill(width)}{ext}"
        else:
            new = name
        out.append(new.strip() or name)
    return out


def problems(names, new) -> str:
    """Why these new names can't be used ("" when they can)."""
    if any("/" in n for n in new):
        return "A name can’t contain “/”."
    if len(set(new)) != len(new):
        return "Two items would get the same name."
    return ""


def rename(files, new, on_done=None, on_error=None) -> None:
    """Rename each file to its new name (a name already taken is skipped);
    on_done() after. One Undo for the batch."""
    pairs = [(f, n) for f, n in zip(files, new) if n != f.get_basename()]
    done = []

    def work(report):
        for f, n in pairs:
            parent = f.get_parent()
            if parent.get_child(n).query_exists(None):
                report(f, GLib.Error.new_literal(Gio.io_error_quark(), f"“{n}” already exists.",
                                                 Gio.IOErrorEnum.EXISTS))
                continue
            try:
                done.append((f.set_display_name(n, None), f.get_basename()))
            except GLib.Error as e:
                report(f, e)

    def finish():
        if done:
            from . import tags, undo
            tags.moved([(nf.get_parent().get_child(old), nf) for nf, old in done])
            undo.history.push(undo.Action(
                f"Rename {len(done)} Items",
                lambda: [nf.set_display_name(old, None) for nf, old in done],
                lambda: [nf.get_parent().get_child(old).set_display_name(nf.get_basename(), None)
                         for nf, old in done]))
        if on_done:
            on_done()
    from . import ops as _ops
    _ops._in_thread(work, finish, on_error)


def dialog(parent, files, on_done=None):
    """The "Rename N Items" alert: the rule, its fields, an example."""
    from gi.repository import Gtk
    from .. import ui
    names = [f.get_basename() for f in files]
    dirs = [os.path.isdir(f.get_path()) if f.get_path() else False for f in files]
    grid = Gtk.Grid(row_spacing=8, column_spacing=8)
    mode = ui.controls.popup_button([label for _k, label in MODES], 0)
    a = ui.controls.text_field(hexpand=True)
    b = ui.controls.text_field(hexpand=True)
    where = ui.controls.popup_button(["after name", "before name"], 0)
    start = ui.controls.text_field("1", hexpand=False)
    la, lb, ls = Gtk.Label(xalign=1), Gtk.Label(xalign=1), Gtk.Label(label="Start at:", xalign=1)
    example = Gtk.Label(xalign=0, css_classes=["dim-label"], wrap=True)
    grid.attach(mode, 1, 0, 1, 1)
    grid.attach(la, 0, 1, 1, 1)
    grid.attach(a, 1, 1, 1, 1)
    grid.attach(lb, 0, 2, 1, 1)
    grid.attach(b, 1, 2, 1, 1)
    grid.attach(where, 1, 3, 1, 1)
    grid.attach(ls, 0, 4, 1, 1)
    grid.attach(start, 1, 4, 1, 1)
    grid.attach(example, 1, 5, 1, 1)

    def rule():
        k = MODES[mode.get_selected()][0]
        try:
            n = int(start.get_text() or "1")
        except ValueError:
            n = 1
        return new_names(names, k, a.get_text(), b.get_text(), where.get_selected() == 0, n, dirs)

    def changed(*_x):
        k = MODES[mode.get_selected()][0]
        la.set_label({"replace": "Find:", "add": "Text:", "format": "Name:"}[k])
        lb.set_label("Replace with:")
        for w in (lb, b):
            w.set_visible(k == "replace")
        where.set_visible(k == "add")
        for w in (ls, start):
            w.set_visible(k == "format")
        new = rule()
        bad = problems(names, new)
        example.set_label(bad or f"Example: {new[0]}")
        if dlg is not None and hasattr(dlg, "set_response_enabled"):
            dlg.set_response_enabled("rename", not bad and new != names)

    def answer(rid):
        if rid == "rename":
            new = rule()
            if not problems(names, new):
                rename(files, new, on_done, lambda f, e: ui.dialog.alert(
                    f"“{f.get_basename()}” can’t be renamed.", e.message, [("ok", "OK", "default")], parent=parent))
    dlg = None
    dlg = ui.dialog.alert(f"Rename {len(files)} Items", "", [("cancel", "Cancel", ""), ("rename", "Rename", "default")],
                          answer, parent=parent)
    dlg.set_extra_child(grid)
    mode.connect("notify::selected", changed)
    where.connect("notify::selected", changed)
    for e in (a, b, start):
        e.connect("changed", changed)
    changed()
    return dlg
