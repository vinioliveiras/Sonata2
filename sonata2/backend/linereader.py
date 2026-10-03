"""Lines of a child's pipe on the GTK main loop, as text.

Read in chunks (read_bytes_async), split here: a non-UTF-8 byte is replaced
instead of ending the read (read_line_finish_utf8 raised on it), and the end
of the pipe is a 0-byte read (PyGObject turns read_line's NULL at EOF into
b"", the same as an empty line)."""
from gi.repository import GLib

CHUNK = 64 * 1024


def read_lines(stream, cancel, on_line, on_end) -> None:
    """on_line(text) per line (without the newline), then on_end() once at
    the end of the stream or on a read error; nothing after `cancel`."""
    rest = [b""]

    def got(s, res):
        try:
            data = s.read_bytes_finish(res).get_data() or b""
        except GLib.Error:
            data = None
        if cancel is not None and cancel.is_cancelled():
            return
        if not data:                                   # end of the pipe (or an error)
            if rest[0]:
                on_line(rest[0].decode("utf-8", "replace"))
            on_end()
            return
        *lines, rest[0] = (rest[0] + data).split(b"\n")
        if len(rest[0]) > 4 * CHUNK:                  # a line that never ends: shown in parts
            lines.append(rest[0])
            rest[0] = b""
        try:
            for ln in lines:
                on_line(ln.decode("utf-8", "replace"))
        finally:
            s.read_bytes_async(CHUNK, GLib.PRIORITY_DEFAULT, cancel, got)
    stream.read_bytes_async(CHUNK, GLib.PRIORITY_DEFAULT, cancel, got)
