"""TextEdit: open, edited title, find, undo, save; formats (encodings, line
endings, converted documents), search, tabs, session restore
(xvfb-run python3 -m unittest tests.test_textedit)."""
import io
import json
import os
import tempfile
import unittest
import zipfile

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.textedit import document, search  # noqa: E402

_APP = {}


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def app():
    if "app" not in _APP:
        Adw.init()
        ui.setup()
        a = Adw.Application(application_id="io.test.textedit")
        a.register(None)
        _APP["app"] = a
    return _APP["app"]


def _json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write(name, data, folder=None):
    path = os.path.join(folder or tempfile.mkdtemp(), name)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    return path


class FormatsTest(unittest.TestCase):
    def test_encodings(self):
        d = document.decode("héllo\n".encode("utf-8"))
        self.assertEqual((d.text, d.encoding, d.bom, d.newline), ("héllo\n", "utf-8", False, "LF"))
        d = document.decode(b"\xef\xbb\xbfhi")
        self.assertEqual((d.text, d.encoding, d.bom), ("hi", "utf-8", True))
        d = document.decode("﻿olá\r\n".encode("utf-16-le"))
        self.assertEqual((d.text, d.encoding, d.bom, d.newline), ("olá\n", "utf-16-le", True, "CRLF"))
        d = document.decode("﻿olá".encode("utf-16-be"))
        self.assertEqual((d.text, d.encoding), ("olá", "utf-16-be"))
        d = document.decode("plain utf16 without bom".encode("utf-16-le"))
        self.assertEqual((d.text, d.encoding, d.bom), ("plain utf16 without bom", "utf-16-le", False))
        d = document.decode("café “quoted”".encode("windows-1252"))
        self.assertEqual((d.text, d.encoding), ("café “quoted”", "windows-1252"))
        d = document.decode(b"\x81\x8d caf\xe9")                  # not Windows-1252 either
        self.assertEqual((d.text, d.encoding), ("\x81\x8d café", "iso-8859-1"))
        with self.assertRaises(document.BinaryFile):
            document.decode(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x01\x00\xff\x13")

    def test_newlines_round_trip(self):
        self.assertEqual(document.detect_newline("a\r\nb\r\nc\n"), "CRLF")
        self.assertEqual(document.detect_newline("a\rb\r"), "CR")
        self.assertEqual(document.detect_newline("no newline"), "LF")
        for raw, enc in (("one\r\ntwo\r\n", "utf-8"), ("uno\rdos\r", "windows-1252"), ("é\nè\n", "iso-8859-1")):
            data = raw.encode(enc)
            d = document.decode(data)
            self.assertNotIn("\r", d.text)
            self.assertEqual(document.encode(d.text, d.encoding, d.bom, d.newline), data)
        bom16 = "﻿x\r\n".encode("utf-16-le")
        d = document.decode(bom16)
        self.assertEqual(document.encode(d.text, d.encoding, d.bom, d.newline), bom16)
        with self.assertRaises(UnicodeEncodeError):
            document.encode("日本", "iso-8859-1")

    def test_converted(self):
        w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml",
                       f'<w:document xmlns:w="{w}"><w:body><w:p><w:r><w:t>Hello</w:t></w:r><w:r><w:tab/>'
                       f'<w:t>world</w:t></w:r></w:p><w:p><w:r><w:t>Second</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(document.convert(buf.getvalue(), "a.docx"), "Hello\tworld\nSecond")
        t = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
        o = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("content.xml", f'<office:document-content xmlns:office="{o}" xmlns:text="{t}"><office:body>'
                       f'<office:text><text:h>Title</text:h><text:p>a<text:s text:c="2"/>b<text:span>c</text:span>'
                       f'</text:p></office:text></office:body></office:document-content>')
        self.assertEqual(document.convert(buf.getvalue(), "b.ODT"), "Title\na  bc")
        rtf = rb"{\rtf1\ansi{\fonttbl\f0 Helvetica;}\f0 Caf\'e9 \b bold\b0\par Next\tab line \u8364?\par}"
        self.assertEqual(document.convert(rtf, "c.rtf"), "Café bold\nNext\tline €\n")
        self.assertIsNone(document.convert(b"x", "d.txt"))
        with self.assertRaises(ValueError):
            document.convert(b"not a zip", "e.docx")

    def test_misc(self):
        self.assertEqual(document.words("Hello, world — it's  me"), 4)
        self.assertTrue(document.is_code("main.py") and document.is_code("Makefile"))
        self.assertFalse(document.is_code("Letter.txt") or document.is_code("Notes.md"))


class SearchTest(unittest.TestCase):
    def test_find(self):
        text = "Sonata sonata SONATA sonatas"
        self.assertEqual(len(search.find_all(text, "sonata")), 4)
        self.assertEqual(search.find_all(text, "sonata", case=True), [(7, 13), (21, 27)])
        self.assertEqual(len(search.find_all(text, "sonata", whole=True)), 3)
        self.assertEqual(search.find_all(text, r"S\w+A", case=True, regex=True), [(14, 20)])
        self.assertEqual(search.find_all(text, ""), [])
        self.assertEqual(search.find_all("a.b", "."), [(1, 2)])            # literal by default
        with self.assertRaises(Exception):
            search.find_all(text, "(", regex=True)
        spans = [(0, 1), (5, 6), (9, 10)]
        self.assertEqual(search.next_span(spans, 5), (9, 10))
        self.assertEqual(search.next_span(spans, 5, include_current=True), (5, 6))
        self.assertEqual(search.next_span(spans, 9), (0, 1))                 # wraps
        self.assertEqual(search.next_span(spans, 0, backwards=True), (9, 10))

    def test_replace(self):
        self.assertEqual(search.replace_all("a.b.c", ".", "\\"), ("a\\b\\c", 2))
        self.assertEqual(search.replace_all("x=1, y=2", r"(\w)=(\d)", r"\2:\1", regex=True), ("1:x, 2:y", 2))
        text = "key=value"
        self.assertEqual(search.expand(text, (0, 9), r"(\w+)=(\w+)", r"\2=\1", regex=True), "value=key")


class TextEditTest(unittest.TestCase):
    def setUp(self):
        from sonata2.textedit import window as tw
        self.tw = tw
        app()
        for w in list(tw._S["windows"]):
            w._closing = True
            w.destroy()
        tw._S.update(parked=[], loaded=True)

    def tearDown(self):
        for w in list(self.tw._S["windows"]):
            w._closing = True
            w.destroy()
        settle(20)

    def test_document(self):
        from sonata2.textedit.window import TextEditWindow
        path = write("doc.txt", "Hello Sonata\nfind sonata here\n")
        w = TextEditWindow(app(), path)
        w.present()
        settle()
        self.assertEqual(w.bar.title_label.get_label(), "doc.txt")
        w.buffer.insert_at_cursor("X")
        settle(50)
        self.assertEqual(w.bar.title_label.get_label(), "doc.txt — Edited")
        w._show_find()
        w.find_entry.set_text("sonata")
        settle(400)
        self.assertEqual(w.find_count.get_label(), "2 found")
        w._hide_find()
        w.save()
        with open(path) as f:
            self.assertTrue(f.read().startswith("XHello"))
        self.assertFalse(w.buffer.get_modified())
        w.destroy()

    def test_keeps_encoding_and_line_endings(self):
        path = write("win.txt", "caf\xe9\r\nline two\r\n".encode("windows-1252"))
        w = self.tw.TextEditWindow(app(), path)
        w.present()
        settle(100)
        self.assertEqual(w.buffer.get_text(*w.buffer.get_bounds(), True), "café\nline two\n")
        self.assertEqual((w.enc_btn.get_label(), w.nl_btn.get_label()), ("Windows-1252", "CRLF"))
        w.buffer.insert(w.buffer.get_end_iter(), "três\n")
        w.save()
        with open(path, "rb") as f:
            self.assertEqual(f.read(), "café\r\nline two\r\ntrês\r\n".encode("windows-1252"))
        w._stats()
        self.assertEqual(w.count_label.get_label(), "4 words, 19 characters")
        w.go_to(2, 3)
        self.assertEqual(w.pos_btn.get_label(), "Line 2, Column 3")

    def test_binary_and_converted(self):
        w = self.tw.TextEditWindow(app())
        w.present()
        self.assertFalse(w.open_file(Gio.File.new_for_path(write("x.bin", b"\x00\x01\x02\xff" * 50))))
        self.assertEqual(len(w.docs), 1)
        buf = io.BytesIO()
        ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body><w:p><w:r><w:t>Report</w:t>'
                       '</w:r></w:p></w:body></w:document>')
        self.assertTrue(w.open_file(Gio.File.new_for_path(write("r.docx", buf.getvalue()))))
        settle(50)
        self.assertEqual(w.buffer.get_text(*w.buffer.get_bounds(), True), "Report")
        self.assertFalse(w.view.get_editable())
        self.assertTrue(w.ro_label.get_visible())

    def test_tabs(self):
        tw = self.tw
        folder = tempfile.mkdtemp()
        a, b = write("a.txt", "alpha", folder), write("b.txt", "beta", folder)
        w = tw.TextEditWindow(app())
        w.present()
        settle(50)
        self.assertFalse(w.tabs_rev.get_reveal_child())             # one tab: no tab bar
        tw.open_paths(app(), [a, b])                                  # files -> tabs of the front window
        self.assertEqual([d.name for d in w.docs], ["a.txt", "b.txt"])   # the empty Untitled took a.txt
        self.assertTrue(w.tabs_rev.get_reveal_child())
        self.assertIs(w.doc, w.docs[1])
        tw.open_paths(app(), [a])                                     # already open: comes forward
        self.assertEqual(len(w.docs), 2)
        self.assertIs(w.doc, w.docs[0])
        cmd = Gdk.ModifierType.CONTROL_MASK
        self.assertTrue(w._key(None, Gdk.KEY_t, 0, cmd))
        self.assertEqual(len(w.docs), 3)
        self.assertIs(w.doc, w.docs[1])                               # opens next to the current tab
        self.assertEqual(w.doc.name, "Untitled")
        w._key(None, Gdk.KEY_Tab, 0, cmd)
        self.assertEqual(w.doc.name, "b.txt")
        w._key(None, Gdk.KEY_ISO_Left_Tab, 0, cmd | Gdk.ModifierType.SHIFT_MASK)
        self.assertEqual(w.doc.name, "Untitled")
        w._key(None, Gdk.KEY_1, 0, Gdk.ModifierType.SUPER_MASK)
        self.assertEqual(w.doc.name, "a.txt")
        # reorder (what dragging a tab does)
        w.tabs.move(w.docs[2], 0)
        w._reordered([t.doc for t in w.tabs.tabs()])
        self.assertEqual([d.name for d in w.docs], ["b.txt", "a.txt", "Untitled"])
        # an edited tab asks before closing; a clean one just closes
        w.select(w.docs[1])
        w.buffer.insert_at_cursor("!")
        self.assertTrue(w.tabs.tab_of(w.doc).has_css_class("modified"))
        w.close_tab()
        settle(50)
        self.assertEqual(len(w.docs), 3)
        w.close_tab(w.docs[2])
        self.assertEqual([d.name for d in w.docs], ["b.txt", "a.txt"])
        w.move_to_new_window(w.docs[0])
        self.assertEqual([d.name for d in w.docs], ["a.txt"])
        other = tw._S["windows"][0]
        self.assertIsNot(other, w)
        self.assertEqual([d.name for d in other.docs], ["b.txt"])
        self.assertFalse(w.tabs_rev.get_reveal_child())

    def test_find_replace(self):
        w = self.tw.TextEditWindow(app())
        w.present()
        w.buffer.set_text("cat Cat cat\nconcat")
        w._show_find(replace=True)
        self.assertTrue(w.replace_rev.get_reveal_child())
        w.find_entry.set_text("cat")
        w._find(0)
        self.assertEqual(w.find_count.get_label(), "4 found")
        w.case_btn.set_active(True)
        self.assertEqual(w.find_count.get_label(), "3 found")
        w.word_btn.set_active(True)
        self.assertEqual(w.find_count.get_label(), "2 found")
        w.buffer.place_cursor(w.buffer.get_start_iter())
        w._find(0)
        w.replace_entry.set_text("dog")
        w.replace_one()                                               # replaces the selected match
        self.assertEqual(w.doc.text(), "dog Cat cat\nconcat")
        sel = w.buffer.get_selection_bounds()
        self.assertEqual((sel[0].get_offset(), sel[1].get_offset()), (8, 11))   # and selects the next
        w.word_btn.set_active(False)
        w.case_btn.set_active(False)
        self.assertEqual(w.replace_all(), 3)
        self.assertEqual(w.doc.text(), "dog dog dog\ncondog")
        w.buffer.undo()                                                # Replace All is one undo step
        self.assertEqual(w.doc.text(), "dog Cat cat\nconcat")
        w.regex_btn.set_active(True)
        w.find_entry.set_text(r"(c)(at)")
        w.replace_entry.set_text(r"\2\1")
        w.replace_all()
        self.assertEqual(w.doc.text(), "dog atC atc\nconatc")
        w.find_entry.set_text("(")
        w._find(0)
        self.assertEqual(w.find_count.get_label(), "Invalid pattern")

    def test_session_round_trip(self):
        tw = self.tw
        from sonata2.textedit import session
        path = write("kept.txt", "saved text\nline 2\n")
        w = tw.TextEditWindow(app(), path)
        w.present()
        settle(50)
        w.buffer.insert(w.buffer.get_end_iter(), "unsaved edit\n")
        w.buffer.place_cursor(w.buffer.get_iter_at_offset(5))
        u = w.new_tab()
        u.buffer.insert_at_cursor("scratch notes ✓")
        w.new_tab()                                                    # empty Untitled: kept, no text file
        w.select(w.docs[1])
        names = [d.name for d in w.docs]
        tw.save_now()
        data = _json(os.path.join(session.data_dir(), "session.json"))
        tabs = data["windows"][0]["tabs"]
        self.assertEqual([t["backup"] for t in tabs], [True, True, False])
        self.assertEqual(session.read_buffer(u.id), "scratch notes ✓")
        # "quit": the last window closes without asking and stays in the session
        w.close()
        settle(50)
        self.assertEqual(os.path.getsize(path), len("saved text\nline 2\n"))     # the file itself untouched
        self.assertEqual(len(tw._S["parked"]), 1)
        # a new process: TextEdit starts without files
        tw._S.update(parked=[], loaded=False)
        tw.open_paths(app(), [])
        settle(100)
        w2 = tw._S["windows"][0]
        self.assertEqual([d.name for d in w2.docs], names)
        self.assertEqual(w2.docs[0].text(), "saved text\nline 2\nunsaved edit\n")
        self.assertTrue(w2.docs[0].dirty)
        self.assertEqual(w2.docs[0].file.get_path(), path)
        self.assertEqual(w2.docs[0].buffer.get_iter_at_mark(w2.docs[0].buffer.get_insert()).get_offset(), 5)
        self.assertEqual(w2.docs[1].text(), "scratch notes ✓")
        self.assertIs(w2.doc, w2.docs[1])                              # the tab that was active
        # closing the untitled tab for good (Don't Save) drops its text from the disk
        w2._remove_tab(w2.docs[1])
        tw.save_now()
        self.assertIsNone(session.read_buffer(u.id))
        # saving the edited file: nothing unsaved is left in the session
        w2.save(w2.docs[0])
        tw.save_now()
        data = _json(os.path.join(session.data_dir(), "session.json"))
        self.assertFalse(any(t["backup"] for win in data["windows"] for t in win["tabs"]))

    def test_closing_one_of_several_windows(self):
        tw = self.tw
        w1 = tw.TextEditWindow(app())
        w2 = tw.TextEditWindow(app())
        w1.present()
        w2.present()
        w2.buffer.insert_at_cursor("don't lose me")
        w2.close()                                                     # no question, the text is kept
        settle(50)
        self.assertEqual(len(tw._S["parked"]), 1)
        w1.close()                                                     # clean and last: kept too
        settle(50)
        self.assertEqual(len(tw._S["parked"]), 2)
        tw._S.update(parked=[], loaded=False)
        self.assertTrue(tw.restore_session(app()))
        texts = sorted(d.text() for w in tw._S["windows"] for d in w.docs)
        self.assertIn("don't lose me", texts)

    def test_look_settings(self):
        tw = self.tw
        from sonata2 import config
        w = tw.TextEditWindow(app(), write("main.py", "print(1)\n"))
        w.present()
        self.assertTrue(w.view.has_css_class("te-mono"))                # Automatic: code is monospaced
        w.open_file(Gio.File.new_for_path(write("prose.txt", "Words.")))
        self.assertTrue(w.view.has_css_class("te-sans"))
        w._set_option("font", "serif")
        self.assertTrue(all(d.view.has_css_class("te-serif") for d in w.docs))
        size = tw._cfg()["font_size"]
        w._zoom(1)
        self.assertGreater(tw._cfg()["font_size"], size)
        self.assertEqual(w.size_label.get_label(), str(tw._cfg()["font_size"]))
        w._zoom(0)
        self.assertEqual(tw._cfg()["font_size"], 14)
        w.wrap_btn.set_active(False)
        self.assertEqual(w.view.get_wrap_mode(), 0)
        w.lines_btn.set_active(True)
        from sonata2.textedit import source
        if source.is_source(w.view):
            self.assertTrue(w.view.get_show_line_numbers())
        else:
            self.assertIsNotNone(w.view.get_gutter(__import__("gi").repository.Gtk.TextWindowType.LEFT))
        stored = config.load("textedit", tw.DEFAULTS)
        self.assertEqual((stored["font"], stored["wrap"], stored["line_numbers"]), ("serif", False, True))
        w._set_option("font", "auto")
        w.wrap_btn.set_active(True)
        w.lines_btn.set_active(False)


if __name__ == "__main__":
    unittest.main()
