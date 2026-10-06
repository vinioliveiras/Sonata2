"""Terminal: tab management with a stand-in terminal widget (no VTE
needed): add, close, reorder, switch, keys, title propagation, moving a
tab to a new window, opening folders as tabs; plus the real shell when
VTE for GTK 4 is installed."""
import os
import tempfile
import unittest

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
os.environ["GDK_BACKEND"] = "x11"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, GObject, Gtk  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.terminal import window as T  # noqa: E402


def settle(ms=200):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class StubTerminal(Gtk.Box):
    """What TerminalTab needs from a terminal widget, without VTE."""
    __gsignals__ = {"window-title-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
                    "current-directory-uri-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
                    "child-exited": (GObject.SignalFlags.RUN_LAST, None, (int,))}
    next_pid = 1000

    def __init__(self):
        super().__init__(vexpand=True, hexpand=True, focusable=True)
        self.uri = None
        self.fed = b""
        self.looks = 0
        self.append(Gtk.Label(label="vini@sonata ~ % ls", xalign=0, valign=Gtk.Align.START, hexpand=True))

    def start(self, cwd, argv, env, done):
        self.uri = GLib.filename_to_uri(cwd, None)
        StubTerminal.next_pid += 1
        done(StubTerminal.next_pid, None)

    def cd(self, path):
        self.uri = GLib.filename_to_uri(path, None)
        self.emit("current-directory-uri-changed")

    def apply_look(self, _size): self.looks += 1
    def copy_text(self): pass
    def paste_clipboard(self): pass
    def clear(self): self.fed = b""
    def feed(self, data): self.fed += data
    def get_has_selection(self): return False
    def get_current_directory_uri(self): return self.uri
    def get_column_count(self): return 80
    def get_row_count(self): return 24
    def get_char_width(self): return 7
    def get_char_height(self): return 16
    def get_pty(self): return None


_app = None


def app():
    global _app
    if _app is None:
        Adw.init()
        ui.setup()
        _app = Adw.Application(application_id="io.test.terminal")
        _app.register(None)
    return _app


