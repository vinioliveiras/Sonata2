"""Review tests for Files, Disk Utility and Preview: file operations against
temp dirs (conflicts, symlinks, cross-device fallback, Put Back, delete),
search, thumbnails cache, packages/archives, bookmarks, automount, the
UDisks model/client helpers and Preview's edits/info. Headless; nothing
touches the real home or a real disk.

    xvfb-run -a python3.12 -m unittest tests.test_review_files -v"""
import os
import shlex
import tarfile
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
from gi.repository import Adw, GdkPixbuf, Gio, GLib  # noqa: E402

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

    @unittest.expectedFailure
    def test_replace_never_deletes_the_source(self):
        """Moving dir/x/x up into dir and answering Replace must not destroy
        the item being moved."""
        # BUG: ops.Transfer._one deletes the conflicting target (dir/x) before
        # moving, and dir/x contains the source dir/x/x -> its data is lost.
        write(self.p("x", "x", "precious.txt"), "data")
        self.answer = "replace"
        self.transfer([self.p("x", "x")], self.d, move=True)
        found = [os.path.join(r, n) for r, _d, fs in os.walk(self.d) for n in fs]
        self.assertTrue(any(read(f) == "data" for f in found), "the moved file was deleted")


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
        name is taken again it restores as "x copy" instead of overwriting."""
        write(self.p("trash", "a.txt"), "trashed")
        orig = self.p("gone", "deeper", "a.txt")
        ops.put_back([self._trashed(self.p("trash", "a.txt"), orig)], lambda f, e: self.fail(e.message))
        self.assertEqual(read(orig), "trashed")
        write(self.p("trash", "b.txt"), "second")
        write(self.p("home", "b.txt"), "current")
        ops.put_back([self._trashed(self.p("trash", "b.txt"), self.p("home", "b.txt"))])
        self.assertEqual(read(self.p("home", "b.txt")), "current")
        self.assertEqual(read(self.p("home", "b copy.txt")), "second")

    def test_put_back_unknown_origin_reports(self):
        """An item with no recorded origin reports an error, nothing moves."""
        write(self.p("trash", "a.txt"))
        errors = []
        ops.put_back([self._trashed(self.p("trash", "a.txt"), "")], lambda f, e: errors.append(e))
        self.assertEqual(len(errors), 1)
        self.assertTrue(os.path.exists(self.p("trash", "a.txt")))

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

    @unittest.expectedFailure
    def test_paste_image_never_overwrites(self):
        """Two pictures pasted in the same second must not overwrite each other."""
        # BUG: ops.paste_image builds "Pasted Image <date> at <time>.png" and
        # saves without checking it exists (no free_name) -> silent overwrite.
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

    @unittest.expectedFailure
    def test_eject_finish_matches_the_started_operation(self):
        """The finish call must match the operation that was started, even if
        can_eject() changes once the drive is gone."""
        # BUG: sidebar._ejected re-asks mount.can_eject() to pick the _finish
        # function; when it flips after the eject, unmount_with_operation_finish
        # is called on an eject result -> a bogus "wasn't ejected" alert.
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

    @unittest.expectedFailure
    def test_member_of_mounted_root_filesystem_is_protected(self):
        """A second device of a multi-device filesystem mounted at / (btrfs
        RAID: same UUID) must be protected from Erase."""
        # BUG: model.Volume.protected only looks at the volume's own
        # MountPoints; UDisks reports the mount on one member only, so the
        # other member of the running root filesystem can be erased.
        sdb = model.parse(self.tree())[1]
        sdb6 = [v for v in sdb.volumes if v.device == "/dev/sdb6"][0]
        self.assertTrue(sdb6.protected)


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

    @unittest.expectedFailure
    def test_error_text_strips_dbus_prefix(self):
        """Alerts show UDisks' sentence, not "GDBus.Error:org...: ..."."""
        # BUG: udisks.error_text calls Gio.DBusError.strip_remote_error(err),
        # which in PyGObject strips a C copy; err.message keeps the prefix, so
        # every Disk Utility error alert shows the raw D-Bus error name.
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

    @unittest.expectedFailure
    def test_save_keeps_file_permissions(self):
        """Saving an edit keeps the file's permissions (a private 0600 photo
        must not become world-readable)."""
        # BUG: Edits.write saves a new temp file and os.replace()s it over the
        # original, so the mode becomes the umask default (0644).
        from sonata2.preview.edit import Edits
        os.chmod(self.path, 0o600)
        ed = Edits.load(self.path)
        ed.push(("flip", "v"))
        ed.save(self.path)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    @unittest.expectedFailure
    def test_save_through_symlink_keeps_link(self):
        """Saving a picture opened through a symlink updates the target and
        keeps the link."""
        # BUG: Edits.write os.replace()s the link itself with a regular file;
        # the real picture is left unedited and the link is gone.
        from sonata2.preview.edit import Edits
        link = os.path.join(self.d, "link.png")
        os.symlink(self.path, link)
        ed = Edits.load(link)
        ed.push(("rotate", 90))
        ed.save(link)
        self.assertTrue(os.path.islink(link))

    @unittest.expectedFailure
    def test_save_keeps_exif(self):
        """Saving a JPEG keeps its camera metadata (date taken, camera)."""
        # BUG: Edits.write calls im.save() without exif=, so EXIF (and the ICC
        # profile) are stripped from the photo on every in-place save.
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

    @unittest.expectedFailure
    def test_info_dimensions_follow_orientation(self):
        """Info's Dimensions match the picture as shown (EXIF orientation applied)."""
        # BUG: info._pillow reports im.size before exif_transpose; a portrait
        # phone photo (Orientation 6) is listed as landscape.
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
