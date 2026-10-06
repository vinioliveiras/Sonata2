"""Menu bar > Window (and the app menu's Hide): every item really does what it
says, run the way the menu runs it (its Gio actions; a checked item's
callback gets the new state first).

Vini: Window > Minimize didn't work; picking a window in that menu raised
"'bool' object has no attribute 'minimized'" (the window was replaced by
the checkmark's state)."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib  # noqa: E402

from sonata2 import ui  # noqa: E402
from sonata2.wl import toplevels as TL  # noqa: E402


def settle(ms=50):
    end = GLib.get_monotonic_time() + ms * 1000
    while GLib.get_monotonic_time() < end:
        GLib.MainContext.default().iteration(False)


class Handle:
    """A window's foreign-toplevel handle: what was asked of it."""

    def __init__(self, log, name):
        self.log, self.name = log, name

    def __getattr__(self, request):
        return lambda *a: self.log.append((request, self.name))


def manager(log, *windows):
    """The real ToplevelManager (its requests), without a compositor."""
    m = TL.ToplevelManager.__new__(TL.ToplevelManager)
    m.toplevels, m.share_bars, m.listeners, m.available = [], [], [], True
    m._seat = object()
    m._display = mock.Mock()
    for app_id, title, states in windows:
        t = TL.Toplevel(Handle(log, title))
        t.app_id, t.title, t.states = app_id, title, frozenset(states)
        m.toplevels.append(t)
    return m


def run(sections, label):
    """Activate the item called `label` as the menu does (through its action)."""
    group = Gio.SimpleActionGroup()
    ui.menu._build(sections, group)
    for s_i, section in enumerate(sections):
        for i_i, item in enumerate(section):
            if item.label == label:
                name = f"i{s_i}_{i_i}"
                assert group.lookup_action(name).get_enabled(), f"{label} is greyed out"
                group.activate_action(name, None)
                return
    raise AssertionError(f"no {label!r} in the menu")


class WindowMenuTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def setUp(self):
        from sonata2.shell import topbar as T
        self.T = T
        self.log = []
        self.bar = T.Bar(None)
        A, M = TL.ACTIVATED, TL.MAXIMIZED
        self.bar.manager = manager(self.log, ("org.gnome.TextEditor", "Notes.txt", {A}),
                                   ("org.gnome.TextEditor", "Todo.txt", set()),
                                   ("firefox", "Web", {M}))
        self.bar._menu = lambda _btn, sections: sections      # the sections instead of a pop-up

    def window_menu(self):
        return self.bar._window_menu(None)

    def test_minimize_after_the_menu_closed(self):
        sections = self.window_menu()
        run(sections, "Minimize")
        self.assertEqual(self.log, [])                       # not while the menu is still closing
        settle(self.T.MENU_SETTLE_MS + 100)
        self.assertEqual(self.log, [("set_minimized", "Notes.txt")])
        self.bar.manager._display.flush.assert_called()

    def test_zoom(self):
        run(self.window_menu(), "Zoom")
        self.assertEqual(self.log, [("set_maximized", "Notes.txt")])

    def test_picking_a_window_brings_it_forward(self):
        sections = self.window_menu()
        wins = next(sec for sec in sections if sec[0].label == "Notes.txt")
        self.assertEqual([i.label for i in wins], ["Notes.txt", "Todo.txt"])   # this app's windows
        self.assertEqual([i.checked for i in wins], [True, False])
        run(sections, "Todo.txt")
        self.assertIn(("activate", "Todo.txt"), self.log)
        self.log.clear()
        run(sections, "Notes.txt")                           # the checked one too
        self.assertIn(("activate", "Notes.txt"), self.log)

    def test_bring_all_to_front(self):
        run(self.window_menu(), "Bring All to Front")
        self.assertEqual([n for r, n in self.log if r == "activate"], ["Notes.txt", "Todo.txt"])

    def test_nothing_focused_greys_it_out(self):
        for t in self.bar.manager.toplevels:
            t.states = frozenset()
        sections = self.window_menu()
        group = Gio.SimpleActionGroup()
        ui.menu._build(sections, group)
        self.assertFalse(group.lookup_action("i0_0").get_enabled())    # Minimize
        self.assertFalse(group.lookup_action("i0_1").get_enabled())    # Zoom

    def test_hide_and_hide_others(self):
        with mock.patch("sonata2.apps.lookup", return_value=None):
            sections = self.bar._app_menu(None)
            labels = [i.label for s in sections for i in s]
            hide = next(lb for lb in labels if lb.startswith("Hide ") and lb != "Hide Others")
            run(sections, hide)
            settle(self.T.MENU_SETTLE_MS + 100)
            self.assertEqual(sorted(n for r, n in self.log if r == "set_minimized"), ["Notes.txt", "Todo.txt"])
            self.log.clear()
            run(self.bar._app_menu(None), "Hide Others")
            settle(self.T.MENU_SETTLE_MS + 100)
            self.assertEqual([n for r, n in self.log if r == "set_minimized"], ["Web"])
            self.log.clear()
            run(self.bar._app_menu(None), "Show All")
            self.assertEqual(sorted(n for r, n in self.log if r == "unset_minimized"),
                             ["Notes.txt", "Todo.txt", "Web"])


