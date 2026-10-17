"""backend.linereader: the lines of a child's output (Software Update, disk
tools) read on the main loop. Lines split across reads, bytes that aren't
UTF-8, empty lines, a last line without a newline and a line that never ends
must all come through; nothing after a cancel.
Run: python3 -m unittest tests.test_linereader"""
import unittest
from unittest import mock

from gi.repository import Gio, GLib

from sonata2.backend import linereader as LR


def read_all(data: bytes, chunk: int = LR.CHUNK, cancel=None, stop_after=None):
    """Feed `data` through read_lines; returns (lines, end calls)."""
    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
    lines, ends = [], []
    loop = GLib.MainLoop()

    def on_line(text):
        lines.append(text)
        if stop_after is not None and len(lines) == stop_after:
            cancel.cancel()
            GLib.timeout_add(100, loop.quit)       # give a wrong extra line time to come

    def on_end():
        ends.append(1)
        GLib.idle_add(loop.quit)
    with mock.patch.object(LR, "CHUNK", chunk):
        LR.read_lines(stream, cancel, on_line, on_end)
        GLib.timeout_add(3000, loop.quit)          # never hang the suite
        loop.run()
    return lines, ends


class ReadLinesTest(unittest.TestCase):
    def test_lines_and_the_end(self):
        lines, ends = read_all(b"one\ntwo\n\nfour\n")
        self.assertEqual(lines, ["one", "two", "", "four"])      # an empty line is a line, not the end
        self.assertEqual(ends, [1])

    def test_last_line_without_a_newline(self):
        self.assertEqual(read_all(b"a\nlast")[0], ["a", "last"])

    def test_lines_split_across_reads(self):
        """8-byte reads cut words and lines anywhere: joined back whole."""
        data = b"Downloading package 1/3\nInstalling\n100%\n"
        self.assertEqual(read_all(data, chunk=8)[0], ["Downloading package 1/3", "Installing", "100%"])

    def test_a_character_split_across_reads(self):
        """A multi-byte character cut by a read boundary comes back whole
        (decoded per line, not per read)."""
        data = "Atualização concluída\n".encode()
        for chunk in (7, 9, 10, 11, 13):                 # reads ending inside a ç, ã or í
            self.assertEqual(read_all(data, chunk=chunk)[0], ["Atualização concluída"], chunk)

    def test_bad_bytes_dont_end_the_read(self):
        """A byte that isn't UTF-8 is replaced; the lines after it still come."""
        lines, ends = read_all(b"ok\nbad \xff\xfe here\nafter\n")
        self.assertEqual(lines, ["ok", "bad �� here", "after"])
        self.assertEqual(ends, [1])

    def test_a_line_that_never_ends_comes_in_parts(self):
        """Progress bars redrawn with \\r only: shown in parts instead of
        held in memory until the child exits."""
        data = b"x" * 50 + b"\n"
        lines, _ends = read_all(data, chunk=4)
        self.assertEqual("".join(lines), "x" * 50)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(len(ln) <= 4 * 4 + 4 for ln in lines))

    def test_empty_stream(self):
        self.assertEqual(read_all(b""), ([], [1]))

    def test_nothing_after_cancel(self):
        """Cancelled (the window closed): no more lines and no on_end."""
        cancel = Gio.Cancellable()
        lines, ends = read_all(b"a\nb\nc\nd\n", chunk=2, cancel=cancel, stop_after=1)
        self.assertEqual(lines, ["a"])
        self.assertEqual(ends, [])


if __name__ == "__main__":
    unittest.main()
