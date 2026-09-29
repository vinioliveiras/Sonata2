"""Software Update: parsers, the step runner and the Settings page with fake
sources (xvfb-run python3 -m unittest tests.test_updates)."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.backend import updates as U  # noqa: E402


def settle(ms=300):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


def fake_sources():
    return [U.Source("system", "System", ["printf", "linux 6.1-1 -> 6.2-1\\nfirefox 130.0-1 -> 131.0-1\\n"],
                     ["sh", "-c", "echo '( 1/2) upgrading linux'; echo '( 2/2) upgrading firefox'"],
                     "true", U.parse_arrow),
            U.Source("flatpak", "Flatpak", ["printf", "Spotify\\tcom.spotify.Client\\t1.2\\n"],
                     ["true"], "true", U.parse_flatpak),
            U.Source("aur", "AUR", ["false"], ["true"], "true", U.parse_arrow)]


class UpdatesTest(unittest.TestCase):
    def test_parsers(self):
        ups = U.parse_arrow("linux 6.1-1 -> 6.2-1\nfoo 1 -> 2 [ignored]\n")
        self.assertEqual([(u.name, u.old, u.new) for u in ups], [("linux", "6.1-1", "6.2-1")])
        ups = U.parse_apt("Listing...\nfirefox/jammy 131 amd64 [upgradable from: 130]\n")
        self.assertEqual((ups[0].name, ups[0].old, ups[0].new), ("firefox", "130", "131"))
        self.assertEqual(U.parse_flatpak("Spotify\tcom.spotify.Client\t1.2\n")[0].name, "Spotify")

    def test_runner(self):
        lines, fr, res = [], [], []
        r = U.Runner([["sh", "-c", "echo '( 1/4) a'; echo b"], ["false"], ["echo", "never"]],
                     lines.append, fr.append, lambda ok, i: res.append((ok, i)))
        r.start()
        end = GLib.get_monotonic_time() + 3_000_000
        while not res and GLib.get_monotonic_time() < end:
            GLib.MainContext.default().iteration(False)
        self.assertEqual(res, [(False, 1)])
        self.assertIn("b", lines)
        self.assertIn(0.25, fr)
        self.assertNotIn("never", lines)

    def test_page(self):
        Adw.init()
        ui.setup()
        from sonata2.settings import app as S
        U.sources = fake_sources
        app = Adw.Application(application_id="io.test.updates")
        app.register(None)
        win = S.Settings(app)
        win.set_default_size(900, 640)
        win.present()
        win.select("updates")
        settle(1500)
        page = win.pages["updates"]
        text = []

        def walk(w):
            if isinstance(w, (Gtk.Label,)):
                text.append(w.get_label())
            c = w.get_first_child()
            while c:
                walk(c)
                c = c.get_next_sibling()
        walk(page)
        joined = " | ".join(text)
        self.assertIn("3 updates available", joined)
        self.assertIn("AUR couldn't be checked", joined)
        if os.environ.get("SHOT"):
            ui_shot(win, os.environ["SHOT"])
        btns = []

        def find(w):
            if isinstance(w, Gtk.Button) and w.get_label() == "Update Now":
                btns.append(w)
            c = w.get_first_child()
            while c:
                find(c)
                c = c.get_next_sibling()
        find(page)
        btns[0].emit("clicked")
        settle(1500)
        text.clear()
        walk(page)
        joined = " | ".join(text)
        self.assertIn("Your computer is up to date", joined)
        self.assertIn("Restart", joined)                  # a kernel was updated
        if os.environ.get("SHOT"):
            ui_shot(win, os.environ["SHOT"].replace(".png", "-done.png"))
        win.destroy()


def ui_shot(win, path):
    settle(300)
    paintable = Gtk.WidgetPaintable.new(win)
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    paintable.snapshot(snap, w, h)
    node = snap.to_node()
    tex = win.get_native().get_renderer().render_texture(node, None)
    tex.save_to_png(path)


if __name__ == "__main__":
    unittest.main()
