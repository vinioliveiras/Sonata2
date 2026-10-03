"""Review tests for Files, Disk Utility and Preview: file operations against
temp dirs (conflicts, symlinks, cross-device fallback, Put Back, delete),
search, thumbnails cache, packages/archives, bookmarks, automount, the
UDisks model/client helpers and Preview's edits/info. Headless; nothing
touches the real home or a real disk.

    xvfb-run -a python3.12 -m unittest tests.test_review_files -v"""
import os
import shlex
import tarfile
import threading
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("XDG_CONFIG_HOME", tempfile.mkdtemp())
os.environ.setdefault("XDG_CACHE_HOME", tempfile.mkdtemp())

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.diskutil import model, udisks  # noqa: E402
from sonata2.files import automount, ops, packages, search, sidebar, thumbs  # noqa: E402

try:
    import PIL  # noqa: F401
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def spin(cond, ms=4000):
    end = GLib.get_monotonic_time() + ms * 1000
    while not cond() and GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)
    return cond()


def write(path, data="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)


def read(path):
    with open(path) as f:
        return f.read()


class Wrapped:
    """A Gio.File stand-in delegating to a real one (to inject failures)."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)


class _Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.g = Gio.File.new_for_path(self.d)
        self.alerts = []
        p = mock.patch.object(ui.dialog, "alert", side_effect=self._alert)
        p.start()
        self.addCleanup(p.stop)
        self.answer = "keep"

    def _alert(self, heading, body, responses, on_response=None, parent=None, check=None):
        self.alerts.append(heading)
        if on_response is not None:
            if check:
                on_response(self.answer, False)
            else:
                on_response(responses[0][0])

    def p(self, *parts):
        return os.path.join(self.d, *parts)

    def transfer(self, srcs, dest, **kw):
        done = []
        ops.Transfer([Gio.File.new_for_path(s) if isinstance(s, str) else s for s in srcs],
                     Gio.File.new_for_path(dest), on_done=lambda: done.append(1), **kw)
        self.assertTrue(spin(lambda: done), "transfer never finished")


# -- ops: names ----------------------------------------------------------------------------
class NamesTest(unittest.TestCase):
    def test_split_edge_cases(self):
        """Extension split used by Duplicate/Keep Both/rename: dotfiles stay
        whole, compound tar suffixes stay together, no-extension names work."""
        self.assertEqual(ops._split(".bashrc"), (".bashrc", ""))
        self.assertEqual(ops._split("a.tar.zst"), ("a", ".tar.zst"))
        self.assertEqual(ops._split("README"), ("README", ""))
        self.assertEqual(ops._split("v1.2.final.pdf"), ("v1.2.final", ".pdf"))
        self.assertEqual(ops.rename_selection(".bashrc", False), (0, 7))
        self.assertEqual(ops.rename_selection(".tar.gz", False), (0, 7))   # never an empty selection

    def test_free_name_number_style_keeps_extension(self):
        """"number" style on a file keeps the extension after the counter."""
        d = tempfile.mkdtemp()
        g = Gio.File.new_for_path(d)
        open(os.path.join(d, "note.txt"), "w").close()
        self.assertEqual(ops.free_name(g, "note.txt", style="number"), "note 2.txt")
        self.assertEqual(ops.free_name(g, "café ünï.txt"), "café ünï copy.txt")


# -- ops: copy / move ------------------------------------------------------------------------
class TransferTest(_Base):
    def test_conflict_keep_both(self):
        """"Keep Both" never overwrites: the incoming file gets a "copy" name."""
        write(self.p("src", "a.txt"), "new")
        write(self.p("dst", "a.txt"), "old")
        self.answer = "keep"
        self.transfer([self.p("src", "a.txt")], self.p("dst"))
        self.assertEqual(read(self.p("dst", "a.txt")), "old")
        self.assertEqual(read(self.p("dst", "a copy.txt")), "new")

    def test_conflict_replace(self):
        """"Replace" swaps the content and leaves no leftover copy."""
        write(self.p("src", "a.txt"), "new")
        write(self.p("dst", "a.txt"), "old")
        self.answer = "replace"
        self.transfer([self.p("src", "a.txt")], self.p("dst"))
        self.assertEqual(read(self.p("dst", "a.txt")), "new")
        self.assertEqual(sorted(os.listdir(self.p("dst"))), ["a.txt"])

    def test_conflict_stop_leaves_everything(self):
        """"Stop" aborts the whole operation: later items aren't copied and
        nothing is overwritten (and no error alert is shown)."""
        write(self.p("src", "a.txt"), "new")
        write(self.p("src", "b.txt"), "b")
        write(self.p("dst", "a.txt"), "old")
        self.answer = "stop"
        self.transfer([self.p("src", "a.txt"), self.p("src", "b.txt")], self.p("dst"))
        self.assertEqual(read(self.p("dst", "a.txt")), "old")
        self.assertFalse(os.path.exists(self.p("dst", "b.txt")))
        self.assertEqual(len(self.alerts), 1)                      # only the conflict question

    def test_symlinks_copied_as_links(self):
        """A symlink (even to a folder) is copied as a link, never followed
        (no recursion into its target, no duplicate of a huge tree)."""
        write(self.p("big", "data.bin"), "payload")
        os.makedirs(self.p("src"))
        os.symlink(self.p("big"), self.p("src", "link"))
        os.makedirs(self.p("dst"))
        self.transfer([self.p("src")], self.p("dst"))
        self.assertTrue(os.path.islink(self.p("dst", "src", "link")))
        self.assertEqual(os.readlink(self.p("dst", "src", "link")), self.p("big"))

    def test_cross_device_move_falls_back_to_copy_delete(self):
        """When rename() isn't possible (another disk), a folder move copies
        everything then deletes the source."""
        write(self.p("src", "sub", "f.txt"), "hello")
        os.makedirs(self.p("dst"))
        src = Wrapped(Gio.File.new_for_path(self.p("src")))

        def move(*_a):
            raise GLib.Error.new_literal(Gio.io_error_quark(), "cross-device", Gio.IOErrorEnum.NOT_SUPPORTED)
        src.move = move
        self.transfer([src], self.p("dst"), move=True)
        self.assertEqual(read(self.p("dst", "src", "sub", "f.txt")), "hello")
        self.assertFalse(os.path.exists(self.p("src")))
        self.assertEqual(self.alerts, [])

    def test_failed_move_keeps_source(self):
        """A move that fails with a real error keeps the source and reports it."""
        write(self.p("src.txt"), "keep me")
        os.makedirs(self.p("dst"))
        src = Wrapped(Gio.File.new_for_path(self.p("src.txt")))

        def move(*_a):
            raise GLib.Error.new_literal(Gio.io_error_quark(), "denied", Gio.IOErrorEnum.PERMISSION_DENIED)
        src.move = move
        self.transfer([src], self.p("dst"), move=True)
        self.assertEqual(read(self.p("src.txt")), "keep me")
        self.assertEqual(self.alerts, ["The operation can’t be completed."])

    def test_replace_never_deletes_the_source(self):
        """Moving dir/x/x up into dir and answering Replace must not destroy
        the item being moved."""
        write(self.p("x", "x", "precious.txt"), "data")
        self.answer = "replace"
        self.transfer([self.p("x", "x")], self.d, move=True)
        found = [os.path.join(r, n) for r, _d, fs in os.walk(self.d) for n in fs]
        self.assertTrue(any(read(f) == "data" for f in found), "the moved file was deleted")


class ReplaceSafetyTest(_Base):
    def test_failed_replace_keeps_the_old_item(self):
        """Replace whose copy fails part-way puts the old item back (it was
        deleted before the copy) and leaves no hidden temp behind."""
        write(self.p("src", "a.txt"), "new")
        write(self.p("dst", "a.txt"), "old")
        src = Wrapped(Gio.File.new_for_path(self.p("src", "a.txt")))

        def copy(*_a):
            raise GLib.Error.new_literal(Gio.io_error_quark(), "disk full", Gio.IOErrorEnum.NO_SPACE)
        src.copy = copy
        self.answer = "replace"
        self.transfer([src], self.p("dst"))
        self.assertEqual(read(self.p("dst", "a.txt")), "old")
        self.assertEqual(os.listdir(self.p("dst")), ["a.txt"])
        self.assertEqual(self.alerts[-1], "The operation can’t be completed.")

    def test_replace_success_leaves_no_temp(self):
        """A successful Replace swaps the item and removes the old one."""
        write(self.p("src", "a.txt"), "new")
        write(self.p("dst", "a.txt"), "old")
        self.answer = "replace"
        self.transfer([self.p("src", "a.txt")], self.p("dst"))
        self.assertEqual(read(self.p("dst", "a.txt")), "new")
        self.assertEqual(os.listdir(self.p("dst")), ["a.txt"])


# -- ops: Trash / delete ------------------------------------------------------------------------
class TrashTest(_Base):
    def _trashed(self, real_path, orig):
        f = Wrapped(Gio.File.new_for_path(real_path))

        def query_info(attrs, *_a):
            info = Gio.FileInfo()
            info.set_attribute_byte_string("trash::orig-path", orig)
            return info
        f.query_info = query_info
        return f

    def test_put_back_recreates_parent_and_never_overwrites(self):
        """Put Back recreates a deleted parent folder, and when the original
        name is taken again it restores as "x copy" instead of overwriting.
        It runs off the GTK thread and calls on_done at the end."""
        write(self.p("trash", "a.txt"), "trashed")
        orig = self.p("gone", "deeper", "a.txt")
        done = []
        ops.put_back([self._trashed(self.p("trash", "a.txt"), orig)], lambda f, e: self.fail(e.message),
                     lambda: done.append(1))
        self.assertTrue(spin(lambda: done))
        self.assertEqual(read(orig), "trashed")
        write(self.p("trash", "b.txt"), "second")
        write(self.p("home", "b.txt"), "current")
        ops.put_back([self._trashed(self.p("trash", "b.txt"), self.p("home", "b.txt"))], on_done=lambda: done.append(2))
        self.assertTrue(spin(lambda: 2 in done))
        self.assertEqual(read(self.p("home", "b.txt")), "current")
        self.assertEqual(read(self.p("home", "b copy.txt")), "second")

    def test_put_back_unknown_origin_reports(self):
        """An item with no recorded origin reports an error, nothing moves."""
        write(self.p("trash", "a.txt"))
        errors = []
        ops.put_back([self._trashed(self.p("trash", "a.txt"), "")], lambda f, e: errors.append(e))
        self.assertTrue(spin(lambda: errors))
        self.assertEqual(len(errors), 1)
        self.assertTrue(os.path.exists(self.p("trash", "a.txt")))

    def test_empty_trash_off_main_loop_sound_after_success(self):
        """Empty Trash lists and deletes in a thread and plays its sound only
        once something was erased (not before, not on failure)."""
        from sonata2 import sounds
        main = threading.current_thread()
        listed_on = []

        class FakeTrash:
            def enumerate_children(self, *_a):
                listed_on.append(threading.current_thread())
                raise GLib.Error.new_literal(Gio.io_error_quark(), "no trash", Gio.IOErrorEnum.NOT_SUPPORTED)
        errors, played = [], []
        with mock.patch.object(Gio.File, "new_for_uri", return_value=FakeTrash()), \
                mock.patch.object(sounds, "play", side_effect=played.append):
            ops.empty_trash(None, lambda f, e: errors.append(e))
            self.assertTrue(spin(lambda: errors))
        self.assertIsNot(listed_on[0], main)
        self.assertEqual(played, [])

    def test_delete_now_never_follows_symlinks(self):
        """Delete Immediately removes a folder holding a symlink to another
        folder without deleting what the link points to; a failing item is
        reported and the rest is still deleted."""
        write(self.p("outside", "keep.txt"), "safe")
        write(self.p("victim", "f.txt"))
        os.symlink(self.p("outside"), self.p("victim", "link"))
        errors, done = [], []
        F = Gio.File.new_for_path
        ops.delete_now([F(self.p("missing")), F(self.p("victim"))], lambda: done.append(1),
                       lambda f, e: errors.append(f.get_basename()))
        self.assertTrue(spin(lambda: done))
        self.assertFalse(os.path.lexists(self.p("victim")))
        self.assertEqual(read(self.p("outside", "keep.txt")), "safe")
        self.assertTrue(spin(lambda: errors))
        self.assertEqual(errors, ["missing"])

    def test_paste_image_never_overwrites(self):
        """Two pictures pasted in the same second must not overwrite each other."""
        saved = []

        class Tex:
            def save_to_png(self, path):
                saved.append(path)
                write(path, f"img{len(saved)}")
                return True

        class Clip:
            def get_formats(self):
                return mock.Mock(contain_gtype=lambda _t: True)

            def read_texture_async(self, _c, cb):
                cb(self, None)

            def read_texture_finish(self, _r):
                return Tex()

        widget = mock.Mock(get_clipboard=lambda: Clip())
        now = mock.Mock(format=lambda fmt: "Pasted Image 2026-01-01 at 10.00.00.png")
        with mock.patch.object(GLib.DateTime, "new_now_local", return_value=now):
            ops.paste_image(widget, self.g)
            ops.paste_image(widget, self.g)
        self.assertEqual(len(saved), 2)
        self.assertNotEqual(saved[0], saved[1])


class ReportsTest(_Base):
    def test_paste_image_failure_is_reported(self):
        """A picture that can't be written is reported, not dropped silently."""
        class Tex:
            def save_to_png(self, _path):
                return False

        class Clip:
            def get_formats(self):
                return mock.Mock(contain_gtype=lambda _t: True)

            def read_texture_async(self, _c, cb):
                cb(self, None)

            def read_texture_finish(self, _r):
                return Tex()
        errors = []
        ops.paste_image(mock.Mock(get_clipboard=lambda: Clip()), self.g, on_error=errors.append)
        self.assertEqual(len(errors), 1)

    def test_drop_on_trash_reports_errors(self):
        """Dropping on the Trash passes an error handler (USB/NFS without a
        Trash used to fail silently)."""
        from sonata2.files.window import FilesWindow
        fake = mock.Mock()
        f = Gio.File.new_for_path(self.p("x"))
        with mock.patch.object(ops, "trash") as trash:
            self.assertTrue(FilesWindow.drop(fake, [f], Gio.File.new_for_uri("trash:///")))
        files, on_error = trash.call_args[0]
        self.assertEqual(files, [f])
        on_error(f, GLib.Error.new_literal(Gio.io_error_quark(), "no trash", Gio.IOErrorEnum.NOT_SUPPORTED))
        fake._error.assert_called_once()


class FolderIndexTest(unittest.TestCase):
    def test_index_by_bisect_matches_scan(self):
        """The monitor's lookup by name (bisect over the sort keys) finds the
        same items as a scan, after inserts and removals too."""
        from sonata2.files import folder
        f = folder.Folder(lambda _u: None, lambda _u, _e: None)
        names = [f"file {i}.txt" for i in range(200)] + ["Zed", "alpha", "Alpha"]
        items = []
        for n in names:
            i = Gio.FileInfo()
            i.set_name(n)
            i.set_display_name(n)
            items.append(i)
        f._fill("file:///x", items)
        store = [f.store.get_item(i).get_name() for i in range(f.store.get_n_items())]
        for n in names:
            self.assertEqual(f._index(n), store.index(n))
        self.assertEqual(f._index("missing"), -1)
        f._remove("file 7.txt")
        self.assertEqual(f._index("file 7.txt"), -1)
        store = [f.store.get_item(i).get_name() for i in range(f.store.get_n_items())]
        self.assertEqual(f._index("file 70.txt"), store.index("file 70.txt"))
        self.assertEqual(len(f._keys), f.store.get_n_items())


# -- search ----------------------------------------------------------------------------------
class SearchTest(_Base):
    def _run(self, root, query):
        hits, done = [], []
        s = search.Search()
        s.start(root, query, lambda items: hits.extend(i.get_name() for i in items), done.append)
        self.assertTrue(spin(lambda: done), "search never finished")
        return sorted(hits), done[0]

    def test_accent_and_case_insensitive_skips_hidden_and_links(self):
        """All words must match, accents/case ignored; hidden folders and
        symlinked folders (loops) aren't walked."""
        write(self.p("Relatório Final.pdf"))
        write(self.p("sub", "relatorio-final-v2.odt"))
        write(self.p(".hidden", "relatorio final.txt"))
        write(self.p("relatorio.txt"))
        os.symlink(self.d, self.p("sub", "loop"))
        hits, truncated = self._run(self.d, "RELATORIO final")
        self.assertEqual(hits, ["Relatório Final.pdf", "relatorio-final-v2.odt"])
        self.assertFalse(truncated)

    def test_limit_truncates(self):
        """Past LIMIT hits the search stops and says it was truncated."""
        for i in range(10):
            write(self.p(f"match{i}.txt"))
        with mock.patch.object(search, "LIMIT", 3):
            hits, truncated = self._run(self.d, "match")
        self.assertEqual(len(hits), 3)
        self.assertTrue(truncated)


# -- thumbnails ----------------------------------------------------------------------------------
class ThumbsTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for name, val in (("CACHE_DIR", os.path.join(self.d, "large")),
                          ("FAIL_DIR", os.path.join(self.d, "fail"))):
            p = mock.patch.object(thumbs, name, val)
            p.start()
            self.addCleanup(p.stop)

    def _png(self, path, w, h):
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
        pb.fill(0x3366ccff)
        pb.savev(path, "png", [], [])

    def test_make_writes_freedesktop_thumbnail(self):
        """A made thumbnail fits 256 px, is named md5(uri).png and carries
        Thumb::URI / Thumb::MTime so other apps (and we) can reuse it."""
        src = os.path.join(self.d, "pic.png")
        self._png(src, 1200, 600)
        uri = Gio.File.new_for_path(src).get_uri()
        out = thumbs._make(uri, src, 1234, "image/png", os.path.getsize(src))
        self.assertTrue(out.endswith(thumbs._name(uri)))
        pb = GdkPixbuf.Pixbuf.new_from_file(out)
        self.assertLessEqual(max(pb.get_width(), pb.get_height()), thumbs.SIZE)
        self.assertEqual(pb.get_option("tEXt::Thumb::URI"), uri)
        self.assertEqual(thumbs._cached(uri, 1234), out)
        self.assertIsNone(thumbs._cached(uri, 9999))           # stale after the file changed
        self.assertFalse([n for n in os.listdir(thumbs.CACHE_DIR) if n.endswith(".tmp")])

    def test_failures_remembered_per_mtime(self):
        """An unreadable picture is marked failed (not retried) until it changes."""
        bad = os.path.join(self.d, "bad.png")
        write(bad, "not a png")
        uri = Gio.File.new_for_path(bad).get_uri()
        self.assertIsNone(thumbs._make(uri, bad, 1, "image/png", 9))
        self.assertTrue(thumbs._failed(uri, 1))
        self.assertFalse(thumbs._failed(uri, 2))

    def test_memory_cache_bounded_and_callbacks(self):
        """The in-RAM cache stays at MEMORY entries; waiting callbacks get the
        texture once, and nothing for a failed one."""
        got = []
        with mock.patch.object(thumbs, "MEMORY", 2), mock.patch.dict(thumbs._mem, clear=True), \
                mock.patch.dict(thumbs._pending, clear=True):
            thumbs._pending[("a", 1)] = [got.append]
            thumbs._pending[("b", 1)] = [got.append]
            thumbs._deliver(("a", 1), "TEX")
            thumbs._deliver(("b", 1), None)
            thumbs._deliver(("c", 1), "TEX3")
            self.assertEqual(list(thumbs._mem), [("b", 1), ("c", 1)])
            self.assertEqual(got, ["TEX"])
            self.assertEqual(thumbs._pending, {})


# -- packages / archives ---------------------------------------------------------------------------
class PackagesTest(_Base):
    def test_kind(self):
        """Suffix detection (case-insensitive, longest first)."""
        self.assertEqual(packages.kind("/x/App-1.0.AppImage"), "appimage")
        self.assertEqual(packages.kind("foo-1.pkg.tar.zst"), "arch")
        self.assertEqual(packages.kind("foo.tar.zst"), "archive")
        self.assertEqual(packages.kind("x.FLATPAKREF"), "flatpakref")
        self.assertIsNone(packages.kind("photo.jpg"))
        self.assertIsNone(packages.kind(None))

    def test_install_commands_quote_hostile_names(self):
        """Paths with spaces, quotes and shell metacharacters stay one argument
        (no shell injection when the terminal runs the command)."""
        path = "/tmp/a b;rm -rf ~ $(id)'x.pkg.tar.zst"
        with mock.patch.object(packages, "family", return_value="arch"):
            cmd = packages.install_command(path)
            self.assertEqual(shlex.split(cmd), ["sudo", "pacman", "-U", path])
            self.assertIsNone(packages.install_command("/tmp/x.rpm"))      # other distro family: refused
        deb = "/tmp/d ir/$(x).deb"
        parts = shlex.split(packages._debtap_command(deb))
        self.assertIn(deb, parts)
        self.assertIn("/tmp/d ir", parts)

    def test_extract_names_and_contents(self):
        """Extract Here: a folder named after the archive ("a", then "a 2"),
        never into an existing folder."""
        write(self.p("in", "doc.txt"), "hi")
        z = self.p("a.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(self.p("in", "doc.txt"), "doc.txt")
        os.makedirs(self.p("a"))
        done = []
        packages.extract(z, done=done.append)
        self.assertTrue(spin(lambda: done))
        self.assertEqual(done[0], self.p("a 2"))
        self.assertEqual(read(self.p("a 2", "doc.txt")), "hi")

    def test_extract_blocks_path_traversal(self):
        """Archive members with "../" never land outside the target folder
        (zip: sanitised; tar: refused with an alert)."""
        z = self.p("evil.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("../escaped-zip.txt", "x")
        t = self.p("evil.tar.gz")
        write(self.p("payload.txt"), "x")
        with tarfile.open(t, "w:gz") as tf:
            tf.add(self.p("payload.txt"), "../escaped-tar.txt")
        done = []
        packages.extract(z, done=done.append)
        self.assertTrue(spin(lambda: done))
        packages.extract(t)
        self.assertTrue(spin(lambda: self.alerts))
        self.assertFalse(os.path.exists(self.p("escaped-zip.txt")))
        self.assertFalse(os.path.exists(self.p("escaped-tar.txt")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.d), "escaped-tar.txt")))

    def test_extract_failure_removes_partial_folder(self):
        """An archive that breaks half-way leaves no half-extracted folder."""
        t = self.p("half.tar")
        write(self.p("one.txt"), "1" * 4000)
        write(self.p("two.txt"), "2" * 40000)
        with tarfile.open(t, "w") as tf:
            tf.add(self.p("one.txt"), "one.txt")
            tf.add(self.p("two.txt"), "two.txt")
        with open(t, "r+b") as fh:
            fh.truncate(12000)                       # one.txt complete, two.txt cut
        packages.extract(t)
        self.assertTrue(spin(lambda: self.alerts))
        self.assertFalse(os.path.exists(self.p("half")))

    def test_appimage_asks_once_per_file(self):
        """The first run of an AppImage asks first (Cancel: nothing runs, no
        chmod); after Open it runs without asking; a changed file asks again."""
        app = self.p("Tool.AppImage")
        write(app, "#!/bin/sh\n")
        os.chmod(app, 0o644)
        runs = []
        with mock.patch.object(packages, "_launch_appimage", side_effect=lambda p, _w=None: runs.append(p)):
            self.answer = None
            responses = []
            self._alert_resp = "cancel"

            def alert(heading, body, rs, on_response=None, parent=None, check=None):
                responses.append(heading)
                on_response(self._alert_resp)
            with mock.patch.object(ui.dialog, "alert", side_effect=alert):
                packages.open_path(app)
                self.assertEqual((len(responses), runs), (1, []))
                self.assertEqual(os.stat(app).st_mode & 0o777, 0o644)
                self._alert_resp = "open"
                packages.open_path(app)
                self.assertEqual((len(responses), runs), (2, [app]))
                packages.open_path(app)                      # remembered: no question
                self.assertEqual((len(responses), runs), (2, [app, app]))
                write(app, "#!/bin/sh\necho changed\n")       # another file now: asked again
                os.utime(app, ns=(1, 1))
                packages.open_path(app)
                self.assertEqual(len(responses), 3)

    def test_extract_corrupt_archive_cleans_up(self):
        """A broken archive shows an alert and leaves no empty folder behind."""
        write(self.p("broken.zip"), "garbage")
        packages.extract(self.p("broken.zip"))
        self.assertTrue(spin(lambda: self.alerts))
        self.assertFalse(os.path.exists(self.p("broken")))


# -- sidebar bookmarks / eject ---------------------------------------------------------------------
class SidebarTest(_Base):
    def test_pin_unpin_keeps_order_labels_and_no_duplicates(self):
        """Favorites pins: inserted before the given one, no duplicates,
        existing labels survive, unpin removes only that entry."""
        bm = self.p("gtk-3.0", "bookmarks")
        write(bm, "file:///a Alpha\nfile:///c\n")
        with mock.patch.object(sidebar, "BOOKMARKS", bm), mock.patch.object(sidebar, "_sidebars", []):
            sidebar.pin(["file:///b", "file:///a"], before="file:///c")
            self.assertEqual(sidebar.read_bookmarks(),
                             [("file:///a", "Alpha"), ("file:///b", ""), ("file:///c", "")])
            sidebar.unpin("file:///a")
            self.assertEqual([u for u, _l in sidebar.read_bookmarks()], ["file:///b", "file:///c"])
        self.assertFalse(os.path.exists(bm + ".new"))

    def test_eject_finish_matches_the_started_operation(self):
        """The finish call must match the operation that was started, even if
        can_eject() changes once the drive is gone."""
        state = {"ejected": False}

        class Mount:
            def can_eject(self):
                return not state["ejected"]

            def eject_with_operation_finish(self, _r):
                return True

            def unmount_with_operation_finish(self, _r):
                raise GLib.Error.new_literal(Gio.io_error_quark(), "wrong result", Gio.IOErrorEnum.FAILED)

            def get_name(self):
                return "USB"
        fake_self = mock.Mock(get_root=lambda: None)
        m = Mount()
        state["ejected"] = True                    # the drive went away before the callback
        sidebar.Sidebar._ejected(fake_self, m, None)
        self.assertEqual(self.alerts, [])


# -- automount ------------------------------------------------------------------------------------
class AutomountTest(_Base):
    class Vol:
        def __init__(self, fail=False, mounted=False, can=True):
            self.fail, self.mounted, self.can, self.calls = fail, mounted, can, 0

        def get_mount(self):
            return object() if self.mounted else None

        def can_mount(self):
            return self.can

        def get_identifier(self, _k):
            return f"/dev/test{id(self)}"

        def get_name(self):
            return "Disk"

        def mount(self, _flags, _op, _cancel, done):
            self.calls += 1
            done(self, None)

        def mount_finish(self, _r):
            if self.fail:
                raise GLib.Error("hibernated")

    def test_mounts_once_and_never_insists(self):
        """Mounted / unmountable volumes are skipped; a failed one isn't
        retried in the same session; a successful one may be again later."""
        with mock.patch.object(automount, "_tried", set()):
            skip, nomount = self.Vol(mounted=True), self.Vol(can=False)
            automount._mount(skip)
            automount._mount(nomount)
            self.assertEqual((skip.calls, nomount.calls), (0, 0))
            bad = self.Vol(fail=True)
            automount._mount(bad)
            automount._mount(bad)
            self.assertEqual(bad.calls, 1)
            ok = self.Vol()
            automount._mount(ok)
            automount._mount(ok)
            self.assertEqual(ok.calls, 2)


# -- Disk Utility ---------------------------------------------------------------------------------
U = "org.freedesktop.UDisks2."


def ay(s):
    return list(s.encode()) + [0]


def blk(dev, size, drive="/", fs="", uuid="", label=""):
    return {U + "Block": {"Device": ay(dev), "PreferredDevice": ay(dev), "Size": size, "Drive": drive,
                          "IdUsage": "filesystem" if fs else "", "IdType": fs, "IdUUID": uuid, "IdLabel": label,
                          "IdVersion": "", "HintIgnore": False, "CryptoBackingDevice": "/"}}


def fs(*mounts):
    return {U + "Filesystem": {"MountPoints": [ay(m) for m in mounts]}}


def prt(table, n, off, size, container=False):
    return {U + "Partition": {"Table": table, "Number": n, "Offset": off, "Size": size, "Name": "",
                              "IsContainer": container}}


class DiskModelTest(unittest.TestCase):
    O = "/org/freedesktop/UDisks2/"
    GB = 1000 ** 3

    def tree(self):
        o, G = self.O, self.GB
        da, db = o + "drives/A", o + "drives/B"
        return {
            da: {U + "Drive": {"Size": 100 * G, "Model": "A", "ConnectionBus": ""}},
            db: {U + "Drive": {"Size": 100 * G, "Model": "B", "ConnectionBus": ""}},
            o + "block_devices/sda": {**blk("/dev/sda", 100 * G, da), U + "PartitionTable": {"Type": "dos"}},
            o + "block_devices/sda1": {**blk("/dev/sda1", 50 * G, da, "btrfs", "U1"), **fs("/"),
                                       **prt(o + "block_devices/sda", 1, 1 << 20, 50 * G)},
            o + "block_devices/sdb": {**blk("/dev/sdb", 100 * G, db), U + "PartitionTable": {"Type": "dos"}},
            o + "block_devices/sdb1": {**blk("/dev/sdb1", 60 * G, db), **prt(o + "block_devices/sdb", 1,
                                                                                1 << 20, 60 * G, True)},
            o + "block_devices/sdb5": {**blk("/dev/sdb5", 20 * G, db, "ext4", "U5"), **fs(),
                                       **prt(o + "block_devices/sdb", 5, 2 * G, 20 * G)},
            o + "block_devices/sdb6": {**blk("/dev/sdb6", 10 * G, db, "btrfs", "U1"), **fs(),
                                       **prt(o + "block_devices/sdb", 6, 30 * G, 10 * G)},
            o + "block_devices/loop9": {**blk("/dev/loop9", 1 * G), U + "Loop": {"BackingFile": ay("")}},
        }

    def test_text_and_parse_basics(self):
        """'ay' decoding; extended containers aren't listed; loop devices
        without a backing file (snaps, zram) aren't disks."""
        self.assertEqual(model.text(ay("/dev/sda")), "/dev/sda")
        self.assertEqual(model.text("x\0"), "x")
        self.assertEqual(model.text(None), "")
        disks = model.parse(self.tree())
        self.assertEqual([d.device for d in disks], ["/dev/sda", "/dev/sdb"])
        sdb = disks[1]
        self.assertEqual([v.device for v in sdb.volumes if v.listed], ["/dev/sdb5", "/dev/sdb6"])

    def test_segments_with_extended_partition(self):
        """Logical partitions are drawn (not their container), with real free
        gaps between them and the bar never exceeding the disk."""
        sdb = model.parse(self.tree())[1]
        segs = model.segments(sdb)
        self.assertEqual([s[0] for s in segs], ["Free Space", "Untitled", "Free Space", "Untitled", "Free Space"])
        self.assertLessEqual(sum(s[1] for s in segs), sdb.size)

    def test_member_of_mounted_root_filesystem_is_protected(self):
        """A second device of a multi-device filesystem mounted at / (btrfs
        RAID: same UUID) must be protected from Erase."""
        sdb = model.parse(self.tree())[1]
        sdb6 = [v for v in sdb.volumes if v.device == "/dev/sdb6"][0]
        self.assertTrue(sdb6.protected)


class DiskMembersTest(unittest.TestCase):
    O = "/org/freedesktop/UDisks2/"
    G = 1000 ** 3

    def test_pool_and_md_members_protected(self):
        """Members of a running md array, zfs/bcache members and a whole-disk
        RAID member can't be erased; an unrelated disk still can."""
        o, G = self.O, self.G
        dc, dd, de = o + "drives/C", o + "drives/D", o + "drives/E"
        md = o + "mdraid/root"
        member = blk("/dev/sdc", 100 * G, dc, "linux_raid_member", "R1")
        member[U + "Block"]["MDRaidMember"] = md
        tree = {
            dc: {U + "Drive": {"Size": 100 * G}}, dd: {U + "Drive": {"Size": 100 * G}},
            de: {U + "Drive": {"Size": 100 * G}},
            md: {U + "MDRaid": {"Running": True}},
            o + "block_devices/sdc": member,                                   # whole-disk RAID member
            o + "block_devices/md0": {**blk("/dev/md0", 100 * G, "/", "ext4", "M"), **fs("/")},
            o + "block_devices/sdd": {**blk("/dev/sdd", 100 * G, dd), U + "PartitionTable": {"Type": "gpt"}},
            o + "block_devices/sdd1": {**blk("/dev/sdd1", 90 * G, dd, "zfs_member", "Z"), **fs(),
                                       **prt(o + "block_devices/sdd", 1, 1 << 20, 90 * G)},
            o + "block_devices/sde": {**blk("/dev/sde", 100 * G, de), U + "PartitionTable": {"Type": "gpt"}},
            o + "block_devices/sde1": {**blk("/dev/sde1", 90 * G, de, "ext4", "E"), **fs(),
                                       **prt(o + "block_devices/sde", 1, 1 << 20, 90 * G)},
        }
        disks = {d.device: d for d in model.parse(tree)}
        self.assertTrue(disks["/dev/sdc"].protected)
        self.assertTrue(disks["/dev/sdd"].protected)
        self.assertTrue(disks["/dev/sdd"].volumes[0].protected)
        self.assertFalse(disks["/dev/sde"].protected)

    def test_erase_rechecks_a_fresh_tree(self):
        """The final Erase reads the tree again: a volume mounted at / since
        the sheet opened is refused (the old object said it was free)."""
        from sonata2.diskutil.window import DiskUtilityWindow
        o, G = self.O, self.G
        da = o + "drives/A"

        def tree(*mounts):
            return {da: {U + "Drive": {"Size": 100 * G}},
                    o + "block_devices/sda": {**blk("/dev/sda", 100 * G, da), U + "PartitionTable": {"Type": "gpt"}},
                    o + "block_devices/sda1": {**blk("/dev/sda1", 90 * G, da, "ext4", "X"), **fs(*mounts),
                                               **prt(o + "block_devices/sda", 1, 1 << 20, 90 * G)}}
        stale = model.parse(tree())[0].volumes[0]
        self.assertFalse(stale.protected)
        calls, alerts = [], []

        class Client:
            objects = tree("/")

            def fetch(self, done):
                done(tree("/"))

            def format(self, *a, **k):
                calls.append(a)
        fake = mock.Mock(client=Client(), _item_key=lambda it: DiskUtilityWindow._item_key(None, it))
        fake._find = lambda disks, item: DiskUtilityWindow._find(fake, disks, item)
        fake._refuse_erase = lambda item: alerts.append(item.name)
        DiskUtilityWindow._do_erase(fake, stale, "x", "ext4", None)
        self.assertEqual(calls, [])
        self.assertEqual(len(alerts), 1)
        fake._erase_now.assert_not_called()


class UDisksHelpersTest(unittest.TestCase):
    def test_options_and_errors(self):
        """a{sv} options: interaction allowed, "_" -> "-", bools typed;
        a dismissed polkit prompt is recognised and errors read cleanly."""
        opts = udisks.options(tear_down=True, label="USB")
        self.assertEqual(opts.get_type_string(), "a{sv}")
        self.assertEqual(opts.unpack(), {"auth.no_user_interaction": False, "tear-down": True, "label": "USB"})
        err = Gio.DBusError.new_for_dbus_error("org.freedesktop.UDisks2.Error.NotAuthorizedDismissed", "dismissed")
        self.assertTrue(udisks.dismissed(err))
        other = Gio.DBusError.new_for_dbus_error("org.freedesktop.UDisks2.Error.Failed", "Device is busy")
        self.assertFalse(udisks.dismissed(other))
        self.assertFalse(udisks.dismissed(ValueError("x")))
        self.assertEqual(udisks.error_text(ValueError("plain")), "plain")

    def test_error_text_strips_dbus_prefix(self):
        """Alerts show UDisks' sentence, not "GDBus.Error:org...: ..."."""
        other = Gio.DBusError.new_for_dbus_error("org.freedesktop.UDisks2.Error.Failed", "Device is busy")
        self.assertEqual(udisks.error_text(other), "Device is busy")

    def test_client_format_and_can(self):
        """Format sends (s, a{sv}) with tear-down; Can* maps the reply and an
        error to (available, missing utility)."""
        c = udisks.Client()
        calls = []
        c.call = lambda path, iface, method, args, done=None, timeout=0: calls.append((path, iface, method, args,
                                                                                         done, timeout))
        c.format("/blk", "exfat", "STICK", update_partition_type=True)
        path, iface, method, args, _done, timeout = calls[-1]
        self.assertEqual((path, iface, method), ("/blk", "Block", "Format"))
        self.assertEqual(args.get_type_string(), "(sa{sv})")
        fs_type, o = args.unpack()
        self.assertEqual(fs_type, "exfat")
        self.assertEqual((o["tear-down"], o["label"], o["update-partition-type"]), (True, "STICK", True))
        self.assertEqual(timeout, udisks.LONG_MS)
        got = []
        c.can("Format", "ntfs", lambda ok, util: got.append((ok, util)))
        calls[-1][4](((False, "mkfs.ntfs"),), None)
        calls[-1][4](None, GLib.Error("no"))
        self.assertEqual(got, [(False, "mkfs.ntfs"), (False, "")])


# -- Preview ---------------------------------------------------------------------------------------
@unittest.skipUnless(HAVE_PIL, "needs Pillow")
class PreviewEditsTest(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.Image = Image
        self.d = tempfile.mkdtemp()
        self.path = os.path.join(self.d, "pic.png")
        Image.new("RGB", (40, 20), (10, 20, 30)).save(self.path)

    def test_size_matches_render(self):
        """size() (shown in Adjust Size) equals the rendered result's size
        after rotate / crop / resize, and undo/revert track edited."""
        from sonata2.preview.edit import Edits
        ed = Edits.load(self.path)
        self.assertFalse(ed.edited)
        for op in (("rotate", 90), ("crop", (0.1, 0.2, 0.9, 0.7)), ("flip", "h"), ("resize", (0.5, 1.5))):
            ed.push(op)
            self.assertEqual(ed.size(), ed.render(ed.full).size, op)
        self.assertTrue(ed.edited)
        self.assertTrue(ed.undo())
        ed.revert()
        self.assertFalse(ed.edited)
        self.assertFalse(ed.undo())

    def test_save_is_atomic_and_writable_rules(self):
        """Save writes through a temp file (no leftover) and only formats
        Preview can write back are saved in place."""
        from sonata2.preview.edit import Edits
        ed = Edits.load(self.path)
        ed.push(("rotate", 90))
        ed.save(self.path)
        with self.Image.open(self.path) as im:
            self.assertEqual(im.size, (20, 40))
        self.assertEqual(os.listdir(self.d), ["pic.png"])
        self.assertFalse(ed.edited)
        self.assertTrue(Edits.writable("/x/a.JPG"))
        self.assertFalse(Edits.writable("/x/a.svg"))
        self.assertFalse(Edits.writable("/x/a.cr2"))

    def test_save_keeps_file_permissions(self):
        """Saving an edit keeps the file's permissions (a private 0600 photo
        must not become world-readable)."""
        from sonata2.preview.edit import Edits
        os.chmod(self.path, 0o600)
        ed = Edits.load(self.path)
        ed.push(("flip", "v"))
        ed.save(self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_save_through_symlink_keeps_link(self):
        """Saving a picture opened through a symlink updates the target and
        keeps the link."""
        from sonata2.preview.edit import Edits
        link = os.path.join(self.d, "link.png")
        os.symlink(self.path, link)
        ed = Edits.load(link)
        ed.push(("rotate", 90))
        ed.save(link)
        self.assertTrue(os.path.islink(link))

    def test_save_keeps_exif(self):
        """Saving a JPEG keeps its camera metadata (date taken, camera)."""
        from sonata2.preview.edit import Edits
        p = os.path.join(self.d, "cam.jpg")
        ex = self.Image.Exif()
        ex[0x010F] = "Canon"
        self.Image.new("RGB", (40, 20)).save(p, "JPEG", exif=ex)
        ed = Edits.load(p)
        ed.push(("flip", "h"))
        ed.save(p)
        with self.Image.open(p) as im:
            self.assertEqual(im.getexif().get(0x010F), "Canon")

    def test_info_dimensions_follow_orientation(self):
        """Info's Dimensions match the picture as shown (EXIF orientation applied)."""
        from sonata2.preview import info
        p = os.path.join(self.d, "rot.jpg")
        ex = self.Image.Exif()
        ex[0x0112] = 6
        self.Image.new("RGB", (40, 20)).save(p, "JPEG", exif=ex)
        dims = dict(info.read(p)["general"])["Dimensions"]
        self.assertEqual(dims, "20 × 40 pixels")

    def test_siblings_natural_order_pictures_only(self):
        """← / → walk the folder's pictures in Files' order, without hidden
        or non-picture files."""
        from sonata2.preview.window import siblings
        for n in ("img10.png", "img2.png", ".hidden.png", "notes.txt", "B.JPG"):
            open(os.path.join(self.d, n), "w").close()
        names = [os.path.basename(p) for p in siblings(os.path.join(self.d, "img2.png"))]
        self.assertNotIn(".hidden.png", names)
        self.assertNotIn("notes.txt", names)
        self.assertLess(names.index("img2.png"), names.index("img10.png"))
        self.assertEqual(siblings(os.path.join(self.d, "nope", "x.png")), [os.path.join(self.d, "nope", "x.png")])


if __name__ == "__main__":
    unittest.main()


# -- Preview / Quick Look: off the main loop, overwrite check --------------------------------------
class OffMainLoopTest(_Base):
    def test_quicklook_decodes_pictures_in_a_thread(self):
        """Quick Look shows the panel at once and decodes the picture in a
        worker thread (a big photo froze Files)."""
        from sonata2 import imageload
        from sonata2.files.quicklook import QuickLook
        threads = []
        tex = Gdk.MemoryTexture.new(2, 2, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(b"\0" * 16), 8)

        def texture(_path):
            threads.append(threading.current_thread())
            return tex
        f = Gio.File.new_for_path(self.p("pic.png"))
        info = Gio.FileInfo()
        info.set_name("pic.png")
        info.set_size(10)
        ql = QuickLook(None)
        with mock.patch.object(imageload, "texture", side_effect=texture):
            pic = ql._preview(info, f, "image/png")
            ql.body.append(pic)
            self.assertTrue(spin(lambda: pic.get_paintable() is tex))
        self.assertIsNot(threads[0], threading.main_thread())
        ql.destroy()

    def test_save_as_extension_asks_before_replacing(self):
        """Save As / Export add the extension after the Save panel's own
        check: an existing "name.png" is asked about, Cancel writes nothing."""
        from sonata2.preview.window import PreviewWindow
        write(self.p("photo.png"), "keep")
        writes = []
        self.assertTrue(PreviewWindow._confirm_replace(None, self.p("new"), self.p("new.png"),
                                                       lambda t: writes.append(t) or True))
        self.assertEqual(writes, [self.p("new.png")])
        writes.clear()
        PreviewWindow._confirm_replace(None, self.p("photo"), self.p("photo.png"), writes.append)
        self.assertEqual(len(self.alerts), 1)
        self.assertEqual(writes, [])                     # the test alert answers its first response: Cancel
        self.assertEqual(read(self.p("photo.png")), "keep")


# -- animations -------------------------------------------------------------------------------------
class AnimationTests(unittest.TestCase):
    """The main user actions of Files, Disk Utility and Preview animate:
    the real code paths start an Adw animation or set a Revealer/Stack
    transition (xvfb)."""

    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()
        cls.app = Adw.Application(application_id="io.github.vinioliveiras.sonata2.reviewanim")
        cls.app.register(None)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        for n in ("A", "B"):
            os.mkdir(os.path.join(self.d, n))
        self.wins = []

    def tearDown(self):
        for w in self.wins:
            w.destroy()

    def _files(self):
        from sonata2.files.window import FilesWindow
        w = FilesWindow(self.app, Gio.File.new_for_path(self.d).get_uri())
        w.present()
        self.wins.append(w)
        self.assertTrue(spin(lambda: w.folder.store.get_n_items() == 2))
        spin(lambda: False, 200)
        return w

    def test_files_folder_change_cross_fades(self):
        """Going into a folder cross-fades the view (Adw.TimedAnimation)."""
        w = self._files()
        fade = w.tab.fade
        w.go(Gio.File.new_for_path(os.path.join(self.d, "A")).get_uri())
        self.assertTrue(spin(lambda: isinstance(fade._anim, Adw.TimedAnimation)), "no cross-fade ran")
        self.assertGreater(fade.duration, 0)

    def test_files_tab_strip_and_search_slide(self):
        """New Tab reveals the tab strip and Search slides its field in."""
        w = self._files()
        self.assertEqual(w.strip.get_transition_type(), Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.assertGreater(w.strip.get_transition_duration(), 0)
        w.new_tab()
        self.assertTrue(w.strip.get_reveal_child())
        self.assertEqual(w.search_rev.get_transition_type(), Gtk.RevealerTransitionType.SLIDE_LEFT)
        self.assertGreater(w.search_rev.get_transition_duration(), 0)
        w._open_search()
        self.assertTrue(w.search_rev.get_reveal_child())

    @unittest.skipUnless(HAVE_PIL, "needs Pillow")
    def test_preview_zoom_sidebar_info_and_next_picture(self):
        """Preview: zoom steps glide (TimedAnimation), Thumbnails / Info
        slide (Revealers), the slideshow's next picture cross-fades."""
        from PIL import Image
        from sonata2.preview.window import PreviewWindow
        a, b = os.path.join(self.d, "a.png"), os.path.join(self.d, "b.png")
        Image.new("RGB", (800, 600), (10, 20, 30)).save(a)
        Image.new("RGB", (800, 600), (90, 20, 30)).save(b)
        w = PreviewWindow(self.app, a)
        w.set_default_size(400, 300)
        w.present()
        self.wins.append(w)
        spin(lambda: False, 300)
        w.step_zoom(1)
        self.assertIsInstance(w.canvas._anim, Adw.TimedAnimation)
        for rev, kind in ((w.sidebar, Gtk.RevealerTransitionType.SLIDE_RIGHT),
                          (w.info_rev, Gtk.RevealerTransitionType.SLIDE_LEFT)):
            self.assertEqual(rev.get_transition_type(), kind)
            self.assertGreater(rev.get_transition_duration(), 0)
        w.toggle_sidebar()
        w.toggle_info()
        self.assertTrue(w.sidebar.get_reveal_child() and w.info_rev.get_reveal_child())
        self.assertTrue(spin(lambda: len(w.pics) == 2))
        w.go(1, fade=True)
        self.assertTrue(spin(lambda: w.path == b))
        self.assertIsNotNone(w.canvas._fade)

    def test_disk_utility_detail_cross_fades_and_sheets_are_dialogs(self):
        """Disk Utility: the detail pane cross-fades; Erase opens an
        Adw.AlertDialog (libadwaita animates it in and out)."""
        from sonata2.diskutil.window import DiskUtilityWindow
        O, G = "/org/freedesktop/UDisks2/", 1000 ** 3
        da = O + "drives/A"
        tree = {da: {U + "Drive": {"Size": 100 * G, "ConnectionBus": "usb"}},
                O + "block_devices/sda": {**blk("/dev/sda", 100 * G, da), U + "PartitionTable": {"Type": "gpt"}},
                O + "block_devices/sda1": {**blk("/dev/sda1", 90 * G, da, "exfat", "X", "STICK"), **fs(),
                                           **prt(O + "block_devices/sda", 1, 1 << 20, 90 * G)}}

        class Client:
            objects = tree

            def start(self, on_objects, _on_error):
                on_objects(tree)

            def stop(self):
                pass

            def can(self, _w, _t, done):
                done(True, "")
        w = DiskUtilityWindow(self.app, client=Client())
        w.present()
        self.wins.append(w)
        spin(lambda: False, 200)
        self.assertEqual(w.stack.get_transition_type(), Gtk.StackTransitionType.CROSSFADE)
        self.assertGreater(w.stack.get_transition_duration(), 0)
        # (a real Adw.AlertDialog crashes under xvfb here, see test_preview: the call is checked)
        with mock.patch.object(ui.dialog, "alert", return_value=mock.Mock()) as alert:
            w.erase()
        self.assertEqual(alert.call_count, 1)
        self.assertTrue(alert.call_args[0][0].startswith("Erase"))