class TabsTest(unittest.TestCase):
    """Vini: tabs from the menu bar, for every app with tabs (macOS: File >
    New Tab, Window > Show Previous / Next Tab). The app's own shortcut is
    pressed once the menu has closed."""

    @classmethod
    def setUpClass(cls):
        Adw.init()
        ui.setup()

    def bar(self, app_id):
        from sonata2.shell import topbar as T
        self.T = T
        self.log = []
        bar = T.Bar(None)
        bar.manager = manager(self.log, (app_id, "Win", {TL.ACTIVATED}))
        bar._menu = lambda _btn, sections: sections
        return bar

    def labels(self, sections):
        return [i.label for sec in sections for i in sec]

    def test_kinds(self):
        from sonata2.shell import apptabs as A
        self.assertEqual(A.kind("io.github.vinioliveiras.sonata2.terminal"), "terminal")
        self.assertEqual(A.kind("org.gnome.Console"), "terminal")
        self.assertEqual(A.kind("kitty"), "terminal")
        self.assertIsNone(A.kind("foot"))                                  # a terminal without tabs
        for a in ("firefox", "google-chrome", "brave-browser", "io.github.vinioliveiras.sonata2.files",
                  "io.github.vinioliveiras.sonata2.textedit", "org.gnome.TextEditor", "org.gnome.Nautilus",
                  "zen", "microsoft-edge"):
            self.assertEqual(A.kind(a), "tabbed", a)
        for a in ("org.gnome.Calculator", "steam", "io.github.vinioliveiras.sonata2.notes", "zenity", ""):
            self.assertIsNone(A.kind(a), a)
        self.assertEqual(A.keys("kitty", "new_tab"), ("t", ("ctrl", "shift")))   # Ctrl+T is the shell's
        self.assertEqual(A.keys("firefox", "new_tab"), ("t", ("ctrl",)))
        self.assertEqual(A.keys("foot", "new_window"), ("n", ("ctrl", "shift")))
        self.assertIsNone(A.keys("foot", "new_tab"))

    def test_terminal_tabs_are_in_window(self):
        """Vini: the tabs belong in Window, not File."""
        bar = self.bar("io.github.vinioliveiras.sonata2.terminal")
        self.assertEqual(self.labels(bar._file_menu(None)), ["New Window", "Close Window"])
        win = bar._window_menu(None)
        tabs = next(sec for sec in win if sec[0].label == "New Tab")
        self.assertEqual([i.label for i in tabs], ["New Tab", "Show Previous Tab", "Show Next Tab", "Close Tab"])
        with mock.patch("sonata2.wl.vkeyboard.press", return_value=True) as key:
            run(win, "New Tab")
            key.assert_not_called()                                         # not while the menu closes
            settle(self.T.MENU_SETTLE_MS + 100)
            key.assert_called_once_with("t", ("ctrl", "shift"))
            run(bar._window_menu(None), "Show Next Tab")
            settle(self.T.MENU_SETTLE_MS + 100)
            self.assertEqual(key.call_args[0], ("Page_Down", ("ctrl",)))
            run(bar._file_menu(None), "New Window")
            settle(self.T.MENU_SETTLE_MS + 100)
            self.assertEqual(key.call_args[0], ("n", ("ctrl", "shift")))
        run(bar._file_menu(None), "Close Window")
        self.assertEqual(self.log, [("close", "Win")])

    def test_browser_and_an_app_without_tabs(self):
        bar = self.bar("firefox")
        with mock.patch("sonata2.wl.vkeyboard.press", return_value=True) as key:
            run(bar._window_menu(None), "New Tab")
            run(bar._window_menu(None), "Show Previous Tab")
            settle(self.T.MENU_SETTLE_MS + 100)
            self.assertEqual([c[0] for c in key.call_args_list], [("t", ("ctrl",)), ("Tab", ("ctrl", "shift"))])
        bar = self.bar("org.gnome.Calculator")
        self.assertEqual(self.labels(bar._file_menu(None)), ["New Window", "Close Window"])
        self.assertNotIn("New Tab", self.labels(bar._window_menu(None)))

    def test_desktop_keeps_files_menu(self):
        bar = self.bar("x")
        for t in bar.manager.toplevels:
            t.states = frozenset()
        with mock.patch.object(bar, "_files_file_menu", return_value="files") as files:
            self.assertEqual(bar._file_menu(None), "files")
            files.assert_called_once()


class CheckedItemsTest(unittest.TestCase):
    def test_every_checked_item_in_the_shell_takes_the_state(self):
        """A checked item's callback is called with the new state: a lambda
        whose first parameter is something else (lambda t=t: ...) gets a bool."""
        import ast
        import pathlib
        root = pathlib.Path(__file__).resolve().parent.parent / "sonata2"
        bad = []
        for f in root.rglob("*.py"):
            tree = ast.parse(f.read_text())
            for call in ast.walk(tree):
                if not (isinstance(call, ast.Call) and getattr(call.func, "attr", getattr(call.func, "id", ""))
                        == "Item"):
                    continue
                kw = {k.arg: k.value for k in call.keywords}
                if "checked" not in kw or len(call.args) < 2 or not isinstance(call.args[1], ast.Lambda):
                    continue
                if isinstance(kw["checked"], ast.Constant) and kw["checked"].value is None:
                    continue
                args = call.args[1].args
                params = args.posonlyargs + args.args
                if not params and not args.vararg:
                    bad.append(f"{f.relative_to(root)}:{call.lineno} lambda: (takes no state)")
                elif params and args.defaults and len(args.defaults) == len(params) and \
                        not params[0].arg.startswith("_") and params[0].arg not in ("on", "v", "value", "state"):
                    bad.append(f"{f.relative_to(root)}:{call.lineno} lambda {params[0].arg}=...")
        self.assertEqual(bad, [])


if __name__ == "__main__":
    unittest.main()
