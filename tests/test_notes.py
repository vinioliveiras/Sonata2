"""Notes & Reminders: storage round-trip, markup, smart lists, search, the
editor and the window (xvfb-run python3 -m unittest tests.test_notes)."""
import datetime
import os
import tempfile
import time
import unittest

os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.notes import markup  # noqa: E402
from sonata2.notes import store as S  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


SAMPLE = ("# Shopping\nPlain **bold** *italic* __under__ ~~gone~~ and a*star\n## Heading\n### Sub\n"
          "- bullet\n1. one\n2. two\n- [ ] todo\n- [x] done\n\\- not a list\n")


class MarkupTest(unittest.TestCase):
    def test_round_trip(self):
        again = markup.serialize(markup.parse(SAMPLE))
        self.assertEqual(markup.parse(again), markup.parse(SAMPLE))
        kinds = [k for k, _c, _r in markup.parse(SAMPLE)]
        self.assertEqual(kinds[:9], ["title", "body", "heading", "subheading", "bullet", "number", "number",
                                     "check", "check"])
        self.assertEqual(markup.plain(SAMPLE)[9], "- not a list")
        self.assertEqual(markup.title_and_preview(SAMPLE)[0], "Shopping")
        self.assertEqual(markup.title_and_preview("")[0], "New Note")


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_round_trip(self):
        st = S.Store(self.dir)
        f = st.add_folder("Work")
        n = st.new_note(f["id"], "# Plan\nship it")
        st.set_pinned(n, True)
        lst = st.add_list("Groceries")
        r = st.new_reminder(lst["id"], "Milk", due="2026-10-01T09:30")
        st.update_reminder(r, flagged=True, priority=2)
        st2 = S.Store(self.dir)
        self.assertEqual(st2.folders, st.folders)
        self.assertEqual(st2.notes, st.notes)
        self.assertEqual(st2.lists, st.lists)
        self.assertEqual(st2.reminders, st.reminders)
        self.assertFalse([x for x in os.listdir(self.dir) if x.endswith(".tmp")])     # atomic writes
        self.assertEqual(S.due_datetime(st2.reminders[0]), datetime.datetime(2026, 10, 1, 9, 30))

    def test_deleted_notes(self):
        st = S.Store(self.dir)
        now = time.time()
        a = st.new_note(body="old", now=now)
        b = st.new_note(body="fresh", now=now)
        st.delete_note(a, now=now - 31 * 86400)
        st.delete_note(b, now=now)
        self.assertEqual({n["id"] for n in st.notes_in(S.DELETED)}, {a["id"], b["id"]})
        self.assertEqual(st.notes_in(S.ALL), [])
        st2 = S.Store(self.dir)                     # older than 30 days: gone
        self.assertEqual([n["id"] for n in st2.notes_in(S.DELETED)], [b["id"]])
        st2.recover_note(st2.note(b["id"]))
        self.assertEqual([n["id"] for n in st2.notes_in(S.DEFAULT_FOLDER)], [b["id"]])

    def test_folders(self):
        st = S.Store(self.dir)
        f = st.add_folder()
        g = st.add_folder()
        self.assertEqual((f["name"], g["name"]), ("New Folder", "New Folder 2"))
        st.new_note(f["id"], "x")
        st.delete_folder(f)
        self.assertIsNone(st.folder(f["id"]))
        self.assertEqual(len(st.notes_in(S.DELETED)), 1)

    def test_sort_and_search(self):
        st = S.Store(self.dir)
        a = st.new_note(body="# Alpha\nred apples", now=100)
        b = st.new_note(body="# Beta\ngreen **pears**", now=200)
        c = st.new_note(body="# Gamma\nred pears", now=50)
        st.set_pinned(c, True)
        self.assertEqual([n["id"] for n in st.notes_in(S.ALL)], [c["id"], b["id"], a["id"]])
        self.assertEqual({n["id"] for n in S.search(st.notes_in(S.ALL), "RED")}, {a["id"], c["id"]})
        self.assertEqual([n["id"] for n in S.search(st.notes_in(S.ALL), "red pears")], [c["id"]])
        self.assertEqual([n["id"] for n in S.search(st.notes_in(S.ALL), "**")], [])   # markers aren't text
        self.assertEqual(len(S.search(st.notes_in(S.ALL), "")), 3)

    def test_smart_lists(self):
        st = S.Store(self.dir)
        today = datetime.date(2026, 9, 30)
        over = st.new_reminder(title="overdue", due="2026-09-28")
        now = st.new_reminder(title="today", due="2026-09-30T15:00")
        later = st.new_reminder(title="later", due="2026-10-05")
        none = st.new_reminder(title="undated")
        done = st.new_reminder(title="done", due="2026-09-30")
        st.update_reminder(done, completed=True)
        ids = lambda xs: [x["id"] for x in xs]      # noqa: E731
        self.assertEqual(ids(st.reminders_in("today", today)), [over["id"], now["id"]])
        self.assertEqual(ids(st.reminders_in("scheduled", today)), [over["id"], now["id"], later["id"]])
        self.assertEqual(set(ids(st.reminders_in("all", today))), {over["id"], now["id"], later["id"], none["id"]})
        self.assertEqual(ids(st.reminders_in("completed", today)), [done["id"]])
        self.assertEqual(len(st.reminders_in(S.DEFAULT_LIST, today)), 4)
        self.assertEqual(ids(S.search_reminders(st.reminders, "LATE")), [later["id"]])
        # alerts: due in (start, end]
        self.assertEqual(ids(st.due_between(datetime.datetime(2026, 9, 30, 14), datetime.datetime(2026, 9, 30, 16))),
                         [now["id"]])


