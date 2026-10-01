"""List columns (Gtk.ColumnView) the macOS way: every column keeps the width
it's given or dragged to, and only the last one takes the room that's left.

A column in the middle that expands (GTK's `expand`) fought every resize:
shrinking its neighbour grew it, so the edge being dragged stayed put, away
from the pointer, and the next press grabbed its header and reordered it
(Files, Task Manager, Music).

    ui.columns.fill_last(view)      # once, before or after adding columns
"""
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def fill_last(view: Gtk.ColumnView) -> None:
    """Only the last visible column expands -- kept so when columns are
    added, reordered, shown or hidden."""
    cols = view.get_columns()
    watched = set()

    def update(*_a):
        shown = [c for c in (cols.get_item(i) for i in range(cols.get_n_items())) if c.get_visible()]
        for c in shown:
            want = c is shown[-1]
            if c.get_expand() != want:
                c.set_expand(want)
        for i in range(cols.get_n_items()):
            c = cols.get_item(i)
            if id(c) not in watched:
                watched.add(id(c))
                c.connect("notify::visible", update)
    cols.connect("items-changed", update)
    view._sonata_fill_last = update          # (kept with the view)
    update()