class TabsTest(unittest.TestCase):
    def setUp(self):
        from sonata2 import config
        config.update("appearance", always_show_tabs=False)          # (the one-tab cases below)
        self.addCleanup(config.update, "appearance", always_show_tabs=True)
        self.dirs = [tempfile.mkdtemp(prefix=f"tm{i}_") for i in range(3)]
        self.w = T.TerminalWindow(app(), self.dirs[0], factory=StubTerminal)
        self.w.present()
        settle(50)

    def tearDown(self):
        for w in list(app().get_windows()):
            if isinstance(w, T.TerminalWindow):
                w._really_close()
        settle(50)

    def names(self, w=None):
        return [os.path.basename(t.cwd()) for t in (w or self.w).tabs]

    def test_tab_bar_always_shown_when_chosen(self):
        """Vini: apps open with their tab bar showing (Settings > Appearance >
        Always show the tab bar, on by default); off: only with 2+ tabs."""
        from sonata2 import config, icons
        self.assertTrue(icons.APPEARANCE_DEFAULTS["always_show_tabs"])
        config.update("appearance", always_show_tabs=True)
        settle(300)                                                   # (the change is watched)
        self.assertTrue(self.w.strip.get_visible())
        config.update("appearance", always_show_tabs=False)
        settle(300)
        self.assertFalse(self.w.strip.get_visible())

    def test_one_tab_hides_strip(self):
        w = self.w
        self.assertEqual(len(w.tabs), 1)
        self.assertFalse(w.strip.get_visible())
        self.assertIsNotNone(w.pid)
        self.assertIn(os.path.basename(self.dirs[0]), w.get_title())
        self.assertIn("80×24", w.get_title())

    def test_add_and_switch(self):
        w = self.w
        b = w.new_tab(self.dirs[1])
        self.assertTrue(w.strip.get_visible())
        self.assertIs(w.current, b)
        self.assertTrue(b.button.has_css_class("active"))
        self.assertFalse(w.tabs[0].button.has_css_class("active"))
        self.assertIn(os.path.basename(self.dirs[1]), w.get_title())
        # a new tab goes right after the current one
        w.select_index(0)
        c = w.new_tab(self.dirs[2])
        self.assertEqual(w.tabs.index(c), 1)
        w.cycle(1)
        self.assertIs(w.current, b)
        w.cycle(1)
        self.assertIs(w.current, w.tabs[0])
        w.cycle(-1)
        self.assertIs(w.current, b)
        self.assertIs(w.stack.get_visible_child(), b.box)

    def test_title_follows_active_tab(self):
        w = self.w
        a = w.tabs[0]
        b = w.new_tab(self.dirs[1])
        a.term.cd(self.dirs[2])                   # background tab: its label only
        self.assertIn(os.path.basename(self.dirs[2]), a.button.label.get_label())
        self.assertNotIn(os.path.basename(self.dirs[2]), w.get_title())
        b.term.cd("/")
        self.assertTrue(w.get_title().startswith("/ — "))
        self.assertEqual(w.bar.title_label.get_label(), w.get_title())
        self.assertNotIn("×", b.button.label.get_label())

    def test_close_and_reorder(self):
        w = self.w
        w.new_tab(self.dirs[1])
        w.new_tab(self.dirs[2])
        n = [os.path.basename(d) for d in self.dirs]
        self.assertEqual(self.names(), n)
        w.move_tab(w.tabs[2], 0)
        self.assertEqual(self.names(), [n[2], n[0], n[1]])
        self.assertEqual([c.tab for c in _children(w.tab_row)], w.tabs)
        w.move_tab(w.tabs[0], 5)                  # clamped to the end
        self.assertEqual(self.names(), [n[0], n[1], n[2]])
        self.assertEqual([c.tab for c in _children(w.tab_row)], w.tabs)
        # closing the current tab selects the one to its right
        w.select_index(1)
        w.close_tab(w.current)
        self.assertEqual(self.names(), [n[0], n[2]])
        self.assertEqual(os.path.basename(w.current.cwd()), n[2])
        # the shell ending closes its tab; the last one closes the window
        w.tabs[1].term.emit("child-exited", 0)
        self.assertEqual(len(w.tabs), 1)
        self.assertFalse(w.strip.get_visible())
        w.tabs[0].term.emit("child-exited", 0)
        settle(50)
        self.assertTrue(w._closing)
        self.assertNotIn(w, app().get_windows())

    def test_busy_tab_asks(self):
        w = self.w
        b = w.new_tab(self.dirs[1])
        b.busy = lambda: "sleep"
        asked = []
        orig = T.ui.dialog.alert
        T.ui.dialog.alert = lambda heading, body, responses, cb, parent=None: asked.append((body, cb))
        try:
            w.close_tab(b)
            self.assertEqual(len(w.tabs), 2)
            self.assertIn("sleep", asked[0][0])
            asked[0][1]("close")
            self.assertEqual(len(w.tabs), 1)
            # the window asks too, for any tab
            w.tabs[0].busy = lambda: "vim"
            self.assertTrue(w._close_request(w))
            self.assertIn("vim", asked[1][0])
        finally:
            T.ui.dialog.alert = orig

    def test_keys(self):
        w = self.w
        cmd = Gdk.ModifierType.SUPER_MASK
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK
        self.assertTrue(w._key(None, Gdk.KEY_t, 0, cmd))
        self.assertTrue(w._key(None, Gdk.KEY_T, 0, ctrl | shift))
        self.assertEqual(len(w.tabs), 3)
        self.assertTrue(w._key(None, Gdk.KEY_1, 0, cmd))
        self.assertIs(w.current, w.tabs[0])
        self.assertTrue(w._key(None, Gdk.KEY_9, 0, ctrl))          # ⌘9: the last tab
        self.assertIs(w.current, w.tabs[2])
        self.assertTrue(w._key(None, Gdk.KEY_Tab, 0, ctrl))
        self.assertIs(w.current, w.tabs[0])
        self.assertTrue(w._key(None, Gdk.KEY_ISO_Left_Tab, 0, ctrl | shift))
        self.assertIs(w.current, w.tabs[2])
        self.assertTrue(w._key(None, Gdk.KEY_braceleft, 0, cmd | shift))
        self.assertIs(w.current, w.tabs[1])
        self.assertTrue(w._key(None, Gdk.KEY_braceright, 0, ctrl | shift))
        self.assertIs(w.current, w.tabs[2])
        self.assertTrue(w._key(None, Gdk.KEY_exclam, 11, ctrl | shift))   # Ctrl+Shift+2 by position
        self.assertIs(w.current, w.tabs[1])
        self.assertTrue(w._key(None, Gdk.KEY_w, 0, cmd))
        self.assertEqual(len(w.tabs), 2)
        self.assertFalse(w._key(None, Gdk.KEY_w, 0, ctrl))          # plain Ctrl+W is the shell's
        self.assertFalse(w._key(None, Gdk.KEY_t, 0, ctrl))
        self.assertEqual(len(w.tabs), 2)
        n = len([x for x in app().get_windows() if isinstance(x, T.TerminalWindow)])
        self.assertTrue(w._key(None, Gdk.KEY_n, 0, cmd))
        self.assertEqual(len([x for x in app().get_windows() if isinstance(x, T.TerminalWindow)]), n + 1)

    def test_new_tab_in_current_folder(self):
        w = self.w
        w.current.term.cd(self.dirs[2])
        w._key(None, Gdk.KEY_t, 0, Gdk.ModifierType.SUPER_MASK)
        self.assertEqual(w.current.cwd(), self.dirs[2])

    def test_move_to_new_window(self):
        w = self.w
        b = w.new_tab(self.dirs[1])
        pid = b.pid
        w2 = w.move_to_new_window(b)
        self.assertEqual(len(w.tabs), 1)
        self.assertFalse(w.strip.get_visible())
        self.assertEqual(w2.tabs, [b])
        self.assertIs(b.win, w2)
        self.assertEqual(b.pid, pid)              # the same process, not a new one
        self.assertIn(os.path.basename(self.dirs[1]), w2.get_title())
        self.assertIsNone(w2.move_to_new_window(b))   # a lone tab stays

    def test_open_folders_as_tabs(self):
        orig = T.new_terminal
        T.new_terminal = StubTerminal
        try:
            self.w.present()
            settle(50)
            T.open_windows(app(), [self.dirs[1], self.dirs[2]])
            wins = [x for x in app().get_windows() if isinstance(x, T.TerminalWindow) and x.tabs]
            self.assertEqual(len(wins), 1)
            self.assertEqual(self.names(), [os.path.basename(d) for d in self.dirs])
            T.open_windows(app(), [])             # no folder: raises, no new tab
            self.assertEqual(len(self.w.tabs), 3)
        finally:
            T.new_terminal = orig

    def test_open_without_window(self):
        orig = T.new_terminal
        T.new_terminal = StubTerminal
        try:
            self.w._really_close()
            settle(50)
            T.open_windows(app(), [self.dirs[1]])
            wins = [x for x in app().get_windows() if isinstance(x, T.TerminalWindow) and not x._closing]
            self.assertEqual(len(wins), 1)
            self.assertEqual(wins[0].tabs[0].cwd(), self.dirs[1])
        finally:
            T.new_terminal = orig


class FallbackTest(unittest.TestCase):
    def test_needs_vte(self):
        orig = T.new_terminal
        T.new_terminal = None
        try:
            w = T.TerminalWindow(app())
            self.assertEqual(w.tabs, [])
            label = w.get_child().get_first_child()
            self.assertIn("VTE", label.get_label())
            w.close()
        finally:
            T.new_terminal = orig


def _children(box):
    c, out = box.get_first_child(), []
    while c:
        out.append(c)
        c = c.get_next_sibling()
    return out


@unittest.skipIf(T.Vte is None, "no VTE for GTK 4")
class ShellTest(unittest.TestCase):
    def test_shell(self):
        os.environ["SHELL"] = "/bin/sh"
        T.user_shell = lambda: "/bin/sh"
        w = T.TerminalWindow(app(), "/tmp")
        w.present()
        settle(1500)
        self.assertIsNotNone(w.pid)
        w.term.feed_child(b"sleep 5\n")
        settle(1500)
        self.assertEqual(w._busy(), "sleep")
        self.assertIn("sleep", w.bar.title_label.get_label())
        w._really_close()


if __name__ == "__main__":
    unittest.main()