class EditorTest(unittest.TestCase):
    def test_typing(self):
        Adw.init()
        ui.setup()
        from gi.repository import Gtk
        from sonata2.notes.editor import NoteEditor
        ed = NoteEditor()
        win = Gtk.Window(child=ed)
        win.present()
        ed.load("")
        buf = ed.buffer

        def type_(text):
            buf.insert_interactive_at_cursor(text, -1, True)

        def enter():                         # what the Return key does
            if not ed._enter():
                type_("\n")
        type_("Hello")                       # a new note starts with a title
        enter()                          # then Body
        type_("plain ")
        ed.toggle_inline("bold")             # nothing selected: bold typing
        type_("strong")
        ed.toggle_inline("bold")
        enter()
        ed.set_style("bullet")
        type_("one")
        enter()                          # a list continues
        type_("two")
        enter()
        enter()                          # Return on an empty item ends the list
        type_("end")
        self.assertEqual(ed.text(), "# Hello\nplain **strong**\n- one\n- two\nend")
        ed.load("1. a\n1. b\n1. c")           # numbers are renumbered
        self.assertEqual(buf.get_text(*buf.get_bounds(), False), "1. a\n2. b\n3. c")
        win.destroy()


class WindowTest(unittest.TestCase):
    def test_window(self):
        Adw.init()
        ui.setup()
        from sonata2.notes.window import NotesWindow
        st = S.Store(tempfile.mkdtemp())
        n1 = st.new_note(body="# First\nhello world", now=time.time() - 100)
        st.new_note(body="# Second\nanother", now=time.time() - 50)
        r = st.new_reminder(title="Soon", due=S.make_due(datetime.date.today(), 23, 59))
        app = Adw.Application(application_id="io.test.notes")
        app.register(None)
        w = NotesWindow(app, store=st)
        w.present()
        settle()
        w.select("n:" + S.ALL)
        settle()
        self.assertEqual(w.sorted.get_n_items(), 2)
        self.assertEqual(w.sorted.get_item(0).title, "Second")           # newest first
        # editor: load, round-trip, type, autosave
        w.reload_notes(select_id=n1["id"])
        settle()
        self.assertIs(w.note, n1)
        self.assertEqual(w.editor.text(), "# First\nhello world")
        buf = w.editor.buffer
        buf.insert(buf.get_end_iter(), "!")
        w.editor.set_style("check")
        settle(700)                                                      # > SAVE_DELAY
        self.assertEqual(n1["body"], "# First\n- [ ] hello world!")
        self.assertEqual(w.sorted.get_item(0).note, n1)                  # edited: to the top
        self.assertIs(w.selection.get_selected_item().note, n1)          # ... still selected
        self.assertEqual(S.Store(st.dir).note(n1["id"])["body"], n1["body"])   # on disk too
        # bold on a selection
        s = buf.get_iter_at_line(0)[1]
        e = s.copy()
        e.forward_to_line_end()
        buf.select_range(s, e)
        w.editor.toggle_inline("bold")
        w._flush()
        settle(50)
        self.assertTrue(n1["body"].startswith("# **First**"))
        # search
        w.search.set_text("another")
        settle(400)
        self.assertEqual(w.sorted.get_n_items(), 1)
        w.search.set_text("")
        settle(400)
        # delete, then undo
        w.reload_notes(select_id=n1["id"])
        w.delete_selected()
        settle()
        self.assertTrue(n1["deleted"])
        self.assertEqual(w.sorted.get_n_items(), 1)
        self.assertIsNotNone(w._row("n:" + S.DELETED))
        self.assertTrue(w.undo_delete())
        settle()
        self.assertFalse(n1["deleted"])
        self.assertEqual(w.sorted.get_n_items(), 2)
        # new note left empty goes away
        w.new_note()
        settle()
        self.assertEqual(len(st.notes), 3)
        w.reload_notes(select_id=n1["id"])
        settle()
        self.assertEqual(len(st.notes), 2)
        # reminders
        w.select("r:today")
        settle()
        self.assertEqual(w.stack.get_visible_child_name(), "reminders")
        self.assertEqual(len(list(w.reminders._rows())), 1)
        self.assertEqual(w._row("r:today").count.get_label(), "1")
        row = next(w.reminders._rows())
        row.check.set_active(True)
        settle(1300)
        self.assertTrue(r["completed"])
        self.assertEqual(len(list(w.reminders._rows())), 0)
        w.select("r:completed")
        settle()
        self.assertEqual(len(list(w.reminders._rows())), 1)
        # alerts
        st.update_reminder(r, completed=False)
        start = datetime.datetime.combine(datetime.date.today(), datetime.time(23, 0))
        w.notifier.last = start
        due = w.notifier.check(start + datetime.timedelta(hours=1))
        self.assertEqual([x["id"] for x in due], [r["id"]])
        self.assertEqual(w.notifier.sent[-1][0], "Soon")
        w.destroy()



class NotesIconTest(unittest.TestCase):
    def test_pale_yellow_top(self):
        """Vini: the icon's blue top band is a soft grey."""
        import os
        from unittest import mock
        from sonata2.notes import window as W
        with mock.patch("sonata2.apps.write_desktop_file", side_effect=lambda _n, text: text):
            self.assertIn("Icon=sonata-notes\n", W.notes_desktop_file("sonata2"))
        svg = open(os.path.join(os.path.dirname(W.__file__), "..", "data", "icons", "Sonata", "apps", "scalable",
                                "sonata-notes.svg")).read()
        self.assertIn("#e5e5ea", svg)                       # grey (yellow looked bad)
        self.assertNotIn("#0069f5", svg)


if __name__ == "__main__":
    unittest.main()
