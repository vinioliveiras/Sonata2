"""The mouse pointer: Sonata's, an installed theme or a downloaded one, its
size, and back to Sonata's (Settings > Mouse & Trackpad > Pointer).

    dbus-run-session -- xvfb-run -a python3 -m unittest tests.test_cursors
"""
import io
import os
import tarfile
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()

from sonata2 import cursors, prefs  # noqa: E402

POINTER = os.path.join(cursors.BUNDLED, "Sonata-Cursors", "cursors", "default")


def _theme_files(name):
    """(archive path, bytes) of a small pointer theme."""
    with open(POINTER, "rb") as f:
        data = f.read()
    return [(f"{name}/index.theme", f"[Icon Theme]\nName={name} Pointers\n".encode()),
            (f"{name}/cursors/default", data)]


def _tar(path, files, links=()):
    with tarfile.open(path, "w:gz") as t:
        for name, data in files:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        for name, target in links:
            info = tarfile.TarInfo(name)
            info.type = tarfile.SYMTYPE
            info.linkname = target
            t.addfile(info)


class CursorsTest(unittest.TestCase):
    def setUp(self):
        self.data = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.data, "XDG_DATA_DIRS": "/nonexistent"})
        self.env.start()
        self.wf = mock.patch("sonata2.wfconfig.wayfire_set")
        self.wayfire_set = self.wf.start()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        self.wf.stop()
        self.env.stop()

    def test_sonatas_pointer_first_then_installed_themes(self):
        os.makedirs(os.path.join(cursors.user_dir(), "Zeta", "cursors"))
        with open(os.path.join(cursors.user_dir(), "Zeta", "cursors", "default"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(cursors.user_dir(), "Zeta", "index.theme"), "w") as f:
            f.write("[Icon Theme]\nName=Zeta Pointers\n")
        os.makedirs(os.path.join(cursors.user_dir(), "IconsOnly", "48x48"))   # an icon theme: not a pointer
        names = [n for n, _l in cursors.themes()]
        self.assertEqual(names[0], cursors.DEFAULT)
        self.assertIn(("Zeta", "Zeta Pointers"), cursors.themes())
        self.assertNotIn("IconsOnly", names)

    def test_apply_and_reset_reach_wayfire_and_the_apps(self):
        cursors.apply("Zeta", 48)
        self.assertEqual(cursors.current(), "Zeta")
        self.assertEqual(cursors.size(), 48)
        self.wayfire_set.assert_any_call("input", "cursor_theme", "Zeta")
        self.wayfire_set.assert_any_call("input", "cursor_size", 48)
        cursors.reset()
        self.assertEqual((cursors.current(), cursors.size()), (cursors.DEFAULT, cursors.DEFAULT_SIZE))
        self.wayfire_set.assert_any_call("input", "cursor_theme", cursors.DEFAULT)

    def test_size_only_keeps_the_theme(self):
        cursors.apply("Zeta")
        cursors.apply(None, 32)
        self.assertEqual((cursors.current(), cursors.size()), ("Zeta", 32))

    def test_add_a_downloaded_tar(self):
        path = os.path.join(self.tmp, "Bibata.tar.gz")
        _tar(path, _theme_files("Bibata-Modern"))
        self.assertEqual(cursors.add(path), ["Bibata-Modern"])
        self.assertTrue(cursors.is_theme(os.path.join(cursors.user_dir(), "Bibata-Modern")))
        self.assertIn("Bibata-Modern", [n for n, _l in cursors.themes()])

    def test_add_a_zip_with_two_themes(self):
        path = os.path.join(self.tmp, "pack.zip")
        with zipfile.ZipFile(path, "w") as z:
            for name, data in _theme_files("pack/Light") + _theme_files("pack/Dark"):
                z.writestr(name, data)
        self.assertEqual(sorted(cursors.add(path)), ["Dark", "Light"])

    def test_add_a_folder(self):
        folder = os.path.join(self.tmp, "Mine")
        for name, data in _theme_files("Mine"):
            os.makedirs(os.path.dirname(os.path.join(self.tmp, name)), exist_ok=True)
            with open(os.path.join(self.tmp, name), "wb") as f:
                f.write(data)
        self.assertEqual(cursors.add(folder), ["Mine"])

    def test_a_file_without_pointers_says_so(self):
        path = os.path.join(self.tmp, "icons.tar.gz")
        _tar(path, [("Icons/index.theme", b"[Icon Theme]\n")])
        with self.assertRaises(cursors.NotATheme):
            cursors.add(path)
        with open(os.path.join(self.tmp, "notes.txt"), "w") as f:
            f.write("x")
        with self.assertRaises(cursors.NotATheme):
            cursors.add(os.path.join(self.tmp, "notes.txt"))

    def test_a_downloaded_archive_never_writes_outside(self):
        """An archive is untrusted: ../ paths and links out of the theme are left out."""
        path = os.path.join(self.tmp, "evil.tar.gz")
        _tar(path, _theme_files("Evil") + [("../../escaped.txt", b"x")],
             links=[("Evil/cursors/pointer", "/etc/passwd")])
        self.assertEqual(cursors.add(path), ["Evil"])
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(self.tmp), "escaped.txt")))
        self.assertFalse(os.path.lexists(os.path.join(cursors.user_dir(), "Evil", "cursors", "pointer")))

    def test_never_over_sonatas_own(self):
        path = os.path.join(self.tmp, "same.tar.gz")
        _tar(path, _theme_files(cursors.DEFAULT))
        self.assertEqual(cursors.add(path), [cursors.DEFAULT + "-custom"])

    def test_the_login_hands_the_pointer_to_every_app(self):
        """Apps that read XCURSOR_THEME at start (Qt, X11, games) kept Sonata's."""
        cursors.apply("Zeta", 48)
        with mock.patch.object(prefs, "_fonts", return_value=set()):
            env = prefs.session_env()
        self.assertIn("export XCURSOR_THEME=Zeta XCURSOR_SIZE=48", env)


@unittest.skipUnless(os.environ.get("DISPLAY"), "needs a display (xvfb-run)")
class PointerSettingsTest(unittest.TestCase):
    def test_mouse_page_has_the_pointer_rows(self):
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib
        from sonata2 import ui
        from sonata2.settings import app as S
        ui.setup()
        win = S.Settings(None)
        win.present()
        win.select("mouse")
        end = GLib.get_monotonic_time() + 1_000_000
        while GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
        titles, stack = [], [win.pages["mouse"]]
        while stack:
            w = stack.pop()
            if isinstance(w, Adw.PreferencesRow):
                titles.append(w.get_title())
            c = w.get_first_child()
            while c is not None:
                stack.append(c)
                c = c.get_next_sibling()
        for t in ("Pointer style", "Pointer size", "Add a pointer theme", "Sonata's pointer"):
            self.assertIn(t, titles)
        win.destroy()


if __name__ == "__main__":
    unittest.main()
