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
        self.assertEqual([i.label for i in sections[1]], ["Notes.txt", "Todo.txt"])   # this app's windows
        self.assertEqual([i.checked for i in sections[1]], [True, False])
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
